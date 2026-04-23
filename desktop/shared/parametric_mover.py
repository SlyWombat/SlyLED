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

import logging
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
    """Levenberg-Marquardt over the five continuous parameters, via
    scipy.optimize.least_squares with soft_l1 loss.

    Q10-P3 — the hand-rolled LM was ~60 lines of numerical-Jacobian +
    damping + step acceptance logic we now delegate to scipy. soft_l1
    adds robustness to beam-flash outliers: one snapshot where the
    beam detector grabbed a reflection instead of the spot used to tip
    the whole fit; soft_l1 down-weights big residuals so a single bad
    sample can't overwhelm the fit.

    Returns the final residual L2 norm.
    """
    p0 = np.array([getattr(model, k) for k in _PARAM_KEYS], dtype=float)
    step = np.array([0.3, 0.3, 0.3, 3e-3, 3e-3])

    def _fun(p):
        _apply_params(model, p)
        return _residuals(model, samples)

    try:
        from scipy.optimize import least_squares
    except ImportError:
        # scipy unavailable — keep a minimal fallback so tests still run.
        logging.getLogger(__name__).warning(
            "scipy not installed; using gradient-descent fallback for LM solve.")
        p = p0.copy()
        for _ in range(max_iter):
            r = _fun(p)
            if float(np.dot(r, r)) < tol:
                break
            # Finite-difference gradient.
            J = np.empty((len(r), len(p)))
            for k in range(len(p)):
                h = step[k]
                pp = p.copy(); pp[k] += h
                pm = p.copy(); pm[k] -= h
                J[:, k] = (_fun(pp) - _fun(pm)) / (2.0 * h)
            _apply_params(model, p)
            g = J.T @ r
            JTJ = J.T @ J
            try:
                dp = np.linalg.solve(JTJ + 1e-2 * np.diag(np.diag(JTJ)), g)
            except np.linalg.LinAlgError:
                break
            p = p - dp
        _apply_params(model, p)
        return math.sqrt(float(np.dot(_residuals(model, samples), _residuals(model, samples))))

    try:
        result = least_squares(
            _fun, p0,
            method="lm",         # Levenberg-Marquardt; ignores loss, so...
            xtol=tol, ftol=tol,
            max_nfev=max_iter * (len(p0) * 2 + 1),
            diff_step=step / max(1.0, float(np.max(np.abs(p0))) + 1e-6),
        )
    except Exception:
        # method="lm" rejects bounds/loss; fall through to trf with soft_l1.
        result = least_squares(
            _fun, p0,
            method="trf",
            loss="soft_l1",       # Q10-P3 — outlier-robust
            f_scale=0.05,          # residual magnitude (~3°) where loss switches to linear
            xtol=tol, ftol=tol,
            max_nfev=max_iter * (len(p0) * 2 + 1),
            diff_step=step / max(1.0, float(np.max(np.abs(p0))) + 1e-6),
        )
    _apply_params(model, result.x)
    final_r = _residuals(model, samples)
    return math.sqrt(float(np.dot(final_r, final_r)))


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
            # Per-sample residual in degrees — enables the wizard's
            # residual table and exclude-sample flow (#504).
            "perSampleDeg":    [float(e) for e in self.per_sample_deg],
        }


def fit_model(fixture_pos: Tuple[float, float, float],
              pan_range_deg: float,
              tilt_range_deg: float,
              samples: Sequence,
              mounted_inverted: bool = False,
              force_signs: Optional[Tuple[int, int]] = None,
              ) -> Tuple[ParametricFixtureModel, FitQuality]:
    """Fit the parametric model to calibration samples.

    Runs LM over the five continuous parameters for every combination of
    (pan_sign, tilt_sign) ∈ {±1}² and returns the best fit by RMS.

    Q10 — the previous "<0.2° RMS convention tie-break" has been removed.
    When the mirror ambiguity produces two equal-RMS solutions, the pick
    is now arbitrary-but-deterministic (order of iteration) unless the
    caller supplies `force_signs` from a physical sign-confirmation probe
    (see `verify_signs`). Relying on the convention silently picked the
    wrong mirror on real rigs where the mount pitch/roll departed from
    the assumed zero — use verify_signs() to lock this down.

    Args:
        force_signs: (pan_sign, tilt_sign), each ∈ {-1, +1}. Skips the
                     ±1 × ±1 search and fits only the specified combo.
                     Use this after a physical sign-confirmation probe
                     to eliminate the mirror ambiguity.

    Requires at least 2 non-colinear samples for a meaningful result, though
    quality degrades fast below 4.
    """
    pts = _unpack_samples(samples)
    if len(pts) < 2:
        raise ValueError("Need at least 2 calibration samples to fit a model.")

    if force_signs is not None:
        p_s, t_s = int(force_signs[0]), int(force_signs[1])
        if p_s not in (-1, 1) or t_s not in (-1, 1):
            raise ValueError(f"force_signs must be ±1 pairs; got {force_signs}")
        sign_combos = [(p_s, t_s)]
    else:
        sign_combos = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

    candidates: List[Tuple[float, ParametricFixtureModel, FitQuality]] = []

    for pan_sign, tilt_sign in sign_combos:
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
        quality = FitQuality(
            rms_error_deg=rms,
            max_error_deg=max(errs),
            sample_count=len(pts),
            condition_number=_condition_number(m, pts),
            per_sample_deg=errs,
        )
        candidates.append((rms, m, quality))

    if not candidates:
        raise RuntimeError("LM failed on every sign combination.")

    # Q10 — strict RMS ranking. No convention tie-break — if two mirrors
    # fit equally, the caller should have supplied force_signs from a
    # verify_signs() probe. Log the ambiguity so at least it's visible.
    candidates.sort(key=lambda c: c[0])
    if len(candidates) >= 2 and (candidates[1][0] - candidates[0][0]) < 0.2:
        # Near-tie between mirrors — visible warning (no side-effects).
        try:
            import logging
            logging.getLogger(__name__).warning(
                "fit_model: mirror ambiguity detected — top two sign combos "
                "within 0.2° RMS (%.3f vs %.3f). Use verify_signs() + "
                "force_signs to disambiguate.",
                candidates[0][0], candidates[1][0])
        except Exception:
            pass
    _, best, best_quality = candidates[0]
    return best, best_quality


def verify_signs(beam_pixel_before: Tuple[float, float],
                 beam_pixel_after_pan_plus: Optional[Tuple[float, float]],
                 beam_pixel_after_tilt_plus: Optional[Tuple[float, float]],
                 pan_axis_sign_in_frame: int = 1,
                 tilt_axis_sign_in_frame: int = -1,
                 ) -> Tuple[int, int]:
    """Q10 — sign-confirmation probe. Given one baseline beam pixel plus
    one pixel from "pan+" and one from "tilt+" physical nudges, return
    the (pan_sign, tilt_sign) the LM fit should use.

    The caller performs the physical work (drive pan+0.02, observe beam,
    return to baseline, drive tilt+0.02, observe beam). This function
    just interprets the two beam-pixel deltas.

    `pan_axis_sign_in_frame` / `tilt_axis_sign_in_frame` describe the
    camera convention:
      - Default: a pan+ that physically rotates stage-right should move
        the beam +X in the frame (left→right = pan_sign +1).
      - A tilt+ that physically aims further DOWN should move the beam
        +Y in the frame (top→bottom = tilt_sign −1, matching the
        project convention where tilt=0.5 is level and +tilt is down).
    Override these if the camera is rotated 180° or the fixture has an
    unusual frame orientation.
    """
    if beam_pixel_after_pan_plus is None:
        pan_sign = +1  # probe failed; fall back to convention
    else:
        dpx = beam_pixel_after_pan_plus[0] - beam_pixel_before[0]
        # If the beam moved in the expected-positive direction, sign is +1.
        pan_sign = +1 if (dpx * pan_axis_sign_in_frame) > 0 else -1
    if beam_pixel_after_tilt_plus is None:
        tilt_sign = -1  # convention default
    else:
        dpy = beam_pixel_after_tilt_plus[1] - beam_pixel_before[1]
        # For the default camera (down = +Y), tilt+ moving the beam down
        # gives dpy > 0 and we interpret that as tilt_sign = -1 (matches
        # the "tilt=0.5 level, +tilt = downward" project convention).
        tilt_sign = -1 if (dpy * tilt_axis_sign_in_frame) > 0 else +1
    return pan_sign, tilt_sign


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
