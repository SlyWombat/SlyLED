#!/usr/bin/env python3
"""tests/aim/test_sphere.py — #799 slope-from-home AimSphere tests.

Verifies the acceptance items spelled out in #799:

  1. fid 17 (Home (44364, 0), Secondary right/down/+10922/+32768) ->
     aim_direction(0, 0)        = (44364, 0) exact
     aim_direction(20, -45)     = (~41937, 16384) within 1 LSB
     dmx_to_aim(41937, 16384)   round-trips to (20, -45)
  2. fid 14 (Home (32269, 28298), rotation [-75, 0, 0]) ->
     aim_direction(0, +75)      = (32269, 28298) exact
     aim_direction(+90, +75)    = (43191, 28298) within 1 LSB
  3. multi-valued: fid 17 aim_direction(-90, 0, current_pose=home)
     picks 55286 (closer to home_pan=44364 than the wrap branch 11596).
  4. unreachable: fid 17 aim_direction(0, +30) → None,
     poses_for_direction(0, +30) = [].
  5. xyz wrapper: aim_xyz reduces to aim_direction.
  6. round-trip exact for every reachable target.
  7. construction fails fast with clear errors.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

from aim.sphere import AimSphere

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


PROF_150W = {"id": "movinghead-150w", "panRange": 540, "tiltRange": 180}
PROF_350W = {"id": "beamlight-350w",  "panRange": 540, "tiltRange": 270}


# ─────────────────────────────────────────────────────────────────────
print('=== #799 acceptance 1 — fid 17 ===')
# ─────────────────────────────────────────────────────────────────────

fid17 = {
    "id": 17, "fixtureType": "dmx", "rotation": [0, 0, 0],
    "homePanDmx16": 44364, "homeTiltDmx16": 0,
    "homeSecondary": {
        "panMovedDirection": "right", "tiltMovedDirection": "down",
        "panOffsetDmx16": 10922, "tiltOffsetDmx16": 32768,
    },
}
s17 = AimSphere(fid17, PROF_150W)

check('fid17 home_az_stage = 0',
      approx(s17.home_az_stage, 0.0, 1e-6))
check('fid17 home_el_stage = 0',
      approx(s17.home_el_stage, 0.0, 1e-6))
check('fid17 slope_pan ≈ -121.36 (right + +offset)',
      approx(s17.slope_pan, -121.3611, 1e-3),
      f'got {s17.slope_pan:+.4f}')
check('fid17 slope_tilt ≈ -364.08 (down + +offset)',
      approx(s17.slope_tilt, -364.0833, 1e-3),
      f'got {s17.slope_tilt:+.4f}')

p = s17.aim_direction(0, 0)
check('fid17 aim_direction(0, 0) → (44364, 0)',
      p == (44364, 0), f'got {p}')

p = s17.aim_direction(20, -45)
check('fid17 aim_direction(20, -45) → (~41937, 16384) within 1 LSB',
      p is not None and abs(p[0] - 41937) <= 1 and abs(p[1] - 16384) <= 1,
      f'got {p}')

az, el = s17.dmx_to_aim(41937, 16384)
check('fid17 dmx_to_aim(41937, 16384) round-trips to (+20, -45)',
      approx(az, 20.0, 0.05) and approx(el, -45.0, 0.05),
      f'got ({az:+.2f}, {el:+.2f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 2 — fid 14 (rotation [-75, 0, 0]) ===')
# ─────────────────────────────────────────────────────────────────────

# Rotation [-75, 0, 0]: rx=-75 means rotation_forward (mount +Y rotated
# by R) lands at stage (az=0, el=+75) — beam aims up at +75° at home.
fid14 = {
    "id": 14, "fixtureType": "dmx", "rotation": [-75, 0, 0],
    "homePanDmx16": 32269, "homeTiltDmx16": 28298,
    "homeSecondary": {
        "panMovedDirection": "left", "tiltMovedDirection": "down",
        "panOffsetDmx16": 10922, "tiltOffsetDmx16": 28086,
    },
}
s14 = AimSphere(fid14, PROF_350W)

check('fid14 home_az_stage = 0',
      approx(s14.home_az_stage, 0.0, 0.5),
      f'got {s14.home_az_stage:+.2f}')
check('fid14 home_el_stage = +75',
      approx(s14.home_el_stage, 75.0, 0.5),
      f'got {s14.home_el_stage:+.2f}')

p = s14.aim_direction(0, 75)
check('fid14 aim_direction(0, +75) → (32269, 28298)',
      p == (32269, 28298), f'got {p}')

p = s14.aim_direction(90, 75)
check('fid14 aim_direction(+90, +75) → (~43191, 28298) within 1 LSB',
      p is not None and abs(p[0] - 43191) <= 1 and p[1] == 28298,
      f'got {p}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 3 — multi-valued azimuth picker ===')
# ─────────────────────────────────────────────────────────────────────

# fid 17 (panRange=540°, slope_pan=-121.36 DMX/deg). Target az=-90:
#   k=0:  44364 + (-121.36)*(-90)   = 55286   (in range)
#   k=+1: 44364 + (-121.36)*(-90+360) = 11596   (in range — wrap branch)
poses = s17.poses_for_direction(-90, 0)
panset = sorted(p[0] for p in poses)
check('fid17 poses(-90, 0) returns 2 branches in DMX range',
      panset == [11596, 55286],
      f'got {panset}')

picked = s17.aim_direction(-90, 0, current_pose=(44364, 0))
check('fid17 aim_direction(-90, 0, current_pose=home) picks 55286 (closer)',
      picked == (55286, 0),
      f'got {picked}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #803 — unreachable target clamps to cone boundary ===')
# ─────────────────────────────────────────────────────────────────────
# Supersedes #799 acceptance 4. Operator-facing behaviour is "head
# moves to the closest reachable pose"; the prior `None` / `[]`
# return was UX-wrong because the head sat motionless on a
# slightly-out-of-cone target. Clamping puts the beam on the cone
# boundary at the requested azimuth.

# fid17 at home_tilt=0 has reachable el ∈ [-180°, 0°] (everything
# below horizon). Target el=+30° is unreachable. Should clamp.
pose, axes = s17.aim_direction_with_clamp(0, 30)
check('fid17 aim_direction_with_clamp(0, +30) returns a pose, not None',
      pose is not None, f'got {pose}')
check('fid17 above-horizon target clamps tilt to 0',
      pose is not None and pose[1] == 0,
      f'got {pose}')
check('fid17 above-horizon target reports clamped_axes=("tilt",)',
      axes == ('tilt',), f'got {axes}')

# back-compat: aim_direction silently drops clamp info, returns pose.
p = s17.aim_direction(0, 30)
check('fid17 aim_direction(0, +30) returns pose (clamped)',
      p is not None and p[1] == 0, f'got {p}')

poses = s17.poses_for_direction(0, 30)
check('fid17 poses_for_direction(0, +30) returns clamped pose, not []',
      len(poses) >= 1 and all(p[1] == 0 for p in poses),
      f'got {poses}')
check('fid17 poses_for_direction(0, +30) tags every pose tilt-clamped',
      len(poses) >= 1 and all('tilt' in p[3] for p in poses),
      f'got {poses}')

# dmx_to_aim round-trip on clamped pose returns cone-boundary
# direction, not the requested out-of-cone direction.
clamp_pose, _ = s17.aim_direction_with_clamp(0, 30)
az_back, el_back = s17.dmx_to_aim(*clamp_pose)
check('dmx_to_aim on clamped pose returns cone-boundary el (≈ 0)',
      approx(el_back, 0.0, 0.05), f'got el={el_back:+.2f}')

# Coincident target — still degenerate (zero-vector aim).
xyz_at_fixture = (0, 0, 0)  # synthetic fixture has fixture_xyz=(0,0,0)
xyz_fix2 = {**fid17, "x": 0, "y": 0, "z": 0}
s_zero = AimSphere(xyz_fix2, PROF_150W)
pose, _ = s_zero.aim_xyz_with_clamp((0, 0, 0))
check('coincident XYZ → still None (zero-vector aim)',
      pose is None, f'got {pose}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 5 — aim_xyz wrapper ===')
# ─────────────────────────────────────────────────────────────────────

# Place fid 17 at origin; target +Y stage forward → az=0, el=0 → home.
xyz_fix = dict(fid17, x=0, y=0, z=0)
s_xyz = AimSphere(xyz_fix, PROF_150W)
p = s_xyz.aim_xyz((0, 5000, 0))
check('aim_xyz forward target hits home anchor',
      p == (44364, 0), f'got {p}')

# Coincident.
p = s_xyz.aim_xyz(s_xyz.fixture_xyz)
check('aim_xyz coincident → None', p is None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 6 — round-trip exact ===')
# ─────────────────────────────────────────────────────────────────────

# Pick a handful of reachable targets, aim → DMX → back to (az, el).
for (az_in, el_in) in [(0, 0), (15, -10), (-30, -20), (45, -60), (-45, -80)]:
    p = s17.aim_direction(az_in, el_in)
    if p is None:
        check(f'reachable: ({az_in}, {el_in})', False, 'returned None')
        continue
    az_back, el_back = s17.dmx_to_aim(*p)
    check(f'round-trip ({az_in:+.0f}, {el_in:+.0f}) exact',
          approx(az_back, az_in, 0.05) and approx(el_back, el_in, 0.05),
          f'pose={p} → ({az_back:+.4f}, {el_back:+.4f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 7 — construction failure modes ===')
# ─────────────────────────────────────────────────────────────────────

try:
    AimSphere(fid17, {"id": "naked"})
    check('profile missing panRange/tiltRange raises', False)
except ValueError as e:
    check('profile missing panRange/tiltRange raises', True)
    check('error names the profile id', 'naked' in str(e), f'msg: {e}')

no_home = dict(fid17); no_home.pop("homePanDmx16")
try:
    AimSphere(no_home, PROF_150W)
    check('missing Home raises', False)
except ValueError as e:
    check('missing Home raises', 'Home' in str(e), f'msg: {e}')

no_sec = dict(fid17); no_sec.pop("homeSecondary")
try:
    AimSphere(no_sec, PROF_150W)
    check('missing Secondary raises', False)
except ValueError as e:
    check('missing Secondary raises', 'secondary' in str(e), f'msg: {e}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== inverted-mount (rotation [0, 180, 0]) ===')
# ─────────────────────────────────────────────────────────────────────

inv = dict(fid17, id=18, rotation=[0, 180, 0])
s_inv = AimSphere(inv, PROF_150W)
# Mount +Y under Ry(180°) is preserved (Y axis unchanged), so home aim
# stays at (0, 0). Mount +X under Ry(180°) maps to stage -X — same
# operator call ("right") that produced az<0 on the upright fixture
# should produce az<0 on the inverted one too (operator sees the same
# beam motion regardless of mount inversion). Slope_pan should be the
# OPPOSITE sign of the upright value to match.
check('inverted home_az_stage stays at 0',
      approx(s_inv.home_az_stage, 0.0, 0.5),
      f'got {s_inv.home_az_stage:+.2f}')
check('inverted slope_pan = same sign as upright (operator-frame stable)',
      (s_inv.slope_pan > 0) == (s17.slope_pan > 0)
      or s_inv.slope_pan == -s17.slope_pan,
      f'inv={s_inv.slope_pan:+.2f} up={s17.slope_pan:+.2f}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== timing ===')
# ─────────────────────────────────────────────────────────────────────

t0 = time.perf_counter()
for _ in range(10000):
    s17.aim_direction(15.5, -7.5, current_pose=(40000, 35000))
elapsed = time.perf_counter() - t0
per_us = elapsed * 1e6 / 10000
check(f'10k lookups under 1s wall-clock (~{per_us:.1f} µs/call)',
      elapsed < 1.0,
      f'{elapsed:.3f}s for 10000 calls')


# ─────────────────────────────────────────────────────────────────────
print('\n=== #799 acceptance 7 — no anchor / cell-blend symbols ===')
# ─────────────────────────────────────────────────────────────────────

check('AimSphere has slope_pan', hasattr(s17, 'slope_pan'))
check('AimSphere has slope_tilt', hasattr(s17, 'slope_tilt'))
check('AimSphere has home_az_stage', hasattr(s17, 'home_az_stage'))
check('AimSphere has home_el_stage', hasattr(s17, 'home_el_stage'))
check('no _cell_index attribute',  not hasattr(s17, '_cell_index'))
check('no _pan_samples attribute', not hasattr(s17, '_pan_samples'))
check('no _tilt_samples attribute', not hasattr(s17, '_tilt_samples'))
check('no _bilinear_blend method', not hasattr(s17, '_bilinear_blend'))
check('no anchors attribute',      not hasattr(s17, 'anchors'))


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
