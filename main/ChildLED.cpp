/*
 * ChildLED.cpp — LED rendering for ESP32 (FreeRTOS task) and D1 Mini
 *                (non-blocking updateLED).
 *
 * Supports 9 action types: Blackout, Solid, Fade, Breathe, Chase,
 * Rainbow, Fire, Comet, Twinkle.
 */

#include <Arduino.h>
#include "BoardConfig.h"

#ifdef BOARD_FASTLED

#include "Protocol.h"
#include "NetUtils.h"
#include "Child.h"
#include "ChildLED.h"

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
      if (t < 85) return CRGB(t*3, 0, 0);
      if (t < 170) return CRGB(255, (uint8_t)((t-85)*3), 0);
      return CRGB(255, 255, (uint8_t)((t-170)*3));
    }
    case PAL_COOL:    return hsvToRgb((uint8_t)(idx/2 + 140), 180, (uint8_t)(180 + idx/4));
    case PAL_PASTEL:  return hsvToRgb(idx, 100, 255);
  }
}

// ── Render helpers (operate on a range st..en within leds[]) ──────────────

static void renderSolid(uint8_t r, uint8_t g, uint8_t b,
                         uint8_t st, uint8_t en) {
  for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(r, g, b);
}

static void renderFade(uint8_t r1, uint8_t g1, uint8_t b1,
                        uint8_t r2, uint8_t g2, uint8_t b2,
                        uint16_t speedMs, unsigned long elapsedMs,
                        uint8_t st, uint8_t en) {
  if (speedMs == 0) speedMs = 1;
  uint16_t t = (uint16_t)(elapsedMs % (uint32_t)speedMs);
  uint8_t frac = (uint8_t)((uint32_t)t * 255 / speedMs);
  uint8_t r = (uint8_t)((r1 * (255 - frac) + r2 * frac) / 255);
  uint8_t g = (uint8_t)((g1 * (255 - frac) + g2 * frac) / 255);
  uint8_t b = (uint8_t)((b1 * (255 - frac) + b2 * frac) / 255);
  for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(r, g, b);
}

static void renderBreathe(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t periodMs, uint8_t minBriPct,
                           unsigned long elapsedMs,
                           uint8_t st, uint8_t en) {
  if (periodMs == 0) periodMs = 1;
  uint8_t phase = (uint8_t)((elapsedMs % (uint32_t)periodMs) * 256 / periodMs);
  uint8_t sVal = sinLut(phase);  // 0-255
  uint8_t minB = (uint8_t)((uint16_t)minBriPct * 255 / 100);
  uint8_t bri = minB + (uint8_t)((uint16_t)(255 - minB) * sVal / 255);
  uint8_t rr = (uint8_t)((uint16_t)r * bri / 255);
  uint8_t gg = (uint8_t)((uint16_t)g * bri / 255);
  uint8_t bb = (uint8_t)((uint16_t)b * bri / 255);
  for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(rr, gg, bb);
}

static void renderChase(uint8_t r, uint8_t g, uint8_t b,
                         uint16_t speedMs, uint8_t spacing, uint8_t dir,
                         unsigned long elapsedMs,
                         uint8_t st, uint8_t en) {
  if (speedMs == 0) speedMs = 1;
  if (spacing < 2) spacing = 3;
  uint8_t rangeLen = en - st + 1;
  uint8_t offset = (uint8_t)((elapsedMs / speedMs) % spacing);
  for (uint8_t i = 0; i < rangeLen; i++) {
    uint8_t idx = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - i) : i;
    leds[st + idx] = ((i + offset) % spacing == 0) ? CRGB(r, g, b) : CRGB::Black;
  }
}

static void renderRainbow(uint16_t speedMs, uint8_t palId, uint8_t dir,
                           unsigned long elapsedMs,
                           uint8_t st, uint8_t en) {
  if (speedMs == 0) speedMs = 1;
  uint8_t rangeLen = en - st + 1;
  uint8_t timeOff = (uint8_t)(elapsedMs / speedMs);
  for (uint8_t i = 0; i < rangeLen; i++) {
    uint8_t idx = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - i) : i;
    uint8_t hue = (uint8_t)((uint16_t)i * 255 / rangeLen + timeOff);
    leds[st + idx] = paletteColor(palId, hue);
  }
}

static void renderFire(uint8_t r, uint8_t g, uint8_t b,
                        uint16_t speedMs, uint8_t cooling, uint8_t sparking,
                        unsigned long elapsedMs,
                        uint8_t st, uint8_t en) {
  static uint8_t heat[MAX_LEDS];
  uint8_t rangeLen = en - st + 1;
  // Cool down
  for (uint8_t i = 0; i < rangeLen; i++) {
    uint8_t cool = random8(0, ((uint16_t)cooling * 10 / rangeLen) + 2);
    heat[i] = (heat[i] > cool) ? heat[i] - cool : 0;
  }
  // Heat rises
  for (uint8_t k = rangeLen - 1; k >= 2; k--) {
    heat[k] = ((uint16_t)heat[k-1] + heat[k-2] + heat[k-2]) / 3;
  }
  // Sparks
  if (random8() < sparking) {
    uint8_t y = random8(min((uint8_t)7, rangeLen));
    heat[y] = qadd8(heat[y], random8(160, 255));
  }
  // Map heat to colour
  for (uint8_t j = 0; j < rangeLen; j++) {
    uint8_t t = heat[j];
    uint8_t rr, gg, bb;
    if (t < 85)       { rr = (uint8_t)((uint16_t)r*t*3/255); gg = 0; bb = 0; }
    else if (t < 170) { rr = r; gg = (uint8_t)((uint16_t)g*(t-85)*3/255); bb = 0; }
    else              { rr = r; gg = g; bb = (uint8_t)((uint16_t)b*(t-170)*3/255); }
    leds[st + j] = CRGB(rr, gg, bb);
  }
}

static void renderComet(uint8_t r, uint8_t g, uint8_t b,
                         uint16_t speedMs, uint8_t tailLen, uint8_t dir,
                         uint8_t decayPct, unsigned long elapsedMs,
                         uint8_t st, uint8_t en) {
  uint8_t rangeLen = en - st + 1;
  if (speedMs == 0) speedMs = 1;
  if (tailLen < 1) tailLen = 5;
  uint8_t headPos = (uint8_t)((elapsedMs / speedMs) % (rangeLen + tailLen));
  // Fade all
  uint8_t fade = decayPct ? (uint8_t)(256 - decayPct * 256 / 100) : 200;
  for (uint8_t i = st; i <= en; i++) leds[i].nscale8(fade);
  // Draw head
  uint8_t pos = (dir == DIR_W || dir == DIR_S) ? (rangeLen - 1 - headPos % rangeLen) : (headPos % rangeLen);
  if (headPos < rangeLen) leds[st + pos] = CRGB(r, g, b);
}

static void renderTwinkle(uint8_t r, uint8_t g, uint8_t b,
                           uint16_t spawnMs, uint8_t density,
                           uint8_t fadeSpeed, unsigned long elapsedMs,
                           uint8_t st, uint8_t en) {
  uint8_t rangeLen = en - st + 1;
  // Fade existing
  uint8_t fade = fadeSpeed ? (uint8_t)(255 - fadeSpeed) : 240;
  for (uint8_t i = st; i <= en; i++) leds[i].nscale8(fade);
  // Spawn new
  uint8_t dens = density ? density : 3;
  if (spawnMs == 0) spawnMs = 1;
  for (uint8_t d = 0; d < dens; d++) {
    if (random8() < 40) {
      uint8_t pos = random8(rangeLen);
      uint8_t bri = random8(128, 255);
      leds[st + pos] = CRGB((uint8_t)((uint16_t)r*bri/255),
                             (uint8_t)((uint16_t)g*bri/255),
                             (uint8_t)((uint16_t)b*bri/255));
    }
  }
}

// ── applyAction — render one action type across a string range ────────────

static bool applyAction(uint8_t at, uint8_t r, uint8_t g, uint8_t b,
                         uint16_t p16a, uint8_t p8a, uint8_t p8b,
                         uint8_t p8c, uint8_t p8d,
                         unsigned long elapsedMs,
                         uint8_t st, uint8_t en) {
  if (st == 0xFF || en < st) return false;
  if (en >= NUM_LEDS) en = NUM_LEDS - 1;

  switch (at) {
    case ACT_SOLID:    renderSolid(r, g, b, st, en); return true;
    case ACT_FADE:     renderFade(r, g, b, p8a, p8b, p8c, p16a, elapsedMs, st, en); return true;
    case ACT_BREATHE:  renderBreathe(r, g, b, p16a, p8a, elapsedMs, st, en); return true;
    case ACT_CHASE:    renderChase(r, g, b, p16a, p8a, p8c, elapsedMs, st, en); return true;
    case ACT_RAINBOW:  renderRainbow(p16a, p8a, p8c, elapsedMs, st, en); return true;
    case ACT_FIRE:     renderFire(r, g, b, p16a, p8a, p8b, elapsedMs, st, en); return true;
    case ACT_COMET:    renderComet(r, g, b, p16a, p8a, p8c, p8d, elapsedMs, st, en); return true;
    case ACT_TWINKLE:  renderTwinkle(r, g, b, p16a, p8a, p8d, elapsedMs, st, en); return true;
    default: return false;
  }
}

// ── applyRunnerStep — shared by both ESP32 and D1 Mini ───────────────────

bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t /*flashPh*/,
                     unsigned long stepMs) {
  bool drew = false;
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    if (applyAction(rs.actionType, rs.r, rs.g, rs.b,
                    rs.p16a, rs.p8a, rs.p8b, rs.p8c, rs.p8d,
                    stepMs, rs.ledStart[j], rs.ledEnd[j]))
      drew = true;
  }
  return drew;
}

// ── Delay per action type ─────────────────────────────────────────────────

static uint8_t actionDelay(uint8_t at) {
  switch (at) {
    case ACT_SOLID:   return 50;
    case ACT_FADE:    return 20;
    case ACT_BREATHE: return 15;
    case ACT_CHASE:   return 10;
    case ACT_RAINBOW: return 10;
    case ACT_FIRE:    return 15;
    case ACT_COMET:   return 10;
    case ACT_TWINKLE: return 10;
    default:          return 10;
  }
}

// ── ESP32: FreeRTOS task (Core 0) ─────────────────────────────────────────

#ifdef BOARD_ESP32

void ledTask(void* parameter) {
  (void)parameter;

  uint8_t       prevActSeq  = 0;
  unsigned long actStart    = 0;
  bool          offRendered = false;
  uint8_t       prevRunStep = 0xFF;
  unsigned long stepStartMs = 0;

  while (true) {
    FastLED.setBrightness(childBrightness);

    // ── 0. Sync blink confirmation ────────────────────────────────────────
    if (childSyncBlink > 0) {
      uint8_t n = childSyncBlink;
      childSyncBlink = 0;
      for (uint8_t b = 0; b < n; b++) {
        fill_solid(leds, NUM_LEDS, CRGB::White);
        FastLED.show(); delay(200);
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show(); delay(200);
      }
      offRendered = true;
      continue;
    }

    // ── 1. Arm runner when epoch reached ──────────────────────────────────
    if (childRunnerArmed && childStepCount > 0) {
      if ((uint32_t)currentEpoch() >= childRunnerStart) {
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
        childRunnerActive = false;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
        delay(10);
        continue;
      }
      if (curStep != prevRunStep) {
        prevRunStep = curStep;
        stepStartMs = millis();
      }
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyRunnerStep(childRunner[curStep], 0, millis() - stepStartMs);
      FastLED.show();
      delay(actionDelay(childRunner[curStep].actionType));
      continue;
    }

    // ── 3. Immediate action ───────────────────────────────────────────────
    uint8_t seq = childActSeq;
    if (seq != prevActSeq) {
      prevActSeq  = seq;
      actStart    = millis();
      offRendered = false;
    }
    uint8_t at = childActType;

    if (at != ACT_OFF) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyAction(at, childActR, childActG, childActB,
                  childActP16a, childActP8a, childActP8b,
                  childActP8c, childActP8d,
                  millis() - actStart, 0, NUM_LEDS - 1);
      FastLED.show();
      delay(actionDelay(at));
    } else {
      if (!offRendered) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
        offRendered = true;
      }
      delay(10);
    }
  }
}

#endif  // BOARD_ESP32

// ── D1 Mini: non-blocking updateLED() ────────────────────────────────────

#ifdef BOARD_D1MINI

void updateLED() {
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
    FastLED.show();
    actRendered = true;
    return;
  }
  if (blinkRemain > 0) {
    if (millis() - blinkTs >= 200) {
      blinkTs = millis();
      if (blinkOn) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show(); blinkOn = false;
      } else {
        blinkRemain--;
        if (blinkRemain > 0) {
          fill_solid(leds, NUM_LEDS, CRGB::White);
          FastLED.show(); blinkOn = true;
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
      childRunnerActive = false;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show(); return;
    }
    if (curStep != prevRunStep) {
      prevRunStep = curStep;
      stepStartMs = millis();
    }
    unsigned long now = millis();
    uint8_t dly = actionDelay(childRunner[curStep].actionType);
    if (now - lastFrame >= dly) {
      lastFrame = now;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyRunnerStep(childRunner[curStep], 0, now - stepStartMs);
      FastLED.show();
    }
    return;
  }

  // ── 3. Immediate action ─────────────────────────────────────────────────
  uint8_t seq = childActSeq;
  if (seq != prevActSeq) {
    prevActSeq  = seq;
    actStart    = millis();
    actRendered = false;
    lastFrame   = 0;
  }
  uint8_t at = childActType;

  if (at != ACT_OFF) {
    unsigned long now = millis();
    uint8_t dly = actionDelay(at);
    if (now - lastFrame >= dly) {
      lastFrame = now;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyAction(at, childActR, childActG, childActB,
                  childActP16a, childActP8a, childActP8b,
                  childActP8c, childActP8d,
                  now - actStart, 0, NUM_LEDS - 1);
      FastLED.show();
    }
  } else {
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
      actRendered = true;
    }
  }
}

#endif  // BOARD_D1MINI

#endif  // BOARD_FASTLED
