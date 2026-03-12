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

The sketch (`main/main.ino`) uses a **two-thread Mbed RTOS architecture** with a **SPA + JSON API** web interface.

### Threading model

| Thread | Responsibility |
|--------|---------------|
| **LED thread** (`rtos::Thread ledThread`) | All `digitalWrite` / PWM calls — never touches WiFi |
| **Main thread** (`loop()`) | All WiFi / HTTP — never touches LED pins |

Shared state is `volatile bool ledRainbowOn` and `volatile bool ledSirenOn`. Bool writes are atomic on ARM Cortex-M7; `volatile` prevents the compiler from caching values in registers. Requires `#include <mbed.h>`.

This eliminates the two classic problems of single-threaded LED+WiFi sketches:
- **No rainbow blanks** — network I/O no longer interrupts the PWM loop
- **No siren phase stalls** — `handleClient()` blocking never affects phase timing

### HTTP routes

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | Full SPA (HTML + CSS + JS) |
| GET | `/status` | `{"onboard_led":{"active":bool,"feature":"rainbow\|siren\|none"}}` |
| POST | `/led/on` | `{"ok":true}` — enable Rainbow |
| POST | `/led/siren/on` | `{"ok":true}` — enable Siren (disables Rainbow) |
| POST | `/led/off` | `{"ok":true}` — disable all |
| GET | `/log` | Event log HTML page |
| GET | `/favicon.ico` | 404 (fast, keeps connection slot free) |

### SPA UI structure

- **Header (`#hdr`)** — app name + `#hdr-status` line; auto-updates via `/status` poll every 2 s
- **Onboard LED card** — one row per pattern (Rainbow, Siren); each row has a badge (`id='badge-rainbow'` / `id='badge-siren'`) and an Enable button; single Disable button turns off all patterns
- **Footer** — version string from `APP_MAJOR`/`APP_MINOR`
- **View Log** anchor — `<a href='/log'>` (plain GET)

### Key functions

| Function | Purpose |
|----------|---------|
| `ledTask()` | LED thread body — runs Rainbow, Siren, or off state; owns all pin writes |
| `hueToRGB(hue, r, g, b)` | Maps hue 0–255 to RGB using 6 linear segments |
| `pwmCycle(r, g, b)` | One 256-step software PWM cycle (~2 ms); drives active-low pins |
| `setRGBFor(r, g, b)` | Repeats `pwmCycle` for `DISPLAY_MS` ms to hold a colour visibly |
| `serveClient(client, waitMs)` | Reads first request line, routes to correct handler; never writes LED pins |
| `handleClient()` | Accepts client (500 ms patience), drains parallel connections (favicon, XHR) |
| `loop()` | Main thread: `printStatus()` + `handleClient()` + `delay(10)` |

### Onboard LED patterns

| Pattern | Route to enable | Behaviour |
|---------|----------------|-----------|
| **Rainbow** | `POST /led/on` | Smooth hue cycle via software PWM |
| **Siren** | `POST /led/siren/on` | Alternating red / blue, 350 ms per phase |

Enabling one pattern automatically disables the other (`ledRainbowOn` and `ledSirenOn` are mutually exclusive — enforced in `serveClient`).

### Module state

`volatile bool ledRainbowOn`, `volatile bool ledSirenOn` — LED thread reads these on every iteration; main thread writes them in `serveClient()`. Adding a new pattern means: add a `volatile bool`, add a route in `serveClient()`, add a pattern row in `sendMain()`, add a branch in `ledTask()`.

**Event log:** Circular buffer (50 entries) stores timestamp, `LedFeature` (FEAT_NONE / FEAT_RAINBOW / FEAT_SIREN), source (Boot/Web), and client IP. NTP-synced timestamps via `pool.ntp.org`.

**Test suite:** `python tests/test_web.py [host]` — run before every upload. From WSL use `powershell.exe -Command "python -X utf8 tests/test_web.py 192.168.10.219"`.

## Git / GitHub

- Remote: `https://github.com/SlyWombat/Giga-LED-Project`
- After a successful upload, offer to sync: `git add . && git commit -m "<message>" && git push origin main`
- `arduino_secrets.h` is gitignored — never commit credentials or WiFi passwords
- Commit messages follow: `feat: <short description>`


# Arduino Web App Performance Rules

## Core Architectural Principles
- **Data-Only API**: Use the Arduino as a JSON/XML API endpoint. The web UI should be a Single Page Application (SPA) that fetches only raw data.
- **Minimal TCP Overhead**: Consolidate `client.print()` calls. Buffer responses to reduce the number of packets sent.

## Code Constraints for Memory & Speed
- **Zero Dynamic Allocation**: Strictly avoid `malloc()`, `new`, or `String` objects to prevent heap fragmentation. Use fixed-size `char` buffers.
- **SRAM Optimization**: Force use of the `F()` macro for all literal strings (e.g., `client.print(F("HTTP/1.1 200 OK"));`).
- **Smallest Data Types**: Always use `uint8_t` or `int8_t` for values under 255. Use `const` or `constexpr` for all fixed values.
- **Integer Math Only**: Avoid `float` or `double`. Use fixed-point arithmetic or integer scaling for sensor data.

## AI Workflow Instructions
- **Check Constraints First**: Before generating code, analyze SRAM and Flash impact.
- **Refactor Cycle**: If code exceeds 500 lines, break it into modular, specialized files.

## Known Arduino Giga / Mbed GCC quirks
- **Auto-prototype generator** fails on functions whose parameters use `enum` types — use `uint8_t` in the signature and cast internally (e.g. `e.source = (LogSource)src`).
- **`static` functions** can conflict with auto-generated prototypes — omit `static` from sketch-level functions.
- **`Serial.print()` blocks forever** on Mbed OS if no USB CDC terminal is connected — guard every print with `if (Serial)`.
- **`WiFi.setHostname()`** must be called *before* `WiFi.begin()` so the hostname appears in DHCP DISCOVER/REQUEST packets (option 12).
- **Browser prefetch / favicon race**: Chrome/Edge open a second TCP connection for `favicon.ico` when loading any page. Fixed by the **SPA+AJAX architecture** — buttons use `XMLHttpRequest`, no page navigation, no favicon request on button press.
- **`rtos::Thread` requires `#include <mbed.h>`** — not pulled in automatically by Arduino.h on the Giga.
- **`volatile bool` for cross-thread state** — bool writes are atomic on Cortex-M7; `volatile` prevents register caching. Sufficient for simple flag sharing between two threads without a mutex.
