"""desktop/shared/aim/ — moving-head aim/cal package (#784, #785, #798).

Self-contained rewrite of the moving-head aim subsystem. Replaces the
union of `coverage_math` IK functions, `sphere_model.py`, and
`mover_calibrator.py`'s aim helpers. Pure-Python, no Flask, no I/O at
the leaf modules; the only Flask coupling lives in `routes.py`.

Public surface:

    profile_mechanics.dmx_to_mechanical(panDmx16, tiltDmx16, profile)
        → (mech_pan_deg, mech_tilt_deg)

    stage_frame.mechanical_to_stage_aim(
        mech_pan_deg, mech_tilt_deg, fixture_rotation)
        → (az_deg_stage, el_deg_stage)

    anchors.CalibrationAnchor(pan_dmx16, tilt_dmx16, az_deg, el_deg)
    anchors.collect_anchors(fixture, profile)
        → list[CalibrationAnchor]
    anchors.derive_legacy_anchors(fixture, profile)
        → list[CalibrationAnchor]   # bootstrap from rotation + L/R/U/D

    sphere.AimSphere(fixture, profile, *, anchors=None)
        .aim_xyz(target_xyz, current_pose=None, prefer="closest")
            → (panDmx16, tiltDmx16) | None
        .aim_direction(az_deg, el_deg, current_pose=None, prefer="closest")
            → (panDmx16, tiltDmx16) | None
        .poses_for_direction(az_deg, el_deg)
            → list[(panDmx16, tiltDmx16, branch_id)]
        .poses_for_xyz(target_xyz)
            → list[(panDmx16, tiltDmx16, branch_id)]
        .direction_to_poses(az_deg, el_deg)             # legacy compat
            → list[(panDmx16, tiltDmx16)]
        .dmx_to_aim(panDmx16, tiltDmx16)
            → (az_deg, el_deg)

The package never reads `mountedInverted`, `panSign*`, `tiltSign*`,
`tiltUp`, or `dmxToMechanical`. Stage-frame inversion lives in
`fixture.rotation`. Per-axis slope is local (computed at aim time
from the bracketing pair of `CalibrationAnchor` observations); there
is no global `pan_sign` / `tilt_sign` instance attribute.

Architectural commitments — see issues #784 (especially comment 3,
2026-05-02) and #798 (anchor-based geometry rewrite, 2026-05-03).
"""

__all__ = [
    "anchors",
    "park",
    "profile_mechanics",
    "stage_frame",
    "sphere",
    "routes",
]
