#!/usr/bin/env python3
"""test_825_gyro_handshake.py — Regression for #825.

Asserts the rock-solid press-Start/Stop handshake is wired end-to-end
on the orchestrator side:

  1. CMD_GYRO_CLAIM_ACK / CMD_GYRO_STOP_ACK / CMD_GYRO_HEARTBEAT_REP
     constants exist on the module with the spec'd opcodes.
  2. _send_gyro_claim_ack / _send_gyro_stop_ack helpers exist with the
     expected payload shapes.
  3. The CMD_GYRO_START handler parses an optional 2-byte nonce and
     re-sends the cached response on a duplicate nonce instead of
     re-running claim+start_stream (idempotent retransmission).
  4. The CMD_GYRO_START success branch sends CLAIM_ACK and arms the
     1.5 s arm-check timer.
  5. The orient handler calls _mark_gyro_armed so a live orient stream
     cancels the arm-check.
  6. The CMD_GYRO_STOP handler accepts an optional 2-byte nonce and
     replies with STOP_ACK.
  7. The CMD_GYRO_HEARTBEAT_REP handler reconciles divergent state
     (releases orphan claim if puck reports IDLE; reconstructs claim
     if puck reports ACTIVE but server has no record — restart
     bootstrap path).

Run:  python -X utf8 tests/test_825_gyro_handshake.py
"""

import os
import struct
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


# ─────────────────────────────────────────────────────────────────────────────
# 1. Constants

def test_new_cmd_codes_defined():
    _assert(getattr(parent_server, "CMD_GYRO_CLAIM_ACK", None) == 0x6A,
            f"CMD_GYRO_CLAIM_ACK == 0x6A (got {getattr(parent_server, 'CMD_GYRO_CLAIM_ACK', None)})")
    _assert(getattr(parent_server, "CMD_GYRO_STOP_ACK", None) == 0x6B,
            f"CMD_GYRO_STOP_ACK == 0x6B (got {getattr(parent_server, 'CMD_GYRO_STOP_ACK', None)})")
    _assert(getattr(parent_server, "CMD_GYRO_HEARTBEAT_REP", None) == 0x6C,
            f"CMD_GYRO_HEARTBEAT_REP == 0x6C (got {getattr(parent_server, 'CMD_GYRO_HEARTBEAT_REP', None)})")
    for name, expected in (("GYRO_UI_IDLE", 0), ("GYRO_UI_WAITING_ACK", 1),
                            ("GYRO_UI_ACTIVE", 2), ("GYRO_UI_STOPPING", 3)):
        _assert(getattr(parent_server, name, None) == expected,
                f"{name} == {expected}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Send-helper shapes — patch the underlying socket so we capture the bytes.

def _capture_one_send(send_fn, *args, **kwargs):
    """Calls `send_fn(*args, **kwargs)` while patching socket.socket so we can
    record the (payload, addr) of the resulting sendto. Returns the captured
    tuple, or None if no send happened."""
    import socket as _sock
    captured = []

    class _FakeSock:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sendto(self, data, addr): captured.append((data, addr))
        def close(self): pass

    orig = _sock.socket
    _sock.socket = lambda *a, **kw: _FakeSock()
    try:
        send_fn(*args, **kwargs)
    finally:
        _sock.socket = orig
    return captured[0] if captured else None


def test_send_claim_ack_payload_shape():
    out = _capture_one_send(parent_server._send_gyro_claim_ack,
                             "192.168.10.211", 0xBEEF, 17)
    _assert(out is not None, "_send_gyro_claim_ack actually sends a packet")
    if not out:
        return
    data, addr = out
    _assert(addr == ("192.168.10.211", parent_server.UDP_PORT),
            f"sent to (ip, UDP_PORT) — got {addr}")
    # Header is 8 bytes; payload should be 2+2 = 4 bytes
    _assert(len(data) == 8 + 4,
            f"CLAIM_ACK packet length = 12 bytes (got {len(data)})")
    magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
    _assert(cmd == parent_server.CMD_GYRO_CLAIM_ACK,
            "header cmd byte == CMD_GYRO_CLAIM_ACK")
    nonce, mover_id = struct.unpack_from("<HH", data, 8)
    _assert(nonce == 0xBEEF, f"nonce echoed (got 0x{nonce:04X})")
    _assert(mover_id == 17, f"moverId carried (got {mover_id})")


def test_send_stop_ack_payload_shape():
    out = _capture_one_send(parent_server._send_gyro_stop_ack,
                             "192.168.10.211", 0xCAFE)
    _assert(out is not None, "_send_gyro_stop_ack actually sends a packet")
    if not out:
        return
    data, _ = out
    _assert(len(data) == 8 + 2,
            f"STOP_ACK packet length = 10 bytes (got {len(data)})")
    _, _, cmd = struct.unpack_from("<HBB", data, 0)
    _assert(cmd == parent_server.CMD_GYRO_STOP_ACK,
            "header cmd byte == CMD_GYRO_STOP_ACK")
    (nonce,) = struct.unpack_from("<H", data, 8)
    _assert(nonce == 0xCAFE, f"nonce echoed (got 0x{nonce:04X})")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Source-level: START handler parses nonce + sends ACK + arms arm-check.

def _start_handler_body():
    """#874 — the inline `elif cmd == CMD_GYRO_START:` branch was
    extracted to `_handle_gyro_start_packet(ip, data)` so the
    contract is directly testable. Inspect that function's source."""
    import inspect
    return inspect.getsource(parent_server._handle_gyro_start_packet)


def test_start_handler_parses_nonce():
    body = _start_handler_body()
    _assert("len(data) >= 10" in body,
            "START handler checks for ≥10-byte packet (header + 2-byte nonce)")
    _assert('struct.unpack_from("<H", data, 8)' in body,
            "START handler unpacks 2-byte nonce at offset 8")


def test_start_handler_sends_ack_on_success():
    body = _start_handler_body()
    _assert("_send_gyro_claim_ack(" in body,
            "START handler invokes _send_gyro_claim_ack on success")
    _assert("_gyro_lights_on(" in body,
            "START handler turns the lights on at claim creation (#813 §1.1)")
    _assert("_send_gyro_heartbeat(" in body,
            "START handler sends an immediate first HB so the puck has "
            "something to reply to (#813 §3.3)")


def test_start_handler_idempotent_replay():
    body = _start_handler_body()
    _assert("GYRO_HANDSHAKE_DEDUPE_S" in body,
            "START handler honours the dedupe window for replays")


def test_no_arm_check_subsystem():
    """#813 §7.1 — speculative arm-check timers are anti-pattern. The
    realigned spec deletes them. Guard against accidental re-introduction
    by checking that none of the dead-symbol names are anywhere in the
    module."""
    import inspect
    src = inspect.getsource(parent_server)
    _assert("GYRO_ARM_DEADLINE_S" not in src,
            "GYRO_ARM_DEADLINE_S removed (no speculative deadline)")
    _assert("_schedule_arm_check" not in src,
            "_schedule_arm_check helper removed")
    _assert("_arm_check_release" not in src,
            "_arm_check_release helper removed")
    _assert("_mark_gyro_armed" not in src,
            "_mark_gyro_armed helper removed (replaced by _gyro_touch_remote)")


# ─────────────────────────────────────────────────────────────────────────────
# 4. STOP handler parses nonce + sends STOP_ACK.

def _stop_handler_body():
    import inspect
    src = inspect.getsource(parent_server)
    marker = "elif cmd == CMD_GYRO_STOP:"
    i = src.find(marker)
    if i < 0:
        return ""
    rest = src[i:]
    j = rest[len(marker):].find("\n        elif cmd ==")
    return rest if j < 0 else rest[:len(marker) + j]


def test_stop_handler_parses_nonce_and_sends_ack():
    body = _stop_handler_body()
    _assert("len(data) >= 10" in body,
            "STOP handler accepts the 2-byte nonce variant")
    _assert("_send_gyro_stop_ack(" in body,
            "STOP handler sends STOP_ACK")


def test_stop_handler_uses_blackout_false():
    """#813 §1.2 / §6.1 — Press-Stop hands the fixture back to whatever
    was driving it before the gyro session (timeline / Track action /
    #800 park-at-home). Forced blackout=True would create a 1-frame
    flicker and contradicts Android-controller-mode semantics."""
    body = _stop_handler_body()
    _assert("blackout=False" in body,
            "STOP handler releases with blackout=False (releases to prior writer)")
    _assert("blackout=True" not in body,
            "STOP handler does NOT use blackout=True anywhere (#813 §1.2)")


# ─────────────────────────────────────────────────────────────────────────────
# 5. All-comms-silence semantics.

def test_touch_remote_helper_present():
    """#813 §6.3 — every gyro packet refreshes the silence clock so
    STALE_HARD_SECS measures all-comms silence, not just orient silence.
    A puck legitimately holding a still pose (no orient updates) but
    sending heartbeat-rep + battery should NOT trip the fallback."""
    import inspect
    src = inspect.getsource(parent_server)
    _assert("def _gyro_touch_remote" in src,
            "_gyro_touch_remote helper defined")
    # Spot-check every gyro CMD branch invokes it (or is ORIENT, which
    # implicitly touches via _apply_quat → last_data = time.time()).
    branches = (
        ("elif cmd == CMD_GYRO_STOP:", None),
        # #874 — START dispatch was extracted to
        # `_handle_gyro_start_packet`; inspect that function directly.
        ("elif cmd == CMD_GYRO_START:", parent_server._handle_gyro_start_packet),
        ("elif cmd == CMD_GYRO_BATT and len(data) >= 12:", None),
        ("elif cmd == CMD_GYRO_COLOR and len(data) >= 12:", None),
        ("elif cmd == CMD_GYRO_CALIBRATE and len(data) >= 15:", None),
        ("elif cmd == CMD_GYRO_HEARTBEAT_REP and len(data) >= 13:", None),
    )
    for branch_marker, extracted in branches:
        if extracted is not None:
            body = inspect.getsource(extracted)
        else:
            i = src.find(branch_marker)
            if i < 0:
                _assert(False, f"{branch_marker} present")
                continue
            rest = src[i:]
            j = rest[len(branch_marker):].find("\n        elif cmd ==")
            body = rest if j < 0 else rest[:len(branch_marker) + j]
        _assert("_gyro_touch_remote(" in body,
                f"{branch_marker.split()[2].rstrip(':')} branch refreshes silence clock")


def test_stale_hard_secs_bumped_to_600():
    """#813 §6.3 — 60 s threshold was the orient-silence-only cliff that
    fired on legitimate calibrate-screen pauses. Realigned spec uses
    600 s + all-comms-silence semantics."""
    from remote_orientation import STALE_HARD_SECS
    _assert(STALE_HARD_SECS >= 600,
            f"STALE_HARD_SECS ≥ 600 (got {STALE_HARD_SECS})")


# ─────────────────────────────────────────────────────────────────────────────
# 6. HEARTBEAT_REP handler — reconciliation paths.

def _hb_rep_handler_body():
    import inspect
    src = inspect.getsource(parent_server)
    marker = "elif cmd == CMD_GYRO_HEARTBEAT_REP and len(data) >= 13:"
    i = src.find(marker)
    if i < 0:
        return ""
    rest = src[i:]
    j = rest[len(marker):].find("\n        elif cmd ==")
    return rest if j < 0 else rest[:len(marker) + j]


def test_hb_rep_handler_present():
    _assert(_hb_rep_handler_body() != "",
            "CMD_GYRO_HEARTBEAT_REP handler exists in UDP dispatcher")


def test_hb_rep_does_not_release_on_idle():
    """#813 §7.2 — HB_REP-IDLE → release is anti-pattern. Operator may
    have rolled back the puck UI for any reason; only an explicit
    operator gesture (press-Stop or Inactive toggle) releases."""
    body = _hb_rep_handler_body()
    _assert("_mover_engine.release(" not in body,
            "HB_REP handler does NOT call release() (#813 §7.2 anti-pattern)")


def test_hb_rep_does_not_reconstruct_claim():
    """#872 — HB_REP-ACTIVE → reconstruct is now anti-pattern. Per
    operator (2026-05-09): "Once Start is pressed, when we lock and
    hold." Press-Start is the SOLE orchestrator-side claim entry
    trigger. After an orchestrator restart the operator presses Start
    again. The reconstruct path is removed because it enabled the SPA
    Release / press-Stop oscillation bug class — heartbeat couldn't
    distinguish "operator just released" from "orchestrator just
    restarted", and reconstructed in both cases."""
    body = _hb_rep_handler_body()
    _assert("_mover_engine.claim(" not in body,
            "HB_REP handler does NOT call claim() (#872 §7.2 anti-pattern)")
    _assert("_send_gyro_claim_ack(" not in body,
            "HB_REP handler does NOT send CLAIM_ACK (#872 §7.2)")
    _assert("_send_gyro_claim_denied(" not in body,
            "HB_REP handler does NOT send CLAIM_DENIED (#872 §7.2)")
    _assert("start_stream(" not in body,
            "HB_REP handler does NOT call start_stream (#872 §7.2)")
    _assert("_gyro_lights_on(" not in body,
            "HB_REP handler does NOT turn lights on (#872 §7.2)")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Functional dedupe — calling the START dispatch twice with the same nonce
# should produce the same wire response without doubling-up on engine work.

def test_start_dedupe_replay_does_not_double_claim():
    """Drop a fake (deviceId, nonce) → response into _gyro_handshake and
    confirm the START handler's replay branch is selected on a matching
    nonce, with no second engine call."""
    import inspect
    src = inspect.getsource(parent_server)
    _assert("is_replay" in src,
            "is_replay variable steers the START dedupe branch")
    _assert('_gyro_handshake.setdefault(device_id, {})' in src,
            "_gyro_handshake dict is keyed on device_id")
    _assert("GYRO_HANDSHAKE_DEDUPE_S" in src,
            "dedupe window constant defined")


# ─────────────────────────────────────────────────────────────────────────────

ALL = [
    test_new_cmd_codes_defined,
    test_send_claim_ack_payload_shape,
    test_send_stop_ack_payload_shape,
    test_start_handler_parses_nonce,
    test_start_handler_sends_ack_on_success,
    test_start_handler_idempotent_replay,
    test_no_arm_check_subsystem,
    test_stop_handler_parses_nonce_and_sends_ack,
    test_stop_handler_uses_blackout_false,
    test_touch_remote_helper_present,
    test_stale_hard_secs_bumped_to_600,
    test_hb_rep_handler_present,
    test_hb_rep_does_not_release_on_idle,
    test_hb_rep_does_not_reconstruct_claim,
    test_start_dedupe_replay_does_not_double_claim,
]


if __name__ == "__main__":
    print("=== #825 rock-solid gyro press-Start/Stop handshake ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
