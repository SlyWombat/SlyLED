/*
 * Child.cpp — Child node (ESP32 / D1 Mini) data, EEPROM config,
 *             UDP responses, HTTP config page and form handler.
 */

#include <Arduino.h>
#include "BoardConfig.h"

#ifdef BOARD_FASTLED

#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "HttpUtils.h"
#include "Child.h"
#include "version.h"

// ── Global data definitions ───────────────────────────────────────────────────

CRGB leds[NUM_LEDS];

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
volatile uint8_t  childActSt[MAX_STR_PER_CHILD];
volatile uint8_t  childActEn[MAX_STR_PER_CHILD];
volatile uint8_t  childBrightness = 255;

ChildRunnerStep   childRunner[MAX_CHILD_STEPS];
volatile uint8_t  childStepCount    = 0;
volatile uint32_t childRunnerStart  = 0;
volatile bool     childRunnerArmed  = false;
volatile bool     childRunnerActive = false;
volatile uint8_t  childSyncBlink    = 0;
volatile bool     childRunnerLoop   = true;

// ── EEPROM / NVS helpers ──────────────────────────────────────────────────────

void loadChildConfig() {
  bool loaded = false;
#ifdef BOARD_ESP32
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
#ifdef BOARD_ESP32
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
#ifdef BOARD_ESP32
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
  childCfg.strings[0].ledCount = 30;
  childCfg.strings[0].lengthMm = 500;
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].flags    = 0;   // not folded
  childCfg.strings[0].cableMm  = 0;
  childCfg.strings[0].stripDir = DIR_E;

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
  childCfg.strings[0].ledCount = 30;
  childCfg.strings[0].lengthMm = 500;
  childCfg.strings[0].ledType  = LEDTYPE_WS2812B;
  childCfg.strings[0].stripDir = DIR_E;
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
            "body{font-family:sans-serif;background:#111;color:#eee;padding:1.2em;max-width:480px}"));
  c.print(F("h1{font-size:1.4em;margin-bottom:.1em}"
            "h2{font-size:.8em;color:#888;font-weight:normal;margin-bottom:.8em}"
            ".tabs{display:flex;gap:4px;margin-bottom:1em}"
            ".tab{flex:1;padding:.4em;background:#1e1e1e;border:1px solid #333;"
            "border-radius:5px;color:#999;font-size:.85em;cursor:pointer;text-align:center}"
            ".tact{background:#446;border-color:#446;color:#fff}"
            ".pane{display:none}.row{display:flex;justify-content:space-between;"
            "padding:.35em 0;border-bottom:1px solid #222;font-size:.9em}"));
  c.print(F(".k{color:#aaa}.v{font-weight:bold}"
            "label{display:block;font-size:.82em;color:#aaa;margin:.5em 0 .15em}"
            "input,select{width:100%;background:#222;color:#eee;border:1px solid #444;"
            "border-radius:4px;padding:.3em .5em;font-size:.88em;margin-bottom:.3em}"
            ".btn{display:inline-block;padding:.4em 1.2em;background:#446;color:#fff;"
            "border:none;border-radius:5px;cursor:pointer;font-size:.9em;margin-top:.6em}"
            ".btn-warn{background:#633}"
            ".btn:active{transform:scale(.95);opacity:.7}"
            ".ftr{margin-top:1.5em;font-size:.7em;color:#444}"
            "</style></head><body>"));

  // Header
  sendBuf(c, "<h1>SlyLED</h1><h2>%s</h2>", childCfg.altName);

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
  c.print(F("<div class='row'><span class='k'>Action</span>"
            "<span class='v' id='act'>--</span></div>"
            "</div>"));

  // ── Settings pane (inside the main form) ───────────────────────────────────
  c.print(F("<form id='cf' action='/config' method='POST'>"));
  c.print(F("<div class='pane' id='p1'>"));
  c.print(F("<label>Name</label><input name='an' maxlength='15' value='"));
  c.print(childCfg.altName);
  c.print(F("'><label>Description</label><input name='desc' maxlength='31' value='"));
  c.print(childCfg.description);
  c.print(F("'><label>Number of strings</label><select name='sc' id='sc' onchange='scChg()'>"));
  for (uint8_t n = 1; n <= CHILD_MAX_STRINGS; n++)
    sendBuf(c, "<option value='%u'%s>%u</option>",
            (unsigned)n, n == childCfg.stringCount ? " selected" : "", (unsigned)n);
  c.print(F("</select><button class='btn' type='button' id='sb1' onclick='doSave(this)'>Save Settings</button>"
            "<button class='btn btn-warn' type='button' style='margin-left:.5em'"
            " onclick=\"document.getElementById('rf').submit()\">Factory Reset</button>"));
  c.print(F("</div>"));

  // ── Config pane ────────────────────────────────────────────────────────────
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
            "</select>"
            "<button class='btn' type='button' style='background:#363;margin-left:.3em' onclick='doTest()'>Run</button>"
            " <button class='btn btn-warn' type='button' onclick='doTestStop()'>Stop</button>"
            "</div>"
            "</div>"));
  c.print(F("</form>"));

  // Factory reset (separate form — HTML forbids nested forms)
  c.print(F("<form id='rf' action='/config/reset' method='POST' style='display:none'></form>"));

  // Footer — version only; Factory Reset lives in the Settings tab
  sendBuf(c, "<div class='ftr'>v%d.%d</div>", APP_MAJOR, APP_MINOR);

  // JavaScript
  c.print(F("<script>"));
  c.print(F("function showTab(t){"
            "for(var i=0;i<3;i++){"
            "document.getElementById('p'+i).style.display=i==t?'block':'none';"
            "document.getElementById('n'+i).className='tab'+(i==t?' tact':'');}"
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
  c.print(F("function doSave(btn){"
            "var orig=btn.textContent;btn.textContent='Saving...';btn.disabled=true;"
            "btn.style.background='#555';"
            "var fd=new FormData(document.getElementById('cf'));"
            "var x=new XMLHttpRequest();"
            "x.open('POST','/config',true);"
            "x.onload=function(){"
            "btn.textContent='Saved!';btn.style.background='#2a2';"
            "setTimeout(function(){location.reload();},800);};"
            "x.onerror=function(){btn.textContent='Error';btn.style.background='#a22';};"
            "x.send(new URLSearchParams(fd));}"));
  c.print(F("function doTest(){"
            "var t=document.getElementById('tt').value;"
            "var x=new XMLHttpRequest();x.open('GET','/test?t='+t,true);"
            "x.send();}"));
  c.print(F("function doTestStop(){"
            "var x=new XMLHttpRequest();x.open('POST','/test/stop',true);"
            "x.send();}"));
  c.print(F("showTab(0);showStr(0);poll();setInterval(poll,3000);"
            "</script></body></html>"));
  c.flush();
}

// ── POST /config ──────────────────────────────────────────────────────────────

void handlePostChildConfig(WiFiClient& c, int contentLen) {
  static char body[320];
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
  }

  saveChildConfig();

  // Send 303 redirect BEFORE the broadcast PONG — the PONG triggers a
  // UDP send that can stall on the D1 Mini, causing the browser to
  // timeout waiting for the HTTP response.
  c.print(F("HTTP/1.1 303 See Other\r\n"
            "Location: /config\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"));
  c.flush();
  // Give the browser time to read the response before closing
  delay(50);
  c.stop();

  sendPong(IPAddress(255, 255, 255, 255));  // notify parent of updated config
}

#endif  // BOARD_FASTLED
