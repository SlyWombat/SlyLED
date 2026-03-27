# LED Action Types — v5.1

SlyLED supports 14 action types, all implemented in `main/ChildLED.cpp` with per-type render functions. Actions are dispatched from the Orchestrator via UDP `CMD_ACTION` (immediate) or `CMD_LOAD_STEP` (runner steps).

## Action type reference

| Type | Name | Render function | Key parameters |
|------|------|-----------------|----------------|
| 0 | Blackout | — | All LEDs off |
| 1 | Solid | `renderSolid()` | `r, g, b` |
| 2 | Fade | `renderFade()` | `r, g, b, r2, g2, b2, speedMs` |
| 3 | Breathe | `renderBreathe()` | `r, g, b, periodMs, minBri` |
| 4 | Chase | `renderChase()` | `r, g, b, speedMs, spacing, direction` |
| 5 | Rainbow | `renderRainbow()` | `speedMs, paletteId (0–7), direction` |
| 6 | Fire | `renderFire()` | `speedMs, cooling, sparking` |
| 7 | Comet | `renderComet()` | `r, g, b, speedMs, tailLen, direction, decay` |
| 8 | Twinkle | `renderTwinkle()` | `r, g, b, spawnMs, density, fadeSpeed` |
| 9 | Strobe | `renderStrobe()` | `r, g, b, onMs, offMs` |
| 10 | Wipe | `renderWipeSeq()` | `r, g, b, speedMs, wipeDir` |
| 11 | Scanner | `renderScanner()` | `r, g, b, speedMs, tailLen` |
| 12 | Sparkle | `renderSparkle()` | `r, g, b, spawnMs, fadeSpeed` |
| 13 | Gradient | `renderGradient()` | `r, g, b, r2, g2, b2, direction` |

## Rainbow palettes (type 5)

| paletteId | Name |
|-----------|------|
| 0 | Classic (full spectrum) |
| 1 | Ocean (blue–teal–green) |
| 2 | Lava (red–orange–yellow) |
| 3 | Forest (green–teal) |
| 4 | Party (pink–purple–blue) |
| 5 | Heat (red–yellow–white) |
| 6 | Cool (blue–cyan–white) |
| 7 | Pastel (soft pastels) |

## Protocol wire format

Actions are sent via the `ActionPayload` struct (26 bytes):

```
type(1) + r/g/b(3) + p16a(2) + p8a/p8b/p8c/p8d(4) + ledStart[8] + ledEnd[8]
```

Generic parameters (`p16a`, `p8a`–`p8d`) are reinterpreted per action type — see `Protocol.h` for the per-type field mapping.

## Board-specific rendering

### ESP32 — FreeRTOS Core 0 task

LED rendering runs in `ledTask()` on Core 0, separate from WiFi/HTTP on Core 1. Uses `FastLED.show()` with hardware RMT peripheral — **never use `noInterrupts()`** around `show()` as it triggers Interrupt WDT with WiFi active.

Per-string GPIO pin assignments loaded from NVS after WiFi connect (`esp32InitLeds()`).

### D1 Mini — non-blocking `updateLED()`

Single-threaded. `updateLED()` called from `loop()` and inside HTTP wait loops. Uses `millis()` timing — no `delay()`. `showSafe()` wraps `FastLED.show()` with `noInterrupts()`/`interrupts()` for bit-banged WS2812B timing on GPIO 2.

### Giga Child — software PWM

Onboard RGB LED only (1 pixel). `GigaLED.h/cpp` provides CRGB-compatible interface with `showSafe()` using `digitalWrite()` + `delayMicroseconds()` software PWM. Active-low pins (LOW = on).

## Runner execution

Actions are sequenced into **runners** (ordered step lists). Each step references an action by ID and has a duration in seconds. Runners are synced to performers via `CMD_LOAD_STEP` packets, then started with `CMD_RUNNER_GO` at an NTP-synced epoch.

**Priority:** runner active > immediate action > idle black.

**Flights** group a runner with performers. **Shows** sequence multiple flights for coordinated playback.

## Software PWM reference (Giga only)

The Giga onboard LED pins do not support hardware PWM — `analogWrite()` crashes. All brightness control uses software PWM:

```
pwmCycle(r, g, b):
  for step in 0..255:
    LEDR = (r > step) ? LOW : HIGH   // active-low
    LEDG = (g > step) ? LOW : HIGH
    LEDB = (b > step) ? LOW : HIGH
    wait 8 µs
```

One cycle = 256 × 8 µs = **2.048 ms**. At ~488 Hz the PWM is well above the flicker fusion threshold (~50 Hz).

## FastLED reference (ESP32 / D1 Mini)

Both boards use FastLED with WS2812B LEDs, colour order GRB. LED count and GPIO pin configured per-string in child config.

```cpp
FastLED.addLeds<WS2812B, pin, GRB>(leds, ledCount);
```

ESP32 supports multi-pin init via `esp32InitLeds()` — up to 8 strings on different GPIOs. D1 Mini: single pin (GPIO 2), up to 2 strings.
