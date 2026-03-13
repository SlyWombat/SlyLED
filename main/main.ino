/*
 * SlyLED — multi-board sketch
 * Phase 2a: UDP command channel, parent child registry, child self-config
 *
 * Supported targets:
 *   Arduino Giga R1 WiFi  (arduino:mbed_giga:giga)    — parent: child registry, no LEDs
 *   ESP32 Dev Module      (esp32:esp32:esp32)           — child: WS2812B strip, FastLED
 *   LOLIN D1 Mini         (esp8266:esp8266:d1_mini)    — child: WS2812B strip, FastLED
 *
 * Threading model:
 *   Giga / ESP32 : dedicated LED thread independent of WiFi — zero animation jitter
 *   D1 Mini      : non-blocking updateLED() called from loop() — minimal jitter
 *
 * Phase 2a adds:
 *   - UDP command channel on port 4210 (all boards)
 *   - CMD_PING / CMD_PONG: parent broadcasts, children respond with full config
 *   - CMD_STATUS_REQ / CMD_STATUS_RESP: parent queries, children reply
 *   - Child self-config: hostname SLYC-XXXX from MAC; string count/length/direction
 *   - Parent child registry: children[] array, registerChild(), 30 s auto-discovery
 *   - GET  /api/children — JSON list of known children
 *   - POST /api/children — register a child by IP: body {"ip":"x.x.x.x"}
 *
 * HTTP routes (all boards):
 *   GET  /              — SPA main page
 *   GET  /status        — JSON LED status
 *   POST /led/on        — enable Rainbow
 *   POST /led/siren/on  — enable Siren
 *   POST /led/off       — disable all
 *   GET  /log           — event log HTML
 *
 * Additional routes (Giga parent only):
 *   GET  /api/children  — JSON array of registered children
 *   POST /api/children  — register child by IP
 */

#include "version.h"
#include "arduino_secrets.h"

// ── Board detection ───────────────────────────────────────────────────────────

#if defined(ESP32)
  #define BOARD_ESP32
#elif defined(ESP8266) || defined(ARDUINO_ARCH_ESP8266)
  #define BOARD_D1MINI
#elif defined(ARDUINO_GIGA) || defined(ARDUINO_ARDUINO_GIGA) || defined(ARDUINO_ARCH_MBED_GIGA) || defined(ARDUINO_ARCH_MBED)
  #define BOARD_GIGA
#else
  #error "Unsupported board. Target: arduino:mbed_giga:giga | esp32:esp32:esp32 | esp8266:esp8266:d1_mini"
#endif

// Helper: boards that use FastLED (ESP32 and D1 Mini — these are the children)
#if defined(BOARD_ESP32) || defined(BOARD_D1MINI)
  #define BOARD_FASTLED
#endif

// ── Board-specific includes ───────────────────────────────────────────────────

#ifdef BOARD_GIGA
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_ESP32)
  #include <FastLED.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#else  // D1 Mini
  #include <FastLED.h>
  #include <ESP8266WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#endif

// ── LED hardware constants ────────────────────────────────────────────────────

#ifdef BOARD_GIGA
  constexpr uint8_t  HUE_STEP   = 2;
  constexpr int      DISPLAY_MS = 35;
  constexpr uint16_t PWM_STEPS  = 256;
  constexpr uint8_t  STEP_US    = 8;
  #define CARD_TITLE "Onboard LED"

#else  // FastLED boards (ESP32 / D1 Mini)
  #ifdef BOARD_D1MINI
    #define DATA_PIN  2
  #else
    #define DATA_PIN  2
  #endif
  #define NUM_LEDS      8
  #define LED_TYPE      WS2812B
  #define COLOR_ORDER   GRB
  constexpr uint8_t LED_BRIGHTNESS = 200;
  constexpr uint8_t RAINBOW_DELTA  = 256 / NUM_LEDS;
  constexpr int     RAINBOW_DELAY  = 20;
  CRGB leds[NUM_LEDS];
  #define CARD_TITLE "LED Strip"
#endif

constexpr int SIREN_HALF_MS = 350;

// ── Phase 2: UDP protocol constants ──────────────────────────────────────────
// Port 4210 is used by all nodes. Parent broadcasts CMD_PING; children respond
// with CMD_PONG containing their full self-config.

constexpr uint16_t UDP_PORT    = 4210;
constexpr uint16_t UDP_MAGIC   = 0x534C;  // 'S','L' — identifies SlyLED packets
constexpr uint8_t  UDP_VERSION = 2;

// Shared string-field sizes (must match on parent and all children)
constexpr uint8_t HOSTNAME_LEN   = 10;   // "SLYC-XXXX\0"
constexpr uint8_t CHILD_NAME_LEN = 16;   // user-set alternate name, incl. null
constexpr uint8_t CHILD_DESC_LEN = 32;   // user-set description, incl. null
constexpr uint8_t MAX_STR_PER_CHILD = 4; // max LED strings per child node

// UDP command codes
// uint8_t (not enum) to avoid Mbed auto-prototype generator issue with enum params
constexpr uint8_t CMD_PING        = 0x01; // parent → broadcast: discover children
constexpr uint8_t CMD_PONG        = 0x02; // child  → parent:    I'm here + config
constexpr uint8_t CMD_STATUS_REQ  = 0x40; // parent → child:     send status
constexpr uint8_t CMD_STATUS_RESP = 0x41; // child  → parent:    status reply

// ── Phase 2: UDP packet structures ───────────────────────────────────────────
// __attribute__((packed)) ensures no padding so sizeof() is reliable and
// memcpy between buffer and struct is safe on all three platforms.

struct __attribute__((packed)) UdpHeader {
  uint16_t magic;    // UDP_MAGIC
  uint8_t  version;  // UDP_VERSION
  uint8_t  cmd;      // CMD_* constant
  uint32_t epoch;    // sender's current Unix timestamp
};  // 8 bytes

// Wire format for one LED string in a PONG packet
struct __attribute__((packed)) PongString {
  uint16_t ledCount;  // number of LEDs
  uint16_t lengthMm;  // physical strip length in mm
  uint8_t  ledType;   // 0=WS2812B, 1=WS2811, 2=APA102
  uint8_t  cableDir;  // 0=E,1=N,2=W,3=S — cable from node to strip start
  uint16_t cableMm;   // cable length in mm
  uint8_t  stripDir;  // 0=E,1=N,2=W,3=S — direction strip runs
};  // 9 bytes

// Payload of a CMD_PONG packet (follows UdpHeader)
struct __attribute__((packed)) PongPayload {
  char       hostname[HOSTNAME_LEN];       // "SLYC-XXXX\0" auto from MAC
  char       altName[CHILD_NAME_LEN];      // user-set name
  char       description[CHILD_DESC_LEN];  // user-set description
  uint8_t    stringCount;
  PongString strings[MAX_STR_PER_CHILD];   // 4 × 9 = 36 bytes
};  // 10+16+32+1+36 = 95 bytes

// Total PongPacket = UdpHeader(8) + PongPayload(95) = 103 bytes

// Payload of a CMD_STATUS_RESP packet (follows UdpHeader)
struct __attribute__((packed)) StatusRespPayload {
  uint8_t  activeAction;  // 0=off, 1=rainbow, 2=siren
  uint8_t  runnerActive;  // 0 or 1
  uint8_t  currentStep;   // runner step index
  uint8_t  wifiRssi;      // abs(RSSI) e.g. 70 means -70 dBm
  uint32_t uptimeS;       // seconds since boot
};  // 8 bytes

// ── WiFi, server, and UDP ─────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";
WiFiServer server(80);
WiFiUDP    ntpUDP;   // temporary, opened/closed around NTP syncs
WiFiUDP    cmdUDP;   // persistent, port 4210, open after WiFi connects
uint8_t    udpBuf[128]; // shared send/receive buffer — covers largest packet (103 B)

// ── Shared module state ───────────────────────────────────────────────────────

volatile bool ledRainbowOn = true;
volatile bool ledSirenOn   = false;

// ── Giga: Mbed RTOS LED thread ────────────────────────────────────────────────

#ifdef BOARD_GIGA
rtos::Thread ledThread;
#endif

// ── Parent data structures (Giga only) ───────────────────────────────────────

#ifdef BOARD_GIGA

constexpr uint8_t MAX_CHILDREN = 8;

// Status of a child node as seen by the parent
// Using uint8_t values rather than an enum to avoid prototype generator issues
constexpr uint8_t CHILD_UNKNOWN = 0;
constexpr uint8_t CHILD_ONLINE  = 1;
constexpr uint8_t CHILD_OFFLINE = 2;

// One LED string as stored in the parent registry
struct StringInfo {       // 9 bytes (packed-equivalent — no padding with these types)
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};

// One registered child node
struct ChildNode {
  uint8_t    ip[4];
  char       hostname[HOSTNAME_LEN];
  char       name[CHILD_NAME_LEN];
  char       description[CHILD_DESC_LEN];
  int16_t    xMm, yMm, zMm;          // canvas position (set in Phase 2b)
  uint8_t    stringCount;
  StringInfo strings[MAX_STR_PER_CHILD];
  uint8_t    status;                  // CHILD_UNKNOWN / ONLINE / OFFLINE
  uint32_t   lastSeenEpoch;
  bool       configFetched;
  bool       inUse;
};

ChildNode children[MAX_CHILDREN];

#endif  // BOARD_GIGA

// ── Child self-config data (ESP32 / D1 Mini only) ────────────────────────────

#ifdef BOARD_FASTLED

// Direction constants — 0=E(+X), 1=N(+Y), 2=W(-X), 3=S(-Y)
constexpr uint8_t DIR_E = 0;
constexpr uint8_t DIR_N = 1;
constexpr uint8_t DIR_W = 2;
constexpr uint8_t DIR_S = 3;

// LED strip hardware type
constexpr uint8_t LEDTYPE_WS2812B = 0;
constexpr uint8_t LEDTYPE_WS2811  = 1;
constexpr uint8_t LEDTYPE_APA102  = 2;

struct ChildStringCfg {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};

struct ChildSelfConfig {
  char           hostname[HOSTNAME_LEN];
  char           altName[CHILD_NAME_LEN];
  char           description[CHILD_DESC_LEN];
  uint8_t        stringCount;
  ChildStringCfg strings[MAX_STR_PER_CHILD];
};

ChildSelfConfig childCfg;

#endif  // BOARD_FASTLED

// ── NTP ───────────────────────────────────────────────────────────────────────

unsigned long ntpEpoch  = 0;
unsigned long ntpMillis = 0;

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
      Serial.print("NTP synced. Epoch: "); Serial.println(ntpEpoch);
      break;
    }
    delay(10);
  }
  ntpUDP.stop();
  if (ntpEpoch == 0) Serial.println("NTP sync failed.");
}

unsigned long currentEpoch() {
  if (ntpEpoch == 0) return millis() / 1000;
  return ntpEpoch + (millis() - ntpMillis) / 1000;
}

void formatTime(unsigned long epoch, char* buf, uint8_t len) {
  if (ntpEpoch == 0) { snprintf(buf, len, "T+%lus", epoch); return; }
  time_t t = (time_t)epoch;
  struct tm* ti = gmtime(&t);
  strftime(buf, len, "%Y-%m-%d %H:%M:%S UTC", ti);
}

// ── Event log ─────────────────────────────────────────────────────────────────

enum LogSource  : uint8_t { SRC_WEB = 0, SRC_BOOT = 1 };
enum LedFeature : uint8_t { FEAT_NONE = 0, FEAT_RAINBOW = 1, FEAT_SIREN = 2 };

struct LogEntry {
  unsigned long epoch;
  uint8_t       ip[4];
  uint8_t       feature;
  LogSource     source;
};

constexpr uint8_t MAX_LOG = 50;
LogEntry logBuf[MAX_LOG];
uint8_t  logCount = 0;
uint8_t  logNext  = 0;

void addLog(uint8_t feat, uint8_t src, uint8_t ip0, uint8_t ip1, uint8_t ip2, uint8_t ip3) {
  LogEntry& e = logBuf[logNext % MAX_LOG];
  e.epoch   = currentEpoch();
  e.feature = feat;
  e.source  = (LogSource)src;
  e.ip[0] = ip0; e.ip[1] = ip1; e.ip[2] = ip2; e.ip[3] = ip3;
  logNext++;
  if (logCount < MAX_LOG) logCount++;
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

char _txbuf[256];

void sendBuf(WiFiClient& c, const char* fmt, ...) {
  va_list ap; va_start(ap, fmt);
  vsnprintf(_txbuf, sizeof(_txbuf), fmt, ap);
  va_end(ap);
  c.print(_txbuf);
}

void sendJsonOk(WiFiClient& c) {
  c.print("HTTP/1.1 200 OK\r\n"
          "Content-Type: application/json\r\n"
          "Content-Length: 11\r\n"
          "Connection: close\r\n"
          "\r\n"
          "{\"ok\":true}");
  c.flush();
}

void sendJsonErr(WiFiClient& c, const char* msg) {
  char body[64];
  int blen = snprintf(body, sizeof(body), "{\"ok\":false,\"err\":\"%s\"}", msg);
  sendBuf(c, "HTTP/1.1 400 Bad Request\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(body);
  c.flush();
}

void sendStatus(WiFiClient& c) {
  const char* feat   = ledRainbowOn ? "rainbow" : (ledSirenOn ? "siren" : "none");
  const char* active = (ledRainbowOn || ledSirenOn) ? "true" : "false";
  char body[64];
  int blen = snprintf(body, sizeof(body),
    "{\"onboard_led\":{\"active\":%s,\"feature\":\"%s\"}}", active, feat);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(body);
  c.flush();
}

// ── Main SPA page ─────────────────────────────────────────────────────────────

void sendMain(WiFiClient& c) {
  c.print("HTTP/1.1 200 OK\r\n"
          "Content-Type: text/html\r\n"
          "Connection: close\r\n"
          "Cache-Control: no-cache, no-store\r\n"
          "\r\n"
          "<!DOCTYPE html><html><head>"
          "<meta charset='utf-8'>"
          "<meta name='viewport' content='width=device-width,initial-scale=1'>"
          "<title>SlyLED</title><style>");
  c.print("*{box-sizing:border-box;margin:0;padding:0}"
          "body{font-family:sans-serif;background:#111;color:#eee;min-height:100vh}"
          "#hdr{background:#1a1a2e;padding:1.2em 2em;border-bottom:2px solid #333}"
          "#hdr h1{font-size:1.8em;margin-bottom:.25em}"
          "#hdr-status{font-size:.95em;color:#aaa}"
          ".modules{padding:1.5em}"
          ".card{background:#1e1e1e;border:1px solid #333;border-radius:10px;"
          "padding:1.2em 1.5em;margin-bottom:1em;max-width:480px}"
          ".card-title{font-size:1.1em;font-weight:bold;color:#ccc;margin-bottom:.9em}"
          ".pattern-row{display:flex;align-items:center;justify-content:space-between;"
          "gap:.5em;margin-bottom:.5em}"
          ".pattern-name{font-weight:bold;font-size:1em;min-width:80px}"
          ".badge{display:inline-block;padding:.25em .8em;border-radius:10px;"
          "font-size:.85em;font-weight:bold;margin-right:.3em}"
          ".bon{background:#1a4d1a;color:#4c4}"
          ".boff{background:#4d1a1a;color:#c44}"
          ".btn{display:inline-block;padding:.5em 1.4em;border-radius:6px;"
          "border:none;cursor:pointer;font-size:.9em;font-family:inherit;"
          "font-weight:bold;margin-left:.3em}"
          ".btn:hover{opacity:.8}"
          ".btn-on{background:#2a2;color:#fff}.btn-off{background:#a22;color:#fff}"
          ".btn-nav{background:#446;color:#fff;text-decoration:none;padding:.6em 1.6em}"
          ".footer{padding:1em 2em;font-size:.72em;color:#444}");
  c.print("</style></head><body>"
          "<div id='hdr'>"
          "<h1>SlyLED</h1>"
          "<div id='hdr-status'>Connecting...</div>"
          "</div>"
          "<div class='modules'>"
          "<div class='card'>");
  sendBuf(c, "<div class='card-title'>%s</div>", CARD_TITLE);
  c.print("<div class='pattern-row'>"
          "<span class='pattern-name'>Rainbow</span>"
          "<span>"
          "<span class='badge boff' id='badge-rainbow'>OFF</span>"
          "<button class='btn btn-on' onclick='setFeature(\"rainbow\")'>Enable</button>"
          "</span></div>"
          "<div class='pattern-row'>"
          "<span class='pattern-name'>Siren</span>"
          "<span>"
          "<span class='badge boff' id='badge-siren'>OFF</span>"
          "<button class='btn btn-on' onclick='setFeature(\"siren\")'>Enable</button>"
          "</span></div>"
          "<div class='pattern-row' style='justify-content:flex-end;margin-top:.3em'>"
          "<button class='btn btn-off' onclick='setFeature(\"off\")'>Disable</button>"
          "</div></div></div>"
          "<div style='padding:0 1.5em'>"
          "<a class='btn btn-nav' href='/log'>View Log</a>"
          "</div>");
  sendBuf(c, "<div class='footer'>v%d.%d</div>", APP_MAJOR, APP_MINOR);
  c.print("<script>"
          "function applyState(d){"
          "var f=d.onboard_led.feature;"
          "var br=document.getElementById('badge-rainbow');"
          "br.textContent=f==='rainbow'?'ON':'OFF';"
          "br.className='badge '+(f==='rainbow'?'bon':'boff');"
          "var bs=document.getElementById('badge-siren');"
          "bs.textContent=f==='siren'?'ON':'OFF';"
          "bs.className='badge '+(f==='siren'?'bon':'boff');"
          "var h=document.getElementById('hdr-status');"
          "if(f==='rainbow'){h.textContent='" CARD_TITLE " - Rainbow ON';h.style.color='#4c4';}"
          "else if(f==='siren'){h.textContent='" CARD_TITLE " - Siren ON';h.style.color='#48f';}"
          "else{h.textContent='" CARD_TITLE " - OFF';h.style.color='#c44';}"
          "}"
          "function poll(){"
          "var x=new XMLHttpRequest();"
          "x.open('GET','/status',true);"
          "x.onload=function(){"
          "if(x.status===200){try{applyState(JSON.parse(x.responseText));}catch(e){}}"
          "};"
          "x.send();"
          "}"
          "function setFeature(f){"
          "var path=f==='rainbow'?'/led/on':(f==='siren'?'/led/siren/on':'/led/off');"
          "var x=new XMLHttpRequest();"
          "x.open('POST',path,true);"
          "x.onload=function(){"
          "if(x.status===200){try{if(JSON.parse(x.responseText).ok)poll();}catch(e){}}"
          "};"
          "x.send();"
          "}"
          "poll();setInterval(poll,2000);"
          "</script></body></html>");
}

// ── Log page ──────────────────────────────────────────────────────────────────

void sendLog(WiFiClient& c) {
  c.print("HTTP/1.1 200 OK\r\n"
          "Content-Type: text/html\r\n"
          "Connection: close\r\n"
          "Cache-Control: no-cache, no-store\r\n"
          "\r\n"
          "<!DOCTYPE html><html><head>"
          "<meta charset='utf-8'>"
          "<meta name='viewport' content='width=device-width,initial-scale=1'>"
          "<title>SlyLED - Log</title><style>");
  c.print("body{font-family:sans-serif;text-align:center;padding:2em;"
          "background:#111;color:#eee;margin:0}"
          "h1{font-size:2em;margin-bottom:.1em}"
          "h2{font-weight:normal;color:#aaa;margin-top:0}"
          ".btn-nav{display:inline-block;padding:.6em 1.6em;border-radius:6px;"
          "background:#446;color:#fff;text-decoration:none;font-weight:bold}"
          ".btn-nav:hover{opacity:.8}"
          "table{margin:1.5em auto;border-collapse:collapse}"
          "th,td{padding:.5em 1.2em;border:1px solid #444;text-align:left}"
          "th{background:#222}tr:nth-child(even){background:#1a1a1a}"
          "</style></head><body>"
          "<h1>SlyLED</h1><h2>Event Log</h2>");
  if (logCount == 0) {
    c.print("<p style='color:#888'>No events recorded yet.</p>");
  } else {
    c.print("<table><tr><th>#</th><th>Timestamp</th><th>Feature</th>"
            "<th>State</th><th>Source</th><th>IP</th></tr>");
    uint8_t startIdx = (logNext - logCount + MAX_LOG * 2) % MAX_LOG;
    for (int8_t i = logCount - 1; i >= 0; i--) {
      uint8_t idx = (startIdx + i) % MAX_LOG;
      char ts[40];
      formatTime(logBuf[idx].epoch, ts, sizeof(ts));
      const char* featLabel;
      const char* color;
      const char* label;
      if (logBuf[idx].feature == FEAT_RAINBOW) {
        featLabel = "Rainbow"; color = "#4c4"; label = "ON";
      } else if (logBuf[idx].feature == FEAT_SIREN) {
        featLabel = "Siren";   color = "#48f"; label = "ON";
      } else {
        featLabel = "-";       color = "#c44"; label = "OFF";
      }
      const char* source = logBuf[idx].source == SRC_BOOT ? "Boot" : "Web";
      char ipStr[16];
      if (logBuf[idx].source == SRC_BOOT) {
        ipStr[0] = '-'; ipStr[1] = '\0';
      } else {
        snprintf(ipStr, sizeof(ipStr), "%u.%u.%u.%u",
                 logBuf[idx].ip[0], logBuf[idx].ip[1],
                 logBuf[idx].ip[2], logBuf[idx].ip[3]);
      }
      sendBuf(c, "<tr><td>%d</td><td>%s</td><td>%s</td>"
                 "<td style='color:%s'><strong>%s</strong></td>"
                 "<td>%s</td><td>%s</td></tr>",
              logCount - i, ts, featLabel, color, label, source, ipStr);
    }
    c.print("</table>");
    c.flush();
  }
  c.print("<br><a class='btn-nav' href='/'>Back</a></body></html>");
}

// ── Forward declarations ──────────────────────────────────────────────────────

#ifdef BOARD_D1MINI
void updateLED();
#endif

#ifdef BOARD_FASTLED
void sendPong(IPAddress dest);
void sendStatusResp(IPAddress dest);
#endif

// ── Web request handler ───────────────────────────────────────────────────────

void serveClient(WiFiClient& client, unsigned int waitMs) {
  unsigned long t = millis();
  while (!client.available() && millis() - t < waitMs) {
#ifdef BOARD_D1MINI
    updateLED();
#endif
    yield();
  }

  IPAddress remoteIP = client.remoteIP();
  uint8_t ip0 = remoteIP[0], ip1 = remoteIP[1], ip2 = remoteIP[2], ip3 = remoteIP[3];

  // Read request line
  char req[128] = {};
  client.readBytesUntil('\n', req, sizeof(req) - 1);

  // Read headers; capture Content-Length for POST routes that need a body
  int contentLen = 0;
  {
    char hdr[80];
    while (true) {
      int n = client.readBytesUntil('\n', hdr, sizeof(hdr) - 1);
      if (n <= 1) break;           // blank line = end of headers
      hdr[n] = '\0';
      if (strncmp(hdr, "Content-Length:", 15) == 0) {
        contentLen = atoi(hdr + 15);
      }
    }
  }

  // Detect method: req starts with "GET " or "POST "
  bool isPost = (req[0] == 'P');

  // ── Route dispatch ────────────────────────────────────────────────────────

  if (strstr(req, " /status ")) {
    sendStatus(client);

  } else if (strstr(req, " /led/siren/on ")) {
    ledRainbowOn = false;
    ledSirenOn   = true;
    addLog(FEAT_SIREN, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);

  } else if (strstr(req, " /led/on ")) {
    ledSirenOn   = false;
    ledRainbowOn = true;
    addLog(FEAT_RAINBOW, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);

  } else if (strstr(req, " /led/off ")) {
    ledRainbowOn = false;
    ledSirenOn   = false;
    addLog(FEAT_NONE, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);

  } else if (strstr(req, " /log ")) {
    sendLog(client);

  } else if (strstr(req, " /favicon.ico ")) {
    client.print("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    client.flush();

#ifdef BOARD_GIGA
  } else if (strstr(req, " /api/children ")) {
    if (isPost) {
      // Read body: {"ip":"x.x.x.x"}
      char body[32] = {};
      if (contentLen > 0 && contentLen < (int)sizeof(body)) {
        client.readBytes(body, contentLen);
      }
      // Extract IP from body
      char* p = strstr(body, "\"ip\":");
      if (p) {
        p += 5;
        while (*p == ' ' || *p == '"') p++;
        int a = 0, b = 0, cc = 0, d = 0;
        if (sscanf(p, "%d.%d.%d.%d", &a, &b, &cc, &d) == 4
            && a >= 0 && a <= 255 && b >= 0 && b <= 255
            && cc >= 0 && cc <= 255 && d >= 0 && d <= 255) {
          // Send a directed ping; child will respond with CMD_PONG
          IPAddress dest(a, b, cc, d);
          UdpHeader hdr;
          hdr.magic   = UDP_MAGIC;
          hdr.version = UDP_VERSION;
          hdr.cmd     = CMD_PING;
          hdr.epoch   = (uint32_t)currentEpoch();
          memcpy(udpBuf, &hdr, sizeof(hdr));
          cmdUDP.beginPacket(dest, UDP_PORT);
          cmdUDP.write(udpBuf, sizeof(hdr));
          cmdUDP.endPacket();
          sendJsonOk(client);
        } else {
          sendJsonErr(client, "bad-ip");
        }
      } else {
        sendJsonErr(client, "no-ip");
      }
    } else {
      sendApiChildren(client);
    }
#endif  // BOARD_GIGA

  } else {
    sendMain(client);
  }

  client.flush();
#ifdef BOARD_D1MINI
  { unsigned long d = millis(); while (millis() - d < 200) { updateLED(); yield(); } }
#else
  delay(5);
#endif
  client.stop();
}

void handleClient() {
  WiFiClient client = server.available();
  if (!client) return;

#ifdef BOARD_D1MINI
  serveClient(client, 100);
  { unsigned long d = millis(); while (millis() - d < 20) { updateLED(); yield(); } }
  while ((client = server.available())) {
    serveClient(client, 50);
  }
#else
  serveClient(client, 500);
  delay(20);
  while ((client = server.available())) {
    serveClient(client, 100);
  }
#endif
}

// ── Parent UDP functions (Giga only) ─────────────────────────────────────────

#ifdef BOARD_GIGA

// Broadcast CMD_PING to discover children on the LAN.
void sendPing(IPAddress dest) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_PING;
  hdr.epoch   = (uint32_t)currentEpoch();
  memcpy(udpBuf, &hdr, sizeof(hdr));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr));
  cmdUDP.endPacket();
}

// Add or update a child in the registry from a received PongPayload.
void registerChild(IPAddress ip, const PongPayload* pong) {
  // Search for existing child with the same hostname (hostname is stable across reboots)
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (children[i].inUse
        && strncmp(children[i].hostname, pong->hostname, HOSTNAME_LEN) == 0) {
      for (uint8_t j = 0; j < 4; j++) children[i].ip[j] = ip[j];
      strncpy(children[i].name,        pong->altName,     CHILD_NAME_LEN - 1);
      strncpy(children[i].description, pong->description, CHILD_DESC_LEN - 1);
      uint8_t sc = (pong->stringCount < MAX_STR_PER_CHILD)
                 ? pong->stringCount : MAX_STR_PER_CHILD;
      children[i].stringCount = sc;
      for (uint8_t j = 0; j < sc; j++) {
        children[i].strings[j].ledCount = pong->strings[j].ledCount;
        children[i].strings[j].lengthMm = pong->strings[j].lengthMm;
        children[i].strings[j].ledType  = pong->strings[j].ledType;
        children[i].strings[j].cableDir = pong->strings[j].cableDir;
        children[i].strings[j].cableMm  = pong->strings[j].cableMm;
        children[i].strings[j].stripDir = pong->strings[j].stripDir;
      }
      children[i].status        = CHILD_ONLINE;
      children[i].lastSeenEpoch = currentEpoch();
      children[i].configFetched = true;
      if (Serial) { Serial.print(F("Child updated: ")); Serial.println(pong->hostname); }
      return;
    }
  }
  // Find an empty slot
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (!children[i].inUse) {
      children[i].inUse = true;
      for (uint8_t j = 0; j < 4; j++) children[i].ip[j] = ip[j];
      strncpy(children[i].hostname,    pong->hostname,    HOSTNAME_LEN   - 1);
      children[i].hostname[HOSTNAME_LEN - 1] = '\0';
      strncpy(children[i].name,        pong->altName,     CHILD_NAME_LEN - 1);
      children[i].name[CHILD_NAME_LEN - 1] = '\0';
      strncpy(children[i].description, pong->description, CHILD_DESC_LEN - 1);
      children[i].description[CHILD_DESC_LEN - 1] = '\0';
      children[i].xMm = 0; children[i].yMm = 0; children[i].zMm = 0;
      uint8_t sc = (pong->stringCount < MAX_STR_PER_CHILD)
                 ? pong->stringCount : MAX_STR_PER_CHILD;
      children[i].stringCount = sc;
      for (uint8_t j = 0; j < sc; j++) {
        children[i].strings[j].ledCount = pong->strings[j].ledCount;
        children[i].strings[j].lengthMm = pong->strings[j].lengthMm;
        children[i].strings[j].ledType  = pong->strings[j].ledType;
        children[i].strings[j].cableDir = pong->strings[j].cableDir;
        children[i].strings[j].cableMm  = pong->strings[j].cableMm;
        children[i].strings[j].stripDir = pong->strings[j].stripDir;
      }
      children[i].status        = CHILD_ONLINE;
      children[i].lastSeenEpoch = currentEpoch();
      children[i].configFetched = true;
      if (Serial) { Serial.print(F("Child added: ")); Serial.println(pong->hostname); }
      return;
    }
  }
  if (Serial) Serial.println(F("Child registry full."));
}

// Serve GET /api/children — JSON array of all registered children.
// Response format per child:
//   {"id":N,"hostname":"SLYC-XXXX","name":"...","desc":"...","ip":"x.x.x.x",
//    "status":N,"sc":N,"seen":epoch}
void sendApiChildren(WiFiClient& c) {
  static char jsonBuf[1400];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  *p++ = '[';
  bool first = true;
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (!children[i].inUse) continue;
    if (!first) { *p++ = ','; }
    first = false;
    char ipStr[16];
    snprintf(ipStr, sizeof(ipStr), "%u.%u.%u.%u",
             children[i].ip[0], children[i].ip[1],
             children[i].ip[2], children[i].ip[3]);
    p += snprintf(p, end - p,
      "{\"id\":%u,\"hostname\":\"%s\",\"name\":\"%s\","
      "\"desc\":\"%s\",\"ip\":\"%s\",\"status\":%u,"
      "\"sc\":%u,\"seen\":%lu}",
      (unsigned)i,
      children[i].hostname, children[i].name, children[i].description,
      ipStr, (unsigned)children[i].status,
      (unsigned)children[i].stringCount,
      (unsigned long)children[i].lastSeenEpoch);
  }
  *p++ = ']';
  *p   = '\0';
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

#endif  // BOARD_GIGA

// ── Child UDP functions (ESP32 / D1 Mini only) ────────────────────────────────

#ifdef BOARD_FASTLED

// Populate childCfg with defaults and generate hostname from MAC.
// Must be called after WiFi.begin() so macAddress() is available.
void initChildConfig() {
  memset(&childCfg, 0, sizeof(childCfg));
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(childCfg.hostname, HOSTNAME_LEN, "SLYC-%02X%02X", mac[4], mac[5]);

  // Default: 1 string, NUM_LEDS LEDs, WS2812B, 500 mm strip, all East
  childCfg.stringCount         = 1;
  childCfg.strings[0].ledCount = NUM_LEDS;
  childCfg.strings[0].lengthMm = 500;
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].cableDir = DIR_E;
  childCfg.strings[0].cableMm  = 0;
  childCfg.strings[0].stripDir = DIR_E;

  if (Serial) { Serial.print(F("Child hostname: ")); Serial.println(childCfg.hostname); }
}

// Send CMD_PONG to dest with our full self-config.
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
    pong.strings[j].cableDir = childCfg.strings[j].cableDir;
    pong.strings[j].cableMm  = childCfg.strings[j].cableMm;
    pong.strings[j].stripDir = childCfg.strings[j].stripDir;
  }

  memcpy(udpBuf,                &hdr,  sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr),  &pong, sizeof(pong));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(pong));
  cmdUDP.endPacket();
}

// Send CMD_STATUS_RESP to dest with current LED state.
void sendStatusResp(IPAddress dest) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_STATUS_RESP;
  hdr.epoch   = (uint32_t)currentEpoch();

  StatusRespPayload resp;
  resp.activeAction = ledRainbowOn ? 1 : (ledSirenOn ? 2 : 0);
  resp.runnerActive = 0;
  resp.currentStep  = 0;
  int32_t rssi = WiFi.RSSI();
  resp.wifiRssi = (rssi < 0) ? (uint8_t)(-rssi) : 0;
  resp.uptimeS  = (uint32_t)(millis() / 1000);

  memcpy(udpBuf,                &hdr,  sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr),  &resp, sizeof(resp));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(resp));
  cmdUDP.endPacket();
}

#endif  // BOARD_FASTLED

// ── Shared UDP receive handler (all boards) ───────────────────────────────────

// Dispatch an incoming UDP packet based on its command byte.
// Called by pollUDP() after the header has been validated.
void handleUdpPacket(uint8_t cmd, IPAddress sender, uint8_t* payload, int plen) {
#ifdef BOARD_GIGA
  if (cmd == CMD_PONG && plen >= (int)sizeof(PongPayload)) {
    PongPayload pong;
    memcpy(&pong, payload, sizeof(pong));
    registerChild(sender, &pong);
  }
  // CMD_STATUS_RESP will be used in Phase 2b for /api/children/:id/status

#else  // children
  if (cmd == CMD_PING) {
    sendPong(sender);
  } else if (cmd == CMD_STATUS_REQ) {
    sendStatusResp(sender);
  }
  (void)plen;
#endif
}

// Check cmdUDP for incoming packets; validate header; dispatch.
void pollUDP() {
  int plen = cmdUDP.parsePacket();
  if (plen <= 0 || plen > (int)sizeof(udpBuf)) return;

  IPAddress sender = cmdUDP.remoteIP();
  int n = cmdUDP.read(udpBuf, sizeof(udpBuf));
  if (n < (int)sizeof(UdpHeader)) return;

  UdpHeader hdr;
  memcpy(&hdr, udpBuf, sizeof(hdr));
  if (hdr.magic != UDP_MAGIC || hdr.version != UDP_VERSION) return;

  handleUdpPacket(hdr.cmd, sender, udpBuf + sizeof(hdr), n - (int)sizeof(hdr));
}

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  Serial.print("Connecting to "); Serial.println(SECRET_SSID);
#ifdef BOARD_D1MINI
  WiFi.mode(WIFI_STA);
  WiFi.hostname(HOSTNAME);
#else
  WiFi.setHostname(HOSTNAME);
#endif
  WiFi.begin(SECRET_SSID, SECRET_PASS);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t > 20000) { Serial.println("\r\nWiFi timeout."); return; }
    delay(500);
    Serial.print('.');
  }
  Serial.println();
  Serial.print("Connected. IP: "); Serial.println(WiFi.localIP());
  server.begin();
  syncNTP();

  // Start persistent UDP command channel
  cmdUDP.begin(UDP_PORT);
  if (Serial) Serial.println(F("UDP command channel open on port 4210."));

#ifdef BOARD_FASTLED
  // Generate child hostname from MAC now that WiFi is up
  initChildConfig();
  // Override the WiFi hostname to the SLYC-XXXX name
#ifdef BOARD_D1MINI
  WiFi.hostname(childCfg.hostname);
#else
  WiFi.setHostname(childCfg.hostname);
#endif
#endif
}

// ── Status print (serial) ─────────────────────────────────────────────────────

void printStatus() {
  static unsigned long last = 0;
  if (millis() - last >= 3000) {
    last = millis();
    Serial.print("IP: ");     Serial.print(WiFi.localIP());
    Serial.print("  WiFi: "); Serial.print(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
    const char* feat = ledRainbowOn ? "Rainbow" : (ledSirenOn ? "Siren" : "OFF");
    Serial.print("  LED: ");  Serial.println(feat);
  }
}

// ── LED implementation — Arduino Giga R1 WiFi ─────────────────────────────────

#ifdef BOARD_GIGA

void hueToRGB(uint8_t hue, uint8_t& r, uint8_t& g, uint8_t& b) {
  uint8_t sector = hue / 43;
  uint8_t frac   = (hue % 43) * 6;
  switch (sector) {
    case 0: r=255;      g=frac;     b=0;        break;
    case 1: r=255-frac; g=255;      b=0;        break;
    case 2: r=0;        g=255;      b=frac;     break;
    case 3: r=0;        g=255-frac; b=255;      break;
    case 4: r=frac;     g=0;        b=255;      break;
    default: r=255;     g=0;        b=255-frac; break;
  }
}

void pwmCycle(uint8_t r, uint8_t g, uint8_t b) {
  for (uint16_t step = 0; step < PWM_STEPS; step++) {
    digitalWrite(LEDR, r > step ? LOW : HIGH);
    digitalWrite(LEDG, g > step ? LOW : HIGH);
    digitalWrite(LEDB, b > step ? LOW : HIGH);
    delayMicroseconds(STEP_US);
  }
}

void setRGBFor(uint8_t r, uint8_t g, uint8_t b) {
  unsigned long end = millis() + DISPLAY_MS;
  while (millis() < end) pwmCycle(r, g, b);
}

void ledTask() {
  uint8_t       hue        = 0;
  uint8_t       sirenPhase = 0;
  unsigned long sirenStart = 0;
  bool          prevSirenOn = false;

  while (true) {
    bool siren   = ledSirenOn;
    bool rainbow = ledRainbowOn;

    if (siren) {
      if (!prevSirenOn) { sirenPhase = 0; sirenStart = millis(); }
      prevSirenOn = true;
      unsigned long now = millis();
      if (now - sirenStart >= (unsigned long)SIREN_HALF_MS) {
        sirenPhase ^= 1;
        sirenStart  = now;
      }
      if (sirenPhase == 0) pwmCycle(255, 0, 0);
      else                  pwmCycle(0, 0, 255);

    } else if (rainbow) {
      prevSirenOn = false;
      uint8_t r, g, b;
      hueToRGB(hue, r, g, b);
      setRGBFor(r, g, b);
      hue += HUE_STEP;

    } else {
      prevSirenOn = false;
      digitalWrite(LEDR, HIGH);
      digitalWrite(LEDG, HIGH);
      digitalWrite(LEDB, HIGH);
      delay(5);
    }
  }
}

#endif  // BOARD_GIGA

// ── LED implementation — ESP32 (FreeRTOS task, Core 0) ───────────────────────

#ifdef BOARD_ESP32

void ledTask(void* parameter) {
  uint8_t       hue        = 0;
  uint8_t       sirenPhase = 0;
  unsigned long sirenStart = 0;
  bool          prevSirenOn = false;

  while (true) {
    bool siren   = ledSirenOn;
    bool rainbow = ledRainbowOn;

    if (siren) {
      if (!prevSirenOn) { sirenPhase = 0; sirenStart = millis(); }
      prevSirenOn = true;
      unsigned long now = millis();
      if (now - sirenStart >= (unsigned long)SIREN_HALF_MS) {
        sirenPhase ^= 1;
        sirenStart  = now;
      }
      for (int i = 0; i < NUM_LEDS; i++)
        leds[i] = ((i % 2) == sirenPhase) ? CRGB::Red : CRGB::Blue;
      FastLED.show();
      delay(5);

    } else if (rainbow) {
      prevSirenOn = false;
      fill_rainbow(leds, NUM_LEDS, hue, RAINBOW_DELTA);
      FastLED.show();
      hue++;
      delay(RAINBOW_DELAY);

    } else {
      prevSirenOn = false;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
      delay(10);
    }
  }
}

#endif  // BOARD_ESP32

// ── LED implementation — D1 Mini (single-threaded, non-blocking) ──────────────

#ifdef BOARD_D1MINI

void updateLED() {
  static uint8_t       hue        = 0;
  static uint8_t       sirenPhase = 0;
  static unsigned long sirenStart = 0;
  static unsigned long lastFrame  = 0;
  static bool          prevSirenOn = false;

  bool siren   = ledSirenOn;
  bool rainbow = ledRainbowOn;

  if (siren) {
    if (!prevSirenOn) { sirenPhase = 0; sirenStart = millis(); }
    prevSirenOn = true;
    unsigned long now = millis();
    if (now - sirenStart >= (unsigned long)SIREN_HALF_MS) {
      sirenPhase ^= 1;
      sirenStart  = now;
    }
    for (int i = 0; i < NUM_LEDS; i++)
      leds[i] = ((i % 2) == sirenPhase) ? CRGB::Red : CRGB::Blue;
    FastLED.show();

  } else if (rainbow) {
    prevSirenOn = false;
    unsigned long now = millis();
    if (now - lastFrame >= (unsigned long)RAINBOW_DELAY) {
      lastFrame = now;
      fill_rainbow(leds, NUM_LEDS, hue, RAINBOW_DELTA);
      FastLED.show();
      hue++;
    }

  } else {
    if (prevSirenOn || hue != 0) {
      prevSirenOn = false;
      hue = 0;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
    }
  }
}

#endif  // BOARD_D1MINI

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== BOOT ===");

#ifdef BOARD_GIGA
  pinMode(LEDR, OUTPUT); digitalWrite(LEDR, HIGH);
  pinMode(LEDG, OUTPUT); digitalWrite(LEDG, HIGH);
  pinMode(LEDB, OUTPUT); digitalWrite(LEDB, HIGH);
  ledThread.start(mbed::callback(ledTask));
  memset(children, 0, sizeof(children));  // clear child registry

#elif defined(BOARD_ESP32)
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
  xTaskCreatePinnedToCore(ledTask, "LED", 4096, NULL, 1, NULL, 0);

#else  // D1 Mini
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
#endif

  connectWiFi();
  addLog(FEAT_RAINBOW, SRC_BOOT, 0, 0, 0, 0);

#ifdef BOARD_GIGA
  // Initial discovery broadcast — children already on the network will respond
  sendPing(IPAddress(255, 255, 255, 255));
#endif
}

void loop() {
  printStatus();
  pollUDP();

#ifdef BOARD_GIGA
  // Re-broadcast discovery ping every 30 s to catch children that rebooted
  static unsigned long lastPing = 0;
  if (millis() - lastPing >= 30000UL) {
    lastPing = millis();
    sendPing(IPAddress(255, 255, 255, 255));
  }
  handleClient();
  delay(10);

#elif defined(BOARD_D1MINI)
  updateLED();
  handleClient();
  yield();

#else  // ESP32
  handleClient();
  delay(10);
#endif
}
