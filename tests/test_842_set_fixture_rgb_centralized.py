#!/usr/bin/env python3
"""test_842_set_fixture_rgb_centralized.py - Regression for #842.

Asserts that DMXUniverse.set_fixture_rgb is the single dispatch point
for RGB-to-DMX colour writes regardless of the profile shape:

  * RGB profile        - red/green/blue offsets get the component bytes.
  * Hybrid RGB+wheel   - RGB writes AND the wheel is forced to slot 0
                          (per feedback_hybrid_rgb_wheel_colors.md).
  * Colour-wheel-only  - the wheel slot is derived from rgb_to_wheel_slot
                          and written to the wheel offset.

Also verifies the three render paths in parent_server.py no longer
contain the duplicated `if "red" in cm: ...; elif "color-wheel" in cm:
rgb_to_wheel_slot(...)` branch (i.e. they delegate to set_fixture_rgb).

Run: python -X utf8 tests/test_842_set_fixture_rgb_centralized.py
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from dmx_universe import DMXUniverse  # noqa: E402

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


# ---------------------------------------------------------------------------

def test_rgb_profile_writes_components():
    u = DMXUniverse()
    profile = {"channel_map": {"red": 0, "green": 1, "blue": 2}}
    u.set_fixture_rgb(1, 100, 200, 50, profile)
    _assert(u.get_channel(1) == 100, f"red byte at addr 1 (got {u.get_channel(1)})")
    _assert(u.get_channel(2) == 200, f"green byte at addr 2 (got {u.get_channel(2)})")
    _assert(u.get_channel(3) == 50,  f"blue byte at addr 3 (got {u.get_channel(3)})")


def test_hybrid_rgb_wheel_forces_wheel_to_open():
    """Hybrid profile (RGB + colour wheel) must drive the wheel to slot
    0 alongside RGB so the wheel's default slot doesn't filter the
    beam (long-standing project rule, now centralized)."""
    u = DMXUniverse()
    profile = {"channel_map": {"red": 0, "green": 1, "blue": 2,
                                 "color-wheel": 6}}
    # Pre-load the wheel with a non-zero value to verify it's actively
    # cleared, not just left untouched.
    u.set_channel(7, 99)
    u.set_fixture_rgb(1, 100, 200, 50, profile)
    _assert(u.get_channel(1) == 100, "RGB write OK on hybrid")
    _assert(u.get_channel(7) == 0,
            f"hybrid forces wheel to slot 0 (was 99, got {u.get_channel(7)})")


def test_wheel_only_profile_derives_slot_from_rgb():
    """A profile with no R/G/B but with a colour wheel must resolve
    RGB to the closest wheel slot via rgb_to_wheel_slot."""
    u = DMXUniverse()
    profile = {
        "channel_map": {"color-wheel": 5},
        "channels": [{
            "type": "color-wheel", "offset": 5,
            "capabilities": [
                {"type": "WheelSlot", "color": "#ffffff", "range": [0, 0]},
                {"type": "WheelSlot", "color": "#ff0000", "range": [10, 19]},
                {"type": "WheelSlot", "color": "#00ff00", "range": [20, 29]},
                {"type": "WheelSlot", "color": "#0000ff", "range": [30, 39]},
            ],
        }],
    }
    # Pure-red request → red slot midpoint = (10+19)//2 = 14.
    u.set_fixture_rgb(1, 255, 0, 0, profile)
    _assert(u.get_channel(6) == 14,
            f"wheel-only red picks red slot (got {u.get_channel(6)}, expected 14)")

    # Pure blue → blue slot midpoint = (30+39)//2 = 34.
    u2 = DMXUniverse()
    u2.set_fixture_rgb(1, 0, 0, 255, profile)
    _assert(u2.get_channel(6) == 34,
            f"wheel-only blue picks blue slot (got {u2.get_channel(6)}, expected 34)")

    # All-zero RGB → slot 0 (open).
    u3 = DMXUniverse()
    u3.set_channel(6, 99)
    u3.set_fixture_rgb(1, 0, 0, 0, profile)
    _assert(u3.get_channel(6) == 0,
            f"wheel-only black goes to open slot (got {u3.get_channel(6)})")


def test_no_profile_falls_back_to_raw_block():
    """No profile → write three contiguous bytes at start_addr."""
    u = DMXUniverse()
    u.set_fixture_rgb(10, 11, 22, 33, profile=None)
    _assert(u.get_channel(10) == 11, "raw-block byte 0")
    _assert(u.get_channel(11) == 22, "raw-block byte 1")
    _assert(u.get_channel(12) == 33, "raw-block byte 2")


def test_empty_profile_silently_noops():
    """A profile with neither RGB nor wheel mappings should no-op
    rather than scribble random bytes."""
    u = DMXUniverse()
    profile = {"channel_map": {"dimmer": 0}}
    u.set_fixture_rgb(1, 100, 200, 50, profile)
    _assert(u.get_channel(1) == 0, "no RGB / wheel → no scribble at addr 1")


# ---------------------------------------------------------------------------
# Static-source assertions: render paths must no longer reach into
# rgb_to_wheel_slot themselves.

def test_render_paths_do_not_inline_rgb_to_wheel_slot():
    import parent_server  # noqa: E402
    for fn_name in ("_dmx_playback_loop", "_show_playback_loop",
                     "_evaluate_track_actions"):
        src = inspect.getsource(getattr(parent_server, fn_name))
        _assert("rgb_to_wheel_slot" not in src,
                f"{fn_name} no longer calls rgb_to_wheel_slot directly")


def test_set_fixture_color_helper_is_a_thin_shim():
    import parent_server  # noqa: E402
    src = inspect.getsource(parent_server._set_fixture_color)
    _assert("rgb_to_wheel_slot" not in src,
            "_set_fixture_color helper is a thin shim over set_fixture_rgb")
    _assert("set_fixture_rgb" in src,
            "_set_fixture_color delegates to set_fixture_rgb")


ALL = [
    test_rgb_profile_writes_components,
    test_hybrid_rgb_wheel_forces_wheel_to_open,
    test_wheel_only_profile_derives_slot_from_rgb,
    test_no_profile_falls_back_to_raw_block,
    test_empty_profile_silently_noops,
    test_render_paths_do_not_inline_rgb_to_wheel_slot,
    test_set_fixture_color_helper_is_a_thin_shim,
]


if __name__ == "__main__":
    print("=== #842 set_fixture_rgb centralizes wheel-slot dispatch ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
