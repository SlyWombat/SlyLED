# Moving-Head Calibration Reliability Review

**Status:** Draft (§0–§7). §8 onward populated as questions are answered.
**No code changes during the review phase.**
**Date:** 2026-04-23
**Scope owner:** Dave (operator) + Claude (implementation)
**Mirrors the structure of:** `docs/mover-alignment-review.md`, `docs/camera-calibration-review.md`
**Related docs:** `docs/mover-calibration-v2.md` (#488), `docs/mover-alignment-review.md` (§9 put this scope out of bounds — that was wrong)

The alignment review assumed calibration worked. It doesn't. The
beam-spot auto-calibration has never completed reliably on the
basement rig — discovery misses, BFS chases reflections, the fit
is mirror-ambiguous, the fitted model is never verified, and no
manual fallback is wired. This review's job is to make calibration
robust enough that Fn 1–3 from the alignment review actually behave
on real hardware.

---

## 0. The architectural bet (read first)

> **Layered calibration with graceful degradation. The operator is
> never stuck.** Four tiers, each one a complete calibration path on
> its own; the operator can start at any tier and always has a path
> forward when a tier fails.
>
> 1. **Camera-assisted auto** — current path, hardened. Dark-reference
>    mandatory, flash-detection default (`battleship_discover`),
>    per-phase timeouts, sign-verification probe, post-fit held-out
>    aim test. Fastest, zero operator touches; fails gracefully to
>    tier 2 when confidence drops.
> 2. **Camera-assisted, operator-in-loop** — auto captures fail to
>    localise the beam → surface a live camera frame, operator clicks
>    the beam centre. Samples flow into the same parametric fit. Same
>    math, same output; camera is still producing the depth/uv; the
>    operator is just the beam-detector.
> 3. **3-point manual aim** — no camera involvement. Operator drives
>    the beam to three known stage-mm points (marked on the floor, or
>    ArUco markers from the camera review) via a phone / gyro / slider
>    UI; records (stage XYZ, pan, tilt) triples; parametric model fits
>    from three samples. grandMA3 / Follow-Me 3D / Zactrack-alignment
>    pattern.
> 4. **GDTF / geometric-only trust** — no calibration. Use fixture
>    profile's `panRange` / `tiltRange` + fixture pose (`rotation`,
>    `position`) directly via `ParametricFixtureModel`'s analytic
>    form. Advisory banner ("uncalibrated — aim accuracy is geometric
>    only"). Works for any fixture the moment it's patched; operator
>    can promote to a higher tier anytime.
>
> **The bet:** no single auto-method is reliable enough alone. Every
> incumbent ships a manual pattern for this reason. Our lever is that
> tier 1 can drive the capture pass for the 80 % of rigs it works on,
> while tiers 2–4 are always available as first-class citizens, not
> emergency workarounds.

The current SlyLED implementation has tier 1 (flaky) and effectively
nothing else: `/api/calibration/mover/<fid>/manual` is stubbed but
unwired, and there's no GDTF-trust fallback. This review's
implementation phase lands 2, 3, 4 and hardens 1.

---

## 1. Purpose

Calibration produces the sample data that makes the IK primitive
(`ParametricFixtureModel.inverse(x, y, z) → (pan, tilt)`) actually
point the beam at `(x, y, z)` on real hardware. The alignment review
(§8.1 Q5) confirmed the runtime path is stage-space end-to-end — but
every downstream feature (Track actions, remote-vector aim, abstract
spatial effects) reads through this primitive. Unreliable calibration
⇒ every downstream feature is also unreliable.

Two production-relevant jobs for this subsystem:

1. **Produce a trusted (pan, tilt) ↔ (stage mm) mapping** per mover,
   in under a minute of wall-clock time, with one operator present.
2. **Keep producing it** when the rig is re-patched (mover moves,
   yoke slips, trim height changes). Re-calibration must be
   one-button and as reliable as first-time.

Accuracy target (proposed, to be ratified in §8):

- **Camera-assisted auto (tier 1):** aim within 100 mm of a commanded
  stage-mm point at 3 m throw, 95th percentile, on the basement rig.
- **Manual tiers (2–3):** aim within 200 mm at 3 m throw — good
  enough for Fn 1 tracking where the target is itself ±300 mm.
- **GDTF-trust (tier 4):** aim within the declared `panRange` /
  `tiltRange` accuracy of the profile; no calibration-tier guarantees
  but never *inverted*.

The 100 mm / 200 mm split is what defines "solid with fall-back
options" for this review.

---

## 2. Review principles

- **No backward compatibility.** Still first beta, no shipped shows
  (shared with `docs/mover-alignment-review.md` §2). Calibration data
  on disk may break; that's fine.
- **Every tier is first-class.** Tier 3 (manual 3-point) is not an
  emergency escape hatch — it's a supported workflow with its own UI,
  tests, and documentation. Same for tier 4.
- **Operator always sees what's happening.** During every phase, the
  operator sees (a) the current pan/tilt the mover is commanded to,
  (b) the live camera frame with the beam highlighted or annotated
  "no beam detected", (c) the current sample count and expected total.
  No silent stalls.
- **Every automated step has a manual override in the same UI panel**
  — not a deep menu. One click away.
- **Nothing completes silently.** "Calibration complete" requires
  operator sign-off after a held-out verification pass. This is the
  one place we copy the pro consoles wholesale — "aim at P5, does the
  beam land there? press Accept or Re-Calibrate."
- **scipy / OpenCV / RANSAC / anything allowed.** Same as the
  alignment review §2.
- **Build on the IK primitive.** `ParametricFixtureModel.inverse` is
  the only IK path. Calibration produces the samples that train it;
  it never replaces it.

---

## 3. Current SlyLED pipeline — what the code actually does

Grounded in the 2026-04-23 static-reading audit
(`/tmp/.../tasks/ad72581c06f1f1fa7.output` summary).

### 3.1 Entry point

```
POST /api/calibration/mover/<fid>/start   parent_server.py:4811
  → spawns _mover_cal_thread                 parent_server.py:4446
        pre-flight (profile + DMX channels)
        kick beam on, pan=tilt=0.5
        acquire calibration lock
        optional warmup sweep (~30 s)
        ─── discover() ────────────────    mover_calibrator.py:1659
        ─── map_visible() (BFS) ───────    mover_calibrator.py:1795
        ─── fit_model() (v2) ──────────    parametric_mover.py:324
        ─── verification_sweep() opt ──    parent_server.py:4719
        set fixture.moverCalibrated = True  (unconditional)
```

### 3.2 Discovery (`mover_calibrator.py:1659`)

- **Coarse grid:** 10×7 probes over pan ∈ [0.02, 0.98], tilt ∈ [0.1, 0.95].
- **Fine spiral:** from an initial estimate (derived from camera+mover
  pose if available, else `(0.5, 0.6)`), spiral out to radius 12 in
  0.05 pan/tilt steps.
- **Termination:** first beam found ⇒ success; else 80 probes
  exhausted ⇒ return `None`, thread aborts with `"Beam not found"`.
- **Alternative:** `battleship_discover()` at `mover_calibrator.py:593`
  uses **flash-detection** (beam on vs off, per-pixel diff) — much
  more robust. Wired as a function, never called from the default
  path.

### 3.3 BFS (`mover_calibrator.py:1795`)

- Seeded from discovery's (pan, tilt). Each visited point triggers a
  DMX write + `_wait_settled()` + `_beam_detect()`.
- Four-neighbour expansion (±0.05 pan/tilt). Boundary when detect
  fails; sample saved when detect succeeds.
- **Termination:** queue empty *or* 50 samples captured. No timeout,
  no cancel check inside `urlopen`.

### 3.4 Beam detection (`firmware/orangepi/beam_detector.py`)

- **Colour filter** — HSV, brightness ≥ 160, saturation ≥ 80 (for
  coloured beams), compactness (contour aspect ratio < 5).
- **Dark-reference subtraction** — supported (`set_dark_frame`) but
  the default calibration thread never calls
  `/dark-reference`. Active rig ambient, stage spill, sunlight, or a
  second lit fixture are all brighter than the threshold.
- **Multi-beam disambiguation** — `detect_center()` expects three
  beams horizontally arranged, picks median-X. Hard-coded assumption;
  fails for 1-beam, 5-beam, vertically-arranged, or multi-fixture
  scenes.

### 3.5 Fit (`parametric_mover.py:324`)

- Five continuous parameters (mount yaw, pitch, roll; pan offset;
  tilt offset) × four sign combinations = four LM solves; pick lowest
  RMS. `soft_l1` loss for outlier robustness.
- **Mirror ambiguity:** when two sign combinations fit within 0.2° of
  each other, `fit_model` picks the first, logs a warning, and moves
  on. `verify_signs()` exists at line 419 but isn't called.
- **No post-fit held-out test.** Samples that trained the model *are*
  the only samples it's ever validated against. Any sampling bias
  (e.g. BFS explored one lobe, missed another) is baked into the model
  silently.

### 3.6 Verification (`parent_server.py:4719`)

- Optional sweep at 3 held-out pan/tilt points after grid is built.
  Checks grid-lookup prediction against observed beam pixel.
- **Does not test the v2 parametric model.** Even when run, it
  validates the grid, not what's used in production.
- **Failures don't block completion** — `f["moverCalibrated"] = True`
  is set regardless of verification outcome.

### 3.7 What exists but isn't wired

- `battleship_discover()` (flash-based discovery)
- `verify_signs()` (mirror disambiguation probe)
- `_dark_reference()` (background-subtraction capture)
- `/api/calibration/mover/<fid>/manual` route (stubbed, not
  implemented end-to-end)
- The parametric model itself as a geometric-only fallback — never
  driven without samples.

Each of these is a building block the review recommends promoting to
the default path or to a tier-2/3/4 fallback surface.

---

## 4. State of the art — calibration in incumbent tools

Competitor scan summarised from 26 manufacturer docs / tutorials
(full URL list captured with the research). Every pro tool ships a
manual aim-at-known-points flow; no consumer tool has any geometric
awareness; nobody else in the consumer price bracket uses cameras.

### 4.1 Pro consoles (manual triangulation)

| Tool | Method | Operator touches |
|------|--------|------------------|
| **grandMA3** | 3- or 4-point per fixture: operator aims beam at known XYZ points, stores (pan, tilt) pairs; console solves pose (PnP-like). | Minutes per fixture. |
| **Chamsys MagicQ** | 4 stage-corner palettes (DSR/DSL/USL/USR); operator aims a tight beam at each stage corner. | Similar — simpler metaphor than MA3's arbitrary XYZ. |
| **High End Hog 4** | No geometric solve. Operator records named **Position Palettes** per fixture by eye. Pan/tilt swap/invert flags for rig orientation. | Every position is a cue; accuracy = whatever the operator eyeballed. |
| **Avolites Titan** | Position palettes + personality-level invert/swap. No public geometric solver. | Palette-centric like Hog 4. |

### 4.2 Tracking-specialised (sensor-assisted)

| Tool | Method | Cost / effort |
|------|--------|---------------|
| **BlackTrax (CAST)** | IR beacons + wand calibration of IR cameras; then per-fixture aim at tracked beacons. | ~$30 k+; wanding is laborious. |
| **Follow-Me 3D** | 4 measured stage points + per-fixture aim refinement; trackball operator mode. | €5 k–€25 k + hardware. |
| **Zactrack PRO / SMART** | UWB beacons on fixtures/performers + "alignment puck": operator aims each fixture at 4 puck positions. Solves pose + stage geometry simultaneously. | €15 k–€60 k; < 1 min per fixture after setup. Philosophically closest to SlyLED. |
| **TAIT Navigator** | Delegates to a third-party tracker (BlackTrax / Zactrack). | Not a direct comparable. |
| **Disguise Designer (d3)** | Trusts CAD/MVR pose for fixtures; calibration focuses on the tracked camera/LED volume, not the movers. | Fixture pose is set, not solved. |

### 4.3 Consumer / OSS

| Tool | Method |
|------|--------|
| **QLC+** | None. Manual DMX sliders; "calibration" = saved scene. Maintainer has stated 3D spatial tracking will never land in QLC+4. |
| **Freestyler DMX** | None. Pan/tilt resize / invert prefs in the fixture file. |
| **DMXIS / Lightjams** | None for movers. Lightjams has 2D/3D LED pixel maps for ambient fixtures; movers are sliders + macros. |
| **Resolume Arena** | None for movers. DMX output is pixel-mapped colour/intensity. |

### 4.4 Schema-driven (geometric-only)

**GDTF** describes the fixture's intrinsic geometry (beam origin, pan/tilt
axes, ranges). **MVR** carries the fixture's pose in the stage (position +
rotation). A console that trusts both can compute aim purely from
geometry — no per-fixture calibration pass.

Every major pro console consumes MVR (grandMA3, Vectorworks, Depence,
Capture, MagicQ) but **none treats it as ground truth without
verification**. Real rigs deviate from CAD: hanging hardware flexes,
yokes slip, pan-home offsets drift. A calibration pass still runs on
top; MVR is a prior, not an oracle.

### 4.5 Camera-assisted auto-calibration of movers

**Nobody else in the consumer price bracket does this.** Research on
camera auto-calibration (Hartley–Zisserman classics; VLP / UAV
self-calibration literature) exists, but no turnkey "point a USB
camera at the stage and auto-solve mover pan/tilt-to-aim" product
ships today. Zactrack's UWB puck and BlackTrax's IR wand are the
closest commercial analogs — both need dedicated sensing hardware and
cost 100× what a USB webcam does.

This is SlyLED's lever and also why our tier-1 approach has no
prior-art template to copy. When tier 1 works, nobody else can match
us on price or on zero-operator-touches. When it fails, we need to
fall back to the manual patterns the incumbents have refined for 20+
years — which is what §0 tiers 2–4 encode.

---

## 5. Gap analysis

Mapping the Top-5 audit failures to the competitor behaviour — where
we're uniquely weak, where we can uniquely win.

### 5.1 Where SlyLED is weaker than every incumbent

1. **Discovery depends on a geometric estimate nobody else relies
   on.** Pro consoles' "point at P1" is operator-driven — the operator
   can move the beam until it's on P1, regardless of mount orientation.
   Our coarse grid + spiral is pre-computed from a floor-target
   calculation that silently fails on inverted mounts (pipeline audit
   Q1, Q2). **Fix:** battleship/flash discovery (already in
   `mover_calibrator.py:593`) scans the reachable hemisphere without a
   geometric seed. Promote it to the default path.
2. **No dark-reference / no flash-detection in the default flow.**
   Incumbents don't have this problem because they use human eyes,
   not a camera. We opted into the camera and then didn't use its
   superpowers. **Fix:** dark-reference before every calibration
   session; flash-detection (beam-on vs beam-off diff) as the default
   detection mode, colour-filter as the fallback.
3. **Mirror ambiguity is silent.** grandMA3 / MagicQ force the
   operator to aim at 3+ points so the hemisphere is unambiguous.
   Zactrack's puck-alignment has the same property. We fit 4 sign
   combinations and pick the first low-RMS one without disambiguating
   (audit Q4). **Fix:** always run `verify_signs()` as a
   post-discovery 2-probe probe (nudge pan +0.02, confirm beam delta
   direction) before BFS starts.
4. **No post-fit verification.** Every pro console ends calibration
   with "aim at P-final, does it look right?" and the operator
   presses Accept. We set `moverCalibrated = True` unconditionally
   (audit Q5). **Fix:** held-out 5th-point test, operator clicks
   Accept / Retry; surface pixel error relative to prediction.
5. **No manual fallback.** Every incumbent has one (§4.1, 4.2).
   We have a stubbed `/manual` route nobody's implementing. **Fix:**
   tier 3 is a first-class UI path.

### 5.2 Where SlyLED can uniquely win

- **Price.** $30 USB webcam vs. $30 k IR-beacon rig. 1000× cost
  advantage that holds forever.
- **Zero beacons, zero pucks, zero wands.** The "known reference
  point" is the beam itself. No physical targets to place or wire.
  Zactrack's puck costs more than an SlyLED full kit.
- **Zero operator touches on tier 1.** Pro consoles need a human to
  eyeball each aim target. Our tier 1 can sweep, capture, fit and
  verify autonomously in < 1 min if it just works. Only failure
  mode is wrong answers, which §5.1 fixes surface.
- **Continuous re-calibration.** No incumbent does this. A camera
  that watches the rig can detect drift between shows (yoke slip, trim
  change) and trigger an automatic re-cal. Not scope for this review
  but it's the long-term lever.
- **Same camera handles fixture and performer.** One sensor for Fn 1
  (person tracking) and tier 1 (fixture calibration). Incumbents need
  separate sensor networks for the two jobs.

### 5.3 Where we must copy the incumbents

- **Tier 3 (3-point manual aim).** This is *the* proven fallback.
  grandMA3 / Follow-Me / MagicQ / Zactrack all ship it. UX detail
  to steal: phone/gyro drives the beam while operator watches on
  stage; "record P1" button captures (pan, tilt) ↔ typed-in XYZ.
- **Tier 4 (GDTF-trust).** Disguise trusts MVR to seed. We should
  too — the parametric model already accepts mount yaw/pitch/roll +
  pan/tilt ranges; feed the profile + fixture pose and we have a
  functional IK with zero samples. Not *accurate* but never
  *inverted*.
- **Hemisphere disambiguation UX.** grandMA3 makes the operator
  confirm "is the beam in front of you?" before recording. A simple
  yes/no after `verify_signs()` is 10 seconds of operator time and
  eliminates a whole class of silent failure.
- **Per-fixture blackout during sweeps.** Zactrack explicitly
  calibrates one fixture at a time with everything else dark. We
  should too — the BFS false-beam problem (audit Q9) disappears if
  no other fixture is lit.
- **Oversample + average for backlash.** Pro consoles get this free
  from operator-averaged aim. We need to oversample at each (pan,
  tilt) and median-filter to suppress yoke backlash + pan-home
  offset noise.

---

## 6. Review questions

Each question cites the audit finding(s) or competitor lever it
builds on. Every implementation recommendation in §8 must cite which
question(s) it answers.

### 6.1 Tier 1 robustness — discovery + capture + fit

1. **Flash-detection as default discovery?** `battleship_discover()`
   already implements it (audit Q2). Should it replace
   colour-filter discovery outright, or be tried first with
   colour-filter as a fallback? What's the probe budget and timeout
   for each?
2. **Mandatory dark-reference + per-session re-capture?** Dark-frame
   subtraction is supported but never auto-called (audit Q7). Capture
   once at calibration start is obvious; should we also re-capture on
   lighting-change events (ambient level crosses a threshold)?
3. **Sign-verification probe.** `verify_signs()` exists (audit Q4);
   should it run unconditionally after discovery, or only when the
   fit produces ambiguous sign combinations?
4. **Per-phase timeouts + circuit breakers.** No phase has a wall-
   clock timeout; a hung `urlopen` blocks 5–30 s per probe (audit
   Q11). What's the right per-phase budget (discovery ≤ 90 s? BFS ≤
   120 s?) and what's the behaviour on timeout — abort, fall back to
   tier 2, retry?
5. **Post-fit held-out verification.** One held-out aim test is the
   MVP; should we also require N ≥ 3 points, pass/fail thresholds in
   pixels or in degrees, and an operator-accept step (pro-console
   pattern)?
6. **Backlash / oversampling.** Pro consoles get backlash tolerance
   from operator-averaged aim. Should each (pan, tilt) sample be
   captured N times (N=3? N=5?) and median-filtered before being
   passed to the fit?

### 6.2 Tier 2 — operator-in-loop beam click

7. **When does tier 2 activate?** On discovery timeout, BFS sample
   count < some threshold, or as an always-available manual override?
   Operator-triggered or automatic?
8. **UI / UX.** Does the operator click on a still frame or a live
   feed? One point at a time or mark multiple beams? What's the
   minimum sample count before fit runs?

### 6.3 Tier 3 — 3-point manual aim

9. **Reference point source.** Physical floor markers the operator
   surveys? ArUco markers from the camera-review pipeline (surveyed
   in `/api/aruco/markers`)? Pre-defined "stage corners"
   (MagicQ-style)?
10. **Aim drive mechanism.** Phone gyro (already working per
    alignment-review §8.1 Q5)? Slider UI? Trackball-style? All three
    as configurable options?
11. **Minimum point count + geometry constraint.** Pure 3 points, or
    push for 4 to break the mirror ambiguity purely from the samples?
    What's the failure mode when the operator picks 3 near-colinear
    points?

### 6.4 Tier 4 — GDTF / geometric-only

12. **When is tier 4 acceptable?** Always available as a "use this
    fixture right now without calibrating" option? Or gated behind a
    big "not calibrated" banner? What's the operator's upgrade path
    from tier 4 → tier 3?
13. **MVR import as tier-4 seed.** Incumbents like Disguise take MVR
    as ground truth. Should we accept MVR to pre-populate fixture
    pose + rotation, then let the operator run tier 1–3 on top?

### 6.5 Cross-cutting

14. **Operator visibility during calibration.** Today the operator
    sees a percentage (audit Q10). Minimum viable: live camera frame
    with beam-detector overlay, current commanded pan/tilt,
    per-phase time-budget countdown, Cancel-that-actually-cancels.
    What else?
15. **Multi-fixture isolation.** Blackout every other fixture during
    a single fixture's calibration sweep (audit Q6). Is this
    mandatory, or a "recommended" operator checkbox? What's the
    back-out plan if the operator forgets (auto-blackout + warning)?
16. **Acceptance test as the calibration gate.** Proposal: aim at
    10 known stage points, assert max-error < 100 mm (tier 1), 200
    mm (tier 2–3), no assertion (tier 4). This is what "works"
    means on the basement rig. Should this be the test that blocks
    "Calibrated" status from being written?

---

## 7. Method

Each question resolves via one of:

- **Static reading.** Already done (2026-04-23 audit). §3 + §5
  reference the code. More static reading only where §8 surfaces
  new "what does this function actually do?" questions.
- **Competitor verification.** Done (2026-04-23 scan, §4). Revisit
  only if a specific tool's claim needs double-checking.
- **Synthetic prototype.** Simulate a mover (known mount params)
  + simulated camera (projection + pose). Feed the pipeline
  synthetic samples; assert the fit recovers the known mount. This
  is the math-level verification for §6 Q3 (sign verification), Q6
  (backlash median filtering), Q11 (3-point minimum fit).
- **Basement-rig live test.** The only way to settle §6 Q1–Q2, Q4,
  Q10, Q14–Q16. Rig already has 3 movers + 2 cameras + ArUco
  markers (camera-review §8.3 baseline).

### 7.1 Live-test protocol (run once per question batch)

1. **Cold start** — server reset, all calibrations cleared, fixtures
   patched from profile only.
2. **Tier 4 baseline** — before any calibration, aim each mover at
   5 known stage points. Record pixel-error from camera. This is
   the "never worse than geometric" floor.
3. **Tier 1 auto-cal** — hit Start Calibration, measure wall-clock
   to completion. If it never completes, log the symptom
   (stuck phase + what the camera saw) and drop to tier 2.
4. **Tier 2 operator-click** — present live frame, operator clicks
   beam for each of the BFS positions. Measure operator time +
   resulting fit quality.
5. **Tier 3 manual** — operator drives mover to 3 surveyed ArUco
   markers, records. Measure operator time + fit quality.
6. **Verification pass (all tiers)** — aim at 10 held-out stage
   points, measure pixel-error + stage-mm-error. This is the
   "calibration works" number.
7. **Drift retest** — bump the mover yoke by a few degrees, re-run
   the verification pass without re-calibrating. This quantifies
   how brittle the current calibration is to physical disturbance.

### 7.2 Synthetic prototype (no hardware)

Write a `tests/test_calibration_synthetic.py` that:

- Instantiates a `ParametricFixtureModel` with known ground-truth
  params (mount yaw/pitch/roll, pan/tilt offsets).
- Generates N sample points `(pan_i, tilt_i)` from a BFS-like sweep;
  computes ground-truth aim via the model's `forward()`.
- Projects aim points through a simulated camera (pinhole model,
  known extrinsics); adds Gaussian pixel noise ± σ.
- Feeds `(pan_i, tilt_i, pixel_u_i, pixel_v_i)` to `fit_model()`.
- Asserts recovered params match ground truth within tolerance.
- Sweeps: noise levels, sample counts (3 / 10 / 50), sign
  ambiguity cases, colinear point arrangements.

This is the `test_beam_detector.py`/`test_spatial_math.py`-style
no-hardware regression that every future calibration change must
continue to pass.

---
