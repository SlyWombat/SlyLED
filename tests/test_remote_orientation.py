"""Unit + integration tests for the remote-orientation primitive.

Part of #484 phase 2. See docs/gyro-stage-space.md §4, §7.

Run:
    python -X utf8 tests/test_remote_orientation.py
"""

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from remote_math import norm3, quat_from_euler_zyx_deg, quat_rotate_vec  # noqa: E402
from remote_orientation import (  # noqa: E402
    REMOTE_FORWARD_LOCAL, REMOTE_UP_LOCAL, STALE_AGE_SECS, STALE_COMMS_SECS,
    Remote, RemoteRegistry, KIND_PUCK, KIND_PHONE,
)


_passed = 0
_failed = 0


def _eq(a, b, tol=1e-9, msg=""):
    global _passed, _failed
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        ok = (len(a) == len(b)
              and all(abs(float(x) - float(y)) < tol for x, y in zip(a, b)))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        ok = abs(a - b) < tol
    else:
        ok = a == b
    if ok:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}: {a!r} != {b!r} (tol={tol})")


def _true(cond, msg=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


# ── Remote basics ─────────────────────────────────────────────────────────

def test_remote_defaults():
    r = Remote(id=1)
    _eq(r.id, 1, msg="id")
    _true(r.name.startswith("Remote"), "default name")
    _eq(r.kind, KIND_PUCK, msg="default kind")
    _true(r.pos == [0.0, 0.0, 1600.0], "default pos head-height")
    _eq(r.calibrated, False, msg="uncalibrated")
    _eq(r.stale_reason, None, msg="no stale reason")
    _eq(r.connection_state, "idle", msg="idle")
    _eq(r.aim_stage, None, msg="no aim yet")


def test_remote_invalid_kind_falls_back():
    r = Remote(id=2, kind="bogus")
    _eq(r.kind, KIND_PUCK, msg="unknown kind → puck")


def test_remote_update_without_calibration():
    r = Remote(id=3)
    r.update_from_euler_deg(0, 0, 0)
    _true(r.last_quat_world is not None, "quat stored")
    _true(r.last_data > 0, "timestamp set")
    _eq(r.connection_state, "idle", msg="still idle (uncalibrated)")
    _eq(r.aim_stage, None, msg="no aim (uncalibrated)")


# ── Calibration: identity case ────────────────────────────────────────────

def test_calibrate_identity():
    """Remote held at identity orientation, target aiming forward (+Y).

    After calibration, rotating the remote's body-forward (+Y) through
    R_world_to_stage should reproduce the target aim (+Y).
    """
    r = Remote(id=10)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0), target_info={"objectId": 7, "kind": "mover"})
    _eq(r.calibrated, True, msg="calibrated flag set")
    _true(r.R_world_to_stage is not None, "R stored")
    _true(r.calibrated_at > 0, "calibrated_at stamped")
    _eq(r.calibrated_against, {"objectId": 7, "kind": "mover"}, msg="target info stamped")
    # aim should be +Y
    _eq(r.aim_stage, (0, 1, 0), tol=1e-9, msg="aim = +Y after identity cal")


def test_calibrate_then_rotate():
    """Calibrate remote at identity aiming +Y. Yaw +90° = rotation about
    body +Z, which by the right-hand rule takes body +Y to world -X.
    """
    r = Remote(id=11)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.update_from_euler_deg(0, 0, 90)
    _eq(r.aim_stage, (-1, 0, 0), tol=1e-9, msg="yaw +90° (Rz) → aim -X")


def test_calibrate_roll_tilts_forward():
    """With body +Y = forward, roll (rotation about body +X) tilts the
    forward axis in the YZ plane — this is what an operator would call
    "tilting the remote up/down".  Roll = +30° takes forward to
    (0, cos 30, sin 30) = aim tilts up.
    """
    r = Remote(id=12)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.update_from_euler_deg(30, 0, 0)
    expected = (0.0, math.cos(math.radians(30)), math.sin(math.radians(30)))
    _eq(r.aim_stage, expected, tol=1e-9, msg="roll +30° tilts forward up (+Z)")


def test_calibrate_pitch_is_roll_about_forward():
    """In aerospace ZYX with body +Y = forward, "pitch" is rotation
    about body +Y — it spins the remote around its own forward axis
    and leaves the aim unchanged.  This is the "twist / roll" gesture
    from an operator's perspective; it must not move the fixture.
    """
    r = Remote(id=13)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.update_from_euler_deg(0, 45, 0)
    _eq(r.aim_stage, (0, 1, 0), tol=1e-9,
        msg="pitch about body-forward axis leaves aim unchanged")


def test_calibrate_offset_target():
    """Remote held at identity; target aims diagonally. Calibration rotates
    the remote's frame so its forward maps to the diagonal.
    """
    r = Remote(id=13)
    r.update_from_euler_deg(0, 0, 0)
    target = (1.0, 1.0, 0.0)
    # normalize so the test compares against the same unit vector
    n = norm3(target)
    target_unit = (target[0]/n, target[1]/n, target[2]/n)
    r.calibrate(target_aim_stage=target)
    _eq(r.aim_stage, target_unit, tol=1e-9, msg="aim matches diagonal target")


def test_calibrate_uses_last_quat():
    """If no explicit orientation is passed, calibrate uses last_quat_world."""
    r = Remote(id=14)
    r.update_from_euler_deg(0, 10, 20)
    q_before = r.last_quat_world
    r.calibrate(target_aim_stage=(0, 1, 0))
    _eq(r.last_quat_world, q_before, tol=1e-12, msg="last_quat unchanged")
    _true(r.calibrated, "calibrated")


def test_calibrate_no_orientation_raises():
    r = Remote(id=15)
    try:
        r.calibrate(target_aim_stage=(0, 1, 0))
    except ValueError:
        _passed_plus = True
    else:
        _passed_plus = False
    _true(_passed_plus, "calibrate raises if no orientation available")


# ── Full user-model flow ──────────────────────────────────────────────────

def test_full_user_model():
    """Mover aimed at centre-stage floor. Operator picks up remote, aligns
    it physically (remote's forward now matches the mover's aim). Triggers
    calibrate. Then operator rotates remote — aim should follow 1:1.
    """
    # Mover at position (0, 0, 3000) (3 m up), aimed at stage centre floor
    # (3000, 3000, 0): aim vector ≈ (0.577, 0.577, -0.577).
    aim_target = (1.0, 1.0, -1.0)
    n = norm3(aim_target)
    aim_unit = (aim_target[0]/n, aim_target[1]/n, aim_target[2]/n)

    r = Remote(id=20, name="Stage Left Puck")
    # Operator physically rotates remote to match the aim direction.
    # We simulate this by saying: the remote's current sensor reading is
    # some arbitrary orientation q_at_calib, and at that moment the remote
    # is visually aligned with the target aim.
    # For the test we pick a specific orientation (mimics real sensor).
    r.update_from_euler_deg(15, -35, 25)
    r.calibrate(target_aim_stage=aim_target)
    _eq(r.aim_stage, aim_unit, tol=1e-9, msg="aim matches target right after cal")

    # Now operator slightly rotates — aim should move correspondingly.
    r.update_from_euler_deg(15, -35, 35)  # 10° more yaw
    # The exact new aim is hard to reason about in closed form, but it
    # must still be a unit vector and should differ from aim_unit.
    _eq(norm3(r.aim_stage), 1.0, tol=1e-9, msg="aim unit length after rotate")
    dx = r.aim_stage[0] - aim_unit[0]
    dy = r.aim_stage[1] - aim_unit[1]
    dz = r.aim_stage[2] - aim_unit[2]
    moved = math.sqrt(dx*dx + dy*dy + dz*dz)
    _true(moved > 0.01, "aim actually moves when remote rotates")


# ── Staleness ─────────────────────────────────────────────────────────────

def test_staleness_age():
    r = Remote(id=30)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    # Backdate calibration by > N days
    r.calibrated_at = time.time() - STALE_AGE_SECS - 10
    r.check_staleness()
    _eq(r.stale_reason, "age", msg="aged calibration flagged")
    _eq(r.connection_state, "stale", msg="stale connection state")


def test_staleness_comms_lost():
    r = Remote(id=31)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    # Backdate last sensor sample
    r.last_data = time.time() - STALE_COMMS_SECS - 10
    r.check_staleness()
    _eq(r.stale_reason, "connection-lost", msg="no comms flagged")


def test_staleness_session_ended():
    r = Remote(id=32)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.end_session()
    _eq(r.stale_reason, "session-ended", msg="session-end flagged")
    _eq(r.connection_state, "stale", msg="stale state")


def test_clear_stale_recomputes():
    r = Remote(id=33)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.end_session()
    _eq(r.connection_state, "stale", msg="staled")
    r.clear_stale()
    _eq(r.stale_reason, None, msg="reason cleared")
    _eq(r.connection_state, "streaming", msg="streaming after clear")


def test_fresh_calibration_clears_stale():
    r = Remote(id=34)
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    r.end_session()
    _eq(r.stale_reason, "session-ended", msg="staled")
    r.calibrate(target_aim_stage=(0, 1, 0))
    _eq(r.stale_reason, None, msg="re-cal clears stale")


# ── Live dict ─────────────────────────────────────────────────────────────

def test_live_dict_shape():
    r = Remote(id=40, name="Test", kind=KIND_PHONE, device_id="phone-xyz",
               pos=[1000, 2000, 1500])
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0),
                target_info={"objectId": 9, "kind": "mover"})
    d = r.live_dict()
    _eq(d["id"], 40, msg="id")
    _eq(d["kind"], KIND_PHONE, msg="kind")
    _eq(d["deviceId"], "phone-xyz", msg="deviceId")
    _eq(d["pos"], [1000, 2000, 1500], msg="pos")
    _eq(d["calibrated"], True, msg="calibrated")
    _true(d["aim"] is not None, "aim vector present")
    _eq(d["staleReason"], None, msg="not stale")
    _eq(d["calibratedAgainst"], {"objectId": 9, "kind": "mover"}, msg="target")
    _eq(d["connectionState"], "streaming", msg="streaming")
    _true(d["lastDataAge"] is not None and d["lastDataAge"] < 1.0,
          "lastDataAge fresh")


# ── Persistence (Remote) ──────────────────────────────────────────────────

def test_remote_persist_roundtrip():
    r = Remote(id=50, name="N", kind=KIND_PHONE, device_id="dev",
               pos=[1, 2, 3], rot=[10, 20, 30])
    r.update_from_euler_deg(5, 10, 15)
    r.calibrate(target_aim_stage=(0, 1, 0),
                target_info={"objectId": 1, "kind": "mover"})
    d = r.to_persisted_dict()
    r2 = Remote.from_persisted_dict(d)
    _eq(r2.id, r.id, msg="id persists")
    _eq(r2.name, r.name, msg="name persists")
    _eq(r2.kind, r.kind, msg="kind persists")
    _eq(r2.device_id, r.device_id, msg="deviceId persists")
    _eq(r2.pos, r.pos, msg="pos persists")
    _eq(r2.rot, r.rot, msg="rot persists")
    _eq(r2.calibrated, True, msg="calibrated persists")
    _eq(r2.calibrated_at, r.calibrated_at, msg="calibrated_at persists")
    _eq(r2.R_world_to_stage, r.R_world_to_stage, tol=1e-12,
        msg="R_world_to_stage persists")
    # Runtime fields NOT persisted
    _eq(r2.last_data, 0.0, msg="last_data not persisted")
    _eq(r2.aim_stage, None, msg="aim_stage not persisted")


# ── RemoteRegistry ────────────────────────────────────────────────────────

def test_registry_add_get_list_remove():
    reg = RemoteRegistry(data_path=None)
    a = reg.add(name="A", kind=KIND_PUCK, device_id="a")
    b = reg.add(name="B", kind=KIND_PHONE, device_id="b")
    _eq(a.id, 1, msg="first id = 1")
    _eq(b.id, 2, msg="second id = 2")
    _eq(reg.get(1).name, "A", msg="get by id")
    _eq(reg.by_device("b").name, "B", msg="by_device")
    _eq(reg.by_device("nonexistent"), None, msg="by_device missing")
    _eq(len(reg.list()), 2, msg="list has 2")
    reg.remove(1)
    _eq(len(reg.list()), 1, msg="list has 1 after remove")
    _eq(reg.get(1), None, msg="removed returns None")


def test_registry_update_fields():
    reg = RemoteRegistry(data_path=None)
    r = reg.add(name="Old", pos=[0, 0, 1600])
    reg.update_fields(r.id, name="New", pos=[1000, 2000, 1500])
    updated = reg.get(r.id)
    _eq(updated.name, "New", msg="name updated")
    _eq(updated.pos, [1000, 2000, 1500], msg="pos updated")


def test_registry_persistence():
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "remotes.json")
        reg = RemoteRegistry(data_path=path)
        a = reg.add(name="Persist me", kind=KIND_PUCK, device_id="d1",
                    pos=[100, 200, 300])
        a.update_from_euler_deg(0, 0, 0)
        a.calibrate(target_aim_stage=(0, 1, 0))
        reg.save()

        # Load in a new registry
        reg2 = RemoteRegistry(data_path=path)
        reg2.load()
        loaded = reg2.get(a.id)
        _true(loaded is not None, "loaded remote exists")
        _eq(loaded.name, "Persist me", msg="name roundtrips")
        _eq(loaded.calibrated, True, msg="calibrated roundtrips")
        _eq(loaded.R_world_to_stage, a.R_world_to_stage, tol=1e-12,
            msg="R_world_to_stage roundtrips")
        # next_id should advance past the highest loaded
        b = reg2.add(name="Next")
        _eq(b.id, a.id + 1, msg="next_id advances past loaded")
    finally:
        import shutil
        shutil.rmtree(tmpdir)


def test_registry_load_missing_file_is_safe():
    reg = RemoteRegistry(data_path="/nonexistent/path/remotes.json")
    reg.load()  # must not raise
    _eq(len(reg.list()), 0, msg="empty registry after missing-file load")


def test_registry_handles_corrupt_entries():
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "remotes.json")
        # Write one good + one bad entry
        with open(path, "w") as f:
            json.dump({
                "schemaVersion": 1,
                "remotes": [
                    {"id": 1, "name": "Good", "kind": "gyro-puck",
                     "pos": [0, 0, 1600], "rot": [0, 0, 0]},
                    {"broken": "missing id"},
                ],
            }, f)
        reg = RemoteRegistry(data_path=path)
        reg.load()
        _eq(len(reg.list()), 1, msg="bad entry skipped, good one loaded")
    finally:
        import shutil
        shutil.rmtree(tmpdir)


def test_registry_live_list():
    reg = RemoteRegistry(data_path=None)
    r = reg.add(name="Stream", device_id="d")
    r.update_from_euler_deg(0, 0, 0)
    r.calibrate(target_aim_stage=(0, 1, 0))
    lst = reg.live_list()
    _eq(len(lst), 1, msg="live list count")
    _eq(lst[0]["calibrated"], True, msg="live entry calibrated")


# ── Forward/up axis sanity ────────────────────────────────────────────────

def test_body_axis_constants():
    # Decision #1: +Y = forward, +Z = up.
    _eq(REMOTE_FORWARD_LOCAL, (0, 1, 0), msg="forward = +Y")
    _eq(REMOTE_UP_LOCAL, (0, 0, 1), msg="up = +Z")
    # Identity quaternion: body-to-world is identity, so forward in world
    # equals forward in body.
    q = quat_from_euler_zyx_deg(0, 0, 0)
    _eq(quat_rotate_vec(q, REMOTE_FORWARD_LOCAL), REMOTE_FORWARD_LOCAL,
        tol=1e-12, msg="identity keeps forward")


# ── Run everything ────────────────────────────────────────────────────────

ALL = [
    test_remote_defaults,
    test_remote_invalid_kind_falls_back,
    test_remote_update_without_calibration,
    test_calibrate_identity,
    test_calibrate_then_rotate,
    test_calibrate_roll_tilts_forward,
    test_calibrate_pitch_is_roll_about_forward,
    test_calibrate_offset_target,
    test_calibrate_uses_last_quat,
    test_calibrate_no_orientation_raises,
    test_full_user_model,
    test_staleness_age,
    test_staleness_comms_lost,
    test_staleness_session_ended,
    test_clear_stale_recomputes,
    test_fresh_calibration_clears_stale,
    test_live_dict_shape,
    test_remote_persist_roundtrip,
    test_registry_add_get_list_remove,
    test_registry_update_fields,
    test_registry_persistence,
    test_registry_load_missing_file_is_safe,
    test_registry_handles_corrupt_entries,
    test_registry_live_list,
    test_body_axis_constants,
]


if __name__ == "__main__":
    for t in ALL:
        t()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
