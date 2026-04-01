/*
 * ArtNetRecv.h — Art-Net 4 receiver for DMX bridge.
 *
 * Listens on UDP 6454 for ArtDMX packets and writes to dmxBuf[].
 * Responds to ArtPoll with ArtPollReply for node discovery.
 *
 * Guarded by #ifdef BOARD_DMX_BRIDGE — safe to include on any board.
 */

#ifndef ARTNETRECV_H
#define ARTNETRECV_H

#include "BoardConfig.h"

#ifdef BOARD_DMX_BRIDGE

#include <WiFiUdp.h>

constexpr uint16_t ARTNET_PORT    = 6454;
constexpr uint8_t  ARTNET_HDR_LEN = 8;   // "Art-Net\0"

// Opcodes
constexpr uint16_t ARTNET_OP_POLL       = 0x2000;
constexpr uint16_t ARTNET_OP_POLL_REPLY = 0x2100;
constexpr uint16_t ARTNET_OP_DMX        = 0x5000;

extern WiFiUDP       artnetUDP;
extern volatile uint32_t artnetRxCount;     // ArtDMX packets received
extern volatile uint32_t artnetPps;         // packets per second (rolling)
extern char          artnetLastSender[16];  // IP of last ArtDMX sender

void artnetInit();       // Bind socket — call after WiFi connected
void pollArtNet();       // Non-blocking receive — call every loop()

#endif // BOARD_DMX_BRIDGE
#endif // ARTNETRECV_H
