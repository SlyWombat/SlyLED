#!/usr/bin/env python3
"""
test_child.py — Automated tests for the SlyLED child firmware (ESP32 / D1 Mini).

Usage:
    python tests/test_child.py [host] [http_port] [udp_port] [max_strings]

If host is omitted, the script broadcasts a UDP PING and auto-discovers the
first child on the network.

Defaults: http_port=80  udp_port=4210  max_strings=2 (D1 Mini)
  Use max_strings=8 for ESP32 targets.

For Wokwi simulation:
    python tests/test_child.py 127.0.0.1 18080 14210
"""

import socket
import struct
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

# ── Configuration ─────────────────────────────────────────────────────────────

_arg_host       = sys.argv[1] if len(sys.argv) > 1 else None
HTTP_PORT       = int(sys.argv[2]) if len(sys.argv) > 2 else 80
UDP_PORT        = int(sys.argv[3]) if len(sys.argv) > 3 else 4210
CHILD_MAX_STRINGS = int(sys.argv[4]) if len(sys.argv) > 4 else 2  # D1 Mini=2, ESP32=8

# Auto-discover if no host given
if _arg_host is None:
    print("No host specified — broadcasting UDP PING to discover children...")
    _disc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    _disc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _disc_sock.settimeout(0.2)
    _disc_sock.bind(("", UDP_PORT))  # children reply to UDP_PORT, not sender's ephemeral port
    _ping = struct.pack("<HBBI", 0x534C, 2, 0x01, int(time.time()) & 0xFFFFFFFF)
    _disc_sock.sendto(_ping, ("255.255.255.255", UDP_PORT))
    _found = {}
    _deadline = time.time() + 3.0
    while time.time() < _deadline:
        try:
            _data, (_ip, _) = _disc_sock.recvfrom(256)
            if len(_data) >= 139:
                _magic, _ver, _cmd, _ = struct.unpack_from("<HBBI", _data, 0)
                if _magic == 0x534C and _cmd == 0x02 and _ip not in _found:
                    _hn = _data[8:18].rstrip(b'\x00').decode("ascii", errors="replace")
                    _found[_ip] = _hn
        except socket.timeout:
            pass
    _disc_sock.close()
    if not _found:
        print("  No children found via broadcast. Pass IP as first argument.")
        sys.exit(2)
    if len(_found) > 1:
        print(f"  Multiple children found: {list(_found.keys())}")
        print("  Pass the target IP as first argument.")
        sys.exit(2)
    _arg_host, _hn = next(iter(_found.items()))
    print(f"  Found: {_arg_host}  ({_hn})\n")

HOST = _arg_host

# ── UDP protocol constants ────────────────────────────────────────────────────

UDP_MAGIC   = 0x534C
UDP_VERSION = 2

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

ACT_OFF   = 0
ACT_SOLID = 1
ACT_FLASH = 2
ACT_WIPE  = 3

# ── Test harness ──────────────────────────────────────────────────────────────

_passed = 0
_failed = 0
_section = ""

def section(name):
    global _section
    _section = name
    print(f"\n── {name} {'─' * (60 - len(name))}")

def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))

def summary():
    total = _passed + _failed
    print(f"\n{'=' * 64}")
    print(f"  {_passed}/{total} passed", "✓" if _failed == 0 else f"  ({_failed} FAILED)")
    print(f"{'=' * 64}")
    sys.exit(0 if _failed == 0 else 1)

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def http_get(path, timeout=5, follow_redirects=True):
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    try:
        req = urllib.request.Request(url)
        req.add_header("Connection", "close")
        opener = _yes_redirect_opener if follow_redirects else _no_redirect_opener
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers)
    except Exception as e:
        return 0, str(e), {}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Raise HTTPError instead of following 3xx redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

_no_redirect_opener  = urllib.request.build_opener(_NoRedirect())
_yes_redirect_opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())

def http_post(path, body="", content_type="application/x-www-form-urlencoded",
              timeout=5, follow_redirects=False):
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    data = body.encode() if isinstance(body, str) else body
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Connection", "close")
        req.add_header("Content-Type", content_type)
        req.add_header("Content-Length", str(len(data)))
        opener = _yes_redirect_opener if follow_redirects else _no_redirect_opener
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers)
    except Exception as e:
        return 0, str(e), {}

# ── UDP helpers ───────────────────────────────────────────────────────────────

def udp_header(cmd, epoch=None):
    """Build 8-byte UDP header: magic(2) + version(1) + cmd(1) + epoch(4)."""
    if epoch is None:
        epoch = int(time.time()) & 0xFFFFFFFF
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd, epoch)

def udp_send_recv(pkt, timeout=2.0, recv_size=256):
    """Send a UDP packet and wait for a reply. Returns bytes or None.

    The child always replies to (sender_ip, UDP_PORT=4210) — the same port it
    listens on — so we must bind our socket to that port to receive responses.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    sock.bind(("", UDP_PORT))
    try:
        sock.sendto(pkt, (HOST, UDP_PORT))
        data, _ = sock.recvfrom(recv_size)
        return data
    except socket.timeout:
        return None
    finally:
        sock.close()

def udp_send(pkt):
    """Fire-and-forget UDP send (no reply expected)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(pkt, (HOST, UDP_PORT))
    finally:
        sock.close()

def parse_header(data):
    """Parse 8-byte UDP header. Returns (magic, version, cmd, epoch) or None."""
    if len(data) < 8:
        return None
    return struct.unpack("<HBBI", data[:8])

def parse_pong(data):
    """Parse CMD_PONG packet. Returns dict or None.

    PONG payload (131 bytes after 8-byte header = 139 total):
      hostname[10], altName[16], desc[32], stringCount(1), PongStrings×8
      Each PongString: <HHBBHB> = ledCount(2)+lengthMm(2)+ledType(1)+cableDir(1)+cableMm(2)+stripDir(1) = 9 bytes
      8 × 9 = 72  →  10+16+32+1+72 = 131
    """
    if len(data) < 8 + 131:
        return None
    payload = data[8:]
    hostname    = payload[0:10].rstrip(b'\x00').decode("ascii", errors="replace")
    alt_name    = payload[10:26].rstrip(b'\x00').decode("ascii", errors="replace")
    description = payload[26:58].rstrip(b'\x00').decode("ascii", errors="replace")
    string_count = payload[58]
    strings = []
    offset = 59
    for _ in range(8):
        led_count, length_mm, led_type, cable_dir, cable_mm, strip_dir = \
            struct.unpack_from("<HHBBHB", payload, offset)
        strings.append({
            "ledCount": led_count, "lengthMm": length_mm,
            "ledType": led_type, "cableDir": cable_dir,
            "cableMm": cable_mm, "stripDir": strip_dir,
        })
        offset += 9
    return {
        "hostname": hostname, "altName": alt_name,
        "description": description, "stringCount": string_count,
        "strings": strings,
    }

def parse_status_resp(data):
    """Parse CMD_STATUS_RESP packet (8-byte payload). Returns dict or None."""
    if len(data) < 8 + 8:
        return None
    payload = data[8:]
    active_action, runner_active, current_step, wifi_rssi, uptime_s = \
        struct.unpack_from("<BBBBI", payload, 0)
    # wifiRssi is stored as uint8 absolute magnitude (e.g. 69 means -69 dBm)
    return {
        "activeAction": active_action,
        "runnerActive": bool(runner_active),
        "currentStep": current_step,
        "wifiRssi": wifi_rssi,
        "uptimeS": uptime_s,
    }

def build_action_pkt(act_type=ACT_SOLID, r=255, g=0, b=0,
                     on_ms=500, off_ms=500, wipe_dir=0, wipe_spd=50,
                     led_start=None, led_end=None):
    """Build CMD_ACTION packet. led_start/led_end are 8-byte string+LED selectors."""
    if led_start is None:
        led_start = [0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    if led_end is None:
        led_end   = [7, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    hdr = udp_header(CMD_ACTION)
    payload = struct.pack("<BBBBHHBB",
        act_type, r, g, b, on_ms, off_ms, wipe_dir, wipe_spd)
    payload += bytes(led_start) + bytes(led_end)
    return hdr + payload

def build_load_step_pkt(step_index=0, total_steps=1,
                        act_type=ACT_SOLID, r=0, g=255, b=0,
                        on_ms=500, off_ms=500, wipe_dir=0, wipe_spd=50,
                        duration_s=5, led_start=None, led_end=None):
    """Build CMD_LOAD_STEP packet (header + 30-byte payload)."""
    if led_start is None:
        led_start = [0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    if led_end is None:
        led_end   = [7, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    hdr = udp_header(CMD_LOAD_STEP)
    payload = struct.pack("<BBBBBBHHBBH",
        step_index, total_steps,
        act_type, r, g, b,
        on_ms, off_ms,
        wipe_dir, wipe_spd,
        duration_s)
    payload += bytes(led_start) + bytes(led_end)
    return hdr + payload

# ── Wait for boot ─────────────────────────────────────────────────────────────

def wait_for_boot(max_wait=30):
    """Poll GET / until the child responds (up to max_wait seconds)."""
    print(f"Waiting for child at {HOST}:{HTTP_PORT} (up to {max_wait}s)...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        code, _, _ = http_get("/status", timeout=2)
        if code == 200:
            print("  Child is up.")
            return True
        time.sleep(1)
    print("  Timed out waiting for child.")
    return False

# ── Tests ─────────────────────────────────────────────────────────────────────

def test_http_basic():
    section("HTTP basic routes")

    # GET / — child root serves config page directly
    code, body, _ = http_get("/", follow_redirects=False)
    check("GET / returns 200 (serves config page)", code == 200, f"got {code}")
    check("GET / returns HTML config page", "<form" in body.lower(), body[:120])

    # GET /status
    code, body, hdrs = http_get("/status")
    check("GET /status returns 200", code == 200, f"got {code}")
    check("GET /status contains role:child", '"role":"child"' in body or '"role": "child"' in body,
          body[:120])
    check("GET /status contains hostname", '"hostname"' in body, body[:120])
    check("GET /status contains action", '"action"' in body, body[:120])

    # GET /config
    code, body, _ = http_get("/config")
    check("GET /config returns 200", code == 200, f"got {code}")
    check("GET /config is HTML form", "<form" in body.lower(), body[:120])
    # Form field names: altName→'an', stringCount→'sc', ledCount→'lc0'..'lc3'
    check("GET /config has altName field (name='an')", "name='an'" in body, body[:200])
    check("GET /config has stringCount field (name='sc')", "name='sc'" in body, body[:200])
    check("GET /config has ledCount field (name='lc0')", "name='lc0'" in body, body[:200])

    # GET /favicon.ico
    code, _, _ = http_get("/favicon.ico")
    check("GET /favicon.ico returns 404", code == 404, f"got {code}")


def test_http_config_post():
    section("HTTP POST /config")

    # Post a minimal config update (field names match the form: an, desc, sc, lc/lm/lt/cd/cm/sd)
    form_data = urllib.parse.urlencode({
        "an":   "TestNode",
        "desc": "Automated test",
        "sc":   "1",
        "lc0":  "8",
        "lm0":  "1000",
        "lt0":  "0",
        "cd0":  "0",
        "cm0":  "100",
        "sd0":  "0",
    })
    code, body, hdrs = http_post("/config", form_data)
    # Expect 303 redirect back to /config
    check("POST /config returns 303", code == 303, f"got {code}")
    location = hdrs.get("Location", "")
    check("POST /config redirects to /config", "/config" in location,
          f"Location: {location}")

    # Verify the change persisted (values appear as input value attributes)
    time.sleep(0.5)
    code, body, _ = http_get("/config")
    check("GET /config after POST shows updated altName",
          "TestNode" in body, body[:400])
    check("GET /config after POST shows updated desc",
          "Automated test" in body, body[:400])


def test_udp_ping_pong():
    section("UDP CMD_PING / CMD_PONG")

    pkt = udp_header(CMD_PING)
    resp = udp_send_recv(pkt, timeout=3.0)
    check("CMD_PING receives response", resp is not None,
          "no response within 3s")
    if resp is None:
        return

    hdr = parse_header(resp)
    check("PONG magic = 0x534C", hdr[0] == UDP_MAGIC, f"got 0x{hdr[0]:04X}")
    check("PONG version = 2", hdr[1] == UDP_VERSION, f"got {hdr[1]}")
    check("PONG cmd = 0x02", hdr[2] == CMD_PONG, f"got 0x{hdr[2]:02X}")
    check("PONG packet length >= 139 bytes", len(resp) >= 139,
          f"got {len(resp)} bytes")

    pong = parse_pong(resp)
    check("PONG payload parses", pong is not None)
    if pong is None:
        return

    hostname = pong["hostname"]
    check("PONG hostname starts with SLYC-", hostname.startswith("SLYC-"),
          f"got '{hostname}'")
    check("PONG hostname is 9 chars (SLYC-XXXX)", len(hostname) == 9,
          f"got '{hostname}' len={len(hostname)}")
    check("PONG stringCount >= 1", pong["stringCount"] >= 1,
          f"got {pong['stringCount']}")
    check("PONG strings[0].ledCount > 0", pong["strings"][0]["ledCount"] > 0,
          f"got {pong['strings'][0]['ledCount']}")


def test_udp_action():
    section("UDP CMD_ACTION (solid red)")

    pkt = build_action_pkt(ACT_SOLID, r=255, g=0, b=0)
    udp_send(pkt)
    time.sleep(0.3)

    # Verify via CMD_STATUS_REQ
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    check("CMD_STATUS_REQ after action gets response", resp is not None,
          "no response")
    if resp is None:
        return

    hdr = parse_header(resp)
    check("STATUS_RESP cmd = 0x41", hdr[2] == CMD_STATUS_RESP,
          f"got 0x{hdr[2]:02X}")
    status = parse_status_resp(resp)
    check("STATUS_RESP parses", status is not None)
    if status is None:
        return
    check("activeAction = ACT_SOLID (1)", status["activeAction"] == ACT_SOLID,
          f"got {status['activeAction']}")
    check("runnerActive = False after action", not status["runnerActive"],
          f"got {status['runnerActive']}")

    # Test ACTION_STOP
    udp_send(udp_header(CMD_ACTION_STOP))
    time.sleep(0.2)

    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("activeAction = ACT_OFF after STOP",
          status is not None and status["activeAction"] == ACT_OFF,
          f"got {status}")


def test_udp_action_types():
    section("UDP CMD_ACTION variants (flash, wipe)")

    # Flash
    pkt = build_action_pkt(ACT_FLASH, r=0, g=0, b=255, on_ms=200, off_ms=200)
    udp_send(pkt)
    time.sleep(0.3)
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("ACT_FLASH accepted",
          status is not None and status["activeAction"] == ACT_FLASH,
          f"got {status}")

    # Wipe
    pkt = build_action_pkt(ACT_WIPE, r=255, g=255, b=0, wipe_dir=0, wipe_spd=50)
    udp_send(pkt)
    time.sleep(0.3)
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("ACT_WIPE accepted",
          status is not None and status["activeAction"] == ACT_WIPE,
          f"got {status}")

    # Off
    pkt = build_action_pkt(ACT_OFF)
    udp_send(pkt)
    time.sleep(0.2)
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("ACT_OFF accepted",
          status is not None and status["activeAction"] == ACT_OFF,
          f"got {status}")


def test_udp_runner():
    section("UDP runner load / start / stop")

    # Load 2 steps
    step0 = build_load_step_pkt(step_index=0, total_steps=2,
                                act_type=ACT_SOLID, r=255, g=0, b=0,
                                duration_s=3)
    step1 = build_load_step_pkt(step_index=1, total_steps=2,
                                act_type=ACT_SOLID, r=0, g=255, b=0,
                                duration_s=3)

    # Load step 0
    resp0 = udp_send_recv(step0, timeout=2.0)
    check("LOAD_STEP 0 gets CMD_LOAD_ACK", resp0 is not None, "no response")
    if resp0:
        hdr = parse_header(resp0)
        check("ACK cmd = 0x21", hdr[2] == CMD_LOAD_ACK, f"got 0x{hdr[2]:02X}")
        ack_idx = resp0[8] if len(resp0) > 8 else -1
        check("ACK step index = 0", ack_idx == 0, f"got {ack_idx}")

    # Load step 1
    resp1 = udp_send_recv(step1, timeout=2.0)
    check("LOAD_STEP 1 gets CMD_LOAD_ACK", resp1 is not None, "no response")
    if resp1:
        ack_idx = resp1[8] if len(resp1) > 8 else -1
        check("ACK step index = 1", ack_idx == 1, f"got {ack_idx}")

    # Read child's current epoch from STATUS_RESP header (hdr.epoch = currentEpoch()
    # on the child).  This is correct whether NTP has synced or not.
    _sr = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    _child_epoch = parse_header(_sr)[3] if _sr else int(time.time())
    start_epoch = _child_epoch + 2
    go_pkt = udp_header(CMD_RUNNER_GO) + struct.pack("<I", start_epoch)
    udp_send(go_pkt)

    # Wait for runner to start
    time.sleep(2.5)
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("runnerActive = True after RUNNER_GO",
          status is not None and status["runnerActive"],
          f"got {status}")

    # Stop runner
    udp_send(udp_header(CMD_RUNNER_STOP))
    time.sleep(0.3)
    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    status = parse_status_resp(resp) if resp else None
    check("runnerActive = False after RUNNER_STOP",
          status is not None and not status["runnerActive"],
          f"got {status}")


def test_udp_status_req():
    section("UDP CMD_STATUS_REQ")

    resp = udp_send_recv(udp_header(CMD_STATUS_REQ), timeout=2.0)
    check("CMD_STATUS_REQ receives response", resp is not None, "no response")
    if resp is None:
        return

    check("STATUS_RESP length = 16 bytes (8+8)", len(resp) == 16,
          f"got {len(resp)}")
    status = parse_status_resp(resp)
    check("STATUS_RESP parses without error", status is not None)
    if status is None:
        return
    check("activeAction is 0–3",
          0 <= status["activeAction"] <= 3,
          f"got {status['activeAction']}")
    check("uptimeS > 0", status["uptimeS"] > 0, f"got {status['uptimeS']}")
    check("wifiRssi > 0 (absolute dBm magnitude, e.g. 69 means -69 dBm)",
          status["wifiRssi"] > 0,
          f"got {status['wifiRssi']}")


def factory_reset():
    """POST /config/reset and wait for the child to reboot/re-announce."""
    http_post("/config/reset", follow_redirects=False)
    time.sleep(1.5)


def test_http_config_strings():
    section(f"HTTP string config (max={CHILD_MAX_STRINGS})")

    # ── Start from a known state ──────────────────────────────────────────────
    factory_reset()

    # ── 1 string ─────────────────────────────────────────────────────────────
    form1 = urllib.parse.urlencode({
        "an":  "TestNode",
        "desc": "1-string test",
        "sc":  "1",
        "lc0": "30",
        "lm0": "600",
        "lt0": "0",   # WS2812B
        "sd0": "0",
    })
    code, _, hdrs = http_post("/config", form1)
    check("1-string POST /config returns 303", code == 303, f"got {code}")
    time.sleep(0.5)

    # Verify via PONG
    resp = udp_send_recv(udp_header(CMD_PING), timeout=3.0)
    check("1-string: PONG received", resp is not None, "no response")
    if resp is not None:
        pong = parse_pong(resp)
        check("1-string: PONG parses", pong is not None)
        if pong:
            check("1-string: stringCount = 1", pong["stringCount"] == 1,
                  f"got {pong['stringCount']}")
            check("1-string: strings[0].ledCount = 30",
                  pong["strings"][0]["ledCount"] == 30,
                  f"got {pong['strings'][0]['ledCount']}")
            check("1-string: strings[0].lengthMm = 600",
                  pong["strings"][0]["lengthMm"] == 600,
                  f"got {pong['strings'][0]['lengthMm']}")
            check("1-string: strings[0].ledType = 0 (WS2812B)",
                  pong["strings"][0]["ledType"] == 0,
                  f"got {pong['strings'][0]['ledType']}")

    # ── 2 strings (supported by all boards D1+ESP32) ─────────────────────────
    form2_fields = {
        "an":  "TestNode2",
        "desc": "2-string test",
        "sc":  "2",
        "lc0": "15",
        "lm0": "300",
        "lt0": "0",
        "sd0": "1",
        "lc1": "45",
        "lm1": "900",
        "lt1": "1",   # WS2811
        "sd1": "0",
    }
    form2 = urllib.parse.urlencode(form2_fields)
    code, _, _ = http_post("/config", form2)
    check("2-string POST /config returns 303", code == 303, f"got {code}")
    time.sleep(0.5)

    resp = udp_send_recv(udp_header(CMD_PING), timeout=3.0)
    check("2-string: PONG received", resp is not None, "no response")
    if resp is not None:
        pong = parse_pong(resp)
        check("2-string: PONG parses", pong is not None)
        if pong:
            check("2-string: stringCount = 2", pong["stringCount"] == 2,
                  f"got {pong['stringCount']}")
            check("2-string: strings[0].ledCount = 15",
                  pong["strings"][0]["ledCount"] == 15,
                  f"got {pong['strings'][0]['ledCount']}")
            check("2-string: strings[1].ledCount = 45",
                  pong["strings"][1]["ledCount"] == 45,
                  f"got {pong['strings'][1]['ledCount']}")
            check("2-string: strings[1].lengthMm = 900",
                  pong["strings"][1]["lengthMm"] == 900,
                  f"got {pong['strings'][1]['lengthMm']}")
            check("2-string: strings[1].ledType = 1 (WS2811)",
                  pong["strings"][1]["ledType"] == 1,
                  f"got {pong['strings'][1]['ledType']}")

    # ── Over-limit clamp ──────────────────────────────────────────────────────
    over_fields = {"an": "TestClamp", "desc": "clamp test", "sc": "99"}
    for i in range(10):
        over_fields[f"lc{i}"] = "10"
        over_fields[f"lm{i}"] = "200"
        over_fields[f"lt{i}"] = "0"
        over_fields[f"sd{i}"] = "0"
    code, _, _ = http_post("/config", urllib.parse.urlencode(over_fields))
    check("clamp: POST sc=99 returns 303", code == 303, f"got {code}")
    time.sleep(0.5)

    resp = udp_send_recv(udp_header(CMD_PING), timeout=3.0)
    check("clamp: PONG received", resp is not None, "no response")
    if resp is not None:
        pong = parse_pong(resp)
        if pong:
            check(f"clamp: stringCount clamped to {CHILD_MAX_STRINGS}",
                  pong["stringCount"] == CHILD_MAX_STRINGS,
                  f"got {pong['stringCount']}")

    # ── Teardown: restore factory state ──────────────────────────────────────
    factory_reset()
    time.sleep(0.5)
    resp = udp_send_recv(udp_header(CMD_PING), timeout=3.0)
    check("teardown: factory reset PONG received", resp is not None, "no response")
    if resp is not None:
        pong = parse_pong(resp)
        if pong:
            check("teardown: stringCount reset to 1", pong["stringCount"] == 1,
                  f"got {pong['stringCount']}")


def test_http_config_spa():
    """Verify rendered HTML content of each tab in the 3-tab config SPA."""

    # ── Setup: known config state ─────────────────────────────────────────────
    factory_reset()
    time.sleep(0.5)
    known = {
        "an":  "TabTest",
        "desc": "SPA tab test",
        "sc":  "1",
        "lc0": "24", "lm0": "480", "lt0": "1", "sd0": "2",  # WS2811, West
    }
    http_post("/config", urllib.parse.urlencode(known))
    time.sleep(0.5)

    code, html, _ = http_get("/config")
    check("SPA: GET /config returns 200", code == 200, f"got {code}")

    # Helper: extract text between two landmarks (returns "" if not found)
    def between(s, a, b):
        i = s.find(a)
        if i == -1:
            return ""
        j = s.find(b, i + len(a))
        return s[i:j] if j != -1 else s[i:]

    p0 = between(html, "id='p0'", "id='p1'")
    p1 = between(html, "id='p1'", "id='p2'")
    p2 = between(html, "id='p2'", "</form>")
    footer = between(html, "class='ftr'", "</div>")

    # ── Tab navigation ────────────────────────────────────────────────────────
    section("Config SPA — tab navigation")
    check("tab nav: 3 tab divs present",
          html.count("class='tab") >= 3)
    check("tab nav: Dashboard tab id='n0'",
          "id='n0'" in html)
    check("tab nav: Settings tab id='n1'",
          "id='n1'" in html)
    check("tab nav: Config tab id='n2'",
          "id='n2'" in html)
    check("tab nav: Dashboard label text",
          ">Dashboard<" in html)
    check("tab nav: Settings label text",
          ">Settings<" in html)
    check("tab nav: Config label text",
          ">Config<" in html)
    # Extract full opening tag for n0 (class attribute precedes id in the tag)
    n0_pos = html.find("id='n0'")
    n0_tag = html[html.rfind("<", 0, n0_pos) : html.find(">", n0_pos) + 1]
    check("tab nav: Dashboard is active on load (class tact)",
          "tact" in n0_tag)

    # ── Dashboard tab (p0) ────────────────────────────────────────────────────
    section("Config SPA — Dashboard tab")
    check("dashboard: pane id='p0' present", "id='p0'" in html)
    check("dashboard: shows hostname (SLYC-)",
          "SLYC-" in p0)
    check("dashboard: shows altName (TabTest)",
          "TabTest" in p0)
    check("dashboard: shows description (SPA tab test)",
          "SPA tab test" in p0)
    check("dashboard: shows string count",
          ">1<" in p0 or "'1'" in p0 or ">1 <" in p0)
    check("dashboard: action status element id='act' present",
          "id='act'" in p0)
    check("dashboard: no form input fields",
          "name='an'" not in p0 and "name='sc'" not in p0 and "name='lc0'" not in p0)
    check("dashboard: no Factory Reset button",
          "Factory Reset" not in p0)

    # ── Settings tab (p1) ────────────────────────────────────────────────────
    section("Config SPA — Settings tab")
    check("settings: pane id='p1' present", "id='p1'" in html)
    check("settings: name input (name='an') present", "name='an'" in p1)
    check("settings: name input value is TabTest",
          "value='TabTest'" in p1)
    check("settings: desc input (name='desc') present", "name='desc'" in p1)
    check("settings: desc input value is correct",
          "SPA tab test" in p1)
    check("settings: string count select (name='sc') present", "name='sc'" in p1)
    check("settings: string count option 1 is selected",
          "value='1' selected" in p1)
    check("settings: Save Settings button present",
          "Save Settings" in p1)
    check("settings: Factory Reset button present in settings pane",
          "Factory Reset" in p1)
    check("settings: no per-string fields (lc0 belongs in Config)",
          "name='lc0'" not in p1)

    # ── Config tab (p2) ───────────────────────────────────────────────────────
    section("Config SPA — Config tab")
    check("config: pane id='p2' present", "id='p2'" in html)
    check("config: string selector id='ss' present", "id='ss'" in p2)
    check("config: ss has option for String 1",
          "String 1" in p2)
    check("config: lc0 input present", "name='lc0'" in p2)
    check("config: lc0 value is 24",
          "name='lc0'" in p2 and "value='24'" in p2)
    check("config: lm0 value is 480",
          "value='480'" in p2)
    check("config: lt0 WS2811 option is selected",
          "value='1' selected" in p2)
    check("config: sd0 West option is selected",
          "value='2' selected" in p2)
    check("config: Save Config button present",
          "Save Config" in p2)
    check("config: no Factory Reset in config pane",
          "Factory Reset" not in p2)

    # ── Factory Reset placement ───────────────────────────────────────────────
    section("Config SPA — Factory Reset placement")
    check("factory reset: form id='rf' present",
          "id='rf'" in html)
    check("factory reset: form#rf action is /config/reset",
          "action='/config/reset'" in html)
    check("factory reset: button present in Settings pane only",
          "Factory Reset" in p1)
    check("factory reset: button NOT in footer",
          "Factory Reset" not in footer)
    check("factory reset: button NOT in Config pane",
          "Factory Reset" not in p2)
    check("factory reset: button NOT in Dashboard pane",
          "Factory Reset" not in p0)

    # ── Form structure ────────────────────────────────────────────────────────
    section("Config SPA — form structure")
    check("forms: main form id='cf' with action=/config",
          "id='cf'" in html and "action='/config'" in html)
    check("forms: reset form id='rf' with action=/config/reset",
          "id='rf'" in html and "action='/config/reset'" in html)
    check("forms: no nested forms (rf is outside cf)",
          html.find("id='rf'") > html.find("</form>"))
    check("forms: Settings and Config share one form (cf wraps p1 and p2)",
          html.find("id='cf'") < html.find("id='p1'") and
          html.find("id='p2'") < html.find("</form>"))

    # ── Footer ────────────────────────────────────────────────────────────────
    section("Config SPA — footer")
    check("footer: version string present (v)",
          "v" in footer and "." in footer)
    check("footer: no Factory Reset in footer",
          "Factory Reset" not in footer)

    # ── Teardown ──────────────────────────────────────────────────────────────
    factory_reset()


def test_http_javascript():
    """Verify the config page JS is intact — no sendBuf truncation artifacts."""
    code, html, _ = http_get("/config")
    check("js: GET /config returns 200", code == 200, f"got {code}")

    # Extract script block
    script_start = html.find("<script>")
    script_end   = html.find("</script>", script_start)
    check("js: <script> block present", script_start != -1 and script_end != -1,
          "no <script>...</script> block")
    if script_start == -1 or script_end == -1:
        return
    js = html[script_start + len("<script>") : script_end]

    # All four functions must be declared at the top level
    section("Config SPA — JavaScript integrity")
    check("js: showTab() defined",   "function showTab("  in js)
    check("js: showStr() defined",   "function showStr("  in js)
    check("js: scChg() defined",     "function scChg("    in js)
    check("js: poll() defined",      "function poll("     in js)

    # poll() must not appear inside another function's body
    # (the truncation bug embeds poll inside the open scChg for-loop)
    scchg_start = js.find("function scChg(")
    poll_start  = js.find("function poll(")
    check("js: poll() not nested inside scChg() (truncation check)",
          poll_start > scchg_start and
          js[scchg_start:poll_start].count("}") >= js[scchg_start:poll_start].count("{"),
          f"scChg at {scchg_start}, poll at {poll_start}")

    # Brace balance across the whole script
    check("js: balanced braces in script block",
          js.count("{") == js.count("}"),
          f"open={js.count('{')}, close={js.count('}')}")

    # Initialisation calls present after functions
    check("js: showTab(0) init call present", "showTab(0)" in js)
    check("js: showStr(0) init call present", "showStr(0)" in js)
    check("js: setInterval(poll,...) present",
          "setInterval(poll," in js or "setInterval(poll," in js)


def test_invalid_udp():
    section("UDP invalid / malformed packets")

    # Wrong magic
    bad_magic = struct.pack("<HBBI", 0xDEAD, 2, CMD_PING, int(time.time()))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.sendto(bad_magic, (HOST, UDP_PORT))
        resp = sock.recvfrom(256)
        check("Wrong magic: no response (or ignored)", False,
              f"unexpectedly got: {resp[0].hex()}")
    except socket.timeout:
        check("Wrong magic: silently ignored", True)
    finally:
        sock.close()

    # Short packet (truncated header)
    short = struct.pack("<HB", 0x534C, 2)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.sendto(short, (HOST, UDP_PORT))
        resp = sock.recvfrom(256)
        check("Short packet: no response", False,
              f"unexpectedly got: {resp[0].hex()}")
    except socket.timeout:
        check("Short packet: silently ignored", True)
    finally:
        sock.close()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"SlyLED child test suite")
    print(f"Target: {HOST}  HTTP:{HTTP_PORT}  UDP:{UDP_PORT}")
    print(f"{'=' * 64}")

    if not wait_for_boot(max_wait=30):
        print("ABORT: child not reachable")
        sys.exit(2)

    test_http_basic()
    test_http_javascript()
    test_http_config_post()
    test_udp_ping_pong()
    test_udp_status_req()
    test_udp_action()
    test_udp_action_types()
    test_udp_runner()
    test_invalid_udp()
    test_http_config_strings()
    test_http_config_spa()

    # Ensure child is left in a clean factory state
    factory_reset()

    summary()
