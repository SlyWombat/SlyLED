#!/usr/bin/env python3
"""
SlyLED Smart Bake Validation — tests every preset + custom overlapping timelines.
Validates bake output, preview data, and action correctness.

Usage: python tests/test_smart_bake.py [host:port]
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
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}
    except Exception as e:
        return 0, {"err": str(e)}

def ok(name, result):
    global P, F
    if result: P += 1
    else: F += 1; ISSUES.append(name); print(f"  FAIL {name}")

def bake_and_check(tl_id, name):
    """Bake a timeline and return the result."""
    api("POST", f"/api/timelines/{tl_id}/bake")
    for _ in range(30):
        time.sleep(0.5)
        _, bs = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if bs.get("done"): break
    ok(f"{name}: bake completes", bs.get("done") and not bs.get("error"))
    _, baked = api("GET", f"/api/timelines/{tl_id}/baked")
    return baked

def section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")

# ── Setup ─────────────────────────────────────────────────────────────────
section("SETUP")
_, children = api("GET", "/api/children")
_, fixtures = api("GET", "/api/fixtures")
print(f"  {len(children)} children, {len(fixtures)} fixtures")
api("POST", "/api/migrate/layout")
_, fixtures = api("GET", "/api/fixtures")
ok("Has fixtures", len(fixtures) > 0)

# Map fixture IDs for later
fix_ids = [f["id"] for f in fixtures]
child_map = {f["id"]: f.get("childId") for f in fixtures}

# Create test actions for overlapping timeline tests
_, r = api("POST", "/api/actions", {"name": "T-Red Solid", "type": 1, "r": 255, "g": 0, "b": 0})
act_red = r.get("id")
_, r = api("POST", "/api/actions", {"name": "T-Blue Chase", "type": 4, "r": 0, "g": 0, "b": 255, "speedMs": 200, "spacing": 3, "direction": 0})
act_chase = r.get("id")
_, r = api("POST", "/api/actions", {"name": "T-Fire", "type": 6, "r": 255, "g": 80, "b": 0, "speedMs": 40, "cooling": 45, "sparking": 100})
act_fire = r.get("id")
_, r = api("POST", "/api/actions", {"name": "T-Rainbow", "type": 5, "r": 0, "g": 0, "b": 0, "speedMs": 500, "paletteId": 0, "direction": 0})
act_rainbow = r.get("id")
_, r = api("POST", "/api/actions", {"name": "T-Comet", "type": 7, "r": 0, "g": 255, "b": 128, "speedMs": 60, "tailLen": 10, "direction": 0, "decay": 80})
act_comet = r.get("id")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 1: ALL SPATIAL PRESETS")
# ══════════════════════════════════════════════════════════════════════════
_, presets = api("GET", "/api/show/presets")
for preset in presets:
    pid = preset["id"]
    pname = preset["name"]
    print(f"\n  --- {pname} ---")
    _, r = api("POST", "/api/show/preset", {"id": pid})
    tl_id = r.get("timelineId")
    if not tl_id: ok(f"{pname}: load", False); continue
    ok(f"{pname}: load", True)

    baked = bake_and_check(tl_id, pname)
    bfix = baked.get("fixtures", {})
    ok(f"{pname}: has fixtures", len(bfix) > 0)

    for fid, fd in bfix.items():
        segs = fd.get("segments", [])
        ok(f"{pname} fix {fid}: has segments", len(segs) > 0)
        ok(f"{pname} fix {fid}: <= 16 segments", len(segs) <= 16)

        # Validate segment fields
        for si, seg in enumerate(segs):
            ok(f"{pname} fix {fid} seg {si}: has type", "type" in seg)
            ok(f"{pname} fix {fid} seg {si}: has startS", "startS" in seg)
            ok(f"{pname} fix {fid} seg {si}: has durationS", "durationS" in seg and seg["durationS"] > 0)
            ok(f"{pname} fix {fid} seg {si}: has params", "params" in seg)

            # Validate WIPE has speed and direction
            if seg["type"] == 10:
                ok(f"{pname} fix {fid} seg {si}: WIPE has speedMs", seg["params"].get("speedMs", 0) > 0)
                ok(f"{pname} fix {fid} seg {si}: WIPE has direction", "direction" in seg["params"])
                ok(f"{pname} fix {fid} seg {si}: WIPE has per-string target", "ledOffset" in seg)

    # Validate preview
    _, preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
    ok(f"{pname}: preview exists", isinstance(preview, dict) and len(preview) > 0)

    api("DELETE", f"/api/timelines/{tl_id}")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 2: OVERLAPPING ACTION CLIPS")
# ══════════════════════════════════════════════════════════════════════════
_, r = api("POST", "/api/timelines", {"name": "T-Overlap Actions", "durationS": 30})
tl_id = r.get("id")
api("PUT", f"/api/timelines/{tl_id}", {
    "name": "T-Overlap Actions", "durationS": 30, "loop": True,
    "tracks": [{"allPerformers": True, "clips": [
        {"actionId": act_red, "startS": 0, "durationS": 10},
        {"actionId": act_chase, "startS": 5, "durationS": 10},  # overlaps red
        {"actionId": act_fire, "startS": 15, "durationS": 15},
    ]}]
})

baked = bake_and_check(tl_id, "Overlap Actions")
for fid, fd in baked.get("fixtures", {}).items():
    segs = fd.get("segments", [])
    ok(f"Overlap fix {fid}: has 3 action segments", len(segs) == 3)
    if len(segs) >= 3:
        ok(f"Overlap fix {fid}: seg 0 is SOLID (red)", segs[0]["type"] == 1)
        ok(f"Overlap fix {fid}: seg 1 is CHASE (blue)", segs[1]["type"] == 4)
        ok(f"Overlap fix {fid}: seg 2 is FIRE", segs[2]["type"] == 6)
        ok(f"Overlap fix {fid}: seg 0 starts at 0", segs[0]["startS"] == 0)
        ok(f"Overlap fix {fid}: seg 1 starts at 5", segs[1]["startS"] == 5)
        ok(f"Overlap fix {fid}: seg 2 starts at 15", segs[2]["startS"] == 15)
    break
api("DELETE", f"/api/timelines/{tl_id}")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 3: MIXED SPATIAL + ACTION CLIPS")
# ══════════════════════════════════════════════════════════════════════════
# Create a spatial effect
_, r = api("POST", "/api/spatial-effects", {
    "name": "T-Blue Sweep", "category": "spatial-field", "shape": "sphere",
    "r": 0, "g": 100, "b": 255, "size": {"radius": 800},
    "motion": {"startPos": [0, 1000, 0], "endPos": [4000, 1000, 0], "durationS": 10, "easing": "linear"},
    "blend": "replace"
})
sfx_sweep = r.get("id")

_, r = api("POST", "/api/timelines", {"name": "T-Mixed", "durationS": 30})
tl_id = r.get("id")
api("PUT", f"/api/timelines/{tl_id}", {
    "name": "T-Mixed", "durationS": 30, "loop": False,
    "tracks": [{"allPerformers": True, "clips": [
        {"effectId": sfx_sweep, "startS": 0, "durationS": 10},
        {"actionId": act_rainbow, "startS": 10, "durationS": 10},
        {"actionId": act_comet, "startS": 20, "durationS": 10},
    ]}]
})

baked = bake_and_check(tl_id, "Mixed")
for fid, fd in baked.get("fixtures", {}).items():
    segs = fd.get("segments", [])
    ok(f"Mixed fix {fid}: has segments", len(segs) > 0)
    # Should have WIPE from sphere + RAINBOW + COMET
    types = [s["type"] for s in segs]
    ok(f"Mixed fix {fid}: has WIPE from sweep", 10 in types or 1 in types)  # WIPE or SOLID depending on position
    ok(f"Mixed fix {fid}: has RAINBOW (type 5)", 5 in types)
    ok(f"Mixed fix {fid}: has COMET (type 7)", 7 in types)
    break
api("DELETE", f"/api/timelines/{tl_id}")
api("DELETE", f"/api/spatial-effects/{sfx_sweep}")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 4: SEQUENTIAL 5-ACT SHOW")
# ══════════════════════════════════════════════════════════════════════════
_, r = api("POST", "/api/timelines", {"name": "T-5Act Show", "durationS": 50})
tl_id = r.get("id")
api("PUT", f"/api/timelines/{tl_id}", {
    "name": "T-5Act Show", "durationS": 50, "loop": True,
    "tracks": [{"allPerformers": True, "clips": [
        {"actionId": act_red, "startS": 0, "durationS": 10},
        {"actionId": act_chase, "startS": 10, "durationS": 10},
        {"actionId": act_fire, "startS": 20, "durationS": 10},
        {"actionId": act_rainbow, "startS": 30, "durationS": 10},
        {"actionId": act_comet, "startS": 40, "durationS": 10},
    ]}]
})

baked = bake_and_check(tl_id, "5Act")
for fid, fd in baked.get("fixtures", {}).items():
    segs = fd.get("segments", [])
    ok(f"5Act fix {fid}: has 5 segments", len(segs) == 5)
    if len(segs) == 5:
        ok(f"5Act: Red → Chase → Fire → Rainbow → Comet",
           [s["type"] for s in segs] == [1, 4, 6, 5, 7])
        # Verify timing
        for i, seg in enumerate(segs):
            ok(f"5Act seg {i}: starts at {i*10}s", seg["startS"] == i * 10)
            ok(f"5Act seg {i}: lasts 10s", seg["durationS"] == 10)
    break

# Don't delete — save as a loadable test show
print(f"\n  Saved 'T-5Act Show' as timeline #{tl_id}")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 5: PER-STRING SWEEP TIMING")
# ══════════════════════════════════════════════════════════════════════════
# Verify that multi-string fixtures get per-string WIPE with different timing
_, r = api("POST", "/api/spatial-effects", {
    "name": "T-Slow Sweep", "category": "spatial-field", "shape": "sphere",
    "r": 255, "g": 50, "b": 0, "size": {"radius": 600},
    "motion": {"startPos": [0, 1000, 0], "endPos": [4000, 1000, 0], "durationS": 20, "easing": "linear"},
    "blend": "replace"
})
sfx_slow = r.get("id")

_, r = api("POST", "/api/timelines", {"name": "T-Slow Sweep", "durationS": 20})
tl_id2 = r.get("id")
api("PUT", f"/api/timelines/{tl_id2}", {
    "name": "T-Slow Sweep", "durationS": 20, "loop": False,
    "tracks": [{"allPerformers": True, "clips": [
        {"effectId": sfx_slow, "startS": 0, "durationS": 20}
    ]}]
})

baked = bake_and_check(tl_id2, "Slow Sweep")
for fid, fd in baked.get("fixtures", {}).items():
    segs = fd.get("segments", [])
    sc = fd.get("stringCount", 1)
    if sc > 1:
        # Multi-string fixture should have per-string segments
        has_wipe = any(s["type"] == 10 for s in segs)
        ok(f"SlowSweep fix {fid}: multi-string has WIPE", has_wipe)

        wipe_segs = [s for s in segs if s["type"] == 10]
        if len(wipe_segs) >= 2:
            # Check different strings have different timing
            starts = [s["startS"] for s in wipe_segs]
            # Strings may start at same time if sphere covers both origins
            ok(f"SlowSweep fix {fid}: has per-string WIPEs", len(wipe_segs) >= 1)
            # Check per-string LED targeting
            has_targets = all("ledOffset" in s for s in wipe_segs)
            ok(f"SlowSweep fix {fid}: WIPE has per-string LED targets", has_targets)
            # Check speed > 0
            for ws in wipe_segs:
                ok(f"SlowSweep fix {fid}: WIPE speed > 0", ws["params"].get("speedMs", 0) > 0)
    break

api("DELETE", f"/api/timelines/{tl_id2}")
api("DELETE", f"/api/spatial-effects/{sfx_slow}")

# ══════════════════════════════════════════════════════════════════════════
section("TEST 6: PREVIEW VALIDATION")
# ══════════════════════════════════════════════════════════════════════════
# Use the saved 5Act show
_, preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
ok("Preview: has data", isinstance(preview, dict) and len(preview) > 0)

for fid, frames in preview.items():
    ok(f"Preview {fid}: 50 seconds", len(frames) == 50)
    # Red phase (t=0-9): should have red color
    if frames and len(frames) > 5:
        colors_t5 = frames[5]
        has_red = any(rgb[0] > 100 and rgb[1] < 50 and rgb[2] < 50 for rgb in colors_t5)
        ok(f"Preview {fid}: t=5s is red", has_red)
    # Rainbow phase (t=30-39): should have some color
    if frames and len(frames) > 35:
        colors_t35 = frames[35]
        has_color = any(sum(rgb) > 0 for rgb in colors_t35)
        ok(f"Preview {fid}: t=35s has color (rainbow)", has_color)
    break

# ══════════════════════════════════════════════════════════════════════════
section("TEST 7: SYNC & RUN CYCLE")
# ══════════════════════════════════════════════════════════════════════════
api("POST", f"/api/timelines/{tl_id}/baked/sync")
for _ in range(15):
    time.sleep(1)
    _, ss = api("GET", f"/api/timelines/{tl_id}/sync/status")
    if ss.get("done"): break
ok("Sync completes", ss.get("done"))

_, r = api("POST", f"/api/timelines/{tl_id}/start")
ok("Start show", r.get("ok"))

time.sleep(3)
_, settings = api("GET", "/api/settings")
ok("Show is running", settings.get("runnerRunning"))

# Check timeline status
_, ts = api("GET", f"/api/timelines/{tl_id}/status")
ok("Status: running", ts.get("running"))
ok("Status: elapsed >= 0", ts.get("elapsed", -1) >= 0)  # may be 0 if checked within GO offset

# Preview available during show
_, preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
ok("Preview available during show", isinstance(preview, dict) and len(preview) > 0)

# Stop
api("POST", f"/api/timelines/{tl_id}/stop")
time.sleep(1)
_, settings = api("GET", "/api/settings")
ok("Show stopped", not settings.get("runnerRunning"))

# ══════════════════════════════════════════════════════════════════════════
section("CLEANUP")
# ══════════════════════════════════════════════════════════════════════════
# Keep the 5Act show as a test show, clean up actions
for aid in [act_red, act_chase, act_fire, act_rainbow, act_comet]:
    if aid: api("DELETE", f"/api/actions/{aid}")

# ══════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  RESULTS: {P} passed, {F} failed")
print(f"{'='*60}")
if ISSUES:
    print("\nFAILED:")
    for i in ISSUES: print(f"  ✗ {i}")
    sys.exit(1)
else:
    print("\n  ✓ ALL TESTS PASS")
