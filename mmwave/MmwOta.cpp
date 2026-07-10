/*
 * MmwOta.cpp — streaming HTTP download → SHA-256 → Update (inactive OTA bank).
 * Fixed buffers only; no String on the data path.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>
#include <Update.h>
#include <mbedtls/sha256.h>
#include "MmwOta.h"
#include "MmwUdp.h"

static uint8_t otaState    = OTA_STATUS_IDLE;
static uint8_t otaProgress = 0;   // 0-100 (%)

// ── CMD_OTA_STATUS (0x51) reporting (#922) ───────────────────────────────────
// Fire-and-forget: one 10-byte datagram per phase change plus every ≥10%
// of download progress, sent to the node that triggered the OTA — the same
// contract as main/OtaUpdate.cpp's otaSetStatus/otaSendStatus. The socket
// lives in MmwUdp (mmwUdpSendOtaStatus); this module only owns the target.

static IPAddress otaReportIp;          // default 0.0.0.0 → reporting disabled
static bool      otaReportKnown = false;

void mmwOtaSetReportTarget(IPAddress ip) {
  otaReportIp    = ip;
  otaReportKnown = true;
}

static void otaSendStatus() {
  if (!otaReportKnown) return;
  mmwUdpSendOtaStatus(otaReportIp, otaState, otaProgress);
}

// Assign + report in one step — every phase transition below goes through
// this so the parent sees downloading/verifying/applying/terminal states.
static void otaSetState(uint8_t st) {
  otaState = st;
  otaSendStatus();
}

static bool shaWanted(const char* sha) {
  if (!sha || sha[0] == '\0') return false;
  for (const char* p = sha; *p; p++) if (*p != '0') return true;
  return false;   // all-zero placeholder → skip verification
}

static void hexLower(const uint8_t* digest, char* out65) {
  static const char* hx = "0123456789abcdef";
  for (int i = 0; i < 32; i++) {
    out65[i * 2]     = hx[digest[i] >> 4];
    out65[i * 2 + 1] = hx[digest[i] & 0x0F];
  }
  out65[64] = '\0';
}

bool mmwOtaStart(const char* url, const char* expectedSha256) {
  otaProgress = 0;
  if (WiFi.status() != WL_CONNECTED) { otaSetState(OTA_STATUS_FAILED); return false; }

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, url)) { otaSetState(OTA_STATUS_FAILED); return false; }
  http.setTimeout(15000);

  otaSetState(OTA_STATUS_DOWNLOADING);
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    if (Serial) { Serial.print(F("MMW OTA: HTTP ")); Serial.println(code); }
    http.end(); otaSetState(OTA_STATUS_FAILED); return false;
  }

  int len = http.getSize();
  if (len <= 0) { http.end(); otaSetState(OTA_STATUS_FAILED); return false; }

  if (!Update.begin(len)) {
    if (Serial) Serial.println(F("MMW OTA: Update.begin failed (partition?)"));
    http.end(); otaSetState(OTA_STATUS_FAILED); return false;
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);

  WiFiClient* stream = http.getStreamPtr();
  static uint8_t buf[1024];
  int remaining = len;
  uint8_t lastReport = 0;           // #922 — last CMD_OTA_STATUS progress %
  uint32_t lastData = millis();
  while (remaining > 0) {
    size_t avail = stream->available();
    if (avail == 0) {
      if (!stream->connected() || millis() - lastData > 15000) break;
      delay(1);
      continue;
    }
    size_t n = stream->readBytes(buf, avail > sizeof(buf) ? sizeof(buf) : avail);
    if (n == 0) continue;
    lastData = millis();
    mbedtls_sha256_update(&sha, buf, n);
    if (Update.write(buf, n) != n) { remaining = -1; break; }
    remaining -= (int)n;
    otaProgress = (uint8_t)((int32_t)(len - remaining) * 100L / len);
    if ((uint8_t)(otaProgress - lastReport) >= 10) {
      lastReport = otaProgress;
      otaSendStatus();              // #922 — ≤10 datagrams per download
    }
  }

  uint8_t digest[32];
  mbedtls_sha256_finish(&sha, digest);
  mbedtls_sha256_free(&sha);
  http.end();

  if (remaining != 0) {
    if (Serial) Serial.println(F("MMW OTA: download incomplete — aborting"));
    Update.abort(); otaSetState(OTA_STATUS_FAILED); return false;
  }

  if (shaWanted(expectedSha256)) {
    otaSetState(OTA_STATUS_VERIFYING);
    char actual[65];
    hexLower(digest, actual);
    bool match = true;
    for (int i = 0; i < 64; i++) {
      char e = expectedSha256[i];
      if (e >= 'A' && e <= 'F') e = (char)(e - 'A' + 'a');
      if (e != actual[i]) { match = false; break; }
    }
    if (!match) {
      if (Serial) Serial.println(F("MMW OTA: sha256 MISMATCH — aborting, not applying"));
      Update.abort(); otaSetState(OTA_STATUS_FAILED); return false;
    }
  } else if (Serial) {
    Serial.println(F("MMW OTA: no sha256 provided — applying unverified"));
  }

  otaSetState(OTA_STATUS_APPLYING);
  if (!Update.end(true)) {
    if (Serial) { Serial.print(F("MMW OTA: finalize error ")); Serial.println(Update.errorString()); }
    otaSetState(OTA_STATUS_FAILED); return false;
  }
  if (Serial) Serial.println(F("MMW OTA: staged OK — rebooting into new image"));
  otaProgress = 100;
  otaSetState(OTA_STATUS_SUCCESS);
  return true;
}

uint8_t mmwOtaStatus() { return otaState; }
