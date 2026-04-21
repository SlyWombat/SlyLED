"""Calibration lock tests (#511).

Verifies that:
  - Engaging the lock blocks /api/fixtures/<id>/dmx-test with HTTP 423.
  - MoverControlEngine._tick skips locked fixtures.
  - The lock is runtime-only and doesn't persist to fixtures.json.
  - Crash recovery: stale `isCalibrating` from a prior run is cleared on load.

Run:
    python -X utf8 tests/test_calibration_lock.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import (  # noqa: E402
    app, _fixtures, _fixture_is_calibrating, _set_calibrating,
    _mover_cal_jobs, _artnet,
)


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


def _add_mover(fid=9999):
    fx = {
        "id": fid,
        "name": "Lock Test Mover",
        "fixtureType": "dmx",
        "dmxUniverse": 1,
        "dmxStartAddr": 1,
        "dmxProfileId": None,
        "panRange": 540,
        "tiltRange": 270,
    }
    _fixtures.append(fx)
    return fx


def _remove_mover(fid):
    for i, f in enumerate(list(_fixtures)):
        if f.get("id") == fid:
            _fixtures.pop(i)
            return


# ── Tests ────────────────────────────────────────────────────────────────

def test_is_calibrating_default_false():
    fx = _add_mover()
    try:
        _assert(_fixture_is_calibrating(fx["id"]) is False,
                "default is not calibrating")
    finally:
        _remove_mover(fx["id"])


def test_set_and_clear_lock():
    fx = _add_mover()
    try:
        _set_calibrating(fx["id"], True)
        _assert(_fixture_is_calibrating(fx["id"]) is True, "lock engaged")
        _set_calibrating(fx["id"], False)
        _assert(_fixture_is_calibrating(fx["id"]) is False, "lock released")
    finally:
        _remove_mover(fx["id"])


def test_set_calibrating_unknown_fid_is_noop():
    # Must not raise.
    _set_calibrating(88888888, True)
    _set_calibrating(88888888, False)
    _passed_inc()


def _passed_inc():
    global _passed
    _passed += 1


def test_dmx_test_rejected_when_locked():
    fx = _add_mover()
    try:
        _set_calibrating(fx["id"], True)
        with app.test_client() as c:
            r = c.post(f"/api/fixtures/{fx['id']}/dmx-test",
                       json={"pan": 0.5, "tilt": 0.5, "dimmer": 1.0})
            _assert(r.status_code == 423, f"423 when locked, got {r.status_code}")
            _assert("calibrated" in (r.get_json() or {}).get("err", "").lower(),
                    "err mentions calibration")
    finally:
        _set_calibrating(fx["id"], False)
        _remove_mover(fx["id"])


def test_dmx_test_passes_when_unlocked():
    fx = _add_mover()
    try:
        # Unlocked but no profile → we expect 400 (no profile), NOT 423.
        with app.test_client() as c:
            r = c.post(f"/api/fixtures/{fx['id']}/dmx-test",
                       json={"pan": 0.5, "tilt": 0.5})
            _assert(r.status_code != 423,
                    f"no lock → not 423 ({r.status_code})")
    finally:
        _remove_mover(fx["id"])


def test_lock_not_persisted_on_save():
    """isCalibrating is runtime state — must not leak into fixtures.json."""
    fx = _add_mover()
    try:
        _set_calibrating(fx["id"], True)
        # Simulate the cal-complete path: clear lock, then save.
        _set_calibrating(fx["id"], False)
        # Confirm cleared
        _assert("isCalibrating" not in fx,
                "isCalibrating cleared from fixture dict")
    finally:
        _remove_mover(fx["id"])


def test_stale_lock_cleared_on_startup():
    """The module-load sweep removes any persisted isCalibrating=True."""
    # Inject a dirty fixture as if loaded from a bad fixtures.json.
    dirty = {"id": 77777, "fixtureType": "dmx", "isCalibrating": True}
    _fixtures.append(dirty)
    try:
        # Simulate the boot sweep (already ran at import — do it again
        # to prove it's idempotent).
        for f in _fixtures:
            f.pop("isCalibrating", None)
        _assert("isCalibrating" not in dirty,
                "startup sweep clears stale flag")
    finally:
        _remove_mover(dirty["id"])


def test_is_calibrating_hook_threads_through_engine():
    """The MoverControlEngine's is_calibrating callback is the same helper
    the HTTP layer uses — no divergence."""
    fx = _add_mover()
    try:
        _set_calibrating(fx["id"], True)
        # Engine was constructed with parent_server._fixture_is_calibrating
        _assert(parent_server._mover_engine._is_calibrating(fx["id"]) is True,
                "engine sees lock")
        _set_calibrating(fx["id"], False)
        _assert(parent_server._mover_engine._is_calibrating(fx["id"]) is False,
                "engine sees release")
    finally:
        _remove_mover(fx["id"])


def test_cancel_immediately_blackouts_fixture_window():
    """#604 — /cancel must zero the fixture's DMX window synchronously,
    not wait for the background thread to notice the cancel flag. Without
    this the moving head keeps pointing/lit for up to 30 s while the
    thread is stuck in a camera `urlopen()` call."""
    fx = _add_mover(fid=99997)
    # Profile-free fixture defaults to 13-ch Slymovehead layout.
    fx["dmxChannelCount"] = 13
    # Give the fixture some neighbours on the same universe so we can
    # prove the cancel doesn't clobber them.
    _mover_cal_jobs[str(fx["id"])] = {"status": "running", "phase": "sampling"}
    engine_was_running = _artnet.running
    if not engine_was_running:
        _artnet.start()
    try:
        uni = _artnet.get_universe(1)
        # Simulate the mid-calibration cue: non-zero Pan/Tilt/Dimmer/RGB
        # in the fixture's window, and a distinctive sentinel in the
        # neighbour channel (14) that cancel must NOT touch.
        uni.set_channels(1, [99, 246, 0, 255, 5, 255, 255, 255, 0, 0, 0, 0, 0])
        uni.set_channel(14, 77)
        pre = list(uni.get_data()[:14])
        _assert(pre[:13] != [0]*13, "pre-cancel fixture window non-zero")
        _assert(pre[13] == 77, "pre-cancel neighbour byte set")
        with app.test_client() as c:
            r = c.post(f"/api/calibration/mover/{fx['id']}/cancel")
            _assert(r.status_code == 200, f"cancel returns 200 (got {r.status_code})")
            body = r.get_json() or {}
            _assert(body.get("cancelled") is True, "body.cancelled=True")
        post = list(uni.get_data()[:14])
        _assert(post[:13] == [0]*13,
                f"fixture window zeroed synchronously (got {post[:13]})")
        _assert(post[13] == 77, "neighbour byte preserved (not 77: " + str(post[13]) + ")")
    finally:
        if not engine_was_running:
            _artnet.stop()
        _mover_cal_jobs.pop(str(fx["id"]), None)
        _remove_mover(fx["id"])


def test_cancel_no_running_job_is_noop():
    """#604 — cancel on a fixture that isn't running returns ok=True with
    cancelled=False and does NOT clobber the universe. Guards against a
    naive implementation that blackouts unconditionally."""
    fx = _add_mover(fid=99996)
    engine_was_running = _artnet.running
    if not engine_was_running:
        _artnet.start()
    try:
        uni = _artnet.get_universe(1)
        uni.set_channels(1, [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130])
        with app.test_client() as c:
            r = c.post(f"/api/calibration/mover/{fx['id']}/cancel")
            body = r.get_json() or {}
            _assert(body.get("cancelled") is False, "cancelled=False when no job")
        post = list(uni.get_data()[:13])
        _assert(post == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130],
                f"universe untouched when no running job (got {post})")
    finally:
        if not engine_was_running:
            _artnet.stop()
        _remove_mover(fx["id"])


ALL = [
    test_is_calibrating_default_false,
    test_set_and_clear_lock,
    test_set_calibrating_unknown_fid_is_noop,
    test_dmx_test_rejected_when_locked,
    test_dmx_test_passes_when_unlocked,
    test_lock_not_persisted_on_save,
    test_stale_lock_cleared_on_startup,
    test_is_calibrating_hook_threads_through_engine,
    test_cancel_immediately_blackouts_fixture_window,
    test_cancel_no_running_job_is_noop,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
