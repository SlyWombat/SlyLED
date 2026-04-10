#!/usr/bin/env python3
"""
test_dmx_moving_heads.py — Tests for DMX moving head pipeline.

Pan/tilt computation, DMX channel helpers, bake engine DMX segments,
playback channel output, and aim point API.

Usage:
    python tests/test_dmx_moving_heads.py
"""

import sys, os, math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from spatial_engine import compute_pan_tilt, effect_aim_point
from dmx_universe import DMXUniverse
from bake_engine import _compile_dmx_fixture, ACT_DMX_SCENE
import parent_server
from parent_server import app

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def run():
    # ================================================================
    # 1. PAN/TILT COMPUTATION
    # ================================================================
    print('-- 1. Pan/tilt computation --')

    # Aim at +Z from origin -> centered (0.5, 0.5)
    pt = compute_pan_tilt([0, 0, 0], [0, 0, 5000], 540, 270)
    ok('Aim +Z: pan centered', pt is not None and abs(pt[0] - 0.5) < 0.02, f'pan={pt[0]:.3f}' if pt else 'None')
    ok('Aim +Z: tilt centered', abs(pt[1] - 0.5) < 0.02, f'tilt={pt[1]:.3f}' if pt else '')

    # Aim straight down -> tilt > 0.5
    pt = compute_pan_tilt([0, 5000, 0], [0, 0, 0], 540, 270)
    ok('Aim down: tilt > 0.5', pt is not None and pt[1] > 0.5, f'tilt={pt[1]:.3f}' if pt else 'None')

    # Aim to +X -> pan > 0.5
    pt = compute_pan_tilt([0, 0, 0], [5000, 0, 0], 540, 270)
    ok('Aim +X: pan > 0.5', pt is not None and pt[0] > 0.5, f'pan={pt[0]:.3f}' if pt else 'None')

    # Aim to -X -> pan < 0.5
    pt = compute_pan_tilt([0, 0, 0], [-5000, 0, 0], 540, 270)
    ok('Aim -X: pan < 0.5', pt is not None and pt[0] < 0.5, f'pan={pt[0]:.3f}' if pt else 'None')

    # Aim up -> tilt < 0.5
    pt = compute_pan_tilt([0, 0, 0], [0, 5000, 5000], 540, 270)
    ok('Aim up: tilt < 0.5', pt is not None and pt[1] < 0.5, f'tilt={pt[1]:.3f}' if pt else 'None')

    # Zero ranges -> None
    pt = compute_pan_tilt([0, 0, 0], [1000, 0, 0], 0, 0)
    ok('Zero range -> None', pt is None)

    # Normalized within 0.0-1.0
    pt = compute_pan_tilt([0, 0, 0], [10000, -10000, 0], 540, 270)
    ok('Clamped to 0-1', pt is not None and 0.0 <= pt[0] <= 1.0 and 0.0 <= pt[1] <= 1.0,
       f'pan={pt[0]:.3f} tilt={pt[1]:.3f}' if pt else 'None')

    # effect_aim_point interpolation
    effect = {"motion": {"startPos": [0, 0, 0], "endPos": [10000, 0, 0], "durationS": 10, "easing": "linear"}}
    p0 = effect_aim_point(effect, 0)
    ok('effect_aim t=0', abs(p0[0]) < 1, f'x={p0[0]}')
    p5 = effect_aim_point(effect, 5)
    ok('effect_aim t=5 (mid)', abs(p5[0] - 5000) < 100, f'x={p5[0]}')
    p10 = effect_aim_point(effect, 10)
    ok('effect_aim t=10 (end)', abs(p10[0] - 10000) < 100, f'x={p10[0]}')

    # ================================================================
    # 2. DMX UNIVERSE HELPERS
    # ================================================================
    print('-- 2. DMX universe helpers --')

    uni = DMXUniverse()
    profile_8bit = {
        "channel_map": {"pan": 0, "tilt": 1, "dimmer": 2, "red": 3, "green": 4, "blue": 5},
        "channels": [
            {"type": "pan", "offset": 0, "bits": 8},
            {"type": "tilt", "offset": 1, "bits": 8},
            {"type": "dimmer", "offset": 2},
            {"type": "red", "offset": 3},
            {"type": "green", "offset": 4},
            {"type": "blue", "offset": 5},
        ]
    }

    # 8-bit pan/tilt
    uni.set_fixture_pan_tilt(1, 0.5, 0.5, profile_8bit)
    ok('8bit pan 0.5 -> 127', uni.get_channel(1) == 127, f'got {uni.get_channel(1)}')
    ok('8bit tilt 0.5 -> 127', uni.get_channel(2) == 127, f'got {uni.get_channel(2)}')

    # 16-bit pan/tilt
    uni2 = DMXUniverse()
    profile_16bit = {
        "channel_map": {"pan": 0, "tilt": 2},
        "channels": [
            {"type": "pan", "offset": 0, "bits": 16},
            {"type": "tilt", "offset": 2, "bits": 16},
        ]
    }
    uni2.set_fixture_pan_tilt(1, 0.5, 0.5, profile_16bit)
    pan_msb = uni2.get_channel(1)
    pan_lsb = uni2.get_channel(2)
    val16 = (pan_msb << 8) | pan_lsb
    ok('16bit pan 0.5 -> ~32767', abs(val16 - 32767) <= 1, f'got {val16}')
    tilt_msb = uni2.get_channel(3)
    tilt_lsb = uni2.get_channel(4)
    tval16 = (tilt_msb << 8) | tilt_lsb
    ok('16bit tilt 0.5 -> ~32767', abs(tval16 - 32767) <= 1, f'got {tval16}')

    # set_fixture_channels generic
    uni3 = DMXUniverse()
    uni3.set_fixture_channels(10, {"red": 200, "green": 150, "blue": 100}, profile_8bit)
    ok('set_channels red', uni3.get_channel(13) == 200)
    ok('set_channels green', uni3.get_channel(14) == 150)
    ok('set_channels blue', uni3.get_channel(15) == 100)

    # Missing profile -> no crash
    uni4 = DMXUniverse()
    uni4.set_fixture_pan_tilt(1, 0.5, 0.5, None)
    ok('No profile no crash (pan_tilt)', True)
    uni4.set_fixture_channels(1, {"pan": 128}, None)
    ok('No profile no crash (channels)', True)

    # Boundary: 0.0 and 1.0
    uni5 = DMXUniverse()
    uni5.set_fixture_pan_tilt(1, 0.0, 1.0, profile_8bit)
    ok('Pan 0.0 -> 0', uni5.get_channel(1) == 0)
    ok('Tilt 1.0 -> 255', uni5.get_channel(2) == 255)

    # ================================================================
    # 3. BAKE ENGINE DMX
    # ================================================================
    print('-- 3. Bake engine DMX --')

    # Static spatial effect
    static_effect = {
        "shape": "sphere", "r": 255, "g": 100, "b": 0,
        "size": {"radius": 5000},
        "motion": {"startPos": [1000, 1000, 1000], "endPos": [1000, 1000, 1000],
                   "durationS": 10, "easing": "linear"},
    }
    profile_info = {"panRange": 540, "tiltRange": 270, "beamWidth": 15}
    clip = {"startS": 0, "durationS": 10}

    segs = _compile_dmx_fixture(clip, static_effect, [0, 5000, 0], [1000, 0, 1000], profile_info, 10)
    ok('Static effect -> 1 segment', len(segs) == 1)
    ok('Segment type ACT_DMX_SCENE', segs[0]["type"] == ACT_DMX_SCENE)
    ok('Segment has pan', "pan" in segs[0]["params"])
    ok('Segment has tilt', "tilt" in segs[0]["params"])
    ok('Segment has dimmer', "dimmer" in segs[0]["params"])

    # Moving spatial effect
    moving_effect = {
        "shape": "sphere", "r": 0, "g": 255, "b": 0,
        "size": {"radius": 2000},
        "motion": {"startPos": [-5000, 0, 0], "endPos": [5000, 0, 0],
                   "durationS": 5, "easing": "linear"},
    }
    clip2 = {"startS": 0, "durationS": 5}
    segs2 = _compile_dmx_fixture(clip2, moving_effect, [0, 3000, 0], [0, 0, 0], profile_info, 5)
    ok('Moving effect -> multiple segments', len(segs2) > 1, f'got {len(segs2)}')
    ok('Moving segments are time-sliced', segs2[0]["durationS"] <= 1.0)

    # Pan values change across segments for moving effect
    pans = [s["params"]["pan"] for s in segs2]
    ok('Pan changes across slices', len(set(round(p, 2) for p in pans)) > 1, f'pans={[round(p,2) for p in pans]}')

    # No profile info -> pan/tilt defaults to 0.5
    segs3 = _compile_dmx_fixture(clip, static_effect, [0, 5000, 0], [0, 0, 0], None, 10)
    ok('No profile -> pan 0.5', segs3[0]["params"]["pan"] == 0.5)

    # No effect -> empty
    segs4 = _compile_dmx_fixture(clip, None, [0, 0, 0], [0, 0, 0], profile_info, 10)
    ok('No effect -> empty', len(segs4) == 0)

    # ================================================================
    # 4. DEMO SHOW PRESETS
    # ================================================================
    print('-- 4. Demo show presets --')

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # List presets — should include new moving-head presets
        r = c.get('/api/show/presets')
        presets = r.get_json()
        preset_ids = [p['id'] for p in presets]
        ok('Presets list has 14+', len(presets) >= 14, f'count={len(presets)}')

        new_presets = ['spotlight-sweep', 'concert-wash', 'figure-eight', 'thunderstorm', 'dance-floor']
        for pid in new_presets:
            ok(f'Preset {pid} listed', pid in preset_ids)

        # Install each new preset — verify it creates timeline + effects
        for pid in new_presets:
            r = c.post('/api/show/preset', json={'id': pid})
            d = r.get_json()
            ok(f'Install {pid}', r.status_code == 200 and d.get('ok'), d.get('err', ''))
            ok(f'{pid} has timeline', d.get('timelineId') is not None)
            ok(f'{pid} has effects', d.get('effects', 0) >= 1, f'effects={d.get("effects")}')

        # Verify timelines were created
        r = c.get('/api/timelines')
        tls = r.get_json()
        ok('Timelines created', len(tls) >= 5, f'count={len(tls)}')

        # Verify spatial effects were created
        r = c.get('/api/spatial-effects')
        fxs = r.get_json()
        ok('Spatial effects created', len(fxs) >= 10, f'count={len(fxs)}')

        # Verify each timeline has allPerformers track with clips
        for tl in tls:
            tracks = tl.get('tracks', [])
            ok(f'TL {tl["name"]} has track', len(tracks) >= 1)
            if tracks:
                ok(f'TL {tl["name"]} allPerformers', tracks[0].get('allPerformers'))

    # ================================================================
    # 5. API — AIM POINT
    # ================================================================
    print('-- 5. API aim point --')

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Create DMX fixture with rotation
        r = c.post('/api/fixtures', json={
            'name': 'MH Test', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 8,
            'rotation': [15, 30, 0]
        })
        fid = r.get_json().get('id')
        ok('Create fixture with rotation', r.status_code == 200)

        # Verify rotation stored
        r = c.get(f'/api/fixtures/{fid}')
        ok('rotation persisted', r.get_json().get('rotation') == [15, 30, 0])

        # PUT /aim with rotation
        r = c.put(f'/api/fixtures/{fid}/aim', json={'rotation': [-10.0, 45.0, 0.0]})
        ok('PUT aim rotation 200', r.status_code == 200)
        r = c.get(f'/api/fixtures/{fid}')
        ok('rotation updated', r.get_json().get('rotation') == [-10.0, 45.0, 0.0])

        # PUT /aim bad data (legacy aimPoint path)
        r = c.put(f'/api/fixtures/{fid}/aim', json={'aimPoint': 'bad'})
        ok('PUT aim bad data -> 400', r.status_code == 400)

        r = c.put(f'/api/fixtures/{fid}/aim', json={'aimPoint': [1, 2]})
        ok('PUT aim wrong length -> 400', r.status_code == 400)

        # PUT /aim on LED fixture -> 404
        r = c.post('/api/fixtures', json={'name': 'LED', 'type': 'linear', 'fixtureType': 'led'})
        led_id = r.get_json().get('id')
        r = c.put(f'/api/fixtures/{led_id}/aim', json={'rotation': [0, 0, 0]})
        ok('PUT aim on LED -> 404', r.status_code == 404)

        # Default rotation on creation
        r = c.post('/api/fixtures', json={
            'name': 'MH Default', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 20, 'dmxChannelCount': 3,
        })
        fid2 = r.get_json().get('id')
        r = c.get(f'/api/fixtures/{fid2}')
        ok('Default rotation', r.get_json().get('rotation') == [0, 0, 0])

    # ================================================================
    # SUMMARY
    # ================================================================
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
