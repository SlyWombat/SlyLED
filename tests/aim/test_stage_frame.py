#!/usr/bin/env python3
"""tests/aim/test_stage_frame.py — #784 PR-2 stage-frame conversion tests.

Stage convention assertions per CLAUDE.md `## Angular-aim convention`:
  * `el_deg > 0` ⇔ beam above stage horizon (toward `+Z`).
  * `az_deg > 0` ⇔ beam swept toward stage `+X`.
  * Stage frame is Z-up.

Pure-math; no Flask, no profile library.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

from aim.stage_frame import (
    mechanical_to_stage_aim,
    stage_aim_to_mechanical,
    stage_aim_from_world_xyz,
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


# ─────────────────────────────────────────────────────────────────────
print('=== mechanical_to_stage_aim: upright fixture ===')
# ─────────────────────────────────────────────────────────────────────

# Upright (rotation=[0,0,0]), home at mechanical zero.
az, el = mechanical_to_stage_aim(0, 0, [0, 0, 0])
check('mech (0,0) upright → (az=0, el=0) — forward at horizon',
      approx(az, 0) and approx(el, 0), f'got ({az}, {el})')

az, el = mechanical_to_stage_aim(0, 30, [0, 0, 0])
check('mech tilt+30 upright → el=+30 (above horizon)',
      approx(az, 0) and approx(el, 30), f'got ({az}, {el})')

az, el = mechanical_to_stage_aim(0, -30, [0, 0, 0])
check('mech tilt-30 upright → el=-30 (below horizon)',
      approx(az, 0) and approx(el, -30), f'got ({az}, {el})')

az, el = mechanical_to_stage_aim(45, 0, [0, 0, 0])
check('mech pan+45 upright → az=+45 (toward stage +X)',
      approx(az, 45) and approx(el, 0), f'got ({az}, {el})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== mechanical_to_stage_aim: ceiling-inverted fixture ===')
# ─────────────────────────────────────────────────────────────────────
# Inverted (rotation=[0,180,0]) — Y-axis preserved (mount-+Y ↔ stage-+Y),
# X and Z flip. Stage convention end-to-end: rotation handles the
# inversion, the math composes without special cases.

az, el = mechanical_to_stage_aim(0, 0, [0, 180, 0])
check('inverted mech (0,0) → (az=0, el=0) — forward at horizon (Y preserved)',
      approx(az, 0) and approx(el, 0), f'got ({az}, {el})')

# +mech_tilt rotates beam toward mount-+Z. Inverted rotation maps
# mount-+Z → stage-(-Z) = below horizon. So mech tilt+30 on inverted
# fixture → el=-30 in stage frame.
az, el = mechanical_to_stage_aim(0, 30, [0, 180, 0])
check('inverted mech tilt+30 → stage el=-30 (rotation flips Z)',
      approx(az, 0) and approx(el, -30), f'got ({az}, {el})')

# +mech_pan on inverted: mount-+X → stage-(-X) = stage-RIGHT (per
# CLAUDE.md +X=stage-left, so -X=stage-right). az should be -45.
az, el = mechanical_to_stage_aim(45, 0, [0, 180, 0])
check('inverted mech pan+45 → stage az=-45 (stage-right)',
      approx(az, -45) and approx(el, 0), f'got ({az}, {el})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== stage_aim_to_mechanical inverse round-trip ===')
# ─────────────────────────────────────────────────────────────────────

for label, rotation in [
    ("upright", [0, 0, 0]),
    ("inverted", [0, 180, 0]),
    ("yawed", [0, 0, 30]),
    ("pitched-down", [15, 0, 0]),
]:
    for az_in, el_in in [(0, 0), (30, 15), (-45, 20), (90, -30), (-120, 45)]:
        mp, mt = stage_aim_to_mechanical(az_in, el_in, rotation)
        az_back, el_back = mechanical_to_stage_aim(mp, mt, rotation)
        ok_az = approx(az_back, az_in, 1e-2)
        ok_el = approx(el_back, el_in, 1e-2)
        check(f'{label} round-trip ({az_in:+}, {el_in:+}) → mech → stage',
              ok_az and ok_el,
              f'got ({az_back:.4f}, {el_back:.4f})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== home offset is baked into mechanics (no home_mech_* args) ===')
# ─────────────────────────────────────────────────────────────────────
# Per #784 comment 3: `profile_mechanics.dmx_to_mechanical` already
# subtracts the home anchor; mechanical (0, 0) IS the home pose. So
# `mechanical_to_stage_aim(0, 0, rotation)` always reports stage
# (0, 0) regardless of where the operator parked Home in DMX.
az, el = mechanical_to_stage_aim(0, 0, [0, 0, 0])
check('mech (0, 0) at home pose → stage (0, 0)',
      approx(az, 0) and approx(el, 0), f'got ({az}, {el})')

# Mechanical (5, 0) is +5° pan from home → stage az=+5.
az, el = mechanical_to_stage_aim(5, 0, [0, 0, 0])
check('mech +5° pan → stage az=+5', approx(az, 5),
      f'got ({az}, {el})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== stage_aim_from_world_xyz ===')
# ─────────────────────────────────────────────────────────────────────

# Target +Y of fixture → az=0, el=0.
az, el = stage_aim_from_world_xyz((0, 5000, 0), (0, 0, 0))
check('target +Y of fixture origin → (0, 0)',
      approx(az, 0) and approx(el, 0))

# Target +X of fixture → az=+90.
az, el = stage_aim_from_world_xyz((5000, 0, 0), (0, 0, 0))
check('target +X of fixture origin → az=+90',
      approx(az, 90) and approx(el, 0), f'got ({az}, {el})')

# Target above fixture → el=+90 (zenith — az is degenerate).
_, el = stage_aim_from_world_xyz((0, 0, 5000), (0, 0, 0))
check('target straight up → el=+90',
      approx(el, 90), f'got el={el}')

# Coincident → None.
result = stage_aim_from_world_xyz((100, 200, 300), (100, 200, 300))
check('coincident target returns None', result is None)


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
