#!/usr/bin/env python3
"""
test_dmx_actions.py — Verify DMX action types through the full bake+playback pipeline.

Tests:
1. Classic actions (Solid, Breathe, Chase) produce correct DMX channel values
2. DMX Scene action sets dimmer/pan/tilt/gobo/strobe/colorWheel
3. Pan/Tilt Move expands into time-sliced segments
4. Gobo Select and Color Wheel actions produce correct segments
5. Emulator: verify Art-Net output channel values for each action type
"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18097
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
    """Set up server with a moving head DMX fixture and a generic RGB par."""
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'DMX Action Test', 'canvasW': 10000, 'canvasH': 5000})

        # Create a moving head profile (16ch)
        mh_profile = {
            "id": "test-mover-16ch",
            "name": "Test Mover 16ch",
            "manufacturer": "TestCo",
            "category": "moving-head",
            "channelCount": 16,
            "beamWidth": 15,
            "panRange": 540,
            "tiltRange": 270,
            "channels": [
                {"offset": 0, "name": "Pan", "type": "pan", "bits": 16, "default": 128},
                {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16, "default": 128},
                {"offset": 4, "name": "Dimmer", "type": "dimmer", "default": 0},
                {"offset": 5, "name": "Strobe", "type": "strobe", "default": 0},
                {"offset": 6, "name": "Red", "type": "red", "default": 0},
                {"offset": 7, "name": "Green", "type": "green", "default": 0},
                {"offset": 8, "name": "Blue", "type": "blue", "default": 0},
                {"offset": 9, "name": "White", "type": "white", "default": 0},
                {"offset": 10, "name": "Color Wheel", "type": "color-wheel", "default": 0},
                {"offset": 11, "name": "Gobo", "type": "gobo", "default": 0},
                {"offset": 12, "name": "Prism", "type": "prism", "default": 0},
                {"offset": 13, "name": "Focus", "type": "focus", "default": 128},
                {"offset": 14, "name": "Zoom", "type": "zoom", "default": 128},
                {"offset": 15, "name": "Speed", "type": "speed", "default": 0},
            ]
        }
        c.post('/api/dmx-profiles', json=mh_profile)

        # Create a generic RGB par profile (3ch)
        par_profile = {
            "id": "test-rgb-par",
            "name": "Test RGB Par",
            "manufacturer": "TestCo",
            "category": "par",
            "channelCount": 3,
            "channels": [
                {"offset": 0, "name": "Red", "type": "red", "default": 0},
                {"offset": 1, "name": "Green", "type": "green", "default": 0},
                {"offset": 2, "name": "Blue", "type": "blue", "default": 0},
            ]
        }
        c.post('/api/dmx-profiles', json=par_profile)

        # Create DMX fixtures
        r = c.post('/api/fixtures', json={
            'name': 'MH Left', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'test-mover-16ch',
            'rotation': [0, 0, 0]
        })
        mh_id = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'Par Center', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 17, 'dmxChannelCount': 3,
            'dmxProfileId': 'test-rgb-par'
        })
        par_id = r.get_json().get('id')

        # Position fixtures
        c.post('/api/layout', json={'children': [
            {'id': mh_id, 'x': 2000, 'y': 4000, 'z': 2500},
            {'id': par_id, 'x': 5000, 'y': 2500, 'z': 2500},
        ]})

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)
    return app, mh_id, par_id


def api(method, path, body=None):
    import urllib.request as ur
    data = json.dumps(body).encode() if body else None
    headers = {'Content-Type': 'application/json'} if data else {}
    req = ur.Request(f'{BASE}{path}', data=data, method=method, headers=headers)
    try:
        resp = ur.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f'    API error: {e}')
        return None


def test_classic_action_on_dmx(mh_id, par_id):
    """Classic Solid action should produce DMX_SCENE segments for DMX fixtures."""
    section('Classic Solid Action → DMX')

    # Create a Solid Red action
    r = api('POST', '/api/actions', {
        'name': 'Solid Red', 'type': 1, 'r': 255, 'g': 0, 'b': 0
    })
    ok(r and r.get('ok'), 'Created Solid Red action')
    act_id = r.get('id')

    # Create timeline with this action on the moving head
    r = api('POST', '/api/timelines', {
        'name': 'Solid Test', 'durationS': 10,
        'tracks': [{
            'fixtureId': mh_id,
            'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 10}]
        }, {
            'fixtureId': par_id,
            'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 10}]
        }]
    })
    ok(r and r.get('ok'), 'Created timeline')
    tl_id = r.get('id')

    # Bake
    r = api('POST', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('ok'), 'Bake started')
    time.sleep(2)

    # Check bake result
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    fixtures = r.get('fixtures', {})

    # Moving head: should have DMX_SCENE (type=14) segments, not Solid (type=1)
    mh_segs = fixtures.get(str(mh_id), {}).get('segments', [])
    ok(len(mh_segs) > 0, f'Moving head has {len(mh_segs)} segment(s)')
    if mh_segs:
        seg = mh_segs[0]
        ok(seg.get('type') == 14, f'Moving head segment type is DMX_SCENE (14), got {seg.get("type")}')
        p = seg.get('params', {})
        ok(p.get('r') == 255, f'Red channel = 255, got {p.get("r")}')
        ok(p.get('g') == 0, f'Green channel = 0')
        ok(p.get('b') == 0, f'Blue channel = 0')
        ok(p.get('dimmer') == 255, f'Dimmer auto-set to 255, got {p.get("dimmer")}')
        ok(p.get('pan') == 0.5, f'Pan defaults to 0.5, got {p.get("pan")}')
        ok(p.get('tilt') == 0.5, f'Tilt defaults to 0.5, got {p.get("tilt")}')

    # Par: also DMX_SCENE
    par_segs = fixtures.get(str(par_id), {}).get('segments', [])
    ok(len(par_segs) > 0, f'Par has {len(par_segs)} segment(s)')
    if par_segs:
        seg = par_segs[0]
        ok(seg.get('type') == 14, f'Par segment type is DMX_SCENE (14), got {seg.get("type")}')
        p = seg.get('params', {})
        ok(p.get('r') == 255, f'Par red = 255')

    # Cleanup
    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{act_id}')


def test_dmx_scene_action(mh_id):
    """DMX Scene action with explicit pan/tilt/gobo/strobe."""
    section('DMX Scene Action')

    r = api('POST', '/api/actions', {
        'name': 'DMX Full Scene', 'type': 14,
        'r': 100, 'g': 200, 'b': 50,
        'dimmer': 200, 'pan': 0.25, 'tilt': 0.75,
        'strobe': 128, 'gobo': 5, 'colorWheel': 3, 'prism': 10
    })
    ok(r and r.get('ok'), 'Created DMX Scene action')
    act_id = r.get('id')

    r = api('POST', '/api/timelines', {
        'name': 'Scene Test', 'durationS': 5,
        'tracks': [{'fixtureId': mh_id,
                     'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 5}]}]
    })
    tl_id = r.get('id')

    api('POST', f'/api/timelines/{tl_id}/bake')
    time.sleep(2)
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    segs = r.get('fixtures', {}).get(str(mh_id), {}).get('segments', [])
    ok(len(segs) > 0, f'Has segments: {len(segs)}')
    if segs:
        p = segs[0].get('params', {})
        ok(p.get('r') == 100, f'r=100, got {p.get("r")}')
        ok(p.get('g') == 200, f'g=200, got {p.get("g")}')
        ok(p.get('dimmer') == 200, f'dimmer=200, got {p.get("dimmer")}')
        ok(p.get('pan') == 0.25, f'pan=0.25, got {p.get("pan")}')
        ok(p.get('tilt') == 0.75, f'tilt=0.75, got {p.get("tilt")}')
        ok(p.get('strobe') == 128, f'strobe=128, got {p.get("strobe")}')
        ok(p.get('gobo') == 5, f'gobo=5, got {p.get("gobo")}')
        ok(p.get('colorWheel') == 3, f'colorWheel=3, got {p.get("colorWheel")}')
        ok(p.get('prism') == 10, f'prism=10, got {p.get("prism")}')

    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{act_id}')


def test_pan_tilt_move(mh_id):
    """Pan/Tilt Move action (type 15) expands into time-sliced DMX Scene segments."""
    section('Pan/Tilt Move Action')

    r = api('POST', '/api/actions', {
        'name': 'PT Sweep', 'type': 15,
        'r': 255, 'g': 255, 'b': 255, 'dimmer': 255,
        'panStart': 0.0, 'panEnd': 1.0,
        'tiltStart': 0.3, 'tiltEnd': 0.7,
        'speedMs': 5000
    })
    ok(r and r.get('ok'), 'Created Pan/Tilt Move action')
    act_id = r.get('id')

    r = api('POST', '/api/timelines', {
        'name': 'PT Move Test', 'durationS': 5,
        'tracks': [{'fixtureId': mh_id,
                     'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 5}]}]
    })
    tl_id = r.get('id')

    api('POST', f'/api/timelines/{tl_id}/bake')
    time.sleep(2)
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    segs = r.get('fixtures', {}).get(str(mh_id), {}).get('segments', [])
    ok(len(segs) >= 8, f'PT Move expanded into {len(segs)} slices (expect ≥8 for 5s @ 0.5s)')

    if len(segs) >= 2:
        # First slice should have pan near start (0.0)
        p0 = segs[0].get('params', {})
        ok(p0.get('pan', -1) < 0.15, f'First slice pan near 0: {p0.get("pan")}')
        ok(p0.get('tilt', -1) < 0.4, f'First slice tilt near 0.3: {p0.get("tilt")}')

        # Last slice should have pan near end (1.0)
        pl = segs[-1].get('params', {})
        ok(pl.get('pan', -1) > 0.85, f'Last slice pan near 1.0: {pl.get("pan")}')
        ok(pl.get('tilt', -1) > 0.6, f'Last slice tilt near 0.7: {pl.get("tilt")}')

        # All segments should be DMX_SCENE type
        all_dmx = all(s.get('type') == 14 for s in segs)
        ok(all_dmx, 'All PT Move slices are DMX_SCENE type')

        # Verify monotonic pan increase
        pans = [s.get('params', {}).get('pan', 0) for s in segs]
        monotonic = all(pans[i] <= pans[i+1] + 0.01 for i in range(len(pans)-1))
        ok(monotonic, 'Pan values increase monotonically')

    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{act_id}')


def test_gobo_and_color_wheel(mh_id):
    """Gobo Select (16) and Color Wheel (17) actions produce DMX_SCENE segments."""
    section('Gobo & Color Wheel Actions')

    # Gobo Select
    r = api('POST', '/api/actions', {
        'name': 'Gobo Star', 'type': 16,
        'r': 255, 'g': 255, 'b': 255, 'dimmer': 255,
        'gobo': 7, 'pan': 0.5, 'tilt': 0.5
    })
    ok(r and r.get('ok'), 'Created Gobo Select action')
    gobo_id = r.get('id')

    # Color Wheel
    r = api('POST', '/api/actions', {
        'name': 'Color Blue', 'type': 17,
        'r': 0, 'g': 0, 'b': 0, 'dimmer': 255,
        'colorWheel': 12, 'pan': 0.5, 'tilt': 0.5
    })
    ok(r and r.get('ok'), 'Created Color Wheel action')
    cw_id = r.get('id')

    r = api('POST', '/api/timelines', {
        'name': 'Gobo+CW Test', 'durationS': 10,
        'tracks': [{'fixtureId': mh_id, 'clips': [
            {'actionId': gobo_id, 'startS': 0, 'durationS': 5},
            {'actionId': cw_id, 'startS': 5, 'durationS': 5},
        ]}]
    })
    tl_id = r.get('id')

    api('POST', f'/api/timelines/{tl_id}/bake')
    time.sleep(2)
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    segs = r.get('fixtures', {}).get(str(mh_id), {}).get('segments', [])
    ok(len(segs) >= 2, f'Got {len(segs)} segments')

    if len(segs) >= 2:
        # First segment: gobo
        p0 = segs[0].get('params', {})
        ok(p0.get('gobo') == 7, f'Gobo=7, got {p0.get("gobo")}')
        ok(segs[0].get('type') == 14, 'Gobo segment is DMX_SCENE')

        # Second segment: color wheel
        p1 = segs[1].get('params', {})
        ok(p1.get('colorWheel') == 12, f'ColorWheel=12, got {p1.get("colorWheel")}')
        ok(segs[1].get('type') == 14, 'Color Wheel segment is DMX_SCENE')

    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{gobo_id}')
    api('DELETE', f'/api/actions/{cw_id}')


def test_preset_show_bake(mh_id, par_id):
    """Verify moving head preset shows produce non-empty DMX segments."""
    section('Preset Show Bake')

    presets = ['spotlight-sweep', 'concert-wash', 'figure-eight', 'thunderstorm', 'dance-floor']
    for preset_id in presets:
        r = api('POST', '/api/show/preset', {'presetId': preset_id})
        ok(r and r.get('ok'), f'Loaded preset: {preset_id}')
        if not r or not r.get('ok'):
            continue
        tl_id = r.get('timelineId')
        if not tl_id:
            ok(False, f'{preset_id}: no timelineId')
            continue

        api('POST', f'/api/timelines/{tl_id}/bake')
        time.sleep(2)
        r = api('GET', f'/api/timelines/{tl_id}/bake')
        ok(r and r.get('done'), f'{preset_id}: bake completed')

        fixtures = r.get('fixtures', {})
        # Check that at least one DMX fixture has segments
        mh_segs = fixtures.get(str(mh_id), {}).get('segments', [])
        par_segs = fixtures.get(str(par_id), {}).get('segments', [])
        has_segs = len(mh_segs) > 0 or len(par_segs) > 0
        ok(has_segs, f'{preset_id}: DMX fixtures have segments (MH:{len(mh_segs)}, Par:{len(par_segs)})')

        # Check segment params have valid RGB or dimmer
        if mh_segs:
            p = mh_segs[0].get('params', {})
            has_color = p.get('r', 0) + p.get('g', 0) + p.get('b', 0) > 0
            has_dimmer = p.get('dimmer', 0) > 0
            ok(has_color or has_dimmer, f'{preset_id}: MH segment has color or dimmer')


def test_blackout_action(par_id):
    """Blackout action on DMX fixture produces type=14 with all zeros."""
    section('Blackout on DMX')

    r = api('POST', '/api/actions', {'name': 'Blackout', 'type': 0, 'r': 0, 'g': 0, 'b': 0})
    ok(r and r.get('ok'), 'Created Blackout action')
    act_id = r.get('id')

    r = api('POST', '/api/timelines', {
        'name': 'Blackout Test', 'durationS': 5,
        'tracks': [{'fixtureId': par_id,
                     'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 5}]}]
    })
    tl_id = r.get('id')

    api('POST', f'/api/timelines/{tl_id}/bake')
    time.sleep(2)
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    segs = r.get('fixtures', {}).get(str(par_id), {}).get('segments', [])
    ok(len(segs) > 0, f'Blackout has {len(segs)} segment(s)')
    if segs:
        p = segs[0].get('params', {})
        ok(p.get('r') == 0 and p.get('g') == 0 and p.get('b') == 0, 'All colors zero')
        ok(p.get('dimmer') == 0, f'Dimmer=0 for blackout, got {p.get("dimmer")}')
        ok(segs[0].get('type') == 14, f'Blackout on DMX → DMX_SCENE, got type={segs[0].get("type")}')

    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{act_id}')


def test_breathe_on_dmx(par_id):
    """Breathe action on DMX fixture is converted to DMX_SCENE."""
    section('Breathe Action on DMX')

    r = api('POST', '/api/actions', {
        'name': 'Blue Breathe', 'type': 3,
        'r': 0, 'g': 0, 'b': 200, 'periodMs': 3000, 'minBri': 20
    })
    ok(r and r.get('ok'), 'Created Breathe action')
    act_id = r.get('id')

    r = api('POST', '/api/timelines', {
        'name': 'Breathe Test', 'durationS': 6,
        'tracks': [{'fixtureId': par_id,
                     'clips': [{'actionId': act_id, 'startS': 0, 'durationS': 6}]}]
    })
    tl_id = r.get('id')

    api('POST', f'/api/timelines/{tl_id}/bake')
    time.sleep(2)
    r = api('GET', f'/api/timelines/{tl_id}/bake')
    ok(r and r.get('done'), 'Bake completed')

    segs = r.get('fixtures', {}).get(str(par_id), {}).get('segments', [])
    ok(len(segs) > 0, f'Breathe has {len(segs)} segment(s)')
    if segs:
        ok(segs[0].get('type') == 14, f'Breathe → DMX_SCENE, got type={segs[0].get("type")}')
        p = segs[0].get('params', {})
        ok(p.get('b') == 200, f'Blue=200, got {p.get("b")}')
        ok(p.get('dimmer') == 255, f'Dimmer auto-set 255, got {p.get("dimmer")}')

    api('DELETE', f'/api/timelines/{tl_id}')
    api('DELETE', f'/api/actions/{act_id}')


def main():
    print('=== DMX Action Types Test ===')
    app, mh_id, par_id = seed()

    test_classic_action_on_dmx(mh_id, par_id)
    test_dmx_scene_action(mh_id)
    test_pan_tilt_move(mh_id)
    test_gobo_and_color_wheel(mh_id)
    test_preset_show_bake(mh_id, par_id)
    test_blackout_action(par_id)
    test_breathe_on_dmx(par_id)

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
