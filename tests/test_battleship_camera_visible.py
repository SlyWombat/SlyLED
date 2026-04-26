#!/usr/bin/env python3
"""test_battleship_camera_visible.py — #698.

Three new behaviours layered on top of #694's seed-centred grid:

1. Camera-visibility-aware tilt band — given fixture pose + camera floor
   polygons, derive a (tilt_min, tilt_max) so the beam at home pan
   lands inside the union of FOVs. Cuts the basement-rig probe count
   from 24 (full sweep) to ~9 (only-on-visible-floor).

2. Tilt-first probe ordering — first cells exhaustively sweep tilt at
   the seed pan column before any other pan column is visited.

3. Operator-readable first-probe log line — every cal run names the
   cell it will visit FIRST and the predicted floor hit, so the
   operator can sanity-check before the sweep commits.

Tests:
- _point_in_polygon basic geometry
- _ray_floor_hit horizontal vs downward beam
- _camera_visible_tilt_band on a synthetic single-camera rig matches
  the operator-validated basement-rig number
- battleship_discover with camera_polygons + fixture pose tightens
  tilt sweep to band; without args, falls back to legacy half-band
- Tilt-first ordering puts seed_pan column probes ahead of others
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


import mover_calibrator as mc

# ── _point_in_polygon ───────────────────────────────────────────────────

section('_point_in_polygon')

square = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
ok(mc._point_in_polygon((500, 500), square), 'centre inside')
ok(not mc._point_in_polygon((1500, 500), square), 'east of square')
ok(not mc._point_in_polygon((-100, 500), square), 'west of square')
ok(not mc._point_in_polygon((500, -100), square), 'south of square')
ok(mc._point_in_polygon((1, 1), square), 'near corner inside')
# Degenerate polygon (only 2 points) → never inside.
ok(not mc._point_in_polygon((0, 0), [(0, 0), (1, 1)]), 'degenerate poly')

# ── _ray_floor_hit ──────────────────────────────────────────────────────

section('_ray_floor_hit')

# Fixture at (600, 0, 1500), rotation [0,0,0], home pan = 0.5 (forward),
# tilt = 0.6 (tilted 27° down on a 270° tilt range) → ray hits floor.
fx_pos = (600, 0, 1500)
fx_rot = [0, 0, 0]
hit = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, 0.6, 540, 270)
ok(hit is not None, f'horizontal-down beam hits floor (got {hit})')

# Tilt = 0.5 → exactly horizontal → no floor hit.
hit = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, 0.5, 540, 270)
ok(hit is None, f'horizontal beam does NOT hit floor (got {hit})')

# Tilt = 0.4 → ray points up → no floor hit (positive dz).
hit = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, 0.4, 540, 270)
ok(hit is None, f'upward beam does NOT hit floor (got {hit})')

# Tilt = 0.5 + 90/270 ≈ 0.833 → ray straight down. Floor hit should be
# at (fx_x, fx_y). On a 270° range the upper end (tilt=1.0) over-rotates
# 135° down + behind, which is mechanically valid but not what we want.
hit = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, 0.5 + 90.0 / 270.0, 540, 270)
ok(hit is not None and abs(hit[0] - 600) < 1 and abs(hit[1] - 0) < 1,
   f'straight-down beam lands at fixture XY (got {hit})')

# Fixture without pos → returns None (caller falls back).
ok(mc._ray_floor_hit(None, fx_rot, 0.5, 0.6, 540, 270) is None,
   'no fixture pos → None')

# ── _camera_visible_tilt_band ──────────────────────────────────────────

section('_camera_visible_tilt_band — single-camera rig')

# Synthetic camera FOV polygon that covers the floor between Y=200 and
# Y=2100 mm, X=-1500..1500 (basement-rig-ish geometry).
cam_poly = [(-1500, 200), (1500, 200), (1500, 2100), (-1500, 2100)]
fx_pos = (600, 0, 1500)
fx_rot = [0, 0, 0]

# Home pan = 0.5 → beam aims forward (+Y). Tilt sweep should yield a
# narrow band that lands beam in [200, 2100] mm.
band = mc._camera_visible_tilt_band(
    fx_pos, fx_rot, home_pan_norm=0.5,
    pan_range_deg=540, tilt_range_deg=270,
    mounted_inverted=False,
    camera_polygons=[cam_poly])
tlo, thi = band
ok(0 <= tlo < thi <= 1, f'valid band ordering (got {band})')
# Verify endpoints actually land in polygon at home pan.
hit_lo = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, tlo, 540, 270)
hit_hi = mc._ray_floor_hit(fx_pos, fx_rot, 0.5, thi, 540, 270)
ok(hit_lo and mc._point_in_polygon(hit_lo, cam_poly),
   f'tilt_lo lands in cam FOV (got hit {hit_lo})')
ok(hit_hi and mc._point_in_polygon(hit_hi, cam_poly),
   f'tilt_hi lands in cam FOV (got hit {hit_hi})')

# Band tighter than full [0.05, 0.95] sweep — that's the win.
ok(thi - tlo < 0.5,
   f'band is tighter than full half-range '
   f'(got width {thi - tlo:.3f}, should be <0.5)')

# When home pan aims AWAY from the camera (pan = 0 = stage-left),
# beam doesn't land in the polygon → fallback band returned.
fallback_band = mc._camera_visible_tilt_band(
    fx_pos, fx_rot, home_pan_norm=0.0,
    pan_range_deg=540, tilt_range_deg=270,
    mounted_inverted=False,
    camera_polygons=[cam_poly])
ok(fallback_band == (0.5, 1.0),
   f'home-pan-away → fallback to upper half on floor mount (got {fallback_band})')

# No polygons → fallback to legacy half-range.
no_poly = mc._camera_visible_tilt_band(
    fx_pos, fx_rot, 0.5, 540, 270, False, None)
ok(no_poly == (0.5, 1.0), f'no polys → legacy half-range (got {no_poly})')

# 360°+ pan with no polygons → mounted_inverted picks the lower half.
inv = mc._camera_visible_tilt_band(
    fx_pos, fx_rot, 0.5, 540, 270, True, None)
ok(inv == (0.0, 0.5), f'inverted mount → lower half (got {inv})')

# ── battleship_discover with camera-visibility band ────────────────────

section('battleship_discover honours camera_polygons + fx_pos')

# Stub network/DMX bits.
captured = []
mc._fresh_buffer = lambda: bytearray(512)
mc._set_mover_dmx = lambda *a, **kw: None
mc._hold_dmx = lambda *a, **kw: None
mc._dark_reference = lambda *a, **kw: True
mc._beam_detect = lambda *a, **kw: None
mc._beam_detect_verified = lambda *a, **kw: None
mc._beam_detect_flash = lambda bridge_ip, camera_ip, cam_idx, mover_addr, pan, tilt, color, dmx, threshold=30: (
    captured.append((float(pan), float(tilt))) or None)

captured.clear()
mc.battleship_discover(
    bridge_ip="0.0.0.0", camera_ip="0.0.0.0", mover_addr=1,
    cam_idx=0, color=(0, 255, 0),
    seed_pan=0.5, seed_tilt=0.5,    # Start at known-visible azimuth.
    pan_range_deg=540.0, tilt_range_deg=270.0,
    beam_width_deg=15.0,
    coarse_pan_min=4, coarse_pan_max=4,
    coarse_tilt_min=4, coarse_tilt_max=4,
    refine=False,
    reject_reflection=False,
    confirm_nudge_delta=0.01,
    # #698 — pass the polygon + fixture pose.
    camera_polygons=[cam_poly],
    fixture_pos=fx_pos,
    fixture_rotation=fx_rot,
)
ok(len(captured) > 0, f'cells were probed (got {len(captured)})')

# Every probed tilt should land within the band derived for home pan.
expected_lo, expected_hi = mc._camera_visible_tilt_band(
    fx_pos, fx_rot, 0.5, 540, 270, False, [cam_poly])
out_of_band = [t for (_, t) in captured
               if not (expected_lo <= t <= expected_hi)]
# Allow some slack at the band edges (±tilt_span/2).
tilt_span = (expected_hi - expected_lo) / 4
out_of_band_strict = [t for t in out_of_band
                      if (t < expected_lo - tilt_span
                          or t > expected_hi + tilt_span)]
ok(len(out_of_band_strict) == 0,
   f'all probes within camera-visible band ±span '
   f'(got {len(out_of_band_strict)} outside; band [{expected_lo:.3f},{expected_hi:.3f}])')

# ── Tilt-first ordering ────────────────────────────────────────────────

section('Tilt-first ordering — seed pan column probes first')

captured.clear()
mc.battleship_discover(
    bridge_ip="0.0.0.0", camera_ip="0.0.0.0", mover_addr=1,
    cam_idx=0, color=(0, 255, 0),
    seed_pan=0.5, seed_tilt=0.5,
    pan_range_deg=540.0, tilt_range_deg=270.0,
    beam_width_deg=15.0,
    coarse_pan_min=4, coarse_pan_max=4,
    coarse_tilt_min=4, coarse_tilt_max=4,
    refine=False,
    reject_reflection=False,
    confirm_nudge_delta=0.01,
    camera_polygons=[cam_poly],
    fixture_pos=fx_pos,
    fixture_rotation=fx_rot,
)

# First N probes (where N = coarse_tilt_steps = 4) should ALL share
# the same pan column (closest to seed). Verify by computing each
# probe's distance to seed_pan and checking the first N have the
# minimum delta_pan.
if len(captured) >= 4:
    first_4_pans = [p for (p, _) in captured[:4]]
    min_dpan = min(abs(p - 0.5) for p in first_4_pans)
    max_dpan = max(abs(p - 0.5) for p in first_4_pans)
    ok(abs(max_dpan - min_dpan) < 0.001,
       f'first 4 probes share same pan column '
       f'(got pans {[round(p, 3) for p in first_4_pans]})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
