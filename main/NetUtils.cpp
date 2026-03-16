/*
 * NetUtils.cpp — WiFi connection, NTP, and periodic serial status.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "Child.h"          // initChildConfig() — guarded by #ifdef BOARD_FASTLED inside Child.h
#include "arduino_secrets.h"

// ── NTP ───────────────────────────────────────────────────────────────────────

void syncNTP() {
  uint8_t buf[48] = {};
  buf[0] = 0b11100011; buf[2] = 6; buf[3] = 0xEC;
  buf[12] = 49; buf[13] = 0x4E; buf[14] = 49; buf[15] = 52;
  ntpUDP.begin(2390);
  ntpUDP.beginPacket("pool.ntp.org", 123);
  ntpUDP.write(buf, 48);
  ntpUDP.endPacket();
  unsigned long start = millis();
  while (millis() - start < 3000) {
    if (ntpUDP.parsePacket()) {
      ntpUDP.read(buf, 48);
      unsigned long secs = (unsigned long)buf[40] << 24 | (unsigned long)buf[41] << 16
                         | (unsigned long)buf[42] <<  8 | (unsigned long)buf[43];
      ntpEpoch  = secs - 2208988800UL;
      ntpMillis = millis();
      if (Serial) { Serial.print("NTP synced. Epoch: "); Serial.println(ntpEpoch); }
      break;
    }
    delay(10);
  }
  ntpUDP.stop();
  if (ntpEpoch == 0 && Serial) Serial.println("NTP sync failed.");
}

unsigned long currentEpoch() {
  if (ntpEpoch == 0) return millis() / 1000;
  return ntpEpoch + (millis() - ntpMillis) / 1000;
}

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  if (Serial) { Serial.print("Connecting to "); Serial.println(SECRET_SSID); }

#ifdef BOARD_CHILD
  // Derive hostname from MAC before WiFi.begin so DHCP gets the correct name
  {
    uint8_t mac[6];
#ifdef BOARD_D1MINI
    WiFi.mode(WIFI_STA);
#endif
    WiFi.macAddress(mac);
    char hn[HOSTNAME_LEN];
    snprintf(hn, sizeof(hn), "SLYC-%02X%02X", mac[4], mac[5]);
#ifdef BOARD_D1MINI
    WiFi.hostname(hn);
#else
    WiFi.setHostname(hn);
#endif
  }
#elif defined(BOARD_GIGA)
  // WiFi.setHostname() must be called before WiFi.begin() for DHCP option 12
  WiFi.setHostname(HOSTNAME);
#endif

  WiFi.begin(SECRET_SSID, SECRET_PASS);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t > 20000) { if (Serial) Serial.println("\r\nWiFi timeout."); return; }
    delay(500);
    if (Serial) Serial.print('.');
  }
  if (Serial) { Serial.println(); Serial.print("Connected. IP: "); Serial.println(WiFi.localIP()); }
  server.begin();
  syncNTP();
  cmdUDP.begin(UDP_PORT);
  if (Serial) Serial.println(F("UDP command channel open on port 4210."));

#ifdef BOARD_CHILD
  initChildConfig();
#endif
}

// ── Periodic serial status print ─────────────────────────────────────────────

void printStatus() {
  static unsigned long last = 0;
  if (millis() - last >= 3000) {
    last = millis();
    if (!Serial) return;
    Serial.print("IP: ");     Serial.print(WiFi.localIP());
    Serial.print("  WiFi: "); Serial.println(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
  }
}
