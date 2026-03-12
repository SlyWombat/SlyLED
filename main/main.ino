/*
 * SlyLED — Arduino Giga R1 WiFi
 * Module-based LED controller with WiFi web interface (SPA + JSON API).
 * No analogWrite (crashes Mbed on LED pins). Active-low: LOW = on.
 *
 * Routes:
 *   GET  /           — SPA main page (HTML + CSS + JS)
 *   GET  /status     — JSON module status (polled every 2 s by the SPA)
 *   POST /led/on     — enable Onboard LED rainbow, returns {"ok":true}
 *   POST /led/off    — disable Onboard LED rainbow, returns {"ok":true}
 *   GET  /log        — event log page (HTML)
 *
 * NOTE: All Serial prints are guarded with `if (Serial)` — on Mbed OS,
 * writing to USB CDC without a connected terminal blocks indefinitely.
 */

#include "version.h"
#include <WiFi.h>
#include <WiFiUDP.h>
#include <time.h>
#include "arduino_secrets.h"

// ── Pins & LED constants ──────────────────────────────────────────────────────

constexpr int     PIN_LEDR     = LEDR;
constexpr int     PIN_LEDG     = LEDG;
constexpr int     PIN_LEDB     = LEDB;
constexpr uint8_t HUE_STEP     = 2;
constexpr int     DISPLAY_MS   = 35;
constexpr int     PWM_CYCLE_US = 2048;
constexpr int     PWM_STEPS    = 256;
constexpr int     STEP_US      = PWM_CYCLE_US / PWM_STEPS;

// ── WiFi & server ─────────────────────────────────────────────────────────────

constexpr char HOSTNAME[] = "slyled";
WiFiServer server(80);

// ── Module state ──────────────────────────────────────────────────────────────

bool ledRainbowOn = true;  // Onboard LED — rainbow pattern active

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

enum LogSource : uint8_t { SRC_WEB = 0, SRC_BOOT = 1 };

struct LogEntry {
  unsigned long epoch;
  uint8_t       ip[4];
  bool          on;
  LogSource     source;
};

constexpr uint8_t MAX_LOG = 50;
LogEntry logBuf[MAX_LOG];
uint8_t  logCount = 0;
uint8_t  logNext  = 0;

void addLog(bool state, uint8_t src, uint8_t ip0, uint8_t ip1, uint8_t ip2, uint8_t ip3) {
  LogEntry& e = logBuf[logNext % MAX_LOG];
  e.epoch = currentEpoch();
  e.on = state; e.source = (LogSource)src;
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
  c.print(ledRainbowOn
    ? F("{\"onboard_led\":{\"active\":true}}")
    : F("{\"onboard_led\":{\"active\":false}}"));
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
            ".pattern-row{display:flex;align-items:center;justify-content:space-between;gap:.5em}"
            ".pattern-name{font-weight:bold;font-size:1em}"
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
  c.print(F("table{margin:1.5em auto;border-collapse:collapse}"
            "th,td{padding:.5em 1.2em;border:1px solid #444;text-align:left}"
            "th{background:#222}tr:nth-child(even){background:#1a1a1a}"
            "</style></head><body>"
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
            "<span class='badge boff' id='badge'>OFF</span>"
            "<button class='btn btn-on'  onclick='setLed(1)'>Enable</button>"
            "<button class='btn btn-off' onclick='setLed(0)'>Disable</button>"
            "</span></div></div></div>"
            "<div style='padding:0 1.5em'>"
            "<a class='btn btn-nav' href='/log'>View Log</a>"
            "</div>"));
  sendBuf(c, "<div class='footer'>v%d.%d</div>", APP_MAJOR, APP_MINOR);
  c.print(F("<script>"
            "function applyState(a){"
            "var b=document.getElementById('badge');"
            "b.textContent=a?'ON':'OFF';"
            "b.className='badge '+(a?'bon':'boff');"
            "var h=document.getElementById('hdr-status');"
            "h.textContent='Onboard LED - Rainbow '+(a?'ON':'OFF');"
            "h.style.color=a?'#4c4':'#c44';"
            "}"
            "function poll(){"
            "var x=new XMLHttpRequest();"
            "x.open('GET','/status',true);"
            "x.onload=function(){"
            "if(x.status===200){try{applyState(JSON.parse(x.responseText).onboard_led.active);}catch(e){}}"
            "};"
            "x.send();"
            "}"
            "function setLed(on){"
            "var x=new XMLHttpRequest();"
            "x.open('POST',on?'/led/on':'/led/off',true);"
            "x.onload=function(){"
            "if(x.status===200){try{if(JSON.parse(x.responseText).ok)applyState(on===1);}catch(e){}}"
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
    c.print(F("<table><tr><th>#</th><th>Timestamp</th><th>State</th><th>Source</th><th>IP</th></tr>"));
    uint8_t startIdx = (logNext - logCount + MAX_LOG * 2) % MAX_LOG;
    for (int8_t i = logCount - 1; i >= 0; i--) {
      uint8_t idx = (startIdx + i) % MAX_LOG;
      char ts[40];
      formatTime(logBuf[idx].epoch, ts, sizeof(ts));
      const char* color  = logBuf[idx].on ? "#4c4" : "#c44";
      const char* label  = logBuf[idx].on ? "ON"   : "OFF";
      const char* source = logBuf[idx].source == SRC_BOOT ? "Boot" : "Web";
      char ipStr[16];
      if (logBuf[idx].source == SRC_BOOT) {
        ipStr[0] = '-'; ipStr[1] = '\0';
      } else {
        snprintf(ipStr, sizeof(ipStr), "%u.%u.%u.%u",
                 logBuf[idx].ip[0], logBuf[idx].ip[1],
                 logBuf[idx].ip[2], logBuf[idx].ip[3]);
      }
      sendBuf(c, "<tr><td>%d</td><td>%s</td>"
                 "<td style='color:%s'><strong>%s</strong></td>"
                 "<td>%s</td><td>%s</td></tr>",
              logCount - i, ts, color, label, source, ipStr);
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
  } else if (strstr(req, " /led/on ")) {
    ledRainbowOn = true;
    addLog(true, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);
  } else if (strstr(req, " /led/off ")) {
    ledRainbowOn = false;
    digitalWrite(PIN_LEDR, HIGH);
    digitalWrite(PIN_LEDG, HIGH);
    digitalWrite(PIN_LEDB, HIGH);
    addLog(false, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);
  } else if (strstr(req, " /log ")) {
    sendLog(client);
  } else if (strstr(req, " /favicon.ico ")) {
    // Respond quickly so favicon doesn't occupy the connection slot on page load
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

// ── Status print (serial) ─────────────────────────────────────────────────────

void printStatus() {
  if (!Serial) return;
  static unsigned long last = 0;
  if (millis() - last >= 3000) {
    last = millis();
    Serial.print(F("IP: "));     Serial.print(WiFi.localIP());
    Serial.print(F("  WiFi: ")); Serial.print(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
    Serial.print(F("  LED: "));  Serial.println(ledRainbowOn ? "ON" : "OFF");
  }
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  { unsigned long t = millis(); while (!Serial && millis() - t < 3000); }
  if (Serial) Serial.println(F("=== BOOT ==="));

  pinMode(PIN_LEDR, OUTPUT); pinMode(PIN_LEDG, OUTPUT); pinMode(PIN_LEDB, OUTPUT);
  digitalWrite(PIN_LEDR, HIGH); digitalWrite(PIN_LEDG, HIGH); digitalWrite(PIN_LEDB, HIGH);

  connectWiFi();
  addLog(true, SRC_BOOT, 0, 0, 0, 0);
}

void loop() {
  printStatus();

  if (!ledRainbowOn) {
    handleClient();
    delay(10);
    return;
  }

  for (int hue = 0; hue < 256; hue += HUE_STEP) {
    if (!ledRainbowOn) break;
    uint8_t r, g, b;
    hueToRGB((uint8_t)hue, r, g, b);
    setRGBFor(r, g, b);
    handleClient();
  }
}
