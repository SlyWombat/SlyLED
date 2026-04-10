"""Runtime 3D show regression test (split from test_full_show.py).

Phases 5+6+7: Creates everything via API, starts show, then Playwright checks
Runtime 3D canvas, fixture nodes, beam cones, dashboard container. Stops and
verifies cleanup.

Run: python -X utf8 tests/regression/test_runtime_3d_show.py
"""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5573
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
    # ── Setup: fixtures + layout + timeline + bake + start ───────────────
    print('\n=== Setup: Full Show Pipeline ===')

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

    # Spatial effect + timeline + bake
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
    fx_id = r.json()['id']

    r = api('POST', '/api/timelines', {
        'name': 'Regression Test Show',
        'durationS': 30,
        'loop': True,
        'tracks': [{
            'allPerformers': True,
            'clips': [{'startS': 0, 'durationS': 30, 'effectId': fx_id}],
        }],
    })
    tl_id = r.json()['id']

    r = api('POST', f'/api/timelines/{tl_id}/bake')
    bake_done = False
    for _ in range(30):
        r = api('GET', f'/api/timelines/{tl_id}/baked/status')
        if r.json().get('done'):
            bake_done = True
            break
        time.sleep(1)

    if not bake_done:
        print('  [SKIP] Bake did not complete -- cannot test runtime 3D')
        proc.kill()
        sys.exit(1)

    # Start show
    api('POST', f'/api/timelines/{tl_id}/start')
    time.sleep(1)

    # ── Runtime 3D (Playwright) ──────────────────────────────────────────
    print('\n=== Runtime 3D ===')

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    js_errors = []
    page.on('console', lambda m: js_errors.append(m.text) if m.type == 'error' else None)
    page.goto(BASE)
    page.wait_for_timeout(2000)

    page.click('#n-runtime')
    page.wait_for_timeout(12000)

    rt_canvas = page.evaluate('() => !!document.querySelector("#emu-3d canvas")')
    check('Runtime has 3D canvas', rt_canvas)

    rt_nodes = page.evaluate('() => (window._emu3d && window._emu3d.nodes) ? window._emu3d.nodes.length : 0')
    check('Runtime fixture nodes: ' + str(rt_nodes), rt_nodes >= 2)

    rt_cones = page.evaluate('''() => {
        var c = 0;
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) { if(o.userData && o.userData.beamCone) c++; });
        });
        return c;
    }''')
    check('Runtime beam cones: ' + str(rt_cones), rt_cones >= 2)

    page.screenshot(path='tests/regression/runtime_3d_show.png')

    # ── Dashboard ────────────────────────────────────────────────────────
    print('\n=== Dashboard ===')

    page.click('#n-dash')
    page.wait_for_timeout(5000)

    dash_3d = page.evaluate('() => !!document.getElementById("dash-3d")')
    check('Dashboard dash-3d container exists', dash_3d)

    page.screenshot(path='tests/regression/dashboard_3d_show.png')

    # ── Cone color check ─────────────────────────────────────────────────
    print('\n=== Cone Animation ===')

    # Navigate back to runtime for cone state
    page.click('#n-runtime')
    page.wait_for_timeout(5000)

    cone_t0 = page.evaluate('''() => {
        var result = [];
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) {
                if(o.userData && o.userData.beamCone && o.isMesh) {
                    result.push({color: o.material.color.getHexString(), opacity: o.material.opacity.toFixed(2)});
                }
            });
        });
        return result;
    }''')

    page.wait_for_timeout(5000)

    cone_t5 = page.evaluate('''() => {
        var result = [];
        if(window._emu3d && window._emu3d.nodes) window._emu3d.nodes.forEach(function(g) {
            g.traverse(function(o) {
                if(o.userData && o.userData.beamCone && o.isMesh) {
                    result.push({color: o.material.color.getHexString(), opacity: o.material.opacity.toFixed(2)});
                }
            });
        });
        return result;
    }''')

    if cone_t5:
        check('Cone color not default idle', any(c['color'] != 'ffff88' or c['opacity'] != '0.10' for c in cone_t5))
    else:
        check('Cone color not default idle', False)

    # ── Stop & Cleanup ───────────────────────────────────────────────────
    print('\n=== Stop & Cleanup ===')

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

    page.screenshot(path='tests/regression/runtime_3d_stopped.png')

    browser.close()
    pw.stop()

    # ── Summary ──────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'{passed} passed, {failed} failed out of {passed + failed} tests')
    print(f'{"="*60}')

finally:
    proc.kill()
    sys.exit(1 if failed else 0)
