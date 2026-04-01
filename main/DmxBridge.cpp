/*
 * DmxBridge.cpp — DMX-512 output via UART + RS-485 transceiver.
 *
 * Giga R1: uses mbed::BufferedSerial on PA_0 (TX1) with manual break.
 * ESP32:   uses HardwareSerial(2) on GPIO17.
 *
 * DMX-512 frame: BREAK (>=88us low) + MAB (>=8us high) + start code + 512 data bytes
 */

#include "DmxBridge.h"

#ifdef BOARD_DMX_BRIDGE

// ── Globals ──────────────────────────────────────────────────────────────────

DmxBridgeConfig dmxCfg;
uint8_t dmxBuf[DMX_UNIVERSE_MAX + 1]; // [0] = start code (0x00), [1..512] = channels
CRGB leds[NUM_LEDS];                  // virtual LED array for action rendering
volatile uint32_t dmxFrameCount = 0;  // diagnostic counter
volatile bool dmxOutputActive = false;

// ── Platform-specific UART ───────────────────────────────────────────────────

#ifdef BOARD_GIGA_DMX
  // Giga R1: use mbed BufferedSerial for precise UART control
  // CQRobot shield wiring: TX1 (PA_0) -> DI, DE/RE on digital pin 2
  #include <mbed.h>
  static mbed::BufferedSerial* dmxSerial = nullptr;
  // TX1 on Giga R1 = PA_0 (Arduino pin 1), RX1 = PI_9 (Arduino pin 0)
  #define DMX_MBED_TX PA_0
  #define DMX_MBED_RX PI_9
#else
  // ESP32: UART2
  #include <HardwareSerial.h>
  static HardwareSerial DmxSerial(2);
#endif

// ── Config persistence ───────────────────────────────────────────────────────

#ifdef BOARD_GIGA_DMX
void dmxLoadConfig() {
  // Giga: RAM only defaults (no NVS)
  dmxCfg.universe           = 0;
  dmxCfg.startAddress       = 1;
  dmxCfg.channelsPerFixture = 13;
  dmxCfg.fixtureCount       = 1;
  memset(dmxCfg.channelNames, 0, sizeof(dmxCfg.channelNames));
  // Default channel names for a typical moving head
  strncpy(dmxCfg.channelNames[0], "Motor 1", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[1], "Motor 2", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[2], "Motor 3", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[3], "Dimmer", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[4], "Strobe", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[5], "Red", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[6], "Green", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[7], "Blue", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[8], "White", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[9], "Ch 10", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[10], "Ch 11", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[11], "Ch 12", DMX_CH_NAME_LEN - 1);
  strncpy(dmxCfg.channelNames[12], "Ch 13", DMX_CH_NAME_LEN - 1);
}
void dmxSaveConfig() { /* RAM only on Giga */ }
#else
#include <Preferences.h>
static const char* NVS_NS = "slydmx";
void dmxLoadConfig() {
  Preferences prefs;
  prefs.begin(NVS_NS, true);
  dmxCfg.universe           = prefs.getUShort("universe", 0);
  dmxCfg.startAddress       = prefs.getUShort("startAddr", 1);
  dmxCfg.channelsPerFixture = prefs.getUChar("chPerFix", 13);
  dmxCfg.fixtureCount       = prefs.getUChar("fixCount", 1);
  for (uint8_t i = 0; i < DMX_MAX_CH_PER_FIX; i++) {
    char key[8];
    snprintf(key, sizeof(key), "cn%u", i);
    String name = prefs.getString(key, "");
    if (name.length() > 0)
      strncpy(dmxCfg.channelNames[i], name.c_str(), DMX_CH_NAME_LEN - 1);
  }
  prefs.end();
}
void dmxSaveConfig() {
  Preferences prefs;
  prefs.begin(NVS_NS, false);
  prefs.putUShort("universe", dmxCfg.universe);
  prefs.putUShort("startAddr", dmxCfg.startAddress);
  prefs.putUChar("chPerFix", dmxCfg.channelsPerFixture);
  prefs.putUChar("fixCount", dmxCfg.fixtureCount);
  for (uint8_t i = 0; i < DMX_MAX_CH_PER_FIX; i++) {
    char key[8];
    snprintf(key, sizeof(key), "cn%u", i);
    prefs.putString(key, dmxCfg.channelNames[i]);
  }
  prefs.end();
}
#endif

// ── DMX-512 UART init ────────────────────────────────────────────────────────

void dmxInit() {
  memset(dmxBuf, 0, sizeof(dmxBuf));
  memset(leds, 0, sizeof(leds));
  dmxFrameCount = 0;

  dmxLoadConfig();
  if (dmxCfg.startAddress < 1) dmxCfg.startAddress = 1;
  if (dmxCfg.channelsPerFixture < 1) dmxCfg.channelsPerFixture = 13;
  if (dmxCfg.fixtureCount < 1) dmxCfg.fixtureCount = 1;

  // RS-485 direction enable (HIGH = transmit)
  pinMode(DMX_EN_PIN, OUTPUT);
  digitalWrite(DMX_EN_PIN, HIGH);

#ifdef BOARD_GIGA_DMX
  // Giga: create mbed BufferedSerial at 250kbaud
  dmxSerial = new mbed::BufferedSerial(DMX_MBED_TX, DMX_MBED_RX, DMX_BAUD);
  dmxSerial->set_format(8, mbed::BufferedSerial::None, 2); // 8N2
  dmxOutputActive = true;
#else
  // ESP32: UART2
  DmxSerial.begin(DMX_BAUD, SERIAL_8N2, -1, DMX_TX_PIN);
  dmxOutputActive = true;
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
  if (!dmxSerial) return;

  // Close serial to manually drive TX pin for BREAK
  delete dmxSerial;
  dmxSerial = nullptr;

  // BREAK: drive TX low for 120us
  pinMode(1, OUTPUT);         // Arduino pin 1 = TX1
  digitalWrite(1, LOW);
  delayMicroseconds(120);

  // MAB: drive TX high for 12us
  digitalWrite(1, HIGH);
  delayMicroseconds(12);

  // Re-open serial and send frame
  dmxSerial = new mbed::BufferedSerial(DMX_MBED_TX, DMX_MBED_RX, DMX_BAUD);
  dmxSerial->set_format(8, mbed::BufferedSerial::None, 2); // 8N2

  // Send start code + all 512 channels
  dmxSerial->write(dmxBuf, DMX_UNIVERSE_MAX + 1);

  // Wait for UART TX to complete (~22ms for 513 bytes at 250kbaud)
  // Each byte = 11 bits (start + 8 data + 2 stop) = 44us per byte
  // 513 * 44us ≈ 22.6ms
  delayMicroseconds(100);  // small extra margin

#else
  // ESP32: toggle TX pin for break/MAB
  DmxSerial.end();
  pinMode(DMX_TX_PIN, OUTPUT);
  digitalWrite(DMX_TX_PIN, LOW);
  delayMicroseconds(120);
  digitalWrite(DMX_TX_PIN, HIGH);
  delayMicroseconds(12);
  DmxSerial.begin(DMX_BAUD, SERIAL_8N2, -1, DMX_TX_PIN);
  DmxSerial.write(dmxBuf, DMX_UNIVERSE_MAX + 1);
  DmxSerial.flush();
#endif

  dmxFrameCount++;
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
    // Map CRGB to the RGB channels within the fixture profile
    // For multi-channel fixtures, RGB maps to channels at offsets within the fixture
    if (cpf >= 1 && slot <= DMX_UNIVERSE_MAX) dmxBuf[slot]     = leds[i].r;
    if (cpf >= 2 && slot + 1 <= DMX_UNIVERSE_MAX) dmxBuf[slot + 1] = leds[i].g;
    if (cpf >= 3 && slot + 2 <= DMX_UNIVERSE_MAX) dmxBuf[slot + 2] = leds[i].b;
  }
}

// ── Direct channel control (for test UI) ─────────────────────────────────────

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
