/*
 * Globals.cpp — definitions of shared global variables.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "Globals.h"

WiFiServer   server(80);
WiFiUDP      ntpUDP;
WiFiUDP      cmdUDP;
uint8_t      udpBuf[160];
char         _txbuf[256];
unsigned long ntpEpoch  = 0;
unsigned long ntpMillis = 0;

#ifdef BOARD_GIGA
const char HOSTNAME[] = "slyled";
#endif
