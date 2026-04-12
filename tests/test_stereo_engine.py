#!/usr/bin/env python3
"""
test_stereo_engine.py — Unit tests for #230: Stereo 3D reconstruction engine.

Tests camera registration, pixel_to_ray, ray-ray triangulation, and
multi-camera linear least squares triangulation.

Usage:
    python tests/test_stereo_engine.py        # run all
    python tests/test_stereo_engine.py -v     # verbose
"""

import sys, os, math

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))

import numpy as np
from stereo_engine import StereoEngine, _closest_approach

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


def approx(a, b, tol=50.0):
    return abs(a - b) <= tol


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── Initialization ───────────────────────────────────────────────────────

section('StereoEngine Init (#230)')

se = StereoEngine()
ok(se.camera_count == 0, 'Empty engine has 0 cameras')
ok(se.camera_ids() == [], 'Empty camera list')

# ── Camera registration from FOV ─────────────────────────────────────────

section('Camera Registration from FOV')

# Camera 1: left side of stage, looking right (+X)
se.add_camera_from_fov("cam1", fov_deg=60, frame_w=640, frame_h=480,
                        stage_pos=[0, 0, 2000])  # left wall, 2m high
ok(se.camera_count == 1, 'After add: 1 camera')

# Camera 2: right side of stage, looking left (-X)
se.add_camera_from_fov("cam2", fov_deg=60, frame_w=640, frame_h=480,
                        stage_pos=[6000, 0, 2000])  # right wall, 2m high
ok(se.camera_count == 2, 'After add: 2 cameras')
ok("cam1" in se.camera_ids() and "cam2" in se.camera_ids(), 'Both cameras registered')

# ── Camera registration from intrinsics + extrinsics ─────────────────────

section('Camera Registration from Intrinsics')

import cv2

se2 = StereoEngine()
# Camera at origin looking along +Z (default OpenCV convention)
rvec = [0.0, 0.0, 0.0]  # identity rotation
tvec = [0.0, 0.0, 0.0]  # at origin
intrinsics = {"fx": 500, "fy": 500, "cx": 320, "cy": 240}
extrinsics = {"rvec": rvec, "tvec": tvec}

se2.add_camera(0, intrinsics, extrinsics)
ok(se2.camera_count == 1, 'Camera registered from intrinsics')

# ── pixel_to_ray ─────────────────────────────────────────────────────────

section('pixel_to_ray (#230)')

# Camera at origin looking along +Z → center pixel should give forward ray
origin, direction = se2.pixel_to_ray(0, 320, 240)
ok(len(origin) == 3, 'Origin is 3-element')
ok(len(direction) == 3, 'Direction is 3-element')
# Origin should be near (0,0,0)
ok(approx(origin[0], 0, tol=1), f'Origin x ≈ 0: {origin[0]:.1f}')
ok(approx(origin[1], 0, tol=1), f'Origin y ≈ 0: {origin[1]:.1f}')
ok(approx(origin[2], 0, tol=1), f'Origin z ≈ 0: {origin[2]:.1f}')
# Center pixel direction should be mostly Z-forward
ok(abs(direction[2]) > 0.9, f'Center ray Z-forward: dz={direction[2]:.3f}')

# Off-center pixel should have non-zero X component
origin2, dir2 = se2.pixel_to_ray(0, 640, 240)  # right edge
ok(dir2[0] > 0, f'Right-edge pixel has positive X direction: {dir2[0]:.3f}')

# Error: unregistered camera
try:
    se2.pixel_to_ray("nonexistent", 0, 0)
    ok(False, 'Should raise for unregistered camera')
except ValueError:
    ok(True, 'Raises ValueError for unregistered camera')

# ── _closest_approach ────────────────────────────────────────────────────

section('Ray-Ray Closest Approach')

# Two rays that intersect exactly at (500, 500, 0)
o1 = [0, 0, 0]
d1 = [1, 1, 0]  # from origin along 45deg
o2 = [1000, 0, 0]
d2 = [-1, 1, 0]  # from (1000,0,0) along 135deg
result = _closest_approach(o1, d1, o2, d2)
ok(result is not None, 'Intersection found')
ok(approx(result["x"], 500), f'x ≈ 500: {result["x"]:.1f}')
ok(approx(result["y"], 500), f'y ≈ 500: {result["y"]:.1f}')
ok(result["error"] < 1.0, f'Error < 1mm (exact intersection): {result["error"]:.2f}')

# Parallel rays → should return None
result_par = _closest_approach([0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 0, 0])
ok(result_par is None, 'Parallel rays return None')

# Skew rays (don't intersect but have closest approach)
o3 = [0, 0, 0]
d3 = [1, 0, 0]
o4 = [0, 100, 500]
d4 = [0, 0, 1]
result_skew = _closest_approach(o3, d3, o4, d4)
ok(result_skew is not None, 'Skew rays return result')
ok(result_skew["error"] > 90, f'Skew error > 90mm: {result_skew["error"]:.1f}')

# ── triangulate_ray_ray ──────────────────────────────────────────────────

section('Two-Camera Triangulation (#230)')

# Set up two cameras at known positions pointing at a known 3D point
se3 = StereoEngine()
# Camera A at (0, 0, 0) looking along +Z
rvec_a = [0.0, 0.0, 0.0]
tvec_a = [0.0, 0.0, 0.0]
intrinsics_a = {"fx": 500, "fy": 500, "cx": 320, "cy": 240}
se3.add_camera("A", intrinsics_a, {"rvec": rvec_a, "tvec": tvec_a})

# Camera B at (2000, 0, 0) looking along +Z
tvec_b = [-2000.0, 0.0, 0.0]  # tvec = -R*pos, with R=I → tvec = -pos
intrinsics_b = {"fx": 500, "fy": 500, "cx": 320, "cy": 240}
se3.add_camera("B", intrinsics_b, {"rvec": [0, 0, 0], "tvec": tvec_b})

# Known 3D point at (1000, 0, 5000) — between both cameras, 5m away
target = np.array([1000, 0, 5000], dtype=np.float64)

# Project target into camera A
K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1]], dtype=np.float64)
proj_a = K @ target
px_a = proj_a[0] / proj_a[2]
py_a = proj_a[1] / proj_a[2]

# Project target into camera B (offset by -2000 in X)
target_b = target.copy()
target_b[0] -= 2000  # camera B sees the point shifted left
proj_b = K @ target_b
px_b = proj_b[0] / proj_b[2]
py_b = proj_b[1] / proj_b[2]

result = se3.triangulate_ray_ray("A", float(px_a), float(py_a),
                                  "B", float(px_b), float(py_b))
ok(result is not None, 'Triangulation returns result')
if result:
    ok(approx(result["x"], 1000, tol=100), f'Triangulated x ≈ 1000: {result["x"]:.1f}')
    ok(approx(result["y"], 0, tol=100), f'Triangulated y ≈ 0: {result["y"]:.1f}')
    ok(approx(result["z"], 5000, tol=200), f'Triangulated z ≈ 5000: {result["z"]:.1f}')
    ok(result["error"] < 100, f'Error < 100mm: {result["error"]:.1f}')

# ── Multi-camera triangulate ─────────────────────────────────────────────

section('Multi-Camera Triangulation (#230)')

# Same setup, add a third camera
tvec_c = [-1000.0, -1000.0, 0.0]
se3.add_camera("C", intrinsics_a, {"rvec": [0, 0, 0], "tvec": tvec_c})

target_c = target.copy()
target_c[0] -= 1000
target_c[1] -= 1000
proj_c = K @ target_c
px_c = proj_c[0] / proj_c[2]
py_c = proj_c[1] / proj_c[2]

result3 = se3.triangulate([
    ("A", float(px_a), float(py_a)),
    ("B", float(px_b), float(py_b)),
    ("C", float(px_c), float(py_c)),
])
ok(result3 is not None, '3-camera triangulation returns result')
if result3:
    ok(approx(result3["x"], 1000, tol=200), f'3-cam x ≈ 1000: {result3["x"]:.1f}')
    ok(approx(result3["z"], 5000, tol=500), f'3-cam z ≈ 5000: {result3["z"]:.1f}')

# Single observation → should return None
result1 = se3.triangulate([("A", 320, 240)])
ok(result1 is None, 'Single observation returns None')

# ── Degenerate cases ─────────────────────────────────────────────────────

section('Degenerate Cases (#230)')

# Same camera for both observations
se4 = StereoEngine()
se4.add_camera("X", intrinsics_a, {"rvec": [0, 0, 0], "tvec": [0, 0, 0]})
result_same = se4.triangulate([("X", 320, 240), ("X", 320, 240)])
ok(result_same is None or result_same["error"] < 1,
   'Same camera observations handled gracefully')

# ── Intrinsics from FOV ─────────────────────────────────────────────────

section('Intrinsics from FOV Formula (#230)')

# fov=60deg, w=640 → fx = 320/tan(30°) = 320/0.5774 = 554.3
fov = 60
w = 640
expected_fx = (w / 2.0) / math.tan(math.radians(fov / 2.0))
ok(approx(expected_fx, 554.3, tol=0.1), f'FOV 60° → fx ≈ 554.3: {expected_fx:.1f}')

fov90 = 90
expected_fx90 = (w / 2.0) / math.tan(math.radians(fov90 / 2.0))
ok(approx(expected_fx90, 320.0, tol=0.1), f'FOV 90° → fx ≈ 320.0: {expected_fx90:.1f}')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
