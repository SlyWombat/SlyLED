#!/usr/bin/env python3
"""
SlyLED Parent Server   " Windows / Mac desktop parent application.

Replaces the Arduino Giga R1 as the full-featured parent.  Once a layout
and runner set is designed here it can be exported and loaded onto a Giga
running the minimal runtime firmware.

Usage (from project root):
    pip install -r desktop/windows/requirements.txt
    python desktop/shared/parent_server.py [--port 8080] [--no-browser]
"""

import argparse
import copy
import json
import math
import os
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
import logging
from datetime import datetime

from wled_bridge import (wled_probe, wled_set_state, wled_map_action, wled_map_step,
                         wled_stop, wled_get_effects, wled_get_palettes, wled_get_segments)
from spatial_engine import (catmull_rom_sample, resolve_fixture,
                            evaluate_spatial_effect, blend_pixel_layers)
from bake_engine import (bake_timeline, pack_lsq_zip, segments_to_load_steps,
                         BakeProgress)
from dmx_profiles import ProfileLibrary
from dmx_artnet import ArtNetEngine
from dmx_sacn import sACNEngine

log = logging.getLogger("slyled")
log.setLevel(logging.DEBUG)
_log_handler = None   # file handler, created/removed by _apply_logging()

def _apply_logging(enabled):
    """Enable/disable file logging.  Each enable creates a new timestamped log file."""
    global _log_handler
    # Remove existing file handler
    if _log_handler:
        log.removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None
    if enabled:
        log_dir = DATA / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fh = logging.FileHandler(str(log_dir / f"slyled_{ts}.log"), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(fh)
        _log_handler = fh
        log.info("Logging started  -' %s", fh.baseFilename)

#  "  "  Version  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

VERSION = "7.5.11"

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
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False, "activeRunner": -1, "runnerElapsed": 0,
    "runnerLoop": True,
})
_layout  = _load("layout",  {"canvasW": 10000, "canvasH": 5000, "children": []})
_stage   = _load("stage",   {"w": 10.0, "h": 5.0, "d": 10.0})
_fixtures   = _load("fixtures",   [])

#  "  "  Fixture migration: backfill fixtureType on old data  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_fix_patched = False
for _f in _fixtures:
    if "fixtureType" not in _f:
        _f["fixtureType"] = "led"
        _fix_patched = True
if _fix_patched:
    _save("fixtures", _fixtures)
del _fix_patched

_surfaces   = _load("surfaces",   [])
_spatial_fx = _load("spatial_fx", [])
_timelines  = _load("timelines",  [])
_runners = _load("runners", [])
_actions = _load("actions", [])
_flights = _load("flights", [])
_shows   = _load("shows",   [])
_wifi    = _load("wifi",    {"ssid": "", "password": ""})

MAX_RUNNERS = 4

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
_nxt_r = max((r["id"] for r in _runners),  default=-1) + 1
_nxt_a = max((a["id"] for a in _actions),  default=-1) + 1
_nxt_f = max((f["id"] for f in _flights),  default=-1) + 1
_nxt_s = max((s["id"] for s in _shows),    default=-1) + 1
_nxt_fix = max((f["id"] for f in _fixtures),   default=-1) + 1
_nxt_sf  = max((f["id"] for f in _surfaces),   default=-1) + 1
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
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            parts = ip.rsplit(".", 1)
            if len(parts) == 2:
                bc = parts[0] + ".255"
                if bc not in bcs:
                    bcs.append(bc)
    except Exception:
        pass
    return bcs

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
    Sends both v3 and v4 PINGs so old and new firmware both respond.
    The UDP listener daemon handles incoming PONGs  -' _recent_pongs."""
    pkt4 = _hdr(CMD_PING)
    # Also send v3 ping for backward compat during OTA transition
    pkt3 = struct.pack("<HBBI", UDP_MAGIC, 3, CMD_PING, int(time.time()) & 0xFFFFFFFF)
    for c in list(_children):
        _send(c["ip"], pkt4)
        _send(c["ip"], pkt3)
    for bc in ["255.255.255.255"] + _local_broadcasts():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(pkt4, (bc, UDP_PORT))
                s.sendto(pkt3, (bc, UDP_PORT))
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
    """Broadcast PING, wait for listener to collect PONGs, return unknown LED performers.
    Excludes DMX bridges (boardType 'dmx') by probing /status on each discovered IP."""
    known_ips = {c["ip"] for c in _children}
    known_hosts = {c.get("hostname") for c in _children}
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    results = []
    for ip, info in _recent_pongs.items():
        if ip in known_ips or info.get("hostname") in known_hosts:
            continue
        # Probe /status to check if this is a DMX bridge
        try:
            import urllib.request as _ur
            resp = _ur.urlopen(f"http://{ip}/status", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("boardType") == "dmx":
                continue  # skip DMX bridges
        except Exception:
            pass  # if probe fails, include it (probably an LED performer)
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

def _action_pkt(act, child):
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(act)
    ls, le = _child_led_ranges(child)
    return _hdr(CMD_ACTION) + struct.pack("<BBBBHBBBB", t, r, g, b, p16a, p8a, p8b, p8c, p8d) + ls + le

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

def start_background_tasks():
    """Call once after import to kick off periodic ping and UDP listener threads."""
    global _startup_check_done
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
    return jsonify(ok=True, id=child["id"], type=child.get("type", "slyled"))

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
    layout["fixtures"] = []
    for f in _fixtures:
        fid = f["id"]
        pos = pos_map.get(fid, pos_map.get(f.get("childId"), {}))
        layout["fixtures"].append({
            **f,
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
    # Accept fixture positions (new) or children positions (legacy)
    fixtures = body.get("fixtures", [])
    if fixtures:
        _layout["children"] = [{"id": f["id"], "x": f.get("x", 0), "y": f.get("y", 0), "z": f.get("z", 0)} for f in fixtures]
    else:
        _layout["children"] = body.get("children", [])
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
    return jsonify(ok=True)

def _auto_create_fixtures():
    """Auto-create linear fixtures from all registered children."""
    global _nxt_fix
    with _lock:
        existing = {f.get("childId") for f in _fixtures}
        for child in _children:
            if child["id"] in existing:
                continue
            f = {
                "id": _nxt_fix,
                "name": child.get("name") or child.get("hostname") or f"Fixture {_nxt_fix}",
                "fixtureType": "led",
                "childId": child["id"],
                "type": "linear",
                "strings": [],
                "rotation": [0, 0, 0],
                "childIds": [],
                "aoeRadius": 1000,
            }
            _fixtures.append(f)
            _nxt_fix += 1
        _save("fixtures", _fixtures)

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
    if fixture_type not in ("led", "dmx"):
        return jsonify(err="Invalid fixtureType - must be 'led' or 'dmx'"), 400
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
    for k in ("name", "type", "fixtureType", "childId", "childIds", "strings",
              "rotation", "aoeRadius", "meshFile",
              "dmxUniverse", "dmxStartAddr", "dmxChannelCount", "dmxProfileId"):
        if k in body:
            f[k] = body[k]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

@app.delete("/api/fixtures/<int:fid>")
def api_fixture_delete(fid):
    global _fixtures
    _fixtures = [f for f in _fixtures if f["id"] != fid]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

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

#  "  "  Surfaces (Phase 2)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/surfaces")
def api_surfaces_get():
    return jsonify(_surfaces)

@app.post("/api/surfaces")
def api_surfaces_create():
    global _nxt_sf
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    with _lock:
        s = {
            "id": _nxt_sf, "name": name or f"Surface {_nxt_sf}",
            "surfaceType": body.get("surfaceType", "custom"),
            "filename": body.get("filename", ""),
            "color": body.get("color", "#334155"),
            "opacity": body.get("opacity", 30),
            "transform": body.get("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]}),
        }
        _surfaces.append(s)
        _nxt_sf += 1
        _save("surfaces", _surfaces)
    return jsonify(ok=True, id=s["id"])

@app.delete("/api/surfaces/<int:sid>")
def api_surface_delete(sid):
    global _surfaces
    _surfaces = [s for s in _surfaces if s["id"] != sid]
    _save("surfaces", _surfaces)
    return jsonify(ok=True)

#  "  "  DMX Profiles  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/dmx-profiles")
def api_dmx_profiles():
    cat = request.args.get("category")
    return jsonify(_profile_lib.list_profiles(category=cat))

@app.get("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_get(profile_id):
    p = _profile_lib.get_profile(profile_id)
    if not p:
        return jsonify(err="Not found"), 404
    return jsonify(p)

@app.post("/api/dmx-profiles")
def api_dmx_profile_create():
    body = request.get_json(silent=True) or {}
    ok_valid, err = _profile_lib.validate_profile(body)
    if not ok_valid:
        return jsonify(err=err), 400
    if _profile_lib.save_profile(body):
        return jsonify(ok=True, id=body["id"])
    return jsonify(err="Failed to save"), 500

@app.delete("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_delete(profile_id):
    if _profile_lib.delete_profile(profile_id):
        return jsonify(ok=True)
    return jsonify(err="Cannot delete (built-in or not found)"), 400

#  "  "  DMX Output Engines  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/dmx/status")
def api_dmx_status():
    return jsonify(
        artnet=_artnet.status(),
        sacn=_sacn.status(),
    )

@app.post("/api/dmx/start")
def api_dmx_start():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol", "artnet")
    if protocol == "artnet":
        _artnet.start()
    elif protocol == "sacn":
        _sacn.start()
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
    """Return Art-Net nodes discovered via ArtPoll."""
    return jsonify(_artnet.discovered_nodes)

# -- DMX Settings (persistent) ------------------------------------------------

_DMX_SETTINGS_DEFAULTS = {
    "protocol": "artnet",
    "frameRate": 40,
    "bindIp": "0.0.0.0",
    "unicastTargets": {},
    "sacnPriority": 100,
    "sacnSourceName": "SlyLED",
}
_dmx_settings = _load("dmx_settings", dict(_DMX_SETTINGS_DEFAULTS))

def _apply_dmx_settings():
    """Apply persisted DMX settings to engines."""
    s = _dmx_settings
    _artnet.configure(
        bind_ip=s.get("bindIp", "0.0.0.0"),
        unicast_targets={int(k): v for k, v in s.get("unicastTargets", {}).items()},
        frame_rate=s.get("frameRate", 40),
    )
    _sacn.configure(
        source_name=s.get("sacnSourceName", "SlyLED"),
        priority=s.get("sacnPriority", 100),
        bind_ip=s.get("bindIp", "0.0.0.0"),
        frame_rate=s.get("frameRate", 40),
    )

_apply_dmx_settings()

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
    for k in ("protocol", "frameRate", "bindIp", "unicastTargets",
              "sacnPriority", "sacnSourceName"):
        if k in body:
            _dmx_settings[k] = body[k]
    fr = _dmx_settings.get("frameRate", 40)
    if not isinstance(fr, int) or fr < 1 or fr > 44:
        _dmx_settings["frameRate"] = 40
    pri = _dmx_settings.get("sacnPriority", 100)
    if not isinstance(pri, int) or pri < 0 or pri > 200:
        _dmx_settings["sacnPriority"] = 100
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
        channels = [{"offset": ch["offset"], "name": ch["name"], "type": ch["type"]}
                    for ch in profile.get("channels", [])]
    else:
        channels = [{"offset": i, "name": f"Ch {i+1}", "type": "dimmer"} for i in range(count)]
    # Read current values from universe buffer
    for ch in channels:
        dmx_addr = addr + ch["offset"]
        val = 0
        if _artnet.running:
            val = _artnet.get_universe(uni).get_channel(dmx_addr)
        elif _sacn.running:
            val = _sacn.get_universe(uni).get_channel(dmx_addr)
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

    # Auto-create fixtures from children if none exist (e.g. after factory reset)
    if not _fixtures and _children:
        _auto_create_fixtures()

    # Expand allPerformers tracks
    raw_tracks = tl.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            for f in _fixtures:
                tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
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

    # Auto-create fixtures from children if none exist
    if not _fixtures:
        _auto_create_fixtures()

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
            )
            # Store result (strip raw frame data to save memory, keep segments + LSQ)
            _bake_result[tid] = {
                "timelineId": tid,
                "bakedAt": int(time.time()),
                "fixtures": result["fixtures"],
                "totalFrames": result["totalFrames"],
                "fps": result["fps"],
                "lsqSize": sum(len(v) for v in result.get("lsq_files", {}).values()),
                "preview": result.get("preview", {}),
            }
            # Save LSQ files to data/baked/
            baked_dir = DATA / "baked"
            baked_dir.mkdir(parents=True, exist_ok=True)
            for fix_id, lsq_data in result.get("lsq_files", {}).items():
                (baked_dir / f"fixture_{fix_id}.lsq").write_bytes(lsq_data)
            # Save zip bundle
            zip_data = pack_lsq_zip(result.get("lsq_files", {}))
            (baked_dir / f"timeline_{tid}.zip").write_bytes(zip_data)
        except Exception as e:
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
        return jsonify(err="No baked data"), 404
    return jsonify(result.get("preview", {}))

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

@app.post("/api/timelines/<int:tid>/start")
def api_timeline_start(tid):
    """Send RUNNER_GO to all children. Call AFTER sync is complete."""
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

    return jsonify(ok=True, started=started, goEpoch=go_epoch)

@app.post("/api/timelines/<int:tid>/stop")
def api_timeline_stop(tid):
    """Stop timeline playback on all children."""
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    stopped = 0
    # Send stop packets 3 times for UDP reliability
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

#  "  "  Settings  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/settings")
def api_settings_get():
    s = dict(_settings)
    # Compute elapsed dynamically from start epoch
    if s.get("runnerRunning") and s.get("runnerStartEpoch"):
        elapsed = max(0, int(time.time()) - s["runnerStartEpoch"])
        # Compute total duration of active runner for loop detection
        rid = s.get("activeRunner", -1)
        rn = next((r for r in _runners if r["id"] == rid), None)
        total = sum(st.get("durationS", 0) for st in rn.get("steps", [])) if rn else 0
        if total > 0 and s.get("runnerLoop") and elapsed >= total:
            elapsed = elapsed % total   # wrap for looping
        s["runnerElapsed"] = elapsed
    return jsonify(s)

@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "units", "canvasW", "canvasH", "darkMode", "runnerLoop", "globalBrightness", "logging"):
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
        _apply_logging(body["logging"])
    return jsonify(ok=True)

#  "  "  Action  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/action")
def api_action():
    act = request.get_json(silent=True) or {}
    tgt = str(act.get("target", "all"))
    targets = (_children if tgt == "all"
                else [c for c in _children if str(c["id"]) == tgt])
    if tgt != "all" and not targets:
        return jsonify(ok=False, err="target not found"), 404
    for c in targets:
        if c.get("type") == "wled":
            wled_set_state(c["ip"], wled_map_action(act))
        else:
            _send(c["ip"], _action_pkt(act, c))
    return jsonify(ok=True)

@app.post("/api/action/stop")
def api_action_stop():
    body = request.get_json(silent=True) or {}
    tgt = str(body.get("target", "all"))
    targets = (_children if tgt == "all"
                else [c for c in _children if str(c["id"]) == tgt])
    if not targets and tgt != "all":
        return jsonify(ok=False, err="target not found"), 404
    for c in targets:
        if c.get("type") == "wled":
            wled_stop(c["ip"])
        else:
            _send(c["ip"], _hdr(CMD_ACTION_STOP))
    return jsonify(ok=True)

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
                  "wledFxOverride", "wledPalOverride", "wledSegId")  # WLED overrides

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

#  "  "  Runners  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/runners")
def api_runners():
    return jsonify([
        {"id": r["id"], "name": r["name"],
         "steps": len(r.get("steps", [])),
         "totalDurationS": sum(s.get("durationS", 0) for s in r.get("steps", [])),
         "computed": r.get("computed", False)}
        for r in _runners
    ])

@app.post("/api/runners")
def api_runners_create():
    global _nxt_r
    body = request.get_json(silent=True) or {}
    with _lock:
        if len(_runners) >= MAX_RUNNERS:
            return jsonify(ok=False, err="max runners reached"), 400
        r = {"id": _nxt_r, "name": body.get("name", "Runner"),
             "computed": False, "steps": []}
        _runners.append(r)
        _nxt_r += 1
        _save("runners", _runners)
    return jsonify(ok=True, id=r["id"])

# NOTE: /api/runners/stop must be registered before /<int:rid> so Flask
# doesn't try to cast "stop" as an integer.
@app.post("/api/runners/stop")
def api_runners_stop():
    _stop_all_shows()
    return jsonify(ok=True)

@app.get("/api/runners/live")
def api_runners_live():
    """Return per-child live action state from pushed ACTION_EVENT packets."""
    now = time.time()
    result = []
    for c in _children:
        ip = c.get("ip")
        ev = _live_events.get(ip)
        if ev and now - ev["ts"] < 30:
            result.append({
                "id": c["id"], "ip": ip,
                "actionType": ev["actionType"],
                "stepIndex": ev["stepIndex"],
                "totalSteps": ev["totalSteps"],
                "event": ev["event"],
                "age": round(now - ev["ts"], 1),
            })
        else:
            result.append({"id": c["id"], "ip": ip, "actionType": None})
    return jsonify(result)

@app.get("/api/runners/<int:rid>")
def api_runner_get(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(r)

@app.put("/api/runners/<int:rid>")
def api_runner_put(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with _lock:
        if "name"  in body: r["name"]  = body["name"]
        if "steps" in body: r["steps"] = body["steps"]; r["computed"] = False
        _save("runners", _runners)
    return jsonify(ok=True, steps=len(r.get("steps", [])))

@app.delete("/api/runners/<int:rid>")
def api_runner_delete(rid):
    global _runners
    with _lock:
        n = len(_runners)
        _runners = [x for x in _runners if x["id"] != rid]
        if len(_runners) == n:
            return jsonify(ok=False, err="not found"), 404
        _save("runners", _runners)
    return jsonify(ok=True)

@app.post("/api/runners/<int:rid>/compute")
def api_runner_compute(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    # Compute per-child delays for canvas-scoped steps
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    child_offsets = []   # list of dicts: {childId: delayMs} per step
    for step in r.get("steps", []):
        offsets = {}
        act = next((a for a in _actions if a["id"] == step.get("actionId")), None)
        if act and act.get("scope") == "canvas":
            dur_ms = step.get("durationS", 5) * 1000
            direction = act.get("direction", 0)  # 0=E, 1=N, 2=W, 3=S
            aoe_x0 = step.get("x0", 0) / 10000.0
            aoe_x1 = step.get("x1", 10000) / 10000.0
            aoe_y0 = step.get("y0", 0) / 10000.0
            aoe_y1 = step.get("y1", 10000) / 10000.0
            cw = _layout.get("canvasW", 10000)
            ch = _layout.get("canvasH", 5000)
            for c in _children:
                pos = pos_map.get(c["id"], {})
                cx = pos.get("x", 0) / cw if cw else 0
                cy = pos.get("y", 0) / ch if ch else 0
                # Project onto effect axis
                if direction == 0:   norm = (cx - aoe_x0) / max(aoe_x1 - aoe_x0, 0.001)  # East
                elif direction == 1: norm = (cy - aoe_y0) / max(aoe_y1 - aoe_y0, 0.001)  # North
                elif direction == 2: norm = (aoe_x1 - cx) / max(aoe_x1 - aoe_x0, 0.001)  # West
                else:                norm = (aoe_y1 - cy) / max(aoe_y1 - aoe_y0, 0.001)  # South
                norm = max(0.0, min(1.0, norm))
                # Use 80% of duration for staggering, 20% for actual effect
                offsets[c["id"]] = int(norm * dur_ms * 0.8)
        child_offsets.append(offsets)
    with _lock:
        r["computed"] = True
        r["childOffsets"] = child_offsets
        _save("runners", _runners)
    return jsonify(ok=True)

def _resolve_step(step):
    """Merge action library fields into a step for packet building.
    Step-level overrides (speedMs, direction, brightness, r/g/b) take priority.
    Supports both new (actionId) and legacy (inline type/r/g/b) formats."""
    if "actionId" in step:
        act = next((a for a in _actions if a["id"] == step["actionId"]), None)
        if not act:
            return None
        # Start with action fields, then overlay step-level overrides
        merged = {}
        for k in ("type", "r", "g", "b", "speedMs", "periodMs", "spawnMs",
                  "p8a", "p8b", "p8c", "p8d", "r2", "g2", "b2", "minBri",
                  "spacing", "paletteId", "cooling", "sparking", "direction",
                  "tailLen", "density", "decay", "fadeSpeed",
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct", "scope"):
            if k in act:
                merged[k] = act[k]
        # Step-level overrides take priority over action defaults
        for k in ("durationS", "targets", "brightness", "speedMs",
                  "direction", "scope", "r", "g", "b"):
            if k in step and step[k] is not None:
                merged[k] = step[k]
        # Preserve step metadata
        merged["actionId"] = step["actionId"]
        merged["durationS"] = step.get("durationS", merged.get("durationS", 5))
        return merged
    return step  # legacy inline format

@app.post("/api/runners/<int:rid>/sync")
def api_runner_sync(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    # Auto-compute if not already computed
    if not r.get("computed"):
        pos_map = {p["id"]: p for p in _layout.get("children", [])}
        child_offsets = []
        for step in r.get("steps", []):
            offsets = {}
            act = next((a for a in _actions if a["id"] == step.get("actionId")), None)
            if act and act.get("scope") == "canvas":
                dur_ms = step.get("durationS", 5) * 1000
                direction = act.get("direction", 0)
                cw = _layout.get("canvasW", 10000)
                ch = _layout.get("canvasH", 5000)
                for c in _children:
                    pos = pos_map.get(c["id"], {})
                    cx = pos.get("x", 0) / cw if cw else 0
                    cy = pos.get("y", 0) / ch if ch else 0
                    if direction == 0:   norm = cx
                    elif direction == 1: norm = cy
                    elif direction == 2: norm = 1.0 - cx
                    else:                norm = 1.0 - cy
                    norm = max(0.0, min(1.0, norm))
                    offsets[c["id"]] = int(norm * dur_ms * 0.8)
            child_offsets.append(offsets)
        with _lock:
            r["computed"] = True
            r["childOffsets"] = child_offsets
            _save("runners", _runners)
    steps = r.get("steps", [])
    if not steps:
        return jsonify(ok=False, err="no steps"), 400
    resolved = [_resolve_step(s) for s in steps]
    if any(rs is None for rs in resolved):
        return jsonify(ok=False, err="step references missing action"), 400
    # Use cached status from background ping thread (avoids UDP port conflicts)
    online = [c for c in _children if c.get("status") == 1]
    if not online:
        return jsonify(ok=True, sent=0, acked=0, online=0,
                       warn="no performers online" if _children else None)
    child_offsets = r.get("childOffsets", [])
    # Stop all children before loading new steps
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off  = _hdr(CMD_ACTION_STOP)
    for child in online:
        log.info("SYNC: sending STOP to %s (%s)", child["ip"], child.get("hostname"))
        _send(child["ip"], pkt_stop)
        _send(child["ip"], pkt_off)
    time.sleep(0.1)
    # Send global brightness to all online children before steps
    bri = _settings.get("globalBrightness", 255)
    bri_pkt = _hdr(CMD_SET_BRIGHTNESS) + bytes([bri & 0xFF])
    sent = 0
    try:
        for child in online:
            _send(child["ip"], bri_pkt)
            log.info("SYNC: loading %d steps to %s (%s)", len(resolved), child["ip"], child.get("hostname"))
            for i, step in enumerate(resolved):
                offsets = child_offsets[i] if i < len(child_offsets) else {}
                # JSON round-trip may stringify int keys   " try both
                cid = child["id"]
                delay_ms = offsets.get(cid, offsets.get(str(cid), 0))
                pkt = _load_step_pkt(i, len(resolved), step, child, delay_ms)
                _send(child["ip"], pkt)
                sent += 1
                log.debug("  step %d/%d type=%d delay=%dms  -' %s",
                          i, len(resolved), int(step.get("type", 0) or 0), int(delay_ms), child["ip"])
                time.sleep(0.03)   # 30ms gap between packets for reliable delivery
    except Exception as e:
        log.error("SYNC failed: %s", e, exc_info=True)
        return jsonify(ok=False, err=str(e)), 500
    log.info("SYNC complete: %d packets to %d children", sent, len(online))
    return jsonify(ok=True, sent=sent, online=len(online))

_wled_runner_stop = threading.Event()

def _start_wled_runner(runner, wled_children, go_epoch, loop):
    """Launch a background thread that drives WLED devices through runner steps."""
    _wled_runner_stop.clear()

    def _run():
        steps = runner.get("steps", [])
        resolved = [_resolve_step(s) for s in steps]
        resolved = [r for r in resolved if r]
        if not resolved:
            return
        offsets_list = runner.get("childOffsets", [])
        bri = _settings.get("globalBrightness", 255)

        # Wait until go_epoch
        wait = go_epoch - time.time()
        if wait > 0:
            if _wled_runner_stop.wait(wait):
                return

        while not _wled_runner_stop.is_set():
            for i, step in enumerate(resolved):
                if _wled_runner_stop.is_set():
                    return
                offsets = offsets_list[i] if i < len(offsets_list) else {}
                dur = int(step.get("durationS", 5) or 5)
                state = wled_map_step(step, bri)

                # Send to each WLED child with its delay offset
                for c in wled_children:
                    cid = c["id"]
                    delay_ms = offsets.get(cid, offsets.get(str(cid), 0))
                    if delay_ms > 0:
                        # Schedule delayed send
                        def _delayed(ip, st, d):
                            if not _wled_runner_stop.wait(d / 1000.0):
                                wled_set_state(ip, st)
                        threading.Thread(target=_delayed, args=(c["ip"], state, delay_ms), daemon=True).start()
                    else:
                        wled_set_state(c["ip"], state)

                # Wait for step duration
                if _wled_runner_stop.wait(dur):
                    return

            if not loop:
                break

        # Blackout WLED devices at end
        for c in wled_children:
            wled_stop(c["ip"])

    threading.Thread(target=_run, daemon=True).start()

@app.post("/api/runners/<int:rid>/start")
def api_runner_start(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    go_epoch = int(time.time()) + 5      # 5 s from now   " time for UDP to reach all children
    # CMD_RUNNER_GO: 4-byte startEpoch + 1-byte loop flag as PAYLOAD
    loop_flag = 1 if _settings.get("runnerLoop", True) else 0
    pkt = _hdr(CMD_RUNNER_GO) + struct.pack("<IB", go_epoch, loop_flag)
    online = [c for c in _children if c["status"] == 1]
    slyled_children = [c for c in online if c.get("type") != "wled"]
    wled_children = [c for c in online if c.get("type") == "wled"]
    for c in slyled_children:
        log.info("START: RUNNER_GO epoch=%d loop=%d  -' %s (%s)", go_epoch, loop_flag, c["ip"], c.get("hostname"))
        _send(c["ip"], pkt)
    # Start WLED runner thread for WLED devices
    if wled_children:
        _start_wled_runner(r, wled_children, go_epoch, bool(loop_flag))
    log.info("START: sent to %d SlyLED + %d WLED children", len(slyled_children), len(wled_children))
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeRunner"]  = rid
        _settings["runnerStartEpoch"] = go_epoch
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)
    return jsonify(ok=True)

#  "  "  Flights  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/flights")
def api_flights():
    return jsonify(_flights)

@app.post("/api/flights")
def api_flights_create():
    global _nxt_f
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    with _lock:
        f = {"id": _nxt_f, "name": name,
             "performerIds": body.get("performerIds", []),
             "runnerId": body.get("runnerId"),
             "priority": body.get("priority", 1)}
        _flights.append(f)
        _nxt_f += 1
        _save("flights", _flights)
    return jsonify(ok=True, id=f["id"])

@app.get("/api/flights/<int:fid>")
def api_flight_get(fid):
    f = next((x for x in _flights if x["id"] == fid), None)
    if not f:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(f)

@app.put("/api/flights/<int:fid>")
def api_flight_put(fid):
    f = next((x for x in _flights if x["id"] == fid), None)
    if not f:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "performerIds", "runnerId", "priority"):
            if k in body:
                f[k] = body[k]
        _save("flights", _flights)
    return jsonify(ok=True)

@app.delete("/api/flights/<int:fid>")
def api_flight_delete(fid):
    global _flights
    with _lock:
        n = len(_flights)
        _flights = [x for x in _flights if x["id"] != fid]
        if len(_flights) == n:
            return jsonify(ok=False, err="not found"), 404
        _save("flights", _flights)
    return jsonify(ok=True)

#  "  "  Shows  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_active_show_threads = []

@app.get("/api/shows")
def api_shows():
    return jsonify(_shows)

@app.post("/api/shows")
def api_shows_create():
    global _nxt_s
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    with _lock:
        s = {"id": _nxt_s, "name": name,
             "flightIds": body.get("flightIds", []),
             "loop": body.get("loop", True)}
        _shows.append(s)
        _nxt_s += 1
        _save("shows", _shows)
    return jsonify(ok=True, id=s["id"])

@app.get("/api/shows/<int:sid>")
def api_show_get(sid):
    s = next((x for x in _shows if x["id"] == sid), None)
    if not s:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(s)

@app.put("/api/shows/<int:sid>")
def api_show_put(sid):
    s = next((x for x in _shows if x["id"] == sid), None)
    if not s:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "flightIds", "loop"):
            if k in body:
                s[k] = body[k]
        _save("shows", _shows)
    return jsonify(ok=True)

@app.delete("/api/shows/<int:sid>")
def api_show_delete(sid):
    global _shows
    with _lock:
        n = len(_shows)
        _shows = [x for x in _shows if x["id"] != sid]
        if len(_shows) == n:
            return jsonify(ok=False, err="not found"), 404
        _save("shows", _shows)
    return jsonify(ok=True)

@app.post("/api/shows/<int:sid>/start")
def api_show_start(sid):
    """Start a show - syncs and starts all flights simultaneously."""
    s = next((x for x in _shows if x["id"] == sid), None)
    if not s:
        return jsonify(ok=False, err="not found"), 404

    # Stop any running show/runners first
    _stop_all_shows()

    show_flights = [f for f in _flights if f["id"] in s.get("flightIds", [])]
    if not show_flights:
        return jsonify(ok=False, err="no flights in show"), 400

    go_epoch = int(time.time()) + 5
    loop_flag = 1 if s.get("loop", True) else 0
    bri = _settings.get("globalBrightness", 255)
    bri_pkt = _hdr(CMD_SET_BRIGHTNESS) + bytes([bri & 0xFF])

    synced_flights = 0
    for flight in show_flights:
        runner = next((r for r in _runners if r["id"] == flight.get("runnerId")), None)
        if not runner:
            continue

        # Get performers for this flight
        perf_ids = set(flight.get("performerIds", []))
        flight_children = [c for c in _children if c.get("status") == 1 and c["id"] in perf_ids]
        if not flight_children:
            continue

        steps = runner.get("steps", [])
        resolved = [_resolve_step(st) for st in steps]
        resolved = [r for r in resolved if r]
        if not resolved:
            continue

        child_offsets = runner.get("childOffsets", [])
        slyled_children = [c for c in flight_children if c.get("type") != "wled"]
        wled_children = [c for c in flight_children if c.get("type") == "wled"]

        # Sync SlyLED children
        for child in slyled_children:
            _send(child["ip"], _hdr(CMD_RUNNER_STOP))
            _send(child["ip"], _hdr(CMD_ACTION_STOP))
        time.sleep(0.05)
        for child in slyled_children:
            _send(child["ip"], bri_pkt)
            for i, step in enumerate(resolved):
                offsets = child_offsets[i] if i < len(child_offsets) else {}
                cid = child["id"]
                delay_ms = offsets.get(cid, offsets.get(str(cid), 0))
                pkt = _load_step_pkt(i, len(resolved), step, child, delay_ms)
                _send(child["ip"], pkt)
                time.sleep(0.03)

        # Send RUNNER_GO to SlyLED children
        pkt_go = _hdr(CMD_RUNNER_GO) + struct.pack("<IB", go_epoch, loop_flag)
        for child in slyled_children:
            _send(child["ip"], pkt_go)

        # Start WLED runner thread
        if wled_children:
            _start_wled_runner(runner, wled_children, go_epoch, bool(loop_flag))

        synced_flights += 1
        log.info("SHOW: flight '%s' synced (%d SlyLED + %d WLED children)",
                 flight.get("name"), len(slyled_children), len(wled_children))

    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeRunner"] = -1
        _settings["activeShow"] = sid
        _settings["runnerStartEpoch"] = go_epoch
        _settings["runnerElapsed"] = 0
        _settings["runnerLoop"] = s.get("loop", True)
        _save("settings", _settings)

    log.info("SHOW '%s' started: %d flights, go_epoch=%d", s.get("name"), synced_flights, go_epoch)
    return jsonify(ok=True, flights=synced_flights)

@app.post("/api/shows/stop")
def api_shows_stop():
    _stop_all_shows()
    return jsonify(ok=True)

def _stop_all_shows():
    """Stop all running shows, runners, and WLED threads."""
    _wled_runner_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off  = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c.get("status") == 1:
            if c.get("type") == "wled":
                wled_stop(c["ip"])
            else:
                _send(c["ip"], pkt_stop)
                _send(c["ip"], pkt_off)
    time.sleep(0.1)
    for c in _children:
        if c.get("status") == 1 and c.get("type") != "wled":
            _send(c["ip"], pkt_stop)
            _send(c["ip"], pkt_off)
    _live_events.clear()
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeRunner"] = -1
        _settings["activeShow"] = -1
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)

#  "  "  Config / Show export-import  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/config/export")
def api_config_export():
    """Bundle children + layout as a portable config file."""
    return jsonify({"type": "slyled-config", "version": 1,
                    "children": _children, "layout": _layout})

@app.post("/api/config/import")
def api_config_import():
    """Merge children by hostname, replace layout with ID remapping."""
    global _nxt_c, _layout
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-config":
        return jsonify(ok=False, err="not a slyled-config file"), 400
    imported_children = data.get("children", [])
    imported_layout = data.get("layout")
    added = updated = 0
    id_map = {}  # old_id -> new_id
    with _lock:
        for c in imported_children:
            old_id = c.get("id", -1)
            ex = next((x for x in _children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                id_map[old_id] = ex["id"]
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = _nxt_c
                id_map[old_id] = _nxt_c
                _nxt_c += 1
                _children.append(c)
                added += 1
        _save("children", _children)
        if imported_layout:
            _layout = imported_layout
            for lc in _layout.get("children", []):
                lc["id"] = id_map.get(lc.get("id"), lc.get("id"))
            _save("layout", _layout)
    return jsonify(ok=True, added=added, updated=updated)

@app.get("/api/show/export")
def api_show_export():
    """Bundle actions + runners + flights + shows as a portable show file."""
    return jsonify({"type": "slyled-show", "version": 1,
                    "actions": _actions, "runners": _runners,
                    "flights": _flights, "shows": _shows})

def _install_show_bundle(bundle):
    """Replace all show data with the given bundle, reassigning IDs.
    Returns a list of orphan detail dicts (empty if none)."""
    global _actions, _runners, _flights, _shows
    global _nxt_a, _nxt_r, _nxt_f, _nxt_s

    _stop_all_shows()

    with _lock:
        # Action ID remap
        a_map = {}
        new_actions = []
        for a in bundle.get("actions", []):
            old_id = a.get("id", -1)
            a = copy.deepcopy(a)
            a["id"] = _nxt_a
            a_map[old_id] = _nxt_a
            _nxt_a += 1
            new_actions.append(a)

        # Runner ID remap + fix actionId refs in steps
        r_map = {}
        new_runners = []
        for r in bundle.get("runners", []):
            old_id = r.get("id", -1)
            r = copy.deepcopy(r)
            r["id"] = _nxt_r
            r_map[old_id] = _nxt_r
            _nxt_r += 1
            for step in r.get("steps", []):
                if "actionId" in step:
                    step["actionId"] = a_map.get(step["actionId"], step["actionId"])
            new_runners.append(r)

        # Flight ID remap + fix runnerId refs
        f_map = {}
        new_flights = []
        current_child_ids = {c["id"] for c in _children}
        orphan_details = []
        for f in bundle.get("flights", []):
            old_id = f.get("id", -1)
            f = copy.deepcopy(f)
            f["id"] = _nxt_f
            f_map[old_id] = _nxt_f
            _nxt_f += 1
            if f.get("runnerId") is not None:
                f["runnerId"] = r_map.get(f["runnerId"], f["runnerId"])
            if f.get("performerIds"):
                missing = [p for p in f["performerIds"] if p not in current_child_ids]
                if missing:
                    orphan_details.append({"flightName": f.get("name"), "missingIds": missing})
            new_flights.append(f)

        # Show ID remap + fix flightIds refs
        new_shows = []
        for s in bundle.get("shows", []):
            s = copy.deepcopy(s)
            s["id"] = _nxt_s
            _nxt_s += 1
            s["flightIds"] = [f_map.get(fid, fid) for fid in s.get("flightIds", [])]
            new_shows.append(s)

        _actions = new_actions
        _runners = new_runners
        _flights = new_flights
        _shows = new_shows
        _save("actions", _actions)
        _save("runners", _runners)
        _save("flights", _flights)
        _save("shows", _shows)

    return orphan_details

@app.post("/api/show/import")
def api_show_import():
    """Import a show bundle, replacing all current show data."""
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-show":
        return jsonify(ok=False, err="not a slyled-show file"), 400
    orphan_details = _install_show_bundle(data)
    resp = {"ok": True, "actions": len(_actions), "runners": len(_runners),
            "flights": len(_flights), "shows": len(_shows)}
    if orphan_details:
        names = ", ".join(d["flightName"] or "(unnamed)" for d in orphan_details)
        resp["warning"] = (f"Flights with missing performers: {names}. "
                           "Re-assign them in the Runtime tab.")
        resp["orphanDetails"] = orphan_details
    return jsonify(resp)

_TYPE_NAMES = {0: "Blackout", 1: "Solid", 2: "Fade", 3: "Breathe",
               4: "Chase", 5: "Rainbow", 6: "Fire", 7: "Comet",
               8: "Twinkle", 9: "Strobe", 10: "Wipe", 11: "Scanner",
               12: "Sparkle", 13: "Gradient"}

#  "  "  Demo show generator  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

# Mood presets   " extensibility point for future smart-show / theme system.
# Each preset defines colors, durations, action types, and type-specific params.
MOOD_PRESETS = {
    "default": {
        "name": "Demo Show",
        "colors": [
            (255, 0, 0), (0, 200, 255), (0, 255, 60), (255, 140, 0),
            (180, 0, 255), (0, 255, 200), (255, 255, 0), (255, 0, 120),
        ],
        "durations": [6, 8, 10, 5, 7, 8, 6, 5],
        "action_types": [1, 2, 3, 4, 5, 6, 7, 8],
    },
    # Future: "calm", "party", "ambient", "holiday", etc.
}

def _demo_action_defaults(atype, color, color2):
    """Return type-specific default params for the demo generator."""
    r, g, b = color
    base = {"r": r, "g": g, "b": b, "scope": "performer"}
    if atype == 2:   # Fade
        base.update({"r2": color2[0], "g2": color2[1], "b2": color2[2], "speedMs": 2000})
    elif atype == 3: # Breathe
        base.update({"periodMs": 3000, "minBri": 20})
    elif atype == 4: # Chase
        base.update({"speedMs": 80, "spacing": 4, "direction": 0})
    elif atype == 5: # Rainbow
        base.update({"speedMs": 40, "paletteId": 0, "direction": 0})
    elif atype == 6: # Fire
        base.update({"speedMs": 15, "cooling": 55, "sparking": 120})
    elif atype == 7: # Comet
        base.update({"speedMs": 25, "tailLen": 12, "direction": 0, "decay": 80})
    elif atype == 8: # Twinkle
        base.update({"spawnMs": 50, "density": 4, "fadeSpeed": 15})
    return base

def _generate_demo_show(mood="default"):
    """Generate a demo show. Works with or without registered performers.
    Actions, runners, and shows are always created. The flight targets
    all current performers (empty list if none registered)."""
    preset = MOOD_PRESETS.get(mood, MOOD_PRESETS["default"])
    colors = preset["colors"]
    durations = preset["durations"]

    # Build actions
    actions = []
    for i, atype in enumerate(preset["action_types"]):
        color = colors[i % len(colors)]
        color2 = colors[(i + 1) % len(colors)]
        a = {"id": i, "name": "Demo %s" % _TYPE_NAMES.get(atype, "Action"),
             "type": atype}
        a.update(_demo_action_defaults(atype, color, color2))
        actions.append(a)

    # Build runner with steps cycling through actions
    steps = []
    for i, a in enumerate(actions):
        steps.append({"actionId": a["id"],
                       "durationS": durations[i % len(durations)]})
    runner = {"id": 0, "name": "Demo Runner", "computed": False, "steps": steps}

    # Build one flight targeting all performers (empty if none registered)
    child_ids = [c["id"] for c in _children]
    flight = {"id": 0, "name": "Demo Flight - All Performers",
              "performerIds": child_ids, "runnerId": 0, "priority": 1}

    # Build show
    show = {"id": 0, "name": preset["name"],
            "flightIds": [0], "loop": True}

    return {"actions": actions, "runners": [runner],
            "flights": [flight], "shows": [show]}

@app.post("/api/show/preset")
def api_show_preset():
    """Install a preset show as a timeline with spatial effects and actions."""
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

    return jsonify(ok=True, name=preset["name"], timelineId=tl["id"],
                   actions=len(action_ids), effects=len(effect_ids))

@app.get("/api/show/presets")
def api_show_presets():
    """List available preset shows."""
    presets = [
        {"id": "rainbow-up",     "name": "Rainbow Up",       "desc": "Moving rainbow from floor to ceiling"},
        {"id": "rainbow-across", "name": "Rainbow Across",   "desc": "Moving rainbow from stage left to right"},
        {"id": "slow-fire",      "name": "Slow Fire",        "desc": "Warm fire effect across all fixtures"},
        {"id": "disco",          "name": "Disco",            "desc": "Random pastel twinkles on all fixtures"},
        {"id": "ocean-wave",     "name": "Ocean Wave",       "desc": "Blue wave sweeping across the stage"},
        {"id": "sunset",         "name": "Sunset Glow",      "desc": "Warm orange breathe with golden sweep"},
        {"id": "police",         "name": "Police Lights",    "desc": "Red strobe with blue flash sweep"},
        {"id": "starfield",      "name": "Starfield",        "desc": "White sparkles on dark background"},
        {"id": "aurora",         "name": "Aurora Borealis",  "desc": "Green curtain with purple shimmer"},
    ]
    return jsonify(presets)

#  "  "  Factory reset  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False, "activeRunner": -1, "activeShow": -1,
    "runnerElapsed": 0, "runnerLoop": True, "logging": False,
}
_DEFAULT_LAYOUT = {"canvasW": 10000, "canvasH": 5000, "children": []}
_DEFAULT_STAGE  = {"w": 10.0, "h": 5.0, "d": 10.0}
_DEFAULT_FIXTURES  = []
_DEFAULT_SURFACES  = []
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

#  "  "  Migration (Phase 8)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/migrate/layout")
def api_migrate_layout():
    """Create fixtures from all registered children (2D -'3D migration)."""
    global _nxt_fix
    created = 0
    existing_child_ids = {f.get("childId") for f in _fixtures}
    with _lock:
        for child in _children:
            if child["id"] in existing_child_ids:
                continue
            f = {
                "id": _nxt_fix,
                "name": child.get("name") or child.get("hostname") or f"Fixture {_nxt_fix}",
                "childId": child["id"],
                "type": "linear",
                "strings": [],
                "aoeRadius": 1000,
            }
            _fixtures.append(f)
            _nxt_fix += 1
            created += 1
        _save("fixtures", _fixtures)
    return jsonify(ok=True, created=created)

@app.post("/api/migrate/runner/<int:rid>")
def api_migrate_runner(rid):
    """Convert a classic runner to a timeline with fixture-local effect clips."""
    global _nxt_sfx, _nxt_tl
    runner = next((r for r in _runners if r["id"] == rid), None)
    if not runner:
        return jsonify(err="Runner not found"), 404

    with _lock:
        # Create spatial effects from each action referenced by runner steps
        fx_map = {}  # action_id  -' spatial_fx_id
        for step in runner.get("steps", []):
            aid = step.get("actionId")
            if aid is None or aid in fx_map:
                continue
            action = next((a for a in _actions if a["id"] == aid), None)
            if not action:
                continue
            fx = {
                "id": _nxt_sfx,
                "name": f"Migrated: {action.get('name', 'Action')}",
                "category": "fixture-local",
                "actionType": action.get("type", 0),
                "r": action.get("r", 0), "g": action.get("g", 0), "b": action.get("b", 0),
                "blend": "replace",
                "fixtureIds": [],
            }
            _spatial_fx.append(fx)
            fx_map[aid] = _nxt_sfx
            _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)

        # Create timeline with one track per fixture, clips from runner steps
        total_dur = sum(s.get("durationS", 0) for s in runner.get("steps", []))
        tracks = []
        for fixture in _fixtures:
            clips = []
            t = 0
            for step in runner.get("steps", []):
                aid = step.get("actionId")
                dur = step.get("durationS", 5)
                if aid in fx_map:
                    clips.append({"effectId": fx_map[aid], "startS": t, "durationS": dur})
                t += dur
            if clips:
                tracks.append({"fixtureId": fixture["id"], "clips": clips})

        tl = {
            "id": _nxt_tl,
            "name": f"Migrated: {runner.get('name', 'Runner')}",
            "durationS": total_dur or 60,
            "tracks": tracks,
            "loop": False,
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)

    return jsonify(ok=True, timelineId=tl["id"], effectsCreated=len(fx_map), tracks=len(tracks))

@app.post("/api/reset")
def api_reset():
    """Clear all data and restore default settings."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    global _children, _settings, _layout, _stage, _runners, _actions, _flights, _shows
    global _fixtures, _surfaces, _spatial_fx, _timelines
    global _wifi, _nxt_c, _nxt_r, _nxt_a, _nxt_f, _nxt_s
    global _nxt_fix, _nxt_sf, _nxt_sfx, _nxt_tl
    _stop_all_shows()
    with _lock:
        _children = []
        _runners  = []
        _actions  = []
        _flights  = []
        _shows    = []
        _wifi     = {"ssid": "", "password": ""}
        _layout   = dict(_DEFAULT_LAYOUT)
        _stage    = dict(_DEFAULT_STAGE)
        _settings = dict(_DEFAULT_SETTINGS)
        _fixtures   = list(_DEFAULT_FIXTURES)
        _surfaces   = list(_DEFAULT_SURFACES)
        _spatial_fx = list(_DEFAULT_SPATIAL_FX)
        _timelines  = list(_DEFAULT_TIMELINES)
        _nxt_c = _nxt_r = _nxt_a = _nxt_f = _nxt_s = 0
        _nxt_fix = _nxt_sf = _nxt_sfx = _nxt_tl = 0
        _save("children", _children)
        _save("runners",  _runners)
        _save("actions",  _actions)
        _save("flights",  _flights)
        _save("shows",    _shows)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("stage",    _stage)
        _save("settings", _settings)
        _save("fixtures",   _fixtures)
        _save("surfaces",   _surfaces)
        _save("spatial_fx", _spatial_fx)
        _save("timelines",  _timelines)
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
    return jsonify(rel)

@app.get("/api/firmware/check")
def api_firmware_check():
    """Compare all children firmware against latest release. Returns per-child update status."""
    if not _wifi.get("ssid") or not _wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required - set them on the Firmware tab before checking for updates"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    latest = rel.get("version", "0.0")
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




