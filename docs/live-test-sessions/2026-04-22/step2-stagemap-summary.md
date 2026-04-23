# Step 2 — Stage-map all 3 cameras

**Date:** 2026-04-22  
**Rig:** basement (post-move, 3 cams, 6 surveyed markers)  
**Call:** `POST /api/cameras/<fid>/stage-map` with marker registry in body, maxSnapshots=6

## Results

| cam | matched | RMS (px) | matchedIds | PnP cameraPosStage (mm) | layout pos (mm) | Δ‖xy‖ (mm) | Δz (mm) | intrinsicSource |
|-----|---------|---------:|-----------|--------------------------|------------------|-----------:|--------:|-----------------|
| 12 | 6/6 | 320.9 | [0, 1, 2, 3, 4, 5] | (1914, 8328, -2550) | (940, 110, 2040) | 8275 | 4590 | calibrated |
| 13 | 6/6 | 608.0 | [0, 1, 2, 3, 4, 5] | (-3713, 18524, -8644) | (1730, 110, 2040) | 19202 | 10684 | calibrated |
| 16 | 2/8 | 97.4 | [1, 5] | (3153, 3989, -1840) | (2350, 1670, 905) | 2454 | 2745 | fov-estimate |

## Pass/fail against §6.2 handoff threshold

- Threshold: `markersMatched ≥ 2` AND `rmsError < 50 px`.
- Cam 12: matched ✓, RMS ✗ → **FAIL**
- Cam 13: matched ✓, RMS ✗ → **FAIL**
- Cam 16: matched ✓, RMS ✗ → **FAIL**

**All three cameras fail the RMS threshold.** This is the pre-fix baseline — the review doc Q7/Q8 predicted solvePnP on mostly-coplanar floor markers (5 @ Z=0 + 1 elevated Pillar Post @ Z=1368) would produce unreliable poses. The data confirms:

- Cam 12: RMS 321 px, PnP places the camera 8 m deep and 2.5 m below the floor (Y=8327, Z=−2549).
- Cam 13: RMS 608 px, PnP places the camera 18 m deep and 8.6 m below floor — nonsensical, likely the solvePnP mirror-pose ambiguity (`feedback_stage_map_coplanar.md`).
- Cam 16: RMS 97 px, only 2/6 markers matched (it physically sees only the back-right corner), fov-estimate intrinsics because the Out Left camera lacks an individual intrinsic calibration.

## Q7 B2 — dual-store check

`_calibrations[str(fid)]` AND `fixture.homography` are both written on stage-map. Per-cam homography first rows:

- Cam 12: `fixture.homography[0]` = `[0.1261582414457859, -0.8039881273805972, 814.0735266206285]` (matches `_calibrations[12].matrix`; both stores present — the P1 Q7 change can collapse to a single store safely).
- Cam 13: `fixture.homography[0]` = `[0.3311916789648166, -0.7001731233822795, -91.66349309583713]` (matches `_calibrations[13].matrix`; both stores present — the P1 Q7 change can collapse to a single store safely).
- Cam 16: `fixture.homography[0]` = `[2.6588106296330865, -7.193257264311847, 544.483077037993]` (matches `_calibrations[16].matrix`; both stores present — the P1 Q7 change can collapse to a single store safely).

## Q8 B3 — cameraPosStage vs layout position disagreement

Expected disagreement magnitude under coplanar ambiguity:

- Cam 12: Δ3D = 9462 mm (disagreement)
- Cam 13: Δ3D = 21974 mm (disagreement)
- Cam 16: Δ3D = 3682 mm (disagreement)

Disagreement is large (up to 21 m for Cam 13) — confirms PnP is unusable here. The P1 fix replaces this with `cv2.findHomography` using only the floor markers, which eliminates the mirror ambiguity and should bring the homography round-trip residual (Step 4+5) back to the <30 mm target even when `cameraPosStage` itself is unreliable.

## Data files

- `step2-stagemap-cam12.json` / `step2-stagemap-cam13.json` / `step2-stagemap-cam16.json` — raw stage-map responses
- `step2-calib-cam12.json` / `step2-calib-cam13.json` / `step2-calib-cam16.json` — persisted calibration summaries