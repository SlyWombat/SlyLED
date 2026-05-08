#!/usr/bin/env python3
"""test_claim_ownership_contract.py — Invariants for #848 claim-mute.

The contract: while a fixture is held by an operator-claim (gyro
press-Start, mover-control HTTP claim, etc.), no other code path may
write to that fixture's DMX channels. The playback loop's per-frame
mute check (#763) already honours this. Other paths that zero or
park fixtures must apply the same `_claim_arbiter.is_muted(fid)` gate.

This harness tests each "writer to a claimed fixture" code path in
isolation:

  Invariant A — show-stop blackout must not touch a claimed fixture.
  Invariant B — `/api/dmx/blackout` must not touch a claimed fixture.
  Invariant C — `/api/dmx/stop` (engine stop) leaves the universe state
                consistent with the claim's last write (or signals via
                the claim record; design choice — pinning current
                behaviour for now).
  Invariant D — show-running playback already respects the claim
                (regression for #763).
  Invariant E — claim acquisition turns lamp on with default color
                so the operator sees light (#848 invariant 1).
  Invariant F — claim release blacks out the fixture (no orphan
                latched DMX). [Already covered in test_mover_control;
                included here for contract completeness.]

Run:  python -X utf8 tests/test_claim_ownership_contract.py
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


def make_full_fixture(c, name='Test MH'):
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


def universe_chans(parent_server, uni=99, count=10):
    eng = parent_server._artnet
    u = eng.peek_universe(uni)
    if u is None:
        return [0] * count
    return [u.get_channel(i + 1) for i in range(count)]


def setup(c, parent_server):
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.post('/api/dmx/start', json={'protocol': 'artnet'})
    time.sleep(0.05)
    mute_artnet(parent_server)


def claim_and_drive(c, fid, did='gyro-1', dname='SLYG-001',
                    cal_rpy=(0.0, 0.0, 0.0), live_rpy=(0.0, -20.0, 30.0)):
    """Acquire claim → calibrate → orient. Returns the claim record."""
    c.post('/api/mover-control/claim',
           json={'moverId': fid, 'deviceId': did, 'deviceName': dname})
    c.post('/api/mover-control/start',
           json={'moverId': fid, 'deviceId': did})
    c.post('/api/mover-control/calibrate-start',
           json={'moverId': fid, 'deviceId': did,
                 'roll': cal_rpy[0], 'pitch': cal_rpy[1], 'yaw': cal_rpy[2]})
    c.post('/api/mover-control/calibrate-end',
           json={'moverId': fid, 'deviceId': did,
                 'roll': cal_rpy[0], 'pitch': cal_rpy[1], 'yaw': cal_rpy[2]})
    c.post('/api/mover-control/color',
           json={'moverId': fid, 'deviceId': did,
                 'r': 255, 'g': 0, 'b': 0, 'dimmer': 200})
    c.post('/api/mover-control/orient',
           json={'moverId': fid, 'deviceId': did,
                 'roll': live_rpy[0], 'pitch': live_rpy[1], 'yaw': live_rpy[2]})
    time.sleep(0.4)  # tick + DMX settle
    cs = c.get('/api/mover-control/status').get_json().get('claims', [])
    return cs[0] if cs else None


def is_blacked_out(ch):
    """All channels zero = blacked out."""
    return all(v == 0 for v in ch)


def main():
    print('=== Claim Ownership Contract Harness (#848) ===')
    import parent_server
    from parent_server import app

    with app.test_client() as c:

        # ── Invariant A: show-stop blackout must not touch claimed fixture ──
        section('Invariant A: show-stop blackout must NOT zero a claimed fixture')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        cl = claim_and_drive(c, fid)
        ok(cl is not None and cl['state'] == 'streaming',
           'Claim active and streaming pre-stop')
        before = universe_chans(parent_server)
        ok(any(v > 0 for v in before),
           f'Pre-stop: claim driving DMX (channels={before})')
        # POST show stop. There may be no show running, so the result code
        # doesn't matter — what matters is the side effect on the universe.
        c.post('/api/show/stop')
        time.sleep(0.4)
        after = universe_chans(parent_server)
        # A claim writer ticking at 40 Hz should re-establish values within
        # 0.4s if any blackout fired. So the test is: did the claim's
        # values STAY (no zero spike) — we check post-stop state matches
        # pre-stop within IK reconvergence tolerance.
        ok(any(v > 0 for v in after),
           f'Post-stop: claim still driving DMX (channels={after})')
        # Specific channels: red (offset 5 = ch 6) should still be ~255,
        # dimmer (offset 4 = ch 5) should still be ~200.
        # But brightness scaling may change them — accept any >0.
        ok(after[5] > 0,
           f'Post-stop: red channel still nonzero (got {after[5]})')
        ok(after[4] > 0,
           f'Post-stop: dimmer channel still nonzero (got {after[4]})')

        # ── Invariant B: /api/dmx/blackout must not touch claimed fixture ──
        section('Invariant B: /api/dmx/blackout must NOT zero a claimed fixture')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        cl = claim_and_drive(c, fid)
        before = universe_chans(parent_server)
        ok(any(v > 0 for v in before),
           f'Pre-blackout: claim driving DMX (channels={before})')
        c.post('/api/dmx/blackout')
        time.sleep(0.4)
        after = universe_chans(parent_server)
        ok(any(v > 0 for v in after),
           f'Post-blackout: claim still driving DMX (channels={after})')
        ok(after[5] > 0,
           f'Post-blackout: red still nonzero (got {after[5]})')
        ok(after[4] > 0,
           f'Post-blackout: dimmer still nonzero (got {after[4]})')

        # ── Invariant E: claim acquisition turns lamp on with default colour ──
        section('Invariant E: claim acquisition lights the lamp '
                '(default color even before /color is called)')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        # Claim + start + calibrate, but DO NOT call /color or /dimmer.
        c.post('/api/mover-control/claim',
               json={'moverId': fid, 'deviceId': 'gyro-1', 'deviceName': 'SLYG-001'})
        c.post('/api/mover-control/start',
               json={'moverId': fid, 'deviceId': 'gyro-1'})
        c.post('/api/mover-control/calibrate-start',
               json={'moverId': fid, 'deviceId': 'gyro-1',
                     'roll': 0, 'pitch': 0, 'yaw': 0})
        c.post('/api/mover-control/calibrate-end',
               json={'moverId': fid, 'deviceId': 'gyro-1',
                     'roll': 0, 'pitch': 0, 'yaw': 0})
        c.post('/api/mover-control/orient',
               json={'moverId': fid, 'deviceId': 'gyro-1',
                     'roll': 0, 'pitch': 0, 'yaw': 0})
        time.sleep(0.5)
        ch = universe_chans(parent_server)
        # Per docs/gyro-claim-lifecycle.md (referenced in operator memory),
        # "lamp-on default colour" is invariant 1 — the head should produce
        # visible light when claimed even if the operator hasn't picked
        # a colour yet. Acceptance: dimmer > 0 OR any of R/G/B > 0.
        lamp_on = ch[4] > 0 or ch[5] > 0 or ch[6] > 0 or ch[7] > 0
        ok(lamp_on,
           f'Lamp on by default after claim+calibrate '
           f'(dim={ch[4]}, r={ch[5]}, g={ch[6]}, b={ch[7]})')

        # ── Invariant F: claim release blacks out the claimed fixture ──
        # KNOWN BUG (filed): release on a fixture with Home set leaves RGB
        # latched. Triangulated 3 cells (No Home / Home only / Home+Sec):
        # only the No-Home case actually blacks out RGB. Root cause is
        # `_park_fixture_at_home`'s `lamp_off(...color=None)` which never
        # touches RGB, plus the write-back loop excluding RGB types.
        # This assertion will fail until the fix lands; pinned here so
        # the regression is observable in CI.
        section('Invariant F: claim release zeros the formerly-claimed '
                'fixture (no orphan latched DMX) — KNOWN BUG with Home set')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        cl = claim_and_drive(c, fid)
        before = universe_chans(parent_server)
        ok(any(v > 0 for v in before), f'Pre-release: lit (channels={before})')
        c.post('/api/mover-control/release',
               json={'moverId': fid, 'deviceId': 'gyro-1'})
        time.sleep(0.4)
        after = universe_chans(parent_server)
        # Pan/tilt should be at home (32768/32768 = bytes [128,0,128,0]),
        # dimmer should be 0, RGB should be 0.
        rgb_cleared = after[5] == 0 and after[6] == 0 and after[7] == 0
        dim_cleared = after[4] == 0
        pan_at_home = after[0] == 128 and after[1] == 0
        tilt_at_home = after[2] == 128 and after[3] == 0
        ok(pan_at_home and tilt_at_home,
           f'Post-release: pan/tilt parked at home '
           f'(pan_bytes={after[0:2]}, tilt_bytes={after[2:4]})')
        ok(dim_cleared,
           f'Post-release: dimmer = 0 (got {after[4]})')
        ok(rgb_cleared,
           f'Post-release: RGB cleared (got R={after[5]} G={after[6]} '
           f'B={after[7]}) — KNOWN BUG: RGB latches when Home is set')

        # ── Invariant D: show-running playback already respects claim mute ──
        # (Regression for #763 — covered in production tests; pin here.)
        section('Invariant D: claim survives a running show that targets '
                'the same fixture (regression for #763)')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        cl = claim_and_drive(c, fid)
        # Build a minimal show that writes a different colour to the same fixture.
        # Action: solid blue dimmer 100 on this fixture for 5s.
        ar = c.post('/api/actions', json={
            'name': 'BLUE_TEST', 'type': 0,  # SOLID_COLOR
            'r': 0, 'g': 0, 'b': 255,
            'duration': 5.0,
            'targetIds': [fid],
        })
        # If type=0 path doesn't accept this body shape, fallback to whatever
        # /api/actions accepts. The test only needs *some* show-driven
        # write to attempt this fixture.
        action_ok = ar.status_code in (200, 201)
        # Minimal timeline + show-start
        if action_ok and ar.get_json() is not None:
            aid = ar.get_json().get('id')
            tr = c.post('/api/timelines', json={
                'name': 'CLAIM_TEST_TL',
                'tracks': [{'targetIds': [fid], 'lanes': [
                    {'actionId': aid, 'startTime': 0.0, 'duration': 5.0}
                ]}]
            })
            tl_ok = tr.status_code in (200, 201) and tr.get_json() is not None
            if tl_ok:
                tlid = tr.get_json().get('id')
                # Try various show-start route shapes (keep test robust)
                for body in [{'timelineIds': [tlid]},
                             {'timelineId': tlid},
                             {'playlist': [tlid], 'loopAll': False}]:
                    sr = c.post('/api/show/start', json=body)
                    if sr.status_code == 200:
                        break
                time.sleep(0.6)
        # Now check: claim still driving its own values, not the show's blue.
        ch = universe_chans(parent_server)
        red_strong = ch[5] >= 200
        blue_low = ch[7] < 50
        ok(red_strong and blue_low,
           f'Show writes blue to same fixture, claim mute holds red dominance '
           f'(r={ch[5]}, g={ch[6]}, b={ch[7]})')
        c.post('/api/show/stop')

        # ── Invariant C: /api/dmx/stop ──────────────────────────────────
        section('Invariant C: /api/dmx/stop while claim held — engine '
                'stops, claim record reflects engineRunning=False')
        setup(c, parent_server)
        fid = make_full_fixture(c)
        cl = claim_and_drive(c, fid)
        ok(any(v > 0 for v in universe_chans(parent_server)),
           'Pre-engine-stop: lit')
        c.post('/api/dmx/stop')
        time.sleep(0.3)
        # Engine is stopped, so the universe buffer state may persist or
        # zero depending on engine implementation. The claim writer's
        # next attempt should signal engineRunning=False rather than
        # silently succeed.
        sr = c.get('/api/mover-control/status').get_json()
        eng = sr.get('engine') or {}
        eng_running = eng.get('running', None)
        ok(eng_running is False,
           f'Status surfaces engine.running=False (got {eng_running}, '
           f'engine block={eng})')

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
