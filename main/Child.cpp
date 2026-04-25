/*
 * Child.cpp — Child node (ESP32 / D1 Mini) data, EEPROM config,
 *             UDP responses, HTTP config page and form handler.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "version.h"

#ifdef BOARD_CHILD

#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "HttpUtils.h"
#include "Child.h"
#include "version.h"

// ── Global data definitions ───────────────────────────────────────────────────

#ifdef BOARD_FASTLED
CRGB leds[NUM_LEDS];   // Giga child: leds[] defined in GigaLED.cpp
#endif

ChildSelfConfig childCfg;

volatile uint8_t  childActType  = 0;
volatile uint8_t  childActR     = 0;
volatile uint8_t  childActG     = 0;
volatile uint8_t  childActB     = 0;
volatile uint16_t childActP16a  = 500;
volatile uint8_t  childActP8a   = 0;
volatile uint8_t  childActP8b   = 0;
volatile uint8_t  childActP8c   = 0;
volatile uint8_t  childActP8d   = 0;
volatile uint8_t  childActSeq   = 0;
volatile uint16_t childActSt[MAX_STR_PER_CHILD];
volatile uint16_t childActEn[MAX_STR_PER_CHILD];
volatile uint8_t  childBrightness = 255;

ChildRunnerStep   childRunner[MAX_CHILD_STEPS];
volatile uint8_t  childStepCount    = 0;
volatile uint32_t childRunnerStart  = 0;
volatile bool     childRunnerArmed  = false;
volatile bool     childRunnerActive = false;
volatile uint8_t  childSyncBlink    = 0;
volatile bool     childRunnerLoop   = true;
volatile bool     childBootDone    = false;

volatile uint32_t childParentIP    = 0;
volatile bool     childEvtPending  = false;
volatile uint8_t  childEvtType     = 0;
volatile uint8_t  childEvtStep     = 0;
volatile uint8_t  childEvtTotal    = 0;
volatile uint8_t  childEvtEvent    = 0;

// ── EEPROM / NVS helpers ──────────────────────────────────────────────────────

void loadChildConfig() {
  bool loaded = false;
#if defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
  // Giga: no persistent storage — always use RAM defaults
#elif defined(BOARD_ESP32)
  Preferences prefs;
  prefs.begin("slyled", true);  // read-only
  if (prefs.getUChar("magic", 0) == EEPROM_MAGIC) {
    prefs.getBytes("cfg", &childCfg, sizeof(childCfg));
    loaded = true;
  }
  prefs.end();
#else  // D1 Mini
  EEPROM.begin(1 + (int)sizeof(childCfg));
  if (EEPROM.read(0) == EEPROM_MAGIC) {
    uint8_t* p = (uint8_t*)&childCfg;
    for (int i = 0; i < (int)sizeof(childCfg); i++) p[i] = EEPROM.read(1 + i);
    loaded = true;
  }
  EEPROM.end();
#endif
  // Hostname is always derived from MAC (cannot be misconfigured via form)
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(childCfg.hostname, HOSTNAME_LEN, "SLYC-%02X%02X", mac[4], mac[5]);
  // Default altName to hostname if not set
  if (childCfg.altName[0] == '\0') {
    strncpy(childCfg.altName, childCfg.hostname, CHILD_NAME_LEN - 1);
    childCfg.altName[CHILD_NAME_LEN - 1] = '\0';
  }
  if (!loaded) {
    saveChildConfig();  // first boot: persist defaults
    if (Serial) Serial.println(F("EEPROM: first boot, defaults saved."));
  }
}

void saveChildConfig() {
#if defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
  if (Serial) Serial.println(F("Config saved (RAM only)."));
  return;
#elif defined(BOARD_ESP32)
  Preferences prefs;
  prefs.begin("slyled", false);  // read-write
  prefs.putUChar("magic", EEPROM_MAGIC);
  prefs.putBytes("cfg", &childCfg, sizeof(childCfg));
  prefs.end();
#else  // D1 Mini
  EEPROM.begin(1 + (int)sizeof(childCfg));
  EEPROM.write(0, EEPROM_MAGIC);
  uint8_t* p = (uint8_t*)&childCfg;
  for (int i = 0; i < (int)sizeof(childCfg); i++) EEPROM.write(1 + i, p[i]);
  EEPROM.commit();
  EEPROM.end();
#endif
  if (Serial) Serial.println(F("Config saved to EEPROM."));
}

void clearChildConfig() {
#if defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
  return;
#elif defined(BOARD_ESP32)
  Preferences prefs;
  prefs.begin("slyled", false);
  prefs.clear();
  prefs.end();
#else  // D1 Mini
  EEPROM.begin(1 + (int)sizeof(childCfg));
  EEPROM.write(0, 0x00);
  EEPROM.commit();
  EEPROM.end();
#endif
  if (Serial) Serial.println(F("Config cleared."));
}

void initChildConfig() {
  // Set RAM defaults first (loadChildConfig overwrites if EEPROM is valid)
  memset(&childCfg, 0, sizeof(childCfg));
  childCfg.stringCount         = 1;
#ifdef BOARD_GIGA_CHILD
  childCfg.strings[0].ledCount = 1;    // onboard RGB = 1 pixel
  childCfg.strings[0].lengthMm = 4;    // ~4mm LED package
#else
  childCfg.strings[0].ledCount = 30;
  childCfg.strings[0].lengthMm = 500;
#endif
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].flags    = 0;   // not folded
  childCfg.strings[0].cableMm  = 0;
  childCfg.strings[0].stripDir = DIR_E;
  childCfg.strings[0].dataPin  = 2;   // default GPIO 2

  loadChildConfig();  // always regenerates hostname from MAC

  if (Serial) { Serial.print(F("Child hostname: ")); Serial.println(childCfg.hostname); }
}

// ── UDP send helpers ──────────────────────────────────────────────────────────

void sendPong(IPAddress dest) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_PONG;
  hdr.epoch   = (uint32_t)currentEpoch();

  PongPayload pong;
  memset(&pong, 0, sizeof(pong));
  strncpy(pong.hostname,    childCfg.hostname,    HOSTNAME_LEN   - 1);
  strncpy(pong.altName,     childCfg.altName,     CHILD_NAME_LEN - 1);
  strncpy(pong.description, childCfg.description, CHILD_DESC_LEN - 1);
  pong.stringCount = childCfg.stringCount;
  uint8_t sc = (childCfg.stringCount < MAX_STR_PER_CHILD)
             ? childCfg.stringCount : MAX_STR_PER_CHILD;
  for (uint8_t j = 0; j < sc; j++) {
    pong.strings[j].ledCount = childCfg.strings[j].ledCount;
    pong.strings[j].lengthMm = childCfg.strings[j].lengthMm;
    pong.strings[j].ledType  = childCfg.strings[j].ledType;
    pong.strings[j].cableDir = childCfg.strings[j].flags;  // cableDir byte carries flags (bit0=folded)
    pong.strings[j].cableMm  = childCfg.strings[j].cableMm;
    pong.strings[j].stripDir = childCfg.strings[j].stripDir;
  }
  pong.fwMajor = APP_MAJOR;
  pong.fwMinor = APP_MINOR;
  pong.fwPatch = APP_PATCH;
  memcpy(udpBuf,               &hdr,  sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr), &pong, sizeof(pong));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(pong));
  cmdUDP.endPacket();
}

void sendStatusResp(IPAddress dest) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_STATUS_RESP;
  hdr.epoch   = (uint32_t)currentEpoch();

  StatusRespPayload resp;
  resp.activeAction = childActType;
  resp.runnerActive = childRunnerActive ? 1 : 0;
  resp.currentStep  = 0;
  if (childRunnerActive && childStepCount > 0) {
    uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
    uint32_t acc = 0;
    for (uint8_t i = 0; i < childStepCount; i++) {
      acc += childRunner[i].durationS;
      if (elapsed < acc) { resp.currentStep = i; break; }
      resp.currentStep = childStepCount - 1;
    }
  }
  int32_t rssi = WiFi.RSSI();
  resp.wifiRssi = (rssi < 0) ? (uint8_t)(-rssi) : 0;
  resp.uptimeS  = (uint32_t)(millis() / 1000);

  memcpy(udpBuf,               &hdr,  sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr), &resp, sizeof(resp));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(resp));
  cmdUDP.endPacket();
}

void sendActionEvent() {
  uint32_t ip = childParentIP;
  if (ip == 0) return;
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_ACTION_EVENT;
  hdr.epoch   = (uint32_t)currentEpoch();

  ActionEventPayload evt;
  evt.actionType = childEvtType;
  evt.stepIndex  = childEvtStep;
  evt.totalSteps = childEvtTotal;
  evt.event      = childEvtEvent;

  memcpy(udpBuf,               &hdr, sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr), &evt, sizeof(evt));

  IPAddress dest((uint8_t)(ip & 0xFF), (uint8_t)((ip >> 8) & 0xFF),
                 (uint8_t)((ip >> 16) & 0xFF), (uint8_t)((ip >> 24) & 0xFF));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(evt));
  cmdUDP.endPacket();
}

// ── ESP32 multi-pin FastLED init ──────────────────────────────────────────────

#if defined(BOARD_ESP32) && !defined(BOARD_DMX_BRIDGE)

static void addLedsForPin(uint8_t pin, CRGB* data, uint16_t count) {
  switch (pin) {
    case  1: FastLED.addLeds<WS2812B,  1, GRB>(data, count); break;
    case  2: FastLED.addLeds<WS2812B,  2, GRB>(data, count); break;
    case  3: FastLED.addLeds<WS2812B,  3, GRB>(data, count); break;
    case  4: FastLED.addLeds<WS2812B,  4, GRB>(data, count); break;
    case  5: FastLED.addLeds<WS2812B,  5, GRB>(data, count); break;
    case 13: FastLED.addLeds<WS2812B, 13, GRB>(data, count); break;
    case 14: FastLED.addLeds<WS2812B, 14, GRB>(data, count); break;
    case 15: FastLED.addLeds<WS2812B, 15, GRB>(data, count); break;
    case 16: FastLED.addLeds<WS2812B, 16, GRB>(data, count); break;
    case 17: FastLED.addLeds<WS2812B, 17, GRB>(data, count); break;
    case 18: FastLED.addLeds<WS2812B, 18, GRB>(data, count); break;
    case 19: FastLED.addLeds<WS2812B, 19, GRB>(data, count); break;
    case 21: FastLED.addLeds<WS2812B, 21, GRB>(data, count); break;
    case 22: FastLED.addLeds<WS2812B, 22, GRB>(data, count); break;
    case 23: FastLED.addLeds<WS2812B, 23, GRB>(data, count); break;
    case 25: FastLED.addLeds<WS2812B, 25, GRB>(data, count); break;
    case 26: FastLED.addLeds<WS2812B, 26, GRB>(data, count); break;
    case 27: FastLED.addLeds<WS2812B, 27, GRB>(data, count); break;
    default: FastLED.addLeds<WS2812B,  2, GRB>(data, count); break;
  }
}

void esp32InitLeds() {
  uint16_t offset = 0;
  for (uint8_t s = 0; s < childCfg.stringCount; s++) {
    uint16_t count = childCfg.strings[s].ledCount;
    if (offset + count > NUM_LEDS) count = NUM_LEDS - offset;
    if (count == 0) break;
    uint8_t pin = childCfg.strings[s].dataPin;
    if (pin == 0) pin = DEFAULT_DATA_PIN;
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
    addLedsForPin(pin, &leds[offset], count);
    if (Serial) {
      Serial.print(F("String ")); Serial.print(s);
      Serial.print(F(": GPIO ")); Serial.print(pin);
      Serial.print(F(", LEDs ")); Serial.print(offset);
      Serial.print(F("..")); Serial.println(offset + count - 1);
    }
    offset += count;
  }
}

#endif  // BOARD_ESP32

// ── URL-encoded form helpers ──────────────────────────────────────────────────

uint8_t hexVal(char ch) {
  if (ch >= '0' && ch <= '9') return (uint8_t)(ch - '0');
  if (ch >= 'a' && ch <= 'f') return (uint8_t)(ch - 'a' + 10);
  if (ch >= 'A' && ch <= 'F') return (uint8_t)(ch - 'A' + 10);
  return 0;
}

int urlGetInt(const char* body, const char* key, int def) {
  char needle[14];
  // Prefer "&key=" to avoid matching key name as a suffix of another key (e.g. "sc=" inside "desc=")
  snprintf(needle, sizeof(needle), "&%s=", key);
  const char* p = strstr(body, needle);
  if (p) { p += strlen(needle); return atoi(p); }
  // Fall back to "key=" only at the very start of the body
  snprintf(needle, sizeof(needle), "%s=", key);
  if (strncmp(body, needle, strlen(needle)) == 0) return atoi(body + strlen(needle));
  return def;
}

void urlGetStr(const char* body, const char* key, char* out, uint8_t maxlen) {
  char needle[14];
  const char* p = NULL;
  // Prefer "&key=" to avoid matching key name as a suffix of another key
  snprintf(needle, sizeof(needle), "&%s=", key);
  const char* found = strstr(body, needle);
  if (found) {
    p = found + strlen(needle);
  } else {
    // Fall back to "key=" only at the very start of the body
    snprintf(needle, sizeof(needle), "%s=", key);
    if (strncmp(body, needle, strlen(needle)) == 0) p = body + strlen(needle);
  }
  if (!p) { out[0] = '\0'; return; }
  uint8_t i = 0;
  while (*p && *p != '&' && i < maxlen - 1) {
    if (*p == '+') { out[i++] = ' '; p++; }
    else if (*p == '%' && *(p+1) && *(p+2)) {
      out[i++] = (char)((hexVal(*(p+1)) << 4) | hexVal(*(p+2)));
      p += 3;
    } else { out[i++] = *p++; }
  }
  out[i] = '\0';
}

// ── POST /config/reset ────────────────────────────────────────────────────────

void handleFactoryReset(WiFiClient& c) {
  clearChildConfig();
  memset(&childCfg, 0, sizeof(childCfg));
  childCfg.stringCount         = 1;
#ifdef BOARD_GIGA_CHILD
  childCfg.strings[0].ledCount = 1;    // onboard RGB = 1 pixel
  childCfg.strings[0].lengthMm = 4;    // ~4mm LED package
#else
  childCfg.strings[0].ledCount = 30;
  childCfg.strings[0].lengthMm = 500;
#endif
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].stripDir = DIR_E;
  childCfg.strings[0].dataPin  = 2;
  loadChildConfig();  // regenerates hostname, defaults altName, saves to EEPROM
  sendPong(IPAddress(255, 255, 255, 255));
  c.print(F("HTTP/1.1 303 See Other\r\n"
            "Location: /\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"));
  c.flush();
}

// ── GET /config — 3-tab SPA ───────────────────────────────────────────────────

void sendChildConfigPage(WiFiClient& c) {
  // HTTP header + CSS
  c.print(F("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
            "Cache-Control: no-cache, no-store\r\n\r\n"
            "<!DOCTYPE html><html><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SlyLED</title><style>"
            "*{box-sizing:border-box;margin:0;padding:0}"
            "body{font-family:sans-serif;background:#0A0F13;color:#e2e8f0;padding:1.2em;max-width:480px}"));
  c.print(F("h1{font-size:1.4em;margin-bottom:.1em}"
            "h2{font-size:.8em;color:#888;font-weight:normal;margin-bottom:.8em}"
            ".tabs{display:flex;gap:4px;margin-bottom:1em}"
            ".tab{flex:1;padding:.4em;background:#0f172a;border:1px solid #334155;"
            "border-radius:5px;color:#999;font-size:.85em;cursor:pointer;text-align:center}"
            ".tact{background:#14532d;border-color:#22c55e;color:#86efac}"
            ".pane{display:none}.row{display:flex;justify-content:space-between;"
            "padding:.35em 0;border-bottom:1px solid #222;font-size:.9em}"));
  c.print(F(".k{color:#aaa}.v{font-weight:bold}"
            "label{display:block;font-size:.82em;color:#aaa;margin:.5em 0 .15em}"
            "input,select{width:100%;background:#0f172a;color:#e2e8f0;border:1px solid #334155;"
            "border-radius:4px;padding:.3em .5em;font-size:.88em;margin-bottom:.3em}"
            ".btn{display:inline-block;padding:.4em 1.2em;background:#14532d;color:#86efac;"
            "border:none;border-radius:5px;cursor:pointer;font-size:.9em;margin-top:.6em}"
            ".btn-warn{background:#7f1d1d;color:#fca5a5}"
            ".btn:active{transform:scale(.95);opacity:.7}"
            ".ftr{margin-top:1.5em;font-size:.7em;color:#444}"
            "</style></head><body>"));

  // Header
  sendBuf(c, "<h1>SlyLED Performer</h1><h2>%s</h2>", childCfg.altName);

  // Tab nav
  c.print(F("<div class='tabs'>"
            "<div class='tab tact' id='n0' onclick='showTab(0)'>Dashboard</div>"
            "<div class='tab' id='n1' onclick='showTab(1)'>Settings</div>"
            "<div class='tab' id='n2' onclick='showTab(2)'>Config</div>"
            "</div>"));

  // ── Dashboard pane ─────────────────────────────────────────────────────────
  c.print(F("<div class='pane' id='p0'>"));
  sendBuf(c, "<div class='row'><span class='k'>Hostname</span>"
             "<span class='v'>%s</span></div>", childCfg.hostname);
  sendBuf(c, "<div class='row'><span class='k'>Name</span>"
             "<span class='v'>%s</span></div>", childCfg.altName);
  sendBuf(c, "<div class='row'><span class='k'>Description</span>"
             "<span class='v'>%s</span></div>",
             childCfg.description[0] ? childCfg.description : "--");
  sendBuf(c, "<div class='row'><span class='k'>Strings</span>"
             "<span class='v'>%u</span></div>", (unsigned)childCfg.stringCount);
  // Total LEDs across all strings
  {
    uint16_t totalLeds = 0;
    for (uint8_t i = 0; i < childCfg.stringCount; i++) totalLeds += childCfg.strings[i].ledCount;
    sendBuf(c, "<div class='row'><span class='k'>Total LEDs</span>"
               "<span class='v'>%u</span></div>", (unsigned)totalLeds);
  }
  c.print(F("<div class='row'><span class='k'>Action</span>"
            "<span class='v' id='act'>--</span></div>"));
  // Board info
#ifdef BOARD_ESP32
  c.print(F("<div class='row'><span class='k'>Board</span>"
            "<span class='v'>ESP32</span></div>"));
#elif defined(BOARD_D1MINI)
  c.print(F("<div class='row'><span class='k'>Board</span>"
            "<span class='v'>D1 Mini (ESP8266)</span></div>"));
#elif defined(BOARD_GIGA_CHILD)
  c.print(F("<div class='row'><span class='k'>Board</span>"
            "<span class='v'>Giga R1 WiFi</span></div>"));
#endif
  sendBuf(c, "<div class='row'><span class='k'>Firmware</span>"
             "<span class='v'>v%u.%u.%u</span></div>",
             (unsigned)APP_MAJOR, (unsigned)APP_MINOR, (unsigned)APP_PATCH);
  // Chip details
#ifdef BOARD_ESP32
  sendBuf(c, "<div class='row'><span class='k'>Chip</span>"
             "<span class='v'>%s</span></div>", ESP.getChipModel());
  sendBuf(c, "<div class='row'><span class='k'>Chip Temp</span>"
             "<span class='v'>%d &deg;C</span></div>", (int)temperatureRead());
#elif defined(BOARD_D1MINI)
  c.print(F("<div class='row'><span class='k'>Chip</span>"
            "<span class='v'>ESP8266</span></div>"));
#endif
#if !defined(BOARD_GIGA_DMX) && !defined(BOARD_GIGA_CHILD)
  sendBuf(c, "<div class='row'><span class='k'>Flash</span>"
             "<span class='v'>%lu KB</span></div>",
             (unsigned long)(ESP.getFlashChipSize() / 1024));
  sendBuf(c, "<div class='row'><span class='k'>Free Heap</span>"
             "<span class='v'>%lu bytes</span></div>",
             (unsigned long)ESP.getFreeHeap());
#endif
#if defined(BOARD_GIGA_DMX) || defined(BOARD_GIGA_CHILD)
  sendBuf(c, "<div class='row'><span class='k'>SDK</span>"
             "<span class='v'>mbed</span></div>");
#else
  sendBuf(c, "<div class='row'><span class='k'>SDK</span>"
             "<span class='v'>%s</span></div>", ESP.getSdkVersion());
#endif
  // Network details
  sendBuf(c, "<div class='row'><span class='k'>WiFi RSSI</span>"
             "<span class='v'>%d dBm</span></div>",
             (int)WiFi.RSSI());
  sendBuf(c, "<div class='row'><span class='k'>IP Address</span>"
             "<span class='v'>%s</span></div>",
             WiFi.localIP().toString().c_str());
  sendBuf(c, "<div class='row'><span class='k'>Uptime</span>"
             "<span class='v' id='upt'>%lu s</span></div>",
             (unsigned long)(millis() / 1000));
  c.print(F("<div style='margin-top:.8em;padding:.6em;background:#1a1a1a;"
            "border:1px solid #333;border-radius:5px'>"
            "<div style='display:flex;align-items:center;gap:.5em'>"
            "<button class='btn' type='button' onclick='checkUpdate()' id='upd-btn'>"
            "Check for Updates</button>"
            "<span id='upd-status' style='font-size:.82em;color:#888'></span></div>"
            "<div id='upd-progress' style='display:none;margin-top:.5em'>"
            "<div style='background:#333;border-radius:3px;height:6px;width:100%'>"
            "<div id='upd-bar' style='background:#4c4;height:6px;border-radius:3px;"
            "width:0%;transition:width .3s'></div></div>"
            "<div id='upd-msg' style='font-size:.75em;color:#aaa;margin-top:.2em'></div>"
            "</div></div>"
            "</div>"));
#ifdef BOARD_DMX_BRIDGE
  c.print(F("<div class='card' style='margin-top:.5em'>"
            "<h3>DMX Output</h3>"
            "<div class='row'><span class='k'>Status</span><span class='v' id='dmx-status'>--</span></div>"
            "<div class='row'><span class='k'>Address</span><span class='v' id='dmx-addr-v'>--</span></div>"
            "<div class='row'><span class='k'>Channels</span><span class='v' id='dmx-ch-v'>--</span></div>"
            "<div class='row'><span class='k'>TX Packets</span><span class='v' id='dmx-frames'>0</span></div>"
            "<div class='row'><span class='k'>Self Test</span><span class='v' id='dmx-selftest'>--</span></div>"
            "</div>"
            "<div class='card' style='margin-top:.5em'>"
            "<h3>Art-Net Input</h3>"
            "<div class='row'><span class='k'>RX Packets</span><span class='v' id='an-rx'>0</span></div>"
            "<div class='row'><span class='k'>Rate</span><span class='v' id='an-pps'>0 pps</span></div>"
            "<div class='row'><span class='k'>Source</span><span class='v' id='an-src'>--</span></div>"
            "</div>"));
#endif

  // ── Settings pane (inside the main form) ───────────────────────────────────
  c.print(F("<form id='cf' action='/config' method='POST'>"));
  c.print(F("<div class='pane' id='p1'>"));
  c.print(F("<label>Name</label><input name='an' maxlength='15' value='"));
  c.print(childCfg.altName);
  c.print(F("'><label>Description</label><input name='desc' maxlength='31' value='"));
  c.print(childCfg.description);
  c.print(F("'>"));
#ifndef BOARD_DMX_BRIDGE
  c.print(F("<label>Number of strings</label><select name='sc' id='sc' onchange='scChg()'>"));
  for (uint8_t n = 1; n <= CHILD_MAX_STRINGS; n++)
    sendBuf(c, "<option value='%u'%s>%u</option>",
            (unsigned)n, n == childCfg.stringCount ? " selected" : "", (unsigned)n);
  c.print(F("</select>"));
#endif
  c.print(F("<button class='btn' type='button' id='sb1' onclick='doSave(this)'>Save Settings</button>"
            "<button class='btn btn-warn' type='button' style='margin-left:.5em'"
            " onclick=\"document.getElementById('rf').submit()\">Factory Reset</button>"));
  c.print(F("</div>"));

#ifndef BOARD_DMX_BRIDGE
  // ── Config pane (LED strings — not shown for DMX bridge) ───────────────────
  c.print(F("<div class='pane' id='p2'>"));
  c.print(F("<label>String</label>"
            "<select id='ss' onchange='showStr(this.value)'>"));
  for (uint8_t n = 0; n < childCfg.stringCount; n++)
    sendBuf(c, "<option value='%u'>String %u</option>", (unsigned)n, (unsigned)(n + 1));
  c.print(F("</select>"));

  // Per-string fieldsets (all rendered; JS shows selected one)
  for (uint8_t j = 0; j < CHILD_MAX_STRINGS; j++) {
    sendBuf(c, "<div id='s%u' style='display:none'>", (unsigned)j);
    sendBuf(c, "<label>LED count</label>"
               "<input name='lc%u' type='number' min='1' max='254' value='%u'>",
               (unsigned)j, (unsigned)childCfg.strings[j].ledCount);
    sendBuf(c, "<label>Length (mm)</label>"
               "<input name='lm%u' type='number' min='1' max='65535' value='%u'>",
               (unsigned)j, (unsigned)childCfg.strings[j].lengthMm);
    sendBuf(c, "<label>LED type</label>"
               "<select name='lt%u'>"
               "<option value='0'%s>WS2812B</option>"
               "<option value='1'%s>WS2811</option>"
               "<option value='2'%s>APA102</option></select>",
               (unsigned)j,
               childCfg.strings[j].ledType == 0 ? " selected" : "",
               childCfg.strings[j].ledType == 1 ? " selected" : "",
               childCfg.strings[j].ledType == 2 ? " selected" : "");
    sendBuf(c, "<label>Direction</label>"
               "<select name='sd%u'>"
               "<option value='0'%s>East</option>"
               "<option value='1'%s>North</option>"
               "<option value='2'%s>West</option>"
               "<option value='3'%s>South</option></select>",
               (unsigned)j,
               childCfg.strings[j].stripDir == 0 ? " selected" : "",
               childCfg.strings[j].stripDir == 1 ? " selected" : "",
               childCfg.strings[j].stripDir == 2 ? " selected" : "",
               childCfg.strings[j].stripDir == 3 ? " selected" : "");
    sendBuf(c, "<label style='display:inline-flex;align-items:center;gap:.3em;margin-top:.5em'>"
               "<input type='checkbox' name='fd%u' value='1' style='width:auto'%s> Folded</label>",
               (unsigned)j,
               (childCfg.strings[j].flags & STR_FLAG_FOLDED) ? " checked" : "");
#ifdef BOARD_ESP32
    sendBuf(c, "<label>Data Pin (GPIO)</label>"
               "<div style='display:flex;gap:.4em;align-items:center'>"
               "<select name='dp%u' id='dp%u' style='flex:1'>", (unsigned)j, (unsigned)j);
    for (uint8_t p = 0; p < ESP32_SAFE_PIN_COUNT; p++) {
      sendBuf(c, "<option value='%u'%s>GPIO %u</option>",
              (unsigned)ESP32_SAFE_PINS[p],
              childCfg.strings[j].dataPin == ESP32_SAFE_PINS[p] ? " selected" : "",
              (unsigned)ESP32_SAFE_PINS[p]);
    }
    sendBuf(c, "</select>"
               "<button class='btn' type='button' style='background:#363;padding:.2em .6em;font-size:.8em'"
               " onclick='testPin(%u)'>Test</button></div>", (unsigned)j);
#endif
    c.print(F("</div>"));
  }
  c.print(F("<button class='btn' type='button' id='sb2' onclick='doSave(this)'>Save Config</button>"
            "<div style='margin-top:.8em;padding:.6em;background:#1a1a1a;border:1px solid #333;border-radius:5px'>"
            "<label style='margin-top:0'>Test Effect</label>"
            "<select id='tt'>"
            "<option value='1'>Solid</option>"
            "<option value='2'>Fade</option>"
            "<option value='3'>Breathe</option>"
            "<option value='4'>Chase</option>"
            "<option value='5' selected>Rainbow</option>"
            "<option value='6'>Fire</option>"
            "<option value='7'>Comet</option>"
            "<option value='8'>Twinkle</option>"
            "<option value='9'>Strobe</option>"
            "<option value='10'>Color Wipe</option>"
            "<option value='11'>Scanner</option>"
            "<option value='12'>Sparkle</option>"
            "<option value='13'>Gradient</option>"
            "</select>"
            "<button class='btn' type='button' style='background:#363;margin-left:.3em' onclick='doTest()'>Run</button>"
            " <button class='btn btn-warn' type='button' onclick='doTestStop()'>Stop</button>"
            "</div>"
            "</div>"));
  c.print(F("</form>"));

  // Factory reset (separate form — HTML forbids nested forms)
  c.print(F("<form id='rf' action='/config/reset' method='POST' style='display:none'></form>"));
#endif  // !BOARD_DMX_BRIDGE

#ifdef BOARD_DMX_BRIDGE
  // ── DMX Config pane (replaces LED config pane p2 for DMX bridge) ──────────
  c.print(F("<div class='pane' id='p2'>"));
  // Section 1: DMX Output Hardware
  c.print(F("<div class='card'><h3>DMX Output</h3>"
            "<p style='font-size:.8em;color:#94a3b8;margin:.2em 0'>"
            "CQRobot Ocean DMX shield on Serial1 (TX1). 250kbaud, 40Hz.</p>"
            "<table style='font-size:.78em;color:#94a3b8;border-collapse:collapse;margin:.3em 0'>"
            "<tr><td style='padding:.15em .5em;border:1px solid #334'>TX</td>"
            "<td style='padding:.15em .5em;border:1px solid #334'><b style='color:#4c9'>tx-uart</b></td>"
            "<td style='padding:.15em .5em;border:1px solid #334;color:#556'>Serial1 drives RS-485</td></tr>"
            "<tr><td style='padding:.15em .5em;border:1px solid #334'>RX</td>"
            "<td style='padding:.15em .5em;border:1px solid #334'><b style='color:#4c9'>rx-uart</b></td>"
            "<td style='padding:.15em .5em;border:1px solid #334;color:#556'>For future receive</td></tr>"
            "<tr><td style='padding:.15em .5em;border:1px solid #334'>Slave/DE</td>"
            "<td style='padding:.15em .5em;border:1px solid #334'><b style='color:#4c9'>DE</b></td>"
            "<td style='padding:.15em .5em;border:1px solid #334;color:#556'>Direction enable = transmit</td></tr>"
            "<tr><td style='padding:.15em .5em;border:1px solid #334'>EN/not-EN</td>"
            "<td style='padding:.15em .5em;border:1px solid #334'><b style='color:#4c9'>EN</b></td>"
            "<td style='padding:.15em .5em;border:1px solid #334;color:#556'>RS-485 chip enabled</td></tr>"
            "</table>"
            "<p style='font-size:.75em;color:#c9a;margin:.3em 0'>"
            "&#9888; Set EN to <b>not-EN</b> before uploading firmware (DFU conflicts with RS-485 chip).</p>"
            "<div id='dmx-diag' style='font-size:.8em;color:#64748b;margin:.3em 0'></div>"
            "</div>"));
  c.flush();
  // Section 2: Art-Net Settings
  c.print(F("<div class='card' style='margin-top:.5em'><h3>Network Input</h3>"
            "<div style='display:grid;grid-template-columns:auto 1fr;gap:.3em .6em;align-items:center;margin:.5em 0'>"
            "<label>Subnet</label><input type='number' id='dmx-sn' min='0' max='15' value='0' style='width:80px'>"
            "<label>Universe <span style='color:#888;font-size:.75em'>(0=Desktop U1)</span></label>"
            "<input type='number' id='dmx-uni' min='0' max='15' value='0' style='width:80px'>"
            "<label>Input Mode</label>"
            "<select id='dmx-im' style='width:160px'>"
            "<option value='0'>SlyLED only</option>"
            "<option value='1'>Art-Net</option>"
            "<option value='2'>sACN</option>"
            "<option value='3' selected>Auto (Art-Net + sACN)</option>"
            "</select>"
            "</div>"
            "<button type='button' class='btn' onclick='dmxSaveGw()' style='margin:.3em 0'>Save</button>"
            "</div>"));
  c.flush();
  // Section 3: Fixture Test
  c.print(F("<div class='card' style='margin-top:.5em'><h3>Fixture Test</h3>"
            "<div style='display:grid;grid-template-columns:auto 1fr;gap:.3em .6em;align-items:center;margin:.5em 0'>"
            "<label>Start Address</label><input type='number' id='dmx-sa' min='1' max='512' style='width:80px'>"
            "<label>Channels</label><input type='number' id='dmx-cpf' min='1' max='24' style='width:80px'>"
            "</div>"
            "<input type='hidden' id='dmx-fc' value='1'>"
            "<button type='button' class='btn' onclick='dmxApplyTest()' style='margin:.3em 0'>OK</button>"
            "<div style='margin:.5em 0'>"
            "<button type='button' class='btn' style='background:#c33' onclick='dmxBlackout()'>Blackout</button>"
            "</div>"
            "<div id='dmx-sliders' style='margin:.5em 0'></div>"
            "</div>"));
  c.print(F("</div>"));
  // DMX Channel Monitor Grid (32 channels)
  c.print(F("<div class='card' style='margin-top:.5em'>"
            "<h3>DMX Monitor</h3>"
            "<div id='dmx-grid' style='display:grid;grid-template-columns:repeat(8,1fr);gap:3px;font-size:.7em'></div>"
            "</div>"));
#endif

  // Footer — version only; Factory Reset lives in the Settings tab
  sendBuf(c, "<div class='ftr'>v%d.%d.%d</div>", APP_MAJOR, APP_MINOR, APP_PATCH);

  // JavaScript
  c.print(F("<script>"));
  c.print(F("function showTab(t){"
            "for(var i=0;i<3;i++){"
            "var p=document.getElementById('p'+i);if(p)p.style.display=i==t?'block':'none';"
            "var n=document.getElementById('n'+i);if(n)n.className='tab'+(i==t?' tact':'');}"
            "}"));
  c.print(F("function showStr(v){"
            "var n=parseInt(document.getElementById('sc').value);"
            "for(var i=0;i<n;i++){"
            "document.getElementById('s'+i).style.display=i==parseInt(v)?'block':'none';}"
            "}"));
  c.print(F("function scChg(){"
            "var n=parseInt(document.getElementById('sc').value);"
            "var ss=document.getElementById('ss');"
            "ss.innerHTML='';"
            "for(var i=0;i<n;i++){"
            "var o=document.createElement('option');"
            "o.value=i;o.text='String '+(i+1);ss.appendChild(o);}"));
  sendBuf(c, "for(var i=0;i<%u;i++){", (unsigned)CHILD_MAX_STRINGS);
  c.print(F("var el=document.getElementById('s'+i);"
            "if(el)el.style.display='none';}showStr(0);}"));
  c.print(F("function poll(){"
            "var x=new XMLHttpRequest();"
            "x.open('GET','/status',true);"
            "x.onload=function(){try{"
            "var d=JSON.parse(x.responseText);"
            "var n=['Off','Solid','Flash','Wipe'];"
            "document.getElementById('act').textContent=n[d.action]||'?';"
            "}catch(e){}};"
            "x.send();}"));
  // Store initial pin values for change detection
  c.print(F("var _curTab=0;var _oldPins={};"));
  for (uint8_t j = 0; j < childCfg.stringCount; j++)
    sendBuf(c, "_oldPins[%u]=%u;", (unsigned)j, (unsigned)childCfg.strings[j].dataPin);
  c.print(F("function doSave(btn){"
            "var orig=btn.textContent;btn.textContent='Saving...';btn.disabled=true;"
            "btn.style.background='#555';"
            "var fd=new FormData(document.getElementById('cf'));"
            "var pinChanged=false;"));
#ifdef BOARD_ESP32
  c.print(F("for(var k in _oldPins){"
            "var sel=document.getElementById('dp'+k);"
            "if(sel&&parseInt(sel.value)!==_oldPins[k]){pinChanged=true;break;}}"));
#endif
  c.print(F("var x=new XMLHttpRequest();"
            "x.open('POST','/config',true);"
            "x.onload=function(){"
            "if(pinChanged){"
            "btn.textContent='Rebooting...';btn.style.background='#c60';"
            "setTimeout(function(){var r=new XMLHttpRequest();r.open('POST','/reboot',true);"
            "r.send();setTimeout(function(){location.reload();},5000);},800);"
            "}else{"
            "btn.textContent='Saved!';btn.style.background='#2a2';"
            "setTimeout(function(){btn.textContent=orig;btn.style.background='';btn.disabled=false;},1200);}};"
            "x.onerror=function(){btn.textContent='Error';btn.style.background='#a22';};"
            "x.send(new URLSearchParams(fd));}"));
  c.print(F("function doTest(){"
            "var t=document.getElementById('tt').value;"
            "var x=new XMLHttpRequest();x.open('GET','/test?t='+t,true);"
            "x.send();}"));
  c.print(F("function doTestStop(){"
            "var x=new XMLHttpRequest();x.open('POST','/test/stop',true);"
            "x.send();}"));
  c.print(F("function testPin(s){"
            "var x=new XMLHttpRequest();"
            "x.open('GET','/test/pin?s='+s,true);x.send();}"));
  c.print(F("function checkUpdate(){"
            "var btn=document.getElementById('upd-btn');"
            "var st=document.getElementById('upd-status');"
            "btn.disabled=true;btn.textContent='Checking...';"
            "st.textContent='';"
            "var x=new XMLHttpRequest();"
            "x.open('GET','https://api.github.com/repos/SlyWombat/SlyLED/releases/latest',true);"
            "x.onload=function(){"
            "try{var d=JSON.parse(x.responseText);"
            "var tag=d.tag_name||'';var ver=tag.replace('v','');"
            "var parts=ver.split('.');var rmaj=parseInt(parts[0])||0;var rmin=parseInt(parts[1])||0;var rpat=parseInt(parts[2])||0;"));
  sendBuf(c,
            "var cmaj=%u;var cmin=%u;var cpat=%u;"
            "var rver=rmaj*10000+rmin*100+rpat;var cver=cmaj*10000+cmin*100+cpat;",
            (unsigned)APP_MAJOR, (unsigned)APP_MINOR, (unsigned)APP_PATCH);
  c.flush();
  c.print(F("if(rver>cver){"
            "st.innerHTML='<b style=\"color:#f60\">v'+ver+' available!</b>';"
            "btn.textContent='Install Update';"
            "btn.disabled=false;"
            "btn.onclick=function(){doOta(d);};"
            "}else{"));
  c.flush();
  c.print(F("st.textContent='Up to date (v'+cmaj+'.'+cmin+'.'+cpat+')';"
            "btn.textContent='Check for Updates';btn.disabled=false;}"
            "}catch(e){st.textContent='Check failed';btn.textContent='Check for Updates';btn.disabled=false;}};"));
  c.flush();
  c.print(F("x.onerror=function(){st.textContent='Cannot reach cloud';btn.textContent='Check for Updates';btn.disabled=false;};"
            "x.send();}"));
  c.flush();
  c.print(F("function doOta(rel){"
            "var btn=document.getElementById('upd-btn');"
            "var st=document.getElementById('upd-status');"
            "var prog=document.getElementById('upd-progress');"
            "var bar=document.getElementById('upd-bar');"
            "var msg=document.getElementById('upd-msg');"
            "btn.disabled=true;btn.textContent='Updating...';"
            "prog.style.display='block';bar.style.width='10%';msg.textContent='Finding binary...';"
            // Find the right asset for this board
            "var assets=rel.assets||[];var url=null;"
            "for(var i=0;i<assets.length;i++){"));
#ifdef BOARD_ESP32
  c.print(F(  "if(assets[i].name==='esp32-firmware-merged.bin'){url=assets[i].browser_download_url;break;}}"));
#elif defined(BOARD_D1MINI)
  c.print(F(  "if(assets[i].name==='d1mini-firmware.bin'){url=assets[i].browser_download_url;break;}}"));
#else
  c.print(F(  "}"));
#endif
  c.flush();
  c.print(F("if(!url){msg.textContent='No firmware binary found in release';btn.disabled=false;return;}"
            "bar.style.width='20%';msg.textContent='Sending update command...';"
            "var tag=rel.tag_name||'';var ver=tag.replace('v','');"));
  c.flush();
  c.print(F("var parts=ver.split('.');var maj=parseInt(parts[0])||0;var mn=parseInt(parts[1])||0;var pt=parseInt(parts[2])||0;"
            "var x=new XMLHttpRequest();"
            "x.open('POST','/ota',true);"
            "x.setRequestHeader('Content-Type','application/json');"
            "x.onload=function(){"
            "bar.style.width='50%';msg.textContent='Downloading firmware... device will reboot';"
            "setTimeout(function(){bar.style.width='80%';msg.textContent='Rebooting...';},5000);"
            "setTimeout(function(){bar.style.width='100%';msg.textContent='Update complete — refreshing...';location.reload();},20000);};"
            "x.onerror=function(){msg.textContent='Update failed — device may be rebooting';bar.style.width='100%';"
            "setTimeout(function(){location.reload();},15000);};"
            "x.send(JSON.stringify({url:url,sha256:'',major:maj,minor:mn,patch:pt}));}"));
#ifdef BOARD_DMX_BRIDGE
  c.print(F(
    "var _dmxNames=[];"
    "function dmxBlackout(){fetch('/dmx/blackout',{method:'POST'}).then(function(){setTimeout(dmxRefresh,100);});}"
  ));
  c.flush();
  c.print(F(
    "function dmxSaveGw(){"
    "var b={subnet:parseInt(document.getElementById('dmx-sn').value)||0,"
    "universe:parseInt(document.getElementById('dmx-uni').value)||0,"
    "inputMode:parseInt((document.getElementById('dmx-im')||{value:3}).value)};"
    "fetch('/dmx/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(function(){dmxRefresh();});}"
    "function dmxApplyTest(){"
    "var cpf=parseInt(document.getElementById('dmx-cpf').value)||13;"
    "var sa=parseInt(document.getElementById('dmx-sa').value)||1;"
    "fetch('/dmx/config',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({startAddr:sa,chPerFix:cpf,fixCount:1})}).then(function(){dmxRefresh();});}"
  ));
  c.flush();
  c.print(F(
    "function dmxRefresh(){"
    "fetch('/dmx/channels').then(function(r){return r.json();}).then(function(d){"
    "var sn=document.getElementById('dmx-sn');if(sn)sn.value=d.subnet||0;"
    "document.getElementById('dmx-uni').value=d.universe;"
    "var im=document.getElementById('dmx-im');if(im&&typeof d.inputMode==='number')im.value=d.inputMode;"
    "document.getElementById('dmx-sa').value=d.start;"
    "document.getElementById('dmx-cpf').value=d.chPerFix;"
    "document.getElementById('dmx-fc').value=d.fixCount;"
    "_dmxNames=d.names||[];"
    "var diag=document.getElementById('dmx-diag');"
    "if(diag)diag.textContent='Packets: '+d.frames+(d.active?' (active)':' (stopped)');"
  ));
  c.flush();
  c.print(F(
    "var ds=document.getElementById('dmx-status');if(ds)ds.textContent=d.active?'Active':'Stopped';"
    "var da=document.getElementById('dmx-addr-v');if(da)da.textContent=d.start+' ('+d.chPerFix+' ch)';"
    "var dc=document.getElementById('dmx-ch-v');if(dc)dc.textContent=d.chPerFix+' x '+d.fixCount;"
    "var df=document.getElementById('dmx-frames');if(df)df.textContent=d.frames;"
    "var dt=document.getElementById('dmx-selftest');if(dt){dt.textContent=d.selfTest?'PASS':'FAIL';dt.style.color=d.selfTest?'#4c9':'#f44';}"
  ));
  c.flush();
  c.print(F(
    ""
  ));
  c.flush();
  c.print(F(
    "var el=document.getElementById('dmx-sliders');var h='';"
    "for(var i=0;i<d.ch.length&&i<d.chPerFix*d.fixCount;i++){"
    "var ch=d.start+i;var v=d.ch[i];"
    "h+='<div style=\"display:flex;align-items:center;gap:.3em;margin:.12em 0\">';"
    "h+='<span style=\"width:24px;font-size:.7em;color:#556;text-align:right\">'+ch+'</span>';"
    "h+='<input type=\"range\" min=\"0\" max=\"255\" value=\"'+v+'\" style=\"flex:1\" ';"
    "h+='oninput=\"dmxCh('+ch+',this.value)\">';"
    "h+='<span id=\"dv'+ch+'\" style=\"width:24px;font-size:.75em;color:#94a3b8;text-align:right\">'+v+'</span>';"
    "h+='</div>';}"
    "el.innerHTML=h;dmxGrid(d);});}"
  ));
  c.flush();
  c.print(F(
    "function dmxCh(ch,v){"
    "var s=document.getElementById('dv'+ch);if(s)s.textContent=v;"
    "var d={};d[ch]=parseInt(v);fetch('/dmx/set',{method:'POST',body:JSON.stringify(d)});}"
    "function dmxGrid(d){"
    "var g=document.getElementById('dmx-grid');if(!g)return;"
    "var h='';for(var i=0;i<32;i++){"
    "var ch=d.start+i;var v=(i<d.ch.length)?d.ch[i]:0;"
    "var bg=v>0?'rgba(76,153,76,'+(v/255*0.6+0.1)+')':'#1a1a2e';"
    "h+='<div style=\"background:'+bg+';border:1px solid #333;border-radius:3px;padding:3px;text-align:center\">';"
    "h+='<div style=\"color:#64748b\">'+ch+'</div>';"
    "h+='<div style=\"color:#e2e8f0;font-weight:bold\">'+v+'</div></div>';}"
    "g.innerHTML=h;}"
    "dmxRefresh();setInterval(function(){"
    "fetch('/dmx/channels').then(function(r){return r.json();}).then(function(d){"
    "var df=document.getElementById('dmx-frames');if(df)df.textContent=d.frames;"
    "var ds=document.getElementById('dmx-status');if(ds)ds.textContent=d.active?'Active':'Stopped';"
    "var ar=document.getElementById('an-rx');if(ar)ar.textContent=d.artnetRx||0;"
    "var ap=document.getElementById('an-pps');if(ap)ap.textContent=(d.artnetPps||0)+' pps';"
    "var as2=document.getElementById('an-src');if(as2)as2.textContent=d.artnetSender||'--';"
    "dmxGrid(d);});},2000);"
  ));
  c.flush();
#endif
  c.print(F("showTab(0);showStr(0);poll();setInterval(poll,3000);"
            "</script></body></html>"));
  c.flush();
}

// ── POST /config ──────────────────────────────────────────────────────────────

void handlePostChildConfig(WiFiClient& c, int contentLen) {
  static char body[400];
  int rlen = (contentLen > 0 && contentLen < (int)sizeof(body) - 1)
             ? contentLen : (int)sizeof(body) - 1;
  c.readBytes(body, rlen);
  body[rlen] = '\0';

  char tmp[CHILD_DESC_LEN];
  urlGetStr(body, "an",   tmp, CHILD_NAME_LEN);
  strncpy(childCfg.altName, tmp, CHILD_NAME_LEN - 1);
  childCfg.altName[CHILD_NAME_LEN - 1] = '\0';
  // Default altName to hostname if cleared
  if (childCfg.altName[0] == '\0') {
    strncpy(childCfg.altName, childCfg.hostname, CHILD_NAME_LEN - 1);
    childCfg.altName[CHILD_NAME_LEN - 1] = '\0';
  }
  urlGetStr(body, "desc", tmp, CHILD_DESC_LEN);
  strncpy(childCfg.description, tmp, CHILD_DESC_LEN - 1);
  childCfg.description[CHILD_DESC_LEN - 1] = '\0';

  int sc = urlGetInt(body, "sc", 1);
  if (sc < 1) sc = 1;
  if (sc > CHILD_MAX_STRINGS) sc = CHILD_MAX_STRINGS;
  childCfg.stringCount = (uint8_t)sc;

  char key[8];
  for (uint8_t j = 0; j < CHILD_MAX_STRINGS; j++) {
    snprintf(key, sizeof(key), "lc%u", (unsigned)j);
    int lc = urlGetInt(body, key, 8); if (lc < 1) lc = 1; if (lc > 254) lc = 254;
    childCfg.strings[j].ledCount = (uint16_t)lc;
    snprintf(key, sizeof(key), "lm%u", (unsigned)j);
    int lm = urlGetInt(body, key, 500); if (lm < 1) lm = 1;
    childCfg.strings[j].lengthMm = (uint16_t)lm;
    snprintf(key, sizeof(key), "lt%u", (unsigned)j);
    int lt = urlGetInt(body, key, 0); if (lt < 0) lt = 0; if (lt > 2) lt = 2;
    childCfg.strings[j].ledType  = (uint8_t)lt;
    snprintf(key, sizeof(key), "sd%u", (unsigned)j);
    int sd = urlGetInt(body, key, 0); if (sd < 0) sd = 0; if (sd > 3) sd = 3;
    childCfg.strings[j].stripDir = (uint8_t)sd;
    // Folded checkbox: present in POST body as fd0=1, fd1=1 etc.
    snprintf(key, sizeof(key), "fd%u", (unsigned)j);
    childCfg.strings[j].flags = urlGetInt(body, key, 0) ? STR_FLAG_FOLDED : 0;
    childCfg.strings[j].cableMm  = 0;
#ifdef BOARD_ESP32
    snprintf(key, sizeof(key), "dp%u", (unsigned)j);
    int dp = urlGetInt(body, key, DEFAULT_DATA_PIN);
    bool pinOk = false;
    for (uint8_t p = 0; p < ESP32_SAFE_PIN_COUNT; p++) {
      if (dp == ESP32_SAFE_PINS[p]) { pinOk = true; break; }
    }
    childCfg.strings[j].dataPin = pinOk ? (uint8_t)dp : DEFAULT_DATA_PIN;
#else
    childCfg.strings[j].dataPin = 2;
#endif
  }

  saveChildConfig();

  // Send 200 OK (XHR expects a response, not a redirect)
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 11\r\n"
            "Connection: close\r\n\r\n"
            "{\"ok\":true}"));
  c.flush();
  delay(50);

  sendPong(IPAddress(255, 255, 255, 255));  // notify parent of updated config
}

#endif  // BOARD_CHILD
