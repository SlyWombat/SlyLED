---
title: SlyLED — Fact Sheet
format: one-pager
---

# SlyLED — Fact Sheet

**One line.** Open, three-tier stage-lighting control with local-first
AI calibration.

## At a glance

| | |
|---|---|
| **Project** | SlyLED |
| **Status** | Public beta — v1.6.3 (April 2026) |
| **Licence** | MIT |
| **Repository** | [github.com/SlyWombat/SlyLED](https://github.com/SlyWombat/SlyLED) |
| **Documentation** | [electricrv.ca/slyled](https://electricrv.ca/slyled) |
| **Platforms** | Windows installer, macOS bundle, Android APK, open firmware |
| **Target users** | Theatres, event productions, houses of worship, creative-tech spaces |
| **Comparable tools** | grandMA3 onPC, Chamsys MagicQ, Follow-Me 3D, Zactrack, QLC+ |
| **Hardware floor** | ~$250 (one moving head + one USB webcam + a PC) |

## What's in the box

- **Orchestrator** — Flask server + 7-tab SPA (Design, Timeline,
  Calibration, Camera, Show, Firmware, Settings) on Windows/macOS.
- **Performer firmware** — ESP32, D1 Mini, and Arduino Giga R1 WiFi
  (in both parent and child roles).
- **Camera firmware** — Orange Pi / Raspberry Pi + USB webcam, running
  YOLOv8n for tracking and Depth-Anything-V2 for 3D.
- **Bridge firmware** — Arduino Giga R1 + RS‑485 transceiver for
  DMX/Art-Net output.
- **Android app** — Kotlin/Compose operator client with gyroscope
  pointer and live show control.

## Why it's novel

- **Camera-auto-calibrated moving heads.** A $30 USB webcam replaces
  $15k–$60k beacon/wand/trackball systems. The four-tier calibration
  ladder (discovery → battleship grid → blink-confirm → ArUco verify)
  ships as a single "Calibrate" button.
- **Local-first vision AI.** Camera auto-tune, person tracking, and
  depth estimation all run on the operator's machine. No cloud. No
  API keys. No telemetry.
- **One engine for every remote.** Gyro puck, Android phone, and
  on-screen sliders drive moving heads through the same control path.
- **Dynamic shows that adapt to the rig.** Fourteen themes re-lay a
  full performance at runtime against the current fixture layout.
- **End-to-end open.** The entire click-to-DMX-byte path is MIT-
  licensed code in one repository. Operators, educators, and venues
  can audit it.

## Milestones

- **v1.0** — April 2026. Public beta.
- **v1.5** — Unified mover control; color wheel; auto-calibration.
- **v1.6** — Camera auto-tune; AI runtime panel; Astro doc site.
- **Roadmap** — local VLM show review; community profile server;
  sACN E1.31; PLASA 2026 submission.

## Contact

Press, partnerships, and integration questions: dave@drscapital.com
