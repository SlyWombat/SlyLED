/*
 * ChildLED.h — LED rendering for ESP32 (FreeRTOS task) and D1 Mini
 *              (non-blocking updateLED).
 *
 * All content is guarded by #ifdef BOARD_FASTLED.
 */

#ifndef CHILDLED_H
#define CHILDLED_H

#include "BoardConfig.h"

#ifdef BOARD_FASTLED

// Applies one ChildRunnerStep to the leds[] array for all affected string ranges.
// Returns true if any LEDs were drawn.
bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t flashPh, unsigned long stepMs);

#ifdef BOARD_ESP32
// FreeRTOS task entry point (pinned to Core 0).
void ledTask(void* parameter);
#endif

#ifdef BOARD_D1MINI
// Non-blocking LED update — called from loop() and within serveClient() waits.
void updateLED();
#endif

#endif  // BOARD_FASTLED

#endif  // CHILDLED_H
