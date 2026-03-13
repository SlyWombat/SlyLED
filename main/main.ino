/*
 * SlyLED — multi-board sketch
 *
 * Supported targets:
 *   Arduino Giga R1 WiFi  (arduino:mbed_giga:giga)    — onboard RGB, software PWM, Mbed RTOS thread
 *   ESP32 Dev Module      (esp32:esp32:esp32)           — WS2812B strip, FastLED, FreeRTOS task Core 0
 *   LOLIN D1 Mini         (esp8266:esp8266:d1_mini)    — WS2812B strip, FastLED, single-threaded loop
 *
 * Threading model:
 *   Giga / ESP32 : dedicated LED thread independent of WiFi — zero animation jitter
 *   D1 Mini      : non-blocking updateLED() called from loop() — minimal jitter
 *
 * Shared state: volatile bool ledRainbowOn / ledSirenOn
 *   Written by WiFi task/loop, read by LED task/function.
 *
 * Routes:
 *   GET  /              — SPA main page (HTML + CSS + JS)
 *   GET  /status        — JSON module status (polled every 2 s by the SPA)
 *   POST /led/on        — enable rainbow
 *   POST /led/siren/on  — enable siren (disables rainbow)
 *   POST /led/off       — disable all
 *   GET  /log           — event log page (HTML)
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

// Helper: boards that use FastLED (ESP32 and D1 Mini)
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
  // Active-low onboard RGB (LEDR/LEDG/LEDB macros provided by Giga core)
  constexpr uint8_t  HUE_STEP   = 2;    // hue advance per cycle (0–255 range)
  constexpr int      DISPLAY_MS = 35;   // ms per colour hold (~17 PWM cycles)
  constexpr uint16_t PWM_STEPS  = 256;
  constexpr uint8_t  STEP_US    = 8;    // µs per PWM step → 2.048 ms/cycle
  #define CARD_TITLE "Onboard LED"

#else  // FastLED boards (ESP32 / D1 Mini)
  #ifdef BOARD_D1MINI
    #define DATA_PIN  2   // GPIO2 — confirmed working in QuinLED reference sketch
  #else
    #define DATA_PIN  2   // GPIO2 on ESP32
  #endif
  #define NUM_LEDS      8
  #define LED_TYPE      WS2812B  // WS2812B timing; NEOPIXEL is an alias for this
  #define COLOR_ORDER   GRB
  constexpr uint8_t LED_BRIGHTNESS = 200;
  constexpr uint8_t RAINBOW_DELTA  = 256 / NUM_LEDS;  // hue spread per LED (32)
  constexpr int     RAINBOW_DELAY  = 20;               // ms per frame (~50 fps)
  CRGB leds[NUM_LEDS];
  #define CARD_TITLE "LED Strip"
#endif

// Shared timing constant
constexpr int SIREN_HALF_MS = 350;  // ms per colour phase

// ── WiFi & server ─────────────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";
WiFiServer server(80);

// ── Shared module state (volatile — written by WiFi path, read by LED path) ──

volatile bool ledRainbowOn = true;
volatile bool ledSirenOn   = false;

// ── Giga: LED thread handle (must be global so it outlives setup()) ───────────

#ifdef BOARD_GIGA
rtos::Thread ledThread;
#endif

// ── NTP ───────────────────────────────────────────────────────────────────────

WiFiUDP ntpUDP;
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

// Forward declaration — updateLED() is defined later but called from serveClient()
#ifdef BOARD_D1MINI
void updateLED();
#endif

// ── Web request handler ───────────────────────────────────────────────────────

void serveClient(WiFiClient& client, unsigned int waitMs) {
  unsigned long t = millis();
  // yield() keeps the ESP8266 WiFi stack fed during the wait
  while (!client.available() && millis() - t < waitMs) {
#ifdef BOARD_D1MINI
    updateLED();  // keep animation running while waiting for request data
#endif
    yield();
  }

  IPAddress remoteIP = client.remoteIP();
  uint8_t ip0 = remoteIP[0], ip1 = remoteIP[1], ip2 = remoteIP[2], ip3 = remoteIP[3];

  char req[128] = {};
  client.readBytesUntil('\n', req, sizeof(req) - 1);
  while (client.available()) client.read();  // drain remaining request headers

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
  } else {
    sendMain(client);
  }

  client.flush();
#ifdef BOARD_D1MINI
  // Give lwIP 200 ms to transmit all buffered response data and receive ACKs.
  // Without this, tcp_close() finds un-ACKed data and falls back to tcp_abort() → RST.
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
  serveClient(client, 100);  // shorter wait; ESP8266 clients send headers fast
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

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  Serial.print("Connecting to "); Serial.println(SECRET_SSID);
#ifdef BOARD_D1MINI
  WiFi.mode(WIFI_STA);      // force station mode; ESP8266 can default to AP+STA
  WiFi.hostname(HOSTNAME);  // ESP8266: hostname() not setHostname()
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
// Software PWM on active-low onboard RGB pins. Runs in a dedicated Mbed RTOS
// thread (ledThread) so animation is never interrupted by WiFi I/O.

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
// FastLED fill_rainbow / fill_solid. Pinned to Core 0 so WiFi on Core 1
// never causes a frame drop.

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
// Called from loop() on every iteration. Uses static state and millis() timing
// so it never blocks — WiFi requests are served between frames.

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
    if (prevSirenOn || hue != 0) {  // only update when state changes
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
  // Active-low onboard RGB — start HIGH (off), then launch LED thread
  pinMode(LEDR, OUTPUT); digitalWrite(LEDR, HIGH);
  pinMode(LEDG, OUTPUT); digitalWrite(LEDG, HIGH);
  pinMode(LEDB, OUTPUT); digitalWrite(LEDB, HIGH);
  ledThread.start(mbed::callback(ledTask));

#elif defined(BOARD_ESP32)
  // Initialise WS2812B strip; pin LED task to Core 0 (WiFi/loop on Core 1)
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
  xTaskCreatePinnedToCore(ledTask, "LED", 4096, NULL, 1, NULL, 0);

#else  // D1 Mini
  // Initialise WS2812B strip; updateLED() will be called from loop()
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
#endif

  connectWiFi();
  addLog(FEAT_RAINBOW, SRC_BOOT, 0, 0, 0, 0);
}

void loop() {
  printStatus();
#ifdef BOARD_D1MINI
  updateLED();
  handleClient();
  yield();
#else
  handleClient();
  delay(10);
#endif
}
