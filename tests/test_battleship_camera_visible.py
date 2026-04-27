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

# #703 — silent (0.0, 0.5)/(0.5, 1.0) fallback removed. Bad inputs now
# raise CalibrationError so the cal-kickoff endpoint surfaces the cause.
# (#704 P0 #2 — the operator-relative IK now ignores pan_norm for the
# tilt-band sweep, since pan is held at home throughout. The previous
# "pan-away" assertion no longer applies; instead, force a no-hit case
# by giving a polygon that's off the floor entirely.)

# Polygon far from any tilt's floor projection → CalibrationError.
unreachable_poly = [(99000, 99000), (99100, 99000),
                    (99100, 99100), (99000, 99100)]
try:
    mc._camera_visible_tilt_band(
        fx_pos, fx_rot, home_pan_norm=0.5,
        pan_range_deg=540, tilt_range_deg=270,
        mounted_inverted=False,
        camera_polygons=[unreachable_poly])
    ok(False, 'unreachable polygon should raise CalibrationError')
except mc.CalibrationError as e:
    ok('camera FOV polygon' in str(e),
       f'unreachable polygon raises with descriptive message (got {e})')

# No polygons → CalibrationError naming the missing input.
try:
    mc._camera_visible_tilt_band(fx_pos, fx_rot, 0.5, 540, 270, False, None)
    ok(False, 'no-polygons should raise CalibrationError')
except mc.CalibrationError as e:
    ok('no camera floor polygons' in str(e),
       f'no polys raises with descriptive message (got {e})')

# Missing pan/tilt range → CalibrationError naming the missing field.
try:
    mc._camera_visible_tilt_band(fx_pos, fx_rot, 0.5, None, 270, False,
                                  [cam_poly])
    ok(False, 'missing panRange should raise CalibrationError')
except mc.CalibrationError as e:
    ok('panRange' in str(e),
       f'missing panRange raises with descriptive message (got {e})')

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

# ── #711 — first probes land on-stage / in-FOV ─────────────────────────

section('#711 first probes land in camera FOV (predictive ordering)')

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

# Pre-#711: first probes were ordered by |pan - seed_pan| /
# |tilt - seed_tilt|, so the home-pan column came first regardless of
# whether those cells hit the floor inside the camera FOV.
# Post-#711: first probes are sorted by (in-FOV bucket, FOV centroid
# distance, |pan - seed_pan|). For a floor-mount fixture at seed_tilt
# = 0.5 (mech 0° horizontal), all cells with tilt < 0.5 aim UP and
# never hit the floor; all cells with tilt > 0.5 aim DOWN. The first
# 3 probes must be cells whose floor projection lands inside the cam
# polygon.
if len(captured) >= 3:
    first_3_in_fov = []
    for pan_n, tilt_n in captured[:3]:
        hit = mc._ray_floor_hit(fx_pos, fx_rot, pan_n, tilt_n,
                                 540, 270,
                                 mounted_inverted=False,
                                 home_pan_norm=0.5)
        in_fov = hit is not None and mc._point_in_polygon(hit, cam_poly)
        first_3_in_fov.append((pan_n, tilt_n, in_fov))
    all_in = all(p[2] for p in first_3_in_fov)
    ok(all_in,
       f'#711: first 3 probes all land in camera FOV '
       f'(got {[(round(p[0], 3), round(p[1], 3), p[2]) for p in first_3_in_fov]})')

# ── #710 — pan_norm rotates the beam azimuth ──────────────────────────

section('#710 _ray_floor_hit honours pan-from-home rotation')

# Without home_pan_norm — legacy behaviour, dx contribution is 0 so
# every probe at a given tilt projects to the same floor XY at home.
fx17 = (600, 0, 1760)
hits_legacy = [
    mc._ray_floor_hit(fx17, [0,0,0], p, 0.2517, 540, 180,
                       mounted_inverted=True)
    for p in (0.375, 0.625, 0.875)
]
xs_legacy = sorted(set(round(h[0]) for h in hits_legacy if h))
ok(xs_legacy == [600],
   f'legacy call (home_pan_norm=None) collapses pan to home (got {xs_legacy})')

# With home_pan_norm — different pan columns produce different floor XY.
hits_new = [
    mc._ray_floor_hit(fx17, [0,0,0], p, 0.2517, 540, 180,
                       mounted_inverted=True, home_pan_norm=0.6770)
    for p in (0.375, 0.625, 0.875)
]
xs_new = sorted(set(round(h[0]) for h in hits_new if h))
ok(len(xs_new) == 3,
   f'new call produces 3 distinct floor X values (got {xs_new})')

# Issue acceptance: pan=0.625, tilt=0.252, home=0.677 → X ≈ +1419.
h = mc._ray_floor_hit((600,0,1760), [0,0,0], 0.625, 0.252, 540, 180,
                       mounted_inverted=True, home_pan_norm=0.677)
ok(h is not None and abs(h[0] - 1419) < 5,
   f'#710 acceptance: pan=0.625 tilt=0.252 home=0.677 → X≈+1419 '
   f'(got X={h[0]:.0f})')

# Tilt-band sweep at home_pan unchanged (no regression on #698 + #706
# code paths that pass pan_norm == home_pan_norm).
for t in (0.05, 0.20, 0.50, 0.70):
    legacy = mc._ray_floor_hit(fx17, [0,0,0], 0.6770, t, 540, 180,
                                 mounted_inverted=True)
    new = mc._ray_floor_hit(fx17, [0,0,0], 0.6770, t, 540, 180,
                             mounted_inverted=True, home_pan_norm=0.6770)
    ok(legacy == new,
       f'#710 no-regression at home pan tilt={t}: legacy={legacy} == new={new}')


# ── #704 P0 #2 acceptance — operator-relative IK on basement rig #17 ───

section('#704 inverted-mount IK matches probe_coverage_3d.py:floor_hit')

# Basement rig fixture #17: pos=(600, 0, 1760), rotation=[0,0,0],
# mountedInverted=True, panRange=540, tiltRange=180. Live-rig
# operator confirmed beam at (490, 1850) for probe (0.6770, 0.2135).
fx17 = (600, 0, 1760)

# Reference table from the issue body — matches tools/probe_coverage_3d.py
for tn, ref_y in [(0.05, 11112), (0.20, 2422), (0.50, 0), (0.70, -1279)]:
    h = mc._ray_floor_hit(fx17, [0, 0, 0], 0.6770, tn, 540, 180,
                           mounted_inverted=True)
    ok(h is not None, f'inverted #17 tilt={tn}: produces floor hit')
    ok(abs(h[0] - 600) < 1,
       f'inverted #17 tilt={tn}: X stays at fixture X (got {h[0]:.1f})')
    ok(abs(h[1] - ref_y) < 5,
       f'inverted #17 tilt={tn}: Y matches reference {ref_y} '
       f'(got {h[1]:.1f})')

# Acceptance test from the issue: tilt=0.20 → audience-side, X near 600.
h = mc._ray_floor_hit(fx_pos=(600, 0, 1760), fx_rot=[0, 0, 0],
                       pan_norm=0.6770, tilt_norm=0.20,
                       pan_range_deg=540, tilt_range_deg=180,
                       mounted_inverted=True)
ok(h is not None, '#704 acceptance: hit not None')
ok(h[1] > 1000, f'#704 acceptance: Y > 1000 (got {h[1]:.0f})')
ok(abs(h[0] - 600) < 100, f'#704 acceptance: X near 600 (got {h[0]:.0f})')

# Yaw rotation routes the home aim into the rotated frame. Standard
# Three.js Euler R_z(+90°) maps mount +Y → stage -X (matches
# remote_math.euler_xyz_deg_to_matrix). So a fixture mounted with
# rz=+90 has its home aim along -X.
h_yaw = mc._ray_floor_hit((600, 1000, 1760), [0, 0, 90], 0.5, 0.20,
                           540, 180, mounted_inverted=True)
ok(h_yaw is not None, 'rz=90: hit not None')
ok(h_yaw[0] < -1000,
   f'rz=90 inverted: home aim rotates +Y -> -X (got X={h_yaw[0]:.0f})')
ok(abs(h_yaw[1] - 1000) < 10,
   f'rz=90 inverted: Y stays at fixture Y (got Y={h_yaw[1]:.0f})')

# ── #711 acceptance — basement-rig fid #17 first 3 probes on-stage + in-FOV

section('#711 basement-rig: first 3 probes on-stage AND in cam FOV')

# Basement rig fid #17: pos (600, 0, 1760), inverted, panRange=540°,
# tiltRange=180°, home_pan=0.6770, home_tilt=0.0. The pre-#711 sort
# put 2 of the first 3 probes off-stage (issue body shows
# (-1004, 5278) and (-237, 2748)).
fx17_pos = (600, 0, 1760)
fx17_rot = [0, 0, 0]
# Camera FOV polygons typical for the basement rig (cam #12 + cam #13
# project floor coverage that overlaps the audience side).
cam12_poly = [(-200, 1500), (3500, 1500), (3500, 4200), (-200, 4200)]
cam13_poly = [(2500, 1500), (4000, 1500), (4000, 4200), (2500, 4200)]
basement_polys = [cam12_poly, cam13_poly]

captured17 = []
def _capture17(bip, cip, ci, ma, p, t, c, dx, threshold=30):
    captured17.append((float(p), float(t)))
    return None
saved_flash = mc._beam_detect_flash
mc._beam_detect_flash = _capture17
try:
    mc.battleship_discover(
        bridge_ip="0.0.0.0", camera_ip="0.0.0.0", mover_addr=1,
        cam_idx=0, color=(0, 255, 0),
        seed_pan=0.6770, seed_tilt=0.0,
        pan_range_deg=540.0, tilt_range_deg=180.0,
        beam_width_deg=3.0,
        coarse_pan_min=8, coarse_pan_max=8,
        coarse_tilt_min=3, coarse_tilt_max=3,
        refine=False,
        reject_reflection=False,
        confirm_nudge_delta=0.01,
        camera_polygons=basement_polys,
        fixture_pos=fx17_pos,
        fixture_rotation=fx17_rot,
        mounted_inverted=True,
    )
finally:
    mc._beam_detect_flash = saved_flash

if len(captured17) >= 3:
    first_3 = []
    for pan_n, tilt_n in captured17[:3]:
        hit = mc._ray_floor_hit(fx17_pos, fx17_rot, pan_n, tilt_n,
                                 540, 180,
                                 mounted_inverted=True,
                                 home_pan_norm=0.6770)
        in_any_fov = (hit is not None
                       and any(mc._point_in_polygon(hit, p) for p in basement_polys))
        first_3.append((pan_n, tilt_n, hit, in_any_fov))
    all_in = all(p[3] for p in first_3)
    ok(all_in,
       f'#711 basement: first 3 probes ALL in some camera FOV. '
       f'Got: {[(round(p[0], 3), round(p[1], 3), bool(p[3])) for p in first_3]}')


# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
