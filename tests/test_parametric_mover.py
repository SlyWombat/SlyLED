"""Parametric mover tests (#488, #495).

Synthetic-only: generate samples from a known ground-truth model, fit a
fresh model, check we recover the parameters and round-trip cleanly.
Also exercises sign-handling, outlier detection, and migration from the
v1 affine sample format.

Run:
    python -X utf8 tests/test_parametric_mover.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from parametric_mover import (  # noqa: E402
    ParametricFixtureModel, fit_model, FitQuality,
)


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


def _close(a, b, tol, msg):
    _assert(abs(a - b) < tol, f"{msg} got={a} want≈{b} tol={tol}")


# ── Forward/inverse identity ─────────────────────────────────────────────

def test_forward_identity():
    """With default parameters, model.forward at pan=tilt=0.5 points +Y."""
    m = ParametricFixtureModel(fixture_pos=(0, 0, 2000))
    dx, dy, dz = m.forward(0.5, 0.5)
    _close(dx, 0.0, 1e-9, "identity: dx")
    _close(dy, 1.0, 1e-9, "identity: dy")
    _close(dz, 0.0, 1e-9, "identity: dz")


def test_forward_tilt_down():
    """Positive tilt delta (tilt_sign=-1 default, so tilt_norm > 0.5 → beam
    rotates toward +Z stage (up)). Sanity check the sign convention."""
    m = ParametricFixtureModel(fixture_pos=(0, 0, 2000))
    _, _, dz_up = m.forward(0.5, 0.7)
    _assert(dz_up > 0.0, f"tilt up: dz>0 got {dz_up}")
    _, _, dz_dn = m.forward(0.5, 0.3)
    _assert(dz_dn < 0.0, f"tilt down: dz<0 got {dz_dn}")


def test_forward_pan_right():
    """Positive pan delta (pan_sign=+1 default) should move beam +X (stage right)."""
    m = ParametricFixtureModel(fixture_pos=(0, 0, 2000))
    dx, _, _ = m.forward(0.6, 0.5)
    _assert(dx > 0.0, f"pan right: dx>0 got {dx}")


def test_inverse_roundtrip_identity():
    """forward(pan, tilt) then inverse(target_at_that_direction) recovers (pan, tilt)."""
    m = ParametricFixtureModel(fixture_pos=(1500, 2000, 3000))
    for pan_n in (0.3, 0.45, 0.5, 0.55, 0.7):
        for tilt_n in (0.35, 0.5, 0.65):
            d = m.forward(pan_n, tilt_n)
            # Project 3m into stage to form a target
            tx = m.fixture_pos[0] + d[0] * 3000
            ty = m.fixture_pos[1] + d[1] * 3000
            tz = m.fixture_pos[2] + d[2] * 3000
            rp, rt = m.inverse(tx, ty, tz)
            _close(rp, pan_n, 1e-6, f"roundtrip pan pan={pan_n} tilt={tilt_n}")
            _close(rt, tilt_n, 1e-6, f"roundtrip tilt pan={pan_n} tilt={tilt_n}")


def test_inverse_roundtrip_with_mount_rotation():
    m = ParametricFixtureModel(
        fixture_pos=(0, 0, 3000),
        mount_yaw_deg=30.0,
        mount_pitch_deg=10.0,
        mount_roll_deg=5.0,
    )
    for pan_n in (0.4, 0.5, 0.6):
        for tilt_n in (0.45, 0.5, 0.55):
            d = m.forward(pan_n, tilt_n)
            tx = m.fixture_pos[0] + d[0] * 5000
            ty = m.fixture_pos[1] + d[1] * 5000
            tz = m.fixture_pos[2] + d[2] * 5000
            rp, rt = m.inverse(tx, ty, tz)
            _close(rp, pan_n, 1e-5, f"mount-rot pan pan={pan_n} tilt={tilt_n}")
            _close(rt, tilt_n, 1e-5, f"mount-rot tilt pan={pan_n} tilt={tilt_n}")


# ── Serialization ─────────────────────────────────────────────────────────

def test_roundtrip_dict():
    m = ParametricFixtureModel(
        fixture_pos=(1, 2, 3),
        pan_range_deg=420, tilt_range_deg=180,
        mount_yaw_deg=15, mount_pitch_deg=-5, mount_roll_deg=3,
        pan_offset=0.47, tilt_offset=0.52,
        pan_sign=-1, tilt_sign=1,
    )
    d = m.to_dict()
    m2 = ParametricFixtureModel.from_dict((1, 2, 3), d)
    _close(m.mount_yaw_deg, m2.mount_yaw_deg, 1e-12, "ser: yaw")
    _close(m.pan_offset,   m2.pan_offset,   1e-12, "ser: pan off")
    _assert(m.pan_sign == m2.pan_sign, "ser: pan sign")
    _assert(m.tilt_sign == m2.tilt_sign, "ser: tilt sign")


# ── LM fitting ────────────────────────────────────────────────────────────

def _make_ground_truth() -> ParametricFixtureModel:
    return ParametricFixtureModel(
        fixture_pos=(1500, 500, 3500),
        pan_range_deg=540, tilt_range_deg=270,
        mount_yaw_deg=-8.0,
        mount_pitch_deg=3.0,
        mount_roll_deg=1.5,
        pan_offset=0.48,
        tilt_offset=0.51,
        pan_sign=1,
        tilt_sign=-1,
    )


def _synthesize_samples(gt: ParametricFixtureModel, n: int, seed: int = 42):
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        # Random pan/tilt across the reasonable hemisphere.
        pan_n = 0.5 + rng.uniform(-0.18, 0.18)
        tilt_n = 0.5 + rng.uniform(-0.1, 0.1)
        d = gt.forward(pan_n, tilt_n)
        # Project to an intersection with a virtual floor/wall plane at a
        # realistic stage distance ~5-8m.
        dist = rng.uniform(4500, 7500)
        tx = gt.fixture_pos[0] + d[0] * dist
        ty = gt.fixture_pos[1] + d[1] * dist
        tz = gt.fixture_pos[2] + d[2] * dist
        samples.append({"pan": pan_n, "tilt": tilt_n,
                         "stageX": tx, "stageY": ty, "stageZ": tz})
    return samples


def test_fit_recovers_ground_truth():
    # Q3 / #652 — production path supplies force_signs from a verify_signs
    # probe. The five continuous params have gauge freedom between
    # mount_yaw and pan_offset, so raw-param recovery is fragile; assert
    # held-out angular error (the production metric) instead.
    gt = _make_ground_truth()
    samples = _synthesize_samples(gt, n=8)
    fit, q = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg,
                       samples, force_signs=(gt.pan_sign, gt.tilt_sign))
    _assert(q.rms_error_deg < 0.5,
            f"fit rms under 0.5deg: got {q.rms_error_deg:.3f}")
    _assert(fit.pan_sign == gt.pan_sign, "fit pan sign matches gt")
    _assert(fit.tilt_sign == gt.tilt_sign, "fit tilt sign matches gt")
    rng = random.Random(4242)
    for _ in range(20):
        pan_n = 0.5 + rng.uniform(-0.18, 0.18)
        tilt_n = 0.5 + rng.uniform(-0.1, 0.1)
        gt_dir = gt.forward(pan_n, tilt_n)
        fit_dir = fit.forward(pan_n, tilt_n)
        cos_ang = max(-1.0, min(1.0, sum(a * b for a, b in zip(gt_dir, fit_dir))))
        ang_deg = math.degrees(math.acos(cos_ang))
        _assert(ang_deg < 0.5,
                f"held-out angle at ({pan_n:.2f},{tilt_n:.2f}): {ang_deg:.3f}deg")


def test_fit_roundtrips_on_synthetic():
    """After fitting, forward→inverse on any sample yields the original DMX."""
    gt = _make_ground_truth()
    samples = _synthesize_samples(gt, n=6, seed=99)
    fit, _ = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg, samples)
    for s in samples:
        rp, rt = fit.inverse(s["stageX"], s["stageY"], s["stageZ"])
        _close(rp, s["pan"],  5e-3, f"fit rt pan  sample={s['pan']:.3f}")
        _close(rt, s["tilt"], 5e-3, f"fit rt tilt sample={s['tilt']:.3f}")


def test_fit_flipped_tilt_sign_still_roundtrips():
    """When ground truth uses a non-standard tilt sign, the model has a
    mirror ambiguity (flipping both signs and yaw by 180° is an identical
    transform). fit_model may pick either solution — what matters is that
    the fit round-trips samples back to their DMX values, not which side
    of the mirror it lands on.
    """
    gt = _make_ground_truth()
    gt.tilt_sign = 1
    samples = _synthesize_samples(gt, n=8, seed=7)
    fit, q = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg, samples)
    _assert(q.rms_error_deg < 0.5,
            f"fit rms under 0.5deg on flipped sign: got {q.rms_error_deg:.3f}")
    for s in samples:
        rp, rt = fit.inverse(s["stageX"], s["stageY"], s["stageZ"])
        _close(rp, s["pan"],  1e-2, f"flipped rt pan pan={s['pan']:.3f}")
        _close(rt, s["tilt"], 1e-2, f"flipped rt tilt tilt={s['tilt']:.3f}")


def test_fit_inverted_mount():
    """Inverted mount (hanging fixture) — initial guess should pick phi_m≈180."""
    gt = _make_ground_truth()
    gt.mount_pitch_deg = 178.5  # Inverted, slight off-axis
    samples = _synthesize_samples(gt, n=10, seed=3)
    fit, q = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg,
                        samples, mounted_inverted=True)
    _assert(q.rms_error_deg < 1.0,
            f"inverted-mount fit rms: got {q.rms_error_deg:.3f}")


# ── v1 → v2 migration ─────────────────────────────────────────────────────

def test_fit_from_v1_sample_tuples():
    """v1 stores samples as [pan, tilt, stageX, stageY, stageZ] tuples — fit
    should accept them without conversion."""
    gt = _make_ground_truth()
    dict_samples = _synthesize_samples(gt, n=6, seed=11)
    tuple_samples = [
        [s["pan"], s["tilt"], s["stageX"], s["stageY"], s["stageZ"]]
        for s in dict_samples
    ]
    fit, q = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg,
                        tuple_samples)
    _assert(q.rms_error_deg < 0.5,
            f"v1 tuple fit rms: got {q.rms_error_deg:.3f}")


def test_fit_too_few_samples_raises():
    try:
        fit_model((0, 0, 0), 540, 270, [{"pan": 0.5, "tilt": 0.5,
                                          "stageX": 0, "stageY": 3000, "stageZ": 0}])
    except ValueError:
        _passed_inc()
        return
    _failed_inc()


def _passed_inc():
    global _passed
    _passed += 1


def _failed_inc():
    global _failed
    _failed += 1
    print("FAIL test_fit_too_few_samples_raises: expected ValueError")


# ── Outlier detection ────────────────────────────────────────────────────

def test_outlier_inflates_max_error():
    """One bad sample should drive max_error_deg up above rms_error_deg."""
    gt = _make_ground_truth()
    samples = _synthesize_samples(gt, n=6, seed=21)
    # Inject an outlier: shift the stage target by 2m on X.
    samples.append({"pan": 0.5, "tilt": 0.5,
                     "stageX": gt.fixture_pos[0] + 2000,
                     "stageY": gt.fixture_pos[1] + 3000,
                     "stageZ": gt.fixture_pos[2]})
    _, q = fit_model(gt.fixture_pos, gt.pan_range_deg, gt.tilt_range_deg, samples)
    _assert(q.max_error_deg > q.rms_error_deg * 2,
            f"outlier max>>rms: rms={q.rms_error_deg:.2f} max={q.max_error_deg:.2f}")


# ── Singularity handling ─────────────────────────────────────────────────

def test_inverse_target_overhead_is_stable():
    """Target directly above the fixture — inverse must not NaN out, just
    return clamped/well-formed values."""
    m = ParametricFixtureModel(fixture_pos=(0, 0, 2000))
    p, t = m.inverse(0, 0, 5000)
    _assert(math.isfinite(p) and math.isfinite(t),
            f"overhead singularity finite: p={p} t={t}")
    _assert(0.0 <= p <= 1.0 and 0.0 <= t <= 1.0,
            f"overhead singularity clamped: p={p} t={t}")


# ── Runner ────────────────────────────────────────────────────────────────

ALL = [
    test_forward_identity,
    test_forward_tilt_down,
    test_forward_pan_right,
    test_inverse_roundtrip_identity,
    test_inverse_roundtrip_with_mount_rotation,
    test_roundtrip_dict,
    test_fit_recovers_ground_truth,
    test_fit_roundtrips_on_synthetic,
    test_fit_flipped_tilt_sign_still_roundtrips,
    test_fit_inverted_mount,
    test_fit_from_v1_sample_tuples,
    test_fit_too_few_samples_raises,
    test_outlier_inflates_max_error,
    test_inverse_target_overhead_is_stable,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
