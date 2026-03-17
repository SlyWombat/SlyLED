/*
 * Protocol.h — UDP wire-protocol constants and packed packet structures.
 *
 * Shared across all boards.  No WiFi types — safe to include anywhere.
 */

#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>

// ── String/name length constants ──────────────────────────────────────────────

constexpr uint8_t HOSTNAME_LEN      = 10;   // "SLYC-XXXX\0"
constexpr uint8_t CHILD_NAME_LEN    = 16;
constexpr uint8_t CHILD_DESC_LEN    = 32;
constexpr uint8_t MAX_STR_PER_CHILD = 8;    // protocol constant — same on all boards

// ── UDP protocol constants ────────────────────────────────────────────────────

constexpr uint16_t UDP_PORT    = 4210;
constexpr uint16_t UDP_MAGIC   = 0x534C;
constexpr uint8_t  UDP_VERSION = 3;         // v3: 9 action types, generic params

// Command bytes
constexpr uint8_t CMD_PING           = 0x01;
constexpr uint8_t CMD_PONG           = 0x02;
constexpr uint8_t CMD_ACTION         = 0x10;
constexpr uint8_t CMD_ACTION_STOP    = 0x11;
constexpr uint8_t CMD_ACTION_EVENT   = 0x12;   // child→parent: action started/ended
constexpr uint8_t CMD_LOAD_STEP      = 0x20;   // parent→child: load one runner step
constexpr uint8_t CMD_LOAD_ACK       = 0x21;   // child→parent: step received
constexpr uint8_t CMD_SET_BRIGHTNESS = 0x22;   // parent→child: global brightness (1 byte)
constexpr uint8_t CMD_RUNNER_GO      = 0x30;   // parent→child: start runner at epoch
constexpr uint8_t CMD_RUNNER_STOP    = 0x31;   // parent→child: stop runner
constexpr uint8_t CMD_STATUS_REQ     = 0x40;
constexpr uint8_t CMD_STATUS_RESP    = 0x41;

// ── Action type codes ─────────────────────────────────────────────────────────
// (uint8_t — avoids Mbed prototype-generator issues with enums)

constexpr uint8_t ACT_BLACKOUT = 0;   // all LEDs off
constexpr uint8_t ACT_SOLID    = 1;   // solid colour
constexpr uint8_t ACT_FADE     = 2;   // linear fade between two colours
constexpr uint8_t ACT_BREATHE  = 3;   // single colour brightness sine wave
constexpr uint8_t ACT_CHASE    = 4;   // theater chase pattern
constexpr uint8_t ACT_RAINBOW  = 5;   // HSV rainbow cycle (8 palettes)
constexpr uint8_t ACT_FIRE     = 6;   // fire / Perlin noise effect
constexpr uint8_t ACT_COMET    = 7;   // shooting comet with fading tail
constexpr uint8_t ACT_TWINKLE  = 8;   // random sparkle + fade
constexpr uint8_t ACT_OFF      = 0;   // alias for ACT_BLACKOUT

// Legacy aliases (for backward compatibility in existing code)
constexpr uint8_t ACT_FLASH    = 2;   // old name for ACT_FADE slot
constexpr uint8_t ACT_WIPE     = 3;   // old name for ACT_BREATHE slot

// Direction codes
constexpr uint8_t DIR_E = 0;
constexpr uint8_t DIR_N = 1;
constexpr uint8_t DIR_W = 2;
constexpr uint8_t DIR_S = 3;

// Rainbow palette IDs
constexpr uint8_t PAL_CLASSIC = 0;
constexpr uint8_t PAL_OCEAN   = 1;
constexpr uint8_t PAL_LAVA    = 2;
constexpr uint8_t PAL_FOREST  = 3;
constexpr uint8_t PAL_PARTY   = 4;
constexpr uint8_t PAL_HEAT    = 5;
constexpr uint8_t PAL_COOL    = 6;
constexpr uint8_t PAL_PASTEL  = 7;

// ── Packed UDP packet structures ──────────────────────────────────────────────

struct __attribute__((packed)) UdpHeader {
  uint16_t magic;
  uint8_t  version;
  uint8_t  cmd;
  uint32_t epoch;
};  // 8 bytes

struct __attribute__((packed)) PongString {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};  // 9 bytes

struct __attribute__((packed)) PongPayload {
  char       hostname[HOSTNAME_LEN];
  char       altName[CHILD_NAME_LEN];
  char       description[CHILD_DESC_LEN];
  uint8_t    stringCount;
  PongString strings[MAX_STR_PER_CHILD];
  uint8_t    fwMajor;               // firmware version (added v4.0)
  uint8_t    fwMinor;
};  // 10+16+32+1+(9×8)+2=133 bytes; total packet = 8+133 = 141

struct __attribute__((packed)) StatusRespPayload {
  uint8_t  activeAction;
  uint8_t  runnerActive;
  uint8_t  currentStep;
  uint8_t  wifiRssi;   // absolute magnitude (e.g. 69 → -69 dBm)
  uint32_t uptimeS;
};  // 8 bytes

// ActionPayload — generic parameter layout (26 bytes, wire-compatible with v2)
//
// Per-type interpretation of param fields:
//   Solid:   r,g,b = colour; params unused
//   Fade:    r,g,b = colour1; p8a/p8b/p8c = r2,g2,b2; p16a = speedMs
//   Breathe: r,g,b = colour; p16a = periodMs; p8a = minBrightness%
//   Chase:   r,g,b = colour; p16a = speedMs; p8a = spacing; p8c = direction
//   Rainbow: p16a = speedMs; p8a = paletteId; p8c = direction
//   Fire:    r,g,b = base tint; p16a = speedMs; p8a = cooling; p8b = sparking
//   Comet:   r,g,b = colour; p16a = speedMs; p8a = tailLen; p8c = direction; p8d = decay%
//   Twinkle: r,g,b = colour; p16a = spawnMs; p8a = density; p8d = fadeSpeed
//   Blackout: all zeros
struct __attribute__((packed)) ActionPayload {
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t p16a;            // speedMs / periodMs / spawnIntervalMs
  uint8_t  p8a;             // r2 / minBri / spacing / paletteId / cooling / tailLen / density
  uint8_t  p8b;             // g2 / sparking
  uint8_t  p8c;             // b2 / direction
  uint8_t  p8d;             // actionSeqId / decay / fadeSpeed
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 10+8+8 = 26 bytes

struct __attribute__((packed)) LoadStepPayload {
  uint8_t  stepIndex;
  uint8_t  totalSteps;
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t p16a;
  uint8_t  p8a, p8b, p8c, p8d;
  uint16_t durationS;
  uint16_t delayMs;             // canvas-scope: per-child start delay (0 = immediate)
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 16+8+8 = 32 bytes; total packet = 8+32 = 40

// ActionEventPayload — child→parent (4 bytes)
struct __attribute__((packed)) ActionEventPayload {
  uint8_t  actionType;
  uint8_t  stepIndex;
  uint8_t  totalSteps;
  uint8_t  event;          // 0=started, 1=ended
};

#endif  // PROTOCOL_H
