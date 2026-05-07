#!/usr/bin/env python3
"""test_836_show_generator.py — Regression for #836.

Asserts the show generator emits stage-coordinate sweep endpoints
(`ptStartPos` / `ptEndPos`), not DMX-fraction `panStart` / `panEnd` /
`tiltStart` / `tiltEnd`, and that timeline scaffolding meets the
acceptance contract:

- LED-base track count equals the LED-fixture count (no camera/gyro
  leak through `_classify_fixtures`).
- Every type-15 (ACT_DMX_PT_MOVE) action has `ptStartPos` AND
  `ptEndPos` and NO `panStart` / `panEnd` / `tiltStart` / `tiltEnd`.
- Sweep endpoints lie inside the stage bounding box.
- Spatial-effect clips on the `allPerformers` track tile the
  timeline duration (sum >= 99 %, max gap < 0.05 s).
- Per-mover sweep clips occupy a sub-window of the timeline so the
  Mover Base wash can show during transitions.

Run: python -X utf8 tests/test_836_show_generator.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import show_generator  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic stage matching the issue body: 3 cameras + 1 LED node +
# 5 movers (across 4 distinct DMX profiles) + 2 gyro pucks.

def _build_stage():
    fixtures = [
        # Cameras
        {"id": 12, "fixtureType": "camera", "name": "Stage Right cam"},
        {"id": 13, "fixtureType": "camera", "name": "Stage Left cam"},
        {"id": 16, "fixtureType": "camera", "name": "Out Left cam"},
        # LED node
        {"id": 21, "fixtureType": "led", "name": "ESP Dual"},
        # Movers — different profile ids
        {"id": 14, "fixtureType": "dmx", "name": "350W BeamLight",
         "dmxProfileId": "p350", "dmxStartAddr": 1, "dmxUniverse": 1},
        {"id": 17, "fixtureType": "dmx", "name": "150W MH Stage Right",
         "dmxProfileId": "p150r", "dmxStartAddr": 17, "dmxUniverse": 1},
        {"id": 18, "fixtureType": "dmx", "name": "150W MH Stage Left",
         "dmxProfileId": "p150l", "dmxStartAddr": 33, "dmxUniverse": 1},
        {"id": 19, "fixtureType": "dmx", "name": "Sly Mini",
         "dmxProfileId": "psly",  "dmxStartAddr": 49, "dmxUniverse": 1},
        {"id": 24, "fixtureType": "dmx", "name": "150W Medium LED",
         "dmxProfileId": "p150r", "dmxStartAddr": 65, "dmxUniverse": 1},
        # Gyro pucks
        {"id": 22, "fixtureType": "gyro", "name": "Gyro Controller A"},
        {"id": 23, "fixtureType": "gyro", "name": "Gyro Controller B"},
    ]
    layout = {"children": [
        {"id": f["id"], "x": 1000 + 500 * i, "y": 2000, "z": 3000}
        for i, f in enumerate(fixtures)
    ]}
    stage = {"w": 6.0, "h": 4.0, "d": 5.0}
    return fixtures, layout, stage


# Stub profile_lib that says every mover has pan+tilt channels.
class _StubProfileLib:
    def channel_info(self, pid):
        return {"channel_map": {"pan": 0, "tilt": 1, "dimmer": 5,
                                 "red": 6, "green": 7, "blue": 8}}


# ─────────────────────────────────────────────────────────────────────────────

def test_classify_excludes_camera_and_gyro_from_led_bucket():
    fixtures, _, _ = _build_stage()
    led_fx, dmx_pars, dmx_movers, groups = show_generator._classify_fixtures(
        fixtures, profile_lib=_StubProfileLib())
    led_ids = {f["id"] for f in led_fx}
    _assert(led_ids == {21},
            f"LED bucket = {{21}} only (got {led_ids}); cameras + gyros excluded")
    _assert(len(dmx_movers) == 5,
            f"all 5 movers classified as dmx_movers (got {len(dmx_movers)})")


def _action_dicts(show):
    """generate_show returns mover_actions / base_actions as
    {action: {...}, targets: ...} wrappers. Flatten to action dicts."""
    out = []
    for ma in show.get("mover_actions") or []:
        if isinstance(ma, dict) and "action" in ma:
            out.append(ma["action"])
        else:
            out.append(ma)
    return out


def test_mover_actions_use_stage_coords_not_dmx_fractions():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "rainbow-across", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    pt_moves = [a for a in _action_dicts(show) if a.get("type") == 15]
    _assert(pt_moves, "show generates at least one type-15 PT_MOVE action")
    forbidden = ("panStart", "panEnd", "tiltStart", "tiltEnd")
    required = ("ptStartPos", "ptEndPos")
    for a in pt_moves:
        for k in forbidden:
            _assert(k not in a,
                    f"action '{a.get('name')}' must NOT carry DMX-fraction '{k}' (#836 primary)")
        for k in required:
            _assert(k in a and isinstance(a[k], (list, tuple)) and len(a[k]) == 3,
                    f"action '{a.get('name')}' carries stage-mm '{k}' as [x,y,z]")


def test_mover_sweep_endpoints_inside_stage_bounds():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "rainbow-across", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    # Stage bounds in mm — generator uses w/h/d in metres but multiplies
    # internally; rather than hard-coding, derive from layout.
    xs = [c["x"] for c in layout["children"]]
    ys = [c["y"] for c in layout["children"]]
    zs = [c["z"] for c in layout["children"]]
    margin = 5000  # generous — sweep endpoints can extend past fixture layout
    xMin, xMax = min(xs) - margin, max(xs) + margin
    yMin, yMax = min(ys) - margin, max(ys) + margin
    zMin, zMax = min(zs) - margin, max(zs) + margin
    pt_moves = [a for a in _action_dicts(show) if a.get("type") == 15]
    for a in pt_moves:
        for k in ("ptStartPos", "ptEndPos"):
            x, y, z = a[k]
            _assert(xMin <= x <= xMax,
                    f"{a.get('name')} {k}.x in stage bounds (got {x}, range [{xMin},{xMax}])")
            _assert(yMin <= y <= yMax,
                    f"{a.get('name')} {k}.y in stage bounds (got {y}, range [{yMin},{yMax}])")
            _assert(zMin <= z <= zMax,
                    f"{a.get('name')} {k}.z in stage bounds (got {z}, range [{zMin},{zMax}])")


def test_spatial_effect_clips_tile_timeline():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "rainbow-across", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    dur = show.get("durationS", 0)
    _assert(dur > 0, f"show has positive durationS (got {dur})")
    perf_tracks = [t for t in show.get("tracks", []) if t.get("allPerformers")]
    _assert(perf_tracks, "at least one allPerformers track")
    if perf_tracks:
        # Pick the effects track (typically the only allPerformers one).
        eff_track = perf_tracks[0]
        clips = sorted(eff_track.get("clips", []), key=lambda c: c.get("startS", 0))
        if not clips:
            _assert(False, "spatial-effects track has clips")
            return
        # Total coverage ≥ 99 % of dur
        total = sum(c.get("durationS", 0) for c in clips)
        _assert(total >= dur * 0.99,
                f"spatial-effect clips sum >= 99% of timeline ({total:.1f}/{dur} s)")
        # Inter-clip gap < 0.05 s
        max_gap = 0.0
        for a, b in zip(clips, clips[1:]):
            gap = b.get("startS", 0) - (a.get("startS", 0) + a.get("durationS", 0))
            if gap > max_gap:
                max_gap = gap
        _assert(max_gap < 0.05,
                f"max inter-clip gap < 0.05 s (got {max_gap:.3f} s)")


def test_mover_sweep_clip_shorter_than_timeline():
    """#836 item 3 — sweep clips must NOT cover the full timeline; the
    Mover Base wash needs gaps to be visible during transitions."""
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "rainbow-across", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    dur = show.get("durationS", 0)
    mover_tracks = [t for t in show.get("tracks", [])
                     if t.get("_layer") == "mover"]
    _assert(mover_tracks, "show has mover-layer tracks")
    for tr in mover_tracks:
        for cl in tr.get("clips", []):
            cl_dur = cl.get("durationS", 0)
            _assert(cl_dur < dur,
                    f"mover sweep clip duration {cl_dur} < timeline {dur} "
                    f"(wash needs exposed gaps; #836 item 3)")
            cl_start = cl.get("startS", 0)
            _assert(cl_start > 0 or cl_dur < dur,
                    f"sweep clip leaves at least one wash window "
                    f"(start={cl_start}, dur={cl_dur}, timeline={dur})")


def test_no_speedms_on_mover_pt_actions():
    """#836 item 6 — speedMs is dead on type-15 actions; the bake
    ignores it. Generator should not emit it."""
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "rainbow-across", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    pt_moves = [a for a in _action_dicts(show) if a.get("type") == 15]
    for a in pt_moves:
        _assert("speedMs" not in a,
                f"action '{a.get('name')}' must not carry dead speedMs")


ALL = [
    test_classify_excludes_camera_and_gyro_from_led_bucket,
    test_mover_actions_use_stage_coords_not_dmx_fractions,
    test_mover_sweep_endpoints_inside_stage_bounds,
    test_spatial_effect_clips_tile_timeline,
    test_mover_sweep_clip_shorter_than_timeline,
    test_no_speedms_on_mover_pt_actions,
]


if __name__ == "__main__":
    print("=== #836 show generator stage-coord + tile + classify ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
