"""schedule_compile.py — compile the show schedule for a HinksPix (#954 Phase 2).

When the orchestrator PC is off, a HinksPix can still run its own
weekday schedule from SD (#941): seven ``<DAY>.sched`` files whose rows name a
``.ply`` playlist. This module turns the scheduler document into those rows for
one controller. Pure: no Flask, no device I/O, no module state.

The controller has weekday rows and **no date** (its RTC packet carries
time-of-day + weekday only), so one compile is exact for the coming seven days
and drifts by the sunset delta after that; the orchestrator recompiles nightly
while it's running.

Inputs:
  doc            the schedule document (schedule_eval format)
  first_day      local date the week starts on
  timeline_ok    callable(timeline_id) → True when that timeline has pixels on
                 this controller (anything else can't play offline)
  timeline_name  callable(timeline_id) → display name
  duration_s     callable(timeline_id) → seconds (for the frame-cap warning)

Output::

    {"from", "to", "days": {"MONDAY": [{"start": "16:28", "end": "23:00",
                                        "playlist": "EVENING", "repeat": 1}, ...]},
     "playlists": {"EVENING": [3, 5], "WASH": [12]},
     "warnings": [...], "windows": [...]}
"""

from datetime import datetime, time as dtime, timedelta

import hinkspix_files as hf
import schedule_eval as se

# datetime.weekday() (Mon=0) → the controller's day names.
_WEEKDAY = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
MAX_FRAMES = hf.MAX_FRAMES
STEP_MS = 25


def _hhmm(dt):
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _play_timelines(play):
    if not play:
        return []
    if play.get("kind") == "timeline":
        return [play.get("timelineId")]
    if play.get("kind") == "playlist":
        return list(play.get("order") or [])
    return []


def _entry_lookup(doc):
    out = {}
    for s in (doc or {}).get("schedules") or []:
        for e in s.get("entries") or []:
            out[(s.get("id"), e.get("id"))] = (s, e)
    return out


def compile_week(doc, first_day, timeline_ok, timeline_name=None, duration_s=None,
                 days=7):
    """Compile *days* (max 7) local dates from *first_day*. See module doc."""
    days = max(1, min(7, int(days)))
    timeline_name = timeline_name or (lambda tid: f"timeline {tid}")
    duration_s = duration_s or (lambda tid: 0)
    tz = se.tzinfo_for(doc)
    pv = se.preview(doc, first_day, days)
    entries = _entry_lookup(doc)
    warnings = list(pv.get("notes") or [])
    rows_by_day = {d: [] for d in hf.DAYS}
    playlists = {}
    taken = set()
    names_for = {}
    windows = []
    warned = set()

    def warn(key, text):
        if key not in warned:
            warned.add(key)
            warnings.append(text)

    def playlist_for(key, label, tids):
        if key in names_for:
            return names_for[key]
        name = hf.short_name(label, taken)
        taken.add(name)
        names_for[key] = name
        playlists[name] = list(tids)
        return name

    idle = (doc or {}).get("idle") or {}
    if idle.get("kind") == "hold":
        warn("hold", "Idle is 'hold the last frame' — the controller can't hold a frame "
                     "on its own, so idle time stays dark offline")

    for seg in pv.get("segments") or []:
        play = seg.get("play") or {}
        kind = play.get("kind")
        if kind in ("off", "hold") or not kind:
            continue                                  # dark offline
        ent = seg.get("entry")
        if ent is not None:
            s_e = entries.get((ent.get("scheduleId"), ent.get("entryId")))
            e = s_e[1] if s_e else {}
            if not ((e.get("hinkspix") or {}).get("compile")):
                continue                              # entry not marked for offline
            if (e.get("transition") or {}).get("fadeInS") or (e.get("transition") or {}).get("fadeOutS"):
                warn(("fade", ent.get("scheduleId"), ent.get("entryId")),
                     f"{seg['what']}: fades don't compile — the controller cuts in and out")
            label = e.get("name") or seg["what"]
            key = ("entry", ent.get("scheduleId"), ent.get("entryId"))
        else:
            if not (((doc or {}).get("hinkspix") or {}).get("compileIdle", True)):
                continue
            label = "WASH"
            key = ("idle",)
        tids = []
        for tid in _play_timelines(play):
            if tid is None:
                continue
            if not timeline_ok(tid):
                warn(("nopix", tid), f"'{timeline_name(tid)}' has no pixels on this "
                                     "controller — left out of the offline schedule")
                continue
            if hf.frames_for_duration(duration_s(tid) or 0, STEP_MS) > MAX_FRAMES:
                warn(("frames", tid), f"'{timeline_name(tid)}' is longer than the "
                                      f"{MAX_FRAMES}-frame sequence limit (27.3 min)")
            tids.append(tid)
        if not tids:
            continue
        name = playlist_for(key, label, tids)
        a = seg["startUtc"].astimezone(tz)
        b = seg["endUtc"].astimezone(tz)
        windows.append({"playlist": name, "what": seg["what"],
                        "startLocal": a.isoformat(), "endLocal": b.isoformat()})
        # Split at local midnights; each piece lands on its own weekday.
        cur = a
        while cur < b:
            next_mid = datetime.combine(cur.date() + timedelta(days=1), dtime(0, 0),
                                        tzinfo=tz)
            piece_end = min(b, next_mid)
            end_txt = "23:59" if piece_end == next_mid else _hhmm(piece_end)
            start_txt = _hhmm(cur)
            if start_txt < end_txt:
                rows_by_day[_WEEKDAY[cur.weekday()]].append(
                    {"start": start_txt, "end": end_txt, "playlist": name,
                     "repeat": 1, "enabled": True})
            cur = piece_end

    # Exceptions beyond the compiled week can't be expressed.
    last = first_day + timedelta(days=days - 1)
    for s in (doc or {}).get("schedules") or []:
        for x in s.get("exceptions") or []:
            try:
                d = datetime.fromisoformat(str(x.get("date"))).date()
            except ValueError:
                continue
            if d > last:
                warn(("exc", s.get("id"), x.get("date")),
                     f"{s.get('name')}: the {x.get('date')} exception is after this "
                     "compiled week — it applies once a nightly recompile reaches it")
    for day in rows_by_day:
        rows_by_day[day].sort(key=lambda r: r["start"])
        for r in rows_by_day[day]:
            bad = hf.validate_schedule_row(r)
            if bad:
                raise hf.HinksPixFileError(f"{day} {r}: {bad}")
    return {"from": first_day.isoformat(), "to": last.isoformat(),
            "days": rows_by_day, "playlists": playlists,
            "warnings": warnings, "windows": windows}
