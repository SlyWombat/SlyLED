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

    # ── 5. solve_dmx_per_degree recovers a synthetic 2-pair fit ───
    pan_range = 540.0
    # Synthetic: home at (32768, 16384), rotation [0,0,0] → home_tilt=0.
    # Choose secondary pan offset = +0.25 * 65535 = 16384 → +0.25 *
    # 540 = +135° pan delta. Choose tilt DMX +1000 with operator
    # reading +5° (so tilt_dmx_per_deg = 1000 / 5 = 200).
    home = {'panDmx16': 32768, 'tiltDmx16': 16384}
    sec = {
        'panDmx16': 32768 + 16384,
        'tiltDmx16': 16384 + 1000,
        'operatorTiltDeg': 5.0,
    }
    est = solve_dmx_per_degree(home, sec, [0, 0, 0], pan_range)
    # Synthetic: full DMX maps to full panRange, so pan_dmx_per_deg ≡
    # 65535 / panRange regardless of the chosen offset fraction.
    expected_pan_per = 65535.0 / pan_range
    ok('solve pan-DMX-per-deg',
       approx(est['panDmxPerDeg'], expected_pan_per, 1e-6),
       f"got={est['panDmxPerDeg']} expected={expected_pan_per}")
    ok('solve tilt-DMX-per-deg',
       approx(est['tiltDmxPerDeg'], 200.0, 1e-3),
       f"got={est['tiltDmxPerDeg']}")
    ok('solve home_tilt_deg at id-rotation = 0',
       approx(est['homeTiltDegStage'], 0.0, 1e-6),
       f"got={est['homeTiltDegStage']}")

    # 6. solve with downward mount: home_tilt_deg should be -90°
    est = solve_dmx_per_degree(home, sec, [90, 0, 0], pan_range)
    ok('solve home_tilt at rx=90 = -90',
       approx(est['homeTiltDegStage'], -90.0, 1e-3),
       f"got={est['homeTiltDegStage']}")

    # 7. solve raises on degenerate inputs
    try:
        solve_dmx_per_degree(home, {'panDmx16': 32768, 'tiltDmx16': 16384,
                                     'operatorTiltDeg': 0.0},
                              [0, 0, 0], pan_range)
        ok('solve rejects identical home/secondary', False, 'no raise')
    except ValueError:
        ok('solve rejects identical home/secondary', True)

    # 8. solve uses 2-pair to feed angles_to_dmx self-consistently:
    # at panDeg=0, tiltDeg=0 (Home) → home DMX exactly.
    est = solve_dmx_per_degree(home, sec, [0, 0, 0], pan_range)
    pdx, tdx = angles_to_dmx(0, 0, est)
    ok('estimate honours home DMX at (0,0)',
       pdx == 32768 and tdx == 16384,
       f'pdx={pdx} tdx={tdx}')

    # 9. Inverse model on dmx_to_angles raises if perDeg=0
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
    # Centroid roughly near fixture XY
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    ok('coverage_polygon: centroid near fixture XY',
       abs(cx - 1500) < 1500 and abs(cy - 1500) < 1500,
       f'centroid=({cx}, {cy})')

    # Sideways-mounted fixture (rx=0) at (1500, 1500, 1500) — beam
    # aims horizontally at home, but the envelope (±135° tilt) sweeps
    # downward enough to hit the floor.
    poly = coverage_polygon((1500, 1500, 1500), [0, 0, 0],
                            prof_default, floor_z=0)
    ok('coverage_polygon: sideways mount hits floor',
       len(poly) >= 3, f'len={len(poly)}')

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
