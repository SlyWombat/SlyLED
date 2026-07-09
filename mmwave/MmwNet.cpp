/*
 * MmwNet.cpp — WiFi + NTP for the MMwave node (ESP32-C61, arduino-esp32 ≥3.3.5).
 */

#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <time.h>
#include "MmwConfig.h"
#include "MmwNet.h"
#include "arduino_secrets.h"   // SECRET_SSID / SECRET_PASS (gitignored; see .example)

static char     hostnameBuf[10] = {0};   // HOSTNAME_LEN on the wire is 10 incl. NUL
static uint32_t lastReconnectMs = 0;

static Preferences prefs;

void mmwLoadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen) {
  prefs.begin("mmwave", true);
  size_t sl = prefs.getString("ssid", ssid, ssidLen);
  size_t pl = prefs.getString("pass", pass, passLen);
  prefs.end();
  if (sl == 0 || pl == 0) {
    strncpy(ssid, SECRET_SSID, ssidLen - 1); ssid[ssidLen - 1] = '\0';
    strncpy(pass, SECRET_PASS, passLen - 1); pass[passLen - 1] = '\0';
  }
}

void mmwSaveWiFiCredentials(const char* ssid, const char* pass) {
  prefs.begin("mmwave", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
}

void mmwNetBegin() {
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(hostnameBuf, sizeof(hostnameBuf), MMW_HOSTNAME_PREFIX "%02X%02X", mac[4], mac[5]);

  char ssid[33] = {0};
  char pass[65] = {0};
  mmwLoadWiFiCredentials(ssid, sizeof(ssid), pass, sizeof(pass));

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(hostnameBuf);         // must precede begin() — DHCP option 12
  WiFi.begin(ssid, pass);
  if (Serial) { Serial.print(F("MMW: WiFi connecting as ")); Serial.println(hostnameBuf); }

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) delay(100);
  if (Serial) {
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print(F("MMW: WiFi up, IP ")); Serial.println(WiFi.localIP());
    } else {
      Serial.println(F("MMW: WiFi connect timeout — supervision will retry"));
    }
  }

  configTime(0, 0, "pool.ntp.org", "time.nist.gov");   // async; mmwEpoch() gates on sync
}

bool mmwNetConnected() { return WiFi.status() == WL_CONNECTED; }

void mmwNetPoll() {
  // ESP32 cores auto-reconnect, but belt-and-braces: nudge every 15 s if down.
  if (WiFi.status() == WL_CONNECTED) return;
  uint32_t now = millis();
  if (now - lastReconnectMs < 15000) return;
  lastReconnectMs = now;
  WiFi.reconnect();
}

const char* mmwHostname() { return hostnameBuf; }

uint32_t mmwEpoch() {
  time_t t = time(nullptr);
  return (t > 1000000000) ? (uint32_t)t : 0;   // 0 until NTP has synced
}

uint8_t mmwRssiAbs() {
  if (WiFi.status() != WL_CONNECTED) return 0;
  int8_t r = (int8_t)WiFi.RSSI();
  return (uint8_t)(r < 0 ? -r : r);
}
