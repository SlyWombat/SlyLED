# Hardware Reference

## Supported boards

### Arduino Giga R1 WiFi

MCU: STM32H747XI (dual-core: Cortex-M7 @ 480 MHz + Cortex-M4 @ 240 MHz)
FQBN: `arduino:mbed_giga:giga`
OS: Mbed OS (runs on M7; M4 available for future use)

#### Onboard RGB LED

| Constant | Pin | Active state |
|----------|-----|-------------|
| `LEDR` | 86 | LOW = on, HIGH = off |
| `LEDG` | 87 | LOW = on, HIGH = off |
| `LEDB` | 88 | LOW = on, HIGH = off |

All three are **active-low**. To turn the red LED on: `digitalWrite(LEDR, LOW)`.

#### First-time Windows setup (Giga)

The Giga uses a DFU bootloader (USB ID `2341:0366`) for flashing. Windows requires the WinUSB driver installed via [Zadig](https://zadig.akeo.ie) before `arduino-cli` can upload.

1. Install Zadig
2. Double-press the reset button to enter bootloader mode (LED will pulse)
3. Select the device with USB ID `2341:0366`, choose **WinUSB**, click Install Driver
4. This only needs to be done once per machine

#### Uploading firmware (Giga)

The board must be in bootloader mode for every upload:

1. Double-press the reset button (the onboard LED pulses slowly)
2. Run the build script:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board giga -Port COM7
```

---

### ESP32 Dev Module (QuinLED-Duo)

MCU: Xtensa LX6 dual-core @ 240 MHz
FQBN: `esp32:esp32:esp32`
OS: FreeRTOS

#### LED strip wiring

| Signal | GPIO | Notes |
|--------|------|-------|
| Data | 2 | WS2812B data line |
| GND | GND | Common ground with strip |
| 5V | 5V | Strip power (separate supply recommended for many LEDs) |

8× WS2812B LEDs. LED type in sketch: `WS2812B`, color order `GRB`.

#### Uploading firmware (ESP32)

No manual bootloader step required — the board auto-resets via DTR/RTS:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board esp32 -Port COM7
```

---

### LOLIN(WEMOS) D1 R2 & mini (QuinLED-Duo)

MCU: ESP8266EX (Xtensa LX106 @ 80 MHz)
FQBN: `esp8266:esp8266:d1_mini`
Board macro: `ARDUINO_ESP8266_WEMOS_D1MINI`

#### LED strip wiring

| Signal | GPIO | D1 Mini label | Notes |
|--------|------|--------------|-------|
| Data | 2 | D4 | WS2812B data line |
| GND | GND | G | Common ground with strip |
| 5V | 5V | 5V | Strip power |

8× WS2812B LEDs. LED type in sketch: `WS2812B`, color order `GRB`.

#### Uploading firmware (D1 Mini)

No manual bootloader step required — the board auto-resets via DTR/RTS. The CH340 USB-to-serial chip appears as **USB-SERIAL CH340** in Device Manager:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Board d1mini -Port COM9
```

#### D1 Mini: single-threaded architecture note

The ESP8266 is single-core and the Arduino core does not expose FreeRTOS task creation. LED animation runs via `updateLED()` called on every `loop()` iteration using `millis()`-based timing — no blocking delays in the animation path. `updateLED()` is also called inside the HTTP request wait loops so animation continues while serving clients.

---

## Arduino CLI setup

`arduino-cli` is installed at `%LOCALAPPDATA%\Arduino\arduino-cli.exe` (not on PATH).

Install cores (one-time):
```powershell
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core update-index
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core install arduino:mbed_giga
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core install esp32:esp32
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core install esp8266:esp8266
```

The ESP8266 core requires its board manager URL to be added first:
```powershell
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" config add board_manager.additional_urls https://arduino.esp8266.com/stable/package_esp8266com_index.json
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core update-index
```

Install FastLED (used by ESP32 and D1 Mini):
```powershell
$env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" lib install "FastLED"
```

`build.ps1` sets `ARDUINO_DIRECTORIES_USER` automatically so `./libraries` is always found.

---

## WiFi credentials

Store in `main/arduino_secrets.h` (gitignored — never commit this file):

```cpp
#define SECRET_SSID "your-network-name"
#define SECRET_PASS "your-password"
```

---

## Serial debug output

Connect a serial terminal at **115200 baud**. The sketch prints:
- `=== BOOT ===` on startup
- WiFi connection progress and assigned IP
- NTP sync result
- Every 3 seconds: `IP: x.x.x.x  WiFi: OK  LED: Rainbow`

---

## Known hardware quirks

### Giga: never use `analogWrite()` on LED pins
Calling `analogWrite()` on LEDR/LEDG/LEDB crashes Mbed OS. Symptom: red LED blinks 4 fast then 4 slow repeatedly. Use `digitalWrite()` only and implement software PWM with `delayMicroseconds()`.

### Giga: FastLED is not compatible
FastLED crashes on the Giga R1 (timer/DMA conflicts with Mbed OS). The Giga path uses a custom `hueToRGB()` + software PWM pipeline.

### Giga: Serial blocks without a terminal
`Serial.print()` blocks indefinitely on Mbed OS if no USB CDC terminal is connected and the TX buffer fills. Every `Serial` call must be guarded:
```cpp
if (Serial) Serial.println(F("message"));
```

### All boards: WiFiServer single-connection slot
`WiFiServer` handles one TCP connection at a time. The SPA+AJAX architecture eliminates the favicon race condition — button presses use `XMLHttpRequest` (no page navigation, no favicon fetch on button press). The `/favicon.ico` route returns a fast 404 to clear the slot immediately.

### All boards: WiFi hostname must be set before begin()
The hostname is sent in the DHCP DISCOVER packet. Calling hostname/setHostname after `begin()` means the first DHCP handshake goes out without it.
```cpp
// Giga / ESP32:
WiFi.setHostname("slyled");
// D1 Mini (ESP8266):
WiFi.hostname("slyled");
WiFi.begin(SECRET_SSID, SECRET_PASS);  // always after hostname
```

### D1 Mini: TCP close sends RST without sufficient drain time
On ESP8266, calling `tcp_close()` while data is still in the lwIP send buffer causes `tcp_abort()` (RST) instead of a graceful FIN. The sketch yields for 200 ms after each response to allow ACKs to be received before closing. JSON responses include `Content-Length` so HTTP clients read the exact byte count and do not depend on connection close behaviour.

### D1 Mini: WiFi.mode(WIFI_STA) required
Without this call, the ESP8266 may start in AP+STA mode. Always call before `WiFi.begin()`.
