"""HTTP integration tests for the /api/remotes/* endpoints.

Part of #484 phase 2b. Covers CRUD, orient, calibrate-end against a
mover fixture, stale lifecycle, and auto-registration from UDP-style
ingest.

Run:
    python -X utf8 tests/test_remotes_api.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import app, _fixtures, _remotes  # noqa: E402


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


def _clear_remotes():
    # Wipe the registry so each test starts clean.
    for r in _remotes.list():
        _remotes.remove(r.id)


def _add_mover_fixture():
    """Inject a minimal DMX mover into _fixtures so calibrate-end has a target."""
    fx = {
        "id": 99991,
        "name": "Test Mover",
        "fixtureType": "dmx",
        "dmxUniverse": 1,
        "dmxStartAddr": 1,
        "dmxProfileId": None,  # no profile → pan/tilt default (0.5, 0.5) — forward aim
        "panRange": 540,
        "tiltRange": 270,
        "rotation": [0, 0, 0],
    }
    _fixtures.append(fx)
    return fx


def _remove_mover_fixture(fid):
    for i, f in enumerate(list(_fixtures)):
        if f.get("id") == fid:
            _fixtures.pop(i)
            return


# ── Tests ─────────────────────────────────────────────────────────────────

def test_empty_list():
    _clear_remotes()
    with app.test_client() as c:
        r = c.get("/api/remotes")
        _assert(r.status_code == 200, "GET /api/remotes status")
        data = r.get_json()
        _assert(isinstance(data.get("remotes"), list), "remotes is list")
        _assert(len(data["remotes"]) == 0, "empty after clear")


def test_create_and_list():
    _clear_remotes()
    with app.test_client() as c:
        r = c.post("/api/remotes", json={
            "name": "Test Puck",
            "kind": "gyro-puck",
            "deviceId": "gyro-192.0.2.1",
            "pos": [1000, 2000, 1600],
        })
        _assert(r.status_code == 200, "POST create status")
        rd = r.get_json()
        _assert(rd["ok"], "create ok")
        created_id = rd["remote"]["id"]
        _assert(rd["remote"]["name"] == "Test Puck", "name persisted")
        _assert(rd["remote"]["pos"] == [1000, 2000, 1600], "pos persisted")

        r = c.get("/api/remotes")
        d = r.get_json()
        _assert(len(d["remotes"]) == 1, "list has 1")
        _assert(d["remotes"][0]["id"] == created_id, "correct id in list")


def test_create_invalid_kind():
    _clear_remotes()
    with app.test_client() as c:
        r = c.post("/api/remotes", json={"kind": "bogus"})
        _assert(r.status_code == 400, "invalid kind rejected")


def test_update_fields():
    _clear_remotes()
    with app.test_client() as c:
        rid = c.post("/api/remotes", json={"name": "Old"}).get_json()["remote"]["id"]
        r = c.post(f"/api/remotes/{rid}",
                   json={"name": "New", "pos": [500, 1000, 1500]})
        _assert(r.status_code == 200, "update status")
        d = r.get_json()
        _assert(d["ok"], "update ok")
        _assert(d["remote"]["name"] == "New", "name updated")
        _assert(d["remote"]["pos"] == [500, 1000, 1500], "pos updated")


def test_update_missing_returns_404():
    with app.test_client() as c:
        r = c.post("/api/remotes/999999", json={"name": "X"})
        _assert(r.status_code == 404, "missing remote → 404")


def test_delete():
    _clear_remotes()
    with app.test_client() as c:
        rid = c.post("/api/remotes", json={"name": "Del"}).get_json()["remote"]["id"]
        r = c.delete(f"/api/remotes/{rid}")
        _assert(r.status_code == 200, "delete status")
        _assert(r.get_json()["ok"], "delete ok")
        _assert(_remotes.get(rid) is None, "gone from registry")


def test_live_shape():
    _clear_remotes()
    with app.test_client() as c:
        rid = c.post("/api/remotes", json={"name": "Live"}).get_json()["remote"]["id"]
        r = c.get("/api/remotes/live")
        _assert(r.status_code == 200, "live status")
        d = r.get_json()
        rec = next((x for x in d["remotes"] if x["id"] == rid), None)
        _assert(rec is not None, "live contains created")
        for key in ("calibrated", "staleReason", "aim",
                    "connectionState", "lastDataAge", "pos"):
            _assert(key in rec, f"live has {key}")


def test_orient_http():
    _clear_remotes()
    with app.test_client() as c:
        rid = c.post("/api/remotes", json={"name": "O"}).get_json()["remote"]["id"]
        r = c.post(f"/api/remotes/{rid}/orient",
                   json={"roll": 0, "pitch": 0, "yaw": 0})
        _assert(r.status_code == 200, "orient status")
        _assert(r.get_json()["ok"], "orient ok")
        rem = _remotes.get(rid)
        _assert(rem.last_data > 0, "last_data stamped")


def test_calibrate_and_orient_flow():
    """Create remote → send orient → calibrate against mover →
    subsequent orient updates produce aim_stage.
    """
    _clear_remotes()
    fx = _add_mover_fixture()
    try:
        with app.test_client() as c:
            rid = c.post("/api/remotes",
                         json={"name": "Cal", "kind": "gyro-puck"}).get_json()["remote"]["id"]
            # Initial orient sample — establishes last_quat_world
            c.post(f"/api/remotes/{rid}/orient",
                   json={"roll": 0, "pitch": 0, "yaw": 0})
            # Calibrate: mover pan/tilt defaults to 0.5,0.5 so target aim = (0, 1, 0)
            r = c.post(f"/api/remotes/{rid}/calibrate-end",
                       json={"targetObjectId": fx["id"], "targetKind": "mover"})
            _assert(r.status_code == 200, "calibrate-end status")
            d = r.get_json()
            _assert(d["ok"], "calibrate ok")
            _assert(d["remote"]["calibrated"], "calibrated flag")
            _assert(d["remote"]["aim"] is not None, "aim vector present")
            # Subsequent orient should produce stream state
            c.post(f"/api/remotes/{rid}/orient",
                   json={"roll": 0, "pitch": 0, "yaw": 0})
            rem = _remotes.get(rid)
            _assert(rem.connection_state == "streaming", "streaming after cal+orient")
    finally:
        _remove_mover_fixture(fx["id"])


def test_calibrate_missing_target_404():
    _clear_remotes()
    with app.test_client() as c:
        rid = c.post("/api/remotes", json={"name": "X"}).get_json()["remote"]["id"]
        c.post(f"/api/remotes/{rid}/orient",
               json={"roll": 0, "pitch": 0, "yaw": 0})
        r = c.post(f"/api/remotes/{rid}/calibrate-end",
                   json={"targetObjectId": 123456789, "targetKind": "mover"})
        _assert(r.status_code == 404, "missing mover → 404")


def test_calibrate_non_mover_rejected():
    _clear_remotes()
    fx = _add_mover_fixture()
    try:
        with app.test_client() as c:
            rid = c.post("/api/remotes", json={"name": "X"}).get_json()["remote"]["id"]
            c.post(f"/api/remotes/{rid}/orient",
                   json={"roll": 0, "pitch": 0, "yaw": 0})
            r = c.post(f"/api/remotes/{rid}/calibrate-end",
                       json={"targetObjectId": fx["id"],
                             "targetKind": "fixture"})  # non-mover
            _assert(r.status_code == 400, "non-mover target rejected (decision #6)")
    finally:
        _remove_mover_fixture(fx["id"])


def test_end_session_and_clear_stale():
    _clear_remotes()
    fx = _add_mover_fixture()
    try:
        with app.test_client() as c:
            rid = c.post("/api/remotes", json={"name": "S"}).get_json()["remote"]["id"]
            c.post(f"/api/remotes/{rid}/orient",
                   json={"roll": 0, "pitch": 0, "yaw": 0})
            c.post(f"/api/remotes/{rid}/calibrate-end",
                   json={"targetObjectId": fx["id"], "targetKind": "mover"})
            # End session
            r = c.post(f"/api/remotes/{rid}/end-session")
            _assert(r.status_code == 200, "end-session status")
            _assert(r.get_json()["remote"]["staleReason"] == "session-ended",
                    "stale reason set")
            # Clear stale
            r = c.post(f"/api/remotes/{rid}/clear-stale")
            _assert(r.get_json()["remote"]["staleReason"] is None,
                    "stale cleared")
    finally:
        _remove_mover_fixture(fx["id"])


def test_auto_register_from_udp_path():
    """Simulate the UDP listener's auto-register logic (not actually sending
    UDP, but invoking _auto_register_remote + update_from_euler_deg).
    """
    _clear_remotes()
    rem = parent_server._auto_register_remote("gyro-192.0.2.77",
                                              kind=parent_server.KIND_PUCK)
    _assert(rem is not None, "auto-register creates remote")
    _assert(rem.device_id == "gyro-192.0.2.77", "device_id matches")
    # Second call returns the same one
    rem2 = parent_server._auto_register_remote("gyro-192.0.2.77")
    _assert(rem2.id == rem.id, "auto-register is idempotent")


ALL = [
    test_empty_list,
    test_create_and_list,
    test_create_invalid_kind,
    test_update_fields,
    test_update_missing_returns_404,
    test_delete,
    test_live_shape,
    test_orient_http,
    test_calibrate_and_orient_flow,
    test_calibrate_missing_target_404,
    test_calibrate_non_mover_rejected,
    test_end_session_and_clear_stale,
    test_auto_register_from_udp_path,
]


if __name__ == "__main__":
    for t in ALL:
        t()
    # Cleanup after ourselves so repeated runs don't leave test data.
    _clear_remotes()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
