"""
test_mover_calibration.py — Unit tests for the mover calibration pipeline.

Pure math tests only — no network calls, no hardware.
Module under test: desktop/shared/mover_calibrator.py

Run: python -X utf8 tests/test_mover_calibration.py
"""

import math
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from mover_calibrator import (
    compute_initial_aim,
    compute_aim_with_orientation,
    pan_tilt_to_ray,
    ray_surface_intersect,
    build_grid,
    grid_lookup,
    grid_inverse,
    build_grid_3d,
    grid_3d_lookup,
    grid_3d_inverse,
    _set_mover_dmx,
)

passed = 0
failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  [PASS] {name}')
    else:
        failed += 1
        print(f'  [FAIL] {name}  {detail}')


def approx(a, b, tol=0.01):
    """Check two values are within tolerance."""
    return abs(a - b) <= tol


# =====================================================================
print('\n=== compute_initial_aim ===')
# =====================================================================

# Forward target (+Y direction): pan should be 0.5
# Mover at origin, target straight ahead at Y=3000
pan, tilt = compute_initial_aim([0, 0, 3000], [0, 3000, 3000])
check('Forward target: pan=0.5', approx(pan, 0.5),
      f'pan={pan:.4f}')
check('Forward target: tilt=0.5 (same height)', approx(tilt, 0.5),
      f'tilt={tilt:.4f}')

# Target to the right (+X): pan > 0.5
pan, tilt = compute_initial_aim([0, 0, 2000], [3000, 0, 2000])
check('Target right: pan > 0.5', pan > 0.5,
      f'pan={pan:.4f}')

# Target to the left (-X): pan < 0.5
pan, tilt = compute_initial_aim([0, 0, 2000], [-3000, 0, 2000])
check('Target left: pan < 0.5', pan < 0.5,
      f'pan={pan:.4f}')

# Target below (lower Z): tilt > 0.5 (tilt increases downward)
# Mover at Z=4000, target at Z=0 (floor), same XY
pan, tilt = compute_initial_aim([2000, 2000, 4000], [2000, 4000, 0])
check('Target below: tilt > 0.5', tilt > 0.5,
      f'tilt={tilt:.4f}')

# Target directly above (higher Z): tilt < 0.5
# Mover at Z=0, target at Z=3000 (ceiling), some forward offset to have dist_xy > 0
pan, tilt = compute_initial_aim([2000, 2000, 0], [2000, 2001, 3000])
check('Target above: tilt < 0.5', tilt < 0.5,
      f'tilt={tilt:.4f}')

# Target behind (-Y direction): pan near 0 or 1
# atan2(0, -3000) = pi, pan = 0.5 + 180/540 = 0.5 + 0.333 = 0.833
pan, tilt = compute_initial_aim([0, 3000, 2000], [0, 0, 2000])
check('Target behind: pan near 0.83', approx(pan, 0.833, 0.02),
      f'pan={pan:.4f}')

# Clamp to [0, 1] — extreme right angle beyond pan_range
# With pan_range=90 (very small), forward+right 45deg = 0.5 + 45/90 = 1.0
pan, tilt = compute_initial_aim([0, 0, 0], [5000, 5000, 0], pan_range=90, tilt_range=90)
check('Clamp pan: 0 <= pan <= 1', 0.0 <= pan <= 1.0,
      f'pan={pan:.4f}')
check('Clamp tilt: 0 <= tilt <= 1', 0.0 <= tilt <= 1.0,
      f'tilt={tilt:.4f}')

# Pan symmetry: targets equidistant left and right should be symmetric around 0.5
pan_r, _ = compute_initial_aim([0, 0, 0], [1000, 3000, 0])
pan_l, _ = compute_initial_aim([0, 0, 0], [-1000, 3000, 0])
check('Pan symmetry: left+right average ~0.5', approx((pan_r + pan_l) / 2, 0.5, 0.001),
      f'avg={(pan_r + pan_l) / 2:.4f}')


# =====================================================================
print('\n=== compute_aim_with_orientation ===')
# =====================================================================

# Identity orientation: panSign=1, tiltSign=-1, offsets=0.5
# This is the standard convention matching compute_initial_aim
identity_orient = {'panSign': 1, 'tiltSign': -1, 'panOffset': 0.5, 'tiltOffset': 0.5}

# Forward target: should give pan=0.5, tilt near 0.5
pan, tilt = compute_aim_with_orientation([0, 0, 3000], [0, 3000, 3000],
                                          identity_orient, pan_range=540, tilt_range=180)
check('Identity orient: forward pan=0.5', approx(pan, 0.5),
      f'pan={pan:.4f}')
# Same height → tilt_deg=0 → tilt = 0.5 + (-1)*0/180 = 0.5
check('Identity orient: level tilt=0.5', approx(tilt, 0.5),
      f'tilt={tilt:.4f}')

# Inverted pan mount (panSign=-1): right target should give pan < 0.5
inverted_pan = {'panSign': -1, 'tiltSign': -1, 'panOffset': 0.5, 'tiltOffset': 0.5}
pan, tilt = compute_aim_with_orientation([0, 0, 2000], [3000, 3000, 2000],
                                          inverted_pan, pan_range=540, tilt_range=180)
check('Inverted pan: right target → pan < 0.5', pan < 0.5,
      f'pan={pan:.4f}')

# Offset shift: panOffset=0.3 instead of 0.5 shifts entire range
offset_orient = {'panSign': 1, 'tiltSign': -1, 'panOffset': 0.3, 'tiltOffset': 0.5}
pan, tilt = compute_aim_with_orientation([0, 0, 3000], [0, 3000, 3000],
                                          offset_orient, pan_range=540, tilt_range=180)
check('Pan offset 0.3: forward → pan=0.3', approx(pan, 0.3),
      f'pan={pan:.4f}')

# Tilt offset shift
tilt_offset_orient = {'panSign': 1, 'tiltSign': -1, 'panOffset': 0.5, 'tiltOffset': 0.7}
pan, tilt = compute_aim_with_orientation([0, 0, 3000], [0, 3000, 3000],
                                          tilt_offset_orient, pan_range=540, tilt_range=180)
check('Tilt offset 0.7: level → tilt=0.7', approx(tilt, 0.7),
      f'tilt={tilt:.4f}')

# Clamp: extreme target that would push beyond [0,1]
extreme_orient = {'panSign': 1, 'tiltSign': -1, 'panOffset': 0.95, 'tiltOffset': 0.05}
pan, tilt = compute_aim_with_orientation([0, 0, 4000], [5000, 5000, 0],
                                          extreme_orient, pan_range=180, tilt_range=90)
check('Clamp with orientation: pan <= 1.0', pan <= 1.0,
      f'pan={pan:.4f}')
check('Clamp with orientation: tilt >= 0.0', tilt >= 0.0,
      f'tilt={tilt:.4f}')

# Round-trip: use orientation to aim, then verify direction vector
# Mover at (1000, 500, 3000), target at (3000, 2000, 0)
mover = [1000, 500, 3000]
target = [3000, 2000, 0]
orient_rt = {'panSign': 1, 'tiltSign': -1, 'panOffset': 0.5, 'tiltOffset': 0.5}
pan, tilt = compute_aim_with_orientation(mover, target, orient_rt,
                                          pan_range=540, tilt_range=270)
check('Orientation round-trip: pan in range', 0.0 <= pan <= 1.0,
      f'pan={pan:.4f}')
check('Orientation round-trip: tilt in range', 0.0 <= tilt <= 1.0,
      f'tilt={tilt:.4f}')


# =====================================================================
print('\n=== pan_tilt_to_ray (extended) ===')
# =====================================================================

# Extreme pan = 0.0: max pan left
# pan_deg = (0.0 - 0.5) * 540 = -270
dx, dy, dz = pan_tilt_to_ray(0.0, 0.5)
check('Extreme pan=0.0: unit vector',
      approx(math.sqrt(dx*dx + dy*dy + dz*dz), 1.0, 0.001),
      f'len={math.sqrt(dx*dx + dy*dy + dz*dz):.4f}')
# -270 deg pan means pointing +Y rotated -270 deg = pointing +X (or equivalently -90 from forward)
# sin(-270) = sin(90) = 1, cos(-270) = cos(90) = 0 → but -270 wraps:
# sin(-270 deg) = 1.0, cos(-270 deg) ~ 0 → dx = sin(-270)*cos(0) = 1.0
# Actually: sin(-270) = sin(90) = 1 (since sin(-270 + 360) = sin(90))
check('Extreme pan=0.0: has horizontal component', abs(dx) > 0.5,
      f'dx={dx:.4f}')

# Extreme pan = 1.0: max pan right
dx, dy, dz = pan_tilt_to_ray(1.0, 0.5)
check('Extreme pan=1.0: unit vector',
      approx(math.sqrt(dx*dx + dy*dy + dz*dz), 1.0, 0.001),
      f'len={math.sqrt(dx*dx + dy*dy + dz*dz):.4f}')

# Extreme tilt = 0.0: max tilt up
# tilt_deg = (0.0 - 0.5) * 270 = -135
dx, dy, dz = pan_tilt_to_ray(0.5, 0.0)
check('Extreme tilt=0.0: has vertical component', abs(dz) > 0.5,
      f'dz={dz:.4f}')
# tilt_deg = -135 → sin(-135) < 0 → dz = -sin(-135) = sin(135) > 0 → pointing up
check('Extreme tilt=0.0: pointing upward (dz > 0)', dz > 0,
      f'dz={dz:.4f}')

# Extreme tilt = 1.0: max tilt down
dx, dy, dz = pan_tilt_to_ray(0.5, 1.0)
check('Extreme tilt=1.0: pointing downward (dz < 0)', dz < 0,
      f'dz={dz:.4f}')

# Backward direction: pan=0.5 + 180/540 = 0.833...
dx, dy, dz = pan_tilt_to_ray(0.833, 0.5)
# pan_deg = 0.333 * 540 = 180 → sin(180)=0, cos(180)=-1 → dy=-1
check('Backward pan=0.833: dy < 0 (backward)', dy < -0.5,
      f'dy={dy:.4f}')

# All extreme values produce unit vectors
for p, t in [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5)]:
    dx, dy, dz = pan_tilt_to_ray(p, t)
    length = math.sqrt(dx*dx + dy*dy + dz*dz)
    check(f'Unit vector at pan={p} tilt={t}', approx(length, 1.0, 0.001),
          f'len={length:.4f}')


# =====================================================================
print('\n=== ray_surface_intersect (extended) ===')
# =====================================================================

# Wall intersection: wall at Y=5000, ray going forward
surfaces_wall = {
    'floor': None,
    'walls': [{'normal': [0, 1, 0], 'd': -5000}],
    'obstacles': []
}
pt = ray_surface_intersect((1000, 0, 2000), (0, 1, 0), surfaces_wall)
check('Wall hit: Y=5000', pt is not None and approx(pt[1], 5000, 5),
      f'pt={pt}')
check('Wall hit: X preserved', pt is not None and approx(pt[0], 1000, 5),
      f'X={pt[0] if pt else None}')
check('Wall hit: Z preserved', pt is not None and approx(pt[2], 2000, 5),
      f'Z={pt[2] if pt else None}')

# No floor case: floor=None, no walls — should return None
surfaces_none = {'floor': None, 'walls': [], 'obstacles': []}
pt = ray_surface_intersect((0, 0, 3000), (0, 1, -0.5), surfaces_none)
check('No surfaces: returns None', pt is None,
      f'pt={pt}')

# Parallel ray to floor (horizontal, dz=0): no floor hit
surfaces_floor = {'floor': {'z': 0, 'normal': [0, 0, 1]}, 'walls': [], 'obstacles': []}
pt = ray_surface_intersect((0, 0, 3000), (1, 0, 0), surfaces_floor)
check('Parallel to floor: no hit', pt is None,
      f'pt={pt}')

# Floor intersection from above
pt = ray_surface_intersect((0, 0, 3000), (0, 0.5, -1), surfaces_floor)
check('Floor from above: Z=0', pt is not None and approx(pt[2], 0, 5),
      f'pt={pt}')

# Ray aimed away from floor (upward, floor at Z=0, origin at Z=3000)
pt = ray_surface_intersect((0, 0, 3000), (0, 0, 1), surfaces_floor)
check('Upward from above floor: no hit', pt is None,
      f'pt={pt}')

# Closest surface wins: floor at Z=0, wall at Y=2000
# Ray going forward+down: should hit whichever is closer
surfaces_both = {
    'floor': {'z': 0, 'normal': [0, 0, 1]},
    'walls': [{'normal': [0, 1, 0], 'd': -2000}],
    'obstacles': []
}
# Origin at (0, 0, 1000), direction mostly forward with slight down
direction = (0, 0.95, -0.1)
dlen = math.sqrt(sum(d*d for d in direction))
direction = tuple(d/dlen for d in direction)
pt = ray_surface_intersect((0, 0, 1000), direction, surfaces_both)
check('Closest surface wins: hit point exists', pt is not None,
      f'pt={pt}')
# Wall is at Y=2000, floor at Z=0 from Z=1000 with shallow angle
# t_wall = 2000/0.95*dlen ~ 2105, t_floor = 1000/0.1*dlen ~ 10000
# Wall should win
if pt:
    check('Closest surface: wall hit (Y near 2000)', approx(pt[1], 2000, 50),
          f'Y={pt[1]}')


# =====================================================================
print('\n=== build_grid ===')
# =====================================================================

# Too few samples: < 4 → None
check('build_grid: < 4 samples → None', build_grid([(0.3, 0.3, 100, 100), (0.4, 0.4, 200, 200)]) is None)

# Too few unique pans: all same pan → None
same_pan = [(0.5, 0.3, 100, 100), (0.5, 0.4, 100, 200),
            (0.5, 0.5, 100, 300), (0.5, 0.6, 100, 400)]
check('build_grid: < 2 unique pans → None', build_grid(same_pan) is None)

# Too few unique tilts: all same tilt → None
same_tilt = [(0.3, 0.5, 100, 100), (0.4, 0.5, 200, 100),
             (0.5, 0.5, 300, 100), (0.6, 0.5, 400, 100)]
check('build_grid: < 2 unique tilts → None', build_grid(same_tilt) is None)

# Regular 3x3 grid
samples_3x3 = []
for p in [0.3, 0.4, 0.5]:
    for t in [0.3, 0.4, 0.5]:
        samples_3x3.append((p, t, p * 1000, t * 500))
grid = build_grid(samples_3x3)
check('build_grid: 3x3 → not None', grid is not None)
if grid:
    check('build_grid: 3 pan steps', len(grid['panSteps']) == 3,
          f'got {len(grid["panSteps"])}')
    check('build_grid: 3 tilt steps', len(grid['tiltSteps']) == 3,
          f'got {len(grid["tiltSteps"])}')

# 7-tuple support (only works when grid is fully covered — no missing cells)
samples_7 = []
for p in [0.2, 0.3, 0.4]:
    for t in [0.3, 0.4, 0.5]:
        samples_7.append((p, t, p * 1000, t * 500, p * 2000, t * 3000, 0))
grid7 = build_grid(samples_7)
check('build_grid: 7-tuple samples → not None', grid7 is not None)

# Missing cells use nearest neighbor: sparse grid with gaps
samples_sparse = [
    (0.2, 0.2, 100, 50),
    (0.2, 0.4, 100, 100),
    (0.4, 0.2, 200, 50),
    (0.4, 0.4, 200, 100),
    # Missing: (0.3, 0.3) — should be filled by nearest neighbor
    (0.3, 0.2, 150, 50),
    (0.3, 0.4, 150, 100),
    (0.2, 0.3, 100, 75),
    (0.4, 0.3, 200, 75),
]
grid_sparse = build_grid(samples_sparse)
check('build_grid: sparse grid → not None', grid_sparse is not None)


# =====================================================================
print('\n=== grid_lookup ===')
# =====================================================================

# Build a clean linear grid: px = pan*1000, py = tilt*500
samples_lin = []
for p in [0.2, 0.3, 0.4, 0.5, 0.6]:
    for t in [0.3, 0.4, 0.5, 0.6]:
        samples_lin.append((p, t, p * 1000, t * 500))
grid_lin = build_grid(samples_lin)

if grid_lin:
    # Exact corner value
    px, py = grid_lookup(grid_lin, 0.2, 0.3)
    check('Lookup exact corner: px=200', approx(px, 200, 1),
          f'px={px:.1f}')
    check('Lookup exact corner: py=150', approx(py, 150, 1),
          f'py={py:.1f}')

    # Midpoint interpolation
    px, py = grid_lookup(grid_lin, 0.35, 0.45)
    check('Lookup midpoint: px=350', approx(px, 350, 2),
          f'px={px:.1f}')
    check('Lookup midpoint: py=225', approx(py, 225, 2),
          f'py={py:.1f}')

    # Clamp outside range: below minimum
    px, py = grid_lookup(grid_lin, 0.0, 0.0)
    check('Lookup clamped low: px=200 (min pan)', approx(px, 200, 1),
          f'px={px:.1f}')
    check('Lookup clamped low: py=150 (min tilt)', approx(py, 150, 1),
          f'py={py:.1f}')

    # Clamp above range
    px, py = grid_lookup(grid_lin, 1.0, 1.0)
    check('Lookup clamped high: px=600 (max pan)', approx(px, 600, 1),
          f'px={px:.1f}')
    check('Lookup clamped high: py=300 (max tilt)', approx(py, 300, 1),
          f'py={py:.1f}')

    # Edge value: exact max pan, mid tilt
    px, py = grid_lookup(grid_lin, 0.6, 0.45)
    check('Lookup edge: px=600', approx(px, 600, 2),
          f'px={px:.1f}')


# =====================================================================
print('\n=== grid_inverse ===')
# =====================================================================

if grid_lin:
    # Converges to known sample point within 2px
    pan, tilt = grid_inverse(grid_lin, 400, 250)
    check('Inverse exact: pan ~ 0.4', approx(pan, 0.4, 0.02),
          f'pan={pan:.4f}')
    check('Inverse exact: tilt ~ 0.5', approx(tilt, 0.5, 0.02),
          f'tilt={tilt:.4f}')

    # Verify convergence: lookup the result, distance should be < 2px
    px, py = grid_lookup(grid_lin, pan, tilt)
    dist = math.sqrt((px - 400)**2 + (py - 250)**2)
    check('Inverse convergence: < 2px', dist < 2,
          f'dist={dist:.2f}')

    # Non-exact point
    pan, tilt = grid_inverse(grid_lin, 350, 225)
    check('Inverse non-exact: pan ~ 0.35', approx(pan, 0.35, 0.02),
          f'pan={pan:.4f}')
    check('Inverse non-exact: tilt ~ 0.45', approx(tilt, 0.45, 0.02),
          f'tilt={tilt:.4f}')

    # Boundary target (near edge of grid)
    pan, tilt = grid_inverse(grid_lin, 200, 150)
    check('Inverse boundary: pan ~ 0.2', approx(pan, 0.2, 0.03),
          f'pan={pan:.4f}')
    check('Inverse boundary: tilt ~ 0.3', approx(tilt, 0.3, 0.03),
          f'tilt={tilt:.4f}')


# =====================================================================
print('\n=== build_grid_3d + grid_3d_lookup + grid_3d_inverse ===')
# =====================================================================

# Build 3D grid: wx = pan*5000, wy = tilt*3000, wz = 0
samples_3d = []
for p in [0.2, 0.3, 0.4, 0.5, 0.6]:
    for t in [0.3, 0.4, 0.5, 0.6]:
        samples_3d.append((p, t, p * 1000, t * 500, p * 5000, t * 3000, 0))

grid3d = build_grid_3d(samples_3d)
check('build_grid_3d: not None', grid3d is not None)

if grid3d:
    check('build_grid_3d: has worldX', 'worldX' in grid3d)
    check('build_grid_3d: has worldY', 'worldY' in grid3d)
    check('build_grid_3d: has worldZ', 'worldZ' in grid3d)

    # Lookup at a known grid point
    wx, wy, wz = grid_3d_lookup(grid3d, 0.4, 0.5)
    check('3D lookup: wx ~ 2000', approx(wx, 2000, 5),
          f'wx={wx:.1f}')
    check('3D lookup: wy ~ 1500', approx(wy, 1500, 5),
          f'wy={wy:.1f}')
    check('3D lookup: wz ~ 0', approx(wz, 0, 5),
          f'wz={wz:.1f}')

    # Interpolated lookup
    wx, wy, wz = grid_3d_lookup(grid3d, 0.35, 0.45)
    check('3D lookup interp: wx ~ 1750', approx(wx, 1750, 10),
          f'wx={wx:.1f}')
    check('3D lookup interp: wy ~ 1350', approx(wy, 1350, 10),
          f'wy={wy:.1f}')

    # Inverse: target at known grid point
    pan, tilt = grid_3d_inverse(grid3d, 2000, 1500, 0)
    check('3D inverse exact: pan ~ 0.4', approx(pan, 0.4, 0.03),
          f'pan={pan:.4f}')
    check('3D inverse exact: tilt ~ 0.5', approx(tilt, 0.5, 0.03),
          f'tilt={tilt:.4f}')

    # Inverse: interpolated target
    pan, tilt = grid_3d_inverse(grid3d, 1750, 1350, 0)
    check('3D inverse interp: pan ~ 0.35', approx(pan, 0.35, 0.03),
          f'pan={pan:.4f}')
    check('3D inverse interp: tilt ~ 0.45', approx(tilt, 0.45, 0.03),
          f'tilt={tilt:.4f}')

    # Inverse ignores Z: same XY target but large Z offset
    pan_z0, tilt_z0 = grid_3d_inverse(grid3d, 2000, 1500, 0)
    pan_z9, tilt_z9 = grid_3d_inverse(grid3d, 2000, 1500, 9999)
    check('3D inverse ignores Z: pan matches', approx(pan_z0, pan_z9, 0.05),
          f'z0={pan_z0:.4f} z9={pan_z9:.4f}')
    check('3D inverse ignores Z: tilt matches', approx(tilt_z0, tilt_z9, 0.05),
          f'z0={tilt_z0:.4f} z9={tilt_z9:.4f}')

# Insufficient samples
check('build_grid_3d: < 4 samples → None',
      build_grid_3d([(0.3, 0.3, 0, 0, 0, 0, 0)]) is None)

# Single unique pan
check('build_grid_3d: < 2 unique pans → None',
      build_grid_3d([(0.5, 0.3, 0, 0, 0, 0, 0), (0.5, 0.4, 0, 0, 0, 0, 0),
                     (0.5, 0.5, 0, 0, 0, 0, 0), (0.5, 0.6, 0, 0, 0, 0, 0)]) is None)


# =====================================================================
print('\n=== _set_mover_dmx ===')
# =====================================================================

dmx = [0] * 512

# Channel layout: pan tilt speed dimmer strobe R G B W UV goboRot gobo macro
# addr=1 → base index 0
_set_mover_dmx(dmx, 1, 0.5, 0.75, 255, 0, 128, dimmer=200)

check('DMX pan: ch0 = 127', dmx[0] == 127,
      f'got {dmx[0]}')
check('DMX tilt: ch1 = 191', dmx[1] == 191,
      f'got {dmx[1]}')
check('DMX speed: ch2 = 0 (fast)', dmx[2] == 0,
      f'got {dmx[2]}')
check('DMX dimmer: ch3 = 200', dmx[3] == 200,
      f'got {dmx[3]}')
check('DMX strobe: ch4 = 0', dmx[4] == 0,
      f'got {dmx[4]}')
check('DMX red: ch5 = 255', dmx[5] == 255,
      f'got {dmx[5]}')
check('DMX green: ch6 = 0', dmx[6] == 0,
      f'got {dmx[6]}')
check('DMX blue: ch7 = 128', dmx[7] == 128,
      f'got {dmx[7]}')
# Channels 8-12 should be 0
for i in range(8, 13):
    check(f'DMX ch{i} = 0', dmx[i] == 0,
          f'got {dmx[i]}')

# Clamp: pan > 1.0 → 255 max
dmx2 = [0] * 512
_set_mover_dmx(dmx2, 1, 1.5, -0.5, 0, 0, 0)
check('DMX clamp pan high: ch0 = 255', dmx2[0] == 255,
      f'got {dmx2[0]}')
check('DMX clamp tilt low: ch1 = 0', dmx2[1] == 0,
      f'got {dmx2[1]}')

# Non-unit address: addr=14 → base index 13
dmx3 = [0] * 512
_set_mover_dmx(dmx3, 14, 1.0, 1.0, 100, 150, 200, dimmer=128)
check('DMX addr=14: pan at idx 13', dmx3[13] == 255,
      f'got {dmx3[13]}')
check('DMX addr=14: dimmer at idx 16', dmx3[16] == 128,
      f'got {dmx3[16]}')
check('DMX addr=14: R at idx 18', dmx3[18] == 100,
      f'got {dmx3[18]}')


# =====================================================================
print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests')
if failed:
    sys.exit(1)
