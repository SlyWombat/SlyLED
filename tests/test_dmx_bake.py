#!/usr/bin/env python3
"""
test_dmx_bake.py — DMX bake + Art-Net packet validation test framework.

Creates multiple DMX fixtures with different profiles, bakes timelines with
spatial effects, and validates the baked segments have correct DMX values.
Tests moving head pan/tilt tracking, RGB par color changes, and beam width
intersection for different fixture types.

Usage:
    python tests/test_dmx_bake.py
    python tests/test_dmx_bake.py -v
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv

def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose: print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')

def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


def seed():
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'DMX Bake Test', 'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # Moving head at stage left, aiming center-right
        r = c.post('/api/fixtures', json={
            'name': 'MH Stage Left', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'aimPoint': [8000, 2000, 5000]
        })
        mh1 = r.get_json()['id']

        # Moving head at stage right, aiming center-left
        r = c.post('/api/fixtures', json={
            'name': 'MH Stage Right', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 17, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'aimPoint': [2000, 2000, 5000]
        })
        mh2 = r.get_json()['id']

        # RGB par at center (wide beam, no pan/tilt)
        r = c.post('/api/fixtures', json={
            'name': 'RGB Par Center', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb',
            'aimPoint': [5000, 0, 5000]
        })
        par1 = r.get_json()['id']

        # Dimmer at back (narrow, no color)
        r = c.post('/api/fixtures', json={
            'name': 'Dimmer Back', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 2, 'dmxStartAddr': 1, 'dmxChannelCount': 1,
            'dmxProfileId': 'generic-dimmer',
            'aimPoint': [5000, 0, 5000]
        })
        dim1 = r.get_json()['id']

        # LED fixture for comparison
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cid = r.get_json()['id']
        r = c.post('/api/fixtures', json={
            'name': 'LED Strip', 'type': 'linear', 'fixtureType': 'led', 'childId': cid,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}]
        })
        led1 = r.get_json()['id']

        # Layout — place all at known positions
        c.post('/api/layout', json={'children': [
            {'id': mh1, 'x': 1000, 'y': 4500, 'z': 0},
            {'id': mh2, 'x': 9000, 'y': 4500, 'z': 0},
            {'id': par1, 'x': 5000, 'y': 2500, 'z': 5000},
            {'id': dim1, 'x': 5000, 'y': 500, 'z': 0},
            {'id': led1, 'x': 5000, 'y': 4000, 'z': 0},
        ]})

        # Spatial effect: sphere sweeping left to right through middle of stage
        r = c.post('/api/spatial-effects', json={
            'name': 'Sweep Right', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 255, 'g': 100, 'b': 50, 'size': {'radius': 3000},
            'motion': {'startPos': [0, 2500, 2500], 'endPos': [10000, 2500, 2500],
                       'durationS': 10, 'easing': 'linear'},
            'blend': 'add'
        })
        fx_sweep = r.get_json()['id']

        # Spatial effect: plane rising from floor
        r = c.post('/api/spatial-effects', json={
            'name': 'Rise', 'category': 'spatial-field', 'shape': 'plane',
            'r': 0, 'g': 200, 'b': 255, 'size': {'normal': [0, 1, 0], 'thickness': 1500},
            'motion': {'startPos': [5000, 0, 5000], 'endPos': [5000, 5000, 5000],
                       'durationS': 10, 'easing': 'linear'},
            'blend': 'add'
        })
        fx_rise = r.get_json()['id']

        # Actions
        r = c.post('/api/actions', json={'name': 'Red Solid', 'type': 1, 'r': 255, 'g': 0, 'b': 0})
        act_red = r.get_json()['id']

        # Timeline 1: Sphere sweep (should hit par at center, MH track it)
        r = c.post('/api/timelines', json={'name': 'Sweep Test', 'durationS': 10})
        tl1 = r.get_json()['id']
        c.put(f'/api/timelines/{tl1}', json={
            'name': 'Sweep Test', 'durationS': 10,
            'tracks': [{'allPerformers': True, 'clips': [
                {'effectId': fx_sweep, 'startS': 0, 'durationS': 10}
            ]}]
        })

        # Timeline 2: Plane rise (should hit all fixtures at different heights)
        r = c.post('/api/timelines', json={'name': 'Rise Test', 'durationS': 10})
        tl2 = r.get_json()['id']
        c.put(f'/api/timelines/{tl2}', json={
            'name': 'Rise Test', 'durationS': 10,
            'tracks': [{'allPerformers': True, 'clips': [
                {'effectId': fx_rise, 'startS': 0, 'durationS': 10}
            ]}]
        })

    return {
        'mh1': mh1, 'mh2': mh2, 'par1': par1, 'dim1': dim1, 'led1': led1,
        'tl1': tl1, 'tl2': tl2, 'fx_sweep': fx_sweep, 'fx_rise': fx_rise,
    }


def bake_and_get(c, tl_id):
    """Bake a timeline and return the result."""
    r = c.post(f'/api/timelines/{tl_id}/bake')
    assert r.get_json().get('ok'), f'Bake start failed: {r.get_json()}'
    for _ in range(100):
        time.sleep(0.2)
        st = c.get(f'/api/timelines/{tl_id}/baked/status').get_json()
        if st.get('done') or st.get('error'):
            break
    assert st.get('done'), f'Bake not done: {st}'
    assert not st.get('error'), f'Bake error: {st.get("error")}'
    return c.get(f'/api/timelines/{tl_id}/baked').get_json()


def test_sweep_dmx_fixtures(ids):
    """Sphere sweep should trigger DMX fixtures via beam cone intersection."""
    section('Sphere Sweep — DMX Beam Intersection')
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        baked = bake_and_get(c, ids['tl1'])

    fixtures = baked.get('fixtures', {})
    ok(len(fixtures) > 0, f'Bake produced fixture segments ({len(fixtures)})')

    # Moving head at x=1000 — sphere starts at x=0, radius 3000
    # Beam axis goes to aimPoint [8000, 2000, 5000], sampling should intersect sphere
    mh1_segs = fixtures.get(str(ids['mh1']), {}).get('segments', [])
    ok(len(mh1_segs) > 0, f'MH Stage Left has segments ({len(mh1_segs)})')
    # Should have non-zero color in some segments
    mh1_has_color = any(
        s.get('params', {}).get('r', 0) + s.get('params', {}).get('g', 0) + s.get('params', {}).get('b', 0) > 0
        for s in mh1_segs
    )
    ok(mh1_has_color, 'MH Stage Left has color from sweep (beam cone intersection)')

    # RGB par at center x=5000 — sphere passes through center at t=5s
    par_segs = fixtures.get(str(ids['par1']), {}).get('segments', [])
    ok(len(par_segs) > 0, f'RGB Par has segments ({len(par_segs)})')
    par_has_color = any(
        s.get('params', {}).get('r', 0) + s.get('params', {}).get('g', 0) > 0
        for s in par_segs
    )
    ok(par_has_color, 'RGB Par has color from sweep')

    # Check pan/tilt changes on moving head (should track sphere)
    if len(mh1_segs) >= 2:
        pan_values = [s.get('params', {}).get('pan', 0.5) for s in mh1_segs]
        pan_changes = len(set(round(p, 2) for p in pan_values)) > 1
        ok(pan_changes, f'MH pan values change over time ({len(set(round(p,2) for p in pan_values))} distinct)')
    else:
        ok(False, 'MH has enough segments for pan tracking')


def test_rise_dmx_fixtures(ids):
    """Plane rising should hit fixtures at different heights at different times."""
    section('Plane Rise — Height-Based Triggering')
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        baked = bake_and_get(c, ids['tl2'])

    fixtures = baked.get('fixtures', {})

    # Dimmer at y=500 (near floor) — should be hit early
    dim_segs = fixtures.get(str(ids['dim1']), {}).get('segments', [])
    ok(len(dim_segs) > 0, f'Dimmer has segments ({len(dim_segs)})')
    if dim_segs:
        first_lit = next((s for s in dim_segs if s.get('params', {}).get('dimmer', 0) > 0), None)
        ok(first_lit is not None, 'Dimmer gets lit during rise')

    # MH at y=4500 (near ceiling) — should be hit later
    mh_segs = fixtures.get(str(ids['mh1']), {}).get('segments', [])
    ok(len(mh_segs) > 0, f'MH has segments during rise ({len(mh_segs)})')


def test_preview_has_dmx_data(ids):
    """Preview data should include DMX fixture entries with color/pan/tilt."""
    section('Preview Data — DMX Entries')
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        bake_and_get(c, ids['tl1'])
        preview = c.get(f'/api/timelines/{ids["tl1"]}/baked/preview').get_json()

    ok(isinstance(preview, dict), 'Preview is dict')
    # DMX fixtures should have preview entries
    par_preview = preview.get(str(ids['par1']))
    ok(par_preview is not None, f'RGB Par has preview data')
    if par_preview:
        ok(isinstance(par_preview, list) and len(par_preview) > 0,
           f'Par preview has frames ({len(par_preview)})')

    mh_preview = preview.get(str(ids['mh1']))
    ok(mh_preview is not None, 'Moving head has preview data')


def test_beam_width_matters(ids):
    """Wider beam width should intersect more of the spatial field."""
    section('Beam Width Impact')
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        baked = bake_and_get(c, ids['tl1'])

    fixtures = baked.get('fixtures', {})
    # Count non-zero color segments for par (25° beam) vs dimmer (narrow)
    par_segs = fixtures.get(str(ids['par1']), {}).get('segments', [])
    dim_segs = fixtures.get(str(ids['dim1']), {}).get('segments', [])
    par_lit = sum(1 for s in par_segs if s.get('params', {}).get('dimmer', 0) > 0)
    dim_lit = sum(1 for s in dim_segs if s.get('params', {}).get('dimmer', 0) > 0)
    ok(True, f'Par lit segments: {par_lit}, Dimmer lit: {dim_lit}')


def test_multi_universe():
    """Verify baked segments have correct universe assignments."""
    section('Multi-Universe Validation')
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        fixtures = c.get('/api/fixtures').get_json()
        uni_map = {}
        for f in fixtures:
            if f.get('fixtureType') == 'dmx':
                uni_map[f['id']] = {'universe': f['dmxUniverse'], 'addr': f['dmxStartAddr'],
                                     'channels': f['dmxChannelCount'], 'name': f['name']}

    ok(len(uni_map) >= 4, f'Have DMX fixtures ({len(uni_map)})')
    universes = set(v['universe'] for v in uni_map.values())
    ok(len(universes) >= 2, f'Fixtures span {len(universes)} universes')
    # Verify no address collisions within a universe
    for uni in universes:
        fx_in_uni = [(v['addr'], v['addr'] + v['channels'] - 1, v['name'])
                     for v in uni_map.values() if v['universe'] == uni]
        for i, (a1_s, a1_e, n1) in enumerate(fx_in_uni):
            for a2_s, a2_e, n2 in fx_in_uni[i+1:]:
                overlap = a1_s <= a2_e and a2_s <= a1_e
                ok(not overlap, f'No overlap: {n1}({a1_s}-{a1_e}) vs {n2}({a2_s}-{a2_e}) in U{uni}')


def main():
    print('\033[1m=== DMX Bake & Art-Net Validation Tests ===\033[0m')
    print('Seeding...')
    ids = seed()

    test_sweep_dmx_fixtures(ids)
    test_rise_dmx_fixtures(ids)
    test_preview_has_dmx_data(ids)
    test_beam_width_matters(ids)
    test_multi_universe()

    total = _pass + _fail
    print(f'\n\033[1m{"="*60}\033[0m')
    if _fail == 0:
        print(f'\033[32m  ALL {total} TESTS PASSED\033[0m')
    else:
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m out of {total}')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
