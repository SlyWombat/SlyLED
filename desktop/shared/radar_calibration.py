"""radar_calibration.py — #913 radar calibration walk: recorder + pose solver.

Design doc: docs/design/mmwave_tracking.md §6 layer 2 (track-correlation
self-calibration), seeded by layer 1 (the operator's manual survey pose).

One person walks a loop covering the sensor overlap zones while this
module records every bound radar observation in SENSOR frame (fixture
id, x/y mm, monotonic timestamp) via the lightweight hook radar_fusion
exposes (`RadarFusion.record_observation` — a no-op None when idle, so
the tracking hot path pays nothing outside a calibration walk).

Solver (per non-reference fixture):

  1. Project the reference radar's recorded trajectory through its
     CURRENT (layer-1 manual) pose → the reference stage trajectory.
     The reference defaults to the radar with the most observations;
     callers may pin it explicitly.
  2. Project the fixture's own trajectory through its own current
     (possibly wrong) pose → the locally-projected trajectory.
  3. Time-align: resample both onto a common 100 ms grid by linear
     interpolation, keeping only the overlapping span; require ≥5 s of
     overlap or fail that fixture with a human-readable message.
  4. Solve the 2-D rigid transform (rotation θ + translation t; Kabsch/
     Procrustes on the z=0 plane, NO scaling) mapping the local
     trajectory onto the reference trajectory:  ref ≈ R(θ)·loc + t.

Because the local trajectory is  loc(s) = p_old + M(pose_old)·s  (s =
sensor-frame point; M = plan-view of the #586/#600 rotation), applying
the solved stage transform composes to a corrected pose directly:

  new position = R(θ)·p_old + t                 (x/y only, z kept)
  new yaw      = old pan − θ°                   (M(pan) is the CW-of-
                 math-convention plan rotation R_math(−pan), so folding
                 a stage-frame CCW θ in means pan decreases by θ)

Only the pan/yaw component of the rotation changes; tilt and roll are
the physical mount and pass through untouched. Rotation arrays are
decoded/encoded ONLY via camera_math.rotation_from_layout /
rotation_to_layout (CLAUDE.md #586/#600).

The routine assumes a single walker: the Rd-03D reports up to 3
targets and all healthy observations are recorded, so a second person
during the walk degrades the fit (visible as a high RMS residual)
rather than crashing anything.

Thread-safety: record() is called from parent_server's UDP listener
thread (under radar_fusion's lock); start/stop/solve run on Flask
request threads. All state here is guarded by this module's own lock.
Lock order is radar_fusion lock → this lock; nothing here calls back
into radar_fusion, so no inversion is possible.

No third-party dependencies (plain-float math, like radar_fusion).
"""

import math
import threading
import time

from camera_math import rotation_from_layout, rotation_to_layout
from radar_fusion import project_to_stage

# Resample grid + gates. GRID_S matches the design pin (100 ms); the
# 5 s overlap floor keeps the Procrustes fit from locking onto a
# geometry-free snippet (a straight 2-step segment fits anything).
GRID_S = 0.1
MIN_OVERLAP_S = 5.0
MIN_SAMPLES = 10         # per-fixture floor to count as "recorded"


class SolveError(ValueError):
    """Human-readable solver failure (mapped to HTTP 400 by the API)."""


def _norm_deg(a):
    """Normalise an angle to (-180, 180]."""
    a = (float(a) + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def solve_rigid_2d(src, dst):
    """Least-squares 2-D rigid transform (rotation + translation, no
    scale) mapping point set `src` onto `dst` (equal-length lists of
    (x, y)). Kabsch/Procrustes closed form on the plane:

        θ  = atan2( Σ(x̂s·ŷd − ŷs·x̂d ... cross), Σ(dot) )   over demeaned
        t  = centroid(dst) − R(θ)·centroid(src)

    Returns (theta_rad, tx, ty, rms_mm).
    """
    n = len(src)
    if n < 2 or n != len(dst):
        raise SolveError("Rigid solve needs at least 2 paired points")
    csx = sum(p[0] for p in src) / n
    csy = sum(p[1] for p in src) / n
    cdx = sum(p[0] for p in dst) / n
    cdy = sum(p[1] for p in dst) / n
    s_dot = s_cross = 0.0
    for (sx, sy), (dx, dy) in zip(src, dst):
        ax, ay = sx - csx, sy - csy
        bx, by = dx - cdx, dy - cdy
        s_dot += ax * bx + ay * by
        s_cross += ax * by - ay * bx
    theta = math.atan2(s_cross, s_dot)
    c, s = math.cos(theta), math.sin(theta)
    tx = cdx - (c * csx - s * csy)
    ty = cdy - (s * csx + c * csy)
    sq = 0.0
    for (sx, sy), (dx, dy) in zip(src, dst):
        rx = c * sx - s * sy + tx - dx
        ry = s * sx + c * sy + ty - dy
        sq += rx * rx + ry * ry
    return theta, tx, ty, math.sqrt(sq / n)


def _resample(traj, grid):
    """Linear-interpolate a time-sorted [(t, x, y), ...] trajectory at
    each grid time. Grid times must lie within [traj[0].t, traj[-1].t]
    (the caller intersects spans first). Returns [(x, y), ...]."""
    out = []
    i = 0
    last = len(traj) - 1
    for t in grid:
        while i < last and traj[i + 1][0] < t:
            i += 1
        t0, x0, y0 = traj[i]
        if i == last or t <= t0:
            out.append((x0, y0))
            continue
        t1, x1, y1 = traj[i + 1]
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return out


class RadarCalibration:
    """Recording session + solver state for the calibration walk."""

    def __init__(self):
        self._lock = threading.Lock()
        self._recording = False
        self._samples = {}       # fixture id -> [(t_mono, x_mm, y_mm), ...]
        self.started_at = None   # epoch, for the UI
        self.stopped_at = None
        self.last_solve = None   # full result dict from the last solve()

    # -- recording -----------------------------------------------------------

    def start(self):
        """Begin a walk. Returns False (no state change) if one is
        already recording — the API maps that to 409."""
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self._samples = {}
            self.started_at = time.time()
            self.stopped_at = None
            self.last_solve = None   # stale proposals die with a new walk
            return True

    def record(self, fixture_id, x_mm, y_mm, t_mono):
        """Hook target for RadarFusion.record_observation: one bound
        SENSOR-frame observation. Drops silently when not recording (the
        hook is normally detached too — this is belt-and-braces)."""
        with self._lock:
            if not self._recording:
                return
            self._samples.setdefault(fixture_id, []).append(
                (float(t_mono), float(x_mm), float(y_mm)))

    def stop(self):
        """End the walk. Returns per-fixture sample counts, or None if
        no walk was recording."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            self.stopped_at = time.time()
            return {fid: len(s) for fid, s in self._samples.items()}

    @property
    def recording(self):
        with self._lock:
            return self._recording

    def status(self):
        """JSON-safe session summary (includes the last solve so the
        SPA card survives a reload without re-solving)."""
        with self._lock:
            return {
                "recording": self._recording,
                "startedAt": self.started_at,
                "stoppedAt": self.stopped_at,
                "samples": {fid: len(s) for fid, s in self._samples.items()},
                "lastSolve": self.last_solve,
            }

    # -- solving ---------------------------------------------------------------

    def solve(self, poses, reference_fixture_id=None):
        """Solve pose proposals from the recorded walk.

        poses: {fixture_id: {"id", "x", "y", "z", "rotation", "name"}}
        pose snapshots for every radar fixture (current layer-1 values —
        position from the layout store, rotation decoded only via
        rotation_from_layout downstream).

        Returns {"referenceFixtureId", "proposals": [...], "timestamp"};
        each proposal carries proposed pos/yaw + rmsResidualMm + samples,
        or a per-fixture "error" when that fixture lacks overlap. Does
        NOT touch any store — apply is the caller's job. Raises
        SolveError for walk-level problems (<2 fixtures, bad reference).
        """
        with self._lock:
            samples = {fid: list(s) for fid, s in self._samples.items()}

        recorded = {fid: sorted(s) for fid, s in samples.items()
                    if len(s) >= MIN_SAMPLES and fid in poses}
        if len(recorded) < 2:
            raise SolveError(
                "Need recorded trajectories from at least 2 radar fixtures "
                f"to solve — got {len(recorded)}. Walk a loop that passes "
                "through the coverage of two or more radars while recording.")

        if reference_fixture_id is not None:
            ref_id = reference_fixture_id
            if ref_id not in recorded:
                raise SolveError(
                    f"Reference fixture {ref_id} has no usable recording "
                    f"(fixtures with samples: {sorted(recorded)}).")
        else:
            ref_id = max(recorded, key=lambda fid: len(recorded[fid]))

        def _project(fid):
            pose = poses[fid]
            return [(t,) + project_to_stage(pose, x, y)
                    for t, x, y in recorded[fid]]

        ref_traj = _project(ref_id)
        proposals = []
        for fid in sorted(recorded):
            if fid == ref_id:
                continue
            loc_traj = _project(fid)
            t_lo = max(ref_traj[0][0], loc_traj[0][0])
            t_hi = min(ref_traj[-1][0], loc_traj[-1][0])
            overlap = t_hi - t_lo
            if overlap < MIN_OVERLAP_S:
                proposals.append({
                    "fixtureId": fid,
                    "name": poses[fid].get("name"),
                    "error": (f"Only {max(overlap, 0.0):.1f} s of trajectory "
                              f"overlap with the reference — need at least "
                              f"{MIN_OVERLAP_S:.0f} s. Walk longer through "
                              "the zone both radars can see."),
                })
                continue
            grid = [t_lo + i * GRID_S
                    for i in range(int(overlap / GRID_S) + 1)]
            src = _resample(loc_traj, grid)
            dst = _resample(ref_traj, grid)
            theta, tx, ty, rms = solve_rigid_2d(src, dst)

            pose = poses[fid]
            x0 = float(pose.get("x", 0) or 0)
            y0 = float(pose.get("y", 0) or 0)
            c, s = math.cos(theta), math.sin(theta)
            nx = c * x0 - s * y0 + tx
            ny = s * x0 + c * y0 + ty
            tilt, pan, roll = rotation_from_layout(
                pose.get("rotation") or [0, 0, 0])
            new_pan = _norm_deg(pan - math.degrees(theta))
            proposals.append({
                "fixtureId": fid,
                "name": pose.get("name"),
                "current": {"x": x0, "y": y0, "yawDeg": pan},
                "proposed": {
                    "x": nx, "y": ny,
                    "z": float(pose.get("z", 0) or 0),   # z untouched
                    "yawDeg": new_pan,
                    "rotation": rotation_to_layout(tilt, new_pan, roll),
                },
                "deltaPosMm": math.hypot(nx - x0, ny - y0),
                "deltaYawDeg": _norm_deg(new_pan - pan),
                "rmsResidualMm": rms,
                "samples": len(src),
            })

        result = {
            "referenceFixtureId": ref_id,
            "referenceSamples": len(recorded[ref_id]),
            "proposals": proposals,
            "timestamp": time.time(),
        }
        with self._lock:
            self.last_solve = result
        return result
