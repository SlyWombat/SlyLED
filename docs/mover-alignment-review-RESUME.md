# Mover-Alignment Review — Resume Notes

**Branch:** `claude/review-mover-alignment-plan`
**PR:** [#643](https://github.com/SlyWombat/SlyLED/pull/643) (open against `main`)
**Review doc:** `docs/mover-alignment-review.md`
**Last session:** 2026-04-23

---

## Current state

PR #643 is open and ready for review. It bundles:
- The mover-alignment review plan (§1–§12)
- Static-reading findings for Q1–Q7, Q12, Q13 (§8.1)
- Architectural decisions for Q8–Q11 (§8.1b)
- **Capability-layer implementation shipped** (§8.1c) — one-PR refactor per
  the Q11 decision, +103 new test assertions, −336 LOC in `bake_engine.py`
- Cross-question synthesis (§8.2)
- `gyro_engine.py` deletion (Q5 cleanup)
- §12.1 future-feature note for velocity-lead / velocity-lag aim

## Issues filed from the review

| Issue | Source | Status |
|-------|--------|--------|
| [#474](https://github.com/SlyWombat/SlyLED/issues/474) | review §10 | **closed** as duplicate of #484 (Q5 runtime verified shipped) |
| [#633](https://github.com/SlyWombat/SlyLED/issues/633) | §8.1 Q7 | open — 3D viewport remote gizmo + live aim ray |
| [#634](https://github.com/SlyWombat/SlyLED/issues/634) | §8.1 Q3+Q4 | open — Track actions EMA smoothing + unified loss UX |
| [#635](https://github.com/SlyWombat/SlyLED/issues/635) | §8.1 Q5 | open — shared three-tier IK fallback helper (blocked on Fn 3 decisions) |
| [#640](https://github.com/SlyWombat/SlyLED/issues/640) | §8.1 Q1 | open — Track actions per-fixture `aimTarget` |
| [#641](https://github.com/SlyWombat/SlyLED/issues/641) | §8.1 Q2 | open — Hungarian head-to-target assignment (scipy.linear_sum_assignment) |
| [#642](https://github.com/SlyWombat/SlyLED/issues/642) | §8.1 Q6 | open — multi-fixture mover-control claim (1 phone → N movers) |

## Still open in the review itself

- **§8.3 live-test** — basement-rig walk / gyro sweep / 10-mover wash prototype. Hardware-dependent. Per §7:
  - Fn 1: walk a defined path, capture per-mover aim error vs ArUco-marked walking pose
  - Fn 2: gyro + phone per-axis sweep, look for residual delta asymmetry
  - Fn 3 live demo: run the wash prototype on the 3-mover basement rig
- Hardware needed: 3 DMX movers, 2 cameras, ESP32 gyro puck, Android phone. All already on the basement rig per `project_basement_rig.md`.

### Fn 1 attempt — 2026-04-23 — blocked on calibration regression

Current basement rig per issue #533 (fresh layout, 4000×3620×2060, 3 cameras). Loaded `tests/user/new basement/new basement.slyshow` (app v1.5.64, 6 fixtures, 6 surveyed markers). Orchestrator runs on port **5600** (8080 blocked on this machine; UDP 4210 WinError 10013 is non-fatal).

MH1 (fid=17) and MH2 (fid=18) are **uncalibrated** — the 3 mover calibrations in the slyshow are from the previous rig: fids 2 and 7 are orphans, only fid 14 (350W) has a matching current calibration (6 samples, no `model`, no `method`).

Started markers-mode calibration on MH1 with green beam. **Consistent false-positive pattern**: every battleship probe fired "Beam found ... — confirming with nudge" without the real beam ever entering the camera view. After 5 probes, one confirmation passed on noise and cal escalated to `sampling` phase on a fake discovery. Cancelled.

Root cause (user-confirmed): `battleship_discover._confirm` in `desktop/shared/mover_calibrator.py` used to re-run the flash blink at the candidate pose; now it runs a pan/tilt nudge + plain `_beam_detect` (color filter). On a scene with greenish carpet, reflective plastic bins, and a white pillar, color-filter noise easily shifts >8 px between two nearby poses → false confirm. Patched candidate detection (`_beam_detect_flash`) is still fine; it's only the secondary confirmation that regressed.

**Blocker:** Fn 1 can't produce meaningful per-mover aim-error numbers without calibrated movers. Operator will file fresh calibration issues; do not attempt the patch inside PR #643 — calibration accuracy is explicitly out-of-scope per review §1 + §9.

Evidence snapshot saved at `/mnt/d/temp/live-test-session/fn1-cal-debug/stage-right-empty.jpg` (empty-frame beam-detect returned `found: false`, confirming the false-confirms are transient noise, not persistent ambient).

Fn 2 (gyro/phone per-axis sweep) is unblocked — it doesn't need the Track action pipeline. Could run before Fn 1 on the next session.

## To pick up the session, useful commands

```bash
# Branch + state
git checkout claude/review-mover-alignment-plan
git log --oneline origin/main..HEAD     # 14+ commits — the whole PR

# Run the capability-layer test suite (no hardware)
python -X utf8 tests/test_capability_layer.py         # 39 assertions
python -X utf8 tests/test_colour_wash_sweep.py        # 56 assertions
python -X utf8 tests/test_capability_bake_e2e.py      # 8 assertions (Playwright + API fallback)

# Existing regressions (weekly suite)
python -X utf8 tests/regression/run_all.py

# Review doc (canonical)
open docs/mover-alignment-review.md
```

## Files to know

- `desktop/shared/spatial_engine.py` — home of the capability layer (`evaluate_primitive`, `derive_caps`, `shape_coverage_time`, `PrimitiveOutputs`, `CAP_*` constants)
- `desktop/shared/bake_engine.py` — `_compile_capability_for_string` + `_compile_capability_for_dmx` are the only compilers now; old `_compile_*_sweep` / `_compile_dmx_fixture` deleted
- `desktop/shared/mover_control.py` — stage-space primitive consumer; placeholder note in the docstring for future gyro-as-pointer work
- `tests/test_capability_layer.py` — unit coverage (39)
- `tests/test_colour_wash_sweep.py` — Q14 synthetic (56)
- `tests/test_capability_bake_e2e.py` — Playwright E2E (8)

## What I'd pick up next, in priority order

1. **Run the regression suite locally** before merging PR #643. The sandbox this PR was authored in lacked some dependencies (Chromium headless-shell, cv2); a full local run-through closes the last verification gap.
2. **Live basement-rig test** for §8.3 — the new bake slice interval (0.05 s vs 1 s) is the main thing to observe. Confirm that movers feel smoother during a colour wash, not worse, and that baked `.lsq` file sizes are still reasonable.
3. **After merge**, start on **#633 (remote gizmo)** — it's the biggest operator UX unlock and unblocks #427 (Android pointer mode).
4. **#634 (Track-action smoothing)** is a small, independent PR that noticeably improves ensemble-follow feel.
5. **#641 (Hungarian assignment)** pairs naturally with #634 — "Track actions feel right" PR that can bundle both if ambition allows.
6. **Q8–Q11 are DONE** — do not re-open the architecture discussion; the capability layer is shipped. Future effects (new shapes, new primitives like gobo/focus) extend `evaluate_primitive` by dispatch, not by adding new compilers.

## Do not

- Do not reintroduce a per-shape compiler path. §2 rules it out and the unified evaluator is the long-term home.
- Do not restore `gyro_engine.py` — if a future gyro-as-pointer feature needs work, it goes through `mover_control.py::_aim_to_pan_tilt` (there's a docstring note there).
- Do not revive `panScale` / `tiltScale` as runtime multipliers per #484 phase 4.

## PR #643 merge checklist

- [x] No new regression failures introduced vs `main` (verified 2026-04-23) — API suites (Stage Setup, Timeline Bake) pass clean; Playwright 3D-viewport failures are pre-existing and tracked in #645
- [ ] Playwright full UI coverage in a provisioned environment (blocked on #645)
- [ ] Basement-rig smoke test post-merge (§8.3)
- [ ] No objections from code reviewers
