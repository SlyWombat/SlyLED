#!/usr/bin/env python3
"""schedule_eval — the pure "what should be playing now" function (#954).

Fake ``now`` throughout (America/Toronto): weekly match, cross-midnight window
still open at 00:30, season wrap, priority / specificity / tie-break order with
reasons, exceptions (patch and standalone), quiet hours, spring-forward and
fall-back, sunset offsets, next-change correctness, resume position, idle
kinds, preview segments, validation errors and warnings.

Run: python3 tests/test_schedule_eval.py
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import schedule_eval as se  # noqa: E402
import solar  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

TZ = ZoneInfo("America/Toronto")
UTC = timezone.utc

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def loc(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


ALL = list(se.DAYS)


def doc(*schedules, idle=None, quiet=None):
    return {"enabled": True,
            "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
            "idle": idle or {"kind": "timeline", "timelineId": 99},
            "quietHours": quiet, "schedules": list(schedules)}


def sch(sid, name, prio, entries, season=None, exceptions=None):
    return {"id": sid, "name": name, "priority": prio, "season": season,
            "entries": entries, "exceptions": exceptions or []}


def ent(eid, name, start, end, play, days=ALL, **kw):
    e = {"id": eid, "name": name, "days": days, "start": start, "end": end, "play": play}
    e.update(kw)
    return e


def clock(t):
    return {"ref": "clock", "time": t}


TL = lambda tid: {"kind": "timeline", "timelineId": tid, "loop": True}  # noqa: E731


def main():
    print("Weekly clock window")
    d = doc(sch(1, "Weeknights", 0, [ent(1, "Evening", clock("18:00"), clock("22:00"), TL(3),
                                           days=["mon", "tue", "wed", "thu", "fri"])]))
    r = se.evaluate(d, at("2026-10-07 19:00"))   # Wednesday
    ok("Wednesday 19:00 plays the entry", r["source"] == "entry" and r["play"]["timelineId"] == 3, r["reason"])
    ok("position is 1 h in", abs(r["elapsedS"] - 3600) < 1, r["elapsedS"])
    r = se.evaluate(d, at("2026-10-10 19:00"))   # Saturday
    ok("Saturday falls to idle = wash", r["source"] == "idle" and r["play"]["timelineId"] == 99, r["reason"])
    r = se.evaluate(d, at("2026-10-07 17:00"))
    ok("next change is 18:00 today", loc(r["next"]["atUtc"]) == "2026-10-07 18:00", r["next"])
    ok("…to the entry", r["next"]["what"] == "Weeknights › Evening")
    r = se.evaluate(d, at("2026-10-07 22:00"))
    ok("end is exclusive (22:00 → idle)", r["source"] == "idle")

    print("Cross-midnight window")
    d = doc(sch(1, "Late", 0, [ent(1, "Night", clock("22:00"), clock("02:00"), TL(4), days=["fri"])]))
    r = se.evaluate(d, at("2026-10-10 00:30"))   # Saturday 00:30, window started Friday
    ok("still open at 00:30 the next day", r["source"] == "entry" and r["play"]["timelineId"] == 4, r["reason"])
    ok("position counts from Friday 22:00", abs(r["elapsedS"] - 2.5 * 3600) < 1, r["elapsedS"])
    r = se.evaluate(d, at("2026-10-10 02:00"))
    ok("closed at 02:00", r["source"] == "idle")

    print("Season wrap (11-20 → 01-06)")
    d = doc(sch(1, "Xmas", 10, [ent(1, "Show", clock("17:00"), clock("23:00"), TL(5))],
                season={"from": "11-20", "to": "01-06"}))
    ok("Nov 19 out of season", se.evaluate(d, at("2026-11-19 18:00"))["source"] == "idle")
    ok("Nov 20 in season", se.evaluate(d, at("2026-11-20 18:00"))["source"] == "entry")
    ok("Jan 6 in season", se.evaluate(d, at("2027-01-06 18:00"))["source"] == "entry")
    ok("Jan 7 out", se.evaluate(d, at("2027-01-07 18:00"))["source"] == "idle")
    d1 = doc(sch(1, "One", 10, [ent(1, "Show", clock("17:00"), clock("23:00"), TL(5))],
                 season={"from": "2026-12-01", "to": "2026-12-03"}))
    ok("one-off season: Dec 2 2026 in", se.evaluate(d1, at("2026-12-02 18:00"))["source"] == "entry")
    ok("one-off season: Dec 2 2027 out", se.evaluate(d1, at("2027-12-02 18:00"))["source"] == "idle")

    print("Priority, specificity, tie-breaks — with reasons")
    base = sch(2, "Default", 0, [ent(1, "Wash", clock("17:00"), clock("23:00"), TL(12))])
    hi = sch(1, "Christmas", 20, [ent(1, "Evening", clock("18:00"), clock("23:00"), TL(3))])
    r = se.evaluate(doc(hi, base), at("2026-12-01 19:00"))
    ok("higher priority wins", r["entry"]["scheduleId"] == 1, r["reason"])
    ok("reason names the loser and why", "wins over Default › Wash (priority 20 > 0)" in r["reason"], r["reason"])
    ok("both are candidates", len(r["candidates"]) == 2)
    seasonal = sch(3, "Seasonal", 0, [ent(1, "S", clock("17:00"), clock("23:00"), TL(7))],
                   season={"from": "11-01", "to": "12-31"})
    r = se.evaluate(doc(base, seasonal), at("2026-12-01 19:00"))
    ok("same priority: seasonal beats no season", r["entry"]["scheduleId"] == 3, r["reason"])
    later = sch(4, "Later", 0, [ent(1, "L", clock("18:30"), clock("23:00"), TL(8))])
    r = se.evaluate(doc(base, later), at("2026-12-01 19:00"))
    ok("same priority + specificity: later start wins", r["entry"]["scheduleId"] == 4, r["reason"])
    twin = sch(5, "Twin", 0, [ent(1, "T", clock("17:00"), clock("23:00"), TL(9))])
    r = se.evaluate(doc(twin, base), at("2026-12-01 19:00"))
    ok("full tie: lower schedule id wins", r["entry"]["scheduleId"] == 2, r["reason"])

    print("Exceptions")
    xmas = sch(1, "Xmas", 10, [ent(1, "Evening", clock("17:00"), clock("23:00"), TL(3))],
               exceptions=[{"date": "2026-12-24", "entryId": 1, "end": clock("01:00")},
                           {"date": "2026-12-31", "play": {"kind": "off"}, "priority": 90,
                            "name": "NYE dark"}])
    r = se.evaluate(doc(xmas), at("2026-12-25 00:30"))
    ok("Dec 24 patch extends the window past midnight", r["source"] == "entry", r["reason"])
    r = se.evaluate(doc(xmas), at("2026-12-25 23:30"))
    ok("Dec 25 back to 23:00", r["source"] == "idle")
    r = se.evaluate(doc(xmas), at("2026-12-31 19:00"))
    ok("NYE standalone exception: off wins", r["play"] == {"kind": "off"}, r["reason"])
    ok("…by priority", "NYE dark" in r["reason"] and "priority 90 > 10" in r["reason"], r["reason"])

    print("Quiet hours force idle")
    d = doc(sch(1, "Late", 0, [ent(1, "Night", clock("22:00"), clock("02:00"), TL(4))]),
            quiet={"start": "23:30", "end": "06:00"})
    r = se.evaluate(d, at("2026-10-07 23:45"))
    ok("23:45 is quiet → idle", r["source"] == "quiet" and r["play"]["timelineId"] == 99, r["reason"])
    ok("reason says so", "quiet hours 23:30–06:00" in r["reason"], r["reason"])
    r = se.evaluate(d, at("2026-10-07 23:00"))
    ok("23:00 plays the entry", r["source"] == "entry")
    ok("next change is quiet hours at 23:30", loc(r["next"]["atUtc"]) == "2026-10-07 23:30", r["next"])

    print("DST — spring forward (2026-03-08) and fall back (2026-11-01)")
    d = doc(sch(1, "Odd", 0, [ent(1, "Early", clock("02:30"), clock("04:00"), TL(6))]))
    r = se.evaluate(d, at("2026-03-08 03:45"))
    ok("02:30 doesn't exist: entry is playing at 03:45 EDT", r["source"] == "entry", r["reason"])
    ok("…it started at 03:30 EDT", loc(r["window"]["startUtc"]) == "2026-03-08 03:30",
       loc(r["window"]["startUtc"]))
    ok("…and a note says so", any("does not exist" in n and "03:30" in n for n in r["notes"]), r["notes"])
    d = doc(sch(1, "Amb", 0, [ent(1, "Ambig", clock("01:30"), clock("03:00"), TL(6))]))
    r = se.evaluate(d, datetime(2026, 11, 1, 5, 45, tzinfo=UTC))   # 01:45 EDT (first pass)
    ok("fall back: 01:30 takes the first occurrence (EDT)",
       r["source"] == "entry" and r["window"]["startUtc"] == datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
       r["window"])
    d = doc(sch(1, "Eve", 0, [ent(1, "E", clock("20:00"), clock("23:00"), TL(6))]))
    r = se.evaluate(d, at("2026-11-01 21:00"))
    ok("a 20:00-23:00 window is 3 h on the fall-back night",
       (r["window"]["endUtc"] - r["window"]["startUtc"]) == timedelta(hours=3))

    print("Sunset with offsets")
    d = doc(sch(1, "Xmas", 20, [ent(1, "Evening", {"ref": "sunset", "offsetMin": -15},
                                    clock("23:00"), TL(3))]))
    sunset = solar.sun_times(date(2026, 12, 1), 43.6532, -79.3832)["sunset"]
    r = se.evaluate(d, at("2026-12-01 12:00"))
    ok("next start = sunset − 15 min",
       abs((r["next"]["atUtc"] - (sunset - timedelta(minutes=15))).total_seconds()) < 1, r["next"])
    r = se.evaluate(d, (sunset - timedelta(minutes=14)).astimezone(TZ))
    ok("playing one minute after sunset − 15", r["source"] == "entry", r["reason"])
    ok("reason reads 'sunset −15 m → 23:00'", "sunset −15 m → 23:00" in r["reason"], r["reason"])
    d = doc(sch(1, "N", 0, [ent(1, "Night", {"ref": "sunset"}, {"ref": "sunrise", "offsetMin": 30}, TL(12))]))
    r = se.evaluate(d, at("2026-12-02 03:00"))
    ok("sunset → sunrise+30 still open at 03:00", r["source"] == "entry", r["reason"])
    d = doc(sch(1, "D", 0, [ent(1, "Dur", clock("18:00"), {"durationMin": 90}, TL(2))]))
    r = se.evaluate(d, at("2026-10-07 19:20"))
    ok("end = start + 90 min: open at 19:20", r["source"] == "entry")
    ok("…closes 19:30", loc(r["next"]["atUtc"]) == "2026-10-07 19:30", r["next"])

    print("resume: start, and idle kinds")
    d = doc(sch(1, "W", 0, [ent(1, "E", clock("18:00"), clock("22:00"), TL(3), resume="start")]))
    ok("resume: start → position 0", se.evaluate(d, at("2026-10-07 19:00"))["elapsedS"] == 0)
    ok("idle hold", se.evaluate(doc(idle={"kind": "hold"}), at("2026-10-07 12:00"))["play"] == {"kind": "hold"})
    ok("idle off", se.evaluate(doc(idle={"kind": "off"}), at("2026-10-07 12:00"))["play"] == {"kind": "off"})
    ok("idle wash without a timeline chosen holds",
       se.evaluate(doc(idle={"kind": "timeline", "timelineId": None}), at("2026-10-07 12:00"))["play"]
       == {"kind": "hold"})

    print("Preview — segments for the week grid")
    d = doc(hi, base)
    p = se.preview(d, date(2026, 12, 1), 1)
    whats = [s["what"] for s in p["segments"]]
    ok("idle → Default wash → Christmas → idle",
       whats == ["idle", "Default › Wash", "Christmas › Evening", "idle"], whats)
    ok("Christmas segment lists Default as a loser",
       p["segments"][2]["losers"] == ["Default › Wash"], p["segments"][2])
    ok("sun times per day", len(p["sun"]) == 1 and p["sun"][0]["sunset"] is not None)

    print("Validation")
    errs, warns = se.validate(doc(sch(1, "X", 0, [ent(1, "E", {"ref": "sunset"}, clock("23:00"), TL(3))])) |
                              {"location": {"tz": "America/Toronto"}}, [3, 99])
    ok("sun ref without coordinates is an error", any("location" in e for e in errs), errs)
    errs, warns = se.validate(doc(sch(1, "X", 0, [ent(1, "E", clock("18:00"), clock("23:00"), TL(42))])), [3, 99])
    ok("unknown timeline is an error", any("42" in e for e in errs), errs)
    errs, warns = se.validate(doc(sch(1, "X", 0, [ent(1, "E", clock("25:00"), clock("23:00"), TL(3))])), [3, 99])
    ok("bad time is an error", errs, errs)
    errs, warns = se.validate(doc(sch(1, "X", 0, [ent(1, "E", clock("18:00"), clock("23:00"), TL(3), days=[])])), [3, 99])
    ok("no days is a warning", not errs and any("never plays" in w for w in warns), (errs, warns))
    errs, warns = se.validate(doc(idle={"kind": "off"}), [99])
    ok("idle off is a warning", not errs and any("dark" in w for w in warns), (errs, warns))
    errs, warns = se.validate(doc(sch(1, "A", 0, [ent(1, "E", clock("18:00"), clock("23:00"), TL(3))]),
                                  sch(2, "B", 0, [ent(1, "F", clock("20:00"), clock("21:00"), TL(3))])), [3, 99])
    ok("equal-priority overlap is a warning naming the winner",
       any("overlap" in w and "wins" in w for w in warns), warns)
    errs, _ = se.validate(dict(doc(), location={"lat": 43.6, "lon": -79.4, "tz": "Mars/Base"}), [99])
    ok("unknown time zone is an error", any("time zone" in e for e in errs), errs)
    ok("default document is disabled with Toronto tz and wash idle",
       se.default_document()["enabled"] is False
       and se.default_document()["location"]["tz"] == "America/Toronto"
       and se.default_document()["idle"]["kind"] == "timeline")

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
