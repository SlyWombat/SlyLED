"""aim/sphere.py — slope-from-home aim model (#799).

Replaces #798's anchor-list / bracketing-interpolation model with the
simpler analytical formula described in #799:

    slope_pan  = sign_pan  * 65535 / panRange       # DMX per stage-deg
    slope_tilt = sign_tilt * 65535 / tiltRange      # DMX per stage-deg

    target_pan_dmx  = home_pan_dmx16  + slope_pan  * (target_az - home_az_stage)
    target_tilt_dmx = home_tilt_dmx16 + slope_tilt * (target_el - home_el_stage)

`home_az_stage` / `home_el_stage` are derived from `fixture.rotation`
applied to the mount-frame +Y forward axis at construction time (the
beam aims along rotation-forward when DMX is at the home anchor).
`sign_pan` / `sign_tilt` come from the operator's `homeSecondary`
direction calls + signed DMX offsets — the wizard wrote a signed
offset and the operator confirmed which way the beam moved in stage
frame; combining the two pins the per-axis sign.

Multi-valued azimuth (panRange > 360°) is enumerated by trying the
candidate angle plus `k*360°` for `k ∈ {-2..+2}`; survivors land in
DMX `[0, 65535]`. Targets with no surviving branch are unreachable —
`aim_*` returns `None`, `poses_for_direction` returns `[]`.

Tilt-mirror optimization (alternative mech paths reaching the same
stage direction by tilting past an extreme) is OUT OF SCOPE per #799.

Failure modes: missing Home / Secondary / panRange → `ValueError`.
"""

import math

from ._rotmat import mount_rotation as _mount_rotation
from ._rotmat import matvec as _matvec
from ._rotmat import transpose as _transpose


def _angles_from_unit_vec(vec):
    """Stage unit vector → `(az_deg, el_deg)` per CLAUDE.md angular-aim
    convention (`+az` toward stage `+X`, `+el` above horizon)."""
    sx, sy, sz = vec
    az_deg = math.degrees(math.atan2(sx, sy))
    el_deg = math.degrees(math.atan2(sz, math.hypot(sx, sy)))
    return az_deg, el_deg


def _wrap_az(az_deg):
    """Wrap azimuth to `(-180, +180]`."""
    while az_deg > 180.0:
        az_deg -= 360.0
    while az_deg <= -180.0:
        az_deg += 360.0
    return az_deg


# `k * 360°` branches enumerated for multi-valued pan. ±2 covers
# fixtures up to panRange = 1080°.
_BRANCH_RANGE = (-2, -1, 0, 1, 2)


class AimSphere:
    """Slope-from-home aim model for a single moving-head fixture.

    Public surface is unchanged from #798's contract — `mover_control`,
    `aim/routes`, `aim/park` keep working without surgery:

        AimSphere(fixture, profile)
            .aim_direction(az, el, current_pose=None, prefer="closest")
            .aim_xyz(target_xyz, current_pose=None, prefer="closest")
            .poses_for_direction(az, el)        → [(panDmx16, tiltDmx16, k), ...]
            .poses_for_xyz(target_xyz)          → same
            .direction_to_poses(az, el)         → legacy 2-tuple list
            .dmx_to_aim(panDmx16, tiltDmx16)    → (az, el)
    """

    __slots__ = (
        "fixture_xyz", "fixture_rotation",
        "home_pan_dmx16", "home_tilt_dmx16",
        "home_az_stage", "home_el_stage",
        "pan_range_deg", "tilt_range_deg",
        "slope_pan", "slope_tilt",   # signed DMX-per-stage-deg
    )

    def __init__(self, fixture, profile, *, anchors=None, step=None):
        # `anchors` and `step` accepted for caller-side compat with
        # the pre-#799 constructor but ignored — slope-from-home needs
        # neither anchors nor a precomputed cell table.
        del anchors, step
        if not isinstance(fixture, dict):
            raise ValueError(
                f"fixture must be a dict, got {type(fixture).__name__}")
        if not isinstance(profile, dict):
            raise ValueError(
                f"profile must be a dict, got {type(profile).__name__}")

        pan_range = profile.get("panRange")
        tilt_range = profile.get("tiltRange")
        if not pan_range or not tilt_range:
            pid = profile.get("id", "<unknown>")
            raise ValueError(
                f"profile {pid!r} has no pan/tilt range — not a moving head")
        self.pan_range_deg = float(pan_range)
        self.tilt_range_deg = float(tilt_range)

        x = fixture.get("x") or 0.0
        y = fixture.get("y") or 0.0
        z = fixture.get("z") or 0.0
        self.fixture_xyz = (float(x), float(y), float(z))
        rot = fixture.get("rotation") or [0.0, 0.0, 0.0]
        rot = (list(rot) + [0.0, 0.0, 0.0])[:3]
        self.fixture_rotation = [float(rot[0]), float(rot[1]), float(rot[2])]

        h_pan = fixture.get("homePanDmx16")
        h_tilt = fixture.get("homeTiltDmx16")
        if h_pan is None or h_tilt is None:
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} has no Home "
                "anchor")
        h_pan = int(h_pan)
        h_tilt = int(h_tilt)
        if not (0 <= h_pan <= 65535) or not (0 <= h_tilt <= 65535):
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} home DMX out of "
                "[0, 65535]")
        self.home_pan_dmx16 = h_pan
        self.home_tilt_dmx16 = h_tilt

        # Home aim direction in stage frame: rotate mount-frame +Y by R.
        R = _mount_rotation(self.fixture_rotation)
        home_stage_aim = _matvec(R, (0.0, 1.0, 0.0))
        self.home_az_stage, self.home_el_stage = _angles_from_unit_vec(
            home_stage_aim)

        sec = fixture.get("homeSecondary") or {}
        pan_dir = sec.get("panMovedDirection")
        tilt_dir = sec.get("tiltMovedDirection")
        pan_off = sec.get("panOffsetDmx16")
        tilt_off = sec.get("tiltOffsetDmx16")
        if (pan_dir not in ("left", "right")
                or tilt_dir not in ("up", "down")
                or pan_off is None or tilt_off is None):
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} needs "
                "Save-Home with secondary slew direction calls")
        pan_off = int(pan_off)
        tilt_off = int(tilt_off)

        # Sign derivation. The wizard wrote a SIGNED DMX offset (the
        # secondary slew used whichever direction had headroom from
        # home — could be negative). The operator's direction call
        # confirmed which way the beam moved in stage frame after that
        # signed offset was applied. Combine the two:
        #
        #   stage az delta sign = (panOffsetDmx16 sign) * pan_sign
        #   stage el delta sign = (tiltOffsetDmx16 sign) * tilt_sign
        #
        # "left"  → stage az > 0   (per CLAUDE +X = stage-left).
        # "right" → stage az < 0.
        # "up"    → stage el > 0.
        # "down"  → stage el < 0.
        # Slope derivation. The wizard wrote a SIGNED DMX offset
        # `off_dmx` (whichever direction had headroom from home). The
        # operator's direction call confirmed which way the beam moved
        # in stage frame: "left" → stage az > 0, "right" → az < 0;
        # "up" → stage el > 0, "down" → el < 0.
        #
        #   target_dmx = home_dmx + slope * delta_stage_deg
        #   off_dmx    = slope * delta_stage_observed
        #   slope      = off_dmx / delta_stage_observed
        #
        # Magnitude: |off_dmx| / (panRange × |off_dmx|/65535) =
        # 65535/panRange (a pure scalar). Sign: sign(off_dmx) ×
        # sign(delta_stage_observed).
        slope_pan_mag = 65535.0 / self.pan_range_deg
        slope_tilt_mag = 65535.0 / self.tilt_range_deg
        expected_az_sign = +1 if pan_dir == "left" else -1
        expected_el_sign = +1 if tilt_dir == "up" else -1
        off_pan_sign = +1 if pan_off >= 0 else -1
        off_tilt_sign = +1 if tilt_off >= 0 else -1
        self.slope_pan = off_pan_sign * expected_az_sign * slope_pan_mag
        self.slope_tilt = off_tilt_sign * expected_el_sign * slope_tilt_mag

    # ── Public API ────────────────────────────────────────────────

    def aim_direction(self, az_deg, el_deg, current_pose=None,
                       prefer="closest"):
        """Stage-frame aim → `(panDmx16, tiltDmx16)`. Always returns
        a pose (#803): targets outside the fixture's cone clamp to
        the nearest cone-boundary pose instead of erroring. Use
        `aim_direction_with_clamp` to know which axes were clamped.

        `current_pose` and `prefer` matter only for multi-valued
        fixtures (`panRange > 360°`); single-valued fixtures return
        the same pose regardless.
        """
        pose, _clamped_axes = self.aim_direction_with_clamp(
            az_deg, el_deg, current_pose=current_pose, prefer=prefer)
        return pose

    def aim_direction_with_clamp(self, az_deg, el_deg, current_pose=None,
                                  prefer="closest"):
        """Stage-frame aim → `((panDmx16, tiltDmx16), clamped_axes)`.
        `clamped_axes` is a tuple of `"pan"` / `"tilt"` strings naming
        any axis whose computed DMX fell outside `[0, 65535]` and was
        clamped to the cone boundary. Empty tuple = exact reachable.

        Routes / SPA use this to surface a `clamped: true` flag in
        the HTTP response so the operator sees "limit reached" UX
        when the head can't quite hit the requested target. Per #803
        the pose itself is always returned.
        """
        assert -180.0 <= float(az_deg) <= 180.0, (
            f"az_deg out of [-180, 180]: {az_deg}")
        assert -90.0 <= float(el_deg) <= 90.0, (
            f"el_deg out of [-90, 90]: {el_deg}")

        if current_pose is None:
            current_pose = (self.home_pan_dmx16, self.home_tilt_dmx16)
        else:
            current_pose = (int(current_pose[0]), int(current_pose[1]))

        poses = self.poses_for_direction(az_deg, el_deg)
        chosen = self._pick_pose(poses, current_pose, prefer)
        if chosen is None:
            return (None, ())
        # Find the clamped_axes tuple of the chosen pose by matching
        # (pan, tilt). Branch_id may differ; pick the first match.
        clamped_axes = ()
        for p, t, _b, axes in poses:
            if p == chosen[0] and t == chosen[1]:
                clamped_axes = axes
                break
        return (chosen, clamped_axes)

    def aim_xyz(self, target_xyz, current_pose=None, prefer="closest"):
        """Stage-mm target → `(panDmx16, tiltDmx16)`. Returns `None`
        only when target coincides with the fixture position
        (zero-vector aim, undefined direction). Targets outside the
        cone clamp to the boundary per #803."""
        pose, _ = self.aim_xyz_with_clamp(
            target_xyz, current_pose=current_pose, prefer=prefer)
        return pose

    def aim_xyz_with_clamp(self, target_xyz, current_pose=None,
                            prefer="closest"):
        """Stage-mm target → `((panDmx16, tiltDmx16), clamped_axes)`
        or `(None, ())` for coincident target. Same contract as
        `aim_direction_with_clamp`."""
        dx = float(target_xyz[0]) - self.fixture_xyz[0]
        dy = float(target_xyz[1]) - self.fixture_xyz[1]
        dz = float(target_xyz[2]) - self.fixture_xyz[2]
        norm2 = dx * dx + dy * dy + dz * dz
        if norm2 < 1e-12:
            return (None, ())
        norm = math.sqrt(norm2)
        az_deg, el_deg = _angles_from_unit_vec((dx / norm, dy / norm, dz / norm))
        return self.aim_direction_with_clamp(
            az_deg, el_deg, current_pose=current_pose, prefer=prefer)

    def poses_for_direction(self, az_deg, el_deg):
        """All candidate `(panDmx16, tiltDmx16, branch_id, clamped_axes)`
        poses for the target stage direction.

        #803 — instead of dropping unreachable branches, clamp them to
        the [0, 65535] DMX boundary and tag which axes were clamped:

        - **Tilt out of range** → tilt clamped to 0 or 65535. Every
          returned pose carries `"tilt"` in `clamped_axes`.
        - **All pan branches out of range** → primary branch (k=0)
          pan clamped; returned with `"pan"` in `clamped_axes`.
        - **At least one pan branch in range** → only those branches
          returned (each without `"pan"` in clamped_axes); the
          out-of-range branches are dropped because the in-range ones
          dominate them.
        - **Both axes in range** → exact reachable pose, empty
          `clamped_axes`.

        Return is non-empty for every direction except the
        constructor-level "no fixture" / "no profile" failures (which
        raise at __init__, not here).
        """
        daz = float(az_deg) - self.home_az_stage
        dele = float(el_deg) - self.home_el_stage

        tilt_dmx_f = self.home_tilt_dmx16 + self.slope_tilt * dele
        tilt_in_range = 0 <= round(tilt_dmx_f) <= 65535
        tilt_dmx16 = max(0, min(65535, int(round(tilt_dmx_f))))
        tilt_axes = () if tilt_in_range else ("tilt",)

        in_range = []
        primary_pan_f = None
        for k in _BRANCH_RANGE:
            cand_pan_f = (self.home_pan_dmx16
                           + self.slope_pan * (daz + 360.0 * k))
            if k == 0:
                primary_pan_f = cand_pan_f
            cand_pan = int(round(cand_pan_f))
            if 0 <= cand_pan <= 65535:
                in_range.append((cand_pan, tilt_dmx16, k, tilt_axes))

        if in_range:
            return in_range
        # No pan branch reachable — clamp the primary (k=0) branch and
        # mark "pan" alongside any tilt clamp.
        clamped_pan = max(0, min(65535,
                                  int(round(primary_pan_f or self.home_pan_dmx16))))
        return [(clamped_pan, tilt_dmx16, 0, tilt_axes + ("pan",))]

    def poses_for_xyz(self, target_xyz):
        """Stage-mm target → `[(panDmx16, tiltDmx16, branch_id, clamped_axes), ...]`.
        Empty list for coincident target. Otherwise always non-empty
        (clamped fallback per #803)."""
        dx = float(target_xyz[0]) - self.fixture_xyz[0]
        dy = float(target_xyz[1]) - self.fixture_xyz[1]
        dz = float(target_xyz[2]) - self.fixture_xyz[2]
        norm2 = dx * dx + dy * dy + dz * dz
        if norm2 < 1e-12:
            return []
        norm = math.sqrt(norm2)
        az_deg, el_deg = _angles_from_unit_vec((dx / norm, dy / norm, dz / norm))
        return self.poses_for_direction(az_deg, el_deg)

    def direction_to_poses(self, az_deg, el_deg):
        """Pre-#798 compat shim — strip branch_id + clamped_axes."""
        return sorted([(p, t)
                        for p, t, _b, _c in self.poses_for_direction(az_deg, el_deg)])

    def dmx_to_aim(self, pan_dmx16, tilt_dmx16):
        """Inverse of `aim_direction` — DMX pose → stage `(az, el)`.
        Round-trip is exact within float precision for any pose
        produced by `aim_direction`."""
        assert 0 <= int(pan_dmx16) <= 65535, (
            f"pan_dmx16 out of [0, 65535]: {pan_dmx16}")
        assert 0 <= int(tilt_dmx16) <= 65535, (
            f"tilt_dmx16 out of [0, 65535]: {tilt_dmx16}")
        az = (self.home_az_stage
               + (int(pan_dmx16) - self.home_pan_dmx16) / self.slope_pan)
        el = (self.home_el_stage
               + (int(tilt_dmx16) - self.home_tilt_dmx16) / self.slope_tilt)
        return (_wrap_az(az), el)

    # ── Picker ────────────────────────────────────────────────────

    @staticmethod
    def _pick_pose(poses, current_pose, prefer):
        """Collapse the multi-valued list to a single pose.

            "closest" — minimum L1 DMX distance from `current_pose`.
            "A"       — primary branch (k = 0); falls back to the
                         lowest-DMX branch if branch 0 was clipped.
            "B"       — highest panDmx16 branch.
        """
        if not poses:
            return None
        if prefer == "A":
            primary = [p for p in poses if p[2] == 0]
            chosen = primary[0] if primary else min(poses, key=lambda p: p[0])
        elif prefer == "B":
            chosen = max(poses, key=lambda p: p[0])
        else:
            cp_pan, cp_tilt = current_pose
            chosen = min(poses,
                          key=lambda p: abs(p[0] - cp_pan) + abs(p[1] - cp_tilt))
        return (int(chosen[0]), int(chosen[1]))
