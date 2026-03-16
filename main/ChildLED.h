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
