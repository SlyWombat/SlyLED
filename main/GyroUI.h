/*
 * GyroUI.h — Touch-screen state machine for the Waveshare ESP32-S3 gyro board.
 *
 * 240×240 round GC9A01 display layout:
 *
 *           ┌─────────────────────┐
 *           │      [WiFi ●]       │  ← WiFi dot top-right (r=10)
 *           │                     │
 *           │  ┌──────────────┐   │
 *           │  │  START/STOP  │   │  ← large button (cx=120, cy=78, r=38)
 *           │  └──────────────┘   │
 *           │                     │
 *           │  R:  0.0°           │  ← roll / pitch / yaw text
 *           │  P:  0.0°           │
 *           │  Y:  0.0°           │
 *           │                     │
 *           │    [  ZERO  ]       │  ← recalibrate button (cx=120, cy=182, r=28)
 *           │                     │
 *           │  ●  ●  ●  ●         │  ← 4 mode preset dots (y=208)
 *           └─────────────────────┘
 *
 * Status ring: coloured arc drawn at r=112 around the full circle.
 *   Colour: grey=idle, green=streaming, red=no-WiFi/IMU error.
 *
 * Mode preset buttons (4 dots at y=208, x=72/96/144/168):
 *   Mode 0 (white) : Full 3-axis — roll→pan, pitch→tilt
 *   Mode 1 (cyan)  : Pan-only (tilt locked)
 *   Mode 2 (orange): Tilt-only (pan locked)
 *   Mode 3 (magenta): Inverted (roll→−pan, pitch→−tilt)
 * Selected mode is outlined; active mode is sent to parent via a reserved
 * flag in CMD_GYRO_ORIENT (bits [5:4] of the flags byte).
 */

#ifndef GYROUI_H
#define GYROUI_H

#ifdef BOARD_GYRO

// ── Init / loop ───────────────────────────────────────────────────────────────

// Initialise UI state and draw the first frame.
void gyroUIInit();

// Called every loop() iteration. Handles touch, updates display, drives IMU.
// Target: ≥10 Hz update rate (the delay(10) in loop() gives ~15 Hz slack).
void gyroUIUpdate();

// ── State exposed for GyroUdp ─────────────────────────────────────────────────

// 0=full, 1=pan-only, 2=tilt-only, 3=inverted
extern uint8_t gyroUIMode;

#endif  // BOARD_GYRO
#endif  // GYROUI_H
