#!/usr/bin/env python3
"""
test_show_playback.py — End-to-end test: load a show file, bake, verify
preview data exists, verify DMX fixture segments, check logging output.

Usage:
    python tests/test_show_playback.py
    python tests/test_show_playback.py -v
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18092
BASE = f'http://127.0.0.1:{PORT}'
_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv

def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose: print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')

def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


def seed_and_start():
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Playback Test', 'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # LED fixtures
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cid1 = r.get_json()['id']
        r = c.post('/api/fixtures', json={
            'name': 'LED Left', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}]
        })
        fix_led = r.get_json()['id']

        # DMX fixtures
        r = c.post('/api/fixtures', json={
            'name': 'Moving Head', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'aimPoint': [5000, 2000, 5000]
        })
        fix_dmx = r.get_json()['id']

        r = c.post('/api/fixtures', json={
            'name': 'RGB Par', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb',
            'aimPoint': [5000, 0, 5000]
        })
        fix_par = r.get_json()['id']

        # Place all
        c.post('/api/layout', json={'children': [
            {'id': fix_led, 'x': 2000, 'y': 4000, 'z': 0},
            {'id': fix_dmx, 'x': 5000, 'y': 5000, 'z': 2000},
            {'id': fix_par, 'x': 8000, 'y': 3000, 'z': 0},
        ]})

        # Load user's exported config (children + layout)
        config_path = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config.json')
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
            r = c.post('/api/config/import', json=config)
            print(f'  Config import: {r.get_json()}')

        # Load user's exported show (actions + effects + timelines)
        show_path = os.path.join(os.path.dirname(__file__), 'user', 'slyled-show.json')
        if os.path.exists(show_path):
            with open(show_path) as f:
                show = json.load(f)
        else:
            # Create minimal show inline
            show = {'type': 'slyled-show', 'version': 1,
                    'actions': [{'id': 0, 'name': 'Solid', 'type': 1, 'r': 255, 'g': 0, 'b': 0}],
                    'spatialEffects': [], 'timelines': [{'id': 0, 'name': 'Test', 'durationS': 10,
                    'tracks': [{'allPerformers': True, 'clips': [{'actionId': 0, 'startS': 0, 'durationS': 10}]}]}]}
        r = c.post('/api/show/import', json=show)
        ok(r.get_json().get('ok'), 'Show imported successfully')

        # Start logging to a known file
        log_path = os.path.join(os.path.dirname(__file__), 'user', 'playback-test.log')
        c.post('/api/logging/start', json={'path': log_path})

    # Start server
    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)

    return {'fix_led': fix_led, 'fix_dmx': fix_dmx, 'fix_par': fix_par,
            'log_path': log_path}


def api(method, path, body=None):
    import urllib.request as ur
    data = json.dumps(body).encode() if body else None
    headers = {'Content-Type': 'application/json'} if data else {}
    req = ur.Request(f'{BASE}{path}', data=data, method=method, headers=headers)
    try:
        resp = ur.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        return None


def test_show_loaded():
    section('Show Data Loaded')
    actions = api('GET', '/api/actions')
    ok(isinstance(actions, list) and len(actions) >= 1, f'Actions loaded ({len(actions)})')
    timelines = api('GET', '/api/timelines')
    ok(isinstance(timelines, list) and len(timelines) >= 1, f'Timelines loaded ({len(timelines)})')
    effects = api('GET', '/api/spatial-effects')
    ok(isinstance(effects, list), f'Spatial effects loaded ({len(effects) if effects else 0})')

    # Check first available timeline
    if timelines:
        tl = api('GET', f'/api/timelines/{timelines[0]["id"]}')
        ok(tl is not None, f'Timeline {timelines[0]["id"]} accessible')
        tracks = tl.get('tracks', [])
        ok(len(tracks) >= 1, f'Timeline has {len(tracks)} tracks')
        total_clips = sum(len(t.get("clips", [])) for t in tracks)
        ok(total_clips >= 1, f'Timeline has {total_clips} total clips')
        # Check clip references — do actionIds/effectIds exist?
        action_ids = {a["id"] for a in actions}
        effect_ids = {e["id"] for e in (effects or [])}
        for ti, track in enumerate(tracks):
            for ci, clip in enumerate(track.get("clips", [])):
                aid = clip.get("actionId")
                eid = clip.get("effectId") or clip.get("spatialEffectId")
                if aid is not None:
                    ok(aid in action_ids, f'Track {ti} clip {ci} actionId={aid} exists')
                if eid is not None:
                    ok(eid in effect_ids, f'Track {ti} clip {ci} effectId={eid} exists')


def test_layout_fixtures(ids):
    section('Layout Fixtures for Rendering')
    layout = api('GET', '/api/layout')
    fixtures = layout.get('fixtures', [])
    ok(len(fixtures) >= 1, f'Layout has fixtures ({len(fixtures)})')
    positioned = [f for f in fixtures if f.get('positioned')]
    ok(len(positioned) >= 1, f'Positioned fixtures ({len(positioned)})')

    led = [f for f in positioned if f.get('fixtureType') != 'dmx']
    dmx = [f for f in positioned if f.get('fixtureType') == 'dmx']
    print(f'    LED: {len(led)}, DMX: {len(dmx)}, total positioned: {len(positioned)}')

    for f in led:
        strings = f.get('strings', [])
        leds = sum(s.get('leds', 0) for s in strings)
        ok(leds > 0, f'LED fixture {f["id"]} "{f.get("name")}" has {leds} LEDs')
    for f in dmx:
        ok(f.get('aimPoint') is not None, f'DMX fixture {f["id"]} "{f.get("name")}" has aimPoint')

    # Check for fixture ID mismatches between show and layout
    timelines = api('GET', '/api/timelines')
    fix_ids = {f['id'] for f in fixtures}
    for tl in (timelines or []):
        for track in tl.get('tracks', []):
            fid = track.get('fixtureId')
            if fid is not None and fid >= 0:
                ok(fid in fix_ids, f'Timeline track fixtureId={fid} exists in layout (have: {fix_ids})')


def test_bake_and_preview(ids):
    section('Bake & Preview')
    timelines = api('GET', '/api/timelines')
    ok(len(timelines) > 0, 'Have timelines')
    tl_id = timelines[0]['id']

    # Bake
    r = api('POST', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('ok'), 'Bake started')

    # Poll
    for _ in range(150):
        time.sleep(0.2)
        st = api('GET', f'/api/timelines/{tl_id}/baked/status')
        if st and (st.get('done') or st.get('error')):
            break

    ok(st.get('done'), 'Bake completed')
    if st.get('error'):
        print(f'    BAKE ERROR: {st.get("error")}')
        ok(False, f'Bake error: {st["error"]}')
        return

    # Bake result
    baked = api('GET', f'/api/timelines/{tl_id}/baked')
    ok(baked is not None and 'err' not in baked, 'Bake result exists')
    n_fix = len(baked.get('fixtures', {})) if isinstance(baked.get('fixtures'), dict) else 0
    ok(n_fix > 0, f'Bake produced fixture segments ({n_fix})')
    ok(baked.get('totalFrames', 0) > 0, f'Bake produced frames ({baked.get("totalFrames")})')

    # Preview
    preview = api('GET', f'/api/timelines/{tl_id}/baked/preview')
    ok(preview is not None and 'err' not in preview, 'Preview data exists')
    if isinstance(preview, dict):
        ok(len(preview) > 0, f'Preview has fixture keys ({list(preview.keys())[:5]})')
        for k, v in list(preview.items())[:2]:
            ok(isinstance(v, list) and len(v) > 0, f'Preview[{k}] has frames ({len(v) if isinstance(v, list) else "N/A"})')


def test_logging(ids):
    section('Logging')
    # Check logging status
    st = api('GET', '/api/logging/status')
    ok(st is not None and st.get('enabled'), 'Logging is enabled')
    ok(st.get('path') is not None, f'Log path: {st.get("path")}')

    # Stop logging
    r = api('POST', '/api/logging/stop')
    ok(r and r.get('ok'), 'Logging stopped')

    st = api('GET', '/api/logging/status')
    ok(not st.get('enabled'), 'Logging disabled after stop')

    # Check log file has content
    log_path = ids.get('log_path')
    if log_path and os.path.exists(log_path):
        size = os.path.getsize(log_path)
        ok(size > 100, f'Log file has content ({size} bytes)')
        with open(log_path) as f:
            content = f.read()
        ok('BAKE' in content, 'Log contains BAKE entries')
        ok('Logging started' in content, 'Log contains start marker')
    else:
        ok(False, f'Log file exists at {log_path}')

    # Restart with default path
    r = api('POST', '/api/logging/start')
    ok(r and r.get('ok'), 'Logging restarted with default path')
    ok(r.get('path') is not None, f'Default log path: {r.get("path")}')

    api('POST', '/api/logging/stop')


def main():
    print('\033[1m=== Show Playback Test ===\033[0m')
    ids = seed_and_start()

    test_show_loaded()
    test_layout_fixtures(ids)
    test_bake_and_preview(ids)
    test_logging(ids)

    total = _pass + _fail
    print(f'\n\033[1m{"="*60}\033[0m')
    if _fail == 0:
        print(f'\033[32m  ALL {total} TESTS PASSED\033[0m')
    else:
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m out of {total}')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
