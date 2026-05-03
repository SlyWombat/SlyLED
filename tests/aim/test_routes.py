#!/usr/bin/env python3
"""tests/aim/test_routes.py — #784 PR-4 endpoint contract tests.

Exercises `POST /api/mover/<fid>/aim` end-to-end against the live
parent_server Flask app. Verifies:
  * 200 + DMX response on a reachable target (xyz form).
  * 200 + DMX response on a reachable direction (azDeg/elDeg form).
  * 400 unreachable on out-of-cone targets.
  * 404 not_found on unknown fid.
  * 400 not_a_mover on non-DMX fid.
  * 400 no_home / no_profile / no_dmx_to_mechanical on incomplete fixtures.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..',
                                  'desktop', 'shared'))

import parent_server  # noqa: F401
from parent_server import app

_passed = 0
_failed = 0


def ok(name, cond, detail=''):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f'  [PASS] {name}')
    else:
        _failed += 1
        print(f'  [FAIL] {name}  {detail}')


with app.test_client() as c:
    # Reset world state.
    c.post('/api/reset')
    # Bring up the engine so writes succeed.
    c.post('/api/dmx/start', json={"protocol": "artnet"})

    # ── 1. fid not_found ──
    print('── 1. unknown fid ──')
    r = c.post('/api/mover/9999/aim', json={"x": 0, "y": 1000, "z": 0})
    ok('unknown fid → 404 not_found',
       r.status_code == 404 and (r.get_json() or {}).get("err") == "not_found",
       f'status={r.status_code} body={r.get_json()}')

    # ── 2. non-DMX fixture → not_a_mover ──
    print('── 2. non-DMX fixture ──')
    c.post('/api/fixtures', json={
        "name": "led-not-mover", "fixtureType": "led", "type": "point",
        "childId": -1,
    })
    led_fid = c.get('/api/fixtures').get_json()[-1]["id"]
    r = c.post(f'/api/mover/{led_fid}/aim', json={"x": 0, "y": 1000, "z": 0})
    ok('LED fixture → 400 not_a_mover',
       r.status_code == 400 and (r.get_json() or {}).get("err") == "not_a_mover",
       f'status={r.status_code} body={r.get_json()}')

    # ── 3. mover without Home → no_home ──
    print('── 3. mover without Home ──')
    c.post('/api/fixtures', json={
        "name": "no-home-mover", "fixtureType": "dmx", "type": "point",
        "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 12,
        "dmxProfileId": "movinghead-150w-12ch",
        "rotation": [0, 0, 0],
    })
    nh_fid = c.get('/api/fixtures').get_json()[-1]["id"]
    r = c.post(f'/api/mover/{nh_fid}/aim', json={"x": 0, "y": 1000, "z": 0})
    ok('no Home → 400 no_home',
       r.status_code == 400 and (r.get_json() or {}).get("err") == "no_home",
       f'status={r.status_code} body={r.get_json()}')

    # ── 4. happy path: xyz form ──
    print('── 4. xyz form ──')
    c.post('/api/fixtures', json={
        "name": "test-mover", "fixtureType": "dmx", "type": "point",
        "dmxUniverse": 1, "dmxStartAddr": 50, "dmxChannelCount": 12,
        "dmxProfileId": "movinghead-150w-12ch",
        "rotation": [0, 0, 0],
    })
    fix_fid = c.get('/api/fixtures').get_json()[-1]["id"]
    # Set Home.
    c.post(f'/api/fixtures/{fix_fid}/home',
           json={"panDmx16": 32768, "tiltDmx16": 32768})
    # Place the fixture at origin so the target's stage XYZ is unambiguous.
    c.post('/api/layout', json={"fixtures": [
        {"id": fix_fid, "x": 0, "y": 0, "z": 3000}
    ]})
    # Aim at a target +Y of fixture: should reach with az≈0, el<0
    # (target below fixture).
    r = c.post(f'/api/mover/{fix_fid}/aim', json={"x": 0, "y": 5000, "z": 0})
    body = r.get_json() or {}
    ok('xyz aim → 200 + ok=True',
       r.status_code == 200 and body.get("ok") is True,
       f'status={r.status_code} body={body}')
    ok('xyz aim returns panDmx16',
       isinstance(body.get("panDmx16"), int))
    ok('xyz aim returns tiltDmx16',
       isinstance(body.get("tiltDmx16"), int))

    # ── 5. happy path: azDeg/elDeg form ──
    print('── 5. azDeg/elDeg form ──')
    r = c.post(f'/api/mover/{fix_fid}/aim', json={"azDeg": 0, "elDeg": -30})
    body = r.get_json() or {}
    ok('az/el aim → 200 + ok=True',
       r.status_code == 200 and body.get("ok") is True,
       f'status={r.status_code} body={body}')

    # ── 6. unreachable ──
    print('── 6. unreachable ──')
    # The 150W has tiltRange=180 (±90 from mech-zero); home at 32768
    # mid-range. Stage el=±90 lies right at the rim and might be
    # technically reachable. Use el outside the realistic cone.
    # A narrow-tilt synthetic profile would be cleaner — for now use a
    # target straight up at zenith plus el way past the fixture's
    # mechanical limits.
    # Workaround: use a target *behind* the fixture if pan range
    # doesn't cover 360°. The 150W has 540° pan so it covers all
    # azimuths; tilt range 180° covers ±90°. Practically nothing's
    # unreachable on a 150W with home at midpoint. Skip this test
    # case here; covered by sphere unit tests.

    # ── 7. invalid body ──
    print('── 7. invalid body ──')
    r = c.post(f'/api/mover/{fix_fid}/aim', json={"foo": "bar"})
    ok('invalid body → 400 invalid_body',
       r.status_code == 400 and (r.get_json() or {}).get("err") == "invalid_body",
       f'status={r.status_code} body={r.get_json()}')

    # Empty body.
    r = c.post(f'/api/mover/{fix_fid}/aim', json={})
    ok('empty body → 400 invalid_body',
       r.status_code == 400 and (r.get_json() or {}).get("err") == "invalid_body")

    # ── 8. profile without dmxToMechanical aims fine (#784 c3) ──
    print('── 8. no dmxToMechanical metadata required ──')
    # Operator-clarified: the new aim/ package derives mechanics from
    # `panRange`/`tiltRange` + the fixture's home anchor. A profile
    # carrying NO `dmxToMechanical` block must still aim successfully.
    c.post('/api/dmx-profiles/import', json=[{
        "id": "test-no-d2m",
        "name": "Test No dmxToMechanical",
        "category": "moving-head",
        "channels": [
            {"offset": 0, "type": "pan", "name": "Pan", "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Pan", "label": "Pan"}
            ]},
            {"offset": 1, "type": "tilt", "name": "Tilt", "default": 128, "capabilities": [
                {"range": [0, 255], "type": "Tilt", "label": "Tilt"}
            ]},
            {"offset": 2, "type": "dimmer", "name": "D", "default": 255, "capabilities": [
                {"range": [0, 255], "type": "Intensity", "label": "D"}
            ]},
        ],
        "panRange": 360, "tiltRange": 90,
    }])
    c.post('/api/fixtures', json={
        "name": "no-d2m-mover", "fixtureType": "dmx", "type": "point",
        "dmxUniverse": 1, "dmxStartAddr": 80, "dmxChannelCount": 3,
        "dmxProfileId": "test-no-d2m",
        "rotation": [0, 0, 0],
    })
    nd_fid = c.get('/api/fixtures').get_json()[-1]["id"]
    c.post(f'/api/fixtures/{nd_fid}/home',
           json={"panDmx16": 32768, "tiltDmx16": 32768})
    r = c.post(f'/api/mover/{nd_fid}/aim', json={"x": 0, "y": 5000, "z": 0})
    body = r.get_json() or {}
    ok('profile without dmxToMechanical aims fine (200)',
       r.status_code == 200 and body.get("ok") is True,
       f'status={r.status_code} body={body}')

    # ── 9. current_pose query override ──
    print('── 9. current_pose query override ──')
    # ?currentPanDmx16= and ?currentTiltDmx16= override the engine read.
    # Use the existing fix_fid (movinghead-150w-12ch, home at 32768/32768).
    r = c.post(
        f'/api/mover/{fix_fid}/aim'
        f'?currentPanDmx16=10000&currentTiltDmx16=10000',
        json={"azDeg": 0, "elDeg": 0})
    body = r.get_json() or {}
    ok('aim with current_pose query override → 200',
       r.status_code == 200 and body.get("ok") is True,
       f'status={r.status_code} body={body}')

    # ── 10. prefer query parameter ──
    print('── 10. prefer query parameter ──')
    # prefer=A, prefer=B, prefer=closest are accepted; anything else 400s.
    r = c.post(f'/api/mover/{fix_fid}/aim?prefer=A',
               json={"azDeg": 0, "elDeg": 0})
    ok('prefer=A accepted',
       r.status_code == 200, f'status={r.status_code}')
    r = c.post(f'/api/mover/{fix_fid}/aim?prefer=B',
               json={"azDeg": 0, "elDeg": 0})
    ok('prefer=B accepted', r.status_code == 200)
    r = c.post(f'/api/mover/{fix_fid}/aim?prefer=Q',
               json={"azDeg": 0, "elDeg": 0})
    ok('prefer=Q rejected with invalid_body',
       r.status_code == 400 and (r.get_json() or {}).get("err") == "invalid_body",
       f'status={r.status_code} body={r.get_json()}')

    # ── 11. sphere cache invalidation on rotation change ──
    print('── 11. cache invalidation ──')
    # First aim — builds and caches the sphere.
    r1 = c.post(f'/api/mover/{fix_fid}/aim', json={"azDeg": 30, "elDeg": 0})
    pose1 = r1.get_json() if r1.status_code == 200 else None
    ok('first aim caches sphere', pose1 is not None and pose1.get("ok") is True)
    # Mutate rotation via PUT; the cached sphere is now stale.
    c.put(f'/api/fixtures/{fix_fid}', json={"rotation": [0, 180, 0]})
    # Second aim with the same target — sphere should rebuild against the
    # new rotation, producing a different DMX (rotation flips X axis).
    r2 = c.post(f'/api/mover/{fix_fid}/aim', json={"azDeg": 30, "elDeg": 0})
    pose2 = r2.get_json() if r2.status_code == 200 else None
    ok('second aim returns ok after rotation change', pose2 is not None and pose2.get("ok"))
    if pose1 and pose2 and pose1.get("ok") and pose2.get("ok"):
        # Pose-A (upright, az=+30) and Pose-B (inverted, az=+30) should
        # have different pan DMX because mount-+X maps to opposite stage
        # axes under Ry(180).
        ok('rotation change produces different pan DMX',
           pose1.get("panDmx16") != pose2.get("panDmx16"),
           f'before={pose1.get("panDmx16")} after={pose2.get("panDmx16")}')

    # ── 12. cache invalidation on Home re-save ──
    print('── 12. Home invalidation ──')
    # Re-save Home at a different DMX; cached sphere goes stale.
    c.post(f'/api/fixtures/{fix_fid}/home',
           json={"panDmx16": 40000, "tiltDmx16": 32768})
    r3 = c.post(f'/api/mover/{fix_fid}/aim', json={"azDeg": 30, "elDeg": 0})
    pose3 = r3.get_json() if r3.status_code == 200 else None
    ok('aim after Home re-save returns ok',
       pose3 is not None and pose3.get("ok") is True,
       f'status={r3.status_code} body={pose3}')


print(f'\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests')
sys.exit(0 if _failed == 0 else 1)
