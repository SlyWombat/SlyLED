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
#include <Arduino.h>
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

enum class UIState : uint8_t { LOGO, IDLE, ACTIVE };

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

// #565 — when true, the IDLE state has been swiped into Settings. This
// keeps the app in UIState::IDLE (so the server isn't claimed) but
// paints the Settings page so operators can reach battery info and
// Sleep before starting. Swipe back to clear.
static bool s_idleSettings = false;

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
    bool locked = gyroUdpHasLock();

    if (s_startHeld && locked) {
        // Holding — bright green, visual feedback
        gyroFillCircle(CX, CY, BTN_MAIN_R, GC_GREEN);
        gyroDrawCircle(CX, CY, BTN_MAIN_R, GC_WHITE);
        const char* lbl = "HOLD";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);
    } else {
        // START button — yellow (waiting for lock) or green (locked by orchestrator)
        uint16_t btnFill = locked ? (uint16_t)0x0360u : (uint16_t)0x4200u;
        uint16_t btnRing = locked ? GC_GREEN : GC_YELLOW;
        gyroFillCircle(CX, CY, BTN_MAIN_R, btnFill);
        gyroDrawCircle(CX, CY, BTN_MAIN_R, btnRing);
        const char* lbl = "START";
        int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
        gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);
    }

    if (!locked) {
        const char* hint = "Waiting for lock...";
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
    gyroClearScreen(GC_BLACK);
    bool live = gyroUdpStreaming();

    if (s_calibHeld) {
        // Holding — orange button, release hint
        gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_ORANGE);
        gyroDrawCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_WHITE);
        gyroDrawText(CX - 24, BTN_CAL_Y - 7, "HOLD", 2, GC_WHITE);
        // "Release to set zero" = 19 chars × 6px = 114px → center
        gyroDrawText(CX - 57, 168, "Release to set zero", 1, GC_CYAN);
    } else if (live) {
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

// ── ACTIVE page 2 — Stop ────────────────────────────────────────────────────

static void drawStopPage() {
    gyroClearScreen(GC_BLACK);
    drawLiveIndicator();

    // STOP button — brighter when held
    uint16_t fill = s_stopHeld ? GC_RED : (uint16_t)0x9800u;
    gyroFillCircle(CX, CY, BTN_MAIN_R, fill);
    gyroDrawCircle(CX, CY, BTN_MAIN_R, GC_RED);
    const char* lbl = s_stopHeld ? "HOLD" : "STOP";
    int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
    gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);

    if (!s_stopHeld)
        gyroDrawText(26, 180, "Hold to stop", 1, GC_GREY);

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
static float readBatteryVoltage() {
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

static int batteryPercent(float voltage) {
    // LiPo: 4.2V=100%, 3.7V=50%, 3.3V=0%
    if (voltage < 0) return -1;  // no battery
    if (voltage >= 4.2f) return 100;
    if (voltage <= 3.3f) return 0;
    return (int)((voltage - 3.3f) / 0.9f * 100.0f);
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

static void drawSettingsPage() {
    gyroClearScreen(GC_BLACK);
    gyroDrawText(CX - 27, 32, "SETTINGS", 1, GC_CYAN);

    // Battery
    float vbat = readBatteryVoltage();
    int pct = batteryPercent(vbat);
    if (pct >= 0) {
        char buf[32];
        bool charging = batteryCharging();
        // "Battery: 87% ⚡" when charging, plain "Battery: 87%" otherwise.
        // The lightning-bolt glyph is ASCII 0x0F in the stock font5x7 table
        // used by gyroDrawText, so fall back to "+" if the font lacks it.
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
    } else {
        gyroDrawText(48, 80, "No battery", 1, GC_GREY);
    }

    // WiFi info
    gyroDrawText(52, 125, wifiOk() ? "WiFi: Connected" : "WiFi: Disconnected", 1,
                 wifiOk() ? GC_GREEN : GC_RED);

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
        && gesture == TOUCH_GEST_NONE && held && gyroUdpHasLock()
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
                gyroUdpSendCalibrate(true);  // calibrate START — server captures orientation
                gyroUdpSetStreaming(false, 0);
                drawCalibratePage();
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
        // Page 3: hold-to-stop
        if (s_page == 3 && held && hitCircle(tx, ty, CX, CY, BTN_MAIN_R)) {
            if (!s_stopHeld) {
                s_stopHeld = true;
                drawStopPage();
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

    // ── Release hold actions when finger lifts (gesture appears or fingers=0) ─
    if (!touching && s_wasTouching) {
        if (s_startHeld) {
            s_startHeld = false;
            // Transition IDLE → ACTIVE. Clear any swiped-into-Settings
            // state from IDLE so a return-to-IDLE later starts on the
            // START screen, not Settings (#565).
            s_idleSettings = false;
            s_state = UIState::ACTIVE;
            s_page  = 0;
            s_calibHeld = false;
            gyroUdpSetStreaming(true, 0);
            drawCurrentPage();
        }
        if (s_calibHeld) {
            s_calibHeld = false;
            gyroUdpSendCalibrate(false);  // calibrate END — server captures reference
            gyroUdpSetStreaming(true, 0);
            gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_CYAN);
            delay(120);
            drawCalibratePage();
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
        if (s_stopHeld) {
            s_stopHeld = false;
            gyroUdpSendStop();  // tell server → release claim + blackout
            gyroUdpSetStreaming(false, 0);
            s_state = UIState::IDLE;
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

periodic:
    // ── Periodic display update ─────────────────────────────────────────────
    uint16_t period = (s_state == UIState::ACTIVE && s_page == 2)
                      ? DRAW_PERIOD_PARK : DRAW_PERIOD_MS;
    if (now - s_lastDrawMs < period) return;
    s_lastDrawMs = now;

    // Read IMU regardless of screen
    float r, p, y;
    gyroIMURead(&r, &p, &y);

    // IDLE: redraw when lock status changes (yellow → green). Only when
    // the START page is showing — skip the redraw when the operator
    // swiped into the Settings view (#565) so we don't clobber it every
    // time the lock flips.
    if (s_state == UIState::IDLE && !s_idleSettings) {
        static bool s_prevLock = false;
        bool locked = gyroUdpHasLock();
        if (locked != s_prevLock) {
            s_prevLock = locked;
            drawIdle();
        }
    }

    // #566 follow-up — feed the charging-detection ring buffer on every
    // periodic tick (0.5 Hz inside batterySample()). Cheap when the
    // Settings page isn't showing; essential when it is.
    batterySample();
    // When the operator is staring at the Settings page, repaint once
    // per batterySample interval so the charging indicator flips live
    // instead of waiting for a page transition. 2 s matches the ring
    // sample cadence.
    static uint32_t s_settingsRedrawMs = 0;
    bool onSettings = (s_state == UIState::IDLE && s_idleSettings) ||
                      (s_state == UIState::ACTIVE && s_page == 4);
    if (onSettings && !s_sleepHeld && (now - s_settingsRedrawMs) >= 2000) {
        s_settingsRedrawMs = now;
        drawSettingsPage();
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
