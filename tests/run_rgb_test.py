#!/usr/bin/env python3
"""
run_rgb_test.py — Create and run a 30-second DMX test show: 10s red, 10s blue, 10s green.

Creates a DMX fixture at U1 @1 (6ch, generic-rgb profile) if none exists,
builds a timeline with 3 solid-color clips, bakes it, and starts playback.

Usage:
    python tests/run_rgb_test.py
"""

import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app

def run():
    with app.test_client() as c:
        # 1. Check/create DMX fixture
        r = c.get('/api/fixtures')
        fixtures = r.get_json() or []
        # Look for an existing 6ch RGB fixture at U1 @1, or create one
        dmx_fix = None
        for f in fixtures:
            if (f.get('fixtureType') == 'dmx' and
                f.get('dmxUniverse') == 1 and f.get('dmxStartAddr') == 1 and
                f.get('dmxProfileId') == 'generic-rgb'):
                dmx_fix = f
                break

        if not dmx_fix:
            print('No DMX fixture found — creating one...')
            r = c.post('/api/fixtures', json={
                'name': 'RGB Test Light',
                'type': 'point',
                'fixtureType': 'dmx',
                'dmxUniverse': 1,
                'dmxStartAddr': 1,
                'dmxChannelCount': 6,
                'dmxProfileId': 'generic-rgb',
                'rotation': [0, 0, 0],
            })
            d = r.get_json()
            print(f"  Created fixture id={d.get('id')}")
            dmx_fix = c.get(f"/api/fixtures/{d['id']}").get_json()
        else:
            print(f"Using existing fixture: {dmx_fix['name']} (id={dmx_fix['id']}) U{dmx_fix.get('dmxUniverse')} @{dmx_fix.get('dmxStartAddr')}")

        fid = dmx_fix['id']

        # 2. Create 3 solid-color actions: Red, Blue, Green
        print('\nCreating actions...')
        action_ids = {}
        for name, r_v, g_v, b_v in [('Test Red', 255, 0, 0), ('Test Blue', 0, 0, 255), ('Test Green', 0, 255, 0)]:
            r = c.post('/api/actions', json={
                'name': name, 'type': 1,
                'r': r_v, 'g': g_v, 'b': b_v,
            })
            aid = r.get_json().get('id')
            action_ids[name] = aid
            print(f"  {name} -> action id={aid}")

        # 3. Create timeline: 30s, one track for the DMX fixture
        print('\nCreating timeline...')
        r = c.post('/api/timelines', json={
            'name': 'RGB Test (30s)',
            'durationS': 30,
            'loop': False,
            'tracks': [{
                'fixtureId': fid,
                'clips': [
                    {'actionId': action_ids['Test Red'],   'startS': 0,  'durationS': 10},
                    {'actionId': action_ids['Test Blue'],  'startS': 10, 'durationS': 10},
                    {'actionId': action_ids['Test Green'], 'startS': 20, 'durationS': 10},
                ],
            }],
        })
        tl_id = r.get_json().get('id')
        print(f"  Timeline id={tl_id}")

        # 4. Bake
        print('\nBaking...')
        r = c.post(f'/api/timelines/{tl_id}/bake', json={})
        if not r.get_json().get('ok'):
            print(f"  Bake failed: {r.get_json()}")
            return 1

        # Poll bake status
        for _ in range(30):
            time.sleep(0.5)
            r = c.get(f'/api/timelines/{tl_id}/baked/status')
            s = r.get_json()
            if s.get('done'):
                if s.get('error'):
                    print(f"  Bake error: {s['error']}")
                    return 1
                print(f"  Bake complete — segments: {s.get('segments', {})}")
                break
        else:
            print('  Bake timeout')
            return 1

        # 5. Check baked result
        r = c.get(f'/api/timelines/{tl_id}/baked')
        baked = r.get_json()
        fix_data = baked.get('fixtures', {}).get(str(fid), baked.get('fixtures', {}).get(fid, {}))
        segs = fix_data.get('segments', [])
        print(f"\n  Baked segments for fixture {fid}: {len(segs)}")
        for seg in segs:
            p = seg.get('params', {})
            print(f"    t={seg.get('startS')}s dur={seg.get('durationS')}s -> R={p.get('r')} G={p.get('g')} B={p.get('b')} pan={p.get('pan')} tilt={p.get('tilt')}")

        # 6. Check DMX settings
        r = c.get('/api/dmx/settings')
        ds = r.get_json()
        print(f"\n  DMX: protocol={ds.get('protocol')}, routes={ds.get('universeRoutes')}")

        print('\n' + '='*50)
        print('Ready to start. The show will run for 30 seconds:')
        print('  0-10s:  RED')
        print('  10-20s: BLUE')
        print('  20-30s: GREEN')
        print('='*50)

        print('\nStarting in 2 seconds...')

        # 7. Start (skip sync since no real children)
        # Directly start the DMX playback thread
        from parent_server import _dmx_playback_stop, _bake_result, _settings
        import threading

        _settings['runnerRunning'] = True
        _settings['activeTimeline'] = tl_id
        go_epoch = int(time.time()) + 2
        _settings['runnerStartEpoch'] = go_epoch
        _dmx_playback_stop.clear()

        from parent_server import _dmx_playback_loop
        t = threading.Thread(target=_dmx_playback_loop, args=(tl_id, go_epoch, 30, False), daemon=True)
        t.start()

        print(f'\nShow started! Go epoch in 2 seconds...')
        print('Watch the DMX light. Press Ctrl+C to stop early.\n')

        try:
            for sec in range(32):
                if sec < 2:
                    label = 'waiting...'
                elif sec < 12:
                    label = 'RED'
                elif sec < 22:
                    label = 'BLUE'
                elif sec < 32:
                    label = 'GREEN'
                print(f'  {sec}s — {label}', flush=True)
                time.sleep(1)
        except KeyboardInterrupt:
            print('\n  Interrupted')

        _dmx_playback_stop.set()
        print('\nShow ended. Cleaning up...')

        # Cleanup
        c.delete(f'/api/timelines/{tl_id}')
        for aid in action_ids.values():
            c.delete(f'/api/actions/{aid}')
        print('Done.')
        return 0


if __name__ == '__main__':
    sys.exit(run())
