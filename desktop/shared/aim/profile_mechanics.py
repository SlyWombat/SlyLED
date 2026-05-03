"""aim/profile_mechanics.py — DMX → mechanical-axis angle conversion (#784, #785).

Per #784 comment-3 operator clarification (2026-05-02): mechanics are
derived from `(panRange, tiltRange, home_pan_dmx16, home_tilt_dmx16)`
only. NO `dmxToMechanical` profile metadata, NO `panSign*`/`tiltSign*`,
NO `tiltUp`. Slope is positive by convention; mount inversion lives
in `fixture.rotation` and composes downstream in `stage_frame`.

Math (the entire IK):

    pan_mech_deg  = (pan_dmx16  - home_pan_dmx16)  / 65535 * pan_range_deg
    tilt_mech_deg = (tilt_dmx16 - home_tilt_dmx16) / 65535 * tilt_range_deg

Home is the angular zero by convention — at `(home_pan_dmx16,
home_tilt_dmx16)` mechanical (pan, tilt) is `(0, 0)` and the beam
aims along `rotation_forward`. No additional `dmx16AtMechZero` offset
because there's nowhere a non-home zero would come from.

Pure functions; no I/O, no Flask, no module state.
"""


class ProfileMechanicsError(ValueError):
    """Raised on malformed inputs to the mechanics conversion. Kept as
    a distinct exception class for callers that want to surface a
    profile-side problem distinctly from a fixture-side problem."""


def dmx_to_mechanical(pan_dmx16, tilt_dmx16,
                       pan_range_deg, tilt_range_deg,
                       home_pan_dmx16, home_tilt_dmx16):
    """Convert a DMX pose to mechanical-axis degrees, anchored at
    `(home_pan_dmx16, home_tilt_dmx16) = (0, 0) mech`. Returns
    `(pan_mech_deg, tilt_mech_deg)`.
    """
    pr = float(pan_range_deg) if pan_range_deg else 0.0
    tr = float(tilt_range_deg) if tilt_range_deg else 0.0
    pan_mech = (float(pan_dmx16) - float(home_pan_dmx16)) / 65535.0 * pr
    tilt_mech = (float(tilt_dmx16) - float(home_tilt_dmx16)) / 65535.0 * tr
    return (pan_mech, tilt_mech)


def mechanical_to_dmx(pan_mech_deg, tilt_mech_deg,
                       pan_range_deg, tilt_range_deg,
                       home_pan_dmx16, home_tilt_dmx16):
    """Inverse of `dmx_to_mechanical`. Returns floats — caller rounds
    + clamps to `[0, 65535]` as appropriate."""
    pr = float(pan_range_deg) if pan_range_deg else 0.0
    tr = float(tilt_range_deg) if tilt_range_deg else 0.0
    if pr <= 0:
        pan_dmx = float(home_pan_dmx16)
    else:
        pan_dmx = float(home_pan_dmx16) + float(pan_mech_deg) / pr * 65535.0
    if tr <= 0:
        tilt_dmx = float(home_tilt_dmx16)
    else:
        tilt_dmx = float(home_tilt_dmx16) + float(tilt_mech_deg) / tr * 65535.0
    return (pan_dmx, tilt_dmx)


def reachable_mechanical_range(pan_range_deg, tilt_range_deg,
                                 home_pan_dmx16, home_tilt_dmx16):
    """Return `((pan_mech_min, pan_mech_max), (tilt_mech_min, tilt_mech_max))`
    derived from the DMX 0..65535 range with `home` as the angular zero."""
    pan_lo = (0 - float(home_pan_dmx16)) / 65535.0 * float(pan_range_deg or 0)
    pan_hi = (65535 - float(home_pan_dmx16)) / 65535.0 * float(pan_range_deg or 0)
    tilt_lo = (0 - float(home_tilt_dmx16)) / 65535.0 * float(tilt_range_deg or 0)
    tilt_hi = (65535 - float(home_tilt_dmx16)) / 65535.0 * float(tilt_range_deg or 0)
    return ((min(pan_lo, pan_hi), max(pan_lo, pan_hi)),
            (min(tilt_lo, tilt_hi), max(tilt_lo, tilt_hi)))
