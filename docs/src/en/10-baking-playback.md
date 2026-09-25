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


---

