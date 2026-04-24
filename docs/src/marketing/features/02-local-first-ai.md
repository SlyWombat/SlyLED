---
slug: local-first-ai
title: Local-first vision AI
icon: shield
order: 2
---

# Your show, your machine, your data

SlyLED's camera auto-tune (#623) adjusts exposure, gain, and white
balance until every downstream task — beam detection, ArUco marker
solving, YOLO object tracking — works. It does that with a local
vision-language model, running entirely on the operator's own
hardware via Ollama.

No cloud. No API keys. No telemetry. No "we anonymise metadata for
telemetry purposes." The rehearsal footage from your theatre's security
camera stays inside your theatre.

## Pluggable backend

- **Heuristic** (default, always available) — histogram statistics.
  Penalises clipped highlights, shadow crush, off-target brightness.
  Works on any installation.
- **Local VLM** — Moondream by default, switchable to LLaVA, Qwen-VL,
  or BakLLaVA via a single env var. Needs 2 GB of disk space and
  whatever GPU or CPU you already have.

The installer component "Local AI camera auto-tune" drops a marker
file; on first launch the orchestrator downloads Ollama, pulls the
vision model, and shows progress in the Settings panel — same pattern
the ZoeDepth runtime uses (#598).

## Why this matters

Most lighting systems grew up in the console era, when "the data" was
a single channel map on a USB stick. Consumer vision AI changes the
problem: cameras now generate kilobytes per frame, feeding continuous
tracking and auto-calibration. Shipping those frames to a cloud API
for a marginal quality bump is a privacy regression we decided not to
make.

SlyLED's auto-tune is the first feature of a longer roadmap: local
VLM assistance for light-plot layout review, rehearsal scene
recognition, fault diagnosis. All of it on the operator's own box.
