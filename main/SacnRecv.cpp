/*
 * SacnRecv.cpp — sACN E1.31 receiver for DMX bridge (#109).
 *
 * Packet layout (E1.31 / ANSI E1.31-2018):
 *   offset 0  : preamble size      uint16 BE = 0x0010
 *   offset 2  : postamble          uint16 BE = 0x0000
 *   offset 4  : ACN packet ID      "ASC-E1.17\0\0\0"  (12 bytes)
 *   offset 16 : RLP flags+length   uint16 BE
 *   offset 18 : root vector        uint32 BE = 0x00000004 (E1.31 data)
 *   offset 22 : sender CID         16 bytes
 *   offset 38 : framing flags+len  uint16 BE
 *   offset 40 : framing vector     uint32 BE = 0x00000002 (E1.31 data)
 *   offset 44 : source name        64 bytes (UTF-8 NUL-terminated)
 *   offset 108: priority           uint8 (0..200, default 100)
 *   offset 109: sync universe      uint16 BE
 *   offset 111: sequence number    uint8
 *   offset 112: options            uint8 (bit 6 = preview, bit 7 = stream-terminated)
 *   offset 113: universe           uint16 BE
 *   offset 115: DMP flags+length   uint16 BE
 *   offset 117: DMP vector         uint8 = 0x02 (set property)
 *   offset 118: address type       uint8 = 0xA1
 *   offset 119: first prop addr    uint16 BE = 0x0000
 *   offset 121: address increment  uint16 BE = 0x0001
 *   offset 123: prop value count   uint16 BE = 1 + dmx slots
 *   offset 125: start code         uint8 = 0x00 for null start code
 *   offset 126: DMX data           up to 512 bytes
 */

#include "SacnRecv.h"

#ifdef BOARD_DMX_BRIDGE

#include <Arduino.h>
#include <WiFi.h>
#include <string.h>
#include "DmxBridge.h"
#include "Globals.h"

WiFiUDP            sacnUDP;
volatile uint32_t  sacnRxCount = 0;
volatile uint32_t  sacnPps     = 0;
char               sacnLastSender[16] = "";

static uint8_t  sacnBuf[SACN_BUF_SIZE];
static uint32_t _ppsCount = 0;
static unsigned long _ppsTime = 0;

// Source-priority arbitration state. We track the CID of whichever
// source is currently winning (highest priority within the timeout
// window). Lower priority sources are ignored until the active source
// goes silent for SACN_SOURCE_TIMEOUT_MS.
static uint8_t  _winningCID[16] = {0};
static uint8_t  _winningPrio    = 0;
static uint8_t  _lastSeq        = 0;
static unsigned long _lastRxMs  = 0;
static bool     _haveWinner     = false;

constexpr unsigned long SACN_SOURCE_TIMEOUT_MS = 2500;  // 2.5 s per E1.31 spec

static const uint8_t ACN_ID[12] = {
  'A','S','C','-','E','1','.','1','7', 0, 0, 0
};

// Multicast group for a universe: 239.255.<hi>.<lo>
static IPAddress sacnGroupForUniverse(uint16_t uni) {
  uint8_t hi = (uni >> 8) & 0xFF;
  uint8_t lo = uni & 0xFF;
  return IPAddress(239, 255, hi, lo);
}

static uint16_t configuredUniverse() {
  // Match Art-Net side: universe = subnet<<4 | universe (0..15 each).
  return ((uint16_t)dmxCfg.subnet << 4) | (dmxCfg.universe & 0x0F);
}

void sacnInit() {
  sacnUDP.begin(SACN_PORT);
  uint16_t uni = configuredUniverse();
  IPAddress grp = sacnGroupForUniverse(uni);
#if defined(WiFi_h) || defined(ESP32) || defined(ARDUINO_ARCH_MBED)
  // beginMulticast varies by core; a no-op on Mbed is acceptable —
  // many routers forward 239.255.0.0/16 to the bridge anyway.
  sacnUDP.beginMulticast(grp, SACN_PORT);
#else
  (void)grp;
#endif
  _ppsTime = millis();
  if (Serial) {
    Serial.print(F("[sACN] Listening on UDP 5568, universe "));
    Serial.println(uni);
  }
}

void sacnRejoin() {
  sacnUDP.stop();
  memset(_winningCID, 0, sizeof(_winningCID));
  _winningPrio = 0;
  _haveWinner = false;
  sacnInit();
}

void pollSacn() {
  unsigned long now = millis();
  if (now - _ppsTime >= 1000) {
    sacnPps = _ppsCount;
    _ppsCount = 0;
    _ppsTime = now;
  }
  // Source timeout: if the active winner has been silent too long,
  // reset so a lower-priority source can take over.
  if (_haveWinner && (now - _lastRxMs) > SACN_SOURCE_TIMEOUT_MS) {
    _haveWinner = false;
    _winningPrio = 0;
  }

  for (int attempt = 0; attempt < 8; attempt++) {
    int plen = sacnUDP.parsePacket();
    if (plen <= 0) return;
    int n = sacnUDP.read(sacnBuf, sizeof(sacnBuf));
    if (n < 126) continue;

    // ── Validate framing ────────────────────────────────
    if (sacnBuf[0] != 0x00 || sacnBuf[1] != 0x10) continue;       // preamble size
    if (sacnBuf[2] != 0x00 || sacnBuf[3] != 0x00) continue;       // postamble
    if (memcmp(sacnBuf + 4, ACN_ID, 12) != 0) continue;            // ACN ID
    // Root vector @18, must be 0x00000004
    if (sacnBuf[18] != 0x00 || sacnBuf[19] != 0x00 ||
        sacnBuf[20] != 0x00 || sacnBuf[21] != 0x04) continue;
    // Framing vector @40, must be 0x00000002
    if (sacnBuf[40] != 0x00 || sacnBuf[41] != 0x00 ||
        sacnBuf[42] != 0x00 || sacnBuf[43] != 0x02) continue;
    // DMP vector @117 must be 0x02 (set property)
    if (sacnBuf[117] != 0x02) continue;
    if (sacnBuf[125] != 0x00) continue;                            // null start code only

    uint16_t pktUniverse = ((uint16_t)sacnBuf[113] << 8) | sacnBuf[114];
    if (pktUniverse != configuredUniverse()) continue;

    uint8_t prio = sacnBuf[108];
    uint8_t seq  = sacnBuf[111];
    uint8_t opts = sacnBuf[112];
    bool streamTerm = (opts & 0x40) != 0;

    // Property value count includes the 0x00 start code byte; subtract
    // it to get the number of DMX slots actually present.
    uint16_t pvCount = ((uint16_t)sacnBuf[123] << 8) | sacnBuf[124];
    uint16_t slots = (pvCount > 0) ? (pvCount - 1) : 0;
    if (slots > 512) slots = 512;
    if (n < 126 + (int)slots) continue;

    // ── Source arbitration ──────────────────────────────
    bool sameSource = _haveWinner && memcmp(_winningCID, sacnBuf + 22, 16) == 0;

    if (streamTerm) {
      // E1.31 stream-terminated bit: source is signalling end of stream.
      if (sameSource) {
        _haveWinner = false;
        _winningPrio = 0;
      }
      continue;
    }

    if (sameSource) {
      // Out-of-order detection — accept if seq is "newer" within a
      // ±20 wrap window (per E1.31 §6.7.2). Stale frames are dropped
      // silently to avoid jitter.
      int8_t delta = (int8_t)(seq - _lastSeq);
      if (delta < -20) continue;
    } else {
      // Different source — only take over if its priority is strictly
      // greater than the current winner, or there is no winner.
      if (_haveWinner && prio < _winningPrio) continue;
      memcpy(_winningCID, sacnBuf + 22, 16);
      _winningPrio = prio;
      _haveWinner = true;
    }
    _lastSeq = seq;
    _lastRxMs = now;

    // ── Copy into universe buffer ───────────────────────
    if (slots > 0) memcpy(dmxBuf + 1, sacnBuf + 126, slots);
    sacnRxCount++;
    _ppsCount++;
    IPAddress sender = sacnUDP.remoteIP();
    snprintf(sacnLastSender, sizeof(sacnLastSender), "%d.%d.%d.%d",
             sender[0], sender[1], sender[2], sender[3]);
  }
}

#endif // BOARD_DMX_BRIDGE
