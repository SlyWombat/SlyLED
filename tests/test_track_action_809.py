#!/usr/bin/env python3
"""test_track_action_809.py — Regression for #809.

Track action used to use legacy geometric IK (`compute_pan_tilt`)
unconditionally; for fixtures with full Home + Secondary + sized
profile that produced DMX disagreeing with the rest of the system
(claim writer, /api/mover/<fid>/aim, 3D viz). Fix routes Track action
through the same `AimSphere.aim_xyz` path used by every other writer.

This test asserts: with the fixture configured, `_evaluate_track_actions`
writes the same pan/tilt DMX that `sphere.aim_xyz` produces for the
same target — within ±2 LSB of rounding.

Run:  python -X utf8 tests/test_track_action_809.py
"""

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


def _ensure_artnet_running():
    """Start Art-Net engine but mute the socket so no real frames hit
    the network — same pattern test_mover_control.py uses."""
    if not parent_server._artnet.running:
        with parent_server.app.test_client() as c:
            c.post("/api/dmx/start", json={"protocol": "artnet"})
        import time
        time.sleep(0.05)
    import socket as _sock_mod

    class _MuteSock:
        def sendto(self, *a, **kw):
            pass

        def recvfrom(self, n):
            raise _sock_mod.timeout

        def close(self):
            pass

        def setblocking(self, *a, **kw):
            pass

        def settimeout(self, *a, **kw):
            pass

    if parent_server._artnet._sock is not None:
        try:
            parent_server._artnet._sock.close()
        except Exception:
            pass
        parent_server._artnet._sock = _MuteSock()


# Stage-Right-shaped test fixture: matches the live-test #809 layout.
TEST_FID = 178099
TEST_PROFILE_ID = "test-mh-809"


def _setup_profile_and_fixture():
    """Inject a moving-head profile + configured fixture into the
    parent's runtime state. Returns the AimSphere built from the same
    data so the test can compute the expected DMX directly."""
    profile_doc = {
        "id": TEST_PROFILE_ID,
        "name": "Test MH 809",
        "manufacturer": "Test",
        "category": "moving-head",
        "channelCount": 12,
        "panRange": 540,
        "tiltRange": 270,
        "channels": [
            {"offset": 0, "name": "Pan", "type": "pan", "bits": 16},
            {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16},
            {"offset": 4, "name": "Dimmer", "type": "dimmer"},
            {"offset": 5, "name": "Red", "type": "red"},
            {"offset": 6, "name": "Green", "type": "green"},
            {"offset": 7, "name": "Blue", "type": "blue"},
        ],
    }
    with parent_server.app.test_client() as c:
        c.post("/api/dmx-profiles", json=profile_doc)

    # Build the fixture record exactly as the live test had it. Stage
    # Right at (600, 0, 1760), Home + Secondary saved, no rotation.
    fx = {
        "id": TEST_FID,
        "name": "Test Stage Right 809",
        "fixtureType": "dmx",
        "dmxUniverse": 1,
        "dmxStartAddr": 1,
        "dmxChannelCount": 12,
        "dmxProfileId": TEST_PROFILE_ID,
        "panRange": 540,
        "tiltRange": 270,
        "rotation": [0, 0, 0],
        "homePanDmx16": 44364,
        "homeTiltDmx16": 0,
        # Saved during Save Home wizard; sphere needs both axes.
        "homeSecondary": {
            "panMovedDirection": "right",
            "panOffsetDmx16": 10922,
            "tiltMovedDirection": "down",
            "tiltOffsetDmx16": 32768,
        },
    }
    parent_server._fixtures.append(fx)

    # Layout child carries the position (mirrors real data shape).
    parent_server._layout.setdefault("children", [])
    parent_server._layout["children"].append({
        "id": TEST_FID,
        "x": 600, "y": 0, "z": 1760,
    })
    return fx


def _teardown_fixture():
    parent_server._fixtures[:] = [
        f for f in parent_server._fixtures if f.get("id") != TEST_FID]
    if "children" in parent_server._layout:
        parent_server._layout["children"] = [
            c for c in parent_server._layout["children"]
            if c.get("id") != TEST_FID]
    parent_server._actions[:] = [
        a for a in parent_server._actions if a.get("name") != "test-809-track"]
    parent_server._temporal_objects[:] = [
        o for o in parent_server._temporal_objects
        if o.get("id") != 99099]
    parent_server._clear_canonical_aim_stage(TEST_FID)


def _build_expected_pose(fx, target_xyz):
    """Compute the DMX `_evaluate_track_actions` should write per
    #809: `AimSphere.aim_xyz(target)` with the fixture's xyz patched
    in (same path the production code now takes)."""
    from aim.routes import _get_or_build_sphere
    prof = parent_server._profile_lib.channel_info(TEST_PROFILE_ID)
    sf = dict(fx)
    sf["x"], sf["y"], sf["z"] = 600, 0, 1760
    sphere = _get_or_build_sphere(sf, prof)
    return sphere.aim_xyz(tuple(target_xyz), prefer="closest")


def _read_pan_tilt_dmx16(fx):
    """Read 16-bit pan/tilt back from the universe buffer."""
    addr = fx.get("dmxStartAddr", 1)
    uni = parent_server._artnet.get_universe(fx.get("dmxUniverse", 1))
    pan_hi = uni.get_channel(addr + 0)
    pan_lo = uni.get_channel(addr + 1)
    tilt_hi = uni.get_channel(addr + 2)
    tilt_lo = uni.get_channel(addr + 3)
    return ((pan_hi << 8) | pan_lo, (tilt_hi << 8) | tilt_lo)


def test_track_writes_sphere_dmx_for_configured_fixture():
    """#809 acceptance #1: physical aim follows operator intent.
    Verified via 'Track-action DMX == sphere.aim_xyz(target) DMX'."""
    _ensure_artnet_running()
    fx = _setup_profile_and_fixture()
    try:
        # Patrol target — figure-8 instantaneous pos from the live test.
        target_xyz = (1008.6, 3241.5, 0.0)

        # Inject a temporal moving object the Track action will pick up.
        parent_server._temporal_objects.append({
            "id": 99099,
            "name": "test-target",
            "objectType": "test-809-target",
            "mobility": "moving",
            "_temporal": True,
            "transform": {"pos": list(target_xyz),
                          "scale": [400, 1700, 400]},
        })

        # Track action scoping to our fixture by trackFixtureIds.
        parent_server._actions.append({
            "id": 990809,
            "name": "test-809-track",
            "type": 18,
            "trackObjectType": "test-809-target",
            "trackFixtureIds": [TEST_FID],
            "trackDimmer": 220,
            "r": 255, "g": 0, "b": 0,
        })

        expected = _build_expected_pose(fx, target_xyz)
        _assert(expected is not None,
                f"sphere produces a pose for target {target_xyz}")

        # Run one tracker tick.
        parent_server._evaluate_track_actions(0.0, parent_server._artnet, [])

        actual = _read_pan_tilt_dmx16(fx)
        # Compare within ±2 LSB to absorb the float→0..1 norm round-trip
        # set_fixture_pan_tilt does internally.
        d_pan = abs(actual[0] - expected[0])
        d_tilt = abs(actual[1] - expected[1])
        _assert(d_pan <= 2,
                f"pan DMX matches sphere within rounding "
                f"(want {expected[0]}, got {actual[0]}, delta {d_pan})")
        _assert(d_tilt <= 2,
                f"tilt DMX matches sphere within rounding "
                f"(want {expected[1]}, got {actual[1]}, delta {d_tilt})")

        # Acceptance #3 — canonical aim_stage matches the operator's
        # intent direction (target - fixture position).
        canon = parent_server._get_canonical_aim_stage(TEST_FID)
        _assert(canon is not None,
                "canonical aim_stage populated by Track tick")
        if canon is not None:
            import math
            dx = target_xyz[0] - 600
            dy = target_xyz[1] - 0
            dz = target_xyz[2] - 1760
            n = math.sqrt(dx * dx + dy * dy + dz * dz)
            want = (dx / n, dy / n, dz / n)
            _assert(all(abs(canon[i] - want[i]) < 1e-3 for i in range(3)),
                    f"canonical aim direction matches target intent "
                    f"(want {want}, got {canon})")
    finally:
        _teardown_fixture()


def test_track_falls_back_to_geometric_for_underconfigured_fixture():
    """#809 acceptance #2: a fixture WITHOUT Home + Secondary still
    works via the geometric fallback (preserves pre-fix behaviour
    for unconfigured movers)."""
    _ensure_artnet_running()

    # Inject same as above but strip the canonical anchors.
    profile_doc = {
        "id": "test-mh-809-uncfg",
        "name": "Test MH 809 unconfig",
        "manufacturer": "Test",
        "category": "moving-head",
        "channelCount": 6,
        "panRange": 540,
        "tiltRange": 270,
        "channels": [
            {"offset": 0, "name": "Pan", "type": "pan", "bits": 16},
            {"offset": 2, "name": "Tilt", "type": "tilt", "bits": 16},
            {"offset": 4, "name": "Dimmer", "type": "dimmer"},
            {"offset": 5, "name": "Red", "type": "red"},
        ],
    }
    with parent_server.app.test_client() as c:
        c.post("/api/dmx-profiles", json=profile_doc)
    fx = {
        "id": 178100,
        "name": "Test Unconfigured 809",
        "fixtureType": "dmx",
        "dmxUniverse": 1,
        "dmxStartAddr": 50,
        "dmxChannelCount": 6,
        "dmxProfileId": "test-mh-809-uncfg",
        "panRange": 540,
        "tiltRange": 270,
        "rotation": [0, 0, 0],
        # Home / Secondary intentionally omitted.
    }
    parent_server._fixtures.append(fx)
    parent_server._layout.setdefault("children", []).append({
        "id": 178100, "x": 0, "y": 0, "z": 2000})
    parent_server._temporal_objects.append({
        "id": 99100,
        "name": "test-target-uncfg",
        "objectType": "test-809-uncfg",
        "mobility": "moving",
        "_temporal": True,
        "transform": {"pos": [1000, 1000, 0],
                      "scale": [400, 1700, 400]},
    })
    parent_server._actions.append({
        "id": 990810,
        "name": "test-809-track-uncfg",
        "type": 18,
        "trackObjectType": "test-809-uncfg",
        "trackFixtureIds": [178100],
        "trackDimmer": 220,
    })

    try:
        # Geometric fallback should succeed without raising.
        parent_server._evaluate_track_actions(0.0, parent_server._artnet, [])
        addr = fx["dmxStartAddr"]
        uni = parent_server._artnet.get_universe(1)
        # Some pan/tilt DMX should have landed (non-zero is enough — the
        # exact values depend on geometric math we're not re-asserting).
        pan_dmx = (uni.get_channel(addr + 0) << 8) | uni.get_channel(addr + 1)
        tilt_dmx = (uni.get_channel(addr + 2) << 8) | uni.get_channel(addr + 3)
        _assert(pan_dmx != 0 or tilt_dmx != 0,
                "geometric fallback wrote pan/tilt DMX for "
                f"unconfigured fixture (got {pan_dmx}, {tilt_dmx})")
    finally:
        parent_server._fixtures[:] = [
            f for f in parent_server._fixtures if f.get("id") != 178100]
        parent_server._layout["children"] = [
            c for c in parent_server._layout.get("children", [])
            if c.get("id") != 178100]
        parent_server._actions[:] = [
            a for a in parent_server._actions
            if a.get("name") != "test-809-track-uncfg"]
        parent_server._temporal_objects[:] = [
            o for o in parent_server._temporal_objects
            if o.get("id") != 99100]
        parent_server._clear_canonical_aim_stage(178100)


ALL = [
    test_track_writes_sphere_dmx_for_configured_fixture,
    test_track_falls_back_to_geometric_for_underconfigured_fixture,
]


if __name__ == "__main__":
    print("=== #809 Track action sphere routing ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
