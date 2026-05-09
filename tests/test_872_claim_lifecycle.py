"""test_872_claim_lifecycle.py — gyro claim-lifecycle contract per #872.

Covers the two architectural changes:

  • Bug A: CMD_GYRO_CLAIM_DENIED carries a 1-byte reason code so the
    puck firmware can render an actionable message instead of the
    legacy single "Mover held by other" string. All four causes
    (controller-inactive, already-claimed, no-mover-assigned, engine-
    unavailable) map to distinct reason codes.

  • Bug F: HB_REP is diagnostics-only. No reconstruct, no implicit
    re-claim, no auto-release-on-IDLE. Press-Start is the SOLE
    orchestrator-side claim entry trigger.

Run: python3 -X utf8 tests/test_872_claim_lifecycle.py
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


# ── Bug A: CLAIM_DENIED reason byte + per-cause mapping ───────────────────────

def test_denied_reason_constants_exist():
    for name, val in [
        ("GYRO_DENIED_IDLE", 0),
        ("GYRO_DENIED_CONTROLLER_INACTIVE", 1),
        ("GYRO_DENIED_ALREADY_CLAIMED", 2),
        ("GYRO_DENIED_NO_MOVER_ASSIGNED", 3),
        ("GYRO_DENIED_ENGINE_UNAVAILABLE", 4),
    ]:
        actual = getattr(parent_server, name, None)
        _assert(actual == val,
                f"parent_server.{name} == {val} (got {actual})")


def test_send_denied_packet_carries_reason_byte():
    """`_send_gyro_claim_denied(ip, reason)` builds a 9-byte packet
    (8-byte header + 1-byte reason) and writes it via UDP. We snoop
    the socket to confirm the payload byte matches the requested
    reason."""
    sent = []
    real_socket = parent_server.socket.socket

    class FakeSocket:
        def __init__(self, *_a, **_k):
            pass

        def sendto(self, data, addr):
            sent.append((data, addr))

        def close(self):
            pass

    parent_server.socket.socket = FakeSocket
    try:
        for reason in (0, 1, 2, 3, 4):
            sent.clear()
            parent_server._send_gyro_claim_denied("192.168.1.1", reason)
            _assert(len(sent) == 1,
                    f"reason={reason} → exactly one UDP packet sent (got {len(sent)})")
            data, addr = sent[0]
            _assert(addr == ("192.168.1.1", parent_server.UDP_PORT),
                    f"reason={reason} → addr is gyro IP+UDP_PORT (got {addr})")
            _assert(len(data) == 9,
                    f"reason={reason} → packet length 9 bytes (got {len(data)})")
            _assert(data[8] == reason,
                    f"reason={reason} → payload[0] == {reason} (got {data[8]})")
    finally:
        parent_server.socket.socket = real_socket


def test_default_reason_is_idle_for_back_compat():
    """`_send_gyro_claim_denied(ip)` (no reason kwarg) defaults to
    GYRO_DENIED_IDLE so legacy call sites that haven't been updated
    still send a valid packet — the puck reads reason 0 as
    legacy/unspecified and renders the original 'Mover held by other'
    string."""
    sent = []

    class FakeSocket:
        def __init__(self, *_a, **_k): pass
        def sendto(self, data, addr): sent.append((data, addr))
        def close(self): pass

    real = parent_server.socket.socket
    parent_server.socket.socket = FakeSocket
    try:
        parent_server._send_gyro_claim_denied("192.168.1.1")
        _assert(len(sent) == 1, "exactly one packet sent on default call")
        _assert(sent[0][0][8] == parent_server.GYRO_DENIED_IDLE,
                f"default reason = GYRO_DENIED_IDLE (got {sent[0][0][8]})")
    finally:
        parent_server.socket.socket = real


def test_start_handler_emits_correct_reason_per_cause():
    """Inspect the CMD_GYRO_START dispatch source. Each refusal branch
    must call `_record_denied(<reason>)` with the matching constant."""
    src = inspect.getsource(parent_server)
    # Locate the GYRO_START handler block.
    start_idx = src.find("elif cmd == CMD_GYRO_START:")
    _assert(start_idx > 0, "CMD_GYRO_START dispatch found")
    # Grab a generous window — handler is ~120 lines.
    block = src[start_idx:start_idx + 6000]

    _assert("_record_denied(GYRO_DENIED_CONTROLLER_INACTIVE)" in block,
            "Inactive branch records reason CONTROLLER_INACTIVE")
    _assert("_record_denied(GYRO_DENIED_NO_MOVER_ASSIGNED)" in block,
            "no-assigned-mover branch records reason NO_MOVER_ASSIGNED")
    _assert("_record_denied(GYRO_DENIED_ENGINE_UNAVAILABLE)" in block,
            "engine-down branch records reason ENGINE_UNAVAILABLE")
    _assert("_record_denied(GYRO_DENIED_ALREADY_CLAIMED)" in block,
            "claim-busy branch records reason ALREADY_CLAIMED")


def test_dedupe_replays_original_reason():
    """Replays of a same-nonce START re-emit the cached reason so the
    puck UI renders the same message it did the first time. Operator
    must press Start with a fresh nonce to retry, even if the
    underlying cause has been resolved (#872 §3.6)."""
    src = inspect.getsource(parent_server)
    _assert('start_response_reason' in src,
            "_gyro_handshake caches `start_response_reason` for replay")
    _assert('_send_gyro_claim_denied(ip, prev_reason)' in src,
            "replay branch passes the cached reason to _send_gyro_claim_denied")


# ── Bug F: HB_REP is diagnostics-only ─────────────────────────────────────────

def _hb_rep_block():
    """Source slice of the CMD_GYRO_HEARTBEAT_REP handler."""
    src = inspect.getsource(parent_server)
    idx = src.find("elif cmd == CMD_GYRO_HEARTBEAT_REP")
    assert idx > 0, "CMD_GYRO_HEARTBEAT_REP dispatch not found"
    # ~80-line handler now.
    return src[idx:idx + 4000]


def test_hb_rep_does_not_call_claim():
    body = _hb_rep_block()
    _assert("_mover_engine.claim(" not in body,
            "HB_REP handler does NOT call _mover_engine.claim() — claim "
            "lifecycle is press-Start only")


def test_hb_rep_does_not_call_release():
    body = _hb_rep_block()
    _assert("_mover_engine.release(" not in body,
            "HB_REP handler does NOT call _mover_engine.release() — operator "
            "gestures are the sole release triggers")


def test_hb_rep_does_not_send_claim_packets():
    body = _hb_rep_block()
    _assert("_send_gyro_claim_ack(" not in body,
            "HB_REP handler does NOT send CLAIM_ACK — only press-Start does")
    _assert("_send_gyro_claim_denied(" not in body,
            "HB_REP handler does NOT send CLAIM_DENIED — only press-Start does")


def test_hb_rep_does_not_call_start_stream():
    body = _hb_rep_block()
    _assert("start_stream(" not in body,
            "HB_REP handler does NOT call start_stream — claim lifecycle "
            "side-effects belong to press-Start handler only")


def test_hb_rep_does_not_turn_lights_on():
    body = _hb_rep_block()
    _assert("_gyro_lights_on(" not in body,
            "HB_REP handler does NOT call _gyro_lights_on — lighting is a "
            "press-Start side effect, not a heartbeat side effect")


def test_hb_rep_still_touches_remote_for_silence_clock():
    """Diagnostics-only does NOT mean the handler is empty — it must
    still update Remote.last_data so the §6.3 600 s silence timer
    measures all-comms silence, not just orient silence."""
    body = _hb_rep_block()
    _assert("_gyro_touch_remote(" in body,
            "HB_REP handler keeps `_gyro_touch_remote` for the §6.3 silence "
            "timer (heartbeat counts as comms)")


# ── Bug B: gyro fixture PUT round-trip — assignedMoverId=None must persist ───

def test_gyro_fixture_unassign_round_trip():
    """The operator's #872 reproduction:
        1. Gyro fixture has `assignedMoverId=14`.
        2. SPA sends `PUT /api/fixtures/<fid> {assignedMoverId: null}`.
        3. GET /api/fixtures/<fid> must return `assignedMoverId == None`.
    Pre-#872 the operator reported the assignment surviving the unassign
    + Save round-trip. The PUT-loop semantics in `api_fixture_update`
    are `if k in body: f[k] = body[k]`, so a `null` literal in the body
    DOES land — but this regression test pins it so a future PATCH-
    style refactor can't silently drop the key."""
    app = parent_server.app
    fixtures = parent_server._fixtures
    # Snapshot original list to restore after the test.
    snapshot = list(fixtures)
    try:
        # Inject a synthetic gyro fixture directly so we don't depend
        # on POST validation or the `_save_fixtures` path.
        gf = {
            "id": 9999,
            "name": "Test Gyro #872",
            "fixtureType": "gyro",
            "type": "point",
            "gyroChildId": 99,
            "assignedMoverId": 14,
            "gyroEnabled": False,
            "smoothing": 0.15,
        }
        fixtures.append(gf)

        with app.test_client() as c:
            # Verify starting state.
            r = c.get(f"/api/fixtures/{gf['id']}")
            _assert(r.status_code == 200,
                    f"GET /api/fixtures/{gf['id']} 200 (got {r.status_code})")
            data = r.get_json()
            _assert(data.get("assignedMoverId") == 14,
                    f"starting assignedMoverId == 14 (got {data.get('assignedMoverId')})")

            # Operator's unassign action.
            r = c.put(f"/api/fixtures/{gf['id']}",
                      json={"assignedMoverId": None})
            _assert(r.status_code == 200,
                    f"PUT unassign 200 (got {r.status_code})")

            # Round-trip via GET.
            r = c.get(f"/api/fixtures/{gf['id']}")
            data = r.get_json()
            _assert(data.get("assignedMoverId") is None,
                    f"after PUT, assignedMoverId is None (got "
                    f"{data.get('assignedMoverId')!r})")

            # Re-assign to confirm the path works in the other direction.
            r = c.put(f"/api/fixtures/{gf['id']}",
                      json={"assignedMoverId": 14})
            r = c.get(f"/api/fixtures/{gf['id']}")
            data = r.get_json()
            _assert(data.get("assignedMoverId") == 14,
                    f"re-assign to 14 round-trips (got {data.get('assignedMoverId')})")
    finally:
        # Restore the fixture list.
        fixtures.clear()
        fixtures.extend(snapshot)


# ── Spec doc note exists (provenance) ─────────────────────────────────────────

def test_spec_doc_records_872_decision():
    """The `docs/gyro-claim-lifecycle.md` §7.2 explicitly states the
    reconstruct path is removed in #872 — so future code review can
    reject any patch that re-introduces it without first updating the
    spec."""
    spec_path = os.path.join(os.path.dirname(__file__), "..",
                              "docs", "gyro-claim-lifecycle.md")
    with open(spec_path, encoding="utf-8") as f:
        spec = f.read()
    _assert("#872" in spec,
            "gyro-claim-lifecycle.md references #872")
    _assert("press-Start is the sole orchestrator-side claim entry trigger"
            in spec.lower()
            or "press-start is the sole" in spec.lower(),
            "spec doc records the press-Start-as-sole-trigger contract")


# ── Test registry ─────────────────────────────────────────────────────────────

ALL = [
    test_denied_reason_constants_exist,
    test_send_denied_packet_carries_reason_byte,
    test_default_reason_is_idle_for_back_compat,
    test_start_handler_emits_correct_reason_per_cause,
    test_dedupe_replays_original_reason,
    test_hb_rep_does_not_call_claim,
    test_hb_rep_does_not_call_release,
    test_hb_rep_does_not_send_claim_packets,
    test_hb_rep_does_not_call_start_stream,
    test_hb_rep_does_not_turn_lights_on,
    test_hb_rep_still_touches_remote_for_silence_clock,
    test_gyro_fixture_unassign_round_trip,
    test_spec_doc_records_872_decision,
]


if __name__ == "__main__":
    print("=== #872 claim-lifecycle contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            import traceback
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            traceback.print_exc()
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
