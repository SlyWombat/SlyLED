/*
 * GyroUI.cpp — Touch-screen state machine for the gyro orientation board.
 *
 * State machine:
 *   LOGO     → WiFi connect (or 5s timeout) → IDLE
 *   IDLE     → tap START                     → ACTIVE (page 0)
 *   ACTIVE   → tap STOP (page 2)            → IDLE
 *   ACTIVE   → swipe left/right             → page 0/1/2/3
 *
 * ACTIVE pages:
 *   0 — Calibrate (hold-to-calibrate)
 *   1 — Colour / Flash
 *   2 — Stop
 *   3 — Status (park / power-save, 2 Hz update)
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroUI.h"
#include "GyroDisplay.h"
#include "GyroTouch.h"
#include "GyroIMU.h"
#include "GyroUdp.h"
#include "GyroLogo.h"
#include <Arduino.h>

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
static bool s_calibHeld = false;
static bool s_flashHeld = false;
static bool s_stopHeld  = false;

// Selected colour preset index (page 1)

// ── Drawing helpers ──────────────────────────────────────────────────────────

static bool wifiOk() { return WiFi.status() == WL_CONNECTED; }

static void drawPageDots() {
    int16_t startX = CX - (int16_t)(3 * PAGE_DOT_SP) / 2;
    for (uint8_t i = 0; i < 4; i++) {
        int16_t dx = startX + i * PAGE_DOT_SP;
        uint16_t col = (i == s_page) ? GC_WHITE : GC_DKGREY;
        gyroFillCircle(dx, PAGE_DOT_Y, PAGE_DOT_R, col);
    }
}

static void drawLiveIndicator() {
    gyroFillCircle(LIVE_X, LIVE_Y, LIVE_R, GC_GREEN);
    gyroDrawText(LIVE_X + 10, LIVE_Y - 4, "LIVE", 1, GC_GREEN);
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
    // WiFi dot
    gyroFillCircle(WIFI_X, WIFI_Y, WIFI_R, wifiOk() ? GC_GREEN : GC_RED);
    // START button
    gyroFillCircle(CX, CY, BTN_MAIN_R, (uint16_t)0x0360u);  // dark green
    gyroDrawCircle(CX, CY, BTN_MAIN_R, GC_GREEN);
    const char* lbl = "START";
    int16_t tw = (int16_t)(strlen(lbl) * 6 * 2);
    gyroDrawText(CX - tw / 2, CY - 7, lbl, 2, GC_WHITE);
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
        gyroDrawText(28, 168, "Release to set zero", 1, GC_CYAN);
    } else if (live) {
        // Live — green calibrate button
        gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, (uint16_t)0x0360u);
        gyroDrawCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_GREEN);
        gyroDrawText(CX - 27, BTN_CAL_Y - 3, "CALIBRATE", 1, GC_WHITE);
        gyroDrawText(24, 168, "Hold to pause & move", 1, GC_GREY);
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
    // 12 distinct colour segments (30° each) — clean on RGB565
    for (int16_t i = 0; i < 12; i++) {
        int16_t hue = i * 30;
        uint8_t r, g, b;
        hueToRGB(hue, &r, &g, &b);
        uint16_t col = gc9a01_rgb565(r, g, b);
        gyroDrawArcSegment(CX, CY, COL_RING_OUTER, COL_RING_T,
                           i * 30, i * 30 + 29, col);
    }
}

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
    uint8_t r, g, b;
    hueToRGB(s_selHue, &r, &g, &b);
    uint16_t col = gc9a01_rgb565(r, g, b);
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
    gyroDrawText(CX - 33, 28, "LIGHT CTRL", 1, GC_CYAN);
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

// ── Page dispatch ────────────────────────────────────────────────────────────

// Pages: 0=Calibrate, 1=Colour, 2=Status(park), 3=Stop
static void drawCurrentPage() {
    switch (s_page) {
        case 0: drawCalibratePage(); break;
        case 1: drawColourPage(); break;
        case 2: drawStatusPage(); break;
        case 3: drawStopPage(); break;
    }
}

// ── Touch helpers ────────────────────────────────────────────────────────────

static bool hitCircle(int16_t tx, int16_t ty, int16_t cx, int16_t cy, int16_t r) {
    int16_t dx = tx - cx, dy = ty - cy;
    return (dx * dx + dy * dy) <= (r * r);
}

// ── Touch handlers per state ─────────────────────────────────────────────────

static void handleTouchIdle(int16_t tx, int16_t ty) {
    if (hitCircle(tx, ty, CX, CY, BTN_MAIN_R)) {
        s_state = UIState::ACTIVE;
        s_page  = 0;
        s_calibHeld = false;
        gyroUdpSetStreaming(true, 0);
        drawCurrentPage();
    }
}

static void handleTouchActive(int16_t tx, int16_t ty, uint8_t gesture) {
    // Swipe navigation (works on all pages)
    if (gesture == TOUCH_GEST_SWIPE_LEFT && s_page < 3) {
        s_page++;
        s_calibHeld = false;
        drawCurrentPage();
        return;
    }
    if (gesture == TOUCH_GEST_SWIPE_RIGHT && s_page > 0) {
        s_page--;
        s_calibHeld = false;
        drawCurrentPage();
        return;
    }

    // Page-specific tap handling
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

    // If WiFi is already connected (rare on cold boot), skip logo
    if (wifiOk()) {
        s_state = UIState::IDLE;
        drawIdle();
    } else {
        drawLogo();
    }
    if (Serial) Serial.println(F("[GyroUI] Ready"));
}

void gyroUIUpdate() {
    unsigned long now = millis();

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
            if (gesture == TOUCH_GEST_SWIPE_LEFT && s_page < 3) {
                s_page++;
                drawCurrentPage();
            } else if (gesture == TOUCH_GEST_SWIPE_RIGHT && s_page > 0) {
                s_page--;
                drawCurrentPage();
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
    if (s_state == UIState::ACTIVE && touching && gesture == TOUCH_GEST_NONE) {
        // Page 0: hold-to-calibrate
        if (s_page == 0 && held && hitCircle(tx, ty, CX, BTN_CAL_Y, BTN_CAL_R)) {
            if (!s_calibHeld) {
                s_calibHeld = true;
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
                    hueToRGB(s_selHue, &cr, &cg, &cb);
                    gyroUdpSendColor(cr, cg, cb, 0);
                }
            }
            // Hold-to-flash (centre button)
            if (held && hitCircle(tx, ty, CX, CY, COL_FLASH_R)) {
                if (!s_flashHeld) {
                    s_flashHeld = true;
                    drawFlashButton();
                }
                if (now - s_flashLastMs >= 200) {
                    s_flashLastMs = now;
                    gyroUdpSendColor(255, 255, 255, 0x01);
                }
            }
        }
        // Page 2: hold-to-stop
        if (s_page == 3 && held && hitCircle(tx, ty, CX, CY, BTN_MAIN_R)) {
            if (!s_stopHeld) {
                s_stopHeld = true;
                drawStopPage();
            }
        }
    }

    // ── Release hold actions when finger lifts (gesture appears or fingers=0) ─
    if (!touching && s_wasTouching) {
        if (s_calibHeld) {
            s_calibHeld = false;
            gyroIMUZero();
            gyroUdpSetStreaming(true, 0);
            gyroFillCircle(CX, BTN_CAL_Y, BTN_CAL_R, GC_CYAN);
            delay(120);
            drawCalibratePage();
        }
        if (s_flashHeld) {
            s_flashHeld = false;
            drawFlashButton();
        }
        if (s_stopHeld) {
            s_stopHeld = false;
            gyroUdpSetStreaming(false, 0);
            s_state = UIState::IDLE;
            drawIdle();
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
