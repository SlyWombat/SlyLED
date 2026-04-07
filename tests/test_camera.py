#!/usr/bin/env python3
"""
test_camera.py — Test suite for the SlyLED camera node firmware.

Usage:
    python tests/test_camera.py [host] [http_port] [udp_port]

If host is omitted, broadcasts a UDP PING and auto-discovers the first
camera node on the network.

Defaults: http_port=5000  udp_port=4210
"""

import json
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

# ── Configuration ─────────────────────────────────────────────────────────────

_arg_host = sys.argv[1] if len(sys.argv) > 1 else None
HTTP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
UDP_PORT  = int(sys.argv[3]) if len(sys.argv) > 3 else 4210

UDP_MAGIC   = 0x534C
UDP_VERSION = 4
CMD_PING       = 0x01
CMD_PONG       = 0x02
CMD_STATUS_REQ = 0x40
CMD_STATUS_RESP = 0x41

# Auto-discover if no host given
if _arg_host is None:
    print("No host specified - broadcasting UDP PING to discover camera nodes...")
    _disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _disc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    _disc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _disc.settimeout(0.3)
    _disc.bind(("", UDP_PORT))
    _ping = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_PING, int(time.time()) & 0xFFFFFFFF)
    _disc.sendto(_ping, ("255.255.255.255", UDP_PORT))
    _found = {}
    _deadline = time.time() + 3.0
    while time.time() < _deadline:
        try:
            _data, (_ip, _) = _disc.recvfrom(512)
            if len(_data) >= 142:
                _magic, _ver, _cmd, _ = struct.unpack_from("<HBBI", _data, 0)
                if _magic == UDP_MAGIC and _cmd == CMD_PONG:
                    _hn = _data[8:18].rstrip(b'\x00').decode("ascii", errors="replace")
                    # Check if it's a camera by probing HTTP /status
                    try:
                        _resp = urllib.request.urlopen(f"http://{_ip}:{HTTP_PORT}/status", timeout=2)
                        _info = json.loads(_resp.read())
                        if _info.get("role") == "camera" and _ip not in _found:
                            _found[_ip] = _hn
                    except Exception:
                        pass
        except socket.timeout:
            pass
    _disc.close()
    if not _found:
        print("  No camera nodes found via broadcast. Pass IP as first argument.")
        sys.exit(2)
    HOST = list(_found.keys())[0]
    print(f"  Found camera: {_found[HOST]} at {HOST}")
else:
    HOST = _arg_host

BASE = f"http://{HOST}:{HTTP_PORT}"

# ── Test framework ────────────────────────────────────────────────────────────

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def get(path, timeout=5):
    return urllib.request.urlopen(f"{BASE}{path}", timeout=timeout)

def post_json(path, data, timeout=5):
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)

def get_json(path, timeout=5):
    resp = get(path, timeout)
    return json.loads(resp.read().decode())

# ── Tests ─────────────────────────────────────────────────────────────────────

def run():
    print(f"\nTesting camera node at {HOST}:{HTTP_PORT} (UDP {UDP_PORT})\n")

    # ── Health ─────────────────────────────────────────────────────
    r = get("/health")
    ok("GET /health", r.status == 200)

    # ── Status ─────────────────────────────────────────────────────
    d = get_json("/status")
    ok("GET /status returns JSON", isinstance(d, dict))
    ok("Status role is camera", d.get("role") == "camera")
    ok("Status has hostname", bool(d.get("hostname")))
    ok("Status has fwVersion", bool(d.get("fwVersion")))
    ok("Status has board", bool(d.get("board")))
    ok("Status has fovDeg", isinstance(d.get("fovDeg"), (int, float)))
    ok("Status has cameraCount", isinstance(d.get("cameraCount"), int))
    ok("Status has cameras array", isinstance(d.get("cameras"), list))
    ok("Status cameraCount matches array",
       d.get("cameraCount") == len(d.get("cameras", [])))
    ok("Status has capabilities", isinstance(d.get("capabilities"), dict))

    cam_count = d.get("cameraCount", 0)
    cameras = d.get("cameras", [])
    hostname = d.get("hostname", "")
    fw_version = d.get("fwVersion", "")
    print(f"  Hostname: {hostname}, FW: {fw_version}, Cameras: {cam_count}")

    # ── Camera array validation ────────────────────────────────────
    if cam_count > 0:
        cam0 = cameras[0]
        ok("Camera has device field", "device" in cam0)
        ok("Camera has name field", "name" in cam0)
        ok("Camera device starts with /dev/video", cam0["device"].startswith("/dev/video"))
        ok("No SoC cameras in list",
           all("sunxi" not in c.get("name", "").lower() for c in cameras),
           "sunxi-vin should be filtered")
        for i, c in enumerate(cameras):
            print(f"    [{i}] {c['device']}: {c['name']} ({c.get('resW', 0)}x{c.get('resH', 0)})")
    else:
        ok("Camera has device field", True, "SKIP: no cameras")
        ok("Camera has name field", True, "SKIP: no cameras")
        ok("Camera device starts with /dev/video", True, "SKIP: no cameras")
        ok("No SoC cameras in list", True, "SKIP: no cameras")

    # ── Config page ────────────────────────────────────────────────
    r = get("/config")
    ok("GET /config returns HTML", r.status == 200)
    html = r.read().decode()
    ok("Config page has SlyLED Camera title", "SlyLED Camera" in html)
    ok("Config page has Capture Frame button", "Capture Frame" in html)
    ok("Config page has Settings tab", "Settings" in html)
    ok("Config page has Dashboard tab", "Dashboard" in html)

    # ── Config JSON ────────────────────────────────────────────────
    d = get_json("/config/json")
    ok("GET /config/json", isinstance(d, dict))
    ok("Config has hostname", "hostname" in d)
    ok("Config has fovDeg", "fovDeg" in d)

    # ── Config update ──────────────────────────────────────────────
    orig_name = d.get("hostname", "")

    # Set a test name
    r = post_json("/config", {"hostname": "TestCam99"})
    rd = json.loads(r.read().decode())
    ok("POST /config ok", rd.get("ok") is True)

    d2 = get_json("/config/json")
    ok("Config hostname updated", d2.get("hostname") == "TestCam99")

    # Restore original name
    post_json("/config", {"hostname": orig_name})
    d3 = get_json("/config/json")
    ok("Config hostname restored", d3.get("hostname") == orig_name)

    # ── Snapshot ───────────────────────────────────────────────────
    if cam_count > 0:
        # Test each camera
        for i in range(cam_count):
            try:
                r = get(f"/snapshot?cam={i}", timeout=15)
                data = r.read()
                is_jpeg = len(data) > 100 and data[:2] == b'\xff\xd8'
                ok(f"Snapshot cam={i} returns JPEG", is_jpeg,
                   f"{len(data)} bytes")
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                ok(f"Snapshot cam={i} returns JPEG", False, body[:100])

        # Out of range camera index
        try:
            get(f"/snapshot?cam={cam_count + 10}")
            ok("Snapshot invalid cam returns 400", False, "Expected error")
        except urllib.error.HTTPError as e:
            ok("Snapshot invalid cam returns 400", e.code == 400)

        # Negative index
        try:
            get("/snapshot?cam=-1")
            ok("Snapshot cam=-1 returns 400", False, "Expected error")
        except urllib.error.HTTPError as e:
            ok("Snapshot cam=-1 returns 400", e.code == 400)
    else:
        # No cameras — snapshot should 404
        try:
            get("/snapshot?cam=0")
            ok("Snapshot no camera returns 404", False, "Expected error")
        except urllib.error.HTTPError as e:
            ok("Snapshot no camera returns 404", e.code == 404)

    # ── UDP PING/PONG ──────────────────────────────────────────────
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", UDP_PORT))
    s.settimeout(3)

    pkt = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_PING,
                      int(time.time()) & 0xFFFFFFFF)
    s.sendto(pkt, (HOST, UDP_PORT))
    try:
        data, addr = s.recvfrom(512)
        ok("UDP PONG received", len(data) >= 142, f"{len(data)} bytes")

        magic, ver, cmd, epoch = struct.unpack_from("<HBBI", data, 0)
        ok("PONG magic correct", magic == UDP_MAGIC)
        ok("PONG version correct", ver == UDP_VERSION)
        ok("PONG cmd correct", cmd == CMD_PONG)

        # Parse payload
        p = data[8:]
        hn = p[0:10].rstrip(b'\x00').decode("ascii", "replace")
        alt = p[10:26].rstrip(b'\x00').decode("ascii", "replace")
        desc = p[26:58].rstrip(b'\x00').decode("ascii", "replace")
        sc = p[58]
        ok("PONG hostname matches", hn == hostname[:10],
           f"got '{hn}' expected '{hostname[:10]}'")
        ok("PONG altName populated", len(alt) > 0)
        ok("PONG description is Camera node", "Camera" in desc or "camera" in desc)
        ok("PONG stringCount = camera count", sc == cam_count,
           f"got {sc} expected {cam_count}")

        # Firmware version from PONG
        if len(p) >= 134:
            fw_maj, fw_min, fw_patch = p[131], p[132], p[133]
            pong_ver = f"{fw_maj}.{fw_min}.{fw_patch}"
            ok("PONG firmware version matches", pong_ver == fw_version,
               f"got {pong_ver} expected {fw_version}")
        else:
            ok("PONG firmware version matches", False, "payload too short")
    except socket.timeout:
        ok("UDP PONG received", False, "timeout")
        for name in ["PONG magic correct", "PONG version correct", "PONG cmd correct",
                     "PONG hostname matches", "PONG altName populated",
                     "PONG description is Camera node", "PONG stringCount = camera count",
                     "PONG firmware version matches"]:
            ok(name, False, "skipped (no PONG)")

    # ── UDP STATUS_REQ/STATUS_RESP ─────────────────────────────────
    pkt = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_STATUS_REQ,
                      int(time.time()) & 0xFFFFFFFF)
    s.sendto(pkt, (HOST, UDP_PORT))
    try:
        data, addr = s.recvfrom(512)
        ok("UDP STATUS_RESP received", len(data) >= 16, f"{len(data)} bytes")

        magic, ver, cmd, _ = struct.unpack_from("<HBBI", data, 0)
        ok("STATUS_RESP magic correct", magic == UDP_MAGIC)
        ok("STATUS_RESP cmd correct", cmd == CMD_STATUS_RESP)

        if len(data) >= 16:
            act, run, step, rssi, uptime = struct.unpack_from("<BBBBI", data, 8)
            ok("STATUS_RESP uptime > 0", uptime > 0, f"uptime={uptime}s")
            ok("STATUS_RESP activeAction is 0", act == 0, "camera has no actions")
    except socket.timeout:
        ok("UDP STATUS_RESP received", False, "timeout")
        for name in ["STATUS_RESP magic correct", "STATUS_RESP cmd correct",
                     "STATUS_RESP uptime > 0", "STATUS_RESP activeAction is 0"]:
            ok(name, False, "skipped (no response)")

    s.close()

    # ── Bad PING (wrong magic) ─────────────────────────────────────
    s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s2.bind(("", UDP_PORT))
    s2.settimeout(1)
    bad_pkt = struct.pack("<HBBI", 0xDEAD, UDP_VERSION, CMD_PING, 0)
    s2.sendto(bad_pkt, (HOST, UDP_PORT))
    try:
        s2.recvfrom(512)
        ok("Bad magic ignored", False, "got unexpected response")
    except socket.timeout:
        ok("Bad magic ignored", True)
    s2.close()

    # ── Print results ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    for name, cond, detail in results:
        mark = '\u2705' if cond else '\u274c'
        line = f"  {mark} {name}"
        if detail:
            line += f"  ({detail})"
        print(line)
    print(f"\n{passed} passed, {failed} failed, {len(results)} total")
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    run()
