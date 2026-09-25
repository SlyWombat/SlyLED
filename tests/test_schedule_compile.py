#!/usr/bin/env python3
"""Compile the show schedule for a HinksPix (#954 Phase 2).

schedule_compile.compile_week → per-weekday rows + playlists; the rows go
through hinkspix_files.schedule_text (per-row playlist) and the bytes are
pinned for a golden week. Overnight windows land as two rows on consecutive
weekdays; the idle wash and the show are different .ply files; entries not
marked for offline, 'off' windows and pixel-less timelines are left out with
warnings; fades / hold / out-of-week exceptions / over-long sequences warn.

Run: python3 tests/test_schedule_compile.py
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_files as hf  # noqa: E402
import schedule_compile as sc  # noqa: E402
import schedule_eval as se  # noqa: E402

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


ALL = list(se.DAYS)
NAMES = {3: "Evening show", 5: "Finale", 12: "Wash", 40: "Garage only", 50: "Epic"}


def doc(**kw):
    d = {"enabled": True,
         "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
         "idle": {"kind": "timeline", "timelineId": 12},
         "hinkspix": {"handoff": "manual", "compileIdle": True},
         "schedules": [{"id": 1, "name": "Christmas", "priority": 20, "entries": [
             {"id": 1, "name": "Evening", "days": ALL,
              "start": {"ref": "clock", "time": "18:00"},
              "end": {"ref": "clock", "time": "01:00"},
              "play": {"kind": "playlist", "order": [3, 5], "loop": True},
              "hinkspix": {"compile": True}},
             {"id": 2, "name": "Dark", "days": ALL,
              "start": {"ref": "clock", "time": "01:00"},
              "end": {"ref": "clock", "time": "06:00"},
              "play": {"kind": "off"}, "hinkspix": {"compile": True}}]}]}
    d.update(kw)
    return d


def ok_tl(tid):
    return tid != 40


def main():
    first = date(2026, 12, 7)          # a Monday
    r = sc.compile_week(doc(), first, ok_tl, NAMES.get, lambda t: 300)
    print("Rows per weekday")
    mon = r["days"]["MONDAY"]
    ok("Monday: wash 06:00-18:00 then Evening 18:00-23:59",
       [(x["start"], x["end"], x["playlist"]) for x in mon if x["start"] >= "06:00"]
       == [("06:00", "18:00", "WASH"), ("18:00", "23:59", "EVENING")], mon)
    tue = r["days"]["TUESDAY"]
    ok("overnight 18:00-01:00 continues on Tuesday 00:00-01:00",
       ("00:00", "01:00", "EVENING") in [(x["start"], x["end"], x["playlist"]) for x in tue], tue)
    ok("'off' 01:00-06:00 is not a row (dark offline)",
       not any(x["start"] == "01:00" for x in tue), tue)
    ok("Sunday has the first half of Sunday night's window",
       ("18:00", "23:59", "EVENING") in [(x["start"], x["end"], x["playlist"]) for x in r["days"]["SUNDAY"]])
    ok("rows sorted by start", all(r["days"][d] == sorted(r["days"][d], key=lambda x: x["start"]) for d in hf.DAYS))
    print("Playlists")
    ok("show and wash are separate playlists",
       r["playlists"] == {"EVENING": [3, 5], "WASH": [12]}, r["playlists"])
    ok("horizon is the 7 days", (r["from"], r["to"]) == ("2026-12-07", "2026-12-13"), (r["from"], r["to"]))

    print("schedule_text with a per-row playlist — golden bytes")
    txt = hf.schedule_text(r["days"]["MONDAY"], "WASH")
    ok("Monday bytes",
       txt == '[{"S":"0000","E":"0100","P":"EVENING.ply","Q":1},'
              '{"S":"0600","E":"1800","P":"WASH.ply","Q":1},'
              '{"S":"1800","E":"2359","P":"EVENING.ply","Q":1}]', txt)
    ok("rows without a playlist still use the default",
       hf.schedule_text([{"start": "18:00", "end": "19:00"}], "SHOW")
       == '[{"S":"1800","E":"1900","P":"SHOW.ply","Q":0}]')

    print("What doesn't compile")
    d2 = doc()
    d2["schedules"][0]["entries"][0]["hinkspix"] = {"compile": False}
    r2 = sc.compile_week(d2, first, ok_tl, NAMES.get, lambda t: 300)
    ok("an entry not marked for offline is left out",
       "EVENING" not in r2["playlists"] and all(x["playlist"] == "WASH"
                                                for d in hf.DAYS for x in r2["days"][d]), r2["playlists"])
    d3 = doc(idle={"kind": "timeline", "timelineId": 40})
    r3 = sc.compile_week(d3, first, ok_tl, NAMES.get, lambda t: 300)
    ok("a wash with no pixels on this controller is left out, with a warning",
       "WASH" not in r3["playlists"] and any("Garage only" in w for w in r3["warnings"]), r3["warnings"])
    d4 = doc(idle={"kind": "hold"})
    ok("idle hold warns", any("hold" in w for w in sc.compile_week(d4, first, ok_tl, NAMES.get)["warnings"]))
    d5 = doc()
    d5["schedules"][0]["entries"][0]["transition"] = {"fadeInS": 2}
    ok("fades warn", any("fades" in w for w in sc.compile_week(d5, first, ok_tl, NAMES.get)["warnings"]))
    d6 = doc()
    d6["schedules"][0]["exceptions"] = [{"date": "2026-12-24", "entryId": 1, "end": {"ref": "clock", "time": "02:00"}}]
    ok("exception beyond the week warns",
       any("2026-12-24" in w for w in sc.compile_week(d6, first, ok_tl, NAMES.get)["warnings"]))
    r7 = sc.compile_week(doc(), first, ok_tl, NAMES.get, lambda t: 3600)
    ok("an over-long sequence warns", any("65535" in w for w in r7["warnings"]), r7["warnings"])
    d8 = doc()
    d8["hinkspix"]["compileIdle"] = False
    ok("compileIdle off leaves the wash out", "WASH" not in sc.compile_week(d8, first, ok_tl, NAMES.get)["playlists"])

    print("Sunset rows resolve per date")
    d9 = doc()
    d9["schedules"][0]["entries"][0]["start"] = {"ref": "sunset", "offsetMin": -15}
    r9 = sc.compile_week(d9, first, ok_tl, NAMES.get)
    starts = [x["start"] for x in r9["days"]["MONDAY"] if x["playlist"] == "EVENING" and x["start"] != "00:00"]
    ok("Monday Dec 7 starts at sunset − 15 (~16:26)", starts and "16:20" <= starts[0] <= "16:32", starts)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
