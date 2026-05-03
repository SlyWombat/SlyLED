"""aim/sphere.py — anchor-based bracketing-interpolation aim model
(#784, #785, #798).

Geometry derives from operator-confirmed `(DMX, stage_aim)` calibration
anchors (see `aim.anchors.CalibrationAnchor`), not from `fixture.rotation`
plus a profile-derived slope. The 3-anchor minimum (Home + pan slew +
tilt slew) gives a single linear segment per axis; additional anchors
captured in a Verify-and-refine pass introduce more bracketing
intervals and tighten accuracy near each anchor.

`fixture.rotation` carries MOUNT BODY orientation only — typically
``[0, 0, 0]`` for an upright pendant, ``[0, 180, 0]`` for an inverted
truss mount. It does NOT encode where the beam aims at home; that
lives in the Home anchor's stage-frame `(az, el)`.

Aim flow (`aim_direction(az_target, el_target)`):
    1. Stage `(az, el)` → mount frame via `R_T = transpose(R)`.
    2. Mount-frame unit vector → mech `(pan_mech, tilt_mech)` via atan2.
    3. Bracket each axis on the anchors' mech values; linear-interp
       DMX from the bracketing pair. Outside the convex hull of the
       observations the model extrapolates from the nearest segment's
       slope and surfaces nothing — extrapolation is silent at the
       sphere level. Callers that want to surface a "clamped" warning
       check the returned DMX against `[panDmxMin, panDmxMax]`.
    4. Pose = `(panDmx16, tiltDmx16)`. Multi-valued enumeration
       (`poses_for_direction`) iterates over `pan_mech ± k×360°`.

`dmx_to_aim` is the inverse — bracket DMX axes on the anchors' DMX
values, interpolate mech, project mech to mount frame, project mount
to stage frame.

Per #798 correction: slope is local (per-segment), not global. There
is no `pan_sign` / `tilt_sign` instance attribute — each segment
carries its own implicit sign from the anchor delta.

Failure modes:
  - Missing anchors → ValueError raised by `aim.anchors.collect_anchors`.
  - Coincident XYZ target on `aim_xyz` → returns `None`.
"""

import math

from .anchors import (
    CalibrationAnchor, collect_anchors,
    mount_from_mech, mount_to_stage, stage_to_mount,
)
from .stage_frame import stage_aim_from_world_xyz


# Branches enumerated by `poses_for_direction` for multi-valued
# azimuth handling. ±2 covers fixtures up to panRange = 1080° — anything
# larger is exotic enough we'd want explicit operator confirmation.
_BRANCH_RANGE = (-2, -1, 0, 1, 2)


def _bracket_pair(value, samples):
    """Find the pair of `samples` (each = `(key, dmx)`) whose `key`
    brackets `value`. Returns `(lo, hi)` even when `value` falls
    outside the convex hull — the model extrapolates using the nearest
    segment with a non-degenerate slope.

    Anchors that don't move on this axis (e.g. the tilt-slew anchor's
    pan_mech matches the home anchor's pan_mech) produce duplicate
    keys. Duplicates collapse during bracketing so extrapolation pulls
    slope from a real segment, not from a (key, key)-degenerate pair.
    """
    sorted_samples = sorted(samples, key=lambda s: s[0])
    n = len(sorted_samples)
    if n == 0:
        raise ValueError("no samples to bracket against")
    if n == 1:
        return (sorted_samples[0], sorted_samples[0])

    # Find the first pair of distinct-key samples — needed both for
    # extrapolation and to short-circuit the all-duplicates case.
    first_distinct = None
    for i in range(n - 1):
        if sorted_samples[i + 1][0] != sorted_samples[i][0]:
            first_distinct = (sorted_samples[i], sorted_samples[i + 1])
            break
    if first_distinct is None:
        return (sorted_samples[0], sorted_samples[-1])

    last_distinct = None
    for i in range(n - 1, 0, -1):
        if sorted_samples[i][0] != sorted_samples[i - 1][0]:
            last_distinct = (sorted_samples[i - 1], sorted_samples[i])
            break
    if last_distinct is None:
        last_distinct = first_distinct

    if value <= sorted_samples[0][0]:
        return first_distinct
    if value >= sorted_samples[-1][0]:
        return last_distinct
    # Interior — find the first segment whose distinct keys bracket
    # `value`. Skip degenerate (equal-key) segments by walking until
    # the next distinct sample.
    for i in range(n - 1):
        lo = sorted_samples[i]
        # Walk forward to the next distinct key.
        j = i + 1
        while j < n and sorted_samples[j][0] == lo[0]:
            j += 1
        if j >= n:
            break
        hi = sorted_samples[j]
        if lo[0] <= value <= hi[0]:
            return (lo, hi)
    return last_distinct


def _interp(value, lo, hi):
    """Linear interp between two `(key, dmx)` samples. Extrapolates
    cleanly outside `[lo.key, hi.key]` using the segment's slope."""
    k_lo, d_lo = lo
    k_hi, d_hi = hi
    if k_hi == k_lo:
        return float(d_lo)
    t = (value - k_lo) / (k_hi - k_lo)
    return float(d_lo) + t * (float(d_hi) - float(d_lo))


def _angles_from_unit_vec(vec):
    """Stage-frame unit vector → `(az_deg, el_deg)` per CLAUDE.md
    angular-aim convention."""
    sx, sy, sz = vec
    az_deg = math.degrees(math.atan2(sx, sy))
    el_deg = math.degrees(math.atan2(sz, math.hypot(sx, sy)))
    return az_deg, el_deg


class AimSphere:
    """Per-fixture anchor-based aim model. Public API kept compatible
    with the pre-#798 `AimSphere` so `aim/routes.py`, `mover_control`,
    and `park.go_home` keep working without surgery."""

    __slots__ = (
        "fixture_xyz", "fixture_rotation",
        "home_pan_dmx16", "home_tilt_dmx16",
        "pan_range_deg", "tilt_range_deg",
        "anchors",            # tuple of CalibrationAnchor
        "_pan_samples",       # sorted [(pan_mech_deg, panDmx16), ...]
        "_tilt_samples",      # sorted [(tilt_mech_deg, tiltDmx16), ...]
        "_pan_dmx_min", "_pan_dmx_max",
        "_tilt_dmx_min", "_tilt_dmx_max",
    )

    def __init__(self, fixture, profile, *, anchors=None, step=None):
        # `step` accepted but ignored — kept for caller compat with the
        # cell-table sphere. Bracketing model has no precomputed cells.
        del step
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
                "anchor — set Home before constructing AimSphere")
        self.home_pan_dmx16 = int(h_pan)
        self.home_tilt_dmx16 = int(h_tilt)

        if anchors is None:
            anchors = collect_anchors(fixture, profile)
        else:
            anchors = list(anchors)
        if len(anchors) < 3:
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} needs ≥3 "
                "calibration anchors (Home + pan slew + tilt slew)")
        self.anchors = tuple(anchors)

        self._build_axis_samples()

    # ── Construction helpers ──────────────────────────────────────

    def _build_axis_samples(self):
        """Project each anchor's stage `(az, el)` into mount-frame
        mechanical angles. Build the per-axis sample lists used by
        `aim_direction`'s bracketing interp.

        Pan sample: ``(pan_mech_deg, panDmx16)`` for every anchor.
        Tilt sample: ``(tilt_mech_deg, tiltDmx16)`` for every anchor.

        Anchors that don't move along an axis (e.g. the tilt-slew
        anchor's pan_mech is typically the same as Home's, so the pan
        sample list contains a duplicate `(0°, panDmx_home)` entry)
        keep their projections — duplicate keys collapse during
        bracketing without altering the slope.
        """
        rot = self.fixture_rotation
        pan_samples = []
        tilt_samples = []
        for a in self.anchors:
            mount = stage_to_mount(a.az_deg, a.el_deg, rot)
            pan_mech, tilt_mech = _mech_from_mount(mount)
            pan_samples.append((pan_mech, a.pan_dmx16))
            tilt_samples.append((tilt_mech, a.tilt_dmx16))
        self._pan_samples = sorted(pan_samples)
        self._tilt_samples = sorted(tilt_samples)
        self._pan_dmx_min = min(a.pan_dmx16 for a in self.anchors)
        self._pan_dmx_max = max(a.pan_dmx16 for a in self.anchors)
        self._tilt_dmx_min = min(a.tilt_dmx16 for a in self.anchors)
        self._tilt_dmx_max = max(a.tilt_dmx16 for a in self.anchors)

    # ── Public API ────────────────────────────────────────────────

    def aim_direction(self, az_deg, el_deg, current_pose=None,
                       prefer="closest"):
        """Stage-frame aim → `(panDmx16, tiltDmx16)`. Always returns a
        pose; outside the calibrated range the model extrapolates from
        the nearest segment's slope.

        `current_pose` and `prefer` matter only for multi-valued
        fixtures (`panRange > 360°`); single-valued fixtures return
        the same pose regardless. See `poses_for_direction` for the
        full enumeration.
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
        return self._pick_pose(poses, current_pose, prefer)

    def aim_xyz(self, target_xyz, current_pose=None, prefer="closest"):
        """Stage-mm target → `(panDmx16, tiltDmx16)`. Returns `None`
        when target coincides with the fixture position."""
        result = stage_aim_from_world_xyz(target_xyz, self.fixture_xyz)
        if result is None:
            return None
        return self.aim_direction(*result, current_pose=current_pose,
                                    prefer=prefer)

    def poses_for_direction(self, az_deg, el_deg):
        """Enumerate every valid `(panDmx16, tiltDmx16, branch_id)` for
        the target stage direction. Multi-valued azimuth bands
        (`panRange > 360°`) yield ≥2 entries; clipped targets yield 1.

        `branch_id` is the `k` in `pan_mech_target + k×360°` — `0` is
        the primary branch, `±1` is the first wrap, `±2` the second.
        Branches whose computed DMX falls outside `[panDmxMin,
        panDmxMax]` (with a small tolerance) are dropped.
        """
        mount_target = stage_to_mount(az_deg, el_deg, self.fixture_rotation)
        pan_mech_target, tilt_mech_target = _mech_from_mount(mount_target)

        # Tilt is single-valued — it never wraps.
        tilt_lo, tilt_hi = _bracket_pair(tilt_mech_target, self._tilt_samples)
        tilt_dmx = _interp(tilt_mech_target, tilt_lo, tilt_hi)
        tilt_dmx16 = int(round(tilt_dmx))

        # Pan can wrap on fixtures with panRange > 360°. Range check
        # uses the fixture's full DMX envelope `[0, 65535]` — anchor
        # span doesn't bound mechanical reachability. A 540° fixture
        # with anchors only spanning a 90° arc still has 360°-wrapped
        # branches reachable elsewhere on the pan axis.
        out = []
        for k in _BRANCH_RANGE:
            cand_pan_mech = pan_mech_target + 360.0 * k
            pan_lo, pan_hi = _bracket_pair(cand_pan_mech, self._pan_samples)
            pan_dmx = _interp(cand_pan_mech, pan_lo, pan_hi)
            pan_dmx16 = int(round(pan_dmx))
            if 0 <= pan_dmx <= 65535:
                out.append((pan_dmx16, tilt_dmx16, k))
        if not out:
            # No branch lands in `[0, 65535]` — return the primary
            # branch as a best-effort extrapolation.
            cand_pan_mech = pan_mech_target
            pan_lo, pan_hi = _bracket_pair(cand_pan_mech, self._pan_samples)
            pan_dmx = _interp(cand_pan_mech, pan_lo, pan_hi)
            out.append((int(round(pan_dmx)), tilt_dmx16, 0))
        return out

    def poses_for_xyz(self, target_xyz):
        """Stage-mm target → `[(panDmx16, tiltDmx16, branch_id), ...]`.
        Returns `[]` for a coincident target."""
        result = stage_aim_from_world_xyz(target_xyz, self.fixture_xyz)
        if result is None:
            return []
        return self.poses_for_direction(*result)

    def direction_to_poses(self, az_deg, el_deg):
        """Pre-#798 compat shim — strip branch_id for callers that
        only want `(pan, tilt)` pairs. Returns sorted list."""
        full = self.poses_for_direction(az_deg, el_deg)
        return sorted([(p, t) for p, t, _b in full])

    def dmx_to_aim(self, pan_dmx16, tilt_dmx16):
        """Forward direction: DMX pose → stage-frame `(az_deg, el_deg)`.

        Bracketing interpolation on the inverse axis: find the anchors
        whose DMX brackets the input on each axis, interp mech, project
        to mount frame, project to stage frame.
        """
        assert 0 <= int(pan_dmx16) <= 65535, (
            f"pan_dmx16 out of [0, 65535]: {pan_dmx16}")
        assert 0 <= int(tilt_dmx16) <= 65535, (
            f"tilt_dmx16 out of [0, 65535]: {tilt_dmx16}")
        pan_inv = sorted([(s[1], s[0]) for s in self._pan_samples])
        tilt_inv = sorted([(s[1], s[0]) for s in self._tilt_samples])
        p_lo, p_hi = _bracket_pair(int(pan_dmx16), pan_inv)
        t_lo, t_hi = _bracket_pair(int(tilt_dmx16), tilt_inv)
        pan_mech = _interp(int(pan_dmx16), p_lo, p_hi)
        tilt_mech = _interp(int(tilt_dmx16), t_lo, t_hi)
        mount = mount_from_mech(pan_mech, tilt_mech)
        az_deg, el_deg = mount_to_stage(mount, self.fixture_rotation)
        return az_deg, el_deg

    # ── Picker ────────────────────────────────────────────────────

    @staticmethod
    def _pick_pose(poses, current_pose, prefer):
        """Collapse multi-valued enumeration to a single pose. The
        caller's `prefer` policy decides when more than one branch is
        in range:

            "closest" — minimum DMX travel from `current_pose` (default).
            "A"       — primary branch (k=0); falls back to the lowest
                         panDmx16 if branch 0 was clipped.
            "B"       — highest panDmx16 (the cable-wrap-positive branch).

        Returns `(pan_dmx16, tilt_dmx16)`.
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


def _mech_from_mount(mount_aim):
    """Module-level alias used during `_build_axis_samples`."""
    mx, my, mz = mount_aim
    pan_mech = math.degrees(math.atan2(mx, my))
    tilt_mech = math.degrees(math.atan2(mz, math.hypot(mx, my)))
    return pan_mech, tilt_mech
