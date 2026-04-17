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

void gyroUdpUpdate() {
    if (!s_streaming) return;

    uint32_t intervalMs = (s_targetFps > 0) ? (1000u / s_targetFps) : 50u;  // 20 Hz default
    static uint32_t s_lastSendMs = 0;
    uint32_t now = (uint32_t)millis();
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
bool    gyroUdpHasLock()   { return s_parentIP != IPAddress(255, 255, 255, 255); }
uint8_t gyroUdpTargetFps() { return s_targetFps; }

void gyroUdpSetStreaming(bool enabled, uint8_t fps) {
    s_streaming = enabled;
    if (fps > 0 && fps <= 50) s_targetFps = fps;
    // Reset fps counter so the first window starts clean
    s_fpsTxCount  = 0;
    s_actualFps   = 0;
    s_fpsWindowMs = (uint32_t)millis();
}

void gyroUdpSendStop() {
    // Send one final orient with flags bit 3 = stop signal → server releases claim
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_GYRO_ORIENT;
    hdr.epoch   = (uint32_t)currentEpoch();

    GyroOrientPayload op;
    memset(&op, 0, sizeof(op));
    op.flags = 0x08u;  // bit 3 = stop

    uint8_t buf[sizeof(hdr) + sizeof(op)];
    memcpy(buf,               &hdr, sizeof(hdr));
    memcpy(buf + sizeof(hdr), &op,  sizeof(op));

    cmdUDP.beginPacket(s_parentIP, UDP_PORT);
    cmdUDP.write(buf, sizeof(buf));
    cmdUDP.endPacket();

    if (Serial) Serial.println(F("[GyroUDP] Sent STOP signal to parent"));
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
    float roll = 0.0f, pitch = 0.0f, yaw = 0.0f;
    gyroIMURead(&roll, &pitch, &yaw);

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

#endif  // BOARD_GYRO
