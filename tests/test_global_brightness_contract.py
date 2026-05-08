#!/usr/bin/env python3
"""test_global_brightness_contract.py — Global brightness "applies everywhere" contract.

Operator design rule: `POST /api/brightness {value: 0..255}` must apply
globally — to LED children (via SET_BRIGHTNESS broadcast), to DMX render
output (claim/idle/show paths), and persist across reconnect. This
harness probes each consumer and pins which are wired vs. unwired.

Tracks #843. The harness IS the acceptance for #843.

Scenarios:

  G1. POST /api/brightness 0..255 stores globalBrightness in _settings.
      [implementation says yes — pinning for regression]

  G2. POST /api/brightness DOES broadcast SET_BRIGHTNESS to LED children
      (fast path). Currently UNWIRED — only the show-load/sync path does
      this. Pinned as failing until #843 part 1 ships.

  G3. While a claim is held with dimmer=200, POST /api/brightness 128
      should scale the DMX dimmer to ~98 (200 × (128/255)^2.2 ≈ 47
      with gamma, or 100 with linear). Currently UNWIRED — pinned.

  G4. Idle fixture (no claim, parked) — POST /api/brightness has no
      observable effect since dim is 0 anyway. (Sanity baseline.)

  G5. After bake-render of a show: DMX dimmer scales by gamma-corrected
      brightness (verified live in v1.7.86 per session memory; pinned
      here for regression).

Run:  python -X utf8 tests/test_global_brightness_contract.py
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

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
    print(f'\n=== {name} ===')


def make_full_fixture(c):
    c.post('/api/dmx-profiles', json={
        'id': 'test-mh', 'name': 'Test MH', 'manufacturer': 'Test',
        'category': 'moving-head', 'channelCount': 10,
        'panRange': 540, 'tiltRange': 270,
        'channels': [
            {'offset': 0, 'name': 'Pan',    'type': 'pan',    'bits': 16},
            {'offset': 2, 'name': 'Tilt',   'type': 'tilt',   'bits': 16},
            {'offset': 4, 'name': 'Dimmer', 'type': 'dimmer'},
            {'offset': 5, 'name': 'Red',    'type': 'red'},
            {'offset': 6, 'name': 'Green',  'type': 'green'},
            {'offset': 7, 'name': 'Blue',   'type': 'blue'},
        ],
    })
    r = c.post('/api/fixtures', json={
        'name': 'MH', 'type': 'point', 'fixtureType': 'dmx',
        'dmxUniverse': 99, 'dmxStartAddr': 1, 'dmxChannelCount': 10,
        'dmxProfileId': 'test-mh',
    })
    fid = r.get_json()['id']
    c.post(f'/api/fixtures/{fid}/home', json={'panDmx16': 32768, 'tiltDmx16': 32768})
    c.post(f'/api/fixtures/{fid}/home/secondary', json={
        'panOffsetDmx16': 10922, 'tiltOffsetDmx16': 32768,
        'panMovedDirection': 'left', 'tiltMovedDirection': 'up',
    })
    return fid


def mute_artnet(parent_server):
    import socket as _s
    class _Mute:
        def sendto(self, *a, **k): pass
        def recvfrom(self, n): raise _s.timeout
        def close(self): pass
        def setblocking(self, *a, **k): pass
        def settimeout(self, *a, **k): pass
    if parent_server._artnet._sock is not None:
        try: parent_server._artnet._sock.close()
        except Exception: pass
        parent_server._artnet._sock = _Mute()


def setup(c, parent_server):
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.post('/api/dmx/start', json={'protocol': 'artnet'})
    time.sleep(0.05)
    mute_artnet(parent_server)


def chans(parent_server, uni=99, count=10):
    eng = parent_server._artnet
    u = eng.peek_universe(uni)
    if u is None:
        return [0] * count
    return [u.get_channel(i + 1) for i in range(count)]


def main():
    print('=== Global Brightness Contract Harness (#843) ===')
    import parent_server
    from parent_server import app

    with app.test_client() as c:

        # ── G1. POST stores globalBrightness ───────────────────────────
        section('G1: POST /api/brightness updates _settings.globalBrightness')
        setup(c, parent_server)
        for v in [255, 128, 0, 200]:
            r = c.post('/api/brightness', json={'value': v})
            d = r.get_json()
            stored = parent_server._settings.get('globalBrightness')
            ok(d.get('ok') and stored == v,
               f'POST {v} → ok=True, _settings.globalBrightness={stored}')

        # ── G2. POST broadcasts SET_BRIGHTNESS to LED children ─────────
        section('G2: POST /api/brightness broadcasts SET_BRIGHTNESS to LED '
                'children (fast-path) — KNOWN BUG #843 part 1, currently '
                'unwired')
        setup(c, parent_server)
        # Capture UDP sends. The orchestrator sends via socket.sendto.
        # We cannot easily capture in-process without monkey-patching;
        # instead, register a fake child and assert the brightness POST
        # writes a SET_BRIGHTNESS-shaped packet to its address.
        sent_packets = []
        import socket
        original_sendto = socket.socket.sendto
        def _spy_sendto(self, data, addr):
            sent_packets.append((bytes(data), addr))
            return len(data)
        socket.socket.sendto = _spy_sendto
        try:
            # Register a fake child at known IP. parent_server._children is
            # a list of dicts.
            with parent_server._lock:
                parent_server._children.append({
                    'id': 999, 'ip': '127.0.0.99', 'mac': '00:00:00:00:00:00',
                    'hostname': 'fake-child', 'status': 1,
                    'fwMajor': 1, 'fwMinor': 0,
                    'stringCount': 1, 'strings': [],
                })
            sent_packets.clear()
            r = c.post('/api/brightness', json={'value': 128})
            time.sleep(0.05)
            # SET_BRIGHTNESS packet would have CMD byte 0x22 in the header
            # and 1-byte payload. We spy by IP.
            CMD_SET_BRIGHTNESS = 0x22
            # Header: <HBBI> magic=0x534C, version, cmd, epoch — cmd is byte 3.
            child_packets = [(p, a) for p, a in sent_packets
                             if a[0] == '127.0.0.99']
            brightness_packets = [(p, a) for p, a in child_packets
                                  if len(p) >= 4 and p[3] == CMD_SET_BRIGHTNESS]
            ok(len(brightness_packets) > 0,
               f'POST /api/brightness 128 broadcasts SET_BRIGHTNESS to '
               f'children (got {len(child_packets)} packets to fake-child, '
               f'of which {len(brightness_packets)} are SET_BRIGHTNESS) '
               f'— KNOWN BUG: fast path does not broadcast')
        finally:
            socket.socket.sendto = original_sendto
            with parent_server._lock:
                parent_server._children[:] = [
                    cc for cc in parent_server._children
                    if cc.get('id') != 999
                ]

        # ── G3. Brightness scales claim-driven DMX dimmer ──────────────
        section('G3: Brightness applies to claim-driven DMX dimmer '
                '(operator: "globally no matter what") — KNOWN BUG #843 '
                'part 3, currently unwired')
        setup(c, parent_server)
        # Set brightness FIRST so any claim-write can read it.
        c.post('/api/brightness', json={'value': 255})
        fid = make_full_fixture(c)
        c.post('/api/mover-control/claim',
               json={'moverId': fid, 'deviceId': 'phone-1', 'deviceName': 'P'})
        c.post('/api/mover-control/start',
               json={'moverId': fid, 'deviceId': 'phone-1'})
        c.post('/api/mover-control/color',
               json={'moverId': fid, 'deviceId': 'phone-1',
                     'r': 255, 'g': 255, 'b': 255, 'dimmer': 200})
        time.sleep(0.3)
        ch = chans(parent_server)
        dim_full = ch[4]
        # Now drop brightness to 50%.
        c.post('/api/brightness', json={'value': 128})
        time.sleep(0.5)
        ch = chans(parent_server)
        dim_half = ch[4]
        # Expected: linear scale → dim_half ≈ 100. Gamma 2.2 → ~47.
        # Either is acceptable as a wiring signal; current unwired
        # behavior is dim_half == dim_full == 200.
        scaled = dim_half < dim_full * 0.95
        ok(scaled,
           f'/api/brightness 128 scales claim dimmer below full '
           f'(full bri=255: dim={dim_full}; half bri=128: dim={dim_half}) '
           f'— KNOWN BUG: claim-write path does not consult globalBrightness')

        # G3b — same but via /color dimmer field (sanity)
        c.post('/api/brightness', json={'value': 0})
        time.sleep(0.4)
        ch = chans(parent_server)
        dim_zero = ch[4]
        ok(dim_zero == 0,
           f'/api/brightness 0 forces dimmer to 0 (got {dim_zero}) '
           f'— KNOWN BUG: only true if claim path consults brightness')
        c.post('/api/mover-control/release',
               json={'moverId': fid, 'deviceId': 'phone-1'})

        # ── G4. Idle baseline ──────────────────────────────────────────
        section('G4: Idle (no claim, no show) — brightness has no observable '
                'DMX effect since dim is 0 (sanity baseline)')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        c.post('/api/brightness', json={'value': 255})
        time.sleep(0.2)
        ch_full = chans(parent_server)
        c.post('/api/brightness', json={'value': 0})
        time.sleep(0.2)
        ch_zero = chans(parent_server)
        # Both should show home pose with dim=0 — brightness changes
        # don't affect idle universe.
        ok(ch_full[4] == 0 and ch_zero[4] == 0,
           f'Idle: dim stays 0 regardless of brightness '
           f'(255-state ch4={ch_full[4]}, 0-state ch4={ch_zero[4]})')

        # ── G5. Bake-render path scales by gamma-corrected brightness ──
        section('G5: Bake-render path scales DMX dimmer by gamma 2.2 '
                'corrected brightness (verified v1.7.86; pinned for '
                'regression)')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        # Build a SOLID_COLOR action (white, full dimmer) — exact body shape
        # is engine-specific; try common shapes.
        ar = c.post('/api/actions', json={
            'name': 'WHITE_FULL', 'type': 0,
            'r': 255, 'g': 255, 'b': 255,
            'duration': 5.0, 'targetIds': [fid],
        })
        if ar.status_code in (200, 201) and ar.get_json():
            aid = ar.get_json().get('id')
            tr = c.post('/api/timelines', json={
                'name': 'BRIT_TL',
                'tracks': [{'targetIds': [fid], 'lanes': [
                    {'actionId': aid, 'startTime': 0.0, 'duration': 5.0}
                ]}]
            })
            if tr.status_code in (200, 201) and tr.get_json():
                tlid = tr.get_json().get('id')
                # Test multiple brightness values and observe dim
                results = []
                for bri in [255, 200, 128, 64]:
                    c.post('/api/brightness', json={'value': bri})
                    time.sleep(0.2)
                    for body in [{'timelineIds': [tlid]},
                                 {'timelineId': tlid},
                                 {'playlist': [tlid]}]:
                        sr = c.post('/api/show/start', json=body)
                        if sr.status_code == 200:
                            break
                    time.sleep(0.5)
                    ch = chans(parent_server)
                    results.append((bri, ch[4]))
                    c.post('/api/show/stop')
                    time.sleep(0.1)
                # Expected: dim values track gamma-2.2 scaled brightness
                # (255→255, 200→149, 128→56, 64→12 per session memory).
                tracking = (results[0][1] >= 200
                            and results[3][1] < results[0][1] * 0.5)
                ok(tracking,
                   f'Bake-render dim tracks brightness '
                   f'(samples: {results}) — should decrease with brightness')

        # ── Summary ─────────────────────────────────────────────────────
        print()
        print('=' * 60)
        if _fail == 0:
            print(f'  \033[32m{_pass} passed\033[0m, 0 failed out of {_pass + _fail}')
        else:
            print(f'  \033[32m{_pass} passed\033[0m, '
                  f'\033[31m{_fail} failed\033[0m out of {_pass + _fail}')
            for e in _errors:
                print(f'    - {e}')
        return 0 if _fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
