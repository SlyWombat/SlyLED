/*
 * GyroTouch.cpp — CST816S capacitive touch controller driver.
 *
 * Register map (CST816S, I2C address 0x15):
 *   0x00 : GestureID  — gesture code (see TOUCH_GEST_* in GyroTouch.h)
 *   0x01 : FingerNum  — number of touch points (0 or 1)
 *   0x02 : XposH      — bits[7:4] event flag, bits[3:0] X high
 *   0x03 : XposL      — X low byte
 *   0x04 : YposH      — bits[7:4] event flag, bits[3:0] Y high
 *   0x05 : YposL      — Y low byte
 *   0xA7 : ChipID     — reads 0xB5 for CST816S
 *   0xFA : MotionMask — bit 0 = continuous gesture detection enable
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroTouch.h"
#include "GyroBoard.h"
#include <Arduino.h>
#include <Wire.h>

// ── CST816S register addresses ────────────────────────────────────────────────
// Register map starts at 0x01 (0x00 is reserved/unused on CST816S).
static constexpr uint8_t REG_GESTURE    = 0x01;
static constexpr uint8_t REG_FINGER_NUM = 0x02;
static constexpr uint8_t REG_XPOS_H     = 0x03;
static constexpr uint8_t REG_CHIP_ID    = 0xA7;
static constexpr uint8_t REG_MOTION_MSK = 0xFA;

static constexpr uint8_t CHIP_ID_EXPECTED = 0xB5;

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

    // Interrupt pin — input with internal pull-up (active-low, falling edge)
    pinMode(GYRO_TP_INT, INPUT_PULLUP);

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

    // Enable continuous gesture detection (EnMTouchKey bit 0 in MotionMask)
    tpWriteReg(REG_MOTION_MSK, 0x01);
}

// ── Read ──────────────────────────────────────────────────────────────────────

bool gyroTouchRead(int16_t* x, int16_t* y, uint8_t* gesture) {
    uint8_t buf[6];
    if (!tpReadReg(REG_GESTURE, buf, 6)) {
        *gesture = TOUCH_GEST_NONE;
        return false;
    }

    *gesture = buf[0];                                // 0x01: GestureID
    uint8_t fingers = buf[1] & 0x0F;                 // 0x02: FingerNum

    *x = (int16_t)(((uint16_t)(buf[2] & 0x0F) << 8) | buf[3]);
    *y = (int16_t)(((uint16_t)(buf[4] & 0x0F) << 8) | buf[5]);

    return fingers > 0;
}

#endif  // BOARD_GYRO
