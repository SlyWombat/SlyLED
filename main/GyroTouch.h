/*
 * GyroTouch.h — CST816S capacitive touch controller driver.
 *
 * Minimal I2C driver; no external library required.
 * Supports single-touch point and gesture detection.
 */

#ifndef GYROTOUCH_H
#define GYROTOUCH_H

#ifdef BOARD_GYRO

#include <stdint.h>

// ── Gesture codes returned by gyroTouchRead() ─────────────────────────────────
constexpr uint8_t TOUCH_GEST_NONE       = 0x00;
constexpr uint8_t TOUCH_GEST_SWIPE_UP   = 0x01;
constexpr uint8_t TOUCH_GEST_SWIPE_DOWN = 0x02;
constexpr uint8_t TOUCH_GEST_SWIPE_LEFT = 0x03;
constexpr uint8_t TOUCH_GEST_SWIPE_RIGHT = 0x04;
constexpr uint8_t TOUCH_GEST_CLICK      = 0x05;
constexpr uint8_t TOUCH_GEST_DBLCLICK   = 0x0B;
constexpr uint8_t TOUCH_GEST_LONGPRESS  = 0x0D;

// ── Initialisation ────────────────────────────────────────────────────────────

// Initialise I2C bus and reset / configure the CST816S.
// Must be called after Wire.begin() (which gyroTouchInit() calls internally).
void gyroTouchInit();

// ── Read ──────────────────────────────────────────────────────────────────────

// Poll for a touch event.  Returns true if a finger is currently detected.
// *x, *y  : touch coordinates in display pixels (0–239)
// *gesture: TOUCH_GEST_* code (TOUCH_GEST_NONE if no gesture was recognised)
//
// Call from loop() at ≥20 Hz; for best responsiveness install an interrupt on
// GYRO_TP_INT and call only when the pin is asserted.
bool gyroTouchRead(int16_t* x, int16_t* y, uint8_t* gesture);

#endif  // BOARD_GYRO
#endif  // GYROTOUCH_H
