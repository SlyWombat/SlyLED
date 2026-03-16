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

#ifdef BOARD_CHILD
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
  strncpy(settings.parentName, "SlyLED Orchestrator", sizeof(settings.parentName) - 1);
  settings.activeRunner  = 0xFF;
  settings.runnerRunning = false;

#elif defined(BOARD_GIGA_CHILD)
  gigaLedInit();
  // Blink onboard LED to confirm boot
  leds[0] = CRGB(255, 0, 0); showSafe(); delay(300);
  leds[0] = CRGB(0, 255, 0); showSafe(); delay(300);
  leds[0] = CRGB(0, 0, 255); showSafe(); delay(300);
  clearAndShow();

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

  connectWiFi();   // also calls initChildConfig() for BOARD_CHILD

#ifdef BOARD_GIGA
  sendPing(IPAddress(255, 255, 255, 255));
#endif
}

// ── Serial command handler (all boards) ───────────────────────────────────────

static void checkSerialCmd() {
  if (!Serial || !Serial.available()) return;
  static char cmdBuf[16];
  static uint8_t cmdPos = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdPos > 0) {
        cmdBuf[cmdPos] = '\0';
        if (strcmp(cmdBuf, "VERSION") == 0) {
          Serial.print("SLYLED:");
          Serial.print(APP_MAJOR);
          Serial.print('.');
          Serial.println(APP_MINOR);
        } else if (strcmp(cmdBuf, "WIFIHASH") == 0) {
          // Simple hash of SSID+password so parent can detect changes
          uint32_t h = 5381;
          for (const char* p = SECRET_SSID; *p; p++) h = h * 33 + *p;
          for (const char* p = SECRET_PASS; *p; p++) h = h * 33 + *p;
          Serial.print("WIFIHASH:");
          Serial.println(h, HEX);
        } else if (strcmp(cmdBuf, "BOARD") == 0) {
#ifdef BOARD_GIGA
          Serial.println("BOARD:giga-parent");
#elif defined(BOARD_GIGA_CHILD)
          Serial.println("BOARD:giga-child");
#elif defined(BOARD_ESP32)
          Serial.println("BOARD:esp32");
#elif defined(BOARD_D1MINI)
          Serial.println("BOARD:d1mini");
#else
          Serial.println("BOARD:unknown");
#endif
        }
        cmdPos = 0;
      }
    } else if (cmdPos < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdPos++] = c;
    }
  }
}

// ── loop ──────────────────────────────────────────────────────────────────────

void loop() {
  checkSerialCmd();
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

#elif defined(BOARD_GIGA_CHILD)
  // Giga child: full action rendering on single onboard RGB pixel
  {
    static uint8_t prevSeq = 0;
    static unsigned long actStart = 0;
    static bool offDone = false;
    uint8_t seq = childActSeq;
    if (seq != prevSeq) { prevSeq = seq; actStart = millis(); offDone = false; }
    uint8_t at = childActType;
    if (at != ACT_OFF) {
      leds[0] = CRGB(0, 0, 0);
      applyAction(at, childActR, childActG, childActB,
                  childActP16a, childActP8a, childActP8b,
                  childActP8c, childActP8d,
                  millis() - actStart, 0, 0, false);
      showSafe();
      delay(20);
    } else if (!offDone) { clearAndShow(); offDone = true; delay(10); }
  }
  handleClient();

#elif defined(BOARD_D1MINI)
  updateLED();
  handleClient();
  yield();

#else  // ESP32
  handleClient();
  delay(10);
#endif
}
