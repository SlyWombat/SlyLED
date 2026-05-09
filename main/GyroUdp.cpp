/*
 * GyroUdp.cpp — UDP implementation for the gyro orientation controller board.
 *
 * On boot the board broadcasts a CMD_PONG (same wire format as an LED child,
 * stringCount=0) so the parent server's existing discovery path picks it up.
 *
 * CMD_GYRO_CTRL (0x61, parent→gyro):
 *   payload[0] = enabled (1=start, 0=stop)
 *   payload[1] = targetFps (0 = default 20 Hz, max 50)
 *   The sender IP is stored as the unicast target for CMD_GYRO_ORIENT.
 *
 * CMD_GYRO_ORIENT (0x60, gyro→parent):
 *   Sent at up to targetFps Hz while enabled.
 *   Fields: roll100, pitch100, yaw100 (int16, ×100), fps (uint8), flags (uint8).
 *
 * CMD_OTA_UPDATE (0x50, parent→gyro):
 *   Same wire format as child OTA — triggers otaStartUpdate() + ESP.restart().
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroUdp.h"
#include "GyroIMU.h"
#include "GyroUI.h"
#include "OtaUpdate.h"
#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "version.h"
#include <Arduino.h>
#include <WiFiUdp.h>

// ── Module state ──────────────────────────────────────────────────────────────

static bool     s_streaming  = false;
static uint8_t  s_targetFps  = 20;      // default 20 Hz
static IPAddress s_parentIP  = IPAddress(255, 255, 255, 255);  // updated from CMD_GYRO_CTRL

// fps accounting
static uint32_t s_fpsWindowMs = 0;
static uint8_t  s_fpsTxCount  = 0;
static uint8_t  s_actualFps   = 0;

// #476 — last heartbeat tick from the parent. 0 = never heard.
// The UI layer (GyroUI.cpp) reads this via gyroGetLastHeartbeatMs() to
// decide whether to show "Reconnecting..." or drop out of the active page.
static uint32_t s_lastHeartbeatMs = 0;
static uint8_t  s_serverClaimActive = 0;   // mirrored from the heartbeat payload
// #772 — set when the server refuses a claim attempt; consumed by the UI
// on the next tick to revert to IDLE with a brief "BUSY" indication.
static bool     s_claimDenied = false;

// #825 — rock-solid handshake bookkeeping.
//
// pendingStart/pendingStop slots track outbound packets that haven't
// been ACKed yet. While set, gyroUdpUpdate() retransmits at
// GYRO_RETRY_INTERVAL_MS cadence up to GYRO_RETRY_MAX retries. The UI
// reads the *Acked* flags to decide when to advance / revert state.
static uint16_t s_pendingStartNonce  = 0;
static uint32_t s_pendingStartLastMs = 0;
static uint8_t  s_pendingStartTries  = 0;
static bool     s_startAcked         = false;
static uint16_t s_startAckedMover    = 0;

static uint16_t s_pendingStopNonce  = 0;
static uint32_t s_pendingStopLastMs = 0;
static uint8_t  s_pendingStopTries  = 0;
static bool     s_stopAcked         = false;

// HB_REP advertised state (set by UI via gyroUdpSetUiState; sent every 2 s).
static uint8_t  s_hbUiState     = 0;     // GYRO_UI_IDLE
static uint16_t s_hbClaimNonce  = 0;
static uint16_t s_hbSeq         = 0;
static uint32_t s_hbRepLastMs   = 0;

uint32_t gyroGetLastHeartbeatMs() { return s_lastHeartbeatMs; }
bool     gyroServerClaimActive()  { return s_serverClaimActive != 0; }

// ── Helpers ───────────────────────────────────────────────────────────────────

static void sendGyroPong(IPAddress dest) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_PONG;
    hdr.epoch   = (uint32_t)currentEpoch();

    PongPayload pp;
    memset(&pp, 0, sizeof(pp));
    const char* hn = WiFi.getHostname();
    strncpy(pp.hostname, hn ? hn : "SLYG-????", sizeof(pp.hostname) - 1);
    strncpy(pp.altName, "Gyro Controller", sizeof(pp.altName) - 1);
    pp.stringCount = 0;     // no LED strings
    pp.fwMajor = APP_MAJOR;
    pp.fwMinor = APP_MINOR;
    pp.fwPatch = APP_PATCH;

    uint8_t buf[sizeof(hdr) + sizeof(pp)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &pp,  sizeof(pp));

    cmdUDP.beginPacket(dest, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();
}

// ── Public API ────────────────────────────────────────────────────────────────

void gyroUdpInit() {
    // Announce presence on the broadcast address so the parent discovers us
    sendGyroPong(IPAddress(255, 255, 255, 255));
    if (Serial) {
        Serial.print(F("[GyroUDP] Init — PONG broadcast. Hostname: "));
        Serial.println(WiFi.getHostname() ? WiFi.getHostname() : "?");
    }
}

// #813 follow-up — battery telemetry. Sent every ~10 s regardless of
// streaming state so the orchestrator can surface battery level (and
// later, charging) without operator interaction. Defined as a free
// function so it can be called from gyroUdpUpdate() (always) AND from
// gyroUdpInit() once the WiFi is up (one immediate sample).
extern float gyroReadBatteryVoltage();   // forward — defined in GyroUI.cpp
extern int   gyroReadBatteryPercent();   // forward — same
extern bool  gyroReadBatteryCharging();  // forward — same
static uint32_t s_lastBattSendMs = 0;
static void sendBatteryTelemetry(IPAddress dest) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_BATT;
    hdr.epoch   = (uint32_t)currentEpoch();

    GyroBattPayload bp;
    float v = gyroReadBatteryVoltage();
    if (v < 0) {
        bp.vbat100 = 0;
        bp.pct = 0xFFu;
        bp.flags = 0;
    } else {
        bp.vbat100 = (uint16_t)(v * 100.0f);
        int pct = gyroReadBatteryPercent();
        bp.pct = (pct < 0) ? 0xFFu : (uint8_t)pct;
        bp.flags = gyroReadBatteryCharging() ? 0x01u : 0u;
    }

    uint8_t buf[sizeof(hdr) + sizeof(bp)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &bp,  sizeof(bp));

    cmdUDP.beginPacket(dest, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();
}

// #825 — low-level helpers that build + ship a single START or STOP frame
// with the supplied nonce. Used both by the public send-with-nonce entry
// points and by the internal retransmit path in gyroUdpUpdate().
static void txStartFrame(uint16_t nonce) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_START;
    hdr.epoch   = (uint32_t)currentEpoch();
    GyroStartStopPayload p;
    p.nonce = nonce;
    uint8_t buf[sizeof(hdr) + sizeof(p)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &p,   sizeof(p));
    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();
}

static void txStopFrame(uint16_t nonce) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_STOP;
    hdr.epoch   = (uint32_t)currentEpoch();
    GyroStartStopPayload p;
    p.nonce = nonce;
    uint8_t buf[sizeof(hdr) + sizeof(p)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &p,   sizeof(p));
    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();
}

static void txHeartbeatRep() {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_HEARTBEAT_REP;
    hdr.epoch   = (uint32_t)currentEpoch();
    GyroHeartbeatRepPayload p;
    p.uiState    = s_hbUiState;
    p.claimNonce = s_hbClaimNonce;
    p.seq        = ++s_hbSeq;
    uint8_t buf[sizeof(hdr) + sizeof(p)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &p,   sizeof(p));
    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();
}

void gyroUdpUpdate() {
    // #813 follow-up — battery telemetry runs every 10 s regardless of
    // whether the orient stream is active. Cheap (~16 ADC reads + one
    // 12-byte UDP packet); lets the orchestrator surface battery state
    // continuously.
    uint32_t now = (uint32_t)millis();
    if (now - s_lastBattSendMs >= 10000u) {
        s_lastBattSendMs = now;
        sendBatteryTelemetry(s_parentIP);
    }

    // #825 — START retransmission. Each retry reuses the same nonce so
    // the orchestrator dedupes; we just keep prodding the wire until an
    // ACK arrives or we burn the retry budget.
    if (s_pendingStartNonce != 0
        && (now - s_pendingStartLastMs) >= GYRO_RETRY_INTERVAL_MS) {
        if (s_pendingStartTries >= GYRO_RETRY_MAX) {
            // Budget exhausted. Caller's UI tick will notice via the
            // pending-still-set + stale lastMs and fall back to a
            // "no response" indication; we don't escalate from here.
        } else {
            s_pendingStartTries++;
            s_pendingStartLastMs = now;
            txStartFrame(s_pendingStartNonce);
        }
    }

    // #825 — STOP retransmission. Same shape as START.
    if (s_pendingStopNonce != 0
        && (now - s_pendingStopLastMs) >= GYRO_RETRY_INTERVAL_MS) {
        if (s_pendingStopTries < GYRO_RETRY_MAX) {
            s_pendingStopTries++;
            s_pendingStopLastMs = now;
            txStopFrame(s_pendingStopNonce);
        }
    }

    // #825 — heartbeat reply at 2 s cadence. Sent regardless of streaming
    // state so the orchestrator can detect divergence (puck rolled back,
    // orchestrator restarted) without needing the orient stream. Default
    // advertised state is IDLE; UI updates it via gyroUdpSetUiState().
    if (now - s_hbRepLastMs >= 2000u) {
        s_hbRepLastMs = now;
        txHeartbeatRep();
    }

    if (!s_streaming) return;

    uint32_t intervalMs = (s_targetFps > 0) ? (1000u / s_targetFps) : 50u;  // 20 Hz default
    static uint32_t s_lastSendMs = 0;
    if (now - s_lastSendMs < intervalMs) return;
    s_lastSendMs = now;

    // Read IMU
    float roll = 0.0f, pitch = 0.0f, yaw = 0.0f;
    bool imuOk = gyroIMURead(&roll, &pitch, &yaw);

    // Build packet
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_ORIENT;
    hdr.epoch   = (uint32_t)currentEpoch();

    GyroOrientPayload op;
    op.roll100  = (int16_t)(roll  * 100.0f);
    op.pitch100 = (int16_t)(pitch * 100.0f);
    op.yaw100   = (int16_t)(yaw   * 100.0f);
    op.fps      = s_actualFps;
    op.flags    = (s_streaming ? 0x01u : 0u)
                | (imuOk       ? 0x02u : 0u)
                | (WiFi.status() == WL_CONNECTED ? 0x04u : 0u)
                | ((uint8_t)(gyroUIMode & 0x03u) << 4);  // bits[5:4] = mode preset

    uint8_t buf[sizeof(hdr) + sizeof(op)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &op,  sizeof(op));

    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();

    // fps accounting: count packets in a 1-second window
    s_fpsTxCount++;
    if (now - s_fpsWindowMs >= 1000u) {
        s_actualFps    = s_fpsTxCount;
        s_fpsTxCount   = 0;
        s_fpsWindowMs  = now;
    }
}

void gyroUdpHandleCmd(uint8_t cmd, IPAddress sender,
                      uint8_t* payload, int plen) {
    if (cmd == CMD_PING) {
        sendGyroPong(sender);

    } else if (cmd == CMD_GYRO_CTRL && plen >= (int)sizeof(GyroCtrlPayload)) {
        GyroCtrlPayload ctrl;
        memcpy(&ctrl, payload, sizeof(ctrl));
        if (ctrl.targetFps > 0 && ctrl.targetFps <= 50)
            s_targetFps = ctrl.targetFps;
        if (ctrl.enabled) {
            // Capture parent IP — establishes the "lock"
            // Don't set s_streaming — user must press START on the device
            s_parentIP = sender;
        } else {
            // Disable — stop streaming + clear the lock
            s_streaming = false;
            s_parentIP = IPAddress(255, 255, 255, 255);
        }
        if (Serial)
            Serial.printf("[GyroUDP] CTRL: lock=%d fps=%d streaming=%d parent=%s\n",
                          ctrl.enabled, s_targetFps, s_streaming, sender.toString().c_str());

    } else if (cmd == CMD_GYRO_RECAL) {
        gyroIMUZero();
        if (Serial) Serial.println(F("[GyroUDP] RECAL: IMU zeroed by parent"));

    } else if (cmd == CMD_GYRO_HEARTBEAT) {
        // Payload = state(1) + claimActive(1); both optional. Any heartbeat
        // counts as "parent is alive"; we just stamp the clock.
        s_lastHeartbeatMs = millis();
        if (plen >= 2) {
            s_serverClaimActive = payload[1];
        } else {
            s_serverClaimActive = 1;
        }
        // #813 green-field — capture parent IP from heartbeats so
        // post-claim outbound packets switch from broadcast (default)
        // to unicast. The orchestrator no longer sends CMD_GYRO_CTRL
        // periodically, so this heartbeat is the puck's only learned-
        // address signal during a claim. First heartbeat arrives ~2 s
        // after CMD_GYRO_START is accepted.
        if (sender != IPAddress(255, 255, 255, 255)) {
            s_parentIP = sender;
        }

    } else if (cmd == CMD_GYRO_CLAIM_DENIED) {
        // #772 — server refused our claim. UI polls
        // gyroUdpClaimDeniedConsume() and reverts to IDLE with a brief
        // BUSY indication, mirroring Android's "Mover claimed by another
        // device" toast. Also clear the pending-start retry slot so the
        // wire goes quiet (#825).
        s_claimDenied = true;
        s_pendingStartNonce = 0;
        s_pendingStartTries = 0;
        if (Serial) Serial.println("[GyroUDP] CLAIM_DENIED — mover busy");

    } else if (cmd == CMD_GYRO_CLAIM_ACK
               && plen >= (int)sizeof(GyroClaimAckPayload)) {
        // #825 — server confirmed claim+start_stream succeeded. Match the
        // echoed nonce against our outstanding START so a replayed stale
        // ACK from a prior gesture can't accidentally advance the UI.
        GyroClaimAckPayload ack;
        memcpy(&ack, payload, sizeof(ack));
        if (s_pendingStartNonce != 0 && ack.nonce == s_pendingStartNonce) {
            s_startAcked       = true;
            s_startAckedMover  = ack.moverId;
            s_pendingStartNonce = 0;
            s_pendingStartTries = 0;
            // Capture parent IP so further unicast packets find the server
            // even before the first parent→gyro HEARTBEAT arrives.
            if (sender != IPAddress(255, 255, 255, 255)) {
                s_parentIP = sender;
            }
            if (Serial)
                Serial.printf("[GyroUDP] CLAIM_ACK nonce=%u mover=%u\n",
                              ack.nonce, ack.moverId);
        } else if (Serial) {
            Serial.printf("[GyroUDP] CLAIM_ACK stale (got=%u pending=%u)\n",
                          ack.nonce, s_pendingStartNonce);
        }

    } else if (cmd == CMD_GYRO_STOP_ACK
               && plen >= (int)sizeof(GyroStopAckPayload)) {
        // #825 — server confirmed claim release. Stop retransmitting.
        GyroStopAckPayload ack;
        memcpy(&ack, payload, sizeof(ack));
        if (s_pendingStopNonce != 0 && ack.nonce == s_pendingStopNonce) {
            s_stopAcked        = true;
            s_pendingStopNonce = 0;
            s_pendingStopTries = 0;
            if (Serial)
                Serial.printf("[GyroUDP] STOP_ACK nonce=%u\n", ack.nonce);
        }

    } else if (cmd == CMD_OTA_UPDATE && plen > 5) {
        uint8_t  newMaj = payload[0];
        uint8_t  newMin = payload[1];
        uint8_t  newPat = payload[2];
        uint16_t urlLen = (uint16_t)(payload[3] | (payload[4] << 8));
        if (urlLen > 0 && urlLen < (uint16_t)(plen - 5)) {
            char otaUrl[256];
            uint16_t copyLen = urlLen < 255u ? urlLen : 255u;
            memcpy(otaUrl, &payload[5], copyLen);
            otaUrl[copyLen] = '\0';
            char otaSha[65] = {0};
            uint16_t shaOff = (uint16_t)(5 + urlLen);
            if (shaOff + 64 <= (uint16_t)plen) {
                memcpy(otaSha, &payload[shaOff], 64);
                otaSha[64] = '\0';
            }
            if (Serial)
                Serial.printf("[GyroUDP] OTA v%d.%d.%d url=%s\n",
                              newMaj, newMin, newPat, otaUrl);
            bool ok = otaStartUpdate(otaUrl, otaSha, newMaj, newMin, newPat);
            if (ok) {
                delay(500);
                ESP.restart();
            }
        }
    }
    // All other commands: silently ignored — gyro board is not an LED child
}

bool    gyroUdpStreaming()  { return s_streaming; }
uint8_t gyroUdpTargetFps() { return s_targetFps; }

bool gyroUdpClaimDeniedConsume() {
    // #772 — one-shot read: returns true exactly once after the server
    // sent CMD_GYRO_CLAIM_DENIED. Lets the UI revert to IDLE without a
    // dedicated polling channel.
    bool denied = s_claimDenied;
    s_claimDenied = false;
    return denied;
}

void gyroUdpSetStreaming(bool enabled, uint8_t fps) {
    s_streaming = enabled;
    if (fps > 0 && fps <= 50) s_targetFps = fps;
    // Reset fps counter so the first window starts clean
    s_fpsTxCount  = 0;
    s_actualFps   = 0;
    s_fpsWindowMs = (uint32_t)millis();
}

void gyroUdpSendStop() {
    // #819 — discrete CMD_GYRO_STOP packet. #825 — payload now carries a
    // 2-byte nonce so the orchestrator can ACK and we can stop retrying
    // once it lands. Nonce 0 is reserved for "no nonce" so the server
    // can distinguish legacy header-only STOPs.
    static uint16_t s_stopNonceCounter = 0;
    if (++s_stopNonceCounter == 0) s_stopNonceCounter = 1;
    s_pendingStopNonce  = s_stopNonceCounter;
    s_pendingStopLastMs = (uint32_t)millis();
    s_pendingStopTries  = 1;
    s_stopAcked         = false;
    txStopFrame(s_pendingStopNonce);
    if (Serial) Serial.printf("[GyroUDP] STOP nonce=%u\n", s_pendingStopNonce);
}

uint16_t gyroUdpSendStartWithNonce(uint16_t nonce) {
    // #825 — UI hands in a fresh nonce; we stamp the pending-start slot
    // and ship the first frame. Subsequent retries fire from
    // gyroUdpUpdate() at GYRO_RETRY_INTERVAL_MS cadence.
    if (nonce == 0) nonce = 1;
    s_pendingStartNonce  = nonce;
    s_pendingStartLastMs = (uint32_t)millis();
    s_pendingStartTries  = 1;
    s_startAcked         = false;
    s_startAckedMover    = 0;
    s_claimDenied        = false;
    txStartFrame(nonce);
    if (Serial) Serial.printf("[GyroUDP] START nonce=%u\n", nonce);
    return nonce;
}

bool gyroUdpStartAckedConsume(uint16_t* outMover) {
    bool acked = s_startAcked;
    if (acked && outMover) *outMover = s_startAckedMover;
    s_startAcked = false;
    return acked;
}

bool gyroUdpStartPending() { return s_pendingStartNonce != 0; }

void gyroUdpClearStartPending() {
    s_pendingStartNonce = 0;
    s_pendingStartTries = 0;
    s_startAcked        = false;
}

bool gyroUdpStopAckedConsume() {
    bool acked = s_stopAcked;
    s_stopAcked = false;
    return acked;
}

bool gyroUdpStopPending() { return s_pendingStopNonce != 0; }

void gyroUdpClearStopPending() {
    s_pendingStopNonce = 0;
    s_pendingStopTries = 0;
    s_stopAcked        = false;
}

void gyroUdpSetUiState(uint8_t uiState, uint16_t claimNonce) {
    s_hbUiState    = uiState;
    s_hbClaimNonce = claimNonce;
}

void gyroUdpSendColor(uint8_t r, uint8_t g, uint8_t b, uint8_t flags) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_COLOR;
    hdr.epoch   = (uint32_t)currentEpoch();

    GyroColorPayload cp;
    cp.r     = r;
    cp.g     = g;
    cp.b     = b;
    cp.flags = flags;

    uint8_t buf[sizeof(hdr) + sizeof(cp)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &cp,  sizeof(cp));

    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();

    if (Serial)
        Serial.printf("[GyroUDP] COLOR r=%d g=%d b=%d flags=0x%02X\n", r, g, b, flags);
}

void gyroUdpSendCalibrate(bool calibrating) {
    // #775 — back-compat path: read IMU at packet-build time. Used for
    // calibrate START (operator is still holding steady, sample is fine)
    // and any caller that doesn't have a latched value to pass in.
    float roll = 0.0f, pitch = 0.0f, yaw = 0.0f;
    gyroIMURead(&roll, &pitch, &yaw);
    gyroUdpSendCalibrateWith(calibrating, roll, pitch, yaw);
}

void gyroUdpSendCalibrateWith(bool calibrating, float roll, float pitch, float yaw) {
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_CALIBRATE;
    hdr.epoch   = (uint32_t)currentEpoch();

    GyroCalibratePayload cp;
    cp.calibrating = calibrating ? 1 : 0;
    cp.roll100     = (int16_t)(roll  * 100.0f);
    cp.pitch100    = (int16_t)(pitch * 100.0f);
    cp.yaw100      = (int16_t)(yaw   * 100.0f);

    uint8_t buf[sizeof(hdr) + sizeof(cp)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &cp,  sizeof(cp));

    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();

    if (Serial)
        Serial.printf("[GyroUDP] CALIBRATE: %s orient=(%.1f,%.1f,%.1f)\n",
                      calibrating ? "START" : "END", roll, pitch, yaw);
}

void gyroUdpSendStart() {
    // #772 / #825 — back-compat entry. Auto-allocates a nonce and
    // delegates to gyroUdpSendStartWithNonce() so callers that don't
    // need to track the nonce (e.g. tests) still get the rock-solid
    // retransmit + ACK-matching behaviour.
    static uint16_t s_startNonceCounter = 0;
    if (++s_startNonceCounter == 0) s_startNonceCounter = 1;
    gyroUdpSendStartWithNonce(s_startNonceCounter);
}

#endif  // BOARD_GYRO
