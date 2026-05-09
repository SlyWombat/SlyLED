# Regression: Android claim+calibrate of moving head broken in three ways (live test 2026-05-08)

**Build:** v1.7.80 (working tree).
**Last known working baseline:** v1.7.52 era — Android claim worked end-to-end with the only known issue being that the per-axis sign convention was wrong (operator gestures mapped to wrong stage axes). That baseline is gone; the path is now broken in three additional ways.

## Reproduction (rig, fid=17, Pixel 9 Pro XL)

1. Android app: claim moving head fid=17.
2. Aim phone at where the head is currently pointing.
3. Press Calibrate.
4. Release Calibrate.

## Symptoms (operator-observed + wire-confirmed)

| # | Symptom | Wire evidence |
|---|---|---|
| 1 | **No lamp lights on claim acquisition.** | `claim.dimmer = None` post-claim; wire dimmer = 0. Stays off through calibrate. |
| 2 | **Head swings to wrong direction on calibrate-end** — operator aimed phone at head's current pose, head should have stayed put; instead it swung back-stage. | `Remote.aim_stage = (0.660, -0.751, 0.001)` after calibrate-end. y = -0.75 → stage-back, x = +0.66 → stage-left. Operator was aiming forward. |
| 3 | **Wire tilt does not track `claim.tilt_smooth`.** | `claim.tiltNorm = 0.0000`, but `wire.tilt = 128` (8-bit profile default). Pan tracks correctly (`claim.panNorm = 0.4202` → `wire.pan = 107`); tilt does not. |

Plus the pre-existing **axis convention is still wrong** — operator gestures map to wrong stage axes. The empirical fix for this is the planned [#826 Android empirical aim-axis wizard](https://github.com/SlyWombat/SlyLED/issues/826), which has not yet shipped.

## Why the harness suite did not catch any of these

| Cell | Asserted behaviour | Why it passed under harness |
|---|---|---|
| `test_claim_ownership_contract.py` Invariant E (lamp on by default after claim) | `dim > 0 OR any RGB > 0` | Harness fixture profile happens to come up at non-zero default channel value; live fid=17 does not |
| `test_orient_contract.py` cell N (calibrate-pose orient → aim at calibrate target) | `aim_stage ≈ (0,1,0)` | **Cell N currently FAILS in CI baseline** (3 hard fails). It correctly predicts symptom #2 above. |
| `test_gyro_dmx_integration.py` Scenario 7b (wire pan/tilt round-trips claim) | `abs(wire_norm − claim_norm) < 0.005` | Harness fixture is 16-bit; live fid=17 is 8-bit. Symptom #3 is only visible on 8-bit profiles. |

Same harness-gap pattern as #860 — synthetic fixture geometry diverges from the live rig profile.

## Hypothesis on root cause for symptom #2

The `#824` qz-negate sign-flip lives in `Remote._apply_quat` (`remote_orientation.py:393-394`) and runs on every orient update for `kind=phone`. **It does not run on the quat passed into `Remote.calibrate()`.** That means the calibrate quat is stored in one frame and the live orient quats are evaluated in a qz-negated frame. Identity-from-calibrate-pose is therefore not identity-from-server's-perspective, and the head swings on calibrate-end even when phone+head were aimed identically. This is exactly what cell N's `test_phone_calibrate_with_nonidentity_quat_identity_delta_aims_at_target` asserts and fails.

The proper fix is **not** to mirror the qz-negate into `calibrate()` — that only chases the symptom. The proper fix is the [#826 Android empirical aim-axis wizard](https://github.com/SlyWombat/SlyLED/issues/826), which measures `forward_local` / `up_local` from three known phone poses and removes the qz-negate hack entirely.

## Hypothesis on root cause for symptom #1

The Android claim path (`POST /api/mover-control/claim` → `MoverControlEngine.claim`) creates a `MoverClaim` with `dimmer=None` and never sets a default. The earlier `start_stream` path (#800) was supposed to handle "lamp on" via the park-pan-tilt-only helper, but `start_stream` is invoked separately and doesn't set claim.dimmer either. The puck path goes through `start_stream` after CLAIM_ACK; the Android path uses `POST /api/mover-control/start` which has the same gap. The harness's Invariant E only "passes" because of unrelated profile-default behaviour on its synthetic fixture.

## Hypothesis on root cause for symptom #3

`set_fixture_pan_tilt(addr, pan_smooth, tilt_smooth, profile)` (in `dmx_universe.py`) handles the 16-bit→8-bit conversion. With `tiltSign = -1` in the profile, `tilt_smooth = 0.0` should produce wire `tilt = 255` (mirrored). Wire shows `tilt = 128` (channel default), suggesting the writer either short-circuits at boundary values or `tiltSign` handling is gated such that the call doesn't write at all. Pan correctly tracks because `panSign = +1` and `pan_smooth = 0.42 ≠ 0` boundary. Worth a small probe with a non-boundary `tilt_smooth` value to disambiguate "tilt sign-handling broken" vs "tilt boundary-skip bug".

## What changed since the last-working v1.7.52 baseline

Major coordinated landings between v1.7.52 (`01eb5cb`) and v1.7.80:

- **`#780, #783` — cal-pipeline overhaul** (`69d3bb0`): `lamp_on`, `mountedInverted` bake, sphere model. Reshaped how aim is captured and applied.
- **`#784 PR-5` — `_aim_to_pan_tilt` rewired to AimSphere only** (`86353ed`, `21ee4ca`): removed SMART/parametric/affine fallbacks; angular IK is now sphere-only.
- **`#785, #784`** — sphere QA round 2 (`12edebc`).
- **`#800, #757-B, #752` — park-at-home lifecycle + single-IK aim_stage** (`aff8691`).
- **`#801, #778, #800-rev` — gyro Active/Inactive + park-loop split** (`aa73a28`).

Symptoms #1 and #3 most likely come out of this cluster; symptom #2 is the older qz-negate hack interacting with the new calibrate flow.

## Live-test capture for the audit trail

```
17:51:07  Android claim landed
            mover_id=17, deviceId=2c785d55-…, deviceName="Pixel 9 Pro XL"
            claim.state=streaming, claim.calibrated=true (from cross-session persist)
            claim.dimmer=None         ← symptom #1
17:51:29  calibrate-start
17:51:29  calibrate-end
            claim.have_pan_tilt = False (will jump on next tick)
            head physically swings back-stage     ← symptom #2
17:52:27  steady state (no AimSphere WARNING in orchestrator log)
            Phone:  aim_stage = (+0.660, -0.751, +0.001)
            Claim:  panNorm = 0.4202   tiltNorm = 0.0000   dim = None
            Wire:   pan = 107          tilt = 128          ← symptom #3
                    (pan tracks claim, tilt stuck at profile default)
```

The orchestrator log file (`tools/qa_orchestrator.log`, started via `POST /api/logging/start`) shows zero `aim_to_pan_tilt: AimSphere failed` warnings during the session. So the IK is **not** throwing for fid=17 — the freeze pattern from #860 is not active here, but symptoms #1–#3 are independent of that.

## Acceptance criteria

1. After Android claim of a moving head with `homePan/homeTilt` set, the lamp visibly lights at a non-zero default. (Harness Invariant E passes against a live-rig-shaped fixture.)
2. After `phone-aimed-at-head → press Calibrate → release`, the head does **not** move. (Cell N of `test_orient_contract.py` flips from FAIL to PASS without applying a qz-negate to `calibrate()`.)
3. Wire tilt tracks `claim.tilt_smooth` for an 8-bit profile with `tiltSign = -1` across the full 0..1 range, including 0.0 and 1.0 boundary values. (`test_gyro_dmx_integration.py` extended with an 8-bit fid mirroring fid=17's profile shape.)
4. Per-axis sign convention is correct: phone yaw left → head pans stage-left, phone pitch down → head tilts down, etc. **The right vehicle for #4 is [#826](https://github.com/SlyWombat/SlyLED/issues/826).**

## Related

- [#860](https://github.com/SlyWombat/SlyLED/issues/860) — orient-freeze on the puck path (different code path, same harness-gap pattern).
- [#826](https://github.com/SlyWombat/SlyLED/issues/826) — Android empirical aim-axis wizard. Canonical fix for the qz-negate hack and the per-axis sign convention.

## Files implicated

- `desktop/shared/remote_orientation.py` (`_apply_quat` qz-negate; `calibrate()` does not mirror it).
- `desktop/shared/mover_control.py` (`claim()` does not seed default dimmer; `start_stream` does not seed dimmer either).
- `desktop/shared/dmx_universe.py` (`set_fixture_pan_tilt` 8-bit + `tiltSign=-1` boundary handling).
- `tests/test_orient_contract.py` cell N (already pinning symptom #2).
- `tests/test_claim_ownership_contract.py` Invariant E (passes under synthetic fixture; needs an 8-bit-profile fixture cell to catch symptom #1).
- `tests/test_gyro_dmx_integration.py` (needs an 8-bit-profile fixture cell to catch symptom #3).
