#!/usr/bin/env python3
"""Test stage preview emulator — validates sizing, fixtures, preview, and show lifecycle.

Usage: python tests/test_emulator.py [host:port]
"""
import json, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"
passed = 0; failed = 0; issues = []

def api(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data: req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items(): req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}
    except Exception as e:
        return 0, {"err": str(e)}

def ok(name, result):
    global passed, failed
    if result: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}"); issues.append(name)

print("\n=== Emulator & Sizing Tests ===\n")

# ── Stage/Canvas size sync ────────────────────────────────────────────────
print("--- Stage/Canvas Sync ---")

# Set stage via stage API
c, d = api("POST", "/api/stage", {"w": 6.0, "h": 3.0, "d": 4.0})
ok("POST /api/stage", d.get("ok"))

# Verify canvas synced
c, d = api("GET", "/api/settings")
ok("Settings canvasW synced from stage", d.get("canvasW") == 6000)
ok("Settings canvasH synced from stage", d.get("canvasH") == 3000)

c, d = api("GET", "/api/layout")
ok("Layout canvasW synced from stage", d.get("canvasW") == 6000)
ok("Layout canvasH synced from stage", d.get("canvasH") == 3000)

# Set canvas via settings API
c, d = api("POST", "/api/settings", {"canvasW": 8000, "canvasH": 4000})
ok("POST /api/settings", d.get("ok"))

# Verify stage synced
c, d = api("GET", "/api/stage")
ok("Stage w synced from settings", d.get("w") == 8.0)
ok("Stage h synced from settings", d.get("h") == 4.0)

# Reset to reasonable size
api("POST", "/api/stage", {"w": 3.0, "h": 2.0, "d": 2.0})

# ── Auto-create fixtures ─────────────────────────────────────────────────
print("\n--- Auto-create Fixtures ---")

# Add a test child if none exist
c, d = api("GET", "/api/children")
children = d if isinstance(d, list) else []
if not children:
    api("POST", "/api/children", {"ip": "192.168.10.99"})
    c, d = api("GET", "/api/children")
    children = d if isinstance(d, list) else []

ok("Has children", len(children) > 0)

# Clear fixtures
c, d = api("GET", "/api/fixtures")
for f in (d if isinstance(d, list) else []):
    api("DELETE", f"/api/fixtures/{f['id']}")

c, d = api("GET", "/api/fixtures")
ok("Fixtures cleared", isinstance(d, list) and len(d) == 0)

# Auto-create
c, d = api("POST", "/api/migrate/layout")
ok("Migrate layout", d.get("ok"))

c, d = api("GET", "/api/fixtures")
fixtures = d if isinstance(d, list) else []
ok("Fixtures auto-created", len(fixtures) > 0)

# ── Timeline + Bake + Preview ────────────────────────────────────────────
print("\n--- Bake & Preview ---")

# Load a preset
c, d = api("POST", "/api/show/preset", {"id": "rainbow-across"})
ok("Load preset", d.get("ok"))
tl_id = d.get("timelineId")

if tl_id is not None:
    # Bake
    c, d = api("POST", f"/api/timelines/{tl_id}/bake")
    ok("Bake started", d.get("ok") or d.get("message"))

    for _ in range(30):
        time.sleep(0.5)
        c, d = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if d.get("done"): break
    ok("Bake completes", d.get("done"))

    # Check baked result has fixtures (auto-created)
    c, d = api("GET", f"/api/timelines/{tl_id}/baked")
    baked_fixtures = d.get("fixtures", {})
    ok("Baked has fixtures", len(baked_fixtures) > 0)

    # Check preview
    c, d = api("GET", f"/api/timelines/{tl_id}/baked/preview")
    preview = d if isinstance(d, dict) else {}
    ok("Preview has data", len(preview) > 0)

    for fid, frames in preview.items():
        ok(f"Preview fix {fid}: has frames", len(frames) > 0)
        if frames:
            ok(f"Preview fix {fid}: has string colors", len(frames[0]) > 0)
            has_color = any(any(sum(rgb) > 0 for rgb in sec) for sec in frames)
            # Children with sc=0 won't have visible colors (no LEDs)
            fix_obj = next((f for f in fixtures if str(f["id"]) == fid), None)
            child_sc = 0
            if fix_obj:
                child_obj = next((c for c in children if c["id"] == fix_obj.get("childId")), None)
                if child_obj: child_sc = child_obj.get("sc", 0)
            if child_sc > 0:
                ok(f"Preview fix {fid}: has visible colors", has_color)
            else:
                ok(f"Preview fix {fid}: no LEDs (sc=0), skip color check", True)

    # ── Show lifecycle ────────────────────────────────────────────────
    print("\n--- Show Lifecycle ---")

    # Sync
    c, d = api("POST", f"/api/timelines/{tl_id}/baked/sync")
    ok("Sync started", d.get("ok"))

    for _ in range(20):
        time.sleep(1)
        c, d = api("GET", f"/api/timelines/{tl_id}/sync/status")
        if d.get("done"): break
    ok("Sync completes", d.get("done"))

    # Start
    c, d = api("POST", f"/api/timelines/{tl_id}/start")
    ok("Start", d.get("ok"))

    # Verify running state
    time.sleep(2)
    c, d = api("GET", "/api/settings")
    ok("Settings shows running", d.get("runnerRunning") == True)
    ok("Settings has activeTimeline", d.get("activeTimeline") == tl_id)
    ok("Settings has epoch", d.get("runnerStartEpoch") is not None and d["runnerStartEpoch"] > 0)

    # Preview should still be available
    c, d = api("GET", f"/api/timelines/{tl_id}/baked/preview")
    ok("Preview available during playback", isinstance(d, dict) and len(d) > 0)

    # Timeline status
    c, d = api("GET", f"/api/timelines/{tl_id}/status")
    ok("Timeline status running", d.get("running") == True)
    ok("Timeline status has elapsed", d.get("elapsed", -1) >= 0)

    # Stop
    c, d = api("POST", f"/api/timelines/{tl_id}/stop")
    ok("Stop", d.get("ok"))

    c, d = api("GET", "/api/settings")
    ok("Settings shows stopped", d.get("runnerRunning") == False)

    # Clean up
    api("DELETE", f"/api/timelines/{tl_id}")

# ── CSRF protection ──────────────────────────────────────────────────────
print("\n--- Security ---")

c, d = api("POST", "/api/reset")
ok("Reset blocked without header", c == 403)

c, d = api("POST", "/api/shutdown")
ok("Shutdown blocked without header", c == 403)

# SSRF
c, d = api("POST", "/api/children", {"ip": "8.8.8.8"})
ok("Public IP rejected", c == 400)

c, d = api("POST", "/api/children", {"ip": "not-valid"})
ok("Invalid IP rejected", c == 400)

print(f"\n=== Results: {passed} passed, {failed} failed ===")
if issues:
    print("FAILED:")
    for i in issues: print(f"  - {i}")
    sys.exit(1)
else:
    print("ALL TESTS PASS")
