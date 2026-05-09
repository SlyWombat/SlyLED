# CLAUDE.md

Guidance for Claude Code working in this repo. Subsystem detail (full route tables, file
inventories, struct layouts, per-tab UI structure) lives in the source — not here.

## Work tree

The canonical work tree is `D:\SlyLED` (WSL: `/mnt/d/SlyLED`). All editing, git
commits, builds, and tests happen here. `D:\OneDrive\My Documents\ElectricRV\
Development\Projects\Lighting Arduino` is **read-only** going forward — the only
files that land there are the operator-facing build artifacts in `dist/` (mirrored
automatically by `build_release.ps1`'s OneDrive-mirror step). OneDrive sync
interferes with mid-build outputs and racy git operations; D:\SlyLED is plain
NTFS and avoids both. (Established 2026-05-06.)

## Target hardware

- **Giga R1 WiFi** — `arduino:mbed_giga:giga`. Onboard RGB on `LEDR/LEDG/LEDB` (86/87/88), **active-low**.
- **ESP32** — `esp32:esp32:esp32` (FastLED multi-string performer).
- **D1 Mini** — `esp8266:esp8266:d1_mini` (FastLED, ≤2 strings).
- **ESP32-S3 (Waveshare round-LCD gyro puck)** — USB-CDC in firmware; a wedged build = no serial = no `esptool` recovery without manual BOOT-button bootloader entry. Always `esptool erase_flash` before `write_flash` between distinct builds; prefer OTA.

## Build & upload

`arduino-cli` is at `%LOCALAPPDATA%\Arduino\arduino-cli.exe` (not on PATH). Find ports with
`arduino-cli board list`. Set `ARDUINO_DIRECTORIES_USER = (Get-Location).Path` so `./libraries`
resolves before manual compile. **First-time Giga DFU upload** needs the WinUSB driver
installed via Zadig (USB ID `2341:0366`).

Standard path is the build script:
```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

**Versioning — two independent tracks:**
- **Firmware** (`main/version.h`, per-board entries in `firmware/registry.json`): only bumps when firmware code changes. `build.ps1` increments `APP_MINOR` on compile+upload.
- **App** (orchestrator + Android — `desktop/shared/parent_server.py` `VERSION`, `android/app/build.gradle.kts`, `desktop/windows/installer.iss`): bumps on app/SPA/server releases.

## Critical hardware quirks

- **Never `analogWrite()` the Giga onboard LED pins** — crashes Mbed OS (4 fast + 4 slow red blink). Use `digitalWrite()` + software PWM.
- **FastLED is unreliable on the Giga R1.** The Giga Child uses custom `hueToRGB()` + software PWM (`GigaLED.h/cpp`).
- **ESP32: never `noInterrupts()` around `FastLED.show()`** — RMT handles WS2812B timing; disabling interrupts with WiFi active triggers Interrupt WDT on CPU1. Only D1 Mini needs `noInterrupts()` (bit-banged). `showSafe()` is board-split.
- **ESP32: FastLED init must happen after WiFi/config loads** (per-string GPIO comes from NVS). D1 Mini inits FastLED before WiFi (hardcoded GPIO 2).
- **`WiFiClient::print()` silently truncates** at ~280–400 bytes on the Giga. Data past the limit is dropped permanently — `flush()` does not recover it. Use `spa(WiFiClient&, const char*)` in `Parent.cpp` for any string > ~256 bytes.
- **Mbed auto-prototype gen** fails on `enum` parameters — use `uint8_t` and cast internally. Sketch-level functions must omit `static`.
- **`Serial.print()` blocks forever** on Mbed if no CDC terminal is attached — guard with `if (Serial)`.
- **`WiFi.setHostname()`** must be called *before* `WiFi.begin()` so DHCP option 12 carries it.
- **`rtos::Thread` requires `#include <mbed.h>`** (not pulled by Arduino.h on Giga). `volatile bool` is sufficient for cross-thread flags on Cortex-M7.
- **Never name a sketch header `Network.h`** on ESP32 core 3.x — silently shadows the core's `Network.h` and breaks `WiFiGeneric.h`. Use `NetUtils.h` etc.

## System architecture (three-tier)

```
Orchestrator (Windows/Mac Flask)         desktop/shared/parent_server.py
  primary design + control UI + firmware mgr; 7-tab SPA in desktop/shared/spa/
       │  UDP 4210 binary protocol v4
       ▼
Performers (ESP32 / D1 Mini / Giga Child)   main/main.ino (board-gated)
  LED execution nodes; PING/PONG/ACTION/LOAD_STEP
       │
Camera Nodes (Linux SBC + USB V4L2 cam)     firmware/orangepi/camera_server.py
  Flask :5000 + UDP PONG :4210 (deployed via SSH+SCP from Firmware tab)
```

The **Giga R1 also compiles as a Performer** (`BOARD_GIGA_CHILD`, define `GIGA_CHILD`) using the onboard RGB via software PWM. Default Giga build (`BOARD_GIGA`) is a minimal runtime — design/control UI lives in the desktop orchestrator.

**Camera nodes:** Ubuntu 22.04+ / Debian Bookworm+ on any Linux SBC (Orange Pi 4A primary; RPi 3B+/4/5 and Orange Pi Zero 3/5 confirmed). USB V4L2 only — Pi CSI ribbon not supported in v1.x. Repo path is `firmware/orangepi/` for historical reasons; code is board-agnostic. Each USB sensor is a separate placeable fixture. Compatibility matrix in `docs/SUPPORTED_HARDWARE.md`.

**"Surfaces" was renamed to "Objects"** across all platforms. Use `/api/objects` only.

## Rotation convention (#586, #600)

`fixture.rotation = [rx, ry, rz]` degrees in stage space, axis-letter-matched to Z-up:

- `rx` — **pitch** (about X). `rx > 0` aims **down** (forward axis tips toward stage -Z).
- `ry` — **roll**  (about Y, stage-forward). `ry > 0` rotates the image clockwise as seen from behind.
- `rz` — **yaw / pan** (about Z, stage-up). `rz > 0` aims toward +X (stage-left).

Shared by DMX fixtures *and* cameras. **Never read `rotation[1]` or `rotation[2]` directly** — route every read through `desktop/shared/camera_math.py::rotation_from_layout(rot) → (tilt, pan, roll)`. Canonical matrix from `build_camera_to_stage(tilt, pan, roll)`. SPA mirror helper is `rotationFromLayout(rot)` in `spa/js/app.js`. Persisted data carries `layout.rotationSchemaVersion = 2`; startup + `/api/project/import` migrate pre-#600 files.

## Angular-aim convention (#783)

Moving-head aim uses **stage-frame fixture-internal angles**, not mechanical yoke angles.

- **`panDeg > 0`** = beam swept toward `+X` (stage-left, matching `rz > 0` in the rotation convention above).
- **`tiltDeg > 0`** = beam **above horizon** (toward `+Z`, sky/ceiling). **`tiltDeg < 0`** = beam **below horizon** (toward `-Z`, floor).
- The canonical reference implementation is `desktop/shared/coverage_math.py::world_to_fixture_pt(target, fix_pos, rotation)` — `tilt_deg = atan2(mz, hypot(mx, my))`, `pan_deg = atan2(mx, my)`. Anything that produces or consumes `(panDeg, tiltDeg)` must round-trip with this function.
- The fixture-internal-to-DMX direction (whether DMX-up rotates the yoke clockwise or CCW; whether mechanical tilt-up = beam-up for top-mount or beam-down for pendant-mount) is **profile metadata's job** (`panSignFromDmx`, `tiltSignFromDmx` on the DMX profile). Call sites and tests express angles in stage convention only — never in mechanical or DMX terms.
- `POST /api/mover/<fid>/aim-angles {panDeg, tiltDeg}` is the canonical low-level move endpoint and obeys this convention end-to-end.

## UDP binary protocol (port 4210)

8-byte header: `struct.pack("<HBBI", magic=0x534C, version=4, cmd, epoch)`.

| Cmd  | Name         | Direction      | Payload |
|------|--------------|----------------|---------|
| 0x01 | PING         | parent→child   | header only |
| 0x02 | PONG         | child→parent   | 133 bytes (see below) |
| 0x10 | ACTION       | parent→child   | 42 bytes (type/rgb/p16a/p8a-d + ledStart[8×u16] + ledEnd[8×u16]) |
| 0x11 | ACTION_STOP  | parent→child   | header only |
| 0x12 | ACTION_EVENT | child→parent   | 4 bytes (actionType, stepIndex, totalSteps, event) |
| 0x20 | LOAD_STEP    | parent→child   | 48 bytes (idx/total/type/rgb/p16a/p8a-d/durS/delayMs + ledStart/End[8×u16]) |
| 0x21 | LOAD_ACK     | child→parent   | 1 byte (step index) |
| 0x22 | SET_BRIGHTNESS | parent→child | 1 byte |
| 0x30 | RUNNER_GO    | parent→child   | 5 bytes (u32 startEpoch + u8 loopFlag) |
| 0x31 | RUNNER_STOP  | parent→child   | header only |
| 0x40 | STATUS_REQ   | parent→child   | header only |
| 0x41 | STATUS_RESP  | child→parent   | 8 bytes `<BBBBI` (activeAction, runnerActive, currentStep, rssi, uptime) |
| 0x66 | GYRO_START   | gyro→parent    | 2 bytes — nonce (#825); legacy header-only still accepted |
| 0x67 | CLAIM_DENIED | parent→gyro    | header only — claim refused (#772) |
| 0x69 | GYRO_STOP    | gyro→parent    | 2 bytes — nonce (#825); legacy header-only still accepted |
| 0x6A | CLAIM_ACK    | parent→gyro    | 4 bytes — nonce + moverId (#825) |
| 0x6B | STOP_ACK     | parent→gyro    | 2 bytes — nonce (#825) |
| 0x6C | HB_REP       | gyro→parent    | 5 bytes — uiState + claimNonce + seq (#825) |
| 0x6D | AUTOBRI_PUSH | phone→parent   | 3 bytes — master + flags + seq (#861); UDP **4211** (#862) — own port to dodge Windows-host 4210 kernel reservations |
| 0x6E | GYRO_OFF     | gyro→parent    | 2 bytes — nonce (#867); same shape as GYRO_STOP but server releases claim with `blackout=True` (head goes dark). ACK reuses CMD_GYRO_STOP_ACK |
| 0x6F | GYRO_AIM_WIZARD | gyro→parent | 36 bytes — three Euler triples in degrees (roll, pitch, yaw) for {neutral, pitch_forward, yaw_left} (#869). Server converts each to a body-to-world unit quat via `quat_from_euler_zyx_deg` and runs the same `_aim_wizard_compute` math the Android wizard (#826) uses; persists derived `forward_local` / `up_local` on the puck's `gyro-<ip>` Remote. Fire-and-forget; no ACK |

**v3→v4:** `ledStart[]` / `ledEnd[]` upgraded uint8 → uint16 (8 entries each, +16 bytes per ACTION/LOAD_STEP). Parent accepts both v3 and v4 PONGs.

**#825 gyro handshake:** press-Start sends a fresh 16-bit nonce; orchestrator replies with CLAIM_ACK echoing the nonce. Puck advances UI only on matching ACK; CLAIM_DENIED reverts; ~1.5s overall timeout reverts with "NO RESPONSE". Server arms a 1.5s timer after CLAIM_ACK that releases the claim if no orient arrives (orphan-claim guard). Press-Stop carries a nonce too and is ACKed via STOP_ACK. Both sides exchange 2s heartbeats — gyro→parent HB_REP carries `uiState + claimNonce` so divergent state is reconciled (puck IDLE + server claim → release; puck ACTIVE + no server claim → reconstruct, the orchestrator-restart bootstrap path). New CMD codes (0x6A/0x6B/0x6C) are back-compat-safe — older firmware silently ignores them; UDP_VERSION stays at 5.

**PONG (133 bytes / 141 total):** `hostname[10] altName[16] description[32] stringCount(1) PongStrings×8 fwMajor(1) fwMinor(1)` where `PongString = <HHBBHB>` (`ledCount, lengthMm, ledType, cableDir, cableMm, stripDir`). `cableDir` bit 0 = folded.

`wifiRssi` is stored as `uint8_t` absolute magnitude (e.g. 69 → -69 dBm); check `> 0`.

**Per-board limits** (`MAX_STR_PER_CHILD = 8` is a *protocol* constant — all wire structs are sized for 8 strings regardless of board):

| Board               | `CHILD_MAX_STRINGS` | Storage                            |
|---------------------|---------------------|------------------------------------|
| D1 Mini             | 2                   | EEPROM (flash-backed)              |
| ESP32               | 8                   | NVS Preferences (`"slyled"` ns)    |
| Giga Child          | 1                   | NVS Preferences; 1 onboard RGB LED |

## Cal-pipeline change checklist (#733)

Any PR touching `mover_calibrator.py`, `coverage_math.py`, `parent_server.py` cal routes,
`mover_control.py`, `surface_analyzer.py` cal helpers, or `dmx_profiles.py` channel-map shape **must**:

1. Run `python tools/emulate_smart_pipeline.py --verbose` against `tests/fixtures/cal/corpus.json` and confirm exit 0.
2. Add a corpus case if the PR introduces a new failure mode.
3. Land the test alongside the fix in the same commit.

The weekly `tests/regression/run_all.py` includes the emulator (`test_smart_pipeline_emulator.py`).

## Tests

All commands run from project root. Wrap with `powershell.exe -Command "python -X utf8 …"` on Windows.

| Suite                                  | Coverage |
|----------------------------------------|----------|
| `tests/test_parent.py`                 | Parent API, action types, WLED, runners, schema (523 assertions) |
| `tests/test_spatial_math.py`           | Coordinate transforms, pan/tilt math (47) |
| `tests/test_mover_calibration.py`      | Initial aim, grid interp, DMX buffer (99) |
| `tests/test_beam_detector.py`          | Synthetic frame detection (35, requires OpenCV) |
| `tests/test_surface_analyzer.py`       | RANSAC walls, obstacle clustering (42) |
| `tests/test_unified_3d.py` etc.        | Playwright visual checks |
| `tests/regression/run_all.py`          | Stage setup → layout → bake → 3D runtime end-to-end |
| `tests/test_camera.py [host]`          | Camera node firmware (81) |
| `tests/test_child.py 192.168.10.x`     | Child firmware (factory-reset before/after) |

Discover children first via `python tests/discover.py`. Dev GUI for browsing / running suites
is `python tools/devgui/server.py` → http://localhost:9090.

## Code constraints (firmware)

Sketch code must be tight on heap and Flash:

- **Zero dynamic allocation.** No `malloc`, `new`, or `String` — fixed-size `char` buffers.
- **`F()` macro** for every literal (forces Flash, not SRAM).
- **Smallest types:** `uint8_t`/`int8_t` for ≤255 values, `const`/`constexpr` for fixed values.
- **Integer math only** — no `float`/`double` in hot paths.
- **Refactor when a file passes 500 lines** into `.h`/`.cpp` modules.
- **Buffer responses, minimize `client.print()` calls** to reduce TCP packet count.

Web UI is a strict SPA: the device is a JSON API, the browser owns rendering.

## Android

```powershell
$env:JAVA_HOME = 'C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot'
$env:ANDROID_SDK_ROOT = 'C:\Android\Sdk'
cd android; .\gradlew.bat assembleDebug --no-daemon
```

APK lands at `android/app/build/outputs/apk/debug/app-debug.apk`. App is operator-only
(no editing) — Stage / Control / Status tabs. Phase tracking in issues #15–#19.

## Git / GitHub

- Remote: `https://github.com/SlyWombat/SlyLED`
- `arduino_secrets.h` is gitignored — never commit credentials.
- Commits: `feat: <short description>`; reference issues (`feat: mDNS discovery (closes #1)`).
- All features tracked in [GitHub Issues](https://github.com/SlyWombat/SlyLED/issues).
- Releases: `gh release create` with binaries. App reset to v1.0 (April 2026); firmware tracks per-board in `firmware/registry.json`.
