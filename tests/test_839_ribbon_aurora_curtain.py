#!/usr/bin/env python3
"""test_839_ribbon_aurora_curtain.py — Regression for #839.

Asserts the new `aurora-curtain` template + ribbon primitive:

- Theme exists in show_generator.THEMES with a `ribbon` block.
- generate_show(aurora-curtain, …) returns exactly one patrol object
  with patrol.pattern == "ribbon".
- Generated mover_actions contains exactly one Track action whose
  trackObjectIds is empty pre-install (filled in by
  _install_preset_show with the ribbon's runtime id).
- Spatial-effect clip count == len(theme.ribbon.phaseOffsets).
- Fade-in / fade-out annotations land on first / last clips.
- The patrol evaluator's "ribbon" pattern is loop-stable: position
  at t=0 == position at t=cycle_s (ping-pong).

Run: python -X utf8 tests/test_839_ribbon_aurora_curtain.py
"""

import math
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


def _build_stage():
    """5-mover + 1-LED stage (matches #836 / #837 fixtures)."""
    fixtures = [
        {"id": 21, "fixtureType": "led", "name": "ESP Dual"},
        {"id": 14, "fixtureType": "dmx", "name": "350W BeamLight",
         "dmxProfileId": "p350", "dmxStartAddr": 1, "dmxUniverse": 1},
        {"id": 17, "fixtureType": "dmx", "name": "150W MH StgR",
         "dmxProfileId": "p150r", "dmxStartAddr": 17, "dmxUniverse": 1},
        {"id": 18, "fixtureType": "dmx", "name": "150W MH StgL",
         "dmxProfileId": "p150l", "dmxStartAddr": 33, "dmxUniverse": 1},
        {"id": 19, "fixtureType": "dmx", "name": "Sly Mini",
         "dmxProfileId": "psly",  "dmxStartAddr": 49, "dmxUniverse": 1},
        {"id": 24, "fixtureType": "dmx", "name": "150W Med",
         "dmxProfileId": "p150r", "dmxStartAddr": 65, "dmxUniverse": 1},
    ]
    layout = {"children": [
        {"id": f["id"], "x": 1500 * (i + 1), "y": 2000, "z": 3000}
        for i, f in enumerate(fixtures)
    ]}
    return fixtures, layout, {"w": 12, "h": 6, "d": 5}


class _StubProfileLib:
    def channel_info(self, pid):
        return {"channel_map": {"pan": 0, "tilt": 1, "dimmer": 5,
                                 "red": 6, "green": 7, "blue": 8}}


# ─────────────────────────────────────────────────────────────────────────────

def test_aurora_curtain_theme_exists_with_ribbon():
    theme = show_generator.THEMES.get("aurora-curtain")
    _assert(theme is not None, "aurora-curtain theme defined")
    if theme is None:
        return
    _assert("ribbon" in theme, "aurora-curtain has a ribbon block")
    rb = theme.get("ribbon", {})
    _assert("axis" in rb and "speedS" in rb and "phaseOffsets" in rb,
            "ribbon block carries axis / speedS / phaseOffsets")
    _assert(theme.get("fadeInS", 0) > 0 and theme.get("fadeOutS", 0) > 0,
            "aurora-curtain declares fadeInS / fadeOutS")
    _assert("sparkle_layer" in theme, "aurora-curtain has a sparkle_layer")


def test_aurora_curtain_generates_one_ribbon_patrol_object():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "aurora-curtain", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    _assert(show is not None, "aurora-curtain generates a show")
    if show is None:
        return
    patrol = show.get("patrol_objects", [])
    _assert(len(patrol) == 1,
            f"exactly one patrol_object emitted (got {len(patrol)})")
    if patrol:
        _assert(patrol[0].get("patrol", {}).get("pattern") == "ribbon",
                "patrol object carries pattern='ribbon'")


def test_aurora_curtain_emits_track_action_for_ribbon():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "aurora-curtain", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    track_actions = []
    for ma in show.get("mover_actions") or []:
        a = ma.get("action") if isinstance(ma, dict) and "action" in ma else ma
        if a.get("type") == 18:
            track_actions.append(a)
    _assert(len(track_actions) == 1,
            f"exactly one Track action emitted (got {len(track_actions)})")
    if track_actions:
        _assert("trackObjectIds" in track_actions[0],
                "Track action has trackObjectIds field "
                "(filled by _install_preset_show at install time)")


def test_aurora_curtain_spatial_clip_count_matches_phase_offsets():
    theme = show_generator.THEMES["aurora-curtain"]
    n_offsets = len(theme["ribbon"]["phaseOffsets"])
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "aurora-curtain", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    effects_track = next((t for t in show.get("tracks", [])
                           if t.get("_layer") == "effects"), None)
    _assert(effects_track is not None, "effects layer track exists")
    if effects_track is None:
        return
    clips = effects_track.get("clips", [])
    _assert(len(clips) == n_offsets,
            f"effects clip count == phaseOffsets count "
            f"(got {len(clips)}, expected {n_offsets})")


def test_fade_brackets_annotated_on_edge_clips():
    fixtures, layout, stage = _build_stage()
    show = show_generator.generate_show(
        "aurora-curtain", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    dur = show.get("durationS", 0)
    _assert(dur > 0, "durationS > 0")
    fade_in_seen = 0
    fade_out_seen = 0
    for tr in show.get("tracks", []):
        for cl in tr.get("clips", []):
            if cl.get("_fadeInS", 0) > 0 and cl.get("startS", 1) < 0.05:
                fade_in_seen += 1
            if cl.get("_fadeOutS", 0) > 0:
                end_t = cl.get("startS", 0) + cl.get("durationS", 0)
                if end_t > dur - 0.05:
                    fade_out_seen += 1
    _assert(fade_in_seen > 0,
            f"at least one clip has _fadeInS at timeline start (got {fade_in_seen})")
    _assert(fade_out_seen > 0,
            f"at least one clip has _fadeOutS at timeline end (got {fade_out_seen})")


# ─────────────────────────────────────────────────────────────────────────────
# Patrol evaluator ribbon-pattern test.

def test_ribbon_pattern_loop_stable_pingpong():
    """The ribbon patrol pattern must be loop-stable: position at t=0
    equals position at t=cycle_s. This is the foundation of the
    "no-blink loop" acceptance criterion."""
    import parent_server  # noqa: E402
    # Create a minimal in-memory ribbon object and run the evaluator.
    obj = {
        "id": 9999,
        "name": "test-ribbon",
        "objectType": "ribbon-target",
        "mobility": "moving",
        "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0],
                       "scale": [400, 400, 400]},
        "patrol": {
            "enabled": True,
            "pattern": "ribbon",
            "axis": "left-right",
            "ribbonAxis": "left-right",
            "elevation": 0.65,
            "loopMode": "ping-pong",
            "cycleS": 12,
            "speedPreset": "custom",  # force cycleS (vs default "medium" 10s)
            "easing": "sine",
        },
    }
    # Inject into _objects for one tick, then remove.
    saved_objs = list(parent_server._objects)
    saved_settings = dict(parent_server._settings)
    try:
        parent_server._objects[:] = [obj]
        # Force runner-running so the on-demand check doesn't gate us;
        # ribbon objects use default patrolMode (not on-demand).
        parent_server._evaluate_object_patrols(0.0)
        pos_t0 = list(parent_server._objects[0]["transform"]["pos"])
        parent_server._evaluate_object_patrols(12.0)  # exactly one cycle
        pos_t12 = list(parent_server._objects[0]["transform"]["pos"])
        for i, (a, b) in enumerate(zip(pos_t0, pos_t12)):
            _assert(abs(a - b) < 1.0,
                    f"ribbon ping-pong loop-stable on axis {i}: "
                    f"pos(t=0)={a:.1f} vs pos(t=cycle)={b:.1f}")
        # Mid-cycle should be at the far endpoint (max excursion).
        parent_server._evaluate_object_patrols(6.0)
        pos_t6 = list(parent_server._objects[0]["transform"]["pos"])
        _assert(abs(pos_t6[0] - pos_t0[0]) > 1000,
                f"ribbon mid-cycle position differs from start "
                f"(pos.x: t=0 {pos_t0[0]:.0f}, t=6 {pos_t6[0]:.0f})")
    finally:
        parent_server._objects[:] = saved_objs


ALL = [
    test_aurora_curtain_theme_exists_with_ribbon,
    test_aurora_curtain_generates_one_ribbon_patrol_object,
    test_aurora_curtain_emits_track_action_for_ribbon,
    test_aurora_curtain_spatial_clip_count_matches_phase_offsets,
    test_fade_brackets_annotated_on_edge_clips,
    test_ribbon_pattern_loop_stable_pingpong,
]


if __name__ == "__main__":
    print("=== #839 ribbon primitive + aurora-curtain ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
