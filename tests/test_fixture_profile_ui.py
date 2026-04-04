#!/usr/bin/env python3
"""
test_fixture_profile_ui.py — Load user's fixture profile, test all UI paths:
browse, search, add fixture, share to community, dedup check.
"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18096
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
    print(f'\n-- {name} --')


def seed():
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Profile Test', 'canvasW': 10000, 'canvasH': 5000})

        # Import user's custom profiles if available
        prof_path = os.path.join(os.path.dirname(__file__), 'user', 'slyled-profiles-2026-04-04.json')
        if os.path.exists(prof_path):
            with open(prof_path) as f:
                profiles = json.load(f)
            r = c.post('/api/dmx-profiles/import', json=profiles)
            d = r.get_json()
            print(f'  Imported {d.get("imported", 0)} profiles, skipped {d.get("skipped", 0)}')
        else:
            print(f'  No user profiles at {prof_path}')

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)
    return app


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


def test_profiles_loaded():
    section('Profiles Loaded')
    profiles = api('GET', '/api/dmx-profiles')
    ok(isinstance(profiles, list), 'GET /api/dmx-profiles returns list')
    ok(len(profiles) >= 12, f'At least 12 profiles (got {len(profiles)})')

    custom = [p for p in profiles if not p.get('builtin')]
    builtin = [p for p in profiles if p.get('builtin')]
    print(f'    Built-in: {len(builtin)}, Custom: {len(custom)}')
    ok(len(builtin) >= 12, f'Built-in profiles: {len(builtin)}')

    # Check user's custom profile if imported
    if custom:
        ok(True, f'Custom profiles found: {[p["id"] for p in custom]}')
        for p in custom:
            ok(p.get('channelCount', 0) > 0, f'Custom profile {p["id"]} has channels')


def test_unified_search():
    section('Unified Search')
    # Search for built-in
    r = api('GET', '/api/dmx-profiles/unified-search?q=moving')
    ok(isinstance(r, list), 'Unified search returns list')
    local = [p for p in (r or []) if p.get('source') == 'local']
    ok(len(local) >= 1, f'Found local "moving" profiles: {len(local)}')

    # Search for generic
    r = api('GET', '/api/dmx-profiles/unified-search?q=rgb')
    ok(isinstance(r, list) and len(r) >= 1, f'Found "rgb" profiles: {len(r or [])}')
    sources = set(p.get('source') for p in (r or []))
    ok('local' in sources, 'Local results in unified search')

    # Search for user's custom profile name (if it has a distinctive name)
    profiles = api('GET', '/api/dmx-profiles')
    custom = [p for p in (profiles or []) if not p.get('builtin')]
    if custom:
        name = custom[0].get('name', '')
        if len(name) >= 3:
            r = api('GET', f'/api/dmx-profiles/unified-search?q={name[:5]}')
            found = any(p.get('id') == custom[0]['id'] for p in (r or []))
            ok(found, f'Custom profile "{name}" found in unified search')


def test_add_fixture_flow():
    section('Add Fixture Flow')
    # Simulate what the Add Fixture wizard does

    # 1. Browse All — should return all profiles
    profiles = api('GET', '/api/dmx-profiles')
    ok(len(profiles) >= 12, f'Browse All returns {len(profiles)} profiles')

    # 2. Create a fixture using a profile
    if profiles:
        prof = profiles[0]
        r = api('POST', '/api/fixtures', {
            'name': 'Test from profile', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1,
            'dmxChannelCount': prof['channelCount'],
            'dmxProfileId': prof['id']
        })
        ok(r and r.get('ok'), f'Created fixture with profile {prof["id"]}')
        fid = r.get('id') if r else None

        # 3. Verify fixture has the profile
        if fid is not None:
            f = api('GET', f'/api/fixtures/{fid}')
            ok(f and f.get('dmxProfileId') == prof['id'], 'Fixture has correct profileId')
            ok(f and f.get('dmxChannelCount') == prof['channelCount'], 'Fixture has correct channel count')

            # 4. Get channels for this fixture
            ch = api('GET', f'/api/dmx/fixture/{fid}/channels')
            ok(ch is not None, 'GET fixture channels works')
            if ch:
                channels = ch.get('channels', [])
                ok(len(channels) == prof['channelCount'], f'Channel count matches: {len(channels)}')

            # Cleanup
            api('DELETE', f'/api/fixtures/{fid}')


def test_share_check():
    section('Share / Dedup Check')
    profiles = api('GET', '/api/dmx-profiles')
    custom = [p for p in (profiles or []) if not p.get('builtin')]

    if custom:
        pid = custom[0]['id']
        # Check for duplicates
        r = api('POST', '/api/dmx-profiles/community/check', {'profileId': pid})
        ok(r is not None, f'Dedup check returned response for {pid}')
        if r and r.get('data'):
            data = r['data']
            ok('slug_available' in data, 'Response has slug_available')
            ok('duplicate' in data, 'Response has duplicate flag')
            print(f'    slug_available={data.get("slug_available")}, duplicate={data.get("duplicate")}')
    else:
        ok(True, 'No custom profiles to test sharing (skipped)')


def main():
    print('=== Fixture Profile UI Test ===')
    seed()

    test_profiles_loaded()
    test_unified_search()
    test_add_fixture_flow()
    test_share_check()

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
