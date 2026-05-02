"""
DMX Fixture Profile System — JSON definitions, channel mapping, capability ranges.

Provides built-in profiles for common fixture types and supports custom
user-defined profiles stored in the data directory.

Profile schema:
{
  "id": "generic-rgb-par",
  "name": "Generic RGB Par",
  "manufacturer": "Generic",
  "category": "par",          # par | wash | spot | moving-head | strobe | fog | laser | other
  "channels": [
    {
      "offset": 0, "name": "Red", "type": "red",
      "capabilities": [
        {"range": [0, 255], "type": "ColorIntensity", "label": "Red 0-100%"}
      ]
    },
    ...
  ],
  "channelCount": 3,
  "colorMode": "rgb",          # rgb | cmy | rgbw | rgba | single
  "beamWidth": 25,             # degrees (0 = wash/flood)
  "panRange": 0,               # degrees (0 = no pan)
  "tiltRange": 0,              # degrees (0 = no tilt)
  "tiltOffsetDmx16": 32768,    # #716 — DMX value at horizontal-forward (default mid-range)
  "tiltUp": false,             # #716 — does +DMX-tilt move beam UP from offset?
  "panSignFromDmx": 1,         # #783 — +1 = +DMX rotates yoke CCW from above
  "tiltSignFromDmx": 1,        # #783 — +1 = +DMX raises beam relative to fixture-up
}

Channel types (primary function): dimmer, red, green, blue, white, amber, uv,
    pan, pan-fine, tilt, tilt-fine, strobe, gobo, gobo-rotation, prism,
    prism-rotation, focus, zoom, frost, color-wheel, color-temp, iris,
    blade, rotation, speed, pan-tilt-speed, effect-speed, macro, reset

Capability types: ColorIntensity, ColorPreset, Intensity, Pan, PanContinuous,
    Tilt, TiltContinuous, PanTiltSpeed, ShutterStrobe, WheelSlot, WheelShake,
    WheelRotation, WheelSlotRotation, Prism, PrismRotation,
    ColorTemperature, Iris, IrisEffect, BladeInsertion, BladeRotation,
    BladeSystemRotation, Rotation, EffectSpeed, Focus, Zoom, Frost, Speed,
    Maintenance, Effect, NoFunction, Generic
"""

import json
import os
from pathlib import Path

# -- Valid sets ---------------------------------------------------------------

CHANNEL_TYPES = {
    "dimmer", "red", "green", "blue", "white", "amber", "uv",
    "pan", "pan-fine", "tilt", "tilt-fine",
    "strobe", "gobo", "gobo-rotation", "prism", "prism-rotation",
    "focus", "zoom", "frost",
    "color-wheel", "color-temp",
    "iris", "blade", "rotation",
    "speed", "pan-tilt-speed", "effect-speed",
    "macro", "reset",
}

CAPABILITY_TYPES = {
    "ColorIntensity", "ColorPreset", "Intensity",
    "Pan", "PanContinuous", "Tilt", "TiltContinuous", "PanTiltSpeed",
    "ShutterStrobe",
    "WheelSlot", "WheelShake", "WheelRotation", "WheelSlotRotation",
    "Prism", "PrismRotation",
    "ColorTemperature",
    "Iris", "IrisEffect",
    "BladeInsertion", "BladeRotation", "BladeSystemRotation",
    "Rotation", "EffectSpeed",
    "Focus", "Zoom", "Frost",
    "Speed", "Maintenance", "Effect", "NoFunction", "Generic",
}

CATEGORIES = {"par", "wash", "spot", "moving-head", "strobe", "fog", "laser", "bar", "matrix", "blinder", "other"}

# -- Color wheel matching -----------------------------------------------------

import math as _math
import re as _re

def rgb_to_wheel_slot(prof_info, r, g, b):
    """Find the closest color wheel slot for an RGB value.

    Searches color-wheel channel capabilities of type WheelSlot that have
    a 'color' hex field.  Uses Euclidean distance in RGB space.

    Args:
        prof_info: dict with 'channels' list (from channel_info() or profile)
        r, g, b: 0-255 target color

    Returns:
        DMX value (midpoint of closest slot's range), or 0 (open/white)
        if no color-annotated WheelSlot caps exist.
    """
    if r == 0 and g == 0 and b == 0:
        return 0  # black = open/white (dimmer controls brightness, not color)
    best_val, best_dist = 0, float("inf")
    for ch in (prof_info.get("channels") or []):
        if ch.get("type") != "color-wheel":
            continue
        for cap in (ch.get("capabilities") or []):
            if cap.get("type") != "WheelSlot":
                continue
            hex_color = cap.get("color", "")
            if not hex_color or len(hex_color) != 7:
                continue
            try:
                cr = int(hex_color[1:3], 16)
                cg = int(hex_color[3:5], 16)
                cb = int(hex_color[5:7], 16)
            except ValueError:
                continue
            dist = _math.sqrt((r - cr)**2 + (g - cg)**2 + (b - cb)**2)
            if dist < best_dist:
                best_dist = dist
                rng = cap.get("range", [0, 0])
                best_val = (rng[0] + rng[1]) // 2
    return best_val


def has_color_wheel_only(prof_info):
    """True if profile has a color-wheel channel but no RGB channels."""
    ch_map = prof_info.get("channel_map", {})
    return "color-wheel" in ch_map and "red" not in ch_map


# ── Shutter / strobe helpers (#516) ────────────────────────────────────────
#
# ShutterStrobe capability ranges can carry a `shutterEffect` field that maps
# semantic meaning (Open / Closed / Strobe / Pulse / Lightning / …) to a DMX
# range. Consumers call these helpers instead of guessing "0 means open".
# For profiles that haven't been annotated we fall back to a label-based
# heuristic so legacy profiles keep working.

# Canonical shutterEffect values (aligned with OFL / GDTF).
SHUTTER_EFFECTS = (
    "Open", "Closed", "Strobe", "Pulse",
    "RampUp", "RampDown", "RampUpDown", "Lightning",
)


def _strobe_channel(prof_info):
    """Return the first ``type: "strobe"`` channel dict, or None."""
    for ch in (prof_info.get("channels") or []):
        if ch.get("type") == "strobe":
            return ch
    return None


def _cap_effect(cap):
    """Extract the canonical shutterEffect from a capability dict, using
    the explicit field when present and falling back to a label scan.
    Returns None if the capability is not a ShutterStrobe."""
    if cap.get("type") != "ShutterStrobe":
        return None
    eff = cap.get("shutterEffect")
    if eff:
        return eff
    label = (cap.get("label") or "").lower()
    if not label:
        return None
    # Most-specific matches first so "solid open" doesn't mistake for Open.
    if "closed" in label or "blackout" in label:
        return "Closed"
    if "lightning" in label:
        return "Lightning"
    if "ramp up" in label and "down" in label:
        return "RampUpDown"
    if "ramp up" in label:
        return "RampUp"
    if "ramp down" in label:
        return "RampDown"
    if "pulse" in label:
        return "Pulse"
    if "strobe" in label:
        return "Strobe"
    if "open" in label or "solid" in label:
        return "Open"
    return None


def strobe_open_value(prof_info):
    """Return the DMX value that means 'shutter open / solid light' for
    this profile — the midpoint of the first ShutterStrobe range whose
    ``shutterEffect`` is ``Open``. Legacy profiles without the annotation
    fall back to label matching (`"open"` / `"solid"`).

    Returns 0 when the profile has no strobe channel, no Open range, or
    isn't a ShutterStrobe — 0 is the safe default for fixtures where
    DMX=0 means full-light (most common wiring convention).
    """
    ch = _strobe_channel(prof_info)
    if not ch:
        return 0
    for cap in (ch.get("capabilities") or []):
        if _cap_effect(cap) == "Open":
            rng = cap.get("range") or [0, 0]
            return int((rng[0] + rng[1]) // 2)
    return 0


def strobe_range(prof_info, effect="Strobe"):
    """Return ``(min_dmx, max_dmx)`` for the named shutter effect, or
    None when the profile doesn't declare that effect. Used to map a
    0-100 % speed slider onto the profile's actual DMX window."""
    ch = _strobe_channel(prof_info)
    if not ch:
        return None
    for cap in (ch.get("capabilities") or []):
        if _cap_effect(cap) == effect:
            rng = cap.get("range")
            if rng and len(rng) == 2:
                return (int(rng[0]), int(rng[1]))
    return None


def strobe_value_for_speed(prof_info, speed_pct, effect="Strobe"):
    """Map a 0-100 % strobe-speed slider to the profile's actual DMX
    range for the given effect. Clamped to the range endpoints. Returns
    None when the profile doesn't declare the requested effect (caller
    should fall back to a literal DMX write)."""
    rng = strobe_range(prof_info, effect)
    if rng is None:
        return None
    p = max(0.0, min(1.0, float(speed_pct) / 100.0 if speed_pct > 1 else float(speed_pct)))
    lo, hi = rng
    return int(round(lo + p * (hi - lo)))


def shutter_effect_at(prof_info, dmx_value):
    """Reverse lookup: which shutterEffect is the fixture currently in,
    given the DMX value on its strobe channel? Returns None when the
    profile has no strobe channel or no matching range. Useful for the
    live-output status widget."""
    ch = _strobe_channel(prof_info)
    if not ch:
        return None
    dv = max(0, min(255, int(dmx_value)))
    for cap in (ch.get("capabilities") or []):
        rng = cap.get("range")
        if not rng or len(rng) != 2:
            continue
        if rng[0] <= dv <= rng[1]:
            return _cap_effect(cap)
    return None


# -- Sphere-model sign metadata (#783 PR-α) -----------------------------------
#
# Moving-head profiles carry two new keys: ``panSignFromDmx`` and
# ``tiltSignFromDmx``. Each is +1 or -1 and tells the sphere model which
# direction increasing DMX rotates the mechanics, in the FIXTURE FRAME
# (not stage frame — mount inversion lives in fixture.rotation per #780
# Principle 1).
#
# Convention:
#   panSignFromDmx = +1  → +DMX-pan rotates the yoke counter-clockwise
#                          when viewed looking down the fixture's mount-up
#                          axis. With rotation=[0,0,0] (mount upright,
#                          beam aimed along stage +Y at horizon level),
#                          +DMX-pan sweeps the beam toward stage +X.
#   tiltSignFromDmx = +1 → +DMX-tilt raises the beam relative to the
#                          fixture's mount-forward axis. With rotation
#                          =[0,0,0] this aims the beam toward stage +Z.
#
# Defaults: both +1. Profiles missing the field fall through to +1 — the
# common wiring convention. The SPA cal flow prompts the operator to
# confirm before SMART runs against an unsigned moving-head profile.


def has_sign_metadata(prof_info):
    """True when the profile carries explicit panSignFromDmx +
    tiltSignFromDmx fields (i.e. the operator / OFL import has confirmed
    them rather than the loader defaulting to +1). The SPA gates SMART
    on this for moving-head profiles to avoid blind sign assumptions."""
    if not isinstance(prof_info, dict):
        return False
    return ("panSignFromDmx" in prof_info
            and "tiltSignFromDmx" in prof_info)


# -- Lamp-on / lamp-off helpers (#749, #780 Principle 3) ----------------------
#
# "Lamp-on means lamp-on": every intensity-class channel a profile defines
# is written in lock-step. Splitting this off as a pure-buffer function makes
# the per-probe contract testable in isolation and gives non-cal callers
# (Set Home wizard, Beam-On — see #737) one place to ask for a lit beam.
#
# Intensity-class types: "dimmer", "intensity" (synonym some custom profiles
# use), and "strobe" (driven to its Open range so the shutter doesn't gate
# the beam). Some fixtures publish a master + secondary dimmer pair where
# both must be hot for the lamp to read full — channels are iterated by
# `type`, not by channel-map slot, so duplicates are honoured (the
# slymovehead profile's ch4 master + ch10 secondary case from #749).

INTENSITY_TYPES = ("dimmer", "intensity")


def _write_intensity(profile, dmx, addr, on):
    """Write every dimmer/intensity-typed channel and the strobe Open value.
    `on=True` writes 255 to dimmers; `on=False` writes 0. Strobe is held at
    its Open value in both cases — strobe is not the blackout mechanism on
    any fixture in the current corpus, and leaving it Open keeps the cal
    pipeline's `_dark_reference()` step from racing the shutter."""
    base = addr - 1
    on_value = 255 if on else 0
    strobe_open = strobe_open_value(profile)
    for ch in (profile.get("channels") or []):
        offset = ch.get("offset", 0)
        if base + offset >= 512:
            continue
        ch_type = ch.get("type", "")
        if ch_type in INTENSITY_TYPES:
            dmx[base + offset] = on_value
        elif ch_type == "strobe":
            dmx[base + offset] = strobe_open


def _write_color(profile, dmx, addr, color):
    """Write RGB / colour-wheel channels for `color = (r, g, b)`. When the
    profile carries BOTH an RGB triad and a colour wheel, the wheel is
    forced to slot 0 (open / white) so a coloured wheel-default doesn't
    filter out the RGB mix — same logic `_set_mover_dmx` relied on, hoisted
    out so a unit test can prove it without driving the whole calibrator."""
    base = addr - 1
    r, g, b = (max(0, min(255, int(v))) for v in color)
    cm = {}
    for ch in (profile.get("channels") or []):
        t = ch.get("type")
        if t and t not in cm:
            cm[t] = ch.get("offset", 0)
    if "red" in cm:
        if base + cm["red"] < 512:
            dmx[base + cm["red"]] = r
        if "green" in cm and base + cm["green"] < 512:
            dmx[base + cm["green"]] = g
        if "blue" in cm and base + cm["blue"] < 512:
            dmx[base + cm["blue"]] = b
        if "color-wheel" in cm and base + cm["color-wheel"] < 512:
            dmx[base + cm["color-wheel"]] = 0
    elif "color-wheel" in cm and base + cm["color-wheel"] < 512:
        slot = rgb_to_wheel_slot(profile, r, g, b) if (r or g or b) else 0
        dmx[base + cm["color-wheel"]] = slot


def lamp_on(profile, dmx, addr, color=None):
    """Write all intensity-class channels of `profile` to lamp-ON values
    in `dmx` at 1-based `addr`. When `color=(r, g, b)` is supplied, also
    writes RGB / colour-wheel channels using the profile's channel map.

    Pan/tilt and auxiliary channels (gobo, prism, macro, etc.) are not
    touched — the caller wires those separately around this helper."""
    if not profile:
        # Legacy 13ch slymovehead fallback shape (mirrors `_set_mover_dmx`'s
        # no-profile branch): dimmer ch3, strobe ch4, RGB ch5-7.
        base = addr - 1
        if base + 3 < 512:
            dmx[base + 3] = 255
        if base + 4 < 512:
            dmx[base + 4] = 0
        if color is not None:
            r, g, b = (max(0, min(255, int(v))) for v in color)
            if base + 5 < 512:
                dmx[base + 5] = r
            if base + 6 < 512:
                dmx[base + 6] = g
            if base + 7 < 512:
                dmx[base + 7] = b
        return
    _write_intensity(profile, dmx, addr, on=True)
    if color is not None:
        _write_color(profile, dmx, addr, color)


def lamp_off(profile, dmx, addr, color=None):
    """Write all intensity-class channels of `profile` to lamp-OFF values.
    Mirrors `lamp_on`: dimmers go to 0, strobe stays in its Open range
    (the dimmer write is the actual blackout)."""
    if not profile:
        base = addr - 1
        if base + 3 < 512:
            dmx[base + 3] = 0
        if base + 4 < 512:
            dmx[base + 4] = 0
        if color is not None:
            r, g, b = (max(0, min(255, int(v))) for v in color)
            if base + 5 < 512:
                dmx[base + 5] = r
            if base + 6 < 512:
                dmx[base + 6] = g
            if base + 7 < 512:
                dmx[base + 7] = b
        return
    _write_intensity(profile, dmx, addr, on=False)
    if color is not None:
        _write_color(profile, dmx, addr, color)


# -- Capability helper --------------------------------------------------------

def _simple_cap(label, cap_type="Intensity"):
    """Shorthand: single capability covering 0-255."""
    return [{"range": [0, 255], "type": cap_type, "label": label}]

def _color_cap(color_name):
    return [{"range": [0, 255], "type": "ColorIntensity", "label": f"{color_name} 0-100%"}]

# -- Built-in fixture profiles ------------------------------------------------

BUILTIN_PROFILES = [
    {
        "id": "generic-rgb",
        "name": "Generic RGB (3ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Red",   "type": "red",   "capabilities": _color_cap("Red")},
            {"offset": 1, "name": "Green", "type": "green", "capabilities": _color_cap("Green")},
            {"offset": 2, "name": "Blue",  "type": "blue",  "capabilities": _color_cap("Blue")},
        ],
        "channelCount": 3,
        "colorMode": "rgb",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-rgbw",
        "name": "Generic RGBW (4ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Red",   "type": "red",   "capabilities": _color_cap("Red")},
            {"offset": 1, "name": "Green", "type": "green", "capabilities": _color_cap("Green")},
            {"offset": 2, "name": "Blue",  "type": "blue",  "capabilities": _color_cap("Blue")},
            {"offset": 3, "name": "White", "type": "white", "capabilities": _color_cap("White")},
        ],
        "channelCount": 4,
        "colorMode": "rgbw",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-dimmer-rgb",
        "name": "Generic Dimmer + RGB (4ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 1, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 2, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 3, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
        ],
        "channelCount": 4,
        "colorMode": "rgb",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-dimmer-rgbw",
        "name": "Generic Dimmer + RGBW (5ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 1, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 2, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 3, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
            {"offset": 4, "name": "White",  "type": "white",  "capabilities": _color_cap("White")},
        ],
        "channelCount": 5,
        "colorMode": "rgbw",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-dimmer",
        "name": "Generic Dimmer (1ch)",
        "manufacturer": "Generic",
        "category": "other",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
        ],
        "channelCount": 1,
        "colorMode": "single",
        "beamWidth": 0,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-rgb-strobe",
        "name": "Generic RGB + Strobe (5ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 1, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 2, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 3, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
            {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Closed", "label": "Closed"},
                {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe slow-fast"},
            ]},
        ],
        "channelCount": 5,
        "colorMode": "rgb",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-moving-head",
        "name": "Generic Moving Head 8-bit (8ch)",
        "manufacturer": "Generic",
        "category": "moving-head",
        "channels": [
            {"offset": 0, "name": "Pan",    "type": "pan",    "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Pan", "label": "Pan 0-540\u00b0", "angleStart": 0, "angleEnd": 540},
            ]},
            {"offset": 1, "name": "Tilt",   "type": "tilt",   "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Tilt", "label": "Tilt 0-270\u00b0", "angleStart": 0, "angleEnd": 270},
            ]},
            {"offset": 2, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 3, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 4, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 5, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
            {"offset": 6, "name": "White",  "type": "white",  "capabilities": _color_cap("White")},
            {"offset": 7, "name": "Speed",  "type": "speed",  "capabilities": _simple_cap("P/T speed fast-slow", "Speed")},
        ],
        "channelCount": 8,
        "colorMode": "rgbw",
        "beamWidth": 15,
        "panRange": 540,
        "tiltRange": 270,
        # #783 PR-α — sphere-model sign metadata. +1 = "increasing DMX
        # rotates yoke counter-clockwise viewed from above" (pan) /
        # "increasing DMX raises the beam" (tilt). Most generic 8-bit
        # moving heads follow this convention; profiles that wire the
        # mechanics opposite override to -1.
        "panSignFromDmx": 1,
        "tiltSignFromDmx": 1,
    },
    {
        "id": "generic-moving-head-16bit",
        "name": "Generic Moving Head 16-bit (13ch)",
        "manufacturer": "Generic",
        "category": "moving-head",
        "channels": [
            {"offset": 0,  "name": "Pan",       "type": "pan",         "bits": 16, "default": 32768, "capabilities": [
                {"range": [0, 65535], "type": "Pan", "label": "Pan 0-540\u00b0", "angleStart": 0, "angleEnd": 540},
            ]},
            {"offset": 2,  "name": "Tilt",      "type": "tilt",        "bits": 16, "default": 32768, "capabilities": [
                {"range": [0, 65535], "type": "Tilt", "label": "Tilt 0-270\u00b0", "angleStart": 0, "angleEnd": 270},
            ]},
            {"offset": 4,  "name": "Speed",     "type": "speed",       "capabilities": _simple_cap("P/T speed fast-slow", "Speed")},
            {"offset": 5,  "name": "Dimmer",    "type": "dimmer",      "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 6,  "name": "Strobe",    "type": "strobe",      "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe slow-fast"},
            ]},
            {"offset": 7,  "name": "Red",       "type": "red",         "capabilities": _color_cap("Red")},
            {"offset": 8,  "name": "Green",     "type": "green",       "capabilities": _color_cap("Green")},
            {"offset": 9,  "name": "Blue",      "type": "blue",        "capabilities": _color_cap("Blue")},
            {"offset": 10, "name": "White",     "type": "white",       "capabilities": _color_cap("White")},
            {"offset": 11, "name": "Color Whl", "type": "color-wheel", "capabilities": [
                {"range": [0, 7],    "type": "WheelSlot", "label": "Open / white"},
                {"range": [8, 15],   "type": "WheelSlot", "label": "Red"},
                {"range": [16, 23],  "type": "WheelSlot", "label": "Blue"},
                {"range": [24, 31],  "type": "WheelSlot", "label": "Green"},
                {"range": [32, 39],  "type": "WheelSlot", "label": "Yellow"},
                {"range": [40, 47],  "type": "WheelSlot", "label": "Magenta"},
                {"range": [48, 55],  "type": "WheelSlot", "label": "Cyan"},
                {"range": [56, 63],  "type": "WheelSlot", "label": "Orange"},
                {"range": [64, 127], "type": "WheelSlot", "label": "Split colors"},
                {"range": [128, 255],"type": "WheelRotation", "label": "Rainbow slow-fast"},
            ]},
            {"offset": 12, "name": "Gobo",      "type": "gobo",        "capabilities": [
                {"range": [0, 7],    "type": "WheelSlot", "label": "Open"},
                {"range": [8, 15],   "type": "WheelSlot", "label": "Gobo 1"},
                {"range": [16, 23],  "type": "WheelSlot", "label": "Gobo 2"},
                {"range": [24, 31],  "type": "WheelSlot", "label": "Gobo 3"},
                {"range": [32, 39],  "type": "WheelSlot", "label": "Gobo 4"},
                {"range": [40, 47],  "type": "WheelSlot", "label": "Gobo 5"},
                {"range": [48, 55],  "type": "WheelSlot", "label": "Gobo 6"},
                {"range": [56, 63],  "type": "WheelSlot", "label": "Gobo 7"},
                {"range": [64, 127], "type": "WheelSlot", "label": "Gobo shake"},
                {"range": [128, 255],"type": "WheelRotation", "label": "Gobo scroll slow-fast"},
            ]},
        ],
        "channelCount": 13,
        "colorMode": "rgbw",
        "beamWidth": 12,
        "panRange": 540,
        "tiltRange": 270,
        "panSignFromDmx": 1,   # #783 PR-α — see generic-moving-head note
        "tiltSignFromDmx": 1,
    },
    {
        "id": "movinghead-150w-12ch",
        "name": "150W Moving Head (12ch)",
        "manufacturer": "Generic",
        "category": "moving-head",
        "channels": [
            {"offset": 0,  "name": "Pan",       "type": "pan",            "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Pan", "label": "Pan 0-540°", "angleStart": 0, "angleEnd": 540},
            ]},
            {"offset": 1,  "name": "Pan Fine",  "type": "pan-fine",       "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Pan", "label": "Pan fine trim"},
            ]},
            {"offset": 2,  "name": "Tilt",      "type": "tilt",           "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Tilt", "label": "Tilt 0-270°", "angleStart": 0, "angleEnd": 270},
            ]},
            {"offset": 3,  "name": "Tilt Fine", "type": "tilt-fine",      "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Tilt", "label": "Tilt fine trim"},
            ]},
            {"offset": 4,  "name": "P/T Speed", "type": "pan-tilt-speed", "capabilities": [
                {"range": [0, 255], "type": "PanTiltSpeed", "label": "Speed fast-slow"},
            ]},
            {"offset": 5,  "name": "Dimmer",    "type": "dimmer",         "default": 0, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 6,  "name": "Strobe",    "type": "strobe",         "capabilities": [
                {"range": [0, 0],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open (no strobe)"},
                {"range": [1, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe 1-25 Hz"},
            ]},
            {"offset": 7,  "name": "Color Whl", "type": "color-wheel",    "capabilities": [
                # #624 — rgb_to_wheel_slot() skips WheelSlot entries without
                # a `color` hex, so without these the default-mode slot
                # selection fell through to 0 (open/white) for every RGB
                # input. Added hexes matching each label.
                {"range": [0, 15],    "type": "WheelSlot",     "label": "Open / white", "color": "#FFFFFF"},
                {"range": [16, 31],   "type": "WheelSlot",     "label": "Red",          "color": "#FF0000"},
                {"range": [32, 47],   "type": "WheelSlot",     "label": "Yellow",       "color": "#FFFF00"},
                {"range": [48, 63],   "type": "WheelSlot",     "label": "Green",        "color": "#00FF00"},
                {"range": [64, 79],   "type": "WheelSlot",     "label": "Magenta",      "color": "#FF00FF"},
                {"range": [80, 95],   "type": "WheelSlot",     "label": "Blue",         "color": "#0000FF"},
                {"range": [96, 111],  "type": "WheelSlot",     "label": "Amber",        "color": "#FFBF00"},
                {"range": [112, 127], "type": "WheelSlot",     "label": "Light Blue",   "color": "#ADD8E6"},
                {"range": [128, 191], "type": "WheelRotation", "label": "CW cycle fast-slow"},
                {"range": [192, 255], "type": "WheelRotation", "label": "CCW cycle slow-fast"},
            ]},
            {"offset": 8,  "name": "Gobo",      "type": "gobo",           "capabilities": [
                {"range": [0, 7],     "type": "WheelSlot",     "label": "Open / white"},
                {"range": [8, 15],    "type": "WheelSlot",     "label": "Pattern 1"},
                {"range": [16, 23],   "type": "WheelSlot",     "label": "Pattern 2"},
                {"range": [24, 31],   "type": "WheelSlot",     "label": "Pattern 3"},
                {"range": [32, 39],   "type": "WheelSlot",     "label": "Pattern 4"},
                {"range": [40, 47],   "type": "WheelSlot",     "label": "Pattern 5"},
                {"range": [48, 55],   "type": "WheelSlot",     "label": "Pattern 6"},
                {"range": [56, 63],   "type": "WheelSlot",     "label": "Pattern 7"},
                {"range": [64, 73],   "type": "WheelShake",    "label": "Pattern 7 jitter slow-fast"},
                {"range": [74, 82],   "type": "WheelShake",    "label": "Pattern 6 jitter slow-fast"},
                {"range": [83, 91],   "type": "WheelShake",    "label": "Pattern 5 jitter slow-fast"},
                {"range": [92, 100],  "type": "WheelShake",    "label": "Pattern 4 jitter slow-fast"},
                {"range": [101, 109], "type": "WheelShake",    "label": "Pattern 3 jitter slow-fast"},
                {"range": [110, 118], "type": "WheelShake",    "label": "Pattern 2 jitter slow-fast"},
                {"range": [119, 127], "type": "WheelShake",    "label": "Pattern 1 jitter slow-fast"},
                {"range": [128, 191], "type": "WheelRotation", "label": "CW scroll fast-slow"},
                {"range": [192, 255], "type": "WheelRotation", "label": "CCW scroll slow-fast"},
            ]},
            {"offset": 9,  "name": "Prism",     "type": "prism",          "capabilities": [
                {"range": [0, 7],     "type": "Prism",         "label": "Closed"},
                {"range": [8, 127],   "type": "Prism",         "label": "Open"},
                {"range": [128, 255], "type": "PrismRotation", "label": "Rotate fast-slow"},
            ]},
            {"offset": 10, "name": "Macro",     "type": "macro",          "capabilities": [
                {"range": [0, 49],    "type": "NoFunction", "label": "DMX mode (ch 1-10 valid)"},
                {"range": [50, 200],  "type": "Effect",     "label": "Auto-run"},
                {"range": [201, 255], "type": "Effect",     "label": "Sound control"},
            ]},
            {"offset": 11, "name": "Reset",     "type": "reset",          "capabilities": [
                {"range": [0, 240],   "type": "NoFunction",  "label": "No function"},
                {"range": [241, 255], "type": "Maintenance", "label": "Lamp reset (hold 5s)"},
            ]},
        ],
        "channelCount": 12,
        "colorMode": "color-wheel",
        "beamWidth": 3,
        "panRange": 540,
        "tiltRange": 180,
        # #783 PR-α — empirical from 2026-04-29 fid 17 Home wizard:
        # operator drove panMovedDirection="right" with +10922 DMX delta
        # from home, beam swept stage-+X. With rotation=[0,0,0] that
        # matches the "+DMX → +panDeg-internal" default convention.
        "panSignFromDmx": 1,
        # Empirical: operator drove tiltMovedDirection="down" with
        # +32768 delta from home (which sat at tiltDmx16=0). +DMX moved
        # beam DOWN — the inverse of the "+DMX raises beam" default,
        # because this profile parks tilt at "all the way up" (tiltDmx16
        # = 0 = horizon-level for a ceiling mount with rotation=[0,0,0]).
        # tiltSignFromDmx still tracks the mechanics-frame convention
        # (does +DMX raise the beam relative to fixture-up?), kept as +1
        # because rotation does the inversion bookkeeping. The asymmetric
        # tilt range is parameterised via tiltUp / tiltOffsetDmx16 (#716).
        "tiltSignFromDmx": 1,
    },
    {
        "id": "generic-spot-6ch",
        "name": "Generic Spot (6ch)",
        "manufacturer": "Generic",
        "category": "spot",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 1, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 2, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 3, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
            {"offset": 4, "name": "Strobe", "type": "strobe", "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe slow-fast"},
            ]},
            {"offset": 5, "name": "Macro",  "type": "macro",  "capabilities": _simple_cap("Macro programs", "Effect")},
        ],
        "channelCount": 6,
        "colorMode": "rgb",
        "beamWidth": 10,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-wash-7ch",
        "name": "Generic Wash (7ch)",
        "manufacturer": "Generic",
        "category": "wash",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 1, "name": "Red",    "type": "red",    "capabilities": _color_cap("Red")},
            {"offset": 2, "name": "Green",  "type": "green",  "capabilities": _color_cap("Green")},
            {"offset": 3, "name": "Blue",   "type": "blue",   "capabilities": _color_cap("Blue")},
            {"offset": 4, "name": "White",  "type": "white",  "capabilities": _color_cap("White")},
            {"offset": 5, "name": "Strobe", "type": "strobe", "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe slow-fast"},
            ]},
            {"offset": 6, "name": "Zoom",   "type": "zoom",   "capabilities": _simple_cap("Zoom narrow-wide", "Zoom")},
        ],
        "channelCount": 7,
        "colorMode": "rgbw",
        "beamWidth": 40,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-fog-2ch",
        "name": "Generic Fog Machine (2ch)",
        "manufacturer": "Generic",
        "category": "fog",
        "channels": [
            {"offset": 0, "name": "Output", "type": "dimmer", "default": 0, "capabilities": _simple_cap("Fog output 0-100%")},
            {"offset": 1, "name": "Fan",    "type": "speed",  "capabilities": _simple_cap("Fan speed", "Speed")},
        ],
        "channelCount": 2,
        "colorMode": "single",
        "beamWidth": 0,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-strobe-2ch",
        "name": "Generic Strobe (2ch)",
        "manufacturer": "Generic",
        "category": "strobe",
        "channels": [
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "default": 255, "capabilities": _simple_cap("Intensity 0-100%")},
            {"offset": 1, "name": "Rate",   "type": "strobe", "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "shutterEffect": "Open",   "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "label": "Strobe rate slow-fast"},
            ]},
        ],
        "channelCount": 2,
        "colorMode": "single",
        "beamWidth": 120,
        "panRange": 0,
        "tiltRange": 0,
    },
]


# -- Profile library ----------------------------------------------------------

class ProfileLibrary:
    """Manages built-in + custom DMX fixture profiles."""

    def __init__(self, data_dir=None):
        self._profiles = {}  # id -> profile dict
        self._data_dir = data_dir
        for p in BUILTIN_PROFILES:
            self._profiles[p["id"]] = dict(p, builtin=True)
        if data_dir:
            self._load_custom(data_dir)

    def _load_custom(self, data_dir):
        """Load custom profiles from data_dir/dmx_profiles/*.json."""
        import logging
        log = logging.getLogger("slyled")
        profile_dir = Path(data_dir) / "dmx_profiles"
        if not profile_dir.is_dir():
            return
        loaded = 0
        for f in profile_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    p = json.load(fh)
                if "id" in p and "channels" in p:
                    p["builtin"] = False
                    p["channelCount"] = len(p["channels"])
                    self._profiles[p["id"]] = p
                    loaded += 1
                else:
                    log.warning("Profile %s: missing id or channels, skipped", f.name)
            except Exception as e:
                log.warning("Profile %s: load error: %s", f.name, e)
        if loaded:
            log.info("Loaded %d custom profile(s) from %s", loaded, profile_dir)

    def list_profiles(self, category=None):
        """Return all profiles, optionally filtered by category."""
        profiles = list(self._profiles.values())
        if category:
            profiles = [p for p in profiles if p.get("category") == category]
        return sorted(profiles, key=lambda p: p.get("name", ""))

    def get_profile(self, profile_id):
        """Return a profile by ID, or None. Case-insensitive fallback."""
        p = self._profiles.get(profile_id)
        if p:
            return p
        # Case-insensitive fallback
        pid_lower = profile_id.lower()
        for k, v in self._profiles.items():
            if k.lower() == pid_lower:
                return v
        return None

    def save_profile(self, profile):
        """Save a custom profile to disk and memory."""
        pid = profile.get("id")
        if not pid or not profile.get("channels"):
            return False
        profile["builtin"] = False
        profile["channelCount"] = len(profile["channels"])
        self._profiles[pid] = profile
        if self._data_dir:
            profile_dir = Path(self._data_dir) / "dmx_profiles"
            profile_dir.mkdir(parents=True, exist_ok=True)
            with open(profile_dir / f"{pid}.json", "w", encoding="utf-8") as fh:
                json.dump(profile, fh, indent=2)
        return True

    def update_profile(self, profile_id, profile):
        """Update an existing custom profile. Returns False for built-ins."""
        existing = self._profiles.get(profile_id)
        if not existing:
            return False, "Not found"
        if existing.get("builtin"):
            return False, "Cannot modify built-in profile"
        profile["id"] = profile_id
        ok, err = self.validate_profile(profile)
        if not ok:
            return False, err
        self.save_profile(profile)
        return True, None

    def delete_profile(self, profile_id):
        """Delete a custom profile. Built-ins cannot be deleted."""
        p = self._profiles.get(profile_id)
        if not p or p.get("builtin"):
            return False
        del self._profiles[profile_id]
        if self._data_dir:
            f = Path(self._data_dir) / "dmx_profiles" / f"{profile_id}.json"
            if f.exists():
                f.unlink()
        return True

    def channel_map(self, profile_id):
        """Return a type->offset dict for quick channel lookup.
        For 16-bit channels, returns the coarse offset.

        #749 — when multiple channels share the same `type` (e.g. the
        slymovehead profile has two `dimmer` channels: ch4 master at
        default=255 and ch10 secondary at default=0), the dict can only
        hold one offset. Pick the channel whose `default` is highest —
        that's the master channel by convention. Last-write-wins
        previously routed dimmer writes to the secondary slot which
        never lights the lamp.
        """
        p = self.get_profile(profile_id)
        if not p:
            return {}
        m = {}            # type -> offset
        defaults = {}     # type -> default value of the channel currently in m
        for ch in p.get("channels", []):
            t = ch.get("type")
            if not t:
                continue
            d = ch.get("default", 0) or 0
            if t not in m or d > defaults[t]:
                m[t] = ch["offset"]
                defaults[t] = d
        return m

    def channel_info(self, profile_id):
        """Return everything needed for DMX output: channel_map, channels list,
        panRange, tiltRange, beamWidth, tiltOffsetDmx16, tiltUp. Returns None
        if profile not found.

        #716 — `tiltOffsetDmx16` (default 32768 = mid-range = legacy 150W
        behaviour) and `tiltUp` (default False = legacy "increasing tilt
        beyond offset rotates beam DOWN") parameterise where horizontal-
        forward sits in the DMX range and which way increasing tilt swings
        the beam from that point. Required for fixtures like the 350W
        BeamLight whose horizontal-forward sits at DMX 4681 with positive
        tilt rotating the beam UP.
        """
        p = self.get_profile(profile_id)
        if not p:
            return None
        return {
            "channel_map": self.channel_map(profile_id),
            "channels": p.get("channels", []),
            "panRange": p.get("panRange", 0),
            "tiltRange": p.get("tiltRange", 0),
            "beamWidth": p.get("beamWidth", 0),
            # #716 — asymmetric tilt-range support.
            "tiltOffsetDmx16": p.get("tiltOffsetDmx16", 32768),
            "tiltUp": bool(p.get("tiltUp", False)),
            # #783 PR-α — sphere-model sign metadata. Default +1 matches
            # the most common wiring convention (increasing DMX rotates
            # yoke CCW from above / raises the beam relative to fixture-
            # up). Profiles missing the field fall through to +1; the
            # SPA prompts the operator to confirm on first SMART use of
            # an unsigned profile (PR-α follow-up).
            "panSignFromDmx": int(p.get("panSignFromDmx", 1)),
            "tiltSignFromDmx": int(p.get("tiltSignFromDmx", 1)),
        }

    def export_profiles(self, ids=None, category=None):
        """Export profiles as a list of dicts (without builtin flag).
        If ids given, export those specific profiles.
        If category given, export all in that category.
        If neither, export all custom profiles."""
        if ids:
            profiles = [self._profiles[pid] for pid in ids if pid in self._profiles]
        elif category:
            profiles = [p for p in self._profiles.values() if p.get("category") == category]
        else:
            profiles = [p for p in self._profiles.values() if not p.get("builtin")]
        result = []
        for p in sorted(profiles, key=lambda x: x.get("name", "")):
            out = {k: v for k, v in p.items() if k != "builtin"}
            result.append(out)
        return result

    def import_profiles(self, profiles):
        """Import a list of profile dicts. Returns {imported, skipped, errors}."""
        imported = 0
        skipped = 0
        errors = []
        for p in profiles:
            pid = p.get("id")
            if not pid:
                errors.append({"id": None, "err": "Missing id"})
                continue
            # Cannot overwrite built-ins
            existing = self._profiles.get(pid)
            if existing and existing.get("builtin"):
                skipped += 1
                continue
            ok, err = self.validate_profile(p)
            if not ok:
                errors.append({"id": pid, "err": err})
                continue
            self.save_profile(p)
            imported += 1
        return {"imported": imported, "skipped": skipped, "errors": errors}

    def validate_profile(self, profile):
        """Validate a profile dict. Returns (ok, error_message)."""
        if not isinstance(profile, dict):
            return False, "Profile must be a dict"
        if not profile.get("id"):
            return False, "Missing id"
        if not profile.get("name"):
            return False, "Missing name"
        channels = profile.get("channels")
        if not channels or not isinstance(channels, list):
            return False, "Missing or empty channels list"
        offsets = set()
        for i, ch in enumerate(channels):
            if "offset" not in ch:
                return False, f"Channel {i}: missing offset"
            if "type" not in ch:
                return False, f"Channel {i}: missing type"
            if ch["type"] not in CHANNEL_TYPES:
                return False, f"Channel {i}: unknown type '{ch['type']}'"
            if ch["offset"] in offsets:
                return False, f"Channel {i}: duplicate offset {ch['offset']}"
            offsets.add(ch["offset"])
            bits = ch.get("bits", 8)
            if bits == 16:
                offsets.add(ch["offset"] + 1)  # fine channel
            # Validate capabilities if present
            caps = ch.get("capabilities")
            if caps is not None:
                if not isinstance(caps, list) or len(caps) == 0:
                    return False, f"Channel {i}: capabilities must be a non-empty list"
                for j, cap in enumerate(caps):
                    if not isinstance(cap, dict):
                        return False, f"Channel {i} cap {j}: must be a dict"
                    rng = cap.get("range")
                    if not isinstance(rng, list) or len(rng) != 2:
                        return False, f"Channel {i} cap {j}: range must be [min, max]"
                    if rng[0] > rng[1]:
                        return False, f"Channel {i} cap {j}: range min > max"
                    if "type" not in cap:
                        return False, f"Channel {i} cap {j}: missing type"
                    if cap["type"] not in CAPABILITY_TYPES:
                        return False, f"Channel {i} cap {j}: unknown capability type '{cap['type']}'"
        # Validate emitters (optional multi-emitter support)
        emitters = profile.get("emitters")
        if emitters is not None:
            if not isinstance(emitters, list):
                return False, "emitters must be a list"
            for ei, em in enumerate(emitters):
                if not isinstance(em, dict):
                    return False, f"Emitter {ei}: must be a dict"
                if "name" not in em:
                    return False, f"Emitter {ei}: missing name"
                offset = em.get("offset", [0, 0, 0])
                if not isinstance(offset, list) or len(offset) != 3:
                    return False, f"Emitter {ei}: offset must be [x, y, z]"
        cat = profile.get("category", "other")
        if cat not in CATEGORIES:
            return False, f"Unknown category '{cat}'"
        return True, None
