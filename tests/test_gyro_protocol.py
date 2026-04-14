#!/usr/bin/env python3
"""
test_gyro_protocol.py - Unit tests for gyro board UDP protocol encoding/decoding.

Tests CMD_GYRO_ORIENT and CMD_GYRO_CTRL packet layout, edge-case angle values,
angle scaling, flags byte, and round-trip correctness.

Usage:
    python tests/test_gyro_protocol.py
"""

import sys
import os
import struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

# Protocol constants (mirrors Protocol.h and parent_server.py)
UDP_MAGIC       = 0x534C
UDP_VERSION     = 4
CMD_GYRO_ORIENT = 0x60
CMD_GYRO_CTRL   = 0x61
CMD_GYRO_RECAL  = 0x62

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def make_orient_pkt(roll_deg, pitch_deg, yaw_deg, fps=20, flags=0b111):
    """Build a raw CMD_GYRO_ORIENT UDP packet matching the firmware wire format."""
    roll100  = int(round(roll_deg  * 100))
    pitch100 = int(round(pitch_deg * 100))
    yaw100   = int(round(yaw_deg   * 100))
    hdr = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_GYRO_ORIENT, 0)
    pay = struct.pack("<hhhBB", roll100, pitch100, yaw100, fps, flags)
    return hdr + pay


def parse_orient_pkt(data):
    """Decode a CMD_GYRO_ORIENT packet. Returns (roll, pitch, yaw, fps, flags)."""
    if len(data) < 16:
        return None
    r100, p100, y100, fps, flags = struct.unpack_from("<hhhBB", data, 8)
    return r100 / 100.0, p100 / 100.0, y100 / 100.0, fps, flags


def run():
    # -- 1. Constant values---------------------------------------------------
    print('-- 1. Protocol constants')
    ok('CMD_GYRO_ORIENT is 0x60', CMD_GYRO_ORIENT == 0x60)
    ok('CMD_GYRO_CTRL   is 0x61', CMD_GYRO_CTRL   == 0x61)
    ok('CMD_GYRO_RECAL  is 0x62', CMD_GYRO_RECAL  == 0x62)

    # -- 2. Packet size-------------------------------------------------------
    print('-- 2. Packet size')
    pkt = make_orient_pkt(0.0, 0.0, 0.0)
    ok('GYRO_ORIENT packet is 16 bytes', len(pkt) == 16,
       f'actual={len(pkt)}')

    # -- 3. Angle encoding - zero---------------------------------------------
    print('-- 3. Angle encoding - zero')
    pkt = make_orient_pkt(0.0, 0.0, 0.0, fps=20, flags=0b111)
    r, p, y, fps, flags = parse_orient_pkt(pkt)
    ok('Roll   0.0deg encodes to 0.0deg',  r   == 0.0)
    ok('Pitch  0.0deg encodes to 0.0deg',  p   == 0.0)
    ok('Yaw    0.0deg encodes to 0.0deg',  y   == 0.0)
    ok('fps=20 round-trips',            fps == 20)
    ok('flags=0b111 round-trips',       flags == 0b111)

    # -- 4. Angle encoding - extremes----------------------------------------
    print('-- 4. Angle encoding - extremes')
    pkt = make_orient_pkt(180.0, -90.0, -180.0)
    r, p, y, _, _ = parse_orient_pkt(pkt)
    ok('Roll  +180.0deg encodes/decodes', abs(r - 180.0) < 0.01,  f'r={r}')
    ok('Pitch  -90.0deg encodes/decodes', abs(p - (-90.0)) < 0.01, f'p={p}')
    ok('Yaw  -180.0deg encodes/decodes',  abs(y - (-180.0)) < 0.01, f'y={y}')

    # -- 5. Sub-degree precision----------------------------------------------
    print('-- 5. Sub-degree precision')
    pkt = make_orient_pkt(12.34, -56.78, 90.01)
    r, p, y, _, _ = parse_orient_pkt(pkt)
    ok('Roll  12.34deg round-trips to 0.01deg precision', abs(r - 12.34) < 0.01,  f'r={r}')
    ok('Pitch -56.78deg round-trips',                   abs(p - (-56.78)) < 0.01, f'p={p}')
    ok('Yaw   90.01deg round-trips',                    abs(y - 90.01) < 0.01,   f'y={y}')

    # -- 6. Flags encoding----------------------------------------------------
    print('-- 6. Flags encoding')
    pkt = make_orient_pkt(0, 0, 0, flags=0b001)  # streaming only
    _, _, _, _, flags = parse_orient_pkt(pkt)
    ok('flags bit0 = streaming', bool(flags & 0x01))
    ok('flags bit1 = imuOk clear', not bool(flags & 0x02))

    pkt = make_orient_pkt(0, 0, 0, flags=0b110)  # imuOk + wifiOk only
    _, _, _, _, flags = parse_orient_pkt(pkt)
    ok('flags bit1 = imuOk', bool(flags & 0x02))
    ok('flags bit2 = wifiOk', bool(flags & 0x04))
    ok('flags bit0 = streaming clear', not bool(flags & 0x01))

    # -- 7. CMD_GYRO_CTRL encoding-------------------------------------------
    print('-- 7. CMD_GYRO_CTRL encoding')
    ctrl_hdr = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_GYRO_CTRL, 0)
    ctrl_pay = struct.pack("<BB", 1, 25)   # enabled=1, fps=25
    pkt = ctrl_hdr + ctrl_pay
    ok('CTRL packet is 10 bytes', len(pkt) == 10, f'actual={len(pkt)}')
    enabled, fps = struct.unpack_from("<BB", pkt, 8)
    ok('CTRL enabled=1 round-trips', enabled == 1)
    ok('CTRL fps=25 round-trips',    fps == 25)

    # -- 8. CMD_GYRO_RECAL has no payload------------------------------------
    print('-- 8. CMD_GYRO_RECAL')
    recal_pkt = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_GYRO_RECAL, 0)
    ok('RECAL packet is 8 bytes (header only)', len(recal_pkt) == 8)
    ok('RECAL cmd byte correct', struct.unpack_from("<B", recal_pkt, 3)[0] == CMD_GYRO_RECAL)


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
