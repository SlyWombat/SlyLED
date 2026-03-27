"""
wled_bridge.py — WLED device communication for the SlyLED orchestrator.

Handles discovery, status polling, and action translation for WLED devices
via their HTTP JSON API (http://<ip>/json/*).
"""

import json
import urllib.request
import urllib.error
import logging

log = logging.getLogger("slyled")

# ── WLED effect ID mapping (WLED 0.14.x+) ────────────────────────────────────
# Maps SlyLED action types to WLED effect IDs.
# These are best-effort defaults; the authoritative list comes from
# GET /json/effects on the device. Use wledFxOverride to pick any native effect.

WLED_EFFECT_MAP = {
    0: None,       # Blackout → turn off
    1: 0,          # Solid → Solid (fx=0)
    2: 112,        # Fade → Blends (fx=112) — smooth crossfade between colors
    3: 2,          # Breathe → Breathe (fx=2)
    4: 28,         # Chase → Chase (fx=28)
    5: 9,          # Rainbow → Rainbow (fx=9)
    6: 66,         # Fire → Fire 2012 (fx=66)
    7: 75,         # Comet → Meteor (fx=75)
    8: 74,         # Twinkle → Twinkling (fx=74)
    9: 15,         # Strobe → Strobe (fx=15)
    10: 22,        # Color Wipe → Color Wipe (fx=22)
    11: 10,        # Scanner → Scan (fx=10)
    12: 82,        # Sparkle → Sparkle (fx=82)
    13: 46,        # Gradient → Gradient (fx=46)
}

# ── SlyLED → WLED palette ID mapping ─────────────────────────────────────────
# SlyLED palette IDs (0-7) to WLED palette IDs (approximate matches).

WLED_PALETTE_MAP = {
    0: 0,   # Classic → Default
    1: 6,   # Ocean → Ocean
    2: 35,  # Lava → Lava
    3: 10,  # Forest → Forest
    4: 11,  # Party → Party
    5: 35,  # Heat → Lava (closest)
    6: 14,  # Cool → Breeze
    7: 13,  # Pastel → Pastel
}


def wled_probe(ip, timeout=2.0):
    """Probe an IP for a WLED device. Returns info dict or None."""
    try:
        req = urllib.request.Request(f"http://{ip}/json/info", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        info = json.loads(resp.read().decode("utf-8"))
        if not info.get("ver") or not info.get("leds"):
            return None
        leds = info["leds"]
        result = {
            "name": info.get("name", f"WLED-{ip}"),
            "ver": info.get("ver", "?"),
            "ledCount": leds.get("count", 0),
            "ip": ip,
            "mac": info.get("mac", ""),
            "arch": info.get("arch", ""),
            "effectCount": info.get("fxcount", 0),
            "paletteCount": info.get("palcount", 0),
            "brand": info.get("brand", "WLED"),
            "uptime": info.get("uptime", 0),
        }
        # Fetch segment info from state
        state = wled_get_state(ip, timeout)
        if state and "seg" in state:
            result["segments"] = [
                {"id": s.get("id", i), "start": s.get("start", 0),
                 "stop": s.get("stop", 0), "len": s.get("len", 0)}
                for i, s in enumerate(state["seg"])
            ]
        return result
    except Exception as e:
        log.debug("wled_probe(%s) failed: %s", ip, e)
        return None


def wled_get_state(ip, timeout=2.0):
    """Get current WLED state. Returns dict or None."""
    try:
        req = urllib.request.Request(f"http://{ip}/json/state", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug("wled_get_state(%s) failed: %s", ip, e)
        return None


def wled_set_state(ip, state, timeout=2.0):
    """POST state to WLED device. Returns True on success."""
    try:
        data = json.dumps(state).encode("utf-8")
        req = urllib.request.Request(
            f"http://{ip}/json/state", data=data, method="POST",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception as e:
        log.debug("wled_set_state(%s) failed: %s", ip, e)
        return False


def wled_get_effects(ip, timeout=2.0):
    """Get list of effect names from WLED device."""
    try:
        req = urllib.request.Request(f"http://{ip}/json/effects", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug("wled_get_effects(%s) failed: %s", ip, e)
        return None


def wled_get_palettes(ip, timeout=2.0):
    """Get list of palette names from WLED device."""
    try:
        req = urllib.request.Request(f"http://{ip}/json/palettes", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug("wled_get_palettes(%s) failed: %s", ip, e)
        return None


def wled_get_segments(ip, timeout=2.0):
    """Get segment list from WLED device state."""
    try:
        state = wled_get_state(ip, timeout)
        if state and "seg" in state:
            return [{"id": s.get("id", i), "start": s.get("start", 0),
                     "stop": s.get("stop", 0), "len": s.get("len", 0)}
                    for i, s in enumerate(state["seg"])]
        return None
    except Exception as e:
        log.debug("wled_get_segments(%s) failed: %s", ip, e)
        return None


def wled_stop(ip):
    """Turn off WLED device."""
    return wled_set_state(ip, {"on": False})


def wled_map_action(act):
    """Convert a SlyLED action dict to a WLED /json/state payload."""
    t = act.get("type", 0)
    r = act.get("r", 0)
    g = act.get("g", 0)
    b = act.get("b", 0)

    # Blackout
    if t == 0:
        return {"on": False}

    fx = WLED_EFFECT_MAP.get(t, 0)
    speed_ms = act.get("speedMs", act.get("periodMs", act.get("spawnMs", 500)))

    # Map SlyLED speed (ms, lower=faster) to WLED speed (0-255, higher=faster)
    if speed_ms and speed_ms > 0:
        wled_speed = max(0, min(255, 255 - int(speed_ms * 255 / 5000)))
    else:
        wled_speed = 128

    seg = {
        "col": [[r, g, b]],
        "fx": fx,
        "sx": wled_speed,
    }

    # Type-specific parameter mapping
    if t == 2:  # Fade — Blends effect with two colors
        r2 = act.get("r2", 0)
        g2 = act.get("g2", 0)
        b2 = act.get("b2", 0)
        seg["col"] = [[r, g, b], [r2, g2, b2]]

    if t == 3:  # Breathe — map minBri to intensity
        min_bri = act.get("minBri", 0)
        seg["ix"] = max(0, min(255, 255 - min_bri * 255 // 100))

    if t == 4:  # Chase — map spacing and direction
        seg["ix"] = act.get("spacing", 3) * 30
        direction = act.get("direction", 0)
        seg["rev"] = direction in (2, 3)
        if act.get("paletteId") is not None:
            seg["pal"] = WLED_PALETTE_MAP.get(act["paletteId"], 0)

    if t == 5:  # Rainbow — map palette and direction
        pal_id = act.get("paletteId", 0)
        seg["pal"] = WLED_PALETTE_MAP.get(pal_id, 0)
        direction = act.get("direction", 0)
        seg["rev"] = direction in (2, 3)

    if t == 6:  # Fire — map cooling and sparking
        cooling = act.get("cooling", 55)
        sparking = act.get("sparking", 120)
        seg["sx"] = max(0, min(255, cooling * 2))
        seg["ix"] = max(0, min(255, sparking))
        if act.get("paletteId") is not None:
            seg["pal"] = WLED_PALETTE_MAP.get(act["paletteId"], 0)

    if t == 7:  # Comet — map tail and direction
        seg["ix"] = act.get("tailLen", 10) * 20
        direction = act.get("direction", 0)
        seg["rev"] = direction in (2, 3)

    if t == 8:  # Twinkle — map density and fade
        seg["ix"] = act.get("density", 3) * 30

    if t == 9:  # Strobe — map on/off timing to speed and duty
        on_ms = act.get("onMs", 50)
        off_ms = act.get("offMs", 450)
        total = on_ms + off_ms
        seg["sx"] = max(0, min(255, 255 - int(total * 255 / 2000))) if total > 0 else 128
        duty_pct = (on_ms * 100 // total) if total > 0 else 50
        seg["ix"] = max(0, min(255, duty_pct * 255 // 100))

    if t == 10:  # Color Wipe — map direction
        direction = act.get("direction", 0)
        seg["rev"] = direction in (2, 3)
        if act.get("paletteId") is not None:
            seg["pal"] = WLED_PALETTE_MAP.get(act["paletteId"], 0)

    if t == 11:  # Scanner — map bar width to intensity
        bar_w = act.get("tailLen", 3)
        seg["ix"] = max(0, min(255, bar_w * 30))

    if t == 12:  # Sparkle — density to intensity
        seg["ix"] = act.get("density", 3) * 30

    if t == 13:  # Gradient — second color + palette
        r2 = act.get("r2", 0)
        g2 = act.get("g2", 0)
        b2 = act.get("b2", 0)
        seg["col"] = [[r, g, b], [r2, g2, b2]]
        if act.get("paletteId") is not None:
            seg["pal"] = WLED_PALETTE_MAP.get(act["paletteId"], 0)

    # Apply WLED overrides (from action editor) — take precedence over auto-mapping
    if act.get("wledFxOverride") is not None:
        seg["fx"] = act["wledFxOverride"]
    if act.get("wledPalOverride") is not None:
        seg["pal"] = act["wledPalOverride"]

    # Segment targeting
    seg_id = act.get("wledSegId")
    if seg_id is not None:
        seg["id"] = seg_id

    return {"on": True, "bri": 255, "seg": [seg]}


def wled_map_step(step, brightness=255):
    """Convert a resolved runner step to a WLED state payload."""
    state = wled_map_action(step)
    if state.get("on"):
        state["bri"] = brightness
    return state
