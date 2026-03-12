# Project memory — key points & learnings

Use this file to track hardware quirks, fixes, and decisions so we don’t repeat mistakes and can onboard quickly.

---

## Giga R1 WiFi — onboard RGB LED

- **Pins:** `LEDR` (86), `LEDG` (87), `LEDB` (88). **Active-low** (LOW = on, HIGH = off).
- **Do not use `analogWrite()`** on these pins. It can crash Mbed OS (red blink: 4 fast + 4 slow).
- **Use `digitalWrite()` only** for reliability. For smooth brightness use **software PWM** (toggle pins in a tight loop with `delayMicroseconds()`).
- **Smooth rainbow:** Implemented in `main.ino` with hue→RGB and software PWM (256 steps per cycle, ~2 ms cycle, display each hue ~35 ms). No FastLED, no analogWrite.
- **FastLED** is not reliable on Giga R1 (crashes / compatibility issues). Prefer custom hue→RGB and digital or software-PWM for the built-in LED.

## Build & deploy

- **Sketch location:** `main/main.ino` (folder name must match main .ino for arduino-cli).
- **Libraries:** Install FastLED (or others) with `ARDUINO_DIRECTORIES_USER` set to this project so `./libraries` is used; `libraries/` is in `.gitignore`.
- **Upload:** Board often appears as COM4 or COM5; run `arduino-cli board list` to get the port. Use `arduino-cli compile --upload --port <PORT> --fqbn arduino:mbed_giga:giga main`.

## Git & GitHub

- **Remote:** https://github.com/SlyWombat/Giga-LED-Project
- After “Ship it” / deploy, offer to “Sync to GitHub” (commit + push to `main`). Never commit `secrets.h` or credentials.

---

*Add new items under a clear heading (e.g. “WiFi”, “external strip”, “RTC”) with short bullets and dates if useful.*
