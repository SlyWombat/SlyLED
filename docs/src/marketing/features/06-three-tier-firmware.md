---
slug: three-tier-firmware
title: Three-tier firmware on consumer hardware
icon: chip
order: 6
---

# Pro-console integration, neighbourhood-electronics-shop bill of materials

SlyLED runs on hardware you can source in an afternoon:

| Role | Board | Street price | Job |
|------|-------|--------------|-----|
| Orchestrator + DMX bridge | Arduino Giga R1 WiFi | ~$80 | Talks to the PC over UDP; drives the RS‑485 UART for DMX/Art‑Net out. |
| LED performer | ESP32-WROOM-32E dev board | ~$10 | Multi-string WS2812B output via FastLED. Up to 8 strings per node. |
| Budget performer | Wemos D1 Mini | ~$4 | 2-string output for scenic props and low-count bar tops. |
| Camera node | Orange Pi 4A / Raspberry Pi | ~$40 | USB webcam capture, YOLO tracking, depth, ArUco solving. |

Every tier uses the same UDP binary protocol v4 (8-byte header plus
payload) and the same JSON fixture schema. A D1 Mini performer
serialises its configuration to the parent over the same PONG packet
the Pi camera node does; the orchestrator doesn't distinguish them.

## One sketch, three board targets

`main/main.ino` compiles for all three LED-performer tiers via
`#ifdef BOARD_D1MINI`, `BOARD_ESP32`, `BOARD_GIGA_CHILD`. Per-board
limits live in `BoardConfig.h`; everything above that layer is shared
code. A board upgrade doesn't require relearning a new firmware.

## OTA and registry

`firmware/registry.json` tracks the latest binary per board. The
orchestrator polls firmware versions from each node over UDP and flags
stale nodes in the Firmware tab. New binaries flash via Art-Net-over-
USB or over-the-air — no cabled bootloader shuffle per performer.

## Camera firmware in Python

Camera nodes run a Flask server (`firmware/orangepi/camera_server.py`)
that answers the same UDP PING the Arduino nodes do. The SSH-based
deploy path in the Firmware tab pushes `camera_server.py`,
`detector.py`, `depth_estimator.py`, and the ONNX models in one
operation. No cross-compilation, no Zadig dance.
