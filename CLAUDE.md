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
- **ESP32-S3 (Waveshare round-LCD gyro controller)** — USB-CDC in firmware; a wedged build = no serial = no `esptool` recovery without manual BOOT-button bootloader entry. Always `esptool erase_flash` before `write_flash` between distinct builds; prefer OTA.

## Build & upload

`arduino-cli` is at `%LOCALAPPDATA%\Arduino\arduino-cli.exe` (not on PATH). Find ports with
`arduino-cli board list`. Set `ARDUINO_DIRECTORIES_USER = (Get-Location).Path` so `./libraries`
resolves before manual compile. **First-time Giga DFU upload** needs the WinUSB driver
installed via Zadig (USB ID `2341:0366`).

Standard path is the build script:
```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

Machine-specific paths (arduino-cli, default COM port, JDK/Android SDK, Inno Setup,
OneDrive dist mirror) live in `build.config.json` at repo root — edit that, not the
scripts. `build_release.ps1`'s firmware step is registry-driven (#902): each
`firmware/registry.json` entry carries `autoBuild` / `sketch` / `hashPaths` /
`versionFile` (and optionally `arduinoConfigFile` — the MMwave ESP32-C61 builds via
the isolated toolchain in `arduino-cli-mmwave.yaml`, sketch `mmwave/`, version track
`mmwave/version.h` with `MMW_*` macros). Use `-DryRun` to print the per-board plan and
`-CompileOnly [-Board <id|esp32|d1mini|gyro|dmx|mmwave>]` to compile with no version
bumps, dist copies, tags, or mirroring.

**Versioning — every platform has its own independent track. Never align them.**
- **Firmware** — `main/version.h` + per-board entry in `firmware/registry.json`. Each board (LED ESP32, LED D1 Mini, gyro ESP32-S3, DMX bridge, camera, Giga child, Giga parent) is its own number. Only bumps when that board's source changes. `build.ps1` increments `APP_MINOR` on manual compile+upload; `build_release.ps1` source-hash-gates each board.
- **Orchestrator** — `desktop/shared/parent_server.py` `VERSION` + `desktop/windows/installer.iss`. Tracks SPA + server changes. Source-hash gated.
- **Android** — `android/app/build.gradle.kts` (`versionName` + `versionCode`). **Tracks independently of the orchestrator** — `build_release.ps1` reads Android's *current* versionName as its baseline and patch-bumps only when Android source changes (cache at `android/.build-cache.json`). A bug fix shipping to the orchestrator does not touch Android, and vice-versa. **Do not align Android's version to the orchestrator's**; see `memory/feedback_android_independent_version.md`.
- **iOS** — `ios/SlyLED/Info.plist` (`CFBundleShortVersionString` user-visible / `CFBundleVersion` monotonic build counter) + the `ios-v*` git tag that triggers `.github/workflows/ios-testflight.yml`. **Tracks independently of every other platform** including Android — Apple's TestFlight pipeline owns its own cadence, and Apple App Review treats each `CFBundleShortVersionString` as a new "version" that re-triggers Beta App Review (subsequent `CFBundleVersion` bumps for the same short-version are instant). To ship a new TestFlight build: bump `CFBundleVersion` (always) and `CFBundleShortVersionString` (only when the user-facing release notes change), then `git tag ios-v<short>-<build>` and push. The workflow does NOT auto-bump — keep version edits intentional. **Do not align iOS's version to Android's or to the orchestrator's.**

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

**Camera nodes:** Ubuntu 22.04+ / Debian Bookworm+ on any Linux SBC (Orange Pi 4A primary; RPi 3B+/4/5 and Orange Pi Zero 3/5 confirmed). USB V4L2 only — Pi CSI ribbon not supported in v1.x. Repo path is `firmware/orangepi/` for historical reasons; code is board-agnostic, and `firmware/orangepi/` is the **only** copy (the stale root `camera/` mirror was deleted under #905). Each USB sensor is a separate placeable fixture. Compatibility matrix in `docs/SUPPORTED_HARDWARE.md`.

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
- The canonical reference implementation is `desktop/shared/aim/stage_frame.py::stage_aim_from_world_xyz(target_xyz, fixture_xyz)` — returns stage-frame `(az_deg, el_deg)` with `az_deg = atan2(sx, sy)`, `el_deg = atan2(sz, hypot(sx, sy))`. The `aim/` package names the same convention `az`/`el` (az = pan, el = tilt); anything that produces or consumes stage-frame aim angles must round-trip with this function.
- The fixture-internal-to-DMX direction (whether DMX-up rotates the yoke clockwise or CCW; whether mechanical tilt-up = beam-up for top-mount or beam-down for pendant-mount) is **profile metadata's job** (`panSignFromDmx`, `tiltSignFromDmx` on the DMX profile). Call sites and tests express angles in stage convention only — never in mechanical or DMX terms.
- `POST /api/mover/<fid>/aim` with `{azDeg, elDeg}` (or a `{x, y, z}` stage-mm target) is the canonical low-level move endpoint and obeys this convention end-to-end. (The legacy `aim-angles` endpoint was deleted under #784 PR-7.)

## UDP binary protocol (port 4210)

8-byte header: `struct.pack("<HBBI", magic=0x534C, version=5, cmd, epoch)`.

| Cmd  | Name         | Direction      | Payload |
|------|--------------|----------------|---------|
| 0x01 | PING         | parent→child   | header only |
| 0x02 | PONG         | child→parent   | 134 bytes (see below) |
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
| 0x50 | OTA_UPDATE   | parent→child   | variable — maj(1) + min(1) + patch(1) + urlLen(2 LE) + url(N) + sha256hex(64); triggers `otaStartUpdate()` + reboot |
| 0x51 | OTA_STATUS   | child→parent   | 2 bytes — status(u8, `OTA_STATUS_*` in `main/OtaUpdate.h`) + progress(u8, 0-100); fire-and-forget to the OTA trigger source on each phase change + every ≥10% of download (esp32/d1mini/gyro/dmx/mmwave — Giga has no OTA). Consumed by the orchestrator (`_handle_ota_status`, #922) → the `ota` field on `/api/firmware/check` rows |
| 0x60 | GYRO_ORIENT  | gyro→parent    | 8 bytes — roll100/pitch100/yaw100 (i16, ×100) + fps(u8) + flags(u8: bit0=streaming, bit1=imuOk, bit2=wifiOk, bit3=RESERVED per #819, bits[5:4]=ui-mode preset); ≤50 Hz stream, 20 Hz default |
| 0x61 | GYRO_CTRL    | parent→gyro    | 2 bytes — enabled(1) + targetFps(1) (0 = board default 20 Hz, max 50) |
| 0x62 | GYRO_RECAL   | parent→gyro    | header only — zero IMU reference |
| 0x63 | GYRO_COLOR   | gyro→parent    | 4 bytes — r, g, b + flags (bit0 = flash: brief full-brightness pulse) |
| 0x64 | GYRO_CALIBRATE | gyro→parent  | 7 bytes — calibrating(1: 1=hold started, 0=hold released) + roll100/pitch100/yaw100 (i16, ×100) |
| 0x65 | GYRO_HEARTBEAT | parent→gyro  | header only — keep-alive, 2 s cadence while a claim is active |
| 0x66 | GYRO_START   | gyro→parent    | 2 bytes — nonce (#825); legacy header-only still accepted |
| 0x67 | GYRO_CLAIM_DENIED | parent→gyro | 1 byte — reason code (#872; see `docs/gyro-claim-lifecycle.md` §3.6). Pre-#872 servers send header-only; the gyro reads that as reason 0 (legacy/unspecified) |
| 0x68 | GYRO_BATT    | gyro→parent    | 4 bytes — vbat100(u16, V×100) + pct(u8, 0xFF = unknown) + flags (bit0 = charging); ~10 s cadence |
| 0x69 | GYRO_STOP    | gyro→parent    | 2 bytes — nonce (#825); legacy header-only still accepted |
| 0x6A | GYRO_CLAIM_ACK | parent→gyro  | 4 bytes — nonce + moverId (#825) |
| 0x6B | GYRO_STOP_ACK | parent→gyro   | 2 bytes — nonce (#825) |
| 0x6C | GYRO_HEARTBEAT_REP | gyro→parent | 5 bytes — uiState + claimNonce + seq (#825) |
| 0x6D | AUTOBRI_PUSH | phone→parent   | 3 bytes — master + flags + seq (#861); UDP **4211** (#862) — own port to dodge Windows-host 4210 kernel reservations |
| 0x6E | GYRO_OFF     | gyro→parent    | 2 bytes — nonce (#867); same shape as GYRO_STOP but server releases claim with `blackout=True` (head goes dark). ACK reuses CMD_GYRO_STOP_ACK |
| 0x6F | GYRO_AIM_WIZARD | gyro→parent | 36 bytes — three Euler triples in degrees (roll, pitch, yaw) for {neutral, pitch_forward, yaw_left} (#869). Server converts each to a body-to-world unit quat via `quat_from_euler_zyx_deg` and runs the same `_aim_wizard_compute` math the Android wizard (#826) uses; persists derived `forward_local` / `up_local` on the gyro's `gyro-<ip>` Remote. Fire-and-forget; no ACK |
| 0x70 | MMW_TARGETS  | node→parent    | 28 bytes — seq(u16) + count(u8) + flags(u8: bit0 = radar-frame parse healthy) + 3 × {xMm i16, yMm i16, speedCms i16, resMm u16}; fixed 3 slots, unused zeroed. Source of truth: `mmwave/MmwProtocol.h::MmwTargetsPayload`; design doc `docs/design/mmwave_tracking.md` §4.3 |
| 0x71 | MMW_CONFIG   | parent→node    | reserved — not implemented in v1 (`mmwave/MmwProtocol.h`; design doc §4.3) |

**v3→v4:** `ledStart[]` / `ledEnd[]` upgraded uint8 → uint16 (8 entries each, +16 bytes per ACTION/LOAD_STEP). Parent accepts both v3 and v4 PONGs. **v4→v5 (#819):** CMD_GYRO_STOP (0x69) split out from the retired CMD_GYRO_ORIENT flags bit 3.

**Version acceptance (deliberate, #819):** children accept v3+ headers (`main/UdpCommon.cpp`), but the parent's synchronous STATUS_RESP wait accepts v4/v5 only (`main/Parent.cpp`).

**#825 gyro handshake:** press-Start sends a fresh 16-bit nonce; orchestrator replies with CLAIM_ACK echoing the nonce. Gyro advances UI only on matching ACK; CLAIM_DENIED reverts; ~1.5s overall timeout reverts with "NO RESPONSE". Server arms a 1.5s timer after CLAIM_ACK that releases the claim if no orient arrives (orphan-claim guard). Press-Stop carries a nonce too and is ACKed via STOP_ACK. Both sides exchange 2s heartbeats — gyro→parent HB_REP carries `uiState + claimNonce` so divergent state is reconciled (gyro IDLE + server claim → release; gyro ACTIVE + no server claim → reconstruct, the orchestrator-restart bootstrap path). New CMD codes (0x6A/0x6B/0x6C) are back-compat-safe — older firmware silently ignores them; UDP_VERSION stays at 5.

**PONG (134 bytes / 142 total):** `hostname[10] altName[16] description[32] stringCount(1) PongStrings×8 fwMajor(1) fwMinor(1) fwPatch(1)` where `PongString = <HHBBHB>` (`ledCount, lengthMm, ledType, cableDir, cableMm, stripDir`). `cableDir` bit 0 = folded. (`fwPatch` added v5.3.6; parent still accepts v3 139-byte and v4 141-byte PONGs.)

`wifiRssi` is stored as `uint8_t` absolute magnitude (e.g. 69 → -69 dBm); check `> 0`.

**Per-board limits** (`MAX_STR_PER_CHILD = 8` is a *protocol* constant — all wire structs are sized for 8 strings regardless of board):

| Board               | `CHILD_MAX_STRINGS` | Storage                            |
|---------------------|---------------------|------------------------------------|
| D1 Mini             | 2                   | EEPROM (flash-backed)              |
| ESP32               | 8                   | NVS Preferences (`"slyled"` ns)    |
| Giga Child          | 1                   | NVS Preferences; 1 onboard RGB LED |

## Aim-pipeline change checklist (#733, reworked under #784)

The legacy IK modules (`mover_calibrator.py`, `coverage_math.py`, `sphere_model.py`,
`parametric_mover.py`) and the SMART-pipeline emulator were deleted in #784 — the
`desktop/shared/aim/` package is the only IK. Any PR touching `desktop/shared/aim/`
(`sphere.py`, `stage_frame.py`, `profile_mechanics.py`, `park.py`, `routes.py`, `_rotmat.py`),
`parent_server.py` cal routes, `mover_control.py`, `surface_analyzer.py` cal helpers, or
`dmx_profiles.py` channel-map shape **must**:

1. Run the offline aim gates and confirm exit 0:
   - `python tests/aim/test_sphere.py` — AimSphere slope-from-home model (#799):
     `aim_direction`/`aim_xyz` round-trips, multi-valued pose selection, unreachable targets.
   - `python tests/aim/test_routes.py` — `POST /api/mover/<fid>/aim` contract against the
     live Flask app: `{x,y,z}` and `{azDeg,elDeg}` forms, unreachable / not-found /
     not-a-mover / incomplete-fixture errors.
   (`tests/aim/test_stage_frame.py` and `tests/aim/test_profile_mechanics.py` cover the
   mechanical ↔ stage-frame conversions the sphere builds on — run them too when touching
   those modules.)
2. Add a test case if the PR introduces a new failure mode.
3. Land the test alongside the fix in the same commit.

## Tests

All commands run from project root. Wrap with `powershell.exe -Command "python -X utf8 …"` on Windows.

| Suite                                  | Coverage |
|----------------------------------------|----------|
| `tests/test_parent.py`                 | Parent API, action types, WLED, runners, schema (523 assertions) |
| `tests/test_spatial_math.py`           | Coordinate transforms, pan/tilt math (47) |
| `tests/aim/` (4 suites)                | AimSphere IK, mech↔stage frame, profile mechanics, `/aim` endpoint (121) |
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
(no editing) — Stage / Control / Status bottom-nav tabs; Settings is the top-right gear.
Long-press the SlyLED logo for instant blackout. Connection state pill replaces the
plain server-info text (Connected / Reconnecting / Offline).

**Control tab (v1.8.1, #888):** persistent NowPlayingAnchor over a 4-page
HorizontalPager — `Master` (default) / `Grab` / `Fixtures` / `Shows`. Page composables
live under `ui/screens/control/pages/`; per-fixture profile sheet under `overlays/`;
profile-driven shortcut renderer under `shortcuts/FixtureShortcuts.kt` (Kotlin twin of
`desktop/shared/spa/js/fixture_shortcuts.js`). Haptics catalogue in
`ui/screens/control/haptics/Haptics.kt`. Connection state machine + pill in
`ui/screens/control/conn/`. Design doc: `docs/design/mobile_ui_redesign.md` (v3).
Phase tracking in issues #15–#19.

## Git / GitHub

- Remote: `https://github.com/SlyWombat/SlyLED`
- `arduino_secrets.h` is gitignored — never commit credentials.
- Commits: `feat: <short description>`; reference issues (`feat: mDNS discovery (closes #1)`).
- All features tracked in [GitHub Issues](https://github.com/SlyWombat/SlyLED/issues).
- Releases: `gh release create` with binaries. App reset to v1.0 (April 2026); firmware tracks per-board in `firmware/registry.json`.
