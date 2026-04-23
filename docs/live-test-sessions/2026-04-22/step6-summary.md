# Step 6 — Mover calibration (all modes × 3 movers)

**Date:** 2026-04-22  
**Method:** `POST /api/calibration/mover/<fid>/start` with `mode ∈ {legacy, v2, markers}`, color=red `[255,0,0]`. Camera auto-selected (`_best_camera_for`) → all three movers picked **cam 12 Stage Right**. DMX bridge SLYC-1152 at 192.168.10.219.

**Setup correction during this step:**
- Initial green-default cal sent the wrong colour: the `movinghead-150w-12ch` profile's WheelSlot capabilities had no `color` hex field, so `rgb_to_wheel_slot()` skipped every slot and returned 0 (open/white). Patched the profile via `PUT /api/dmx-profiles/movinghead-150w-12ch` to add hex matching each slot label. Filed as **#624**. The 350W profile was already correct.
- All three modes were exercised against the **known-bad camera-12 homography** from Step 2 (RMS 321 px, fit poisoned by the elevated Pillar Post marker). Step 4+5 already showed cam 12's H residuals at 225-2200 mm. Per the handoff: *"If residuals exceed 100 mm, the homography is bad and Q6 (mover-cal) will fail regardless of mode."* That is exactly what happened.

## Results matrix

| mover | profile | legacy | v2 | markers |
|-------|---------|--------|----|---------|
| #14 350W BeamLight | `beamlight-350w-16ch` | **DONE** (6 samples) | error: Only 0 of 4 targets converged | error: Only 0/4 markers converged |
| #17 150W MH Stage Right | `movinghead-150w-12ch` | error: Only 0 samples collected | error: Only 0 of 4 targets converged | error: Only 1/4 markers converged |
| #18 150W MH Stage Left | `movinghead-150w-12ch` | error: Only 1 samples collected | error: Only 0 of 4 targets converged | error: Only 0/4 markers converged |

**Headline:** 1 of 9 calibration runs completed (350W legacy, 6/50 samples, no `fit` metrics — v1 grid format).

## Per-mode failure analysis

### Legacy (BFS auto-discovery + sample expansion)

- **350W (#14):** completed — 6 samples, status=done. The 350W is mounted low (z=550 mm) on the back wall. From cam 12's perspective the beam moves freely across the frame as pan/tilt vary, so BFS can collect samples before exhausting the search budget. Saved as v1 grid (no parametric fit).
- **150W movers (#17, #18):** discovery succeeds but BFS collects ≤1 sample. The geometric warm-start lands the beam at pixel x≈3770-3830 — **the right edge of the 3840-wide frame**. Any neighbour move on the BFS expansion sends the beam off-camera, so detection returns no new pixel and the search terminates with `Only 0/1 samples collected — need at least 6`. The 150W movers are mounted high (z=1760) and the cam-12-to-mover geometry pushes the beam toward the frame edge no matter where the warm-start aims.

### v2 (target-driven convergence loop)

- **All three movers, all four targets `skipped`.** v2 picks 4 stage targets, projects each through cam 12's homography to get an expected pixel, and aims the mover to land the beam there. Because cam 12's H is broken (Step 4+5 residuals 225-2200 mm), the expected pixels are wrong → mover aims to the wrong place → beam isn't where v2 expects it → marked `skipped` after 3-5 iterations. Telemetry: target 4 on 350W reported `errorPx=1758` (actual beam was 1758 pixels from where H said it should be).
- v2's iteration cap (3-5 attempts per target) doesn't allow a broader search when the initial pixel is far off — confirmed operator's observation that *"it did not do any searching, it only tried one location."*

### Markers (#610 — battleship + flash + nudge per surveyed marker)

- **#17 only converged on marker 2** at pan=0.645, tilt=0.145, residual=**1.2 px** in 3 iterations. That's the inverse of Step 4+5's pattern: marker 2 is the closest marker to mover #17 and the convergence loop happened to find it. The other 3 visible markers (0, 1, 4) failed: 712 px, 191 px, 1024 px after 4-7 iterations. Cal aborts requiring ≥3 markers for a stable fit.
- **#14 and #18:** 0/4 converged. All marker convergence iterations returned `Nonepx` (beam not detected at the attempted pose). Same pattern: the convergence step requests a pose computed from the bad H, the beam lands somewhere off-target / off-camera, no detection.
- Battleship discovery itself (the 4×4 coarse scan + flash confirm) **worked correctly**: every probe found a beam pixel, just clustered at the same x≈3750-3830 right-edge zone. Discovery isn't homography-dependent so it's robust — the failure happens at the per-marker convergence step that *is* homography-dependent.

## Q6 verdict (handoff acceptance criteria)

Handoff thresholds:
> - Markers ≤ 2° RMS and completes without operator intervention → markers is the default.
> - v2 needs a pre-placed target and gives ≤ 1° RMS → v2 is "advanced" option.
> - Legacy is deprecated regardless of residual.

**None of the modes meet the criteria on this rig with the current homography.** That said:

- **Markers mode is the only one with a path forward.** Its discovery step (battleship + flash) is the most robust component of any of the three modes — it doesn't depend on the camera homography at all. It has the only at-fit single-marker convergence (1.2 px on mover #17 marker 2). Once cam 12's H is fixed (P1), markers mode should hit the ≤2° target quickly because the per-marker convergence will be operating on accurate expected pixels.
- **v2 is unsalvageable on a rig with bad H.** It assumes the homography is reliable and short-circuits when it isn't. Even with a fixed H, v2's 4-target geometry doesn't add diagnostic value over markers mode with 5+ surveyed markers.
- **Legacy is deprecated as expected.** The 350W success was an accident of geometry; the 150W movers can't even warm-start cleanly.

**Recommended Q6 outcome:** **markers becomes the default**, contingent on the P1 H fix. Legacy stays as a debug-only fallback. v2 can be retired (or relabelled and gated on a healthy-H sanity check).

## Implications for #488 (parametric mover model + LM solver)

- The #488 ParametricFixtureModel + LM solver assumes good (pan, tilt, stage_xy) tuples. Today the only way to *get* those tuples is markers mode. Without P1, we have no reliable input data to feed the solver.
- After P1 fixes the H, this rig should be able to produce the input tuples markers mode needs, and #488 can be exercised end-to-end on physical hardware.

## Issues filed during Step 6

- **#624** — `movinghead-150w-12ch` builtin profile WheelSlot capabilities missing `color` hex; RGB always maps to slot 0 (white). Patched in this session via PUT to the running orchestrator; permanent fix to be merged into the builtin profile.

Other potential follow-ups (not filed yet):
- Legacy BFS gives up too quickly when neighbour moves push the beam off-camera. A retry policy that nudges in the orthogonal direction could keep the search alive.
- v2 silently treats all 4 targets as `skipped` without surfacing the underlying H residual to the operator. Should fail loudly with a *"camera homography RMS too high (X px) — re-run stage-map"* message.
- Markers mode `Nonepx` failures could be made more informative — operator can't tell if the beam actually flashed at the requested pose or if the mover aimed elsewhere.

## Data files

- `step6-{350W,MH-SR,MH-SL}-{legacy,v2,markers}.json` — 9 cal job final-state JSONs