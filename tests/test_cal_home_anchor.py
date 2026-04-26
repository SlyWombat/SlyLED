#!/usr/bin/env python3
"""test_cal_home_anchor.py — #691.

Three independent fixes from the basement-rig trace 2026-04-26:

  Bug 1 — cal warm-start reads f["orientation"]["homePan"] (pre-#687
          field). The actual Set Home anchor lives at top-level
          f["homePanDmx16"] / f["homeTiltDmx16"] (DMX-16 units). The
          old read silently no-op'd; the warm-start fell through to
          the geometric estimate.

  Bug 2 — compute_pan_tilt_writes detected 16-bit only by `bits == 16`
          on the coarse channel. Profiles that model pan as two
          separate channels (e.g. movinghead-150w-12ch — `pan` +
          `pan-fine` with no `bits` annotation) were misclassified as
          8-bit and lost the LSB on every move. Test in
          test_pan_tilt_resolution.py.

  Bug 3 — end-of-cal cleanup blackouts the fixture, leaving it slumped
          at mechanical (0,0). Should park at the Set Home anchor when
          one is set. _park_fixture_at_home() does this; falls back to
          blackout when no anchor exists.

This file covers Bug 1 and Bug 3.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


import parent_server
from parent_server import _warm_start_from_home

# ── Bug 1 — _warm_start_from_home ───────────────────────────────────────

section('_warm_start_from_home reads top-level homePanDmx16')

# Operator-set Set Home: pan_norm 0.6770, tilt_norm 0.0000
fix_with_home = {
    'id': 17, 'name': 'MH Stage Right',
    'homePanDmx16': 44364, 'homeTiltDmx16': 0,
    'rotation': [0, 0, 0],
}
home = _warm_start_from_home(fix_with_home)
ok(home is not None, f'home anchor read (got {home})')
ok(abs(home[0] - 0.6770) < 0.0001,
   f'pan_norm matches operator anchor (got {home[0]:.4f})')
ok(home[1] == 0.0,
   f'tilt_norm = 0 (got {home[1]})')

# Missing fields → None (caller falls back to geometric / orientation).
ok(_warm_start_from_home({'id': 1}) is None,
   'no anchor set → None')
ok(_warm_start_from_home({'id': 1, 'homePanDmx16': 100}) is None,
   'partial anchor → None')

# Out-of-range values clamped to [0, 1] without exception.
nuts = _warm_start_from_home({'id': 1, 'homePanDmx16': 99999, 'homeTiltDmx16': -50})
ok(nuts == (1.0, 0.0), f'clamped to [0,1] (got {nuts})')

# Garbage values fail closed (no warm-start).
ok(_warm_start_from_home({'id': 1,
                          'homePanDmx16': 'banana',
                          'homeTiltDmx16': None}) is None,
   'garbage → None (no warm-start, falls through)')

# ── Bug 3 — _park_fixture_at_home ───────────────────────────────────────

section('_park_fixture_at_home routes pan/tilt through the helper')

# Use Flask test client so the in-memory _fixtures + _artnet are wired.
from parent_server import app, _park_fixture_at_home, _targeted_fixture_blackout

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

    # Create a 12-channel moving head fixture (uses split-channel
    # pan/tilt — pan + pan-fine — exercises Bug 2's helper too).
    rv = c.post('/api/fixtures', json={
        'name': 'MH Park Test',
        'type': 'point', 'fixtureType': 'dmx',
        'dmxUniverse': 1, 'dmxStartAddr': 1,
        'dmxChannelCount': 12,
        'dmxProfileId': 'movinghead-150w-12ch',
    })
    fid = rv.get_json()['id']

    # Stamp Set Home anchor.
    c.post(f'/api/fixtures/{fid}/home', json={
        'panDmx16': 44364, 'tiltDmx16': 0,
    })

    # Start DMX engine on loopback so the universe buffer accepts writes.
    c.post('/api/dmx/settings', json={
        'protocol': 'artnet', 'frameRate': 40, 'bindIp': '127.0.0.1',
        'autoStartEngine': True, 'bootBlinkFixtures': False,
    })
    c.post('/api/dmx/start')

    # Drop something visible into the channels first so we can detect
    # the park write actually ran.
    rv = c.post('/api/dmx/monitor/1/set', json={
        'channels': [
            {'addr': 1,  'value': 99},   # pan coarse — should be overwritten
            {'addr': 2,  'value': 99},   # pan fine
            {'addr': 5,  'value': 200},  # dimmer — should zero
            {'addr': 12, 'value': 88},   # last channel — should zero
        ]
    })

    # Park.
    _park_fixture_at_home(fid)

    # Verify pan MSB/LSB at the home anchor and other channels zeroed.
    rv = c.get('/api/dmx/monitor/1')
    chans = rv.get_json()['channels']
    # 44364 = 0xAD4C → pan MSB=173, pan LSB=76
    ok(chans[0] == 173, f'pan MSB=173 at the home anchor (got {chans[0]})')
    ok(chans[1] == 76,  f'pan LSB=76 (#691 + #689 routing) (got {chans[1]})')
    # Tilt MSB/LSB = 0 (anchor was 0).
    ok(chans[2] == 0,   f'tilt MSB=0 (got {chans[2]})')
    ok(chans[3] == 0,   f'tilt LSB=0 (got {chans[3]})')
    # Dimmer + later channels zeroed by the band-clear.
    ok(chans[4] == 0,   f'dimmer=0 (got {chans[4]})')
    ok(chans[11] == 0,  f'ch12=0 (got {chans[11]})')

# ── Bug 3 — fallback when no Set Home anchor ────────────────────────────

section('_park_fixture_at_home falls back to blackout without anchor')

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    rv = c.post('/api/fixtures', json={
        'name': 'MH No-Home',
        'type': 'point', 'fixtureType': 'dmx',
        'dmxUniverse': 1, 'dmxStartAddr': 1,
        'dmxChannelCount': 12,
        'dmxProfileId': 'movinghead-150w-12ch',
    })
    fid = rv.get_json()['id']
    # NO /home POST → fixture has no homePanDmx16.

    c.post('/api/dmx/settings', json={
        'protocol': 'artnet', 'frameRate': 40, 'bindIp': '127.0.0.1',
        'autoStartEngine': True, 'bootBlinkFixtures': False,
    })
    c.post('/api/dmx/start')
    c.post('/api/dmx/monitor/1/set', json={
        'channels': [{'addr': 1, 'value': 200}, {'addr': 5, 'value': 200}]
    })

    _park_fixture_at_home(fid)

    rv = c.get('/api/dmx/monitor/1')
    chans = rv.get_json()['channels']
    ok(chans[0] == 0 and chans[4] == 0,
       f'no-anchor fixture → blackout fallback (ch1={chans[0]} ch5={chans[4]})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
