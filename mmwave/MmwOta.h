/*
 * MmwOta.h — OTA update for the MMwave node (ESP32-C61 dual-bank).
 *
 * Same wire contract as the fleet (CMD_OTA_UPDATE 0x50: maj/min/patch +
 * urlLen + url + sha256hex), with streaming SHA-256 verification — the
 * update is aborted, not applied, on hash mismatch (#890 contract).
 */

#ifndef MMW_OTA_H
#define MMW_OTA_H

#include <stdint.h>

// Download url, hash-verify against expectedSha256 (64 hex chars; empty or
// all-zero → verification skipped), flash to the inactive bank. Returns true
// if the update is staged and a reboot should follow.
bool mmwOtaStart(const char* url, const char* expectedSha256);

uint8_t mmwOtaStatus();   // 0 idle, 1 downloading, 2 verifying, 3 applying, 4 error

#endif  // MMW_OTA_H
