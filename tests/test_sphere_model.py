#!/usr/bin/env python3
"""test_sphere_model.py — #783 PR-β regression tests for the sphere model.

Covers:
  * SphereModel construction from profile + fixture record.
  * dmx_to_direction round-trip with direction_to_poses.
  * Multi-valued pan azimuth (panRange > 360°).
  * Tilt asymmetry / reachability gates.
  * Sign-flip via panSignFromDmx / tiltSignFromDmx.
  * `aim` policy selection (closest / A / B).
  * `aim_world_xyz` wrapper.
  * Fixture rotation handling (mount inversion via rotation[1]=180).

Pure-math; no network, no profile library, no flask app. Run:

    python tests/test_sphere_model.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from sphere_model import (
    SphereModel,
    dmx_to_direction, direction_to_poses, aim, aim_world_xyz,
)

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


def vec_approx(a, b, tol=1e-3):
    return all(approx(x, y, tol) for x, y in zip(a, b))


# ─────────────────────────────────────────────────────────────────────
print('\n=== construction ===')
# ─────────────────────────────────────────────────────────────────────

# Default upright moving-head: pan range 540°, tilt range 270°, home at
# (32768, 32768) — mechanical centre, beam aimed forward (+Y) level.
# Profile signs (`pan_sign`, `tilt_sign`) describe mount-frame mechanics
# only; rotation handles stage-frame inversion. The math composes the
# two with no inversion-aware special cases (#783).
sphere = SphereModel(
    fixture_xyz=(0, 0, 3000),
    fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
check('home pan stored', sphere.home_pan_dmx16 == 32768)
check('rotation list normalised', sphere.fixture_rotation == [0.0, 0.0, 0.0])
check('pan_dmx_per_deg = +65535/540 (pan_sign=+1)',
      approx(sphere._pan_dmx_per_deg, 65535.0 / 540.0))
check('tilt_dmx_per_deg = +65535/270 (tilt_sign=+1)',
      approx(sphere._tilt_dmx_per_deg, 65535.0 / 270.0))

# Companion sphere with tilt_sign=-1 — fixtures wired so +DMX rotates
# the beam in the opposite mount direction. The math doesn't branch on
# tilt-up/down; the profile sign IS the source of truth.
sphere_neg_tilt = SphereModel(
    fixture_xyz=(0, 0, 3000),
    fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=-1,
)
check('tilt_dmx_per_deg = -65535/270 (tilt_sign=-1)',
      approx(sphere_neg_tilt._tilt_dmx_per_deg, -65535.0 / 270.0),
      f'got {sphere_neg_tilt._tilt_dmx_per_deg}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== dmx_to_angles (Home anchors zero) ===')
# ─────────────────────────────────────────────────────────────────────

p, t = sphere.dmx_to_angles(32768, 32768)
check('home → (0, 0)', approx(p, 0) and approx(t, 0), f'got ({p}, {t})')

p, t = sphere.dmx_to_angles(32768 + int(round(65535/540)), 32768)
check('+1 dmx-step in pan from home → +1°', approx(p, 1, 0.05),
      f'got pan_deg={p}')

# Negative pan_sign flips the mapping.
sphere_neg = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=-1, tilt_sign=+1,
)
p, _ = sphere_neg.dmx_to_angles(32768 + int(round(65535/540)), 32768)
check('pan_sign=-1: +1 dmx-step → -1° pan',
      approx(p, -1, 0.05), f'got pan_deg={p}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== dmx_to_direction (forward at home) ===')
# ─────────────────────────────────────────────────────────────────────

# At Home with rotation=[0,0,0], beam aims along stage-+Y.
direction = sphere.dmx_to_direction(32768, 32768)
check('home aim → stage-+Y', vec_approx(direction, (0, 1, 0)),
      f'got {direction}')

# Pan 90° (in fixture frame: panDeg=90 → mount-+X).
pan_dmx_for_90 = 32768 + int(round(90 * 65535 / 540))
direction = sphere.dmx_to_direction(pan_dmx_for_90, 32768)
check('+90° pan from home → stage-+X (rotation=0)',
      vec_approx(direction, (1, 0, 0), tol=0.01),
      f'got {direction}')

# Tilt 45° (mount frame). With tilt_sign=+1, +DMX → mount-+Z. The
# stage-frame interpretation depends on rotation: for an upright fixture
# (rotation=0), mount-+Z = stage-+Z = above horizon.
tilt_dmx_for_45 = 32768 + int(round(45 * 65535 / 270))
direction = sphere.dmx_to_direction(32768, tilt_dmx_for_45)
check('upright + tilt_sign=+1: +DMX-tilt → above horizon',
      direction[2] > 0 and approx(direction[2], math.sin(math.radians(45)), 0.01),
      f'got {direction}')

# Same DMX on a tilt_sign=-1 fixture: opposite mount-frame motion.
# +DMX → mount-(-Z) → for upright (rotation=0), stage-(-Z) = below
# horizon. Profile sign IS the mount-mechanic source; rotation
# (which is identity here) doesn't enter.
direction_inv = sphere_neg_tilt.dmx_to_direction(32768, tilt_dmx_for_45)
check('upright + tilt_sign=-1: +DMX-tilt → below horizon',
      direction_inv[2] < 0 and approx(direction_inv[2], -math.sin(math.radians(45)), 0.01),
      f'got {direction_inv}')

# Stage-frame API contract — `aim_stage_angles(0, +30)` on an upright
# fixture must produce a DMX whose physical aim is 30° above horizon.
# The rotation transform (identity here) and the profile sign compose;
# the math doesn't branch on inversion.
p_dmx, t_dmx = sphere.aim_stage_angles(0.0, 30.0, clamp=True)
direction = sphere.dmx_to_direction(p_dmx, t_dmx)
check('aim_stage_angles(0, +30) upright → physical el ≈ +30°',
      direction[2] > 0 and approx(direction[2], math.sin(math.radians(30)), 0.01),
      f'got {direction}')

p_dmx, t_dmx = sphere.aim_stage_angles(0.0, -30.0, clamp=True)
direction = sphere.dmx_to_direction(p_dmx, t_dmx)
check('aim_stage_angles(0, -30) upright → physical el ≈ -30°',
      direction[2] < 0 and approx(direction[2], -math.sin(math.radians(30)), 0.01),
      f'got {direction}')

# Inverted (rotation=[0,180,0]). aim_stage_angles still produces the
# correct physical aim — rotation handles the inversion in the math
# without any mount-frame sign juggling.
sphere_inv_for_stage = SphereModel(
    fixture_xyz=(0, 0, 3000),
    fixture_rotation=[0, 180, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
p_dmx, t_dmx = sphere_inv_for_stage.aim_stage_angles(0.0, +30.0, clamp=True)
direction = sphere_inv_for_stage.dmx_to_direction(p_dmx, t_dmx)
check('aim_stage_angles(0, +30) inverted → physical el ≈ +30° (rotation handles inversion)',
      direction[2] > 0 and approx(direction[2], math.sin(math.radians(30)), 0.01),
      f'got {direction}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== direction_to_poses round-trip ===')
# ─────────────────────────────────────────────────────────────────────

# Round-trip: dmx → direction → poses → dmx — original DMX should be in poses.
for (pp, tt) in [(32768, 32768), (40000, 35000), (25000, 30000),
                  (50000, 45000), (15000, 20000)]:
    direction = sphere.dmx_to_direction(pp, tt)
    poses = sphere.direction_to_poses(direction)
    found = any(abs(p[0] - pp) <= 2 and abs(p[1] - tt) <= 2 for p in poses)
    check(f'round-trip dmx({pp},{tt}) → direction → poses ⊇ orig',
          found, f'poses={poses}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== multi-valued azimuth (540° pan fixture) ===')
# ─────────────────────────────────────────────────────────────────────

# A 540° pan fixture covers ~180° of azimuth twice. Aiming at any
# direction in that doubled band should produce 2 distinct DMX poses.

# Direction along stage +Y (azimuth 0°). With 540° range centred on
# home, pan at +0° AND pan at +360° both reach there, so we expect 2.
direction_pos_y = (0, 1, 0)
poses = sphere.direction_to_poses(direction_pos_y)
# Whether multi-valued depends on the home position relative to the
# DMX limits. Home at 32768 with ±270° pan range each side: panDeg=0 →
# DMX 32768 (in range); panDeg=+360 → DMX 32768 + 360 * 121.36 ≈ 76456
# (out of 65535 range — clamped). panDeg=-360 → DMX -10920 (out).
# So 540° centred fixtures with home at midpoint have ONE pose at 0°.
check('panRange=540, home=mid: forward direction has 1 pose',
      len(poses) == 1, f'poses={poses}')

# Now home anchored near one edge: home at panDmx16=10923 (pan range
# allows ~+360° via positive deltas).
sphere_off_home = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=10923, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
poses = sphere_off_home.direction_to_poses(direction_pos_y)
check('panRange=540, home=10923 (low): forward direction has 2 poses',
      len(poses) == 2, f'poses={poses}')
# The two poses should be 360° apart in DMX space (= 360 * 121.36 ≈ 43690).
if len(poses) == 2:
    delta_pan = poses[1][0] - poses[0][0]
    check('panRange=540, two poses 360° apart in DMX',
          43000 <= delta_pan <= 44500, f'delta_pan={delta_pan}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== tilt reachability ===')
# ─────────────────────────────────────────────────────────────────────

# A narrow-tilt fixture: tiltRange=60° centred on Home → reachable
# tilt is ±30° from horizon. Aim at tilt=+45° → unreachable.
sphere_narrow = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=180, tilt_range_deg=60,
    pan_sign=+1, tilt_sign=+1,
)
# tilt=+45° direction in mount frame: (0, cos(45°), sin(45°))
tilt_too_high = (0,
                  math.cos(math.radians(45)),
                  math.sin(math.radians(45)))
poses = sphere_narrow.direction_to_poses(tilt_too_high)
check('narrow-tilt fixture: tilt=+45° (above ±30° range) unreachable',
      poses == [], f'poses={poses}')

# Within reach: tilt=+15°.
tilt_in_reach = (0,
                  math.cos(math.radians(15)),
                  math.sin(math.radians(15)))
poses = sphere_narrow.direction_to_poses(tilt_in_reach)
check('narrow-tilt fixture: tilt=+15° (within ±30° range) reachable',
      len(poses) >= 1, f'poses={poses}')

# Aiming straight DOWN (tilt=-90°) at the wide 540°×270° sphere with
# home-mid: beam below horizon, well within ±135° tilt range.
direction_down = (0, 0, -1)
poses = sphere.direction_to_poses(direction_down)
check('wide fixture: tilt=-90° (straight down) reachable',
      len(poses) >= 1, f'poses={poses}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== aim policy selection ===')
# ─────────────────────────────────────────────────────────────────────

# Multi-valued pan: with two poses, A picks the lower DMX, B the higher.
poses = sphere_off_home.direction_to_poses(direction_pos_y)
if len(poses) == 2:
    a = sphere_off_home.aim(direction_pos_y, prefer="A")
    b = sphere_off_home.aim(direction_pos_y, prefer="B")
    check('aim prefer=A returns lowest-pan pose',
          a == poses[0], f'a={a} poses[0]={poses[0]}')
    check('aim prefer=B returns highest-pan pose',
          b == poses[-1], f'b={b} poses[-1]={poses[-1]}')
    check('A and B differ when multi-valued', a != b)

    # closest=current_pose. If current is near A, "closest" picks A.
    near_a = (poses[0][0] + 100, poses[0][1] + 50)
    closest = sphere_off_home.aim(direction_pos_y, current_pose=near_a, prefer="closest")
    check('closest picks A when current is near A', closest == poses[0])
    near_b = (poses[1][0] - 100, poses[1][1] - 50)
    closest = sphere_off_home.aim(direction_pos_y, current_pose=near_b, prefer="closest")
    check('closest picks B when current is near B', closest == poses[1])

# Single-valued: "A"/"B"/"closest" all return the same pose.
direction_misc = sphere.dmx_to_direction(40000, 35000)
poses = sphere.direction_to_poses(direction_misc)
check('mid-range direction has exactly 1 pose', len(poses) == 1,
      f'poses={poses}')
if len(poses) == 1:
    a = sphere.aim(direction_misc, prefer="A")
    b = sphere.aim(direction_misc, prefer="B")
    c = sphere.aim(direction_misc, prefer="closest")
    check('single-valued: A == B == closest', a == b == c)

# Unreachable target → aim returns None.
unreachable = (0,
                math.cos(math.radians(45)),
                math.sin(math.radians(45)))
check('aim returns None on unreachable (narrow fixture, tilt+45°)',
      sphere_narrow.aim(unreachable) is None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== fixture rotation (#780 P1: rotation is the truth) ===')
# ─────────────────────────────────────────────────────────────────────

# Inverted ceiling mount: rotation=[0, 180, 0]. Same Home pose.
sphere_inv = SphereModel(
    fixture_xyz=(0, 0, 3000),
    fixture_rotation=[0, 180, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
direction_inv = sphere_inv.dmx_to_direction(32768, 32768)
# At Home with rotation=[0,180,0], beam should still aim at the
# stage-rotation-forward axis. The +180° roll about Y inverts ±X and
# ±Z; mount-+Y stays mount-+Y. So aim is still stage-+Y.
check('inverted mount Home → stage-+Y (Y axis preserved by ry=180)',
      vec_approx(direction_inv, (0, 1, 0), tol=0.01),
      f'got {direction_inv}')

# Pan +90° on inverted mount: mount-+X → stage-(-X) (because rolling
# +180° about Y flips +X to -X).
direction_inv_90 = sphere_inv.dmx_to_direction(pan_dmx_for_90, 32768)
check('inverted pan+90° → stage-(-X) (mirrored)',
      vec_approx(direction_inv_90, (-1, 0, 0), tol=0.01),
      f'got {direction_inv_90}')

# Same world target → upright + inverted produce DIFFERENT DMX poses
# (since the mechanics must counter-rotate to hit the same direction).
target = (1.0, 0.5, -0.3)
norm = math.sqrt(sum(c * c for c in target))
target = tuple(c / norm for c in target)
pose_up = sphere.aim(target)
pose_inv = sphere_inv.aim(target)
check('upright vs inverted produce different DMX poses for same aim',
      pose_up != pose_inv,
      f'upright={pose_up} inverted={pose_inv}')

# But the resulting BEAM direction should be the same (within IK tolerance).
if pose_up and pose_inv:
    aim_up = sphere.dmx_to_direction(*pose_up)
    aim_inv = sphere_inv.dmx_to_direction(*pose_inv)
    check('different DMX, same world aim',
          vec_approx(aim_up, aim_inv, tol=0.01),
          f'aim_up={aim_up} aim_inv={aim_inv}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== aim_world_xyz wrapper ===')
# ─────────────────────────────────────────────────────────────────────

# Target straight ahead at +Y: aim should match Home pose.
sphere_origin = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
)
pose = aim_world_xyz((0, 5000, 0), sphere_origin)
check('aim_world_xyz: +Y target → pan at home',
      pose is not None and abs(pose[0] - 32768) <= 2,
      f'pose={pose}')
check('aim_world_xyz: +Y target → tilt at home (level)',
      pose is not None and abs(pose[1] - 32768) <= 2,
      f'pose={pose}')

# Target above the fixture (Z+): on an upright fixture (sphere_origin
# rotation=[0,0,0]) with tilt_sign=+1, mount tilt-up = +DMX delta.
pose = aim_world_xyz((0, 100, 5000), sphere_origin)
check('aim_world_xyz: high target → DMX > home (upright, tilt_sign=+1)',
      pose is not None and pose[1] > 32768,
      f'pose={pose}')

# tilt_sign=-1 fixture: +DMX rotates the beam in the opposite mount
# direction, so reaching the same high target requires DMX < home.
sphere_origin_neg = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=-1,
)
pose_neg = aim_world_xyz((0, 100, 5000), sphere_origin_neg)
check('aim_world_xyz: high target → DMX < home (upright, tilt_sign=-1)',
      pose_neg is not None and pose_neg[1] < 32768,
      f'pose={pose_neg}')

# Coincident with fixture position → None (degenerate).
pose = aim_world_xyz((0, 0, 0), sphere_origin)
check('aim_world_xyz: target at fixture origin → None',
      pose is None, f'pose={pose}')

# Module-level aliases.
direction = dmx_to_direction(32768, 32768, sphere)
check('module dmx_to_direction alias works',
      vec_approx(direction, (0, 1, 0)))
poses = direction_to_poses(direction_pos_y, sphere)
check('module direction_to_poses alias works', isinstance(poses, list))
result = aim(direction_pos_y, None, sphere)
check('module aim alias works', result is not None)


# ─────────────────────────────────────────────────────────────────────
print('\n=== from_fixture builder ===')
# ─────────────────────────────────────────────────────────────────────

# Construct from a fixture record + profile_info dict.
fixture = {
    "id": 17, "x": 600, "y": 0, "z": 1760,
    "rotation": [0, 180, 0],  # ceiling-mount inverted, post-#780 P1 bake
    "homePanDmx16": 44364, "homeTiltDmx16": 0,
}
profile_info = {
    "panRange": 540, "tiltRange": 180,
    "panSignFromDmx": 1, "tiltSignFromDmx": 1,
}
fid17 = SphereModel.from_fixture(fixture, profile_info)
check('from_fixture: pan_range', fid17.pan_range_deg == 540)
check('from_fixture: tilt_range', fid17.tilt_range_deg == 180)
check('from_fixture: home_pan', fid17.home_pan_dmx16 == 44364)
check('from_fixture: rotation baked', fid17.fixture_rotation == [0.0, 180.0, 0.0])
check('from_fixture: pan_sign', fid17.pan_sign == 1)

# At Home, fid17 aims along its rotation_forward — with rotation=[0,180,0]
# that's stage-+Y (the +180° roll keeps +Y → +Y). The inverted mount's
# mechanical effect on +X / -X is what differs from upright.
direction_home = fid17.dmx_to_direction(44364, 0)
check('fid17 home → stage-+Y',
      vec_approx(direction_home, (0, 1, 0), tol=0.01),
      f'got {direction_home}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== world_to_fixture_pt round-trip (#783 acceptance #3) ===')
# ─────────────────────────────────────────────────────────────────────
#
# The issue's #3 acceptance: world_to_fixture_pt(target) → aim-angles
# round-trip must be byte-identical, with no sign-flipping at the call
# site. The sphere model's `direction_to_poses(target)` and
# `world_to_fixture_pt → angles_to_dmx` paths must produce matching DMX.

from coverage_math import world_to_fixture_pt as cm_world_to_fixture_pt

def _round_trip_check(label, sphere, target_xyz):
    """world_to_fixture_pt path → DMX, vs sphere `aim_world_xyz` → DMX.
    Both should land within 2 DMX units of each other."""
    angles = cm_world_to_fixture_pt(target_xyz, sphere.fixture_xyz,
                                     sphere.fixture_rotation)
    if angles is None:
        check(f'{label}: world_to_fixture_pt non-degenerate', False,
              'angles=None')
        return
    pan_deg, tilt_deg = angles
    direct_pan, direct_tilt = sphere.angles_to_dmx(pan_deg, tilt_deg, clamp=True)
    pose = aim_world_xyz(target_xyz, sphere)
    if pose is None:
        check(f'{label}: aim_world_xyz reachable', False,
              f'unreachable angles=({pan_deg}, {tilt_deg})')
        return
    aw_pan, aw_tilt = pose
    check(f'{label}: angles_to_dmx ≡ aim_world_xyz pan',
          abs(direct_pan - aw_pan) <= 2,
          f'angles_to_dmx={direct_pan} aim_world_xyz={aw_pan}')
    check(f'{label}: angles_to_dmx ≡ aim_world_xyz tilt',
          abs(direct_tilt - aw_tilt) <= 2,
          f'angles_to_dmx={direct_tilt} aim_world_xyz={aw_tilt}')

# Round-trip uses fixtures whose Home is driven to the convention
# (rotation_forward at horizon level). Operator-broken Homes (e.g. fid
# 17's homeTiltDmx16=0 parking the head at a mechanical extreme rather
# than horizon) are not valid acceptance inputs — they're workflow bugs
# the operator must correct in the SPA, not sphere-model bugs.

# Upright tilt_sign=+1 (most moving heads):
sphere_upright = SphereModel(
    fixture_xyz=(0, 0, 3000), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
_round_trip_check('upright tilt_sign=+1 forward', sphere_upright, (0.0, 5000.0, 3000.0))
_round_trip_check('upright tilt_sign=+1 +X target', sphere_upright, (3000.0, 5000.0, 1000.0))
_round_trip_check('upright tilt_sign=+1 floor target', sphere_upright, (0.0, 5000.0, 0.0))

# Upright tilt_sign=-1 (fixtures wired so +DMX rotates beam opposite):
sphere_upright_neg = SphereModel(
    fixture_xyz=(0, 0, 3000), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=180,
    pan_sign=+1, tilt_sign=-1,
)
_round_trip_check('upright tilt_sign=-1 forward', sphere_upright_neg, (0.0, 5000.0, 3000.0))
_round_trip_check('upright tilt_sign=-1 floor target', sphere_upright_neg, (0.0, 5000.0, 0.0))

# Inverted ceiling-mount (rotation handles inversion; profile signs
# stay mount-frame). Beam aims at floor markers — same DMX math as
# upright, just composed through the rotation matrix.
sphere_ceiling = SphereModel(
    fixture_xyz=(600, 0, 1760), fixture_rotation=[0, 180, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=180,
    pan_sign=+1, tilt_sign=+1,
)
_round_trip_check('ceiling-inverted floor-forward', sphere_ceiling, (600.0, 2000.0, 0.0))
_round_trip_check('ceiling-inverted +X floor', sphere_ceiling, (1500.0, 2000.0, 0.0))


# ─────────────────────────────────────────────────────────────────────
print('\n=== stage convention: aim_stage_angles ≡ above horizon ===')
# ─────────────────────────────────────────────────────────────────────
#
# Operator's bug report (2026-05-02): aim-angles {tiltDeg:+30} was
# producing physical aim DOWN on inverted fid 17. The fix is at the API
# surface — `aim_stage_angles` builds a stage-frame direction, applies
# `fixture.rotation` to get mount-frame, then translates mount-frame to
# DMX via profile signs. Rotation handles inversion; the math doesn't
# branch on whether the fixture is upright or ceiling-mounted.

# Upright fixture: aim_stage_angles(0, +30) must produce above-horizon.
sphere_up = SphereModel(
    fixture_xyz=(0, 0, 3000), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=180,
    pan_sign=+1, tilt_sign=+1,
)
direction_up = sphere_up.dmx_to_direction(
    *sphere_up.aim_stage_angles(0, 30, clamp=True))
check('upright aim_stage_angles(0, +30) → physical aim above horizon (z>0)',
      direction_up[2] > 0, f'got direction={direction_up}')

direction_down = sphere_up.dmx_to_direction(
    *sphere_up.aim_stage_angles(0, -30, clamp=True))
check('upright aim_stage_angles(0, -30) → physical aim below horizon (z<0)',
      direction_down[2] < 0, f'got direction={direction_down}')

# Inverted fixture: same stage-frame angles must produce same physical
# direction. Rotation handles the inversion in the math; no profile or
# sphere-model branching.
sphere_inv = SphereModel(
    fixture_xyz=(0, 0, 3000), fixture_rotation=[0, 180, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=180,
    pan_sign=+1, tilt_sign=+1,
)
direction_inv_up = sphere_inv.dmx_to_direction(
    *sphere_inv.aim_stage_angles(0, 30, clamp=True))
check('inverted aim_stage_angles(0, +30) → physical aim above horizon (z>0)',
      direction_inv_up[2] > 0, f'got direction={direction_inv_up}')

direction_inv_down = sphere_inv.dmx_to_direction(
    *sphere_inv.aim_stage_angles(0, -30, clamp=True))
check('inverted aim_stage_angles(0, -30) → physical aim below horizon (z<0)',
      direction_inv_down[2] < 0, f'got direction={direction_inv_down}')

# Mount-frame `angles_to_dmx` (the low-level helper) takes mount-frame
# angles directly. For an upright fixture with tilt_sign=+1, mount
# tilt+30 = mount-+Z = stage-+Z above horizon (rotation is identity).
sphere_350w = SphereModel(
    fixture_xyz=(0, 0, 0), fixture_rotation=[0, 0, 0],
    home_pan_dmx16=32768, home_tilt_dmx16=32768,
    pan_range_deg=540, tilt_range_deg=270,
    pan_sign=+1, tilt_sign=+1,
)
direction_mount = sphere_350w.dmx_to_direction(*sphere_350w.angles_to_dmx(0, 30, clamp=True))
check('upright + tilt_sign=+1: mount tiltDeg=+30 → above horizon',
      direction_mount[2] > 0, f'got direction={direction_mount}')


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
