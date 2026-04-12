"""
test_spatial_math.py — Regression tests for spatial math and coordinate transforms.

Stage coordinate system: X=width, Y=depth (toward audience), Z=height (floor to ceiling).

Validates: rotation matrices, Z-flip, depth ordering, ray-surface intersection,
grid interpolation, homography, inverse lookup, RANSAC floor detection.

Run: python -X utf8 tests/test_spatial_math.py
"""

import math
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

passed = 0
failed = 0

def ok(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}  {detail}')


def approx(a, b, tol=1.0):
    """Check two values are within tolerance."""
    return abs(a - b) <= tol


def approx_vec(a, b, tol=1.0):
    """Check two vectors are element-wise within tolerance."""
    return all(abs(ai - bi) <= tol for ai, bi in zip(a, b))


# ═══════════════════════════════════════════════════════════════════════
print('\n=== space_mapper.py — transform_points ===')
# ═══════════════════════════════════════════════════════════════════════

from space_mapper import transform_points

# Test 1: Identity transform (no rotation, origin camera)
# Camera: X-right=1000, Y-down=500, Z-forward=2000
# Stage:  X=1000, Y=2000 (cam Z→stage Y), Z=-500 (cam -Y→stage Z)
pts = [[1000, 500, 2000, 255, 0, 0]]
result = transform_points(pts, (0, 0, 0), [0, 0, 0])
ok('Identity X', approx(result[0][0], 1000), f'got {result[0][0]}')
ok('Identity Y (cam Z→stage Y)', approx(result[0][1], 2000), f'got {result[0][1]}')
ok('Identity Z (cam -Y→stage Z)', approx(result[0][2], -500), f'got {result[0][2]}')

# Test 2: Camera Y-down flip — camera Y-down=100 becomes stage Z=-100
pts = [[0, 100, 0, 0, 0, 0]]
result = transform_points(pts, (0, 0, 0), [0, 0, 0])
ok('Y-flip: cam (0,100,0) → stage Z negative', result[0][2] < 0, f'got Z={result[0][2]}')
ok('Y-flip: magnitude preserved', approx(abs(result[0][2]), 100), f'got {result[0][2]}')

# Test 3: Camera looking down 45° — Stage: X=width, Y=depth, Z=height
# Camera at (5000, 0, 3000) aimed at (5000, 3000, 0) — same X, 3m forward, floor
# Frame swap: cam(1000, 500, 2000) → stage-aligned(1000, 2000, -500)
# Pitch=-45°: RX(-45°) rotates Y/Z
# After RX(-45°): sx=1000, sy=2000*cos(-45°)-(-500)*sin(-45°)=2000*0.707-500*0.707≈1061
#                         sz=2000*sin(-45°)+(-500)*cos(-45°)=-2000*0.707-500*0.707≈-1768
# + (5000, 0, 3000) → (6000, 1061, 1232)
pts = [[1000, 500, 2000, 128, 128, 128]]
result = transform_points(pts, (5000, 0, 3000), [0, 0, 0],
                          cam_aim=(5000, 3000, 0))
ok('45° aim: X ≈ 6000', approx(result[0][0], 6000, 50), f'got {result[0][0]}')
ok('45° aim: Y > 0 (depth)', result[0][1] > 0, f'got {result[0][1]}')
ok('45° aim: Z > 0 (height)', result[0][2] > 0, f'got {result[0][2]}')

# Test 4: Pure yaw 90° — camera looking along +X
# Frame swap: cam(0,0,1000) → stage-aligned(0, 1000, 0)
# Yaw=90° (atan2(1000,0)=pi/2): RY(90°) rotates X/Y
# After RY(90°): wx=0*cos90+1000*sin90*0=1000*0... hmm
# Actually aim=(1000,0,0) from origin: yaw=atan2(1000,0)=pi/2
# RY(pi/2): wx=cos(pi/2)*0 + sin(pi/2)*... = 1000
pts = [[0, 0, 1000, 0, 0, 0]]  # 1m forward in camera space
result = transform_points(pts, (0, 0, 0), [0, 0, 0],
                          cam_aim=(1000, 0, 0))  # aim along +X
ok('Yaw 90°: forward maps to +X', abs(result[0][0]) > 500, f'X={result[0][0]}')
ok('Yaw 90°: Y near 0', approx(result[0][1], 0, 200), f'Y={result[0][1]}')

# Test 5: Rotation matrix is correct for RY*RX*RZ
# Verified against numpy: Ry(20°) @ Rx(10°) @ Rz(0°) applied to (1000, 0, 0)
# Expected: (cos20° * 1000, 0, -sin20° * 1000) = (940, 0, -342)
pts = [[1000, 0, 0, 0, 0, 0]]
result = transform_points(pts, (0, 0, 0), [10, 20, 0])
ok('RY*RX*RZ: X-axis point X ≈ 940', approx(result[0][0], 940, 20), f'got {result[0][0]}')

# Test 6: No NaN/Inf in output (filter test)
pts = [[float('nan'), 0, 0, 0, 0, 0], [0, float('inf'), 0, 0, 0, 0], [100, 200, 300, 0, 0, 0]]
result = transform_points(pts, (0, 0, 0), [0, 0, 0])
ok('NaN/Inf filtered: only 1 valid point', len(result) == 1, f'got {len(result)} points')

# Test 7: Output is float, not rounded int (#266)
pts = [[100.5, 200.3, 300.7, 0, 0, 0]]
result = transform_points(pts, (0, 0, 0), [0, 0, 0])
ok('Output is float (not rounded)', isinstance(result[0][0], float), f'type={type(result[0][0])}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== mover_calibrator.py — pan_tilt_to_ray ===')
# ═══════════════════════════════════════════════════════════════════════

from mover_calibrator import (compute_initial_aim, pan_tilt_to_ray,
                               ray_surface_intersect, build_grid, grid_inverse,
                               grid_3d_inverse, grid_3d_lookup, build_grid_3d)

# Test: Home position (0.5, 0.5) → forward along +Y (depth)
dx, dy, dz = pan_tilt_to_ray(0.5, 0.5)
ok('Home ray: dx=0', approx(dx, 0, 0.01), f'dx={dx}')
ok('Home ray: dy=1 (forward)', approx(dy, 1, 0.01), f'dy={dy}')
ok('Home ray: dz=0', approx(dz, 0, 0.01), f'dz={dz}')

# Test: Pan full right (pan=0.5 + 90°/540° ≈ 0.667) → should have +X component
dx, dy, dz = pan_tilt_to_ray(0.667, 0.5)
ok('Pan right: +X component', dx > 0.5, f'dx={dx}')

# Test: Tilt down (tilt > 0.5) → -Z component (height)
dx, dy, dz = pan_tilt_to_ray(0.5, 0.6)
ok('Tilt down: -Z component', dz < -0.1, f'dz={dz}')

# Test: Unit vector
dx, dy, dz = pan_tilt_to_ray(0.3, 0.7)
length = math.sqrt(dx*dx + dy*dy + dz*dz)
ok('Ray is unit vector', approx(length, 1.0, 0.001), f'length={length}')

# Test: Roundtrip compute_initial_aim → pan_tilt_to_ray
# Stage: X=width, Y=depth, Z=height.  mover at (2000, 500, 4000), target at (5000, 3000, 0)
mover = [2000, 500, 4000]
target = [5000, 3000, 0]
pan, tilt = compute_initial_aim(mover, target)
dx, dy, dz = pan_tilt_to_ray(pan, tilt)
# Direction from mover to target
ex = target[0] - mover[0]
ey = target[1] - mover[1]
ez = target[2] - mover[2]
elen = math.sqrt(ex*ex + ey*ey + ez*ez)
ex, ey, ez = ex/elen, ey/elen, ez/elen
dot = dx*ex + dy*ey + dz*ez
ok('Roundtrip: direction dot > 0.99', dot > 0.99, f'dot={dot:.4f}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== mover_calibrator.py — ray_surface_intersect ===')
# ═══════════════════════════════════════════════════════════════════════

# Test: Ray aimed at floor from height (floor at Z=0, origin at Z=4500)
surfaces = {"floor": {"z": 0, "normal": [0, 0, 1]}, "walls": [], "obstacles": []}
origin = (2000, 0, 4500)
direction = (0.3, 0.5, -0.8)  # going forward (+Y) and down (-Z)
# Normalize direction
dlen = math.sqrt(sum(d*d for d in direction))
direction = tuple(d/dlen for d in direction)
pt = ray_surface_intersect(origin, direction, surfaces)
ok('Floor hit: Z ≈ 0', pt is not None and approx(pt[2], 0, 5), f'pt={pt}')
ok('Floor hit: X > origin X', pt is not None and pt[0] > origin[0], f'X={pt[0] if pt else None}')

# Test: Ray aimed upward should NOT hit floor (positive Z = away from floor)
direction_up = (0, 0, 1)
pt = ray_surface_intersect(origin, direction_up, surfaces)
ok('Upward ray: no floor hit', pt is None, f'pt={pt}')

# Test: Ray aimed at wall (wall normal in XY plane)
surfaces_wall = {"floor": None, "walls": [{"normal": [0, 1, 0], "d": -3000}], "obstacles": []}
direction_fwd = (0, 1, 0)  # forward along +Y (depth)
pt = ray_surface_intersect((1000, 0, 2000), direction_fwd, surfaces_wall)
ok('Wall hit: Y = 3000', pt is not None and approx(pt[1], 3000, 5), f'pt={pt}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== mover_calibrator.py — build_grid + grid_inverse ===')
# ═══════════════════════════════════════════════════════════════════════

# Create synthetic linear mapping: px = pan * 1000, py = tilt * 500
samples = []
for p in [0.2, 0.3, 0.4, 0.5, 0.6]:
    for t in [0.3, 0.4, 0.5, 0.6]:
        samples.append((p, t, p * 1000, t * 500))

grid = build_grid(samples)
ok('Grid built', grid is not None)

if grid:
    from mover_calibrator import grid_lookup
    # Lookup a known point
    px, py = grid_lookup(grid, 0.4, 0.5)
    ok('Grid lookup (0.4, 0.5): px ≈ 400', approx(px, 400, 5), f'px={px}')
    ok('Grid lookup (0.4, 0.5): py ≈ 250', approx(py, 250, 5), f'py={py}')

    # Inverse: pixel (350, 200) → pan ≈ 0.35, tilt ≈ 0.4
    result = grid_inverse(grid, 350, 200)
    if result:
        ok('Grid inverse: pan ≈ 0.35', approx(result[0], 0.35, 0.02), f'pan={result[0]:.3f}')
        ok('Grid inverse: tilt ≈ 0.4', approx(result[1], 0.4, 0.02), f'tilt={result[1]:.3f}')
    else:
        ok('Grid inverse returned result', False, 'returned None')

# Test: build_grid with 7-tuple samples (collect_3d=True) (#264)
samples_7 = [(p, t, p*1000, t*500, p*2000, 0, t*3000) for p, t, _, _ in samples]
grid7 = build_grid(samples_7)
ok('Grid from 7-tuple samples: built', grid7 is not None)


# ═══════════════════════════════════════════════════════════════════════
print('\n=== surface_analyzer.py — floor detection ===')
# ═══════════════════════════════════════════════════════════════════════

from surface_analyzer import analyze_surfaces, beam_surface_check

# Test: Flat floor at Z=0 (Z=height in new convention)
import random
random.seed(42)
floor_pts = [[random.uniform(0, 5000), random.uniform(0, 5000), random.uniform(-50, 50), 128, 128, 128]
             for _ in range(200)]
# Add some ceiling points at Z≈4000
ceil_pts = [[random.uniform(0, 5000), random.uniform(0, 5000), random.uniform(3950, 4050), 128, 128, 128]
            for _ in range(50)]
surfaces = analyze_surfaces(floor_pts + ceil_pts)
ok('Floor detected', surfaces["floor"] is not None)
if surfaces["floor"]:
    ok('Floor Z ≈ 0', approx(surfaces["floor"]["z"], 0, 100), f'z={surfaces["floor"]["z"]}')
    ok('Floor normal near vertical (Z+)', surfaces["floor"]["normal"][2] > 0.95,
       f'nz={surfaces["floor"]["normal"][2]}')

# Test: Tilted floor (5° tilt) — RANSAC should still find it (#261)
random.seed(42)
tilt_rad = math.radians(5)
tilted_pts = []
for _ in range(300):
    x = random.uniform(0, 5000)
    y = random.uniform(0, 5000)
    z = math.tan(tilt_rad) * y + random.uniform(-30, 30)  # tilted along Y
    tilted_pts.append([x, y, z, 128, 128, 128])
surfaces = analyze_surfaces(tilted_pts)
ok('Tilted floor detected', surfaces["floor"] is not None)
if surfaces["floor"]:
    ok('Tilted floor normal still near-vertical (Z+)', surfaces["floor"]["normal"][2] > 0.95,
       f'normal={surfaces["floor"]["normal"]}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== surface_analyzer.py — ray-sphere obstacle intersection ===')
# ═══════════════════════════════════════════════════════════════════════

# Test: Ray hits obstacle (pos: X=3000, Y=3000 depth, Z=500 height)
surfaces_obs = {
    "floor": {"z": 0, "normal": [0, 0, 1], "d": 0},
    "walls": [],
    "obstacles": [{"pos": [3000, 3000, 500], "size": [500, 500, 1000], "label": "pillar"}]
}
# Ray aimed directly at obstacle from (1000, 0, 2000)
direction = (3000 - 1000, 3000 - 0, 500 - 2000)
dlen = math.sqrt(sum(d*d for d in direction))
direction = tuple(d/dlen for d in direction)
result = beam_surface_check(surfaces_obs, (1000, 0, 2000), direction)
ok('Ray hits obstacle', result is not None and result["surface"] == "pillar",
   f'result={result}')

# Test: Ray aimed AWAY from obstacle should NOT hit (#260)
direction_away = (-1, 0, 0)  # pointing left, obstacle is to the right
result = beam_surface_check(surfaces_obs, (1000, 0, 2000), direction_away)
no_pillar = result is None or result["surface"] != "pillar"
ok('Ray away from obstacle: no hit', no_pillar, f'result={result}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== parent_server.py — _inverse_axis_lookup ===')
# ═══════════════════════════════════════════════════════════════════════

# Import the function
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))
# Can't easily import from parent_server without Flask, so test inline
# Horizontal plane is XY (width+depth), not XZ
def _inverse_axis_lookup(mapping, target_x, target_y):
    sx, bx = mapping["intercept_x"], mapping["slope_x"]
    sy, by = mapping["intercept_y"], mapping["slope_y"]
    vals, weights = [], []
    if abs(bx) > 0.001:
        vals.append((target_x - sx) / bx)
        weights.append(abs(bx))
    if abs(by) > 0.001:
        vals.append((target_y - sy) / by)
        weights.append(abs(by))
    if not vals:
        return 0.5
    wsum = sum(v * w for v, w in zip(vals, weights))
    return max(0.0, min(1.0, wsum / sum(weights)))

# Test: Dominant X axis (large slope_x, small slope_y)
mapping = {"intercept_x": 0, "slope_x": 10000, "intercept_y": 500, "slope_y": 100}
# Target at X=5000 → norm = 5000/10000 = 0.5
# Target at Y=550 → norm = (550-500)/100 = 0.5
# Both agree, result should be 0.5
result = _inverse_axis_lookup(mapping, 5000, 550)
ok('Weighted lookup: both agree → 0.5', approx(result, 0.5, 0.01), f'got {result}')

# Test: Conflicting axes — X says 0.3, Y says 0.8. X slope dominates.
# X: norm = 3000/10000 = 0.3
# Y: norm = (580-500)/100 = 0.8
# Weighted: (0.3 * 10000 + 0.8 * 100) / (10000 + 100) = 3080/10100 ≈ 0.305
result = _inverse_axis_lookup(mapping, 3000, 580)
ok('Weighted lookup: X dominates', approx(result, 0.305, 0.02), f'got {result:.4f}')
# Old unweighted would give (0.3 + 0.8) / 2 = 0.55 — wrong!
ok('Weighted != unweighted', abs(result - 0.55) > 0.1, f'would have been 0.55, got {result:.4f}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== grid_3d_inverse convergence check ===')
# ═══════════════════════════════════════════════════════════════════════

# Create a 3D grid where wx = pan*5000, wy = tilt*3000, wz = 0
# Stage: X=width, Y=depth, Z=height. Convergence on XY (horizontal).
samples_3d = []
for p in [0.2, 0.3, 0.4, 0.5, 0.6]:
    for t in [0.3, 0.4, 0.5, 0.6]:
        samples_3d.append((p, t, p*1000, t*500, p*5000, t*3000, 0))
grid3d = build_grid_3d(samples_3d)
ok('3D grid built', grid3d is not None)

if grid3d:
    # Inverse: target (2000, 1350, 0) → pan ≈ 0.4, tilt ≈ 0.45
    pan, tilt = grid_3d_inverse(grid3d, 2000, 1350, 0)
    ok('3D inverse: pan ≈ 0.4', approx(pan, 0.4, 0.03), f'pan={pan:.3f}')
    ok('3D inverse: tilt ≈ 0.45', approx(tilt, 0.45, 0.03), f'tilt={tilt:.3f}')

    # Verify convergence uses XY only (#265)
    # Target with large Z offset but matching XY — should still converge
    pan2, tilt2 = grid_3d_inverse(grid3d, 2000, 1350, 9999)  # Z=9999 (huge Z error)
    ok('3D inverse ignores Z: pan still ≈ 0.4', approx(pan2, 0.4, 0.05), f'pan={pan2:.3f}')


# ═══════════════════════════════════════════════════════════════════════
print('\n=== Rotation matrix verification ===')
# ═══════════════════════════════════════════════════════════════════════

# Verify RY*RX*RZ matches numpy computation
try:
    import numpy as np

    rx, ry, rz = math.radians(10), math.radians(20), math.radians(30)
    Rx = np.array([[1,0,0],[0,math.cos(rx),-math.sin(rx)],[0,math.sin(rx),math.cos(rx)]])
    Ry = np.array([[math.cos(ry),0,math.sin(ry)],[0,1,0],[-math.sin(ry),0,math.cos(ry)]])
    Rz = np.array([[math.cos(rz),-math.sin(rz),0],[math.sin(rz),math.cos(rz),0],[0,0,1]])
    R_expected = Ry @ Rx @ Rz

    # Build from code's formula
    cos_rx, sin_rx = math.cos(rx), math.sin(rx)
    cos_ry, sin_ry = math.cos(ry), math.sin(ry)
    cos_rz, sin_rz = math.cos(rz), math.sin(rz)
    R_code = np.array([
        [cos_ry*cos_rz + sin_ry*sin_rx*sin_rz, -cos_ry*sin_rz + sin_ry*sin_rx*cos_rz, sin_ry*cos_rx],
        [cos_rx*sin_rz, cos_rx*cos_rz, -sin_rx],
        [-sin_ry*cos_rz + cos_ry*sin_rx*sin_rz, sin_ry*sin_rz + cos_ry*sin_rx*cos_rz, cos_ry*cos_rx],
    ])
    ok('Rotation matrix matches numpy RY@RX@RZ', np.allclose(R_expected, R_code, atol=1e-10))

    # Verify it's a proper rotation (det = 1, R^T @ R = I)
    ok('Rotation det = 1', approx(np.linalg.det(R_code), 1.0, 1e-10))
    ok('Rotation orthogonal', np.allclose(R_code.T @ R_code, np.eye(3), atol=1e-10))
except ImportError:
    print('  [SKIP] numpy not available — rotation matrix test skipped')


# ═══════════════════════════════════════════════════════════════════════
print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests')
if failed:
    sys.exit(1)
