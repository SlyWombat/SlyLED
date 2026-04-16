#!/usr/bin/env python3
"""test_mover_control.py — Unit tests for unified mover control engine (#472)."""
import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18299
_pass = 0
_fail = 0
_errors = []

def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')

def section(name):
    print(f'\n-- {name} --')

def api(c, method, path, body=None):
    if method == 'GET':
        r = c.get(path)
    elif method == 'POST':
        r = c.post(path, json=body)
    elif method == 'DELETE':
        r = c.delete(path)
    else:
        r = c.put(path, json=body)
    return r.get_json()

def main():
    print('=== Mover Control Engine Tests ===')

    import parent_server
    from parent_server import app

    with app.test_client() as c:
        # Reset
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Create a mover fixture
        c.post('/api/dmx-profiles', json={
            "id": "test-mh", "name": "Test MH", "manufacturer": "Test",
            "category": "moving-head", "channelCount": 10,
            "panRange": 540, "tiltRange": 270,
            "channels": [
                {"offset": 0, "name": "Pan", "type": "pan", "bits": 16},
                {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16},
                {"offset": 4, "name": "Dimmer", "type": "dimmer"},
                {"offset": 5, "name": "Red", "type": "red"},
                {"offset": 6, "name": "Green", "type": "green"},
                {"offset": 7, "name": "Blue", "type": "blue"},
            ]
        })
        r = c.post('/api/fixtures', json={
            'name': 'MH Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 10,
            'dmxProfileId': 'test-mh'
        })
        mover_id = r.get_json().get('id')

        # Start DMX engine
        c.post('/api/dmx/start', json={'protocol': 'artnet'})

        # ── Claim Tests ─────────────────────────────────────────
        section('Claim / Release')

        r = api(c, 'POST', '/api/mover-control/claim',
                {'moverId': mover_id, 'deviceId': 'phone-1', 'deviceName': 'Pixel 8'})
        ok(r and r.get('ok'), 'Claim mover → ok')

        r = api(c, 'POST', '/api/mover-control/claim',
                {'moverId': mover_id, 'deviceId': 'gyro-1', 'deviceName': 'SLYG-001'})
        ok(r and not r.get('ok'), 'Second claim → rejected')
        ok('Pixel 8' in r.get('err', ''), 'Rejection names the holder')

        r = api(c, 'GET', '/api/mover-control/status')
        ok(r and len(r.get('claims', [])) == 1, 'Status shows 1 claim')
        ok(r['claims'][0]['deviceName'] == 'Pixel 8', 'Status shows correct device')

        r = api(c, 'POST', '/api/mover-control/release',
                {'moverId': mover_id, 'deviceId': 'phone-1'})
        ok(r and r.get('ok'), 'Release → ok')

        r = api(c, 'GET', '/api/mover-control/status')
        ok(len(r.get('claims', [])) == 0, 'Status empty after release')

        # Re-claim after release
        r = api(c, 'POST', '/api/mover-control/claim',
                {'moverId': mover_id, 'deviceId': 'gyro-1', 'deviceName': 'SLYG-001'})
        ok(r and r.get('ok'), 'Reclaim after release → ok')

        # ── Start / Calibrate / Orient ──────────────────────────
        section('Stream / Calibrate / Orient')

        r = api(c, 'POST', '/api/mover-control/start',
                {'moverId': mover_id, 'deviceId': 'gyro-1'})
        ok(r and r.get('ok'), 'Start stream → ok')

        r = api(c, 'POST', '/api/mover-control/calibrate-start',
                {'moverId': mover_id, 'deviceId': 'gyro-1',
                 'roll': 10.0, 'pitch': -20.0, 'yaw': 45.0})
        ok(r and r.get('ok'), 'Calibrate start → ok')
        ok('refPan' in r, 'Returns refPan')

        r = api(c, 'POST', '/api/mover-control/calibrate-end',
                {'moverId': mover_id, 'deviceId': 'gyro-1',
                 'roll': 10.0, 'pitch': -20.0, 'yaw': 45.0})
        ok(r and r.get('ok'), 'Calibrate end → ok')

        # Orient
        r = api(c, 'POST', '/api/mover-control/orient',
                {'moverId': mover_id, 'deviceId': 'gyro-1',
                 'roll': 20.0, 'pitch': -30.0, 'yaw': 50.0})
        ok(r and r.get('ok'), 'Orient → ok')

        # Wait for tick to process
        time.sleep(0.1)

        r = api(c, 'GET', '/api/mover-control/status')
        claim = r['claims'][0] if r.get('claims') else {}
        ok(claim.get('calibrated'), 'Claim shows calibrated')
        ok(claim.get('state') == 'streaming', 'State is streaming')
        # Pan/tilt should have moved from reference (delta applied)
        ok(claim.get('panNorm', 0.5) != 0.5 or claim.get('tiltNorm', 0.5) != 0.5,
           'Pan/tilt moved from center')

        # ── Color ───────────────────────────────────────────────
        section('Color')

        r = api(c, 'POST', '/api/mover-control/color',
                {'moverId': mover_id, 'deviceId': 'gyro-1',
                 'r': 255, 'g': 0, 'b': 0})
        ok(r and r.get('ok'), 'Set color → ok')

        r = api(c, 'GET', '/api/mover-control/status')
        color = r['claims'][0].get('color', {}) if r.get('claims') else {}
        ok(color.get('r') == 255, 'Color r=255')
        ok(color.get('g') == 0, 'Color g=0')

        # ── Wrong device ────────────────────────────────────────
        section('Wrong device rejection')

        r = api(c, 'POST', '/api/mover-control/orient',
                {'moverId': mover_id, 'deviceId': 'wrong-device',
                 'roll': 0, 'pitch': 0, 'yaw': 0})
        ok(r and not r.get('ok'), 'Orient from wrong device → rejected')

        r = api(c, 'POST', '/api/mover-control/release',
                {'moverId': mover_id, 'deviceId': 'wrong-device'})
        ok(not r.get('ok'), 'Release from wrong device → rejected')

        # ── Cleanup ─────────────────────────────────────────────
        api(c, 'POST', '/api/mover-control/release',
            {'moverId': mover_id, 'deviceId': 'gyro-1'})

    total = _pass + _fail
    print(f'\n{"="*60}')
    if _fail == 0:
        print(f'\033[32m  ALL {total} TESTS PASSED\033[0m')
    else:
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m out of {total}')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)

if __name__ == '__main__':
    main()
