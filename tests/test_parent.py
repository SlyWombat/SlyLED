#!/usr/bin/env python3
"""
test_parent.py — Comprehensive test suite for the SlyLED parent server.

Usage:
    python tests/test_parent.py
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from parent_server import app, _children, _settings

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
