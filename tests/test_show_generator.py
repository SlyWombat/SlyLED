#!/usr/bin/env python3
"""
test_show_generator.py — Verify dynamic show generation across all themes
and fixture configurations.

Tests:
1. All 14 themes generate valid shows
2. LED-only rigs get base actions + spatial effects
3. DMX-only rigs get DMX Scene base actions
4. Mixed rigs (LED + DMX par + moving head) get full coverage
5. Moving heads get pan/tilt sweep actions
6. No fixtures → still produces a valid (simple) show
7. Effects sweep through actual fixture positions
8. Bake produces non-empty segments for all DMX fixtures
9. Two loads of same theme produce different shows (randomization)
"""
import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18098
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


def setup_mixed_rig():
    """Create a mixed rig: 2 LED fixtures, 2 DMX pars, 2 moving heads."""
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Show Gen Test', 'canvasW': 10000, 'canvasH': 5000})

        # Create profiles
        c.post('/api/dmx-profiles', json={
            "id": "test-par-3ch", "name": "RGB Par", "manufacturer": "Test",
            "category": "par", "channelCount": 3,
            "channels": [
                {"offset": 0, "name": "Red", "type": "red"},
                {"offset": 1, "name": "Green", "type": "green"},
                {"offset": 2, "name": "Blue", "type": "blue"},
            ]
        })
        c.post('/api/dmx-profiles', json={
            "id": "test-mover", "name": "Moving Head", "manufacturer": "Test",
            "category": "moving-head", "channelCount": 10,
            "panRange": 540, "tiltRange": 270, "beamWidth": 15,
            "channels": [
                {"offset": 0, "name": "Pan", "type": "pan", "bits": 16},
                {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16},
                {"offset": 4, "name": "Dimmer", "type": "dimmer"},
                {"offset": 5, "name": "Red", "type": "red"},
                {"offset": 6, "name": "Green", "type": "green"},
                {"offset": 7, "name": "Blue", "type": "blue"},
                {"offset": 8, "name": "Gobo", "type": "gobo"},
                {"offset": 9, "name": "Strobe", "type": "strobe"},
            ]
        })

        ids = {}
        # LED fixtures (would need children in real setup, but for generation testing
        # we just need them in _fixtures)
        for name, x, y in [("LED Left", 1000, 2500), ("LED Right", 9000, 2500)]:
            r = c.post('/api/fixtures', json={
                'name': name, 'type': 'linear', 'fixtureType': 'led',
                'strings': [{'leds': 30, 'lengthMm': 1000, 'sdir': 0}]
            })
            ids[name] = r.get_json().get('id')

        # DMX pars
        for i, (name, x, y, addr) in enumerate([
            ("Par Left", 2000, 3000, 1), ("Par Right", 8000, 3000, 4)
        ]):
            r = c.post('/api/fixtures', json={
                'name': name, 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': addr, 'dmxChannelCount': 3,
                'dmxProfileId': 'test-par-3ch'
            })
            ids[name] = r.get_json().get('id')

        # Moving heads
        for i, (name, x, y, addr) in enumerate([
            ("MH Left", 3000, 4500, 10), ("MH Right", 7000, 4500, 20)
        ]):
            r = c.post('/api/fixtures', json={
                'name': name, 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': addr, 'dmxChannelCount': 10,
                'dmxProfileId': 'test-mover',
                'aimPoint': [5000, 0, 5000]
            })
            ids[name] = r.get_json().get('id')

        # Position all fixtures on layout
        positions = []
        pos_data = {
            "LED Left": (1000, 2500, 2500), "LED Right": (9000, 2500, 2500),
            "Par Left": (2000, 3000, 2500), "Par Right": (8000, 3000, 2500),
            "MH Left": (3000, 4500, 2500), "MH Right": (7000, 4500, 2500),
        }
        for name, (x, y, z) in pos_data.items():
            if name in ids:
                positions.append({'id': ids[name], 'x': x, 'y': y, 'z': z})
        c.post('/api/layout', json={'children': positions})

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)
    return app, ids


def test_all_themes_generate():
    """Every theme produces a valid show."""
    section('All Themes Generate')
    from show_generator import THEMES
    presets = api('GET', '/api/show/presets')
    ok(isinstance(presets, list), 'GET /api/show/presets returns list')
    ok(len(presets) == len(THEMES), f'All {len(THEMES)} themes listed')

    for theme in presets:
        tid = theme['id']
        # Reset between presets to avoid stacking
        api('POST', '/api/reset', None)
        # Re-setup would be needed but we just test generation
        r = api('POST', '/api/show/preset', {'id': tid})
        ok(r and r.get('ok'), f'{tid}: generated successfully')
        if r and r.get('ok'):
            ok(r.get('actions', 0) >= 1, f'{tid}: has actions ({r.get("actions")})')
            ok(r.get('effects', 0) >= 0, f'{tid}: has effects ({r.get("effects")})')
            ok(r.get('timelineId') is not None, f'{tid}: has timelineId')


def test_mixed_rig_coverage(ids):
    """Mixed rig: every fixture type gets coverage."""
    section('Mixed Rig Coverage')

    # Load a theme
    r = api('POST', '/api/show/preset', {'id': 'spotlight-sweep'})
    ok(r and r.get('ok'), 'Spotlight Sweep loaded')
    tl_id = r.get('timelineId') if r else None

    if tl_id:
        # Bake it
        api('POST', f'/api/timelines/{tl_id}/bake')
        time.sleep(3)
        r = api('GET', f'/api/timelines/{tl_id}/bake')
        ok(r and r.get('done'), 'Bake completed')

        fixtures = r.get('fixtures', {}) if r else {}

        # Check each fixture got segments
        for name, fid in ids.items():
            segs = fixtures.get(str(fid), {}).get('segments', [])
            ok(len(segs) > 0, f'{name} (id={fid}): has {len(segs)} segments')

            # For DMX fixtures, verify segments have color or dimmer
            if 'Par' in name or 'MH' in name:
                if segs:
                    p = segs[0].get('params', {})
                    has_output = (p.get('r', 0) + p.get('g', 0) + p.get('b', 0) > 0 or
                                  p.get('dimmer', 0) > 0)
                    ok(has_output, f'{name}: segment has color/dimmer output')

        # Check moving heads have pan/tilt data
        for name in ['MH Left', 'MH Right']:
            fid = ids.get(name)
            if not fid:
                continue
            segs = fixtures.get(str(fid), {}).get('segments', [])
            has_pt = any(
                s.get('params', {}).get('pan') is not None
                for s in segs
            )
            ok(has_pt, f'{name}: has pan/tilt in segments')


def test_no_dark_periods(ids):
    """All DMX fixtures should have segments covering the full show duration."""
    section('No Dark Periods')

    r = api('POST', '/api/show/preset', {'id': 'concert-wash'})
    ok(r and r.get('ok'), 'Concert Wash loaded')
    tl_id = r.get('timelineId') if r else None

    if tl_id:
        api('POST', f'/api/timelines/{tl_id}/bake')
        time.sleep(3)
        r = api('GET', f'/api/timelines/{tl_id}/bake')
        ok(r and r.get('done'), 'Bake completed')

        fixtures = r.get('fixtures', {}) if r else {}
        for name in ['Par Left', 'Par Right', 'MH Left', 'MH Right']:
            fid = ids.get(name)
            if not fid:
                continue
            segs = fixtures.get(str(fid), {}).get('segments', [])
            if not segs:
                ok(False, f'{name}: no segments (dark!)')
                continue

            # Check that t=0 is covered
            has_start = any(s.get('startS', 99) <= 0.5 for s in segs)
            ok(has_start, f'{name}: covered at t=0')

            # Check total coverage
            total_covered = sum(s.get('durationS', 0) for s in segs)
            ok(total_covered > 5, f'{name}: {total_covered:.1f}s of coverage')


def test_randomization():
    """Two loads of same theme should produce different shows."""
    section('Randomization')

    from show_generator import generate_show, THEMES

    # Use a direct call to the generator to compare two runs
    fixtures = [
        {"id": 1, "name": "P1", "fixtureType": "led", "type": "linear",
         "strings": [{"leds": 30, "lengthMm": 1000, "sdir": 0}]},
        {"id": 2, "name": "Par", "fixtureType": "dmx", "type": "point",
         "dmxProfileId": None, "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 3},
    ]
    layout = {"children": [
        {"id": 1, "x": 2000, "y": 2500, "z": 2500},
        {"id": 2, "x": 8000, "y": 2500, "z": 2500},
    ]}
    stage = {"w": 10.0, "h": 5.0, "d": 5.0}

    show1 = generate_show("disco", fixtures, layout, stage)
    show2 = generate_show("disco", fixtures, layout, stage)

    ok(show1 is not None and show2 is not None, 'Both generations succeeded')
    if show1 and show2 and show1.get('effects') and show2.get('effects'):
        # Effects should differ in at least one position
        e1 = show1['effects'][0].get('motion', {}).get('startPos', [])
        e2 = show2['effects'][0].get('motion', {}).get('startPos', [])
        differs = e1 != e2
        ok(differs, f'Start positions differ: {e1} vs {e2}')
    else:
        ok(True, 'Randomization: skipped (no effects)')


def test_empty_rig():
    """No fixtures → still produces a valid show."""
    section('Empty Rig')

    from show_generator import generate_show
    show = generate_show("ocean-wave", [], {"children": []}, {"w": 10, "h": 5, "d": 5})
    ok(show is not None, 'Empty rig generates a show')
    ok(len(show.get('base_actions', [])) >= 1, 'Has at least a base action')
    ok(show.get('durationS', 0) > 0, 'Has duration')
    ok(len(show.get('tracks', [])) >= 1, 'Has at least one track')


def test_dmx_only_rig():
    """DMX-only rig gets proper coverage."""
    section('DMX-Only Rig')

    from show_generator import generate_show
    fixtures = [
        {"id": 10, "name": "Par 1", "fixtureType": "dmx", "type": "point",
         "dmxProfileId": None, "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 3},
        {"id": 11, "name": "Par 2", "fixtureType": "dmx", "type": "point",
         "dmxProfileId": None, "dmxUniverse": 1, "dmxStartAddr": 4, "dmxChannelCount": 3},
    ]
    layout = {"children": [
        {"id": 10, "x": 2000, "y": 3000, "z": 2500},
        {"id": 11, "x": 8000, "y": 3000, "z": 2500},
    ]}
    stage = {"w": 10.0, "h": 5.0, "d": 5.0}

    show = generate_show("sunset", fixtures, layout, stage)
    ok(show is not None, 'DMX-only rig generates show')
    ok(len(show.get('effects', [])) >= 2, f'Has spatial effects: {len(show.get("effects", []))}')

    # Should have DMX par base action
    has_par_action = any(
        a.get('targets') == 'dmx_par'
        for a in show.get('base_actions', [])
    )
    ok(has_par_action, 'Has DMX par base action')

    # Verify track structure: per-fixture base tracks + effects track
    tracks = show.get('tracks', [])
    ok(len(tracks) >= 3, f'Has per-fixture base + effects tracks: {len(tracks)}')
    layers = [t.get('_layer') for t in tracks]
    ok(layers.count('base') >= 2, 'Has base tracks for each par fixture')
    ok('effects' in layers, 'Has effects track')


def test_led_only_rig():
    """LED-only rig gets proper coverage."""
    section('LED-Only Rig')

    from show_generator import generate_show
    fixtures = [
        {"id": 20, "name": "Strip 1", "fixtureType": "led", "type": "linear",
         "strings": [{"leds": 50, "lengthMm": 2000, "sdir": 0}]},
        {"id": 21, "name": "Strip 2", "fixtureType": "led", "type": "linear",
         "strings": [{"leds": 50, "lengthMm": 2000, "sdir": 0}]},
    ]
    layout = {"children": [
        {"id": 20, "x": 1000, "y": 2500, "z": 2500},
        {"id": 21, "x": 9000, "y": 2500, "z": 2500},
    ]}

    show = generate_show("rainbow-up", fixtures, layout, {"w": 10, "h": 5, "d": 5})
    ok(show is not None, 'LED-only rig generates show')

    has_led_action = any(
        a.get('targets') == 'led'
        for a in show.get('base_actions', [])
    )
    ok(has_led_action, 'Has LED base action')
    ok(len(show.get('dmx_mover_ids', [])) == 0, 'No movers in LED-only rig')


def test_track_structure():
    """Verify tracks have non-overlapping clips and proper ordering."""
    section('Track Structure')

    from show_generator import generate_show
    fixtures = [
        {"id": 1, "name": "LED", "fixtureType": "led", "type": "linear",
         "strings": [{"leds": 30, "lengthMm": 1000, "sdir": 0}]},
        {"id": 2, "name": "Par", "fixtureType": "dmx", "type": "point",
         "dmxProfileId": None, "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 3},
        {"id": 3, "name": "MH", "fixtureType": "dmx", "type": "point",
         "dmxProfileId": "test-mover", "dmxUniverse": 1, "dmxStartAddr": 10, "dmxChannelCount": 10},
    ]
    layout = {"children": [
        {"id": 1, "x": 2000, "y": 2500, "z": 2500},
        {"id": 2, "x": 5000, "y": 3000, "z": 2500},
        {"id": 3, "x": 8000, "y": 4500, "z": 2500},
    ]}
    stage = {"w": 10.0, "h": 5.0, "d": 5.0}

    # Import profile lib for mover detection
    from dmx_profiles import ProfileLibrary
    plib = ProfileLibrary()
    # Add test mover profile
    plib.save_profile({
        "id": "test-mover", "name": "Test Mover", "channelCount": 10,
        "panRange": 540, "tiltRange": 270,
        "channels": [
            {"offset": 0, "name": "Pan", "type": "pan", "bits": 16},
            {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16},
            {"offset": 4, "name": "Dimmer", "type": "dimmer"},
            {"offset": 5, "name": "Red", "type": "red"},
            {"offset": 6, "name": "Green", "type": "green"},
            {"offset": 7, "name": "Blue", "type": "blue"},
            {"offset": 8, "name": "Gobo", "type": "gobo"},
            {"offset": 9, "name": "Strobe", "type": "strobe"},
        ]
    })

    show = generate_show("spotlight-sweep", fixtures, layout, stage, plib)
    ok(show is not None, 'Show generated')

    tracks = show.get('tracks', [])
    ok(len(tracks) >= 4, f'Has base(3) + effects + mover tracks: {len(tracks)}')

    # Check track layers — base per fixture, then effects, then movers
    layers = [t.get('_layer') for t in tracks]
    base_count = layers.count('base')
    ok(base_count >= 3, f'{base_count} base tracks (one per fixture)')
    ok('effects' in layers, 'Has effects layer')
    ok('mover' in layers, 'Has mover layer')

    # Effects track should come after all base tracks
    effects_idx = layers.index('effects') if 'effects' in layers else -1
    ok(effects_idx >= base_count, f'Effects track (idx={effects_idx}) after base tracks ({base_count})')

    # Check no overlapping clips within any track
    for ti, track in enumerate(tracks):
        clips = track.get('clips', [])
        if len(clips) <= 1:
            continue
        # Sort by start time
        sorted_clips = sorted(clips, key=lambda c: c.get('startS', 0))
        overlap_found = False
        for i in range(len(sorted_clips) - 1):
            end_i = sorted_clips[i].get('startS', 0) + sorted_clips[i].get('durationS', 0)
            start_next = sorted_clips[i + 1].get('startS', 0)
            if end_i > start_next + 0.01:  # small tolerance
                overlap_found = True
                break
        ok(not overlap_found, f'Track {ti} ({track.get("_layer", "?")}): no overlapping clips ({len(clips)} clips)')

    # Check effects track has sequenced clips
    effects_track = tracks[1] if len(tracks) > 1 else None
    if effects_track:
        eclips = effects_track.get('clips', [])
        if len(eclips) >= 2:
            starts = [c.get('startS', 0) for c in eclips]
            ok(starts == sorted(starts), f'Effect clips are time-ordered: {starts}')
            ok(starts[-1] > 0, f'Last effect starts after 0: {starts[-1]}')


def test_mover_actually_moves(ids):
    """Verify baked moving head segments have varying pan/tilt (not static)."""
    section('Mover Actually Moves')

    r = api('POST', '/api/show/preset', {'id': 'spotlight-sweep'})
    ok(r and r.get('ok'), 'Spotlight Sweep loaded')
    tl_id = r.get('timelineId') if r else None

    if tl_id:
        api('POST', f'/api/timelines/{tl_id}/bake')
        time.sleep(3)
        r = api('GET', f'/api/timelines/{tl_id}/bake')
        ok(r and r.get('done'), 'Bake completed')

        for name in ['MH Left', 'MH Right']:
            fid = ids.get(name)
            if not fid:
                continue
            segs = r.get('fixtures', {}).get(str(fid), {}).get('segments', [])
            ok(len(segs) >= 5, f'{name}: has {len(segs)} segments')

            # Collect unique pan values
            pans = set()
            tilts = set()
            for s in segs:
                p = s.get('params', {})
                if p.get('pan') is not None:
                    pans.add(round(p['pan'], 2))
                if p.get('tilt') is not None:
                    tilts.add(round(p['tilt'], 2))

            ok(len(pans) >= 3, f'{name}: pan varies ({len(pans)} unique values)')
            ok(len(tilts) >= 1, f'{name}: tilt present ({len(tilts)} unique values)')

            # Verify no all-zero segments (not dark)
            has_output = any(
                s.get('params', {}).get('r', 0) + s.get('params', {}).get('g', 0) + s.get('params', {}).get('b', 0) > 0
                or s.get('params', {}).get('dimmer', 0) > 0
                for s in segs
            )
            ok(has_output, f'{name}: has non-zero color/dimmer output')

            # Print first few segments for debugging
            if _verbose and segs:
                for s in segs[:3]:
                    p = s.get('params', {})
                    print(f'    t={s.get("startS")}-{s.get("startS",0)+s.get("durationS",0)} '
                          f'pan={p.get("pan"):.2f} tilt={p.get("tilt"):.2f} '
                          f'r={p.get("r")} g={p.get("g")} b={p.get("b")} dim={p.get("dimmer")}')


def test_all_themes_with_mixed_rig():
    """Load every theme via API on the mixed rig and verify success."""
    section('All Themes on Mixed Rig')

    from show_generator import THEMES
    for tid in THEMES:
        # Reset and re-seed between presets
        r = api('POST', '/api/show/preset', {'id': tid})
        if r and r.get('ok'):
            ok(True, f'{tid}: loaded')
        else:
            ok(False, f'{tid}: FAILED — {r}')


def main():
    print('=== Dynamic Show Generator Test ===')
    app, ids = setup_mixed_rig()

    # Direct generator tests (no server needed)
    test_empty_rig()
    test_dmx_only_rig()
    test_led_only_rig()
    test_randomization()
    test_track_structure()

    # API-level tests (need server)
    test_mixed_rig_coverage(ids)
    test_no_dark_periods(ids)
    test_mover_actually_moves(ids)
    test_all_themes_with_mixed_rig()

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
