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
  #ifdef GIGA_CHILD
    #define BOARD_GIGA_CHILD   // Giga R1 as LED child (onboard RGB LED)
  #else
    #define BOARD_GIGA          // Giga R1 as parent (Orchestrator runtime)
  #endif
#else
  #error "Unsupported board. Target: arduino:mbed_giga:giga | esp32:esp32:esp32 | esp8266:esp8266:d1_mini"
#endif

// BOARD_FASTLED = child with WS2812B/addressable strips via FastLED
#if defined(BOARD_ESP32) || defined(BOARD_D1MINI)
  #define BOARD_FASTLED
#endif

// BOARD_CHILD = any board acting as a child/performer
#if defined(BOARD_FASTLED) || defined(BOARD_GIGA_CHILD)
  #define BOARD_CHILD
#endif

// ── Board-specific includes ───────────────────────────────────────────────────

#ifdef BOARD_GIGA
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_GIGA_CHILD)
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_ESP32)
  #define FASTLED_ALLOW_INTERRUPTS 0
  #include <FastLED.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
  #include <Preferences.h>
#else  // D1 Mini
  // Disable interrupts during FastLED.show() — prevents WiFi IRQs from
  // corrupting WS2812B timing signal (causes random pixel flashes).
  // 150 LEDs × 30μs = 4.5ms interrupt blackout — acceptable.
  #define FASTLED_ALLOW_INTERRUPTS 0
  #include <FastLED.h>
  #include <ESP8266WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
  #include <EEPROM.h>
#endif

// ── LED hardware constants ──────────────────────────────────────────────────

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

#ifdef BOARD_GIGA_CHILD
  // Onboard RGB LED: active-low GPIO pins (LOW = on, HIGH = off)
  #define PIN_LEDR      LEDR
  #define PIN_LEDG      LEDG
  #define PIN_LEDB      LEDB
  #define NUM_LEDS      1         // 1 RGB pixel (the onboard LED)
  #define MAX_LEDS      1
  constexpr uint8_t LED_BRIGHTNESS = 255;
#endif

#endif  // BOARDCONFIG_H
