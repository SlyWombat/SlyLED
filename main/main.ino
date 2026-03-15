/*
 * SlyLED — multi-board sketch
 *
 * Parent (Giga R1 WiFi): serves multi-tab SPA, manages children via UDP
 * Children (ESP32 / D1 Mini): execute UDP actions; serve self-config page
 *
 * HTTP routes (all boards):
 *   GET  /              — SPA (parent) or redirect to /config (child)
 *   GET  /status        — JSON status
 *   GET  /favicon.ico   — 404
 *
 * HTTP routes (child boards only):
 *   GET  /config        — child self-config form
 *   POST /config        — save config to EEPROM, notify parent via CMD_PONG
 *   POST /config/reset  — factory reset
 *
 * HTTP routes (Giga parent only):
 *   GET/POST  /api/children              — child list
 *   GET       /api/children/export       — download children JSON
 *   POST      /api/children/import       — upload children JSON
 *   *         /api/children/:id          — per-child CRUD + refresh
 *   GET/POST  /api/layout                — canvas positions
 *   GET/POST  /api/settings              — app settings
 *   POST      /api/action                — immediate action to child(ren)
 *   POST      /api/action/stop           — stop immediate action
 *   GET/POST  /api/runners               — runner list
 *   GET/PUT/DELETE /api/runners/:id      — per-runner CRUD
 *   POST      /api/runners/stop          — stop all runners
 */

#include "version.h"
#include "arduino_secrets.h"
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

#ifdef BOARD_FASTLED
#include "Child.h"
#include "ChildLED.h"
#endif

// ── setup ─────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
#ifdef BOARD_FASTLED
  // Drive data pin low immediately to prevent WS2812B power-on glitch
  // (GPIO2 is pulled high during ESP boot → strip reads garbage → white flash)
  pinMode(DATA_PIN, OUTPUT);
  digitalWrite(DATA_PIN, LOW);
  delay(1);           // 1 ms reset pulse for WS2812B
#endif
  delay(500);
  if (Serial) Serial.println("=== BOOT ===");

#ifdef BOARD_GIGA
  memset(children,  0, sizeof(children));
  memset(runners,   0, sizeof(runners));
  memset(&settings, 0, sizeof(settings));
  settings.units          = 0;
  settings.darkMode       = 1;
  settings.canvasWidthMm  = 10000;
  settings.canvasHeightMm = 5000;
  strncpy(settings.parentName, "SlyLED Parent", sizeof(settings.parentName) - 1);
  settings.activeRunner  = 0xFF;
  settings.runnerRunning = false;

#elif defined(BOARD_ESP32)
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
  xTaskCreatePinnedToCore(ledTask, "LED", 4096, NULL, 1, NULL, 0);

#else  // D1 Mini
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
#endif

  connectWiFi();

#ifdef BOARD_GIGA
  sendPing(IPAddress(255, 255, 255, 255));
#endif
}

// ── loop ──────────────────────────────────────────────────────────────────────

void loop() {
  printStatus();
  pollUDP();

#ifdef BOARD_GIGA
  static unsigned long lastPing = 0;
  if (millis() - lastPing >= 30000UL) {
    lastPing = millis();
    sendPing(IPAddress(255, 255, 255, 255));
  }
  handleClient();
  delay(10);

#elif defined(BOARD_D1MINI)
  updateLED();
  handleClient();
  yield();

#else  // ESP32
  handleClient();
  delay(10);
#endif
}
