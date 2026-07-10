/*
 * MmwUart.h — Ai-Thinker Rd-03D UART frame parser (multi-target mode).
 *
 * Data frame (30 bytes, little-endian words), per the Rd-03D multi-target
 * trajectory user manual and cross-checked against the ESPHome rd03d
 * component:
 *   AA FF 03 00 | target0(8) | target1(8) | target2(8) | 55 CC
 * Each 8-byte target: x(u16) y(u16) speed(u16) distanceRes(u16).
 * x/y/speed carry sign in the MSB: value = raw & 0x7FFF, MSB set → positive,
 * MSB clear → negative (all-zero slot = no target).
 * NOTE: sign convention + mode-switch ACK behaviour to be confirmed on real
 * hardware during bench validation (#908).
 */

#ifndef MMW_UART_H
#define MMW_UART_H

#include <stdint.h>
#include "MmwProtocol.h"

void mmwUartBegin();                 // init Serial1 + request multi-target mode
void mmwUartPoll();                  // pump bytes through the frame parser

// Latest complete frame. Returns target count (0..3) and fills out[]; sets
// *fresh=true exactly once per newly parsed frame (then false until the next).
uint8_t mmwUartTargets(MmwTarget out[MMW_MAX_TARGETS], bool* fresh);

bool     mmwUartHealthy();           // a valid frame arrived within MMW_FRAME_STALE_MS
uint32_t mmwUartFrameCount();        // total valid frames since boot
uint32_t mmwUartErrorCount();        // bad-tail / desync count since boot
uint32_t mmwUartByteCount();         // raw UART bytes received since boot (#908)

#endif  // MMW_UART_H
