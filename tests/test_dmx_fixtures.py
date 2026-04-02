#!/usr/bin/env python3
"""
test_dmx_fixtures.py — Comprehensive test suite for DMX fixture type selector (#91).

Tests fixture CRUD with fixtureType 'led' and 'dmx', backwards compatibility
migration, DMX validation, PUT validation, mixed fixture lists, cascade delete,
profile linkage, address bounds, auto-create, and factory reset.

Usage:
    python tests/test_dmx_fixtures.py
"""

import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def run():
    with app.test_client() as c:

        # ── Factory reset — clean slate ─────────────────────────────
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # ================================================================
        # 1. LED FIXTURE CRUD
        # ================================================================
        print('── 1. LED fixture CRUD ──')

        r = c.get('/api/fixtures')
        ok('Empty fixture list', r.status_code == 200 and r.get_json() == [])

        # Create LED fixture (explicit type)
        r = c.post('/api/fixtures', json={
            'name': 'LED Strip 1', 'type': 'linear', 'fixtureType': 'led',
            'childId': None
        })
        d = r.get_json()
        ok('POST LED fixture', r.status_code == 200 and d.get('ok'))
        led1 = d.get('id')

        # GET LED fixture — verify fields
        r = c.get(f'/api/fixtures/{led1}')
        d = r.get_json()
        ok('LED fixture name', d.get('name') == 'LED Strip 1')
        ok('LED fixture type=linear', d.get('type') == 'linear')
        ok('LED fixtureType=led', d.get('fixtureType') == 'led')
        ok('LED fixture has rotation', d.get('rotation') == [0, 0, 0])
        ok('LED fixture has aoeRadius', d.get('aoeRadius') == 1000)
        ok('LED fixture no dmxUniverse', 'dmxUniverse' not in d)
        ok('LED fixture no dmxStartAddr', 'dmxStartAddr' not in d)
        ok('LED fixture no dmxChannelCount', 'dmxChannelCount' not in d)

        # Create second LED fixture (default fixtureType)
        r = c.post('/api/fixtures', json={'name': 'LED Strip 2', 'type': 'linear'})
        d = r.get_json()
        ok('POST LED fixture (default type)', r.status_code == 200 and d.get('ok'))
        led2 = d.get('id')
        ok('Fixture IDs auto-increment', led2 > led1)

        r = c.get(f'/api/fixtures/{led2}')
        ok('Default fixtureType=led', r.get_json().get('fixtureType') == 'led')

        # Update LED fixture
        r = c.put(f'/api/fixtures/{led1}', json={
            'name': 'Renamed LED', 'rotation': [0, 0, 90]
        })
        ok('PUT LED fixture', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get(f'/api/fixtures/{led1}')
        d = r.get_json()
        ok('LED name updated', d.get('name') == 'Renamed LED')
        ok('LED rotation updated', d.get('rotation') == [0, 0, 90])

        # Delete LED fixture
        r = c.delete(f'/api/fixtures/{led2}')
        ok('DELETE LED fixture', r.status_code == 200 and r.get_json().get('ok'))
        r = c.get(f'/api/fixtures/{led2}')
        ok('Deleted LED fixture → 404', r.status_code == 404)

        # ================================================================
        # 2. DMX FIXTURE CRUD
        # ================================================================
        print('── 2. DMX fixture CRUD ──')

        r = c.post('/api/fixtures', json={
            'name': 'Moving Head 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit'
        })
        d = r.get_json()
        ok('POST DMX fixture', r.status_code == 200 and d.get('ok'))
        dmx1 = d.get('id')

        r = c.get(f'/api/fixtures/{dmx1}')
        d = r.get_json()
        ok('DMX fixtureType=dmx', d.get('fixtureType') == 'dmx')
        ok('DMX universe=1', d.get('dmxUniverse') == 1)
        ok('DMX startAddr=1', d.get('dmxStartAddr') == 1)
        ok('DMX channelCount=16', d.get('dmxChannelCount') == 16)
        ok('DMX profileId set', d.get('dmxProfileId') == 'generic-moving-head-16bit')
        ok('DMX type=point', d.get('type') == 'point')
        ok('DMX has name', d.get('name') == 'Moving Head 1')

        # Create second DMX fixture (different universe)
        r = c.post('/api/fixtures', json={
            'name': 'RGB Par 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 2, 'dmxStartAddr': 1, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb'
        })
        ok('POST DMX fixture universe 2', r.status_code == 200)
        dmx2 = r.get_json().get('id')

        # Create DMX fixture without profileId (optional)
        r = c.post('/api/fixtures', json={
            'name': 'Dimmer Pack', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 100, 'dmxChannelCount': 4
        })
        ok('POST DMX no profile', r.status_code == 200)
        dmx3 = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{dmx3}')
        ok('DMX no profile → null', r.get_json().get('dmxProfileId') is None)

        # Update DMX fixture
        r = c.put(f'/api/fixtures/{dmx1}', json={
            'dmxStartAddr': 50, 'dmxUniverse': 3, 'dmxChannelCount': 13,
            'dmxProfileId': 'generic-moving-head'
        })
        ok('PUT DMX fixture', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get(f'/api/fixtures/{dmx1}')
        d = r.get_json()
        ok('DMX addr updated to 50', d.get('dmxStartAddr') == 50)
        ok('DMX universe updated to 3', d.get('dmxUniverse') == 3)
        ok('DMX channelCount updated to 13', d.get('dmxChannelCount') == 13)
        ok('DMX profileId updated', d.get('dmxProfileId') == 'generic-moving-head')

        # Update DMX fixture name only
        r = c.put(f'/api/fixtures/{dmx1}', json={'name': 'Mover A'})
        ok('PUT DMX name only', r.status_code == 200)
        r = c.get(f'/api/fixtures/{dmx1}')
        ok('DMX name persisted', r.get_json().get('name') == 'Mover A')
        ok('DMX addr unchanged after name update', r.get_json().get('dmxStartAddr') == 50)

        # Delete DMX fixture
        r = c.delete(f'/api/fixtures/{dmx2}')
        ok('DELETE DMX fixture', r.status_code == 200)
        r = c.get(f'/api/fixtures/{dmx2}')
        ok('Deleted DMX → 404', r.status_code == 404)

        # ================================================================
        # 3. CREATE VALIDATION — fixtureType
        # ================================================================
        print('── 3. Create validation ──')

        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'invalid'
        })
        ok('Bad fixtureType → 400', r.status_code == 400)
        ok('Error msg mentions fixtureType', 'fixtureType' in r.get_json().get('err', ''))

        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': ''
        })
        ok('Empty fixtureType → 400', r.status_code == 400)

        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'invalid', 'fixtureType': 'led'
        })
        ok('Bad geometry type → 400', r.status_code == 400)

        # ================================================================
        # 4. CREATE VALIDATION — DMX fields
        # ================================================================
        print('── 4. DMX create validation ──')

        # Missing universe
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX missing universe → 400', r.status_code == 400)

        # Universe = 0
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 0, 'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX universe=0 → 400', r.status_code == 400)

        # Universe = string
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 'one', 'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX universe=string → 400', r.status_code == 400)

        # Missing startAddr
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxChannelCount': 3
        })
        ok('DMX missing startAddr → 400', r.status_code == 400)

        # startAddr = 0
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 0, 'dmxChannelCount': 3
        })
        ok('DMX startAddr=0 → 400', r.status_code == 400)

        # startAddr = 513
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 513, 'dmxChannelCount': 3
        })
        ok('DMX startAddr=513 → 400', r.status_code == 400)

        # startAddr = -1
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': -1, 'dmxChannelCount': 3
        })
        ok('DMX startAddr=-1 → 400', r.status_code == 400)

        # Missing channelCount
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1
        })
        ok('DMX missing channelCount → 400', r.status_code == 400)

        # channelCount = 0
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 0
        })
        ok('DMX channelCount=0 → 400', r.status_code == 400)

        # channelCount = string
        r = c.post('/api/fixtures', json={
            'name': 'Bad', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 'many'
        })
        ok('DMX channelCount=string → 400', r.status_code == 400)

        # ================================================================
        # 5. PUT VALIDATION — DMX fields
        # ================================================================
        print('── 5. PUT validation ──')

        # Invalid fixtureType on update
        r = c.put(f'/api/fixtures/{dmx1}', json={'fixtureType': 'invalid'})
        ok('PUT bad fixtureType → 400', r.status_code == 400)

        # Invalid geometry type on update
        r = c.put(f'/api/fixtures/{dmx1}', json={'type': 'bogus'})
        ok('PUT bad geometry type → 400', r.status_code == 400)

        # Invalid dmxStartAddr on update
        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxStartAddr': 0})
        ok('PUT dmxStartAddr=0 → 400', r.status_code == 400)

        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxStartAddr': 513})
        ok('PUT dmxStartAddr=513 → 400', r.status_code == 400)

        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxStartAddr': 'ten'})
        ok('PUT dmxStartAddr=string → 400', r.status_code == 400)

        # Invalid dmxUniverse on update
        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxUniverse': 0})
        ok('PUT dmxUniverse=0 → 400', r.status_code == 400)

        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxUniverse': -5})
        ok('PUT dmxUniverse=-5 → 400', r.status_code == 400)

        # Invalid dmxChannelCount on update
        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxChannelCount': 0})
        ok('PUT dmxChannelCount=0 → 400', r.status_code == 400)

        r = c.put(f'/api/fixtures/{dmx1}', json={'dmxChannelCount': -1})
        ok('PUT dmxChannelCount=-1 → 400', r.status_code == 400)

        # Verify fixture unchanged after failed PUTs
        r = c.get(f'/api/fixtures/{dmx1}')
        d = r.get_json()
        ok('Fixture unchanged after bad PUTs', d.get('dmxStartAddr') == 50 and d.get('dmxUniverse') == 3)

        # PUT nonexistent fixture → 404
        r = c.put('/api/fixtures/99999', json={'name': 'Ghost'})
        ok('PUT nonexistent → 404', r.status_code == 404)

        # DELETE nonexistent → 200 (idempotent)
        r = c.delete('/api/fixtures/99999')
        ok('DELETE nonexistent', r.status_code == 200)

        # ================================================================
        # 6. VALID DMX BOUNDARY VALUES
        # ================================================================
        print('── 6. DMX boundary values ──')

        # startAddr=1 (minimum)
        r = c.post('/api/fixtures', json={
            'name': 'Addr 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 1
        })
        ok('DMX startAddr=1 (min)', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # startAddr=512 (maximum)
        r = c.post('/api/fixtures', json={
            'name': 'Addr 512', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 512, 'dmxChannelCount': 1
        })
        ok('DMX startAddr=512 (max)', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # channelCount=1 (minimum)
        r = c.post('/api/fixtures', json={
            'name': 'Ch 1', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 1
        })
        ok('DMX channelCount=1 (min)', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # Large channelCount (512)
        r = c.post('/api/fixtures', json={
            'name': 'Full Universe', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 512
        })
        ok('DMX channelCount=512', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # High universe number
        r = c.post('/api/fixtures', json={
            'name': 'High Uni', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 32768, 'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX universe=32768', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # ================================================================
        # 7. MIXED FIXTURE LIST
        # ================================================================
        print('── 7. Mixed fixture list ──')

        r = c.get('/api/fixtures')
        flist = r.get_json()
        ok('Fixture list is array', isinstance(flist, list))
        led_count = sum(1 for f in flist if f.get('fixtureType') == 'led')
        dmx_count = sum(1 for f in flist if f.get('fixtureType') == 'dmx')
        ok('Has LED fixtures in list', led_count >= 1, f'led={led_count}')
        ok('Has DMX fixtures in list', dmx_count >= 1, f'dmx={dmx_count}')

        # Verify each fixture has fixtureType
        all_have_type = all('fixtureType' in f for f in flist)
        ok('All fixtures have fixtureType', all_have_type)

        # Verify all IDs unique
        ids = [f['id'] for f in flist]
        ok('All fixture IDs unique', len(ids) == len(set(ids)))

        # ================================================================
        # 8. GEOMETRY TYPES
        # ================================================================
        print('── 8. Geometry types ──')

        for geom in ('linear', 'point', 'surface', 'group'):
            r = c.post('/api/fixtures', json={
                'name': f'Test {geom}', 'type': geom, 'fixtureType': 'led'
            })
            ok(f'Create {geom} LED fixture', r.status_code == 200)
            fid = r.get_json().get('id')
            r = c.get(f'/api/fixtures/{fid}')
            ok(f'{geom} type persisted', r.get_json().get('type') == geom)
            c.delete(f'/api/fixtures/{fid}')

        for geom in ('linear', 'point', 'surface', 'group'):
            r = c.post('/api/fixtures', json={
                'name': f'Test DMX {geom}', 'type': geom, 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 3
            })
            ok(f'Create {geom} DMX fixture', r.status_code == 200)
            c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # ================================================================
        # 9. DEFAULT NAME GENERATION
        # ================================================================
        print('── 9. Default names ──')

        r = c.post('/api/fixtures', json={
            'type': 'linear', 'fixtureType': 'led'
        })
        ok('LED fixture gets default name', r.status_code == 200)
        fid = r.get_json().get('id')
        r = c.get(f'/api/fixtures/{fid}')
        name = r.get_json().get('name', '')
        ok('Default name starts with Fixture', name.startswith('Fixture'))
        c.delete(f'/api/fixtures/{fid}')

        r = c.post('/api/fixtures', json={
            'name': '', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 3
        })
        ok('DMX empty name gets default', r.status_code == 200)
        fid = r.get_json().get('id')
        r = c.get(f'/api/fixtures/{fid}')
        ok('DMX default name non-empty', len(r.get_json().get('name', '')) > 0)
        c.delete(f'/api/fixtures/{fid}')

        # ================================================================
        # 10. FIXTURE RESOLVE (LED)
        # ================================================================
        print('── 10. Fixture resolve ──')

        r = c.post(f'/api/fixtures/{led1}/resolve')
        d = r.get_json()
        ok('Resolve LED fixture', r.status_code == 200)
        ok('Resolve has pixelPositions', 'pixelPositions' in d)

        # Resolve nonexistent → 404
        r = c.post('/api/fixtures/99999/resolve')
        ok('Resolve nonexistent → 404', r.status_code == 404)

        # ================================================================
        # 11. CHILD REGISTRATION + LED FIXTURE AUTO-CREATE
        # ================================================================
        print('── 11. Child + fixture auto-create ──')

        # Register a fake child
        r = c.post('/api/children', json={'ip': '10.0.0.50'})
        d = r.get_json()
        ok('Register child', d.get('ok'))
        child_id = d.get('id')

        # Auto-create fixture for that child
        r = c.post('/api/fixtures', json={
            'name': 'Auto LED', 'type': 'linear', 'fixtureType': 'led',
            'childId': child_id
        })
        ok('Create fixture linked to child', r.status_code == 200)
        linked_fix_id = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{linked_fix_id}')
        d = r.get_json()
        ok('Linked fixture has childId', d.get('childId') == child_id)
        ok('Linked fixture fixtureType=led', d.get('fixtureType') == 'led')

        # ================================================================
        # 12. CASCADE DELETE — LED fixture removes child
        # ================================================================
        print('── 12. Cascade delete ──')

        # Register another child for cascade test
        r = c.post('/api/children', json={'ip': '10.0.0.51'})
        cascade_child_id = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'Cascade Test', 'type': 'linear', 'fixtureType': 'led',
            'childId': cascade_child_id
        })
        cascade_fix_id = r.get_json().get('id')

        # Delete fixture — the SPA cascade-deletes child via JS, but the API
        # only deletes the fixture. The SPA handles child cleanup separately.
        r = c.delete(f'/api/fixtures/{cascade_fix_id}')
        ok('DELETE linked fixture', r.status_code == 200)

        r = c.get(f'/api/fixtures/{cascade_fix_id}')
        ok('Deleted fixture gone', r.status_code == 404)

        # Child still exists (server doesn't cascade — SPA does)
        r = c.get('/api/children')
        child_ids = [ch['id'] for ch in r.get_json()]
        ok('Child still exists after fixture delete', cascade_child_id in child_ids)

        # Clean up cascade child
        c.delete(f'/api/children/{cascade_child_id}')

        # (Migration tests removed in v8.0 — /api/migrate/layout endpoint removed)

        # ================================================================
        # 13. GROUP FIXTURES
        # ================================================================
        print('── 14. Group fixtures ──')

        # Create LED fixtures for grouping
        r = c.post('/api/fixtures', json={
            'name': 'Group Member A', 'type': 'linear', 'fixtureType': 'led'
        })
        ga = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'Group Member B', 'type': 'linear', 'fixtureType': 'led'
        })
        gb = r.get_json().get('id')

        # Create group fixture
        r = c.post('/api/fixtures', json={
            'name': 'My Group', 'type': 'group', 'fixtureType': 'led',
            'childIds': [ga, gb]
        })
        ok('Create group fixture', r.status_code == 200)
        grp_id = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{grp_id}')
        d = r.get_json()
        ok('Group type=group', d.get('type') == 'group')
        ok('Group has childIds', d.get('childIds') == [ga, gb])

        # Group with DMX fixtures
        r = c.post('/api/fixtures', json={
            'name': 'DMX Group', 'type': 'group', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 200, 'dmxChannelCount': 6,
            'childIds': []
        })
        ok('Create DMX group fixture', r.status_code == 200)
        c.delete(f'/api/fixtures/{r.get_json().get("id")}')

        # Clean up
        c.delete(f'/api/fixtures/{ga}')
        c.delete(f'/api/fixtures/{gb}')
        c.delete(f'/api/fixtures/{grp_id}')

        # ================================================================
        # 15. STRINGS OVERRIDE ON LED FIXTURE
        # ================================================================
        print('── 15. Strings override ──')

        r = c.post('/api/fixtures', json={
            'name': 'Custom Strings', 'type': 'linear', 'fixtureType': 'led',
            'strings': [{'leds': 100, 'mm': 5000, 'sdir': 0}]
        })
        ok('LED fixture with strings', r.status_code == 200)
        sfid = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{sfid}')
        strings = r.get_json().get('strings', [])
        ok('Strings persisted', len(strings) == 1 and strings[0].get('leds') == 100)

        r = c.put(f'/api/fixtures/{sfid}', json={
            'strings': [{'leds': 150, 'mm': 6000, 'sdir': 1}]
        })
        ok('PUT strings update', r.status_code == 200)

        r = c.get(f'/api/fixtures/{sfid}')
        ok('Strings updated', r.get_json().get('strings')[0].get('leds') == 150)

        c.delete(f'/api/fixtures/{sfid}')

        # ================================================================
        # 16. MESHFILE AND AOE RADIUS
        # ================================================================
        print('── 16. MeshFile and AoE ──')

        r = c.post('/api/fixtures', json={
            'name': 'Mesh Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 8,
            'meshFile': 'moving-head.glb', 'aoeRadius': 3000
        })
        ok('Create fixture with meshFile', r.status_code == 200)
        mfid = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{mfid}')
        d = r.get_json()
        ok('meshFile persisted', d.get('meshFile') == 'moving-head.glb')
        ok('aoeRadius=3000', d.get('aoeRadius') == 3000)

        r = c.put(f'/api/fixtures/{mfid}', json={'meshFile': None, 'aoeRadius': 500})
        ok('Clear meshFile', r.status_code == 200)
        r = c.get(f'/api/fixtures/{mfid}')
        ok('meshFile cleared', r.get_json().get('meshFile') is None)
        ok('aoeRadius updated', r.get_json().get('aoeRadius') == 500)

        c.delete(f'/api/fixtures/{mfid}')

        # ================================================================
        # 17. MULTIPLE UNIVERSE DMX FIXTURES
        # ================================================================
        print('── 17. Multiple universes ──')

        uni_ids = []
        for u in range(1, 5):
            r = c.post('/api/fixtures', json={
                'name': f'Uni{u} Par', 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': u, 'dmxStartAddr': 1, 'dmxChannelCount': 3
            })
            ok(f'Create fixture in universe {u}', r.status_code == 200)
            uni_ids.append(r.get_json().get('id'))

        r = c.get('/api/fixtures')
        unis = {f.get('dmxUniverse') for f in r.get_json() if f.get('fixtureType') == 'dmx'}
        ok('Multiple universes present', len(unis) >= 4, f'universes={unis}')

        for fid in uni_ids:
            c.delete(f'/api/fixtures/{fid}')

        # ================================================================
        # 18. DMX FIXTURE CHANNEL QUERIES
        # ================================================================
        print('── 18. DMX channel queries ──')

        # Create fixture with known profile
        r = c.post('/api/fixtures', json={
            'name': 'Channel Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 10, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb'
        })
        ok('Create fixture for channel test', r.status_code == 200)
        ch_fid = r.get_json().get('id')

        r = c.get(f'/api/dmx/fixture/{ch_fid}/channels')
        if r.status_code == 200:
            d = r.get_json()
            ok('Channel query returns universe', d.get('universe') == 1)
            ok('Channel query returns startAddr', d.get('startAddr') == 10)
            ok('Channel query returns channels', isinstance(d.get('channels'), list))
            ok('Channel count matches', len(d.get('channels', [])) >= 3)
        else:
            ok('Channel query (may need DMX engine)', False, f'status={r.status_code}')
            ok('skip', True)
            ok('skip', True)
            ok('skip', True)

        # Channel query for nonexistent fixture
        r = c.get('/api/dmx/fixture/99999/channels')
        ok('Channel query nonexistent → 404', r.status_code == 404)

        c.delete(f'/api/fixtures/{ch_fid}')

        # ================================================================
        # 19. DMX PROFILES LIST
        # ================================================================
        print('── 19. DMX profiles ──')

        r = c.get('/api/dmx-profiles')
        if r.status_code == 200:
            profiles = r.get_json()
            ok('GET profiles list', isinstance(profiles, list))
            ok('Has built-in profiles', len(profiles) >= 5, f'count={len(profiles)}')
            # Check generic-rgb exists
            rgb = next((p for p in profiles if p.get('id') == 'generic-rgb'), None)
            ok('generic-rgb profile exists', rgb is not None)
            if rgb:
                ok('generic-rgb has 3 channels', rgb.get('channelCount') == 3)
        else:
            ok('Profiles endpoint exists', False, f'status={r.status_code}')
            ok('skip', True)
            ok('skip', True)
            ok('skip', True)

        # GET single profile
        r = c.get('/api/dmx-profiles/generic-rgb')
        if r.status_code == 200:
            d = r.get_json()
            ok('GET single profile', d.get('id') == 'generic-rgb')
            ok('Profile has channels', isinstance(d.get('channels'), list))
        else:
            ok('Single profile endpoint', False, f'status={r.status_code}')
            ok('skip', True)

        # Nonexistent profile
        r = c.get('/api/dmx-profiles/nonexistent-xyz')
        ok('Nonexistent profile → 404', r.status_code == 404)

        # ================================================================
        # 20. BACKWARDS COMPATIBILITY — no fixtureType in JSON
        # ================================================================
        print('── 20. Backwards compatibility ──')

        # Simulate old-style fixture creation (no fixtureType field)
        r = c.post('/api/fixtures', json={'name': 'Legacy Fix', 'type': 'linear'})
        ok('Create without fixtureType', r.status_code == 200)
        legacy_id = r.get_json().get('id')

        r = c.get(f'/api/fixtures/{legacy_id}')
        d = r.get_json()
        ok('Legacy defaults to fixtureType=led', d.get('fixtureType') == 'led')
        ok('Legacy has no DMX fields', 'dmxUniverse' not in d)

        c.delete(f'/api/fixtures/{legacy_id}')

        # ================================================================
        # 21. VALID PUT ON LED FIXTURE — DMX fields should be silently accepted
        # ================================================================
        print('── 21. PUT LED fixture with various fields ──')

        r = c.put(f'/api/fixtures/{led1}', json={
            'name': 'Final LED Name', 'type': 'point', 'aoeRadius': 2000,
            'meshFile': 'strip.glb'
        })
        ok('PUT LED fixture with all fields', r.status_code == 200)
        r = c.get(f'/api/fixtures/{led1}')
        d = r.get_json()
        ok('LED name final', d.get('name') == 'Final LED Name')
        ok('LED type changed to point', d.get('type') == 'point')
        ok('LED aoeRadius=2000', d.get('aoeRadius') == 2000)
        ok('LED meshFile set', d.get('meshFile') == 'strip.glb')

        # ================================================================
        # 22. BULK OPERATIONS
        # ================================================================
        print('── 22. Bulk operations ──')

        bulk_ids = []
        for i in range(10):
            ft = 'dmx' if i % 2 == 0 else 'led'
            body = {'name': f'Bulk {i}', 'type': 'point', 'fixtureType': ft}
            if ft == 'dmx':
                body.update({'dmxUniverse': 1, 'dmxStartAddr': (i * 10) + 1, 'dmxChannelCount': 5})
            r = c.post('/api/fixtures', json=body)
            ok(f'Bulk create fixture {i}', r.status_code == 200)
            bulk_ids.append(r.get_json().get('id'))

        r = c.get('/api/fixtures')
        ok('Bulk fixtures in list', len(r.get_json()) >= 10)

        # Delete all bulk fixtures
        for fid in bulk_ids:
            c.delete(f'/api/fixtures/{fid}')

        r = c.get('/api/fixtures')
        remaining_bulk = [f for f in r.get_json() if f.get('name', '').startswith('Bulk ')]
        ok('Bulk fixtures deleted', len(remaining_bulk) == 0)

        # ================================================================
        # 23. FACTORY RESET CLEARS FIXTURES
        # ================================================================
        print('── 23. Factory reset ──')

        # Create some fixtures first
        c.post('/api/fixtures', json={'name': 'Pre-Reset', 'type': 'linear'})
        c.post('/api/fixtures', json={
            'name': 'Pre-Reset DMX', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 3
        })

        r = c.get('/api/fixtures')
        ok('Fixtures exist before reset', len(r.get_json()) >= 2)

        r = c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        ok('Factory reset', r.status_code == 200 and r.get_json().get('ok'))

        r = c.get('/api/fixtures')
        ok('Reset cleared all fixtures', len(r.get_json()) == 0)

        r = c.get('/api/children')
        ok('Reset cleared all children', len(r.get_json()) == 0)

    # ── Print results ───────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)

    print(f'\n{"=" * 60}')
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
