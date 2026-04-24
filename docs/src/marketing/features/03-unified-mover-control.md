---
slug: unified-mover-control
title: Unified mover control — one engine, three input devices
icon: puzzle
order: 3
---

# Gyro puck, pocket phone, on-screen slider — same engine

Operators don't pick one input device per show. A lighting designer
might aim a spot from a tablet during rehearsal, hand the rig to a
follow-spot operator with a gyro puck mid-run, and fall back to a
faders-and-sliders SPA when the puck battery dies. Most systems treat
each of those as a separate code path, which means three sets of bugs
and three flavours of "works a little differently."

SlyLED routes every remote through one `MoverControlEngine`
(`desktop/shared/mover_control.py`). Gyro packets over UDP, HTTP POSTs
from the Android app, and on-screen slider events from the SPA all
hit the same claim/release gate, the same calibrate-then-orient
pipeline, and the same profile-aware DMX resolver.

## What the engine handles

- **Claim/release.** One device owns a mover at a time. TTL auto-release
  (30 s) keeps a dropped remote from latching the head.
- **Hold-to-calibrate.** The operator holds the calibrate button; the
  engine captures the device's orientation and the mover's current
  pan/tilt as a reference pair. On release, device deltas become mover
  deltas — no coordinate math for the operator.
- **20 fps orientation streaming.** Normalised pan/tilt output resolved
  through the fixture profile, so the same gesture works across a 16‑bit
  BeamLight and an 8‑bit budget wash.
- **Auto colour resolution.** RGB values are mapped to the nearest
  colour-wheel slot on wheel-only fixtures. The operator picks a colour;
  the engine figures out how to make the beam that colour.

## Why it matters

Consolidating remotes behind one engine is the difference between "we
support an Android app" and "Android, gyro, and SPA are the same
feature." New inputs plug into the same API: a WebMIDI fader board, a
Stream Deck macro, a voice assistant demo — they all speak the six
verbs (`claim`, `release`, `start`, `calibrate-start`, `calibrate-end`,
`orient`) and the engine handles the rest.
