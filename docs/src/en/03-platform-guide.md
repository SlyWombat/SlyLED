## 3. Platform Guide

### Windows Desktop (SPA)
The primary design and control interface. Full-featured 7-tab SPA with 2D/3D layout, timeline editor, spatial effects, DMX profiles, and firmware management.

**Launch:** `powershell -File desktop\windows\run.ps1` or run `SlyLED.exe`
**Install:** Run `SlyLED-Setup.exe` (includes system tray icon)

### Android App
Live operator tool for running shows from your phone. Connects to the desktop server over WiFi. As of v1.8.1 the Control tab is rebuilt as a **Command Surface** — see #888 / `docs/design/mobile_ui_redesign.md`.

**Install:** Sideload `slyled-android.apk` to your phone and install.
**Connect:** Scan the QR code on the desktop Settings tab, or enter the server IP and port manually.

![Android Connect screen](screenshots/android/android-connection.png)

**Bottom nav (3 tabs):** Stage / Control / Status. Settings lives at the top-right ⚙ gear, not in the bottom nav.

**Top bar gestures:**
- **Long-press the SlyLED logo** → instant blackout (master = 0). Heavy double-haptic. The only "nuclear" gesture; other safety actions live as per-page buttons.
- **Connection pill** — green dot = Connected; orange slow pulse = Reconnecting (Degraded); red fast pulse = Offline. Tap to retry.
- **⚙ Settings gear** — server name, stage dimensions, Auto Brightness calibration, config export/import, disconnect.

**Stage tab** — live viewport showing all fixtures with beam cones, tracked object markers, grid floor. Pinch-to-zoom + drag-to-pan.

![Android Stage view](screenshots/android/android-stage-idle.png)

**Control tab (rebuilt for v1.8.1):** persistent Now Playing anchor over a 4-page pager.

- **Master** *(default page)* — global brightness slider with ±5% steppers + bloom-on-drag. Auto Brightness toggle (moved from Settings) + source picker (Mic / Playback / USB) + live envelope meter.

  ![Control · Master](screenshots/android/android-control-master.png)

- **Grab** — moving-head tiles showing current colour + pan/tilt direction arrow. Favourites row at top (star to add). Tap a tile → Controller Mode (gyro-driven pan/tilt at 20 Hz). Top-right "Send all home" button homes every mover.

  ![Control · Grab](screenshots/android/android-control-grab.png)

- **Fixtures** — non-mover DMX fixtures (bubble machines, hazers, washes, pars, strobes) with profile-driven shortcuts: 🫧 bubbles, 💨 haze low/med/high, 🌀 fan slow/med/fast, 💡 colour swatches, 🟣 UV, ⚡ momentary strobe, 🧼 hold-to-clean. "More controls →" opens a per-channel sheet with capability sliders. Top-right "Stop all effects" kills strobes + bubble/haze in parallel.

  ![Control · Fixtures](screenshots/android/android-control-fixtures.png)

- **Shows** — starred → recent → all sections, ranked by last-played time. One-tap launch. Long-press to star.

  ![Control · Shows](screenshots/android/android-control-shows.png)

**Now Playing anchor** sits above the pager — name, loop chip, elapsed / total, progress bar, STOP and Next.

**Status tab** — device monitoring (performers online/offline, RSSI, firmware), camera nodes with Track button to start/stop person tracking, and Art-Net/DMX engine status.

![Android Status](screenshots/android/android-status.png)

**Settings sheet** (top-right ⚙) — System name, units, stage dimensions (W × H × D), dark mode, logging, plus the Auto Brightness configuration block (enable, model mode, sensitivity/floor/ceiling/attack/release sliders).

![Android Settings](screenshots/android/android-settings.png)

**Controller Mode (Grab → tap a mover):** Hold the phone and point where you want the beam — pan/tilt follows your phone orientation at 20 Hz. Tap Recenter to calibrate, X to exit. Press-Start / press-Stop guarded by nonce+ACK (#825). The first time you use it on a new phone, the aim-axis wizard (#869) measures your phone's body-frame axes; takes ~10 seconds.

### Firmware Config (ESP32/D1 Mini)
Each performer serves a 3-tab config page at `http://<device-ip>/config`:
- **Dashboard** — hostname, firmware version, active action status
- **Settings** — device name, description, string count
- **Config** — per-string LED count, length, direction, GPIO pin (ESP32)

---

