/*
 * JsonUtils.cpp — Minimal JSON key-value parsers (no dynamic allocation).
 */

#include <Arduino.h>
#include "JsonUtils.h"

int jsonGetInt(const char* json, const char* key, int defVal) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* p = strstr(json, needle);
  if (!p) return defVal;
  p += strlen(needle);
  while (*p == ' ') p++;
  if (*p == '-' || (*p >= '0' && *p <= '9')) return atoi(p);
  return defVal;
}

void jsonGetStr(const char* json, const char* key, char* out, uint8_t len) {
  char needle[28];
  snprintf(needle, sizeof(needle), "\"%s\":", key);
  const char* p = strstr(json, needle);
  if (!p) { out[0] = '\0'; return; }
  p += strlen(needle);
  while (*p == ' ' || *p == '\t') p++;
  if (*p != '"') { out[0] = '\0'; return; }
  p++;
  uint8_t i = 0;
  while (*p && *p != '"' && i < len - 1) out[i++] = *p++;
  out[i] = '\0';
}
