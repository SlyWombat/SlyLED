# Supported hardware

Canonical reference for which boards/OS combinations the SlyLED stack
runs on. Closes the documentation half of issue #569.

## Camera nodes

Camera nodes are **not Orange-Pi-specific**. Any Linux SBC that meets the
requirements below can run `firmware/orangepi/camera_server.py` (the
folder name is historical — issue #569 tracks the rename to
`firmware/camera-node/`). The Python code already filters SoC video
nodes for both Allwinner (Orange Pi: `sunxi`, `sun6i`, `cedrus`) and
Broadcom (Raspberry Pi: `bcm2835`, `bcm2835-isp`).

### Requirements

| Requirement | Detail |
|-------------|--------|
| **OS**      | Debian/Ubuntu family — uses `apt`, `systemd`, `/usr/bin/python3` |
| **Kernel**  | Any Linux kernel with V4L2 (`/dev/video*`, `v4l2-ctl`)         |
| **Python**  | Python 3 + Flask ≥ 3.0, `zeroconf` ≥ 0.80, `onnxruntime` ≥ 1.17 |
| **Cameras** | USB only via V4L2 — no CSI / ribbon camera support in v1.x      |
| **Init**    | systemd (`slyled-cam.service` targets `network-online.target`) |
| **OpenCV**  | `python3-opencv` and `python3-numpy` from `apt` (do **not** pip-install — slow ARM compile) |

### Compatibility matrix

| Board                | OS                                    | Status                       |
|----------------------|---------------------------------------|------------------------------|
| Orange Pi 4A         | Ubuntu 22.04 Jammy                    | ✅ Working (primary dev board) |
| Orange Pi Zero 3     | Ubuntu 22.04 Jammy                    | ✅ Working                    |
| Orange Pi 5          | Ubuntu 22.04 Jammy                    | ✅ Working                    |
| Orange Pi 4A         | Armbian Bookworm / Trixie             | ❌ Boot/DHCP failure          |
| Raspberry Pi 3B+     | Raspberry Pi OS (Bullseye/Bookworm)   | ⚠ Code-compatible — `/scan` needs fw ≥ 1.6.0 (#620) |
| Raspberry Pi 4       | Raspberry Pi OS (Bookworm)            | 🔲 Untested — code compatible |
| Raspberry Pi 5       | Raspberry Pi OS (Bookworm)            | 🔲 Untested — code compatible |

### Known camera-node firmware fixes

- **fw 1.6.0** (#620) — `/scan` falls back through fswebcam/ffmpeg/v4l2-ctl
  the same way `/snapshot` does, so RPi 3 V4L2-OpenCV failures still produce a
  detectable frame.

### Cameras themselves

Tested USB cameras: any UVC-class device that exposes MJPG at 1080p over
V4L2. Capture path priority:

1. OpenCV `VideoCapture(device, CAP_V4L2)` with MJPG fourcc.
2. `fswebcam` subprocess (returns JPEG → decoded back to BGR).
3. `ffmpeg -f v4l2` subprocess.
4. `v4l2-ctl --stream-to=-` subprocess.

`/snapshot` and `/scan` (#620) use the same chain.

## DMX bridge

| Board               | Notes |
|---------------------|-------|
| Arduino Giga R1 WiFi + CQRobot RS-485 | Primary supported bridge (`SLYC-1152`) — see `hardware_giga_dmx_artnet.md` for build flags + serial-break gotchas. |

## LED performers

| Board                          | Max strings | Storage             |
|--------------------------------|-------------|---------------------|
| Wemos D1 Mini (`BOARD_D1MINI`) | 2           | EEPROM (flash-backed) |
| ESP32 (`BOARD_ESP32`)          | 8           | NVS Preferences (`"slyled"` namespace) |
| Arduino Giga Child (`BOARD_GIGA_CHILD`) | 1   | NVS Preferences; 1 onboard RGB LED |

`MAX_STR_PER_CHILD = 8` is a **protocol constant** — all PONG/ACTION
structs are sized for 8 strings regardless of the board's storage limit.

## Gyro / phone controller

| Hardware                                | Notes |
|-----------------------------------------|-------|
| Waveshare ESP32-S3 1.28" round LCD      | Primary gyro puck — see `hardware_waveshare_gyro.md` for pin map and build flags. |
| Android phone (Compose app)             | Uses HTTP POST to `/api/mover-control/*` — same engine as the puck. |
