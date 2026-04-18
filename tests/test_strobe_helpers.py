"""Shutter / strobe capability helper tests (#516).

Covers `dmx_profiles.strobe_open_value`, `strobe_range`,
`strobe_value_for_speed`, and `shutter_effect_at` across:

  - annotated profiles (shutterEffect field on ShutterStrobe ranges)
  - legacy profiles (label-only heuristic)
  - unusual wirings (Closed at DMX=0)
  - profiles without a strobe channel at all

Run:
    python -X utf8 tests/test_strobe_helpers.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from dmx_profiles import (  # noqa: E402
    strobe_open_value, strobe_range, strobe_value_for_speed,
    shutter_effect_at, SHUTTER_EFFECTS,
)


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


# ── Profile fixtures ─────────────────────────────────────────────────────

ANNOTATED_OPEN_LOW = {
    "channels": [
        {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
            {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
            {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe slow-fast"},
        ]},
    ],
    "channel_map": {"strobe": 4},
}

ANNOTATED_CLOSED_LOW = {
    # Some fixtures close the shutter at DMX=0 instead of opening.
    "channels": [
        {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
            {"range": [0, 3],     "type": "ShutterStrobe", "shutterEffect": "Closed", "label": "Closed"},
            {"range": [4, 9],     "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
            {"range": [10, 255],  "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe"},
        ]},
    ],
    "channel_map": {"strobe": 4},
}

LEGACY_OPEN = {
    # No shutterEffect annotation — helper must fall back to labels.
    "channels": [
        {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
            {"range": [0, 3],   "type": "ShutterStrobe", "label": "Open"},
            {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
        ]},
    ],
    "channel_map": {"strobe": 4},
}

LEGACY_CLOSED = {
    "channels": [
        {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
            {"range": [0, 3],   "type": "ShutterStrobe", "label": "Closed"},
            {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
        ]},
    ],
    "channel_map": {"strobe": 4},
}

NO_STROBE = {
    "channels": [
        {"offset": 0, "name": "Red",   "type": "red",   "capabilities": []},
        {"offset": 1, "name": "Green", "type": "green", "capabilities": []},
        {"offset": 2, "name": "Blue",  "type": "blue",  "capabilities": []},
    ],
    "channel_map": {"red": 0, "green": 1, "blue": 2},
}


# ── strobe_open_value ─────────────────────────────────────────────────────

def test_open_annotated_low():
    # Open range is [0, 3], midpoint = 1.
    _assert(strobe_open_value(ANNOTATED_OPEN_LOW) == 1,
            f"annotated Open=[0,3] mid, got {strobe_open_value(ANNOTATED_OPEN_LOW)}")


def test_open_annotated_mid():
    # Open range is [4, 9], midpoint = 6.
    v = strobe_open_value(ANNOTATED_CLOSED_LOW)
    _assert(v == 6, f"annotated Open=[4,9] mid, got {v}")


def test_open_legacy_label_open():
    v = strobe_open_value(LEGACY_OPEN)
    _assert(v == 1, f"legacy label 'Open' → mid=1, got {v}")


def test_open_legacy_label_closed_at_zero_returns_zero():
    # First range is 'Closed' (no Open anywhere) — helper returns safe 0.
    v = strobe_open_value(LEGACY_CLOSED)
    _assert(v == 0, f"legacy Closed-at-0, no Open → 0, got {v}")


def test_open_no_strobe_channel():
    _assert(strobe_open_value(NO_STROBE) == 0, "no strobe channel → 0")


# ── strobe_range ──────────────────────────────────────────────────────────

def test_range_strobe_annotated():
    _assert(strobe_range(ANNOTATED_OPEN_LOW, "Strobe") == (4, 255),
            f"strobe range, got {strobe_range(ANNOTATED_OPEN_LOW, 'Strobe')}")


def test_range_open_annotated():
    _assert(strobe_range(ANNOTATED_CLOSED_LOW, "Open") == (4, 9),
            f"open range, got {strobe_range(ANNOTATED_CLOSED_LOW, 'Open')}")


def test_range_legacy_label_strobe():
    _assert(strobe_range(LEGACY_OPEN, "Strobe") == (4, 255),
            "legacy 'Strobe slow-fast' label → Strobe range via heuristic")


def test_range_missing_effect_none():
    _assert(strobe_range(ANNOTATED_OPEN_LOW, "Lightning") is None,
            "missing effect returns None")


def test_range_no_strobe_channel():
    _assert(strobe_range(NO_STROBE, "Strobe") is None,
            "no strobe channel → None")


# ── strobe_value_for_speed ────────────────────────────────────────────────

def test_speed_mid():
    v = strobe_value_for_speed(ANNOTATED_OPEN_LOW, 50)
    # Range [4, 255], 50 % → 4 + 0.5*(251) = 129.5 → round 130
    _assert(v == 130, f"speed 50 → 130, got {v}")


def test_speed_min_clamped():
    _assert(strobe_value_for_speed(ANNOTATED_OPEN_LOW, 0) == 4,
            "speed 0 → range min 4")


def test_speed_max_clamped():
    _assert(strobe_value_for_speed(ANNOTATED_OPEN_LOW, 100) == 255,
            "speed 100 → range max 255")


def test_speed_fractional_accepts_0to1():
    v = strobe_value_for_speed(ANNOTATED_OPEN_LOW, 0.5)
    _assert(v == 130, f"speed 0.5 (fractional) also 130, got {v}")


def test_speed_no_strobe_returns_none():
    _assert(strobe_value_for_speed(NO_STROBE, 50) is None,
            "no strobe channel → None so caller can fall back")


# ── shutter_effect_at ────────────────────────────────────────────────────

def test_effect_lookup_at_zero():
    _assert(shutter_effect_at(ANNOTATED_OPEN_LOW, 0) == "Open",
            "DMX 0 in Open range")


def test_effect_lookup_mid():
    _assert(shutter_effect_at(ANNOTATED_OPEN_LOW, 128) == "Strobe",
            "DMX 128 in Strobe range")


def test_effect_lookup_boundary_closed_at_zero():
    _assert(shutter_effect_at(ANNOTATED_CLOSED_LOW, 0) == "Closed",
            "DMX 0 → Closed when wired that way")
    _assert(shutter_effect_at(ANNOTATED_CLOSED_LOW, 6) == "Open",
            "DMX 6 → Open window")
    _assert(shutter_effect_at(ANNOTATED_CLOSED_LOW, 200) == "Strobe",
            "DMX 200 → Strobe range")


def test_effect_lookup_no_channel():
    _assert(shutter_effect_at(NO_STROBE, 0) is None,
            "no strobe channel → None")


def test_effect_lookup_out_of_range_clamp():
    _assert(shutter_effect_at(ANNOTATED_OPEN_LOW, -5) == "Open",
            "DMX -5 clamps to 0 → Open")
    _assert(shutter_effect_at(ANNOTATED_OPEN_LOW, 9999) == "Strobe",
            "DMX 9999 clamps to 255 → Strobe")


# ── SHUTTER_EFFECTS constant ──────────────────────────────────────────────

def test_shutter_effects_contains_core_values():
    for v in ("Open", "Closed", "Strobe", "Pulse", "Lightning"):
        _assert(v in SHUTTER_EFFECTS, f"{v} in SHUTTER_EFFECTS")


# ── Built-in profile sanity ───────────────────────────────────────────────

def test_builtin_rgb_strobe_5ch():
    """generic-rgb-strobe uses Closed at DMX=0; Open lookup must return 0
    (the safe default) since there's no Open range declared."""
    from dmx_profiles import BUILTIN_PROFILES
    prof = next(p for p in BUILTIN_PROFILES if p["id"] == "generic-rgb-strobe")
    _assert(strobe_open_value(prof) == 0,
            "generic-rgb-strobe (Closed at 0, no Open) → 0")
    _assert(shutter_effect_at(prof, 0) == "Closed",
            "generic-rgb-strobe DMX=0 reports Closed")


def test_builtin_moving_head_16bit():
    """generic-moving-head-16bit has Open=[0,3] so opening is safe at 0."""
    from dmx_profiles import BUILTIN_PROFILES
    prof = next(p for p in BUILTIN_PROFILES if p["id"] == "generic-moving-head-16bit")
    v = strobe_open_value(prof)
    _assert(0 <= v <= 3, f"moving-head-16bit open value in [0,3], got {v}")
    _assert(shutter_effect_at(prof, 1) == "Open", "DMX=1 reports Open")


ALL = [
    test_open_annotated_low,
    test_open_annotated_mid,
    test_open_legacy_label_open,
    test_open_legacy_label_closed_at_zero_returns_zero,
    test_open_no_strobe_channel,
    test_range_strobe_annotated,
    test_range_open_annotated,
    test_range_legacy_label_strobe,
    test_range_missing_effect_none,
    test_range_no_strobe_channel,
    test_speed_mid,
    test_speed_min_clamped,
    test_speed_max_clamped,
    test_speed_fractional_accepts_0to1,
    test_speed_no_strobe_returns_none,
    test_effect_lookup_at_zero,
    test_effect_lookup_mid,
    test_effect_lookup_boundary_closed_at_zero,
    test_effect_lookup_no_channel,
    test_effect_lookup_out_of_range_clamp,
    test_shutter_effects_contains_core_values,
    test_builtin_rgb_strobe_5ch,
    test_builtin_moving_head_16bit,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
