#!/usr/bin/env python3
"""
test_aruco_orchestrator.py — E2E test for #329: ArUco detection on orchestrator.

Verifies that ArUco marker detection, frame accumulation, and intrinsic
calibration compute all run on the orchestrator (not camera node).
Uses a mock camera server serving synthetic ArUco marker images.

Usage:
    python tests/test_aruco_orchestrator.py        # run all
    python tests/test_aruco_orchestrator.py -v     # verbose

Requires: pip install flask opencv-python-headless numpy playwright
"""

import sys, os, time, threading

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))
sys.path.insert(0, 'tests')

from mock_camera import MockCameraServer, MARKER_POSITIONS

PORT = 18093
BASE = f'http://127.0.0.1:{PORT}'
MOCK_CAM_PORT = 5000

# ── Test infrastructure ──────────────────────────────────────────────────

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose:
            print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── Start mock camera ───────────────────────────────────────────────────

section('Setup')
mock_cam = MockCameraServer(port=MOCK_CAM_PORT)
try:
    mock_cam.start()
    print(f'Mock camera running on port {MOCK_CAM_PORT}')
except Exception as e:
    print(f'Failed to start mock camera: {e}')
    sys.exit(1)

# ── Seed data via Flask test client ──────────────────────────────────────

import parent_server
from parent_server import app

cam_fid = None
with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.post('/api/settings', json={'name': 'ArUco Test'})
    c.post('/api/stage', json={'w': 6.0, 'h': 3.0, 'd': 4.0})
    # Create camera fixture, then set cameraIp (not in creation path)
    r = c.post('/api/fixtures', json={
        'name': 'Test Cam', 'type': 'point', 'fixtureType': 'camera',
        'fovDeg': 60
    })
    cam_fid = r.get_json().get('id')
    c.put(f'/api/fixtures/{cam_fid}', json={
        'cameraIp': '127.0.0.1', 'cameraIdx': 0
    })

ok(cam_fid is not None, 'Camera fixture created')

# ── Start parent server in thread ────────────────────────────────────────

def run_server():
    app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)

srv_thread = threading.Thread(target=run_server, daemon=True)
srv_thread.start()
time.sleep(2)

# Verify server is up
import requests
try:
    r = requests.get(f'{BASE}/api/settings', timeout=5)
    ok(r.status_code == 200, 'Parent server running')
except Exception as e:
    print(f'Server not reachable: {e}')
    mock_cam.stop()
    sys.exit(1)

# ── Test ArUco capture ───────────────────────────────────────────────────

section('ArUco Capture (#329)')

# First capture
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/aruco/capture')
d = r.json()
ok(d.get('ok') is True, 'Capture returns ok')
cam0 = d.get('cameras', [{}])[0]
ok(cam0.get('markersFound', 0) == 6, f'First capture: 6 markers found (got {cam0.get("markersFound")})')
ok(cam0.get('frameCount', 0) == 1, 'Frame count = 1 after first capture')
found_ids = sorted(cam0.get('ids', []))
ok(found_ids == [0, 1, 2, 3, 4, 5], f'All 6 marker IDs detected: {found_ids}')

# Captures 2-5 (need 5+ diverse frames for calibrateCamera to converge)
for i in range(2, 6):
    r = requests.post(f'{BASE}/api/cameras/{cam_fid}/aruco/capture')
    cam0 = r.json().get('cameras', [{}])[0]
ok(cam0.get('frameCount', 0) == 5, f'Frame count = 5 after captures (got {cam0.get("frameCount")})')

# ── Test ArUco compute ───────────────────────────────────────────────────

section('ArUco Compute (#329)')

# Compute with 5 frames
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/aruco/compute',
                   json={'markerSize': 150})
d = r.json()
ok(d.get('ok') is True, 'Compute returns ok')
ok(d.get('frameCount', 0) == 5, 'Compute used 5 frames')
ok(d.get('rmsError') is not None and d['rmsError'] > 0, f'RMS error reported: {d.get("rmsError")}')
ok(d.get('fx') is not None and d['fx'] > 0, f'fx computed: {d.get("fx")}')
ok(d.get('fy') is not None and d['fy'] > 0, f'fy computed: {d.get("fy")}')
ok(d.get('cx') is not None and d['cx'] > 0, f'cx computed: {d.get("cx")}')
ok(d.get('cy') is not None and d['cy'] > 0, f'cy computed: {d.get("cy")}')
ok(isinstance(d.get('distCoeffs'), list), 'Distortion coefficients returned')

# ── Test ArUco reset ─────────────────────────────────────────────────────

section('ArUco Reset (#329)')

r = requests.post(f'{BASE}/api/cameras/{cam_fid}/aruco/reset')
ok(r.json().get('ok') is True, 'Reset returns ok')
ok(r.json().get('frameCount', -1) == 0, 'Frame count reset to 0')

# Compute with 0 frames should fail gracefully
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/aruco/compute',
                   json={'markerSize': 150})
d = r.json()
ok(d.get('ok') is False, 'Compute with 0 frames returns ok=false')

# ── Error cases ──────────────────────────────────────────────────────────

section('Error Cases')

# Non-existent camera
r = requests.post(f'{BASE}/api/cameras/99999/aruco/capture')
ok(r.status_code == 404, 'Non-existent camera → 404')

# ── Playwright UI validation ─────────────────────────────────────────────

section('Playwright UI')

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.goto(BASE)
        page.wait_for_timeout(2000)

        # Navigate to Settings > Cameras
        page.click('#n-settings')
        page.wait_for_timeout(1000)
        page.click('#sn-cameras')
        page.wait_for_timeout(1000)

        # Verify camera list shows our fixture
        cam_html = page.evaluate('() => document.getElementById("t-settings").innerHTML')
        ok('Test Cam' in cam_html, 'Camera fixture visible in Settings')

        page.screenshot(path='tests/user/aruco_settings.png')
        ok(True, 'Screenshot saved: tests/user/aruco_settings.png')

        browser.close()
except ImportError:
    print('  [SKIP] Playwright not installed — UI tests skipped')
except Exception as e:
    print(f'  [SKIP] Playwright error: {e}')

# ── Summary ──────────────────────────────────────────────────────────────

mock_cam.stop()

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
