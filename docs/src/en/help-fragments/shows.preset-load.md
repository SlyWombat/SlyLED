## Load Show — Presets

The **Presets** dialog generates a full show on demand from a high-level
theme. The generator inspects your current rig (fixtures, layout,
camera-tracked objects) and tailors track lengths, action types, and
fixture assignments so the output isn't generic.

### Choosing a preset

Each preset card shows a theme name and a short description. Click
**Load** to generate the show in place. The current timeline is
replaced — back up first if you want it.

Common presets:

- **Energetic** — fast strobes, vivid spectrum sweeps; multiple
  tracks layered so the bake stays busy.
- **Ambient** — slow colour fades, gentle pan/tilt arcs; one or two
  long tracks rather than many short ones.
- **Vertical Bar Array** — special template for stage rigs with a
  vertical column of LED bars; designed to make use of vertical
  position metadata on the fixtures.
- **Sequenced catalog** / **Ribbon** / **Live-track** — theme-branch
  templates that hook directly into the generator instead of going
  through the standard `_generate_spatial_effects` path. See
  `feedback_show_template_branch_pattern` for the structural rule.

### What the generator inspects

- Fixture count and types (movers vs LED bars vs par cans).
- Layout — fixtures at the edges get different treatment than centre
  ones (e.g. spotlights vs wash).
- Camera-tracked **moving** objects — if any exist, the generator
  inserts a Track action so the cued movers follow them.
- Stage dimensions — clip durations scale with stage depth so big
  rigs get longer sweeps.

### After loading

The generated timeline lands in the editor exactly as if you'd
authored it by hand. Edit, re-bake, and start as usual. Bake is
**not** automatic — Load drops you into the timeline view for review,
not into a running show.

**More info →** chapter 13, *Preset Shows*.
