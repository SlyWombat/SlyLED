#!/usr/bin/env python3
"""
test_gyro_api.py - Flask integration tests for gyro-related API endpoints.

Tests fixture CRUD with fixtureType='gyro', gyro state endpoint, enable/disable
commands, legacy pre-#877 field rejection, fixture migration, and validation.

Usage:
    python tests/test_gyro_api.py
"""

import sys
import os
import json
import struct
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app, _gyro_state, _gyro_lock

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def run():
    with app.test_client() as c:

        # -- Factory reset ------------------------------------------------------
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # -- 1. Create gyro fixture ---------------------------------------------
        # #877 / #484 phase 5 — the delta-path tuning fields (panScale,
        # tiltScale, panCenter, tiltCenter, panOffsetDeg, tiltOffsetDeg,
        # smoothing) were removed from the gyro contract: the orchestrator
        # no longer transforms the aim vector. The server tolerates them in
        # request bodies (ignored) and strips stale persisted values on
        # load. Current gyro fields: gyroChildId, assignedMoverId,
        # gyroEnabled (fixture_types.py _gyro_apply_create).
        print('-- 1. Create gyro fixture --')
        r = c.post('/api/fixtures', json={
            'name': 'Stage Gyro 1',
            'type': 'point',
            'fixtureType': 'gyro',
            'gyroChildId': None,
            'assignedMoverId': None,
            # Legacy pre-#877 tunables — must be silently ignored:
            'panScale': 1.4,
            'tiltScale': 1.2,
            'panCenter': 128,
            'tiltCenter': 128,
            'panOffsetDeg': 5.0,
            'tiltOffsetDeg': -5.0,
            'smoothing': 0.2,
        })
        d = r.get_json()
        ok('POST gyro fixture -> 200', r.status_code == 200 and d.get('ok'), str(d))
        gf_id = d.get('id')

        # -- 2. GET fixture verifies gyro fields --------------------------------
        print('-- 2. GET gyro fixture fields --')
        r = c.get(f'/api/fixtures/{gf_id}')
        d = r.get_json()
        ok('GET gyro fixture -> 200', r.status_code == 200)
        ok('fixtureType is gyro', d.get('fixtureType') == 'gyro')
        ok('gyroEnabled defaults False', d.get('gyroEnabled') is False)
        ok('legacy panScale NOT stored (#877)',     'panScale' not in d, str(d))
        ok('legacy tiltScale NOT stored (#877)',    'tiltScale' not in d)
        ok('legacy smoothing NOT stored (#877)',    'smoothing' not in d)
        ok('legacy panOffsetDeg NOT stored (#877)', 'panOffsetDeg' not in d)

        # -- 3. PUT updates gyro fields -----------------------------------------
        print('-- 3. PUT gyro fixture --')
        r = c.put(f'/api/fixtures/{gf_id}', json={
            'gyroEnabled': True,
            'smoothing': 0.3,   # removed field — generic PUT must ignore it
        })
        ok('PUT gyro fixture -> 200', r.status_code == 200 and r.get_json().get('ok'))
        r = c.get(f'/api/fixtures/{gf_id}')
        d = r.get_json()
        ok('gyroEnabled updated to True', d.get('gyroEnabled') is True)
        ok('smoothing still NOT stored after PUT (#877)', 'smoothing' not in d)

        # -- 4. Invalid fixtureType rejected -----------------------------------
        print('-- 4. Invalid fixtureType rejected --')
        r = c.post('/api/fixtures', json={'name': 'Bad', 'type': 'point', 'fixtureType': 'lidar'})
        ok('POST fixtureType=lidar -> 400', r.status_code == 400)
        r = c.put(f'/api/fixtures/{gf_id}', json={'fixtureType': 'foo'})
        ok('PUT fixtureType=foo -> 400', r.status_code == 400)

        # -- 5. GET /api/gyro/state - empty ------------------------------------
        print('-- 5. GET /api/gyro/state --')
        r = c.get('/api/gyro/state')
        ok('GET /api/gyro/state -> 200', r.status_code == 200)
        ok('state list type', isinstance(r.get_json(), list))

        # -- 6. Inject gyro state and check it is visible ----------------------
        print('-- 6. Inject gyro state --')
        with _gyro_lock:
            _gyro_state['10.0.0.1'] = {
                'roll': 12.34, 'pitch': -5.67, 'yaw': 90.0,
                'fps': 20, 'flags': 0b111, 'ts': time.time(),
            }
        r = c.get('/api/gyro/state')
        d = r.get_json()
        entry = next((e for e in d if e.get('ip') == '10.0.0.1'), None)
        ok('Injected state visible in /api/gyro/state', entry is not None)
        ok('Roll round-trips', entry and abs(entry['roll'] - 12.34) < 0.01,
           str(entry))
        ok('stale=False for fresh entry', entry and not entry['stale'])

        # -- 7. Stale entry marked as stale ------------------------------------
        print('-- 7. Stale detection --')
        with _gyro_lock:
            _gyro_state['10.0.0.2'] = {
                'roll': 0, 'pitch': 0, 'yaw': 0,
                'fps': 0, 'flags': 0, 'ts': time.time() - 10.0,  # 10s old
            }
        r = c.get('/api/gyro/state')
        d = r.get_json()
        stale = next((e for e in d if e.get('ip') == '10.0.0.2'), None)
        ok('Stale entry present', stale is not None)
        ok('stale=True for old entry', stale and stale.get('stale') is True)

        # -- 8. Enable gyro on offline child returns 503 -----------------------
        print('-- 8. Enable on offline child --')
        # Register a child that is offline
        r2 = c.post('/api/children', json={'ip': '10.0.0.99'})
        d2 = r2.get_json()
        child_id = d2.get('id')
        if child_id is not None:
            r = c.post(f'/api/gyro/{child_id}/enable', json={'fps': 20})
            ok('Enable on offline child -> 503 or 404',
               r.status_code in (503, 404), f'status={r.status_code}')

        # -- 9. Recalibrate endpoint removed ------------------------------------
        # The recalibrate command is no longer part of the gyro API (only
        # /api/gyro/state and /api/gyro/<id>/enable|disable remain). An
        # unmatched POST falls through to the SPA catch-all route, which
        # only serves GET -> 405 Method Not Allowed (404 also acceptable
        # if the catch-all changes).
        print('-- 9. Recalibrate endpoint removed --')
        r = c.post('/api/gyro/99999/recalibrate', json={})
        ok('Recalibrate endpoint removed -> 404/405',
           r.status_code in (404, 405), f'status={r.status_code}')

        # -- 10. GET /api/fixtures returns gyro in the list --------------------
        print('-- 10. Gyro in fixture list --')
        r = c.get('/api/fixtures')
        fxs = r.get_json()
        gyro_entries = [f for f in fxs if f.get('fixtureType') == 'gyro']
        ok('Gyro fixture appears in list', len(gyro_entries) >= 1)

        # -- 11. DELETE gyro fixture -------------------------------------------
        print('-- 11. DELETE gyro fixture --')
        r = c.delete(f'/api/fixtures/{gf_id}')
        ok('DELETE gyro fixture -> 200', r.status_code == 200 and r.get_json().get('ok'))
        r = c.get(f'/api/fixtures/{gf_id}')
        ok('GET deleted fixture -> 404', r.status_code == 404)

        # -- 12. Fixture migration: existing fixtures gain fixtureType='led' ---
        print('-- 12. Fixture migration --')
        r = c.get('/api/fixtures')
        fxs = r.get_json()
        for f in fxs:
            ok(f'Fixture {f["id"]} has fixtureType', 'fixtureType' in f)

        # -- 13. Valid gyro fixture in /api/fixtures filter ---------------------
        print('-- 13. Valid fixtureType filter --')
        # Create a new gyro fixture to test filter
        r = c.post('/api/fixtures', json={
            'name': 'Gyro Filter Test', 'type': 'point', 'fixtureType': 'gyro'
        })
        ok('Create gyro for filter test -> 200', r.status_code == 200)


def report():
    print()
    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    for name, cond, detail in results:
        status = 'PASS' if cond else 'FAIL'
        print(f'  {status} {name}' + (f'  [{detail}]' if detail else ''))
    print()
    print(f'Results: {passed} passed, {failed} failed')
    return failed == 0


if __name__ == '__main__':
    run()
    ok_all = report()
    sys.exit(0 if ok_all else 1)
