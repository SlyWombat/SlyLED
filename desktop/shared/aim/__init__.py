"""desktop/shared/aim/ — moving-head aim/cal package (#784).

Self-contained rewrite of the moving-head aim subsystem. Replaces the
union of `coverage_math` IK functions, `sphere_model.py`, and
`mover_calibrator.py`'s aim helpers. Pure-Python, no Flask, no I/O at
the leaf modules; the only Flask coupling lives in `routes.py`.

Public surface:

    profile_mechanics.dmx_to_mechanical(panDmx16, tiltDmx16, profile)
        → (mech_pan_deg, mech_tilt_deg)

    stage_frame.mechanical_to_stage_aim(
        mech_pan_deg, mech_tilt_deg, fixture_rotation,
        home_mech_pan_deg, home_mech_tilt_deg)
        → (az_deg_stage, el_deg_stage)

    sphere.AimSphere(fixture, profile)
        .aim_xyz(target_xyz, current_pose=None)        → (panDmx16, tiltDmx16) | None
        .aim_direction(az_deg, el_deg, current_pose)   → (panDmx16, tiltDmx16) | None
        .direction_to_poses(az_deg, el_deg)            → list[(panDmx16, tiltDmx16)]
        .dmx_to_aim(panDmx16, tiltDmx16)               → (az_deg, el_deg)

The package never reads `mountedInverted`, `homeSecondary`,
`panMovedDirection`, `tiltMovedDirection`, `panSign*`, `tiltSign*`,
`tiltUp`, `tiltOffsetDmx16`, or `dmxToMechanical`. Profile mechanics
are derived from `panRange` + `tiltRange` + the fixture's home anchor;
stage-frame inversion lives in `fixture.rotation`. Architectural
commitments — see issue #784 (especially comment 3, 2026-05-02).
"""

__all__ = [
    "profile_mechanics",
    "stage_frame",
    "sphere",
    "routes",
]
