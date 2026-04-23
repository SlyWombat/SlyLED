# Step 3 — Tracking placement baseline (pre-fix, broken ingest)

**Date:** 2026-04-22  
**Rig:** basement (post-move)  
**Method:** Chair placed centred on each ArUco floor-marker; `POST /api/cameras/<fid>/scan` on cam 12 + cam 13 (cam 16 excluded — #620 RPi-Sly1 /scan broken). Chair bbox centre sent through `POST /api/objects/temporal` with `cameraId`/`pixelBox`/`frameSize` — the broken proportional ingest path at `parent_server.py:7218-7232`.

**Ground truth:** marker XY from `/api/aruco/markers` registry (matches issue #533 exactly).  
**Traversal order:** 0 → 2 → 1 → 5 → 4 (near-to-far, user-chosen).  
**Test object:** ornate wooden chair (single item, moved between markers). Detected reliably as YOLO class `chair` when visible. Confidence varies with distance / occlusion.

## Results

| marker | label | GT (mm) | cam | conf | bbox ctr (px) | placed (mm) | dx (mm) | dy (mm) | **err ‖xy‖ (mm)** |
|-------:|-------|---------|----|-----:|---------------|-------------|--------:|--------:|-------------------:|
| 0 | Back Right | (500,2280) | 12 | 0.78 | (2538,1417) | (1356,1238) | +856 | -1042 | **1348** |
| 0 | Back Right | (500,2280) | 13 | 0.86 | (3414,1412) | (444,1247) | -56 | -1033 | **1035** |
| 2 | Pillar Base | (1150,2100) | 12 | — | — | — | — | — | **MISS** (no chair detection) |
| 2 | Pillar Base | (1150,2100) | 13 | 0.84 | (2714,1468) | (1173,1152) | +23 | -948 | **948** |
| 1 | Furnace Door | (2050,3170) | 12 | 0.42 | (1021,942) | (2936,2031) | +886 | -1139 | **1443** |
| 1 | Furnace Door | (2050,3170) | 13 | 0.44 | (1644,950) | (2288,2017) | +238 | -1153 | **1178** |
| 5 | Patent | (3120,3090) | 12 | 0.32 | (138,1276) | (3856,1473) | +736 | -1617 | **1776** |
| 5 | Patent | (3120,3090) | 13 | 0.78 | (684,998) | (3288,1937) | +168 | -1153 | **1165** |
| 4 | Stairs | (500,3500) | 12 | 0.71 | (2394,859) | (1507,2168) | +1007 | -1332 | **1669** |
| 4 | Stairs | (500,3500) | 13 | — | — | — | — | — | **MISS** (no chair detection) |

## Per-camera aggregates (detected samples only)

- **cam 12**: 4 detected / 1 missed — err range 1348–1776 mm (mean 1559); mean dx = +871, mean dy = -1282.
- **cam 13**: 4 detected / 1 missed — err range 948–1178 mm (mean 1081); mean dx = +93, mean dy = -1072.

## Misses

- **cam 12 @ marker 2 (Pillar Base):** YOLOv8n @ 640 would not classify the chair — even at threshold 0.10 it only returned a false-positive `refrigerator` on the storage-bin stack. The pillar directly behind the chair likely splits the silhouette. Related issue: **#621** (resolution cap limits small/occluded object detection).
- **cam 13 @ marker 4 (Stairs):** Zero detections at threshold 0.20. Stairs is the deepest marker (Y=3500 mm, beyond the back-wall camera's useful band Y ∈ [1400, 2967] per prior rig notes).
- **cam 16 (all markers):** Excluded — scan endpoint broken on RPi-Sly1 fw 1.3.0 (**#620**).

## Key pattern — Y-axis (depth) under-estimated by ~1–1.6 m

Across every detected sample, the broken ingest places the object at **shallower Y than ground truth**:

| sample | GT Y | placed Y | dy |
|--------|-----:|---------:|---:|
| cam12 M0 | 2280 | 1238 | -1042 |
| cam13 M0 | 2280 | 1247 | -1033 |
| cam13 M2 | 2100 | 1152 | -948 |
| cam12 M1 | 3170 | 2031 | -1139 |
| cam13 M1 | 3170 | 2017 | -1153 |
| cam12 M5 | 3090 | 1473 | -1617 |
| cam13 M5 | 3090 | 1937 | -1153 |
| cam12 M4 | 3500 | 2168 | -1332 |

Mean dy across 8 samples = **-1177 mm**. This is exactly the expected Q1 failure mode: the proportional mapping in `api_objects_temporal_create()` treats the bbox Y-fraction as a direct linear map to stage depth — but the camera's tilt (30°) compresses the back of the stage into the top of the frame. An object deep in the stage lands near the horizon; an object close to the camera takes up the bottom of the frame. Without homography, the true Y can't be recovered from Y-pixel alone.

## Key pattern — X-axis (width) alternates per camera

X projection accidentally works for **cam 13** on centre/back-right markers (dx = +23, +168, +238 mm on markers 2, 5, 1) because cam 13 is mounted near-centre on the back wall and the stage X axis happens to align loosely with its horizontal pixel axis. Cam 12 is offset stage-right and its pixel X no longer maps to stage X proportionally — dx errors 736–1007 mm.

## Pass/fail against handoff expected range (1000–3000 mm pre-fix)

- 7/8 detected samples land in the predicted 1–2 m range — baseline confirmed.
- 1 sample (cam13 M2 @ 948 mm) is just below the 1000-mm lower bound; reflects that cam 13 sees Pillar Base near-centrally.
- **Q1 fix acceptance criterion:** post-fix errors should drop below **50 mm** for all detected samples (homography round-trip baseline; see Step 4+5 once run).

## Data files

- `step3-M{0,1,2,4,5}-rows.json` — per-marker raw rows
- `step3-master.json` — aggregated master list
- `snapshots/M{id}-cam{12,13}.jpg` — scan snapshots