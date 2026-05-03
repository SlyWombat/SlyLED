#!/usr/bin/env python3
"""tests/aim/test_sphere.py — #784/#785 AimSphere tests.

Verifies:
  * 2°-cell construction + dictionary-keyed lookup.
  * Pick-first-then-average pipeline preserves branch consistency.
  * Multi-valued cells hold ≥2 rows from different DMX areas.
  * Bilinear bracketing-cell blend on both DMX axes.
  * Clipped target falls back to nearest stored vector (NOT None).
  * `current_pose` locks the branch under prefer="closest".
  * O(1) lookup smoke timing.
  * Failure modes: missing dmxToMechanical / Home / out-of-range Home.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

from aim.sphere import AimSphere, CELL_SIZE_DEG

_passed = 0
_failed = 0


def check(name, cond, detail=''):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f'  [PASS] {name}')
    else:
        _failed += 1
        print(f'  [FAIL] {name}  {detail}')


def approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


PROF_150W = {
    "id": "movinghead-150w-12ch",
    "panRange": 540, "tiltRange": 180,
}


# ─────────────────────────────────────────────────────────────────────
print('=== construction ===')
# ─────────────────────────────────────────────────────────────────────

upright_fix = {
    "id": 1, "x": 0, "y": 0, "z": 3000,
    "rotation": [0, 0, 0],
    "homePanDmx16": 32768, "homeTiltDmx16": 32768, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000},
}
sphere = AimSphere(upright_fix, PROF_150W, step=256)
check('construction succeeds', sphere is not None)
check('home stored', sphere.home_pan_dmx16 == 32768)
check('rotation normalised', sphere.fixture_rotation == [0.0, 0.0, 0.0])
# #784 c3 — home is the angular zero by definition; the mechanics
# module subtracts the home anchor inside `dmx_to_mechanical`. There's
# no `_home_mech_*` instance attribute anymore.
check('panRange stored from profile', sphere.pan_range_deg == 540)
check('tiltRange stored from profile', sphere.tilt_range_deg == 180)
check('cell_index populated', len(sphere._cell_index) > 0)
check('all_rows populated (DMX walk)', len(sphere._all_rows) > 0)


# ─────────────────────────────────────────────────────────────────────
print('\n=== 2°-cell granularity ===')
# ─────────────────────────────────────────────────────────────────────

# Cells cover 2° × 2°. For a 540°×180° fixture covering a near-full
# sphere of azimuths, we expect on the order of (180+180)/2 × (180/2)
# = 90 × 90 = 8100 cells. The actual count varies by reachable cone.
check('2° cell size is 2 degrees', CELL_SIZE_DEG == 2.0)
check('cell count > 1000 (covers most of the sphere)',
      len(sphere._cell_index) > 1000,
      f'got {len(sphere._cell_index)}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== aim_direction round-trip ===')
# ─────────────────────────────────────────────────────────────────────

for (az_in, el_in) in [(0, 0), (30, 15), (-45, -10), (60, 25), (-30, 30),
                         (90, -45), (-90, 0), (1.5, 0.5)]:
    pose = sphere.aim_direction(az_in, el_in)
    check(f'aim_direction({az_in}, {el_in}) returns a pose',
          pose is not None and isinstance(pose[0], int) and isinstance(pose[1], int))
    if pose:
        az_back, el_back = sphere.dmx_to_aim(*pose)
        ok_az = approx(az_back, az_in, CELL_SIZE_DEG)
        ok_el = approx(el_back, el_in, CELL_SIZE_DEG)
        check(f'round-trip ({az_in:+.1f}, {el_in:+.1f}) within 2° cell',
              ok_az and ok_el,
              f'back ({az_back:.3f}, {el_back:.3f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== multi-valued cell: ≥2 rows from different DMX areas ===')
# ─────────────────────────────────────────────────────────────────────

# 540° pan fixture, off-centre Home so the doubled-azimuth band is
# reachable on both sides.
fix_off_centre = {
    "id": 1, "x": 0, "y": 0, "z": 3000,
    "rotation": [0, 0, 0],
    "homePanDmx16": 10923, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000},   # near low end
    "homeTiltDmx16": 32768,
}
sphere_off = AimSphere(fix_off_centre, PROF_150W, step=128)

# Find an azimuth in the doubled band by looking at the cell index.
multi_cell = None
for key, rows in sphere_off._cell_index.items():
    if len(rows) >= 2:
        multi_cell = (key, rows)
        break
check('540° + off-centre Home produces multi-valued cells',
      multi_cell is not None,
      f'no multi-cell found in {len(sphere_off._cell_index)} cells')

if multi_cell:
    (cell_az, cell_el), rows = multi_cell
    pan_dmx_values = [r[0] for r in rows]
    pan_spread = max(pan_dmx_values) - min(pan_dmx_values)
    # Two branches should be ~half the pan range apart in DMX
    # (= 360° * 65535/540 = 43690 DMX units).
    check('multi-valued rows are ~half-pan-range apart in DMX',
          pan_spread > 30000,
          f'cell ({cell_az}, {cell_el}) pan_dmx values {pan_dmx_values}, '
          f'spread {pan_spread}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== pick-first-then-average preserves branch consistency ===')
# ─────────────────────────────────────────────────────────────────────

# Pick a target direction in the doubled-azimuth band and run aim_direction
# with prefer="A" and prefer="B" — the resulting averaged DMX must lie on
# the requested branch (A=lowest pan_dmx, B=highest), NOT between them.
if multi_cell:
    (cell_az, cell_el), rows = multi_cell
    az_target = (cell_az + 0.5) * CELL_SIZE_DEG
    el_target = (cell_el + 0.5) * CELL_SIZE_DEG
    pose_a = sphere_off.aim_direction(az_target, el_target, prefer="A")
    pose_b = sphere_off.aim_direction(az_target, el_target, prefer="B")
    check('prefer=A and prefer=B return distinct poses on multi-valued',
          pose_a != pose_b,
          f'A={pose_a} B={pose_b}')
    check('prefer=A pose pan_dmx is on the LOW branch',
          pose_a is not None and pose_a[0] < pose_b[0],
          f'A_pan={pose_a[0] if pose_a else None} B_pan={pose_b[0] if pose_b else None}')
    # The averaged result must be near one of the branch extremes,
    # not at a halfway point. Half-spread = ~21845 DMX; if averaging
    # crossed branches the result would be near (A+B)/2.
    halfway = (pose_a[0] + pose_b[0]) // 2
    a_to_halfway = abs(pose_a[0] - halfway)
    a_to_a = 0  # by construction
    b_to_halfway = abs(pose_b[0] - halfway)
    check('A pose stays close to A branch (not the A/B midpoint)',
          a_to_halfway > 5000,  # Way more than the bilinear spread within a cell
          f'pose_a[0]={pose_a[0]} halfway={halfway} dist={a_to_halfway}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== prefer="closest" + current_pose locks branch ===')
# ─────────────────────────────────────────────────────────────────────

if multi_cell:
    (cell_az, cell_el), rows = multi_cell
    az_target = (cell_az + 0.5) * CELL_SIZE_DEG
    el_target = (cell_el + 0.5) * CELL_SIZE_DEG
    sorted_rows = sorted(rows, key=lambda r: r[0])
    a_branch_pan = sorted_rows[0][0]
    b_branch_pan = sorted_rows[-1][0]
    a_branch_tilt = sorted_rows[0][1]
    b_branch_tilt = sorted_rows[-1][1]
    # current_pose near A → result locks to A branch.
    pose_near_a = sphere_off.aim_direction(
        az_target, el_target, prefer="closest",
        current_pose=(a_branch_pan + 100, a_branch_tilt + 50))
    check('closest with current_pose near A → result on A branch',
          pose_near_a is not None
          and abs(pose_near_a[0] - a_branch_pan) < abs(pose_near_a[0] - b_branch_pan),
          f'pose={pose_near_a} A={a_branch_pan} B={b_branch_pan}')
    # current_pose near B → result locks to B branch.
    pose_near_b = sphere_off.aim_direction(
        az_target, el_target, prefer="closest",
        current_pose=(b_branch_pan - 100, b_branch_tilt - 50))
    check('closest with current_pose near B → result on B branch',
          pose_near_b is not None
          and abs(pose_near_b[0] - b_branch_pan) < abs(pose_near_b[0] - a_branch_pan),
          f'pose={pose_near_b} A={a_branch_pan} B={b_branch_pan}')
    check('closest result flips branches with current_pose',
          pose_near_a != pose_near_b)


# ─────────────────────────────────────────────────────────────────────
print('\n=== bilinear interpolation across 4 bracketing cells ===')
# ─────────────────────────────────────────────────────────────────────

# An off-cell-centre target should produce a DMX between the 4 bracketing
# corners' picks. Take a target at (1.5°, 0.5°) — the center of the
# (0..2, 0..2) cell quadrant. Verify that the result is BETWEEN the
# 4 corners' picks on both axes.
target_az = 1.5
target_el = 0.5
pose = sphere.aim_direction(target_az, target_el)
check('off-cell-centre target returns a pose', pose is not None)

# Fetch the 4 corner cell rows manually and verify the result is bilinear.
# Cell coords for target: (0, 0). Bracketing cells: (0, 0), (1, 0),
# (0, 1), (1, 1) — but those depend on (az/2, el/2) floor. With az=1.5,
# el=0.5: floor(1.5/2)=0, floor(0.5/2)=0, ceil=1, ceil=1.
if pose:
    pan_min = 65535
    pan_max = 0
    tilt_min = 65535
    tilt_max = 0
    for cell in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        for r in sphere._cell_index.get(cell, []):
            pan_min = min(pan_min, r[0])
            pan_max = max(pan_max, r[0])
            tilt_min = min(tilt_min, r[1])
            tilt_max = max(tilt_max, r[1])
    # Bilinear blend should produce a result within the bounding
    # rectangle of the 4 corners' chosen rows (with a small tolerance
    # for the fact that pick-policy may select different rows in
    # multi-valued cells).
    check('bilinear result within pan bounds of bracketing corners',
          pan_min - 100 <= pose[0] <= pan_max + 100,
          f'pose={pose} pan_min={pan_min} pan_max={pan_max}')
    check('bilinear result within tilt bounds of bracketing corners',
          tilt_min - 100 <= pose[1] <= tilt_max + 100,
          f'pose={pose} tilt_min={tilt_min} tilt_max={tilt_max}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== clipped target returns closest stored vector (not None) ===')
# ─────────────────────────────────────────────────────────────────────

# Synthetic narrow-cone fixture: tilt range only ±20°.
narrow_prof = {
    "id": "narrow-tilt",
    "panRange": 90, "tiltRange": 40,
}
narrow_fix = {
    "id": 3, "x": 0, "y": 0, "z": 0,
    "rotation": [0, 0, 0],
    "homePanDmx16": 32768, "homeTiltDmx16": 32768, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000},
}
sphere_narrow = AimSphere(narrow_fix, narrow_prof, step=256)
# Reachable mech-tilt is ±20°; ask for el=+45° (way past the cone).
pose = sphere_narrow.aim_direction(0, 45)
check('clipped tilt target returns a pose (NOT None)', pose is not None)
if pose:
    az_back, el_back = sphere_narrow.dmx_to_aim(*pose)
    # The returned aim should be on or near the cone boundary (near
    # +20°), NOT at +45° (which is unreachable) and NOT at home (0°).
    check('clipped pose lands near the cone rim, not at home or impossible target',
          15 < el_back < 25,
          f'el_back={el_back} (expected near cone boundary ~20°)')

# Ask for az=+90° (also past the narrow ±45° pan cone).
pose_pan = sphere_narrow.aim_direction(90, 0)
check('clipped pan target returns a pose', pose_pan is not None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== aim_xyz wrapper ===')
# ─────────────────────────────────────────────────────────────────────

# Target +Y of fixture origin → near home.
pose = sphere.aim_xyz((0, 5000, 3000))
check('aim_xyz forward → pose near home',
      pose is not None and abs(pose[0] - 32768) <= 256,
      f'pose={pose}')

# High target above fixture → tilt above home (mech tilt+, slope+,
# DMX > home).
pose = sphere.aim_xyz((0, 100, 8000))
check('aim_xyz high target → tilt > home',
      pose is not None and pose[1] > 32768,
      f'pose={pose}')

# Coincident target → None.
pose = sphere.aim_xyz(sphere.fixture_xyz)
check('aim_xyz coincident → None', pose is None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== inverted fixture (rotation handles inversion) ===')
# ─────────────────────────────────────────────────────────────────────

inverted_fix = {
    "id": 2, "x": 600, "y": 0, "z": 1760,
    "rotation": [0, 180, 0],
    "homePanDmx16": 32768, "homeTiltDmx16": 32768, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000},
}
sphere_inv = AimSphere(inverted_fix, PROF_150W, step=256)

az, el = sphere_inv.dmx_to_aim(32768, 32768)
check('inverted home → (0, 0) — Y preserved under Ry(180)',
      approx(az, 0, CELL_SIZE_DEG) and approx(el, 0, CELL_SIZE_DEG),
      f'got ({az}, {el})')

# Same stage-frame target on upright vs inverted: different DMX values,
# same physical aim direction.
pose_up = sphere.aim_direction(30, 15)
pose_inv = sphere_inv.aim_direction(30, 15)
check('upright vs inverted produce different DMX for same stage aim',
      pose_up != pose_inv)
if pose_up and pose_inv:
    az_up, el_up = sphere.dmx_to_aim(*pose_up)
    az_inv, el_inv = sphere_inv.dmx_to_aim(*pose_inv)
    check('different DMX, same stage aim',
          approx(az_up, az_inv, 1.0) and approx(el_up, el_inv, 1.0),
          f'up=({az_up:.2f}, {el_up:.2f}) inv=({az_inv:.2f}, {el_inv:.2f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== O(1) lookup smoke timing ===')
# ─────────────────────────────────────────────────────────────────────

t0 = time.perf_counter()
for _ in range(10000):
    sphere.aim_direction(15.5, 7.5, current_pose=(40000, 35000))
elapsed = time.perf_counter() - t0
per_call_us = elapsed * 1e6 / 10000
check(f'10k lookups under 1s wall-clock (~{per_call_us:.1f} µs/call)',
      elapsed < 1.0,
      f'{elapsed:.3f}s for 10000 calls')


# ─────────────────────────────────────────────────────────────────────
print('\n=== failure modes ===')
# ─────────────────────────────────────────────────────────────────────

# Profile missing pan/tilt range → constructor raises with profile id.
# (#784 c3 — `dmxToMechanical` no longer exists; the only profile
# requirement is panRange + tiltRange.)
try:
    AimSphere(upright_fix, {"id": "naked-profile"})
    check('profile missing panRange/tiltRange raises', False, 'no exception')
except ValueError as e:
    check('profile missing panRange/tiltRange raises', True)
    check('error names the profile id', 'naked-profile' in str(e),
          f'msg: {e}')

# Fixture missing Home → raises.
try:
    AimSphere({"id": "no-home", "x": 0, "y": 0, "z": 0, "rotation": [0, 0, 0]},
               PROF_150W)
    check('missing Home raises', False)
except ValueError:
    check('missing Home raises', True)

# Out-of-range Home → raises with offending DMX value.
try:
    AimSphere({"id": "bad-home", "x": 0, "y": 0, "z": 0, "rotation": [0, 0, 0],
                "homePanDmx16": 99999, "homeTiltDmx16": 32768, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000}},
               PROF_150W)
    check('out-of-range Home pan raises', False)
except ValueError as e:
    check('out-of-range Home pan raises', True)
    check('error names the offending DMX', '99999' in str(e), f'msg: {e}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== fid 17 emulation ===')
# ─────────────────────────────────────────────────────────────────────

fid17 = {
    "id": 17, "x": 600, "y": 0, "z": 1760,
    "rotation": [0, 180, 0],
    "homePanDmx16": 44364, "homeTiltDmx16": 0, "homeSecondary": {"panMovedDirection": "right", "tiltMovedDirection": "down", "panOffsetDmx16": 10922, "tiltOffsetDmx16": 32768},
}
sphere_fid17 = AimSphere(fid17, PROF_150W, step=128)

# Pose 1 — Home (44364, 0): operator drove Home to "rotation forward at horizon",
# so dmx_to_aim should report ≈ (0, 0).
az, el = sphere_fid17.dmx_to_aim(44364, 0)
check('fid17 Home dmx (44364, 0) → stage ≈ (0, 0)',
      approx(az, 0, CELL_SIZE_DEG) and approx(el, 0, CELL_SIZE_DEG),
      f'got ({az}, {el})')

# Pose 2 — Pan-only secondary (55286, 0): operator drove pan +10922,
# called "right". Stage convention: right = stage-(-X) = az<0.
az, el = sphere_fid17.dmx_to_aim(55286, 0)
check('fid17 pan-only secondary → stage az<0 (right)', az < 0,
      f'got az={az}')

# Pose 3 — Tilt-only secondary (44364, 32768): operator drove tilt
# +32768, called "down". Stage convention: down = el<0.
az, el = sphere_fid17.dmx_to_aim(44364, 32768)
check('fid17 tilt-only secondary → stage el<0 (down)', el < 0,
      f'got el={el}')


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
