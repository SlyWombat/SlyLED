/*
 * GigaLED.h — Onboard RGB LED driver for Giga R1 WiFi acting as a child.
 *
 * Provides a CRGB-compatible interface so ChildLED.cpp can render to leds[0]
 * and showSafe() outputs via GPIO software PWM on the active-low RGB pins.
 */

#ifndef GIGALED_H
#define GIGALED_H

#ifdef BOARD_GIGA_CHILD

#include <Arduino.h>

// ── Minimal CRGB struct (compatible with ChildLED.cpp expectations) ──────────

struct CRGB {
  uint8_t r, g, b;
  CRGB() : r(0), g(0), b(0) {}
  CRGB(uint8_t rr, uint8_t gg, uint8_t bb) : r(rr), g(gg), b(bb) {}
  void nscale8(uint8_t scale) {
    r = (uint16_t)r * scale / 256;
    g = (uint16_t)g * scale / 256;
    b = (uint16_t)b * scale / 256;
  }
  static const CRGB Black;
  static const CRGB White;
};

// ── Minimal CHSV and conversion ──────────────────────────────────────────────

struct CHSV {
  uint8_t h, s, v;
  CHSV(uint8_t hh, uint8_t ss, uint8_t vv) : h(hh), s(ss), v(vv) {}
};

void hsv2rgb_rainbow(const CHSV& hsv, CRGB& rgb);

// ── LED array and helpers ────────────────────────────────────────────────────

extern CRGB leds[NUM_LEDS];

inline void fill_solid(CRGB* arr, int count, CRGB color) {
  for (int i = 0; i < count; i++) arr[i] = color;
}

// FastLED-compatible random helpers
uint8_t random8();
uint8_t random8(uint8_t lim);
uint8_t random8(uint8_t lo, uint8_t hi);
uint8_t qadd8(uint8_t a, uint8_t b);

// ── showSafe — output leds[0] to the onboard RGB pins via software PWM ───────

void gigaLedInit();
void showSafe();
void clearAndShow();

#endif  // BOARD_GIGA_CHILD
#endif  // GIGALED_H
