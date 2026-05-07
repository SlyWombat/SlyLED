## 13. Preset Shows

Preset shows are a one-click way to put any rig into a polished
look — useful for soundcheck, pre-show ambience, or a quick reset
between cues. Open them from **Runtime → Load Show → Presets**.

The show generator inspects the rig at install time (LED strips,
moving heads, camera nodes) and adapts each theme to what's actually
on the stage. Themes that include moving-head choreography
auto-classify any DMX fixture with `panRange > 0` and `tiltRange > 0`
as a candidate, then emit either coordinated sweeps (stage-coordinate
motion via `ptStartPos` / `ptEndPos`) or live target-tracking via a
Track action.

### Available themes

| Preset | Description | Movers do |
| --- | --- | --- |
| **Rainbow Up** | Rainbow plane rising floor to ceiling | Stage-coord sweep up the Z axis (#837) |
| **Rainbow Across** | Rainbow sphere sweeping stage-left to stage-right | Stage-coord sweep along X |
| **Slow Fire** | Warm fire effect on every fixture | Static beam, dimmer flicker |
| **Disco** | Pastel twinkle sparkles | Mid-stage stage-coord chase |
| **Ocean Wave** | Blue wave sweep with teal wash | Slow front-to-back sweep |
| **Sunset Glow** | Warm breathe with golden sweep | Static aim, breathing dimmer |
| **Police Lights** | Red strobe with blue flash sweep | Strobe + slow side-to-side |
| **Starfield** | White sparkles on dark background | Static beam, dimmer twinkle |
| **Aurora Borealis** | Green curtain with purple shimmer | Slow front-to-back sweep |
| **Aurora Curtain** *(new in v1.7.83)* | Coordinated travelling curtain — every mover rides the same ribbon target at a phase offset, so the curtain visibly travels the rig instead of every head moving in unison. Includes a sparkle layer and fade-in / fade-out brackets so the look opens and closes cleanly. | Track a ribbon target, ping-pong on the chosen axis |
| **Spotlight Follow Person** | Warm orb that camera-tracked people inherit | Track action — heads chase detected person |
| **Concert Wash** | Magenta flood + amber sweep | Stage-coord sweep, no tracking |
| **Figure Eight** | Crossing orbs — heads trace X paths | Track action — heads chase crossing patrol props |
| **Thunderstorm** | Lightning strikes from the rig top-down | Strobe-style stage-coord bolts (Z-down, #837) |
| **Dance Floor** | Fast orbiting spots | Stage-coord chase, no tracking |

### Track-action vs sweep

The description column tells you whether the heads chase a target
("Track action") or follow a pre-baked sweep path ("stage-coord
sweep"). Pre-v1.7.83, several themes promised tracking in their
description but emitted only a sweep — fixed in #837 so the table is
honest now: only **Spotlight Follow Person**, **Figure Eight**, and
**Aurora Curtain** create a Track action when installed.

### Customising an installed preset

Installing a preset writes it into the regular Actions / Timeline /
Objects state, so every part of it is editable in place:

- **Recolour a sweep** — open the action under **Actions**, change RGB.
  The bake re-renders next time the timeline plays; no need to
  re-install the preset.
- **Scope a Track action to specific fixtures** — open the Track
  action's Advanced expander (chapter 8), set
  `trackFixtureIds` to the heads you want, leave it empty to scope to
  every mover.
- **Adjust the tracker cycle** — Cycle Time (ms) in the Advanced
  expander controls how often a head jumps to a new target when there
  are more targets than heads.
- **Replace the patrol target** — for Aurora Curtain or Figure Eight,
  the underlying patrol object is on the **Layout / Objects** tab.
  Change its axis, speedPreset, or pattern; the next playback frame
  picks up the new motion.

### Behaviour outside playback

Preset shows respect the same idle-behaviour contract as any other
timeline (chapter 17 → "Idle behaviour"): the rig parks at home and
the lamp closes when the show ends, claim is released, or the
operator stops playback. The Aurora Curtain ribbon's patrol object
auto-stops at timeline end since it's tagged `patrolMode: on-demand`
when the preset installs it (chapter 6).

---

