"""aim/stage_frame.py — mechanical ↔ stage-frame aim conversion (#784).

Composes mechanical angles (mount-frame, with the home pose at
`mech (0, 0)` per `profile_mechanics`) with `fixture.rotation` to
produce stage-frame aim direction `(az_deg, el_deg)` per CLAUDE.md
`## Angular-aim convention (#783)`.

Stage convention:
    `az_deg > 0` → beam swept toward stage `+X`
                    (matches `rz > 0` in the rotation convention).
    `el_deg > 0` → beam aimed above horizon (toward stage `+Z`).

The home offset is already baked into `profile_mechanics.dmx_to_mechanical`
(home maps to mech (0, 0)), so this module no longer needs `home_mech_*`
parameters. The math is just rotation × pan/tilt × stage transform.

Pure functions; no I/O, no Flask. Constructible from synthetic data.
"""

import math

# Self-contained rotation primitives. The aim package never imports
# from `coverage_math` / `mover_calibrator` / `mover_control` /
# `sphere_model` per the #784 / #785 architectural rule.
from ._rotmat import mount_rotation as _mount_rotation
from ._rotmat import matvec as _matvec
from ._rotmat import transpose as _transpose


def _stage_aim_from_angles(az_deg, el_deg):
    """Stage-frame unit aim vector from `(az, el)` in stage convention.
    `(az=0, el=0)` is stage `+Y` (forward, level)."""
    ar = math.radians(float(az_deg))
    er = math.radians(float(el_deg))
    cer = math.cos(er)
    return (math.sin(ar) * cer,
            math.cos(ar) * cer,
            math.sin(er))


def _angles_from_stage_aim(stage_aim):
    """Inverse of `_stage_aim_from_angles`. Returns `(az, el)` in
    degrees with `az` in (-180, +180]. Gimbal-lock at the zenith
    (`el = ±90°`) leaves `az` degenerate."""
    sx, sy, sz = stage_aim
    az_deg = math.degrees(math.atan2(sx, sy))
    el_deg = math.degrees(math.atan2(sz, math.hypot(sx, sy)))
    return (az_deg, el_deg)


def _mount_aim_from_mech(mech_pan_deg, mech_tilt_deg):
    """Mount-frame unit aim from mechanical axis angles. At
    `(mech_pan=0, mech_tilt=0)` the beam aims mount `+Y` — the
    operator drove Home to that pose. `+mech_pan` rotates toward
    mount `+X`; `+mech_tilt` rotates toward mount `+Z`."""
    pr = math.radians(float(mech_pan_deg))
    tr = math.radians(float(mech_tilt_deg))
    ctr = math.cos(tr)
    return (math.sin(pr) * ctr,
            math.cos(pr) * ctr,
            math.sin(tr))


def _mech_from_mount_aim(mount_aim):
    """Inverse of `_mount_aim_from_mech`. Same gimbal-lock caveat."""
    mx, my, mz = mount_aim
    mech_pan = math.degrees(math.atan2(mx, my))
    mech_tilt = math.degrees(math.atan2(mz, math.hypot(mx, my)))
    return (mech_pan, mech_tilt)


def mechanical_to_stage_aim(mech_pan_deg, mech_tilt_deg, fixture_rotation):
    """Convert mechanical-axis angles to stage-frame `(az_deg, el_deg)`.

    Mechanical angles are relative to home (home pose = mech (0, 0)
    by `profile_mechanics`'s convention). `fixture_rotation` orients
    the mount in stage frame.
    """
    mount_aim = _mount_aim_from_mech(mech_pan_deg, mech_tilt_deg)
    R = _mount_rotation(fixture_rotation or [0.0, 0.0, 0.0])
    stage_aim = _matvec(R, mount_aim)
    return _angles_from_stage_aim(stage_aim)


def stage_aim_to_mechanical(az_deg, el_deg, fixture_rotation):
    """Inverse of `mechanical_to_stage_aim`. At zenith / nadir
    (`el = ±90°`) `az` is degenerate; caller treats that as
    gimbal-lock and decides `az` separately."""
    stage_aim = _stage_aim_from_angles(az_deg, el_deg)
    R = _mount_rotation(fixture_rotation or [0.0, 0.0, 0.0])
    mount_aim = _matvec(_transpose(R), stage_aim)
    return _mech_from_mount_aim(mount_aim)


def stage_aim_from_world_xyz(target_xyz, fixture_xyz):
    """Convert a stage-mm target to stage-frame aim `(az_deg, el_deg)`.
    Returns `None` if target coincides with the fixture position
    (degenerate aim — caller treats as 'no move')."""
    dx = float(target_xyz[0]) - float(fixture_xyz[0])
    dy = float(target_xyz[1]) - float(fixture_xyz[1])
    dz = float(target_xyz[2]) - float(fixture_xyz[2])
    norm2 = dx * dx + dy * dy + dz * dz
    if norm2 < 1e-12:
        return None
    norm = math.sqrt(norm2)
    return _angles_from_stage_aim((dx / norm, dy / norm, dz / norm))
