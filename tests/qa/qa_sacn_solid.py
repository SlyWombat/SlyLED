#!/usr/bin/env python3
"""QA: stream a solid colour to a pixel controller over sACN (E1.31), unicast.

Standalone on purpose — no orchestrator in the loop — so it proves the
controller's port/universe config independently of SlyLED's output engine.

  python tests/qa/qa_sacn_solid.py 192.168.10.6 --pixels 200 --rgb 255,0,0 --seconds 120
  python tests/qa/qa_sacn_solid.py 192.168.10.6 --pixels 200 --rgb 0,0,0 --seconds 3   # off
"""
import argparse
import socket
import struct
import time
import uuid

CID = uuid.UUID("5a1ed000-0943-4a00-8000-000000000943").bytes


def packet(universe, seq, data, source="SlyLED QA", priority=100):
    n = len(data)
    dmp = struct.pack("!HBBHHH", 0x7000 | (10 + 1 + n), 0x02, 0xA1, 0, 1, 1 + n) + b"\x00" + data
    framing = (struct.pack("!HI", 0x7000 | (77 + len(dmp)), 0x00000002)
               + source.encode()[:63].ljust(64, b"\x00")
               + struct.pack("!BHBBH", priority, 0, seq & 0xFF, 0, universe))
    root = (struct.pack("!HH12s", 0x0010, 0, b"ASC-E1.17\x00\x00\x00")
            + struct.pack("!HI", 0x7000 | (22 + len(framing) + len(dmp)), 0x00000004) + CID)
    return root + framing + dmp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ip")
    ap.add_argument("--pixels", type=int, default=200)
    ap.add_argument("--rgb", default="255,0,0")
    ap.add_argument("--universe", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--fps", type=float, default=25)
    a = ap.parse_args()
    rgb = bytes(int(x) for x in a.rgb.split(","))
    frame = rgb * a.pixels
    chunks = [frame[i:i + 510] for i in range(0, len(frame), 510)]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq, end = 0, time.time() + a.seconds
    print(f"sACN -> {a.ip}: {a.pixels} px rgb={tuple(rgb)} universes "
          f"{a.universe}..{a.universe + len(chunks) - 1} ({[len(c) for c in chunks]} ch) "
          f"for {a.seconds}s @ {a.fps} fps", flush=True)
    while time.time() < end:
        for i, c in enumerate(chunks):
            sock.sendto(packet(a.universe + i, seq, c), (a.ip, 5568))
        seq += 1
        time.sleep(1 / a.fps)
    print(f"done, {seq} frames", flush=True)


if __name__ == "__main__":
    main()
