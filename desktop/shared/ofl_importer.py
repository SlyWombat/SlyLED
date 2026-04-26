"""
OFL Importer — Convert Open Fixture Library JSON to SlyLED profile format.

The Open Fixture Library (open-fixture-library.org) stores ~3000+ DMX fixture
definitions. Their JSON schema uses availableChannels + modes[] with capability
ranges per channel. This module converts that to SlyLED's profile format.

Usage:
    from ofl_importer import ofl_to_slyled
    profiles = ofl_to_slyled(ofl_json)           # returns list (one per mode)
    profiles = ofl_to_slyled(ofl_json, mode=0)   # single mode
"""

import re

from dmx_profiles import CHANNEL_TYPES, CAPABILITY_TYPES

# -- OFL capability type -> SlyLED channel type mapping -----------------------

# Maps OFL capability type to SlyLED channel type.
# ColorIntensity is special — resolved by color hex.
_OFL_TYPE_MAP = {
    "Intensity":        "dimmer",
    "Pan":              "pan",
    "PanContinuous":    "pan",
    "Tilt":             "tilt",
    "TiltContinuous":   "tilt",
    "PanTiltSpeed":     "pan-tilt-speed",  # #517
    "ShutterStrobe":    "strobe",
    "StrobeSpeed":      "speed",
    "StrobeDuration":   "strobe",
    "WheelSlot":        "gobo",       # overridden to color-wheel if color wheel
    "WheelShake":       "gobo",       # #520 — shake on same wheel channel
    "WheelRotation":    "gobo-rotation",
    "WheelSlotRotation":"gobo-rotation",  # #521 — rotation indexed to a slot
    "Prism":            "prism",
    "PrismRotation":    "prism-rotation",  # #522
    "Focus":            "focus",
    "Zoom":             "zoom",
    "Frost":            "frost",
    "FrostEffect":      "frost",
    "Speed":            "speed",
    "Maintenance":      "reset",
    # #523 Iris — mechanical aperture, separate from Zoom (optics).
    "Iris":             "iris",
    "IrisEffect":       "iris",
    # #524 Blade — framing shutters on profile fixtures.
    "BladeInsertion":   "blade",
    "BladeRotation":    "blade",
    "BladeSystemRotation": "blade",
    "Fog":              "dimmer",
    "FogOutput":        "dimmer",
    "FogType":          "macro",
    "BeamAngle":        "zoom",
    "BeamPosition":     "macro",
    "ColorPreset":      "color-wheel",  # #518 — OFL preset palette channel
    "ColorTemperature": "color-temp",  # #519 — dedicated CTO / CTB channel
    "Effect":           "macro",
    "EffectSpeed":      "effect-speed",  # #525 — paired to an Effect channel
    "EffectDuration":   "macro",
    "EffectParameter":  "macro",
    "SoundSensitivity": "macro",
    "Rotation":         "rotation",  # #526 — full-body / animation wheel spin
    "NoFunction":       "macro",
    "Generic":          "macro",
}

# Map color hex (from OFL ColorIntensity) to SlyLED channel type.
_COLOR_HEX_MAP = {
    "#ff0000": "red",
    "#00ff00": "green",
    "#0000ff": "blue",
    "#ffffff": "white",
    "#ffbf00": "amber",
    "#ff8000": "amber",
    "#7b00ff": "uv",
    "#8800ff": "uv",
}

# OFL category -> SlyLED category
_CATEGORY_MAP = {
    "Moving Head":      "moving-head",
    "Color Changer":    "par",
    "Dimmer":           "other",
    "Scanner":          "spot",
    "Flower":           "other",
    "Blinder":          "strobe",
    "Strobe":           "strobe",
    "Laser":            "laser",
    "Hazer":            "fog",
    "Fan":              "fog",
    "Smoke":            "fog",
    "Effect":           "other",
    "Pixel Bar":        "wash",
    "Stand":            "other",
    "Barrel Scanner":   "spot",
}

# OFL capability type -> SlyLED capability type
_CAP_TYPE_MAP = {
    "Intensity":        "Intensity",
    "ColorIntensity":   "ColorIntensity",
    "Pan":              "Pan",
    "PanContinuous":    "PanContinuous",
    "Tilt":             "Tilt",
    "TiltContinuous":   "TiltContinuous",
    "PanTiltSpeed":     "PanTiltSpeed",    # #517
    "ShutterStrobe":    "ShutterStrobe",
    "StrobeSpeed":      "Speed",
    "StrobeDuration":   "ShutterStrobe",
    "WheelSlot":        "WheelSlot",
    "WheelShake":       "WheelShake",      # #520 — distinct from WheelSlot now
    "WheelRotation":    "WheelRotation",
    "WheelSlotRotation":"WheelSlotRotation",  # #521
    "Prism":            "Prism",
    "PrismRotation":    "PrismRotation",   # #522 — distinct from WheelRotation now
    "Focus":            "Focus",
    "Zoom":             "Zoom",
    "Frost":            "Frost",
    "FrostEffect":      "Frost",
    "Speed":            "Speed",
    "Maintenance":      "Maintenance",
    "Fog":              "Intensity",
    "FogOutput":        "Intensity",
    "FogType":          "Effect",
    "BeamAngle":        "Zoom",
    "ColorPreset":      "ColorPreset",      # #518 — distinct from WheelSlot
    "ColorTemperature": "ColorTemperature",  # #519
    "Iris":             "Iris",             # #523
    "IrisEffect":       "IrisEffect",       # #523
    "BladeInsertion":   "BladeInsertion",   # #524
    "BladeRotation":    "BladeRotation",    # #524
    "BladeSystemRotation": "BladeSystemRotation",  # #524
    "Effect":           "Effect",
    "EffectSpeed":      "EffectSpeed",      # #525
    "EffectDuration":   "Effect",
    "EffectParameter":  "Effect",
    "SoundSensitivity": "Effect",
    "Rotation":         "Rotation",         # #526
    "NoFunction":       "NoFunction",
    "Generic":          "Generic",
}


def _slugify(text):
    """Convert text to a URL-friendly slug."""
    s = text.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def _color_hex_to_type(hex_color):
    """Map OFL color hex to SlyLED channel type."""
    if not hex_color:
        return "dimmer"
    h = hex_color.lower().strip()
    if h in _COLOR_HEX_MAP:
        return _COLOR_HEX_MAP[h]
    # Approximate: check dominant channel
    try:
        r = int(h[1:3], 16)
        g = int(h[3:5], 16)
        b = int(h[5:7], 16)
    except (ValueError, IndexError):
        return "dimmer"
    if r > 200 and g < 50 and b < 50:
        return "red"
    if g > 200 and r < 50 and b < 50:
        return "green"
    if b > 200 and r < 50 and g < 50:
        return "blue"
    if r > 200 and g > 200 and b > 200:
        return "white"
    if r > 200 and g > 100 and b < 50:
        return "amber"
    if b > 150 and r > 80 and g < 30:
        return "uv"
    return "dimmer"


def _resolve_channel_type(ofl_channel):
    """Determine SlyLED channel type from OFL channel capabilities."""
    caps = ofl_channel.get("capabilities", [])
    if not caps:
        dt = ofl_channel.get("defaultValue")
        return "dimmer"

    # Use the first non-NoFunction capability to determine type
    for cap in caps:
        ct = cap.get("type", "Generic")
        if ct == "NoFunction":
            continue
        if ct == "ColorIntensity":
            color = cap.get("color")
            if isinstance(color, str):
                return _color_hex_to_type(color)
            if isinstance(color, list) and color:
                return _color_hex_to_type(color[0] if isinstance(color[0], str) else "")
            return "dimmer"
        if ct == "WheelSlot":
            # Check if it's a color wheel or gobo wheel
            slot_name = (cap.get("slotNumber", "") or cap.get("comment", "")).lower()
            wheel = ofl_channel.get("wheel", {})
            if isinstance(wheel, str) and "color" in wheel.lower():
                return "color-wheel"
            return "gobo"
        return _OFL_TYPE_MAP.get(ct, "macro")

    return "dimmer"


def _convert_capabilities(ofl_channel, is_16bit=False):
    """Convert OFL capabilities to SlyLED capabilities list."""
    ofl_caps = ofl_channel.get("capabilities", [])
    if not ofl_caps:
        max_val = 65535 if is_16bit else 255
        return [{"range": [0, max_val], "type": "Generic", "label": "Channel"}]

    result = []
    for cap in ofl_caps:
        ofl_type = cap.get("type", "Generic")
        sly_cap_type = _CAP_TYPE_MAP.get(ofl_type, "Generic")
        # Ensure cap type is valid
        if sly_cap_type not in CAPABILITY_TYPES:
            sly_cap_type = "Generic"

        dmx_range = cap.get("dmxRange", [0, 255])
        if not isinstance(dmx_range, list) or len(dmx_range) != 2:
            dmx_range = [0, 255]

        # Build label
        label = cap.get("comment", "")
        if not label:
            if ofl_type == "ColorIntensity":
                color = cap.get("color", "")
                if isinstance(color, list):
                    color = color[0] if color else ""
                label = f"Color {color}" if color else "Color intensity"
            elif ofl_type == "ShutterStrobe":
                se = cap.get("shutterEffect", "")
                label = str(se).replace("_", " ").title() if se else "Shutter"
            elif ofl_type == "WheelSlot":
                sn = cap.get("slotNumber", "")
                label = f"Slot {sn}" if sn else "Wheel slot"
            elif ofl_type == "Intensity":
                label = "Intensity 0-100%"
            elif ofl_type == "NoFunction":
                label = "No function"
            else:
                label = ofl_type.replace("_", " ")

        entry = {"range": dmx_range, "type": sly_cap_type, "label": label}

        # Preserve angle data for pan/tilt
        if ofl_type in ("Pan", "Tilt", "PanContinuous", "TiltContinuous"):
            for key in ("angleStart", "angleEnd", "angle"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("deg", "").strip())
                        except ValueError:
                            continue
                    entry[key] = val

        # #516 — preserve shutterEffect so runtime can identify Open /
        # Strobe / Closed ranges without re-guessing from the label.
        if ofl_type == "ShutterStrobe":
            se = cap.get("shutterEffect")
            if isinstance(se, str) and se:
                entry["shutterEffect"] = se

        # #519 ColorTemperature — preserve the Kelvin value (or start/end
        # pair for warm→cool ramps) so the runtime can show Kelvin in UI
        # and map a slider to Kelvin space.
        if ofl_type == "ColorTemperature":
            for key in ("colorTemperature", "colorTemperatureStart", "colorTemperatureEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("K", "").strip())
                        except ValueError:
                            continue
                    entry[key] = val

        # #520 WheelShake — preserve shakeSpeed + slotNumber so the
        # runtime can match shake ranges to their underlying gobo.
        if ofl_type == "WheelShake":
            for key in ("slotNumber", "shakeSpeed", "shakeSpeedStart",
                         "shakeSpeedEnd", "shakeAngle"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("Hz", "").replace("deg", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        # #522 PrismRotation — preserve speed/angle fields.
        if ofl_type == "PrismRotation":
            for key in ("speed", "speedStart", "speedEnd", "angle",
                         "angleStart", "angleEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("Hz", "").replace("deg", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        # #517 PanTiltSpeed — preserve speed / duration fields.
        if ofl_type == "PanTiltSpeed":
            for key in ("speed", "speedStart", "speedEnd",
                         "duration", "durationStart", "durationEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("s", "").replace("%", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        # #518 ColorPreset — preserve colors array + optional Kelvin.
        if ofl_type == "ColorPreset":
            if isinstance(cap.get("colors"), list):
                entry["colors"] = [str(c) for c in cap["colors"]]
            for key in ("colorTemperature", "colorTemperatureStart", "colorTemperatureEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("K", "").strip())
                        except ValueError:
                            continue
                    entry[key] = val

        # #521 WheelSlotRotation — slot + speed/angle ranges.
        if ofl_type == "WheelSlotRotation":
            for key in ("slotNumber", "speed", "speedStart", "speedEnd",
                         "angle", "angleStart", "angleEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("Hz", "").replace("deg", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        # #523 Iris / IrisEffect — openPercent + effect name.
        if ofl_type in ("Iris", "IrisEffect"):
            for key in ("openPercent", "openPercentStart", "openPercentEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("%", "").strip())
                        except ValueError:
                            continue
                    entry[key] = val
            if ofl_type == "IrisEffect" and isinstance(cap.get("effectName"), str):
                entry["effectName"] = cap["effectName"]

        # #524 Blade — framing shutter channels.
        if ofl_type in ("BladeInsertion", "BladeRotation", "BladeSystemRotation"):
            for key in ("bladeNumber",
                         "insertion", "insertionStart", "insertionEnd",
                         "angle", "angleStart", "angleEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("%", "").replace("deg", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        # #525 EffectSpeed — effect-speed paired to an Effect channel.
        if ofl_type == "EffectSpeed":
            for key in ("speed", "speedStart", "speedEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("Hz", "").replace("%", "").strip())
                        except ValueError:
                            continue
                    entry[key] = val

        # #526 Rotation — whole-element / animation-wheel spin.
        if ofl_type == "Rotation":
            for key in ("speed", "speedStart", "speedEnd",
                         "angle", "angleStart", "angleEnd"):
                if key in cap:
                    val = cap[key]
                    if isinstance(val, str):
                        try:
                            val = float(val.replace("Hz", "").replace("deg", "").strip())
                        except ValueError:
                            val = cap[key]
                    entry[key] = val

        result.append(entry)

    return result


def _detect_color_mode(channels):
    """Detect colorMode from channel types present."""
    types = {ch["type"] for ch in channels}
    has_r = "red" in types
    has_g = "green" in types
    has_b = "blue" in types
    has_w = "white" in types
    has_a = "amber" in types
    if has_r and has_g and has_b:
        if has_w and has_a:
            return "rgba"
        if has_w:
            return "rgbw"
        return "rgb"
    return "single"


def _map_category(ofl_categories):
    """Map OFL categories list to single SlyLED category."""
    if not ofl_categories:
        return "other"
    for ofl_cat in ofl_categories:
        if ofl_cat in _CATEGORY_MAP:
            return _CATEGORY_MAP[ofl_cat]
    # Heuristic: if any category contains certain keywords
    combined = " ".join(ofl_categories).lower()
    if "moving" in combined:
        return "moving-head"
    if "wash" in combined or "par" in combined or "bar" in combined:
        return "wash"
    if "spot" in combined or "scanner" in combined:
        return "spot"
    if "strobe" in combined or "blinder" in combined:
        return "strobe"
    if "fog" in combined or "haze" in combined or "smoke" in combined:
        return "fog"
    if "laser" in combined:
        return "laser"
    return "par"  # default for color changers etc.


def _parse_matrix(physical, mode_def):
    """Parse OFL matrix/matrixPixels into SlyLED emitters list.

    OFL stores matrix layout as:
    - physical.matrixPixels.dimensions: [cols, rows] or [cols, rows, layers]
    - physical.matrixPixels.spacing: [x_mm, y_mm] between pixels

    Returns list of emitter dicts: [{name, offset: [x_mm, y_mm, z_mm]}] or None.
    """
    mp = physical.get("matrixPixels", {})
    if not mp:
        # Check mode-level physical override
        mp = (mode_def.get("physical", {}) or {}).get("matrixPixels", {})
    if not mp:
        return None

    dims = mp.get("dimensions")
    spacing = mp.get("spacing", [30, 30])  # default 30mm spacing
    if not dims or not isinstance(dims, list) or len(dims) < 2:
        return None

    cols = int(dims[0])
    rows = int(dims[1])
    layers = int(dims[2]) if len(dims) > 2 else 1
    sx = float(spacing[0]) if len(spacing) > 0 else 30
    sy = float(spacing[1]) if len(spacing) > 1 else 30
    sz = float(spacing[2]) if len(spacing) > 2 else 0

    emitters = []
    idx = 0
    for lz in range(layers):
        for ry in range(rows):
            for cx in range(cols):
                emitters.append({
                    "name": f"Pixel {idx + 1}",
                    "offset": [round(cx * sx), round(ry * sy), round(lz * sz)],
                })
                idx += 1

    return emitters if len(emitters) > 1 else None


def ofl_to_slyled(ofl_json, mode=None):
    """Convert OFL fixture JSON to SlyLED profile(s).

    Args:
        ofl_json: Parsed OFL fixture dict.
        mode: If int, convert only that mode index. If None, convert all modes.

    Returns:
        List of SlyLED profile dicts (one per mode).
    """
    if not isinstance(ofl_json, dict):
        return []

    name = ofl_json.get("name", "Unknown Fixture")
    manufacturer = ofl_json.get("manufacturer", "Unknown")
    if isinstance(manufacturer, dict):
        manufacturer = manufacturer.get("name", "Unknown")
    ofl_categories = ofl_json.get("categories", [])
    category = _map_category(ofl_categories)

    # Physical data
    physical = ofl_json.get("physical", {})
    beam_width = 0
    pan_range = 0
    tilt_range = 0
    lens = physical.get("lens", {})
    if lens and lens.get("degreesMinMax"):
        beam_width = max(lens["degreesMinMax"])
    focus = physical.get("focus", {})
    if focus:
        pan_range = focus.get("panMax", 0) or 0
        tilt_range = focus.get("tiltMax", 0) or 0

    available_channels = ofl_json.get("availableChannels", {})
    modes = ofl_json.get("modes", [])
    if not modes:
        # Single implicit mode from all channels
        modes = [{"name": "Default", "channels": list(available_channels.keys())}]

    if mode is not None:
        if mode < 0 or mode >= len(modes):
            return []
        modes = [modes[mode]]

    results = []
    for mi, m in enumerate(modes):
        mode_name = m.get("name") or m.get("shortName") or f"Mode {mi + 1}"
        mode_channels = m.get("channels", [])

        # Mode can override physical
        mode_phys = m.get("physical", {})
        m_beam = beam_width
        m_pan = pan_range
        m_tilt = tilt_range
        if mode_phys:
            ml = mode_phys.get("lens", {})
            if ml and ml.get("degreesMinMax"):
                m_beam = max(ml["degreesMinMax"])
            mf = mode_phys.get("focus", {})
            if mf:
                m_pan = mf.get("panMax", pan_range) or pan_range
                m_tilt = mf.get("tiltMax", tilt_range) or tilt_range

        sly_channels = []
        fine_aliases = {}  # map fine channel key -> coarse channel key

        # First pass: identify fine channel aliases
        for ch_key, ch_def in available_channels.items():
            if isinstance(ch_def, dict):
                for alias in ch_def.get("fineChannelAliases", []):
                    fine_aliases[alias] = ch_key

        # Pre-walk: index of each fine-alias entry in mode_channels gives
        # its actual wire offset. OFL fixtures may place the fine channel
        # at any slot — not necessarily right after the coarse one. #689.
        fine_wire_offset = {}  # coarse_key -> wire offset of fine alias
        for idx, ch_key in enumerate(mode_channels):
            if ch_key in fine_aliases:
                coarse_key = fine_aliases[ch_key]
                fine_wire_offset[coarse_key] = idx

        # The mode list is the wire layout itself: position N == DMX offset N
        # within the fixture's address window. Emit one entry per slot
        # (skipping None / unknown / fine-alias slots, which are still
        # counted in the offset by virtue of using `idx` directly).
        for idx, ch_key in enumerate(mode_channels):
            if ch_key is None:
                continue
            # Skip fine channels here — emitted alongside their coarse
            # partner below, with the proper "pan-fine" / "tilt-fine" type.
            if ch_key in fine_aliases:
                continue

            ch_def = available_channels.get(ch_key, {})
            if not isinstance(ch_def, dict):
                continue

            is_16bit = bool(ch_def.get("fineChannelAliases"))
            ch_type = _resolve_channel_type(ch_def)
            if ch_type not in CHANNEL_TYPES:
                ch_type = "macro"

            caps = _convert_capabilities(ch_def, is_16bit)

            sly_ch = {
                "offset": idx,
                "name": ch_def.get("name", ch_key),
                "type": ch_type,
                "capabilities": caps,
            }
            if is_16bit:
                sly_ch["bits"] = 16

            sly_channels.append(sly_ch)

            # #689 — only emit an explicit pan-fine / tilt-fine entry when
            # the fine channel is NON-contiguous with its coarse partner.
            # For contiguous fixtures the existing validator already treats
            # `bits=16` as owning slots `[offset, offset+1]`; emitting a
            # separate pan-fine entry at coarse+1 trips its duplicate-offset
            # check. The compute_pan_tilt_writes helper falls back to
            # `coarse_off + 1` when no pan-fine entry exists, so contiguous
            # layouts keep working unchanged. Non-contiguous layouts (where
            # the OFL mode places fine channels somewhere other than the
            # next slot) get the explicit entry they need to route the LSB
            # correctly.
            fine_type_for = {"pan": "pan-fine", "tilt": "tilt-fine"}
            if (is_16bit and ch_type in fine_type_for
                    and ch_key in fine_wire_offset
                    and fine_wire_offset[ch_key] != idx + 1):
                sly_channels.append({
                    "offset": fine_wire_offset[ch_key],
                    "name": ch_def.get("name", ch_key) + " Fine",
                    "type": fine_type_for[ch_type],
                    "capabilities": [],
                })

        if not sly_channels:
            continue

        suffix = f" ({mode_name})" if len(modes) > 1 or mode is None else ""
        profile_name = f"{name}{suffix}"
        profile_id = _slugify(f"{manufacturer}-{name}{suffix}")

        color_mode = _detect_color_mode(sly_channels)

        profile = {
            "id": profile_id,
            "name": profile_name,
            "manufacturer": manufacturer if isinstance(manufacturer, str) else "Unknown",
            "category": category,
            "channels": sly_channels,
            "channelCount": len(sly_channels),
            "colorMode": color_mode,
            "beamWidth": m_beam,
            "panRange": m_pan,
            "tiltRange": m_tilt,
        }

        # Parse matrix/pixel layout for multi-emitter fixtures
        emitters = _parse_matrix(physical, m)
        if emitters:
            profile["emitters"] = emitters

        results.append(profile)

    return results
