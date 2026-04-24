# Gemini Review v2: mover calibration — with parent_server.py subset

_Generated The system cannot accept the date entered.
Enter the new date: (yy-mm-dd) via `tools/gemini_review_mover_cal_v2.py`._

Adds calibration-relevant sections of `parent_server.py` to the v1 payload so the two previously-unverifiable issues (#653 phase budgets, #626 multi-snapshot ArUco) can be confirmed.

---

## 1. Verification of #626 and #653

### #626 (markers-mode pre-check)

**VERIFIED.** The markers-mode calibration path correctly implements multi-snapshot aggregation with forced blackout.

-   **Call site:** `parent_server.py:4226` in `_mover_cal_thread_markers_body` calls `_aruco_multi_snapshot_detect` with `max_snapshots=3` and passes the `blackout_bridge_ip`.
-   **Aggregation:** `parent_server.py:6193` (`_aruco_multi_snapshot_detect`) loops up to `max_snapshots` times (`parent_server.py:6207`).
-   **Best-per-ID:** It uses a `best_per_id` dictionary and selects the detection with the largest corner perimeter, overwriting previous detections for the same marker ID (`parent_server.py:6223-6228`).
-   **Forced Blackout:** When `blackout_bridge_ip` is provided, it calls `_mcal._hold_dmx` with a 512-byte zero buffer before each snapshot attempt (`parent_server.py:6209-6211`).

### #653 (per-phase time budgets)

**VERIFIED.** The legacy BFS calibration path (`_mover_cal_thread_body`) implements per-phase time budgets, blackout-on-timeout, and a tier-2 handoff flag.

-   **Budget Constants:** Defined at `parent_server.py:4713-4717`.
    -   `CAL_BUDGET_DISCOVERY_BATTLESHIP_S = 60.0`
    -   `CAL_BUDGET_DISCOVERY_COLOUR_FALLBACK_S = 90.0`
    -   `CAL_BUDGET_MAPPING_S = 120.0`
    -   `CAL_BUDGET_FIT_S = 10.0`
    -   `CAL_BUDGET_VERIFICATION_S = 30.0`
-   **Timeout Trigger:** The `_phase_timeout` helper function (`parent_server.py:4726`) is called when a discovery phase exceeds its budget (`parent_server.py:4976`, `5000`).
-   **Blackout-on-Timeout:** `_phase_timeout` calls `_cal_blackout` (`parent_server.py:4735`), which sends a 512-byte zero DMX frame and releases the calibration lock (`parent_server.py:4721-4725`).
-   **Tier-2 Handoff:** `_phase_timeout` sets `job["pendingTier2Handoff"] = True` (`parent_server.py:4730`). The mapping phase also sets this flag on timeout but does not abort the run (`parent_server.py:5125`).

---

## 2. New bugs (not in the P1-P3 list above)

### P1: Universe-wide blackout on thread exit clobbers other fixtures

-   **file:line**: `parent_server.py:4722` (`_cal_blackout`), `parent_server.py:4672` (cancel path), `parent_server.py:4686` (error path).
-   **Reproduction**: Start calibrating mover A on universe 1. While it's running, use a DMX tool to turn on mover B on the same universe. Let mover A's calibration finish, fail, or be cancelled. The `_cal_blackout()` function is called, which sends `[0]*512` to the entire universe. Mover B will be blacked out unexpectedly.
-   **Fix**: The `_cal_blackout` function and other thread-exit blackout calls should perform a *targeted* blackout on only the fixture being calibrated, identical to the logic in `/api/calibration/mover/<fid>/cancel` (`parent_server.py:5608-5616`). This requires passing the fixture object or its DMX properties (address, channel count) to the blackout function.

### P2: ArUco pre-scan blackout can interfere with other running calibrations

-   **file:line**: `parent_server.py:6209-6211` inside `_aruco_multi_snapshot_detect`.
-   **Reproduction**: This is a variant of the P1 bug above. Start calibrating mover A on universe 1. In a separate browser tab, start a markers-mode calibration for mover B on universe 1. The pre-scan for mover B will call `_aruco_multi_snapshot_detect`, which sends a universe-wide blackout. This will interfere with the DMX frames being sent by the calibration thread for mover A. The system lacks a universe-level lock to prevent this.
-   **Fix**: The blackout inside `_aruco_multi_snapshot_detect` should not be a full-universe zeroing. A better strategy is to specifically black out any *other* DMX movers on the same universe that are *not* currently under a calibration lock, leaving the one under calibration untouched. Alternatively, if no other calibration is running, it can black out all movers. This requires more state awareness. A simpler, safer fix is to disallow concurrent calibrations entirely.

### P2: Manual aim API bypasses calibration lock

-   **file:line**: `parent_server.py:5843` (`/api/calibration/mover/<fid>/aim`).
-   **Reproduction**: Start a calibration for fixture `fid=10`. While the calibration thread is running, send a POST request to `/api/calibration/mover/10/aim` with a target position. The API route will write DMX values to the fixture, directly conflicting with the DMX frames being sent by the calibration thread. This can corrupt samples or cause convergence loops to fail.
-   **Fix**: The `/aim` route handler must check if the target fixture is locked for calibration before writing DMX. Add `if _fixture_is_calibrating(fid): return jsonify(err="Fixture is busy calibrating"), 409` near the top of the function.

### P3: `_set_calibrating` lock is not persisted

-   **file:line**: `parent_server.py:9525-9542`.
-   **Reproduction**: Start a long calibration (e.g., legacy BFS with many samples). While it is running, restart the `parent_server.py` process. On startup, the server iterates through all fixtures and unconditionally removes the `isCalibrating` flag (`parent_server.py:9541`). The background calibration thread is killed, but the fixture is now unlocked. If the last DMX frame sent by the thread was non-zero, the DMX engine may continue sending it, leaving the fixture lit and aimed at a specific point with no indication that it's "stuck".
-   **Fix**: This is arguably correct behavior (a restart should clear locks), but the consequence is a potentially "hot" fixture. The fix is for the DMX engine itself to black out all movers on startup before beginning its normal 40Hz transmission, ensuring a clean slate after a restart.

---

## 3. Cross-file correctness

The most significant cross-file issue is the inconsistency in blackout implementation, which leads to the P1 bug described above.

-   **Disagreement**: The `/cancel` API endpoint in `parent_server.py:5608` performs a **targeted blackout**, zeroing only the channels for the specific fixture being cancelled. In contrast, the calibration threads' own cleanup paths (`_cal_blackout` at `parent_server.py:4722`, called from all three calibration modes on exit) and the ArUco pre-scan (`parent_server.py:6209`) perform a **universe-wide blackout** by sending 512 zeros.
-   **Impact**: The library code in `mover_calibrator.py` (`_hold_dmx`) is used for both, but the `channels` payload it receives from `parent_server.py` is fundamentally different depending on the call path. The library itself is correct, but the caller (`parent_server.py`) uses it inconsistently, leading to dangerous side effects (clobbering other fixtures).

---

## 4. Prioritised new-bug fix list

1.  **Fix P1: Make all calibration blackouts targeted, not universe-wide.**
    -   **File**: `parent_server.py`
    -   **Change**: Modify the `_cal_blackout` function (and similar calls in error/cancel paths for all three calibration threads) to accept a fixture object. Inside the function, get the fixture's DMX address and channel count, and use `engine.get_universe(uni).set_channels(addr, [0] * ch_count)` instead of `_mcal._hold_dmx(bridge_ip, [0]*512, ...)`. This mirrors the safe implementation already present in the `/cancel` handler.

2.  **Fix P2: Prevent ArUco pre-scan from interfering with other fixtures.**
    -   **File**: `parent_server.py`
    -   **Change**: In `_aruco_multi_snapshot_detect` (`parent_server.py:6209`), replace the universe-wide blackout with a more intelligent one. The function should iterate through all DMX fixtures on the same universe as the camera's associated mover (if any) and black out only those that are *not* currently under a calibration lock (`isCalibrating != True`). This prevents it from clobbering an unrelated, active calibration.

3.  **Fix P2: Add calibration lock check to the manual aim API.**
    -   **File**: `parent_server.py`
    -   **Change**: At the top of `api_mover_cal_aim` (`parent_server.py:5843`), add a check:
        ```python
        if _fixture_is_calibrating(fid):
            return jsonify(err="Fixture is busy calibrating and cannot be aimed manually"), 409
        ```