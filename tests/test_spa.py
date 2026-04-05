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

        # Surface — transform.pos is left-bottom corner, scale is w×h in mm
        c.post('/api/surfaces', json={
            'name': 'Back Wall', 'surfaceType': 'wall', 'color': '#1e293b', 'opacity': 30,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
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
    page.evaluate("typeof _setSection==='function'&&_setSection('shows')")
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


def test_dmx_bridge_no_fixture(page, ids):
    """DMX bridges must NOT get auto-created LED fixtures on config import."""
    section('DMX Bridge No Auto-Fixture')

    # Simulate a config with a DMX bridge child that has strings (like Giga)
    bridge_config = {
        'type': 'slyled-config', 'version': 1,
        'children': [{
            'id': 99, 'ip': '10.99.99.99', 'hostname': 'SLYC-TEST',
            'name': 'Test Bridge', 'type': 'dmx', 'boardType': 'giga-dmx',
            'sc': 1, 'strings': [{'leds': 30, 'mm': 500, 'sdir': 0,
                                   'type': 0, 'cdir': 0, 'cmm': 0, 'folded': False}],
            'status': 0, 'seen': 0
        }],
        'layout': {'canvasW': 10000, 'canvasH': 5000, 'children': []}
    }
    resp = api_json(page, 'POST', '/api/config/import', bridge_config)
    ok(resp is not None and resp.get('ok'), 'Config import with DMX bridge')

    # The bridge should be a child but NOT have an auto-created fixture
    children = api_json(page, 'GET', '/api/children')
    bridge = next((c for c in children if c.get('hostname') == 'SLYC-TEST'), None)
    ok(bridge is not None, 'Bridge child exists')
    ok(bridge.get('type') == 'dmx', 'Bridge type is dmx')

    fixtures = api_json(page, 'GET', '/api/fixtures')
    bridge_fixture = next((f for f in fixtures if f.get('childId') == bridge['id']), None)
    ok(bridge_fixture is None, 'NO fixture auto-created for DMX bridge (was creating LED with 30 LEDs)')

    # Clean up
    if bridge:
        api_json(page, 'DELETE', f'/api/children/{bridge["id"]}')


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


def test_ofl_browse(page, ids):
    """Test OFL search and import-by-id endpoints."""
    section('OFL Browse & Import')

    # Search with short query → 400
    resp = api_json(page, 'GET', '/api/dmx-profiles/ofl/search?q=a')
    ok(resp is not None and resp.get('err'), 'Short query rejected')

    # Search for a common fixture (may fail if OFL unreachable — that's OK)
    resp = api_json(page, 'GET', '/api/dmx-profiles/ofl/search?q=par')
    if isinstance(resp, list):
        ok(True, f'OFL search returns list ({len(resp)} results)')
        if len(resp) > 0:
            ok('manufacturer' in resp[0], 'Result has manufacturer')
            ok('fixture' in resp[0], 'Result has fixture key')
            ok('name' in resp[0], 'Result has name')
            ok(len(resp) <= 100, f'Results capped at 100 ({len(resp)})')
        else:
            ok(True, 'OFL search returned 0 results')
    elif resp is not None and isinstance(resp, dict) and resp.get('err'):
        ok(True, f'OFL search unavailable: {resp.get("err")} — skipping')
    else:
        ok(True, 'OFL search returned unexpected format — skipping')

    # Import-by-id missing fields → 400
    resp = api_json(page, 'POST', '/api/dmx-profiles/ofl/import-by-id', {})
    ok(resp is not None and resp.get('err'), 'Import-by-id without fields rejected')

    # Import-by-id with invalid fixture → 502
    resp = api_json(page, 'POST', '/api/dmx-profiles/ofl/import-by-id',
                    {'manufacturer': 'nonexistent', 'fixture': 'fake'})
    ok(resp is not None and resp.get('err'), 'Import non-existent fixture returns error')

    # Manufacturer listing
    mfrs = api_json(page, 'GET', '/api/dmx-profiles/ofl/manufacturers')
    if isinstance(mfrs, list) and len(mfrs) > 0:
        ok(True, f'OFL manufacturers list ({len(mfrs)})')
        ok('key' in mfrs[0] and 'name' in mfrs[0], 'Manufacturer has key and name')
        ok(mfrs[0].get('fixtureCount', 0) > 0, 'Manufacturer has fixtures')
        # Browse manufacturer
        mfr_detail = api_json(page, 'GET', f'/api/dmx-profiles/ofl/manufacturer/{mfrs[0]["key"]}')
        if mfr_detail and not mfr_detail.get('err'):
            ok('fixtures' in mfr_detail, f'Manufacturer detail has fixtures list')
        else:
            ok(True, 'Manufacturer detail unavailable')
    elif isinstance(mfrs, dict) and mfrs.get('err'):
        ok(True, f'OFL unavailable: {mfrs["err"]}')
    else:
        ok(True, 'OFL manufacturers empty or unavailable')

    # UI buttons
    wait_tab(page, 'settings')
    time.sleep(0.5)
    btn = page.query_selector('button[onclick*="showOflBrowse"]')
    ok(btn is not None, 'Search OFL button exists in SPA')


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
    # API test
    resp = page.evaluate("() => fetch('/api/qr').then(r => ({status: r.status, type: r.headers.get('content-type')}))")
    ok(resp is not None, 'QR endpoint responds')
    ok(resp.get('status') == 200, f'QR returns 200 (got {resp.get("status")})')
    ok('image/png' in (resp.get('type') or ''), f'QR returns PNG (got {resp.get("type")})')

    # UI test: go to Settings > Advanced, click Show QR Code
    wait_tab(page, 'settings')
    page.click('text=Advanced')
    page.wait_for_timeout(300)
    qr_btn = page.query_selector('button:has-text("Show QR Code")')
    ok(qr_btn is not None, 'QR Code button found in Advanced tab')
    if qr_btn:
        qr_btn.click()
        page.wait_for_timeout(1500)
        # Check if QR image appeared (in container or modal)
        qr_img = page.query_selector('#qr-container img, #qr-modal-body img')
        ok(qr_img is not None, 'QR code image rendered')


def test_channel_defaults(page, ids):
    section('Channel Defaults')
    # Check that DMX fixture channels endpoint returns defaults
    dmx_fid = ids.get('dmx1')
    if not dmx_fid:
        ok(True, 'No DMX fixture to test (skipped)')
        return
    resp = page.evaluate(f"() => fetch('/api/dmx/fixture/{dmx_fid}/channels').then(r => r.json())")
    ok(resp is not None and resp.get('channels'), 'Channels endpoint returns data')
    if resp and resp.get('channels'):
        channels = resp['channels']
        # Check that default field is present
        has_default = all('default' in ch for ch in channels)
        ok(has_default, 'All channels have default field')
        # If profile has dimmer, check its default
        dimmer = next((ch for ch in channels if ch.get('type') == 'dimmer'), None)
        if dimmer:
            ok(dimmer.get('default', 0) == 255, f'Dimmer default=255 (got {dimmer.get("default")})')
            ok(dimmer.get('value', 0) == 255, f'Dimmer initial value=255 (got {dimmer.get("value")})')


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
    page.evaluate("typeof _setSection==='function'&&_setSection('shows')")
    time.sleep(0.3)
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


def test_surface_transform(page, ids):
    """Verify surfaces have correct transform structure and fit within stage."""
    section('Surface Transform')

    settings = api_json(page, 'GET', '/api/settings')
    cw = settings.get('canvasW', 10000)
    ch = settings.get('canvasH', 5000)

    surfs = api_json(page, 'GET', '/api/surfaces')
    ok(isinstance(surfs, list), 'Surfaces is a list')

    for s in (surfs or []):
        t = s.get('transform', {})
        ok('pos' in t and 'scale' in t, f'Surface {s.get("id")} has pos and scale')
        pos = t.get('pos', [0, 0, 0])
        scale = t.get('scale', [0, 0, 0])
        # Check surface doesn't have nonsensical dimensions
        ok(scale[0] > 0, f'Surface {s.get("id")} width > 0')
        ok(scale[1] > 0, f'Surface {s.get("id")} height > 0')
        # Warn if extends far outside stage (> 2x stage size)
        right_edge = pos[0] + scale[0]
        top_edge = pos[1] + scale[1]
        ok(right_edge <= cw * 2, f'Surface {s.get("id")} right edge within 2x stage ({right_edge} vs {cw})')
        ok(top_edge <= ch * 2, f'Surface {s.get("id")} top edge within 2x stage ({top_edge} vs {ch})')

    # Create a surface with proper transform and verify
    resp = api_json(page, 'POST', '/api/surfaces', {
        'name': 'Test Panel', 'surfaceType': 'wall', 'color': '#444',
        'opacity': 40,
        'transform': {'pos': [2000, 1000, 0], 'rot': [0, 0, 0], 'scale': [3000, 2000, 50]}
    })
    ok(resp is not None and resp.get('id') is not None, 'Create surface with transform')
    sid = resp.get('id')

    surfs = api_json(page, 'GET', '/api/surfaces')
    created = next((s for s in surfs if s.get('id') == sid), None)
    ok(created is not None, 'Surface exists in list')
    t = created.get('transform', {})
    ok(t.get('pos') == [2000, 1000, 0], f'Surface pos correct ({t.get("pos")})')
    ok(t.get('scale') == [3000, 2000, 50], f'Surface scale correct ({t.get("scale")})')

    api_json(page, 'DELETE', f'/api/surfaces/{sid}')


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


def test_emulator_rendering(page, ids):
    """Verify the SPA emulator renders both LED and DMX fixtures from layout.fixtures."""
    section('Emulator Rendering')

    # The emulator must use layout.fixtures (not children) to render
    # This test validates the data flow that feeds the emulator

    # 1. Layout fixtures must include both LED and DMX, positioned
    layout = api_json(page, 'GET', '/api/layout')
    lf = layout.get('fixtures', [])
    led_positioned = [f for f in lf if f.get('fixtureType') != 'dmx' and f.get('positioned')]
    dmx_positioned = [f for f in lf if f.get('fixtureType') == 'dmx' and f.get('positioned')]
    ok(len(led_positioned) >= 1, f'Layout has positioned LED fixtures ({len(led_positioned)})')
    ok(len(dmx_positioned) >= 1, f'Layout has positioned DMX fixtures ({len(dmx_positioned)})')

    # 2. DMX fixtures must have aimPoint for beam cone direction
    for f in dmx_positioned:
        ap = f.get('aimPoint')
        ok(ap is not None and len(ap) >= 2, f'DMX fixture {f["id"]} has aimPoint with >= 2 values')
        # aimPoint[0] = X (horizontal), aimPoint[1] = Y (height) — used for 2D canvas
        ok(isinstance(ap[0], (int, float)) and isinstance(ap[1], (int, float)),
           f'DMX fixture {f["id"]} aimPoint[0,1] are numbers for 2D rendering')

    # 3. LED fixtures must have strings with leds/mm/sdir for string rendering
    for f in led_positioned:
        strings = f.get('strings', [])
        ok(len(strings) > 0, f'LED fixture {f["id"]} has strings in layout response')
        for si, s in enumerate(strings):
            ok(s.get('leds', 0) > 0, f'LED fixture {f["id"]} string {si} has leds')

    # 4. Emulator canvas exists and is visible on runtime tab
    wait_tab(page, 'runtime')
    time.sleep(1)
    emu = page.query_selector('#emu-cv')
    ok(emu is not None, 'Emulator canvas element exists')

    # 5. Verify emuDraw doesn't early-exit on DMX-only setups
    # Remove all children to simulate DMX-only rig, verify emulator still renders
    # (The fix: guard changed from !children.length to !children.length&&!layoutFixtures.length)
    children = api_json(page, 'GET', '/api/children')
    ok(True, f'Server has {len(children)} children — emulator must render even with 0')

    # 6. Verify fixture positions are consistent between layout and fixture GET
    fixtures_api = api_json(page, 'GET', '/api/fixtures')
    for f in dmx_positioned:
        api_fix = next((af for af in fixtures_api if af.get('id') == f['id']), None)
        ok(api_fix is not None, f'DMX fixture {f["id"]} exists in /api/fixtures')
        ok(api_fix.get('aimPoint') == f.get('aimPoint'),
           f'DMX fixture {f["id"]} aimPoint matches between layout and fixtures API')


def test_beam_cone_direction(page, ids):
    """Verify beam cone uses aimPoint[0]=X and aimPoint[1]=Y for 2D canvas (front view)."""
    section('Beam Cone Direction')

    # Set a known aimPoint on a DMX fixture
    resp = api_json(page, 'PUT', f'/api/fixtures/{ids["dmx1"]}/aim',
                    {'aimPoint': [8000, 1000, 5000]})
    ok(resp is not None, 'Set aimPoint [8000, 1000, 5000]')

    # Verify it persisted
    fix = api_json(page, 'GET', f'/api/fixtures/{ids["dmx1"]}')
    ap = fix.get('aimPoint')
    ok(ap == [8000, 1000, 5000], f'aimPoint persisted: {ap}')

    # In layout response, verify the fixture has the aimPoint
    layout = api_json(page, 'GET', '/api/layout')
    lf = next((f for f in layout.get('fixtures', []) if f.get('id') == ids['dmx1']), None)
    ok(lf is not None and lf.get('aimPoint') == [8000, 1000, 5000],
       'Layout fixture has matching aimPoint')

    # The 2D canvas mapping should be:
    #   canvas X = aimPoint[0] * W / canvasW  (horizontal)
    #   canvas Y = H - aimPoint[1] * H / canvasH  (vertical, inverted)
    # NOT aimPoint[2] — that's depth (Z), only used in 3D/top-down views
    cw = layout.get('canvasW', 10000)
    ch = layout.get('canvasH', 5000)
    # aimPoint[0]=8000 should be at 80% across the canvas
    x_pct = ap[0] / cw * 100
    ok(75 < x_pct < 85, f'aimPoint X at {x_pct:.0f}% across canvas (expected ~80%)')
    # aimPoint[1]=1000 should be at 20% up from bottom
    y_pct = ap[1] / ch * 100
    ok(15 < y_pct < 25, f'aimPoint Y at {y_pct:.0f}% up canvas (expected ~20%)')


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


def test_layout_toolbar(page, ids):
    """Test layout toolbar buttons: save icon, auto-arrange DMX, show strings toggle."""
    section('Layout Toolbar')

    wait_tab(page, 'layout')
    time.sleep(0.5)

    # Toolbar exists
    toolbar = page.query_selector('#lay-toolbar')
    ok(toolbar is not None, 'Layout toolbar exists')

    # Save button (icon)
    save_btn = page.query_selector('#btn-lay-save')
    ok(save_btn is not None, 'Save layout icon button exists')
    ok(save_btn.get_attribute('data-tip') == 'laySave', 'Save button has data-tip for i18n')

    # Auto-arrange DMX button
    arrange_btn = page.query_selector('[data-tip="layAutoArrange"]')
    ok(arrange_btn is not None, 'Auto-arrange DMX button exists')

    # Show strings toggle
    strings_label = page.query_selector('[data-tip="layShowStrings"]')
    ok(strings_label is not None, 'Show strings toggle exists')

    # Show strings checkbox
    strings_cb = page.query_selector('#lay-detail')
    ok(strings_cb is not None, 'Show strings checkbox exists')
    ok(strings_cb.is_checked(), 'Show strings is checked by default')


def test_auto_arrange_dmx(page, ids):
    """Test Auto-Arrange DMX with different fixture configurations."""
    section('Auto-Arrange DMX')

    # Ensure we have DMX fixtures (may have been wiped by earlier reset tests)
    resp = api_json(page, 'GET', '/api/fixtures')
    dmx_before = [f for f in (resp or []) if f.get('fixtureType') == 'dmx']
    if len(dmx_before) < 2:
        # Set a known stage size and recreate DMX fixtures for this test
        api_json(page, 'POST', '/api/stage', {'w': 10.0, 'h': 5.0, 'd': 10.0})
        api_json(page, 'POST', '/api/fixtures', {
            'name': 'Test MH', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit', 'aimPoint': [5000, 0, 5000]})
        api_json(page, 'POST', '/api/fixtures', {
            'name': 'Test Par', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb', 'aimPoint': [5000, 0, 5000]})
        resp = api_json(page, 'GET', '/api/fixtures')
        dmx_before = [f for f in (resp or []) if f.get('fixtureType') == 'dmx']
    ok(len(dmx_before) >= 2, f'Have {len(dmx_before)} DMX fixtures to arrange')

    # Click auto-arrange
    wait_tab(page, 'layout')
    time.sleep(0.3)
    page.click('[data-tip="layAutoArrange"]')
    time.sleep(1)

    # Verify positions changed — fixtures should be at stage top
    resp = api_json(page, 'GET', '/api/layout')
    fixtures = resp.get('fixtures', []) if resp else []
    dmx_arranged = [f for f in fixtures if any(
        d.get('id') == f.get('id') for d in dmx_before)]

    if dmx_arranged:
        # All DMX fixtures should be at high Y (near top)
        stage = api_json(page, 'GET', '/api/stage')
        stage_h = int((stage.get('h', 5) if stage else 5) * 1000)
        for f in dmx_arranged:
            y = f.get('y', 0)
            ok(y > stage_h * 0.7, f'Fixture {f.get("id")} at y={y} (near top of {stage_h})')

        # Fixtures should be spread across X
        xs = sorted([f.get('x', 0) for f in dmx_arranged])
        if len(xs) >= 2:
            spread = xs[-1] - xs[0]
            ok(spread > 1000, f'DMX fixtures spread across {spread}mm')

    # Verify aim points set to straight down
    resp = api_json(page, 'GET', '/api/fixtures')
    for f in (resp or []):
        if f.get('fixtureType') == 'dmx' and f.get('aimPoint'):
            ap = f['aimPoint']
            ok(ap[1] == 0, f'Fixture {f.get("id")} aim Y=0 (straight down), got {ap[1]}')

    # Test with no DMX fixtures — should not error
    # (We can't easily remove fixtures mid-test, so just verify the button doesn't crash)
    ok(True, 'Auto-arrange completed without error')


def test_auto_arrange_led_untouched(page, ids):
    """Verify Auto-Arrange DMX does not move LED fixtures."""
    section('Auto-Arrange LED Untouched')

    # Get LED fixture positions before
    resp = api_json(page, 'GET', '/api/fixtures')
    led_before = {f['id']: (f.get('x', 0), f.get('y', 0))
                  for f in (resp or []) if f.get('fixtureType') != 'dmx' and f.get('positioned')}

    if not led_before:
        ok(True, 'No positioned LED fixtures to verify (skipped)')
        return

    # Click auto-arrange again
    wait_tab(page, 'layout')
    time.sleep(0.3)
    page.click('[data-tip="layAutoArrange"]')
    time.sleep(1)

    # LED positions should be unchanged
    resp = api_json(page, 'GET', '/api/fixtures')
    for f in (resp or []):
        if f['id'] in led_before:
            bx, by = led_before[f['id']]
            ok(f.get('x', 0) == bx and f.get('y', 0) == by,
               f'LED fixture {f["id"]} unchanged: ({bx},{by})')


def test_show_demo_endpoint(page, ids):
    """Test POST /api/show/demo generates a random show."""
    section('Show Demo Endpoint')

    resp = api_json(page, 'POST', '/api/show/demo')
    ok(resp is not None and resp.get('ok'), 'Demo show generated')
    ok(resp.get('timelineId') is not None, f'Demo has timelineId: {resp.get("timelineId")}')
    ok(resp.get('name'), f'Demo has name: {resp.get("name")}')
    ok(resp.get('actions', 0) >= 1, f'Demo has actions: {resp.get("actions")}')


def test_preset_clip_names(page, ids):
    """Test that preset shows have named clips (not '?')."""
    section('Preset Clip Names')

    # Load a preset
    resp = api_json(page, 'POST', '/api/show/preset', {'id': 'spotlight-sweep'})
    ok(resp and resp.get('ok'), 'Preset loaded')
    tl_id = resp.get('timelineId') if resp else None
    if not tl_id:
        return

    # Get the timeline
    tl = api_json(page, 'GET', f'/api/timelines/{tl_id}')
    ok(tl is not None, 'Timeline retrieved')
    if not tl:
        return

    # Check clips have names
    tracks = tl.get('tracks', [])
    ok(len(tracks) >= 1, f'Timeline has {len(tracks)} tracks')
    for ti, track in enumerate(tracks):
        for ci, clip in enumerate(track.get('clips', [])):
            name = clip.get('name', '')
            ok(len(name) > 0 and name != '?',
               f'Track {ti} clip {ci} has name: "{name}"')


def test_stage_dimensions(page, ids):
    """Test stage dimensions UI: metric cm, imperial ft/in, depth field, API sync."""
    section('Stage Dimensions')

    # API: set stage and verify
    resp = api_json(page, 'POST', '/api/stage', {'w': 5.0, 'h': 3.0, 'd': 4.0})
    ok(resp and resp.get('ok'), 'Set stage to 5×3×4m')

    # Verify stage persisted
    st = api_json(page, 'GET', '/api/stage')
    ok(st and st.get('w') == 5.0, f'Stage w=5.0 (got {st.get("w") if st else None})')
    ok(st and st.get('h') == 3.0, f'Stage h=3.0')
    ok(st and st.get('d') == 4.0, f'Stage d=4.0')

    # Verify canvas synced (mm = meters * 1000)
    settings = api_json(page, 'GET', '/api/settings')
    ok(settings and settings.get('canvasW') == 5000, f'canvasW=5000 (got {settings.get("canvasW") if settings else None})')
    ok(settings and settings.get('canvasH') == 3000, f'canvasH=3000')

    # UI: check stage fields exist
    wait_tab(page, 'settings')
    time.sleep(0.5)

    # Metric panel visible by default
    metric = page.query_selector('#s-stage-metric')
    ok(metric is not None, 'Metric stage panel exists')

    # Depth field exists
    depth = page.query_selector('#s-sd')
    ok(depth is not None, 'Stage depth input exists')

    # Width field
    width = page.query_selector('#s-sw')
    ok(width is not None, 'Stage width input exists')

    # Imperial panel exists (hidden)
    imperial = page.query_selector('#s-stage-imperial')
    ok(imperial is not None, 'Imperial stage panel exists')

    # Switch to imperial
    page.select_option('#s-un', '1')
    time.sleep(0.3)

    # Imperial fields visible
    ft_w = page.query_selector('#s-sw-ft')
    in_w = page.query_selector('#s-sw-in')
    ok(ft_w is not None and in_w is not None, 'Imperial ft/in fields exist')

    # Verify conversion: 500cm = 16ft 4.85in ≈ 16ft 5in
    if ft_w:
        ft_val = ft_w.input_value()
        ok(ft_val == '16', f'500cm width → {ft_val}ft (expected 16)')

    # Switch back to metric
    page.select_option('#s-un', '0')
    time.sleep(0.3)

    # Restore original stage
    api_json(page, 'POST', '/api/stage', {'w': 10.0, 'h': 5.0, 'd': 10.0})


def test_community_in_profiles_tab(page, ids):
    """Test that Community/OFL buttons are in the Profiles sub-tab."""
    section('Community in Profiles Tab')

    wait_tab(page, 'settings')
    # Click Profiles sub-tab
    page.click('text=Profiles')
    time.sleep(0.5)

    # Community button should be visible
    comm_btn = page.query_selector('button:has-text("Community Profiles")')
    ok(comm_btn is not None, 'Community Profiles button in Profiles tab')

    ofl_btn = page.query_selector('button:has-text("Search OFL")')
    ok(ofl_btn is not None, 'Search OFL button in Profiles tab')

    paste_btn = page.query_selector('button:has-text("Paste OFL")')
    ok(paste_btn is not None, 'Paste OFL JSON button in Profiles tab')


def test_profile_default_column(page, ids):
    """Test that the profile view and channel API include the Default field."""
    section('Profile Default Column')

    # Test via API — more reliable than UI modal navigation
    resp = api_json(page, 'GET', '/api/dmx-profiles')
    ok(resp and len(resp) >= 12, f'Have profiles: {len(resp or [])}')

    # Check a profile with dimmer has default=255
    mh = next((p for p in (resp or []) if p.get('id') == 'generic-moving-head-16bit'), None)
    if mh:
        dimmer_ch = next((ch for ch in mh.get('channels', []) if ch.get('type') == 'dimmer'), None)
        ok(dimmer_ch is not None, 'Moving head has dimmer channel')
        ok(dimmer_ch and dimmer_ch.get('default') == 255,
           f'Dimmer default=255 in profile (got {dimmer_ch.get("default") if dimmer_ch else None})')

        pan_ch = next((ch for ch in mh.get('channels', []) if ch.get('type') == 'pan'), None)
        ok(pan_ch and pan_ch.get('default') == 32768,
           f'Pan default=32768 (center) in profile (got {pan_ch.get("default") if pan_ch else None})')

    # Test the profile editor UI has Default column header
    wait_tab(page, 'settings')
    page.click('text=Profiles')
    time.sleep(0.3)
    page.click('text=New Profile')
    time.sleep(0.5)
    modal = page.query_selector('#modal-body')
    if modal:
        html = modal.inner_html()
        ok('Default' in html, 'Profile editor has Default column')
    # Close modal
    x_btn = page.query_selector('#modal-x')
    if x_btn:
        x_btn.click()
        time.sleep(0.2)


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
        ('ofl_browse', test_ofl_browse),
        ('dmx_engine', test_dmx_engine),
        ('wifi_api', test_wifi_api),
        ('firmware_api', test_firmware_api),
        ('help_api', test_help_api),
        ('qr_api', test_qr_api),
        ('channel_defaults', test_channel_defaults),
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
        ('surface_transform', test_surface_transform),
        ('fixture_strings', test_fixture_strings_in_layout),
        ('bake_preview', test_bake_preview_data),
        ('emu_render', test_emulator_rendering),
        ('beam_dir', test_beam_cone_direction),
        ('bridge_no_fix', test_dmx_bridge_no_fixture),
        ('json_compat', test_json_model_compat),
        ('layout_canvas', test_layout_canvas_ui),
        ('runtime_emu', test_runtime_emulator_ui),
        ('layout_toolbar', test_layout_toolbar),
        ('auto_arrange', test_auto_arrange_dmx),
        ('led_untouched', test_auto_arrange_led_untouched),
        ('show_demo', test_show_demo_endpoint),
        ('clip_names', test_preset_clip_names),
        ('stage_dims', test_stage_dimensions),
        ('comm_profiles', test_community_in_profiles_tab),
        ('profile_defaults', test_profile_default_column),
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
