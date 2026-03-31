#!/usr/bin/env python3
"""Test show preview emulator — validates all presets produce correct bake preview data.

Usage: python tests/test_preview.py [host:port]
"""
import json, sys, time, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"
passed = 0; failed = 0

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
    global passed, failed
    if result: passed += 1; print(f"  PASS {name}")
    else: failed += 1; print(f"  FAIL {name}")

# Ensure fixtures exist
api("POST", "/api/migrate/layout")

# Get existing presets
presets = api("GET", "/api/show/presets")
print(f"\n=== Testing {len(presets)} preset shows ===\n")

for preset in presets:
    pid = preset["id"]
    pname = preset["name"]
    print(f"--- {pname} ({pid}) ---")

    # Load preset
    r = api("POST", "/api/show/preset", {"id": pid})
    ok(f"{pname}: load preset", r.get("ok"))
    tl_id = r.get("timelineId")
    if not tl_id:
        print(f"  SKIP — no timeline created")
        continue

    # Verify timeline structure
    tl = api("GET", f"/api/timelines/{tl_id}")
    ok(f"{pname}: has tracks", len(tl.get("tracks", [])) > 0)
    ok(f"{pname}: has clips", sum(len(t.get("clips",[])) for t in tl.get("tracks",[])) > 0)
    ok(f"{pname}: has allPerformers", any(t.get("allPerformers") for t in tl.get("tracks",[])))

    # Bake
    r = api("POST", f"/api/timelines/{tl_id}/bake")
    ok(f"{pname}: bake started", r.get("ok") or r.get("message"))

    # Poll bake completion
    for _ in range(30):
        time.sleep(0.5)
        bs = api("GET", f"/api/timelines/{tl_id}/baked/status")
        if bs.get("done"): break
    ok(f"{pname}: bake completes", bs.get("done"))
    if bs.get("error"):
        print(f"  ERROR: {bs['error']}")
        continue

    # Check baked result
    baked = api("GET", f"/api/timelines/{tl_id}/baked")
    fixtures_baked = baked.get("fixtures", {})
    ok(f"{pname}: has baked fixtures", len(fixtures_baked) > 0)

    for fid, fd in fixtures_baked.items():
        segs = fd.get("segments", [])
        ok(f"{pname}: fixture {fid} has segments", len(segs) > 0)
        # Check segments have valid types
        valid_types = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
        all_valid = all(s.get("type") in valid_types for s in segs)
        ok(f"{pname}: fixture {fid} valid action types", all_valid)
        # Check at least some non-blackout segments
        has_color = any(s.get("type") != 0 for s in segs)
        ok(f"{pname}: fixture {fid} has non-blackout", has_color)

    # Check preview data
    preview = api("GET", f"/api/timelines/{tl_id}/baked/preview")
    ok(f"{pname}: has preview data", isinstance(preview, dict) and len(preview) > 0)

    for fid, frames in preview.items():
        ok(f"{pname}: preview fix {fid} has frames", len(frames) > 0)
        # Check frames have per-string colors
        if frames:
            ok(f"{pname}: preview fix {fid} has string colors", len(frames[0]) > 0)
            # Check that at least some seconds have non-zero colors
            has_visible = any(
                any(sum(rgb) > 0 for rgb in sec)
                for sec in frames
            )
            ok(f"{pname}: preview fix {fid} has visible colors", has_visible)

            # Verify color values are in valid range
            all_valid_colors = all(
                all(0 <= c <= 255 for rgb in sec for c in rgb)
                for sec in frames
            )
            ok(f"{pname}: preview fix {fid} colors in 0-255 range", all_valid_colors)

    # Clean up (delete the timeline)
    api("DELETE", f"/api/timelines/{tl_id}")
    print()

print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed:
    print("SOME TESTS FAILED")
    sys.exit(1)
else:
    print("ALL TESTS PASS")
