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
        # Universe 99 keeps these tests off every real stage universe —
        # some engines (Art-Net) broadcast, so U1 writes would reach live
        # fixtures on the LAN even through the test client.
        r = c.post('/api/fixtures', json={
            'name': 'MH Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 99, 'dmxStartAddr': 1, 'dmxChannelCount': 10,
            'dmxProfileId': 'test-mh'
        })
        mover_id = r.get_json().get('id')
        # #784 PR-5 — `_aim_to_pan_tilt` is now sphere-only and needs a
        # Home anchor + a moving-head profile (`panRange`/`tiltRange`).
        # Without Home it correctly refuses to aim, so the orient/streaming
        # tests below would assert against (None, None). Save Home at the
        # mid-range pose so the sphere can build.
        c.post(f'/api/fixtures/{mover_id}/home',
                json={'panDmx16': 32768, 'tiltDmx16': 32768})

        # Start DMX engine, then swap its socket for a no-op proxy so the
        # test never emits Art-Net frames onto the LAN — we only need the
        # universe buffer state, not actual network output. (An earlier
        # run on U1 reached a live DMX bridge and latched a strobe on
        # Sly MH 2 — never again.)
        c.post('/api/dmx/start', json={'protocol': 'artnet'})
        time.sleep(0.05)  # let _run_loop bind the real socket first
        import socket as _sock_mod
        class _MuteSock:
            def sendto(self, *a, **kw): pass
            def recvfrom(self, n): raise _sock_mod.timeout
            def close(self): pass
            def setblocking(self, *a, **kw): pass
            def settimeout(self, *a, **kw): pass
        if parent_server._artnet._sock is not None:
            try: parent_server._artnet._sock.close()
            except Exception: pass
            parent_server._artnet._sock = _MuteSock()

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

        # Wait for tick to process. The engine tick loop is daemon-threaded
        # at 40 Hz (#784 PR-5 path: aim builds a sphere lookup table once
        # per fixture, ~6 ms; subsequent ticks reuse the cache); 1.5 s
        # gives the orient effect ample time to propagate even with
        # interleaved Flask test-client requests on the same process.
        time.sleep(1.5)

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

        # ── Flash / strobe release (#509) ───────────────────────
        section('Flash release — #509')
        # Build a second profile with an annotated strobe channel so we
        # can assert the channel returns to its Open range after flash-off.
        c.post('/api/dmx-profiles', json={
            "id": "test-strobe", "name": "Test Strobe", "manufacturer": "Test",
            "category": "moving-head", "channelCount": 6,
            "panRange": 540, "tiltRange": 270,
            "channels": [
                {"offset": 0, "name": "Pan",    "type": "pan",    "bits": 16},
                {"offset": 2, "name": "Tilt",   "type": "tilt",   "bits": 16},
                {"offset": 4, "name": "Shutter","type": "strobe", "default": 0,
                 "capabilities": [
                    {"type": "ShutterStrobe", "shutterEffect": "Open",
                     "range": [0, 10], "label": "Open"},
                    {"type": "ShutterStrobe", "shutterEffect": "Strobe",
                     "range": [50, 200], "label": "Strobe slow→fast"},
                 ]},
                {"offset": 5, "name": "Dimmer", "type": "dimmer", "default": 255},
            ],
        })
        r = c.post('/api/fixtures', json={
            'name': 'Strobe MH', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 99, 'dmxStartAddr': 20, 'dmxChannelCount': 6,
            'dmxProfileId': 'test-strobe',
        })
        strobe_mover = r.get_json().get('id')
        # #784 PR-5 — sphere needs Home to compute pan/tilt; strobe
        # write itself is independent but the claim flow still calls
        # _aim_to_pan_tilt during orient. Home at midpoint.
        c.post(f'/api/fixtures/{strobe_mover}/home',
                json={'panDmx16': 32768, 'tiltDmx16': 32768})
        api(c, 'POST', '/api/mover-control/claim',
            {'moverId': strobe_mover, 'deviceId': 'phone-2', 'deviceName': 'Pixel'})
        api(c, 'POST', '/api/mover-control/start',
            {'moverId': strobe_mover, 'deviceId': 'phone-2'})
        api(c, 'POST', '/api/mover-control/calibrate-start',
            {'moverId': strobe_mover, 'deviceId': 'phone-2',
             'roll': 0, 'pitch': 0, 'yaw': 0})
        api(c, 'POST', '/api/mover-control/calibrate-end',
            {'moverId': strobe_mover, 'deviceId': 'phone-2',
             'roll': 0, 'pitch': 0, 'yaw': 0})
        api(c, 'POST', '/api/mover-control/orient',
            {'moverId': strobe_mover, 'deviceId': 'phone-2',
             'roll': 5, 'pitch': 5, 'yaw': 5})
        time.sleep(1.0)
        engine = parent_server._artnet
        uni = engine.get_universe(99)
        shutter_dmx = lambda: uni.get_data()[20 + 4 - 1]  # addr 20 + offset 4
        api(c, 'POST', '/api/mover-control/flash',
            {'moverId': strobe_mover, 'deviceId': 'phone-2', 'on': True})
        time.sleep(1.0)
        on_val = shutter_dmx()
        ok(50 <= on_val <= 200, f'Flash on → shutter in Strobe range ({on_val})')
        api(c, 'POST', '/api/mover-control/flash',
            {'moverId': strobe_mover, 'deviceId': 'phone-2', 'on': False})
        time.sleep(0.15)
        off_val = shutter_dmx()
        ok(0 <= off_val <= 10,
           f'Flash off → shutter back to Open range ({off_val}) — #509')
        api(c, 'POST', '/api/mover-control/release',
            {'moverId': strobe_mover, 'deviceId': 'phone-2'})

        # ── Release blackout (#650) ──────────────────────────────
        section('Release blackout — #650')
        # Claim a fresh mover, light it up, then release and assert the
        # fixture's dimmer+RGB channels are zero. Pan/tilt stay put.
        r = c.post('/api/fixtures', json={
            'name': 'Blackout MH', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 99, 'dmxStartAddr': 30, 'dmxChannelCount': 10,
            'dmxProfileId': 'test-mh',
        })
        blackout_mover = r.get_json().get('id')
        api(c, 'POST', '/api/mover-control/claim',
            {'moverId': blackout_mover, 'deviceId': 'phone-3', 'deviceName': 'Pixel 8'})
        api(c, 'POST', '/api/mover-control/start',
            {'moverId': blackout_mover, 'deviceId': 'phone-3'})
        api(c, 'POST', '/api/mover-control/color',
            {'moverId': blackout_mover, 'deviceId': 'phone-3',
             'r': 255, 'g': 128, 'b': 64})
        time.sleep(0.1)
        engine = parent_server._artnet
        uni = engine.get_universe(99)
        # Addr 30, offsets: Pan=0,1 Tilt=2,3 Dimmer=4 R=5 G=6 B=7
        before = uni.get_data()
        ok(before[30 + 4 - 1] > 0, f'Pre-release dimmer lit ({before[30 + 4 - 1]})')
        ok(before[30 + 5 - 1] == 255, f'Pre-release red=255 ({before[30 + 5 - 1]})')

        r = api(c, 'POST', '/api/mover-control/release',
                {'moverId': blackout_mover, 'deviceId': 'phone-3'})
        ok(r and r.get('ok'), 'Release → ok')
        ok('engineRunning' in r, 'Release response surfaces engineRunning (#647)')
        time.sleep(0.1)
        after = uni.get_data()
        ok(after[30 + 4 - 1] == 0, f'Post-release dimmer=0 ({after[30 + 4 - 1]})')
        ok(after[30 + 5 - 1] == 0, f'Post-release red=0 ({after[30 + 5 - 1]})')
        ok(after[30 + 6 - 1] == 0, f'Post-release green=0 ({after[30 + 6 - 1]})')
        ok(after[30 + 7 - 1] == 0, f'Post-release blue=0 ({after[30 + 7 - 1]})')

        # ── Wrong device ────────────────────────────────────────
        section('Wrong device rejection')

        r = api(c, 'POST', '/api/mover-control/orient',
                {'moverId': mover_id, 'deviceId': 'wrong-device',
                 'roll': 0, 'pitch': 0, 'yaw': 0})
        ok(r and not r.get('ok'), 'Orient from wrong device → rejected')

        r = api(c, 'POST', '/api/mover-control/release',
                {'moverId': mover_id, 'deviceId': 'wrong-device'})
        ok(not r.get('ok'), 'Release from wrong device → rejected')

        # ── Cleanup — release and blackout ─────────────────────
        api(c, 'POST', '/api/mover-control/release',
            {'moverId': mover_id, 'deviceId': 'gyro-1'})
        c.post('/api/dmx/blackout', json={})
        c.post('/api/dmx/stop', json={})

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
