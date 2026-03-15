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
import socket
import struct
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

# ── Version ───────────────────────────────────────────────────────────────────

VERSION = "3.6"

# ── UDP protocol ──────────────────────────────────────────────────────────────

UDP_MAGIC   = 0x534C
UDP_VERSION = 2
UDP_PORT    = 4210

CMD_PING        = 0x01
CMD_PONG        = 0x02
CMD_ACTION      = 0x10
CMD_ACTION_STOP = 0x11
CMD_LOAD_STEP   = 0x20
CMD_LOAD_ACK    = 0x21
CMD_RUNNER_GO   = 0x30
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
})
_layout  = _load("layout",  {"canvasW": 10000, "canvasH": 5000, "children": []})
_runners = _load("runners", [])

MAX_RUNNERS = 4

_nxt_c = max((c["id"] for c in _children), default=-1) + 1
_nxt_r = max((r["id"] for r in _runners),  default=-1) + 1
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

def _send_recv(ip, pkt, timeout=1.5, maxb=256):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(pkt, (ip, UDP_PORT))
            return s.recvfrom(maxb)[0]
    except Exception:
        return None

def _parse_pong(data, src_ip):
    # Full PONG = 8-byte header + 131-byte PongPayload = 139 bytes
    # PongPayload: hostname[10]+altName[16]+desc[32]+stringCount(1)+PongString[8]×9
    if not data or len(data) < 139:
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
                         "cdir": cd, "cmm": cm, "sdir": sd})
        off += 9
    return {
        "hostname": hn, "name": nm or hn, "desc": ds, "sc": sc,
        "strings": strings, "ip": src_ip,
        "status": 1, "seen": int(time.time()),
    }

def _ping(child):
    resp = _send_recv(child["ip"], _hdr(CMD_PING))
    info = _parse_pong(resp, child["ip"])
    if info:
        child.update({k: v for k, v in info.items() if k != "id"})
        return True
    child["status"] = 0
    return False

def _action_pkt(act, child):
    t   = act.get("type", 0)
    r, g, b = act.get("r", 0), act.get("g", 0), act.get("b", 0)
    on, off  = act.get("onMs", 500), act.get("offMs", 500)
    wd, ws   = act.get("wipeDir", 0), act.get("wipeSpeedPct", 50)
    # ledStart/ledEnd must each be 8 bytes (MAX_STR_PER_CHILD=8); 0xFF = unused slot
    ls = bytes([0,    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    le = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    return _hdr(CMD_ACTION) + struct.pack("<BBBBHHBB", t, r, g, b, on, off, wd, ws) + ls + le

def _load_step_pkt(idx, total, step, child):
    t    = step.get("type", 1)
    r, g, b = step.get("r", 255), step.get("g", 0), step.get("b", 0)
    on, off  = step.get("onMs", 500), step.get("offMs", 500)
    wd, ws   = step.get("wdir", 0), step.get("wspd", 50)
    dur      = step.get("durationS", 5)
    ls = bytes([0,    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    le = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    pl = struct.pack("<BBBBBBHHBBH", idx, total, t, r, g, b, on, off, wd, ws, dur)
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

@app.get("/api/children")
def api_children():
    return jsonify(_children)

@app.get("/api/children/export")
def api_children_export():
    return jsonify(_children)

@app.post("/api/children")
def api_children_add():
    global _nxt_c
    ip = (request.get_json(silent=True) or {}).get("ip", "").strip()
    if not ip:
        return jsonify(ok=False, err="ip required"), 400
    with _lock:
        child = {"id": _nxt_c, "ip": ip, "hostname": ip, "name": ip,
                 "desc": "", "sc": 0, "strings": [], "status": 0, "seen": 0}
        _ping(child)
        _children.append(child)
        _nxt_c += 1
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
    with _lock:
        _ping(child)
        _save("children", _children)
    return jsonify(ok=True)

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
         "y": pos_map.get(c["id"], {}).get("y", 0)}
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
    return jsonify(_settings)

@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "units", "canvasW", "canvasH", "darkMode"):
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

# ── Runners ───────────────────────────────────────────────────────────────────

@app.get("/api/runners")
def api_runners():
    return jsonify([
        {"id": r["id"], "name": r["name"],
         "steps": len(r.get("steps", [])),
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
    pkt = _hdr(CMD_RUNNER_STOP)
    for c in _children:
        if c["status"] == 1:
            _send(c["ip"], pkt)
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeRunner"] = -1
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
    with _lock:
        r["computed"] = True
        _save("runners", _runners)
    return jsonify(ok=True)

@app.post("/api/runners/<int:rid>/sync")
def api_runner_sync(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r or not r.get("computed"):
        return jsonify(ok=False, err="not computed"), 400
    steps = r.get("steps", [])
    for child in _children:
        if child["status"] != 1:
            continue
        for i, step in enumerate(steps):
            _send_recv(child["ip"], _load_step_pkt(i, len(steps), step, child),
                       timeout=0.5)
    return jsonify(ok=True)

@app.post("/api/runners/<int:rid>/start")
def api_runner_start(rid):
    r = next((x for x in _runners if x["id"] == rid), None)
    if not r:
        return jsonify(ok=False, err="not found"), 404
    go_epoch = int(time.time()) + 2      # 2 s from now — time for UDP to reach children
    pkt = _hdr(CMD_RUNNER_GO, go_epoch)
    for c in _children:
        if c["status"] == 1:
            _send(c["ip"], pkt)
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeRunner"]  = rid
        _settings["runnerElapsed"] = 0
        _save("settings", _settings)
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

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SlyLED Parent Server")
    ap.add_argument("--port",       type=int, default=8080)
    ap.add_argument("--host",       default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open, daemon=True).start()

    print(f"SlyLED Parent  v{VERSION}")
    print(f"  UI   →  http://localhost:{args.port}")
    print(f"  Data →  {DATA}")
    app.run(host=args.host, port=args.port, threaded=True)
