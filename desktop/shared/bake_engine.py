"""
SlyLED Bake Engine — compile timelines into per-fixture action sequences.

The compilation pipeline:
1. For each clip, analyze the spatial relationship between the effect and each fixture
2. Compute per-string timed instructions directly from spatial math
3. Output the minimal set of action steps each child needs to execute locally
4. No per-frame rendering needed — children run native action types

Each clip produces per-string instructions like:
  "at t=5.2s, run WIPE_SEQ color=#6400ff speed=67ms/px dir=East for 10s"

This is fundamentally different from rendering frames and trying to detect patterns.
The children already know how to run WIPE, SOLID, FADE etc — we just compute WHEN and HOW.
"""

import io
import math
import struct
import time
import zipfile

BAKE_FPS = 40  # only used for preview generation
LSQ_MAGIC = b"LSQ\x00"
LSQ_VERSION = 1

# Action type constants (must match Protocol.h)
ACT_BLACKOUT = 0
ACT_SOLID = 1
ACT_FADE = 2
ACT_BREATHE = 3
ACT_CHASE = 4
ACT_RAINBOW = 5
ACT_FIRE = 6
ACT_COMET = 7
ACT_TWINKLE = 8
ACT_STROBE = 9
ACT_WIPE = 10
ACT_SCANNER = 11
ACT_SPARKLE = 12
ACT_GRADIENT = 13
ACT_DMX_SCENE = 14  # DMX fixture: holds all channel values for a time slice
ACT_DMX_PT_MOVE = 15  # Pan/Tilt animated move
ACT_DMX_GOBO = 16  # Gobo select
ACT_DMX_COLOR_WHEEL = 17  # Color wheel select


def _rotation_to_aim(rotation, pos, dist=3000):
    """Convert rotation [rx, ry, rz] (degrees) + position to an aim point.

    rx = tilt/pitch, ry = pan/yaw.  Default distance is 3000mm (3m).
    Stage coordinates: X=width, Y=depth (forward), Z=height (up).
    Returns [x, y, z] in stage mm coordinates.
    """
    rx, ry = rotation[0] if rotation else 0, rotation[1] if rotation and len(rotation) > 1 else 0
    pan_rad = math.radians(ry)
    tilt_rad = math.radians(rx)
    dx = math.sin(pan_rad) * math.cos(tilt_rad) * dist
    dy = math.cos(pan_rad) * math.cos(tilt_rad) * dist   # Y = depth (forward)
    dz = -math.sin(tilt_rad) * dist                       # Z = height (up)
    return [pos[0] + dx, pos[1] + dy, pos[2] + dz]


class BakeProgress:
    """Bake progress tracker."""
    def __init__(self, total_steps=0):
        self.total_frames = total_steps
        self.current_frame = 0
        self.status = "starting"
        self.fixtures_done = 0
        self.total_fixtures = 0
        self.segments = {}
        self.error = None
        self.done = False

    def to_dict(self):
        return {
            "running": not self.done,
            "status": self.status,
            "frame": self.current_frame,
            "totalFrames": self.total_frames,
            "progress": round(self.current_frame / max(self.total_frames, 1) * 100, 1),
            "fixturesDone": self.fixtures_done,
            "totalFixtures": self.total_fixtures,
            "segments": self.segments,
            "error": self.error,
            "done": self.done,
        }


# ── Spatial math helpers ─────────────────────────────────────────────────────

def _ease(t, easing):
    t = max(0.0, min(1.0, t))
    if easing == "ease-in": return t * t
    if easing == "ease-out": return t * (2 - t)
    if easing == "ease-in-out": return t * t * (3 - 2 * t)
    return t

def _lerp(a, b, t):
    return a + (b - a) * t

def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i])**2 for i in range(min(len(a), len(b)))))


def _sphere_intersection_time(pixel_pos, start_pos, end_pos, radius, duration, easing="linear"):
    """Compute when a sphere's center is closest to a pixel (peak brightness time).
    Returns (t_enter, t_peak, t_exit) in seconds, or None if never intersected."""
    # Binary search for closest approach along the motion path
    best_t = 0
    best_dist = float('inf')
    steps = 100
    for i in range(steps + 1):
        raw_t = i / steps
        eased_t = _ease(raw_t, easing)
        center = [_lerp(start_pos[j], end_pos[j], eased_t) for j in range(3)]
        d = _dist(pixel_pos, center)
        if d < best_dist:
            best_dist = d
            best_t = raw_t

    if best_dist > radius:
        return None  # never intersected

    t_peak = best_t * duration

    # Find enter/exit by scanning outward from peak
    t_enter = t_peak
    t_exit = t_peak
    for i in range(steps + 1):
        raw_t = max(0, best_t - i / steps)
        eased_t = _ease(raw_t, easing)
        center = [_lerp(start_pos[j], end_pos[j], eased_t) for j in range(3)]
        if _dist(pixel_pos, center) <= radius:
            t_enter = raw_t * duration
        else:
            break
    for i in range(steps + 1):
        raw_t = min(1, best_t + i / steps)
        eased_t = _ease(raw_t, easing)
        center = [_lerp(start_pos[j], end_pos[j], eased_t) for j in range(3)]
        if _dist(pixel_pos, center) <= radius:
            t_exit = raw_t * duration
        else:
            break

    return (t_enter, t_peak, t_exit)


def _compile_clip_for_string(clip, effect, pixel_positions, string_offset, string_count, duration):
    """Compile a spatial effect clip into action steps for a single string.

    Analyzes the spatial relationship between the effect's motion path and
    the string's pixel positions to determine the optimal action type.

    Returns list of action step dicts with timing.
    """
    if not pixel_positions or not effect:
        return []

    shape = effect.get("shape", "sphere")
    motion = effect.get("motion", {})
    start_pos = motion.get("startPos", [0, 0, 0])
    end_pos = motion.get("endPos", [0, 0, 0])
    fx_dur = motion.get("durationS", duration) or duration
    easing = motion.get("easing", "linear")
    color = [effect.get("r", 255), effect.get("g", 255), effect.get("b", 255)]
    size = effect.get("size", {})

    if shape == "sphere":
        radius = size.get("radius", 1000)
        return _compile_sphere_sweep(
            pixel_positions, start_pos, end_pos, radius, color,
            clip.get("startS", 0), clip.get("durationS", duration),
            fx_dur, easing, string_offset, string_count
        )

    elif shape == "plane":
        normal = size.get("normal", [0, 1, 0])
        thickness = size.get("thickness", 400)
        return _compile_plane_sweep(
            pixel_positions, start_pos, end_pos, normal, thickness, color,
            clip.get("startS", 0), clip.get("durationS", duration),
            fx_dur, easing, string_offset, string_count
        )

    elif shape == "box":
        return _compile_box(
            pixel_positions, start_pos, end_pos, size, color,
            clip.get("startS", 0), clip.get("durationS", duration),
            fx_dur, easing, string_offset, string_count
        )

    return []


def _compile_sphere_sweep(pixels, start_pos, end_pos, radius, color,
                          clip_start, clip_dur, fx_dur, easing, str_offset, str_count):
    """Compile a sphere sweep into WIPE_SEQ or SOLID steps."""
    n = len(pixels)
    if n == 0:
        return []

    # Compute peak time for first and last pixel
    time_scale = clip_dur / fx_dur if fx_dur > 0 else 1

    first_peak = _sphere_intersection_time(pixels[0], start_pos, end_pos, radius, fx_dur, easing)
    last_peak = _sphere_intersection_time(pixels[-1], start_pos, end_pos, radius, fx_dur, easing)

    # If neither pixel is ever intersected, this string is out of range
    if first_peak is None and last_peak is None:
        return []

    # Check middle pixel too
    mid_peak = _sphere_intersection_time(pixels[n // 2], start_pos, end_pos, radius, fx_dur, easing)

    # Determine if this is a sweep (peaks at different times) or simultaneous
    peak_times = []
    if first_peak: peak_times.append(("first", 0, first_peak))
    if mid_peak: peak_times.append(("mid", n // 2, mid_peak))
    if last_peak: peak_times.append(("last", n - 1, last_peak))

    if len(peak_times) < 2:
        # Only one pixel hit — emit SOLID for the lit duration
        pt = peak_times[0][2] if peak_times else (0, 0, clip_dur)
        enter = clip_start + pt[0] * time_scale
        exit_t = clip_start + pt[2] * time_scale
        return [{
            "type": ACT_SOLID,
            "params": {"r": color[0], "g": color[1], "b": color[2]},
            "startS": round(enter, 2), "durationS": round(max(1, exit_t - enter), 2),
            "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
        }]

    # Check if peaks are spread out (sweep) or clustered (simultaneous)
    t_first = peak_times[0][2][1]  # peak time of first pixel
    t_last = peak_times[-1][2][1]  # peak time of last pixel
    sweep_duration = abs(t_last - t_first)

    if sweep_duration < 0.5:
        # All pixels peak within 0.5s — treat as SOLID (whole string at once)
        enter = clip_start + min(p[2][0] for p in peak_times) * time_scale
        exit_t = clip_start + max(p[2][2] for p in peak_times) * time_scale
        return [{
            "type": ACT_SOLID,
            "params": {"r": color[0], "g": color[1], "b": color[2]},
            "startS": round(enter, 2), "durationS": round(max(1, exit_t - enter), 2),
            "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
        }]

    # It's a sweep! Compute WIPE_SEQ parameters
    # Direction: which pixel peaks first?
    if t_first < t_last:
        direction = 0  # East (forward: pixel 0 → pixel N)
    else:
        direction = 2  # West (reverse: pixel N → pixel 0)

    # Speed: time per pixel
    speed_ms = max(5, int(sweep_duration * time_scale * 1000 / n))

    # Start time: when first pixel enters
    enter_times = [p[2][0] for p in peak_times]
    t_start = clip_start + min(enter_times) * time_scale

    # Duration: from first enter to last exit
    exit_times = [p[2][2] for p in peak_times]
    t_end = clip_start + max(exit_times) * time_scale

    return [{
        "type": ACT_WIPE,
        "params": {
            "r": color[0], "g": color[1], "b": color[2],
            "speedMs": speed_ms, "direction": direction,
        },
        "startS": round(t_start, 2), "durationS": round(max(1, t_end - t_start), 2),
        "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
    }]


def _compile_plane_sweep(pixels, start_pos, end_pos, normal, thickness, color,
                         clip_start, clip_dur, fx_dur, easing, str_offset, str_count):
    """Compile a plane sweep — similar logic to sphere but with planar distance."""
    n = len(pixels)
    if n == 0:
        return []

    # Normalize normal
    mag = math.sqrt(sum(x * x for x in normal)) or 1
    norm = [x / mag for x in normal]

    time_scale = clip_dur / fx_dur if fx_dur > 0 else 1

    # Compute when the plane passes each pixel
    def plane_peak_time(px):
        # The plane moves with the motion path. Find when dot(plane_center, normal) = dot(pixel, normal)
        px_proj = sum(px[i] * norm[i] for i in range(3))
        best_t, best_d = 0, float('inf')
        for step in range(101):
            raw_t = step / 100
            eased = _ease(raw_t, easing)
            center = [_lerp(start_pos[j], end_pos[j], eased) for j in range(3)]
            plane_offset = sum(center[i] * norm[i] for i in range(3))
            d = abs(px_proj - plane_offset)
            if d < best_d:
                best_d = d
                best_t = raw_t
        if best_d > thickness:
            return None
        return best_t * fx_dur

    first_t = plane_peak_time(pixels[0])
    last_t = plane_peak_time(pixels[-1])

    if first_t is None and last_t is None:
        return []

    # Same sweep logic as sphere
    if first_t is not None and last_t is not None:
        sweep = abs(last_t - first_t)
        if sweep > 0.5:
            direction = 0 if first_t < last_t else 2
            speed_ms = max(5, int(sweep * time_scale * 1000 / n))
            t_start = clip_start + min(first_t, last_t) * time_scale
            return [{
                "type": ACT_WIPE,
                "params": {"r": color[0], "g": color[1], "b": color[2],
                           "speedMs": speed_ms, "direction": direction},
                "startS": round(t_start, 2),
                "durationS": round(max(1, sweep * time_scale + thickness / 500), 2),
                "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
            }]

    # No sweep — SOLID
    t = first_t if first_t is not None else last_t
    return [{
        "type": ACT_SOLID,
        "params": {"r": color[0], "g": color[1], "b": color[2]},
        "startS": round(clip_start + (t or 0) * time_scale, 2),
        "durationS": round(max(1, clip_dur), 2),
        "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
    }]


def _compile_box(pixels, start_pos, end_pos, size, color,
                 clip_start, clip_dur, fx_dur, easing, str_offset, str_count):
    """Compile a box field — SOLID for the duration it covers the string."""
    # Simplified: just emit SOLID for the clip duration
    return [{
        "type": ACT_SOLID,
        "params": {"r": color[0], "g": color[1], "b": color[2]},
        "startS": round(clip_start, 2), "durationS": round(max(1, clip_dur), 2),
        "ledOffset": str_offset, "ledCount": str_count, "stringIndex": 0,
    }]


def _compile_dmx_fixture(clip, effect, fixture_pos, aim_point, profile_info, duration,
                         beam_pixels=None, mounted_inverted=False):
    """Compile a spatial effect clip for a DMX fixture (moving head / par).

    beam_pixels: list of [x,y,z] sample points along the beam cone. If provided,
    the spatial effect is evaluated against all samples and the brightest color is used.
    This means a spatial sphere passing through the beam cone (not just the fixture
    position) will trigger the fixture.
    """
    from spatial_engine import compute_pan_tilt, effect_aim_point, evaluate_spatial_effect

    if not effect:
        return []

    eval_pixels = beam_pixels or [fixture_pos]

    def _best_color(colors_list):
        """Return the brightest color from a list of [r,g,b] values."""
        best = [0, 0, 0]
        best_sum = 0
        for c in colors_list:
            s = c[0] + c[1] + c[2]
            if s > best_sum:
                best = c
                best_sum = s
        return best

    motion = effect.get("motion", {})
    start_pos = motion.get("startPos", [0, 0, 0])
    end_pos = motion.get("endPos", [0, 0, 0])
    clip_start = clip.get("startS", 0)
    clip_dur = clip.get("durationS", duration)

    pan_range = profile_info.get("panRange", 0) if profile_info else 0
    tilt_range = profile_info.get("tiltRange", 0) if profile_info else 0
    has_pt = pan_range > 0 and tilt_range > 0

    # Check if effect moves
    is_moving = _dist(start_pos, end_pos) > 10  # > 10mm

    if is_moving and has_pt:
        # Time-slice into 1-second segments for smooth tracking
        segments = []
        slice_dur = 1.0
        t = 0
        while t < clip_dur:
            seg_dur = min(slice_dur, clip_dur - t)
            mid_t = t + seg_dur / 2
            aim = effect_aim_point(effect, mid_t)
            colors = evaluate_spatial_effect(effect, eval_pixels, mid_t)
            c = _best_color(colors) if colors else [0, 0, 0]
            pt = compute_pan_tilt(fixture_pos, aim, pan_range, tilt_range,
                                 mounted_inverted=mounted_inverted)
            segments.append({
                "type": ACT_DMX_SCENE,
                "params": {
                    "r": c[0], "g": c[1], "b": c[2],
                    "dimmer": 255 if any(v > 0 for v in c) else 0,
                    "pan": pt[0] if pt else 0.5,
                    "tilt": pt[1] if pt else 0.5,
                },
                "startS": round(clip_start + t, 2),
                "durationS": round(seg_dur, 2),
            })
            t += slice_dur
        return segments
    else:
        # Static or no pan/tilt: single or time-sliced segments
        if is_moving:
            # Moving effect but no pan/tilt — still time-slice color
            segments = []
            slice_dur = 1.0
            t = 0
            while t < clip_dur:
                seg_dur = min(slice_dur, clip_dur - t)
                mid_t = t + seg_dur / 2
                colors = evaluate_spatial_effect(effect, eval_pixels, mid_t)
                c = _best_color(colors) if colors else [0, 0, 0]
                segments.append({
                    "type": ACT_DMX_SCENE,
                    "params": {
                        "r": c[0], "g": c[1], "b": c[2],
                        "dimmer": 255 if any(v > 0 for v in c) else 0,
                        "pan": 0.5, "tilt": 0.5,
                    },
                    "startS": round(clip_start + t, 2),
                    "durationS": round(seg_dur, 2),
                })
                t += slice_dur
            return segments
        # Static: single segment
        mid_t = clip_dur / 2
        if has_pt:
            aim = aim_point
            pt = compute_pan_tilt(fixture_pos, aim, pan_range, tilt_range,
                                 mounted_inverted=mounted_inverted)
        else:
            pt = None
        colors = evaluate_spatial_effect(effect, eval_pixels, mid_t)
        c = _best_color(colors) if colors else [0, 0, 0]
        return [{
            "type": ACT_DMX_SCENE,
            "params": {
                "r": c[0], "g": c[1], "b": c[2],
                "dimmer": 255 if any(v > 0 for v in c) else 0,
                "pan": pt[0] if pt else 0.5,
                "tilt": pt[1] if pt else 0.5,
            },
            "startS": round(clip_start, 2),
            "durationS": round(max(1, clip_dur), 2),
        }]


# ── Main bake function ───────────────────────────────────────────────────────

def bake_timeline(timeline, fixtures, spatial_fx, layout,
                  progress=None, actions=None,
                  resolve_fn=None, evaluate_fn=None, blend_fn=None):
    """Compile a timeline into per-fixture action sequences.

    Instead of rendering 40Hz frames, this directly analyzes each clip's spatial
    relationship with each fixture's pixel positions and computes the optimal
    action type + parameters + timing.
    """
    duration = timeline.get("durationS", 60)

    if progress:
        progress.status = "resolving fixtures"

    fix_map = {f["id"]: f for f in fixtures}
    pos_map = {p["id"]: p for p in layout.get("children", [])}
    fx_map = {f["id"]: f for f in spatial_fx}
    act_map = {a["id"]: a for a in (actions or [])}

    # Resolve pixel positions and string info per fixture
    fixture_data = {}
    for f in fixtures:
        fid = f["id"]
        # Look up position by fixture ID first, then childId
        lp = pos_map.get(fid, pos_map.get(f.get("childId"), {}))
        child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
        ft = f.get("fixtureType", "led")
        if ft == "dmx":
            # DMX fixture: sample points along beam axis (fixture → rotation direction)
            # so spatial effects intersecting the beam cone trigger the fixture
            aim = _rotation_to_aim(f.get("rotation", [0, 0, 0]), child_pos)
            beam_samples = [child_pos]  # fixture position
            n_samples = 5  # sample along beam at 0%, 25%, 50%, 75%, 100%
            for si in range(1, n_samples + 1):
                t_s = si / n_samples
                beam_samples.append([
                    child_pos[0] + (aim[0] - child_pos[0]) * t_s,
                    child_pos[1] + (aim[1] - child_pos[1]) * t_s,
                    child_pos[2] + (aim[2] - child_pos[2]) * t_s,
                ])
            # Also add points at beam width spread at the aim end
            _pid = f.get("dmxProfileId")
            _dmx_profile_info = None
            if _pid:
                from dmx_profiles import ProfileLibrary
                _plib = ProfileLibrary()
                _dmx_profile_info = _plib.channel_info(_pid)
            beam_width_deg = 15
            if _dmx_profile_info:
                beam_width_deg = _dmx_profile_info.get("beamWidth", 15) or 15
            import math as _math
            beam_len = _math.sqrt(sum((aim[i] - child_pos[i])**2 for i in range(3)))
            half_spread = _math.tan(_math.radians(beam_width_deg / 2)) * beam_len
            if half_spread > 50:  # only if meaningful spread
                # Add spread points perpendicular to beam at aim end
                dx = aim[0] - child_pos[0]
                dz = aim[2] - child_pos[2]
                perp_len = _math.sqrt(dx*dx + dz*dz) or 1
                px, pz = -dz / perp_len * half_spread, dx / perp_len * half_spread
                beam_samples.append([aim[0] + px, aim[1], aim[2] + pz])
                beam_samples.append([aim[0] - px, aim[1], aim[2] - pz])
            pixels = beam_samples
            strings_info = [{"offset": 0, "count": len(beam_samples), "sdir": 0}]
        else:
            resolve_input = {
                "type": f.get("type", "linear"),
                "childPos": child_pos,
                "strings": f.get("strings", []),
                "rotation": f.get("rotation", [0, 0, 0]),
                "aoeRadius": f.get("aoeRadius", 1000),
            }
            from spatial_engine import resolve_fixture as _resolve
            resolved = (resolve_fn or _resolve)(resolve_input)
            pixels = resolved.get("pixelPositions", [])
            strings_info = []
            offset = 0
            for s in resolve_input.get("strings", []):
                leds = s.get("leds", 0)
                if leds > 0:
                    strings_info.append({"offset": offset, "count": leds, "sdir": s.get("sdir", 0)})
                    offset += leds
        fixture_data[fid] = {
            "pixels": pixels, "pixelCount": len(pixels), "strings": strings_info,
            "fixtureType": ft,
            "profileInfo": _dmx_profile_info if ft == "dmx" else None,
            "rotation": f.get("rotation", [0, 0, 0]) if ft == "dmx" else None,
            "position": child_pos,
            "mountedInverted": f.get("mountedInverted", False),
        }

    # Expand allPerformers and group fixtures into per-fixture tracks
    _fix_map = {f["id"]: f for f in fixtures}
    raw_tracks = timeline.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            for f in fixtures:
                if f.get("type") != "group":
                    tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
            fid = track.get("fixtureId")
            grp = _fix_map.get(fid)
            if grp and grp.get("type") == "group" and grp.get("childIds"):
                for mid in grp["childIds"]:
                    if mid in _fix_map:
                        tracks.append({"fixtureId": mid, "clips": list(track.get("clips", []))})
            else:
                tracks.append(track)

    # Merge clips per fixture, stamping each clip with track priority
    # Higher track index = higher priority (overrides lower tracks at same time)
    merged_clips = {}
    for ti, track in enumerate(tracks):
        fid = track.get("fixtureId")
        if fid not in fixture_data:
            continue
        if fid not in merged_clips:
            merged_clips[fid] = []
        for clip in track.get("clips", []):
            c = dict(clip)
            c["_priority"] = ti  # track index as priority
            merged_clips[fid].append(c)

    if progress:
        progress.total_fixtures = len(merged_clips)
        progress.total_frames = len(merged_clips) * 10  # rough estimate
        progress.status = "compiling"

    result = {"fixtures": {}, "lsq_files": {}, "totalFrames": 0, "fps": BAKE_FPS}

    for fid, clips in merged_clips.items():
        fdata = fixture_data.get(fid, {})
        pixels = fdata.get("pixels", [])
        strings = fdata.get("strings", [])
        all_segments = []

        for clip in clips:
            clip_pri = clip.get("_priority", 0)
            _seg_before = len(all_segments)
            # Action-type clips: emit directly
            aid = clip.get("actionId")
            if aid is not None:
                act = act_map.get(aid)
                if act:
                    act_type = act.get("type", 0)
                    params = {k: act.get(k, 0) for k in (
                        "r", "g", "b", "r2", "g2", "b2", "speedMs", "periodMs",
                        "spawnMs", "minBri", "spacing", "paletteId", "cooling",
                        "sparking", "direction", "tailLen", "density", "decay", "fadeSpeed")}
                    # DMX fixtures: convert classic LED actions to DMX scene
                    if fdata.get("fixtureType") == "dmx" and act_type < ACT_DMX_SCENE:
                        params["dimmer"] = act.get("dimmer", 255 if (params["r"] or params["g"] or params["b"]) else 0)
                        params["pan"] = act.get("pan", 0.5)
                        params["tilt"] = act.get("tilt", 0.5)
                        if act.get("strobe") is not None:
                            params["strobe"] = act["strobe"]
                        if act.get("gobo") is not None:
                            params["gobo"] = act["gobo"]
                        act_type = ACT_DMX_SCENE
                    # DMX Scene actions: pass through all DMX params
                    if act_type >= ACT_DMX_SCENE:
                        for k in ("dimmer", "pan", "tilt", "strobe", "gobo",
                                  "colorWheel", "prism", "focus", "zoom"):
                            if k not in params and act.get(k) is not None:
                                params[k] = act[k]
                    # Pan/Tilt Move: expand into time-sliced DMX_SCENE segments.
                    # Only carries pan/tilt (+ dimmer if set) — does NOT carry r/g/b
                    # so color from lower-priority tracks can show through.
                    if act_type == ACT_DMX_PT_MOVE:
                        clip_start_s = clip.get("startS", 0)
                        clip_dur_s = clip.get("durationS", 1)
                        pt_dimmer = act.get("dimmer")
                        # Prefer stage coordinate positions (ptStartPos/ptEndPos) over
                        # legacy DMX-normalized values.
                        start_pos = act.get("ptStartPos")
                        end_pos = act.get("ptEndPos")
                        prof_info = fdata.get("profileInfo") or {}
                        pan_range = prof_info.get("panRange", 0)
                        tilt_range = prof_info.get("tiltRange", 0)
                        if start_pos and end_pos and pan_range > 0 and tilt_range > 0:
                            from spatial_engine import compute_pan_tilt as _cpt
                            fx_pos = fdata.get("position", [0, 0, 0])
                            mounted_inv = bool(fdata.get("mountedInverted"))
                            pt_s = _cpt(fx_pos, start_pos, pan_range, tilt_range,
                                        mounted_inverted=mounted_inv)
                            pt_e = _cpt(fx_pos, end_pos, pan_range, tilt_range,
                                        mounted_inverted=mounted_inv)
                            ps, ts = pt_s if pt_s else (0.0, 0.5)
                            pe, te = pt_e if pt_e else (1.0, 0.5)
                        else:
                            # Fallback for legacy actions without stage coords
                            ps = act.get("panStart", 0)
                            pe = act.get("panEnd", 1)
                            ts = act.get("tiltStart", 0.5)
                            te = act.get("tiltEnd", 0.5)
                        # Scale slice duration to keep segment count reasonable
                        slice_dur = 1.0 if clip_dur_s > 15 else 0.5
                        t = 0
                        while t < clip_dur_s:
                            sd = min(slice_dur, clip_dur_s - t)
                            frac = (t + sd / 2) / clip_dur_s if clip_dur_s > 0 else 0
                            sp = {"pan": ps + (pe - ps) * frac,
                                  "tilt": ts + (te - ts) * frac}
                            if pt_dimmer is not None:
                                sp["dimmer"] = pt_dimmer
                            all_segments.append({
                                "type": ACT_DMX_SCENE,
                                "params": sp,
                                "startS": round(clip_start_s + t, 2),
                                "durationS": round(sd, 2),
                            })
                            t += slice_dur
                    # Gobo/Color Wheel: emit as DMX Scene
                    elif act_type == ACT_DMX_GOBO or act_type == ACT_DMX_COLOR_WHEEL:
                        seg = {
                            "type": ACT_DMX_SCENE,
                            "params": params,
                            "startS": clip.get("startS", 0),
                            "durationS": clip.get("durationS", 1),
                        }
                        all_segments.append(seg)
                    else:
                        seg = {
                            "type": act_type,
                            "params": params,
                            "startS": clip.get("startS", 0),
                            "durationS": clip.get("durationS", 1),
                        }
                        all_segments.append(seg)
                # Stamp priority on action-produced segments before continuing
                for _si in range(_seg_before, len(all_segments)):
                    all_segments[_si]["_pri"] = clip_pri
                continue

            # Spatial effect clips: compile from spatial math
            eid = clip.get("effectId")
            fx = fx_map.get(eid)
            if not fx:
                continue

            if fdata.get("fixtureType") == "dmx":
                # DMX fixture: compile with beam cone samples for intersection
                fx_pos = pixels[0] if pixels else [0, 0, 0]
                aim_from_rot = _rotation_to_aim(fdata.get("rotation", [0, 0, 0]), fx_pos)
                steps = _compile_dmx_fixture(
                    clip, fx, fx_pos,
                    aim_from_rot,
                    fdata.get("profileInfo"),
                    duration,
                    beam_pixels=pixels,
                    mounted_inverted=bool(fdata.get("mountedInverted")),
                )
                all_segments.extend(steps)
            elif strings:
                # Multi-string: compile per-string
                for si, sinfo in enumerate(strings):
                    off = sinfo["offset"]
                    cnt = sinfo["count"]
                    str_pixels = pixels[off:off + cnt]
                    steps = _compile_clip_for_string(clip, fx, str_pixels, off, cnt, duration)
                    for step in steps:
                        step["stringIndex"] = si
                    all_segments.extend(steps)
            elif pixels:
                # Single string
                steps = _compile_clip_for_string(clip, fx, pixels, 0, len(pixels), duration)
                all_segments.extend(steps)

            # Stamp track priority on all segments produced by this clip
            for _si in range(_seg_before, len(all_segments)):
                all_segments[_si]["_pri"] = clip_pri

        # Sort by start time, then by priority descending (higher track overrides)
        # Playback uses first matching segment, so higher priority must sort first
        all_segments.sort(key=lambda s: (s.get("startS", 0), -s.get("_pri", 0)))
        all_segments = all_segments[:64]

        result["fixtures"][fid] = {
            "segments": all_segments,
            "pixelCount": len(pixels),
            "stringCount": len(strings),
        }
        result["lsq_files"][fid] = b""  # no raw frame data in smart bake

        if progress:
            progress.fixtures_done += 1
            progress.current_frame = progress.fixtures_done * 10
            fix_name = fix_map.get(fid, {}).get("name", str(fid))
            progress.segments[fix_name] = len(all_segments)

    # Generate preview data for emulator
    # Solid-colour actions → [r, g, b]
    # Procedural actions  → {"t": type, "p": {params}, "e": elapsed}
    #   The SPA computes per-pixel colours client-side from the action metadata.
    PROCEDURAL_TYPES = {ACT_RAINBOW, ACT_FIRE, ACT_CHASE, ACT_COMET,
                        ACT_TWINKLE, ACT_STROBE, ACT_WIPE, ACT_SCANNER,
                        ACT_SPARKLE, ACT_BREATHE, ACT_FADE, ACT_GRADIENT}
    preview = {}
    n_seconds = int(math.ceil(duration))
    for fid, clips in merged_clips.items():
        fdata = fixture_data.get(fid, {})
        strings = fdata.get("strings", [])
        pixels = fdata.get("pixels", [])
        fix_segments = result["fixtures"].get(fid, {}).get("segments", [])

        # DMX fixtures: emit beam metadata per second (dict, not array)
        if fdata.get("fixtureType") == "dmx":
            fix_preview = []
            prof_info = fdata.get("profileInfo") or {}
            for sec in range(n_seconds):
                dmx_entry = {"r": 0, "g": 0, "b": 0, "pan": 0.5, "tilt": 0.5,
                             "dimmer": 0, "beamWidth": prof_info.get("beamWidth", 15)}
                for seg in fix_segments:
                    ss = seg.get("startS", 0)
                    sd = seg.get("durationS", 1)
                    if ss <= sec < ss + sd:
                        p = seg.get("params", {})
                        dmx_entry = {
                            "r": p.get("r", 0), "g": p.get("g", 0), "b": p.get("b", 0),
                            "pan": p.get("pan", 0.5), "tilt": p.get("tilt", 0.5),
                            "dimmer": p.get("dimmer", 0),
                            "beamWidth": prof_info.get("beamWidth", 15),
                        }
                        break
                fix_preview.append(dmx_entry)
            preview[fid] = fix_preview
            continue

        if not strings and not pixels:
            continue  # skip fixtures with no LED data
        n_strings = max(len(strings), 1)

        fix_preview = []
        for sec in range(n_seconds):
            string_colors = []
            for si in range(n_strings):
                # Find active segment for this string at this second
                entry = [0, 0, 0]
                for seg in fix_segments:
                    seg_si = seg.get("stringIndex")
                    if seg_si is not None and seg_si != si:
                        continue
                    ss = seg.get("startS", 0)
                    sd = seg.get("durationS", 1)
                    if ss <= sec < ss + sd:
                        p = seg.get("params", {})
                        stype = seg.get("type", 0)
                        elapsed = sec - ss
                        if stype in PROCEDURAL_TYPES:
                            # Emit action metadata — SPA renders per-pixel
                            entry = {"t": stype, "p": p, "e": elapsed}
                        elif stype == ACT_SOLID:
                            entry = [p.get("r", 0), p.get("g", 0), p.get("b", 0)]
                        break
                string_colors.append(entry)
            fix_preview.append(string_colors)
        preview[fid] = fix_preview
    result["preview"] = preview

    if progress:
        progress.status = "complete"
        progress.current_frame = progress.total_frames
        progress.done = True

    return result


def pack_lsq_zip(lsq_files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fix_id, data in lsq_files.items():
            if data:
                zf.writestr(f"fixture_{fix_id}.lsq", data)
    return buf.getvalue()


def segments_to_load_steps(segments, max_steps=16):
    steps = []
    for seg in segments[:max_steps]:
        step = {
            "actionType": seg["type"],
            "durationS": max(1, int(math.ceil(seg.get("durationS", 1)))),
            "delayMs": 0,
            "r": seg["params"].get("r", 0),
            "g": seg["params"].get("g", 0),
            "b": seg["params"].get("b", 0),
        }
        if seg["type"] == ACT_FADE:
            step["r2"] = seg["params"].get("r2", 0)
            step["g2"] = seg["params"].get("g2", 0)
            step["b2"] = seg["params"].get("b2", 0)
            step["speedMs"] = seg["params"].get("speedMs", 1000)
        if seg["type"] == ACT_WIPE:
            step["speedMs"] = seg["params"].get("speedMs", 50)
            step["direction"] = seg["params"].get("direction", 0)
        # Pass through per-string targeting
        if "ledOffset" in seg:
            step["_ledOffset"] = seg["ledOffset"]
            step["_ledCount"] = seg["ledCount"]
            step["_stringIndex"] = seg.get("stringIndex", 0)
        steps.append(step)
    # Append a final blackout so LEDs turn off when the show ends naturally
    if steps and steps[-1]["actionType"] != ACT_BLACKOUT and len(steps) < max_steps:
        steps.append({"actionType": ACT_BLACKOUT, "durationS": 1, "delayMs": 0,
                       "r": 0, "g": 0, "b": 0})
    return steps
