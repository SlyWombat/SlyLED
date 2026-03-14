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
constexpr uint8_t  UDP_VERSION = 2;

// Command bytes
constexpr uint8_t CMD_PING        = 0x01;
constexpr uint8_t CMD_PONG        = 0x02;
constexpr uint8_t CMD_ACTION      = 0x10;
constexpr uint8_t CMD_ACTION_STOP = 0x11;
constexpr uint8_t CMD_LOAD_STEP   = 0x20;   // parent→child: load one runner step
constexpr uint8_t CMD_LOAD_ACK    = 0x21;   // child→parent: step received
constexpr uint8_t CMD_RUNNER_GO   = 0x30;   // parent→child: start runner at epoch
constexpr uint8_t CMD_RUNNER_STOP = 0x31;   // parent→child: stop runner
constexpr uint8_t CMD_STATUS_REQ  = 0x40;
constexpr uint8_t CMD_STATUS_RESP = 0x41;

// Action type codes (uint8_t to avoid Mbed prototype-generator issues with enums)
constexpr uint8_t ACT_OFF   = 0;
constexpr uint8_t ACT_SOLID = 1;
constexpr uint8_t ACT_FLASH = 2;
constexpr uint8_t ACT_WIPE  = 3;

// Wipe / strip direction codes
constexpr uint8_t DIR_E = 0;
constexpr uint8_t DIR_N = 1;
constexpr uint8_t DIR_W = 2;
constexpr uint8_t DIR_S = 3;

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
};  // 10+16+32+1+(9×8)=131 bytes; total packet = 8+131 = 139

struct __attribute__((packed)) StatusRespPayload {
  uint8_t  activeAction;
  uint8_t  runnerActive;
  uint8_t  currentStep;
  uint8_t  wifiRssi;   // absolute magnitude (e.g. 69 → -69 dBm)
  uint32_t uptimeS;
};  // 8 bytes

struct __attribute__((packed)) ActionPayload {
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t onMs;
  uint16_t offMs;
  uint8_t  wipeDir;
  uint8_t  wipeSpeedPct;
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 10+8+8 = 26 bytes

struct __attribute__((packed)) LoadStepPayload {
  uint8_t  stepIndex;
  uint8_t  totalSteps;
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t onMs, offMs;
  uint8_t  wipeDir;
  uint8_t  wipeSpeedPct;
  uint16_t durationS;
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 14+8+8 = 30 bytes; total packet = 8+30 = 38

#endif  // PROTOCOL_H
