/*
 * NetUtils.h — WiFi connection, NTP, and periodic serial status.
 */

#ifndef NETUTILS_H
#define NETUTILS_H

void syncNTP();
unsigned long currentEpoch();
void connectWiFi();
void printStatus();

#endif  // NETUTILS_H
