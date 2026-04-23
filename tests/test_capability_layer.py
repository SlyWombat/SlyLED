"""test_capability_layer.py — Unit tests for the capability-layer primitive.

Covers evaluate_primitive() and derive_caps() added in phase 4 of
spatial_engine.py per docs/mover-alignment-review.md §8.1b.

Run:
    python -X utf8 tests/test_capability_layer.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from spatial_engine import (  # noqa: E402
    evaluate_primitive,
    derive_caps,
    PrimitiveOutputs,
    CAP_COLOR_RGB,
    CAP_COLOR_WHITE,
    CAP_COLOR_WHEEL,
    CAP_INTENSITY,
    CAP_DIRECTION,
    CAP_STROBE,
    CAP_BEAM_ZOOM,
    CAP_BEAM_FOCUS,
)


passed = 0
failed = 0


def assert_eq(actual, expected, msg=""):
    global passed, failed
    if actual == expected:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}: expected {expected!r}, got {actual!r}")


def assert_close(actual, expected, tol, msg=""):
    global passed, failed
    if abs(actual - expected) <= tol:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}: expected {expected} ±{tol}, got {actual}")


def assert_true(cond, msg=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


# ── evaluate_primitive — shape coverage ─────────────────────────────────────

def test_sphere_field_hits_fixture_inside_radius():
    effect = {
        "shape": "sphere",
        "r": 255, "g": 0, "b": 0,
        "size": {"radius": 2000},
        "motion": {"startPos": [0, 0, 0], "endPos": [0, 0, 0],
                   "durationS": 1, "easing": "linear"},
    }
    out = evaluate_primitive([0, 0, 0], effect, 0.5)
    assert_true(isinstance(out, PrimitiveOutputs),
                "sphere: result type is PrimitiveOutputs")
    assert_true(out.color[0] > 0,
                "sphere: fixture inside radius is lit red (got %s)" % (out.color,))
    assert_eq(out.intensity, 1.0, "sphere: intensity 1.0 when lit")
    assert_eq(out.aim, (0, 0, 0), "sphere: aim is effect centre")


def test_sphere_field_misses_fixture_outside_radius():
    effect = {
        "shape": "sphere",
        "r": 255, "g": 0, "b": 0,
        "size": {"radius": 500},
        "motion": {"startPos": [0, 0, 0], "endPos": [0, 0, 0],
                   "durationS": 1, "easing": "linear"},
    }
    out = evaluate_primitive([5000, 0, 0], effect, 0.5)
    assert_eq(out.color, (0, 0, 0), "sphere: fixture outside radius is dark")
    assert_eq(out.intensity, 0.0, "sphere: intensity 0 when dark")


def test_plane_sweep_aim_follows_effect_centre():
    # Plane sweep: moves from (-3000, 1500, 1500) to (+3000, 1500, 1500)
    effect = {
        "shape": "plane",
        "r": 0, "g": 0, "b": 255,
        "size": {"normal": [1, 0, 0], "thickness": 400},
        "motion": {"startPos": [-3000, 1500, 1500],
                   "endPos":   [ 3000, 1500, 1500],
                   "durationS": 2.0, "easing": "linear"},
    }
    # At t=0, plane is at -3000 → midway (t=1s) plane centre is at 0
    out = evaluate_primitive([0, 1500, 1500], effect, 1.0)
    assert_eq(out.aim, (0, 1500, 1500),
              "plane: aim at effect centre at t=1.0")
    assert_true(out.color[2] > 0,
                "plane: fixture on the plane at midway is lit blue (got %s)" % (out.color,))


def test_plane_sweep_fixture_not_on_plane_is_dark():
    effect = {
        "shape": "plane",
        "r": 0, "g": 0, "b": 255,
        "size": {"normal": [1, 0, 0], "thickness": 200},
        "motion": {"startPos": [-3000, 1500, 1500],
                   "endPos":   [ 3000, 1500, 1500],
                   "durationS": 2.0, "easing": "linear"},
    }
    # At t=0, plane is at x=-3000; fixture at x=+2000 is not covered
    out = evaluate_primitive([2000, 1500, 1500], effect, 0.0)
    assert_eq(out.color, (0, 0, 0),
              "plane: fixture far from plane at t=0 is dark")


def test_box_field_contains_fixture():
    effect = {
        "shape": "box",
        "r": 0, "g": 255, "b": 0,
        "size": {"width": 2000, "height": 2000, "depth": 2000},
        "motion": {"startPos": [0, 0, 0], "endPos": [0, 0, 0],
                   "durationS": 1, "easing": "linear"},
    }
    out = evaluate_primitive([500, 500, 500], effect, 0.5)
    assert_true(out.color[1] > 0,
                "box: fixture inside AABB is lit green (got %s)" % (out.color,))
    assert_eq(out.aim, (0, 0, 0), "box: aim is centre at static position")


def test_none_effect_returns_zero():
    out = evaluate_primitive([0, 0, 0], None, 0.0)
    assert_eq(out.color, (0, 0, 0), "None effect: colour zero")
    assert_eq(out.intensity, 0.0, "None effect: intensity zero")
    assert_eq(out.aim, None, "None effect: no aim")


# ── derive_caps — DMX profile scanning ──────────────────────────────────────

def test_caps_rgb_dimmer_par():
    profile = {
        "channels": [
            {"type": "dimmer"},
            {"type": "red"},
            {"type": "green"},
            {"type": "blue"},
        ],
    }
    caps = derive_caps(profile)
    assert_true(CAP_COLOR_RGB in caps, "par: RGB cap detected")
    assert_true(CAP_INTENSITY in caps, "par: dimmer cap detected")
    assert_true(CAP_DIRECTION not in caps, "par: no direction")


def test_caps_moving_head():
    profile = {
        "channels": [
            {"type": "pan"},
            {"type": "tilt"},
            {"type": "dimmer"},
            {"type": "red"},
            {"type": "green"},
            {"type": "blue"},
            {"type": "white"},
            {"type": "strobe"},
            {"type": "zoom"},
        ],
        "panRange": 540,
        "tiltRange": 270,
    }
    caps = derive_caps(profile)
    for expected in (CAP_COLOR_RGB, CAP_COLOR_WHITE, CAP_INTENSITY,
                     CAP_DIRECTION, CAP_STROBE, CAP_BEAM_ZOOM):
        assert_true(expected in caps, f"mover: expected {expected} in {caps}")


def test_caps_direction_from_range_only():
    # Some profiles might declare panRange/tiltRange without named channels
    profile = {
        "channels": [
            {"type": "red"}, {"type": "green"}, {"type": "blue"},
        ],
        "panRange": 540, "tiltRange": 270,
    }
    caps = derive_caps(profile)
    assert_true(CAP_DIRECTION in caps,
                "range-only: direction cap derived from panRange/tiltRange")


def test_caps_wheel_slot_color():
    profile = {
        "channels": [
            {"type": "colorwheel",
             "capabilities": [
                 {"type": "WheelSlot", "color": "#ff0000"},
                 {"type": "WheelSlot", "color": "#00ff00"},
             ]},
        ],
    }
    caps = derive_caps(profile)
    assert_true(CAP_COLOR_WHEEL in caps,
                "wheel: colour-wheel cap detected from WheelSlot capabilities")


def test_caps_empty_profile():
    assert_eq(derive_caps(None), [], "None profile → empty caps")
    assert_eq(derive_caps({}), [], "empty profile → empty caps")


def test_caps_sorted_deduplicated():
    profile = {
        "channels": [
            {"type": "red"}, {"type": "green"}, {"type": "blue"},
            {"type": "red"},
            {"type": "dimmer"},
        ],
    }
    caps = derive_caps(profile)
    assert_eq(caps, sorted(set(caps)), "caps: sorted and deduplicated")


# ── Consumer example: selective primitive reading ──────────────────────────

def test_consumer_reads_only_declared_caps():
    """A fixture with caps=['color.rgb', 'intensity.dimmer'] reads colour +
    intensity and ignores aim — demonstrating the fixture-type-agnostic
    dispatch pattern."""
    effect = {
        "shape": "sphere",
        "r": 100, "g": 200, "b": 50,
        "size": {"radius": 5000},
        "motion": {"startPos": [0, 0, 0], "endPos": [0, 0, 0],
                   "durationS": 1, "easing": "linear"},
    }
    out = evaluate_primitive([0, 0, 0], effect, 0.5)

    par_caps = [CAP_COLOR_RGB, CAP_INTENSITY]
    # A PAR consumer reads colour + intensity; no aim read.
    par_color = out.color if CAP_COLOR_RGB in par_caps else None
    par_intensity = out.intensity if CAP_INTENSITY in par_caps else None
    assert_eq(par_color, (100, 200, 50), "PAR consumer: colour read")
    assert_eq(par_intensity, 1.0, "PAR consumer: intensity read")

    mover_caps = [CAP_COLOR_RGB, CAP_INTENSITY, CAP_DIRECTION]
    mover_aim = out.aim if CAP_DIRECTION in mover_caps else None
    assert_true(mover_aim is not None, "mover consumer: aim read")


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_sphere_field_hits_fixture_inside_radius,
        test_sphere_field_misses_fixture_outside_radius,
        test_plane_sweep_aim_follows_effect_centre,
        test_plane_sweep_fixture_not_on_plane_is_dark,
        test_box_field_contains_fixture,
        test_none_effect_returns_zero,
        test_caps_rgb_dimmer_par,
        test_caps_moving_head,
        test_caps_direction_from_range_only,
        test_caps_wheel_slot_color,
        test_caps_empty_profile,
        test_caps_sorted_deduplicated,
        test_consumer_reads_only_declared_caps,
    ]
    for t in tests:
        t()
    print(f"\n{passed} passed, {failed} failed (out of {passed + failed})")
    sys.exit(0 if failed == 0 else 1)
