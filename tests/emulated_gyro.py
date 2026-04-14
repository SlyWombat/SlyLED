#!/usr/bin/env python3
"""
emulated_gyro.py - Virtual gyro board for CI integration testing.

Simulates a Waveshare ESP32-S3 gyro board:
  - Sends CMD_GYRO_ORIENT packets at a configurable rate
  - Responds to CMD_PING with CMD_PONG
  - Responds to CMD_GYRO_CTRL (enable/disable + fps)
  - Responds to CMD_GYRO_RECAL (zeroes the angle)

Usage (as subprocess or direct import):
    # Start emulator on localhost, sending to parent on 127.0.0.1:4210
    python tests/emulated_gyro.py --parent 127.0.0.1 --port 4210 --fps 20

    # As module import for use in test scripts:
    from emulated_gyro import EmulatedGyro
    gyro = EmulatedGyro(parent_ip='127.0.0.1')
    gyro.start()
    ...
    gyro.stop()
"""

import argparse
import math
import socket
import struct
import threading
import time
from typing import Optional

# -- Protocol constants (mirror Protocol.h) ------------------------------------

UDP_MAGIC   = 0x534C
UDP_VERSION = 4
UDP_PORT    = 4210

CMD_PING         = 0x01
CMD_PONG         = 0x02
CMD_GYRO_ORIENT  = 0x60
CMD_GYRO_CTRL    = 0x61
CMD_GYRO_RECAL   = 0x62

HOSTNAME_LEN     = 10
CHILD_NAME_LEN   = 16
CHILD_DESC_LEN   = 32
MAX_STR          = 8

# -- EmulatedGyro --------------------------------------------------------------

class EmulatedGyro:
    """Emulates the UDP behaviour of a BOARD_GYRO device."""

    def __init__(self,
                 parent_ip: str = '127.0.0.1',
                 bind_ip: str   = '0.0.0.0',
                 port: int      = UDP_PORT,
                 hostname: str  = 'SLYG-TEST',
                 fps: int       = 20):
        self.parent_ip  = parent_ip
        self.bind_ip    = bind_ip
        self.port       = port
        self.hostname   = hostname
        self.fps        = fps

        # State
        self._streaming  = False
        self._target_fps = fps
        self._roll_ref   = 0.0
        self._pitch_ref  = 0.0
        self._yaw_ref    = 0.0
        self._t0         = time.time()

        self._sock = None    # type: Optional[socket.socket]
        self._thread = None  # type: Optional[threading.Thread]
        self._running = False
        self._lock = threading.Lock()

        # Counters for assertions
        self.orient_sent  = 0
        self.pong_sent    = 0
        self.recal_count  = 0

    # -- Angle generation ------------------------------------------------------

    def _angles(self):
        """Return (roll, pitch, yaw) as a slow sine wave pattern."""
        t = time.time() - self._t0
        roll  = 30.0 * math.sin(t * 0.3)  - self._roll_ref
        pitch = 20.0 * math.sin(t * 0.2 + 1.0) - self._pitch_ref
        yaw   = 45.0 * math.sin(t * 0.1 + 2.0) - self._yaw_ref
        return roll, pitch, yaw

    # -- Packet builders -------------------------------------------------------

    def _hdr(self, cmd):
        return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd,
                           int(time.time()) & 0xFFFFFFFF)

    def _pong_pkt(self):
        hdr = self._hdr(CMD_PONG)
        hn  = self.hostname.encode('ascii')[:HOSTNAME_LEN-1].ljust(HOSTNAME_LEN, b'\x00')
        nm  = b'Gyro Controller'.ljust(CHILD_NAME_LEN, b'\x00')
        ds  = b'Emulated gyro board'.ljust(CHILD_DESC_LEN, b'\x00')
        sc  = b'\x00'   # stringCount = 0
        strings = b'\x00' * (9 * MAX_STR)
        fw  = b'\x08\x04\x03'  # fwMajor=8 fwMinor=4 fwPatch=3
        return hdr + hn + nm + ds + sc + strings + fw

    def _orient_pkt(self):
        roll, pitch, yaw = self._angles()
        roll100  = max(-18000, min(18000, int(round(roll  * 100))))
        pitch100 = max(-18000, min(18000, int(round(pitch * 100))))
        yaw100   = max(-18000, min(18000, int(round(yaw   * 100))))
        flags = (0x01 if self._streaming else 0) | 0x02 | 0x04  # streaming+imuOk+wifiOk
        hdr = self._hdr(CMD_GYRO_ORIENT)
        pay = struct.pack("<hhhBB", roll100, pitch100, yaw100,
                          self._target_fps, flags)
        return hdr + pay

    # -- Socket I/O ------------------------------------------------------------

    def _send(self, pkt, dest_ip=None):
        if self._sock is None:
            return
        try:
            self._sock.sendto(pkt, (dest_ip or self.parent_ip, self.port))
        except OSError:
            pass

    def _handle(self, data, addr):
        if len(data) < 8:
            return
        magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        if magic != UDP_MAGIC:
            return

        if cmd == CMD_PING:
            self._send(self._pong_pkt(), addr[0])
            self.pong_sent += 1

        elif cmd == CMD_GYRO_CTRL and len(data) >= 10:
            enabled, fps = struct.unpack_from("<BB", data, 8)
            with self._lock:
                self._streaming = bool(enabled)
                if fps > 0:
                    self._target_fps = fps

        elif cmd == CMD_GYRO_RECAL:
            roll, pitch, yaw = self._angles()
            with self._lock:
                self._roll_ref  += roll
                self._pitch_ref += pitch
                self._yaw_ref   += yaw
            self.recal_count += 1

    # -- Background thread -----------------------------------------------------

    def _loop(self):
        last_orient = 0.0
        while self._running:
            # Receive
            try:
                self._sock.settimeout(0.02)
                data, addr = self._sock.recvfrom(256)
                self._handle(data, addr)
            except socket.timeout:
                pass
            except Exception:
                pass

            # Send ORIENT if streaming
            now = time.time()
            interval = 1.0 / max(1, self._target_fps)
            if self._streaming and (now - last_orient) >= interval:
                self._send(self._orient_pkt())
                self.orient_sent += 1
                last_orient = now

    # -- Public API ------------------------------------------------------------

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.bind_ip, self.port + 1))  # bind on PORT+1 to avoid collision
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        # Announce
        self._send(self._pong_pkt())
        self.pong_sent += 1

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._sock:
            self._sock.close()
            self._sock = None

    def enable(self, fps: int = 20):
        with self._lock:
            self._streaming  = True
            self._target_fps = fps

    def disable(self):
        with self._lock:
            self._streaming = False

    def is_streaming(self) -> bool:
        return self._streaming

    def wait_orient(self, count: int = 1, timeout: float = 3.0) -> bool:
        """Block until `count` ORIENT packets have been sent, or timeout."""
        start = time.time()
        initial = self.orient_sent
        while time.time() - start < timeout:
            if self.orient_sent - initial >= count:
                return True
            time.sleep(0.02)
        return False


# -- CLI entry point -----------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Emulated gyro board')
    parser.add_argument('--parent', default='127.0.0.1', help='Parent server IP')
    parser.add_argument('--port',   type=int, default=UDP_PORT, help='UDP port')
    parser.add_argument('--fps',    type=int, default=20, help='Orient stream fps')
    parser.add_argument('--stream', action='store_true', help='Start streaming immediately')
    args = parser.parse_args()

    gyro = EmulatedGyro(parent_ip=args.parent, port=args.port, fps=args.fps)
    gyro.start()
    if args.stream:
        gyro.enable(args.fps)

    print(f'[emulated_gyro] Running. Parent={args.parent}:{args.port} streaming={args.stream}')
    print('[emulated_gyro] Press Ctrl+C to stop.')
    try:
        while True:
            roll, pitch, yaw = gyro._angles()
            print(f'\r  R={roll:+7.2f}deg  P={pitch:+7.2f}deg  Y={yaw:+7.2f}deg  '
                  f'sent={gyro.orient_sent}  stream={gyro.is_streaming()}  ', end='', flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        gyro.stop()
        print('\n[emulated_gyro] Stopped.')
