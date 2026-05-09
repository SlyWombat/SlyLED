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
// #813 — `gyroUdpHasLock()` retired. The Start button no longer waits
// for an orchestrator-pushed CMD_GYRO_CTRL packet; press-Start is sent
// to the broadcast address and the orchestrator answers on its bound
// UDP 4210 listener. Parent IP is captured opportunistically from
// CMD_GYRO_HEARTBEAT after the claim establishes.
uint8_t gyroUdpTargetFps();  // configured target fps

// Direct local control — used by GyroUI to toggle streaming without a UDP
// round-trip.  Does not change the stored parent IP.
void gyroUdpSetStreaming(bool enabled, uint8_t fps = 0);

// Send a discrete CMD_GYRO_STOP with a fresh nonce. Stamps the pending-stop
// retry slot so gyroUdpUpdate() retransmits up to GYRO_RETRY_MAX times if
// no CMD_GYRO_STOP_ACK arrives within GYRO_RETRY_INTERVAL_MS.
void gyroUdpSendStop();

// #867 — discrete CMD_GYRO_OFF: shares the pending-stop retry slot with
// gyroUdpSendStop() (same nonce machinery, same CMD_GYRO_STOP_ACK), only
// difference on the wire is the cmd byte. Server-side: release claim
// with blackout=True so the head goes dark.
void gyroUdpSendOff();

// Send CMD_GYRO_COLOR (0x63) to parent — colour preset or flash pulse.
// flags bit0 = flash (brief full-brightness pulse)
void gyroUdpSendColor(uint8_t r, uint8_t g, uint8_t b, uint8_t flags);

// Send CMD_GYRO_CALIBRATE (0x64) to parent — calibrate hold start/end.
// calibrating: 1 = hold started, 0 = hold released
void gyroUdpSendCalibrate(bool calibrating);

// #775 — variant that sends a caller-supplied (roll, pitch, yaw) instead
// of reading the IMU at packet-build time. Used on calibrate-end so the
// reference sample is the last-stable orientation captured during the
// hold, not whatever post-lift jiggle the IMU sees after the finger
// leaves the screen. Angles in degrees, same convention as the orient
// stream (roll = X, pitch = Y, yaw = Z, sensor frame).
void gyroUdpSendCalibrateWith(bool calibrating, float roll, float pitch, float yaw);

// #772 — explicit START packet. Press-release of the IDLE START button
// sends this once before any orient frames; server replies with claim+
// start_stream and gates the orient stream on success. Mirrors Android's
// /api/mover-control/claim → /api/mover-control/start sequence so the
// puck gets explicit deny feedback when another device already holds the
// mover instead of silently reaching ACTIVE with no DMX output.
void gyroUdpSendStart();

// #476 — heartbeat state accessors. UI polls these to show reconnecting.
uint32_t gyroGetLastHeartbeatMs();  // millis() of last CMD_GYRO_HEARTBEAT, 0 if never
bool     gyroServerClaimActive();   // server-reported claim-active flag

// #772 — one-shot read of the CMD_GYRO_CLAIM_DENIED flag. Returns true the
// first time it's polled after a deny packet arrives, then resets so the
// UI doesn't loop on it. UI uses this to revert ACTIVE → IDLE.
bool gyroUdpClaimDeniedConsume();

// #825 — rock-solid press-Start/Stop handshake.
//
// Retry budget for outbound START/STOP. 5 × 150 ms = 750 ms total — long
// enough to cover one network blip yet short enough that the operator
// gives up before they think the device is wedged.
constexpr uint16_t GYRO_RETRY_INTERVAL_MS = 150;
constexpr uint8_t  GYRO_RETRY_MAX         = 5;

// Press-Start variant that ships an explicit nonce. The puck advances UI
// only when CMD_GYRO_CLAIM_ACK arrives carrying this same nonce; stale
// ACKs from a prior START packet replay are silently dropped. Returns
// the nonce that was actually sent (= argument unless the slot was busy).
uint16_t gyroUdpSendStartWithNonce(uint16_t nonce);

// One-shot read of the "the server ACKed our latest START" flag. Returns
// true the first time it's polled after a matching CLAIM_ACK arrives;
// also exposes the mover-id the server bound the claim to via *outMover
// (optional). Pairs with gyroUdpClaimDeniedConsume().
bool gyroUdpStartAckedConsume(uint16_t* outMover = nullptr);

// True while we have an outstanding START that hasn't been ACKed or
// timed out yet. UI uses this to keep a "connecting" indicator on screen.
bool gyroUdpStartPending();

// Forces the pending-start retry slot back to "no pending" without
// sending anything. UI calls this after a timeout/denied so a future
// START doesn't see a stale slot.
void gyroUdpClearStartPending();

// One-shot read of "STOP got an ACK".
bool gyroUdpStopAckedConsume();

// True while a STOP retry is still in flight.
bool gyroUdpStopPending();

void gyroUdpClearStopPending();

// UI tells GyroUdp what state to advertise in the next CMD_GYRO_HEARTBEAT_REP.
// Values mirror Protocol.h's GYRO_UI_* constants. The UDP layer caches the
// last value and stamps it into outgoing HB_REPs at 2 s cadence.
void gyroUdpSetUiState(uint8_t uiState, uint16_t claimNonce);

#endif  // BOARD_GYRO
#endif  // GYROUDP_H
