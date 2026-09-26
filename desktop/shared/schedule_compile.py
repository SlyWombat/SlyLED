"""schedule_compile.py — compile the show schedule for a HinksPix (#954 Phase 2).

When the orchestrator PC is off, a HinksPix can still run its own
weekday schedule from SD (#941): seven ``<DAY>.sched`` files whose rows name a
``.ply`` playlist. This module turns the scheduler document into those rows for
one controller. Pure: no Flask, no device I/O, no module state.

The controller has weekday rows and **no date** (its RTC packet carries
time-of-day + weekday only), so one compile is exact for the coming seven days
and drifts by the sunset delta after that; the orchestrator recompiles nightly
while it's running.

Only a show that runs *entirely* on one controller's pixels can play offline
(#963): a timeline that also drives DMX fixtures, performers or another
controller's pixels needs SlyLED running, so it is refused with a plain reason,
never compiled partially. ``offline_check`` is that rule; the compile, the
standalone deploy (orch_hinkspix) and the SPA all use it.

Inputs:
  doc            the schedule document (schedule_eval format)
  first_day      local date the week starts on
  timeline_ok    callable(timeline_id) → ``offline_check`` result for this
                 controller (a plain bool is still accepted: True = eligible)
  timeline_name  callable(timeline_id) → display name
  duration_s     callable(timeline_id) → seconds (for the frame-cap warning)

Output::

    {"from", "to", "days": {"MONDAY": [{"start": "16:28", "end": "23:00",
                                        "playlist": "EVENING", "repeat": 1}, ...]},
     "playlists": {"EVENING": [3, 5], "WASH": [12]},
     "warnings": [...], "windows": [...],
     "refused": [{"entry": "Christmas", "reason": "..."}]}
"""

from datetime import datetime, time as dtime, timedelta

import fixture_types
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


# ── Offline eligibility (#963) ──────────────────────────────────────────────

def _is_output(f, children_by_id):
    """A fixture playback actually lights: a DMX fixture, or an LED pixel
    target bound to a device. Cameras, remotes, radar and empty controller
    placeholders are not outputs."""
    ft = f.get("fixtureType") or "led"
    if ft == "dmx":
        return True
    if ft != "led" or f.get("type") == "group":
        return False
    return (f.get("childId") in children_by_id
            and fixture_types.is_pixel_target(f, children_by_id))


def driven_fixtures(timeline, fixtures, children):
    """The output fixtures *timeline*'s clip-bearing tracks drive, expanded
    the way the bake expands them (stage-wide tracks → every fixture, group
    tracks → their members)."""
    by_id = {f.get("id"): f for f in fixtures or []}
    by_child = {c.get("id"): c for c in children or []}
    out = {}
    for tr in (timeline or {}).get("tracks") or []:
        if not tr.get("clips"):
            continue
        if tr.get("allPerformers"):
            cands = [f for f in fixtures or [] if f.get("type") != "group"]
        else:
            f = by_id.get(tr.get("fixtureId"))
            if f is None:
                continue
            if f.get("type") == "group" and f.get("childIds"):
                cands = [by_id[m] for m in f["childIds"] if m in by_id]
            else:
                cands = [f]
        for f in cands:
            if _is_output(f, by_child):
                out[f.get("id")] = f
    return list(out.values())


def _pixel_controller(f, children_by_id):
    """The pixel controller id a driven fixture is on, else None."""
    if (f.get("fixtureType") or "led") != "led":
        return None
    ch = children_by_id.get(f.get("childId")) or {}
    return ch.get("id") if ch.get("type") in fixture_types.PIXEL_CONTROLLER_TYPES else None


def _names(items, noun, plural):
    items = sorted(items)
    if len(items) == 1:
        return f"{noun} '{items[0]}'"
    if len(items) == 2:
        return f"{plural} '{items[0]}' and '{items[1]}'"
    return f"{len(items)} {plural}"


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def offline_check(timeline, cid, fixtures, children):
    """Can *timeline* play offline on HinksPix *cid*?

    Returns ``{"eligible", "uses", "reason"}``: ``uses`` is True when the
    timeline lights any of this controller's pixels; ``eligible`` only when
    that is ALL it lights. ``reason`` says why not, in operator words.
    """
    by_child = {c.get("id"): c for c in children or []}
    name = (timeline or {}).get("name") or f"timeline {(timeline or {}).get('id')}"
    ctl = by_child.get(cid) or {}
    cname = ctl.get("name") or ctl.get("ip") or f"controller {cid}"
    drv = driven_fixtures(timeline, fixtures, children)
    mine = [f for f in drv if _pixel_controller(f, by_child) == cid]
    if not mine:
        return {"eligible": False, "uses": False,
                "reason": f"'{name}' doesn't light any of {cname}'s pixels"}
    dmx = [f.get("name") or f"fixture {f.get('id')}" for f in drv
           if (f.get("fixtureType") or "led") == "dmx"]
    other_ctl = sorted({by_child[_pixel_controller(f, by_child)].get("name")
                        or by_child[_pixel_controller(f, by_child)].get("ip")
                        for f in drv if _pixel_controller(f, by_child) not in (None, cid)})
    performers = {f.get("childId") for f in drv
                  if (f.get("fixtureType") or "led") == "led" and _pixel_controller(f, by_child) is None}
    parts = []
    if dmx:
        parts.append(_names(dmx, "DMX fixture", "DMX fixtures"))
    if performers:
        parts.append(f"{len(performers)} performer" + ("s" if len(performers) != 1 else ""))
    if other_ctl:
        parts.append("pixels on " + _join([f"'{n}'" for n in other_ctl]))
    if parts:
        return {"eligible": False, "uses": True,
                "reason": f"'{name}' also drives {_join(parts)} — it needs SlyLED running, "
                          f"so it can't be scheduled offline on {cname}"}
    return {"eligible": True, "uses": True, "reason": None}


def offline_summary(timeline, fixtures, children):
    """Per-timeline answer for the SPA: the controller it can play offline
    on (``controllerId``), or why it can't play offline anywhere."""
    by_child = {c.get("id"): c for c in children or []}
    counts = {}
    for f in driven_fixtures(timeline, fixtures, children):
        c = _pixel_controller(f, by_child)
        if c is not None:
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        name = (timeline or {}).get("name") or f"timeline {(timeline or {}).get('id')}"
        return {"eligible": False, "controllerId": None,
                "reason": f"'{name}' doesn't light any HinksPix pixels — nothing to play offline"}
    cid = max(counts, key=lambda k: (counts[k], -k))
    r = offline_check(timeline, cid, fixtures, children)
    return {"eligible": r["eligible"], "controllerId": cid, "reason": r["reason"]}


def _as_check(result):
    """Normalise a timeline_ok result: a plain bool (older callers/tests)
    means eligible-and-used or neither."""
    if isinstance(result, dict):
        return result
    return {"eligible": bool(result), "uses": bool(result), "reason": None}


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
    refused = {}          # key → {"entry", "reason"}

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
        if key in refused:
            continue
        tids = []
        why_not = None
        for tid in _play_timelines(play):
            if tid is None:
                continue
            chk = _as_check(timeline_ok(tid))
            if not chk["uses"]:
                warn(("nopix", tid), f"'{timeline_name(tid)}' has no pixels on this "
                                     "controller — left out of the offline schedule")
                continue
            if not chk["eligible"]:
                why_not = chk["reason"] or (f"'{timeline_name(tid)}' drives more than "
                                            "this controller")
                break
            if hf.frames_for_duration(duration_s(tid) or 0, STEP_MS) > MAX_FRAMES:
                warn(("frames", tid), f"'{timeline_name(tid)}' is longer than the "
                                      f"{MAX_FRAMES}-frame sequence limit (27.3 min)")
            tids.append(tid)
        if why_not:
            # #963: refuse the whole entry — never a partial playlist.
            refused[key] = {"entry": label, "reason": why_not}
            warn(("refused",) + key, f"{label}: not scheduled offline — {why_not}")
            continue
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
            "warnings": warnings, "windows": windows,
            "refused": list(refused.values())}
