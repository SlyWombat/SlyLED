/*
 * GyroIMU.h — QMI8658 6-axis IMU driver (accelerometer + gyroscope).
 *
 * Minimal I2C driver; no external library required.
 * Outputs Euler angles (roll, pitch, yaw) in degrees via a complementary filter.
 *
 * Axis convention (matches Waveshare board silkscreen):
 *   Roll  : rotation around X-axis (tilt left/right)   — range −180°..+180°
 *   Pitch : rotation around Y-axis (tilt forward/back) — range −90°..+90°
 *   Yaw   : rotation around Z-axis (twist)             — accumulated from gyro,
 *             drifts over time; call gyroIMUZero() to reset the reference.
 *
 * Floats are used deliberately: the ESP32-S3 has a hardware FPU and the
 * complementary filter requires trigonometric operations.
 */

#ifndef GYROIM_H
#define GYROIM_H

#ifdef BOARD_GYRO

#include <stdint.h>

// ── Initialisation ────────────────────────────────────────────────────────────

// Configure QMI8658: ±8g accel at 256 Hz, ±512 dps gyro at 256 Hz.
// Enable both sensors. Must be called after Wire.begin() (gyroTouchInit()
// calls Wire.begin, so gyroIMUInit() should be called after gyroTouchInit()).
// Returns true if the chip was found (WHO_AM_I check passed).
bool gyroIMUInit();

// ── Read ──────────────────────────────────────────────────────────────────────

// Read raw sensor data and update the complementary filter.
// *roll, *pitch, *yaw are written in degrees, relative to the last zero reference.
// Returns true on success; false if the I2C read failed (values unchanged).
//
// Call at ≥50 Hz for accurate yaw integration; 20 Hz is the minimum for
// meaningful angle tracking.
bool gyroIMURead(float* roll, float* pitch, float* yaw);

// ── Calibration ──────────────────────────────────────────────────────────────

// Set the current orientation as the zero reference.
// After this call, gyroIMURead() returns angles relative to the new reference.
void gyroIMUZero();

#endif  // BOARD_GYRO
#endif  // GYROIM_H
