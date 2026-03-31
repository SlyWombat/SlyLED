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


# ── Main bake function ───────────────────────────────────────────────────────

def bake_timeline(timeline, fixtures, spatial_fx, layout, resolve_fn, evaluate_fn, blend_fn,
                  progress=None, actions=None):
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
        lp = pos_map.get(f.get("childId"), {})
        child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
        resolve_input = {
            "type": f.get("type", "linear"),
            "childPos": child_pos,
            "strings": f.get("strings", []),
            "rotation": f.get("rotation", [0, 0, 0]),
            "aoeRadius": f.get("aoeRadius", 1000),
        }
        resolved = resolve_fn(resolve_input)
        pixels = resolved.get("pixelPositions", [])
        strings_info = []
        offset = 0
        for s in resolve_input.get("strings", []):
            leds = s.get("leds", 0)
            if leds > 0:
                strings_info.append({"offset": offset, "count": leds, "sdir": s.get("sdir", 0)})
                offset += leds
        fixture_data[fid] = {"pixels": pixels, "pixelCount": len(pixels), "strings": strings_info}

    # Expand allPerformers tracks
    raw_tracks = timeline.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            for f in fixtures:
                tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
            tracks.append(track)

    # Merge clips per fixture
    merged_clips = {}
    for track in tracks:
        fid = track.get("fixtureId")
        if fid not in fixture_data:
            continue
        if fid not in merged_clips:
            merged_clips[fid] = []
        merged_clips[fid].extend(track.get("clips", []))

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
            # Action-type clips: emit directly
            aid = clip.get("actionId")
            if aid is not None:
                act = act_map.get(aid)
                if act:
                    seg = {
                        "type": act.get("type", 0),
                        "params": {k: act.get(k, 0) for k in (
                            "r", "g", "b", "r2", "g2", "b2", "speedMs", "periodMs",
                            "spawnMs", "minBri", "spacing", "paletteId", "cooling",
                            "sparking", "direction", "tailLen", "density", "decay", "fadeSpeed")},
                        "startS": clip.get("startS", 0),
                        "durationS": clip.get("durationS", 1),
                    }
                    all_segments.append(seg)
                continue

            # Spatial effect clips: compile from spatial math
            eid = clip.get("effectId")
            fx = fx_map.get(eid)
            if not fx:
                continue

            if strings:
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

        # Sort by start time, cap at 16
        all_segments.sort(key=lambda s: s.get("startS", 0))
        all_segments = all_segments[:16]

        result["fixtures"][fid] = {
            "segments": all_segments,
            "pixelCount": len(pixels),
            "stringCount": len(strings),
        }
        result["lsq_files"][fid] = b""  # no raw frame data in smart bake

        if progress:
            progress.fixtures_done += 1
            progress.current_frame = progress.fixtures_done * 10
            progress.segments[str(fid)] = len(all_segments)

    # Generate preview data for emulator (1 color per string per second)
    preview = {}
    n_seconds = int(math.ceil(duration))
    for fid, clips in merged_clips.items():
        fdata = fixture_data.get(fid, {})
        strings = fdata.get("strings", [])
        pixels = fdata.get("pixels", [])
        n_strings = max(len(strings), 1)
        fix_segments = result["fixtures"].get(fid, {}).get("segments", [])

        fix_preview = []
        for sec in range(n_seconds):
            string_colors = []
            for si in range(n_strings):
                # Find active segment for this string at this second
                color = [0, 0, 0]
                for seg in fix_segments:
                    seg_si = seg.get("stringIndex")
                    # Match: either no string index (whole fixture) or matching string
                    if seg_si is not None and seg_si != si:
                        continue
                    ss = seg.get("startS", 0)
                    sd = seg.get("durationS", 1)
                    if ss <= sec < ss + sd:
                        p = seg.get("params", {})
                        color = [p.get("r", 0), p.get("g", 0), p.get("b", 0)]
                        stype = seg.get("type", 0)
                        if sum(color) == 0 and stype > 0:
                            # Generate representative preview color for procedural effects
                            elapsed = sec - ss
                            if stype == ACT_RAINBOW:
                                # Cycle through hue over time
                                hue = (elapsed * 30) % 360
                                if hue < 60: color = [255, int(hue*255/60), 0]
                                elif hue < 120: color = [int((120-hue)*255/60), 255, 0]
                                elif hue < 180: color = [0, 255, int((hue-120)*255/60)]
                                elif hue < 240: color = [0, int((240-hue)*255/60), 255]
                                elif hue < 300: color = [int((hue-240)*255/60), 0, 255]
                                else: color = [255, 0, int((360-hue)*255/60)]
                            elif stype == ACT_FIRE:
                                color = [255, 80 + int(elapsed * 3) % 80, 0]
                            elif stype == ACT_TWINKLE:
                                color = [200 + (elapsed * 17) % 55, 200 + (elapsed * 13) % 55, 200 + (elapsed * 19) % 55]
                            elif stype == ACT_SPARKLE:
                                color = [180, 180, 220]
                            elif stype == ACT_CHASE:
                                color = [p.get("r", 0) or 100, p.get("g", 0) or 200, p.get("b", 0) or 255]
                            elif stype == ACT_BREATHE:
                                base = [p.get("r", 200), p.get("g", 100), p.get("b", 255)]
                                bri = 0.5 + 0.5 * math.sin(elapsed * math.pi / max(p.get("periodMs", 3000) / 1000, 1))
                                color = [int(c * bri) for c in base]
                            else:
                                color = [128, 128, 128]
                        break
                string_colors.append(color)
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
    return steps
