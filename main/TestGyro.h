/*
 * TestGyro.h — diagnostic build of the Waveshare ESP32-S3 1.28" round-LCD
 * gyro puck. Compiled when both BOARD_GYRO and GYRO_TEST_BOARD are defined.
 *
 * Goal (issue #776): show what the IMU actually reports per axis, so we
 * can resolve the "head moves all over the place vs tiny wrist motion"
 * symptom by inspecting raw chip counts + filter output before any
 * server-side convention transform masks the picture.
 *
 * Same hardware + same OTA receive path as the regular gyro firmware,
 * so the test build can be pushed onto a live puck via OTA and the
 * regular firmware can be pushed back the same way once diagnostics
 * are done.
 */

#ifndef TESTGYRO_H
#define TESTGYRO_H

#if defined(BOARD_GYRO) && defined(GYRO_TEST_BOARD)

#include <WiFi.h>   // WiFiClient is a typedef on ESP32, not a class

// Init: clears the screen and draws the static frame (header, axis
// labels). Must be called after gyroDisplayInit() + gyroIMUInit().
void testGyroSetup();

// Tick: reads the IMU, repaints live values at ~10 Hz, polls the touch
// for a "zero" tap. Replaces gyroUIUpdate() in the diagnostic build.
void testGyroUpdate();

// Serve GET /imu — returns a JSON snapshot of the latest IMU read:
//   raw chip counts, accel-g, gyro-dps, filtered Euler (with + without
//   zero ref), dt, uptime. Mounted in UdpCommon.cpp's HTTP dispatcher.
void testGyroSendImuJson(WiFiClient& client);

#endif  // BOARD_GYRO && GYRO_TEST_BOARD
#endif  // TESTGYRO_H
