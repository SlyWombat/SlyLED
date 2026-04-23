# Mover-Calibration Reliability Review — Resume Notes

**Status:** All review work lives on `main`. This machine is a
single-user dev env — no feature branches; continuations land directly
on main (see memory `feedback_no_branching.md`).
**Review doc:** `docs/mover-calibration-reliability-review.md` —
§0–§8.1 complete, §8.2 / §8.3 / §12 placeholders pending.
**Last session:** 2026-04-23

---

## Current state

**PR #646 merged** 2026-04-23 via rebase-merge. Six commits landed on
`main` (search `git log --oneline --grep='calibration-reliability'`).
What's in the doc now:

- §0–§7 draft + §8–§12 scaffolding
- §8.1 static-reading round (Q1–Q6 tier-1 hardening findings) with
  code citations (`file:line`) for every recommendation
- `tests/test_calibration_synthetic.py` (36 assertions, green) —
  no-hardware regression gate for tier-1 fixes

The review was born from the realisation that the mover-alignment
review (PR #643, merged 2026-04-23) shipped a clean capability-layer
architecture but **never touched calibration reliability**, which is
the actual user-facing breakage. Alignment review §9 put calibration
out of scope "because the camera review landed ParametricFixtureModel".
That's true but irrelevant — the IK primitive is fine; the *data* it
gets trained on is junk, because the capture pipeline never completes.

The alignment review's post-merge resume notes (`docs/mover-alignment-
review.md` §8.3 entry, commit subject line *"§8.3 Fn 1 attempt
2026-04-23 blocked on cal regression"*) are live validation of this
review's thesis.

The review was born from the realisation that the mover-alignment
review (PR #643 on branch `claude/review-mover-alignment-plan`) shipped
a clean capability-layer architecture but **never touched calibration
reliability**, which is the actual user-facing breakage. Alignment
review §9 put calibration out of scope "because the camera review
landed ParametricFixtureModel". That's true but irrelevant — the IK
primitive is fine; the *data* it gets trained on is junk, because the
capture pipeline never completes.

## The architectural bet (§0)

Four-tier fallback ladder. Operator is never stuck.

1. **Camera-assisted auto** (hardened current path)
2. **Camera-assisted operator-in-loop** (click-the-beam on live frame)
3. **3-point manual aim** (grandMA3 / Follow-Me / Zactrack pattern)
4. **GDTF / geometric-only trust** (Disguise pattern)

## 16 review questions (§6)

### Tier 1 robustness
- Q1 flash-detection as default (`battleship_discover` exists)
- Q2 mandatory dark-reference + re-capture
- Q3 sign-verification probe (`verify_signs` exists)
- Q4 per-phase timeouts / circuit breakers
- Q5 post-fit held-out verification
- Q6 backlash oversampling

### Tier 2 operator-in-loop
- Q7 activation trigger
- Q8 click UX

### Tier 3 manual 3-point
- Q9 reference point source (ArUco markers preferred)
- Q10 aim drive (phone gyro / slider / trackball)
- Q11 point-count + geometry

### Tier 4 GDTF
- Q12 when acceptable
- Q13 MVR import as seed

### Cross-cutting
- Q14 operator visibility during calibration
- Q15 multi-fixture isolation (blackout others)
- Q16 acceptance test as the "calibrated" gate

## Pre-investigation already done

- **Pipeline audit** (2026-04-23 Explore agent) — 15 code-reading
  questions across `mover_calibrator.py` / `beam_detector.py` /
  `parent_server.py`. Output reflected in §3 + §5.1. Top-5 failures:
  1. Discovery from bad geometric estimate
  2. BFS chases reflections (no dark-ref, colour thresholds spoofed)
  3. HTTP hangs 5–30 s per probe
  4. Mirror ambiguity silent in fit
  5. `_wait_settled()` too aggressive
- **Competitor scan** (2026-04-23 general-purpose agent, web) — 14
  tools across pro / tracking / consumer / schema / camera-auto.
  Output reflected in §4. 26 URLs captured.

## Next session — planned

**User intent for 2026-04-24:** live-test on the basement rig (§7.1
protocol). Everything else below is queued behind it.

### Primary — §7.1 live-test (basement rig, hardware)

Cold-start, all calibrations cleared:

1. **Tier-4 baseline** — aim each mover at 5 known stage points
   without any calibration. Record pixel-error. This is the
   never-worse-than-geometric floor.
2. **Tier-1 auto-cal** — hit Start Calibration, measure wall-clock
   to completion. If never completes, log the stuck phase + what
   the camera saw. Falls back to tier 2.
3. **Tier-2 operator-click** — live frame, operator clicks beam for
   each BFS position. Measure operator time + resulting fit.
4. **Tier-3 manual** — drive mover to 3 surveyed ArUco markers,
   record. Measure operator time + fit quality.
5. **Verification pass** — aim at 10 held-out points, measure
   pixel-error + stage-mm-error. This is the "calibration works"
   number.
6. **Drift retest** — bump the yoke, re-run verification without
   re-calibrating. Quantifies brittleness.

Rig assumed ready: 3 movers + 2 cameras + ArUco markers (the
expanded basement rig from camera-review §8.3 baseline).

Output populates **§8.3 (live-test resolution)** of
`docs/mover-calibration-reliability-review.md` and ratifies the
100 mm / 200 mm accuracy targets in §1.

### Queued (after live-test, or if hardware blocks)

- **Draft a live-test runner** — `tests/test_calibration_live_basement.py`
  automates the §7.1 protocol against a live orchestrator (QA
  test-script scope; no server code). Safe to start pre-rig; first
  real run reveals tweaks.
- **§8.2 tier 2–4 static reading (Q7–Q13)** — what code exists per
  tier. Worth double-checking `parent_server.py:5260`
  (`/api/calibration/mover/<fid>/manual`) — review §3.7 called it
  stubbed but recent reading suggests it's wired end-to-end.
- **Cross-reference issues** — update #488, #610, #486 status to
  link §8.1 findings. Light bookkeeping.

## Context to know

### Files this review is about
- `desktop/shared/mover_calibrator.py` — the thing that doesn't work
- `firmware/orangepi/beam_detector.py` — colour-filter + 3-beam
  detection; no dark-reference auto-capture
- `firmware/orangepi/camera_server.py` — `/beam-detect`,
  `/beam-detect/center`, `/dark-reference` endpoints
- `desktop/shared/parent_server.py:4446` — `_mover_cal_thread_body`,
  the orchestration; `:4811` entry point; `:5260` stubbed manual route
- `desktop/shared/parametric_mover.py:324` — `fit_model`, LM solver
  + mirror ambiguity (`verify_signs` at `:419` exists but unused)

### Files this review does NOT touch
- Camera intrinsic calibration (camera review, PR #632 merged)
- DMX profile editor / OFL importer (separate surface)
- `mover_control.py` / `remote_orientation.py` (alignment review
  PR #643 owns these)
- Capability layer / bake engine (alignment review PR #643)

### Do not
- Do **not** regress calibration to "out of scope" again. That was
  alignment review §9's mistake.
- Do **not** rewrite `ParametricFixtureModel.inverse`. It's correct
  (alignment review §8.1 Q5 verified). This review produces cleaner
  sample data for its `fit_model` counterpart; it does not replace
  the IK.
- Do **not** implement all four tiers in one PR. Tier 4 is smallest,
  ship it first; then tier 1 hardening; then tier 3 (needs new UI);
  tier 2 last.
- Do **not** skip the held-out verification pass. Pro consoles all
  have it for a reason (§5.3); silent-completion is the root of
  "calibration has never worked."

### Useful commands

```bash
# Branch + state
git checkout claude/review-mover-calibration-reliability
git log --oneline origin/main..HEAD

# Read the doc (canonical)
less docs/mover-calibration-reliability-review.md

# When answering §6 — static reading
grep -n "battleship_discover\|verify_signs\|_dark_reference" desktop/shared/mover_calibrator.py
grep -n "moverCalibrated" desktop/shared/parent_server.py

# Once synthetic prototype exists
python -X utf8 tests/test_calibration_synthetic.py
```

## Dependencies on PR #643 (alignment review)

Tier 1 hardening (Q1–Q6) is independent of PR #643 — safe to start
either before or after #643 merges.

Tier 3 UX (Q9–Q11) relies on the phone-gyro aim primitive that
already shipped in the alignment review's Fn 2 path. That's on main,
not gated by #643.

Tier 4 geometric fallback depends on the shared IK helper tracked in
#635 (filed from alignment review §8.2). Not a hard dep — tier 4 can
inline the geometric IK path and migrate when #635 lands.

## PR #643 follow-up reminder

The alignment review PR (`claude/review-mover-alignment-plan` → main)
is independent of this work. If merging that first: this branch will
need a rebase onto main afterward. Nothing in this review touches
files the alignment PR touches (bake_engine / spatial_engine /
mover_control / gyro_engine deletion), so the rebase is trivial —
just a branch-pointer update.
