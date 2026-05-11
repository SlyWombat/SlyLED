## Fixture Group Control

The **Group Control** modal exposes live sliders for every fixture
group on the rig. Move a slider, every fixture in that group responds
instantly — no bake, no timeline.

### What's a group?

A **fixture group** is a fixture record with `type=group`, holding a
list of member `childIds`. Create one from **Setup → + Add Fixture →
Fixture Group**. Groups can mix LED + DMX members; the engine
forwards the relevant subset of channels to each member.

### Per-group controls

Each card shows:

- **Dimmer** (0–255) — master scalar for every member. Multiplies into
  the member's own dimmer / RGB values; doesn't replace them.
- **R / G / B** (0–255 each) — global colour. Setting any of them
  forces dimmer to 255 (so the colour is visible without a separate
  fader nudge). Drag all three to 0 to blackout the group.
- **Warm / Cool / Red / Off** — quick preset buttons. Useful for
  pre-show ambient looks before the actual timeline runs.

### When to use Group Control vs an Action

- Use **Group Control** for live, ad-hoc adjustments — pre-show ambient,
  rehearsal cues, troubleshooting "is this fixture wired right?".
- Use a **saved Action + timeline** for repeatable show content. Group
  Control's sliders aren't recorded anywhere; closing the modal
  leaves the last value on the wire but the next bake overwrites it.

### Pitfalls

- An empty group (no `childIds`) shows the card but every slider is a
  no-op. Add members first.
- The colour preset buttons set RGB but **leave dimmer at 255**. If
  your group is in the middle of a fade and you tap Red, you may
  override the fade's dimmer ramp. Tap Off to restore zero output.

**More info →** chapter 4, *Fixture Setup* — "Groups".
