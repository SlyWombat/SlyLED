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

# ── WLED effect ID mapping ───────────────────────────────────────────────────
# Maps SlyLED action types to WLED effect IDs (WLED 0.14.x+)

WLED_EFFECT_MAP = {
    0: None,       # Blackout → turn off
    1: 0,          # Solid → Solid (fx=0)
    2: 0,          # Fade → Solid + transition
    3: 2,          # Breathe → Breathe (fx=2)
    4: 28,         # Chase → Chase (fx=28)
    5: 9,          # Rainbow → Rainbow (fx=9)
    6: 66,         # Fire → Fire 2012 (fx=66)
    7: 75,         # Comet → Comet (fx=75)
    8: 74,         # Twinkle → Twinkle (fx=74)
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
        return {
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
    except Exception:
        return None


def wled_get_state(ip, timeout=2.0):
    """Get current WLED state. Returns dict or None."""
    try:
        req = urllib.request.Request(f"http://{ip}/json/state", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
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
    except Exception:
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
    # SlyLED: 30ms=very fast, 3000ms=very slow
    # WLED: 255=fastest, 0=slowest
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
    if t == 2:  # Fade — use transition between two colors
        r2 = act.get("r2", act.get("p8a", 0))
        g2 = act.get("g2", act.get("p8b", 0))
        b2 = act.get("b2", act.get("p8c", 0))
        seg["col"] = [[r, g, b], [r2, g2, b2]]
        # Use WLED's transition for crossfade
        transition = max(1, int((speed_ms or 1000) / 100))
        return {"on": True, "transition": transition, "seg": [seg]}

    if t == 3:  # Breathe — map minBri to intensity
        min_bri = act.get("minBri", act.get("p8a", 0))
        seg["ix"] = max(0, min(255, 255 - min_bri * 255 // 100))

    if t == 4:  # Chase — map spacing and direction
        seg["ix"] = act.get("spacing", act.get("p8a", 3)) * 30
        direction = act.get("direction", act.get("p8c", 0))
        seg["rev"] = direction in (2, 3)  # West or South = reverse

    if t == 5:  # Rainbow — map palette
        pal_id = act.get("paletteId", act.get("p8a", 0))
        # Map SlyLED palette IDs to WLED palette IDs (approximate)
        pal_map = {0: 0, 1: 6, 2: 35, 3: 10, 4: 11, 5: 35, 6: 14, 7: 13}
        seg["pal"] = pal_map.get(pal_id, 0)
        direction = act.get("direction", act.get("p8c", 0))
        seg["rev"] = direction in (2, 3)

    if t == 6:  # Fire — map cooling and sparking
        cooling = act.get("cooling", act.get("p8a", 55))
        sparking = act.get("sparking", act.get("p8b", 120))
        seg["sx"] = max(0, min(255, cooling * 2))
        seg["ix"] = max(0, min(255, sparking))

    if t == 7:  # Comet — map tail and direction
        seg["ix"] = act.get("tailLen", act.get("p8a", 10)) * 20
        direction = act.get("direction", act.get("p8c", 0))
        seg["rev"] = direction in (2, 3)

    if t == 8:  # Twinkle — map density and fade
        seg["ix"] = act.get("density", act.get("p8a", 3)) * 30

    return {"on": True, "bri": 255, "seg": [seg]}


def wled_map_step(step, brightness=255):
    """Convert a resolved runner step to a WLED state payload."""
    state = wled_map_action(step)
    if state.get("on"):
        state["bri"] = brightness
    return state
