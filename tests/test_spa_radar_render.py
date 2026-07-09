#!/usr/bin/env python3
"""
test_spa_radar_render.py — Playwright tests for the SPA `radar` fixture-type
descriptor (#911, SPA half).

Seeds one radar fixture at a known pose (pos [1000, 500, 2000], yaw 30°)
plus one LED and one DMX fixture, then verifies against a live server:

  - Layout tab renders the radar as a box node + translucent coverage
    sector (fan fill + outline, tagged radarSector/cameraCone) with zero
    console errors.
  - Sector orientation: the outline's boresight (arc midpoint) points
    toward yaw +30° — three.js (sin30, 0, cos30)·range — matching the
    camera-cone convention (rz>0 aims toward +X / stage-left).
  - Sector visibility follows the Camera Cones view toggle (default OFF,
    like camera FOV cones).
  - Side panel: RDR badge, node binding, range (m), FoV (±deg), enabled
    state, "Feeds person tracking" note — and NO pre-#911 unknown-type
    "does not know" fallback notice.
  - Edit modal round-trips radarNode / rangeMm / fovDeg / radarEnabled,
    with validation on nonsense range/FoV values.
  - Runtime tab renders the same sector at lower opacity, zero errors.

Seeding: POSTs /api/fixtures with fixtureType 'radar' first; until the
server half of #911 (fixture_types.py radar descriptor) lands that returns
400, so the test falls back to injecting the record directly into the
fixtures store (parent_server._fixtures + _save). Round-trip assertions on
radar-only fields (radarNode/rangeMm/radarEnabled) are skipped with a NOTE
in fallback mode because the generic-PUT whitelist won't include them until
the server registers the type; fovDeg (shared with camera) is asserted
always.

Usage:
    python tests/test_spa_radar_render.py [-v]

Requires: pip install playwright && python -m playwright install chromium
"""

import sys, os, json, time, tempfile, threading

# Isolate data dir BEFORE importing parent_server (it loads data at import).
os.environ.setdefault('SLYLED_DATA', tempfile.mkdtemp(prefix='slyled-radar-test-'))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18095
BASE = f'http://127.0.0.1:{PORT}'
SHOT_DIR = os.environ.get('SLYLED_TEST_SHOTS', tempfile.gettempdir())

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv


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


# ── Seed via Flask test client ────────────────────────────────────────────────

RADAR_POSE = {'x': 1000, 'y': 500, 'z': 2000, 'rotation': [0, 0, 30]}


def seed_data():
    """Seed one led + one dmx + one radar fixture. Returns ids dict with
    server_radar=True when POST /api/fixtures accepted fixtureType radar
    (i.e. the #911 server half has landed)."""
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Radar Test', 'darkMode': 1,
                                      'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cid = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'Left Strip', 'type': 'linear', 'fixtureType': 'led',
            'childId': cid,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}]})
        led = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'Moving Head', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'rotation': [0, 0, 0]})
        dmx = r.get_json().get('id')

        radar_body = {
            'name': 'Radar 1', 'type': 'point', 'fixtureType': 'radar',
            'radarNode': 'mmwave-01.local', 'rangeMm': 8000, 'fovDeg': 120,
            'radarEnabled': True, 'rotation': list(RADAR_POSE['rotation'])}
        r = c.post('/api/fixtures', json=radar_body)
        if r.status_code == 200 and (r.get_json() or {}).get('ok'):
            server_radar = True
            radar = r.get_json().get('id')
        else:
            # Server half of #911 not landed yet — inject the record
            # directly into the fixtures store so the SPA half can render.
            server_radar = False
            with parent_server._lock:
                radar = parent_server._nxt_fix
                parent_server._fixtures.append({
                    'id': radar, 'name': 'Radar 1', 'fixtureType': 'radar',
                    'childId': None, 'type': 'point', 'childIds': [],
                    'strings': [], 'rotation': list(RADAR_POSE['rotation']),
                    'aoeRadius': 1000, 'meshFile': None,
                    'radarNode': 'mmwave-01.local', 'rangeMm': 8000,
                    'fovDeg': 120, 'radarEnabled': True})
                parent_server._nxt_fix += 1
                parent_server._save('fixtures', parent_server._fixtures)
            print('  NOTE: server rejected fixtureType radar (server half of '
                  '#911 not landed) — seeded via direct fixtures-store '
                  'injection; radarNode/rangeMm/radarEnabled PUT round-trip '
                  'assertions will be skipped.')

        c.post('/api/layout', json={'children': [
            {'id': led, 'x': 3000, 'y': 4500, 'z': 0},
            {'id': dmx, 'x': 5000, 'y': 4800, 'z': 2500},
            {'id': radar, 'x': RADAR_POSE['x'], 'y': RADAR_POSE['y'],
             'z': RADAR_POSE['z']},
        ]})

    return {'led': led, 'dmx': dmx, 'radar': radar, 'server_radar': server_radar}


def start_server():
    from parent_server import app

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)


# ── Playwright helpers ────────────────────────────────────────────────────────

def wait_tab(page, tab_id, timeout=5000):
    page.click(f'#n-{tab_id}')
    page.wait_for_selector(f'#t-{tab_id}', state='visible', timeout=timeout)
    time.sleep(0.4)


def drain_errors(console_errors):
    """Return + clear collected console/page errors. Chrome logs every
    failed HTTP resource as a console error; the SPA's background polls
    (/api/firmware/check 400 without build config, /api/space 404, favicon)
    fail identically with or without a radar fixture, so filter that noise
    and keep genuine JS errors / page exceptions."""
    errs = [e for e in console_errors
            if 'favicon' not in e.lower()
            and not e.startswith('Failed to load resource')]
    del console_errors[:]
    return errs


# ── Test suites ───────────────────────────────────────────────────────────────

def test_registry(page, ids, console_errors):
    section('Registry: radar descriptor known (no pre-#911 fallback)')
    d = page.evaluate("""() => {
        var desc = fixtureTypeDesc({fixtureType: 'radar'});
        return {registered: !!FIXTURE_TYPES.radar, key: desc.key,
                label: desc.label, badge: desc.badge.text,
                shape: desc.nodeShape,
                caps: desc.caps,
                panel: desc.panelDetailHtml({id: 0, radarNode: '',
                                             rotation: [0, 0, 0]})};
    }""")
    ok(d['registered'], 'FIXTURE_TYPES.radar is registered')
    ok(d['key'] == 'radar' and d['label'] == 'Radar', 'key/label = radar/Radar')
    ok(d['badge'] == 'RDR', "badge text is 'RDR'")
    ok(d['shape'] == 'box', 'nodeShape is box')
    ok(d['caps'] == {'hasBeam': False, 'hasFov': True, 'tracksPeople': True,
                     'hasOrientation': True, 'hasInvertedMount': False,
                     'hasRotationEditor': True, 'countsAsCamera': False},
       'caps match the #911 design')
    ok('does not know' not in d['panel'],
       'panelDetailHtml has no unknown-type "does not know" notice')


def test_layout_render(page, ids, console_errors):
    section('Layout tab: box node + coverage sector')
    rid = ids['radar']
    wait_tab(page, 'layout')
    time.sleep(1.5)

    d = page.evaluate("""(rid) => {
        var grp = _s3d.nodes.find(g => g.userData.childId === rid);
        if (!grp) return {err: 'no scene group for radar fixture'};
        var sect = [];
        grp.traverse(o => { if (o.userData && o.userData.radarSector) sect.push(o); });
        var fan = sect.find(o => o.isMesh);
        var line = sect.find(o => o.isLine);
        var mid = null, first = null;
        if (line) {
            // outline points: [origin, arc(25 pts), origin] — arc midpoint
            // (boresight) is index 13, first arc point (-FoV/2) index 1.
            var p = line.geometry.getAttribute('position');
            mid = [p.getX(13), p.getY(13), p.getZ(13)];
            first = [p.getX(1), p.getY(1), p.getZ(1)];
        }
        return {
            sectorParts: sect.length,
            nodeGeo: grp.children[0].geometry.type,
            fanOpacity: fan ? fan.material.opacity : null,
            fanVisibleDefault: fan ? fan.visible : null,
            camConeTagged: sect.every(o => o.userData.cameraCone === true),
            mid: mid, first: first,
        };
    }""", rid)
    ok('err' not in d, f"radar scene group found ({d.get('err', 'ok')})")
    ok(d.get('nodeGeo') == 'BoxGeometry', 'node mesh is a box')
    ok(d.get('sectorParts') == 2, 'sector = fan fill + outline (2 parts)')
    ok(abs((d.get('fanOpacity') or 0) - 0.10) < 1e-6, 'layout fan opacity 0.10')
    ok(d.get('camConeTagged') is True,
       'sector tagged cameraCone (Camera Cones view-toggle gate)')
    ok(d.get('fanVisibleDefault') is False,
       'sector hidden by default (camCones default OFF, camera convention)')

    # Orientation sanity: yaw 30° → boresight three.js dir (sin30, 0, cos30);
    # at range 8 m the arc midpoint is (4.0, 0, 6.93). The first arc point
    # (-60° off boresight → stage azimuth -30°) lands at (-4.0, 0, 6.93):
    # same depth, mirrored X — a symmetric wedge opened toward +yaw.
    mid = d.get('mid') or [0, 0, 0]
    first = d.get('first') or [0, 0, 0]
    ok(abs(mid[0] - 4.0) < 0.05 and abs(mid[1]) < 0.01 and abs(mid[2] - 6.928) < 0.05,
       f'boresight arc midpoint = +yaw-30° direction, 8 m (got {[round(v, 3) for v in mid]})')
    ok(abs(first[0] + 4.0) < 0.05 and abs(first[2] - 6.928) < 0.05,
       f'-FoV/2 arc point mirrored across boresight (got {[round(v, 3) for v in first]})')

    # Camera Cones toggle shows the sector.
    vis = page.evaluate("""(rid) => {
        _layShowCamCones = true; _applyAllVisibility();
        var grp = _s3d.nodes.find(g => g.userData.childId === rid);
        var fan = null;
        grp.traverse(o => { if (o.userData && o.userData.radarSector && o.isMesh) fan = o; });
        return fan ? fan.visible : null;
    }""", rid)
    ok(vis is True, 'Camera Cones toggle ON shows the radar sector')

    # Side panel.
    page.evaluate(f'_updateSidePanel({rid})')
    time.sleep(0.3)
    badge = page.evaluate("() => document.getElementById('panel-fx-badge').textContent")
    det = page.evaluate("() => document.getElementById('panel-details').innerText")
    ok(badge == 'RDR', f"side-panel badge is 'RDR' (got '{badge}')")
    ok('mmwave-01.local' in det, 'panel shows radarNode binding')
    ok('8 m' in det, 'panel shows range in metres (8 m)')
    ok('±60°' in det, 'panel shows FoV as ±60°')
    ok('Enabled' in det, 'panel shows enabled state')
    ok('Feeds person tracking' in det, 'panel shows tracked-people note')
    ok('does not know' not in det, 'no pre-#911 fallback notice in panel')

    shot = os.path.join(SHOT_DIR, 'radar_layout.png')
    page.screenshot(path=shot)
    print(f'  screenshot: {shot}')

    errs = drain_errors(console_errors)
    ok(not errs, f'zero console errors on layout tab ({errs[:3]})')


def test_edit_roundtrip(page, ids, console_errors):
    section('Edit modal: radar fields render, validate, round-trip')
    rid = ids['radar']
    page.evaluate(f'editFixture({rid})')
    page.wait_for_selector('#fx-radar-node', timeout=5000)

    vals = page.evaluate("""() => ({
        node: document.getElementById('fx-radar-node').value,
        range: document.getElementById('fx-radar-range').value,
        fov: document.getElementById('fx-radar-fov').value,
        enabled: document.getElementById('fx-radar-enabled').checked,
        title: document.getElementById('modal-title').textContent,
        body: document.getElementById('modal-body').innerText,
    })""")
    ok(vals['node'] == 'mmwave-01.local', 'edit: radarNode prefilled')
    ok(vals['range'] == '8000', 'edit: rangeMm prefilled (mm, like Position)')
    ok(vals['fov'] == '120', 'edit: fovDeg prefilled')
    ok(vals['enabled'] is True, 'edit: enabled checkbox checked')
    ok('does not know' not in vals['body'], 'edit modal has no fallback notice')

    # Validation: nonsense range aborts the save with an alert.
    dialogs = []
    page.once('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
    page.evaluate("() => { document.getElementById('fx-radar-range').value = '-5'; }")
    page.click('#modal-body .btn.btn-on')
    time.sleep(0.5)
    ok(len(dialogs) == 1 and 'Range' in dialogs[0],
       f'invalid range rejected with alert ({dialogs})')

    # Valid save round-trip.
    page.evaluate("""() => {
        document.getElementById('fx-radar-node').value = 'mmwave-02.local';
        document.getElementById('fx-radar-range').value = '6500';
        document.getElementById('fx-radar-fov').value = '90';
        document.getElementById('fx-radar-enabled').checked = false;
    }""")
    page.click('#modal-body .btn.btn-on')
    time.sleep(1.0)
    f = page.evaluate(f"() => fetch('/api/fixtures/{rid}').then(r => r.json())")
    ok(f.get('fovDeg') == 90, 'PUT round-trip: fovDeg = 90')
    if ids['server_radar']:
        ok(f.get('radarNode') == 'mmwave-02.local', 'PUT round-trip: radarNode')
        ok(f.get('rangeMm') == 6500, 'PUT round-trip: rangeMm')
        ok(f.get('radarEnabled') is False, 'PUT round-trip: radarEnabled')
    else:
        print('  NOTE: radarNode/rangeMm/radarEnabled round-trip skipped — '
              'server half of #911 not landed (generic-PUT whitelist does '
              'not include radar fields yet).')
        # Restore the seeded values the later tests rely on.
        import parent_server
        with parent_server._lock:
            for fx in parent_server._fixtures:
                if fx['id'] == rid:
                    fx['fovDeg'] = 120

    errs = drain_errors(console_errors)
    ok(not errs, f'zero console errors around edit modal ({errs[:3]})')


def test_runtime_render(page, ids, console_errors):
    section('Runtime tab: sector at lower opacity, zero errors')
    rid = ids['radar']
    wait_tab(page, 'runtime')
    time.sleep(1.5)

    d = page.evaluate("""(rid) => {
        var grp = (_emu3d.nodes || []).find(g => g.userData.fixtureId === rid);
        if (!grp) return {err: 'no runtime group for radar fixture'};
        var sect = [];
        grp.traverse(o => { if (o.userData && o.userData.radarSector) sect.push(o); });
        var fan = sect.find(o => o.isMesh);
        return {sectorParts: sect.length,
                fanOpacity: fan ? fan.material.opacity : null};
    }""", rid)
    ok('err' not in d, f"runtime radar group found ({d.get('err', 'ok')})")
    ok(d.get('sectorParts') == 2, 'runtime sector = fan + outline')
    ok(abs((d.get('fanOpacity') or 0) - 0.06) < 1e-6,
       'runtime fan opacity 0.06 (lower than layout, camera-style static)')

    errs = drain_errors(console_errors)
    ok(not errs, f'zero console errors on runtime tab ({errs[:3]})')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('\033[1m=== SPA radar fixture descriptor tests (#911) ===\033[0m')
    print(f'Target: {BASE}   data: {os.environ["SLYLED_DATA"]}\n')

    print('Seeding test data...')
    ids = seed_data()
    print(f'  IDs: {ids}')

    print('Starting Flask server...')
    start_server()

    from playwright.sync_api import sync_playwright
    console_errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800},
                                  color_scheme='dark')
        page = ctx.new_page()
        page.on('console',
                lambda m: console_errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: console_errors.append(str(e)))
        page.goto(BASE, wait_until='domcontentloaded', timeout=15000)
        time.sleep(1.0)

        for name, fn in [('registry', test_registry),
                         ('layout', test_layout_render),
                         ('edit', test_edit_roundtrip),
                         ('runtime', test_runtime_render)]:
            try:
                fn(page, ids, console_errors)
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
        print(f'\033[32m  {_pass} passed\033[0m, \033[31m{_fail} failed\033[0m of {total}')
        for e in _errors:
            print(f'    - {e}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
