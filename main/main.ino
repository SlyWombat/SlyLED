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
#include "OtaUpdate.h"
#endif

#ifdef BOARD_DMX_BRIDGE
#include "DmxBridge.h"
#include "ArtNetRecv.h"
#endif

#ifdef BOARD_GYRO
#include "GyroBoard.h"
#include "GyroDisplay.h"
#include "GyroTouch.h"
#include "GyroIMU.h"
#include "GyroUdp.h"
#include "GyroUI.h"
#endif

// ── setup ─────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
#ifdef BOARD_D1MINI
  // Drive data pin low immediately to prevent WS2812B power-on glitch
  // (GPIO2 is pulled high during ESP boot → strip reads garbage → white flash)
  pinMode(DATA_PIN, OUTPUT);
  digitalWrite(DATA_PIN, LOW);
  delay(1);           // 1 ms reset pulse for WS2812B
#elif defined(BOARD_ESP32) && !defined(BOARD_DMX_BRIDGE)
  // Default pin low for glitch prevention; real pins configured after config loads
  pinMode(2, OUTPUT);
  digitalWrite(2, LOW);
  delay(1);
#elif defined(BOARD_GYRO)
  // Backlight off during init to avoid white-flash on cold power-up
  pinMode(GYRO_LCD_BL, OUTPUT);
  digitalWrite(GYRO_LCD_BL, LOW);
#endif
#ifdef BOARD_GIGA_DMX
  // Onboard LED diagnostics: RED = booting
  pinMode(LEDR, OUTPUT); pinMode(LEDG, OUTPUT); pinMode(LEDB, OUTPUT);
  digitalWrite(LEDR, LOW); digitalWrite(LEDG, HIGH); digitalWrite(LEDB, HIGH); // RED on
#endif
  delay(500);
  if (Serial) Serial.println("=== BOOT ===");

#ifdef BOARD_GYRO
  gyroIMUInit();
  gyroTouchInit();
  connectWiFi();
  gyroDisplayInit();  // display after WiFi so we can show connect status
  gyroUIInit();
  gyroUdpInit();

#elif defined(BOARD_GIGA)
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
  clearAndShow();

#elif defined(BOARD_D1MINI)
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
#endif

#ifndef BOARD_GYRO
  connectWiFi();   // also calls initChildConfig() for BOARD_CHILD
#endif
#ifdef BOARD_GIGA_DMX
  // GREEN = WiFi connected
  digitalWrite(LEDR, HIGH); digitalWrite(LEDG, LOW); // GREEN on
#endif

#ifdef BOARD_DMX_BRIDGE
  dmxInit();
#elif defined(BOARD_ESP32)
  // Config now loaded from NVS — init FastLED with per-string GPIO pins
  esp32InitLeds();
  FastLED.setBrightness(LED_BRIGHTNESS);
  FastLED.clear();
  FastLED.show();
  xTaskCreatePinnedToCore(ledTask, "LED", 4096, NULL, 1, NULL, 0);
#endif

#ifdef BOARD_CHILD
  otaConfirmBoot();  // ESP32: start 60s watchdog for OTA rollback safety
  bootAnimation();
  // Announce ourselves to any listening parent
  sendPong(IPAddress(255, 255, 255, 255));
#endif
#ifdef BOARD_GIGA_DMX
  // BLUE = fully initialized, PONG sent
  digitalWrite(LEDG, HIGH); digitalWrite(LEDB, LOW); // BLUE on
  if (Serial) { Serial.print(F("[DMX] Ready. IP: ")); Serial.println(WiFi.localIP()); }
#endif

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
          Serial.print(APP_MINOR);
          Serial.print('.');
          Serial.println(APP_PATCH);
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
#elif defined(BOARD_GIGA_DMX)
          Serial.println("BOARD:giga-dmx");
#elif defined(BOARD_GIGA_CHILD)
          Serial.println("BOARD:giga-child");
#elif defined(BOARD_DMX_BRIDGE)
          Serial.println("BOARD:dmx-bridge");
#elif defined(BOARD_GYRO)
          Serial.println("BOARD:gyro");
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
  // Giga child: sync blink + action rendering on onboard RGB pixel
  if (!childBootDone) { delay(10); goto giga_end; }
  {
    // Sync blink confirmation
    if (childSyncBlink > 0) {
      uint8_t n = childSyncBlink;
      childSyncBlink = 0;
      for (uint8_t b = 0; b < n; b++) {
        leds[0] = CRGB(255, 255, 255); showSafe(); delay(200);
        leds[0] = CRGB(0, 0, 0); showSafe(); delay(200);
      }
    }
    // Runner execution
    if (childRunnerArmed && childStepCount > 0) {
      uint32_t now = (uint32_t)currentEpoch();
      if (now < 1577836800UL || now >= childRunnerStart) {
        childRunnerArmed = false;
        childRunnerActive = true;
      }
    }
    if (childRunnerActive && childStepCount > 0) {
      static uint8_t prevStep = 0xFF;
      static unsigned long stepStart = 0;
      uint32_t elapsed = (uint32_t)currentEpoch() - childRunnerStart;
      uint8_t curStep = 0; uint32_t acc = 0; bool done = true;
      for (uint8_t i = 0; i < childStepCount; i++) {
        acc += childRunner[i].durationS;
        if (elapsed < acc) { curStep = i; done = false; break; }
      }
      if (done) {
        if (childRunnerLoop) { childRunnerStart += acc; prevStep = 0xFF; }
        else { childRunnerActive = false; }
        clearAndShow(); goto giga_end;
      }
      if (curStep != prevStep) { prevStep = curStep; stepStart = millis(); }
      leds[0] = CRGB(0, 0, 0);
      unsigned long se = millis() - stepStart;
      uint16_t dly = childRunner[curStep].delayMs;
      if (se >= dly) {
        applyAction(childRunner[curStep].actionType,
                    childRunner[curStep].r, childRunner[curStep].g, childRunner[curStep].b,
                    childRunner[curStep].p16a, childRunner[curStep].p8a,
                    childRunner[curStep].p8b, childRunner[curStep].p8c, childRunner[curStep].p8d,
                    se - dly, 0, 0, false);
      }
      showSafe(); delay(20); goto giga_end;
    }
    // Immediate action
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
  giga_end:
  handleClient();

#elif defined(BOARD_D1MINI)
  updateLED();
  handleClient();
  otaCheckConfirm();
  yield();

#elif defined(BOARD_DMX_BRIDGE)
  // DMX bridge: dumb node — Art-Net/sACN → dmxBuf → DMX output
  // pollArtNet() is highest priority — must run frequently to catch 40Hz stream
  pollArtNet();
  {
    static unsigned long lastFrame = 0;
    if (millis() - lastFrame >= (1000 / DMX_FRAME_HZ)) {
      lastFrame = millis();
      dmxSendFrame();
    }
  }
  pollArtNet();     // poll again after frame send
  pollUDP();        // SlyLED protocol — config, PING/PONG, status
  pollArtNet();     // poll again after UDP
  handleClient();   // HTTP — config UI, /dmx/set, /dmx/channels

#elif defined(BOARD_GYRO)
  gyroUIUpdate();
  gyroUdpUpdate();
  handleClient();
  delay(10);

#else  // ESP32
  handleClient();
  otaCheckConfirm();
  delay(10);
#endif
}
