/*
 * TestGyro.cpp — diagnostic display + /imu HTTP for the gyro puck (#776).
 */

#include "BoardConfig.h"

#if defined(BOARD_GYRO) && defined(GYRO_TEST_BOARD)

#include <Arduino.h>
#include <WiFi.h>
#include "TestGyro.h"
#include "GyroDisplay.h"
#include "GyroTouch.h"
#include "GyroIMU.h"
#include "GyroBoard.h"
#include "version.h"

static GyroImuRaw s_lastRaw;
static bool       s_haveSample = false;
static unsigned long s_lastDrawMs = 0;
static unsigned long s_zeroFlashMs = 0;
static unsigned long s_bootMs = 0;

// Round 240x240 GC9A01 — useful drawing area is roughly the inscribed
// square. We treat (0..240, 0..240) as the full canvas; visible content
// stays within a circle of radius 120 centred at (120, 120).
static constexpr int16_t SCR_W = 240;
static constexpr int16_t SCR_CX = 120;

static void drawHeader() {
    gyroFillRect(0, 0, SCR_W, 28, GC_BLACK);
    gyroDrawText(SCR_CX - 30, 6,  "TEST MODE",   2, GC_AMBER);
    char ver[24];
    snprintf(ver, sizeof(ver), "v%d.%d.%d", APP_MAJOR, APP_MINOR, APP_PATCH);
    int16_t vw = (int16_t)(strlen(ver) * 6);
    gyroDrawText(SCR_CX - vw / 2, 24, ver, 1, GC_GREY);
    // IP under the version
    IPAddress ip = WiFi.localIP();
    char ipBuf[24];
    snprintf(ipBuf, sizeof(ipBuf), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    int16_t iw = (int16_t)(strlen(ipBuf) * 6);
    gyroDrawText(SCR_CX - iw / 2, 36, ipBuf, 1, GC_CYAN);
}

static void drawValueRow(int16_t y, const char* label, float value, uint16_t valueCol) {
    // Erase the value strip (label fits in left half, value in right).
    gyroFillRect(0, y, SCR_W, 14, GC_BLACK);
    gyroDrawText(20, y, label, 1, GC_GREY);
    char buf[24];
    if (fabsf(value) < 1000.0f) {
        snprintf(buf, sizeof(buf), "%+8.2f", value);
    } else {
        snprintf(buf, sizeof(buf), "%+8.0f", value);
    }
    gyroDrawText(110, y, buf, 1, valueCol);
}

static void drawIntRow(int16_t y, const char* label, int16_t value, uint16_t valueCol) {
    gyroFillRect(0, y, SCR_W, 14, GC_BLACK);
    gyroDrawText(20, y, label, 1, GC_GREY);
    char buf[16];
    snprintf(buf, sizeof(buf), "%+7d", value);
    gyroDrawText(110, y, buf, 1, valueCol);
}

void testGyroSetup() {
    s_bootMs = millis();
    gyroClearScreen(GC_BLACK);
    drawHeader();
    // Fixed legend — matches the Waveshare board silkscreen convention.
    gyroDrawText(SCR_CX - 60, 56, "FILTERED EULER (deg)", 1, GC_WHITE);
    gyroDrawText(SCR_CX - 54, 116, "GYRO  RAW (counts)",  1, GC_WHITE);
    gyroDrawText(SCR_CX - 54, 170, "ACCEL RAW (counts)",  1, GC_WHITE);
    // Tap-to-zero hint at the bottom.
    gyroDrawText(SCR_CX - 33, 224, "Tap to zero", 1, GC_GREY);
    if (Serial) Serial.println("[TestGyro] Diagnostic UI ready");
}

void testGyroUpdate() {
    // Always read the IMU at the loop rate so /imu has a fresh sample
    // even if the screen update is throttled.
    if (gyroIMUReadRaw(&s_lastRaw)) {
        s_haveSample = true;
    }

    // Touch — single tap zeroes the IMU. Easy to reach mid-test without
    // unplugging the device.
    int16_t tx = 0, ty = 0; uint8_t gesture = 0;
    bool touching = gyroTouchRead(&tx, &ty, &gesture);
    static bool s_wasTouching = false;
    if (touching && !s_wasTouching) {
        gyroIMUZero();
        s_zeroFlashMs = millis();
        if (Serial) Serial.println("[TestGyro] IMU zeroed");
    }
    s_wasTouching = touching;

    // Repaint at 10 Hz. The IMU read above runs every loop tick.
    unsigned long now = millis();
    if (!s_haveSample || (now - s_lastDrawMs) < 100) return;
    s_lastDrawMs = now;

    // Header was painted before connectWiFi(), so the IP line started
    // at 0.0.0.0. Redraw once we observe a real IP, so the operator can
    // read it off the screen.
    static bool s_headerHasIp = false;
    if (!s_headerHasIp && WiFi.status() == WL_CONNECTED) {
        drawHeader();
        s_headerHasIp = true;
    }

    // Filtered Euler block (top): roll, pitch, yaw with zero applied.
    drawValueRow( 70, "ROLL  X", s_lastRaw.filteredEulerDeg[0], GC_GREEN);
    drawValueRow( 84, "PITCH Y", s_lastRaw.filteredEulerDeg[1], GC_GREEN);
    drawValueRow( 98, "YAW   Z", s_lastRaw.filteredEulerDeg[2], GC_GREEN);

    // Raw gyro counts
    drawIntRow(130, "Gx", s_lastRaw.rawGx, GC_CYAN);
    drawIntRow(144, "Gy", s_lastRaw.rawGy, GC_CYAN);
    drawIntRow(158, "Gz", s_lastRaw.rawGz, GC_CYAN);

    // Raw accel counts
    drawIntRow(184, "Ax", s_lastRaw.rawAx, GC_YELLOW);
    drawIntRow(198, "Ay", s_lastRaw.rawAy, GC_YELLOW);
    drawIntRow(212, "Az", s_lastRaw.rawAz, GC_YELLOW);

    // Brief amber flash at the top after a zero, so the operator gets
    // immediate visual confirmation of the tap.
    if (s_zeroFlashMs && (now - s_zeroFlashMs) < 300) {
        gyroFillRect(0, 0, SCR_W, 28, GC_AMBER);
        gyroDrawText(SCR_CX - 18, 6, "ZEROED", 2, GC_BLACK);
    } else if (s_zeroFlashMs && (now - s_zeroFlashMs) >= 300) {
        s_zeroFlashMs = 0;
        drawHeader();
    }
}

void testGyroSendImuJson(WiFiClient& client) {
    GyroImuRaw r;
    bool ok = gyroIMUReadRaw(&r);
    if (ok) s_lastRaw = r; else r = s_lastRaw;
    char buf[640];
    int n = snprintf(buf, sizeof(buf),
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
        "{\"role\":\"gyro-test\",\"version\":\"%d.%d.%d\",\"uptimeMs\":%lu,"
        "\"raw\":{\"ax\":%d,\"ay\":%d,\"az\":%d,\"gx\":%d,\"gy\":%d,\"gz\":%d},"
        "\"accelG\":[%.4f,%.4f,%.4f],\"gyroDps\":[%.3f,%.3f,%.3f],"
        "\"filteredDeg\":{\"roll\":%.3f,\"pitch\":%.3f,\"yaw\":%.3f},"
        "\"absoluteDeg\":{\"roll\":%.3f,\"pitch\":%.3f,\"yaw\":%.3f},"
        "\"dtSec\":%.5f,\"haveSample\":%s,\"readOk\":%s}",
        APP_MAJOR, APP_MINOR, APP_PATCH,
        (unsigned long)(millis() - s_bootMs),
        r.rawAx, r.rawAy, r.rawAz, r.rawGx, r.rawGy, r.rawGz,
        r.accelG[0], r.accelG[1], r.accelG[2],
        r.gyroDps[0], r.gyroDps[1], r.gyroDps[2],
        r.filteredEulerDeg[0], r.filteredEulerDeg[1], r.filteredEulerDeg[2],
        r.absoluteEulerDeg[0], r.absoluteEulerDeg[1], r.absoluteEulerDeg[2],
        r.dtSec,
        s_haveSample ? "true" : "false",
        ok ? "true" : "false");
    if (n > 0) {
        client.write((const uint8_t*)buf, (size_t)n);
        client.flush();
    }
}

#endif  // BOARD_GYRO && GYRO_TEST_BOARD
