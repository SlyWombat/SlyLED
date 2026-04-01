/*
 * ArtNetRecv.cpp — Art-Net 4 receiver for DMX bridge.
 *
 * Wire format (Art-Net 4 spec):
 *   ArtDMX (0x5000):
 *     [0..7]   "Art-Net\0"
 *     [8..9]   opcode (LE) = 0x5000
 *     [10..11] protocol version (BE) = 14
 *     [12]     sequence (1-255, 0=disable)
 *     [13]     physical port
 *     [14..15] port-address / universe (LE, 15-bit)
 *     [16..17] data length (BE, max 512, even)
 *     [18..]   DMX channel data
 *
 *   ArtPoll (0x2000):
 *     [0..7]   "Art-Net\0"
 *     [8..9]   opcode (LE) = 0x2000
 *     [10..11] protocol version (BE) = 14
 *     [12]     TalkToMe flags
 *     [13]     DiagPriority
 *
 *   ArtPollReply (0x2100): 239 bytes — node identity response
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

static uint8_t artBuf[600];  // max ArtDMX = 18 + 512 = 530

// Rolling PPS counter
static uint32_t _ppsCount = 0;
static unsigned long _ppsTime = 0;

static const uint8_t ARTNET_ID[8] = {'A','r','t','-','N','e','t',0};

// ── ArtPollReply builder ────────────────────────────────────────────────────

static void sendArtPollReply(IPAddress remoteIP, uint16_t remotePort) {
  uint8_t reply[239];
  memset(reply, 0, sizeof(reply));

  // Header
  memcpy(reply, ARTNET_ID, 8);
  reply[8] = ARTNET_OP_POLL_REPLY & 0xFF;
  reply[9] = (ARTNET_OP_POLL_REPLY >> 8) & 0xFF;

  // IP address (4 bytes at offset 10)
  IPAddress myIP = WiFi.localIP();
  reply[10] = myIP[0]; reply[11] = myIP[1];
  reply[12] = myIP[2]; reply[13] = myIP[3];

  // Port (LE at offset 14)
  reply[14] = ARTNET_PORT & 0xFF;
  reply[15] = (ARTNET_PORT >> 8) & 0xFF;

  // Version (BE at offset 16)
  reply[16] = 0x00;
  reply[17] = APP_PATCH;

  // NetSwitch (offset 18), SubSwitch (offset 19)
  reply[18] = 0;                   // net
  reply[19] = dmxCfg.subnet & 0x0F;  // subnet

  // OEM (BE at offset 20)
  reply[20] = 0x00; reply[21] = 0xFF;

  // UBEA version (offset 22)
  reply[22] = 0;

  // Status1 (offset 23)
  reply[23] = 0xD0;  // indicator normal, Art-Net capable

  // ESTA manufacturer (LE at offset 24)
  reply[24] = 0xF0; reply[25] = 0x7F;

  // Short name (18 bytes at offset 26)
  const char* hostname = childCfg.altName[0] ? childCfg.altName : childCfg.hostname;
  strncpy((char*)reply + 26, hostname, 17);

  // Long name (64 bytes at offset 44)
  snprintf((char*)reply + 44, 63, "SlyLED DMX Bridge v%d.%d.%d",
           APP_MAJOR, APP_MINOR, APP_PATCH);

  // Node report (64 bytes at offset 108)
  snprintf((char*)reply + 108, 63, "#0001 [%04lu] OK", (unsigned long)artnetRxCount);

  // NumPorts (BE at offset 172)
  reply[172] = 0; reply[173] = 1;

  // PortTypes (4 bytes at offset 174): output, Art-Net
  reply[174] = 0x80;  // output, Art-Net

  // GoodOutput (4 bytes at offset 182)
  reply[182] = 0x80;  // data transmitting

  // SwOut (4 bytes at offset 190): output universe
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

  // Drain all pending packets (up to 10 per call to stay responsive)
  for (int attempt = 0; attempt < 10; attempt++) {
    int plen = artnetUDP.parsePacket();
    if (plen <= 0) return;

    int n = artnetUDP.read(artBuf, sizeof(artBuf));
    if (n < 12) continue;

    // Validate Art-Net header
    if (memcmp(artBuf, ARTNET_ID, 8) != 0) continue;

    // Extract opcode (LE)
    uint16_t opcode = artBuf[8] | ((uint16_t)artBuf[9] << 8);

    if (opcode == ARTNET_OP_DMX && n >= 18) {
      // ArtDMX: extract universe and data
      uint16_t rxUniverse = artBuf[14] | ((uint16_t)artBuf[15] << 8);
      uint16_t myUniverse = ((uint16_t)dmxCfg.subnet << 4) | dmxCfg.universe;

      if (rxUniverse == myUniverse) {
        uint16_t dataLen = ((uint16_t)artBuf[16] << 8) | artBuf[17];
        if (dataLen > 512) dataLen = 512;
        if (n >= 18 + (int)dataLen) {
          // Write directly to dmxBuf (offset 1 = channel 1)
          memcpy(dmxBuf + 1, artBuf + 18, dataLen);
          artnetRxCount++;
          _ppsCount++;
          // Record sender IP
          IPAddress sender = artnetUDP.remoteIP();
          snprintf(artnetLastSender, sizeof(artnetLastSender), "%d.%d.%d.%d",
                   sender[0], sender[1], sender[2], sender[3]);
        }
      }
    }
    else if (opcode == ARTNET_OP_POLL) {
      // Respond with ArtPollReply
      sendArtPollReply(artnetUDP.remoteIP(), artnetUDP.remotePort());
    }
  }
}

#endif // BOARD_DMX_BRIDGE
