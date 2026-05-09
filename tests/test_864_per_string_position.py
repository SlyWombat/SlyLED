"""#864 — per-string positions on multi-string LED fixtures.

Multi-string LED fixtures (e.g. one ESP32 driving four physically
separated strips) historically inherited a single (x, y, z) from the
fixture's layout entry, so spatial bake primitives treated all pixels
as co-located. The change lets each string carry its own (x, y, z)
override; fields like the travelling sphere primitive then naturally
hit string A before string B in the right physical order.

Coverage:
* `resolve_fixture` returns pixels at the per-string base when set.
* Strings without overrides still resolve at the fixture's childPos.
* A travelling sphere field hits per-string positions at distinct
  times correlated with their stage-X locations.
* `parent_server` validates partial overrides as 400.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import spatial_engine
import parent_server


def test_864_resolve_fixture_per_string_override():
    fixture = {
        "type": "linear",
        "childPos": [0, 0, 0],          # legacy fixture base
        "rotation": [0, 0, 0],
        "strings": [
            {"leds": 10, "mm": 1000, "sdir": 0, "x": 1000, "y": 2000, "z": 1500},
            {"leds": 10, "mm": 1000, "sdir": 0, "x": 3000, "y": 2000, "z": 1500},
        ],
    }
    out = spatial_engine.resolve_fixture(fixture)
    pixels = out["pixelPositions"]
    assert len(pixels) == 20

    # First string starts near (1000, 2000, 1500) and runs +X to ~2000.
    assert abs(pixels[0][0] - 1000) < 1, pixels[0]
    assert abs(pixels[0][1] - 2000) < 1, pixels[0]
    assert abs(pixels[0][2] - 1500) < 1, pixels[0]
    # Second string starts near (3000, 2000, 1500) — at distinct stage-X.
    assert abs(pixels[10][0] - 3000) < 1, pixels[10]
    # Distinct enough that no pixel from string A reaches string B's start.
    assert all(p[0] < 2500 for p in pixels[:10]), "string A must stay below 2500mm in X"
    assert all(p[0] >= 2999 for p in pixels[10:]), "string B must stay at/above 3000mm in X"


def test_864_resolve_fixture_inherits_when_unset():
    """Strings without per-string positions stay at the fixture's childPos
    so legacy projects (no x/y/z on string entries) bake identically."""
    fixture = {
        "type": "linear",
        "childPos": [500, 500, 500],
        "rotation": [0, 0, 0],
        "strings": [
            {"leds": 4, "mm": 400, "sdir": 0},
            {"leds": 4, "mm": 400, "sdir": 1},
        ],
    }
    out = spatial_engine.resolve_fixture(fixture)
    pixels = out["pixelPositions"]
    assert len(pixels) == 8
    # Both strings begin at the fixture base.
    assert abs(pixels[0][0] - 500) < 1
    assert abs(pixels[0][1] - 500) < 1
    assert abs(pixels[4][0] - 500) < 1
    assert abs(pixels[4][1] - 500) < 1


def test_864_sphere_sweep_distinct_timing_per_string():
    """A sphere travelling stage +X over 5 s with radius 600 mm hits
    string A (at x=1000) earlier than string B (at x=3000). The peak
    intensity moment for each string falls at the time when the sphere
    centre crosses its base X — distinct frames, in geometrically
    correct order."""
    fixture = {
        "type": "linear",
        "childPos": [0, 0, 0],
        "rotation": [0, 0, 0],
        "strings": [
            {"leds": 5, "mm": 50, "sdir": 0, "x": 1000, "y": 2000, "z": 1500},
            {"leds": 5, "mm": 50, "sdir": 0, "x": 3000, "y": 2000, "z": 1500},
        ],
    }
    pixels = spatial_engine.resolve_fixture(fixture)["pixelPositions"]
    string_a = pixels[:5]
    string_b = pixels[5:]

    radius = 600
    color = [255, 255, 255]
    duration = 5.0
    samples = 101

    def avg_intensity(p_list, sphere_x):
        out = spatial_engine.sphere_field_evaluate(
            [sphere_x, 2000, 1500], radius, p_list, color, falloff=True)
        return sum(p[0] for p in out) / max(1, len(out))

    a_peak_t = None
    b_peak_t = None
    a_peak_v = -1
    b_peak_v = -1
    for i in range(samples):
        t = i / (samples - 1) * duration
        sphere_x = (t / duration) * 5000  # 0 → 5000 over 5 s
        a = avg_intensity(string_a, sphere_x)
        b = avg_intensity(string_b, sphere_x)
        if a > a_peak_v:
            a_peak_v = a
            a_peak_t = t
        if b > b_peak_v:
            b_peak_v = b
            b_peak_t = t

    # Both strings light up.
    assert a_peak_v > 100
    assert b_peak_v > 100
    # String A peaks before String B by ~2 s (geometrically: 2000 mm at
    # 1000 mm/s sphere velocity = 2 s).
    assert a_peak_t is not None and b_peak_t is not None
    assert b_peak_t - a_peak_t > 1.5, (a_peak_t, b_peak_t)


def test_864_validate_partial_override_rejected():
    """POST/PUT must reject a string that fills only some of x/y/z so
    a half-set entry can't silently mix per-string and inherited axes."""
    err = parent_server._validate_fixture_strings([
        {"leds": 10, "mm": 1000, "sdir": 0, "x": 100},  # missing y, z
    ])
    assert err is not None and "partial" in err.lower(), err

    err2 = parent_server._validate_fixture_strings([
        {"leds": 10, "mm": 1000, "sdir": 0, "x": "wrong", "y": 1, "z": 2},
    ])
    assert err2 is not None and "numeric" in err2.lower(), err2

    # Fully-set is OK.
    assert parent_server._validate_fixture_strings([
        {"leds": 10, "mm": 1000, "sdir": 0, "x": 1, "y": 2, "z": 3},
    ]) is None
    # Fully-absent is OK (legacy default).
    assert parent_server._validate_fixture_strings([
        {"leds": 10, "mm": 1000, "sdir": 0},
    ]) is None


if __name__ == "__main__":
    test_864_resolve_fixture_per_string_override()
    test_864_resolve_fixture_inherits_when_unset()
    test_864_sphere_sweep_distinct_timing_per_string()
    test_864_validate_partial_override_rejected()
    print("OK — #864 per-string position tests passed")
