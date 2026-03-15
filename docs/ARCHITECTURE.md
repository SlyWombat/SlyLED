# SlyLED Architecture — v4.0

## Overview

SlyLED is a multi-board LED controller. One **parent** (Windows app or Giga R1 WiFi) serves a browser UI and coordinates timing; one or more **child** boards (ESP32 / D1 Mini) own the physical LED strips and execute actions locally.

The child boards share a single sketch (`main/main.ino`) gated by preprocessor guards:

```cpp
#ifdef BOARD_GIGA    // parent runtime — Giga R1 WiFi (future)
#ifdef BOARD_ESP32   // child  — ESP32 (QuinLED)
#ifdef BOARD_D1MINI  // child  — ESP8266 D1 Mini
#ifdef BOARD_FASTLED // child  — either ESP32 or D1 Mini
```

---

## Board / process roles

| Target | Role | LEDs |
|--------|------|------|
| **Windows (SlyLED.exe)** | **Primary Parent** — Flask app + tray icon; serves SPA on :8080, manages children, computes runners, dispatches UDP | None |
| Arduino Giga R1 WiFi (STM32H747) | **Runtime Parent** *(future)* — minimal SPA for start/stop once layout is designed | None |
| ESP32 (QuinLED Quad / Uno) | **Child** — executes LED actions, FreeRTOS task on Core 0 | Up to 4× WS2812B on GPIO 2 |
| ESP8266 D1 Mini (LOLIN/WEMOS) | **Child** — same but single-threaded non-blocking loop | Up to 4× WS2812B on GPIO 2 |

---

## Threading model

### Windows Parent (SlyLED.exe)

Two threads:

```
Main thread — pystray icon.run()
  └── tray menu (Open / Quit)

Daemon thread — Flask app.run()
  └── HTTP :8080 (threaded=True)
        └── REST API handlers
        └── UDP send helpers (inline, no background thread)
```

UDP discovery and dispatch happen synchronously inside API request handlers. `CMD_STATUS_REQ` polls for up to 300 ms inside the `/api/children/:id/status` handler.

### Giga (parent runtime — future)

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

All packets: `UdpHeader (8 bytes) + payload`. Magic `0x534C`, version `2`.

| Command | Hex | Direction | Payload |
|---------|-----|-----------|---------|
| CMD_PING | 0x01 | parent → broadcast | none |
| CMD_PONG | 0x02 | child → parent | `PongPayload` (131 B) — hostname[10], altName[16], desc[32], stringCount(1), PongString[8]×9 |
| CMD_ACTION | 0x10 | parent → child | `ActionPayload` (26 B) — type, r/g/b, onMs, offMs, wDir, wSpd, ledStart[8], ledEnd[8] |
| CMD_ACTION_STOP | 0x11 | parent → child | none |
| CMD_LOAD_STEP | 0x20 | parent → child | `LoadStepPayload` (22 B) |
| CMD_LOAD_ACK | 0x21 | child → parent | 1 byte (step index) |
| CMD_RUNNER_GO | 0x30 | parent → child | 4 bytes (uint32_t startEpoch) |
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
| GET | `/` | Full 6-tab SPA |
| GET | `/status` | `{"role":"parent","hostname":"slyled"}` |
| GET | `/api/children/discover` | Broadcast PING, return unregistered children found within 1.5 s |
| GET/POST | `/api/children/import` | Import JSON array; dedup by hostname |
| GET | `/api/children/export` | Export all children as JSON |
| GET/POST/DELETE | `/api/children/:id/...` | Child CRUD, refresh, status poll |
| GET/POST | `/api/children` | List / add by IP |
| GET/POST | `/api/layout` | Canvas positions |
| GET/POST | `/api/settings` | App settings |
| POST | `/api/action/stop` | Stop immediate action on all/one child |
| POST | `/api/action` | Send immediate action |
| POST | `/api/runners/stop` | Broadcast CMD_RUNNER_STOP |
| GET/POST/PUT/DELETE | `/api/runners/:id/...` | Runner compute / sync / start |
| GET/POST | `/api/runners` | List / create runners |
| GET/POST | `/api/actions` | List / create actions (reusable presets) |
| GET/PUT/DELETE | `/api/actions/:id` | Get / update / delete action |
| POST | `/api/reset` | Factory reset — clear all children, runners, actions, layout, settings |

**Route order matters:** `/api/runners/stop` is checked before `/api/runners/`; `/api/children/import` before `/api/children/export` before `/api/children/`.

### GET /api/children/:id/status

Sends `CMD_STATUS_REQ` to the child's IP and polls UDP for up to 300 ms. Returns `{"ok":true,"action":N,"runner":bool,"step":N,"rssi":-N,"uptime":N}` or `{"ok":false,"err":"timeout"}`.

---

## Child HTTP routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Status page (hostname, current action) |
| GET | `/status` | `{"role":"child","hostname":"SLYC-XXXX","action":N}` |
| GET | `/config` | Self-config form |
| POST | `/config` | Save config to EEPROM, broadcast CMD_PONG, redirect 303 |

---

## Parent data structures

```
ChildNode      — ip[4], hostname, altName, desc, xMm/yMm/zMm,
                 stringCount, strings[4] (StringInfo), status, lastSeenEpoch
AppSettings    — units, darkMode, canvasWidthMm, canvasHeightMm,
                 parentName, activeRunner, runnerRunning
RunnerStep(20) — RunnerAction(10) + AreaRect(8) + durationS(2)
Runner(~1363B) — name, stepCount, computed, steps[16], payload[16][8]

children[8]    — 896 bytes
runners[4]     — ~5452 bytes
AppSettings    — 24 bytes
```

## Child data structures

```
ChildSelfConfig  — hostname[10], altName[16], description[32],
                   stringCount, strings[4] (ChildStringCfg, 10 B each)

// Volatile immediate action state (written by UDP handler, read by LED task):
childActType, childActR/G/B, childActOnMs/OffMs,
childActWDir/WSpd, childActSt[4]/En[4], childActSeq

// Volatile runner state:
childRunner[16] (ChildRunnerStep, 20 B each)
childStepCount, childRunnerStart, childRunnerArmed, childRunnerActive
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
Parent                               Children
  POST /api/runners/:id/sync
  → CMD_LOAD_STEP (step 0) ────────►
  ◄──────────── CMD_LOAD_ACK ────────
  → CMD_LOAD_STEP (step 1) ────────►
  ◄──────────── CMD_LOAD_ACK ────────
  ...

  POST /api/runners/:id/start
  → CMD_RUNNER_GO (epoch + 2s) ────►
                    (at startEpoch)
                    child executes step 0,
                    advances by durationS,
                    loops or stops
```

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

Six tabs, all served as one HTML response with inline CSS and JS:

| Tab | Data source | Key actions |
|-----|-------------|-------------|
| Dashboard | GET /api/children, GET /api/settings | Stop / Go runner |
| Setup | GET /api/children, GET /api/settings | **Discover** (broadcast PING, list new children), Add/remove/refresh, details modal, JSON import/export |
| Layout | GET /api/layout | Sidebar lists unplaced children (drag onto canvas); 900×450 canvas with detailed string view (direction + length) or simple icon mode; double-click node to edit position or remove from canvas; labels flip above when near bottom |
| Actions | GET /api/actions | Reusable action library — create/edit/delete named presets (no live hardware changes) |
| Runtime | GET /api/runners, GET /api/actions | Create runners from library actions; steps = action ref + area-of-effect + duration; Compute/Sync/Start/Stop |
| Settings | GET /api/settings | Dark mode, units, canvas size, parent name; **Factory Reset** (POST /api/reset — clears all data) |

Dark mode: `body#app` CSS class `light` toggled by `applyDarkMode()`. Persisted in `settings.darkMode`. Applied before first tab renders.

---

## Flash usage (v3.6)

| Board | Flash | RAM |
|-------|-------|-----|
| Giga | ~310 KB / 1966 KB (16%) | ~81 KB / 524 KB (15%) |
| ESP32 | ~1030 KB / 1311 KB (79%) | ~50 KB / 328 KB (15%) |
| D1 Mini | ~270 KB / 1049 KB (26%) | ~32 KB / 80 KB (40%) |

ESP32 flash is the tightest constraint. Each new feature should be checked after compile.

---

## Known GCC / Mbed quirks

- **No `static` on sketch-level functions** — conflicts with Mbed's auto-prototype generator.
- **No enum in function signatures** — use `uint8_t` and cast internally; auto-prototype generator fails on enum parameters.
- **`volatile bool` for cross-thread state** — sufficient for simple flags; bool writes are atomic on both Cortex-M7 and Xtensa LX6.
- **`WiFi.setHostname()` before `WiFi.begin()`** — required for DHCP option 12 (hostname).
- **`Serial.print()` guards** — always `if (Serial)` on Mbed OS; blocks forever if no USB CDC terminal is connected.
