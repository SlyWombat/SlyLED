"""
DMX Fixture Profile System — JSON definitions, channel mapping, local library.

Provides built-in profiles for common fixture types and supports custom
user-defined profiles stored in the data directory.

Profile schema:
{
  "id": "generic-rgb-par",
  "name": "Generic RGB Par",
  "manufacturer": "Generic",
  "category": "par",          # par | wash | spot | moving-head | strobe | fog | laser | other
  "channels": [
    {"offset": 0, "name": "Red",   "type": "red"},
    {"offset": 1, "name": "Green", "type": "green"},
    {"offset": 2, "name": "Blue",  "type": "blue"}
  ],
  "channelCount": 3,
  "colorMode": "rgb",          # rgb | cmy | rgbw | rgba | single
  "beamWidth": 25,             # degrees (0 = wash/flood)
  "panRange": 0,               # degrees (0 = no pan)
  "tiltRange": 0,              # degrees (0 = no tilt)
}

Channel types: dimmer, red, green, blue, white, amber, uv, pan, pan-fine,
               tilt, tilt-fine, strobe, gobo, gobo-rotation, prism, focus,
               zoom, frost, color-wheel, speed, macro, reset
"""

import json
import os
from pathlib import Path

# ── Built-in fixture profiles ────────────────────────────────────────────────

BUILTIN_PROFILES = [
    {
        "id": "generic-rgb",
        "name": "Generic RGB (3ch)",
        "manufacturer": "Generic",
        "category": "par",
        "channels": [
            {"offset": 0, "name": "Red",   "type": "red"},
            {"offset": 1, "name": "Green", "type": "green"},
            {"offset": 2, "name": "Blue",  "type": "blue"},
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
            {"offset": 0, "name": "Red",   "type": "red"},
            {"offset": 1, "name": "Green", "type": "green"},
            {"offset": 2, "name": "Blue",  "type": "blue"},
            {"offset": 3, "name": "White", "type": "white"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Red",    "type": "red"},
            {"offset": 2, "name": "Green",  "type": "green"},
            {"offset": 3, "name": "Blue",   "type": "blue"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Red",    "type": "red"},
            {"offset": 2, "name": "Green",  "type": "green"},
            {"offset": 3, "name": "Blue",   "type": "blue"},
            {"offset": 4, "name": "White",  "type": "white"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Red",    "type": "red"},
            {"offset": 2, "name": "Green",  "type": "green"},
            {"offset": 3, "name": "Blue",   "type": "blue"},
            {"offset": 4, "name": "Strobe", "type": "strobe"},
        ],
        "channelCount": 5,
        "colorMode": "rgb",
        "beamWidth": 25,
        "panRange": 0,
        "tiltRange": 0,
    },
    {
        "id": "generic-moving-head-8ch",
        "name": "Generic Moving Head 8-bit (8ch)",
        "manufacturer": "Generic",
        "category": "moving-head",
        "channels": [
            {"offset": 0, "name": "Pan",    "type": "pan"},
            {"offset": 1, "name": "Tilt",   "type": "tilt"},
            {"offset": 2, "name": "Dimmer", "type": "dimmer"},
            {"offset": 3, "name": "Red",    "type": "red"},
            {"offset": 4, "name": "Green",  "type": "green"},
            {"offset": 5, "name": "Blue",   "type": "blue"},
            {"offset": 6, "name": "White",  "type": "white"},
            {"offset": 7, "name": "Speed",  "type": "speed"},
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
            {"offset": 0,  "name": "Pan",       "type": "pan",       "bits": 16},
            {"offset": 2,  "name": "Tilt",      "type": "tilt",      "bits": 16},
            {"offset": 4,  "name": "Speed",     "type": "speed"},
            {"offset": 5,  "name": "Dimmer",    "type": "dimmer"},
            {"offset": 6,  "name": "Strobe",    "type": "strobe"},
            {"offset": 7,  "name": "Red",       "type": "red"},
            {"offset": 8,  "name": "Green",     "type": "green"},
            {"offset": 9,  "name": "Blue",      "type": "blue"},
            {"offset": 10, "name": "White",     "type": "white"},
            {"offset": 11, "name": "Color Whl", "type": "color-wheel"},
            {"offset": 12, "name": "Gobo",      "type": "gobo"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Red",    "type": "red"},
            {"offset": 2, "name": "Green",  "type": "green"},
            {"offset": 3, "name": "Blue",   "type": "blue"},
            {"offset": 4, "name": "Strobe", "type": "strobe"},
            {"offset": 5, "name": "Macro",  "type": "macro"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Red",    "type": "red"},
            {"offset": 2, "name": "Green",  "type": "green"},
            {"offset": 3, "name": "Blue",   "type": "blue"},
            {"offset": 4, "name": "White",  "type": "white"},
            {"offset": 5, "name": "Strobe", "type": "strobe"},
            {"offset": 6, "name": "Zoom",   "type": "zoom"},
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
            {"offset": 0, "name": "Output", "type": "dimmer"},
            {"offset": 1, "name": "Fan",    "type": "speed"},
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
            {"offset": 0, "name": "Dimmer", "type": "dimmer"},
            {"offset": 1, "name": "Rate",   "type": "strobe"},
        ],
        "channelCount": 2,
        "colorMode": "single",
        "beamWidth": 120,
        "panRange": 0,
        "tiltRange": 0,
    },
]

# ── Valid channel types ──────────────────────────────────────────────────────

CHANNEL_TYPES = {
    "dimmer", "red", "green", "blue", "white", "amber", "uv",
    "pan", "pan-fine", "tilt", "tilt-fine",
    "strobe", "gobo", "gobo-rotation", "prism", "focus", "zoom", "frost",
    "color-wheel", "speed", "macro", "reset",
}

CATEGORIES = {"par", "wash", "spot", "moving-head", "strobe", "fog", "laser", "other"}


# ── Profile library ──────────────────────────────────────────────────────────

class ProfileLibrary:
    """Manages built-in + custom DMX fixture profiles."""

    def __init__(self, data_dir=None):
        self._profiles = {}  # id → profile dict
        self._data_dir = data_dir
        # Load built-ins
        for p in BUILTIN_PROFILES:
            self._profiles[p["id"]] = dict(p, builtin=True)
        # Load custom from disk
        if data_dir:
            self._load_custom(data_dir)

    def _load_custom(self, data_dir):
        """Load custom profiles from data_dir/dmx_profiles/*.json."""
        profile_dir = Path(data_dir) / "dmx_profiles"
        if not profile_dir.is_dir():
            return
        for f in profile_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    p = json.load(fh)
                if "id" in p and "channels" in p:
                    p["builtin"] = False
                    p["channelCount"] = len(p["channels"])
                    self._profiles[p["id"]] = p
            except Exception:
                pass  # skip invalid files

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
        """Return a type→offset dict for quick channel lookup.
        For 16-bit channels, returns the coarse offset."""
        p = self.get_profile(profile_id)
        if not p:
            return {}
        m = {}
        for ch in p.get("channels", []):
            m[ch["type"]] = ch["offset"]
        return m

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
        cat = profile.get("category", "other")
        if cat not in CATEGORIES:
            return False, f"Unknown category '{cat}'"
        return True, None
