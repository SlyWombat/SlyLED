# SlyLED

Multi-board LED controller with a parent/child architecture. The **Windows Parent** serves a browser UI, coordinates timing, and dispatches commands; **child boards** (ESP32 / D1 Mini) own the physical LED strips and execute actions locally in NTP-synchronized time.

**Current version: v3.6**

## Hardware

| Role | Board | LEDs |
|------|-------|------|
| Parent | Windows 11 x64 (SlyLED.exe) | None |
| Parent Runtime *(future)* | Arduino Giga R1 WiFi (STM32H747) | None |
| Child | ESP32 (QuinLED Quad / Uno) | Up to 4× WS2812B strips |
| Child | ESP8266 D1 Mini (LOLIN/WEMOS) | Up to 4× WS2812B strips |

## Architecture

The **Windows Parent** runs as a system-tray app (`SlyLED.exe`). It serves a 6-tab SPA on HTTP port 8080, manages a child registry, positions children on a 2D canvas, defines runners, pre-computes per-child LED ranges, and dispatches commands via UDP port 4210.

A future runtime version of the parent will run on the Giga board with a minimal UI — useful once the layout and runner are designed and saved.

All three board targets share one sketch (`main/main.ino`) with `#ifdef BOARD_GIGA / BOARD_ESP32 / BOARD_D1MINI` guards.

**Children (ESP32 / D1 Mini)** — receive UDP commands, execute LED actions (Solid / Flash / Wipe / Off) against assigned LED ranges, and run pre-loaded runner steps triggered by a synchronized epoch timestamp. Config and string layout are persisted in EEPROM and reported to the parent via CMD_PONG.

### Communication

- **UDP port 4210** — binary protocol with 8-byte header (magic `0x534C`, version 2, command, epoch). Commands: PING/PONG (discovery), ACTION/ACTION_STOP (immediate), LOAD_STEP/LOAD_ACK (runner loading), RUNNER_GO/RUNNER_STOP (synchronized execution), STATUS_REQ/STATUS_RESP.
- **NTP** — all boards sync to `pool.ntp.org` on boot; epoch timestamps make runner execution deterministic across children (±50 ms jitter tolerable).

### Parent SPA tabs

| Tab | Function |
|-----|----------|
| Dashboard | Children table, runner progress bar, Stop / Go buttons |
| Setup | **Discover** children on the network (UDP broadcast, shows unregistered nodes), add / remove / refresh, details modal, JSON import/export |
| Layout | Sidebar lists unplaced children; drag onto 900×450 canvas to position; detailed view shows LED strings with direction/length; double-click node to edit position or remove; metric or imperial |
| Actions | Send immediate Solid / Flash / Wipe / Off to any child |
| Runtime | Create runners, add/edit steps (action + area-of-effect + duration), Compute / Sync / Start |
| Settings | Dark mode, units, canvas dimensions, parent name; **Factory Reset** clears all children / runners / layout |

### Child config page

Each child serves `GET /` (config page) — a self-contained HTML form for configuring the node:

- Alternate name and description
- Number of LED strings (1–4)
- Per string: LED count, strip length (mm), LED type (WS2812B / WS2811 / APA102), cable direction and length from node, strip direction

`POST /config` saves to EEPROM (Preferences on ESP32, EEPROM.h on D1 Mini) and broadcasts an updated CMD_PONG so the parent refreshes automatically. Settings survive power cycles; hostname is always auto-generated from MAC.

## Quick start — Windows Parent

1. Run the installer: `desktop/windows/dist/SlyLED-Parent-Setup.exe`
   - Installs to `%ProgramFiles%\SlyLED` (or `%LOCALAPPDATA%\Programs\SlyLED` if no admin)
   - Adds firewall rules for UDP 4210 and TCP 8080
   - Optional desktop shortcut and Windows startup entry

2. SlyLED starts and opens a browser to `http://localhost:8080/`

3. The tray icon (system tray) has **Open** and **Quit** menu items.

4. Flash children (see below) and they will auto-appear on the Dashboard within 30 s.

## Quick start — Child firmware

1. Copy `main/arduino_secrets.h.example` to `main/arduino_secrets.h` and fill in your WiFi credentials.

2. **ESP32** — connect via USB, then upload:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board esp32 -Port COM8
   ```

3. **D1 Mini** — connect via USB, then upload:
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board d1mini -Port COM9
   ```

4. On each child, open `http://<child-ip>/` to set its name, description, and LED string layout.

> **Compile only (no upload):** add `-NoUpload` to any build command.
> `build.ps1` auto-increments `APP_MINOR` in `main/version.h` on every compile.

## Building the Windows Parent from source

```batch
cd desktop\windows
build.bat
```

Produces `dist\SlyLED.exe` (standalone) and `dist\SlyLED-Parent-Setup.exe` (installer).
Requires Python 3.11+ on PATH and [Inno Setup 6](https://jrsoftware.org/isinfo.php) (install via `winget install JRSoftware.InnoSetup`).

## Project layout

```
main/
  main.ino              — Single sketch for all boards
  version.h             — APP_MAJOR / APP_MINOR
  arduino_secrets.h     — WiFi credentials (gitignored)
  arduino_secrets.h.example
desktop/
  shared/
    parent_server.py    — Flask REST API + UDP parent
    main.py             — System tray launcher (pystray)
    spa/
      index.html        — 6-tab SPA (inline CSS + JS)
  windows/
    build.bat           — Build SlyLED.exe + installer
    build.py            — PyInstaller helper (avoids path-with-spaces issues)
    installer.iss       — Inno Setup 6 script
    requirements.txt    — flask, pystray, pillow
tests/
  test_web.py           — 105-check HTTP/JSON API test suite (parent)
  test_child.py         — Child firmware tests (JS integrity, UDP, runners)
docs/
  ARCHITECTURE.md       — Full design: threading, UDP protocol, data structures
  API.md                — Windows Parent REST API reference
  HARDWARE.md           — Board wiring, Arduino CLI setup, quirks
  PATTERNS.md           — LED pattern reference + how to add new patterns
build.ps1               — PowerShell build/upload script (-Board esp32|d1mini|giga)
arduino-cli.yaml        — Sets project folder as Arduino user directory
```

## Running the tests

### Parent (Windows)

Start the parent server, then from WSL:

```bash
cd tests
python test_web.py localhost:8080
```

Covers: connectivity, SPA structure (sidebar, canvas, drag-drop), cache headers, `/status`, children CRUD + import/export + status poll + IP sanitization, layout `positioned` flag + multi-child placement/removal round-trip, settings round-trip, full runner lifecycle (create / PUT steps / compute / stop / delete), error handling, MAX_RUNNERS overflow, Content-Length headers, mock UDP child (PING/PONG/ACTION/STATUS). **184 checks.**

### Child (ESP32 / D1 Mini)

With a child on the network:

```bash
python tests/test_child.py <child-ip>
```

Covers: config page JS integrity (sendBuf truncation detection), HTTP routes, UDP ping/pong, action dispatch, runner loading and epoch-synchronized start.

## Flash usage (v3.6)

| Board | Flash | RAM |
|-------|-------|-----|
| Giga | ~310 KB / 1966 KB (16%) | ~81 KB / 524 KB (15%) |
| ESP32 | ~1030 KB / 1311 KB (79%) | ~50 KB / 328 KB (15%) |
| D1 Mini | ~270 KB / 1049 KB (26%) | ~32 KB / 80 KB (40%) |

ESP32 flash is the tightest constraint. Check usage after every new feature.

## License

Private project.
