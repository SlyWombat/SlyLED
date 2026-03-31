/*
 * DmxBridge.cpp — DMX-512 output via UART + RS-485 transceiver.
 *
 * Supports both ESP32 (UART2) and Giga R1 (Serial1).
 *
 * DMX-512 frame format:
 *   BREAK  (>=88us low)
 *   MAB    (>=8us high / mark-after-break)
 *   START  (0x00 start code, 8N2)
 *   DATA   (1-512 channel bytes, 8N2)
 */

#include "DmxBridge.h"

#ifdef BOARD_DMX_BRIDGE

// ── Globals ──────────────────────────────────────────────────────────────────

DmxBridgeConfig dmxCfg;
uint8_t dmxBuf[DMX_UNIVERSE_MAX + 1]; // [0] = start code, [1..512] = channels
CRGB leds[NUM_LEDS];                  // virtual LED array for action rendering

// ── Platform-specific UART ───────────────────────────────────────────────────

#ifdef BOARD_GIGA_DMX
  // Giga R1: Serial1 on TX1 (pin 1) / RX1 (pin 0)
  #define DMX_SERIAL Serial1
  #define DMX_TX_PIN_GIGA 1   // TX1 on Giga header
#else
  // ESP32: UART2
  #include <HardwareSerial.h>
  static HardwareSerial DmxSerial(2);
  #define DMX_SERIAL DmxSerial
#endif

// ── Config persistence ───────────────────────────────────────────────────────

#ifdef BOARD_GIGA_DMX
// Giga has no NVS — store in RAM, defaults on reboot
void dmxLoadConfig() {
  dmxCfg.universe           = 0;
  dmxCfg.startAddress       = 1;
  dmxCfg.channelsPerFixture = 3;
  dmxCfg.fixtureCount       = 1;
}
void dmxSaveConfig() { /* RAM only on Giga — persists until reboot */ }
#else
#include <Preferences.h>
static const char* NVS_NS = "slydmx";
void dmxLoadConfig() {
  Preferences prefs;
  prefs.begin(NVS_NS, true);
  dmxCfg.universe           = prefs.getUShort("universe", 0);
  dmxCfg.startAddress       = prefs.getUShort("startAddr", 1);
  dmxCfg.channelsPerFixture = prefs.getUChar("chPerFix", 3);
  dmxCfg.fixtureCount       = prefs.getUChar("fixCount", 1);
  prefs.end();
}
void dmxSaveConfig() {
  Preferences prefs;
  prefs.begin(NVS_NS, false);
  prefs.putUShort("universe", dmxCfg.universe);
  prefs.putUShort("startAddr", dmxCfg.startAddress);
  prefs.putUChar("chPerFix", dmxCfg.channelsPerFixture);
  prefs.putUChar("fixCount", dmxCfg.fixtureCount);
  prefs.end();
}
#endif

// ── DMX-512 UART init ────────────────────────────────────────────────────────

void dmxInit() {
  memset(dmxBuf, 0, sizeof(dmxBuf));
  memset(leds, 0, sizeof(leds));

  dmxLoadConfig();
  if (dmxCfg.startAddress < 1) dmxCfg.startAddress = 1;
  if (dmxCfg.channelsPerFixture < 1) dmxCfg.channelsPerFixture = 3;
  if (dmxCfg.fixtureCount < 1) dmxCfg.fixtureCount = 1;

  // RS-485 direction enable pin (HIGH = transmit)
  pinMode(DMX_EN_PIN, OUTPUT);
  digitalWrite(DMX_EN_PIN, HIGH);

  // Configure UART for DMX: 250kbaud, 8N2
#ifdef BOARD_GIGA_DMX
  DMX_SERIAL.begin(DMX_BAUD);
#else
  DMX_SERIAL.begin(DMX_BAUD, SERIAL_8N2, -1, DMX_TX_PIN);
#endif

  if (Serial) {
    Serial.print(F("[DMX] Init: universe="));
    Serial.print(dmxCfg.universe);
    Serial.print(F(" addr="));
    Serial.print(dmxCfg.startAddress);
    Serial.print(F(" ch/fix="));
    Serial.print(dmxCfg.channelsPerFixture);
    Serial.print(F(" fixtures="));
    Serial.println(dmxCfg.fixtureCount);
  }
}

// ── DMX-512 frame output ─────────────────────────────────────────────────────

void dmxSendFrame() {
#ifdef BOARD_GIGA_DMX
  // Giga: generate break by driving TX1 pin low manually
  DMX_SERIAL.end();
  pinMode(DMX_TX_PIN_GIGA, OUTPUT);
  digitalWrite(DMX_TX_PIN_GIGA, LOW);
  delayMicroseconds(120);
  digitalWrite(DMX_TX_PIN_GIGA, HIGH);
  delayMicroseconds(12);
  DMX_SERIAL.begin(DMX_BAUD);
#else
  // ESP32: toggle TX pin for break/MAB
  DMX_SERIAL.end();
  pinMode(DMX_TX_PIN, OUTPUT);
  digitalWrite(DMX_TX_PIN, LOW);
  delayMicroseconds(120);
  digitalWrite(DMX_TX_PIN, HIGH);
  delayMicroseconds(12);
  DMX_SERIAL.begin(DMX_BAUD, SERIAL_8N2, -1, DMX_TX_PIN);
#endif

  // Send start code + all 512 channels
  DMX_SERIAL.write(dmxBuf, DMX_UNIVERSE_MAX + 1);
  DMX_SERIAL.flush();
}

// ── Copy virtual leds[] to DMX channel buffer ────────────────────────────────

void dmxUpdateFromLeds() {
  uint16_t addr = dmxCfg.startAddress;
  uint8_t cpf = dmxCfg.channelsPerFixture;
  uint16_t maxFix = dmxCfg.fixtureCount;
  if (maxFix > NUM_LEDS) maxFix = NUM_LEDS;

  for (uint16_t i = 0; i < maxFix; i++) {
    uint16_t slot = addr + (uint16_t)i * cpf;
    if (slot > DMX_UNIVERSE_MAX) break;
    if (cpf >= 1 && slot <= DMX_UNIVERSE_MAX) dmxBuf[slot]     = leds[i].r;
    if (cpf >= 2 && slot + 1 <= DMX_UNIVERSE_MAX) dmxBuf[slot + 1] = leds[i].g;
    if (cpf >= 3 && slot + 2 <= DMX_UNIVERSE_MAX) dmxBuf[slot + 2] = leds[i].b;
    if (cpf >= 4 && slot + 3 <= DMX_UNIVERSE_MAX) dmxBuf[slot + 3] = 255;
  }
}

// ── Set a single DMX channel directly (for test UI sliders) ──────────────────

void dmxSetChannel(uint16_t channel, uint8_t value) {
  if (channel >= 1 && channel <= DMX_UNIVERSE_MAX)
    dmxBuf[channel] = value;
}

void dmxBlackout() {
  memset(dmxBuf, 0, sizeof(dmxBuf));
  memset(leds, 0, sizeof(CRGB) * NUM_LEDS);
  dmxSendFrame();
}

// ── Helpers for BOARD_CHILD compatibility ─────────────────────────────────

void clearAndShow() {
  memset(leds, 0, sizeof(CRGB) * NUM_LEDS);
  memset(dmxBuf, 0, sizeof(dmxBuf));
  dmxSendFrame();
}

void fill_solid(CRGB* arr, uint16_t count, CRGB color) {
  for (uint16_t i = 0; i < count; i++) arr[i] = color;
}

#endif // BOARD_DMX_BRIDGE
