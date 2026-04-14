/*
 * GyroUI.cpp — Touch-screen state machine for the gyro orientation board.
 *
 * State machine:
 *   IDLE      → touch START → STREAMING
 *   STREAMING → touch STOP  → IDLE
 *   either    → touch ZERO  → IMU zero reference set (no state change)
 *   either    → touch mode dot → change mapping mode (stored in gyroUIMode)
 *
 * Full redraw only on state transitions; RPY text and ring arc update at
 * DRAW_PERIOD_MS (100 ms) to keep the display current without flicker.
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroUI.h"
#include "GyroDisplay.h"
#include "GyroTouch.h"
#include "GyroIMU.h"
#include "GyroUdp.h"
#include <Arduino.h>

// ── Layout constants ──────────────────────────────────────────────────────────

static constexpr int16_t CX  = 120;  // screen centre
static constexpr int16_t CY  = 120;

// START / STOP button
static constexpr int16_t BTN_START_X = CX;
static constexpr int16_t BTN_START_Y = 78;
static constexpr int16_t BTN_START_R = 38;

// ZERO / Recalibrate button
static constexpr int16_t BTN_ZERO_X  = CX;
static constexpr int16_t BTN_ZERO_Y  = 182;
static constexpr int16_t BTN_ZERO_R  = 28;

// Mode preset dots (4 × r=10, evenly spaced on y=208)
static const int16_t MODE_X[4]  = {68, 96, 144, 172};
static constexpr int16_t MODE_Y = 208;
static constexpr int16_t MODE_R = 10;

// WiFi indicator
static constexpr int16_t WIFI_X = 190;
static constexpr int16_t WIFI_Y = 50;
static constexpr int16_t WIFI_R = 7;

// Status ring
static constexpr int16_t RING_R = 112;
static constexpr int16_t RING_T = 5;

// RPY text origin
static constexpr int16_t RPY_X = 48;
static constexpr int16_t RPY_Y = 122;
static constexpr int16_t RPY_SP = 16;   // line spacing

// Mode colours + labels
static const uint16_t MODE_COL[4] = {GC_WHITE, GC_CYAN, GC_ORANGE, GC_MAGENTA};

// ── Module state ──────────────────────────────────────────────────────────────

enum class UIState : uint8_t { IDLE, STREAMING };

static UIState     s_state        = UIState::IDLE;
uint8_t            gyroUIMode     = 0;  // exported

static unsigned long s_lastTouchMs = 0;
static unsigned long s_lastDrawMs  = 0;

static constexpr uint16_t DEBOUNCE_MS   = 350;
static constexpr uint16_t DRAW_PERIOD_MS = 100;

// Cached values to detect display changes
static UIState  s_drawnState = (UIState)0xFF;
static uint8_t  s_drawnMode  = 0xFF;
static bool     s_drawnWifi  = false;

// Current IMU angles
static float s_roll = 0.0f, s_pitch = 0.0f, s_yaw = 0.0f;

// ── Drawing helpers ───────────────────────────────────────────────────────────

static uint16_t ringColour() {
    if (WiFi.status() != WL_CONNECTED) return GC_RED;
    return (s_state == UIState::STREAMING) ? GC_GREEN : GC_DKGREY;
}

static void drawRing() {
    gyroDrawArcSegment(CX, CY, RING_R, RING_T, 0, 359, ringColour());
}

static void drawStartBtn() {
    uint16_t fill = (s_state == UIState::STREAMING) ? (uint16_t)0x9800u   // dark red
                                                     : (uint16_t)0x0360u;  // dark green
    gyroFillCircle(BTN_START_X, BTN_START_Y, BTN_START_R, fill);
    gyroDrawCircle(BTN_START_X, BTN_START_Y, BTN_START_R, GC_WHITE);
    const char* lbl = (s_state == UIState::STREAMING) ? "STOP" : "START";
    int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
    gyroDrawText(BTN_START_X - tw / 2, BTN_START_Y - 7, lbl, 2, GC_WHITE);
}

static void drawZeroBtn() {
    gyroFillCircle(BTN_ZERO_X, BTN_ZERO_Y, BTN_ZERO_R, GC_DKGREY);
    gyroDrawCircle(BTN_ZERO_X, BTN_ZERO_Y, BTN_ZERO_R, GC_GREY);
    gyroDrawText(BTN_ZERO_X - 12, BTN_ZERO_Y - 5, "ZERO", 1, GC_WHITE);
}

static void drawModeDots() {
    for (uint8_t i = 0; i < 4; i++) {
        uint16_t fill   = (i == gyroUIMode) ? MODE_COL[i] : GC_DKGREY;
        uint16_t border = MODE_COL[i];
        gyroFillCircle(MODE_X[i], MODE_Y, MODE_R, fill);
        gyroDrawCircle(MODE_X[i], MODE_Y, MODE_R, border);
    }
}

static void drawWifi() {
    bool ok = (WiFi.status() == WL_CONNECTED);
    gyroFillCircle(WIFI_X, WIFI_Y, WIFI_R, ok ? GC_GREEN : GC_RED);
    s_drawnWifi = ok;
}

static void drawRPY() {
    // Erase with black rect then draw text
    gyroFillRect(RPY_X - 2, RPY_Y - 2, 130, RPY_SP * 3 + 4, GC_BLACK);

    char buf[20];
    int d, f;

    // Roll
    d = (int)s_roll;
    f = (int)((s_roll - (float)d) * 10.0f);
    if (f < 0) f = -f;
    snprintf(buf, sizeof(buf), "R: %4d.%1d%c", d, f, '\xb0');
    gyroDrawText(RPY_X, RPY_Y, buf, 1, GC_WHITE);

    // Pitch
    d = (int)s_pitch;
    f = (int)((s_pitch - (float)d) * 10.0f);
    if (f < 0) f = -f;
    snprintf(buf, sizeof(buf), "P: %4d.%1d%c", d, f, '\xb0');
    gyroDrawText(RPY_X, RPY_Y + RPY_SP, buf, 1, GC_WHITE);

    // Yaw
    d = (int)s_yaw;
    f = (int)((s_yaw - (float)d) * 10.0f);
    if (f < 0) f = -f;
    snprintf(buf, sizeof(buf), "Y: %4d.%1d%c", d, f, '\xb0');
    gyroDrawText(RPY_X, RPY_Y + RPY_SP * 2, buf, 1, GC_WHITE);
}

static void fullRedraw() {
    gyroClearScreen(GC_BLACK);
    drawRing();
    drawStartBtn();
    drawZeroBtn();
    drawModeDots();
    drawWifi();
    drawRPY();
    s_drawnState = s_state;
    s_drawnMode  = gyroUIMode;
    s_drawnWifi  = (WiFi.status() == WL_CONNECTED);
}

// ── Touch hit-testing ─────────────────────────────────────────────────────────

static bool hitCircle(int16_t tx, int16_t ty, int16_t cx, int16_t cy, int16_t r) {
    int16_t dx = tx - cx, dy = ty - cy;
    return (dx * dx + dy * dy) <= (r * r);
}

static void handleTouch(int16_t tx, int16_t ty) {
    // START / STOP
    if (hitCircle(tx, ty, BTN_START_X, BTN_START_Y, BTN_START_R)) {
        if (s_state == UIState::IDLE) {
            s_state = UIState::STREAMING;
            gyroUdpSetStreaming(true, 0);
        } else {
            s_state = UIState::IDLE;
            gyroUdpSetStreaming(false, 0);
        }
        fullRedraw();
        return;
    }
    // ZERO / Recalibrate
    if (hitCircle(tx, ty, BTN_ZERO_X, BTN_ZERO_Y, BTN_ZERO_R)) {
        gyroIMUZero();
        // Brief blue flash as visual confirmation
        gyroFillCircle(BTN_ZERO_X, BTN_ZERO_Y, BTN_ZERO_R, GC_BLUE);
        gyroDrawText(BTN_ZERO_X - 12, BTN_ZERO_Y - 5, "ZERO", 1, GC_WHITE);
        delay(180);
        drawZeroBtn();
        return;
    }
    // Mode preset dots
    for (uint8_t i = 0; i < 4; i++) {
        if (hitCircle(tx, ty, MODE_X[i], MODE_Y, MODE_R)) {
            gyroUIMode  = i;
            s_drawnMode = 0xFF;  // force redraw of dots on next pass
            drawModeDots();
            return;
        }
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

void gyroUIInit() {
    s_state       = UIState::IDLE;
    gyroUIMode    = 0;
    s_drawnState  = (UIState)0xFF;
    s_drawnMode   = 0xFF;
    s_drawnWifi   = false;
    s_lastTouchMs = 0;
    s_lastDrawMs  = 0;
    gyroSetBacklight(true);
    fullRedraw();
    if (Serial) Serial.println(F("[GyroUI] Ready"));
}

void gyroUIUpdate() {
    unsigned long now = millis();

    // ── Touch ────────────────────────────────────────────────────────────────
    if (now - s_lastTouchMs >= DEBOUNCE_MS) {
        int16_t tx = 0, ty = 0;
        uint8_t gesture = 0;
        if (gyroTouchRead(&tx, &ty, &gesture)) {
            s_lastTouchMs = now;
            handleTouch(tx, ty);
        }
    }

    // ── Periodic display update ───────────────────────────────────────────────
    if (now - s_lastDrawMs < DRAW_PERIOD_MS) return;
    s_lastDrawMs = now;

    // Read IMU every draw period (independent of streaming)
    gyroIMURead(&s_roll, &s_pitch, &s_yaw);

    // Full redraw if state or mode changed
    if (s_state != s_drawnState || gyroUIMode != s_drawnMode) {
        fullRedraw();
        return;
    }

    // Incremental updates
    bool wifiNow = (WiFi.status() == WL_CONNECTED);
    if (wifiNow != s_drawnWifi) {
        drawWifi();
        drawRing();
    }

    drawRPY();
}

#endif  // BOARD_GYRO
