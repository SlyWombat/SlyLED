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
- **No new dependencies without justification.** numpy + cv2 are in. scipy can be used if needed
  (hand-roll LM, per `project_calibration_v2_phase1.md`).
- **No backward compatibility required.** This is the first beta release —
  there are no shipped customers, no saved projects in the wild to preserve,
  and no on-disk schema to migrate. Prefer the clean breaking change over a
  compat shim. Fixes in §8 drop the word "migration" wherever it appeared in
  the first draft: stale fields can simply be deleted, reused keys renamed in
  place, and default JSON files regenerated from scratch.

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

## 8. Findings

### 8.1 Static-reading round (2026-04-22)

Six questions closed by grep/read only — no hardware required. Each cites the
evidence line numbers against the snapshot in §3 / §4.

#### Q1 — pixel→stage mapping in the tracker-ingest endpoint → **code-fix**

**Answer:** The current mapping is *not* acceptable for any real install. It
must be replaced before further auto-track work, but the fix is trivial because
the correct helper already exists and is used elsewhere.

Evidence (`desktop/shared/parent_server.py`):
- `api_objects_temporal_create` at **7206–7232** runs exactly the back-wall
  proportional `pos = [sw·(1-cx), sd·(1-cy), 0]`. It ignores camera position,
  rotation, FOV, and any persisted homography. Z hard-coded 0, height 1700,
  depth 400.
- `_pixel_to_stage` at **1888** already does the right thing: homography
  first (`_calibrations[fid].matrix`), ground-plane projection fallback using
  `_rotation_to_aim` + `fovDeg`. Identical helper chain that `/api/cameras/<id>/scan`
  uses at **2027**.
- Confirms A1 and A2 — the ingest path was written before `_pixel_to_stage`
  was generalised, and the wire-up was simply missed.

**Fix shape:** Replace the inline block at 7218–7232 with a single call to
`_pixel_to_stage([{x,y,w,h,label,confidence}], cam_fixture, fw, fh)[0]`,
using its returned stage coords. Keep the `cameraId`/`pixelBox`/`frameSize`
contract on the wire. Ticket: **P1**, citations Q1, Q2, A1, A2.

#### Q2 — calibrated fallback vs hard-gate homography → **code-fix (merges with Q1)**

**Answer:** A calibrated fallback already exists inside `_pixel_to_stage`
(homography-or-FOV-projection). Once Q1 is wired, tracking degrades
gracefully: homography when surveyed, FOV ground-plane otherwise, raw
pixel passthrough only when the camera has no stage position at all
(line **1917** — `dist < 1`). Therefore the hard-gate option is
unnecessary. Keep a UI warning when `_calibrations.get(str(fid))` is
absent so operators know they're on the weaker fallback, but don't
refuse to start.

Depends on Q12 (FOV convention) — the fallback is currently wrong by a
`cos(diagonal/2)/cos(horizontal/2)` factor because `_pixel_to_stage`
treats `fovDeg` as horizontal and most consumer spec sheets publish
diagonal.

Also depends on Q4 — the ingest path now needs to return **two** stage
points per detection (feet + head) rather than a single center, so the
Q1 fix must land Q4's ground-contact + height inference at the same
time.

#### Q4 — per-class height / ground-contact estimator → **code-fix (lands with Q1)**

**Answer:** Yes, we need per-person height. Tracked auto-aim must be
able to target **feet**, **center**, or **head** per action — e.g. a
"follow-spot" action aims at the head, while a "footlight" or
"hot-spot" action aims at the ground-contact point. A single fixed
`z = 1700` is insufficient.

**Approach (geometric, uses the calibrated camera Q1 already restores):**

1. **Feet (authoritative ground point).** Project the bottom-center of
   the YOLO bbox through the camera to the floor plane `z = 0` using
   the homography path (when available) or the FOV ray fallback. This
   is the stage-space point the mover aims at for "feet".
2. **Head (inferred from bbox top).** Project the top-center pixel as
   a ray from the camera; intersect that ray with the vertical line
   through the feet point (same `x`, `y`, varying `z`). Solve for `z`.
   That's the head position *and* gives us the person's height for
   free. Works for any upright object; falls back sensibly if the ray
   is near-parallel to the vertical.
3. **Center.** `(feet + head) / 2`, computed on demand.

**Per-class fallback (used when no homography AND camera pose
untrusted):** static table of default heights —
`{person: 1700, child: 1100, cat: 300, dog: 600, chair: 900,
bicycle: 1100, suitcase: 500}` etc. Only a fallback; geometric
inference is preferred whenever the camera is stage-mapped.

**Data-model changes to `_temporal_objects[n]`:**
- `transform.pos` — **feet** (x, y, z=0). Canonical aim-target for
  "floor" actions and for the 3D renderer's ground footprint.
- `transform.scale[1]` — height in mm (was hard-coded 1700).
- New optional `_headPos` — `[x, y, z_head]` stored alongside pos when
  the geometric inference succeeded. Missing → auto-track falls back
  to `pos + [0, 0, scale[1]]`.

**Action-model change (`/api/actions` + track-action evaluator):**
- Add `aimTarget` enum to each track-action: `"feet" | "center" |
  "head"` (default `"center"`).
- `_evaluate_track_actions` at `parent_server.py:10179` resolves
  `aimTarget` → stage XYZ from the object's `pos` / `_headPos` /
  midpoint, then passes that to the existing mover-aim math.

**Non-goal for this ticket:** bbox-height-in-pixels → real-world
height purely from the detector (no geometry). It's tempting but
depends on subject-to-camera distance which we don't have without the
homography anyway. Skip.

Ticket: **P1** (ships with Q1). Closes Q4 + A6.

#### Q7 — homography storage: one source of truth → **code-fix**

**Answer:** Collapse to `_calibrations[str(fid)]["matrix"]` as the sole
writer and reader. There are currently **three** names for the same
matrix and one of them is dead code:

1. `_calibrations[str(fid)]["matrix"]` — written at **2761**, read by
   `_pixel_to_stage` at **1895**.
2. `fixture["homography"]` — written at **2777** (mirror), read at
   **3553** as fallback in `_mover_cal_thread_v2_body`.
3. `_calibrated_cameras` — referenced at **3546** with a
   `if "_calibrated_cameras" in globals()` guard. `grep` shows the
   symbol is **never defined** anywhere in `parent_server.py`. The
   guard always evaluates False, so `cam_cal` is always None and the
   line exists as pure dead code. Origin appears to be an abandoned
   refactor noted in the comment at **2757**.

Breaking change (beta — no compat shim per §2):
- Remove the `fixture["homography"]` write at 2777 and the fallback
  read at 3553 outright. Any existing `fixture.homography` field in a
  saved layout is simply dropped on next load.
- Delete the `_calibrated_cameras` branch at 3546 entirely.
- Update the v2 pre-check comment block at 2755–2777 to reflect the
  single-store policy.
- Operators re-run stage-map calibration once on upgrade; result lands
  straight into `_calibrations[str(fid)].matrix`.

Ticket: **P1** (small, mechanical, covered by `test_parent.py`). B2.

#### Q8 — solvePnP path in stage-map → **code-fix (demote to diagnostic)**

**Answer:** Keep `cv2.findHomography` as the canonical output; downgrade
`solvePnP` to diagnostic-only, and stop exposing its derived
`cameraPosition` as a primary field.

Evidence at **2663–2721**:
- solvePnP runs first (lines 2663–2690) purely to derive `cam_pos` for
  "operator sanity check" display.
- The exported homography is unconditionally overwritten by the direct
  `cv2.findHomography(img_pts, stage_pts_xy)` result at **2715–2717**.
  The pose-derived fallback at **2720–2721** only runs inside an
  `except` — it's unreachable in practice for clean ArUco corners.
- Comments at 2698–2710 explicitly state that the direct homography is
  "strictly better" for coplanar floor markers; the code agrees.

So solvePnP is already second-class; the confusion is in the response
payload, not the math. Fix:
- Rename `cameraPosition` / `cameraPosStage` keys to
  `cameraPositionDiagnostic` (or nest under `diagnostics.pnp`) so SPA
  consumers stop treating it as authoritative.
- Keep running solvePnP (cheap) and report its reprojection error
  alongside the findHomography RMS. If the two disagree by more than
  ~2× expected, surface a warning — it's the best we have for catching
  mirror-pose ambiguity.
- Drop the stale "strategy" comment at 2621; the code no longer has
  parallel paths the way the comment implies.

Ticket: **P2**. B3.

#### Q9 — retire v1 legacy helpers → **defer with deprecation schedule**

**Answer:** Cannot delete yet. The legacy helpers have **seven** live
callers across three files. Retirement is a multi-PR project.

Live callers (from grep):

| Symbol | Callers |
|--------|---------|
| `affine_pan_tilt` | `mover_control.py:363`, `bake_engine.py:672`, `parent_server.py:4502, 8606, 8610, 10301` |
| `affine_stage_point` | `parent_server.py:8305` |
| `pan_tilt_to_ray` | `structured_light.py:55`, `parent_server.py:7911` (import), `8320`, `9464` |
| `_range_cal` / `range_calibrations` | `parent_server.py:236` (load), `2903`, `12034`, `12581` (saves) |

Proposed phased retirement (create as sub-issues under one epic):

1. **Phase 1 — `mover_control.aim_to_pan_tilt`** (user-facing aim path).
   It already prefers `ParametricFixtureModel` when `_mover_models[fid]`
   is fit; the `affine_pan_tilt` call at `mover_control.py:363` is the
   last-resort fallback. Convert the fallback to emit a warning when
   used, so we can measure real-world hit rate.
2. **Phase 2 — `bake_engine`** (offline, so cheap to test). Swap to
   `ParametricFixtureModel` with per-fixture cached model. Regression
   test: `test_timeline_bake.py`.
3. **Phase 3 — `parent_server` debug/preview routes** (`8305, 8606,
   10301`). These back SPA "where will this aim land?" previews. The
   v2 model's `inverse` already covers this; port and delete the imports.
4. **Phase 4 — `structured_light`** (#236 is already parked out-of-scope
   per §9). Do not touch until #236 unparks.
5. **Phase 5 — `_range_cal` record deletion.** Only after phases 1–4
   confirm no live reads. Per §2 (beta, no compat), skip the sample-
   migration step — just delete `range_calibrations.json`, the loader
   at `parent_server.py:236`, and the four save sites (2903, 12034,
   12581). Operators re-run range cal once. B4.

Since there is no production data to preserve, Phase 1's
"warn-but-keep" fallback can instead be a hard removal — measure hit
rate only if it turns out we're still calling `affine_pan_tilt` from
somewhere we didn't catch in grep.

Ticket: **P3 (epic)**, six sub-tickets. Do not start until Q7 lands.

#### Q12 — `fovType` dropped by fixture PUT → **code-fix (and doc-fix)**

**Answer:** Two independent bugs confirmed:

1. **`fovType` is silently dropped.** The PUT whitelist at
   `parent_server.py:1451–1458` lists `"fovDeg"` but **not** `"fovType"`.
   Any client that sends `fovType` in the body has it ignored. This is
   #611 as filed.

2. **Default inconsistency across consumers.** `fovType` default
   varies by reader:
   - `parent_server.py:5235` — defaults to **`"diagonal"`**
     (`_aruco_stage_map_simple`).
   - `parent_server.py:5913, 5916, 5950` — default **`"horizontal"`**
     (stereo/triangulation path).
   - `_pixel_to_stage` at **1888–1997** uses `fovDeg` directly as
     horizontal half-angle — ignores `fovType` entirely.

   For the basement rig the EMEET 4K spec sheet publishes 90° as
   *diagonal*. A horizontal-default consumer treating 90° as horizontal
   overestimates horizontal FOV by cos(diag/2)/cos(horiz/2) ≈ 12–15%.
   That's the cos-factor error flagged in B9.

**Fix shape:**
- Add `"fovType"` to the whitelist at 1454.
- Validate: must be one of `{"diagonal", "horizontal", "vertical"}`.
- Unify default to **`"diagonal"`** (consumer spec sheets use diagonal).
- Make `_pixel_to_stage` read `fovType` and convert to horizontal
  half-angle internally via `camera_math` (add `normalise_fov(fov_deg,
  fov_type, aspect)` if it doesn't exist yet).
- Update `docs/camera.md` and `project_basement_rig.md` to reflect
  "diagonal is the canonical input; everything else is derived".

Ticket: **P1** (ships with Q1/Q2; Q2 fallback accuracy depends on it).
Closes #611 + B9.

#### Q10 — LM sign-confirmation probe → **code-fix + new unit test**

**Answer:** Add an active **one-nudge probe** at the end of every
mover-cal run that empirically confirms `(pan_sign, tilt_sign)` against
observed beam motion, and refits if the signs disagree. Kill the
`<0.2° RMS` convention tie-break at `parametric_mover.py:378–384` —
it's a heuristic and the probe replaces it with a measurement.

**Root cause (recap of B5):**
- `fit_model` at **316–385** runs LM over the 5 continuous parameters
  for every `(pan_sign, tilt_sign) ∈ {±1}²` combination.
- On sparse samples (markers-mode often uses 3 floor points per rig),
  two or more combos converge to near-identical angular RMS because a
  180° yaw flip absorbs the sign flip — it's a genuine symmetry of
  the forward map *on the fit points*, but **the symmetry breaks
  outside the sample hull**. So the convention tie-break picks a fit
  that looks identical at the markers but aims the wrong way when
  extrapolating to front-stage.

**Probe design — one helper, called once per cal:**

```python
# parametric_mover.py (new)
def verify_signs(model: ParametricFixtureModel,
                 nudge_probe: Callable[[float, float], Optional[Tuple[float, float]]],
                 base_aim_norm: Tuple[float, float],
                 nudge_norm: float = 0.005,
                 ) -> Tuple[int, int, float]:
    """Return (pan_sign, tilt_sign, confidence_deg).

    nudge_probe(pan_norm, tilt_norm) drives the fixture and returns the
    observed beam pixel (or None on miss). Typically wired up to:
      bridge.set_pan_tilt(); sleep; camera.beam_detect().

    Algorithm:
      1. Capture pixel p0 at base_aim.
      2. Capture pixel p_pan at base_aim + (+nudge, 0).
      3. Capture pixel p_tilt at base_aim + (0, +nudge).
      4. Predict expected pixel deltas from model.forward() projected
         through the camera's homography.
      5. Dot product sign of (predicted, observed) tells us whether
         each axis sign is correct.
      6. Return the signs plus a confidence = min angle between
         predicted and observed deltas (small = trustworthy).
    """
```

Integration in `mover_calibrator.py` (end of every `_mover_cal_thread_*_body`):

```python
fitted, quality = parametric_mover.fit_model(...)
# Use the camera we already used for cal; re-use beam detector.
def _probe(pn, tn):
    engine.set_pan_tilt(fixture, pn, tn)
    time.sleep(0.15)  # fixture settle
    spots = beam_detector.detect(camera_snapshot(cam))
    return (spots[0]["x"], spots[0]["y"]) if spots else None

# Probe at the centroid of the samples so the beam is guaranteed in-frame.
cx, cy = _sample_centroid(samples)
base = fitted.inverse(cx, cy, 0)
p_sign, t_sign, conf = parametric_mover.verify_signs(fitted, _probe, base)

if (p_sign, t_sign) != (fitted.pan_sign, fitted.tilt_sign):
    log.warning("MOVER-CAL: sign probe disagrees with LM fit — forcing "
                "(%d,%d) and re-running LM", p_sign, t_sign)
    fitted, quality = parametric_mover.fit_model(
        ..., force_signs=(p_sign, t_sign))
```

`fit_model` gets a new `force_signs` kwarg that bypasses the
enumeration — only LM over the 5 continuous params for the given sign
combo.

**Killing the convention tie-break:** lines **378–384** become
`candidates.sort(key=lambda c: c[0]); _, _, best, best_q = candidates[0]`.
The probe — not a convention heuristic — decides ambiguous cases.

**Nudge size:**
- `0.005` normalized pan = **2.7°** on a 540° fixture, projecting to
  **~230 mm** beam displacement at 5 m — comfortably above a 2–5 px
  beam-detector noise floor and well within typical camera FOV.
- Auto-clip: if `base + nudge > 1.0`, use `-nudge`. If
  `base − nudge < 0.0`, use `+nudge`.
- If either probe misses (no beam pixel), fall back to the next-best
  RMS combo from the 4-way enumeration and flag `quality.signProbe
  = "failed"` so the SPA shows a warning.

**Scipy (newly allowed per §2):** does NOT simplify this probe — it's
pure geometry, no optimisation involved. Scipy is still worth using
in `_lm_solve` itself (`scipy.optimize.least_squares(method="lm",
loss="soft_l1")`) because:
1. Built-in robust loss handles occasional beam-flash outliers that
   currently bias the hand-rolled LM.
2. Analytic 3-point Jacobian is faster and more stable than our
   central-differences.
3. Lose ~40 lines of code.
But: that's a **separate P3 ticket** — the probe is the Q10 answer and
ships independently.

**Synthetic unit test (no hardware):**

```python
# tests/test_parametric_sign_probe.py
def test_probe_catches_flipped_pan_sign():
    truth = ParametricFixtureModel(
        fixture_pos=(1500, 0, 3000),
        mount_yaw_deg=30, pan_sign=+1, tilt_sign=-1,
    )
    # 3 samples (mimics markers-mode on basement rig)
    stage_pts = [(500, 2000, 0), (2500, 2000, 0), (1500, 2500, 0)]
    samples = [
        {"pan": truth.inverse(*p)[0], "tilt": truth.inverse(*p)[1],
         "stageX": p[0], "stageY": p[1], "stageZ": p[2]} for p in stage_pts
    ]
    # Force a wrong-sign fit
    bad = ParametricFixtureModel(**{**asdict(truth), "pan_sign": -1,
                                     "mount_yaw_deg": 30 + 180})
    # Synthetic "camera" = truth model + identity homography
    def probe(pn, tn):
        d = truth.forward(pn, tn)  # unit vector
        # project to z=0
        px, py, pz = truth.fixture_pos
        t = -pz / d[2]
        return (px + t * d[0], py + t * d[1])  # pixel ≡ stage in this test
    p_sign, t_sign, _ = verify_signs(bad, probe, base_aim_norm=bad.inverse(1500, 2200, 0))
    assert (p_sign, t_sign) == (+1, -1)

def test_probe_agrees_when_fit_is_correct(): ...
def test_probe_handles_beam_miss_gracefully(): ...
def test_probe_auto_flips_nudge_at_dmx_boundary(): ...
```

**Acceptance criteria:**
- On every saved cal, `mover_calibrations.json[fid].signProbe` stores
  `{"ok": bool, "panConfidenceDeg": float, "tiltConfidenceDeg": float,
  "nudgePx": [dx_pan, dy_pan, dx_tilt, dy_tilt]}`.
- SPA shows a green/amber/red badge on the cal result (green: both
  confidences < 5°; amber: < 15°; red: ≥ 15° or probe missed).
- Synthetic test suite has ≥ 4 cases (flipped pan, flipped tilt,
  both flipped, correct).

Ticket: **P2** (isolated, self-contained, no upstream deps). B5, Q10.

#### Q5 — graceful degradation for unmapped camera → **code-fix (rides with Q1) + ux-fix**

**Answer:** Three-tier transparent degradation; never refuse to start a
show. The operator always sees the best placement we can compute plus
a clear indicator of which tier is active.

**Tier stack (already implemented at `_pixel_to_stage`, activated once Q1 wires it into the ingest path):**

| Tier | Requires | Expected accuracy | Code path |
|------|----------|-------------------|-----------|
| `homography` | Surveyed ArUco stage-map in `_calibrations[fid].matrix` | ±50–150 mm inside hull, degrades outside | `_pixel_to_stage_homography` at 1859 |
| `fov-projection` | Camera position + rotation + `fovDeg` + `fovType` (post-Q12) | ±300–800 mm at typical stage distances | `_pixel_to_stage` fallback at 1888–1997 |
| `raw` | Nothing; `dist < 1` in the projection loop | unusable for auto-track; shown "as-is" in 3D viewport | line 1917 |

**Backend contract additions (ride with the Q1 P1 ticket):**

1. `_pixel_to_stage` returns the method used per detection
   (`"homography" | "fov-projection" | "raw"`).
2. `api_objects_temporal_create` echoes that method in the response
   and stamps it onto the stored object: `_temporal_objects[n]._method`.
3. Auto-track evaluator (`_evaluate_track_actions` at 10179) reads
   `_method` and applies per-tier behaviour:
   - `homography` — normal operation.
   - `fov-projection` — normal operation but log a throttled warning
     (once per 10 s per fixture) with the estimated accuracy.
   - `raw` — **skip the aim update**; the fixture holds its last
     good position. Prevents random slews from placing movers at
     arbitrary spots when a camera ghost-detects. SPA shows the badge
     red, operator knows to run stage-map.
4. New GET endpoint `/api/cameras/<fid>/calibration-status` returns:
   ```json
   {
     "tier": "homography" | "fov-projection" | "raw",
     "stageMap": {"matchedIds": [...], "rmsError": 12.3, "ageMinutes": 45},
     "position": {"ok": true, "x": 1500, "y": 120, "z": 1920},
     "rotation": {"ok": true, "tilt": 30, "pan": 0, "roll": 0},
     "fov": {"deg": 90, "type": "diagonal"},
     "estimatedAccuracyMm": 150
   }
   ```
   Feeds Q13's camera-health dashboard later; standalone endpoint now
   so Setup-tab badges can call it without waiting on Q13.

**Minimal UX (not the full Q13 dashboard — that's separate):**

- **Setup-tab camera card** — single badge next to camera name:
  🟢 Calibrated / 🟡 FOV fallback / 🔴 Not calibrated. Click →
  opens Calibration tab pre-scrolled to this camera's stage-map
  section.
- **Runtime-tab Dashboard** — persistent amber banner if any active
  camera is on tier `fov-projection`, red banner if any is on `raw`.
  Dismissible for the session (`sessionStorage`), re-appears on
  reload.
- **Track-action editor** (`/api/actions` modal) — show
  "Tracking accuracy: ~±150 mm (calibrated)" or
  "~±600 mm (FOV fallback)" next to the camera picker, computed
  from the tier and the stage depth. Uses same endpoint above.

**What we explicitly do NOT do:**
- Refuse to start the show when calibration is missing — operators
  routinely tour venues where stage-map was never run; "run the show
  anyway in 3D-preview mode" is required.
- Silently substitute a default fixture for an uncalibrated mover.
- Modal-block the SPA on startup if cameras are uncalibrated. It's
  a banner, not a gate.

**Depends on:** Q1 ingest wiring (returns the method), Q12 fovType
(feeds the tier's accuracy estimate).

Ticket split:
- **P1** — backend additions (three lines in the Q1 patch to stamp
  `_method`; one new GET endpoint; one new branch in
  `_evaluate_track_actions`). Rolls into the Q1 P1 ticket.
- **P2** — SPA badges + banners + track-action editor accuracy hint.
  Small, self-contained, ships after Q1/Q12 backend.

Closes Q5.

#### Q3 — multi-camera fusion policy → **code-fix (post-Q1)**

**Answer:** Hybrid weighted-cluster policy. Live-test step 7 confirms
the constants; the structure can be designed and code-reviewed now.

**Problem (A3 recap):** Each camera independently posts temporal
objects through `api_objects_temporal_create`. Two cameras seeing one
person create two objects; tracker auto-aim then fights itself or
slews back-and-forth.

**Three candidate policies + trade-offs:**

| Policy | Pro | Con |
|--------|-----|-----|
| Highest-confidence wins | Trivial | Jitters at handoff zones when both YOLO scores are similar |
| Weighted average | Smooth | Needs per-camera accuracy weight (we have it from Q5) |
| First-camera-owns-TTL | No jitter, deterministic | Owning camera can be wrong when person walks into a zone it doesn't see |

**Recommended: weighted clustering.**

For each ingest call:
1. Compute the detection's stage point (Q1 fix).
2. Compute its weight:
   ```
   w = yolo_conf
       × tier_weight[_method]            # homography 1.0 | fov 0.4 | raw 0.0
       × hull_distance_falloff(stage_pt) # 1.0 inside marker hull,
                                         # 0.2 at FOV edge, linear
   ```
3. Periodic deduplication pass (every `min(ttl)/4` seconds):
   - Cluster all live `_temporal_objects` of the same `objectType`
     within `trackReidMm` (default 500 mm).
   - Each cluster collapses to a single object whose `pos` is
     weight-averaged from its members; the surviving object inherits
     the highest TTL in the cluster.
   - Add a hidden `_camOrigins: [fid, fid, ...]` field for debugging.
4. The auto-track evaluator uses the surviving cluster object — never
   the raw per-camera detections.

**Why this works:**
- A `homography` camera near the person dominates an `fov` camera
  far away — accuracy weight does the right thing.
- A camera looking AT a marker hull edge falls off smoothly; no
  cliff-edge handoff.
- `raw` tier contributes weight 0 → its detections never poison the
  cluster (consistent with Q5's "skip aim update on raw").
- Code lives entirely in `parent_server.py`; `tracker.py` on the
  Orange Pi continues to post per-camera detections — fusion is the
  orchestrator's job.

**Live-test step 7 measures:**
- Ghost-object count per 30 s walk (target 0).
- Handoff smoothness — pos-delta between consecutive cluster updates
  when a person crosses between camera coverage zones (target
  < 200 mm).
- The two constants worth tuning live: `tier_weight["fov"]` (default
  0.4) and the hull-falloff slope.

Ticket: **P2** (depends on Q1 backend landing first; clean code split).
Closes Q3, A3.

#### Q11 — marker-coverage pre-cal UX → **ux-fix (#612 is build ticket)**

**Answer:** A simple top-down overlay on the existing Layout viewport,
rendered in the Calibration tab's stage-map section. No new graphics
engine — reuse `scene-3d.js` orthographic top-view.

**Visual elements (per camera):**

1. **Stage rectangle** — full `(stage_w × stage_d)` mm, grey fill.
2. **Marker dots** — surveyed ArUco positions from `_aruco_markers`.
3. **Convex hull polygon** — yellow outline around the markers, with
   "X% stage covered" badge (`hull_area / stage_area × 100`).
4. **Per-camera FOV cone** — projected onto the floor plane via
   `_pixel_to_stage` corner projection (call it for the four image
   corners). Translucent green where camera sees the floor.
5. **Coverage intersection** — region where (FOV cone) ∩ (marker
   hull). This is the "trustworthy" zone for that camera. Solid
   green fill.
6. **Recommendation pin** — drop a magenta pin at the centroid of
   the largest uncovered region inside the camera's FOV cone.
   Tooltip: *"Place a marker here to expand coverage by ~ N%."*

**Interactions:**
- Click a marker dot → opens its edit modal (#596 marker registry).
- Drag a phantom marker into the viewport → live-update the hull and
  coverage-percent. Useful before the operator surveys the new pose.
- Per-camera toggle in a sidebar lets operator see one camera at a
  time or all overlaid.

**Backend additions:**
- `GET /api/cameras/<fid>/coverage` returns:
  ```json
  {
    "fovCornersStage": [[x,y], [x,y], [x,y], [x,y]],
    "markerHullArea": 1.2e6,
    "stageArea": 4.5e6,
    "coveragePct": 27,
    "recommendedMarkerStage": [1800, 800]
  }
  ```
- Pure derived data (no persistence); recompute on every call.

**No-go alternatives:**
- 3D viewport overlays — looks cool, but operator decisions are 2D
  ("where on the floor"). Top-down is faster to read.
- Auto-running coverage analysis on every layout edit — wait for
  user to open the Calibration tab; cheap to compute on demand.

**Build ticket already exists:** #612. Q11's spec belongs in #612's
description; close #612 when the SPA piece ships.

Ticket: **P2**. Closes Q11, B8 (operator guidance on marker placement).

#### Q13 — camera-health dashboard → **ux-fix (composes Q5 endpoint)**

**Answer:** Single panel showing all cameras side-by-side, reading
the per-camera endpoint Q5 already specifies. Lives at the top of
the Calibration tab; no new tab.

**Per-camera card contents:**

```
┌─────────────────────────────────────────────────────┐
│ EMEET-1  [🟢 calibrated]            [Run beam test] │
│                                                     │
│ Stage map:  3 markers · 12 mm RMS · 45 min ago     │
│ Position:   (1500, 120, 1920) mm                    │
│ Rotation:   tilt 30° pan 0° roll 0°                 │
│ FOV:        90° diagonal                            │
│ Tracking:   2.1 fps · last detection 0.3 s ago      │
│ Last beam:  detected 12 min ago                     │
│                                                     │
│ Estimated tracking accuracy: ±150 mm                │
└─────────────────────────────────────────────────────┘
```

Three new endpoint-level fields (extend the Q5
`/api/cameras/<fid>/calibration-status` payload):

```json
{
  "tracking": {"fps": 2.1, "lastDetectionSec": 0.3,
               "frameCount": 1247, "errorCount": 3},
  "lastBeamDetect": {"timestamp": ..., "ok": true,
                     "centerPx": [320, 180]}
}
```

`tracking.fps` and `lastBeamDetect` come from existing per-camera
state on the Orange Pi (`tracker.py` already counts frames; beam
detect history can be a 1-slot LRU).

**Aggregate banner at top of panel:** "3 cameras: 🟢 2 calibrated,
🟡 1 FOV fallback" — same logic as the Runtime banner from Q5.

**Refresh strategy:** 5 s poll while tab visible (`document.
visibilityState === "visible"`); pause when hidden. Each card has
its own AbortController so tab-switch cancels in-flight requests.

**No-go alternatives:**
- WebSocket push of live tracking stats — defer; 5 s poll is fine
  for a configuration screen, and we don't need new transport for
  this. (Issue #2 is the WebSocket ticket; not a Q13 dependency.)
- Dedicated "Cameras" tab — adds nav clutter for what's a 200-line
  panel.

Ticket: **P3**. Depends on Q5 backend + Q11 sharing the layout
viewport idiom. Closes Q13.

#### Q14 — end-to-end regression test → **test-fix (slots into #533/#409/#277/#280)**

**Answer:** Two synthetic-pipeline tests, both pure-Python, no
hardware, ride on the existing weekly Playwright runner (#280 infra,
#277 epic). Q14 produces the **scope spec**; the implementation
ticket is **#409** (already filed for the tracking half).

**Test 1 — tracking pipeline (extends #409):**

```
Mock YOLO output → tracker.py post → orchestrator ingest
  → temporal object → auto-track action → mover DMX value
```

Steps:
1. Spin up parent server with factory-reset state + a fixture set
   from `tests/regression/fixtures/`.
2. POST a hand-crafted detection payload to
   `/api/objects/temporal` (bypasses the camera node entirely;
   simulates what `tracker.py` would post).
3. Assert: `/api/objects` returns the temporal object at the
   expected stage position (within 50 mm for homography case;
   within 500 mm for fov-fallback case).
4. Trigger an action with `aimTarget="head"`; assert the mover's
   pan/tilt DMX values match expected within 0.5°.
5. Repeat for: walking-line trajectory, two-cameras-one-person
   (Q3 fusion), unmapped camera (Q5 raw tier).

Expected ~150 lines, lives at `tests/regression/test_track_pipeline.py`.

**Test 2 — mover-cal pipeline (extends #533):**

```
Synthetic ArUco frames + synthetic beam frames → camera_server
  endpoint stubs → mover_calibrator → fitted ParametricFixtureModel
```

Steps:
1. Generate 9 synthetic 640×480 frames containing ArUco markers at
   known stage positions (use `cv2.aruco.drawMarker`); compute the
   ground-truth homography.
2. Generate 6 synthetic beam-spot frames at known DMX (pan, tilt)
   pairs (white circle at the projected pixel; the projection uses
   the truth model from Q10's test).
3. Monkey-patch `_camera_snapshot` and `_beam_detect_flash` to
   serve these frames in sequence.
4. Run `_mover_cal_thread_markers_body` end-to-end.
5. Assert: fitted model's `forward()` at any sample DMX is within
   1.5° angular error of the truth model.
6. Assert: `signProbe.ok == true` (Q10).

Expected ~200 lines, lives at `tests/regression/test_mover_cal_pipeline.py`.

**Ride-on changes:**
- Add `tests/regression/fixtures/` with the canonical fixture set
  used by both tests (3 cameras, 3 movers, 3 markers — mirrors the
  basement rig).
- `tests/regression/run_all.py` (already exists per CLAUDE.md) gets
  the two new entries.

**Cross-references** (per §10.3):
- #533 — parent epic; Q14's deliverable lives here.
- #409 — exact mocked-tracking spec, becomes the implementation ticket
  for Test 1.
- #277 — weekly Playwright harness; both tests slot in.
- #280 — runner infra; no changes needed.

Ticket: **P2**. Closes Q14, X3. Depends on Q1+Q4+Q5 (test 1 needs
the new ingest contract) and Q10 (test 2 asserts signProbe).

### 8.2 Still-open questions (need live test or more digging)

| Q | Status | Blocker |
|---|--------|---------|
| Q6 default mover-cal mode | **resolved with caveats** (2026-04-22 live test) — see §8.3. Markers is the right default **after** the P1 convergence + pre-check fixes land; none of the 3 modes works out-of-the-box on this rig today. |

All other questions (Q1–Q5, Q7–Q14) closed in §8.1. Q3 live-test
constants (tier_weight, hull-falloff slope) remain to be tuned —
requires the Q1 P1 fix to land first so multi-camera fusion has
accurate placements to cluster on (see §12 for the follow-up
shortlist).

### 8.3 Q6 live-test resolution (basement rig, 2026-04-22)

Full session artifacts in `docs/live-test-sessions/2026-04-22/`.
Six cal runs executed (3 modes × 2 fixtures — 350W floor-mount fid 14,
MH1 ceiling-ish fid 2). **All six failed**, each with a distinct
failure mode:

| Fixture | Mode | Elapsed | Outcome |
|---------|------|---------|---------|
| 350W | markers | 36 s | FAIL — discovery + wiggle-confirm worked in 11 s, then convergence loop lost the beam (`Nonepx`) on the first DMX step from discovery pose toward marker 0; warm-start-to-discovery retried and failed the same way 3× on each of 3 markers (0/3 converged) |
| 350W | v2 | <1 s | FAIL — `Only 2 targets — need at least 4 for a stable fit` (auto-target generator under-produced) |
| 350W | legacy | 75 s | FAIL — BFS mapping stalled at 4/50 samples; floor-mount geometry pushes the beam off-camera with small pan/tilt moves, exhausting reachable BFS cells |
| MH1 | markers | 182 s (cancelled at timeout) | PARTIAL — converged marker 0 in 78 s, was still converging marker 1 at 3-min timeout; might have finished given more time |
| MH1 | v2 | <1 s | FAIL — same under-production of targets |
| MH1 | legacy | 240 s | FAIL — `Beam not found` during discovery; flagged operator-observed laser channel left ON (profile ch 10, default=0), which the cal's default-apply loop doesn't reset because it only applies `default > 0` |

**Q6 verdict:** markers-mode architecture is the correct default —
only mode that uses real surveyed ground truth, matches the operator-
preferred algorithm in `feedback_cal_algorithm.md`, and only mode that
made forward progress. But two blocking bugs prevent "just ship it":

- **Convergence loop can't recover from transient beam loss.** On the
  first failed pixel read, it warm-starts back to the discovery pose
  and retries the same commanded step — which fails identically. It
  needs bracket-and-retry: halve the DMX step and re-try from the last
  known-good pose instead of discovery. Ref: `mover_calibrator.py`
  convergence callbacks in `_mover_cal_thread_markers_body`.
- **Pre-flight uses a single-frame ArUco detect.** Step 2's stage-map
  aggregates up to 6 snapshots for the same reason (markers flicker in
  and out of detection at the edge of the frame). Markers mode
  checks once and fails if any marker misses that one frame — even
  though it's detectable in a neighboring snapshot. Compounded by the
  350W beam staying ON between test steps and washing out the
  pre-check frame.

Plus a third safety issue surfaced that doesn't block markers mode but
needs fixing regardless:

- **Non-pan/tilt channels can retain stale state.** `dmx-test` and the
  cal DMX-apply loop only apply channel defaults where `default > 0`.
  Channels with `default == 0` (like slymovehead ch 10 "Laser") keep
  whatever was last written. Cal should explicitly zero every non-
  pan/tilt channel at start — don't trust the default-apply loop.

See §8.5 (updated) for P1 line-items and `step6-summary.md` for the
full log.

### 8.4 Numeric baselines (2026-04-22 live test)

Independent of Q6, the session captured baselines for Q1/Q2/Q7/Q8/Q12
that the post-fix implementation should beat:

**Stage-map (step 2):**

| Cam | Markers matched | Reproj RMS (px) | cameraPosStage plausible? |
|-----|-----------------|------------------|---------------------------|
| 12  | 3 / 3           | **1226.7**       | no (X=−3857 mm, off stage) |
| 13  | 2 / 3 (id 1 missed, past visible band) | **1045.6** | no (Z=104 mm, ~floor level) |

Both solvePnP poses are physically impossible — Q8 B3 confirmed.
The direct-findHomography fallback path at `parent_server.py:2711–2718`
is the only usable output. `cameraPosStage` should be demoted to
diagnostic per §8.5 P2.

**Tracking ingest baseline (step 3, broken path at :7205–7232):**

11 valid rows + 3 OOF rows at 7 positions (5 taped floor marks + 2
surveyed ArUco markers). Raw error dominated by an orthogonal bug
(`stage.json` holds w=10 m, d=8 m vs actual rig 2 × 3.5 m; drives
≈ 5× raw-error magnitude).

| Metric | Mean | Median | Min | Max |
|--------|------|--------|-----|-----|
| Raw error (mm) | 4638 | 4913 | 2519 | 6942 |
| **Dims-normalized error (mm)** | 380 | 347 | 265 | 708 |
| Dims-normalized dx bias | **+1** | — | (stdev 221) | — |
| Dims-normalized dy bias | **−307** | — | (stdev 163) | — |

Signature: zero X-bias, consistent 307 mm Y-under-shoot. Cause: bbox
vertical centre sits at mid-body height (~900 mm above floor); tilted-
down camera maps that pixel row to a stage-Y closer than the actual
foot position. The Q1 P1 fix (homography-based pixel→floor + feet
bbox-bottom instead of centre) eliminates this.

**Homography round-trip (step 4+5):**

Beam aimed at each surveyed marker, `/beam-detect` (brightness-only,
per-camera threshold) → apply stored homography → compare vs
surveyed (x, y).

| Marker | Location | Cam 12 err | Cam 13 err | Notes |
|--------|----------|------------|------------|-------|
| 2 "middle" | **inside** 3-marker hull | **14 mm** | **4 mm** | both PASS (≤ 30 mm target) |
| 0 "close"  | at corner of hull | 53 mm | 264 mm | cam 12 near-PASS, cam 13 FAIL (only 2-marker fit) |
| 1 "back"   | **outside** Y-range of hull | 107 mm | 302 mm | both FAIL — extrapolation past marker hull |

Findings: homography is trustworthy inside the surveyed hull, degrades
linearly with extrapolation distance. Operator-facing implication:
tracking UI should gate on "inside surveyed hull" and refuse / warn
for auto-track targets beyond it. Also strongly recommend ≥ 4
surveyed markers spread across the tracked region (cam 13's 2-marker
fit was under-constrained).

### 8.5 Prioritised fix list (from 8.1 + 8.3 live-test additions)

| Priority | Ticket shape | Closes | Depends on |
|----------|--------------|--------|------------|
| P1 (new, from §8.3) | Markers-mode convergence: bracket-and-retry instead of warm-start-to-discovery. On `pixelX==None` from `_beam_detect`, halve the DMX step and retry from the last known-good pose; give up only when step size ≤ 1 DMX unit | Q6 (unblocks markers-as-default) | — |
| P1 (new, from §8.3) | Markers-mode pre-check: multi-snapshot ArUco aggregation (≥ 3 frames, best-per-id by corner perimeter) AND forced blackout of ALL fixtures before the detect loop. Matches stage-map's existing aggregation | Q6 | — |
| P1 (new, from §8.3) | Cal safety: explicit zero of every non-Pan/Tilt/Dimmer channel at cal start; don't rely on profile `default > 0` sweep. Laser and other potentially-on channels (`slymovehead` ch 10) stay lit otherwise | Q6 (safety) | — |
| P1 (new, from §8.3) | Stage bounds auto-derive: stop reading `stage.json` w/d from operator-settable free-form field; derive from the layout's fixture bounding box (or surveyed-marker hull). Eliminates the 5× raw-error multiplier observed at Q1 baseline | Q1 (amplifier) | — |
| P1 | Wire `api_objects_temporal_create` through `_pixel_to_stage` + return feet/head stage points; add `aimTarget` to track-actions; stamp `_method` tier per-object; add `/api/cameras/<fid>/calibration-status` | Q1, Q2, Q4, Q5 (backend), A1, A2, A6 | Q12 for accuracy of FOV fallback |
| P1 | Single-source homography: collapse to `_calibrations[str(fid)].matrix`, remove `_calibrated_cameras` dead code, migrate `fixture.homography` once | Q7, B2 | — |
| P1 | `fovType` whitelist + unified `"diagonal"` default + honour in `_pixel_to_stage` | Q12, B9, #611 | — |
| P2 | Demote solvePnP to diagnostic in stage-map response; rename `cameraPosition` → `cameraPositionDiagnostic`; report pnp-vs-homography disagreement | Q8, B3 | — |
| P2 | LM sign-confirmation probe — `verify_signs()` + `force_signs` kwarg on `fit_model`; kill the `<0.2° RMS` convention tie-break; synthetic test suite | Q10, B5 | — |
| P2 | SPA badges + warning banners + track-action accuracy hint for unmapped/FOV-fallback cameras | Q5 (UX) | Q1+Q5-backend + Q12 shipped |
| P2 | Multi-camera weighted-cluster fusion — periodic dedup pass over `_temporal_objects`; tier × confidence × hull-falloff weights | Q3, A3 | Q1 backend + Q5 `_method` stamping |
| P2 | Marker-coverage pre-cal viewport — top-down hull + FOV cone overlay + recommendation pin; closes #612 | Q11, B8 | — |
| P2 | E2E regression tests — synthetic tracking pipeline + synthetic mover-cal pipeline; ride on #277/#280 runner | Q14, X3 | Q1+Q4+Q5+Q10 shipped |
| P3 (epic) | Retire v1 legacy helpers — 5-phase plan in Q9 | Q9, B4 | Q7 must land first |
| P3 | Swap hand-rolled `_lm_solve` for `scipy.optimize.least_squares(loss="soft_l1")` — robust to flash outliers, drops ~40 lines | B5, B6 | Q10 probe lands first (test coverage) |
| P3 | Camera-health dashboard panel at top of Calibration tab; per-camera card composing `/api/cameras/<fid>/calibration-status` + tracking/beam history | Q13 | Q5 backend + Q11 layout idiom |

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

## 10. Related open issues (cross-reference)

Audit of 84 open issues on 2026-04-22; 43 touched camera/calibration
keywords. This table is the actionable subset — what to close, update,
or cross-link when each §8.1 fix lands. Everything else (#569 camera HW
compat, #293/#291 auto-reconnect, #203 camera OTA, #573 screenshot
chores, #307/#306 unrelated features) is orthogonal.

### 10.1 Will be closed by §8.1 fixes

| Issue | Title (short) | Closed by | Notes |
|-------|--------------|-----------|-------|
| **#611** | Fixture PUT silently drops fovType | P1 Q12 | Already cited in §8.1 Q12. Close when the PR merges. |
| **#612** | Marker placement UI — live per-camera coverage in Layout | Q11 (when answered) | Exact duplicate of Q11. Once Q11 closes, `#612` is the implementation ticket — keep that number, close on merge. |
| **#357** | Mover cal discovery doesn't detect beam at computed initial aim | Q6 (markers-mode default) | The legacy-discovery failure mode disappears once Q6 picks markers-mode; verify with live-test step 6, then close. |
| **#423** | YOLO not detecting `chair` class in tracking | P1 Q4 | Chair goes into the per-class fallback table (900 mm). Update the issue to explain class filter vs model coverage — user may be hitting the allowlist, not the model. |

### 10.2 Must update (comment / re-scope)

| Issue | Title (short) | Why update |
|-------|--------------|-----------|
| **#610** | Mover cal without operator pre-knowledge | Already the motivating ticket for markers-mode. Comment with a link to §8.1 Q6 + the upcoming Q6 live-test results, so #610's "done" bar is the markers-mode default. |
| **#600** | Swap ry ↔ rz on camera/fixture rotation (breaking) | **Coordination required.** Our P1 Q1+Q4 patch uses `_rotation_to_aim` / `camera_math.build_camera_to_stage(tilt, pan, roll)` with the **current** ordering. If #600 lands first, our fixes adopt the new order at the start; if after, a single rename sweep updates all `rotation=[tilt, pan, roll]` call-sites plus this doc's §3.4 convention. Either order is fine — just don't interleave. Comment so whoever picks it up knows. |
| **#597** | Advanced Scan — clear per-camera intrinsic / ArUco cal | After P1 Q7 lands, "clear" has **one** store to touch (`_calibrations[str(fid)]`) instead of two. Update the spec to reflect single-store, and drop any language about `fixture.homography`. |
| **#484** | Gyro/phone controller: stage-space orientation architecture | Downstream consumer of `ParametricFixtureModel.inverse`. Mention that P3 Q9 Phase 1 (`mover_control.affine_pan_tilt` fallback removal) will ripple into the gyro path. |
| **#474** | Gyro controller: absolute stage-space orientation mapping | Same as #484 — one aim stack after Q9. |
| **#427** | Android pointer mode — 3D spatial aiming | Same as #484/#474. Explicitly note it should target the v2 stack only — do not reintroduce `affine_pan_tilt` in new code. |
| **#510** | Android calibrate locks against server-assumed aim | Peripheral but worth a comment: the single-store homography (Q7) makes the server's "known aim" less ambiguous. Re-evaluate after P1 Q7 ships. |

### 10.3 Cross-link to Q14 end-to-end regression test

Q14 (still open) should absorb the scope of these existing tickets
rather than a fresh issue:

| Issue | Title (short) | Fit |
|-------|--------------|-----|
| **#533** | End to End Testing | Parent epic — Q14's deliverable lands here. |
| **#409** | Live show test session — simulated person tracking | Exact mocked-pipeline spec that Q14 should produce. Keep #409 as the spec issue; Q14 ticket implements it. |
| **#277** | End-to-end show regression — weekly Playwright | Already-scoped regression harness. Q14's camera-specific tests plug into this. |
| **#280** | Regression test runner — weekly CI harness | Infra for #277. Orthogonal but Q14's tests ride on it. |

### 10.4 No action

Mentioned only to confirm they were reviewed and are not affected:

- **#418** — Android 3D view coordinate space wrong. May be related to
  #600 (axis convention) but not to §8.1 fixes. No cross-link needed.
- **#595** — Gemini AI depth/reconstruction review. Out of scope per §9.
- **#569**, **#293**, **#291**, **#203**, **#573**, **#307**, **#306** —
  orthogonal.

---

## 11. Change log

- **2026-04-22** — initial draft.
- **2026-04-22** — §8.1 static-reading round: closed Q1, Q2, Q7, Q8, Q9, Q12.
  Five P1/P2 tickets + one P3 epic proposed. No code changes yet.
- **2026-04-22** — added §2 "no backward compatibility" clause (first
  beta release). Reworded Q7 and Q9 Phase 5 to drop migration steps;
  prefer clean breaking changes over compat shims.
- **2026-04-22** — closed Q4: geometric feet/head inference from bbox
  + homography; new `aimTarget` enum (`feet`/`center`/`head`) on
  track-actions. Q4 lands with Q1 (same P1 ticket). §2 (scipy
  allowed) noted from user edit 002ec14.
- **2026-04-22** — added §10 "Related open issues" cross-reference.
  4 issues close with §8.1 fixes (#611, #612, #357, #423); 7 need
  updates (#610, #600, #597, #484, #474, #427, #510); 4 cross-link
  to Q14 (#533, #409, #277, #280). No GitHub comments posted yet —
  table is advisory until we approve §8.1.
- **2026-04-22** — closed Q10: one-nudge sign-confirmation probe
  (`verify_signs()` in `parametric_mover.py`), kill the `<0.2° RMS`
  convention tie-break at 378–384, `force_signs` kwarg on `fit_model`,
  synthetic test suite. Bonus P3 ticket added for scipy LM swap
  (soft_l1 loss — robust to beam-flash outliers).
- **2026-04-22** — closed Q5: three-tier degradation
  (`homography`/`fov-projection`/`raw`) already provided by
  `_pixel_to_stage`; ingest path stamps `_method`; auto-track skips
  aim update on `raw` (fixture holds last good). New
  `/api/cameras/<fid>/calibration-status` endpoint. Backend rides
  with Q1 P1 ticket; SPA badges + banners are a separate P2 UX
  ticket. Never refuse-to-start; never modal-block; always show
  best-available placement.
- **2026-04-22** — closed Q3, Q11, Q13, Q14 (the no-hardware batch).
  Q3: weighted-cluster fusion (tier × YOLO conf × hull falloff) +
  periodic dedup pass over `_temporal_objects`. Q11: top-down hull
  + FOV cone + recommendation pin overlay; #612 is the build
  ticket. Q13: per-camera health-card panel composing the Q5
  endpoint + tracking/beam history fields. Q14: two synthetic-
  pipeline tests (tracking + mover-cal) riding on #277/#280
  infrastructure; #409 becomes Test 1's implementation ticket.
  Only **Q6** remains open — strictly hardware-bound (basement
  rig, live-test step 6).
- **2026-04-22 (live test)** — basement rig, steps 1/2/3/4+5/6
  executed (steps 7/8 deferred pending Q1 fix). Q6 resolved with
  caveats: **markers-mode is the correct default after the four
  new P1 fixes in §8.5 land** (convergence bracket-and-retry,
  multi-snapshot pre-check, cal-start channel zeroing, stage-bounds
  auto-derive). None of the three modes worked out-of-the-box; all
  six runs failed with distinct root causes. Numeric baselines
  captured in §8.4: stage-map RMS 1046/1227 px (solvePnP bogus,
  direct-homography usable), tracking-ingest dims-normalized
  error 380 mm mean with a −307 mm Y bias, beam→homography
  round-trip 4–14 mm inside the marker hull (Q7 passes) with
  degradation at corners (53/264 mm) and extrapolation
  (107/302 mm past hull). Full data and per-step write-ups at
  `docs/live-test-sessions/2026-04-22/`. Added §12 with follow-up
  exploration shortlist — items that surfaced during the session
  but sit outside §8.5's blocking-fix scope.
- **2026-04-22 (live test, re-run on expanded rig)** — basement rig
  reconfigured between runs: **3 cameras** (added Cam 16 "Out Left"
  on RPi-Sly1 deep in the stage at (2350, 1670, 905), rotation 1°),
  **3 movers** (added 150W MH Stage Left using the new
  `movinghead-150w-12ch` builtin profile from 8ff9a65), and **6
  surveyed ArUco markers** (added the elevated Pillar Post marker
  at Z=1368 mm, plus Stairs and Patent). Prior §8.3/§8.4 numbers
  were against the 2-cam / 2-mover / 3-marker rig and are
  **superseded** by this session's measurements against the
  expanded rig. Steps 1/2/3/4+5/6 re-executed; steps 7/8 still
  deferred. Full session artifacts replace the prior-run data at
  `docs/live-test-sessions/2026-04-22/`. Summary by step:
    - **Step 2 stage-map** — with the elevated Pillar Post marker
      in the registry, solvePnP produces near-mirror-pose poor
      fits on all three cameras: Cam 12 RMS **321 px** (6/6
      matched), Cam 13 **608 px** (6/6), Cam 16 **97 px** (2/6).
      Worse than the 3-marker baseline: solvePnP tries to use the
      one non-coplanar marker to resolve the mirror ambiguity and
      ends up on the wrong side of the plane. Strengthens the §8.5
      P1 "drop solvePnP, keep findHomography on floor markers"
      call — Pillar Post should be excluded from the fit (Z=0
      only) even when surveyed.
    - **Step 3 tracking baseline (broken ingest)** — 8/10 detected
      samples across cams 12+13 (cam 16 skipped, see §10 follow-up
      #620). Range 948–2221 mm with **mean dy = −1202 mm**
      (consistent Y-undershoot, same signature as the prior run's
      −307 mm but larger magnitude because stage dimensions are
      now correct at 4×3.6 m vs the 10×8 m the prior-session rig
      had cached). All 8/10 land in the 1000–3000 mm predicted
      pre-fix range. Q1 P1 fix still validated.
    - **Step 4+5 beam→homography round-trip** — 2/12 samples pass
      the ≤30 mm target, **both on Cam 16 at its fit markers**:
      marker 1 @ **25 mm**, marker 5 @ **13 mm**. Everything else
      225–2221 mm. Cam 16's homography was fit from 2 floor
      markers only (the Pillar Post was out of its FOV), so the
      P1-target flow of "findHomography on floor markers only"
      is effectively what Cam 16 already does — and its at-fit
      numbers land exactly where the fix aims to put cams 12/13.
      **Strongest single piece of evidence that P1 will work.**
    - **Step 6 mover-cal all modes × 3 movers** — 1/9 runs
      completed (350W legacy, 6 samples, v1 grid — no fit metrics).
      All 8 failures trace back to cam 12's bad H per the handoff
      prediction ("If residuals exceed 100 mm, the homography is
      bad and Q6 will fail regardless of mode"). Markers mode's
      **discovery** step (battleship + flash) was the most robust
      homography-independent component of any mode; markers mode's
      **convergence** on #17 marker 2 landed at **1.2 px RMS in 3
      iterations** — proving the markers-mode architecture works
      once the H is correct. v2 is unsalvageable on a bad-H rig
      (all targets `skipped`). Legacy's 350W success was a
      geometry accident. **Q6 re-verdict reinforced:** markers
      becomes default after P1; legacy → debug fallback; v2 →
      retire or gate on H-health.
    - **Five new camera-node ergonomics issues surfaced** and filed
      during this session:
      **#619** (no way to clear stale camera stage-map cal — only
      overwrite or factory reset),
      **#620** (RPi-Sly1 fw 1.3.0 `/scan` capture fails while
      `/snapshot` works — blocked Cam 16 from Step 3),
      **#621** (camera node `/scan` resolution hard-capped at
      320/640 — small / distant objects undetectable at 4K; SAHI
      tiling or 1280 re-export proposed),
      **#623** (camera settings auto-tune / slots / WB+exposure+
      gain button + optional AI evaluator — the lights-down /
      threshold-200 iteration during Step 4+5 shouldn't be
      manual),
      **#624** (`movinghead-150w-12ch` builtin profile WheelSlot
      capabilities missing `color` hex — RGB always mapped to slot
      0 (white); patched the running orchestrator via PUT so
      Step 6 cals emitted the requested red, permanent fix
      pending).
    - **Also noted (not yet filed):** v2 mode silently reports all
      targets as `skipped` without surfacing the underlying H
      residual; should fail loudly with "camera homography RMS
      too high — re-run stage-map." Legacy BFS gives up when
      neighbour moves push the beam off-camera and doesn't try
      orthogonal nudges. Markers-mode `Nonepx` failures are
      ambiguous — operator can't tell whether the beam flashed at
      the requested pose or whether the mover aimed elsewhere.

---

## 12. Recommendations for further exploration

Items that surfaced during the 2026-04-22 live test but fall outside
the P1/P2 "must-land" scope in §8.5. These are hypotheses to validate,
measurements to take, or investigations worth running before the next
calibration rebuild. Ordered by how much they'd improve operator
confidence in the final system.

### 12.1 Effective FOV vs nameplate FOV (cameras)

Observed in §8.4 and step 3 Finding 3: both cameras failed to see
targets that should have been well inside their advertised FOV. Cam 12
at X=1275 with advertised 100° HFOV clipped a target at X=1600 Y=1500
(only 13° off-axis — should be trivially in-frame). Step 2's node-
returned intrinsics imply fx values consistent with roughly 62° (cam 12)
and 148° (cam 13) HFOV at 3840-wide frames, contradicting the advertised
100°/90°.

- Root cause unknown. Three hypotheses to test:
  1. Node's `/calibrate/intrinsic` returns stale K from a prior sensor
     (EMEET 4K swapped in mid-session per `project_basement_rig.md`);
     cal data didn't follow the swap.
  2. Advertised `fovDeg` is diagonal (per `#611` comment and
     `project_basement_rig.md`); horizontal/vertical computations
     should divide by the aspect-ratio factor, which we're not doing.
  3. Lens has significant barrel distortion that's not modelled, so
     an object 13° off-axis in the real scene is > 30° off-axis in
     pixel space.
- **Recommend:** run `tests/test_camera_fov.py` (doesn't exist yet; new
  test) that places a measured-length target at known distance,
  measures its pixel extent, and back-solves effective HFOV.
  Write into a camera-node `/calibrate/intrinsic/report` endpoint.
  Useful for any rig, not just basement.

### 12.2 Beam detector: brightness-mode as default, per-camera threshold

The color-filtered `/beam-detect` (default path) gave 1–2 M px areas
with centroids on blue-cast static objects (shelving, walls). Only
brightness-only mode produced usable detections (∼10 k–30 k px areas
centred on the beam). And thresholds needed per-camera tuning
(100 for cam 12, 80 for cam 13).

- **Recommend:** change `/beam-detect` default to `mode: "brightness"`
  when `color` is not specified; keep color-filter as opt-in.
- **Recommend:** add `fixture.beamDetectThreshold` (per-camera override)
  to the camera fixture record. Cal code reads it; unset → detector's
  built-in default. Existing rigs re-tune once via §12.2's tune
  endpoint (no compat-shim, per §2).
- **Recommend:** add a `/beam-detect/tune` endpoint that sweeps
  thresholds 20→200, picks the threshold where `area` first stabilises
  below some cap (e.g. < 50 000 px) at `brightness > 230`, and stores
  it on the fixture. One-click "tune this camera" action in Setup.

### 12.3 Marker placement guidance (operator UX)

Q7 round-trip showed sharp quality cliff: 4–14 mm inside the hull,
> 100 mm outside. Cam 13's 2-marker fit was over-fit on its training
points (4 mm at marker 2, 264 mm at marker 0). Our rig also has
marker 1 "back" physically at Y=3100, past the computed visible band
Y=2967 — even on cam 12's 3-marker fit, extrapolation was unreliable.

- **Recommend:** surface a "coverage score" on the Setup → ArUco tab
  that counts: (a) registered markers, (b) cameras that see each
  marker, (c) area of the marker hull per camera, (d) **hull quality
  warning** when < 3 markers visible to a camera or when any visible
  marker falls outside the camera's computed floor-band.
- **Recommend:** documentation/UX guidance "minimum 4 markers,
  distributed across the tracking region, all inside the visible
  floor band". Auto-validate against the floor-band formula in
  `project_basement_rig.md` (Y = camZ / tan(camTilt + halfVFOV)).
- **Recommend:** refuse to save a camera calibration with <3 in-frame
  markers — make `markersMatched == 2` a hard warning the operator
  must acknowledge, not a silent success (step 2 saved cam 13's fit
  without any warning).

### 12.4 Orphan cleanup in `calibrations.json`

Step 2 Finding 1 noted fid 26 in `calibrations.json` pointing at
fixtures 23/24/25 that no longer exist. The store has no garbage
collection. Not a live bug today, but will gradually pollute migrations
and imports.

- **Recommend:** on `_load("calibrations", ...)` startup, drop any
  entries whose fid isn't in `_fixtures`. Log which ones got pruned.
- Folds into the Q7 P1 single-source-homography ticket.

### 12.5 Multi-camera fusion constants (step 7, deferred)

Step 7 (Q3 fusion weights) was deferred until Q1 lands. Once ingest
produces accurate per-camera placements, we need to tune:

- `tier_weight = {homography: 1.0, fov-projection: 0.3, raw: 0.05}`
  per §8.1 Q3 proposal — confirm or adjust based on live multi-camera
  measurements.
- `hull_falloff_slope` — step 4+5 shows error grows ~3 mm per mm of
  extrapolation distance past the hull edge; tune the weight
  discount so a camera's placement at 500 mm-past-hull gets reduced
  trust automatically.

- **Recommend:** re-do step 7 on the same rig after Q1 + Q3 fixes
  ship. Drop a person at 5 positions × 2 cameras simultaneously,
  measure fused-placement error vs single-camera, characterise when
  fusion helps vs hurts. This is a 30-minute protocol — cheap.

### 12.6 End-to-end tracking error (step 8, deferred)

Step 8 (beam-follows-person) was deferred pending Q1. Once Q1 lands:

- Run a person walking a slow stage-cross along a known path (tape
  line); log YOLO detection timestamps, ingest placements, auto-track
  aim commands, and camera-beam-detect positions simultaneously.
- **Latency measurement**: time from YOLO detect → beam on target.
  Expect this to be dominated by the tracking-rate gate (1 Hz
  default?) rather than any compute stage; confirm.
- **Tracking-accuracy measurement**: how far behind the person does
  the beam lag on a walk at typical-show pace (1 m/s)?

### 12.7 Floor-mount fixture support in cal

All three cal modes struggled with the 350W floor-mount. The BFS and
v2 target heuristics assume "fixture points at floor from above";
the 350W points "forward and slightly up" from Z=550. Small pan/tilt
moves take the beam off camera faster than for ceiling-mounts.

- **Recommend:** add a fixture-mount-mode field (`ceiling`/`floor`/
  `side`) and adapt discovery/target-generation per mode. Floor-mount
  calibration should probe along a tighter angular range near the
  narrow floor band Y ∈ [visible_min, visible_max], not across the
  full pan/tilt plane.
- Markers mode partly sidesteps this (it only needs to drive-to
  known pixels), but the convergence loop still needs tighter step
  sizing for narrow-reach fixtures. Tie into §8.5 bracket-and-retry
  fix.

### 12.8 ArUco marker robustness under scene illumination

Step 6 first attempt failed because the 350W beam stayed ON from the
previous step and ArUco detect missed marker 1. Even with beam off,
relaxed-params detector picks up non-registered markers (id 37) —
suggesting scenes with incidental ArUco-like patterns (logos,
barcodes, etc.) could cause false detections.

- **Recommend:** pre-check in markers-mode + stage-map should
  **cross-reference detected ids against the registry**. Detected
  markers NOT in the registry are ignored for calibration but should
  be surfaced in the UI ("saw untracked ArUco #37 — did you mean to
  register it?") so operators can't accidentally survey a decoy.
- **Recommend:** when pre-flight fails due to marker-invisible,
  automatically retry with all-fixtures-blackout (like we did manually
  in step 6) before returning the "needs ≥3 markers" error.

### 12.9 Test the dependent issue list (§10)

§10's table is a paper audit; after the P1 fixes land and their PRs
merge, walk through each listed issue (#611, #612, #357, #423, #610,
#600, #597, #484, #474, #427, #510, #533, #409, #277, #280) and post
the close/update/cross-link comments as §10.1/10.2/10.3 specify. Do
this in one coordinated pass — operators seeing 15 stale issues update
at once is less noisy than a random trickle.

- **Recommend:** script it: `gh issue comment <n> -F comment.md`
  batch, one comment per issue, templates from §10's table. 20-min
  task, high signal to project-tracking observers.
