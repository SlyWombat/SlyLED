# Gyro / Phone Remote Controller — Stage-Space Orientation Architecture

**Status:** Design — pending review
**Scope:** ESP32 gyro puck (`SLYG-*`) and Android phone in controller mode. Both feed `POST /api/mover-control/orient`.
**Tracking issue:** #484
**Related:** #468 (unified mover control), #474 (absolute stage-space mapping), #477 (axis verification), #478–#483 (Android parity — blocked on this)

---

## 1. Problem

Current implementation in `desktop/shared/mover_control.py` is **delta-based in the remote's own frame**:

- On calibrate-end (or auto-start), store `ref_roll/pitch/yaw` + `ref_pan/tilt`.
- Per orient update: compute `(cur_az - ref_az, cur_el - ref_el)` via `_euler_to_aim()`, scale by `panScale/tiltScale`, add to `ref_pan/tilt`.

This treats the remote's roll axis as pan and pitch axis as tilt, regardless of where the fixture is mounted, whether it's inverted, and where in the stage it sits. Inverted mounts, off-centre fixtures, and diagonal aiming all feel wrong. The Android client hits the same engine so it has the same bug.

The user model is:

> The mover is currently pointed at a known stage vector. Calibration aligns the remote's current forward vector *with that same stage vector*. From that moment on, the remote's orientation **is** the mover's aim direction in stage space. Any rotation of the remote should rotate the mover's aim through the same angle in stage space.

There is no `panScale` in that model — it is 1:1 in stage angles by construction.

## 2. Goals and non-goals

**Goals**
- 1:1 mapping from remote rotation to mover aim rotation, measured in stage angles.
- Inverted / angled fixture mounts are handled by the mount rotation matrix, not per-fixture flip flags.
- One pipeline handles both ESP32 puck and Android phone.
- Each active remote is a first-class stage-space object with a visible aim ray in the 3D viewport, for debugging and for operator feedback.

**Non-goals (v1)**
- Absolute north-referenced control (point phone at a real-world direction, fixture aims there regardless of where the operator moved). v1 is anchored by explicit calibration.
- Simultaneous control of multiple movers by one remote.
- Predictive tracking / latency compensation.
- Automatic drift correction for the ESP32 (no magnetometer). Re-calibration is the mitigation.

## 3. Coordinate conventions

### 3.1 Stage frame — reference

From `project_coordinate_system.md` (memory):

| Axis | Direction | Units |
|------|-----------|-------|
| X | Stage right → stage left | mm |
| Y | Back wall (upstage) → audience | mm |
| Z | Floor → ceiling | mm |

Origin `(0,0,0)` = floor, stage right, back wall. All components (calibration, rendering, fixture positioning) use this single frame. Calibration computed in any other frame produces wrong results.

### 3.2 Mover frame

- **Position:** `fixture.x`, `fixture.y`, `fixture.z` (stage mm).
- **Mount rotation:** `fixture.rotation = [rx, ry, rz]` — Euler angles in degrees, applied in the engine's existing fixture transform. (See `desktop/shared/spa/js/fixtures.js:122`. Order: currently applied by the baker / viewport as intrinsic XYZ; verify and document in implementation.)
- **Pan/tilt convention** (from `mover_calibrator.py:634 pan_tilt_to_ray`):
  - `pan_norm = 0.5`, `tilt_norm = 0.5` ⇒ aim = stage `+Y` (depth, forward into the room) when the mount rotation is identity.
  - `pan_deg = (pan_norm - 0.5) * pan_range` — pan rotates in the XY plane; increases clockwise viewed from above.
  - `tilt_deg = (tilt_norm - 0.5) * tilt_range` — positive tilt aims downward (`dz < 0`).
  - Forward vector in mount frame:
    `dx = sin(pan) * cos(tilt)`
    `dy = cos(pan) * cos(tilt)`
    `dz = -sin(tilt)`
- **pan_range / tilt_range:** on the fixture (typically 540° / 270°).

### 3.3 Remote frame — ESP32 gyro puck

- IMU: QMI8658 (6-axis, no magnetometer).
- Output from `main/GyroIMU.cpp:151–168` (complementary filter, `CF_ALPHA = 0.98`):
  - `roll` = atan2(ay, az) + gyro integration → rotation about body X (tilt left/right)
  - `pitch` = atan2(-ax, √(ay²+az²)) + gyro integration → rotation about body Y (tilt forward/back)
  - `yaw` = pure gyro integration about body Z. **Drifts** (no magnetometer). Reset by `gyroIMUZero()` on calibrate hold.
- Body axes of the puck (to be confirmed against physical labelling in implementation):
  - `+X` → screen 3-o'clock direction
  - `+Y` → screen top (12-o'clock) — **defined as "forward"** (this is the pointing direction)
  - `+Z` → screen normal, out of the LCD face — **defined as "up"**
- Euler → rotation matrix: ZYX intrinsic order (yaw, pitch, roll), standard aerospace convention.
- "World" for the ESP32 means a gravity-aligned frame whose yaw zero was the yaw at last `gyroIMUZero()`.

### 3.4 Remote frame — Android phone

- Sensor: `TYPE_ROTATION_VECTOR` (fused accel + gyro + magnetometer).
- Current code (`ControllerModeOverlay.kt:91–96`) converts via `SensorManager.getOrientation()` to:
  - `orientation[0]` = azimuth (yaw about Z, magnetic-north referenced when mag is valid)
  - `orientation[1]` = pitch (rotation about X, ±π/2)
  - `orientation[2]` = roll (rotation about Y, ±π)
- Android body axes (documented; to be confirmed with operator UX decisions):
  - `+X` → screen right edge
  - `+Y` → screen top edge — **defined as "forward"**
  - `+Z` → out of the screen — **defined as "up"**
- Critical difference vs ESP32: Android's rotation vector is **world-referenced** (gravity-aligned Z, magnetic north when available). `R_remote_to_stage` is approximately constant over time — minimal drift.

### 3.5 Implication: ESP32 vs Android

| Property | ESP32 puck | Android phone |
|----------|-----------|---------------|
| Yaw reference | Gyro integration; resets on calibrate | Magnetic north (when mag valid) |
| Long-term stability | Drifts minutes-scale | Stable |
| Native output | Euler (Int16 * 100) | Quaternion (rotation vector) |
| Mitigation | Re-calibrate when drift is visible | None needed |

The server pipeline treats all inputs as a rotation (quaternion internally) from "remote world" to "remote body." What differs is how stable the remote's world-frame is over time — that's a UX concern, not a math concern.

## 4. Calibration math

**Entry point:** `POST /api/mover-control/calibrate-end` (or equivalent on CMD_GYRO_CALIBRATE).

At calibrate-end, with the operator holding the remote so it visually matches the mover's current aim:

1. **Read the mover's current pan/tilt.** Source: the last DMX value the engine wrote for this mover's pan/tilt channels, or a safe default `(0.5, 0.5)` if none yet. Call this `(p_norm, t_norm)`.
2. **Compute aim vector in the mount-local frame** via `pan_tilt_to_ray(p_norm, t_norm, pan_range, tilt_range)` → `aim_mount`.
3. **Rotate into stage frame** by the fixture's mount rotation:
   `a_stage = R_mount_to_stage · aim_mount`
   where `R_mount_to_stage` is built from `fixture.rotation` (Euler deg, intrinsic XYZ).
4. **Choose the stage "up" reference:**
   `u_stage = normalize(Z_stage - (Z_stage · a_stage) · a_stage)`
   (project world-up onto the plane perpendicular to `a_stage`; gives a well-defined "top" direction for the aim).
5. **Read the remote's current orientation** from the last orient sample:
   - ESP32: Euler `(roll, pitch, yaw)` → `q_body_to_world` via ZYX.
   - Android: quaternion directly if the wire format exposes it (see §6); otherwise Euler → quaternion same as ESP32.
6. **Compute remote forward and up in remote world:**
   `f_remote = q_body_to_world · [0, 1, 0]ᵀ`  (body +Y = forward)
   `u_remote = q_body_to_world · [0, 0, 1]ᵀ`  (body +Z = up)
7. **Solve two-axis frame alignment.** Find the rotation `R_world_to_stage` such that:
   `R_world_to_stage · f_remote ≈ a_stage`
   `R_world_to_stage · u_remote ≈ u_stage`  (in the plane ⊥ `a_stage`)

   Standard quaternion solution:
   ```python
   def frame_align(f_src, u_src, f_dst, u_dst):
       # 1. Align forward vectors with minimum-angle rotation
       q1 = quat_from_to(f_src, f_dst)

       # 2. Rotate source up through q1
       u_src_rot = q1.rotate(u_src)

       # 3. Project both ups onto plane perpendicular to f_dst, then align
       u1 = normalize(u_src_rot - dot(u_src_rot, f_dst) * f_dst)
       u2 = normalize(u_dst    - dot(u_dst,    f_dst) * f_dst)
       q2 = quat_from_to(u1, u2)
       return q2 * q1   # apply q1 then q2
   ```
8. **Store on the claim:**
   ```
   claim.R_world_to_stage = R_world_to_stage   # quaternion
   claim.calibrated       = True
   claim.remote_forward_body_axis = [0, 1, 0]  # convention
   claim.remote_up_body_axis      = [0, 0, 1]
   ```
9. **Discard deltas.** The existing `ref_roll/pitch/yaw`, `ref_pan/tilt`, `pan_scale`, `tilt_scale` fields are no longer used in the orient path. Keep them in memory only during the migration window so legacy code paths don't crash.

## 5. Live update math

**Entry point:** `MoverControlEngine.orient()`, called from either UDP (`CMD_GYRO_ORIENT`) or HTTP (`POST /api/mover-control/orient`).

Per sample (roll, pitch, yaw) or quaternion:

1. **Build body→world quaternion.**
   ESP32: Euler (ZYX) → quaternion.
   Android: use the `quat` field directly if present.
2. **Transform body +Y (forward) and +Z (up) into remote world frame:**
   `f_world = q_body_to_world · [0, 1, 0]ᵀ`
   `u_world = q_body_to_world · [0, 0, 1]ᵀ`
3. **Map remote world → stage:**
   `a_stage = claim.R_world_to_stage · f_world`
   (`u_stage` can also be derived if needed for a future "roll" axis; v1 only uses forward.)
4. **Inverse-kinematics: stage aim → pan/tilt.** New helper in `mover_calibrator.py`:
   ```python
   def aim_to_pan_tilt(aim_stage, mount_rotation_deg,
                      pan_range=540, tilt_range=270):
       """Inverse of pan_tilt_to_ray with mount orientation.

       Returns (pan_norm, tilt_norm) in [0,1], clipped.
       """
       R_stage_to_mount = euler_xyz_deg(mount_rotation_deg).T
       aim_mount = R_stage_to_mount @ aim_stage
       dx, dy, dz = aim_mount
       pan_deg  = math.degrees(math.atan2(dx, dy))
       horiz    = math.hypot(dx, dy)
       tilt_deg = math.degrees(math.atan2(-dz, horiz))
       pan_norm  = 0.5 + pan_deg  / pan_range
       tilt_norm = 0.5 + tilt_deg / tilt_range
       return (clamp01(pan_norm), clamp01(tilt_norm))
   ```
   Note: this is algebraically the inverse of `pan_tilt_to_ray` when `mount_rotation = identity`. Should be verified by a round-trip test.
5. **EMA smoothing** (per-fixture `smoothing` field, default 0.15):
   `pan_smooth  += α · (pan_norm  - pan_smooth)`
   `tilt_smooth += α · (tilt_norm - tilt_smooth)`
   where α = smoothing. (Rename for clarity: 1 - smoothing is the true EMA coefficient; current code uses `alpha = 1 - smoothing`. Keep existing behaviour, document precisely.)
6. **Write DMX.** Reuse `_write_dmx()` from `mover_control.py` unchanged.

**No `panScale` / `tiltScale` in the primary path.** The mapping is 1:1 in stage angles by construction.

## 6. Wire format decision

**ESP32 → server:** keep `CMD_GYRO_ORIENT` with int16 `roll/pitch/yaw` × 100. No change. Simple, stable, already deployed on all pucks.

**Android → server:** Two options.

| Option | Payload | Pros | Cons |
|--------|---------|------|------|
| A — stay Euler | `{roll, pitch, yaw}` | No client change | Lossy near ±90° pitch; Euler-order ambiguity |
| B — add quaternion | `{quat: [w,x,y,z]}` preferred, Euler fallback | Matches Android native output; no gimbal loss | Wire schema change |

**Recommendation: B.** `TYPE_ROTATION_VECTOR` is already a quaternion on the Android side; round-tripping through Euler and back is a lossy conversion. The server accepts both; Android sends quat, ESP32 keeps Euler. Wire payload for `POST /api/mover-control/orient` (HTTP JSON only — UDP stays binary for ESP32):

```json
{
  "moverId": 3,
  "deviceId": "phone-abcd",
  "quat":  [0.707, 0.0, 0.707, 0.0],
  "roll":  0.0,
  "pitch": 0.0,
  "yaw":   0.0
}
```

Engine prefers `quat` when present, falls back to Euler.

## 7. Remote object schema

Each active remote is an ephemeral stage-space object while claimed. Lifetime = claim lifetime. Not persisted.

### 7.1 In-memory fields on `MoverClaim`

Add to `desktop/shared/mover_control.py::MoverClaim`:

| Field | Type | Purpose |
|-------|------|---------|
| `remote_kind` | `"gyro-puck" \| "phone"` | Icon selection in the viewport |
| `remote_pos_stage` | `[x, y, z]` mm | Operator position; default stage centre at head height |
| `R_world_to_stage` | quaternion `[w, x, y, z]` | Calibration result |
| `calibrated` | bool | Existing, reused |
| `last_quat_world` | quaternion | Most recent remote orientation in remote world frame |
| `last_aim_stage` | `[dx, dy, dz]` | Cached aim vector for viewport; updated per orient sample |

Existing `ref_*`, `pan_scale`, `tilt_scale` fields: retained but **unused** in the stage-space path. Removed in a follow-up cleanup.

### 7.2 Read API

New endpoint (or extend existing `/api/mover-control/status`):

```
GET /api/remotes/live
->
{
  "remotes": [
    {
      "deviceId":     "gyro-192.168.10.201",
      "kind":         "gyro-puck",
      "moverId":      3,
      "moverName":    "Stage Left MH",
      "claimState":   "streaming",
      "calibrated":   true,
      "lastDataAge":  0.12,
      "pos":          [1500, 2100, 1600],
      "aim":          [0.20, 0.80, -0.55]
    }
  ]
}
```

Frontend polls this while the 3D viewport is visible (reuse the existing `/api/fixtures/live` polling cadence ~10Hz) or wires into the same SSE/WebSocket channel if/when one exists.

## 8. 3D debug visualisation

In the Three.js viewport (`desktop/shared/spa/js/scene-3d.js`), add a `remotes` group parallel to fixtures / temporal objects:

- **Icon at `pos`:** small sprite — disc for gyro puck, rounded rectangle for phone.
- **Ray from `pos` along `aim`** of configurable length (default 3000 mm or until it hits floor/wall, whichever is first — reuse `ray_surface_intersect` at `mover_calibrator.py:660`).
- **Colour coding by claim state + data age:**
  - Green = streaming, fresh (age < 2s)
  - Amber = streaming, stale (age 2–10s)
  - Blue = claimed, not yet streaming
  - Grey / fade-out = released (removed next poll)
- **Label:** `{deviceShortName} → {moverName}`

Rationale: temporal objects currently only carry position + bounding box (`parent_server.py:4072–4111`). Remotes have orientation and a ray, so a dedicated group is cleaner than shoehorning into temporals.

## 9. Migration plan

### 9.1 Fixture schema

| Field | v1.5.x | Stage-space v2 |
|-------|--------|----------------|
| `panCenter`, `tiltCenter` | User-tunable DMX centre | Implicit in IK (`pan_norm=0.5 = forward`). Field retained, no longer tunable in UI. |
| `panScale`, `tiltScale` | Primary sensitivity knob | Deprecated. Ignored in stage-space path. Hidden in UI behind an "Advanced / Legacy" panel. Removed one release after v2 ships cleanly. |
| `panOffsetDeg`, `tiltOffsetDeg` | Per-gyro calibration offsets | Subsumed by `mount_rotation`. One-time migration: if non-zero and `fixture.rotation` is identity, synthesise an equivalent mount rotation. |
| `smoothing` | EMA coefficient | Retained. Same semantics. |
| `mount_rotation` | — | Uses existing `fixture.rotation [rx, ry, rz]` degrees. Inverted mounts represented as `[180, 0, 0]` or `[0, 0, 180]` depending on convention (resolve in §11). |

### 9.2 Controller UI (desktop SPA + Android)

- Hide `panScale`, `tiltScale`, `panOffsetDeg`, `tiltOffsetDeg` in the gyro config modal and Android controller overlay.
- Keep **Smoothing** slider (operator preference).
- The "Speed" slider from #480 becomes an optional **post-scale factor** (default 1.0× = true 1:1). Document that 1.0× is the intended value. Considered for removal in the cleanup release.
- Show calibration status prominently (already on the path via the #479 live status card).

### 9.3 Backward compatibility

- Old claims still work via a `legacy_delta` code path kept in `mover_control.py` for one release cycle, triggered when `claim.R_world_to_stage` is absent.
- `.slyshow` files with fixture-level `panScale`/`tiltScale` import cleanly; values are retained but not used.
- ESP32 gyro firmware < v1.1.2 still sends plain Euler; server handles it as before.

## 10. Test plan

### 10.1 Unit tests — new file `tests/test_stage_space_orient.py`

- **Rotation primitives:**
  - Euler (ZYX) → quaternion → rotation matrix round-trip against known rotations (90° about each axis).
  - `quat_from_to` returns identity when vectors equal.
  - `frame_align` maps `(f_src, u_src) → (f_dst, u_dst)` within 1e-6 tolerance.
- **IK round-trip:**
  - For 100 random stage aim vectors, `aim_to_pan_tilt` → `pan_tilt_to_ray` reproduces the vector within 1e-6.
  - With `mount_rotation = [180, 0, 0]` (ceiling mount), a downward aim yields `tilt_norm ≈ 0.5` (mover is already aimed down when hanging).
- **Synthetic orient trace:**
  - Calibrate at identity; send 20 samples representing a 45° pitch-up rotation; assert `tilt_norm` increases by `45 / tilt_range`; `pan_norm` unchanged within 1 DMX step.
  - Same for yaw: 45° yaw change → `pan_norm` changes by `45 / pan_range`.
  - Mount rotated 180° about Z (facing stage right): same user-facing behaviour (up is up).

### 10.2 Integration tests

- End-to-end via `MoverControlEngine`: claim → calibrate-end with synthetic DMX pan/tilt → orient samples → assert DMX output matches expected curve.
- UDP: replay a recorded `CMD_GYRO_ORIENT` trace. Compare engine output to a golden pan/tilt trace captured from the manual hardware test.
- HTTP: POST recorded Android quaternion samples to `/api/mover-control/orient`; same assertions.

### 10.3 Hardware tests (`tests/user/gyro-stage-space-test.log`)

- **Convention verification:** aim the mover at the centre of the back wall; calibrate; move remote 45° up; mover should tilt up by the same angle; measure with camera.
- **Inverted mount:** hang a spare mover upside down (or set `fixture.rotation = [180, 0, 0]` in data), repeat; verify behaviour is identical from the operator's point of view.
- **Off-centre mover:** place mover at stage-left position, aim at a point on the floor centre; calibrate; move remote; verify aim tracks across the stage, not in mover-local angles.
- **Drift (ESP32 only):** observe the 3D debug ray over 2 minutes; measure how much it rotates without user motion; confirm re-calibrate corrects it.
- **Android north stability:** same, 2 minutes, expect minimal drift.

### 10.4 Regression

- Extend `tests/regression/test_mover_tracking.py` with a stage-space synthetic run: operator script → expected stage aim at each frame → expected DMX. Treat this as the canary for regressions.

## 11. Open decisions — resolve before implementation

1. **Remote "forward" axis convention** for each device.
   - Recommend: body `+Y` (screen top / 12-o'clock) for both puck and phone. Pointing the top of the device at a target is the natural operator gesture.
   - Confirm the phone convention is the same when held in landscape (screen top becomes left edge in portrait; we assume controller mode locks landscape).
2. **Euler order for `fixture.rotation`.**
   - Existing baker / viewport applies `[rx, ry, rz]` as intrinsic XYZ (confirm by reading `desktop/shared/bake_engine.py` / `scene-3d.js`). The IK must use the same order or fixtures that already render correctly will break.
3. **Android wire format** — ship quaternion in v2 (recommended) or defer and stay Euler? Impacts schedule, not math.
4. **Remote position input.**
   - v1: default to stage centre at head height (`stageW/2, stageD * 0.7, 1600`), with a setting in the controller overlay to override. No camera tracking.
   - Future: phone camera / stage camera auto-locates the operator (post-v2).
5. **Speed slider.** Keep as 1.0× post-scale for "it feels slow" escape hatch, or remove entirely? Recommend keep for v2, remove in cleanup.
6. **Inverted-mount representation.** If `fixture.rotation = [180, 0, 0]` and `[0, 0, 180]` both represent "ceiling mount" depending on convention, settle on one. Bakers and the manual-calibration wizard (#345–#371 era) already made a choice — match it.

Resolving 1–6 is the first milestone; everything downstream is straightforward math implementation.

## 12. Implementation phasing (after plan is approved)

Split from this issue once the above decisions are recorded:

1. **Math foundation.** `aim_to_pan_tilt`, `frame_align`, `quat_from_euler_zyx`, extended `pan_tilt_to_ray(mount_rotation)`. Unit tests. One PR.
2. **Engine rewrite.** `MoverControlEngine.calibrate_end` + `.orient` use the new math. Legacy path guarded by `claim.R_world_to_stage is None`. Integration tests.
3. **Wire format.** Android adds quat to orient payload. ESP32 stays as-is.
4. **Remote object + API.** `MoverClaim` fields, `GET /api/remotes/live`.
5. **3D debug visualisation.** `scene-3d.js` renders remotes.
6. **UI cleanup.** Hide legacy scale fields. Android parity (#478–#483) rebased onto the new architecture.
7. **Cleanup release.** Remove `panScale`/`tiltScale` from engine and schema after one stable cycle.
