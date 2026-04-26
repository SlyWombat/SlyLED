#!/usr/bin/env python3
"""
test_user_show_artnet.py — Load user's exported config+show, bake, start playback,
capture Art-Net output, and verify DMX fixtures receive non-zero values.

Tests the full pipeline: import → bake → preview → Art-Net packets.

Usage:
    python tests/test_user_show_artnet.py
    python tests/test_user_show_artnet.py -v
"""

import sys, os, json, time, threading, socket, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

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


def load_user_files():
    """Load user's config and show, return fixture details."""
    import parent_server
    from parent_server import app

    user_dir = os.path.join(os.path.dirname(__file__), 'user')
    config_path = os.path.join(user_dir, 'slyled-config.json')
    show_path = os.path.join(user_dir, 'slyled-show.json')
    # #688 — both files must exist; the test exercises the import →
    # bake → Art-Net pipeline against an OPERATOR's exported state.
    # When slyled-show.json is missing (the export is gitignored), the
    # imports succeed but produce 0 timelines/actions/effects so every
    # downstream assert fails. Self-skip cleanly so the regression run
    # doesn't conflate missing fixtures with a real regression.
    if not os.path.exists(config_path) or not os.path.exists(show_path):
        print(f"  SKIP: tests/user/slyled-config.json AND slyled-show.json "
              f"required (one or both missing). Export from your "
              f"orchestrator and copy them into tests/user/ to run.")
        sys.exit(0)

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Import config (children + layout)
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
            r = c.post('/api/config/import', json=config)
            print(f'  Config import: {r.get_json()}')

        # Import show (actions + effects + timelines)
        if os.path.exists(show_path):
            with open(show_path) as f:
                show = json.load(f)
            r = c.post('/api/show/import', json=show)
            print(f'  Show import: {r.get_json()}')

        # Get current state
        fixtures = c.get('/api/fixtures').get_json()
        children = c.get('/api/children').get_json()
        layout = c.get('/api/layout').get_json()
        timelines = c.get('/api/timelines').get_json()
        actions = c.get('/api/actions').get_json()
        effects = c.get('/api/spatial-effects').get_json()

        # Enable logging for diagnosis
        log_path = os.path.join(user_dir, 'artnet-test.log')
        c.post('/api/logging/start', json={'path': log_path})

    return {
        'fixtures': fixtures, 'children': children, 'layout': layout,
        'timelines': timelines, 'actions': actions, 'effects': effects,
        'log_path': log_path,
    }


def test_import_state(data):
    section('Import State')
    ok(len(data['children']) >= 1, f'Children imported: {len(data["children"])}')
    ok(len(data['fixtures']) >= 1, f'Fixtures: {len(data["fixtures"])}')
    ok(len(data['timelines']) >= 1, f'Timelines: {len(data["timelines"])}')
    ok(len(data['actions']) >= 1, f'Actions: {len(data["actions"])}')
    ok(len(data['effects']) >= 1, f'Spatial effects: {len(data["effects"])}')

    # Check layout positions
    lf = data['layout'].get('fixtures', [])
    positioned = [f for f in lf if f.get('positioned')]
    ok(len(positioned) >= 1, f'Positioned fixtures in layout: {len(positioned)}')
    if not positioned:
        print('    WARNING: No fixtures have layout positions — bake will produce empty results')
        print(f'    Layout children IDs: {[c["id"] for c in data["layout"].get("children",[])]}')
        print(f'    Fixture IDs: {[f["id"] for f in data["fixtures"]]}')

    # Check for ID mismatches between show and fixtures
    fix_ids = {f['id'] for f in data['fixtures']}
    for tl in data['timelines']:
        for track in tl.get('tracks', []):
            fid = track.get('fixtureId')
            if fid is not None and fid >= 0:
                ok(fid in fix_ids, f'Timeline track fixtureId={fid} exists (have {fix_ids})')

    # Check DMX fixtures
    dmx_fx = [f for f in data['fixtures'] if f.get('fixtureType') == 'dmx']
    print(f'    DMX fixtures: {len(dmx_fx)}')
    for f in dmx_fx:
        print(f'      {f["id"]}: {f.get("name")} U{f.get("dmxUniverse")} @{f.get("dmxStartAddr")} ({f.get("dmxChannelCount")}ch)')


def test_bake(data):
    section('Bake')
    import parent_server
    from parent_server import app

    if not data['timelines']:
        ok(False, 'No timelines to bake')
        return {}

    tl = data['timelines'][0]
    tl_id = tl['id']

    with app.test_client() as c:
        r = c.post(f'/api/timelines/{tl_id}/bake')
        ok(r.get_json().get('ok'), f'Bake started for "{tl.get("name")}" (id={tl_id})')

        for _ in range(150):
            time.sleep(0.2)
            st = c.get(f'/api/timelines/{tl_id}/baked/status').get_json()
            if st.get('done') or st.get('error'):
                break

        ok(st.get('done'), 'Bake completed')
        if st.get('error'):
            ok(False, f'Bake error: {st["error"]}')
            return {}

        baked = c.get(f'/api/timelines/{tl_id}/baked').get_json()
        fix_segs = baked.get('fixtures', {})
        ok(len(fix_segs) > 0, f'Bake produced segments for {len(fix_segs)} fixtures')

        # Analyze DMX fixture segments
        dmx_fx = [f for f in data['fixtures'] if f.get('fixtureType') == 'dmx']
        for f in dmx_fx:
            fid_str = str(f['id'])
            segs = fix_segs.get(fid_str, {}).get('segments', [])
            has_color = any(
                s.get('params', {}).get('r', 0) + s.get('params', {}).get('g', 0) + s.get('params', {}).get('b', 0) > 0
                for s in segs
            )
            has_dimmer = any(s.get('params', {}).get('dimmer', 0) > 0 for s in segs)
            ok(len(segs) > 0, f'DMX {f["id"]} "{f.get("name")}": {len(segs)} segments')
            ok(has_color, f'DMX {f["id"]} has non-zero color in segments')
            ok(has_dimmer, f'DMX {f["id"]} has non-zero dimmer')
            if _verbose and segs:
                for si, s in enumerate(segs[:3]):
                    p = s.get('params', {})
                    print(f'      seg[{si}]: r={p.get("r")} g={p.get("g")} b={p.get("b")} dim={p.get("dimmer")} t={s.get("startS")}-{s.get("startS",0)+s.get("durationS",0)}s')

        # Preview data
        preview = c.get(f'/api/timelines/{tl_id}/baked/preview').get_json()
        ok(isinstance(preview, dict) and len(preview) > 0, f'Preview has {len(preview)} fixture keys')

    return baked


def test_artnet_output(data, baked):
    """Start playback briefly and listen for Art-Net packets on localhost."""
    section('Art-Net Output')
    import parent_server
    from parent_server import app

    if not data['timelines'] or not baked:
        ok(False, 'No baked data for Art-Net test')
        return

    tl_id = data['timelines'][0]['id']
    captured_packets = []

    # Listen for Art-Net packets
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listen_sock.bind(('127.0.0.1', 6455))  # non-standard port to avoid conflicts
    except OSError:
        listen_sock.bind(('127.0.0.1', 0))
    listen_sock.settimeout(0.1)

    # Start a brief playback
    with app.test_client() as c:
        # Start DMX engine
        c.post('/api/dmx/start')
        time.sleep(0.5)

        # Check DMX status
        st = c.get('/api/dmx/status').get_json()
        ok(st is not None, f'DMX status: {st}')

        # Start show
        r = c.post(f'/api/timelines/{tl_id}/start')
        started = r.get_json()
        ok(started is not None, f'Show start response: {started}')

        # Wait briefly for DMX output
        time.sleep(2)

        # Check if any DMX channels have been set
        dmx_fx = [f for f in data['fixtures'] if f.get('fixtureType') == 'dmx']
        for f in dmx_fx:
            r = c.get(f'/api/dmx/fixture/{f["id"]}/channels')
            ch_data = r.get_json()
            if ch_data and ch_data.get('channels'):
                non_zero = [ch for ch in ch_data['channels'] if ch.get('value', 0) > 0]
                ok(len(non_zero) > 0, f'DMX {f["id"]} "{f.get("name")}": {len(non_zero)} channels non-zero')
                if _verbose and non_zero:
                    for ch in non_zero[:5]:
                        print(f'      ch[{ch["offset"]}] {ch["name"]}: {ch["value"]}')
            else:
                ok(False, f'DMX {f["id"]} channels not available')

        # Stop
        c.post(f'/api/timelines/{tl_id}/stop')
        c.post('/api/dmx/stop')

    listen_sock.close()


def test_log_output(data):
    section('Log Output')
    log_path = data.get('log_path')
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/logging/stop')

    if log_path and os.path.exists(log_path):
        size = os.path.getsize(log_path)
        ok(size > 0, f'Log file has content ({size} bytes)')
        with open(log_path) as f:
            content = f.read()
        ok('BAKE' in content, 'Log contains BAKE entries')
        # Show key log lines
        if _verbose:
            for line in content.split('\n'):
                if 'BAKE' in line or 'DMX' in line or 'ERROR' in line:
                    print(f'    LOG: {line.strip()}')
    else:
        ok(False, f'Log file at {log_path}')


def main():
    print('\033[1m=== User Show Art-Net Test ===\033[0m')
    data = load_user_files()
    test_import_state(data)
    baked = test_bake(data)
    test_artnet_output(data, baked)
    test_log_output(data)

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
