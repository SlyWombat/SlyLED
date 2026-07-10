/*
 * OtaUpdate.h — Over-The-Air firmware update for ESP32 and D1 Mini.
 *
 * Downloads firmware binary from a URL (GitHub Releases or parent proxy),
 * verifies its SHA-256 hash while streaming (#890), and applies the update
 * only when the hash matches. ESP32/gyro use dual-bank OTA partitions;
 * D1 Mini uses the single-slot ESP8266 Updater.
 *
 * Giga R1 does not support OTA — USB/DFU only.
 */

#ifndef OTA_UPDATE_H
#define OTA_UPDATE_H

#if defined(BOARD_CHILD) || defined(BOARD_GYRO)

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

// #B3 — where CMD_OTA_STATUS (0x51) reports go. Set by the trigger path
// (UDP CMD_OTA_UPDATE sender / HTTP POST /ota client) before calling
// otaStartUpdate(); 0.0.0.0 disables reporting. No-op stub on Giga.
void otaSetReportTarget(IPAddress ip);

// Start an OTA update. Returns true if update was applied (caller should reboot).
// url: HTTP URL to firmware binary
// expectedSha256: 64-char hex string, case-insensitive. Empty or all-zero
//                 skips verification (legacy senders without a hash).
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

#endif // BOARD_CHILD || BOARD_GYRO
#endif // OTA_UPDATE_H
