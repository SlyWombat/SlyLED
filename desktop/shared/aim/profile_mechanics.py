"""aim/profile_mechanics.py — DMX → mechanical-axis angle conversion (#784, #785).

Per #784 comment-3 + #785 QA findings (2026-05-02/03): mechanics are
derived from `(panRange, tiltRange, home_pan_dmx16, home_tilt_dmx16,
pan_sign, tilt_sign)`. NO `dmxToMechanical` profile metadata, NO
`tiltUp`. The signs come from the fixture's `homeSecondary` direction
calls (the operator drove Home, then drove an axis a known direction
and confirmed which way the beam went) — derivation lives in
`AimSphere.__init__`. Mount inversion separately lives in
`fixture.rotation` and composes downstream in `stage_frame`.

Math (the entire IK):

    pan_mech_deg  = (pan_dmx16  - home_pan_dmx16)  / 65535 * pan_range  * pan_sign
    tilt_mech_deg = (tilt_dmx16 - home_tilt_dmx16) / 65535 * tilt_range * tilt_sign

Home is the angular zero by convention — at `(home_pan_dmx16,
home_tilt_dmx16)` mechanical (pan, tilt) is `(0, 0)` and the beam aims
along `rotation_forward`. `pan_sign` / `tilt_sign` ∈ `{+1, -1}` capture
the per-fixture wiring direction (which way +DMX rotates the
mechanics relative to the operator's stage-frame observation).

Pure functions; no I/O, no Flask, no module state.
"""


class ProfileMechanicsError(ValueError):
    """Raised on malformed inputs to the mechanics conversion."""


def dmx_to_mechanical(pan_dmx16, tilt_dmx16,
                       pan_range_deg, tilt_range_deg,
                       home_pan_dmx16, home_tilt_dmx16,
                       pan_sign=1, tilt_sign=1):
    """Convert a DMX pose to mechanical-axis degrees, anchored at
    `(home_pan_dmx16, home_tilt_dmx16) = (0, 0) mech`. Returns
    `(pan_mech_deg, tilt_mech_deg)`.

    `pan_sign` / `tilt_sign` ∈ `{+1, -1}` invert the slope direction
    when the operator's `homeSecondary` direction call indicates the
    fixture's mechanics are wired so +DMX moves the beam opposite the
    default convention.
    """
    pr = float(pan_range_deg) if pan_range_deg else 0.0
    tr = float(tilt_range_deg) if tilt_range_deg else 0.0
    ps = +1.0 if int(pan_sign) >= 0 else -1.0
    ts = +1.0 if int(tilt_sign) >= 0 else -1.0
    pan_mech = (float(pan_dmx16) - float(home_pan_dmx16)) / 65535.0 * pr * ps
    tilt_mech = (float(tilt_dmx16) - float(home_tilt_dmx16)) / 65535.0 * tr * ts
    return (pan_mech, tilt_mech)


def mechanical_to_dmx(pan_mech_deg, tilt_mech_deg,
                       pan_range_deg, tilt_range_deg,
                       home_pan_dmx16, home_tilt_dmx16,
                       pan_sign=1, tilt_sign=1):
    """Inverse of `dmx_to_mechanical`. Returns floats — caller rounds
    + clamps to `[0, 65535]` as appropriate."""
    pr = float(pan_range_deg) if pan_range_deg else 0.0
    tr = float(tilt_range_deg) if tilt_range_deg else 0.0
    ps = +1.0 if int(pan_sign) >= 0 else -1.0
    ts = +1.0 if int(tilt_sign) >= 0 else -1.0
    if pr <= 0:
        pan_dmx = float(home_pan_dmx16)
    else:
        pan_dmx = float(home_pan_dmx16) + float(pan_mech_deg) / pr / ps * 65535.0
    if tr <= 0:
        tilt_dmx = float(home_tilt_dmx16)
    else:
        tilt_dmx = float(home_tilt_dmx16) + float(tilt_mech_deg) / tr / ts * 65535.0
    return (pan_dmx, tilt_dmx)


def reachable_mechanical_range(pan_range_deg, tilt_range_deg,
                                 home_pan_dmx16, home_tilt_dmx16,
                                 pan_sign=1, tilt_sign=1):
    """Return `((pan_mech_min, pan_mech_max), (tilt_mech_min, tilt_mech_max))`
    derived from the DMX 0..65535 range with `home` as the angular zero."""
    p_lo, _ = dmx_to_mechanical(0, 0, pan_range_deg, tilt_range_deg,
                                  home_pan_dmx16, home_tilt_dmx16,
                                  pan_sign, tilt_sign)
    p_hi, _ = dmx_to_mechanical(65535, 0, pan_range_deg, tilt_range_deg,
                                  home_pan_dmx16, home_tilt_dmx16,
                                  pan_sign, tilt_sign)
    _, t_lo = dmx_to_mechanical(0, 0, pan_range_deg, tilt_range_deg,
                                  home_pan_dmx16, home_tilt_dmx16,
                                  pan_sign, tilt_sign)
    _, t_hi = dmx_to_mechanical(0, 65535, pan_range_deg, tilt_range_deg,
                                  home_pan_dmx16, home_tilt_dmx16,
                                  pan_sign, tilt_sign)
    return ((min(p_lo, p_hi), max(p_lo, p_hi)),
            (min(t_lo, t_hi), max(t_lo, t_hi)))
