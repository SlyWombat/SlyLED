/*
 * Child.h — Child node (ESP32 / D1 Mini / Giga-child) config structs,
 *           volatile action state, runner state, and function declarations.
 *
 * All content is guarded by #ifdef BOARD_CHILD — safe to include on any board.
 */

#ifndef CHILD_H
#define CHILD_H

#include "BoardConfig.h"
#include "Protocol.h"

#ifdef BOARD_CHILD

#ifdef BOARD_GIGA_CHILD
  #include "GigaLED.h"       // provides CRGB, leds[], showSafe(), fill_solid()
#elif defined(BOARD_DMX_BRIDGE)
  #include "DmxBridge.h"     // provides CRGB, leds[], dmxBuf, dmxSendFrame()
#endif

// ── Child-specific constants ──────────────────────────────────────────────────

constexpr uint8_t LEDTYPE_WS2812B = 0;
constexpr uint8_t LEDTYPE_WS2811  = 1;
constexpr uint8_t LEDTYPE_APA102  = 2;

// Per-board maximum strings (config UI and EEPROM only;
// all boards always send 8-slot PONGs so the protocol is uniform)
#if defined(BOARD_DMX_BRIDGE)
  constexpr uint8_t CHILD_MAX_STRINGS = 1;  // DMX bridge: 1 "string" = the DMX universe
#elif defined(BOARD_D1MINI)
  constexpr uint8_t CHILD_MAX_STRINGS = 2;
#else  // BOARD_ESP32
  constexpr uint8_t CHILD_MAX_STRINGS = 8;
#endif

constexpr uint8_t EEPROM_MAGIC    = 0xA8;   // bumped: added dataPin to ChildStringCfg
constexpr uint8_t MAX_CHILD_STEPS = 16;

// ── Child config structs ──────────────────────────────────────────────────────

struct ChildStringCfg {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  flags;      // bit 0 = folded (strip goes out and back)
  uint16_t cableMm;    // always 0 — not exposed in config UI
  uint8_t  stripDir;
  uint8_t  dataPin;    // GPIO pin for LED data (ESP32 only; 0 = default GPIO 2)
};

constexpr uint8_t STR_FLAG_FOLDED = 0x01;

#ifdef BOARD_ESP32
  constexpr uint8_t DEFAULT_DATA_PIN = 2;
  constexpr uint8_t ESP32_SAFE_PINS[] = {1, 2, 3, 4, 5, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27};
  constexpr uint8_t ESP32_SAFE_PIN_COUNT = sizeof(ESP32_SAFE_PINS) / sizeof(ESP32_SAFE_PINS[0]);
  void esp32InitLeds();
#endif

struct ChildSelfConfig {
  char           hostname[HOSTNAME_LEN];
  char           altName[CHILD_NAME_LEN];
  char           description[CHILD_DESC_LEN];
  uint8_t        stringCount;
  ChildStringCfg strings[CHILD_MAX_STRINGS];
};

// ── Runner step (stored in RAM for execution) ─────────────────────────────────

struct ChildRunnerStep {
  uint8_t  actionType, r, g, b;
  uint16_t p16a;
  uint8_t  p8a, p8b, p8c, p8d;
  uint16_t durationS;
  uint16_t delayMs;             // canvas-scope: ms to wait before executing
  uint16_t ledStart[MAX_STR_PER_CHILD];
  uint16_t ledEnd[MAX_STR_PER_CHILD];
};  // 38 bytes × 16 = 608 bytes

// ── FastLED pixel array ───────────────────────────────────────────────────────

extern CRGB leds[NUM_LEDS];

// ── Global data (defined in Child.cpp) ───────────────────────────────────────

extern ChildSelfConfig childCfg;

// Volatile action state — written by UDP handler (main thread), read by LED task
extern volatile uint8_t  childActType;
extern volatile uint8_t  childActR;
extern volatile uint8_t  childActG;
extern volatile uint8_t  childActB;
extern volatile uint16_t childActP16a;   // speedMs / periodMs / spawnMs
extern volatile uint8_t  childActP8a;    // r2 / minBri / spacing / palette / cooling / tailLen / density
extern volatile uint8_t  childActP8b;    // g2 / sparking
extern volatile uint8_t  childActP8c;    // b2 / direction
extern volatile uint8_t  childActP8d;    // actionSeqId / decay / fadeSpeed
extern volatile uint8_t  childActSeq;
extern volatile uint16_t childActSt[MAX_STR_PER_CHILD];
extern volatile uint16_t childActEn[MAX_STR_PER_CHILD];
extern volatile uint8_t  childBrightness; // global brightness 0-255

// Runner execution state — written by UDP handler, read by LED task
extern ChildRunnerStep   childRunner[MAX_CHILD_STEPS];
extern volatile uint8_t  childStepCount;
extern volatile uint32_t childRunnerStart;
extern volatile bool     childRunnerArmed;
extern volatile bool     childRunnerActive;
extern volatile uint8_t  childSyncBlink;   // >0 = blink white N times after sync
extern volatile bool     childRunnerLoop;  // true = loop runner, false = stop after last step
extern volatile bool     childBootDone;   // false until bootAnimation() completes

// Parent IP (set when CMD_RUNNER_GO received — used for ACTION_EVENT replies)
extern volatile uint32_t childParentIP;

// Event-pending flags — LED task sets these, main loop sends the UDP packet
extern volatile bool    childEvtPending;
extern volatile uint8_t childEvtType;
extern volatile uint8_t childEvtStep;
extern volatile uint8_t childEvtTotal;
extern volatile uint8_t childEvtEvent;   // 0=started, 1=ended

void sendActionEvent();

// ── Function declarations ─────────────────────────────────────────────────────

void initChildConfig();
void loadChildConfig();
void saveChildConfig();
void clearChildConfig();
void sendPong(IPAddress dest);
void sendStatusResp(IPAddress dest);
void sendChildConfigPage(WiFiClient& c);
void handlePostChildConfig(WiFiClient& c, int contentLen);
void handleFactoryReset(WiFiClient& c);

// URL-encoded form helpers (used by POST /config handler)
uint8_t hexVal(char ch);
int     urlGetInt(const char* body, const char* key, int def);
void    urlGetStr(const char* body, const char* key, char* out, uint8_t maxlen);

#endif  // BOARD_CHILD

#endif  // CHILD_H
