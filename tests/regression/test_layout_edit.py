"""Layout edit regression test (split from test_full_show.py).

Phase 2: Layout 3D verification, default view, double-click dialog, rotation
fields, inverted checkbox, save persistence. Needs Playwright. Creates fixtures
via API first.

Run: python -X utf8 tests/regression/test_layout_edit.py
"""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5571
BASE = f'http://localhost:{PORT}'

proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', str(PORT)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    requests.get(f'{BASE}/api/settings', timeout=5)
    print('Server up on port', PORT)
except Exception:
    print('Server failed'); proc.kill(); sys.exit(1)

passed = 0
failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print(f'  [PASS] {name}')
    else: failed += 1; print(f'  [FAIL] {name}')

def api(method, path, data=None):
    fn = getattr(requests, method.lower())
    headers = {'X-SlyLED-Confirm': 'true'} if '/reset' in path else {}
    r = fn(f'{BASE}{path}', json=data, headers=headers, timeout=10)
    return r

try:
    # ── Setup: create fixtures via API ───────────────────────────────────
    print('\n=== Setup: Create Fixtures ===')

    api('POST', '/api/reset')
    time.sleep(1)

    api('POST', '/api/stage', {'w': 6, 'h': 3, 'd': 4})

    api('POST', '/api/dmx-profiles', {
        'id': 'test-mover-13ch', 'name': 'Test Moving Head 13ch',
        'manufacturer': 'Test', 'category': 'moving-head',
        'panRange': 540, 'tiltRange': 270, 'beamWidth': 15,
        'channels': [
            {'offset': 0, 'type': 'pan', 'capabilities': [{'range': [0, 255], 'type': 'Pan', 'label': 'Pan'}]},
            {'offset': 1, 'type': 'tilt', 'capabilities': [{'range': [0, 255], 'type': 'Tilt', 'label': 'Tilt'}]},
            {'offset': 2, 'type': 'speed', 'capabilities': [{'range': [0, 255], 'type': 'Speed', 'label': 'Speed'}]},
            {'offset': 3, 'type': 'dimmer', 'capabilities': [{'range': [0, 255], 'type': 'Intensity', 'label': 'Dimmer'}]},
            {'offset': 4, 'type': 'strobe', 'capabilities': [{'range': [0, 255], 'type': 'ShutterStrobe', 'label': 'Strobe'}]},
            {'offset': 5, 'type': 'red', 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity', 'label': 'Red'}]},
            {'offset': 6, 'type': 'green', 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity', 'label': 'Green'}]},
            {'offset': 7, 'type': 'blue', 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity', 'label': 'Blue'}]},
        ],
    })

    r = api('POST', '/api/fixtures', {
        'name': 'MH1 Left', 'fixtureType': 'dmx',
        'rotation': [-30, -10, 0],
        'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 13,
        'dmxProfileId': 'test-mover-13ch',
    })
    mh1_id = r.json()['id']
    api('PUT', f'/api/fixtures/{mh1_id}', {'mountedInverted': True})

    r = api('POST', '/api/fixtures', {
        'name': 'MH2 Right', 'fixtureType': 'dmx',
        'rotation': [-30, 10, 0],
        'dmxUniverse': 1, 'dmxStartAddr': 14, 'dmxChannelCount': 13,
        'dmxProfileId': 'test-mover-13ch',
    })
    mh2_id = r.json()['id']

    r = api('POST', '/api/fixtures', {
        'name': 'LED Strip Center', 'fixtureType': 'led',
        'strings': [{'leds': 60, 'mm': 2000, 'sdir': 0}],
    })
    led_id = r.json()['id']

    layout = api('GET', '/api/layout').json()
    layout['children'] = [
        {'id': mh1_id, 'x': 1500, 'y': 0, 'z': 2800},
        {'id': mh2_id, 'x': 4500, 'y': 0, 'z': 2800},
        {'id': led_id, 'x': 3000, 'y': 0, 'z': 3000},
    ]
    api('POST', '/api/layout', layout)

    # ── Layout Verification (Playwright) ─────────────────────────────────
    print('\n=== Layout Edit Verification ===')

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.goto(BASE)
    page.wait_for_timeout(2000)

    # Layout tab -- default 3D
    page.click('#n-layout')
    page.wait_for_timeout(3000)

    view = page.evaluate('() => window._layView')
    check('Layout default view is 3D', view == '3d')

    nodes = page.evaluate('() => (window._s3d && window._s3d.nodes) ? window._s3d.nodes.length : 0')
    check('3 fixture nodes in layout', nodes == 3)

    cones = page.evaluate('''() => {
        var c = 0;
        if(window._s3d && window._s3d.nodes) window._s3d.nodes.forEach(function(g) {
            g.traverse(function(o) { if(o.userData && o.userData.beamCone) c++; });
        });
        return c;
    }''')
    check('Beam cones in layout: ' + str(cones), cones >= 2)

    # Double-click MH1, verify rotation fields
    page.evaluate(f'() => {{ var f=null; _fixtures.forEach(function(fx){{if(fx.id==={mh1_id})f=fx;}}); if(f)showNodeEdit(f); }}')
    page.wait_for_timeout(500)
    tilt_val = page.evaluate('() => document.getElementById("ne-tilt") ? document.getElementById("ne-tilt").value : null')
    check('MH1 double-click shows tilt=-30', tilt_val == '-30')

    inv_checked = page.evaluate('() => { var el=document.getElementById("ne-inverted"); return el ? el.checked : null; }')
    check('MH1 inverted checkbox checked', inv_checked == True)

    # Save and verify fixtures persist
    page.evaluate(f'() => applyNodePos({mh1_id})')
    page.wait_for_timeout(3000)
    nodes_after = page.evaluate('() => (window._s3d && window._s3d.nodes) ? window._s3d.nodes.length : 0')
    check('Fixtures persist after save: ' + str(nodes_after), nodes_after == 3)

    # Verify layout readback via API matches
    lay = api('GET', '/api/layout').json()
    fixtures = lay.get('fixtures', [])
    positioned = [f for f in fixtures if f.get('positioned')]
    check('3 fixtures still positioned after edit', len(positioned) == 3)

    # Verify rotation survived save
    mh1 = next((f for f in fixtures if f['id'] == mh1_id), None)
    check('MH1 rotation persists after save', mh1 and mh1.get('rotation', [None])[0] == -30)

    page.screenshot(path='tests/regression/layout_edit.png')

    browser.close()
    pw.stop()

    # ── Summary ──────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'{passed} passed, {failed} failed out of {passed + failed} tests')
    print(f'{"="*60}')

finally:
    proc.kill()
    sys.exit(1 if failed else 0)
