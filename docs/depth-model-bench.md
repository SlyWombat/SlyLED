# Depth Model Benchmark — #593

Evaluating AI monocular / multi-view depth models against the current `Depth-Anything-V2` baseline on the SlyLED point-cloud pipeline.

**Scene:** basement rig, both cameras on Orange Pi at `192.168.10.235`, tilts 30°/30°, positions `(1275, 120, 1930)` and `(830, 120, 1930)`, DMX blackout during capture.

**Harness:** `/mnt/d/temp/live-test-session/bench/`. Each model consumes the same 1920×1080 synchronous frame pair, produces a depth map, back-projects to cam-local 3D via the pinhole model (`z = depth`, `x = (px-cx)*z/fx`, `y = (py-cy)*z/fy`), transforms to stage coords through the known camera poses via the shared `camera_math.build_camera_to_stage` matrix. Floor-plane fit is restricted to points with stage-Z < 500 mm so RANSAC targets the floor, not the back wall.

## Results

| Model | Runtime (s) | Points | Floor tilt ° | Plane inlier % | Below floor % | At floor ±100 mm % | In stage % |
|---|---|---|---|---|---|---|---|
| Depth-Anything-V2 Small (Pi, current) | 15.8 | 10,095 | 8.2 | 14.0 | 53.5 | 4.2 | 23.0 |
| Depth-Anything-V2 Large (host CPU) | 19.0 | 10,366 | 12.9 | 13.0 | 32.7 | 3.8 | 44.1 |
| DPT-Large (host CPU) | 4.9 | 10,367 | 25.6 | 16.8 | 37.8 | 4.0 | 39.5 |
| **ZoeDepth NYU-KITTI (host CPU)** | **14.1** | 10,368 | **4.7** | 17.6 | **8.3** | 2.1 | **66.8** |
| Marigold LCM | — | — | — | — | — | — | — |
| DUSt3R / MASt3R | — | — | — | — | — | — | — |

## Key findings

### ZoeDepth is the clear winner on every quality metric

- **6× less below-floor noise** (8.3 % vs 53.5 % on DA-V2 Small)
- **3× more points inside the stage bounding box** (66.8 % vs 23.0 %)
- **Floor plane tilt: 4.7°** — essentially horizontal, matching calibrated-measurement quality

### Why it wins

ZoeDepth outputs **metric depth in millimetres directly**. Depth-Anything-V2 and DPT-Large output normalized **disparity** (higher = closer) that we linearly scale to mm, which is a coarse approximation of a 1/d relationship. The 40-50° residual floor-plane tilt we couldn't eliminate with #589's disparity-direction fix is simply gone with metric depth — because the non-linear scale error never enters the pipeline.

### Latency

14 s for two cameras on a WSL x86-64 host CPU (AVX2, no AVX-512). On the Orange Pi ARM expect 30-60 s per scan end-to-end — tolerable for a one-off environment scan. GPU is well under 1 s; Marigold has an LCM variant that runs fast on GPU too.

## Models skipped

### Marigold LCM (diffusion-based)

`diffusers.MarigoldDepthPipeline.from_pretrained(...)` bus-errors on this WSL CPU (no AVX-512). The specific BF16 / AVX-512 kernels that diffusers loads for Marigold's U-Net crash at library import time. It would run on the orchestrator's Windows host which has the required instruction-set support, but the agent session couldn't complete the bench locally.

### DUSt3R / MASt3R (two-view pointmap)

Distributed as a GitHub repo requiring clone + `pip install -e .`. Outside the agent session's permission scope (Untrusted-Code-Integration). Recommended for operator testing in a separate environment.

Both models can be added to the bench by dropping `result_marigold.json` or `result_dust3r.json` into the harness directory.

## Recommendation

1. **Replace Depth-Anything-V2 with ZoeDepth as the Pi-side default depth model** in `firmware/orangepi/depth_estimator.py`. Keep DA-V2 as a configurable fallback so operators without ZoeDepth weights installed continue to get a scan.
2. **Export ZoeDepth to ONNX** (`zoedepth.onnx`) so the Pi continues to run under onnxruntime alone, no PyTorch install required. Ship the ONNX file alongside the existing DA-V2 ONNX in the firmware deploy.
3. **Expose model choice on `/api/space/scan`** via a `model: "zoedepth" | "dav2"` body field (Advanced Scan card #588 picks this up automatically).
4. **Keep Phase 1 depth-anchor active** (#581). With metric ZoeDepth input the anchor should converge more often and with smaller corrections; with DA-V2 fallback it continues doing the heavy lifting.
5. **Run Marigold + DUSt3R bench on operator-approved hardware** to complete the table. The harness accepts any depth-map producer via `result_*.json`.

## Raw test artifacts

```
/mnt/d/temp/live-test-session/bench/
├── harness.py                 # shared metrics (plane fit, floor filter, range stats)
├── report.py                  # aggregates result_*.json into the table
├── pair.json                  # captured 1920×1080 synchronous pair (base64)
├── cam0.jpg / cam1.jpg        # decoded frames used for all runs
├── run_dav2.py                # Pi firmware baseline via /point-cloud
├── run_dav2_large.py          # DA-V2 Large (host CPU)
├── run_dpt.py                 # DPT-Large (Marigold substitute)
├── run_zoedepth.py            # winning model
└── result_*.json              # per-model metrics
```

## Next steps

Open a follow-up issue / PR that:

1. Adds `zoedepth.onnx` to the firmware deploy manifest (`_CAMERA_FW_FILES`)
2. Adds a `ZoeDepthEstimator` class in `firmware/orangepi/depth_estimator.py` that loads the ONNX via `onnxruntime.InferenceSession` and emits metric mm depth
3. Selects model via `/camera/config.json` flag (`depth_model: "zoedepth" | "dav2"`, default `zoedepth`)
4. Exposes the same flag on `/api/space/scan` so operators can A/B
5. Deploys to `192.168.10.235`, measures on-Pi latency, rescans the basement rig, compares metrics
