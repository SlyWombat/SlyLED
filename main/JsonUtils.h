/*
 * JsonUtils.h — Minimal JSON key-value parsers (no dynamic allocation).
 */

#ifndef JSONUTILS_H
#define JSONUTILS_H

#include <stdint.h>

int  jsonGetInt(const char* json, const char* key, int defVal);
void jsonGetStr(const char* json, const char* key, char* out, uint8_t len);

#endif  // JSONUTILS_H
