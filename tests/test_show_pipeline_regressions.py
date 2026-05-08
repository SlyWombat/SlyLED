#!/usr/bin/env python3
"""test_show_pipeline_regressions.py — regression pinning for closed
show-pipeline issues.

Pins fixes for #835 (orphan Track action blackout) and #840 (loop
blackout between iterations) so any future regression surfaces in CI.

NOTE (2026-05-08): action shape needs follow-up. Type 1 (Solid LED)
auto-promotes to ACT_DMX_SCENE (14) for DMX fixtures via bake_engine
line 485, but the resulting baked frames produce rainbow-cycling output
in this harness rather than steady RGB. Likely the auto-promote path
needs an explicit DMX_SCENE channel-value structure that this harness
doesn't supply (`dimmer`, `pan`, `tilt`, `red`/`green`/`blue` inside the
action body shape used by ACT_DMX_SCENE specifically). Until the
correct DMX-targeting action body is documented and used here, the
#835 + #840 assertions only verify "show start succeeds + show_running
becomes True" — they don't pin the colour-correctness aspects of the
fixes. File a follow-up to extend this harness with proper DMX_SCENE
actions (probably needs `_ACTION_FIELDS` for type 14: dmx-channel
mapping per fixture rather than r/g/b/dimmer at the action level).

Run:  python -X utf8 tests/test_show_pipeline_regressions.py
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


def make_mover(c, name='MH'):
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
    print('=== Show-Pipeline Regressions Harness ===')
    import parent_server
    from parent_server import app

    with app.test_client() as c:

        # ── Regression #835: orphan Track action must not blackout ─────
        # An unrelated Track action sitting in _actions should NOT zero
        # movers in a different running timeline. Prior to fix, the
        # orphan Track action's per-fixture sweeper zeroed every mover's
        # dimmer on every tick.
        section('#835: orphan Track action does NOT blackout movers in '
                'an unrelated running timeline')
        setup(c, parent_server)
        fid = make_mover(c, 'MH-target')
        # Create a SOLID Red action and a timeline that uses it.
        ar = c.post('/api/actions', json={
            'name': 'RED_SOLID', 'type': 1,        # Solid
            'r': 255, 'g': 0, 'b': 0, 'dimmer': 200,
            'targetIds': [fid],
        })
        red_id = ar.get_json().get('id')
        ok(red_id is not None, 'Red Solid action created')
        # Create the orphan Track action (NOT placed in any timeline).
        tr = c.post('/api/actions', json={
            'name': 'ORPHAN_TRACK', 'type': 18,    # Track
            'targetIds': [fid],
            'trackObjectIds': [],
            'trackFixtureIds': [fid],
            'trackCycleMs': 4000,
            'trackDimmer': 255,
        })
        orphan_id = tr.get_json().get('id')
        ok(orphan_id is not None, 'Orphan Track action created')
        # Build a timeline that references ONLY the red action.
        tl = c.post('/api/timelines', json={
            'name': 'RED_TL',
            'tracks': [{'targetIds': [fid], 'clips': [
                {'actionId': red_id, 'startTime': 0.0, 'duration': 5.0}
            ]}]
        })
        tlid = tl.get_json().get('id')
        ok(tlid is not None, 'Red timeline created')
        # Bake the timeline (async — poll until items show baked=true).
        c.post(f'/api/timelines/{tlid}/bake')
        for _ in range(30):
            time.sleep(0.1)
            st = c.get('/api/show/status').get_json()
            it = next((i for i in st.get('items', []) if i.get('id') == tlid), None)
            if it and it.get('baked'):
                break
        # Set playlist.
        c.post('/api/show/playlist', json={'order': [tlid], 'loopAll': False})
        sr = c.post('/api/show/start', json={'order': [tlid]})
        ok(sr.status_code == 200,
           f'Show started (status={sr.status_code}, body={sr.get_json()})')
        # /api/show/start sets go_epoch = now + 2s — wait past that.
        time.sleep(2.8)
        ch = chans(parent_server)
        red_lit = ch[5] >= 200 and ch[6] < 50 and ch[7] < 50
        dim_lit = ch[4] > 0
        ok(red_lit,
           f'Red dominates fixture (R={ch[5]}, G={ch[6]}, B={ch[7]}) — '
           f'orphan Track action does NOT blackout the mover (#835)')
        ok(dim_lit, f'Dimmer lit (got {ch[4]}) — no orphan-Track zero-sweep')
        c.post('/api/show/stop')
        time.sleep(0.2)

        # ── Regression #840: loop blackout between iterations ──────────
        # When a single-timeline playlist loops, _dmx_playback_loop must
        # NOT zero the universe between iterations. Pre-fix, the wrap
        # caused a visible blackout flicker every cycle.
        section('#840: looping playlist does NOT blackout between '
                'iterations (no zero frames at wrap)')
        setup(c, parent_server)
        fid = make_mover(c, 'MH-loop')
        ar = c.post('/api/actions', json={
            'name': 'GREEN_SOLID', 'type': 1,
            'r': 0, 'g': 255, 'b': 0, 'dimmer': 200,
            'targetIds': [fid],
        })
        green_id = ar.get_json().get('id')
        # Short timeline (0.5s) so we wrap multiple times in our sample window.
        tl = c.post('/api/timelines', json={
            'name': 'GREEN_LOOP',
            'tracks': [{'targetIds': [fid], 'clips': [
                {'actionId': green_id, 'startTime': 0.0, 'duration': 0.5}
            ]}]
        })
        tlid = tl.get_json().get('id')
        c.post(f'/api/timelines/{tlid}/bake')
        for _ in range(30):
            time.sleep(0.1)
            st = c.get('/api/show/status').get_json()
            it = next((i for i in st.get('items', []) if i.get('id') == tlid), None)
            if it and it.get('baked'):
                break
        c.post('/api/show/playlist', json={'order': [tlid], 'loopAll': True})
        sr = c.post('/api/show/start', json={'order': [tlid], 'loopAll': True})
        ok(sr.status_code == 200, 'Loop show started')
        # Wait past go_epoch (now + 2s).
        time.sleep(2.2)
        # Sample for 2.5s (5 wraps at 0.5s each). Capture every 0.05s.
        # Count ANY frame where dimmer == 0 — should be zero such frames.
        zero_frames = 0
        green_frames = 0
        total = 0
        end = time.time() + 2.5
        while time.time() < end:
            ch = chans(parent_server)
            total += 1
            if ch[4] == 0 and ch[6] == 0:
                zero_frames += 1
            if ch[6] >= 200:
                green_frames += 1
            time.sleep(0.05)
        ok(zero_frames == 0,
           f'No zero frames during 2.5s of looping (zero={zero_frames}/'
           f'{total}, green={green_frames}/{total}) — #840 wrap-blackout '
           f'fix holds')
        ok(green_frames > 20,
           f'Green dominates ({green_frames}/{total} frames) — show is '
           f'actually running and writing the action')
        c.post('/api/show/stop')

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
