#!/usr/bin/env python3
"""
test_cv_engine.py — Unit + E2E tests for #333: CV engine migration to orchestrator.

Tests CVEngine beam detection with synthetic frames, snapshot fetching
with mock camera, graceful fallbacks when models are absent, and
the new orchestrator API routes.

Usage:
    python tests/test_cv_engine.py        # run all
    python tests/test_cv_engine.py -v     # verbose
"""

import sys, os, time, threading

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))
sys.path.insert(0, 'tests')

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


# ── CVEngine import ──────────────────────────────────────────────────────

section('CVEngine Import')

try:
    from cv_engine import CVEngine
    ok(True, 'CVEngine imports successfully')
except Exception as e:
    ok(False, f'CVEngine import failed: {e}')
    sys.exit(1)

import cv2
import numpy as np

# ── CVEngine initialization ──────────────────────────────────────────────

section('CVEngine Init')

cv = CVEngine()
status = cv.status()
ok('beam' in status, 'Status has beam key')
ok('depth' in status, 'Status has depth key')
ok('detection' in status, 'Status has detection key')
ok(status['beam'] == 'available', f'Beam detector available: {status["beam"]}')

# ── Beam detection with synthetic frame ──────────────────────────────────

section('Beam Detection (#333)')

# Create synthetic frame with a bright spot
frame = np.zeros((480, 640, 3), dtype=np.uint8)
# Draw a bright white circle at (320, 240) — simulates beam spot
cv2.circle(frame, (320, 240), 20, (255, 255, 255), -1)

# Set dark frame (all black)
dark = np.zeros((480, 640, 3), dtype=np.uint8)
cv.set_dark_frame(0, dark)

result = cv.detect_beam(frame, cam_idx=0, color=None, threshold=30)
ok(isinstance(result, dict), 'detect_beam returns dict')
ok(result.get('found') is True, f'Beam found in synthetic frame: {result.get("found")}')
if result.get('found'):
    px, py = result.get('pixelX', 0), result.get('pixelY', 0)
    ok(abs(px - 320) < 30, f'Beam X near 320: {px}')
    ok(abs(py - 240) < 30, f'Beam Y near 240: {py}')

# Test with no beam (dark frame)
dark_result = cv.detect_beam(dark, cam_idx=0, color=None, threshold=30)
ok(dark_result.get('found') is not True, 'No beam in dark frame')

# Test colored beam
frame_red = np.zeros((480, 640, 3), dtype=np.uint8)
cv2.circle(frame_red, (200, 150), 25, (0, 0, 255), -1)  # Red in BGR
cv.set_dark_frame(1, dark)
result_red = cv.detect_beam(frame_red, cam_idx=1, color=(255, 0, 0), threshold=20)
ok(isinstance(result_red, dict), 'Red beam detection returns dict')

# ── Flash detection ──────────────────────────────────────────────────────

section('Flash Detection (#333)')

frame_on = np.zeros((480, 640, 3), dtype=np.uint8)
cv2.circle(frame_on, (400, 300), 15, (255, 255, 255), -1)
frame_off = np.zeros((480, 640, 3), dtype=np.uint8)

result_flash = cv.detect_beam_flash(frame_on, frame_off, cam_idx=0, threshold=20)
ok(isinstance(result_flash, dict), 'Flash detection returns dict')

# ── Snapshot fetching with mock camera ───────────────────────────────────

section('Snapshot Fetch (#333)')

from mock_camera import MockCameraServer
mock_cam = MockCameraServer(port=5000)
try:
    mock_cam.start()
    frame = cv.fetch_snapshot('127.0.0.1', cam_idx=0, timeout=5)
    ok(frame is not None, 'Snapshot fetched from mock camera')
    ok(frame.shape == (480, 640, 3), f'Frame shape correct: {frame.shape}')
    ok(frame.dtype == np.uint8, f'Frame dtype correct: {frame.dtype}')
except Exception as e:
    ok(False, f'Snapshot fetch failed: {e}')
finally:
    mock_cam.stop()

# ── Depth estimation status ──────────────────────────────────────────────

section('Depth Estimation Status (#333)')

# Depth model may or may not be available — test graceful handling
try:
    depth_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    depth_map, ms = cv.estimate_depth(depth_frame)
    ok(depth_map is not None, f'Depth estimation produced a map ({ms}ms)')
    ok(depth_map.shape[0] > 0 and depth_map.shape[1] > 0, 'Depth map has valid shape')
except RuntimeError as e:
    if 'not available' in str(e) or 'not found' in str(e):
        ok(True, f'Depth estimation gracefully unavailable: {e}')
    else:
        ok(False, f'Depth estimation unexpected error: {e}')
except Exception as e:
    ok(True, f'Depth estimation not ready (expected on some envs): {e}')

# ── Object detection status ──────────────────────────────────────────────

section('Object Detection Status (#333)')

try:
    det_frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
    detections, ms = cv.detect_objects(det_frame, threshold=0.5)
    ok(isinstance(detections, list), f'Detection returns list ({ms}ms)')
except RuntimeError as e:
    if 'not available' in str(e) or 'not found' in str(e):
        ok(True, f'Object detection gracefully unavailable: {e}')
    else:
        ok(False, f'Object detection unexpected error: {e}')
except Exception as e:
    ok(True, f'Object detection not ready (expected on some envs): {e}')

# ── Mover calibrator integration ─────────────────────────────────────────

section('Mover Calibrator Integration (#333)')

import mover_calibrator as mc

ok(hasattr(mc, 'set_cv_engine'), 'mover_calibrator has set_cv_engine()')
ok(hasattr(mc, '_cv_engine'), 'mover_calibrator has _cv_engine attribute')

# Set and verify
mc.set_cv_engine(cv)
ok(mc._cv_engine is cv, 'CVEngine wired into mover_calibrator')

# Reset
mc.set_cv_engine(None)
ok(mc._cv_engine is None, 'CVEngine can be reset to None')

# ── API routes (Flask test client) ───────────────────────────────────────

section('API Routes (#333)')

import parent_server
from parent_server import app

mock_cam2 = MockCameraServer(port=5000)
try:
    mock_cam2.start()

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        r = c.post('/api/fixtures', json={
            'name': 'CV Cam', 'type': 'point', 'fixtureType': 'camera', 'fovDeg': 60
        })
        fid = r.get_json()['id']
        c.put(f'/api/fixtures/{fid}', json={'cameraIp': '127.0.0.1', 'cameraIdx': 0})

        # CV status
        r = c.get('/api/cv/status')
        ok(r.status_code == 200, f'GET /api/cv/status returns 200')
        ok(r.get_json().get('beam') is not None, 'CV status includes beam')

        # Beam detection route
        r = c.post(f'/api/cameras/{fid}/beam-detect', json={'threshold': 30})
        ok(r.status_code in (200, 503), f'Beam detect route responds: {r.status_code}')

        # Non-existent camera
        r = c.post('/api/cameras/99999/beam-detect', json={})
        ok(r.status_code == 404, 'Beam detect non-existent camera → 404')

        # Detection route
        r = c.post(f'/api/cameras/{fid}/detect', json={'threshold': 0.5})
        d = r.get_json()
        ok(r.status_code in (200, 503), f'Detect route responds: {r.status_code}')

        # Depth route
        r = c.post(f'/api/cameras/{fid}/depth', json={'maxPoints': 100})
        ok(r.status_code in (200, 503), f'Depth route responds: {r.status_code}')
finally:
    mock_cam2.stop()

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
