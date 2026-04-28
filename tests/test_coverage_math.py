#!/usr/bin/env python3
"""test_coverage_math.py — #720 PR-1.5 unit tests.

Covers the canonical IK and the 2-pair affine estimate. Forward IK and
inverse IK MUST be exact inverses (within float tolerance) on synthetic
fixtures; angles_to_dmx and dmx_to_angles MUST be exact inverses on
synthetic models. solve_dmx_per_degree MUST recover the expected
panDmxPerDeg/tiltDmxPerDeg from a synthetic Home + Secondary pair.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                'desktop', 'shared'))

from coverage_math import (  # noqa: E402
    world_to_fixture_pt,
    fixture_aim_to_world,
    solve_dmx_per_degree,
    angles_to_dmx,
    dmx_to_angles,
    coverage_polygon,
    _profile_envelope_deg,
    working_area,
    sample_grid,
    _polygon_signed_area,
    _point_in_polygon,
    _min_distance_to_polygon_edge,
    _sutherland_hodgman,
)

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


def run():
    # ── 1. IK at identity rotation ─────────────────────────────────
    fix = (0.0, 0.0, 2000.0)  # 2 m above origin

    # Target straight forward (stage +Y) → (panDeg=0, tiltDeg=0)
    p, t = world_to_fixture_pt((0, 5000, 2000), fix, [0, 0, 0])
    ok('id-rotation: forward → (0, 0)',
       approx(p, 0, 1e-6) and approx(t, 0, 1e-6),
       f'p={p} t={t}')

    # Target +X (stage-left) → (panDeg=+90, tiltDeg=0)
    p, t = world_to_fixture_pt((5000, 0, 2000), fix, [0, 0, 0])
    ok('id-rotation: stage+X → (+90, 0)',
       approx(p, 90, 1e-3) and approx(t, 0, 1e-3),
       f'p={p} t={t}')

    # Target straight down → (panDeg=0, tiltDeg=-90)
    p, t = world_to_fixture_pt((0, 0, 0), fix, [0, 0, 0])
    ok('id-rotation: -Z → (0, -90)',
       approx(p, 0, 1e-3) and approx(t, -90, 1e-3),
       f'p={p} t={t}')

    # ── 2. IK round-trip (world → angles → world) ─────────────────
    cases = [
        # (rotation, target_xyz)
        ([0, 0, 0],   (1500, 3000, 0)),
        ([90, 0, 0],  (500, 1200, 0)),       # downward mount
        ([45, 0, 30], (-1000, 2000, 500)),   # tilted + yawed
        ([0, 0, 90],  (1500, 1500, 0)),
        ([90, 0, 90], (-200, 400, 0)),
    ]
    for rot, tgt in cases:
        ang = world_to_fixture_pt(tgt, fix, rot)
        ok(f'IK valid for rot={rot} tgt={tgt}', ang is not None)
        if ang is None:
            continue
        axis, _floor = fixture_aim_to_world(ang[0], ang[1], fix, rot)
        # axis_unit should point from fix toward tgt.
        dx = tgt[0] - fix[0]
        dy = tgt[1] - fix[1]
        dz = tgt[2] - fix[2]
        norm = math.sqrt(dx*dx + dy*dy + dz*dz)
        u = (dx/norm, dy/norm, dz/norm)
        ok(f'IK round-trip exact rot={rot}',
           approx(axis[0], u[0], 1e-9)
           and approx(axis[1], u[1], 1e-9)
           and approx(axis[2], u[2], 1e-9),
           f'axis={axis} expected={u}')

    # ── 3. fixture_aim_to_world floor intersection ─────────────────
    # Down-aimed mount, tilt -90 in world → straight down → hits floor
    # directly under fixture.
    fix2 = (1000.0, 2000.0, 3000.0)
    axis, floor = fixture_aim_to_world(0, -90, fix2, [0, 0, 0], floor_z=0)
    ok('floor intersection direct under fixture',
       floor is not None
       and approx(floor[0], 1000, 1e-3)
       and approx(floor[1], 2000, 1e-3)
       and approx(floor[2], 0, 1e-3),
       f'floor={floor}')

    # Beam pointed up — no floor intersection (tilt +30 → still has
    # negative z component? Let's check: aim_mount = (0, cos30, sin30) =
    # (0, 0.866, 0.5) — z>0 means UP, no floor hit forward.)
    axis, floor = fixture_aim_to_world(0, +30, fix2, [0, 0, 0], floor_z=0)
    ok('upward beam → no floor intersection', floor is None,
       f'floor={floor}')

    # ── 4. angles_to_dmx / dmx_to_angles inverses ─────────────────
    model = {
        'panDmxPerDeg': 121.36,
        'tiltDmxPerDeg': 200.0,
        'homePanDmx16': 32768,
        'homeTiltDmx16': 16384,
    }
    for p_deg, t_deg in [(0, 0), (10, -20), (-45, 5), (1.5, -3.7)]:
        pdx, tdx = angles_to_dmx(p_deg, t_deg, model)
        rp, rt = dmx_to_angles(pdx, tdx, model)
        # Round-trip is exact within DMX rounding (1 tick = 1/perDeg deg).
        # Tolerance 0.01° / smallest perDeg is plenty.
        ok(f'angles_to_dmx round-trip ({p_deg}, {t_deg})',
           abs(rp - p_deg) < 0.01 and abs(rt - t_deg) < 0.01,
           f'rp={rp} rt={rt}')

    # angles_to_dmx at home (0,0) returns home DMX exactly
    pdx, tdx = angles_to_dmx(0, 0, model)
    ok('angles_to_dmx(0,0) = home DMX',
       pdx == 32768 and tdx == 16384,
       f'pdx={pdx} tdx={tdx}')

    # angles_to_dmx clamps to [0, 65535]
    pdx, tdx = angles_to_dmx(1000, 1000, model)  # huge angles
    ok('angles_to_dmx clamps high', pdx == 65535 and tdx == 65535,
       f'pdx={pdx} tdx={tdx}')
    pdx, tdx = angles_to_dmx(-1000, -1000, model)
    ok('angles_to_dmx clamps low', pdx == 0 and tdx == 0,
       f'pdx={pdx} tdx={tdx}')

    # ── 5. #730 solve_dmx_per_degree — direction-only inputs ──────
    pan_range = 540.0
    tilt_range = 270.0
    home = {'panDmx16': 32768, 'tiltDmx16': 16384}
    # Direction-only secondary: operator answered "right" + "up".
    sec_ru = {
        'panOffsetDmx16': 16384,
        'tiltOffsetDmx16': 16384,
        'panMovedDirection': 'right',
        'tiltMovedDirection': 'up',
    }
    est = solve_dmx_per_degree(home, sec_ru, [0, 0, 0], pan_range, tilt_range)
    expected_pan_mag = 65535.0 / pan_range
    expected_tilt_mag = 65535.0 / tilt_range
    ok('#730 solve right/up: pan slope = +65535/panRange',
       approx(est['panDmxPerDeg'], +expected_pan_mag, 1e-6),
       f"got={est['panDmxPerDeg']}")
    ok('#730 solve right/up: tilt slope = +65535/tiltRange',
       approx(est['tiltDmxPerDeg'], +expected_tilt_mag, 1e-6),
       f"got={est['tiltDmxPerDeg']}")
    ok('#730 solve homePanDmx16 = home pan',
       est['homePanDmx16'] == 32768)
    ok('#730 solve homeTiltDmx16 = home tilt',
       est['homeTiltDmx16'] == 16384)

    # Operator answered "left" + "down" → both signs flip.
    sec_ld = {
        'panOffsetDmx16': -16384,
        'tiltOffsetDmx16': -16384,
        'panMovedDirection': 'left',
        'tiltMovedDirection': 'down',
    }
    est_ld = solve_dmx_per_degree(home, sec_ld, [0, 0, 0], pan_range, tilt_range)
    ok('#730 solve left/down: pan slope = -65535/panRange',
       approx(est_ld['panDmxPerDeg'], -expected_pan_mag, 1e-6))
    ok('#730 solve left/down: tilt slope = -65535/tiltRange',
       approx(est_ld['tiltDmxPerDeg'], -expected_tilt_mag, 1e-6))

    # Vertical-home regression: rotation aiming straight down (the case
    # that broke the operatorTiltDeg solver pre-#730). Direction-only
    # inputs yield a finite, sensible model.
    est_v = solve_dmx_per_degree(home, sec_ru, [90, 0, 0], pan_range, tilt_range)
    ok('#730 vertical home: pan slope finite',
       math.isfinite(est_v['panDmxPerDeg']) and est_v['panDmxPerDeg'] != 0,
       f"got={est_v['panDmxPerDeg']}")
    ok('#730 vertical home: tilt slope finite',
       math.isfinite(est_v['tiltDmxPerDeg']) and est_v['tiltDmxPerDeg'] != 0,
       f"got={est_v['tiltDmxPerDeg']}")
    ok('#730 vertical home: homeTiltDegStage = -90',
       approx(est_v['homeTiltDegStage'], -90.0, 1e-3),
       f"got={est_v['homeTiltDegStage']}")

    # 6. Asymmetric profile (350W BeamLight) honours its own tiltRange.
    est_350 = solve_dmx_per_degree(home, sec_ru, [0, 0, 0], 540.0, 540.0)
    ok('#730 solve uses provided tiltRange (350W envelope)',
       approx(est_350['tiltDmxPerDeg'], 65535.0 / 540.0, 1e-6),
       f"got={est_350['tiltDmxPerDeg']}")

    # 7. Legacy PR-1 shape (operatorTiltDeg only) → stale-format error.
    legacy = {'panDmx16': 49152, 'tiltDmx16': 32768, 'operatorTiltDeg': 5.0}
    try:
        solve_dmx_per_degree(home, legacy, [0, 0, 0], pan_range, tilt_range)
        ok('#730 solve rejects legacy format', False, 'no raise')
    except ValueError as e:
        ok('#730 solve rejects legacy format with stale token',
           'home_secondary_stale_format' in str(e),
           f'got {e}')

    # 8. Bad direction string → ValueError.
    try:
        solve_dmx_per_degree(home, {**sec_ru, 'panMovedDirection': 'sideways'},
                              [0, 0, 0], pan_range, tilt_range)
        ok('#730 solve rejects bogus direction', False, 'no raise')
    except ValueError:
        ok('#730 solve rejects bogus direction', True)

    # 9. solve self-consistency: angles_to_dmx(0,0) with the estimate
    #    returns the home DMX exactly.
    pdx, tdx = angles_to_dmx(0, 0, est)
    ok('#730 estimate honours home DMX at (0,0)',
       pdx == 32768 and tdx == 16384,
       f'pdx={pdx} tdx={tdx}')

    # 10. Inverse model on dmx_to_angles raises if perDeg=0.
    try:
        dmx_to_angles(0, 0, {'panDmxPerDeg': 0, 'tiltDmxPerDeg': 1,
                             'homePanDmx16': 0, 'homeTiltDmx16': 0})
        ok('dmx_to_angles rejects zero perDeg', False, 'no raise')
    except ValueError:
        ok('dmx_to_angles rejects zero perDeg', True)

    # ── 10. coverage_polygon ──────────────────────────────────────
    # Downward-mounted fixture (rx=90) at (1500, 1500, 3000) with a
    # 540°/270° envelope projected onto floor z=0 — should produce a
    # non-empty polygon roughly centred at (1500, 1500).
    prof_default = {'panRange': 540, 'tiltRange': 270,
                    'tiltOffsetDmx16': 32768, 'tiltUp': False}
    poly = coverage_polygon((1500, 1500, 3000), [90, 0, 0],
                            prof_default, floor_z=0)
    ok('coverage_polygon: down-mounted fixture has polygon',
       len(poly) >= 3, f'len={len(poly)}')
    # #731 — non-degeneracy invariants every coverage_polygon test
    # case must satisfy: 2D extent in both axes, finite shoelace area,
    # centroid near the fixture xy. `len(poly) >= 3` alone allowed a
    # 1D-line polygon to pass.
    def _poly_invariants(name, poly, fixture_xy, *, min_extent=100.0,
                         min_area=10000.0, max_centroid_distance=None):
        if len(poly) < 3:
            ok(f'{name}: len(poly) >= 3', False, f'len={len(poly)}')
            return
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x_extent = max(xs) - min(xs)
        y_extent = max(ys) - min(ys)
        ok(f'{name}: x extent > {min_extent}mm',
           x_extent > min_extent, f'x_extent={x_extent}')
        ok(f'{name}: y extent > {min_extent}mm',
           y_extent > min_extent, f'y_extent={y_extent}')
        area = abs(_polygon_signed_area(poly))
        ok(f'{name}: shoelace area > {min_area}mm²',
           area > min_area, f'area={area}')
        cx = sum(xs) / len(poly)
        cy = sum(ys) / len(poly)
        if max_centroid_distance is not None:
            d = math.hypot(cx - fixture_xy[0], cy - fixture_xy[1])
            ok(f'{name}: centroid within {max_centroid_distance}mm of fixture',
               d < max_centroid_distance, f'd={d}')

    _poly_invariants('down-mounted', poly, (1500, 1500),
                      max_centroid_distance=2000.0)

    # Sideways-mounted fixture (rx=0) at (1500, 1500, 1500). Pre-#731
    # this returned a 1D line on y=0; now produces a real 2D footprint.
    poly = coverage_polygon((1500, 1500, 1500), [0, 0, 0],
                            prof_default, floor_z=0)
    ok('coverage_polygon: sideways mount hits floor',
       len(poly) >= 3, f'len={len(poly)}')
    _poly_invariants('sideways-mount', poly, (1500, 1500))

    # #731 fid-#17 regression: basement 150W MH Stage Right inputs that
    # collapsed the polygon to a 1D line on y=0 in v1.6.80.
    fid17_prof = {'panRange': 540, 'tiltRange': 180,
                  'tiltOffsetDmx16': 32768, 'tiltUp': False}
    poly_17 = coverage_polygon((600, 0, 1760), [0, 0, 0],
                               fid17_prof, floor_z=-4.0)
    ok('#731 fid#17 regression: polygon present',
       len(poly_17) >= 3, f'len={len(poly_17)}')
    _poly_invariants('#731 fid#17', poly_17, (600, 0))

    # Tightly upward mount: rx=-90 + tiny pan/tilt envelope — confirms
    # the helper returns [] when no edge ray crosses below horizon. (A
    # full 540° pan still wraps past horizontal to hit the floor at
    # extreme yaw, so we constrain pan too.)
    tiny_prof = {'panRange': 30, 'tiltRange': 30,
                 'tiltOffsetDmx16': 32768, 'tiltUp': False}
    poly = coverage_polygon((1500, 1500, 3000), [-90, 0, 0],
                            tiny_prof, floor_z=0)
    ok('coverage_polygon: tight upward envelope returns empty',
       poly == [], f'poly={poly[:3]}')

    # Asymmetric tilt (350W BeamLight: tiltOffsetDmx16=4681, tiltUp=True)
    asym = {'panRange': 540, 'tiltRange': 270,
            'tiltOffsetDmx16': 4681, 'tiltUp': True}
    pmin, pmax, tmin, tmax = _profile_envelope_deg(asym)
    # Most of the range is above horizon (tiltUp=True), small slice below
    ok('asymmetric envelope: tilt range still 270',
       abs((tmax - tmin) - 270.0) < 1.0, f'tmax-tmin={tmax-tmin}')
    ok('asymmetric envelope: more above horizon than below',
       tmax > abs(tmin), f'tmin={tmin} tmax={tmax}')

    # ── 11. Sutherland-Hodgman + working_area ─────────────────────
    sq = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    clip = [[500, 500], [1500, 500], [1500, 1500], [500, 1500]]
    inter = _sutherland_hodgman(sq, clip)
    ok('SH clip overlap area ≈ 250000',
       abs(abs(_polygon_signed_area(inter)) - 250000) < 1.0,
       f'area={_polygon_signed_area(inter)}')

    # Disjoint polygons → empty intersection
    far = [[5000, 5000], [6000, 5000], [6000, 6000], [5000, 6000]]
    ok('SH clip disjoint → empty', _sutherland_hodgman(sq, far) == [])

    # Subject fully inside clip → unchanged area
    inner = [[100, 100], [900, 100], [900, 900], [100, 900]]
    ok('SH clip contained → preserves area',
       abs(abs(_polygon_signed_area(_sutherland_hodgman(inner, sq))) - 640000) < 1.0)

    # working_area returns empty when intersection too tiny / empty
    ok('working_area: empty inputs → empty', working_area([], []) == [])
    ok('working_area: disjoint → empty', working_area(sq, far) == [])

    # working_area with overlap returns a non-empty polygon
    wa = working_area(sq, clip, margin_mm=50)
    ok('working_area: overlap returns polygon', len(wa) >= 3,
       f'len={len(wa)}')

    # ── 12. sample_grid ──────────────────────────────────────────
    big_square = [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]
    pts = sample_grid(big_square, n=16, min_edge_margin_mm=150)
    ok('sample_grid: 16 points in big square', len(pts) >= 12,
       f'got {len(pts)}')
    # All points must be inside the polygon and respect the margin.
    all_inside = all(_point_in_polygon(p, big_square) for p in pts)
    ok('sample_grid: all points inside polygon', all_inside)
    margin_ok = all(_min_distance_to_polygon_edge(p, big_square) >= 149.0
                    for p in pts)
    ok('sample_grid: all points respect 150mm edge margin', margin_ok,
       f'min={min(_min_distance_to_polygon_edge(p, big_square) for p in pts) if pts else "n/a"}')

    # Tiny polygon: no candidate fits the margin → empty grid
    tiny = [[0, 0], [200, 0], [200, 200], [0, 200]]
    pts = sample_grid(tiny, n=16, min_edge_margin_mm=150)
    ok('sample_grid: tiny polygon → empty (margin too tight)',
       pts == [], f'got {len(pts)}')

    # Empty input → empty
    ok('sample_grid: empty input → empty',
       sample_grid([], n=16, min_edge_margin_mm=150) == [])

    # ── Print results ─────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
