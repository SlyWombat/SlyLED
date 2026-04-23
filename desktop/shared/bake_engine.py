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
import logging
import math
import struct
import time
import zipfile

log = logging.getLogger("slyled")

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
    # Route through rotation_from_layout so the rotation[] index→semantic
    # mapping lives in one place (camera_math). #600.
    try:
        from camera_math import rotation_from_layout
        rx, ry, _roll = rotation_from_layout(rotation)
    except Exception:
        rx = rotation[0] if rotation else 0
        ry = rotation[1] if rotation and len(rotation) > 1 else 0
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


def _compile_capability_for_string(clip, effect, pixel_positions,
                                   string_offset, string_count, duration):
    """Compile a spatial effect for an LED string via the capability-layer
    evaluator (docs/mover-alignment-review.md §8.1b).

    Replaces the per-shape compilers (_compile_sphere_sweep,
    _compile_plane_sweep, _compile_box) with one generic function that
    uses shape_coverage_time() to detect sweep patterns and emits the
    same ACT_WIPE / ACT_SOLID output shape the children already consume.
    """
    from spatial_engine import shape_coverage_time

    if not pixel_positions or not effect:
        return []

    n = len(pixel_positions)
    color = [effect.get("r", 255), effect.get("g", 255), effect.get("b", 255)]
    motion = effect.get("motion") or {}
    fx_dur = motion.get("durationS", duration) or duration
    clip_start = clip.get("startS", 0)
    clip_dur = clip.get("durationS", duration)
    time_scale = clip_dur / fx_dur if fx_dur > 0 else 1

    # Sample coverage at first / mid / last pixels — same 3-point probe the
    # old sphere compiler used. Finer scans are unnecessary; sweep detection
    # only needs to know relative peak ordering.
    probes = [(0, pixel_positions[0])]
    if n > 2:
        probes.append((n // 2, pixel_positions[n // 2]))
    if n > 1:
        probes.append((n - 1, pixel_positions[-1]))

    covs = []
    for idx, px in probes:
        cov = shape_coverage_time(effect, px)
        if cov is not None:
            covs.append((idx, cov))

    if not covs:
        return []

    enter_times = [c[1][0] for c in covs]
    peak_times  = [c[1][1] for c in covs]
    exit_times  = [c[1][2] for c in covs]

    t_first_peak = peak_times[0]
    t_last_peak = peak_times[-1]
    sweep_duration = abs(t_last_peak - t_first_peak) if len(covs) >= 2 else 0

    t_start = clip_start + min(enter_times) * time_scale
    t_end   = clip_start + max(exit_times) * time_scale
    seg_dur = round(max(1, t_end - t_start), 2)

    if sweep_duration > 0.5:
        # WIPE — pixels peak at different times → sweep
        direction = 0 if t_first_peak < t_last_peak else 2  # 0=forward, 2=reverse
        speed_ms = max(5, int(sweep_duration * time_scale * 1000 / n))
        return [{
            "type": ACT_WIPE,
            "params": {
                "r": color[0], "g": color[1], "b": color[2],
                "speedMs": speed_ms, "direction": direction,
            },
            "startS": round(t_start, 2),
            "durationS": seg_dur,
            "ledOffset": string_offset,
            "ledCount": string_count,
            "stringIndex": 0,
        }]

    # SOLID — all pixels peak within 0.5s (includes the single-pixel-hit and
    # non-moving-box cases the old code handled separately)
    return [{
        "type": ACT_SOLID,
        "params": {"r": color[0], "g": color[1], "b": color[2]},
        "startS": round(t_start, 2),
        "durationS": seg_dur,
        "ledOffset": string_offset,
        "ledCount": string_count,
        "stringIndex": 0,
    }]


def _compile_capability_for_dmx(clip, effect, fixture_pos, profile_info, duration,
                                beam_pixels=None, mounted_inverted=False,
                                aim_override=None, slice_s=0.05):
    """Compile a spatial effect for a DMX fixture via the capability-layer
    evaluator. Replaces _compile_dmx_fixture.

    Samples evaluate_primitive at slice_s intervals (default 0.05s per Q9
    in the review). Consecutive slices with identical output are merged,
    so a static clip still collapses to a single segment and a slow-moving
    clip emits only as many segments as there are distinct outputs.

    Args:
        clip: timeline clip dict (startS, durationS, …)
        effect: spatial effect dict (shape, color, size, motion)
        fixture_pos: [x, y, z] stage mm — the fixture's location
        profile_info: DMX profile dict (panRange, tiltRange, channels, …)
        duration: fallback effect duration if the effect's motion.durationS
            is missing
        beam_pixels: optional list of [x, y, z] sample points along the beam
            cone. When provided, the evaluator is run against each sample
            and the brightest-colour output wins — so a field passing
            through the beam cone (not just the fixture origin) triggers
            the fixture.
        mounted_inverted: per-fixture flag for inverted ceiling mounts
        aim_override: force this aim point for static effects (used by the
            bake caller to honour fixture rotation pose for non-moving fx)
        slice_s: sample interval in seconds
    """
    from spatial_engine import (
        evaluate_primitive, compute_pan_tilt, derive_caps,
        CAP_DIRECTION,
    )

    if not effect:
        return []

    clip_start = clip.get("startS", 0)
    clip_dur = clip.get("durationS", duration)
    if clip_dur <= 0:
        return []

    caps = derive_caps(profile_info)
    has_pt = CAP_DIRECTION in caps
    pan_range = profile_info.get("panRange", 0) if profile_info else 0
    tilt_range = profile_info.get("tiltRange", 0) if profile_info else 0

    motion = effect.get("motion") or {}
    start_pos = motion.get("startPos", [0, 0, 0])
    end_pos = motion.get("endPos", [0, 0, 0])
    is_moving = _dist(start_pos, end_pos) > 10

    eval_samples = beam_pixels if beam_pixels else [list(fixture_pos)]

    def _best_at(t):
        """Evaluate all eval_samples at time t; return the brightest
        PrimitiveOutputs (largest sum of rgb channels)."""
        best = None
        best_sum = -1
        for sample in eval_samples:
            out = evaluate_primitive(sample, effect, t)
            s = out.color[0] + out.color[1] + out.color[2]
            if s > best_sum:
                best = out
                best_sum = s
        return best

    def _make_segment(start_s, dur_s, prim, aim_xyz):
        pt = None
        if has_pt and aim_xyz:
            pt = compute_pan_tilt(
                fixture_pos, aim_xyz,
                pan_range, tilt_range,
                mounted_inverted=mounted_inverted,
            )
        return {
            "type": ACT_DMX_SCENE,
            "params": {
                "r": prim.color[0], "g": prim.color[1], "b": prim.color[2],
                "dimmer": int(round(prim.intensity * 255)),
                "pan": pt[0] if pt else 0.5,
                "tilt": pt[1] if pt else 0.5,
            },
            "startS": round(start_s, 3),
            "durationS": round(dur_s, 3),
        }

    # Static clip — single mid-time evaluation, one segment
    if not is_moving:
        mid = clip_dur / 2
        prim = _best_at(mid)
        aim = aim_override if aim_override else prim.aim
        seg = _make_segment(clip_start, max(1, clip_dur), prim, aim)
        return [seg]

    # Moving clip — sample at slice_s intervals, then consolidate adjacent
    # identical segments so static regions of a moving effect collapse.
    segments = []
    t = 0.0
    while t < clip_dur:
        seg_dur = min(slice_s, clip_dur - t)
        mid_t = t + seg_dur / 2
        prim = _best_at(mid_t)
        segments.append(_make_segment(clip_start + t, seg_dur, prim, prim.aim))
        t += slice_s

    # Consolidate consecutive identical segments (same type + same params)
    result = [segments[0]]
    for s in segments[1:]:
        last = result[-1]
        if s["params"] == last["params"] and s["type"] == last["type"]:
            last["durationS"] = round(last["durationS"] + s["durationS"], 3)
        else:
            result.append(s)
    return result


# ── Main bake function ───────────────────────────────────────────────────────

def bake_timeline(timeline, fixtures, spatial_fx, layout,
                  progress=None, actions=None,
                  resolve_fn=None, evaluate_fn=None, blend_fn=None,
                  profile_lib=None, mover_calibrations=None):
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
                if profile_lib:
                    _dmx_profile_info = profile_lib.channel_info(_pid)
                else:
                    from dmx_profiles import ProfileLibrary
                    _dmx_profile_info = ProfileLibrary().channel_info(_pid)
            beam_width_deg = 15
            if _dmx_profile_info:
                beam_width_deg = _dmx_profile_info.get("beamWidth", 15) or 15
            import math as _math
            beam_len = _math.sqrt(sum((aim[i] - child_pos[i])**2 for i in range(3)))
            half_spread = _math.tan(_math.radians(beam_width_deg / 2)) * beam_len
            if half_spread > 50:  # only if meaningful spread
                # Add spread points perpendicular to beam in XY plane (#386)
                # Stage: X=width, Y=depth, Z=height — spread stays at same height
                dx = aim[0] - child_pos[0]
                dy = aim[1] - child_pos[1]
                perp_len = _math.sqrt(dx*dx + dy*dy) or 1
                px, py = -dy / perp_len * half_spread, dx / perp_len * half_spread
                beam_samples.append([aim[0] + px, aim[1] + py, aim[2]])
                beam_samples.append([aim[0] - px, aim[1] - py, aim[2]])
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
                    # Skip if action targets specific fixtures and this isn't one (#363)
                    _scope = act.get("scope", "performer")
                    _tids = act.get("targetIds") or clip.get("fixtureIds")
                    if _scope == "performer-selected" and _tids and fid not in _tids:
                        continue
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
                            fx_pos = fdata.get("position", [0, 0, 0])
                            mounted_inv = bool(fdata.get("mountedInverted"))
                            # Prefer calibration data when available (#366, #368)
                            _mcal_data = (mover_calibrations or {}).get(str(fid))
                            _grid = _mcal_data.get("grid") if _mcal_data else None
                            _cal_used = False
                            if _mcal_data and _mcal_data.get("method") == "manual":
                                # Manual calibration: use affine transform for full extrapolation (#371)
                                from mover_calibrator import affine_pan_tilt as _apt
                                _samples = _mcal_data.get("samples", [])
                                pt_s = _apt(_samples, start_pos[0], start_pos[1])
                                pt_e = _apt(_samples, end_pos[0], end_pos[1])
                                if pt_s and pt_e:
                                    ps, ts = pt_s
                                    pe, te = pt_e
                                    _cal_used = True
                                    log.info("Bake PT Move fid=%s: manual affine pan=%.3f→%.3f tilt=%.3f→%.3f",
                                             fid, ps, pe, ts, te)
                            if not _cal_used and _grid and _mcal_data:
                                # Camera-based calibration: apply center offset
                                _cal_center = (_mcal_data.get("centerPan", 0.5),
                                               _mcal_data.get("centerTilt", 0.5))
                                from spatial_engine import compute_pan_tilt as _cpt
                                _center_target = _mcal_data.get("centerTarget", fx_pos)
                                pt_s_geo = _cpt(fx_pos, start_pos, pan_range, tilt_range,
                                                mounted_inverted=mounted_inv)
                                pt_e_geo = _cpt(fx_pos, end_pos, pan_range, tilt_range,
                                                mounted_inverted=mounted_inv)
                                pt_c_geo = _cpt(fx_pos, _center_target, pan_range, tilt_range,
                                                mounted_inverted=mounted_inv)
                                if pt_s_geo and pt_e_geo and pt_c_geo:
                                    dp = _cal_center[0] - pt_c_geo[0]
                                    dt = _cal_center[1] - pt_c_geo[1]
                                    ps = max(0, min(1, pt_s_geo[0] + dp))
                                    ts = max(0, min(1, pt_s_geo[1] + dt))
                                    pe = max(0, min(1, pt_e_geo[0] + dp))
                                    te = max(0, min(1, pt_e_geo[1] + dt))
                                    _cal_used = True
                                    log.info("Bake PT Move fid=%s: camera cal offset dp=%.3f dt=%.3f", fid, dp, dt)
                            if not _cal_used:
                                from spatial_engine import compute_pan_tilt as _cpt
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
                        # Pre-position segment: pan/tilt at start with dimmer=0 (#372)
                        # Gives the fixture time to physically move before the light turns on.
                        prepos_dur = 0.5  # 500ms settle time
                        prepos_params = {"pan": ps, "tilt": ts, "dimmer": 0}
                        for ck in ("r", "g", "b"):
                            if ck in act:
                                prepos_params[ck] = act[ck]
                        all_segments.append({
                            "type": ACT_DMX_SCENE,
                            "params": prepos_params,
                            "startS": round(clip_start_s, 2),
                            "durationS": prepos_dur,
                        })
                        # Scale slice duration to keep segment count reasonable
                        slice_dur = 1.0 if clip_dur_s > 15 else 0.5
                        t = prepos_dur  # start after pre-position
                        effective_dur = clip_dur_s - prepos_dur
                        while t < clip_dur_s:
                            sd = min(slice_dur, clip_dur_s - t)
                            frac = (t - prepos_dur + sd / 2) / effective_dur if effective_dur > 0 else 0
                            frac = max(0.0, min(1.0, frac))
                            sp = {"pan": ps + (pe - ps) * frac,
                                  "tilt": ts + (te - ts) * frac}
                            if pt_dimmer is not None:
                                sp["dimmer"] = pt_dimmer
                            # Copy RGB from action (#362)
                            for ck in ("r", "g", "b"):
                                if ck in act:
                                    sp[ck] = act[ck]
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
                # DMX fixture: compile via capability-layer evaluator
                # (review §8.1b Q9 — runtime-first evaluator sampled at
                # slice_s for bake-time segments)
                fx_pos = pixels[0] if pixels else [0, 0, 0]
                aim_from_rot = _rotation_to_aim(fdata.get("rotation", [0, 0, 0]), fx_pos)
                steps = _compile_capability_for_dmx(
                    clip, fx, fx_pos,
                    fdata.get("profileInfo"),
                    duration,
                    beam_pixels=pixels,
                    mounted_inverted=bool(fdata.get("mountedInverted")),
                    aim_override=aim_from_rot,
                )
                all_segments.extend(steps)
            elif strings:
                # Multi-string: compile per-string via capability-layer
                for si, sinfo in enumerate(strings):
                    off = sinfo["offset"]
                    cnt = sinfo["count"]
                    str_pixels = pixels[off:off + cnt]
                    steps = _compile_capability_for_string(clip, fx, str_pixels, off, cnt, duration)
                    for step in steps:
                        step["stringIndex"] = si
                    all_segments.extend(steps)
            elif pixels:
                # Single string
                steps = _compile_capability_for_string(clip, fx, pixels, 0, len(pixels), duration)
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
