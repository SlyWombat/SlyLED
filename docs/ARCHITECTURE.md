# SlyLED Architecture

## Overview

SlyLED runs on the Arduino Giga R1 WiFi (STM32H747, dual Cortex-M7 + M4). The sketch runs entirely on the M7 core under Mbed OS and uses two Mbed RTOS threads to decouple LED animation from WiFi I/O.

## Threading model

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

**Why two threads?**
In a single-threaded sketch, `handleClient()` blocks for up to 500 ms waiting for an HTTP request. During that time the LED pins are held at whatever state they were last left in — causing visible blanks in the rainbow and irregular siren phase timing. The RTOS thread runs the animation loop continuously regardless of what the main thread is doing.

**Thread safety:**
Bool writes are atomic on ARM Cortex-M7 (single-instruction store). `volatile` prevents the compiler from caching the value in a register between loop iterations. No mutex is required for simple flag reads/writes between two threads.

## Web interface

The board serves a **Single Page Application** (SPA). The browser loads the full HTML/CSS/JS once, then communicates via `XMLHttpRequest`:

- **`/status`** — polled every 2 s to update badges and header
- **`/led/on`**, **`/led/siren/on`**, **`/led/off`** — button press handlers

No page navigation occurs on button press. This eliminates the favicon race condition that plagued earlier form-POST architectures (see [HARDWARE.md](HARDWARE.md#wifi-server-quirks)).

```
Browser                           Board (M7)
  │                                   │
  │  GET /                            │
  │──────────────────────────────────>│  sendMain() — full SPA HTML
  │<──────────────────────────────────│
  │                                   │
  │  XHR GET /status  (every 2 s)     │
  │──────────────────────────────────>│  sendStatus() — JSON
  │<──────────────────────────────────│
  │                                   │
  │  XHR POST /led/siren/on           │  serveClient():
  │──────────────────────────────────>│    ledRainbowOn = false
  │                                   │    ledSirenOn   = true  ← LED thread picks up
  │  {"ok":true}                      │    addLog(FEAT_SIREN, ...)
  │<──────────────────────────────────│    sendJsonOk()
  │                                   │
  │  XHR GET /status                  │
  │──────────────────────────────────>│  {"onboard_led":{"active":true,"feature":"siren"}}
  │<──────────────────────────────────│
```

## Module pattern

Each controllable LED behaviour is a **module**. The current module is **Onboard LED** with two patterns (Rainbow, Siren). Future modules could control external LEDs wired to GPIO pins.

Each module requires:
- One or more `volatile bool` state flags
- A route in `serveClient()` that sets/clears the flags and calls `addLog()`
- A branch in `ledTask()` that runs the animation when the flag is set
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

Entries are written by `addLog()` (called from `serveClient()` and `setup()`). The log page renders them newest-first with columns: #, Timestamp, Feature, State, Source, IP.

## Memory usage (v2.0 / sketch v1.17)

| Resource | Used | Available |
|----------|------|-----------|
| Flash | 277 KB (14%) | 1966 KB |
| SRAM | 63 KB (12%) | 524 KB |

Flash is dominated by the embedded SPA HTML/CSS/JS. SRAM is dominated by the log buffer (50 × ~20 bytes = ~1 KB) and WiFi stack (~50 KB).

## File structure

```
main/
  main.ino      — All sketch code (single file, <450 lines)
  version.h     — APP_MAJOR / APP_MINOR
  arduino_secrets.h  — SECRET_SSID / SECRET_PASS (gitignored)
tests/
  test_web.py   — Python test suite (75 tests, no dependencies beyond stdlib)
docs/           — This folder
build.ps1       — Bumps APP_MINOR, compiles, uploads via arduino-cli
arduino-cli.yaml — Sets project root as Arduino user dir (finds ./libraries)
```
