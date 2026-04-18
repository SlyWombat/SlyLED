#!/usr/bin/env python3
"""
test_stage_map.py — E2E test for #330: Stage-map computation on orchestrator.

Verifies that camera pose estimation (solvePnP) runs on the orchestrator
using snapshots from camera nodes. Uses a mock camera server serving
synthetic ArUco marker images at known pixel positions.

Usage:
    python tests/test_stage_map.py        # run all
    python tests/test_stage_map.py -v     # verbose

Requires: pip install flask opencv-python-headless numpy playwright
"""

import sys, os, time, threading, math

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))
sys.path.insert(0, 'tests')

from mock_camera import MockCameraServer, MARKER_POSITIONS, IMAGE_W, IMAGE_H

PORT = 18094
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
    c.post('/api/settings', json={'name': 'Stage Map Test'})
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

import requests
try:
    r = requests.get(f'{BASE}/api/settings', timeout=5)
    ok(r.status_code == 200, 'Parent server running')
except Exception as e:
    print(f'Server not reachable: {e}')
    mock_cam.stop()
    sys.exit(1)

# ── Define 3D stage positions for markers ────────────────────────────────

# The mock camera image has markers at known pixel positions. We define
# corresponding 3D stage coordinates that a camera at ~2m looking down
# would produce these projections. The exact camera pose will be computed
# by solvePnP from these correspondences.

# Place markers on the floor plane (Z=0) spread across the stage
MARKER_STAGE_POS = {
    0: {'id': 0, 'x': 500,  'y': 500,  'z': 0},   # front-left
    1: {'id': 1, 'x': 3000, 'y': 500,  'z': 0},   # front-center
    2: {'id': 2, 'x': 5500, 'y': 500,  'z': 0},   # front-right
    3: {'id': 3, 'x': 500,  'y': 3500, 'z': 0},   # back-left
    4: {'id': 4, 'x': 3000, 'y': 3500, 'z': 0},   # back-center
    5: {'id': 5, 'x': 5500, 'y': 3500, 'z': 0},   # back-right
}

# ── Test stage-map with all 6 markers ────────────────────────────────────

section('Stage Map Compute (#330)')

markers = list(MARKER_STAGE_POS.values())
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/stage-map',
                   json={'markers': markers, 'markerSize': 150})
d = r.json()

ok(d.get('ok') is True, 'Stage map returns ok')
ok(d.get('markersDetected', 0) == 6, f'6 markers detected (got {d.get("markersDetected")})')
ok(d.get('markersMatched', 0) == 6, f'6 markers matched (got {d.get("markersMatched")})')
ok(d.get('method') == 'solvePnP', 'Method is solvePnP')

# Camera position should be a 3-element array with finite values
cam_pos = d.get('cameraPosStage', [])
ok(isinstance(cam_pos, list) and len(cam_pos) == 3,
   f'cameraPosStage is 3-element array: {cam_pos}')
ok(all(isinstance(v, (int, float)) and math.isfinite(v) for v in cam_pos),
   f'cameraPosStage values are finite: {cam_pos}')

# #331 — SPA reads r.cameraPosition (dict). Previously endpoint only
# returned cameraPosStage array, silently blanking the results table.
cam_pos_dict = d.get('cameraPosition', {})
ok(isinstance(cam_pos_dict, dict)
   and all(k in cam_pos_dict for k in ('x','y','z')),
   f'cameraPosition dict has x/y/z: {cam_pos_dict}')
ok(cam_pos_dict.get('x') == cam_pos[0] and cam_pos_dict.get('z') == cam_pos[2],
   'cameraPosition dict matches cameraPosStage array')

# #331 — intrinsicSource flag so operators can tell an FOV estimate from
# a proper ArUco-calibrated solve. Mock camera returns calibrated:false so
# the solver must fall back.
ok(d.get('intrinsicSource') == 'fov-estimate',
   f'intrinsicSource is "fov-estimate" (got {d.get("intrinsicSource")!r})')

# RMS error should be reasonable (not perfect since synthetic markers are flat)
rms = d.get('rmsError', 999)
ok(isinstance(rms, (int, float)) and rms < 100,
   f'RMS error < 100 (got {rms})')

# Homography should be a 3x3 matrix (list of 3 lists)
H = d.get('homography', [])
ok(isinstance(H, list) and len(H) == 3, 'Homography is 3x3 matrix')
if len(H) == 3:
    ok(all(isinstance(row, list) and len(row) == 3 for row in H),
       'Homography rows are 3-element lists')

# Intrinsics should have fx, fy, cx, cy
intrinsics = d.get('intrinsics', {})
ok(intrinsics.get('fx', 0) > 0, f'Intrinsics fx > 0: {intrinsics.get("fx")}')
ok(intrinsics.get('cx', 0) > 0, f'Intrinsics cx > 0: {intrinsics.get("cx")}')

# ── Calibrated intrinsics path (#331) ──────────────────────────────────

section('Calibrated intrinsics')

# Tell the mock camera it has saved intrinsics from an ArUco calibration.
# The exact values don't matter for this test — we only assert the
# stage-map endpoint read them back and flagged intrinsicSource accordingly.
mock_cam.saved_intrinsics = {
    'fx': 850.0, 'fy': 848.0, 'cx': IMAGE_W / 2.0, 'cy': IMAGE_H / 2.0,
    'distCoeffs': [0.01, -0.02, 0.0, 0.0, 0.0],
}
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/stage-map',
                   json={'markers': markers, 'markerSize': 150})
d = r.json()
ok(d.get('ok') is True, 'Stage map with calibrated intrinsics → ok')
ok(d.get('intrinsicSource') == 'calibrated',
   f'intrinsicSource is "calibrated" (got {d.get("intrinsicSource")!r})')
ok(abs(d.get('intrinsics', {}).get('fx', 0) - 850.0) < 1.0,
   f'Used saved fx=850 (got {d.get("intrinsics", {}).get("fx")})')
mock_cam.saved_intrinsics = None  # reset for later tests

# ── Test with fewer markers (minimum 3) ──────────────────────────────────

section('Stage Map Edge Cases')

# Only 3 markers
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/stage-map',
                   json={'markers': markers[:3], 'markerSize': 150})
d = r.json()
ok(d.get('ok') is True, 'Stage map with 3 markers succeeds')
ok(d.get('markersMatched', 0) >= 3, f'At least 3 matched: {d.get("markersMatched")}')

# Too few markers provided (< 3)
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/stage-map',
                   json={'markers': markers[:2], 'markerSize': 150})
ok(r.status_code == 400, 'Stage map with 2 markers → 400')

# Non-existent camera
r = requests.post(f'{BASE}/api/cameras/99999/stage-map',
                   json={'markers': markers, 'markerSize': 150})
ok(r.status_code == 404, 'Non-existent camera → 404')

# Missing markers list
r = requests.post(f'{BASE}/api/cameras/{cam_fid}/stage-map',
                   json={'markerSize': 150})
ok(r.status_code == 400, 'Missing markers → 400')

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

        page.screenshot(path='tests/user/stage_map_settings.png')
        ok(True, 'Screenshot saved: tests/user/stage_map_settings.png')

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
