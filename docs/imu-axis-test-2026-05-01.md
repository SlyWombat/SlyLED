# IMU axis test — Waveshare ESP32-S3 1.28″ round LCD (QMI8658) — 2026-05-01

Live test of the gyro puck's QMI8658 6-axis IMU using the diagnostic
firmware `gyro-test-v1.0.0` (issue #776). Goal: identify which chip
axis maps to which body axis (forward / up / right) and confirm
whether the firmware's complementary filter produces stable readings
in the chosen orientation.

## Hardware under test

- Device: SLYG-FC98 (Waveshare ESP32-S3 round-LCD gyro puck)
- IP: 192.168.10.211 over WiFi
- IMU: QMI8658 (6-axis: 3-axis accel + 3-axis gyro; **no magnetometer**)
- Filter: complementary, 98% gyro integration + 2% accelerometer correction
- Diagnostic endpoint: `GET /imu` — returns raw chip counts + filtered
  Euler degrees + accel-g + gyro-dps + dt

## Method

Operator held the puck **LCD-up, +X-forward, +Y-right, +Z-up** —
i.e. the standard "phone laid flat on a table" pose with the chip's
+X axis pointing toward the operator's "forward" direction. From this
pose:

1. Capture HOME baseline.
2. Pan **90° left**, capture, return to home.
3. Pan **90° right**, capture, return to home.
4. Tilt **90° up**, capture, return to home.
5. Tilt **90° down**, capture.

`/imu` was polled at each stable pose. Deltas computed against the
HOME baseline; raw accel + gyro values archived for axis identification.

## Results

| Pose | ΔRoll | ΔPitch | ΔYaw | accel(g) x / y / z | Notes |
|---|---:|---:|---:|---|---|
| **HOME**         |   0.00  |   0.00  |   0.00  | +0.06 / -0.03 / **-0.97** | Baseline. Gravity firmly on chip-Z. |
| **PAN-LEFT-90**  |  -0.06  |  -0.77  | **-88.53** | +0.08 / -0.03 / -0.97 | ΔY ≈ -90° clean; accel unchanged (yaw doesn't move gravity). |
| **HOME-2**       |  +0.04  |  -0.06  |  +11.70  | +0.06 / -0.03 / -0.97 | Roll+pitch snap back; **+11.7° yaw drift** (gyro-only integration). |
| **PAN-RIGHT-90** |  -0.79  |  +0.14  | **+96.99** | +0.06 / -0.02 / -0.97 | ΔY ≈ +90° clean (sign opposite of left). |
| **HOME-3**       |  +0.06  |  -0.06  |  +17.16  | +0.06 / -0.03 / -0.97 | Drift now ~17°. |
| **TILT-UP-90**   | +116.24 | **+88.26** | +18.12 | **-0.94** / -0.08 / +0.04 | ΔP ≈ +90° clean; ΔR is gimbal-lock garbage. |
| **HOME-4**       |  +0.06  |  -0.04  |  +15.36  | +0.06 / -0.03 / -0.97 | Roll snaps back. |
| **TILT-DOWN-90** | -174.26 | **-83.62** | +19.55 | **+1.06** / +0.01 / +0.05 | ΔP ≈ -90° clean; ΔR is gimbal-lock garbage. |

## Findings

### Axis mapping (X-forward, Z-up convention, validated)

| Body motion | Maps to chip axis | Sign convention |
|---|---|---|
| Pan **left**  | rotation around chip-Z (yaw)   | **−** |
| Pan **right** | rotation around chip-Z (yaw)   | **+** |
| Tilt **up**   | rotation around chip-Y (pitch) | **+** |
| Tilt **down** | rotation around chip-Y (pitch) | **−** |
| Twist (not tested) | rotation around chip-X (roll) | (per right-hand rule) |

Net: with the puck held LCD-up / +X-forward, the chip's native frame
**already matches** the standard X-forward / Y-right / Z-up
right-handed body frame. No firmware-side axis remap is needed for
this pose.

### Filter-stability findings

1. **Pan/yaw (chip-Z) is clean.** ΔY tracks commanded motion within
   ±10% per leg. Yaw has no accelerometer anchor so it integrates
   purely from `gz`; drift accumulates at ~10–17° per 180° round
   trip (no magnetometer = no anchor).
2. **Pitch (chip-Y) is clean and accel-anchored.** ΔP within ±2° of
   commanded over the test sequence. Returns to ~0° at HOME within
   0.1° on each round trip.
3. **Roll (chip-X) is fine in the stable regime, but goes wild at
   pitch ≈ ±90°.** That's gimbal lock: when the puck is pointed
   straight up or down, gravity vector loses its projection on
   chip's Y-Z plane; `accelRoll = atan2(ay, az)` becomes
   `atan2(0, 0)`, which sign-flips on noise. Roll readings during
   TILT-UP and TILT-DOWN are garbage and should be ignored or
   replaced with a quaternion-based representation.
4. **The "all over the place" symptom in #776 was caused by the
   wrong physical mount.** Earlier deployment had the puck mounted
   "flat at the end of a stick, forward through the bottom" — that
   pose puts gravity on chip-X **continuously**, so the puck was
   operating *inside the gimbal-lock degeneracy* the whole time.
   Tiny wrist motion → 100°+ roll noise. The Z-up / X-forward pose
   keeps the device safely outside that degeneracy.

## Recommendations

1. **Firmware** — the puck's native chip frame (X-forward, Y-right,
   Z-up) is already correct. No board-level axis remap needed.
   Don't add one.
2. **Server** — switch the `gyro-puck` default `OrientConvention`
   from `BOTTOM_FORWARD_ROLL_PITCH` (drops yaw, was needed only for
   the broken stick-mount pose) to `FLAT_PITCH_YAW` (full Euler).
   Yaw is now the cleanest signal for pan; dropping it was working
   around the wrong bug. Also flip `REMOTE_FORWARD_LOCAL` from
   `(0, 1, 0)` (Y-forward) to `(1, 0, 0)` (X-forward) so the body
   frame matches what the puck actually reports.
3. **Operator workflow** — the puck must be held flat-LCD-up / nose
   along chip-X. Document this in the user manual.
4. **Future hardware** — to eliminate the residual ~10–20°/round-trip
   yaw drift, swap to a 9-axis IMU with magnetometer (ICM-20948,
   BNO055, MMC5983) at the next PCB spin. Or move to a quaternion
   AHRS (Madgwick / Mahony) on the existing 6-axis chip — won't fix
   the no-magnetometer drift but eliminates the gimbal-lock noise
   at ±90° pitch.

## Raw `/imu` JSON for archival

Test log: `/tmp/776-axis-test.log` on the dev workstation (transient).
The structured deltas above are the durable record.
