/*
 * DmxBridge.h — DMX-512 output via UART + RS-485 transceiver.
 *
 * Guarded by #ifdef BOARD_DMX_BRIDGE — safe to include on any board.
 */

#ifndef DMXBRIDGE_H
#define DMXBRIDGE_H

#include "BoardConfig.h"

#ifdef BOARD_DMX_BRIDGE

#include "GigaLED.h"  // provides CRGB, CHSV, hsv2rgb_rainbow (shared with Giga-child)

// DMX bridge config (persisted to NVS)
struct DmxBridgeConfig {
  uint16_t universe;      // Art-Net universe (0-based)
  uint16_t startAddress;  // DMX start address (1-512)
  uint8_t  channelsPerFixture; // 3 (RGB) or 4 (RGBD) or custom
  uint8_t  fixtureCount;  // number of RGB fixtures addressed
};

extern DmxBridgeConfig dmxCfg;
extern uint8_t dmxBuf[DMX_UNIVERSE_MAX + 1]; // start code + 512 channels
extern CRGB leds[NUM_LEDS]; // virtual LED array for action rendering

void dmxInit();
void dmxSendFrame();
void dmxUpdateFromLeds();  // copy leds[] → dmxBuf channels
void clearAndShow();
void fill_solid(CRGB* arr, uint16_t count, CRGB color);

#endif // BOARD_DMX_BRIDGE
#endif // DMXBRIDGE_H
