#!/usr/bin/env python3
"""
test_parent.py — Comprehensive test suite for the SlyLED parent server.

Usage:
    python tests/test_parent.py
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app, _children, _settings, _github_release_cache

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    with app.test_client() as c:

        # ── Status ──────────────────────────────────────────────────
        r = c.get('/status')
        d = r.get_json()
        ok('GET /status', r.status_code == 200 and d.get('role') == 'parent')

        # ── Settings CRUD ───────────────────────────────────────────
        r = c.get('/api/settings')
        ok('GET /api/settings', r.status_code == 200 and 'name' in r.get_json())

        r = c.post('/api/settings', json={'name': 'TestLED', 'darkMode': 1, 'logging': False})
        ok('POST /api/settings', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/settings')
        ok('Settings name persisted', r.get_json().get('name') == 'TestLED')

        r = c.post('/api/settings', json={'globalBrightness': 128})
        ok('Settings brightness', r.status_code == 200)

        r = c.post('/api/settings', json={'runnerLoop': False})
        ok('Settings runnerLoop', r.status_code == 200)

        # ── Children CRUD ───────────────────────────────────────────
        r = c.get('/api/children')
        ok('GET /api/children', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/children', json={'ip': '10.0.0.99'})
        d = r.get_json()
        ok('POST add child', d.get('ok') and 'id' in d)
        cid = d.get('id')

        r = c.post('/api/children', json={'ip': '10.0.0.99'})
        ok('Duplicate IP returns existing', r.get_json().get('duplicate') == True)

        r = c.post('/api/children', json={})
        ok('Add child no IP → 400', r.status_code == 400)

        r = c.post('/api/children', json={'ip': ''})
        ok('Add child empty IP → 400', r.status_code == 400)

        r = c.post('/api/children', json={'ip': 'http://10.0.0.50'})
        d2 = r.get_json()
        ok('Add child strips http://', d2.get('ok'))
        cid2 = d2.get('id')

        r = c.post(f'/api/children/{cid}/refresh')
        ok('POST refresh (fake IP)', r.status_code == 200)

        r = c.post(f'/api/children/{cid}/reboot')
        ok('POST reboot (fake IP)', r.status_code == 200 and r.get_json().get('ok'))

        r = c.delete(f'/api/children/{cid}')
        ok('DELETE child', r.status_code == 200 and r.get_json().get('ok'))

        r = c.delete(f'/api/children/{cid}')
        ok('DELETE nonexistent → 404', r.status_code == 404)

        if cid2:
            c.delete(f'/api/children/{cid2}')

        r = c.post('/api/children/refresh-all')
        ok('POST refresh-all', r.status_code == 200 and 'online' in r.get_json())

        r = c.get('/api/children/discover')
        ok('GET discover', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.get('/api/children/export')
        ok('GET export', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/children/import', json=[
            {'hostname': 'TEST-0001', 'ip': '10.0.0.50', 'name': 'Test',
             'sc': 1, 'strings': [], 'status': 0, 'seen': 0}
        ])
        d = r.get_json()
        ok('POST import', d.get('added', 0) >= 1)

        r = c.post('/api/children/import', json='not a list')
        ok('Import bad data → 400', r.status_code == 400)

        # ── Layout ──────────────────────────────────────────────────
        r = c.get('/api/layout')
        ok('GET /api/layout', r.status_code == 200 and 'canvasW' in r.get_json())

        r = c.post('/api/layout', json={'children': [{'id': 0, 'x': 1000, 'y': 2000}]})
        ok('POST /api/layout', r.status_code == 200 and r.get_json().get('ok'))

        # ── Actions library ─────────────────────────────────────────
        r = c.post('/api/actions', json={'name': 'Test Solid', 'type': 1, 'r': 255, 'g': 0, 'b': 0})
        ok('POST create action', r.status_code == 200 and r.get_json().get('ok'))
        aid = r.get_json().get('id')

        r = c.post('/api/actions', json={'name': '', 'type': 1})
        ok('Create action no name → 400', r.status_code == 400)

        # Create all 14 action types
        aids = []
        for t in range(14):
            r = c.post('/api/actions', json={
                'name': f'Type {t}', 'type': t,
                'r': 100, 'g': 50, 'b': 200,
                'speedMs': 500, 'periodMs': 1000, 'spawnMs': 100,
                'r2': 0, 'g2': 255, 'b2': 0,
                'minBri': 10, 'spacing': 3, 'paletteId': 0,
                'cooling': 55, 'sparking': 120, 'direction': 0,
                'tailLen': 5, 'density': 3, 'decay': 80, 'fadeSpeed': 10,
            })
            ok(f'Create action type {t}', r.status_code == 200)
            aids.append(r.get_json().get('id'))

        r = c.get('/api/actions')
        ok('GET list actions', r.status_code == 200 and len(r.get_json()) >= 14)

        r = c.get(f'/api/actions/{aid}')
        ok('GET action by id', r.status_code == 200 and r.get_json().get('name') == 'Test Solid')

        r = c.put(f'/api/actions/{aid}', json={'name': 'Updated Solid', 'r': 128})
        ok('PUT update action', r.status_code == 200)

        r = c.get(f'/api/actions/{aid}')
        ok('Action update persisted', r.get_json().get('name') == 'Updated Solid' and r.get_json().get('r') == 128)

        r = c.get('/api/actions/99999')
        ok('GET nonexistent action → 404', r.status_code == 404)

        r = c.delete(f'/api/actions/{aid}')
        ok('DELETE action', r.status_code == 200)

        r = c.delete(f'/api/actions/{aid}')
        ok('DELETE nonexistent action → 404', r.status_code == 404)

        # ── Runners ─────────────────────────────────────────────────
        r = c.post('/api/runners', json={'name': 'Test Runner'})
        ok('POST create runner', r.status_code == 200)
        rid = r.get_json().get('id')

        r = c.put(f'/api/runners/{rid}', json={
            'steps': [{'actionId': aids[1], 'durationS': 5},
                      {'actionId': aids[5], 'durationS': 10}]
        })
        ok('PUT runner steps', r.status_code == 200)

        r = c.get(f'/api/runners/{rid}')
        d = r.get_json()
        ok('GET runner', r.status_code == 200 and len(d.get('steps', [])) == 2)

        r = c.get('/api/runners')
        ok('GET list runners', r.status_code == 200)

        r = c.post(f'/api/runners/{rid}/compute')
        ok('POST compute', r.status_code == 200 and r.get_json().get('ok'))

        r = c.post(f'/api/runners/{rid}/sync')
        ok('POST sync (no online children)', r.status_code == 200)

        r = c.post(f'/api/runners/{rid}/start')
        ok('POST start runner', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/settings')
        ok('Runner running in settings', r.get_json().get('runnerRunning') == True)

        r = c.get('/api/runners/live')
        ok('GET /api/runners/live', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/runners/stop')
        ok('POST runners/stop', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/settings')
        ok('Runner stopped', r.get_json().get('runnerRunning') == False)

        r = c.delete(f'/api/runners/{rid}')
        ok('DELETE runner', r.status_code == 200)

        r = c.delete(f'/api/runners/{rid}')
        ok('DELETE nonexistent runner → 404', r.status_code == 404)

        r = c.get(f'/api/runners/99999')
        ok('GET nonexistent runner → 404', r.status_code == 404)

        # ── Action dispatch ─────────────────────────────────────────
        r = c.post('/api/action', json={'type': 1, 'r': 255, 'g': 0, 'b': 0, 'target': 'all'})
        ok('POST /api/action', r.status_code == 200)

        r = c.post('/api/action/stop', json={'target': 'all'})
        ok('POST /api/action/stop', r.status_code == 200)

        r = c.post('/api/action', json={'type': 1, 'target': '99999'})
        ok('Action nonexistent target → 404', r.status_code == 404)

        # ── WiFi ────────────────────────────────────────────────────
        r = c.get('/api/wifi')
        ok('GET /api/wifi', r.status_code == 200 and 'ssid' in r.get_json())

        r = c.post('/api/wifi', json={'ssid': 'TestNet', 'password': 'secret123'})
        ok('POST /api/wifi', r.status_code == 200)

        r = c.get('/api/wifi')
        ok('WiFi SSID persisted', r.get_json().get('ssid') == 'TestNet')
        ok('WiFi password stored', r.get_json().get('hasPassword') == True)

        # ── WLED bridge ─────────────────────────────────────────────
        from wled_bridge import wled_map_action, wled_map_step, wled_probe

        for t in range(14):
            state = wled_map_action({
                'type': t, 'r': 255, 'g': 100, 'b': 50,
                'speedMs': 500, 'r2': 0, 'g2': 0, 'b2': 255,
                'p8a': 50, 'p8b': 120, 'p8c': 0, 'p8d': 80,
                'minBri': 10, 'spacing': 3, 'paletteId': 0,
                'cooling': 55, 'sparking': 120, 'direction': 0,
                'tailLen': 5, 'density': 3, 'decay': 80, 'fadeSpeed': 10,
                'duty': 50, 'barWidth': 3,
            })
            ok(f'WLED map type {t}', isinstance(state, dict) and 'on' in state)

        st = wled_map_step({'type': 5, 'r': 0, 'g': 0, 'b': 0, 'speedMs': 100}, brightness=200)
        ok('WLED map_step brightness', st.get('bri') == 200)

        # Probe fake IP (should return None, not crash)
        result = wled_probe('192.0.2.1', timeout=0.5)
        ok('WLED probe fake IP', result is None)

        # ── SPA / fallback ──────────────────────────────────────────
        r = c.get('/')
        ok('GET / (SPA)', r.status_code == 200)

        r = c.get('/favicon.ico')
        ok('GET /favicon.ico → 404', r.status_code == 404)

        r = c.get('/nonexistent/path')
        ok('GET unknown path → SPA fallback', r.status_code == 200)

        # ── Flights ──────────────────────────────────────────────────
        r = c.post('/api/flights', json={'name': 'Ceiling', 'performerIds': [0, 1], 'runnerId': 0, 'priority': 1})
        ok('POST create flight', r.status_code == 200 and r.get_json().get('ok'))
        fid = r.get_json().get('id')

        r = c.post('/api/flights', json={'name': 'Floor', 'performerIds': [2], 'runnerId': 0, 'priority': 2})
        ok('POST create flight 2', r.status_code == 200)
        fid2 = r.get_json().get('id')

        r = c.post('/api/flights', json={'name': ''})
        ok('Flight no name → 400', r.status_code == 400)

        r = c.get('/api/flights')
        ok('GET flights', r.status_code == 200 and len(r.get_json()) >= 2)

        r = c.get(f'/api/flights/{fid}')
        ok('GET flight by id', r.status_code == 200 and r.get_json().get('name') == 'Ceiling')

        r = c.put(f'/api/flights/{fid}', json={'name': 'Ceiling Updated', 'performerIds': [0, 1, 3]})
        ok('PUT update flight', r.status_code == 200)

        r = c.get(f'/api/flights/{fid}')
        ok('Flight update persisted', r.get_json().get('name') == 'Ceiling Updated')

        r = c.delete(f'/api/flights/{fid2}')
        ok('DELETE flight', r.status_code == 200)

        r = c.delete(f'/api/flights/{fid2}')
        ok('DELETE nonexistent flight → 404', r.status_code == 404)

        # ── Shows ───────────────────────────────────────────────────
        r = c.post('/api/shows', json={'name': 'Evening Show', 'flightIds': [fid], 'loop': True})
        ok('POST create show', r.status_code == 200 and r.get_json().get('ok'))
        show_id = r.get_json().get('id')

        r = c.post('/api/shows', json={'name': ''})
        ok('Show no name → 400', r.status_code == 400)

        r = c.get('/api/shows')
        ok('GET shows', r.status_code == 200 and len(r.get_json()) >= 1)

        r = c.get(f'/api/shows/{show_id}')
        ok('GET show by id', r.status_code == 200 and r.get_json().get('name') == 'Evening Show')

        r = c.put(f'/api/shows/{show_id}', json={'name': 'Night Show', 'loop': False})
        ok('PUT update show', r.status_code == 200)

        r = c.get(f'/api/shows/{show_id}')
        ok('Show update persisted', r.get_json().get('name') == 'Night Show')

        r = c.post(f'/api/shows/{show_id}/start')
        ok('POST start show (no online children)', r.status_code == 200)

        r = c.post('/api/shows/stop')
        ok('POST stop shows', r.status_code == 200)

        r = c.delete(f'/api/shows/{show_id}')
        ok('DELETE show', r.status_code == 200)

        r = c.delete(f'/api/shows/{show_id}')
        ok('DELETE nonexistent show → 404', r.status_code == 404)

        # Clean up remaining flight
        c.delete(f'/api/flights/{fid}')

        # ── Step-level overrides ────────────────────────────────────
        # Create an action and runner to test step overrides
        r = c.post('/api/actions', json={'name': 'Override Test', 'type': 5, 'speedMs': 100})
        oa_id = r.get_json().get('id')
        r = c.post('/api/runners', json={'name': 'Override Runner'})
        or_id = r.get_json().get('id')
        r = c.put(f'/api/runners/{or_id}', json={'steps': [
            {'actionId': oa_id, 'durationS': 5, 'targets': [0, 1], 'brightness': 128, 'speedMs': 500},
            {'actionId': oa_id, 'durationS': 10, 'direction': 2},
        ]})
        ok('PUT runner with step overrides', r.status_code == 200)
        r = c.get(f'/api/runners/{or_id}')
        steps = r.get_json().get('steps', [])
        ok('Step overrides preserved', len(steps) == 2 and steps[0].get('brightness') == 128)
        c.delete(f'/api/runners/{or_id}')
        c.delete(f'/api/actions/{oa_id}')

        # ── Config export/import ──────────────────────────────────────
        # Add a child + layout for testing
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        cfg_cid = r.get_json().get('id')
        c.post('/api/layout', json={'canvasW': 8000, 'canvasH': 4000,
               'children': [{'id': cfg_cid, 'x': 500, 'y': 300}]})

        r = c.get('/api/config/export')
        d = r.get_json()
        ok('Config export type', d.get('type') == 'slyled-config')
        ok('Config export version', d.get('version') == 1)
        ok('Config export has children', len(d.get('children', [])) >= 1)
        ok('Config export has layout', 'canvasW' in d.get('layout', {}))
        config_bundle = d

        # Bad type rejected
        r = c.post('/api/config/import', json={'type': 'wrong'})
        ok('Config import bad type → 400', r.status_code == 400)

        # Import with a new child
        new_cfg = {'type': 'slyled-config', 'version': 1,
                   'children': [{'id': 99, 'hostname': 'IMPORT-TEST', 'ip': '10.0.0.77',
                                 'name': 'Imported', 'desc': '', 'sc': 0, 'strings': [], 'status': 0}],
                   'layout': {'canvasW': 10000, 'canvasH': 5000,
                              'children': [{'id': 99, 'x': 200, 'y': 400}]}}
        r = c.post('/api/config/import', json=new_cfg)
        d = r.get_json()
        ok('Config import ok', d.get('ok'))
        ok('Config import added 1', d.get('added') == 1)

        # Re-import updates
        r = c.post('/api/config/import', json=new_cfg)
        d = r.get_json()
        ok('Config import update', d.get('updated') == 1 and d.get('added') == 0)

        # Layout IDs remapped
        r = c.get('/api/layout')
        lay = r.get_json()
        lay_ids = [lc['id'] for lc in lay.get('children', [])]
        ok('Config import layout remapped', 99 not in lay_ids, f'layout ids: {lay_ids}')

        # Clean up imported child
        r = c.get('/api/children')
        for ch in r.get_json():
            if ch.get('hostname') == 'IMPORT-TEST':
                c.delete(f'/api/children/{ch["id"]}')

        # ── Show export/import ────────────────────────────────────────
        r = c.get('/api/show/export')
        d = r.get_json()
        ok('Show export type', d.get('type') == 'slyled-show')
        ok('Show export version', d.get('version') == 1)
        ok('Show export has actions', isinstance(d.get('actions'), list))
        ok('Show export has runners', isinstance(d.get('runners'), list))
        ok('Show export has flights', isinstance(d.get('flights'), list))
        ok('Show export has shows', isinstance(d.get('shows'), list))

        # Bad type rejected
        r = c.post('/api/show/import', json={'type': 'wrong'})
        ok('Show import bad type → 400', r.status_code == 400)

        # Import a small show bundle
        show_bundle = {'type': 'slyled-show', 'version': 1,
                       'actions': [{'id': 0, 'name': 'TestSolid', 'type': 1, 'r': 255, 'g': 0, 'b': 0}],
                       'runners': [{'id': 0, 'name': 'TestRunner', 'computed': False,
                                    'steps': [{'actionId': 0, 'durationS': 5}]}],
                       'flights': [{'id': 0, 'name': 'TestFlight', 'performerIds': [999],
                                    'runnerId': 0, 'priority': 1}],
                       'shows': [{'id': 0, 'name': 'TestShow', 'flightIds': [0], 'loop': False}]}
        r = c.post('/api/show/import', json=show_bundle)
        d = r.get_json()
        ok('Show import ok', d.get('ok'))
        ok('Show import actions count', d.get('actions') == 1)
        ok('Show import runners count', d.get('runners') == 1)
        ok('Show import flights count', d.get('flights') == 1)
        ok('Show import shows count', d.get('shows') == 1)
        ok('Show import orphan warning', 'warning' in d)

        # Verify ID remapping — runner step.actionId should point to new action ID
        r = c.get('/api/runners')
        runners = r.get_json()
        ok('Show import runner exists', len(runners) == 1)
        if runners:
            # GET /api/runners returns step count; fetch full runner for steps
            full_r = c.get(f'/api/runners/{runners[0]["id"]}').get_json()
            r_steps = full_r.get('steps', [])
            r_actions = c.get('/api/actions').get_json()
            if r_actions and r_steps and isinstance(r_steps[0], dict):
                ok('Show import actionId remapped', r_steps[0].get('actionId') == r_actions[0]['id'])
            else:
                ok('Show import actionId remapped', False, f'steps={r_steps}, actions={r_actions}')

        # ── Demo show ─────────────────────────────────────────────────
        r = c.post('/api/show/demo', json={'mood': 'default'})
        d = r.get_json()
        ok('Demo show ok', d.get('ok'))
        ok('Demo show 8 actions', d.get('actions') == 8)
        ok('Demo show 1 runner', d.get('runners') == 1)
        ok('Demo show 1 flight', d.get('flights') == 1)
        ok('Demo show 1 show', d.get('shows') == 1)

        # Verify demo data is queryable
        r = c.get('/api/actions')
        ok('Demo actions in store', len(r.get_json()) == 8)
        r = c.get('/api/shows')
        ok('Demo show in store', len(r.get_json()) == 1 and r.get_json()[0].get('name') == 'Demo Show')

        # Clean up test child
        c.delete(f'/api/children/{cfg_cid}')

        # Demo with no children — should still create actions/runner/show
        c.post('/api/reset')
        r = c.post('/api/show/demo', json={})
        d = r.get_json()
        ok('Demo show no performers → 200', r.status_code == 200 and d.get('ok'))
        ok('Demo no-perf creates 8 actions', d.get('actions') == 8)
        ok('Demo no-perf creates 1 runner', d.get('runners') == 1)
        ok('Demo no-perf creates 1 show', d.get('shows') == 1)
        r = c.get('/api/flights')
        flights = r.get_json()
        ok('Demo no-perf flight has empty performerIds',
           len(flights) == 1 and flights[0].get('performerIds') == [])

        # ── OTA firmware endpoints ─────────────────────────────────
        # /api/firmware/latest — may fail if no internet, but should not crash
        r = c.get('/api/firmware/latest')
        ok('GET /api/firmware/latest returns JSON', r.status_code in (200, 502))

        # /api/firmware/check — needs children and WiFi
        c.post('/api/children', json={'ip': '10.0.0.88'})
        r = c.get('/api/firmware/check')
        if r.status_code == 200:
            d = r.get_json()
            ok('Firmware check has children list', 'children' in d)
            ok('Firmware check has latest version', 'latest' in d)
        else:
            ok('Firmware check blocked (no WiFi or no internet)', r.status_code in (400, 502))

        # /api/firmware/ota — child not found
        r = c.post('/api/firmware/ota/9999')
        ok('OTA unknown child → 404', r.status_code == 404)

        # /api/firmware/ota — child offline
        children_list = c.get('/api/children').get_json()
        if children_list:
            test_cid = children_list[-1]['id']
            r = c.post(f'/api/firmware/ota/{test_cid}')
            ok('OTA offline child → 400', r.status_code == 400)
            c.delete(f'/api/children/{test_cid}')

        # ── OTA asset map + proxy URL tests (mocked release) ────────
        # Seed the GitHub release cache so these tests don't need internet
        import time as _time
        _github_release_cache["data"] = {
            "version": "5.4.0",
            "assets": [
                {"name": "esp32-firmware-app.bin", "url": "https://example.com/esp32-app.bin"},
                {"name": "esp32-firmware-merged.bin", "url": "https://example.com/esp32-merged.bin"},
                {"name": "d1mini-firmware.bin", "url": "https://example.com/d1mini.bin"},
            ]
        }
        _github_release_cache["ts"] = _time.time()

        # WiFi must be configured for firmware check/flash/OTA
        # Test guards: clear WiFi, verify check and flash are blocked
        c.post('/api/wifi', json={'ssid': '', 'password': ''})
        r = c.get('/api/firmware/check')
        ok('Firmware check without WiFi -> 400', r.status_code == 400)
        r = c.post('/api/firmware/flash', json={'port': 'COM99', 'firmwareId': 'test', 'board': 'esp32'})
        ok('USB flash without WiFi -> 400', r.status_code == 400)
        # Set WiFi for remaining tests
        c.post('/api/wifi', json={'ssid': 'TestNet', 'password': 'testpass'})

        # Add children with known firmware version and boardType for check tests
        # NOTE: use parent_server._children (not the imported _children) because
        # child DELETE rebinds the module-level list, making the import stale.
        r = c.post('/api/children', json={'ip': '10.99.0.50'})
        ota_cid = r.get_json().get('id')
        # Patch the child inline to simulate an online ESP32
        for ch in parent_server._children:
            if ch['id'] == ota_cid:
                ch['fwVersion'] = '5.3.9'
                ch['boardType'] = 'ESP32'
                ch['status'] = 1
                break

        # /api/firmware/check should prefer app-only binary for ESP32
        r = c.get('/api/firmware/check')
        d = r.get_json()
        esp_child = next((x for x in d['children'] if x['id'] == ota_cid), None)
        ok('OTA check: ESP32 needs update', esp_child and esp_child['needsUpdate'])
        ok('OTA check: ESP32 downloadUrl is app-only',
           esp_child and 'esp32-app.bin' in esp_child.get('downloadUrl', ''))
        ok('OTA check: ESP32 downloadUrl is NOT merged',
           esp_child and 'merged' not in esp_child.get('downloadUrl', ''))

        # Add a D1 Mini child
        r = c.post('/api/children', json={'ip': '10.99.0.51'})
        d1_cid = r.get_json().get('id')
        for ch in parent_server._children:
            if ch['id'] == d1_cid:
                ch['fwVersion'] = '5.3.9'
                ch['boardType'] = 'D1 Mini'
                ch['status'] = 1
                break

        r = c.get('/api/firmware/check')
        d = r.get_json()
        d1_child = next((x for x in d['children'] if x['id'] == d1_cid), None)
        ok('OTA check: D1 Mini downloadUrl correct',
           d1_child and 'd1mini.bin' in d1_child.get('downloadUrl', ''))

        # Test that when only merged binary is available (no app), it falls back
        _github_release_cache["data"]["assets"] = [
            {"name": "esp32-firmware-merged.bin", "url": "https://example.com/esp32-merged.bin"},
            {"name": "d1mini-firmware.bin", "url": "https://example.com/d1mini.bin"},
        ]
        _github_release_cache["ts"] = _time.time()
        r = c.get('/api/firmware/check')
        d = r.get_json()
        esp_child2 = next((x for x in d['children'] if x['id'] == ota_cid), None)
        ok('OTA check: ESP32 falls back to merged when no app-only',
           esp_child2 and 'esp32-merged.bin' in esp_child2.get('downloadUrl', ''))

        # Restore full asset list for OTA trigger test
        _github_release_cache["data"]["assets"] = [
            {"name": "esp32-firmware-app.bin", "url": "https://example.com/esp32-app.bin"},
            {"name": "esp32-firmware-merged.bin", "url": "https://example.com/esp32-merged.bin"},
            {"name": "d1mini-firmware.bin", "url": "https://example.com/d1mini.bin"},
        ]
        _github_release_cache["ts"] = _time.time()

        # /api/firmware/ota — requires WiFi credentials
        # Clear WiFi first to test the guard
        c.post('/api/wifi', json={'ssid': '', 'password': ''})
        r = c.post(f'/api/firmware/ota/{ota_cid}')
        ok('OTA trigger without WiFi → 400',
           r.status_code == 400 and 'WiFi' in r.get_json().get('err', ''))

        # Set WiFi credentials so OTA can proceed (trigger will fail at HTTP to child, which is OK)
        c.post('/api/wifi', json={'ssid': 'TestNet', 'password': 'pass123'})
        r = c.post(f'/api/firmware/ota/{ota_cid}')
        d = r.get_json()
        # The trigger may succeed (returns ok:True) or fail connecting to fake IP — either is acceptable
        # What matters is it doesn't crash and board detection works
        ok('OTA trigger does not crash', r.status_code in (200, 500))
        if r.status_code == 200:
            ok('OTA trigger returns board=esp32', d.get('board') == 'esp32')
            ok('OTA trigger returns version', d.get('version') == '5.4.0')

        # /api/firmware/binary/<board> — serves binary or tries to download
        r = c.get('/api/firmware/binary/unknown')
        ok('OTA binary unknown board → 404', r.status_code == 404)

        # /api/firmware/registry — check versions updated
        r = c.get('/api/firmware/registry')
        reg = r.get_json()
        esp_fw = next((f for f in reg.get('firmware', []) if f['id'] == 'child-led-esp32'), None)
        ok('Registry ESP32 version is 5.3.10', esp_fw and esp_fw['version'] == '5.3.10')
        d1_fw = next((f for f in reg.get('firmware', []) if f['id'] == 'child-led-d1mini'), None)
        ok('Registry D1 Mini version is 5.3.10', d1_fw and d1_fw['version'] == '5.3.10')

        # Clean up OTA test children
        c.delete(f'/api/children/{ota_cid}')
        c.delete(f'/api/children/{d1_cid}')
        # Clear release cache
        _github_release_cache["data"] = None
        _github_release_cache["ts"] = 0

        # ── Shutdown (don't actually call it) ───────────────────────
        # r = c.post('/api/shutdown')  # skip — would kill process

        # ── Factory reset (last test) ───────────────────────────────
        r = c.post('/api/reset')
        ok('POST /api/reset', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/children')
        ok('Reset cleared children', len(r.get_json()) == 0)

        r = c.get('/api/runners')
        ok('Reset cleared runners', len(r.get_json()) == 0)

        r = c.get('/api/actions')
        ok('Reset cleared actions', len(r.get_json()) == 0)

        r = c.get('/api/flights')
        ok('Reset cleared flights', len(r.get_json()) == 0)

        r = c.get('/api/shows')
        ok('Reset cleared shows', len(r.get_json()) == 0)

    # ── Print results ───────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)

    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)

    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
