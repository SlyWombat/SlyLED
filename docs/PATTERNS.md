# LED Patterns

## Current patterns

### Rainbow

Smooth hue cycle through the full colour spectrum using software PWM.

| Parameter | Value | Description |
|-----------|-------|-------------|
| `HUE_STEP` | 2 | Hue increment per step (0–255 range, 128 steps per cycle) |
| `DISPLAY_MS` | 35 ms | How long each colour is held |
| `PWM_CYCLE_US` | 2048 µs | One full software PWM cycle |
| `PWM_STEPS` | 256 | PWM resolution |
| `STEP_US` | 8 µs | Time per PWM step |

Full cycle duration: 128 steps × 35 ms ≈ **4.5 seconds** per rainbow loop.

**Enable:** `POST /led/on`
**Status feature field:** `"rainbow"`

### Siren

Alternating red and blue flashes.

| Parameter | Value | Description |
|-----------|-------|-------------|
| `SIREN_HALF_MS` | 350 ms | Duration of each colour phase |

Full cycle: 700 ms (≈ 1.4 Hz).

**Enable:** `POST /led/siren/on`
**Status feature field:** `"siren"`

---

## How to add a new pattern

This is a checklist for adding a new pattern to the **Onboard LED** module. Adding a completely new module (e.g. external LEDs on GPIO pins) follows the same steps plus adding a new card section in `sendMain()`.

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

Also clear the new flag in the `/led/off` branch:
```cpp
ledPulseOn = false;
```

And in the `/led/on` and `/led/siren/on` branches:
```cpp
ledPulseOn = false;
```

### 4. Add the animation branch in `ledTask()`

Add before the `rainbow` branch so it takes priority:

```cpp
} else if (ledPulseOn) {
    prevSirenOn = false;
    // ... animation code using only digitalWrite / pwmCycle / setRGBFor
    delay(5);
```

The LED thread owns all pin writes. Do not call `handleClient()` or any WiFi function here.

### 5. Add a pattern row in `sendMain()` HTML

In the Onboard LED card:

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
else if(f==='pulse'){h.textContent='Onboard LED - Pulse ON';h.style.color='#fa0';}
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

## Software PWM reference

The onboard LED pins do not support hardware PWM from Mbed OS (and `analogWrite()` crashes). All brightness control uses software PWM:

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
