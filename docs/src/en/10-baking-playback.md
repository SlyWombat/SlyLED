## 10. Baking & Playback

### Bake
Compiles a timeline into minimal action instructions per performer:
1. Click **Bake** → progress shows frame count and segments
2. Click **Sync** to push instructions to performers via UDP
3. Click **Start** for synchronized NTP-timed playback

### Output
- **Action segments**: Sequences of the 19 action types (14 classic + 5 DMX/spatial)
- **LSQ files**: Raw per-pixel RGB data at 40Hz (downloadable as ZIP)
- **Preview data**: 1 color per string per second for emulator


### Scheduling shows (sunset, weekly, seasons)
**Runtime → Schedule** plays shows by themselves — "the Christmas show from 15 minutes before sunset until 11 pm, every night from Nov 20 to Jan 6" — and puts the wash on between them.

1. **Location first.** Sunrise/sunset times need your latitude and longitude: **Settings → General → Location** (West longitudes are negative; the time zone defaults to America/Toronto). SlyLED never looks your location up.
2. **Between entries.** Choose the **wash** timeline to play whenever nothing is scheduled (the default — the stage is never dark unless you ask for it), or *hold the last frame*, or *off*. **Quiet hours** force the idle choice during a time range, e.g. 23:30–06:00.
3. **+ Schedule**, then entries. Each entry has days, a **start** and **end** (*at time*, or *sunset / sunrise / civil dusk / civil dawn* ± minutes; the end can also be *for N minutes*), and what to **play**: a timeline, the show playlist, or *off*. An end before its start means the next morning. A schedule can have a **season** (`11-20` to `01-06` repeats every year; `2026-12-01` to `2026-12-03` is one-off) and a **priority**: when entries overlap the higher priority wins, then the more specific one, then the later start — the panel says which won and why.
4. **Save**, then switch the schedule **On**. The header says what is playing and why ("Evening (Christmas) — sunset −15 m → 23:00 · until 23:00 · pos 12:31") and what comes next. The **week grid** draws every window with sunset lines; hover a block for the reason. **Simulate a date** lists any day's plan.

**Manual control wins.** Pressing Start, Stop or Next (SPA or Android), or a blackout, pauses the schedule: the header and the Android Now Playing line read *Manual — schedule paused* with **Resume**. By default it also picks up again at the next window edge (untick *resume at the next window edge* to stay paused until Resume). A restart does not undo a manual stop.

**Restarts.** If SlyLED restarts mid-window it joins the show where it would be by now (untick *Join late* on an entry to start it from the top). Timelines are baked automatically before they start, and the DMX engine is started if it was stopped. Transitions hand over without a dark frame.

**Fades.** An entry can fade in (seconds) when it comes up from dark, and fade out when the schedule is about to go dark (*off*). Fades never apply between two shows or into the wash — those hand over without a dip. Fades scale the output on top of your Master; the Master slider itself doesn't move.

**HinksPix standalone — shows that only use one controller's lights** *(not yet verified on hardware)*. A show that lights **nothing but** one HinksPix's pixels can also be played by that controller from its SD card. Tick **Offline** on such entries: the box is only offered for them (hover ⓘ on the others for the reason). **A show that also drives DMX fixtures, performers or another controller's pixels needs SlyLED running** — it can't be ticked, and if a ticked show is later edited to use more lights, the compile refuses that entry with the reason instead of sending part of it. In **HinksPix standalone**, tick the controller, then **Preview compile** (shows the week's rows) or **Compile & send**: SlyLED renders each eligible timeline, copies the shows and the wash to the controller's SD card with a weekday schedule, and sets its clock. The controller has no calendar, so one compile covers the coming week; tick *recompile + send nightly* and SlyLED redoes it at 03:30 while it runs (which also corrects the clock across daylight-saving changes). **Hand-off** decides when the controller plays from SD: *copy files only* (you switch modes yourself), *standalone when SlyLED exits, live when it starts*, or *standalone after every send*. The controller's port config must have been pushed first (Configure → Apply). Other things the controller can't do on its own are listed as warnings: fades, *hold the last frame*, and exceptions beyond the compiled week. The same rule applies to the controller's **Standalone playback** screen (its port configuration → **Standalone playback →**): only shows that use nothing but its pixels can be added.



---

