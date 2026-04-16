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

**Versioning — two independent version tracks:**
- **Firmware version** (`main/version.h`): `APP_MAJOR` / `APP_MINOR` / `APP_PATCH`. Only changes when firmware code (`.ino`, `.h`, `.cpp`) changes. `build.ps1` increments `APP_MINOR` on compile+upload. Firmware registry (`firmware/registry.json`) tracks firmware versions per board.
- **App version** (orchestrator + Android): Set in `desktop/shared/parent_server.py` (`VERSION`), `android/app/build.gradle.kts` (`versionName`/`versionCode`), `desktop/windows/installer.iss` (`AppVersion`). Changes on app/SPA/server releases. Independent of firmware version.

## Critical hardware quirks

- **Never use `analogWrite()`** on the onboard LED pins — it crashes Mbed OS (symptom: red LED blinks 4 fast + 4 slow).
- **Use `digitalWrite()` only.** For smooth dimming/fading, implement software PWM: toggle pins in a tight loop with `delayMicroseconds()`.
- **FastLED is not reliable on the Giga R1** (crashes/compatibility issues). The current sketch uses custom `hueToRGB()` + software PWM instead.

## System architecture (three-tier)

```
The Orchestrator (Windows/Mac Flask)  ← primary design + control UI + firmware manager
    desktop/shared/parent_server.py
    desktop/shared/firmware_manager.py
    desktop/shared/spa/              ← 7-tab SPA (22 files: HTML shell + 16 JS modules + CSS)
    desktop/windows/run.ps1  (Windows launcher)
    desktop/mac/run.sh        (Mac launcher)
         │  UDP port 4210 binary protocol v4
         ▼
Performers (ESP32 / D1 Mini / Giga Child)  ← LED execution nodes
    (managed via Setup tab, UDP PING/PONG/ACTION/LOAD_STEP)
         │
Camera Nodes (Orange Pi / Raspberry Pi)    ← video capture nodes
    firmware/orangepi/camera_server.py
    (Flask HTTP :5000 + UDP PONG :4210, deployed via SSH+SCP from Firmware tab)
```

### Camera nodes

Camera nodes run on Orange Pi or Raspberry Pi SBCs. Firmware is a Python Flask server (`firmware/orangepi/camera_server.py`) that responds to UDP PING with PONG (same binary protocol v4) and serves HTTP endpoints on port 5000:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | JSON status (hostname, cameras, capabilities, uptime) |
| GET | `/config` | HTML config SPA (dashboard + settings, detection UI) |
| GET | `/snapshot?cam=N` | JPEG snapshot from camera N (OpenCV → fswebcam fallback) |
| POST | `/scan` | Object detection — YOLOv8n via ONNX Runtime |
| POST | `/depth-map` | Monocular depth estimation (Depth-Anything-V2) |
| POST | `/point-cloud` | Generate 3D point cloud from depth + camera |
| POST | `/beam-detect` | Fast beam spot detection (for calibration) |
| POST | `/beam-detect/center` | Center beam of multi-beam fixture |
| POST | `/dark-reference` | Capture dark reference frame |
| POST | `/track/start` | Start continuous person tracking |
| POST | `/track/stop` | Stop tracking |
| GET | `/track/status` | Tracking state |
| GET | `/health` | Health check |

**Orchestrator calibration API routes (served by `desktop/shared/parent_server.py`):**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/calibration/mover/<fid>/start` | Start unified mover calibration (background) |
| GET | `/api/calibration/mover/<fid>/status` | Poll calibration progress |
| GET | `/api/calibration/mover/<fid>` | Get saved calibration data |
| DELETE | `/api/calibration/mover/<fid>` | Delete calibration |
| POST | `/api/calibration/mover/<fid>/aim` | Aim using calibration grid |

- **Systemd service:** `slyled-cam` for auto-start on boot (tracked in `firmware/orangepi/slyled-cam.service`)
- **Multi-camera support:** USB cameras only (V4L2). Filters SoC/ISP video nodes (sunxi-vin, bcm2835-isp). Pi CSI ribbon cameras not supported in v1.x.
- **Object detection:** `firmware/orangepi/detector.py` — YOLOv8n ONNX model via onnxruntime (falls back to OpenCV DNN). Model deployed via SCP (`models/yolov8n.onnx`, 12 MB, gitignored). Returns bounding boxes with labels and confidence scores.
- **Config page:** Per-camera cards with Capture Frame + Detect Objects buttons, threshold slider, resolution toggle (320/640), auto-refresh, canvas overlay with bounding boxes
- **Depth estimation:** `firmware/orangepi/depth_estimator.py` — Depth-Anything-V2 small (95 MB ONNX). 6.5s on ARM. Produces relative depth maps and 3D point clouds.
- **Beam detection:** `firmware/orangepi/beam_detector.py` — Color-filtered beam detection for moving head calibration. Brightness + saturation + compactness checks. 3-beam center identification.
- **Tracking:** `firmware/orangepi/tracker.py` — Continuous person detection with proximity re-ID (500mm threshold). Pushes temporal objects to orchestrator.
- **Deploy:** SSH+SCP from the Firmware tab in the SPA; uploads `camera_server.py`, `detector.py`, `depth_estimator.py`, `beam_detector.py`, `tracker.py`, `requirements.txt`, `slyled-cam.service`, and models. Version comparison with force-reinstall option.
- **Per-camera fixtures:** Each USB camera sensor registers as a separate placeable fixture with own FOV, resolution, and calibration data.

**Giga board roles:** The Giga R1 compiles in two modes:
- `BOARD_GIGA` (default) — runtime Orchestrator with minimal SPA for start/stop
- `BOARD_GIGA_CHILD` (define `GIGA_CHILD`) — LED Performer using onboard RGB LED via software PWM (GigaLED.h/cpp provides CRGB-compatible interface)

### Desktop parent files

| Path | Purpose |
|------|---------|
| `desktop/shared/parent_server.py` | Flask server — all `/api/*` routes + UDP child protocol + WiFi + firmware API |
| `desktop/shared/firmware_manager.py` | Board detection (VID:PID), serial version query, esptool/arduino-cli flash |
| `desktop/shared/spa/index.html` | HTML shell (592 lines) — 7-tab SPA structure |
| `desktop/shared/spa/css/app.css` | Extracted stylesheet (102 lines) |
| `desktop/shared/spa/js/app.js` | Core JS (797 lines) — layout, navigation, utils, modal, init |
| `desktop/shared/spa/js/dashboard.js` | Dashboard tab — live grid, runner status, gyro |
| `desktop/shared/spa/js/setup-ui.js` | Setup tab — fixture CRUD, discovery, cameras, gyro config |
| `desktop/shared/spa/js/scene-3d.js` | Three.js 3D viewport, view controls, alignment |
| `desktop/shared/spa/js/timelines.js` | Timeline editor, preview (performance.now), bake |
| `desktop/shared/spa/js/actions.js` | Action library, editor modal |
| `desktop/shared/spa/js/objects-effects.js` | Stage objects, spatial effects |
| `desktop/shared/spa/js/fixtures.js` | Fixture editor, orientation test |
| `desktop/shared/spa/js/profiles.js` | Profile browser/editor, OFL import, community |
| `desktop/shared/spa/js/emulation.js` | Stage preview, 3D runtime emulator |
| `desktop/shared/spa/js/calibration.js` | Camera/mover calibration, tracking, point cloud |
| `desktop/shared/spa/js/settings.js` | Settings, DMX engine, monitor, group control |
| `desktop/shared/spa/js/firmware.js` | OTA, flash, ports, GitHub firmware |
| `desktop/shared/spa/js/camera-deploy.js` | SSH config, key gen, camera deploy |
| `desktop/shared/spa/js/show-runtime.js` | Playlist, show playback |
| `desktop/shared/spa/js/wizard.js` | Fixture creation wizard |
| `desktop/shared/spa/js/file-manager.js` | Project file I/O, File System API |
| `desktop/shared/mover_control.py` | Unified mover control engine — claim/release, calibrate, orient, color |
| `desktop/shared/data/` | JSON persistence (children, layout, runners, settings, actions, wifi) — gitignored |
| `desktop/windows/run.ps1` | PowerShell launcher — installs deps, starts server |
| `desktop/windows/requirements.txt` | `flask, pystray, pillow, pyserial, esptool` |
| `desktop/mac/run.sh` | Bash launcher — installs deps, starts server |
| `desktop/mac/requirements.txt` | `flask>=3.0` |
| `desktop/shared/wled_bridge.py` | WLED device HTTP communication (probe, state, action mapping) |
| `desktop/shared/bake_engine.py` | Timeline bake — spatial math, DMX scene conversion, track priority |
| `desktop/shared/show_generator.py` | Dynamic show generation — 14 themes adapt to actual fixtures |
| `desktop/shared/dmx_profiles.py` | DMX fixture profile library — CRUD, validation, OFL import |
| `desktop/shared/dmx_artnet.py` | Art-Net engine — universe buffers, ArtDMX/ArtPoll output |
| `desktop/shared/community_client.py` | Community profile server client (electricrv.ca) |
| `firmware/registry.json` | Firmware binary registry (board, version, file) |
| `firmware/orangepi/camera_server.py` | Camera node firmware — Flask HTTP + UDP PONG + all endpoints |
| `firmware/orangepi/detector.py` | YOLOv8n object detection via ONNX Runtime |
| `firmware/orangepi/depth_estimator.py` | Depth-Anything-V2 monocular depth + point cloud |
| `firmware/orangepi/beam_detector.py` | Color-filtered beam detection for calibration |
| `firmware/orangepi/tracker.py` | Continuous person tracking with proximity re-ID |
| `firmware/orangepi/slyled-cam.service` | Systemd unit file for camera service |
| `firmware/orangepi/flash.ps1` | SSH+SCP deploy script for camera nodes |
| `desktop/shared/mover_calibrator.py` | Moving head calibration — discovery, BFS, grid, convergence |
| `desktop/shared/space_mapper.py` | Multi-camera point cloud merge + transform |
| `desktop/shared/surface_analyzer.py` | RANSAC floor/wall/obstacle detection from point cloud |

**Running on Windows:** `powershell.exe -ExecutionPolicy Bypass -File desktop\windows\run.ps1`

**Running on Mac:** `bash desktop/mac/run.sh`

### Desktop API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/children` | List / add performer nodes |
| GET | `/api/children/<id>/status` | Poll performer via UDP STATUS |
| DELETE/POST | `/api/children/<id>` | Remove / refresh performer |
| POST | `/api/children/<id>/reboot` | Remote reboot performer via HTTP |
| GET | `/api/children/discover` | Broadcast PING, return unregistered performers |
| GET/POST | `/api/children/export` | Bulk export/import JSON |
| GET/POST | `/api/layout` | LED layout config |
| GET/POST | `/api/settings` | App settings (dark mode, runnerLoop, etc.) |
| POST | `/api/action` | Send ACTION packet to performer (9 types) |
| POST | `/api/action/stop` | Send STOP to all performers |
| GET/POST | `/api/actions` | List / create action presets (library) |
| GET/PUT/DELETE | `/api/actions/<id>` | Get / update / delete action preset |
| GET/POST | `/api/runners` | List / create runners |
| GET/PUT/DELETE | `/api/runners/<id>` | Get / update / delete runner |
| POST | `/api/runners/<id>/compute` | Compute runner steps (canvas-scoped delays) |
| POST | `/api/runners/<id>/sync` | Sync runner to performer via LOAD_STEP |
| POST | `/api/runners/<id>/start` | Start runner on performer (with loop flag) |
| GET | `/api/runners/live` | Per-child live action state (from pushed ACTION_EVENTs) |
| POST | `/api/runners/stop` | Stop all runners |
| GET/POST | `/api/wifi` | WiFi credential management (encrypted storage) |
| GET | `/api/firmware/ports` | List COM ports with board detection |
| POST | `/api/firmware/query` | Serial version + WiFi hash query |
| GET | `/api/firmware/registry` | List available firmware binaries |
| POST | `/api/firmware/detect` | Detect chip type via esptool |
| POST | `/api/firmware/flash` | Flash firmware (background thread) |
| GET | `/api/firmware/flash/status` | Poll flash progress |
| GET | `/api/cameras/<id>/snapshot` | Proxy JPEG snapshot from camera node |
| GET | `/api/cameras/<id>/status` | Live status from camera node |
| POST | `/api/cameras/<id>/scan` | Forward camera scan to camera node |
| POST | `/api/calibration/mover/<fid>/start` | Start unified mover calibration (background) |
| GET | `/api/calibration/mover/<fid>/status` | Poll calibration progress |
| GET | `/api/calibration/mover/<fid>` | Get saved calibration data |
| DELETE | `/api/calibration/mover/<fid>` | Delete calibration |
| POST | `/api/calibration/mover/<fid>/aim` | Aim using calibration grid |
| GET | `/api/fixtures/live` | Per-fixture live output state (RGB, dimmer, pan/tilt, effect) |
| GET/POST | `/api/show/playlist` | Ordered timeline playlist + loop setting |
| POST | `/api/show/start` | Start sequential show playback (all timelines) |
| POST | `/api/show/stop` | Stop show playback |
| GET | `/api/show/status` | Sequential playback status (current timeline, progress) |
| GET | `/api/project/export` | Bundle ALL state into `.slyshow` project file |
| POST | `/api/project/import` | Load complete project file, replace ALL state |
| GET | `/api/project/name` | Current project name |
| POST | `/api/project/name` | Set project name |
| POST | `/api/reset` | Factory reset all data |
| POST | `/api/shutdown` | Terminate parent process |

**Naming:** The "Surfaces" concept has been renamed to **"Objects"** across all platforms. Use `/api/objects` only — the `/api/surfaces` alias has been removed.

### Unified mover control (gyro + Android)

Both ESP32-S3 gyro boards and Android phones control moving heads through a single `MoverControlEngine` (`desktop/shared/mover_control.py`). Key concepts:

- **Claim/Release**: Only one device controls a mover at a time. TTL auto-release (30s).
- **Calibrate (hold-to-align)**: User holds calibrate button → server captures device orientation + current mover pan/tilt as reference pair. On release, device delta maps to mover delta.
- **Orient**: 20fps updates → delta from reference → normalized pan/tilt → profile-aware DMX.
- **Color**: RGB auto-resolved to color-wheel slot for color-wheel fixtures.

| API | Purpose |
|-----|---------|
| POST `/api/mover-control/claim` | Claim a mover (device exclusivity) |
| POST `/api/mover-control/release` | Release a mover |
| POST `/api/mover-control/start` | Turn on light, enter streaming |
| POST `/api/mover-control/calibrate-start` | Capture reference orientation + position |
| POST `/api/mover-control/calibrate-end` | Lock reference, resume streaming |
| POST `/api/mover-control/orient` | Orientation update (20fps) |
| POST `/api/mover-control/color` | Set beam color |
| GET `/api/mover-control/status` | Active claims status |

ESP32 gyro uses UDP (CMD_GYRO_ORIENT 0x60, CMD_GYRO_CALIBRATE 0x64) → server translates to engine calls.
Android uses HTTP POST to the same endpoints.

### UDP binary protocol (port 4210)

All packets share an 8-byte header: `struct.pack("<HBBI", magic=0x534C, version=4, cmd, epoch)`.

| Cmd byte | Name | Direction | Payload |
|---------|------|-----------|---------|
| 0x01 | PING | parent→child | header only |
| 0x02 | PONG | child→parent | 133 bytes — see PONG layout below |
| 0x10 | ACTION | parent→child | 42 bytes: type(1)+r/g/b(3)+p16a(2)+p8a-p8d(4)+ledStart[8×uint16](16)+ledEnd[8×uint16](16) |
| 0x11 | ACTION_STOP | parent→child | header only |
| 0x12 | ACTION_EVENT | child→parent | 4 bytes (actionType, stepIndex, totalSteps, event) |
| 0x20 | LOAD_STEP | parent→child | 48 bytes: idx/total/type/r/g/b/p16a/p8a-d/durS/delayMs(16) + ledStart[8×uint16](16)+ledEnd[8×uint16](16) |
| 0x21 | LOAD_ACK | child→parent | 1 byte (step index) |
| 0x22 | SET_BRIGHTNESS | parent→child | 1 byte (brightness 0–255) |
| 0x30 | RUNNER_GO | parent→child | 5 bytes (uint32_t startEpoch + uint8_t loopFlag) |
| 0x31 | RUNNER_STOP | parent→child | header only |
| 0x40 | STATUS_REQ | parent→child | header only |
| 0x41 | STATUS_RESP | child→parent | 8 bytes `<BBBBI` (activeAction, runnerActive, currentStep, rssi, uptime) |

**v3→v4 change:** `ledStart[]` and `ledEnd[]` upgraded from uint8 to uint16 arrays (8 entries each), adding 16 bytes to ACTION and LOAD_STEP payloads. Parent accepts both v3 and v4 PONGs for backward compatibility.

**PONG payload (133 bytes = total packet 141):**
```
hostname[10]  altName[16]  description[32]  stringCount(1)  PongStrings×8  fwMajor(1)  fwMinor(1)
PongString = <HHBBHB>: ledCount(2) + lengthMm(2) + ledType(1) + cableDir(1) + cableMm(2) + stripDir(1) = 9 bytes
8 × 9 = 72  →  10+16+32+1+72+2 = 133
cableDir bit 0 = folded flag (string folds back on itself)
```

**wifiRssi** is stored as `uint8_t` absolute magnitude (e.g. 69 → -69 dBm). Check `> 0`.

---

## Child node architecture (ESP32 / D1 Mini / Giga Child)

The same `main/main.ino` sketch compiles for ESP32, D1 Mini, and Giga Child via `#ifdef BOARD_CHILD` (which includes both `BOARD_FASTLED` and `BOARD_GIGA_CHILD`).

### Per-board limits

| Board | `CHILD_MAX_STRINGS` | EEPROM / storage |
|-------|---------------------|-----------------|
| D1 Mini (`BOARD_D1MINI`) | 2 | EEPROM (flash-backed) |
| ESP32 (`BOARD_ESP32`) | 8 | NVS Preferences (`"slyled"` namespace) |
| Giga Child (`BOARD_GIGA_CHILD`) | 1 | NVS Preferences; 1 onboard RGB LED (NUM_LEDS=1) |

`MAX_STR_PER_CHILD = 8` is a **protocol constant** — all protocol structs (PongPayload, ActionPayload, LoadStepPayload) are sized for 8 strings regardless of board. `CHILD_MAX_STRINGS` only affects EEPROM layout and the config UI.

### HTTP routes (child)

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | 302 redirect → `/config` |
| GET | `/status` | JSON: `{"role":"child","hostname":…,"action":…,"udpRx":…}` |
| GET | `/config` | 3-tab HTML SPA |
| POST | `/config` | 200 JSON (saves to EEPROM; auto-reboots if pin changed) |
| POST | `/config/reset` | 303 redirect → `/config` (factory reset) |
| POST | `/reboot` | 200 JSON then ESP.restart() / NVIC_SystemReset() |
| GET | `/test/pin?p=16` | ESP32 only: flash single pixel R/G/B on GPIO (neopixelWrite) |
| GET | `/favicon.ico` | 404 |

### Config SPA (3 tabs)

- **Dashboard** — hostname, altName, stringCount (server-rendered); live action status (XHR poll `/status` every 3 s)
- **Settings** (inside `<form id='cf' action='/config' method='POST'>`): `name='an'` altName, `name='desc'` description, `name='sc'` string count (1..`CHILD_MAX_STRINGS`)
- **Config** — string selector dropdown; per-string fieldsets with `lc/lm/lt/sd/dp` (ledCount, lengthMm, ledType, stripDir, dataPin); GPIO pin dropdown (ESP32 only) with Test button; auto-reboots on pin change
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
    uint8_t  ledType;  uint8_t  flags;     // bit 0 = folded
    uint16_t cableMm;                       // cableMm always 0
    uint8_t  stripDir;
    uint8_t  dataPin;                       // GPIO pin (ESP32 only; 0 = default GPIO 2)
};
```

`EEPROM_MAGIC = 0xA8` — bump when struct layout changes to force re-initialisation.

### Test suite (parent — 523 assertions)

```
powershell.exe -Command "python -X utf8 tests/test_parent.py"
```

Covers all API endpoints, all 14 action types, WLED bridge mapping, children CRUD, runners lifecycle, settings, WiFi, layout, action dispatch, schema versioning (v3 export, future version rejection), edge cases, and factory reset.

### Test suites (calibration — 223 assertions)

```
powershell.exe -Command "python -X utf8 tests/test_spatial_math.py"          # 47 assertions
powershell.exe -Command "python -X utf8 tests/test_mover_calibration.py"     # 99 assertions
powershell.exe -Command "python -X utf8 tests/test_beam_detector.py"         # 35 (requires OpenCV)
powershell.exe -Command "python -X utf8 tests/test_surface_analyzer.py"      # 42 assertions
```

Covers: coordinate transforms, pan/tilt math, grid interpolation, Newton inverse, RANSAC floor/wall detection, obstacle clustering, beam detection with synthetic frames, DMX buffer layout.

### Test suites (visual — Playwright)

```
powershell.exe -Command "python -X utf8 tests/test_unified_3d.py"           # 17 assertions
powershell.exe -Command "python -X utf8 tests/test_edit_rotation.py"        # 17 assertions
powershell.exe -Command "python -X utf8 tests/test_aruco_click.py"          # 9 assertions
powershell.exe -Command "python -X utf8 tests/test_fixture_grid.py"        # 24 assertions
```

Covers: 3D viewport on Dashboard/Runtime/Layout, tab switching round-trip, fixture edit rotation persistence, ArUco marker modal.

### Regression tests (weekly — tests/regression/)

```
powershell.exe -Command "python -X utf8 tests/regression/run_all.py"        # runs all 4
powershell.exe -Command "python -X utf8 tests/regression/test_stage_setup.py"    # 11
powershell.exe -Command "python -X utf8 tests/regression/test_layout_edit.py"    # 8
powershell.exe -Command "python -X utf8 tests/regression/test_timeline_bake.py"  # 10
powershell.exe -Command "python -X utf8 tests/regression/test_mover_tracking.py" # 11
```

End-to-end: fixture creation → layout → timeline → bake → show playback → 3D runtime verification. Each test is self-contained with own server + factory reset.

### Developer Management GUI

```
python tools/devgui/server.py    # http://localhost:9090
```

Standalone Flask app for running tests (auto-discovery, SSE live output), building releases (PyInstaller + Inno Setup), version dashboard (10 tracked sources), manual build (EN/FR), and website deploy.

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

### Test suite (camera — 81 assertions)

```
powershell.exe -Command "python -X utf8 tests/test_camera.py [host] [http_port] [udp_port]"
```

Covers camera node HTTP endpoints, UDP PONG response, snapshot capture, object detection `/scan`, config page UI, and detection overlay controls.

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
| `Child.h` / `Child.cpp` | ESP32, D1 Mini, Giga Child | Child config structs, EEPROM load/save, `sendPong()`, `sendStatusResp()`, `sendActionEvent()`, `esp32InitLeds()` (ESP32 multi-pin FastLED init), `sendChildConfigPage()`, `handlePostChildConfig()`, `handleFactoryReset()` |
| `ChildLED.h` / `ChildLED.cpp` | ESP32, D1 Mini, Giga Child | `applyRunnerStep()` (shared), `ledTask()` (ESP32 FreeRTOS Core 0), `updateLED()` (D1 Mini non-blocking); 14 action types with generic params |
| `GigaLED.h` / `GigaLED.cpp` | Giga Child | CRGB-compatible struct, `hsv2rgb_rainbow()`, `showSafe()` (software PWM on active-low RGB pins), `fill_solid()`, random helpers |
| `Parent.h` / `Parent.cpp` | Giga | Parent data structures (`ChildNode`, `Runner`, `AppSettings`, …), all `/api/*` handlers, `sendParentSPA()`, runner compute/sync/start/stop |

All board-specific headers use both include guards (`#ifndef FILE_H`) and content guards (`#ifdef BOARD_XXX`) so they are safe to include unconditionally on any board.

### Giga HTTP routes

| Method | Path | Handler |
|--------|------|---------|
| GET | `/` | `sendParentSPA()` — full 7-tab SPA |
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
| Giga (parent) | `printStatus()` → `pollUDP()` → periodic `sendPing(broadcast)` every 30 s → `handleClient()` → `delay(10)` |
| Giga (child) | `printStatus()` → `pollUDP()` → `updateLED()` → `handleClient()` → `delay(10)` |
| ESP32 | `printStatus()` → `pollUDP()` (drains ACTION_EVENT) → `handleClient()` → `delay(10)` |
| D1 Mini | `printStatus()` → `pollUDP()` → `updateLED()` → `handleClient()` → `yield()` |

## Git / GitHub

- Remote: `https://github.com/SlyWombat/SlyLED`
- After a successful upload, offer to sync: `git add . && git commit -m "<message>" && git push origin main`
- `arduino_secrets.h` is gitignored — never commit credentials or WiFi passwords
- Commit messages follow: `feat: <short description>`
- **Feature tracking:** All features and enhancements are managed via [GitHub Issues](https://github.com/SlyWombat/SlyLED/issues). Reference issues in commits where applicable (e.g. `feat: mDNS discovery (closes #1)`)
- **Releases:** Published via `gh release create` with binaries attached. App version reset to v1.0 (April 2026). Firmware versions track independently per board in `firmware/registry.json`.

## Android app

Native Android client at `android/`. Kotlin + Jetpack Compose + Material 3. Consumes the same REST API as the desktop SPA.

**Build (from project root):**
```powershell
$env:JAVA_HOME = 'C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot'
$env:ANDROID_SDK_ROOT = 'C:\Android\Sdk'
cd android
.\gradlew.bat assembleDebug --no-daemon
```

**APK output:** `android/app/build/outputs/apk/debug/app-debug.apk`

**Structure:** `android/app/src/main/java/com/slywombat/slyled/` — data layer (Retrofit API, models, repository), DI (Hilt), UI screens (6 tabs: Dashboard, Setup, Layout, Actions, Runtime, Settings), ViewModels.

**Phase tracking:** Issues #15–#19.

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
- **ESP32: never use `noInterrupts()` around `FastLED.show()`** — the ESP32 RMT peripheral handles WS2812B timing in hardware. Disabling interrupts with WiFi active triggers an Interrupt WDT timeout on CPU1. Only D1 Mini needs `noInterrupts()` (bit-banged output). The `showSafe()` function in `ChildLED.cpp` is split by board.
- **ESP32: FastLED init must happen after WiFi/config loads** — `esp32InitLeds()` reads per-string GPIO pin assignments from NVS, so it runs after `connectWiFi()` → `initChildConfig()`. D1 Mini inits FastLED before WiFi (hardcoded GPIO 2).
- **ESP32: `neopixelWrite(pin, r, g, b)`** is available in ESP32 Arduino core 3.x for driving a single WS2812B pixel on any GPIO without FastLED. Used by the `/test/pin` endpoint for GPIO testing.
- **Never name a sketch header `Network.h`** when targeting ESP32 (Arduino core 3.x). The core ships `libraries/Network/src/Network.h` which defines `network_event_handle_t`, `NetworkEventCb`, etc. used internally by `WiFiGeneric.h`. The sketch directory is searched first, so a custom `Network.h` silently shadows the library header and causes cryptic `'network_event_handle_t' does not name a type` build failures. Use a unique name (e.g. `NetUtils.h`).
