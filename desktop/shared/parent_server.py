#!/usr/bin/env python3
"""
SlyLED Parent Server   " Windows / Mac desktop parent application.

Replaces the Arduino Giga R1 as the full-featured parent.  Manages layout,
timelines, spatial effects, and DMX output.

Usage (from project root):
    pip install -r desktop/windows/requirements.txt
    python desktop/shared/parent_server.py [--port 8080] [--no-browser]
"""

import argparse
import json
import math
import os
try:
    import numpy as np
except ImportError:
    np = None
import signal
import socket
import struct
import sys
import threading
import time
import webbrowser
from pathlib import Path

import io
from flask import Flask, abort, jsonify, request, send_file, send_from_directory
import flask.cli
flask.cli.show_server_banner = lambda *a, **kw: None   # suppress dev-server warning (#289)
import logging
from datetime import datetime

from wled_bridge import (wled_probe, wled_stop,
                         wled_get_effects, wled_get_palettes, wled_get_segments)
from spatial_engine import (catmull_rom_sample, resolve_fixture,
                            evaluate_spatial_effect, blend_pixel_layers,
                            compute_pan_tilt)
from bake_engine import (bake_timeline, pack_lsq_zip, segments_to_load_steps,
                         BakeProgress)
from dmx_profiles import ProfileLibrary
from dmx_artnet import ArtNetEngine
from dmx_sacn import sACNEngine

log = logging.getLogger("slyled")
log.setLevel(logging.DEBUG)
_log_handler = None   # file handler, created/removed by _apply_logging()

def _apply_logging(enabled, log_path=None):
    """Enable/disable file logging.  Optionally set custom log file path."""
    global _log_handler
    # Remove existing file handler
    if _log_handler:
        log.removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None
    if enabled:
        if log_path:
            log_file = Path(log_path)
            # If path is a directory (or has no extension), treat as directory and add filename
            if log_file.is_dir() or (log_file.suffix == '' and not log_file.name.endswith('.log')):
                log_file.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = log_file / f"slyled_{ts}.log"
            else:
                log_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            log_dir = DATA / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"slyled_{ts}.log"
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(fh)
        _log_handler = fh
        log.info("Logging started -> %s", fh.baseFilename)

#  "  "  Version  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

VERSION = "1.4.22"

#  "  "  UDP protocol  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

UDP_MAGIC   = 0x534C
UDP_VERSION = 4
UDP_PORT    = 4210

CMD_PING        = 0x01
CMD_PONG        = 0x02
CMD_ACTION      = 0x10
CMD_ACTION_STOP = 0x11
CMD_LOAD_STEP       = 0x20
CMD_LOAD_ACK        = 0x21
CMD_SET_BRIGHTNESS  = 0x22
CMD_RUNNER_GO       = 0x30
CMD_RUNNER_STOP = 0x31
CMD_ACTION_EVENT = 0x12
CMD_STATUS_REQ  = 0x40
CMD_STATUS_RESP = 0x41

#  "  "  Paths  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

BASE = Path(__file__).parent

# When packaged with PyInstaller --onefile, files land in sys._MEIPASS
if getattr(sys, "frozen", False):
    SPA = Path(sys._MEIPASS) / "spa"
else:
    SPA = BASE / "spa"

# Persist data under %APPDATA%\SlyLED on Windows; fall back to BASE/data elsewhere
if os.name == "nt" and os.environ.get("APPDATA"):
    DATA = Path(os.environ["APPDATA"]) / "SlyLED" / "data"
else:
    DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)

#  "  "  Persistence  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _load(name, default):
    p = DATA / f"{name}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:
        return default

def _save(name, obj):
    (DATA / f"{name}.json").write_text(json.dumps(obj, indent=2))

#  "  "  In-memory state  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_children = _load("children", [])
# Reset all children to offline on startup   " ping sweep will restore responsive ones
for _c in _children:
    _c["status"] = 0
_settings = _load("settings", {
    "name": "SlyLED", "units": 0, "canvasW": 3000, "canvasH": 2000,
    "darkMode": 1, "runnerRunning": False, "runnerElapsed": 0,
    "runnerLoop": True,
})
_layout  = _load("layout",  {"canvasW": 3000, "canvasH": 2000, "children": []})
_stage   = _load("stage",   {"w": 3.0, "h": 2.0, "d": 1.5})
_fixtures   = _load("fixtures",   [])

#  "  "  Fixture migration: backfill fixtureType on old data  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_fix_patched = False
for _f in _fixtures:
    if "fixtureType" not in _f:
        _f["fixtureType"] = "led"
        _fix_patched = True
    # Migrate aimPoint → rotation (one-time conversion)
    if _f.get("aimPoint") and (not _f.get("rotation") or _f["rotation"] == [0, 0, 0]):
        _ap = _f["aimPoint"]
        _fx = _f.get("x", 0) or 0
        _fy = _f.get("y", 0) or 0
        _fz = _f.get("z", 0) or 0
        _dx, _dy, _dz = _ap[0] - _fx, _ap[1] - _fy, _ap[2] - _fz
        _hdist = math.sqrt(_dx * _dx + _dy * _dy)  # floor plane = XY (Z=height)
        if _hdist > 0.001 or abs(_dz) > 0.001:
            _f["rotation"] = [
                round(-math.atan2(_dz, _hdist) * 180 / math.pi, 2),  # tilt (pitch)
                round(math.atan2(_dx, _dy) * 180 / math.pi, 2),       # pan (yaw)
                0
            ]
        del _f["aimPoint"]
        _fix_patched = True
    if _f.get("fixtureType") == "dmx" and "rotation" not in _f:
        _f["rotation"] = [0, 0, 0]
        _fix_patched = True
    if _f.get("fixtureType") == "camera":
        if "rotation" not in _f:
            _f["rotation"] = [0, 0, 0]
            _fix_patched = True
        if "fovDeg" not in _f:
            _f["fovDeg"] = 60
            _fix_patched = True
if _fix_patched:
    _save("fixtures", _fixtures)
del _fix_patched

def _rotation_to_aim(rotation, pos, dist=3000):
    """Convert rotation [rx, ry, rz] (degrees) + position to an aim point [x,y,z].

    rx = tilt/pitch, ry = pan/yaw.  Default distance is 3000mm (3m).
    Stage coordinates: X=width, Y=depth (forward), Z=height (up).
    """
    rx = rotation[0] if rotation else 0
    ry = rotation[1] if rotation and len(rotation) > 1 else 0
    pan_rad = math.radians(ry)
    tilt_rad = math.radians(rx)
    dx = math.sin(pan_rad) * math.cos(tilt_rad) * dist
    dy = math.cos(pan_rad) * math.cos(tilt_rad) * dist   # Y = depth (forward)
    dz = -math.sin(tilt_rad) * dist                       # Z = height (up)
    return [pos[0] + dx, pos[1] + dy, pos[2] + dz]

_objects    = _load("objects",     [])
_spatial_fx = _load("spatial_fx", [])
_timelines  = _load("timelines",  [])
_show_playlist = _load("show_playlist", {"order": [], "loopAll": False})  # {order: [tid,...], loopAll: bool}
_actions = _load("actions", [])
_wifi    = _load("wifi",    {"ssid": "", "password": ""})
_ssh     = _load("ssh",    {"sshUser": "root", "sshPassword": "", "sshKeyPath": ""})
_camera_ssh = _load("camera_ssh", {})  # {ip: {authType, user, password(encrypted), keyPath, keyStored}}
_calibrations = _load("calibrations", {})  # {fixtureId_str: {matrix, error, points, timestamp}}
_range_cal    = _load("range_calibrations", {})  # {fixtureId_str: {pan, tilt, timestamp}}
_mover_cal    = _load("mover_calibrations", {})  # {fixtureId_str: {grid, samples, ...}}
_ssh_bootstrapped = False  # deferred pre-population (needs _encrypt_pw defined later)

# Live action events pushed by children (ip  -' {actionType, stepIndex, totalSteps, event, ts})
_live_events = {}

# Recent PONGs seen by UDP listener (ip  -' parsed pong info)   " used by discover
_recent_pongs = {}

# Bake state (Phase 5)
_bake_progress = None   # BakeProgress instance while baking
_bake_result = {}       # timeline_id  -' bake result dict

# Apply logging from saved settings on startup
_apply_logging(_settings.get("logging", False))

_nxt_c = max((c["id"] for c in _children), default=-1) + 1
_nxt_a = max((a["id"] for a in _actions),  default=-1) + 1
_nxt_fix = max((f["id"] for f in _fixtures),   default=-1) + 1
_nxt_obj = max((f["id"] for f in _objects),    default=-1) + 1
_temporal_objects = []  # in-memory only, never saved
_nxt_tmp = 10000       # temporal IDs start at 10000 to avoid collision
_nxt_sfx = max((f["id"] for f in _spatial_fx),  default=-1) + 1
_nxt_tl  = max((t["id"] for t in _timelines),  default=-1) + 1
_lock  = threading.Lock()

#  "  "  DMX subsystems  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_profile_lib = ProfileLibrary(data_dir=str(DATA))
_artnet = ArtNetEngine()
_sacn = sACNEngine()

#  "  "  UDP helpers  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _hdr(cmd, epoch=0):
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd,
                       epoch or (int(time.time()) & 0xFFFFFFFF))

def _send(ip, pkt):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(pkt, (ip, UDP_PORT))
    except Exception:
        pass

def _local_broadcasts():
    """Return subnet-directed broadcast addresses for all non-loopback interfaces."""
    bcs = []
    for prefix in _local_subnet_prefixes():
        bc = prefix + ".255"
        if bc not in bcs:
            bcs.append(bc)
    return bcs

def _local_subnet_prefixes():
    """Return /24 subnet prefixes (e.g. '192.168.10') for all non-loopback interfaces."""
    prefixes = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            prefix = ip.rsplit(".", 1)[0]
            if prefix not in prefixes:
                prefixes.append(prefix)
    except Exception:
        pass
    # Fallback: WSL2 / single-NIC hosts where getaddrinfo only returns loopback
    if not prefixes:
        try:
            prefix = _get_local_ip().rsplit(".", 1)[0]
            if prefix:
                prefixes.append(prefix)
        except Exception:
            pass
    return prefixes

def _send_recv(ip, pkt, timeout=1.5, maxb=256):
    """Send UDP packet and wait for reply from the specified IP only.
    Binds to UDP_PORT (with SO_REUSEADDR) so the child replies to the
    firewall-allowed port 4210.  Falls back to an ephemeral port if 4210
    is momentarily busy.  Discards packets from other sources.
    """
    for bind_port in (UDP_PORT, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(timeout)
                s.bind(("", bind_port))
                s.sendto(pkt, (ip, UDP_PORT))
                deadline = time.time() + timeout
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    s.settimeout(remaining)
                    data, addr = s.recvfrom(maxb)
                    if addr[0] == ip:
                        return data
                    # else: discard stale packet from different source
        except OSError:
            if bind_port == 0:
                return None   # ephemeral port also failed
            continue          # port 4210 busy   " retry with ephemeral
        except Exception:
            return None
    return None

def _parse_pong(data, src_ip):
    # PONG v4: 8-byte header + 133-byte PongPayload = 141 bytes (v3: 139 bytes)
    # PongPayload: hostname[10]+altName[16]+desc[32]+stringCount(1)+PongString[8] --9+fwMajor(1)+fwMinor(1)
    if not data or len(data) < 139:  # backward compat: accept v3 (139) and v4 (141)
        return None
    if data[3] != CMD_PONG:
        return None
    p  = data[8:]
    hn = p[0:10].rstrip(b"\x00").decode("ascii", "replace")
    nm = p[10:26].rstrip(b"\x00").decode("ascii", "replace")
    ds = p[26:58].rstrip(b"\x00").decode("ascii", "replace")
    sc = p[58]
    strings = []
    off = 59
    for _ in range(8):
        leds, mm, tp, cd, cm, sd = struct.unpack_from("<HHBBHB", p, off)
        strings.append({"leds": leds, "mm": mm, "type": tp,
                         "cdir": cd, "cmm": cm, "sdir": sd,
                         "folded": bool(cd & 0x01)})
        off += 9
    # Firmware version: v4.0 added fwMajor+fwMinor (141 bytes), v5.3.6+ adds fwPatch (142 bytes)
    fw_ver = None
    if len(data) >= 142:
        fw_ver = f"{p[131]}.{p[132]}.{p[133]}"
    elif len(data) >= 141:
        fw_ver = f"{p[131]}.{p[132]}"
    return {
        "hostname": hn, "name": nm or hn, "desc": ds, "sc": sc,
        "strings": strings, "ip": src_ip,
        "status": 1, "seen": int(time.time()),
        "fwVersion": fw_ver,
    }

def _probe_board_type(child):
    """Fetch board type, version, and telemetry from child's HTTP /status endpoint."""
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://{child['ip']}/status", method="GET")
        resp = _ur.urlopen(req, timeout=2)
        data = json.loads(resp.read().decode("utf-8"))
        board = data.get("board")
        if board:
            board_map = {"esp32": "ESP32", "d1mini": "D1 Mini", "giga-child": "Giga",
                         "dmx-bridge": "DMX Bridge"}
            child["boardType"] = board_map.get(board, board)
        # Detect DMX bridge from boardType field in /status
        bt = data.get("boardType")
        if bt == "dmx":
            child["type"] = "dmx"
        # Full version from /status (3-part: 5.3.2) overrides PONG's 2-part version
        version = data.get("version")
        if version:
            child["fwVersion"] = version
        # Extended telemetry
        for key in ("rssi", "chipModel", "chipTemp", "flashSize", "freeHeap",
                     "sdkVersion", "uptime"):
            if key in data:
                child[key] = data[key]
    except Exception:
        pass

def _ping(child, retries=2):
    """Send CMD_PING and update child from PONG response.
    Retries up to `retries` times on timeout before marking offline.
    """
    pkt = _hdr(CMD_PING)
    for _ in range(retries + 1):
        resp = _send_recv(child["ip"], pkt)
        info = _parse_pong(resp, child["ip"])
        if info:
            # Don't let PONG's 2-digit fwVersion overwrite a more detailed 3-digit version
            saved_fw = child.get("fwVersion", "")
            child.update({k: v for k, v in info.items() if k != "id"})
            if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                child["fwVersion"] = saved_fw
            # Always probe for full telemetry (version, board type, RSSI, etc.)
            _probe_board_type(child)
            return True
    child["status"] = 0
    return False

def _broadcast_ping_all():
    """Send broadcast PINGs + direct pings to all known children.
    The UDP listener daemon handles incoming PONGs  -' _recent_pongs."""
    pkt = _hdr(CMD_PING)
    for c in list(_children):
        _send(c["ip"], pkt)
    for bc in ["255.255.255.255"] + _local_broadcasts():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(pkt, (bc, UDP_PORT))
        except Exception:
            pass

def _discover_all():
    """Broadcast PING, wait for listener to collect PONGs, return all by hostname."""
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    return {info.get("hostname"): info for ip, info in _recent_pongs.items()
            if info.get("hostname")}

def _discover():
    """Broadcast PING, wait for listener to collect PONGs, return unknown devices.
    Includes LED performers, DMX bridges, and camera nodes."""
    known_ips = {c["ip"] for c in _children}
    known_hosts = {c.get("hostname") for c in _children}
    known_cam_ips = {f.get("cameraIp") for f in _fixtures
                     if f.get("fixtureType") == "camera" and f.get("cameraIp")}
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    results = []
    pong_ips = set()
    for ip, info in _recent_pongs.items():
        pong_ips.add(ip)
        if ip in known_cam_ips:
            continue
        if ip in known_ips or info.get("hostname") in known_hosts:
            continue
        # Probe /status to detect board type — try port 80 (performers), then 5000 (cameras)
        import urllib.request as _ur
        board_type = "slyled"
        for probe_port in (80, 5000):
            try:
                resp = _ur.urlopen(f"http://{ip}:{probe_port}/status", timeout=2)
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("role") == "camera":
                    board_type = "camera"
                    # Enrich with camera-specific fields
                    info.update({
                        "fovDeg": data.get("fovDeg"),
                        "resolutionW": data.get("resolutionW"),
                        "resolutionH": data.get("resolutionH"),
                        "cameraUrl": data.get("cameraUrl", ""),
                    })
                    break
                board_type = data.get("boardType", "slyled")
                break
            except Exception:
                continue
        info["boardType"] = board_type
        results.append(info)
    return results

#  "  "  Async discover / refresh state  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_discover_state = {"pending": False, "data": []}
_refresh_state  = {"pending": False, "data": {}}

def _discover_bg():
    """Run _discover() in background, store results."""
    try:
        _discover_state["data"] = _discover()
    finally:
        _discover_state["pending"] = False

def _refresh_bg():
    """Run refresh-all logic in background, store results."""
    try:
        _recent_pongs.clear()
        _broadcast_ping_all()
        time.sleep(2.5)
        responded_ips = set(_recent_pongs.keys())
        responded_hostnames = {info.get("hostname") for info in _recent_pongs.values()}
        for c in _children:
            if c.get("type") == "wled":
                wled_info = wled_probe(c["ip"], timeout=2.0)
                if wled_info:
                    c["status"] = 1
                    c["seen"] = int(time.time())
                else:
                    c["status"] = 0
            elif c["ip"] in responded_ips or c.get("hostname") in responded_hostnames:
                for ip, info in _recent_pongs.items():
                    if info.get("hostname") == c.get("hostname"):
                        if ip != c["ip"]:
                            c["ip"] = ip
                        c.update({k: v for k, v in info.items() if k != "id"})
                        break
            else:
                c["status"] = 0
        with _lock:
            _save("children", _children)
        online = sum(1 for c in _children if c.get("status") == 1)
        _refresh_state["data"] = {"ok": True, "total": len(_children), "online": online}
    finally:
        _refresh_state["pending"] = False

def _child_led_ranges(child):
    """Build ledStart[8] / ledEnd[8] as uint16 arrays from child's string config.
    ESP32 multi-string: strings are concatenated in one leds[] array,
    so string N starts at the sum of all previous string lengths.
    For unconfigured strings: 0xFFFF (sentinel)."""
    ls = [0xFFFF] * 8
    le = [0xFFFF] * 8
    sc = child.get("sc", 0)
    strings = child.get("strings", [])
    offset = 0
    for j in range(min(sc, len(strings), 8)):
        leds = strings[j].get("leds", 0)
        if leds > 0:
            ls[j] = offset
            le[j] = offset + leds - 1
            offset += leds
    return struct.pack("<8H", *ls), struct.pack("<8H", *le)

def _act_params(act):
    """Extract generic param fields from an action dict, all coerced to int."""
    t = act.get("type", 0)
    r, g, b = act.get("r", 0), act.get("g", 0), act.get("b", 0)
    p16a = act.get("speedMs", act.get("periodMs", act.get("spawnMs", 500)))
    p8a = act.get("p8a", act.get("r2", act.get("minBri", act.get("spacing",
           act.get("paletteId", act.get("cooling", act.get("tailLen",
           act.get("density", 0))))))))
    p8b = act.get("p8b", act.get("g2", act.get("sparking", 0)))
    p8c = act.get("p8c", act.get("b2", act.get("direction", 0)))
    p8d = act.get("p8d", act.get("decay", act.get("fadeSpeed", 0)))
    return tuple(int(v or 0) for v in (t, r, g, b, p16a, p8a, p8b, p8c, p8d))

def _load_step_pkt(idx, total, step, child, delay_ms=0):
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(step)
    dur = int(step.get("durationS", 5) or 5)
    # Check for per-string LED range override from bake
    if "_ledOffset" in step:
        # Target specific string's LED range only
        ls = [0xFFFF] * 8
        le = [0xFFFF] * 8
        si = step.get("_stringIndex", 0)
        ls[si] = step["_ledOffset"]
        le[si] = step["_ledOffset"] + step["_ledCount"] - 1
        ls = struct.pack("<8H", *ls)
        le = struct.pack("<8H", *le)
    else:
        ls, le = _child_led_ranges(child)
    pl = struct.pack("<BBBBBBHBBBBHH", idx, total, t, r, g, b, p16a, p8a, p8b, p8c, p8d, dur, int(delay_ms))
    return _hdr(CMD_LOAD_STEP) + pl + ls + le

#  "  "  Flask application  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

app = Flask(__name__, static_folder=None)

#  "  "  Status  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/favicon.ico")
def favicon():
    abort(404)

@app.get("/status")
def status():
    return jsonify(role="parent", hostname=socket.gethostname(), version=VERSION)

#  "  "  Children  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

CHILD_STALE_S = 120   # mark offline if not seen for 2 minutes
_startup_check_done = False

def _periodic_ping():
    """Background thread: broadcast PING periodically.  The UDP listener
    daemon picks up PONGs and updates child records   " no per-child
    send_recv needed, so there are no port conflicts."""
    global _startup_check_done
    # Startup sweep: ping twice with a gap for slow booters
    _broadcast_ping_all()
    _startup_check_done = True
    time.sleep(5)
    _broadcast_ping_all()
    with _lock:
        # Mark children not seen recently as offline
        now = int(time.time())
        for c in _children:
            if c.get("seen", 0) > 0 and now - c["seen"] > CHILD_STALE_S:
                c["status"] = 0
        _save("children", _children)
    # Periodic sweep every 30 seconds
    while True:
        time.sleep(30)
        _broadcast_ping_all()
        # Also probe WLED devices via HTTP
        for c in list(_children):
            if c.get("type") == "wled":
                info = wled_probe(c["ip"], timeout=2.0)
                if info:
                    c["status"] = 1
                    c["seen"] = int(time.time())
                    c["fwVersion"] = info.get("ver")
                else:
                    c["status"] = 0
        time.sleep(2)   # allow PONGs to arrive
        with _lock:
            now = int(time.time())
            for c in _children:
                if c.get("type") != "wled" and c.get("seen", 0) > 0 and now - c["seen"] > CHILD_STALE_S:
                    c["status"] = 0
            _save("children", _children)

def _udp_listener():
    """Background daemon: persistent bind on UDP_PORT, receives ACTION_EVENT packets from children."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", UDP_PORT))
        s.settimeout(1.0)
    except OSError as e:
        print(f"[udp-listener] Could not bind port {UDP_PORT}: {e}")
        return
    while True:
        try:
            data, addr = s.recvfrom(256)
        except socket.timeout:
            continue
        except Exception:
            continue
        if len(data) < 8:
            continue
        magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        if magic != UDP_MAGIC or ver not in (3, UDP_VERSION):
            continue
        ip = addr[0]
        if cmd == CMD_ACTION_EVENT and len(data) >= 12:
            at, si, tot, ev = struct.unpack_from("<BBBB", data, 8)
            _live_events[ip] = {
                "actionType": at, "stepIndex": si,
                "totalSteps": tot, "event": ev,
                "ts": time.time(),
            }
            log.debug("ACTION_EVENT from %s: type=%d step=%d/%d event=%s",
                       ip, at, si, tot, "started" if ev == 0 else "ended")
        elif cmd == CMD_PONG:
            # Handle PONGs from broadcast/direct pings
            info = _parse_pong(data, ip)
            if info:
                log.debug("PONG from %s (%s) fw=%s", ip, info.get("hostname"), info.get("fwVersion"))
                # Store for discover to find
                _recent_pongs[ip] = info
                # Update known children
                for c in _children:
                    if c.get("ip") == ip or c.get("hostname") == info.get("hostname"):
                        saved_fw = c.get("fwVersion", "")
                        c.update({k: v for k, v in info.items() if k != "id"})
                        # Preserve 3-digit version over PONG's 2-digit
                        if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                            c["fwVersion"] = saved_fw
                        _probe_board_type(c)
                        break
        else:
            log.debug("UDP cmd=0x%02X from %s (%d bytes)", cmd, ip, len(data))

def _bootstrap_ssh_defaults():
    """Pre-populate SSH credentials on first run (default OrangePi/RPi creds)."""
    global _ssh, _ssh_bootstrapped
    if _ssh_bootstrapped:
        return
    _ssh_bootstrapped = True
    if not _ssh.get("sshPassword") and not _ssh.get("sshKeyPath"):
        import pathlib
        key_path = str(pathlib.Path.home() / ".ssh" / "id_ed25519")
        _ssh["sshUser"] = "root"
        _ssh["sshPassword"] = _encrypt_pw("orangepi")
        if pathlib.Path(key_path).exists():
            _ssh["sshKeyPath"] = key_path
        _save("ssh", _ssh)
        log.info("SSH defaults set: root/orangepi, key=%s", _ssh.get("sshKeyPath") or "(none)")

def start_background_tasks():
    """Call once after import to kick off periodic ping and UDP listener threads."""
    global _startup_check_done
    _bootstrap_ssh_defaults()
    threading.Thread(target=_udp_listener, daemon=True).start()
    if _children:
        threading.Thread(target=_periodic_ping, daemon=True).start()
    else:
        _startup_check_done = True

@app.get("/api/children")
def api_children():
    now = int(time.time())
    for c in _children:
        if c.get("status") == 1 and c.get("seen", 0) > 0:
            if now - c["seen"] > CHILD_STALE_S:
                c["status"] = 0
    return jsonify([dict(c, startupDone=_startup_check_done) for c in _children])

@app.get("/api/children/discover")
def api_children_discover():
    if _discover_state["pending"]:
        return jsonify(pending=True)
    # Start background discovery
    _discover_state["pending"] = True
    _discover_state["data"] = []
    threading.Thread(target=_discover_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/children/discover/results")
def api_children_discover_results():
    if _discover_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_discover_state["data"])

@app.get("/api/children/export")
def api_children_export():
    return jsonify(_children)

@app.post("/api/children")
def api_children_add():
    global _nxt_c
    ip = (request.get_json(silent=True) or {}).get("ip", "").strip()
    # Sanitize: strip protocol prefix and any path/port suffix
    ip = ip.replace("https://", "").replace("http://", "").split("/")[0].strip()
    if not ip:
        return jsonify(ok=False, err="ip required"), 400
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        if not addr.is_private:
            return jsonify(ok=False, err="Only private/LAN IP addresses allowed"), 400
    except ValueError:
        return jsonify(ok=False, err="Invalid IP address"), 400
    # Prevent duplicate IP entries
    existing = next((c for c in _children if c.get("ip") == ip), None)
    if existing:
        return jsonify(ok=True, id=existing["id"], duplicate=True)
    child = {"ip": ip, "hostname": ip, "name": ip,
             "desc": "", "sc": 0, "strings": [], "status": 0, "seen": 0,
             "type": "slyled"}
    with _lock:
        child["id"] = _nxt_c
        _nxt_c += 1
        _children.append(child)
        _save("children", _children)
    # Try SlyLED PING first
    _ping(child)
    # If SlyLED ping failed, try WLED probe
    if child.get("status") != 1:
        wled_info = wled_probe(ip)
        if wled_info:
            child["type"] = "wled"
            child["hostname"] = wled_info["name"]
            child["name"] = wled_info["name"]
            child["sc"] = 1
            child["strings"] = [{"leds": wled_info["ledCount"], "mm": 0,
                                  "type": 0, "cdir": 0, "cmm": 0, "sdir": 0, "folded": False}]
            child["status"] = 1
            child["seen"] = int(time.time())
            child["fwVersion"] = wled_info["ver"]
            child["wled"] = wled_info
            log.info("WLED device found at %s: %s (%d LEDs, v%s)",
                     ip, wled_info["name"], wled_info["ledCount"], wled_info["ver"])
    with _lock:
        _save("children", _children)
    ct = child.get("type", "slyled")
    return jsonify(ok=True, id=child["id"], type=ct, boardType=child.get("boardType", ""),
                   name=child.get("name", ""), hostname=child.get("hostname", ""))

@app.delete("/api/children/<int:cid>")
def api_children_delete(cid):
    global _children
    with _lock:
        n = len(_children)
        _children = [c for c in _children if c["id"] != cid]
        if len(_children) == n:
            abort(404)
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/<int:cid>/refresh")
def api_children_refresh(cid):
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        abort(404)
    _ping(child)          # ping outside lock so DELETE/other requests aren't blocked
    with _lock:
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/<int:cid>/reboot")
def api_children_reboot(cid):
    """Send HTTP POST /reboot to a child, causing it to restart."""
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        abort(404)
    ip = child["ip"]
    log.info("REBOOT: sending to %s (%s)", ip, child.get("hostname"))
    try:
        import urllib.request
        req = urllib.request.Request(f"http://{ip}/reboot", method="POST", data=b"")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # child reboots immediately, response may not arrive
    child["status"] = 0
    with _lock:
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/refresh-all")
def api_children_refresh_all():
    """Broadcast ping all children. Non-blocking - starts background thread."""
    if _refresh_state["pending"]:
        return jsonify(pending=True)
    _refresh_state["pending"] = True
    _refresh_state["data"] = {}
    threading.Thread(target=_refresh_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/children/refresh-all/results")
def api_children_refresh_all_results():
    if _refresh_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_refresh_state["data"])

@app.get("/api/children/<int:cid>/status")
def api_child_status(cid):
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        return jsonify(ok=False, err="not found")
    resp = _send_recv(child["ip"], _hdr(CMD_STATUS_REQ))
    if not resp or len(resp) < 16:
        return jsonify(ok=False, err="timeout")
    aa, ra, cs, rssi, up = struct.unpack_from("<BBBbI", resp, 8)
    return jsonify(ok=True, activeAction=aa, runnerActive=bool(ra),
                   currentStep=cs, wifiRssi=rssi, uptimeS=up)

@app.post("/api/children/import")
def api_children_import():
    global _nxt_c
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        abort(400)
    added = updated = skipped = 0
    with _lock:
        for c in data:
            ex = next((x for x in _children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = _nxt_c
                _nxt_c += 1
                _children.append(c)
                added += 1
        _save("children", _children)
    return jsonify(ok=True, added=added, updated=updated, skipped=skipped)

#  "  "  WLED device API  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_wled_cache = {}   # child_id  -' {"effects": [...], "palettes": [...], "ts": epoch}
_WLED_CACHE_TTL = 300  # 5 minutes

@app.get("/api/wled/effects/<int:cid>")
def api_wled_effects(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    now = time.time()
    cached = _wled_cache.get(cid)
    if cached and cached.get("effects") and now - cached.get("ts", 0) < _WLED_CACHE_TTL:
        return jsonify(cached["effects"])
    effects = wled_get_effects(child["ip"])
    if effects is None:
        return jsonify(ok=False, err="device unreachable"), 502
    _wled_cache.setdefault(cid, {})["effects"] = effects
    _wled_cache[cid]["ts"] = now
    return jsonify(effects)

@app.get("/api/wled/palettes/<int:cid>")
def api_wled_palettes(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    now = time.time()
    cached = _wled_cache.get(cid)
    if cached and cached.get("palettes") and now - cached.get("ts", 0) < _WLED_CACHE_TTL:
        return jsonify(cached["palettes"])
    palettes = wled_get_palettes(child["ip"])
    if palettes is None:
        return jsonify(ok=False, err="device unreachable"), 502
    _wled_cache.setdefault(cid, {})["palettes"] = palettes
    _wled_cache[cid]["ts"] = now
    return jsonify(palettes)

@app.get("/api/wled/segments/<int:cid>")
def api_wled_segments(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    # Try cached segments from probe first
    segs = child.get("wled", {}).get("segments")
    if segs:
        return jsonify(segs)
    segs = wled_get_segments(child["ip"])
    if segs is None:
        return jsonify(ok=False, err="device unreachable"), 502
    return jsonify(segs)

#  "  "  Layout  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/layout")
def api_layout_get():
    layout = dict(_layout)
    # Merge fixture positions into fixture objects for the SPA
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    child_map = {c["id"]: c for c in _children}
    layout["fixtures"] = []
    for f in _fixtures:
        fid = f["id"]
        pos = pos_map.get(fid, pos_map.get(f.get("childId"), {}))
        fixture_data = {**f}
        # Merge string data from linked child if fixture doesn't have its own
        if f.get("childId") is not None and not fixture_data.get("strings"):
            child = child_map.get(f["childId"])
            if child:
                fixture_data["strings"] = child.get("strings", [])
                fixture_data["sc"] = child.get("sc", 0)
        layout["fixtures"].append({
            **fixture_data,
            "x": pos.get("x", 0),
            "y": pos.get("y", 0),
            "z": pos.get("z", 0),
            "positioned": fid in pos_map or f.get("childId") in pos_map,
        })
    # Legacy: keep children for backward compat with bake/resolve
    layout["children"] = _layout.get("children", [])
    return jsonify(layout)

@app.post("/api/layout")
def api_layout_save():
    body = request.get_json(silent=True) or {}
    fixtures = body.get("fixtures", body.get("children", []))
    _layout["children"] = [{"id": f["id"], "x": f.get("x", 0), "y": f.get("y", 0), "z": f.get("z", 0)} for f in fixtures]
    _save("layout", _layout)
    return jsonify(ok=True)

#  "  "  Stage  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/stage")
def api_stage_get():
    return jsonify(_stage)

@app.post("/api/stage")
def api_stage_save():
    body = request.get_json(silent=True) or {}
    for k in ("w", "h", "d"):
        if k in body:
            v = body[k]
            if not isinstance(v, (int, float)) or v <= 0:
                return jsonify(err=f"Stage dimension '{k}' must be a positive number"), 400
            _stage[k] = float(v)
    _save("stage", _stage)
    # Sync canvas dimensions (mm) from stage (meters)
    with _lock:
        _settings["canvasW"] = int(_stage["w"] * 1000)
        _settings["canvasH"] = int(_stage["h"] * 1000)
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
        _save("settings", _settings)
        _save("layout", _layout)
        _sync_locked_objects()
    return jsonify(ok=True)


#  "  "  Fixtures (Phase 2)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/fixtures")
def api_fixtures_get():
    return jsonify(_fixtures)

@app.post("/api/fixtures")
def api_fixtures_create():
    global _nxt_fix
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    ftype = body.get("type", "linear")
    if ftype not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    fixture_type = body.get("fixtureType", "led")
    if fixture_type not in ("led", "dmx", "camera"):
        return jsonify(err="Invalid fixtureType - must be 'led', 'dmx', or 'camera'"), 400
    # DMX-specific validation
    if fixture_type == "dmx":
        dmx_uni = body.get("dmxUniverse")
        dmx_addr = body.get("dmxStartAddr")
        dmx_ch = body.get("dmxChannelCount")
        if not isinstance(dmx_uni, int) or dmx_uni < 1:
            return jsonify(err="dmxUniverse must be an integer >= 1"), 400
        if not isinstance(dmx_addr, int) or dmx_addr < 1 or dmx_addr > 512:
            return jsonify(err="dmxStartAddr must be 1-512"), 400
        if not isinstance(dmx_ch, int) or dmx_ch < 1:
            return jsonify(err="dmxChannelCount must be an integer >= 1"), 400
    # Camera-specific validation
    if fixture_type == "camera":
        fov = body.get("fovDeg")
        if fov is not None and (not isinstance(fov, (int, float)) or fov < 1 or fov > 180):
            return jsonify(err="fovDeg must be 1-180"), 400
    with _lock:
        f = {
            "id": _nxt_fix, "name": name or f"Fixture {_nxt_fix}",
            "fixtureType": fixture_type,
            "childId": body.get("childId"), "type": ftype,
            "childIds": body.get("childIds", []),  # for group fixtures
            "strings": body.get("strings", []),
            "rotation": body.get("rotation", [0, 0, 0]),  # [rx, ry, rz] degrees   " overrides child stripDir
            "aoeRadius": body.get("aoeRadius", 1000),
            "meshFile": body.get("meshFile"),
        }
        if fixture_type == "dmx":
            f["dmxUniverse"] = body["dmxUniverse"]
            f["dmxStartAddr"] = body["dmxStartAddr"]
            f["dmxChannelCount"] = body["dmxChannelCount"]
            f["dmxProfileId"] = body.get("dmxProfileId")
        if fixture_type == "camera":
            f["fovDeg"] = body.get("fovDeg", 60)
            f["cameraUrl"] = body.get("cameraUrl", "")
            f["resolutionW"] = body.get("resolutionW", 1920)
            f["resolutionH"] = body.get("resolutionH", 1080)
        _fixtures.append(f)
        _nxt_fix += 1
        _save("fixtures", _fixtures)
    return jsonify(ok=True, id=f["id"])

@app.get("/api/fixtures/<int:fid>")
def api_fixture_get(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    return jsonify(f)

@app.put("/api/fixtures/<int:fid>")
def api_fixture_update(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    # Validate fixtureType if changing
    if "fixtureType" in body and body["fixtureType"] not in ("led", "dmx", "camera"):
        return jsonify(err="Invalid fixtureType - must be 'led', 'dmx', or 'camera'"), 400
    # Validate geometry type if changing
    if "type" in body and body["type"] not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    # Validate DMX fields
    ft = body.get("fixtureType", f.get("fixtureType", "led"))
    if ft == "dmx":
        addr = body.get("dmxStartAddr", f.get("dmxStartAddr"))
        if "dmxStartAddr" in body:
            if not isinstance(addr, int) or addr < 1 or addr > 512:
                return jsonify(err="dmxStartAddr must be 1-512"), 400
        uni = body.get("dmxUniverse", f.get("dmxUniverse"))
        if "dmxUniverse" in body:
            if not isinstance(uni, int) or uni < 1:
                return jsonify(err="dmxUniverse must be an integer >= 1"), 400
        ch = body.get("dmxChannelCount", f.get("dmxChannelCount"))
        if "dmxChannelCount" in body:
            if not isinstance(ch, int) or ch < 1:
                return jsonify(err="dmxChannelCount must be an integer >= 1"), 400
    # Validate camera fields
    if ft == "camera" and "fovDeg" in body:
        fov = body["fovDeg"]
        if not isinstance(fov, (int, float)) or fov < 1 or fov > 180:
            return jsonify(err="fovDeg must be 1-180"), 400
    for k in ("name", "type", "fixtureType", "childId", "childIds", "strings",
              "rotation", "orientation", "mountedInverted", "aoeRadius", "meshFile",
              "dmxUniverse", "dmxStartAddr", "dmxChannelCount", "dmxProfileId",
              "fovDeg", "cameraUrl", "cameraIp", "cameraIdx", "resolutionW", "resolutionH"):
        if k in body:
            f[k] = body[k]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

@app.put("/api/fixtures/<int:fid>/aim")
def api_fixture_set_aim(fid):
    """Set rotation for a DMX or camera fixture.

    Accepts either {rotation: [rx, ry, rz]} or legacy {aimPoint: [x,y,z]}
    (converted to rotation on import).
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") not in ("dmx", "camera"):
        return jsonify(err="DMX or camera fixture not found"), 404
    body = request.get_json(silent=True) or {}
    # Accept rotation directly
    rot = body.get("rotation")
    if isinstance(rot, list) and len(rot) == 3:
        try:
            f["rotation"] = [float(v) for v in rot]
        except (TypeError, ValueError):
            return jsonify(err="rotation values must be numbers"), 400
        _save("fixtures", _fixtures)
        return jsonify(ok=True)
    # Legacy aimPoint → convert to rotation
    ap = body.get("aimPoint")
    if not isinstance(ap, list) or len(ap) != 3:
        return jsonify(err="rotation must be [rx,ry,rz]"), 400
    try:
        ap = [float(v) for v in ap]
    except (TypeError, ValueError):
        return jsonify(err="aimPoint values must be numbers"), 400
    fx = f.get("x", 0) or 0
    fy = f.get("y", 0) or 0
    fz = f.get("z", 0) or 0
    dx, dy, dz = ap[0] - fx, ap[1] - fy, ap[2] - fz
    hdist = math.sqrt(dx * dx + dy * dy)  # floor plane = XY (Z=height)
    if hdist > 0.001 or abs(dz) > 0.001:
        f["rotation"] = [
            round(-math.atan2(dz, hdist) * 180 / math.pi, 2),
            round(math.atan2(dx, dy) * 180 / math.pi, 2),
            f.get("rotation", [0, 0, 0])[2] if f.get("rotation") else 0
        ]
    _save("fixtures", _fixtures)
    return jsonify(ok=True, rotation=f.get("rotation", [0, 0, 0]))

@app.delete("/api/fixtures/<int:fid>")
def api_fixture_delete(fid):
    global _fixtures
    if not any(f["id"] == fid for f in _fixtures):
        return jsonify(ok=False, err="fixture not found"), 404
    _fixtures = [f for f in _fixtures if f["id"] != fid]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

# ── Camera discovery & CRUD ─────────────────────────────────────────────

_cam_discover_state = {"pending": False, "data": []}

def _probe_camera(ip, timeout=2):
    """Probe a camera node via HTTP GET /status. Returns info dict or None."""
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/status", timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("role") != "camera":
            return None
        return {
            "ip": ip,
            "hostname": data.get("hostname", ip),
            "name": data.get("hostname", ip),
            "fwVersion": data.get("fwVersion", ""),
            "fovDeg": data.get("fovDeg"),
            "resolutionW": data.get("resolutionW"),
            "resolutionH": data.get("resolutionH"),
            "capabilities": data.get("capabilities", {}),
            "cameraUrl": data.get("cameraUrl", ""),
            "cameras": data.get("cameras", []),
            "cameraCount": data.get("cameraCount", 0),
        }
    except Exception:
        return None

def _discover_cameras():
    """Scan all local subnets for camera nodes, return unregistered ones."""
    known_ips = set()
    for f in _fixtures:
        if f.get("fixtureType") == "camera" and f.get("cameraIp"):
            known_ips.add(f["cameraIp"])
    results = []
    for prefix in _local_subnet_prefixes():
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            if ip in known_ips:
                continue
            info = _probe_camera(ip, timeout=0.3)
            if info:
                results.append(info)
    return results

def _cam_discover_bg():
    try:
        _cam_discover_state["data"] = _discover_cameras()
    finally:
        _cam_discover_state["pending"] = False

@app.get("/api/cameras")
def api_cameras():
    """List registered camera fixtures with live status."""
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    result = []
    for c in cams:
        cam = dict(c)
        ip = c.get("cameraIp")
        cam["online"] = False
        if ip:
            info = _probe_camera(ip, timeout=1)
            if info:
                cam["online"] = True
                cam["fwVersion"] = info.get("fwVersion", "")
                cam["hostname"] = info.get("hostname", "")
                cam["capabilities"] = info.get("capabilities", {})
        result.append(cam)
    return jsonify(result)

@app.get("/api/cameras/discover")
def api_cameras_discover():
    if _cam_discover_state["pending"]:
        return jsonify(pending=True)
    _cam_discover_state["pending"] = True
    _cam_discover_state["data"] = []
    threading.Thread(target=_cam_discover_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/cameras/discover/results")
def api_cameras_discover_results():
    if _cam_discover_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_cam_discover_state["data"])

@app.post("/api/cameras/probe")
def api_cameras_probe():
    """Probe a single IP for a camera node."""
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    if not ip:
        return jsonify(ok=False, err="ip required"), 400
    info = _probe_camera(ip, timeout=3)
    if info:
        return jsonify(ok=True, info=info)
    return jsonify(ok=False, err="No camera found"), 404

def _camera_fov_from_info(info, cam_idx=0):
    """Extract per-camera FOV from probe info, falling back to node-level."""
    if not info:
        return None
    cameras = info.get("cameras", [])
    if cam_idx < len(cameras) and "fovDeg" in cameras[cam_idx]:
        return cameras[cam_idx]["fovDeg"]
    return info.get("fovDeg")

@app.post("/api/cameras")
def api_cameras_register():
    """Register a camera node — creates a camera fixture."""
    global _nxt_fix
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    if not ip:
        return jsonify(err="ip required"), 400
    import ipaddress as _ipa
    try:
        addr = _ipa.ip_address(ip)
        if not addr.is_private:
            return jsonify(err="Only private/LAN IP addresses allowed"), 400
    except ValueError:
        return jsonify(err="Invalid IP address"), 400
    # Probe camera for info
    info = _probe_camera(ip, timeout=3)
    cameras = (info or {}).get("cameras", [])
    base_name = body.get("name") or (info.get("hostname") if info else None) or f"Camera {ip}"

    # Create one fixture per camera sensor (not one per node)
    created_ids = []
    with _lock:
        for cam_idx in range(max(1, len(cameras))):
            # Check for duplicate (same IP + same camera index)
            dup = next((f for f in _fixtures if f.get("fixtureType") == "camera"
                        and f.get("cameraIp") == ip and f.get("cameraIdx", 0) == cam_idx), None)
            if dup:
                continue
            cam_info = cameras[cam_idx] if cam_idx < len(cameras) else {}
            # Use user-set custom name from camera config page, fall back to hardware name
            cam_name = cam_info.get("customName") or cam_info.get("name", "")
            fixture_name = f"{base_name} — {cam_name}" if len(cameras) > 1 and cam_name else base_name
            f = {
                "id": _nxt_fix, "name": fixture_name,
                "fixtureType": "camera", "type": "point",
                "childId": None, "childIds": [], "strings": [],
                "rotation": [0, 0, 0], "aoeRadius": 1000, "meshFile": None,
                "cameraIp": ip,
                "cameraIdx": cam_idx,
                "fovDeg": _camera_fov_from_info(info, cam_idx) or body.get("fovDeg") or 60,
                "cameraUrl": (info or {}).get("cameraUrl") or body.get("cameraUrl", ""),
                "resolutionW": cam_info.get("resW") or body.get("resolutionW") or 1920,
                "resolutionH": cam_info.get("resH") or body.get("resolutionH") or 1080,
            }
            _fixtures.append(f)
            created_ids.append(_nxt_fix)
            _nxt_fix += 1
        _save("fixtures", _fixtures)
    if not created_ids:
        return jsonify(err="Camera already registered at this IP"), 409
    return jsonify(ok=True, id=created_ids[0], ids=created_ids, count=len(created_ids)), 201

@app.delete("/api/cameras/<int:fid>")
def api_cameras_delete(fid):
    """Unregister a camera — removes the fixture."""
    global _fixtures
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    with _lock:
        _fixtures = [x for x in _fixtures if x["id"] != fid]
        _save("fixtures", _fixtures)
    return jsonify(ok=True), 200

@app.get("/api/cameras/<int:fid>/snapshot")
def api_camera_snapshot(fid):
    """Proxy a snapshot from a camera node. ?cam=0 selects camera index."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = request.args.get("cam", 0, type=int)
    try:
        import urllib.request as _ur
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        data = resp.read()
        from flask import Response
        return Response(data, mimetype="image/jpeg")
    except Exception as e:
        return jsonify(err=str(e)), 503

@app.get("/api/cameras/<int:fid>/status")
def api_camera_status(fid):
    """Fetch live status from a camera node."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    info = _probe_camera(ip, timeout=3)
    if not info:
        return jsonify(err="Camera offline"), 503
    return jsonify(info)

def _pixel_to_stage_homography(detections, H_flat, frame_w, frame_h):
    """Transform detections using a calibrated homography matrix."""
    stage_w = _stage.get("w", 3.0) * 1000
    stage_d = _stage.get("d", 1.5) * 1000
    result = []
    for det in detections:
        # Bounding box center
        px = det["x"] + det["w"] / 2
        py = det["y"] + det["h"] / 2
        sx, sz = _apply_homography(H_flat, px, py)
        # Estimate size using corner-to-corner transform
        px1, py1 = det["x"], det["y"]
        px2, py2 = det["x"] + det["w"], det["y"] + det["h"]
        sx1, sz1 = _apply_homography(H_flat, px1, py1)
        sx2, sz2 = _apply_homography(H_flat, px2, py2)
        obj_w = abs(sx2 - sx1)
        obj_h = abs(sz2 - sz1)
        # Clamp to stage
        sx = max(0, min(sx, stage_w))
        sz = max(0, min(sz, stage_d))
        result.append({
            "label": det["label"],
            "confidence": det["confidence"],
            "x": round(sx), "y": 0, "z": round(sz),
            "w": round(max(obj_w, 100)), "h": round(max(obj_h, 100)),
            "pixelBox": {"x": det["x"], "y": det["y"], "w": det["w"], "h": det["h"]},
        })
    return result

def _pixel_to_stage(detections, cam_fixture, frame_w, frame_h):
    """Transform pixel-space detections to stage-space (mm).

    Uses calibrated homography if available, otherwise falls back to
    ground-plane projection using camera position, rotation, and FOV.
    """
    # Try calibrated homography first
    cal = _calibrations.get(str(cam_fixture.get("id")))
    if cal and cal.get("matrix"):
        return _pixel_to_stage_homography(detections, cal["matrix"], frame_w, frame_h)

    # Camera position from layout
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    cam_pos = pos_map.get(cam_fixture["id"], {})
    cx = cam_pos.get("x", 0)  # mm (width)
    cy = cam_pos.get("y", 0)  # mm (depth)
    cz = cam_pos.get("z", 0)  # mm (height)

    # Compute aim from rotation
    aim = _rotation_to_aim(cam_fixture.get("rotation", [0, 0, 0]), [cx, cy, cz])
    ax, ay, az = aim[0], aim[1], aim[2]

    fov_deg = cam_fixture.get("fovDeg", 60)
    fov_rad = math.radians(fov_deg)

    # Camera look direction (normalized)
    dx, dy, dz = ax - cx, ay - cy, az - cz
    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1:
        return detections  # Camera not positioned, return raw

    dx, dy, dz = dx/dist, dy/dist, dz/dist

    # Camera right vector (cross of look × world_up)
    # World up = (0, 0, 1) — Z is height
    # cross(look, up) = (dy*1 - dz*0, dz*0 - dx*1, dx*0 - dy*0) = (dy, -dx, 0)
    rx = dy
    ry = -dx
    rz = 0
    r_len = math.sqrt(rx*rx + ry*ry)
    if r_len < 0.001:
        rx, ry, rz = 1, 0, 0  # Looking straight up/down, pick arbitrary right
    else:
        rx, ry = rx/r_len, ry/r_len

    # Camera up vector (cross of right × look)
    ux = ry*dz - rz*dy
    uy = rz*dx - rx*dz
    uz = rx*dy - ry*dx

    # Half-FOV determines the image plane extent
    half_fov = fov_rad / 2
    aspect = frame_w / frame_h if frame_h > 0 else 1.0

    stage_w = _stage.get("w", 3.0) * 1000  # mm
    stage_h = _stage.get("h", 2.0) * 1000
    stage_d = _stage.get("d", 1.5) * 1000

    result = []
    for det in detections:
        # Bounding box center in pixel coords
        px = det["x"] + det["w"] / 2
        py = det["y"] + det["h"] / 2

        # Normalize pixel coords to [-1, 1] (NDC)
        ndc_x = (px / frame_w - 0.5) * 2   # -1 (left) to 1 (right)
        ndc_y = -(py / frame_h - 0.5) * 2  # -1 (bottom) to 1 (top), flip Y

        # Ray direction through pixel on image plane
        ray_x = dx + math.tan(half_fov) * (ndc_x * rx + ndc_y / aspect * ux)
        ray_y = dy + math.tan(half_fov) * (ndc_x * ry + ndc_y / aspect * uy)
        ray_z = dz + math.tan(half_fov) * (ndc_x * rz + ndc_y / aspect * uz)

        # Intersect ray with ground plane (z=0)
        # Point = camera_pos + t * ray, solve for z=0: cz + t * ray_z = 0
        if abs(ray_z) < 0.0001:
            # Ray parallel to ground — place at aim point distance
            t = dist
        else:
            t = -cz / ray_z
            if t < 0:
                t = dist  # Ray points away from ground, use aim distance

        # Stage intersection point
        sx = cx + t * ray_x
        sy = cy + t * ray_y

        # Estimate object size on ground plane from bounding box
        # Use proportion of FOV covered by the box
        ground_span = 2 * t * math.tan(half_fov)  # total width visible at distance t
        obj_w = (det["w"] / frame_w) * ground_span
        obj_h = (det["h"] / frame_h) * ground_span / aspect

        # Clamp to stage bounds
        sx = max(0, min(sx, stage_w))
        sy = max(0, min(sy, stage_d))

        result.append({
            "label": det["label"],
            "confidence": det["confidence"],
            "x": round(sx),
            "y": round(sy),
            "z": 0,
            "w": round(max(obj_w, 100)),   # minimum 100mm
            "h": round(max(obj_h, 100)),
            "pixelBox": {"x": det["x"], "y": det["y"], "w": det["w"], "h": det["h"]},
        })
    return result

@app.post("/api/cameras/<int:fid>/scan")
def api_camera_scan(fid):
    """Proxy scan request to camera node. Returns detections with stage coordinates."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    body = request.get_json(silent=True) or {}
    threshold = body.get("threshold", 0.5)
    cam_idx = body.get("cam", 0)
    resolution = body.get("resolution", 320)
    # Forward to camera node /scan endpoint
    try:
        import urllib.request as _ur
        req_data = json.dumps({"threshold": threshold, "cam": cam_idx,
                                "resolution": resolution}).encode()
        req = _ur.Request(f"http://{ip}:5000/scan",
                          data=req_data,
                          headers={"Content-Type": "application/json"})
        resp = _ur.urlopen(req, timeout=30)
        raw = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify(err=f"Camera scan failed: {e}"), 503
    if not raw.get("ok"):
        return jsonify(err=raw.get("err", "Scan failed")), 503
    raw_dets = raw.get("detections", [])
    frame_size = raw.get("frameSize", [640, 480])
    # Transform pixel coords to stage coords
    stage_dets = _pixel_to_stage(raw_dets, f, frame_size[0], frame_size[1])
    return jsonify(
        ok=True,
        detections=stage_dets,
        cameraId=fid,
        captureMs=raw.get("captureMs"),
        inferenceMs=raw.get("inferenceMs"),
    )

# ── Camera calibration — homography math ──────────────────────────────

def _compute_homography(stage_pts, pixel_pts):
    """Compute 3×3 homography mapping pixel coords → stage coords (mm) using DLT.

    Args:
        stage_pts: list of [x, z] in stage mm (ground plane, y=0)
        pixel_pts: list of [px, py] in camera pixels

    Returns:
        (matrix_3x3_flat, avg_reproj_error_px) or raises ValueError
    """
    n = len(stage_pts)
    if n < 2:
        raise ValueError(f"Need at least 2 reference points, got {n}")
    if n != len(pixel_pts):
        raise ValueError("stage_pts and pixel_pts must have same length")

    # Check for collinearity (all points on a line) — only relevant for 3+ points
    if n >= 3:
        pts = np.array(pixel_pts, dtype=float)
        v1 = pts[1] - pts[0]
        v2 = pts[2] - pts[0]
        cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
        if cross < 1.0:
            raise ValueError("Reference points are collinear — need non-collinear points")

    sp = np.array(stage_pts, dtype=float)
    pp = np.array(pixel_pts, dtype=float)

    # 2-point case: compute similarity transform (scale + translate)
    if n == 2:
        # Simple affine: stage = scale * pixel + offset
        dp = pp[1] - pp[0]
        ds = sp[1] - sp[0]
        px_dist = np.linalg.norm(dp)
        if px_dist < 0.001:
            raise ValueError("Reference pixel points are identical")
        st_dist = np.linalg.norm(ds)
        scale = st_dist / px_dist
        # Rotation angle
        angle_p = np.arctan2(dp[1], dp[0])
        angle_s = np.arctan2(ds[1], ds[0])
        theta = angle_s - angle_p
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        # Build 3x3 matrix: rotate + scale + translate
        R = scale * np.array([[cos_t, -sin_t], [sin_t, cos_t]])
        t = sp[0] - R @ pp[0]
        H = np.array([
            [R[0, 0], R[0, 1], t[0]],
            [R[1, 0], R[1, 1], t[1]],
            [0, 0, 1],
        ])
        # Compute error
        errors = []
        for i in range(n):
            v = H @ np.array([pp[i][0], pp[i][1], 1.0])
            errors.append(np.sqrt((v[0] - sp[i][0])**2 + (v[1] - sp[i][1])**2))
        return H.flatten().tolist(), float(np.mean(errors))

    # Build DLT matrix A (2n × 9) for 3+ points
    A = []
    for i in range(n):
        px, py = pp[i]
        sx, sz = sp[i]
        A.append([-px, -py, -1, 0, 0, 0, sx*px, sx*py, sx])
        A.append([0, 0, 0, -px, -py, -1, sz*px, sz*py, sz])
    A = np.array(A)

    # SVD solve for h (last column of V)
    _, _, Vt = np.linalg.svd(A)
    h = Vt[-1]
    H = h.reshape(3, 3)

    # Normalize so H[2,2] = 1
    if abs(H[2, 2]) > 1e-10:
        H = H / H[2, 2]

    # Compute reprojection error
    errors = []
    for i in range(n):
        px, py = pp[i]
        v = H @ np.array([px, py, 1.0])
        if abs(v[2]) > 1e-10:
            proj_sx, proj_sz = v[0]/v[2], v[1]/v[2]
        else:
            proj_sx, proj_sz = v[0], v[1]
        err = np.sqrt((proj_sx - sp[i][0])**2 + (proj_sz - sp[i][1])**2)
        errors.append(err)
    avg_error = float(np.mean(errors))

    return H.flatten().tolist(), avg_error

def _apply_homography(H_flat, px, py):
    """Apply 3×3 homography to a pixel point → stage coords [x, z] in mm."""
    H = [H_flat[0:3], H_flat[3:6], H_flat[6:9]]
    w = H[2][0]*px + H[2][1]*py + H[2][2]
    if abs(w) < 1e-10:
        w = 1e-10
    sx = (H[0][0]*px + H[0][1]*py + H[0][2]) / w
    sz = (H[1][0]*px + H[1][1]*py + H[1][2]) / w
    return sx, sz


_calib_state = {}  # {cam_fid: {step, fixtures, flashing, detected}}

@app.post("/api/cameras/<int:fid>/calibrate/start")
def api_camera_calibrate_start(fid):
    """Start calibration sequence — identifies reference fixtures to flash."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404

    # Find positioned LED/DMX fixtures as reference points
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    refs = []
    for fx in _fixtures:
        if fx["id"] == fid:
            continue
        if fx["id"] not in pos_map:
            continue
        if fx.get("fixtureType") not in ("led", "dmx"):
            continue
        p = pos_map[fx["id"]]
        refs.append({"id": fx["id"], "name": fx.get("name", ""),
                      "x": p.get("x", 0), "z": p.get("z", 0),
                      "fixtureType": fx.get("fixtureType")})

    if len(refs) < 2:
        return jsonify(err=f"Need at least 2 positioned fixtures as reference points, found {len(refs)}"), 400

    _calib_state[fid] = {"step": 0, "fixtures": refs, "detected": []}
    return jsonify(ok=True, steps=len(refs), fixtures=refs)


@app.post("/api/cameras/<int:fid>/calibrate/detect")
def api_camera_calibrate_detect(fid):
    """Capture a detection for a specific reference fixture during calibration.
    Body: {fixtureId, pixelX, pixelY} — the pixel position where the fixture was detected."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    state = _calib_state.get(fid)
    if not state:
        return jsonify(err="No calibration in progress — call /calibrate/start first"), 400
    body = request.get_json(silent=True) or {}
    fix_id = body.get("fixtureId")
    px = body.get("pixelX")
    py = body.get("pixelY")
    if fix_id is None or px is None or py is None:
        return jsonify(err="fixtureId, pixelX, pixelY required"), 400
    # Verify fixture is in the reference list
    ref = next((r for r in state["fixtures"] if r["id"] == fix_id), None)
    if not ref:
        return jsonify(err=f"Fixture {fix_id} is not a calibration reference"), 400
    state["detected"].append({
        "fixtureId": fix_id, "stageX": ref["x"], "stageZ": ref["z"],
        "pixelX": float(px), "pixelY": float(py),
    })
    state["step"] = len(state["detected"])
    return jsonify(ok=True, step=state["step"], total=len(state["fixtures"]))


@app.post("/api/cameras/<int:fid>/calibrate/compute")
def api_camera_calibrate_compute(fid):
    """Compute homography from collected reference points."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    state = _calib_state.get(fid)
    if not state or len(state.get("detected", [])) < 2:
        return jsonify(err="Need at least 2 detected reference points"), 400
    detected = state["detected"]
    stage_pts = [[d["stageX"], d["stageZ"]] for d in detected]
    pixel_pts = [[d["pixelX"], d["pixelY"]] for d in detected]
    try:
        matrix, error = _compute_homography(stage_pts, pixel_pts)
    except ValueError as e:
        return jsonify(err=str(e)), 400
    # Store calibration
    cal = {
        "matrix": matrix,
        "error": round(error, 2),
        "points": detected,
        "timestamp": time.time(),
    }
    _calibrations[str(fid)] = cal
    _save("calibrations", _calibrations)
    f["calibrated"] = True
    _save("fixtures", _fixtures)
    # Clean up state
    _calib_state.pop(fid, None)
    return jsonify(ok=True, error=round(error, 2), calibrated=True)


@app.get("/api/cameras/<int:fid>/intrinsic")
def api_camera_intrinsic_get(fid):
    """Proxy intrinsic calibration data from a camera node."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/calibrate/intrinsic?cam={cam_idx}", timeout=10)
        return jsonify(json.loads(resp.read().decode("utf-8")))
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


# -- ArUco calibration — detection runs on orchestrator, cameras only provide snapshots (#329)

_aruco_frames = {}  # {fid: [(corners, ids, frame_size), ...]}

def _aruco_detect(frame):
    """Run ArUco detection on a frame. Returns (corners, ids, rejected, frame_size).
    Tries default params first (fast), falls back to relaxed for high-res.
    Compatible with OpenCV 4.7 (detectMarkers) and 4.8+ (ArucoDetector)."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    params = cv2.aruco.DetectorParameters()
    def _detect(g, d, p):
        if hasattr(cv2.aruco, 'ArucoDetector'):
            return cv2.aruco.ArucoDetector(d, p).detectMarkers(g)
        return cv2.aruco.detectMarkers(g, d, parameters=p)
    corners, ids, rejected = _detect(gray, aruco_dict, params)
    if (ids is None or len(ids) == 0) and frame.shape[1] >= 1920:
        params.adaptiveThreshWinSizeMin = 3
        params.adaptiveThreshWinSizeMax = 53
        params.adaptiveThreshWinSizeStep = 4
        params.minMarkerPerimeterRate = 0.01
        params.maxMarkerPerimeterRate = 4.0
        params.polygonalApproxAccuracyRate = 0.05
        params.minCornerDistanceRate = 0.01
        params.minDistanceToBorder = 1
        params.errorCorrectionRate = 0.8
        corners, ids, rejected = _detect(gray, aruco_dict, params)
    return corners, ids, rejected, gray.shape[::-1]


@app.post("/api/cameras/<int:fid>/aruco/capture")
def api_camera_aruco_capture(fid):
    """Fetch snapshot from camera, run ArUco detection on orchestrator."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed on orchestrator"), 500
    # Fetch JPEG snapshot from camera
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        jpeg_data = resp.read()
    except Exception as e:
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                       "err": f"Snapshot failed: {e}",
                       "frameCount": len(_aruco_frames.get(fid, []))}])
    # Decode and detect
    frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                       "err": "Decode failed",
                       "frameCount": len(_aruco_frames.get(fid, []))}])
    corners, ids, rejected, frame_size = _aruco_detect(frame)
    if ids is not None and len(ids) > 0:
        if fid not in _aruco_frames:
            _aruco_frames[fid] = []
        _aruco_frames[fid].append((corners, ids, frame_size))
        found_ids = ids.flatten().tolist()
        log.info("ArUco capture fid=%d: %d markers (ids=%s), total=%d frames",
                 fid, len(ids), found_ids, len(_aruco_frames[fid]))
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": len(ids),
                       "ids": found_ids, "frameCount": len(_aruco_frames[fid])}])
    return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                   "frameCount": len(_aruco_frames.get(fid, []))}])


@app.post("/api/cameras/<int:fid>/aruco/compute")
def api_camera_aruco_compute(fid):
    """Compute intrinsic calibration from accumulated frames — all on orchestrator."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    frames = _aruco_frames.get(fid, [])
    if len(frames) < 3:
        return jsonify(ok=False, err=f"Need at least 3 frames, have {len(frames)}")
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed"), 500
    body = request.get_json(silent=True) or {}
    marker_size = body.get("markerSize", 150)
    half = marker_size / 2.0
    # Build calibration arrays: each marker = 4 object + 4 image points
    obj_points = []
    img_points = []
    frame_size = frames[0][2]
    for corners, ids, sz in frames:
        for i in range(len(ids)):
            obj = np.array([[-half, half, 0], [half, half, 0],
                            [half, -half, 0], [-half, -half, 0]], dtype=np.float32)
            obj_points.append(obj)
            img_points.append(corners[i].reshape(4, 2).astype(np.float32))
    try:
        ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
            obj_points, img_points, frame_size, None, None)
    except Exception as e:
        return jsonify(ok=False, err=f"calibrateCamera failed: {e}")
    if not ret or K is None:
        return jsonify(ok=False, err="Calibration failed — try more frames")
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    rms = float(ret)
    # Save to camera node if possible
    ip = f.get("cameraIp")
    cam_idx = f.get("cameraIdx", 0)
    if ip:
        cal_data = {"cam": cam_idx, "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                    "distCoeffs": dist.flatten().tolist() if dist is not None else [],
                    "rmsError": rms, "frameCount": len(frames)}
        try:
            import urllib.request as _ur
            req = _ur.Request(f"http://{ip}:5000/calibrate/intrinsic/save",
                              data=json.dumps(cal_data).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            _ur.urlopen(req, timeout=5)
        except Exception:
            pass  # Save failed — calibration still valid locally
    return jsonify(ok=True, frameCount=len(frames), rmsError=round(rms, 4),
                   fx=round(fx, 1), fy=round(fy, 1), cx=round(cx, 1), cy=round(cy, 1),
                   distCoeffs=dist.flatten().tolist() if dist is not None else [])


@app.post("/api/cameras/<int:fid>/aruco/reset")
def api_camera_aruco_reset(fid):
    """Reset accumulated ArUco frames for a camera."""
    _aruco_frames.pop(fid, None)
    return jsonify(ok=True, frameCount=0)


@app.post("/api/cameras/<int:fid>/stage-map")
def api_camera_stage_map(fid):
    """Compute stage-map calibration on orchestrator using solvePnP (#330).

    Fetches a snapshot from the camera, runs ArUco detection locally,
    matches detected markers against provided marker positions, and
    computes camera pose via cv2.solvePnP.
    """
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed on orchestrator"), 500
    if np is None:
        return jsonify(ok=False, err="NumPy not installed on orchestrator"), 500
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", f.get("cameraIdx", 0))
    markers = body.get("markers", [])
    if not markers or len(markers) < 3:
        return jsonify(ok=False, err="Need at least 3 marker positions"), 400
    marker_size = body.get("markerSize", 150)  # mm
    half = marker_size / 2.0
    # Build lookup: marker_id → {x, y, z}
    marker_map = {}
    for m in markers:
        mid = m.get("id")
        if mid is not None:
            marker_map[int(mid)] = m
    # Fetch JPEG snapshot from camera
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        jpeg_data = resp.read()
    except Exception as e:
        return jsonify(ok=False, err=f"Snapshot failed: {e}"), 503
    # Decode and run ArUco detection
    frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify(ok=False, err="Failed to decode snapshot"), 500
    corners, ids, _rejected, frame_size = _aruco_detect(frame)
    h, w = frame.shape[:2]
    detected_count = len(ids) if ids is not None else 0
    if ids is None or len(ids) == 0:
        return jsonify(ok=True, markersDetected=0, markersMatched=0,
                       err="No ArUco markers detected in snapshot")
    # Match detected markers against provided positions
    obj_points = []  # 3D stage coords
    img_points = []  # 2D pixel coords
    matched_ids = []
    flat_ids = ids.flatten()
    for i, mid in enumerate(flat_ids):
        mid_int = int(mid)
        if mid_int in marker_map:
            m = marker_map[mid_int]
            mx = float(m.get("x", 0))
            my = float(m.get("y", 0))
            mz = float(m.get("z", 0))
            # 3D corners: spread in X and Y, constant Z
            obj_pts = np.array([
                [mx - half, my + half, mz],   # top-left
                [mx + half, my + half, mz],   # top-right
                [mx + half, my - half, mz],   # bottom-right
                [mx - half, my - half, mz],   # bottom-left
            ], dtype=np.float64)
            img_pts = corners[i].reshape(4, 2).astype(np.float64)
            obj_points.append(obj_pts)
            img_points.append(img_pts)
            matched_ids.append(mid_int)
    if len(matched_ids) < 3:
        return jsonify(ok=True, markersDetected=detected_count,
                       markersMatched=len(matched_ids),
                       err=f"Only {len(matched_ids)} markers matched (need 3+)")
    # Stack all points
    obj_all = np.vstack(obj_points)  # (N*4, 3)
    img_all = np.vstack(img_points)  # (N*4, 2)
    # Estimate camera intrinsics from FOV
    fov_deg = f.get("fovDeg", 60)
    fov_rad = math.radians(fov_deg)
    fx_est = (w / 2.0) / math.tan(fov_rad / 2.0)
    fy_est = fx_est  # square pixels
    cx_est = w / 2.0
    cy_est = h / 2.0
    K = np.array([
        [fx_est, 0,      cx_est],
        [0,      fy_est, cy_est],
        [0,      0,      1     ],
    ], dtype=np.float64)
    dist_coeffs = np.zeros(4, dtype=np.float64)
    # solvePnP
    success, rvec, tvec = cv2.solvePnP(obj_all, img_all, K, dist_coeffs,
                                        flags=cv2.SOLVEPNP_ITERATIVE)
    if not success:
        return jsonify(ok=False, markersDetected=detected_count,
                       markersMatched=len(matched_ids),
                       err="solvePnP failed")
    # Compute camera position in stage coords: cam_pos = -R^T @ tvec
    R, _ = cv2.Rodrigues(rvec)
    cam_pos = (-R.T @ tvec).flatten()
    # Compute reprojection error (RMS)
    proj, _ = cv2.projectPoints(obj_all, rvec, tvec, K, dist_coeffs)
    proj = proj.reshape(-1, 2)
    err = np.sqrt(np.mean(np.sum((img_all - proj) ** 2, axis=1)))
    # Build floor-plane homography (floor is Z=0)
    # Drop Z column (column 2) of rotation matrix
    H_cam_to_floor = K @ np.column_stack([R[:, 0], R[:, 1], tvec.flatten()])
    H_floor = np.linalg.inv(H_cam_to_floor)
    # Get camera layout position for cross-validation
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    lp = pos_map.get(fid)
    camera_pos_layout = None
    if lp:
        camera_pos_layout = {"x": lp.get("x", 0), "y": lp.get("y", 0), "z": lp.get("z", 0)}
    result = {
        "ok": True,
        "markersDetected": detected_count,
        "markersMatched": len(matched_ids),
        "matchedIds": matched_ids,
        "cameraPosStage": [round(float(cam_pos[0]), 1),
                           round(float(cam_pos[1]), 1),
                           round(float(cam_pos[2]), 1)],
        "rmsError": round(float(err), 2),
        "method": "solvePnP",
        "homography": H_floor.tolist(),
        "intrinsics": {"fx": round(fx_est, 1), "fy": round(fy_est, 1),
                       "cx": round(cx_est, 1), "cy": round(cy_est, 1)},
    }
    if camera_pos_layout:
        result["cameraPos"] = camera_pos_layout
    return jsonify(result)


@app.get("/api/cameras/<int:fid>/calibration")
def api_camera_calibration_get(fid):
    """Get calibration data for a camera."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    cal = _calibrations.get(str(fid))
    if not cal:
        return jsonify(calibrated=False)
    return jsonify(calibrated=True, error=cal.get("error"),
                   points=len(cal.get("points", [])),
                   timestamp=cal.get("timestamp"))


# ── Moving head range calibration ─────────────────────────────────────

def _compute_axis_mapping(samples):
    """Fit a linear mapping from normalized DMX value (0-1) → stage position.

    Args:
        samples: list of (dmx_norm, stage_x, stage_z) tuples

    Returns:
        (offset, scale_x, scale_z) where stage_pos ≈ offset + dmx_norm * scale
    """
    if len(samples) < 2:
        return None
    norms = np.array([s[0] for s in samples])
    xs = np.array([s[1] for s in samples])
    zs = np.array([s[2] for s in samples])
    # Linear fit: stage_coord = a + b * dmx_norm
    A = np.vstack([np.ones_like(norms), norms]).T
    sol_x = np.linalg.lstsq(A, xs, rcond=None)[0]  # [intercept, slope]
    sol_z = np.linalg.lstsq(A, zs, rcond=None)[0]
    return {
        "intercept_x": float(sol_x[0]), "slope_x": float(sol_x[1]),
        "intercept_z": float(sol_z[0]), "slope_z": float(sol_z[1]),
    }


def _inverse_axis_lookup(mapping, target_x, target_z):
    """Given a linear mapping and target stage position, compute the DMX normalized value.
    Weight by abs(slope) so the axis with more signal dominates. (#259)"""
    sx, bx = mapping["intercept_x"], mapping["slope_x"]
    sz, bz = mapping["intercept_z"], mapping["slope_z"]
    vals, weights = [], []
    if abs(bx) > 0.001:
        vals.append((target_x - sx) / bx)
        weights.append(abs(bx))
    if abs(bz) > 0.001:
        vals.append((target_z - sz) / bz)
        weights.append(abs(bz))
    if not vals:
        return 0.5
    wsum = sum(v * w for v, w in zip(vals, weights))
    return max(0.0, min(1.0, wsum / sum(weights)))


@app.post("/api/fixtures/<int:fid>/calibrate-range")
def api_fixture_calibrate_range(fid):
    """Calibrate a moving head's pan/tilt range using camera observation.

    Body: {cameraId, panSamples: [{dmxNorm, pixelX, pixelY}], tiltSamples: [...]}
    The SPA wizard sweeps the head through its range, captures beam positions via
    the camera, and submits the collected samples here for processing.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    if f.get("fixtureType") != "dmx":
        return jsonify(err="Only DMX fixtures support range calibration"), 400

    body = request.get_json(silent=True) or {}
    cam_id = body.get("cameraId")
    pan_samples = body.get("panSamples", [])
    tilt_samples = body.get("tiltSamples", [])

    if not cam_id:
        return jsonify(err="cameraId required"), 400

    # Need camera calibration for pixel→stage transform
    cal = _calibrations.get(str(cam_id))
    if not cal or not cal.get("matrix"):
        return jsonify(err="Camera must be calibrated first"), 400

    H = cal["matrix"]

    # Transform pixel samples to stage coordinates
    pan_stage = []
    for s in pan_samples:
        sx, sz = _apply_homography(H, s["pixelX"], s["pixelY"])
        pan_stage.append((s["dmxNorm"], sx, sz))

    tilt_stage = []
    for s in tilt_samples:
        sx, sz = _apply_homography(H, s["pixelX"], s["pixelY"])
        tilt_stage.append((s["dmxNorm"], sx, sz))

    result = {}

    if len(pan_stage) >= 2:
        pan_map = _compute_axis_mapping(pan_stage)
        if pan_map:
            result["pan"] = pan_map
            result["panSampleCount"] = len(pan_stage)

    if len(tilt_stage) >= 2:
        tilt_map = _compute_axis_mapping(tilt_stage)
        if tilt_map:
            result["tilt"] = tilt_map
            result["tiltSampleCount"] = len(tilt_stage)

    if not result:
        return jsonify(err="Need at least 2 samples per axis"), 400

    result["timestamp"] = time.time()
    result["cameraId"] = cam_id
    _range_cal[str(fid)] = result
    _save("range_calibrations", _range_cal)

    f["rangeCalibrated"] = True
    _save("fixtures", _fixtures)

    return jsonify(ok=True, rangeCalibrated=True, result=result)


@app.post("/api/fixtures/<int:fid>/dmx-test")
def api_fixture_dmx_test(fid):
    """Send test DMX values to a fixture. Used by range calibration wizard.
    Body: {pan: 0-1, tilt: 0-1, dimmer: 0-1}"""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    body = request.get_json(silent=True) or {}
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    if not prof_info:
        return jsonify(err="Fixture has no profile"), 400
    uni = f.get("dmxUniverse", 1)
    addr = f.get("dmxStartAddr", 1)
    try:
        uni_buf = _artnet.get_universe(uni)
    except Exception:
        return jsonify(err="Art-Net engine not running"), 503
    profile = {"channel_map": prof_info.get("channel_map"),
               "channels": prof_info.get("channels", [])}
    pan = body.get("pan", 0.5)
    tilt = body.get("tilt", 0.5)
    dimmer = body.get("dimmer", 1.0)
    uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
    # Set dimmer if available
    ch_map = prof_info.get("channel_map", {})
    if "dimmer" in ch_map:
        uni_buf.set_channel(addr + ch_map["dimmer"], int(dimmer * 255))
    return jsonify(ok=True)


@app.get("/api/fixtures/<int:fid>/calibrate-range")
def api_fixture_range_cal_get(fid):
    """Get range calibration data for a fixture."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    cal = _range_cal.get(str(fid))
    if not cal:
        return jsonify(rangeCalibrated=False)
    return jsonify(rangeCalibrated=True, **cal)


def compute_pan_tilt_calibrated(fixture_id, target_pos):
    """Compute calibrated pan/tilt for a fixture aiming at target_pos.

    Returns (pan_norm, tilt_norm) 0.0-1.0 using calibration data,
    or None if fixture has no range calibration.
    """
    cal = _range_cal.get(str(fixture_id))
    if not cal:
        return None
    pan_norm = 0.5
    tilt_norm = 0.5
    if "pan" in cal:
        pan_norm = _inverse_axis_lookup(cal["pan"], target_pos[0], target_pos[2])
    if "tilt" in cal:
        tilt_norm = _inverse_axis_lookup(cal["tilt"], target_pos[0], target_pos[2])
    return (pan_norm, tilt_norm)


# ── CV Engine — orchestrator-side computer vision (#333) ──────────────

try:
    from cv_engine import CVEngine
    _cv = CVEngine()
    log.info("CVEngine loaded — beam=%s depth=%s detection=%s",
             _cv.status()["beam"], _cv.status()["depth"], _cv.status()["detection"])
except Exception as _cv_err:
    _cv = None
    log.warning("CVEngine not available: %s", _cv_err)


@app.get("/api/cv/status")
def api_cv_status():
    """Return CV engine model loading status."""
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not initialized")
    return jsonify(ok=True, **_cv.status())


@app.post("/api/cameras/<int:fid>/detect")
def api_camera_detect_local(fid):
    """Run object detection locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        detections, ms = _cv.detect_objects(
            frame, threshold=body.get("threshold", 0.5),
            classes=body.get("classes"), input_size=body.get("inputSize", 640))
        return jsonify(ok=True, detections=detections, inferenceMs=ms)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


@app.post("/api/cameras/<int:fid>/depth")
def api_camera_depth_local(fid):
    """Run depth estimation locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        fov = f.get("fovDeg", 60)
        points, ms = _cv.generate_point_cloud(
            frame, fov, max_points=body.get("maxPoints", 5000),
            max_depth_mm=body.get("maxDepthMm", 5000))
        return jsonify(ok=True, pointCount=len(points), inferenceMs=ms)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


@app.post("/api/cameras/<int:fid>/beam-detect")
def api_camera_beam_detect_local(fid):
    """Run beam detection locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        result = _cv.detect_beam(frame, cam_idx,
                                  color=body.get("color"),
                                  threshold=body.get("threshold", 30))
        return jsonify(ok=True, **result)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


# ── Stereo 3D reconstruction (#230) ──────────────────────────────────

try:
    from stereo_engine import StereoEngine
    _stereo = StereoEngine()
except ImportError:
    _stereo = None


@app.post("/api/calibration/stereo/calibrate")
def api_stereo_calibrate():
    """Build stereo engine from calibrated cameras. Requires stage-map data."""
    if _stereo is None:
        return jsonify(ok=False, err="StereoEngine not available"), 503
    body = request.get_json(silent=True) or {}
    camera_ids = body.get("cameraIds")
    # Auto-select all cameras with stage-map data if no IDs given
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"
            and f.get("cameraIp")]
    added = 0
    for cam in cams:
        fid = cam["id"]
        if camera_ids and fid not in camera_ids:
            continue
        fov = cam.get("fovDeg", 60)
        pos = None
        for p in _layout.get("children", []):
            if p.get("id") == fid:
                pos = [p.get("x", 0), p.get("y", 0), p.get("z", 0)]
                break
        if pos:
            rot = cam.get("rotation", [0, 0, 0])
            _stereo.add_camera_from_fov(str(fid), fov, 640, 480, pos, rot)
            added += 1
    return jsonify(ok=True, camerasAdded=added,
                   totalCameras=_stereo.camera_count)


@app.post("/api/calibration/stereo/triangulate")
def api_stereo_triangulate():
    """Triangulate 3D point from pixel observations across cameras."""
    if _stereo is None:
        return jsonify(ok=False, err="StereoEngine not available"), 503
    if _stereo.camera_count < 2:
        return jsonify(ok=False, err="Need at least 2 calibrated cameras"), 400
    body = request.get_json(silent=True) or {}
    observations = body.get("observations", [])
    if len(observations) < 2:
        return jsonify(ok=False, err="Need at least 2 observations"), 400
    obs_tuples = [(str(o["camId"]), o["px"], o["py"]) for o in observations]
    result = _stereo.triangulate(obs_tuples)
    if result is None:
        return jsonify(ok=False, err="Triangulation failed (parallel rays?)")
    return jsonify(ok=True, **result)


# ── Unified mover calibration (grid-based) ────────────────────────────

import mover_calibrator as _mcal

# Wire CVEngine into the calibrator for local processing (#333)
if _cv is not None:
    _mcal.set_cv_engine(_cv)

# Wire DMX engine into calibrator so it uses the engine buffer, not raw UDP (#344)
def _mcal_dmx_sender(universe_1based, start_addr, values):
    """Write DMX channels through the Art-Net/sACN engine."""
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    if not engine:
        log.warning("DMX sender: no engine running — DMX write discarded")  # #346
        return
    uni = engine.get_universe(universe_1based)
    uni.set_channels(start_addr, values)
_mcal.set_dmx_sender(_mcal_dmx_sender)

_mover_cal_jobs = {}  # fid_str → {thread, status, phase, progress, error, result}


def _best_camera_for(fixture):
    """Pick the widest-FOV positioned camera for calibration."""
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"
            and f.get("cameraIp")]  # #342 — was cameraUrl
    if not cams:
        return None
    # Prefer widest FOV
    cams.sort(key=lambda c: c.get("fovDeg") or 60, reverse=True)
    return cams[0]


def _get_bridge_ip():
    """Find the DMX Art-Net bridge IP from discovered nodes or children."""
    nodes = _artnet.discovered_nodes if hasattr(_artnet, 'discovered_nodes') and isinstance(_artnet.discovered_nodes, dict) else {}
    for ip, info in nodes.items():
        if info.get("style") == "bridge" or "giga" in info.get("longName", "").lower():
            return ip
    if nodes:
        return next(iter(nodes))
    # Fallback: DMX children
    for c in _children:
        if c.get("type") == "dmx":
            return c.get("ip")
    # Fallback: universe routes destination
    for r in _dmx_settings.get("universeRoutes", []):
        if r.get("destination"):
            return r["destination"]
    return None


def _mover_cal_thread(fid, cam, bridge_ip, mover_color):
    """Background thread: discovery → mapping → save grid."""
    job = _mover_cal_jobs[str(fid)]
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        job["error"] = "Fixture not found"
        job["status"] = "error"
        return
    addr = f.get("dmxStartAddr", 1)
    uni = f.get("dmxUniverse", 1) - 1  # Art-Net is 0-based
    cam_ip = cam.get("cameraIp", "")  # #342
    cam_idx = cam.get("cameraIdx", 0)  # #342

    # Phase 1: Discovery
    job["phase"] = "discovery"
    job["status"] = "running"
    job["progress"] = 10
    try:
        inverted = f.get("mountedInverted", False)  # #349
        # Positions live in _layout["children"], not in _fixtures
        pos_map = {p["id"]: p for p in _layout.get("children", [])}
        fp = pos_map.get(f["id"], {})
        cp = pos_map.get(cam["id"], {})
        fx_pos = [fp.get("x", 0), fp.get("y", 0), fp.get("z", 0)]
        cam_pos = [cp.get("x", 0), cp.get("y", 0), cp.get("z", 0)]

        # Compute initial aim from camera geometry in the thread (#347)
        cam_rot = cam.get("rotation", [15, 0, 0])
        cam_fov = cam.get("fovDeg", 90)
        stage_d = int(_stage.get("d", 4.0) * 1000)
        import math as _m
        _ct = cam_rot[0] if cam_rot else 15
        _fh = cam_fov / 2
        _ba = min(_ct + _fh, 89)
        _near = cam_pos[1] + cam_pos[2] / _m.tan(_m.radians(_ba)) if cam_pos[2] > 0 else 0
        _ceny = cam_pos[1] + cam_pos[2] / _m.tan(_m.radians(_ct)) if _ct > 0.1 else stage_d
        _ceny = min(_ceny, stage_d)
        _ty = _near + (_ceny - _near) * 0.67
        floor_target = [(fx_pos[0] + cam_pos[0]) / 2, _ty, 0]
        start_pan, start_tilt = _mcal.compute_initial_aim(
            fx_pos, floor_target, mounted_inverted=inverted)

        # Override with orientation/rotation if available
        orient = f.get("orientation", {})
        if orient.get("homePan") is not None:
            start_pan = orient["homePan"]
            start_tilt = orient.get("homeTilt", 0.5)
        rot = f.get("rotation", [0, 0, 0])
        if any(v != 0 for v in rot):
            aim_pt = _rotation_to_aim(rot, fx_pos)
            pt = _mcal.compute_initial_aim(fx_pos, aim_pt, mounted_inverted=inverted)
            if pt:
                start_pan, start_tilt = pt

        job["debug"] = {"fx_pos": fx_pos, "cam_pos": cam_pos, "cam_rot": cam_rot,
                        "cam_fov": cam_fov, "stage_d": stage_d, "inverted": inverted,
                        "start_pan": start_pan, "start_tilt": start_tilt,
                        "floor_target": floor_target}
        found = _mcal.discover(
            bridge_ip, cam_ip, addr, cam_idx, mover_color,
            universe=uni, mover_pos=fx_pos, camera_pos=cam_pos,
            start_pan=start_pan, start_tilt=start_tilt,
            mounted_inverted=inverted, max_probes=80,
            camera_rotation=cam_rot, camera_fov=cam_fov,
            stage_depth=stage_d)
        if not found:
            job["error"] = "Beam not found — check fixture and camera positions"
            job["status"] = "error"
            return
        job["progress"] = 30
        # discover() returns (pan, tilt, pixelX, pixelY) tuple
        found_pan, found_tilt = found[0], found[1]
        found_px, found_py = found[2], found[3]
        job["foundAt"] = {"pan": found_pan, "tilt": found_tilt,
                          "pixelX": found_px, "pixelY": found_py}
        log.info("MOVER-CAL fixture %d: beam discovered at pan=%.2f tilt=%.2f pixel=(%d,%d)",
                 fid, found_pan, found_tilt, found_px, found_py)
    except Exception as e:
        job["error"] = f"Discovery failed: {e}"
        job["status"] = "error"
        log.exception("Mover cal discovery error fid=%d", fid)
        return

    # Phase 2: BFS mapping
    job["phase"] = "mapping"
    job["progress"] = 35
    try:
        samples, boundaries = _mcal.map_visible(
            bridge_ip, cam_ip, addr, cam_idx, mover_color,
            start_pan=found_pan, start_tilt=found_tilt,
            collect_3d=False, max_samples=50)
        if len(samples) < 6:
            job["error"] = f"Only {len(samples)} samples collected — need at least 6"
            job["status"] = "error"
            return
        job["progress"] = 70
        job["sampleCount"] = len(samples)
        log.info("MOVER-CAL fixture %d: %d BFS samples collected", fid, len(samples))
    except Exception as e:
        job["error"] = f"Mapping failed: {e}"
        job["status"] = "error"
        log.exception("Mover cal mapping error fid=%d", fid)
        return

    # Phase 3: Build grid
    job["phase"] = "grid"
    job["progress"] = 80
    try:
        grid = _mcal.build_grid(samples)
        if not grid:
            job["error"] = "Grid build failed — insufficient sample spread"
            job["status"] = "error"
            return
    except Exception as e:
        job["error"] = f"Grid build failed: {e}"
        job["status"] = "error"
        return

    # Save calibration data
    cal_data = {
        "cameraId": cam["id"],
        "color": mover_color,
        "samples": samples,
        "grid": grid,
        "boundaries": boundaries,
        "sampleCount": len(samples),
        "foundAt": found,
        "centerPan": found_pan,    # calibrated pan center (#366)
        "centerTilt": found_tilt,  # calibrated tilt center (#366)
        "timestamp": time.time(),
    }
    _mover_cal[str(fid)] = cal_data
    _save("mover_calibrations", _mover_cal)
    f["moverCalibrated"] = True
    _save("fixtures", _fixtures)

    job["result"] = {"sampleCount": len(samples), "gridSize": len(grid.get("panSteps", []))}
    job["progress"] = 100
    job["status"] = "done"
    job["phase"] = "complete"
    log.info("MOVER-CAL fixture %d: calibration complete, %d samples, grid %s",
             fid, len(samples), job["result"]["gridSize"])


@app.post("/api/calibration/mover/<int:fid>/start")
def api_mover_cal_start(fid):
    """Start unified mover calibration (discovery + BFS + grid) in background."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    # Check if already running
    existing = _mover_cal_jobs.get(str(fid))
    if existing and existing.get("status") == "running":
        return jsonify(err="Calibration already running"), 409
    cam = _best_camera_for(f)
    if not cam:
        return jsonify(err="No camera available — register and position a camera first"), 400
    bridge_ip = _get_bridge_ip()
    if not bridge_ip:
        return jsonify(err="No Art-Net bridge found — start the Art-Net engine"), 400
    if not _artnet.running and not _sacn.running:  # #346
        return jsonify(err="DMX engine is not running — start it from Settings \u2192 DMX Engine"), 400
    body = request.get_json(silent=True) or {}
    color = body.get("color", [0, 255, 0])  # default green
    job = {"status": "running", "phase": "starting", "progress": 0,
           "error": None, "result": None, "cameraId": cam["id"],
           "cameraName": cam.get("name", "Camera"), "bridgeIp": bridge_ip}
    _mover_cal_jobs[str(fid)] = job
    t = threading.Thread(target=_mover_cal_thread,
                         args=(fid, cam, bridge_ip, color), daemon=True)
    job["thread"] = t
    t.start()
    return jsonify(ok=True, started=True, cameraId=cam["id"],
                   cameraName=cam.get("name"))


@app.get("/api/calibration/mover/<int:fid>/status")
def api_mover_cal_status(fid):
    """Poll calibration progress."""
    job = _mover_cal_jobs.get(str(fid))
    if not job:
        # Check for saved calibration
        cal = _mover_cal.get(str(fid))
        if cal:
            return jsonify(status="done", calibrated=True,
                           sampleCount=cal.get("sampleCount"),
                           timestamp=cal.get("timestamp"))
        return jsonify(status="none", calibrated=False)
    return jsonify(status=job["status"], phase=job.get("phase"),
                   progress=job.get("progress", 0),
                   error=job.get("error"),
                   result=job.get("result"),
                   cameraId=job.get("cameraId"),
                   foundAt=job.get("foundAt"),
                   sampleCount=job.get("sampleCount"),
                   debug=job.get("debug"))


@app.get("/api/calibration/mover/<int:fid>")
def api_mover_cal_get(fid):
    """Get saved mover calibration data."""
    cal = _mover_cal.get(str(fid))
    if not cal:
        return jsonify(calibrated=False)
    return jsonify(calibrated=True, sampleCount=cal.get("sampleCount"),
                   timestamp=cal.get("timestamp"),
                   grid=cal.get("grid") is not None,
                   cameraId=cal.get("cameraId"))


@app.delete("/api/calibration/mover/<int:fid>")
def api_mover_cal_delete(fid):
    """Delete mover calibration data."""
    if str(fid) in _mover_cal:
        del _mover_cal[str(fid)]
        _save("mover_calibrations", _mover_cal)
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if f:
        f.pop("moverCalibrated", None)
        _save("fixtures", _fixtures)
    return jsonify(ok=True)


@app.post("/api/calibration/mover/<int:fid>/aim")
def api_mover_cal_aim(fid):
    """Use calibration grid to aim a mover at a target pixel or stage position.
    Body: {targetX, targetY} (stage mm) or {pixelX, pixelY}"""
    cal = _mover_cal.get(str(fid))
    if not cal or not cal.get("grid"):
        return jsonify(err="Fixture not calibrated"), 400
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    body = request.get_json(silent=True) or {}
    grid = cal["grid"]
    # Direct pixel target
    px = body.get("pixelX")
    py = body.get("pixelY")
    if px is not None and py is not None:
        result = _mcal.grid_inverse(grid, px, py)
        if result:
            pan, tilt = result
            # Send DMX
            pid = f.get("dmxProfileId")
            prof_info = _profile_lib.channel_info(pid) if pid else None
            if prof_info:
                uni = f.get("dmxUniverse", 1)
                addr = f.get("dmxStartAddr", 1)
                try:
                    uni_buf = _artnet.get_universe(uni)
                    profile = {"channel_map": prof_info.get("channel_map"),
                               "channels": prof_info.get("channels", [])}
                    uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
                except Exception:
                    pass
            return jsonify(ok=True, pan=round(pan, 4), tilt=round(tilt, 4))
    return jsonify(err="Provide pixelX/pixelY"), 400


@app.post("/api/calibration/mover/<int:fid>/manual")
def api_mover_cal_manual(fid):
    """Save manual calibration from jog marker samples (#368).

    Body: {samples: [{pan, tilt, stageX, stageY, stageZ}, ...]}
    The grid maps pan/tilt → stage coords (not pixels), so grid_inverse
    returns pan/tilt from a stage target directly.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    body = request.get_json(silent=True) or {}
    samples = body.get("samples", [])
    if len(samples) < 2:
        return jsonify(err="Need at least 2 calibration samples"), 400

    # Build grid using stage coords as the "pixel" dimension
    grid_samples = [(s["pan"], s["tilt"], s["stageX"], s["stageY"]) for s in samples]
    grid = None
    if len(grid_samples) >= 4:
        try:
            grid = _mcal.build_grid(grid_samples)
        except Exception as e:
            log.warning("Manual calibration grid build failed: %s", e)

    avg_pan = sum(s["pan"] for s in samples) / len(samples)
    avg_tilt = sum(s["tilt"] for s in samples) / len(samples)
    avg_x = sum(s["stageX"] for s in samples) / len(samples)
    avg_y = sum(s["stageY"] for s in samples) / len(samples)
    avg_z = sum(s.get("stageZ", 0) for s in samples) / len(samples)

    # Compute boundaries from samples
    pans = [s["pan"] for s in samples]
    tilts = [s["tilt"] for s in samples]

    cal_data = {
        "method": "manual",
        "samples": samples,
        "grid": grid,
        "boundaries": {
            "panMin": round(min(pans), 3), "panMax": round(max(pans), 3),
            "tiltMin": round(min(tilts), 3), "tiltMax": round(max(tilts), 3),
            "verified": False,
        },
        "centerPan": round(avg_pan, 4),
        "centerTilt": round(avg_tilt, 4),
        "centerTarget": [round(avg_x), round(avg_y), round(avg_z)],
        "sampleCount": len(samples),
        "timestamp": time.time(),
    }
    _mover_cal[str(fid)] = cal_data
    _save("mover_calibrations", _mover_cal)
    f["moverCalibrated"] = True
    _save("fixtures", _fixtures)
    log.info("Manual calibration saved for fixture %d: %d samples, grid=%s",
             fid, len(samples), "yes" if grid else "no")
    return jsonify(ok=True, sampleCount=len(samples), hasGrid=grid is not None)


def pixel_to_pan_tilt(fixture_id, px, py):
    """Direct pixel→pan/tilt lookup using mover calibration grid.
    Returns (pan, tilt) or None if not calibrated."""
    cal = _mover_cal.get(str(fixture_id))
    if not cal or not cal.get("grid"):
        return None
    return _mcal.grid_inverse(cal["grid"], px, py)


# ── Environment point cloud ───────────────────────────────────────────

from space_mapper import SpaceScan

_space_scan = SpaceScan()
_point_cloud = _load("pointcloud", None)

@app.post("/api/space/scan")
def api_space_scan():
    """Start an async environment scan using all positioned camera sensors."""
    if _space_scan.running:
        return jsonify(err="Scan already in progress"), 409
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    if not cams:
        return jsonify(err="No camera fixtures registered"), 400
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    positioned_cams = [c for c in cams if c["id"] in pos_map]
    if not positioned_cams:
        return jsonify(err="No camera fixtures positioned on layout"), 400
    body = request.get_json(silent=True) or {}
    max_pts = body.get("maxPointsPerCamera", 10000)
    _space_scan.start(positioned_cams, pos_map, max_points_per_cam=max_pts)
    return jsonify(ok=True, pending=True, cameras=len(positioned_cams))

@app.get("/api/space/scan/status")
def api_space_scan_status():
    """Poll environment scan progress."""
    st = _space_scan.status
    if not st["running"] and st.get("result"):
        global _point_cloud
        _point_cloud = st["result"]
        _point_cloud["stageW"] = int(_stage.get("w", 3) * 1000)
        _point_cloud["stageH"] = int(_stage.get("h", 2) * 1000)
        _point_cloud["stageD"] = int(_stage.get("d", 1.5) * 1000)
        _save("pointcloud", _point_cloud)
    return jsonify(running=st["running"], progress=st["progress"],
                   message=st["message"],
                   totalPoints=st["result"]["totalPoints"] if st.get("result") else 0)

@app.get("/api/space")
def api_space_get():
    """Get the stored point cloud."""
    if not _point_cloud:
        return jsonify(ok=False, err="No environment scan available"), 404
    return jsonify(ok=True, **_point_cloud)

@app.post("/api/space/analyze")
def api_space_analyze():
    """Analyze the point cloud to detect surfaces (floor, walls, obstacles)."""
    if not _point_cloud or not _point_cloud.get("points"):
        return jsonify(err="No point cloud — run environment scan first"), 404
    from surface_analyzer import analyze_surfaces
    result = analyze_surfaces(_point_cloud["points"])
    _point_cloud["surfaces"] = result
    _save("pointcloud", _point_cloud)
    return jsonify(ok=True, **result)

@app.post("/api/space/create-objects")
def api_space_create_objects():
    """Create stage objects from detected surfaces (floor, walls, obstacles)."""
    global _nxt_obj
    if not _point_cloud or not _point_cloud.get("surfaces"):
        return jsonify(err="No surface analysis — run /api/space/analyze first"), 404
    surfaces = _point_cloud["surfaces"]
    created = []
    with _lock:
        # Floor
        floor = surfaces.get("floor")
        if floor:
            ext = floor.get("extent", {})
            w = ext.get("xMax", 0) - ext.get("xMin", 0)
            d = ext.get("zMax", 0) - ext.get("zMin", 0)
            obj = {
                "id": _nxt_obj, "name": "Floor",
                "objectType": "floor", "mobility": "static",
                "color": "#475569", "opacity": 15,
                "transform": {
                    "pos": [ext.get("xMin", 0), floor["y"], ext.get("zMin", 0)],
                    "rot": [0, 0, 0],
                    "scale": [max(w, 100), 10, max(d, 100)],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": "Floor"})
            _nxt_obj += 1

        # Walls
        for i, wall in enumerate(surfaces.get("walls", [])):
            ext = wall.get("extent", {})
            w = ext.get("xMax", 0) - ext.get("xMin", 0)
            h = ext.get("yMax", 0) - ext.get("yMin", 0)
            n = wall.get("normal", [0, 0, 1])
            # Name based on direction
            if abs(n[2]) > 0.7:
                wname = "Back Wall" if n[2] > 0 else "Front Wall"
            elif abs(n[0]) > 0.7:
                wname = "Right Wall" if n[0] > 0 else "Left Wall"
            else:
                wname = f"Wall {i+1}"
            obj = {
                "id": _nxt_obj, "name": wname,
                "objectType": "wall", "mobility": "static",
                "color": "#334155", "opacity": 10,
                "transform": {
                    "pos": [ext.get("xMin", 0), ext.get("yMin", 0), ext.get("zMin", 0)],
                    "rot": [0, 0, 0],
                    "scale": [max(w, 100), max(h, 100), 50],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": wname})
            _nxt_obj += 1

        # Obstacles
        for obs in surfaces.get("obstacles", []):
            obj = {
                "id": _nxt_obj, "name": obs.get("label", "Obstacle").title(),
                "objectType": "prop", "mobility": "static",
                "color": "#7c3aed", "opacity": 20,
                "transform": {
                    "pos": [obs["pos"][0] - obs["size"][0]//2,
                            obs["pos"][1] - obs["size"][1]//2,
                            obs["pos"][2] - obs["size"][2]//2],
                    "rot": [0, 0, 0],
                    "scale": [max(obs["size"][0], 100), max(obs["size"][1], 100),
                              max(obs["size"][2], 100)],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": obs.get("label", "Obstacle").title()})
            _nxt_obj += 1

        _save("objects", _objects)
    return jsonify(ok=True, created=created, count=len(created))

@app.get("/api/space/surfaces")
def api_space_surfaces():
    """Get detected surfaces from the last analysis."""
    if not _point_cloud or not _point_cloud.get("surfaces"):
        return jsonify(err="No surface analysis — run /api/space/analyze first"), 404
    return jsonify(ok=True, **_point_cloud["surfaces"])

@app.delete("/api/space")
def api_space_clear():
    """Clear the stored point cloud."""
    global _point_cloud
    _point_cloud = None
    _save("pointcloud", None)
    return jsonify(ok=True)


# ── Camera tracking — orchestrator proxy ──────────────────────────────

_tracking_state = {}  # {cam_fid: True/False}

@app.post("/api/cameras/<int:fid>/track/start")
def api_camera_track_start(fid):
    """Start tracking on a camera node."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    body = request.get_json(silent=True) or {}
    local_ip = _get_local_ip()
    port = request.host.split(":")[-1] if ":" in request.host else "8080"
    try:
        import urllib.request as _ur
        req_data = json.dumps({
            "cam": body.get("cam", 0),
            "orchestratorUrl": f"http://{local_ip}:{port}",
            "cameraId": fid,
            "fps": body.get("fps", 2),
            "threshold": body.get("threshold", 0.4),
            "ttl": body.get("ttl", 5),
        }).encode()
        req = _ur.Request(f"http://{ip}:5000/track/start",
                          data=req_data,
                          headers={"Content-Type": "application/json"})
        resp = _ur.urlopen(req, timeout=10)
        r = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify(err=f"Failed to start tracking: {e}"), 503
    _tracking_state[fid] = True
    return jsonify(ok=True, tracking=True)


@app.post("/api/cameras/<int:fid>/track/stop")
def api_camera_track_stop(fid):
    """Stop tracking on a camera node."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://{ip}:5000/track/stop",
                          data=b"{}",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception:
        pass  # Camera may be offline — still mark as stopped
    _tracking_state.pop(fid, None)
    return jsonify(ok=True, tracking=False)


@app.get("/api/cameras/<int:fid>/track/status")
def api_camera_track_status(fid):
    """Get tracking state for a camera."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    return jsonify(tracking=_tracking_state.get(fid, False))


def _get_local_ip():
    """Get local IP by connecting a UDP socket (no traffic sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())

# ── Camera network scan (SSH port scan for fresh SBCs) ──────────────────

_ssh_scan_state = {"pending": False, "data": []}

def _scan_ssh_devices():
    """TCP connect scan for port 22 on all local subnets. Returns SSH-accessible hosts."""
    import concurrent.futures
    try:
        local_ip = _get_local_ip()
    except Exception:
        local_ip = "192.168.1.1"
    skip_ips = {local_ip}
    for c in _children:
        skip_ips.add(c.get("ip", ""))

    def _check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            if s.connect_ex((ip, 22)) == 0:
                s.close()
                cam_info = _probe_camera(ip, timeout=0.5)
                return {"ip": ip, "hasCamera": cam_info is not None,
                        "hostname": (cam_info or {}).get("hostname", ""),
                        "fwVersion": (cam_info or {}).get("fwVersion", "")}
            s.close()
        except Exception:
            pass
        return None

    ips = []
    for prefix in _local_subnet_prefixes():
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            if ip not in skip_ips:
                ips.append(ip)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        for r in pool.map(_check, ips):
            if r:
                results.append(r)
    return results

def _ssh_scan_bg():
    try:
        _ssh_scan_state["data"] = _scan_ssh_devices()
    finally:
        _ssh_scan_state["pending"] = False

@app.get("/api/cameras/scan-network")
def api_cameras_scan_network():
    if _ssh_scan_state["pending"]:
        return jsonify(pending=True)
    _ssh_scan_state["pending"] = True
    _ssh_scan_state["data"] = []
    threading.Thread(target=_ssh_scan_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/cameras/scan-network/results")
def api_cameras_scan_network_results():
    if _ssh_scan_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_ssh_scan_state["data"])

# ── Camera deploy via SSH+SCP ───────────────────────────────────────────

_deploy_status = {"running": False, "progress": 0, "message": "", "error": None,
                  "ip": "", "remoteVersion": None, "localVersion": None}
_deploy_lock = threading.Lock()

_CAMERA_FW_FILES = ("camera_server.py", "detector.py", "depth_estimator.py",
                    "beam_detector.py", "tracker.py", "requirements.txt", "slyled-cam.service")
_github_camera_cache = {"version": None, "ts": 0}
_GITHUB_CAMERA_TTL = 3600  # 1 hour cache

def _parse_version_from_text(text):
    """Extract VERSION = "1.4.22" from camera_server.py source text."""
    import re
    m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None

def _camera_local_version():
    """Read VERSION from the local (bundled) camera_server.py source."""
    for base in [Path(getattr(sys, '_MEIPASS', '')) / "firmware" / "orangepi",
                 _FW_DIR / "orangepi"]:
        p = base / "camera_server.py"
        if p.exists():
            try:
                v = _parse_version_from_text(p.read_text(encoding="utf-8"))
                if v:
                    return v
            except Exception:
                pass
    return None

def _camera_downloaded_version():
    """Read VERSION from the downloaded (cached) camera_server.py if present."""
    p = DATA / "firmware" / "camera" / "camera_server.py"
    if p.exists():
        try:
            return _parse_version_from_text(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None

def _camera_deploy_version():
    """Return the version that would actually be deployed (downloaded > local)."""
    dl = _camera_downloaded_version()
    loc = _camera_local_version()
    if dl and loc:
        # Compare semver-style: prefer whichever is newer
        try:
            dl_t = tuple(int(x) for x in dl.split("."))
            loc_t = tuple(int(x) for x in loc.split("."))
            return dl if dl_t >= loc_t else loc
        except (ValueError, AttributeError):
            return dl
    return dl or loc

def _camera_deploy_dir():
    """Return the directory to use for camera firmware deployment.
    Prefers downloaded cache if it has a newer version than bundled."""
    dl_dir = DATA / "firmware" / "camera"
    dl_ver = _camera_downloaded_version()
    loc_ver = _camera_local_version()
    if dl_ver and dl_dir.exists() and (dl_dir / "camera_server.py").exists():
        if not loc_ver:
            return dl_dir
        try:
            dl_t = tuple(int(x) for x in dl_ver.split("."))
            loc_t = tuple(int(x) for x in loc_ver.split("."))
            if dl_t >= loc_t:
                return dl_dir
        except (ValueError, AttributeError):
            return dl_dir
    return _FW_DIR / "orangepi"

@app.get("/api/firmware/camera/check")
def api_firmware_camera_check():
    """Compare bundled vs downloaded vs GitHub latest camera firmware versions."""
    import urllib.request as _ur
    local_ver = _camera_local_version() or "0.0.0"
    dl_ver = _camera_downloaded_version()
    now = time.time()
    # Check cache first
    if _github_camera_cache["version"] and now - _github_camera_cache["ts"] < _GITHUB_CAMERA_TTL:
        latest = _github_camera_cache["version"]
    else:
        latest = None
        try:
            req = _ur.Request(
                "https://api.github.com/repos/SlyWombat/SlyLED/contents/firmware/orangepi/camera_server.py?ref=main",
                headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
            import base64
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            latest = _parse_version_from_text(content)
            if latest:
                _github_camera_cache["version"] = latest
                _github_camera_cache["ts"] = now
                log.info("GitHub camera firmware: v%s", latest)
        except Exception as e:
            log.debug("GitHub camera check failed: %s", e)
            latest = _github_camera_cache.get("version")  # stale cache
    # Determine if update is available
    update = False
    effective = dl_ver or local_ver
    if latest and effective:
        try:
            latest_t = tuple(int(x) for x in latest.split("."))
            eff_t = tuple(int(x) for x in effective.split("."))
            update = latest_t > eff_t
        except (ValueError, AttributeError):
            pass
    return jsonify(localVersion=local_ver, downloadedVersion=dl_ver,
                   latestVersion=latest, updateAvailable=update)

@app.post("/api/firmware/camera/download")
def api_firmware_camera_download():
    """Download all camera firmware files from GitHub main branch."""
    import urllib.request as _ur
    dest = DATA / "firmware" / "camera"
    dest.mkdir(parents=True, exist_ok=True)
    downloaded = []
    errors = []
    for fname in _CAMERA_FW_FILES:
        url = f"https://raw.githubusercontent.com/SlyWombat/SlyLED/main/firmware/orangepi/{fname}"
        try:
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=15)
            content = resp.read()
            (dest / fname).write_bytes(content)
            downloaded.append(fname)
        except Exception as e:
            log.warning("Failed to download %s: %s", fname, e)
            errors.append(f"{fname}: {e}")
    # Parse version from downloaded camera_server.py
    ver = _camera_downloaded_version()
    if ver:
        _github_camera_cache["version"] = ver
        _github_camera_cache["ts"] = time.time()
    log.info("Downloaded %d camera firmware files (v%s)", len(downloaded), ver)
    if errors:
        return jsonify(ok=True, version=ver, files=downloaded,
                       warnings=errors)
    return jsonify(ok=True, version=ver, files=downloaded)

def _deploy_camera_bg(ip, force=False):
    """Deploy camera_server.py to a remote SBC via SSH+SCP."""
    def _update(progress, message, error=None):
        with _deploy_lock:
            _deploy_status.update(progress=progress, message=message, error=error)
    try:
        import paramiko
    except ImportError:
        _update(0, "paramiko not installed", error="pip install paramiko")
        with _deploy_lock:
            _deploy_status["running"] = False
        return

    deploy_ver = _camera_deploy_version()
    with _deploy_lock:
        _deploy_status["localVersion"] = deploy_ver

    try:
        # ── Version check ──────────────────────────────────────────
        _update(2, "Checking remote version...")
        remote_info = _probe_camera(ip, timeout=3)
        remote_ver = remote_info.get("fwVersion") if remote_info else None
        with _deploy_lock:
            _deploy_status["remoteVersion"] = remote_ver

        if remote_ver and deploy_ver and remote_ver == deploy_ver and not force:
            _update(100, f"Already up-to-date \u2014 v{remote_ver}")
            return

        if remote_ver:
            _update(3, f"Upgrading {remote_ver} \u2192 {deploy_ver}...")
        else:
            _update(3, f"Fresh install \u2014 v{deploy_ver}...")

        # ── SSH connect ────────────────────────────────────────────
        _update(5, f"Connecting to {ip} via SSH...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Use per-node SSH credentials if available, else fall back to global
        _cam_ssh = _get_node_ssh(ip)
        user = _cam_ssh["user"]
        key_path = os.path.expanduser(_cam_ssh.get("keyPath", "")) if _cam_ssh.get("keyPath") else ""
        pw = _cam_ssh.get("password", "")

        connected = False
        # Try key auth first
        if key_path and os.path.isfile(key_path):
            try:
                ssh.connect(hostname=ip, port=22, username=user,
                            key_filename=key_path, timeout=10,
                            look_for_keys=False, allow_agent=False)
                connected = True
            except paramiko.AuthenticationException:
                pass
        # Try password auth
        if not connected and pw:
            try:
                ssh.connect(hostname=ip, port=22, username=user,
                            password=pw, timeout=10,
                            look_for_keys=False, allow_agent=False)
                connected = True
            except paramiko.AuthenticationException as e:
                if "publickey" in str(e):
                    _update(0, "Key auth required",
                            error="This device only accepts SSH key authentication. "
                                  "Generate a key pair in Camera Setup, then add the "
                                  "public key to the device's ~/.ssh/authorized_keys")
                    with _deploy_lock:
                        _deploy_status["running"] = False
                    return
                raise
        # Try default keys from agent/system
        if not connected:
            try:
                ssh.connect(hostname=ip, port=22, username=user, timeout=10)
                connected = True
            except paramiko.AuthenticationException as e:
                auth_types = str(e)
                if "publickey" in auth_types and not key_path:
                    _update(0, "Key auth required",
                            error="This device only accepts SSH key authentication. "
                                  "Generate a key pair in Camera Setup, then add the "
                                  "public key to the device's ~/.ssh/authorized_keys")
                else:
                    _update(0, "Authentication failed",
                            error=f"Could not authenticate to {ip}. "
                                  f"Check SSH credentials in Camera Setup. ({auth_types})")
                with _deploy_lock:
                    _deploy_status["running"] = False
                return

        # ── Detect if we need sudo ─────────────────────────────────
        _, stdout, _ = ssh.exec_command("id -u")
        uid = stdout.read().decode().strip()
        sudo = "" if uid == "0" else "sudo "
        log.info("Deploy: uid=%s, sudo=%s", uid, "no" if not sudo else "yes")

        # ── Pre-flight checks ──────────────────────────────────────
        _update(10, "Pre-flight checks...")
        _, stdout, _ = ssh.exec_command("python3 --version")
        py_out = stdout.read().decode().strip()
        if not py_out:
            _update(0, "Python3 not found on device", error="python3 is required")
            ssh.close()
            return

        _, stdout, _ = ssh.exec_command("which pip3")
        if not stdout.read().decode().strip():
            _update(15, "Installing pip3...")
            _, stdout, stderr = ssh.exec_command(
                f"{sudo}apt-get update -qq && {sudo}apt-get install -y -qq python3-pip", timeout=120)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode("utf-8", errors="replace")[:300]
                _update(0, "Failed to install pip3", error=err)
                ssh.close()
                return

        # ── Create target directory ────────────────────────────────
        _update(25, "Creating /opt/slyled...")
        _, stdout, _ = ssh.exec_command(f"{sudo}mkdir -p /opt/slyled/models && {sudo}chmod 777 /opt/slyled /opt/slyled/models")
        stdout.channel.recv_exit_status()

        # ── Upload firmware files ──────────────────────────────────
        _update(30, "Uploading firmware files...")
        sftp = ssh.open_sftp()
        src_dir = _camera_deploy_dir()
        log.info("Deploy: using firmware from %s", src_dir)
        for fname in _CAMERA_FW_FILES:
            src = src_dir / fname
            if src.exists():
                sftp.put(str(src), f"/opt/slyled/{fname}")
        # Upload YOLO model if present locally (check both downloaded cache and bundled)
        model_src = src_dir / "models" / "yolov8n.onnx"
        if not model_src.exists():
            model_src = _FW_DIR / "orangepi" / "models" / "yolov8n.onnx"
        if model_src.exists():
            _update(35, "Uploading detection model (~12 MB)...")
            try:
                sftp.stat("/opt/slyled/models")
            except FileNotFoundError:
                sftp.mkdir("/opt/slyled/models")
            sftp.put(str(model_src), "/opt/slyled/models/yolov8n.onnx")
        sftp.close()

        # ── Install system packages ────────────────────────────────
        _update(40, "Installing system packages...")
        _, stdout, stderr = ssh.exec_command(
            f"{sudo}apt-get install -y -qq fswebcam python3-opencv python3-numpy v4l-utils",
            timeout=120)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace")[:300]
            log.warning("apt-get partial failure (continuing): %s", err)

        # ── Install Python dependencies ────────────────────────────
        _update(50, "Installing Python dependencies...")
        _, stdout, stderr = ssh.exec_command(
            f"cd /opt/slyled && {sudo}pip3 install --break-system-packages -r requirements.txt 2>&1",
            timeout=180)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            # Try without --break-system-packages (older pip)
            _, stdout, stderr = ssh.exec_command(
                f"cd /opt/slyled && {sudo}pip3 install -r requirements.txt 2>&1", timeout=180)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode("utf-8", errors="replace")[:500]
                _update(50, "pip install failed", error=err)
                ssh.close()
                return

        # ── Verify detection model ─────────────────────────────────
        _update(60, "Checking detection model...")
        _, stdout, _ = ssh.exec_command("test -f /opt/slyled/models/yolov8n.onnx && echo EXISTS")
        if "EXISTS" in stdout.read().decode():
            _update(65, "Detection model present")
        else:
            log.warning("yolov8n.onnx not on device (not bundled locally?) — scan will be unavailable")

        # ── Install systemd service ────────────────────────────────
        _update(70, "Setting up systemd service...")
        ssh.exec_command(f"{sudo}systemctl stop slyled-cam 2>/dev/null || true")
        time.sleep(1)
        # Copy tracked service file from upload to systemd
        _, stdout, _ = ssh.exec_command(
            f"{sudo}cp /opt/slyled/slyled-cam.service /etc/systemd/system/slyled-cam.service "
            f"&& {sudo}systemctl daemon-reload && {sudo}systemctl enable slyled-cam")
        stdout.channel.recv_exit_status()

        # ── Start and verify ───────────────────────────────────────
        _update(80, "Starting camera server...")
        _, stdout, _ = ssh.exec_command(f"{sudo}systemctl start slyled-cam")
        stdout.channel.recv_exit_status()
        ssh.close()

        _update(90, "Verifying camera server...")
        # Retry probe with increasing delays — slow devices (RPi) can take 60s+
        info = None
        for attempt in range(12):
            time.sleep(5 if attempt < 3 else 10)
            _update(90 + min(attempt, 9), f"Verifying... ({(attempt+1)*5}s)")
            info = _probe_camera(ip, timeout=5)
            if info:
                break
        if info:
            new_ver = info.get("fwVersion", "?")
            if remote_ver:
                _update(100, f"Upgrade complete \u2014 v{remote_ver} \u2192 v{new_ver}")
            else:
                _update(100, f"Deploy complete \u2014 {info.get('hostname', ip)} v{new_ver} online")
        else:
            _update(100, f"\u2713 Deploy uploaded successfully. Server may still be starting on {ip}.")
    except Exception as e:
        _update(_deploy_status.get("progress", 0), "Deploy failed", error=str(e))
    finally:
        with _deploy_lock:
            _deploy_status["running"] = False

@app.post("/api/cameras/deploy")
def api_cameras_deploy():
    with _deploy_lock:
        if _deploy_status["running"]:
            return jsonify(err="Deploy already in progress"), 409
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    force = body.get("force", False)
    if not ip:
        return jsonify(err="ip required"), 400
    if not _ssh.get("sshPassword") and not _ssh.get("sshKeyPath"):
        return jsonify(err="SSH credentials not configured"), 400
    with _deploy_lock:
        _deploy_status.update(running=True, progress=0, message="Starting...",
                              error=None, ip=ip, remoteVersion=None,
                              localVersion=None)
    threading.Thread(target=_deploy_camera_bg, args=(ip, force), daemon=True).start()
    return jsonify(ok=True, pending=True)

@app.get("/api/cameras/deploy/status")
def api_cameras_deploy_status():
    with _deploy_lock:
        return jsonify(dict(_deploy_status))

# ── Camera SSH settings ─────────────────────────────────────────────────

@app.get("/api/cameras/ssh")
def api_cameras_ssh_get():
    key_path = _ssh.get("sshKeyPath", "")
    key_exists = bool(key_path and Path(os.path.expanduser(key_path)).exists())
    return jsonify({
        "sshUser": _ssh.get("sshUser", "root"),
        "hasPassword": bool(_ssh.get("sshPassword")),
        "sshKeyPath": key_path,
        "hasKey": key_exists,
    })

@app.post("/api/cameras/ssh/generate-key")
def api_cameras_ssh_generate_key():
    """Generate an Ed25519 SSH key pair for camera deployments."""
    try:
        import paramiko
    except ImportError:
        return jsonify(err="paramiko not installed"), 500
    key_dir = DATA / "ssh"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "camera_key"
    pub_file = key_dir / "camera_key.pub"

    # Generate Ed25519 key using cryptography library (paramiko wraps it)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()
    )
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH
    )
    key_file.write_bytes(priv_pem)
    key_file.chmod(0o600)

    pub_str = pub_bytes.decode("utf-8") + " slyled-camera"
    pub_file.write_text(pub_str + "\n")

    with _lock:
        _ssh["sshKeyPath"] = str(key_file)
        _save("ssh", _ssh)

    return jsonify(ok=True, publicKey=pub_str, keyPath=str(key_file))

@app.post("/api/cameras/ssh")
def api_cameras_ssh_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        if "sshUser" in body:
            _ssh["sshUser"] = body["sshUser"]
        if "sshPassword" in body:
            _ssh["sshPassword"] = _encrypt_pw(body["sshPassword"])
        if "sshKeyPath" in body:
            _ssh["sshKeyPath"] = body["sshKeyPath"]
        if "sshKeyContent" in body:
            # Save pasted key content to a managed file
            key_dir = DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            key_file = key_dir / "camera_key"
            key_file.write_text(body["sshKeyContent"])
            key_file.chmod(0o600)
            _ssh["sshKeyPath"] = str(key_file)
        _save("ssh", _ssh)
    return jsonify(ok=True)

# -- Per-camera-node SSH config (#311) -------------------------------------------
# SSH credentials keyed by camera node IP (not per sensor/fixture).
# Multiple sensors on the same Orange Pi share one SSH config.

@app.get("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_get(ip):
    """Get SSH config for a camera hardware node by IP (password masked)."""
    ssh = _camera_ssh.get(ip, {})
    return jsonify({
        "ip": ip,
        "authType": ssh.get("authType", "password"),
        "user": ssh.get("user", _ssh.get("sshUser", "root")),
        "hasPassword": bool(ssh.get("password")),
        "keyPath": ssh.get("keyPath", ""),
        "keyStored": ssh.get("keyStored", False),
        "configured": bool(ssh),
    })


@app.post("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_save(ip):
    """Save SSH config for a camera hardware node."""
    body = request.get_json(silent=True) or {}
    with _lock:
        ssh = _camera_ssh.get(ip, {})
        if "authType" in body:
            ssh["authType"] = body["authType"]
        if "user" in body:
            ssh["user"] = body["user"]
        if "password" in body:
            ssh["password"] = _encrypt_pw(body["password"]) if body["password"] else ""
        if "keyPath" in body:
            ssh["keyPath"] = body["keyPath"]
            ssh["keyStored"] = False
        if "keyContent" in body and body["keyContent"]:
            key_dir = DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            safe_ip = ip.replace(".", "_")
            key_file = key_dir / f"cam_node_{safe_ip}_key"
            key_file.write_text(body["keyContent"])
            key_file.chmod(0o600)
            ssh["keyPath"] = str(key_file)
            ssh["keyStored"] = True
        _camera_ssh[ip] = ssh
        _save("camera_ssh", _camera_ssh)
    return jsonify(ok=True)


@app.post("/api/cameras/node/<path:ip>/ssh/test")
def api_camera_node_ssh_test(ip):
    """Test SSH connection to a camera node."""
    ssh_cfg = _get_node_ssh(ip)
    try:
        import paramiko
    except ImportError:
        return jsonify(ok=False, err="paramiko not installed — run: pip install paramiko")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {"hostname": ip, "port": 22, "username": ssh_cfg["user"], "timeout": 8}
        if ssh_cfg.get("keyPath") and Path(os.path.expanduser(ssh_cfg["keyPath"])).exists():
            kwargs["key_filename"] = os.path.expanduser(ssh_cfg["keyPath"])
        elif ssh_cfg.get("password"):
            kwargs["password"] = ssh_cfg["password"]
        else:
            return jsonify(ok=False, err="No password or key configured. Enter credentials and save first.",
                           guidance="Set a password or provide an SSH key, then click Save.")
        client.connect(**kwargs)
        stdin, stdout, stderr = client.exec_command("whoami")
        user = stdout.read().decode().strip()
        client.close()
        return jsonify(ok=True, user=user, msg=f"Connected as {user}")
    except paramiko.AuthenticationException:
        return jsonify(ok=False, err="Authentication failed",
                       guidance="Check username and password, or ensure your SSH key is in the device's authorized_keys file.")
    except paramiko.SSHException as e:
        return jsonify(ok=False, err=f"SSH error: {e}",
                       guidance="Check that SSH is enabled on the device and the IP is correct.")
    except OSError as e:
        if "timed out" in str(e).lower():
            return jsonify(ok=False, err="Connection timed out",
                           guidance="Camera not responding. Check the IP address and network connectivity.")
        return jsonify(ok=False, err=f"Connection refused: {e}",
                       guidance="Check that the camera is powered on and SSH port 22 is accessible.")
    except Exception as e:
        return jsonify(ok=False, err=str(e))
    finally:
        try:
            client.close()
        except Exception:
            pass


def _get_node_ssh(ip):
    """Get SSH credentials for a camera node by IP, falling back to global _ssh."""
    ssh = _camera_ssh.get(ip, {})
    if ssh.get("password") or ssh.get("keyPath"):
        pw = ""
        if ssh.get("password"):
            try:
                pw = _decrypt_pw(ssh["password"])
            except Exception:
                pw = ""
        return {
            "user": ssh.get("user", "root"),
            "password": pw,
            "keyPath": ssh.get("keyPath", ""),
        }
    # Fall back to global SSH config
    pw = ""
    if _ssh.get("sshPassword"):
        try:
            pw = _decrypt_pw(_ssh["sshPassword"])
        except Exception:
            pw = ""
    return {
        "user": _ssh.get("sshUser", "root"),
        "password": pw,
        "keyPath": _ssh.get("sshKeyPath", ""),
    }


@app.post("/api/fixtures/<int:fid>/resolve")
def api_fixture_resolve(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    # Build resolve input from fixture + child position
    child = next((c for c in _children if c["id"] == f.get("childId")), None)
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    lp = pos_map.get(f.get("childId"), {})
    child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
    resolve_input = {
        "type": f.get("type", "linear"),
        "childPos": child_pos,
        "strings": f.get("strings", []),
        "aoeRadius": f.get("aoeRadius", 1000),
    }
    # If child has string info, merge it
    if child and not f.get("strings"):
        resolve_input["strings"] = [
            {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
            for s in child.get("strings", [])[:child.get("sc", 0)]
        ]
    result = resolve_fixture(resolve_input)
    return jsonify(result)

#  "  "  Objects (Phase 2)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

def _apply_stage_lock(s):
    """Resize a stage-locked object to match current stage dimensions (mm)."""
    sw = int(_stage["w"] * 1000)
    sh = int(_stage["h"] * 1000)
    sd = int(_stage["d"] * 1000)
    t = s.setdefault("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]})
    st = s.get("objectType", "custom")
    if st == "wall":
        t["scale"] = [sw, sh, 100]
        t["pos"] = [0, 0, 0]
    elif st == "floor":
        t["scale"] = [sw, sd + 1000, 100]
        t["pos"] = [0, 0, 0]

def _sync_locked_objects():
    """Re-apply stage dimensions to all stage-locked objects."""
    changed = False
    for s in _objects:
        if s.get("stageLocked"):
            _apply_stage_lock(s)
            changed = True
    if changed:
        _save("objects", _objects)

def _reap_temporal_objects():
    """Remove expired temporal objects."""
    now = time.time()
    global _temporal_objects
    _temporal_objects = [o for o in _temporal_objects if o.get("_expiresAt", 0) > now]

@app.get("/api/objects")
def api_objects_get():
    _reap_temporal_objects()
    return jsonify(_objects + _temporal_objects)

@app.post("/api/objects")
def api_objects_create():
    global _nxt_obj
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    with _lock:
        s = {
            "id": _nxt_obj, "name": name or f"Object {_nxt_obj}",
            "objectType": body.get("objectType", "custom"),
            "mobility": body.get("mobility", "static"),
            "filename": body.get("filename", ""),
            "color": body.get("color", "#334155"),
            "opacity": body.get("opacity", 30),
            "transform": body.get("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]}),
            "stageLocked": body.get("stageLocked", False),
        }
        if "patrol" in body and isinstance(body["patrol"], dict):
            s["patrol"] = body["patrol"]
        if s["stageLocked"]:
            _apply_stage_lock(s)
        _objects.append(s)
        _nxt_obj += 1
        _save("objects", _objects)
    return jsonify(ok=True, id=s["id"])

@app.delete("/api/objects/<int:sid>")
def api_object_delete(sid):
    global _objects, _temporal_objects
    before = len(_objects) + len(_temporal_objects)
    _objects = [s for s in _objects if s["id"] != sid]
    _temporal_objects = [s for s in _temporal_objects if s["id"] != sid]
    if len(_objects) + len(_temporal_objects) < before:
        _save("objects", _objects)
    return jsonify(ok=True)

@app.put("/api/objects/<int:oid>/pos")
def api_object_pos(oid):
    body = request.get_json(silent=True) or {}
    pos = body.get("pos")
    if not pos or not isinstance(pos, list) or len(pos) != 3:
        return jsonify(err="pos must be [x, y, z]"), 400
    with _lock:
        obj = next((o for o in _objects if o["id"] == oid), None)
        if not obj:
            obj = next((o for o in _temporal_objects if o["id"] == oid), None)
        if not obj:
            return jsonify(err="not found"), 404
        obj.setdefault("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]})["pos"] = [float(p) for p in pos]
        if obj.get("_temporal") and obj.get("ttl"):
            obj["_expiresAt"] = time.time() + obj["ttl"]
    return jsonify(ok=True)

@app.post("/api/objects/temporal")
def api_objects_temporal_create():
    global _nxt_tmp
    body = request.get_json(silent=True) or {}
    ttl = body.get("ttl")
    if not isinstance(ttl, (int, float)) or ttl <= 0:
        return jsonify(err="ttl must be > 0"), 400
    pos = body.get("pos", [0, 0, 0])
    scale = body.get("scale", [500, 1800, 500])
    with _lock:
        obj = {
            "id": _nxt_tmp, "name": body.get("name", f"Temporal {_nxt_tmp}"),
            "objectType": body.get("objectType", "prop"),
            "mobility": "moving",
            "_temporal": True,
            "ttl": ttl,
            "_expiresAt": time.time() + ttl,
            "color": body.get("color", "#FF6B35"),
            "opacity": body.get("opacity", 40),
            "transform": {"pos": [float(p) for p in pos], "rot": [0,0,0], "scale": [float(s) for s in scale]},
        }
        _temporal_objects.append(obj)
        _nxt_tmp += 1
    return jsonify(ok=True, id=obj["id"])

#  "  "  DMX Profiles  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/dmx-profiles")
def api_dmx_profiles():
    cat = request.args.get("category")
    return jsonify(_profile_lib.list_profiles(category=cat))

@app.post("/api/dmx-profiles")
def api_dmx_profile_create():
    body = request.get_json(silent=True) or {}
    ok_valid, err = _profile_lib.validate_profile(body)
    if not ok_valid:
        return jsonify(err=err), 400
    if _profile_lib.save_profile(body):
        return jsonify(ok=True, id=body["id"])
    return jsonify(err="Failed to save"), 500

# Static sub-paths BEFORE parameterized <profile_id>
@app.get("/api/dmx-profiles/export")
def api_dmx_profiles_export():
    ids = request.args.get("ids")
    category = request.args.get("category")
    id_list = [s.strip() for s in ids.split(",") if s.strip()] if ids else None
    profiles = _profile_lib.export_profiles(ids=id_list, category=category)
    return jsonify(profiles)

@app.post("/api/dmx-profiles/import")
def api_dmx_profiles_import():
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify(err="Body must be a JSON array of profiles"), 400
    result = _profile_lib.import_profiles(data)
    return jsonify(ok=True, **result)

# OFL data cache
_ofl_mfr_cache = {"data": None, "ts": 0}   # manufacturer index (name + fixtureCount)
_ofl_fix_cache = {}                          # mfr_key → [fixture dicts]
_OFL_CACHE_TTL = 3600

def _ofl_fetch_manufacturer_index():
    """Fetch manufacturer index (name + fixtureCount only, no fixture lists)."""
    import urllib.request as _ur
    now = time.time()
    if _ofl_mfr_cache["data"] and now - _ofl_mfr_cache["ts"] < _OFL_CACHE_TTL:
        return _ofl_mfr_cache["data"]
    url = "https://open-fixture-library.org/api/v1/manufacturers"
    req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
    resp = _ur.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    _ofl_mfr_cache["data"] = data
    _ofl_mfr_cache["ts"] = now
    log.info("OFL: cached %d manufacturers", len(data))
    return data

def _ofl_fetch_manufacturer_fixtures(mfr_key):
    """Fetch fixtures for a specific manufacturer (cached)."""
    import urllib.request as _ur
    if mfr_key in _ofl_fix_cache:
        return _ofl_fix_cache[mfr_key]
    url = f"https://open-fixture-library.org/api/v1/manufacturers/{mfr_key}"
    req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
    resp = _ur.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    fixtures = data.get("fixtures", [])
    _ofl_fix_cache[mfr_key] = {"name": data.get("name", mfr_key), "fixtures": fixtures}
    return _ofl_fix_cache[mfr_key]

@app.get("/api/dmx-profiles/ofl/manufacturers")
def api_ofl_manufacturers():
    """List all OFL manufacturers with fixture counts."""
    try:
        data = _ofl_fetch_manufacturer_index()
        result = []
        for mfr_key, mfr in sorted(data.items()):
            if not isinstance(mfr, dict):
                continue
            count = mfr.get("fixtureCount", 0)
            if count <= 0:
                continue
            result.append({
                "key": mfr_key,
                "name": mfr.get("name", mfr_key),
                "fixtureCount": count,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify(err=f"OFL fetch failed: {e}"), 502

@app.get("/api/dmx-profiles/ofl/manufacturer/<mfr_key>")
def api_ofl_manufacturer_fixtures(mfr_key):
    """List all fixtures for a specific manufacturer."""
    try:
        mfr_data = _ofl_fetch_manufacturer_fixtures(mfr_key)
        fixtures = mfr_data.get("fixtures", [])
        return jsonify({
            "key": mfr_key,
            "name": mfr_data.get("name", mfr_key),
            "fixtures": [{"key": f.get("key", f) if isinstance(f, dict) else f,
                          "name": f.get("name", f.get("key","?")) if isinstance(f, dict) else f.replace("-"," ").title(),
                          "categories": f.get("categories", []) if isinstance(f, dict) else []}
                         for f in fixtures],
        })
    except Exception as e:
        return jsonify(err=f"OFL fetch failed: {e}"), 502

# Full fixture index: flat list of all fixtures across all manufacturers
_ofl_full_index = {"data": None, "ts": 0}

def _ofl_build_full_index():
    """Build a flat searchable index of ALL OFL fixtures. Fetches all manufacturers."""
    import urllib.request as _ur
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    if _ofl_full_index["data"] and now - _ofl_full_index["ts"] < _OFL_CACHE_TTL:
        return _ofl_full_index["data"]
    mfr_index = _ofl_fetch_manufacturer_index()
    mfr_keys = [k for k, m in mfr_index.items()
                if isinstance(m, dict) and m.get("fixtureCount", 0) > 0]
    log.info("OFL: building full index from %d manufacturers...", len(mfr_keys))
    all_fixtures = []
    def fetch_one(mfr_key):
        try:
            data = _ofl_fetch_manufacturer_fixtures(mfr_key)
            mfr_name = data.get("name", mfr_key)
            results = []
            for f in data.get("fixtures", []):
                fkey = f.get("key", f) if isinstance(f, dict) else f
                fname = f.get("name", fkey) if isinstance(f, dict) else fkey.replace("-", " ").title()
                cats = f.get("categories", []) if isinstance(f, dict) else []
                results.append({"manufacturer": mfr_key, "manufacturerName": mfr_name,
                                "fixture": fkey, "name": fname, "categories": cats})
            return results
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in pool.map(fetch_one, mfr_keys):
            all_fixtures.extend(batch)
    _ofl_full_index["data"] = all_fixtures
    _ofl_full_index["ts"] = now
    log.info("OFL: full index built — %d fixtures from %d manufacturers", len(all_fixtures), len(mfr_keys))
    return all_fixtures

@app.get("/api/dmx-profiles/ofl/search")
def api_dmx_profiles_ofl_search():
    """Search ALL OFL fixtures by name, manufacturer, or category."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify(err="Query must be at least 2 characters"), 400
    limit = min(int(request.args.get("limit", 100)), 500)
    try:
        all_fixtures = _ofl_build_full_index()
        ql = q.lower()
        results = []
        for f in all_fixtures:
            if (ql in f["fixture"].lower() or ql in f["name"].lower()
                    or ql in f["manufacturerName"].lower() or ql in f["manufacturer"]
                    or any(ql in cat.lower() for cat in f.get("categories", []))):
                results.append(f)
                if len(results) >= limit:
                    break
        return jsonify(results)
    except Exception as e:
        return jsonify(err=f"OFL search failed: {e}"), 502

@app.get("/api/dmx-profiles/ofl/browse")
def api_dmx_profiles_ofl_browse():
    """Browse ALL OFL fixtures. Returns full index (cached). ?offset=0&limit=100."""
    offset = int(request.args.get("offset", 0))
    limit = min(int(request.args.get("limit", 100)), 500)
    try:
        all_fixtures = _ofl_build_full_index()
        page = all_fixtures[offset:offset + limit]
        return jsonify({"total": len(all_fixtures), "offset": offset, "fixtures": page})
    except Exception as e:
        return jsonify(err=f"OFL browse failed: {e}"), 502

@app.post("/api/dmx-profiles/ofl/import-by-id")
def api_dmx_profiles_ofl_import_by_id():
    """Fetch fixture(s) from OFL and import. Body: {manufacturer, fixture} or {manufacturer} for all."""
    import urllib.request as _ur
    body = request.get_json(silent=True) or {}
    manufacturer = body.get("manufacturer", "").strip()
    fixture = body.get("fixture", "").strip()
    mode_idx = body.get("mode")
    if not manufacturer:
        return jsonify(err="manufacturer required"), 400

    from ofl_importer import ofl_to_slyled
    all_profiles = []
    errors = []

    # Single fixture or all from manufacturer
    if fixture:
        fixture_keys = [fixture]
    else:
        try:
            mfr_data = _ofl_fetch_manufacturer_fixtures(manufacturer)
            raw_fixtures = mfr_data.get("fixtures", [])
            fixture_keys = [f.get("key", f) if isinstance(f, dict) else f for f in raw_fixtures]
        except Exception as e:
            return jsonify(err=f"Could not fetch manufacturer: {e}"), 502

    for fix_key in fixture_keys:
        try:
            url = f"https://open-fixture-library.org/{manufacturer}/{fix_key}.json"
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
            resp = _ur.urlopen(req, timeout=15)
            ofl_json = json.loads(resp.read().decode("utf-8"))
            profiles = ofl_to_slyled(ofl_json, mode=mode_idx)
            all_profiles.extend(profiles)
        except Exception as e:
            errors.append(f"{fix_key}: {e}")
            log.debug("OFL import %s/%s failed: %s", manufacturer, fix_key, e)

    if not all_profiles:
        return jsonify(err=f"No profiles converted. Errors: {'; '.join(errors[:5])}"), 400

    result = _profile_lib.import_profiles(all_profiles)
    resp = {"ok": True, **result,
            "profiles": [{"id": p["id"], "name": p["name"], "channels": p["channelCount"]} for p in all_profiles]}
    if errors:
        resp["warnings"] = errors[:10]
    return jsonify(resp)

@app.post("/api/dmx-profiles/ofl/import-json")
def api_dmx_profiles_ofl_import():
    """Import OFL fixture JSON directly (paste or upload)."""
    body = request.get_json(silent=True) or {}
    ofl_json = body.get("ofl") or body
    mode_idx = body.get("mode")
    if "ofl" in body:
        ofl_json = body["ofl"]
    from ofl_importer import ofl_to_slyled
    profiles = ofl_to_slyled(ofl_json, mode=mode_idx)
    if not profiles:
        return jsonify(err="Could not convert OFL fixture (no valid modes/channels)"), 400
    result = _profile_lib.import_profiles(profiles)
    return jsonify(ok=True, profiles=[p["id"] for p in profiles], **result)

# ── Community Profile Server ─────────────────────────────────────────────

@app.get("/api/dmx-profiles/community/search")
def api_community_search():
    import community_client as cc
    q = request.args.get("q", "")
    cat = request.args.get("category")
    limit = int(request.args.get("limit", 50))
    return jsonify(cc.search(q, cat, limit))

@app.get("/api/dmx-profiles/community/recent")
def api_community_recent():
    import community_client as cc
    return jsonify(cc.recent(int(request.args.get("limit", 20))))

@app.get("/api/dmx-profiles/community/popular")
def api_community_popular():
    import community_client as cc
    return jsonify(cc.popular(int(request.args.get("limit", 20))))

@app.get("/api/dmx-profiles/community/stats")
def api_community_stats():
    import community_client as cc
    return jsonify(cc.stats())

@app.post("/api/dmx-profiles/community/upload")
def api_community_upload():
    """Upload a local profile to the community server."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profileId")
    if not profile_id:
        return jsonify(ok=False, err="profileId required"), 400
    profile = _profile_lib.get_profile(profile_id)
    if not profile:
        return jsonify(ok=False, err="Profile not found locally"), 404
    # Strip builtin flag and ensure slug-safe ID
    import re
    p = {k: v for k, v in profile.items() if k != "builtin"}
    slug = re.sub(r'[^a-z0-9\-]', '-', p.get("id", "").lower())
    slug = re.sub(r'-+', '-', slug).strip('-')[:128]
    if not slug:
        return jsonify(ok=False, err="Profile ID cannot be converted to a valid slug"), 400
    p["id"] = slug
    result = cc.upload(p)
    log.info("Community upload '%s' (slug '%s'): %s", profile_id, slug, result)
    return jsonify(result)

@app.post("/api/dmx-profiles/community/download")
def api_community_download():
    """Download a community profile and import it locally."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    slug = body.get("slug", "").strip()
    if not slug:
        return jsonify(ok=False, err="slug required"), 400
    result = cc.get_profile(slug)
    if not result or not result.get("ok"):
        return jsonify(ok=False, err=result.get("error", "Fetch failed")), 502
    profile = result.get("data", result)
    if isinstance(profile, dict) and "id" in profile:
        imported = _profile_lib.import_profiles([profile])
        log.info("Community download '%s': %s", slug, imported)
        if imported.get("errors"):
            log.warning("Community download errors: %s", imported["errors"])
        return jsonify(ok=True, **imported)
    log.warning("Community download '%s': invalid data — keys=%s", slug,
                list(profile.keys()) if isinstance(profile, dict) else type(profile).__name__)
    return jsonify(ok=False, err="Invalid profile data"), 400

@app.post("/api/dmx-profiles/community/check")
def api_community_check():
    """Check if a profile would be a duplicate on the community server."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profileId")
    if not profile_id:
        return jsonify(ok=False, err="profileId required"), 400
    profile = _profile_lib.get_profile(profile_id)
    if not profile:
        return jsonify(ok=False, err="Profile not found"), 404
    import re as _re
    p = {k: v for k, v in profile.items() if k != "builtin"}
    slug = _re.sub(r'[^a-z0-9\-]', '-', p.get("id", "").lower())
    slug = _re.sub(r'-+', '-', slug).strip('-')[:128]
    if slug:
        p["id"] = slug
    return jsonify(cc.check_duplicate(p))

@app.get("/api/dmx-profiles/unified-search")
def api_unified_search():
    """Search local + community + OFL in one call."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify(err="Query must be at least 2 characters"), 400
    ql = q.lower()
    results = []
    seen = set()
    # 1. Local profiles (instant)
    for p in _profile_lib.list_profiles():
        if ql in p.get("name", "").lower() or ql in p.get("manufacturer", "").lower() or ql in p.get("id", "").lower():
            results.append({"id": p["id"], "name": p["name"], "manufacturer": p.get("manufacturer", ""),
                            "category": p.get("category", ""), "channelCount": p.get("channelCount", 0),
                            "source": "local", "builtin": p.get("builtin", False)})
            seen.add(p["id"])
    # 2. Community (fast)
    try:
        import community_client as cc
        cr = cc.search(q, limit=20)
        data = cr.get("data", cr)
        profiles = data.get("profiles", data) if isinstance(data, dict) else data
        for p in (profiles if isinstance(profiles, list) else []):
            slug = p.get("slug", "")
            if slug and slug not in seen:
                results.append({"id": slug, "name": p.get("name", slug), "manufacturer": p.get("manufacturer", ""),
                                "channelCount": int(p.get("channel_count", 0)), "source": "community"})
                seen.add(slug)
    except Exception:
        pass
    # 3. OFL (if still need more)
    if len(results) < 30:
        try:
            for f in _ofl_build_full_index():
                fk = f.get("fixture", "")
                if fk in seen: continue
                if ql in fk.lower() or ql in f.get("name", "").lower() or ql in f.get("manufacturerName", "").lower():
                    results.append({"id": fk, "name": f.get("name", fk), "manufacturer": f.get("manufacturerName", ""),
                                    "source": "ofl", "oflMfr": f.get("manufacturer", "")})
                    seen.add(fk)
                    if len(results) >= 50: break
        except Exception:
            pass
    return jsonify(results[:50])

# Parameterized routes AFTER static paths
@app.get("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_get(profile_id):
    p = _profile_lib.get_profile(profile_id)
    if not p:
        return jsonify(err="Not found"), 404
    return jsonify(p)

@app.put("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_update(profile_id):
    body = request.get_json(silent=True) or {}
    ok_upd, err = _profile_lib.update_profile(profile_id, body)
    if not ok_upd:
        p = _profile_lib.get_profile(profile_id)
        code = 400 if p else 404
        return jsonify(err=err), code
    return jsonify(ok=True)

@app.delete("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_delete(profile_id):
    if _profile_lib.delete_profile(profile_id):
        return jsonify(ok=True)
    return jsonify(err="Cannot delete (built-in or not found)"), 400

#  "  "  DMX Patch / Conflicts  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/dmx/patch")
def api_dmx_patch():
    """Return DMX address map per universe with conflict detection."""
    dmx_fixtures = [f for f in _fixtures if f.get("fixtureType") == "dmx"]
    universes = {}
    conflicts = []
    for f in dmx_fixtures:
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        count = f.get("dmxChannelCount", 1)
        if uni not in universes:
            universes[uni] = []
        entry = {"id": f["id"], "name": f.get("name", "?"), "startAddr": addr,
                 "channelCount": count, "endAddr": addr + count - 1,
                 "profileId": f.get("dmxProfileId")}
        # Check for overlaps within this universe
        for existing in universes[uni]:
            if addr <= existing["endAddr"] and existing["startAddr"] <= addr + count - 1:
                conflicts.append({
                    "universe": uni,
                    "fixtures": [existing["name"], entry["name"]],
                    "overlapStart": max(addr, existing["startAddr"]),
                    "overlapEnd": min(addr + count - 1, existing["endAddr"]),
                })
        universes[uni].append(entry)
    return jsonify(universes=universes, conflicts=conflicts,
                   totalFixtures=len(dmx_fixtures), totalConflicts=len(conflicts))

#  "  "  DMX Output Engines  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/dmx/status")
def api_dmx_status():
    return jsonify(
        artnet=_artnet.status(),
        sacn=_sacn.status(),
    )

def _apply_profile_defaults(engine):
    """Apply profile channel default values to all DMX fixtures."""
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pid = f.get("dmxProfileId")
        if not pid:
            continue
        info = _profile_lib.channel_info(pid)
        if not info:
            continue
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        uni_buf = engine.get_universe(uni)
        profile = {"channel_map": info.get("channel_map", {}), "channels": info.get("channels", [])}
        for ch in info.get("channels", []):
            default = ch.get("default")
            if default is not None and default > 0:
                offset = ch.get("offset", 0)
                bits = ch.get("bits", 8)
                if bits == 16:
                    val16 = max(0, min(65535, int(default)))
                    uni_buf.set_channel(addr + offset, val16 >> 8)
                    uni_buf.set_channel(addr + offset + 1, val16 & 0xFF)
                else:
                    uni_buf.set_channel(addr + offset, max(0, min(255, int(default))))

@app.post("/api/dmx/start")
def api_dmx_start():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol", "artnet")
    if protocol == "artnet":
        _artnet.start()
        _apply_profile_defaults(_artnet)
    elif protocol == "sacn":
        _sacn.start()
        _apply_profile_defaults(_sacn)
    else:
        return jsonify(err=f"Unknown protocol: {protocol}"), 400
    return jsonify(ok=True, protocol=protocol)

@app.post("/api/dmx/stop")
def api_dmx_stop():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol")
    if protocol == "artnet" or protocol is None:
        _artnet.stop()
    if protocol == "sacn" or protocol is None:
        _sacn.stop()
    return jsonify(ok=True)

@app.post("/api/dmx/blackout")
def api_dmx_blackout():
    _artnet.blackout()
    _sacn.blackout()
    return jsonify(ok=True)

@app.post("/api/dmx/channel")
def api_dmx_set_channel():
    """Set a single DMX channel. Body: {universe, channel, value}."""
    body = request.get_json(silent=True) or {}
    uni = body.get("universe", 1)
    ch = body.get("channel")
    val = body.get("value", 0)
    if not ch or ch < 1 or ch > 512:
        return jsonify(err="channel must be 1-512"), 400
    if _artnet.running:
        _artnet.set_channel(uni, ch, val)
    if _sacn.running:
        _sacn.set_channel(uni, ch, val)
    return jsonify(ok=True)

@app.post("/api/dmx/fixture")
def api_dmx_set_fixture():
    """Set DMX channels for a fixture by ID. Body: {fixtureId, r, g, b, dimmer}."""
    body = request.get_json(silent=True) or {}
    fid = body.get("fixtureId")
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)
    pid = fixture.get("dmxProfileId")
    profile_map = _profile_lib.channel_map(pid) if pid else None

    r = body.get("r", 0)
    g = body.get("g", 0)
    b = body.get("b", 0)
    dimmer = body.get("dimmer")

    for engine in (_artnet, _sacn):
        if engine.running:
            engine.set_fixture_rgb(uni, addr, r, g, b,
                                   {"channel_map": profile_map} if profile_map else None)
            if dimmer is not None and profile_map and "dimmer" in profile_map:
                engine.get_universe(uni).set_fixture_dimmer(
                    addr, dimmer, {"channel_map": profile_map})
    return jsonify(ok=True)

@app.get("/api/dmx/discovered")
def api_dmx_discovered():
    """Return Art-Net nodes discovered via ArtPoll. Sends a poll if engine is running."""
    if _artnet.running:
        _artnet.poll()
    else:
        # One-shot ArtPoll even when engine is stopped
        _artnet_oneshot_poll()
    return jsonify(_artnet.discovered_nodes)

def _artnet_oneshot_poll():
    """Send ArtPoll + listen for replies without starting the full engine."""
    try:
        from dmx_artnet import build_artpoll, parse_artnet_header, parse_artpoll_reply, ARTNET_PORT, OP_POLL_REPLY
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        sock.bind(("", 0))
        pkt = build_artpoll()
        # Broadcast on all common paths
        for dest in ("255.255.255.255", "192.168.10.255", "192.168.1.255", "10.0.0.255"):
            try:
                sock.sendto(pkt, (dest, ARTNET_PORT))
            except Exception:
                pass
        # Also unicast to known children with type=dmx
        for c in _children:
            if c.get("type") == "dmx" and c.get("ip"):
                try:
                    sock.sendto(pkt, (c["ip"], ARTNET_PORT))
                except Exception:
                    pass
        # Listen for replies (up to 2 seconds)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
                hdr = parse_artnet_header(data)
                if hdr and hdr[0] == OP_POLL_REPLY:
                    info = parse_artpoll_reply(data)
                    if info:
                        _artnet._discovered[info["ip"]] = info
                        log.info("ArtPoll reply from %s: %s", info["ip"], info.get("shortName"))
            except (socket.timeout, BlockingIOError, OSError):
                break
        sock.close()
    except Exception as e:
        log.debug("One-shot ArtPoll failed: %s", e)

# -- DMX Monitor (live 512-channel view) --------------------------------------

@app.get("/api/dmx/monitor/<int:uni>")
def api_dmx_monitor(uni):
    """Return all 512 channel values for a universe as a flat array."""
    for engine in (_artnet, _sacn):
        if engine.running and uni in engine._universes:
            data = engine._universes[uni].get_data()
            return jsonify({"universe": uni, "channels": list(data)})
    # No engine running or universe not created — return zeros
    return jsonify({"universe": uni, "channels": [0] * 512})

@app.post("/api/dmx/monitor/<int:uni>/set")
def api_dmx_monitor_set(uni):
    """Set individual channels. Body: {channels: [{addr: 1-512, value: 0-255}]}."""
    body = request.get_json(silent=True) or {}
    for ch in body.get("channels", []):
        addr = ch.get("addr", 0)
        val = max(0, min(255, int(ch.get("value", 0))))
        for engine in (_artnet, _sacn):
            if engine.running:
                engine.set_channel(uni, addr, val)
    return jsonify(ok=True)

# -- Fixture Group Control ----------------------------------------------------

@app.post("/api/fixtures/group/<int:gid>/control")
def api_group_control(gid):
    """Apply dimmer/color to all members of a fixture group."""
    group = next((f for f in _fixtures if f["id"] == gid and f.get("type") == "group"), None)
    if not group:
        return jsonify(err="Group not found"), 404
    body = request.get_json(silent=True) or {}
    r = body.get("r")
    g = body.get("g")
    b = body.get("b")
    dimmer = body.get("dimmer")
    member_ids = group.get("childIds", [])
    applied = 0
    for mid in member_ids:
        member = next((f for f in _fixtures if f["id"] == mid), None)
        if not member or member.get("fixtureType") != "dmx":
            continue
        uni = member.get("dmxUniverse", 1)
        addr = member.get("dmxStartAddr", 1)
        pid = member.get("dmxProfileId")
        profile_map = None
        if pid:
            prof = _profile_lib.get_profile(pid)
            if prof:
                profile_map = {}
                for ch in prof.get("channels", []):
                    profile_map[ch["type"]] = ch["offset"]
        for engine in (_artnet, _sacn):
            if engine.running:
                if r is not None and g is not None and b is not None:
                    engine.set_fixture_rgb(uni, addr, r, g, b,
                                           {"channel_map": profile_map} if profile_map else None)
                if dimmer is not None and profile_map and "dimmer" in profile_map:
                    engine.get_universe(uni).set_channel(addr + profile_map["dimmer"], dimmer)
        applied += 1
    return jsonify(ok=True, applied=applied)

# -- DMX Settings (persistent) ------------------------------------------------

_DMX_SETTINGS_DEFAULTS = {
    "protocol": "artnet",
    "frameRate": 40,
    "bindIp": "0.0.0.0",
    "universeRoutes": [],     # [{universe: int, destination: ip, label: str}]
    "sacnPriority": 100,
    "sacnSourceName": "SlyLED",
}
_dmx_settings = _load("dmx_settings", dict(_DMX_SETTINGS_DEFAULTS))
# Migrate old unicastTargets to universeRoutes
if "unicastTargets" in _dmx_settings and not _dmx_settings.get("universeRoutes"):
    _old = _dmx_settings.pop("unicastTargets", {})
    _dmx_settings["universeRoutes"] = [
        {"universe": int(k), "destination": v, "label": ""}
        for k, v in _old.items() if v
    ]

def _routes_to_unicast(routes):
    """Convert universeRoutes list to {universe_int: ip} dict for engine."""
    result = {}
    for r in (routes or []):
        uni = r.get("universe")
        dest = r.get("destination", "").strip()
        if uni is not None and dest:
            result[int(uni)] = dest
    return result

def _apply_dmx_settings():
    """Apply persisted DMX settings to engines."""
    s = _dmx_settings
    _artnet.configure(
        bind_ip=s.get("bindIp", "0.0.0.0"),
        unicast_targets=_routes_to_unicast(s.get("universeRoutes", [])),
        frame_rate=s.get("frameRate", 40),
    )
    _sacn.configure(
        source_name=s.get("sacnSourceName", "SlyLED"),
        priority=s.get("sacnPriority", 100),
        bind_ip=s.get("bindIp", "0.0.0.0"),
        frame_rate=s.get("frameRate", 40),
    )

_apply_dmx_settings()

# Auto-start DMX engine if universe routes are configured
if _dmx_settings.get("universeRoutes"):
    _proto = _dmx_settings.get("protocol", "artnet")
    _engine = _artnet if _proto == "artnet" else _sacn if _proto == "sacn" else None
    if _engine:
        try:
            _engine.start()
        except Exception as e:
            # Bind IP may be stale (DHCP changed) — retry with 0.0.0.0 (#345)
            log.warning("DMX auto-start failed on %s: %s — retrying with 0.0.0.0",
                        _dmx_settings.get("bindIp", "?"), e)
            try:
                _engine._bind_ip = "0.0.0.0"
                _engine.start()
            except Exception as e2:
                log.warning("DMX auto-start fallback also failed: %s", e2)
        if _engine.running:
            _apply_profile_defaults(_engine)
            log.info("%s auto-started (%d routes), profile defaults applied",
                     _proto.upper(), len(_dmx_settings["universeRoutes"]))

@app.get("/api/dmx/interfaces")
def api_dmx_interfaces():
    """List local network interfaces with their IPv4 addresses."""
    result = [{"name": "All Interfaces", "ip": "0.0.0.0"}]
    try:
        # Cross-platform: use socket.getaddrinfo on the hostname
        import socket as _sock
        hostname = _sock.gethostname()
        for info in _sock.getaddrinfo(hostname, None, _sock.AF_INET):
            ip = info[4][0]
            if ip and ip != "127.0.0.1" and not any(r["ip"] == ip for r in result):
                result.append({"name": hostname, "ip": ip})
        # Also try netifaces if available (gives interface names)
        try:
            import netifaces
            for iface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(iface)
                for addr_info in addrs.get(netifaces.AF_INET, []):
                    ip = addr_info.get("addr", "")
                    if ip and ip != "127.0.0.1" and not any(r["ip"] == ip for r in result):
                        result.append({"name": iface, "ip": ip})
        except ImportError:
            pass
    except Exception:
        pass
    # Fallback: probe default route
    if len(result) == 1:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            result.append({"name": "default", "ip": s.getsockname()[0]})
            s.close()
        except Exception:
            pass
    return jsonify(result)

@app.get("/api/dmx/settings")
def api_dmx_settings_get():
    return jsonify(_dmx_settings)

@app.post("/api/dmx/settings")
def api_dmx_settings_save():
    body = request.get_json(silent=True) or {}
    for k in ("protocol", "frameRate", "bindIp", "universeRoutes",
              "sacnPriority", "sacnSourceName"):
        if k in body:
            _dmx_settings[k] = body[k]
    # Remove legacy field
    _dmx_settings.pop("unicastTargets", None)
    fr = _dmx_settings.get("frameRate", 40)
    if not isinstance(fr, int) or fr < 1 or fr > 44:
        _dmx_settings["frameRate"] = 40
    pri = _dmx_settings.get("sacnPriority", 100)
    if not isinstance(pri, int) or pri < 0 or pri > 200:
        _dmx_settings["sacnPriority"] = 100
    # Validate routes
    routes = _dmx_settings.get("universeRoutes", [])
    _dmx_settings["universeRoutes"] = [
        r for r in routes
        if isinstance(r, dict) and r.get("destination")
    ]
    _save("dmx_settings", _dmx_settings)
    _apply_dmx_settings()
    return jsonify(ok=True)

# -- DMX Fixture Test ---------------------------------------------------------

@app.get("/api/dmx/fixture/<int:fid>/channels")
def api_dmx_fixture_channels(fid):
    """Return channel list for a DMX fixture (from its profile or generic)."""
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    pid = fixture.get("dmxProfileId")
    profile = _profile_lib.get_profile(pid) if pid else None
    count = fixture.get("dmxChannelCount", 3)
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)
    if profile:
        channels = [{"offset": ch["offset"], "name": ch["name"], "type": ch["type"],
                      "default": ch.get("default", 0),
                      "capabilities": ch.get("capabilities", [])}
                    for ch in profile.get("channels", [])]
    else:
        channels = [{"offset": i, "name": f"Ch {i+1}", "type": "dimmer",
                      "default": 0,
                      "capabilities": [{"range": [0, 255], "type": "Intensity", "label": f"Ch {i+1} 0-100%"}]}
                    for i in range(count)]
    # Read current values from universe buffer; fall back to profile default
    for ch in channels:
        dmx_addr = addr + ch["offset"]
        val = 0
        if _artnet.running:
            val = _artnet.get_universe(uni).get_channel(dmx_addr)
        elif _sacn.running:
            val = _sacn.get_universe(uni).get_channel(dmx_addr)
        # If engine isn't running or channel is 0, use profile default
        if val == 0 and ch.get("default", 0) > 0:
            val = ch["default"]
        ch["value"] = val
    return jsonify(universe=uni, startAddr=addr, channels=channels)

@app.post("/api/dmx/fixture/<int:fid>/test")
def api_dmx_fixture_test(fid):
    """Set channel values for testing a DMX fixture. Body: {channels: [{offset, value}]}."""
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    body = request.get_json(silent=True) or {}
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)
    for ch in body.get("channels", []):
        dmx_addr = addr + ch.get("offset", 0)
        val = max(0, min(255, int(ch.get("value", 0))))
        if _artnet.running:
            _artnet.set_channel(uni, dmx_addr, val)
        if _sacn.running:
            _sacn.set_channel(uni, dmx_addr, val)
    return jsonify(ok=True)

# -- Live fixture status (#303) -----------------------------------------------

# Action type names — must match SPA _typeNames array
_ACTION_NAMES = [
    "Blackout", "Solid", "Fade", "Breathe", "Chase", "Rainbow", "Fire",
    "Comet", "Twinkle", "Strobe", "Color Wipe", "Scanner", "Sparkle",
    "Gradient", "DMX Scene", "Pan/Tilt Move", "Gobo Select", "Color Wheel",
    "Track",
]

@app.get("/api/fixtures/live")
def api_fixtures_live():
    """Return per-fixture live output state for the dashboard status grid.

    For DMX fixtures: reads current channel values from Art-Net/sACN universe
    buffers and maps them to named parameters (r, g, b, dimmer, pan, tilt, …).

    For LED children: uses ACTION_EVENT data pushed by child nodes to report
    the current action type and step.

    Returns a list of fixture status objects, one per fixture.
    """
    running = bool(_settings.get("runnerRunning"))
    result = []
    for f in _fixtures:
        fid = f["id"]
        ft = f.get("fixtureType", "led")
        entry = {
            "id": fid,
            "name": f.get("name") or f"Fixture {fid}",
            "fixtureType": ft,
            "r": 0, "g": 0, "b": 0,
            "dimmer": 0,
            "active": False,
            "effect": None,
        }
        if ft == "dmx":
            uni_num = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            pid = f.get("dmxProfileId")
            prof_info = _profile_lib.channel_info(pid) if pid else None
            ch_map = prof_info.get("channel_map") if prof_info else None
            # Read channels from running engine
            engine = None
            if _artnet.running:
                engine = _artnet
            elif _sacn.running:
                engine = _sacn
            if engine:
                uni = engine.get_universe(uni_num)
                if ch_map:
                    if "red" in ch_map:
                        entry["r"] = uni.get_channel(addr + ch_map["red"])
                    if "green" in ch_map:
                        entry["g"] = uni.get_channel(addr + ch_map["green"])
                    if "blue" in ch_map:
                        entry["b"] = uni.get_channel(addr + ch_map["blue"])
                    if "dimmer" in ch_map:
                        entry["dimmer"] = uni.get_channel(addr + ch_map["dimmer"])
                    if "pan" in ch_map:
                        entry["pan"] = uni.get_channel(addr + ch_map["pan"])
                    if "tilt" in ch_map:
                        entry["tilt"] = uni.get_channel(addr + ch_map["tilt"])
                    if "pan-fine" in ch_map:
                        entry["panFine"] = uni.get_channel(addr + ch_map["pan-fine"])
                    if "tilt-fine" in ch_map:
                        entry["tiltFine"] = uni.get_channel(addr + ch_map["tilt-fine"])
                else:
                    # Generic RGB fixture — assume channels at start
                    count = f.get("dmxChannelCount", 3)
                    if count >= 3:
                        entry["r"] = uni.get_channel(addr)
                        entry["g"] = uni.get_channel(addr + 1)
                        entry["b"] = uni.get_channel(addr + 2)
                    if count >= 4:
                        entry["dimmer"] = uni.get_channel(addr + 3)
            entry["active"] = (entry["r"] > 0 or entry["g"] > 0
                               or entry["b"] > 0 or entry["dimmer"] > 0)
            # DMX address info for display
            entry["dmxAddr"] = f"U{uni_num}.{addr}"
        elif ft == "led":
            # LED fixtures — check live_events from child node
            cid = f.get("childId")
            child = next((c for c in _children if c["id"] == cid), None) if cid is not None else None
            entry["online"] = bool(child and child.get("status") == 1) if child else False
            if child:
                ip = child.get("ip", "")
                ev = _live_events.get(ip)
                if ev and time.time() - ev.get("ts", 0) < 30:
                    at = ev.get("actionType", 0)
                    entry["active"] = ev.get("event", 1) == 0  # 0=started
                    if at < len(_ACTION_NAMES):
                        entry["effect"] = _ACTION_NAMES[at]
                    entry["step"] = ev.get("stepIndex", 0)
                    entry["totalSteps"] = ev.get("totalSteps", 0)
        elif ft == "camera":
            entry["online"] = bool(f.get("ip"))
            continue  # cameras aren't light-emitting fixtures
        result.append(entry)
    return jsonify({"running": running, "fixtures": result})


# -- Spatial Effects (Phase 3) ------------------------------------------------

@app.get("/api/spatial-effects")
def api_sfx_get():
    return jsonify(_spatial_fx)

@app.post("/api/spatial-effects")
def api_sfx_create():
    global _nxt_sfx
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(err="Name required"), 400
    cat = body.get("category", "spatial-field")
    if cat not in ("fixture-local", "spatial-field"):
        return jsonify(err="Invalid category"), 400
    with _lock:
        fx = {"id": _nxt_sfx, "name": name, "category": cat}
        for k in ("shape", "r", "g", "b", "r2", "g2", "b2",
                  "size", "motion", "blend", "fixtureIds", "params",
                  "actionType"):
            if k in body:
                fx[k] = body[k]
        # Defaults
        fx.setdefault("shape", "sphere")
        fx.setdefault("r", 255)
        fx.setdefault("g", 255)
        fx.setdefault("b", 255)
        fx.setdefault("blend", "replace")
        fx.setdefault("size", {"radius": 1000})
        fx.setdefault("motion", {"startPos": [0,0,0], "endPos": [5000,0,0], "easing": "linear", "durationS": 5})
        fx.setdefault("fixtureIds", [])
        _spatial_fx.append(fx)
        _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True, id=fx["id"])

@app.get("/api/spatial-effects/<int:fxid>")
def api_sfx_detail(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    return jsonify(fx)

@app.put("/api/spatial-effects/<int:fxid>")
def api_sfx_update(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    for k in ("name", "category", "shape", "r", "g", "b", "r2", "g2", "b2",
              "size", "motion", "blend", "fixtureIds", "params", "actionType"):
        if k in body:
            fx[k] = body[k]
    _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True)

@app.delete("/api/spatial-effects/<int:fxid>")
def api_sfx_delete(fxid):
    global _spatial_fx
    _spatial_fx = [f for f in _spatial_fx if f["id"] != fxid]
    _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True)

@app.post("/api/spatial-effects/<int:fxid>/evaluate")
def api_sfx_evaluate(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    t = float(request.args.get("t", 0))
    # Gather pixel positions from targeted fixtures
    fix_ids = fx.get("fixtureIds", [])
    all_pixels = []
    for fid in fix_ids:
        fixture = next((f for f in _fixtures if f["id"] == fid), None)
        if fixture:
            resolved = resolve_fixture(_build_resolve_input(fixture))
            all_pixels.extend(resolved.get("pixelPositions", []))
    if not all_pixels:
        # Fall back: all fixtures
        for fixture in _fixtures:
            resolved = resolve_fixture(_build_resolve_input(fixture))
            all_pixels.extend(resolved.get("pixelPositions", []))
    colors = evaluate_spatial_effect(fx, all_pixels, t)
    return jsonify(pixels=colors)

def _build_resolve_input(fixture):
    """Build resolve input dict from a fixture record."""
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    # Look up position by fixture ID first, then fall back to childId
    lp = pos_map.get(fixture["id"], pos_map.get(fixture.get("childId"), {}))
    child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
    child = next((c for c in _children if c["id"] == fixture.get("childId")), None)
    strings = fixture.get("strings", [])
    has_leds = strings and any(s.get("leds", 0) > 0 for s in strings)
    if not has_leds and child:
        strings = [
            {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
            for s in child.get("strings", [])[:child.get("sc", 0)]
        ]
    return {
        "type": fixture.get("type", "linear"),
        "childPos": child_pos,
        "strings": strings,
        "rotation": fixture.get("rotation", [0, 0, 0]),
        "aoeRadius": fixture.get("aoeRadius", 1000),
    }

#  "  "  Timelines (Phase 4)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/timelines")
def api_timelines_get():
    return jsonify(_timelines)

@app.post("/api/timelines")
def api_timelines_create():
    global _nxt_tl
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(err="Name required"), 400
    with _lock:
        tl = {
            "id": _nxt_tl, "name": name,
            "durationS": body.get("durationS", 60),
            "tracks": body.get("tracks", []),
            "loop": body.get("loop", False),
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)
    return jsonify(ok=True, id=tl["id"])

@app.get("/api/timelines/<int:tid>")
def api_timeline_detail(tid):
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    return jsonify(tl)

@app.put("/api/timelines/<int:tid>")
def api_timeline_update(tid):
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    for k in ("name", "durationS", "tracks", "loop"):
        if k in body:
            tl[k] = body[k]
    _save("timelines", _timelines)
    return jsonify(ok=True)

@app.delete("/api/timelines/<int:tid>")
def api_timeline_delete(tid):
    global _timelines
    if not any(t["id"] == tid for t in _timelines):
        return jsonify(ok=False, err="timeline not found"), 404
    _timelines = [t for t in _timelines if t["id"] != tid]
    _save("timelines", _timelines)
    return jsonify(ok=True)

@app.post("/api/timelines/<int:tid>/frame")
def api_timeline_frame(tid):
    """Evaluate all active clips at time t, return per-fixture pixel colors."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    t = float(request.args.get("t", 0))

    # Expand allPerformers and group fixtures into per-fixture tracks
    fix_map_local = {f["id"]: f for f in _fixtures}
    raw_tracks = tl.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            for f in _fixtures:
                if f.get("type") != "group":
                    tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
            # Expand group fixtures to their members
            fid = track.get("fixtureId")
            grp = fix_map_local.get(fid)
            if grp and grp.get("type") == "group" and grp.get("childIds"):
                for mid in grp["childIds"]:
                    if mid in fix_map_local:
                        tracks.append({"fixtureId": mid, "clips": list(track.get("clips", []))})
                continue
            tracks.append(track)

    result = {}  # fixture_id  -' [r,g,b] array
    for track in tracks:
        fix_id = track.get("fixtureId")
        fixture = next((f for f in _fixtures if f["id"] == fix_id), None)
        if not fixture:
            continue

        # Resolve pixel positions for this fixture
        resolved = resolve_fixture(_build_resolve_input(fixture))
        pixels = resolved.get("pixelPositions", [])
        if not pixels:
            continue

        # Find active clips at time t
        layers = []
        modes = []
        for clip in track.get("clips", []):
            cs = clip.get("startS", 0)
            cd = clip.get("durationS", 1)
            if cs <= t < cs + cd:
                # Handle classic action clips   " fill all pixels with action color
                aid = clip.get("actionId")
                if aid is not None:
                    act = next((a for a in _actions if a["id"] == aid), None)
                    if act:
                        col = [act.get("r", 0), act.get("g", 0), act.get("b", 0)]
                        layers.append([col] * len(pixels))
                        modes.append("replace")
                    continue
                # Get the spatial effect
                eid = clip.get("effectId")
                fx = next((f for f in _spatial_fx if f["id"] == eid), None)
                if not fx:
                    continue
                local_t = t - cs
                # Scale local_t to effect's motion duration
                motion = fx.get("motion", {})
                fx_dur = motion.get("durationS", cd) or cd
                scaled_t = local_t * (fx_dur / cd) if cd > 0 else 0
                colors = evaluate_spatial_effect(fx, pixels, scaled_t)
                layers.append(colors)
                modes.append(fx.get("blend", "replace"))

        if layers:
            blended = blend_pixel_layers(layers, modes)
            result[str(fix_id)] = blended
        else:
            result[str(fix_id)] = [[0,0,0]] * len(pixels)

    return jsonify(result)

#  "  "  Baking (Phase 5)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/timelines/<int:tid>/bake")
def api_timeline_bake(tid):
    """Start baking a timeline (background thread)."""
    global _bake_progress
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    if _bake_progress and not _bake_progress.done:
        return jsonify(err="Bake already in progress"), 409

    n_frames = int(math.ceil(tl.get("durationS", 60) * 40))
    _bake_progress = BakeProgress(n_frames)

    # Pre-enrich fixtures with child string data so the bake engine can resolve pixels
    enriched_fixtures = []
    for f in _fixtures:
        ef = dict(f)
        fix_strings = ef.get("strings", [])
        has_leds = fix_strings and any(s.get("leds", 0) > 0 for s in fix_strings)
        if not has_leds:
            child = next((c for c in _children if c["id"] == ef.get("childId")), None)
            if child:
                ef["strings"] = [
                    {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
                    for s in child.get("strings", [])[:child.get("sc", 0)]
                ]
        enriched_fixtures.append(ef)

    log.info("BAKE: timeline %d '%s' dur=%ds frames=%d fixtures=%d clips=%d effects=%d",
             tid, tl.get("name"), tl.get("durationS", 0), n_frames, len(enriched_fixtures),
             sum(len(t.get("clips", [])) for t in tl.get("tracks", [])),
             len(_spatial_fx))
    for ef in enriched_fixtures:
        ft = ef.get("fixtureType", "led")
        strings = ef.get("strings", [])
        leds = sum(s.get("leds", 0) for s in strings)
        log.info("  fixture %d '%s' type=%s strings=%d leds=%d rot=%s pos=(%s,%s)",
                 ef.get("id"), ef.get("name"), ft, len(strings), leds,
                 ef.get("rotation"), ef.get("x", "?"), ef.get("y", "?"))
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    placed = [f for f in enriched_fixtures if f["id"] in pos_map]
    log.info("BAKE: %d/%d fixtures have layout positions", len(placed), len(enriched_fixtures))

    def _bake_thread():
        global _bake_result
        try:
            result = bake_timeline(
                tl, enriched_fixtures, _spatial_fx, _layout,
                resolve_fn=resolve_fixture,
                evaluate_fn=evaluate_spatial_effect,
                blend_fn=blend_pixel_layers,
                progress=_bake_progress,
                actions=_actions,
                profile_lib=_profile_lib,
                mover_calibrations=_mover_cal,
            )
            n_fix = len(result.get("fixtures", {}))
            n_frames_out = result.get("totalFrames", 0)
            lsq_size = sum(len(v) for v in result.get("lsq_files", {}).values())
            preview_keys = list(result.get("preview", {}).keys())
            log.info("BAKE DONE: %d fixtures, %d frames, %d LSQ bytes, preview keys=%s",
                     n_fix, n_frames_out, lsq_size, preview_keys[:5])
            # Store result
            _bake_result[tid] = {
                "timelineId": tid,
                "bakedAt": int(time.time()),
                "fixtures": result["fixtures"],
                "totalFrames": result["totalFrames"],
                "fps": result["fps"],
                "lsqSize": lsq_size,
                "preview": result.get("preview", {}),
            }
            # Save LSQ files to data/baked/
            baked_dir = DATA / "baked"
            baked_dir.mkdir(parents=True, exist_ok=True)
            for fix_id, lsq_data in result.get("lsq_files", {}).items():
                (baked_dir / f"fixture_{fix_id}.lsq").write_bytes(lsq_data)
            zip_data = pack_lsq_zip(result.get("lsq_files", {}))
            (baked_dir / f"timeline_{tid}.zip").write_bytes(zip_data)
        except Exception as e:
            import traceback
            log.error("BAKE FAILED: %s\n%s", e, traceback.format_exc())
            _bake_progress.error = str(e)
            _bake_progress.done = True

    threading.Thread(target=_bake_thread, daemon=True).start()
    return jsonify(ok=True, message="Bake started")

@app.get("/api/timelines/<int:tid>/baked/status")
def api_bake_status(tid):
    if not _bake_progress:
        return jsonify(running=False, done=False, progress=0)
    return jsonify(_bake_progress.to_dict())

@app.get("/api/timelines/<int:tid>/baked")
def api_bake_result(tid):
    result = _bake_result.get(tid)
    if not result:
        return jsonify(err="No baked data for this timeline"), 404
    return jsonify(result)

@app.get("/api/timelines/<int:tid>/baked/download")
def api_bake_download(tid):
    zip_path = DATA / "baked" / f"timeline_{tid}.zip"
    if not zip_path.exists():
        return jsonify(err="No baked data"), 404
    return send_file(str(zip_path), mimetype="application/zip",
                     as_attachment=True, download_name=f"timeline_{tid}_lsq.zip")

@app.get("/api/timelines/<int:tid>/baked/preview")
def api_bake_preview(tid):
    result = _bake_result.get(tid)
    if not result:
        log.debug("PREVIEW: no bake result for timeline %d (available: %s)", tid, list(_bake_result.keys()))
        return jsonify(err="No baked data"), 404
    preview = result.get("preview", {})
    log.debug("PREVIEW: timeline %d -> %d fixture keys, sample: %s",
              tid, len(preview), list(preview.keys())[:3])
    return jsonify(preview)

# Sync progress   " tracks per-child sync state for UI polling
_sync_progress = None  # dict when active

@app.post("/api/timelines/<int:tid>/baked/sync")
def api_bake_sync(tid):
    """Sync baked segments to all children. Runs in background with progress tracking."""
    global _sync_progress
    result = _bake_result.get(tid)
    if not result:
        return jsonify(err="No baked data - bake first"), 404

    targets = [c for c in _children if c.get("ip")]
    if not targets:
        return jsonify(ok=True, synced=0, warn="no performers registered")

    # Build per-child sync plan
    plan = []  # [{child, steps, fixture_name}]
    for fix_id_str, fix_data in result.get("fixtures", {}).items():
        fix_id = int(fix_id_str) if isinstance(fix_id_str, str) else fix_id_str
        fixture = next((f for f in _fixtures if f["id"] == fix_id), None)
        if not fixture:
            continue
        child = next((c for c in targets if c["id"] == fixture.get("childId")), None)
        if not child:
            continue
        segments = fix_data.get("segments", [])
        fix_strings = fixture.get("strings", [])
        steps = []
        # Per-pixel effect types where speedMs = time per pixel step
        PER_PIXEL_TYPES = {4, 7, 10, 11}  # CHASE, COMET, WIPE, SCANNER
        # Directional effect types (use direction param)
        DIR_TYPES = {4, 5, 7, 10, 11}  # CHASE, RAINBOW, COMET, WIPE, SCANNER
        # Direction flip map: E -"W, N -"S
        DIR_FLIP = {0: 2, 1: 3, 2: 0, 3: 1}
        REF_PITCH_MM = 16.67  # 60 LEDs/m reference density
        for seg in segments[:16]:
            step = dict(seg.get("params", {}))
            step["type"] = seg.get("type", 0)
            step["durationS"] = max(1, int(math.ceil(seg.get("durationS", 1))))
            # Per-string LED range override from bake
            if "ledOffset" in seg:
                step["_ledOffset"] = seg["ledOffset"]
                step["_ledCount"] = seg["ledCount"]
                step["_stringIndex"] = seg.get("stringIndex", 0)
            si = seg.get("stringIndex", 0)
            sinfo = fix_strings[si] if si < len(fix_strings) else {}
            # Map action direction to string physical direction:
            # if string faces W or S, flip the effect direction so the
            # visual sweep matches physical orientation
            if step["type"] in DIR_TYPES:
                sdir = sinfo.get("sdir", 0)
                if sdir in (2, 3):  # West or South   " flip direction
                    step["direction"] = DIR_FLIP.get(step.get("direction", 0), 0)
            # Normalize speedMs for per-pixel effects so physical speed is
            # consistent regardless of LED density (50 LEDs/1m = 150 LEDs/1m)
            if step["type"] in PER_PIXEL_TYPES and step.get("speedMs", 0) > 0:
                leds = sinfo.get("leds", 0)
                mm = sinfo.get("mm", 0)
                if leds > 0 and mm > 0:
                    pitch = mm / leds
                    step["speedMs"] = max(1, round(step["speedMs"] * pitch / REF_PITCH_MM))
            steps.append(step)
        # Append final blackout so LEDs turn off when the show ends
        if steps and steps[-1].get("type", 0) != 0 and len(steps) < 16:
            steps.append({"type": 0, "durationS": 1, "r": 0, "g": 0, "b": 0})
        if steps:
            plan.append({"child": child, "steps": steps, "name": fixture.get("name", "?")})

    # Initialize progress
    _sync_progress = {
        "done": False, "allReady": False,
        "performers": {p["child"]["id"]: {
            "name": p.get("name") or p["child"].get("name") or p["child"].get("hostname"),
            "ip": p["child"]["ip"],
            "status": "pending", "stepsLoaded": 0, "totalSteps": len(p["steps"]),
            "retries": 0, "verified": False, "error": None
        } for p in plan},
        "totalPerformers": len(plan), "readyCount": 0,
    }

    def _sync_thread():
        MAX_RETRIES = 3
        # Stop any running show first   " both on children and server state
        pkt_stop = _hdr(CMD_RUNNER_STOP)
        pkt_off = _hdr(CMD_ACTION_STOP)
        for c in _children:
            if c.get("ip"):
                _send(c["ip"], pkt_stop)
                _send(c["ip"], pkt_off)
        with _lock:
            _settings["runnerRunning"] = False
            _settings["activeTimeline"] = -1
            _save("settings", _settings)
        time.sleep(0.15)

        bri = _settings.get("globalBrightness", 255)
        bri_pkt = _hdr(CMD_SET_BRIGHTNESS) + bytes([bri & 0xFF])

        for p in plan:
            child = p["child"]
            cid = child["id"]
            steps = p["steps"]
            ip = child["ip"]
            prog = _sync_progress["performers"][cid]
            prog["status"] = "syncing"

            _send(ip, bri_pkt)
            time.sleep(0.02)

            # Send each step with retry
            all_ok = True
            for idx, step in enumerate(steps):
                pkt = _load_step_pkt(idx, len(steps), step, child, 0)
                sent = False
                for attempt in range(MAX_RETRIES):
                    _send(ip, pkt)
                    time.sleep(0.04)
                    # Simple verification: send and trust (LOAD_ACK comes async via UDP listener)
                    sent = True
                    break
                if sent:
                    prog["stepsLoaded"] = idx + 1
                else:
                    prog["error"] = f"Step {idx} failed after {MAX_RETRIES} retries"
                    all_ok = False
                    break

            if all_ok:
                prog["status"] = "verifying"
                # Verify child is alive via HTTP /status (more reliable than UDP)
                verified = False
                for attempt in range(MAX_RETRIES):
                    try:
                        import urllib.request
                        resp = urllib.request.urlopen(f"http://{ip}/status", timeout=3)
                        if resp.status == 200:
                            verified = True
                            break
                    except Exception:
                        pass
                    prog["retries"] = attempt + 1
                    time.sleep(0.2)
                # If HTTP failed, still consider it loaded (steps were sent successfully)
                if not verified and prog["stepsLoaded"] == prog["totalSteps"]:
                    verified = True
                    prog["status"] = "ready"
                    log.info("SYNC: %s HTTP verify failed but all steps loaded - accepting", ip)
                prog["verified"] = verified
                prog["status"] = "ready" if verified else "unverified"
                if verified:
                    _sync_progress["readyCount"] = _sync_progress.get("readyCount", 0) + 1
            else:
                prog["status"] = "failed"

        _sync_progress["done"] = True
        _sync_progress["allReady"] = _sync_progress["readyCount"] == len(plan)

    threading.Thread(target=_sync_thread, daemon=True).start()
    return jsonify(ok=True, performers=len(plan))

@app.get("/api/timelines/<int:tid>/sync/status")
def api_sync_status(tid):
    if not _sync_progress:
        return jsonify(done=False, performers={})
    return jsonify(_sync_progress)

#  "  "  Show Execution (Phase 6)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_dmx_playback_stop = threading.Event()

_PATROL_SPEED_PRESETS = {"slow": 20.0, "medium": 10.0, "fast": 5.0}

def _evaluate_object_patrols(elapsed):
    """Update positions of patrolling objects based on elapsed playback time.

    Motion patterns:
      pingpong — oscillate back and forth along axis (default)
      circle   — circular motion in the horizontal plane (XY)
      figure8  — figure-8 pattern in the horizontal plane (XY)
      square   — rectangular path along the perimeter of the range

    Bounding box: if patrol.boundingObject is set to another object's name,
    the patrol range is derived from that object's transform (pos + scale)
    instead of using startPct/endPct of the stage dimensions.
    """
    sw = _stage.get("w", 10) * 1000  # stage width in mm (X)
    sd = _stage.get("d", 10) * 1000  # stage depth in mm (Y)
    sh = _stage.get("h", 5) * 1000   # stage height in mm (Z)
    dims = {"x": sw, "y": sd, "z": sh}
    all_objs = _objects + _temporal_objects
    # Build name→object lookup for bounding box references
    obj_by_name = {o.get("name", ""): o for o in all_objs if o.get("name")}

    for obj in all_objs:
        if obj.get("mobility") != "moving":
            continue
        pat = obj.get("patrol")
        if not pat or not pat.get("enabled"):
            continue
        preset = pat.get("speedPreset", "medium")
        cycle_s = _PATROL_SPEED_PRESETS.get(preset, pat.get("cycleS", 10.0))
        if cycle_s <= 0:
            continue
        easing = pat.get("easing", "sine")
        pattern = pat.get("pattern", "pingpong")

        # Phase: 0→1 over one full cycle
        phase = (elapsed % cycle_s) / cycle_s

        # Determine bounding range — either from a named bounding object or stage %
        bound_obj_name = pat.get("boundingObject", "")
        if bound_obj_name and bound_obj_name in obj_by_name:
            # Use the bounding object's transform as the motion range
            bo = obj_by_name[bound_obj_name]
            bt = bo.get("transform", {})
            bp = bt.get("pos", [0, 0, 0])
            bs = bt.get("scale", [1000, 1000, 1000])
            x_lo, x_hi = bp[0], bp[0] + bs[0]
            y_lo, y_hi = bp[1], bp[1] + bs[1]
            z_lo, z_hi = bp[2], bp[2] + bs[2]
        else:
            start_pct = pat.get("startPct", 10) / 100.0
            end_pct = pat.get("endPct", 90) / 100.0
            x_lo, x_hi = sw * start_pct, sw * end_pct
            y_lo, y_hi = sd * start_pct, sd * end_pct
            z_lo, z_hi = 0, 0  # floor level for horizontal patterns

        # Center and half-size for circular/figure-8 patterns
        cx = (x_lo + x_hi) / 2.0
        cy = (y_lo + y_hi) / 2.0
        rx = (x_hi - x_lo) / 2.0
        ry = (y_hi - y_lo) / 2.0

        pos = obj.get("transform", {}).get("pos", [0, 0, 0])
        new_pos = list(pos)

        if pattern == "circle":
            # Circular motion in XY plane
            angle = phase * 2.0 * math.pi
            if easing == "sine":
                angle = phase * 2.0 * math.pi  # already smooth for circle
            new_pos[0] = cx + rx * math.cos(angle)
            new_pos[1] = cy + ry * math.sin(angle)

        elif pattern == "figure8":
            # Figure-8 (lissajous): X has 1x frequency, Y has 2x frequency
            angle = phase * 2.0 * math.pi
            new_pos[0] = cx + rx * math.sin(angle)
            new_pos[1] = cy + ry * math.sin(2.0 * angle)

        elif pattern == "square":
            # Rectangular perimeter path: 4 equal segments
            # Segment 0: left→right (bottom), 1: bottom→top (right),
            # 2: right→left (top), 3: top→bottom (left)
            seg = int(phase * 4) % 4
            seg_t = (phase * 4) % 1.0
            if easing == "sine":
                seg_t = 0.5 - 0.5 * math.cos(seg_t * math.pi)
            if seg == 0:
                new_pos[0] = x_lo + seg_t * (x_hi - x_lo)
                new_pos[1] = y_lo
            elif seg == 1:
                new_pos[0] = x_hi
                new_pos[1] = y_lo + seg_t * (y_hi - y_lo)
            elif seg == 2:
                new_pos[0] = x_hi - seg_t * (x_hi - x_lo)
                new_pos[1] = y_hi
            else:
                new_pos[0] = x_lo
                new_pos[1] = y_hi - seg_t * (y_hi - y_lo)

        else:
            # Default: pingpong — back-and-forth along axis
            t = 1.0 - abs(2.0 * phase - 1.0)  # triangle wave 0→1→0
            if easing == "sine":
                t = 0.5 - 0.5 * math.cos(t * math.pi)
            axis = pat.get("axis", "x")
            for ax in (list(axis) if len(axis) > 1 else [axis]):
                dim = dims.get(ax, sw)
                start_pct = pat.get("startPct", 10) / 100.0
                end_pct = pat.get("endPct", 90) / 100.0
                lo = dim * start_pct
                hi = dim * end_pct
                if bound_obj_name and bound_obj_name in obj_by_name:
                    lo = {"x": x_lo, "y": y_lo, "z": z_lo}.get(ax, lo)
                    hi = {"x": x_hi, "y": y_hi, "z": z_hi}.get(ax, hi)
                idx = {"x": 0, "y": 1, "z": 2}.get(ax, 0)
                new_pos[idx] = lo + t * (hi - lo)

        obj.setdefault("transform", {})["pos"] = new_pos

def _evaluate_track_actions(elapsed, engine, dmx_fixtures):
    """Evaluate active Track actions -- compute real-time pan/tilt for moving heads."""
    track_actions = [a for a in _actions if a.get("type") == 18]
    if not track_actions:
        return
    all_objects = _objects + _temporal_objects
    moving_objects = [o for o in all_objects if o.get("mobility") == "moving"]
    if not moving_objects:
        return
    # Build fixture lookup: id -> fixture info (with profile pan/tilt range)
    fx_lookup = {}
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pid = f.get("dmxProfileId")
        prof = _profile_lib.get_profile(pid) if pid else None
        pan_range = prof.get("panRange", 0) if prof else 0
        tilt_range = prof.get("tiltRange", 0) if prof else 0
        if pan_range > 0 and tilt_range > 0:
            fx_lookup[f["id"]] = {
                "fixture": f, "pan_range": pan_range, "tilt_range": tilt_range,
                "prof_info": _profile_lib.channel_info(pid) if pid else None
            }
    if not fx_lookup:
        return
    for ta in track_actions:
        # Resolve target objects
        target_ids = ta.get("trackObjectIds", [])
        targets = [o for o in moving_objects if o["id"] in target_ids] if target_ids else moving_objects
        if not targets:
            continue
        # Resolve fixtures
        fix_ids = ta.get("trackFixtureIds", [])
        heads = [fx_lookup[fid] for fid in (fix_ids or fx_lookup.keys()) if fid in fx_lookup]
        if not heads:
            continue
        # Global offset
        g_off = ta.get("trackOffset", [0, 0, 0])
        per_fx_off = ta.get("trackFixtureOffsets", {})
        auto_spread = ta.get("trackAutoSpread", False)
        cycle_ms = ta.get("trackCycleMs", 2000)
        cycle_s = max(cycle_ms / 1000.0, 0.1)
        n_heads = len(heads)
        n_targets = len(targets)
        for hi, head_info in enumerate(heads):
            f = head_info["fixture"]
            fid = f["id"]
            fx_pos = [f.get("x", 0), f.get("y", 0), f.get("z", 0)]
            # Assignment
            if n_heads <= n_targets:
                # Cycling: this head covers a chunk of targets
                chunk_size = max(1, n_targets // n_heads)
                chunk_start = hi * chunk_size
                chunk = targets[chunk_start:chunk_start + chunk_size]
                if hi == n_heads - 1:
                    chunk = targets[chunk_start:]  # last head gets remainder
                if len(chunk) > 1:
                    idx = int(elapsed / cycle_s) % len(chunk)
                    obj = chunk[idx]
                else:
                    obj = chunk[0]
            else:
                # More heads than targets: spread heads across targets
                obj = targets[hi % n_targets]
            obj_pos = obj.get("transform", {}).get("pos", [0, 0, 0])
            # Apply offsets
            p_off = per_fx_off.get(str(fid), [0, 0, 0])
            # Auto-spread when multiple heads on same object
            spread_off = [0, 0, 0]
            if auto_spread and n_heads > n_targets:
                heads_on_this = n_heads // n_targets + (1 if hi % n_targets < n_heads % n_targets else 0)
                if heads_on_this > 1:
                    obj_w = obj.get("transform", {}).get("scale", [500, 1800, 500])[0]
                    local_idx = (hi // n_targets)
                    spread_off[0] = (local_idx - (heads_on_this - 1) / 2.0) * obj_w / max(heads_on_this, 1)
            aim = [obj_pos[i] + g_off[i] + p_off[i] + spread_off[i] for i in range(3)]
            # Clamp to stage bounds
            sw = _stage.get("w", 10) * 1000
            sh = _stage.get("h", 5) * 1000
            sd = _stage.get("d", 10) * 1000
            aim[0] = max(0, min(sw, aim[0]))
            aim[1] = max(0, min(sh, aim[1]))
            aim[2] = max(0, min(sd, aim[2]))
            # Compute pan/tilt — use calibrated mapping if available, else geometric
            pt_cal = compute_pan_tilt_calibrated(f["id"], aim)
            if pt_cal:
                pan, tilt = pt_cal
            else:
                pt = compute_pan_tilt(fx_pos, aim, head_info["pan_range"], head_info["tilt_range"])
                if pt is None:
                    continue
                pan, tilt = pt
            # Write to DMX universe
            prof_info = head_info["prof_info"]
            if prof_info:
                profile = {"channel_map": prof_info.get("channel_map"), "channels": prof_info.get("channels", [])}
                uni_buf = engine.get_universe(f.get("dmxUniverse", 1))
                uni_buf.set_fixture_pan_tilt(f.get("dmxStartAddr", 1), pan, tilt, profile)

def _dmx_playback_loop(tid, go_epoch, duration, loop):
    """Background thread: stream DMX channel data during show playback."""
    result = _bake_result.get(tid)
    if not result:
        log.warning("DMX playback: no bake result for timeline %d", tid)
        return
    baked_fixtures = result.get("fixtures", {})
    # Collect DMX fixtures with their baked segments
    dmx_fixtures = []
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f["id"]
        # Bake result keys can be int or str depending on JSON round-trip
        fix_data = baked_fixtures.get(fid) or baked_fixtures.get(str(fid), {})
        segs = fix_data.get("segments", [])
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        pid = f.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        ch_map = prof_info.get("channel_map") if prof_info else None
        channels = prof_info.get("channels", []) if prof_info else []
        log.info("DMX playback: fixture %d '%s' uni=%d addr=%d segs=%d profile=%s",
                 fid, f.get("name", "?"), uni, addr, len(segs), pid or "none")
        if not segs:
            log.warning("DMX playback: fixture %d has 0 segments - skipping", fid)
            continue
        dmx_fixtures.append({"fid": fid, "name": f.get("name", "?"),
                             "uni": uni, "addr": addr, "ch_map": ch_map,
                             "channels": channels, "segs": segs})
    if not dmx_fixtures:
        log.warning("DMX playback: no DMX fixtures with segments found")
        return
    log.info("DMX playback: %d fixture(s), duration=%ds, loop=%s", len(dmx_fixtures), duration, loop)
    # Auto-start Art-Net engine if not running
    proto = _dmx_settings.get("protocol", "artnet")
    engine = _artnet if proto == "artnet" else _sacn
    if not engine.running:
        engine.start()
        log.info("DMX playback: auto-started %s engine", proto)
    # Wait until go_epoch
    wait = go_epoch - time.time()
    if wait > 0:
        _dmx_playback_stop.wait(timeout=wait)
    if _dmx_playback_stop.is_set():
        return
    # 40Hz playback loop
    interval = 0.025
    next_frame = time.monotonic()
    frame_count = 0
    while not _dmx_playback_stop.is_set():
        now_mono = time.monotonic()
        if now_mono < next_frame:
            _dmx_playback_stop.wait(timeout=next_frame - now_mono)
            if _dmx_playback_stop.is_set():
                break
            continue
        next_frame += interval
        if next_frame < now_mono:
            next_frame = now_mono + interval
        elapsed = time.time() - go_epoch
        if elapsed < 0:
            continue
        if loop and duration > 0:
            elapsed = elapsed % duration
        elif elapsed > duration:
            break  # show ended
        # Evaluate each DMX fixture — merge ALL matching segments per-channel.
        # Higher-priority segments (_pri) override lower ones per-channel,
        # allowing e.g. a PT sweep to control pan/tilt while a base wash
        # controls color independently.
        for fx in dmx_fixtures:
            # Collect per-channel values: {channel_name: (value, priority)}
            ch_vals = {}
            for seg in fx["segs"]:
                ss = seg.get("startS", 0)
                sd = seg.get("durationS", 1)
                if ss <= elapsed < ss + sd:
                    p = seg.get("params", {})
                    pri = seg.get("_pri", 0)
                    for k, v in p.items():
                        if v is not None and (k not in ch_vals or pri >= ch_vals[k][1]):
                            ch_vals[k] = (v, pri)
            r = ch_vals.get("r", (0, 0))[0]
            g = ch_vals.get("g", (0, 0))[0]
            b = ch_vals.get("b", (0, 0))[0]
            pan = ch_vals.get("pan", (None, 0))[0]
            tilt = ch_vals.get("tilt", (None, 0))[0]
            dimmer = ch_vals.get("dimmer", (None, 0))[0]
            strobe = ch_vals.get("strobe", (None, 0))[0]
            gobo = ch_vals.get("gobo", (None, 0))[0]
            color_wheel = ch_vals.get("colorWheel", (None, 0))[0]
            prism = ch_vals.get("prism", (None, 0))[0]
            focus = ch_vals.get("focus", (None, 0))[0]
            zoom = ch_vals.get("zoom", (None, 0))[0]
            profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
            uni_buf = engine.get_universe(fx["uni"])
            # RGB
            uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
            # Dimmer
            if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
            # Pan/Tilt
            if pan is not None and tilt is not None and profile:
                uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
            # Extra DMX channels via set_fixture_channels
            # Channel types use hyphenated names (color-wheel, gobo-rotation)
            extra_ch = {}
            if strobe is not None:
                extra_ch["strobe"] = strobe
            if gobo is not None:
                extra_ch["gobo"] = gobo
            if color_wheel is not None:
                extra_ch["color-wheel"] = color_wheel
            if prism is not None:
                extra_ch["prism"] = prism
            if focus is not None:
                extra_ch["focus"] = focus
            if zoom is not None:
                extra_ch["zoom"] = zoom
            if extra_ch and profile:
                uni_buf.set_fixture_channels(fx["addr"], extra_ch, profile)
        # ── Object patrols: update moving object positions ──
        _evaluate_object_patrols(elapsed)
        # ── Track action: real-time pan/tilt for moving heads following objects ──
        if frame_count % 40 == 0:  # reap temporals every 1s
            _reap_temporal_objects()
        _evaluate_track_actions(elapsed, engine, dmx_fixtures)
        frame_count += 1
        if frame_count == 1:
            log.info("DMX playback: first frame sent at elapsed=%.1fs", elapsed)
    log.info("DMX playback: stopped after %d frames", frame_count)
    # Blackout DMX fixtures on stop (#364) — zero all channels
    for fx in dmx_fixtures:
        profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
        uni_buf = engine.get_universe(fx["uni"])
        uni_buf.set_fixture_rgb(fx["addr"], 0, 0, 0, profile)
        if fx["ch_map"] and "dimmer" in fx["ch_map"]:
            uni_buf.set_fixture_dimmer(fx["addr"], 0, profile)
        if profile and fx["ch_map"]:
            zero_ch = {}
            for ch_type in ("pan", "tilt", "strobe", "gobo", "color-wheel", "prism", "focus", "zoom", "speed"):
                if ch_type in fx["ch_map"]:
                    zero_ch[ch_type] = 0
            if zero_ch:
                uni_buf.set_fixture_channels(fx["addr"], zero_ch, profile)

@app.post("/api/timelines/<int:tid>/start")
def api_timeline_start(tid):
    """Send RUNNER_GO to all children + start DMX playback thread."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    if tid not in _bake_result:
        return jsonify(err="Timeline not baked yet - bake first"), 400

    # Check sync is done
    if _sync_progress and not _sync_progress.get("done"):
        return jsonify(err="Sync still in progress - wait for it to finish"), 409

    # Send RUNNER_GO with 5s offset for NTP alignment
    go_epoch = int(time.time()) + 5
    loop_flag = 1 if tl.get("loop") else 0
    go_pkt = _hdr(CMD_RUNNER_GO, go_epoch) + struct.pack("<IB", go_epoch, loop_flag)

    started = 0
    for child in _children:
        if not child.get("ip"):
            continue
        _send(child["ip"], go_pkt)
        started += 1

    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeTimeline"] = tid
        _settings["runnerStartEpoch"] = go_epoch
        _save("settings", _settings)

    # Start DMX playback thread for DMX fixtures
    _dmx_playback_stop.clear()
    duration = tl.get("durationS", 60)
    loop = tl.get("loop", False)
    threading.Thread(target=_dmx_playback_loop, args=(tid, go_epoch, duration, loop),
                     daemon=True).start()

    return jsonify(ok=True, started=started, goEpoch=go_epoch)

@app.post("/api/timelines/<int:tid>/stop")
def api_timeline_stop(tid):
    """Stop timeline playback on all children + DMX playback thread."""
    # Stop DMX playback thread
    _dmx_playback_stop.set()

    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    stopped = 0
    for _attempt in range(3):
        for child in _children:
            if not child.get("ip"):
                continue
            _send(child["ip"], pkt_stop)
            _send(child["ip"], pkt_off)
            if _attempt == 0:
                stopped += 1

    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)

    return jsonify(ok=True, stopped=stopped)

@app.get("/api/timelines/<int:tid>/status")
def api_timeline_playback_status(tid):
    """Get playback status for a timeline."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404

    running = _settings.get("runnerRunning") and _settings.get("activeTimeline") == tid
    elapsed = 0
    if running and _settings.get("runnerStartEpoch"):
        elapsed = max(0, int(time.time()) - _settings["runnerStartEpoch"])

    return jsonify(
        id=tid,
        name=tl.get("name", "Timeline"),
        running=running,
        elapsed=elapsed,
        durationS=tl.get("durationS", 0),
        loop=tl.get("loop", False),
        activeTimeline=_settings.get("activeTimeline", -1),
    )

#  "  "  Show playlist (sequential playback)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

# Show-level playback state (for sequential multi-timeline playback)
_show_playback = {
    "running": False,
    "currentIndex": 0,     # index into playlist order
    "currentTid": -1,
    "startEpoch": 0,
    "loopAll": False,
    "totalElapsed": 0,
}

@app.get("/api/show/playlist")
def api_show_playlist_get():
    """Return ordered timeline playlist + loop setting."""
    order = _show_playlist.get("order", [])
    # Build enriched list with timeline metadata
    items = []
    for tid in order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if tl:
            items.append({
                "id": tid,
                "name": tl.get("name", f"Timeline {tid}"),
                "durationS": tl.get("durationS", 0),
                "baked": tid in _bake_result,
            })
    total_duration = sum(it["durationS"] for it in items)
    return jsonify({
        "order": order,
        "loopAll": _show_playlist.get("loopAll", False),
        "items": items,
        "totalDurationS": total_duration,
    })


@app.post("/api/show/playlist")
def api_show_playlist_set():
    """Set ordered timeline playlist + loop setting."""
    data = request.get_json(silent=True) or {}
    if "order" in data:
        # Validate all IDs exist
        valid_ids = {t["id"] for t in _timelines}
        _show_playlist["order"] = [tid for tid in data["order"] if tid in valid_ids]
    if "loopAll" in data:
        _show_playlist["loopAll"] = bool(data["loopAll"])
    _save("show_playlist", _show_playlist)
    return jsonify(ok=True)


def _show_playback_loop(playlist_order, loop_all, go_epoch):
    """Background thread: play timelines sequentially."""
    global _show_playback
    tl_list = []
    for tid in playlist_order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if not tl or tid not in _bake_result:
            continue
        tl_list.append((tid, tl))
    if not tl_list:
        log.warning("Show playback: no baked timelines in playlist")
        return

    log.info("Show playback: %d timelines, loop=%s", len(tl_list), loop_all)
    cumulative = 0

    while not _dmx_playback_stop.is_set():
        for idx, (tid, tl) in enumerate(tl_list):
            if _dmx_playback_stop.is_set():
                break
            duration = tl.get("durationS", 60)
            _show_playback["currentIndex"] = idx
            _show_playback["currentTid"] = tid
            log.info("Show playback: starting timeline %d '%s' (%ds)",
                     tid, tl.get("name", "?"), duration)

            # Reuse single-timeline playback for this segment
            _settings["activeTimeline"] = tid
            _save("settings", _settings)

            # Run the single-timeline DMX loop inline (blocking)
            _dmx_playback_single(tid, time.time(), duration)

            if _dmx_playback_stop.is_set():
                break
            cumulative += duration
            _show_playback["totalElapsed"] = cumulative

        if not loop_all or _dmx_playback_stop.is_set():
            break
        # Loop: reset and go again
        cumulative = 0

    _show_playback["running"] = False
    _show_playback["currentTid"] = -1
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)
    log.info("Show playback: finished")


def _dmx_playback_single(tid, go_epoch, duration):
    """Play a single timeline's DMX data. Returns when done or stopped."""
    result = _bake_result.get(tid)
    if not result:
        return
    baked_fixtures = result.get("fixtures", {})
    dmx_fixtures = []
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f["id"]
        fix_data = baked_fixtures.get(fid) or baked_fixtures.get(str(fid), {})
        segs = fix_data.get("segments", [])
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        pid = f.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        ch_map = prof_info.get("channel_map") if prof_info else None
        channels = prof_info.get("channels", []) if prof_info else []
        if not segs:
            continue
        dmx_fixtures.append({"fid": fid, "name": f.get("name", "?"),
                             "uni": uni, "addr": addr, "ch_map": ch_map,
                             "channels": channels, "segs": segs})
    if not dmx_fixtures:
        # No DMX fixtures — just wait for duration to pass
        _dmx_playback_stop.wait(timeout=duration)
        return

    proto = _dmx_settings.get("protocol", "artnet")
    engine = _artnet if proto == "artnet" else _sacn
    if not engine.running:
        engine.start()

    interval = 0.025
    next_frame = time.monotonic()
    frame_count = 0
    while not _dmx_playback_stop.is_set():
        now_mono = time.monotonic()
        if now_mono < next_frame:
            _dmx_playback_stop.wait(timeout=next_frame - now_mono)
            if _dmx_playback_stop.is_set():
                break
            continue
        next_frame += interval
        if next_frame < now_mono:
            next_frame = now_mono + interval
        elapsed = time.time() - go_epoch
        if elapsed < 0:
            continue
        if elapsed > duration:
            break
        for fx in dmx_fixtures:
            ch_vals = {}
            for seg in fx["segs"]:
                ss = seg.get("startS", 0)
                sd = seg.get("durationS", 1)
                if ss <= elapsed < ss + sd:
                    p = seg.get("params", {})
                    pri = seg.get("_pri", 0)
                    for k, v in p.items():
                        if v is not None and (k not in ch_vals or pri >= ch_vals[k][1]):
                            ch_vals[k] = (v, pri)
            r = ch_vals.get("r", (0, 0))[0]
            g = ch_vals.get("g", (0, 0))[0]
            b = ch_vals.get("b", (0, 0))[0]
            pan = ch_vals.get("pan", (None, 0))[0]
            tilt = ch_vals.get("tilt", (None, 0))[0]
            dimmer = ch_vals.get("dimmer", (None, 0))[0]
            strobe = ch_vals.get("strobe", (None, 0))[0]
            gobo = ch_vals.get("gobo", (None, 0))[0]
            color_wheel = ch_vals.get("colorWheel", (None, 0))[0]
            prism = ch_vals.get("prism", (None, 0))[0]
            focus = ch_vals.get("focus", (None, 0))[0]
            zoom = ch_vals.get("zoom", (None, 0))[0]
            profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
            uni_buf = engine.get_universe(fx["uni"])
            uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
            if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
            if pan is not None and tilt is not None and profile:
                uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
            extra_ch = {}
            if strobe is not None: extra_ch["strobe"] = strobe
            if gobo is not None: extra_ch["gobo"] = gobo
            if color_wheel is not None: extra_ch["color-wheel"] = color_wheel
            if prism is not None: extra_ch["prism"] = prism
            if focus is not None: extra_ch["focus"] = focus
            if zoom is not None: extra_ch["zoom"] = zoom
            if extra_ch and profile:
                uni_buf.set_fixture_channels(fx["addr"], extra_ch, profile)
        _evaluate_object_patrols(elapsed)
        if frame_count % 40 == 0:
            _reap_temporal_objects()
        _evaluate_track_actions(elapsed, engine, dmx_fixtures)
        frame_count += 1
    # Blackout on segment end (#364) — zero RGB, dimmer, pan/tilt, and all extras
    for fx in dmx_fixtures:
        profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
        uni_buf = engine.get_universe(fx["uni"])
        uni_buf.set_fixture_rgb(fx["addr"], 0, 0, 0, profile)
        if fx["ch_map"] and "dimmer" in fx["ch_map"]:
            uni_buf.set_fixture_dimmer(fx["addr"], 0, profile)
        if profile and fx["ch_map"]:
            # Zero all mapped channels (pan, tilt, strobe, gobo, etc.)
            zero_ch = {}
            for ch_type in ("pan", "tilt", "strobe", "gobo", "color-wheel", "prism", "focus", "zoom", "speed"):
                if ch_type in fx["ch_map"]:
                    zero_ch[ch_type] = 0
            if zero_ch:
                uni_buf.set_fixture_channels(fx["addr"], zero_ch, profile)


@app.post("/api/show/start")
def api_show_start():
    """Start sequential playback of the show playlist."""
    global _show_playback
    data = request.get_json(silent=True) or {}
    order = data.get("order") or _show_playlist.get("order", [])
    loop_all = data.get("loopAll", _show_playlist.get("loopAll", False))
    if not order:
        return jsonify(err="Playlist is empty"), 400
    # Verify all timelines are baked
    unbaked = [tid for tid in order if tid not in _bake_result]
    if unbaked:
        return jsonify(err="Unbaked timelines in playlist", unbaked=unbaked), 400
    # Stop any existing playback
    _dmx_playback_stop.set()
    time.sleep(0.1)
    _dmx_playback_stop.clear()

    go_epoch = int(time.time()) + 2
    # Send RUNNER_GO to all children
    loop_flag = 1 if loop_all else 0
    go_pkt = _hdr(CMD_RUNNER_GO, go_epoch) + struct.pack("<IB", go_epoch, loop_flag)
    started = 0
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], go_pkt)
            started += 1

    _show_playback = {
        "running": True, "currentIndex": 0, "currentTid": order[0],
        "startEpoch": go_epoch, "loopAll": loop_all, "totalElapsed": 0,
    }
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeTimeline"] = order[0]
        _settings["runnerStartEpoch"] = go_epoch
        _save("settings", _settings)

    threading.Thread(target=_show_playback_loop, args=(order, loop_all, go_epoch),
                     daemon=True).start()
    return jsonify(ok=True, started=started, goEpoch=go_epoch, timelines=len(order))


@app.post("/api/show/stop")
def api_show_stop():
    """Stop sequential show playback."""
    _dmx_playback_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], pkt_stop)
            _send(child["ip"], pkt_off)
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)
    _show_playback["running"] = False
    _show_playback["currentTid"] = -1
    return jsonify(ok=True)


@app.get("/api/show/status")
def api_show_status():
    """Get sequential show playback status."""
    running = _show_playback.get("running", False)
    current_tid = _show_playback.get("currentTid", -1)
    current_tl = next((t for t in _timelines if t["id"] == current_tid), None)
    # Compute elapsed for current timeline
    current_elapsed = 0
    if running and _settings.get("runnerStartEpoch"):
        current_elapsed = max(0, int(time.time()) - _settings["runnerStartEpoch"])
    # Build enriched playlist
    order = _show_playlist.get("order", [])
    items = []
    cumulative_before = 0
    for tid in order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if tl:
            d = tl.get("durationS", 0)
            items.append({
                "id": tid, "name": tl.get("name", "?"),
                "durationS": d, "baked": tid in _bake_result,
                "playing": tid == current_tid,
            })
            if tid == current_tid:
                break
            cumulative_before += d
    total_elapsed = cumulative_before + current_elapsed if running else 0
    total_duration = sum(
        t.get("durationS", 0) for t in _timelines
        if t["id"] in order
    )
    return jsonify({
        "running": running,
        "loopAll": _show_playback.get("loopAll", False),
        "currentTimeline": current_tid,
        "currentName": current_tl.get("name", "?") if current_tl else None,
        "currentIndex": _show_playback.get("currentIndex", 0),
        "currentElapsed": current_elapsed,
        "currentDurationS": current_tl.get("durationS", 0) if current_tl else 0,
        "totalElapsed": total_elapsed,
        "totalDurationS": total_duration,
        "items": items,
    })


#  "  "  Settings  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/settings")
def api_settings_get():
    s = dict(_settings)
    # Compute elapsed dynamically from start epoch
    if s.get("runnerRunning") and s.get("runnerStartEpoch"):
        s["runnerElapsed"] = max(0, int(time.time()) - s["runnerStartEpoch"])
    return jsonify(s)

@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "units", "canvasW", "canvasH", "darkMode", "runnerLoop", "globalBrightness", "logging", "logPath"):
            if k in body:
                _settings[k] = body[k]
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
        _save("settings", _settings)
        # Sync stage dimensions (meters) from canvas (mm)
        _stage["w"] = _settings["canvasW"] / 1000.0
        _stage["h"] = _settings["canvasH"] / 1000.0
        _save("stage", _stage)
    # Toggle file logging if changed
    if "logging" in body:
        _apply_logging(body["logging"], body.get("logPath"))
    return jsonify(ok=True)

@app.post("/api/logging/start")
def api_logging_start():
    """Start file logging. Optional body: {path: '/path/to/file.log'}."""
    try:
        body = request.get_json(silent=True) or {}
        log_path = body.get("path") if isinstance(body, dict) else None
        _settings["logging"] = True
        _save("settings", _settings)
        _apply_logging(True, log_path)
        return jsonify(ok=True, path=_log_handler.baseFilename if _log_handler else None)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 500

@app.post("/api/logging/stop")
def api_logging_stop():
    """Stop file logging."""
    _settings["logging"] = False
    _save("settings", _settings)
    _apply_logging(False)
    return jsonify(ok=True)

@app.get("/api/logging/status")
def api_logging_status():
    """Return current logging state and file path."""
    return jsonify(
        enabled=bool(_log_handler),
        path=_log_handler.baseFilename if _log_handler else None
    )

#  "  "  Actions library  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/actions")
def api_actions():
    return jsonify(_actions)

_ACTION_FIELDS = ("name", "type", "scope", "canvasEffect", "targetIds", "r", "g", "b",
                  "r2", "g2", "b2",           # Fade second colour
                  "speedMs", "periodMs", "spawnMs",  # timing
                  "minBri", "spacing", "paletteId",  # Breathe/Chase/Rainbow
                  "cooling", "sparking",              # Fire
                  "direction", "tailLen", "density",  # Chase/Comet/Twinkle
                  "decay", "fadeSpeed",               # Comet/Twinkle
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct",  # legacy compat
                  "wledFxOverride", "wledPalOverride", "wledSegId",  # WLED overrides
                  "trackObjectIds", "trackCycleMs", "trackOffset",  # Track action
                  "trackFixtureIds", "trackFixtureOffsets", "trackAutoSpread",
                  "dimmer", "pan", "tilt", "strobe", "gobo", "colorWheel", "prism",  # DMX channels
                  "ptStartPos", "ptEndPos")  # Pan/Tilt Move: stage coordinate positions [x,y,z] mm

@app.post("/api/actions")
def api_actions_create():
    global _nxt_a
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    with _lock:
        a = {"id": _nxt_a}
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        a.setdefault("name", name)
        a.setdefault("type", 1)
        _actions.append(a)
        _nxt_a += 1
        _save("actions", _actions)
    return jsonify(ok=True, id=a["id"])

@app.get("/api/actions/<int:aid>")
def api_action_get(aid):
    a = next((x for x in _actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(a)

@app.put("/api/actions/<int:aid>")
def api_action_put(aid):
    a = next((x for x in _actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        _save("actions", _actions)
    return jsonify(ok=True)

@app.delete("/api/actions/<int:aid>")
def api_action_delete(aid):
    global _actions
    with _lock:
        n = len(_actions)
        _actions = [x for x in _actions if x["id"] != aid]
        if len(_actions) == n:
            return jsonify(ok=False, err="not found"), 404
        _save("actions", _actions)
    return jsonify(ok=True)

#  "  "  Config export-import  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

CONFIG_SCHEMA_VERSION = 3  # bump when export format changes incompatibly
CONFIG_MIN_IMPORT_VERSION = 1  # oldest version we can still import

@app.get("/api/config/export")
def api_config_export():
    """Bundle children + fixtures + layout as a portable config file.

    Schema v3: strip internal-only fields (aimPoint, orientation, _placed,
    _beamWidth, status, moverCalibrated, calibrated, _temporal, _ttl).
    """
    # Strip internal/transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = []
    for c in _children:
        cc = {k: v for k, v in c.items() if k not in _CHILD_STRIP}
        clean_children.append(cc)

    # Strip internal/transient fields from fixtures
    _FIX_STRIP = {"aimPoint", "orientation", "_placed", "_beamWidth",
                  "moverCalibrated", "calibrated", "rangeCalibrated",
                  "_temporal", "_ttl", "positioned"}
    clean_fixtures = []
    for f in _fixtures:
        cf = {k: v for k, v in f.items() if k not in _FIX_STRIP}
        clean_fixtures.append(cf)

    return jsonify({
        "type": "slyled-config",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "version": CONFIG_SCHEMA_VERSION,  # backward compat
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": _layout,
    })

@app.post("/api/config/import")
def api_config_import():
    """Merge children by hostname, auto-create fixtures, remap layout IDs."""
    global _nxt_c, _nxt_fix, _layout
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-config":
        return jsonify(ok=False, err="Not a SlyLED config file (missing type field)"), 400
    # Schema version check — accept v1-v3, reject future incompatible versions
    sv = data.get("schemaVersion") or data.get("version") or 1
    if sv > CONFIG_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, but this app only supports up to version {CONFIG_SCHEMA_VERSION}. Please update SlyLED."), 400
    if sv < CONFIG_MIN_IMPORT_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, which is too old. Minimum supported version is {CONFIG_MIN_IMPORT_VERSION}."), 400
    imported_children = data.get("children", [])
    imported_layout = data.get("layout")
    added = updated = fixtures_created = 0
    child_id_map = {}  # old_child_id -> new_child_id
    fixture_id_map = {}  # old_layout_id -> new_fixture_id
    with _lock:
        # Import children
        for c in imported_children:
            old_id = c.get("id", -1)
            ex = next((x for x in _children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                child_id_map[old_id] = ex["id"]
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = _nxt_c
                child_id_map[old_id] = _nxt_c
                _nxt_c += 1
                _children.append(c)
                added += 1
        _save("children", _children)

        # Auto-create fixtures for children that don't already have one
        for c in _children:
            cid = c["id"]
            # Skip if fixture already exists for this child
            if any(f.get("childId") == cid for f in _fixtures):
                continue
            # DMX bridges never get auto-fixtures (they ARE the transport, not a light)
            if c.get("type") == "dmx" or c.get("boardType") in ("giga-dmx", "DMX Bridge"):
                continue
            # Create LED fixture if child has strings with LEDs
            sc = c.get("sc", 0)
            strings = c.get("strings", [])[:sc]
            if not strings or not any(s.get("leds", 0) > 0 for s in strings):
                continue
            f = {
                "id": _nxt_fix,
                "name": c.get("name") or c.get("hostname") or f"Fixture {_nxt_fix}",
                "fixtureType": "led", "type": "linear", "childId": cid,
                "strings": [{"leds": s.get("leds", 0), "mm": s.get("mm", 1000),
                              "sdir": s.get("sdir", 0)} for s in strings if s.get("leds", 0) > 0],
                "rotation": [0, 0, 0], "aoeRadius": 1000,
            }
            _fixtures.append(f)
            # Map: if layout had an entry for this child's old ID, remap to new fixture ID
            for old_cid, new_cid in child_id_map.items():
                if new_cid == cid:
                    fixture_id_map[old_cid] = _nxt_fix
            fixture_id_map[cid] = _nxt_fix
            _nxt_fix += 1
            fixtures_created += 1
        _save("fixtures", _fixtures)

        # Remap layout position IDs
        if imported_layout:
            _layout = imported_layout
            for lc in _layout.get("children", []):
                old_id = lc.get("id")
                # Try fixture map first (old fixture/child ID → new fixture ID)
                new_id = fixture_id_map.get(old_id)
                if new_id is None:
                    # Try child map
                    new_cid = child_id_map.get(old_id)
                    if new_cid is not None:
                        new_id = fixture_id_map.get(new_cid, new_cid)
                if new_id is not None:
                    lc["id"] = new_id
            _save("layout", _layout)

        # Import explicit fixtures from config (v2+ includes fixtures array)
        imported_fixtures = data.get("fixtures", [])
        for f in imported_fixtures:
            old_fid = f.get("id", -1)
            # Skip if we already auto-created a fixture for this child
            cid = f.get("childId")
            if cid is not None:
                new_cid = child_id_map.get(cid, cid)
                if any(ef.get("childId") == new_cid for ef in _fixtures):
                    # Already exists — update fixture_id_map for layout remapping
                    existing = next(ef for ef in _fixtures if ef.get("childId") == new_cid)
                    fixture_id_map[old_fid] = existing["id"]
                    continue
            # Create the fixture with a new ID
            f = dict(f)
            new_fid = _nxt_fix
            fixture_id_map[old_fid] = new_fid
            f["id"] = new_fid
            if cid is not None:
                f["childId"] = child_id_map.get(cid, cid)
            _fixtures.append(f)
            _nxt_fix += 1
            fixtures_created += 1
        _save("fixtures", _fixtures)

        # Re-remap layout IDs with the complete fixture_id_map
        if imported_layout:
            for lc in _layout.get("children", []):
                old_id = lc.get("id")
                new_id = fixture_id_map.get(old_id)
                if new_id is not None:
                    lc["id"] = new_id
            _save("layout", _layout)

    log.info("CONFIG IMPORT: %d children added, %d updated, %d fixtures created, child_map=%s, fix_map=%s",
             added, updated, fixtures_created, child_id_map, fixture_id_map)
    return jsonify(ok=True, added=added, updated=updated, fixturesCreated=fixtures_created)

@app.post("/api/show/preset")
def api_show_preset():
    """Install a preset show by theme ID from request body."""
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")
    return _install_preset_show(preset_id)

def _install_preset_show(preset_id):
    """Install a preset show as a timeline with spatial effects and actions.

    Dynamically generates a show based on the selected theme and the user's
    actual fixtures, positions, and capabilities. Every fixture gets coverage
    so there are no dark periods.
    """
    global _nxt_a, _nxt_sfx, _nxt_tl

    from show_generator import generate_show, THEMES
    if preset_id not in THEMES:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    show = generate_show(preset_id, _fixtures, _layout, _stage, _profile_lib)
    if not show:
        return jsonify(ok=False, err="Failed to generate show"), 500

    with _lock:
        dur = show["durationS"]

        # Create action records and build id lookup
        # action_ref_map: maps python id() of the action_info dict -> assigned action id
        action_ref_map = {}
        action_count = 0
        for act_info in show.get("base_actions", []) + show.get("mover_actions", []):
            act_data = act_info.get("action", act_info) if isinstance(act_info, dict) and "action" in act_info else act_info
            act = {"id": _nxt_a, **act_data}
            _actions.append(act)
            action_ref_map[id(act_info)] = _nxt_a
            action_count += 1
            _nxt_a += 1
        _save("actions", _actions)

        # Create spatial effect records
        effect_ref_map = {}  # maps python id() of effect dict -> assigned effect id
        for fx in show.get("effects", []):
            fx_rec = {"id": _nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            _spatial_fx.append(fx_rec)
            effect_ref_map[id(fx)] = _nxt_sfx
            _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)

        # Build timeline tracks from generator's track structure
        # Tracks are ordered: lower index = lower priority (background)
        tracks = []
        for gen_track in show.get("tracks", []):
            track = {}
            if gen_track.get("allPerformers"):
                track["allPerformers"] = True
            elif gen_track.get("fixtureId"):
                track["fixtureId"] = gen_track["fixtureId"]
            else:
                continue

            clips = []
            for gen_clip in gen_track.get("clips", []):
                clip = {
                    "startS": gen_clip.get("startS", 0),
                    "durationS": gen_clip.get("durationS", dur),
                }
                # Resolve action or effect reference
                aref = gen_clip.get("_action_ref")
                eref = gen_clip.get("_effect_ref")
                if aref and id(aref) in action_ref_map:
                    clip["actionId"] = action_ref_map[id(aref)]
                    act_data = aref.get("action", aref) if isinstance(aref, dict) and "action" in aref else aref
                    clip["name"] = act_data.get("name", "Action")
                elif eref and id(eref) in effect_ref_map:
                    clip["effectId"] = effect_ref_map[id(eref)]
                    clip["name"] = eref.get("name", "Effect")
                else:
                    continue  # skip clips with no resolved reference
                clips.append(clip)

            if clips:
                track["clips"] = clips
                tracks.append(track)

        tl = {
            "id": _nxt_tl, "name": show["name"],
            "durationS": dur,
            "tracks": tracks,
            "loop": True,
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)

    return jsonify(ok=True, name=show["name"], timelineId=tl["id"],
                   actions=action_count, effects=len(effect_ref_map))


def _api_show_preset_old():
    """LEGACY: hardcoded preset shows — kept as fallback reference."""
    global _nxt_a, _nxt_sfx, _nxt_tl
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")

    PRESETS = {
        "rainbow-up": {
            "name": "Rainbow Up",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 60,
                         "paletteId": 0, "direction": 1}],
        },
        "rainbow-across": {
            "name": "Rainbow Across",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 50,
                         "paletteId": 0, "direction": 0}],
        },
        "slow-fire": {
            "name": "Slow Fire",
            "durationS": 60,
            "actions": [{"name": "Fire Effect", "type": 6, "r": 255, "g": 80, "b": 0,
                         "speedMs": 40, "cooling": 45, "sparking": 100}],
        },
        "disco": {
            "name": "Disco",
            "durationS": 60,
            "actions": [{"name": "Disco Twinkle", "type": 8, "r": 200, "g": 100, "b": 255,
                         "spawnMs": 80, "density": 5, "fadeSpeed": 15}],
        },
        "ocean-wave": {
            "name": "Ocean Wave",
            "durationS": 40,
            "effects": [{"name": "Blue Wave", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 80, "b": 220, "size": {"normal": [1,0,0], "thickness": 800},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 10, "easing": "ease-in-out"},
                         "blend": "add"},
                        {"name": "Teal Wash", "category": "spatial-field", "shape": "sphere",
                         "r": 0, "g": 180, "b": 160, "size": {"radius": 2500},
                         "motion": {"startPos": [8000,1000,0], "endPos": [0,3000,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "screen"}],
        },
        "sunset": {
            "name": "Sunset Glow",
            "durationS": 45,
            "actions": [{"name": "Warm Breathe", "type": 3, "r": 255, "g": 100, "b": 20,
                         "periodMs": 4000, "minBri": 30}],
            "effects": [{"name": "Golden Sweep", "category": "spatial-field", "shape": "plane",
                         "r": 255, "g": 160, "b": 30, "size": {"normal": [0,1,0], "thickness": 1000},
                         "motion": {"startPos": [5000,5000,0], "endPos": [5000,0,0], "durationS": 20, "easing": "ease-out"},
                         "blend": "screen"}],
        },
        "police": {
            "name": "Police Lights",
            "durationS": 30,
            "actions": [{"name": "Red Strobe", "type": 9, "r": 255, "g": 0, "b": 0,
                         "periodMs": 200, "p8a": 50}],
            "effects": [{"name": "Blue Flash Sweep", "category": "spatial-field", "shape": "box",
                         "r": 0, "g": 0, "b": 255, "size": {"width": 2000, "height": 5000, "depth": 3000},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 2, "easing": "linear"},
                         "blend": "add"}],
        },
        "starfield": {
            "name": "Starfield",
            "durationS": 60,
            "actions": [{"name": "Star Sparkle", "type": 12, "r": 5, "g": 5, "b": 20,
                         "spawnMs": 60, "density": 4}],
        },
        "aurora": {
            "name": "Aurora Borealis",
            "durationS": 40,
            "effects": [{"name": "Green Curtain", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 255, "b": 80, "size": {"normal": [1,0.3,0], "thickness": 1500},
                         "motion": {"startPos": [0,2000,0], "endPos": [10000,3000,0], "durationS": 15, "easing": "ease-in-out"},
                         "blend": "screen"},
                        {"name": "Purple Shimmer", "category": "spatial-field", "shape": "sphere",
                         "r": 120, "g": 0, "b": 200, "size": {"radius": 2000},
                         "motion": {"startPos": [8000,3000,0], "endPos": [1000,1500,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "add"}],
        },
        # ── Moving-head-aware presets ──────────────────────────────────
        # These use spatial effects with motion paths. LED fixtures get
        # color washes; DMX moving heads also track the effect center
        # with pan/tilt, creating beam sweeps across the stage.
        "spotlight-sweep": {
            "name": "Spotlight Sweep",
            "durationS": 20,
            "effects": [
                {"name": "Sweep Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 240, "b": 200, "size": {"radius": 3000},
                 "motion": {"startPos": [0, 2500, 2500], "endPos": [10000, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Return Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 180, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [10000, 2500, 2500], "endPos": [0, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "concert-wash": {
            "name": "Concert Wash",
            "durationS": 30,
            "actions": [{"name": "Slow Breathe Blue", "type": 3, "r": 0, "g": 40, "b": 200,
                         "periodMs": 5000, "minBri": 20}],
            "effects": [
                {"name": "Magenta Flood", "category": "spatial-field", "shape": "plane",
                 "r": 220, "g": 0, "b": 180, "size": {"normal": [1, 0, 0], "thickness": 2000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 12, "easing": "ease-in-out"},
                 "blend": "screen"},
                {"name": "Amber Spot", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 160, "b": 40, "size": {"radius": 3000},
                 "motion": {"startPos": [8000, 2500, 3000], "endPos": [2000, 2500, 7000],
                            "durationS": 15, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "figure-eight": {
            "name": "Figure Eight",
            "durationS": 24,
            "effects": [
                # Two spheres crossing at center stage — moving heads track each
                {"name": "Cyan Path A", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Cyan Path B", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return A", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 8000], "endPos": [1000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return B", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 8000], "endPos": [9000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "thunderstorm": {
            "name": "Thunderstorm",
            "durationS": 30,
            "actions": [{"name": "Deep Blue Base", "type": 1, "r": 5, "g": 5, "b": 30}],
            "effects": [
                # Lightning bolts — fast-moving spheres that moving heads chase
                {"name": "Lightning Strike 1", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 255, "b": 240, "size": {"radius": 3000},
                 "motion": {"startPos": [3000, 5000, 5000], "endPos": [3000, 0, 5000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Lightning Strike 2", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 200, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [7000, 5000, 3000], "endPos": [7000, 0, 3000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Rolling Thunder", "category": "spatial-field", "shape": "plane",
                 "r": 30, "g": 20, "b": 80, "size": {"normal": [1, 0, 0], "thickness": 3000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 8, "easing": "linear"},
                 "blend": "screen"},
            ],
        },
        "dance-floor": {
            "name": "Dance Floor",
            "durationS": 20,
            "actions": [{"name": "Chase Pulse", "type": 4, "r": 255, "g": 0, "b": 128,
                         "speedMs": 30, "spacing": 6, "tailLen": 3, "direction": 0}],
            "effects": [
                # Fast orbiting spots — moving heads rapidly track
                {"name": "Red Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 0, "b": 50, "size": {"radius": 2500},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Blue Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 50, "g": 0, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Green Flash", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 255, "b": 80, "size": {"radius": 3000},
                 "motion": {"startPos": [5000, 5000, 5000], "endPos": [5000, 1000, 5000],
                            "durationS": 2, "easing": "ease-in"},
                 "blend": "add"},
            ],
        },
    }

    preset = PRESETS.get(preset_id)
    if not preset:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    with _lock:
        # Create actions from preset
        action_ids = []
        for a in preset.get("actions", []):
            act = {"id": _nxt_a, **a}
            _actions.append(act)
            action_ids.append(_nxt_a)
            _nxt_a += 1
        _save("actions", _actions)

        # Create spatial effects from preset
        effect_ids = []
        for fx in preset.get("effects", []):
            fx_rec = {"id": _nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            _spatial_fx.append(fx_rec)
            effect_ids.append(_nxt_sfx)
            _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)

        # Build timeline with one "all performers" track
        clips = []
        t = 0
        for aid in action_ids:
            dur = preset.get("durationS", 30)
            clips.append({"actionId": aid, "startS": 0, "durationS": dur})
        for eid in effect_ids:
            dur = preset.get("durationS", 30)
            clips.append({"effectId": eid, "startS": 0, "durationS": dur})

        tl = {
            "id": _nxt_tl, "name": preset["name"],
            "durationS": preset.get("durationS", 30),
            "tracks": [{"allPerformers": True, "clips": clips}],
            "loop": True,
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)

    return jsonify(ok=True, name=preset["name"], timelineId=tl["id"],
                   actions=len(action_ids), effects=len(effect_ids))

@app.get("/api/show/presets")
def api_show_presets():
    """List available preset shows."""
    from show_generator import list_themes
    presets = list_themes()
    return jsonify(presets)

@app.post("/api/show/demo")
def api_show_demo():
    """Generate a demo show using a random preset theme and existing fixtures."""
    from show_generator import THEMES
    import random
    theme_id = random.choice(list(THEMES.keys()))
    return _install_preset_show(theme_id)

@app.get("/api/show/export")
def api_show_export():
    """Bundle actions + spatial effects + timelines as a portable show file."""
    return jsonify({"type": "slyled-show", "version": 1,
                    "actions": _actions, "spatialEffects": _spatial_fx,
                    "timelines": _timelines})

@app.post("/api/show/import")
def api_show_import():
    """Replace all actions, spatial effects, and timelines from a show file."""
    global _actions, _spatial_fx, _timelines, _nxt_a, _nxt_sfx, _nxt_tl
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-show":
        return jsonify(ok=False, err="not a slyled-show file"), 400
    with _lock:
        _actions = data.get("actions", [])
        _spatial_fx = data.get("spatialEffects", [])
        _timelines = data.get("timelines", [])
        _nxt_a = max((a["id"] for a in _actions), default=-1) + 1
        _nxt_sfx = max((f["id"] for f in _spatial_fx), default=-1) + 1
        _nxt_tl = max((t["id"] for t in _timelines), default=-1) + 1
        _save("actions", _actions)
        _save("spatial_fx", _spatial_fx)
        _save("timelines", _timelines)
    return jsonify(ok=True, actions=len(_actions), spatialEffects=len(_spatial_fx),
                   timelines=len(_timelines),
                   runners=0, flights=0, shows=0)

#  "  "  Project file (complete save/load)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

PROJECT_SCHEMA_VERSION = 2   # bumped from 1 → 2 for spatial data (#336)


def _compress_cloud(cloud):
    """Gzip-compress point cloud data for .slyshow portability (#336)."""
    import gzip, base64, io
    raw = json.dumps(cloud.get("points", [])).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as f:
        f.write(raw)
    result = {k: v for k, v in cloud.items() if k != "points"}
    result["points"] = {"compressed": True,
                        "data": base64.b64encode(buf.getvalue()).decode("ascii")}
    return result


def _decompress_cloud(cloud):
    """Decompress gzip-compressed point cloud from import (#336)."""
    import gzip, base64, io
    pts = cloud.get("points")
    if isinstance(pts, dict) and pts.get("compressed"):
        raw = gzip.decompress(base64.b64decode(pts["data"]))
        cloud["points"] = json.loads(raw)
    return cloud


@app.get("/api/project/export")
def api_project_export():
    """Bundle ALL state into a complete project file (.slyshow)."""
    # Strip transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = [{k: v for k, v in c.items() if k not in _CHILD_STRIP}
                      for c in _children]
    # Strip transient fields from fixtures
    _FIX_STRIP = {"_placed", "_beamWidth", "_temporal", "_ttl", "positioned"}
    clean_fixtures = [{k: v for k, v in f.items() if k not in _FIX_STRIP}
                      for f in _fixtures]
    # Camera SSH: export per-node config with passwords stripped (not portable)
    clean_camera_ssh = {}
    for ip, ssh in _camera_ssh.items():
        clean = dict(ssh)
        clean.pop("password", None)  # encrypted passwords are machine-specific
        clean_camera_ssh[ip] = clean
    # Settings minus transient runtime state
    clean_settings = {k: v for k, v in _settings.items()
                      if k not in ("runnerRunning", "runnerElapsed")}
    # Point cloud: compress if large (#336)
    cloud_export = None
    if _point_cloud and _point_cloud.get("points"):
        cloud_export = _compress_cloud(_point_cloud)
    # Light maps from mover calibrations (#336)
    light_maps = {}
    for fid, cal in _mover_cal.items():
        lm = cal.get("lightMap")
        if lm:
            light_maps[fid] = lm
    # Collect custom DMX profiles referenced by fixtures (#337)
    profile_ids = set()
    for f in _fixtures:
        pid = f.get("dmxProfileId")
        if pid:
            profile_ids.add(pid)
    export_profiles = []
    for pid in profile_ids:
        p = _profile_lib.get_profile(pid)
        if p and not p.get("builtin"):
            export_profiles.append(p)
    return jsonify({
        "type": "slyled-project",
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "appVersion": VERSION,
        "savedAt": datetime.utcnow().isoformat() + "Z",
        "name": _settings.get("name", "SlyLED"),
        "stage": _stage,
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": _layout,
        "actions": _actions,
        "spatialEffects": _spatial_fx,
        "timelines": _timelines,
        "objects": _objects,
        "dmxSettings": {k: v for k, v in _dmx_settings.items()},
        "calibrations": _calibrations,
        "rangeCalibrations": _range_cal,
        "moverCalibrations": _mover_cal,
        "cameraSsh": clean_camera_ssh,
        "showPlaylist": _show_playlist,
        "profiles": export_profiles,
        "settings": clean_settings,
        # Spatial data (#336)
        "pointCloud": cloud_export,
        "lightMaps": light_maps if light_maps else None,
    })


@app.post("/api/project/import")
def api_project_import():
    """Load a complete project file, replacing ALL state."""
    global _children, _fixtures, _layout, _stage, _settings
    global _actions, _spatial_fx, _timelines, _objects
    global _dmx_settings, _calibrations, _range_cal, _mover_cal
    global _nxt_c, _nxt_a, _nxt_fix, _nxt_obj, _nxt_sfx, _nxt_tl
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-project":
        return jsonify(ok=False, err="Not a SlyLED project file"), 400
    sv = data.get("schemaVersion", 1)
    if sv > PROJECT_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Project file is version {sv}, but this app only supports version {PROJECT_SCHEMA_VERSION}. Please update SlyLED."), 400
    # Stop active playback
    _dmx_playback_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c.get("ip"):
            _send(c["ip"], pkt_stop)
            _send(c["ip"], pkt_off)
    _live_events.clear()
    _bake_result.clear()
    with _lock:
        _children = data.get("children", [])
        for c in _children:
            c["status"] = 0  # all offline until next ping
        _fixtures = data.get("fixtures", [])
        _layout = data.get("layout", {"canvasW": 3000, "canvasH": 2000, "children": []})
        _stage = data.get("stage", {"w": 10.0, "h": 5.0, "d": 10.0})
        _actions = data.get("actions", [])
        _spatial_fx = data.get("spatialEffects", [])
        _timelines = data.get("timelines", [])
        _objects = data.get("objects", [])
        _dmx_settings = data.get("dmxSettings", dict(_DMX_SETTINGS_DEFAULTS))
        # Reconfigure and restart engine with imported settings (#350)
        if _artnet.running:
            _artnet.stop()
        if _sacn.running:
            _sacn.stop()
        _apply_dmx_settings()
        _proto = _dmx_settings.get("protocol", "artnet")
        _eng = _artnet if _proto == "artnet" else _sacn if _proto == "sacn" else None
        if _eng and _dmx_settings.get("universeRoutes"):
            _eng._bind_ip = "0.0.0.0"  # always use wildcard — saved IP may be stale (#345)
            try:
                _eng.start()
            except Exception:
                pass
            if _eng.running:
                _apply_profile_defaults(_eng)
        _calibrations.clear()
        _calibrations.update(data.get("calibrations", {}))
        _range_cal.clear()
        _range_cal.update(data.get("rangeCalibrations", {}))
        _mover_cal.clear()
        _mover_cal.update(data.get("moverCalibrations", {}))
        # Restore show playlist
        _show_playlist.clear()
        _show_playlist.update(data.get("showPlaylist", {"order": [], "loopAll": False}))
        # Auto-populate playlist if empty but timelines exist (fixes #312)
        if not _show_playlist.get("order") and _timelines:
            _show_playlist["order"] = [t["id"] for t in _timelines]
        # Restore per-node camera SSH (passwords stripped — user must re-enter)
        imported_cam_ssh = data.get("cameraSsh", {})
        if imported_cam_ssh:
            _camera_ssh.update(imported_cam_ssh)
            _save("camera_ssh", _camera_ssh)
        # Merge imported settings (preserve runtime-only fields)
        imp_settings = data.get("settings", {})
        for k, v in imp_settings.items():
            _settings[k] = v
        _settings["runnerRunning"] = False
        _settings["runnerElapsed"] = 0
        # Recompute sequence counters
        _nxt_c = max((c["id"] for c in _children), default=-1) + 1
        _nxt_fix = max((f["id"] for f in _fixtures), default=-1) + 1
        _nxt_a = max((a["id"] for a in _actions), default=-1) + 1
        _nxt_obj = max((o["id"] for o in _objects), default=-1) + 1
        _nxt_sfx = max((f["id"] for f in _spatial_fx), default=-1) + 1
        _nxt_tl = max((t["id"] for t in _timelines), default=-1) + 1
        # Restore spatial data (#336)
        global _point_cloud
        cloud = data.get("pointCloud")
        if cloud:
            _point_cloud = _decompress_cloud(cloud)
            _save("pointcloud", _point_cloud)
        # Restore light maps into mover calibrations (#336)
        light_maps = data.get("lightMaps")
        if light_maps:
            for fid_str, lm in light_maps.items():
                if fid_str in _mover_cal:
                    _mover_cal[fid_str]["lightMap"] = lm
        # Import custom DMX profiles referenced by fixtures (#337)
        for p in data.get("profiles", []):
            pid = p.get("id")
            if pid and not _profile_lib.get_profile(pid):
                _profile_lib.save_profile(p)
        # Fetch missing profiles from community server (#351)
        _missing_pids = set()
        for f in _fixtures:
            pid = f.get("dmxProfileId")
            if pid and not _profile_lib.get_profile(pid):
                _missing_pids.add(pid)
        if _missing_pids:
            try:
                import community_client as cc
                for pid in _missing_pids:
                    result = cc.get_profile(pid)
                    if result and result.get("ok"):
                        prof = result.get("data", result)
                        if isinstance(prof, dict) and "id" in prof:
                            _profile_lib.import_profiles([prof])
                            log.info("Project import: fetched missing profile '%s' from community", pid)
                        else:
                            log.warning("Project import: community returned invalid data for '%s'", pid)
                    else:
                        log.warning("Project import: could not fetch profile '%s' from community", pid)
            except Exception as e:
                log.warning("Project import: community profile fetch failed: %s", e)
        # Persist everything
        _save("children", _children)
        _save("fixtures", _fixtures)
        _save("layout", _layout)
        _save("stage", _stage)
        _save("actions", _actions)
        _save("spatial_fx", _spatial_fx)
        _save("timelines", _timelines)
        _save("objects", _objects)
        _save("dmx_settings", _dmx_settings)
        _save("calibrations", _calibrations)
        _save("range_calibrations", _range_cal)
        _save("mover_calibrations", _mover_cal)
        _save("show_playlist", _show_playlist)
        _save("settings", _settings)
    _apply_dmx_settings()
    name = data.get("name", "Untitled")
    # Report camera nodes that need SSH credentials re-entered
    ssh_needed = []
    for ip, ssh in _camera_ssh.items():
        if ssh.get("authType") == "password" and not ssh.get("password"):
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "password"})
        elif ssh.get("authType") == "key" and ssh.get("keyPath") and not Path(os.path.expanduser(ssh["keyPath"])).exists():
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "key", "keyPath": ssh["keyPath"]})
    return jsonify(ok=True, name=name,
                   children=len(_children), fixtures=len(_fixtures),
                   actions=len(_actions), timelines=len(_timelines),
                   objects=len(_objects), sshNeeded=ssh_needed)


@app.get("/api/project/name")
def api_project_name_get():
    return jsonify(name=_settings.get("name", "SlyLED"))


@app.post("/api/project/name")
def api_project_name_set():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    _settings["name"] = name
    _save("settings", _settings)
    return jsonify(ok=True, name=name)


#  "  "  Factory reset  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 3000, "canvasH": 2000,
    "darkMode": 1, "runnerRunning": False,
    "runnerElapsed": 0, "runnerLoop": True, "logging": False,
}
_DEFAULT_LAYOUT = {"canvasW": 3000, "canvasH": 2000, "children": []}
_DEFAULT_STAGE  = {"w": 10.0, "h": 5.0, "d": 10.0}
_DEFAULT_FIXTURES  = []
_DEFAULT_OBJECTS   = []
_DEFAULT_SPATIAL_FX = []
_DEFAULT_TIMELINES = []

#  "  "  WiFi credentials  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

import base64, hashlib
from cryptography.fernet import Fernet, InvalidToken

def _wifi_key():
    """Derive a Fernet key from machine identity using PBKDF2."""
    seed = (socket.gethostname() + "-slyled-wifi").encode()
    dk = hashlib.pbkdf2_hmac("sha256", seed, b"slyled-salt-v2", 100_000, dklen=32)
    return base64.urlsafe_b64encode(dk)

def _encrypt_pw(plain):
    if not plain:
        return ""
    f = Fernet(_wifi_key())
    return f.encrypt(plain.encode("utf-8")).decode("ascii")

def _decrypt_pw(enc):
    if not enc:
        return ""
    try:
        f = Fernet(_wifi_key())
        return f.decrypt(enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        # Fallback: try legacy XOR decryption for migration
        try:
            legacy_seed = (socket.gethostname() + "-slyled-wifi").encode()
            legacy_key = hashlib.sha256(legacy_seed).digest()
            raw = base64.b64decode(enc)
            plain = bytes(b ^ legacy_key[i % len(legacy_key)] for i, b in enumerate(raw)).decode("utf-8")
            # Re-encrypt with Fernet for auto-migration
            return plain
        except Exception:
            return enc   # last resort: return as-is (old unencrypted data)

@app.get("/api/wifi")
def api_wifi_get():
    return jsonify({"ssid": _wifi.get("ssid", ""),
                    "hasPassword": bool(_wifi.get("password"))})

@app.post("/api/wifi")
def api_wifi_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        if "ssid" in body:
            _wifi["ssid"] = body["ssid"]
        if "password" in body:
            _wifi["password"] = _encrypt_pw(body["password"])
        _save("wifi", _wifi)
    return jsonify(ok=True)

def get_wifi_password():
    """Get decrypted WiFi password (for firmware flashing)."""
    return _decrypt_pw(_wifi.get("password", ""))

#  "  "  Firmware management  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

try:
    from firmware_manager import list_ports, load_registry, flash_board, get_flash_status, detect_chip, query_serial
    _fw_available = True
except ImportError:
    _fw_available = False

# Firmware directory: check PyInstaller bundle first, then project root, then alongside exe
if getattr(sys, "frozen", False):
    _FW_DIR = Path(sys._MEIPASS) / "firmware"
    if not _FW_DIR.exists():
        _FW_DIR = Path(sys.executable).parent / "firmware"
else:
    _FW_DIR = BASE.parent.parent / "firmware"   # project root: ../../firmware from desktop/shared/
    if not _FW_DIR.exists():
        _FW_DIR = BASE / "firmware"

def _parent_wifi_hash():
    """Compute the same djb2 hash as the firmware for SSID+password comparison."""
    ssid = _wifi.get("ssid", "")
    pw = _decrypt_pw(_wifi.get("password", ""))
    h = 5381
    for c in ssid:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    for c in pw:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    return format(h, 'X')

@app.get("/api/firmware/ports")
def api_fw_ports():
    """Fast port list - no serial queries. Use /api/firmware/query for per-port info."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not installed"), 500
    return jsonify(list_ports())

@app.post("/api/firmware/query")
def api_fw_query_port():
    """Query a single port via serial for version + wifi hash. Slow (~2s)."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    info = query_serial(port, timeout=2.0)
    if not info:
        return jsonify(ok=True, fwVersion=None, fwBoard=None, wifiMatch=None)
    parent_hash = _parent_wifi_hash()
    bmap = {"esp32": "esp32", "d1mini": "d1mini", "giga-child": "giga", "giga-parent": "giga"}
    return jsonify(ok=True,
                   fwVersion=info.get("version"),
                   fwBoard=info.get("board"),
                   board=bmap.get(info.get("board", ""), None),
                   wifiHash=info.get("wifiHash"),
                   wifiMatch=(info.get("wifiHash") == parent_hash) if info.get("wifiHash") else None)

@app.get("/api/firmware/registry")
def api_fw_registry():
    return jsonify(load_registry(_FW_DIR))

@app.post("/api/firmware/download")
def api_fw_download():
    """Download latest firmware from GitHub Releases and save locally for USB flashing."""
    body = request.get_json(silent=True) or {}
    board = body.get("board", "")
    if board not in ("esp32", "d1mini"):
        return jsonify(ok=False, err="board must be esp32 or d1mini"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    # USB flash needs merged binary; OTA needs app-only. Download both for ESP32.
    # For D1 Mini there's only one binary that works for both.
    downloads = {
        "esp32": [
            ("esp32-firmware-merged.bin", "esp32/main.ino.merged.bin"),
            ("esp32-firmware-app.bin",    "esp32/main.ino.bin"),
        ],
        "d1mini": [
            ("d1mini-firmware.bin", "d1mini/main.ino.bin"),
        ],
    }
    assets_available = {a["name"]: a["url"] for a in rel.get("assets", [])}
    pairs = downloads.get(board, [])
    downloaded = 0
    import urllib.request as _ur
    try:
        for asset_name, target_path in pairs:
            url = assets_available.get(asset_name)
            if not url:
                log.warning("Asset %s not in release, skipping", asset_name)
                continue
            log.info("Downloading %s from %s", asset_name, url)
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=60)
            data = resp.read()
            dest = _FW_DIR / target_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            log.info("Downloaded %s (%d bytes)  -' %s", asset_name, len(data), dest)
            downloaded += 1
        if downloaded == 0:
            return jsonify(ok=False, err=f"No firmware assets for {board} in release"), 404
        # Update local registry version
        reg_path = _FW_DIR / "registry.json"
        if reg_path.exists():
            reg = json.loads(reg_path.read_text())
            for fw in reg.get("firmware", []):
                if fw.get("board") == board and "child" in fw.get("id", ""):
                    fw["version"] = rel["version"]
            reg_path.write_text(json.dumps(reg, indent=2))
        return jsonify(ok=True, version=rel["version"], downloaded=downloaded)
    except Exception as e:
        log.error("Download failed: %s", e)
        return jsonify(ok=False, err=str(e)), 502

@app.get("/api/firmware/binary/<board>")
def api_fw_binary(board):
    """Serve a firmware binary for OTA   " child downloads from parent over plain HTTP.
    ESP32 OTA needs app-only binary (main.ino.bin), NOT the merged binary."""
    file_map = {"esp32": "esp32/main.ino.bin", "d1mini": "d1mini/main.ino.bin"}
    rel_path = file_map.get(board)
    if not rel_path:
        return jsonify(ok=False, err=f"unknown board: {board}"), 404
    bin_path = _FW_DIR / rel_path
    if not bin_path.exists():
        # Try downloading from GitHub first
        rel = _fetch_github_release()
        if rel:
            # OTA needs app-only binary; try esp32-firmware-app.bin first, fallback to merged
            asset_names = {"esp32": ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
                           "d1mini": ["d1mini-firmware.bin"]}
            asset_name = None
            for name in asset_names.get(board, []):
                if any(a["name"] == name for a in rel.get("assets", [])):
                    asset_name = name
                    break
            for a in rel.get("assets", []):
                if a["name"] == asset_name:
                    try:
                        import urllib.request as _ur
                        log.info("Downloading %s from GitHub for proxy serve", asset_name)
                        req = _ur.Request(a["url"], headers={"User-Agent": "SlyLED-Parent"})
                        resp = _ur.urlopen(req, timeout=60)
                        data = resp.read()
                        bin_path.parent.mkdir(parents=True, exist_ok=True)
                        bin_path.write_bytes(data)
                    except Exception as e:
                        log.error("Download failed: %s", e)
                        return jsonify(ok=False, err="download from GitHub failed"), 502
                    break
    if not bin_path.exists():
        return jsonify(ok=False, err="firmware binary not available"), 404
    return send_file(str(bin_path), mimetype="application/octet-stream",
                     download_name=f"slyled-{board}.bin")

@app.post("/api/firmware/detect")
def api_fw_detect():
    """Detect chip type on an ambiguous port."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    chip = detect_chip(port)
    return jsonify(ok=True, board=chip)

@app.post("/api/firmware/flash")
def api_fw_flash():
    """Flash firmware to a board in a background thread."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    if not _wifi.get("ssid") or not _wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required before flashing - set them on the Firmware tab first"), 400
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    fw_id = body.get("firmwareId", "")
    board = body.get("board", "")
    if not port or not fw_id:
        return jsonify(ok=False, err="port and firmwareId required"), 400
    reg = load_registry(_FW_DIR)
    fw = next((f for f in reg.get("firmware", []) if f["id"] == fw_id), None)
    if not fw:
        return jsonify(ok=False, err="firmware not found in registry"), 404
    bin_path = _FW_DIR / fw["file"]
    if not bin_path.exists():
        return jsonify(ok=False, err=f"binary not found: {fw['file']}"), 404
    # Flash in background thread
    def _do_flash():
        flash_board(port, str(bin_path), board or fw["board"],
                    wifi_ssid=_wifi.get("ssid"), wifi_pass=_decrypt_pw(_wifi.get("password", "")))
    threading.Thread(target=_do_flash, daemon=True).start()
    return jsonify(ok=True, message="Flashing started")

@app.get("/api/firmware/flash/status")
def api_fw_flash_status():
    if not _fw_available:
        return jsonify(running=False, progress=0, message="not available")
    return jsonify(get_flash_status())

#  "  "  Help (Phase 7)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_HELP_SECTIONS = {
    "dash": "Dashboard",
    "setup": "Setup",
    "layout": "1-getting-started",
    "spatial-effects": "3-spatial-effects",
    "timeline": "4-timeline",
    "settings": "Settings",
    "firmware": "Firmware",
}

@app.get("/api/help/<section>")
def api_help(section):
    """Return help content for a given section, extracted from USER_MANUAL.md."""
    manual_path = BASE.parent.parent / "docs" / "USER_MANUAL.md"
    if not manual_path.exists():
        return jsonify(html="<p>User manual not found.</p>")
    try:
        text = manual_path.read_text(encoding="utf-8")
        # Find section by heading
        anchor = _HELP_SECTIONS.get(section, section)
        lines = text.split("\n")
        collecting = False
        result = []
        for line in lines:
            if line.startswith("## ") and anchor.lower() in line.lower():
                collecting = True
                result.append(line)
                continue
            if collecting and line.startswith("## "):
                break
            if collecting:
                result.append(line)
        if not result:
            return jsonify(html=f"<p>No help found for '{section}'.</p>")
        # Simple markdown  -' HTML conversion
        html = ""
        for line in result:
            if line.startswith("### "):
                html += f"<h4 style='color:#e2e8f0;margin:1em 0 .4em'>{line[4:]}</h4>"
            elif line.startswith("## "):
                html += f"<h3 style='color:#22d3ee;margin:0 0 .6em'>{line[3:]}</h3>"
            elif line.startswith("| "):
                html += f"<div style='font-family:monospace;font-size:.85em;color:#64748b'>{line}</div>"
            elif line.startswith("- "):
                html += f"<div style='padding-left:1em'>&#x2022; {line[2:]}</div>"
            elif line.strip():
                html += f"<p style='margin:.3em 0'>{line}</p>"
        return jsonify(html=html)
    except Exception as e:
        return jsonify(html=f"<p>Error loading help: {e}</p>")

@app.post("/api/reset")
def api_reset():
    """Clear all data and restore default settings."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    global _children, _settings, _layout, _stage, _actions
    global _fixtures, _objects, _temporal_objects, _spatial_fx, _timelines
    global _wifi, _nxt_c, _nxt_a, _dmx_settings, _bake_result
    global _nxt_fix, _nxt_obj, _nxt_sfx, _nxt_tl
    # Stop DMX playback + engines
    _dmx_playback_stop.set()
    try:
        _artnet.stop()
    except Exception:
        pass
    try:
        _sacn.stop()
    except Exception:
        pass
    # Stop all children
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c.get("ip"):
            if c.get("type") == "wled":
                wled_stop(c["ip"])
            else:
                _send(c["ip"], pkt_stop)
                _send(c["ip"], pkt_off)
    _live_events.clear()
    _bake_result.clear()
    with _lock:
        _children = []
        _actions  = []
        _wifi     = {"ssid": "", "password": ""}
        _ssh      = {"sshUser": "root", "sshPassword": "", "sshKeyPath": ""}
        _layout   = dict(_DEFAULT_LAYOUT)
        _stage    = dict(_DEFAULT_STAGE)
        _settings = dict(_DEFAULT_SETTINGS)
        _fixtures   = list(_DEFAULT_FIXTURES)
        _objects    = list(_DEFAULT_OBJECTS)
        _temporal_objects.clear()
        _spatial_fx = list(_DEFAULT_SPATIAL_FX)
        _timelines  = list(_DEFAULT_TIMELINES)
        _dmx_settings = {"protocol": "artnet", "frameRate": 40, "bindIp": "0.0.0.0",
                         "universeRoutes": [], "sacnPriority": 100, "sacnSourceName": "SlyLED"}
        _nxt_c = _nxt_a = 0
        _nxt_fix = _nxt_obj = _nxt_sfx = _nxt_tl = 0
        _save("children", _children)
        _save("actions",  _actions)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("stage",    _stage)
        _save("settings", _settings)
        _save("fixtures",   _fixtures)
        _save("objects",    _objects)
        _save("spatial_fx", _spatial_fx)
        _save("timelines",  _timelines)
        _save("dmx_settings", _dmx_settings)
        _show_playlist.clear()
        _show_playlist.update({"order": [], "loopAll": False})
        _save("show_playlist", _show_playlist)
        _camera_ssh.clear()
        _save("camera_ssh", _camera_ssh)
        _calibrations.clear()
        _save("calibrations", _calibrations)
        _range_cal.clear()
        _save("range_calibrations", _range_cal)
        _mover_cal.clear()
        _save("mover_calibrations", _mover_cal)
        _mover_cal_jobs.clear()
        _calib_state.clear()
        _tracking_state.clear()
        # Delete custom profiles (keep built-ins)
        for p in list(_profile_lib._profiles.values()):
            if not p.get("builtin"):
                _profile_lib.delete_profile(p["id"])
    return jsonify(ok=True)

#  "  "  OTA firmware update  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_github_release_cache = {"data": None, "ts": 0}
_GITHUB_RELEASE_TTL = 3600  # 1 hour cache

def _fetch_github_release():
    """Fetch latest release info from GitHub API. Returns dict or None."""
    import urllib.request as _ur
    now = time.time()
    if _github_release_cache["data"] and now - _github_release_cache["ts"] < _GITHUB_RELEASE_TTL:
        return _github_release_cache["data"]
    try:
        req = _ur.Request(
            "https://api.github.com/repos/SlyWombat/SlyLED/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SlyLED-Parent"})
        resp = _ur.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "").lstrip("v")
        assets = []
        for a in data.get("assets", []):
            assets.append({
                "name": a["name"],
                "size": a.get("size", 0),
                "url": a.get("browser_download_url", ""),
            })
        result = {"version": tag, "tag": data.get("tag_name", ""), "assets": assets,
                  "url": data.get("html_url", "")}
        _github_release_cache["data"] = result
        _github_release_cache["ts"] = now
        log.info("GitHub release: v%s (%d assets)", tag, len(assets))
        return result
    except Exception as e:
        log.debug("GitHub release fetch failed: %s", e)
        return _github_release_cache.get("data")  # return stale cache if available

@app.get("/api/firmware/latest")
def api_firmware_latest():
    """Return latest firmware version from GitHub Releases."""
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info from GitHub"), 502
    # Include registry firmware version + whether release has firmware binaries
    registry = load_registry(_FW_DIR).get("firmware", [])
    reg_versions = {e.get("board"): e.get("version", "0.0") for e in registry}
    has_fw = any(a.get("name", "").endswith(".bin") for a in rel.get("assets", []))
    return jsonify(**rel, registryVersion=max(reg_versions.values(), default="0.0"),
                   hasFirmware=has_fw)

@app.get("/api/firmware/check")
def api_firmware_check():
    """Compare all children firmware against latest release. Returns per-child update status."""
    if not _wifi.get("ssid") or not _wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required - set them on the Firmware tab before checking for updates"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    # Use registry.json firmware version, not GitHub tag (desktop releases != firmware releases)
    registry = load_registry(_FW_DIR).get("firmware", [])
    reg_versions = {e.get("board"): e.get("version", "0.0") for e in registry}
    gh_version = rel.get("version", "0.0")
    # Only use GitHub version if release has firmware binaries attached
    has_firmware_assets = any(a.get("name", "").endswith(".bin") for a in rel.get("assets", []))
    latest = gh_version if has_firmware_assets else max(reg_versions.values(), default="0.0")
    results = []
    for c in _children:
        fw = c.get("fwVersion", "0.0") or "0.0"
        needs_update = False
        try:
            cur_parts = [int(x) for x in fw.split(".")]
            lat_parts = [int(x) for x in latest.split(".")]
            # Pad to 3 parts for consistent comparison (7.0  -' 7.0.0)
            while len(cur_parts) < 3: cur_parts.append(0)
            while len(lat_parts) < 3: lat_parts.append(0)
            needs_update = lat_parts > cur_parts
        except (ValueError, IndexError):
            needs_update = fw != latest
        # Determine board type from stored boardType (from /status probe) or fallback
        bt = c.get("boardType", "")
        if c.get("type") == "wled":
            board = "wled"
        elif bt in ("ESP32", "esp32"):
            board = "esp32"
        elif bt in ("D1 Mini", "d1mini"):
            board = "d1mini"
        elif bt in ("Giga", "giga-child"):
            board = "giga"
        else:
            board = "esp32"  # default fallback
        # OTA needs app-only binary for ESP32; try app first, fallback to merged
        asset_prefs = {"esp32": ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
                       "d1mini": ["d1mini-firmware.bin"]}
        download_url = ""
        for name in asset_prefs.get(board, []):
            for a in rel.get("assets", []):
                if a["name"] == name:
                    download_url = a["url"]
                    break
            if download_url:
                break
        results.append({
            "id": c["id"], "hostname": c.get("hostname"), "name": c.get("name", ""),
            "ip": c.get("ip", ""),
            "currentVersion": fw, "latestVersion": latest,
            "needsUpdate": needs_update, "board": board,
            "status": c.get("status", 0),
            "downloadUrl": download_url,
        })
    return jsonify({"latest": latest, "children": results})

@app.post("/api/firmware/ota/<int:cid>")
def api_firmware_ota(cid):
    """Trigger OTA update on a specific child."""
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        return jsonify(ok=False, err="child not found"), 404
    if child.get("type") == "wled":
        return jsonify(ok=False, err="WLED devices update through their own UI"), 400
    if child.get("status") != 1:
        return jsonify(ok=False, err="child is offline"), 400

    # Require WiFi credentials to be configured before OTA
    if not _wifi.get("ssid"):
        return jsonify(ok=False, err="WiFi credentials not configured - set them on the Firmware tab first"), 400

    # Push WiFi credentials to child before OTA (so new firmware can reconnect)
    ip = child["ip"]
    try:
        import urllib.request as _ur
        wifi_body = json.dumps({"ssid": _wifi["ssid"],
                                "password": _decrypt_pw(_wifi.get("password", ""))}).encode()
        wifi_req = _ur.Request(f"http://{ip}/wifi", data=wifi_body, method="POST",
                               headers={"Content-Type": "application/json"})
        _ur.urlopen(wifi_req, timeout=3)
        log.info("OTA: pushed WiFi credentials to %s", ip)
    except Exception as e:
        log.warning("OTA: failed to push WiFi to %s: %s (continuing anyway)", ip, e)

    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    latest = rel.get("version", "0.0")

    # Determine board type from stored boardType
    bt = child.get("boardType", "")
    board = "esp32" if bt in ("ESP32", "esp32") else "d1mini" if bt in ("D1 Mini", "d1mini") else "esp32"
    # OTA needs app-only binary for ESP32; try app first, fallback to merged
    asset_prefs = {"esp32": ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
                   "d1mini": ["d1mini-firmware.bin"]}
    download_url = ""
    for name in asset_prefs.get(board, []):
        for a in rel.get("assets", []):
            if a["name"] == name:
                download_url = a["url"]
                break
        if download_url:
            break
    if not download_url:
        return jsonify(ok=False, err=f"no firmware binary for {board}"), 404

    # Parse version
    try:
        parts = latest.split(".")
        new_major = int(parts[0])
        new_minor = int(parts[1]) if len(parts) > 1 else 0
        new_patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return jsonify(ok=False, err="invalid version format"), 500

    # Send OTA command   " use parent as proxy (child can't do HTTPS to GitHub)
    ip = child["ip"]
    # Determine parent's LAN IP for the proxy URL
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        parent_ip = s.getsockname()[0]
        s.close()
    except Exception:
        parent_ip = "127.0.0.1"
    # Use the actual Flask port from the incoming request (not hardcoded 8080)
    parent_port = request.host.split(":")[-1] if ":" in request.host else "8080"
    proxy_url = f"http://{parent_ip}:{parent_port}/api/firmware/binary/{board}"
    log.info("OTA: triggering update on %s (%s) to v%s via proxy %s", ip, child.get("hostname"), latest, proxy_url)
    try:
        import urllib.request as _ur
        body = json.dumps({"url": proxy_url, "sha256": "", "major": new_major, "minor": new_minor, "patch": new_patch}).encode()
        req = _ur.Request(f"http://{ip}/ota", data=body, method="POST",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception as e:
        log.warning("OTA trigger to %s failed: %s", ip, e)
        # Child may have already started updating and dropped the connection   " that's OK
        pass

    return jsonify(ok=True, version=latest, board=board)

#  "  "  QR code for mobile app  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/qr")
def api_qr():
    """Generate a QR code PNG encoding slyled://{host}:{port} for the mobile app."""
    try:
        import qrcode
    except ImportError:
        return jsonify(ok=False, err="qrcode package not installed"), 500
    # Use the machine's LAN IP, not request.host (which may be localhost)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host = s.getsockname()[0]
        s.close()
    except Exception:
        host = request.host.split(":")[0]
    port = request.host.split(":")[-1] if ":" in request.host else "8080"
    url = f"slyled://{host}:{port}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="slyled-qr.png")

#  "  "  CORS  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.after_request
def add_cors(response):
    # Allow same-origin and Android app connections from LAN
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        response.headers["Access-Control-Allow-Origin"] = request.host_url.rstrip("/")
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SlyLED-Confirm"
    return response

#  "  "  Shutdown  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/shutdown")
def api_shutdown():
    """Terminate the parent process after sending the response."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    def _kill():
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_kill, daemon=True).start()
    return jsonify(ok=True)

#  "  "  SPA fallback - must be last  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.route("/lib/<path:filename>")
def spa_lib(filename):
    """Serve bundled JS libraries (Three.js etc.) — no internet required (#269)."""
    return send_from_directory(str(SPA / "lib"), filename)

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa_fallback(path):
    if path.startswith("api/") or path in ("status", "favicon.ico"):
        abort(404)
    resp = send_from_directory(str(SPA), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

#  "  "  Entry point  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _check_single_instance(port):
    """Check if another instance is already running on this port."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://localhost:{port}/status", timeout=2)
        data = resp.read().decode()
        if "parent" in data or "SlyLED" in data:
            return True   # another instance is running
    except Exception:
        pass
    return False

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SlyLED Parent Server")
    ap.add_argument("--port",       type=int, default=8080)
    ap.add_argument("--host",       default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if _check_single_instance(args.port):
        print(f"SlyLED Orchestrator is already running on port {args.port}.")
        print(f"Opening browser to existing instance...")
        webbrowser.open(f"http://localhost:{args.port}")
        sys.exit(0)

    start_background_tasks()

    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open, daemon=True).start()

    print(f"SlyLED Orchestrator  v{VERSION}")
    print(f"  UI   -> http://localhost:{args.port}")
    print(f"  Data -> {DATA}")
    app.run(host=args.host, port=args.port, threaded=True)








