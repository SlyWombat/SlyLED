"""aim/anchors.py — operator-confirmed `(DMX, stage_aim)` calibration
anchors that drive the bracketing-interpolation sphere model (#798).

A ``CalibrationAnchor`` is a single observation: at DMX pose
``(panDmx16, tiltDmx16)`` the beam aimed at stage direction
``(az_deg, el_deg)``. Three anchors is the architectural minimum
(Home + one pan slew + one tilt slew); operators may capture more in a
"Verify and refine" pass to harden the model across the DMX range.

Per #798's correction note: slope is local, not global. The sphere
computes deg/DMX fresh from the bracketing pair of anchors at aim time
— there is no per-axis ``pan_sign`` / ``tilt_sign`` instance attribute,
each segment carries its own implicit sign from the anchor delta.

Pre-#798 fixtures that don't carry explicit aimStage data still need
to be aimable; ``derive_legacy_anchors()`` synthesises a 3-anchor list
from the existing ``fixture.rotation`` + ``homeSecondary`` direction
labels + offsets so the new model produces byte-equivalent output
to the old rotation-derived sphere for those fixtures. Operators
unlock the corrected geometry by re-running Save Home once the SPA
wizard captures explicit aimStage values.

Pure functions; no Flask, no I/O.
"""

from collections import namedtuple
import math

from ._rotmat import mount_rotation as _mount_rotation
from ._rotmat import matvec as _matvec
from ._rotmat import transpose as _transpose


CalibrationAnchor = namedtuple(
    "CalibrationAnchor",
    ["pan_dmx16", "tilt_dmx16", "az_deg", "el_deg"],
)


def stage_to_mount(az_deg, el_deg, fixture_rotation):
    """Project a stage-frame direction back to the fixture's mount
    frame using only the mount-body orientation rotation matrix.
    Returns the unit vector ``(mx, my, mz)`` in mount coordinates."""
    ar = math.radians(float(az_deg))
    er = math.radians(float(el_deg))
    cer = math.cos(er)
    stage_aim = (math.sin(ar) * cer,
                  math.cos(ar) * cer,
                  math.sin(er))
    R = _mount_rotation(fixture_rotation or [0.0, 0.0, 0.0])
    return _matvec(_transpose(R), stage_aim)


def mount_to_stage(mount_aim, fixture_rotation):
    """Inverse of `stage_to_mount` — mount-frame unit vector to
    stage-frame `(az_deg, el_deg)`. Gimbal lock at the zenith
    (`el = ±90°`) leaves `az` degenerate; caller treats as a no-op."""
    R = _mount_rotation(fixture_rotation or [0.0, 0.0, 0.0])
    sx, sy, sz = _matvec(R, mount_aim)
    az_deg = math.degrees(math.atan2(sx, sy))
    el_deg = math.degrees(math.atan2(sz, math.hypot(sx, sy)))
    return az_deg, el_deg


def mech_from_mount(mount_aim):
    """Decompose a mount-frame unit vector into mechanical pan/tilt
    angles. ``+pan_mech`` rotates toward mount ``+X``;
    ``+tilt_mech`` rotates toward mount ``+Z``. Inverse of the
    mount-aim-from-mech construction in stage_frame."""
    mx, my, mz = mount_aim
    pan_mech = math.degrees(math.atan2(mx, my))
    tilt_mech = math.degrees(math.atan2(mz, math.hypot(mx, my)))
    return pan_mech, tilt_mech


def mount_from_mech(pan_mech_deg, tilt_mech_deg):
    """Inverse of `mech_from_mount`. Mount-frame unit vector for
    a given mechanical pose."""
    pr = math.radians(float(pan_mech_deg))
    tr = math.radians(float(tilt_mech_deg))
    ctr = math.cos(tr)
    return (math.sin(pr) * ctr,
            math.cos(pr) * ctr,
            math.sin(tr))


def derive_legacy_anchors(fixture, profile):
    """Synthesise a 3-anchor list from a pre-#798 fixture's existing
    `rotation` + `homeSecondary` direction labels + offsets.

    Behaviour matches the old rotation-derived sphere by construction
    so legacy fixtures keep producing the same DMX outputs through the
    new model. Operators get the corrected geometry once they re-run
    Save Home and the wizard captures explicit aimStage values
    (`fixture.homeAimStage`, `homeSecondary.panAimStageAfter`,
    `homeSecondary.tiltAimStageAfter`).

    Raises ValueError if the fixture/profile lack the data needed.
    """
    rot = fixture.get("rotation") or [0.0, 0.0, 0.0]
    rot = [float(rot[0]) if len(rot) > 0 else 0.0,
           float(rot[1]) if len(rot) > 1 else 0.0,
           float(rot[2]) if len(rot) > 2 else 0.0]

    h_pan = fixture.get("homePanDmx16")
    h_tilt = fixture.get("homeTiltDmx16")
    if h_pan is None or h_tilt is None:
        raise ValueError(
            f"fixture {fixture.get('id', '<unknown>')} has no Home "
            "anchor — set Home before deriving anchors")
    h_pan = int(h_pan)
    h_tilt = int(h_tilt)
    if not (0 <= h_pan <= 65535) or not (0 <= h_tilt <= 65535):
        raise ValueError(
            f"fixture {fixture.get('id', '<unknown>')} home DMX out of "
            "[0, 65535]")

    pan_range = profile.get("panRange")
    tilt_range = profile.get("tiltRange")
    if not pan_range or not tilt_range:
        raise ValueError(
            f"profile {profile.get('id', '<unknown>')!r} has no "
            "pan/tilt range — not a moving head")
    pan_range = float(pan_range)
    tilt_range = float(tilt_range)

    sec = fixture.get("homeSecondary") or {}
    pan_dir = sec.get("panMovedDirection")
    tilt_dir = sec.get("tiltMovedDirection")
    pan_off = sec.get("panOffsetDmx16")
    tilt_off = sec.get("tiltOffsetDmx16")
    if (pan_dir not in ("left", "right")
            or tilt_dir not in ("up", "down")
            or not pan_off or not tilt_off):
        raise ValueError(
            f"fixture {fixture.get('id', '<unknown>')} has no "
            "homeSecondary direction calls + offsets — required to "
            "bootstrap calibration anchors for a pre-#798 fixture")
    pan_off = int(pan_off)
    tilt_off = int(tilt_off)

    # Home anchor — beam aims along the rotation-forward vector.
    # mount-frame +Y rotated by R is the home aim direction in stage
    # frame for an operator who saved home with the head pointing
    # straight along its mount-body forward axis.
    home_az, home_el = mount_to_stage((0.0, 1.0, 0.0), rot)

    # Pan slew anchor — mech_pan delta from DMX offset.
    pan_mech_delta = (pan_off / 65535.0) * pan_range
    # Direction-label sign disambiguation — assume sign=+1 first, see
    # which way stage az moves, flip if mismatched against the
    # operator's call. Reuses the #785 QA derivation logic.
    sim_mount = mount_from_mech(+pan_mech_delta, 0.0)
    sim_az, _sim_el = mount_to_stage(sim_mount, rot)
    expected_az_sign = -1 if pan_dir == "right" else +1
    actual_az_sign = +1 if sim_az > 0 else (-1 if sim_az < 0 else +1)
    pan_sign = +1 if expected_az_sign == actual_az_sign else -1
    pan_mount = mount_from_mech(pan_sign * pan_mech_delta, 0.0)
    pan_az, pan_el = mount_to_stage(pan_mount, rot)

    # Tilt slew anchor — mech_tilt delta from DMX offset.
    tilt_mech_delta = (tilt_off / 65535.0) * tilt_range
    sim_mount = mount_from_mech(0.0, +tilt_mech_delta)
    _sim_az, sim_el = mount_to_stage(sim_mount, rot)
    expected_el_sign = -1 if tilt_dir == "down" else +1
    actual_el_sign = +1 if sim_el > 0 else (-1 if sim_el < 0 else +1)
    tilt_sign = +1 if expected_el_sign == actual_el_sign else -1
    tilt_mount = mount_from_mech(0.0, tilt_sign * tilt_mech_delta)
    tilt_az, tilt_el = mount_to_stage(tilt_mount, rot)

    return [
        CalibrationAnchor(h_pan, h_tilt, home_az, home_el),
        CalibrationAnchor(h_pan + pan_sign * pan_off, h_tilt,
                            pan_az, pan_el),
        CalibrationAnchor(h_pan, h_tilt + tilt_sign * tilt_off,
                            tilt_az, tilt_el),
    ]


def collect_anchors(fixture, profile):
    """Build the anchor list for a fixture. Prefers explicit aimStage
    captures (post-#798 SPA wizard) over the legacy-derived bootstrap.

    Operator-captured fields:
        fixture["homeAimStage"] = [az_deg, el_deg]
        fixture["homeSecondary"]["panAimStageAfter"]  = [az_deg, el_deg]
        fixture["homeSecondary"]["tiltAimStageAfter"] = [az_deg, el_deg]
        fixture["calibrationAnchors"] = [
            {panDmx16, tiltDmx16, azDeg, elDeg},
            ...                        # additional Verify-and-refine captures
        ]

    Returns the full list — Home first, then secondary anchors, then
    any extras. Raises ValueError when neither path can produce ≥3
    anchors (Home + 2 secondary observations).
    """
    h_pan = fixture.get("homePanDmx16")
    h_tilt = fixture.get("homeTiltDmx16")
    if h_pan is None or h_tilt is None:
        raise ValueError(
            f"fixture {fixture.get('id', '<unknown>')} has no Home "
            "anchor — set Home before collecting anchors")
    h_pan = int(h_pan)
    h_tilt = int(h_tilt)

    home_aim = fixture.get("homeAimStage")
    sec = fixture.get("homeSecondary") or {}
    pan_after = sec.get("panAimStageAfter")
    tilt_after = sec.get("tiltAimStageAfter")
    pan_off = sec.get("panOffsetDmx16")
    tilt_off = sec.get("tiltOffsetDmx16")

    if (home_aim and pan_after and tilt_after
            and pan_off and tilt_off):
        # Post-#798 explicit-capture path — use operator-typed values.
        pan_dir = sec.get("panMovedDirection")
        tilt_dir = sec.get("tiltMovedDirection")
        pan_sign = -1 if pan_dir == "right" else +1
        tilt_sign = -1 if tilt_dir == "down" else +1
        anchors = [
            CalibrationAnchor(h_pan, h_tilt,
                                float(home_aim[0]), float(home_aim[1])),
            CalibrationAnchor(h_pan + pan_sign * int(pan_off), h_tilt,
                                float(pan_after[0]), float(pan_after[1])),
            CalibrationAnchor(h_pan, h_tilt + tilt_sign * int(tilt_off),
                                float(tilt_after[0]), float(tilt_after[1])),
        ]
    else:
        # Legacy path — bootstrap from rotation + direction labels.
        anchors = derive_legacy_anchors(fixture, profile)

    # Operator-captured extras from a Verify-and-refine pass.
    for extra in (fixture.get("calibrationAnchors") or []):
        try:
            anchors.append(CalibrationAnchor(
                int(extra["panDmx16"]),
                int(extra["tiltDmx16"]),
                float(extra["azDeg"]),
                float(extra["elDeg"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    return anchors
