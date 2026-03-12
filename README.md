# Giga LED Project

Arduino Giga R1 WiFi project for onboard RGB LED effects. The sketch cycles the built-in LED through rainbow colors using [FastLED](https://github.com/FastLED/FastLED).

## Hardware

- **Board:** Arduino Giga R1 WiFi (`arduino:mbed_giga:giga`)
- **LED:** Onboard RGB on pins LEDR, LEDG, LEDB (active-low)

## Setup

1. **Arduino CLI**  
   Install [arduino-cli](https://arduino.github.io/arduino-cli/) and the Giga core:

   ```bash
   arduino-cli core update-index
   arduino-cli core install arduino:mbed_giga
   ```

2. **FastLED**  
   From this project folder (so the library installs into `./libraries`):

   ```powershell
   $env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
   arduino-cli lib install "FastLED"
   ```

## Build & upload

- **Compile only:**  
  `.\test.ps1`

- **Compile and upload to connected Giga:**  
  `.\test.ps1 -Upload`

The script uses this project as the Arduino user directory so `./libraries` (e.g. FastLED) is found. Optionally, run from this folder with `arduino-cli.yaml` in place so the same path is used.

## Project layout

- `main/main.ino` — Rainbow cycle sketch
- `test.ps1` — Build/test script (compile ± upload)
- `arduino-cli.yaml` — Config for project-local user directory
- `libraries/` — Local libraries (FastLED); not in git (see Setup)

## License

Private project.
