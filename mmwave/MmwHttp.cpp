/*
 * MmwHttp.cpp — hand-rolled HTTP on WiFiServer (fleet child pattern; the
 * ESP32 has no WiFiClient::print truncation quirk — that's Giga-only).
 * Zero dynamic allocation: fixed buffers, F() literals, chunked writes.
 */

#include <Arduino.h>
#include <WiFi.h>
#include "MmwConfig.h"
#include "MmwHttp.h"
#include "MmwNet.h"
#include "MmwProtocol.h"
#include "MmwUart.h"
#include "version.h"

static WiFiServer httpServer(80);
static bool serverUp = false;

// ── Page (PROGMEM; thin shell over /status.json) ─────────────────────────────
static const char PAGE[] PROGMEM =
  "<!DOCTYPE html><html><head><meta charset='utf-8'>"
  "<meta name='viewport' content='width=device-width,initial-scale=1'>"
  "<title>SlyLED MMwave</title><style>"
  "body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}"
  "h1{font-size:1.2rem;margin:0 0 4px}h2{font-size:.95rem;color:#94a3b8;margin:18px 0 6px}"
  ".c{max-width:560px;margin:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}"
  "td,th{padding:4px 8px;text-align:right;border-bottom:1px solid #1e293b}th{color:#94a3b8}"
  "td:first-child,th:first-child{text-align:left}"
  ".ok{color:#4ade80}.bad{color:#f87171}.stat{display:inline-block;margin-right:14px;color:#94a3b8}"
  ".stat b{color:#e2e8f0}input{background:#1e293b;color:#e2e8f0;border:1px solid #334155;"
  "border-radius:4px;padding:6px;width:100%;box-sizing:border-box;margin:2px 0 8px}"
  "button{background:#f59e0b;color:#0f172a;border:0;border-radius:4px;padding:8px 14px;font-weight:600}"
  "</style></head><body><div class='c'>"
  "<h1>SlyLED MMwave <span id='hn'></span></h1>"
  "<div><span class='stat'>radar <b id='hl'>?</b></span><span class='stat'>frames <b id='fr'>0</b></span>"
  "<span class='stat'>errs <b id='er'>0</b></span><span class='stat'>rssi <b id='rs'>?</b></span>"
  "<span class='stat'>up <b id='up'>0</b>s</span><span class='stat'>fw <b id='fw'></b></span></div>"
  "<h2>Targets</h2><table><thead><tr><th>#</th><th>x mm</th><th>y mm</th>"
  "<th>dist m</th><th>speed cm/s</th></tr></thead><tbody id='tb'>"
  "<tr><td colspan='5' style='text-align:center;color:#64748b'>none</td></tr></tbody></table>"
  "<h2>WiFi credentials (writes NVS, then reboots)</h2>"
  "<form method='POST' action='/wifi'>SSID<input name='ssid' maxlength='32' required>"
  "Password<input name='pass' type='password' maxlength='64' required>"
  "<button>Save &amp; reboot</button></form>"
  "<script>async function t(){try{const r=await fetch('/status.json');const s=await r.json();"
  "hn.textContent=s.hostname;fw.textContent=s.fw;fr.textContent=s.frames;er.textContent=s.errs;"
  "rs.textContent='-'+s.rssi+' dBm';up.textContent=s.uptimeS;"
  "hl.textContent=s.healthy?'OK':'STALE';hl.className=s.healthy?'ok':'bad';"
  "tb.innerHTML=s.targets.length?s.targets.map((p,i)=>'<tr><td>'+(i+1)+'</td><td>'+p.x+'</td><td>'+p.y+"
  "'</td><td>'+(Math.hypot(p.x,p.y)/1000).toFixed(2)+'</td><td>'+p.speed+'</td></tr>').join('')"
  ":'<tr><td colspan=5 style=text-align:center;color:#64748b>none</td></tr>'}catch(e){}}"
  "setInterval(t,500);t()</script></div></body></html>";

// ── Helpers ───────────────────────────────────────────────────────────────────
static void sendHeader(WiFiClient& c, const __FlashStringHelper* type, int code) {
  c.print(F("HTTP/1.1 ")); c.print(code); c.println(code == 200 ? F(" OK") : F(" See Other"));
  c.print(F("Content-Type: ")); c.println(type);
  c.println(F("Connection: close"));
  c.println();
}

// %xx + '+' decode in place (WiFi creds form)
static void urlDecode(char* s) {
  char* w = s;
  for (char* r = s; *r; r++) {
    if (*r == '+') { *w++ = ' '; }
    else if (*r == '%' && r[1] && r[2]) {
      auto hex = [](char h) -> int {
        if (h >= '0' && h <= '9') return h - '0';
        if (h >= 'a' && h <= 'f') return h - 'a' + 10;
        if (h >= 'A' && h <= 'F') return h - 'A' + 10;
        return 0;
      };
      *w++ = (char)((hex(r[1]) << 4) | hex(r[2]));
      r += 2;
    } else { *w++ = *r; }
  }
  *w = '\0';
}

// Extract form field `key=` from urlencoded body into out (decoded).
static bool formField(const char* body, const char* key, char* out, size_t outLen) {
  size_t klen = strlen(key);
  const char* p = body;
  while (p && *p) {
    if (strncmp(p, key, klen) == 0 && p[klen] == '=') {
      p += klen + 1;
      size_t i = 0;
      while (*p && *p != '&' && i < outLen - 1) out[i++] = *p++;
      out[i] = '\0';
      urlDecode(out);
      return true;
    }
    p = strchr(p, '&');
    if (p) p++;
  }
  return false;
}

static void sendStatusJson(WiFiClient& c) {
  MmwTarget t[MMW_MAX_TARGETS];
  uint8_t count = mmwUartTargets(t, nullptr);
  char buf[512];
  int n = snprintf(buf, sizeof(buf),
    "{\"hostname\":\"%s\",\"fw\":\"%d.%d.%d\",\"ip\":\"%s\",\"rssi\":%u,"
    "\"uptimeS\":%lu,\"frames\":%lu,\"bytes\":%lu,\"errs\":%lu,\"healthy\":%s,"
    "\"targets\":[",
    mmwHostname(), MMW_MAJOR, MMW_MINOR, MMW_PATCH,
    WiFi.localIP().toString().c_str(), (unsigned)mmwRssiAbs(),
    (unsigned long)(millis() / 1000), (unsigned long)mmwUartFrameCount(),
    (unsigned long)mmwUartByteCount(), (unsigned long)mmwUartErrorCount(),
    mmwUartHealthy() ? "true" : "false");
  for (uint8_t i = 0; i < count && n < (int)sizeof(buf) - 80; i++) {
    n += snprintf(buf + n, sizeof(buf) - n, "%s{\"x\":%d,\"y\":%d,\"speed\":%d,\"res\":%u}",
                  i ? "," : "", t[i].xMm, t[i].yMm, t[i].speedCms, t[i].resMm);
  }
  n += snprintf(buf + n, sizeof(buf) - n, "]}");
  sendHeader(c, F("application/json"), 200);
  c.write((const uint8_t*)buf, n);
}

// ── Public API ────────────────────────────────────────────────────────────────
void mmwHttpBegin() {
  if (serverUp || !mmwNetConnected()) return;
  httpServer.begin();
  serverUp = true;
  if (Serial) { Serial.print(F("MMW: HTTP up at http://")); Serial.println(WiFi.localIP()); }
}

void mmwHttpPoll() {
  if (!serverUp) { mmwHttpBegin(); return; }   // WiFi may join after boot
  WiFiClient client = httpServer.accept();
  if (!client) return;

  char req[128]  = {0};   // request line
  char body[224] = {0};   // POST body (ssid=32 + pass=64, urlencoded ×3 margin)
  uint32_t start = millis();
  size_t ri = 0;
  int contentLen = 0;
  // Request line
  while (client.connected() && millis() - start < 1000) {
    if (!client.available()) { delay(1); continue; }
    char ch = (char)client.read();
    if (ch == '\n') break;
    if (ch != '\r' && ri < sizeof(req) - 1) req[ri++] = ch;
  }
  // Headers (only Content-Length matters)
  char line[96]; size_t li = 0;
  while (client.connected() && millis() - start < 1500) {
    if (!client.available()) { delay(1); continue; }
    char ch = (char)client.read();
    if (ch == '\n') {
      if (li == 0) break;                       // blank line = end of headers
      line[li] = '\0'; li = 0;
      if (strncasecmp(line, "Content-Length:", 15) == 0) contentLen = atoi(line + 15);
    } else if (ch != '\r' && li < sizeof(line) - 1) line[li++] = ch;
  }

  if (strncmp(req, "GET /status.json", 16) == 0) {
    sendStatusJson(client);
  } else if (strncmp(req, "POST /wifi", 10) == 0) {
    if (contentLen > 0 && contentLen < (int)sizeof(body)) {
      int bi = 0;
      while (bi < contentLen && client.connected() && millis() - start < 3000) {
        if (!client.available()) { delay(1); continue; }
        body[bi++] = (char)client.read();
      }
      body[bi] = '\0';
      char ssid[33], pass[65];
      if (formField(body, "ssid", ssid, sizeof(ssid)) &&
          formField(body, "pass", pass, sizeof(pass)) && ssid[0]) {
        mmwSaveWiFiCredentials(ssid, pass);
        sendHeader(client, F("text/html"), 200);
        client.print(F("<meta charset='utf-8'>Saved. Rebooting — reconnect on the new network."));
        client.flush();
        delay(300);
        ESP.restart();
      }
    }
    sendHeader(client, F("text/html"), 200);
    client.print(F("Bad form data — nothing saved."));
  } else {   // GET / and anything else → the page
    sendHeader(client, F("text/html"), 200);
    // Chunked flash writes; ESP32 WiFiClient handles arbitrary sizes.
    client.print((const __FlashStringHelper*)PAGE);
  }
  client.flush();
  delay(1);
  client.stop();
}
