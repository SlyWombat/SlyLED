/*
 * GyroDisplay.h — GC9A01 240×240 round TFT display driver.
 *
 * Minimal SPI driver with no external library dependency.
 * Provides drawing primitives used by GyroUI.h/.cpp (Issue #401).
 *
 * Colour format: RGB565 (16-bit big-endian on the wire).
 * Use the gc9a01_rgb565() helper or the pre-defined colour constants.
 */

#ifndef GYRODISPLAY_H
#define GYRODISPLAY_H

#ifdef BOARD_GYRO

#include <stdint.h>

// ── Pre-defined RGB565 colours ────────────────────────────────────────────────
constexpr uint16_t GC_BLACK   = 0x0000;
constexpr uint16_t GC_WHITE   = 0xFFFF;
constexpr uint16_t GC_RED     = 0xF800;
constexpr uint16_t GC_GREEN   = 0x07E0;
constexpr uint16_t GC_BLUE    = 0x001F;
constexpr uint16_t GC_CYAN    = 0x07FF;
constexpr uint16_t GC_YELLOW  = 0xFFE0;
constexpr uint16_t GC_MAGENTA = 0xF81F;
constexpr uint16_t GC_GREY    = 0x7BEF;
constexpr uint16_t GC_DKGREY  = 0x39E7;
constexpr uint16_t GC_ORANGE  = 0xFD20;

// Convert 8-bit R, G, B components to RGB565.
inline uint16_t gc9a01_rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return (uint16_t)((r & 0xF8) << 8) | (uint16_t)((g & 0xFC) << 3) | (b >> 3);
}

// ── Initialisation ────────────────────────────────────────────────────────────

// Initialise SPI bus, GPIO pins, reset the GC9A01 and send the init sequence.
// Backlight is enabled at the end. Call once from setup().
void gyroDisplayInit();

// ── Backlight ─────────────────────────────────────────────────────────────────
void gyroSetBacklight(bool on);

// ── Drawing primitives ────────────────────────────────────────────────────────

// Fill the entire screen with a single colour (clears to black by default).
void gyroClearScreen(uint16_t colour = GC_BLACK);

// Fill an axis-aligned rectangle.
void gyroFillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour);

// Draw a single pixel (used internally; prefer bulk operations for speed).
void gyroDrawPixel(int16_t x, int16_t y, uint16_t colour);

// Filled disc.
void gyroFillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t colour);

// Circle outline (1 px wide).
void gyroDrawCircle(int16_t cx, int16_t cy, int16_t r, uint16_t colour);

// Arc segment: draws the arc from startDeg to endDeg (0° = right, clockwise)
// with the given radial thickness (pixels toward centre from radius r).
void gyroDrawArcSegment(int16_t cx, int16_t cy, int16_t r, int16_t thickness,
                        int16_t startDeg, int16_t endDeg, uint16_t colour);

// Render a string using the built-in 5×7 bitmap font.
// size=1 → 5×7 px per glyph; size=2 → 10×14 px; etc.
// Printable ASCII 0x20–0x7E only; other bytes are skipped.
void gyroDrawText(int16_t x, int16_t y, const char* str,
                  uint8_t size, uint16_t colour);

// Draw a single character; returns the pixel width consumed (including gap).
int16_t gyroDrawChar(int16_t x, int16_t y, char c,
                     uint8_t size, uint16_t colour);

#endif  // BOARD_GYRO
#endif  // GYRODISPLAY_H
