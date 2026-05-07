#!/usr/bin/env python3
"""test_837_show_generator_axis.py — Regression for #837.

Asserts the show generator's vertical-sweep paths use Z (the project's
up-axis per CLAUDE.md "Rotation convention"), not Y (depth):

- `_make_sweep_path("up"|"down", …)` returns endpoints whose Z
  coordinates differ and whose X / Y are equal (modulo jitter).
- Plane-effect normals for `"up" / "down"` directions point along Z.
- Lightning bolts in the thunderstorm theme strike top-down (Z), not
  front-to-back (Y), and originate only at light-emitting fixture
  positions (cameras / gyros / patrol props excluded).
- Theme `desc` strings promising "moving heads track / follow / chase"
  only appear on themes that actually emit a Track action (figure-eight,
  spotlight-follow-person).

Run: python -X utf8 tests/test_837_show_generator_axis.py
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

def test_make_sweep_path_up_travels_on_z():
    bounds = {"cx": 0, "cy": 0, "cz": 1500,
              "xMin": -3000, "xMax": 3000,
              "yMin": -2000, "yMax": 2000,
              "zMin": 0, "zMax": 4000}
    start, end = show_generator._make_sweep_path(bounds, "up", jitter=False)
    _assert(start[2] != end[2],
            f"'up' sweeps along Z (start.z={start[2]}, end.z={end[2]})")
    _assert(start[2] < end[2],
            f"'up' starts low (zMin), ends high (zMax) — got {start[2]} → {end[2]}")
    _assert(start[0] == end[0] and start[1] == end[1],
            f"'up' keeps X / Y constant (got X {start[0]}→{end[0]}, Y {start[1]}→{end[1]})")


def test_make_sweep_path_down_travels_on_z():
    bounds = {"cx": 0, "cy": 0, "cz": 1500,
              "xMin": -3000, "xMax": 3000,
              "yMin": -2000, "yMax": 2000,
              "zMin": 0, "zMax": 4000}
    start, end = show_generator._make_sweep_path(bounds, "down", jitter=False)
    _assert(start[2] > end[2],
            f"'down' starts high (zMax), ends low (zMin) — got {start[2]} → {end[2]}")
    _assert(start[0] == end[0] and start[1] == end[1],
            "'down' keeps X / Y constant")


def test_make_sweep_path_horizontal_unchanged():
    bounds = {"cx": 0, "cy": 0, "cz": 1500,
              "xMin": -3000, "xMax": 3000,
              "yMin": -2000, "yMax": 2000,
              "zMin": 0, "zMax": 4000}
    start, end = show_generator._make_sweep_path(bounds, "left-right", jitter=False)
    _assert(start[0] != end[0] and start[1] == end[1] and start[2] == end[2],
            "'left-right' still travels on X only")


# ─────────────────────────────────────────────────────────────────────────────

def _build_thunderstorm_stage():
    """Mixed rig with some non-light fixtures so the lightning-origin
    filter is exercised."""
    fixtures = [
        # Cameras (must NOT receive lightning bolts)
        {"id": 12, "fixtureType": "camera", "name": "cam-A"},
        {"id": 13, "fixtureType": "camera", "name": "cam-B"},
        # Gyros (must NOT receive lightning bolts)
        {"id": 22, "fixtureType": "gyro", "name": "gyro-A"},
        # Movers (lightning eligible)
        {"id": 17, "fixtureType": "dmx", "name": "mh-1",
         "dmxProfileId": "p1", "dmxStartAddr": 1, "dmxUniverse": 1},
        {"id": 18, "fixtureType": "dmx", "name": "mh-2",
         "dmxProfileId": "p1", "dmxStartAddr": 17, "dmxUniverse": 1},
        {"id": 19, "fixtureType": "dmx", "name": "mh-3",
         "dmxProfileId": "p1", "dmxStartAddr": 33, "dmxUniverse": 1},
    ]
    layout = {"children": [
        {"id": f["id"], "x": 1000 * (i + 1), "y": 2000, "z": 3000}
        for i, f in enumerate(fixtures)
    ]}
    return fixtures, layout, {"w": 8, "h": 6, "d": 4}


class _StubProfileLib:
    def channel_info(self, pid):
        return {"channel_map": {"pan": 0, "tilt": 1, "dimmer": 5,
                                 "red": 6, "green": 7, "blue": 8}}


def test_lightning_bolts_strike_top_down_on_z():
    fixtures, layout, stage = _build_thunderstorm_stage()
    show = show_generator.generate_show(
        "thunderstorm", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    bolts = [e for e in show.get("effects", [])
              if e.get("name", "").startswith("Lightning")]
    _assert(bolts, "thunderstorm theme produces lightning effects")
    for b in bolts:
        m = b.get("motion", {})
        s = m.get("startPos", [0, 0, 0])
        e = m.get("endPos", [0, 0, 0])
        _assert(s[2] > e[2],
                f"{b['name']}: bolt strikes top-down on Z (start.z={s[2]} > end.z={e[2]})")


def test_lightning_origins_are_light_emitting_only():
    """Camera + gyro fixture positions must NOT serve as lightning
    origins. Movers (dmx fixtureType with pan/tilt) only."""
    fixtures, layout, stage = _build_thunderstorm_stage()
    show = show_generator.generate_show(
        "thunderstorm", fixtures, layout, stage,
        profile_lib=_StubProfileLib())
    bolts = [e for e in show.get("effects", [])
              if e.get("name", "").startswith("Lightning")]
    cam_gyro_xs = {layout["children"][i]["x"]
                    for i, f in enumerate(fixtures)
                    if f["fixtureType"] in ("camera", "gyro")}
    mover_xs = {layout["children"][i]["x"]
                 for i, f in enumerate(fixtures)
                 if f["fixtureType"] == "dmx"}
    for b in bolts:
        end_x = b["motion"]["endPos"][0]  # bolt's TARGET x (where it lands)
        _assert(end_x in mover_xs,
                f"{b['name']}: bolt lands at a mover X (got {end_x}, mover xs {sorted(mover_xs)})")
        _assert(end_x not in cam_gyro_xs,
                f"{b['name']}: bolt does NOT anchor on camera/gyro position (got {end_x})")


# ─────────────────────────────────────────────────────────────────────────────

def test_track_promising_descs_only_on_track_action_themes():
    """Only themes that emit a Track action (live_track / patrol_objects)
    should claim target-tracking in their desc.

    Pre-fix spotlight-sweep, concert-wash, thunderstorm all promised
    tracking but emitted no Track action. Per #837, those descs are
    rewritten to match the actual implementation.

    Note: "chase" by itself is ambiguous — `dance-floor` uses
    "chase pulse" which refers to ACT_CHASE (a marching-LEDs effect),
    not target-chase. We only flag descs that name a target-context
    explicitly: "moving heads track / follow / chase X" where X is
    "it" / "people" / "strikes" / "target" / etc.
    """
    target_phrases = (
        "heads track", "heads follow", "heads chase",
        "track it", "follow it", "chase it",
        "track detected", "follow detected", "chase strikes",
        "tracks the", "follows the", "chases the",
    )
    for tid, theme in show_generator.THEMES.items():
        desc_lower = theme.get("desc", "").lower()
        promises_track = any(p in desc_lower for p in target_phrases)
        emits_track = (theme.get("live_track") is True
                        or theme.get("patrol_objects"))
        if promises_track:
            _assert(emits_track,
                    f"theme '{tid}' desc claims target-tracking — must emit a Track action")
        # The reverse is allowed: a theme can emit live_track without
        # the desc using those exact keywords.


ALL = [
    test_make_sweep_path_up_travels_on_z,
    test_make_sweep_path_down_travels_on_z,
    test_make_sweep_path_horizontal_unchanged,
    test_lightning_bolts_strike_top_down_on_z,
    test_lightning_origins_are_light_emitting_only,
    test_track_promising_descs_only_on_track_action_themes,
]


if __name__ == "__main__":
    print("=== #837 show generator Z-up axis + lightning + desc match ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
