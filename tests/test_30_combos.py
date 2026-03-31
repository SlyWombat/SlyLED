#!/usr/bin/env python3
"""
SlyLED 30-Combination Bake Test — tests diverse timeline configurations
against 10 emulated children with varied string layouts.

Prerequisites:
  1. Server running: python desktop/shared/parent_server.py
  2. Emulated children running: python tests/emulated_children.py
  3. Children registered (this script auto-registers them)

Usage: python tests/test_30_combos.py [host:port]
"""
import json, sys, time, urllib.request, random

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"
P = 0; F = 0; ISSUES = []

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, json.loads(raw) if raw else {}
    except Exception as e:
        return 0, {"err": str(e)}

def ok(name, result):
    global P, F
    if result: P += 1
    else: F += 1; ISSUES.append(name); print(f"    FAIL {name}")

def bake(tl_id, name):
    api("POST", f"/api/timelines/{tl_id}/bake")
    for _ in range(30):
        time.sleep(0.3)
        _, bs = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if bs.get("done"): break
    ok(f"{name}: bake ok", bs.get("done") and not bs.get("error"))
    _, baked = api("GET", f"/api/timelines/{tl_id}/baked")
    return baked

# ── Setup: register emulated children ────────────────────────────────────
print("=== SETUP ===")
for port in range(9000, 9010):
    api("POST", "/api/children", {"ip": f"127.0.0.1"})  # will fail for non-private but that's ok for localhost

# Actually children need to be on the network — use discover or direct add
# For localhost testing, just use existing children
_, children = api("GET", "/api/children")
print(f"  {len(children)} children available")
if not children:
    print("  No children! Start emulated_children.py or ensure real children are online.")
    sys.exit(1)

api("POST", "/api/migrate/layout")
_, fixtures = api("GET", "/api/fixtures")
print(f"  {len(fixtures)} fixtures")

# Place children in a line across the stage
positions = []
for i, c in enumerate(children):
    x = int((i + 0.5) * 4000 / len(children))
    positions.append({"id": c["id"], "x": x, "y": 1000, "z": 0})
api("POST", "/api/layout", {"children": positions})
print(f"  Placed {len(positions)} children across 4m stage")

# ── Create test actions ──────────────────────────────────────────────────
print("\n=== CREATING TEST DATA ===")
actions = {}
for name, atype, params in [
    ("Solid Red", 1, {"r": 255, "g": 0, "b": 0}),
    ("Solid Blue", 1, {"r": 0, "g": 0, "b": 255}),
    ("Solid Green", 1, {"r": 0, "g": 255, "b": 0}),
    ("Chase Cyan", 4, {"r": 0, "g": 255, "b": 255, "speedMs": 150, "spacing": 3, "direction": 0}),
    ("Chase Rev", 4, {"r": 255, "g": 128, "b": 0, "speedMs": 100, "spacing": 4, "direction": 2}),
    ("Rainbow Fast", 5, {"speedMs": 200, "paletteId": 0, "direction": 0}),
    ("Rainbow Slow", 5, {"speedMs": 1000, "paletteId": 3, "direction": 2}),
    ("Fire Hot", 6, {"r": 255, "g": 60, "b": 0, "speedMs": 30, "cooling": 55, "sparking": 120}),
    ("Comet East", 7, {"r": 0, "g": 200, "b": 255, "speedMs": 40, "tailLen": 12, "direction": 0, "decay": 80}),
    ("Comet West", 7, {"r": 255, "g": 0, "b": 200, "speedMs": 60, "tailLen": 8, "direction": 2, "decay": 60}),
    ("Twinkle", 8, {"r": 255, "g": 255, "b": 255, "spawnMs": 80, "density": 5, "fadeSpeed": 15}),
    ("Strobe", 9, {"r": 255, "g": 255, "b": 255, "periodMs": 150, "p8a": 50}),
    ("Wipe East", 10, {"r": 255, "g": 0, "b": 128, "speedMs": 30, "direction": 0}),
    ("Breathe", 3, {"r": 200, "g": 100, "b": 255, "periodMs": 3000, "minBri": 20}),
    ("Gradient", 13, {"r": 255, "g": 0, "b": 0, "r2": 0, "g2": 0, "b2": 255}),
]:
    _, r = api("POST", "/api/actions", {"name": f"T30-{name}", "type": atype, **params})
    actions[name] = r.get("id")

# Create spatial effects
effects = {}
for name, cfg in [
    ("Sphere Right", {"shape": "sphere", "r": 255, "g": 50, "b": 0, "size": {"radius": 800},
                      "motion": {"startPos": [0,1000,0], "endPos": [4000,1000,0], "durationS": 15, "easing": "linear"}}),
    ("Sphere Left", {"shape": "sphere", "r": 0, "g": 100, "b": 255, "size": {"radius": 600},
                     "motion": {"startPos": [4000,1000,0], "endPos": [0,1000,0], "durationS": 10, "easing": "ease-in-out"}}),
    ("Sphere Up", {"shape": "sphere", "r": 0, "g": 255, "b": 100, "size": {"radius": 1000},
                   "motion": {"startPos": [2000,0,0], "endPos": [2000,3000,0], "durationS": 12, "easing": "ease-out"}}),
    ("Plane Right", {"shape": "plane", "r": 200, "g": 0, "b": 255, "size": {"normal": [1,0,0], "thickness": 500},
                     "motion": {"startPos": [0,1000,0], "endPos": [4000,1000,0], "durationS": 8, "easing": "linear"}}),
    ("Plane Down", {"shape": "plane", "r": 255, "g": 200, "b": 0, "size": {"normal": [0,1,0], "thickness": 400},
                    "motion": {"startPos": [2000,3000,0], "endPos": [2000,0,0], "durationS": 10, "easing": "ease-in"}}),
    ("Box Center", {"shape": "box", "r": 128, "g": 0, "b": 255, "size": {"width": 2000, "height": 2000, "depth": 2000},
                    "motion": {"startPos": [2000,1000,0], "endPos": [2000,1000,0], "durationS": 5, "easing": "linear"}}),
]:
    _, r = api("POST", "/api/spatial-effects", {"name": f"T30-{name}", "category": "spatial-field", "blend": "replace", **cfg})
    effects[name] = r.get("id")

print(f"  {len(actions)} actions, {len(effects)} effects created")

# ── Define 30 test timelines ─────────────────────────────────────────────
TESTS = [
    # Single action tests
    {"name": "01-Solid Red 10s", "dur": 10, "clips": [{"actionId": actions["Solid Red"], "startS": 0, "durationS": 10}]},
    {"name": "02-Chase Cyan 15s", "dur": 15, "clips": [{"actionId": actions["Chase Cyan"], "startS": 0, "durationS": 15}]},
    {"name": "03-Rainbow Fast 20s", "dur": 20, "clips": [{"actionId": actions["Rainbow Fast"], "startS": 0, "durationS": 20}]},
    {"name": "04-Fire 30s", "dur": 30, "clips": [{"actionId": actions["Fire Hot"], "startS": 0, "durationS": 30}]},
    {"name": "05-Comet East 15s", "dur": 15, "clips": [{"actionId": actions["Comet East"], "startS": 0, "durationS": 15}]},
    # Single spatial tests
    {"name": "06-Sphere Right", "dur": 15, "clips": [{"effectId": effects["Sphere Right"], "startS": 0, "durationS": 15}]},
    {"name": "07-Sphere Left", "dur": 10, "clips": [{"effectId": effects["Sphere Left"], "startS": 0, "durationS": 10}]},
    {"name": "08-Sphere Up", "dur": 12, "clips": [{"effectId": effects["Sphere Up"], "startS": 0, "durationS": 12}]},
    {"name": "09-Plane Right", "dur": 8, "clips": [{"effectId": effects["Plane Right"], "startS": 0, "durationS": 8}]},
    {"name": "10-Plane Down", "dur": 10, "clips": [{"effectId": effects["Plane Down"], "startS": 0, "durationS": 10}]},
    # Sequential actions
    {"name": "11-Red→Blue→Green", "dur": 15, "clips": [
        {"actionId": actions["Solid Red"], "startS": 0, "durationS": 5},
        {"actionId": actions["Solid Blue"], "startS": 5, "durationS": 5},
        {"actionId": actions["Solid Green"], "startS": 10, "durationS": 5},
    ]},
    {"name": "12-Chase→Rainbow→Fire", "dur": 30, "clips": [
        {"actionId": actions["Chase Cyan"], "startS": 0, "durationS": 10},
        {"actionId": actions["Rainbow Slow"], "startS": 10, "durationS": 10},
        {"actionId": actions["Fire Hot"], "startS": 20, "durationS": 10},
    ]},
    {"name": "13-5 Actions Sequential", "dur": 25, "clips": [
        {"actionId": actions["Solid Red"], "startS": 0, "durationS": 5},
        {"actionId": actions["Chase Cyan"], "startS": 5, "durationS": 5},
        {"actionId": actions["Rainbow Fast"], "startS": 10, "durationS": 5},
        {"actionId": actions["Comet East"], "startS": 15, "durationS": 5},
        {"actionId": actions["Twinkle"], "startS": 20, "durationS": 5},
    ]},
    # Sequential spatial
    {"name": "14-Sphere R→L", "dur": 25, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 15},
        {"effectId": effects["Sphere Left"], "startS": 15, "durationS": 10},
    ]},
    {"name": "15-Plane R→Down", "dur": 18, "clips": [
        {"effectId": effects["Plane Right"], "startS": 0, "durationS": 8},
        {"effectId": effects["Plane Down"], "startS": 8, "durationS": 10},
    ]},
    # Overlapping actions
    {"name": "16-Red+Chase overlap", "dur": 15, "clips": [
        {"actionId": actions["Solid Red"], "startS": 0, "durationS": 10},
        {"actionId": actions["Chase Cyan"], "startS": 5, "durationS": 10},
    ]},
    {"name": "17-3 overlapping", "dur": 15, "clips": [
        {"actionId": actions["Solid Blue"], "startS": 0, "durationS": 15},
        {"actionId": actions["Chase Rev"], "startS": 3, "durationS": 9},
        {"actionId": actions["Strobe"], "startS": 6, "durationS": 6},
    ]},
    # Mixed spatial + action
    {"name": "18-Sweep then Fire", "dur": 25, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 15},
        {"actionId": actions["Fire Hot"], "startS": 15, "durationS": 10},
    ]},
    {"name": "19-Action then Sweep", "dur": 25, "clips": [
        {"actionId": actions["Rainbow Fast"], "startS": 0, "durationS": 10},
        {"effectId": effects["Sphere Left"], "startS": 10, "durationS": 10},
        {"actionId": actions["Solid Green"], "startS": 20, "durationS": 5},
    ]},
    {"name": "20-Interleaved", "dur": 30, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 8},
        {"actionId": actions["Chase Cyan"], "startS": 8, "durationS": 7},
        {"effectId": effects["Plane Down"], "startS": 15, "durationS": 8},
        {"actionId": actions["Comet West"], "startS": 23, "durationS": 7},
    ]},
    # Multi-effect spatial
    {"name": "21-Two spheres", "dur": 15, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 15},
        {"effectId": effects["Sphere Up"], "startS": 0, "durationS": 12},
    ]},
    {"name": "22-Sphere+Plane", "dur": 15, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 15},
        {"effectId": effects["Plane Down"], "startS": 5, "durationS": 10},
    ]},
    # Long shows
    {"name": "23-60s Fire", "dur": 60, "clips": [{"actionId": actions["Fire Hot"], "startS": 0, "durationS": 60}]},
    {"name": "24-60s Sweep", "dur": 60, "clips": [{"effectId": effects["Sphere Right"], "startS": 0, "durationS": 60}]},
    # Short shows
    {"name": "25-3s Flash", "dur": 3, "clips": [{"actionId": actions["Strobe"], "startS": 0, "durationS": 3}]},
    {"name": "26-5s Wipe", "dur": 5, "clips": [{"actionId": actions["Wipe East"], "startS": 0, "durationS": 5}]},
    # Edge cases
    {"name": "27-All 14 types", "dur": 70, "clips": [
        {"actionId": aid, "startS": i * 5, "durationS": 5}
        for i, aid in enumerate(actions.values())
    ]},
    {"name": "28-Breathe+Gradient", "dur": 20, "clips": [
        {"actionId": actions["Breathe"], "startS": 0, "durationS": 10},
        {"actionId": actions["Gradient"], "startS": 10, "durationS": 10},
    ]},
    {"name": "29-Box Static", "dur": 10, "clips": [
        {"effectId": effects["Box Center"], "startS": 0, "durationS": 10}
    ]},
    {"name": "30-Kitchen Sink", "dur": 60, "clips": [
        {"effectId": effects["Sphere Right"], "startS": 0, "durationS": 10},
        {"actionId": actions["Chase Cyan"], "startS": 10, "durationS": 5},
        {"effectId": effects["Plane Down"], "startS": 15, "durationS": 8},
        {"actionId": actions["Fire Hot"], "startS": 23, "durationS": 7},
        {"effectId": effects["Sphere Left"], "startS": 30, "durationS": 10},
        {"actionId": actions["Rainbow Fast"], "startS": 40, "durationS": 5},
        {"actionId": actions["Comet East"], "startS": 45, "durationS": 5},
        {"effectId": effects["Sphere Up"], "startS": 50, "durationS": 10},
    ]},
]

# ── Run all 30 tests ─────────────────────────────────────────────────────
print(f"\n=== RUNNING {len(TESTS)} BAKE TESTS ===\n")

for test in TESTS:
    name = test["name"]
    print(f"  {name}")

    _, r = api("POST", "/api/timelines", {"name": name, "durationS": test["dur"]})
    tl_id = r.get("id")
    if not tl_id:
        ok(f"{name}: create", False); continue

    api("PUT", f"/api/timelines/{tl_id}", {
        "name": name, "durationS": test["dur"], "loop": True,
        "tracks": [{"allPerformers": True, "clips": test["clips"]}]
    })

    baked = bake(tl_id, name)
    bfix = baked.get("fixtures", {})
    ok(f"{name}: has fixtures", len(bfix) > 0)

    total_segs = 0
    for fid, fd in bfix.items():
        segs = fd.get("segments", [])
        total_segs += len(segs)
        ok(f"{name} fix {fid}: <= 16 segs", len(segs) <= 16)
        for seg in segs:
            ok(f"{name} fix {fid}: valid type", 0 <= seg.get("type", -1) <= 13)
            ok(f"{name} fix {fid}: duration > 0", seg.get("durationS", 0) > 0)

    # Check preview
    _, preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
    ok(f"{name}: preview exists", isinstance(preview, dict) and len(preview) > 0)

    print(f"    → {len(bfix)} fixtures, {total_segs} total segments")
    api("DELETE", f"/api/timelines/{tl_id}")

# ── Cleanup ──────────────────────────────────────────────────────────────
print("\n=== CLEANUP ===")
for aid in actions.values():
    if aid: api("DELETE", f"/api/actions/{aid}")
for eid in effects.values():
    if eid: api("DELETE", f"/api/spatial-effects/{eid}")

print(f"\n{'='*60}")
print(f"  RESULTS: {P} passed, {F} failed")
print(f"  ({len(TESTS)} timelines × {len(fixtures)} fixtures)")
print(f"{'='*60}")
if ISSUES:
    print("\nFAILED:")
    for i in ISSUES[:20]: print(f"  ✗ {i}")
    if len(ISSUES) > 20: print(f"  ... and {len(ISSUES)-20} more")
    sys.exit(1)
else:
    print("\n  ✓ ALL TESTS PASS")
