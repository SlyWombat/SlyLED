"""test_872_claim_lifecycle.py — gyro claim-lifecycle contract per #872.

Covers the two architectural changes:

  • Bug A: CMD_GYRO_CLAIM_DENIED carries a 1-byte reason code so the
    gyro firmware can render an actionable message instead of the
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
    still send a valid packet — the gyro reads reason 0 as
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
    """Inspect the CMD_GYRO_START dispatch source (post-#874:
    `_handle_gyro_start_packet` top-level function). Each refusal
    branch must call `_record_denied(<reason>)` with the matching
    constant."""
    block = inspect.getsource(parent_server._handle_gyro_start_packet)
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
    gyro UI renders the same message it did the first time. Operator
    must press Start with a fresh nonce to retry, even if the
    underlying cause has been resolved (#872 §3.6)."""
    # #920 — the replay branch lives in _handle_gyro_start_packet;
    # inspect that function instead of the whole module source.
    src = inspect.getsource(parent_server._handle_gyro_start_packet)
    _assert('start_response_reason' in src,
            "_gyro_handshake caches `start_response_reason` for replay")
    _assert('_send_gyro_claim_denied(ip, prev_reason)' in src,
            "replay branch passes the cached reason to _send_gyro_claim_denied")


# ── Bug F: HB_REP is diagnostics-only ─────────────────────────────────────────

def _hb_rep_block():
    """Source of the CMD_GYRO_HEARTBEAT_REP handler. #920 — post-#901
    the handler is the module-level `_handle_gyro_hb_rep`; inspect it
    directly (same pattern as `_handle_gyro_start_packet` above) instead
    of slicing a fixed 4000-char window off a docstring marker."""
    handler = getattr(parent_server, "_handle_gyro_hb_rep", None)
    assert handler is not None, "CMD_GYRO_HEARTBEAT_REP dispatch not found"
    return inspect.getsource(handler)


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


# ── #874 — happy-path press-Start + fixture-PUT-during-active-claim ──────────

import struct as _struct


class _FakeMoverEngine:
    """Minimal _mover_engine substitute for #874 functional tests.
    Records claim/start_stream calls; behavior knobs:
      `next_claim_ok` — return value of claim().
      `claims` — emulates `get_status()`'s output for active claim
                 introspection (Bug F-style assertions)."""

    def __init__(self, claim_ok=True):
        self.next_claim_ok = claim_ok
        self.claim_calls = []
        self.start_stream_calls = []
        self.claims = []  # list of {moverId, deviceId, state}

    def claim(self, mover_id, device_id, dname, kind, smoothing=0.15):
        self.claim_calls.append((mover_id, device_id, dname, kind, smoothing))
        if not self.next_claim_ok:
            return False, "busy"
        self.claims.append({"moverId": mover_id, "deviceId": device_id,
                            "deviceName": dname, "deviceType": kind,
                            "state": "claimed"})
        return True, None

    def start_stream(self, mover_id, device_id):
        self.start_stream_calls.append((mover_id, device_id))
        for cl in self.claims:
            if cl["moverId"] == mover_id and cl["deviceId"] == device_id:
                cl["state"] = "streaming"

    def get_status(self):
        return list(self.claims)

    def release(self, mover_id, device_id, blackout=False):
        self.claims = [cl for cl in self.claims
                        if not (cl["moverId"] == mover_id
                                and cl["deviceId"] == device_id)]


class _GyroSandbox:
    """Per-test isolation for the parent_server module-level state we
    poke at: _fixtures, _mover_engine, _gyro_handshake, and the network
    side-effect helpers (_send_gyro_claim_ack/denied, _send_gyro_heartbeat,
    _gyro_lights_on). Restores everything on exit so tests can't leak."""

    def __enter__(self):
        self._orig_fixtures = list(parent_server._fixtures)
        self._orig_engine = parent_server._mover_engine
        self._orig_handshake = dict(parent_server._gyro_handshake)
        self._orig_send_ack = parent_server._send_gyro_claim_ack
        self._orig_send_denied = parent_server._send_gyro_claim_denied
        self._orig_send_hb = parent_server._send_gyro_heartbeat
        self._orig_lights_on = parent_server._gyro_lights_on
        # Spy slots
        self.acks = []
        self.denies = []
        self.heartbeats = []
        self.lights_on = []
        parent_server._send_gyro_claim_ack = (
            lambda ip, n, m: self.acks.append((ip, n, m)))
        parent_server._send_gyro_claim_denied = (
            lambda ip, reason=0: self.denies.append((ip, reason)))
        parent_server._send_gyro_heartbeat = (
            lambda ip: self.heartbeats.append(ip))
        parent_server._gyro_lights_on = (
            lambda mid: self.lights_on.append(mid))
        return self

    def install_fixtures(self, fixtures):
        parent_server._fixtures.clear()
        parent_server._fixtures.extend(fixtures)

    def install_engine(self, engine):
        parent_server._mover_engine = engine

    def __exit__(self, *_a):
        parent_server._fixtures.clear()
        parent_server._fixtures.extend(self._orig_fixtures)
        parent_server._mover_engine = self._orig_engine
        parent_server._gyro_handshake.clear()
        parent_server._gyro_handshake.update(self._orig_handshake)
        parent_server._send_gyro_claim_ack = self._orig_send_ack
        parent_server._send_gyro_claim_denied = self._orig_send_denied
        parent_server._send_gyro_heartbeat = self._orig_send_hb
        parent_server._gyro_lights_on = self._orig_lights_on


def _build_start_packet(nonce):
    """Build a CMD_GYRO_START UDP packet: 8-byte header + 2-byte nonce."""
    header = _struct.pack("<HBBI",
                          parent_server.UDP_MAGIC,
                          parent_server.UDP_VERSION,
                          parent_server.CMD_GYRO_START,
                          0)
    return header + _struct.pack("<H", nonce)


def test_874_gyro_start_on_fresh_fixture_emits_claim_ack():
    """The happy-path symptom A from #872: fresh gyro fixture
    (assignedMoverId=23, gyroEnabled=True) + GYRO_START arrives →
    server emits CLAIM_ACK with nonce echoed and moverId=23, the
    claim store gains a streaming entry, and NO CLAIM_DENIED is
    emitted. Pre-#874 nothing in the suite caught a regression
    where the dispatch silently dropped this path."""
    with _GyroSandbox() as sb:
        sb.install_fixtures([
            {"id": 23, "name": "350W BeamLight", "fixtureType": "dmx"},
            {"id": 99, "name": "Test Gyro", "fixtureType": "gyro",
             "type": "point", "gyroChildId": 5, "assignedMoverId": 23,
             "gyroEnabled": True, "smoothing": 0.15},
        ])
        # _gyro_fixture_for_ip looks up by child IP via _children, so
        # add a child entry that the gyro fixture's gyroChildId points at.
        orig_children = list(parent_server._children)
        parent_server._children.clear()
        parent_server._children.append(
            {"id": 5, "ip": "192.168.10.250",
             "hostname": "SLYG-TEST", "altName": ""})
        engine = _FakeMoverEngine(claim_ok=True)
        sb.install_engine(engine)
        try:
            packet = _build_start_packet(nonce=0xABCD)
            parent_server._handle_gyro_start_packet("192.168.10.250", packet)
        finally:
            parent_server._children.clear()
            parent_server._children.extend(orig_children)

        _assert(len(engine.claim_calls) == 1,
                f"_mover_engine.claim called once (got {len(engine.claim_calls)})")
        if engine.claim_calls:
            mover_id, device_id, _dname, kind, smoothing = engine.claim_calls[0]
            _assert(mover_id == 23,
                    f"claim with moverId=23 (got {mover_id})")
            _assert(device_id == "gyro-192.168.10.250",
                    f"claim with deviceId='gyro-192.168.10.250' (got {device_id!r})")
            _assert(kind == "gyro",
                    f"claim kind='gyro' (got {kind!r})")
            _assert(abs(smoothing - 0.15) < 1e-9,
                    f"claim smoothing=0.15 (got {smoothing})")
        _assert(len(engine.start_stream_calls) == 1,
                f"start_stream called once (got {len(engine.start_stream_calls)})")
        _assert(len(sb.acks) == 1,
                f"_send_gyro_claim_ack called once (got {len(sb.acks)})")
        if sb.acks:
            ip, n, m = sb.acks[0]
            _assert((ip, n, m) == ("192.168.10.250", 0xABCD, 23),
                    f"claim_ack(ip='192.168.10.250', nonce=0xABCD, mover=23) "
                    f"(got ip={ip!r}, nonce=0x{n:04X}, mover={m})")
        _assert(len(sb.denies) == 0,
                f"NO CLAIM_DENIED emitted (got {len(sb.denies)})")
        _assert(len(sb.heartbeats) == 1,
                f"immediate first heartbeat sent (got {len(sb.heartbeats)})")
        _assert(len(sb.lights_on) == 1 and sb.lights_on[0] == 23,
                f"_gyro_lights_on called for mover 23 (got {sb.lights_on})")
        # Claim store has streaming entry for mover 23.
        active = [cl for cl in engine.get_status() if cl["moverId"] == 23]
        _assert(len(active) == 1 and active[0]["state"] == "streaming",
                f"claim store has streaming entry for mover 23 "
                f"(got {active})")


def test_874_fixture_put_during_active_claim_does_not_leak():
    """Symptom-C scenario from #872: gyro fixture has an active
    claim (state=streaming). Operator sends `PUT /api/fixtures/<fid>`
    mutating a non-claim field (name). Claim store is NOT mutated
    as a side effect — fixture-save should never disturb a live
    gyro session. Pre-#874 nothing in the suite caught a regression
    where the PUT route accidentally torched the claim."""
    with _GyroSandbox() as sb:
        sb.install_fixtures([
            {"id": 23, "name": "350W BeamLight", "fixtureType": "dmx",
             "type": "point", "x": 0, "y": 0, "z": 0},
            {"id": 99, "name": "Test Gyro", "fixtureType": "gyro",
             "type": "point", "gyroChildId": 5, "assignedMoverId": 23,
             "gyroEnabled": True, "smoothing": 0.15},
        ])
        engine = _FakeMoverEngine(claim_ok=True)
        # Pre-seed an active streaming claim.
        engine.claims.append({"moverId": 23, "deviceId": "gyro-192.168.10.250",
                              "deviceName": "Test Gyro", "deviceType": "gyro",
                              "state": "streaming"})
        sb.install_engine(engine)

        with parent_server.app.test_client() as c:
            # PUT mutating a non-claim field on the DMX mover.
            r = c.put("/api/fixtures/23", json={"name": "350W (renamed)"})
            _assert(r.status_code == 200,
                    f"PUT /api/fixtures/23 200 (got {r.status_code})")

        # Claim still present + streaming.
        still_active = [cl for cl in engine.get_status() if cl["moverId"] == 23]
        _assert(len(still_active) == 1 and still_active[0]["state"] == "streaming",
                f"claim still streaming after PUT (got {still_active})")
        _assert(len(sb.denies) == 0,
                f"NO CLAIM_DENIED emitted by the PUT path (got {len(sb.denies)})")

        # Confirm the fixture rename actually landed.
        with parent_server.app.test_client() as c:
            r = c.get("/api/fixtures/23")
            data = r.get_json()
            _assert(data.get("name") == "350W (renamed)",
                    f"name persisted to '350W (renamed)' (got {data.get('name')!r})")


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
    test_874_gyro_start_on_fresh_fixture_emits_claim_ack,
    test_874_fixture_put_during_active_claim_does_not_leak,
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
