/*
 * MmwUart.cpp — Rd-03D frame parser. Zero-alloc, integer-only (repo firmware
 * constraints). State machine tolerates desync: hunts for the AA FF 03 00
 * header byte-by-byte, validates the 55 CC tail before accepting a frame.
 */

#include <Arduino.h>
#include "MmwConfig.h"
#include "MmwUart.h"

// Rd-03D command frames (manual §"command protocol"; ACKs are drained, not parsed).
static const uint8_t CMD_MULTI_TARGET[] = {
  0xFD, 0xFC, 0xFB, 0xFA, 0x02, 0x00, 0x90, 0x00, 0x04, 0x03, 0x02, 0x01
};

static constexpr uint8_t FRAME_LEN = 30;   // 4 hdr + 3×8 targets + 2 tail

static uint8_t  frameBuf[FRAME_LEN];
static uint8_t  framePos      = 0;
static bool     inFrame       = false;

static MmwTarget latest[MMW_MAX_TARGETS];
static uint8_t   latestCount  = 0;
static bool      latestFresh  = false;
static uint32_t  lastFrameMs  = 0;
static uint32_t  frameCount   = 0;
static uint32_t  errorCount   = 0;

// Decode the Rd-03D signed-magnitude word: MSB set → +(raw & 0x7FFF), else −raw.
static int16_t mmwSigned(uint16_t raw) {
  if (raw & 0x8000) return (int16_t)(raw & 0x7FFF);
  return (int16_t)(-(int16_t)raw);
}

static uint16_t rdU16(const uint8_t* p) {
  return (uint16_t)(p[0] | (p[1] << 8));
}

static void acceptFrame() {
  uint8_t count = 0;
  for (uint8_t i = 0; i < MMW_MAX_TARGETS; i++) {
    const uint8_t* t = &frameBuf[4 + i * 8];
    uint16_t rx = rdU16(t);
    uint16_t ry = rdU16(t + 2);
    uint16_t rs = rdU16(t + 4);
    uint16_t rr = rdU16(t + 6);
    MmwTarget& o = latest[i];
    if (rx == 0 && ry == 0 && rs == 0 && rr == 0) {
      o.xMm = 0; o.yMm = 0; o.speedCms = 0; o.resMm = 0;   // empty slot
      continue;
    }
    o.xMm      = mmwSigned(rx);
    o.yMm      = mmwSigned(ry);
    o.speedCms = mmwSigned(rs);
    o.resMm    = rr;
    count++;
  }
  latestCount = count;
  latestFresh = true;
  lastFrameMs = millis();
  frameCount++;
}

void mmwUartBegin() {
  Serial1.begin(MMW_UART_BAUD, SERIAL_8N1, MMW_UART_RX_PIN, MMW_UART_TX_PIN);
  delay(50);
  // Ask for multi-target trajectory mode. The module answers with an ACK
  // frame (FD FC FB FA ...) which the parser below simply hunts past.
  Serial1.write(CMD_MULTI_TARGET, sizeof(CMD_MULTI_TARGET));
  Serial1.flush();
  if (Serial) Serial.println(F("MMW: UART up @256000, multi-target mode requested"));
}

void mmwUartPoll() {
  while (Serial1.available() > 0) {
    uint8_t b = (uint8_t)Serial1.read();

    if (!inFrame) {
      // Hunt the 4-byte header AA FF 03 00.
      if      (framePos == 0 && b == 0xAA) framePos = 1;
      else if (framePos == 1 && b == 0xFF) framePos = 2;
      else if (framePos == 2 && b == 0x03) framePos = 3;
      else if (framePos == 3 && b == 0x00) {
        frameBuf[0] = 0xAA; frameBuf[1] = 0xFF; frameBuf[2] = 0x03; frameBuf[3] = 0x00;
        framePos = 4;
        inFrame  = true;
      }
      else framePos = (b == 0xAA) ? 1 : 0;   // restart hunt (byte may itself be a header start)
      continue;
    }

    frameBuf[framePos++] = b;
    if (framePos < FRAME_LEN) continue;

    // Full candidate frame — validate tail.
    inFrame  = false;
    framePos = 0;
    if (frameBuf[FRAME_LEN - 2] == 0x55 && frameBuf[FRAME_LEN - 1] == 0xCC) {
      acceptFrame();
    } else {
      errorCount++;   // desync or interleaved ACK — resume header hunt
    }
  }
}

uint8_t mmwUartTargets(MmwTarget out[MMW_MAX_TARGETS], bool* fresh) {
  for (uint8_t i = 0; i < MMW_MAX_TARGETS; i++) out[i] = latest[i];
  if (fresh) { *fresh = latestFresh; }
  latestFresh = false;
  return latestCount;
}

bool mmwUartHealthy() {
  return frameCount > 0 && (millis() - lastFrameMs) < MMW_FRAME_STALE_MS;
}

uint32_t mmwUartFrameCount() { return frameCount; }
uint32_t mmwUartErrorCount() { return errorCount; }
