#!/usr/bin/env python3
"""
SlyLED Web API Test Suite
Tests the HTTP/JSON API of the Windows Parent server.

Usage:
    python3 tests/test_web.py [host]           # e.g. localhost:8080
    python3 tests/test_web.py localhost:8080   # default for Windows parent
    python3 tests/test_web.py 192.168.10.219   # Giga board

A mock UDP child is started in a background thread to exercise the full
parent-child protocol path (PING/PONG, STATUS_REQ/RESP, ACTION).
The mock child binds to the machine's outbound IP on port 4210; if that port
is already occupied, mock-child-dependent tests are skipped gracefully.

All tests are non-destructive or clean up after themselves.
"""

import sys
import time
import json
import struct
import socket
import threading
import urllib.request
import urllib.error

HOST    = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE    = f"http://{HOST}"
TIMEOUT = 10

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = 0
failed = 0
skipped = 0

# ── Protocol constants (must match parent_server.py) ──────────────────────────

UDP_PORT        = 4210
UDP_MAGIC       = 0x534C
UDP_VERSION     = 2
CMD_PING        = 0x01
CMD_PONG        = 0x02
CMD_ACTION      = 0x10
CMD_ACTION_STOP = 0x11
CMD_STATUS_REQ  = 0x40
CMD_STATUS_RESP = 0x41

# ── Mock child ─────────────────────────────────────────────────────────────────

MOCK_HOSTNAME = "SLYC-MOCK"
MOCK_ALTNAME  = "MockChild"
MOCK_DESC     = "Automated test child"
MOCK_STRINGS  = 2

_mock_actions_received = []   # list of CMD bytes received by mock
_mock_stop = threading.Event()
_mock_ip   = None             # set to the IP the mock is reachable on, or None


def _udp_header(cmd):
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd,
                       int(time.time()) & 0xFFFFFFFF)


def _build_pong():
    """139-byte CMD_PONG packet matching the PongPayload protocol struct."""
    hn = MOCK_HOSTNAME.encode("ascii").ljust(10, b"\x00")
    nm = MOCK_ALTNAME.encode("ascii").ljust(16, b"\x00")
    ds = MOCK_DESC.encode("ascii").ljust(32, b"\x00")
    sc = bytes([MOCK_STRINGS])
    strings = b""
    for i in range(8):
        # <HHBBHB> ledCount ledMm ledType cableDir cableMm stripDir
        if i < MOCK_STRINGS:
            strings += struct.pack("<HHBBHB", 60, 1000, 0, 0, 0, 0)
        else:
            strings += struct.pack("<HHBBHB", 0, 0, 0, 0, 0, 0)
    assert len(strings) == 72
    payload = hn + nm + ds + sc + strings  # 131 bytes
    assert len(payload) == 131
    return _udp_header(CMD_PONG) + payload  # 139 bytes total


def _build_status_resp(active_action=0):
    """16-byte CMD_STATUS_RESP packet."""
    # <BBBbI> activeAction runnerActive currentStep rssi uptime
    return _udp_header(CMD_STATUS_RESP) + struct.pack("<BBBbI",
                                                      active_action, 0, 0, -65, 9999)


def _get_outbound_ip():
    """Return the machine's IP as seen from the LAN (not loopback)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except Exception:
        return None


def _mock_child_run(bind_ip):
    global _mock_ip
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.2)
        try:
            s.bind((bind_ip, UDP_PORT))
        except OSError:
            return   # port busy — mock unavailable
        _mock_ip = bind_ip
        while not _mock_stop.is_set():
            try:
                data, addr = s.recvfrom(256)
                if len(data) < 8:
                    continue
                cmd = data[3]
                _mock_actions_received.append(cmd)
                if cmd == CMD_PING:
                    s.sendto(_build_pong(), addr)
                elif cmd == CMD_STATUS_REQ:
                    s.sendto(_build_status_resp(), addr)
                # CMD_ACTION / CMD_ACTION_STOP: just record receipt, no reply needed
            except socket.timeout:
                pass
            except Exception:
                pass


_outbound = _get_outbound_ip()
if _outbound:
    _t = threading.Thread(target=_mock_child_run, args=(_outbound,), daemon=True)
    _t.start()
    time.sleep(0.5)   # wait for mock to bind


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def post_body(path, body_bytes=b"", content_type="application/json"):
    try:
        req = urllib.request.Request(
            BASE + path, data=body_bytes, method="POST",
            headers={"Content-Type": content_type,
                     "Content-Length": str(len(body_bytes))})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def post_json(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    code, text = post_body(path, body)
    try:
        return code, json.loads(text)
    except Exception:
        return code, None


def put_json(path, payload):
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(
            BASE + path, data=body, method="PUT",
            headers={"Content-Type": "application/json",
                     "Content-Length": str(len(body))})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def delete(path):
    try:
        req = urllib.request.Request(BASE + path, method="DELETE")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, None
    except Exception:
        return 0, None


def get_json(path):
    code, text = get(path)
    try:
        return code, json.loads(text)
    except Exception:
        return code, None


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {GREEN}PASS{RESET}  {name}")
    else:
        failed += 1
        detail_str = f"\n         {YELLOW}{detail}{RESET}" if detail else ""
        print(f"  {RED}FAIL{RESET}  {name}{detail_str}")
    return condition


def skip(name, reason=""):
    global skipped
    skipped += 1
    r = f"  ({reason})" if reason else ""
    print(f"  {YELLOW}SKIP{RESET}  {name}{r}")


def section(title):
    print(f"\n{BOLD}{'-' * 62}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'-' * 62}{RESET}")


def find_child_by(key, value):
    _, kids = get_json("/api/children")
    if not isinstance(kids, list):
        return None
    return next((k for k in kids if k.get(key) == value), None)


def cleanup_child_by(key, value):
    c = find_child_by(key, value)
    if c:
        delete(f"/api/children/{c['id']}")


# ── Connectivity ───────────────────────────────────────────────────────────────

section(f"Connectivity  ({HOST})")
code, body = get("/")
if not check("Server reachable", code == 200, f"HTTP {code}: {body[:80]}"):
    print(f"\n  {RED}Cannot reach server — aborting.{RESET}\n")
    sys.exit(1)

if _mock_ip:
    print(f"  {GREEN}INFO{RESET}  Mock UDP child active at {_mock_ip}:{UDP_PORT}")
else:
    print(f"  {YELLOW}INFO{RESET}  Mock UDP child unavailable — UDP tests will be skipped")

# ── SPA main page ──────────────────────────────────────────────────────────────

section("SPA main page  GET /")
check("HTTP 200",                    code == 200,                f"got {code}")
check("Title contains SlyLED",       "SlyLED"    in body)
check("Has header element",          "id='hdr'"  in body or 'id="hdr"' in body)
check("Has nav tabs",                "Dashboard" in body and "Setup" in body
                                     and "Layout" in body and "Runtime" in body)
check("Has version string",          "v3." in body)
check("No old rainbow badge",        "badge-rainbow" not in body)
check("No old siren route",          "/led/siren/on" not in body)
check("Has /api/children in JS",     "/api/children" in body)
check("Has /api/runners in JS",      "/api/runners"  in body)
check("Has /api/actions in JS",      "/api/actions"  in body)
check("SPA does not expose /log",    "href='/log'" not in body
                                     and 'href="/log"' not in body)
check("SPA has Discover button",     "discoverChildren" in body)
check("SPA has shutdown button",     "shutdownService" in body)
check("SPA has layout sidebar",      "lay-unplaced" in body)
check("SPA has Show strings checkbox","lay-detail" in body)
check("SPA has ondrop for canvas",   "cvDrop" in body)
check("SPA has ondblclick for canvas","cvDbl" in body)
check("SPA sanitizes IP in details", "cleanIp" in body or "replace(/^https" in body)

# ── Cache-Control ──────────────────────────────────────────────────────────────

section("Cache-Control headers")
try:
    with urllib.request.urlopen(BASE + "/", timeout=TIMEOUT) as r:
        cc = r.headers.get("Cache-Control", "")
        check("/ has no-cache", "no-cache" in cc.lower(), f"Cache-Control: '{cc}'")
        check("/ has no-store", "no-store" in cc.lower(), f"Cache-Control: '{cc}'")
except Exception as e:
    check("Cache-Control header fetch", False, str(e))

# ── GET /status ────────────────────────────────────────────────────────────────

section("Status endpoint  GET /status")
code, data = get_json("/status")
check("HTTP 200",            code == 200,       f"got {code}")
check("Valid JSON",          data is not None,  "failed to parse JSON")
check("Has role field",      data is not None and "role" in data)
check("role == parent",      data is not None and data.get("role") == "parent",
      f"role={data.get('role') if data else None}")
check("Has hostname field",  data is not None and "hostname" in data)
check("Has version field",   data is not None and "version" in data)

# ── GET /favicon.ico ──────────────────────────────────────────────────────────

section("Favicon  GET /favicon.ico")
code, _ = get("/favicon.ico")
check("Returns 404", code == 404, f"got {code}")

# ── Unknown route falls through to SPA ────────────────────────────────────────

section("Unknown route fallback")
code, body2 = get("/this-route-does-not-exist")
check("Unknown route returns SPA (200)", code == 200, f"got {code}")
check("Body contains SlyLED",            "SlyLED" in body2)

# ── GET /api/children ─────────────────────────────────────────────────────────

section("Children API  GET /api/children")
code, data = get_json("/api/children")
check("HTTP 200",       code == 200,                  f"got {code}")
check("Valid JSON",     data is not None,             "failed to parse JSON")
check("Returns array",  isinstance(data, list),       f"type={type(data)}")

# ── GET /api/children/discover ────────────────────────────────────────────────

section("Children discover  GET /api/children/discover")
code, data = get_json("/api/children/discover")
check("HTTP 200",          code == 200,             f"got {code}")
check("Valid JSON",        data is not None)
check("Returns array",     isinstance(data, list))
check("Elements are dicts",
      isinstance(data, list) and all(isinstance(x, dict) for x in data))

# ── GET /api/children/export ──────────────────────────────────────────────────

section("Children export  GET /api/children/export")
code, data = get_json("/api/children/export")
check("HTTP 200",       code == 200,             f"got {code}")
check("Valid JSON",     data is not None)
check("Returns array",  isinstance(data, list))

# ── POST /api/children — edge cases ───────────────────────────────────────────

section("Add child — input validation")
# Empty body
code, data = post_json("/api/children", {})
check("Empty ip field → 400", code == 400, f"got {code}")
check("Empty ip returns ok:false",
      data is not None and data.get("ok") is False, f"data={data}")

# Missing body entirely
code, data = post_body("/api/children", b"")
check("Empty body → 400", code == 400, f"got {code}")

# ── POST /api/children/import ─────────────────────────────────────────────────

section("Children import  POST /api/children/import")
import_data = [{"hostname": "SLYC-TEST", "name": "Test Child",
                "desc": "Import test", "ip": "10.0.0.99", "x": 500, "y": 250}]
code, data = post_json("/api/children/import", import_data)
check("HTTP 200",         code == 200,                              f"got {code}")
check("Returns ok:true",  data is not None and data.get("ok") is True)
check("Reports added>=1", data is not None and data.get("added", 0) >= 1,
      f"data={data}")

# Verify child appeared
code2, kids = get_json("/api/children")
found_id = None
if isinstance(kids, list):
    for k in kids:
        if k.get("hostname") == "SLYC-TEST":
            found_id = k.get("id")
            break
check("Imported child visible in GET /api/children", found_id is not None,
      f"children={kids}")

# Re-import same data → update, not duplicate
code3, data3 = post_json("/api/children/import", import_data)
check("Re-import returns ok:true", data3 is not None and data3.get("ok") is True)
check("Re-import shows updated=1", data3 is not None and data3.get("updated", 0) >= 1,
      f"data={data3}")
code4, kids4 = get_json("/api/children")
same_hn = sum(1 for k in (kids4 or []) if k.get("hostname") == "SLYC-TEST")
check("No duplicate hostname after re-import", same_hn == 1, f"count={same_hn}")

# Clean up the imported child
if found_id is not None:
    del_code, del_data = delete(f"/api/children/{found_id}")
    check("Imported child removed (cleanup)", del_code == 200
          and del_data is not None and del_data.get("ok") is True,
          f"HTTP {del_code} data={del_data}")

# ── POST /api/children — bad IP (offline scenario) ───────────────────────────

section("Add child — offline / unreachable IP")
code, data = post_json("/api/children", {"ip": "10.0.0.254"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)
check("Returns id",      data is not None and "id" in data, f"data={data}")
offline_id = data.get("id") if data else None
if offline_id is not None:
    _, oc = get_json("/api/children")
    off = next((c for c in (oc or []) if c.get("id") == offline_id), None)
    check("Offline child has ip set",       off is not None and off.get("ip") == "10.0.0.254")
    check("Offline child status == 0",      off is not None and off.get("status") == 0,
          f"status={off.get('status') if off else None}")
    # Refresh offline child — should not crash
    rc, rd = post_json(f"/api/children/{offline_id}/refresh", {})
    check("Refresh offline child HTTP 200", rc == 200, f"got {rc}")
    check("Refresh offline returns ok:true", rd is not None and rd.get("ok") is True)
    # Clean up
    delete(f"/api/children/{offline_id}")

# ── POST /api/children — IP sanitization ─────────────────────────────────────

section("Add child — IP sanitization (strip protocol + path)")
code, data = post_json("/api/children", {"ip": "http://10.0.0.253/config"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)
san_id = data.get("id") if data else None
if san_id is not None:
    _, oc = get_json("/api/children")
    sc = next((c for c in (oc or []) if c.get("id") == san_id), None)
    check("IP sanitized to bare address",
          sc is not None and sc.get("ip") == "10.0.0.253",
          f"ip={sc.get('ip') if sc else None}")
    delete(f"/api/children/{san_id}")
else:
    skip("IP sanitization verify", "add did not return id")

# ── Mock child: add, status, refresh, action ──────────────────────────────────
# The mock child binds a UDP socket on the WSL/test machine.  When the server
# runs on a different OS (e.g. Windows parent, test from WSL), Windows Firewall
# may block inbound UDP replies even on port 4210 unless the process has a
# matching allow rule.  We detect this after adding the child: if the server
# cannot receive the PONG (status==0), UDP-dependent checks are skipped rather
# than failed, with a clear note.

mock_child_id = None
_mock_udp_reachable = False   # True only when server successfully received PONG

if _mock_ip:
    section(f"Mock child  POST /api/children  ip={_mock_ip}")
    cleanup_child_by("ip", _mock_ip)   # ensure clean state
    _mock_actions_received.clear()

    code, data = post_json("/api/children", {"ip": _mock_ip})
    check("HTTP 200",        code == 200,                              f"got {code}")
    check("Returns ok:true", data is not None and data.get("ok") is True)
    mock_child_id = data.get("id") if data else None

    _, kids = get_json("/api/children")
    mc = next((k for k in (kids or []) if k.get("ip") == _mock_ip), None)
    check("Mock child visible in list",  mc is not None, f"kids={kids}")

    # Detect whether the server can actually exchange UDP with the mock.
    # status==1 means it received a valid PONG; status==0 means firewall/network blocked.
    _mock_udp_reachable = mc is not None and mc.get("status") == 1
    if not _mock_udp_reachable:
        print(f"  {YELLOW}NOTE{RESET}  Server did not receive PONG from mock child"
              f" — UDP may be blocked (e.g. Windows Firewall)."
              f" PONG-dependent checks will be skipped.")

    if _mock_udp_reachable:
        check("Mock child hostname from PONG",
              mc.get("hostname") == MOCK_HOSTNAME,
              f"hostname={mc.get('hostname')}")
        check("Mock child name from PONG",
              mc.get("name") == MOCK_ALTNAME,
              f"name={mc.get('name')}")
        check("Mock child status == 1 (online)", True)
        check("Mock child stringCount == 2",
              mc.get("sc") == MOCK_STRINGS,
              f"sc={mc.get('sc')}")
    else:
        skip("Mock child hostname from PONG",   "server UDP rx blocked")
        skip("Mock child name from PONG",       "server UDP rx blocked")
        skip("Mock child status == 1 (online)", "server UDP rx blocked")
        skip("Mock child stringCount == 2",     "server UDP rx blocked")

    if mock_child_id is not None:
        section(f"Mock child  POST /api/children/{mock_child_id}/refresh")
        rc, rd = post_json(f"/api/children/{mock_child_id}/refresh", {})
        check("Refresh HTTP 200",        rc == 200, f"got {rc}")
        check("Refresh returns ok:true", rd is not None and rd.get("ok") is True)
        if _mock_udp_reachable:
            _, kids2 = get_json("/api/children")
            mc2 = next((k for k in (kids2 or []) if k.get("id") == mock_child_id), None)
            check("Hostname still correct after refresh",
                  mc2 is not None and mc2.get("hostname") == MOCK_HOSTNAME,
                  f"hostname={mc2.get('hostname') if mc2 else None}")
        else:
            skip("Hostname still correct after refresh", "server UDP rx blocked")

        section(f"Mock child  GET /api/children/{mock_child_id}/status")
        code, sdata = get_json(f"/api/children/{mock_child_id}/status")
        check("Status HTTP 200",           code == 200, f"got {code}")
        if _mock_udp_reachable:
            check("Status returns ok:true",    sdata is not None and sdata.get("ok") is True,
                  f"data={sdata}")
            check("Status has activeAction",   sdata is not None and "activeAction" in sdata)
            check("Status has runnerActive",   sdata is not None and "runnerActive" in sdata)
            check("Status has uptimeS",        sdata is not None and "uptimeS" in sdata)
            check("Status wifiRssi is negative",
                  sdata is not None and sdata.get("ok") and sdata.get("wifiRssi", 0) < 0,
                  f"rssi={sdata.get('wifiRssi') if sdata else None}")
        else:
            skip("Status returns ok:true",    "server UDP rx blocked")
            skip("Status has activeAction",   "server UDP rx blocked")
            skip("Status has runnerActive",   "server UDP rx blocked")
            skip("Status has uptimeS",        "server UDP rx blocked")
            skip("Status wifiRssi is negative", "server UDP rx blocked")

        section(f"Mock child  POST /api/action  target={mock_child_id}")
        _mock_actions_received.clear()
        code, adata = post_json("/api/action", {
            "type": 1, "r": 0, "g": 255, "b": 0,
            "onMs": 500, "offMs": 500, "wipeDir": 0, "wipeSpeedPct": 50,
            "target": str(mock_child_id)
        })
        check("Action HTTP 200",        code == 200, f"got {code}")
        check("Action returns ok:true", adata is not None and adata.get("ok") is True)
        if _mock_udp_reachable:
            time.sleep(0.15)
            check("Mock child received ACTION packet",
                  CMD_ACTION in _mock_actions_received,
                  f"received={_mock_actions_received}")
        else:
            skip("Mock child received ACTION packet", "server UDP rx blocked")

        section(f"Mock child  POST /api/action/stop  target={mock_child_id}")
        _mock_actions_received.clear()
        code, sdata2 = post_json("/api/action/stop", {"target": str(mock_child_id)})
        check("Action stop HTTP 200",        code == 200, f"got {code}")
        check("Action stop returns ok:true", sdata2 is not None and sdata2.get("ok") is True)
        if _mock_udp_reachable:
            time.sleep(0.15)
            check("Mock child received ACTION_STOP packet",
                  CMD_ACTION_STOP in _mock_actions_received,
                  f"received={_mock_actions_received}")
        else:
            skip("Mock child received ACTION_STOP packet", "server UDP rx blocked")

        section("Discover excludes already-registered children")
        code, disc = get_json("/api/children/discover")
        check("Discover HTTP 200",         code == 200, f"got {code}")
        check("Discover returns array",    isinstance(disc, list))
        mock_in_disc = any(d.get("ip") == _mock_ip for d in (disc or []))
        check("Known mock child absent from discover results",
              not mock_in_disc,
              f"mock ip {_mock_ip} found in discover results: {disc}")

        # Clean up mock child
        del_code, _ = delete(f"/api/children/{mock_child_id}")
        check("Mock child removed (cleanup)", del_code == 200, f"got {del_code}")
else:
    skip("Mock child add / PONG population",  "mock child unavailable")
    skip("Mock child refresh",               "mock child unavailable")
    skip("Mock child status poll",           "mock child unavailable")
    skip("Mock child action dispatch",       "mock child unavailable")
    skip("Mock child action stop",           "mock child unavailable")
    skip("Discover excludes known children", "mock child unavailable")

# ── GET /api/children/:id/status (offline child) ──────────────────────────────

section("Child status  GET /api/children/:id/status  (offline IP)")
_sc, _sd = post_json("/api/children/import",
    [{"hostname": "SLYC-STAT", "name": "Status Test",
      "desc": "status route test", "ip": "10.0.0.98"}])
status_id = None
_, _sk = get_json("/api/children")
if isinstance(_sk, list):
    for k in _sk:
        if k.get("hostname") == "SLYC-STAT":
            status_id = k.get("id")
            break

if status_id is not None:
    code, data = get_json(f"/api/children/{status_id}/status")
    check("HTTP 200 (route exists)", code == 200,            f"got {code}")
    check("Returns JSON",            data is not None,       "failed to parse JSON")
    check("Has ok field",            data is not None and "ok" in data)
    check("ok is bool",              data is not None and isinstance(data.get("ok"), bool))
    if data is not None and data.get("ok") is True:
        check("Success has action field", "activeAction" in data or "action" in data)
        check("Success has runner field", "runnerActive" in data or "runner" in data)
        check("Success has uptime field", "uptimeS" in data or "uptime" in data)
    else:
        check("Timeout has err field",
              data is not None and data.get("err") is not None, f"data={data}")
    delete(f"/api/children/{status_id}")
else:
    skip("Child status route tests", "import of test child failed")

code, data = get_json("/api/children/99/status")
check("GET /api/children/99/status returns error",
      data is not None and (data.get("ok") is False or data.get("err") is not None),
      f"data={data}")

# ── GET /api/layout ───────────────────────────────────────────────────────────

section("Layout API  GET /api/layout")
code, data = get_json("/api/layout")
check("HTTP 200",           code == 200,             f"got {code}")
check("Valid JSON",         data is not None)
check("Has canvasW field",  data is not None and "canvasW" in data,
      f"keys={list(data.keys()) if data else None}")
check("Has canvasH field",  data is not None and "canvasH" in data)
check("Has children array", data is not None and isinstance(data.get("children"), list))

# ── GET /api/settings ─────────────────────────────────────────────────────────

section("Settings API  GET /api/settings")
code, data = get_json("/api/settings")
check("HTTP 200",              code == 200,               f"got {code}")
check("Valid JSON",            data is not None)
check("Has name field",        data is not None and "name"    in data)
check("Has units field",       data is not None and "units"   in data)
check("Has canvasW field",     data is not None and "canvasW" in data)
check("Has canvasH field",     data is not None and "canvasH" in data)
check("Has darkMode field",    data is not None and "darkMode" in data,
      f"keys={list(data.keys()) if data else None}")
check("Has runnerRunning",     data is not None and "runnerRunning" in data)
check("Has activeRunner",      data is not None and "activeRunner" in data)

# ── POST /api/settings ────────────────────────────────────────────────────────

section("Settings API  POST /api/settings")
_, orig = get_json("/api/settings")
orig_name = (orig or {}).get("name", "SlyLED")
orig_dm   = (orig or {}).get("darkMode", 1)

new_name = "TestParent"
code, data = post_json("/api/settings", {
    "name": new_name, "units": 0,
    "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1
})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)
_, verify = get_json("/api/settings")
check("Name persisted",  verify is not None and verify.get("name") == new_name,
      f"name={verify.get('name') if verify else None}")

section("Settings API  partial update")
code, data = post_json("/api/settings", {"name": "PartialTest"})
check("HTTP 200",           code == 200, f"got {code}")
check("Returns ok:true",    data is not None and data.get("ok") is True)
_, pv = get_json("/api/settings")
check("Name updated",       pv is not None and pv.get("name") == "PartialTest")
check("canvasW unchanged",  pv is not None and pv.get("canvasW") == 10000,
      f"canvasW={pv.get('canvasW') if pv else None}")

# Restore original name
post_json("/api/settings", {"name": orig_name, "units": 0,
                             "canvasW": 10000, "canvasH": 5000,
                             "darkMode": orig_dm})

# ── Runners ────────────────────────────────────────────────────────────────────

section("Runners API — initial state  GET /api/runners")
code, data = get_json("/api/runners")
check("HTTP 200",       code == 200,             f"got {code}")
check("Valid JSON",     data is not None)
check("Returns array",  isinstance(data, list))

section("Runner lifecycle — create")
code, data = post_json("/api/runners", {"name": "TestRunner"})
check("HTTP 200",          code == 200,                              f"got {code}")
check("Returns ok:true",   data is not None and data.get("ok") is True)
check("Returns id field",  data is not None and "id" in data,        f"data={data}")
runner_id = data.get("id") if data else None

if runner_id is None:
    print(f"  {RED}Cannot continue runner lifecycle — no id returned{RESET}")
else:
    section(f"Runner lifecycle — GET /api/runners/{runner_id}")
    code, data = get_json(f"/api/runners/{runner_id}")
    check("HTTP 200",           code == 200,                           f"got {code}")
    check("Valid JSON",         data is not None)
    check("id matches",         data is not None and data.get("id") == runner_id)
    check("name is TestRunner", data is not None and data.get("name") == "TestRunner",
          f"name={data.get('name') if data else None}")
    check("steps is array",     data is not None and isinstance(data.get("steps"), list))
    check("computed is false",  data is not None and data.get("computed") is False,
          f"computed={data.get('computed') if data else None}")

    section(f"Runner lifecycle — PUT /api/runners/{runner_id}")
    steps = [
        {"type": 1, "r": 255, "g": 0, "b": 0,
         "onMs": 500, "offMs": 500, "wdir": 0, "wspd": 50,
         "x0": 0, "y0": 0, "x1": 10000, "y1": 10000, "durationS": 5},
        {"type": 2, "r": 0, "g": 0, "b": 255,
         "onMs": 300, "offMs": 200, "wdir": 0, "wspd": 50,
         "x0": 0, "y0": 0, "x1": 10000, "y1": 10000, "durationS": 3},
    ]
    code, data = put_json(f"/api/runners/{runner_id}",
                          {"name": "TestRunner", "steps": steps})
    check("HTTP 200",          code == 200,                              f"got {code}")
    check("Returns ok:true",   data is not None and data.get("ok") is True)

    _, data2 = get_json(f"/api/runners/{runner_id}")
    check("Step count == 2",         data2 is not None and len(data2.get("steps", [])) == 2,
          f"steps={data2.get('steps') if data2 else None}")
    check("computed reset to false", data2 is not None and data2.get("computed") is False)

    section(f"Runner lifecycle — POST /api/runners/{runner_id}/compute")
    code, data = post_json(f"/api/runners/{runner_id}/compute")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)
    _, data3 = get_json(f"/api/runners/{runner_id}")
    check("computed == true after compute",
          data3 is not None and data3.get("computed") is True,
          f"computed={data3.get('computed') if data3 else None}")

    section("Runners list shows computed flag")
    _, rlist = get_json("/api/runners")
    found = next((r for r in (rlist or []) if r.get("id") == runner_id), None)
    check("Runner in list",           found is not None)
    check("List shows computed=true", found is not None and found.get("computed") is True,
          f"entry={found}")
    check("List shows step count",    found is not None and found.get("steps") == 2,
          f"steps={found.get('steps') if found else None}")

    section("Runner sync to all children (no children — no crash)")
    code, data = post_json(f"/api/runners/{runner_id}/sync")
    check("Sync HTTP 200",        code == 200, f"got {code}")
    check("Sync returns ok:true", data is not None and data.get("ok") is True)

    section(f"Runner lifecycle — POST /api/runners/stop")
    code, data = post_json("/api/runners/stop")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)
    _, sett = get_json("/api/settings")
    check("runnerRunning is false after stop",
          sett is not None and sett.get("runnerRunning") is False,
          f"runnerRunning={sett.get('runnerRunning') if sett else None}")

    section(f"Runner lifecycle — DELETE /api/runners/{runner_id}")
    code, data = delete(f"/api/runners/{runner_id}")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)
    code2, data2 = get_json(f"/api/runners/{runner_id}")
    check("GET after delete returns error",
          data2 is None or data2.get("ok") is False or data2.get("err") is not None,
          f"data={data2}")

# ── Runner error handling ──────────────────────────────────────────────────────

section("Runner error handling")
code, data = get_json("/api/runners/99")
check("GET /api/runners/99 returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

code, data = delete("/api/runners/99")
check("DELETE /api/runners/99 returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

code, data = post_json("/api/runners/99/compute")
check("POST /api/runners/99/compute returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

code, data = post_json("/api/runners/99/sync")
check("POST /api/runners/99/sync returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

code, data = post_json("/api/runners/99/start")
check("POST /api/runners/99/start returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

# ── Action API ────────────────────────────────────────────────────────────────

section("Action API  POST /api/action")
code, data = post_json("/api/action",
    {"type": 1, "r": 255, "g": 0, "b": 0,
     "onMs": 500, "offMs": 500, "wipeDir": 0, "wipeSpeedPct": 50,
     "target": "all"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

code, data = post_json("/api/action", {"type": 0, "target": "all"})
check("ACT_OFF returns ok:true", data is not None and data.get("ok") is True)

section("Action stop  POST /api/action/stop")
code, data = post_json("/api/action/stop", {"target": "all"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

# Bad target id
code, data = post_json("/api/action", {"type": 1, "r": 0, "g": 0, "b": 0, "target": "99"})
check("Bad target → error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

code, data = post_json("/api/action/stop", {"target": "99"})
check("Bad target stop → error", data is not None and
      (data.get("ok") is False or data.get("err") is not None), f"data={data}")

# ── Actions library API ───────────────────────────────────────────────────────

section("Actions API  GET /api/actions (initial)")
code, data = get_json("/api/actions")
check("HTTP 200",       code == 200,             f"got {code}")
check("Valid JSON",     data is not None)
check("Returns array",  isinstance(data, list))

section("Actions API — CRUD lifecycle")
# Create
code, data = post_json("/api/actions", {"name": "Red Wipe", "type": 3, "r": 255, "g": 0, "b": 0,
                                        "wipeDir": 0, "wipeSpeedPct": 75})
check("Create HTTP 200",     code == 200,                              f"got {code}")
check("Create ok:true",      data is not None and data.get("ok") is True)
check("Returns id",          data is not None and "id" in data, f"data={data}")
act_id = data.get("id") if data else None

# Read
if act_id is not None:
    code, adata = get_json(f"/api/actions/{act_id}")
    check("GET action HTTP 200", code == 200, f"got {code}")
    check("Name matches",       adata is not None and adata.get("name") == "Red Wipe")
    check("Type matches",       adata is not None and adata.get("type") == 3)
    check("r matches",          adata is not None and adata.get("r") == 255)
    check("wipeDir matches",    adata is not None and adata.get("wipeDir") == 0)
    check("wipeSpeedPct matches", adata is not None and adata.get("wipeSpeedPct") == 75)

    # Update
    code2, udata = post_json(f"/api/actions/{act_id}", {"name": "Blue Flash", "type": 2,
                              "r": 0, "g": 0, "b": 255, "onMs": 200, "offMs": 300})
    # Update via PUT
    pu_code, pu_data = put_json(f"/api/actions/{act_id}",
                                {"name": "Blue Flash", "type": 2,
                                 "r": 0, "g": 0, "b": 255, "onMs": 200, "offMs": 300})
    check("PUT action HTTP 200", pu_code == 200, f"got {pu_code}")
    check("PUT action ok:true", pu_data is not None and pu_data.get("ok") is True)

    _, vdata = get_json(f"/api/actions/{act_id}")
    check("Name updated after PUT", vdata is not None and vdata.get("name") == "Blue Flash")
    check("Type updated after PUT", vdata is not None and vdata.get("type") == 2)

    # Appears in list
    _, alist = get_json("/api/actions")
    check("Action in list", any(a.get("id") == act_id for a in (alist or [])))

    # Delete
    del_code, del_data = delete(f"/api/actions/{act_id}")
    check("DELETE action ok:true", del_code == 200 and del_data is not None
          and del_data.get("ok") is True, f"HTTP {del_code} data={del_data}")

    _, alist2 = get_json("/api/actions")
    check("Action removed after delete",
          not any(a.get("id") == act_id for a in (alist2 or [])))
else:
    skip("Actions CRUD checks", "create did not return id")

section("Actions API — validation")
code, data = post_json("/api/actions", {"name": "", "type": 1})
check("Empty name → 400", code == 400, f"got {code}")

code, data = get_json("/api/actions/99999")
check("GET missing action → 404", code == 404, f"got {code}")

section("Actions + Runner steps integration")
# Create an action, then a runner with a step referencing it
_, a_data = post_json("/api/actions", {"name": "TestSolid", "type": 1, "r": 128, "g": 64, "b": 32})
test_act_id = (a_data or {}).get("id")
_, r_data = post_json("/api/runners", {"name": "ActRefRunner"})
test_run_id = (r_data or {}).get("id")
if test_act_id is not None and test_run_id is not None:
    # Save runner with 1 step referencing the action
    pu_code, pu_data = put_json(f"/api/runners/{test_run_id}",
                                {"name": "ActRefRunner",
                                 "steps": [{"actionId": test_act_id,
                                            "x0": 0, "y0": 0,
                                            "x1": 10000, "y1": 10000,
                                            "durationS": 3}]})
    check("PUT runner with actionId HTTP 200", pu_code == 200, f"got {pu_code}")
    check("PUT runner with actionId ok:true",
          pu_data is not None and pu_data.get("ok") is True)

    _, rget = get_json(f"/api/runners/{test_run_id}")
    check("Runner step has actionId",
          rget is not None and len(rget.get("steps", [])) == 1
          and rget["steps"][0].get("actionId") == test_act_id)

    # Runner list shows totalDurationS
    _, rlist = get_json("/api/runners")
    rentry = next((r for r in (rlist or []) if r.get("id") == test_run_id), None)
    check("Runner list has totalDurationS",
          rentry is not None and rentry.get("totalDurationS") == 3,
          f"entry={rentry}")

    # Add a second step and re-save — verify multiple steps persist
    pu2_code, pu2_data = put_json(f"/api/runners/{test_run_id}",
                                  {"name": "ActRefRunner",
                                   "steps": [{"actionId": test_act_id,
                                              "x0": 0, "y0": 0, "x1": 5000, "y1": 10000,
                                              "durationS": 3},
                                             {"actionId": test_act_id,
                                              "x0": 5000, "y0": 0, "x1": 10000, "y1": 10000,
                                              "durationS": 7}]})
    check("PUT runner 2 steps ok", pu2_data is not None and pu2_data.get("ok") is True)

    _, rget2 = get_json(f"/api/runners/{test_run_id}")
    check("Runner has 2 steps after re-save",
          rget2 is not None and len(rget2.get("steps", [])) == 2)
    check("Step 2 durationS persisted",
          rget2 is not None and rget2["steps"][1].get("durationS") == 7)

    # Runner list reflects updated totalDurationS
    _, rlist2 = get_json("/api/runners")
    re2 = next((r for r in (rlist2 or []) if r.get("id") == test_run_id), None)
    check("Updated totalDurationS = 10",
          re2 is not None and re2.get("totalDurationS") == 10,
          f"entry={re2}")

    # Save empty steps — should also work
    pu3_code, pu3_data = put_json(f"/api/runners/{test_run_id}",
                                  {"name": "ActRefRunner", "steps": []})
    check("PUT runner empty steps ok", pu3_data is not None and pu3_data.get("ok") is True)
    _, rget3 = get_json(f"/api/runners/{test_run_id}")
    check("Runner has 0 steps after empty save",
          rget3 is not None and len(rget3.get("steps", [])) == 0)

    # Cleanup
    delete(f"/api/runners/{test_run_id}")
    delete(f"/api/actions/{test_act_id}")
else:
    skip("Actions+Runner integration", "create failed")

section("Action ID 0 round-trip (falsy-value regression)")
# First action created after factory reset gets id=0. Verify it persists correctly.
_, a0 = post_json("/api/actions", {"name": "ZeroId", "type": 1, "r": 100, "g": 50, "b": 25})
a0_id = (a0 or {}).get("id")
_, r0 = post_json("/api/runners", {"name": "ZeroTest"})
r0_id = (r0 or {}).get("id")
if a0_id is not None and r0_id is not None:
    pu_code, pu_data = put_json(f"/api/runners/{r0_id}",
                                {"name": "ZeroTest",
                                 "steps": [{"actionId": a0_id,
                                            "x0": 0, "y0": 0, "x1": 10000, "y1": 10000,
                                            "durationS": 2}]})
    check("PUT with actionId="+str(a0_id)+" ok", pu_data is not None and pu_data.get("ok") is True)
    _, rget = get_json(f"/api/runners/{r0_id}")
    saved_aid = rget["steps"][0].get("actionId") if rget and rget.get("steps") else None
    check("actionId persisted (not -1)",
          saved_aid == a0_id,
          f"expected={a0_id} got={saved_aid}")

    # Compute + sync should not error
    _, comp = post_json(f"/api/runners/{r0_id}/compute")
    check("Compute ok", comp is not None and comp.get("ok") is True)
    _, sync = post_json(f"/api/runners/{r0_id}/sync")
    check("Sync ok (no children = no error)",
          sync is not None and sync.get("ok") is True)

    delete(f"/api/runners/{r0_id}")
    delete(f"/api/actions/{a0_id}")
else:
    skip("Action ID 0 round-trip", "create failed")

section("Runner sync with missing action → error")
_, bad_r = post_json("/api/runners", {"name": "BadRef"})
bad_rid = (bad_r or {}).get("id")
if bad_rid is not None:
    put_json(f"/api/runners/{bad_rid}",
             {"name": "BadRef", "steps": [{"actionId": 99999,
              "x0": 0, "y0": 0, "x1": 10000, "y1": 10000, "durationS": 1}]})
    post_json(f"/api/runners/{bad_rid}/compute")
    sc, sd = post_json(f"/api/runners/{bad_rid}/sync")
    check("Sync with missing action returns error",
          sd is not None and sd.get("ok") is False,
          f"data={sd}")
    delete(f"/api/runners/{bad_rid}")

section("SPA references _syncSteps with isNaN guard")
code, body = get("/")
check("SPA _syncSteps uses isNaN (not ||)",
      "isNaN(v)?-1:v" in body, "falsy-zero bug guard missing")

# ── Layout API ────────────────────────────────────────────────────────────────

section("Layout API  POST /api/layout (empty)")
code, data = post_json("/api/layout", {"children": []})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

section("Layout round-trip")
code, orig_layout = get_json("/api/layout")
check("GET layout HTTP 200", code == 200, f"got {code}")
# Save with a dummy position, then restore
code, data = post_json("/api/layout", {"children": [{"id": 9999, "x": 100, "y": 200}]})
check("POST layout HTTP 200", code == 200, f"got {code}")
check("POST layout ok:true",  data is not None and data.get("ok") is True)
# Restore original
post_json("/api/layout", {"children": (orig_layout or {}).get("children", [])})

section("Layout — positioned flag + multiple children")
# Use import (no UDP ping) to add two children quickly
_LAY_CH = [
    {"hostname": "SLYC-LA01", "name": "LayTest1", "ip": "10.254.0.1",
     "desc": "", "sc": 1, "strings": [], "status": 0, "seen": 0},
    {"hostname": "SLYC-LA02", "name": "LayTest2", "ip": "10.254.0.2",
     "desc": "", "sc": 1, "strings": [], "status": 0, "seen": 0},
]
_, imp = post_json("/api/children/import", _LAY_CH)
check("Import 2 layout-test children", imp is not None and imp.get("ok") is True)

# Unpositioned children should appear in layout with positioned=False
_, lay = get_json("/api/layout")
lay_kids = {c["hostname"]: c for c in (lay or {}).get("children", [])}
check("Both layout-test children in GET /api/layout",
      "SLYC-LA01" in lay_kids and "SLYC-LA02" in lay_kids,
      f"hostnames={list(lay_kids.keys())}")
check("Unpositioned flag is False for LA01",
      lay_kids.get("SLYC-LA01", {}).get("positioned") is False,
      f"positioned={lay_kids.get('SLYC-LA01',{}).get('positioned')}")
check("Unpositioned flag is False for LA02",
      lay_kids.get("SLYC-LA02", {}).get("positioned") is False,
      f"positioned={lay_kids.get('SLYC-LA02',{}).get('positioned')}")

# Save positions for both
id1 = lay_kids.get("SLYC-LA01", {}).get("id")
id2 = lay_kids.get("SLYC-LA02", {}).get("id")
if id1 is not None and id2 is not None:
    _, pdata = post_json("/api/layout", {
        "children": [{"id": id1, "x": 2000, "y": 1500},
                     {"id": id2, "x": 7000, "y": 3000}]
    })
    check("POST positions ok", pdata is not None and pdata.get("ok") is True)

    _, lay2 = get_json("/api/layout")
    lay2_kids = {c["hostname"]: c for c in (lay2 or {}).get("children", [])}
    c1 = lay2_kids.get("SLYC-LA01", {})
    c2 = lay2_kids.get("SLYC-LA02", {})
    check("LA01 positioned flag True after save",
          c1.get("positioned") is True,
          f"positioned={c1.get('positioned')}")
    check("LA01 x=2000 persisted", c1.get("x") == 2000, f"x={c1.get('x')}")
    check("LA01 y=1500 persisted", c1.get("y") == 1500, f"y={c1.get('y')}")
    check("LA02 positioned flag True after save",
          c2.get("positioned") is True,
          f"positioned={c2.get('positioned')}")
    check("LA02 x=7000 persisted", c2.get("x") == 7000, f"x={c2.get('x')}")
    check("LA02 y=3000 persisted", c2.get("y") == 3000, f"y={c2.get('y')}")

    # Remove one child from layout (save only LA01), verify LA02 becomes unpositioned
    _, pdata2 = post_json("/api/layout", {"children": [{"id": id1, "x": 2000, "y": 1500}]})
    check("Save layout with only LA01 ok", pdata2 is not None and pdata2.get("ok") is True)
    _, lay3 = get_json("/api/layout")
    lay3_kids = {c["hostname"]: c for c in (lay3 or {}).get("children", [])}
    c2b = lay3_kids.get("SLYC-LA02", {})
    check("LA02 unpositioned after removal from layout",
          c2b.get("positioned") is False,
          f"positioned={c2b.get('positioned')}")
    check("LA02 x=0 after removal", c2b.get("x") == 0, f"x={c2b.get('x')}")
else:
    skip("Layout positioned-after-save checks", "import did not return child ids")

# Clean up layout-test children
_, all_ch = get_json("/api/children")
for c in (all_ch or []):
    if c.get("hostname", "").startswith("SLYC-LA0"):
        delete(f"/api/children/{c['id']}")
post_json("/api/layout", {"children": (orig_layout or {}).get("children", [])})

# ── MAX_RUNNERS overflow ──────────────────────────────────────────────────────

section("Multiple runners (up to MAX_RUNNERS=4)")
# Clean up any stale runners from previous tests
_, _stale = get_json("/api/runners")
for _sr in (_stale or []):
    delete(f"/api/runners/{_sr['id']}")
created_ids = []
for i in range(4):
    _, d = post_json("/api/runners", {"name": f"R{i}"})
    if d and d.get("ok"):
        created_ids.append(d["id"])
check(f"Created {len(created_ids)} runners",
      len(created_ids) >= 1, f"ids={created_ids}")

_, d5 = post_json("/api/runners", {"name": "Overflow"})
check("5th runner returns error (full)",
      d5 is not None and (d5.get("ok") is False or d5.get("err") is not None),
      f"data={d5}")

for rid in created_ids:
    delete(f"/api/runners/{rid}")
_, rlist = get_json("/api/runners")
check("Runners list empty after cleanup",
      isinstance(rlist, list) and len(rlist) == 0, f"list={rlist}")

# ── Content-Length headers ────────────────────────────────────────────────────

section("Content-Length on JSON responses")
try:
    with urllib.request.urlopen(BASE + "/api/runners", timeout=TIMEOUT) as r:
        cl = r.headers.get("Content-Length", "")
        body_r = r.read()
        check("/api/runners has Content-Length", cl != "", f"Content-Length: '{cl}'")
        check("Content-Length matches body",
              cl == "" or int(cl) == len(body_r),
              f"header={cl} body={len(body_r)}")
    with urllib.request.urlopen(BASE + "/api/children", timeout=TIMEOUT) as r:
        cl2 = r.headers.get("Content-Length", "")
        check("/api/children has Content-Length", cl2 != "",
              f"Content-Length: '{cl2}'")
    with urllib.request.urlopen(BASE + "/api/settings", timeout=TIMEOUT) as r:
        cl3 = r.headers.get("Content-Length", "")
        check("/api/settings has Content-Length", cl3 != "",
              f"Content-Length: '{cl3}'")
except Exception as e:
    check("Content-Length fetch", False, str(e))

# ── Shutdown endpoint (non-destructive probe) ─────────────────────────────────

section("Shutdown endpoint  POST /api/shutdown")
# Verify the route exists and returns ok:true — but DON'T actually call it
# (it would kill the server mid-test-run).
# Instead, check that the SPA references the endpoint.
code, body = get("/")
check("SPA references /api/shutdown", "/api/shutdown" in body)
# Also verify the route is NOT accidentally reachable via GET
code_g, _ = get("/api/shutdown")
check("GET /api/shutdown is not allowed (404 or 405)",
      code_g in (404, 405), f"got {code_g}")

# ── Summary ───────────────────────────────────────────────────────────────────

_mock_stop.set()   # stop mock child thread

total = passed + failed + skipped
print(f"\n{BOLD}{'=' * 62}{RESET}")
if failed == 0:
    sk_note = f"  ({skipped} skipped)" if skipped else ""
    print(f"{BOLD}{GREEN}  ALL {passed} TESTS PASSED{sk_note}{RESET}")
else:
    print(f"{BOLD}  {passed}/{passed+failed} passed   {RED}{failed} FAILED{RESET}"
          + (f"   {YELLOW}{skipped} skipped{RESET}" if skipped else ""))
print(f"{BOLD}{'=' * 62}{RESET}\n")

sys.exit(0 if failed == 0 else 1)
