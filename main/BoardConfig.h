/*
 * BoardConfig.h — Board detection, board-specific includes, LED hardware constants.
 *
 * Include this first in every header and .cpp that needs board awareness.
 */

#ifndef BOARDCONFIG_H
#define BOARDCONFIG_H

// ── Board detection ───────────────────────────────────────────────────────────

#if defined(ESP32)
  #define BOARD_ESP32
#elif defined(ESP8266) || defined(ARDUINO_ARCH_ESP8266)
  #define BOARD_D1MINI
#elif defined(ARDUINO_GIGA) || defined(ARDUINO_ARDUINO_GIGA) || \
      defined(ARDUINO_ARCH_MBED_GIGA) || defined(ARDUINO_ARCH_MBED)
  #define BOARD_GIGA
#else
  #error "Unsupported board. Target: arduino:mbed_giga:giga | esp32:esp32:esp32 | esp8266:esp8266:d1_mini"
#endif

#if defined(BOARD_ESP32) || defined(BOARD_D1MINI)
  #define BOARD_FASTLED
#endif

// ── Board-specific includes ───────────────────────────────────────────────────

#ifdef BOARD_GIGA
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_ESP32)
  #include <FastLED.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
  #include <Preferences.h>
#else  // D1 Mini
  #include <FastLED.h>
  #include <ESP8266WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
  #include <EEPROM.h>
#endif

// ── LED hardware constants (FastLED children only) ────────────────────────────

#ifdef BOARD_FASTLED
  #define DATA_PIN      2
  #define LED_TYPE      WS2812B
  #define COLOR_ORDER   GRB
  constexpr uint8_t LED_BRIGHTNESS = 200;
  #ifdef BOARD_ESP32
    #define MAX_LEDS    255       // max per string — 765 bytes CRGB
  #else
    #define MAX_LEDS    150       // D1 Mini — 450 bytes, fits in 80K RAM
  #endif
  #define NUM_LEDS MAX_LEDS       // FastLED array size (runtime count from EEPROM)
#endif

#endif  // BOARDCONFIG_H
