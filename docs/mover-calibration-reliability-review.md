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

## 8. Findings

Mirrors `docs/mover-alignment-review.md` §8.1 (static-reading round) and
§8.3 (live-test resolution). Pipeline-audit outputs (2026-04-23) are
already reflected in §3 and §5.1 and are not duplicated here.

### 8.1 Static reading — tier 1 hardening (Q1–Q6)

Each finding cites source code (`file:line`), states the concrete change
in one paragraph, and flags what §7.1 live-test must resolve that
code-reading cannot.

**Note on §7.2 — sample shape.** The review spec §7.2 was drafted when
`fit_model` consumed `(pan, tilt, pixel_u, pixel_v)`. The v2
implementation at `parametric_mover.py:324` consumes
`(pan, tilt, stageX, stageY, stageZ)` — the camera node's depth lookup
and ray/floor intersection run upstream of `fit_model`, so samples
arrive in stage-mm. The synthetic prototype
(`tests/test_calibration_synthetic.py`) follows the code.
`verify_signs()` at `parametric_mover.py:419` remains pixel-native and
is where the §7.2 pinhole simulation lives.

#### Q1 — Flash-detection as default discovery

- **Code state.** `battleship_discover()` at `mover_calibrator.py:593`
  implements `coarse_steps × coarse_steps` flash probes (default 4 ⇒ 16
  probes) with a confirmation nudge (pan±0.02, tilt±0.02, require ≥ 8
  px beam movement) to reject reflections. Seed-aware: sorts probes by
  distance to `(seed_pan, seed_tilt)` so a good estimate hits in
  3–5 probes. `discover()` at `:1659` is the current default and uses a
  colour-filter 10×7 grid (70 probes) + radial spiral (up to
  `max_probes=80` total) with no ambient-rejection guard.
- **Finding.** Promote `battleship_discover` to the default path.
  Colour-filter `discover()` becomes the tier-1 fallback when
  battleship returns `None` (low-FPS camera where on/off diff blurs,
  or high-ambient cases). Keep the seed path; battleship already
  consumes it.
- **Cost / risk.** Worst case 16 × 5 s = 80 s urlopen-stall on a
  wedged camera — must land with Q4's phase timeout.
- **Open for live-test.** Correct `coarse_steps` for basement coverage
  (4 vs 5 vs 6) — tuning question, not a code finding.

#### Q2 — Mandatory dark-reference + per-session re-capture

- **Code state.** `BeamDetector.set_dark_frame` at
  `beam_detector.py:33` stores per-camera dark frames;
  `cv2.absdiff(frame, dark)` is applied in detect paths at lines 66,
  75, 163, 171. The `/dark-reference` camera endpoint at
  `camera_server.py:1271` captures a frame and calls `set_dark_frame`.
  The helper `_dark_reference(camera_ip, cam_idx=-1)` exists at
  `mover_calibrator.py:1193`. **No call site in
  `parent_server.py`'s calibration thread.**
- **Finding.** The current phase order kicks the beam on at
  `parent_server.py:4488–4502` *before* acquiring the calibration lock
  at `:4507`. Dark-reference must be captured with the beam **off** —
  otherwise the frame contains the beam-reflection ambient we're
  trying to subtract out. Restructure the thread as:
  `pre-flight → acquire lock → dark-reference capture (beam off) →
  kick beam on → warmup → discover`.
- **Cost / risk.** +~1 s wall-clock per session (capture across all
  cameras). Dark frame is per-camera — adding / moving a camera
  mid-session invalidates it, but that's a future concern (§12).
- **Open for live-test.** Auto-re-capture trigger on ambient change
  needs the basement-rig ambient-delta distribution to set the
  threshold — §7.1.

#### Q3 — Sign-verification probe

- **Code state.** `verify_signs()` at `parametric_mover.py:419` is
  pure math — takes `(pixel_before, pixel_after_pan+,
  pixel_after_tilt+)` and returns `(pan_sign, tilt_sign)` ∈
  {-1, +1}². `fit_model()` at `:324` runs all four sign combinations
  and picks lowest RMS; per the comment at `:400–402` the old
  "first low-RMS" tie-break is gone and the caller is expected to
  supply `force_signs` when the top two mirrors fit within 0.2°
  (`:404`). **Nothing supplies `force_signs`** — it's plumbed through
  at `:363` but no call site in the codebase invokes `verify_signs`
  to compute it, leaving the mirror ambiguity silent (§5.1 #3).
- **Finding.** Run `verify_signs` unconditionally at the end of
  discovery, before BFS. After `discover()` returns
  `(pan, tilt, pixel_x, pixel_y)`, issue two additional DMX writes
  (`pan + 0.02`, reset; `tilt + 0.02`, reset) with beam detect at
  each, and pass the computed signs into
  `fit_model(..., force_signs=(ps, ts))`. Collapses the four-sign LM
  loop into a single solve (~4× faster) and closes the silent
  mirror-ambiguity hole.
- **Cost / risk.** 2 additional probes (~0.5–1 s each with settle) in
  discovery. Low risk — `battleship_discover` already uses the same
  nudge pattern for confirmation.
- **Open for live-test.** Sensitivity of sign recovery to nudge
  magnitude (±0.02 vs ±0.01) on real-rig noise — the new synthetic
  test quantifies the pixel-noise floor; §7.1 confirms the hardware
  number.
- **Corroborating artifact.** `tests/test_parametric_mover.py:155`
  (`test_fit_recovers_ground_truth`) fails on this branch under the
  current `fit_model` (97/98 assertions; `pan_offset` lands at 0.56
  vs asserted 0.48). The scipy `least_squares` warning on the same
  run is the exact mirror-ambiguity message quoted above. This
  pre-existing failure is evidence of the silent-mirror cost; closing
  Q3 in implementation will also repair it.

#### Q4 — Per-phase timeouts / circuit breakers

- **Code state.** All camera-node calls use hardcoded urlopen
  timeouts: beam-detect 5 s at `mover_calibrator.py:1154`, depth-map
  30 s at `:1183`, dark-reference 10 s at `:1210`. No wall-clock
  budget at any phase. In `_mover_cal_thread_body`
  (`parent_server.py:4446`) discovery, BFS, fit, verification run to
  completion; a wedged camera compounds to 80 probes × 5 s = 400 s
  stall.
- **Finding.** Add a `time.monotonic()` budget guard per phase inside
  the thread. Proposed budgets as named module constants:
  - Discovery (battleship): 60 s
  - Discovery (colour-filter fallback): 90 s
  - BFS: 120 s
  - Fit: 10 s
  - Verification: 30 s

  On timeout: call `_cal_blackout()`, set thread error to
  `"phase_timeout"`, surface captured samples so far on the status
  endpoint, mark the fixture `pendingTier2Handoff=True` rather than
  aborting without recourse. Tier 2's operator-in-loop UI picks up
  that flag.
- **Cost / risk.** Zero on the happy path — pure guard logic.
- **Open for live-test.** Whether 60 s battleship is generous on the
  slowest camera node (Orange Pi Zero 2) — §7.1.

#### Q5 — Post-fit held-out verification

- **Code state.** `verification_sweep()` at `mover_calibrator.py:903`
  aims 3 held-out points and measures pixel error against
  `grid_lookup()` — it validates the v1 grid, not the v2 parametric
  model. Its pass/fail is advisory: `parent_server.py:4748–4750` logs
  failures but does not block the unconditional
  `f["moverCalibrated"] = True` at `:4779`.
- **Finding.** Rewrite verification to call
  `ParametricFixtureModel.inverse(x, y, z)` for N ≥ 3 (proposed: 5)
  held-out stage-mm points, command the mover, capture the beam
  pixel, and compare predicted vs observed. **Gate
  `f["moverCalibrated"] = True` on verification pass.** Operator sees
  per-point error + pass/fail; Accept / Retry buttons, and Retry
  leaves `moverCalibrated` false. Threshold: 100 mm stage-space error
  at 3 m throw (§1 tier-1 target). Pixel threshold derived inline
  from per-camera FOV + fixture distance — not hardcoded.
- **Cost / risk.** Held-out points must be truly held out. Select
  from reachable regions **outside** BFS-explored boundaries
  (`map_visible` returns them at `:1795`). If the BFS explored only a
  narrow lobe, "outside" can exceed camera FOV — fallback rule
  needed (e.g., sample BFS-interior if outside-region is
  camera-invisible). Flag for §7.1.
- **Open for live-test.** Realistic pass thresholds on basement rig.
  100 mm is aspirational — §7.1 verification data sets the floor.

#### Q6 — Backlash / oversampling

- **Code state.** Each BFS probe is a single
  `_wait_settled() + _beam_detect()`
  (`mover_calibrator.py:1795` onward). `_wait_settled()` at `:1091`
  waits for pixel convergence (`SETTLE_BASE=0.4 s`, escalating to
  1.5 s via `SETTLE_ESCALATE` at `:34`) but captures a single pixel
  once converged. No repeat-and-median logic anywhere in the probe
  pipeline.
- **Finding.** Oversample each BFS probe N=3 times with ~50 ms gap;
  median-filter `(pixel_x, pixel_y)` component-wise before appending
  to the sample list. Convergence proves drift < `SETTLE_PIXEL_THRESH
  = 30` px (`:36`) but doesn't suppress per-capture sensor noise or
  residual yoke backlash (~50–100 mm = ~15 px at 3 m throw on a 640
  px frame). Median-of-3 is the pro-console aim-averaging pattern
  (§5.3).
- **Cost / risk.** +~100 ms/probe × 50 probes = 5 s BFS wall-clock.
  Median-of-3 tolerates one outlier per probe — strengthens the
  outlier-resistance behaviour already tested at
  `test_parametric_mover.py:248`.
- **Open for live-test.** Actual backlash magnitude on basement
  movers — §7.1 drift retest (#7). The synthetic test validates the
  median-filter math under simulated noise.

#### Synthetic validation accompanying this round

`tests/test_calibration_synthetic.py` (new, 27 assertions) exercises
the math behind Q3 and Q6 without hardware:

- noise sweep on stage-space samples (σ 0 / 10 / 50 / 200 mm)
- sample-count sweep (3 / 10 / 50)
- colinear-geometry degeneracy flagged by `FitQuality.condition_number`
- four-sign RMS gap — correct signs ≫ wrong signs
- `verify_signs` robustness under σ=3 px Gaussian pixel noise

**Metric.** Accuracy is asserted via **held-out angular error** —
`fit.forward(p, t)` vs `truth.forward(p, t)` on 20 unseen
(pan, tilt) probes — not via raw mount-param recovery. The
5-parameter (yaw, pitch, roll, pan_offset, tilt_offset) decomposition
has near-equivalent tuples that produce the same beam rays; only the
*predictions* must match truth. This also matches what downstream
production code relies on (`ParametricFixtureModel.forward/inverse`
called from track actions, remote-vector aim, spatial effects).

**Locally convergent solver.** The test uses small mount deviations
(yaw=5°, pitch=3°, roll=2°) matching a properly-hung fixture. The LM
solver is locally convergent; large truth deviations (≥ 15°) can
expose additional local minima at moderate noise. These cases fall
through to tier 2/3 operator-in-loop calibration in the hardened
pipeline — they are not in tier-1 synthetic scope.

Together with the existing `test_parametric_mover.py` coverage (clean
fit recovery, sign flip, inverted mount, outlier inflation), the
parametric-fit subsystem now has end-to-end no-hardware regression
coverage. Regression gate for every subsequent tier-1 fix.

### 8.2 Tier 2–4 static reading — placeholder

To be populated. Tier 2 (Q7–Q8), tier 3 (Q9–Q11), tier 4 (Q12–Q13)
cover surfaces whose code is either stubbed or absent; most answers
need implementation-phase decisions, not code-reading.

### 8.3 Live-test resolution — placeholder

To be populated after a §7.1 protocol run on the basement rig.

---

## 9. Out of scope

- **Camera intrinsic / extrinsic calibration.** That's the camera
  review's territory (`docs/camera-calibration-review.md`, PR #632).
  This review assumes `(u, v) → stage-mm ray` is solved.
- **DMX profile correctness.** Profile editor + OFL import own the
  `panRange` / `tiltRange` / channel map. Bad profile ⇒ bad
  calibration, but fixing profiles is a separate surface.
- **Stage coordinate system.** Locked at X=width, Y=depth, Z=height
  per `project_coordinate_system.md` + alignment review #600.
- **Fixture discovery / patching.** Adding a mover to the layout is
  upstream; this review starts once a fixture exists in `_fixtures`.
- **Moving-head hardware-level quirks.** Pan wrap, tilt limits,
  home-position reset, thermal-compensated backlash — the review
  treats these as "fit absorbs them" via oversampling (Q6). Deep
  per-fixture quirks need per-profile annotation, which is out of
  scope here but flagged for the profile editor.
- **Continuous drift re-calibration.** Long-term lever (§5.2). Not
  in this review's implementation plan; filed for a future review.

---

## 10. Related open issues

- **#488** — `ParametricFixtureModel` + LM solver. The IK primitive
  this review feeds. This review's §6 Q3, Q6 feed back into it.
- **#610** — Mover calibration discovery / blink-confirm /
  validation. This review IS the concrete plan for #610 — close
  #610 when this review's §8 lands.
- **#486** — v1.5.8 live-test bug log (closed items already cover
  several calibration-UX issues; re-open as needed if §8.3 surfaces
  regressions).
- **Alignment review (PR #643)** issues carry over as tier-1
  dependencies: #633 (3D remote gizmo — tier 3 UX needs it), #635
  (shared IK fallback helper — tier 4 mechanics).
- New issues filed from §8 will be labelled
  `mover-calibration-reliability-review-2026-04-23`.

---

## 11. Change log

- **2026-04-23** — Initial draft (§0–§7 + §10). Born from the
  realisation that the mover-alignment review (PR #643) shipped
  architecture without touching the calibration-never-completes
  operator pain. Branch
  `claude/review-mover-calibration-reliability`. Based on a pipeline
  audit of `mover_calibrator.py` / `beam_detector.py` /
  `parent_server.py` and a competitor scan of 14 tools.

---

## 12. Recommendations for further exploration

To be filled in after §8 lands. Mirrors camera review §12 and
alignment review §12 — a place for ideas surfaced during the review
that aren't in the immediate fix list but are worth scheduling.

### 12.1 Continuous drift re-calibration (future)

Once tier 1 works reliably, a camera that watches the rig can detect
yoke slip / trim changes between shows by periodically re-verifying
a known aim (e.g. every 30 min: command mover to stage centre,
measure beam pixel, compare to baseline). Deviation above a
threshold triggers an operator advisory and optional automatic
re-cal. No incumbent ships this. Leverage we already have (cameras +
calibration pipeline) but out of scope for the first-stable-release
pass.

### 12.2 MVR import as tier-4 seed (future)

Vectorworks / Capture export an MVR with every fixture's pose.
Consuming it pre-populates `_layout.children` + fixture rotation
before any calibration runs — tier 4 becomes "use what the lighting
designer drew" for free. Scope: an `/api/project/import-mvr`
endpoint plus the MVR parser. Flagged for a future review once tier
4 lands.
