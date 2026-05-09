"""#851 acceptance — claim.panNorm/tiltNorm refresh from orient packets.

Bug: head locks at the first computed pose despite continuous puck
movement. droppedWrites=0, claim.calibrated=True, but panNorm doesn't
move while the puck sweeps through degrees of orient.

This harness drives three distinct (roll, pitch, yaw) tuples through
the actual `Remote.update_from_euler_deg` → `aim_stage` →
`MoverControlEngine._tick` chain (the same path the live UDP handler
runs). Asserts:

  * After each orient update, `claim.pan_smooth` differs from the
    prior tick's value (puck moves → head moves).
  * Three identical orients produce stable panNorm (no IK jitter).

If the freeze is in any code under test, this harness fails loud. If
it passes, the production freeze is environmental (live fixture record
shape, threading, or a code path the synthetic harness doesn't exercise)
— operator can capture the WARNING-level logs already promoted at
mover_control.py:800 / :478 / :424 to surface the exception text on
the next live run.
"""

import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from mover_control import MoverControlEngine, MoverClaim
from remote_orientation import Remote, KIND_PUCK


# Synthetic profile + fixture matching the live slymovehead-shaped rig:
# 540° pan, 180° tilt, off-centre home, inverted tilt slope.
LIVE_PROFILE = {
    "id": "slymovehead-test",
    "panRange": 540.0,
    "tiltRange": 180.0,
}
LIVE_FIXTURE = {
    "id": 19,
    "fixtureType": "dmx",
    "dmxProfileId": "slymovehead-test",
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


def _build_engine(remote):
    eng = MoverControlEngine(
        get_fixtures=lambda: [LIVE_FIXTURE],
        get_layout=lambda: {},
        get_profile_info=lambda pid: LIVE_PROFILE if pid else None,
        get_engine=lambda: None,
        set_fixture_color_fn=lambda *a, **kw: None,
        get_remote_by_device_id=lambda did: remote if did == remote.device_id else None,
    )
    return eng


def _seed_streaming_claim(eng, remote):
    """Set up an active claim in the same shape `start_stream` produces.
    Avoids the network/lock plumbing in the public claim API."""
    claim = MoverClaim(
        mover_id=LIVE_FIXTURE["id"],
        device_id=remote.device_id,
        device_name="SLYG-001",
    )
    claim.state = "streaming"
    claim.calibrated_here = True       # operator pressed calibrate-end
    claim.have_pan_tilt = False         # first orient seeds smoothing
    claim.smoothing = 0.15
    eng._claims[LIVE_FIXTURE["id"]] = claim
    return claim


def test_851_three_distinct_orients_advance_panNorm():
    """Acceptance per #851: three distinct orient tuples → three
    distinct panNorm values on the claim."""
    remote = Remote(id=1, name="puck", kind=KIND_PUCK, device_id="gyro-test")
    # Calibrate against an arbitrary in-cone target so R_world_to_stage
    # is set and `aim_stage` derives from `last_quat_world`.
    remote.update_from_euler_deg(0, 0, 0)
    remote.calibrate(target_aim_stage=(0.0, 1.0, 0.0))  # target due-forward

    eng = _build_engine(remote)
    claim = _seed_streaming_claim(eng, remote)

    # Three distinct orients within the puck's normal travel. Vary BOTH
    # pitch (drives tilt) AND yaw (drives az → pan) so the test
    # exercises both axes — pure-pitch sweeps would legitimately keep
    # az=0 and pan_norm constant, masking a real freeze.
    orients = [
        (0.0,  10.0,  20.0),
        (0.0,  30.0, -15.0),
        (0.0, -20.0,  40.0),
    ]
    panNorms = []
    tiltNorms = []
    for roll, pitch, yaw in orients:
        remote.update_from_euler_deg(roll, pitch, yaw)
        eng._tick()
        panNorms.append(round(claim.pan_smooth, 4))
        tiltNorms.append(round(claim.tilt_smooth, 4))

    # All three should differ from each other (within tolerance).
    distinct_pan = len(set(panNorms)) == len(panNorms)
    distinct_tilt = len(set(tiltNorms)) >= 2  # tilt may collide on
                                                  # one pair if symmetric
    assert distinct_pan, (
        f"#851: panNorm did not advance across distinct orients. "
        f"Values: {panNorms}. Expected three distinct values.")
    assert distinct_tilt, (
        f"#851: tiltNorm collapsed across distinct orients. "
        f"Values: {tiltNorms}. Expected ≥2 distinct values.")


def test_851_identical_orients_produce_stable_panNorm():
    """No-jitter check: feeding the same orient twice in a row produces
    a stable panNorm (smoothing converges to the same value)."""
    remote = Remote(id=2, name="puck2", kind=KIND_PUCK, device_id="gyro-test2")
    remote.update_from_euler_deg(0, 0, 0)
    remote.calibrate(target_aim_stage=(0.0, 1.0, 0.0))

    eng = _build_engine(remote)
    claim = _seed_streaming_claim(eng, remote)

    remote.update_from_euler_deg(0, 25, 0)
    eng._tick()
    pan_first = claim.pan_smooth
    # Tick again WITHOUT touching the remote — same aim_stage. Smoothing
    # should converge to the same target; with alpha=0.85 the first tick
    # already lands close, the second tick sands off the residual.
    eng._tick()
    pan_second = claim.pan_smooth
    eng._tick()
    pan_third = claim.pan_smooth

    # second and third should be within ~0.001 of each other (smoothing
    # of a constant target).
    assert abs(pan_third - pan_second) < 0.001, (
        f"#851 jitter: identical orient ticks produced moving panNorm. "
        f"first={pan_first} second={pan_second} third={pan_third}")


def test_851_first_tick_sets_have_pan_tilt():
    """The first tick post-calibrate must flip claim.have_pan_tilt to
    True and seed pan_smooth/tilt_smooth from the IK output (no
    smoothing yet — direct assignment per mover_control.py:670-673)."""
    remote = Remote(id=3, name="puck3", kind=KIND_PUCK, device_id="gyro-test3")
    remote.update_from_euler_deg(0, 0, 0)
    remote.calibrate(target_aim_stage=(0.0, 1.0, 0.0))

    eng = _build_engine(remote)
    claim = _seed_streaming_claim(eng, remote)

    assert claim.have_pan_tilt is False, "precondition: have_pan_tilt starts False"
    remote.update_from_euler_deg(0, 15, 0)
    eng._tick()
    assert claim.have_pan_tilt is True, (
        "first tick post-calibrate must set have_pan_tilt=True")
    # And pan_smooth must be a real value, not the 0.5 init seed.
    assert claim.pan_smooth != 0.5 or claim.tilt_smooth != 0.5, (
        f"first tick must seed pan_smooth/tilt_smooth from IK; "
        f"got {claim.pan_smooth}/{claim.tilt_smooth} (still at 0.5 init)")


if __name__ == "__main__":
    test_851_three_distinct_orients_advance_panNorm()
    test_851_identical_orients_produce_stable_panNorm()
    test_851_first_tick_sets_have_pan_tilt()
    print("OK — #851 orient → panNorm refresh acceptance tests passed")
