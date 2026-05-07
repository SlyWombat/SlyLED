#!/usr/bin/env python3
"""test_816_phone_grip_publish.py - Regression for #816.

End-to-end test of the Android-side grip-publish flow:

  1. Android client (simulated) POSTs to /api/remotes/grip with
     {deviceId, forwardLocal, upLocal} matching its current Surface
     rotation.
  2. Server auto-registers the Remote if it doesn't exist yet
     (matches the orient handler's auto-register shape so a fresh
     phone's first claim works without a separate /api/remotes POST).
  3. The Remote's forward_local / up_local are persisted.
  4. A subsequent orient packet uses the published grip — pitch-
     forward gestures produce aim_stage.z < 0 on a portrait phone
     (the symptom in the issue: pitch produced no tilt because the
     server defaulted to forward_local = (1, 0, 0)).
  5. POSTing a new grip mid-session updates the mapping immediately
     (portrait → landscape rotation while a session is active).

Run: python -X utf8 tests/test_816_phone_grip_publish.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from remote_math import quat_from_axis_angle  # noqa: E402

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


def _client():
    parent_server.app.config["TESTING"] = True
    return parent_server.app.test_client()


# Surface rotation → (forwardLocal, upLocal) — must mirror the table in
# ControlViewModel.publishGripFromSurfaceRotation.
GRIPS_BY_ROTATION = {
    0: ([0.0,  1.0, 0.0], [0.0, 0.0, 1.0]),   # ROTATION_0   portrait
    1: ([1.0,  0.0, 0.0], [0.0, 0.0, 1.0]),   # ROTATION_90  landscape-left
    2: ([0.0, -1.0, 0.0], [0.0, 0.0, 1.0]),   # ROTATION_180 inverted-portrait
    3: ([-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]),   # ROTATION_270 landscape-right
}


# ---------------------------------------------------------------------------

def test_grip_endpoint_auto_registers_phone():
    """First call from a fresh Android device — Remote must be created
    on demand (matches the orient handler's auto-register behaviour)."""
    c = _client()
    fwd, up = GRIPS_BY_ROTATION[0]
    r = c.post("/api/remotes/grip", json={
        "deviceId": "test-816-fresh", "forwardLocal": fwd, "upLocal": up,
    })
    _assert(r.status_code == 200, f"POST returns 200 (got {r.status_code})")
    body = r.get_json()
    _assert(body.get("ok") is True, f"ok=true (got {body.get('ok')})")
    rec = parent_server._remotes.by_device("test-816-fresh")
    _assert(rec is not None, "auto-registered Remote exists")
    if rec is not None:
        _assert(rec.kind == "phone",
                f"auto-registered as kind=phone (got {rec.kind})")


def test_grip_persists_forward_and_up_local():
    c = _client()
    fwd, up = GRIPS_BY_ROTATION[0]
    c.post("/api/remotes/grip", json={
        "deviceId": "test-816-portrait",
        "forwardLocal": fwd, "upLocal": up,
    })
    rec = parent_server._remotes.by_device("test-816-portrait")
    _assert(rec is not None, "Remote registered")
    if rec is None:
        return
    _assert(tuple(rec.forward_local) == (0.0, 1.0, 0.0),
            f"forward_local persisted as portrait (got {rec.forward_local})")
    _assert(tuple(rec.up_local) == (0.0, 0.0, 1.0),
            f"up_local persisted (got {rec.up_local})")


def test_grip_validates_forward_shape():
    c = _client()
    r = c.post("/api/remotes/grip", json={
        "deviceId": "test-816-bad", "forwardLocal": [1, 2],  # too short
    })
    _assert(r.status_code == 400,
            f"missing length-3 forwardLocal rejected (got {r.status_code})")


def test_grip_requires_device_id():
    c = _client()
    r = c.post("/api/remotes/grip", json={
        "forwardLocal": [0, 1, 0], "upLocal": [0, 0, 1],
    })
    _assert(r.status_code == 400,
            f"missing deviceId rejected (got {r.status_code})")


def test_portrait_grip_pitch_forward_tilts_down():
    """The acceptance criterion from #816: with portrait grip
    published, pitching the phone forward 30° should produce
    aim_stage.z < 0 (head tilts down)."""
    c = _client()
    # Establish portrait grip via the API.
    fwd, up = GRIPS_BY_ROTATION[0]
    c.post("/api/remotes/grip", json={
        "deviceId": "test-816-pitch",
        "forwardLocal": fwd, "upLocal": up,
    })
    rec = parent_server._remotes.by_device("test-816-pitch")
    _assert(rec is not None, "Remote registered for pitch test")
    if rec is None:
        return
    # Calibrate so R_world_to_stage is identity (pose-as-target).
    rec.calibrate(target_aim_stage=(0.0, 1.0, 0.0),
                   quat=(1.0, 0.0, 0.0, 0.0))
    # 30° pitch forward (rotation around body +X by -angle, per
    # right-hand rule taking +Y → -Z).
    q = quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(30))
    rec.update_from_quat(q)
    _assert(rec.aim_stage is not None, "pitch forward produces aim_stage")
    if rec.aim_stage is None:
        return
    _assert(rec.aim_stage[2] < -0.3,
            f"portrait pitch forward 30° → aim_stage.z < -0.3 "
            f"(got {rec.aim_stage[2]:.3f}) — tilts DOWN as expected")
    _assert(rec.aim_stage[1] > 0.7,
            f"portrait pitch forward 30° → aim_stage.y > +0.7 "
            f"(got {rec.aim_stage[1]:.3f})")


def test_grip_can_be_updated_mid_session():
    """Mid-session rotation: device starts portrait, switches to
    landscape; the orchestrator's mapping must update on the next
    grip POST without restarting the Remote."""
    c = _client()
    # Portrait first.
    fwd, up = GRIPS_BY_ROTATION[0]
    c.post("/api/remotes/grip", json={
        "deviceId": "test-816-rotate",
        "forwardLocal": fwd, "upLocal": up,
    })
    rec = parent_server._remotes.by_device("test-816-rotate")
    _assert(tuple(rec.forward_local) == (0.0, 1.0, 0.0),
            "starts portrait")
    # Now landscape-left.
    fwd2, up2 = GRIPS_BY_ROTATION[1]
    c.post("/api/remotes/grip", json={
        "deviceId": "test-816-rotate",
        "forwardLocal": fwd2, "upLocal": up2,
    })
    rec = parent_server._remotes.by_device("test-816-rotate")
    _assert(tuple(rec.forward_local) == (1.0, 0.0, 0.0),
            f"switches to landscape (got {rec.forward_local})")
    _assert(rec.id is not None and rec.device_id == "test-816-rotate",
            "same Remote row — no duplicate registration")


def test_all_four_surface_rotations_round_trip():
    """Every Surface.ROTATION_* value the Android client may publish
    must be accepted and persisted as the matching pointer axis."""
    c = _client()
    for rot, (fwd, up) in GRIPS_BY_ROTATION.items():
        did = f"test-816-rot{rot}"
        r = c.post("/api/remotes/grip", json={
            "deviceId": did, "forwardLocal": fwd, "upLocal": up,
        })
        _assert(r.status_code == 200,
                f"ROTATION_{rot * 90} accepted (got {r.status_code})")
        rec = parent_server._remotes.by_device(did)
        _assert(rec is not None, f"ROTATION_{rot * 90} Remote registered")
        if rec is None:
            continue
        _assert([float(v) for v in rec.forward_local] == fwd,
                f"ROTATION_{rot * 90} forward_local match "
                f"(expected {fwd}, got {list(rec.forward_local)})")


ALL = [
    test_grip_endpoint_auto_registers_phone,
    test_grip_persists_forward_and_up_local,
    test_grip_validates_forward_shape,
    test_grip_requires_device_id,
    test_portrait_grip_pitch_forward_tilts_down,
    test_grip_can_be_updated_mid_session,
    test_all_four_surface_rotations_round_trip,
]


def _cleanup():
    """Remove test Remotes so re-running the suite is idempotent."""
    for did in ("test-816-fresh", "test-816-portrait", "test-816-bad",
                 "test-816-pitch", "test-816-rotate", "test-816-rot0",
                 "test-816-rot1", "test-816-rot2", "test-816-rot3"):
        rec = parent_server._remotes.by_device(did)
        if rec is not None:
            try:
                parent_server._remotes.delete(rec.id)
            except Exception:
                pass
    parent_server._remotes.save()


if __name__ == "__main__":
    print("=== #816 phone grip publish via /api/remotes/grip ===")
    try:
        for t in ALL:
            print(f"\n-- {t.__name__} --")
            try:
                t()
            except Exception as e:
                _failed += 1
                print(f"  [FAIL] {t.__name__} raised: {e}")
                import traceback
                traceback.print_exc()
    finally:
        _cleanup()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
