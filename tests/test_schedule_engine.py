#!/usr/bin/env python3
"""show_scheduler.ShowScheduler — the engine, with a fake clock and fake
actions (#954).

Transition sequence and reasons, restart mid-window → play(position>0),
a missed window is skipped (no catch-up), manual override blocks,
auto-resume at the next window edge, explicit resume, kill switch,
entry ↔ idle on the same content doesn't restart it, clock jump logged,
state view shape.

Run: python3 tests/test_schedule_engine.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import schedule_eval as se  # noqa: E402
import show_scheduler as ss  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

TZ = ZoneInfo("America/Toronto")

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


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Actions:
    def __init__(self):
        self.calls = []

    def play(self, play, pos, decision):
        self.calls.append(("play", play.get("timelineId") or tuple(play.get("order") or ()), round(pos)))

    def idle(self, play, decision):
        self.calls.append(("idle", play.get("kind"), play.get("timelineId")))

    def off(self, decision):
        self.calls.append(("off",))


def make_doc(enabled=True, idle_tl=12):
    return {"enabled": enabled,
            "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
            "idle": {"kind": "timeline", "timelineId": idle_tl},
            "overridePolicy": {"autoResumeAtBoundary": True},
            "schedules": [{"id": 1, "name": "Eaves", "priority": 10, "entries": [
                {"id": 1, "name": "Evening", "days": list(se.DAYS),
                 "start": {"ref": "clock", "time": "18:00"},
                 "end": {"ref": "clock", "time": "23:00"},
                 "play": {"kind": "timeline", "timelineId": 3, "loop": True}},
                {"id": 2, "name": "Late wash", "days": list(se.DAYS),
                 "start": {"ref": "clock", "time": "23:00"},
                 "end": {"ref": "clock", "time": "23:30"},
                 "play": {"kind": "timeline", "timelineId": 12, "loop": True}},
                {"id": 3, "name": "Dark", "days": list(se.DAYS),
                 "start": {"ref": "clock", "time": "23:30"},
                 "end": {"ref": "clock", "time": "06:00"},
                 "play": {"kind": "off"}}]}]}


def main():
    print("Transitions follow the schedule, with reasons")
    doc = make_doc()
    clk = Clock(ts("2026-10-07 17:00"))
    act = Actions()
    state = {}
    eng = ss.ShowScheduler(lambda: doc, act, state=state, clock=clk)
    wait = eng.tick()
    ok("17:00 → idle wash", act.calls == [("idle", "timeline", 12)], act.calls)
    ok("sleeps until the 18:00 edge", 3590 <= wait <= 3610, wait)
    clk.t = ts("2026-10-07 18:00") + 1
    eng.tick()
    ok("18:00 → play timeline 3 from ~0", act.calls[-1] == ("play", 3, 1), act.calls)
    ok("log line explains why",
       any("-> Eaves › Evening because" in e["msg"] for e in eng.log_ring), [e["msg"] for e in eng.log_ring])
    n = len(act.calls)
    clk.t += 30
    eng.tick()
    ok("no re-trigger inside the same window", len(act.calls) == n)
    clk.t = ts("2026-10-07 23:00") + 1
    eng.tick()
    ok("23:00 → Late wash (entry) plays timeline 12", act.calls[-1] == ("play", 12, 1), act.calls[-1])
    clk.t = ts("2026-10-07 23:30") + 1
    eng.tick()
    ok("23:30 → off", act.calls[-1] == ("off",), act.calls[-1])
    clk.t = ts("2026-10-08 06:00") + 1
    eng.tick()
    ok("06:00 → idle wash", act.calls[-1] == ("idle", "timeline", 12), act.calls[-1])

    print("Entry ↔ idle on the same content is not restarted")
    act2 = Actions()
    d2 = make_doc()
    d2["schedules"][0]["entries"] = [d2["schedules"][0]["entries"][1]]   # only "Late wash" (tl 12)
    clk2 = Clock(ts("2026-10-07 22:59"))
    e2 = ss.ShowScheduler(lambda: d2, act2, clock=clk2)
    e2.tick()
    clk2.t = ts("2026-10-07 23:00") + 1
    e2.tick()
    clk2.t = ts("2026-10-07 23:30") + 1
    e2.tick()
    ok("idle wash → entry wash → idle wash: started once", act2.calls == [("idle", "timeline", 12)], act2.calls)

    print("Restart mid-window joins in position; a missed window isn't replayed")
    act3 = Actions()
    clk3 = Clock(ts("2026-10-07 20:30"))
    e3 = ss.ShowScheduler(lambda: doc, act3, clock=clk3)
    e3.tick()
    ok("restart at 20:30 plays tl 3 at 2.5 h", act3.calls == [("play", 3, 9000)], act3.calls)
    act4 = Actions()
    clk4 = Clock(ts("2026-10-07 23:10"))
    e4 = ss.ShowScheduler(lambda: doc, act4, clock=clk4)
    e4.tick()
    ok("restart after the 18-23 window: no catch-up, current entry only",
       act4.calls == [("play", 12, 600)], act4.calls)

    print("Manual override: passive until resume or the next window edge")
    act5 = Actions()
    saved = []
    clk5 = Clock(ts("2026-10-07 19:00"))
    st5 = {}
    e5 = ss.ShowScheduler(lambda: doc, act5, state=st5, save_state=lambda s: saved.append(dict(s)),
                          clock=clk5)
    e5.tick()
    e5.set_override("manual", by="192.168.10.44")
    ok("override persisted", saved and saved[-1]["override"]["active"])
    n = len(act5.calls)
    clk5.t += 600
    e5.tick()
    ok("no output while overridden", len(act5.calls) == n, act5.calls)
    ok("resumeAt is the next window edge (23:00)",
       abs(st5["override"]["resumeAt"] - ts("2026-10-07 23:00")) < 1, st5["override"])
    clk5.t = ts("2026-10-07 23:00") + 1
    e5.tick()
    ok("auto-resume at the edge applies the schedule", act5.calls[-1] == ("play", 12, 1), act5.calls[-1])
    ok("override cleared", st5["override"] is None)
    e5.set_override("manual")
    e5.resume(by="phone")
    e5.tick()
    ok("explicit resume re-applies what should play (even the same content)",
       act5.calls[-1] == ("play", 12, 1), act5.calls[-1])
    doc_na = make_doc()
    doc_na["overridePolicy"] = {"autoResumeAtBoundary": False}
    act6 = Actions()
    clk6 = Clock(ts("2026-10-07 19:00"))
    e6 = ss.ShowScheduler(lambda: doc_na, act6, clock=clk6)
    e6.tick()
    e6.set_override("manual")
    clk6.t = ts("2026-10-07 23:00") + 1
    e6.tick()
    ok("auto-resume off: stays paused past the edge", len(act6.calls) == 1, act6.calls)

    print("Kill switch")
    act7 = Actions()
    off_doc = make_doc(enabled=False)
    e7 = ss.ShowScheduler(lambda: off_doc, act7, clock=Clock(ts("2026-10-07 19:00")))
    e7.tick()
    ok("enabled:false never touches output", act7.calls == [], act7.calls)

    print("Clock jump")
    act8 = Actions()
    clk8 = Clock(ts("2026-10-07 17:00"))
    e8 = ss.ShowScheduler(lambda: doc, act8, clock=clk8)
    e8.tick()
    clk8.t += 3600 * 3
    e8.tick()
    ok("jump logged", any("clock jumped" in e["msg"] for e in e8.log_ring))
    ok("…and re-evaluated into the window", act8.calls[-1] == ("play", 3, 7200), act8.calls[-1])

    print("State view")
    sv = e8.state_view(doc)
    ok("state has now/next/clock/override", all(k in sv for k in ("now", "next", "clock", "override")), list(sv))
    ok("now.positionS matches wall clock", abs(sv["now"]["positionS"] - 7200) < 1, sv["now"]["positionS"])
    ok("next is 23:00 Late wash", sv["next"]["what"] == "Eaves › Late wash", sv["next"])
    ok("clock reports tz and DST", sv["clock"]["tz"] == "America/Toronto" and sv["clock"]["isDst"] is True)
    ok("JSON-able after to_json", isinstance(se.to_json(sv)["clock"]["now"], str))

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
