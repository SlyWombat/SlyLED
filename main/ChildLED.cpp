/*
 * ChildLED.cpp — LED rendering for ESP32 (FreeRTOS task) and D1 Mini
 *                (non-blocking updateLED).
 */

#include <Arduino.h>
#include "BoardConfig.h"

#ifdef BOARD_FASTLED

#include "Protocol.h"
#include "NetUtils.h"
#include "Child.h"
#include "ChildLED.h"

// ── applyRunnerStep — shared by both ESP32 and D1 Mini ───────────────────────

bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t flashPh,
                     unsigned long stepMs) {
  bool drew = false;
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    uint8_t st = rs.ledStart[j], en = rs.ledEnd[j];
    if (st == 0xFF) continue;
    if (en >= NUM_LEDS) en = NUM_LEDS - 1;
    if (st > en) continue;
    uint8_t rangeLen = en - st + 1;

    if (rs.actionType == ACT_SOLID) {
      for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(rs.r, rs.g, rs.b);
      drew = true;
    } else if (rs.actionType == ACT_FLASH) {
      if (!flashPh) {
        for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(rs.r, rs.g, rs.b);
      }
      drew = true;
    } else if (rs.actionType == ACT_WIPE) {
      uint8_t spd = rs.wipeSpeedPct ? rs.wipeSpeedPct : 1;
      uint32_t front = (uint32_t)stepMs * spd * rangeLen / 100000UL;
      if (front > rangeLen) front = rangeLen;
      uint8_t dir = rs.wipeDir;
      for (uint8_t i = st; i <= en; i++) {
        uint8_t ri = i - st;
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((rangeLen - 1 - ri) < front)
                 : (ri < front);
        if (lit) leds[i] = CRGB(rs.r, rs.g, rs.b);
      }
      drew = true;
    }
  }
  return drew;
}

// ── ESP32: FreeRTOS task (Core 0) ─────────────────────────────────────────────

#ifdef BOARD_ESP32

void ledTask(void* parameter) {
  (void)parameter;

  uint8_t       prevActSeq  = 0;
  unsigned long actStart    = 0;
  uint8_t       flashPhase  = 0;
  bool          offRendered = false;
  uint8_t       prevRunStep = 0xFF;
  unsigned long stepStartMs = 0;
  uint8_t       runFlashPh  = 0;

  while (true) {
    // ── 0. Sync blink confirmation ────────────────────────────────────────────
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

    // ── 1. Arm runner when epoch reached ─────────────────────────────────────
    if (childRunnerArmed && childStepCount > 0) {
      if ((uint32_t)currentEpoch() >= childRunnerStart) {
        childRunnerArmed  = false;
        childRunnerActive = true;
        prevRunStep       = 0xFF;
      }
    }

    // ── 2. Execute runner ─────────────────────────────────────────────────────
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
        runFlashPh  = 0;
      }
      const ChildRunnerStep& rs = childRunner[curStep];

      if (rs.actionType == ACT_FLASH) {
        unsigned long now = millis();
        uint16_t period = runFlashPh ? rs.offMs : rs.onMs;
        if (now - stepStartMs >= (unsigned long)period) {
          runFlashPh ^= 1;
          stepStartMs = now;
        }
      }
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyRunnerStep(rs, runFlashPh, millis() - stepStartMs);
      FastLED.show();
      delay(rs.actionType == ACT_FLASH ? 5 :
            rs.actionType == ACT_WIPE  ? 10 : 50);
      continue;  // skip immediate action
    }

    // ── 3. Immediate action ───────────────────────────────────────────────────
    uint8_t seq = childActSeq;
    if (seq != prevActSeq) {
      prevActSeq  = seq;
      actStart    = millis();
      flashPhase  = 0;
      offRendered = false;
    }
    uint8_t at = childActType;
    uint8_t r  = childActR, g = childActG, b = childActB;

    if (at == ACT_SOLID) {
      fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
      FastLED.show();
      delay(50);

    } else if (at == ACT_FLASH) {
      unsigned long now = millis();
      uint16_t period = flashPhase ? childActOffMs : childActOnMs;
      if (now - actStart >= (unsigned long)period) {
        flashPhase ^= 1;
        actStart    = now;
      }
      fill_solid(leds, NUM_LEDS, flashPhase ? CRGB::Black : CRGB(r, g, b));
      FastLED.show();
      delay(5);

    } else if (at == ACT_WIPE) {
      uint8_t spd = childActWSpd ? childActWSpd : 1;
      uint32_t front = (uint32_t)(millis() - actStart) * spd * NUM_LEDS / 100000UL;
      if (front > NUM_LEDS) front = NUM_LEDS;
      uint8_t dir = childActWDir;
      for (uint8_t i = 0; i < NUM_LEDS; i++) {
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((NUM_LEDS - 1 - i) < front)
                 : (i < front);
        leds[i] = lit ? CRGB(r, g, b) : CRGB::Black;
      }
      FastLED.show();
      delay(10);

    } else {  // ACT_OFF — render black once, then idle
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

// ── D1 Mini: non-blocking updateLED() ────────────────────────────────────────

#ifdef BOARD_D1MINI

void updateLED() {
  static uint8_t       prevActSeq   = 0;
  static unsigned long actStart     = 0;
  static uint8_t       flashPhase   = 0;
  static bool          actRendered  = false;
  static uint8_t       prevRunStep  = 0xFF;
  static unsigned long stepStartMs  = 0;
  static uint8_t       runFlashPh   = 0;
  static uint8_t       lastSolidSt  = 0xFF;
  static unsigned long lastWipe     = 0;
  static uint8_t       blinkRemain  = 0;
  static unsigned long blinkTs      = 0;
  static bool          blinkOn      = false;

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
        FastLED.show();
        blinkOn = false;
      } else {
        blinkRemain--;
        if (blinkRemain > 0) {
          fill_solid(leds, NUM_LEDS, CRGB::White);
          FastLED.show();
          blinkOn = true;
        }
      }
    }
    return;
  }

  // ── 1. Arm runner when epoch reached ─────────────────────────────────────
  if (childRunnerArmed && childStepCount > 0) {
    if ((uint32_t)currentEpoch() >= childRunnerStart) {
      childRunnerArmed  = false;
      childRunnerActive = true;
      prevRunStep       = 0xFF;
      lastSolidSt       = 0xFF;
    }
  }

  // ── 2. Execute runner ─────────────────────────────────────────────────────
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
      return;
    }
    if (curStep != prevRunStep) {
      prevRunStep = curStep;
      stepStartMs = millis();
      runFlashPh  = 0;
      lastSolidSt = 0xFF;
    }
    const ChildRunnerStep& rs = childRunner[curStep];

    if (rs.actionType == ACT_SOLID) {
      if (curStep != lastSolidSt) {
        lastSolidSt = curStep;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, 0, 0);
        FastLED.show();
      }
    } else if (rs.actionType == ACT_FLASH) {
      unsigned long now = millis();
      uint16_t period = runFlashPh ? rs.offMs : rs.onMs;
      if (now - stepStartMs >= (unsigned long)period) {
        runFlashPh ^= 1;
        stepStartMs = now;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, runFlashPh, 0);
        FastLED.show();
      }
    } else if (rs.actionType == ACT_WIPE) {
      unsigned long now = millis();
      if (now - lastWipe >= 20) {
        lastWipe = now;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, 0, now - stepStartMs);
        FastLED.show();
      }
    } else {  // ACT_OFF — black once per step
      if (curStep != lastSolidSt) {
        lastSolidSt = curStep;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
      }
    }
    return;  // skip immediate action
  }

  // ── 3. Immediate action ───────────────────────────────────────────────────
  uint8_t seq = childActSeq;
  if (seq != prevActSeq) {
    prevActSeq  = seq;
    actStart    = millis();
    flashPhase  = 0;
    actRendered = false;
  }
  uint8_t at = childActType;
  uint8_t r  = childActR, g = childActG, b = childActB;

  if (at == ACT_SOLID) {
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
      FastLED.show();
      actRendered = true;
    }

  } else if (at == ACT_FLASH) {
    unsigned long now = millis();
    uint16_t period = flashPhase ? childActOffMs : childActOnMs;
    if (now - actStart >= (unsigned long)period) {
      flashPhase ^= 1;
      actStart = now;
      fill_solid(leds, NUM_LEDS, flashPhase ? CRGB::Black : CRGB(r, g, b));
      FastLED.show();
    }

  } else if (at == ACT_WIPE) {
    unsigned long now = millis();
    if (now - lastWipe >= 20) {
      lastWipe = now;
      uint8_t spd = childActWSpd ? childActWSpd : 1;
      uint32_t front = (uint32_t)(now - actStart) * spd * NUM_LEDS / 100000UL;
      if (front > NUM_LEDS) front = NUM_LEDS;
      uint8_t dir = childActWDir;
      for (uint8_t i = 0; i < NUM_LEDS; i++) {
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((NUM_LEDS - 1 - i) < front)
                 : (i < front);
        leds[i] = lit ? CRGB(r, g, b) : CRGB::Black;
      }
      FastLED.show();
    }

  } else {  // ACT_OFF — render black once
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
      actRendered = true;
    }
  }
}

#endif  // BOARD_D1MINI

#endif  // BOARD_FASTLED
