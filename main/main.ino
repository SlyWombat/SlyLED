/*
 * Rainbow Cycle — Arduino Giga R1 WiFi
 * Smooth rainbow on the onboard RGB LED using software PWM only.
 * No analogWrite (crashes Mbed on LED pins). Active-low: LOW = on.
 * WiFi web interface at http://slyled (port 80).
 *
 * NOTE: All Serial prints are guarded with `if (Serial)` because on Mbed OS
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
bool rainbowOn = true;

// ── NTP ───────────────────────────────────────────────────────────────────────

WiFiUDP ntpUDP;
unsigned long ntpEpoch  = 0;  // Unix epoch at time of NTP sync
unsigned long ntpMillis = 0;  // millis() at time of NTP sync

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
  if (ntpEpoch == 0 && Serial) Serial.println(F("NTP sync failed — timestamps will be relative."));
}

unsigned long currentEpoch() {
  if (ntpEpoch == 0) return millis() / 1000;
  return ntpEpoch + (millis() - ntpMillis) / 1000;
}

// Format as "YYYY-MM-DD HH:MM:SS UTC" or "T+Ns" if NTP unavailable
void formatTime(unsigned long epoch, char* buf, uint8_t len) {
  if (ntpEpoch == 0) {
    snprintf(buf, len, "T+%lus", epoch);
    return;
  }
  time_t t = (time_t)epoch;
  struct tm* ti = gmtime(&t);
  strftime(buf, len, "%Y-%m-%d %H:%M:%S UTC", ti);
}

// ── Event log ─────────────────────────────────────────────────────────────────

// Source of the state change
enum LogSource : uint8_t { SRC_WEB = 0, SRC_BOOT = 1 };

struct LogEntry {
  unsigned long epoch;
  bool          on;
  LogSource     source;
};

constexpr uint8_t MAX_LOG = 50;
LogEntry logBuf[MAX_LOG];
uint8_t  logCount = 0;
uint8_t  logNext  = 0;  // next write index (circular)

void addLog(bool state, LogSource src = SRC_WEB) {
  logBuf[logNext % MAX_LOG] = { currentEpoch(), state, src };
  logNext++;
  if (logCount < MAX_LOG) logCount++;
}

// ── HTTP response helpers ─────────────────────────────────────────────────────

// Single buffered write to reduce TCP packet count
char _txbuf[256];

void sendBuf(WiFiClient& c, const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(_txbuf, sizeof(_txbuf), fmt, ap);
  va_end(ap);
  c.print(_txbuf);
}

void sendPageHeader(WiFiClient& c, const char* title) {
  c.print(F("HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-cache, no-store\r\n"
            "\r\n"
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"));
  c.print(F("<meta name='viewport' content='width=device-width,initial-scale=1'>"));
  c.print(F("<title>")); c.print(title); c.print(F("</title><style>"));
  c.print(F("body{font-family:sans-serif;text-align:center;padding:2em;"
            "background:#111;color:#eee;margin:0}"));
  c.print(F("h1{font-size:2.2em;margin-bottom:.1em}"));
  c.print(F("h2{font-weight:normal;color:#aaa;margin-top:0}"));
  c.print(F(".btn{display:inline-block;padding:.7em 2em;margin:.4em;"
            "border-radius:8px;text-decoration:none;font-size:1.1em;font-weight:bold;"
            "border:none;cursor:pointer;font-family:inherit}"));
  c.print(F(".btn:hover{opacity:.8}"));
  c.print(F(".on{background:#2a2;color:#fff}"
            ".off{background:#a22;color:#fff}"
            ".nav{background:#446;color:#fff}"));
  c.print(F("table{margin:1.5em auto;border-collapse:collapse}"));
  c.print(F("th,td{padding:.5em 1.2em;border:1px solid #444;text-align:left}"));
  c.print(F("th{background:#222}tr:nth-child(even){background:#1a1a1a}"));
  c.print(F("</style></head><body>"));
}

void sendMain(WiFiClient& c) {
  sendPageHeader(c, "SlyLED");
  c.print(F("<h1>SlyLED</h1>"));
  const char* stateCol = rainbowOn ? "#4c4" : "#c44";
  const char* stateStr = rainbowOn ? "ON"   : "OFF";
  sendBuf(c, "<p style='font-size:1.4em;margin:1em 0'>Rainbow is "
             "<strong style='color:%s'>%s</strong></p>", stateCol, stateStr);
  c.print(F("<form method='post' action='/on'  style='display:inline'>"
            "<button class='btn on'  type='submit'>Turn On</button></form>"));
  c.print(F("<form method='post' action='/off' style='display:inline'>"
            "<button class='btn off' type='submit'>Turn Off</button></form>"));
  c.print(F("<br><br><a class='btn nav' href='/log'>View Log</a>"));
  sendBuf(c, "<p style='font-size:.75em;color:#555;margin-top:3em'>v%d.%d</p>",
          APP_MAJOR, APP_MINOR);
  c.print(F("</body></html>"));
}

void sendLog(WiFiClient& c) {
  sendPageHeader(c, "SlyLED - Log");
  c.print(F("<h1>SlyLED</h1><h2>Event Log</h2>"));
  if (logCount == 0) {
    c.print(F("<p style='color:#888'>No events recorded yet.</p>"));
  } else {
    c.print(F("<table><tr><th>#</th><th>Timestamp</th><th>State</th><th>Source</th></tr>"));
    uint8_t startIdx = (logNext - logCount + MAX_LOG * 2) % MAX_LOG;
    for (int8_t i = logCount - 1; i >= 0; i--) {
      uint8_t idx = (startIdx + i) % MAX_LOG;
      char ts[40];
      formatTime(logBuf[idx].epoch, ts, sizeof(ts));
      const char* color = logBuf[idx].on ? "#4c4" : "#c44";
      const char* label  = logBuf[idx].on ? "ON"   : "OFF";
      const char* source = logBuf[idx].source == SRC_BOOT ? "Boot" : "Web";
      sendBuf(c, "<tr><td>%d</td><td>%s</td>"
                 "<td style='color:%s'><strong>%s</strong></td>"
                 "<td>%s</td></tr>",
              logCount - i, ts, color, label, source);
    }
    c.print(F("</table>"));
    c.flush();
  }
  c.print(F("<br><a class='btn nav' href='/'>Back</a></body></html>"));
}

// ── Web request handler ───────────────────────────────────────────────────────

void handleClient() {
  WiFiClient client = server.available();
  if (!client) return;

  // Wait up to 500 ms for the HTTP request line to arrive after TCP connect.
  unsigned long t = millis();
  while (!client.available() && millis() - t < 500);

  char req[128] = {};
  client.readBytesUntil('\n', req, sizeof(req) - 1);
  client.flush();

  if (strstr(req, " /on ")) {
    rainbowOn = true;
    addLog(true);
  } else if (strstr(req, " /off ")) {
    rainbowOn = false;
    digitalWrite(PIN_LEDR, HIGH);
    digitalWrite(PIN_LEDG, HIGH);
    digitalWrite(PIN_LEDB, HIGH);
    addLog(false);
  }

  if (strstr(req, " /log ")) {
    sendLog(client);
  } else {
    sendMain(client);
  }

  client.flush();
  delay(5);
  client.stop();
}

// ── WiFi connect ──────────────────────────────────────────────────────────────

void connectWiFi() {
  if (Serial) { Serial.print(F("Connecting to ")); Serial.println(SECRET_SSID); }
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
  WiFi.setHostname(HOSTNAME);
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
  if      (hue < 43)  { r = 255;             g = hue * 6;        b = 0;              }
  else if (hue < 85)  { r = 255-(hue-43)*6;  g = 255;            b = 0;              }
  else if (hue < 128) { r = 0;               g = 255;            b = (hue-85)*6;     }
  else if (hue < 170) { r = 0;               g = 255-(hue-128)*6;b = 255;            }
  else if (hue < 213) { r = (hue-170)*6;     g = 0;              b = 255;            }
  else                { r = 255;             g = 0;              b = 255-(hue-213)*6;}
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
    Serial.print(F("IP: "));    Serial.print(WiFi.localIP());
    Serial.print(F("  WiFi: ")); Serial.print(WiFi.status() == WL_CONNECTED ? "OK" : "DISCONNECTED");
    Serial.print(F("  Rainbow: ")); Serial.println(rainbowOn ? "ON" : "OFF");
  }
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  { unsigned long t = millis(); while (!Serial && millis() - t < 3000); }
  if (Serial) Serial.println(F("=== BOOT ==="));

  pinMode(PIN_LEDR, OUTPUT);
  pinMode(PIN_LEDG, OUTPUT);
  pinMode(PIN_LEDB, OUTPUT);
  digitalWrite(PIN_LEDR, HIGH);
  digitalWrite(PIN_LEDG, HIGH);
  digitalWrite(PIN_LEDB, HIGH);

  connectWiFi();
  addLog(true, SRC_BOOT);  // record initial ON state
}

void loop() {
  printStatus();

  if (!rainbowOn) {
    handleClient();
    delay(10);
    return;
  }

  for (int hue = 0; hue < 256; hue += HUE_STEP) {
    if (!rainbowOn) break;
    uint8_t r, g, b;
    hueToRGB((uint8_t)hue, r, g, b);
    setRGBFor(r, g, b);
    handleClient();
  }
}
