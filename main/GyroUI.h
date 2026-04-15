/*
 * GyroUI.h — Touch-screen state machine for the Waveshare ESP32-S3 gyro board.
 *
 * State machine:  LOGO → IDLE → ACTIVE (page 0/1/2/3)
 *
 * ACTIVE pages (swipe left/right):
 *   Page 0 — Calibrate   (hold-to-calibrate)
 *   Page 1 — Colour/Flash
 *   Page 2 — Stop
 *   Page 3 — Status (park / power-save)
 */

#ifndef GYROUI_H
#define GYROUI_H

#ifdef BOARD_GYRO

#include <stdint.h>

// ── Init / loop ───────────────────────────────────────────────────────────────

// Initialise UI state and draw the LOGO screen.
void gyroUIInit();

// Called every loop() iteration. Handles touch, updates display, drives IMU.
void gyroUIUpdate();

// ── State exposed for GyroUdp ─────────────────────────────────────────────────

// 0=full, 1=pan-only, 2=tilt-only, 3=inverted (settable from SPA, not on-device)
extern uint8_t gyroUIMode;

#endif  // BOARD_GYRO
#endif  // GYROUI_H
