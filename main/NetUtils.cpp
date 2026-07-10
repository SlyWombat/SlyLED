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
#ifdef BOARD_GYRO
  #include <esp_mac.h>      // esp_efuse_mac_get_default()
#endif

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

// One-time post-connect service bring-up (HTTP server, NTP, UDP command
// channel, board-specific init). Split out of connectWiFi() under #B3 so
// maintainWiFi() can also start services when the first successful join
// happens AFTER boot (AP slower to power up than the board).
static bool netServicesUp = false;

static void startNetServices() {
  if (netServicesUp) return;
  netServicesUp = true;

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
  // ESP32-S3: WiFi.macAddress() returns zeros before WiFi.mode() — use efuse instead
  {
    uint8_t mac[6];
    esp_efuse_mac_get_default(mac);
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

#ifdef BOARD_GYRO
  // Disable WiFi power save — reduces RTT from ~500ms to ~5ms.
  // Critical for real-time orientation streaming.
  WiFi.setSleep(false);
  if (Serial) Serial.println(F("WiFi power save disabled (low latency mode)"));
#endif

  startNetServices();
}

// ── Mbed WiFi reconnect supervision (#B3) ────────────────────────────────────
// ESP cores rejoin a lost AP on their own; the Mbed core does NOT — its
// statusCallback merely flips status() to WL_CONNECTION_LOST and the board
// stays offline until power-cycle. maintainWiFi() runs from loop() on the
// Giga variants: a state machine that re-runs WiFi.begin() with exponential
// backoff once the link has been down past a grace period.
//
// NOTE: Mbed's WiFi.begin() is synchronous (scan + join, ~7 s core join
// timeout; worst case ~10-15 s including the scan) — there is no async
// connect on this core. The blocking window only occurs while the AP is
// already unreachable, and backoff caps it at one attempt per RETRY_MAX_MS;
// between attempts the render/DMX paths run at full rate. NEEDS BENCH
// VALIDATION on Giga hardware before release — in particular that bound
// UDP/TCP sockets keep working across a reconnect + DHCP re-lease.
#if defined(BOARD_GIGA) || defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)

void maintainWiFi() {
  constexpr unsigned long CHECK_MS     = 2000;    // status poll cadence
  constexpr unsigned long RETRY_MIN_MS = 10000;   // grace + first retry delay
  constexpr unsigned long RETRY_MAX_MS = 60000;   // backoff ceiling
  static unsigned long lastCheck   = 0;
  static unsigned long lastAttempt = 0;
  static unsigned long retryMs     = RETRY_MIN_MS;
  static bool          down        = false;

  unsigned long now = millis();
  if (now - lastCheck < CHECK_MS) return;
  lastCheck = now;

  if (WiFi.status() == WL_CONNECTED) {
    if (down) {
      if (Serial) { Serial.print(F("WiFi: reconnected. IP: ")); Serial.println(WiFi.localIP()); }
      startNetServices();   // no-op unless boot-time connect never succeeded
    }
    down    = false;
    retryMs = RETRY_MIN_MS;
    return;
  }

  if (!down) {
    // Just noticed the loss — arm the retry timer (grace period first, in
    // case the driver recovers the roam on its own).
    down        = true;
    lastAttempt = now;
    if (Serial) Serial.println(F("WiFi: link down — reconnect supervision armed"));
    return;
  }
  if (now - lastAttempt < retryMs) return;
  lastAttempt = now;
  if (retryMs < RETRY_MAX_MS) retryMs *= 2;

  char ssid[33] = {};
  char pass[65] = {};
  loadWiFiCredentials(ssid, sizeof(ssid), pass, sizeof(pass));
  if (Serial) { Serial.print(F("WiFi: reconnecting to ")); Serial.println(ssid); }
  WiFi.disconnect();   // clear half-up driver state before re-begin
#ifdef BOARD_GIGA
  // Quirk: WiFi.setHostname() must precede WiFi.begin() for DHCP option 12.
  WiFi.setHostname(HOSTNAME);
#else
  // Giga child/DMX derive their hostname from the MAC, which reads valid
  // once the radio has been up at least once (see connectWiFi()).
  if (childCfg.hostname[0]) WiFi.setHostname(childCfg.hostname);
#endif
  WiFi.begin(ssid, pass);   // synchronous on Mbed — see NOTE above
}

#endif  // BOARD_GIGA || BOARD_GIGA_CHILD || BOARD_GIGA_DMX

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
