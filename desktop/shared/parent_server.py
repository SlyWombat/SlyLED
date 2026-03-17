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

from flask import Flask, abort, jsonify, request, send_from_directory

# ── Version ───────────────────────────────────────────────────────────────────

VERSION = "4.0"

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
_wifi    = _load("wifi",    {"ssid": "", "password": ""})

MAX_RUNNERS = 4

_nxt_c = max((c["id"] for c in _children), default=-1) + 1
_nxt_r = max((r["id"] for r in _runners),  default=-1) + 1
_nxt_a = max((a["id"] for a in _actions),  default=-1) + 1
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
    # Firmware version (added in v4.0 — 141-byte PONG)
    fw_ver = None
    if len(data) >= 141:
        fw_ver = f"{p[131]}.{p[132]}"
    return {
        "hostname": hn, "name": nm or hn, "desc": ds, "sc": sc,
        "strings": strings, "ip": src_ip,
        "status": 1, "seen": int(time.time()),
        "fwVersion": fw_ver,
    }

def _ping(child, retries=2):
    """Send CMD_PING and update child from PONG response.
    Retries up to `retries` times on timeout before marking offline.
    """
    pkt = _hdr(CMD_PING)
    for _ in range(retries + 1):
        resp = _send_recv(child["ip"], pkt)
        info = _parse_pong(resp, child["ip"])
        if info:
            child.update({k: v for k, v in info.items() if k != "id"})
            return True
    child["status"] = 0
    return False

def _discover_all():
    """Broadcast PING and collect PONGs for ~2 s, keyed by hostname.
    Includes all responders (even known children) for IP-change detection."""
    found = {}
    broadcasts = ["255.255.255.255"] + _local_broadcasts()
    for bind_port in (UDP_PORT, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(0.1)
                s.bind(("", bind_port))
                for bc in broadcasts:
                    try:
                        s.sendto(_hdr(CMD_PING), (bc, UDP_PORT))
                    except Exception:
                        pass
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        data, addr = s.recvfrom(256)
                        info = _parse_pong(data, addr[0])
                        if info and info.get("hostname"):
                            found[info["hostname"]] = info
                    except socket.timeout:
                        pass
            break
        except OSError:
            if bind_port == 0:
                break
            continue
        except Exception:
            break
    return found

def _discover():
    """Broadcast PING and collect PONGs for ~2 s.
    Sends to 255.255.255.255 and all subnet-directed broadcast addresses.
    Binds to UDP_PORT so replies arrive on the firewall-allowed port.
    Excludes IPs already registered as children.
    """
    found = {}
    known_ips = {c["ip"] for c in _children}
    broadcasts = ["255.255.255.255"] + _local_broadcasts()
    for bind_port in (UDP_PORT, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(0.1)
                s.bind(("", bind_port))
                for bc in broadcasts:
                    try:
                        s.sendto(_hdr(CMD_PING), (bc, UDP_PORT))
                    except Exception:
                        pass
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        data, addr = s.recvfrom(256)
                        src_ip = addr[0]
                        if src_ip not in found and src_ip not in known_ips:
                            info = _parse_pong(data, src_ip)
                            if info:
                                found[src_ip] = info
                    except socket.timeout:
                        pass
            break   # socket opened successfully
        except OSError:
            if bind_port == 0:
                break
            continue   # port 4210 busy — retry with ephemeral
        except Exception:
            break
    return list(found.values())

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
    """Extract generic param fields from an action dict."""
    t = act.get("type", 0)
    r, g, b = act.get("r", 0), act.get("g", 0), act.get("b", 0)
    p16a = act.get("speedMs", act.get("periodMs", act.get("spawnMs", 500)))
    p8a = act.get("p8a", act.get("r2", act.get("minBri", act.get("spacing",
           act.get("paletteId", act.get("cooling", act.get("tailLen",
           act.get("density", 0))))))))
    p8b = act.get("p8b", act.get("g2", act.get("sparking", 0)))
    p8c = act.get("p8c", act.get("b2", act.get("direction", 0)))
    p8d = act.get("p8d", act.get("decay", act.get("fadeSpeed", 0)))
    return t, r, g, b, p16a, p8a, p8b, p8c, p8d

def _action_pkt(act, child):
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(act)
    ls, le = _child_led_ranges(child)
    return _hdr(CMD_ACTION) + struct.pack("<BBBBHBBBB", t, r, g, b, p16a, p8a, p8b, p8c, p8d) + ls + le

def _load_step_pkt(idx, total, step, child, delay_ms=0):
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(step)
    dur = step.get("durationS", 5)
    ls, le = _child_led_ranges(child)
    pl = struct.pack("<BBBBBBHBBBBHH", idx, total, t, r, g, b, p16a, p8a, p8b, p8c, p8d, dur, delay_ms)
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
    """Background thread: ping all children periodically to keep status fresh.
    First sweep at startup (with retry to catch children still booting),
    then every 30 seconds."""
    global _startup_check_done
    # Startup sweep: try twice with a gap to catch children still in boot animation
    for attempt in range(2):
        for c in list(_children):
            _ping(c, retries=1)
        with _lock:
            _save("children", _children)
        if attempt == 0:
            _startup_check_done = True
            time.sleep(5)   # wait for slow-booting children (e.g. 3s boot animation)
    # Periodic sweep every 30 seconds
    while True:
        time.sleep(30)
        for c in list(_children):
            _ping(c, retries=1)
        with _lock:
            _save("children", _children)

def start_background_tasks():
    """Call once after import to kick off periodic ping thread."""
    global _startup_check_done
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
    child = {"ip": ip, "hostname": ip, "name": ip,
             "desc": "", "sc": 0, "strings": [], "status": 0, "seen": 0}
    with _lock:
        child["id"] = _nxt_c
        _nxt_c += 1
        _children.append(child)
        _save("children", _children)
    _ping(child)          # ping outside lock so DELETE/other requests aren't blocked
    with _lock:
        _save("children", _children)
    return jsonify(ok=True, id=child["id"])

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

@app.post("/api/children/refresh-all")
def api_children_refresh_all():
    """Ping all children to update their status. Also tries to find children
    that may have changed IP by doing a broadcast discovery."""
    # First try direct ping of known IPs
    for c in list(_children):
        _ping(c, retries=1)
    # For any offline children, try to match by hostname from a broadcast discover
    offline = [c for c in _children if c.get("status") != 1]
    if offline:
        found = _discover_all()   # discover including known IPs
        for c in offline:
            match = found.get(c.get("hostname"))
            if match and match["ip"] != c["ip"]:
                # Child moved to a new IP
                c["ip"] = match["ip"]
                _ping(c, retries=1)
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
        for k in ("name", "units", "canvasW", "canvasH", "darkMode", "runnerLoop", "globalBrightness"):
            if k in body:
                _settings[k] = body[k]
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
        _save("settings", _settings)
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
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct")  # legacy compat

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
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off  = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c["status"] == 1:
            _send(c["ip"], pkt_stop)
            _send(c["ip"], pkt_off)   # belt-and-suspenders: also stop immediate action
    # Retry once after brief delay for reliability
    time.sleep(0.1)
    for c in _children:
        if c["status"] == 1:
            _send(c["ip"], pkt_stop)
            _send(c["ip"], pkt_off)
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeRunner"] = -1
        _settings["runnerStartEpoch"] = 0
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)
    return jsonify(ok=True)

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
    Supports both new (actionId) and legacy (inline type/r/g/b) formats."""
    if "actionId" in step:
        act = next((a for a in _actions if a["id"] == step["actionId"]), None)
        if not act:
            return None
        # Merge all action fields into the step (step's area/duration preserved)
        merged = dict(step)
        for k in ("type", "r", "g", "b", "speedMs", "periodMs", "spawnMs",
                  "p8a", "p8b", "p8c", "p8d", "r2", "g2", "b2", "minBri",
                  "spacing", "paletteId", "cooling", "sparking", "direction",
                  "tailLen", "density", "decay", "fadeSpeed",
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct"):
            if k in act:
                merged[k] = act[k]
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
    # Send global brightness to all online children before steps
    bri = _settings.get("globalBrightness", 255)
    bri_pkt = _hdr(CMD_SET_BRIGHTNESS) + bytes([bri & 0xFF])
    sent = 0
    for child in online:
        _send(child["ip"], bri_pkt)
        for i, step in enumerate(resolved):
            delay_ms = child_offsets[i].get(child["id"], 0) if i < len(child_offsets) else 0
            pkt = _load_step_pkt(i, len(resolved), step, child, delay_ms)
            _send(child["ip"], pkt)
            sent += 1
            time.sleep(0.03)   # 30ms gap between packets for reliable delivery
    return jsonify(ok=True, sent=sent, online=len(online))

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
    for c in online:
        _send(c["ip"], pkt)
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeRunner"]  = rid
        _settings["runnerStartEpoch"] = go_epoch
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)
    return jsonify(ok=True)

# ── Factory reset ─────────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1, "runnerRunning": False, "activeRunner": -1, "runnerElapsed": 0,
    "runnerLoop": True,
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
    """Clear all children, runners, actions, layout, wifi and restore default settings."""
    global _children, _settings, _layout, _runners, _actions, _wifi, _nxt_c, _nxt_r, _nxt_a
    with _lock:
        _children = []
        _runners  = []
        _actions  = []
        _wifi     = {"ssid": "", "password": ""}
        _layout   = dict(_DEFAULT_LAYOUT)
        _settings = dict(_DEFAULT_SETTINGS)
        _nxt_c = 0
        _nxt_r = 0
        _nxt_a = 0
        _save("children", _children)
        _save("runners",  _runners)
        _save("actions",  _actions)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("settings", _settings)
    return jsonify(ok=True)

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
