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

This treats the remote's roll axis as pan and pitch axis as tilt, regardless of where the stage object is mounted, whether it's inverted, or where in the stage it sits. Inverted mounts, off-centre fixtures, and diagonal aiming all feel wrong. The Android client hits the same engine so it has the same bug. The architecture also fuses two concerns — "the remote's orientation in stage space" and "what pan/tilt value to send to this specific mover" — into a single code path, which is why one can't exist without the other today.

The user model (verbatim from #484):

> The selected stage object (eg moving head) is currently oriented at its known orientation vector. Calibration is where the remote is moved without the stage object moving so the user aligns the remote's current forward vector with that same stage object vector. From that moment on, the remote's orientation is known in stage space. Features then build upon this accurate representation of this object. The first feature is that a selected moving head follows the same direction vector the remote is.

Key distinctions this model draws, each of which must be reflected in the architecture:

1. **The calibration target is any stage object with an orientation vector.** A moving head is the first use case, not the only one. Anything in the stage with a forward direction (mover aim, prop facing, camera bore-sight) can be the reference. The calibration math must not bake in "mover" as an assumption.
2. **During calibration the stage object does not move.** Only the remote moves. The user physically rotates the remote until its forward direction matches the stage object's known vector, then signals done. The engine must hold the stage object still during the calibration window (no DMX updates from timelines, shows, or other sources to the target fixture).
3. **After calibration, the remote's orientation in stage space is a primitive.** It is a first-class property of the remote — `remote.R_world_to_stage` — independent of any mover or consumer. Queryable by anyone.
4. **Consumer features read the primitive.** "A selected moving head follows the same direction vector the remote is" is **feature #1** built on the primitive. Others that will come later (record a motion path, drive a floor spot, aim a tracking camera, snap-to-object) read the same primitive. The primitive owes them nothing except "here's where the remote is pointing, in stage coordinates."
5. **1:1 in stage angles.** No `panScale` — rotation of the remote by θ degrees in stage rotates the consumer's output by θ degrees in stage.

These five points are the spec. The rest of this document is how to realise them.

## 2. Goals and non-goals

**Goals**
- **Primitive:** remote orientation in stage space is a standalone, queryable property of each remote. Not coupled to any mover or consumer.
- **Calibration** is against any stage object with an orientation vector, not hard-coded to movers. Stage object is held still by the engine during calibration.
- **Feature #1 — mover-follow:** 1:1 mapping from remote rotation to mover aim rotation in stage angles. Shipped alongside the primitive, but isolated from it.
- Inverted / angled fixture mounts are handled by the mount rotation matrix, not per-fixture flip flags.
- One pipeline handles both ESP32 puck and Android phone.
- Each active remote is a first-class stage-space object (position + orientation) with a visible aim ray in the 3D viewport.

**Non-goals (v1)**
- Consumer features beyond mover-follow (record/playback of remote paths, floor-spot aiming, camera slewing, snap-to-object). These are downstream and live in their own issues once the primitive exists.
- Simultaneous control of multiple consumers from one remote.
- Absolute north-referenced control without calibration.
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

## 4. Primitive — remote orientation in stage space

The primitive has two operations: `calibrate(remote, target)` sets `remote.R_world_to_stage`, and `update(remote, orient_sample)` refreshes `remote.aim_stage` per incoming sensor frame. No mover, no DMX, no consumer at this layer.

### 4.1 Calibration

**Entry point:** `POST /api/remotes/<id>/calibrate-end` with body `{ "targetObjectId": <id>, "targetKind": "mover" | "fixture" | "object" }`.

**Precondition during the calibration hold window:**
- Consumer output from this remote is paused (no mover-follow writes).
- The target stage object is **held still** — any active timeline or show source writing to the target is suppressed until calibration ends or is cancelled. Implementation: on `calibrate-start`, the engine marks the target's output channels as "held" and re-asserts the last written value each tick.
- The remote continues to stream orientation samples so the engine has fresh data when the user triggers `calibrate-end`.

**Steps on `calibrate-end`:**

1. **Resolve the target's stage-space orientation vector** `a_stage`:
   - Mover: `a_stage = R_mount_to_stage · pan_tilt_to_ray(p_norm, t_norm, pan_range, tilt_range)` using the last DMX pan/tilt for this fixture. `R_mount_to_stage` built from `fixture.rotation` (Euler deg).
   - Other stage objects (fixture with a face direction, camera bore-sight, prop facing): `a_stage = R_object_to_stage · object_forward_local`, where `object_forward_local` is that object type's canonical "forward" axis.
   - Defined in a single resolver `object_orientation_vector(obj) -> (pos, a_stage)` so new object kinds slot in without touching calibration.
2. **Choose the stage "up" reference:**
   `u_stage = normalize(Z_stage - (Z_stage · a_stage) · a_stage)`
   (project stage +Z onto plane perpendicular to `a_stage`; gives a well-defined "top" direction for the aim).
3. **Read the remote's current orientation** from the last orient sample (see §5 for wire formats), producing a body→world quaternion `q_body_to_world`.
4. **Compute remote forward and up in remote world:**
   `f_remote = q_body_to_world · [0, 1, 0]ᵀ`  (body +Y = forward)
   `u_remote = q_body_to_world · [0, 0, 1]ᵀ`  (body +Z = up)
5. **Solve two-axis frame alignment.** Find the rotation `R_world_to_stage` such that:
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
   Note: the user model is expressed as a single-vector alignment (align the remote's forward to the object's forward). The math uses two axes (forward + up) because aligning forward alone leaves the remote's rotation about its own forward axis unconstrained, which makes the downstream IK ambiguous. The "up" constraint is a derived engineering requirement, not an addition to the user model — it is how "any position change by the remote is represented correctly in stage space" is made true.
6. **Store on the remote:**
   ```
   remote.R_world_to_stage = R_world_to_stage   # quaternion
   remote.calibrated       = True
   remote.calibrated_at    = now()
   remote.calibrated_against = {"objectId": ..., "kind": ...}  # for debug/UX
   ```
7. **Release the target's held output.** Timelines/shows may resume writing to it.
8. **Delete the old delta fields** from `MoverClaim`: `ref_roll/pitch/yaw`, `ref_pan/tilt`, `pan_scale`, `tilt_scale` are removed. No shim.

### 4.2 Live primitive update

**Entry points:**
- UDP `CMD_GYRO_ORIENT` (ESP32) — binary Euler × 100.
- HTTP `POST /api/remotes/<id>/orient` (Android) — JSON, prefers quaternion (§6).

Per sample:

1. **Build body→world quaternion.**
   ESP32: Euler (ZYX) → quaternion.
   Android: use the `quat` field directly if present; else Euler fallback.
2. **Transform body axes into remote world:**
   `f_world = q_body_to_world · [0, 1, 0]ᵀ`
   `u_world = q_body_to_world · [0, 0, 1]ᵀ`
3. **Map remote world → stage via calibration:**
   `a_stage = remote.R_world_to_stage · f_world`
   `u_stage = remote.R_world_to_stage · u_world`  (kept for features that care about roll about the aim axis)
4. **Store on the remote:**
   ```
   remote.aim_stage  = a_stage
   remote.up_stage   = u_stage
   remote.last_data  = now()
   ```
5. **Notify consumers** — any feature subscribed to this remote (see §5) reads the updated `aim_stage` and acts on it.

That is the entirety of the primitive. Output = a unit aim vector in stage coordinates, plus a timestamp.

## 5. Feature #1 — mover-follow

A consumer of the primitive. Subscribes to a remote, reads `remote.aim_stage` on each update, computes pan/tilt IK against its assigned mover, writes DMX. Contains no orientation logic of its own.

**State:** still lives in `MoverControlEngine`. The claim (device → mover mapping, TTL, streaming lifecycle) stays. What changes is that the engine no longer touches Euler angles — it consumes `remote.aim_stage` directly.

**Per update** (triggered when `remote.aim_stage` changes):

1. Look up the mover fixture (`pos`, `rotation`, `pan_range`, `tilt_range`).
2. **Inverse-kinematics: stage aim → pan/tilt.** New helper in `mover_calibrator.py`:
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
   Algebraically the inverse of `pan_tilt_to_ray` when `mount_rotation = identity`. Round-trip verified by unit test.
3. **EMA smoothing** (per-fixture `smoothing` field, default 0.15):
   `pan_smooth  += α · (pan_norm  - pan_smooth)`
   `tilt_smooth += α · (tilt_norm - tilt_smooth)`
   where α = `1 - smoothing` as in the current engine. Document the sign convention where the field is defined.
4. **Write DMX.** Reuse `_write_dmx()` from `mover_control.py` unchanged.

**No `panScale` / `tiltScale`.** The feature inherits 1:1 from the primitive.

Future consumer features (floor-spot follow, record/replay, camera slewing, snap-to-object) implement their own step 2+ against the same `remote.aim_stage`. The primitive does not change.

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

A remote is a first-class stage-space object — not an attribute of a claim. It owns its own orientation state (the primitive). Consumer features like mover-follow reference a remote by id; they don't store rotation data.

### 7.1 Persistence

Remotes appear in the layout alongside fixtures, so operators can place a puck or phone at its known position and the viewport can render it.

Stored in `desktop/shared/data/remotes.json` (new file) or a new array in the existing layout JSON, whichever fits the existing persistence pattern (resolve during implementation):

```json
{
  "remotes": [
    {
      "id":   1,
      "name": "Stage Left Puck",
      "kind": "gyro-puck",
      "deviceId": "gyro-192.168.10.201",
      "pos":  [1500, 2100, 1600],
      "rot":  [0, 0, 0]
    }
  ]
}
```

`pos` is operator position in stage mm. Entered from the layout UI (preferred) or defaulted to stage centre at head height (`stageW/2, stageD*0.7, 1600`) on first appearance if the user hasn't placed it. `rot` is an optional mounting orientation for static remotes; most remotes leave it identity since orientation is driven live by the sensor.

### 7.2 Runtime fields (not persisted)

Attached to the remote record at runtime by the engine:

| Field | Type | Purpose |
|-------|------|---------|
| `R_world_to_stage` | quaternion `[w, x, y, z]` | Calibration result. Persist to `remotes.json` on calibrate-end so a reboot doesn't force re-calibration (decision — keep across sessions or wipe on startup?). |
| `calibrated` | bool | Whether `R_world_to_stage` is valid. |
| `calibrated_against` | `{objectId, kind}` | Debug / UX — which object was used for the last calibration. |
| `last_quat_world` | quaternion | Most recent sensor orientation in remote world frame. |
| `aim_stage` | `[dx, dy, dz]` | Primitive output — aim unit vector in stage coords. |
| `up_stage` | `[dx, dy, dz]` | Stage-space "up" at the aim (for future roll-sensitive features). |
| `last_data` | float timestamp | Freshness indicator for the viewport. |
| `connection_state` | `"idle" \| "armed" \| "streaming" \| "stale"` | Lifecycle shown in viewport + UI. |

The `MoverClaim` in the engine becomes lean: just `(mover_id, remote_id, state, last_write_ts, smoothing)`. No orientation fields. Read orientation from `remotes[remote_id].aim_stage` on each tick.

### 7.3 Read API

```
GET /api/remotes                          -> static list (pos, kind, name)
GET /api/remotes/live                     -> runtime state (aim, calibration, claim, freshness)
POST /api/remotes/<id>                    -> edit pos/name
POST /api/remotes/<id>/calibrate-start    -> begin calibration (holds target object)
POST /api/remotes/<id>/calibrate-end      -> compute R_world_to_stage
POST /api/remotes/<id>/orient             -> push orientation sample (Android); ESP32 keeps UDP
DELETE /api/remotes/<id>                  -> remove
```

`/api/remotes/live` response shape:

```json
{
  "remotes": [
    {
      "id":         1,
      "deviceId":   "gyro-192.168.10.201",
      "kind":       "gyro-puck",
      "name":       "Stage Left Puck",
      "pos":        [1500, 2100, 1600],
      "aim":        [0.20, 0.80, -0.55],
      "calibrated": true,
      "calibratedAgainst": {"objectId": 3, "kind": "mover"},
      "connectionState": "streaming",
      "lastDataAge":     0.12,
      "consumers": [
        {"feature": "mover-follow", "targetId": 3, "targetName": "Stage Left MH"}
      ]
    }
  ]
}
```

Frontend polls at the `/api/fixtures/live` cadence (~10 Hz) while the 3D viewport is visible.

## 8. 3D debug visualisation

In the Three.js viewport (`desktop/shared/spa/js/scene-3d.js`), add a `remotes` group parallel to fixtures and temporal objects:

- **Icon at `pos`:** small sprite — disc for gyro puck, rounded rectangle for phone.
- **Ray from `pos` along `aim`** of configurable length (default 3000 mm or until it hits floor/wall, whichever is first — reuse `ray_surface_intersect` at `mover_calibrator.py:660`).
- **Colour coding by connection state + data age:**
  - Green = streaming, fresh (age < 2s)
  - Amber = streaming, stale (age 2–10s)
  - Blue = armed (calibrated, no live data yet)
  - Grey = idle / uncalibrated
- **Label:** `{remoteName} → {consumer targets}`  (e.g. "Stage Left Puck → Stage Left MH").

Rationale: temporal objects currently only carry position + bounding box (`parent_server.py:4072–4111`). Remotes have orientation and a ray, so a dedicated group is cleaner than shoehorning into temporals. The viewport rendering is a pure consumer of `/api/remotes/live` — no coupling to mover-follow.

## 9. Clean cut — no backwards compatibility

This is a clean rewrite. No legacy code path, no deprecation cycle, no shim. The v2 release ships without the old delta math. Users re-calibrate once after updating; nothing else transfers.

### 9.1 Fixture schema — delete these fields outright

Remove from the fixture schema, from all `_fixtures` entries at load time (drop on read), from `/api/fixtures` responses, from the SPA/Android UI, from the engine:

- `panScale`, `tiltScale`
- `panOffsetDeg`, `tiltOffsetDeg`
- `panCenter`, `tiltCenter` (pan=0.5 = forward-in-mount is now the only convention)

Keep and reuse:

- `fixture.rotation = [rx, ry, rz]` (Euler deg) — the mount rotation for IK. Already exists. Inverted / angled mounts use this; no separate flip flag.
- `smoothing` — EMA coefficient, still an operator preference.

### 9.2 Data files (`.slyshow`, `_fixtures.json`, etc.)

Old files opened by a v2 build get the deleted fields silently stripped on load, then saved without them on next write. No loader errors, no warnings. If a user's old calibration was encoded only in the removed fields, they re-calibrate — there's nothing to migrate.

### 9.3 Controller UI

- Gyro config modal (`desktop/shared/spa/js/setup-ui.js`): remove the Speed slider, the Advanced Tuning `<details>` block, and every legacy numeric input. Remaining controls: Send Lock, live status card, Smoothing slider, mover assignment, name.
- Android controller overlay: same — no Speed/pan/tilt scale UI. Smoothing slider is the only tuning control.

### 9.4 Engine split

The current `mover_control.py` owns both the primitive and the feature. v2 splits them:

- New `desktop/shared/remote_orientation.py` — primitive. `Remote` class (fields in §7), `calibrate()`, `update(orient_sample)`, persistence I/O. Knows nothing about movers.
- `desktop/shared/mover_control.py` — feature-only after the split. Each tick: read `Remote.aim_stage` from the primitive module for the remote id in the claim, run IK, write DMX. Delete `_euler_to_aim`, the `claim.calibrated` delta-reference capture, and the `pan_scale`/`tilt_scale` multipliers.

Both files shrink compared to today's combined file.

### 9.5 Firmware

ESP32 puck firmware stays as-is (still sends Euler via `CMD_GYRO_ORIENT`). No protocol break on the wire. The change is server-side.

Android APK in v2 adds the `quat` field to `POST /api/mover-control/orient` (§6). If an older APK is in the field, the server falls back to Euler → quaternion and still runs the stage-space pipeline. This isn't a compatibility layer — it's just that the math accepts both input shapes.

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

Split by layer:

**Primitive (`remote_orientation.py`) — no movers involved:**
- Calibrate remote against a synthetic stage object with `a_stage = (0, 1, 0)`. Assert `remote.R_world_to_stage · remote_forward = a_stage`.
- Send 20 orient samples representing a 45° pitch-up rotation. Assert `remote.aim_stage` rotates by 45° about the stage X axis.
- Calibrate against an object aimed at a diagonal (`(0.5, 0.5, -0.5)` normalised). Verify aim tracks correctly through arbitrary remote motion.

**Feature (`mover-follow` in `mover_control.py`) — consumes primitive:**
- Stub the primitive with a scripted sequence of `aim_stage` vectors. Assert the DMX buffer contains the expected pan/tilt for each.
- Inverted mount (`fixture.rotation = [180, 0, 0]`) + downward aim: mover DMX is pan=0.5, tilt=0.5.

**End-to-end:**
- UDP: replay a recorded `CMD_GYRO_ORIENT` trace through the full stack. Compare DMX output to a golden trace.
- HTTP: POST recorded Android quaternion samples to `/api/remotes/<id>/orient`; same assertions.

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
   - v1: default to stage centre at head height (`stageW/2, stageD * 0.7, 1600`), with placement via the layout UI. No camera tracking.
   - Future: phone camera / stage camera auto-locates the operator (post-v2).
5. **Inverted-mount representation.** If `fixture.rotation = [180, 0, 0]` and `[0, 0, 180]` both represent "ceiling mount" depending on convention, settle on one. Bakers and the manual-calibration wizard (#345–#371 era) already made a choice — match it.
6. **Calibration target kinds for v1.** Movers are required. Which other objects must be supported on day one — just fixtures with a facing direction, or also arbitrary stage props? Recommend ship with mover + "any fixture with `rotation`" and defer free props to a follow-up.
7. **Persist `R_world_to_stage` across sessions?** Reboot-stable calibration is nice, but the remote's physical reference (the hand-held pointing direction) is lost on reboot. Recommend: persist, but auto-flag as "stale" after N days and prompt re-calibration.

Resolving 1–7 is the first milestone; everything downstream is straightforward math implementation.

## 12. Implementation phasing (after plan is approved)

Split from this issue once the above decisions are recorded. Each phase is a separate PR; they land as v2 in order. The primitive comes first and ships standalone so features can be written against it in isolation.

1. **Math foundation.** `aim_to_pan_tilt`, `frame_align`, `quat_from_euler_zyx`, extended `pan_tilt_to_ray(mount_rotation)`. Unit tests. Zero runtime impact.
2. **Primitive — `remote_orientation.py`.** New module. `Remote` class, `calibrate()`, `update()`, persistence to `remotes.json`. `GET /api/remotes`, `/live`, `/calibrate-*`, `/orient`. Target-holding during calibration. Integration tests against a stub consumer.
3. **Wire format.** Android adds `quat` to orient payload. ESP32 unchanged.
4. **3D debug visualisation.** `scene-3d.js` renders remotes + aim rays from `/api/remotes/live`. This lands before feature-rewrite so we can **see** whether the primitive is right before plumbing it into DMX. If it looks wrong in the viewport, fix the primitive, not the feature.
5. **Feature rewrite — mover-follow.** `mover_control.py` reduced to the IK-and-DMX consumer. Reads `aim_stage` from the primitive. Delete `_euler_to_aim`, delta fields, `pan_scale`/`tilt_scale`. Integration + hardware tests.
6. **UI cut.** Delete `panScale` / `tiltScale` / `panOffsetDeg` / `tiltOffsetDeg` / `panCenter` / `tiltCenter` from the schema, SPA modals, and Android overlay. Rebase Android parity work (#478–#483) onto the new architecture.

No cleanup release — everything legacy is gone by the end of phase 6. Phases 2–4 give a working "calibrate and see the ray move" experience with no moving fixtures involved, which is the cheapest way to shake out the primitive before hardware risk.
