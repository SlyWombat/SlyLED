/*
 * HttpUtils.h — HTTP response helpers shared across all boards.
 */

#ifndef HTTPUTILS_H
#define HTTPUTILS_H

#include "BoardConfig.h"

void sendBuf(WiFiClient& c, const char* fmt, ...);
void sendJsonOk(WiFiClient& c);
void sendJsonErr(WiFiClient& c, const char* msg);
void sendStatus(WiFiClient& c);

#endif  // HTTPUTILS_H
