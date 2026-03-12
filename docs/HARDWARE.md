# Hardware Reference

## Board

**Arduino Giga R1 WiFi**
MCU: STM32H747XI (dual-core: Cortex-M7 @ 480 MHz + Cortex-M4 @ 240 MHz)
FQBN: `arduino:mbed_giga:giga`
OS: Mbed OS (runs on M7; M4 available for future use)

## Onboard RGB LED

| Constant | Pin | Active state |
|----------|-----|-------------|
| `LEDR` | 86 | LOW = on, HIGH = off |
| `LEDG` | 87 | LOW = on, HIGH = off |
| `LEDB` | 88 | LOW = on, HIGH = off |

All three are **active-low**. To turn the red LED on: `digitalWrite(LEDR, LOW)`.

## First-time Windows setup

The Giga uses a DFU bootloader (USB ID `2341:0366`) for flashing. Windows requires the WinUSB driver to be installed before `arduino-cli` can upload.

1. Install [Zadig](https://zadig.akeo.ie)
2. Double-press the reset button to enter bootloader mode (the LED will pulse)
3. In Zadig: select the device with USB ID `2341:0366`, choose **WinUSB**, click Install Driver
4. This only needs to be done once per machine

## Uploading firmware

The board must be in bootloader mode for every upload:

1. Double-press the reset button (the onboard LED pulses slowly when in bootloader mode)
2. Run the build script:

```powershell
powershell.exe -ExecutionPolicy Bypass -File build.ps1 -Port COM7
```

The port is typically **COM7**. To find it: `& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" board list`

## Arduino CLI setup

`arduino-cli` is installed at `%LOCALAPPDATA%\Arduino\arduino-cli.exe` (not on PATH).

Install the Giga core (one-time):
```powershell
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core update-index
& "$env:LOCALAPPDATA\Arduino\arduino-cli.exe" core install arduino:mbed_giga
```

Always set the user directory before compiling so `./libraries` is found:
```powershell
$env:ARDUINO_DIRECTORIES_USER = (Get-Location).Path
```
`build.ps1` handles this automatically.

## Known hardware quirks

### Never use `analogWrite()` on LED pins
Calling `analogWrite()` on LEDR/LEDG/LEDB crashes Mbed OS. Symptom: red LED blinks 4 fast then 4 slow repeatedly. Use `digitalWrite()` only. For dimming/fading, implement software PWM in a tight loop with `delayMicroseconds()`.

### FastLED is not compatible
FastLED crashes on the Giga R1 (timer/DMA conflicts with Mbed OS). This sketch uses a custom `hueToRGB()` + software PWM pipeline instead.

### Serial blocks without a terminal
`Serial.print()` blocks indefinitely on Mbed OS if no USB CDC terminal is connected and the TX buffer fills. Every `Serial` call must be guarded:
```cpp
if (Serial) Serial.println(F("message"));
```

### WiFiServer single-connection slot
`WiFiServer` handles one TCP connection at a time. Chrome/Edge automatically fetch `favicon.ico` in a second parallel connection on every page load. If favicon occupies the slot, the user's button press waits in queue.

**Fix in this sketch:** SPA+AJAX architecture — button presses use `XMLHttpRequest`, not form POSTs, so no page navigation occurs and no favicon is fetched on button press. The favicon route returns a fast 404 to clear the slot immediately.

### WiFi.setHostname() must come before WiFi.begin()
The hostname is sent in the DHCP DISCOVER packet (option 12). Calling `setHostname()` after `begin()` means the first DHCP handshake goes out without the hostname — it won't appear in router lease tables.

```cpp
WiFi.setHostname("slyled");  // BEFORE begin()
WiFi.begin(SECRET_SSID, SECRET_PASS);
```

## WiFi credentials

Store in `main/arduino_secrets.h` (gitignored — never commit this file):

```cpp
#define SECRET_SSID "your-network-name"
#define SECRET_PASS "your-password"
```

## Serial debug output

Connect a serial terminal at **115200 baud**. The sketch prints:
- `=== BOOT ===` on startup
- WiFi connection progress and assigned IP
- NTP sync result
- Every 3 seconds: `IP: x.x.x.x  WiFi: OK  LED: Rainbow`
