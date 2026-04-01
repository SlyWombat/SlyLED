/*
 * ArtNetRecv.cpp — Art-Net 4 receiver for DMX bridge.
 *
 * IMPORTANT: WIFI_UDP_BUFFER_SIZE must be defined to >= 580 BEFORE
 * including any WiFi headers, to fit full ArtDMX packets (530 bytes).
 * This is done in ArtNetRecv.h via #define before #include <WiFiUdp.h>.
 */

#include "ArtNetRecv.h"

#ifdef BOARD_DMX_BRIDGE

#include <Arduino.h>
#include <WiFi.h>
#include "DmxBridge.h"
#include "Child.h"
#include "Globals.h"
#include "version.h"

WiFiUDP       artnetUDP;
volatile uint32_t artnetRxCount = 0;
volatile uint32_t artnetPps = 0;
char          artnetLastSender[16] = "";

static uint8_t artBuf[600];

static uint32_t _ppsCount = 0;
static unsigned long _ppsTime = 0;

static const uint8_t ARTNET_ID[8] = {'A','r','t','-','N','e','t',0};

// ── ArtPollReply builder ────────────────────────────────────────────────────

static void sendArtPollReply(IPAddress remoteIP, uint16_t remotePort) {
  uint8_t reply[239];
  memset(reply, 0, sizeof(reply));

  memcpy(reply, ARTNET_ID, 8);
  reply[8] = ARTNET_OP_POLL_REPLY & 0xFF;
  reply[9] = (ARTNET_OP_POLL_REPLY >> 8) & 0xFF;

  IPAddress myIP = WiFi.localIP();
  reply[10] = myIP[0]; reply[11] = myIP[1];
  reply[12] = myIP[2]; reply[13] = myIP[3];

  reply[14] = ARTNET_PORT & 0xFF;
  reply[15] = (ARTNET_PORT >> 8) & 0xFF;
  reply[16] = 0x00; reply[17] = APP_PATCH;
  reply[18] = 0;
  reply[19] = dmxCfg.subnet & 0x0F;
  reply[20] = 0x00; reply[21] = 0xFF;
  reply[22] = 0;
  reply[23] = 0xD0;
  reply[24] = 0xF0; reply[25] = 0x7F;

  const char* hostname = childCfg.altName[0] ? childCfg.altName : childCfg.hostname;
  strncpy((char*)reply + 26, hostname, 17);
  snprintf((char*)reply + 44, 63, "SlyLED DMX Bridge v%d.%d.%d",
           APP_MAJOR, APP_MINOR, APP_PATCH);
  snprintf((char*)reply + 108, 63, "#0001 [%04lu] OK", (unsigned long)artnetRxCount);

  reply[172] = 0; reply[173] = 1;
  reply[174] = 0x80;
  reply[182] = 0x80;
  reply[190] = dmxCfg.universe & 0x0F;

  artnetUDP.beginPacket(remoteIP, remotePort);
  artnetUDP.write(reply, sizeof(reply));
  artnetUDP.endPacket();
}

// ── Init ────────────────────────────────────────────────────────────────────

void artnetInit() {
  artnetUDP.begin(ARTNET_PORT);
  _ppsTime = millis();
  if (Serial) Serial.println(F("[ArtNet] Listening on port 6454"));
}

// ── Non-blocking poll ───────────────────────────────────────────────────────

void pollArtNet() {
  // Update PPS counter every second
  unsigned long now = millis();
  if (now - _ppsTime >= 1000) {
    artnetPps = _ppsCount;
    _ppsCount = 0;
    _ppsTime = now;
  }

  // Drain pending packets
  for (int attempt = 0; attempt < 10; attempt++) {
    int plen = artnetUDP.parsePacket();
    if (plen <= 0) return;

    int n = artnetUDP.read(artBuf, sizeof(artBuf));
    if (n < 12) continue;

    if (memcmp(artBuf, ARTNET_ID, 8) != 0) continue;

    uint16_t opcode = artBuf[8] | ((uint16_t)artBuf[9] << 8);

    if (opcode == ARTNET_OP_DMX && n >= 18) {
      uint16_t dataLen = ((uint16_t)artBuf[16] << 8) | artBuf[17];
      if (dataLen > 512) dataLen = 512;
      if (n >= 18 + (int)dataLen) {
        memcpy(dmxBuf + 1, artBuf + 18, dataLen);
        artnetRxCount++;
        _ppsCount++;
        IPAddress sender = artnetUDP.remoteIP();
        snprintf(artnetLastSender, sizeof(artnetLastSender), "%d.%d.%d.%d",
                 sender[0], sender[1], sender[2], sender[3]);
      }
    }
    else if (opcode == ARTNET_OP_POLL) {
      sendArtPollReply(artnetUDP.remoteIP(), artnetUDP.remotePort());
    }
  }
}

#endif // BOARD_DMX_BRIDGE
