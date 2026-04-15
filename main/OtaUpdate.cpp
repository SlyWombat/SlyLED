/*
 * OtaUpdate.cpp — OTA firmware update implementation.
 *
 * ESP32: Uses HTTPUpdate with dual OTA partitions (ota_0 / ota_1).
 * D1 Mini: Uses ESP8266httpUpdate with single-slot update.
 * Giga R1: Not supported (stubs only).
 */

#include "BoardConfig.h"
#include "OtaUpdate.h"

#if defined(BOARD_CHILD) || defined(BOARD_GYRO)

#include "version.h"
#include <Arduino.h>

volatile uint8_t otaStatus   = OTA_STATUS_IDLE;
volatile uint8_t otaProgress = 0;

// ── ESP32 OTA ────────────────────────────────────────────────────────────────

#if defined(BOARD_ESP32) || defined(BOARD_GYRO)
#include <HTTPUpdate.h>
#include <WiFi.h>
#include <esp_ota_ops.h>

static constexpr unsigned long OTA_CONFIRM_DELAY_MS = 60000;
static unsigned long otaBootTime = 0;
static bool otaConfirmed = false;

bool otaStartUpdate(const char* url, const char* expectedSha256,
                    uint8_t newMajor, uint8_t newMinor, uint8_t newPatch) {
    // Anti-rollback: compare full 3-part version
    uint32_t newVer = (uint32_t)newMajor * 10000 + (uint32_t)newMinor * 100 + newPatch;
    uint32_t curVer = (uint32_t)APP_MAJOR * 10000 + (uint32_t)APP_MINOR * 100 + APP_PATCH;
    if (newVer <= curVer) {
        if (Serial) Serial.printf("OTA: rejected v%d.%d.%d (current v%d.%d.%d)\n",
                                   newMajor, newMinor, newPatch, APP_MAJOR, APP_MINOR, APP_PATCH);
        otaStatus = OTA_STATUS_REJECTED;
        return false;
    }
    otaStatus = OTA_STATUS_DOWNLOADING;
    otaProgress = 0;
    if (Serial) Serial.printf("OTA: downloading from %s\n", url);

    WiFiClient client;
    httpUpdate.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
    httpUpdate.onProgress([](int cur, int total) {
        if (total > 0) otaProgress = (uint8_t)(cur * 100L / total);
    });

    t_httpUpdate_return result = httpUpdate.update(client, String(url));
    switch (result) {
        case HTTP_UPDATE_OK:
            otaStatus = OTA_STATUS_SUCCESS;
            otaProgress = 100;
            if (Serial) Serial.println("OTA: success — rebooting");
            return true;
        case HTTP_UPDATE_FAILED:
            otaStatus = OTA_STATUS_FAILED;
            if (Serial) Serial.printf("OTA: failed — %s\n", httpUpdate.getLastErrorString().c_str());
            return false;
        default:
            otaStatus = OTA_STATUS_IDLE;
            return false;
    }
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
#include <ESP8266httpUpdate.h>
#include <ESP8266WiFi.h>

bool otaStartUpdate(const char* url, const char* expectedSha256,
                    uint8_t newMajor, uint8_t newMinor, uint8_t newPatch) {
    // Anti-rollback: compare full 3-part version
    uint32_t newVer = (uint32_t)newMajor * 10000 + (uint32_t)newMinor * 100 + newPatch;
    uint32_t curVer = (uint32_t)APP_MAJOR * 10000 + (uint32_t)APP_MINOR * 100 + APP_PATCH;
    if (newVer <= curVer) {
        if (Serial) Serial.printf("OTA: rejected v%d.%d.%d (current v%d.%d.%d)\n",
                                   newMajor, newMinor, newPatch, APP_MAJOR, APP_MINOR, APP_PATCH);
        otaStatus = OTA_STATUS_REJECTED;
        return false;
    }
    otaStatus = OTA_STATUS_DOWNLOADING;
    otaProgress = 0;
    if (Serial) Serial.printf("OTA: downloading from %s\n", url);

    WiFiClient client;
    ESPhttpUpdate.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);
    ESPhttpUpdate.onProgress([](int cur, int total) {
        if (total > 0) otaProgress = (uint8_t)(cur * 100L / total);
    });

    t_httpUpdate_return result = ESPhttpUpdate.update(client, String(url));
    switch (result) {
        case HTTP_UPDATE_OK:
            otaStatus = OTA_STATUS_SUCCESS;
            otaProgress = 100;
            if (Serial) Serial.println("OTA: success — rebooting");
            return true;
        case HTTP_UPDATE_FAILED:
            otaStatus = OTA_STATUS_FAILED;
            if (Serial) Serial.printf("OTA: failed — %s\n", ESPhttpUpdate.getLastErrorString().c_str());
            return false;
        default:
            otaStatus = OTA_STATUS_IDLE;
            return false;
    }
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
