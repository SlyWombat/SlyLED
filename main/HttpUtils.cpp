/*
 * HttpUtils.cpp — HTTP response helpers shared across all boards.
 */

#include <Arduino.h>
#include <stdarg.h>
#include "BoardConfig.h"
#include "Protocol.h"
#include "Globals.h"
#include "HttpUtils.h"
#include "Child.h"   // childCfg, childActType — guarded inside Child.h

// ── Formatted print into shared tx buffer ────────────────────────────────────

void sendBuf(WiFiClient& c, const char* fmt, ...) {
  va_list ap; va_start(ap, fmt);
  vsnprintf(_txbuf, sizeof(_txbuf), fmt, ap);
  va_end(ap);
  c.print(_txbuf);
}

// ── Standard JSON responses ───────────────────────────────────────────────────

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

// ── GET /status ───────────────────────────────────────────────────────────────

void sendStatus(WiFiClient& c) {
  char body[128];
  int blen;
#ifdef BOARD_GIGA
  blen = snprintf(body, sizeof(body), "{\"role\":\"parent\",\"hostname\":\"slyled\"}");
#else
  const char* boardName =
#ifdef BOARD_ESP32
    "esp32";
#elif defined(BOARD_D1MINI)
    "d1mini";
#elif defined(BOARD_GIGA_CHILD)
    "giga-child";
#else
    "unknown";
#endif
  blen = snprintf(body, sizeof(body),
    "{\"role\":\"child\",\"hostname\":\"%s\",\"board\":\"%s\",\"action\":%u,\"udpRx\":%lu}",
    childCfg.hostname, boardName, (unsigned)childActType, (unsigned long)udpRxCount);
#endif
  sendBuf(c, "HTTP/1.1 200 OK\r\n"
             "Content-Type: application/json\r\n"
             "Connection: close\r\n"
             "Cache-Control: no-cache, no-store\r\n"
             "Content-Length: %d\r\n\r\n", blen);
  c.print(body);
  c.flush();
}
