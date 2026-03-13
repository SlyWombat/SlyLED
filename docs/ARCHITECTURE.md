# SlyLED Architecture

## Overview

SlyLED is a single sketch that compiles for three boards. Board-specific sections are wrapped in `#ifdef BOARD_GIGA` / `#ifdef BOARD_ESP32` / `#ifdef BOARD_D1MINI`. The HTTP/SPA/log/NTP code (~80% of the file) is shared and identical across all boards.

## Board detection

```cpp
#if defined(ESP32)
  #define BOARD_ESP32
#elif defined(ESP8266) || defined(ARDUINO_ARCH_ESP8266)
  #define BOARD_D1MINI
#elif defined(ARDUINO_GIGA) || ...
  #define BOARD_GIGA
#endif

#if defined(BOARD_ESP32) || defined(BOARD_D1MINI)
  #define BOARD_FASTLED   // both use FastLED
#endif
```

## Threading model

Each board uses a different approach to keep LED animation independent of WiFi I/O:

### Giga — Mbed RTOS dedicated thread

```
┌─────────────────────────────────────┐  ┌─────────────────────────────────────┐
│           LED Thread                │  │           Main Thread               │
│  (rtos::Thread ledThread)           │  │           (loop())                  │
│                                     │  │                                     │
│  ledTask()                          │  │  handleClient()                     │
│    ├── Siren: red/blue toggle       │  │    ├── serveClient(client, 500ms)   │
│    ├── Rainbow: hue cycle + PWM     │  │    └── drain parallel connections   │
│    └── Off: pins HIGH, sleep        │  │                                     │
│                                     │  │  printStatus() [serial debug]       │
│  Owns: all digitalWrite / pwmCycle  │  │  Owns: all WiFi / HTTP              │
└──────────────┬──────────────────────┘  └──────────────┬──────────────────────┘
               │  reads volatile flags                   │  writes volatile flags
               └──────────────┬──────────────────────────┘
                              │
                   volatile bool ledRainbowOn
                   volatile bool ledSirenOn
```

Bool writes are atomic on ARM Cortex-M7. `volatile` prevents register caching. No mutex required.

### ESP32 — FreeRTOS task pinned to Core 0

```
Core 0                          Core 1
──────────────────────────      ──────────────────────────
ledTask(void*)                  loop()
  fill_rainbow / fill_solid       handleClient()
  FastLED.show()                  printStatus()
  delay(20)
```

WiFi stack runs on Core 1 (Arduino default). LED task pinned to Core 0 via `xTaskCreatePinnedToCore()`. Zero interference between animation and network I/O.

### D1 Mini — non-blocking loop (single core)

The ESP8266 is single-core; FreeRTOS task pinning is not available. `updateLED()` is called from `loop()` on every iteration using `millis()`-based timing:

```
loop():
  printStatus()
  updateLED()      ← non-blocking; advances animation if RAINBOW_DELAY elapsed
  handleClient()   ← may block briefly while serving a request
  yield()          ← feeds ESP8266 WiFi/OS scheduler
```

`updateLED()` is also called inside the HTTP client wait loops and the post-response drain delay, so animation continues even while serving requests.

## Web interface

The board serves a **Single Page Application** (SPA). The browser loads HTML/CSS/JS once, then communicates via `XMLHttpRequest`:

- **`/status`** — polled every 2 s to update badges and header
- **`/led/on`**, **`/led/siren/on`**, **`/led/off`** — button press handlers

```
Browser                           Board
  │                                   │
  │  GET /                            │
  │──────────────────────────────────>│  sendMain() — full SPA HTML
  │<──────────────────────────────────│
  │                                   │
  │  XHR GET /status  (every 2 s)     │
  │──────────────────────────────────>│  sendStatus() — JSON + Content-Length
  │<──────────────────────────────────│
  │                                   │
  │  XHR POST /led/siren/on           │  serveClient():
  │──────────────────────────────────>│    ledRainbowOn = false
  │                                   │    ledSirenOn   = true
  │  {"ok":true}                      │    addLog(FEAT_SIREN, ...)
  │<──────────────────────────────────│    sendJsonOk()  ← Content-Length: 11
```

JSON responses (`sendJsonOk`, `sendStatus`) include a `Content-Length` header. This lets HTTP clients read the exact byte count without waiting for the connection to close, which avoids issues with ESP8266's RST-on-close behaviour.

## Module pattern

Each controllable LED behaviour is a **module**. The current module is **LED** (onboard RGB on Giga, WS2812B strip on ESP32/D1 Mini) with two patterns (Rainbow, Siren).

Each module requires:
- One or more `volatile bool` state flags
- A route in `serveClient()` that sets/clears the flags and calls `addLog()`
- Board-specific animation code in `ledTask()` / `updateLED()`
- A card in `sendMain()` with pattern rows, badges, and buttons
- A JSON field in `sendStatus()`

See [PATTERNS.md](PATTERNS.md) for a step-by-step guide.

## Event log

Circular buffer of 50 `LogEntry` structs:

```cpp
struct LogEntry {
  unsigned long epoch;    // Unix timestamp (NTP-synced)
  uint8_t       ip[4];   // Client IP (0.0.0.0 for boot entries)
  uint8_t       feature; // LedFeature enum: FEAT_NONE / FEAT_RAINBOW / FEAT_SIREN
  LogSource     source;  // SRC_WEB / SRC_BOOT
};
```

Entries are written by `addLog()` (called from `serveClient()` and `setup()`). The log page renders them newest-first.

## Memory usage (v2.6)

### Arduino Giga R1 WiFi
| Resource | Used | Available |
|----------|------|-----------|
| Flash | 277 KB (14%) | 1966 KB |
| SRAM | 63 KB (12%) | 524 KB |

### ESP32 Dev Module
| Resource | Used | Available |
|----------|------|-----------|
| Flash | 1006 KB (78%) | 1280 KB |
| SRAM | 50 KB (15%) | 320 KB |

### LOLIN D1 Mini
| Resource | Used | Available |
|----------|------|-----------|
| Flash (IROM) | 268 KB (25%) | 1024 KB |
| IRAM | 27 KB (91%) | 30 KB |
| RAM | 35 KB (44%) | 80 KB |

IRAM on D1 Mini is high (91%) due to FastLED's clockless driver. Sufficient headroom remains but monitor when adding features.

## File structure

```
main/
  main.ino      — All sketch code; board sections in #ifdef blocks
  version.h     — APP_MAJOR / APP_MINOR
  arduino_secrets.h  — SECRET_SSID / SECRET_PASS (gitignored)
tests/
  test_web.py   — Python test suite (75 tests, board-agnostic)
docs/           — This folder
build.ps1       — Bumps APP_MINOR on upload; -Board giga|esp32|d1mini
arduino-cli.yaml — Sets project root as Arduino user dir (finds ./libraries)
```
