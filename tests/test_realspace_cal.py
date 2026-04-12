#!/usr/bin/env python3
"""
test_realspace_cal.py — Unit tests for #246: All calibration in real-space coordinates.

Tests pixel_to_stage(), compute_depth_scale(), and integration of
stage-map homography into mover calibration grid samples.

Usage:
    python tests/test_realspace_cal.py        # run all
    python tests/test_realspace_cal.py -v     # verbose
"""

import sys, os, math

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))

import numpy as np
import mover_calibrator as mc

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


def approx(a, b, tol=10.0):
    return abs(a - b) <= tol


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── pixel_to_stage ───────────────────────────────────────────────────────

section('pixel_to_stage (#246)')

# Known homography: simple scaling (1px = 10mm, offset)
# H maps pixel (px, py) → stage (x_mm, y_mm)
# stage_x = 10 * px + 500, stage_y = 10 * py + 200
H = [[10.0, 0.0, 500.0],
     [0.0, 10.0, 200.0],
     [0.0, 0.0, 1.0]]

result = mc.pixel_to_stage(0, 0, H)
ok(result is not None, 'pixel_to_stage returns value')
ok(approx(result[0], 500), f'(0,0) → x≈500: {result[0]}')
ok(approx(result[1], 200), f'(0,0) → y≈200: {result[1]}')

result2 = mc.pixel_to_stage(100, 50, H)
ok(approx(result2[0], 1500), f'(100,50) → x≈1500: {result2[0]}')
ok(approx(result2[1], 700), f'(100,50) → y≈700: {result2[1]}')

# Center of 640x480 image
result3 = mc.pixel_to_stage(320, 240, H)
ok(approx(result3[0], 3700), f'(320,240) → x≈3700: {result3[0]}')
ok(approx(result3[1], 2600), f'(320,240) → y≈2600: {result3[1]}')

# Perspective homography (non-trivial)
H_persp = [[2.5, 0.1, 100.0],
           [0.05, 3.0, 50.0],
           [0.0001, 0.00005, 1.0]]
result4 = mc.pixel_to_stage(320, 240, H_persp)
ok(result4 is not None, 'Perspective homography returns result')
ok(isinstance(result4[0], float) and math.isfinite(result4[0]),
   f'Perspective result x finite: {result4[0]:.1f}')
ok(isinstance(result4[1], float) and math.isfinite(result4[1]),
   f'Perspective result y finite: {result4[1]:.1f}')

# ── compute_depth_scale ──────────────────────────────────────────────────

section('compute_depth_scale (#246)')

# Two markers at known positions, with synthetic depth map
markers_3d = [
    {"id": 0, "x": 500, "y": 500, "z": 0},
    {"id": 1, "x": 2500, "y": 500, "z": 0},
]
# Real distance = 2000mm
real_dist = 2000.0

# Markers at pixel positions: (100, 240) and (540, 240)
markers_px = [(100, 240), (540, 240)]

# Create synthetic depth map: uniform depth of 0.5 (relative)
depth_map = np.full((480, 640), 0.5, dtype=np.float32)

scale = mc.compute_depth_scale(markers_3d, markers_px, depth_map, 60, 640, 480)
ok(scale is not None, 'compute_depth_scale returns a value')
ok(isinstance(scale, float) and scale > 0, f'Scale > 0: {scale}')

# With too few markers
scale_bad = mc.compute_depth_scale(markers_3d[:1], markers_px[:1], depth_map, 60, 640, 480)
ok(scale_bad is None, 'Insufficient markers returns None')

# With varying depth (closer marker has higher depth value)
depth_map2 = np.full((480, 640), 0.5, dtype=np.float32)
depth_map2[:, :320] = 0.6  # left half closer
depth_map2[:, 320:] = 0.4  # right half farther
scale2 = mc.compute_depth_scale(markers_3d, markers_px, depth_map2, 60, 640, 480)
ok(scale2 is not None, 'Varying depth produces a scale')
ok(scale2 != scale, f'Varying depth gives different scale: {scale2} vs {scale}')

# ── Integration: pixel_to_stage with realistic homography ────────────────

section('Integration (#246)')

# Simulate a camera at (3000, 0, 2000) looking at a stage floor (Z=0)
# This is a simplified test — the real homography comes from solvePnP
H_floor = np.eye(3)
H_floor[0, 0] = 10.0   # scale X
H_floor[1, 1] = 10.0   # scale Y
H_floor[0, 2] = -2700   # offset X
H_floor[1, 2] = -1900   # offset Y

# pixel (320, 240) = center → should resolve to a valid stage position
cx, cy = mc.pixel_to_stage(320, 240, H_floor.tolist())
ok(cx is not None, 'Floor homography center pixel resolves')
ok(isinstance(cx, float) and math.isfinite(cx), f'Center x is finite: {cx:.0f}')
ok(isinstance(cy, float) and math.isfinite(cy), f'Center y is finite: {cy:.0f}')

# Two pixels should have proportional stage coordinates
p1 = mc.pixel_to_stage(200, 240, H_floor.tolist())
p2 = mc.pixel_to_stage(400, 240, H_floor.tolist())
ok(p2[0] > p1[0], f'Right pixel has larger stage X: {p2[0]:.0f} > {p1[0]:.0f}')
ok(abs(p1[1] - p2[1]) < 1.0, 'Same row has same stage Y')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
