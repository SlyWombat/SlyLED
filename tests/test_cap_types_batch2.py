"""Second DMX-capability-type batch (#518, #521, #523, #524, #525, #526).

Each test creates a minimal profile carrying the new cap type and runs
it through:

  1. ProfileLibrary.validate_profile — asserts the cap type is in
     CAPABILITY_TYPES so the profile editor accepts it.
  2. OFL importer — feeds the equivalent OFL mode through
     `_build_channels_from_ofl` and confirms the canonical SlyLED
     cap type + channel type land, plus the specific metadata fields
     (colors/colorTemperature/slotNumber/insertion/speed/…) pass
     through untouched.

Run:
    python -X utf8 tests/test_cap_types_batch2.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from dmx_profiles import (  # noqa: E402
    ProfileLibrary, CAPABILITY_TYPES, CHANNEL_TYPES,
)
from ofl_importer import _resolve_channel_type, _convert_capabilities  # noqa: E402


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


def _lib():
    return ProfileLibrary()


def _validate_profile(profile):
    lib = _lib()
    return lib.validate_profile(profile)


def _build(ofl_channels, ofl_modes_channel_list):
    """Single-channel OFL → SlyLED helper. The real importer merges in
    mode offsets etc., but for cap-type verification we only need the
    per-channel output so we skip that layer."""
    out = []
    for i, name in enumerate(ofl_modes_channel_list):
        ofl_ch = ofl_channels.get(name) or {}
        ch = {
            "offset": i,
            "name": name,
            "type": _resolve_channel_type(ofl_ch),
            "bits": 8,
            "capabilities": _convert_capabilities(ofl_ch),
        }
        out.append(ch)
    return out


# ── #518 ColorPreset ─────────────────────────────────────────────────────

def test_colorpreset_registered_and_importer():
    _assert("ColorPreset" in CAPABILITY_TYPES, "ColorPreset is a registered cap type")
    ofl_ch = {
        "Color Preset": {
            "capabilities": [
                {"dmxRange": [0, 7],   "type": "ColorPreset", "comment": "White + Open"},
                {"dmxRange": [8, 23],  "type": "ColorPreset", "colors": ["#ff0000"],
                 "comment": "Red"},
                {"dmxRange": [24, 39], "type": "ColorPreset",
                 "colors": ["#ff0000", "#ffff00"], "colorTemperature": "3200K",
                 "comment": "Red/Amber (3200K)"},
            ]
        }
    }
    mode_chs = ["Color Preset"]
    result = _build(ofl_ch, mode_chs)
    _assert(len(result) == 1, f"1 channel emitted, got {len(result)}")
    ch = result[0]
    _assert(ch["type"] == "color-wheel", f"channel type color-wheel, got {ch['type']}")
    caps = ch["capabilities"]
    _assert(len(caps) == 3, f"3 caps, got {len(caps)}")
    _assert(caps[1]["type"] == "ColorPreset", "cap type preserved")
    _assert(caps[1].get("colors") == ["#ff0000"], "single-colour array preserved")
    _assert(caps[2].get("colors") == ["#ff0000", "#ffff00"], "multi-colour array preserved")
    _assert(caps[2].get("colorTemperature") == 3200.0, "Kelvin parsed from '3200K'")


# ── #521 WheelSlotRotation ───────────────────────────────────────────────

def test_wheel_slot_rotation():
    _assert("WheelSlotRotation" in CAPABILITY_TYPES,
            "WheelSlotRotation registered")
    ofl_ch = {
        "Gobo Rotation": {
            "capabilities": [
                {"dmxRange": [0, 127],   "type": "WheelSlotRotation",
                 "slotNumber": 3, "speedStart": "0Hz", "speedEnd": "5Hz"},
                {"dmxRange": [128, 255], "type": "WheelSlotRotation",
                 "slotNumber": 3, "angle": "45deg"},
            ]
        }
    }
    result = _build(ofl_ch, ["Gobo Rotation"])
    caps = result[0]["capabilities"]
    _assert(result[0]["type"] == "gobo-rotation",
            f"channel gobo-rotation, got {result[0]['type']}")
    _assert(caps[0]["type"] == "WheelSlotRotation", "cap type preserved")
    _assert(caps[0]["slotNumber"] == 3, "slotNumber preserved")
    _assert(caps[0]["speedStart"] == 0.0 and caps[0]["speedEnd"] == 5.0,
            "speed range parsed from Hz strings")
    _assert(caps[1]["angle"] == 45.0, "angle parsed")


# ── #523 Iris / IrisEffect ───────────────────────────────────────────────

def test_iris_and_effect():
    for t in ("Iris", "IrisEffect"):
        _assert(t in CAPABILITY_TYPES, f"{t} registered")
    _assert("iris" in CHANNEL_TYPES, "iris channel type registered")
    ofl_ch = {
        "Iris": {
            "capabilities": [
                {"dmxRange": [0, 0],     "type": "Iris", "openPercent": "100%"},
                {"dmxRange": [1, 180],   "type": "Iris",
                 "openPercentStart": "100%", "openPercentEnd": "0%"},
                {"dmxRange": [181, 255], "type": "IrisEffect",
                 "effectName": "Slow pulse"},
            ]
        }
    }
    result = _build(ofl_ch, ["Iris"])
    _assert(result[0]["type"] == "iris", f"channel iris, got {result[0]['type']}")
    caps = result[0]["capabilities"]
    _assert(caps[0]["openPercent"] == 100.0, "static openPercent parsed")
    _assert(caps[1]["openPercentStart"] == 100.0 and caps[1]["openPercentEnd"] == 0.0,
            "open range parsed")
    _assert(caps[2]["type"] == "IrisEffect", "effect variant cap type")
    _assert(caps[2]["effectName"] == "Slow pulse", "effectName preserved")


# ── #524 Blade ────────────────────────────────────────────────────────────

def test_blade_channels():
    for t in ("BladeInsertion", "BladeRotation", "BladeSystemRotation"):
        _assert(t in CAPABILITY_TYPES, f"{t} registered")
    _assert("blade" in CHANNEL_TYPES, "blade channel type registered")
    ofl_ch = {
        "Blade 1": {
            "capabilities": [
                {"dmxRange": [0, 255], "type": "BladeInsertion",
                 "bladeNumber": 1,
                 "insertionStart": "0%", "insertionEnd": "100%"},
            ]
        },
        "Blade 1 Rot": {
            "capabilities": [
                {"dmxRange": [0, 255], "type": "BladeRotation",
                 "bladeNumber": 1,
                 "angleStart": "-45deg", "angleEnd": "45deg"},
            ]
        },
        "Frame Rot": {
            "capabilities": [
                {"dmxRange": [0, 255], "type": "BladeSystemRotation",
                 "angleStart": "-60deg", "angleEnd": "60deg"},
            ]
        },
    }
    result = _build(ofl_ch, ["Blade 1", "Blade 1 Rot", "Frame Rot"])
    for ch in result:
        _assert(ch["type"] == "blade",
                f"channel type blade on {ch['name']}, got {ch['type']}")
    _assert(result[0]["capabilities"][0]["bladeNumber"] == 1,
            "blade insertion bladeNumber preserved")
    _assert(result[0]["capabilities"][0]["insertionEnd"] == 100.0,
            "insertion percent parsed")
    _assert(result[1]["capabilities"][0]["angleStart"] == -45.0,
            "rotation negative angle parsed")
    _assert(result[2]["capabilities"][0]["type"] == "BladeSystemRotation",
            "system rotation preserved distinct from per-blade")


# ── #525 EffectSpeed ─────────────────────────────────────────────────────

def test_effect_speed():
    _assert("EffectSpeed" in CAPABILITY_TYPES, "EffectSpeed registered")
    _assert("effect-speed" in CHANNEL_TYPES, "effect-speed channel type registered")
    ofl_ch = {
        "FX Speed": {
            "capabilities": [
                {"dmxRange": [0, 127],   "type": "EffectSpeed",
                 "speedStart": "0Hz", "speedEnd": "5Hz"},
                {"dmxRange": [128, 255], "type": "EffectSpeed",
                 "speedStart": "5Hz", "speedEnd": "15Hz"},
            ]
        }
    }
    result = _build(ofl_ch, ["FX Speed"])
    _assert(result[0]["type"] == "effect-speed",
            f"channel effect-speed, got {result[0]['type']}")
    caps = result[0]["capabilities"]
    _assert(caps[0]["type"] == "EffectSpeed", "cap type preserved")
    _assert(caps[0]["speedEnd"] == 5.0, "low range end parsed")
    _assert(caps[1]["speedStart"] == 5.0, "high range start parsed")


# ── #526 Rotation ────────────────────────────────────────────────────────

def test_rotation_bodyspin():
    _assert("Rotation" in CAPABILITY_TYPES, "Rotation registered")
    _assert("rotation" in CHANNEL_TYPES, "rotation channel type registered")
    ofl_ch = {
        "Animation Wheel Spin": {
            "capabilities": [
                {"dmxRange": [0, 0],     "type": "Rotation", "speed": "0Hz"},
                {"dmxRange": [1, 127],   "type": "Rotation",
                 "speedStart": "0.1Hz", "speedEnd": "5Hz"},
                {"dmxRange": [128, 128], "type": "Rotation", "speed": "0Hz"},
                {"dmxRange": [129, 255], "type": "Rotation",
                 "speedStart": "-0.1Hz", "speedEnd": "-5Hz"},
            ]
        }
    }
    result = _build(ofl_ch, ["Animation Wheel Spin"])
    _assert(result[0]["type"] == "rotation",
            f"channel rotation, got {result[0]['type']}")
    caps = result[0]["capabilities"]
    _assert(all(c["type"] == "Rotation" for c in caps),
            "all caps are Rotation type")
    _assert(caps[1]["speedStart"] == 0.1 and caps[1]["speedEnd"] == 5.0,
            "CW speed range parsed")
    _assert(caps[3]["speedStart"] == -0.1 and caps[3]["speedEnd"] == -5.0,
            "CCW negative speed parsed")


# ── Round-trip validation ────────────────────────────────────────────────

def test_profile_validation_accepts_new_types():
    """A synthetic profile using every new cap type must validate cleanly."""
    profile = {
        "id": "test-batch2-fixture",
        "name": "Test Batch2",
        "manufacturer": "Test",
        "category": "moving-head",
        "colorMode": "rgb",
        "beamWidth": 10,
        "panRange": 540, "tiltRange": 270,
        "channels": [
            {"offset": 0, "name": "Color Preset", "type": "color-wheel",
             "capabilities": [{"range": [0, 255], "type": "ColorPreset",
                               "colors": ["#ff0000"], "label": "Red"}]},
            {"offset": 1, "name": "Gobo Rot", "type": "gobo-rotation",
             "capabilities": [{"range": [0, 255], "type": "WheelSlotRotation",
                               "slotNumber": 1, "label": "Rotate"}]},
            {"offset": 2, "name": "Iris", "type": "iris",
             "capabilities": [{"range": [0, 255], "type": "Iris",
                               "openPercent": 100, "label": "Iris"}]},
            {"offset": 3, "name": "Blade 1", "type": "blade",
             "capabilities": [{"range": [0, 255], "type": "BladeInsertion",
                               "bladeNumber": 1, "label": "Blade 1 in"}]},
            {"offset": 4, "name": "Effect Speed", "type": "effect-speed",
             "capabilities": [{"range": [0, 255], "type": "EffectSpeed",
                               "label": "Effect speed"}]},
            {"offset": 5, "name": "Body Spin", "type": "rotation",
             "capabilities": [{"range": [0, 255], "type": "Rotation",
                               "label": "Body rotation"}]},
        ],
        "channelCount": 6,
    }
    ok, err = _validate_profile(profile)
    _assert(ok, f"profile validates clean, err={err}")


ALL = [
    test_colorpreset_registered_and_importer,
    test_wheel_slot_rotation,
    test_iris_and_effect,
    test_blade_channels,
    test_effect_speed,
    test_rotation_bodyspin,
    test_profile_validation_accepts_new_types,
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
