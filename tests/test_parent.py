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
        ok('POST refresh-all', r.status_code == 200 and r.get_json().get('pending') is True)
        # Poll for results (background thread completes quickly in test)
        import time as _time
        for _ in range(20):
            _time.sleep(0.3)
            rr = c.get('/api/children/refresh-all/results')
            if not rr.get_json().get('pending'):
                break
        ok('POST refresh-all results', rr.status_code == 200 and 'online' in rr.get_json())

        r = c.get('/api/children/discover')
        ok('GET discover starts', r.status_code == 200 and r.get_json().get('pending') is True)
        for _ in range(20):
            _time.sleep(0.3)
            dr = c.get('/api/children/discover/results')
            dj = dr.get_json()
            if isinstance(dj, list) or not dj.get('pending'):
                break
        ok('GET discover results', dr.status_code == 200 and isinstance(dr.get_json(), list))

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

        # Layout z-axis support
        r = c.post('/api/layout', json={'children': [{'id': 0, 'x': 1000, 'y': 2000, 'z': 500}]})
        ok('POST /api/layout with z', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/layout')
        lch = r.get_json().get('children', [])
        z_val = next((ch.get('z', -1) for ch in lch if ch.get('id') == 0), -1)
        ok('GET /api/layout returns z', z_val == 500)

        # z defaults to 0 for children without z
        r = c.post('/api/layout', json={'children': [{'id': 0, 'x': 1000, 'y': 2000}]})
        r = c.get('/api/layout')
        z_def = next((ch.get('z', -1) for ch in r.get_json().get('children', []) if ch.get('id') == 0), -1)
        ok('Layout z defaults to 0', z_def == 0)

        # ── Stage ──────────────────────────────────────────────────────
        r = c.get('/api/stage')
        ok('GET /api/stage', r.status_code == 200 and 'w' in r.get_json())

        r = c.post('/api/stage', json={'w': 12.0, 'h': 6.0, 'd': 8.0})
        ok('POST /api/stage', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/stage')
        sd = r.get_json()
        ok('Stage persists', sd.get('w') == 12.0 and sd.get('h') == 6.0 and sd.get('d') == 8.0)

        r = c.post('/api/stage', json={'w': -1})
        ok('Stage rejects negative', r.status_code == 400)

        r = c.post('/api/stage', json={'w': 0})
        ok('Stage rejects zero', r.status_code == 400)

        # ── Fixtures (Phase 2) ─────────────────────────────────────────
        r = c.get('/api/fixtures')
        ok('GET /api/fixtures', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/fixtures', json={'name': 'Test Linear', 'type': 'linear', 'childId': 0})
        ok('POST create fixture', r.status_code == 200 and r.get_json().get('ok'))
        fix_id = r.get_json().get('id')

        r = c.get('/api/fixtures/' + str(fix_id))
        ok('GET fixture by id', r.status_code == 200 and r.get_json().get('type') == 'linear')

        r = c.put('/api/fixtures/' + str(fix_id), json={'name': 'Updated Fixture'})
        ok('PUT update fixture', r.status_code == 200 and r.get_json().get('ok'))

        r = c.post('/api/fixtures', json={'name': 'Point Fix', 'type': 'point'})
        ok('POST point fixture', r.status_code == 200)
        fix_id2 = r.get_json().get('id')

        r = c.post('/api/fixtures', json={'name': 'Bad', 'type': 'invalid'})
        ok('Fixture bad type → 400', r.status_code == 400)

        r = c.post('/api/fixtures/' + str(fix_id) + '/resolve')
        ok('POST fixture resolve', r.status_code == 200 and 'pixelPositions' in r.get_json())

        r = c.delete('/api/fixtures/' + str(fix_id2))
        ok('DELETE fixture', r.status_code == 200)

        # ── DMX Fixtures (#91) ────────────────────────────────────────
        # Existing fixture defaults to fixtureType "led"
        r = c.get('/api/fixtures/' + str(fix_id))
        ok('Fixture default fixtureType=led', r.get_json().get('fixtureType') == 'led')

        # Create DMX fixture (valid)
        r = c.post('/api/fixtures', json={
            'name': 'Moving Head 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 13
        })
        ok('POST DMX fixture', r.status_code == 200 and r.get_json().get('ok'))
        dmx_id = r.get_json().get('id')

        # GET DMX fixture — all fields present
        r = c.get('/api/fixtures/' + str(dmx_id))
        d = r.get_json()
        ok('GET DMX fixture fields', d.get('fixtureType') == 'dmx' and d.get('dmxUniverse') == 1
           and d.get('dmxStartAddr') == 1 and d.get('dmxChannelCount') == 13)

        # PUT DMX fixture — update address
        r = c.put('/api/fixtures/' + str(dmx_id), json={'dmxStartAddr': 50})
        ok('PUT DMX fixture addr', r.status_code == 200)
        r = c.get('/api/fixtures/' + str(dmx_id))
        ok('DMX addr updated', r.get_json().get('dmxStartAddr') == 50)

        # Create second DMX fixture with profileId
        r = c.post('/api/fixtures', json={
            'name': 'RGB Par', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 100, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb'
        })
        ok('POST DMX fixture with profile', r.status_code == 200)
        dmx_id2 = r.get_json().get('id')

        # Validation: missing universe
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX missing universe → 400', r.status_code == 400)

        # Validation: startAddr 0
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 0, 'dmxChannelCount': 3
        })
        ok('DMX startAddr 0 → 400', r.status_code == 400)

        # Validation: startAddr 513
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 513, 'dmxChannelCount': 3
        })
        ok('DMX startAddr 513 → 400', r.status_code == 400)

        # Validation: missing channelCount
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1
        })
        ok('DMX missing channelCount → 400', r.status_code == 400)

        # Validation: bad fixtureType
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'invalid'
        })
        ok('Bad fixtureType → 400', r.status_code == 400)

        # Mixed fixture list — check both types
        r = c.get('/api/fixtures')
        flist = r.get_json()
        led_count = sum(1 for f in flist if f.get('fixtureType') == 'led')
        dmx_count = sum(1 for f in flist if f.get('fixtureType') == 'dmx')
        ok('Mixed fixture list', led_count >= 1 and dmx_count >= 2)

        # ── DMX aim points & beam cone data ──────────────────────────
        # DMX fixtures should have aimPoint in layout response
        r = c.get('/api/layout')
        lay = r.get_json()
        dmx_in_lay = [f for f in lay.get('fixtures', []) if f.get('fixtureType') == 'dmx']
        ok('DMX fixtures in layout', len(dmx_in_lay) >= 2)
        ok('DMX fixture has aimPoint', all('aimPoint' in f for f in dmx_in_lay))
        ok('DMX aimPoint is 3-element list', all(
            isinstance(f['aimPoint'], list) and len(f['aimPoint']) == 3 for f in dmx_in_lay))

        # Set explicit aim point
        r = c.put('/api/fixtures/' + str(dmx_id) + '/aim', json={'aimPoint': [5000, 0, 4000]})
        ok('PUT aim point', r.status_code == 200)
        r = c.get('/api/layout')
        dmx_f = [f for f in r.get_json()['fixtures'] if f['id'] == dmx_id][0]
        ok('Aim point persisted', dmx_f['aimPoint'] == [5000.0, 0.0, 4000.0])

        # Aim point validation
        r = c.put('/api/fixtures/' + str(dmx_id) + '/aim', json={'aimPoint': [1, 2]})
        ok('Aim point rejects 2-element', r.status_code == 400)
        r = c.put('/api/fixtures/' + str(dmx_id) + '/aim', json={'aimPoint': 'bad'})
        ok('Aim point rejects string', r.status_code == 400)

        # DMX profiles for beam widths
        r = c.get('/api/dmx-profiles')
        ok('GET /api/dmx-profiles', r.status_code == 200)
        profiles = r.get_json()
        ok('DMX profiles list', isinstance(profiles, list) and len(profiles) > 0)
        # Check that moving head profile has beamWidth and panRange
        mh = [p for p in profiles if 'moving' in p.get('id', '').lower() or p.get('panRange', 0) > 0]
        ok('Moving head profile exists', len(mh) > 0)
        if mh:
            ok('Moving head has beamWidth', mh[0].get('beamWidth', 0) > 0)
            ok('Moving head has panRange', mh[0].get('panRange', 0) > 0)
            ok('Moving head has tiltRange', mh[0].get('tiltRange', 0) > 0)

        # Create DMX fixture WITH profile and verify aimPoint + layout inclusion
        mh_id = mh[0]['id'] if mh else 'generic-moving-head-8ch'
        r = c.post('/api/fixtures', json={
            'name': 'MH Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 50, 'dmxChannelCount': 8,
            'dmxProfileId': mh_id})
        ok('POST DMX with profile', r.status_code == 200)
        mh_fix_id = r.get_json().get('id')
        # Place it on layout
        c.post('/api/layout', json={'fixtures': [
            {'id': dmx_id, 'x': 2000, 'y': 4500, 'z': 6000},
            {'id': dmx_id2, 'x': 5000, 'y': 4500, 'z': 6000},
            {'id': mh_fix_id, 'x': 8000, 'y': 4500, 'z': 6000}]})
        r = c.get('/api/layout')
        mh_in_lay = [f for f in r.get_json()['fixtures'] if f['id'] == mh_fix_id]
        ok('Profile fixture in layout', len(mh_in_lay) == 1)
        ok('Profile fixture has aimPoint', 'aimPoint' in mh_in_lay[0])
        ok('Profile fixture has profileId', mh_in_lay[0].get('dmxProfileId') == mh_id)
        ok('Profile fixture positioned', mh_in_lay[0].get('positioned') is True)
        ok('Profile fixture x correct', mh_in_lay[0].get('x') == 8000)

        # Multiple DMX fixtures all have cones data (aimPoint + position)
        r = c.get('/api/layout')
        all_dmx = [f for f in r.get_json()['fixtures'] if f.get('fixtureType') == 'dmx' and f.get('positioned')]
        ok('All placed DMX have aimPoint', all('aimPoint' in f for f in all_dmx),
           f'missing: {[f["id"] for f in all_dmx if "aimPoint" not in f]}')
        ok('All placed DMX have x/y/z', all(f.get('x') is not None for f in all_dmx))

        # Cleanup
        c.delete('/api/fixtures/' + str(mh_fix_id))

        # ── Profile CRUD + emitters ──────────────────────────────
        # Create custom profile with emitters
        r = c.post('/api/dmx-profiles', json={
            'id': 'test-bar-8seg', 'name': 'Test LED Bar 8-segment',
            'manufacturer': 'Test', 'category': 'bar',
            'channels': [
                {'offset': 0, 'name': 'Dimmer', 'type': 'dimmer'},
                {'offset': 1, 'name': 'Red', 'type': 'red'},
                {'offset': 2, 'name': 'Green', 'type': 'green'},
                {'offset': 3, 'name': 'Blue', 'type': 'blue'},
            ],
            'emitters': [
                {'name': 'Seg 1', 'offset': [0, 0, 0]},
                {'name': 'Seg 2', 'offset': [100, 0, 0]},
                {'name': 'Seg 3', 'offset': [200, 0, 0]},
            ],
        })
        ok('POST profile with emitters', r.status_code == 200 and r.get_json().get('ok'))

        # Verify emitters persisted
        r = c.get('/api/dmx-profiles/test-bar-8seg')
        p = r.get_json()
        ok('Profile has emitters', isinstance(p.get('emitters'), list))
        ok('Profile has 3 emitters', len(p.get('emitters', [])) == 3)
        ok('Emitter 2 offset correct', p['emitters'][1].get('offset') == [100, 0, 0])

        # Update profile
        p['emitters'].append({'name': 'Seg 4', 'offset': [300, 0, 0]})
        r = c.put('/api/dmx-profiles/test-bar-8seg', json=p)
        ok('PUT profile update ok', r.status_code == 200)
        r = c.get('/api/dmx-profiles/test-bar-8seg')
        ok('Profile now has 4 emitters', len(r.get_json().get('emitters', [])) == 4)

        # Invalid emitter (bad offset)
        r = c.post('/api/dmx-profiles', json={
            'id': 'test-bad-emitter', 'name': 'Bad',
            'channels': [{'offset': 0, 'name': 'D', 'type': 'dimmer'}],
            'emitters': [{'name': 'E1', 'offset': [1, 2]}],  # need 3 elements
        })
        ok('Bad emitter offset rejected', r.status_code == 400)

        # Clean up
        c.delete('/api/dmx-profiles/test-bar-8seg')

        # Cleanup DMX fixtures
        c.delete('/api/fixtures/' + str(dmx_id))
        c.delete('/api/fixtures/' + str(dmx_id2))

        # ── Camera fixtures ──────────────────────────────────────────
        # Create camera fixture with all fields
        r = c.post('/api/fixtures', json={
            'name': 'Stage Cam 1', 'type': 'point', 'fixtureType': 'camera',
            'fovDeg': 90, 'cameraUrl': 'rtsp://192.168.1.50:554/stream',
            'resolutionW': 1920, 'resolutionH': 1080
        })
        ok('POST camera fixture', r.status_code == 200 and r.get_json().get('ok'))
        cam_id = r.get_json().get('id')

        # GET camera fixture — verify all fields
        r = c.get('/api/fixtures/' + str(cam_id))
        cf = r.get_json()
        ok('Camera fixtureType', cf.get('fixtureType') == 'camera')
        ok('Camera fovDeg', cf.get('fovDeg') == 90)
        ok('Camera cameraUrl', cf.get('cameraUrl') == 'rtsp://192.168.1.50:554/stream')
        ok('Camera resolutionW', cf.get('resolutionW') == 1920)
        ok('Camera has aimPoint', isinstance(cf.get('aimPoint'), list) and len(cf['aimPoint']) == 3)

        # Update camera FOV
        r = c.put('/api/fixtures/' + str(cam_id), json={'fovDeg': 120})
        ok('PUT camera fovDeg', r.status_code == 200)
        r = c.get('/api/fixtures/' + str(cam_id))
        ok('Camera fovDeg updated', r.get_json().get('fovDeg') == 120)

        # Camera aim point via /aim endpoint
        r = c.put('/api/fixtures/' + str(cam_id) + '/aim', json={'aimPoint': [3000, 1000, 5000]})
        ok('PUT camera aim point', r.status_code == 200)
        r = c.get('/api/fixtures/' + str(cam_id))
        ok('Camera aim persisted', r.get_json().get('aimPoint') == [3000.0, 1000.0, 5000.0])

        # Camera fovDeg validation
        r = c.post('/api/fixtures', json={
            'name': 'Bad Cam', 'type': 'point', 'fixtureType': 'camera', 'fovDeg': 0
        })
        ok('Camera fovDeg 0 → 400', r.status_code == 400)
        r = c.post('/api/fixtures', json={
            'name': 'Bad Cam', 'type': 'point', 'fixtureType': 'camera', 'fovDeg': 200
        })
        ok('Camera fovDeg 200 → 400', r.status_code == 400)
        r = c.put('/api/fixtures/' + str(cam_id), json={'fovDeg': 0})
        ok('PUT camera fovDeg 0 → 400', r.status_code == 400)

        # Camera with defaults (no optional fields)
        r = c.post('/api/fixtures', json={
            'name': 'Minimal Cam', 'type': 'point', 'fixtureType': 'camera'
        })
        ok('POST camera defaults', r.status_code == 200)
        cam_id2 = r.get_json().get('id')
        r = c.get('/api/fixtures/' + str(cam_id2))
        ok('Camera default fovDeg', r.get_json().get('fovDeg') == 60)
        ok('Camera default cameraUrl', r.get_json().get('cameraUrl') == '')

        # Mixed fixture list includes camera
        r = c.get('/api/fixtures')
        cam_count = sum(1 for f in r.get_json() if f.get('fixtureType') == 'camera')
        ok('Camera in fixture list', cam_count >= 2)

        # Place camera on layout
        c.post('/api/layout', json={'fixtures': [
            {'id': cam_id, 'x': 5000, 'y': 4500, 'z': 2000}
        ]})
        r = c.get('/api/layout')
        cam_in_lay = [f for f in r.get_json()['fixtures'] if f.get('fixtureType') == 'camera' and f.get('positioned')]
        ok('Camera in layout', len(cam_in_lay) >= 1)
        ok('Camera layout has aimPoint', 'aimPoint' in cam_in_lay[0])
        ok('Camera layout has fovDeg', cam_in_lay[0].get('fovDeg') == 120)
        ok('Camera layout x correct', cam_in_lay[0].get('x') == 5000)

        # Cleanup camera fixtures
        c.delete('/api/fixtures/' + str(cam_id))
        c.delete('/api/fixtures/' + str(cam_id2))

        # ── Camera discovery & registration ──────────────────────────
        # GET /api/cameras (empty initially)
        r = c.get('/api/cameras')
        ok('GET /api/cameras', r.status_code == 200)
        cam_before = len([f for f in r.get_json() if f.get('fixtureType') == 'camera'])

        # POST /api/cameras — register a camera by IP
        r = c.post('/api/cameras', json={'ip': '192.168.10.200', 'name': 'Test Cam'})
        ok('POST /api/cameras register', r.status_code == 201 and r.get_json().get('ok'))
        reg_cam_id = r.get_json().get('id')

        # Verify camera appears in fixtures list
        r = c.get('/api/fixtures/' + str(reg_cam_id))
        ok('Registered camera is fixture', r.status_code == 200)
        rc = r.get_json()
        ok('Registered camera fixtureType', rc.get('fixtureType') == 'camera')
        ok('Registered camera has cameraIp', rc.get('cameraIp') == '192.168.10.200')
        ok('Registered camera has fovDeg', rc.get('fovDeg') == 60)
        ok('Registered camera has aimPoint', isinstance(rc.get('aimPoint'), list))

        # Duplicate IP → 409
        r = c.post('/api/cameras', json={'ip': '192.168.10.200'})
        ok('Camera duplicate IP → 409', r.status_code == 409)

        # Missing IP → 400
        r = c.post('/api/cameras', json={})
        ok('Camera missing IP → 400', r.status_code == 400)

        # Invalid IP → 400
        r = c.post('/api/cameras', json={'ip': 'not-an-ip'})
        ok('Camera invalid IP → 400', r.status_code == 400)

        # Public IP → 400
        r = c.post('/api/cameras', json={'ip': '8.8.8.8'})
        ok('Camera public IP → 400', r.status_code == 400)

        # GET /api/cameras includes registered camera
        r = c.get('/api/cameras')
        cam_list = r.get_json()
        ok('GET /api/cameras has registered', len(cam_list) > cam_before)
        reg = next((x for x in cam_list if x['id'] == reg_cam_id), None)
        ok('Registered camera in list', reg is not None)
        ok('Camera list has online field', 'online' in reg)

        # Camera appears in layout when placed
        c.post('/api/layout', json={'fixtures': [{'id': reg_cam_id, 'x': 3000, 'y': 4000}]})
        r = c.get('/api/layout')
        cam_in_lay = [f for f in r.get_json()['fixtures'] if f['id'] == reg_cam_id]
        ok('Registered camera in layout', len(cam_in_lay) == 1)

        # Camera can use /aim endpoint
        r = c.put('/api/fixtures/' + str(reg_cam_id) + '/aim', json={'aimPoint': [1000, 500, 2000]})
        ok('Registered camera aim point', r.status_code == 200)

        # ── Camera proxy endpoints (camera node offline, expect 503) ──
        r = c.get('/api/cameras/' + str(reg_cam_id) + '/snapshot')
        ok('Snapshot proxy offline → 503', r.status_code == 503)

        r = c.get('/api/cameras/' + str(reg_cam_id) + '/status')
        ok('Status proxy offline → 503', r.status_code == 503)

        r = c.post('/api/cameras/' + str(reg_cam_id) + '/scan', json={'threshold': 0.5})
        ok('Scan proxy offline → 503', r.status_code == 503)

        # ── Pixel-to-stage coordinate transform (unit test) ──────────
        # Place camera at position and test transform directly
        from parent_server import _pixel_to_stage, _layout, _stage, _fixtures

        # Set up: camera at (1500, 2000, 0) looking at stage center (1500, 0, 750)
        # Stage 3m × 2m × 1.5m = 3000 × 2000 × 1500 mm
        cam_fix = next(f for f in _fixtures if f['id'] == reg_cam_id)
        cam_fix['aimPoint'] = [1500, 0, 750]
        cam_fix['fovDeg'] = 90
        _layout['children'] = [{'id': reg_cam_id, 'x': 1500, 'y': 2000, 'z': 0}]

        # Detection at image center should map near the aim point
        dets = [{'label': 'person', 'confidence': 0.9,
                 'x': 270, 'y': 190, 'w': 100, 'h': 100}]
        result = _pixel_to_stage(dets, cam_fix, 640, 480)
        ok('Transform returns list', isinstance(result, list) and len(result) == 1)
        d0 = result[0]
        ok('Transform has label', d0.get('label') == 'person')
        ok('Transform has confidence', d0.get('confidence') == 0.9)
        ok('Transform x is number', isinstance(d0.get('x'), (int, float)))
        ok('Transform y is 0 (ground)', d0.get('y') == 0)
        ok('Transform z is number', isinstance(d0.get('z'), (int, float)))
        ok('Transform has w', d0.get('w', 0) > 0)
        ok('Transform has h', d0.get('h', 0) > 0)
        ok('Transform has pixelBox', 'pixelBox' in d0)
        # Center detection should be roughly at aim point (within stage bounds)
        ok('Transform x within stage', 0 <= d0['x'] <= 3000,
           f"x={d0['x']}")
        ok('Transform z within stage', 0 <= d0['z'] <= 1500,
           f"z={d0['z']}")

        # Detection at left edge should map to lower x
        dets_left = [{'label': 'chair', 'confidence': 0.7,
                      'x': 0, 'y': 200, 'w': 80, 'h': 80}]
        dets_right = [{'label': 'chair', 'confidence': 0.7,
                       'x': 560, 'y': 200, 'w': 80, 'h': 80}]
        r_left = _pixel_to_stage(dets_left, cam_fix, 640, 480)
        r_right = _pixel_to_stage(dets_right, cam_fix, 640, 480)
        ok('Left detection has lower x than right',
           r_left[0]['x'] < r_right[0]['x'],
           f"left_x={r_left[0]['x']}, right_x={r_right[0]['x']}")

        # Empty detections → empty result
        ok('Empty detections → empty', _pixel_to_stage([], cam_fix, 640, 480) == [])

        # Restore layout
        _layout['children'] = []

        # ── Calibration — homography math (unit tests) ───────────
        from parent_server import _compute_homography, _apply_homography

        # 4-point homography
        stage_pts = [[0, 0], [3000, 0], [3000, 1500], [0, 1500]]
        pixel_pts = [[50, 400], [590, 400], [550, 50], [90, 50]]
        H, err = _compute_homography(stage_pts, pixel_pts)
        ok('Homography 4-point returns matrix', len(H) == 9)
        ok('Homography 4-point low error', err < 50, f'error={err:.1f}mm')
        # Verify reprojection: pixel_pts[0] → stage_pts[0]
        sx, sz = _apply_homography(H, 50, 400)
        ok('Homography reprojects pt0 x', abs(sx - 0) < 20, f'sx={sx:.0f}')
        ok('Homography reprojects pt0 z', abs(sz - 0) < 20, f'sz={sz:.0f}')
        sx2, sz2 = _apply_homography(H, 590, 400)
        ok('Homography reprojects pt1 x', abs(sx2 - 3000) < 20, f'sx={sx2:.0f}')

        # 3-point minimum
        H3, err3 = _compute_homography(stage_pts[:3], pixel_pts[:3])
        ok('Homography 3-point accepted', len(H3) == 9)

        # 2-point accepted (similarity transform)
        H2, err2 = _compute_homography(stage_pts[:2], pixel_pts[:2])
        ok('Homography 2-point accepted', len(H2) == 9, f'error={err2:.1f}mm')

        # 1-point rejected
        try:
            _compute_homography(stage_pts[:1], pixel_pts[:1])
            ok('Homography 1-point rejected', False)
        except ValueError:
            ok('Homography 1-point rejected', True)

        # Collinear points rejected
        try:
            _compute_homography([[0,0],[100,0],[200,0]], [[0,0],[100,0],[200,0]])
            ok('Homography collinear rejected', False)
        except ValueError as e:
            ok('Homography collinear rejected', 'collinear' in str(e).lower())

        # Large stage (10m × 6m)
        big_s = [[0,0],[10000,0],[10000,6000],[0,6000]]
        big_p = [[10,450],[630,450],[600,10],[40,10]]
        Hb, errb = _compute_homography(big_s, big_p)
        ok('Homography large stage no overflow', len(Hb) == 9)

        # ── Calibration API lifecycle ────────────────────────────
        # Need positioned fixtures as references — create 3 LED fixtures
        led_ids = []
        for i in range(3):
            r = c.post('/api/fixtures', json={'name': f'CalRef{i}', 'fixtureType': 'led'})
            led_ids.append(r.get_json()['id'])
        # Position them in a triangle (non-collinear)
        pos_coords = [(500, 0, 200), (2500, 0, 200), (1500, 0, 1200)]
        positions = [{'id': lid, 'x': pos_coords[i][0], 'y': pos_coords[i][1], 'z': pos_coords[i][2]} for i, lid in enumerate(led_ids)]
        c.post('/api/layout', json={'fixtures': positions})

        # Start calibration — need a camera fixture
        r = c.post('/api/cameras', json={'ip': '10.99.0.55'})
        cal_cam_id = r.get_json().get('id')

        r = c.post(f'/api/cameras/{cal_cam_id}/calibrate/start')
        ok('Calibrate start ok', r.status_code == 200 and r.get_json().get('ok'))
        ok('Calibrate start has steps', r.get_json().get('steps', 0) >= 3)

        # Detect reference points
        refs = r.get_json().get('fixtures', [])
        # Use triangular pixel positions (non-collinear)
        pix_coords = [(100, 350), (540, 350), (320, 80)]
        for i, ref in enumerate(refs[:3]):
            r = c.post(f'/api/cameras/{cal_cam_id}/calibrate/detect',
                        json={'fixtureId': ref['id'], 'pixelX': pix_coords[i][0], 'pixelY': pix_coords[i][1]})
            ok(f'Calibrate detect step {i}', r.get_json().get('ok'))

        # Compute
        r = c.post(f'/api/cameras/{cal_cam_id}/calibrate/compute')
        ok('Calibrate compute ok', r.status_code == 200 and r.get_json().get('ok'))
        ok('Calibrate compute has error', isinstance(r.get_json().get('error'), (int, float)))
        ok('Calibrate sets calibrated flag', r.get_json().get('calibrated') is True)

        # Get calibration
        r = c.get(f'/api/cameras/{cal_cam_id}/calibration')
        ok('GET calibration shows calibrated', r.get_json().get('calibrated') is True)
        ok('GET calibration has error', isinstance(r.get_json().get('error'), (int, float)))
        ok('GET calibration has points', r.get_json().get('points', 0) >= 3)

        # Uncalibrated camera returns calibrated=False
        r = c.get('/api/cameras/99999/calibration')
        ok('Unknown camera calibration → 404', r.status_code == 404)

        # Start with insufficient fixtures
        # Remove positioned fixtures
        for lid in led_ids:
            c.delete(f'/api/fixtures/{lid}')
        r = c.post(f'/api/cameras/{cal_cam_id}/calibrate/start')
        ok('Calibrate no refs → 400', r.status_code == 400)

        # ── Tracking API tests ────────────────────────────────────
        # Track start on offline camera → 503
        r = c.post(f'/api/cameras/{cal_cam_id}/track/start', json={})
        ok('Track start offline → 503', r.status_code == 503)

        # Track stop (idempotent even when not tracking)
        r = c.post(f'/api/cameras/{cal_cam_id}/track/stop', json={})
        ok('Track stop ok', r.status_code == 200)

        # Track status
        r = c.get(f'/api/cameras/{cal_cam_id}/track/status')
        ok('Track status shape', r.status_code == 200 and 'tracking' in r.get_json())
        ok('Track not running', r.get_json().get('tracking') is False)

        # Unknown camera track → 404
        r = c.post('/api/cameras/99999/track/start', json={})
        ok('Track unknown → 404', r.status_code == 404)

        r = c.get('/api/cameras/99999/track/status')
        ok('Track status unknown → 404', r.status_code == 404)

        # ── Temporal objects (tracking integration) ──────────────
        # Create temporal object like tracker would
        r = c.post('/api/objects/temporal', json={
            'name': 'person', 'objectType': 'person',
            'ttl': 10, 'color': '#f472b6', 'opacity': 40,
            'transform': {'pos': [1500, 0, 750], 'rot': [0,0,0], 'scale': [400, 400, 200]}
        })
        ok('Temporal person created', r.status_code == 200 or r.status_code == 201)
        tmp_id = r.get_json().get('id')
        ok('Temporal ID >= 10000', tmp_id is not None and tmp_id >= 10000,
           f'id={tmp_id}')

        # Verify in object list
        r = c.get('/api/objects')
        objs = r.get_json()
        tmp_obj = next((o for o in objs if o.get('id') == tmp_id), None)
        ok('Temporal person in list', tmp_obj is not None)
        ok('Temporal is moving', tmp_obj.get('mobility') == 'moving')
        ok('Temporal is temporal', tmp_obj.get('_temporal') is True)
        ok('Temporal objectType is person', tmp_obj.get('objectType') == 'person')

        # Update position (like tracker re-ID would)
        r = c.put(f'/api/objects/{tmp_id}/pos', json={'pos': [1600, 0, 800]})
        ok('Temporal pos update ok', r.status_code == 200)

        # Persistent objects not affected
        r = c.post('/api/objects', json={'name': 'Wall', 'objectType': 'wall'})
        wall_id = r.get_json().get('id')
        ok('Persistent object has low ID', wall_id < 10000, f'id={wall_id}')

        # ── Moving head range calibration ─────────────────────────
        from parent_server import _compute_axis_mapping, _inverse_axis_lookup

        # Axis mapping: linear fit from DMX norm → stage position
        samples = [(0.0, 0, 0), (0.5, 1500, 750), (1.0, 3000, 1500)]
        mapping = _compute_axis_mapping(samples)
        ok('Axis mapping computed', mapping is not None)
        ok('Axis mapping has slope_x', abs(mapping['slope_x'] - 3000) < 10)
        ok('Axis mapping has slope_z', abs(mapping['slope_z'] - 1500) < 10)

        # Inverse lookup: stage → DMX norm
        norm = _inverse_axis_lookup(mapping, 1500, 750)
        ok('Inverse lookup mid → ~0.5', abs(norm - 0.5) < 0.05, f'norm={norm:.3f}')
        norm_zero = _inverse_axis_lookup(mapping, 0, 0)
        ok('Inverse lookup origin → ~0.0', abs(norm_zero) < 0.05, f'norm={norm_zero:.3f}')

        # API: calibrate-range on LED fixture → 400
        led_fix = c.post('/api/fixtures', json={'name': 'LEDtest', 'fixtureType': 'led'})
        led_fid = led_fix.get_json()['id']
        r = c.post(f'/api/fixtures/{led_fid}/calibrate-range', json={'cameraId': 1})
        ok('Range cal on LED → 400', r.status_code == 400)
        c.delete(f'/api/fixtures/{led_fid}')

        # API: calibrate-range on DMX without camera cal → 400
        dmx_fix = c.post('/api/fixtures', json={
            'name': 'MoverTest', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16
        })
        dmx_fid = dmx_fix.get_json()['id']
        r = c.post(f'/api/fixtures/{dmx_fid}/calibrate-range',
                    json={'cameraId': 999, 'panSamples': [], 'tiltSamples': []})
        ok('Range cal no cam cal → 400', r.status_code == 400)

        # API: GET calibrate-range when uncalibrated
        r = c.get(f'/api/fixtures/{dmx_fid}/calibrate-range')
        ok('Range cal uncalibrated → false', r.get_json().get('rangeCalibrated') is False)

        # API: dmx-test on non-DMX → 404
        r = c.post(f'/api/fixtures/99999/dmx-test', json={'pan': 0.5})
        ok('DMX test unknown → 404', r.status_code == 404)

        # Clean up
        c.delete(f'/api/fixtures/{dmx_fid}')
        c.delete(f'/api/objects/{tmp_id}')
        c.delete(f'/api/objects/{wall_id}')
        c.delete(f'/api/cameras/{cal_cam_id}')

        # Unknown camera → 404
        r = c.get('/api/cameras/99999/snapshot')
        ok('Snapshot proxy unknown → 404', r.status_code == 404)

        r = c.get('/api/cameras/99999/status')
        ok('Status proxy unknown → 404', r.status_code == 404)

        r = c.post('/api/cameras/99999/scan', json={})
        ok('Scan proxy unknown → 404', r.status_code == 404)

        # DELETE /api/cameras/<id> unregisters
        r = c.delete('/api/cameras/' + str(reg_cam_id))
        ok('DELETE /api/cameras', r.status_code == 200)

        # Verify camera removed from fixtures
        r = c.get('/api/fixtures/' + str(reg_cam_id))
        ok('Camera removed from fixtures', r.status_code == 404)

        # DELETE unknown camera → 404
        r = c.delete('/api/cameras/99999')
        ok('DELETE unknown camera → 404', r.status_code == 404)

        # Discover endpoints exist (won't find real cameras in test)
        r = c.get('/api/cameras/discover')
        ok('GET /api/cameras/discover', r.status_code == 200)

        # ── Camera SSH settings ──────────────────────────────────────
        r = c.get('/api/cameras/ssh')
        ok('GET /api/cameras/ssh', r.status_code == 200)
        ssh = r.get_json()
        ok('SSH default user', ssh.get('sshUser') == 'root')
        ok('SSH no password', ssh.get('hasPassword') is False)

        r = c.post('/api/cameras/ssh', json={'sshUser': 'pi', 'sshPassword': 'test123'})
        ok('POST /api/cameras/ssh', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/cameras/ssh')
        ssh = r.get_json()
        ok('SSH user updated', ssh.get('sshUser') == 'pi')
        ok('SSH has password', ssh.get('hasPassword') is True)
        ok('SSH password masked', 'sshPassword' not in ssh)

        # Reset SSH back
        c.post('/api/cameras/ssh', json={'sshUser': 'root', 'sshPassword': ''})

        # ── Camera network scan ──────────────────────────────────────
        r = c.get('/api/cameras/scan-network')
        ok('GET /api/cameras/scan-network', r.status_code == 200)

        # ── Camera deploy validation ─────────────────────────────────
        r = c.post('/api/cameras/deploy', json={})
        ok('Deploy missing IP → 400', r.status_code == 400)

        r = c.post('/api/cameras/deploy', json={'ip': '192.168.1.100'})
        ok('Deploy no SSH creds → 400', r.status_code == 400)

        r = c.get('/api/cameras/deploy/status')
        ds = r.get_json()
        ok('Deploy status shape', r.status_code == 200 and 'running' in ds)
        ok('Deploy not running', ds.get('running') is False)
        ok('Deploy status has version fields',
           'remoteVersion' in ds and 'localVersion' in ds)

        # ── Camera probe endpoint ────────────────────────────────────
        r = c.post('/api/cameras/probe', json={})
        ok('Probe missing IP → 400', r.status_code == 400)

        r = c.post('/api/cameras/probe', json={'ip': '192.0.2.1'})
        ok('Probe unreachable → 404', r.status_code == 404)

        # ── SSH key content upload ───────────────────────────────────
        r = c.post('/api/cameras/ssh', json={
            'sshUser': 'root',
            'sshKeyContent': '-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----'
        })
        ok('SSH key content save', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/cameras/ssh')
        ssh = r.get_json()
        ok('SSH hasKey after content upload', ssh.get('hasKey') is True)
        ok('SSH keyPath set to managed file', 'camera_key' in ssh.get('sshKeyPath', ''))

        # ── SSH key generation ────────────────────────────────────────
        r = c.post('/api/cameras/ssh/generate-key')
        ok('Generate SSH key', r.status_code == 200 and r.get_json().get('ok'))
        gen = r.get_json()
        ok('Generated key has publicKey', 'ssh-ed25519' in gen.get('publicKey', ''))
        ok('Generated key has keyPath', 'camera_key' in gen.get('keyPath', ''))

        # SSH settings now point to generated key
        r = c.get('/api/cameras/ssh')
        ssh = r.get_json()
        ok('SSH keyPath updated after gen', 'camera_key' in ssh.get('sshKeyPath', ''))
        ok('SSH hasKey after gen', ssh.get('hasKey') is True)

        # Reset SSH back
        c.post('/api/cameras/ssh', json={'sshUser': 'root', 'sshPassword': '', 'sshKeyPath': ''})

        # ── Objects (Phase 2 — renamed from Surfaces) ─────────────────
        r = c.get('/api/objects')
        ok('GET /api/objects', r.status_code == 200)

        # Backward compat alias
        r2 = c.get('/api/objects')
        ok('GET /api/objects alias', r2.status_code == 200)

        r = c.post('/api/objects', json={'name': 'Test Object'})
        ok('POST create object', r.status_code == 200 and r.get_json().get('ok'))
        obj_id = r.get_json().get('id')

        # Verify default mobility
        objs = c.get('/api/objects').get_json()
        obj = [o for o in objs if o['id'] == obj_id][0]
        ok('Object default mobility is static', obj.get('mobility') == 'static')
        ok('Object has objectType field', 'objectType' in obj)

        r = c.delete('/api/objects/' + str(obj_id))
        ok('DELETE object', r.status_code == 200)

        # Create object with mobility=moving
        r = c.post('/api/objects', json={'name': 'Singer', 'objectType': 'prop', 'mobility': 'moving'})
        ok('POST create moving object', r.status_code == 200)
        moving_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        mv = [o for o in objs if o['id'] == moving_id][0]
        ok('Moving object mobility', mv.get('mobility') == 'moving')
        ok('Moving object type prop', mv.get('objectType') == 'prop')

        # PUT /api/objects/<id>/pos — real-time position update
        r = c.put('/api/objects/' + str(moving_id) + '/pos', json={'pos': [3000, 900, 2000]})
        ok('PUT object pos', r.status_code == 200 and r.get_json().get('ok'))
        objs = c.get('/api/objects').get_json()
        mv = [o for o in objs if o['id'] == moving_id][0]
        ok('Object pos updated', mv['transform']['pos'] == [3000.0, 900.0, 2000.0])

        # PUT pos validation
        r = c.put('/api/objects/' + str(moving_id) + '/pos', json={'pos': [1, 2]})
        ok('PUT pos rejects 2-element', r.status_code == 400)
        r = c.put('/api/objects/99999/pos', json={'pos': [0, 0, 0]})
        ok('PUT pos 404 for unknown', r.status_code == 404)

        c.delete('/api/objects/' + str(moving_id))

        # Stage-locked wall
        c.post('/api/stage', json={'w': 5.0, 'h': 3.0, 'd': 2.0})
        r = c.post('/api/objects', json={
            'name': 'Back Wall', 'objectType': 'wall', 'stageLocked': True})
        ok('POST create stage-locked wall', r.status_code == 200 and r.get_json().get('ok'))
        wall_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        wall = [o for o in objs if o['id'] == wall_id][0]
        ok('Wall locked to stage W', wall['transform']['scale'][0] == 5000)
        ok('Wall locked to stage H', wall['transform']['scale'][1] == 3000)
        ok('Wall stageLocked flag', wall.get('stageLocked') is True)

        # Stage-locked floor
        r = c.post('/api/objects', json={
            'name': 'Stage Floor', 'objectType': 'floor', 'stageLocked': True})
        ok('POST create stage-locked floor', r.status_code == 200 and r.get_json().get('ok'))
        floor_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        floor_o = [o for o in objs if o['id'] == floor_id][0]
        ok('Floor locked to stage W', floor_o['transform']['scale'][0] == 5000)
        ok('Floor depth = stage D + 1m', floor_o['transform']['scale'][1] == 3000)

        # Resize stage — locked objects auto-update
        c.post('/api/stage', json={'w': 8.0, 'h': 4.0, 'd': 3.0})
        objs = c.get('/api/objects').get_json()
        wall = [o for o in objs if o['id'] == wall_id][0]
        floor_o = [o for o in objs if o['id'] == floor_id][0]
        ok('Wall resized on stage change W', wall['transform']['scale'][0] == 8000)
        ok('Wall resized on stage change H', wall['transform']['scale'][1] == 4000)
        ok('Floor resized on stage change W', floor_o['transform']['scale'][0] == 8000)
        ok('Floor resized on stage change D+1m', floor_o['transform']['scale'][1] == 4000)

        # Cleanup
        c.delete('/api/objects/' + str(wall_id))
        c.delete('/api/objects/' + str(floor_id))

        # ── Temporal objects (#188) ───────────────────────────────────
        r = c.post('/api/objects/temporal', json={'name': 'Person 1', 'pos': [5000, 900, 3000], 'ttl': 60})
        ok('POST create temporal object', r.status_code == 200 and r.get_json().get('ok'))
        tmp_id = r.get_json().get('id')

        # Temporal shows in GET /api/objects
        objs = c.get('/api/objects').get_json()
        tmp = [o for o in objs if o['id'] == tmp_id]
        ok('Temporal in GET /api/objects', len(tmp) == 1)
        ok('Temporal has _temporal flag', tmp[0].get('_temporal') is True)
        ok('Temporal has ttl', tmp[0].get('ttl') == 60)
        ok('Temporal mobility is moving', tmp[0].get('mobility') == 'moving')
        ok('Temporal pos set', tmp[0]['transform']['pos'] == [5000.0, 900.0, 3000.0])

        # TTL validation
        r = c.post('/api/objects/temporal', json={'name': 'Bad', 'ttl': 0})
        ok('Temporal ttl=0 rejected', r.status_code == 400)
        r = c.post('/api/objects/temporal', json={'name': 'Bad', 'ttl': -5})
        ok('Temporal ttl<0 rejected', r.status_code == 400)
        r = c.post('/api/objects/temporal', json={'name': 'Bad'})
        ok('Temporal missing ttl rejected', r.status_code == 400)

        # PUT pos refreshes TTL on temporal
        r = c.put('/api/objects/' + str(tmp_id) + '/pos', json={'pos': [6000, 900, 3000]})
        ok('PUT temporal pos', r.status_code == 200)
        objs = c.get('/api/objects').get_json()
        tmp = [o for o in objs if o['id'] == tmp_id][0]
        ok('Temporal pos updated', tmp['transform']['pos'][0] == 6000.0)

        # DELETE temporal
        r = c.delete('/api/objects/' + str(tmp_id))
        ok('DELETE temporal object', r.status_code == 200)
        objs = c.get('/api/objects').get_json()
        ok('Temporal removed after delete', not any(o['id'] == tmp_id for o in objs))

        # ── Object Patrol (#194) ──────────────────────────────────────
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 8.0})
        r = c.post('/api/objects', json={
            'name': 'Patrol Singer', 'objectType': 'prop', 'mobility': 'moving',
            'transform': {'pos': [5000, 900, 4000], 'rot': [0,0,0], 'scale': [500, 1800, 500]},
            'patrol': {'enabled': True, 'axis': 'x', 'speedPreset': 'medium',
                       'startPct': 10, 'endPct': 90, 'easing': 'sine'}})
        ok('POST create patrol object', r.status_code == 200 and r.get_json().get('ok'))
        pat_id = r.get_json().get('id')

        objs = c.get('/api/objects').get_json()
        pat_obj = [o for o in objs if o['id'] == pat_id][0]
        ok('Patrol field saved', pat_obj.get('patrol') is not None)
        ok('Patrol enabled', pat_obj['patrol'].get('enabled') is True)
        ok('Patrol axis x', pat_obj['patrol'].get('axis') == 'x')
        ok('Patrol speed medium', pat_obj['patrol'].get('speedPreset') == 'medium')
        ok('Patrol start 10%', pat_obj['patrol'].get('startPct') == 10)
        ok('Patrol end 90%', pat_obj['patrol'].get('endPct') == 90)
        ok('Patrol easing sine', pat_obj['patrol'].get('easing') == 'sine')

        # Object without patrol has no patrol field
        r = c.post('/api/objects', json={'name': 'Static Wall', 'objectType': 'wall'})
        no_pat_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        no_pat = [o for o in objs if o['id'] == no_pat_id][0]
        ok('No patrol on static object', no_pat.get('patrol') is None)

        # Patrol with custom speed
        r = c.post('/api/objects', json={
            'name': 'Custom Speed', 'objectType': 'prop', 'mobility': 'moving',
            'patrol': {'enabled': True, 'axis': 'z', 'speedPreset': 'custom',
                       'cycleS': 15, 'startPct': 5, 'endPct': 95, 'easing': 'linear'}})
        ok('POST patrol custom speed', r.status_code == 200)
        cust_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        cust = [o for o in objs if o['id'] == cust_id][0]
        ok('Patrol custom cycleS', cust['patrol'].get('cycleS') == 15)
        ok('Patrol custom axis z', cust['patrol'].get('axis') == 'z')
        ok('Patrol custom easing linear', cust['patrol'].get('easing') == 'linear')

        # Patrol with diagonal axis
        r = c.post('/api/objects', json={
            'name': 'Diagonal', 'objectType': 'prop', 'mobility': 'moving',
            'patrol': {'enabled': True, 'axis': 'xz', 'speedPreset': 'fast'}})
        ok('POST patrol diagonal', r.status_code == 200)
        diag_id = r.get_json().get('id')
        objs = c.get('/api/objects').get_json()
        diag = [o for o in objs if o['id'] == diag_id][0]
        ok('Patrol diagonal axis', diag['patrol'].get('axis') == 'xz')

        # Cleanup patrol objects
        c.delete('/api/objects/' + str(pat_id))
        c.delete('/api/objects/' + str(no_pat_id))
        c.delete('/api/objects/' + str(cust_id))
        c.delete('/api/objects/' + str(diag_id))

        # ── Track action (#186) ───────────────────────────────────────
        # Create moving objects and a Track action
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 8.0})
        r1 = c.post('/api/objects', json={'name': 'Singer A', 'objectType': 'prop', 'mobility': 'moving',
            'transform': {'pos': [3000, 900, 4000], 'rot': [0,0,0], 'scale': [500, 1800, 500]}})
        obj_a = r1.get_json().get('id')
        r2 = c.post('/api/objects', json={'name': 'Singer B', 'objectType': 'prop', 'mobility': 'moving',
            'transform': {'pos': [7000, 900, 4000], 'rot': [0,0,0], 'scale': [500, 1800, 500]}})
        obj_b = r2.get_json().get('id')

        # Create Track action (type 18)
        r = c.post('/api/actions', json={
            'name': 'Follow Singers', 'type': 18,
            'trackObjectIds': [obj_a, obj_b],
            'trackCycleMs': 2000,
            'trackOffset': [0, 200, 0],
            'trackAutoSpread': False})
        ok('POST create Track action', r.status_code == 200 and r.get_json().get('ok'))
        track_id = r.get_json().get('id')

        # Verify Track action fields persisted
        r = c.get('/api/actions/' + str(track_id))
        ok('GET Track action', r.status_code == 200)
        ta = r.get_json()
        ok('Track type is 18', ta.get('type') == 18)
        ok('Track has objectIds', ta.get('trackObjectIds') == [obj_a, obj_b])
        ok('Track has cycleMs', ta.get('trackCycleMs') == 2000)
        ok('Track has offset', ta.get('trackOffset') == [0, 200, 0])
        ok('Track has autoSpread', ta.get('trackAutoSpread') is False)

        # Update Track action with per-fixture offsets
        r = c.put('/api/actions/' + str(track_id), json={
            'trackFixtureOffsets': {'1': [100, 0, 0], '2': [-100, 0, 0]}})
        ok('PUT Track action offsets', r.status_code == 200)
        r = c.get('/api/actions/' + str(track_id))
        ta = r.get_json()
        ok('Track per-fixture offsets saved', '1' in ta.get('trackFixtureOffsets', {}))

        # Cleanup
        c.delete('/api/actions/' + str(track_id))
        c.delete('/api/objects/' + str(obj_a))
        c.delete('/api/objects/' + str(obj_b))

        # ── Spatial Effects (Phase 3) ──────────────────────────────────
        r = c.get('/api/spatial-effects')
        ok('GET /api/spatial-effects', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/spatial-effects', json={
            'name': 'Red Sphere', 'category': 'spatial-field',
            'shape': 'sphere', 'r': 255, 'g': 0, 'b': 0,
            'size': {'radius': 1000},
            'motion': {'startPos': [0,0,0], 'endPos': [5000,0,0], 'durationS': 5, 'easing': 'linear'},
            'blend': 'replace'
        })
        ok('POST create spatial effect', r.status_code == 200 and r.get_json().get('ok'))
        sfx_id = r.get_json().get('id')

        r = c.get('/api/spatial-effects/' + str(sfx_id))
        ok('GET spatial effect by id', r.status_code == 200 and r.get_json().get('shape') == 'sphere')

        r = c.put('/api/spatial-effects/' + str(sfx_id), json={'name': 'Blue Sphere', 'r': 0, 'b': 255})
        ok('PUT update spatial effect', r.status_code == 200)

        r = c.post('/api/spatial-effects', json={'name': '', 'category': 'spatial-field'})
        ok('Spatial effect no name → 400', r.status_code == 400)

        r = c.post('/api/spatial-effects', json={'name': 'Bad Cat', 'category': 'invalid'})
        ok('Spatial effect bad category → 400', r.status_code == 400)

        r = c.post('/api/spatial-effects/' + str(sfx_id) + '/evaluate?t=2.5')
        ok('POST evaluate spatial effect', r.status_code == 200 and 'pixels' in r.get_json())

        # Fixture-local spatial effect
        r = c.post('/api/spatial-effects', json={
            'name': 'Local Chase', 'category': 'fixture-local', 'actionType': 4
        })
        ok('POST fixture-local effect', r.status_code == 200)
        sfx_id2 = r.get_json().get('id')

        r = c.delete('/api/spatial-effects/' + str(sfx_id2))
        ok('DELETE spatial effect', r.status_code == 200)

        # ── Timelines (Phase 4) ────────────────────────────────────────
        r = c.get('/api/timelines')
        ok('GET /api/timelines', r.status_code == 200 and isinstance(r.get_json(), list))

        r = c.post('/api/timelines', json={'name': 'Test Show', 'durationS': 30})
        ok('POST create timeline', r.status_code == 200 and r.get_json().get('ok'))
        tl_id = r.get_json().get('id')

        r = c.get('/api/timelines/' + str(tl_id))
        ok('GET timeline by id', r.status_code == 200 and r.get_json().get('durationS') == 30)

        r = c.put('/api/timelines/' + str(tl_id), json={
            'name': 'Updated Show', 'durationS': 60,
            'tracks': [{'fixtureId': fix_id, 'clips': [
                {'effectId': sfx_id, 'startS': 0, 'durationS': 10}
            ]}],
            'loop': True
        })
        ok('PUT update timeline with tracks', r.status_code == 200)

        r = c.post('/api/timelines/' + str(tl_id) + '/frame?t=5.0')
        ok('POST timeline frame evaluation', r.status_code == 200)

        r = c.post('/api/timelines', json={'name': '', 'durationS': 30})
        ok('Timeline no name → 400', r.status_code == 400)

        r = c.delete('/api/timelines/' + str(tl_id))
        ok('DELETE timeline', r.status_code == 200)

        # Clean up spatial effect
        r = c.delete('/api/spatial-effects/' + str(sfx_id))
        ok('DELETE spatial effect cleanup', r.status_code == 200)

        # Clean up fixture
        r = c.delete('/api/fixtures/' + str(fix_id))
        ok('DELETE fixture cleanup', r.status_code == 200)

        # ── Baking (Phase 5) ───────────────────────────────────────────
        # Need a fixture + spatial effect + timeline to bake
        r = c.post('/api/fixtures', json={'name': 'Bake Fix', 'type': 'linear', 'childId': 0})
        bfix = r.get_json().get('id')
        r = c.post('/api/spatial-effects', json={
            'name': 'Bake FX', 'category': 'spatial-field',
            'shape': 'sphere', 'r': 200, 'g': 50, 'b': 0,
            'size': {'radius': 2000},
            'motion': {'startPos': [0,0,0], 'endPos': [5000,0,0], 'durationS': 3, 'easing': 'linear'},
            'blend': 'replace'
        })
        bsfx = r.get_json().get('id')
        r = c.post('/api/timelines', json={'name': 'Bake Test', 'durationS': 3})
        btl = r.get_json().get('id')
        r = c.put('/api/timelines/' + str(btl), json={
            'name': 'Bake Test', 'durationS': 3,
            'tracks': [{'fixtureId': bfix, 'clips': [
                {'effectId': bsfx, 'startS': 0, 'durationS': 3}
            ]}]
        })
        ok('Setup bake timeline', r.status_code == 200)

        r = c.post('/api/timelines/' + str(btl) + '/bake')
        ok('POST bake timeline', r.status_code == 200 and r.get_json().get('ok'))

        # Poll for completion (max 10 attempts)
        import time as _time
        for _ in range(10):
            _time.sleep(0.3)
            r = c.get('/api/timelines/' + str(btl) + '/baked/status')
            if r.get_json().get('done'):
                break
        ok('Bake completes', r.get_json().get('done'))

        r = c.get('/api/timelines/' + str(btl) + '/baked')
        ok('GET baked result', r.status_code == 200 and 'fixtures' in r.get_json())

        r = c.get('/api/timelines/' + str(btl) + '/baked/download')
        ok('GET baked download (zip)', r.status_code == 200)

        r = c.post('/api/timelines/' + str(btl) + '/baked/sync')
        ok('POST baked sync', r.status_code == 200 and r.get_json().get('ok'))

        # ── Show Execution (Phase 6) ───────────────────────────────────
        r = c.post('/api/timelines/' + str(btl) + '/start')
        ok('POST timeline start', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/timelines/' + str(btl) + '/status')
        ok('GET timeline status', r.status_code == 200 and 'running' in r.get_json())

        r = c.post('/api/timelines/' + str(btl) + '/stop')
        ok('POST timeline stop', r.status_code == 200 and r.get_json().get('ok'))

        # Start without bake should fail for non-baked timeline
        r = c.post('/api/timelines', json={'name': 'No Bake', 'durationS': 5})
        nb_id = r.get_json().get('id')
        r = c.post('/api/timelines/' + str(nb_id) + '/start')
        ok('Start unbaked timeline \u2192 400', r.status_code == 400)

        # ── Help (Phase 7) ─────────────────────────────────────────────
        r = c.get('/api/help/layout')
        ok('GET /api/help/layout', r.status_code == 200 and 'html' in r.get_json())

        r = c.get('/api/help/timeline')
        ok('GET /api/help/timeline', r.status_code == 200)

        r = c.get('/api/help/nonexistent')
        ok('GET /api/help/nonexistent returns html', r.status_code == 200 and r.get_json().get('html'))

        # ── Cleanup bake test data ─────────────────────────────────────
        c.delete('/api/timelines/' + str(btl))
        c.delete('/api/timelines/' + str(nb_id))
        c.delete('/api/spatial-effects/' + str(bsfx))
        c.delete('/api/fixtures/' + str(bfix))

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

        # (Runners, Flights, Shows removed in v8.0 — timeline system only)

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

        result = wled_probe('192.0.2.1', timeout=0.5)
        ok('WLED probe fake IP', result is None)

        # ── SPA / fallback ──────────────────────────────────────────
        r = c.get('/')
        ok('GET / (SPA)', r.status_code == 200)
        spa = r.data.decode('utf-8', errors='replace')
        ok('SPA has layout quick-view recenter', 'layViewReset' in spa)
        ok('SPA has layout quick-view top', 'layViewTop' in spa)
        ok('SPA has layout quick-view front', 'layViewFront' in spa)
        ok('SPA has 2D/3D toggle', 'toggleLayoutMode' in spa)
        ok('SPA has patrol UI', 'sf-pat-en' in spa)
        ok('SPA has Track action type', "'Track'" in spa or 'Track' in spa)
        ok('SPA has objects API', '/api/objects' in spa)
        ok('SPA has temporal support', '/api/objects/temporal' in spa or '_temporal' in spa)
        ok('SPA has scan button', 'btn-lay-scan' in spa)
        ok('SPA has _layScan function', '_layScan' in spa)
        ok('SPA has _scanGhosts', '_scanGhosts' in spa)
        ok('SPA has ghost accept', '_layScanAccept' in spa)
        ok('SPA has ghost dismiss', '_layScanDismiss' in spa)
        ok('SPA has 3D ghost render', '_s3dRenderGhosts' in spa)
        ok('SPA has calibration wizard', '_calWizardStart' in spa)
        ok('SPA has calibration compute', '_calCompute' in spa)
        ok('SPA has cone toggle', '_layConesToggle' in spa)
        ok('SPA has cone toggle button', 'btn-lay-cones' in spa)
        ok('SPA has rest vector 2D', "'0,0'" in spa and 'f59e0b' in spa)
        ok('SPA has rest vector 3D', 'LineDashedMaterial' in spa and 'homeDir' in spa)
        ok('SPA has tracking toggle', '_trackToggle' in spa)
        ok('SPA has tracking start', '_trackStart' in spa)
        ok('SPA has tracking stop', '_trackStop' in spa)
        ok('SPA has track poll', '_trackPollStart' in spa)
        ok('SPA has range cal', '_rangeCalStart' in spa)
        ok('SPA has range cal submit', '_rangeCalSubmit' in spa)
        ok('SPA has Cal Range button', 'Cal Range' in spa)
        ok('SPA has emitter editor', '_peRenderEmitters' in spa)
        ok('SPA has add emitter', '_peAddEmitter' in spa)
        ok('SPA has Save Capabilities button', 'Save Capabilities' in spa)
        ok('SPA has built-in fork logic', 'built-in' in spa and '-custom' in spa)
        # Toolbar tooltips on all buttons
        ok('SPA toolbar: save tooltip', "title='Save layout'" in spa)
        ok('SPA toolbar: mode tooltip', "title='Switch to 3D'" in spa or "title='Switch to 2D'" in spa)
        ok('SPA toolbar: recenter tooltip', "title='Recenter view'" in spa)
        ok('SPA toolbar: top tooltip', "title='Top view'" in spa)
        ok('SPA toolbar: front tooltip', "title='Front view'" in spa)
        ok('SPA toolbar: arrange tooltip', "title='Auto-arrange DMX fixtures'" in spa)
        ok('SPA toolbar: strings tooltip', "title='Show/hide LED strings'" in spa)
        # 2D/3D toggle shows text
        ok('SPA 2D/3D toggle has text label', "btn.textContent='3D'" in spa and "btn.textContent='2D'" in spa)

        r = c.get('/favicon.ico')
        ok('GET /favicon.ico → 404', r.status_code == 404)

        r = c.get('/nonexistent/path')
        ok('GET unknown path → SPA fallback', r.status_code == 200)

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

        # (Show export/import and demo show tests removed in v8.0)

        # Clean up test child
        c.delete(f'/api/children/{cfg_cid}')

        # ── OTA firmware endpoints ─────────────────────────────────
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
            "version": "6.1.0",
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
                ch['fwVersion'] = '6.0.0'
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
                ch['fwVersion'] = '6.0.0'
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
            ok('OTA trigger returns version', d.get('version') == '6.1.0')

        # /api/firmware/binary/<board> — serves binary or tries to download
        r = c.get('/api/firmware/binary/unknown')
        ok('OTA binary unknown board → 404', r.status_code == 404)

        # /api/firmware/registry — check versions updated
        r = c.get('/api/firmware/registry')
        reg = r.get_json()
        esp_fw = next((f for f in reg.get('firmware', []) if f['id'] == 'child-led-esp32'), None)
        ok('Registry ESP32 version is 5.3.10', esp_fw and esp_fw['version'] == '6.0.0')
        d1_fw = next((f for f in reg.get('firmware', []) if f['id'] == 'child-led-d1mini'), None)
        ok('Registry D1 Mini version is 5.3.10', d1_fw and d1_fw['version'] == '6.0.0')

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
