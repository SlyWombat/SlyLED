/*
 * OtaUpdate.cpp — OTA firmware update implementation.
 *
 * ESP32 / gyro: manual HTTPClient download into dual OTA partitions
 *   (ota_0 / ota_1) with streaming SHA-256 verification (mbedTLS).
 * D1 Mini: manual HTTPClient download, single-slot Updater with streaming
 *   SHA-256 verification (BearSSL). The final chunk is held back until the
 *   hash matches so a mismatch can still abort (ESP8266 has no
 *   Update.abort()).
 * Giga R1: Not supported (stubs only).
 */

#include "BoardConfig.h"
#include "OtaUpdate.h"

#if defined(BOARD_CHILD) || defined(BOARD_GYRO)

#include "version.h"
#include <Arduino.h>

volatile uint8_t otaStatus   = OTA_STATUS_IDLE;
volatile uint8_t otaProgress = 0;

#if defined(BOARD_ESP32) || defined(BOARD_GYRO) || defined(BOARD_D1MINI)

// ── SHA-256 helpers (#890) ───────────────────────────────────────────────────

// True when a real hash was supplied (non-empty and not all zeros).
static bool otaShaExpected(const char* sha) {
    if (!sha || !sha[0]) return false;
    for (const char* p = sha; *p; p++)
        if (*p != '0') return true;
    return false;
}

// Case-insensitive compare of a 32-byte digest against a 64-char hex string.
static bool otaShaMatches(const uint8_t* digest, const char* expected) {
    if (strlen(expected) != 64) return false;
    static const char hexDigits[] = "0123456789abcdef";
    for (uint8_t i = 0; i < 32; i++) {
        char eh = expected[2 * i];
        char el = expected[2 * i + 1];
        if (eh >= 'A' && eh <= 'F') eh = (char)(eh + 32);
        if (el >= 'A' && el <= 'F') el = (char)(el + 32);
        if (eh != hexDigits[digest[i] >> 4] || el != hexDigits[digest[i] & 0x0F])
            return false;
    }
    return true;
}

// Shared anti-rollback gate: compare full 3-part version.
static bool otaVersionAllowed(uint8_t newMajor, uint8_t newMinor, uint8_t newPatch) {
    uint32_t newVer = (uint32_t)newMajor * 10000 + (uint32_t)newMinor * 100 + newPatch;
    uint32_t curVer = (uint32_t)APP_MAJOR * 10000 + (uint32_t)APP_MINOR * 100 + APP_PATCH;
    if (newVer <= curVer) {
        if (Serial) Serial.printf("OTA: rejected v%d.%d.%d (current v%d.%d.%d)\n",
                                   newMajor, newMinor, newPatch, APP_MAJOR, APP_MINOR, APP_PATCH);
        otaStatus = OTA_STATUS_REJECTED;
        return false;
    }
    return true;
}

static constexpr unsigned long OTA_STALL_TIMEOUT_MS = 15000;  // abort if stream stalls

#endif  // BOARD_ESP32 || BOARD_GYRO || BOARD_D1MINI

// ── ESP32 OTA ────────────────────────────────────────────────────────────────

#if defined(BOARD_ESP32) || defined(BOARD_GYRO)
#include <HTTPClient.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>

static constexpr unsigned long OTA_CONFIRM_DELAY_MS = 60000;
static unsigned long otaBootTime = 0;
static bool otaConfirmed = false;

bool otaStartUpdate(const char* url, const char* expectedSha256,
                    uint8_t newMajor, uint8_t newMinor, uint8_t newPatch) {
    if (!otaVersionAllowed(newMajor, newMinor, newPatch)) return false;
    otaStatus = OTA_STATUS_DOWNLOADING;
    otaProgress = 0;
    if (Serial) Serial.printf("OTA: downloading from %s\n", url);

    // #890 — manual download loop instead of HTTPUpdate so the image can be
    // SHA-256-hashed while it streams into the inactive OTA partition.
    WiFiClient client;
    HTTPClient http;
    http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
    if (!http.begin(client, url)) {
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.println("OTA: failed — bad URL");
        return false;
    }
    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — HTTP %d\n", code);
        return false;
    }
    int total = http.getSize();
    if (total <= 0) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.println("OTA: failed — no Content-Length");
        return false;
    }
    if (!Update.begin((size_t)total)) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — begin: %s\n", Update.errorString());
        return false;
    }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);   // 0 = SHA-256 (not SHA-224)

    uint8_t buf[1024];                // fixed stack buffer — no heap
    int written = 0;
    unsigned long lastData = millis();
    WiFiClient* stream = http.getStreamPtr();
    while (written < total) {
        int avail = stream->available();
        if (avail > 0) {
            int toRead = avail;
            if (toRead > (int)sizeof(buf))    toRead = (int)sizeof(buf);
            if (toRead > total - written)     toRead = total - written;
            int n = stream->read(buf, (size_t)toRead);
            if (n > 0) {
                if ((int)Update.write(buf, (size_t)n) != n) {
                    Update.abort();
                    http.end();
                    mbedtls_sha256_free(&sha);
                    otaStatus = OTA_STATUS_FAILED;
                    if (Serial) Serial.printf("OTA: failed — write: %s\n", Update.errorString());
                    return false;
                }
                mbedtls_sha256_update(&sha, buf, (size_t)n);
                written += n;
                otaProgress = (uint8_t)((int32_t)written * 100L / total);
                lastData = millis();
            }
        } else {
            if (!stream->connected()) break;
            delay(1);
        }
        if (millis() - lastData > OTA_STALL_TIMEOUT_MS) break;
    }
    http.end();

    if (written != total) {
        Update.abort();
        mbedtls_sha256_free(&sha);
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — short read %d/%d\n", written, total);
        return false;
    }

    uint8_t digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);

    if (otaShaExpected(expectedSha256)) {
        otaStatus = OTA_STATUS_VERIFYING;
        if (!otaShaMatches(digest, expectedSha256)) {
            Update.abort();
            otaStatus = OTA_STATUS_FAILED;
            if (Serial) Serial.println("OTA: failed — SHA-256 mismatch");
            return false;
        }
        if (Serial) Serial.println("OTA: SHA-256 verified");
    } else if (Serial) {
        Serial.println("OTA: no SHA-256 supplied — skipping verification");
    }

    otaStatus = OTA_STATUS_APPLYING;
    if (!Update.end(true)) {
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — end: %s\n", Update.errorString());
        return false;
    }
    otaStatus = OTA_STATUS_SUCCESS;
    otaProgress = 100;
    if (Serial) Serial.println("OTA: success — rebooting");
    return true;
}

void otaConfirmBoot() {
    otaBootTime = millis();
    otaConfirmed = false;
    const esp_partition_t* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK) {
        if (state == ESP_OTA_IMG_PENDING_VERIFY) {
            if (Serial) Serial.println("OTA: new firmware — confirming after 60s");
            return;
        }
    }
    otaConfirmed = true;
}

bool otaIsNewFirmware() {
    const esp_partition_t* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK)
        return state == ESP_OTA_IMG_PENDING_VERIFY;
    return false;
}

void otaCheckConfirm() {
    if (otaConfirmed) return;
    if (millis() - otaBootTime >= OTA_CONFIRM_DELAY_MS) {
        esp_ota_mark_app_valid_cancel_rollback();
        otaConfirmed = true;
        if (Serial) Serial.println("OTA: boot confirmed");
    }
}

// ── D1 Mini OTA ──────────────────────────────────────────────────────────────

#elif defined(BOARD_D1MINI)
#include <ESP8266HTTPClient.h>
#include <ESP8266WiFi.h>
#include <Updater.h>
#include <bearssl/bearssl_hash.h>

bool otaStartUpdate(const char* url, const char* expectedSha256,
                    uint8_t newMajor, uint8_t newMinor, uint8_t newPatch) {
    if (!otaVersionAllowed(newMajor, newMinor, newPatch)) return false;
    otaStatus = OTA_STATUS_DOWNLOADING;
    otaProgress = 0;
    if (Serial) Serial.printf("OTA: downloading from %s\n", url);

    // #890 — manual download loop instead of ESP8266httpUpdate so the image
    // is SHA-256-hashed while it streams. Single-slot semantics: ESP8266
    // Updater has no abort() and end(false) commits once every byte is
    // written, so the newest chunk is HELD BACK from flash until the full
    // stream hash matches. On mismatch the update still has bytes remaining
    // and Update.end(false) discards it — flash layout stays bootable.
    WiFiClient client;
    HTTPClient http;
    http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
    if (!http.begin(client, url)) {
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.println("OTA: failed — bad URL");
        return false;
    }
    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — HTTP %d\n", code);
        return false;
    }
    int total = http.getSize();
    if (total <= 0) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.println("OTA: failed — no Content-Length");
        return false;
    }
    if (!Update.begin((size_t)total)) {
        http.end();
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) { Serial.print("OTA: failed — begin: "); Update.printError(Serial); }
        return false;
    }

    br_sha256_context sha;
    br_sha256_init(&sha);

    // Two small fixed stack buffers (ESP8266 loop stack is only 4 KB):
    // `cur` receives from the stream, `held` is the newest chunk not yet
    // written to flash.
    uint8_t bufA[512], bufB[512];
    uint8_t* cur     = bufA;
    uint8_t* held    = bufB;
    int      heldLen = 0;
    int received = 0;
    unsigned long lastData = millis();
    WiFiClient* stream = http.getStreamPtr();
    while (received < total) {
        int avail = stream->available();
        if (avail > 0) {
            int toRead = avail;
            if (toRead > 512)                 toRead = 512;
            if (toRead > total - received)    toRead = total - received;
            int n = stream->read(cur, (size_t)toRead);
            if (n > 0) {
                br_sha256_update(&sha, cur, (size_t)n);
                received += n;
                // Flush the previously held chunk — always keeps >= 1 byte
                // of the image unwritten until the hash is verified.
                if (heldLen > 0 && (int)Update.write(held, (size_t)heldLen) != heldLen) {
                    http.end();
                    Update.end(false);   // bytes remain → discard update
                    otaStatus = OTA_STATUS_FAILED;
                    if (Serial) { Serial.print("OTA: failed — write: "); Update.printError(Serial); }
                    return false;
                }
                uint8_t* t = held; held = cur; cur = t;
                heldLen = n;
                otaProgress = (uint8_t)((int32_t)received * 100L / total);
                lastData = millis();
            }
        } else {
            if (!stream->connected()) break;
            delay(1);   // yields — keeps WiFi + WDT serviced
        }
        if (millis() - lastData > OTA_STALL_TIMEOUT_MS) break;
    }
    http.end();

    if (received != total) {
        Update.end(false);   // bytes remain → discard update
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) Serial.printf("OTA: failed — short read %d/%d\n", received, total);
        return false;
    }

    uint8_t digest[32];
    br_sha256_out(&sha, digest);

    if (otaShaExpected(expectedSha256)) {
        otaStatus = OTA_STATUS_VERIFYING;
        if (!otaShaMatches(digest, expectedSha256)) {
            Update.end(false);   // held chunk unwritten → discard update
            otaStatus = OTA_STATUS_FAILED;
            if (Serial) Serial.println("OTA: failed — SHA-256 mismatch");
            return false;
        }
        if (Serial) Serial.println("OTA: SHA-256 verified");
    } else if (Serial) {
        Serial.println("OTA: no SHA-256 supplied — skipping verification");
    }

    // Hash verified (or not supplied) — write the held tail, then commit.
    otaStatus = OTA_STATUS_APPLYING;
    if (heldLen > 0 && (int)Update.write(held, (size_t)heldLen) != heldLen) {
        Update.end(false);
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) { Serial.print("OTA: failed — write: "); Update.printError(Serial); }
        return false;
    }
    if (!Update.end()) {
        otaStatus = OTA_STATUS_FAILED;
        if (Serial) { Serial.print("OTA: failed — end: "); Update.printError(Serial); }
        return false;
    }
    otaStatus = OTA_STATUS_SUCCESS;
    otaProgress = 100;
    if (Serial) Serial.println("OTA: success — rebooting");
    return true;
}

void otaConfirmBoot() {}
bool otaIsNewFirmware() { return false; }
void otaCheckConfirm() {}

// ── Giga Child stub ──────────────────────────────────────────────────────────

#elif defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)

bool otaStartUpdate(const char*, const char*, uint8_t, uint8_t, uint8_t) { return false; }
void otaConfirmBoot() {}
bool otaIsNewFirmware() { return false; }
void otaCheckConfirm() {}

#endif // board selection

#endif // BOARD_CHILD || BOARD_GYRO
