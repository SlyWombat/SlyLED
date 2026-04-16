/*
 * GyroBoard.h — Hardware pin-map for Waveshare ESP32-S3 1.28" Round Touch LCD.
 *
 * Board: Waveshare ESP32-S3-LCD-1.28
 * Display:  GC9A01  240×240 round TFT, SPI
 * Touch:    CST816S capacitive, I2C
 * IMU:      QMI8658 6-axis accel+gyro, I2C (shared bus with touch)
 *
 * Build with: -DGYRO_BOARD (arduino-cli --build-property build.extra_flags=-DGYRO_BOARD)
 */

#ifndef GYROBOARD_H
#define GYROBOARD_H

#ifdef BOARD_GYRO

// ── GC9A01 Round LCD (240×240, SPI) ──────────────────────────────────────────
// SPI2 bus; CS driven in software for fine-grained transaction control.
// GPIO9 is routed to LCD_CS on this board (flash uses QIO on GPIO[0:5,15:17]).
constexpr uint8_t GYRO_LCD_MOSI = 11;
constexpr uint8_t GYRO_LCD_SCLK = 10;
constexpr uint8_t GYRO_LCD_CS   =  9;
constexpr uint8_t GYRO_LCD_DC   =  8;
constexpr uint8_t GYRO_LCD_RST  = 14;
constexpr uint8_t GYRO_LCD_BL   =  2;   // active-high backlight (PWM-capable)
constexpr uint16_t GYRO_LCD_W   = 240;
constexpr uint16_t GYRO_LCD_H   = 240;

// MADCTL orientation byte — 0x08 = BGR order, no row/column mirroring.
// Change to 0x48 (MX | BGR) or 0xC8 (MY | MX | BGR) to rotate display.
constexpr uint8_t GYRO_LCD_MADCTL = 0x08;

// ── CST816S Capacitive Touch (I2C, address 0x15) ─────────────────────────────
constexpr uint8_t GYRO_TP_SDA  =  6;
constexpr uint8_t GYRO_TP_SCL  =  7;
constexpr uint8_t GYRO_TP_INT  =  5;   // active-low, falling-edge interrupt
constexpr uint8_t GYRO_TP_RST  = 13;
constexpr uint8_t GYRO_TP_ADDR = 0x15;

// ── QMI8658 6-Axis IMU (I2C, shared bus, address 0x6B) ───────────────────────
// SDA/SCL are the same pins as the touch controller.
constexpr uint8_t GYRO_IMU_ADDR = 0x6B;
constexpr uint8_t GYRO_IMU_INT1 =  4;  // data-ready interrupt (optional)

// ── I2C bus frequency ─────────────────────────────────────────────────────────
// CST816S supports up to 400 kHz; QMI8658 up to 1 MHz in standard I2C mode.
constexpr uint32_t GYRO_I2C_FREQ = 400000UL;

// ── Battery voltage ADC (optional — set to 0 if no battery circuit) ─────
// Waveshare ESP32-S3-LCD-1.28 with battery: voltage divider on GPIO1
// Vbat → 100k → ADC → 100k → GND (divider ratio 2:1, max 4.2V → 2.1V)
constexpr uint8_t GYRO_BAT_PIN = 1;        // GPIO1 ADC, 0 = disabled
constexpr float   GYRO_BAT_DIVIDER = 2.0f; // voltage divider ratio

#endif  // BOARD_GYRO
#endif  // GYROBOARD_H
