# Show scheduler (#954) — Phase 1

The authoritative design is issue #954. This note records what Phase 1 builds,
the operator defaults it ships with, and where each piece lives.

## Operator defaults (2026-09-24) — each is a setting

| Question (#954) | Default | Where it's set |
|---|---|---|
| Location | tz `America/Toronto`, **no** coordinates — sun refs need lat/lon entered | Runtime → Schedule → Location (stored in `schedule.json`) |
| Between entries | `idle = {"kind": "timeline", "timelineId": <wash>}` — never black | Schedule panel → Idle |
| Manual Stop | pauses the schedule; auto-resumes at the next window edge | `overridePolicy.autoResumeAtBoundary` (default `true`) |
| HinksPix hand-off | `hinkspix.handoff = "manual"` — no automatic mode switch (pending the mode-G bench question) | Schedule document; Phase 2 acts on it |
| Where the UI lives | Runtime tab, "Schedule" panel | — |

## Pieces

| Module | Role |
|---|---|
| `desktop/shared/solar.py` | NOAA sunrise/sunset/civil twilight, pure, UTC |
| `desktop/shared/schedule_eval.py` | `evaluate(doc, now)` → decision with reasons, next change, sun; `preview(doc, day, n)` → week-grid segments; `validate(doc, timeline_ids)` → errors/warnings. Pure. |
| `desktop/shared/show_scheduler.py` | `ShowScheduler` thread: tick → evaluate → `actions.play/idle/off`; override / resume / auto-resume; kill switch; log ring |
| `parent_server.py` | persistence (`schedule.json`, `schedule_state.json`), the actions (bake + sync + `_start_show_at` with position and hand-off), override hooks on manual playback verbs, `/api/schedule/*` |
| `spa/js/schedule.js` | Runtime-tab panel: state header, enable, entries editor, idle/quiet/location, week grid, simulate a date, log |
| Android | NowPlayingAnchor schedule line + Resume; Shows "Next up"; Status line |

## Playback rules

* **Start in position.** `_start_show_at(order, loop, offset_s, handoff)` computes
  where in the playlist `offset_s` falls and starts that item with its
  `go_epoch` in the past, so the DMX loop and performers (`ChildLED.cpp`
  resumes from a past `startEpoch`) join in progress.
* **Bake before start.** Bakes are in-memory; after a restart every timeline is
  unbaked. The actions bake (and sync performers) before starting, the same
  dance `_auto_start_show` does. A timeline that won't bake is logged and
  skipped.
* **Engine.** Starts go through `_ensure_output_engine` (#958).
* **Hand-off, no dark frame.** A scheduler transition sets a hand-off token
  before stopping the outgoing playback; the loops' end-of-play blackout
  sweeps skip when it is set, so the universes hold the last frame until the
  incoming show writes its first (#840 generalised to show-to-show).
* **Override.** `/api/show/start|stop|next` and `/api/timelines/<id>/start|stop`
  without `source: "schedule"` set the override (persisted). `POST
  /api/schedule/resume` clears it.
* **autoStartShow (#390)** is skipped when the scheduler is enabled.

## Phase 2

* **Compile** (`schedule_compile.compile_week`, pure): the next ≤ 7 local days
  of winning windows → per-weekday rows (split at midnight; the second half on
  the next weekday), one `.ply` per offline entry plus `WASH` for idle.
  `POST /api/schedule/compile/hinkspix/<cid> {deploy, horizonDays}`; deploy
  reuses the #941 worker (render `.hseq`, upload `.ply`s + seven `.sched`,
  set the clock). Rows name their own playlist (`hinkspix_files.schedule_text`).
* **Hand-off policy** `doc.hinkspix.handoff`: `manual` (default — files +
  clock, never a mode switch), `shutdown` (standalone on clean exit, live on
  start), `always` (standalone after each send). `nightly` recompiles + sends
  the listed `controllers` at 03:30 local, which also re-syncs the controller
  clock (DST).
* **Fades** `entry.transition {fadeInS, fadeOutS}`: a separate multiplier at
  the engines' send-time master gate (`_master_with_fade`) — the operator's
  master value is never written. Fade-in only when coming up from dark;
  fade-out only ahead of an `off` edge; a manual verb resets it.

## Not built

Multi-lane targets, brightness scheduling, shuffle, the Android Status-tab
health line.
