---
slug: dynamic-shows
title: Dynamic shows that adapt to your rig
icon: sparkles
order: 4
---

# Pre-baked cues can't see your stage. SlyLED can.

Traditional show files are written once against a specific rig. Swap a
par for a mover, move a camera, or run the same show in a smaller
venue, and half the cues point at lights that no longer exist.

SlyLED's show generator reads the current fixture layout and lays down
a full performance at runtime. Fourteen themes — arena rock, ambient,
theatre, worship, club, kids' show, and more — adapt their track
priority, movement envelopes, and colour palettes to the actual
hardware in the room. A show built for three movers, two washes, and
a camera doesn't silently regress to "everything on" when you plug in
a fourth mover. It uses the new fixture.

## How the tracks compose

- **No-overlap guarantee** within a track. The bake engine treats
  higher-numbered tracks as overrides for the same fixture, so you can
  layer a "follow the singer" track on top of a "wash the band" track
  without worrying which one wins.
- **Track priority is explicit** — higher track numbers override lower
  tracks on the same fixture. The 3D runtime renders the resolved
  output, so designers see exactly which clip is driving which head.
- **Track-action effects** live in the track itself, not per-fixture
  code. Person-tracking, patrol sweeps, and spatial effects are just
  another track type — bake-free, runtime-generated.

## Baked, previewed, saved

The same dynamic show can be baked into a deterministic clip for an
export or a follow-me replay. Nothing about the generator is tied to
runtime; the baked output is identical to what was generated live.
Designers iterate on the fixture layout, not the show.
