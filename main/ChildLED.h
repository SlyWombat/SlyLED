/*
 * ChildLED.h — LED rendering for ESP32 (FreeRTOS), D1 Mini (non-blocking),
 *              and Giga-child (Mbed thread + onboard RGB).
 *
 * All content is guarded by #ifdef BOARD_CHILD.
 */

#ifndef CHILDLED_H
#define CHILDLED_H

#include "BoardConfig.h"

#ifdef BOARD_CHILD

// Boot animation — call once after WiFi + config loaded.
// If strings configured: single rainbow sweep across all strings.
// If no strings: single red flash across 150 LEDs.
void bootAnimation();

// Render a single action type to a range within leds[].
bool applyAction(uint8_t at, uint8_t r, uint8_t g, uint8_t b,
                 uint16_t p16a, uint8_t p8a, uint8_t p8b,
                 uint8_t p8c, uint8_t p8d,
                 unsigned long elapsedMs, uint16_t st, uint16_t en, bool folded,
                 uint8_t strIdx = 0);

// Applies one ChildRunnerStep to the leds[] array for all affected string ranges.
bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t flashPh, unsigned long stepMs);

#if defined(BOARD_ESP32) || defined(BOARD_GIGA_CHILD)
// Blocking LED task (FreeRTOS on ESP32, Mbed thread on Giga-child).
void ledTask(void* parameter);
#endif

#ifdef BOARD_D1MINI
// Non-blocking LED update — called from loop() and within serveClient() waits.
void updateLED();
#endif

#endif  // BOARD_CHILD

#endif  // CHILDLED_H
