#!/usr/bin/env python3
"""
test_fixture_edit_position.py — Regression for the fixture-edit modal
pre-populating X/Y/Z from saved layout positions.

Launches its own SlyLED server on a test port, seeds a couple of
fixtures with known positions, opens the SPA's fixture edit modal for
each via Playwright, and compares the input values against the saved
values. Prior to the fix in fixtures.js:loadFixtures → /api/layout,
this was silently returning 0, 0, 0 for every fixture because
/api/fixtures doesn't include positions.
"""
import os
import sys
import threading
import time
import json
import urllib.request

PROJ = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(PROJ, 'desktop', 'shared'))

PORT = 18086
BASE = f"http://127.0.0.1:{PORT}"

# ── Start isolated server ───────────────────────────────────────────────
def _start_server():
    # Force a clean data dir under /tmp so we don't clobber user data
    import tempfile
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="slyled-test-")
    import parent_server
    parent_server.app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)

t = threading.Thread(target=_start_server, daemon=True)
t.start()

# Wait for the server to come up
deadline = time.time() + 20
up = False
while time.time() < deadline:
    try:
        r = urllib.request.urlopen(f"{BASE}/status", timeout=1).read()
        up = True
        break
    except Exception:
        time.sleep(0.3)
if not up:
    print("FAIL: server did not come up")
    sys.exit(1)

# ── Seed fixtures + layout ──────────────────────────────────────────────
def _post(path, payload):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())

def _put(path, payload):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())

def _get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=5).read())

# Create a DMX fixture and a camera
dmx = _post("/api/fixtures", {"name": "TestMover", "type": "point",
                               "fixtureType": "dmx", "dmxUniverse": 1,
                               "dmxStartAddr": 1, "dmxChannelCount": 4})
cam = _post("/api/fixtures", {"name": "TestCam", "type": "point",
                               "fixtureType": "camera",
                               "cameraIp": "10.0.0.99", "cameraIdx": 0,
                               "fovDeg": 90,
                               "resolutionW": 1920, "resolutionH": 1080})

# Set positions via /api/layout
_post("/api/layout", {
    "canvasH": 600, "canvasW": 600,
    "children": [
        {"id": dmx["id"], "x": 1500, "y": 200, "z": 1800},
        {"id": cam["id"], "x":  900, "y": 150, "z": 2100},
    ]})

saved = {dmx["id"]: (1500, 200, 1800), cam["id"]: (900, 150, 2100)}
print(f"Seeded: DMX id={dmx['id']} at {saved[dmx['id']]}  "
      f"Cam id={cam['id']} at {saved[cam['id']]}")

# ── Drive the SPA via Playwright ────────────────────────────────────────
from playwright.sync_api import sync_playwright

passed = 0
failed = 0
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{BASE}/?tab=setup", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)

    for fid, (sx, sy, sz) in saved.items():
        page.evaluate(f"editFixture({fid})")
        page.wait_for_selector("#fx-px", timeout=3000)
        px = int(page.input_value("#fx-px"))
        py = int(page.input_value("#fx-py"))
        pz = int(page.input_value("#fx-pz"))
        ok = (px, py, pz) == (sx, sy, sz)
        if ok:
            print(f"  [PASS] id={fid}: modal shows ({px},{py},{pz}) matches saved")
            passed += 1
        else:
            print(f"  [FAIL] id={fid}: modal shows ({px},{py},{pz}) "
                  f"but saved was ({sx},{sy},{sz})")
            failed += 1
        page.evaluate("closeModal()")
        page.wait_for_timeout(200)
    b.close()

print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
sys.exit(0 if failed == 0 else 1)
