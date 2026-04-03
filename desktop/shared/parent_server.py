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

from wled_bridge import (wled_probe, wled_stop,
                         wled_get_effects, wled_get_palettes, wled_get_segments)
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

VERSION = "8.2.1"

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
    "darkMode": 1, "runnerRunning": False, "runnerElapsed": 0,
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
    if _f.get("fixtureType") == "dmx" and "aimPoint" not in _f:
        _f["aimPoint"] = [0, -1000, 0]
        _fix_patched = True
if _fix_patched:
    _save("fixtures", _fixtures)
del _fix_patched

_surfaces   = _load("surfaces",   [])
_spatial_fx = _load("spatial_fx", [])
_timelines  = _load("timelines",  [])
_actions = _load("actions", [])
_wifi    = _load("wifi",    {"ssid": "", "password": ""})

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
    Includes LED performers and DMX bridges — probes /status for board type."""
    known_ips = {c["ip"] for c in _children}
    known_hosts = {c.get("hostname") for c in _children}
    # Also exclude IPs that already have a DMX fixture pointing at them
    known_dmx_ips = set()
    for f in _fixtures:
        if f.get("fixtureType") == "dmx":
            # DMX fixtures don't have IPs directly, but check children
            pass
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    results = []
    for ip, info in _recent_pongs.items():
        if ip in known_ips or info.get("hostname") in known_hosts:
            continue
        # Probe /status to detect board type
        try:
            import urllib.request as _ur
            resp = _ur.urlopen(f"http://{ip}/status", timeout=2)
            data = json.loads(resp.read().decode("utf-8"))
            info["boardType"] = data.get("boardType", "slyled")
        except Exception:
            info["boardType"] = "slyled"
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
            f["aimPoint"] = body.get("aimPoint", [0, -1000, 0])
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
    if "fixtureType" in body and body["fixtureType"] not in ("led", "dmx"):
        return jsonify(err="Invalid fixtureType - must be 'led' or 'dmx'"), 400
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
    for k in ("name", "type", "fixtureType", "childId", "childIds", "strings",
              "rotation", "aoeRadius", "meshFile",
              "dmxUniverse", "dmxStartAddr", "dmxChannelCount", "dmxProfileId", "aimPoint"):
        if k in body:
            f[k] = body[k]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

@app.put("/api/fixtures/<int:fid>/aim")
def api_fixture_set_aim(fid):
    """Set aim point for a DMX fixture."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    body = request.get_json(silent=True) or {}
    ap = body.get("aimPoint")
    if not isinstance(ap, list) or len(ap) != 3:
        return jsonify(err="aimPoint must be [x,y,z]"), 400
    try:
        f["aimPoint"] = [float(v) for v in ap]
    except (TypeError, ValueError):
        return jsonify(err="aimPoint values must be numbers"), 400
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

@app.delete("/api/fixtures/<int:fid>")
def api_fixture_delete(fid):
    global _fixtures
    if not any(f["id"] == fid for f in _fixtures):
        return jsonify(ok=False, err="fixture not found"), 404
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
                      "capabilities": ch.get("capabilities", [])}
                    for ch in profile.get("channels", [])]
    else:
        channels = [{"offset": i, "name": f"Ch {i+1}", "type": "dimmer",
                      "capabilities": [{"range": [0, 255], "type": "Intensity", "label": f"Ch {i+1} 0-100%"}]}
                    for i in range(count)]
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
        log.info("  fixture %d '%s' type=%s strings=%d leds=%d aim=%s pos=(%s,%s)",
                 ef.get("id"), ef.get("name"), ft, len(strings), leds,
                 ef.get("aimPoint"), ef.get("x", "?"), ef.get("y", "?"))
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
        # Evaluate each DMX fixture
        for fx in dmx_fixtures:
            r, g, b = 0, 0, 0
            pan, tilt, dimmer, strobe, gobo = None, None, None, None, None
            for seg in fx["segs"]:
                ss = seg.get("startS", 0)
                sd = seg.get("durationS", 1)
                if ss <= elapsed < ss + sd:
                    p = seg.get("params", {})
                    r, g, b = p.get("r", 0), p.get("g", 0), p.get("b", 0)
                    pan = p.get("pan")
                    tilt = p.get("tilt")
                    dimmer = p.get("dimmer")
                    strobe = p.get("strobe")
                    gobo = p.get("gobo")
                    break
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
            # Strobe
            if strobe is not None and fx["ch_map"] and "strobe" in fx["ch_map"]:
                uni_buf.set_fixture_channels(fx["addr"], {"strobe": strobe}, profile)
            # Gobo
            if gobo is not None and fx["ch_map"] and "gobo" in fx["ch_map"]:
                uni_buf.set_fixture_channels(fx["addr"], {"gobo": gobo}, profile)
        frame_count += 1
        if frame_count == 1:
            log.info("DMX playback: first frame sent at elapsed=%.1fs", elapsed)
    log.info("DMX playback: stopped after %d frames", frame_count)
    # Blackout DMX fixtures on stop, then remove universes to stop sending
    for fx in dmx_fixtures:
        profile = {"channel_map": fx["ch_map"]} if fx["ch_map"] else None
        engine.set_fixture_rgb(fx["uni"], fx["addr"], 0, 0, 0, profile)
        if fx["ch_map"] and "dimmer" in fx["ch_map"]:
            engine.get_universe(fx["uni"]).set_fixture_dimmer(fx["addr"], 0, profile)

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

#  "  "  Config export-import  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/config/export")
def api_config_export():
    """Bundle children + fixtures + layout as a portable config file."""
    return jsonify({"type": "slyled-config", "version": 2,
                    "children": _children, "fixtures": _fixtures, "layout": _layout})

@app.post("/api/config/import")
def api_config_import():
    """Merge children by hostname, auto-create fixtures, remap layout IDs."""
    global _nxt_c, _nxt_fix, _layout
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-config":
        return jsonify(ok=False, err="not a slyled-config file"), 400
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
            # Create LED fixture if child has strings
            sc = c.get("sc", 0)
            strings = c.get("strings", [])[:sc]
            if not strings or not any(s.get("leds", 0) > 0 for s in strings):
                # DMX bridge — create as DMX fixture placeholder
                if c.get("type") == "dmx":
                    continue  # bridges don't need auto-fixtures
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
        # ── Moving-head-aware presets ──────────────────────────────────
        # These use spatial effects with motion paths. LED fixtures get
        # color washes; DMX moving heads also track the effect center
        # with pan/tilt, creating beam sweeps across the stage.
        "spotlight-sweep": {
            "name": "Spotlight Sweep",
            "durationS": 20,
            "effects": [
                {"name": "Sweep Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 240, "b": 200, "size": {"radius": 1500},
                 "motion": {"startPos": [0, 0, 5000], "endPos": [10000, 0, 5000],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Return Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 180, "b": 255, "size": {"radius": 1500},
                 "motion": {"startPos": [10000, 0, 5000], "endPos": [0, 0, 5000],
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
                 "r": 255, "g": 160, "b": 40, "size": {"radius": 2000},
                 "motion": {"startPos": [8000, 0, 3000], "endPos": [2000, 0, 7000],
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
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 1800},
                 "motion": {"startPos": [1000, 0, 2000], "endPos": [9000, 0, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Cyan Path B", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 1800},
                 "motion": {"startPos": [9000, 0, 2000], "endPos": [1000, 0, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return A", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 1800},
                 "motion": {"startPos": [9000, 0, 8000], "endPos": [1000, 0, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return B", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 1800},
                 "motion": {"startPos": [1000, 0, 8000], "endPos": [9000, 0, 2000],
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
                 "r": 255, "g": 0, "b": 50, "size": {"radius": 1200},
                 "motion": {"startPos": [1000, 0, 2000], "endPos": [9000, 0, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Blue Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 50, "g": 0, "b": 255, "size": {"radius": 1200},
                 "motion": {"startPos": [9000, 0, 2000], "endPos": [1000, 0, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Green Flash", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 255, "b": 80, "size": {"radius": 1500},
                 "motion": {"startPos": [5000, 5000, 5000], "endPos": [5000, 0, 5000],
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
        {"id": "spotlight-sweep","name": "Spotlight Sweep", "desc": "Warm orb sweeps stage left-right — moving heads track it"},
        {"id": "concert-wash",  "name": "Concert Wash",    "desc": "Magenta flood + amber spot — moving heads follow the amber"},
        {"id": "figure-eight",  "name": "Figure Eight",    "desc": "Crossing cyan/gold orbs — moving heads trace figure-eight paths"},
        {"id": "thunderstorm",  "name": "Thunderstorm",    "desc": "Lightning bolts on deep blue — moving heads chase the strikes"},
        {"id": "dance-floor",   "name": "Dance Floor",     "desc": "Fast orbiting spots + chase pulse — moving heads rapid-track"},
    ]
    return jsonify(presets)

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

#  "  "  Factory reset  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False,
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

@app.post("/api/reset")
def api_reset():
    """Clear all data and restore default settings."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    global _children, _settings, _layout, _stage, _actions
    global _fixtures, _surfaces, _spatial_fx, _timelines
    global _wifi, _nxt_c, _nxt_a, _dmx_settings, _bake_result
    global _nxt_fix, _nxt_sf, _nxt_sfx, _nxt_tl
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
        _layout   = dict(_DEFAULT_LAYOUT)
        _stage    = dict(_DEFAULT_STAGE)
        _settings = dict(_DEFAULT_SETTINGS)
        _fixtures   = list(_DEFAULT_FIXTURES)
        _surfaces   = list(_DEFAULT_SURFACES)
        _spatial_fx = list(_DEFAULT_SPATIAL_FX)
        _timelines  = list(_DEFAULT_TIMELINES)
        _dmx_settings = {"protocol": "artnet", "frameRate": 40, "bindIp": "0.0.0.0",
                         "universeRoutes": [], "sacnPriority": 100, "sacnSourceName": "SlyLED"}
        _nxt_c = _nxt_a = 0
        _nxt_fix = _nxt_sf = _nxt_sfx = _nxt_tl = 0
        _save("children", _children)
        _save("actions",  _actions)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("stage",    _stage)
        _save("settings", _settings)
        _save("fixtures",   _fixtures)
        _save("surfaces",   _surfaces)
        _save("spatial_fx", _spatial_fx)
        _save("timelines",  _timelines)
        _save("dmx_settings", _dmx_settings)
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










