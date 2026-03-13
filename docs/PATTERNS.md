# LED Patterns

## Current patterns

### Rainbow

Smooth hue cycle through the full colour spectrum.

**Enable:** `POST /led/on`
**Status feature field:** `"rainbow"`

#### Giga R1 — software PWM

| Parameter | Value | Description |
|-----------|-------|-------------|
| `HUE_STEP` | 2 | Hue increment per step (0–255 range, 128 steps per cycle) |
| `DISPLAY_MS` | 35 ms | How long each colour is held |
| `PWM_CYCLE_US` | 2048 µs | One full software PWM cycle |
| `PWM_STEPS` | 256 | PWM resolution |
| `STEP_US` | 8 µs | Time per PWM step |

Full cycle duration: 128 steps × 35 ms ≈ **4.5 seconds** per rainbow loop.

Implemented via `hueToRGB()` + `pwmCycle()` + `setRGBFor()` in `ledTask()`. See [Software PWM reference](#software-pwm-reference) below.

#### ESP32 / D1 Mini — FastLED

| Parameter | Value | Description |
|-----------|-------|-------------|
| `RAINBOW_DELAY` | 20 ms | Delay between hue steps |
| Hue increment | 1 per frame | Advances `hue` by 1 each step |

**ESP32** — `fill_rainbow(leds, NUM_LEDS, hue, 255/NUM_LEDS)` spreads the full spectrum across all 8 LEDs simultaneously. Runs in a FreeRTOS task on Core 0 with `delay(RAINBOW_DELAY)`.

**D1 Mini** — same `fill_rainbow()` call, but runs non-blocking via `updateLED()`. `lastFrame` tracks `millis()`; advances only when `RAINBOW_DELAY` has elapsed. Called from `loop()` and from inside all HTTP wait loops so animation continues while serving clients.

Full cycle: 256 hue steps × 20 ms ≈ **5.1 seconds** per loop.

---

### Siren

Alternating red and blue flashes.

**Enable:** `POST /led/siren/on`
**Status feature field:** `"siren"`

| Parameter | Value | Description |
|-----------|-------|-------------|
| `SIREN_HALF_MS` | 350 ms | Duration of each colour phase |

Full cycle: 700 ms (≈ 1.4 Hz).

#### Giga R1 — software PWM

Each phase calls `setRGBFor(255, 0, 0)` then `setRGBFor(0, 0, 255)` in `ledTask()`. Active-low pins: red = LEDR LOW, blue = LEDB LOW.

#### ESP32 — FastLED

`fill_solid(leds, NUM_LEDS, CRGB::Red)` / `fill_solid(leds, NUM_LEDS, CRGB::Blue)` + `FastLED.show()` with `delay(SIREN_HALF_MS)`. Runs in the dedicated FreeRTOS task on Core 0.

#### D1 Mini — FastLED non-blocking

`sirenPhase` and `sirenStart` track which colour is active and when the phase started. `updateLED()` flips the phase when `millis() - sirenStart >= SIREN_HALF_MS`. No blocking delay.

---

## How to add a new pattern

This checklist adds a new pattern to the **LED module**. All three boards must be handled.

### 1. Add the state flag (global scope)

```cpp
volatile bool ledPulseOn = false;  // new pattern
```

### 2. Add a `LedFeature` enum value

```cpp
enum LedFeature : uint8_t { FEAT_NONE = 0, FEAT_RAINBOW = 1, FEAT_SIREN = 2, FEAT_PULSE = 3 };
```

### 3. Add the HTTP route in `serveClient()`

Add before the `/led/on` branch:

```cpp
} else if (strstr(req, " /led/pulse/on ")) {
    ledRainbowOn = false;
    ledSirenOn   = false;
    ledPulseOn   = true;
    addLog(FEAT_PULSE, SRC_WEB, ip0, ip1, ip2, ip3);
    sendJsonOk(client);
```

Also clear the new flag in `/led/off`, `/led/on`, and `/led/siren/on`:
```cpp
ledPulseOn = false;
```

### 4. Add the animation code

#### Giga — add branch in `ledTask()`

```cpp
} else if (ledPulseOn) {
    // animation using only digitalWrite / pwmCycle / setRGBFor
    // do NOT call WiFi or handleClient here
    delay(5);
```

#### ESP32 — add branch in `ledTask(void*)`

```cpp
} else if (ledPulseOn) {
    // animation using fill_solid / FastLED.show / delay
    // delay() is fine here — this is a dedicated FreeRTOS task on Core 0
```

#### D1 Mini — add branch in `updateLED()`

Add static timing variables and use `millis()` — no `delay()`:

```cpp
} else if (ledPulseOn) {
    static unsigned long pulseStart = 0;
    static uint8_t       pulsePhase = 0;
    if (millis() - pulseStart >= PULSE_HALF_MS) {
        pulseStart = millis();
        pulsePhase ^= 1;
        fill_solid(leds, NUM_LEDS, pulsePhase ? CRGB::White : CRGB::Black);
        FastLED.show();
    }
```

### 5. Add a pattern row in `sendMain()` HTML

In the LED card:

```html
<div class='pattern-row'>
  <span class='pattern-name'>Pulse</span>
  <span>
    <span class='badge boff' id='badge-pulse'>OFF</span>
    <button class='btn btn-on' onclick='setFeature("pulse")'>Enable</button>
  </span>
</div>
```

### 6. Update `setFeature()` in the SPA JavaScript

```javascript
function setFeature(f){
  var path = f==='rainbow' ? '/led/on'
           : f==='siren'   ? '/led/siren/on'
           : f==='pulse'   ? '/led/pulse/on'
           :                 '/led/off';
  // ... rest unchanged
```

### 7. Update `applyState()` in the SPA JavaScript

```javascript
var pOn = f==='pulse';
var bp = document.getElementById('badge-pulse');
bp.textContent = pOn ? 'ON' : 'OFF';
bp.className   = 'badge ' + (pOn ? 'bon' : 'boff');
```

Add a header status case:
```javascript
else if(f==='pulse'){h.textContent='LED - Pulse ON';h.style.color='#fa0';}
```

### 8. Update `sendStatus()`

```cpp
const char* feat = ledRainbowOn ? "rainbow"
                 : ledSirenOn   ? "siren"
                 : ledPulseOn   ? "pulse"
                 :                "none";
```

### 9. Update `sendLog()` feature label

```cpp
} else if (logBuf[idx].feature == FEAT_PULSE) {
    featLabel = "Pulse"; color = "#fa0"; label = "ON";
```

### 10. Update the test suite

Add a section in `tests/test_web.py`:

```python
section("Enable pulse  POST /led/pulse/on")
code, data = post_json("/led/pulse/on")
check("HTTP 200",        code == 200)
check("Returns ok:true", data is not None and data.get("ok") is True)
time.sleep(0.4)
_, st = get_json("/status")
check("/status feature=pulse", led_feature(st) == "pulse", f"status: {st}")
```

---

## Software PWM reference (Giga only)

The Giga onboard LED pins do not support hardware PWM from Mbed OS — `analogWrite()` crashes (symptom: 4 fast + 4 slow red blinks). All brightness control uses software PWM:

```
pwmCycle(r, g, b):
  for step in 0..255:
    LEDR = (r > step) ? LOW : HIGH   ← active-low: low = on
    LEDG = (g > step) ? LOW : HIGH
    LEDB = (b > step) ? LOW : HIGH
    wait 8 µs
```

One cycle = 256 steps × 8 µs = **2.048 ms**. `setRGBFor(r, g, b)` repeats cycles for `DISPLAY_MS` ms (default 35 ms ≈ 17 cycles per colour hold).

Human eye flicker fusion threshold is ~50 Hz (20 ms). At 2 ms/cycle the PWM is invisible — the LED appears as a smooth mixed colour.

---

## FastLED reference (ESP32 / D1 Mini)

Both ESP boards use FastLED with **WS2812B** LEDs, colour order **GRB**, 8 LEDs on **GPIO 2**.

```cpp
#define DATA_PIN    2
#define NUM_LEDS    8
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB
CRGB leds[NUM_LEDS];

// In setup():
FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
FastLED.setBrightness(200);
```

Key functions:

| Function | Description |
|----------|-------------|
| `fill_rainbow(leds, NUM_LEDS, hue, deltaHue)` | Spread a rainbow across all LEDs starting at `hue` |
| `fill_solid(leds, NUM_LEDS, colour)` | Set all LEDs to a single colour |
| `FastLED.show()` | Push the `leds` array to the strip (required after every change) |

On ESP32, `FastLED.show()` is called from the dedicated Core 0 task — no interference with WiFi on Core 1. On D1 Mini, `FastLED.show()` is called only when state changes (not every loop tick) to minimise blocking time in the single-threaded environment.
