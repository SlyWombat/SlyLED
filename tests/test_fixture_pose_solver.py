#!/usr/bin/env python3
"""test_fixture_pose_solver.py — #699.

Synthetic round-trip: pick a known fixture pose, generate marker
observations by computing the pan/tilt that aim beam at each marker
(using the same ray math the solver uses), then verify the solver
recovers the original pose.

Also tests the API endpoints (start / aim / observe / solve / apply /
cancel) via the Flask test client.
"""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


from fixture_pose_solver import (solve_fixture_pose, _pan_tilt_to_ray,
                                   _solve_3x3)


def _aim_at(fx_pos, marker_xyz, rotation_deg, pan_range, tilt_range):
    """Inverse: compute pan/tilt that aim beam from fx_pos at marker.
    Used by the test to synthesise observations the solver must recover.
    """
    dx = marker_xyz[0] - fx_pos[0]
    dy = marker_xyz[1] - fx_pos[1]
    dz = marker_xyz[2] - fx_pos[2]
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    aim = (dx / norm, dy / norm, dz / norm)
    # Inverse-transform out of mount rotation.
    if rotation_deg and any(rotation_deg):
        rx, ry, rz = (math.radians(float(a)) for a in rotation_deg)
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        # Transpose of XYZ Euler matrix.
        Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
        Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
        Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))

        def matmul(A, B):
            return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                                for j in range(3)) for i in range(3))

        def transpose(R):
            return tuple(tuple(R[j][i] for j in range(3)) for i in range(3))

        R = matmul(matmul(Rx, Ry), Rz)
        Rt = transpose(R)
        aim = (
            Rt[0][0] * aim[0] + Rt[0][1] * aim[1] + Rt[0][2] * aim[2],
            Rt[1][0] * aim[0] + Rt[1][1] * aim[1] + Rt[1][2] * aim[2],
            Rt[2][0] * aim[0] + Rt[2][1] * aim[1] + Rt[2][2] * aim[2],
        )
    pan_deg = math.degrees(math.atan2(aim[0], aim[1]))
    horiz = math.hypot(aim[0], aim[1])
    tilt_deg = math.degrees(math.atan2(-aim[2], horiz))
    pan_n = 0.5 + pan_deg / pan_range
    tilt_n = 0.5 + tilt_deg / tilt_range
    return max(0.0, min(1.0, pan_n)), max(0.0, min(1.0, tilt_n))


# ── Round-trip: known pose → synthetic observations → recover pose ─────

section('Solver round-trip — basement-rig geometry')

true_pose = (600.0, 0.0, 1500.0)   # operator-validated true Z ≈ 1500
markers = [
    (0, [500.0, 2280.0, 0.0]),
    (2, [1150.0, 2100.0, 0.0]),
    (5, [3120.0, 3090.0, 0.0]),
    (1, [2050.0, 3170.0, 0.0]),
]
observations = []
for mid, mxyz in markers:
    pan_n, tilt_n = _aim_at(true_pose, mxyz, [0, 0, 0], 540, 270)
    observations.append({
        "markerId": mid,
        "panNorm": pan_n,
        "tiltNorm": tilt_n,
        "markerXYZ": mxyz,
    })

result = solve_fixture_pose(observations,
                              fixture_rotation_deg=[0, 0, 0],
                              pan_range_deg=540, tilt_range_deg=270)
ok("error" not in result, f'solve succeeded (got {result.get("error", "ok")})')
ok(abs(result["x"] - true_pose[0]) < 1.0,
   f'recovered X {result["x"]} ≈ true {true_pose[0]} '
   f'(error {abs(result["x"] - true_pose[0]):.2f} mm)')
ok(abs(result["y"] - true_pose[1]) < 1.0,
   f'recovered Y {result["y"]} ≈ true {true_pose[1]} '
   f'(error {abs(result["y"] - true_pose[1]):.2f} mm)')
ok(abs(result["z"] - true_pose[2]) < 1.0,
   f'recovered Z {result["z"]} ≈ true {true_pose[2]} '
   f'(error {abs(result["z"] - true_pose[2]):.2f} mm)')
ok(result["residualRmsMm"] < 1.0,
   f'residual RMS < 1 mm (got {result["residualRmsMm"]} mm)')
ok(result["observationsUsed"] == 4, f'used all 4 markers')


# ── Edge cases ─────────────────────────────────────────────────────────

section('Solver edge cases')

# Single observation → error.
result = solve_fixture_pose([observations[0]],
                              fixture_rotation_deg=[0, 0, 0])
ok("error" in result, f'1 marker → error (got {result})')

# Empty list → error.
result = solve_fixture_pose([], fixture_rotation_deg=[0, 0, 0])
ok("error" in result, f'no markers → error (got {result})')

# Beams aimed up (tilt < 0.5) skip the floor-hit and don't contribute.
upward = [
    {"markerId": 0, "panNorm": 0.5, "tiltNorm": 0.3,
     "markerXYZ": [0, 1000, 0]},
    {"markerId": 1, "panNorm": 0.6, "tiltNorm": 0.2,
     "markerXYZ": [200, 2000, 0]},
]
result = solve_fixture_pose(upward, fixture_rotation_deg=[0, 0, 0])
ok("error" in result, f'all upward beams → error (got {result})')


# ── Noise robustness ──────────────────────────────────────────────────

section('Solver tolerates ±0.5° pan/tilt noise on real markers')

import random
random.seed(42)
true_pose = (600.0, 0.0, 1500.0)
noisy = []
for mid, mxyz in markers:
    pan_n, tilt_n = _aim_at(true_pose, mxyz, [0, 0, 0], 540, 270)
    pan_noise = random.uniform(-0.5, 0.5) / 540.0      # ±0.5° pan
    tilt_noise = random.uniform(-0.5, 0.5) / 270.0     # ±0.5° tilt
    noisy.append({
        "markerId": mid,
        "panNorm": pan_n + pan_noise,
        "tiltNorm": tilt_n + tilt_noise,
        "markerXYZ": mxyz,
    })
result = solve_fixture_pose(noisy, fixture_rotation_deg=[0, 0, 0],
                              pan_range_deg=540, tilt_range_deg=270)
# With ±0.5° noise on 4 markers at floor-rays of 1.3-3.0 m, expected
# pose error scales with tan(0.5°) × distance → ~30-50 mm worst case.
ok(abs(result["x"] - 600) < 100,
   f'noisy solve recovers X within 100 mm (got {result["x"]})')
ok(abs(result["z"] - 1500) < 100,
   f'noisy solve recovers Z within 100 mm (got {result["z"]})')


# ── _solve_3x3 unit test ───────────────────────────────────────────────

section('_solve_3x3 Gaussian elimination')

# Trivial diagonal system.
M = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
b = [3, 5, 7]
ok(_solve_3x3(M, b) == [3, 5, 7], 'identity solve')

# Non-trivial.
M = [[2, 1, 1], [1, 3, 2], [1, 0, 4]]
b = [4, 5, 6]
sol = _solve_3x3(M, b)
ok(sol is not None, 'non-trivial solve produced result')
# Verify M·sol = b.
for i in range(3):
    val = sum(M[i][j] * sol[j] for j in range(3))
    ok(abs(val - b[i]) < 1e-9, f'row {i} satisfies (got {val} vs {b[i]})')

# Singular system → None.
M_singular = [[1, 1, 1], [2, 2, 2], [3, 3, 3]]
ok(_solve_3x3(M_singular, [1, 2, 3]) is None,
   'singular system returns None')


# ── End-to-end via Flask test client ───────────────────────────────────

section('API endpoints — start / aim / observe / solve / apply / cancel')

import parent_server
from parent_server import app

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    # Seed a fixture + a couple of floor markers.
    rv = c.post('/api/fixtures', json={
        'name': 'Verify Pose Test', 'type': 'point', 'fixtureType': 'dmx',
        'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 12,
        'dmxProfileId': 'movinghead-150w-12ch',
    })
    fid = rv.get_json()['id']
    c.post(f'/api/layout', json={'children': [
        {'id': fid, 'x': 600, 'y': 0, 'z': 1500},
    ]})
    # Plant ArUco markers directly (bypasses /api/aruco/markers signature).
    parent_server._aruco_markers[:] = [
        {"id": 0, "name": "M0", "x": 500, "y": 2280, "z": 0,
         "rx": 0, "ry": 0, "rz": 0},
        {"id": 1, "name": "M1", "x": 2050, "y": 3170, "z": 0,
         "rx": 0, "ry": 0, "rz": 0},
        {"id": 2, "name": "M2", "x": 1150, "y": 2100, "z": 0,
         "rx": 0, "ry": 0, "rz": 0},
    ]

    # /start
    rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/start')
    j = rv.get_json()
    ok(rv.status_code == 200 and j['ok'], '/start → 200 ok')
    ok(len(j['floorMarkers']) == 3, f'3 floor markers returned')
    ok(j['currentPose']['x'] == 600, f'currentPose echoes layout')

    # /observe needs an active session — verify error path first.
    parent_server._fixture_pose_sessions.clear()
    rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/observe',
                json={'markerId': 0, 'panNorm': 0.5, 'tiltNorm': 0.6})
    ok(rv.status_code == 400,
       f'/observe without /start → 400 (got {rv.status_code})')

    # /start again, then post observations matching true pose at (600, 0, 1500).
    # The 150W profile declares tiltRange=180 (not 270), so synthesise with
    # the profile's actual range to keep the round-trip exact.
    c.post(f'/api/calibration/fixture/{fid}/verify-pose/start')
    for mid, mxyz in [(0, [500, 2280, 0]), (1, [2050, 3170, 0]),
                       (2, [1150, 2100, 0])]:
        pan_n, tilt_n = _aim_at((600, 0, 1500), mxyz, [0, 0, 0], 540, 180)
        rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/observe',
                    json={'markerId': mid, 'panNorm': pan_n,
                          'tiltNorm': tilt_n})
        ok(rv.status_code == 200, f'/observe marker {mid} → 200')

    # /solve
    rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/solve')
    j = rv.get_json()
    ok(rv.status_code == 200 and j['ok'],
       f'/solve → 200 ok (got {rv.status_code} {j})')
    ok(abs(j['x'] - 600) < 1.0,
       f'solver recovers X=600 (got {j["x"]})')
    ok(abs(j['z'] - 1500) < 1.0,
       f'solver recovers Z=1500 (got {j["z"]})')

    # /apply persists into the layout.
    rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/apply',
                json={'x': j['x'], 'y': j['y'], 'z': j['z']})
    ok(rv.status_code == 200, '/apply → 200')

    # Layout should reflect the new pose.
    pos = parent_server._fixture_position(fid)
    ok(abs(pos[0] - j['x']) < 1.0,
       f'layout updated to solved X (got {pos[0]})')

    # Session was popped by /apply — /cancel after that is a no-op.
    rv = c.post(f'/api/calibration/fixture/{fid}/verify-pose/cancel')
    ok(rv.status_code == 200, '/cancel after apply → 200 (idempotent)')


print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
