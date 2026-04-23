# SlyLED Design Review
**Model:** gemini-robotics-er-1.6-preview  
**Date:** 2026-04-17 19:43  
**Files reviewed:** 5  
**Groups:** calibration-design

---

## Group: calibration-design — docs/mover-calibration-v2.md, desktop/shared/mover_calibrator.py, desktop/shared/dmx_profiles.py, desktop/shared/surface_analyzer.py, desktop/shared/space_mapper.py

This review evaluates the **SlyLED Mover Calibration v2** design document against the provided source code (`mover_calibrator.py`, `dmx_profiles.py`, etc.).

---

### 1. Mathematical Correctness
*   **Kinematic Model:** The transition to a parametric model ($R_{mount} \times R_{pan} \times R_{tilt}$) is mathematically sound. The use of $R_z(\psi) R_x(\phi_m) R_y(\rho)$ for the mount orientation correctly captures the 3-DOF mounting uncertainty.
*   **IK Formulas:** The closed-form IK using `atan2` is correct for the defined coordinate system. The singularity guard (`dist_xy > epsilon`) is present in the design but must be strictly implemented in the code to prevent `NaN` results when aiming vertically (Zenith/Nadir).
*   **Solver Configuration:** 
    *   **Residuals:** Using angular error (radians) as the residual is superior to DMX-space residuals, as it prevents the solver from over-weighting errors near the tilt poles.
    *   **Sign Handling:** The "Exhaustive Search" (Option B) for discrete signs ($s_{pan}, s_{tilt}$) is highly recommended over axis probing. 4 LM runs take milliseconds and eliminate the risk of pixel-detection noise during the "nudge" phase.
    *   **Jacobian:** Numerical differentiation (finite differences) is acceptable for 5 parameters, but ensure the step size for `epsilon` in LM is tuned to the precision of the DMX normalized values ($1/65535$).

### 2. Competitor Analysis Accuracy
*   **ETC Augment3d:** The assessment is correct. Augment3d is the benchmark for sample-based calibration.
*   **grandMA3:** Correct. MA3 relies heavily on the GDTF geometry tree. It assumes the user has correctly defined the "Fixture Type" and "Stage Position."
*   **Missing Competitor:** **disguise (d3)**. Their "OmniCal" system uses multiple cameras to calibrate projectors and could be considered a high-end parallel, though it targets projection rather than DMX fixtures.
*   **ETC ArUco:** It is worth noting that ETC recently introduced an ArUco-based position estimation for Augment3d (using a phone camera to find the fixture), but SlyLED’s approach of using the **fixture's own beam** to find the stage is a distinct and valid inversion of that logic.

### 3. Architecture and Feasibility
*   **Migration:** The fallback chain (`v2 -> v1 -> geometric`) is robust. 
*   **Codebase Integration:** `mover_calibrator.py` is currently a collection of helper functions. The design proposes a `ParametricFixtureModel` class. This is feasible, but the orchestrator (`parent_server.py`) must be updated to handle the state of this object.
*   **Race Conditions:** The biggest risk is the `_dmx_sender` callback. If the calibration loop is running at the same time as a sequence/show, there will be "flicker" or "fighting" for the P/T channels. The design needs a formal **"Calibration Lock"** in the DMX engine to suppress sequence output for the fixture under test.

### 4. Calibration Workflow
*   **Settle Times:** `SETTLE_BASE = 0.4s` is aggressive. While fine for small LED movers (e.g., 7x10W washes), professional arc-source spots (e.g., Viper, BMFL) have significant inertia and may require $1.2s - 2.0s$ to fully dampen oscillation. The **Adaptive Settle** logic in `_wait_settled` is a critical safety net here.
*   **Beam Detection:** The design assumes a single beam. For multi-instance fixtures (e.g., pixel-bars or "spiders"), the `beam_count` parameter in `calibrate_fixture_orientation` is a good start, but the solver should ideally target the **centroid** of all detected pixels for that fixture.
*   **Ambient Light:** This is the "silent killer" of camera calibration. The design should explicitly include a **"Confidence Score"** for beam detection. If the delta between "Light ON" and "Light OFF" frames is below a threshold, the sample should be auto-rejected.

### 5. Home Position Design
*   **Formalization:** Formalizing `rotation` as Home is excellent. It aligns with the "Locate" feature in Avolites/MA.
*   **Edge Case:** If a fixture is uncalibrated, the IK for Home will fail. The design correctly identifies the fallback to `centerPan/Tilt`, but I recommend a **"Home on Calibrate"** toggle: automatically updating the Home position to the stage center only if the user hasn't manually set one.

### 6. Point Cloud Integration
*   **Monocular Depth Risk:** Using `estimate_depth` for target placement is the weakest link. Monocular depth (even with ArUco scaling) has high variance. 
*   **Recommendation:** Prioritize **Floor-Plane Intersection**. If the camera sees the floor, use the homography-derived Z=0 plane for targets. Only use the point cloud for "Wall" targets if the RANSAC fit for that wall has high inlier counts (>200 points).

### 7. Gaps and Risks
*   **Gaps:** 
    1. **Non-linear Tilt:** Some cheap fixtures use non-linear mapping for tilt to maximize speed in the center. The model assumes linear degrees-per-DMX.
    2. **Thermal Drift:** LED movers expand as they heat up; arc movers shift as they cool. Calibration should ideally happen after a 10-minute "warm-up" sequence.
*   **Risks:**
    1. **Solver Divergence:** If the initial 4 samples are nearly collinear (e.g., all on a straight line on the floor), the Jacobian becomes ill-conditioned, and the mount rotation solve will explode.

---

### Overall Assessment: **READY TO IMPLEMENT (with minor revisions)**

The design is mathematically superior to the current v1 implementation and brings SlyLED closer to professional-grade tools like Augment3d.

#### Top 5 Risks
1.  **Ill-Conditioned Geometry:** Solver failure when calibration targets lack sufficient 3D spread (e.g., all targets on the floor in a straight line).
2.  **Monocular Depth Inaccuracy:** Placing 3D targets based on noisy depth data, leading to a "shifted" kinematic model.
3.  **Mechanical Backlash:** Fixture hysteresis (approaching a point from Left vs. Right) causing residual errors that LM cannot solve.
4.  **Ambient Interference:** False positives in beam detection due to stage reflections or high ambient light.
5.  **DMX Contention:** Conflicts between the calibration loop and the main DMX engine output.

#### Top 5 Recommendations
1.  **Target Diversity Enforcement:** The "Auto-target" logic must force at least one target to be at a significantly different height (e.g., a wall or a high riser) to accurately solve for `mountPitch`.
2.  **Calibration Lock:** Implement a `fixture.is_calibrating` flag in the DMX engine to prevent other cues from overriding the calibration DMX values.
3.  **Residual Visualization:** In the SPA, show the "Error Vectors" (lines from predicted to actual) in the 3D view. This helps users see if the fixture is physically loose.
4.  **Warm-up Routine:** Add a "Warm-up" step to the wizard that moves the fixture through its full range for 30 seconds to settle the motors and belts.
5.  **Robust Initial Guess:** Use the existing `compute_initial_aim` logic to seed the LM solver, but run the 4-sign exhaustive search by default to ensure the global minimum is found.