/*
 * UdpCommon.cpp — UDP packet dispatch and HTTP server loop.
 */

#include <Arduino.h>
#include "BoardConfig.h"
#include "Protocol.h"
#include "Globals.h"
#include "NetUtils.h"
#include "HttpUtils.h"
#include "UdpCommon.h"

#ifdef BOARD_GIGA
#include "Parent.h"
#endif

#ifdef BOARD_FASTLED
#include "Child.h"
#include "ChildLED.h"
#endif

// ── handleUdpPacket ───────────────────────────────────────────────────────────

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
      memcpy(udpBuf,              &ack,           sizeof(ack));
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

// ── pollUDP ───────────────────────────────────────────────────────────────────

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
      if (strncmp(hdr, "Content-Length:", 15) == 0) {
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

#ifdef BOARD_FASTLED
  } else if (isPost && strstr(req, " /config/reset ")) {
    handleFactoryReset(client);
  } else if (strstr(req, " /config ")) {
    if (isPost) handlePostChildConfig(client, contentLen);
    else        sendChildConfigPage(client);
#endif  // BOARD_FASTLED

  } else {
#ifdef BOARD_FASTLED
    client.print("HTTP/1.1 302 Found\r\n"
                 "Location: /config\r\n"
                 "Content-Length: 0\r\n"
                 "Connection: close\r\n\r\n");
    client.flush();
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
