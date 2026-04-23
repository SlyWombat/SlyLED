# SlyLED Code Review
**Model:** gemini-robotics-er-1.6-preview  
**Date:** 2026-04-15 10:27  
**Files reviewed:** 10

---

This is a professional code review of the **SlyLED Gyro Integration** subsystem.

---

### 1. main/Protocol.h

**Bugs**
- **None identified.** The use of `__attribute__((packed))` correctly ensures wire compatibility across different architectures (ESP32 vs. x86/x64).

**Security issues**
- **None identified.** Constants are well-defined.

**Robustness**
- **String Termination:** `HOSTNAME_LEN` is 10. If a hostname is exactly 10 characters, `strncpy(..., sizeof(pp.hostname) - 1)` in `GyroUdp.cpp` will truncate it to 9 chars to ensure null termination. This is safe, though it limits the usable length.

**Performance**
- **None identified.**

**Code quality**
- **Readability:** Excellent documentation of the `ActionPayload` parameter mapping.
- **Structure:** Good use of `constexpr` over `#define`.

---

### 2. main/GyroUdp.cpp (and .h)

**Bugs**
- **Broadcast Default:** `s_parentIP` defaults to `255.255.255.255`. If the board starts streaming before receiving a `CMD_GYRO_CTRL`, it will spam the entire network with high-frequency UDP traffic (up to 50Hz).
- **Integer Overflow/Underflow:** In `gyroUdpUpdate`, `intervalMs = (s_targetFps > 0) ? (1000u / s_targetFps) : 50u;`. If `s_targetFps` is set to a very high value (though limited to 50 in `HandleCmd`), this is safe. However, `now - s_lastSendMs` logic is susceptible to `millis()` rollover every 49 days (standard Arduino issue), though usually negligible for short-lived sessions.

**Security issues**
- **OTA Buffer Overflow:** In `CMD_OTA_UPDATE` handler:
  ```cpp
  char otaUrl[256];
  uint16_t copyLen = urlLen < 255u ? urlLen : 255u;
  memcpy(otaUrl, &payload[5], copyLen);
  otaUrl[copyLen] = '\0';
  ```
  While `copyLen` is capped at 255, if `urlLen` is 255, `otaUrl[255]` is the 256th index (valid). If `urlLen` was larger, it stays safe. However, `shaOff = 5 + urlLen`. If `urlLen` is `0xFFFF`, `shaOff` wraps around or points deep into invalid memory. Although `shaOff + 64 <= plen` is checked, an extremely large `urlLen` could cause `shaOff + 64` to overflow `uint16_t` and pass the check.

**Robustness**
- **Unicast Target:** `s_parentIP` is only updated on `CMD_GYRO_CTRL`. If the parent server restarts and changes IP, the gyro will continue sending data to the old IP until it receives a new control packet.

**Performance**
- **Redundant `memcpy`:** In `gyroUdpUpdate`, the code creates a `buf` and `memcpy`s the header and payload. `WiFiUDP::write` can take multiple buffers or you can write directly from the structs to avoid the extra stack copy.

---

### 3. main/GyroUI.cpp (and .h)

**Bugs**
- **Negative Angle Display:** In `drawRPY()`:
  ```cpp
  d = (int)s_roll;
  f = (int)((s_roll - (float)d) * 10.0f);
  if (f < 0) f = -f;
  snprintf(buf, sizeof(buf), "R: %4d.%1d%c", d, f, '\xb0');
  ```
  If `s_roll` is `-0.5`, `d` becomes `0`. The output will be `R:    0.5`. The negative sign is lost because `0` cannot carry a sign in an integer.
- **Blocking UI:** `handleTouch` contains `delay(180)` for the "ZERO" visual confirmation. Since `gyroUdpUpdate()` is called from the same main loop, orientation packets will stop being sent for nearly 200ms whenever the user recalibrates.

**Robustness**
- **Touch Debounce:** `DEBOUNCE_MS = 350` is quite high. It makes the UI feel sluggish/unresponsive if the user tries to toggle modes quickly.

**Performance**
- **Expensive Redraws:** `fullRedraw()` clears the screen and redraws everything. It is called on every state change. While acceptable on ESP32-S3, incremental updates for the START/STOP button would be smoother.

---

### 4. desktop/shared/gyro_engine.py

**Bugs**
- **Hardcoded Channel Offsets:** 
  ```python
  pan_ch  = start
  tilt_ch = start + 2
  ```
  This assumes a specific 16-bit or multi-channel fixture layout where Tilt is exactly 2 channels away from Pan. If using a fixture where Tilt is `start + 1`, this will overwrite the Pan-Fine channel or a different attribute.
- **Thread Safety:** `_tick` iterates over `list(self._fixtures)`. While this prevents "dictionary changed size during iteration" errors, the fixture objects themselves are dictionaries that might be modified by the Flask API thread simultaneously, potentially leading to `KeyError` if a key is deleted mid-tick.

**Security issues**
- **None identified.**

**Performance**
- **Double UDP Send:** It sends to both ArtNet and sACN engines every tick. If the user has both enabled, it doubles network traffic. If only one is used, the other `try...except` block is unnecessary overhead.
- **Math in Loop:** `float(f.get("panCenter", 128))` is called every 40ms (25Hz) for every fixture. These should be cached or pre-converted when `update_assignment` is called.

**Code quality**
- **Exception Handling:** `except Exception: pass` is used aggressively. This hides critical errors (e.g., network interface down, type errors in config) that would make debugging difficult.

---

### 5. Summary and Prioritised Recommendations

The system is well-architected for a hobbyist/prosumer lighting system. The protocol is compact, and the separation of concerns between the UI, UDP handling, and the Python DMX engine is clear.

#### **Top Issues to Fix**

| ID | Priority | Category | File | Description |
|:---|:---|:---|:---|:---|
| **1** | **P1** | **Bug** | `GyroUI.cpp` | **Sign loss on small negative angles:** Angles between `-0.9` and `0.0` display as positive. Fix by checking `if (s_roll < 0 && d == 0)` and prepending `-`. |
| **2** | **P1** | **Robustness** | `GyroUdp.cpp` | **Broadcast Spam:** Initialize `s_parentIP` to `0.0.0.0` and only stream if `!s_parentIP.isSet()`. Avoid default broadcast streaming. |
| **3** | **P2** | **Performance** | `GyroUI.cpp` | **Blocking Delay:** Replace `delay(180)` in `handleTouch` with a non-blocking timer logic to prevent UDP packet drops during UI interaction. |
| **4** | **P2** | **Robustness** | `gyro_engine.py` | **Fixture Mapping:** Move `pan_ch` and `tilt_ch` offsets to fixture configuration. Hardcoding `+2` breaks compatibility with many common movers. |
| **5** | **P2** | **Security** | `GyroUdp.cpp` | **OTA Integer Overflow:** Tighten `shaOff` calculation. Ensure `urlLen` is validated against `plen` before any pointer arithmetic. |
| **6** | **P3** | **Performance** | `gyro_engine.py` | **Attribute Caching:** Cache `panCenter`, `panScale`, etc., in `update_assignment` to avoid repeated dictionary lookups and float conversions in the high-frequency `_tick` loop. |

**Overall Grade: B+**
The code is functional and follows good embedded patterns, but needs minor refinements in UI responsiveness and display logic to be considered "production-ready."