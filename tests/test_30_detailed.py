#!/usr/bin/env python3
"""
SlyLED 30-Combination Detailed Analysis — validates the runtime preview emulator.
Counts LEDs lit from PREVIEW DATA (what the user sees on screen), not segment params.

Usage: python tests/test_30_detailed.py [host:port]
"""
import json, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"
P = 0; F = 0; ISSUES = []

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"err": str(e)}

def ok(name, result):
    global P, F
    if result: P += 1
    else: F += 1; ISSUES.append(name)

# ── Setup ─────────────────────────────────────────────────────────────────
print("Setting up synthetic test fixtures with varied string configs...")

# Delete existing fixtures to start clean
for f in (api("GET", "/api/fixtures") or []):
    if isinstance(f, dict): api("DELETE", f"/api/fixtures/{f['id']}")

# Create 8 synthetic fixtures with diverse multi-string configurations
# These represent what different real children would look like
SYNTH_FIXTURES = [
    {"name": "F1-East100",      "x": 250,  "y": 1000, "strings": [{"leds": 100, "mm": 1600, "sdir": 0}]},
    {"name": "F2-DualEW",       "x": 1000, "y": 1000, "strings": [{"leds": 150, "mm": 2400, "sdir": 2}, {"leds": 150, "mm": 2400, "sdir": 0}]},
    {"name": "F3-North120",     "x": 1800, "y": 500,  "strings": [{"leds": 120, "mm": 1920, "sdir": 1}]},
    {"name": "F4-DualNS",       "x": 2500, "y": 1500, "strings": [{"leds": 80, "mm": 1280, "sdir": 1}, {"leds": 80, "mm": 1280, "sdir": 3}]},
    {"name": "F5-TriENW",       "x": 500,  "y": 1800, "strings": [{"leds": 50, "mm": 800, "sdir": 0}, {"leds": 50, "mm": 800, "sdir": 1}, {"leds": 50, "mm": 800, "sdir": 2}]},
    {"name": "F6-Long200E",     "x": 3000, "y": 1000, "strings": [{"leds": 200, "mm": 3200, "sdir": 0}]},
    {"name": "F7-QuadENWS",     "x": 2000, "y": 1000, "strings": [{"leds": 30, "mm": 480, "sdir": 0}, {"leds": 30, "mm": 480, "sdir": 1}, {"leds": 30, "mm": 480, "sdir": 2}, {"leds": 30, "mm": 480, "sdir": 3}]},
    {"name": "F8-West80",       "x": 3500, "y": 500,  "strings": [{"leds": 80, "mm": 1280, "sdir": 2}]},
]

fix_ids = []
fix_leds = {}
total_stage_leds = 0
for sf in SYNTH_FIXTURES:
    r = api("POST", "/api/fixtures", {"name": sf["name"], "type": "linear", "strings": sf["strings"]})
    fid = r.get("id")
    fix_ids.append(fid)
    string_leds = [s["leds"] for s in sf["strings"]]
    fix_leds[str(fid)] = string_leds
    total_stage_leds += sum(string_leds)

# We need matching "children" in the layout for the fixtures to resolve pixel positions
# Use existing children or create layout entries that map to fixture positions
children = api("GET", "/api/children") or []
# Create layout entries — use fixture positions directly since fixtures have their own strings
positions = []
for i, sf in enumerate(SYNTH_FIXTURES):
    # Map fixture to a child if available, otherwise just use the fixture position
    cid = children[i]["id"] if i < len(children) else i + 100
    positions.append({"id": cid, "x": sf["x"], "y": sf["y"], "z": 0})
    # Update fixture childId to match
    if fix_ids[i]:
        api("PUT", f"/api/fixtures/{fix_ids[i]}", {"childId": cid if i < len(children) else None})

api("POST", "/api/layout", {"children": positions})

# For fixtures without a real child, the strings field in the fixture itself provides LED data
# The bake uses fixture.strings when present

fixtures = api("GET", "/api/fixtures") or []
print(f"  {len(fixtures)} fixtures, {total_stage_leds} total LEDs")
for f in fixtures:
    strs = f.get("strings", [])
    desc = " + ".join(f"{s['leds']}{['E','N','W','S'][s.get('sdir',0)]}" for s in strs)
    print(f"    #{f['id']} {f['name']}: {desc} ({sum(s['leds'] for s in strs)} LEDs)")

# Create actions + effects
acts = {}
for name, t, p in [
    ("Red",1,{"r":255,"g":0,"b":0}), ("Blue",1,{"r":0,"g":0,"b":255}),
    ("Green",1,{"r":0,"g":255,"b":0}), ("Chase",4,{"r":0,"g":255,"b":255,"speedMs":150,"spacing":3,"direction":0}),
    ("ChaseRev",4,{"r":255,"g":128,"b":0,"speedMs":100,"spacing":4,"direction":2}),
    ("Rainbow",5,{"speedMs":200,"paletteId":0,"direction":0}),
    ("RainbowSlow",5,{"speedMs":1000,"paletteId":3,"direction":2}),
    ("Fire",6,{"r":255,"g":60,"b":0,"speedMs":30,"cooling":55,"sparking":120}),
    ("CometE",7,{"r":0,"g":200,"b":255,"speedMs":40,"tailLen":12,"direction":0,"decay":80}),
    ("CometW",7,{"r":255,"g":0,"b":200,"speedMs":60,"tailLen":8,"direction":2,"decay":60}),
    ("Twinkle",8,{"r":255,"g":255,"b":255,"spawnMs":80,"density":5,"fadeSpeed":15}),
    ("Strobe",9,{"r":255,"g":255,"b":255,"periodMs":150,"p8a":50}),
    ("Wipe",10,{"r":255,"g":0,"b":128,"speedMs":30,"direction":0}),
    ("Breathe",3,{"r":200,"g":100,"b":255,"periodMs":3000,"minBri":20}),
    ("Gradient",13,{"r":255,"g":0,"b":0,"r2":0,"g2":0,"b2":255}),
]:
    r = api("POST", "/api/actions", {"name": f"D-{name}", "type": t, **p})
    acts[name] = r.get("id")

efx = {}
for name, cfg in [
    ("SphR", {"shape":"sphere","r":255,"g":50,"b":0,"size":{"radius":800},
              "motion":{"startPos":[0,1000,0],"endPos":[4000,1000,0],"durationS":15,"easing":"linear"}}),
    ("SphL", {"shape":"sphere","r":0,"g":100,"b":255,"size":{"radius":600},
              "motion":{"startPos":[4000,1000,0],"endPos":[0,1000,0],"durationS":10,"easing":"ease-in-out"}}),
    ("SphU", {"shape":"sphere","r":0,"g":255,"b":100,"size":{"radius":1000},
              "motion":{"startPos":[2000,0,0],"endPos":[2000,3000,0],"durationS":12,"easing":"ease-out"}}),
    ("PlnR", {"shape":"plane","r":200,"g":0,"b":255,"size":{"normal":[1,0,0],"thickness":500},
              "motion":{"startPos":[0,1000,0],"endPos":[4000,1000,0],"durationS":8,"easing":"linear"}}),
    ("PlnD", {"shape":"plane","r":255,"g":200,"b":0,"size":{"normal":[0,1,0],"thickness":400},
              "motion":{"startPos":[2000,3000,0],"endPos":[2000,0,0],"durationS":10,"easing":"ease-in"}}),
    ("Box",  {"shape":"box","r":128,"g":0,"b":255,"size":{"width":2000,"height":2000,"depth":2000},
              "motion":{"startPos":[2000,1000,0],"endPos":[2000,1000,0],"durationS":5,"easing":"linear"}}),
]:
    r = api("POST", "/api/spatial-effects", {"name": f"D-{name}", "category": "spatial-field", "blend": "replace", **cfg})
    efx[name] = r.get("id")

TESTS = [
    ("01 Solid Red 10s", 10, [{"actionId":acts["Red"],"startS":0,"durationS":10}]),
    ("02 Chase Cyan 15s", 15, [{"actionId":acts["Chase"],"startS":0,"durationS":15}]),
    ("03 Rainbow Fast 20s", 20, [{"actionId":acts["Rainbow"],"startS":0,"durationS":20}]),
    ("04 Fire 30s", 30, [{"actionId":acts["Fire"],"startS":0,"durationS":30}]),
    ("05 Comet East 15s", 15, [{"actionId":acts["CometE"],"startS":0,"durationS":15}]),
    ("06 Sphere Right", 15, [{"effectId":efx["SphR"],"startS":0,"durationS":15}]),
    ("07 Sphere Left", 10, [{"effectId":efx["SphL"],"startS":0,"durationS":10}]),
    ("08 Sphere Up", 12, [{"effectId":efx["SphU"],"startS":0,"durationS":12}]),
    ("09 Plane Right", 8, [{"effectId":efx["PlnR"],"startS":0,"durationS":8}]),
    ("10 Plane Down", 10, [{"effectId":efx["PlnD"],"startS":0,"durationS":10}]),
    ("11 Red→Blue→Green", 15, [
        {"actionId":acts["Red"],"startS":0,"durationS":5},
        {"actionId":acts["Blue"],"startS":5,"durationS":5},
        {"actionId":acts["Green"],"startS":10,"durationS":5}]),
    ("12 Chase→Rainbow→Fire", 30, [
        {"actionId":acts["Chase"],"startS":0,"durationS":10},
        {"actionId":acts["RainbowSlow"],"startS":10,"durationS":10},
        {"actionId":acts["Fire"],"startS":20,"durationS":10}]),
    ("13 5-Act Sequential", 25, [
        {"actionId":acts["Red"],"startS":0,"durationS":5},
        {"actionId":acts["Chase"],"startS":5,"durationS":5},
        {"actionId":acts["Rainbow"],"startS":10,"durationS":5},
        {"actionId":acts["CometE"],"startS":15,"durationS":5},
        {"actionId":acts["Twinkle"],"startS":20,"durationS":5}]),
    ("14 Sphere R→L", 25, [
        {"effectId":efx["SphR"],"startS":0,"durationS":15},
        {"effectId":efx["SphL"],"startS":15,"durationS":10}]),
    ("15 Plane R→Down", 18, [
        {"effectId":efx["PlnR"],"startS":0,"durationS":8},
        {"effectId":efx["PlnD"],"startS":8,"durationS":10}]),
    ("16 Red+Chase overlap", 15, [
        {"actionId":acts["Red"],"startS":0,"durationS":10},
        {"actionId":acts["Chase"],"startS":5,"durationS":10}]),
    ("17 3-way overlap", 15, [
        {"actionId":acts["Blue"],"startS":0,"durationS":15},
        {"actionId":acts["ChaseRev"],"startS":3,"durationS":9},
        {"actionId":acts["Strobe"],"startS":6,"durationS":6}]),
    ("18 Sweep→Fire", 25, [
        {"effectId":efx["SphR"],"startS":0,"durationS":15},
        {"actionId":acts["Fire"],"startS":15,"durationS":10}]),
    ("19 Action→Sweep→Action", 25, [
        {"actionId":acts["Rainbow"],"startS":0,"durationS":10},
        {"effectId":efx["SphL"],"startS":10,"durationS":10},
        {"actionId":acts["Green"],"startS":20,"durationS":5}]),
    ("20 Interleaved 4-clip", 30, [
        {"effectId":efx["SphR"],"startS":0,"durationS":8},
        {"actionId":acts["Chase"],"startS":8,"durationS":7},
        {"effectId":efx["PlnD"],"startS":15,"durationS":8},
        {"actionId":acts["CometW"],"startS":23,"durationS":7}]),
    ("21 Two spheres", 15, [
        {"effectId":efx["SphR"],"startS":0,"durationS":15},
        {"effectId":efx["SphU"],"startS":0,"durationS":12}]),
    ("22 Sphere+Plane", 15, [
        {"effectId":efx["SphR"],"startS":0,"durationS":15},
        {"effectId":efx["PlnD"],"startS":5,"durationS":10}]),
    ("23 60s Fire", 60, [{"actionId":acts["Fire"],"startS":0,"durationS":60}]),
    ("24 60s Sweep", 60, [{"effectId":efx["SphR"],"startS":0,"durationS":60}]),
    ("25 3s Flash", 3, [{"actionId":acts["Strobe"],"startS":0,"durationS":3}]),
    ("26 5s Wipe", 5, [{"actionId":acts["Wipe"],"startS":0,"durationS":5}]),
    ("27 All 14 types", 70, [{"actionId":aid,"startS":i*5,"durationS":5} for i,aid in enumerate(acts.values())]),
    ("28 Breathe+Gradient", 20, [
        {"actionId":acts["Breathe"],"startS":0,"durationS":10},
        {"actionId":acts["Gradient"],"startS":10,"durationS":10}]),
    ("29 Box Static", 10, [{"effectId":efx["Box"],"startS":0,"durationS":10}]),
    ("30 Kitchen Sink", 60, [
        {"effectId":efx["SphR"],"startS":0,"durationS":10},
        {"actionId":acts["Chase"],"startS":10,"durationS":5},
        {"effectId":efx["PlnD"],"startS":15,"durationS":8},
        {"actionId":acts["Fire"],"startS":23,"durationS":7},
        {"effectId":efx["SphL"],"startS":30,"durationS":10},
        {"actionId":acts["Rainbow"],"startS":40,"durationS":5},
        {"actionId":acts["CometE"],"startS":45,"durationS":5},
        {"effectId":efx["SphU"],"startS":50,"durationS":10}]),
]

TYPE_NAMES = {0:"Blackout",1:"Solid",2:"Fade",3:"Breathe",4:"Chase",5:"Rainbow",
              6:"Fire",7:"Comet",8:"Twinkle",9:"Strobe",10:"Wipe",11:"Scanner",
              12:"Sparkle",13:"Gradient"}

# ── Run all 30 ────────────────────────────────────────────────────────────
print(f"\n{'='*115}")
print(f"{'#':>3} {'Test Name':<28} {'Dur':>5} {'Bake':>6} {'Segs':>5} {'Peak LEDs':>10} {'Avg LEDs':>9} {'LED-Sec':>8} {'Colors':>7} {'Dark%':>6} {'Actions'}")
print(f"{'='*115}")

for idx, (name, dur, clips) in enumerate(TESTS):
    r = api("POST", "/api/timelines", {"name": name, "durationS": dur})
    tl_id = r.get("id")
    if not tl_id: continue
    api("PUT", f"/api/timelines/{tl_id}", {
        "name": name, "durationS": dur, "loop": True,
        "tracks": [{"allPerformers": True, "clips": clips}]
    })

    t0 = time.time()
    api("POST", f"/api/timelines/{tl_id}/bake")
    for _ in range(60):
        time.sleep(0.2)
        bs = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if bs.get("done"): break
    bake_ms = int((time.time() - t0) * 1000)
    ok(f"{name}: bake", bs.get("done") and not bs.get("error"))

    baked = api("GET", f"/api/timelines/{tl_id}/baked")
    preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")

    # Count segments
    total_segs = sum(len(fd.get("segments", [])) for fd in baked.get("fixtures", {}).values())
    seg_types = set()
    for fd in baked.get("fixtures", {}).values():
        for seg in fd.get("segments", []):
            seg_types.add(seg.get("type", 0))

    # ── Analyze preview (what the runtime view shows) ──────────────────
    peak_leds = 0
    total_led_seconds = 0
    dark_seconds = 0
    all_colors = set()
    per_second_leds = []

    if isinstance(preview, dict):
        for sec_idx in range(dur):
            leds_this_second = 0
            any_lit = False
            for fid, frames in preview.items():
                if sec_idx >= len(frames):
                    continue
                colors = frames[sec_idx]
                string_led_counts = fix_leds.get(fid, [])
                for si, rgb in enumerate(colors):
                    if not isinstance(rgb, list) or len(rgb) < 3:
                        continue
                    bri = rgb[0] + rgb[1] + rgb[2]
                    if bri > 10:  # threshold: visible to user
                        any_lit = True
                        led_count = string_led_counts[si] if si < len(string_led_counts) else 0
                        leds_this_second += led_count
                        all_colors.add(tuple(rgb))

            per_second_leds.append(leds_this_second)
            total_led_seconds += leds_this_second
            if leds_this_second > peak_leds:
                peak_leds = leds_this_second
            if not any_lit:
                dark_seconds += 1

    avg_leds = total_led_seconds / dur if dur > 0 else 0
    dark_pct = dark_seconds / dur * 100 if dur > 0 else 0

    # Validate
    ok(f"{name}: has preview", isinstance(preview, dict) and len(preview) > 0)
    ok(f"{name}: LEDs light up", peak_leds > 0)
    ok(f"{name}: has colors", len(all_colors) > 0)
    # Spatial effects may have high dark% if strings are perpendicular to sweep direction
    if dark_pct > 95 and total_segs > 0:
        ok(f"{name}: has some lit time ({dark_pct:.0f}% dark)", dark_pct < 100)

    type_str = "+".join(TYPE_NAMES.get(t, "?") for t in sorted(seg_types))
    print(f"{idx+1:3} {name:<28} {dur:4}s {bake_ms:5}ms {total_segs:5} {peak_leds:10} {avg_leds:9.0f} {total_led_seconds:8} {len(all_colors):7} {dark_pct:5.0f}% {type_str}")

    api("DELETE", f"/api/timelines/{tl_id}")

# ── Cleanup ───────────────────────────────────────────────────────────────
for aid in acts.values():
    if aid: api("DELETE", f"/api/actions/{aid}")
for eid in efx.values():
    if eid: api("DELETE", f"/api/spatial-effects/{eid}")

print(f"\n{'='*115}")
print(f"\n  Assertions: {P} passed, {F} failed")
if ISSUES:
    print("  FAILED:")
    for i in ISSUES: print(f"    ✗ {i}")
    sys.exit(1)
else:
    print("  ✓ ALL TESTS PASS")
