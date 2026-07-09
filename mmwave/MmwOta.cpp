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

static uint8_t otaState = 0;   // 0 idle 1 downloading 2 verifying 3 applying 4 error

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
  if (WiFi.status() != WL_CONNECTED) { otaState = 4; return false; }

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, url)) { otaState = 4; return false; }
  http.setTimeout(15000);

  otaState = 1;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    if (Serial) { Serial.print(F("MMW OTA: HTTP ")); Serial.println(code); }
    http.end(); otaState = 4; return false;
  }

  int len = http.getSize();
  if (len <= 0) { http.end(); otaState = 4; return false; }

  if (!Update.begin(len)) {
    if (Serial) Serial.println(F("MMW OTA: Update.begin failed (partition?)"));
    http.end(); otaState = 4; return false;
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);

  WiFiClient* stream = http.getStreamPtr();
  static uint8_t buf[1024];
  int remaining = len;
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
  }

  uint8_t digest[32];
  mbedtls_sha256_finish(&sha, digest);
  mbedtls_sha256_free(&sha);
  http.end();

  if (remaining != 0) {
    if (Serial) Serial.println(F("MMW OTA: download incomplete — aborting"));
    Update.abort(); otaState = 4; return false;
  }

  if (shaWanted(expectedSha256)) {
    otaState = 2;
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
      Update.abort(); otaState = 4; return false;
    }
  } else if (Serial) {
    Serial.println(F("MMW OTA: no sha256 provided — applying unverified"));
  }

  otaState = 3;
  if (!Update.end(true)) {
    if (Serial) { Serial.print(F("MMW OTA: finalize error ")); Serial.println(Update.errorString()); }
    otaState = 4; return false;
  }
  if (Serial) Serial.println(F("MMW OTA: staged OK — rebooting into new image"));
  otaState = 0;
  return true;
}

uint8_t mmwOtaStatus() { return otaState; }
