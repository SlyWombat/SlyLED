#!/usr/bin/env python3
"""
test_child.py — Automated tests for the SlyLED child firmware (ESP32 / D1 Mini).

Usage:
    python tests/test_child.py [host] [http_port] [udp_port]

If host is omitted, the script broadcasts a UDP PING and auto-discovers the
first child on the network.

Defaults: http_port=80  udp_port=4210

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

_arg_host = sys.argv[1] if len(sys.argv) > 1 else None
HTTP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 80
UDP_PORT  = int(sys.argv[3]) if len(sys.argv) > 3 else 4210

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
            if len(_data) >= 103:
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

def http_get(path, timeout=5):
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    try:
        req = urllib.request.Request(url)
        req.add_header("Connection", "close")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace"), {}
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
    """Parse CMD_PONG packet. Returns dict or None."""
    if len(data) < 8 + 95:
        return None
    payload = data[8:]
    hostname    = payload[0:10].rstrip(b'\x00').decode("ascii", errors="replace")
    alt_name    = payload[10:26].rstrip(b'\x00').decode("ascii", errors="replace")
    description = payload[26:58].rstrip(b'\x00').decode("ascii", errors="replace")
    string_count = payload[58]
    strings = []
    offset = 59
    for _ in range(4):
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
    if led_start is None:
        led_start = [0, 0xFF, 0xFF, 0xFF]
    if led_end is None:
        led_end = [7, 0xFF, 0xFF, 0xFF]
    hdr = udp_header(CMD_ACTION)
    payload = struct.pack("<BBBBHHBB",
        act_type, r, g, b, on_ms, off_ms, wipe_dir, wipe_spd)
    payload += bytes(led_start) + bytes(led_end)
    return hdr + payload

def build_load_step_pkt(step_index=0, total_steps=1,
                        act_type=ACT_SOLID, r=0, g=255, b=0,
                        on_ms=500, off_ms=500, wipe_dir=0, wipe_spd=50,
                        duration_s=5, led_start=None, led_end=None):
    """Build CMD_LOAD_STEP packet (header + 22-byte payload)."""
    if led_start is None:
        led_start = [0, 0xFF, 0xFF, 0xFF]
    if led_end is None:
        led_end = [7, 0xFF, 0xFF, 0xFF]
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

    # GET /
    code, body, hdrs = http_get("/")
    check("GET / returns 200", code == 200, f"got {code}")
    # Hostname is loaded via XHR from /status — not embedded in the static HTML.
    # Just confirm the page is valid HTML with the expected title.
    check("GET / body is SlyLED child page",
          "SlyLED" in body and "Child" in body, body[:80])
    # Child sendMain() does not include Content-Length (chunked response) — skip that check.

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
    check("PONG packet length >= 103 bytes", len(resp) >= 103,
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

    # Start runner 2 seconds from now
    start_epoch = int(time.time()) + 2
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
    test_http_config_post()
    test_udp_ping_pong()
    test_udp_status_req()
    test_udp_action()
    test_udp_action_types()
    test_udp_runner()
    test_invalid_udp()

    summary()
