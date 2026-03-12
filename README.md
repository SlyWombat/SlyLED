# SlyLED

Arduino Giga R1 WiFi project for onboard RGB LED effects with a WiFi web interface.

- Control LED patterns from any browser on the local network
- Module-based architecture — easy to add new patterns and hardware modules
- SPA + JSON API — single-page app polls status every 2 s; button presses use AJAX
- Two-thread Mbed RTOS design — LED animation runs on a dedicated thread, completely independent of WiFi I/O

## Current patterns

| Pattern | Description |
|---------|-------------|
| **Rainbow** | Smooth hue cycle through the full colour spectrum |
| **Siren** | Alternating red and blue, 350 ms per phase |

## Hardware

- **Board:** Arduino Giga R1 WiFi (`arduino:mbed_giga:giga`)
- **LED:** Onboard RGB — pins LEDR (86), LEDG (87), LEDB (88), active-low

## Quick start

1. Copy `main/arduino_secrets.h.example` to `main/arduino_secrets.h` and fill in your WiFi credentials
2. Double-press the reset button to enter bootloader mode
3. Run the build script (auto-increments the minor version on every compile):

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

4. Open `http://<board-ip>/` in a browser

## Project layout

```
main/
  main.ino          — Sketch (SPA server + LED animation)
  version.h         — APP_MAJOR / APP_MINOR (build.ps1 auto-increments minor)
  arduino_secrets.h — WiFi credentials (gitignored)
tests/
  test_web.py       — HTTP/JSON API test suite (75 tests)
docs/
  ARCHITECTURE.md   — Threading model, module design, code structure
  API.md            — Complete HTTP API reference
  HARDWARE.md       — Hardware setup, pin reference, known quirks
  PATTERNS.md       — LED pattern reference and guide for adding new patterns
build.ps1           — PowerShell build/upload script
arduino-cli.yaml    — Sets project folder as Arduino user directory
```

## Running the tests

From WSL (WSL2 cannot reach the Windows WiFi adapter directly):

```powershell
powershell.exe -Command "python -X utf8 tests/test_web.py 192.168.10.219"
```

## Documentation

See the [`docs/`](docs/) folder for full technical documentation.

## License

Private project.
