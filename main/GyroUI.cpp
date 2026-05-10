/*
 * GyroUI.cpp — Touch-screen state machine for the gyro orientation board.
 *
 * State machine:
 *   LOGO     → WiFi connect (or 5s timeout) → IDLE
 *   IDLE     → tap START                     → ACTIVE (page 0)
 *   ACTIVE   → tap STOP (page 2)            → IDLE
 *   ACTIVE   → swipe left/right             → page 0/1/2/3/4
 *
 * ACTIVE pages:
 *   0 — Calibrate (hold-to-calibrate → server captures reference)
 *   1 — Colour / Flash (rainbow ring + flash button)
 *   2 — Status (park / power-save, 2 Hz update)
 *   3 — Stop (hold-to-stop → returns to IDLE)
 *   4 — Settings (battery, WiFi, hold-to-sleep → deep sleep)
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroUI.h"
#include "GyroBoard.h"
#include "GyroDisplay.h"
#include "GyroTouch.h"
#include "GyroIMU.h"
#include "GyroUdp.h"
#include "GyroLogo.h"
#include "Protocol.h"   // GYRO_UI_* state constants (#825)
#include <Arduino.h>
#include <WiFi.h>            // #813 — WiFi.status() drives Start-button gate
#include <esp_sleep.h>

// ── Forward declaration ──────────────────────────────────────────────────────
void gyroUdpSendColor(uint8_t r, uint8_t g, uint8_t b, uint8_t flags);

// ── Layout constants ─────────────────────────────────────────────────────────

static constexpr int16_t CX = 120;
static constexpr int16_t CY = 120;

// START / STOP button (IDLE and page 2)
static constexpr int16_t BTN_MAIN_R = 50;

// Calibrate button (page 0)
static constexpr int16_t BTN_CAL_Y = 110;
static constexpr int16_t BTN_CAL_R = 45;

// WiFi indicator
static constexpr int16_t WIFI_X = 200;
static constexpr int16_t WIFI_Y = 38;
static constexpr int16_t WIFI_R = 5;

// Page indicator dots
static constexpr int16_t PAGE_DOT_Y = 215;
static constexpr int16_t PAGE_DOT_R = 4;
static constexpr int16_t PAGE_DOT_SP = 20;   // spacing between dots

// Streaming indicator
static constexpr int16_t LIVE_X = 30;
static constexpr int16_t LIVE_Y = 38;
static constexpr int16_t LIVE_R = 5;

// Colour page layout — continuous rainbow ring + central flash button
static constexpr int16_t COL_RING_OUTER = 108;  // outer radius of colour wheel
static constexpr int16_t COL_RING_INNER = 88;   // inner radius (for drawing)
static constexpr int16_t COL_RING_T     = 20;   // thickness (outer - inner)
static constexpr int16_t COL_HIT_INNER  = 50;   // expanded inner hit area (accept touches closer to center)
static constexpr int16_t COL_FLASH_R    = 28;   // smaller central flash button

// Selected hue angle (-1 = none)
static int16_t s_selHue = -1;

// ── Hue → RGB conversion (full saturation, full value) ──────────────────────
static void hueToRGB(int16_t hue, uint8_t* r, uint8_t* g, uint8_t* b) {
    // hue 0-359, outputs 0-255 RGB
    hue = hue % 360;
    if (hue < 0) hue += 360;
    int16_t sector = hue / 60;
    int16_t frac   = ((hue % 60) * 255) / 60;
    switch (sector) {
        case 0: *r = 255; *g = (uint8_t)frac;       *b = 0;   break;
        case 1: *r = 255 - (uint8_t)frac; *g = 255; *b = 0;   break;
        case 2: *r = 0;   *g = 255; *b = (uint8_t)frac;       break;
        case 3: *r = 0;   *g = 255 - (uint8_t)frac; *b = 255; break;
        case 4: *r = (uint8_t)frac;       *g = 0;   *b = 255; break;
        default:*r = 255; *g = 0;   *b = 255 - (uint8_t)frac; break;
    }
}

// ── State ────────────────────────────────────────────────────────────────────

// #825 — WAITING_ACK is a transient state between IDLE and ACTIVE while
// the press-Start handshake is in flight. UI shows a "Connecting…" splash;
// only a matching CMD_GYRO_CLAIM_ACK advances to ACTIVE. CLAIM_DENIED
// reverts to IDLE with a brief "BUSY" indication; an overall timeout
// (~1500 ms — comfortably longer than the 750 ms retry budget) reverts
// with a "NO RESPONSE" indication.
enum class UIState : uint8_t { LOGO, IDLE, WAITING_ACK, ACTIVE, WIZARD };

static constexpr uint16_t START_ACK_TIMEOUT_MS = 1500;

static UIState  s_state     = UIState::LOGO;
static uint8_t  s_page      = 0;      // 0-3 when ACTIVE
uint8_t         gyroUIMode  = 0;      // exported — settable from SPA

static unsigned long s_logoStartMs = 0;
static unsigned long s_lastDrawMs  = 0;
static unsigned long s_lastEventMs = 0;  // debounce between processed events

static constexpr uint16_t EVENT_DEBOUNCE_MS = 400;
static constexpr uint16_t DRAW_PERIOD_MS    = 100;  // 10 Hz default
static constexpr uint16_t DRAW_PERIOD_PARK  = 500;  // 2 Hz on page 3

// Touch state
static uint8_t s_prevGesture    = TOUCH_GEST_NONE;
static bool    s_wasTouching    = false;
static int16_t s_lastTouchX     = CX;
static int16_t s_lastTouchY     = CY;
static unsigned long s_holdStartMs  = 0;
static unsigned long s_gestCoolMs   = 0;    // cooldown after processing a gesture
static constexpr uint16_t HOLD_MS      = 400;
static constexpr uint16_t GEST_COOL_MS = 600;  // ignore gestures for 600ms after one fires
static unsigned long s_flashLastMs  = 0;
static unsigned long s_colorSendMs  = 0;

// Hold states (active while finger down on button for > HOLD_MS)
static bool s_startHeld = false;
static bool s_calibHeld = false;
static bool s_flashHeld = false;
static bool s_stopHeld  = false;
// #867 — second hold flag for the new OFF button on the CONFIRM page
// (page 3). OFF and STOP share the page; tracked separately so the
// hold-fill animation only paints the button the operator's finger is
// actually on.
static bool s_offHeld   = false;

// #774 — calibrate-end debounce. When the finger lifts off the calibrate
// button we don't fire the END packet immediately; instead we wait
// CALIB_RELEASE_DEBOUNCE_MS for a re-press (capacitive noise / brief
// finger drift). A re-press inside the window cancels the pending end
// and continues the existing hold; window expiry commits the end.
//
// #775 — `s_calibLastRoll/Pitch/Yaw` latches the IMU sample taken WHILE
// the finger was still on the screen, so the END packet ships the
// orientation captured during the hold rather than whatever post-lift
// jiggle the IMU sees a few ms after the finger leaves.
static unsigned long s_calibReleasePendingMs = 0;
static constexpr uint16_t CALIB_RELEASE_DEBOUNCE_MS = 100;
static float s_calibLastRoll  = 0.0f;
static float s_calibLastPitch = 0.0f;
static float s_calibLastYaw   = 0.0f;

// #565 — when true, the IDLE state has been swiped into Settings. This
// keeps the app in UIState::IDLE (so the server isn't claimed) but
// paints the Settings page so operators can reach battery info and
// Sleep before starting. Swipe back to clear.
static bool s_idleSettings = false;

// #869 — empirical aim-axis wizard state. Reached from the IDLE-
// Settings page; captures three Euler triples (neutral / pitch_fwd /
// yaw_left) and ships them in one CMD_GYRO_AIM_WIZARD packet so the
// orchestrator derives forward_local / up_local the same way the
// Android wizard (#826) does. Strictly additive — no other state
// machine touches s_wizStep, and entry/exit only happens in the
// WIZARD-state branches. Android claim path is untouched.
static uint8_t s_wizStep = 0;        // 0=neutral, 1=pitch, 2=yaw, 3=sent
static float   s_wizEulers[3][3] = {0};  // [step][roll, pitch, yaw]
static unsigned long s_wizSentMs = 0;

// #825 — press-Start handshake bookkeeping. s_startNonce is the nonce we
// advertised on the wire; the matching CLAIM_ACK from the server carries
// it back. s_startSentMs starts the overall timeout window. s_claimNonce
// is the nonce of the *currently-held* claim (post-ACK), surfaced in
// CMD_GYRO_HEARTBEAT_REP so the orchestrator can reconcile.
static uint16_t s_startNonce      = 0;
static uint32_t s_startSentMs     = 0;
static uint16_t s_claimNonce      = 0;
static uint16_t s_nextNonce       = 1;   // monotonic; never 0 (reserved)

// Selected colour preset index (page 1)

// ── Drawing helpers ──────────────────────────────────────────────────────────

static bool wifiOk() { return WiFi.status() == WL_CONNECTED; }

static void drawPageDots() {
    int16_t startX = CX - (int16_t)(4 * PAGE_DOT_SP) / 2;
    for (uint8_t i = 0; i < 5; i++) {
        int16_t dx = startX + i * PAGE_DOT_SP;
        uint16_t col = (i == s_page) ? GC_WHITE : GC_DKGREY;
        gyroFillCircle(dx, PAGE_DOT_Y, PAGE_DOT_R, col);
    }
}

// #476 — heartbeat-driven state:
//   never heard   → LIVE (first-session or firmware without the feature)
//   < 5 s stale   → LIVE (green)
//   < 20 s stale  → RECON (amber, "Reconnecting...")
//   > 20 s stale  → LOST (red, drops back to IDLE state via update loop)
static void drawLiveIndicator() {
    uint32_t hb = gyroGetLastHeartbeatMs();
    uint32_t now = millis();
    uint32_t age = (hb == 0) ? 0 : (now - hb);
    if (hb != 0 && age > 5000u && age <= 20000u) {
        gyroFillCircle(LIVE_X, LIVE_Y, LIVE_R, GC_YELLOW);
        gyroDrawText(LIVE_X + 10, LIVE_Y - 4, "RECON", 1, GC_YELLOW);
    } else if (hb != 0 && age > 20000u) {
        gyroFillCircle(LIVE_X, LIVE_Y, LIVE_R, GC_RED);
        gyroDrawText(LIVE_X + 10, LIVE_Y - 4, "LOST",  1, GC_RED);
    } else {
        gyroFillCircle(LIVE_X, LIVE_Y, LIVE_R, GC_GREEN);
        gyroDrawText(LIVE_X + 10, LIVE_Y - 4, "LIVE",  1, GC_GREEN);
    }
}

static void drawFpsIndicator() {
    char buf[8];
    uint8_t fps = gyroUdpTargetFps();
    snprintf(buf, sizeof(buf), "%dHz", fps);
    // Right-aligned near top-right
    int16_t tw = (int16_t)(strlen(buf) * 6);
    gyroDrawText(230 - tw, LIVE_Y - 4, buf, 1, GC_GREY);
}

// ── LOGO screen ──────────────────────────────────────────────────────────────

static void drawLogo() {
    gyroClearScreen(GC_BLACK);

    // Blit logo image centred (80×92, transparent black background)
    gyroDrawImage(CX - LOGO_W / 2, 24, LOGO_W, LOGO_H, s_logoImg);

    gyroDrawText(36, 150, "Connecting to WiFi..", 1, GC_GREY);

    // Progress bar
    gyroFillRect(40, 172, 160, 8, GC_DKGREY);
}

static void updateLogoProgress(float progress) {
    int16_t w = (int16_t)(progress * 156.0f);
    if (w < 1) w = 1;
    if (w > 156) w = 156;
    gyroFillRect(42, 174, w, 4, GC_CYAN);
}

// ── IDLE screen ──────────────────────────────────────────────────────────────

static void drawIdle() {
    gyroClearScreen(GC_BLACK);
    // #813 green-field — Start button no longer waits for an orchestrator
    // CTRL(1) "lock" packet. WiFi connectivity is the operator-visible
    // gate: if the gyro has a network it can broadcast `CMD_GYRO_START`
    // on press; the orchestrator binds UDP 4210 and replies (CLAIM_ACK
    // is implicit — claim transitions to streaming silently; CLAIM_DENIED
    // arrives only on refusal, handled by gyroUdpClaimDeniedConsume()).
    bool ready = (WiFi.status() == WL_CONNECTED);

    if (s_startHeld && ready) {
        // Holding — bright green, visual feedback
        gyroFillCircle(CX, CY, BTN_MAIN_R, GC_GREEN);
        gyroDrawCircle(CX, CY, BTN_MAIN_R, GC_WHITE);
        const char* lbl = "HOLD";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);
    } else {
        // START button — yellow (no WiFi) or green (ready)
        uint16_t btnFill = ready ? (uint16_t)0x0360u : (uint16_t)0x4200u;
        uint16_t btnRing = ready ? GC_GREEN : GC_YELLOW;
        gyroFillCircle(CX, CY, BTN_MAIN_R, btnFill);
        gyroDrawCircle(CX, CY, BTN_MAIN_R, btnRing);
        const char* lbl = "START";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);
    }

    if (!ready) {
        const char* hint = "Connecting WiFi...";
        int16_t hw = (int16_t)(strlen(hint) * 6);
        gyroDrawText(CX - hw / 2, CY + 65, hint, 1, GC_GREY);
    } else if (!s_startHeld) {
        const char* hint = "Hold to start";
        int16_t hw = (int16_t)(strlen(hint) * 6);
        gyroDrawText(CX - hw / 2, CY + 65, hint, 1, GC_GREY);
    }
}

// ── ACTIVE page 0 — Calibrate ───────────────────────────────────────────────

static void drawCalibratePage() {
    // #773 — full-screen yellow flood while the calibrate button is held.
    // Mirrors Android's #FBBF24 halo so the operator gets an unmistakable
    // "device locked, hold steady" cue. Skip page-dot + button rendering
    // entirely — the flood owns the screen until release.
    if (s_calibHeld) {
        gyroClearScreen(GC_AMBER);
        gyroDrawText(CX - 36, CY - 8,  "HOLD STEADY",        2, GC_BLACK);
        gyroDrawText(CX - 60, CY + 16, "Release to set zero", 1, GC_BLACK);
        return;
    }

    gyroClearScreen(GC_BLACK);
    bool live = gyroUdpStreaming();

    if (live) {
        // Live — green calibrate button
        gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, (uint16_t)0x0360u);
        gyroDrawCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_GREEN);
        gyroDrawText(CX - 27, BTN_CAL_Y - 3, "CALIBRATE", 1, GC_WHITE);
        // "Hold to pause & move" = 20 chars × 6px = 120px → center
        gyroDrawText(CX - 60, 168, "Hold to pause & move", 1, GC_GREY);
    } else {
        // Not live — this IS the start button
        gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, (uint16_t)0x0360u);
        gyroDrawCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_GREEN);
        gyroDrawText(CX - 30, BTN_CAL_Y - 7, "START", 2, GC_WHITE);
    }

    drawPageDots();
}

// ── ACTIVE page 1 — Colour / Flash ──────────────────────────────────────────

static void drawColourWheel() {
    // 13 segments: 12 hue (28° each, 0°-335°) + 1 white (336°-359°, top-right)
    // atan2 convention: 0°=right(3 o'clock), 90°=down(6), 180°=left(9), 270°=up(12)
    for (int16_t i = 0; i < 12; i++) {
        int16_t startDeg = i * 28;
        int16_t endDeg = startDeg + 27;
        int16_t hue = i * 30;
        uint8_t r, g, b;
        hueToRGB(hue, &r, &g, &b);
        uint16_t col = gc9a01_rgb565(r, g, b);
        gyroDrawArcSegment(CX, CY, COL_RING_OUTER, COL_RING_T,
                           startDeg, endDeg, col);
    }
    // White segment at 336°-359° (just before red at 0°)
    gyroDrawArcSegment(CX, CY, COL_RING_OUTER, COL_RING_T, 336, 359, GC_WHITE);
}

static constexpr int16_t WHITE_SEG_START = 336;

// Draw a lightning bolt icon (pixel art, ~10x16)
static void drawBolt(int16_t cx, int16_t cy, uint16_t col) {
    // Simple zigzag bolt shape centred at cx, cy
    //   ##
    //  ##
    // ####
    //   ##
    //  ##
    // ##
    gyroFillRect(cx + 1, cy - 7, 4, 2, col);
    gyroFillRect(cx - 1, cy - 5, 4, 2, col);
    gyroFillRect(cx - 4, cy - 3, 8, 2, col);  // wide bar
    gyroFillRect(cx + 1, cy - 1, 4, 2, col);
    gyroFillRect(cx - 1, cy + 1, 4, 2, col);
    gyroFillRect(cx - 3, cy + 3, 4, 2, col);
}

// Fill the ring between flash button and colour wheel with selected colour
static void drawColourFill() {
    if (s_selHue < 0) return;
    uint16_t col;
    if (s_selHue >= WHITE_SEG_START) {
        col = GC_WHITE;
    } else {
        uint8_t r, g, b;
        int16_t hue = (int16_t)((float)s_selHue * 360.0f / (float)WHITE_SEG_START);
        hueToRGB(hue, &r, &g, &b);
        col = gc9a01_rgb565(r, g, b);
    }
    // Fill ring from just outside flash button to just inside colour wheel
    gyroDrawArcSegment(CX, CY, COL_RING_INNER - 2, COL_RING_INNER - COL_FLASH_R - 4,
                       0, 359, col);
}

static void drawFlashButton() {
    uint16_t fill = s_flashHeld ? gc9a01_rgb565(40, 60, 70)
                                : gc9a01_rgb565(25, 25, 25);
    gyroFillCircle(CX, CY, COL_FLASH_R, fill);
    gyroDrawCircle(CX, CY, COL_FLASH_R, gc9a01_rgb565(60, 60, 60));
    if (s_flashHeld) gyroDrawCircle(CX, CY, COL_FLASH_R + 1, GC_CYAN);
    drawBolt(CX, CY, GC_CYAN);
}

static void drawColourPage() {
    gyroClearScreen(GC_BLACK);
    drawColourWheel();
    drawColourFill();
    drawFlashButton();
    drawPageDots();
}

// ── ACTIVE page 3 — Confirm-terminate (OFF / STOP) ──────────────────────────
// #867 — two-button screen reached from the ACTIVE page swipe. OFF
// blackouts the claimed head AND releases the claim; STOP releases
// the claim leaving the head at its last frame. Both buttons are
// hold-to-action, mirroring the original single-STOP UX so the
// operator can't terminate by accidental brush.
static constexpr int16_t CONFIRM_BTN_R = BTN_MAIN_R - 28;  // smaller so two fit
static constexpr int16_t CONFIRM_OFF_CY  = CY - (CONFIRM_BTN_R + 6);
static constexpr int16_t CONFIRM_STOP_CY = CY + (CONFIRM_BTN_R + 6);

static void drawStopPage() {
    gyroClearScreen(GC_BLACK);
    drawLiveIndicator();

    // OFF button (top half) — yellow/amber when idle, red when held.
    // OFF semantically = "lights out + release"; visually loudest
    // because it's the operator-visible blackout path.
    {
        uint16_t fill = s_offHeld ? GC_RED : (uint16_t)0xFD00u;  // amber
        gyroFillCircle(CX, CONFIRM_OFF_CY, CONFIRM_BTN_R, fill);
        gyroDrawCircle(CX, CONFIRM_OFF_CY, CONFIRM_BTN_R, GC_RED);
        const char* lbl = s_offHeld ? "HOLD" : "OFF";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CONFIRM_OFF_CY - 7, lbl, 2, GC_BLACK);
    }

    // STOP button (bottom half) — current colour scheme. Releases
    // claim only; head holds last frame.
    {
        uint16_t fill = s_stopHeld ? GC_RED : (uint16_t)0x9800u;
        gyroFillCircle(CX, CONFIRM_STOP_CY, CONFIRM_BTN_R, fill);
        gyroDrawCircle(CX, CONFIRM_STOP_CY, CONFIRM_BTN_R, GC_RED);
        const char* lbl = s_stopHeld ? "HOLD" : "STOP";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CONFIRM_STOP_CY - 7, lbl, 2, GC_WHITE);
    }

    if (!s_offHeld && !s_stopHeld) {
        gyroDrawText(20, 220, "Hold OFF to blackout, STOP to release", 1, GC_GREY);
    }

    drawPageDots();
}

// ── ACTIVE page 3 — Status (park) ───────────────────────────────────────────

static void drawStatusPage() {
    gyroClearScreen(GC_BLACK);

    // Single status dot
    bool ok = gyroUdpStreaming() && wifiOk();
    gyroFillCircle(CX, CY, 12, ok ? GC_GREEN : GC_RED);

    drawPageDots();
}

// ── ACTIVE page 4 — Settings ────────────────────────────────────────────────

// #566 follow-up — the old `analogRead() / 4095 * 3.3` was wildly off on
// the ESP32-S3 because it ignored the per-chip eFuse ADC calibration and
// the attenuation default. `analogReadMilliVolts()` from the Arduino
// ESP32 core returns calibrated mV (applies the TwoPoint / VRef
// calibration written to eFuse at Espressif's factory test). 16-sample
// average smooths the ~±20 mV ADC noise.
// #813 follow-up — exposed as gyroReadBatteryVoltage() (non-static) so
// the CMD_GYRO_BATT sender in GyroUdp.cpp can sample without a UDP
// round-trip back through the UI layer. Same body as the original
// `static readBatteryVoltage`; kept the static alias below for the
// existing file-local callers.
float gyroReadBatteryVoltage() {
    if (GYRO_BAT_PIN == 0) return -1.0f;  // no battery pin
    // Explicitly pick 11 dB attenuation — max divider-output of a 4.2 V
    // LiPo through a 2:1 divider is 2.1 V, comfortably inside 11 dB's
    // ~3.1 V linear range.
    static bool s_adcInited = false;
    if (!s_adcInited) {
        analogSetPinAttenuation(GYRO_BAT_PIN, ADC_11db);
        s_adcInited = true;
    }
    uint32_t mvSum = 0;
    for (int i = 0; i < 16; i++) mvSum += analogReadMilliVolts(GYRO_BAT_PIN);
    float mv = (float)mvSum / 16.0f;
    return (mv / 1000.0f) * GYRO_BAT_DIVIDER;
}
// File-local alias kept so existing callers in this file don't need to
// be renamed.
static inline float readBatteryVoltage() { return gyroReadBatteryVoltage(); }

static int batteryPercent(float voltage) {
    // LiPo discharge curve (calibrated 2026-05-05). 4.20 V is the
    // charging plateau the cell holds only while USB is supplying
    // current; a freshly-charged cell unplugged settles at ~4.10 V
    // within minutes. Mapping 4.10 V → 100% (instead of the
    // physically-unreachable 4.20 V) makes "just charged" read 100%
    // on the LCD instead of 92%, matching every consumer LiPo gauge.
    // 3.30 V remains the empty cutoff (cell protection circuit
    // disconnects at ~3.0 V; 3.30 V leaves a small headroom).
    if (voltage < 0) return -1;  // no battery
    if (voltage >= 4.10f) return 100;
    if (voltage <= 3.30f) return 0;
    return (int)((voltage - 3.30f) / 0.80f * 100.0f);
}

// #566 follow-up — charging detection without a dedicated CHRG GPIO.
// The Waveshare 1.28 board doesn't wire the TP4056 CHRG line out, so we
// infer charge state from the battery voltage curve:
//
//   • A 4.15 V+ plateau holds only while USB is supplying current (a
//     disconnected LiPo under any load drops into the 3.9–4.1 V range
//     within minutes).
//   • A monotonic 20+ mV rise across a 20 s window is unambiguously a
//     charge cycle — discharge slope under the ~80 mA display load is
//     always downward, never upward.
//
// s_batHist is a 10-slot rolling ring sampled at 0.5 Hz.
static float   s_batHist[10]      = {0};
static uint8_t s_batHistIdx       = 0;
static uint32_t s_batLastSampleMs = 0;

static void batterySample() {
    uint32_t now = millis();
    if (now - s_batLastSampleMs < 2000) return;  // 0.5 Hz
    s_batLastSampleMs = now;
    float v = readBatteryVoltage();
    if (v < 0) return;  // no battery
    s_batHist[s_batHistIdx] = v;
    s_batHistIdx = (uint8_t)((s_batHistIdx + 1) % 10);
}

static bool batteryCharging() {
    float curr = s_batHist[(s_batHistIdx + 9) % 10];
    if (curr <= 0) return false;
    if (curr >= 4.15f) return true;           // USB plateau
    float oldest = s_batHist[s_batHistIdx];   // next slot to overwrite = oldest
    if (oldest <= 0) return false;            // buffer not full yet
    return (curr - oldest) > 0.02f;           // >20 mV rise over 20 s
}

// #813 follow-up — non-static thunks for CMD_GYRO_BATT in GyroUdp.cpp.
int  gyroReadBatteryPercent()  { return batteryPercent(readBatteryVoltage()); }
bool gyroReadBatteryCharging() { return batteryCharging(); }

static bool s_sleepHeld = false;

static void enterDeepSleep() {
    // Stop streaming
    gyroUdpSetStreaming(false, 0);

    // Show sleep message
    gyroClearScreen(GC_BLACK);
    gyroDrawText(52, 100, "Sleeping...", 1, GC_GREY);
    gyroDrawText(28, 120, "Touch screen to wake", 1, GC_DKGREY);
    delay(1000);

    // Turn off backlight
    digitalWrite(GYRO_LCD_BL, LOW);

    // Configure wake on touch INT pin (CST816S asserts INT on any touch)
    esp_sleep_enable_ext0_wakeup((gpio_num_t)GYRO_TP_INT, 0);  // wake on LOW

    // Enter deep sleep — device restarts on wake
    esp_deep_sleep_start();
}

// #565 — sleep-button arc geometry. Instead of a cramped circle at y=175
// the sleep button is now a circular segment hugging the bottom of the
// 240 px round display. `SLEEP_ARC_YTOP` is the top row of the segment
// — rows ≥ this and inside the display circle belong to the button.
// Lifting the top to 180 yields a rise of ~60 px (≈ 1/4 of the screen),
// which matches the issue spec and leaves ~150 px of content area above.
static constexpr int16_t SLEEP_ARC_YTOP = 180;
static constexpr int16_t SLEEP_ARC_RADIUS = 118;  // just inside the 120 px screen edge

// Fill the bottom segment of the display circle from yTop downward.
// At row y, the horizontal span is x = CX ± sqrt(R² − (y−CY)²).
static void fillSleepArc(uint16_t colour) {
    for (int16_t y = SLEEP_ARC_YTOP; y <= CY + SLEEP_ARC_RADIUS && y < GYRO_LCD_H; y++) {
        int32_t dy = y - CY;
        int32_t rSq = (int32_t)SLEEP_ARC_RADIUS * SLEEP_ARC_RADIUS;
        int32_t dySq = dy * dy;
        if (dySq > rSq) continue;
        int16_t span = (int16_t)sqrtf((float)(rSq - dySq));
        gyroFillRect(CX - span, y, 2 * span, 1, colour);
    }
}

// Hit test — a tap is on the sleep arc when y ≥ yTop AND inside the
// screen disc. Matches exactly the filled region so the button never
// "lies" about its touch area.
static bool hitSleepArc(int16_t tx, int16_t ty) {
    if (ty < SLEEP_ARC_YTOP) return false;
    int32_t dx = tx - CX, dy = ty - CY;
    return (dx * dx + dy * dy) <= (int32_t)SLEEP_ARC_RADIUS * SLEEP_ARC_RADIUS;
}

// Repaint just the battery readout block (rows ~60-112). Called from
// drawSettingsPage on entry and from the 2 s tick so the charging
// indicator updates live without clearing the whole screen (which
// caused visible flicker). Wipes the rect first so stale pixels from
// a longer previous string don't survive.
static void drawBatteryInfo() {
    gyroFillRect(0, 60, GYRO_LCD_W, 52, GC_BLACK);
    float vbat = readBatteryVoltage();
    int pct = batteryPercent(vbat);
    if (pct < 0) {
        gyroDrawText(48, 80, "No battery", 1, GC_GREY);
        return;
    }
    char buf[32];
    bool charging = batteryCharging();
    // "Battery: 87% +" when charging (lightning-bolt glyph isn't in
    // font5x7, so "+" is the operator-visible charge marker).
    snprintf(buf, sizeof(buf), charging ? "Battery: %d%% +" : "Battery: %d%%", pct);
    uint16_t col = charging
                   ? GC_CYAN
                   : (pct > 20 ? GC_GREEN : (pct > 5 ? GC_ORANGE : GC_RED));
    gyroDrawText(44, 70, buf, 1, col);
    snprintf(buf, sizeof(buf), "%.2fV", vbat);
    gyroDrawText(80, 85, buf, 1, GC_GREY);
    // Battery bar — tint cyan while charging so the state is legible
    // even without reading the label.
    gyroFillRect(50, 100, 140, 10, GC_DKGREY);
    int barW = pct * 136 / 100;
    if (barW > 0) gyroFillRect(52, 102, barW, 6, col);
}

// #869 — forward-decl so drawSettingsPage can paint the wizard pill
// before the full wizard helper block is defined further down.
static void drawWizPill();

static void drawSettingsPage() {
    gyroClearScreen(GC_BLACK);
    gyroDrawText(CX - 27, 32, "SETTINGS", 1, GC_CYAN);
    // #869 — wizard entry pill, only on the IDLE-Settings overlay.
    // The ACTIVE-page-4 path also draws Settings but the operator
    // shouldn't enter the wizard mid-claim — gating on s_idleSettings
    // keeps the affordance scoped to the pre-claim setup phase.
    if (s_state == UIState::IDLE && s_idleSettings) drawWizPill();

    drawBatteryInfo();

    // WiFi info — #778: when connected, render hostname + RSSI + IP
    // beneath the status line so the operator can read the gyro's
    // DHCP-assigned address right off the LCD instead of `arp -a`-ing
    // from the workstation.
    if (wifiOk()) {
        gyroDrawText(52, 125, "WiFi: Connected", 1, GC_GREEN);
        char hostLine[40];
        const char* hn = WiFi.getHostname();
        int8_t rssi = (int8_t)WiFi.RSSI();
        snprintf(hostLine, sizeof(hostLine), "%s (%d dBm)",
                 (hn && *hn) ? hn : "?", rssi);
        gyroDrawText(52, 138, hostLine, 1, GC_GREY);
        // localIP().toString() returns a transient String; c_str() is
        // valid for the duration of this draw call.
        String ipStr = WiFi.localIP().toString();
        gyroDrawText(52, 151, ipStr.c_str(), 1, GC_WHITE);
    } else {
        gyroDrawText(52, 125, "WiFi: Disconnected", 1, GC_RED);
    }

    // Sleep arc at bottom — filled red when held for feedback, dark red
    // otherwise. Label sits inside the arc just below yTop (y=180).
    uint16_t arcFill = s_sleepHeld ? GC_RED : (uint16_t)0x6000u;  // dark red
    fillSleepArc(arcFill);
    if (s_sleepHeld) {
        gyroDrawText(CX - 12, SLEEP_ARC_YTOP + 12, "HOLD", 1, GC_WHITE);
        gyroDrawText(CX - 51, SLEEP_ARC_YTOP + 30, "Release to sleep", 1, GC_WHITE);
    } else {
        gyroDrawText(CX - 15, SLEEP_ARC_YTOP + 12, "SLEEP", 1, GC_WHITE);
        gyroDrawText(CX - 39, SLEEP_ARC_YTOP + 30, "Hold to sleep", 1, GC_GREY);
    }

    drawPageDots();
}

// ── #869 wizard-page geometry + helpers ─────────────────────────────────────
// Operator-visible affordance: small "WIZ" pill at the top of the
// IDLE-Settings page so the wizard isn't reachable mid-claim. The
// hit-rect is sized to be touch-comfortable on the 1.28" round LCD
// without overlapping the existing battery / WiFi / SLEEP zones.
static constexpr int16_t WIZ_PILL_X = CX - 24;
static constexpr int16_t WIZ_PILL_Y = 14;
static constexpr int16_t WIZ_PILL_W = 48;
static constexpr int16_t WIZ_PILL_H = 16;

static bool hitWizPill(int16_t tx, int16_t ty) {
    return (tx >= WIZ_PILL_X && tx <= WIZ_PILL_X + WIZ_PILL_W
            && ty >= WIZ_PILL_Y && ty <= WIZ_PILL_Y + WIZ_PILL_H);
}

static void drawWizPill() {
    gyroFillRect(WIZ_PILL_X, WIZ_PILL_Y, WIZ_PILL_W, WIZ_PILL_H,
                 (uint16_t)0x2104u);  // dark navy
    gyroDrawText(WIZ_PILL_X + 12, WIZ_PILL_Y + 5, "WIZ", 1, GC_CYAN);
}

static void drawWizardPage() {
    gyroClearScreen(GC_BLACK);
    gyroDrawText(CX - 33, 28, "AIM WIZARD", 1, GC_CYAN);

    // Step prompt — short enough to fit on the round LCD without
    // overflowing past the circular bezel.
    const char* prompt1 = "";
    const char* prompt2 = "";
    switch (s_wizStep) {
        case 0:
            prompt1 = "Hold gyro aimed";
            prompt2 = "at head, then tap";
            break;
        case 1:
            prompt1 = "Tip gyro FORWARD";
            prompt2 = "(toward floor), tap";
            break;
        case 2:
            prompt1 = "Yaw to STAGE-LEFT";
            prompt2 = "(audience right), tap";
            break;
        default:
            prompt1 = "Wizard saved.";
            prompt2 = "";
            break;
    }
    gyroDrawText(CX - (int16_t)(strlen(prompt1) * 3), 56, prompt1, 1, GC_WHITE);
    gyroDrawText(CX - (int16_t)(strlen(prompt2) * 3), 70, prompt2, 1, GC_GREY);

    // Capture button (or "DONE" indicator on step 3).
    if (s_wizStep < 3) {
        gyroFillCircle(CX, CY + 20, BTN_MAIN_R - 18, (uint16_t)0x0410u);  // teal
        gyroDrawCircle(CX, CY + 20, BTN_MAIN_R - 18, GC_CYAN);
        char lbl[12];
        snprintf(lbl, sizeof(lbl), "TAP %u/3", (unsigned)(s_wizStep + 1));
        int16_t tw = (int16_t)(strlen(lbl) * 6);
        gyroDrawText(CX - tw / 2, CY + 14, lbl, 1, GC_WHITE);
    } else {
        gyroFillCircle(CX, CY + 20, BTN_MAIN_R - 18, (uint16_t)0x0480u);  // green
        gyroDrawText(CX - 18, CY + 14, "SAVED", 1, GC_WHITE);
    }

    gyroDrawText(28, 220, "swipe to cancel", 1, GC_DKGREY);
}

// Capture the gyro's current orientation into the s_wizEulers slot
// for the active step. After step 2, ship the triple as one
// CMD_GYRO_AIM_WIZARD packet and bounce back to IDLE.
static void wizardCaptureCurrentStep() {
    if (s_wizStep > 2) return;
    float r = 0, p = 0, y = 0;
    gyroIMURead(&r, &p, &y);
    s_wizEulers[s_wizStep][0] = r;
    s_wizEulers[s_wizStep][1] = p;
    s_wizEulers[s_wizStep][2] = y;
    s_wizStep++;
    if (s_wizStep == 3) {
        gyroUdpSendAimWizard(s_wizEulers[0], s_wizEulers[1], s_wizEulers[2]);
        s_wizSentMs = millis();
    }
}

// ── Page dispatch ────────────────────────────────────────────────────────────

// Pages: 0=Calibrate, 1=Colour, 2=Status/park, 3=Stop, 4=Settings
static void drawCurrentPage() {
    switch (s_page) {
        case 0: drawCalibratePage(); break;
        case 1: drawColourPage(); break;
        case 2: drawStatusPage(); break;
        case 3: drawStopPage(); break;
        case 4: drawSettingsPage(); break;
    }
}

// ── Touch helpers ────────────────────────────────────────────────────────────

static bool hitCircle(int16_t tx, int16_t ty, int16_t cx, int16_t cy, int16_t r) {
    int16_t dx = tx - cx, dy = ty - cy;
    return (dx * dx + dy * dy) <= (r * r);
}

// ── Touch handlers per state ─────────────────────────────────────────────────

static void handleTouchIdle(int16_t tx, int16_t ty) {
    // START is hold-to-start, handled in update loop — taps ignored
    (void)tx; (void)ty;
}

static void handleTouchActive(int16_t tx, int16_t ty, uint8_t gesture) {
    // Swipe navigation handled in gyroUIUpdate() gesture edge detector.
    // This function handles TAP events only.
    switch (s_page) {
        case 0:
            // If not streaming, tap = START
            if (!gyroUdpStreaming() && hitCircle(tx, ty, CX, BTN_CAL_Y, BTN_CAL_R)) {
                gyroUdpSetStreaming(true, 0);
                drawCalibratePage();
            }
            // If streaming, calibrate hold is handled in update loop
            break;
        case 1:
            // Colour: continuous tracking handles ring, hold handles flash
            break;
        case 2:
            // Status (park) — taps do nothing (intentional)
            break;
        case 3:
            // Stop — handled via hold in update loop
            break;
    }
}

// ── Public API ───────────────────────────────────────────────────────────────

void gyroUIInit() {
    s_state        = UIState::LOGO;
    s_page         = 0;
    s_prevGesture  = TOUCH_GEST_NONE;
    s_wasTouching  = false;
    s_holdStartMs  = 0;
    s_gestCoolMs   = 0;
    s_selHue       = -1;
    s_logoStartMs  = millis();
    s_lastEventMs  = 0;
    s_lastDrawMs   = 0;

    // Skip logo if waking from deep sleep (touch wake) or WiFi already connected
    esp_sleep_wakeup_cause_t wakeReason = esp_sleep_get_wakeup_cause();
    if (wakeReason == ESP_SLEEP_WAKEUP_EXT0 || wifiOk()) {
        s_state = UIState::IDLE;
        drawIdle();
        if (Serial) Serial.println(F("[GyroUI] Woke from sleep — skipping logo"));
    } else {
        drawLogo();
    }
    if (Serial) Serial.println(F("[GyroUI] Ready"));
}

void gyroUIUpdate() {
    unsigned long now = millis();

    // #476 — heartbeat watchdog. If we've heard at least one heartbeat but
    // none in the last 20 s while streaming/active, drop back to IDLE: the
    // server has already auto-released the claim, so keeping the ACTIVE
    // pages on-screen would mislead the operator.
    {
        uint32_t hb = gyroGetLastHeartbeatMs();
        if (hb != 0 && (now - hb) > 20000u && s_state == UIState::ACTIVE) {
            gyroUdpSetStreaming(false, 0);
            s_state = UIState::IDLE;
            // #825 — drop the claim-nonce; HB_REPs will advertise IDLE
            // so the orchestrator releases its half of the claim too.
            s_claimNonce = 0;
            gyroUdpSetUiState(GYRO_UI_IDLE, 0);
            drawIdle();
        }
    }

    // ── LOGO state ──────────────────────────────────────────────────────────
    if (s_state == UIState::LOGO) {
        unsigned long elapsed = now - s_logoStartMs;
        float progress = (float)elapsed / 5000.0f;
        if (progress > 1.0f) progress = 1.0f;
        updateLogoProgress(progress);

        if (wifiOk() || elapsed >= 5000) {
            s_state = UIState::IDLE;
            drawIdle();
        }
        delay(50);
        return;
    }

    // ── Touch polling ─────────────────────────────────────────────────────
    // CST816S behaviour on this board:
    //   Finger on screen → f=1, g=0x00 (touching, no gesture yet)
    //   Gesture detected → f=0, g=XX  (gesture code persists many reads)
    //   Idle             → f=0, g=0x00
    // So we detect events on GESTURE EDGES, not finger up/down.

    int16_t tx = 0, ty = 0;
    uint8_t gesture = TOUCH_GEST_NONE;
    bool touching = gyroTouchRead(&tx, &ty, &gesture);

    // Gesture edge: new gesture appearing (NONE→code), with cooldown to prevent bouncing
    bool newGesture = (gesture != TOUCH_GEST_NONE && s_prevGesture == TOUCH_GEST_NONE
                       && (now - s_gestCoolMs >= GEST_COOL_MS));
    s_prevGesture = gesture;

    // Track last known touch position (from f=1 reads)
    if (touching) { s_lastTouchX = tx; s_lastTouchY = ty; }

    // Finger-down edge (for hold tracking)
    bool fingerDown = (touching && !s_wasTouching);
    if (fingerDown) s_holdStartMs = now;

    // Finger still held? (for hold-to-calibrate/flash/stop)
    bool held = (touching && s_holdStartMs > 0 && (now - s_holdStartMs >= HOLD_MS));

    // ── New gesture → process immediately ───────────────────────────────────
    if (newGesture) {
        bool isSwipe = (gesture == TOUCH_GEST_SWIPE_LEFT || gesture == TOUCH_GEST_SWIPE_RIGHT);
        bool isTap   = (gesture == TOUCH_GEST_CLICK);

        s_gestCoolMs = now;  // start cooldown

        if (s_state == UIState::ACTIVE && isSwipe) {
            if (gesture == TOUCH_GEST_SWIPE_LEFT && s_page < 4) {
                s_page++;
                drawCurrentPage();
            } else if (gesture == TOUCH_GEST_SWIPE_RIGHT && s_page > 0) {
                s_page--;
                drawCurrentPage();
            }
            s_holdStartMs = 0;
        } else if (s_state == UIState::IDLE && isSwipe) {
            // #565 — IDLE has two screens: START and Settings. Swipe
            // left reveals Settings, swipe right returns to START.
            if (gesture == TOUCH_GEST_SWIPE_LEFT && !s_idleSettings) {
                s_idleSettings = true;
                drawSettingsPage();
            } else if (gesture == TOUCH_GEST_SWIPE_RIGHT && s_idleSettings) {
                s_idleSettings = false;
                drawIdle();
            }
            s_holdStartMs = 0;
        } else if (isTap) {
            if (s_state == UIState::IDLE) {
                handleTouchIdle(tx, ty);
            } else if (s_state == UIState::ACTIVE) {
                handleTouchActive(tx, ty, gesture);
            }
            s_holdStartMs = 0;
        }
    }

    // ── Hold actions (finger still on screen, no gesture yet) ───────────────

    // IDLE: hold-to-start — only fires on the START screen, not when
    // the user has swiped into the IDLE Settings view (#565).
    if (s_state == UIState::IDLE && !s_idleSettings && touching
        && gesture == TOUCH_GEST_NONE && held
        && (WiFi.status() == WL_CONNECTED)   // #813 green-field gate
        && hitCircle(tx, ty, CX, CY, BTN_MAIN_R)) {
        if (!s_startHeld) {
            s_startHeld = true;
            drawIdle();  // show HOLD feedback
        }
    }

    if (s_state == UIState::ACTIVE && touching && gesture == TOUCH_GEST_NONE) {
        // Page 0: hold-to-calibrate
        if (s_page == 0 && held && hitCircle(tx, ty, CX, BTN_CAL_Y, BTN_CAL_R)) {
            if (!s_calibHeld) {
                s_calibHeld = true;
                s_calibReleasePendingMs = 0;  // #774 — clear any stale debounce
                // #775 — seed the latched orientation with the start-of-hold
                // sample. The tick loop refreshes it on every cycle while
                // held so the END packet ships the last-stable pose.
                gyroIMURead(&s_calibLastRoll, &s_calibLastPitch, &s_calibLastYaw);
                gyroUdpSendCalibrate(true);  // calibrate START — server captures orientation
                gyroUdpSetStreaming(false, 0);
                drawCalibratePage();
            } else {
                // #775 — refresh the latched sample while the finger is
                // still on the button. Cheap (one I2C read per UI tick,
                // ~10 Hz), keeps the pose fresh up to the lift instant.
                gyroIMURead(&s_calibLastRoll, &s_calibLastPitch, &s_calibLastYaw);
            }
            // #774 — re-press inside the debounce window cancels the pending
            // release: operator's brief finger lift / capacitive scuff doesn't
            // commit a premature calibrate-end.
            if (s_calibReleasePendingMs != 0) {
                s_calibReleasePendingMs = 0;
            }
        }
        // Page 1: continuous colour tracking on ring
        if (s_page == 1) {
            int16_t dx = tx - CX, dy = ty - CY;
            int32_t distSq = (int32_t)dx * dx + (int32_t)dy * dy;
            int32_t hitInSq = (int32_t)COL_HIT_INNER * COL_HIT_INNER;
            int32_t hitOutSq = (int32_t)(COL_RING_OUTER + 15) * (COL_RING_OUTER + 15);
            if (distSq >= hitInSq && distSq <= hitOutSq) {
                // Finger on ring — compute hue, send at 10 Hz, no UI update
                float angle = atan2f((float)dy, (float)dx) * 180.0f / (float)M_PI;
                if (angle < 0) angle += 360.0f;
                s_selHue = (int16_t)angle;
                if (now - s_colorSendMs >= 100) {
                    s_colorSendMs = now;
                    uint8_t cr, cg, cb;
                    if (s_selHue >= WHITE_SEG_START) {
                        // White segment
                        cr = 255; cg = 255; cb = 255;
                    } else {
                        // Map angle to hue: 0°-335° → 0-360 hue
                        int16_t hue = (int16_t)((float)s_selHue * 360.0f / (float)WHITE_SEG_START);
                        hueToRGB(hue, &cr, &cg, &cb);
                    }
                    gyroUdpSendColor(cr, cg, cb, 0);
                }
            }
            // Hold-to-flash (centre button) — full screen flash feedback
            if (held && hitCircle(tx, ty, CX, CY, COL_FLASH_R)) {
                if (!s_flashHeld) {
                    s_flashHeld = true;
                    gyroClearScreen(GC_WHITE);  // full screen flash
                }
                if (now - s_flashLastMs >= 200) {
                    s_flashLastMs = now;
                    gyroUdpSendColor(255, 255, 255, 0x01);
                }
            }
        }
        // Page 3 (#867): two-button confirm — OFF (top) and STOP
        // (bottom). Hit-test each button independently so the hold
        // animation only fills the one the operator's finger is on.
        // hitCircle is exclusive — one or the other.
        if (s_page == 3 && held) {
            if (hitCircle(tx, ty, CX, CONFIRM_OFF_CY, CONFIRM_BTN_R)) {
                if (!s_offHeld) {
                    s_offHeld = true;
                    drawStopPage();
                }
            } else if (hitCircle(tx, ty, CX, CONFIRM_STOP_CY, CONFIRM_BTN_R)) {
                if (!s_stopHeld) {
                    s_stopHeld = true;
                    drawStopPage();
                }
            }
        }
        // Page 4: hold-to-sleep — arc hit test follows the bottom of
        // the display (matches the filled region).
        if (s_page == 4 && held && hitSleepArc(tx, ty)) {
            if (!s_sleepHeld) {
                s_sleepHeld = true;
                drawSettingsPage();
            }
        }
    }

    // #565 — hold-to-sleep also fires from the IDLE Settings view so
    // operators can power down before ever starting a session.
    if (s_state == UIState::IDLE && s_idleSettings && touching
        && gesture == TOUCH_GEST_NONE && held && hitSleepArc(tx, ty)) {
        if (!s_sleepHeld) {
            s_sleepHeld = true;
            drawSettingsPage();
        }
    }

    // #869 — wizard pill on the IDLE-Settings overlay. Quick tap (not
    // a hold) enters the wizard. Strictly additive: state machine
    // transitions only into UIState::WIZARD which has its own
    // handling block below.
    if (s_state == UIState::IDLE && s_idleSettings && touching
        && gesture == TOUCH_GEST_NONE && !s_wasTouching && hitWizPill(tx, ty)) {
        s_state = UIState::WIZARD;
        s_wizStep = 0;
        s_idleSettings = false;
        drawWizardPage();
    }

    // #869 — WIZARD state. Tap (not hold) on the centre button captures
    // the current Euler triple for the active step and advances. Step 3
    // means all captures are sent; auto-return to IDLE after a brief
    // confirmation. Any swipe gesture cancels and bounces back.
    if (s_state == UIState::WIZARD) {
        bool isCancelSwipe = (gesture == TOUCH_GEST_SWIPE_LEFT
                               || gesture == TOUCH_GEST_SWIPE_RIGHT
                               || gesture == TOUCH_GEST_SWIPE_UP
                               || gesture == TOUCH_GEST_SWIPE_DOWN);
        if (isCancelSwipe) {
            s_state = UIState::IDLE;
            s_wizStep = 0;
            s_idleSettings = true;
            drawSettingsPage();
        } else if (touching && !s_wasTouching && s_wizStep < 3
                    && hitCircle(tx, ty, CX, CY + 20, BTN_MAIN_R - 18)) {
            wizardCaptureCurrentStep();
            drawWizardPage();
        } else if (s_wizStep == 3 && (millis() - s_wizSentMs) > 1500) {
            // Brief "Wizard saved" confirmation has been on screen
            // long enough — return to IDLE. Operator can now claim
            // a head and the new axes are in effect.
            s_state = UIState::IDLE;
            s_wizStep = 0;
            drawIdle();
        }
    }

    // ── Release hold actions when finger lifts (gesture appears or fingers=0) ─
    if (!touching && s_wasTouching) {
        if (s_startHeld) {
            s_startHeld = false;
            // #825 — transition IDLE → WAITING_ACK, NOT directly to ACTIVE.
            // We only advance to ACTIVE when CMD_GYRO_CLAIM_ACK arrives
            // carrying a matching nonce; CLAIM_DENIED reverts; an overall
            // timeout reverts with "NO RESPONSE". This eliminates the
            // pre-#825 silent-failure window where the orchestrator held
            // an orphan claim while the gyro UI rolled back on its own.
            s_idleSettings = false;
            s_calibHeld = false;
            // Allocate a fresh nonce; gyroUdpSendStartWithNonce() ships
            // the first frame and stamps the retry slot for retransmits.
            if (s_nextNonce == 0) s_nextNonce = 1;
            s_startNonce = gyroUdpSendStartWithNonce(s_nextNonce++);
            s_startSentMs = (uint32_t)millis();
            s_state = UIState::WAITING_ACK;
            // Don't start streaming orient yet — server hasn't confirmed
            // the claim. Holding off until ACK keeps the engine from
            // churning on aim updates that'd be discarded.
            gyroUdpSetUiState(GYRO_UI_WAITING_ACK, s_startNonce);
            // Splash: dim circle + "Connecting…" hint. Cheap to draw;
            // operator gets immediate feedback their press registered.
            gyroClearScreen(GC_BLACK);
            gyroDrawCircle(CX, CY, BTN_MAIN_R, GC_GREY);
            gyroDrawText(CX - 36, CY - 7, "CONNECT", 2, GC_GREY);
            gyroDrawText(CX - 30, CY + 65, "waiting…", 1, GC_DKGREY);
        }
        if (s_calibHeld) {
            // #774 — don't commit calibrate-end yet. Start the debounce
            // timer; if the finger comes back inside CALIB_RELEASE_DEBOUNCE_MS,
            // the hold-branch above will clear s_calibReleasePendingMs and
            // we keep the existing hold. Otherwise the periodic-tick path
            // below commits the end on window expiry, using the latched
            // (last-stable) orientation captured during the hold (#775).
            if (s_calibReleasePendingMs == 0) {
                s_calibReleasePendingMs = millis();
            }
        }
        if (s_flashHeld) {
            s_flashHeld = false;
            // Send current colour (no flash flag) to cancel strobe
            if (s_selHue >= 0) {
                uint8_t cr, cg, cb;
                if (s_selHue >= WHITE_SEG_START) {
                    cr = 255; cg = 255; cb = 255;
                } else {
                    int16_t hue = (int16_t)((float)s_selHue * 360.0f / (float)WHITE_SEG_START);
                    hueToRGB(hue, &cr, &cg, &cb);
                }
                gyroUdpSendColor(cr, cg, cb, 0);  // flags=0 → no flash → cancels strobe
            } else {
                gyroUdpSendColor(255, 255, 255, 0);  // default white, no strobe
            }
            drawColourPage();  // restore full page after flash
        }
        if (s_stopHeld || s_offHeld) {
            // #867 — STOP and OFF share the post-hold state-reset path;
            // only the wire packet differs. gyroUdpSendOff() releases
            // the claim with blackout=True (head goes dark);
            // gyroUdpSendStop() releases without blackout (head holds
            // last frame).
            // #825 — both helpers allocate a fresh nonce + arm the
            // retry slot. UI snaps back to IDLE immediately; the UDP
            // layer retransmits until CMD_GYRO_STOP_ACK lands or the
            // retry budget burns. Operator gets a responsive UI;
            // orphan-claim risk is server-side (60 s stale-release
            // fallback if all retries fail).
            bool wasOff = s_offHeld;
            s_stopHeld = false;
            s_offHeld  = false;
            if (wasOff) gyroUdpSendOff();
            else        gyroUdpSendStop();
            gyroUdpSetStreaming(false, 0);
            s_state = UIState::IDLE;
            s_claimNonce = 0;
            s_startNonce = 0;
            gyroUdpSetUiState(GYRO_UI_IDLE, 0);
            drawIdle();
        }
        if (s_sleepHeld) {
            s_sleepHeld = false;
            enterDeepSleep();  // does not return — device restarts on touch wake
        }
        // On colour page: update the colour fill ring on finger release
        if (s_state == UIState::ACTIVE && s_page == 1 && s_selHue >= 0) {
            drawColourFill();
            drawFlashButton();  // redraw flash on top of fill
        }
        s_holdStartMs = 0;
    }

    s_wasTouching = touching;

    // #774 — calibrate-end debounce expiry. Runs every loop (not gated on
    // the periodic-redraw timer) so the actual commit happens within ~1 ms
    // of the window closing, not on the next 100 ms UI tick.
    if (s_calibHeld && s_calibReleasePendingMs != 0
        && !touching
        && (millis() - s_calibReleasePendingMs) >= CALIB_RELEASE_DEBOUNCE_MS) {
        s_calibHeld = false;
        s_calibReleasePendingMs = 0;
        // #775 — ship the latched (last-stable) sample, NOT a fresh IMU
        // read. The fresh read would catch post-lift jiggle and produce
        // an off-axis reference — root cause of #775's vector mis-alignment.
        gyroUdpSendCalibrateWith(false,
                                 s_calibLastRoll,
                                 s_calibLastPitch,
                                 s_calibLastYaw);
        gyroUdpSetStreaming(true, 0);
        // Brief cyan-fill confirmation flash on the Calibrate button.
        gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_CYAN);
        delay(120);
        // #813 §2.1 — auto-advance to the Colour page after the
        // calibrate gesture completes. The Calibrate page exists only
        // to perform the gesture; once done, the operator's next useful
        // surface is colour selection. Pre-fix the UI stayed on page 0
        // and the operator had to swipe manually.
        s_page = 1;
        drawColourPage();
    }

    // #772 / #825 / #872 — server refused the claim. Bounce back to
    // IDLE with a reason-specific indication. Polled here once per
    // loop; one-shot read of the flag + reason byte.
    {
        uint8_t deniedReason = 0;
        if (gyroUdpClaimDeniedConsume(&deniedReason)) {
            gyroUdpSetStreaming(false, 0);
            gyroUdpClearStartPending();
            s_state = UIState::IDLE;
            s_calibHeld = false;
            s_startHeld = false;
            s_startNonce = 0;
            s_claimNonce = 0;
            gyroUdpSetUiState(GYRO_UI_IDLE, 0);
            gyroClearScreen(GC_RED);
            // Title line + body line. Layout matches the original BUSY
            // screen so the operator's visual rhythm is preserved; only
            // the body text is reason-keyed.
            const char* title = "BUSY";
            const char* body  = "Mover held by other";
            switch (deniedReason) {
                case GYRO_DENIED_CONTROLLER_INACTIVE:
                    title = "OFF";
                    body  = "Enable in Setup";
                    break;
                case GYRO_DENIED_ALREADY_CLAIMED:
                    title = "BUSY";
                    body  = "Held by another remote";
                    break;
                case GYRO_DENIED_NO_MOVER_ASSIGNED:
                    title = "NONE";
                    body  = "No head assigned";
                    break;
                case GYRO_DENIED_ENGINE_UNAVAILABLE:
                    title = "DOWN";
                    body  = "DMX engine off";
                    break;
                default:
                    // GYRO_DENIED_IDLE (0) or unknown — legacy strings.
                    break;
            }
            gyroDrawText(CX - 18, CY - 8, title, 2, GC_WHITE);
            gyroDrawText(CX - 60, CY + 16, body, 1, GC_WHITE);
            delay(700);
            drawIdle();
        }
    }

    // #825 — WAITING_ACK resolution paths.
    if (s_state == UIState::WAITING_ACK) {
        uint16_t ackedMover = 0;
        if (gyroUdpStartAckedConsume(&ackedMover)) {
            // Claim is live. Advance to ACTIVE, start the orient stream,
            // and remember the nonce so HB_REPs can advertise it.
            s_claimNonce = s_startNonce;
            s_startNonce = 0;
            s_state = UIState::ACTIVE;
            s_page  = 0;
            gyroUdpSetStreaming(true, 0);
            gyroUdpSetUiState(GYRO_UI_ACTIVE, s_claimNonce);
            drawCurrentPage();
        } else if ((uint32_t)millis() - s_startSentMs > START_ACK_TIMEOUT_MS) {
            // Retry budget burned without an ACK or DENIED. Most likely
            // orchestrator is unreachable. Show a "NO RESPONSE" splash
            // and revert to IDLE; the operator can hold START again.
            gyroUdpClearStartPending();
            s_state = UIState::IDLE;
            s_startNonce = 0;
            s_claimNonce = 0;
            gyroUdpSetUiState(GYRO_UI_IDLE, 0);
            gyroClearScreen(GC_DKGREY);
            gyroDrawText(CX - 30, CY - 12, "NO RESP", 2, GC_RED);
            gyroDrawText(CX - 60, CY + 16, "Server unreachable", 1, GC_WHITE);
            delay(700);
            drawIdle();
        }
    }

periodic:
    // ── Periodic display update ─────────────────────────────────────────────
    uint16_t period = (s_state == UIState::ACTIVE && s_page == 2)
                      ? DRAW_PERIOD_PARK : DRAW_PERIOD_MS;
    if (now - s_lastDrawMs < period) return;
    s_lastDrawMs = now;

    // Read IMU regardless of screen
    float r, p, y;
    gyroIMURead(&r, &p, &y);

    // IDLE: redraw when WiFi-ready status changes (yellow → green).
    // Only when the START page is showing — skip the redraw when the
    // operator swiped into the Settings view (#565) so we don't
    // clobber it every time the gate flips. #813 — gate is WiFi
    // connectivity now, not the deleted CMD_GYRO_CTRL "lock" packet.
    if (s_state == UIState::IDLE && !s_idleSettings) {
        static bool s_prevReady = false;
        bool ready = (WiFi.status() == WL_CONNECTED);
        if (ready != s_prevReady) {
            s_prevReady = ready;
            drawIdle();
        }
    }

    // #566 follow-up — feed the charging-detection ring buffer on every
    // periodic tick (0.5 Hz inside batterySample()). Cheap when the
    // Settings page isn't showing; essential when it is.
    batterySample();
    // When the operator is staring at the Settings page, repaint only
    // the battery block (rows 60-112) so the charging indicator flips
    // live, plus the WiFi block (rows 125-160) so RSSI updates and
    // the IP/hostname appears as soon as WiFi associates (#778). Full-
    // page redraw caused a visible flicker every 2 s, so we repaint
    // just those rows.
    static uint32_t s_settingsRedrawMs = 0;
    bool onSettings = (s_state == UIState::IDLE && s_idleSettings) ||
                      (s_state == UIState::ACTIVE && s_page == 4);
    if (onSettings && !s_sleepHeld && (now - s_settingsRedrawMs) >= 2000) {
        s_settingsRedrawMs = now;
        drawBatteryInfo();
        // #778 — repaint the WiFi block. Cover rows 125-160 first so
        // stale text doesn't bleed through, then redraw.
        gyroFillRect(0, 125, GYRO_LCD_W, 35, GC_BLACK);
        if (wifiOk()) {
            gyroDrawText(52, 125, "WiFi: Connected", 1, GC_GREEN);
            char hostLine[40];
            const char* hn = WiFi.getHostname();
            int8_t rssi = (int8_t)WiFi.RSSI();
            snprintf(hostLine, sizeof(hostLine), "%s (%d dBm)",
                     (hn && *hn) ? hn : "?", rssi);
            gyroDrawText(52, 138, hostLine, 1, GC_GREY);
            String ipStr = WiFi.localIP().toString();
            gyroDrawText(52, 151, ipStr.c_str(), 1, GC_WHITE);
        } else {
            gyroDrawText(52, 125, "WiFi: Disconnected", 1, GC_RED);
        }
    }

    // Page 2 (status park): only redraw dot if status changed
    if (s_state == UIState::ACTIVE && s_page == 2) {
        static bool s_prevOk = false;
        bool ok = gyroUdpStreaming() && wifiOk();
        if (ok != s_prevOk) {
            gyroFillCircle(CX, CY, 12, ok ? GC_GREEN : GC_RED);
            s_prevOk = ok;
        }
    }
}

#endif  // BOARD_GYRO
