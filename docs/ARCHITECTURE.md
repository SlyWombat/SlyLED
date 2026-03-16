# SlyLED Architecture — v4.0

## Overview

SlyLED is a multi-board LED controller. **The Orchestrator** (Windows app or Giga R1 WiFi parent) serves a browser UI and coordinates timing; one or more **Performers** (ESP32 / D1 Mini / Giga Child) own the physical LEDs and execute actions locally.

All boards share a single sketch (`main/main.ino`) gated by preprocessor guards:

```cpp
#ifdef BOARD_GIGA        // Giga R1 as parent (Orchestrator runtime)
#ifdef BOARD_GIGA_CHILD  // Giga R1 as LED performer (onboard RGB LED)
#ifdef BOARD_ESP32       // child  — ESP32 (QuinLED)
#ifdef BOARD_D1MINI      // child  — ESP8266 D1 Mini
#ifdef BOARD_FASTLED     // child  — either ESP32 or D1 Mini (addressable strips)
#ifdef BOARD_CHILD       // any board acting as a performer (FASTLED or GIGA_CHILD)
```

---

## Board / process roles

| Target | Role | LEDs |
|--------|------|------|
| **Windows (SlyLED.exe)** | **The Orchestrator** — Flask app + tray icon; serves 7-tab SPA on :8080, manages performers, computes runners with canvas-scoped delays, dispatches UDP, firmware management | None |
| Arduino Giga R1 WiFi (`BOARD_GIGA`) | **Runtime Orchestrator** — minimal SPA for start/stop once layout is designed | None |
| Arduino Giga R1 WiFi (`BOARD_GIGA_CHILD`) | **Performer** — executes LED actions on onboard RGB LED via software PWM (GigaLED.h/cpp) | 1× onboard RGB LED |
| ESP32 (QuinLED Quad / Uno) | **Performer** — executes LED actions, FreeRTOS task on Core 0 | Up to 4× WS2812B on GPIO 2 |
| ESP8266 D1 Mini (LOLIN/WEMOS) | **Performer** — same but single-threaded non-blocking loop | Up to 4× WS2812B on GPIO 2 |

---

## Threading model

### Windows Parent — The Orchestrator (SlyLED.exe)

Two threads (+ background flash thread on demand):

```
Main thread — pystray icon.run()
  └── tray menu (Open / Quit)

Daemon thread — Flask app.run()
  └── HTTP :8080 (threaded=True)
        └── REST API handlers
        └── UDP send helpers (inline, no background thread)
        └── Firmware flash (spawns background thread per flash operation)
```

Single-instance detection: on startup, checks `http://localhost:{port}/status` — if another Orchestrator is already running, opens the browser to it and exits.

UDP discovery and dispatch happen synchronously inside API request handlers. `CMD_STATUS_REQ` polls for up to 300 ms inside the `/api/children/:id/status` handler.

### Giga (parent runtime)

Single `loop()` thread. No LED code at all.

```
loop()
  └── printStatus()       — serial heartbeat
  └── pollUDP()           — receive and dispatch UDP packets
  └── sendPing()          — broadcast every 30 s
  └── handleClient()      — accept HTTP, route to handler, respond
```

### ESP32 (child)

Two FreeRTOS tasks on separate cores:

```
Core 1 — main task (loop())        Core 0 — LED task (ledTask())
  pollUDP()                           if childRunnerActive → applyRunnerStep()
  handleClient()                      else if childActType → immediate action
  delay(10)                           else → black / idle
```

Volatile flags (`childActType`, `childRunnerArmed`, `childRunnerActive`, etc.) cross the core boundary safely — bool writes are atomic on Xtensa LX6; `volatile` prevents register caching.

### D1 Mini (child)

Single `loop()` thread. `updateLED()` is called on every iteration (non-blocking, all state in `static` locals) and also yielded to during HTTP request handling.

```
loop()
  └── pollUDP()
  └── updateLED()    — non-blocking LED state machine
  └── handleClient() — calls updateLED() and yield() while waiting
```

---

## Communication

### UDP protocol (port 4210)

All packets: `UdpHeader (8 bytes) + payload`. Magic `0x534C`, version `3`.

| Command | Hex | Direction | Payload |
|---------|-----|-----------|---------|
| CMD_PING | 0x01 | parent → broadcast | none |
| CMD_PONG | 0x02 | child → parent | `PongPayload` (133 B) — hostname[10], altName[16], desc[32], stringCount(1), PongString[8]×9, fwMajor(1), fwMinor(1) |
| CMD_ACTION | 0x10 | parent → child | `ActionPayload` (26 B) — type, r/g/b, p16a, p8a-p8d (generic params), ledStart[8], ledEnd[8] |
| CMD_ACTION_STOP | 0x11 | parent → child | none |
| CMD_ACTION_EVENT | 0x12 | child → parent | `ActionEventPayload` (3 B) — actionType, actionSeqId, event |
| CMD_LOAD_STEP | 0x20 | parent → child | `LoadStepPayload` (32 B) — includes delayMs for canvas-scoped per-child stagger |
| CMD_LOAD_ACK | 0x21 | child → parent | 1 byte (step index) |
| CMD_SET_BRIGHTNESS | 0x22 | parent → child | 1 byte (brightness 0–255) |
| CMD_RUNNER_GO | 0x30 | parent → child | 5 bytes (uint32_t startEpoch + uint8_t loopFlag) |
| CMD_RUNNER_STOP | 0x31 | parent → child | none |
| CMD_STATUS_REQ | 0x40 | parent → child | none |
| CMD_STATUS_RESP | 0x41 | child → parent | `StatusRespPayload` (8 B) |

### NTP time sync

All boards sync to `pool.ntp.org` on boot. Epoch timestamps in UDP headers and `CMD_RUNNER_GO` make runner execution deterministic across children without a dedicated sync protocol. Typical LAN jitter is ±10–50 ms.

---

## Parent HTTP API

Routes matched in order (longest/most-specific first):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Full 7-tab SPA |
| GET | `/status` | `{"role":"parent","hostname":"...","version":"4.0"}` |
| GET | `/api/children/discover` | Broadcast PING, return unregistered performers found within 2 s |
| GET/POST | `/api/children/import` | Import JSON array; dedup by hostname |
| GET | `/api/children/export` | Export all performers as JSON |
| GET/POST/DELETE | `/api/children/:id/...` | Performer CRUD, refresh, status poll |
| GET/POST | `/api/children` | List / add by IP |
| GET/POST | `/api/layout` | Canvas positions |
| GET/POST | `/api/settings` | App settings (includes runnerLoop) |
| POST | `/api/action/stop` | Stop immediate action on all/one performer |
| POST | `/api/action` | Send immediate action (9 types, generic params) |
| POST | `/api/runners/stop` | Broadcast CMD_RUNNER_STOP + CMD_ACTION_STOP |
| GET/POST/PUT/DELETE | `/api/runners/:id/...` | Runner compute (canvas-scoped delays) / sync / start |
| GET/POST | `/api/runners` | List / create runners |
| GET/POST | `/api/actions` | List / create actions (reusable presets) |
| GET/PUT/DELETE | `/api/actions/:id` | Get / update / delete action |
| GET/POST | `/api/wifi` | WiFi credential management (encrypted storage, hash comparison) |
| GET | `/api/firmware/ports` | List COM ports with board detection by USB VID:PID |
| POST | `/api/firmware/query` | Serial version + WiFi hash query on a port (~2 s) |
| GET | `/api/firmware/registry` | List available firmware binaries |
| POST | `/api/firmware/detect` | Detect chip type via esptool |
| POST | `/api/firmware/flash` | Flash firmware in background thread |
| GET | `/api/firmware/flash/status` | Poll flash progress |
| POST | `/api/reset` | Factory reset — clear all performers, runners, actions, layout, settings |
| POST | `/api/shutdown` | Terminate the parent process |

**Route order matters:** `/api/runners/stop` is checked before `/api/runners/`; `/api/children/import` before `/api/children/export` before `/api/children/`.

### GET /api/children/:id/status

Sends `CMD_STATUS_REQ` to the child's IP and polls UDP for up to 300 ms. Returns `{"ok":true,"action":N,"runner":bool,"step":N,"rssi":-N,"uptime":N}` or `{"ok":false,"err":"timeout"}`.

---

## Child (Performer) HTTP routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | 302 redirect to `/config` |
| GET | `/status` | `{"role":"child","hostname":"SLYC-XXXX","action":N}` |
| GET | `/config` | 3-tab self-config SPA (Dashboard / Settings / Config) |
| POST | `/config` | Save config to EEPROM, broadcast CMD_PONG, redirect 303 |
| POST | `/config/reset` | Factory reset to defaults, redirect 303 |
| GET | `/favicon.ico` | 404 |

---

## Parent data structures

```
ChildNode      — ip[4], hostname, altName, desc, xMm/yMm/zMm,
                 stringCount, strings[4] (StringInfo), status, lastSeenEpoch, fwVersion
AppSettings    — units, darkMode, canvasWidthMm, canvasHeightMm,
                 parentName, activeRunner, runnerRunning, runnerLoop,
                 runnerStartEpoch, runnerElapsed
RunnerStep(20) — RunnerAction(10) + AreaRect(8) + durationS(2)
Runner(~1363B) — name, stepCount, computed, steps[16], payload[16][8], childOffsets[]

children[8]    — 896 bytes
runners[4]     — ~5452 bytes
AppSettings    — 24 bytes
wifi           — ssid, password (encrypted at rest)
```

## Child (Performer) data structures

```
ChildSelfConfig  — hostname[10], altName[16], description[32],
                   stringCount, strings[CHILD_MAX_STRINGS] (ChildStringCfg, 10 B each)

// Volatile immediate action state (written by UDP handler, read by LED task):
childActType, childActR/G/B, childActP16a, childActP8a-P8d,
childActSt[8]/En[8], childActSeq

// Volatile runner state:
childRunner[16] (ChildRunnerStep — includes delayMs per step)
childStepCount, childRunnerStart, childRunnerArmed, childRunnerActive, childRunnerLoop
```

Runner priority in LED task: **runner active > immediate action > idle black**

---

## Action types (v4.0)

```cpp
ACT_BLACKOUT = 0   // all LEDs off
ACT_SOLID    = 1   // solid colour
ACT_FADE     = 2   // linear fade between two colours
ACT_BREATHE  = 3   // single colour brightness sine wave
ACT_CHASE    = 4   // theater chase (every Nth pixel lit, shifts)
ACT_RAINBOW  = 5   // HSV rainbow cycle (8 palettes: Classic/Ocean/Lava/Forest/Party/Heat/Cool/Pastel)
ACT_FIRE     = 6   // fire / Perlin noise (cooling + sparking params)
ACT_COMET    = 7   // shooting comet with fading tail
ACT_TWINKLE  = 8   // random sparkle + fade
```

ActionPayload uses generic parameter fields (p16a, p8a-p8d) reinterpreted per type — see Protocol.h comments for the per-type mapping.

Wipe direction: `DIR_E=0 (+X)`, `DIR_N=1 (+Y)`, `DIR_W=2 (-X)`, `DIR_S=3 (-Y)`.

---

## Runner pre-computation

Triggered by `POST /api/runners/:id/compute`. For each step × child × string:

1. Convert area-of-effect (0–10000 units) to mm using canvas dimensions
2. Compute string origin: `childX + cableMm × dx[cableDir]`
3. Walk LEDs: `pos = origin + i × lengthMm × dx[stripDir] / (ledCount-1)`
4. Record first/last LED index inside AoE as `ledStart[j]` / `ledEnd[j]`
5. `0xFF` = string not in AoE

Results stored in `runners[id].payload[step][child]`. Integer arithmetic only — no float.

---

## Runner execution sequence

```
Parent                               Performers
  POST /api/runners/:id/compute
  → compute per-child delayMs for canvas-scoped actions
    (projects child position onto effect axis, scales to 80% of duration)

  POST /api/runners/:id/sync
  → CMD_LOAD_STEP (step 0, delayMs=N) ────►
  ◄──────────── CMD_LOAD_ACK ──────────────
  → CMD_LOAD_STEP (step 1, delayMs=M) ────►
  ◄──────────── CMD_LOAD_ACK ──────────────
  ...

  POST /api/runners/:id/start
  → CMD_RUNNER_GO (epoch + 5s, loopFlag) ──►
                    (at startEpoch)
                    performer executes step 0 after delayMs,
                    advances by durationS,
                    loops (if loopFlag) or stops
```

---

## WiFi credential management

WiFi credentials are stored in `%APPDATA%\SlyLED\data\wifi.json`. The password is encrypted at rest using XOR with a machine-derived key (SHA-256 of hostname + salt). The parent computes a djb2 hash of SSID+password for comparison with firmware-reported WiFi hashes via serial query.

API: `GET /api/wifi` returns `{ssid, hasPassword}` (never the plaintext password). `POST /api/wifi` accepts `{ssid, password}` and stores the password encrypted.

---

## Firmware Manager

The Firmware Manager (`desktop/shared/firmware_manager.py`) provides board detection, serial version query, and flashing for all supported boards.

**Board detection:** USB VID:PID matching against `KNOWN_BOARDS` table (CP2102, CH340, CH9102, FT232 for ESP; native/DFU for Giga). Ambiguous ports (e.g. CH340 shared by D1 Mini and ESP32) can be resolved via esptool chip detection.

**Serial query:** Sends `VERSION\n`, `BOARD\n`, `WIFIHASH\n` over 115200 baud serial. Firmware responds with `SLYLED:x.y`, `BOARD:esp32|d1mini|giga-child`, `WIFIHASH:XXXXXXXX`.

**Flashing:**
- **ESP32 / D1 Mini:** `esptool write_flash 0x0` (921600 baud for ESP32, 460800 for D1 Mini)
- **Giga R1:** `arduino-cli upload --fqbn arduino:mbed_giga:giga --input-file` (DFU mode required)

Flash operations run in a background thread with progress polling via `GET /api/firmware/flash/status`.

**Firmware registry:** `firmware/registry.json` lists available binaries with board type, version, and filename.

---

## Child EEPROM persistence

Config survives power cycles. Layout:

```
Byte  0     : magic 0xA5 (uninitialised = skip load)
Bytes 1..N  : ChildSelfConfig struct (sizeof(childCfg) bytes)
```

- **ESP32**: `Preferences` library (NVS namespace `"slyled"`)
- **D1 Mini**: `EEPROM.h` (byte-addressed)

Hostname is **always** regenerated from the last 2 MAC octets (`SLYC-XXXX`) — never stored. First boot writes defaults automatically.

---

## SPA structure

Seven tabs, all served as one HTML response with inline CSS and JS:

| Tab | Data source | Key actions |
|-----|-------------|-------------|
| Dashboard | GET /api/children, GET /api/settings | Stop / Go runner; real-time progress bar with elapsed/total time |
| Setup | GET /api/children, GET /api/settings | **Discover** (broadcast PING, list new performers), Add/remove/refresh, details modal (shows firmware version), JSON import/export |
| Layout | GET /api/layout | Sidebar lists unplaced performers (drag onto canvas); 900×450 canvas with detailed string view (direction + length, folded-string support) or simple icon mode; double-click node to edit position or remove from canvas; labels flip above when near bottom |
| Actions | GET /api/actions | Reusable action library — 9 effect types with per-type params; canvas-scoped actions; create/edit/delete named presets (no live hardware changes) |
| Runtime | GET /api/runners, GET /api/actions | Create runners from library actions; steps = action ref + area-of-effect + duration; Compute (with per-child delay for canvas scope)/Sync/Start/Stop; runner loop toggle |
| Settings | GET /api/settings, GET/POST /api/wifi | Dark mode, units, canvas size, parent name, runner loop; WiFi credential management (encrypted storage); **Factory Reset** (POST /api/reset — clears all data) |
| Firmware | GET /api/firmware/ports, /registry | Board detection by USB VID:PID; serial version query + WiFi hash comparison; flash ESP32/D1 Mini via esptool; flash Giga via arduino-cli DFU; progress polling |

Dark mode: `body#app` CSS class `light` toggled by `applyDarkMode()`. Persisted in `settings.darkMode`. Applied before first tab renders.

---

## Flash usage (v4.0)

| Board | Flash | RAM |
|-------|-------|-----|
| Giga | ~310 KB / 1966 KB (16%) | ~81 KB / 524 KB (15%) |
| ESP32 | ~1030 KB / 1311 KB (79%) | ~50 KB / 328 KB (15%) |
| D1 Mini | ~305 KB / 1049 KB (29%) | ~32.5 KB / 80 KB (40%) |

ESP32 flash is the tightest constraint. Each new feature should be checked after compile.

---

## Known GCC / Mbed quirks

- **No `static` on sketch-level functions** — conflicts with Mbed's auto-prototype generator.
- **No enum in function signatures** — use `uint8_t` and cast internally; auto-prototype generator fails on enum parameters.
- **`volatile bool` for cross-thread state** — sufficient for simple flags; bool writes are atomic on both Cortex-M7 and Xtensa LX6.
- **`WiFi.setHostname()` before `WiFi.begin()`** — required for DHCP option 12 (hostname).
- **`Serial.print()` guards** — always `if (Serial)` on Mbed OS; blocks forever if no USB CDC terminal is connected.
