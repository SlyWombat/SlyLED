#!/usr/bin/env python3
"""
test_light_map.py — Unit tests for #234: Per-fixture light mapping.

Tests build_light_map() and light_map_inverse() with mock beam detection.

Usage:
    python tests/test_light_map.py        # run all
    python tests/test_light_map.py -v     # verbose
"""

import sys, os, math
from unittest.mock import patch

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))

import mover_calibrator as mc

# ── Test infrastructure ──────────────────────────────────────────────────

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose:
            print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── build_light_map ──────────────────────────────────────────────────────

section('build_light_map (#234)')

# Mock: beam detection returns pixel proportional to pan/tilt
def mock_settle(cam_ip, cam_idx, color, prev_pan=None, prev_tilt=None,
                new_pan=None, new_tilt=None, center=False, threshold=50):
    if new_pan is None:
        return (320, 240)
    # Pixel proportional to pan/tilt (simulates a simple camera view)
    px = int(100 + new_pan * 440)
    py = int(50 + new_tilt * 380)
    return (px, py)

# Homography: simple scale (1px = 10mm)
H = [[10.0, 0.0, 0.0],
     [0.0, 10.0, 0.0],
     [0.0, 0.0, 1.0]]

boundaries = {"panMin": 0.2, "panMax": 0.8, "tiltMin": 0.3, "tiltMax": 0.7}

with patch.object(mc, '_wait_settled', mock_settle):
    with patch.object(mc, '_hold_dmx'):
        with patch.object(mc, '_send_artnet'):
            lm = mc.build_light_map(
                '10.0.0.1', '10.0.0.2', 0, 1, (255, 0, 0),
                boundaries, H,
                pan_steps=5, tilt_steps=4)

ok(lm is not None, 'build_light_map returns result')
ok(lm['panSteps'] == 5, f'panSteps = 5: {lm.get("panSteps")}')
ok(lm['tiltSteps'] == 4, f'tiltSteps = 4: {lm.get("tiltSteps")}')
ok(lm['sampleCount'] >= 10, f'Sample count >= 10: {lm.get("sampleCount")}')
ok(len(lm['samples']) > 0, 'Samples list is non-empty')

# Verify sample structure
s0 = lm['samples'][0]
ok('pan' in s0 and 'tilt' in s0, 'Sample has pan/tilt')
ok('stageX' in s0 and 'stageY' in s0, 'Sample has stageX/Y')
ok('px' in s0 and 'py' in s0, 'Sample has pixel coords')

# Stage coords should increase with pan
first_pan = [s for s in lm['samples'] if abs(s['tilt'] - 0.3) < 0.05]
if len(first_pan) >= 2:
    ok(first_pan[-1]['stageX'] > first_pan[0]['stageX'],
       'Stage X increases with pan')

# ── light_map_inverse ────────────────────────────────────────────────────

section('light_map_inverse (#234)')

# Use the light map we just built
if lm:
    # Pick a known sample and try to inverse-lookup it
    target = lm['samples'][len(lm['samples']) // 2]
    result = mc.light_map_inverse(lm, target['stageX'], target['stageY'])
    ok(result is not None, 'Inverse lookup returns result')
    if result:
        pan, tilt = result
        ok(abs(pan - target['pan']) < 0.1,
           f'Inverse pan ≈ {target["pan"]}: got {pan:.3f}')
        ok(abs(tilt - target['tilt']) < 0.1,
           f'Inverse tilt ≈ {target["tilt"]}: got {tilt:.3f}')

    # Out-of-range target: should still return nearest
    result2 = mc.light_map_inverse(lm, -9999, -9999)
    ok(result2 is not None, 'Out-of-range target still returns nearest')

    # Empty light map
    result3 = mc.light_map_inverse({"samples": []}, 0, 0)
    ok(result3 is None, 'Empty light map returns None')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
