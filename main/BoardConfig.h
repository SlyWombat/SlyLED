/*
 * BoardConfig.h — Board detection, board-specific includes, LED hardware constants.
 *
 * Include this first in every header and .cpp that needs board awareness.
 */

#ifndef BOARDCONFIG_H
#define BOARDCONFIG_H

// ── Board detection ───────────────────────────────────────────────────────────

#if defined(ESP32) && defined(DMX_BRIDGE)
  #define BOARD_ESP32
  #define BOARD_DMX_BRIDGE
#elif defined(ESP32)
  #define BOARD_ESP32
#elif defined(ESP8266) || defined(ARDUINO_ARCH_ESP8266)
  #define BOARD_D1MINI
#elif defined(ARDUINO_GIGA) || defined(ARDUINO_ARDUINO_GIGA) || \
      defined(ARDUINO_ARCH_MBED_GIGA) || defined(ARDUINO_ARCH_MBED)
  #ifdef GIGA_DMX
    #define BOARD_GIGA_DMX       // Giga R1 as DMX bridge
    #define BOARD_DMX_BRIDGE     // shared DMX bridge logic
  #elif defined(GIGA_CHILD)
    #define BOARD_GIGA_CHILD   // Giga R1 as LED child (onboard RGB LED)
  #else
    #define BOARD_GIGA          // Giga R1 as parent (Orchestrator runtime)
  #endif
#else
  #error "Unsupported board. Target: arduino:mbed_giga:giga | esp32:esp32:esp32 | esp8266:esp8266:d1_mini"
#endif

// BOARD_FASTLED = child with WS2812B/addressable strips via FastLED
#if (defined(BOARD_ESP32) || defined(BOARD_D1MINI)) && !defined(BOARD_DMX_BRIDGE)
  #define BOARD_FASTLED
#endif

// BOARD_CHILD = any board acting as a child/performer
#if defined(BOARD_FASTLED) || defined(BOARD_GIGA_CHILD) || defined(BOARD_DMX_BRIDGE)
  #define BOARD_CHILD
#endif

// Giga DMX shares Giga child's mbed/WiFi stack
#ifdef BOARD_GIGA_DMX
  #define BOARD_GIGA_CHILD_OR_DMX
#endif
#ifdef BOARD_GIGA_CHILD
  #define BOARD_GIGA_CHILD_OR_DMX
#endif

// ── Board-specific includes ───────────────────────────────────────────────────

#ifdef BOARD_GIGA
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_GIGA_CHILD) || defined(BOARD_GIGA_DMX)
  #include <mbed.h>
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
#elif defined(BOARD_DMX_BRIDGE)
  #include <WiFi.h>
  #include <WiFiUdp.h>
  #include <time.h>
  #include <Preferences.h>
  #include <HardwareSerial.h>
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
  #define LED_TYPE      WS2812B
  #define COLOR_ORDER   GRB
  constexpr uint8_t LED_BRIGHTNESS = 200;
  #ifdef BOARD_ESP32
    #define MAX_LEDS    255       // max per string — 765 bytes CRGB
    // DATA_PIN removed — each string uses its own GPIO from config
  #else
    #define DATA_PIN      2       // D1 Mini: hardcoded GPIO 2
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

#ifdef BOARD_DMX_BRIDGE
  // DMX-512 constants (shared by ESP32 and Giga DMX bridges)
  constexpr uint16_t DMX_UNIVERSE_MAX = 512; // DMX-512 channels per universe
  constexpr uint32_t DMX_BAUD        = 250000;
  constexpr uint8_t  DMX_FRAME_HZ    = 40;  // output frame rate
  // Virtual LED array for action system compatibility
  #define NUM_LEDS  170    // 512/3 = 170 RGB fixtures max
  #define MAX_LEDS  170
  constexpr uint8_t LED_BRIGHTNESS = 255;

  #ifdef BOARD_GIGA_DMX
    // Giga R1: Serial1 on TX1(pin 1)/RX1(pin 0), DE/RE on digital pin 2
    constexpr uint8_t DMX_EN_PIN = 2;
  #else
    // ESP32: UART2 TX on GPIO17, DE/RE on GPIO4
    constexpr uint8_t DMX_TX_PIN = 17;
    constexpr uint8_t DMX_EN_PIN = 4;
  #endif
#endif

#endif  // BOARDCONFIG_H
