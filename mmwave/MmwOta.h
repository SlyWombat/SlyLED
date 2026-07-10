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
#include <IPAddress.h>

// OTA status codes (sent via CMD_OTA_STATUS) — fleet parity: these MUST
// stay numerically identical to main/OtaUpdate.h's OTA_STATUS_* so the
// orchestrator decodes every board's 0x51 the same way (#922).
constexpr uint8_t OTA_STATUS_IDLE        = 0;
constexpr uint8_t OTA_STATUS_DOWNLOADING = 1;
constexpr uint8_t OTA_STATUS_VERIFYING   = 2;
constexpr uint8_t OTA_STATUS_APPLYING    = 3;
constexpr uint8_t OTA_STATUS_SUCCESS     = 4;
constexpr uint8_t OTA_STATUS_FAILED      = 5;
constexpr uint8_t OTA_STATUS_REJECTED    = 6;  // anti-rollback (unused here)

// #922 — where CMD_OTA_STATUS (0x51) reports go. Set by the OTA trigger
// path (MmwUdp's CMD_OTA_UPDATE case → the sender) before mmwOtaStart();
// 0.0.0.0 disables reporting.
void mmwOtaSetReportTarget(IPAddress ip);

// Download url, hash-verify against expectedSha256 (64 hex chars; empty or
// all-zero → verification skipped), flash to the inactive bank. Returns true
// if the update is staged and a reboot should follow.
bool mmwOtaStart(const char* url, const char* expectedSha256);

uint8_t mmwOtaStatus();   // OTA_STATUS_* code (SUCCESS only briefly — a reboot follows)

#endif  // MMW_OTA_H
