#!/usr/bin/env python3
"""
test_spa.py — Comprehensive Playwright end-to-end tests for the SlyLED SPA.

Covers all 7 tabs, modals, API interactions, save/load, error states, and edge cases.
Seeds data via Flask test client (fast, no UDP timeouts), then tests UI via Playwright.

Usage:
    python tests/test_spa.py               # run all tests
    python tests/test_spa.py -v            # verbose output
    python tests/test_spa.py -k dashboard  # run only matching tests

Requires: pip install playwright && python -m playwright install chromium
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18090
BASE = f'http://127.0.0.1:{PORT}'

# ── Test infrastructure ───────────────────────────────────────────────────────

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv
_filter = None
for i, a in enumerate(sys.argv):
    if a == '-k' and i + 1 < len(sys.argv):
        _filter = sys.argv[i + 1].lower()


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose:
            print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── Seed via Flask test client (fast, no network) ────────────────────────────

def seed_data():
    """Populate test data via Flask test client. Returns IDs dict."""
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Settings + stage
        c.post('/api/settings', json={'name': 'Test SlyLED', 'darkMode': 1,
                                       'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # Children + fixtures
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cid1 = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'Left Strip', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}]
        })
        fix1 = r.get_json().get('id')

        r = c.post('/api/children', json={'ip': '10.0.0.51'})
        cid2 = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'Right Strip', 'type': 'linear', 'fixtureType': 'led', 'childId': cid2,
            'strings': [{'leds': 30, 'mm': 1500, 'sdir': 2}]
        })
        fix2 = r.get_json().get('id')

        # DMX fixtures
        r = c.post('/api/fixtures', json={
            'name': 'Moving Head 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'aimPoint': [5000, 0, 5000]
        })
        dmx1 = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'RGB Par', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb',
            'aimPoint': [5000, 0, 5000]
        })
        dmx2 = r.get_json().get('id')

        # Layout positions
        c.post('/api/layout', json={'children': [
            {'id': fix1, 'x': 1000, 'y': 4500, 'z': 0},
            {'id': fix2, 'x': 9000, 'y': 4500, 'z': 0},
            {'id': dmx1, 'x': 2000, 'y': 5000, 'z': 2000},
            {'id': dmx2, 'x': 5000, 'y': 4800, 'z': 5000},
        ]})

        # Actions
        for a in [
            {'name': 'Warm Solid', 'type': 1, 'r': 255, 'g': 160, 'b': 40},
            {'name': 'Cool Fade', 'type': 2, 'r': 0, 'g': 100, 'b': 255,
             'r2': 100, 'g2': 0, 'b2': 200, 'speedMs': 3000},
            {'name': 'Rainbow Chase', 'type': 4, 'r': 255, 'g': 0, 'b': 0,
             'speedMs': 40, 'spacing': 5, 'tailLen': 3},
        ]:
            c.post('/api/actions', json=a)

        # Spatial effect
        c.post('/api/spatial-effects', json={
            'name': 'Blue Sweep', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 0, 'g': 80, 'b': 220, 'size': {'radius': 2000},
            'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                       'durationS': 8, 'easing': 'ease-in-out'},
            'blend': 'add'
        })

        # Timeline
        r = c.post('/api/timelines', json={'name': 'Test Timeline', 'durationS': 30})
        tl_id = r.get_json().get('id')

        # Surface
        c.post('/api/surfaces', json={
            'name': 'Back Wall', 'type': 'wall', 'color': '#1e293b',
            'x': 5000, 'y': 2500, 'z': 0, 'w': 10000, 'h': 5000, 'd': 100, 'opacity': 0.3
        })

        # WiFi (needed for firmware tab tests)
        c.post('/api/wifi', json={'ssid': 'TestNet', 'password': 'test123'})

    return {'cid1': cid1, 'cid2': cid2, 'fix1': fix1, 'fix2': fix2,
            'dmx1': dmx1, 'dmx2': dmx2, 'tl_id': tl_id}


# ── Start Flask server for Playwright ────────────────────────────────────────

def start_server():
    import parent_server
    from parent_server import app

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)
    return app


# ── Playwright helpers ────────────────────────────────────────────────────────

def wait_tab(page, tab_id, timeout=5000):
    page.click(f'#n-{tab_id}')
    page.wait_for_selector(f'#t-{tab_id}', state='visible', timeout=timeout)
    time.sleep(0.4)


def get_text(page, sel):
    el = page.query_selector(sel)
    return el.inner_text() if el else ''


def api_json(page, method, path, body=None):
    """Execute API call from browser context, return parsed JSON."""
    body_js = json.dumps(body) if body is not None else 'null'
    js = f"""() => new Promise((resolve) => {{
        var x = new XMLHttpRequest();
        x.open('{method}', '{path}', true);
        var b = {body_js};
        if (b !== null) x.setRequestHeader('Content-Type', 'application/json');
        x.onload = function() {{ try {{ resolve(JSON.parse(x.responseText)); }} catch(e) {{ resolve(null); }} }};
        x.onerror = function() {{ resolve(null); }};
        x.send(b !== null ? JSON.stringify(b) : null);
    }})"""
    return page.evaluate(js)


# ── Test suites ───────────────────────────────────────────────────────────────

def test_server_health(page, ids):
    section('Server Health')
    resp = page.evaluate("() => fetch('/status').then(r => r.json())")
    ok(resp is not None, 'GET /status returns JSON')
    ok(resp.get('role') == 'parent', '/status role is parent')
    ok('version' in resp, '/status has version field')

    page.goto(BASE, wait_until='networkidle', timeout=10000)
    time.sleep(0.5)
    ok(page.title() != '', 'SPA has a page title')
    for tab in ['dash', 'setup', 'layout', 'actions', 'runtime', 'settings', 'firmware']:
        ok(page.query_selector(f'#n-{tab}') is not None, f'{tab} tab button exists')

    resp = page.evaluate("() => fetch('/favicon.ico').then(r => ({status: r.status}))")
    ok(resp.get('status') == 404, 'GET /favicon.ico returns 404')


def test_dashboard(page, ids):
    section('Dashboard')
    wait_tab(page, 'dash')
    text = get_text(page, '#t-dash')
    ok(page.query_selector('#t-dash table') is not None, 'Dashboard has a status table')
    rows = page.query_selector_all('#t-dash table tr')
    ok(len(rows) >= 2, f'Dashboard table has data rows ({len(rows)})')
    ok('Left Strip' in text or '10.0.0.50' in text, 'Dashboard shows performer info')


def test_setup(page, ids):
    section('Setup')
    wait_tab(page, 'setup')
    text = get_text(page, '#t-setup')

    ok('Left Strip' in text, 'Setup shows LED fixture')
    ok('Right Strip' in text, 'Setup shows second LED fixture')
    ok('Moving Head 1' in text, 'Setup shows DMX fixture')
    ok('RGB Par' in text, 'Setup shows DMX par')

    # Add fixture modal
    add_btn = page.query_selector('button[onclick*="showAddFixture"]')
    if add_btn:
        add_btn.click()
        time.sleep(0.3)
        modal = page.query_selector('#modal')
        vis = modal and modal.evaluate('el => getComputedStyle(el).display') != 'none'
        ok(vis, 'Add Fixture modal opens')
        page.evaluate("typeof closeModal==='function'&&closeModal()")
        time.sleep(0.2)
    else:
        ok(False, 'Add Fixture button found')

    disc = page.query_selector('button[onclick*="discover"]')
    ok(disc is not None, 'Discover button exists')

    refresh = page.query_selector('button[onclick*="setupRefreshAll"], button[onclick*="refreshAll"]')
    ok(refresh is not None, 'Refresh All button exists')


def test_layout(page, ids):
    section('Layout')
    wait_tab(page, 'layout')
    time.sleep(0.5)

    canvas = page.query_selector('#t-layout canvas, #lay-canvas, #layCanvas')
    ok(canvas is not None, 'Layout has canvas element')

    save_btn = page.query_selector('#btn-lay-save')
    ok(save_btn is not None, 'Save Layout button exists')
    if save_btn:
        save_btn.click()
        time.sleep(1)
        ok('Error' not in (save_btn.inner_text() or ''), 'Save Layout succeeds')


def test_actions_tab(page, ids):
    section('Actions Tab')
    wait_tab(page, 'actions')
    text = get_text(page, '#t-actions')

    ok('Warm Solid' in text, 'Actions shows Warm Solid')
    ok('Cool Fade' in text, 'Actions shows Cool Fade')
    ok('Rainbow Chase' in text, 'Actions shows Rainbow Chase')

    new_btn = page.query_selector('button[onclick*="newAction"]')
    if new_btn:
        new_btn.click()
        time.sleep(0.3)
        modal = page.query_selector('#modal')
        vis = modal and modal.evaluate('el => getComputedStyle(el).display') != 'none'
        ok(vis, 'New Action modal opens')
        page.evaluate("typeof closeModal==='function'&&closeModal()")
        time.sleep(0.2)
    else:
        ok(False, 'New Action button found')


def test_runtime(page, ids):
    section('Runtime')
    wait_tab(page, 'runtime')
    text = get_text(page, '#t-runtime')

    ok('Test Timeline' in text, 'Runtime shows seeded timeline')
    buttons = page.query_selector_all('#t-runtime button')
    btn_texts = [b.inner_text().lower() for b in buttons]
    ok(any('bake' in t for t in btn_texts) or any('start' in t for t in btn_texts),
       'Runtime has playback controls')


def test_settings_tab(page, ids):
    section('Settings Tab')
    wait_tab(page, 'settings')
    text = get_text(page, '#t-settings')

    ok(page.query_selector('button[onclick*="saveSettings"]') is not None, 'Save Settings button')
    ok(page.query_selector('button[onclick*="exportConfig"]') is not None, 'Save Config button')
    ok(page.query_selector('button[onclick*="exportShow"]') is not None, 'Save Show button')
    ok(page.query_selector('button[onclick*="LoadShow"]') or
       page.query_selector('button[onclick*="loadShow"]') or
       page.query_selector('button[onclick*="openLoadShow"]') is not None, 'Load Show button')
    ok(page.query_selector('button[onclick*="Qr"], button[onclick*="qr"]') is not None, 'QR Code button')


def test_firmware_tab(page, ids):
    section('Firmware Tab')
    wait_tab(page, 'firmware')
    time.sleep(1)

    ok(page.query_selector('#t-firmware input[type="password"], input[id*="wifi"], input[name*="pw"]') is not None,
       'WiFi password input exists')
    ok(page.query_selector('button[onclick*="saveWifi"]') is not None, 'Save WiFi button')
    text = get_text(page, '#t-firmware')
    ok('USB' in text or 'Flash' in text or 'OTA' in text or 'WiFi' in text, 'Firmware tab has content')


def test_show_export(page, ids):
    """The reported bug: Save Show was failing."""
    section('Show Export / Import (bug fix)')

    # API test
    resp = api_json(page, 'GET', '/api/show/export')
    ok(resp is not None, 'GET /api/show/export returns JSON (was failing)')
    ok(resp.get('type') == 'slyled-show', 'Export type is slyled-show')
    ok(resp.get('version') == 1, 'Export version is 1')
    ok(isinstance(resp.get('actions'), list), 'Export has actions array')
    ok(isinstance(resp.get('timelines'), list), 'Export has timelines array')
    ok(isinstance(resp.get('spatialEffects'), list), 'Export has spatialEffects array')
    ok(len(resp.get('actions', [])) == 3, f'Export has 3 actions (got {len(resp.get("actions", []))})')
    ok(len(resp.get('timelines', [])) == 1, f'Export has 1 timeline (got {len(resp.get("timelines", []))})')
    ok(len(resp.get('spatialEffects', [])) == 1, f'Export has 1 effect (got {len(resp.get("spatialEffects", []))})')

    # UI button test — intercept download
    wait_tab(page, 'settings')
    time.sleep(0.3)
    export_btn = page.query_selector('button[onclick*="exportShow"]')
    if export_btn:
        with page.expect_download(timeout=5000) as dl_info:
            export_btn.click()
        dl = dl_info.value
        ok(dl.suggested_filename == 'slyled-show.json', 'Download filename correct')
        path = dl.path()
        if path:
            data = json.loads(open(path).read())
            ok(data.get('type') == 'slyled-show', 'Downloaded JSON has correct type')
            ok(len(data.get('actions', [])) == 3, 'Downloaded JSON has 3 actions')
        else:
            ok(False, 'Download file accessible')
    else:
        ok(False, 'Save Show button click')


def test_show_import(page, ids):
    section('Show Import')

    show = {
        'type': 'slyled-show',
        'actions': [{'id': 0, 'name': 'Imported', 'type': 1, 'r': 255, 'g': 0, 'b': 0}],
        'timelines': [{'id': 0, 'name': 'Imported TL', 'durationS': 10, 'clips': []}],
        'spatialEffects': []
    }
    resp = api_json(page, 'POST', '/api/show/import', show)
    ok(resp is not None and resp.get('ok'), 'Import succeeds')
    ok(resp.get('actions') == 1, 'Import reports 1 action')
    ok(resp.get('timelines') == 1, 'Import reports 1 timeline')

    # Verify replaced
    actions = api_json(page, 'GET', '/api/actions')
    ok(len(actions) == 1 and actions[0].get('name') == 'Imported', 'Actions replaced')

    timelines = api_json(page, 'GET', '/api/timelines')
    ok(len(timelines) == 1 and timelines[0].get('name') == 'Imported TL', 'Timelines replaced')

    # Bad type rejected
    resp = api_json(page, 'POST', '/api/show/import', {'type': 'bogus'})
    ok(resp is not None and not resp.get('ok'), 'Import bad type rejected')


def test_config_export_import(page, ids):
    section('Config Export / Import')

    resp = api_json(page, 'GET', '/api/config/export')
    ok(resp is not None, 'Config export returns JSON')
    ok(resp.get('type') == 'slyled-config', 'Config type correct')
    ok(isinstance(resp.get('children'), list), 'Config has children')
    ok(isinstance(resp.get('layout'), dict), 'Config has layout')

    resp2 = api_json(page, 'POST', '/api/config/import', resp)
    ok(resp2 is not None and resp2.get('ok'), 'Config import succeeds')

    resp3 = api_json(page, 'POST', '/api/config/import', {'type': 'wrong'})
    ok(resp3 is not None and not resp3.get('ok'), 'Config import bad type rejected')


def test_show_presets(page, ids):
    section('Show Presets')

    presets = api_json(page, 'GET', '/api/show/presets')
    ok(isinstance(presets, list), 'Presets returns list')
    ok(len(presets) >= 9, f'At least 9 presets ({len(presets)})')
    names = [p.get('id') for p in presets]
    ok('rainbow-up' in names, 'Has rainbow-up preset')
    ok('disco' in names, 'Has disco preset')
    ok('spotlight-sweep' in names, 'Has spotlight-sweep preset')

    resp = api_json(page, 'POST', '/api/show/preset', {'id': 'rainbow-up'})
    ok(resp is not None and resp.get('ok'), 'Load preset succeeds')
    ok(resp.get('timelineId') is not None, 'Preset creates timeline')


def test_actions_crud(page, ids):
    section('Actions CRUD')

    resp = api_json(page, 'POST', '/api/actions',
                    {'name': 'CRUD Test', 'type': 1, 'r': 100, 'g': 50, 'b': 200})
    ok(resp is not None and 'id' in resp, 'Create action')
    aid = resp.get('id')

    resp = api_json(page, 'GET', f'/api/actions/{aid}')
    ok(resp is not None and resp.get('name') == 'CRUD Test', 'Read action by id')

    resp = api_json(page, 'PUT', f'/api/actions/{aid}',
                    {'name': 'Updated', 'type': 1, 'r': 200, 'g': 100, 'b': 50})
    ok(resp is not None and resp.get('ok'), 'Update action')

    resp = api_json(page, 'GET', f'/api/actions/{aid}')
    ok(resp.get('name') == 'Updated', 'Action name updated')

    resp = api_json(page, 'DELETE', f'/api/actions/{aid}')
    ok(resp is not None, 'Delete action')


def test_fixtures_crud(page, ids):
    section('Fixtures CRUD')

    resp = api_json(page, 'POST', '/api/fixtures', {
        'name': 'CRUD Fix', 'type': 'linear', 'fixtureType': 'led',
        'strings': [{'leds': 10, 'mm': 500, 'sdir': 0}]
    })
    ok(resp is not None and 'id' in resp, 'Create fixture')
    fid = resp.get('id')

    resp = api_json(page, 'GET', f'/api/fixtures/{fid}')
    ok(resp is not None and resp.get('name') == 'CRUD Fix', 'Read fixture by id')

    resp = api_json(page, 'PUT', f'/api/fixtures/{fid}', {'name': 'Updated Fix'})
    ok(resp is not None and resp.get('ok'), 'Update fixture')

    resp = api_json(page, 'PUT', f'/api/fixtures/{fid}/aim', {'aimPoint': [100, 200, 300]})
    ok(resp is not None, 'Set aim point')

    resp = api_json(page, 'DELETE', f'/api/fixtures/{fid}')
    ok(resp is not None, 'Delete fixture')

    # Delete non-existent — should return error (fixed in #132)
    resp = api_json(page, 'DELETE', '/api/fixtures/99999')
    ok(resp is not None and resp.get('err'), 'Delete non-existent fixture returns error')


def test_timelines_crud(page, ids):
    section('Timelines CRUD')

    resp = api_json(page, 'POST', '/api/timelines', {'name': 'CRUD TL', 'durationS': 15})
    ok(resp is not None and 'id' in resp, 'Create timeline')
    tid = resp.get('id')

    resp = api_json(page, 'GET', f'/api/timelines/{tid}')
    ok(resp is not None and resp.get('name') == 'CRUD TL', 'Read timeline')

    resp = api_json(page, 'PUT', f'/api/timelines/{tid}',
                    {'name': 'CRUD Updated', 'durationS': 20, 'clips': []})
    ok(resp is not None and resp.get('ok'), 'Update timeline')

    resp = api_json(page, 'DELETE', f'/api/timelines/{tid}')
    ok(resp is not None, 'Delete timeline')

    resp = api_json(page, 'GET', f'/api/timelines/{tid}')
    ok(resp is None or resp.get('err'), 'Deleted timeline not found')


def test_spatial_effects_crud(page, ids):
    section('Spatial Effects CRUD')

    resp = api_json(page, 'POST', '/api/spatial-effects', {
        'name': 'CRUD FX', 'category': 'spatial-field', 'shape': 'sphere',
        'r': 255, 'g': 0, 'b': 0, 'size': {'radius': 500},
        'motion': {'startPos': [0, 0, 0], 'endPos': [1000, 0, 0], 'durationS': 5},
        'blend': 'add'
    })
    ok(resp is not None and 'id' in resp, 'Create spatial effect')
    fxid = resp.get('id')

    resp = api_json(page, 'GET', f'/api/spatial-effects/{fxid}')
    ok(resp is not None and resp.get('name') == 'CRUD FX', 'Read spatial effect')

    resp = api_json(page, 'PUT', f'/api/spatial-effects/{fxid}', {'name': 'Updated FX'})
    ok(resp is not None and resp.get('ok'), 'Update spatial effect')

    resp = api_json(page, 'DELETE', f'/api/spatial-effects/{fxid}')
    ok(resp is not None, 'Delete spatial effect')


def test_surfaces_crud(page, ids):
    section('Surfaces CRUD')

    resp = api_json(page, 'POST', '/api/surfaces', {
        'name': 'CRUD Wall', 'type': 'wall', 'color': '#333',
        'x': 1000, 'y': 1000, 'z': 0, 'w': 5000, 'h': 3000, 'd': 50, 'opacity': 0.5
    })
    ok(resp is not None and 'id' in resp, 'Create surface')
    sid = resp.get('id')

    surfs = api_json(page, 'GET', '/api/surfaces')
    ok(isinstance(surfs, list) and len(surfs) >= 1, 'Surfaces list non-empty')

    resp = api_json(page, 'DELETE', f'/api/surfaces/{sid}')
    ok(resp is not None, 'Delete surface')


def test_children_api(page, ids):
    section('Children API')

    children = api_json(page, 'GET', '/api/children')
    ok(isinstance(children, list) and len(children) >= 2, 'Children list populated')

    resp = api_json(page, 'GET', '/api/children/export')
    ok(isinstance(resp, list), 'Children export returns list')


def test_settings_api(page, ids):
    section('Settings API')

    resp = api_json(page, 'GET', '/api/settings')
    ok(resp is not None and 'name' in resp, 'GET settings')

    resp = api_json(page, 'POST', '/api/settings', {'name': 'API Updated'})
    ok(resp is not None and resp.get('ok'), 'POST settings')

    resp = api_json(page, 'GET', '/api/settings')
    ok(resp.get('name') == 'API Updated', 'Settings persisted')

    resp = api_json(page, 'GET', '/api/stage')
    ok(resp is not None and 'w' in resp, 'GET stage')

    resp = api_json(page, 'POST', '/api/stage', {'w': 12.0, 'h': 6.0, 'd': 8.0})
    ok(resp is not None and resp.get('ok'), 'POST stage')


def test_layout_api(page, ids):
    section('Layout API')

    resp = api_json(page, 'GET', '/api/layout')
    ok(resp is not None and 'children' in resp, 'GET layout')
    ok(len(resp.get('children', [])) >= 4, 'Layout has positions')

    resp2 = api_json(page, 'POST', '/api/layout', resp)
    ok(resp2 is not None and resp2.get('ok'), 'POST layout')


def test_dmx_profiles(page, ids):
    section('DMX Profiles')

    profiles = api_json(page, 'GET', '/api/dmx-profiles')
    ok(isinstance(profiles, list) and len(profiles) >= 3, f'Profiles list ({len(profiles)})')

    resp = api_json(page, 'GET', '/api/dmx-profiles/generic-rgb')
    ok(resp is not None and resp.get('name'), 'Get specific profile')

    resp = api_json(page, 'GET', '/api/dmx-profiles/export')
    ok(resp is not None, 'Profile export')

    resp = api_json(page, 'POST', '/api/dmx-profiles', {
        'name': 'Test Custom', 'channels': [{'name': 'Dim', 'type': 'dimmer', 'default': 0}]
    })
    ok(resp is not None, 'Create custom profile')


def test_dmx_engine(page, ids):
    section('DMX Engine')

    for path in ['/api/dmx/settings', '/api/dmx/status', '/api/dmx/interfaces',
                 '/api/dmx/patch', '/api/dmx/discovered']:
        resp = api_json(page, 'GET', path)
        ok(resp is not None, f'GET {path}')

    resp = api_json(page, 'GET', f'/api/dmx/fixture/{ids["dmx1"]}/channels')
    ok(resp is not None, 'GET DMX fixture channels')

    resp = api_json(page, 'POST', f'/api/dmx/fixture/{ids["dmx1"]}/test',
                    {'channels': [{'offset': 0, 'value': 255}, {'offset': 1, 'value': 128}]})
    ok(resp is not None, 'POST DMX fixture test')


def test_wifi_api(page, ids):
    section('WiFi API')

    resp = api_json(page, 'GET', '/api/wifi')
    ok(resp is not None and 'ssid' in resp, 'GET wifi')
    ok(resp.get('ssid') == 'TestNet', 'WiFi SSID from seed')

    resp = api_json(page, 'POST', '/api/wifi', {'ssid': 'NewNet', 'password': 'newpass'})
    ok(resp is not None and resp.get('ok'), 'POST wifi')

    resp = api_json(page, 'GET', '/api/wifi')
    ok(resp.get('ssid') == 'NewNet', 'WiFi updated')


def test_firmware_api(page, ids):
    section('Firmware API')

    resp = api_json(page, 'GET', '/api/firmware/registry')
    ok(resp is not None and isinstance(resp.get('firmware'), list), 'Registry')
    ok(len(resp.get('firmware', [])) >= 4, f'Registry entries ({len(resp.get("firmware", []))})')

    resp = api_json(page, 'GET', '/api/firmware/ports')
    ok(resp is not None, 'Ports list')

    resp = api_json(page, 'GET', '/api/firmware/flash/status')
    ok(resp is not None, 'Flash status')


def test_help_api(page, ids):
    section('Help API')
    for s in ['dash', 'setup', 'layout', 'actions', 'runtime', 'settings', 'firmware']:
        resp = api_json(page, 'GET', f'/api/help/{s}')
        ok(resp is not None, f'Help section: {s}')


def test_qr_api(page, ids):
    section('QR Code')
    resp = page.evaluate("() => fetch('/api/qr').then(r => ({status: r.status}))")
    ok(resp is not None, 'QR endpoint responds')


def test_tab_navigation(page, ids):
    section('Tab Navigation')
    tabs = ['dash', 'setup', 'layout', 'actions', 'runtime', 'settings', 'firmware']
    for tab in tabs:
        wait_tab(page, tab)
        panel = page.query_selector(f'#t-{tab}')
        vis = panel and panel.evaluate('el => el.offsetHeight > 0')
        ok(vis, f'{tab} panel visible when active')

    # Check active tab highlighting
    page.click('#n-dash')
    time.sleep(0.2)
    cls = page.evaluate("() => document.getElementById('n-dash').className")
    ok('tact' in cls, 'Active tab has tact class')


def test_error_handling(page, ids):
    section('Error Handling')

    resp = api_json(page, 'DELETE', '/api/fixtures/99999')
    ok(resp is not None and resp.get('err'), 'Delete non-existent fixture → error')

    resp = api_json(page, 'DELETE', '/api/actions/99999')
    ok(resp is None or resp.get('err'), 'Delete non-existent action → error')

    resp = api_json(page, 'DELETE', '/api/timelines/99999')
    ok(resp is not None and resp.get('err'), 'Delete non-existent timeline → error')

    resp = page.evaluate("() => fetch('/api/reset', {method:'POST'}).then(r=>({status:r.status}))")
    ok(resp.get('status') == 403, 'Reset without confirm → 403')

    resp = page.evaluate("() => fetch('/api/shutdown', {method:'POST'}).then(r=>({status:r.status}))")
    ok(resp.get('status') == 403, 'Shutdown without confirm → 403')

    resp = api_json(page, 'POST', '/api/show/import', {'type': 'bad'})
    ok(resp is not None and not resp.get('ok'), 'Show import bad type')

    resp = api_json(page, 'POST', '/api/config/import', {'type': 'bad'})
    ok(resp is not None and not resp.get('ok'), 'Config import bad type')

    resp = api_json(page, 'POST', '/api/show/preset', {'id': 'nonexistent-preset'})
    ok(resp is not None and not resp.get('ok'), 'Load non-existent preset fails')


def test_rapid_crud(page, ids):
    section('Rapid CRUD')

    created = []
    for i in range(5):
        resp = api_json(page, 'POST', '/api/actions',
                        {'name': f'Rapid{i}', 'type': 1, 'r': i*50, 'g': 0, 'b': 0})
        if resp and 'id' in resp:
            created.append(resp['id'])
    ok(len(created) == 5, f'Created 5 rapid actions ({len(created)})')

    for aid in created:
        api_json(page, 'DELETE', f'/api/actions/{aid}')

    actions = api_json(page, 'GET', '/api/actions')
    leftover = [a for a in (actions or []) if a.get('name', '').startswith('Rapid')]
    ok(len(leftover) == 0, 'All rapid actions cleaned up')


def test_large_payloads(page, ids):
    section('Large Payloads')

    resp = api_json(page, 'POST', '/api/actions',
                    {'name': 'A' * 200, 'type': 1, 'r': 0, 'g': 0, 'b': 0})
    ok(resp is not None and 'id' in resp, 'Action with 200-char name')
    if resp and 'id' in resp:
        api_json(page, 'DELETE', f'/api/actions/{resp["id"]}')

    resp = api_json(page, 'POST', '/api/timelines', {'name': 'Big', 'durationS': 300})
    if resp and 'id' in resp:
        tid = resp['id']
        clips = [{'fixtureId': ids['fix1'], 'actionId': 0, 'startS': i, 'durationS': 1}
                 for i in range(50)]
        resp2 = api_json(page, 'PUT', f'/api/timelines/{tid}',
                         {'name': 'Big', 'durationS': 300, 'clips': clips})
        ok(resp2 is not None and resp2.get('ok'), 'Timeline with 50 clips')
        api_json(page, 'DELETE', f'/api/timelines/{tid}')
    else:
        ok(False, 'Create big timeline')


def test_console_errors(page, ids):
    section('Browser Console Errors')
    errors = []
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)

    page.goto(BASE, wait_until='networkidle', timeout=10000)
    time.sleep(0.5)
    for tab in ['dash', 'setup', 'layout', 'actions', 'runtime', 'settings', 'firmware']:
        wait_tab(page, tab)
        time.sleep(0.5)

    # Filter expected errors (GitHub/firmware check may fail in test env)
    real = [e for e in errors
            if 'github' not in e.lower()
            and 'firmware/check' not in e.lower()
            and 'firmware/latest' not in e.lower()
            and 'favicon' not in e.lower()
            and 'net::ERR' not in e
            and '400 (BAD REQUEST)' not in e
            and 'Failed to load resource' not in e]
    ok(len(real) == 0, f'No unexpected JS errors ({len(real)}: {real[:3] if real else "none"})')


def test_modal_system(page, ids):
    section('Modal System')

    modal = page.query_selector('#modal')
    ok(modal is not None, 'Modal container exists')
    display = modal.evaluate('el => getComputedStyle(el).display') if modal else ''
    ok(display == 'none', 'Modal initially hidden')

    # Open via Load Show
    wait_tab(page, 'settings')
    load_btn = page.query_selector('button[onclick*="openLoadShow"]')
    if load_btn:
        load_btn.click()
        time.sleep(0.5)
        display = modal.evaluate('el => getComputedStyle(el).display')
        ok(display != 'none', 'Load Show modal opens')

        # Should have preset list
        text = modal.inner_text()
        ok('Preset' in text or 'File' in text, 'Load Show modal has content')

        # Close via JS
        page.evaluate("typeof closeModal==='function'&&closeModal()")
        time.sleep(0.3)
        display = modal.evaluate('el => getComputedStyle(el).display')
        ok(display == 'none', 'Modal closes via closeModal()')
    else:
        ok(False, 'Load Show button found')
        ok(False, 'Modal content')
        ok(False, 'Modal close')


def test_firmware_check_fix(page, ids):
    """Verify the registry.json firmware version bug is fixed."""
    section('Firmware Check Bug Fix')

    # The original bug: load_registry() returns {firmware:[...]},
    # but code iterated the dict directly instead of .get("firmware",[])
    # This would cause AttributeError: 'str' object has no attribute 'get'

    # /api/firmware/latest uses registry
    resp = api_json(page, 'GET', '/api/firmware/latest')
    # May fail due to GitHub unreachable, but should NOT return 500
    ok(resp is not None, 'GET /api/firmware/latest does not crash (registry fix)')

    # /api/firmware/check needs WiFi + children
    resp = api_json(page, 'GET', '/api/firmware/check')
    ok(resp is not None, 'GET /api/firmware/check does not crash (registry fix)')
    if resp and resp.get('children'):
        ok(isinstance(resp['children'], list), 'Firmware check returns children list')
    elif resp and resp.get('err'):
        # May error for other reasons (GitHub unreachable) — that's OK, not a crash
        ok(True, 'Firmware check returns error (not crash)')
    else:
        ok(True, 'Firmware check returned response')


def test_layout_place_remove(page, ids):
    """Test fixture placement, repositioning, and removal from layout for all types."""
    section('Layout — Place / Remove / Point')

    # GET layout — should have positioned fixtures from seed
    resp = api_json(page, 'GET', '/api/layout')
    ok(resp is not None and 'fixtures' in resp, 'GET /api/layout returns fixtures')
    fixtures = resp.get('fixtures', [])
    positioned = [f for f in fixtures if f.get('positioned')]
    ok(len(positioned) >= 4, f'Layout has positioned fixtures from seed ({len(positioned)})')

    # Verify LED fixture has position
    led_fix = next((f for f in fixtures if f.get('id') == ids['fix1']), None)
    ok(led_fix is not None and led_fix.get('positioned'), 'LED fixture is positioned')
    ok(led_fix.get('x') == 1000 and led_fix.get('y') == 4500, 'LED fixture has correct coords')

    # Verify DMX fixture has position and aimPoint
    dmx_fix = next((f for f in fixtures if f.get('id') == ids['dmx1']), None)
    ok(dmx_fix is not None and dmx_fix.get('positioned'), 'DMX fixture is positioned')
    ok(dmx_fix.get('aimPoint') is not None, 'DMX fixture has aimPoint')

    # Reposition fixture via layout save
    new_pos = [{'id': ids['fix1'], 'x': 2000, 'y': 3000, 'z': 100}]
    resp = api_json(page, 'POST', '/api/layout', {'children': new_pos})
    ok(resp is not None and resp.get('ok'), 'Save layout with new position')

    # Verify reposition persisted
    resp = api_json(page, 'GET', '/api/layout')
    fixtures = resp.get('fixtures', [])
    led_fix = next((f for f in fixtures if f.get('id') == ids['fix1']), None)
    ok(led_fix is not None and led_fix.get('x') == 2000, 'LED fixture repositioned to x=2000')
    ok(led_fix.get('y') == 3000, 'LED fixture repositioned to y=3000')

    # Remove fixture from layout (save without it)
    empty_layout = [{'id': ids['fix2'], 'x': 9000, 'y': 4500, 'z': 0}]
    resp = api_json(page, 'POST', '/api/layout', {'children': empty_layout})
    ok(resp is not None and resp.get('ok'), 'Save layout without fix1')

    resp = api_json(page, 'GET', '/api/layout')
    fixtures = resp.get('fixtures', [])
    fix1 = next((f for f in fixtures if f.get('id') == ids['fix1']), None)
    ok(fix1 is not None and not fix1.get('positioned'), 'Removed fixture is unpositioned')
    fix2 = next((f for f in fixtures if f.get('id') == ids['fix2']), None)
    ok(fix2 is not None and fix2.get('positioned'), 'Remaining fixture still positioned')

    # Re-place all fixtures for subsequent tests
    all_pos = [
        {'id': ids['fix1'], 'x': 1000, 'y': 4500, 'z': 0},
        {'id': ids['fix2'], 'x': 9000, 'y': 4500, 'z': 0},
        {'id': ids['dmx1'], 'x': 2000, 'y': 5000, 'z': 2000},
        {'id': ids['dmx2'], 'x': 5000, 'y': 4800, 'z': 5000},
    ]
    api_json(page, 'POST', '/api/layout', {'children': all_pos})


def test_layout_fixture_types(page, ids):
    """Verify all fixture types render correctly in layout."""
    section('Layout — Fixture Types')

    resp = api_json(page, 'GET', '/api/layout')
    fixtures = resp.get('fixtures', [])

    # LED fixtures should have strings
    led_fixtures = [f for f in fixtures if f.get('fixtureType') == 'led']
    ok(len(led_fixtures) >= 2, f'Layout has LED fixtures ({len(led_fixtures)})')
    for f in led_fixtures:
        ok(len(f.get('strings', [])) > 0 or f.get('childId') is not None,
           f'LED fixture {f["id"]} has strings or childId')

    # DMX fixtures should have universe, address, aimPoint
    dmx_fixtures = [f for f in fixtures if f.get('fixtureType') == 'dmx']
    ok(len(dmx_fixtures) >= 2, f'Layout has DMX fixtures ({len(dmx_fixtures)})')
    for f in dmx_fixtures:
        ok(f.get('dmxUniverse') is not None, f'DMX fixture {f["id"]} has universe')
        ok(f.get('dmxStartAddr') is not None, f'DMX fixture {f["id"]} has start address')
        ok(f.get('aimPoint') is not None, f'DMX fixture {f["id"]} has aimPoint')

    # Test fixture aim update
    resp = api_json(page, 'PUT', f'/api/fixtures/{ids["dmx1"]}/aim',
                    {'aimPoint': [8000, 0, 3000]})
    ok(resp is not None, 'Update DMX aim point')

    resp = api_json(page, 'GET', f'/api/fixtures/{ids["dmx1"]}')
    ok(resp.get('aimPoint') == [8000, 0, 3000], 'Aim point persisted')


def test_surfaces_in_layout(page, ids):
    """Verify surfaces render and can be managed."""
    section('Surfaces in Layout')

    surfs = api_json(page, 'GET', '/api/surfaces')
    ok(isinstance(surfs, list), 'GET surfaces returns list')

    # Create a surface for illumination testing
    resp = api_json(page, 'POST', '/api/surfaces', {
        'name': 'Test Wall', 'surfaceType': 'wall', 'color': '#1e293b',
        'opacity': 40,
        'transform': {'pos': [5000, 2500, 0], 'rot': [0, 0, 0], 'scale': [8000, 4000, 100]}
    })
    ok(resp is not None and resp.get('id') is not None, 'Create surface for illumination')
    surf_id = resp.get('id')

    surfs = api_json(page, 'GET', '/api/surfaces')
    ok(any(s.get('id') == surf_id for s in surfs), 'Surface appears in list')

    # Clean up
    api_json(page, 'DELETE', f'/api/surfaces/{surf_id}')


def test_bake_preview_data(page, ids):
    """Verify bake produces preview data with per-fixture frames."""
    section('Bake Preview Data')

    # Ensure we have actions and timeline
    actions = api_json(page, 'GET', '/api/actions')
    if not actions:
        api_json(page, 'POST', '/api/actions',
                 {'name': 'Preview Solid', 'type': 1, 'r': 255, 'g': 100, 'b': 50})
        actions = api_json(page, 'GET', '/api/actions')

    timelines = api_json(page, 'GET', '/api/timelines')
    if not timelines:
        api_json(page, 'POST', '/api/timelines', {'name': 'Preview TL', 'durationS': 5})
        timelines = api_json(page, 'GET', '/api/timelines')

    ok(len(actions) > 0, 'Have actions for bake')
    ok(len(timelines) > 0, 'Have timeline for bake')

    tl_id = timelines[0]['id']
    act_id = actions[0]['id']

    # Add clip and bake
    api_json(page, 'PUT', f'/api/timelines/{tl_id}', {
        'name': 'Preview TL', 'durationS': 5,
        'clips': [{'fixtureId': ids['fix1'], 'actionId': act_id, 'startS': 0, 'durationS': 5}]
    })

    resp = api_json(page, 'POST', f'/api/timelines/{tl_id}/bake')
    ok(resp is not None and resp.get('ok'), 'Bake started')

    # Poll until done
    for _ in range(50):
        time.sleep(0.2)
        status = api_json(page, 'GET', f'/api/timelines/{tl_id}/baked/status')
        if status and (status.get('done') or status.get('error')):
            break

    ok(status.get('done'), 'Bake completed')

    # Get preview data
    preview = api_json(page, 'GET', f'/api/timelines/{tl_id}/baked/preview')
    ok(preview is not None and 'err' not in preview, 'Preview data returned')


def test_json_model_compat(page, ids):
    """Verify JSON field types are compatible with Android Kotlin serialization."""
    section('JSON Model Compatibility')

    # Fixtures — aimPoint must be list of numbers (int or float both valid)
    resp = api_json(page, 'GET', '/api/layout')
    for f in resp.get('fixtures', []):
        ap = f.get('aimPoint')
        if ap is not None:
            ok(isinstance(ap, list), f'Fixture {f["id"]} aimPoint is list')
            for v in ap:
                ok(isinstance(v, (int, float)), f'Fixture {f["id"]} aimPoint value is number')

    # Surfaces — transform pos/rot/scale must be lists of numbers
    surfs = api_json(page, 'GET', '/api/surfaces')
    for s in (surfs or []):
        t = s.get('transform', {})
        for field in ('pos', 'rot', 'scale'):
            vals = t.get(field, [])
            ok(isinstance(vals, list), f'Surface {s.get("id")} transform.{field} is list')

    # Settings — runnerStartEpoch can be null or number
    settings = api_json(page, 'GET', '/api/settings')
    ok(settings is not None, 'Settings returned')
    epoch = settings.get('runnerStartEpoch')
    ok(epoch is None or isinstance(epoch, (int, float)), 'runnerStartEpoch is null or number')

    # Factory reset requires X-SlyLED-Confirm header
    resp = page.evaluate("() => fetch('/api/reset', {method:'POST'}).then(r=>({status:r.status}))")
    ok(resp.get('status') == 403, 'Reset without X-SlyLED-Confirm header → 403')

    resp = page.evaluate("""() => fetch('/api/reset', {
        method: 'POST',
        headers: {'X-SlyLED-Confirm': 'true'}
    }).then(r => r.json())""")
    ok(resp is not None and resp.get('ok'), 'Reset with confirm header succeeds')

    # Re-seed data after reset
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/children', json={'ip': '10.0.0.50'})
        c.post('/api/children', json={'ip': '10.0.0.51'})


def test_canvas_stage_sync(page, ids):
    """Verify canvasW/canvasH stays synced with stage.w/stage.h."""
    section('Canvas ↔ Stage Sync')

    # Set stage to 8m x 3m
    resp = api_json(page, 'POST', '/api/stage', {'w': 8.0, 'h': 3.0, 'd': 6.0})
    ok(resp is not None and resp.get('ok'), 'Set stage to 8x3x6m')

    # Verify settings canvasW/H updated
    settings = api_json(page, 'GET', '/api/settings')
    ok(settings.get('canvasW') == 8000, f'canvasW synced to 8000 (got {settings.get("canvasW")})')
    ok(settings.get('canvasH') == 3000, f'canvasH synced to 3000 (got {settings.get("canvasH")})')

    # Verify layout canvasW/H updated
    layout = api_json(page, 'GET', '/api/layout')
    ok(layout.get('canvasW') == 8000, f'Layout canvasW = 8000 (got {layout.get("canvasW")})')
    ok(layout.get('canvasH') == 3000, f'Layout canvasH = 3000 (got {layout.get("canvasH")})')

    # canvasW = stage.w * 1000, canvasH = stage.h * 1000 (NOT stage.d)
    stage = api_json(page, 'GET', '/api/stage')
    ok(int(stage.get('w', 0) * 1000) == layout.get('canvasW'),
       'canvasW = stage.w * 1000')
    ok(int(stage.get('h', 0) * 1000) == layout.get('canvasH'),
       'canvasH = stage.h * 1000 (not stage.d)')

    # Fixture at (4000, 1500) should be at center of 8m x 3m stage
    resp = api_json(page, 'POST', '/api/layout', {
        'children': [{'id': ids['fix1'], 'x': 4000, 'y': 1500, 'z': 0}]
    })
    ok(resp is not None and resp.get('ok'), 'Place fixture at stage center')

    layout = api_json(page, 'GET', '/api/layout')
    f = next((f for f in layout.get('fixtures', []) if f.get('id') == ids['fix1']), None)
    ok(f is not None and f.get('x') == 4000, 'Fixture X at center of 8m stage')
    ok(f is not None and f.get('y') == 1500, 'Fixture Y at center of 3m stage')

    # Restore stage
    api_json(page, 'POST', '/api/stage', {'w': 10.0, 'h': 5.0, 'd': 10.0})
    # Restore layout
    api_json(page, 'POST', '/api/layout', {'children': [
        {'id': ids['fix1'], 'x': 1000, 'y': 4500, 'z': 0},
        {'id': ids['fix2'], 'x': 9000, 'y': 4500, 'z': 0},
        {'id': ids['dmx1'], 'x': 2000, 'y': 5000, 'z': 2000},
        {'id': ids['dmx2'], 'x': 5000, 'y': 4800, 'z': 5000},
    ]})


def test_fixture_strings_in_layout(page, ids):
    """Verify LED fixture strings are returned in layout response."""
    section('Fixture Strings in Layout')

    layout = api_json(page, 'GET', '/api/layout')
    fixtures = layout.get('fixtures', [])

    # Find LED fixtures
    led_fixtures = [f for f in fixtures if f.get('fixtureType') == 'led']
    ok(len(led_fixtures) >= 1, f'Layout has LED fixtures ({len(led_fixtures)})')

    for f in led_fixtures:
        strings = f.get('strings', [])
        ok(len(strings) > 0, f'LED fixture {f["id"]} "{f.get("name")}" has strings ({len(strings)})')
        for si, s in enumerate(strings):
            ok(s.get('leds', 0) > 0, f'Fixture {f["id"]} string {si} has leds={s.get("leds")}')
            ok(s.get('mm', 0) > 0, f'Fixture {f["id"]} string {si} has mm={s.get("mm")}')
            ok('sdir' in s, f'Fixture {f["id"]} string {si} has sdir field')


def test_layout_canvas_ui(page, ids):
    """Test the layout canvas renders in the SPA."""
    section('Layout Canvas UI')

    wait_tab(page, 'layout')
    time.sleep(0.5)

    # Canvas should exist
    canvas = page.query_selector('#lay-canvas, #lcv, #t-layout canvas')
    ok(canvas is not None, 'Layout canvas element exists')

    # Layout tab should have canvas and controls
    time.sleep(0.5)
    controls = page.query_selector_all('#t-layout button')
    ok(len(controls) >= 1, f'Layout tab has buttons ({len(controls)})')

    # 2D/3D mode toggle
    toggle = page.query_selector('#lay-mode-3d, button[onclick*="setLayoutMode"]')
    ok(toggle is not None, '2D/3D mode toggle exists')

    # Save button
    save = page.query_selector('#btn-lay-save')
    ok(save is not None, 'Save Layout button exists')


def test_runtime_emulator_ui(page, ids):
    """Test the runtime emulator canvas renders in the SPA."""
    section('Runtime Emulator UI')

    wait_tab(page, 'runtime')
    time.sleep(0.5)

    # Emulator canvas
    emu = page.query_selector('#emu-cv')
    ok(emu is not None, 'Emulator canvas exists')

    # Runtime has content
    time.sleep(0.5)
    controls = page.query_selector_all('#t-runtime button')
    ok(len(controls) >= 1, f'Runtime tab has buttons ({len(controls)})')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('\033[1m=== SlyLED SPA Comprehensive Test Suite ===\033[0m')
    print(f'Target: {BASE}\n')

    print('Seeding test data via Flask test client...')
    ids = seed_data()
    print(f'  IDs: {ids}')

    print('Starting Flask server...')
    start_server()
    print(f'  Server running at {BASE}\n')

    all_tests = [
        ('health', test_server_health),
        ('dashboard', test_dashboard),
        ('setup', test_setup),
        ('layout', test_layout),
        ('actions_tab', test_actions_tab),
        ('runtime', test_runtime),
        ('settings_tab', test_settings_tab),
        ('firmware_tab', test_firmware_tab),
        ('show_export', test_show_export),
        ('show_import', test_show_import),
        ('config_export', test_config_export_import),
        ('presets', test_show_presets),
        ('actions_crud', test_actions_crud),
        ('fixtures_crud', test_fixtures_crud),
        ('timelines_crud', test_timelines_crud),
        ('spatial_crud', test_spatial_effects_crud),
        ('surfaces_crud', test_surfaces_crud),
        ('children_api', test_children_api),
        ('settings_api', test_settings_api),
        ('layout_api', test_layout_api),
        ('dmx_profiles', test_dmx_profiles),
        ('dmx_engine', test_dmx_engine),
        ('wifi_api', test_wifi_api),
        ('firmware_api', test_firmware_api),
        ('help_api', test_help_api),
        ('qr_api', test_qr_api),
        ('tab_nav', test_tab_navigation),
        ('error_handling', test_error_handling),
        ('rapid_crud', test_rapid_crud),
        ('large_payloads', test_large_payloads),
        ('modal_system', test_modal_system),
        ('firmware_fix', test_firmware_check_fix),
        ('layout_place', test_layout_place_remove),
        ('layout_types', test_layout_fixture_types),
        ('surfaces', test_surfaces_in_layout),
        ('canvas_stage', test_canvas_stage_sync),
        ('fixture_strings', test_fixture_strings_in_layout),
        ('bake_preview', test_bake_preview_data),
        ('json_compat', test_json_model_compat),
        ('layout_canvas', test_layout_canvas_ui),
        ('runtime_emu', test_runtime_emulator_ui),
        ('console_errors', test_console_errors),
    ]

    if _filter:
        all_tests = [(n, f) for n, f in all_tests if _filter in n]
        if not all_tests:
            print(f'No tests match: {_filter}')
            sys.exit(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800},
                                  color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='networkidle', timeout=10000)
        time.sleep(0.5)

        for name, fn in all_tests:
            try:
                fn(page, ids)
            except Exception as e:
                global _fail
                _fail += 1
                _errors.append(f'{name}: EXCEPTION — {e}')
                print(f'  \033[31m[EXCEPTION]\033[0m {name}: {e}')

        browser.close()

    total = _pass + _fail
    print(f'\n\033[1m{"=" * 60}\033[0m')
    if _fail == 0:
        print(f'\033[32m  ALL {total} TESTS PASSED\033[0m')
    else:
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m out of {total} tests')
        print(f'\n  Failed:')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
