# Bug: gyro orient → DMX wire frozen after first tick (live test 2026-05-08)

**Build:** v1.7.80 (working tree, uncommitted)
**Severity:** P1 — gyro can't drive moving heads at all on the rig.

## Symptom

1. Operator presses Start on the puck. Claim acquires; head moves once to a position; lights up.
2. Operator moves the puck. **Head does not respond.**
3. SPA's 3D viewport DOES show the puck's aim vector and the moving-head's beam vector tracking correctly.
4. Operator presses Stop. Claim releases server-side, but **head stays where it was** (does not park to home, no show running to take over).

## Empirical timeline (lockstep poller, fid=19)

```
17:11:31  calibrate-end  aim=(0.348, 0.312, 0.884)  →  claim=(0.8831, 0.3286)  wire=(225, 83)   ← jump on calibrate-end
17:11:36  puck moves     aim=(-0.146, 0.915, 0.375) →  claim UNCHANGED         wire UNCHANGED
17:11:40  puck moves     aim=( 0.065, 0.985, 0.157) →  claim UNCHANGED         wire UNCHANGED
17:11:45  puck moves     aim=( 0.082, 0.989, 0.119) →  claim UNCHANGED         wire UNCHANGED
17:11:49  puck moves     aim=( 0.115, 0.984, 0.133) →  claim UNCHANGED         wire UNCHANGED
17:11:54  puck moves     aim=( 0.187, 0.974, 0.126) →  claim UNCHANGED         wire UNCHANGED
17:11:58  puck moves     aim=( 0.086, 0.994, 0.066) →  claim UNCHANGED         wire UNCHANGED
```

aim_stage ranged across:
- x: -0.146 → +0.187 (33% span)
- y: 0.915 → 0.994
- z: 0.066 → 0.375 (huge — beam from 4° above horizon to 22° above)

That's az span ≈ 20°, el span ≈ 15°. **Both well within the IK's responsive region** per probe (see below).

## What's working

- **Puck → server**: orient packets at 20 fps. ✓
- **Server → `Remote.aim_stage`**: updates with each orient packet. ✓
- **`/api/remotes/live`** exposes correct, varying `aim` values. ✓
- **SPA 3D viz** reads aim_stage and renders the vector correctly. ✓
- **Direct HTTP `POST /api/mover/19/aim {azDeg, elDeg}`**: returns distinct DMX for distinct inputs across the entire reachable cone (verified by 50-point probe). ✓

## What's broken

The path **`Remote.aim_stage` → tick loop → `_aim_to_pan_tilt` → `claim.pan_smooth` → `_write_dmx`**.

Specifically:
- After the first tick post-claim or post-calibrate-end, `claim.pan_smooth` / `claim.tilt_smooth` **stop updating** despite `Remote.aim_stage` continuing to change.
- The tick loop *is* running (`engine.running=True`, `claim.lastWriteAge < 1s`).
- The wire holds the same DMX as the frozen claim.
- The bug reproduces deterministically on this rig with fid=19 (Sly Moving Head Super Mini, profile `slymovehead`).

## Probe evidence — `_aim_to_pan_tilt` is responsive when called externally

Externally driving the same code path via `POST /api/mover/19/aim` with `current_pose` matching the frozen claim (`59802, 4763`) produces **distinct DMX for the puck's varying aim_stage values**:

| aim_stage              | az/el         | IK output (pan, tilt) |
|------------------------|---------------|------------------------|
| (-0.146, 0.915, 0.375) | (-9.1, +22.0) | (50911, 8010)          |
| ( 0.065, 0.985, 0.157) | (+3.8,  +9.0) | (52476, 3277)          |
| ( 0.187, 0.974, 0.126) | (+10.9, +7.2) | (53338, 2621)          |
| ( 0.974,-0.108, 0.198) | (+96.3,+11.4) | (63702, 4151)          |
| ( 0.000, 1.000, 0.000) | (0, 0)        | (52015, 0)             |

So the IK function is responsive — **the freeze is NOT IK boundary-clamp** at the puck's calibrated aim.

## Hypothesis (not yet confirmed)

`_aim_to_pan_tilt` (mover_control.py:614) raises an exception silently inside the tick loop, caught at line 657:
```python
except Exception as e:
    log.warning("aim_to_pan_tilt: AimSphere failed for mover %s: %s",
                mover_id, e)
    return (None, None)
```
The DEBUG-level log was bumped to WARNING during the session and orchestrator file-logging was enabled, but the operator interrupted before another full cycle could be captured under the new logging. **Next session: reproduce with WARNING active and capture the exception text.**

Alternative: the tick is calling `_aim_to_pan_tilt` and getting a real return value, but something in the smoothing/write path silently drops it. Smoothing alpha (`1 - claim.smoothing`) was confirmed `0.85` (gyro fixture smoothing=0.15), so single-tick convergence to new value should be ~85%.

## Why the existing harness didn't catch this

`tests/test_gyro_dmx_integration.py` builds a synthetic fixture and asserts orient → DMX wire end-to-end. The harness passed 19/19. **The fixture geometry is not representative of the live rig:**

| Property      | Harness fixture        | Live fid=19            |
|---------------|------------------------|------------------------|
| `tiltRange`   | 270°                   | **180°**               |
| `homePan`     | center (32768)         | **off-center (52015)** |
| `homeTilt`    | center (32768)         | **extreme (0)**        |
| `tiltSign`    | +1 (default)           | **−1 (inverted)**      |
| pan/tilt bits | 16-bit (per harness)   | **8-bit (slymovehead)**|

The harness's center-home + 16-bit + +1-sign fixture exercises a very different IK code path than the live extreme-home + 8-bit + −1-sign fixture. **Action item: extend `test_gyro_dmx_integration.py` to add a second fixture matching the slymovehead profile shape, or add a regression cell that reproduces the live freeze pattern (multiple distinct aim_stage values, assert claim.panNorm/tiltNorm vary between them).**

## Two related bugs same session

**Press-Stop also fails to park the head.** Lockstep log at 16:52:38 showed claim released cleanly (`nClaims: 1 → 0`, staleReason latched) but the wire stayed at the last claim values (pan=152, tilt=2, dimmer=255) — head physically did not move to home. Spec says press-Stop must park-at-home or hand off to a running show; show was `running: false` so park should have run. `_park_fn` either threw silently (caught at `mover_control.py:351-353`, logged at DEBUG) or succeeded but produced no observable wire change. **Same instrumentation fix (DEBUG → WARNING) on the park-failure exception will tell us which.**

## Files touched this session

- **edited**: `desktop/shared/mover_control.py:658` — `log.debug` → `log.warning` for `_aim_to_pan_tilt` failure path. Keep this change; it's the diagnostic that's about to surface the exception.
- **created**: `tools/qa_poll_gyro.py` — state-change poller for the gyro/remote/claim chain.
- **created**: `tools/qa_dual_lockstep.py` — captures (puck rpy, aim_stage, claim, wire) in lockstep.
- **created**: `tools/qa_orchestrator.log` — orchestrator file log started via `POST /api/logging/start`.
- **created**: `tools/qa_lockstep.log` — lockstep poller's session log.
- **created**: `tools/gyro_remote_review.md` — Gemini-review output (separate session).

## Reproduction steps

1. Start orchestrator (v1.7.80 working tree).
2. Confirm fid=19 (Sly Moving Head Super Mini) is patched and reachable on Art-Net universe 1.
3. Start `python tools/qa_dual_lockstep.py --fid 19`.
4. On the puck, press Start.
5. Press Calibrate, align, release.
6. Move puck through ~30° of yaw or pitch.
7. Observe: aim_stage varies in `qa_lockstep.log`, claim/wire stay at one value.
8. Press Stop. Observe: claim released, head doesn't park.

## Next-session priorities

1. Reproduce with `mover_control.py:658` at WARNING level. Capture the exception text from `tools/qa_orchestrator.log`.
2. Apply same DEBUG→WARNING bump to the `_park_fn` exception at `mover_control.py:351-353` so the press-Stop park failure surfaces too.
3. Extend `test_gyro_dmx_integration.py` with a fixture geometry matching the live rig (8-bit, extreme home, inverted tiltSign).
4. Once the exception text is in hand, the actual fix path is direct — likely an `assert` in `aim/sphere.py:212-215` or a profile-shape edge case in `_aim_to_pan_tilt`.
