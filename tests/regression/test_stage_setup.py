"""Stage setup regression test (split from test_full_show.py).

Phase 1: stage dimensions, DMX profile, fixture creation, layout positions,
mountedInverted. Pure API tests, no Playwright.

Run: python -X utf8 tests/regression/test_stage_setup.py
"""
import subprocess, time, requests, sys, os

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
    print('\n=== Stage Setup ===')

    # Factory reset
    api('POST', '/api/reset')
    time.sleep(1)

    # Stage dimensions: 6m x 3m x 4m
    r = api('POST', '/api/stage', {'w': 6, 'h': 3, 'd': 4})
    check('Stage dimensions set', r.status_code == 200)

    # Verify stage readback
    r = api('GET', '/api/stage')
    check('Stage readback', r.status_code == 200)

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

    # Verify fixture list via API
    r = api('GET', '/api/fixtures')
    check('Fixture list has 3', len(r.json()) == 3)

    # Verify profile persists
    r = api('GET', '/api/dmx-profiles')
    profiles = r.json()
    check('Profile persists', any(p['id'] == 'test-mover-13ch' for p in profiles))

    # ── Summary ──────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print(f'{passed} passed, {failed} failed out of {passed + failed} tests')
    print(f'{"="*60}')

finally:
    proc.kill()
    sys.exit(1 if failed else 0)
