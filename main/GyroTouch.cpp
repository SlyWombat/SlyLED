/*
 * GyroTouch.cpp — CST816S capacitive touch controller driver.
 *
 * Uses the CST816S interrupt pin (active-low, falling edge) to detect
 * real touch events. Validates gestures by requiring a finger to be
 * present and coordinates to be within the display area.
 *
 * Register map (CST816S, I2C address 0x15):
 *   0x01 : GestureID  — gesture code (see TOUCH_GEST_* in GyroTouch.h)
 *   0x02 : FingerNum  — number of touch points (0 or 1)
 *   0x03 : XposH      — bits[7:4] event flag, bits[3:0] X high
 *   0x04 : XposL      — X low byte
 *   0x05 : YposH      — bits[7:4] event flag, bits[3:0] Y high
 *   0x06 : YposL      — Y low byte
 *   0xA7 : ChipID     — reads 0xB5 for CST816S
 *   0xEC : IrqCtl     — interrupt mode control
 *   0xFA : MotionMask — bit 0 = continuous gesture detection enable
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroTouch.h"
#include "GyroBoard.h"
#include <Arduino.h>
#include <Wire.h>

// ── CST816S register addresses ────────────────────────────────────────────────
static constexpr uint8_t REG_GESTURE    = 0x01;
static constexpr uint8_t REG_FINGER_NUM = 0x02;
static constexpr uint8_t REG_XPOS_H     = 0x03;
static constexpr uint8_t REG_CHIP_ID    = 0xA7;
static constexpr uint8_t REG_IRQ_CTL    = 0xEC;
static constexpr uint8_t REG_MOTION_MSK = 0xFA;
static constexpr uint8_t REG_AUTO_RESET = 0xFB;
static constexpr uint8_t REG_LONG_PRESS = 0xFC;
static constexpr uint8_t REG_DIS_AUTO_SLEEP = 0xFE;

static constexpr uint8_t CHIP_ID_EXPECTED = 0xB5;

// Interrupt state — set by ISR, cleared after read
static volatile bool s_irqFired = false;
static volatile unsigned long s_irqMs = 0;

// Last valid gesture state
static uint8_t  s_lastGesture = TOUCH_GEST_NONE;
static bool     s_fingerDown  = false;
static int16_t  s_lastX = 120;
static int16_t  s_lastY = 120;

// ── ISR ──────────────────────────────────────────────────────────────────────
static void IRAM_ATTR touchISR() {
    s_irqFired = true;
    s_irqMs = millis();
}

// ── I2C helpers ───────────────────────────────────────────────────────────────
static bool tpReadReg(uint8_t reg, uint8_t* buf, uint8_t len) {
    Wire.beginTransmission(GYRO_TP_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    uint8_t received = Wire.requestFrom((uint8_t)GYRO_TP_ADDR, len);
    if (received != len) return false;
    for (uint8_t i = 0; i < len; i++) buf[i] = Wire.read();
    return true;
}

static bool tpWriteReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(GYRO_TP_ADDR);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

// ── Initialisation ────────────────────────────────────────────────────────────
void gyroTouchInit() {
    // I2C bus shared with QMI8658 IMU; use explicit pins and frequency.
    Wire.begin(GYRO_TP_SDA, GYRO_TP_SCL, GYRO_I2C_FREQ);

    // Hardware reset sequence for CST816S
    pinMode(GYRO_TP_RST, OUTPUT);
    digitalWrite(GYRO_TP_RST, LOW);  delay(5);
    digitalWrite(GYRO_TP_RST, HIGH); delay(50);

    // Interrupt pin — active-low, falling edge
    pinMode(GYRO_TP_INT, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(GYRO_TP_INT), touchISR, FALLING);

    // Verify chip ID
    uint8_t id = 0;
    if (tpReadReg(REG_CHIP_ID, &id, 1)) {
        if (Serial) {
            Serial.print(F("[TOUCH] CST816S chip ID: 0x"));
            Serial.println(id, HEX);
            if (id != CHIP_ID_EXPECTED)
                Serial.println(F("[TOUCH] Warning: unexpected chip ID"));
        }
    } else {
        if (Serial) Serial.println(F("[TOUCH] I2C error reading CST816S"));
    }

    // Configure CST816S:
    // - Disable auto-sleep so gestures are always available
    tpWriteReg(REG_DIS_AUTO_SLEEP, 0x01);
    // - Enable interrupt on touch (motion trigger mode)
    tpWriteReg(REG_IRQ_CTL, 0x61);  // EN_MOTION | EN_TOUCH | EN_CHANGE
    // - Enable continuous gesture detection
    tpWriteReg(REG_MOTION_MSK, 0x01);

    s_irqFired = false;
    s_fingerDown = false;
    s_lastGesture = TOUCH_GEST_NONE;

    if (Serial) Serial.println(F("[TOUCH] Interrupt-driven mode ready"));
}

// ── Read ──────────────────────────────────────────────────────────────────────
bool gyroTouchRead(int16_t* x, int16_t* y, uint8_t* gesture) {
    *gesture = TOUCH_GEST_NONE;
    *x = s_lastX;
    *y = s_lastY;

    // Only read I2C when the interrupt fired (real touch event)
    // Also poll periodically (every ~50ms) to catch finger-up transitions
    static unsigned long s_lastPollMs = 0;
    unsigned long now = millis();
    bool shouldRead = s_irqFired || (now - s_lastPollMs >= 50);
    if (!shouldRead) {
        // Return last known finger state
        return s_fingerDown;
    }
    s_irqFired = false;
    s_lastPollMs = now;

    // Read gesture + finger count + coordinates (6 bytes from register 0x01)
    uint8_t buf[6];
    if (!tpReadReg(REG_GESTURE, buf, 6)) {
        s_fingerDown = false;
        return false;
    }

    uint8_t rawGesture = buf[0];
    uint8_t fingers = buf[1] & 0x0F;
    int16_t rx = (int16_t)(((uint16_t)(buf[2] & 0x0F) << 8) | buf[3]);
    int16_t ry = (int16_t)(((uint16_t)(buf[4] & 0x0F) << 8) | buf[5]);

    // Validate coordinates are within display bounds (0-239)
    // Reject phantom touches from vibration/noise
    bool validCoords = (rx >= 0 && rx < 240 && ry >= 0 && ry < 240);

    if (fingers > 0 && validCoords) {
        s_fingerDown = true;
        s_lastX = rx;
        s_lastY = ry;
        *x = rx;
        *y = ry;
    } else {
        s_fingerDown = false;
    }

    // Validate gesture: only accept if finger was/is present AND
    // gesture is a known code (reject stale/noise gesture IDs)
    if (rawGesture != TOUCH_GEST_NONE) {
        bool knownGesture = (rawGesture == TOUCH_GEST_SWIPE_UP ||
                             rawGesture == TOUCH_GEST_SWIPE_DOWN ||
                             rawGesture == TOUCH_GEST_SWIPE_LEFT ||
                             rawGesture == TOUCH_GEST_SWIPE_RIGHT ||
                             rawGesture == TOUCH_GEST_CLICK ||
                             rawGesture == TOUCH_GEST_DBLCLICK ||
                             rawGesture == TOUCH_GEST_LONGPRESS);
        // For swipes: require that the interrupt fired (not just periodic poll)
        // This prevents vibration-triggered false swipes
        bool irqRecent = (now - s_irqMs < 200);
        if (knownGesture && irqRecent) {
            *gesture = rawGesture;
        }
    }

    return s_fingerDown;
}

#endif  // BOARD_GYRO
