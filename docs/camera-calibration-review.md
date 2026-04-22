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

### 8.2 Still-open questions (need live test or more digging)

| Q | Status | Blocker |
|---|--------|---------|
| Q3 multi-camera fusion policy | open | Live-test step 7 (ghost-object count) |
| Q5 UX for unmapped camera | open | Depends on Q1/Q2 landing |
| Q6 default mover-cal mode | open | Live-test steps 6 — need per-mode residuals on basement rig |
| Q11 marker-coverage UX | open | UX-fix; scope after Q6 |
| Q13 "camera health" dashboard | open | Scope after Q1/Q7/Q12 |
| Q14 end-to-end regression test | open | Scope after Q1/Q7/Q12 — test shape depends on final ingest contract |

### 8.3 Prioritised fix list (from 8.1)

| Priority | Ticket shape | Closes | Depends on |
|----------|--------------|--------|------------|
| P1 | Wire `api_objects_temporal_create` through `_pixel_to_stage` + return feet/head stage points; add `aimTarget` to track-actions | Q1, Q2, Q4, A1, A2, A6 | Q12 for accuracy of FOV fallback |
| P1 | Single-source homography: collapse to `_calibrations[str(fid)].matrix`, remove `_calibrated_cameras` dead code, migrate `fixture.homography` once | Q7, B2 | — |
| P1 | `fovType` whitelist + unified `"diagonal"` default + honour in `_pixel_to_stage` | Q12, B9, #611 | — |
| P2 | Demote solvePnP to diagnostic in stage-map response; rename `cameraPosition` → `cameraPositionDiagnostic`; report pnp-vs-homography disagreement | Q8, B3 | — |
| P2 | LM sign-confirmation probe — `verify_signs()` + `force_signs` kwarg on `fit_model`; kill the `<0.2° RMS` convention tie-break; synthetic test suite | Q10, B5 | — |
| P3 (epic) | Retire v1 legacy helpers — 5-phase plan in Q9 | Q9, B4 | Q7 must land first |
| P3 | Swap hand-rolled `_lm_solve` for `scipy.optimize.least_squares(loss="soft_l1")` — robust to flash outliers, drops ~40 lines | B5, B6 | Q10 probe lands first (test coverage) |

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
