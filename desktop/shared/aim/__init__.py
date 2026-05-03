"""desktop/shared/aim/ — moving-head aim/cal package (#784, #785, #798, #799).

Self-contained rewrite of the moving-head aim subsystem. Pure-Python,
no Flask, no I/O at the leaf modules; the only Flask coupling lives in
`routes.py`.

Public surface (slope-from-home model, #799):

    sphere.AimSphere(fixture, profile)
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
        .dmx_to_aim(panDmx16, tiltDmx16)                # analytical inverse
            → (az_deg, el_deg)

Construction inputs (all required — fail-fast `ValueError` otherwise):

    fixture.x / y / z                          (xyz from layout merge)
    fixture.rotation                           (mount orientation)
    fixture.homePanDmx16 / homeTiltDmx16       (Home anchor)
    fixture.homeSecondary.{
        panMovedDirection, tiltMovedDirection,
        panOffsetDmx16,    tiltOffsetDmx16
    }                                          (sign derivation)
    profile.panRange / tiltRange               (slope magnitudes)

The math: `slope = signed × 65535 / range` per axis (sign from the
operator's secondary direction call combined with the wizard's signed
DMX offset). Aim formula: `target_dmx = home_dmx + slope × (target_stage
- home_stage)` where `home_stage` is the stage-frame `(az, el)` direction
the beam aims at when DMX is at home (derived from `fixture.rotation`
applied to mount-frame +Y forward at construction).

Multi-valued azimuth (panRange > 360°) enumerated per branch
(`pan_mech_target + k×360°` for `k ∈ {-2..+2}`); branches whose computed
DMX falls outside `[0, 65535]` get dropped.

`fixture.rotation` carries MOUNT BODY orientation only —
`[0, 0, 0]` for an upright pendant, `[0, 180, 0]` for an inverted truss
mount, `[-75, 0, 0]` for a fixture pre-tilted up at home, etc.

Architectural commitments — see issues #784 (esp. comment 3, 2026-05-02),
#785 (sphere PR-3), #798 (anchor-bracketing experiment, superseded),
and #799 (slope-from-home, current).
"""

__all__ = [
    "park",
    "profile_mechanics",
    "stage_frame",
    "sphere",
    "routes",
]
