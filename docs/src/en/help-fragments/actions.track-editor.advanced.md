## Track Action — Advanced

The **Advanced** expander on a Track action exposes the timing and
distribution knobs the simple flow keeps hidden. Most operators never
need to touch these — sensible defaults work for the standard
"follow person X" case.

### Cycle Time (ms)

How often the head re-aims at its assigned target.

- **Lower** (500–1000 ms) — snappier, edgier tracking. Good for
  fast-moving subjects or aggressive lighting moods.
- **Default** (2000 ms) — smooth on humans walking; doesn't pump on
  jitter from the camera detector.
- **Higher** (5000+ ms) — laggy but very smooth. Useful for slow,
  ambient looks where the audience shouldn't see the head re-tasking.

### Offset X / Y / Z (mm)

Aim point relative to the target's centroid, in stage millimetres.

- Use `Z = +800` to aim at the **head** of a person whose centroid
  sits at hip height (`Z ≈ 1000` for a standing adult — adjust to
  your camera's depth scale).
- Use `Y = +500` to lead a target moving toward `+Y` (the head aims
  ahead of where they are, which looks natural in a follow shot).
- All offsets are in the **stage** frame, not the fixture frame — so
  `+X` is stage-left for every fixture.

### Auto-spread across targets

When ticked, multiple movers on the same action distribute themselves
across all detected targets (one head per person, cycled through).
Off = every mover aims at the same primary target.

### Fixed assignment (1:1 — extra targets ignored)

When ticked, each mover sticks to its initially-assigned target index
and refuses to re-task. If a target leaves frame, the mover holds its
last aim instead of jumping to a different person.

> Mutually exclusive with **Auto-spread** — ticking Fixed turns
> Auto-spread off (and vice versa). The UI doesn't enforce this; the
> bake engine treats Fixed as a stricter override.

**More info →** chapter 8, *Track Actions*.
