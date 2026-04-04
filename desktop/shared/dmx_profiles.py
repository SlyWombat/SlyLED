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
}

Channel types (primary function): dimmer, red, green, blue, white, amber, uv,
    pan, pan-fine, tilt, tilt-fine, strobe, gobo, gobo-rotation, prism, focus,
    zoom, frost, color-wheel, speed, macro, reset

Capability types: ColorIntensity, Intensity, Pan, PanContinuous, Tilt,
    TiltContinuous, ShutterStrobe, WheelSlot, WheelRotation, Prism, Focus,
    Zoom, Frost, Speed, Maintenance, Effect, NoFunction, Generic
"""

import json
import os
from pathlib import Path

# -- Valid sets ---------------------------------------------------------------

CHANNEL_TYPES = {
    "dimmer", "red", "green", "blue", "white", "amber", "uv",
    "pan", "pan-fine", "tilt", "tilt-fine",
    "strobe", "gobo", "gobo-rotation", "prism", "focus", "zoom", "frost",
    "color-wheel", "speed", "macro", "reset",
}

CAPABILITY_TYPES = {
    "ColorIntensity", "Intensity", "Pan", "PanContinuous",
    "Tilt", "TiltContinuous", "ShutterStrobe", "WheelSlot",
    "WheelRotation", "Prism", "Focus", "Zoom", "Frost",
    "Speed", "Maintenance", "Effect", "NoFunction", "Generic",
}

CATEGORIES = {"par", "wash", "spot", "moving-head", "strobe", "fog", "laser", "other"}

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
                {"range": [0, 3],   "type": "ShutterStrobe", "label": "Closed"},
                {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
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
            {"offset": 5,  "name": "Dimmer",    "type": "dimmer",      "capabilities": _simple_cap("Dimmer 0-100%")},
            {"offset": 6,  "name": "Strobe",    "type": "strobe",      "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
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
                {"range": [0, 3],   "type": "ShutterStrobe", "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
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
                {"range": [0, 3],   "type": "ShutterStrobe", "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe slow-fast"},
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
            {"offset": 0, "name": "Output", "type": "dimmer", "capabilities": _simple_cap("Fog output 0-100%")},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer", "capabilities": _simple_cap("Intensity 0-100%")},
            {"offset": 1, "name": "Rate",   "type": "strobe", "capabilities": [
                {"range": [0, 3],   "type": "ShutterStrobe", "label": "Open"},
                {"range": [4, 255], "type": "ShutterStrobe", "label": "Strobe rate slow-fast"},
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
        """Return a profile by ID, or None."""
        return self._profiles.get(profile_id)

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
        For 16-bit channels, returns the coarse offset."""
        p = self.get_profile(profile_id)
        if not p:
            return {}
        m = {}
        for ch in p.get("channels", []):
            m[ch["type"]] = ch["offset"]
        return m

    def channel_info(self, profile_id):
        """Return everything needed for DMX output: channel_map, channels list,
        panRange, tiltRange, beamWidth. Returns None if profile not found."""
        p = self.get_profile(profile_id)
        if not p:
            return None
        return {
            "channel_map": self.channel_map(profile_id),
            "channels": p.get("channels", []),
            "panRange": p.get("panRange", 0),
            "tiltRange": p.get("tiltRange", 0),
            "beamWidth": p.get("beamWidth", 0),
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
        cat = profile.get("category", "other")
        if cat not in CATEGORIES:
            return False, f"Unknown category '{cat}'"
        return True, None
