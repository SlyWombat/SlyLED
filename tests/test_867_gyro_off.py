"""test_867_gyro_off.py — #867 OFF wire-cmd contract.

Mirrors `test_825_gyro_handshake.py` style: static-source inspection
on `parent_server.py` to pin the new CMD_GYRO_OFF (0x6E) constant, the
elif-branch existence, the nonce-parsing shape, the `release(blackout
=True)` call, and the ACK-via-CMD_GYRO_STOP_ACK path. Plus the
firmware Protocol.h pin and the CLAUDE.md UDP-table row.

The wire-protocol delta from STOP is only:
  - cmd byte 0x6E (vs STOP's 0x69)
  - server release call uses blackout=True (vs STOP's blackout=False)
Everything else (payload nonce shape, ACK reuse, dedupe window,
handshake-state slots) is shared with STOP, so the tests here are
deliberately narrow.

Run: python -X utf8 tests/test_867_gyro_off.py
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


def _off_handler_body():
    """Slice the OFF elif-branch out of parent_server.py source."""
    src = inspect.getsource(parent_server)
    marker = "elif cmd == CMD_GYRO_OFF:"
    i = src.find(marker)
    if i < 0:
        return ""
    rest = src[i:]
    j = rest[len(marker):].find("\n        elif cmd ==")
    return rest if j < 0 else rest[:len(marker) + j]


# ─────────────────────────────────────────────────────────────────────────────
# 1. CMD constant.

def test_cmd_gyro_off_constant():
    val = getattr(parent_server, "CMD_GYRO_OFF", None)
    _assert(val == 0x6E,
            f"parent_server.CMD_GYRO_OFF == 0x6E (got {val!r})")


# ─────────────────────────────────────────────────────────────────────────────
# 2. UDP dispatch elif-branch present.

def test_off_handler_branch_exists():
    body = _off_handler_body()
    _assert(len(body) > 0,
            "parent_server has `elif cmd == CMD_GYRO_OFF:` branch")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Nonce parsing shape (mirrors STOP — 2-byte little-endian after 8-byte hdr).

def test_off_handler_parses_nonce():
    body = _off_handler_body()
    _assert("len(data) >= 10" in body,
            "OFF handler accepts the 2-byte nonce variant (header+nonce >= 10)")
    _assert('struct.unpack_from("<H", data, 8)' in body,
            "OFF handler unpacks the nonce at offset 8 little-endian uint16")


# ─────────────────────────────────────────────────────────────────────────────
# 4. CRITICAL — server releases with blackout=True (the only behavioural
#    delta from STOP).

def test_off_handler_uses_blackout_true():
    body = _off_handler_body()
    _assert("blackout=True" in body,
            "OFF handler calls release with blackout=True (head goes dark)")
    _assert("blackout=False" not in body,
            "OFF handler does NOT use blackout=False — that's the STOP path")


# ─────────────────────────────────────────────────────────────────────────────
# 5. ACK path reuses CMD_GYRO_STOP_ACK (gyro only knows one ACK shape).

def test_off_handler_sends_stop_ack():
    body = _off_handler_body()
    _assert("_send_gyro_stop_ack(" in body,
            "OFF handler ACKs via _send_gyro_stop_ack (same ACK as STOP)")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Idempotent replay — re-emit on ACK loss must not double-release.

def test_off_handler_dedupe_replay():
    body = _off_handler_body()
    _assert("is_replay" in body,
            "OFF handler checks the replay-dedupe flag")
    _assert("GYRO_HANDSHAKE_DEDUPE_S" in body,
            "OFF handler uses the same dedupe window as STOP")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Handshake-state cleanup mirrors STOP: clears start_nonce + mover_id,
#    writes stop_nonce/stop_ack_ts so subsequent retries replay the ACK.

def test_off_handler_handshake_cleanup():
    body = _off_handler_body()
    _assert('st_hs["start_nonce"] = None' in body,
            "OFF handler clears the start_nonce handshake slot")
    _assert('st_hs["stop_nonce"]' in body,
            "OFF handler stamps stop_nonce so retries replay the ACK")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Firmware Protocol.h carries the matching wire constant.

def test_protocol_h_constant():
    proto = os.path.join(os.path.dirname(__file__), "..", "main", "Protocol.h")
    if not os.path.exists(proto):
        _assert(False, f"main/Protocol.h not found at {proto}")
        return
    src = open(proto).read()
    _assert("CMD_GYRO_OFF" in src and "0x6E" in src,
            "main/Protocol.h declares CMD_GYRO_OFF = 0x6E")


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLAUDE.md UDP-protocol table row present (so future contributors
#    don't add wire commands without a paired doc entry — same trap that
#    bit pre-#866 firmware-tab updates).

def test_claude_md_table_row():
    cmd = os.path.join(os.path.dirname(__file__), "..", "CLAUDE.md")
    if not os.path.exists(cmd):
        _assert(False, f"CLAUDE.md not found at {cmd}")
        return
    src = open(cmd).read()
    _assert("0x6E | GYRO_OFF" in src,
            "CLAUDE.md UDP table has a 0x6E GYRO_OFF row")
    _assert("blackout=True" in src,
            "CLAUDE.md row notes the blackout=True behaviour delta")


ALL = [
    test_cmd_gyro_off_constant,
    test_off_handler_branch_exists,
    test_off_handler_parses_nonce,
    test_off_handler_uses_blackout_true,
    test_off_handler_sends_stop_ack,
    test_off_handler_dedupe_replay,
    test_off_handler_handshake_cleanup,
    test_protocol_h_constant,
    test_claude_md_table_row,
]


if __name__ == "__main__":
    print("=== #867 GYRO_OFF wire contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
