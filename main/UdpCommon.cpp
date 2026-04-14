/*
 * UdpCommon.cpp — UDP packet dispatch and HTTP server loop.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "HttpUtils.h"
#include "JsonUtils.h"
#include "UdpCommon.h"

#ifdef BOARD_GIGA
#include "Parent.h"
#endif

#ifdef BOARD_CHILD
#include "Child.h"
#include "ChildLED.h"
#include "OtaUpdate.h"
#endif

#ifdef BOARD_DMX_BRIDGE
#include "ArtNetRecv.h"
#endif

// ── handleUdpPacket ───────────────────────────────────────────────────────────

void handleUdpPacket(uint8_t cmd, IPAddress sender, uint8_t* payload, int plen) {
#ifdef BOARD_GIGA
  if (cmd == CMD_PONG && plen >= (int)sizeof(PongPayload)) {
    PongPayload pong;
    memcpy(&pong, payload, sizeof(pong));
    registerChild(sender, &pong);
  }
#elif defined(BOARD_GYRO)
  // Gyro board — full UDP protocol (CMD_GYRO_ORIENT, CMD_GYRO_CTRL, PONG, OTA)
  // is implemented in Issue #402 (GyroUdp.h/.cpp).  Suppress unused-parameter
  // warnings until that issue is merged.
  (void)cmd; (void)sender; (void)payload; (void)plen;
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
    childActP16a  = ap.p16a ? ap.p16a : 500;
    childActP8a   = ap.p8a;
    childActP8b   = ap.p8b;
    childActP8c   = ap.p8c;
    childActP8d   = ap.p8d;
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
      cr.p16a         = ls.p16a;
      cr.p8a = ls.p8a; cr.p8b = ls.p8b;
      cr.p8c = ls.p8c; cr.p8d = ls.p8d;
      cr.durationS    = ls.durationS;
      cr.delayMs      = ls.delayMs;
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
      memcpy(udpBuf,              &ack,           sizeof(ack));
      udpBuf[sizeof(ack)] = ls.stepIndex;
      cmdUDP.beginPacket(sender, UDP_PORT);
      cmdUDP.write(udpBuf, sizeof(ack) + 1);
      cmdUDP.endPacket();
      // Last step received → single blink confirmation
      if (ls.stepIndex + 1 == ls.totalSteps)
        childSyncBlink = 1;
    }
  } else if (cmd == CMD_RUNNER_GO && plen >= 4) {
    uint32_t startEpoch;
    memcpy(&startEpoch, payload, 4);
    childRunnerStart  = startEpoch;
    childRunnerArmed  = true;
    childRunnerActive = false;
    childRunnerLoop   = (plen >= 5) ? (payload[4] != 0) : true;
    childSyncBlink    = 0;  // cancel any pending sync blink
    // Store parent IP for ACTION_EVENT replies
    uint32_t sip = (uint32_t)sender[0] | ((uint32_t)sender[1] << 8)
                 | ((uint32_t)sender[2] << 16) | ((uint32_t)sender[3] << 24);
    childParentIP = sip;
  } else if (cmd == CMD_RUNNER_STOP) {
    childRunnerActive = false;
    childRunnerArmed  = false;
    childActType = ACT_OFF;
    childActSeq++;
  } else if (cmd == CMD_SET_BRIGHTNESS && plen >= 1) {
    childBrightness = payload[0];
#if !defined(BOARD_GIGA_DMX) && !defined(BOARD_GIGA_CHILD)
  } else if (cmd == CMD_OTA_UPDATE && plen > 5) {
    // Payload: newMajor(1) + newMinor(1) + newPatch(1) + urlLen(2) + url(N) + sha256(64)
    uint8_t newMaj = payload[0];
    uint8_t newMin = payload[1];
    uint8_t newPat = payload[2];
    uint16_t urlLen = payload[3] | (payload[4] << 8);
    if (urlLen > 0 && urlLen < (uint16_t)(plen - 5)) {
      char otaUrl[256];
      uint16_t copyLen = urlLen < 255 ? urlLen : 255;
      memcpy(otaUrl, &payload[5], copyLen);
      otaUrl[copyLen] = '\0';
      char otaSha[65] = {0};
      uint16_t shaOff = 5 + urlLen;
      if (shaOff + 64 <= (uint16_t)plen) {
        memcpy(otaSha, &payload[shaOff], 64);
        otaSha[64] = '\0';
      }
      if (Serial) Serial.printf("OTA: received update cmd v%d.%d.%d url=%s\n", newMaj, newMin, newPat, otaUrl);
      bool ok = otaStartUpdate(otaUrl, otaSha, newMaj, newMin, newPat);
      if (ok) {
        delay(500);
        #ifdef BOARD_ESP32
        ESP.restart();
        #elif defined(BOARD_D1MINI)
        ESP.restart();
        #endif
      }
    }
#endif  // !BOARD_GIGA_DMX && !BOARD_GIGA_CHILD
  }
  (void)plen;
#endif
}

// ── pollUDP ───────────────────────────────────────────────────────────────────

void pollUDP() {
#ifdef BOARD_CHILD
  // Drain pending ACTION_EVENT from LED task (must send from main thread)
  if (childEvtPending) {
    childEvtPending = false;
    sendActionEvent();
  }
#endif

  int plen = cmdUDP.parsePacket();
  if (plen <= 0 || plen > (int)sizeof(udpBuf)) return;
  udpRxCount++;

  IPAddress sender = cmdUDP.remoteIP();
  int n = cmdUDP.read(udpBuf, sizeof(udpBuf));
  if (n < (int)sizeof(UdpHeader)) return;

  UdpHeader hdr;
  memcpy(&hdr, udpBuf, sizeof(hdr));
  if (hdr.magic != UDP_MAGIC || hdr.version != UDP_VERSION) return;

  handleUdpPacket(hdr.cmd, sender, udpBuf + sizeof(hdr), n - (int)sizeof(hdr));
}

// ── serveClient ───────────────────────────────────────────────────────────────

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
      if (strncasecmp(hdr, "Content-Length:", 15) == 0) {
        contentLen = atoi(hdr + 15);
      }
    }
  }

  bool isPost = strncmp(req, "POST", 4) == 0;
  bool isPut  = strncmp(req, "PUT ", 4) == 0;
  bool isDel  = strncmp(req, "DELE", 4) == 0;

  // ── Route dispatch ─────────────────────────────────────────────────────────

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

#ifdef BOARD_CHILD
  } else if (isPost && strstr(req, " /wifi ")) {
    // Save WiFi credentials: POST /wifi {"ssid":"...","password":"..."}
    char wfBody[128] = {0};
    if (contentLen > 0 && contentLen < (int)sizeof(wfBody))
      client.readBytes(wfBody, contentLen);
    char newSSID[33] = {0};
    char newPASS[65] = {0};
    char* ss = strstr(wfBody, "\"ssid\"");
    char* pp = strstr(wfBody, "\"password\"");
    if (ss) { char* v = strchr(ss + 6, '"'); if (v) { char* e = strchr(v + 1, '"'); if (e && e - v - 1 < 33) memcpy(newSSID, v + 1, e - v - 1); } }
    if (pp) { char* v = strchr(pp + 10, '"'); if (v) { char* e = strchr(v + 1, '"'); if (e && e - v - 1 < 65) memcpy(newPASS, v + 1, e - v - 1); } }
    if (newSSID[0]) {
      saveWiFiCredentials(newSSID, newPASS);
      sendJsonOk(client);
    } else {
      sendJsonErr(client, "ssid required");
    }

  } else if (isPost && strstr(req, " /ota ")) {
    // OTA update: POST /ota with JSON body {"url":"...","sha256":"...","major":5,"minor":2}
    char otaBody[512] = {0};
    if (contentLen > 0 && contentLen < (int)sizeof(otaBody))
      client.readBytes(otaBody, contentLen);
    char* urlStart = strstr(otaBody, "\"url\"");
    char* shaStart = strstr(otaBody, "\"sha256\"");
    char* majStart = strstr(otaBody, "\"major\"");
    char* minStart = strstr(otaBody, "\"minor\"");
    char* patStart = strstr(otaBody, "\"patch\"");
    char otaUrl[256] = {0};
    char otaSha[65] = {0};
    uint8_t otaMaj = 0, otaMin = 0, otaPat = 0;
    if (urlStart) { char* v = strchr(urlStart + 5, '"'); if (v) { char* e = strchr(v + 1, '"'); if (e && e - v - 1 < 255) { memcpy(otaUrl, v + 1, e - v - 1); } } }
    if (shaStart) { char* v = strchr(shaStart + 8, '"'); if (v) { char* e = strchr(v + 1, '"'); if (e && e - v - 1 <= 64) { memcpy(otaSha, v + 1, e - v - 1); } } }
    if (majStart) { char* v = strchr(majStart + 7, ':'); if (v) otaMaj = (uint8_t)atoi(v + 1); }
    if (minStart) { char* v = strchr(minStart + 7, ':'); if (v) otaMin = (uint8_t)atoi(v + 1); }
    if (patStart) { char* v = strchr(patStart + 7, ':'); if (v) otaPat = (uint8_t)atoi(v + 1); }
    if (otaUrl[0]) {
      sendJsonOk(client);
      client.flush();
      delay(200);
      bool ok = otaStartUpdate(otaUrl, otaSha, otaMaj, otaMin, otaPat);
      if (ok) {
        delay(500);
        #ifdef BOARD_ESP32
        ESP.restart();
        #elif defined(BOARD_D1MINI)
        ESP.restart();
        #endif
      }
    } else {
      sendJsonErr(client, "url required");
    }

  } else if (isPost && strstr(req, " /reboot ")) {
    sendJsonOk(client);
    client.flush();
    delay(200);
#if defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
    NVIC_SystemReset();
#else
    ESP.restart();
#endif

#if defined(BOARD_ESP32) && !defined(BOARD_DMX_BRIDGE)
  } else if (strstr(req, " /test/pin")) {
    // Pin test: /test/pin?s=0 — flashes ONLY the selected string via FastLED
    // Uses string index (not GPIO pin) to address the correct LED range
    uint8_t si = 0;
    char* pp = strstr(req, "?s=");
    if (pp) si = (uint8_t)atoi(pp + 3);
    // Compute LED range for this string
    uint16_t st = 0, en = 0;
    for (uint8_t j = 0; j < childCfg.stringCount && j <= si; j++) {
      if (j == si) { en = st + childCfg.strings[j].ledCount - 1; break; }
      st += childCfg.strings[j].ledCount;
    }
    if (en >= NUM_LEDS) en = NUM_LEDS - 1;
    // Flash R/G/B on just this string's LEDs
    fill_solid(leds, NUM_LEDS, CRGB::Black);
    for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(255, 0, 0);
    FastLED.show(); delay(500);
    for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(0, 255, 0);
    FastLED.show(); delay(500);
    for (uint16_t i = st; i <= en; i++) leds[i] = CRGB(0, 0, 255);
    FastLED.show(); delay(500);
    fill_solid(leds, NUM_LEDS, CRGB::Black);
    FastLED.show();
    sendJsonOk(client);
#endif
#ifdef BOARD_DMX_BRIDGE
  } else if (isPost && strstr(req, " /dmx/set ")) {
    // Set DMX channels: JSON body {"1":255,"2":128,...}
    {
      char body[512] = {};
      int toRead = contentLen < (int)sizeof(body) - 1 ? contentLen : (int)sizeof(body) - 1;
      if (toRead > 0) client.readBytes(body, toRead);
      body[toRead] = '\0';
      char* p = body;
      while (*p) {
        if (*p == '"') {
          uint16_t ch = (uint16_t)atoi(p + 1);
          char* colon = strchr(p + 1, ':');
          if (colon && ch >= 1 && ch <= DMX_UNIVERSE_MAX) {
            uint8_t val = (uint8_t)atoi(colon + 1);
            dmxSetChannel(ch, val);
          }
          char* next = strchr(p + 1, ',');
          if (!next) break;
          p = next + 1;
        } else p++;
      }
      dmxSendFrame();
    }
    sendJsonOk(client);
  } else if (strstr(req, " /dmx/channels")) {
    // Return DMX state + config + channel names as JSON
    uint16_t n = dmxCfg.startAddress + (uint16_t)dmxCfg.fixtureCount * dmxCfg.channelsPerFixture - 1;
    if (n > DMX_UNIVERSE_MAX) n = DMX_UNIVERSE_MAX;
    if (n < dmxCfg.startAddress + dmxCfg.channelsPerFixture - 1)
        n = dmxCfg.startAddress + dmxCfg.channelsPerFixture - 1;
    char buf[3072];
    int pos = snprintf(buf, sizeof(buf),
      "{\"subnet\":%u,\"universe\":%u,\"start\":%u,\"chPerFix\":%u,\"fixCount\":%u,"
      "\"frames\":%lu,\"active\":%s,\"selfTest\":%s,\"dePin\":%u,\"names\":[",
      (unsigned)dmxCfg.subnet, (unsigned)dmxCfg.universe, dmxCfg.startAddress, dmxCfg.channelsPerFixture,
      dmxCfg.fixtureCount, (unsigned long)dmxFrameCount,
      dmxOutputActive ? "true" : "false",
      dmxSelfTestOk ? "true" : "false",
      (unsigned)DMX_EN_PIN);
    for (uint8_t i = 0; i < dmxCfg.channelsPerFixture && i < DMX_MAX_CH_PER_FIX && pos < (int)sizeof(buf) - 40; i++) {
      pos += snprintf(buf + pos, sizeof(buf) - pos, "%s\"%s\"", i > 0 ? "," : "", dmxCfg.channelNames[i]);
    }
    pos += snprintf(buf + pos, sizeof(buf) - pos, "],\"ch\":[");
    for (uint16_t i = dmxCfg.startAddress; i <= n && pos < (int)sizeof(buf) - 8; i++) {
      pos += snprintf(buf + pos, sizeof(buf) - pos, "%s%u", i > dmxCfg.startAddress ? "," : "", dmxBuf[i]);
    }
    pos += snprintf(buf + pos, sizeof(buf) - pos, "],"
      "\"artnetRx\":%lu,\"artnetPps\":%lu,\"artnetSender\":\"%s\"}",
      (unsigned long)artnetRxCount, (unsigned long)artnetPps, artnetLastSender);
    sendBuf(client, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %d\r\n\r\n", pos);
    // Send in chunks to avoid WiFi TX buffer truncation (~280 bytes limit)
    for (int sent = 0; sent < pos; ) {
      int chunk = pos - sent;
      if (chunk > 240) chunk = 240;
      client.write((const uint8_t*)(buf + sent), chunk);
      client.flush();
      sent += chunk;
    }
  } else if (isPost && strstr(req, " /dmx/blackout ")) {
    dmxBlackout();
    sendJsonOk(client);
  } else if (isPost && strstr(req, " /dmx/config ")) {
    // Update DMX config: {"startAddr":1,"chPerFix":13,"fixCount":1,"universe":0,"names":["Motor","Dim",...]}
    {
      char body[1024] = {};
      int toRead = contentLen;
      if (toRead <= 0) { delay(20); toRead = client.available(); }  // fallback if Content-Length missing
      if (toRead > (int)sizeof(body) - 1) toRead = (int)sizeof(body) - 1;
      if (toRead > 0) client.readBytes(body, toRead);
      body[toRead] = '\0';
      int sa = jsonGetInt(body, "startAddr", -1);
      int cpf = jsonGetInt(body, "chPerFix", -1);
      int fc = jsonGetInt(body, "fixCount", -1);
      int sn = jsonGetInt(body, "subnet", -1);
      int uni = jsonGetInt(body, "universe", -1);
      if (sa >= 1 && sa <= 512) dmxCfg.startAddress = (uint16_t)sa;
      if (cpf >= 1 && cpf <= DMX_MAX_CH_PER_FIX) dmxCfg.channelsPerFixture = (uint8_t)cpf;
      if (fc >= 1 && fc <= 170) dmxCfg.fixtureCount = (uint8_t)fc;
      if (sn >= 0 && sn <= 15) dmxCfg.subnet = (uint8_t)sn;
      if (uni >= 0 && uni <= 15) dmxCfg.universe = (uint8_t)uni;
      // Parse channel names from "names":["Motor","Dim",...]
      char* namesArr = strstr(body, "\"names\"");
      if (namesArr) {
        char* bracket = strchr(namesArr, '[');
        if (bracket) {
          char* p = bracket + 1;
          for (uint8_t i = 0; i < DMX_MAX_CH_PER_FIX && *p; i++) {
            while (*p == ' ' || *p == ',') p++;
            if (*p == ']') break;
            if (*p == '"') {
              p++;
              char* end = strchr(p, '"');
              if (end) {
                uint8_t len = (uint8_t)(end - p);
                if (len >= DMX_CH_NAME_LEN) len = DMX_CH_NAME_LEN - 1;
                memset(dmxCfg.channelNames[i], 0, DMX_CH_NAME_LEN);
                memcpy(dmxCfg.channelNames[i], p, len);
                p = end + 1;
              } else break;
            } else break;
          }
        }
      }
      dmxSaveConfig();
    }
    sendJsonOk(client);
#endif
  } else if (isPost && strstr(req, " /test/stop ")) {
    childActType = ACT_OFF;
    childActSeq++;
    sendJsonOk(client);
  } else if (strstr(req, " /test")) {
    // Parse action type from ?t=N in URL (default 1=solid)
    uint8_t testType = ACT_SOLID;
    char* tq = strstr(req, "?t=");
    if (tq) testType = (uint8_t)atoi(tq + 3);
    childActType = testType;
    childActR = 255; childActG = 0; childActB = 0;
    childActP16a = 200;  // speed for animated effects (slower default)
    childActP8a  = 3;    // spacing/palette/cooling/tail/density
    childActP8b  = 120;  // sparking
    childActP8c  = DIR_E; childActP8d = 80; // direction/decay
    // Set reasonable defaults per type
    if (testType == ACT_FADE)    { childActP8a = 0; childActP8b = 0; childActP8c = 255; childActP16a = 3000; }
    if (testType == ACT_BREATHE) { childActP16a = 3000; childActP8a = 10; }
    if (testType == ACT_CHASE)   { childActP16a = 200; childActP8a = 3; }
    if (testType == ACT_RAINBOW) { childActP16a = 80; childActP8a = 0; }
    if (testType == ACT_FIRE)    { childActP16a = 30; childActP8a = 55; childActP8b = 120; }
    if (testType == ACT_COMET)   { childActP16a = 60; childActP8a = 10; childActP8d = 80; }
    if (testType == ACT_TWINKLE) { childActP16a = 100; childActP8a = 3; childActP8d = 10; }
    if (testType == ACT_STROBE)   { childActP16a = 200; childActP8a = 50; }  // 200ms period, 50% duty
    if (testType == ACT_WIPE_SEQ) { childActP16a = 50; childActP8c = DIR_E; }
    if (testType == ACT_SCANNER)  { childActP16a = 30; childActP8a = 3; }    // 30ms speed, 3-pixel bar
    if (testType == ACT_SPARKLE)  { childActP16a = 50; childActP8a = 3; }
    if (testType == ACT_GRADIENT) { childActP8a = 0; childActP8b = 0; childActP8c = 255; }  // red→blue
    { uint16_t off = 0;
    for (uint8_t j = 0; j < MAX_STR_PER_CHILD; j++) {
      if (j < childCfg.stringCount && childCfg.strings[j].ledCount > 0) {
        childActSt[j] = off;
        childActEn[j] = off + childCfg.strings[j].ledCount - 1;
        off += childCfg.strings[j].ledCount;
      } else { childActSt[j] = 0xFFFF; childActEn[j] = 0xFFFF; }
    } }
    childActSeq++;
    sendJsonOk(client);
  } else if (isPost && strstr(req, " /config/reset ")) {
    handleFactoryReset(client);
  } else if (strstr(req, " /config ")) {
    if (isPost) handlePostChildConfig(client, contentLen);
    else        sendChildConfigPage(client);
#endif  // BOARD_CHILD

  } else {
#ifdef BOARD_CHILD
    sendChildConfigPage(client);
#elif defined(BOARD_GYRO)
    // Gyro board: redirect all unmatched routes to /status
    sendBuf(client, F("HTTP/1.1 302 Found\r\nLocation: /status\r\nContent-Length: 0\r\n\r\n"));
#else
    sendParentSPA(client);
#endif
  }

  client.flush();
#ifdef BOARD_D1MINI
  { unsigned long d2 = millis(); while (millis() - d2 < 200) { updateLED(); yield(); } }
#else
  delay(5);
#endif
  client.stop();
}

// ── handleClient ──────────────────────────────────────────────────────────────

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
