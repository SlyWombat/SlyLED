#!/usr/bin/env python3
"""
SlyLED Emulated Children — 10 virtual performers for bake testing.

Each child runs a minimal HTTP server + UDP responder that implements
enough of the child protocol for the parent to discover, register,
and sync shows to them.

Usage: python tests/emulated_children.py [--base-port 9000]

Creates 10 children with varied string configurations:
  Child 0: 1 str East,  50 LEDs
  Child 1: 1 str West, 100 LEDs
  Child 2: 1 str North, 150 LEDs
  Child 3: 1 str South,  75 LEDs
  Child 4: 2 str East+West, 100+100 LEDs
  Child 5: 2 str North+South, 60+60 LEDs
  Child 6: 3 str East+North+West, 50+50+50 LEDs
  Child 7: 1 str East, 200 LEDs (long string)
  Child 8: 2 str East+East, 150+150 LEDs (folded)
  Child 9: 4 str E+N+W+S, 30+30+30+30 LEDs
"""

import argparse
import json
import socket
import struct
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

UDP_MAGIC = 0x534C
UDP_VERSION = 4
CMD_PING = 0x01
CMD_PONG = 0x02
CMD_LOAD_STEP = 0x20
CMD_LOAD_ACK = 0x21
CMD_RUNNER_GO = 0x30
CMD_RUNNER_STOP = 0x31
CMD_ACTION = 0x10
CMD_ACTION_STOP = 0x11
CMD_SET_BRIGHTNESS = 0x22
CMD_STATUS_REQ = 0x40
CMD_STATUS_RESP = 0x41

CHILDREN_CONFIG = [
    {"name": "Emu-East50",       "strings": [{"leds": 50,  "mm": 800,  "sdir": 0}]},
    {"name": "Emu-West100",      "strings": [{"leds": 100, "mm": 1600, "sdir": 2}]},
    {"name": "Emu-North150",     "strings": [{"leds": 150, "mm": 2400, "sdir": 1}]},
    {"name": "Emu-South75",      "strings": [{"leds": 75,  "mm": 1200, "sdir": 3}]},
    {"name": "Emu-DualEW",       "strings": [{"leds": 100, "mm": 1600, "sdir": 0}, {"leds": 100, "mm": 1600, "sdir": 2}]},
    {"name": "Emu-DualNS",       "strings": [{"leds": 60,  "mm": 960,  "sdir": 1}, {"leds": 60,  "mm": 960,  "sdir": 3}]},
    {"name": "Emu-TriENW",       "strings": [{"leds": 50,  "mm": 800,  "sdir": 0}, {"leds": 50,  "mm": 800,  "sdir": 1}, {"leds": 50, "mm": 800, "sdir": 2}]},
    {"name": "Emu-Long200",      "strings": [{"leds": 200, "mm": 3200, "sdir": 0}]},
    {"name": "Emu-Folded",       "strings": [{"leds": 150, "mm": 2400, "sdir": 0, "folded": True}, {"leds": 150, "mm": 2400, "sdir": 0}]},
    {"name": "Emu-Quad",         "strings": [{"leds": 30,  "mm": 480,  "sdir": 0}, {"leds": 30,  "mm": 480,  "sdir": 1}, {"leds": 30, "mm": 480, "sdir": 2}, {"leds": 30, "mm": 480, "sdir": 3}]},
    {"name": "Emu-DMXBridge",   "strings": [], "boardType": "dmx"},
]


class ChildState:
    def __init__(self, idx, cfg, http_port):
        self.idx = idx
        self.cfg = cfg
        self.name = cfg["name"]
        self.hostname = f"EMU-{idx:04X}"
        self.http_port = http_port
        self.strings = cfg["strings"]
        self.sc = len(self.strings)
        self.steps_loaded = 0
        self.running = False
        self.current_step = 0
        self.brightness = 255
        self.action_type = 0
        self.start_time = 0


def build_pong(child, src_ip="127.0.0.1"):
    """Build a PONG response packet."""
    hdr = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_PONG, int(time.time()) & 0xFFFFFFFF)

    hostname = child.hostname.encode()[:10].ljust(10, b'\x00')
    altname = child.name.encode()[:16].ljust(16, b'\x00')
    desc = b'\x00' * 32
    sc = child.sc

    strings_data = b''
    for i in range(8):
        if i < sc:
            s = child.strings[i]
            leds = s.get("leds", 0)
            mm = s.get("mm", 0)
            led_type = 0
            cable_dir = 1 if s.get("folded") else 0
            cable_mm = 0
            strip_dir = s.get("sdir", 0)
            strings_data += struct.pack("<HHBBHB", leds, mm, led_type, cable_dir, cable_mm, strip_dir)
        else:
            strings_data += struct.pack("<HHBBHB", 0, 0, 0, 0, 0, 0)

    fw_major, fw_minor, fw_patch = 7, 3, 0
    payload = hostname + altname + desc + bytes([sc]) + strings_data + bytes([fw_major, fw_minor, fw_patch])
    return hdr + payload


class ChildHTTPHandler(BaseHTTPRequestHandler):
    child = None

    def log_message(self, fmt, *args):
        pass  # silence

    def do_GET(self):
        if self.path == "/status":
            bt = self.child.cfg.get("boardType", "led")
            board = "dmx-bridge" if bt == "dmx" else "emulated"
            data = {
                "role": "child",
                "hostname": self.child.hostname,
                "board": board,
                "boardType": bt,
                "version": "7.3.0",
                "action": self.child.action_type,
                "udpRx": 0,
                "freeHeap": 200000,
                "uptime": int(time.time() - self.child.start_time),
                "rssi": -45,
                "sc": self.child.sc,
            }
            self._json(data)
        elif self.path == "/config":
            self._json({"ok": True})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/reboot":
            self._json({"ok": True})
        elif self.path == "/ota":
            self._json({"ok": True})
        else:
            self._json({"ok": True})

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_udp_listener(children, udp_port=4210):
    """Listen for parent UDP packets and respond."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", udp_port))
    except OSError:
        print(f"[UDP] Port {udp_port} busy — emulated children won't respond to UDP pings")
        return
    sock.settimeout(1.0)
    print(f"[UDP] Listening on port {udp_port}")

    while True:
        try:
            data, addr = sock.recvfrom(256)
        except socket.timeout:
            continue
        except Exception:
            continue
        if len(data) < 8:
            continue
        magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        if magic != UDP_MAGIC:
            continue

        if cmd == CMD_PING:
            # Respond with PONG from all children
            for child in children:
                pong = build_pong(child)
                sock.sendto(pong, addr)

        elif cmd == CMD_LOAD_STEP:
            # ACK silently
            pass

        elif cmd == CMD_RUNNER_GO:
            for child in children:
                child.running = True

        elif cmd == CMD_RUNNER_STOP or cmd == CMD_ACTION_STOP:
            for child in children:
                child.running = False
                child.action_type = 0

        elif cmd == CMD_STATUS_REQ:
            # Respond with status
            hdr = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_STATUS_RESP, int(time.time()) & 0xFFFFFFFF)
            for child in children:
                payload = struct.pack("<BBBBI",
                    child.action_type, 1 if child.running else 0,
                    child.current_step, 45, int(time.time() - child.start_time))
                sock.sendto(hdr + payload, addr)


def main():
    parser = argparse.ArgumentParser(description="SlyLED Emulated Children")
    parser.add_argument("--base-port", type=int, default=9000, help="Base HTTP port (children use 9000-9009)")
    parser.add_argument("--count", type=int, default=10, help="Number of children to emulate")
    args = parser.parse_args()

    children = []
    servers = []

    for i in range(min(args.count, len(CHILDREN_CONFIG))):
        cfg = CHILDREN_CONFIG[i]
        port = args.base_port + i
        child = ChildState(i, cfg, port)
        child.start_time = time.time()
        children.append(child)

        # Create HTTP handler class with child reference
        handler = type(f"Handler{i}", (ChildHTTPHandler,), {"child": child})
        server = HTTPServer(("0.0.0.0", port), handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        servers.append(server)
        total_leds = sum(s.get("leds", 0) for s in cfg["strings"])
        dirs = {0: "E", 1: "N", 2: "W", 3: "S"}
        str_desc = " + ".join(f"{s['leds']}{dirs[s['sdir']]}" for s in cfg["strings"])
        print(f"  [{i}] {cfg['name']:16} HTTP :{port}  {child.sc} str  {str_desc}  ({total_leds} LEDs)")

    # UDP listener
    udp_thread = threading.Thread(target=run_udp_listener, args=(children,), daemon=True)
    udp_thread.start()

    print(f"\n{len(children)} emulated children running. Press Ctrl+C to stop.\n")
    print("To register with parent:")
    for child in children:
        print(f"  curl -X POST http://localhost:8080/api/children -H 'Content-Type: application/json' -d '{{\"ip\":\"127.0.0.1:{child.http_port}\"}}'")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping emulated children...")
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
