"""
SlyLED Bake Engine — compile timelines into per-fixture binary sequences.

The baking pipeline:
1. Iterate timeline at 40Hz frame rate
2. Evaluate all active effects per frame per fixture
3. Analyze RGB streams to detect matching action types (action segmentation)
4. Output per-fixture action sequences compatible with existing LoadStepPayload
5. Pack raw frame data into .LSQ binary files

The key innovation: baked sequences compile DOWN to the same 14 action types
children already understand. No firmware changes needed.
"""

import io
import math
import os
import struct
import time
import zipfile

BAKE_FPS = 40
LSQ_MAGIC = b"LSQ\x00"
LSQ_VERSION = 1

# Action type constants (must match Protocol.h)
ACT_BLACKOUT = 0
ACT_SOLID = 1
ACT_FADE = 2
ACT_BREATHE = 3
ACT_CHASE = 4
ACT_RAINBOW = 5
ACT_WIPE = 10


class BakeProgress:
    """Thread-safe bake progress tracker."""
    def __init__(self, total_frames):
        self.total_frames = total_frames
        self.current_frame = 0
        self.status = "starting"
        self.fixtures_done = 0
        self.total_fixtures = 0
        self.segments = {}  # fixture_id → segment count
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


def bake_timeline(timeline, fixtures, spatial_fx, layout, resolve_fn, evaluate_fn, blend_fn, progress=None, actions=None):
    """Bake a timeline into per-fixture action sequences.

    Args:
        timeline: dict with durationS, tracks, loop
        fixtures: list of fixture dicts
        spatial_fx: list of spatial effect dicts
        layout: layout dict with children positions
        resolve_fn: function(fixture_input) → {pixelPositions: [...]}
        evaluate_fn: function(effect, pixels, t) → [[r,g,b],...]
        blend_fn: function(layers, modes) → [[r,g,b],...]
        progress: BakeProgress instance (optional)
        actions: list of classic action dicts (optional, for actionId clips)

    Returns:
        dict: {
            fixtures: {fixture_id: {frames: [...], segments: [...], pixelCount: N}},
            lsq_files: {fixture_id: bytes},
            totalFrames: N,
            fps: 40
        }
    """
    duration = timeline.get("durationS", 60)
    n_frames = int(math.ceil(duration * BAKE_FPS))

    if progress:
        progress.total_frames = n_frames
        progress.status = "resolving fixtures"

    # Build fixture map and resolve pixel positions
    fix_map = {f["id"]: f for f in fixtures}
    pos_map = {p["id"]: p for p in layout.get("children", [])}
    fx_map = {f["id"]: f for f in spatial_fx}
    act_map = {a["id"]: a for a in (actions or [])}

    # Pre-process: expand "allPerformers" tracks into per-fixture tracks
    raw_tracks = timeline.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            # Duplicate this track's clips for every fixture
            for f in fixtures:
                tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
            tracks.append(track)

    # Per-fixture resolved data
    fixture_data = {}  # fix_id → {pixels: [[x,y,z],...], pixelCount: N}

    if progress:
        progress.total_fixtures = len(set(t.get("fixtureId") for t in tracks))

    for track in tracks:
        fix_id = track.get("fixtureId")
        fixture = fix_map.get(fix_id)
        if not fixture or fix_id in fixture_data:
            continue

        # Build resolve input
        lp = pos_map.get(fixture.get("childId"), {})
        child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
        resolve_input = {
            "type": fixture.get("type", "linear"),
            "childPos": child_pos,
            "strings": fixture.get("strings", []),
            "rotation": fixture.get("rotation", [0, 0, 0]),
            "aoeRadius": fixture.get("aoeRadius", 1000),
        }
        resolved = resolve_fn(resolve_input)
        pixels = resolved.get("pixelPositions", [])
        # Store per-string pixel ranges for per-string segmentation
        strings_info = []
        offset = 0
        for s in resolve_input.get("strings", []):
            leds = s.get("leds", 0)
            if leds > 0:
                strings_info.append({"offset": offset, "count": leds, "sdir": s.get("sdir", 0)})
                offset += leds
        fixture_data[fix_id] = {"pixels": pixels, "pixelCount": len(pixels), "strings": strings_info}

    if progress:
        progress.status = "baking frames"

    # Bake frame-by-frame
    # Structure: per_fixture_frames[fix_id][frame_idx] = [[r,g,b], ...]
    per_fixture_frames = {fid: [] for fid in fixture_data}

    for frame_idx in range(n_frames):
        t = frame_idx / BAKE_FPS
        if progress:
            progress.current_frame = frame_idx

        for track in tracks:
            fix_id = track.get("fixtureId")
            if fix_id not in fixture_data:
                continue

            pixels = fixture_data[fix_id]["pixels"]
            if not pixels:
                per_fixture_frames[fix_id].append([[0,0,0]] * max(len(pixels), 1))
                continue

            # Evaluate active clips
            layers = []
            modes = []
            for clip in track.get("clips", []):
                cs = clip.get("startS", 0)
                cd = clip.get("durationS", 1)
                if cs <= t < cs + cd:
                    eid = clip.get("effectId")
                    fx = fx_map.get(eid)
                    if not fx:
                        continue
                    local_t = t - cs
                    motion = fx.get("motion", {})
                    fx_dur = motion.get("durationS", cd) or cd
                    scaled_t = local_t * (fx_dur / cd) if cd > 0 else 0
                    colors = evaluate_fn(fx, pixels, scaled_t)
                    layers.append(colors)
                    modes.append(fx.get("blend", "replace"))

            if layers:
                blended = blend_fn(layers, modes)
                per_fixture_frames[fix_id].append(blended)
            else:
                per_fixture_frames[fix_id].append([[0,0,0]] * len(pixels))

    if progress:
        progress.status = "segmenting actions"

    # Build direct action segments from actionId clips (bypass frame analysis)
    direct_segments = {}  # fix_id → list of segments from classic action clips
    for track in tracks:
        fix_id = track.get("fixtureId")
        if fix_id not in fixture_data:
            continue
        direct = []
        for clip in track.get("clips", []):
            aid = clip.get("actionId")
            if aid is None:
                continue
            act = act_map.get(aid)
            if not act:
                continue
            direct.append({
                "type": act.get("type", 0),
                "params": {k: act.get(k, 0) for k in ("r","g","b","r2","g2","b2","speedMs","periodMs",
                           "spawnMs","minBri","spacing","paletteId","cooling","sparking",
                           "direction","tailLen","density","decay","fadeSpeed")},
                "startFrame": int(clip.get("startS", 0) * BAKE_FPS),
                "endFrame": int((clip.get("startS", 0) + clip.get("durationS", 1)) * BAKE_FPS),
                "startS": clip.get("startS", 0),
                "durationS": clip.get("durationS", 1),
            })
        direct_segments[fix_id] = direct

    # Action segmentation and LSQ generation
    result = {"fixtures": {}, "lsq_files": {}, "totalFrames": n_frames, "fps": BAKE_FPS}

    for fix_id, frames in per_fixture_frames.items():
        pixel_count = fixture_data[fix_id]["pixelCount"]
        string_info = fixture_data[fix_id].get("strings", [])

        # Per-string segmentation: analyze each string's pixels separately
        # so spatial sweeps create timed segments per string
        if len(string_info) > 1:
            segments = _segment_per_string(frames, string_info, n_frames)
        else:
            segments = _segment_actions(frames, pixel_count)

        # Merge with direct action segments
        action_segments = direct_segments.get(fix_id, [])
        if action_segments:
            segments = _merge_segments(segments, action_segments, n_frames)

        lsq = _pack_lsq(fix_id, frames, pixel_count)

        result["fixtures"][fix_id] = {
            "segments": segments,
            "pixelCount": pixel_count,
            "frameCount": len(frames),
            "stringCount": len(string_info),
        }
        result["lsq_files"][fix_id] = lsq

        if progress:
            progress.fixtures_done += 1
            progress.segments[str(fix_id)] = len(segments)

    # Generate preview data: 1 color per string per second
    preview = {}
    for fix_id, frames in per_fixture_frames.items():
        sinfo = fixture_data[fix_id].get("strings", [])
        fix_preview = []
        for sec in range(0, n_frames, BAKE_FPS):
            frame = frames[min(sec, len(frames) - 1)]
            string_colors = []
            if sinfo:
                for si in sinfo:
                    off, cnt = si["offset"], si["count"]
                    spx = frame[off:off+cnt] if off+cnt <= len(frame) else []
                    string_colors.append(_dominant_color(spx) if spx else [0,0,0])
            else:
                string_colors.append(_dominant_color(frame))
            fix_preview.append(string_colors)
        preview[fix_id] = fix_preview
    result["preview"] = preview

    if progress:
        progress.status = "complete"
        progress.current_frame = n_frames
        progress.done = True

    return result


def _segment_per_string(frames, string_info, n_frames, max_per_string=8):
    """Segment frames per-string: analyze each string's pixels separately.

    For multi-string fixtures, a spatial sweep hitting string 0 first then
    string 1 will produce different segments for each string with correct timing.

    Each segment includes ledStartOverride/ledEndOverride to target specific strings.
    """
    if not frames or not string_info:
        return []

    all_segments = []
    steps_per_string = max(1, 16 // max(len(string_info), 1))

    for si, sinfo in enumerate(string_info):
        offset = sinfo["offset"]
        count = sinfo["count"]
        if count <= 0:
            continue

        # Extract this string's pixels from each frame
        string_frames = []
        for frame in frames:
            string_pixels = frame[offset:offset + count] if offset + count <= len(frame) else []
            string_frames.append(string_pixels)

        # Segment this string independently
        segs = _segment_actions(string_frames, count, max_segments=steps_per_string)

        # Tag each segment with the string's LED range
        for seg in segs:
            seg["stringIndex"] = si
            seg["ledOffset"] = offset
            seg["ledCount"] = count
        all_segments.extend(segs)

    # Sort by start time, cap at 16
    all_segments.sort(key=lambda s: (s["startS"], s.get("stringIndex", 0)))
    return all_segments[:16]


def _merge_segments(frame_segments, action_segments, n_frames):
    """Merge frame-analyzed segments with direct action segments.
    Action segments take priority — they replace frame segments in their time range.
    Result is sorted by startS and capped at 16 total."""
    # Build a time-sorted list of all segments
    all_segs = list(action_segments)  # action clips first (priority)
    # Add frame segments that don't overlap with any action segment
    for fs in frame_segments:
        overlaps = False
        for a in action_segments:
            # Check overlap
            if fs["startS"] < a["startS"] + a["durationS"] and fs["startS"] + fs["durationS"] > a["startS"]:
                overlaps = True
                break
        if not overlaps:
            all_segs.append(fs)
    all_segs.sort(key=lambda s: s["startS"])
    return all_segs[:16]


def _dominant_color(frame_pixels):
    """Get the dominant color — average of pixels with the most brightness.

    Instead of averaging ALL pixels (which dilutes spatial effects like sweeps),
    find the peak brightness and average only pixels at >= 50% of that peak.
    This gives cleaner colors when a spatial effect only illuminates part of the strip.
    """
    if not frame_pixels:
        return [0, 0, 0]
    # Find the brightest pixel
    max_bri = 0
    best = [0, 0, 0]
    for p in frame_pixels:
        bri = p[0] + p[1] + p[2]
        if bri > max_bri:
            max_bri = bri
            best = list(p)
    if max_bri == 0:
        return [0, 0, 0]
    # Average all pixels that are at least 50% of peak brightness
    threshold = max_bri * 0.5
    total_r, total_g, total_b, count = 0, 0, 0, 0
    for p in frame_pixels:
        if p[0] + p[1] + p[2] >= threshold:
            total_r += p[0]; total_g += p[1]; total_b += p[2]
            count += 1
    if count == 0:
        return best
    return [total_r // count, total_g // count, total_b // count]


def _color_distance(a, b):
    return abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2])


def _segment_actions(frames, pixel_count, max_segments=16):
    """Analyze frame sequence and produce coarse action segments that fit the 16-step limit.

    Strategy: divide the timeline into max_segments equal windows. For each window,
    compute the dominant color at the start and end. If they differ significantly,
    emit a FADE; if both are dark, emit BLACKOUT; otherwise emit SOLID with the
    average color. This guarantees at most max_segments outputs.

    Returns list of segments:
        [{type, params, startFrame, endFrame, startS, durationS}, ...]
    """
    if not frames:
        return []

    n = len(frames)
    # Determine window size — divide frames evenly into max_segments windows
    window = max(1, n // max_segments)
    segments = []

    i = 0
    while i < n:
        j = min(i + window, n)
        start_color = _dominant_color(frames[i])
        end_color = _dominant_color(frames[j - 1])
        mid_color = _dominant_color(frames[(i + j) // 2])
        avg_color = [(start_color[c] + mid_color[c] + end_color[c]) // 3 for c in range(3)]

        start_s = round(i / BAKE_FPS, 3)
        dur_s = round((j - i) / BAKE_FPS, 3)

        # Check for blackout (all dark)
        if all(c < 8 for c in avg_color):
            segments.append({
                "type": ACT_BLACKOUT, "params": {},
                "startFrame": i, "endFrame": j,
                "startS": start_s, "durationS": dur_s,
            })
        # Check for fade (start and end colors differ significantly)
        elif _color_distance(start_color, end_color) > 60:
            segments.append({
                "type": ACT_FADE,
                "params": {
                    "r": start_color[0], "g": start_color[1], "b": start_color[2],
                    "r2": end_color[0], "g2": end_color[1], "b2": end_color[2],
                    "speedMs": int(dur_s * 1000),
                },
                "startFrame": i, "endFrame": j,
                "startS": start_s, "durationS": dur_s,
            })
        # Otherwise solid with the average color
        else:
            segments.append({
                "type": ACT_SOLID,
                "params": {"r": avg_color[0], "g": avg_color[1], "b": avg_color[2]},
                "startFrame": i, "endFrame": j,
                "startS": start_s, "durationS": dur_s,
            })

        i = j

    return segments


def _pack_lsq(fixture_id, frames, pixel_count):
    """Pack frames into LSQ binary format.

    Header (16 bytes):
        magic:      4 bytes "LSQ\x00"
        version:    uint8  (1)
        fixtureId:  uint8
        frameCount: uint32
        fps:        uint8  (40)
        pixelCount: uint16
        reserved:   3 bytes

    Frame data:
        frameCount * pixelCount * 3 bytes (RGB per pixel per frame)
    """
    header = struct.pack("<4sBBIBH3s",
        LSQ_MAGIC,
        LSQ_VERSION,
        fixture_id & 0xFF,
        len(frames),
        BAKE_FPS,
        pixel_count & 0xFFFF,
        b"\x00\x00\x00"
    )

    frame_data = bytearray()
    for frame in frames:
        for pi in range(pixel_count):
            if pi < len(frame):
                px = frame[pi]
                frame_data.append(min(255, max(0, px[0])))
                frame_data.append(min(255, max(0, px[1])))
                frame_data.append(min(255, max(0, px[2])))
            else:
                frame_data.extend(b"\x00\x00\x00")

    return bytes(header) + bytes(frame_data)


def pack_lsq_zip(lsq_files):
    """Pack multiple LSQ files into a zip archive.

    Args:
        lsq_files: dict of fixture_id → bytes

    Returns:
        bytes (zip file content)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fix_id, data in lsq_files.items():
            zf.writestr(f"fixture_{fix_id}.lsq", data)
    return buf.getvalue()


def segments_to_load_steps(segments, max_steps=16):
    """Convert baked segments to LoadStepPayload-compatible dicts.

    Returns list of step dicts compatible with the existing runner sync protocol.
    Each step has: actionType, r, g, b, params, durationS, delayMs
    """
    steps = []
    for seg in segments[:max_steps]:
        step = {
            "actionType": seg["type"],
            "durationS": max(1, int(math.ceil(seg["durationS"]))),
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
        steps.append(step)
    return steps
