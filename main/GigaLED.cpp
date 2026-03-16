/*
 * GigaLED.cpp — Onboard RGB LED driver for Giga R1 WiFi child.
 *
 * The Giga's onboard LED uses active-low GPIO pins (LEDR, LEDG, LEDB).
 * analogWrite crashes Mbed OS, so we use software PWM via
 * mbed::PwmOut for smooth colour control.
 */

#include "BoardConfig.h"

#ifdef BOARD_GIGA_CHILD

#include "GigaLED.h"
#include <mbed.h>

// ── Static instances ─────────────────────────────────────────────────────────

const CRGB CRGB::Black = CRGB(0, 0, 0);
const CRGB CRGB::White = CRGB(255, 255, 255);

CRGB leds[NUM_LEDS];

static mbed::PwmOut* _pwmR = nullptr;
static mbed::PwmOut* _pwmG = nullptr;
static mbed::PwmOut* _pwmB = nullptr;

static uint8_t _brightness = 255;

// ── HSV to RGB (rainbow wheel) ───────────────────────────────────────────────

void hsv2rgb_rainbow(const CHSV& hsv, CRGB& rgb) {
  uint8_t h = hsv.h, s = hsv.s, v = hsv.v;
  uint8_t region = h / 43;
  uint8_t remainder = (h - (region * 43)) * 6;
  uint8_t p = (uint16_t)v * (255 - s) / 255;
  uint8_t q = (uint16_t)v * (255 - ((uint16_t)s * remainder / 255)) / 255;
  uint8_t t = (uint16_t)v * (255 - ((uint16_t)s * (255 - remainder) / 255)) / 255;
  switch (region) {
    case 0:  rgb = CRGB(v, t, p); break;
    case 1:  rgb = CRGB(q, v, p); break;
    case 2:  rgb = CRGB(p, v, t); break;
    case 3:  rgb = CRGB(p, q, v); break;
    case 4:  rgb = CRGB(t, p, v); break;
    default: rgb = CRGB(v, p, q); break;
  }
}

// ── Random helpers ───────────────────────────────────────────────────────────

uint8_t random8() { return (uint8_t)(rand() & 0xFF); }
uint8_t random8(uint8_t lim) { return lim ? random8() % lim : 0; }
uint8_t random8(uint8_t lo, uint8_t hi) { return lo + random8(hi - lo); }
uint8_t qadd8(uint8_t a, uint8_t b) { uint16_t t = a + b; return t > 255 ? 255 : (uint8_t)t; }

// ── Init ─────────────────────────────────────────────────────────────────────

void gigaLedInit() {
  // PwmOut on the active-low RGB pins (1.0 = off, 0.0 = full on)
  _pwmR = new mbed::PwmOut(digitalPinToPinName(PIN_LEDR));
  _pwmG = new mbed::PwmOut(digitalPinToPinName(PIN_LEDG));
  _pwmB = new mbed::PwmOut(digitalPinToPinName(PIN_LEDB));
  _pwmR->period_us(500);  // 2kHz PWM
  _pwmG->period_us(500);
  _pwmB->period_us(500);
  clearAndShow();
}

// ── Show — write leds[0] to hardware ─────────────────────────────────────────

void showSafe() {
  CRGB c = leds[0];
  // Apply brightness
  uint8_t r = (uint16_t)c.r * _brightness / 255;
  uint8_t g = (uint16_t)c.g * _brightness / 255;
  uint8_t b = (uint16_t)c.b * _brightness / 255;
  // Active-low: 1.0 = off, 0.0 = full brightness
  if (_pwmR) _pwmR->write(1.0f - r / 255.0f);
  if (_pwmG) _pwmG->write(1.0f - g / 255.0f);
  if (_pwmB) _pwmB->write(1.0f - b / 255.0f);
}

void clearAndShow() {
  leds[0] = CRGB::Black;
  showSafe();
}

// ── Brightness (called from ChildLED.cpp via childBrightness) ────────────────

namespace GigaLEDInternal {
  void setBrightness(uint8_t b) { _brightness = b; }
}

#endif  // BOARD_GIGA_CHILD
