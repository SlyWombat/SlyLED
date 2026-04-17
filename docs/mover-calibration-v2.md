# Mover Calibration v2 -- Design Document

**Issue:** #488  
**Status:** Draft  
**Date:** 2026-04-17  

---

## 1. Executive Summary

SlyLED's current moving-head calibration uses a linear affine fit from 4 manually-aimed
samples, or an automated camera-assisted BFS grid scan. Both approaches suffer from
fundamental limitations: the affine extrapolates nonsensically outside the sampled region,
the grid scan takes minutes and produces no physical model, and neither recovers the
fixture's actual mounting orientation. This document proposes a **parametric kinematic
model** calibrated via **non-linear least-squares fitting**, with both manual and
camera-assisted workflows. The result is a system that is accurate within the sampled
volume, degrades gracefully outside it, and stores a physical model that can be
visualized, debugged, and transferred between venues.

---

## 2. Competitor Survey

### 2.1 Comparison Table

| Dimension | grandMA3 | Avolites Titan + Capture | Chamsys MagicQ + MagicVis | ETC Eos + Augment3d | Hog 4 | Depence R3 | Maxedia |
|---|---|---|---|---|---|---|---|
| **Input data** | Manual XYZ + rotation entry per fixture | Manual XYZ in Capture visualizer | Manual XYZ in patch window | 3+ manual aim-at-point samples per fixture | None (external visualizer) | Manual XYZ + rotation alignment | Manual aim at surface corners |
| **Sample count** | 0 (position only) | 0 (position only) | 0 (position only) | 3+ (minimum 3, more for accuracy) | 0 | 0 (position only) | 2-4 (surface corners) |
| **Fit model** | Direct rigid-body FK from GDTF profile | Direct FK from personality file | Direct FK from heads file | Rigid-body kinematic chain solve (calibrated) | None | Direct FK from fixture profile | Simple geometric projection |
| **Representation** | XYZ + Euler rotation + GDTF geometry | XYZ + rotation + personality | XYZ + rotation + pan/tilt invert/swap flags | XYZ + orientation + calibration offsets | Raw DMX palettes | XYZ + rotation + alignment offsets | Per-fixture projection mapping |
| **UX workflow** | Place fixture in Stage view, verify visually in 3D | Place in Capture, sync via Synergy | Enter XYZ in patch, verify in MagicVis | Calibration wizard: aim at targets, confirm, see residuals | Focus by hand, store palettes | Place in 3D scene, fine-tune alignment | Aim at surface corners, store |
| **Drift handling** | Manual repositioning | Manual | Manual | Re-run calibration wizard | Re-focus manually | Re-run alignment | Re-calibrate corners |
| **Auto-focus from 3D** | Yes (Stage view click-to-aim) | Yes (Capture Focus-on-point) | Yes (Auto Focus palettes, trigonometric IK) | Yes (click 3D position, console computes pan/tilt from calibrated model) | No | Yes (3D scene IK) | No |
| **Camera/sensor assist** | No | No | No | No | No | BlackTrax / PSN tracking integration | No |
| **Fixture profile source** | GDTF / MVR (pan/tilt ranges, yoke geometry, beam origin) | Avolites personality files | Chamsys heads files | ETC fixture library | HES fixture library | GDTF / custom | Martin fixture library |

### 2.2 Key Observations

1. **No competitor does camera-assisted calibration.** Every system relies on manual
   fixture placement (XYZ + rotation) or manual aim-at-point. Camera beam detection for
   automated sample collection is novel.

2. **ETC Augment3d is the gold standard** for sample-based calibration. It uses 3+
   manual aim points to solve a rigid-body kinematic model. The operator sees real-time
   3D feedback during calibration and residuals after.

3. **Most systems assume the model is correct.** grandMA3, Avolites, Chamsys, and
   Depence all use "place the fixture, trust the profile" -- no fitting, no error
   analysis, no drift detection. This works in professional rigs where fixture positions
   are surveyed and profiles are validated, but fails in hobbyist/semi-pro setups where
   a fixture hanging from a makeshift truss may be off by 5-10 degrees.

4. **Tracking integration (BlackTrax/PSN)** in Depence shows the value of runtime
   position data, but is used for moving targets, not for calibration itself.

### 2.3 SlyLED v2 Positioning Assessment

| Dimension | Position | Justification |
|---|---|---|
| **Input data** | **Exceeds field** | Camera-assisted automated sampling eliminates operator error. Manual fallback for camera-less setups. Both paths feed the same parametric solver. |
| **Fit model** | **Matches ETC, exceeds others** | Non-linear parametric fit (Levenberg-Marquardt) recovers mount orientation from samples, same class as Augment3d's kinematic solve. All others use zero-sample direct FK. |
| **Representation** | **Matches field** | Per-fixture parametric model (position, mount rotation, pan/tilt ranges, DMX mapping) stored as JSON. Equivalent to GDTF placement + calibration offsets. |
| **UX workflow** | **Matches ETC** | Wizard with progress feedback, residual reporting, per-sample test. Camera workflow adds unattended operation that ETC lacks. |
| **Drift handling** | **Exceeds field** | Camera-assisted re-calibration is a one-button operation. Competitors require full manual re-focus. Future: automated drift detection via periodic beam check. |
| **Auto-focus from 3D** | **Matches field** | Closed-form IK from the parametric model, same capability as grandMA3/Chamsys/Depence. |
| **Camera/sensor assist** | **Exceeds field** | No competitor offers camera-based calibration. Depence supports BlackTrax for tracking but not for calibration. |
| **Profile data** | **Concedes to grandMA3/Depence (acceptable)** | GDTF profiles include full yoke geometry (axis offsets, beam origin). SlyLED profiles store pan/tilt ranges but not yoke dimensions. Concession is acceptable: yoke offset is typically 50-150mm, producing sub-degree error at stage distances (>3m). If OFL/GDTF import is added later, yoke geometry can be folded into the kinematic model without changing the calibration pipeline. |

---

## 3. Mathematical Model

### 3.1 Fixture Kinematic Model

A moving head is a 2-DOF serial manipulator (pan-tilt gimbal). The beam direction is
determined by the mounting frame and the two joint angles.

**Coordinate system** (matches SlyLED stage conventions):
- X = stage width (stage right = 0, increases toward stage left)
- Y = depth (back wall = 0, increases toward audience)
- Z = height (floor = 0, increases toward ceiling)

**Parameters per fixture** (6 unknowns):

| Parameter | Symbol | Description | Source |
|---|---|---|---|
| Mount yaw | `psi` | Rotation of fixture base around Z axis (degrees) | Calibration solve |
| Mount pitch | `phi_m` | Forward/backward tilt of mount (degrees) | Calibration solve |
| Mount roll | `rho` | Twist of mount around forward axis (degrees) | Calibration solve (usually ~0) |
| Pan offset | `theta_0` | DMX value where pan axis is at geometric zero (normalized 0-1) | Calibration solve |
| Tilt offset | `phi_0` | DMX value where tilt axis is at geometric zero (normalized 0-1) | Calibration solve |
| Pan sign | `s_pan` | +1 or -1, direction of increasing DMX pan | Axis probe or exhaustive search |
| Tilt sign | `s_tilt` | +1 or -1, direction of increasing DMX tilt | Axis probe or exhaustive search |

**Fixed parameters** (from profile or layout):

| Parameter | Source |
|---|---|
| Position (x, y, z) | Layout fixture placement |
| Pan range (degrees) | DMX profile `panRange` |
| Tilt range (degrees) | DMX profile `tiltRange` |
| Mounted inverted | Layout fixture `mountedInverted` flag |
| Home target (x, y, z) | Layout fixture `homeTarget` vector |

### 3.2 Home Position (Aim Vector as Home Preset)

Every mover fixture in the layout already stores an **aim vector** -- a `rotation`
field ([rx, ry, rz] in degrees) that defines the fixture's rest aim direction, editable
by dragging the red aim-point sphere in the 3D viewport or via the
`PUT /api/fixtures/<fid>/aim` endpoint (which accepts either `rotation` or `aimPoint`
and converts between them). This existing vector is the **home position preset**.

Currently this vector is used only for 3D visualization (beam cone direction). The v2
calibration formalizes it as the **home position** -- the safe, useful position the
fixture should aim at whenever it powers on, receives a stop command, or has no active
cue. This replaces the current behavior where fixtures power up at DMX 0/0 (pan=0,
tilt=0), which is arbitrary and often points the beam into the rigging, the audience,
or the ceiling.

**Existing infrastructure** (no new fields needed):
- `fixture.rotation` -- [rx, ry, rz] degrees, already stored in layout data
- `_rotToAim(rot, pos, dist)` in the SPA -- converts rotation to aim point
- `/api/fixtures/<fid>/aim` -- accepts `aimPoint` [x,y,z] and converts to rotation
- Red aim-point sphere in the 3D viewport -- draggable via TransformControls gizmo

**New behavior** (what changes):
- On DMX engine start (`_apply_profile_defaults`), each mover's pan/tilt channels are
  set to the IK-computed values for its `rotation` aim vector instead of the profile
  channel defaults. Dimmer is set to 0 (beam off but pre-aimed at the home position).
- On show stop / runner stop / blackout, movers return to home position at dimmer 0.
- On calibration complete, if the fixture has no explicit aim vector set (default
  [0,0,0]), default it to the calibration centroid (average of all sample stage
  positions) so the fixture has a meaningful rest position.
- The SPA labels this vector as **"Home Position"** in the fixture edit UI to make its
  purpose clear.

**Default when unset**: If `rotation` is [0,0,0] and no calibration exists, fall back
to the current `centerPan` / `centerTilt` from the calibration data or the profile
defaults. If nothing is available, use (pan=0.5, tilt=0.5) as the last resort -- at
least this points the fixture roughly forward/horizontal rather than at a mechanical
endstop.

### 3.3 Forward Kinematics

Given normalized DMX values `pan_n` and `tilt_n` in [0, 1]:

```
theta_pan  = s_pan  * (pan_n  - theta_0) * pan_range_deg
theta_tilt = s_tilt * (tilt_n - phi_0)   * tilt_range_deg
```

Rotation matrices (clockwise pan from above, downward-positive tilt):

```
R_mount = R_z(psi) * R_x(phi_m) * R_y(rho)

R_pan(theta) = |  cos(theta)   sin(theta)  0 |
               | -sin(theta)   cos(theta)  0 |
               |      0            0       1 |

R_tilt(phi)  = | 1      0          0     |
               | 0   cos(phi)   sin(phi) |
               | 0  -sin(phi)   cos(phi) |
```

Verification: `R_pan * R_tilt * [0, 1, 0]^T` yields
`[sin(theta)*cos(phi), cos(theta)*cos(phi), -sin(phi)]`, matching the codebase's
`pan_tilt_to_ray`.

Beam direction (home = +Y forward):

```
d = R_mount * R_pan(theta_pan) * R_tilt(theta_tilt) * [0, 1, 0]^T
```

Expanding with identity mount (`psi = phi_m = rho = 0`):

```
d = [ sin(theta_pan) * cos(theta_tilt),
      cos(theta_pan) * cos(theta_tilt),
     -sin(theta_tilt) ]
```

**Ray-surface intersection**: The beam hits surfaces at `P + t * d` where `P` is the
fixture position and `t > 0`. Intersect with floor plane (Z = floor_z), walls, etc.

### 3.4 Inverse Kinematics (Closed-Form)

Given fixture position `P` and target point `T`:

```
v = T - P                          (direction vector, not normalized)
v_local = R_mount^(-1) * v         (transform to fixture-local frame)

theta_pan  = atan2(v_local.x, v_local.y)
theta_tilt = atan2(-v_local.z, sqrt(v_local.x^2 + v_local.y^2))
```

Convert back to DMX:

```
pan_n  = theta_0 + s_pan  * theta_pan  / pan_range_deg
tilt_n = phi_0   + s_tilt * theta_tilt / tilt_range_deg
```

Clamp to [0, 1]. **No grid lookup, no affine, no round-trip mismatch.**

**Singularity**: When the target is directly above or below the fixture
(`sqrt(v_local.x^2 + v_local.y^2) -> 0`), pan becomes undefined. Guard with
`dist_xy > epsilon` and fall back to current pan value.

### 3.5 Parameter Estimation (Calibration Solver)

Given N calibration samples `{(pan_n_i, tilt_n_i, T_i)}` where `T_i` is the known
stage position the beam hits:

**Residual function** for sample i:

```
d_i = forward_kinematics(pan_n_i, tilt_n_i, params)    // beam direction from model
v_i = normalize(T_i - P)                                // observed direction to target

residual_i = [angle_between(d_i, v_i)]                  // angular error in radians
```

Or equivalently, decomposed into pan and tilt residuals:

```
(pan_predicted, tilt_predicted) = inverse_kinematics(T_i, params)
residual_pan_i  = pan_n_i  - pan_predicted
residual_tilt_i = tilt_n_i - tilt_predicted
```

This gives 2N residuals for the unknown parameters.

**Discrete vs. continuous parameters**: `s_pan` and `s_tilt` are discrete (+/-1) and
**cannot be optimized by gradient-based solvers** like LM. They are determined
separately:
- **Option A (preferred)**: Axis probing -- the existing `calibrate_fixture_orientation`
  logic nudges pan/tilt by a small delta and observes pixel movement direction to
  determine signs. This runs once before the LM solve.
- **Option B**: Exhaustive search -- run LM for all 4 sign combinations
  (`++, +-, -+, --`) and keep the fit with lowest residual. 4 LM runs is cheap (< 1s).

After signs are fixed, LM optimizes the **5 continuous parameters**: `psi, phi_m, rho,
theta_0, phi_0`. This gives 2N residuals for 5 unknowns (or 3 unknowns if mount
rotation is frozen and only offsets are fitted).

**Solver**: Levenberg-Marquardt (`scipy.optimize.least_squares`). LM is preferred over
Gauss-Newton for robustness to poor initialization. The Jacobian can be computed
analytically (chain rule through the rotation matrices) or numerically (finite
differences on the residual function -- simpler, sufficient for 5 parameters).

**Minimum samples**:
- 3 unknowns (mount frozen, offsets only): N >= 2 non-collinear targets
- 5 unknowns (full solve): N >= 3 non-collinear targets (6 residuals, 5 unknowns)
- **Recommended: N >= 6** for redundancy and error detection

**Initial guess**: `psi = 0, phi_m = 0, rho = 0, theta_0 = 0.5, phi_0 = 0.5`.
For inverted mounts: `phi_m = 180`. The current orientation calibration
(`calibrate_fixture_orientation`) already discovers signs and approximate offsets --
these become a warm start for LM.

**Convergence criteria**: Residual RMS < 0.5 degrees. Report per-sample residuals so
the operator can identify and exclude bad samples.

### 3.6 Residual Analysis and Quality Metrics

After fitting, report:

| Metric | Threshold | Meaning |
|---|---|---|
| RMS angular error | < 1.0 deg | Good fit |
| Max single-sample error | < 3.0 deg | No outlier samples |
| Condition number of Jacobian | < 100 | Samples have good spatial spread |
| Pan range residual | < 5% of profile | Profile range is accurate |

If max error > 3.0 degrees, flag the outlier sample and offer to exclude + re-fit.

---

## 4. Migration Plan

### 4.1 Data Model Changes

**Current** (`mover_calibrations.json`):

```json
{
  "fixtureId": {
    "cameraId": 1,
    "color": [0, 255, 0],
    "samples": [[pan, tilt, px, py], ...],
    "grid": { "panSteps": [...], "tiltSteps": [...], "pixelX": [[...]], "pixelY": [[...]] },
    "boundaries": { "panMin": 0.1, "panMax": 0.9, "tiltMin": 0.3, "tiltMax": 0.8 },
    "centerPan": 0.5,
    "centerTilt": 0.6,
    "timestamp": 1713400000
  }
}
```

**Proposed v2** (additive -- existing fields preserved):

```json
{
  "fixtureId": {
    "version": 2,
    "model": {
      "mountYaw": 0.0,
      "mountPitch": 0.0,
      "mountRoll": 0.0,
      "panOffset": 0.5,
      "tiltOffset": 0.5,
      "panSign": 1,
      "tiltSign": -1,
      "panRangeDeg": 540,
      "tiltRangeDeg": 270
    },
    "fit": {
      "rmsErrorDeg": 0.4,
      "maxErrorDeg": 1.2,
      "sampleCount": 8,
      "conditionNumber": 12.3
    },
    "samples": [
      { "pan": 0.3, "tilt": 0.4, "stageX": 1200, "stageY": 3000, "stageZ": 0, "errorDeg": 0.3 },
      ...
    ],
    "centerPan": 0.5,
    "centerTilt": 0.5,
    "timestamp": 1713400000,
    "grid": null,
    "boundaries": null
  }
}
```

### 4.2 Backward Compatibility

1. **v1 data detected on load**: If `"version"` key is absent, treat as v1. The
   existing affine samples (`stageX/Y/Z + pan/tilt`) are fed into the LM solver to
   produce a v2 parametric model. The v1 grid is kept as a fallback for any fixture
   that fails the re-fit.

2. **Gradual migration**: On first access of each fixture's calibration (aim request,
   bake, etc.), auto-migrate v1 -> v2 in the background. No big-bang migration.

3. **Fallback chain for IK**:
   ```
   v2 parametric model (preferred)
     -> v1 affine_pan_tilt (if v2 fit failed or not yet migrated)
       -> geometric estimate (compute_initial_aim, no calibration)
   ```

### 4.3 API Changes

| Route | Change |
|---|---|
| `POST /api/calibration/mover/<fid>/start` | New `mode` parameter: `"auto"` (camera) or `"manual"` (jog). Default `"auto"`. |
| `GET /api/calibration/mover/<fid>` | Response includes `model`, `fit` quality metrics. |
| `POST /api/calibration/mover/<fid>/aim` | Uses v2 IK (closed-form) instead of grid inverse. |
| `POST /api/calibration/mover/<fid>/manual` | Samples fed to LM solver instead of raw affine storage. |
| `GET /api/calibration/mover/<fid>/residuals` | **New.** Returns per-sample errors for the current fit. |

---

## 5. Camera-Assisted Workflow

### 5.1 Overview

The camera-assisted workflow automates what ETC Augment3d requires manually: aiming the
fixture at known target points and recording the DMX values. The operator picks target
positions on the stage (or accepts defaults), the system sweeps the fixture to each
target using camera feedback, and records the DMX pan/tilt at which the beam lands on
each target.

### 5.2 Workflow Steps

```
Operator presses "Calibrate" on a mover fixture
  |
  v
1. Target selection (auto or manual)
   - Auto: generate N targets from known stage geometry (floor center, corners,
     walls if visible). Use layout dimensions + camera FOV to pick points that
     are both reachable by the fixture and visible to the camera.
   - Manual: operator clicks N points in the 3D viewport or enters coordinates.
   - Minimum N = 4 (for 4-param solve), recommended N = 6-8.
  |
  v
2. Dark reference capture
   - All movers blacked out.
   - Camera captures dark frame for beam detection baseline.
  |
  v
3. Per-target sweep (for each target i = 1..N):
   a. Compute initial aim estimate from current model (or geometric estimate
      for first calibration).
   b. Send DMX pan/tilt to initial estimate, wait for settle.
   c. Camera detects beam position.
   d. If beam not found: spiral search (existing discover() logic).
   e. Closed-loop convergence: compare beam pixel position to target pixel
      position, nudge pan/tilt, repeat until beam is within 20px of target.
   f. Record (pan_n, tilt_n, target_stage_x, target_stage_y, target_stage_z).
   g. Report progress to UI: "Sample 3/8 -- converged in 4 iterations"
  |
  v
4. Parameter fit
   - Run LM solver on collected samples.
   - Report fit quality (RMS error, per-sample residuals).
   - If RMS > threshold, flag bad samples, offer re-collection.
  |
  v
5. Verification sweep (optional, recommended)
   - Aim fixture at 2-3 targets NOT used in calibration.
   - Compare predicted beam position to actual camera detection.
   - Report verification error.
  |
  v
6. Save and activate
   - Store v2 calibration data.
   - Set fixture.moverCalibrated = true.
   - If fixture aim vector is unset ([0,0,0]), default to the calibration centroid.
   - Aim fixture at home position, dimmer 0 (parked safely).
```

### 5.3 Stage Geometry Sources

Accurate stage geometry is the foundation of target selection and IK ray-surface
intersection. The calibration system uses a **priority chain** of geometry sources:

1. **Point cloud (preferred when available)**. The existing `space_mapper.py` +
   `surface_analyzer.py` pipeline produces a merged point cloud from all camera nodes,
   then extracts structural surfaces via RANSAC (floor plane, wall planes, obstacle
   clusters). When a point cloud scan has been run (manually from the Layout tab or
   automatically before calibration), the calibration system uses the detected surfaces
   as ground truth for:
   - **Target placement**: Targets are placed on detected floor and wall surfaces at
     known 3D coordinates, not just on the layout's rectangular stage box.
   - **Ray-surface intersection**: The IK `ray_surface_intersect()` uses actual wall
     planes and floor Z from the point cloud rather than layout-entered dimensions.
   - **Obstacle avoidance**: Targets are not placed on or behind detected obstacles.
   - **Stage extent validation**: If the point cloud shows the stage is smaller or
     larger than the layout dimensions, warn the operator.

2. **Layout dimensions (fallback)**. The layout's `stageWidth`, `stageDepth`,
   `stageHeight` define a rectangular stage box with floor at Z=0. Used when no point
   cloud is available, or as an initial approximation before a scan.

3. **Camera FOV cone (minimum)**. Even without a point cloud or layout dimensions, the
   camera's field of view defines a visible floor region. Target placement can fall back
   to the camera-visible floor area.

**Workflow integration**: If no point cloud exists when the operator starts calibration,
the system should offer to run a quick environment scan first (20-30s with one camera).
The scan results are cached in `_space_scan` and reused across all fixture calibrations
in the session.

### 5.4 Target Selection Strategy

Good target placement is critical for solver accuracy. Targets should:
- Span a wide angular range (not all in a line or cluster)
- Be on known surfaces (floor, walls) so stage coordinates are known
- Be visible to the camera
- Be reachable by the fixture's beam

**Default auto-targets** (for a typical stage with floor visible to camera):

When a point cloud is available, targets are placed on the **actual detected surfaces**:
- Floor targets at the centroid and at points near the detected floor extent boundaries
- Wall targets where walls were detected (provides vertical angular diversity)
- Targets are spaced to maximize angular spread from each fixture's position

When only layout dimensions are available, fall back to a regular grid:

```
  [back wall]
     T5----T6
     |      |
     T3    T4
     |      |
     T1----T2
  [audience]
```

Where T1-T6 are floor points at known stage coordinates (derived from layout
`stageWidth`, `stageDepth`, camera FOV). Points are placed at 20% and 80% of each
dimension to avoid extreme angles.

### 5.5 Solving the #357 Discovery Bug

The current discovery issue (#357) stems from the initial aim estimate being wrong
because the mounting orientation is unknown for uncalibrated fixtures. The v2 workflow
addresses this:

1. **First calibration** (no prior model): Use the existing coarse grid scan (8x5 = 40
   positions) to find the beam anywhere in the camera FOV. This is slow (~40-80s) but
   only happens once per fixture.

2. **Re-calibration** (prior model exists): Use the prior model's IK to compute the
   initial aim. Since the model already captures mounting orientation, the beam should
   be found immediately at the predicted position. Discovery falls back to spiral only
   if the model prediction fails (fixture physically moved).

---

## 6. Manual Calibration Workflow (Camera-less)

For setups without a camera, the existing manual jog workflow is retained but upgraded:

1. Operator places 4+ stage markers (ArUco, tape marks, known positions).
2. For each marker, operator jogs pan/tilt sliders until the beam hits the marker.
3. Confirms each sample. Samples are `{pan_n, tilt_n, stageX, stageY, stageZ}`.
4. LM solver fits the parametric model (same solver as camera workflow).
5. Residuals displayed. Operator can re-jog any sample with high error.

**Improvement over v1**: The affine fit is replaced by the parametric solver. The
operator sees fit quality and can iteratively improve. With 6+ samples, the solver
provides redundancy that detects errors (e.g., operator aimed at the wrong marker).

---

## 7. UX Design

### 7.1 Calibration Wizard Modal (SPA)

The modal progresses through steps, showing status at each:

```
+---------------------------------------------------------+
|  Calibrate: "Front Mover Left"                    [X]   |
+---------------------------------------------------------+
|                                                         |
|  Mode: [Auto (Camera)] [Manual (Jog)]                   |
|                                                         |
|  Step 1/3: Collecting samples                           |
|  +-------------------------------------------------+    |
|  | Target | Stage Position | Status     | Error    |    |
|  |--------|---------------|------------|----------|    |
|  |   1    | (800, 1200, 0)| Converged  | 0.3 deg  |    |
|  |   2    | (2200, 1200, 0)| Converged | 0.5 deg  |    |
|  |   3    | (1500, 3000, 0)| Sweeping..| --       |    |
|  |   4    | (800, 3000, 0) | Pending   | --       |    |
|  +-------------------------------------------------+    |
|                                                         |
|  [==============================--------] 65%           |
|  "Converging on target 3 (iteration 2/10)"             |
|                                                         |
|  [Cancel]                                               |
+---------------------------------------------------------+
```

After fitting:

```
+---------------------------------------------------------+
|  Calibrate: "Front Mover Left"                    [X]   |
+---------------------------------------------------------+
|                                                         |
|  Calibration complete                                   |
|                                                         |
|  Fit quality: GOOD                                      |
|  RMS error: 0.4 deg    Max error: 1.1 deg              |
|  Samples: 6/6 used     Condition: 12.3                  |
|                                                         |
|  Mount orientation recovered:                           |
|    Yaw: -2.3 deg  Pitch: 178.1 deg (inverted)          |
|    Roll: 0.4 deg  Pan offset: 0.512  Tilt offset: 0.108|
|                                                         |
|  Per-sample residuals:                                  |
|  | # | Target          | Pan err | Tilt err | Total  | |
|  |---|-----------------|---------|----------|--------| |
|  | 1 | (800, 1200, 0)  | 0.2 deg | 0.1 deg  | 0.3 d  | |
|  | 2 | (2200, 1200, 0) | 0.4 deg | 0.3 deg  | 0.5 d  | |
|  | ...                                                  |
|                                                         |
|  [Verify]  [Accept]  [Re-collect sample 2]  [Cancel]    |
+---------------------------------------------------------+
```

### 7.2 Error States

| Condition | UX |
|---|---|
| Beam not found during discovery | "Beam not detected. Check: fixture powered on, DMX address correct, beam color visible to camera. [Retry] [Switch to manual]" |
| Fit RMS > 3 degrees | "Poor fit quality. Samples may be inaccurate or fixture may have moved during calibration. [Show residuals] [Re-collect worst sample] [Accept anyway]" |
| Single outlier sample | Highlight row in red. "[Exclude and re-fit] [Re-collect this sample]" |
| Camera offline mid-calibration | "Camera connection lost. [Retry] [Switch to manual with samples collected so far]" |

### 7.3 Re-calibration UX

Fixtures with existing calibration show a status indicator:

- Green check: calibrated, fit quality good
- Yellow warning: calibrated, fit quality marginal (RMS 1-3 deg)
- Red cross: calibration failed or never done

One-button re-calibration uses the existing model as a warm start, so discovery is
near-instant and the full workflow completes in under 60 seconds.

---

## 8. Implementation Plan

### Phase 1: Parametric Model + Manual Solver (no camera changes)

1. Add `ParametricFixtureModel` class to `mover_calibrator.py`:
   - `forward(pan_n, tilt_n) -> (dx, dy, dz)` beam direction
   - `inverse(target_xyz) -> (pan_n, tilt_n)` closed-form IK
   - `fit(samples) -> model_params` LM solver
   - `residuals(samples) -> per_sample_errors`

2. Update manual calibration path (`/api/calibration/mover/<fid>/manual`):
   - Feed samples to LM solver instead of raw affine storage
   - Return fit quality metrics in response

3. Update all IK consumers to use `model.inverse()`:
   - `_aim_to_pan_tilt` in `parent_server.py`
   - Bake engine mover aiming
   - Gyro controller mover output

4. Formalize the existing fixture `rotation` aim vector as home position:
   - Label the aim vector in the SPA layout editor as "Home Position"
   - Update `_apply_profile_defaults` to compute pan/tilt from `rotation` via IK
     and write to DMX on engine start (dimmer=0, fixture pre-aimed but dark)
   - On show stop / blackout, return movers to home position
   - Default aim vector to calibration centroid on calibration complete

5. Add v1 -> v2 auto-migration on calibration data load.

6. Update tests: `test_mover_calibration.py` + new `test_parametric_model.py`.

### Phase 2: Camera-Assisted Workflow Upgrade

1. Integrate point cloud as primary stage geometry source:
   - Before calibration, check for existing `_space_scan` data
   - If absent, offer quick environment scan (space_mapper pipeline)
   - Use detected floor/wall surfaces for target placement and ray intersection
   - Fall back to layout dimensions when no point cloud available
2. Implement target selection (auto from point cloud surfaces or layout geometry + manual click).
3. Replace discovery spiral with model-predicted initial aim (for re-calibration).
4. Implement per-target convergence loop (aim -> detect -> nudge -> record).
5. Wire up progress reporting to the calibration status API.
6. Verification sweep after fitting.

### Phase 3: UX + Polish

1. Calibration wizard modal with progress table and residual display.
2. Fit quality indicators on fixture cards.
3. "Re-collect sample" flow (exclude + re-aim + re-fit).
4. One-button re-calibration.

---

## 9. Test Plan

### 9.1 Synthetic Tests (no hardware)

| Test | Description | Assertions |
|---|---|---|
| Forward/inverse round-trip | For N random mount orientations and targets, verify `inverse(forward(pan, tilt)) == (pan, tilt)` within epsilon | Round-trip error < 0.01 deg |
| LM solver convergence | Generate synthetic samples from a known model with Gaussian noise, verify solver recovers parameters | Parameter error < 1% of true value |
| Inverted mount | Model with `mountPitch = 180`, verify IK produces correct pan/tilt | Matches hand-calculated values |
| Side-mount | Model with `mountRoll = 90`, verify IK | Matches hand-calculated values |
| Minimum samples | Verify solver works with exactly 4 samples (4-param) and exactly 6 (6-param) | Converges, RMS < 1 deg |
| Outlier detection | Add one bad sample (10 deg error), verify residual analysis flags it | Outlier identified, RMS improves after exclusion |
| v1 migration | Load v1 calibration data, verify auto-migration produces valid v2 model | v2 model IK matches v1 affine within 2 deg |
| Extrapolation | Test IK for targets outside the sampled region | Degrades gracefully (no NaN, no >180 deg output), clamps to [0,1] |
| Home position IK | Set fixture aim vector on a calibrated fixture, verify IK produces correct pan/tilt for home position | Round-trip: aim at home, verify beam lands within 2 deg of target |
| Home position default | Complete calibration with aim vector at [0,0,0], verify centroid is set | Fixture rotation updated to point at average of sample stage positions |
| Point cloud targets | Generate targets from a synthetic point cloud (floor + 2 walls), verify targets span angular range | At least 1 target per detected surface, angular spread > 60 deg |
| Fallback to layout | No point cloud available, verify targets generated from layout stageWidth/stageDepth | 6 targets on rectangular grid |

### 9.2 Hardware Tests (with camera)

| Test | Description |
|---|---|
| Full auto-calibration | Run camera-assisted workflow end-to-end on a real moving head, verify fit quality |
| Re-calibration speed | Run calibration on an already-calibrated fixture, verify < 60s |
| Two-fixture sequential | Calibrate two movers sequentially, verify no cross-contamination |
| Manual fallback | Start auto, cancel mid-way, switch to manual, complete calibration |
| Drift simulation | Physically rotate fixture 5 degrees, re-calibrate, verify new model |

### 9.3 Regression Against v1

| Test | Description |
|---|---|
| Existing manual samples | Load v1 manual calibration data, verify v2 model produces equivalent aim |
| Bake output | Run timeline bake with v1 and v2 calibration, compare DMX output frame-by-frame |
| Gyro controller | Verify gyro-controlled mover aiming works with v2 model |

---

## 10. Open Questions

1. **Yoke geometry from OFL/GDTF**: If we later import GDTF profiles, the yoke offset
   (distance from pan axis to tilt axis, and from tilt axis to beam origin) becomes
   available. Should the kinematic model include these from the start (set to zero by
   default) or add them later? **Recommendation**: include in the model struct now,
   default to zero, so the forward/inverse code doesn't need to change later.

2. **16-bit pan/tilt**: Some fixtures use 16-bit (coarse + fine channels) for pan and
   tilt. The model works with normalized [0, 1] values, so 8-bit vs 16-bit is a
   concern only at the DMX write layer, which already handles this. No model changes
   needed.

3. **Continuous rotation fixtures**: Some movers have continuous (>360 deg) pan rotation.
   The IK must handle angle wrapping. `atan2` naturally returns [-180, 180], so for a
   540 deg range fixture, the model may need to pick the shortest path or respect a
   preferred rotation direction.

4. **Multi-camera triangulation**: With 2+ cameras, beam position can be triangulated in
   3D without a depth model. This is a future enhancement that doesn't change the
   calibration model, only the sample collection pipeline.

---

## 11. References

- Corke, P. *Robotics, Vision and Control* (2011), Ch. 7 -- serial-link manipulators
- Hartley, R. & Zisserman, A. *Multiple View Geometry* (2003) -- LM for parameter estimation
- GDTF specification (gdtf-share.com) -- fixture geometry model
- MVR specification -- stage coordinate exchange format
- Open Fixture Library (open-fixture-library.org) -- pan/tilt range data
- ETC Augment3d documentation (etcconnect.com)
- grandMA3 user manual (help.malighting.com) -- Stage view, fixture setup
- SlyLED issue #357 -- discovery beam detection failure
- SlyLED issue #484 -- stage-space architecture review
- SlyLED issue #486 -- live test bug log (round-trip mismatch)
