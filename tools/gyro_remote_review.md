# Gemini Review — Remote → Moving-Head Control Pipeline

_Generated 2026-05-08T15:28:14Z via `tools/gemini_review_remote_mover.py`._

**Model:** gemini-2.5-pro  
**Source:** 348,612 chars across 21 full files + parent_server.py subset  
**Closed issues pre-fed:** #802, #813, #819, #823, #825 (open: #826)

---

## Empirical verification & re-scoring — 2026-05-08

After Gemini produced the review below, the findings were spot-verified against
the actual code (Gemini's line citations drifted ~70 lines on every P1 — likely
tokenization-row drift; the bug *patterns* are real but cite the corrected
locations below before filing). The re-scoring inverts Gemini's priority order.

### Corrected file:line citations

| Gemini said | Actual location |
|---|---|
| `spatial_engine.py:461` | `spatial_engine.py:383-385` (in `compute_pan_tilt`) |
| `remote_orientation.py:464` | `remote_orientation.py:393-394` (in `_apply_quat`) |
| `mover_control.py:426` | `mover_control.py:325-355` (in `release`, body of try/except `_park_fn` → `_blackout_mover`) |

### P1 #1 (tilt inversion) — **OVERTURNED. Do NOT apply Gemini's fix.**

Bench-tested via direct call to `compute_pan_tilt` (no parent_server bootstrap):

```
fix=[0,0,0]    aim=[0,0,5000]    → tilt_norm=0.167  (internal -90°, "beam above horizon")
fix=[0,0,5000] aim=[0,0,0]       → tilt_norm=0.833  (internal +90°, "beam below horizon")
fix=[0,0,0]    aim=[0,5000,0]    → tilt_norm=0.500  (0°, horizon)
fix=[0,0,0]    aim=[0,5000,5000] → tilt_norm=0.333  (-45°, above)
fix=[0,0,5000] aim=[0,5000,0]    → tilt_norm=0.667  (+45°, below)
```

`tests/test_dmx_moving_heads.py:35-51` asserts exactly this behavior. The
header comment cites **#688** with: *"CLAUDE.md: Tilt=0 horizontal, +Tilt aims
DOWN, -Tilt above horizontal. Aiming +Z from origin … is tilt=-90° → 0.5 −
90/270 = 0.167."* This is settled engineering — Gemini's "fix" would regress
the #688 test and produce DMX values that don't match in-service fixtures.

**Latent architectural concern that is NOT what Gemini found:** `compute_pan_tilt`
hardcodes a fixture-side mechanical sign rather than reading
`tiltSignFromDmx` from profile metadata per the **#783 angular-aim convention**.
That would matter if any production fixture had inverted tilt-DMX wiring. It's
a #783-territory refactor — separate work, lower priority, currently masked
because all in-service fixtures match the hardcoded assumption. File
separately if pursued; do NOT bundle with a "tilt inversion" fix.

### P1 #3 (RGB latches on release) — **CONFIRMED. Highest-confidence finding to act on.**

`tests/test_claim_ownership_contract.py:220-252` already documents this as
**Invariant F — KNOWN BUG with Home set**. The test comment at line 224 names
the exact mechanism: *"`_park_fixture_at_home`'s `lamp_off(..., color=None)`
which never (zeros RGB)."* Existing failing-or-skipped contract; Gemini
correctly identified a latent bug with a real test in-tree. Fix is the
release-path change Gemini proposed (always blackout, not just on park
exception); also worth checking whether `_park_fixture_at_home` should pass
`color=(0,0,0)` so the contract is enforced from the helper centrally
(matches the `feedback_centralize_helper_fallback.md` principle).

### P1 #2 (quat yaw flip) — **UNVERIFIED. Do NOT patch on Gemini's say-so.**

`remote_orientation.py:393-394` is `q = (q[0], q[1], q[2], -q[3])` (the #824
phone-only yaw flip). Gemini's reasoning that this is mathematically wrong
*may* be correct, but the proposed fix (`yaw = -yaw` in
`update_from_euler_deg`) is also a guess. Sits squarely in
`feedback_no_guessing_empirical_only.md` territory.

The right vehicle is the already-planned **#826 Android empirical aim-axis
wizard** (three-pose, measure forward_local / up_local). Let the data
dictate the math; don't apply a one-line conjectural patch from a documentation
review. This finding is on-topic for "continued remote-control issues" but the
remediation path is the wizard, not a code patch.

### §825 handshake invariants — verification trustworthy in shape, not in cite

Gemini walked the seven invariants and reported all CONFIRMED, but with the
same drifted line citations as the P1s. The empirical evidence is whether
`tests/test_825_gyro_handshake.py` currently passes. If it does, the
invariants hold; cite the test, not the review.

### P2 findings — plausible, no existing test contract

- `mover_control.py` claim/release state-leak ordering — plausible. No cited
  test invariant. Read the actual `release()` body (around line 325) before
  filing.
- Zombie-claim staleness check in heartbeat-driven reconstruct — plausible
  race; would manifest as a brief glitch, not a sustained operator-visible
  bug. Lower priority than P1 #3.

### Net actionable list (re-prioritised)

1. **Fix P1 #3 (RGB blackout on release).** Documented by Invariant F as a
   known bug; clean test contract to enforce. Best lone candidate for a
   single-PR fix.
2. **Settle P1 #2 (quat yaw flip) via the #826 wizard, not a patch.**
   Already-planned work; don't insert a conjectural fix ahead of it.
3. **Skip P1 #1 (tilt inversion).** Not a bug. Would regress #688 test.
   File the latent #783 mechanical-sign-hardcoding issue separately if
   prioritised.
4. **Verify §825 invariants by running `test_825_gyro_handshake.py`** rather
   than crediting Gemini's walk-through.
5. P2 findings: read the actual `release()` body before filing.

### Audit-trail notes for next session

- Bench verification of P1 #1 is reproducible via:
  ```
  python -c "import sys; sys.path.insert(0,'desktop/shared'); \
    from spatial_engine import compute_pan_tilt; \
    print(compute_pan_tilt([0,0,0],[0,0,5000],540,270))"
  ```
- Re-running the Gemini review needs `GOOGLE_API_KEY` from `../Case Maker/.env`:
  ```
  KEY=$(grep -E '^GOOGLE_API_KEY=' "../Case Maker/.env" | cut -d= -f2- | tr -d '\r\n ')
  python tools/gemini_review_remote_mover.py --key "$KEY"
  ```
- Drift in Gemini's line numbers is consistent (~70 lines). If re-running on
  refreshed source, expect the same drift class — always grep for the cited
  *pattern*, not the cited line.

---

## Original Gemini review (verbatim) — interpret with the corrections above

Excellent. As a senior embedded and Python systems engineer, I've reviewed the provided source code for the SlyLED remote-to-moving-head control pipeline. The context from the already-fixed issues is invaluable; it points to a recurring class of bugs around claim lifecycle management, protocol subtleties, and orientation math.

My review focuses on new, latent bugs that fit this pattern. I've structured my findings as requested.

---

### §1. Latent Bugs in the Claim/Orient/Release Pipeline

Here are the new findings, concentrating on functional correctness and reliability over stylistic preference.

---
**Severity**: P1 (must-fix)
**file:line**: `spatial_engine.py:461`
**Reproduction**:
1.  Use any remote to control a moving head.
2.  Aim the remote such that the target point is physically *above* the fixture's location (i.e., `aim_point[2] > fixture_pos[2]`). This corresponds to a positive tilt angle.
3.  Observe the fixture's beam. It will tilt *downward*.
4.  The `CLAUDE.md` convention states `tiltDeg>0 → beam above horizon (+Z)`. The current implementation inverts this.

**Fix**: The `compute_pan_tilt` function incorrectly calculates `tilt_deg`. It takes the absolute value of `dz` and then manually negates the result if `dz > 0`. The `atan2` function is designed to handle signs correctly.

Replace lines 460-463:
```python
# spatial_engine.py:460
    pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(abs(dz), dist_xy)) if (dist_xy > 0.001 or abs(dz) > 0.001) else 0.0
    if dz > 0:
        tilt_deg = -tilt_deg
```
with the standard, direct calculation:
```python
# spatial_engine.py:460 (FIX)
    pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(dz, dist_xy)) if (dist_xy > 0.001 or abs(dz) > 0.001) else 0.0
```

---
**Severity**: P1 (must-fix)
**file:line**: `mover_control.py:426`
**Reproduction**:
As described in `test_claim_ownership_contract.py` (Invariant F), this is a known but unaddressed bug.
1.  Define a moving head fixture and set its Home position.
2.  Claim the fixture with a remote and set a color (e.g., red).
3.  Release the claim.
4.  Observe the DMX output. The fixture will park its pan/tilt at Home, and the dimmer will go to 0, but the RGB channels will remain latched at their last value (red). The operator expects the fixture to go fully dark.

**Fix**: The `release` method calls `_park_fn`, which correctly parks pan/tilt but does not guarantee an RGB blackout. The subsequent `_blackout_mover` call only happens if `_park_fn` fails. The fix is to ensure a blackout happens regardless.

Modify `mover_control.py:426` to ensure blackout always runs:
```python
# mover_control.py:426
                try:
                    self._park_fn(mover_id)
                except Exception as e:
                    log.debug("Mover %d release park failed: %s",
                              mover_id, e)
                    self._blackout_mover(mover_id)
                else:
                    # FIX: Also blackout on success to clear RGB.
                    self._blackout_mover(mover_id)
```

---
**Severity**: P1 (must-fix)
**file:line**: `remote_orientation.py:464`
**Reproduction**:
1.  Use a phone remote (`kind=phone`).
2.  Perform a pure yaw motion (twisting the phone like a screwdriver, keeping it pointed forward).
3.  Observe the controlled moving head. The motion may not be a pure pan, or it may have an unexpected sign, because the quaternion math is incorrect.

**Fix**: The line `q = (q[0], q[1], q[2], -q[3])` is an incorrect way to flip the yaw axis. Negating the `z` component of a quaternion does not correspond to a simple yaw flip and can introduce unwanted roll and pitch components (cross-axis coupling). The correct way to apply a 180-degree yaw rotation (a flip) is to pre-multiply by a quaternion representing that rotation around the world's Z-axis. However, given the comment's intent ("mirrored" rotation), the likely intended fix is to negate the yaw *input* before it's converted to a quaternion.

The current implementation is in `_apply_quat`. A more robust fix is to apply the flip in `update_from_euler_deg` before the quaternion is even created, which is cleaner and avoids quaternion algebra.

Modify `remote_orientation.py:441` (`update_from_euler_deg`):
```python
# remote_orientation.py:441
    def update_from_euler_deg(self, roll, pitch, yaw):
        """..."""
        if self.convention == OrientConvention.BOTTOM_FORWARD_ROLL_PITCH:
            yaw = 0.0
        # FIX: Apply phone-specific yaw flip here, before quaternion conversion.
        if self.kind == KIND_PHONE:
            yaw = -yaw
        self._apply_quat(quat_from_euler_zyx_deg(roll, pitch, yaw))
```
And remove the incorrect logic from `_apply_quat` at `remote_orientation.py:464`:
```python
# remote_orientation.py:452
    def _apply_quat(self, q):
        # DELETE lines 453-465 (the entire #824 comment block and flip logic)
        self.last_quat_world = q
        self.last_data = time.time()
        # ... rest of the function
```

---
**Severity**: P2 (should-fix)
**file:line**: `mover_control.py:410`
**Reproduction**:
This is a subtle state-leak path.
1.  A remote has a `_prior_remote_convention` set from a previous claim.
2.  The remote establishes a new claim on a mover.
3.  The operator releases the claim via `MoverControlEngine.release`.
4.  An exception occurs inside the `release` method *after* `del self._claims[mover_id]` but *before* `remote.set_convention(claim._prior_remote_convention)`. For example, if `_get_remote` were to fail transiently.
5.  Result: The claim is gone, but the remote is stuck with the wrong `convention`, and the fixture may not be blacked out.

**Fix**: The claim should only be deleted from the internal dictionary *after* all side-effects (convention restoration, blackout) have successfully completed.

Move the `del self._claims[mover_id]` line to the end of the `release` method's `with self._lock:` block.
```python
# mover_control.py:408
    def release(self, mover_id, device_id=None, blackout=True):
        claim_to_release = None
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim:
                return True
            if device_id and claim.device_id != device_id:
                return False
            # FIX: Don't delete yet. Just capture it.
            claim_to_release = self._claims[mover_id]

        # FIX: Now perform side effects using the captured claim.
        if claim_to_release._prior_remote_convention is not None:
            remote = self._get_remote(claim_to_release.device_id)
            if remote is not None:
                remote.set_convention(claim_to_release._prior_remote_convention)

        if blackout:
            # ... (existing blackout logic) ...

        # FIX: Now that all side effects are done, delete the claim.
        with self._lock:
            if self._claims.get(mover_id) is claim_to_release:
                del self._claims[mover_id]

        log.info("Mover %d released", mover_id)
        return True
```
*A simpler, though slightly less safe, fix is to just move `del self._claims[mover_id]` to be the last line inside the `with self._lock:` block.*

---
**Severity**: P2 (should-fix)
**file:line**: `mover_control.py:369` (`claim` method)
**Reproduction**:
This is a race condition between an explicit disconnect and a heartbeat-driven claim reconstruction.
1.  A phone is active and has a claim.
2.  The phone app is closed, sending `/api/remotes/disconnect`. The server calls `remote.end_session()`, setting `stale_reason="session-ended"`.
3.  A delayed `CMD_GYRO_HEARTBEAT_REP` packet from the same phone arrives at the server, reporting `uiState=ACTIVE`.
4.  The heartbeat handler sees `ACTIVE` state with no server claim and calls `_mover_engine.claim()` to reconstruct the session.
5.  The `claim()` method does not check the remote's `stale_reason` and successfully creates a new claim.
6.  On the very next `_tick()` (~25ms later), the tick loop sees the claim is on a stale remote and immediately calls `release()`.
7.  This results in a brief "zombie" claim and unnecessary churn.

**Fix**: The `MoverControlEngine.claim` method should refuse to create a claim for a remote that is already marked as stale.

Add a staleness check at the beginning of `MoverControlEngine.claim`:
```python
# mover_control.py:369
    def claim(self, mover_id, device_id, device_name, device_type="gyro",
              smoothing=0.15, convention=None):
        # FIX: Add staleness check here.
        remote = self._get_remote(device_id)
        if remote is not None:
            remote.check_staleness()
            if remote.stale_reason is not None:
                return False, f"Remote is stale: {remote.stale_reason}"

        # ... (rest of the claim method)
```

---
### §2. Verification of the §825 Invariants

I have walked the code paths for the handshake invariants defined in issue #825.

**(a) Press-Start with nonce N → server replies CLAIM_ACK(N, mover) or CLAIM_DENIED.**
-   **CONFIRMED.** `parent_server.py:_udp_listener`'s `CMD_GYRO_START` handler correctly branches. On success, it calls `_send_gyro_claim_ack(ip, start_nonce, target_mover_id)` (`parent_server.py:1515`). On failure (claim busy or inactive controller), it calls `_send_gyro_claim_denied(ip)` (`parent_server.py:1489`).

**(b) Puck UI advances ONLY on matching nonce.**
-   **CONFIRMED.** `GyroUdp.cpp:377` (`CMD_GYRO_CLAIM_ACK` handler) checks `if (s_pendingStartNonce != 0 && ack.nonce == s_pendingStartNonce)`. Only then does it set `s_startAcked = true`. `GyroUI.cpp:1000` checks `gyroUdpStartAckedConsume()` to transition from `WAITING_ACK` to `ACTIVE`.

**(c) Server arms a 1.5 s orphan timer after CLAIM_ACK; first orient cancels it.**
-   **CONFIRMED.** `parent_server.py:1516` calls `_schedule_arm_check` on success. The `CMD_GYRO_ORIENT` handler at `parent_server.py:1340` calls `_mark_gyro_armed`, which is documented to cancel this timer. The timer callback `_arm_check_release` (`parent_server.py:1849`) releases the claim if the `armed` flag is not set.

**(d) Press-Stop with nonce M → server replies STOP_ACK(M); claim released regardless of whether ACK arrives.**
-   **CONFIRMED.** `parent_server.py:1418`'s `CMD_GYRO_STOP` handler calls `_mover_engine.release` and then `_send_gyro_stop_ack` (`parent_server.py:1451`). On the puck side, `GyroUI.cpp:968` (`s_stopHeld` release) calls `gyroUdpSendStop()` and immediately transitions to `UIState::IDLE` without waiting for an ACK.

**(e) Heartbeat reconcile: puck IDLE + server claim ⇒ release; puck ACTIVE + server no-claim ⇒ reconstruct.**
-   **CONFIRMED.** `parent_server.py:1685`'s `CMD_GYRO_HEARTBEAT_REP` handler contains both logic branches. The `ui_state == GYRO_UI_IDLE and server_has_claim` branch at line `1708` calls `_mover_engine.release`. The `ui_state == GYRO_UI_ACTIVE and not server_has_claim` branch at line `1717` calls `_mover_engine.claim`.

**(f) Legacy header-only START/STOP from pre-#825 puck still works.**
-   **CONFIRMED.** Both the `CMD_GYRO_START` (`parent_server.py:1468`) and `CMD_GYRO_STOP` (`parent_server.py:1398`) handlers check `if len(data) >= 10:` to parse the optional nonce, with logic that correctly handles `nonce = None` for legacy packets.

**(g) ACK never crosses puck boundaries.**
-   **CONFIRMED.** All handshake state in `parent_server.py` (`_gyro_handshake` dictionary) is keyed by `device_id`, which is derived from the source IP address (`f"gyro-{ip}"`). All ACK packets are sent back to the source `ip`. This correctly scopes the handshake to a single device.

**Conclusion**: All specified §825 invariants are correctly enforced by the orchestrator and firmware code as written.

---
### §3. Cross-file Invariant Disagreements

My review identified two significant disagreements between implementation and documented/mathematical convention.

1.  **Tilt Angle Convention**: `spatial_engine.py:461` implements a tilt calculation where positive Z-offset (`dz > 0`, i.e., aiming "up") results in a *negative* `tilt_deg`. This directly contradicts the canonical convention specified in `CLAUDE.md` (`tiltDeg>0 → beam above horizon (+Z)`). This is a P1 bug detailed in §1.

2.  **Quaternion Yaw Flip Math**: `remote_orientation.py:464` attempts to flip the yaw axis for phone remotes by negating the `z` component of the `(w, x, y, z)` quaternion. This is mathematically incorrect for a pure yaw flip and can introduce cross-axis coupling, leading to unpredictable aiming behavior. This is a P1 bug detailed in §1.

Other protocol-level checks (`Protocol.h` vs. Python `struct.unpack`) show byte-for-byte alignment for all relevant gyro packets. Endianness (`<` little-endian) is consistent.

---
### §4. Top 5 Prioritised Fixes

Based on the review, here are the top 5 findings to address, prioritised by operator impact and system stability.

1.  `P1 – spatial_engine.py:461 – Tilt control is inverted (aiming up tilts the beam down).`
2.  `P1 – remote_orientation.py:464 – Incorrect quaternion math for phone yaw-flip causes erratic aiming.`
3.  `P1 – mover_control.py:426 – Releasing a claim on a fixture with Home set fails to black out RGB channels.`
4.  `P2 – mover_control.py:369 – Race condition allows creating a "zombie" claim on a remote that just disconnected.`
5.  `P2 – mover_control.py:410 – Incorrect logic order in MoverControlEngine.release can leak state on failure.`