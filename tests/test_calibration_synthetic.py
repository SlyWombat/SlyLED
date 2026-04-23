#!/usr/bin/env python3
"""
test_calibration_synthetic.py — Regression tests for the v2 parametric-fit
pipeline under simulated noise.

Synthetic only (no hardware, no OpenCV, no network). The v2 `fit_model`
pipeline consumes stage-mm samples; the camera node's depth lookup and
ray/floor intersection run upstream. These tests generate samples
against a known-truth `ParametricFixtureModel`, intersect each beam ray
with the floor (z=0), add Gaussian noise, and assert recovery.

Non-overlapping delta from tests/test_parametric_mover.py:
  - noise sweep (σ 0 / 10 / 50 / 200 mm on stage coords)
  - sample-count sweep (3 / 10 / 50)
  - colinear-geometry degeneracy (fixed tilt sweep) flagged by
    FitQuality.condition_number
  - four-sign RMS gap (correct signs << wrong signs)
  - verify_signs() under pixel-space noise (§7.2 intent preserved)

Source: docs/mover-calibration-reliability-review.md §7.2 + §8.1.

Stage coordinate system: X=width, Y=depth, Z=height.

Usage:
    /usr/bin/python3 -X utf8 tests/test_calibration_synthetic.py
    # or on Windows:
    python -X utf8 tests/test_calibration_synthetic.py
"""

import math
import os
import random
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'desktop', 'shared'),
)

from parametric_mover import ParametricFixtureModel, fit_model, verify_signs

passed = 0
failed = 0


def check(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── Helpers ────────────────────────────────────────────────────────────────

def make_truth(yaw=5.0, pitch=3.0, roll=2.0,
               pan_off=0.52, tilt_off=0.50,
               pan_sign=1, tilt_sign=-1,
               fixture_pos=(0.0, 0.0, 3000.0)):
    # Small mount deviations (yaw/pitch/roll) match a correctly-hung fixture.
    # fit_model's LM solver is locally convergent — large deviations (yaw≥15°)
    # expose additional local minima and are handled in production by the
    # verify_signs probe + tier 2/3 operator-in-loop fallbacks (§8.1 Q3).
    return ParametricFixtureModel(
        fixture_pos=fixture_pos,
        pan_range_deg=540.0,
        tilt_range_deg=270.0,
        mount_yaw_deg=yaw,
        mount_pitch_deg=pitch,
        mount_roll_deg=roll,
        pan_offset=pan_off,
        tilt_offset=tilt_off,
        pan_sign=pan_sign,
        tilt_sign=tilt_sign,
    )


def floor_hit(truth, pan_norm, tilt_norm):
    """Intersect the commanded beam with the floor (z=0). Returns (x,y,z) or None."""
    d = truth.forward(pan_norm, tilt_norm)
    fx, fy, fz = truth.fixture_pos
    # Beam must point downward to hit floor
    if d[2] >= -1e-6:
        return None
    s = -fz / d[2]
    return (fx + s * d[0], fy + s * d[1], 0.0)


def held_out_max_deg(fit, truth, n_points=20, seed=99,
                     pan_range=(0.32, 0.72), tilt_range=(0.24, 0.43)):
    """Max angular error between fit.forward(p,t) and truth.forward(p,t) on
    n_points random held-out (pan, tilt) probes.

    This is the production-relevant accuracy metric: downstream code calls
    forward()/inverse() to point the beam at arbitrary targets, so we care
    whether the fit reproduces truth's beam directions — not whether the
    fit recovered the specific (yaw, pitch, roll, pan_off, tilt_off) tuple
    that generated the samples. The 5-parameter decomposition has
    near-equivalent sets that produce the same rays.
    """
    rng = random.Random(seed)
    max_err = 0.0
    for _ in range(n_points):
        p = rng.uniform(pan_range[0], pan_range[1])
        t = rng.uniform(tilt_range[0], tilt_range[1])
        td = truth.forward(p, t)
        fd = fit.forward(p, t)
        dot = max(-1.0, min(1.0, sum(td[i] * fd[i] for i in range(3))))
        err = math.degrees(math.acos(dot))
        if err > max_err:
            max_err = err
    return max_err


def gen_samples(truth, n, noise_mm=0.0, rng=None,
                pan_range=(0.30, 0.75), tilt_range=(0.22, 0.45)):
    """Generate up to n BFS-like samples on a uniform grid that hit the floor.

    Default tilt_range is below truth.tilt_offset=0.50 with tilt_sign=-1 so the
    beam points downward (see forward() convention). Samples that miss the
    floor are skipped — caller should use a tilt range that mostly hits.
    """
    if rng is None:
        rng = random.Random(42)
    samples = []
    side = max(2, int(math.ceil(math.sqrt(n))))
    for i in range(side):
        for j in range(side):
            if len(samples) >= n:
                break
            p = pan_range[0] + (pan_range[1] - pan_range[0]) * (i / (side - 1))
            t = tilt_range[0] + (tilt_range[1] - tilt_range[0]) * (j / (side - 1))
            hit = floor_hit(truth, p, t)
            if hit is None:
                continue
            x, y, z = hit
            samples.append({
                "pan": p,
                "tilt": t,
                "stageX": x + rng.gauss(0, noise_mm),
                "stageY": y + rng.gauss(0, noise_mm),
                "stageZ": z + rng.gauss(0, noise_mm),
            })
    return samples


# ── Simple pinhole camera for verify_signs tests ───────────────────────────

class Pinhole:
    """Camera at (0, -5000, 1500), looking +Y, 90° HFOV, frame 800×600.

    Camera convention: +X_cam = +X_stage (right), +Y_cam = +Z_stage (up),
    +Z_cam = +Y_stage (forward). Matches verify_signs defaults
    (pan_axis_sign_in_frame=+1: pan+ → u+; tilt_axis_sign_in_frame=-1:
    tilt+ → v-, i.e. beam moves UP in frame when tilt increases).
    """

    def __init__(self, pos=(0.0, -5000.0, 1500.0), focal=400.0, w=800, h=600):
        self.pos = pos
        self.focal = focal
        self.w = w
        self.h = h

    def project(self, stage_pt):
        sx, sy, sz = stage_pt
        cx, cy, cz = self.pos
        dx = sx - cx           # +right in frame
        dy = sz - cz           # +up (stage Z) in frame
        dz = sy - cy           # +forward (stage Y)
        if dz <= 1e-6:
            return None
        u = self.w / 2 + (dx / dz) * self.focal
        v = self.h / 2 - (dy / dz) * self.focal
        return (u, v)


# ── Tests ──────────────────────────────────────────────────────────────────

def test_noise_sweep():
    """Fit held-out prediction accuracy degrades smoothly with stage-space noise.

    Assumes verify_signs has supplied force_signs (§8.1 Q3). Metric: max
    angular error between fit.forward() and truth.forward() across 20
    held-out (pan, tilt) points — this is what drives production aim
    accuracy, not raw mount-param recovery (see held_out_max_deg docstring).
    """
    print("\ntest_noise_sweep:")
    truth = make_truth()
    truth_signs = (truth.pan_sign, truth.tilt_sign)
    results = []
    for sigma in (0.0, 10.0, 50.0, 200.0):
        samples = gen_samples(truth, n=25, noise_mm=sigma,
                              rng=random.Random(int(sigma) + 1))
        fit, q = fit_model(truth.fixture_pos, 540.0, 270.0, samples,
                           force_signs=truth_signs)
        ho_max = held_out_max_deg(fit, truth)
        results.append((sigma, q.rms_error_deg, ho_max, fit))

    clean = results[0]
    check("noise=0mm: held-out max < 0.1°",
          clean[2] < 0.1, f"got {clean[2]:.4f}")
    check("noise=0mm: rms < 0.1°",
          clean[1] < 0.1, f"got {clean[1]:.4f}")

    mid = [r for r in results if r[0] == 50.0][0]
    # At 3m throw, 50mm stage noise ≈ 1° angular; fit averages it down.
    check("noise=50mm: held-out max < 2°",
          mid[2] < 2.0, f"got {mid[2]:.3f}")

    high = results[-1]  # σ=200mm
    check("noise=200mm: held-out max < 5°",
          high[2] < 5.0, f"got {high[2]:.3f}")
    check("noise=200mm: rms is finite",
          math.isfinite(high[1]), f"got {high[1]}")

    # Held-out error grows (mostly) monotonically with noise
    ho_vals = [r[2] for r in results]
    violations = sum(
        1 for i in range(len(ho_vals) - 1)
        if ho_vals[i + 1] + 0.3 < ho_vals[i]
    )
    check("held-out error grows (mostly) with noise",
          violations == 0,
          f"violations={violations}, series={[f'{v:.2f}' for v in ho_vals]}")


def test_sample_count_sweep():
    """Fit quality at 3 / 10 / 50 samples; held-out accuracy improves with data."""
    print("\ntest_sample_count_sweep:")
    truth = make_truth()
    truth_signs = (truth.pan_sign, truth.tilt_sign)

    # n=3 — tier-3 manual minimum. Underdetermined for 5 continuous params
    # (3 residuals × 3 samples = 9 equations for 5 unknowns, but geometry
    # is tight with 3 points). Expect it to fit but with limited generality.
    s3 = gen_samples(truth, n=3, noise_mm=0.0, rng=random.Random(3),
                     pan_range=(0.30, 0.70), tilt_range=(0.25, 0.45))
    check("n=3: gen_samples produced ≥3 points", len(s3) >= 3,
          f"got {len(s3)}")
    if len(s3) >= 3:
        fit3, q3 = fit_model(truth.fixture_pos, 540, 270, s3,
                             force_signs=truth_signs)
        check("n=3: fit completes without raise", fit3 is not None)
        check("n=3: sample_count reported correctly", q3.sample_count == len(s3))

    s10 = gen_samples(truth, n=10, noise_mm=5.0, rng=random.Random(10))
    fit10, q10 = fit_model(truth.fixture_pos, 540, 270, s10,
                           force_signs=truth_signs)
    ho10 = held_out_max_deg(fit10, truth)
    check("n=10 σ=5mm: held-out max < 1°",
          ho10 < 1.0, f"got {ho10:.3f}")
    check("n=10: sample_count reported correctly", q10.sample_count == len(s10))

    s50 = gen_samples(truth, n=50, noise_mm=5.0, rng=random.Random(50))
    fit50, q50 = fit_model(truth.fixture_pos, 540, 270, s50,
                           force_signs=truth_signs)
    ho50 = held_out_max_deg(fit50, truth)
    check("n=50 σ=5mm: held-out max < 1°",
          ho50 < 1.0, f"got {ho50:.3f}")

    # With more data at same noise, held-out accuracy should not be worse —
    # and condition_number should not explode.
    check("n=50: condition_number ≤ n=10 × 2 + 1",
          q50.condition_number <= q10.condition_number * 2.0 + 1.0,
          f"n10={q10.condition_number:.2e} n50={q50.condition_number:.2e}")


def test_colinear_geometry():
    """Fixed-tilt pan sweep — degenerate for roll. FitQuality flags it."""
    print("\ntest_colinear_geometry:")
    truth = make_truth()
    fixed_tilt = 0.35
    samples = []
    for p in (0.25, 0.35, 0.45, 0.55, 0.65, 0.75):
        hit = floor_hit(truth, p, fixed_tilt)
        if hit is None:
            continue
        x, y, z = hit
        samples.append({"pan": p, "tilt": fixed_tilt,
                        "stageX": x, "stageY": y, "stageZ": z})
    check("colinear: gen'd ≥3 samples", len(samples) >= 3,
          f"got {len(samples)}")

    fit, q = fit_model(truth.fixture_pos, 540, 270, samples)
    # Condition number on a fixed-tilt line is very high or infinite —
    # samples carry no information about how mount rotations decompose
    # into pan vs roll. Assert it's flagged as >> the well-conditioned case.
    check("colinear: condition_number ≥ 100 (degeneracy flagged)",
          (q.condition_number >= 100.0) or math.isinf(q.condition_number),
          f"got {q.condition_number:.3e}")

    # Baseline: a 2D pan×tilt grid with same sample count should be much better
    well = gen_samples(truth, n=len(samples), noise_mm=0.0,
                       rng=random.Random(111))
    _, q_well = fit_model(truth.fixture_pos, 540, 270, well)
    check("colinear: condition_number >> well-conditioned case's",
          q.condition_number > q_well.condition_number * 10,
          f"colinear={q.condition_number:.2e} well={q_well.condition_number:.2e}")


def test_four_sign_rms_gap():
    """force_signs=(correct) RMS is far below force_signs=(wrong) RMS."""
    print("\ntest_four_sign_rms_gap:")
    truth = make_truth(pan_sign=1, tilt_sign=-1)
    samples = gen_samples(truth, n=20, noise_mm=5.0, rng=random.Random(7))

    _, q_correct = fit_model(truth.fixture_pos, 540, 270, samples,
                             force_signs=(1, -1))
    _, q_flip_pan = fit_model(truth.fixture_pos, 540, 270, samples,
                              force_signs=(-1, -1))
    _, q_flip_tilt = fit_model(truth.fixture_pos, 540, 270, samples,
                               force_signs=(1, 1))

    check("correct signs: rms < 1°",
          q_correct.rms_error_deg < 1.0, f"got {q_correct.rms_error_deg:.3f}")
    check("flip pan: rms >> correct (gap ≥ 5°)",
          q_flip_pan.rms_error_deg > q_correct.rms_error_deg + 5.0,
          f"correct={q_correct.rms_error_deg:.2f} "
          f"flip_pan={q_flip_pan.rms_error_deg:.2f}")
    check("flip tilt: rms >> correct (gap ≥ 5°)",
          q_flip_tilt.rms_error_deg > q_correct.rms_error_deg + 5.0,
          f"correct={q_correct.rms_error_deg:.2f} "
          f"flip_tilt={q_flip_tilt.rms_error_deg:.2f}")

    # Default (no force_signs) four-sign loop should also land on the correct pair
    fit_default, _ = fit_model(truth.fixture_pos, 540, 270, samples)
    check("default four-sign loop picks correct pan_sign",
          fit_default.pan_sign == 1, f"got {fit_default.pan_sign}")
    check("default four-sign loop picks correct tilt_sign",
          fit_default.tilt_sign == -1, f"got {fit_default.tilt_sign}")


def test_verify_signs_clean():
    """verify_signs recovers ground-truth signs from noise-free pixel deltas.

    Preserves the §7.2 pinhole-simulation intent — this is the only path
    through the v2 stack where pixel deltas flow end-to-end.
    """
    print("\ntest_verify_signs_clean:")
    cam = Pinhole()

    for ps_truth in (1, -1):
        for ts_truth in (1, -1):
            truth = make_truth(pan_sign=ps_truth, tilt_sign=ts_truth)

            def pixel_at(p, t):
                hit = floor_hit(truth, p, t)
                if hit is None:
                    return None
                return cam.project(hit)

            p0, t0 = 0.5, 0.35
            px_before = pixel_at(p0, t0)
            px_pan = pixel_at(p0 + 0.02, t0)
            px_tilt = pixel_at(p0, t0 + 0.02)
            if px_before is None or px_pan is None or px_tilt is None:
                # Skip combos where the simulated rig geometry misses the floor
                continue
            ps, ts = verify_signs(px_before, px_pan, px_tilt)
            check(f"verify_signs(ps={ps_truth}, ts={ts_truth}) pan recovered",
                  ps == ps_truth, f"got {ps}")
            check(f"verify_signs(ps={ps_truth}, ts={ts_truth}) tilt recovered",
                  ts == ts_truth, f"got {ts}")


def test_verify_signs_with_pixel_noise():
    """verify_signs stays correct under small Gaussian pixel noise (σ=3 px)."""
    print("\ntest_verify_signs_with_pixel_noise:")
    truth = make_truth(pan_sign=1, tilt_sign=-1)
    cam = Pinhole()
    rng = random.Random(77)
    sigma = 3.0
    N = 80

    def pixel_at(p, t):
        hit = floor_hit(truth, p, t)
        if hit is None:
            return None
        return cam.project(hit)

    def noisy(px):
        if px is None:
            return None
        return (px[0] + rng.gauss(0, sigma), px[1] + rng.gauss(0, sigma))

    p0, t0 = 0.5, 0.35
    correct_pan = 0
    correct_tilt = 0
    usable = 0
    for _ in range(N):
        pxb = noisy(pixel_at(p0, t0))
        pxp = noisy(pixel_at(p0 + 0.02, t0))
        pxt = noisy(pixel_at(p0, t0 + 0.02))
        if pxb is None or pxp is None or pxt is None:
            continue
        usable += 1
        ps, ts = verify_signs(pxb, pxp, pxt)
        if ps == 1:
            correct_pan += 1
        if ts == -1:
            correct_tilt += 1

    check("verify_signs pixel-noise σ=3: ≥90% pan correct",
          correct_pan >= 0.9 * usable,
          f"got {correct_pan}/{usable}")
    check("verify_signs pixel-noise σ=3: ≥90% tilt correct",
          correct_tilt >= 0.9 * usable,
          f"got {correct_tilt}/{usable}")


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 64)
    print("test_calibration_synthetic.py")
    print("=" * 64)

    tests = [
        test_noise_sweep,
        test_sample_count_sweep,
        test_colinear_geometry,
        test_four_sign_rms_gap,
        test_verify_signs_clean,
        test_verify_signs_with_pixel_noise,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__} raised "
                  f"{type(e).__name__}: {e}")

    print()
    print("=" * 64)
    total = passed + failed
    print(f"PASSED: {passed}/{total}   FAILED: {failed}")
    print("=" * 64)
    sys.exit(0 if failed == 0 else 1)
