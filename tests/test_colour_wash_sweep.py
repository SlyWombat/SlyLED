"""test_colour_wash_sweep.py — Q14 synthetic acceptance test.

Per docs/mover-alignment-review.md §6.4 Q14 and §8.1b Q10/Q14:
"10 movers placed at random stage XY, all participate in a colour-wash
sweep; assert each fires at the expected wall-clock time within ±50 ms
and at the expected RGB."

This is the prototype test that proves the capability layer end-to-end
without hardware. It validates:
- evaluate_primitive returns colour + aim for every fixture regardless
  of where they're placed
- Movers participating in a wash each get an aim point that tracks the
  wash's current centre
- Timing matches physics: wash at 1 m/s travels 3 m in 3 s; a mover at
  x=1500 should fire at t ≈ 1.5 s

Run:
    python -X utf8 tests/test_colour_wash_sweep.py
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from spatial_engine import evaluate_primitive  # noqa: E402


# ── Test infrastructure ─────────────────────────────────────────────────────

passed = 0
failed = 0
messages = []


def ok(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        messages.append(msg)


# ── Synthetic test: 10 movers, colour wash ───────────────────────────────────

def synthetic_wash_test():
    """10 movers placed at random stage XY receive a plane-sweep wash
    travelling stage-left → stage-right at 1 m/s; each should fire in
    the expected colour at the time the wash's X-coordinate crosses the
    mover's X position."""

    random.seed(42)  # deterministic layout

    # Random stage XY within a 6m × 4m stage at z = 1500
    movers = []
    for _ in range(10):
        x = random.uniform(-3000, 3000)
        y = random.uniform(0, 4000)
        movers.append([x, y, 1500])

    # Plane sweep: stage-left to stage-right over 6 s (1 m/s), full stage.
    # Normal along +X → plane is a vertical sheet parallel to YZ.
    wash = {
        "shape": "plane",
        "r": 0, "g": 0, "b": 255,
        "size": {"normal": [1, 0, 0], "thickness": 400},
        "motion": {
            "startPos": [-3000, 2000, 1500],
            "endPos":   [ 3000, 2000, 1500],
            "durationS": 6.0, "easing": "linear",
        },
    }

    tolerance_s = 0.050  # ±50 ms per Q14

    for i, mover_pos in enumerate(movers):
        # The wash centre passes mover's X when
        # start_x + (end_x - start_x) * (t/dur) == mover_x
        # → t = dur * (mover_x - start_x) / (end_x - start_x)
        expected_t = 6.0 * (mover_pos[0] - (-3000)) / (3000 - (-3000))

        # Evaluate at expected_t — mover should be lit blue
        out_at_peak = evaluate_primitive(mover_pos, wash, expected_t)
        ok(out_at_peak.color[2] > 0,
           f"mover {i} at x={mover_pos[0]:.0f}: lit blue at t={expected_t:.3f}s "
           f"(got rgb={out_at_peak.color})")
        ok(out_at_peak.intensity > 0,
           f"mover {i}: intensity > 0 at peak")

        # Aim should point at the wash centre at that time — plane sweep
        # centre advances linearly so aim_x ≈ mover_x
        assert out_at_peak.aim is not None, f"mover {i}: aim is not None at peak"
        aim_x = out_at_peak.aim[0]
        ok(abs(aim_x - mover_pos[0]) < 400,
           f"mover {i}: aim tracks wash centre "
           f"(aim_x={aim_x:.0f}, mover_x={mover_pos[0]:.0f})")

        # Also check that ±tolerance around the peak is still within the
        # lit window (plane thickness 400 @ 1 m/s → ~200 ms centre-of-mass)
        out_before = evaluate_primitive(mover_pos, wash, max(0, expected_t - tolerance_s))
        out_after  = evaluate_primitive(mover_pos, wash, min(6.0, expected_t + tolerance_s))
        # Tolerance 50 ms × 1000 mm/s = 50 mm; thickness is 400 mm, so we
        # should be within the lit window regardless of tolerance direction
        ok(out_before.intensity > 0,
           f"mover {i}: still lit at peak - 50ms")
        ok(out_after.intensity > 0,
           f"mover {i}: still lit at peak + 50ms")


def synthetic_wash_dark_outside_wash():
    """Movers placed outside the wash's travel range should never fire."""
    wash = {
        "shape": "plane",
        "r": 255, "g": 255, "b": 0,
        "size": {"normal": [1, 0, 0], "thickness": 300},
        "motion": {
            "startPos": [-1000, 0, 0], "endPos": [1000, 0, 0],
            "durationS": 4.0, "easing": "linear",
        },
    }

    # Mover way off stage at x=9000 — never hit
    out = evaluate_primitive([9000, 0, 0], wash, 2.0)
    ok(out.intensity == 0.0, f"off-stage mover dark at t=2.0 (got {out.intensity})")

    # Multiple time samples — always dark
    all_dark = all(
        evaluate_primitive([9000, 0, 0], wash, t).intensity == 0.0
        for t in (0.0, 1.0, 2.0, 3.0, 4.0)
    )
    ok(all_dark, "off-stage mover dark at all sample times")


def synthetic_wash_aim_direction_sweep():
    """Across the wash's duration, the aim X coordinate should sweep
    monotonically from start to end — this is what makes the movers
    physically sweep in sync with the wash."""
    wash = {
        "shape": "plane",
        "r": 255, "g": 0, "b": 0,
        "size": {"normal": [1, 0, 0], "thickness": 400},
        "motion": {
            "startPos": [-3000, 2000, 1500],
            "endPos":   [ 3000, 2000, 1500],
            "durationS": 6.0, "easing": "linear",
        },
    }

    mover_pos = [0, 1000, 1500]  # centre-of-stage mover
    aim_xs = []
    for t in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        out = evaluate_primitive(mover_pos, wash, t)
        aim_xs.append(out.aim[0] if out.aim else None)

    # All aim X values present
    ok(all(ax is not None for ax in aim_xs), "aim present at every sample")

    # Monotonically increasing
    sorted_check = all(aim_xs[i] <= aim_xs[i + 1] for i in range(len(aim_xs) - 1))
    ok(sorted_check, f"aim X sweeps monotonically (values={[round(x) for x in aim_xs]})")

    # Endpoints match motion start/end
    ok(abs(aim_xs[0] - (-3000)) < 10, f"aim at t=0 is at startPos X (got {aim_xs[0]})")
    ok(abs(aim_xs[-1] - 3000) < 10, f"aim at t=6 is at endPos X (got {aim_xs[-1]})")


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    synthetic_wash_test()
    synthetic_wash_dark_outside_wash()
    synthetic_wash_aim_direction_sweep()
    print(f"{passed} passed, {failed} failed (out of {passed + failed})")
    if failed:
        print("\nFailures:")
        for m in messages:
            print(f"  - {m}")
    sys.exit(0 if failed == 0 else 1)
