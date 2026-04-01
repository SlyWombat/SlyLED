/*
 * ArtNetRecv.h — Art-Net 4 receiver for DMX bridge.
 *
 * MUST be included BEFORE any WiFi headers to override WIFI_UDP_BUFFER_SIZE.
 * ArtDMX packets are 530 bytes (18 header + 512 data), exceeding the
 * default 508-byte WiFiUDP buffer. We bump it to 580 for safety.
 */

#ifndef ARTNETRECV_H
#define ARTNETRECV_H

#include "BoardConfig.h"

#ifdef BOARD_DMX_BRIDGE

// WIFI_UDP_BUFFER_SIZE overridden in BoardConfig.h (580 bytes for ArtDMX)
#include <WiFiUdp.h>

constexpr uint16_t ARTNET_PORT    = 6454;
constexpr uint8_t  ARTNET_HDR_LEN = 8;

constexpr uint16_t ARTNET_OP_POLL       = 0x2000;
constexpr uint16_t ARTNET_OP_POLL_REPLY = 0x2100;
constexpr uint16_t ARTNET_OP_DMX        = 0x5000;

extern WiFiUDP       artnetUDP;
extern volatile uint32_t artnetRxCount;
extern volatile uint32_t artnetPps;
extern char          artnetLastSender[16];

void artnetInit();
void pollArtNet();

#endif // BOARD_DMX_BRIDGE
#endif // ARTNETRECV_H
