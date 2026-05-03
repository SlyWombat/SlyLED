"""sphere_model.py — #783 PR-β profile-derived sphere model.

The sphere model is the single canonical inverse-kinematics primitive for
moving-head fixtures. Home alone — combined with profile metadata
(panRange, tiltRange, tiltOffsetDmx16, tiltUp, panSignFromDmx,
tiltSignFromDmx) and `fixture.rotation` — fully determines the fixture's
reachable aim sphere. Home-Secondary capture and the 16-point probe grid
are deprecated under #783.

Three primitive operations:

    dmx_to_direction(panDmx16, tiltDmx16, sphere)
        DMX pose → stage-frame unit aim vector.

    direction_to_poses(target_direction, sphere)
        stage-frame unit aim → list of valid (panDmx16, tiltDmx16) poses.
        0..N entries — 0 = unreachable, >1 = multi-valued azimuth band
        (a 540° pan fixture covers ~180° of azimuth twice).

    aim(target_direction, current_pose, sphere, prefer="closest")
        direction_to_poses + pick best per `prefer` policy.
        Default: minimum DMX travel from `current_pose`.

World-XYZ aim is a thin wrapper: `aim_world_xyz(target_xyz, sphere, ...)`.

This module has zero call sites in PR-β — it lands disabled. PR-γ wires
the wrapper into `aim-angles` and `_aim_to_pan_tilt`. PR-ε deletes
`coverage_math.solve_dmx_per_degree`.

#780 P1: rotation is the single source of truth for mount orientation.
The sphere model NEVER reads `mountedInverted` — inverted ceiling mounts
encode the +180° roll in `rotation[1]`.
"""

import math

# Re-export coverage_math's rotation primitives so callers don't need
# to import two modules. The sphere model owns no new rotation math —
# fixture-frame ↔ stage-frame is identical to the existing convention.
from coverage_math import _mount_rotation, _matvec, _transpose


class SphereModel:
    """Per-fixture aim sphere derived from Home + profile + rotation.

    Construction is cheap (no LSQ, no probe data). Re-build on every
    fixture / profile / rotation mutation rather than caching across
    edits — the operator's Home anchor is the single source the model
    consumes, and changes to it should propagate immediately.
    """

    __slots__ = (
        "fixture_xyz", "fixture_rotation",
        "home_pan_dmx16", "home_tilt_dmx16",
        "pan_range_deg", "tilt_range_deg",
        "pan_sign", "tilt_sign",
        "pan_min_dmx16", "pan_max_dmx16",
        "tilt_min_dmx16", "tilt_max_dmx16",
        "_pan_dmx_per_deg", "_tilt_dmx_per_deg",
    )

    def __init__(self, fixture_xyz, fixture_rotation,
                 home_pan_dmx16, home_tilt_dmx16,
                 pan_range_deg, tilt_range_deg,
                 pan_sign=1, tilt_sign=1,
                 pan_min_dmx16=0, pan_max_dmx16=65535,
                 tilt_min_dmx16=0, tilt_max_dmx16=65535):
        self.fixture_xyz = (float(fixture_xyz[0]),
                             float(fixture_xyz[1]),
                             float(fixture_xyz[2]))
        rot = list(fixture_rotation or [0.0, 0.0, 0.0])
        rot = (rot + [0.0, 0.0, 0.0])[:3]
        self.fixture_rotation = [float(rot[0]), float(rot[1]), float(rot[2])]
        self.home_pan_dmx16 = int(home_pan_dmx16)
        self.home_tilt_dmx16 = int(home_tilt_dmx16)
        self.pan_range_deg = float(pan_range_deg)
        self.tilt_range_deg = float(tilt_range_deg)
        self.pan_sign = +1 if int(pan_sign) >= 0 else -1
        self.tilt_sign = +1 if int(tilt_sign) >= 0 else -1
        self.pan_min_dmx16 = int(pan_min_dmx16)
        self.pan_max_dmx16 = int(pan_max_dmx16)
        self.tilt_min_dmx16 = int(tilt_min_dmx16)
        self.tilt_max_dmx16 = int(tilt_max_dmx16)
        # DMX-per-degree, signed. Zero range → zero rate (degenerate
        # fixtures with panRange=0 / tiltRange=0 still construct cleanly
        # so a non-moving-head profile doesn't crash the loader).
        # Mount-frame mechanics live entirely in `pan_sign` / `tilt_sign`.
        # Stage-frame inversion lives entirely in `fixture_rotation`. The
        # math composes them; nothing here special-cases inverted vs
        # upright (#783 design — "the math doesn't know about inversion").
        if self.pan_range_deg > 0:
            self._pan_dmx_per_deg = 65535.0 * self.pan_sign / self.pan_range_deg
        else:
            self._pan_dmx_per_deg = 0.0
        if self.tilt_range_deg > 0:
            self._tilt_dmx_per_deg = 65535.0 * self.tilt_sign / self.tilt_range_deg
        else:
            self._tilt_dmx_per_deg = 0.0

    # ── Construction helpers ───────────────────────────────────────

    @classmethod
    def from_fixture(cls, fixture, profile_info):
        """Build a SphereModel from a fixture record + profile channel_info.

        `fixture`     : dict with x/y/z, rotation, homePanDmx16, homeTiltDmx16.
        `profile_info`: dict from `ProfileLibrary.channel_info(...)` —
                        panRange, tiltRange, panSignFromDmx, tiltSignFromDmx.

        Note: homePanDmx16 / homeTiltDmx16 = 0 is a *valid* anchor (fid 17
        on basement rig parks tilt at DMX 0 by mechanical convention). Use
        `... if ... is not None else default` rather than `... or default`
        so a legit zero doesn't fall through to the mid-range default.
        """
        def _coalesce(v, default):
            return default if v is None else v
        return cls(
            fixture_xyz=(_coalesce(fixture.get("x"), 0),
                          _coalesce(fixture.get("y"), 0),
                          _coalesce(fixture.get("z"), 0)),
            fixture_rotation=fixture.get("rotation") or [0.0, 0.0, 0.0],
            home_pan_dmx16=int(_coalesce(fixture.get("homePanDmx16"), 32768)),
            home_tilt_dmx16=int(_coalesce(fixture.get("homeTiltDmx16"), 32768)),
            pan_range_deg=float(_coalesce(profile_info.get("panRange"), 540) or 540),
            tilt_range_deg=float(_coalesce(profile_info.get("tiltRange"), 270) or 270),
            pan_sign=int(_coalesce(profile_info.get("panSignFromDmx"), 1)),
            tilt_sign=int(_coalesce(profile_info.get("tiltSignFromDmx"), 1)),
        )

    # ── DMX ↔ angle conversion ─────────────────────────────────────

    def dmx_to_angles(self, pan_dmx16, tilt_dmx16):
        """DMX pose → fixture-internal (panDeg, tiltDeg).

        At Home: returns (0, 0) by construction. The operator drives
        Home so the beam aims along `rotation_forward`; that pose is
        the angular zero for the sphere.
        """
        if self._pan_dmx_per_deg == 0.0:
            pan_deg = 0.0
        else:
            pan_deg = (int(pan_dmx16) - self.home_pan_dmx16) / self._pan_dmx_per_deg
        if self._tilt_dmx_per_deg == 0.0:
            tilt_deg = 0.0
        else:
            tilt_deg = (int(tilt_dmx16) - self.home_tilt_dmx16) / self._tilt_dmx_per_deg
        return (pan_deg, tilt_deg)

    def angles_to_dmx(self, pan_deg, tilt_deg, *, clamp=False):
        """Fixture-internal angles → DMX pose. Clamping is opt-in: the
        sphere's reachability gate uses the unclamped result to decide
        whether a target is in range, then clamps before returning to
        the caller. `clamp=True` collapses both behaviours into one
        call when the caller doesn't care about reachability."""
        p_dmx = self.home_pan_dmx16 + float(pan_deg) * self._pan_dmx_per_deg
        t_dmx = self.home_tilt_dmx16 + float(tilt_deg) * self._tilt_dmx_per_deg
        p_dmx = int(round(p_dmx))
        t_dmx = int(round(t_dmx))
        if clamp:
            p_dmx = max(self.pan_min_dmx16, min(self.pan_max_dmx16, p_dmx))
            t_dmx = max(self.tilt_min_dmx16, min(self.tilt_max_dmx16, t_dmx))
        return (p_dmx, t_dmx)

    # ── Frame transforms ───────────────────────────────────────────

    def angles_to_aim_mount(self, pan_deg, tilt_deg):
        """Fixture-internal angles → fixture-frame unit aim vector.

        Convention (matches `coverage_math.fixture_aim_to_world`):
            (panDeg=0, tiltDeg=0) → mount-+Y
            +panDeg sweeps +Y toward +X
            +tiltDeg lifts beam toward +Z
        """
        pr = math.radians(float(pan_deg))
        tr = math.radians(float(tilt_deg))
        ct = math.cos(tr)
        return (math.sin(pr) * ct,
                math.cos(pr) * ct,
                math.sin(tr))

    def aim_mount_to_stage(self, aim_mount):
        R = _mount_rotation(self.fixture_rotation)
        return _matvec(R, aim_mount)

    def aim_stage_to_mount(self, aim_stage):
        R = _mount_rotation(self.fixture_rotation)
        return _matvec(_transpose(R), aim_stage)

    # ── Forward + inverse direction ────────────────────────────────

    def dmx_to_direction(self, pan_dmx16, tilt_dmx16):
        """DMX pose → stage-frame unit aim vector."""
        pan_deg, tilt_deg = self.dmx_to_angles(pan_dmx16, tilt_dmx16)
        return self.aim_mount_to_stage(
            self.angles_to_aim_mount(pan_deg, tilt_deg))

    # ── Stage-convention API (#783 angular-aim CLAUDE.md) ─────────

    def aim_stage_angles(self, stage_pan_deg, stage_tilt_deg, *, clamp=True):
        """Stage-convention angles → DMX pose.

        Per CLAUDE.md `## Angular-aim convention (#783)`:
          - `stage_tilt_deg > 0` = beam above horizon (toward stage +Z).
          - `stage_pan_deg > 0`  = beam swept toward stage +X.

        The transform: build a stage-frame unit aim from the stage angles,
        rotate into the fixture's mount frame, then apply profile-sign
        metadata to translate mount-frame angles to DMX. Inversion lives
        ENTIRELY in `fixture.rotation` — neither the math here nor the
        profile metadata branches on whether the fixture is upright or
        ceiling-mounted; the rotation matrix handles it transparently.

        Companion to `aim_world_xyz`, which takes a stage-XYZ target;
        `aim_stage_angles` takes stage-frame angles directly. Both flow
        through `direction_to_poses` semantics (pose clamping, no
        multi-valued-pan resolution — caller passes specific angles).
        """
        pr = math.radians(float(stage_pan_deg))
        tr = math.radians(float(stage_tilt_deg))
        ct = math.cos(tr)
        stage_aim = (math.sin(pr) * ct,
                      math.cos(pr) * ct,
                      math.sin(tr))
        mount_aim = self.aim_stage_to_mount(stage_aim)
        mx, my, mz = mount_aim
        mount_pan_deg = math.degrees(math.atan2(mx, my))
        mount_tilt_deg = math.degrees(math.atan2(mz, math.hypot(mx, my)))
        return self.angles_to_dmx(mount_pan_deg, mount_tilt_deg, clamp=clamp)

    def dmx_to_stage_angles(self, pan_dmx16, tilt_dmx16):
        """Inverse of `aim_stage_angles`: DMX pose → stage-convention
        (panDeg, tiltDeg). Useful for the SPA's "show me what stage
        direction this pose aims at" readouts."""
        direction = self.dmx_to_direction(pan_dmx16, tilt_dmx16)
        dx, dy, dz = direction
        stage_pan_deg = math.degrees(math.atan2(dx, dy))
        stage_tilt_deg = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
        return (stage_pan_deg, stage_tilt_deg)

    def direction_to_poses(self, target_direction):
        """stage-frame unit aim → list of (panDmx16, tiltDmx16) poses.

        0 entries: target unreachable (out of tilt range, or no panDeg
        candidate within DMX limits).
        1 entry: standard single-valued aim.
        2+ entries: multi-valued azimuth band (e.g. 540° pan fixtures
        cover ~180° of azimuth twice — "pan A" and "pan B" in console
        parlance).

        Each entry is a clamped (panDmx16, tiltDmx16) tuple. Order is
        ascending by `panDmx16`.
        """
        if self._pan_dmx_per_deg == 0.0 and self._tilt_dmx_per_deg == 0.0:
            return []
        # Stage → fixture-frame target direction.
        aim_mount = self.aim_stage_to_mount(target_direction)
        mx, my, mz = aim_mount
        # Mount-frame inverse: pan_deg = atan2(mx, my); tilt_deg = atan2(mz, hypot(mx, my)).
        # Matches `world_to_fixture_pt` in coverage_math.py.
        h = math.hypot(mx, my)
        pan_deg_principal = math.degrees(math.atan2(mx, my))
        tilt_deg = math.degrees(math.atan2(mz, h))

        # Tilt reachability — derived from DMX limits + home anchor.
        if self._tilt_dmx_per_deg != 0.0:
            t_lo = (self.tilt_min_dmx16 - self.home_tilt_dmx16) / self._tilt_dmx_per_deg
            t_hi = (self.tilt_max_dmx16 - self.home_tilt_dmx16) / self._tilt_dmx_per_deg
            if t_lo > t_hi:
                t_lo, t_hi = t_hi, t_lo
            # 0.5° slack so a target right at the limit doesn't drop out
            # to numerical-edge noise.
            if tilt_deg < t_lo - 0.5 or tilt_deg > t_hi + 0.5:
                return []

        # Pan: try the principal angle plus ±360° / ±720° offsets to
        # capture multi-valued azimuth. A 540° pan fixture (panRange =
        # 540) covers any single azimuth at panDeg AND panDeg+360
        # (mechanically, the head can reach the same world direction
        # via either side of the yoke). 360° fixtures usually have one
        # pose per azimuth; 720°+ fixtures may have three.
        poses = []
        seen = set()
        for k in (-2, -1, 0, +1, +2):
            cand_pan_deg = pan_deg_principal + 360.0 * k
            p_dmx16, t_dmx16 = self.angles_to_dmx(cand_pan_deg, tilt_deg)
            # In-range gate (1-unit slack mirrors the tilt slack — keeps
            # round-trip exactness when home sits at a DMX limit).
            if not (self.pan_min_dmx16 - 1 <= p_dmx16 <= self.pan_max_dmx16 + 1):
                continue
            if not (self.tilt_min_dmx16 - 1 <= t_dmx16 <= self.tilt_max_dmx16 + 1):
                continue
            p_dmx16 = max(self.pan_min_dmx16, min(self.pan_max_dmx16, p_dmx16))
            t_dmx16 = max(self.tilt_min_dmx16, min(self.tilt_max_dmx16, t_dmx16))
            key = (p_dmx16, t_dmx16)
            if key in seen:
                continue
            seen.add(key)
            poses.append(key)
        poses.sort()
        return poses

    def aim(self, target_direction, current_pose=None, prefer="closest"):
        """Pick a single (panDmx16, tiltDmx16) pose per `prefer` policy.

        prefer:
            "closest" — default. Minimum |Δpan_dmx| + |Δtilt_dmx| from
                        `current_pose`. When `current_pose` is None,
                        falls back to closest-to-Home (matches operator
                        expectation when a fixture has just been parked).
            "A"       — lowest panDmx16 among reachable poses.
            "B"       — highest panDmx16. For 540° fixtures, "A"/"B"
                        select the two sides of the doubled azimuth
                        band; for single-valued azimuths "A" == "B".
        Returns None when the target is unreachable.
        """
        poses = self.direction_to_poses(target_direction)
        if not poses:
            return None
        if prefer == "A":
            return poses[0]
        if prefer == "B":
            return poses[-1]
        # "closest" — default.
        if current_pose is None:
            current_pose = (self.home_pan_dmx16, self.home_tilt_dmx16)
        cp_pan, cp_tilt = int(current_pose[0]), int(current_pose[1])
        return min(poses,
                    key=lambda p: abs(p[0] - cp_pan) + abs(p[1] - cp_tilt))


# ── Module-level convenience wrappers ─────────────────────────────────


def dmx_to_direction(pan_dmx16, tilt_dmx16, sphere):
    """Functional alias for `sphere.dmx_to_direction(...)` so call sites
    can read like the issue's spec verbatim."""
    return sphere.dmx_to_direction(pan_dmx16, tilt_dmx16)


def direction_to_poses(target_direction, sphere):
    return sphere.direction_to_poses(target_direction)


def aim(target_direction, current_pose, sphere, prefer="closest"):
    return sphere.aim(target_direction, current_pose, prefer)


def aim_world_xyz(target_xyz, sphere, current_pose=None, prefer="closest"):
    """stage-mm target XYZ → DMX pose. Wrapper around `aim` that handles
    the world-XYZ → unit-aim normalisation. Returns None when:
      - the target coincides with the fixture position (degenerate aim);
      - the resulting direction is outside the fixture's reach.
    """
    fx, fy, fz = sphere.fixture_xyz
    dx = float(target_xyz[0]) - fx
    dy = float(target_xyz[1]) - fy
    dz = float(target_xyz[2]) - fz
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-9:
        return None
    direction = (dx / norm, dy / norm, dz / norm)
    return sphere.aim(direction, current_pose, prefer)
