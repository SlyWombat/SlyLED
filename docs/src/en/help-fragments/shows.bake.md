## Bake panel

**Baking** compiles a timeline of actions into per-fixture step
buffers and pushes them to every performer. Until the show is baked,
the runner has nothing to play.

### What baking does

1. Walks the timeline beat by beat, evaluating which clips are live on
   each fixture.
2. Resolves each clip's action through the spatial / spectrum /
   beam-mode engine, producing per-step output (RGB for LED, 0–255
   DMX channels for DMX fixtures).
3. Packs each fixture's steps into the wire-protocol shape — for ESP32
   children, the 8-byte header + 48-byte LOAD_STEP frames; for
   DMX, the Art-Net per-tick channel buffers.
4. Streams LOAD_STEP packets to each performer, ACKing every frame.

### The Sync step

After bake completes, the bake panel shows a green **Sync** badge for
each performer that ACK'd all its load-steps. A red badge with a step
index means that performer dropped a frame (usually a WiFi blip);
re-bake to recover.

### Start

Once every performer is green, **Start** issues a `RUNNER_GO` with
the agreed start epoch. Every performer begins playing the loaded
buffer in lockstep — the orchestrator doesn't drive frame timing,
only the global clock.

### Common pitfalls

- A new fixture added since the last bake **must be re-baked** before
  Start, or it sits dark.
- The `track` action (#812) bypasses the bake requirement — it runs
  live from the orchestrator's main loop, so editing a track action
  doesn't force a re-bake. All other action types do.
- Bake duration scales with timeline length × fixture count. A
  10-minute timeline on 24 fixtures is ~5 s on a modern CPU; older
  rigs may take longer.

**More info →** chapter 10, *Baking & Playback*.
