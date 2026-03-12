# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Target hardware

- **Board:** Arduino Giga R1 WiFi
- **FQBN:** `arduino:mbed_giga:giga`
- **Onboard RGB LED pins:** `LEDR` (86), `LEDG` (87), `LEDB` (88) — **active-low** (LOW = on, HIGH = off)

## Build & upload commands

`arduino-cli` is installed at `%LOCALAPPDATA%\Arduino\arduino-cli.exe` (not on PATH — use the full path or add it).

Find the board port:
```powershell
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" board list
```

Compile and upload (the Giga typically appears on COM8):
```powershell
$env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" compile --upload --port COM8 --fqbn arduino:mbed_giga:giga main
```

The `arduino-cli.yaml` config sets this project folder as the Arduino user directory, so `./libraries` is automatically found. Always set `ARDUINO_DIRECTORIES_USER` to the project root before compiling.

**First-time Windows setup:** The Giga's DFU bootloader (USB ID `2341:0366`) requires the WinUSB driver installed via [Zadig](https://zadig.akeo.ie) before uploads will work. Double-press reset to enter bootloader mode, then install the driver once.

markdown## 📚 CRITICAL DOCUMENTATION PATTERN
**ALWAYS ADD IMPORTANT DOCS HERE!** When you create or discover:
- Architecture diagrams → docs/ARCHITECTURE.md
- Database schemas → /docs/DATABASE_ARCHITECTURE.md  
- Problem solutions → /docs/PROBLEM_SOLUTIONS.md
- Setup guides → /docs/SETUP.md
- Feature Requests → /docs/enhancements/README.md

This prevents context loss! Update this file IMMEDIATELY when creating important docs.

## Critical hardware quirks

- **Never use `analogWrite()`** on the onboard LED pins — it crashes Mbed OS (symptom: red LED blinks 4 fast + 4 slow).
- **Use `digitalWrite()` only.** For smooth dimming/fading, implement software PWM: toggle pins in a tight loop with `delayMicroseconds()`.
- **FastLED is not reliable on the Giga R1** (crashes/compatibility issues). The current sketch uses custom `hueToRGB()` + software PWM instead.

## Architecture

The sketch (`main/main.ino`) implements a rainbow cycle on the onboard RGB LED without any external library:

1. `hueToRGB(hue, r, g, b)` — maps hue 0–255 to RGB values using 6 linear segments
2. `pwmCycle(r, g, b)` — one 256-step software PWM cycle (~2 ms total, 8 µs per step); drives active-low pins
3. `setRGBFor(r, g, b)` — repeats `pwmCycle` for `DISPLAY_MS` milliseconds to hold a color visibly
4. `loop()` — steps hue by `HUE_STEP` across the full range, calling `setRGBFor` each step

## Git / GitHub

- Remote: `https://github.com/SlyWombat/Giga-LED-Project`
- After a successful upload, offer to sync: `git add . && git commit -m "<message>" && git push origin main`
- `arduino_secrets.h` is gitignored — never commit credentials or WiFi passwords
- Commit messages should follow the pattern: `feat: <short description of LED behavior change>`


# Arduino Web App Performance Rules

## Core Architectural Principles
- **Offload Static Assets**: Do not embed large HTML/CSS/JS in PROGMEM. Host assets on a CDN or SD card. 
- **Data-Only API**: Use the Arduino as a JSON/XML API endpoint. The web UI should be a Single Page Application (SPA) that fetches only raw data.
- **Minimal TCP Overhead**: Consolidate `client.print()` calls. Buffer responses to reduce the number of packets sent.

## Code Constraints for Memory & Speed
- **Zero Dynamic Allocation**: Strictly avoid `malloc()`, `new`, or `String` objects to prevent heap fragmentation. Use fixed-size `char` buffers.
- **SRAM Optimization**: Force use of the `F()` macro for all literal strings (e.g., `client.print(F("HTTP/1.1 200 OK"));`).
- **Smallest Data Types**: Always use `uint8_t` or `int8_t` for values under 255. Use `const` or `constexpr` for all fixed values.
- **Integer Math Only**: Avoid `float` or `double`. Use fixed-point arithmetic or integer scaling for sensor data.
- **Direct Register I/O**: For high-frequency operations, prefer direct port manipulation over `digitalWrite()`.

## AI Workflow Instructions
- **Check Constraints First**: Before generating code, analyze SRAM and Flash impact.
- **Manual Verification**: Include a step to verify memory usage with `millis()` or free-RAM checking functions.
- **Refactor Cycle**: If code exceeds 500 lines, break it into modular, specialized files.

