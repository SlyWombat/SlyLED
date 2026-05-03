"""aim/sphere.py — `(az, el)`-keyed precomputed lookup table (#784, #785).

Per the #784 comment-3 design: mechanics are derived from `(panRange,
tiltRange, home_pan_dmx16, home_tilt_dmx16)`. No `dmxToMechanical`
profile metadata, no per-axis sign fields. Home is the angular zero;
slope is positive by convention; mount inversion lives in
`fixture.rotation` and composes downstream in `stage_frame`.

Construction inverts the aim formula: walk the DMX grid, evaluate
`dmx_to_mechanical → mechanical_to_stage_aim` for each pose, bin the
result into a 2°×2° stage-frame cell. Each cell holds the list of
DMX rows whose stored `(az, el)` lands in it. Multi-valued cells
(540°-pan fixtures, off-centre Home) hold N≥2 rows.

Runtime aim is dictionary lookup + bilinear blend. Pipeline:

  1. Identify the 4 cells bracketing target `(az, el)`.
  2. From each corner, pick a single representative row using the same
     `prefer` policy + the same `current_pose`. Branch consistency is
     by construction.
  3. Bilinear average the 4 picked rows on both DMX axes. Empty /
     branch-clipped corners drop with weight renormalization.

Clipped targets fall back to the nearest stored row across the table.
`aim_xyz` returns `None` only on a coincident XYZ target.

Failure modes:
  - Missing Home anchor on fixture → constructor raises `ValueError`.
  - Home DMX out of `[0, 65535]` → constructor raises `ValueError`.
  - Profile missing `panRange` / `tiltRange` → constructor raises
    `ValueError` with the profile id.
  - Coincident target on `aim_xyz` → returns `None`.
"""

import math

from .profile_mechanics import dmx_to_mechanical
from .stage_frame import (
    mechanical_to_stage_aim, stage_aim_from_world_xyz,
)


# Cell size = 2° per architectural commitment in #785.
CELL_SIZE_DEG = 2.0

# DMX walk granularity used at construction. 256 samples per axis (=
# step 256 in 16-bit DMX) yields 65k forward evaluations — enough to
# fully populate the 2° cell map for fixtures with panRange ≤ 540°.
DEFAULT_DMX_STEP = 256


def _cell_key(az_deg, el_deg):
    """Map a stage-frame direction to its 2° cell key."""
    return (int(math.floor(az_deg / CELL_SIZE_DEG)),
            int(math.floor(el_deg / CELL_SIZE_DEG)))


class AimSphere:
    """Per-fixture precomputed `(az, el) → DMX` lookup."""

    __slots__ = (
        "fixture_xyz", "fixture_rotation",
        "home_pan_dmx16", "home_tilt_dmx16",
        "pan_range_deg", "tilt_range_deg",
        "_step",
        "_cell_index",     # dict[(int_az, int_el)] → list of rows
        "_all_rows",       # flat list of all rows (clipped fallback)
    )

    def __init__(self, fixture, profile, *, step=DEFAULT_DMX_STEP):
        if not isinstance(fixture, dict):
            raise ValueError(
                f"fixture must be a dict, got {type(fixture).__name__}")
        if not isinstance(profile, dict):
            raise ValueError(
                f"profile must be a dict, got {type(profile).__name__}")

        # Profile must declare panRange + tiltRange. The 0-or-missing
        # case applies to non-moving-head profiles which shouldn't be
        # consulted here at all.
        pan_range = profile.get("panRange")
        tilt_range = profile.get("tiltRange")
        if not pan_range or not tilt_range:
            pid = profile.get("id", "<unknown>")
            raise ValueError(
                f"profile {pid!r} has no pan/tilt range — not a moving head")
        self.pan_range_deg = float(pan_range)
        self.tilt_range_deg = float(tilt_range)

        # Fixture position + rotation.
        x = fixture.get("x") or 0.0
        y = fixture.get("y") or 0.0
        z = fixture.get("z") or 0.0
        self.fixture_xyz = (float(x), float(y), float(z))
        rot = fixture.get("rotation") or [0.0, 0.0, 0.0]
        rot = (list(rot) + [0.0, 0.0, 0.0])[:3]
        self.fixture_rotation = [float(rot[0]), float(rot[1]), float(rot[2])]

        # Home anchor — required.
        h_pan = fixture.get("homePanDmx16")
        h_tilt = fixture.get("homeTiltDmx16")
        if h_pan is None or h_tilt is None:
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} has no Home "
                "anchor — set Home before constructing AimSphere")
        h_pan = int(h_pan)
        h_tilt = int(h_tilt)
        if not (0 <= h_pan <= 65535):
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} homePanDmx16="
                f"{h_pan} out of [0, 65535]")
        if not (0 <= h_tilt <= 65535):
            raise ValueError(
                f"fixture {fixture.get('id', '<unknown>')} homeTiltDmx16="
                f"{h_tilt} out of [0, 65535]")
        self.home_pan_dmx16 = h_pan
        self.home_tilt_dmx16 = h_tilt

        if int(step) < 1:
            raise ValueError("step must be >= 1")
        self._step = int(step)

        self._cell_index = {}
        self._all_rows = []
        self._build_table()

    # ── Construction ───────────────────────────────────────────────

    def _build_table(self):
        step = self._step
        rot = self.fixture_rotation
        h_pan = self.home_pan_dmx16
        h_tilt = self.home_tilt_dmx16
        pan_range = self.pan_range_deg
        tilt_range = self.tilt_range_deg

        pan_grid = list(range(0, 65536, step))
        if pan_grid[-1] != 65535:
            pan_grid.append(65535)
        tilt_grid = list(range(0, 65536, step))
        if tilt_grid[-1] != 65535:
            tilt_grid.append(65535)

        for pdmx in pan_grid:
            for tdmx in tilt_grid:
                mech_p, mech_t = dmx_to_mechanical(
                    pdmx, tdmx, pan_range, tilt_range, h_pan, h_tilt)
                az, el = mechanical_to_stage_aim(mech_p, mech_t, rot)
                row = (pdmx, tdmx, az, el)
                self._all_rows.append(row)
                key = _cell_key(az, el)
                self._cell_index.setdefault(key, []).append(row)

    # ── Internal: pick + bilinear blend ────────────────────────────

    @staticmethod
    def _pick_from_cell(rows, prefer, current_pose):
        """Collapse a corner cell's rows to a single representative
        per the `prefer` policy. Returns None when `rows` is empty."""
        if not rows:
            return None
        if prefer == "A":
            return min(rows, key=lambda r: r[0])
        if prefer == "B":
            return max(rows, key=lambda r: r[0])
        cp_pan, cp_tilt = current_pose
        return min(rows,
                    key=lambda r: abs(r[0] - cp_pan) + abs(r[1] - cp_tilt))

    def _nearest_row_to(self, az_deg, el_deg):
        """O(N) scan across `_all_rows` for the row whose stored
        `(az, el)` is closest to the target — clipped fallback."""
        if not self._all_rows:
            return None
        return min(self._all_rows,
                    key=lambda r: math.hypot(r[2] - az_deg, r[3] - el_deg))

    def _bilinear_blend(self, az_deg, el_deg, prefer, current_pose):
        """Pick-first-then-average pipeline. Returns
        `(pan_dmx16, tilt_dmx16)` blended across the 4 bracketing cells,
        or `None` when all 4 corners are empty."""
        az_idx = az_deg / CELL_SIZE_DEG
        el_idx = el_deg / CELL_SIZE_DEG
        az_lo = int(math.floor(az_idx))
        az_hi = az_lo + 1
        el_lo = int(math.floor(el_idx))
        el_hi = el_lo + 1
        fa = az_idx - az_lo
        fe = el_idx - el_lo
        weights = (
            ((az_lo, el_lo), (1.0 - fa) * (1.0 - fe)),
            ((az_hi, el_lo), fa * (1.0 - fe)),
            ((az_lo, el_hi), (1.0 - fa) * fe),
            ((az_hi, el_hi), fa * fe),
        )

        picks = []
        for cell, w in weights:
            picked = self._pick_from_cell(
                self._cell_index.get(cell, ()), prefer, current_pose)
            if picked is not None and w > 0.0:
                picks.append((picked, w))

        if not picks:
            return None

        total = sum(w for _, w in picks)
        if total <= 0.0:
            total = float(len(picks))
            picks = [(r, 1.0) for r, _ in picks]
        pan_blend = sum(r[0] * w for r, w in picks) / total
        tilt_blend = sum(r[1] * w for r, w in picks) / total
        return (int(round(pan_blend)), int(round(tilt_blend)))

    # ── Public API ─────────────────────────────────────────────────

    def aim_direction(self, az_deg, el_deg, current_pose=None,
                       prefer="closest"):
        """Stage-frame aim → `(pan_dmx16, tilt_dmx16)`. Always returns
        a pose; clipped targets return the nearest stored row."""
        assert -180.0 <= float(az_deg) <= 180.0, (
            f"az_deg out of [-180, 180]: {az_deg}")
        assert -90.0 <= float(el_deg) <= 90.0, (
            f"el_deg out of [-90, 90]: {el_deg}")

        if current_pose is None:
            current_pose = (self.home_pan_dmx16, self.home_tilt_dmx16)
        else:
            current_pose = (int(current_pose[0]), int(current_pose[1]))

        result = self._bilinear_blend(az_deg, el_deg, prefer, current_pose)
        if result is None:
            nearest = self._nearest_row_to(az_deg, el_deg)
            if nearest is None:
                return None
            return (int(nearest[0]), int(nearest[1]))
        return result

    def aim_xyz(self, target_xyz, current_pose=None, prefer="closest"):
        """Stage-mm target → `(pan_dmx16, tilt_dmx16)`. Returns `None`
        when target is coincident with the fixture position."""
        result = stage_aim_from_world_xyz(target_xyz, self.fixture_xyz)
        if result is None:
            return None
        return self.aim_direction(*result, current_pose=current_pose,
                                    prefer=prefer)

    def direction_to_poses(self, az_deg, el_deg):
        """Return all `(pan_dmx16, tilt_dmx16)` rows in the cell
        containing `(az, el)`. Multi-valued cells return ≥2 entries.
        Empty cell returns `[]`."""
        assert -180.0 <= float(az_deg) <= 180.0, (
            f"az_deg out of [-180, 180]: {az_deg}")
        assert -90.0 <= float(el_deg) <= 90.0, (
            f"el_deg out of [-90, 90]: {el_deg}")
        rows = self._cell_index.get(_cell_key(az_deg, el_deg), [])
        return sorted([(int(r[0]), int(r[1])) for r in rows])

    def dmx_to_aim(self, pan_dmx16, tilt_dmx16):
        """Forward direction: DMX pose → stage-frame `(az, el)`.

        Bilinear interpolation across the 4 bracketing rows in the
        DMX grid — pure table lookup, no slope math."""
        assert 0 <= int(pan_dmx16) <= 65535, (
            f"pan_dmx16 out of [0, 65535]: {pan_dmx16}")
        assert 0 <= int(tilt_dmx16) <= 65535, (
            f"tilt_dmx16 out of [0, 65535]: {tilt_dmx16}")
        step = self._step
        pan_lo = (int(pan_dmx16) // step) * step
        pan_hi = min(65535, pan_lo + step)
        tilt_lo = (int(tilt_dmx16) // step) * step
        tilt_hi = min(65535, tilt_lo + step)
        fp = (int(pan_dmx16) - pan_lo) / max(1, pan_hi - pan_lo) if pan_hi > pan_lo else 0.0
        ft = (int(tilt_dmx16) - tilt_lo) / max(1, tilt_hi - tilt_lo) if tilt_hi > tilt_lo else 0.0

        def _row_at(p, t):
            for r in self._all_rows:
                if r[0] == p and r[1] == t:
                    return r
            return None
        c00 = _row_at(pan_lo, tilt_lo)
        c10 = _row_at(pan_hi, tilt_lo)
        c01 = _row_at(pan_lo, tilt_hi)
        c11 = _row_at(pan_hi, tilt_hi)
        corners = [
            (c00, (1.0 - fp) * (1.0 - ft)),
            (c10, fp * (1.0 - ft)),
            (c01, (1.0 - fp) * ft),
            (c11, fp * ft),
        ]
        corners = [(r, w) for r, w in corners if r is not None and w > 0]
        if not corners:
            for r in self._all_rows:
                if r[0] == int(pan_dmx16) and r[1] == int(tilt_dmx16):
                    return (r[2], r[3])
            return (0.0, 0.0)
        total = sum(w for _, w in corners)
        az = sum(r[2] * w for r, w in corners) / total
        el = sum(r[3] * w for r, w in corners) / total
        return (az, el)
