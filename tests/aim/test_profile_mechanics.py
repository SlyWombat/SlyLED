#!/usr/bin/env python3
"""tests/aim/test_profile_mechanics.py — #784 comment-3 tests.

Per operator-clarified design (2026-05-02): mechanics are derived from
`(panRange, tiltRange, home_pan_dmx16, home_tilt_dmx16)` only. No
`dmxToMechanical` profile metadata, no per-axis sign fields. Home is
the angular zero by convention; slope is positive.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

from aim.profile_mechanics import (
    dmx_to_mechanical, mechanical_to_dmx, reachable_mechanical_range,
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


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ─────────────────────────────────────────────────────────────────────
print('=== dmx_to_mechanical: home-anchored zero ===')
# ─────────────────────────────────────────────────────────────────────

# 150W-shape: panRange=540, tiltRange=180, home at midpoint.
PAN_RANGE = 540.0
TILT_RANGE = 180.0
HOME_PAN = 32768
HOME_TILT = 32768

p, t = dmx_to_mechanical(HOME_PAN, HOME_TILT, PAN_RANGE, TILT_RANGE,
                          HOME_PAN, HOME_TILT)
check('home DMX → mech (0, 0)', approx(p, 0) and approx(t, 0),
      f'got ({p}, {t})')

# +1° pan from home: DMX delta = 65535/540.
p, _ = dmx_to_mechanical(HOME_PAN + int(round(65535 / 540)), HOME_TILT,
                          PAN_RANGE, TILT_RANGE, HOME_PAN, HOME_TILT)
check('+1° pan: DMX delta → +1° mech',
      approx(p, 1.0, 0.01), f'got pan_deg={p}')

# +1° tilt: DMX delta = 65535/180.
_, t = dmx_to_mechanical(HOME_PAN, HOME_TILT + int(round(65535 / 180)),
                          PAN_RANGE, TILT_RANGE, HOME_PAN, HOME_TILT)
check('+1° tilt: DMX delta → +1° mech',
      approx(t, 1.0, 0.01), f'got tilt_deg={t}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== round-trip dmx ↔ mech ===')
# ─────────────────────────────────────────────────────────────────────

for (pdmx, tdmx) in [(0, 0), (10000, 50000), (32768, 32768), (65535, 0)]:
    pm, tm = dmx_to_mechanical(pdmx, tdmx, PAN_RANGE, TILT_RANGE,
                                 HOME_PAN, HOME_TILT)
    rp, rt = mechanical_to_dmx(pm, tm, PAN_RANGE, TILT_RANGE,
                                 HOME_PAN, HOME_TILT)
    check(f'round-trip dmx({pdmx},{tdmx})',
          approx(rp, pdmx, 1e-3) and approx(rt, tdmx, 1e-3),
          f'got ({rp}, {rt})')


# ─────────────────────────────────────────────────────────────────────
print('\n=== off-centre home (operator drove Home off mid-range) ===')
# ─────────────────────────────────────────────────────────────────────

# fid 17: home_tilt=0 (operator drove tilt to a mechanical extreme as
# their Home reference). The mech-zero is wherever DMX = home.
HOME_PAN_FID17 = 44364
HOME_TILT_FID17 = 0

p, t = dmx_to_mechanical(HOME_PAN_FID17, HOME_TILT_FID17,
                          PAN_RANGE, TILT_RANGE,
                          HOME_PAN_FID17, HOME_TILT_FID17)
check('fid17 home → mech (0, 0)',
      approx(p, 0) and approx(t, 0), f'got ({p}, {t})')

# DMX = home + 32768 → +90° tilt mech (half of tiltRange=180).
_, t = dmx_to_mechanical(HOME_PAN_FID17, HOME_TILT_FID17 + 32768,
                          PAN_RANGE, TILT_RANGE,
                          HOME_PAN_FID17, HOME_TILT_FID17)
check('fid17 +half-range tilt → mech +90°',
      approx(t, 90.0, 0.01), f'got tilt_deg={t}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== reachable_mechanical_range ===')
# ─────────────────────────────────────────────────────────────────────

# Home at midpoint: range is symmetric, ±panRange/2, ±tiltRange/2.
(pan_lo, pan_hi), (tilt_lo, tilt_hi) = reachable_mechanical_range(
    PAN_RANGE, TILT_RANGE, HOME_PAN, HOME_TILT)
check('mid-home pan reach: -270° lo', approx(pan_lo, -270.0, 1e-2),
      f'got {pan_lo}')
check('mid-home pan reach: +270° hi', approx(pan_hi, +270.0, 1e-2),
      f'got {pan_hi}')
check('mid-home tilt reach: -90° lo', approx(tilt_lo, -90.0, 1e-2))
check('mid-home tilt reach: +90° hi', approx(tilt_hi, +90.0, 1e-2))

# Off-centre home: asymmetric reach. fid17 home_tilt=0 → reachable
# tilt is [0, +180°] (DMX 0..65535 mapped through the home-zero anchor).
(_, _), (tilt_lo17, tilt_hi17) = reachable_mechanical_range(
    PAN_RANGE, TILT_RANGE, HOME_PAN_FID17, HOME_TILT_FID17)
check('fid17 tilt reach lo = 0', approx(tilt_lo17, 0.0, 1e-2),
      f'got {tilt_lo17}')
check('fid17 tilt reach hi = +180°', approx(tilt_hi17, 180.0, 1e-2),
      f'got {tilt_hi17}')


# ─────────────────────────────────────────────────────────────────────
print('\n=== zero range edge cases ===')
# ─────────────────────────────────────────────────────────────────────

# Zero panRange (non-moving fixture): mech_pan = 0 regardless of DMX.
p, _ = dmx_to_mechanical(50000, 32768, 0, TILT_RANGE, HOME_PAN, HOME_TILT)
check('panRange=0 → mech_pan = 0', approx(p, 0), f'got {p}')

# Inverse: zero range → DMX = home regardless of mech.
rp, _ = mechanical_to_dmx(45.0, 0.0, 0, TILT_RANGE, HOME_PAN, HOME_TILT)
check('panRange=0 inverse → DMX = home', approx(rp, HOME_PAN), f'got {rp}')


# ─────────────────────────────────────────────────────────────────────
print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
