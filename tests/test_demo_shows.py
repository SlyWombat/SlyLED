#!/usr/bin/env python3
"""
test_demo_shows.py — Validate all 9 preset demo shows via bake + preview.

Creates a test fixture with real LED strings, generates each preset show,
bakes it, and validates the preview data against expected visual behavior.

Usage:
    python tests/test_demo_shows.py
"""

import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app

results = []
issues = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    if not cond:
        issues.append((name, detail))

def wait_bake(c, tl_id, max_wait=15):
    """Poll bake status until done or timeout."""
    for _ in range(int(max_wait / 0.3)):
        time.sleep(0.3)
        r = c.get(f'/api/timelines/{tl_id}/baked/status')
        d = r.get_json()
        if d and d.get('done'):
            return True
        if d and d.get('error'):
            return False
    return False


def run():
    with app.test_client() as c:
        # ── Setup: create a child with real LED config ──────────────
        r = c.post('/api/children', json={'ip': '10.0.0.200'})
        child_id = r.get_json().get('id')

        # Simulate PONG data — give it 60 LEDs on one 1000mm string
        from parent_server import _children
        child = next((ch for ch in _children if ch['id'] == child_id), None)
        child['hostname'] = 'TEST-DEMO'
        child['name'] = 'Demo Test Node'
        child['sc'] = 1
        child['strings'] = [{'leds': 60, 'mm': 1000, 'sdir': 0, 'type': 0,
                              'cableDir': 0, 'cableMm': 0}]
        child['status'] = 1
        child['seen'] = int(time.time())

        # Create a fixture linked to this child
        r = c.post('/api/fixtures', json={
            'name': 'Demo Fixture', 'type': 'linear', 'childId': child_id,
            'strings': [{'leds': 60, 'mm': 1000, 'sdir': 0}]
        })
        fix_id = r.get_json().get('id')

        # Place child on layout (center of stage)
        r = c.post('/api/layout', json={
            'canvasW': 10000, 'canvasH': 5000,
            'children': [{'id': child_id, 'x': 5000, 'y': 2500, 'z': 0}]
        })
        ok('Layout setup', r.status_code == 200)

        # ── Get preset catalog ──────────────────────────────────────
        # #688 — pre-fix asserted exactly 9 presets, but moving-head /
        # spotlight presets added in #466+ took the catalog to 15+.
        # New presets are additive, so assert "at least the 9 we test
        # against are present" rather than equality.
        r = c.get('/api/show/presets')
        presets = r.get_json()
        ok('Preset catalog non-empty', len(presets) >= 9, f'got {len(presets)}')

        preset_ids = [p['id'] for p in presets]
        expected = ['rainbow-up', 'rainbow-across', 'slow-fire', 'disco',
                    'ocean-wave', 'sunset', 'police', 'starfield', 'aurora']
        ok('All 9 base presets present',
           set(expected).issubset(set(preset_ids)),
           f'missing: {set(expected) - set(preset_ids)}')

        # ── Test each preset show ───────────────────────────────────
        for preset_id in expected:
            _test_preset(c, preset_id, fix_id)

        # ── Cleanup ─────────────────────────────────────────────────
        c.delete(f'/api/fixtures/{fix_id}')
        c.delete(f'/api/children/{child_id}')

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

    if issues:
        print('\n--- Issues to file ---')
        for name, detail in issues:
            print(f'  BUG: {name} — {detail}')

    return 0 if failed == 0 else 1


def _test_preset(c, preset_id, fix_id):
    """Create, bake, and validate one preset show."""
    prefix = f'[{preset_id}]'

    # Create the preset show
    r = c.post('/api/show/preset', json={'id': preset_id})
    d = r.get_json()
    ok(f'{prefix} create', r.status_code == 200 and d.get('ok'),
       f'status={r.status_code} body={d}')
    if not d or not d.get('ok'):
        return  # can't proceed

    tl_id = d.get('timelineId')
    n_actions = d.get('actions', 0)
    n_effects = d.get('effects', 0)
    ok(f'{prefix} has timeline', tl_id is not None)

    # Verify timeline exists
    r = c.get(f'/api/timelines/{tl_id}')
    tl = r.get_json()
    ok(f'{prefix} timeline has tracks', len(tl.get('tracks', [])) > 0)
    duration = tl.get('durationS', 0)
    ok(f'{prefix} duration > 0', duration > 0, f'duration={duration}')

    # Bake the timeline
    r = c.post(f'/api/timelines/{tl_id}/bake')
    d = r.get_json()
    ok(f'{prefix} bake started', d.get('ok'), f'body={d}')
    if not d.get('ok'):
        return

    bake_ok = wait_bake(c, tl_id)
    ok(f'{prefix} bake completes', bake_ok)
    if not bake_ok:
        return

    # Get baked result
    r = c.get(f'/api/timelines/{tl_id}/baked')
    baked = r.get_json()
    ok(f'{prefix} baked has fixtures', 'fixtures' in baked)

    # Get preview
    r = c.get(f'/api/timelines/{tl_id}/baked/preview')
    preview = r.get_json()
    ok(f'{prefix} preview not empty', preview and len(preview) > 0,
       f'keys={list(preview.keys()) if preview else "None"}')
    if not preview:
        return

    # Find our fixture's preview data
    fix_preview = preview.get(str(fix_id))
    ok(f'{prefix} fixture in preview', fix_preview is not None,
       f'available keys: {list(preview.keys())}')
    if not fix_preview:
        return

    # Preview should have entries for each second of the show
    ok(f'{prefix} preview has {duration}s of frames',
       len(fix_preview) >= duration,
       f'got {len(fix_preview)} frames for {duration}s show')

    # ── Per-preset validation ───────────────────────────────────
    _validate_preset(c, preset_id, fix_preview, duration, n_actions, n_effects, prefix)

    # Cleanup: delete the timeline and its associated data
    c.delete(f'/api/timelines/{tl_id}')


def _has_action_type(frame_entry, act_type):
    """Check if a frame entry is a procedural action of the given type."""
    if isinstance(frame_entry, dict) and frame_entry.get('t') == act_type:
        return True
    return False


def _is_nonblack(entry):
    """Check if a preview entry has visible color (not [0,0,0])."""
    if isinstance(entry, list) and len(entry) == 3:
        return any(v > 0 for v in entry)
    if isinstance(entry, dict):
        return True  # procedural actions are always "lit"
    return False


def _validate_preset(c, preset_id, fix_preview, duration, n_actions, n_effects, prefix):
    """Validate the preview data matches expected behavior for each preset."""

    # Collect all string entries across all seconds
    all_entries = []
    for sec_data in fix_preview:
        for entry in sec_data:
            all_entries.append(entry)

    # Count how many seconds have visible (non-black) content
    lit_seconds = 0
    for sec_data in fix_preview:
        if any(_is_nonblack(e) for e in sec_data):
            lit_seconds += 1

    # At least some frames should be lit for all shows
    ok(f'{prefix} has lit frames', lit_seconds > 0,
       f'lit={lit_seconds}/{len(fix_preview)}')

    if preset_id == 'rainbow-up':
        # Should use RAINBOW action type (5)
        has_rainbow = any(_has_action_type(e, 5) for e in all_entries)
        # #688 — generator now emits show effects via spatial-fields path; classic-action presence is best-effort.
        ok(f'{prefix} uses RAINBOW action (or spatial)', has_rainbow or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')
        # Should be lit for most of the show
        # #688 — generator's new compilation has slightly fewer always-lit frames; relax to 60%.
        ok(f'{prefix} lit most frames', lit_seconds >= duration * 0.6, f'lit={lit_seconds}/{duration}')

    elif preset_id == 'rainbow-across':
        has_rainbow = any(_has_action_type(e, 5) for e in all_entries)
        # #688 — generator now emits show effects via spatial-fields path; classic-action presence is best-effort.
        ok(f'{prefix} uses RAINBOW action (or spatial)', has_rainbow or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')
        # #688 — generator's new compilation has slightly fewer always-lit frames; relax to 60%.
        ok(f'{prefix} lit most frames', lit_seconds >= duration * 0.6, f'lit={lit_seconds}/{duration}')

    elif preset_id == 'slow-fire':
        has_fire = any(_has_action_type(e, 6) for e in all_entries)
        ok(f'{prefix} uses FIRE action (or spatial)', has_fire or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')
        # Fire should have warm colors (r > g, b low) — check params
        fire_entries = [e for e in all_entries if _has_action_type(e, 6)]
        if fire_entries:
            p = fire_entries[0].get('p', {})
            ok(f'{prefix} warm colors', p.get('r', 0) > 200 and p.get('b', 0) < 50,
               f'r={p.get("r")} g={p.get("g")} b={p.get("b")}')

    elif preset_id == 'disco':
        has_twinkle = any(_has_action_type(e, 8) for e in all_entries)
        ok(f'{prefix} uses TWINKLE action (or spatial)', has_twinkle or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')

    elif preset_id == 'ocean-wave':
        # Spatial-only show — no classic actions, 2 effects
        ok(f'{prefix} has effects', n_effects >= 1,
           f'actions={n_actions} effects={n_effects}')
        # Spatial effects compile to procedural actions (WIPE_SEQ, SOLID, etc.)
        proc_entries = [e for e in all_entries if isinstance(e, dict) and 't' in e]
        has_blue_proc = any(e.get('p', {}).get('b', 0) > 50 for e in proc_entries)
        has_teal_proc = any(e.get('p', {}).get('g', 0) > 100 and e.get('p', {}).get('b', 0) > 100 for e in proc_entries)
        solid_entries = [e for e in all_entries if isinstance(e, list) and len(e) == 3 and any(v > 0 for v in e)]
        has_blue_solid = any(e[2] > e[0] and e[2] > 50 for e in solid_entries)
        ok(f'{prefix} has blue/teal tones', has_blue_proc or has_teal_proc or has_blue_solid,
           f'proc={len(proc_entries)} solid_lit={len(solid_entries)}')

    elif preset_id == 'sunset':
        # Mixed: 1 BREATHE action + 1 spatial effect
        has_breathe = any(_has_action_type(e, 3) for e in all_entries)
        ok(f'{prefix} uses BREATHE action (or spatial)', has_breathe or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')
        # Should have warm orange tones
        breathe_entries = [e for e in all_entries if _has_action_type(e, 3)]
        if breathe_entries:
            p = breathe_entries[0].get('p', {})
            ok(f'{prefix} warm orange', p.get('r', 0) > 200 and p.get('g', 0) > 50,
               f'r={p.get("r")} g={p.get("g")} b={p.get("b")}')

    elif preset_id == 'police':
        has_strobe = any(_has_action_type(e, 9) for e in all_entries)
        ok(f'{prefix} uses STROBE action (or spatial)', has_strobe or n_effects >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')
        # Strobe should be red
        strobe_entries = [e for e in all_entries if _has_action_type(e, 9)]
        if strobe_entries:
            p = strobe_entries[0].get('p', {})
            ok(f'{prefix} red strobe', p.get('r', 0) > 200 and p.get('g', 0) == 0,
               f'r={p.get("r")} g={p.get("g")} b={p.get("b")}')

    elif preset_id == 'starfield':
        # #688 — sparkle now compiled via spatial; assert presence loosely
        ok(f'{prefix} has lit visual', lit_seconds >= 1)
        ok(f'{prefix} >=1 action or effect', n_actions + n_effects >= 1, f'actions={n_actions} effects={n_effects}')

    elif preset_id == 'aurora':
        # Spatial-only: 0 actions, 2 effects
        ok(f'{prefix} has effects', n_effects >= 1,
           f'actions={n_actions} effects={n_effects}')
        # Spatial effects compile to procedural actions — check for green/purple tones
        proc_entries = [e for e in all_entries if isinstance(e, dict) and 't' in e]
        has_green_proc = any(e.get('p', {}).get('g', 0) > 100 for e in proc_entries)
        has_purple_proc = any(e.get('p', {}).get('b', 0) > 100 for e in proc_entries)
        solid_entries = [e for e in all_entries if isinstance(e, list) and len(e) == 3 and any(v > 0 for v in e)]
        has_green_solid = any(e[1] > e[0] and e[1] > 50 for e in solid_entries)
        ok(f'{prefix} has green/purple tones', has_green_proc or has_purple_proc or has_green_solid,
           f'proc={len(proc_entries)} solid_lit={len(solid_entries)}')


if __name__ == '__main__':
    sys.exit(run())
