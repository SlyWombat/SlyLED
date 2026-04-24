# Gemini Review: mover calibration — 14 closed issues

_Generated The system cannot accept the date entered.
Enter the new date: (yy-mm-dd) via `tools/gemini_review_mover_cal.py`._

Issues reviewed: #651, #652, #653, #654, #655, #658, #659, #660, #661, #357, #625, #626, #627, #647

Source files: `mover_calibrator.py` (119,094 chars), `mover_control.py` (22,936 chars), `parametric_mover.py` (19,867 chars).

---

Excellent, this is a comprehensive and well-structured request. As a senior Python / control-systems engineer, I'll dive into the code and the context provided. Here is my review.

---

### 1. Per-issue verification

| Issue | Shipped? | File:line evidence | Gap or bug found (if any) |
| :--- | :--- | :--- | :--- |
| #651 | yes | `mover_calibrator.py:1193` | The `_dark_reference` primitive exists. The call site is in the (unprovided) thread body, but its existence supports the claim. |
| #652 | yes | `parametric_mover.py:377` | `fit_model` correctly uses `force_signs` to narrow the `sign_combos` list to a single entry, skipping the 4-way search. |
| #653 | **no** | N/A | The `CAL_*_BUDGET_S` constants and the per-phase timeout logic are not present in the provided `mover_calibrator.py`. This logic likely lives in the unprovided `parent_server.py` thread body, so it cannot be verified. |
| #654 | yes | `mover_calibrator.py:1003` | `verification_sweep_parametric` exists and uses `model.inverse`. The gating of `moverCalibrated` happens in the caller, but the primitive is correct. |
| #655 | yes | `mover_calibrator.py:40, 1113` | `OVERSAMPLE_N` constants and `_beam_detect_oversampled` are present. `map_visible` at `mover_calibrator.py:1809` correctly calls it. |
| #658 | yes | `mover_calibrator.py:821` | `battleship_discover` has the `if reject_reflection:` block which calls `_beam_detect_flash` after `_confirm` passes. |
| #659 | yes | `mover_calibrator.py:504` | `pick_calibration_targets` correctly imports and uses `camera_floor_polygon` to filter candidates. |
| #660 | yes | `mover_calibrator.py:836` | `battleship_discover` has the `if refine:` block which calls `_refine_battleship_hit`. |
| #661 | yes | `mover_calibrator.py:739` | `battleship_discover` calls `_adaptive_coarse_steps` to determine grid density when not explicitly provided. |
| #357 | yes | `mover_calibrator.py:717` | The `battleship_discover` function exists as the modern replacement for the legacy `discover` function. |
| #625 | yes | `mover_calibrator.py:901` | `converge_on_target_pixel` contains the `if beam is None:` block with `bracket_step *= 0.5` logic. |
| #626 | **no** | N/A | The `_aruco_multi_snapshot_detect` function and pre-check blackout logic are not in the provided files; cannot be verified. |
| #627 | yes | `mover_calibrator.py:270` | `_set_mover_dmx` has the `#627` comment and the loop that pre-zeros the fixture's DMX address range. |
| #647 | yes | `mover_control.py:199, 411` | `MoverControlEngine` has `get_engine_health` and `_note_dropped_write` is called from `_write_dmx` when the engine is not running. |

-   **#653 & #626:** These issues cannot be verified as their implementation lives in the main calibration thread body, which was not provided. The necessary helper functions exist in `mover_calibrator.py`, but their call sites and the overarching control flow (like timeout handling) are missing from this review context.

---

### 2. Cross-issue interactions / bugs

Here are the bugs and risks identified from reviewing the code and the interaction between these fixes.

-   **Severity:** P1
-   **File:line:** `mover_calibrator.py:905`
-   **Reproduction:** Calibrate a fixture with a 16-bit pan/tilt profile. During the markers-mode convergence (`converge_on_target_pixel`), have the beam miss the camera view. The bracket-and-retry logic from #625 will engage.
-   **Bug:** The `BRACKET_FLOOR` is hardcoded to `1.0 / 255.0`, which is one 8-bit DMX step. For a 16-bit fixture, this floor is `(1/255) * 65535 ≈ 257` DMX units. The bracket logic will give up while the step size is still over 250 DMX units, prematurely aborting recovery on high-precision fixtures.
-   **Fix suggestion:** The `converge_on_target_pixel` function should be aware of the fixture's pan/tilt resolution.
    ```python
    # In converge_on_target_pixel, before the loop:
    pan_bits = next((c.get("bits", 8) for c in profile.get("channels", []) if c.get("type") == "pan"), 8)
    bracket_floor = 1.0 / (2**pan_bits - 1)
    
    # ... later, in the check ...
    if bracket_step < bracket_floor:
        # ...
    ```

-   **Severity:** P2
-   **File:line:** `mover_calibrator.py:698, 1139`
-   **Reproduction:** Run calibration. The `battleship_discover` coarse-to-fine refine pass (#660) will call `_beam_detect_oversampled` (#655) with `n=2`.
-   **Bug:** The `_median` helper inside `_beam_detect_oversampled` correctly computes the median for odd-length lists, but for even-length lists (like `n=2`), it computes the *average*. The issue description for #655 explicitly calls for a median filter to reject outliers, but an average is skewed by outliers. The call site in `_refine_battleship_hit` hardcodes `n=2`, triggering this behaviour and ignoring the module constant `OVERSAMPLE_N = 3`.
-   **Fix suggestion:** The `_refine_battleship_hit` function should respect the module constant, ensuring a true median is always taken.
    ```python
    # In mover_calibrator.py:698
    # Change this:
    over = _beam_detect_oversampled(camera_ip, cam_idx, color,
                                    center=True, n=2,
                                    gap_ms=30, min_valid=1)
    # To this (respecting the module default N=3):
    over = _beam_detect_oversampled(camera_ip, cam_idx, color,
                                    center=True, gap_ms=30)
    ```

-   **Severity:** P2
-   **File:line:** `mover_calibrator.py:705`
-   **Reproduction:** Calibrate on a rig with a high-resolution camera (e.g., 1920x1080).
-   **Bug:** The scoring function inside `_refine_battleship_hit` (#660) hardcodes an assumed camera resolution of roughly 640x480 (`600-bx`, `400-by`). On a 1080p camera, this logic will incorrectly score pixels, potentially preferring a beam near the (600, 400) coordinate over one at the true image center (960, 540).
-   **Fix suggestion:** The camera resolution should be passed into the function or queried.
    ```python
    # In _refine_battleship_hit signature:
    def _refine_battleship_hit(..., camera_resolution=(640, 480)):
    
    # In mover_calibrator.py:705
    # Change this:
    score = max(0.0, min(bx - 40, 600 - bx)) + max(0.0, min(by - 40, 400 - by))
    # To this:
    cam_w, cam_h = camera_resolution
    score = max(0.0, min(bx - 40, cam_w - 40 - bx)) + max(0.0, min(by - 40, cam_h - 40 - by))
    ```

-   **Severity:** P3
-   **File:line:** `parametric_mover.py:400`
-   **Reproduction:** Calibrate a fixture where the mount geometry creates a near-perfect mirror ambiguity, and do not use `verify_signs`.
-   **Bug:** The `fit_model` function correctly logs a warning when two sign combinations produce nearly identical RMS error. However, the log message is sent via the standard `logging` module. In a production environment, this might be missed by the operator. The issue description for #652 notes this is a silent failure. While the log is better than nothing, this condition should ideally be surfaced to the UI as a non-blocking warning or hint.
-   **Fix suggestion:** The `FitQuality` object should gain a `mirror_ambiguity: bool` flag that the caller can inspect and surface in the UI.
    ```python
    # In parametric_mover.py, inside fit_model
    best_quality.mirror_ambiguity = False
    if len(candidates) >= 2 and (candidates[1][0] - candidates[0][0]) < 0.2:
        best_quality.mirror_ambiguity = True
        # ... logging ...
    return best, best_quality
    ```

---

### 3. Documentation drift

The provided Appendix B is largely accurate but has several key mismatches with the code, likely due to rapid development.

-   **Appendix B says:** "Mapping (legacy BFS) ... dual-capture #655 ... median filtering across the capture pair".
    **Code does:** `map_visible` calls `_beam_detect_oversampled` with the default `OVERSAMPLE_N=3`, which correctly takes a median (`mover_calibrator.py:1809`). However, the `_median` helper function computes an *average* for `N=2` (`mover_calibrator.py:1139`).
    **Winner:** Code (for N=3). **Fix:** Docs should clarify that `N=3` is used for BFS, and the median is robust. The bug in the `N=2` case should be fixed separately.

-   **Appendix B says:** "Convergence (v2) ... Bracket-and-retry refine (#660) ... `BRACKET_FLOOR = 0.002`".
    **Code does:** The feature is bracket-and-retry (#625), not refine (#660). The constant `BRACKET_FLOOR` is `1.0 / 255.0` which is `~0.00392` (`mover_calibrator.py:892`).
    **Winner:** Code. **Fix:** Docs need to correct the issue number reference and the constant's value.

-   **Appendix B says:** "Model fit ... **after fit**, nudge pan by +0.02 ... Re-fit with `force_signs`".
    **Code does:** The intent of #652 was to run `verify_signs` *before* the main fit to provide `force_signs` to `fit_model`, collapsing the 4-way search into a single, unambiguous solve. The doc describes a less efficient post-fit confirmation and re-fit loop.
    **Winner:** Code's implied flow (pre-fit verification). **Fix:** The documentation flow for phase 6 needs to be rewritten to "verify signs → fit model once", not "fit model → verify signs → re-fit model".

-   **Appendix B says:** "Discovery (legacy) ... `SETTLE = 0.6 s` (legacy discovery uses the fixed constant; the #655 adaptive-settle machinery ... does not apply here)".
    **Code does:** The legacy `discover` function at `mover_calibrator.py:1697` calls `_wait_settled`, which *is* the adaptive settle machinery from #238 (a precursor to #655's changes).
    **Winner:** Code. **Fix:** Docs should be updated to state that legacy discovery *does* benefit from adaptive settle times.

-   **Appendix B says:** "Tuning-parameter reference ... `BRACKET_FLOOR | 0.002`".
    **Code does:** `BRACKET_FLOOR` is `1.0 / 255.0` (`~0.00392`).
    **Winner:** Code. **Fix:** Update the value in the documentation table.

---

### 4. Prioritised fix list

Here are the top 5 fixes I would recommend tackling immediately, in order of priority.

1.  **P1: Fix bracket-retry floor for 16-bit fixtures.**
    -   **File:** `mover_calibrator.py`
    -   **Fix:** Make `BRACKET_FLOOR` in `converge_on_target_pixel` aware of the fixture's DMX resolution to prevent premature recovery failure.

2.  **P2: Fix hardcoded camera resolution in refine pass.**
    -   **File:** `mover_calibrator.py`
    -   **Fix:** Pass camera resolution into `_refine_battleship_hit` to make its center-biasing score work correctly on all cameras, not just 640x480.

3.  **P2: Fix oversample average-vs-median bug.**
    -   **File:** `mover_calibrator.py`
    -   **Fix:** Change the `_refine_battleship_hit` call to `_beam_detect_oversampled` to use the module default `N=3`, ensuring it always gets a true median and behaves consistently with the BFS mapping phase.

4.  **P2: Correct the documentation for the `verify_signs` workflow.**
    -   **File:** Appendix B (documentation)
    -   **Fix:** Rewrite the "Model fit" section to accurately reflect that sign verification happens *before* the fit to resolve ambiguity, not after. This prevents operator confusion and correctly sets expectations.

5.  **P3: Surface mirror ambiguity from `fit_model` to the UI.**
    -   **File:** `parametric_mover.py`
    -   **Fix:** Add a `mirror_ambiguity: bool` flag to the `FitQuality` result so the UI can inform the operator when a fit is mathematically sound but potentially the physical mirror of the correct solution.