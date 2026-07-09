/*
 * mmwave.ino — SlyLED MMwave radar node (issue #909, design doc
 * docs/design/mmwave_tracking.md).
 *
 * Board:  ESP32-C61-DevKitC-1-N8R2  (fqbn esp32:esp32:esp32c61, core ≥3.3.5,
 *         isolated toolchain — see arduino-cli-mmwave.yaml at repo root)
 * Radar:  Ai-Thinker Rd-03D on UART1 (GPIO4 RX / GPIO5 TX, 256000 8N1)
 *
 * Behaviour: parse Rd-03D multi-target frames; report sensor-frame targets
 * to the orchestrator as CMD_MMW_TARGETS (0x70) — on every fresh radar frame
 * while targets are present (rate-capped), plus a 1 Hz empty keepalive.
 * Speaks the common fleet protocol: boot-broadcast PONG, PING→PONG,
 * STATUS_REQ→STATUS_RESP, CMD_OTA_UPDATE with sha256 verification.
 *
 * Deliberately a separate sketch from main/ (operator decision, design doc
 * v2): shares wire behaviour, not builds. Wire structs are duplicated in
 * MmwProtocol.h and guarded by tests/test_mmwave_wire_parity.py.
 */

#include "MmwConfig.h"
#include "MmwNet.h"
#include "MmwOta.h"
#include "MmwProtocol.h"
#include "MmwUart.h"
#include "MmwUdp.h"
#include "version.h"

static uint32_t lastSendMs      = 0;
static uint32_t lastHeartbeatMs = 0;
static uint32_t lastStatsMs     = 0;

void setup() {
  Serial.begin(115200);
  delay(200);
  if (Serial) {
    Serial.print(F("SlyLED MMwave node v"));
    Serial.print(MMW_MAJOR); Serial.print('.');
    Serial.print(MMW_MINOR); Serial.print('.');
    Serial.println(MMW_PATCH);
  }

  mmwNetBegin();     // WiFi (secrets + NVS override), hostname, NTP
  mmwUdpBegin();     // bind 4210, broadcast discovery PONG
  mmwUartBegin();    // radar UART + multi-target mode
}

void loop() {
  mmwNetPoll();      // WiFi reconnect supervision
  mmwUartPoll();     // pump radar bytes
  mmwUdpPoll();      // PING / STATUS / OTA

  uint32_t now = millis();

  MmwTarget targets[MMW_MAX_TARGETS];
  bool fresh = false;
  uint8_t count = mmwUartTargets(targets, &fresh);
  uint8_t flags = mmwUartHealthy() ? 0x01 : 0x00;

  // Fresh radar frame with people in view → report (rate-capped).
  if (fresh && count > 0 && now - lastSendMs >= MMW_MIN_REPORT_MS) {
    mmwUdpSendTargets(targets, count, flags);
    lastSendMs      = now;
    lastHeartbeatMs = now;
  }

  // Idle keepalive so the orchestrator can distinguish "no people" from "node gone".
  if (now - lastHeartbeatMs >= MMW_HEARTBEAT_MS) {
    MmwTarget empty[MMW_MAX_TARGETS];
    memset(empty, 0, sizeof(empty));
    mmwUdpSendTargets(empty, 0, flags);
    lastHeartbeatMs = now;
  }

  // Bench-friendly serial stats every 10 s (#908).
  if (Serial && now - lastStatsMs >= 10000) {
    lastStatsMs = now;
    Serial.print(F("MMW: frames=")); Serial.print(mmwUartFrameCount());
    Serial.print(F(" errs="));       Serial.print(mmwUartErrorCount());
    Serial.print(F(" targets="));    Serial.print(count);
    Serial.print(F(" healthy="));    Serial.println(flags & 0x01 ? 1 : 0);
  }
}
