/*
 * SlyLED — multi-board sketch
 * Phase 2d: Remove rainbow/siren; add Runner data structures and RUNTIME tab
 *
 * Parent (Giga R1 WiFi): no LEDs, serves multi-tab SPA, manages children
 * Children (ESP32 / D1 Mini): execute UDP actions; idle = LEDs off
 *
 * HTTP routes (all boards):
 *   GET  /              — SPA main page
 *   GET  /status        — JSON status
 *   GET  /favicon.ico   — 404
 *
 * HTTP routes (Giga parent only):
 *   GET  /api/children              — child list
 *   POST /api/children              — add child by IP
 *   GET  /api/children/export       — download children JSON
 *   DELETE /api/children/:id        — remove child
 *   POST   /api/children/:id/refresh — re-ping child
 *   GET  /api/layout                — canvas positions
 *   POST /api/layout                — update canvas positions
 *   GET  /api/settings              — app settings
 *   POST /api/settings              — update app settings
 *   POST /api/action                — send immediate action to child(ren)
 *   POST /api/action/stop           — stop immediate action
 *   GET  /api/runners               — runner list
 *   POST /api/runners               — create runner
 *   GET  /api/runners/:id           — full runner with steps
 *   PUT  /api/runners/:id           — update runner steps
 *   DELETE /api/runners/:id         — delete runner
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

// ── LED hardware constants (FastLED children only) ────────────────────────────

#ifdef BOARD_FASTLED
  #define DATA_PIN      2
  #define NUM_LEDS      8
  #define LED_TYPE      WS2812B
  #define COLOR_ORDER   GRB
  constexpr uint8_t LED_BRIGHTNESS = 200;
  CRGB leds[NUM_LEDS];
#endif

// ── Phase 2 UDP protocol constants ───────────────────────────────────────────

constexpr uint16_t UDP_PORT    = 4210;
constexpr uint16_t UDP_MAGIC   = 0x534C;
constexpr uint8_t  UDP_VERSION = 2;

constexpr uint8_t HOSTNAME_LEN      = 10;   // "SLYC-XXXX\0"
constexpr uint8_t CHILD_NAME_LEN    = 16;
constexpr uint8_t CHILD_DESC_LEN    = 32;
constexpr uint8_t MAX_STR_PER_CHILD = 4;

constexpr uint8_t CMD_PING        = 0x01;
constexpr uint8_t CMD_PONG        = 0x02;
constexpr uint8_t CMD_ACTION      = 0x10;
constexpr uint8_t CMD_ACTION_STOP = 0x11;
constexpr uint8_t CMD_LOAD_STEP   = 0x20;  // parent→child: load one runner step
constexpr uint8_t CMD_LOAD_ACK    = 0x21;  // child→parent: step received
constexpr uint8_t CMD_RUNNER_GO   = 0x30;  // parent→child: start runner at epoch
constexpr uint8_t CMD_RUNNER_STOP = 0x31;  // parent→child: stop runner
constexpr uint8_t CMD_STATUS_REQ  = 0x40;
constexpr uint8_t CMD_STATUS_RESP = 0x41;

// Action type codes (uint8_t to avoid Mbed prototype-generator issues with enums)
constexpr uint8_t ACT_OFF   = 0;
constexpr uint8_t ACT_SOLID = 1;
constexpr uint8_t ACT_FLASH = 2;
constexpr uint8_t ACT_WIPE  = 3;

// Wipe direction codes
constexpr uint8_t DIR_E = 0;
constexpr uint8_t DIR_N = 1;
constexpr uint8_t DIR_W = 2;
constexpr uint8_t DIR_S = 3;

// ── UDP packet structures ─────────────────────────────────────────────────────

struct __attribute__((packed)) UdpHeader {
  uint16_t magic;
  uint8_t  version;
  uint8_t  cmd;
  uint32_t epoch;
};  // 8 bytes

struct __attribute__((packed)) PongString {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};  // 9 bytes

struct __attribute__((packed)) PongPayload {
  char       hostname[HOSTNAME_LEN];
  char       altName[CHILD_NAME_LEN];
  char       description[CHILD_DESC_LEN];
  uint8_t    stringCount;
  PongString strings[MAX_STR_PER_CHILD];
};  // 95 bytes

struct __attribute__((packed)) StatusRespPayload {
  uint8_t  activeAction;
  uint8_t  runnerActive;
  uint8_t  currentStep;
  uint8_t  wifiRssi;
  uint32_t uptimeS;
};  // 8 bytes

struct __attribute__((packed)) ActionPayload {
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t onMs;
  uint16_t offMs;
  uint8_t  wipeDir;
  uint8_t  wipeSpeedPct;
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 18 bytes

struct __attribute__((packed)) LoadStepPayload {
  uint8_t  stepIndex;
  uint8_t  totalSteps;
  uint8_t  actionType;
  uint8_t  r, g, b;
  uint16_t onMs, offMs;
  uint8_t  wipeDir;
  uint8_t  wipeSpeedPct;
  uint16_t durationS;
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 22 bytes; packet = 8 + 22 = 30 bytes

// ── WiFi, server, UDP ─────────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";
WiFiServer server(80);
WiFiUDP    ntpUDP;
WiFiUDP    cmdUDP;
uint8_t    udpBuf[128];

// ── Parent data structures (Giga only) ───────────────────────────────────────

#ifdef BOARD_GIGA

constexpr uint8_t MAX_CHILDREN  = 8;
constexpr uint8_t CHILD_UNKNOWN = 0;
constexpr uint8_t CHILD_ONLINE  = 1;
constexpr uint8_t CHILD_OFFLINE = 2;

struct StringInfo {
  uint16_t ledCount;
  uint16_t lengthMm;
  uint8_t  ledType;
  uint8_t  cableDir;
  uint16_t cableMm;
  uint8_t  stripDir;
};

struct ChildNode {
  uint8_t    ip[4];
  char       hostname[HOSTNAME_LEN];
  char       name[CHILD_NAME_LEN];
  char       description[CHILD_DESC_LEN];
  int16_t    xMm, yMm, zMm;
  uint8_t    stringCount;
  StringInfo strings[MAX_STR_PER_CHILD];
  uint8_t    status;
  uint32_t   lastSeenEpoch;
  bool       configFetched;
  bool       inUse;
};

struct AppSettings {
  uint8_t  units;
  uint8_t  darkMode;
  uint16_t canvasWidthMm;
  uint16_t canvasHeightMm;
  char     parentName[16];
  uint8_t  activeRunner;
  bool     runnerRunning;
};

// ── Runner data structures (Phase 2d) ────────────────────────────────────────

constexpr uint8_t MAX_RUNNERS     = 4;
constexpr uint8_t MAX_STEPS       = 16;
constexpr uint8_t RUNNER_NAME_LEN = 16;

struct RunnerAction {
  uint8_t  type;
  uint8_t  r, g, b;
  uint16_t onMs, offMs;
  uint8_t  wipeDir, wipeSpeedPct;
};  // 10 bytes

struct AreaRect {
  uint16_t x0, y0, x1, y1;  // 0–10000 (units of 0.01%)
};  // 8 bytes

struct RunnerStep {
  RunnerAction action;
  AreaRect     area;
  uint16_t     durationS;
};  // 20 bytes

struct ChildStepPayload {
  uint8_t ledStart[MAX_STR_PER_CHILD];
  uint8_t ledEnd[MAX_STR_PER_CHILD];
};  // 8 bytes

struct Runner {
  char             name[RUNNER_NAME_LEN];
  uint8_t          stepCount;
  bool             computed;
  bool             inUse;
  RunnerStep       steps[MAX_STEPS];                    // 320 bytes
  ChildStepPayload payload[MAX_STEPS][MAX_CHILDREN];    // 1024 bytes
};  // ~1363 bytes each; 4 runners = ~5452 bytes

ChildNode children[MAX_CHILDREN];
AppSettings settings;
Runner runners[MAX_RUNNERS];

#endif  // BOARD_GIGA

// ── Child self-config data (ESP32 / D1 Mini only) ────────────────────────────

#ifdef BOARD_FASTLED

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

// Volatile action state — written by UDP handler (main thread), read by LED task
volatile uint8_t  childActType  = 0;
volatile uint8_t  childActR     = 0;
volatile uint8_t  childActG     = 0;
volatile uint8_t  childActB     = 0;
volatile uint16_t childActOnMs  = 500;
volatile uint16_t childActOffMs = 500;
volatile uint8_t  childActWDir  = 0;
volatile uint8_t  childActWSpd  = 50;
volatile uint8_t  childActSeq   = 0;
volatile uint8_t  childActSt[MAX_STR_PER_CHILD];
volatile uint8_t  childActEn[MAX_STR_PER_CHILD];

// Runner execution state — written by UDP handler, read by LED task
constexpr uint8_t MAX_CHILD_STEPS = 16;

struct ChildRunnerStep {
  uint8_t  actionType, r, g, b;
  uint16_t onMs, offMs;
  uint8_t  wipeDir, wipeSpeedPct;
  uint16_t durationS;
  uint8_t  ledStart[MAX_STR_PER_CHILD];
  uint8_t  ledEnd[MAX_STR_PER_CHILD];
};  // 20 bytes × 16 = 320 bytes

ChildRunnerStep   childRunner[MAX_CHILD_STEPS];
volatile uint8_t  childStepCount    = 0;
volatile uint32_t childRunnerStart  = 0;   // epoch when runner should begin
volatile bool     childRunnerArmed  = false;
volatile bool     childRunnerActive = false;

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
  char body[96];
  int blen;
#ifdef BOARD_GIGA
  blen = snprintf(body, sizeof(body), "{\"role\":\"parent\",\"hostname\":\"slyled\"}");
#else
  blen = snprintf(body, sizeof(body),
    "{\"role\":\"child\",\"hostname\":\"%s\",\"action\":%u}",
    childCfg.hostname, (unsigned)childActType);
#endif
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(body);
  c.flush();
}

// ── JSON parse helpers ────────────────────────────────────────────────────────

int jsonGetInt(const char* json, const char* key, int defVal) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* p = strstr(json, needle);
  if (!p) return defVal;
  p += strlen(needle);
  while (*p == ' ') p++;
  if (*p == '-' || (*p >= '0' && *p <= '9')) return atoi(p);
  return defVal;
}

void jsonGetStr(const char* json, const char* key, char* out, uint8_t len) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":\"", key);
  const char* p = strstr(json, needle);
  if (!p) { out[0] = '\0'; return; }
  p += strlen(needle);
  uint8_t i = 0;
  while (*p && *p != '"' && i < len - 1) out[i++] = *p++;
  out[i] = '\0';
}

// ── Forward declarations ──────────────────────────────────────────────────────

#ifdef BOARD_D1MINI
void updateLED();
#endif

#ifdef BOARD_FASTLED
void sendPong(IPAddress dest);
void sendStatusResp(IPAddress dest);
bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t flashPh, unsigned long stepMs);
#endif

#ifdef BOARD_GIGA
void sendParentSPA(WiFiClient& c);
void sendApiChildren(WiFiClient& c);
void sendApiLayout(WiFiClient& c);
void handlePostLayout(WiFiClient& c, int contentLen);
void sendApiSettings(WiFiClient& c);
void handlePostSettings(WiFiClient& c, int contentLen);
void sendApiChildrenExport(WiFiClient& c);
void handleChildIdRoute(WiFiClient& c, const char* req, bool isPost, bool isDel, int contentLen);
void handleApiChildrenImport(WiFiClient& c, int contentLen);
void sendPing(IPAddress dest);
void handleApiAction(WiFiClient& c, int contentLen);
void handleApiActionStop(WiFiClient& c, int contentLen);
void sendApiRunners(WiFiClient& c);
void sendApiRunner(WiFiClient& c, uint8_t id);
void handlePostRunners(WiFiClient& c, int contentLen);
void handleRunnerIdRoute(WiFiClient& c, const char* req, bool isGet, bool isPut, bool isDel, int contentLen);
void computeRunner(uint8_t id);
void syncRunner(uint8_t id);
void startRunner(uint8_t id);
void stopAllRunners();
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
  (void)ip0; (void)ip1; (void)ip2; (void)ip3;

  char req[128] = {};
  client.readBytesUntil('\n', req, sizeof(req) - 1);

  int contentLen = 0;
  {
    char hdr[80];
    while (true) {
      int n = client.readBytesUntil('\n', hdr, sizeof(hdr) - 1);
      if (n <= 1) break;
      hdr[n] = '\0';
      if (strncmp(hdr, "Content-Length:", 15) == 0) {
        contentLen = atoi(hdr + 15);
      }
    }
  }

  bool isPost = strncmp(req, "POST", 4) == 0;
  bool isPut  = strncmp(req, "PUT ", 4) == 0;
  bool isDel  = strncmp(req, "DELE", 4) == 0;

  // ── Route dispatch ────────────────────────────────────────────────────────

  if (strstr(req, " /status ")) {
    sendStatus(client);

  } else if (strstr(req, " /favicon.ico ")) {
    client.print("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    client.flush();

#ifdef BOARD_GIGA
  } else if (isPost && strstr(req, " /api/children/import")) {
    handleApiChildrenImport(client, contentLen);

  } else if (strstr(req, " /api/children/export")) {
    sendApiChildrenExport(client);

  } else if (strstr(req, " /api/children/")) {
    handleChildIdRoute(client, req, isPost, isDel, contentLen);

  } else if (strstr(req, " /api/children ")) {
    if (isPost) {
      char body[32] = {};
      if (contentLen > 0 && contentLen < (int)sizeof(body))
        client.readBytes(body, contentLen);
      char* p = strstr(body, "\"ip\":");
      if (p) {
        p += 5;
        while (*p == ' ' || *p == '"') p++;
        int a = 0, b = 0, cc = 0, d = 0;
        if (sscanf(p, "%d.%d.%d.%d", &a, &b, &cc, &d) == 4
            && a >= 0 && a <= 255 && b >= 0 && b <= 255
            && cc >= 0 && cc <= 255 && d >= 0 && d <= 255) {
          IPAddress dest(a, b, cc, d);
          sendPing(dest);
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

  } else if (strstr(req, " /api/layout ")) {
    if (isPost) handlePostLayout(client, contentLen);
    else        sendApiLayout(client);

  } else if (strstr(req, " /api/settings ")) {
    if (isPost) handlePostSettings(client, contentLen);
    else        sendApiSettings(client);

  } else if (strstr(req, " /api/action/stop ")) {
    handleApiActionStop(client, contentLen);

  } else if (strstr(req, " /api/action ")) {
    handleApiAction(client, contentLen);

  } else if (isPost && strstr(req, " /api/runners/stop ")) {
    stopAllRunners();
    sendJsonOk(client);

  } else if (strstr(req, " /api/runners/")) {
    handleRunnerIdRoute(client, req, !isPost && !isPut && !isDel, isPut, isDel, contentLen);

  } else if (strstr(req, " /api/runners ")) {
    if (isPost) handlePostRunners(client, contentLen);
    else        sendApiRunners(client);

#endif  // BOARD_GIGA

  } else {
    sendMain(client);
  }

  client.flush();
#ifdef BOARD_D1MINI
  { unsigned long d2 = millis(); while (millis() - d2 < 200) { updateLED(); yield(); } }
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
  { unsigned long d2 = millis(); while (millis() - d2 < 20) { updateLED(); yield(); } }
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

// ── Parent SPA (Giga only) ────────────────────────────────────────────────────

#ifdef BOARD_GIGA

void sendParentSPA(WiFiClient& c) {
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
          "#hdr{background:#1a1a2e;padding:1em 2em;border-bottom:2px solid #333}"
          "#hdr h1{font-size:1.6em;margin-bottom:.2em}"
          "#hs{font-size:.9em;color:#aaa}"
          "nav{background:#161625;padding:.4em 1.5em;border-bottom:1px solid #2a2a3a}"
          ".tnav{background:none;border:none;color:#888;padding:.55em 1.1em;"
          "cursor:pointer;font-size:.92em;font-family:inherit;"
          "border-bottom:2px solid transparent}"
          ".tnav:hover{color:#ccc}"
          ".tact{color:#fff;border-bottom:2px solid #66f}"
          ".tab{padding:1.5em}"
          ".card{background:#1e1e1e;border:1px solid #333;border-radius:10px;"
          "padding:1.2em 1.5em;margin-bottom:1em;max-width:480px}"
          ".card-title{font-size:1.05em;font-weight:bold;color:#ccc;margin-bottom:.8em}");

  c.print(".tbl{border-collapse:collapse;width:100%;max-width:900px}"
          ".tbl th,.tbl td{padding:.4em .7em;border:1px solid #2a2a2a;text-align:left;font-size:.85em}"
          ".tbl th{background:#1e1e1e;color:#aaa}"
          ".tbl tr:nth-child(even){background:#191919}"
          ".badge{display:inline-block;padding:.2em .7em;border-radius:9px;"
          "font-size:.8em;font-weight:bold}"
          ".bon{background:#1a4d1a;color:#4c4}"
          ".boff{background:#4d1a1a;color:#c44}"
          ".btn{display:inline-block;padding:.4em 1.1em;border-radius:5px;"
          "border:none;cursor:pointer;font-size:.85em;font-family:inherit;"
          "font-weight:bold;margin-left:.3em}"
          ".btn:hover{opacity:.8}"
          ".btn-on{background:#2a2;color:#fff}"
          ".btn-off{background:#a22;color:#fff}"
          ".btn-nav{background:#446;color:#fff;text-decoration:none;padding:.4em 1.1em}"
          "input,select{background:#222;color:#eee;border:1px solid #444;"
          "border-radius:4px;padding:.35em .5em;margin:.2em 0}"
          "label{color:#999;font-size:.88em;display:block;margin-top:.6em}"
          ".footer{padding:.8em 2em;font-size:.7em;color:#444}"
          ".modal-bg{display:none;position:fixed;top:0;left:0;width:100%;height:100%;"
          "background:rgba(0,0,0,.75);z-index:100;overflow:auto}"
          ".modal-box{background:#1e1e1e;border:1px solid #444;border-radius:10px;"
          "max-width:540px;margin:3em auto;padding:1.5em}"
          ".modal-hdr{display:flex;justify-content:space-between;align-items:center;"
          "margin-bottom:.8em}"
          ".modal-close{background:none;border:none;color:#aaa;font-size:1.3em;"
          "cursor:pointer;line-height:1}"
          ".prog-bar{height:8px;background:#2a2a2a;border-radius:4px;margin-top:.4em}"
          ".prog-fill{height:100%;background:#66f;border-radius:4px;transition:width .5s}"
          "body.light{background:#f0f2f5;color:#222}"
          "body.light #hdr{background:#e8eaf0;border-bottom-color:#ccc}"
          "body.light nav{background:#eaeaf5;border-bottom-color:#ccc}"
          "body.light .tnav{color:#555}"
          "body.light .tnav:hover{color:#111}"
          "body.light .tact{color:#111;border-bottom-color:#66f}"
          "body.light .card{background:#fff;border-color:#ccc}"
          "body.light .card-title{color:#333}"
          "body.light .tbl th{background:#f0f0f0;color:#555}"
          "body.light .tbl td{color:#333}"
          "body.light .tbl tr:nth-child(even){background:#f9f9f9}"
          "body.light .modal-box{background:#fff;border-color:#ccc}"
          "body.light input,body.light select{background:#fff;color:#111;border-color:#bbb}"
          "body.light label{color:#555}"
          "body.light #hs{color:#555}"
          "body.light .footer{color:#aaa}"
          "body.light .prog-bar{background:#ddd}");

  c.print("</style></head><body id='app'>"
          "<div id='hdr'><h1>SlyLED</h1><div id='hs'>Loading...</div></div>"
          "<nav>"
          "<button id='n-dash' class='tnav tact' onclick='showTab(\"dash\")'>Dashboard</button>"
          "<button id='n-setup' class='tnav' onclick='showTab(\"setup\")'>Setup</button>"
          "<button id='n-layout' class='tnav' onclick='showTab(\"layout\")'>Layout</button>"
          "<button id='n-actions' class='tnav' onclick='showTab(\"actions\")'>Actions</button>"
          "<button id='n-runtime' class='tnav' onclick='showTab(\"runtime\")'>Runtime</button>"
          "<button id='n-settings' class='tnav' onclick='showTab(\"settings\")'>Settings</button>"
          "</nav>");

  c.print("<div id='t-dash' class='tab'><p style='color:#888'>Loading...</p></div>"
          "<div id='t-setup' class='tab' style='display:none'></div>");

  c.print("<div id='t-layout' class='tab' style='display:none'>"
          "<p style='color:#888;font-size:.85em;margin-bottom:.6em'>"
          "Drag nodes to position. (0,0)=bottom-left.</p>"
          "<canvas id='lcv' width='640' height='320'"
          " style='border:1px solid #444;cursor:grab;display:block;background:#0d0d0d'"
          " onmousedown='cvDown(event)' onmousemove='cvMove(event)' onmouseup='cvUp()'"
          " onmouseleave='cvUp()'></canvas>"
          "<div style='margin-top:.6em'>"
          "<button class='btn btn-on' onclick='saveLayout()'>Save Layout</button>"
          "</div></div>");

  c.print("<div id='t-actions' class='tab' style='display:none'>"
          "<div class='card' style='max-width:480px'>"
          "<div class='card-title'>Immediate Action</div>"
          "<label>Type</label>"
          "<select id='at' onchange='onAtC()'>"
          "<option value='0'>Off (Stop)</option>"
          "<option value='1' selected>Solid</option>"
          "<option value='2'>Flash</option>"
          "<option value='3'>Wipe</option>"
          "</select>"
          "<div id='a-cr'><label>Colour</label>"
          "<input type='color' id='ac' value='#ff0000'></div>"
          "<div id='a-fr' style='display:none'>"
          "<label>On (ms)</label><input type='number' id='a-on' value='500' min='50' max='5000' style='width:90px'>"
          "<label>Off (ms)</label><input type='number' id='a-of' value='500' min='50' max='5000' style='width:90px'>"
          "</div>"
          "<div id='a-wr' style='display:none'>"
          "<label>Direction</label>"
          "<select id='a-wd'>"
          "<option value='0'>East (+X)</option>"
          "<option value='1'>North (+Y)</option>"
          "<option value='2'>West (-X)</option>"
          "<option value='3'>South (-Y)</option>"
          "</select>"
          "<label>Speed (% of strip / second)</label>"
          "<input type='number' id='a-ws' value='50' min='1' max='100' style='width:80px'>"
          "</div>"
          "<label>Target</label>"
          "<select id='a-tg'><option value='all'>All Children</option></select>"
          "<div style='margin-top:1em'>"
          "<button class='btn btn-on' onclick='applyAct()'>Apply</button>"
          "<button class='btn btn-off' onclick='stopAct()' style='margin-left:.5em'>Stop All</button>"
          "</div></div></div>");

  // Runtime tab
  c.print("<div id='t-runtime' class='tab' style='display:none'>"
          "<div id='rt-list'>"
          "<div style='margin-bottom:.8em'>"
          "<button class='btn btn-on' onclick='newRunner()'>+ New Runner</button>"
          "<button class='btn btn-off' onclick='stopRunners()' style='margin-left:.5em'>Stop All Runners</button>"
          "</div>"
          "<div id='rn-list'><p style='color:#888'>Loading...</p></div>"
          "</div>"
          "<div id='rt-edit' style='display:none'>"
          "<div style='margin-bottom:.8em;display:flex;align-items:center;gap:.8em'>"
          "<button class='btn' onclick='backToRunners()' style='background:#446;color:#fff'>&larr; Runners</button>"
          "<span id='re-nm' style='font-weight:bold;color:#ccc'></span>"
          "</div>"
          "<div id='re-steps'></div>"
          "<div style='margin-top:.8em'>"
          "<button class='btn btn-on' onclick='addStep()'>+ Add Step</button>"
          "<button class='btn btn-on' onclick='saveRunner()' style='margin-left:.5em'>Save Runner</button>"
          "</div></div></div>");

  c.print("<div id='t-settings' class='tab' style='display:none'>"
          "<div class='card'>"
          "<div class='card-title'>App Settings</div>"
          "<label>Parent Name</label>"
          "<input id='s-nm' maxlength='15' style='width:200px'>"
          "<label>Units</label>"
          "<select id='s-un'>"
          "<option value='0'>Metric (mm)</option>"
          "<option value='1'>Imperial (in)</option>"
          "</select>"
          "<label>Canvas Width (mm)</label>"
          "<input id='s-cw' type='number' min='1000' max='100000' style='width:120px'>"
          "<label>Canvas Height (mm)</label>"
          "<input id='s-ch' type='number' min='1000' max='100000' style='width:120px'>"
          "<label style='display:flex;align-items:center;gap:.5em;margin-top:.8em'>"
          "<input type='checkbox' id='s-dm'> Dark Mode"
          "</label>"
          "<div style='margin-top:1em'>"
          "<button class='btn btn-on' onclick='saveSettings()'>Save Settings</button>"
          "</div></div></div>");

  c.print("<div id='modal' class='modal-bg' onclick='if(event.target===this)closeModal()'>"
          "<div class='modal-box'>"
          "<div class='modal-hdr'>"
          "<span id='modal-title' style='font-weight:bold;color:#ccc'></span>"
          "<button class='modal-close' onclick='closeModal()'>&#x2715;</button>"
          "</div>"
          "<div id='modal-body'></div>"
          "</div></div>");
  sendBuf(c, "<div class='footer'>v%d.%d &mdash; Parent</div>", APP_MAJOR, APP_MINOR);
  c.flush();

  // ── JavaScript ──────────────────────────────────────────────────────────
  c.print("<script>"
          "var ctab='dash',ld=null,phW=10000,phH=5000,drag=null,dox=0,doy=0,units=0;"
          "var curRid=-1,curRname='',curRsteps=[];"
          "function showTab(t){"
          "ctab=t;"
          "['dash','setup','layout','actions','runtime','settings'].forEach(function(id){"
          "document.getElementById('t-'+id).style.display=id===t?'block':'none';"
          "var n=document.getElementById('n-'+id);"
          "n.className='tnav'+(id===t?' tact':'');"
          "});"
          "if(t==='dash')loadDash();"
          "else if(t==='setup')loadSetup();"
          "else if(t==='layout')loadLayout();"
          "else if(t==='actions')loadActions();"
          "else if(t==='runtime')loadRuntime();"
          "else if(t==='settings')loadSettings();"
          "}");

  c.print("function ra(m,p,b,cb){"
          "var x=new XMLHttpRequest();"
          "x.open(m,p,true);"
          "if(b)x.setRequestHeader('Content-Type','application/json');"
          "x.onload=function(){try{if(cb)cb(JSON.parse(x.responseText));}catch(e){if(cb)cb(null);}};"
          "x.send(b?JSON.stringify(b):null);"
          "}");

  c.print("var dashRunnerTimer=null;"
          "function loadDash(){"
          "if(dashRunnerTimer)clearInterval(dashRunnerTimer);"
          "ra('GET','/api/children',null,function(d){"
          "var h='<table class=\"tbl\"><tr>"
          "<th>Hostname</th><th>Name</th><th>IP</th>"
          "<th>Status</th><th>Strings</th><th>Last Seen</th></tr>';"
          "if(d&&d.length){"
          "d.forEach(function(c){"
          "var st=c.status===1"
          "?'<span class=\"badge bon\">Online</span>'"
          ":'<span class=\"badge boff\">Offline</span>';"
          "var ts=c.seen>0?new Date(c.seen*1000).toLocaleString():'-';"
          "h+='<tr><td>'+c.hostname+'</td><td>'+(c.name||'-')+'</td><td>'"
          "+c.ip+'</td><td>'+st+'</td><td>'+c.sc+'</td><td>'+ts+'</td></tr>';"
          "});"
          "}else{"
          "h+='<tr><td colspan=\"6\" style=\"color:#888;text-align:center\">"
          "No children registered</td></tr>';"
          "}"
          "h+='</table><div id=\"dash-runner\" style=\"margin-top:1em\"></div>';"
          "document.getElementById('t-dash').innerHTML=h;"
          "document.getElementById('hs').textContent="
          "d&&d.length?d.length+' child'+(d.length>1?'ren':'')+' registered':'No children';"
          "refreshRunnerStatus();"
          "dashRunnerTimer=setInterval(refreshRunnerStatus,3000);"
          "});"
          "}"
          "function refreshRunnerStatus(){"
          "ra('GET','/api/settings',null,function(s){"
          "var el=document.getElementById('dash-runner');if(!el)return;"
          "if(!s||!s.runnerRunning||s.activeRunner<0){"
          "el.innerHTML='<p style=\"color:#888;font-size:.85em\">No runner active.</p>';"
          "return;"
          "}"
          "ra('GET','/api/runners/'+s.activeRunner,null,function(r){"
          "var el2=document.getElementById('dash-runner');if(!el2)return;"
          "if(!r){el2.innerHTML='';return;}"
          "var total=0;"
          "r.steps.forEach(function(st){total+=st.durationS||0;});"
          "var h='<div class=\"card\" style=\"max-width:420px\">';"
          "h+='<div class=\"card-title\">&#9654; Runner: '+r.name+'</div>';"
          "if(total>0){"
          "var pct=Math.min(100,Math.round(s.runnerElapsed*100/total));"
          "h+='<div class=\"prog-bar\"><div class=\"prog-fill\" id=\"pf\" style=\"width:'+pct+'%\"></div></div>';"
          "}"
          "h+='<div style=\"margin-top:.6em\">';"
          "h+='<button class=\"btn btn-off\" onclick=\"stopRunners()\" style=\"margin-right:.5em\">Stop</button>';"
          "h+='</div></div>';"
          "el2.innerHTML=h;"
          "});"
          "});"
          "}");

  c.print("function loadSetup(){"
          "ra('GET','/api/children',null,function(d){"
          "var h='<div style=\"margin-bottom:1em\">"
          "<input id=\"aip\" placeholder=\"Child IP (x.x.x.x)\" style=\"width:160px\">"
          "<button class=\"btn btn-on\" onclick=\"addChild()\" style=\"margin-left:.5em\">Add Child</button>"
          "<a class=\"btn btn-nav\" href=\"/api/children/export\" style=\"margin-left:.5em\">Export JSON</a>"
          "<label class=\"btn btn-nav\" style=\"cursor:pointer;margin-left:.5em\">Import JSON"
          "<input type=\"file\" accept=\".json\" style=\"display:none\" onchange=\"importChildren(this)\">"
          "</label>"
          "</div>"
          "<table class=\"tbl\"><tr>"
          "<th>Hostname</th><th>Name</th><th>IP</th><th>Status</th><th>Strings</th><th>Actions</th>"
          "</tr>';"
          "if(d&&d.length){"
          "d.forEach(function(c){"
          "h+='<tr><td>'+c.hostname+'</td><td>'+(c.name||'-')+'</td><td>'"
          "+c.ip+'</td><td>'+(c.status===1?'Online':'Offline')+'</td><td>'+c.sc+'</td><td>'"
          "+'<button class=\"btn\" onclick=\"showDetails('+c.id+')\""
          " style=\"background:#446;color:#fff\">Details</button>'"
          "+' <button class=\"btn btn-on\" onclick=\"refreshChild('+c.id+')\">Refresh</button>'"
          "+' <button class=\"btn btn-off\" onclick=\"removeChild('+c.id+')\">Remove</button></td></tr>';"
          "});"
          "}else{"
          "h+='<tr><td colspan=\"6\" style=\"color:#888;text-align:center\">No children</td></tr>';"
          "}"
          "document.getElementById('t-setup').innerHTML=h+'</table>';"
          "});"
          "}");

  c.print("function addChild(){"
          "var ip=document.getElementById('aip').value.trim();"
          "if(!ip)return;"
          "ra('POST','/api/children',{ip:ip},function(){loadSetup();});"
          "}"
          "function removeChild(id){"
          "if(!confirm('Remove this child?'))return;"
          "var x=new XMLHttpRequest();"
          "x.open('DELETE','/api/children/'+id,true);"
          "x.onload=function(){loadSetup();};"
          "x.send();"
          "}"
          "function refreshChild(id){"
          "ra('POST','/api/children/'+id+'/refresh',{},function(){"
          "setTimeout(loadSetup,700);"
          "});"
          "}"
          "function closeModal(){"
          "document.getElementById('modal').style.display='none';"
          "}"
          "function showDetails(id){"
          "ra('GET','/api/children/export',null,function(d){"
          "if(!d)return;"
          "var c=null;"
          "for(var i=0;i<d.length;i++){if(d[i].id===id){c=d[i];break;}}"
          "if(!c)return;"
          "var dirs=['E','N','W','S'];"
          "var types=['WS2812B','WS2811','APA102'];"
          "var h='<p style=\"font-size:.85em;margin-bottom:.6em\">';"
          "h+='IP: <a href=\"http://'+c.ip+'/config\" target=\"_blank\""
          " style=\"color:#88f\">'+c.ip+'/config</a>';"
          "if(c.desc)h+=' &mdash; '+c.desc;"
          "h+='</p>';"
          "h+='<table class=\"tbl\" style=\"font-size:.8em\">';"
          "h+='<tr><th>#</th><th>LEDs</th><th>Len mm</th><th>Type</th>"
          "<th>Cable Dir</th><th>Cable mm</th><th>Strip Dir</th></tr>';"
          "(c.strings||[]).forEach(function(s,i){"
          "h+='<tr><td>'+(i+1)+'</td>';"
          "h+='<td>'+s.leds+'</td><td>'+s.mm+'</td>';"
          "h+='<td>'+(types[s.type]||s.type)+'</td>';"
          "h+='<td>'+(dirs[s.cdir]||s.cdir)+'</td>';"
          "h+='<td>'+s.cmm+'</td>';"
          "h+='<td>'+(dirs[s.sdir]||s.sdir)+'</td></tr>';"
          "});"
          "h+='</table>';"
          "document.getElementById('modal-title').textContent="
          "c.hostname+(c.name&&c.name!==c.hostname?' ('+c.name+')':'');"
          "document.getElementById('modal-body').innerHTML=h;"
          "document.getElementById('modal').style.display='block';"
          "});"
          "}"
          "function importChildren(input){"
          "var f=input.files[0];if(!f)return;"
          "var rd=new FileReader();"
          "rd.onload=function(e){"
          "try{var data=JSON.parse(e.target.result);}catch(ex){alert('Invalid JSON');return;}"
          "var x=new XMLHttpRequest();"
          "x.open('POST','/api/children/import',true);"
          "x.setRequestHeader('Content-Type','application/json');"
          "x.onload=function(){"
          "try{"
          "var r=JSON.parse(x.responseText);"
          "if(r.ok){"
          "document.getElementById('hs').textContent="
          "'Imported: +'+r.added+' updated '+r.updated+' skipped '+r.skipped;"
          "loadSetup();"
          "}"
          "}catch(ex){}"
          "};"
          "x.send(JSON.stringify(data));"
          "};"
          "rd.readAsText(f);"
          "input.value='';"
          "}");

  c.print("function loadLayout(){"
          "ra('GET','/api/settings',null,function(s){if(s)units=s.units||0;});"
          "ra('GET','/api/layout',null,function(d){"
          "if(!d)return;"
          "ld=d;phW=d.canvasW||10000;phH=d.canvasH||5000;"
          "drawLayout();"
          "});"
          "}"
          "function fmtCoord(mm){"
          "if(units===1)return (mm/25.4).toFixed(1)+'\"';"
          "return mm+'mm';"
          "}"
          "function drawLayout(){"
          "var cv=document.getElementById('lcv');"
          "if(!cv||!ld)return;"
          "var W=cv.width,H=cv.height;"
          "var ctx=cv.getContext('2d');"
          "ctx.fillStyle='#0d0d0d';ctx.fillRect(0,0,W,H);"
          "ctx.strokeStyle='#1e1e1e';ctx.lineWidth=1;"
          "for(var gx=0;gx<=W;gx+=64){ctx.beginPath();ctx.moveTo(gx,0);ctx.lineTo(gx,H);ctx.stroke();}"
          "for(var gy=0;gy<=H;gy+=32){ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(W,gy);ctx.stroke();}"
          "ctx.strokeStyle='#2a2a2a';ctx.lineWidth=1;"
          "ctx.strokeRect(0,0,W,H);"
          "if(!ld.children)return;"
          "ld.children.forEach(function(c){"
          "var cx=Math.round(c.x*W/phW);"
          "var cy=H-Math.round(c.y*H/phH);"
          "ctx.beginPath();ctx.arc(cx,cy,12,0,2*Math.PI);"
          "ctx.fillStyle=c.status===1?'#1a6b3a':'#444';ctx.fill();"
          "ctx.strokeStyle=c.status===1?'#4c4':'#888';"
          "ctx.lineWidth=1.5;ctx.stroke();"
          "ctx.fillStyle='#eee';ctx.font='10px sans-serif';ctx.textAlign='center';"
          "ctx.fillText(c.hostname,cx,cy+22);"
          "var lbl2=c.name&&c.name!==c.hostname?c.name:'';"
          "var coordStr='('+fmtCoord(c.x)+','+fmtCoord(c.y)+')';"
          "ctx.fillStyle='#888';ctx.font='9px sans-serif';"
          "if(lbl2){ctx.fillText(lbl2,cx,cy+33);ctx.fillText(coordStr,cx,cy+44);}"
          "else{ctx.fillText(coordStr,cx,cy+33);}"
          "});"
          "}");

  c.print("function saveLayout(){"
          "if(!ld||!ld.children)return;"
          "ra('POST','/api/layout',"
          "{children:ld.children.map(function(c){return{id:c.id,x:c.x,y:c.y};})},"
          "function(r){"
          "if(r&&r.ok)document.getElementById('hs').textContent='Layout saved';"
          "});"
          "}"
          "function cvDown(e){"
          "var cv=document.getElementById('lcv');"
          "var r=cv.getBoundingClientRect();"
          "var mx=e.clientX-r.left,my=e.clientY-r.top;"
          "var W=cv.width,H=cv.height;"
          "if(!ld||!ld.children)return;"
          "drag=null;"
          "ld.children.forEach(function(c,i){"
          "var cx=Math.round(c.x*W/phW);"
          "var cy=H-Math.round(c.y*H/phH);"
          "if(Math.abs(mx-cx)<=14&&Math.abs(my-cy)<=14){"
          "drag=i;dox=mx-cx;doy=my-cy;"
          "}"
          "});"
          "}"
          "function cvMove(e){"
          "if(drag===null)return;"
          "var cv=document.getElementById('lcv');"
          "var r=cv.getBoundingClientRect();"
          "var W=cv.width,H=cv.height;"
          "var mx=Math.max(0,Math.min(W,e.clientX-r.left-dox));"
          "var my=Math.max(0,Math.min(H,e.clientY-r.top-doy));"
          "ld.children[drag].x=Math.round(mx*phW/W);"
          "ld.children[drag].y=Math.round((H-my)*phH/H);"
          "drawLayout();"
          "}"
          "function cvUp(){drag=null;}");

  c.print("function loadActions(){"
          "ra('GET','/api/children',null,function(d){"
          "var s=document.getElementById('a-tg');"
          "while(s.options.length>1)s.remove(1);"
          "if(d&&d.length)d.forEach(function(c){"
          "var o=document.createElement('option');"
          "o.value=String(c.id);"
          "o.text=c.hostname+(c.name?(' ('+c.name+')'):'');"
          "s.add(o);"
          "});"
          "});"
          "}"
          "function onAtC(){"
          "var t=parseInt(document.getElementById('at').value);"
          "document.getElementById('a-cr').style.display=t===0?'none':'block';"
          "document.getElementById('a-fr').style.display=t===2?'block':'none';"
          "document.getElementById('a-wr').style.display=t===3?'block':'none';"
          "}"
          "function h2r(h){return{r:parseInt(h.slice(1,3),16),g:parseInt(h.slice(3,5),16),b:parseInt(h.slice(5,7),16)};}"
          "function applyAct(){"
          "var t=parseInt(document.getElementById('at').value);"
          "var col=t>0?h2r(document.getElementById('ac').value):{r:0,g:0,b:0};"
          "var body={"
          "type:t,r:col.r,g:col.g,b:col.b,"
          "onMs:parseInt(document.getElementById('a-on').value)||500,"
          "offMs:parseInt(document.getElementById('a-of').value)||500,"
          "wipeDir:parseInt(document.getElementById('a-wd').value)||0,"
          "wipeSpeedPct:parseInt(document.getElementById('a-ws').value)||50,"
          "target:document.getElementById('a-tg').value"
          "};"
          "ra('POST','/api/action',body,function(r){"
          "if(r&&r.ok)document.getElementById('hs').textContent="
          "t===0?'Action: off':'Action applied';"
          "});"
          "}"
          "function stopAct(){"
          "ra('POST','/api/action/stop',"
          "{target:document.getElementById('a-tg').value},"
          "function(r){if(r&&r.ok)document.getElementById('hs').textContent='Action stopped';});"
          "}");

  // ── Runtime tab JavaScript ───────────────────────────────────────────────
  c.print("function loadRuntime(){"
          "ra('GET','/api/runners',null,function(d){"
          "var h='';"
          "if(d&&d.length){"
          "d.forEach(function(r){"
          "h+='<div style=\"display:flex;align-items:center;gap:.5em;"
          "padding:.4em 0;border-bottom:1px solid #2a2a2a\">';"
          "h+='<span style=\"flex:1\">'+r.name"
          "+'<span style=\"color:#888;font-size:.82em\"> ('+r.steps+' steps"
          "+(r.computed?' \u2713':'')+' )</span></span>';"
          "h+='<button class=\"btn btn-on\" onclick=\"showRunner('+r.id+')\""
          " style=\"padding:.3em .8em\">Edit</button>';"
          "h+=' <button class=\"btn\" onclick=\"doCompute('+r.id+')\""
          " style=\"background:#446;color:#fff;padding:.3em .8em\">Compute</button>';"
          "if(r.computed){"
          "h+=' <button class=\"btn\" onclick=\"doSync('+r.id+')\""
          " style=\"background:#264;color:#fff;padding:.3em .8em\">Sync</button>';"
          "h+=' <button class=\"btn btn-on\" onclick=\"doStart('+r.id+')\""
          " style=\"padding:.3em .8em\">Start</button>';"
          "}"
          "h+=' <button class=\"btn btn-off\" onclick=\"delRunner('+r.id+')\""
          " style=\"padding:.3em .8em\">Del</button>';"
          "h+='</div>';"
          "});"
          "}else{h='<p style=\"color:#888\">No runners yet.</p>';}"
          "document.getElementById('rn-list').innerHTML=h;"
          "});"
          "}");

  c.print("function newRunner(){"
          "var nm=prompt('Runner name:','Runner');"
          "if(!nm)return;"
          "ra('POST','/api/runners',{name:nm},function(r){"
          "if(r&&r.ok){loadRuntime();showRunner(r.id);}"
          "});"
          "}"
          "function delRunner(id){"
          "if(!confirm('Delete runner?'))return;"
          "var x=new XMLHttpRequest();"
          "x.open('DELETE','/api/runners/'+id,true);"
          "x.onload=function(){backToRunners();loadRuntime();};"
          "x.send();"
          "}"
          "function showRunner(id){"
          "ra('GET','/api/runners/'+id,null,function(d){"
          "if(!d)return;"
          "curRid=d.id;curRname=d.name;"
          "curRsteps=(d.steps||[]).map(function(s){return Object.assign({},s);});"
          "document.getElementById('re-nm').textContent=d.name;"
          "document.getElementById('rt-list').style.display='none';"
          "document.getElementById('rt-edit').style.display='block';"
          "renderSteps();"
          "});"
          "}"
          "function backToRunners(){"
          "document.getElementById('rt-list').style.display='block';"
          "document.getElementById('rt-edit').style.display='none';"
          "}");

  c.print("function rgb2h(r,g,b){"
          "return'#'+('0'+r.toString(16)).slice(-2)"
          "+('0'+g.toString(16)).slice(-2)"
          "+('0'+b.toString(16)).slice(-2);"
          "}"
          "function renderSteps(){"
          "var h='';"
          "if(curRsteps.length>0){"
          "h+='<div style=\"overflow-x:auto\">"
          "<table class=\"tbl\" style=\"font-size:.8em\">';"
          "h+='<tr><th>#</th><th>Type</th><th>Col</th>"
          "<th>On ms</th><th>Off ms</th><th>WDir</th><th>Spd%</th>"
          "<th>x0-x1%</th><th>y0-y1%</th><th>Dur s</th><th></th></tr>';"
          "curRsteps.forEach(function(st,i){"
          "var col=rgb2h(st.r||0,st.g||0,st.b||0);"
          "var x0=Math.round((st.x0||0)/100);"
          "var x1=Math.round((st.x1!=null?st.x1:10000)/100);"
          "var y0=Math.round((st.y0||0)/100);"
          "var y1=Math.round((st.y1!=null?st.y1:10000)/100);"
          "h+='<tr><td>'+(i+1)+'</td>';"
          "h+='<td><select id=\"s'+i+'t\">';"
          "['Off','Solid','Flash','Wipe'].forEach(function(n,v){"
          "h+='<option value=\"'+v+'\"'+(st.type===v?' selected':'')+'>'+n+'</option>';"
          "});"
          "h+='</select></td>';"
          "h+='<td><input type=\"color\" id=\"s'+i+'c\" value=\"'+col+'\""
          " style=\"width:44px;height:26px\"></td>';"
          "h+='<td><input type=\"number\" id=\"s'+i+'on\" value=\"'+(st.onMs||500)"
          "+'\"\tmin=\"50\" max=\"9999\" style=\"width:60px\"></td>';"
          "h+='<td><input type=\"number\" id=\"s'+i+'of\" value=\"'+(st.offMs||500)"
          "+'\"\tmin=\"50\" max=\"9999\" style=\"width:60px\"></td>';"
          "h+='<td><select id=\"s'+i+'wd\">';"
          "['E','N','W','S'].forEach(function(n,v){"
          "h+='<option value=\"'+v+'\"'+(st.wdir===v?' selected':'')+'>'+n+'</option>';"
          "});"
          "h+='</select></td>';"
          "h+='<td><input type=\"number\" id=\"s'+i+'ws\" value=\"'+(st.wspd||50)"
          "+'\"\tmin=\"1\" max=\"100\" style=\"width:48px\"></td>';"
          "h+='<td style=\"white-space:nowrap\">';"
          "h+='<input type=\"number\" id=\"s'+i+'x0\" value=\"'+x0"
          "+'\"\tmin=\"0\" max=\"100\" style=\"width:38px\">-';"
          "h+='<input type=\"number\" id=\"s'+i+'x1\" value=\"'+x1"
          "+'\"\tmin=\"0\" max=\"100\" style=\"width:38px\">';"
          "h+='</td><td style=\"white-space:nowrap\">';"
          "h+='<input type=\"number\" id=\"s'+i+'y0\" value=\"'+y0"
          "+'\"\tmin=\"0\" max=\"100\" style=\"width:38px\">-';"
          "h+='<input type=\"number\" id=\"s'+i+'y1\" value=\"'+y1"
          "+'\"\tmin=\"0\" max=\"100\" style=\"width:38px\">';"
          "h+='</td>';"
          "h+='<td><input type=\"number\" id=\"s'+i+'d\" value=\"'+(st.durationS||5)"
          "+'\"\tmin=\"1\" max=\"3600\" style=\"width:52px\"></td>';"
          "h+='<td><button class=\"btn btn-off\" onclick=\"rmStep('+i+')\""
          " style=\"padding:.2em .5em\">\u00d7</button></td>';"
          "h+='</tr>';"
          "});"
          "h+='</table></div>';"
          "}else{h='<p style=\"color:#888;margin:.5em 0\">No steps. Add one below.</p>';}"
          "document.getElementById('re-steps').innerHTML=h;"
          "}");

  c.print("function addStep(){"
          "curRsteps.push({type:1,r:255,g:0,b:0,onMs:500,offMs:500,"
          "wdir:0,wspd:50,x0:0,y0:0,x1:10000,y1:10000,durationS:5});"
          "renderSteps();"
          "}"
          "function rmStep(i){curRsteps.splice(i,1);renderSteps();}"
          "function gv(id,def){"
          "var el=document.getElementById(id);"
          "if(!el)return def;"
          "var v=parseInt(el.value,10);"
          "return isNaN(v)?def:v;"
          "}"
          "function gcol(id){"
          "var el=document.getElementById(id);"
          "if(!el)return{r:0,g:0,b:0};"
          "var h=el.value;"
          "return{r:parseInt(h.slice(1,3),16),"
          "g:parseInt(h.slice(3,5),16),"
          "b:parseInt(h.slice(5,7),16)};"
          "}"
          "function saveRunner(){"
          "if(curRid<0)return;"
          "var steps=[];"
          "for(var i=0;i<curRsteps.length;i++){"
          "var col=gcol('s'+i+'c');"
          "steps.push({type:gv('s'+i+'t',1),"
          "r:col.r,g:col.g,b:col.b,"
          "onMs:gv('s'+i+'on',500),offMs:gv('s'+i+'of',500),"
          "wdir:gv('s'+i+'wd',0),wspd:gv('s'+i+'ws',50),"
          "x0:gv('s'+i+'x0',0)*100,y0:gv('s'+i+'y0',0)*100,"
          "x1:gv('s'+i+'x1',100)*100,y1:gv('s'+i+'y1',100)*100,"
          "dur:gv('s'+i+'d',5)});"
          "}"
          "var x=new XMLHttpRequest();"
          "x.open('PUT','/api/runners/'+curRid,true);"
          "x.setRequestHeader('Content-Type','application/json');"
          "x.onload=function(){"
          "try{if(JSON.parse(x.responseText).ok){"
          "document.getElementById('hs').textContent='Runner saved';"
          "loadRuntime();"
          "}}catch(e){}"
          "};"
          "x.send(JSON.stringify({name:curRname,steps:steps}));"
          "}"
          "function doCompute(id){"
          "document.getElementById('hs').textContent='Computing...';"
          "ra('POST','/api/runners/'+id+'/compute',null,function(r){"
          "if(r&&r.ok){"
          "document.getElementById('hs').textContent='Runner computed \u2713';"
          "loadRuntime();"
          "}"
          "});"
          "}"
          "function doSync(id){"
          "document.getElementById('hs').textContent='Syncing to children...';"
          "ra('POST','/api/runners/'+id+'/sync',null,function(r){"
          "if(r&&r.ok)document.getElementById('hs').textContent='Synced \u2713';"
          "});"
          "}"
          "function doStart(id){"
          "ra('POST','/api/runners/'+id+'/start',null,function(r){"
          "if(r&&r.ok)document.getElementById('hs').textContent='Runner started';"
          "});"
          "}"
          "function stopRunners(){"
          "ra('POST','/api/runners/stop',null,function(r){"
          "if(r&&r.ok)document.getElementById('hs').textContent='Runners stopped';"
          "});"
          "}");

  c.print("function applyDarkMode(dm){"
          "var b=document.getElementById('app');"
          "if(dm)b.classList.remove('light');"
          "else b.classList.add('light');"
          "}"
          "function loadSettings(){"
          "ra('GET','/api/settings',null,function(d){"
          "if(!d)return;"
          "document.getElementById('s-nm').value=d.name||'';"
          "document.getElementById('s-un').value=d.units||0;"
          "document.getElementById('s-cw').value=d.canvasW||10000;"
          "document.getElementById('s-ch').value=d.canvasH||5000;"
          "var cb=document.getElementById('s-dm');"
          "if(cb)cb.checked=(d.darkMode!==0);"
          "applyDarkMode(d.darkMode!==0);"
          "});"
          "}"
          "function saveSettings(){"
          "var dm=document.getElementById('s-dm').checked?1:0;"
          "applyDarkMode(dm);"
          "ra('POST','/api/settings',{"
          "name:document.getElementById('s-nm').value.trim(),"
          "units:parseInt(document.getElementById('s-un').value)||0,"
          "canvasW:parseInt(document.getElementById('s-cw').value)||10000,"
          "canvasH:parseInt(document.getElementById('s-ch').value)||5000,"
          "darkMode:dm"
          "},function(r){if(r&&r.ok)document.getElementById('hs').textContent='Settings saved';});"
          "}"
          "ra('GET','/api/settings',null,function(d){"
          "applyDarkMode(!d||d.darkMode!==0);"
          "showTab('dash');"
          "});"
          "</script></body></html>");
  c.flush();
}

#endif  // BOARD_GIGA

// ── SPA dispatcher ────────────────────────────────────────────────────────────

void sendMain(WiFiClient& c) {
#ifdef BOARD_GIGA
  sendParentSPA(c);
#else
  // Child SPA — simple status page
  c.print("HTTP/1.1 200 OK\r\n"
          "Content-Type: text/html\r\n"
          "Connection: close\r\n"
          "Cache-Control: no-cache, no-store\r\n"
          "\r\n"
          "<!DOCTYPE html><html><head>"
          "<meta charset='utf-8'>"
          "<meta name='viewport' content='width=device-width,initial-scale=1'>"
          "<title>SlyLED Child</title><style>"
          "*{box-sizing:border-box;margin:0;padding:0}"
          "body{font-family:sans-serif;background:#111;color:#eee;padding:2em}"
          "h1{font-size:1.6em;margin-bottom:.3em}"
          ".card{background:#1e1e1e;border:1px solid #333;border-radius:8px;"
          "padding:1.2em;max-width:360px;margin-top:1em}"
          ".row{display:flex;justify-content:space-between;padding:.4em 0;"
          "border-bottom:1px solid #2a2a2a;font-size:.92em}"
          ".lbl{color:#888}.val{font-weight:bold}"
          ".footer{margin-top:2em;font-size:.7em;color:#444}"
          "</style></head><body>"
          "<h1>SlyLED</h1><p style='color:#888;font-size:.9em'>Child Node</p>"
          "<div class='card' id='sc'><p style='color:#888'>Loading...</p></div>");
  sendBuf(c, "<div class='footer'>v%d.%d</div>", APP_MAJOR, APP_MINOR);
  c.print("<script>"
          "var actN=['Off','Solid','Flash','Wipe'];"
          "function poll(){"
          "var x=new XMLHttpRequest();"
          "x.open('GET','/status',true);"
          "x.onload=function(){"
          "try{"
          "var d=JSON.parse(x.responseText);"
          "var h='<div class=\"row\"><span class=\"lbl\">Hostname</span>"
          "<span class=\"val\">'+d.hostname+'</span></div>'"
          "+'<div class=\"row\"><span class=\"lbl\">Action</span>"
          "<span class=\"val\">'+(actN[d.action]||'?')+'</span></div>';"
          "document.getElementById('sc').innerHTML=h;"
          "}catch(e){}"
          "};"
          "x.send();"
          "}"
          "poll();setInterval(poll,3000);"
          "</script></body></html>");
#endif
}

// ── Parent UDP + API functions (Giga only) ────────────────────────────────────

#ifdef BOARD_GIGA

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

void registerChild(IPAddress ip, const PongPayload* pong) {
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

void sendApiChildren(WiFiClient& c) {
  static char jsonBuf[1400];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  *p++ = '[';
  bool first = true;
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (!children[i].inUse) continue;
    if (!first) *p++ = ',';
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
  *p++ = ']'; *p = '\0';
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

void sendApiChildrenExport(WiFiClient& c) {
  static char jsonBuf[1800];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  *p++ = '[';
  bool first = true;
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (!children[i].inUse) continue;
    if (!first) *p++ = ',';
    first = false;
    char ipStr[16];
    snprintf(ipStr, sizeof(ipStr), "%u.%u.%u.%u",
             children[i].ip[0], children[i].ip[1],
             children[i].ip[2], children[i].ip[3]);
    p += snprintf(p, end - p,
      "{\"id\":%u,\"hostname\":\"%s\",\"name\":\"%s\","
      "\"desc\":\"%s\",\"ip\":\"%s\",\"status\":%u,"
      "\"x\":%d,\"y\":%d,\"sc\":%u,\"seen\":%lu,\"strings\":[",
      (unsigned)i,
      children[i].hostname, children[i].name, children[i].description,
      ipStr, (unsigned)children[i].status,
      (int)children[i].xMm, (int)children[i].yMm,
      (unsigned)children[i].stringCount,
      (unsigned long)children[i].lastSeenEpoch);
    for (uint8_t j = 0; j < children[i].stringCount && j < MAX_STR_PER_CHILD; j++) {
      if (j > 0) *p++ = ',';
      p += snprintf(p, end - p,
        "{\"leds\":%u,\"mm\":%u,\"type\":%u,\"cdir\":%u,\"cmm\":%u,\"sdir\":%u}",
        (unsigned)children[i].strings[j].ledCount,
        (unsigned)children[i].strings[j].lengthMm,
        (unsigned)children[i].strings[j].ledType,
        (unsigned)children[i].strings[j].cableDir,
        (unsigned)children[i].strings[j].cableMm,
        (unsigned)children[i].strings[j].stripDir);
    }
    p += snprintf(p, end - p, "]}");
  }
  *p++ = ']'; *p = '\0';
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Content-Disposition: attachment; filename=\"slyled-children.json\"\r\n"
             "Connection: close\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

void handleChildIdRoute(WiFiClient& c, const char* req, bool isPost, bool isDel, int contentLen) {
  (void)contentLen;
  const char* idStart = strstr(req, "/api/children/");
  if (!idStart) { sendJsonErr(c, "bad-route"); return; }
  idStart += 14;
  int id = atoi(idStart);
  if (id < 0 || id >= MAX_CHILDREN) { sendJsonErr(c, "bad-id"); return; }

  bool isRefresh = (strstr(idStart, "/refresh") != NULL);

  if (isDel && !isRefresh) {
    if (children[id].inUse) {
      memset(&children[id], 0, sizeof(ChildNode));
      if (Serial) { Serial.print(F("Child removed: ")); Serial.println(id); }
      sendJsonOk(c);
    } else {
      sendJsonErr(c, "not-found");
    }
  } else if (isPost && isRefresh) {
    if (children[id].inUse) {
      IPAddress dest(children[id].ip[0], children[id].ip[1],
                     children[id].ip[2], children[id].ip[3]);
      sendPing(dest);
      sendJsonOk(c);
    } else {
      sendJsonErr(c, "not-found");
    }
  } else {
    sendJsonErr(c, "method");
  }
}

void sendApiLayout(WiFiClient& c) {
  static char jsonBuf[900];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  p += snprintf(p, end - p,
    "{\"canvasW\":%u,\"canvasH\":%u,\"children\":[",
    (unsigned)settings.canvasWidthMm, (unsigned)settings.canvasHeightMm);
  bool first = true;
  for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
    if (!children[i].inUse) continue;
    if (!first) *p++ = ',';
    first = false;
    p += snprintf(p, end - p,
      "{\"id\":%u,\"hostname\":\"%s\",\"name\":\"%s\","
      "\"x\":%d,\"y\":%d,\"status\":%u}",
      (unsigned)i,
      children[i].hostname, children[i].name,
      (int)children[i].xMm, (int)children[i].yMm,
      (unsigned)children[i].status);
  }
  p += snprintf(p, end - p, "]}");
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

void handlePostLayout(WiFiClient& c, int contentLen) {
  char body[256] = {};
  if (contentLen > 0 && contentLen < (int)sizeof(body))
    c.readBytes(body, contentLen);
  const char* p = body;
  while ((p = strstr(p, "\"id\":")) != NULL) {
    p += 5;
    int id = atoi(p);
    const char* xp = strstr(p, "\"x\":");
    const char* yp = strstr(p, "\"y\":");
    if (!xp || !yp) break;
    int x = atoi(xp + 4);
    int y = atoi(yp + 4);
    if (id >= 0 && id < MAX_CHILDREN && children[id].inUse) {
      if (x < -30000) x = -30000; if (x > 30000) x = 30000;
      if (y < -30000) y = -30000; if (y > 30000) y = 30000;
      children[id].xMm = (int16_t)x;
      children[id].yMm = (int16_t)y;
    }
    p++;
  }
  sendJsonOk(c);
}

void sendApiSettings(WiFiClient& c) {
  char body[192];
  int blen = snprintf(body, sizeof(body),
    "{\"name\":\"%s\",\"units\":%u,\"canvasW\":%u,\"canvasH\":%u"
    ",\"darkMode\":%u,\"runnerRunning\":%s,\"activeRunner\":%d}",
    settings.parentName,
    (unsigned)settings.units,
    (unsigned)settings.canvasWidthMm,
    (unsigned)settings.canvasHeightMm,
    (unsigned)settings.darkMode,
    settings.runnerRunning ? "true" : "false",
    (settings.activeRunner == 0xFF) ? -1 : (int)settings.activeRunner);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(body);
  c.flush();
}

void handlePostSettings(WiFiClient& c, int contentLen) {
  char body[160] = {};
  if (contentLen > 0 && contentLen < (int)sizeof(body))
    c.readBytes(body, contentLen);
  jsonGetStr(body, "name", settings.parentName, sizeof(settings.parentName));
  int u  = jsonGetInt(body, "units",    (int)settings.units);
  int dm = jsonGetInt(body, "darkMode", (int)settings.darkMode);
  settings.units    = (uint8_t)(u & 1);
  settings.darkMode = (uint8_t)(dm ? 1 : 0);
  int cw = jsonGetInt(body, "canvasW", (int)settings.canvasWidthMm);
  int ch = jsonGetInt(body, "canvasH", (int)settings.canvasHeightMm);
  if (cw >= 1000 && cw <= 100000) settings.canvasWidthMm  = (uint16_t)cw;
  if (ch >= 1000 && ch <= 100000) settings.canvasHeightMm = (uint16_t)ch;
  sendJsonOk(c);
}

void sendCmdAction(IPAddress dest, const ActionPayload* p) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_ACTION;
  hdr.epoch   = (uint32_t)currentEpoch();
  memcpy(udpBuf,               &hdr, sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr), p,    sizeof(*p));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(*p));
  cmdUDP.endPacket();
}

void sendCmdActionStop(IPAddress dest) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_ACTION_STOP;
  hdr.epoch   = (uint32_t)currentEpoch();
  memcpy(udpBuf, &hdr, sizeof(hdr));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr));
  cmdUDP.endPacket();
}

void handleApiAction(WiFiClient& c, int contentLen) {
  char body[128] = {};
  if (contentLen > 0 && contentLen < (int)sizeof(body))
    c.readBytes(body, contentLen);

  ActionPayload p;
  memset(&p, 0, sizeof(p));
  p.actionType   = (uint8_t)jsonGetInt(body, "type",         ACT_OFF);
  p.r            = (uint8_t)jsonGetInt(body, "r",            0);
  p.g            = (uint8_t)jsonGetInt(body, "g",            0);
  p.b            = (uint8_t)jsonGetInt(body, "b",            0);
  p.onMs         = (uint16_t)jsonGetInt(body, "onMs",        500);
  p.offMs        = (uint16_t)jsonGetInt(body, "offMs",       500);
  p.wipeDir      = (uint8_t)jsonGetInt(body, "wipeDir",      0);
  p.wipeSpeedPct = (uint8_t)jsonGetInt(body, "wipeSpeedPct", 50);
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    p.ledStart[j] = 0x00;
    p.ledEnd[j]   = 0xFF;
  }

  char target[8] = {};
  jsonGetStr(body, "target", target, sizeof(target));

  if (strcmp(target, "all") == 0 || target[0] == '\0') {
    for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
      if (!children[i].inUse) continue;
      IPAddress dest(children[i].ip[0], children[i].ip[1],
                     children[i].ip[2], children[i].ip[3]);
      if (p.actionType == ACT_OFF) sendCmdActionStop(dest);
      else                         sendCmdAction(dest, &p);
    }
  } else {
    int id = atoi(target);
    if (id < 0 || id >= MAX_CHILDREN || !children[id].inUse) {
      sendJsonErr(c, "bad-target"); return;
    }
    IPAddress dest(children[id].ip[0], children[id].ip[1],
                   children[id].ip[2], children[id].ip[3]);
    if (p.actionType == ACT_OFF) sendCmdActionStop(dest);
    else                         sendCmdAction(dest, &p);
  }
  sendJsonOk(c);
}

void handleApiActionStop(WiFiClient& c, int contentLen) {
  char body[64] = {};
  if (contentLen > 0 && contentLen < (int)sizeof(body))
    c.readBytes(body, contentLen);
  char target[8] = {};
  jsonGetStr(body, "target", target, sizeof(target));

  if (strcmp(target, "all") == 0 || target[0] == '\0') {
    for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
      if (!children[i].inUse) continue;
      IPAddress dest(children[i].ip[0], children[i].ip[1],
                     children[i].ip[2], children[i].ip[3]);
      sendCmdActionStop(dest);
    }
  } else {
    int id = atoi(target);
    if (id < 0 || id >= MAX_CHILDREN || !children[id].inUse) {
      sendJsonErr(c, "bad-target"); return;
    }
    IPAddress dest(children[id].ip[0], children[id].ip[1],
                   children[id].ip[2], children[id].ip[3]);
    sendCmdActionStop(dest);
  }
  sendJsonOk(c);
}

// POST /api/children/import — body is JSON array in same format as export
void handleApiChildrenImport(WiFiClient& c, int contentLen) {
  static char body[1800];
  memset(body, 0, sizeof(body));
  int readLen = (contentLen < (int)sizeof(body) - 1) ? contentLen : (int)sizeof(body) - 1;
  if (readLen > 0) c.readBytes(body, readLen);

  uint8_t added = 0, updated = 0, skipped = 0;

  const char* p = body;
  while ((p = strchr(p, '{')) != NULL) {
    int depth = 0;
    const char* ep = p;
    while (*ep) {
      if (*ep == '{') depth++;
      else if (*ep == '}') { if (--depth == 0) { ep++; break; } }
      ep++;
    }
    char obj[320] = {};
    int olen = (int)(ep - p);
    if (olen >= (int)sizeof(obj)) { p = ep; skipped++; continue; }
    memcpy(obj, p, olen);

    char hn[HOSTNAME_LEN]   = {};
    char nm[CHILD_NAME_LEN] = {};
    char ds[CHILD_DESC_LEN] = {};
    char ip[16]             = {};
    jsonGetStr(obj, "hostname", hn, sizeof(hn));
    jsonGetStr(obj, "name",     nm, sizeof(nm));
    jsonGetStr(obj, "desc",     ds, sizeof(ds));
    jsonGetStr(obj, "ip",       ip, sizeof(ip));
    if (hn[0] == '\0') { p = ep; skipped++; continue; }

    // Match by hostname — update if found, add if not
    uint8_t slot = 0xFF;
    for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
      if (children[i].inUse &&
          strncmp(children[i].hostname, hn, HOSTNAME_LEN - 1) == 0) {
        slot = i; break;
      }
    }
    bool isNew = (slot == 0xFF);
    if (isNew) {
      for (uint8_t i = 0; i < MAX_CHILDREN; i++) {
        if (!children[i].inUse) { slot = i; break; }
      }
      if (slot == 0xFF) { skipped++; p = ep; continue; }
      memset(&children[slot], 0, sizeof(ChildNode));
      children[slot].inUse = true;
      strncpy(children[slot].hostname, hn, HOSTNAME_LEN - 1);
      added++;
    } else {
      updated++;
    }
    if (nm[0]) strncpy(children[slot].name,        nm, CHILD_NAME_LEN - 1);
    if (ds[0]) strncpy(children[slot].description, ds, CHILD_DESC_LEN - 1);
    int a = 0, b = 0, cc = 0, d = 0;
    if (sscanf(ip, "%d.%d.%d.%d", &a, &b, &cc, &d) == 4) {
      children[slot].ip[0] = (uint8_t)a; children[slot].ip[1] = (uint8_t)b;
      children[slot].ip[2] = (uint8_t)cc; children[slot].ip[3] = (uint8_t)d;
    }
    int x = jsonGetInt(obj, "x", 0);
    int y = jsonGetInt(obj, "y", 0);
    if (x < -30000) x = -30000; if (x > 30000) x = 30000;
    if (y < -30000) y = -30000; if (y > 30000) y = 30000;
    children[slot].xMm = (int16_t)x;
    children[slot].yMm = (int16_t)y;
    p = ep;
  }

  char resp[80];
  int rlen = snprintf(resp, sizeof(resp),
    "{\"ok\":true,\"added\":%u,\"updated\":%u,\"skipped\":%u}",
    (unsigned)added, (unsigned)updated, (unsigned)skipped);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Content-Length: %d\r\n\r\n", rlen);
  c.print(resp);
  c.flush();
}

// ── Runner API (Phase 2d) ─────────────────────────────────────────────────────

// GET /api/runners — compact list
void sendApiRunners(WiFiClient& c) {
  static char jsonBuf[512];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  *p++ = '[';
  bool first = true;
  for (uint8_t i = 0; i < MAX_RUNNERS; i++) {
    if (!runners[i].inUse) continue;
    if (!first) *p++ = ',';
    first = false;
    p += snprintf(p, end - p,
      "{\"id\":%u,\"name\":\"%s\",\"steps\":%u,\"computed\":%s}",
      (unsigned)i, runners[i].name,
      (unsigned)runners[i].stepCount,
      runners[i].computed ? "true" : "false");
  }
  *p++ = ']'; *p = '\0';
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

// GET /api/runners/:id — full runner with steps
void sendApiRunner(WiFiClient& c, uint8_t id) {
  if (id >= MAX_RUNNERS || !runners[id].inUse) {
    sendJsonErr(c, "not-found"); return;
  }
  static char jsonBuf[2048];
  char* p   = jsonBuf;
  char* end = jsonBuf + sizeof(jsonBuf) - 2;
  p += snprintf(p, end - p,
    "{\"id\":%u,\"name\":\"%s\",\"computed\":%s,\"steps\":[",
    (unsigned)id, runners[id].name,
    runners[id].computed ? "true" : "false");
  for (uint8_t s = 0; s < runners[id].stepCount; s++) {
    if (s > 0) *p++ = ',';
    RunnerStep& st = runners[id].steps[s];
    p += snprintf(p, end - p,
      "{\"type\":%u,\"r\":%u,\"g\":%u,\"b\":%u,"
      "\"onMs\":%u,\"offMs\":%u,\"wdir\":%u,\"wspd\":%u,"
      "\"x0\":%u,\"y0\":%u,\"x1\":%u,\"y1\":%u,\"durationS\":%u}",
      (unsigned)st.action.type,
      (unsigned)st.action.r, (unsigned)st.action.g, (unsigned)st.action.b,
      (unsigned)st.action.onMs, (unsigned)st.action.offMs,
      (unsigned)st.action.wipeDir, (unsigned)st.action.wipeSpeedPct,
      (unsigned)st.area.x0, (unsigned)st.area.y0,
      (unsigned)st.area.x1, (unsigned)st.area.y1,
      (unsigned)st.durationS);
  }
  p += snprintf(p, end - p, "]}");
  int blen = (int)(p - jsonBuf);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(jsonBuf);
  c.flush();
}

// POST /api/runners — body: {"name":"..."}
void handlePostRunners(WiFiClient& c, int contentLen) {
  char body[32] = {};
  if (contentLen > 0 && contentLen < (int)sizeof(body))
    c.readBytes(body, contentLen);
  uint8_t slot = 0xFF;
  for (uint8_t i = 0; i < MAX_RUNNERS; i++) {
    if (!runners[i].inUse) { slot = i; break; }
  }
  if (slot == 0xFF) { sendJsonErr(c, "full"); return; }
  memset(&runners[slot], 0, sizeof(Runner));
  runners[slot].inUse = true;
  jsonGetStr(body, "name", runners[slot].name, RUNNER_NAME_LEN);
  if (runners[slot].name[0] == '\0')
    snprintf(runners[slot].name, RUNNER_NAME_LEN, "Runner %u", (unsigned)slot);
  char resp[32];
  int rlen = snprintf(resp, sizeof(resp), "{\"ok\":true,\"id\":%u}", (unsigned)slot);
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Content-Length: %d\r\n\r\n", rlen);
  c.print(resp);
  c.flush();
}

// ── Pre-computation algorithm (Phase 2e) ─────────────────────────────────────

// Direction unit vectors indexed by DIR_E/N/W/S (0/1/2/3)
// dx[DIR_E]=+1, dx[DIR_N]=0, dx[DIR_W]=-1, dx[DIR_S]=0
// dy[DIR_E]=0,  dy[DIR_N]=+1, dy[DIR_W]=0,  dy[DIR_S]=-1
static const int8_t DIR_DX[4] = {  1,  0, -1,  0 };
static const int8_t DIR_DY[4] = {  0,  1,  0, -1 };

void computeRunner(uint8_t id) {
  if (id >= MAX_RUNNERS || !runners[id].inUse) return;
  Runner& r = runners[id];

  // Clear all payloads to 0xFF (= string not affected)
  memset(r.payload, 0xFF, sizeof(r.payload));

  for (uint8_t s = 0; s < r.stepCount; s++) {
    RunnerStep& step = r.steps[s];

    // Area of effect in mm — integer arithmetic, no float
    int32_t axMin = ((int32_t)step.area.x0 * settings.canvasWidthMm)  / 10000;
    int32_t axMax = ((int32_t)step.area.x1 * settings.canvasWidthMm)  / 10000;
    int32_t ayMin = ((int32_t)step.area.y0 * settings.canvasHeightMm) / 10000;
    int32_t ayMax = ((int32_t)step.area.y1 * settings.canvasHeightMm) / 10000;

    for (uint8_t c = 0; c < MAX_CHILDREN; c++) {
      if (!children[c].inUse) continue;
      ChildStepPayload& pl = r.payload[s][c];

      for (uint8_t j = 0; j < children[c].stringCount && j < MAX_STR_PER_CHILD; j++) {
        StringInfo& str = children[c].strings[j];
        uint16_t lc = str.ledCount;
        if (lc == 0) continue;

        // String origin in mm: child position + cable offset
        uint8_t cd = str.cableDir & 3;
        int32_t sx = (int32_t)children[c].xMm + (int32_t)str.cableMm * DIR_DX[cd];
        int32_t sy = (int32_t)children[c].yMm + (int32_t)str.cableMm * DIR_DY[cd];

        uint8_t sd   = str.stripDir & 3;
        uint16_t lm  = str.lengthMm;
        int32_t  div = (lc > 1) ? (int32_t)(lc - 1) : 1;

        uint8_t first = 0xFF;
        uint8_t last  = 0xFF;

        for (uint16_t i = 0; i < lc; i++) {
          // LED position: multiply before divide to preserve integer precision
          int32_t lx = sx + (int32_t)i * lm * DIR_DX[sd] / div;
          int32_t ly = sy + (int32_t)i * lm * DIR_DY[sd] / div;

          if (lx >= axMin && lx <= axMax && ly >= ayMin && ly <= ayMax) {
            uint8_t idx = (i > 254) ? 254 : (uint8_t)i;  // clamp to uint8_t range
            if (first == 0xFF) first = idx;
            last = idx;
          }
        }
        pl.ledStart[j] = first;
        pl.ledEnd[j]   = last;
      }
    }
  }

  r.computed = true;
  if (Serial) { Serial.print(F("Runner computed: ")); Serial.println(id); }
}

// ── Runner sync / start / stop (Phase 2f) ────────────────────────────────────

void sendLoadStep(IPAddress dest, uint8_t stepIdx, uint8_t totalSteps,
                  const RunnerStep& step, const ChildStepPayload& pl) {
  UdpHeader hdr;
  hdr.magic   = UDP_MAGIC;
  hdr.version = UDP_VERSION;
  hdr.cmd     = CMD_LOAD_STEP;
  hdr.epoch   = (uint32_t)currentEpoch();

  LoadStepPayload ls;
  ls.stepIndex     = stepIdx;
  ls.totalSteps    = totalSteps;
  ls.actionType    = step.action.type;
  ls.r             = step.action.r;
  ls.g             = step.action.g;
  ls.b             = step.action.b;
  ls.onMs          = step.action.onMs;
  ls.offMs         = step.action.offMs;
  ls.wipeDir       = step.action.wipeDir;
  ls.wipeSpeedPct  = step.action.wipeSpeedPct;
  ls.durationS     = step.durationS;
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    ls.ledStart[j] = pl.ledStart[j];
    ls.ledEnd[j]   = pl.ledEnd[j];
  }
  memcpy(udpBuf,               &hdr, sizeof(hdr));
  memcpy(udpBuf + sizeof(hdr), &ls,  sizeof(ls));
  cmdUDP.beginPacket(dest, UDP_PORT);
  cmdUDP.write(udpBuf, sizeof(hdr) + sizeof(ls));
  cmdUDP.endPacket();
}

void syncRunner(uint8_t id) {
  if (id >= MAX_RUNNERS || !runners[id].inUse || !runners[id].computed) return;
  Runner& r = runners[id];
  for (uint8_t c = 0; c < MAX_CHILDREN; c++) {
    if (!children[c].inUse) continue;
    IPAddress dest(children[c].ip[0], children[c].ip[1],
                   children[c].ip[2], children[c].ip[3]);
    for (uint8_t s = 0; s < r.stepCount; s++) {
      sendLoadStep(dest, s, r.stepCount, r.steps[s], r.payload[s][c]);
      delay(5);
    }
  }
  if (Serial) { Serial.print(F("Runner synced: ")); Serial.println(id); }
}

void startRunner(uint8_t id) {
  if (id >= MAX_RUNNERS || !runners[id].inUse) return;
  uint32_t startEpoch = (uint32_t)currentEpoch() + 2;
  settings.activeRunner  = id;
  settings.runnerRunning = true;
  for (uint8_t c = 0; c < MAX_CHILDREN; c++) {
    if (!children[c].inUse) continue;
    IPAddress dest(children[c].ip[0], children[c].ip[1],
                   children[c].ip[2], children[c].ip[3]);
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_RUNNER_GO;
    hdr.epoch   = (uint32_t)currentEpoch();
    memcpy(udpBuf,               &hdr,        sizeof(hdr));
    memcpy(udpBuf + sizeof(hdr), &startEpoch, 4);
    cmdUDP.beginPacket(dest, UDP_PORT);
    cmdUDP.write(udpBuf, sizeof(hdr) + 4);
    cmdUDP.endPacket();
  }
  if (Serial) { Serial.print(F("Runner started: ")); Serial.println(id); }
}

void stopAllRunners() {
  settings.runnerRunning = false;
  settings.activeRunner  = 0xFF;
  for (uint8_t c = 0; c < MAX_CHILDREN; c++) {
    if (!children[c].inUse) continue;
    IPAddress dest(children[c].ip[0], children[c].ip[1],
                   children[c].ip[2], children[c].ip[3]);
    UdpHeader hdr;
    hdr.magic   = UDP_MAGIC;
    hdr.version = UDP_VERSION;
    hdr.cmd     = CMD_RUNNER_STOP;
    hdr.epoch   = (uint32_t)currentEpoch();
    memcpy(udpBuf, &hdr, sizeof(hdr));
    cmdUDP.beginPacket(dest, UDP_PORT);
    cmdUDP.write(udpBuf, sizeof(hdr));
    cmdUDP.endPacket();
  }
  if (Serial) Serial.println(F("All runners stopped."));
}

// Handle GET / PUT / DELETE / POST(compute/sync/start) /api/runners/:id
void handleRunnerIdRoute(WiFiClient& c, const char* req, bool isGet, bool isPut, bool isDel, int contentLen) {
  const char* idStart = strstr(req, "/api/runners/");
  if (!idStart) { sendJsonErr(c, "bad-route"); return; }
  idStart += 13;
  int id = atoi(idStart);
  if (id < 0 || id >= MAX_RUNNERS) { sendJsonErr(c, "bad-id"); return; }

  bool isCompute = (strstr(idStart, "/compute") != NULL);
  bool isSync    = (strstr(idStart, "/sync")    != NULL);
  bool isStart   = (strstr(idStart, "/start")   != NULL);

  if (isSync) {
    if (!runners[id].inUse || !runners[id].computed) { sendJsonErr(c, "not-computed"); return; }
    syncRunner((uint8_t)id);
    sendJsonOk(c);
  } else if (isStart) {
    if (!runners[id].inUse) { sendJsonErr(c, "not-found"); return; }
    startRunner((uint8_t)id);
    sendJsonOk(c);
  } else if (isCompute) {
    if (!runners[id].inUse) { sendJsonErr(c, "not-found"); return; }
    computeRunner((uint8_t)id);
    sendJsonOk(c);
  } else if (isDel) {
    if (!runners[id].inUse) { sendJsonErr(c, "not-found"); return; }
    memset(&runners[id], 0, sizeof(Runner));
    sendJsonOk(c);
  } else if (isPut) {
    if (!runners[id].inUse) { sendJsonErr(c, "not-found"); return; }
    // Read body
    static char body[2048];
    memset(body, 0, sizeof(body));
    int readLen = (contentLen < (int)sizeof(body) - 1) ? contentLen : (int)sizeof(body) - 1;
    if (readLen > 0) c.readBytes(body, readLen);

    // Update name if provided
    char nm[RUNNER_NAME_LEN] = {};
    jsonGetStr(body, "name", nm, sizeof(nm));
    if (nm[0] != '\0') strncpy(runners[id].name, nm, RUNNER_NAME_LEN - 1);

    // Parse steps array
    const char* sa = strstr(body, "\"steps\":[");
    if (sa) {
      sa += 9;
      uint8_t sc = 0;
      while (*sa && sc < MAX_STEPS) {
        // Skip to next '{'
        while (*sa && *sa != '{' && *sa != ']') sa++;
        if (*sa != '{') break;
        // Find matching '}'
        int depth = 0;
        const char* ep = sa;
        while (*ep) {
          if (*ep == '{') depth++;
          else if (*ep == '}') { depth--; if (depth == 0) { ep++; break; } }
          ep++;
        }
        // Extract step object
        char stepBuf[160] = {};
        int slen = (int)(ep - sa);
        if (slen > (int)sizeof(stepBuf) - 1) slen = sizeof(stepBuf) - 1;
        memcpy(stepBuf, sa, slen);

        RunnerStep& st = runners[id].steps[sc];
        st.action.type         = (uint8_t)jsonGetInt(stepBuf, "type", 0);
        st.action.r            = (uint8_t)jsonGetInt(stepBuf, "r",    0);
        st.action.g            = (uint8_t)jsonGetInt(stepBuf, "g",    0);
        st.action.b            = (uint8_t)jsonGetInt(stepBuf, "b",    0);
        st.action.onMs         = (uint16_t)jsonGetInt(stepBuf, "onMs",  500);
        st.action.offMs        = (uint16_t)jsonGetInt(stepBuf, "offMs", 500);
        st.action.wipeDir      = (uint8_t)jsonGetInt(stepBuf, "wdir", 0);
        st.action.wipeSpeedPct = (uint8_t)jsonGetInt(stepBuf, "wspd", 50);
        int x0 = jsonGetInt(stepBuf, "x0", 0);
        int y0 = jsonGetInt(stepBuf, "y0", 0);
        int x1 = jsonGetInt(stepBuf, "x1", 10000);
        int y1 = jsonGetInt(stepBuf, "y1", 10000);
        st.area.x0 = (uint16_t)(x0 < 0 ? 0 : (x0 > 10000 ? 10000 : x0));
        st.area.y0 = (uint16_t)(y0 < 0 ? 0 : (y0 > 10000 ? 10000 : y0));
        st.area.x1 = (uint16_t)(x1 < 0 ? 0 : (x1 > 10000 ? 10000 : x1));
        st.area.y1 = (uint16_t)(y1 < 0 ? 0 : (y1 > 10000 ? 10000 : y1));
        int dur = jsonGetInt(stepBuf, "dur", 5);
        st.durationS = (uint16_t)(dur < 1 ? 1 : (dur > 65535 ? 65535 : dur));

        sc++;
        sa = ep;
      }
      runners[id].stepCount = sc;
      runners[id].computed  = false;
    }
    sendJsonOk(c);
  } else {  // GET
    sendApiRunner(c, (uint8_t)id);
  }
}

#endif  // BOARD_GIGA

// ── Child UDP functions (ESP32 / D1 Mini only) ────────────────────────────────

#ifdef BOARD_FASTLED

void initChildConfig() {
  memset(&childCfg, 0, sizeof(childCfg));
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(childCfg.hostname, HOSTNAME_LEN, "SLYC-%02X%02X", mac[4], mac[5]);

  childCfg.stringCount         = 1;
  childCfg.strings[0].ledCount = NUM_LEDS;
  childCfg.strings[0].lengthMm = 500;
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].cableDir = DIR_E;
  childCfg.strings[0].cableMm  = 0;
  childCfg.strings[0].stripDir = DIR_E;

  if (Serial) { Serial.print(F("Child hostname: ")); Serial.println(childCfg.hostname); }
}

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
  // Compute current step from elapsed time
  resp.currentStep = 0;
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

#endif  // BOARD_FASTLED

// ── Shared UDP receive handler ────────────────────────────────────────────────

void handleUdpPacket(uint8_t cmd, IPAddress sender, uint8_t* payload, int plen) {
#ifdef BOARD_GIGA
  if (cmd == CMD_PONG && plen >= (int)sizeof(PongPayload)) {
    PongPayload pong;
    memcpy(&pong, payload, sizeof(pong));
    registerChild(sender, &pong);
  }
#else
  if (cmd == CMD_PING) {
    sendPong(sender);
  } else if (cmd == CMD_STATUS_REQ) {
    sendStatusResp(sender);
  } else if (cmd == CMD_ACTION && plen >= (int)sizeof(ActionPayload)) {
    ActionPayload ap;
    memcpy(&ap, payload, sizeof(ap));
    childActType  = ap.actionType;
    childActR     = ap.r;
    childActG     = ap.g;
    childActB     = ap.b;
    childActOnMs  = ap.onMs  ? ap.onMs  : 500;
    childActOffMs = ap.offMs ? ap.offMs : 500;
    childActWDir  = ap.wipeDir;
    childActWSpd  = ap.wipeSpeedPct ? ap.wipeSpeedPct : 50;
    for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
      childActSt[j] = ap.ledStart[j];
      childActEn[j] = ap.ledEnd[j];
    }
    childActSeq++;
  } else if (cmd == CMD_ACTION_STOP) {
    childActType = ACT_OFF;
    childActSeq++;
  } else if (cmd == CMD_LOAD_STEP && plen >= (int)sizeof(LoadStepPayload)) {
    LoadStepPayload ls;
    memcpy(&ls, payload, sizeof(ls));
    if (ls.stepIndex < MAX_CHILD_STEPS) {
      ChildRunnerStep& cr = childRunner[ls.stepIndex];
      cr.actionType   = ls.actionType;
      cr.r            = ls.r; cr.g = ls.g; cr.b = ls.b;
      cr.onMs         = ls.onMs;  cr.offMs = ls.offMs;
      cr.wipeDir      = ls.wipeDir;
      cr.wipeSpeedPct = ls.wipeSpeedPct;
      cr.durationS    = ls.durationS;
      for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
        cr.ledStart[j] = ls.ledStart[j];
        cr.ledEnd[j]   = ls.ledEnd[j];
      }
      if ((uint8_t)(ls.stepIndex + 1) > childStepCount)
        childStepCount = (uint8_t)(ls.stepIndex + 1);
      // Send ACK
      UdpHeader ack;
      ack.magic   = UDP_MAGIC;
      ack.version = UDP_VERSION;
      ack.cmd     = CMD_LOAD_ACK;
      ack.epoch   = (uint32_t)currentEpoch();
      memcpy(udpBuf,               &ack,            sizeof(ack));
      udpBuf[sizeof(ack)] = ls.stepIndex;
      cmdUDP.beginPacket(sender, UDP_PORT);
      cmdUDP.write(udpBuf, sizeof(ack) + 1);
      cmdUDP.endPacket();
    }
  } else if (cmd == CMD_RUNNER_GO && plen >= 4) {
    uint32_t startEpoch;
    memcpy(&startEpoch, payload, 4);
    childRunnerStart  = startEpoch;
    childRunnerArmed  = true;
    childRunnerActive = false;
  } else if (cmd == CMD_RUNNER_STOP) {
    childRunnerActive = false;
    childRunnerArmed  = false;
  }
  (void)plen;
#endif
}

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
  if (Serial) { Serial.print("Connecting to "); Serial.println(SECRET_SSID); }
#ifdef BOARD_FASTLED
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
#else
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

#ifdef BOARD_FASTLED
  initChildConfig();
#endif
}

// ── Status print (serial) ─────────────────────────────────────────────────────

void printStatus() {
  static unsigned long last = 0;
  if (millis() - last >= 3000) {
    last = millis();
    if (!Serial) return;
    Serial.print("IP: ");     Serial.print(WiFi.localIP());
    Serial.print("  WiFi: "); Serial.println(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
  }
}

// ── LED helpers (shared by ESP32 and D1 Mini) ─────────────────────────────────

#ifdef BOARD_FASTLED

// Apply one runner step to the leds[] array for all affected string ranges.
bool applyRunnerStep(const ChildRunnerStep& rs, uint8_t flashPh,
                     unsigned long stepMs) {
  bool drew = false;
  for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
    uint8_t st = rs.ledStart[j], en = rs.ledEnd[j];
    if (st == 0xFF) continue;
    if (en >= NUM_LEDS) en = NUM_LEDS - 1;
    if (st > en) continue;
    uint8_t rangeLen = en - st + 1;

    if (rs.actionType == ACT_SOLID) {
      for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(rs.r, rs.g, rs.b);
      drew = true;
    } else if (rs.actionType == ACT_FLASH) {
      if (!flashPh) {
        for (uint8_t i = st; i <= en; i++) leds[i] = CRGB(rs.r, rs.g, rs.b);
      }
      drew = true;
    } else if (rs.actionType == ACT_WIPE) {
      uint8_t spd = rs.wipeSpeedPct ? rs.wipeSpeedPct : 1;
      uint32_t front = (uint32_t)stepMs * spd * rangeLen / 100000UL;
      if (front > rangeLen) front = rangeLen;
      uint8_t dir = rs.wipeDir;
      for (uint8_t i = st; i <= en; i++) {
        uint8_t ri = i - st;
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((rangeLen - 1 - ri) < front)
                 : (ri < front);
        if (lit) leds[i] = CRGB(rs.r, rs.g, rs.b);
      }
      drew = true;
    }
  }
  return drew;
}

#endif  // BOARD_FASTLED

// ── LED implementation — ESP32 (FreeRTOS task, Core 0) ───────────────────────

#ifdef BOARD_ESP32

void ledTask(void* parameter) {
  // Immediate action state
  uint8_t       prevActSeq  = 0;
  unsigned long actStart    = 0;
  uint8_t       flashPhase  = 0;
  bool          offRendered = false;
  // Runner state
  uint8_t       prevRunStep = 0xFF;
  unsigned long stepStartMs = 0;
  uint8_t       runFlashPh  = 0;

  while (true) {
    // ── 1. Arm runner when epoch reached ───────────────────────────────────
    if (childRunnerArmed && childStepCount > 0) {
      if ((uint32_t)currentEpoch() >= childRunnerStart) {
        childRunnerArmed  = false;
        childRunnerActive = true;
        prevRunStep       = 0xFF;
      }
    }

    // ── 2. Execute runner ───────────────────────────────────────────────────
    if (childRunnerActive && childStepCount > 0) {
      uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
      uint8_t  curStep = 0;
      uint32_t acc     = 0;
      bool     done    = true;
      for (uint8_t i = 0; i < childStepCount; i++) {
        acc += childRunner[i].durationS;
        if (elapsed < acc) { curStep = i; done = false; break; }
      }
      if (done) {
        childRunnerActive = false;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
        delay(10);
        continue;
      }
      if (curStep != prevRunStep) {
        prevRunStep = curStep;
        stepStartMs = millis();
        runFlashPh  = 0;
      }
      const ChildRunnerStep& rs = childRunner[curStep];

      // Flash phase timer (shared across strings in same step)
      if (rs.actionType == ACT_FLASH) {
        unsigned long now = millis();
        uint16_t period = runFlashPh ? rs.offMs : rs.onMs;
        if (now - stepStartMs >= (unsigned long)period) {
          runFlashPh ^= 1;
          stepStartMs = now;
        }
      }
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      applyRunnerStep(rs, runFlashPh, millis() - stepStartMs);
      FastLED.show();
      delay(rs.actionType == ACT_FLASH ? 5 :
            rs.actionType == ACT_WIPE  ? 10 : 50);
      continue;  // skip immediate action
    }

    // ── 3. Immediate action ─────────────────────────────────────────────────
    uint8_t seq = childActSeq;
    if (seq != prevActSeq) {
      prevActSeq  = seq;
      actStart    = millis();
      flashPhase  = 0;
      offRendered = false;
    }
    uint8_t at = childActType;
    uint8_t r  = childActR, g = childActG, b = childActB;

    if (at == ACT_SOLID) {
      fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
      FastLED.show();
      delay(50);

    } else if (at == ACT_FLASH) {
      unsigned long now = millis();
      uint16_t period = flashPhase ? childActOffMs : childActOnMs;
      if (now - actStart >= (unsigned long)period) {
        flashPhase ^= 1;
        actStart    = now;
      }
      fill_solid(leds, NUM_LEDS, flashPhase ? CRGB::Black : CRGB(r, g, b));
      FastLED.show();
      delay(5);

    } else if (at == ACT_WIPE) {
      uint8_t spd = childActWSpd ? childActWSpd : 1;
      uint32_t front = (uint32_t)(millis() - actStart) * spd * NUM_LEDS / 100000UL;
      if (front > NUM_LEDS) front = NUM_LEDS;
      uint8_t dir = childActWDir;
      for (uint8_t i = 0; i < NUM_LEDS; i++) {
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((NUM_LEDS - 1 - i) < front)
                 : (i < front);
        leds[i] = lit ? CRGB(r, g, b) : CRGB::Black;
      }
      FastLED.show();
      delay(10);

    } else {  // ACT_OFF — render black once, then idle
      if (!offRendered) {
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
        offRendered = true;
      }
      delay(10);
    }
  }
}

#endif  // BOARD_ESP32

// ── LED implementation — D1 Mini (single-threaded, non-blocking) ──────────────

#ifdef BOARD_D1MINI

void updateLED() {
  // Immediate action state
  static uint8_t       prevActSeq   = 0;
  static unsigned long actStart     = 0;
  static uint8_t       flashPhase   = 0;
  static bool          actRendered  = false;
  // Runner state
  static uint8_t       prevRunStep  = 0xFF;
  static unsigned long stepStartMs  = 0;
  static uint8_t       runFlashPh   = 0;
  static uint8_t       lastSolidSt  = 0xFF;  // step index when solid was last rendered
  static unsigned long lastWipe     = 0;

  // ── 1. Arm runner when epoch reached ─────────────────────────────────────
  if (childRunnerArmed && childStepCount > 0) {
    if ((uint32_t)currentEpoch() >= childRunnerStart) {
      childRunnerArmed  = false;
      childRunnerActive = true;
      prevRunStep       = 0xFF;
      lastSolidSt       = 0xFF;
    }
  }

  // ── 2. Execute runner ─────────────────────────────────────────────────────
  if (childRunnerActive && childStepCount > 0) {
    uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
    uint8_t  curStep = 0;
    uint32_t acc     = 0;
    bool     done    = true;
    for (uint8_t i = 0; i < childStepCount; i++) {
      acc += childRunner[i].durationS;
      if (elapsed < acc) { curStep = i; done = false; break; }
    }
    if (done) {
      childRunnerActive = false;
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
      return;
    }
    if (curStep != prevRunStep) {
      prevRunStep = curStep;
      stepStartMs = millis();
      runFlashPh  = 0;
      lastSolidSt = 0xFF;
    }
    const ChildRunnerStep& rs = childRunner[curStep];

    if (rs.actionType == ACT_SOLID) {
      if (curStep != lastSolidSt) {
        lastSolidSt = curStep;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, 0, 0);
        FastLED.show();
      }
    } else if (rs.actionType == ACT_FLASH) {
      unsigned long now = millis();
      uint16_t period = runFlashPh ? rs.offMs : rs.onMs;
      if (now - stepStartMs >= (unsigned long)period) {
        runFlashPh ^= 1;
        stepStartMs = now;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, runFlashPh, 0);
        FastLED.show();
      }
    } else if (rs.actionType == ACT_WIPE) {
      unsigned long now = millis();
      if (now - lastWipe >= 20) {
        lastWipe = now;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        applyRunnerStep(rs, 0, now - stepStartMs);
        FastLED.show();
      }
    } else {  // ACT_OFF — black once per step
      if (curStep != lastSolidSt) {
        lastSolidSt = curStep;
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
      }
    }
    return;  // skip immediate action
  }

  // ── 3. Immediate action ───────────────────────────────────────────────────
  uint8_t seq = childActSeq;
  if (seq != prevActSeq) {
    prevActSeq  = seq;
    actStart    = millis();
    flashPhase  = 0;
    actRendered = false;
  }

  uint8_t at = childActType;
  uint8_t r  = childActR, g = childActG, b = childActB;

  if (at == ACT_SOLID) {
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB(r, g, b));
      FastLED.show();
      actRendered = true;
    }

  } else if (at == ACT_FLASH) {
    unsigned long now = millis();
    uint16_t period = flashPhase ? childActOffMs : childActOnMs;
    if (now - actStart >= (unsigned long)period) {
      flashPhase ^= 1;
      actStart = now;
      fill_solid(leds, NUM_LEDS, flashPhase ? CRGB::Black : CRGB(r, g, b));
      FastLED.show();
    }

  } else if (at == ACT_WIPE) {
    unsigned long now = millis();
    if (now - lastWipe >= 20) {
      lastWipe = now;
      uint8_t spd = childActWSpd ? childActWSpd : 1;
      uint32_t front = (uint32_t)(now - actStart) * spd * NUM_LEDS / 100000UL;
      if (front > NUM_LEDS) front = NUM_LEDS;
      uint8_t dir = childActWDir;
      for (uint8_t i = 0; i < NUM_LEDS; i++) {
        bool lit = (dir == DIR_W || dir == DIR_S)
                 ? ((NUM_LEDS - 1 - i) < front)
                 : (i < front);
        leds[i] = lit ? CRGB(r, g, b) : CRGB::Black;
      }
      FastLED.show();
    }

  } else {  // ACT_OFF — render black once
    if (!actRendered) {
      fill_solid(leds, NUM_LEDS, CRGB::Black);
      FastLED.show();
      actRendered = true;
    }
  }
}

#endif  // BOARD_D1MINI

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  if (Serial) Serial.println("=== BOOT ===");

#ifdef BOARD_GIGA
  memset(children, 0, sizeof(children));
  memset(runners,  0, sizeof(runners));
  memset(&settings, 0, sizeof(settings));
  settings.units          = 0;
  settings.darkMode       = 1;
  settings.canvasWidthMm  = 10000;
  settings.canvasHeightMm = 5000;
  strncpy(settings.parentName, "SlyLED Parent", sizeof(settings.parentName) - 1);
  settings.activeRunner  = 0xFF;
  settings.runnerRunning = false;

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

#ifdef BOARD_GIGA
  sendPing(IPAddress(255, 255, 255, 255));
#endif
}

void loop() {
  printStatus();
  pollUDP();

#ifdef BOARD_GIGA
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
