/*
 * ChildLED.cpp — LED rendering for ESP32 (FreeRTOS task) and D1 Mini
 *                (non-blocking updateLED).
 *
 * Supports 9 action types: Blackout, Solid, Fade, Breathe, Chase,
 * Rainbow, Fire, Comet, Twinkle.
 */

#include <Arduino.h>
#include "BoardConfig.h"

#ifdef BOARD_CHILD

#include "Protocol.h"
#include "NetUtils.h"
#include "Child.h"
#include "ChildLED.h"

// ── Named constants (Issue #28 — replace magic numbers) ──────────────────

// Chase defaults
constexpr uint8_t  CHASE_MIN_SPACING       = 2;
constexpr uint8_t  CHASE_DEFAULT_SPACING    = 3;

// Fire effect
constexpr uint8_t  FIRE_COOL_BASE_ADD      = 2;    // added to cooling divisor
constexpr uint8_t  FIRE_SPARK_ZONE_MAX     = 7;    // max spark spawn pixel
constexpr uint8_t  FIRE_SPARK_TEMP_MIN     = 160;  // min spark temperature
constexpr uint8_t  FIRE_SPARK_TEMP_MAX     = 255;  // max spark temperature (exclusive)
constexpr uint8_t  FIRE_HUE_ORANGE         = 85;   // heat threshold: red → orange
constexpr uint8_t  FIRE_HUE_YELLOW         = 170;  // heat threshold: orange → yellow
constexpr uint8_t  FIRE_HEAT_SCALE         = 3;    // multiplier for heat-to-RGB mapping
constexpr uint16_t FIRE_COOL_SCALE         = 10;   // numerator for cooling calculation

// Comet effect
constexpr uint8_t  COMET_DEFAULT_TAIL      = 5;
constexpr uint8_t  COMET_DEFAULT_FADE      = 200;  // nscale8 fade when no decay specified
constexpr uint8_t  COMET_DECAY_SCALE       = 100;  // divisor for decay percentage

// Twinkle effect
constexpr uint8_t  TWINKLE_DEFAULT_FADE    = 240;  // nscale8 fade when no fadeSpeed specified
constexpr uint8_t  TWINKLE_DEFAULT_DENSITY = 3;
constexpr uint8_t  TWINKLE_SPAWN_CHANCE    = 40;   // random8() threshold for spawning
constexpr uint8_t  TWINKLE_BRI_MIN         = 128;  // minimum brightness for new twinkle
constexpr uint8_t  TWINKLE_BRI_MAX         = 255;  // max brightness (exclusive)

// Strobe defaults
constexpr uint16_t STROBE_DEFAULT_PERIOD   = 200;
constexpr uint8_t  STROBE_DEFAULT_DUTY     = 50;
constexpr uint8_t  STROBE_MAX_DUTY         = 100;

// Wipe sequence
constexpr uint16_t WIPE_DEFAULT_SPEED      = 50;
constexpr uint8_t  WIPE_CYCLE_MULTIPLIER   = 2;    // fill + unfill = 2x range length

// Scanner
constexpr uint16_t SCANNER_DEFAULT_SPEED   = 30;
constexpr uint8_t  SCANNER_DEFAULT_BAR     = 3;
constexpr uint8_t  SCANNER_TRAIL_FADE      = 200;  // nscale8 trail fade

// Sparkle
constexpr uint8_t  SPARKLE_DEFAULT_DENSITY = 2;
constexpr uint8_t  SPARKLE_SPAWN_CHANCE    = 60;   // random8() threshold

// Boot animation
constexpr uint16_t BOOT_WAIT_MS           = 3000;
constexpr uint16_t BOOT_SWEEP_MS          = 1500;  // total rainbow sweep duration
constexpr uint8_t  BOOT_MIN_DELAY         = 2;     // minimum per-pixel delay
constexpr uint16_t BOOT_HOLD_MS           = 500;   // hold after sweep
constexpr uint16_t BOOT_FLASH_COUNT       = 150;   // LED count for no-string flash
constexpr uint16_t BOOT_FLASH_MS          = 800;
constexpr uint8_t  BOOT_GIGA_HUE_STEP     = 4;     // hue increment per frame
constexpr uint8_t  BOOT_GIGA_FRAME_MS     = 6;     // ms per frame
constexpr uint16_t BOOT_GIGA_HOLD_MS      = 200;

// Sync blink
constexpr uint16_t SYNC_BLINK_MS          = 200;

// Frame delays (ms per action type)
constexpr uint8_t FRAME_DELAY_SOLID       = 50;
constexpr uint8_t FRAME_DELAY_FADE        = 20;
constexpr uint8_t FRAME_DELAY_BREATHE     = 15;
constexpr uint8_t FRAME_DELAY_CHASE       = 10;
constexpr uint8_t FRAME_DELAY_RAINBOW     = 10;
constexpr uint8_t FRAME_DELAY_FIRE        = 15;
constexpr uint8_t FRAME_DELAY_COMET       = 10;
constexpr uint8_t FRAME_DELAY_TWINKLE     = 10;
constexpr uint8_t FRAME_DELAY_STROBE      = 5;
constexpr uint8_t FRAME_DELAY_WIPE_SEQ    = 10;
constexpr uint8_t FRAME_DELAY_SCANNER     = 10;
constexpr uint8_t FRAME_DELAY_SPARKLE     = 15;
constexpr uint8_t FRAME_DELAY_GRADIENT    = 100;
constexpr uint8_t FRAME_DELAY_DEFAULT     = 10;
constexpr uint8_t FRAME_DELAY_OFF         = 10;

// Boot animation wait loop
constexpr uint8_t BOOT_POLL_MS            = 50;

// Palette constants (Heat palette thresholds reuse FIRE_HUE_*)
constexpr uint8_t PAL_HEAT_THRESH1        = 85;
constexpr uint8_t PAL_HEAT_THRESH2        = 170;
constexpr uint8_t PAL_HEAT_SCALE          = 3;

// NTP epoch threshold: January 1, 2020 — if currentEpoch() < this, NTP failed
constexpr uint32_t NTP_EPOCH_2020         = 1577836800UL;

// ── Show + clear helpers ──────────────────────────────────────────────────

#ifdef BOARD_FASTLED
static void showSafe() {
  FastLED.setBrightness(childBrightness);
#ifdef BOARD_D1MINI
  // D1 Mini: bit-banged output needs interrupts disabled to prevent
  // WiFi IRQs from corrupting the 800kHz WS2812B timing signal.
  noInterrupts();
  FastLED.show();
  interrupts();
#else
  // ESP32: RMT peripheral handles timing in hardware — no need to
  // disable interrupts (doing so triggers WDT with WiFi active).
  FastLED.show();
#endif
}

static inline void clearAndShow() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  showSafe();
  // Double-send to ensure the strip latches cleanly
  delayMicroseconds(300);
  showSafe();
}
#endif
// BOARD_GIGA_CHILD: showSafe() and clearAndShow() defined in GigaLED.cpp

// ── Sin lookup table (256 entries, 0-255 representing 0-2π) ────────────────

static const uint8_t PROGMEM _sin8[] = {
  128,131,134,137,140,143,146,149,152,155,158,162,165,167,170,173,
  176,179,182,185,188,190,193,196,198,201,203,206,208,211,213,215,
  218,220,222,224,226,228,230,232,234,235,237,239,240,242,243,244,
  246,247,248,249,250,251,252,253,253,254,254,255,255,255,255,255,
  255,255,255,255,255,254,254,253,253,252,251,250,249,248,247,246,
  244,243,242,240,239,237,235,234,232,230,228,226,224,222,220,218,
  215,213,211,208,206,203,201,198,196,193,190,188,185,182,179,176,
  173,170,167,165,162,158,155,152,149,146,143,140,137,134,131,128,
  124,121,118,115,112,109,106,103,100, 97, 94, 90, 87, 85, 82, 79,
   76, 73, 70, 67, 64, 62, 59, 56, 54, 51, 49, 46, 44, 41, 39, 37,
   34, 32, 30, 28, 26, 24, 22, 20, 18, 17, 15, 13, 12, 10,  9,  8,
    6,  5,  4,  3,  2,  1,  1,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,  0,  1,  1,  2,  2,  3,  4,  5,  6,  7,  8,  9,
   11, 12, 13, 15, 16, 18, 20, 21, 23, 25, 27, 29, 31, 33, 35, 37,
   40, 42, 44, 47, 49, 52, 54, 57, 59, 62, 65, 67, 70, 73, 76, 79,
   82, 85, 88, 90, 93, 97,100,103,106,109,112,115,118,121,124,128
};

static uint8_t sinLut(uint8_t x) { return pgm_read_byte(&_sin8[x]); }

// ── HSV to RGB helper ─────────────────────────────────────────────────────

static CRGB hsvToRgb(uint8_t h, uint8_t s, uint8_t v) {
  CHSV hsv(h, s, v);
  CRGB rgb;
  hsv2rgb_rainbow(hsv, rgb);
  return rgb;
}

// ── Palette colours for Rainbow mode ──────────────────────────────────────

static CRGB paletteColor(uint8_t palId, uint8_t idx) {
  // idx = 0-255 position in palette
  switch (palId) {
    default:
    case PAL_CLASSIC: return hsvToRgb(idx, 255, 255);
    case PAL_OCEAN:   return hsvToRgb((uint8_t)(idx/2 + 120), 200, (uint8_t)(160 + idx/3));
    case PAL_LAVA:    return hsvToRgb((uint8_t)(idx/4), 255, (uint8_t)(200 + sinLut(idx)/5));
    case PAL_FOREST:  return hsvToRgb((uint8_t)(idx/3 + 60), 220, (uint8_t)(100 + sinLut(idx)/2));
    case PAL_PARTY:   return hsvToRgb((uint8_t)(idx*3), 255, 255);
    case PAL_HEAT: {
      uint8_t t = idx;
      if (t < PAL_HEAT_THRESH1) return CRGB(t*PAL_HEAT_SCALE, 0, 0);
      if (t < PAL_HEAT_THRESH2) return CRGB(255, (uint8_t)((t-PAL_HEAT_THRESH1)*PAL_HEAT_SCALE), 0);
      return CRGB(255, 255, (uint8_t)((t-PAL_HEAT_THRESH2)*PAL_HEAT_SCALE));
    }
    case PAL_COOL:    return hsvToRgb((uint8_t)(idx/2 + 140), 180, (uint8_t)(180 + idx/4));
    case PAL_PASTEL:  return hsvToRgb(idx, 100, 255);
  }
}

// ── Render helpers (operate on a range st..en within leds[]) ──────────────

static void renderSolid(uint8_t r, uint8_t g, uint8_t b,
                         uint16_t st, uint16_t en) {
  for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(r, g, b);
}

static void renderFade(uint8_t r1, uint8_t g1, uint8_t b1,
                        uint8_t r2, uint8_t g2, uint8_t b2,
                        uint16_t speedMs, unsigned long elapsedMs,
                        uint16_t st, uint16_t en) {
  if (speedMs == 0) speedMs = 1;
  // Ping-pong: fade out to colour2 then back to colour1 (no abrupt wrap)
  uint32_t cycle = (uint32_t)speedMs * 2;
  uint32_t t = elapsedMs % cycle;
  uint8_t frac;
  if (t < speedMs)
    frac = (uint8_t)((uint32_t)t * 255 / speedMs);         // 0→255
  else
    frac = (uint8_t)((uint32_t)(cycle - t) * 255 / speedMs); // 255→0
  uint8_t r = (uint8_t)((r1 * (255 - frac) + r2 * frac) / 255);
  uint8_t g = (uint8_t)((g1 * (255 - frac) + g2 * frac) / 255);
  uint8_t b = (uint8_t)((b1 * (255 - frac) + b2 * frac) / 255);
  for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(r, g, b);
}

static void renderBreathe(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t periodMs, uint8_t minBriPct,
                           unsigned long elapsedMs,
                           uint16_t st, uint16_t en) {
  if (periodMs == 0) periodMs = 1;
  uint8_t phase = (uint8_t)((elapsedMs % (uint32_t)periodMs) * 256 / periodMs);
  uint8_t sVal = sinLut(phase);  // 0-255
  uint8_t minB = (uint8_t)((uint16_t)minBriPct * 255 / 100);
  uint8_t bri = minB + (uint8_t)((uint16_t)(255 - minB) * sVal / 255);
  uint8_t rr = (uint8_t)((uint16_t)r * bri / 255);
  uint8_t gg = (uint8_t)((uint16_t)g * bri / 255);
  uint8_t bb = (uint8_t)((uint16_t)b * bri / 255);
  for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(rr, gg, bb);
}

static void renderChase(uint8_t r, uint8_t g, uint8_t b,
                         uint16_t speedMs, uint8_t spacing, uint8_t dir,
                         unsigned long elapsedMs,
                         uint16_t st, uint16_t en) {
  if (speedMs == 0) speedMs = 1;
  if (spacing < CHASE_MIN_SPACING) spacing = CHASE_DEFAULT_SPACING;
  uint16_t rangeLen = en - st + 1;
  uint16_t offset = (uint16_t)((elapsedMs / speedMs) % spacing);
  for (uint16_t i = 0; i < rangeLen; i++) {
    uint16_t idx = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - i) : i;
    leds[st + idx] = ((i + offset) % spacing == 0) ? CRGB(r, g, b) : CRGB::Black;
  }
}

static void renderRainbow(uint16_t speedMs, uint8_t palId, uint8_t dir,
                           unsigned long elapsedMs,
                           uint16_t st, uint16_t en) {
  if (speedMs == 0) speedMs = 1;
  uint16_t rangeLen = en - st + 1;
  uint8_t timeOff = (uint8_t)(elapsedMs / speedMs);
  for (uint16_t i = 0; i < rangeLen; i++) {
    uint16_t idx = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - i) : i;
    uint8_t hue = (uint8_t)((uint32_t)i * 255 / rangeLen + timeOff);
    leds[st + idx] = paletteColor(palId, hue);
  }
}

static void renderFire(uint8_t r, uint8_t g, uint8_t b,
                        uint16_t speedMs, uint8_t cooling, uint8_t sparking,
                        unsigned long elapsedMs,
                        uint16_t st, uint16_t en) {
  static uint8_t heat[MAX_LEDS];
  uint16_t rangeLen = en - st + 1;
  // Cool down
  for (uint16_t i = 0; i < rangeLen; i++) {
    uint8_t cool = random8(0, ((uint16_t)cooling * FIRE_COOL_SCALE / rangeLen) + FIRE_COOL_BASE_ADD);
    heat[i] = (heat[i] > cool) ? heat[i] - cool : 0;
  }
  // Heat rises
  for (uint16_t k = rangeLen - 1; k >= 2; k--) {
    heat[k] = ((uint16_t)heat[k-1] + heat[k-2] + heat[k-2]) / 3;
  }
  // Sparks
  if (random8() < sparking) {
    uint16_t y = random8(min(FIRE_SPARK_ZONE_MAX, (uint8_t)rangeLen));
    heat[y] = qadd8(heat[y], random8(FIRE_SPARK_TEMP_MIN, FIRE_SPARK_TEMP_MAX));
  }
  // Map heat to colour
  for (uint16_t j = 0; j < rangeLen; j++) {
    uint8_t t = heat[j];
    uint8_t rr, gg, bb;
    if (t < FIRE_HUE_ORANGE)       { rr = (uint8_t)((uint16_t)r*t*FIRE_HEAT_SCALE/255); gg = 0; bb = 0; }
    else if (t < FIRE_HUE_YELLOW)  { rr = r; gg = (uint8_t)((uint16_t)g*(t-FIRE_HUE_ORANGE)*FIRE_HEAT_SCALE/255); bb = 0; }
    else                           { rr = r; gg = g; bb = (uint8_t)((uint16_t)b*(t-FIRE_HUE_YELLOW)*FIRE_HEAT_SCALE/255); }
    leds[st + j] = CRGB(rr, gg, bb);
  }
}

static void renderComet(uint8_t r, uint8_t g, uint8_t b,
                         uint16_t speedMs, uint8_t tailLen, uint8_t dir,
                         uint8_t decayPct, unsigned long elapsedMs,
                         uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  if (speedMs == 0) speedMs = 1;
  if (tailLen < 1) tailLen = COMET_DEFAULT_TAIL;
  uint16_t headPos = (uint16_t)((elapsedMs / speedMs) % (rangeLen + tailLen));
  // Fade all
  uint8_t fade = decayPct ? (uint8_t)(256 - decayPct * 256 / COMET_DECAY_SCALE) : COMET_DEFAULT_FADE;
  for (uint16_t i = st; i <= en; i++) leds[i].nscale8(fade);
  // Draw head
  uint16_t pos = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - headPos % rangeLen) : (headPos % rangeLen);
  if (headPos < rangeLen) leds[st + pos] = CRGB(r, g, b);
}

static void renderTwinkle(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t spawnMs, uint8_t density,
                           uint8_t fadeSpeed, unsigned long elapsedMs,
                           uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  // Fade existing
  uint8_t fade = fadeSpeed ? (uint8_t)(255 - fadeSpeed) : TWINKLE_DEFAULT_FADE;
  for (uint16_t i = st; i <= en; i++) leds[i].nscale8(fade);
  // Spawn new
  uint8_t dens = density ? density : TWINKLE_DEFAULT_DENSITY;
  if (spawnMs == 0) spawnMs = 1;
  for (uint8_t d = 0; d < dens; d++) {
    if (random8() < TWINKLE_SPAWN_CHANCE) {
      uint16_t pos = random16(rangeLen);
      uint8_t bri = random8(TWINKLE_BRI_MIN, TWINKLE_BRI_MAX);
      leds[st + pos] = CRGB((uint8_t)((uint16_t)r*bri/255),
                             (uint8_t)((uint16_t)g*bri/255),
                             (uint8_t)((uint16_t)b*bri/255));
    }
  }
}

static void renderStrobe(uint8_t r, uint8_t g, uint8_t b,
                          uint16_t periodMs, uint8_t dutyPct,
                          unsigned long elapsedMs,
                          uint16_t st, uint16_t en) {
  if (periodMs == 0) periodMs = STROBE_DEFAULT_PERIOD;
  if (dutyPct > STROBE_MAX_DUTY) dutyPct = STROBE_DEFAULT_DUTY;
  uint32_t phase = elapsedMs % (uint32_t)periodMs;
  uint32_t onMs = (uint32_t)periodMs * dutyPct / 100;
  bool on = (phase < onMs);
  for (uint16_t i = st; i <= en; i++)
    leds[i] = on ? CRGB(r, g, b) : CRGB::Black;
}

static void renderWipeSeq(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t speedMs, uint8_t dir,
                           unsigned long elapsedMs,
                           uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  if (speedMs == 0) speedMs = WIPE_DEFAULT_SPEED;
  // Number of pixels filled so far (wraps around for continuous wipe)
  uint16_t filled = (uint16_t)((elapsedMs / speedMs) % (rangeLen * WIPE_CYCLE_MULTIPLIER));
  bool filling = (filled < rangeLen);
  uint16_t count = filling ? filled : (rangeLen * WIPE_CYCLE_MULTIPLIER - filled);
  for (uint16_t i = 0; i < rangeLen; i++) {
    uint16_t idx = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - i) : i;
    leds[st + idx] = (i < count) ? (filling ? CRGB(r, g, b) : CRGB::Black)
                                 : (filling ? CRGB::Black : CRGB(r, g, b));
  }
}

static void renderScanner(uint8_t r, uint8_t g, uint8_t b,
                            uint16_t speedMs, uint8_t barWidth,
                            unsigned long elapsedMs,
                            uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  if (speedMs == 0) speedMs = SCANNER_DEFAULT_SPEED;
  if (barWidth < 1) barWidth = SCANNER_DEFAULT_BAR;
  if (barWidth > rangeLen) barWidth = rangeLen;
  // Ping-pong position
  uint16_t travel = rangeLen - barWidth;
  if (travel == 0) travel = 1;
  uint32_t cycle = (uint32_t)travel * 2;
  uint32_t pos = (elapsedMs / speedMs) % cycle;
  if (pos >= travel) pos = cycle - pos;
  // Fade all
  for (uint16_t i = st; i <= en; i++) leds[i].nscale8(SCANNER_TRAIL_FADE);
  // Draw bar
  for (uint16_t w = 0; w < barWidth && (pos + w) < rangeLen; w++)
    leds[st + pos + w] = CRGB(r, g, b);
}

static void renderSparkle(uint8_t r, uint8_t g, uint8_t b,
                            uint16_t spawnMs, uint8_t density,
                            unsigned long elapsedMs,
                            uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  // Solid background
  for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(r, g, b);
  // Random white sparkles
  uint8_t dens = density ? density : SPARKLE_DEFAULT_DENSITY;
  for (uint8_t d = 0; d < dens; d++) {
    if (random8() < SPARKLE_SPAWN_CHANCE)
      leds[st + random16(rangeLen)] = CRGB::White;
  }
}

static void renderGradient(uint8_t r1, uint8_t g1, uint8_t b1,
                            uint8_t r2, uint8_t g2, uint8_t b2,
                            uint16_t st, uint16_t en) {
  uint16_t rangeLen = en - st + 1;
  if (rangeLen <= 1) { leds[st] = CRGB(r1, g1, b1); return; }
  for (uint16_t i = 0; i < rangeLen; i++) {
    uint8_t frac = (uint8_t)((uint32_t)i * 255 / (rangeLen - 1));
    leds[st + i] = CRGB(
      (uint8_t)((r1 * (255 - frac) + r2 * frac) / 255),
      (uint8_t)((g1 * (255 - frac) + g2 * frac) / 255),
      (uint8_t)((b1 * (255 - frac) + b2 * frac) / 255));
  }
}

// ── Fold mirror — for folded strings, mirror first half onto second half ──

static void applyFold(uint16_t st, uint16_t en) {
  // A folded strip: LEDs go out half-way then fold back.
  // LED 0 pairs with LED N-1, LED 1 with N-2, etc.
  // We render to the first half, then mirror to the second half.
  uint16_t total = en - st + 1;
  uint16_t half = total / 2;
  for (uint16_t i = 0; i < half; i++) {
    leds[st + total - 1 - i] = leds[st + i];
  }
  // If odd count, the middle LED (fold point) keeps its value
}

// ── Boot animation ──────────────────────────────────────────────────────

void bootAnimation() {
#ifdef BOARD_FASTLED
  // Ensure LEDs are off first, then wait 3 seconds
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  showSafe();
  delay(BOOT_WAIT_MS);

  uint8_t sc = childCfg.stringCount;
  if (sc > 0) {
    // Rainbow sweep across all configured strings
    // Compute total LED count across all strings
    uint16_t totalLeds = 0;
    for (uint8_t s = 0; s < sc; s++) {
      uint16_t lc = childCfg.strings[s].ledCount;
      if (lc > 0 && totalLeds + lc <= NUM_LEDS)
        totalLeds += lc;
    }
    if (totalLeds == 0) totalLeds = NUM_LEDS;
    // Progressive rainbow fill: light each LED one at a time
    uint16_t delayPer = BOOT_SWEEP_MS / totalLeds;
    if (delayPer < BOOT_MIN_DELAY) delayPer = BOOT_MIN_DELAY;
    for (uint16_t i = 0; i < totalLeds; i++) {
      uint8_t hue = (uint8_t)((uint32_t)i * 255 / totalLeds);
      CHSV hsv(hue, 255, 255);
      CRGB rgb;
      hsv2rgb_rainbow(hsv, rgb);
      leds[i] = rgb;
      showSafe();
      delay(delayPer);
    }
    // Hold then fade to black
    delay(BOOT_HOLD_MS);
  } else {
    // No strings configured: flash red across BOOT_FLASH_COUNT LEDs
    uint16_t count = NUM_LEDS < BOOT_FLASH_COUNT ? NUM_LEDS : BOOT_FLASH_COUNT;
    fill_solid(leds, count, CRGB(255, 0, 0));
    showSafe();
    delay(BOOT_FLASH_MS);
  }
  // All off
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  showSafe();

#elif defined(BOARD_GIGA_CHILD)
  // Giga child: wait then rainbow cycle on single onboard LED
  clearAndShow();
  delay(BOOT_WAIT_MS);
  for (uint16_t h = 0; h < 256; h += BOOT_GIGA_HUE_STEP) {
    CHSV hsv((uint8_t)h, 255, 255);
    CRGB rgb;
    hsv2rgb_rainbow(hsv, rgb);
    leds[0] = rgb;
    showSafe();
    delay(BOOT_GIGA_FRAME_MS);
  }
  delay(BOOT_GIGA_HOLD_MS);
  clearAndShow();
#endif
  childBootDone = true;
}

// ── applyAction — render one action type across a string range ────────────

bool applyAction(uint8_t at, uint8_t r, uint8_t g, uint8_t b,
                  uint16_t p16a, uint8_t p8a, uint8_t p8b,
                  uint8_t p8c, uint8_t p8d,
                         unsigned long elapsedMs,
                         uint16_t st, uint16_t en, bool folded) {
  if (st == 0xFFFF || en < st) return false;
  if (en >= NUM_LEDS) en = NUM_LEDS - 1;

  // For folded strings, render to virtual half-length then mirror
  uint16_t realEn = en;
  if (folded) {
    uint16_t total = en - st + 1;
    uint16_t half = (total + 1) / 2;  // ceil — includes fold LED if odd
    en = st + half - 1;
  }

  switch (at) {
    case ACT_SOLID:    renderSolid(r, g, b, st, en); break;
    case ACT_FADE:     renderFade(r, g, b, p8a, p8b, p8c, p16a, elapsedMs, st, en); break;
    case ACT_BREATHE:  renderBreathe(r, g, b, p16a, p8a, elapsedMs, st, en); break;
    case ACT_CHASE:    renderChase(r, g, b, p16a, p8a, p8c, elapsedMs, st, en); break;
    case ACT_RAINBOW:  renderRainbow(p16a, p8a, p8c, elapsedMs, st, en); break;
    case ACT_FIRE:     renderFire(r, g, b, p16a, p8a, p8b, elapsedMs, st, en); break;
    case ACT_COMET:    renderComet(r, g, b, p16a, p8a, p8c, p8d, elapsedMs, st, en); break;
    case ACT_TWINKLE:  renderTwinkle(r, g, b, p16a, p8a, p8d, elapsedMs, st, en); break;
    case ACT_STROBE:   renderStrobe(r, g, b, p16a, p8a, elapsedMs, st, en); break;
    case ACT_WIPE_SEQ: renderWipeSeq(r, g, b, p16a, p8c, elapsedMs, st, en); break;
    case ACT_SCANNER:  renderScanner(r, g, b, p16a, p8a, elapsedMs, st, en); break;
    case ACT_SPARKLE:  renderSparkle(r, g, b, p16a, p8a, elapsedMs, st, en); break;
    case ACT_GRADIENT: renderGradient(r, g, b, p8a, p8b, p8c, st, en); break;
    default: return false;
  }

  if (folded) applyFold(st, realEn);
  return true;
}

// ── applyRunnerStep — shared by both ESP32 and D1 Mini ───────────────────

bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t /*flashPh*/,
                     unsigned long stepMs) {
  bool drew = false;
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    bool folded = (j < childCfg.stringCount) &&
                  (childCfg.strings[j].flags & STR_FLAG_FOLDED);
    if (applyAction(rs.actionType, rs.r, rs.g, rs.b,
                    rs.p16a, rs.p8a, rs.p8b, rs.p8c, rs.p8d,
                    stepMs, rs.ledStart[j], rs.ledEnd[j], folded))
      drew = true;
  }
  return drew;
}

// ── Delay per action type ─────────────────────────────────────────────────

// Effects that rely on previous frame data (fade trails, random sparks)
static bool actionNeedsPersist(uint8_t at) {
  return at == ACT_COMET || at == ACT_TWINKLE || at == ACT_FIRE
      || at == ACT_SCANNER || at == ACT_SPARKLE;
}

static uint8_t actionDelay(uint8_t at) {
  switch (at) {
    case ACT_SOLID:    return FRAME_DELAY_SOLID;
    case ACT_FADE:     return FRAME_DELAY_FADE;
    case ACT_BREATHE:  return FRAME_DELAY_BREATHE;
    case ACT_CHASE:    return FRAME_DELAY_CHASE;
    case ACT_RAINBOW:  return FRAME_DELAY_RAINBOW;
    case ACT_FIRE:     return FRAME_DELAY_FIRE;
    case ACT_COMET:    return FRAME_DELAY_COMET;
    case ACT_TWINKLE:  return FRAME_DELAY_TWINKLE;
    case ACT_STROBE:   return FRAME_DELAY_STROBE;
    case ACT_WIPE_SEQ: return FRAME_DELAY_WIPE_SEQ;
    case ACT_SCANNER:  return FRAME_DELAY_SCANNER;
    case ACT_SPARKLE:  return FRAME_DELAY_SPARKLE;
    case ACT_GRADIENT: return FRAME_DELAY_GRADIENT;  // static — no need to refresh fast
    default:           return FRAME_DELAY_DEFAULT;
  }
}

// ── ESP32 / Giga-child: blocking LED task ─────────────────────────────────

#if defined(BOARD_ESP32) || defined(BOARD_GIGA_CHILD)

void ledTask(void* parameter) {
  (void)parameter;

  // Wait for boot animation to complete before rendering
  while (!childBootDone) delay(BOOT_POLL_MS);

  uint8_t       prevActSeq  = 0;
  unsigned long actStart    = 0;
  bool          offRendered = false;
  uint8_t       prevRunStep = 0xFF;
  unsigned long stepStartMs = 0;

  while (true) {

    // ── 0. Sync blink confirmation ────────────────────────────────────────
    if (childSyncBlink > 0) {
      uint8_t n = childSyncBlink;
      childSyncBlink = 0;
      for (uint8_t b = 0; b < n; b++) {
        fill_solid(leds, NUM_LEDS, CRGB::White);
        showSafe(); delay(SYNC_BLINK_MS);
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        showSafe(); delay(SYNC_BLINK_MS);
      }
      offRendered = true;
      continue;
    }

    // ── 1. Arm runner when epoch reached (or immediately if NTP failed) ──
    if (childRunnerArmed && childStepCount > 0) {
      uint32_t now = (uint32_t)currentEpoch();
      // If NTP failed (epoch < year 2020), start immediately
      if (now < NTP_EPOCH_2020 || now >= childRunnerStart) {
        childRunnerArmed  = false;
        childRunnerActive = true;
        prevRunStep       = 0xFF;
      }
    }

    // ── 2. Execute runner ─────────────────────────────────────────────────
    if (childRunnerActive && childStepCount > 0) {
      uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
      uint8_t  curStep = 0;
      uint32_t acc     = 0;
      bool     done    = true;
      for (uint8_t i = 0; i < childStepCount; i++) {
        acc += childRunner[i].durationS;
        if (elapsed < acc) { curStep = i; done = false; break; }
      }
      if (done) {
        // Signal runner ended event
        childEvtType  = childRunner[childStepCount - 1].actionType;
        childEvtStep  = childStepCount - 1;
        childEvtTotal = childStepCount;
        childEvtEvent = 1;  // ended
        childEvtPending = true;
        if (childRunnerLoop) {
          // Loop: reset start time so runner repeats from step 0
          childRunnerStart += acc;
          prevRunStep = 0xFF;
        } else {
          childRunnerActive = false;
        }
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        showSafe();
        delay(10);
        continue;
      }
      if (curStep != prevRunStep) {
        prevRunStep = curStep;
        stepStartMs = millis();
        // Signal step-started event
        childEvtType  = childRunner[curStep].actionType;
        childEvtStep  = curStep;
        childEvtTotal = childStepCount;
        childEvtEvent = 0;  // started
        childEvtPending = true;
      }
      {
        unsigned long elapsed = millis() - stepStartMs;
        uint16_t dly = childRunner[curStep].delayMs;
        if (!actionNeedsPersist(childRunner[curStep].actionType))
          fill_solid(leds, NUM_LEDS, CRGB::Black);
        if (elapsed >= dly) {
          applyRunnerStep(childRunner[curStep], 0, elapsed - dly);
        }
        showSafe();
        delay(actionDelay(childRunner[curStep].actionType));
      }
      continue;
    }

    // ── 3. Immediate action ───────────────────────────────────────────────
    // Snapshot volatile fields atomically on seq change (Issue #27)
    static uint8_t  locAt = ACT_OFF, locR = 0, locG = 0, locB = 0;
    static uint16_t locP16a = 0;
    static uint8_t  locP8a = 0, locP8b = 0, locP8c = 0, locP8d = 0;

    uint8_t seq = childActSeq;
    if (seq != prevActSeq) {
      prevActSeq = seq;
      locAt   = childActType;
      locR    = childActR;
      locG    = childActG;
      locB    = childActB;
      locP16a = childActP16a;
      locP8a  = childActP8a;
      locP8b  = childActP8b;
      locP8c  = childActP8c;
      locP8d  = childActP8d;
      actStart    = millis();
      offRendered = false;
    }

    if (locAt != ACT_OFF) {
      if (!actionNeedsPersist(locAt))
        fill_solid(leds, NUM_LEDS, CRGB::Black);
      // Apply action per-string using correct LED ranges and per-string fold flag
      for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
        bool fold = (j < childCfg.stringCount) && (childCfg.strings[j].flags & STR_FLAG_FOLDED);
        applyAction(locAt, locR, locG, locB,
                    locP16a, locP8a, locP8b, locP8c, locP8d,
                    millis() - actStart, childActSt[j], childActEn[j], fold);
      }
      showSafe();
      delay(actionDelay(locAt));
    } else {
      if (!offRendered) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        showSafe();
        offRendered = true;
      }
      delay(FRAME_DELAY_OFF);
    }
  }
}

#endif  // BOARD_ESP32 || BOARD_GIGA_CHILD

// ── D1 Mini: non-blocking updateLED() ────────────────────────────────────

#ifdef BOARD_D1MINI

void updateLED() {
  if (!childBootDone) return;   // boot animation still running

  static uint8_t       prevActSeq   = 0;
  static unsigned long actStart     = 0;
  static bool          actRendered  = false;
  static uint8_t       prevRunStep  = 0xFF;
  static unsigned long stepStartMs  = 0;
  static unsigned long lastFrame    = 0;
  static uint8_t       blinkRemain  = 0;
  static unsigned long blinkTs      = 0;
  static bool          blinkOn      = false;

  FastLED.setBrightness(childBrightness);

  // ── 0. Sync blink confirmation (non-blocking) ──────────────────────────
  if (childSyncBlink > 0) {
    blinkRemain = childSyncBlink;
    childSyncBlink = 0;
    blinkOn = true;
    blinkTs = millis();
    fill_solid(leds, NUM_LEDS, CRGB::White);
    showSafe();
    actRendered = true;
    return;
  }
  if (blinkRemain > 0) {
    if (millis() - blinkTs >= SYNC_BLINK_MS) {
      blinkTs = millis();
      if (blinkOn) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        showSafe(); blinkOn = false;
      } else {
        blinkRemain--;
        if (blinkRemain > 0) {
          fill_solid(leds, NUM_LEDS, CRGB::White);
          showSafe(); blinkOn = true;
        }
      }
    }
    return;
  }

  // ── 1. Arm runner when epoch reached ────────────────────────────────────
  if (childRunnerArmed && childStepCount > 0) {
    if ((uint32_t)currentEpoch() >= childRunnerStart) {
      childRunnerArmed  = false;
      childRunnerActive = true;
      prevRunStep       = 0xFF;
    }
  }

  // ── 2. Execute runner ───────────────────────────────────────────────────
  if (childRunnerActive && childStepCount > 0) {
    uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
    uint8_t  curStep = 0;
    uint32_t acc     = 0;
    bool     done    = true;
    for (uint8_t i = 0; i < childStepCount; i++) {
      acc += childRunner[i].durationS;
      if (elapsed < acc) { curStep = i; done = false; break; }
    }
    if (done) {
      childEvtType  = childRunner[childStepCount - 1].actionType;
      childEvtStep  = childStepCount - 1;
      childEvtTotal = childStepCount;
      childEvtEvent = 1;
      childEvtPending = true;
      if (childRunnerLoop) {
        childRunnerStart += acc;
        prevRunStep = 0xFF;
      } else {
        childRunnerActive = false;
      }
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      showSafe(); return;
    }
    if (curStep != prevRunStep) {
      prevRunStep = curStep;
      stepStartMs = millis();
      childEvtType  = childRunner[curStep].actionType;
      childEvtStep  = curStep;
      childEvtTotal = childStepCount;
      childEvtEvent = 0;
      childEvtPending = true;
    }
    unsigned long now = millis();
    uint8_t frameDly = actionDelay(childRunner[curStep].actionType);
    if (now - lastFrame >= frameDly) {
      lastFrame = now;
      unsigned long elapsed = now - stepStartMs;
      uint16_t stepDly = childRunner[curStep].delayMs;
      if (!actionNeedsPersist(childRunner[curStep].actionType))
        fill_solid(leds, NUM_LEDS, CRGB::Black);
      if (elapsed >= stepDly) {
        applyRunnerStep(childRunner[curStep], 0, elapsed - stepDly);
      }
      showSafe();
    }
    return;
  }

  // ── 3. Immediate action ─────────────────────────────────────────────────
  // Snapshot volatile fields atomically on seq change (Issue #27)
  static uint8_t  locAt = ACT_OFF, locR = 0, locG = 0, locB = 0;
  static uint16_t locP16a = 0;
  static uint8_t  locP8a = 0, locP8b = 0, locP8c = 0, locP8d = 0;

  uint8_t seq = childActSeq;
  if (seq != prevActSeq) {
    prevActSeq = seq;
    locAt   = childActType;
    locR    = childActR;
    locG    = childActG;
    locB    = childActB;
    locP16a = childActP16a;
    locP8a  = childActP8a;
    locP8b  = childActP8b;
    locP8c  = childActP8c;
    locP8d  = childActP8d;
    actStart    = millis();
    actRendered = false;
    lastFrame   = 0;
  }

  if (locAt != ACT_OFF) {
    unsigned long now = millis();
    uint8_t dly = actionDelay(locAt);
    if (now - lastFrame >= dly) {
      lastFrame = now;
      if (!actionNeedsPersist(locAt))
        fill_solid(leds, NUM_LEDS, CRGB::Black);
      for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
        bool fold = (j < childCfg.stringCount) && (childCfg.strings[j].flags & STR_FLAG_FOLDED);
        applyAction(locAt, locR, locG, locB,
                    locP16a, locP8a, locP8b, locP8c, locP8d,
                    now - actStart, childActSt[j], childActEn[j], fold);
      }
      showSafe();
    }
  } else {
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      showSafe();
      actRendered = true;
    }
  }
}

#endif  // BOARD_D1MINI

#endif  // BOARD_CHILD
