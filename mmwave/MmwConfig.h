/*
 * MmwConfig.h — pins and tunables for the MMwave radar node.
 *
 * Board: ESP32-C61-DevKitC-1-N8R2.  Radar: Ai-Thinker Rd-03D ("G550" rev,
 * 1×4P 1.25 mm connector: 5V / GND / TX / RX).  Wiring per design doc §2.3:
 * GPIO7/8/9 are strapping (8 = RGB LED), GPIO10/11 UART0 console,
 * GPIO12/13 native USB, GPIO14 PSRAM SPICS1 on N8R2 — GPIO0–6 are free.
 */

#ifndef MMW_CONFIG_H
#define MMW_CONFIG_H

#include <stdint.h>

// ── Radar UART ────────────────────────────────────────────────────────────────
// BENCH-VALIDATED 2026-07-10 (#908): radar TX→GPIO2, radar RX→GPIO3,
// 256000 8N1 as spec'd — 10 Hz frames, zero parse errors, mode-command
// ACK confirmed. Wrong-pin symptom is bytes-scale-with-baud noise.
constexpr int8_t   MMW_UART_RX_PIN = 2;       // ← radar TX
constexpr int8_t   MMW_UART_TX_PIN = 3;       // → radar RX
constexpr uint32_t MMW_UART_BAUD   = 256000;  // Rd-03D fixed rate, 8N1

// ── Reporting cadence ─────────────────────────────────────────────────────────
constexpr uint32_t MMW_HEARTBEAT_MS   = 1000; // empty MMW_TARGETS keepalive when idle
constexpr uint32_t MMW_MIN_REPORT_MS  = 40;   // rate cap ≈25 Hz (radar itself ~10-15 Hz)

// ── Radar health ─────────────────────────────────────────────────────────────
constexpr uint32_t MMW_FRAME_STALE_MS = 2000; // no valid frame for this long → unhealthy

// ── Identity ─────────────────────────────────────────────────────────────────
#define MMW_HOSTNAME_PREFIX "MMW-"            // + last 4 hex of MAC → "MMW-A1B2"
#define MMW_ALT_NAME        "MMwave"
#define MMW_DESCRIPTION     "Rd-03D 24GHz radar people tracker"

#endif  // MMW_CONFIG_H
