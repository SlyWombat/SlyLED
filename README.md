# SlyLED

Multi-board Arduino LED controller with a parent/child architecture. One parent board (Giga R1 WiFi) serves a browser UI and coordinates timing; child boards (ESP32 / D1 Mini) own the LED strips and execute actions locally in NTP-synchronized time.

**Current version: v3.0**

## Hardware

| Role | Board | LEDs |
|------|-------|------|
| Parent | Arduino Giga R1 WiFi (STM32H747) | None |
| Child | ESP32 (QuinLED Quad / Uno) | Up to 4× WS2812B strips, GPIO 2 |
| Child | ESP8266 D1 Mini (LOLIN/WEMOS) | Up to 4× WS2812B strips, GPIO 2 |

## Architecture

All three boards share one sketch (`main/main.ino`) with `#ifdef BOARD_GIGA / BOARD_ESP32 / BOARD_D1MINI` guards.

**Parent (Giga)** — serves a 6-tab SPA, manages a child registry, positions children on a 2D canvas, defines runners, pre-computes per-child LED ranges, and dispatches commands via UDP.

**Children (ESP32 / D1 Mini)** — receive UDP commands, execute LED actions (Solid / Flash / Wipe / Off) against assigned LED ranges, and run pre-loaded runner steps triggered by a synchronized epoch timestamp. Config and string layout are persisted in EEPROM and reported to the parent via CMD_PONG.

### Communication

- **UDP port 4210** — binary protocol with 8-byte header (magic `0x534C`, version, command, epoch). Commands: PING/PONG (discovery), ACTION/ACTION_STOP (immediate), LOAD_STEP/LOAD_ACK (runner loading), RUNNER_GO/RUNNER_STOP (synchronized execution), STATUS_REQ/STATUS_RESP.
- **NTP** — all boards sync to `pool.ntp.org`; epoch timestamps make runner execution deterministic across children (±50 ms jitter tolerable).

### Parent SPA tabs

| Tab | Function |
|-----|----------|
| Dashboard | Children table, runner progress bar, Stop / Go buttons |
| Setup | Add / remove / refresh children, details modal, JSON import/export, app settings (dark mode, units, canvas size) |
| Layout | Drag-and-drop canvas positioning of children and their LED strings (metric or imperial) |
| Actions | Send immediate Solid / Flash / Wipe / Off to any child |
| Runtime | Create runners, add/edit steps (action + area-of-effect + duration), Compute / Sync / Start |
| Settings | Dark mode, units, canvas dimensions, parent name |

### Child config page

Each child serves `GET /config` — a self-contained HTML form for configuring the node:

- Alternate name and description
- Number of LED strings (1–4)
- Per string: LED count, strip length (mm), LED type (WS2812B / WS2811 / APA102), cable direction and length from node, strip direction

`POST /config` saves to EEPROM (Preferences on ESP32, EEPROM.h on D1 Mini) and broadcasts an updated CMD_PONG so the parent refreshes automatically. Settings survive power cycles; hostname is always auto-generated from MAC.

## Quick start

1. Copy `main/arduino_secrets.h.example` to `main/arduino_secrets.h` and fill in your WiFi credentials.

2. **Giga** — double-press reset to enter bootloader, then upload:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board giga -Port COM7
   ```

3. **ESP32** — connect via USB, then upload:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board esp32 -Port COM8
   ```

4. **D1 Mini** — connect via USB, then upload:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board d1mini -Port COM9
   ```

5. Open `http://slyled/` (or `http://<giga-ip>/`) in a browser.

6. On each child, open `http://<child-ip>/config` to set its name, description, and LED string layout.

> **Compile only (no upload):** add `-NoUpload` to any build command.
> `build.ps1` auto-increments `APP_MINOR` in `main/version.h` on every compile.

## Project layout

```
main/
  main.ino              — Single sketch for all three boards
  version.h             — APP_MAJOR / APP_MINOR
  arduino_secrets.h     — WiFi credentials (gitignored)
  arduino_secrets.h.example
tests/
  test_web.py           — HTTP/JSON API test suite (~70 checks, parent only)
docs/
  PHASE2_DESIGN.md      — Full protocol, data structures, and implementation roadmap
build.ps1               — PowerShell build/upload script (-Board giga|esp32|d1mini)
arduino-cli.yaml        — Sets project folder as Arduino user directory
```

## Running the tests

Tests run against the parent (Giga). From WSL:

```powershell
powershell.exe -Command "python -X utf8 tests/test_web.py 192.168.10.219"
```

Covers: connectivity, SPA structure, `/status`, children CRUD + import/export + status poll, layout, settings round-trip, full runner lifecycle (create / PUT steps / compute / stop / delete), error handling, max-runner overflow, Content-Length headers.

## Flash usage (v2.10)

| Board | Flash | RAM |
|-------|-------|-----|
| Giga | ~310 KB / 1966 KB (16%) | ~81 KB / 524 KB (15%) |
| ESP32 | ~1030 KB / 1311 KB (79%) | ~50 KB / 328 KB (15%) |
| D1 Mini | ~270 KB / 1049 KB (26%) | ~32 KB / 80 KB (40%) |

## License

Private project.
