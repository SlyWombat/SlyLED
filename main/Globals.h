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
extern uint8_t       udpBuf[160];   // header(8) + PongPayload(131) = 139 max; 160 for safety
extern char          _txbuf[256];   // scratch buffer for sendBuf()
extern unsigned long ntpEpoch;
extern unsigned long ntpMillis;

#ifdef BOARD_GIGA
extern const char HOSTNAME[];       // "slyled"
#endif

#endif  // GLOBALS_H
