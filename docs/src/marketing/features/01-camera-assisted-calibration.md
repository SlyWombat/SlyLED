---
slug: camera-assisted-calibration
title: Camera-assisted moving-head calibration
icon: target
order: 1
---

# No beacons. No pucks. No wands.

Every pro-tier tracking system that came before SlyLED needed dedicated
sensing hardware — IR wands from BlackTrax, UWB beacons from Zactrack,
trackballs from Follow-Me. Each adds $15 k–$60 k to the rig just to
answer the question "where is that moving head actually pointing?"

SlyLED answers the same question with a $30 USB webcam.

## How it works

1. Position the camera so it can see the stage floor.
2. Click **Calibrate** on any moving head.
3. The orchestrator flashes the beam, scans a coarse battleship grid,
   confirms hits with a blink test, refines to sub-grid accuracy, and
   runs a held-out verification pass against ArUco markers surveyed in
   stage coordinates.
4. The fit produces a `ParametricFixtureModel` the show engine uses
   thereafter. 100 mm accuracy at 3 m throw, 95th percentile.

The review series behind this feature (#610, #651–#661) ran the pipeline
phase-by-phase, benchmarked against grandMA3 and Zactrack's calibration
flows, and wrote an 800-line reliability spec with code citations. All
of it lives in the repo — see
[`docs/mover-calibration-reliability-review.md`](https://github.com/SlyWombat/SlyLED/blob/main/docs/mover-calibration-reliability-review.md).

## Four-tier fallback

No single auto-method is reliable enough alone. SlyLED ships four
tiers; the operator is never stuck:

1. **Camera-assisted auto** (this feature) — flash detection, battleship
   discovery, blink-confirm, sign verification, 5-point held-out test.
2. **Camera-assisted, operator-in-loop** — click the beam on a live frame
   when auto-detect fails.
3. **3-point manual aim** — the grandMA3 / Follow-Me pattern, using
   phone gyroscope, slider, or trackball to aim.
4. **GDTF / geometric-only trust** — compute aim from fixture pose +
   DMX profile alone. Advisory banner; never inverted.

Tier 1 converges on 80 % of rigs. Tiers 2–4 are first-class citizens,
not emergency workarounds.
