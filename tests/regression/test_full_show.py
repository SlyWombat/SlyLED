"""End-to-end show regression test (#277).

Sets up a complete show from scratch: fixtures → layout → timeline → bake →
playback → validates 3D runtime visualization via Playwright.

Run: python -X utf8 tests/regression/test_full_show.py
Time: ~90 seconds
"""
import subprocess, time, requests, sys, os, json

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5570
BASE = f'http://localhost:{PORT}'

proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', str(PORT)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    requests.get(f'{BASE}/api/settings', timeout=5)
    print('Server up on port', PORT)
except:
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
    # ── Phase 1: Stage Setup ─────────────────────────────────────────────
    print('\n=== Phase 1: Stage Setup ===')

    # Factory reset
    api('POST', '/api/reset')
    time.sleep(1)

    # Stage dimensions: 6m x 3m x 4m
    r = api('POST', '/api/stage', {'w': 6, 'h': 3, 'd': 4})
    check('Stage dimensions set', r.status_code == 200)

    # Create DMX profile with pan/tilt
    r = api('POST', '/api/dmx-profiles', {
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
    check('DMX profile created', r.status_code == 200)

    # Create MH1 (stage left, truss, inverted)
    r = api('POST', '/api/fixtures', {
        'name': 'MH1 Left', 'fixtureType': 'dmx',
        'rotation': [-30, -10, 0],
        'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 13,
        'dmxProfileId': 'test-mover-13ch',
    })
    check('MH1 created', r.status_code == 200)
    mh1_id = r.json()['id']
    # Set mountedInverted via PUT (not available on POST)
    api('PUT', f'/api/fixtures/{mh1_id}', {'mountedInverted': True})

    # Create MH2 (stage right, truss)
    r = api('POST', '/api/fixtures', {
        'name': 'MH2 Right', 'fixtureType': 'dmx',
        'rotation': [-30, 10, 0],
        'dmxUniverse': 1, 'dmxStartAddr': 14, 'dmxChannelCount': 13,
        'dmxProfileId': 'test-mover-13ch',
    })
    check('MH2 created', r.status_code == 200)
    mh2_id = r.json()['id']

    # Create LED strip
    r = api('POST', '/api/fixtures', {
        'name': 'LED Strip Center', 'fixtureType': 'led',
        'strings': [{'leds': 60, 'mm': 2000, 'sdir': 0}],
    })
    check('LED strip created', r.status_code == 200)
    led_id = r.json()['id']

    # Position all fixtures in layout
    layout = api('GET', '/api/layout').json()
    layout['children'] = [
        {'id': mh1_id, 'x': 1500, 'y': 0, 'z': 2800},
        {'id': mh2_id, 'x': 4500, 'y': 0, 'z': 2800},
        {'id': led_id, 'x': 3000, 'y': 0, 'z': 3000},
    ]
    r = api('POST', '/api/layout', layout)
    check('Layout positions saved', r.status_code == 200)

    # Verify fixtures are positioned
    lay = api('GET', '/api/layout').json()
    fixtures = lay.get('fixtures', [])
    positioned = [f for f in fixtures if f.get('positioned')]
    check('3 fixtures positioned', len(positioned) == 3)

    # Verify MH1 is inverted
    mh1 = next((f for f in fixtures if f['id'] == mh1_id), None)
    check('MH1 mountedInverted', mh1 and mh1.get('mountedInverted'))

    # ── Phase 2: Layout Verification (Playwright) ────────────────────────
    print('\n=== Phase 2: Layout Verification ===')

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    js_errors = []
    dash_logs = []
    page.on('console', lambda m: js_errors.append(m.text) if m.type == 'error' else (dash_logs.append(m.text) if 'DASH3D' in m.text else None))
    page.goto(BASE)
    page.wait_for_timeout(2000)

    # Layout tab — default 3D
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

    # Double-click MH1, verify rotation, save, verify fixtures persist
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

    page.screenshot(path='tests/regression/layout_regression.png')

    # ── Phase 3: Timeline & Show Creation ────────────────────────────────
    print('\n=== Phase 3: Timeline Creation ===')

    # Create spatial effect: green sphere, figure-8 motion
    r = api('POST', '/api/spatial-effects', {
        'name': 'Green Sweep', 'category': 'spatial-field',
        'shape': 'sphere', 'r': 0, 'g': 255, 'b': 50,
        'size': {'radius': 1000},
        'motion': {
            'startPos': [1000, 2000, 0],
            'endPos': [5000, 2000, 0],
            'easing': 'ease-in-out',
            'durationS': 10,
        },
    })
    check('Spatial effect created', r.status_code == 200)
    fx_id = r.json()['id']

    # Create timeline with track
    r = api('POST', '/api/timelines', {
        'name': 'Regression Test Show',
        'durationS': 30,
        'loop': True,
        'tracks': [{
            'allPerformers': True,
            'clips': [{'startS': 0, 'durationS': 30, 'effectId': fx_id}],
        }],
    })
    check('Timeline created', r.status_code == 200)
    tl_id = r.json()['id']

    # Bake
    r = api('POST', f'/api/timelines/{tl_id}/bake')
    check('Bake started', r.status_code == 200)

    # Poll bake status
    bake_done = False
    for _ in range(30):
        r = api('GET', f'/api/timelines/{tl_id}/baked/status')
        st = r.json()
        if st.get('done'):
            bake_done = True
            break
        time.sleep(1)
    check('Bake completed', bake_done)
    check('Bake no error', st.get('error') is None)

    # Verify preview data exists
    r = api('GET', f'/api/timelines/{tl_id}/baked/preview')
    check('Preview data available', r.status_code == 200 and isinstance(r.json(), dict))

    # ── Phase 4: Start Show ──────────────────────────────────────────────
    print('\n=== Phase 4: Start Show ===')

    r = api('POST', f'/api/timelines/{tl_id}/start')
    check('Show started', r.status_code == 200)

    time.sleep(1)
    settings = api('GET', '/api/settings').json()
    check('runnerRunning is true', settings.get('runnerRunning') == True)

    # ── Phase 5: Runtime 3D (test Runtime first — more reliable than Dashboard) ──
    print('\n=== Phase 5: Runtime 3D ===')

    page.click('#n-runtime')
    # Wait for emuLoadStage async chain (5 nested API calls)
    page.wait_for_timeout(12000)

    rt_canvas = page.evaluate('() => !!document.querySelector("#emu-3d canvas")')
    check('Runtime has 3D canvas', rt_canvas)

    rt_nodes = page.evaluate('() => (window._emu3d && window._emu3d.nodes) ? window._emu3d.nodes.length : 0')
    check('Runtime fixture nodes: ' + str(rt_nodes), rt_nodes >= 2)

    page.screenshot(path='tests/regression/runtime_show_playing.png')

    # ── Phase 6: Dashboard show status ───────────────────────────────────
    print('\n=== Phase 6: Dashboard ===')

    page.click('#n-dash')
    page.wait_for_timeout(5000)

    dash_3d = page.evaluate('() => !!document.getElementById("dash-3d")')
    check('Dashboard dash-3d container exists', dash_3d)

    page.screenshot(path='tests/regression/dashboard_show_running.png')

    # Check beam cones exist in runtime
    rt_cones = page.evaluate('''() => {
        var c = 0;
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) { if(o.userData && o.userData.beamCone) c++; });
        });
        return c;
    }''')
    check('Runtime beam cones: ' + str(rt_cones), rt_cones >= 2)

    # Capture cone state at T=0
    cone_t0 = page.evaluate('''() => {
        var result = [];
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) {
                if(o.userData && o.userData.beamCone && o.isMesh) {
                    result.push({x: o.position.x.toFixed(3), y: o.position.y.toFixed(3), z: o.position.z.toFixed(3),
                        color: o.material.color.getHexString(), opacity: o.material.opacity.toFixed(2)});
                }
            });
        });
        return result;
    }''')

    page.wait_for_timeout(5000)
    page.screenshot(path='tests/regression/runtime_show_playing.png')

    # Cone state at T+5
    cone_t5 = page.evaluate('''() => {
        var result = [];
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) {
                if(o.userData && o.userData.beamCone && o.isMesh) {
                    result.push({x: o.position.x.toFixed(3), y: o.position.y.toFixed(3), z: o.position.z.toFixed(3),
                        color: o.material.color.getHexString(), opacity: o.material.opacity.toFixed(2)});
                }
            });
        });
        return result;
    }''')

    # Check cone color changed from idle (ffff88 is default yellow)
    if cone_t5:
        check('Cone color not default idle', any(c['color'] != 'ffff88' or c['opacity'] != '0.10' for c in cone_t5))
    else:
        check('Cone color not default idle', False)

    # ── Phase 7: Stop & Cleanup ──────────────────────────────────────────
    print('\n=== Phase 7: Stop & Cleanup ===')

    r = api('POST', f'/api/timelines/{tl_id}/stop')
    check('Show stopped', r.status_code == 200)
    time.sleep(1)

    settings = api('GET', '/api/settings').json()
    check('runnerRunning is false', settings.get('runnerRunning') == False)

    # Check no JS errors (filter network errors)
    real_errors = [e for e in js_errors if '400' not in e and '404' not in e and 'favicon' not in e]
    check('No JS errors', len(real_errors) == 0)
    if real_errors:
        for e in real_errors[:5]:
            print(f'    JS ERROR: {e}')

    browser.close()
    pw.stop()

    # ── Summary ──────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'{passed} passed, {failed} failed out of {passed + failed} tests')
    print(f'{"="*60}')

finally:
    proc.kill()
    sys.exit(1 if failed else 0)
