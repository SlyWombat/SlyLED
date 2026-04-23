# Step 4+5 — Beam → homography round-trip

**Date:** 2026-04-22  
**Method:** Mover beam manually aimed at each floor marker centre (room lights down). Orchestrator `POST /api/cameras/<fid>/beam-detect` with `threshold=200` returns the beam centroid pixel. Persisted homography (from Step 2) projects the pixel to stage coords. Residual = `‖H(beam_px) − marker_stage_xy‖`.

**Test object:** 350 W / 150 W mover beam (aim manually per marker, operator driven)  
**Marker 3 (Pillar Post, Z=1368 mm) excluded:** H maps pixel → floor plane (Z=0); the elevated marker would compute a floor-shadow residual that's not the test's target.

## Pre-conditions

- First attempt with default threshold (30) and bright room: area values 4-6 million pixels (50-75% of the frame), detector lock-on to wall reflections, residuals 600-11000 mm — **data discarded**.
- After lights-down and threshold=200: area values 10-640k pixels, detector isolates the beam spot. These are the results below.
- Red colour filter (`color="red"`) returned `found=false` on all cameras; the red-filter branch of beam_detector appears to have a bug and isn't usable as a sanity cross-check — filed informally for follow-up.

## Results (threshold=200, lights-down)

| marker | GT (mm) | cam | at-fit? | beam pixel | H→stage (mm) | dx | dy | **err (mm)** |
|:------:|---------|:--:|:-------:|------------|--------------|---:|---:|-------------:|
| 0 Back Right | (500,2280) | 12 | ✓ | (3384,1759) | (496,2055) | -4 | -225 | **225** |
| 0 Back Right | (500,2280) | 13 | ✓ | (2906,1715) | (743,2558) | +243 | +278 | **370** |
| 0 Back Right | (500,2280) | 16 | — | — | — | — | — | **N/A** (no beam) |
| 2 Pillar Base | (1150,2100) | 12 | ✓ | (3306,1936) | (668,2103) | -482 | +3 | **482** |
| 2 Pillar Base | (1150,2100) | 13 | ✓ | (2866,1703) | (770,2565) | -380 | +465 | **601** |
| 2 Pillar Base | (1150,2100) | 16 | — | — | — | — | — | **N/A** (HTTP 503) |
| 1 Furnace Door | (2050,3170) | 12 | ✓ | (1783,1370) | (1124,2189) | -926 | -981 | **1349** |
| 1 Furnace Door | (2050,3170) | 13 | ✓ | (2660,1358) | (1339,2688) | -711 | -482 | **859** |
| 1 Furnace Door | (2050,3170) | 16 | ✓ | (1110,1024) | (2064,3149) | +14 | -21 | **25** |
| 5 Patent | (3120,3090) | 12 | ✓ | (1785,1376) | (1113,2189) | -2007 | -901 | **2200** |
| 5 Patent | (3120,3090) | 13 | ✓ | — | — | — | — | **N/A** (no beam) |
| 5 Patent | (3120,3090) | 16 | ✓ | (395,1035) | (3131,3096) | +11 | +6 | **13** |
| 4 Stairs | (500,3500) | 12 | ✓ | (1786,1372) | (1116,2188) | +616 | -1312 | **1449** |
| 4 Stairs | (500,3500) | 13 | ✓ | (2944,1257) | (-487,1510) | -987 | -1990 | **2221** |
| 4 Stairs | (500,3500) | 16 | — | (1827,916) | (735,3520) | +235 | +20 | **236** |

## Per-camera aggregates

| cam | H fit on | detected samples | err range (mm) | mean err (mm) | notes |
|-----|----------|:----------------:|----------------|--------------:|-------|
| 12 | [0, 1, 2, 3, 4, 5] | 5 | 225–2200 | 1141 | H fit from **6 markers incl. elevated Pillar Post** — PnP mirror-pose ambiguity |
| 13 | [0, 1, 2, 3, 4, 5] | 4 | 370–2221 | 1013 | H fit from **6 markers incl. elevated Pillar Post** — PnP mirror-pose ambiguity |
| 16 | [1, 5] | 3 | 13–236 | 91 | H fit from **2 floor markers only** — accurate at-fit, moderate extrapolation |

## Smoking gun: cam 16 at its fit markers

| marker | cam 16 residual | status |
|:------:|:---------------:|-------|
| 1 | 25 mm | ✓ **passes ≤30 mm target** (at-fit) |
| 5 | 13 mm | ✓ **passes ≤30 mm target** (at-fit) |
| 4 | 236 mm | extrapolated (extrapolation) |

Cam 16's homography was fit from **only 2 floor markers** (ids 1 and 5). At those two markers the beam→H round-trip lands **13 mm and 25 mm** — **both inside the 30 mm target**. At other markers (extrapolation), residuals degrade to 236 mm. That's the signal: the camera-model pipeline and the beam-detector both work when the H is correct. Cams 12/13 residuals of 200-2200 mm are dominated by bad H, not by detection.

## Pass/fail against handoff threshold (≤30 mm RMS)

- **2/12 samples pass** (cam 16 at markers 1 and 5).
- **10/12 samples fail**, ranging 225–2221 mm.
- Handoff rule: *"If residuals exceed 100 mm, the homography is bad and Q6 (mover-cal) will fail regardless of mode."* — cams 12/13 confirm this. Expect Q6 to fail for both of them.

## P1 fix acceptance criterion (implication for #488)

Replacing `cv2.solvePnP` with `cv2.findHomography` using only the 5 **floor** markers (z=0) should:

1. Eliminate the coplanar-with-one-outlier mirror-pose ambiguity that poisons cam 12/13.
2. Bring cam 12/13's residuals to the same regime as cam 16's at-fit readings (~10-30 mm).
3. Cam 16 itself likely improves modestly (5 floor markers instead of 2 fitted).

If post-P1 residuals are **not** <50 mm on the same basement rig, the fix is incomplete and we need to revisit the intrinsic calibration step (cam 12 reports `intrinsicSource=calibrated` but the fit behaviour suggests the intrinsics are stale).

## Data files

- `step4-5-M{0,1,2,4,5}-rows.json` — per-marker raw rows (marker 3 excluded)
- `snapshots/M{id}-beam-cam12*.jpg` — beam snapshots with visible spot

## Known issues flagged during this step

- `color="red"` filter path returns `found=false` for all-red beam snapshots that succeed without the filter. Likely a threshold scaling bug in the colour-masked branch of `beam_detector.py`. Non-blocking (threshold=200 without colour filter works).
- RPi-Sly1 snapshot is slower under low light (one M2/M4 cam-16 request timed out at 20 s).