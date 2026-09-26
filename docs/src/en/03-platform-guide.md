## 3. Platform Guide

### Windows Desktop (SPA)
The primary design and control interface. Full-featured 7-tab SPA with 2D/3D layout, timeline editor, spatial effects, DMX profiles, and firmware management.

**Launch:** `powershell -File desktop\windows\run.ps1` or run `SlyLED.exe`
**Install:** Run `SlyLED-Setup.exe` (includes system tray icon)

### Linux (headless controller)
The same orchestrator as a background service on a rack or bench machine with no display — a Raspberry Pi 4/5, a NUC, or any Ubuntu 22.04+ / Debian Bookworm+ host (x86_64 or aarch64). Operate it from a browser on another machine or from the Android app.

**Install:** every release carries `SlyLED-<version>-linux.tar.gz` (one file for x86_64 and aarch64). Either `curl -fsSL https://raw.githubusercontent.com/SlyWombat/SlyLED/main/desktop/linux/install.sh | sudo bash -s -- --release latest` (downloads it and checks its SHA-256), or download it yourself, `tar xzf SlyLED-<version>-linux.tar.gz` and `sudo bash SlyLED-<version>/desktop/linux/install.sh`. It installs the code to `/opt/slyled` with its own Python environment, creates the `slyled` service account (member of `dialout`, so the Firmware tab can flash USB boards), and starts the `slyled` service on port 8080. Then open `http://<host>:8080`. No git checkout needed.
**Data:** projects, settings and logs are kept in `/var/lib/slyled/SlyLED/data`; downloaded firmware in `/var/lib/slyled/SlyLED/firmware`.
**Logs:** `journalctl -u slyled -f`
**Upgrade:** run a newer tarball's installer (or `--release latest` again) — it says `upgrading vX → vY` and keeps your data. `bash install.sh --version` shows what is installed.
**Remove:** `sudo bash desktop/linux/install.sh --uninstall` (keeps data; add `--purge` to delete it and the service account).
**Firewall:** when ufw or firewalld is active the installer opens TCP 8080 and UDP 4210, 4211, 5568 and 6454.
**Network:** discovery, Art-Net and the camera scan use every physical network interface; Docker/VM bridges and VPN tunnels are skipped.

### Docker (Linux hosts)
The orchestrator is also an image, `ghcr.io/slywombat/slyled:<version>` (x86_64 and arm64): `docker run -d --name slyled --network host --restart unless-stopped -e TZ=America/Toronto -v slyled-data:/var/lib/slyled ghcr.io/slywombat/slyled:<version>`, or `docker compose up -d` with the `docker-compose.yml` attached to the release.

**Host networking is required.** Discovery, the HinksPix sweep, Art-Net and sACN multicast need to reach your LAN, which a Docker bridge network can't. That also makes the image **Linux-only**: Docker Desktop on Windows or macOS runs containers in a VM. On Windows use `SlyLED-Setup.exe`.
**Data:** the `slyled-data` volume holds projects, settings and firmware (same layout as the service).
**Firewall:** open TCP 8080 and UDP 4210, 4211, 5568 and 6454 on the host.
**Don't run it next to the `slyled` service** on the same port — the second one stops with "already answering on port 8080".

### AI Runtime — local or remote Ollama
Camera auto-tune works without AI (the default CV **analyzer**). The optional **AI** evaluator sends camera frames to a vision model served by [Ollama](https://ollama.com). **Settings → Advanced → AI Runtime → Runs on** chooses where Ollama runs:

- **This machine** — SlyLED can install Ollama for you (**Install**) and starts/stops the `ollama serve` it launched.
- **A remote Ollama** — another computer on your LAN (e.g. a Mac mini or a GPU box). Enter its URL (`http://192.168.10.67:11434`), **Test connection** (shows the Ollama version, its vision models and the round-trip time), **Save**, then pick a vision model. SlyLED is **hands-off** with a remote: it never installs, starts, stops or warms up Ollama there, and **Install** is hidden. Pulling a model onto the remote is off by default; tick *Allow pulling models onto the remote* and SlyLED still asks each time, naming the host and the download size. If the remote stops answering, the card says **Remote unreachable** and auto-tune uses the CV analyzer until it's back — nothing is installed locally.

**Security:** Ollama has no login. The remote must listen on the LAN (`OLLAMA_HOST=0.0.0.0` in its environment) and camera frames travel over the network to it — use a LAN-only host, never one exposed to the internet.
**Headless (Linux service, Docker):** the Settings page works the same. To pin it in the deployment instead, set `SLYLED_OLLAMA_URL` (and optionally `SLYLED_OLLAMA_MODEL`): `sudo systemctl edit slyled` → `[Service]` / `Environment=SLYLED_OLLAMA_URL=http://192.168.10.67:11434`, or `environment: SLYLED_OLLAMA_URL: http://192.168.10.67:11434` in `docker-compose.yml`. The environment wins over Settings and the card shows *set by environment*. A non-loopback URL means remote; `SLYLED_OLLAMA_MODE=local|remote` forces the mode.

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

