/*
 * Child.h — Child node (ESP32 / D1 Mini) config structs, volatile action state,
 *           runner state, and function declarations.
 *
 * All content is guarded by #ifdef BOARD_FASTLED — safe to include on any board.
 */

#ifndef CHILD_H
#define CHILD_H

#include "BoardConfig.h"
#include "Protocol.h"

#ifdef BOARD_FASTLED

// ── Child-specific constants ──────────────────────────────────────────────────

constexpr uint8_t LEDTYPE_WS2812B = 0;
constexpr uint8_t LEDTYPE_WS2811  = 1;
constexpr uint8_t LEDTYPE_APA102  = 2;

// Per-board maximum strings (config UI and EEPROM only;
// all boards always send 8-slot PONGs so the protocol is uniform)
#if defined(BOARD_D1MINI)
  constexpr uint8_t CHILD_MAX_STRINGS = 2;
#else  // BOARD_ESP32
  constexpr uint8_t CHILD_MAX_STRINGS = 8;
#endif

constexpr uint8_t EEPROM_MAGIC    = 0xA6;   // bump whenever ChildSelfConfig layout changes
constexpr uint8_t MAX_CHILD_STEPS = 16;

// ── Child config structs ──────────────────────────────────────────────────────

struct ChildStringCfg {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;   // always 0 — not exposed in config UI
  uint16_t cableMm;    // always 0 — not exposed in config UI
  uint8_t  stripDir;
};

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
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 20 bytes × 16 = 320 bytes

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
extern volatile uint8_t  childActSt[MAX_STR_PER_CHILD];
extern volatile uint8_t  childActEn[MAX_STR_PER_CHILD];
extern volatile uint8_t  childBrightness; // global brightness 0-255

// Runner execution state — written by UDP handler, read by LED task
extern ChildRunnerStep   childRunner[MAX_CHILD_STEPS];
extern volatile uint8_t  childStepCount;
extern volatile uint32_t childRunnerStart;
extern volatile bool     childRunnerArmed;
extern volatile bool     childRunnerActive;
extern volatile uint8_t  childSyncBlink;   // >0 = blink white N times after sync

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

#endif  // BOARD_FASTLED

#endif  // CHILD_H
