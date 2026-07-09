/*
 * MmwUdp.cpp — UDP 4210 endpoint. Mirrors the fleet conventions: reply PONG
 * to the sender of a PING, broadcast PONG on boot, answer STATUS_REQ, accept
 * CMD_OTA_UPDATE. Targets go to the last parent that pinged us (fall back to
 * subnet broadcast until one has).
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "MmwConfig.h"
#include "MmwNet.h"
#include "MmwOta.h"
#include "MmwProtocol.h"
#include "MmwUart.h"
#include "MmwUdp.h"
#include "version.h"

static WiFiUDP udp;
static uint8_t rxBuf[384];          // sized per #895 (OTA URL + sha payload)
static IPAddress parentIp;          // last PING sender; targets unicast here
static bool      parentKnown = false;
static uint16_t  txSeq       = 0;

static void writeHeader(uint8_t* buf, uint8_t cmd) {
  UdpHeader h;
  h.magic   = UDP_MAGIC;
  h.version = UDP_VERSION;
  h.cmd     = cmd;
  h.epoch   = mmwEpoch();
  memcpy(buf, &h, sizeof(h));
}

static void sendPong(IPAddress dest) {
  static uint8_t pkt[sizeof(UdpHeader) + sizeof(PongPayload)];
  writeHeader(pkt, CMD_PONG);
  PongPayload p;
  memset(&p, 0, sizeof(p));
  strncpy(p.hostname, mmwHostname(), HOSTNAME_LEN - 1);
  strncpy(p.altName, MMW_ALT_NAME, CHILD_NAME_LEN - 1);
  strncpy(p.description, MMW_DESCRIPTION, CHILD_DESC_LEN - 1);
  p.stringCount = 0;                 // no LED strings on a radar node
  p.fwMajor = MMW_MAJOR;
  p.fwMinor = MMW_MINOR;
  p.fwPatch = MMW_PATCH;
  memcpy(pkt + sizeof(UdpHeader), &p, sizeof(p));
  udp.beginPacket(dest, UDP_PORT);
  udp.write(pkt, sizeof(pkt));
  udp.endPacket();
}

static void sendStatus(IPAddress dest, uint16_t port) {
  static uint8_t pkt[sizeof(UdpHeader) + sizeof(StatusRespPayload)];
  writeHeader(pkt, CMD_STATUS_RESP);
  StatusRespPayload s;
  MmwTarget t[MMW_MAX_TARGETS];
  s.activeAction = 0;
  s.runnerActive = 0;
  s.currentStep  = mmwUartTargets(t, nullptr);   // repurposed: tracked-target count
  s.wifiRssi     = mmwRssiAbs();
  s.uptimeS      = millis() / 1000;
  memcpy(pkt + sizeof(UdpHeader), &s, sizeof(s));
  udp.beginPacket(dest, port);
  udp.write(pkt, sizeof(pkt));
  udp.endPacket();
}

void mmwUdpBegin() {
  udp.begin(UDP_PORT);
  mmwUdpBroadcastPong();
}

void mmwUdpBroadcastPong() {
  sendPong(IPAddress(255, 255, 255, 255));
}

void mmwUdpSendTargets(const MmwTarget targets[MMW_MAX_TARGETS],
                       uint8_t count, uint8_t flags) {
  static uint8_t pkt[sizeof(UdpHeader) + sizeof(MmwTargetsPayload)];
  writeHeader(pkt, CMD_MMW_TARGETS);
  MmwTargetsPayload p;
  p.seq   = txSeq++;
  p.count = count;
  p.flags = flags;
  for (uint8_t i = 0; i < MMW_MAX_TARGETS; i++) p.targets[i] = targets[i];
  memcpy(pkt + sizeof(UdpHeader), &p, sizeof(p));
  IPAddress dest = parentKnown ? parentIp : IPAddress(255, 255, 255, 255);
  udp.beginPacket(dest, UDP_PORT);
  udp.write(pkt, sizeof(pkt));
  udp.endPacket();
}

void mmwUdpPoll() {
  int plen = udp.parsePacket();
  if (plen <= 0) return;
  if (plen > (int)sizeof(rxBuf)) {
    if (Serial) { Serial.print(F("MMW UDP: oversize datagram dropped: ")); Serial.println(plen); }
    udp.flush();
    return;
  }
  int n = udp.read(rxBuf, sizeof(rxBuf));
  if (n < (int)sizeof(UdpHeader)) return;

  UdpHeader h;
  memcpy(&h, rxBuf, sizeof(h));
  if (h.magic != UDP_MAGIC) return;
  if (h.version < 3 || h.version > UDP_VERSION) return;   // fleet-wide acceptance window

  const uint8_t* payload = rxBuf + sizeof(UdpHeader);
  uint16_t payLen = (uint16_t)(n - sizeof(UdpHeader));

  switch (h.cmd) {
    case CMD_PING:
      parentIp    = udp.remoteIP();
      parentKnown = true;
      sendPong(parentIp);
      break;

    case CMD_STATUS_REQ:
      sendStatus(udp.remoteIP(), udp.remotePort());
      break;

    case CMD_OTA_UPDATE: {
      // Payload: newMajor(1) + newMinor(1) + newPatch(1) + urlLen(2 LE) + url(N) + sha256hex(64)
      if (payLen <= 5) break;
      uint16_t urlLen = (uint16_t)(payload[3] | (payload[4] << 8));
      if (urlLen == 0 || urlLen >= (uint16_t)(payLen - 5)) break;
      char url[256];
      uint16_t copyLen = urlLen < 255 ? urlLen : 255;
      memcpy(url, &payload[5], copyLen);
      url[copyLen] = '\0';
      char sha[65] = {0};
      uint16_t shaOff = (uint16_t)(5 + urlLen);
      if (shaOff + 64 <= payLen) { memcpy(sha, &payload[shaOff], 64); sha[64] = '\0'; }
      if (Serial) { Serial.print(F("MMW OTA: update requested: ")); Serial.println(url); }
      if (mmwOtaStart(url, sha)) {
        delay(500);
        ESP.restart();
      }
      break;
    }

    default:
      break;   // unknown/foreign commands are ignored (fleet back-compat rule)
  }
}
