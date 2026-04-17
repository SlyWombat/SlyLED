"""parametric_mover.py — Kinematic model for moving-head fixtures (#488).

A moving head is a 2-DOF pan/tilt gimbal on a rotated mount. Six parameters
fully describe how DMX pan/tilt values map to a stage-space beam direction:

    mount_yaw_deg   (psi)    — rotation of fixture base around stage Z
    mount_pitch_deg (phi_m)  — forward/backward tilt of the mount
    mount_roll_deg  (rho)    — twist of the mount around its forward axis
    pan_offset      (theta_0)— normalized DMX value at geometric pan zero
    tilt_offset     (phi_0)  — normalized DMX value at geometric tilt zero
    pan_sign        (±1)     — direction of increasing DMX pan
    tilt_sign       (±1)     — direction of increasing DMX tilt

Pan/tilt range in degrees comes from the DMX profile and is not a fit
parameter. Sign handling: ±1 are discrete and cannot be optimized by a
gradient method, so `fit_model()` runs four LM solves (one per sign
combo) and keeps the lowest-residual fit — the whole thing is O(ms) on
modern hardware.

The module is self-contained: no import from mover_calibrator or
parent_server. Consumers plug it in via `ParametricFixtureModel.forward`
(DMX → aim unit vector) and `ParametricFixtureModel.inverse`
(stage target → DMX).

Design: docs/mover-calibration-v2.md §3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Sequence

import numpy as np


# ── Rotation helpers ───────────────────────────────────────────────────────

def _rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def _rot_x(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def _rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def _mount_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """R_mount = R_z(yaw) · R_x(pitch) · R_y(roll). Matches the doc §3.3."""
    return _rot_z(yaw_deg) @ _rot_x(pitch_deg) @ _rot_y(roll_deg)


# ── Model ──────────────────────────────────────────────────────────────────

@dataclass
class ParametricFixtureModel:
    """Full kinematic description of a moving head in stage space.

    The fixture position and pan/tilt ranges are fixed (from layout +
    profile). The six mount/offset/sign parameters are what calibration
    estimates.
    """

    fixture_pos: Tuple[float, float, float]
    pan_range_deg: float = 540.0
    tilt_range_deg: float = 270.0

    mount_yaw_deg: float = 0.0
    mount_pitch_deg: float = 0.0
    mount_roll_deg: float = 0.0
    pan_offset: float = 0.5
    tilt_offset: float = 0.5
    pan_sign: int = 1
    tilt_sign: int = -1   # DMX tilt up → stage Z up, so default sign is negative

    # ── Forward kinematics ────────────────────────────────────────────────

    def forward(self, pan_norm: float, tilt_norm: float) -> Tuple[float, float, float]:
        """Return the beam unit direction in stage coords for a DMX pair."""
        theta_pan_deg = self.pan_sign * (pan_norm - self.pan_offset) * self.pan_range_deg
        theta_tilt_deg = self.tilt_sign * (tilt_norm - self.tilt_offset) * self.tilt_range_deg
        theta_pan = math.radians(theta_pan_deg)
        theta_tilt = math.radians(theta_tilt_deg)

        # Mount-local beam direction (pan around Z, then tilt downward).
        cos_t = math.cos(theta_tilt)
        local = np.array([
            math.sin(theta_pan) * cos_t,
            math.cos(theta_pan) * cos_t,
            -math.sin(theta_tilt),
        ])
        R = _mount_matrix(self.mount_yaw_deg, self.mount_pitch_deg, self.mount_roll_deg)
        out = R @ local
        n = float(np.linalg.norm(out))
        if n < 1e-12:
            return (0.0, 1.0, 0.0)
        return (float(out[0] / n), float(out[1] / n), float(out[2] / n))

    # ── Inverse kinematics ─────────────────────────────────────────────────

    def inverse(self, target_x: float, target_y: float, target_z: float
                 ) -> Tuple[float, float]:
        """Return the (pan_norm, tilt_norm) that aim at a stage point.

        Clamped to [0, 1]. Singularity (target directly above/below) is
        handled by pinning pan at its current offset so tilt still resolves.
        """
        px, py, pz = self.fixture_pos
        v = np.array([target_x - px, target_y - py, target_z - pz])
        R = _mount_matrix(self.mount_yaw_deg, self.mount_pitch_deg, self.mount_roll_deg)
        local = R.T @ v
        dx, dy, dz = float(local[0]), float(local[1]), float(local[2])

        horiz = math.hypot(dx, dy)
        if horiz < 1e-6:
            # Target is along the mount's up/down axis — pan is degenerate.
            theta_pan_deg = 0.0
        else:
            theta_pan_deg = math.degrees(math.atan2(dx, dy))
        theta_tilt_deg = math.degrees(math.atan2(-dz, horiz))

        pan_norm = self.pan_offset + self.pan_sign * theta_pan_deg / self.pan_range_deg
        tilt_norm = self.tilt_offset + self.tilt_sign * theta_tilt_deg / self.tilt_range_deg
        return (max(0.0, min(1.0, pan_norm)), max(0.0, min(1.0, tilt_norm)))

    # ── Serialization ──────────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        return {
            "mountYaw":      self.mount_yaw_deg,
            "mountPitch":    self.mount_pitch_deg,
            "mountRoll":     self.mount_roll_deg,
            "panOffset":     self.pan_offset,
            "tiltOffset":    self.tilt_offset,
            "panSign":       int(self.pan_sign),
            "tiltSign":      int(self.tilt_sign),
            "panRangeDeg":   self.pan_range_deg,
            "tiltRangeDeg":  self.tilt_range_deg,
        }

    @classmethod
    def from_dict(cls, fixture_pos: Tuple[float, float, float],
                  d: Dict) -> "ParametricFixtureModel":
        return cls(
            fixture_pos=tuple(fixture_pos),
            pan_range_deg=float(d.get("panRangeDeg", 540.0)),
            tilt_range_deg=float(d.get("tiltRangeDeg", 270.0)),
            mount_yaw_deg=float(d.get("mountYaw", 0.0)),
            mount_pitch_deg=float(d.get("mountPitch", 0.0)),
            mount_roll_deg=float(d.get("mountRoll", 0.0)),
            pan_offset=float(d.get("panOffset", 0.5)),
            tilt_offset=float(d.get("tiltOffset", 0.5)),
            pan_sign=int(d.get("panSign", 1)),
            tilt_sign=int(d.get("tiltSign", -1)),
        )


# ── Levenberg-Marquardt fit ────────────────────────────────────────────────

def _unpack_samples(samples: Sequence) -> List[Tuple[float, float, float, float, float]]:
    """Coerce heterogeneous sample formats into (pan, tilt, x, y, z) tuples."""
    out = []
    for s in samples:
        if isinstance(s, dict):
            out.append((
                float(s["pan"]), float(s["tilt"]),
                float(s["stageX"]), float(s["stageY"]),
                float(s.get("stageZ", 0.0)),
            ))
        else:
            out.append((
                float(s[0]), float(s[1]), float(s[2]), float(s[3]),
                float(s[4]) if len(s) >= 5 else 0.0,
            ))
    return out


def _residuals(model: ParametricFixtureModel,
               samples: List[Tuple[float, float, float, float, float]]
               ) -> np.ndarray:
    """Per-sample (pan_err, tilt_err) in normalized DMX space, flattened."""
    res = np.empty(2 * len(samples))
    for i, (pan_n, tilt_n, tx, ty, tz) in enumerate(samples):
        pp, pt = model.inverse(tx, ty, tz)
        res[2 * i]     = pan_n - pp
        res[2 * i + 1] = tilt_n - pt
    return res


def _angular_error_deg(model: ParametricFixtureModel,
                        samples: List[Tuple[float, float, float, float, float]]
                        ) -> List[float]:
    """Per-sample beam misalignment in degrees between model forward and observation."""
    errs: List[float] = []
    px, py, pz = model.fixture_pos
    for pan_n, tilt_n, tx, ty, tz in samples:
        d = np.array(model.forward(pan_n, tilt_n))
        obs = np.array([tx - px, ty - py, tz - pz])
        n_obs = np.linalg.norm(obs)
        n_d = np.linalg.norm(d)
        if n_obs < 1e-9 or n_d < 1e-9:
            errs.append(0.0)
            continue
        c = float(np.clip(np.dot(d, obs) / (n_d * n_obs), -1.0, 1.0))
        errs.append(math.degrees(math.acos(c)))
    return errs


# Parameter vector layout during LM:  [yaw, pitch, roll, pan_off, tilt_off]
_PARAM_KEYS = ("mount_yaw_deg", "mount_pitch_deg", "mount_roll_deg",
               "pan_offset", "tilt_offset")


def _apply_params(model: ParametricFixtureModel, p: np.ndarray) -> None:
    model.mount_yaw_deg = float(p[0])
    model.mount_pitch_deg = float(p[1])
    model.mount_roll_deg = float(p[2])
    model.pan_offset = float(p[3])
    model.tilt_offset = float(p[4])


def _lm_solve(model: ParametricFixtureModel,
              samples: List[Tuple[float, float, float, float, float]],
              max_iter: int = 120, tol: float = 1e-10) -> float:
    """Plain Levenberg-Marquardt over the five continuous parameters.

    Numerical Jacobian via central differences — five columns, so cost
    is trivially small (~10 residual evaluations per iteration).
    Returns the final residual L2 norm.

    Note: LM minimises ||r||² with the update p_new = p − (JᵀJ + λ·diag(JᵀJ))⁻¹ Jᵀr.
    Step sizes are tuned to produce measurable residual change — a 1e-3°
    angle step is below float noise for a 5 m stage distance, so we use
    0.3° for angles and 3e-3 for pan/tilt offsets (~1.6° of deflection).
    """
    p = np.array([getattr(model, k) for k in _PARAM_KEYS], dtype=float)
    lam = 1e-2
    r = _residuals(model, samples)
    f = float(np.dot(r, r))
    step = np.array([0.3, 0.3, 0.3, 3e-3, 3e-3])

    for _ in range(max_iter):
        # Numerical Jacobian via central differences.
        J = np.empty((len(r), len(p)))
        for k in range(len(p)):
            h = step[k]
            p_plus = p.copy(); p_plus[k] += h
            p_minus = p.copy(); p_minus[k] -= h
            _apply_params(model, p_plus)
            r_plus = _residuals(model, samples)
            _apply_params(model, p_minus)
            r_minus = _residuals(model, samples)
            J[:, k] = (r_plus - r_minus) / (2.0 * h)
        _apply_params(model, p)  # restore

        JTJ = J.T @ J
        g = J.T @ r
        damp = lam * np.diag(np.diag(JTJ))
        try:
            dp = np.linalg.solve(JTJ + damp, g)
        except np.linalg.LinAlgError:
            lam *= 10.0
            if lam > 1e8:
                break
            continue

        p_new = p - dp  # descend the gradient of ||r||²
        _apply_params(model, p_new)
        r_new = _residuals(model, samples)
        f_new = float(np.dot(r_new, r_new))

        if f_new < f:
            lam = max(lam / 3.0, 1e-8)
            converged = abs(f - f_new) < tol * max(1.0, f)
            p = p_new
            r = r_new
            f = f_new
            if converged:
                break
        else:
            _apply_params(model, p)
            lam *= 3.0
            if lam > 1e8:
                break

    _apply_params(model, p)
    return math.sqrt(f)


@dataclass
class FitQuality:
    rms_error_deg: float
    max_error_deg: float
    sample_count: int
    condition_number: float
    per_sample_deg: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "rmsErrorDeg":     self.rms_error_deg,
            "maxErrorDeg":     self.max_error_deg,
            "sampleCount":     self.sample_count,
            "conditionNumber": self.condition_number,
        }


def fit_model(fixture_pos: Tuple[float, float, float],
              pan_range_deg: float,
              tilt_range_deg: float,
              samples: Sequence,
              mounted_inverted: bool = False,
              ) -> Tuple[ParametricFixtureModel, FitQuality]:
    """Fit the parametric model to calibration samples.

    Runs LM over the five continuous parameters for every combination of
    (pan_sign, tilt_sign) ∈ {±1}² and returns the best fit. Requires at
    least 2 non-colinear samples for a meaningful result, though quality
    degrades fast below 4.
    """
    pts = _unpack_samples(samples)
    if len(pts) < 2:
        raise ValueError("Need at least 2 calibration samples to fit a model.")

    # Collect every fit. The parametric model has a real sign ambiguity:
    # flipping both signs and yaw by 180° produces the same forward beam, so
    # two or more sign combos can converge to the same residual. We tie-break
    # toward the project convention (pan_sign=+1, tilt_sign=-1) when a
    # candidate is within 0.2° RMS of the best — the convention matches
    # `pan_tilt_to_ray` and SPA rendering, avoiding surprising flip-outs
    # during extrapolation.
    candidates: List[Tuple[float, int, ParametricFixtureModel, FitQuality]] = []

    for pan_sign in (1, -1):
        for tilt_sign in (1, -1):
            m = ParametricFixtureModel(
                fixture_pos=tuple(fixture_pos),
                pan_range_deg=pan_range_deg,
                tilt_range_deg=tilt_range_deg,
                mount_yaw_deg=0.0,
                mount_pitch_deg=180.0 if mounted_inverted else 0.0,
                mount_roll_deg=0.0,
                pan_offset=0.5,
                tilt_offset=0.5,
                pan_sign=pan_sign,
                tilt_sign=tilt_sign,
            )
            try:
                _lm_solve(m, pts)
            except Exception:
                continue
            errs = _angular_error_deg(m, pts)
            if not errs:
                continue
            rms = math.sqrt(sum(e * e for e in errs) / len(errs))
            # Convention preference: 0 if (+1, -1), 1 otherwise — cheap sort key.
            conv_dist = 0 if (pan_sign == 1 and tilt_sign == -1) else 1
            quality = FitQuality(
                rms_error_deg=rms,
                max_error_deg=max(errs),
                sample_count=len(pts),
                condition_number=_condition_number(m, pts),
                per_sample_deg=errs,
            )
            candidates.append((rms, conv_dist, m, quality))

    if not candidates:
        raise RuntimeError("LM failed on every sign combination.")

    # Sort by (rms, conv_dist) then tie-break: anything within 0.2° of the
    # best RMS is considered "equally good", so the convention winner wins.
    candidates.sort(key=lambda c: (c[0], c[1]))
    best_rms = candidates[0][0]
    near = [c for c in candidates if c[0] - best_rms < 0.2]
    near.sort(key=lambda c: (c[1], c[0]))
    _, _, best, best_quality = near[0]
    return best, best_quality


def _condition_number(model: ParametricFixtureModel,
                       samples: List[Tuple[float, float, float, float, float]]
                       ) -> float:
    """Condition number of the residual Jacobian — a rough indicator of
    sample spatial spread. High numbers mean ill-conditioned (colinear
    samples, insufficient coverage)."""
    p = np.array([getattr(model, k) for k in _PARAM_KEYS], dtype=float)
    r = _residuals(model, samples)
    step = np.array([0.3, 0.3, 0.3, 3e-3, 3e-3])
    J = np.empty((len(r), len(p)))
    for k in range(len(p)):
        h = step[k]
        p_plus = p.copy(); p_plus[k] += h
        p_minus = p.copy(); p_minus[k] -= h
        _apply_params(model, p_plus); r_plus = _residuals(model, samples)
        _apply_params(model, p_minus); r_minus = _residuals(model, samples)
        J[:, k] = (r_plus - r_minus) / (2.0 * h)
    _apply_params(model, p)
    try:
        s = np.linalg.svd(J, compute_uv=False)
    except np.linalg.LinAlgError:
        return float("inf")
    s_max = float(s.max())
    s_min = float(s[s > 1e-12].min()) if any(s > 1e-12) else 1e-12
    return s_max / s_min
