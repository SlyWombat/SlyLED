/*
 * DmxBridge.h — DMX-512 output via UART + RS-485 transceiver.
 *
 * Guarded by #ifdef BOARD_DMX_BRIDGE — safe to include on any board.
 */

#ifndef DMXBRIDGE_H
#define DMXBRIDGE_H

#include "BoardConfig.h"

#ifdef BOARD_DMX_BRIDGE

#include "GigaLED.h"  // provides CRGB, CHSV, hsv2rgb_rainbow

// Per-channel name length and max channels per fixture
constexpr uint8_t DMX_CH_NAME_LEN    = 12;
constexpr uint8_t DMX_MAX_CH_PER_FIX = 24;  // max channels per fixture profile

// #110 — input mode selector. The bridge can either render its own
// SlyLED actions into dmxBuf via dmxUpdateFromLeds() (mode 0), or accept
// external Art-Net (1) / sACN (2) packets that write directly into the
// universe buffer. Mode 3 listens for both passthrough protocols
// simultaneously and lets the last received packet win.
constexpr uint8_t DMX_INPUT_SLYLED = 0;
constexpr uint8_t DMX_INPUT_ARTNET = 1;
constexpr uint8_t DMX_INPUT_SACN   = 2;
constexpr uint8_t DMX_INPUT_AUTO   = 3;

// DMX bridge config (persisted to NVS on ESP32, RAM-only on Giga)
struct DmxBridgeConfig {
  uint8_t  subnet;                                // Art-Net subnet (0-15, default 0)
  uint8_t  universe;                              // Art-Net universe (0-15, default 0)
  uint16_t startAddress;                          // DMX start address (1-512)
  uint8_t  channelsPerFixture;                    // channels per fixture (e.g., 13)
  uint8_t  fixtureCount;                          // number of fixtures addressed
  uint8_t  inputMode;                              // #110 — DMX_INPUT_*
  char     channelNames[DMX_MAX_CH_PER_FIX][DMX_CH_NAME_LEN]; // per-channel labels
};

extern DmxBridgeConfig dmxCfg;
extern uint8_t dmxBuf[DMX_UNIVERSE_MAX + 1];      // start code + 512 channels
extern CRGB leds[NUM_LEDS];                       // virtual LED array
extern volatile uint32_t dmxFrameCount;           // frames sent (diagnostic)
extern volatile bool dmxOutputActive;             // true if DMX output is running
extern volatile bool dmxSelfTestOk;              // true if boot self-test passed

void dmxInit();
void dmxSendFrame();
void dmxUpdateFromLeds();
void dmxSetChannel(uint16_t channel, uint8_t value);
void dmxBlackout();
void dmxLoadConfig();
void dmxSaveConfig();
void clearAndShow();
void fill_solid(CRGB* arr, uint16_t count, CRGB color);

#endif // BOARD_DMX_BRIDGE
#endif // DMXBRIDGE_H
