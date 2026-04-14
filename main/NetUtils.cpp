/*
 * NetUtils.cpp — WiFi connection, NTP, and periodic serial status.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "Child.h"          // initChildConfig() — guarded by #ifdef BOARD_FASTLED inside Child.h
#include "ArtNetRecv.h"     // artnetInit() — guarded by #ifdef BOARD_DMX_BRIDGE
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

// ── WiFi credential storage (survives OTA + power cycles) ────────────────────

#if defined(BOARD_ESP32) || defined(BOARD_GYRO)
#include <Preferences.h>

void loadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen) {
  Preferences prefs;
  prefs.begin("slyled-wifi", true);  // read-only
  String s = prefs.getString("ssid", "");
  String p = prefs.getString("pass", "");
  prefs.end();
  if (s.length() > 0) {
    strncpy(ssid, s.c_str(), ssidLen - 1); ssid[ssidLen - 1] = '\0';
    strncpy(pass, p.c_str(), passLen - 1); pass[passLen - 1] = '\0';
  } else {
    // No stored credentials — use compiled defaults and save them
    strncpy(ssid, SECRET_SSID, ssidLen - 1); ssid[ssidLen - 1] = '\0';
    strncpy(pass, SECRET_PASS, passLen - 1); pass[passLen - 1] = '\0';
    saveWiFiCredentials(ssid, pass);
  }
}

void saveWiFiCredentials(const char* ssid, const char* pass) {
  Preferences prefs;
  prefs.begin("slyled-wifi", false);
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
  if (Serial) Serial.println(F("WiFi credentials saved to NVS."));
}

bool hasStoredWiFiCredentials() {
  Preferences prefs;
  prefs.begin("slyled-wifi", true);
  String s = prefs.getString("ssid", "");
  prefs.end();
  return s.length() > 0;
}

#elif defined(BOARD_D1MINI)  // BOARD_ESP32 / BOARD_GYRO block ends above

// D1 Mini: store WiFi creds at EEPROM offset after childCfg
// Layout: [0]=magic, [1..sizeof(childCfg)]=config, [next]=wifi_magic, ssid[33], pass[65]
#include <EEPROM.h>

static constexpr int WIFI_EEPROM_OFFSET = 1 + (int)sizeof(ChildSelfConfig) + 4;  // skip config + padding
static constexpr uint8_t WIFI_MAGIC = 0xB7;

void loadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen) {
  EEPROM.begin(WIFI_EEPROM_OFFSET + 1 + 33 + 65);
  if (EEPROM.read(WIFI_EEPROM_OFFSET) == WIFI_MAGIC) {
    for (size_t i = 0; i < 33 && i < ssidLen; i++) ssid[i] = EEPROM.read(WIFI_EEPROM_OFFSET + 1 + i);
    ssid[ssidLen - 1] = '\0';
    for (size_t i = 0; i < 65 && i < passLen; i++) pass[i] = EEPROM.read(WIFI_EEPROM_OFFSET + 34 + i);
    pass[passLen - 1] = '\0';
  } else {
    strncpy(ssid, SECRET_SSID, ssidLen - 1); ssid[ssidLen - 1] = '\0';
    strncpy(pass, SECRET_PASS, passLen - 1); pass[passLen - 1] = '\0';
    // Save compiled defaults so they persist across OTA
    EEPROM.write(WIFI_EEPROM_OFFSET, WIFI_MAGIC);
    for (size_t i = 0; i < 33; i++) EEPROM.write(WIFI_EEPROM_OFFSET + 1 + i, i < strlen(ssid) ? ssid[i] : 0);
    for (size_t i = 0; i < 65; i++) EEPROM.write(WIFI_EEPROM_OFFSET + 34 + i, i < strlen(pass) ? pass[i] : 0);
    EEPROM.commit();
  }
  EEPROM.end();
}

void saveWiFiCredentials(const char* ssid, const char* pass) {
  EEPROM.begin(WIFI_EEPROM_OFFSET + 1 + 33 + 65);
  EEPROM.write(WIFI_EEPROM_OFFSET, WIFI_MAGIC);
  for (size_t i = 0; i < 33; i++) EEPROM.write(WIFI_EEPROM_OFFSET + 1 + i, i < strlen(ssid) ? ssid[i] : 0);
  for (size_t i = 0; i < 65; i++) EEPROM.write(WIFI_EEPROM_OFFSET + 34 + i, i < strlen(pass) ? pass[i] : 0);
  EEPROM.commit();
  EEPROM.end();
  if (Serial) Serial.println(F("WiFi credentials saved to EEPROM."));
}

bool hasStoredWiFiCredentials() {
  EEPROM.begin(WIFI_EEPROM_OFFSET + 1);
  bool has = EEPROM.read(WIFI_EEPROM_OFFSET) == WIFI_MAGIC;
  EEPROM.end();
  return has;
}

#else  // Giga (parent or child)

void loadWiFiCredentials(char* ssid, size_t ssidLen, char* pass, size_t passLen) {
  strncpy(ssid, SECRET_SSID, ssidLen - 1); ssid[ssidLen - 1] = '\0';
  strncpy(pass, SECRET_PASS, passLen - 1); pass[passLen - 1] = '\0';
}
void saveWiFiCredentials(const char*, const char*) {}
bool hasStoredWiFiCredentials() { return true; }

#endif

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  // Load credentials from persistent storage (NVS/EEPROM)
  // First boot: uses compiled defaults from arduino_secrets.h and saves them
  char wifiSSID[33] = {};
  char wifiPASS[65] = {};
  loadWiFiCredentials(wifiSSID, sizeof(wifiSSID), wifiPASS, sizeof(wifiPASS));

  if (Serial) { Serial.print("Connecting to "); Serial.println(wifiSSID); }

#if defined(BOARD_FASTLED)
  // ESP boards: derive hostname from MAC before WiFi.begin
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
#elif defined(BOARD_GYRO)
  // Gyro board: derive hostname from MAC, prefix SLYG- to distinguish from LED children
  {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    char hn[HOSTNAME_LEN];
    snprintf(hn, sizeof(hn), "SLYG-%02X%02X", mac[4], mac[5]);
    WiFi.setHostname(hn);
  }
#elif defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
  // Giga child/DMX: WiFi.macAddress() returns zeros before begin(),
  // so we set hostname after connect in initChildConfig()
#elif defined(BOARD_GIGA)
  // WiFi.setHostname() must be called before WiFi.begin() for DHCP option 12
  WiFi.setHostname(HOSTNAME);
#endif

  WiFi.begin(wifiSSID, wifiPASS);
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

#ifdef BOARD_DMX_BRIDGE
  artnetInit();
#endif

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
