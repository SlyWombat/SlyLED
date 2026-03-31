#!/usr/bin/env python3
"""
test_preview_emulation.py — Per-pixel preview emulation tests.

Validates that the bake engine emits action metadata for procedural effects
so the SPA emulator can render per-pixel colours client-side.  Also verifies
the Python-side palette / HSV maths match expected firmware output.

Usage:
    python tests/test_preview_emulation.py [host:port]

Docker:
    docker build -t slyled-test -f tests/docker/Dockerfile .
    docker run --rm --network=host slyled-test python tests/test_preview_emulation.py
"""
import json, math, os, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"

passed = 0
failed = 0
assertions = 0


def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"err": str(e)}


def ok(name, result, detail=""):
    global passed, failed, assertions
    assertions += 1
    if result:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def wait_bake(tl_id, max_wait=30):
    for _ in range(max_wait * 2):
        time.sleep(0.5)
        bs = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if bs.get("done"):
            return bs
    return bs


# ── Action type constants (must match Protocol.h / bake_engine.py) ──────────
ACT_BLACKOUT = 0
ACT_SOLID    = 1
ACT_FADE     = 2
ACT_BREATHE  = 3
ACT_CHASE    = 4
ACT_RAINBOW  = 5
ACT_FIRE     = 6
ACT_COMET    = 7
ACT_TWINKLE  = 8
ACT_STROBE   = 9
ACT_WIPE     = 10
ACT_SCANNER  = 11
ACT_SPARKLE  = 12
ACT_GRADIENT = 13

PROCEDURAL_TYPES = {
    ACT_FADE, ACT_BREATHE, ACT_CHASE, ACT_RAINBOW, ACT_FIRE,
    ACT_COMET, ACT_TWINKLE, ACT_STROBE, ACT_WIPE, ACT_SCANNER,
    ACT_SPARKLE, ACT_GRADIENT,
}

# Two batches to stay under the 16-segment-per-fixture bake cap
ACTION_BATCH_1 = [
    ("Rainbow Classic",     {"type": ACT_RAINBOW,  "speedMs": 50,  "paletteId": 0, "direction": 0}),
    ("Rainbow Ocean",       {"type": ACT_RAINBOW,  "speedMs": 80,  "paletteId": 1, "direction": 2}),
    ("Rainbow Lava",        {"type": ACT_RAINBOW,  "speedMs": 30,  "paletteId": 2, "direction": 1}),
    ("Rainbow Party",       {"type": ACT_RAINBOW,  "speedMs": 100, "paletteId": 4, "direction": 3}),
    ("Rainbow Heat",        {"type": ACT_RAINBOW,  "speedMs": 60,  "paletteId": 5, "direction": 0}),
    ("Rainbow Pastel",      {"type": ACT_RAINBOW,  "speedMs": 40,  "paletteId": 7, "direction": 0}),
    ("Chase Blue",          {"type": ACT_CHASE,    "r": 0,   "g": 100, "b": 255, "speedMs": 100, "spacing": 3, "direction": 0}),
    ("Comet White",         {"type": ACT_COMET,    "r": 255, "g": 255, "b": 255, "speedMs": 40, "tailLen": 10, "direction": 0}),
    ("Fire",                {"type": ACT_FIRE,     "r": 255, "g": 100, "b": 20, "speedMs": 30, "cooling": 55, "sparking": 120}),
    ("Solid Red",           {"type": ACT_SOLID,    "r": 255, "g": 0, "b": 0}),
    ("Blackout",            {"type": ACT_BLACKOUT}),
]

ACTION_BATCH_2 = [
    ("Twinkle Warm",        {"type": ACT_TWINKLE,  "r": 255, "g": 200, "b": 80, "spawnMs": 50, "density": 5, "fadeSpeed": 20}),
    ("Strobe",              {"type": ACT_STROBE,   "r": 255, "g": 255, "b": 255, "periodMs": 100, "dutyPct": 50}),
    ("Wipe Red",            {"type": ACT_WIPE,     "r": 255, "g": 0,   "b": 0, "speedMs": 30, "direction": 0}),
    ("Scanner",             {"type": ACT_SCANNER,  "r": 0,   "g": 255, "b": 0, "speedMs": 30, "barWidth": 3}),
    ("Sparkle",             {"type": ACT_SPARKLE,  "r": 180, "g": 180, "b": 220, "density": 3}),
    ("Fade Red-Blue",       {"type": ACT_FADE,     "r": 255, "g": 0, "b": 0, "r2": 0, "g2": 0, "b2": 255, "speedMs": 2000}),
    ("Breathe Purple",      {"type": ACT_BREATHE,  "r": 200, "g": 0, "b": 255, "periodMs": 3000, "minBri": 10}),
    ("Gradient RG",         {"type": ACT_GRADIENT, "r": 255, "g": 0, "b": 0, "r2": 0, "g2": 255, "b2": 0}),
]

ALL_ACTION_DEFS = ACTION_BATCH_1 + ACTION_BATCH_2


# ── HSV / palette helpers (mirror firmware + SPA) ───────────────────────────
def hsv_to_rgb(h, s, v):
    """FastLED-style hsv2rgb_rainbow approximation."""
    h, s, v = h & 0xFF, s & 0xFF, v & 0xFF
    inv = 255 - s
    sext = h // 43
    frac = (h - sext * 43) * 6
    if sext == 0:
        r, g, b = v, v * (255 - (s * (255 - frac) >> 8)) >> 8, v * inv >> 8
    elif sext == 1:
        r, g, b = v * (255 - (s * frac >> 8)) >> 8, v, v * inv >> 8
    elif sext == 2:
        r, g, b = v * inv >> 8, v, v * (255 - (s * (255 - frac) >> 8)) >> 8
    elif sext == 3:
        r, g, b = v * inv >> 8, v * (255 - (s * frac >> 8)) >> 8, v
    elif sext == 4:
        r, g, b = v * (255 - (s * (255 - frac) >> 8)) >> 8, v * inv >> 8, v
    else:
        r, g, b = v, v * inv >> 8, v * (255 - (s * frac >> 8)) >> 8
    return (round(r), round(g), round(b))


def pal_color(pal_id, idx):
    idx = idx & 0xFF
    if pal_id == 0:
        return hsv_to_rgb(idx, 255, 255)
    elif pal_id == 1:
        return hsv_to_rgb(((idx >> 1) + 120) & 0xFF, 200, min(255, 160 + idx // 3))
    elif pal_id == 2:
        return hsv_to_rgb((idx >> 2) & 0xFF, 255, min(255, 200 + round(math.sin(idx * math.pi / 128) * 51)))
    elif pal_id == 3:
        return hsv_to_rgb(((idx // 3) + 60) & 0xFF, 220, min(255, 100 + round(math.sin(idx * math.pi / 128) * 128)))
    elif pal_id == 4:
        return hsv_to_rgb((idx * 3) & 0xFF, 255, 255)
    elif pal_id == 5:
        if idx < 85:
            return (idx * 3, 0, 0)
        if idx < 170:
            return (255, (idx - 85) * 3, 0)
        return (255, 255, min(255, (idx - 170) * 3))
    elif pal_id == 6:
        return hsv_to_rgb(((idx >> 1) + 140) & 0xFF, 180, min(255, 180 + (idx >> 2)))
    elif pal_id == 7:
        return hsv_to_rgb(idx, 100, 255)
    return hsv_to_rgb(idx, 255, 255)


def render_rainbow_pixels(n_pixels, speed_ms, pal_id, direction, elapsed_s):
    """Simulate firmware renderRainbow for a string of n_pixels."""
    if speed_ms < 1:
        speed_ms = 1
    elapsed_ms = elapsed_s * 1000
    time_off = int(elapsed_ms / speed_ms) & 0xFF
    pixels = []
    for i in range(n_pixels):
        idx = (n_pixels - 1 - i) if direction in (2, 3) else i
        hue = (idx * 255 // n_pixels + time_off) & 0xFF
        pixels.append(pal_color(pal_id, hue))
    return pixels


# ═══════════════════════════════════════════════════════════════════════════════
def run():
    print(f"\n{'='*70}")
    print(f"  SlyLED Preview Emulation Tests — {BASE}")
    print(f"{'='*70}\n")

    # ── 1. Reset state ──────────────────────────────────────────────────────
    print("--- Setup: reset + create test fixtures ---")
    api("POST", "/api/reset", {})
    time.sleep(0.3)

    # ── 2. Create two children with different string configs ────────────────
    child_configs = [
        {"ip": "10.0.0.50", "name": "Test-50LED", "sc": 2,
         "strings": [{"leds": 50, "mm": 1000, "sdir": 0},
                     {"leds": 30, "mm": 600,  "sdir": 1}]},
        {"ip": "10.0.0.100", "name": "Test-100LED", "sc": 1,
         "strings": [{"leds": 100, "mm": 2000, "sdir": 2}]},
    ]
    child_ids = []
    for cfg in child_configs:
        r = api("POST", "/api/children", {"ip": cfg["ip"]})
        cid = r.get("id")
        child_ids.append(cid)
        ok(f"Create child {cfg['name']}", cid is not None)
        # Update strings
        api("POST", f"/api/children/{cid}", {
            "name": cfg["name"], "sc": cfg["sc"], "strings": cfg["strings"]
        })

    # ── 3. Create fixtures for each child ───────────────────────────────────
    fixture_ids = []
    for i, cid in enumerate(child_ids):
        cfg = child_configs[i]
        r = api("POST", "/api/fixtures", {
            "name": f"Fix-{i}", "type": "linear", "childId": cid,
            "strings": cfg["strings"],
        })
        fid = r.get("id")
        fixture_ids.append(fid)
        ok(f"Create fixture {i}", fid is not None)

    # ── 4. Create all action presets ────────────────────────────────────────
    print("\n--- Create action presets ---")
    action_ids = {}
    for name, params in ALL_ACTION_DEFS:
        r = api("POST", "/api/actions", {"name": name, **params})
        aid = r.get("id")
        action_ids[name] = aid
        ok(f"Action '{name}'", aid is not None)

    # ── 5–8. Bake and validate each batch ───────────────────────────────────
    tl_ids = []
    batches = [("Batch 1", ACTION_BATCH_1), ("Batch 2", ACTION_BATCH_2)]
    for batch_name, action_defs in batches:
        print(f"\n--- {batch_name}: create and bake timeline ({len(action_defs)} actions) ---")
        duration = len(action_defs) * 5
        r = api("POST", "/api/timelines", {"name": batch_name, "durationS": duration})
        tl_id = r.get("id")
        tl_ids.append(tl_id)
        ok(f"{batch_name}: create timeline", tl_id is not None)

        tracks = []
        for fi, fid in enumerate(fixture_ids):
            clips = []
            for ci, (name, _) in enumerate(action_defs):
                clips.append({
                    "actionId": action_ids[name],
                    "fixtureId": fid,
                    "startS": ci * 5,
                    "durationS": 5,
                })
            tracks.append({"fixtureId": fid, "clips": clips})

        r = api("PUT", f"/api/timelines/{tl_id}", {
            "name": batch_name, "durationS": duration, "tracks": tracks
        })
        ok(f"{batch_name}: add tracks", isinstance(r, dict) and r.get("ok"))

        r = api("POST", f"/api/timelines/{tl_id}/bake")
        ok(f"{batch_name}: start bake", r.get("ok"))
        bs = wait_bake(tl_id)
        ok(f"{batch_name}: bake completes", bs.get("done"))
        ok(f"{batch_name}: no bake errors", not bs.get("error"), bs.get("error", ""))

        print(f"\n--- {batch_name}: validate preview ---")
        preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
        ok(f"{batch_name}: preview is dict", isinstance(preview, dict))
        ok(f"{batch_name}: preview has fixtures", len(preview) > 0)

        for fid_str, frames in preview.items():
            fid = int(fid_str)
            fi = fixture_ids.index(fid) if fid in fixture_ids else -1
            n_strings = child_configs[fi]["sc"] if fi >= 0 else 1
            print(f"\n  Fixture {fid} (idx {fi}, {n_strings} strings)")

            ok(f"  Fix {fid}: has frames", len(frames) > 0)
            ok(f"  Fix {fid}: frame count ~ duration", abs(len(frames) - duration) <= 1)

            for ci, (name, params) in enumerate(action_defs):
                atype = params.get("type", 0)
                sec = ci * 5 + 2
                if sec >= len(frames):
                    continue

                frame = frames[sec]
                ok(f"  Fix {fid} @ {sec}s [{name}]: has {n_strings} strings",
                   len(frame) >= n_strings)

                for si in range(min(n_strings, len(frame))):
                    entry = frame[si]

                    if atype == ACT_BLACKOUT:
                        ok(f"    str{si} [{name}]: blackout is [0,0,0]",
                           isinstance(entry, list) and entry == [0, 0, 0])

                    elif atype == ACT_SOLID:
                        ok(f"    str{si} [{name}]: solid is [r,g,b]",
                           isinstance(entry, list) and len(entry) == 3)
                        ok(f"    str{si} [{name}]: colour matches",
                           entry == [params.get("r", 0), params.get("g", 0), params.get("b", 0)])

                    elif atype in PROCEDURAL_TYPES:
                        ok(f"    str{si} [{name}]: is action metadata",
                           isinstance(entry, dict) and "t" in entry)

                        if isinstance(entry, dict):
                            ok(f"    str{si} [{name}]: type={atype}",
                               entry.get("t") == atype)
                            ok(f"    str{si} [{name}]: has params",
                               isinstance(entry.get("p"), dict))
                            ok(f"    str{si} [{name}]: has elapsed",
                               "e" in entry and isinstance(entry["e"], (int, float)))

                            ep = entry.get("p", {})
                            if atype == ACT_RAINBOW:
                                ok(f"    str{si} [{name}]: speedMs={params['speedMs']}",
                                   ep.get("speedMs") == params["speedMs"])
                                ok(f"    str{si} [{name}]: paletteId={params['paletteId']}",
                                   ep.get("paletteId") == params["paletteId"])
                                ok(f"    str{si} [{name}]: direction={params['direction']}",
                                   ep.get("direction") == params["direction"])

                            if atype == ACT_CHASE:
                                ok(f"    str{si} [{name}]: spacing={params['spacing']}",
                                   ep.get("spacing") == params["spacing"])

                            if atype == ACT_COMET:
                                ok(f"    str{si} [{name}]: tailLen={params['tailLen']}",
                                   ep.get("tailLen") == params["tailLen"])

    # ── 9. Verify no flat-colour fallback for procedural types ──────────────
    print("\n--- Verify no flat-colour fallback across all batches ---")
    flat_found = 0
    meta_found = 0
    for batch_name, action_defs in batches:
        tl_id = tl_ids[batches.index((batch_name, action_defs))]
        preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
        for fid_str, frames in preview.items():
            for sec, frame in enumerate(frames):
                ci = sec // 5
                if ci >= len(action_defs):
                    continue
                atype = action_defs[ci][1].get("type", 0)
                for si, entry in enumerate(frame):
                    if isinstance(entry, dict) and "t" in entry:
                        meta_found += 1
                    elif isinstance(entry, list) and atype in PROCEDURAL_TYPES:
                        flat_found += 1

    ok(f"Procedural entries use metadata ({meta_found} found)", meta_found > 0)
    ok(f"No flat [r,g,b] for procedural types ({flat_found} violations)", flat_found == 0,
       f"found {flat_found} flat entries that should be metadata")

    # ── 10. Palette colour accuracy ─────────────────────────────────────────
    print("\n--- Palette colour accuracy (Python reference) ---")

    # Test that our Python palette matches expected known values
    # PAL_CLASSIC (0): hue 0 should be red-ish, hue 85 green-ish, hue 170 blue-ish
    r0 = pal_color(0, 0)
    ok("PAL_CLASSIC hue=0 is red-dominant", r0[0] > 200 and r0[1] < 50 and r0[2] < 50,
       f"got {r0}")

    g0 = pal_color(0, 85)
    ok("PAL_CLASSIC hue=85 is green-dominant", g0[1] > 150 and g0[0] < g0[1],
       f"got {g0}")

    b0 = pal_color(0, 170)
    ok("PAL_CLASSIC hue=170 is blue-dominant", b0[2] > 150 and b0[0] < b0[2],
       f"got {b0}")

    # PAL_HEAT (5): low idx → dark red, mid → orange, high → white
    h_low = pal_color(5, 30)
    ok("PAL_HEAT low=30 is dark red", h_low[0] > 0 and h_low[1] == 0 and h_low[2] == 0,
       f"got {h_low}")

    h_mid = pal_color(5, 120)
    ok("PAL_HEAT mid=120 is orange", h_mid[0] == 255 and h_mid[1] > 0 and h_mid[2] == 0,
       f"got {h_mid}")

    h_high = pal_color(5, 250)
    ok("PAL_HEAT high=250 is near-white", h_high[0] == 255 and h_high[1] == 255 and h_high[2] > 200,
       f"got {h_high}")

    # ── 10. Rainbow pixel distribution ──────────────────────────────────────
    print("\n--- Rainbow pixel distribution ---")

    # 50 pixels, classic palette, East direction, elapsed=0
    pixels = render_rainbow_pixels(50, 50, 0, 0, 0)
    ok("Rainbow 50px: all non-black", all(sum(p) > 0 for p in pixels))
    ok("Rainbow 50px: first pixel red-ish", pixels[0][0] > 200,
       f"got {pixels[0]}")
    # Pixel 25 (middle) should be roughly opposite hue from pixel 0
    mid = pixels[25]
    ok("Rainbow 50px: mid pixel differs from first",
       abs(mid[0] - pixels[0][0]) > 50 or abs(mid[1] - pixels[0][1]) > 50 or abs(mid[2] - pixels[0][2]) > 50,
       f"first={pixels[0]} mid={mid}")

    # All pixels should have unique-ish colours (no solid purple!)
    unique = len(set(pixels))
    ok("Rainbow 50px: colour variety (not solid)",
       unique > 10, f"only {unique} unique colours out of 50")

    # Direction=West should reverse
    pixels_e = render_rainbow_pixels(20, 50, 0, 0, 0)
    pixels_w = render_rainbow_pixels(20, 50, 0, 2, 0)
    ok("Rainbow direction: E vs W are reversed",
       pixels_e[0] == pixels_w[-1] and pixels_e[-1] == pixels_w[0],
       f"E[0]={pixels_e[0]} W[-1]={pixels_w[-1]}")

    # Time offset shifts colours
    pixels_t0 = render_rainbow_pixels(20, 50, 0, 0, 0)
    pixels_t5 = render_rainbow_pixels(20, 50, 0, 0, 5)
    ok("Rainbow time: 0s vs 5s differ",
       pixels_t0 != pixels_t5)

    # All palette IDs produce non-black pixels
    for pal_id in range(8):
        px = render_rainbow_pixels(30, 50, pal_id, 0, 2)
        any_lit = any(sum(p) > 0 for p in px)
        ok(f"Palette {pal_id}: produces visible pixels", any_lit)
        unique_pal = len(set(px))
        ok(f"Palette {pal_id}: colour variety > 5", unique_pal > 5,
           f"only {unique_pal} unique")

    # ── Cleanup ─────────────────────────────────────────────────────────────
    for tid in tl_ids:
        api("DELETE", f"/api/timelines/{tid}")

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  Results: {passed} passed, {failed} failed, {assertions} assertions")
    print(f"{'='*70}\n")
    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
