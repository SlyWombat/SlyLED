/*
 * UdpCommon.h — UDP packet dispatch and HTTP server loop.
 *               Shared across all boards.
 */

#ifndef UDPCOMMON_H
#define UDPCOMMON_H

#include "BoardConfig.h"

// Dispatch a validated UDP packet (magic/version already checked).
void handleUdpPacket(uint8_t cmd, IPAddress sender, uint8_t* payload, int plen);

// Read one UDP packet from cmdUDP and call handleUdpPacket.
void pollUDP();

// Read and route one HTTP request from an already-accepted client.
void serveClient(WiFiClient& client, unsigned int waitMs);

// Accept and drain all pending HTTP clients.
void handleClient();

#endif  // UDPCOMMON_H
