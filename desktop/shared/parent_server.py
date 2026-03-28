#!/usr/bin/env python3
"""
SlyLED Parent Server — Windows / Mac desktop parent application.

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
        log.info("Logging started → %s", fh.baseFilename)

# ── Version ───────────────────────────────────────────────────────────────────

VERSION = "5.3.6"

# ── UDP protocol ──────────────────────────────────────────────────────────────

UDP_MAGIC   = 0x534C
UDP_VERSION = 3
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

# ── Paths ─────────────────────────────────────────────────────────────────────

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

# ── Persistence ───────────────────────────────────────────────────────────────

def _load(name, default):
    p = DATA / f"{name}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:
        return default

def _save(name, obj):
    (DATA / f"{name}.json").write_text(json.dumps(obj, indent=2))

# ── In-memory state ───────────────────────────────────────────────────────────

_children = _load("children", [])
_settings = _load("settings", {
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False, "activeRunner": -1, "runnerElapsed": 0,
    "runnerLoop": True,
})
_layout  = _load("layout",  {"canvasW": 10000, "canvasH": 5000, "children": []})
_runners = _load("runners", [])
_actions = _load("actions", [])
_flights = _load("flights", [])
_shows   = _load("shows",   [])
_wifi    = _load("wifi",    {"ssid": "", "password": ""})

MAX_RUNNERS = 4

# Live action events pushed by children (ip → {actionType, stepIndex, totalSteps, event, ts})
_live_events = {}

# Recent PONGs seen by UDP listener (ip → parsed pong info) — used by discover
_recent_pongs = {}

# Apply logging from saved settings on startup
_apply_logging(_settings.get("logging", False))

_nxt_c = max((c["id"] for c in _children), default=-1) + 1
_nxt_r = max((r["id"] for r in _runners),  default=-1) + 1
_nxt_a = max((a["id"] for a in _actions),  default=-1) + 1
_nxt_f = max((f["id"] for f in _flights),  default=-1) + 1
_nxt_s = max((s["id"] for s in _shows),    default=-1) + 1
_lock  = threading.Lock()

# ── UDP helpers ───────────────────────────────────────────────────────────────

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
            continue          # port 4210 busy — retry with ephemeral
        except Exception:
            return None
    return None

def _parse_pong(data, src_ip):
    # PONG v4: 8-byte header + 133-byte PongPayload = 141 bytes (v3: 139 bytes)
    # PongPayload: hostname[10]+altName[16]+desc[32]+stringCount(1)+PongString[8]×9+fwMajor(1)+fwMinor(1)
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
            board_map = {"esp32": "ESP32", "d1mini": "D1 Mini", "giga-child": "Giga"}
            child["boardType"] = board_map.get(board, board)
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
    The UDP listener daemon handles incoming PONGs → _recent_pongs."""
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
    """Broadcast PING, wait for listener to collect PONGs, return unknown performers."""
    known_ips = {c["ip"] for c in _children}
    known_hosts = {c.get("hostname") for c in _children}
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    return [info for ip, info in _recent_pongs.items()
            if ip not in known_ips and info.get("hostname") not in known_hosts]

def _child_led_ranges(child):
    """Build ledStart[8] / ledEnd[8] arrays from child's string config.
    For each configured string: start=0, end=ledCount-1.
    For unconfigured strings: 0xFF (not included)."""
    ls = [0xFF] * 8
    le = [0xFF] * 8
    sc = child.get("sc", 0)
    strings = child.get("strings", [])
    for j in range(min(sc, len(strings), 8)):
        leds = strings[j].get("leds", 0)
        if leds > 0:
            ls[j] = 0
            le[j] = min(leds - 1, 254)   # clamp to uint8, 0xFF=unused sentinel
    return bytes(ls), bytes(le)

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
    ls, le = _child_led_ranges(child)
    pl = struct.pack("<BBBBBBHBBBBHH", idx, total, t, r, g, b, p16a, p8a, p8b, p8c, p8d, dur, int(delay_ms))
    return _hdr(CMD_LOAD_STEP) + pl + ls + le

# ── Flask application ─────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/favicon.ico")
def favicon():
    abort(404)

@app.get("/status")
def status():
    return jsonify(role="parent", hostname=socket.gethostname(), version=VERSION)

# ── Children ──────────────────────────────────────────────────────────────────

CHILD_STALE_S = 120   # mark offline if not seen for 2 minutes
_startup_check_done = False

def _periodic_ping():
    """Background thread: broadcast PING periodically.  The UDP listener
    daemon picks up PONGs and updates child records — no per-child
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
        if magic != UDP_MAGIC or ver != UDP_VERSION:
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
    return jsonify(_discover())

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
    """Broadcast ping all children. The UDP listener updates their status
    from PONGs. Also detects IP changes for offline children."""
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.5)
    # Record which children responded
    responded_ips = set(_recent_pongs.keys())
    responded_hostnames = {info.get("hostname") for info in _recent_pongs.values()}
    for c in _children:
        if c.get("type") == "wled":
            # WLED: probe via HTTP
            wled_info = wled_probe(c["ip"], timeout=2.0)
            if wled_info:
                c["status"] = 1
                c["seen"] = int(time.time())
            else:
                c["status"] = 0
        elif c["ip"] in responded_ips or c.get("hostname") in responded_hostnames:
            # SlyLED child responded to ping
            for ip, info in _recent_pongs.items():
                if info.get("hostname") == c.get("hostname"):
                    if ip != c["ip"]:
                        c["ip"] = ip  # IP change detected
                    c.update({k: v for k, v in info.items() if k != "id"})
                    break
        else:
            # No response — mark offline
            c["status"] = 0
    with _lock:
        _save("children", _children)
    online = sum(1 for c in _children if c.get("status") == 1)
    return jsonify(ok=True, total=len(_children), online=online)

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

# ── WLED device API ───────────────────────────────────────────────────────────

_wled_cache = {}   # child_id → {"effects": [...], "palettes": [...], "ts": epoch}
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

# ── Layout ────────────────────────────────────────────────────────────────────

@app.get("/api/layout")
def api_layout_get():
    layout = dict(_layout)
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    layout["children"] = [
        {**c,
         "x": pos_map.get(c["id"], {}).get("x", 0),
         "y": pos_map.get(c["id"], {}).get("y", 0),
         "positioned": c["id"] in pos_map}
        for c in _children
    ]
    return jsonify(layout)

@app.post("/api/layout")
def api_layout_save():
    body = request.get_json(silent=True) or {}
    _layout["children"] = body.get("children", [])
    _save("layout", _layout)
    return jsonify(ok=True)

# ── Settings ──────────────────────────────────────────────────────────────────

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
    # Toggle file logging if changed
    if "logging" in body:
        _apply_logging(body["logging"])
    return jsonify(ok=True)

# ── Action ────────────────────────────────────────────────────────────────────

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

# ── Actions library ───────────────────────────────────────────────────────────

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

# ── Runners ───────────────────────────────────────────────────────────────────

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
                # JSON round-trip may stringify int keys — try both
                cid = child["id"]
                delay_ms = offsets.get(cid, offsets.get(str(cid), 0))
                pkt = _load_step_pkt(i, len(resolved), step, child, delay_ms)
                _send(child["ip"], pkt)
                sent += 1
                log.debug("  step %d/%d type=%d delay=%dms → %s",
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
    go_epoch = int(time.time()) + 5      # 5 s from now — time for UDP to reach all children
    # CMD_RUNNER_GO: 4-byte startEpoch + 1-byte loop flag as PAYLOAD
    loop_flag = 1 if _settings.get("runnerLoop", True) else 0
    pkt = _hdr(CMD_RUNNER_GO) + struct.pack("<IB", go_epoch, loop_flag)
    online = [c for c in _children if c["status"] == 1]
    slyled_children = [c for c in online if c.get("type") != "wled"]
    wled_children = [c for c in online if c.get("type") == "wled"]
    for c in slyled_children:
        log.info("START: RUNNER_GO epoch=%d loop=%d → %s (%s)", go_epoch, loop_flag, c["ip"], c.get("hostname"))
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

# ── Flights ───────────────────────────────────────────────────────────────────

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

# ── Shows ─────────────────────────────────────────────────────────────────────

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
    """Start a show — syncs and starts all flights simultaneously."""
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
        _settings["runnerStartEpoch"] = 0
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)

# ── Config / Show export-import ───────────────────────────────────────────────

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

# ── Demo show generator ─────────────────────────────────────────────────────

# Mood presets — extensibility point for future smart-show / theme system.
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
    flight = {"id": 0, "name": "Demo Flight — All Performers",
              "performerIds": child_ids, "runnerId": 0, "priority": 1}

    # Build show
    show = {"id": 0, "name": preset["name"],
            "flightIds": [0], "loop": True}

    return {"actions": actions, "runners": [runner],
            "flights": [flight], "shows": [show]}

@app.post("/api/show/demo")
def api_show_demo():
    """Generate and install a demo show from current children/layout."""
    body = request.get_json(silent=True) or {}
    mood = body.get("mood", "default")
    bundle = _generate_demo_show(mood)
    _install_show_bundle(bundle)
    return jsonify(ok=True, actions=len(bundle["actions"]),
                   runners=len(bundle["runners"]),
                   flights=len(bundle["flights"]),
                   shows=len(bundle["shows"]))

# ── Factory reset ─────────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False, "activeRunner": -1, "activeShow": -1,
    "runnerElapsed": 0, "runnerLoop": True, "logging": False,
}
_DEFAULT_LAYOUT = {"canvasW": 10000, "canvasH": 5000, "children": []}

# ── WiFi credentials ─────────────────────────────────────────────────────────

import base64, hashlib

def _wifi_key():
    """Derive an encryption key from machine identity (not portable, but protects at rest)."""
    seed = (socket.gethostname() + "-slyled-wifi").encode()
    return hashlib.sha256(seed).digest()

def _encrypt_pw(plain):
    if not plain:
        return ""
    key = _wifi_key()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(plain.encode("utf-8")))
    return base64.b64encode(out).decode("ascii")

def _decrypt_pw(enc):
    if not enc:
        return ""
    try:
        key = _wifi_key()
        raw = base64.b64decode(enc)
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return enc   # fallback: return as-is if decryption fails (old unencrypted data)

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

# ── Firmware management ──────────────────────────────────────────────────────

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
    """Fast port list — no serial queries. Use /api/firmware/query for per-port info."""
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
    asset_map = {"esp32": "esp32-firmware-merged.bin", "d1mini": "d1mini-firmware.bin"}
    target_map = {"esp32": "esp32/main.ino.merged.bin", "d1mini": "d1mini/main.ino.bin"}
    asset_name = asset_map[board]
    download_url = None
    for a in rel.get("assets", []):
        if a["name"] == asset_name:
            download_url = a["url"]
            break
    if not download_url:
        return jsonify(ok=False, err=f"No {asset_name} in release"), 404
    # Download to firmware directory
    import urllib.request as _ur
    try:
        log.info("Downloading %s from %s", asset_name, download_url)
        req = _ur.Request(download_url, headers={"User-Agent": "SlyLED-Parent"})
        resp = _ur.urlopen(req, timeout=60)
        data = resp.read()
        dest = _FW_DIR / target_map[board]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        # Update local registry version
        reg_path = _FW_DIR / "registry.json"
        if reg_path.exists():
            reg = json.loads(reg_path.read_text())
            for fw in reg.get("firmware", []):
                if fw.get("board") == board and "child" in fw.get("id", ""):
                    fw["version"] = rel["version"]
            reg_path.write_text(json.dumps(reg, indent=2))
        log.info("Downloaded %s (%d bytes) → %s", asset_name, len(data), dest)
        return jsonify(ok=True, version=rel["version"], size=len(data))
    except Exception as e:
        log.error("Download failed: %s", e)
        return jsonify(ok=False, err=str(e)), 502

@app.get("/api/firmware/binary/<board>")
def api_fw_binary(board):
    """Serve a firmware binary for OTA — child downloads from parent over plain HTTP."""
    file_map = {"esp32": "esp32/main.ino.merged.bin", "d1mini": "d1mini/main.ino.bin"}
    rel_path = file_map.get(board)
    if not rel_path:
        return jsonify(ok=False, err=f"unknown board: {board}"), 404
    bin_path = _FW_DIR / rel_path
    if not bin_path.exists():
        # Try downloading from GitHub first
        rel = _fetch_github_release()
        if rel:
            asset_name = {"esp32": "esp32-firmware-merged.bin", "d1mini": "d1mini-firmware.bin"}.get(board)
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
                    wifi_ssid=_wifi.get("ssid"), wifi_pass=_wifi.get("password"))
    threading.Thread(target=_do_flash, daemon=True).start()
    return jsonify(ok=True, message="Flashing started")

@app.get("/api/firmware/flash/status")
def api_fw_flash_status():
    if not _fw_available:
        return jsonify(running=False, progress=0, message="not available")
    return jsonify(get_flash_status())

@app.post("/api/reset")
def api_reset():
    """Clear all data and restore default settings."""
    global _children, _settings, _layout, _runners, _actions, _flights, _shows
    global _wifi, _nxt_c, _nxt_r, _nxt_a, _nxt_f, _nxt_s
    _stop_all_shows()
    with _lock:
        _children = []
        _runners  = []
        _actions  = []
        _flights  = []
        _shows    = []
        _wifi     = {"ssid": "", "password": ""}
        _layout   = dict(_DEFAULT_LAYOUT)
        _settings = dict(_DEFAULT_SETTINGS)
        _nxt_c = _nxt_r = _nxt_a = _nxt_f = _nxt_s = 0
        _save("children", _children)
        _save("runners",  _runners)
        _save("actions",  _actions)
        _save("flights",  _flights)
        _save("shows",    _shows)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("settings", _settings)
    return jsonify(ok=True)

# ── OTA firmware update ───────────────────────────────────────────────────────

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
        asset_map = {"esp32": "esp32-firmware-merged.bin", "d1mini": "d1mini-firmware.bin"}
        asset_name = asset_map.get(board)
        download_url = ""
        if asset_name:
            for a in rel.get("assets", []):
                if a["name"] == asset_name:
                    download_url = a["url"]
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
        return jsonify(ok=False, err="WiFi credentials not configured — set them on the Firmware tab first"), 400

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
    asset_map = {"esp32": "esp32-firmware-merged.bin", "d1mini": "d1mini-firmware.bin"}
    asset_name = asset_map.get(board)
    download_url = ""
    for a in rel.get("assets", []):
        if a["name"] == asset_name:
            download_url = a["url"]
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

    # Send OTA command — use parent as proxy (child can't do HTTPS to GitHub)
    ip = child["ip"]
    # Determine parent's LAN IP for the proxy URL
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        parent_ip = s.getsockname()[0]
        s.close()
    except Exception:
        parent_ip = "127.0.0.1"
    proxy_url = f"http://{parent_ip}:8080/api/firmware/binary/{board}"
    log.info("OTA: triggering update on %s (%s) to v%s via proxy %s", ip, child.get("hostname"), latest, proxy_url)
    try:
        import urllib.request as _ur
        body = json.dumps({"url": proxy_url, "sha256": "", "major": new_major, "minor": new_minor, "patch": new_patch}).encode()
        req = _ur.Request(f"http://{ip}/ota", data=body, method="POST",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception as e:
        log.warning("OTA trigger to %s failed: %s", ip, e)
        # Child may have already started updating and dropped the connection — that's OK
        pass

    return jsonify(ok=True, version=latest, board=board)

# ── QR code for mobile app ────────────────────────────────────────────────────

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

# ── CORS ─────────────────────────────────────────────────────────────────────

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

# ── Shutdown ──────────────────────────────────────────────────────────────────

@app.post("/api/shutdown")
def api_shutdown():
    """Terminate the parent process after sending the response."""
    def _kill():
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_kill, daemon=True).start()
    return jsonify(ok=True)

# ── SPA fallback — must be last ───────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa_fallback(path):
    if path.startswith("api/") or path in ("status", "favicon.ico"):
        abort(404)
    resp = send_from_directory(str(SPA), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

# ── Entry point ───────────────────────────────────────────────────────────────

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
    print(f"  UI   →  http://localhost:{args.port}")
    print(f"  Data →  {DATA}")
    app.run(host=args.host, port=args.port, threaded=True)
