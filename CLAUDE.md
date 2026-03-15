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

Or manually — set `ARDUINO_DIRECTORIES_USER` first so `./libraries` is found:
```powershell
$env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
# Giga R1 WiFi (COM7):
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" compile --upload --port COM7 --fqbn arduino:mbed_giga:giga main
# ESP32:
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" compile --fqbn esp32:esp32:esp32 main
# D1 Mini:
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" compile --fqbn esp8266:esp8266:d1_mini main
```

**First-time Windows setup:** The Giga's DFU bootloader (USB ID `2341:0366`) requires the WinUSB driver installed via [Zadig](https://zadig.akeo.ie) before uploads will work. Double-press reset to enter bootloader mode, then install the driver once.

**Versioning:** `main/version.h` holds `APP_MAJOR` / `APP_MINOR`. `build.ps1` increments `APP_MINOR` automatically on every compile.

## Critical hardware quirks

- **Never use `analogWrite()`** on the onboard LED pins — it crashes Mbed OS (symptom: red LED blinks 4 fast + 4 slow).
- **Use `digitalWrite()` only.** For smooth dimming/fading, implement software PWM: toggle pins in a tight loop with `delayMicroseconds()`.
- **FastLED is not reliable on the Giga R1** (crashes/compatibility issues). The current sketch uses custom `hueToRGB()` + software PWM instead.

## System architecture (three-tier)

```
Windows / Mac parent (Flask)          ← primary design + control UI
    desktop/shared/parent_server.py
    desktop/shared/spa/index.html
    desktop/windows/run.ps1  (Windows launcher)
    desktop/mac/run.sh        (Mac launcher)
         │  UDP port 4210 binary protocol
         ▼
ESP32 / D1 Mini children              ← LED execution nodes
    (managed via Setup tab, UDP PING/PONG/ACTION/LOAD_STEP)
```

**Giga board role (optional):** The Giga R1 can optionally act as a runtime parent once a layout and runners have been designed on Windows. In that role it runs a minimal SPA (status + start/stop only) and the same UDP child protocol, but without the full design UI.

### Desktop parent files

| Path | Purpose |
|------|---------|
| `desktop/shared/parent_server.py` | Flask server — all `/api/*` routes + UDP child protocol |
| `desktop/shared/spa/index.html` | Full SPA (identical logic to Giga embedded version) |
| `desktop/shared/data/` | JSON persistence (children, layout, runners, settings) — gitignored |
| `desktop/windows/run.ps1` | PowerShell launcher — installs deps, starts server |
| `desktop/windows/requirements.txt` | `flask>=3.0` |
| `desktop/mac/run.sh` | Bash launcher — installs deps, starts server |
| `desktop/mac/requirements.txt` | `flask>=3.0` |

**Running on Windows:** `powershell.exe -ExecutionPolicy Bypass -File desktop\windows\run.ps1`

**Running on Mac:** `bash desktop/mac/run.sh`

### Desktop API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/children` | List / add child nodes |
| GET | `/api/children/<id>/status` | Poll child via UDP STATUS |
| DELETE/POST | `/api/children/<id>` | Remove / update child |
| GET/POST | `/api/children/export` | Bulk export/import JSON |
| GET/POST | `/api/layout` | LED layout config |
| GET/POST | `/api/settings` | App settings (dark mode etc.) |
| POST | `/api/action` | Send ACTION packet to child |
| POST | `/api/action/stop` | Send STOP to all children |
| GET/POST | `/api/actions` | List / create action presets (library) |
| GET/PUT/DELETE | `/api/actions/<id>` | Get / update / delete action preset |
| GET/POST | `/api/runners` | List / create runners |
| GET/PUT/DELETE | `/api/runners/<id>` | Get / update / delete runner |
| POST | `/api/runners/<id>/compute` | Compute runner steps |
| POST | `/api/runners/<id>/sync` | Sync runner to child via LOAD_STEP |
| POST | `/api/runners/<id>/start` | Start runner on child |
| POST | `/api/runners/stop` | Stop all runners |

### UDP binary protocol (port 4210)

All packets share an 8-byte header: `struct.pack("<HBBI", magic=0x534C, version=2, cmd, epoch)`.

| Cmd byte | Name | Direction | Payload |
|---------|------|-----------|---------|
| 0x01 | PING | parent→child | header only |
| 0x02 | PONG | child→parent | 131 bytes — see PONG layout below |
| 0x10 | ACTION | parent→child | 26 bytes: `<BBBBHHBB` (10) + led_start[8] + led_end[8] |
| 0x11 | LOAD_STEP | parent→child | 30 bytes: `<BBBBBBHHBBH` (14) + ls[8] + le[8] |
| 0x20 | RUNNER_GO | parent→child | 4 bytes (uint32_t startEpoch) |
| 0x30 | RUNNER_STOP | parent→child | header only |
| 0x40 | STATUS_REQ | parent→child | header only |
| 0x41 | STATUS_RESP | child→parent | 8 bytes `<BBBBI` (activeAction, runnerActive, currentStep, rssi, uptime) |
| 0x21 | LOAD_ACK | child→parent | 1 byte (step index) |

**PONG payload (131 bytes = total packet 139):**
```
hostname[10]  altName[16]  description[32]  stringCount(1)  PongStrings×8
PongString = <HHBBHB>: ledCount(2) + lengthMm(2) + ledType(1) + cableDir(1) + cableMm(2) + stripDir(1) = 9 bytes
8 × 9 = 72  →  10+16+32+1+72 = 131
```

**wifiRssi** is stored as `uint8_t` absolute magnitude (e.g. 69 → -69 dBm). Check `> 0`.

---

## Child node architecture (ESP32 / D1 Mini)

The same `main/main.ino` sketch compiles for ESP32 and D1 Mini via `#ifdef BOARD_FASTLED`.

### Per-board limits

| Board | `CHILD_MAX_STRINGS` | EEPROM / storage |
|-------|---------------------|-----------------|
| D1 Mini (`BOARD_D1MINI`) | 2 | EEPROM (flash-backed) |
| ESP32 (`BOARD_ESP32`) | 8 | NVS Preferences (`"slyled"` namespace) |

`MAX_STR_PER_CHILD = 8` is a **protocol constant** — all protocol structs (PongPayload, ActionPayload, LoadStepPayload) are sized for 8 strings regardless of board. `CHILD_MAX_STRINGS` only affects EEPROM layout and the config UI.

### HTTP routes (child)

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | 302 redirect → `/config` |
| GET | `/status` | JSON: `{"role":"child","hostname":…,"action":…}` |
| GET | `/config` | 3-tab HTML SPA |
| POST | `/config` | 303 redirect → `/config` (saves to EEPROM) |
| POST | `/config/reset` | 303 redirect → `/config` (factory reset) |
| GET | `/favicon.ico` | 404 |

### Config SPA (3 tabs)

- **Dashboard** — hostname, altName, stringCount (server-rendered); live action status (XHR poll `/status` every 3 s)
- **Settings** (inside `<form id='cf' action='/config' method='POST'>`): `name='an'` altName, `name='desc'` description, `name='sc'` string count (1..`CHILD_MAX_STRINGS`)
- **Config** — string selector dropdown; per-string fieldsets with `lc/lm/lt/sd` (ledCount, lengthMm, ledType, stripDir); no cable fields
- **Factory Reset** — separate `<form id='rf' action='/config/reset' method='POST'>` (never nested inside `cf`)

### ChildSelfConfig struct

```cpp
struct ChildSelfConfig {
    char hostname[HOSTNAME_LEN];        // "SLYC-XXXX" (MAC-derived)
    char altName[CHILD_NAME_LEN];       // defaults to hostname if blank
    char description[CHILD_DESC_LEN];
    uint8_t stringCount;
    ChildStringCfg strings[CHILD_MAX_STRINGS];
};
struct ChildStringCfg {
    uint16_t ledCount; uint16_t lengthMm;
    uint8_t  ledType;  uint8_t  cableDir;  // cableDir always 0
    uint16_t cableMm;                       // cableMm always 0
    uint8_t  stripDir;
};
```

`EEPROM_MAGIC = 0xA6` — bump when struct layout changes to force re-initialisation.

### Test suite (child)

```
powershell.exe -Command "python -X utf8 tests/test_child.py 192.168.10.x"
# D1 Mini (max 2 strings — default)
# For ESP32: python tests/test_child.py 192.168.10.x 80 4210 8
```

Discover children first:
```
powershell.exe -Command "python tests/discover.py"
```

Tests restore factory state before and after string config tests. If a test run is interrupted, factory-reset the child manually via `POST /config/reset`.

---

## Sketch module structure

The sketch is split into modular `.h`/`.cpp` files, all in `main/`. `main/main.ino` contains only `setup()` and `loop()`.

### Module files

| File | Board(s) | Purpose |
|------|----------|---------|
| `BoardConfig.h` | all | Board detection macros (`BOARD_GIGA`, `BOARD_ESP32`, `BOARD_D1MINI`, `BOARD_FASTLED`), board-specific includes, LED hardware constants |
| `Protocol.h` | all | UDP wire-protocol constants and packed structs (`UdpHeader`, `PongPayload`, `ActionPayload`, `LoadStepPayload`, …) |
| `Globals.h` / `Globals.cpp` | all | Shared globals: `WiFiServer server`, `WiFiUDP ntpUDP/cmdUDP`, `udpBuf[]`, NTP state |
| `NetUtils.h` / `NetUtils.cpp` | all | `connectWiFi()`, `syncNTP()`, `currentEpoch()`, `printStatus()` |
| `HttpUtils.h` / `HttpUtils.cpp` | all | `sendBuf()`, `sendJsonOk()`, `sendJsonErr()`, `sendStatus()` |
| `JsonUtils.h` / `JsonUtils.cpp` | all | `jsonGetInt()`, `jsonGetStr()` — lightweight JSON field extraction |
| `UdpCommon.h` / `UdpCommon.cpp` | all | `handleUdpPacket()`, `pollUDP()`, `serveClient()`, `handleClient()` — full HTTP route dispatch and UDP receive loop |
| `Child.h` / `Child.cpp` | ESP32, D1 Mini | Child config structs, EEPROM load/save, `sendPong()`, `sendStatusResp()`, `sendChildConfigPage()`, `handlePostChildConfig()`, `handleFactoryReset()` |
| `ChildLED.h` / `ChildLED.cpp` | ESP32, D1 Mini | `applyRunnerStep()` (shared), `ledTask()` (ESP32 FreeRTOS Core 0), `updateLED()` (D1 Mini non-blocking) |
| `Parent.h` / `Parent.cpp` | Giga | Parent data structures (`ChildNode`, `Runner`, `AppSettings`, …), all `/api/*` handlers, `sendParentSPA()`, runner compute/sync/start/stop |

All board-specific headers use both include guards (`#ifndef FILE_H`) and content guards (`#ifdef BOARD_XXX`) so they are safe to include unconditionally on any board.

### Giga HTTP routes

| Method | Path | Handler |
|--------|------|---------|
| GET | `/` | `sendParentSPA()` — full 6-tab SPA |
| GET | `/status` | `sendStatus()` |
| GET/POST | `/api/children` | `sendApiChildren()` / inline IP parse → `sendPing()` |
| GET | `/api/children/export` | `sendApiChildrenExport()` |
| POST | `/api/children/import` | `handleApiChildrenImport()` |
| * | `/api/children/:id` | `handleChildIdRoute()` |
| GET/POST | `/api/layout` | `sendApiLayout()` / `handlePostLayout()` |
| GET/POST | `/api/settings` | `sendApiSettings()` / `handlePostSettings()` |
| POST | `/api/action` | `handleApiAction()` |
| POST | `/api/action/stop` | `handleApiActionStop()` |
| GET/POST | `/api/runners` | `sendApiRunners()` / `handlePostRunners()` |
| GET/PUT/DELETE | `/api/runners/:id` | `handleRunnerIdRoute()` |
| POST | `/api/runners/stop` | `stopAllRunners()` |

### loop() per board

| Board | loop() body |
|-------|------------|
| Giga | `printStatus()` → `pollUDP()` → periodic `sendPing(broadcast)` every 30 s → `handleClient()` → `delay(10)` |
| ESP32 | `printStatus()` → `pollUDP()` → `handleClient()` → `delay(10)` |
| D1 Mini | `printStatus()` → `pollUDP()` → `updateLED()` → `handleClient()` → `yield()` |

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
- **`WiFiClient::print()` silently truncates** strings longer than the internal TX buffer (~280–400 bytes on the Giga R1 WiFi). Data past the limit is **dropped permanently** — `flush()` after the call does NOT recover it. Symptom: SPA JavaScript arrives with mid-string cuts, causing browser syntax errors and pages stuck at "Loading...". **Fix**: use the `spa(WiFiClient&, const char*)` chunked-write helper (in `Parent.cpp`) for any `c.print()` call whose string exceeds ~256 bytes. Small strings (JSON responses, HTTP headers) can use `c.print()` safely.
- **Never name a sketch header `Network.h`** when targeting ESP32 (Arduino core 3.x). The core ships `libraries/Network/src/Network.h` which defines `network_event_handle_t`, `NetworkEventCb`, etc. used internally by `WiFiGeneric.h`. The sketch directory is searched first, so a custom `Network.h` silently shadows the library header and causes cryptic `'network_event_handle_t' does not name a type` build failures. Use a unique name (e.g. `NetUtils.h`).
