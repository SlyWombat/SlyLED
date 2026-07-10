/*
 * HttpUtils.h — HTTP response helpers shared across all boards.
 */

#ifndef HTTPUTILS_H
#define HTTPUTILS_H

#include "BoardConfig.h"

void sendBuf(WiFiClient& c, const char* fmt, ...);
void sendJsonOk(WiFiClient& c);
void sendJsonErr(WiFiClient& c, const char* msg);
void sendJsonTooLarge(WiFiClient& c);   // 413 — body exceeds parse buffer, nothing applied
void sendStatus(WiFiClient& c);

#endif  // HTTPUTILS_H
