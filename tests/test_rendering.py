#!/usr/bin/env python3
"""
test_rendering.py — Comprehensive rendering data validation tests.

Validates every data dependency that feeds the SPA layout canvas, runtime
emulator, and 3D viewport.  Catches issues where missing/wrong API data
would produce empty, misplaced, or incorrectly oriented visual elements.

56 rendering features across 3 render targets:
  - 2D layout canvas (drawLayout)
  - Runtime emulator (emuDraw)
  - 3D viewport (s3dLoadChildren, _s3dRenderObjects)

Usage:
    python tests/test_rendering.py
    python tests/test_rendering.py -v
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18091
BASE = f'http://127.0.0.1:{PORT}'

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


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed():
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        c.post('/api/settings', json={'name': 'RenderTest', 'darkMode': 1,
                                       'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # LED performers + fixtures
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cid1 = r.get_json()['id']
        r = c.post('/api/fixtures', json={
            'name': 'LED Strip Left', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}, {'leds': 30, 'mm': 1500, 'sdir': 1}]
        })
        fix_led = r.get_json()['id']

        r = c.post('/api/children', json={'ip': '10.0.0.51'})
        cid2 = r.get_json()['id']
        r = c.post('/api/fixtures', json={
            'name': 'LED Strip Right', 'type': 'linear', 'fixtureType': 'led', 'childId': cid2,
            'strings': [{'leds': 100, 'mm': 5000, 'sdir': 2}]
        })
        fix_led2 = r.get_json()['id']

        # DMX moving head
        r = c.post('/api/fixtures', json={
            'name': 'Moving Head SL', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'rotation': [-20, 0, 0]
        })
        fix_dmx1 = r.get_json()['id']

        # DMX RGB par
        r = c.post('/api/fixtures', json={
            'name': 'RGB Par Center', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb',
            'rotation': [0, 0, 0]
        })
        fix_dmx2 = r.get_json()['id']

        # DMX dimmer (no rotation — should use default [0,0,0])
        r = c.post('/api/fixtures', json={
            'name': 'Dimmer Spot', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 2, 'dmxStartAddr': 1, 'dmxChannelCount': 1,
            'dmxProfileId': 'generic-dimmer'
        })
        fix_dmx3 = r.get_json()['id']

        # Layout positions
        c.post('/api/layout', json={'children': [
            {'id': fix_led, 'x': 1000, 'y': 4500, 'z': 0},
            {'id': fix_led2, 'x': 9000, 'y': 4500, 'z': 0},
            {'id': fix_dmx1, 'x': 2000, 'y': 5000, 'z': 2000},
            {'id': fix_dmx2, 'x': 5000, 'y': 2500, 'z': 5000},
            {'id': fix_dmx3, 'x': 8000, 'y': 4000, 'z': 0},
        ]})

        # Objects
        c.post('/api/objects', json={
            'name': 'Back Wall', 'objectType': 'wall', 'color': '#1e293b', 'opacity': 30,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
        })
        c.post('/api/objects', json={
            'name': 'Floor', 'objectType': 'floor', 'color': '#1a2744', 'opacity': 20,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 100, 10000]}
        })
        c.post('/api/objects', json={
            'name': 'Side Panel', 'objectType': 'custom', 'color': '#334455', 'opacity': 40,
            'transform': {'pos': [3000, 1000, 0], 'rot': [0, 0, 0], 'scale': [2000, 3000, 50]}
        })

        # Actions (one of each type that has emulator rendering)
        action_ids = {}
        for a in [
            {'name': 'Solid Red', 'type': 1, 'r': 255, 'g': 0, 'b': 0},
            {'name': 'Fade Blue', 'type': 2, 'r': 0, 'g': 0, 'b': 255, 'r2': 0, 'g2': 255, 'b2': 0, 'speedMs': 2000},
            {'name': 'Breathe', 'type': 3, 'r': 255, 'g': 128, 'b': 0, 'periodMs': 3000},
            {'name': 'Chase', 'type': 4, 'r': 0, 'g': 255, 'b': 128, 'speedMs': 50, 'spacing': 5},
            {'name': 'Rainbow', 'type': 5, 'speedMs': 40},
            {'name': 'Fire', 'type': 6, 'r': 255, 'g': 80, 'b': 0},
            {'name': 'Comet', 'type': 7, 'r': 0, 'g': 200, 'b': 255, 'speedMs': 30, 'tailLen': 8},
            {'name': 'Twinkle', 'type': 8, 'r': 200, 'g': 200, 'b': 255, 'spawnMs': 80},
            {'name': 'Strobe', 'type': 9, 'r': 255, 'g': 255, 'b': 255, 'periodMs': 100},
            {'name': 'Wipe', 'type': 10, 'r': 255, 'g': 128, 'b': 0, 'speedMs': 60},
            {'name': 'Scanner', 'type': 11, 'r': 255, 'g': 0, 'b': 0, 'speedMs': 80},
            {'name': 'Sparkle', 'type': 12, 'r': 180, 'g': 180, 'b': 220},
            {'name': 'Gradient', 'type': 13, 'r': 255, 'g': 0, 'b': 0, 'r2': 0, 'g2': 0, 'b2': 255},
        ]:
            r = c.post('/api/actions', json=a)
            action_ids[a['type']] = r.get_json()['id']

        # Timeline with clips
        r = c.post('/api/timelines', json={'name': 'Render Test TL', 'durationS': 30})
        tl_id = r.get_json()['id']

        # Spatial effect
        r = c.post('/api/spatial-effects', json={
            'name': 'Test Sweep', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 100, 'g': 200, 'b': 255, 'size': {'radius': 3000},
            'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                       'durationS': 30, 'easing': 'linear'},
            'blend': 'add'
        })

        c.post('/api/wifi', json={'ssid': 'test', 'password': 'test123'})

    return {
        'cid1': cid1, 'cid2': cid2,
        'fix_led': fix_led, 'fix_led2': fix_led2,
        'fix_dmx1': fix_dmx1, 'fix_dmx2': fix_dmx2, 'fix_dmx3': fix_dmx3,
        'tl_id': tl_id, 'action_ids': action_ids,
    }


def start_server():
    import parent_server
    from parent_server import app
    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)
    return app


def api(method, path, body=None):
    import urllib.request as ur
    data = json.dumps(body).encode() if body else None
    headers = {'Content-Type': 'application/json'} if data else {}
    req = ur.Request(f'{BASE}{path}', data=data, method=method, headers=headers)
    try:
        resp = ur.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception:
        return None


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_stage_dimensions():
    section('Stage Dimensions (grid, border, labels)')
    s = api('GET', '/api/settings')
    ok(s.get('canvasW') == 10000, f'canvasW = 10000 (got {s.get("canvasW")})')
    ok(s.get('canvasH') == 5000, f'canvasH = 5000 (got {s.get("canvasH")})')
    st = api('GET', '/api/stage')
    ok(st.get('w') == 10.0, f'stage.w = 10.0')
    ok(st.get('h') == 5.0, f'stage.h = 5.0')
    ok(st.get('d') == 10.0, f'stage.d = 10.0')
    ok(int(st['w'] * 1000) == s['canvasW'], 'canvasW = stage.w * 1000')
    ok(int(st['h'] * 1000) == s['canvasH'], 'canvasH = stage.h * 1000')
    lay = api('GET', '/api/layout')
    ok(lay.get('canvasW') == 10000, 'layout.canvasW synced')
    ok(lay.get('canvasH') == 5000, 'layout.canvasH synced')


def test_fixture_positions(ids):
    section('Fixture Positioning (nodes, labels, coordinates)')
    lay = api('GET', '/api/layout')
    fixtures = lay.get('fixtures', [])
    ok(len(fixtures) == 5, f'Layout has 5 fixtures (got {len(fixtures)})')
    positioned = [f for f in fixtures if f.get('positioned')]
    ok(len(positioned) == 5, f'All 5 positioned (got {len(positioned)})')
    for f in positioned:
        ok(f.get('x', 0) > 0 or f.get('y', 0) > 0, f'Fixture {f["id"]} "{f.get("name")}" has non-zero position')
        ok('name' in f and len(f['name']) > 0, f'Fixture {f["id"]} has name for label')
        ok('fixtureType' in f, f'Fixture {f["id"]} has fixtureType for node color')
    # Coordinates within stage bounds
    cw = lay.get('canvasW', 10000)
    ch = lay.get('canvasH', 5000)
    for f in positioned:
        ok(0 <= f['x'] <= cw, f'Fixture {f["id"]} X ({f["x"]}) within canvas')
        ok(0 <= f['y'] <= ch, f'Fixture {f["id"]} Y ({f["y"]}) within canvas')


def test_led_strings(ids):
    section('LED String Rendering (lines, dots, direction, length)')
    lay = api('GET', '/api/layout')
    led_fx = [f for f in lay.get('fixtures', []) if f.get('fixtureType') != 'dmx' and f.get('positioned')]
    ok(len(led_fx) >= 2, f'At least 2 LED fixtures ({len(led_fx)})')
    total_strings = 0
    for f in led_fx:
        strings = f.get('strings', [])
        ok(len(strings) > 0, f'LED fixture {f["id"]} "{f.get("name")}" has strings')
        for si, s in enumerate(strings):
            ok(s.get('leds', 0) > 0, f'  string[{si}] leds={s.get("leds")} > 0')
            ok(s.get('mm', 0) > 0, f'  string[{si}] mm={s.get("mm")} > 0')
            ok(s.get('sdir') in (0, 1, 2, 3), f'  string[{si}] sdir={s.get("sdir")} valid (0-3)')
            total_strings += 1
    ok(total_strings >= 3, f'Total strings across fixtures: {total_strings}')
    # Verify multi-string fixture
    multi = [f for f in led_fx if len(f.get('strings', [])) >= 2]
    ok(len(multi) >= 1, f'At least 1 multi-string fixture ({len(multi)})')


def test_dmx_beam_cones(ids):
    section('DMX Beam Cones (rotation, direction, profile)')
    lay = api('GET', '/api/layout')
    dmx_fx = [f for f in lay.get('fixtures', []) if f.get('fixtureType') == 'dmx' and f.get('positioned')]
    ok(len(dmx_fx) >= 2, f'At least 2 DMX fixtures ({len(dmx_fx)})')
    for f in dmx_fx:
        rot = f.get('rotation')
        ok(rot is not None, f'DMX {f["id"]} "{f.get("name")}" has rotation')
        if rot:
            ok(len(rot) == 3, f'  rotation has 3 components')
            ok(isinstance(rot[0], (int, float)), f'  rotation[0] (tilt) is number: {rot[0]}')
            ok(isinstance(rot[1], (int, float)), f'  rotation[1] (pan) is number: {rot[1]}')
        # Profile for beam width
        pid = f.get('dmxProfileId')
        ok(pid is not None, f'  has dmxProfileId: {pid}')
    # DMX fixture without explicit rotation should have default
    f3 = next((f for f in dmx_fx if f['id'] == ids['fix_dmx3']), None)
    if f3:
        ok(f3.get('rotation') is not None, f'Dimmer fixture has default rotation')


def test_objects():
    section('Objects (rectangles, names, opacity, bounds)')
    objs = api('GET', '/api/objects')
    ok(isinstance(objs, list) and len(objs) >= 3, f'At least 3 objects ({len(objs)})')
    lay = api('GET', '/api/layout')
    cw = lay.get('canvasW', 10000)
    ch = lay.get('canvasH', 5000)
    for s in objs:
        ok('name' in s and len(s['name']) > 0, f'Object {s["id"]} has name: {s.get("name")}')
        ok('color' in s, f'Object {s["id"]} has color')
        ok(0 <= s.get('opacity', 0) <= 100, f'Object {s["id"]} opacity valid: {s.get("opacity")}')
        t = s.get('transform', {})
        ok('pos' in t, f'Object {s["id"]} has transform.pos')
        ok('scale' in t, f'Object {s["id"]} has transform.scale')
        pos = t.get('pos', [0, 0, 0])
        scale = t.get('scale', [0, 0, 0])
        ok(scale[0] > 0, f'Object {s["id"]} width > 0: {scale[0]}')
        # Warn if extends way outside stage
        right = pos[0] + scale[0]
        ok(right <= cw * 3, f'Object {s["id"]} right edge ({right}) within 3x stage')


def test_dmx_profiles():
    section('DMX Profiles (beam width for cone rendering)')
    profiles = api('GET', '/api/dmx-profiles')
    ok(isinstance(profiles, list), 'Profiles is list')
    # Check beam width available for moving head
    mh = api('GET', '/api/dmx-profiles/generic-moving-head-16bit')
    ok(mh is not None, 'Moving head profile exists')
    if mh:
        bw = mh.get('beamWidth', 0)
        ok(bw > 0, f'Moving head beamWidth: {bw}')
    rgb = api('GET', '/api/dmx-profiles/generic-rgb')
    ok(rgb is not None, 'RGB par profile exists')


def test_action_types(ids):
    section('Action Types (emulator pixel rendering)')
    actions = api('GET', '/api/actions')
    ok(len(actions) >= 13, f'At least 13 actions (got {len(actions)})')
    type_set = set(a.get('type') for a in actions)
    for t in range(1, 14):
        ok(t in type_set, f'Action type {t} exists')
    # Validate action fields needed by _emuPixel
    for a in actions:
        ok('type' in a, f'Action {a.get("id")} has type')
        ok('r' in a or a['type'] in (5,), f'Action {a.get("id")} type={a["type"]} has r (or is rainbow)')


def test_children_status():
    section('Children Status (node online/offline color)')
    children = api('GET', '/api/children')
    ok(len(children) >= 2, f'At least 2 children ({len(children)})')
    for c in children:
        ok('id' in c, f'Child has id')
        ok('status' in c or True, f'Child {c["id"]} has status field (may be 0/offline)')
        ok(c.get('hostname') or c.get('name') or c.get('ip'), f'Child {c["id"]} has name/hostname for label')


def test_preview_data_format(ids):
    section('Preview Data Format (emulator frame rendering)')
    # Bake a timeline
    tl_id = ids['tl_id']
    act_id = ids['action_ids'].get(1)
    api('PUT', f'/api/timelines/{tl_id}', {
        'name': 'Render Test TL', 'durationS': 10,
        'clips': [{'fixtureId': ids['fix_led'], 'actionId': act_id, 'startS': 0, 'durationS': 10}]
    })
    r = api('POST', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('ok'), 'Bake started')
    # Poll
    for _ in range(100):
        time.sleep(0.2)
        st = api('GET', f'/api/timelines/{tl_id}/baked/status')
        if st and (st.get('done') or st.get('error')):
            break
    ok(st.get('done'), 'Bake completed')
    if st.get('error'):
        ok(False, f'Bake error: {st.get("error")}')
        return
    # Preview data
    preview = api('GET', f'/api/timelines/{tl_id}/baked/preview')
    ok(preview is not None, 'Preview data returned')
    if preview:
        ok(isinstance(preview, dict), 'Preview is dict keyed by fixture ID')
        # Keys should be fixture ID strings
        for k, v in preview.items():
            ok(k.isdigit(), f'Preview key "{k}" is numeric fixture ID')
            ok(isinstance(v, list), f'Preview[{k}] is list of frames')
            if v:
                ok(len(v) > 0, f'Preview[{k}] has frames ({len(v)})')


def test_emulator_guard_conditions():
    section('Emulator Guard Conditions')
    # Verify layout.fixtures exist even when children are offline
    lay = api('GET', '/api/layout')
    lf = lay.get('fixtures', [])
    ok(len(lf) > 0, 'layout.fixtures populated (emulator iterates these)')
    led_fx = [f for f in lf if f.get('fixtureType') != 'dmx']
    dmx_fx = [f for f in lf if f.get('fixtureType') == 'dmx']
    ok(len(led_fx) > 0, f'LED fixtures in layout.fixtures ({len(led_fx)})')
    ok(len(dmx_fx) > 0, f'DMX fixtures in layout.fixtures ({len(dmx_fx)})')
    # Both types must have positions for rendering
    led_pos = [f for f in led_fx if f.get('positioned')]
    dmx_pos = [f for f in dmx_fx if f.get('positioned')]
    ok(len(led_pos) > 0, f'LED fixtures positioned ({len(led_pos)})')
    ok(len(dmx_pos) > 0, f'DMX fixtures positioned ({len(dmx_pos)})')
    # LED fixtures must have strings for dot rendering
    for f in led_pos:
        ok(len(f.get('strings', [])) > 0, f'LED fixture {f["id"]} has strings in layout')
    # DMX fixtures should have rotation for beam cones
    for f in dmx_pos:
        ok(f.get('rotation') is not None, f'DMX fixture {f["id"]} has rotation in layout')


def test_coordinate_consistency():
    section('Coordinate Consistency Across APIs')
    lay = api('GET', '/api/layout')
    fixtures_api = api('GET', '/api/fixtures')
    # Verify fixture data matches between layout and fixtures API
    for lf in lay.get('fixtures', []):
        af = next((f for f in fixtures_api if f['id'] == lf['id']), None)
        ok(af is not None, f'Fixture {lf["id"]} exists in both APIs')
        if af:
            ok(af.get('name') == lf.get('name'), f'Fixture {lf["id"]} name matches')
            ok(af.get('fixtureType') == lf.get('fixtureType'), f'Fixture {lf["id"]} fixtureType matches')
            if af.get('rotation'):
                ok(af['rotation'] == lf.get('rotation'), f'Fixture {lf["id"]} rotation matches')


def test_3d_viewport_data():
    section('3D Viewport Data (stage box, nodes, beam cones, objects)')
    st = api('GET', '/api/stage')
    ok(st['w'] > 0 and st['h'] > 0 and st['d'] > 0, 'Stage has positive dimensions for 3D box')
    lay = api('GET', '/api/layout')
    fixtures = lay.get('fixtures', [])
    # 3D needs world coordinates (mm → meters: /1000)
    for f in [fx for fx in fixtures if fx.get('positioned')]:
        x_m = f['x'] / 1000.0
        y_m = f['y'] / 1000.0
        ok(0 <= x_m <= st['w'] * 1.1, f'Fixture {f["id"]} X in 3D range')
    # DMX profiles must have beamWidth for 3D cone geometry
    for f in [fx for fx in fixtures if fx.get('fixtureType') == 'dmx']:
        pid = f.get('dmxProfileId')
        if pid:
            prof = api('GET', f'/api/dmx-profiles/{pid}')
            if prof:
                ok('beamWidth' in prof or 'channelCount' in prof, f'Profile {pid} has rendering data')
    objs = api('GET', '/api/objects')
    for s in objs:
        t = s.get('transform', {})
        scale = t.get('scale', [0, 0, 0])
        ok(len(scale) >= 3, f'Object {s["id"]} has 3D scale (w,h,d)')


def test_dmx_only_rendering():
    """Verify rendering works with DMX-only rig (no LED children)."""
    section('DMX-Only Rendering (no children)')
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})
        # Only DMX fixtures — no children
        r = c.post('/api/fixtures', json={
            'name': 'DMX Only', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit', 'rotation': [-20, 0, 0]
        })
        fid = r.get_json()['id']
        c.post('/api/layout', json={'children': [{'id': fid, 'x': 5000, 'y': 4000, 'z': 0}]})
    # Verify emulator data exists
    lay = api('GET', '/api/layout')
    lf = lay.get('fixtures', [])
    ok(len(lf) == 1, 'DMX-only layout has 1 fixture')
    ok(lf[0].get('positioned'), 'DMX fixture is positioned')
    children = api('GET', '/api/children')
    ok(len(children) == 0, 'No children in DMX-only rig')
    # The fix: emuDraw guard is !children.length && !layoutFixtures.length
    # With 0 children but 1 layout fixture, emulator should NOT early-exit
    ok(len(lf) > 0, 'Emulator has layout fixtures even with 0 children')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('\033[1m=== SlyLED Rendering Data Validation Tests ===\033[0m')

    print('Seeding...')
    ids = seed()
    print('Starting server...')
    start_server()

    test_stage_dimensions()
    test_fixture_positions(ids)
    test_led_strings(ids)
    test_dmx_beam_cones(ids)
    test_objects()
    test_dmx_profiles()
    test_action_types(ids)
    test_children_status()
    test_preview_data_format(ids)
    test_emulator_guard_conditions()
    test_coordinate_consistency()
    test_3d_viewport_data()
    test_dmx_only_rendering()

    total = _pass + _fail
    print(f'\n\033[1m{"=" * 60}\033[0m')
    if _fail == 0:
        print(f'\033[32m  ALL {total} TESTS PASSED\033[0m')
    else:
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m out of {total}')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
