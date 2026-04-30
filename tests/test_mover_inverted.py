#!/usr/bin/env python3
"""test_mover_inverted.py — #760 regression: inverted mount must AIM AT THE
SAME world point as an upright mount when given the same stage-frame aim.

The bug-shaped invariant: ``aim_stage`` is a stage-frame unit vector — the
direction the beam should go in the world. Whether the head is upright or
upside-down, that direction is the same; only the motor commands needed to
reach it change. So for an inverted-mount fixture, we expect:

    fixture_aim_to_world(_aim_to_pan_tilt(aim_stage)) ≈ aim_stage

just like for an upright fixture. Without consuming ``mountedInverted`` in
the world↔fixture transforms, the inverted fixture aims somewhere else.

Earlier shape of this test (post-flip on pan_norm/tilt_norm) was wrong: it
mirrored DMX about 0.5, which only happens to land on the right answer if
Home is at the mechanical centre. With a real Home pose like
``homeTiltDmx16=0`` the symmetric flip puts the beam in a totally
different direction.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from mover_control import MoverControlEngine  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


def _make_engine(get_fixtures):
    return MoverControlEngine(
        get_fixtures=get_fixtures,
        get_layout=lambda: {},
        get_profile_info=lambda pid: None,
        get_engine=lambda: None,
        set_fixture_color_fn=lambda *a, **kw: None,
        get_remote_by_device_id=lambda did: None,
    )


def _norm_to_pan_tilt_deg(pan_norm, tilt_norm, pan_range=540.0, tilt_range=270.0):
    """norm → fixture-internal degrees (panRange/2 ↔ ±panRange/2)."""
    pan_deg = (pan_norm - 0.5) * pan_range
    tilt_deg = (tilt_norm - 0.5) * tilt_range
    return pan_deg, tilt_deg


def _fixture_pan_tilt_to_world_aim(pan_norm, tilt_norm, rotation,
                                    pan_range=540.0, tilt_range=270.0):
    """Round-trip helper: pan/tilt norms → stage-frame unit aim using the
    same convention as `aim_to_pan_tilt`'s inverse (mount [0,1,0] forward
    rotated by R(rotation))."""
    import math
    from remote_math import euler_xyz_deg_to_matrix, matrix_vec_mul
    pan_deg, tilt_deg = _norm_to_pan_tilt_deg(pan_norm, tilt_norm,
                                              pan_range, tilt_range)
    pr = math.radians(pan_deg); tr = math.radians(tilt_deg)
    # `aim_to_pan_tilt` builds aim_mount with: dx, dy, dz where
    #   pan_deg = atan2(dx, dy), tilt_deg = atan2(-dz, hypot(dx, dy))
    # Inverse: dx = sin(pan)cos(tilt); dy = cos(pan)cos(tilt); dz = -sin(tilt)
    aim_mount = (math.sin(pr) * math.cos(tr),
                 math.cos(pr) * math.cos(tr),
                 -math.sin(tr))
    R = euler_xyz_deg_to_matrix(rotation or [0, 0, 0])
    return matrix_vec_mul(R, aim_mount)


def test_no_cal_path_inverted_aims_at_same_world_target():
    """Path 3 (no calibration, generic IK): given the same stage-frame
    aim, an inverted-mount fixture must land its beam on the same world
    direction as an upright one (the DMX numbers will differ; the world
    aim must not).
    """
    upright = {
        "id": 1, "fixtureType": "dmx",
        "x": 0, "y": 0, "z": 3000,
        "rotation": [0.0, 0.0, 0.0],
        "panRange": 540, "tiltRange": 270,
        "mountedInverted": False,
    }
    inverted = dict(upright); inverted["id"] = 2
    inverted["mountedInverted"] = True

    eng = _make_engine(lambda: [upright, inverted])

    # A non-trivial aim direction: down-and-stage-right.
    import math
    aim = (0.5, 0.6, -0.6)
    norm = math.sqrt(aim[0] ** 2 + aim[1] ** 2 + aim[2] ** 2)
    aim = (aim[0] / norm, aim[1] / norm, aim[2] / norm)

    pn_up, tn_up = eng._aim_to_pan_tilt(1, upright, aim)
    pn_in, tn_in = eng._aim_to_pan_tilt(2, inverted, aim)

    # Round-trip both back to world aim using their (effective) rotation.
    aim_up = _fixture_pan_tilt_to_world_aim(pn_up, tn_up, [0.0, 0.0, 0.0])
    rot_inv_eff = [0.0, 180.0, 0.0]  # what _aim_to_pan_tilt feeds for inverted
    aim_in = _fixture_pan_tilt_to_world_aim(pn_in, tn_in, rot_inv_eff)

    # Both should reproduce the input aim (within IK numerical tolerance).
    for c, label in zip(range(3), ["x", "y", "z"]):
        _assert(abs(aim_up[c] - aim[c]) < 1e-3,
                f"upright aim[{label}] round-trips: {aim_up[c]:.4f} vs {aim[c]:.4f}")
        _assert(abs(aim_in[c] - aim[c]) < 1e-3,
                f"inverted aim[{label}] round-trips: {aim_in[c]:.4f} vs {aim[c]:.4f}")


def test_inverted_dmx_differs_from_upright():
    """The DMX numbers (pan/tilt norms) for upright vs inverted SHOULD
    differ for any aim that is not directly along the rotation axis —
    inverting the mount changes the motor commands required to hit the
    same world direction."""
    upright = {
        "id": 1, "fixtureType": "dmx",
        "x": 0, "y": 0, "z": 3000,
        "rotation": [0.0, 0.0, 0.0],
        "panRange": 540, "tiltRange": 270,
        "mountedInverted": False,
    }
    inverted = dict(upright); inverted["id"] = 2
    inverted["mountedInverted"] = True

    eng = _make_engine(lambda: [upright, inverted])
    aim = (0.4, 0.7, -0.6)  # off-axis
    pn_up, tn_up = eng._aim_to_pan_tilt(1, upright, aim)
    pn_in, tn_in = eng._aim_to_pan_tilt(2, inverted, aim)
    differs = (abs(pn_up - pn_in) > 0.01) or (abs(tn_up - tn_in) > 0.01)
    _assert(differs, f"upright vs inverted DMX must differ for off-axis aim "
                     f"(got pn:{pn_up:.4f}/{pn_in:.4f} tn:{tn_up:.4f}/{tn_in:.4f})")


def test_no_mountedinverted_field_defaults_false():
    """Fixtures without an explicit mountedInverted should behave exactly
    as mountedInverted=False (no NPE, no off-by-half artefact)."""
    a = {
        "id": 1, "fixtureType": "dmx",
        "x": 0, "y": 0, "z": 3000,
        "rotation": [0.0, 0.0, 0.0],
        "panRange": 540, "tiltRange": 270,
    }  # NO mountedInverted key
    b = dict(a); b["id"] = 2; b["mountedInverted"] = False

    eng = _make_engine(lambda: [a, b])
    aim = (0.4, 0.5, -0.7)
    pn_a, tn_a = eng._aim_to_pan_tilt(1, a, aim)
    pn_b, tn_b = eng._aim_to_pan_tilt(2, b, aim)
    _assert(abs(pn_a - pn_b) < 1e-12 and abs(tn_a - tn_b) < 1e-12,
            "missing mountedInverted ≡ False")


ALL = [
    test_no_cal_path_inverted_aims_at_same_world_target,
    test_inverted_dmx_differs_from_upright,
    test_no_mountedinverted_field_defaults_false,
]


if __name__ == "__main__":
    for t in ALL:
        t()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
