#!/usr/bin/env python3
"""
SlyLED Full QA Test Suite — exercises every feature end-to-end.
Simulates what a user does on both desktop SPA and Android app.

Usage: python tests/test_full_qa.py [host:port]
"""
import json, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"
P = 0; F = 0; ISSUES = []

def api(method, path, body=None, headers=None, expect=200):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data: req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items(): req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read()) if resp.read else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}
    except Exception as e:
        return 0, {"err": str(e)}

def ok(name, result):
    global P, F
    if result: P += 1
    else: F += 1; ISSUES.append(name); print(f"  FAIL {name}")

def section(name):
    print(f"\n{'='*60}\n  {name}\n{'='*60}")

# ═══════════════════════════════════════════════════════════════
section("1. CONNECTION & STATUS")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/status")
ok("GET /status returns 200", c == 200)
ok("Status has version", "version" in d)
ok("Status has hostname", "hostname" in d)

# ═══════════════════════════════════════════════════════════════
section("2. SETTINGS")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/settings")
ok("GET settings", c == 200)
ok("Settings has name", "name" in d)
ok("Settings has canvasW", "canvasW" in d)
ok("Settings has canvasH", "canvasH" in d)
ok("Settings has activeTimeline", "activeTimeline" in d)
ok("Settings has runnerRunning", "runnerRunning" in d)
ok("Settings has darkMode", "darkMode" in d)

c, d = api("POST", "/api/settings", {"name": "QA Test", "canvasW": 5000, "canvasH": 3000})
ok("POST settings", c == 200 and d.get("ok"))

# Verify stage synced
c, d = api("GET", "/api/stage")
ok("Stage synced from settings: w=5.0", d.get("w") == 5.0)
ok("Stage synced from settings: h=3.0", d.get("h") == 3.0)

# Set stage, verify settings synced back
c, d = api("POST", "/api/stage", {"w": 4.0, "h": 2.5, "d": 3.0})
ok("POST stage", c == 200 and d.get("ok"))
c, d = api("GET", "/api/settings")
ok("Settings synced from stage: canvasW=4000", d.get("canvasW") == 4000)
ok("Settings synced from stage: canvasH=2500", d.get("canvasH") == 2500)

# ═══════════════════════════════════════════════════════════════
section("3. CHILDREN / PERFORMERS")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/children")
ok("GET children", c == 200 and isinstance(d, list))
initial_count = len(d)

# Add child with valid private IP
c, d = api("POST", "/api/children", {"ip": "192.168.1.200"})
ok("Add child (private IP)", c == 200 and d.get("ok"))
test_cid = d.get("id")

# Add child with public IP (should fail)
c, d = api("POST", "/api/children", {"ip": "8.8.8.8"})
ok("Reject public IP", c == 400)

# Add child with invalid IP
c, d = api("POST", "/api/children", {"ip": "not-an-ip"})
ok("Reject invalid IP", c == 400)

# Discover
c, d = api("GET", "/api/children/discover")
ok("Discover returns list", c == 200 and isinstance(d, list))

# Delete test child
if test_cid is not None:
    c, d = api("DELETE", f"/api/children/{test_cid}")
    ok("Delete child", c == 200 and d.get("ok"))

# ═══════════════════════════════════════════════════════════════
section("4. LAYOUT")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/layout")
ok("GET layout", c == 200)
ok("Layout has canvasW", "canvasW" in d)
ok("Layout has children list", "children" in d)

# Save layout with z coordinate
c, d = api("GET", "/api/children")
if d and isinstance(d, list) and d:
    cid = d[0]["id"]
    c, d = api("POST", "/api/layout", {"children": [{"id": cid, "x": 2000, "y": 1500, "z": 300}]})
    ok("Save layout with z", c == 200 and d.get("ok"))
    c, d = api("GET", "/api/layout")
    lc = next((c for c in d.get("children", []) if c.get("id") == cid), None)
    ok("Layout returns z", lc is not None and lc.get("z") == 300)

# ═══════════════════════════════════════════════════════════════
section("5. FIXTURES")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/fixtures")
ok("GET fixtures", c == 200 and isinstance(d, list))

# Auto-create
c, d = api("POST", "/api/migrate/layout")
ok("Migrate layout (auto-create fixtures)", c == 200 and d.get("ok"))

c, d = api("GET", "/api/fixtures")
ok("Fixtures exist after migrate", isinstance(d, list) and len(d) > 0)

# Create with rotation
c, d = api("POST", "/api/fixtures", {"name": "QA Fix", "type": "linear", "rotation": [0, 0, 90]})
ok("Create fixture with rotation", c == 200 and d.get("ok"))
qa_fix = d.get("id")

c, d = api("GET", f"/api/fixtures/{qa_fix}")
ok("Fixture has rotation", d.get("rotation") == [0, 0, 90])

# Resolve
c, d = api("POST", f"/api/fixtures/{qa_fix}/resolve")
ok("Resolve fixture", c == 200 and "pixelPositions" in d)

# Bad type
c, d = api("POST", "/api/fixtures", {"name": "Bad", "type": "invalid"})
ok("Reject invalid fixture type", c == 400)

# Group type
c, d = api("POST", "/api/fixtures", {"name": "Group", "type": "group"})
ok("Create group fixture", c == 200 and d.get("ok"))
api("DELETE", f"/api/fixtures/{d.get('id')}")

api("DELETE", f"/api/fixtures/{qa_fix}")

# ═══════════════════════════════════════════════════════════════
section("6. SURFACES")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", "/api/surfaces", {"name": "QA Wall", "color": "#ff0000", "opacity": 50,
    "surfaceType": "wall", "transform": {"pos": [0,0,0], "rot": [0,0,0], "scale": [3000, 2000, 200]}})
ok("Create surface", c == 200 and d.get("ok"))
sf_id = d.get("id")

c, d = api("GET", "/api/surfaces")
ok("GET surfaces", c == 200 and isinstance(d, list) and len(d) > 0)
sf = next((s for s in d if s.get("id") == sf_id), None)
ok("Surface has color", sf and sf.get("color") == "#ff0000")
ok("Surface has surfaceType", sf and sf.get("surfaceType") == "wall")
ok("Surface has depth in scale", sf and sf.get("transform", {}).get("scale", [0,0,0])[2] == 200)

api("DELETE", f"/api/surfaces/{sf_id}")

# ═══════════════════════════════════════════════════════════════
section("7. SPATIAL EFFECTS")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", "/api/spatial-effects", {"name": "QA Sphere", "category": "spatial-field",
    "shape": "sphere", "r": 255, "g": 0, "b": 0, "size": {"radius": 1000},
    "motion": {"startPos": [0,0,0], "endPos": [3000,0,0], "durationS": 5, "easing": "ease-in-out"},
    "blend": "add"})
ok("Create spatial effect", c == 200 and d.get("ok"))
sfx_id = d.get("id")

c, d = api("GET", f"/api/spatial-effects/{sfx_id}")
ok("GET spatial effect", c == 200 and d.get("shape") == "sphere")
ok("Effect has easing", d.get("motion", {}).get("easing") == "ease-in-out")
ok("Effect has blend", d.get("blend") == "add")

# Fixture-local
c, d = api("POST", "/api/spatial-effects", {"name": "QA Chase", "category": "fixture-local", "actionType": 4})
ok("Create fixture-local effect", c == 200)
api("DELETE", f"/api/spatial-effects/{d.get('id')}")

# Bad category
c, d = api("POST", "/api/spatial-effects", {"name": "Bad", "category": "wrong"})
ok("Reject invalid category", c == 400)

# Empty name
c, d = api("POST", "/api/spatial-effects", {"name": "", "category": "spatial-field"})
ok("Reject empty name", c == 400)

api("DELETE", f"/api/spatial-effects/{sfx_id}")

# ═══════════════════════════════════════════════════════════════
section("8. ACTIONS (classic)")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/actions")
ok("GET actions", c == 200 and isinstance(d, list))

c, d = api("POST", "/api/actions", {"name": "QA Solid", "type": 1, "r": 255, "g": 128, "b": 0})
ok("Create action", c == 200 and d.get("ok"))
act_id = d.get("id")

# Immediate action
c, d = api("POST", "/api/action", {"type": 1, "r": 0, "g": 255, "b": 0})
ok("Send immediate action", c == 200 and d.get("ok"))

c, d = api("POST", "/api/action/stop")
ok("Stop immediate action", c == 200)

# ═══════════════════════════════════════════════════════════════
section("9. PRESETS")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/show/presets")
ok("GET presets", c == 200 and isinstance(d, list))
ok("Has 9 presets", len(d) == 9)
preset_names = [p["name"] for p in d]
for expected in ["Rainbow Up", "Rainbow Across", "Slow Fire", "Disco", "Ocean Wave",
                  "Sunset Glow", "Police Lights", "Starfield", "Aurora Borealis"]:
    ok(f"Preset: {expected}", expected in preset_names)

# ═══════════════════════════════════════════════════════════════
section("10. TIMELINES")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", "/api/timelines", {"name": "QA Timeline", "durationS": 20})
ok("Create timeline", c == 200 and d.get("ok"))
tl_id = d.get("id")

c, d = api("GET", f"/api/timelines/{tl_id}")
ok("GET timeline", c == 200 and d.get("durationS") == 20)

# Add allPerformers track with action clip
c, d = api("PUT", f"/api/timelines/{tl_id}", {
    "name": "QA Timeline", "durationS": 20, "loop": True,
    "tracks": [{"allPerformers": True, "clips": [
        {"actionId": act_id, "startS": 0, "durationS": 10},
    ]}]
})
ok("Add allPerformers track with action clip", c == 200 and d.get("ok"))

# Verify structure
c, d = api("GET", f"/api/timelines/{tl_id}")
ok("Timeline has tracks", len(d.get("tracks", [])) == 1)
ok("Track is allPerformers", d["tracks"][0].get("allPerformers") == True)
ok("Track has 1 clip", len(d["tracks"][0].get("clips", [])) == 1)
ok("Clip has actionId", d["tracks"][0]["clips"][0].get("actionId") == act_id)

# Empty name rejected
c, d = api("POST", "/api/timelines", {"name": "", "durationS": 10})
ok("Reject empty timeline name", c == 400)

# ═══════════════════════════════════════════════════════════════
section("11. BAKE")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", f"/api/timelines/{tl_id}/bake")
ok("Start bake", c == 200)

for _ in range(30):
    time.sleep(0.5)
    c, d = api("GET", f"/api/timelines/{tl_id}/baked/status")
    if d.get("done"): break
ok("Bake completes", d.get("done"))
ok("No bake error", d.get("error") is None)
ok("Bake has progress 100", d.get("progress", 0) >= 99)

c, d = api("GET", f"/api/timelines/{tl_id}/baked")
ok("GET baked result", c == 200 and "fixtures" in d)
ok("Baked has fixtures", len(d.get("fixtures", {})) > 0)
ok("Baked has preview", "preview" in d)

# ═══════════════════════════════════════════════════════════════
section("12. PREVIEW DATA")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", f"/api/timelines/{tl_id}/baked/preview")
ok("GET preview", c == 200 and isinstance(d, dict))
ok("Preview has fixture data", len(d) > 0)

for fid, frames in d.items():
    ok(f"Preview {fid}: has frames ({len(frames)}s)", len(frames) > 0)
    if frames:
        ok(f"Preview {fid}: has string colors", isinstance(frames[0], list) and len(frames[0]) > 0)
        # Check first frame has valid RGB
        for rgb in frames[0]:
            ok(f"Preview {fid}: RGB is list of 3", isinstance(rgb, list) and len(rgb) == 3)
            ok(f"Preview {fid}: values 0-255", all(0 <= v <= 255 for v in rgb))
            break  # just check first string
    break  # just check first fixture

# ═══════════════════════════════════════════════════════════════
section("13. SYNC & START")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", f"/api/timelines/{tl_id}/baked/sync")
ok("Start sync", c == 200)

for _ in range(20):
    time.sleep(1)
    c, d = api("GET", f"/api/timelines/{tl_id}/sync/status")
    if d.get("done"): break
ok("Sync completes", d.get("done"))
ok("Sync has performers", "performers" in d)
ok("Sync has readyCount", "readyCount" in d)
ok("Sync has totalPerformers", "totalPerformers" in d)

# Verify performer status shape
for cid, p in d.get("performers", {}).items():
    ok(f"Performer {cid}: has name", "name" in p)
    ok(f"Performer {cid}: has status", "status" in p)
    ok(f"Performer {cid}: has stepsLoaded", "stepsLoaded" in p)
    ok(f"Performer {cid}: has verified", "verified" in p)
    break

# Start
c, d = api("POST", f"/api/timelines/{tl_id}/start")
ok("Start show", c == 200 and d.get("ok"))
ok("Start returns goEpoch", d.get("goEpoch", 0) > 0)
ok("Start returns started count", d.get("started", 0) >= 0)

# ═══════════════════════════════════════════════════════════════
section("14. RUNNING SHOW STATE")
# ═══════════════════════════════════════════════════════════════
time.sleep(3)
c, d = api("GET", "/api/settings")
ok("Settings: runnerRunning=True", d.get("runnerRunning") == True)
ok("Settings: activeTimeline matches", d.get("activeTimeline") == tl_id)
ok("Settings: has runnerStartEpoch", d.get("runnerStartEpoch", 0) > 0)

c, d = api("GET", f"/api/timelines/{tl_id}/status")
ok("Timeline status: running", d.get("running") == True)
ok("Timeline status: has elapsed", d.get("elapsed", -1) >= 0)
ok("Timeline status: has durationS", d.get("durationS") == 20)
ok("Timeline status: has name", d.get("name") == "QA Timeline")
ok("Timeline status: has loop", "loop" in d)

# Preview still available during playback
c, d = api("GET", f"/api/timelines/{tl_id}/baked/preview")
ok("Preview available during playback", c == 200 and len(d) > 0)

# ═══════════════════════════════════════════════════════════════
section("15. STOP SHOW")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", f"/api/timelines/{tl_id}/stop")
ok("Stop show", c == 200 and d.get("ok"))

c, d = api("GET", "/api/settings")
ok("Settings: runnerRunning=False after stop", d.get("runnerRunning") == False)
ok("Settings: activeTimeline=-1 after stop", d.get("activeTimeline") == -1)

# ═══════════════════════════════════════════════════════════════
section("16. PRESET LOAD → BAKE → PREVIEW CYCLE")
# ═══════════════════════════════════════════════════════════════
for preset_id in ["rainbow-across", "slow-fire", "disco"]:
    c, d = api("POST", "/api/show/preset", {"id": preset_id})
    ok(f"Load preset {preset_id}", c == 200 and d.get("ok"))
    ptl = d.get("timelineId")
    if ptl is None: continue

    c, d = api("POST", f"/api/timelines/{ptl}/bake")
    ok(f"Bake {preset_id}", c == 200)
    for _ in range(30):
        time.sleep(0.5)
        c, d = api("GET", f"/api/timelines/{ptl}/baked/status")
        if d.get("done"): break
    ok(f"Bake {preset_id} completes", d.get("done"))

    c, d = api("GET", f"/api/timelines/{ptl}/baked/preview")
    has_data = isinstance(d, dict) and len(d) > 0
    ok(f"Preview {preset_id} has data", has_data)
    if has_data:
        for fid, frames in d.items():
            has_color = any(any(sum(rgb) > 0 for rgb in sec) for sec in frames) if frames else False
            ok(f"Preview {preset_id}/{fid} has visible color", has_color)
            break

    api("DELETE", f"/api/timelines/{ptl}")

# ═══════════════════════════════════════════════════════════════
section("17. SECURITY")
# ═══════════════════════════════════════════════════════════════
c, d = api("POST", "/api/reset")
ok("Reset blocked without CSRF header", c == 403)

c, d = api("POST", "/api/shutdown")
ok("Shutdown blocked without CSRF header", c == 403)

# ═══════════════════════════════════════════════════════════════
section("18. HELP API")
# ═══════════════════════════════════════════════════════════════
for sec in ["layout", "timeline", "spatial-effects"]:
    c, d = api("GET", f"/api/help/{sec}")
    ok(f"Help {sec}", c == 200 and "html" in d and len(d["html"]) > 10)

# ═══════════════════════════════════════════════════════════════
section("19. WIFI & FIRMWARE")
# ═══════════════════════════════════════════════════════════════
c, d = api("GET", "/api/wifi")
ok("GET wifi", c == 200 and "ssid" in d)

c, d = api("GET", "/api/firmware/check")
ok("Firmware check", c == 200)

c, d = api("GET", "/api/firmware/registry")
ok("Firmware registry", c == 200)

# ═══════════════════════════════════════════════════════════════
section("20. CLEANUP")
# ═══════════════════════════════════════════════════════════════
api("DELETE", f"/api/timelines/{tl_id}")
api("DELETE", f"/api/actions/{act_id}")

# Restore settings
api("POST", "/api/settings", {"name": "SlyLED", "canvasW": 10000, "canvasH": 5000})

# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  RESULTS: {P} passed, {F} failed")
print(f"{'='*60}")
if ISSUES:
    print("\nFAILED TESTS:")
    for i in ISSUES:
        print(f"  ✗ {i}")
    sys.exit(1)
else:
    print("\n  ✓ ALL TESTS PASS")
