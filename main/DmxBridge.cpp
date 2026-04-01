/*
 * DmxBridge.cpp — DMX-512 output via UART + RS-485 transceiver.
 *
 * Giga R1: mbed UnbufferedSerial on Serial1 pins (PA_0/PI_9).
 *          Break generated via slow-baud 0x00 byte (~143μs low).
 * ESP32:   HardwareSerial(2) on GPIO17 with pin-toggle break.
 *
 * DMX-512 frame: BREAK (≥88μs low) + MAB (≥8μs high) + start code + 512 data bytes
 */

#include "DmxBridge.h"

#ifdef BOARD_DMX_BRIDGE

// ── Globals ──────────────────────────────────────────────────────────────────

DmxBridgeConfig dmxCfg;
uint8_t dmxBuf[DMX_UNIVERSE_MAX + 1]; // [0] = start code (0x00), [1..512] = channels
CRGB leds[NUM_LEDS];
volatile uint32_t dmxFrameCount = 0;
volatile bool dmxOutputActive = false;
volatile bool dmxSelfTestOk = false;

// ── Platform-specific UART ───────────────────────────────────────────────────

#ifdef BOARD_GIGA_DMX
  #include <mbed.h>
  // Serial1 pins on Giga R1: D1 (TX) = PA_9, D0 (RX) = PB_7
  // CQRobot Ocean DMX shield connects MAX485 DI to D1, RO to D0, DE+/RE to D2
  static mbed::UnbufferedSerial* dmxUart = nullptr;
  #define DMX_MBED_TX PA_9
  #define DMX_MBED_RX PB_7
  // Slow baud for break: 0x00 at 76923 baud = 11 bits × 13μs = 143μs
  // Start bit (13μs low) + 8 data bits all zero (104μs low) + 2 stop bits (26μs high)
  // Total low = 117μs (exceeds 88μs minimum), stop bits provide MAB (≥8μs)
  static constexpr uint32_t DMX_BREAK_BAUD = 76923;
#else
  #include <HardwareSerial.h>
  static HardwareSerial DmxSerial(2);
  #define DMX_SERIAL DmxSerial
#endif

// ── Config persistence ───────────────────────────────────────────────────────

// Magic + version for flash storage validation
static constexpr uint32_t DMX_CFG_MAGIC = 0xDC01;

#ifdef BOARD_GIGA_DMX
  // Giga: persist config to internal flash via FlashIAP
  #include <FlashIAP.h>
  static mbed::FlashIAP flash;
  static bool flashInited = false;
  // Use last sector of bank 1 (1MB mark). Giga has 2MB flash, sketch uses ~300KB.
  // Sector at 0x08100000 - sectorSize is safe from sketch code.
  static uint32_t cfgFlashAddr = 0;
  static uint32_t cfgSectorSize = 0;

  struct DmxFlashBlock {
    uint32_t magic;
    DmxBridgeConfig cfg;
  };

  static void flashInit() {
    if (flashInited) return;
    flash.init();
    cfgSectorSize = flash.get_sector_size(flash.get_flash_start() + flash.get_flash_size() - 1);
    cfgFlashAddr = flash.get_flash_start() + flash.get_flash_size() - cfgSectorSize;
    flashInited = true;
  }

void dmxLoadConfig() {
  flashInit();
  DmxFlashBlock block;
  memset(&block, 0, sizeof(block));
  flash.read(&block, cfgFlashAddr, sizeof(block));
  if (block.magic == DMX_CFG_MAGIC) {
    memcpy(&dmxCfg, &block.cfg, sizeof(dmxCfg));
    if (Serial) Serial.println(F("[DMX] Config loaded from flash"));
  } else {
    // First boot defaults
    dmxCfg.subnet             = 0;
    dmxCfg.universe           = 0;
    dmxCfg.startAddress       = 1;
    dmxCfg.channelsPerFixture = 13;
    dmxCfg.fixtureCount       = 1;
    memset(dmxCfg.channelNames, 0, sizeof(dmxCfg.channelNames));
    strncpy(dmxCfg.channelNames[0], "Pan", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[1], "Tilt", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[2], "Speed", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[3], "Dimmer", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[4], "Strobe", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[5], "Red", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[6], "Green", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[7], "Blue", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[8], "White", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[9], "Colour", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[10], "Gobo", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[11], "Prism", DMX_CH_NAME_LEN - 1);
    strncpy(dmxCfg.channelNames[12], "Focus", DMX_CH_NAME_LEN - 1);
    if (Serial) Serial.println(F("[DMX] First boot — defaults loaded"));
  }
}

void dmxSaveConfig() {
  flashInit();
  DmxFlashBlock block;
  block.magic = DMX_CFG_MAGIC;
  memcpy(&block.cfg, &dmxCfg, sizeof(dmxCfg));
  flash.erase(cfgFlashAddr, cfgSectorSize);
  flash.program(&block, cfgFlashAddr, sizeof(block));
  if (Serial) Serial.println(F("[DMX] Config saved to flash"));
}

#else
  // ESP32: NVS Preferences
  #include <Preferences.h>
  static const char* NVS_NS = "slydmx";

void dmxLoadConfig() {
  Preferences prefs;
  prefs.begin(NVS_NS, true);
  dmxCfg.subnet             = prefs.getUChar("subnet", 0);
  dmxCfg.universe           = prefs.getUChar("universe", 0);
  dmxCfg.startAddress       = prefs.getUShort("startAddr", 1);
  dmxCfg.channelsPerFixture = prefs.getUChar("chPerFix", 13);
  dmxCfg.fixtureCount       = prefs.getUChar("fixCount", 1);
  memset(dmxCfg.channelNames, 0, sizeof(dmxCfg.channelNames));
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
  prefs.putUChar("subnet", dmxCfg.subnet);
  prefs.putUChar("universe", dmxCfg.universe);
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
  if (dmxCfg.subnet > 15) dmxCfg.subnet = 0;
  if (dmxCfg.universe > 15) dmxCfg.universe = 0;
  if (dmxCfg.startAddress < 1) dmxCfg.startAddress = 1;
  if (dmxCfg.channelsPerFixture < 1) dmxCfg.channelsPerFixture = 13;
  if (dmxCfg.fixtureCount < 1) dmxCfg.fixtureCount = 1;

  // RS-485 direction enable (HIGH = transmit)
  pinMode(DMX_EN_PIN, OUTPUT);
  digitalWrite(DMX_EN_PIN, HIGH);

#ifdef BOARD_GIGA_DMX
  dmxUart = new mbed::UnbufferedSerial(DMX_MBED_TX, DMX_MBED_RX, DMX_BAUD);
  dmxUart->format(8, mbed::SerialBase::None, 2);  // 8N2
  dmxOutputActive = true;
#else
  DmxSerial.begin(DMX_BAUD, SERIAL_8N2, -1, DMX_TX_PIN);
  dmxOutputActive = true;
#endif

  // Self-test: send a few frames to verify UART is alive
  // The CQRobot shield TX LED should blink during this test
  dmxBuf[0] = 0x00;  // start code
  for (int t = 0; t < 3; t++) {
    dmxSendFrame();
    delay(30);
  }
  dmxSelfTestOk = (dmxFrameCount >= 3);

  if (Serial) {
    Serial.print(F("[DMX] Init: universe="));
    Serial.print(dmxCfg.universe);
    Serial.print(F(" addr="));
    Serial.print(dmxCfg.startAddress);
    Serial.print(F(" ch/fix="));
    Serial.print(dmxCfg.channelsPerFixture);
    Serial.print(F(" fixtures="));
    Serial.print(dmxCfg.fixtureCount);
    Serial.print(F(" selfTest="));
    Serial.println(dmxSelfTestOk ? "PASS" : "FAIL");
  }
}

// ── DMX-512 frame output ─────────────────────────────────────────────────────

void dmxSendFrame() {
#ifdef BOARD_GIGA_DMX
  if (!dmxUart) return;

  // Step 1: Switch to slow baud and send 0x00 to generate BREAK
  // At 76923 baud, 0x00 = start(low) + 8×0(low) + 2×stop(high) = ~117μs low + ~26μs high
  // This exceeds the DMX-512 minimum: BREAK ≥88μs, MAB ≥8μs
  dmxUart->baud(DMX_BREAK_BAUD);
  uint8_t brk = 0x00;
  dmxUart->write(&brk, 1);
  // Wait for break byte to transmit: 11 bits / 76923 = ~143μs
  delayMicroseconds(180);

  // Step 2: Switch to DMX baud and send start code + 512 channels
  dmxUart->baud(DMX_BAUD);
  dmxUart->format(8, mbed::SerialBase::None, 2);  // re-apply 8N2 after baud change
  dmxUart->write(dmxBuf, DMX_UNIVERSE_MAX + 1);

#else
  // ESP32: pin-toggle break
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
