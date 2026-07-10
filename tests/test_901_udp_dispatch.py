#!/usr/bin/env python3
"""test_901_udp_dispatch.py — #901 UDP dispatch-table refactor contract.

The ~400-line `elif cmd ==` chain in `_udp_listener` became a
`{cmd: (min_total_datagram_len, handler)}` table (`_UDP_DISPATCH`) with
module-level `_handle_<name>(ip, port, hdr, data)` functions, so every
handler is drivable without a socket and #910's 0x70 MMW_TARGETS is a
one-line registration.

Covered here:
  1. Table coverage — exactly the commands the pre-#901 chain handled,
     values cross-checked against main/Protocol.h.
  2. Min-length gates mirror the old `elif ... and len(data) >= N` gates.
  3. PONG handler records into _recent_pongs (discovery path).
  4. ACTION_EVENT handler records into _live_events.
  5. Unknown cmd / short datagram → dispatch miss (silent-ignore path).
  6. Gyro handshake case: GYRO_STOP with nonce sends STOP_ACK, and a
     same-nonce replay re-ACKs without a second release.
  7. #901 runtime-health counters exist and increment.

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_901_udp_dispatch.py
"""

import os
import re
import struct
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-901-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _hdr(cmd, ver=None):
    return struct.pack("<HBBI", ps.UDP_MAGIC, ver or ps.UDP_VERSION, cmd, 0)


# ── 1+2. Dispatch-table coverage + min-length gates ─────────────────────────

# Every command the pre-#901 elif chain handled, with the exact total-
# datagram length its `elif cmd == X [and len(data) >= N]` gate required
# (branches without a length clause implicitly required the 8-byte header).
EXPECTED = {
    "CMD_ACTION_EVENT": 12,
    "CMD_GYRO_ORIENT": 16,
    "CMD_GYRO_STOP": 8,
    "CMD_GYRO_OFF": 8,
    "CMD_GYRO_AIM_WIZARD": 8,
    "CMD_GYRO_START": 8,
    "CMD_GYRO_BATT": 12,
    "CMD_GYRO_COLOR": 12,
    "CMD_GYRO_CALIBRATE": 15,
    "CMD_GYRO_HEARTBEAT_REP": 13,
    "CMD_AUTOBRI_PUSH": 11,
    "CMD_PONG": 8,
}


def run_table_coverage():
    table = ps._UDP_DISPATCH
    expected_cmds = {getattr(ps, name): (name, mlen) for name, mlen in EXPECTED.items()}
    # #910 landed 0x70 MMW_TARGETS and #922 landed 0x51 OTA_STATUS as the
    # promised one-line registrations; MMW_TARGETS is checked separately
    # below (its wire truth is mmwave/MmwProtocol.h, not main/Protocol.h
    # like the pre-#901 set) and OTA_STATUS has its own contract suite
    # (tests/test_922_ota_status.py).
    post_901 = {ps.CMD_MMW_TARGETS, ps.CMD_OTA_STATUS}
    ok("dispatch table covers exactly the pre-#901 command set "
       "+ MMW_TARGETS + OTA_STATUS",
       set(table.keys()) == set(expected_cmds.keys()) | post_901,
       f"extra={sorted(set(table) - set(expected_cmds) - post_901)} "
       f"missing={sorted(set(expected_cmds) - set(table))}")
    for cmd, (name, mlen) in sorted(expected_cmds.items()):
        entry = table.get(cmd)
        ok(f"{name} min-length gate == {mlen}",
           entry is not None and entry[0] == mlen,
           f"got {entry}")
        ok(f"{name} handler is callable",
           entry is not None and callable(entry[1]))

    # Cross-check the wire values against main/Protocol.h so the table
    # can't silently drift from the firmware's command list.
    proto = os.path.join(os.path.dirname(__file__), "..", "main", "Protocol.h")
    src = open(proto, encoding="utf-8", errors="replace").read()
    fw = {m.group(1): int(m.group(2), 16)
          for m in re.finditer(r"constexpr uint8_t\s+(CMD_\w+)\s*=\s*0x([0-9A-Fa-f]{2})", src)}
    for name in EXPECTED:
        ok(f"{name} value matches main/Protocol.h",
           name in fw and getattr(ps, name) == fw[name],
           f"py={getattr(ps, name):#04x} fw={fw.get(name)}")

    # #910 — 0x70 MMW_TARGETS landed as the one-line addition this seam
    # reserved. Gate = 8-byte header + 28-byte MmwTargetsPayload; wire
    # value cross-checked against mmwave/MmwProtocol.h (its source of
    # truth — the 0x7x range is the radar node's, absent from main/).
    entry = table.get(ps.CMD_MMW_TARGETS)
    ok("0x70 MMW_TARGETS registered with 36-byte gate (#910)",
       ps.CMD_MMW_TARGETS == 0x70 and entry is not None and entry[0] == 36,
       f"got {entry}")
    ok("MMW_TARGETS handler is callable",
       entry is not None and callable(entry[1]))
    mmw_proto = os.path.join(os.path.dirname(__file__), "..",
                             "mmwave", "MmwProtocol.h")
    mmw_src = open(mmw_proto, encoding="utf-8", errors="replace").read()
    mmw_fw = {m.group(1): int(m.group(2), 16)
              for m in re.finditer(
                  r"constexpr uint8_t\s+(CMD_\w+)\s*=\s*0x([0-9A-Fa-f]{2})",
                  mmw_src)}
    ok("CMD_MMW_TARGETS value matches mmwave/MmwProtocol.h",
       mmw_fw.get("CMD_MMW_TARGETS") == ps.CMD_MMW_TARGETS,
       f"py={ps.CMD_MMW_TARGETS:#04x} fw={mmw_fw.get('CMD_MMW_TARGETS')}")
    # 0x71 MMW_CONFIG stays reserved/unimplemented in v1 (design doc §4.3).
    ok("0x71 MMW_CONFIG not dispatched (reserved, parent→node)",
       mmw_fw.get("CMD_MMW_CONFIG", 0x71) not in table)


# ── 3. PONG handler records discovery info ──────────────────────────────────

def _make_pong(hostname, alt, desc, sc=1, ver=5):
    payload = bytearray()
    payload += hostname.encode().ljust(10, b"\x00")
    payload += alt.encode().ljust(16, b"\x00")
    payload += desc.encode().ljust(32, b"\x00")
    payload += bytes([sc])
    for i in range(8):
        payload += struct.pack("<HHBBHB", 30 if i < sc else 0, 1000, 1, 0, 0, 0)
    payload += bytes([7, 5, 2])  # fwMajor, fwMinor, fwPatch → v5 142-byte shape
    return _hdr(ps.CMD_PONG, ver=ver) + bytes(payload)


def run_pong_recording():
    ip = "10.99.88.77"  # matches no child → no _probe_board_type HTTP call
    ps._recent_pongs.pop(ip, None)
    pkt = _make_pong("sly-test", "Test Node", "dispatch test")
    entry = ps._UDP_DISPATCH[ps.CMD_PONG]
    ok("PONG datagram passes its min-length gate", len(pkt) >= entry[0])
    entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_PONG), pkt)
    info = ps._recent_pongs.get(ip)
    ok("PONG recorded in _recent_pongs", info is not None)
    ok("PONG hostname parsed", info and info.get("hostname") == "sly-test",
       repr(info))
    ok("PONG fwVersion parsed (3-part v5 shape)",
       info and info.get("fwVersion") == "7.5.2", repr(info))
    ps._recent_pongs.pop(ip, None)


# ── 4. ACTION_EVENT handler records live events ─────────────────────────────

def run_action_event():
    ip = "10.99.88.66"
    ps._live_events.pop(ip, None)
    pkt = _hdr(ps.CMD_ACTION_EVENT) + struct.pack("<BBBB", 3, 2, 8, 0)
    entry = ps._UDP_DISPATCH[ps.CMD_ACTION_EVENT]
    entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_ACTION_EVENT), pkt)
    ev = ps._live_events.get(ip)
    ok("ACTION_EVENT recorded", ev is not None)
    ok("ACTION_EVENT fields parsed",
       ev and ev.get("actionType") == 3 and ev.get("stepIndex") == 2
       and ev.get("totalSteps") == 8 and ev.get("event") == 0, repr(ev))
    ps._live_events.pop(ip, None)


# ── 5. Unknown cmd / short datagram → dispatch miss ─────────────────────────

def run_dispatch_miss():
    ok("unknown cmd 0xEE has no table entry (silent-ignore path)",
       ps._UDP_DISPATCH.get(0xEE) is None)
    # A known cmd shorter than its gate must take the same miss path the
    # old chain's trailing `else` took (elif condition False → else).
    short = _hdr(ps.CMD_ACTION_EVENT) + b"\x01"  # 9 bytes < 12
    entry = ps._UDP_DISPATCH[ps.CMD_ACTION_EVENT]
    ok("short ACTION_EVENT fails the min-length gate (→ miss path)",
       len(short) < entry[0])


# ── 6. Gyro handshake: STOP nonce → STOP_ACK, replay dedupe ─────────────────

def run_gyro_stop_handshake():
    ip = "10.99.88.55"
    did = f"gyro-{ip}"
    acks = []
    releases = []

    orig_ack = ps._send_gyro_stop_ack
    orig_engine = ps._mover_engine

    class _FakeEngine:
        def release(self, mover_id, device_id, blackout=None):
            releases.append((mover_id, device_id, blackout))

    ps._send_gyro_stop_ack = lambda ip_, nonce: acks.append((ip_, nonce))
    ps._mover_engine = _FakeEngine()  # truthy; no gyro fixture → no release
    try:
        with ps._gyro_handshake_lock:
            ps._gyro_handshake.pop(did, None)
        pkt = _hdr(ps.CMD_GYRO_STOP) + struct.pack("<H", 0xBEEF)
        entry = ps._UDP_DISPATCH[ps.CMD_GYRO_STOP]
        entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_GYRO_STOP), pkt)
        ok("GYRO_STOP sends STOP_ACK", acks == [(ip, 0xBEEF)], repr(acks))
        with ps._gyro_handshake_lock:
            st = dict(ps._gyro_handshake.get(did) or {})
        ok("GYRO_STOP stamps stop_nonce", st.get("stop_nonce") == 0xBEEF, repr(st))
        ok("GYRO_STOP clears start_nonce", st.get("start_nonce") is None, repr(st))
        # Replay: same nonce within the dedupe window re-ACKs, no state churn.
        entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_GYRO_STOP), pkt)
        ok("GYRO_STOP replay re-sends ACK", acks == [(ip, 0xBEEF)] * 2, repr(acks))
        ok("no release fired (no gyro fixture for this ip)", releases == [],
           repr(releases))
        # Legacy header-only variant (≤ v1.2.6 firmware): nonce=None, no ACK.
        acks.clear()
        with ps._gyro_handshake_lock:
            ps._gyro_handshake.pop(did, None)
        entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_GYRO_STOP),
                 _hdr(ps.CMD_GYRO_STOP))
        ok("legacy header-only GYRO_STOP sends no ACK", acks == [], repr(acks))
    finally:
        ps._send_gyro_stop_ack = orig_ack
        ps._mover_engine = orig_engine
        with ps._gyro_handshake_lock:
            ps._gyro_handshake.pop(did, None)


# ── 7. Receive-loop failure counters (#901 runtime-health note) ─────────────

def run_recv_error_counters():
    status = ps.get_udp_listener_status()
    ok("recvErrors exposed in listener status", "recvErrors" in status,
       repr(sorted(status)))
    ok("autobriRecvErrors exposed in listener status",
       "autobriRecvErrors" in status, repr(sorted(status)))
    before = ps.get_udp_listener_status().get("recvErrors", 0)
    ps._udp_count_recv_error("recvErrors")
    after = ps.get_udp_listener_status().get("recvErrors", 0)
    ok("recvErrors counter increments", after == before + 1,
       f"{before} -> {after}")


def main():
    run_table_coverage()
    run_pong_recording()
    run_action_event()
    run_dispatch_miss()
    run_gyro_stop_handshake()
    run_recv_error_counters()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
