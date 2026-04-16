/*
 * GyroUdp.h — UDP send/receive for the Waveshare ESP32-S3 gyro board.
 *
 * Implements issue #402: CMD_GYRO_ORIENT (0x60) orientation stream and
 * CMD_GYRO_CTRL (0x61) control handler, plus CMD_PING / CMD_OTA_UPDATE.
 *
 * Integration:
 *   gyroUdpInit()   — call once in setup(), after connectWiFi().
 *   gyroUdpUpdate() — call every loop() iteration.
 *   gyroUdpHandleCmd() — called by handleUdpPacket() for BOARD_GYRO.
 */

#ifndef GYROUDP_H
#define GYROUDP_H

#ifdef BOARD_GYRO

#include <stdint.h>
#include <WiFi.h>

// ── Init / loop ───────────────────────────────────────────────────────────────

// Call once after WiFi + IMU are initialised.
// Announces presence to the broadcast address via CMD_PONG.
void gyroUdpInit();

// Called every loop() iteration.
// Reads IMU and transmits CMD_GYRO_ORIENT at the configured rate when enabled.
void gyroUdpUpdate();

// ── Command dispatcher ───────────────────────────────────────────────────────

// Called by handleUdpPacket() (UdpCommon.cpp) for all BOARD_GYRO UDP commands.
void gyroUdpHandleCmd(uint8_t cmd, IPAddress sender,
                      uint8_t* payload, int plen);

// ── State accessors / setters ─────────────────────────────────────────────────

bool    gyroUdpStreaming();   // true while sending GYRO_ORIENT
uint8_t gyroUdpTargetFps();  // configured target fps

// Direct local control — used by GyroUI to toggle streaming without a UDP
// round-trip.  Does not change the stored parent IP.
void gyroUdpSetStreaming(bool enabled, uint8_t fps = 0);

// Send CMD_GYRO_COLOR (0x63) to parent — colour preset or flash pulse.
// flags bit0 = flash (brief full-brightness pulse)
void gyroUdpSendColor(uint8_t r, uint8_t g, uint8_t b, uint8_t flags);

// Send CMD_GYRO_CALIBRATE (0x64) to parent — calibrate hold start/end.
// calibrating: 1 = hold started, 0 = hold released
void gyroUdpSendCalibrate(bool calibrating);

#endif  // BOARD_GYRO
#endif  // GYROUDP_H
