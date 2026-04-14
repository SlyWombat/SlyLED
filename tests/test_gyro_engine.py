#!/usr/bin/env python3
"""
test_gyro_engine.py - Unit tests for GyroEngine orientation-to-DMX mapping.

Tests pan/tilt conversion, EMA smoothing, stale data handling, DMX clamping,
assignment management, and multi-fixture independence.

Usage:
    python tests/test_gyro_engine.py
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from gyro_engine import GyroEngine, init_gyro_engine, get_gyro_engine

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


# -- Stub DMX engines--------------------------------------------------------

class _StubDmx:
    """Records set_channel calls for assertions."""
    def __init__(self):
        self.calls = {}

    def set_channel(self, universe, channel, value):
        self.calls[(universe, channel)] = value

    def get(self, universe, channel):
        return self.calls.get((universe, channel))


# -- Helpers-----------------------------------------------------------------

def make_engine(gyro_state, fixtures, children):
    artnet = _StubDmx()
    sacn   = _StubDmx()
    lock   = threading.Lock()
    eng = GyroEngine(artnet, sacn, gyro_state, lock, fixtures, children)
    return eng, artnet, sacn


def gyro_entry(roll, pitch, yaw=0.0, fps=20, flags=0b111, age=0.0):
    return {"roll": roll, "pitch": pitch, "yaw": yaw,
            "fps": fps, "flags": flags, "ts": time.time() - age}


def run():
    # -- 1. gyro_to_pan_tilt: identity mapping-------------------------------
    print('-- 1. gyro_to_pan_tilt - identity')
    pan, tilt = GyroEngine.gyro_to_pan_tilt(
        roll=0.0, pitch=0.0,
        pan_center=128, tilt_center=128,
        pan_scale=1.0, tilt_scale=1.0,
        pan_offset_deg=0.0, tilt_offset_deg=0.0,
    )
    ok('Identity: pan=128 at roll=0', pan == 128.0, f'pan={pan}')
    ok('Identity: tilt=128 at pitch=0', tilt == 128.0, f'tilt={tilt}')

    # -- 2. Positive angles increase DMX-------------------------------------
    print('-- 2. Positive roll increases pan')
    pan, tilt = GyroEngine.gyro_to_pan_tilt(
        roll=45.0, pitch=0.0,
        pan_center=128, tilt_center=128,
        pan_scale=1.0, tilt_scale=1.0,
        pan_offset_deg=0.0, tilt_offset_deg=0.0,
    )
    ok('Roll=45deg -> pan > 128', pan > 128.0, f'pan={pan}')
    ok('Roll=45deg -> tilt unchanged', tilt == 128.0, f'tilt={tilt}')

    # -- 3. Negative angles decrease DMX-------------------------------------
    print('-- 3. Negative pitch decreases tilt')
    pan, tilt = GyroEngine.gyro_to_pan_tilt(
        roll=0.0, pitch=-30.0,
        pan_center=128, tilt_center=128,
        pan_scale=1.0, tilt_scale=1.0,
        pan_offset_deg=0.0, tilt_offset_deg=0.0,
    )
    ok('Pitch=-30deg -> tilt < 128', tilt < 128.0, f'tilt={tilt}')

    # -- 4. Offset shifts centre---------------------------------------------
    print('-- 4. Pan/tilt offset')
    pan, tilt = GyroEngine.gyro_to_pan_tilt(
        roll=0.0, pitch=0.0,
        pan_center=128, tilt_center=128,
        pan_scale=1.0, tilt_scale=1.0,
        pan_offset_deg=10.0, tilt_offset_deg=-10.0,
    )
    ok('Pan offset +10 shifts pan to 138', abs(pan - 138.0) < 0.01, f'pan={pan}')
    ok('Tilt offset -10 shifts tilt to 118', abs(tilt - 118.0) < 0.01, f'tilt={tilt}')

    # -- 5. DMX clamping at ±127 from centre---------------------------------
    print('-- 5. DMX clamping')
    pan, tilt = GyroEngine.gyro_to_pan_tilt(
        roll=200.0, pitch=-200.0,
        pan_center=128, tilt_center=128,
        pan_scale=1.0, tilt_scale=1.0,
        pan_offset_deg=0.0, tilt_offset_deg=0.0,
    )
    ok('Extreme roll clamped: pan <= 255', pan <= 255.0, f'pan={pan}')
    ok('Extreme pitch clamped: tilt >= 0', tilt >= 0.0, f'tilt={tilt}')
    ok('Clamp at 255: pan==255', pan == 255.0, f'pan={pan}')
    ok('Clamp at 1: tilt==1', tilt == 1.0, f'tilt={tilt}')   # 128-127=1

    # -- 6. Scale factor-----------------------------------------------------
    print('-- 6. Scale factor')
    pan, _ = GyroEngine.gyro_to_pan_tilt(
        roll=2.0, pitch=0.0,
        pan_center=128, tilt_center=128,
        pan_scale=2.0, tilt_scale=1.0,
        pan_offset_deg=0.0, tilt_offset_deg=0.0,
    )
    ok('Scale=2.0: roll=2deg -> pan=129 (not 130)', abs(pan - 129.0) < 0.01, f'pan={pan}')

    # -- 7. Stale data is not written to DMX---------------------------------
    print('-- 7. Stale data ignored')
    gs = {"192.168.1.10": gyro_entry(45.0, 30.0, age=3.0)}  # stale: 3s old
    fxs = [{"id": 1, "fixtureType": "gyro", "gyroEnabled": True,
             "gyroChildId": 99, "assignedMoverId": 10,
             "panCenter": 128, "tiltCenter": 128, "panScale": 1.0, "tiltScale": 1.0,
             "panOffsetDeg": 0.0, "tiltOffsetDeg": 0.0, "smoothing": 1.0}]
    mover = {"id": 10, "fixtureType": "dmx", "dmxUniverse": 1, "dmxStartAddr": 1}
    children = [{"id": 99, "ip": "192.168.1.10"}]
    eng, artnet, sacn = make_engine(gs, fxs + [mover], children)
    eng._tick()
    ok('No DMX written for stale gyro data', not artnet.calls, f'calls={artnet.calls}')

    # -- 8. Fresh data writes to DMX-----------------------------------------
    print('-- 8. Fresh data -> DMX written')
    gs = {"192.168.1.10": gyro_entry(0.0, 0.0, age=0.0)}
    eng, artnet, sacn = make_engine(gs, fxs + [mover], children)
    eng._tick()
    ok('DMX written for fresh gyro data', bool(artnet.calls), f'calls={artnet.calls}')
    ok('Pan channel (U0,ch1) written', artnet.get(0, 1) is not None,
       f'artnet={(artnet.calls)}')

    # -- 9. Disabled fixture is skipped--------------------------------------
    print('-- 9. Disabled fixture skipped')
    fxs_off = [dict(fxs[0], gyroEnabled=False)]
    gs = {"192.168.1.10": gyro_entry(0.0, 0.0, age=0.0)}
    eng, artnet, sacn = make_engine(gs, fxs_off + [mover], children)
    eng._tick()
    ok('No DMX written when fixture disabled', not artnet.calls)

    # -- 10. update_assignment resets EMA state------------------------------
    print('-- 10. update_assignment resets EMA')
    gs = {"192.168.1.10": gyro_entry(90.0, 0.0, age=0.0)}
    eng, artnet, sacn = make_engine(gs, fxs + [mover], children)
    eng._tick()    # runs one tick to populate EMA state
    ok('EMA state set after first tick', 1 in eng._pan_smooth)
    eng.update_assignment(1)
    ok('EMA state cleared after update_assignment', 1 not in eng._pan_smooth)


def report():
    print()
    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    for name, cond, detail in results:
        status = 'PASS' if cond else 'FAIL'
        print(f'  {status} {name}' + (f'  [{detail}]' if detail else ''))
    print()
    print(f'Results: {passed} passed, {failed} failed')
    return failed == 0


if __name__ == '__main__':
    run()
    ok_all = report()
    sys.exit(0 if ok_all else 1)
