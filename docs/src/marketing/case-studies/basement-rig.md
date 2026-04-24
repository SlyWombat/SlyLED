---
title: "Case study — the basement rig"
slug: basement-rig
stage_size: 4000 × 3620 × 2060 mm
fixtures: 3 moving heads + 3 USB cameras + 6 ArUco markers
cost_total_usd: ~500
status: reference-implementation
---

# The basement rig

SlyLED's reference-implementation home: a single-bay basement in
Canada, 4000 mm × 3620 mm × 2060 mm, with three 350 W moving heads on
a truss, three USB cameras covering stage-left / stage-right / backwall,
and six ArUco tags surveyed on the floor.

Total hardware cost below USD 500. Runtime is a regular Windows 11
desktop; operator control is either a browser on the same desk or a
Pixel 9 over WiFi.

## Why it exists

Every feature described in the public SlyLED documentation runs
against this rig before it lands on `main`. The basement is the
proving ground for:

- **Camera-assisted mover calibration** (Appendix B). Every commit in
  the #610 / #651–#661 reliability series was live-tested here
  against real 350 W movers with real reflective flooring and real
  ambient light.
- **Stage-coordinate system validation** (#600, #586). Rotation
  schema v2 was shaken out against three camera orientations + three
  mover mounts.
- **Multi-camera point-cloud merge** (`space_mapper.py`). Three
  overlapping views stress the cross-camera consistency check that
  pro-tier installs would skip.
- **Local-first auto-tune** (#623). Three cameras at 4K + one RPi
  running Ollama's Moondream produced the reference numbers used in
  the feature's release notes.

## Specifications

| Component | Model | Role |
|---|---|---|
| Orchestrator | Windows 11 + i7 + GTX 1070 | Show design, baking, Ollama VLM |
| DMX bridge | Arduino Giga R1 WiFi + CQRobot MAX485 | Art-Net → DMX512 |
| Moving heads | 3× generic 350 W BeamLight 16ch | Test workload |
| Cameras | 1× EMEET SmartCam 4K (2), 1× USB Live Cam 1080p | Stage vision |
| Camera hosts | Orange Pi / Raspberry Pi | Flask camera firmware |
| Markers | 6× ArUco DICT_4X4_50, printed on rigid foam | Ground-truth reference |

## What live-test sessions proved

- **2026-04-21** — basement-rig evaluated with stage-mapped camera
  homographies. Three cameras submicron-level self-consistent (<2 px
  RMS); the pillar obstacle registered correctly against the point
  cloud.
- **2026-04-22** — markers-mode calibration landed as the default
  mover-cal path after end-to-end validation. Bracket-and-retry on
  beam loss (#625) + multi-snapshot ArUco aggregation (#626) closed
  the gaps that blocked auto-cal on glossy flooring.
- **2026-04-23** — Art-Net silent-death bug (#647) surfaced on a live
  Fn 2 attempt and was instrumented the same session via engine-
  health signals in `mover_control.py`.

Every session's artefacts (screenshots, logs, beam traces) are in
`docs/live-test-sessions/YYYY-MM-DD/` on the repo.

## Reproducibility

The basement rig is not a prerequisite. SlyLED is designed so the
minimum viable reproduction is:

- 1 Windows PC with Python + the installer
- 1 USB camera
- 1 Art-Net-capable DMX bridge (Giga, ENTTEC, etc.)
- 1 moving head

With those four pieces, any reader can replicate Appendix B's mover-
calibration path against their own fixture and confirm the
accuracy numbers cited in the PLASA submission.
