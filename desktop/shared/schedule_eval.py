"""schedule_eval.py — what should be playing right now (#954).

A pure function of (schedule document, clock): no Flask, no module state, no
side effects, so every rule below is a unit test with a fake ``now``. The
engine (``show_scheduler.py``) turns its answer into playback.

Document (``data/schedule.json``)::

    {"enabled": true,
     "location": {"lat": 43.65, "lon": -79.38, "tz": "America/Toronto"},
     "idle": {"kind": "timeline", "timelineId": 12} | {"kind": "hold"} | {"kind": "off"},
     "quietHours": {"start": "23:30", "end": "06:00"} | null,
     "overridePolicy": {"autoResumeAtBoundary": true},
     "schedules": [{"id", "name", "priority", "enabled",
                    "season": {"from": "11-20", "to": "01-06"} | null,
                    "entries": [{"id", "name", "enabled", "days": ["mon", ...],
                                 "start": {"ref": "clock", "time": "18:00"}
                                        | {"ref": "sunset"|"sunrise"|"civilDusk"|"civilDawn",
                                           "offsetMin": -15},
                                 "end":   same, or {"durationMin": 90},
                                 "play":  {"kind": "timeline", "timelineId", "loop"}
                                        | {"kind": "playlist", "order": [...], "loop"}
                                        | {"kind": "off"},
                                 "resume": "position" | "start"}],
                    "exceptions": [{"date": "2026-12-24", "entryId": 1, ...patch}
                                   | {"date": "2026-12-31", "play": {...},
                                      "priority": 90, "name": "NYE dark",
                                      "start"?, "end"?}]}]}

Times are wall-clock in ``location.tz``; every comparison is in UTC. An end
at or before its start means the next day, so cross-midnight windows are
first class. Conflicts resolve deterministically: highest priority, then most
specific (exception > one-season entry > recurring season > no season), then
the later start, then the lower (scheduleId, entryId).
"""

from datetime import date, datetime, time as dtime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover — py < 3.9 is not supported
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

import solar

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SUN_REFS = ("sunrise", "sunset", "civilDawn", "civilDusk")
DEFAULT_TZ = "America/Toronto"
UTC = timezone.utc


class ScheduleError(ValueError):
    """A document the evaluator cannot use at all."""


# ── Document defaults ────────────────────────────────────────────────────────

def default_document():
    """A fresh, disabled schedule with the operator's agreed defaults (#954):
    tz America/Toronto with no guessed coordinates, idle = the wash timeline
    (chosen in the panel), auto-resume at the next window edge."""
    return {"enabled": False,
            "location": {"lat": None, "lon": None, "tz": DEFAULT_TZ},
            "idle": {"kind": "timeline", "timelineId": None},
            "quietHours": None,
            "overridePolicy": {"autoResumeAtBoundary": True},
            "hinkspix": {"handoff": "manual"},
            "schedules": []}


def tzinfo_for(doc):
    name = ((doc or {}).get("location") or {}).get("tz") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ScheduleError(f"unknown time zone {name!r}") from exc


def _hhmm(s):
    try:
        hh, mm = str(s).split(":")
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise ScheduleError(f"time must be HH:MM, not {s!r}")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ScheduleError(f"time out of range: {s!r}")
    return hh, mm


# ── Resolving a time reference on a date ────────────────────────────────────

def local_clock(day, hhmm, tz, notes=None):
    """Wall-clock *hhmm* on *day* in *tz*, as UTC. Spring-forward: a time
    that doesn't exist moves forward by the gap (02:30 → 03:30). Fall-back:
    an ambiguous time takes its first occurrence (fold=0)."""
    hh, mm = _hhmm(hhmm)
    local = datetime.combine(day, dtime(hh, mm), tzinfo=tz).replace(fold=0)
    utc = local.astimezone(UTC)
    back = utc.astimezone(tz)
    if (back.hour, back.minute) != (hh, mm) and notes is not None:
        notes.append(f"{hh:02d}:{mm:02d} does not exist on {day.isoformat()} "
                     f"(DST); using {back.hour:02d}:{back.minute:02d}")
    return utc


def resolve_ref(ref, day, doc, tz, notes=None):
    """A ``start``/``end`` reference on local date *day* → UTC datetime, or
    None when it can't happen (the sun never sets that day)."""
    ref = ref or {}
    kind = ref.get("ref", "clock")
    if kind == "clock":
        return local_clock(day, ref.get("time", "00:00"), tz, notes)
    if kind in SUN_REFS:
        loc = (doc or {}).get("location") or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        if lat is None or lon is None:
            raise ScheduleError("a sunrise/sunset time needs the location "
                                "(latitude/longitude) set in Settings")
        at = solar.sun_times(day, float(lat), float(lon))[kind]
        if at is None:
            return None
        return at + timedelta(minutes=float(ref.get("offsetMin") or 0))
    raise ScheduleError(f"unknown time reference {kind!r}")


def describe_ref(ref):
    ref = ref or {}
    if "durationMin" in ref:
        return f"{ref['durationMin']} min later"
    kind = ref.get("ref", "clock")
    if kind == "clock":
        return ref.get("time", "00:00")
    off = int(ref.get("offsetMin") or 0)
    name = {"sunset": "sunset", "sunrise": "sunrise",
            "civilDusk": "civil dusk", "civilDawn": "civil dawn"}.get(kind, kind)
    return name if not off else f"{name} {'+' if off > 0 else '−'}{abs(off)} m"


# ── Seasons ─────────────────────────────────────────────────────────────────

def _season_key(s):
    """'MM-DD' → (None, m, d); 'YYYY-MM-DD' → (y, m, d)."""
    parts = str(s).split("-")
    try:
        if len(parts) == 2:
            return None, int(parts[0]), int(parts[1])
        if len(parts) == 3:
            return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        pass
    raise ScheduleError(f"season dates are MM-DD or YYYY-MM-DD, not {s!r}")


def in_season(season, day):
    if not season:
        return True
    fy, fm, fd = _season_key(season.get("from"))
    ty, tm, td = _season_key(season.get("to"))
    if fy is not None and ty is not None:
        return date(fy, fm, fd) <= day <= date(ty, tm, td)
    a, b, x = (fm, fd), (tm, td), (day.month, day.day)
    if a <= b:
        return a <= x <= b
    return x >= a or x <= b                      # wraps the year (11-20 → 01-06)


def season_specificity(season):
    if not season:
        return 0
    fy, _, _ = _season_key(season.get("from"))
    return 2 if fy is not None else 1


# ── Windows ─────────────────────────────────────────────────────────────────

def _entry_windows(doc, day, tz, notes):
    """Every window that STARTS on local date *day*."""
    out = []
    for sch in (doc or {}).get("schedules") or []:
        if sch.get("enabled") is False or not in_season(sch.get("season"), day):
            continue
        spec = season_specificity(sch.get("season"))
        excs = [e for e in (sch.get("exceptions") or []) if e.get("date") == day.isoformat()]
        patches = {e.get("entryId"): e for e in excs if e.get("entryId") is not None}
        for ent in sch.get("entries") or []:
            if ent.get("enabled") is False:
                continue
            patch = patches.get(ent.get("id"))
            e = dict(ent)
            if patch:
                e.update({k: v for k, v in patch.items() if k not in ("date", "entryId")})
            if e.get("enabled") is False:
                continue
            if DAYS[day.weekday()] not in (e.get("days") or []):
                continue
            w = _window(doc, sch, e, day, tz, notes,
                        specificity=3 if patch else spec, exception=bool(patch))
            if w:
                out.append(w)
        for exc in excs:
            if exc.get("entryId") is not None:
                continue
            e = {"id": f"x{exc.get('date')}", "name": exc.get("name") or "Exception",
                 "start": exc.get("start") or {"ref": "clock", "time": "00:00"},
                 "end": exc.get("end") or {"ref": "clock", "time": "00:00"},
                 "play": exc.get("play") or {"kind": "off"},
                 "resume": exc.get("resume", "position")}
            s2 = dict(sch, priority=exc.get("priority", sch.get("priority", 0)))
            w = _window(doc, s2, e, day, tz, notes, specificity=3, exception=True)
            if w:
                out.append(w)
    return out


def _window(doc, sch, ent, day, tz, notes, specificity, exception):
    start = resolve_ref(ent.get("start"), day, doc, tz, notes)
    if start is None:
        notes.append(f"{sch.get('name')} › {ent.get('name')}: no "
                     f"{describe_ref(ent.get('start'))} on {day.isoformat()}")
        return None
    end_ref = ent.get("end") or {}
    if "durationMin" in end_ref:
        end = start + timedelta(minutes=float(end_ref["durationMin"]))
    else:
        end = resolve_ref(end_ref, day, doc, tz, notes)
        if end is None:
            notes.append(f"{sch.get('name')} › {ent.get('name')}: no "
                         f"{describe_ref(end_ref)} on {day.isoformat()}")
            return None
        if end <= start:                        # ends the next day
            end = resolve_ref(end_ref, day + timedelta(days=1), doc, tz, notes)
            if end is None:
                return None
    if end <= start:
        return None
    return {"scheduleId": sch.get("id"), "scheduleName": sch.get("name") or "",
            "entryId": ent.get("id"), "name": ent.get("name") or "",
            "priority": int(sch.get("priority") or 0), "specificity": specificity,
            "exception": exception, "day": day.isoformat(),
            "startUtc": start, "endUtc": end,
            "startDesc": describe_ref(ent.get("start")),
            "endDesc": describe_ref(end_ref),
            "play": ent.get("play") or {"kind": "off"},
            "resume": ent.get("resume", "position"),
            "transition": ent.get("transition") or None}


def _rank(w):
    return (-w["priority"], -w["specificity"], -w["startUtc"].timestamp(),
            str(w["scheduleId"]), str(w["entryId"]))


def _label(w):
    return f"{w['scheduleName']} › {w['name']}" if w.get("scheduleName") else w["name"]


def _why_wins(win, other):
    if win["priority"] != other["priority"]:
        return f"priority {win['priority']} > {other['priority']}"
    if win["specificity"] != other["specificity"]:
        return "more specific (" + ("date exception" if win["specificity"] == 3 else "season") + ")"
    if win["startUtc"] != other["startUtc"]:
        return "started later"
    return "lower schedule/entry id"


def in_quiet_hours(doc, now_utc, tz):
    q = (doc or {}).get("quietHours")
    if not q or not q.get("start") or not q.get("end"):
        return None
    local = now_utc.astimezone(tz)
    s, e = _hhmm(q["start"]), _hhmm(q["end"])
    x = (local.hour, local.minute)
    inside = (s <= x < e) if s <= e else (x >= s or x < e)
    return f"quiet hours {q['start']}–{q['end']}" if inside else None


def _quiet_edges(doc, day, tz):
    q = (doc or {}).get("quietHours")
    if not q or not q.get("start") or not q.get("end"):
        return []
    return [local_clock(day, q["start"], tz), local_clock(day, q["end"], tz)]


def windows_between(doc, start_day, end_day, tz, notes=None):
    """All windows starting on local dates start_day..end_day inclusive."""
    notes = [] if notes is None else notes
    out, d = [], start_day
    while d <= end_day:
        out.extend(_entry_windows(doc, d, tz, notes))
        d += timedelta(days=1)
    return out


# ── The decision ────────────────────────────────────────────────────────────

def _decide_at(doc, now, tz, windows, notes):
    """(winner window | None, candidates, reason, quiet)."""
    cands = sorted([w for w in windows if w["startUtc"] <= now < w["endUtc"]], key=_rank)
    quiet = in_quiet_hours(doc, now, tz)
    if quiet:
        return None, cands, quiet, True
    if not cands:
        return None, cands, "no entry matches", False
    win = cands[0]
    reason = f"{_label(win)}: {win['startDesc']} → {win['endDesc']}"
    if len(cands) > 1:
        reason += f"; wins over {_label(cands[1])} ({_why_wins(win, cands[1])})"
    return win, cands, reason, False


def _idle_play(doc):
    idle = (doc or {}).get("idle") or {"kind": "off"}
    kind = idle.get("kind", "off")
    if kind == "timeline":
        if idle.get("timelineId") is None:
            return {"kind": "hold"}, "idle: no wash timeline chosen — holding the last frame"
        return {"kind": "timeline", "timelineId": idle["timelineId"], "loop": True}, "idle = wash"
    if kind == "hold":
        return {"kind": "hold"}, "idle = hold the last frame"
    return {"kind": "off"}, "idle = off (dark)"


def _key(win, play):
    if win is None:
        return ("idle", repr(sorted((play or {}).items())))
    return (str(win["scheduleId"]), str(win["entryId"]), win["startUtc"].isoformat())


def evaluate(doc, now_utc, _with_next=True):
    """What should be playing at *now_utc* (aware datetime or epoch seconds).

    Returns a plain dict (JSON-ready once datetimes are ISO strings — see
    ``to_json``)::

        {"play": {...}, "source": "entry"|"idle"|"quiet", "elapsedS": float,
         "entry": {...} | None, "window": {"startUtc", "endUtc"} | None,
         "reason": str, "candidates": [...], "key": tuple,
         "next": {"atUtc", "what", "reason"} | None,
         "sun": {...}, "notes": [...]}
    """
    if not isinstance(now_utc, datetime):
        now_utc = datetime.fromtimestamp(float(now_utc), UTC)
    tz = tzinfo_for(doc)
    notes = []
    today = now_utc.astimezone(tz).date()
    windows = windows_between(doc, today - timedelta(days=1), today + timedelta(days=1),
                              tz, notes)
    win, cands, reason, quiet = _decide_at(doc, now_utc, tz, windows, notes)
    if win is not None:
        play = dict(win["play"])
        source = "entry"
        elapsed = ((now_utc - win["startUtc"]).total_seconds()
                   if win.get("resume", "position") == "position" else 0.0)
    else:
        play, idle_reason = _idle_play(doc)
        source = "quiet" if quiet else "idle"
        reason = f"{reason}; {idle_reason}"
        elapsed = 0.0
    out = {"play": play, "source": source, "elapsedS": elapsed,
           "entry": None if win is None else {
               "scheduleId": win["scheduleId"], "scheduleName": win["scheduleName"],
               "entryId": win["entryId"], "name": win["name"],
               "priority": win["priority"]},
           "window": None if win is None else {"startUtc": win["startUtc"],
                                               "endUtc": win["endUtc"],
                                               "startDesc": win["startDesc"],
                                               "endDesc": win["endDesc"],
                                               "transition": win.get("transition")},
           "reason": reason,
           "candidates": [{"scheduleId": c["scheduleId"], "entryId": c["entryId"],
                           "name": _label(c), "priority": c["priority"],
                           "startUtc": c["startUtc"], "endUtc": c["endUtc"]}
                          for c in cands],
           "key": _key(win, play), "notes": notes}
    if _with_next:
        out["next"] = _next_change(doc, now_utc, tz, out["key"])
        loc = (doc or {}).get("location") or {}
        if loc.get("lat") is not None and loc.get("lon") is not None:
            st = solar.sun_times(today, float(loc["lat"]), float(loc["lon"]))
            out["sun"] = {"date": today.isoformat(),
                          "sunrise": st["sunrise"], "sunset": st["sunset"]}
        else:
            out["sun"] = None
    return out


def _next_change(doc, now, tz, cur_key, horizon_days=8):
    """The first window edge after *now* at which the decision changes."""
    today = now.astimezone(tz).date()
    notes = []
    windows = windows_between(doc, today - timedelta(days=1),
                              today + timedelta(days=horizon_days), tz, notes)
    edges = set()
    for w in windows:
        for t in (w["startUtc"], w["endUtc"]):
            if t > now:
                edges.add(t)
    d = today
    for _ in range(horizon_days + 1):
        for t in _quiet_edges(doc, d, tz):
            if t > now:
                edges.add(t)
        d += timedelta(days=1)
    for t in sorted(edges):
        win, _c, reason, quiet = _decide_at(doc, t, tz, windows, notes)
        if win is not None:
            play = win["play"]
            what = _label(win)
        else:
            play, idle_reason = _idle_play(doc)
            what = "quiet hours" if quiet else "idle"
            reason = f"{reason}; {idle_reason}"
        if _key(win, play) != cur_key:
            return {"atUtc": t, "what": what, "play": play, "reason": reason}
    return None


# ── Preview (week grid / simulate a date) ───────────────────────────────────

def preview(doc, first_day, days=7):
    """Resolved windows and the winning segments for *days* local dates from
    *first_day*: what the week grid draws and "simulate a date" lists."""
    tz = tzinfo_for(doc)
    notes = []
    last = first_day + timedelta(days=days - 1)
    windows = windows_between(doc, first_day - timedelta(days=1), last, tz, notes)
    t0 = datetime.combine(first_day, dtime(0, 0), tzinfo=tz).astimezone(UTC)
    t1 = datetime.combine(last + timedelta(days=1), dtime(0, 0), tzinfo=tz).astimezone(UTC)
    edges = {t0, t1}
    for w in windows:
        for t in (w["startUtc"], w["endUtc"]):
            if t0 < t < t1:
                edges.add(t)
    d = first_day
    while d <= last:
        for t in _quiet_edges(doc, d, tz):
            if t0 < t < t1:
                edges.add(t)
        d += timedelta(days=1)
    edges = sorted(edges)
    segments = []
    for a, b in zip(edges, edges[1:]):
        mid = a + (b - a) / 2
        win, cands, reason, quiet = _decide_at(doc, mid, tz, windows, notes)
        if win is not None:
            play, what = win["play"], _label(win)
        else:
            play, idle_reason = _idle_play(doc)
            what = "quiet hours" if quiet else "idle"
            reason = f"{reason}; {idle_reason}"
        seg = {"startUtc": a, "endUtc": b, "what": what, "play": play,
               "reason": reason, "entry": None if win is None else
               {"scheduleId": win["scheduleId"], "entryId": win["entryId"]},
               "losers": [_label(c) for c in cands[1:]] if win is not None else []}
        if segments and segments[-1]["what"] == seg["what"] \
                and segments[-1]["play"] == seg["play"] and segments[-1]["endUtc"] == a:
            segments[-1]["endUtc"] = b
        else:
            segments.append(seg)
    sun = []
    loc = (doc or {}).get("location") or {}
    d = first_day
    while d <= last:
        if loc.get("lat") is not None and loc.get("lon") is not None:
            st = solar.sun_times(d, float(loc["lat"]), float(loc["lon"]))
            sun.append({"date": d.isoformat(), "sunrise": st["sunrise"], "sunset": st["sunset"]})
        d += timedelta(days=1)
    visible = [w for w in windows if w["endUtc"] > t0 and w["startUtc"] < t1]
    return {"from": first_day.isoformat(), "days": days, "tz": str(tz),
            "segments": segments, "windows": visible, "sun": sun,
            "notes": notes, "warnings": overlap_warnings(visible)}


def overlap_warnings(windows):
    out = []
    ws = sorted(windows, key=lambda w: w["startUtc"])
    for i, a in enumerate(ws):
        for b in ws[i + 1:]:
            if b["startUtc"] >= a["endUtc"]:
                break
            if a["priority"] == b["priority"] and (a["scheduleId"], a["entryId"]) != \
                    (b["scheduleId"], b["entryId"]):
                win, lose = sorted([a, b], key=_rank)
                out.append(f"{_label(a)} and {_label(b)} overlap on {a['day']} at the "
                           f"same priority; {_label(win)} wins ({_why_wins(win, lose)})")
    return out


# ── Validation ──────────────────────────────────────────────────────────────

def validate(doc, timeline_ids=()):
    """(errors, warnings) for a document about to be saved."""
    errors, warnings = [], []
    if not isinstance(doc, dict):
        return ["schedule must be an object"], []
    try:
        tz = tzinfo_for(doc)
    except ScheduleError as exc:
        errors.append(str(exc))
        tz = None
    loc = doc.get("location") or {}
    for k, lo, hi in (("lat", -90, 90), ("lon", -180, 180)):
        v = loc.get(k)
        if v is not None:
            try:
                if not lo <= float(v) <= hi:
                    errors.append(f"location.{k} must be {lo}..{hi}")
            except (TypeError, ValueError):
                errors.append(f"location.{k} must be a number")
    tids = set(timeline_ids or ())
    uses_sun = False
    idle = doc.get("idle") or {}
    if idle.get("kind") not in ("timeline", "hold", "off"):
        errors.append("idle.kind must be timeline, hold or off")
    elif idle.get("kind") == "off":
        warnings.append("idle is off: the stage goes dark between entries")
    elif idle.get("kind") == "timeline":
        if idle.get("timelineId") is None:
            warnings.append("no wash timeline chosen for idle — the last frame is held")
        elif tids and idle["timelineId"] not in tids:
            errors.append(f"idle timeline {idle['timelineId']} doesn't exist")
    q = doc.get("quietHours")
    if q:
        for k in ("start", "end"):
            try:
                _hhmm(q.get(k))
            except ScheduleError as exc:
                errors.append(f"quietHours.{k}: {exc}")

    def check_ref(where, ref, allow_duration=False):
        nonlocal uses_sun
        ref = ref or {}
        if allow_duration and "durationMin" in ref:
            try:
                if float(ref["durationMin"]) <= 0:
                    errors.append(f"{where}: duration must be > 0")
            except (TypeError, ValueError):
                errors.append(f"{where}: duration must be a number")
            return
        kind = ref.get("ref", "clock")
        if kind == "clock":
            try:
                _hhmm(ref.get("time"))
            except ScheduleError as exc:
                errors.append(f"{where}: {exc}")
        elif kind in SUN_REFS:
            uses_sun = True
            try:
                float(ref.get("offsetMin") or 0)
            except (TypeError, ValueError):
                errors.append(f"{where}: offset must be minutes")
        else:
            errors.append(f"{where}: unknown time reference {kind!r}")

    def check_play(where, play):
        play = play or {}
        k = play.get("kind")
        if k == "timeline":
            if tids and play.get("timelineId") not in tids:
                errors.append(f"{where}: timeline {play.get('timelineId')} doesn't exist")
        elif k == "playlist":
            order = play.get("order") or []
            if not order:
                errors.append(f"{where}: playlist is empty")
            missing = [t for t in order if tids and t not in tids]
            if missing:
                errors.append(f"{where}: timelines {missing} don't exist")
        elif k != "off":
            errors.append(f"{where}: play.kind must be timeline, playlist or off")

    seen_s = set()
    for sch in doc.get("schedules") or []:
        sname = sch.get("name") or f"schedule {sch.get('id')}"
        if sch.get("id") in seen_s:
            errors.append(f"duplicate schedule id {sch.get('id')}")
        seen_s.add(sch.get("id"))
        if sch.get("season"):
            try:
                _season_key(sch["season"].get("from"))
                _season_key(sch["season"].get("to"))
            except ScheduleError as exc:
                errors.append(f"{sname}: {exc}")
        seen_e = set()
        for ent in sch.get("entries") or []:
            where = f"{sname} › {ent.get('name') or ent.get('id')}"
            if ent.get("id") in seen_e:
                errors.append(f"{sname}: duplicate entry id {ent.get('id')}")
            seen_e.add(ent.get("id"))
            if not [d for d in (ent.get("days") or []) if d in DAYS]:
                warnings.append(f"{where}: no days selected — never plays")
            bad_days = [d for d in (ent.get("days") or []) if d not in DAYS]
            if bad_days:
                errors.append(f"{where}: unknown days {bad_days}")
            check_ref(f"{where} start", ent.get("start"))
            check_ref(f"{where} end", ent.get("end"), allow_duration=True)
            check_play(where, ent.get("play"))
            if ent.get("resume", "position") not in ("position", "start"):
                errors.append(f"{where}: resume must be position or start")
            s, e = ent.get("start") or {}, ent.get("end") or {}
            if s.get("ref", "clock") == "clock" and e.get("ref", "clock") == "clock" \
                    and "durationMin" not in e and s.get("time") == e.get("time"):
                warnings.append(f"{where}: start and end are the same time — "
                                "read as a 24-hour window")
        for exc in sch.get("exceptions") or []:
            try:
                date.fromisoformat(str(exc.get("date")))
            except ValueError:
                errors.append(f"{sname}: exception date must be YYYY-MM-DD")
            if exc.get("play"):
                check_play(f"{sname} exception {exc.get('date')}", exc["play"])
            if exc.get("start"):
                check_ref(f"{sname} exception {exc.get('date')} start", exc["start"])
            if exc.get("end"):
                check_ref(f"{sname} exception {exc.get('date')} end", exc["end"], True)
    if uses_sun and (loc.get("lat") is None or loc.get("lon") is None):
        errors.append("sunrise/sunset times need the location: set latitude and "
                      "longitude in Settings → Schedule")
    if not errors and tz is not None:
        try:
            today = datetime.now(UTC).astimezone(tz).date()
            warnings.extend(preview(doc, today, 7)["warnings"])
        except ScheduleError as exc:
            errors.append(str(exc))
    return errors, warnings


def to_json(obj):
    """Datetimes → ISO strings, tuples → lists, recursively."""
    if isinstance(obj, datetime):
        return obj.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    return obj
