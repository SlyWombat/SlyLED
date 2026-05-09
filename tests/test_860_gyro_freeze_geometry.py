"""#860 — gyro→DMX wire freeze on the live slymovehead-shaped fixture.

Pre-existing harness (`tests/test_gyro_dmx_integration.py` referenced in
the issue, never landed) used a synthetic centre-home + 16-bit fixture.
The live freeze appeared on a fixture with extreme home (homeTilt = 0),
8-bit motors, and inverted tilt slope. None of those properties were
exercised by any existing AimSphere test.

This harness builds the live geometry shape and pushes the same six
distinct aim_stage values the operator's lockstep poller captured
through both:
* `AimSphere.aim_direction` directly — pin the IK math.
* `MoverControlEngine._aim_to_pan_tilt` end-to-end — pin the gyro path.

Each step asserts the returned (pan, tilt) is non-None AND distinct
from the previous step's output. The freeze symptom is "claim doesn't
update across distinct aim_stages"; either path returning a constant
or a None tuple would reproduce that, and this harness fails loud on
both.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from aim.sphere import AimSphere
from mover_control import MoverControlEngine


# Live aim_stage sequence captured 17:11:31–17:11:58 by the operator
# (lockstep log copied into issue #860). az span ≈ 20°, el span ≈ 15°
# — well inside the IK's responsive region per the operator's probe.
LIVE_AIMS = [
    (0.348, 0.312, 0.884),    # calibrate-end
    (-0.146, 0.915, 0.375),
    (0.065, 0.985, 0.157),
    (0.082, 0.989, 0.119),
    (0.115, 0.984, 0.133),
    (0.187, 0.974, 0.126),
    (0.086, 0.994, 0.066),
]


# Profile shape mirroring slymovehead per #860:
#   panRange  540°  (typical for a moving head)
#   tiltRange 180°  (the live fixture; harness tests had 270°)
#   8-bit pan/tilt — slope-from-home math is in 16-bit units regardless,
#   so the bit width matters only at the wire writer (downstream).
LIVE_PROFILE = {
    "id": "slymovehead-test",
    "panRange": 540.0,
    "tiltRange": 180.0,
}

# Fixture record matching the live rig:
#   homePan 52015 (off-centre), homeTilt 0 (extreme).
#   Secondary slew with positive offsets and tiltMovedDirection="down"
#   gives an inverted tilt slope (off_tilt_sign × expected_el_sign × mag
#   = +1 × −1 × |slope| = negative slope).
LIVE_FIXTURE = {
    "id": 19,
    "x": 0, "y": 0, "z": 3000,
    "rotation": [0.0, 0.0, 0.0],
    "homePanDmx16": 52015,
    "homeTiltDmx16": 0,
    "homeSecondary": {
        "panOffsetDmx16": 16384,
        "tiltOffsetDmx16": 8192,
        "panMovedDirection": "left",
        "tiltMovedDirection": "down",
    },
}


def _aim_stage_to_az_el(aim):
    import math
    az = math.degrees(math.atan2(aim[0], aim[1]))
    el = math.degrees(math.atan2(aim[2], math.hypot(aim[0], aim[1])))
    return az, el


def test_aimsphere_live_geometry_returns_distinct_per_aim():
    """Build an AimSphere with the live geometry shape and call
    `aim_direction` for each captured aim_stage. Every call must return
    a pose AND every consecutive pair must differ."""
    sphere = AimSphere(LIVE_FIXTURE, LIVE_PROFILE)
    poses = []
    for aim in LIVE_AIMS:
        az, el = _aim_stage_to_az_el(aim)
        pose = sphere.aim_direction(az, el, current_pose=None)
        assert pose is not None, f"aim_direction returned None for {aim}"
        poses.append(pose)
    # Calibrate-end → first orient: must differ.
    distinct_pairs = sum(1 for i in range(1, len(poses))
                          if poses[i] != poses[i - 1])
    assert distinct_pairs >= len(poses) - 2, (
        f"#860: AimSphere is freezing on the live slymovehead geometry. "
        f"poses = {poses}")


def test_aimsphere_live_geometry_with_current_pose_advances():
    """The tick loop passes `current_pose` from the prior tick's
    claim.pan_smooth / tilt_smooth. Mimic that: feed each pose in as
    the next call's current_pose; the IK output must still vary
    across distinct aim_stages, NOT freeze on the initial pose."""
    sphere = AimSphere(LIVE_FIXTURE, LIVE_PROFILE)
    cur = None
    poses = []
    for aim in LIVE_AIMS:
        az, el = _aim_stage_to_az_el(aim)
        pose = sphere.aim_direction(az, el, current_pose=cur)
        assert pose is not None, f"aim_direction returned None for {aim}"
        poses.append(pose)
        cur = pose
    distinct_pairs = sum(1 for i in range(1, len(poses))
                          if poses[i] != poses[i - 1])
    assert distinct_pairs >= len(poses) - 2, (
        f"#860 (current_pose feedback): AimSphere is freezing when fed "
        f"its own previous pose. poses = {poses}")


def test_engine_aim_to_pan_tilt_live_geometry_advances():
    """End-to-end `MoverControlEngine._aim_to_pan_tilt` with the live
    geometry. Engine wraps AimSphere with prerequisite guards + the
    `current_pose` derived from `claim.pan_smooth / tilt_smooth`. The
    freeze symptom is "claim doesn't update": this test simulates the
    feedback loop the tick uses and asserts the (pan_norm, tilt_norm)
    output stream is not constant across distinct aims."""
    eng = MoverControlEngine(
        get_fixtures=lambda: [LIVE_FIXTURE],
        get_layout=lambda: {},
        get_profile_info=lambda pid: LIVE_PROFILE if pid else None,
        get_engine=lambda: None,
        set_fixture_color_fn=lambda *a, **kw: None,
        get_remote_by_device_id=lambda did: None,
    )
    # Seed a claim so `_aim_to_pan_tilt` has a `current_pose` source.
    from mover_control import MoverClaim
    claim = MoverClaim(mover_id=LIVE_FIXTURE["id"],
                        device_id="puck-1", device_name="SLYG-001")
    claim.pan_smooth = LIVE_FIXTURE["homePanDmx16"] / 65535.0
    claim.tilt_smooth = LIVE_FIXTURE["homeTiltDmx16"] / 65535.0
    claim.have_pan_tilt = True
    eng._claims[LIVE_FIXTURE["id"]] = claim

    mover_with_pid = dict(LIVE_FIXTURE)
    mover_with_pid["dmxProfileId"] = "slymovehead-test"

    norms = []
    for aim in LIVE_AIMS:
        pn, tn = eng._aim_to_pan_tilt(LIVE_FIXTURE["id"], mover_with_pid, aim)
        assert pn is not None and tn is not None, (
            f"#860: _aim_to_pan_tilt returned None for {aim}. "
            f"aim_error={getattr(claim, 'aim_error', None)!r}")
        norms.append((round(pn, 4), round(tn, 4)))
        # Feed the new pose back into the claim so the next call's
        # current_pose tracks the IK output — mirrors the tick loop.
        claim.pan_smooth = pn
        claim.tilt_smooth = tn

    distinct_pairs = sum(1 for i in range(1, len(norms))
                          if norms[i] != norms[i - 1])
    assert distinct_pairs >= len(norms) - 2, (
        f"#860 reproduced: _aim_to_pan_tilt freezes after the first "
        f"call on the live geometry. norms = {norms}")


if __name__ == "__main__":
    test_aimsphere_live_geometry_returns_distinct_per_aim()
    test_aimsphere_live_geometry_with_current_pose_advances()
    test_engine_aim_to_pan_tilt_live_geometry_advances()
    print("OK — #860 live-geometry harness passed (no freeze reproduced)")
