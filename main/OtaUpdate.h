/*
 * OtaUpdate.h — Over-The-Air firmware update for ESP32 and D1 Mini.
 *
 * Downloads firmware binary from a URL (GitHub Releases or parent proxy),
 * verifies SHA-256 hash, and applies the update. ESP32 uses dual-bank
 * OTA partitions; D1 Mini uses single-slot ESP8266httpUpdate.
 *
 * Giga R1 does not support OTA — USB/DFU only.
 */

#ifndef OTA_UPDATE_H
#define OTA_UPDATE_H

#ifdef BOARD_CHILD

#include "BoardConfig.h"
#include <stdint.h>

// OTA status codes (sent via CMD_OTA_STATUS)
constexpr uint8_t OTA_STATUS_IDLE       = 0;
constexpr uint8_t OTA_STATUS_DOWNLOADING = 1;
constexpr uint8_t OTA_STATUS_VERIFYING  = 2;
constexpr uint8_t OTA_STATUS_APPLYING   = 3;
constexpr uint8_t OTA_STATUS_SUCCESS    = 4;
constexpr uint8_t OTA_STATUS_FAILED     = 5;
constexpr uint8_t OTA_STATUS_REJECTED   = 6;  // anti-rollback

// Current OTA state (readable by status endpoint)
extern volatile uint8_t otaStatus;
extern volatile uint8_t otaProgress;  // 0-100

// Start an OTA update. Returns true if update was applied (caller should reboot).
// url: HTTP URL to firmware binary
// expectedSha256: 64-char hex string (or empty to skip verification)
// newMajor/newMinor/newPatch: version of the incoming firmware (for anti-rollback)
bool otaStartUpdate(const char* url, const char* expectedSha256,
                    uint8_t newMajor, uint8_t newMinor, uint8_t newPatch);

// Called from setup() after boot — ESP32: marks OTA partition as valid after
// stable operation. D1 Mini: no-op (single slot).
void otaConfirmBoot();

// Check if current boot is from an OTA update (ESP32 only)
bool otaIsNewFirmware();

// Call from loop() — ESP32: confirms OTA partition after 60s stable operation
void otaCheckConfirm();

#endif // BOARD_CHILD
#endif // OTA_UPDATE_H
