#!/usr/bin/env python3
"""tests/aim/test_sphere.py — #798 anchor-based bracketing-sphere tests.

Verifies:
  * Three-anchor minimum constructs cleanly.
  * Each anchor round-trips exactly through aim_direction + dmx_to_aim.
  * Bracketing interp produces midpoint DMX = midpoint of two anchors.
  * Multi-valued azimuth (panRange > 360°, off-centre Home) returns
    ≥2 branches with distinct branch_ids.
  * `prefer="closest"` against `current_pose` locks branch on multi-
    valued targets.
  * Legacy bootstrap (rotation + direction labels) reproduces three
    anchors that match the legacy model's home/pan-slew/tilt-slew DMX.
  * aim_xyz coincident → None.
  * O(1) timing.
  * Failure modes: missing Home / fewer than 3 anchors / no profile.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

from aim.anchors import (
    CalibrationAnchor, derive_legacy_anchors, collect_anchors,
)
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
PROF_350W = {"id": "beamlight-350w", "panRange": 540, "tiltRange": 270}


# ─────────────────────────────────────────────────────────────────────
print('=== fid 14 (350W) acceptance test from #798 ===')
# ─────────────────────────────────────────────────────────────────────

fid14 = {
    "id": 14, "fixtureType": "dmx", "rotation": [0, 0, 0],
    "homePanDmx16": 32269, "homeTiltDmx16": 28298,
}
fid14_anchors = [
    CalibrationAnchor(32269, 28298, 0.0, 75.0),    # Home — beam aims up at +75°
    CalibrationAnchor(43191, 28298, 90.0, 75.0),   # Pan slew — operator confirmed +90°
    CalibrationAnchor(32269, 56384, 0.0, -15.0),   # Tilt slew — past vertical, below horizon
]
s14 = AimSphere(fid14, PROF_350W, anchors=fid14_anchors)

# Each anchor round-trips exactly.
for a in fid14_anchors:
    pose = s14.aim_direction(a.az_deg, a.el_deg)
    check(f'fid14 aim_direction({a.az_deg:+.0f}, {a.el_deg:+.0f}) hits anchor',
          pose == (a.pan_dmx16, a.tilt_dmx16),
          f'got {pose}, expected ({a.pan_dmx16}, {a.tilt_dmx16})')
    az, el = s14.dmx_to_aim(a.pan_dmx16, a.tilt_dmx16)
    check(f'fid14 dmx_to_aim({a.pan_dmx16}, {a.tilt_dmx16}) round-trips',
          approx(az, a.az_deg, 0.01) and approx(el, a.el_deg, 0.01),
          f'got ({az:+.2f}, {el:+.2f})')

# Midway between Home and pan-slew on pan axis.
mid = s14.aim_direction(45.0, 75.0)
expected_pan = (32269 + 43191) // 2
check(f'fid14 aim_direction(+45, +75) hits midway pan DMX',
      mid is not None and abs(mid[0] - expected_pan) <= 1,
      f'got {mid}, expected ({expected_pan}, 28298)')


# ─────────────────────────────────────────────────────────────────────
print('\n=== multi-valued azimuth (panRange=540°, off-centre Home) ===')
# ─────────────────────────────────────────────────────────────────────

# Home offset to the LEFT end of pan range so the +X direction wraps to
# multiple mechanical positions.
multi_fix = {
    "id": 99, "fixtureType": "dmx", "rotation": [0, 0, 0],
    "homePanDmx16": 10000, "homeTiltDmx16": 32768,
}
multi_anchors = [
    CalibrationAnchor(10000, 32768, 0.0, 0.0),
    CalibrationAnchor(20923, 32768, 90.0, 0.0),
    CalibrationAnchor(10000, 38229, 0.0, -22.5),
]
s_multi = AimSphere(multi_fix, PROF_150W, anchors=multi_anchors)

poses0 = s_multi.poses_for_direction(0.0, 0.0)
check('multi-valued: az=0 returns ≥2 branches',
      len(poses0) >= 2,
      f'got {len(poses0)}: {poses0}')
branch_ids = sorted(set(b for _p, _t, b in poses0))
check('multi-valued: branch_ids are distinct integers',
      len(branch_ids) >= 2 and 0 in branch_ids,
      f'branch_ids={branch_ids}')

# Each branch's DMX round-trips back to (az=0, el=0) under dmx_to_aim,
# modulo 360° azimuth wrap on the wrap branches.
for p, t, b in poses0:
    az, el = s_multi.dmx_to_aim(p, t)
    az_mod = ((az + 180.0) % 360.0) - 180.0
    check(f'multi-valued: branch {b:+d} ({p}, {t}) round-trips az≈0',
          approx(az_mod, 0.0, 0.5) and approx(el, 0.0, 0.5),
          f'got ({az:+.2f}, {el:+.2f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== prefer="closest" + current_pose locks branch ===')
# ─────────────────────────────────────────────────────────────────────

# Use the multi-valued sphere; ask for az=0 with current_pose near each
# branch and verify the picker locks to it.
if len(poses0) >= 2:
    branch0 = next(p for p in poses0 if p[2] == 0)
    branch1 = next(p for p in poses0 if p[2] != 0)

    pose_near0 = s_multi.aim_direction(
        0.0, 0.0, prefer="closest",
        current_pose=(branch0[0] + 100, branch0[1]))
    check('closest with current_pose near branch 0 → branch 0',
          pose_near0 == (branch0[0], branch0[1]),
          f'got {pose_near0}')

    pose_near1 = s_multi.aim_direction(
        0.0, 0.0, prefer="closest",
        current_pose=(branch1[0] - 100, branch1[1]))
    check('closest with current_pose near branch 1 → branch 1',
          pose_near1 == (branch1[0], branch1[1]),
          f'got {pose_near1}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== prefer="A" returns primary branch ===')
# ─────────────────────────────────────────────────────────────────────

if len(poses0) >= 2:
    pose_a = s_multi.aim_direction(0.0, 0.0, prefer="A")
    branch0 = next(p for p in poses0 if p[2] == 0)
    check('prefer=A returns branch 0',
          pose_a == (branch0[0], branch0[1]),
          f'got {pose_a}, expected {branch0[:2]}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== legacy bootstrap matches old rotation-derived sphere ===')
# ─────────────────────────────────────────────────────────────────────

legacy_fix = {
    "id": 17, "fixtureType": "dmx", "rotation": [0, 0, 0],
    "homePanDmx16": 32269, "homeTiltDmx16": 32768,
    "homeSecondary": {
        "panMovedDirection": "left",
        "tiltMovedDirection": "down",
        "panOffsetDmx16": 10922,
        "tiltOffsetDmx16": 5461,
    },
}
legacy_anchors = derive_legacy_anchors(legacy_fix, PROF_150W)
check('legacy bootstrap returns 3 anchors', len(legacy_anchors) == 3)

# Home anchor: rotation forward (mount +Y rotated by I) → stage (0, 0).
home_anchor = legacy_anchors[0]
check('legacy home anchor at stage (0, 0)',
      approx(home_anchor.az_deg, 0.0, 0.01)
      and approx(home_anchor.el_deg, 0.0, 0.01),
      f'got ({home_anchor.az_deg:+.2f}, {home_anchor.el_deg:+.2f})')

# Pan slew anchor — direction "left" maps to stage az>0; offset in
# DMX should land at +panDir × panOff away from home.
pan_anchor = legacy_anchors[1]
check('legacy pan anchor stage az has correct sign',
      pan_anchor.az_deg > 0,  # "left" → +X → +az
      f'got az={pan_anchor.az_deg:+.2f}')
check('legacy pan anchor DMX delta = ±panOffsetDmx16',
      abs(pan_anchor.pan_dmx16 - 32269) == 10922,
      f'got panDmx={pan_anchor.pan_dmx16}')

# Tilt slew anchor — direction "down" → el<0.
tilt_anchor = legacy_anchors[2]
check('legacy tilt anchor stage el<0 (down)',
      tilt_anchor.el_deg < 0,
      f'got el={tilt_anchor.el_deg:+.2f}')

# Sphere built from collect_anchors (no explicit aimStage) round-trips
# all three anchors.
s_legacy = AimSphere(legacy_fix, PROF_150W)
for a in legacy_anchors:
    pose = s_legacy.aim_direction(a.az_deg, a.el_deg)
    check(f'legacy sphere round-trips ({a.az_deg:+.1f}, {a.el_deg:+.1f})',
          pose == (a.pan_dmx16, a.tilt_dmx16),
          f'got {pose}, expected ({a.pan_dmx16}, {a.tilt_dmx16})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== inverted mount via legacy path (rotation[1]=180°) ===')
# ─────────────────────────────────────────────────────────────────────

inv_fix = dict(legacy_fix, rotation=[0, 180, 0], id=18)
inv_anchors = derive_legacy_anchors(inv_fix, PROF_150W)
# Home rotation-forward under Ry(180°): mount +Y is preserved by Ry,
# so home aim is still stage (0, 0).
check('inverted home anchor still at stage (0, 0)',
      approx(inv_anchors[0].az_deg, 0.0, 0.01)
      and approx(inv_anchors[0].el_deg, 0.0, 0.01),
      f'got ({inv_anchors[0].az_deg:+.2f}, {inv_anchors[0].el_deg:+.2f})')
# Pan slew: mount +X under Ry(180°) → stage -X. Operator says "left" →
# expected stage az>0 (toward +X). Sign-derivation flips → DMX delta
# is NEGATIVE, but anchor stage az is still +90° (operator's call).
check('inverted pan anchor stage az has correct sign (matches operator call)',
      inv_anchors[1].az_deg > 0,
      f'got az={inv_anchors[1].az_deg:+.2f}')
check('inverted pan anchor DMX delta has flipped sign',
      inv_anchors[1].pan_dmx16 < legacy_anchors[1].pan_dmx16,
      f'inv panDmx={inv_anchors[1].pan_dmx16} '
      f'upright panDmx={legacy_anchors[1].pan_dmx16}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== aim_xyz wrapper ===')
# ─────────────────────────────────────────────────────────────────────

xyz_fix = {
    "id": 1, "fixtureType": "dmx",
    "x": 0, "y": 0, "z": 3000,
    "rotation": [0, 0, 0],
    "homePanDmx16": 32768, "homeTiltDmx16": 32768,
    "homeSecondary": {
        "panMovedDirection": "right", "tiltMovedDirection": "down",
        "panOffsetDmx16": 1000, "tiltOffsetDmx16": 1000,
    },
}
s_xyz = AimSphere(xyz_fix, PROF_150W)

# Coincident target → None.
pose = s_xyz.aim_xyz(s_xyz.fixture_xyz)
check('aim_xyz coincident → None', pose is None)

# Forward target.
pose = s_xyz.aim_xyz((0, 5000, 3000))
check('aim_xyz forward target returns a pose', pose is not None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== timing ===')
# ─────────────────────────────────────────────────────────────────────

t0 = time.perf_counter()
for _ in range(10000):
    s14.aim_direction(15.5, 7.5, current_pose=(40000, 35000))
elapsed = time.perf_counter() - t0
per_us = elapsed * 1e6 / 10000
check(f'10k lookups under 1s wall-clock (~{per_us:.1f} µs/call)',
      elapsed < 1.0,
      f'{elapsed:.3f}s for 10000 calls')


# ─────────────────────────────────────────────────────────────────────
print('\n=== failure modes ===')
# ─────────────────────────────────────────────────────────────────────

# Profile missing pan/tilt range → ValueError.
try:
    AimSphere(legacy_fix, {"id": "naked"})
    check('missing panRange/tiltRange raises', False, 'no exception')
except ValueError as e:
    check('missing panRange/tiltRange raises', True)
    check('error names the profile id', 'naked' in str(e),
          f'msg: {e}')

# Fixture missing Home → ValueError.
try:
    AimSphere({"id": "no-home", "fixtureType": "dmx",
                "rotation": [0, 0, 0]}, PROF_150W)
    check('missing Home raises', False)
except ValueError:
    check('missing Home raises', True)

# Fewer than 3 anchors → ValueError.
try:
    AimSphere(xyz_fix, PROF_150W,
                anchors=[CalibrationAnchor(32768, 32768, 0, 0)])
    check('< 3 anchors raises', False)
except ValueError:
    check('< 3 anchors raises', True)


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
