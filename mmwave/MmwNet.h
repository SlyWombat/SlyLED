/*
 * MmwNet.h — WiFi onboarding, hostname, NTP epoch.
 *
 * Common-onboarding contract (design doc §4.2): compiled arduino_secrets.h
 * credentials, overridable by NVS-stored credentials that survive OTA;
 * hostname set BEFORE WiFi.begin() so DHCP option 12 carries it
 * (CLAUDE.md hardware quirks).
 */

#ifndef MMW_NET_H
#define MMW_NET_H

#include <stdint.h>

void        mmwNetBegin();          // hostname + connect + start NTP (non-blocking sync)
bool        mmwNetConnected();
void        mmwNetPoll();           // reconnect supervision
const char* mmwHostname();          // "MMW-XXXX"
uint32_t    mmwEpoch();             // unix seconds once NTP synced, else 0
uint8_t     mmwRssiAbs();           // |RSSI| dBm, 0 if unknown

// NVS credential override (survives OTA and power cycles)
void mmwLoadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen);
void mmwSaveWiFiCredentials(const char* ssid, const char* pass);

#endif  // MMW_NET_H
