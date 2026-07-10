/*
 * MmwProtocol.h — UDP wire-protocol subset for the MMwave radar node.
 *
 * DUPLICATED from main/Protocol.h by design decision (#909, design doc §4.2):
 * the MMwave sketch builds with its own isolated toolchain and must not
 * include across sketch trees. Wire constants and struct layouts below MUST
 * stay byte-identical to main/Protocol.h — tests/test_mmwave_wire_parity.py
 * fails the suite if they drift.
 */

#ifndef MMW_PROTOCOL_H
#define MMW_PROTOCOL_H

#include <stdint.h>

// ── String/name length constants (parity: main/Protocol.h) ───────────────────

constexpr uint8_t HOSTNAME_LEN      = 10;   // "MMW-XXXX\0"
constexpr uint8_t CHILD_NAME_LEN    = 16;
constexpr uint8_t CHILD_DESC_LEN    = 32;
constexpr uint8_t MAX_STR_PER_CHILD = 8;    // protocol constant — same on all boards

// ── UDP protocol constants (parity: main/Protocol.h) ─────────────────────────

constexpr uint16_t UDP_PORT    = 4210;
constexpr uint16_t UDP_MAGIC   = 0x534C;
constexpr uint8_t  UDP_VERSION = 5;

// Command bytes shared with the fleet (parity: main/Protocol.h)
constexpr uint8_t CMD_PING           = 0x01;
constexpr uint8_t CMD_PONG           = 0x02;
constexpr uint8_t CMD_STATUS_REQ     = 0x40;
constexpr uint8_t CMD_STATUS_RESP    = 0x41;
constexpr uint8_t CMD_OTA_UPDATE     = 0x50;   // parent→child: trigger OTA (URL + sha256 in payload)
constexpr uint8_t CMD_OTA_STATUS     = 0x51;   // child→parent: OTA progress/result

// ── MMwave command range (0x7x — owned by this node type, #910) ──────────────

constexpr uint8_t CMD_MMW_TARGETS = 0x70;   // node→parent: tracked-target frame
constexpr uint8_t CMD_MMW_CONFIG  = 0x71;   // parent→node: reserved (not implemented in v1)

// ── Packed UDP packet structures (parity: main/Protocol.h) ───────────────────

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
  uint8_t    stringCount;               // 0 — the radar node carries no LED strings
  PongString strings[MAX_STR_PER_CHILD];
  uint8_t    fwMajor;
  uint8_t    fwMinor;
  uint8_t    fwPatch;
};  // 10+16+32+1+(9×8)+3 = 134 bytes; total packet = 8+134 = 142

struct __attribute__((packed)) StatusRespPayload {
  uint8_t  activeAction;   // always 0 on the radar node
  uint8_t  runnerActive;   // always 0
  uint8_t  currentStep;    // repurposed: current tracked-target count (0..3)
  uint8_t  wifiRssi;       // absolute magnitude (e.g. 69 → -69 dBm)
  uint32_t uptimeS;
};  // 8 bytes

// OtaStatusPayload — node→parent (2 bytes), CMD_OTA_STATUS (#922).
// Fire-and-forget progress report to the CMD_OTA_UPDATE sender: once per
// phase change + every ≥10% of download progress. Status codes are the
// fleet's OTA_STATUS_* constants (main/OtaUpdate.h; local copies in
// MmwOta.h). Parity with main/Protocol.h is gated by
// tests/test_mmwave_wire_parity.py.
struct __attribute__((packed)) OtaStatusPayload {
  uint8_t status;          // OTA_STATUS_* code (MmwOta.h)
  uint8_t progress;        // 0-100 (%)
};  // 2 bytes; total packet = 8 + 2 = 10

// ── MMwave payloads (new in 0x7x range — this file is their source of truth;
//    the orchestrator handler (#910) must match) ──────────────────────────────

constexpr uint8_t MMW_MAX_TARGETS = 3;   // Rd-03D hard limit (multi-target mode)

struct __attribute__((packed)) MmwTarget {
  int16_t  xMm;       // sensor-frame lateral, mm (+ right of boresight, from radar's PoV)
  int16_t  yMm;       // sensor-frame boresight distance, mm (always ≥ 0 for real targets)
  int16_t  speedCms;  // radial speed, cm/s (sign = toward/away per Rd-03D convention)
  uint16_t resMm;     // Rd-03D "distance resolution" word, mm (diagnostic)
};  // 8 bytes

struct __attribute__((packed)) MmwTargetsPayload {
  uint16_t  seq;                        // monotonic per-boot packet counter
  uint8_t   count;                      // valid entries in targets[] (0..3)
  uint8_t   flags;                      // bit0 = radar-frame parse healthy; rest reserved
  MmwTarget targets[MMW_MAX_TARGETS];   // fixed 3 slots, unused slots zeroed
};  // 4 + 3×8 = 28 bytes; total packet = 8+28 = 36

#endif  // MMW_PROTOCOL_H
