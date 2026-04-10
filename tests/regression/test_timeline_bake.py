"""Timeline bake regression test (split from test_full_show.py).

Phases 3+4: Create timeline with spatial effect, bake, verify preview, start
show, check runnerRunning. Pure API, no Playwright.

Run: python -X utf8 tests/regression/test_timeline_bake.py
"""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5572
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
    # ── Setup: fixtures + layout ─────────────────────────────────────────
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

    # ── Timeline & Bake ──────────────────────────────────────────────────
    print('\n=== Timeline & Bake ===')

    # Create spatial effect
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

    # Create timeline
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
    st = {}
    for _ in range(30):
        r = api('GET', f'/api/timelines/{tl_id}/baked/status')
        st = r.json()
        if st.get('done'):
            bake_done = True
            break
        time.sleep(1)
    check('Bake completed', bake_done)
    check('Bake no error', st.get('error') is None)

    # Verify preview data
    r = api('GET', f'/api/timelines/{tl_id}/baked/preview')
    check('Preview data available', r.status_code == 200 and isinstance(r.json(), dict))

    # ── Start Show ───────────────────────────────────────────────────────
    print('\n=== Start Show ===')

    r = api('POST', f'/api/timelines/{tl_id}/start')
    check('Show started', r.status_code == 200)

    time.sleep(1)
    settings = api('GET', '/api/settings').json()
    check('runnerRunning is true', settings.get('runnerRunning') == True)

    # Stop show
    r = api('POST', f'/api/timelines/{tl_id}/stop')
    check('Show stopped', r.status_code == 200)
    time.sleep(1)

    settings = api('GET', '/api/settings').json()
    check('runnerRunning is false after stop', settings.get('runnerRunning') == False)

    # ── Summary ──────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'{passed} passed, {failed} failed out of {passed + failed} tests')
    print(f'{"="*60}')

finally:
    proc.kill()
    sys.exit(1 if failed else 0)
