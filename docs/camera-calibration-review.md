# Camera Calibration Review Plan

**Status:** Draft — review only, no code changes until this plan is approved.
**Date:** 2026-04-22
**Scope owner:** Dave (operator) + Claude (implementation)
**Related docs:** `docs/mover-calibration-v2.md` (#488), `docs/camera-review.md` (Gemini 2026-04-15), `docs/camera.md`, `docs/pointcloud.md`

---

## 1. Purpose

The camera subsystem exists for exactly **two production-relevant jobs**:

1. **Track people (and other objects) in stage space.** YOLO on the Orange Pi detects
   objects; the orchestrator must place them as temporal objects at the right stage
   `(x, y, z)` mm so that tracks, timelines, and auto-track movers follow them.
2. **Assist moving-head calibration.** When a fixture fires its beam, the camera
   detects the beam spot, and that detection is used to recover the fixture's
   pan/tilt↔stage-XYZ relationship.

Everything else the camera node can do (depth maps, surfaces, point clouds,
structured-light refinement, 3D viewport overlays) is **secondary** and only earns
its keep by improving one of those two jobs.

This plan defines **what we'll review, the questions we want answered, and the
method** — before any code is edited. No merges until we agree on the findings and
prioritised fixes.

## 2. Review principles

- **One coordinate system end-to-end.** Stage mm per `project_coordinate_system.md`
  (origin stage-right / back-wall / floor; X=width, Y=depth, Z=height). Any hop
  between frames must be explicit and testable.
- **Don't trust silent assumptions.** "Back-wall camera, zero rotation" is a valid
  config but not the only one. Whenever code bakes an assumption in, flag it.
- **Two separate products, two separate accuracy budgets.** Tracking tolerates
  ±100–200 mm (person is 400 mm wide). Mover calibration needs ±10–30 mm at the
  floor (beam waist on a 350 W fixture is ~150 mm). Don't over-engineer tracking
  or under-engineer calibration.
- **Operator-first, not algorithm-first.** Per `feedback_cal_algorithm.md`: the
  operator will not run a multi-frame chessboard wizard. Survey markers once,
  reuse forever. Any proposal that requires per-session ArUco gymnastics is out.
- **No new dependencies without justification.** numpy + cv2 are in. scipy is out
  (hand-roll LM, per `project_calibration_v2_phase1.md`).

---

## 3. Current architecture — snapshot

### 3.1 Hardware context (basement rig, reference)

Per `project_basement_rig.md`:

- Two EMEET 4K cameras on one Orange Pi at `192.168.10.235`, both at
  `(x, 120, 1920) mm`, tilt-down 30°, FOV ≈ 90° (diagonal spec).
- Camera-visible floor band ≈ Y ∈ [1400, 2967] mm — the cameras cannot see their
  own near-field (Y < 1400).
- Three surveyed ArUco markers (DICT_4X4_50, 150 mm, all on floor Z=0).
- Three DMX fixtures: two ceiling-ish movers, one floor-mount 350 W BeamLight.

This is the rig every recommendation must work on — not a pristine lab.

### 3.2 Job A — tracking pipeline

```
 ┌──────────────────────────┐      ┌───────────────────────────┐
 │ Orange Pi camera node    │      │ Orchestrator (Flask)      │
 │                          │      │                           │
 │ capture → detector.py    │      │ /api/objects/temporal     │
 │   (YOLOv8n, pixel bbox)  │──┐   │   → simple proportional   │
 │                          │  │   │     pixel→stage (L7205)   │
 │ tracker.py _tick()       │  │   │                           │
 │   - pixel-center re-ID   │──┴──>│ _temporal_objects[]       │
 │   - TTL, reidMm          │      │                           │
 │   - trackClasses filter  │      │ /api/objects  (GET)       │
 │                          │      │   → bake, auto-track,     │
 │ POST pixelBox+frameSize  │      │     3D renderer           │
 └──────────────────────────┘      └───────────────────────────┘
```

**Key files:** `firmware/orangepi/tracker.py`, `firmware/orangepi/detector.py`,
`desktop/shared/parent_server.py` (functions `api_objects_temporal_create`
line ~7205, `_pixel_to_stage` line ~1888, `_pixel_to_stage_homography` line ~1859,
`_evaluate_track_actions` line ~10179).

**Frame journey:** pixel (camera) → pixel bbox (detector) → **simple proportional
scale assuming back-wall camera** (orchestrator) → stage mm.

### 3.3 Job B — mover-calibration pipeline

```
 ┌────────────┐  1. /api/cameras/<fid>/stage-map
 │ Surveyed   │─────────────────────────────────┐
 │ ArUco map  │  (one-off per venue)            │
 └────────────┘                                 ▼
                                  ┌───────────────────────────────┐
                                  │ Homography H_pixel→floor      │
                                  │ stored in:                    │
                                  │   _calibrations[fid].matrix   │
                                  │   fixture.homography          │
                                  └──────────────┬────────────────┘
                                                 │
         ┌───────────────────────────────────────┘
         │  2. /api/calibration/mover/<fid>/start  (mode = legacy|v2|markers)
         ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ mover_calibrator.py                                             │
 │   Legacy:  discovery → BFS map → grid → pixel_to_pan_tilt       │
 │   v2:      ParametricFixtureModel.inverse → target→pixel nudge  │
 │   Markers: battleship → flash-confirm → nudge to ArUco pixels   │
 │                                                                 │
 │ Writes samples → parametric_mover.fit_model (LM solver)         │
 │ Stores:  _mover_cal[fid] = { samples, grid?, model, fit }       │
 └─────────────────────────────────────────────────────────────────┘
                                                 │
                                                 ▼
                               ┌──────────────────────────────────┐
                               │ mover_control.MoverControlEngine │
                               │   _aim_to_pan_tilt →             │
                               │     ParametricFixtureModel.inverse│
                               └──────────────────────────────────┘
```

**Key files:** `desktop/shared/mover_calibrator.py` (2436 lines),
`desktop/shared/parametric_mover.py` (412 lines, LM solver),
`desktop/shared/parent_server.py` (stage-map route line ~2463, mover-cal routes
`_mover_cal_thread_*`), `firmware/orangepi/beam_detector.py`.

### 3.4 Shared plumbing (both jobs)

- `desktop/shared/camera_math.py` — canonical `build_camera_to_stage(tilt, pan, roll)`
  rotation matrix. Must be the only place any code derives a camera→stage rotation.
- `desktop/shared/space_mapper.py`, `stereo_engine.py`, `surface_analyzer.py` —
  point-cloud helpers. Used for surfaces/pillar/floor detection, not for primary
  tracking or mover cal in the current flow.
- `_calibrations` dict + `fixture.homography` — **two stores for the same
  value** (#592 rationale). Source of truth is unclear.

---

## 4. What we already know is wrong or suspect

Findings that dropped out of the architecture survey. Each should be validated
or refuted during the review, not patched yet.

### Job A — tracking

| # | Finding | Evidence | Impact |
|---|---------|----------|--------|
| A1 | `api_objects_temporal_create` **does not use** `_pixel_to_stage` / homography. It runs a simple `pos = [sw·(1-cx), sd·(1-cy), 0]` back-wall proportional mapping and ignores camera rotation, FOV, and position. | `parent_server.py:7218-7232` | Wrong stage placement for any camera that isn't back-wall facing audience. On the basement rig (tilt-down 30°), near-field persons are already outside the band y∈[1400,2967] yet the mapping will still place them anywhere in [0, stage_d]. |
| A2 | `_pixel_to_stage` exists, supports homography and FOV ground-plane projection, is used by `_evaluate_track_actions` — but the tracker-ingestion endpoint was written before it was wired in. | `parent_server.py:1888` (helper), `parent_server.py:7218` (endpoint doesn't call it) | Dead-but-loaded code; simple fix, need to validate test coverage. |
| A3 | No multi-camera fusion. Each camera posts its own temporal objects; two cameras seeing one person = two objects. Re-ID is per-camera only (`tracker.py` proximity re-ID, 500 mm). | `tracker.py _tick()` | Trackers double-fire and fight each other when auto-track uses both cameras. |
| A4 | TTL is fixed (5 s default) and not occlusion-aware. A person re-entering frame gets a new object. | `tracker.py`, `api_objects_temporal_create` | Rapidly spawns/expires objects; auto-track jitters on occlusion. |
| A5 | Camera-node `tracker.py` has (a) a `_tracks` dict mutated in a worker thread and read from Flask threads without locking (race per Gemini review), and (b) a persistent `cap` that isn't released before `_cv_capture` fallback (file-descriptor contention). | `docs/camera-review.md` §3 | Flagged in prior review, not yet fixed. Verify still present. |
| A6 | Z is always 0 (floor), scale Z is always 1700 (person height). Hardcoded across tracker + orchestrator. | `tracker.py _orch_create_temporal`, `parent_server.py:7230` | Wrong for non-person classes (cat, chair, bicycle) and for cameras aiming at raised surfaces. |

### Job B — mover-calibration

| # | Finding | Evidence | Impact |
|---|---------|----------|--------|
| B1 | Three calibration modes co-exist (legacy, v2, markers) with overlapping phases and no clear deprecation. | `parent_server.py _mover_cal_thread_body`, `_mover_cal_thread_v2_body`, `_mover_cal_thread_markers_body` | Operator confusion; markers is the operator-preferred path per `feedback_cal_algorithm.md` but legacy is still the default. UI ranking is not live-tested. |
| B2 | Homography is stored in **two places** (`_calibrations[fid].matrix` and `fixture.homography`) because v2 pre-check reads one and other consumers read the other. | `parent_server.py:2761-2781` | Any future writer has to remember to write both; easy to go stale. |
| B3 | solvePnP path still runs even though the direct `cv2.findHomography` path is always preferred for coplanar floor markers (per `feedback_stage_map_coplanar.md`). The solvePnP result is exposed as `cameraPosition` for "operator sanity check" but is known to be unreliable (mirror ambiguity, `z=-58` vs true `z=1920`). | `parent_server.py:2695-2721` | Clutter + misleading field. Decide: trust homography only, drop PnP, OR compute PnP only with non-coplanar markers. |
| B4 | Legacy v1 helpers (`affine_pan_tilt`, `affine_stage_point`, `pan_tilt_to_ray`, `range_calibrations`) still exist and are called by `bake_engine`, `structured_light`, calibrator wizard, etc. Per `project_calibration_v2_phase1.md`, they are "legacy fallback — do NOT delete yet". | `mover_calibrator.py:2213`, grep `affine_` | Two parallel IK stacks. Risk of diverging behaviour; hard to reason about "which model aimed this beam". |
| B5 | `ParametricFixtureModel` has a real mirror symmetry — different `(pan_sign, tilt_sign)` combinations give identical forward output at sampled points but diverge outside the sample hull (#488 discussion, `feedback_parametric_mirror_ambiguity.md`). LM fits all 4 combos and picks best RMS, but ties are broken by convention `(+1, -1)` within 0.2°. | `parametric_mover.py _lm_solve`, `fit_model` | Fits can flip between sessions; extrapolation outside the marker hull can be wrong direction. Need a canonical way to verify sign choice — probably "does the beam go the expected direction when we nudge pan?" during the cal itself. |
| B6 | Duplicated numerical-Jacobian logic (`_lm_solve` vs `_condition_number` in `parametric_mover.py`). | `parametric_mover.py:249,388` | Maintenance hazard, not a bug today. |
| B7 | Beam detection tolerates hybrid RGB+wheel fixtures via `_beam_detect_flash` (ON/OFF diff) per `feedback_cal_algorithm.md`. Good. But `beam_detector.detect_center()` assumes horizontally-arranged multi-beams (median-X sort) — fails for a fixture rotated 90°. | `docs/camera-review.md` §4, `beam_detector.py` | 350 W BeamLight is single-beam so unaffected; verify for any future multi-beam fixture. |
| B8 | Homography extrapolates badly outside the fit markers' convex hull (`feedback_stage_map_coplanar.md`). On the basement rig, all three markers cluster on the right-back half of the stage — the left-front half has no anchor. v2 target-driven cal trusts the homography wherever the target is placed. | ibid. | Front-of-stage targets are the primary auto-track zone → precisely where the homography is worst. The operator has to be told that surveyed markers must span the target volume. |
| B9 | `fovType` field is silently dropped by the fixture PUT (#611 open). FOV values from consumer spec sheets are usually **diagonal**, not horizontal — the `_pixel_to_stage` fallback assumes the `fovDeg` is horizontal. | `project_basement_rig.md` | Ground-plane fallback (when no homography) is off by the cos factor. Tracking without calibration = wrong placement. |
| B10 | `_invalidate_mover_model(fid)` must fire on every `_mover_cal` mutation. Easy to miss a call site → stale cached parametric model served for aim. | `project_calibration_v2_phase1.md` | Not observed misbehaving; worth a grep-audit. |

### Cross-cutting

| # | Finding | Impact |
|---|---------|--------|
| X1 | Per-camera tracking config (`trackClasses`, `trackFps`, `trackThreshold`, `trackTtl`, `trackReidMm`) is forwarded to the node and also stored on the fixture — but the *camera* is the fixture (each sensor = one fixture per `feedback_camera_per_sensor.md`). The per-camera-per-class relationship is correct; verify no old "first camera wins" assumption lingers. | Potentially wrong tracking config on multi-camera nodes. |
| X2 | `project_calibration_architecture_20260409.md` promises "everything in one coordinate system" — but the point-cloud pipeline still has floor-normalisation code and a Z-up vs Y-up ambiguity per Gemini review. Objects-placed-from-point-cloud may not round-trip. | Stage objects may be misplaced in 3D viewport but correct in DMX. |
| X3 | No end-to-end test proves "detected-pixel → placed stage object → auto-track pan/tilt → beam lands on person" as a single pipeline. Each piece has unit coverage, the composition is validated only by the live-test sessions. | Regressions in one stage surface only at the next live test. |

---

## 5. Review questions

The review exists to answer these. Every recommendation must cite which
question(s) it answers.

### 5.1 Job A — tracking

1. **Is the current simple-proportional pixel→stage mapping in
   `api_objects_temporal_create` acceptable for ANY real install, or does it
   need to be replaced before further auto-track work?** (→ A1, A2)
2. **Should tracking always require a stage-map homography (hard gate), or
   should there be a calibrated fallback using camera position + rotation +
   FOV?** (→ A2, B9)
3. **What's the right fusion policy when two cameras see the same person —
   prefer higher-confidence, weighted average, or first-come/TTL?** (→ A3)
4. **Do we need per-class height defaults or a height estimator (bbox height
   in pixels → ground contact point)?** (→ A6) — impacts non-person classes
   and accurately placing the ground contact for mover aim.
5. **How does tracking degrade gracefully when the camera has no stage-map
   yet?** Placeholder? Refuse to start? Warning banner?

### 5.2 Job B — mover-calibration

6. **Which of the three cal modes (legacy, v2, markers) should be the
   default, and which (if any) can be deprecated?** (→ B1) — per
   `feedback_cal_algorithm.md` markers is operator-preferred; decide the path
   to deprecation and the UI copy. Live-test each before declaring a default.
7. **Can we collapse `_calibrations[fid].matrix` and `fixture.homography` to
   a single source of truth, and how do we migrate existing records?** (→ B2)
8. **Drop solvePnP path entirely, keep as diagnostic, or only run when
   markers are NOT all coplanar?** (→ B3)
9. **Plan to retire the v1 legacy helpers** (`affine_pan_tilt`, `pan_tilt_to_ray`,
   range-cal records) without breaking `bake_engine`, `structured_light`, etc.
   (→ B4)
10. **How do we lock down the LM sign ambiguity** — a confirmation probe
    during cal ("nudge pan +0.02, verify pixel moves in expected direction
    given the fitted signs, else flip and refit")? (→ B5)
11. **Operator guidance on marker placement.** The hull-extrapolation problem
    (B8) is not a code bug; it's a procedural one. Do we need a simple
    pre-cal screen that shows "your surveyed markers cover 42% of the stage
    — you probably want two more near front-stage-left"? (→ B8)
12. **Fix the dropped `fovType` field and resolve diagonal-vs-horizontal
    FOV convention.** (→ B9, #611)

### 5.3 Both

13. **Do we need a single "camera health" dashboard** showing, per camera:
    intrinsic-cal status, stage-map status (markers matched + RMS), live
    tracking status, last beam-detect success? Today this info is spread
    across Setup / Calibration tabs. Good target for a small focused UI.
14. **Is there an end-to-end regression test we could add** that runs the
    full pipeline with mocked frames? (tracking: mock YOLO output → assert
    stage placement; mover cal: mock beam frames → assert fitted model
    within tolerance). (→ X3)

---

## 6. Review method

Each question gets an answer from one of these sources. **No code is modified
until question → method → answer is written down below.**

- **Static reading** (grep, read the function). Fastest; enough for questions
  about whether code paths are reachable, which consumer reads what.
- **Basement-rig live test.** For questions about UX, extrapolation, multi-
  camera fusion, and marker-placement guidance. Capture screenshots and
  numeric errors into `/mnt/d/temp/live-test-session/` per
  `feedback_screenshot_folder.md`.
- **Synthetic unit test.** For algorithmic questions (LM sign convergence,
  homography extrapolation error vs marker spread) — no hardware required.
- **Existing memory/docs.** Check `feedback_cal_algorithm.md`,
  `project_live_test_*`, `docs/mover-calibration-v2.md` before deriving from
  first principles.

### 6.1 Instrumentation we'll add (read-only, in review)

Temporary, review-only additions (revert after):

- Log every call to `api_objects_temporal_create` with (pixel, camera rotation,
  chosen conversion path, resulting stage mm). Visible in server log.
- A `/api/debug/camera/<fid>/pixel-at` GET endpoint that takes `u,v` in [0,1]
  and returns the four candidate stage mm values (simple-proportional,
  homography, FOV-projection, 3D-ray via `_pixel_to_stage`). Lets the operator
  click a marker's known pixel and see disagreement directly.
- Optional: store last 100 YOLO detections per camera with timestamps — for
  offline replay during review.

### 6.2 Live-test checklist (basement rig)

Run once after instrumentation is in, before any fixes:

1. Power up rig, deploy latest firmware, start orchestrator.
2. Stage-map both cameras — record `markersMatched`, `rmsError`, and the
   persisted homography matrices.
3. Place a known object (tripod marker) at five stage positions covering
   near/mid/far and left/right. For each position, capture the reported
   temporal-object pos and compute error in mm.
4. With only one camera enabled, fire a beam at each ArUco marker (manual
   aim via console). Record detected beam pixel, homography-mapped stage
   coord, and surveyed marker stage coord.
5. Repeat (4) with the other camera.
6. Run markers-mode mover cal on MH1 and on the 350 W. Record per-marker
   residuals, final fit RMS, and whether the fit sign combo matches the
   operator-observed axis directions.
7. Enable both cameras for tracking; walk a defined path; log the stream
   of temporal-object IDs. Count "ghost" objects (one person → N IDs).
8. Auto-track a mover onto the walking person. Record beam-to-person error
   at five path points.

Target tolerances (subject to review):
- Tracking placement: ≤ 300 mm error near centre, ≤ 600 mm near frame edges.
- Mover calibration residual: ≤ 100 mm RMS on floor markers, ≤ 30° beam-to-
  target when aimed in-hull.
- No ghost-object events in a 30-second single-person walk.

---

## 7. Deliverables

At the end of the review, before any code changes, we produce:

1. **Findings table** — each question from §5 answered with an outcome
   (`no-change`, `code-fix`, `ux-fix`, `doc-fix`, `defer`) and a short
   justification. Appended to this document as §8.
2. **Prioritised fix list** with GitHub issues created, labelled
   `calibration-review-2026-04-22`. Ranked P1 / P2 / P3. Cite the question
   each fix closes.
3. **Recommended default for mover cal mode** (legacy vs v2 vs markers)
   based on live-test results, with a one-paragraph UI copy update.
4. **Single-source-of-truth decision** for homography storage (B2) and
   migration plan.
5. **Deprecation schedule** for v1 legacy helpers (B4) — which callers
   migrate first, which can stay forever.
6. **Test plan** for the end-to-end regression test (X3) — not the test
   itself yet, but what it covers and what fixtures it'd replace.

All deliverables land either in this file (§8+) or as issues in the
`SlyWombat/SlyLED` repo. **No code edits during the review phase.**

---

## 8. Findings (to be filled in during review)

_Empty — will be populated as each question is answered._

---

## 9. Out of scope (for this review)

- Depth-Anything-V2 accuracy and point-cloud scaling. `docs/pointcloud.md`
  and the Gemini review cover this; re-opens only if it turns out to be
  on the critical path for one of the two primary jobs.
- Structured-light / beam-as-laser 3D refinement (#236). Parked until the
  primary pipelines are healthy.
- Intrinsic camera calibration (lens distortion). The EMEET 4K's bundled
  intrinsics are "good enough" per the 2026-04-17 session; revisit only
  if mover-cal residuals exceed target after other fixes.
- Camera-node firmware security/auth hardening. Tracked by Gemini's P2 but
  unrelated to the two primary jobs; separate issue.
- Android gyro stream → mover control. Separate workstream (`#474-477`).

---

## 10. Change log

- **2026-04-22** — initial draft.
