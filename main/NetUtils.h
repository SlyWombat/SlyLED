/*
 * NetUtils.h — WiFi connection, NTP, and periodic serial status.
 */

#ifndef NETUTILS_H
#define NETUTILS_H

void syncNTP();
unsigned long currentEpoch();
void connectWiFi();
void printStatus();

// WiFi credential storage (NVS/EEPROM — survives OTA and power cycles)
void loadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen);
void saveWiFiCredentials(const char* ssid, const char* pass);
bool hasStoredWiFiCredentials();

#endif  // NETUTILS_H
