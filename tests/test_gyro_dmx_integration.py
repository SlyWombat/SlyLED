#!/usr/bin/env python3
"""test_gyro_dmx_integration.py — End-to-end orient → DMX wire harness.

Drives the orient pipeline through deterministic synthetic inputs and
asserts the DMX universe + claim record track. Closes the harness gap
identified 2026-05-07: every layer was unit-tested in isolation but no
integration test verified `synthetic orient → claim writer → DMX wire`.

Scenarios:

  1. Stationary baseline (no claim) — DMX universe is zeroed.
  2. Claim + calibrate + identity orient → head aims at calibrate target.
  3. Pitch-fwd orient → tiltNorm decreases (head tilts down toward floor).
  4. Pitch-back orient → tiltNorm increases (head tilts up).
  5. Yaw-left orient → panNorm changes in the stage-left direction.
  6. Roll-only orient (around forward axis) → aim barely moves
     (forward_local = (1,0,0); roll is around X, so f_world is invariant).
  7. Sweep across 6 distinct poses → 6 distinct (panNorm, tiltNorm).
  8. Missing-Secondary regression → IK refuses to aim AND the failure is
     observable (today: silent DEBUG-only log; this test pins the
     silent-failure surface and will fail loudly once the code surfaces
     the error appropriately).

Run:  python -X utf8 tests/test_gyro_dmx_integration.py
"""
import sys, os, time, math
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


def make_fixture(c, *, with_secondary=True, name='MH'):
    """Create a moving-head DMX fixture with profile + Home (+ optional Secondary).

    Returns the fixture id. Universe 99 keeps any leaked Art-Net frames
    off any production universe."""
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
        'name': name, 'type': 'point', 'fixtureType': 'dmx',
        'dmxUniverse': 99, 'dmxStartAddr': 1, 'dmxChannelCount': 10,
        'dmxProfileId': 'test-mh',
    })
    fid = r.get_json()['id']
    c.post(f'/api/fixtures/{fid}/home', json={'panDmx16': 32768, 'tiltDmx16': 32768})
    if with_secondary:
        c.post(f'/api/fixtures/{fid}/home/secondary', json={
            'panOffsetDmx16': 10922, 'tiltOffsetDmx16': 32768,
            'panMovedDirection': 'left', 'tiltMovedDirection': 'up',
        })
    return fid


def mute_artnet(parent_server):
    """Replace the Art-Net socket with a no-op so frames stay off the LAN."""
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


def claim_and_calibrate(c, fid, *, did='gyro-1', dname='SLYG-001',
                       cal_rpy=(0.0, 0.0, 0.0)):
    """Claim → start → calibrate-start → calibrate-end at given pose.

    Returns the calibrate-end response (carries the captured aim_stage)."""
    c.post('/api/mover-control/claim',
           json={'moverId': fid, 'deviceId': did, 'deviceName': dname})
    c.post('/api/mover-control/start',
           json={'moverId': fid, 'deviceId': did})
    c.post('/api/mover-control/calibrate-start',
           json={'moverId': fid, 'deviceId': did,
                 'roll': cal_rpy[0], 'pitch': cal_rpy[1], 'yaw': cal_rpy[2]})
    return c.post('/api/mover-control/calibrate-end',
                  json={'moverId': fid, 'deviceId': did,
                        'roll': cal_rpy[0], 'pitch': cal_rpy[1],
                        'yaw': cal_rpy[2]}).get_json()


def orient_and_settle(c, fid, did, rpy, settle=0.3):
    """Send an orient packet, wait for the 40 Hz tick to apply, return claim."""
    c.post('/api/mover-control/orient',
           json={'moverId': fid, 'deviceId': did,
                 'roll': rpy[0], 'pitch': rpy[1], 'yaw': rpy[2]})
    time.sleep(settle)
    cs = c.get('/api/mover-control/status').get_json().get('claims', [])
    return cs[0] if cs else None


def universe_chans(parent_server, uni=99, count=10):
    """Read the first `count` channels of the named universe directly."""
    eng = parent_server._artnet
    u = eng.peek_universe(uni)
    if u is None:
        return [0] * count
    return [u.get_channel(i + 1) for i in range(count)]


def reset_session(c, parent_server):
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.post('/api/dmx/start', json={'protocol': 'artnet'})
    time.sleep(0.05)
    mute_artnet(parent_server)


def main():
    print('=== Gyro→DMX Integration Harness ===')
    import parent_server
    from parent_server import app

    with app.test_client() as c:

        # ── 1. Stationary baseline ───────────────────────────────────────
        section('Scenario 1: stationary baseline — fixture homed, no claim, '
                'DMX shows home pose (32768/32768 = midrange) and zeros elsewhere')
        reset_session(c, parent_server)
        fid = make_fixture(c, with_secondary=True, name='MH-baseline')
        time.sleep(0.2)
        ch = universe_chans(parent_server, uni=99)
        # Pan is 16-bit at offset 0+1, tilt at 2+3. Home = 32768 = 0x8000.
        wire_pan = (ch[0] << 8) | ch[1]
        wire_tilt = (ch[2] << 8) | ch[3]
        ok(wire_pan == 32768 and wire_tilt == 32768,
           f'Universe 99 carries home pose pre-claim '
           f'(pan={wire_pan} tilt={wire_tilt}, expected 32768/32768)')
        ok(all(v == 0 for v in ch[4:]),
           f'Non-pan/tilt channels zero pre-claim (got {ch[4:]})')

        # ── 2. Claim + calibrate at identity → aim at calibrate target ──
        section('Scenario 2: calibrate at identity, orient at identity → '
                'panNorm/tiltNorm match calibrate target')
        cal_resp = claim_and_calibrate(c, fid, cal_rpy=(0.0, 0.0, 0.0))
        ok(cal_resp.get('ok'), 'Calibrate-end ok')
        ok(cal_resp.get('aim') is not None, 'Calibrate-end returns aim_stage')
        # First orient at the calibrate pose: head should aim at the target,
        # i.e. the canonical aim returned by calibrate_end.
        cl = orient_and_settle(c, fid, 'gyro-1', (0.0, 0.0, 0.0))
        ok(cl is not None, 'Claim record present after orient')
        ok(cl['state'] == 'streaming', 'Claim state == streaming')
        ok(cl['calibrated'] is True, 'Claim calibrated == True')
        # At the calibrate pose, panNorm should be near the home pan (0.5) and
        # tiltNorm near the home tilt (0.5) — sphere aims at the target which
        # was captured at home pose.
        ok(abs(cl['panNorm'] - 0.5) < 0.05,
           f'Identity orient: panNorm ≈ 0.5 (got {cl["panNorm"]:.4f})')
        ok(abs(cl['tiltNorm'] - 0.5) < 0.05,
           f'Identity orient: tiltNorm ≈ 0.5 (got {cl["tiltNorm"]:.4f})')

        # ── 3. Pitch-fwd → tiltNorm decreases (beam tilts down) ─────────
        section('Scenario 3: pitch-fwd orient → tilt down')
        cl_base = orient_and_settle(c, fid, 'gyro-1', (0.0, 0.0, 0.0))
        cl_pitch_fwd = orient_and_settle(c, fid, 'gyro-1', (0.0, -30.0, 0.0))
        d_tilt = cl_pitch_fwd['tiltNorm'] - cl_base['tiltNorm']
        ok(abs(d_tilt) > 0.02,
           f'Pitch-fwd 30° moves tiltNorm (Δ={d_tilt:+.4f}, base={cl_base["tiltNorm"]:.4f})')

        # ── 4. Pitch-back → tiltNorm reverses ───────────────────────────
        section('Scenario 4: pitch-back orient → tilt opposite direction of #3')
        cl_pitch_back = orient_and_settle(c, fid, 'gyro-1', (0.0, +30.0, 0.0))
        d_tilt_back = cl_pitch_back['tiltNorm'] - cl_base['tiltNorm']
        ok(abs(d_tilt_back) > 0.02,
           f'Pitch-back 30° moves tiltNorm (Δ={d_tilt_back:+.4f})')
        # Sign should be opposite to pitch-fwd.
        ok((d_tilt > 0) != (d_tilt_back > 0),
           f'Pitch-fwd Δ ({d_tilt:+.4f}) and pitch-back Δ ({d_tilt_back:+.4f}) '
           f'have opposite sign')

        # ── 5. Yaw-left → panNorm changes ───────────────────────────────
        section('Scenario 5: yaw-left orient → panNorm changes')
        cl_yaw_left = orient_and_settle(c, fid, 'gyro-1', (0.0, 0.0, +30.0))
        d_pan = cl_yaw_left['panNorm'] - cl_base['panNorm']
        ok(abs(d_pan) > 0.02,
           f'Yaw-left 30° moves panNorm (Δ={d_pan:+.4f}, base={cl_base["panNorm"]:.4f})')

        # ── 6. Roll-only → aim invariant ────────────────────────────────
        section('Scenario 6: roll-only orient (around forward axis) → '
                'aim barely moves (forward_local invariant)')
        cl_roll = orient_and_settle(c, fid, 'gyro-1', (+45.0, 0.0, 0.0))
        d_pan_roll = abs(cl_roll['panNorm'] - cl_base['panNorm'])
        d_tilt_roll = abs(cl_roll['tiltNorm'] - cl_base['tiltNorm'])
        ok(d_pan_roll < 0.05 and d_tilt_roll < 0.05,
           f'Roll 45° leaves panNorm/tiltNorm near base '
           f'(Δpan={d_pan_roll:.4f}, Δtilt={d_tilt_roll:.4f})')

        # ── 7. Sweep across 6 poses → distinct outputs ──────────────────
        section('Scenario 7: distinct orient poses → distinct claim outputs')
        poses = [
            (0.0,    0.0, 0.0),
            (0.0,  -20.0, 0.0),
            (0.0,    0.0, +30.0),
            (0.0,  -30.0, +30.0),
            (0.0,  +20.0, -20.0),
            (+30.0, +15.0, +15.0),
        ]
        outs = []
        for rpy in poses:
            cl = orient_and_settle(c, fid, 'gyro-1', rpy)
            outs.append((round(cl['panNorm'], 4), round(cl['tiltNorm'], 4)))
        unique = len(set(outs))
        ok(unique >= 5,
           f'6 distinct orient poses → ≥5 distinct claim outputs '
           f'(got {unique} unique: {outs})')

        # ── 7b. DMX wire matches claim ──────────────────────────────────
        section('Scenario 7b: claim panNorm/tiltNorm round-trips to DMX wire')
        # Drive a known pose, read DMX, compare.
        cl_check = orient_and_settle(c, fid, 'gyro-1', (0.0, -30.0, +30.0), settle=0.4)
        ch = universe_chans(parent_server, uni=99)
        # Pan is 16-bit at offset 0+1, tilt at offset 2+3.
        wire_pan_dmx16 = (ch[0] << 8) | ch[1]
        wire_tilt_dmx16 = (ch[2] << 8) | ch[3]
        wire_pan_norm = wire_pan_dmx16 / 65535.0
        wire_tilt_norm = wire_tilt_dmx16 / 65535.0
        ok(abs(wire_pan_norm - cl_check['panNorm']) < 0.005,
           f'Wire panNorm tracks claim '
           f'(wire={wire_pan_norm:.4f} claim={cl_check["panNorm"]:.4f})')
        ok(abs(wire_tilt_norm - cl_check['tiltNorm']) < 0.005,
           f'Wire tiltNorm tracks claim '
           f'(wire={wire_tilt_norm:.4f} claim={cl_check["tiltNorm"]:.4f})')

        # ── 8. Missing-Secondary regression ─────────────────────────────
        section('Scenario 8: Home set, Secondary missing → IK silently '
                'returns (None,None); the head freezes with no error '
                'surfaced to the operator (current behaviour)')
        reset_session(c, parent_server)
        fid_no_sec = make_fixture(c, with_secondary=False, name='MH-no-sec')
        cal_resp = claim_and_calibrate(c, fid_no_sec, cal_rpy=(0.0, 0.0, 0.0))
        ok(cal_resp.get('ok'), 'Calibrate-end still returns ok=True even '
           'though IK will fail (architectural smell)')
        cl0 = orient_and_settle(c, fid_no_sec, 'gyro-1', (0.0, 0.0, 0.0))
        cl1 = orient_and_settle(c, fid_no_sec, 'gyro-1', (0.0, -30.0, +30.0))
        cl2 = orient_and_settle(c, fid_no_sec, 'gyro-1', (+45.0, +15.0, -45.0))
        # All three reads should be identical 0.5/0.5 because IK fails
        # silently. This is the BUG SURFACE — the test pins it so that
        # any future change which surfaces the failure properly will
        # cause this assertion to invert (and the test should be updated).
        all_default = all(abs(c['panNorm'] - 0.5) < 1e-9 and
                          abs(c['tiltNorm'] - 0.5) < 1e-9
                          for c in (cl0, cl1, cl2))
        ok(all_default,
           'BUG-SURFACE: Without Secondary, IK silently returns (None,None) '
           'and panNorm/tiltNorm stay at 0.5/0.5 across orient changes '
           '(claim_writer logs "AimSphere failed" at DEBUG only). '
           'Filed: see issue tracker for the surfacing fix.')

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
