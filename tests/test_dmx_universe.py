#!/usr/bin/env python3
"""test_dmx_universe.py — DMXUniverse helper contract.

Pins the contract for `set_fixture_rgb` and `set_fixture_dimmer` in
`dmx_universe.py`. Currently fails Scenario 3 (color-wheel-only
profile) until #842 reopen ships; the assertion is pinned so the
regression test inverts when the helper centralizes color-wheel
resolution.

Run:  python -X utf8 tests/test_dmx_universe.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))
from dmx_universe import DMXUniverse
from dmx_profiles import rgb_to_wheel_slot

_pass = 0
_fail = 0
_errors = []

def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')

def section(name):
    print(f'\n=== {name} ===')


def rgb_only_profile():
    return {
        'channels': [
            {'offset': 0, 'name': 'Red',   'type': 'red'},
            {'offset': 1, 'name': 'Green', 'type': 'green'},
            {'offset': 2, 'name': 'Blue',  'type': 'blue'},
        ],
        'channel_map': {'red': 0, 'green': 1, 'blue': 2},
    }


def wheel_only_profile():
    return {
        'channels': [
            {'offset': 0, 'name': 'Pan', 'type': 'pan', 'bits': 16},
            {'offset': 2, 'name': 'Tilt', 'type': 'tilt', 'bits': 16},
            {'offset': 4, 'name': 'Dimmer', 'type': 'dimmer'},
            {'offset': 5, 'name': 'Color', 'type': 'color-wheel',
             'capabilities': [
                 {'type': 'WheelSlot', 'range': [0,  9],  'label': 'Open',  'color': '#FFFFFF'},
                 {'type': 'WheelSlot', 'range': [10, 19], 'label': 'Red',   'color': '#FF0000'},
                 {'type': 'WheelSlot', 'range': [20, 29], 'label': 'Green', 'color': '#00FF00'},
                 {'type': 'WheelSlot', 'range': [30, 39], 'label': 'Blue',  'color': '#0000FF'},
             ]},
        ],
        'channel_map': {'pan': 0, 'tilt': 2, 'dimmer': 4, 'color-wheel': 5},
    }


def hybrid_profile():
    """Both RGB and color-wheel — RGB takes precedence; wheel ignored."""
    return {
        'channels': [
            {'offset': 0, 'name': 'Red',   'type': 'red'},
            {'offset': 1, 'name': 'Green', 'type': 'green'},
            {'offset': 2, 'name': 'Blue',  'type': 'blue'},
            {'offset': 3, 'name': 'Color', 'type': 'color-wheel',
             'capabilities': [
                 {'type': 'WheelSlot', 'range': [0, 9],   'label': 'Open',  'color': '#FFFFFF'},
                 {'type': 'WheelSlot', 'range': [10, 19], 'label': 'Red',   'color': '#FF0000'},
             ]},
        ],
        'channel_map': {'red': 0, 'green': 1, 'blue': 2, 'color-wheel': 3},
    }


def main():
    print('=== DMXUniverse Helper Contract ===')

    # ── 1. RGB-only profile ─────────────────────────────────────────
    section('Scenario 1: RGB-only profile — set_fixture_rgb writes R/G/B '
            'channels at the mapped offsets')
    u = DMXUniverse(1)
    u.set_fixture_rgb(1, 255, 128, 64, rgb_only_profile())
    ok(u.get_channel(1) == 255 and u.get_channel(2) == 128 and u.get_channel(3) == 64,
       f'R/G/B written at offsets 0/1/2 (got {[u.get_channel(i) for i in range(1,4)]})')

    # ── 2. No-profile fallback ──────────────────────────────────────
    section('Scenario 2: profile=None — set_fixture_rgb writes 3 '
            'consecutive channels starting at start_addr')
    u = DMXUniverse(1)
    u.set_fixture_rgb(10, 100, 150, 200, profile=None)
    ok(u.get_channel(10) == 100 and u.get_channel(11) == 150 and u.get_channel(12) == 200,
       f'R/G/B at consecutive offsets (got {[u.get_channel(i) for i in range(10, 13)]})')

    # ── 3. Color-wheel-only profile ─────────────────────────────────
    # CURRENT BEHAVIOUR: writes nothing because no R/G/B in channel_map.
    # EXPECTED (per #842 reopen): set_fixture_rgb resolves the closest
    # wheel slot and writes its midpoint value.
    section('Scenario 3: color-wheel-only profile — set_fixture_rgb '
            'should resolve wheel slot. KNOWN BUG #842 reopen — '
            'currently writes nothing')
    u = DMXUniverse(1)
    prof = wheel_only_profile()
    u.set_fixture_rgb(1, 255, 0, 0, prof)
    expected_slot = rgb_to_wheel_slot(prof, 255, 0, 0)
    actual_wheel = u.get_channel(6)  # color-wheel at offset 5 → addr 6
    ok(actual_wheel == expected_slot,
       f'set_fixture_rgb(255,0,0) writes wheel slot '
       f'(expected {expected_slot} for "Red", got {actual_wheel}) — '
       f'KNOWN BUG #842: helper does not resolve wheel; caller must '
       f'invoke rgb_to_wheel_slot manually')

    # ── 4. Hybrid profile (RGB wins) ────────────────────────────────
    section('Scenario 4: hybrid profile (RGB + color-wheel) — RGB takes '
            'precedence; wheel slot is left untouched')
    u = DMXUniverse(1)
    prof = hybrid_profile()
    u.set_fixture_rgb(1, 255, 0, 0, prof)
    rgb_ok = u.get_channel(1) == 255 and u.get_channel(2) == 0 and u.get_channel(3) == 0
    ok(rgb_ok,
       f'RGB written to dedicated channels (got R={u.get_channel(1)} '
       f'G={u.get_channel(2)} B={u.get_channel(3)})')
    # Per #842 fix spec: wheel should be left at 0 when RGB channels exist.
    # Current behaviour is already this (no wheel write happens).
    ok(u.get_channel(4) == 0,
       f'Color-wheel channel left at 0 when RGB present (got {u.get_channel(4)})')

    # ── 5. set_fixture_dimmer with profile ─────────────────────────
    section('Scenario 5: set_fixture_dimmer writes ONLY the master dimmer '
            'channel (#749 — auxiliary "dimmer"-typed channels untouched)')
    profile = {
        'channels': [
            {'offset': 0, 'name': 'Master Dim', 'type': 'dimmer', 'default': 255},
            {'offset': 1, 'name': 'Red',  'type': 'red'},
            {'offset': 2, 'name': 'Aux Dim', 'type': 'dimmer', 'default': 0},  # mistyped aux
        ],
        'channel_map': {'dimmer': 0, 'red': 1},  # master only
    }
    u = DMXUniverse(1)
    u.set_channel(3, 99)  # pre-load aux channel with sentinel
    u.set_fixture_dimmer(1, 200, profile)
    ok(u.get_channel(1) == 200,
       f'Master dimmer written (got {u.get_channel(1)})')
    ok(u.get_channel(3) == 99,
       f'Aux dimmer channel untouched (got {u.get_channel(3)}, expected 99) '
       f'— #749 master-only behaviour holds')

    # ── 6. set_fixture_dimmer with no profile — no-op ───────────────
    section('Scenario 6: set_fixture_dimmer with profile=None — no-op')
    u = DMXUniverse(1)
    u.set_fixture_dimmer(1, 200, profile=None)
    ok(all(u.get_channel(i) == 0 for i in range(1, 11)),
       f'No-profile dimmer call writes nothing (chans={[u.get_channel(i) for i in range(1,11)]})')

    # ── Summary ─────────────────────────────────────────────────────
    print()
    print('=' * 60)
    if _fail == 0:
        print(f'  \033[32m{_pass} passed\033[0m, 0 failed out of {_pass + _fail}')
    else:
        print(f'  \033[32m{_pass} passed\033[0m, '
              f'\033[31m{_fail} failed\033[0m out of {_pass + _fail}')
        for e in _errors:
            print(f'    - {e}')
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
