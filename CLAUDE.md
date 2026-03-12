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

Compile and upload using the build script (auto-increments minor version):
```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

Or manually (the Giga appears on COM7):
```powershell
$env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" compile --upload --port COM7 --fqbn arduino:mbed_giga:giga main
```

The `arduino-cli.yaml` config sets this project folder as the Arduino user directory, so `./libraries` is automatically found. Always set `ARDUINO_DIRECTORIES_USER` to the project root before compiling.

**First-time Windows setup:** The Giga's DFU bootloader (USB ID `2341:0366`) requires the WinUSB driver installed via [Zadig](https://zadig.akeo.ie) before uploads will work. Double-press reset to enter bootloader mode, then install the driver once.

**Versioning:** `main/version.h` holds `APP_MAJOR` / `APP_MINOR`. `build.ps1` increments `APP_MINOR` automatically on every compile.

## Critical hardware quirks

- **Never use `analogWrite()`** on the onboard LED pins — it crashes Mbed OS (symptom: red LED blinks 4 fast + 4 slow).
- **Use `digitalWrite()` only.** For smooth dimming/fading, implement software PWM: toggle pins in a tight loop with `delayMicroseconds()`.
- **FastLED is not reliable on the Giga R1** (crashes/compatibility issues). The current sketch uses custom `hueToRGB()` + software PWM instead.

## Architecture

The sketch (`main/main.ino`) is a **SPA + JSON API** design. The browser loads a single HTML page and communicates with the board via `XMLHttpRequest` — no page navigation on button press, no favicon race condition.

### HTTP routes

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | Full SPA (HTML + CSS + JS) |
| GET | `/status` | `{"onboard_led":{"active":true/false}}` |
| POST | `/led/on` | `{"ok":true}` — enable rainbow |
| POST | `/led/off` | `{"ok":true}` — disable rainbow |
| GET | `/log` | Event log HTML page |
| GET | `/favicon.ico` | 404 (fast, keeps connection slot free) |

### SPA UI structure

- **Header (`#hdr`)** — app name + `#hdr-status` line; auto-updates via `/status` poll every 2 s
- **Module cards** — one card per module (currently: Onboard LED / Rainbow). Each card has an `Enable`/`Disable` button that calls `setLed(1/0)` via XHR; response updates badge + header immediately
- **Footer** — version string from `APP_MAJOR`/`APP_MINOR`
- **View Log** anchor — `<a href='/log'>` (plain GET; no state side-effect)

### Key functions

1. `hueToRGB(hue, r, g, b)` — maps hue 0–255 to RGB using 6 linear segments
2. `pwmCycle(r, g, b)` — one 256-step software PWM cycle (~2 ms); drives active-low pins
3. `setRGBFor(r, g, b)` — repeats `pwmCycle` for `DISPLAY_MS` ms to hold a color visibly
4. `serveClient(client, waitMs)` — reads first request line, routes to correct handler
5. `handleClient()` — accepts client with 500 ms patience, then drains any additional parallel connections (favicon, XHR) in a tight loop
6. `loop()` — steps hue across 0–255, calling `setRGBFor` + `handleClient` each step

### Module state

`bool ledRainbowOn` — global controlling whether the rainbow pattern runs. New modules add their own state variables and a matching card in `sendMain()`.

**Event log:** Circular buffer (50 entries) stores timestamp, on/off state, source (Boot/Web), and client IP. NTP-synced timestamps via `pool.ntp.org`.

**Test suite:** `python tests/test_web.py [host]` — run before every upload. From WSL use `powershell.exe -Command "python -X utf8 tests/test_web.py 192.168.10.219"`.

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

## Known Arduino Giga / Mbed GCC quirks
- **Auto-prototype generator** fails on functions whose parameters use `enum` types — use `uint8_t` in the signature and cast internally (e.g. `e.source = (LogSource)src`).
- **`static` functions** can conflict with auto-generated prototypes — omit `static` from sketch-level functions.
- **`Serial.print()` blocks forever** on Mbed OS if no USB CDC terminal is connected — guard every print with `if (Serial)`.
- **`WiFi.setHostname()`** must be called *before* `WiFi.begin()` so the hostname appears in DHCP DISCOVER/REQUEST packets (option 12). Calling it after `begin()` means the first DHCP handshake goes out without the hostname.
- **Browser prefetch / favicon race**: Chrome/Edge open a second TCP connection for `favicon.ico` when loading any page. With a single-slot WiFiServer this consumed the connection slot before button responses could be served. Fixed by the **SPA+AJAX architecture** — buttons use `XMLHttpRequest`, no page navigation, no favicon request on button press.

