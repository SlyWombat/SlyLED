#!/usr/bin/env python3
"""
test_settle_sweep.py — Unit tests for #238 (settle time) and #239 (sweep boundary).

Tests adaptive settle time logic, movement-distance scaling, boundary
detection during BFS mapping, and boundary verification.

Usage:
    python tests/test_settle_sweep.py        # run all
    python tests/test_settle_sweep.py -v     # verbose
"""

import sys, os, math, time
from unittest.mock import patch, MagicMock

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


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── #238: Settle time constants ──────────────────────────────────────────

section('Settle Time Constants (#238)')

ok(mc.SETTLE_BASE == 0.8, f'SETTLE_BASE = 0.8 (got {mc.SETTLE_BASE})')
ok(len(mc.SETTLE_ESCALATE) == 3, f'3 escalation stages (got {len(mc.SETTLE_ESCALATE)})')
ok(mc.SETTLE_ESCALATE[0] == 0.8, f'Stage 1 = 0.8s')
ok(mc.SETTLE_ESCALATE[1] == 1.5, f'Stage 2 = 1.5s')
ok(mc.SETTLE_ESCALATE[2] == 2.5, f'Stage 3 = 2.5s')
ok(mc.SETTLE_VERIFY_GAP == 0.3, f'Verify gap = 0.3s')
ok(mc.SETTLE_PIXEL_THRESH == 30, f'Pixel threshold = 30')

# ── #238: _wait_settled logic ────────────────────────────────────────────

section('_wait_settled Logic (#238)')

# Test 1: Stable beam — both captures agree (within threshold)
call_count = [0]
def mock_detect_stable(cam_ip, cam_idx, color=None, threshold=50, center=False):
    call_count[0] += 1
    return (320, 240)  # Always same position

with patch.object(mc, '_beam_detect', mock_detect_stable):
    with patch.object(mc, 'time') as mock_time:
        mock_time.sleep = lambda x: None  # skip actual waits
        result = mc._wait_settled('1.2.3.4', 0, (255, 0, 0))
        ok(result is not None, 'Stable beam returns position')
        ok(result == (320, 240), f'Returns correct position: {result}')

# Test 2: Beam drifting — first attempt exceeds threshold, second succeeds
drift_calls = [0]
def mock_detect_drift(cam_ip, cam_idx, color=None, threshold=50, center=False):
    drift_calls[0] += 1
    # Calls 1-2: drifting (dx=50, exceeds threshold of 30)
    # Calls 3-4: settled (dx=2, within threshold)
    if drift_calls[0] <= 2:
        return (250 + drift_calls[0] * 50, 200)
    return (320, 240)

with patch.object(mc, '_beam_detect', mock_detect_drift):
    with patch.object(mc, 'time') as mock_time:
        mock_time.sleep = lambda x: None
        result = mc._wait_settled('1.2.3.4', 0, (255, 0, 0))
        ok(result is not None, 'Drifting beam: eventually settles')
        ok(drift_calls[0] >= 3, f'Escalated to more captures: {drift_calls[0]}')

# Test 3: No beam at all
def mock_detect_none(cam_ip, cam_idx, color=None, threshold=50, center=False):
    return None

with patch.object(mc, '_beam_detect', mock_detect_none):
    with patch.object(mc, 'time') as mock_time:
        mock_time.sleep = lambda x: None
        result = mc._wait_settled('1.2.3.4', 0, (255, 0, 0))
        ok(result is None, 'No beam → returns None')

# Test 4: Movement distance scaling — large move gets longer base time
with patch.object(mc, '_beam_detect', mock_detect_stable):
    with patch.object(mc, 'time') as mock_time:
        sleep_times = []
        mock_time.sleep = lambda x: sleep_times.append(x)
        # Small move
        mc._wait_settled('1.2.3.4', 0, (255, 0, 0),
                         prev_pan=0.5, prev_tilt=0.5,
                         new_pan=0.55, new_tilt=0.5)
        small_first_sleep = sleep_times[0] if sleep_times else 0

        sleep_times.clear()
        # Large move
        mc._wait_settled('1.2.3.4', 0, (255, 0, 0),
                         prev_pan=0.1, prev_tilt=0.1,
                         new_pan=0.9, new_tilt=0.9)
        large_first_sleep = sleep_times[0] if sleep_times else 0

        ok(large_first_sleep >= small_first_sleep,
           f'Large move wait >= small: {large_first_sleep:.2f} >= {small_first_sleep:.2f}')

# ── #239: map_visible boundary tracking ──────────────────────────────────

section('map_visible Boundary Tracking (#239)')

# Test: BFS with mock that has a visible region only in center
visible_region = set()
for p in range(4, 7):   # pan 0.20 to 0.30
    for t in range(4, 7):  # tilt 0.20 to 0.30
        visible_region.add((round(p * 0.05, 3), round(t * 0.05, 3)))

detect_calls = [0]
def mock_detect_region(cam_ip, cam_idx, color=None, threshold=50, center=False):
    detect_calls[0] += 1
    return (320, 240)  # fixed pixel for simplicity

# We need to mock both _wait_settled and the DMX functions
with patch.object(mc, '_hold_dmx'):
    with patch.object(mc, '_send_artnet'):
        with patch.object(mc, '_wait_settled') as mock_ws:
            def settled_or_lost(cam_ip, cam_idx, color, prev_pan=None, prev_tilt=None,
                                new_pan=None, new_tilt=None, center=False, threshold=50):
                key = (round(new_pan, 3), round(new_tilt, 3)) if new_pan is not None else (0.25, 0.25)
                if key in visible_region:
                    return (320 + int(new_pan * 100), 240 + int(new_tilt * 100))
                return None

            mock_ws.side_effect = settled_or_lost

            samples, bounds = mc.map_visible(
                '10.0.0.1', '10.0.0.2', 1, 0, (255, 0, 0),
                start_pan=0.25, start_tilt=0.25,
                step=0.05, max_samples=30)

            ok(len(samples) > 0, f'Got samples: {len(samples)}')
            ok('panMin' in bounds, 'Boundaries contain panMin')
            ok('panMax' in bounds, 'Boundaries contain panMax')
            ok('tiltMin' in bounds, 'Boundaries contain tiltMin')
            ok('tiltMax' in bounds, 'Boundaries contain tiltMax')
            ok('verified' in bounds, 'Boundaries contain verified flag')

            # Boundaries should be within the visible region
            ok(bounds['panMin'] >= 0.15, f'panMin >= 0.15: {bounds["panMin"]}')
            ok(bounds['panMax'] <= 0.40, f'panMax <= 0.40: {bounds["panMax"]}')
            ok(bounds['tiltMin'] >= 0.15, f'tiltMin >= 0.15: {bounds["tiltMin"]}')
            ok(bounds['tiltMax'] <= 0.40, f'tiltMax <= 0.40: {bounds["tiltMax"]}')

# Test: BFS returns both samples and boundaries (tuple unpacking)
with patch.object(mc, '_hold_dmx'):
    with patch.object(mc, '_send_artnet'):
        with patch.object(mc, '_wait_settled', return_value=(320, 240)):
            result = mc.map_visible(
                '10.0.0.1', '10.0.0.2', 1, 0, (255, 0, 0),
                start_pan=0.5, start_tilt=0.5,
                step=0.05, max_samples=5)
            ok(isinstance(result, tuple) and len(result) == 2,
               'map_visible returns (samples, boundaries) tuple')
            s, b = result
            ok(isinstance(s, list), 'First element is samples list')
            ok(isinstance(b, dict), 'Second element is boundaries dict')

# ── #239: _verify_boundary ───────────────────────────────────────────────

section('Boundary Verification (#239)')

# Test: beam invisible at boundary → True
with patch.object(mc, '_beam_detect', return_value=None):
    with patch.object(mc, '_hold_dmx'):
        with patch.object(mc, '_send_artnet'):
            dmx = [0] * 512
            is_boundary = mc._verify_boundary(
                '10.0.0.1', '10.0.0.2', 0, 1, 0.3, 0.3,
                (255, 0, 0), dmx)
            ok(is_boundary is True, 'No beam at boundary → True')

# Test: beam visible at boundary → False
with patch.object(mc, '_beam_detect', return_value=(100, 200)):
    with patch.object(mc, '_hold_dmx'):
        with patch.object(mc, '_send_artnet'):
            dmx = [0] * 512
            is_boundary = mc._verify_boundary(
                '10.0.0.1', '10.0.0.2', 0, 1, 0.3, 0.3,
                (255, 0, 0), dmx)
            ok(is_boundary is False, 'Beam visible at boundary → False')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
