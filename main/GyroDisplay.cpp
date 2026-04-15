/*
 * GyroDisplay.cpp — GC9A01 SPI driver for Waveshare ESP32-S3 1.28" Round LCD.
 *
 * Implements a minimal register-level SPI driver (no external library required).
 * Init sequence derived from the Waveshare GC9A01 application notes.
 */

#include "BoardConfig.h"

#ifdef BOARD_GYRO

#include "GyroDisplay.h"
#include "GyroBoard.h"
#include <Arduino.h>
#include <SPI.h>
#include <math.h>

// ── 5×7 bitmap font (ASCII 0x20–0x7E) ────────────────────────────────────────
// Each entry is 5 bytes; each byte is one column (bit 0 = top row, bit 6 = bottom).
// 95 characters × 5 bytes = 475 bytes (no PROGMEM needed on ESP32).
static const uint8_t s_font5x7[][5] = {
  { 0x00, 0x00, 0x00, 0x00, 0x00 }, // ' '  0x20
  { 0x00, 0x00, 0x5F, 0x00, 0x00 }, // '!'
  { 0x00, 0x07, 0x00, 0x07, 0x00 }, // '"'
  { 0x14, 0x7F, 0x14, 0x7F, 0x14 }, // '#'
  { 0x24, 0x2A, 0x7F, 0x2A, 0x12 }, // '$'
  { 0x23, 0x13, 0x08, 0x64, 0x62 }, // '%'
  { 0x36, 0x49, 0x55, 0x22, 0x50 }, // '&'
  { 0x00, 0x05, 0x03, 0x00, 0x00 }, // '\''
  { 0x00, 0x1C, 0x22, 0x41, 0x00 }, // '('
  { 0x00, 0x41, 0x22, 0x1C, 0x00 }, // ')'
  { 0x14, 0x08, 0x3E, 0x08, 0x14 }, // '*'
  { 0x08, 0x08, 0x3E, 0x08, 0x08 }, // '+'
  { 0x00, 0x50, 0x30, 0x00, 0x00 }, // ','
  { 0x08, 0x08, 0x08, 0x08, 0x08 }, // '-'
  { 0x00, 0x60, 0x60, 0x00, 0x00 }, // '.'
  { 0x20, 0x10, 0x08, 0x04, 0x02 }, // '/'
  { 0x3E, 0x51, 0x49, 0x45, 0x3E }, // '0'
  { 0x00, 0x42, 0x7F, 0x40, 0x00 }, // '1'
  { 0x42, 0x61, 0x51, 0x49, 0x46 }, // '2'
  { 0x21, 0x41, 0x45, 0x4B, 0x31 }, // '3'
  { 0x18, 0x14, 0x12, 0x7F, 0x10 }, // '4'
  { 0x27, 0x45, 0x45, 0x45, 0x39 }, // '5'
  { 0x3C, 0x4A, 0x49, 0x49, 0x30 }, // '6'
  { 0x01, 0x71, 0x09, 0x05, 0x03 }, // '7'
  { 0x36, 0x49, 0x49, 0x49, 0x36 }, // '8'
  { 0x06, 0x49, 0x49, 0x29, 0x1E }, // '9'
  { 0x00, 0x36, 0x36, 0x00, 0x00 }, // ':'
  { 0x00, 0x56, 0x36, 0x00, 0x00 }, // ';'
  { 0x08, 0x14, 0x22, 0x41, 0x00 }, // '<'
  { 0x14, 0x14, 0x14, 0x14, 0x14 }, // '='
  { 0x00, 0x41, 0x22, 0x14, 0x08 }, // '>'
  { 0x02, 0x01, 0x51, 0x09, 0x06 }, // '?'
  { 0x32, 0x49, 0x79, 0x41, 0x3E }, // '@'
  { 0x7E, 0x11, 0x11, 0x11, 0x7E }, // 'A'
  { 0x7F, 0x49, 0x49, 0x49, 0x36 }, // 'B'
  { 0x3E, 0x41, 0x41, 0x41, 0x22 }, // 'C'
  { 0x7F, 0x41, 0x41, 0x22, 0x1C }, // 'D'
  { 0x7F, 0x49, 0x49, 0x49, 0x41 }, // 'E'
  { 0x7F, 0x09, 0x09, 0x09, 0x01 }, // 'F'
  { 0x3E, 0x41, 0x49, 0x49, 0x7A }, // 'G'
  { 0x7F, 0x08, 0x08, 0x08, 0x7F }, // 'H'
  { 0x00, 0x41, 0x7F, 0x41, 0x00 }, // 'I'
  { 0x20, 0x40, 0x41, 0x3F, 0x01 }, // 'J'
  { 0x7F, 0x08, 0x14, 0x22, 0x41 }, // 'K'
  { 0x7F, 0x40, 0x40, 0x40, 0x40 }, // 'L'
  { 0x7F, 0x02, 0x0C, 0x02, 0x7F }, // 'M'
  { 0x7F, 0x04, 0x08, 0x10, 0x7F }, // 'N'
  { 0x3E, 0x41, 0x41, 0x41, 0x3E }, // 'O'
  { 0x7F, 0x09, 0x09, 0x09, 0x06 }, // 'P'
  { 0x3E, 0x41, 0x51, 0x21, 0x5E }, // 'Q'
  { 0x7F, 0x09, 0x19, 0x29, 0x46 }, // 'R'
  { 0x46, 0x49, 0x49, 0x49, 0x31 }, // 'S'
  { 0x01, 0x01, 0x7F, 0x01, 0x01 }, // 'T'
  { 0x3F, 0x40, 0x40, 0x40, 0x3F }, // 'U'
  { 0x1F, 0x20, 0x40, 0x20, 0x1F }, // 'V'
  { 0x3F, 0x40, 0x38, 0x40, 0x3F }, // 'W'
  { 0x63, 0x14, 0x08, 0x14, 0x63 }, // 'X'
  { 0x07, 0x08, 0x70, 0x08, 0x07 }, // 'Y'
  { 0x61, 0x51, 0x49, 0x45, 0x43 }, // 'Z'
  { 0x00, 0x7F, 0x41, 0x41, 0x00 }, // '['
  { 0x02, 0x04, 0x08, 0x10, 0x20 }, // '\'
  { 0x00, 0x41, 0x41, 0x7F, 0x00 }, // ']'
  { 0x04, 0x02, 0x01, 0x02, 0x04 }, // '^'
  { 0x40, 0x40, 0x40, 0x40, 0x40 }, // '_'
  { 0x00, 0x01, 0x02, 0x04, 0x00 }, // '`'
  { 0x20, 0x54, 0x54, 0x54, 0x78 }, // 'a'
  { 0x7F, 0x48, 0x44, 0x44, 0x38 }, // 'b'
  { 0x38, 0x44, 0x44, 0x44, 0x20 }, // 'c'
  { 0x38, 0x44, 0x44, 0x48, 0x7F }, // 'd'
  { 0x38, 0x54, 0x54, 0x54, 0x18 }, // 'e'
  { 0x08, 0x7E, 0x09, 0x01, 0x02 }, // 'f'
  { 0x0C, 0x52, 0x52, 0x52, 0x3E }, // 'g'
  { 0x7F, 0x08, 0x04, 0x04, 0x78 }, // 'h'
  { 0x00, 0x44, 0x7D, 0x40, 0x00 }, // 'i'
  { 0x20, 0x40, 0x44, 0x3D, 0x00 }, // 'j'
  { 0x7F, 0x10, 0x28, 0x44, 0x00 }, // 'k'
  { 0x00, 0x41, 0x7F, 0x40, 0x00 }, // 'l'
  { 0x7C, 0x04, 0x18, 0x04, 0x78 }, // 'm'
  { 0x7C, 0x08, 0x04, 0x04, 0x78 }, // 'n'
  { 0x38, 0x44, 0x44, 0x44, 0x38 }, // 'o'
  { 0x7C, 0x14, 0x14, 0x14, 0x08 }, // 'p'
  { 0x08, 0x14, 0x14, 0x18, 0x7C }, // 'q'
  { 0x7C, 0x08, 0x04, 0x04, 0x08 }, // 'r'
  { 0x48, 0x54, 0x54, 0x54, 0x20 }, // 's'
  { 0x04, 0x3F, 0x44, 0x40, 0x20 }, // 't'
  { 0x3C, 0x40, 0x40, 0x20, 0x7C }, // 'u'
  { 0x1C, 0x20, 0x40, 0x20, 0x1C }, // 'v'
  { 0x3C, 0x40, 0x30, 0x40, 0x3C }, // 'w'
  { 0x44, 0x28, 0x10, 0x28, 0x44 }, // 'x'
  { 0x0C, 0x50, 0x50, 0x50, 0x3C }, // 'y'
  { 0x44, 0x64, 0x54, 0x4C, 0x44 }, // 'z'
  { 0x00, 0x08, 0x36, 0x41, 0x00 }, // '{'
  { 0x00, 0x00, 0x7F, 0x00, 0x00 }, // '|'
  { 0x00, 0x41, 0x36, 0x08, 0x00 }, // '}'
  { 0x10, 0x08, 0x08, 0x10, 0x08 }, // '~'
};

// ── Internal SPI helpers ──────────────────────────────────────────────────────
// DC is normally HIGH (data); briefly pulled LOW for commands.
// CS is held LOW for the duration of an operation.

static void lcdBegin() {
    SPI.beginTransaction(SPISettings(40000000, MSBFIRST, SPI_MODE0));
    digitalWrite(GYRO_LCD_CS, LOW);
}

static void lcdEnd() {
    digitalWrite(GYRO_LCD_CS, HIGH);
    SPI.endTransaction();
}

static void lcdCmd(uint8_t cmd) {
    digitalWrite(GYRO_LCD_DC, LOW);
    SPI.write(cmd);
    digitalWrite(GYRO_LCD_DC, HIGH);
}

static void lcdByte(uint8_t data) {
    SPI.write(data);
}

// Set pixel address window for a subsequent memory-write burst.
// Must be called inside lcdBegin/lcdEnd.
static void lcdSetWindow(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    lcdCmd(0x2A);           // CASET — column address
    lcdByte(x0 >> 8); lcdByte(x0 & 0xFF);
    lcdByte(x1 >> 8); lcdByte(x1 & 0xFF);
    lcdCmd(0x2B);           // RASET — row address
    lcdByte(y0 >> 8); lcdByte(y0 & 0xFF);
    lcdByte(y1 >> 8); lcdByte(y1 & 0xFF);
    lcdCmd(0x2C);           // RAMWR — begin memory write
}

// ── GC9A01 initialisation sequence ───────────────────────────────────────────

// Helper: send a command followed by len data bytes.
static void lcdCmdData(uint8_t cmd, const uint8_t* data, uint8_t len) {
    lcdCmd(cmd);
    for (uint8_t i = 0; i < len; i++) lcdByte(data[i]);
}

void gyroDisplayInit() {
    pinMode(GYRO_LCD_DC,  OUTPUT);
    pinMode(GYRO_LCD_CS,  OUTPUT);
    pinMode(GYRO_LCD_RST, OUTPUT);
    pinMode(GYRO_LCD_BL,  OUTPUT);
    digitalWrite(GYRO_LCD_CS,  HIGH);
    digitalWrite(GYRO_LCD_DC,  HIGH);
    digitalWrite(GYRO_LCD_BL,  LOW);   // backlight off during init

    SPI.begin(GYRO_LCD_SCLK, -1, GYRO_LCD_MOSI, -1);

    // Hardware reset
    digitalWrite(GYRO_LCD_RST, HIGH); delay(10);
    digitalWrite(GYRO_LCD_RST, LOW);  delay(10);
    digitalWrite(GYRO_LCD_RST, HIGH); delay(120);

    lcdBegin();

    // --- GC9A01 power / vendor init sequence ---
    lcdCmd(0xEF);
    lcdCmdData(0xEB, (const uint8_t[]){0x14}, 1);
    lcdCmd(0xFE);
    lcdCmd(0xEF);
    lcdCmdData(0xEB, (const uint8_t[]){0x14}, 1);
    lcdCmdData(0x84, (const uint8_t[]){0x40}, 1);
    lcdCmdData(0x85, (const uint8_t[]){0xFF}, 1);
    lcdCmdData(0x86, (const uint8_t[]){0xFF}, 1);
    lcdCmdData(0x87, (const uint8_t[]){0xFF}, 1);
    lcdCmdData(0x88, (const uint8_t[]){0x0A}, 1);
    lcdCmdData(0x89, (const uint8_t[]){0x21}, 1);
    lcdCmdData(0x8A, (const uint8_t[]){0x00}, 1);
    lcdCmdData(0x8B, (const uint8_t[]){0x80}, 1);
    lcdCmdData(0x8C, (const uint8_t[]){0x01}, 1);
    lcdCmdData(0x8D, (const uint8_t[]){0x01}, 1);
    lcdCmdData(0x8E, (const uint8_t[]){0xFF}, 1);
    lcdCmdData(0x8F, (const uint8_t[]){0xFF}, 1);
    lcdCmdData(0xB6, (const uint8_t[]){0x00, 0x20}, 2);  // Display Function Control

    // --- Standard MIPI commands ---
    lcdCmdData(0x36, (const uint8_t[]){GYRO_LCD_MADCTL}, 1); // MADCTL: orientation
    lcdCmdData(0x3A, (const uint8_t[]){0x05}, 1);             // COLMOD: RGB565

    // --- Timing / power-supply tune ---
    lcdCmdData(0x90, (const uint8_t[]){0x08, 0x08, 0x08, 0x08}, 4);
    lcdCmdData(0xBD, (const uint8_t[]){0x06}, 1);
    lcdCmdData(0xBC, (const uint8_t[]){0x00}, 1);
    lcdCmdData(0xFF, (const uint8_t[]){0x60, 0x01, 0x04}, 3);
    lcdCmdData(0xC3, (const uint8_t[]){0x13}, 1);
    lcdCmdData(0xC4, (const uint8_t[]){0x13}, 1);
    lcdCmdData(0xC9, (const uint8_t[]){0x22}, 1);
    lcdCmdData(0xBE, (const uint8_t[]){0x11}, 1);
    lcdCmdData(0xE1, (const uint8_t[]){0x10, 0x0E}, 2);
    lcdCmdData(0xDF, (const uint8_t[]){0x21, 0x0C, 0x02}, 3);

    // --- Gamma correction ---
    lcdCmdData(0xF0, (const uint8_t[]){0x45, 0x09, 0x08, 0x08, 0x26, 0x2A}, 6);
    lcdCmdData(0xF1, (const uint8_t[]){0x43, 0x70, 0x72, 0x36, 0x37, 0x6F}, 6);
    lcdCmdData(0xF2, (const uint8_t[]){0x45, 0x09, 0x08, 0x08, 0x26, 0x2A}, 6);
    lcdCmdData(0xF3, (const uint8_t[]){0x43, 0x70, 0x72, 0x36, 0x37, 0x6F}, 6);
    lcdCmdData(0xED, (const uint8_t[]){0x1B, 0x0B}, 2);
    lcdCmdData(0xAE, (const uint8_t[]){0x77}, 1);
    lcdCmdData(0xCD, (const uint8_t[]){0x63}, 1);
    lcdCmdData(0x70, (const uint8_t[]){0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03}, 9);
    lcdCmdData(0xE8, (const uint8_t[]){0x34}, 1);
    lcdCmdData(0x62, (const uint8_t[]){0x18, 0x0D, 0x71, 0xED, 0x70, 0x70,
                                       0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70}, 12);
    lcdCmdData(0x63, (const uint8_t[]){0x18, 0x11, 0x71, 0xF1, 0x70, 0x70,
                                       0x18, 0x13, 0x71, 0xF3, 0x70, 0x70}, 12);
    lcdCmdData(0x64, (const uint8_t[]){0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07}, 7);
    lcdCmdData(0x66, (const uint8_t[]){0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00}, 10);
    lcdCmdData(0x67, (const uint8_t[]){0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98}, 10);
    lcdCmdData(0x74, (const uint8_t[]){0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00}, 7);
    lcdCmdData(0x98, (const uint8_t[]){0x3E, 0x07}, 2);

    lcdCmd(0x35);   // Tearing Effect Line ON
    lcdCmd(0x21);   // Display Inversion ON (needed for correct GC9A01 colours)

    lcdEnd();

    // Sleep Out — must be issued outside the previous transaction per MIPI spec
    lcdBegin(); lcdCmd(0x11); lcdEnd();
    delay(120);

    // Clear framebuffer before turning on display (avoids visual garbage)
    gyroClearScreen(GC_BLACK);

    lcdBegin(); lcdCmd(0x29); lcdEnd();  // Display ON
    delay(20);

    digitalWrite(GYRO_LCD_BL, HIGH);    // backlight on
}

void gyroSetBacklight(bool on) {
    digitalWrite(GYRO_LCD_BL, on ? HIGH : LOW);
}

// ── Bulk pixel fill helpers ───────────────────────────────────────────────────
// 128-pixel buffer (256 bytes) reused for any solid-colour fill.
static uint8_t s_pixBuf[256];
static uint16_t s_pixBufColour = 0;  // last colour loaded into s_pixBuf
static bool s_pixBufValid = false;

static void fillPixelBuf(uint16_t colour) {
    if (s_pixBufValid && colour == s_pixBufColour) return;
    s_pixBufColour = colour;
    s_pixBufValid  = true;
    uint8_t hi = colour >> 8, lo = colour & 0xFF;
    for (int i = 0; i < 256; i += 2) { s_pixBuf[i] = hi; s_pixBuf[i + 1] = lo; }
}

// ── Drawing primitives ────────────────────────────────────────────────────────

void gyroClearScreen(uint16_t colour) {
    gyroFillRect(0, 0, GYRO_LCD_W, GYRO_LCD_H, colour);
}

void gyroFillRect(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour) {
    if (w <= 0 || h <= 0) return;
    if (x >= GYRO_LCD_W || y >= GYRO_LCD_H) return;
    // Clip to screen
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > GYRO_LCD_W) w = GYRO_LCD_W - x;
    if (y + h > GYRO_LCD_H) h = GYRO_LCD_H - y;
    if (w <= 0 || h <= 0) return;

    fillPixelBuf(colour);
    lcdBegin();
    lcdSetWindow(x, y, x + w - 1, y + h - 1);
    uint32_t total = (uint32_t)w * h;
    while (total > 0) {
        uint32_t chunk = (total < 128) ? total : 128;
        SPI.writeBytes(s_pixBuf, (uint32_t)chunk * 2);
        total -= chunk;
    }
    lcdEnd();
}

void gyroDrawPixel(int16_t x, int16_t y, uint16_t colour) {
    if (x < 0 || x >= GYRO_LCD_W || y < 0 || y >= GYRO_LCD_H) return;
    lcdBegin();
    lcdSetWindow(x, y, x, y);
    lcdByte(colour >> 8); lcdByte(colour & 0xFF);
    lcdEnd();
}

// Filled circle via horizontal scan-lines (Bresenham).
void gyroFillCircle(int16_t cx, int16_t cy, int16_t r, uint16_t colour) {
    if (r <= 0) return;
    for (int16_t dy = -r; dy <= r; dy++) {
        int16_t dx = (int16_t)sqrtf((float)(r * r - dy * dy));
        gyroFillRect(cx - dx, cy + dy, 2 * dx + 1, 1, colour);
    }
}

// Circle outline (Bresenham midpoint algorithm).
void gyroDrawCircle(int16_t cx, int16_t cy, int16_t r, uint16_t colour) {
    int16_t x = 0, y = r, d = 1 - r;
    while (x <= y) {
        gyroDrawPixel(cx + x, cy + y, colour);
        gyroDrawPixel(cx - x, cy + y, colour);
        gyroDrawPixel(cx + x, cy - y, colour);
        gyroDrawPixel(cx - x, cy - y, colour);
        gyroDrawPixel(cx + y, cy + x, colour);
        gyroDrawPixel(cx - y, cy + x, colour);
        gyroDrawPixel(cx + y, cy - x, colour);
        gyroDrawPixel(cx - y, cy - x, colour);
        if (d < 0) { d += 2 * x + 3; }
        else       { d += 2 * (x - y) + 5; y--; }
        x++;
    }
}

// Arc segment using polar coordinates.
// startDeg..endDeg: 0° = right (3 o'clock), increases clockwise.
// Draws individual pixels; acceptable for status-ring use (≤360 iterations × thickness).
void gyroDrawArcSegment(int16_t cx, int16_t cy, int16_t r, int16_t thickness,
                        int16_t startDeg, int16_t endDeg, uint16_t colour) {
    if (thickness <= 0 || r <= 0) return;
    for (int16_t deg = startDeg; deg <= endDeg; deg++) {
        float rad = (float)deg * (float)M_PI / 180.0f;
        float cosv = cosf(rad), sinv = sinf(rad);
        for (int16_t t = 0; t < thickness; t++) {
            int16_t px = cx + (int16_t)((r - t) * cosv);
            int16_t py = cy + (int16_t)((r - t) * sinv);
            gyroDrawPixel(px, py, colour);
        }
    }
}

// ── Text rendering ────────────────────────────────────────────────────────────

int16_t gyroDrawChar(int16_t x, int16_t y, char c, uint8_t size, uint16_t colour) {
    if (c < 0x20 || c > 0x7E) return size * 6; // skip unprintable
    const uint8_t* glyph = s_font5x7[(uint8_t)c - 0x20];
    for (uint8_t col = 0; col < 5; col++) {
        uint8_t bits = glyph[col];
        for (uint8_t row = 0; row < 7; row++) {
            if (bits & (1 << row)) {
                if (size == 1) {
                    gyroDrawPixel(x + col, y + row, colour);
                } else {
                    gyroFillRect(x + col * size, y + row * size, size, size, colour);
                }
            }
        }
    }
    return 6 * size; // 5 px glyph + 1 px gap
}

void gyroDrawText(int16_t x, int16_t y, const char* str, uint8_t size, uint16_t colour) {
    int16_t cx = x;
    while (*str) {
        cx += gyroDrawChar(cx, y, *str, size, colour);
        str++;
    }
}

// Blit RGB565 image with black-as-transparent
void gyroDrawImage(int16_t x, int16_t y, int16_t w, int16_t h,
                   const uint16_t* data) {
    for (int16_t row = 0; row < h; row++) {
        for (int16_t col = 0; col < w; col++) {
            uint16_t px = data[row * w + col];
            if (px != 0x0000) {  // skip black = transparent
                gyroDrawPixel(x + col, y + row, px);
            }
        }
    }
}

#endif  // BOARD_GYRO
