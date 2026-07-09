/*
 * Globals.h — extern declarations for shared global variables.
 *
 * Board-specific data (children[], childCfg, etc.) are declared in
 * Parent.h and Child.h respectively.
 */

#ifndef GLOBALS_H
#define GLOBALS_H

#include "BoardConfig.h"
#include "Protocol.h"

extern WiFiServer    server;
extern WiFiUDP       ntpUDP;
extern WiFiUDP       cmdUDP;
// #895 — sized for the largest inbound datagram: OTA_UPDATE is header(8) +
// ver(3) + urlLen(2) + url(≤255) + sha256(64) = 332 max (a 160-byte buffer
// silently capped OTA URLs at ~83 chars). Next largest is PONG: header(8) +
// PongPayload(134) = 142.
extern uint8_t       udpBuf[384];
extern char          _txbuf[256];   // scratch buffer for sendBuf()
extern unsigned long ntpEpoch;
extern unsigned long ntpMillis;
extern volatile uint32_t udpRxCount;   // total UDP packets received (debug)

#ifdef BOARD_GIGA
extern const char HOSTNAME[];       // "slyled"
#endif

#endif  // GLOBALS_H
