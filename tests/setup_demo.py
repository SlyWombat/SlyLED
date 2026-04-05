#!/usr/bin/env python3
"""
SlyLED Demo Setup — creates a complete 3D show with 4 effects over 30 seconds.

Usage:
    python tests/setup_demo.py [--host localhost:5000]

Prerequisites: server running, at least 1 child online.
"""
import json
import sys
import time
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:5000"
BASE = f"http://{HOST}"

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR {method} {path}: {e}")
        return None

def ok(name, result):
    status = "PASS" if result else "FAIL"
    print(f"  [{status}] {name}")
    return result

# ── Step 1: Check children ──────────────────────────────────────────────────
print("\n=== Step 1: Discover children ===")
children = api("GET", "/api/children") or []
online = [c for c in children if c.get("status") == 1]
print(f"  {len(children)} children registered, {len(online)} online")
for c in children:
    st = "ONLINE" if c["status"] == 1 else "offline"
    leds = sum(s.get("leds", 0) for s in c.get("strings", [])[:c.get("sc", 0)])
    print(f"    #{c['id']} {c['name']} ({c['hostname']}) {c['ip']} [{st}] {c['sc']}x{leds} LEDs")

if not online:
    print("\n  No online children. Cannot proceed.")
    sys.exit(1)

# ── Step 2: Set up stage ────────────────────────────────────────────────────
print("\n=== Step 2: Configure stage ===")
r = api("POST", "/api/stage", {"w": 3.0, "h": 2.0, "d": 2.0})
ok("Stage set to 3m x 2m x 2m", r and r.get("ok"))

# ── Step 2b: Create stage objects ──────────────────────────────────────────
print("\n=== Step 2b: Create stage objects ===")
r = api("POST", "/api/objects", {
    "name": "Back Wall", "objectType": "wall", "stageLocked": True, "mobility": "static"})
ok("Back Wall (stage-locked)", r and r.get("ok"))

r = api("POST", "/api/objects", {
    "name": "Stage Floor", "objectType": "floor", "stageLocked": True, "mobility": "static"})
ok("Stage Floor (stage-locked)", r and r.get("ok"))

r = api("POST", "/api/objects", {
    "name": "Lead Singer", "objectType": "prop", "mobility": "moving",
    "color": "#FF6B35", "opacity": 40,
    "transform": {"pos": [1500, 900, 1000], "rot": [0,0,0], "scale": [500, 1800, 500]},
    "patrol": {"enabled": True, "axis": "x", "speedPreset": "medium",
               "startPct": 20, "endPct": 80, "easing": "sine"}})
ok("Lead Singer (patrol, moving)", r and r.get("ok"))
singer_id = r["id"] if r else None

r = api("POST", "/api/objects", {
    "name": "Drummer", "objectType": "prop", "mobility": "moving",
    "color": "#3B82F6", "opacity": 40,
    "transform": {"pos": [2200, 900, 500], "rot": [0,0,0], "scale": [500, 1800, 500]}})
ok("Drummer (stationary moving object)", r and r.get("ok"))
drummer_id = r["id"] if r else None

# ── Step 3: Place children on layout ────────────────────────────────────────
print("\n=== Step 3: Place children on canvas ===")
layout_children = []
positions = [
    {"x": 500, "y": 1000, "z": 0},    # left side, 1m up
    {"x": 2500, "y": 1000, "z": 0},   # right side, 1m up
    {"x": 1500, "y": 500, "z": 0},    # center, low
    {"x": 1500, "y": 1500, "z": 0},   # center, high
]
for i, c in enumerate(children):
    pos = positions[i % len(positions)]
    layout_children.append({"id": c["id"], "x": pos["x"], "y": pos["y"], "z": pos["z"]})
    print(f"    #{c['id']} {c['name']} → ({pos['x']}, {pos['y']}, {pos['z']})mm")

r = api("POST", "/api/layout", {"children": layout_children})
ok("Layout saved", r and r.get("ok"))

# ── Step 4: Create fixtures from children ───────────────────────────────────
print("\n=== Step 4: Auto-create fixtures ===")
r = api("POST", "/api/migrate/layout")
ok(f"Fixtures created: {r.get('created', 0)}", r and r.get("ok"))

fixtures = api("GET", "/api/fixtures") or []
print(f"  {len(fixtures)} fixtures total")
for f in fixtures:
    print(f"    #{f['id']} {f['name']} type={f['type']} child={f.get('childId')}")

if not fixtures:
    print("  No fixtures. Cannot proceed.")
    sys.exit(1)

# ── Step 5: Create 4 spatial effects ────────────────────────────────────────
print("\n=== Step 5: Create spatial effects ===")

effects = [
    {
        "name": "Red Sweep Right",
        "category": "spatial-field",
        "shape": "sphere", "r": 255, "g": 0, "b": 0,
        "size": {"radius": 800},
        "motion": {"startPos": [0, 1000, 0], "endPos": [3000, 1000, 0],
                   "durationS": 7, "easing": "ease-in-out"},
        "blend": "replace",
    },
    {
        "name": "Blue Sweep Left",
        "category": "spatial-field",
        "shape": "sphere", "r": 0, "g": 50, "b": 255,
        "size": {"radius": 800},
        "motion": {"startPos": [3000, 1000, 0], "endPos": [0, 1000, 0],
                   "durationS": 7, "easing": "ease-in-out"},
        "blend": "replace",
    },
    {
        "name": "Green Plane Drop",
        "category": "spatial-field",
        "shape": "plane", "r": 0, "g": 255, "b": 50,
        "size": {"normal": [0, 1, 0], "thickness": 400},
        "motion": {"startPos": [1500, 2000, 0], "endPos": [1500, 0, 0],
                   "durationS": 8, "easing": "ease-out"},
        "blend": "add",
    },
    {
        "name": "Purple Box Pulse",
        "category": "spatial-field",
        "shape": "box", "r": 180, "g": 0, "b": 255,
        "size": {"width": 2000, "height": 2000, "depth": 2000},
        "motion": {"startPos": [1500, 1000, 0], "endPos": [1500, 1000, 0],
                   "durationS": 8, "easing": "linear"},
        "blend": "screen",
    },
]

fx_ids = []
for fx in effects:
    r = api("POST", "/api/spatial-effects", fx)
    if ok(f"Created: {fx['name']}", r and r.get("ok")):
        fx_ids.append(r["id"])
    else:
        fx_ids.append(None)

# ── Step 6: Create timeline with 4 clips across 30 seconds ─────────────────
print("\n=== Step 6: Build 30-second timeline ===")

r = api("POST", "/api/timelines", {"name": "Demo Show", "durationS": 30})
ok("Timeline created", r and r.get("ok"))
tl_id = r["id"] if r else None

if tl_id is not None:
    # Create one track per fixture, with all 4 effects staggered
    tracks = []
    for f in fixtures:
        clips = []
        # Effect 1: Red sweep (0s - 7s)
        if fx_ids[0] is not None:
            clips.append({"effectId": fx_ids[0], "startS": 0, "durationS": 7})
        # Effect 2: Blue sweep (8s - 15s)
        if fx_ids[1] is not None:
            clips.append({"effectId": fx_ids[1], "startS": 8, "durationS": 7})
        # Effect 3: Green plane drop (16s - 24s)
        if fx_ids[2] is not None:
            clips.append({"effectId": fx_ids[2], "startS": 16, "durationS": 8})
        # Effect 4: Purple box pulse (24s - 30s)
        if fx_ids[3] is not None:
            clips.append({"effectId": fx_ids[3], "startS": 24, "durationS": 6})
        tracks.append({"fixtureId": f["id"], "clips": clips})

    r = api("PUT", f"/api/timelines/{tl_id}", {
        "name": "Demo Show", "durationS": 30,
        "tracks": tracks, "loop": True
    })
    ok(f"Timeline configured: {len(tracks)} tracks, 4 effects each", r and r.get("ok"))

    # ── Step 7: Bake ────────────────────────────────────────────────────────
    print("\n=== Step 7: Bake timeline ===")
    r = api("POST", f"/api/timelines/{tl_id}/bake")
    ok("Bake started", r and r.get("ok"))

    # Poll for completion
    for attempt in range(60):
        time.sleep(0.5)
        s = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if s:
            pct = s.get("progress", 0)
            print(f"  Baking... {pct:.0f}% (frame {s.get('frame',0)}/{s.get('totalFrames',0)})", end="\r")
            if s.get("done"):
                print()
                if s.get("error"):
                    print(f"  BAKE ERROR: {s['error']}")
                else:
                    ok("Bake complete", True)
                    segs = s.get("segments", {})
                    for fid, cnt in segs.items():
                        print(f"    Fixture {fid}: {cnt} action segments")
                break
    else:
        print("\n  Bake timed out after 30s")

    # ── Step 8: Check baked result ──────────────────────────────────────────
    print("\n=== Step 8: Verify baked data ===")
    r = api("GET", f"/api/timelines/{tl_id}/baked")
    if r:
        ok("Baked result available", "fixtures" in r)
        print(f"  Total frames: {r.get('totalFrames', 0)} @ {r.get('fps', 0)}fps")
        print(f"  LSQ size: {r.get('lsqSize', 0)} bytes")
        for fid, fd in r.get("fixtures", {}).items():
            print(f"    Fixture {fid}: {fd.get('frameCount',0)} frames, {len(fd.get('segments',[]))} segments, {fd.get('pixelCount',0)} pixels")

    # ── Step 9: Sync and start ──────────────────────────────────────────────
    print("\n=== Step 9: Sync to performers and START ===")
    r = api("POST", f"/api/timelines/{tl_id}/start")
    if r and r.get("ok"):
        ok(f"Show started! {r.get('started', 0)} performers, go epoch={r.get('goEpoch', 0)}", True)
        print(f"\n  >>> SHOW IS RUNNING on {r.get('started', 0)} performer(s) <<<")
        print(f"  >>> Timeline: 30s, looping, 4 effects <<<")
        print(f"  >>> Effects: Red Sweep → Blue Sweep → Green Drop → Purple Pulse <<<")
        print(f"\n  Monitoring for 10 seconds...")
        for i in range(10):
            time.sleep(1)
            st = api("GET", f"/api/timelines/{tl_id}/status")
            if st:
                print(f"    t={st.get('elapsed',0)}s running={st.get('running',False)}")
    else:
        print(f"  Start failed: {r}")

    # ── Step 10: Let it run, then report ────────────────────────────────────
    print("\n=== Done ===")
    print(f"  Show is running (looping). To stop:")
    print(f"    curl -X POST {BASE}/api/timelines/{tl_id}/stop")
    print(f"  Or use the SPA Runtime tab → Timeline mode → Stop Show")

else:
    print("  Failed to create timeline.")

# ── Step 10b: Create Track action for moving heads ─────────────────────────
print("\n=== Step 10b: Track action (moving heads follow objects) ===")
target_ids = [oid for oid in [singer_id, drummer_id] if oid is not None]
if target_ids:
    r = api("POST", "/api/actions", {
        "name": "Follow Performers", "type": 18,
        "trackObjectIds": target_ids,
        "trackCycleMs": 2000,
        "trackOffset": [0, 200, 0],
        "trackAutoSpread": True})
    ok(f"Track action created (targets: {target_ids})", r and r.get("ok"))
else:
    print("  No moving objects — skipping Track action")

print("\n=== Summary ===")
print(f"  Children: {len(online)} online")
print(f"  Fixtures: {len(fixtures)}")
print(f"  Effects:  {len([x for x in fx_ids if x is not None])}")
print(f"  Timeline: #{tl_id} 'Demo Show' (30s, loop)")
