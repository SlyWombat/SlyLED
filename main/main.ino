/*
 * SlyLED — Arduino Giga R1 WiFi
 * Module-based LED controller with WiFi web interface (SPA + JSON API).
 *
 * Two-thread architecture (Mbed RTOS on M7):
 *   LED thread  — pure animation, no WiFi calls, no blanks or stalls
 *   Main thread — pure WiFi / HTTP, no LED pin writes
 *
 * Shared state: volatile bool ledRainbowOn / ledSirenOn
 *   Written by main thread (serveClient), read by LED thread.
 *   Bool assignments are atomic on ARM Cortex-M7; volatile prevents
 *   compiler optimisation from caching the value in a register.
 *
 * Routes:
 *   GET  /              — SPA main page (HTML + CSS + JS)
 *   GET  /status        — JSON module status (polled every 2 s by the SPA)
 *   POST /led/on        — enable rainbow
 *   POST /led/siren/on  — enable siren (disables rainbow)
 *   POST /led/off       — disable all onboard LED features
 *   GET  /log           — event log page (HTML)
 *
 * NOTE: All Serial prints are guarded with `if (Serial)` — on Mbed OS,
 * writing to USB CDC without a connected terminal blocks indefinitely.
 */

#include "version.h"
#include <mbed.h>
#include <WiFi.h>
#include <WiFiUDP.h>
#include <time.h>
#include "arduino_secrets.h"

// ── Pins & LED constants ──────────────────────────────────────────────────────

constexpr int     PIN_LEDR      = LEDR;
constexpr int     PIN_LEDG      = LEDG;
constexpr int     PIN_LEDB      = LEDB;
constexpr uint8_t HUE_STEP      = 2;
constexpr int     DISPLAY_MS    = 35;
constexpr int     PWM_CYCLE_US  = 2048;
constexpr int     PWM_STEPS     = 256;
constexpr int     STEP_US       = PWM_CYCLE_US / PWM_STEPS;
constexpr int     SIREN_HALF_MS = 350;   // ms per colour in siren flash

// ── WiFi & server ─────────────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";
WiFiServer server(80);

// ── Shared module state (volatile — written by WiFi thread, read by LED thread) ──

volatile bool ledRainbowOn = true;
volatile bool ledSirenOn   = false;

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
      if (Serial) { Serial.print(F("NTP synced. Epoch: ")); Serial.println(ntpEpoch); }
      break;
    }
    delay(10);
  }
  ntpUDP.stop();
  if (ntpEpoch == 0 && Serial) Serial.println(F("NTP sync failed."));
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
  uint8_t       feature;   // LedFeature — what was turned on (FEAT_NONE = off)
  LogSource     source;
};

constexpr uint8_t MAX_LOG = 50;
LogEntry logBuf[MAX_LOG];
uint8_t  logCount = 0;
uint8_t  logNext  = 0;

// src and feat are uint8_t (not enum) to avoid Arduino auto-prototype breakage
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
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{\"ok\":true}"));
  c.flush();
}

void sendStatus(WiFiClient& c) {
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-cache, no-store\r\n"
            "\r\n"));
  const char* feat   = ledRainbowOn ? "rainbow" : (ledSirenOn ? "siren" : "none");
  const char* active = (ledRainbowOn || ledSirenOn) ? "true" : "false";
  sendBuf(c, "{\"onboard_led\":{\"active\":%s,\"feature\":\"%s\"}}", active, feat);
  c.flush();
}

// ── Main SPA page ─────────────────────────────────────────────────────────────

void sendMain(WiFiClient& c) {
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-cache, no-store\r\n"
            "\r\n"
            "<!DOCTYPE html><html><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SlyLED</title><style>"));
  c.print(F("*{box-sizing:border-box;margin:0;padding:0}"
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
            ".footer{padding:1em 2em;font-size:.72em;color:#444}"));
  c.print(F("</style></head><body>"
            "<div id='hdr'>"
            "<h1>SlyLED</h1>"
            "<div id='hdr-status'>Connecting...</div>"
            "</div>"
            "<div class='modules'>"
            "<div class='card'>"
            "<div class='card-title'>Onboard LED</div>"
            "<div class='pattern-row'>"
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
            "</div>"));
  sendBuf(c, "<div class='footer'>v%d.%d</div>", APP_MAJOR, APP_MINOR);
  c.print(F("<script>"
            "function applyState(d){"
            "var f=d.onboard_led.feature;"
            "var br=document.getElementById('badge-rainbow');"
            "br.textContent=f==='rainbow'?'ON':'OFF';"
            "br.className='badge '+(f==='rainbow'?'bon':'boff');"
            "var bs=document.getElementById('badge-siren');"
            "bs.textContent=f==='siren'?'ON':'OFF';"
            "bs.className='badge '+(f==='siren'?'bon':'boff');"
            "var h=document.getElementById('hdr-status');"
            "if(f==='rainbow'){h.textContent='Onboard LED - Rainbow ON';h.style.color='#4c4';}"
            "else if(f==='siren'){h.textContent='Onboard LED - Siren ON';h.style.color='#48f';}"
            "else{h.textContent='Onboard LED - OFF';h.style.color='#c44';}"
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
            "</script></body></html>"));
}

// ── Log page ──────────────────────────────────────────────────────────────────

void sendLog(WiFiClient& c) {
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-cache, no-store\r\n"
            "\r\n"
            "<!DOCTYPE html><html><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SlyLED - Log</title><style>"));
  c.print(F("body{font-family:sans-serif;text-align:center;padding:2em;"
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
            "<h1>SlyLED</h1><h2>Event Log</h2>"));
  if (logCount == 0) {
    c.print(F("<p style='color:#888'>No events recorded yet.</p>"));
  } else {
    c.print(F("<table><tr><th>#</th><th>Timestamp</th><th>Feature</th>"
              "<th>State</th><th>Source</th><th>IP</th></tr>"));
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
    c.print(F("</table>"));
    c.flush();
  }
  c.print(F("<br><a class='btn-nav' href='/'>Back</a></body></html>"));
}

// ── Web request handler ───────────────────────────────────────────────────────

void serveClient(WiFiClient& client, unsigned int waitMs) {
  unsigned long t = millis();
  while (!client.available() && millis() - t < waitMs);

  IPAddress remoteIP = client.remoteIP();
  uint8_t ip0 = remoteIP[0], ip1 = remoteIP[1], ip2 = remoteIP[2], ip3 = remoteIP[3];

  char req[128] = {};
  client.readBytesUntil('\n', req, sizeof(req) - 1);
  client.flush();

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
    client.print(F("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"));
    client.flush();
  } else {
    sendMain(client);
  }

  client.flush();
  delay(5);
  client.stop();
}

void handleClient() {
  WiFiClient client = server.available();
  if (!client) return;

  serveClient(client, 500);

  // Drain any additional parallel connections the browser opened simultaneously
  // (e.g. favicon, XHR poll). Without draining these accumulate in the server
  // queue and are served stale on the next button press.
  delay(20);
  while ((client = server.available())) {
    serveClient(client, 100);
  }
}

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  if (Serial) { Serial.print(F("Connecting to ")); Serial.println(SECRET_SSID); }
  WiFi.setHostname(HOSTNAME);  // before begin() so hostname appears in DHCP requests
  WiFi.begin(SECRET_SSID, SECRET_PASS);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t > 20000) {
      if (Serial) Serial.println(F("\r\nWiFi timeout."));
      return;
    }
    delay(500);
    if (Serial) Serial.print('.');
  }
  if (Serial) {
    Serial.println();
    Serial.print(F("Connected. IP: "));
    Serial.println(WiFi.localIP());
  }
  server.begin();
  syncNTP();
}

// ── LED helpers ───────────────────────────────────────────────────────────────

void hueToRGB(uint8_t hue, uint8_t& r, uint8_t& g, uint8_t& b) {
  if      (hue < 43)  { r = 255;             g = hue * 6;         b = 0;               }
  else if (hue < 85)  { r = 255-(hue-43)*6;  g = 255;             b = 0;               }
  else if (hue < 128) { r = 0;               g = 255;             b = (hue-85)*6;      }
  else if (hue < 170) { r = 0;               g = 255-(hue-128)*6; b = 255;             }
  else if (hue < 213) { r = (hue-170)*6;     g = 0;               b = 255;             }
  else                { r = 255;             g = 0;               b = 255-(hue-213)*6; }
}

void pwmCycle(uint8_t r, uint8_t g, uint8_t b) {
  for (int step = 0; step < PWM_STEPS; step++) {
    digitalWrite(PIN_LEDR, r > step ? LOW : HIGH);
    digitalWrite(PIN_LEDG, g > step ? LOW : HIGH);
    digitalWrite(PIN_LEDB, b > step ? LOW : HIGH);
    delayMicroseconds(STEP_US);
  }
}

void setRGBFor(uint8_t r, uint8_t g, uint8_t b) {
  unsigned long start = millis();
  while (millis() - start < (unsigned long)DISPLAY_MS) {
    pwmCycle(r, g, b);
  }
}

// ── LED thread — runs independently of WiFi ───────────────────────────────────

rtos::Thread ledThread;

void ledTask() {
  uint8_t       sirenPhase      = 0;
  unsigned long sirenPhaseStart = 0;
  bool          prevSirenOn     = false;

  while (true) {
    bool siren   = ledSirenOn;
    bool rainbow = ledRainbowOn;

    if (siren) {
      // Reset phase timing whenever siren is first activated
      if (!prevSirenOn) {
        sirenPhase      = 0;
        sirenPhaseStart = millis();
        digitalWrite(PIN_LEDR, LOW);
        digitalWrite(PIN_LEDG, HIGH);
        digitalWrite(PIN_LEDB, HIGH);
      }
      prevSirenOn = true;

      unsigned long now = millis();
      if (now - sirenPhaseStart >= (unsigned long)SIREN_HALF_MS) {
        sirenPhase ^= 1;
        sirenPhaseStart = now;
        if (sirenPhase == 0) {
          digitalWrite(PIN_LEDR, LOW);   // red
          digitalWrite(PIN_LEDG, HIGH);
          digitalWrite(PIN_LEDB, HIGH);
        } else {
          digitalWrite(PIN_LEDR, HIGH);
          digitalWrite(PIN_LEDG, HIGH);
          digitalWrite(PIN_LEDB, LOW);   // blue
        }
      }
      delay(5);

    } else if (rainbow) {
      prevSirenOn = false;
      for (int hue = 0; hue < 256; hue += HUE_STEP) {
        if (!ledRainbowOn || ledSirenOn) break;
        uint8_t r, g, b;
        hueToRGB((uint8_t)hue, r, g, b);
        setRGBFor(r, g, b);
      }

    } else {
      prevSirenOn = false;
      digitalWrite(PIN_LEDR, HIGH);
      digitalWrite(PIN_LEDG, HIGH);
      digitalWrite(PIN_LEDB, HIGH);
      delay(10);
    }
  }
}

// ── Status print (serial) ─────────────────────────────────────────────────────

void printStatus() {
  if (!Serial) return;
  static unsigned long last = 0;
  if (millis() - last >= 3000) {
    last = millis();
    Serial.print(F("IP: "));     Serial.print(WiFi.localIP());
    Serial.print(F("  WiFi: ")); Serial.print(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
    const char* feat = ledRainbowOn ? "Rainbow" : (ledSirenOn ? "Siren" : "OFF");
    Serial.print(F("  LED: "));  Serial.println(feat);
  }
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  { unsigned long t = millis(); while (!Serial && millis() - t < 3000); }
  if (Serial) Serial.println(F("=== BOOT ==="));

  pinMode(PIN_LEDR, OUTPUT); pinMode(PIN_LEDG, OUTPUT); pinMode(PIN_LEDB, OUTPUT);
  digitalWrite(PIN_LEDR, HIGH); digitalWrite(PIN_LEDG, HIGH); digitalWrite(PIN_LEDB, HIGH);

  // Start LED animation thread before WiFi so the LED is active during connect
  ledThread.start(ledTask);

  connectWiFi();
  addLog(FEAT_RAINBOW, SRC_BOOT, 0, 0, 0, 0);
}

void loop() {
  printStatus();
  handleClient();
  delay(10);
}
