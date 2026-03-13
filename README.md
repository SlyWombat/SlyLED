# SlyLED

WiFi LED controller with a browser-based interface. Supports three boards from a single sketch.

- Control LED patterns from any browser on the local network
- SPA + JSON API — single-page app polls status every 2 s; button presses use AJAX
- Module-based architecture — easy to add new patterns

## Supported boards

| Board | FQBN | LED hardware | Threading |
|-------|------|-------------|-----------|
| Arduino Giga R1 WiFi | `arduino:mbed_giga:giga` | Onboard RGB (active-low) | Mbed RTOS dedicated thread |
| ESP32 Dev Module | `esp32:esp32:esp32` | WS2812B strip, GPIO 2 | FreeRTOS task on Core 0 |
| LOLIN D1 Mini | `esp8266:esp8266:d1_mini` | WS2812B strip, GPIO 2 | Non-blocking loop |

## Current patterns

| Pattern | Description |
|---------|-------------|
| **Rainbow** | Smooth hue cycle — single colour sweep (Giga) or spread across all LEDs (strip) |
| **Siren** | Alternating red and blue, 350 ms per phase |

## Quick start

1. Copy `main/arduino_secrets.h.example` to `main/arduino_secrets.h` and fill in your WiFi credentials
2. Run the build script — auto-detects the connected board, increments the minor version on upload:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1
```

Or specify the board explicitly:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board esp32 -Port COM7
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board d1mini -Port COM9
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board giga  -Port COM7
```

3. Open `http://slyled/` or `http://<board-ip>/` in a browser

**Giga only:** double-press the reset button to enter bootloader mode before uploading.

## Project layout

```
main/
  main.ino          — Single sketch for all three boards (#ifdef per board)
  version.h         — APP_MAJOR / APP_MINOR (build.ps1 auto-increments minor)
  arduino_secrets.h — WiFi credentials (gitignored)
tests/
  test_web.py       — HTTP/JSON API test suite (75 tests, board-agnostic)
docs/
  ARCHITECTURE.md   — Threading model, module design, code structure
  API.md            — Complete HTTP API reference
  HARDWARE.md       — Board setup, pin reference, known quirks
  PATTERNS.md       — LED pattern reference and guide for adding new patterns
build.ps1           — PowerShell build/upload script (-Board giga|esp32|d1mini)
arduino-cli.yaml    — Sets project folder as Arduino user directory
```

## Running the tests

```powershell
powershell.exe -Command "python -X utf8 tests/test_web.py <board-ip>"
```

## Documentation

See the [`docs/`](docs/) folder for full technical documentation.

## License

Private project.
