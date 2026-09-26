#!/usr/bin/env python3
"""HinksPix offline/standalone only for shows entirely on that controller (#963).

schedule_compile.offline_check is the one rule: a timeline plays offline on a
controller only when every output fixture its tracks drive is one of that
controller's pixel fixtures. Mixed shows (DMX, performers, another
controller) are refused with a plain reason — the compile drops the whole
entry, the standalone deploy refuses the sequence — never compiled partially.

Part 1 is the pure rule; part 2 drives the real routes (no device traffic).

Run: python3 tests/test_963_offline_eligibility.py
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import os
import sys
from datetime import date

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


# ── Part 1: the rule ────────────────────────────────────────────────────────
HP, HP2, PERF = 7, 8, 9
CHILDREN = [{"id": HP, "type": "hinkspix", "name": "Kazoo"},
            {"id": HP2, "type": "hinkspix", "name": "Garage"},
            {"id": PERF, "type": "slyled", "name": "Porch tree"}]
EAVES = {"id": 1, "name": "Eaves", "fixtureType": "led", "type": "linear", "childId": HP,
         "strings": [{"port": 1, "leds": 50}]}
ARCH = {"id": 2, "name": "Arch", "fixtureType": "led", "type": "linear", "childId": HP,
        "strings": [{"port": 2, "leds": 30}]}
PLACEHOLDER = {"id": 3, "name": "Kazoo", "fixtureType": "led", "type": "linear", "childId": HP}
GARAGE = {"id": 4, "name": "Garage run", "fixtureType": "led", "type": "linear", "childId": HP2,
          "strings": [{"port": 1, "leds": 40}]}
PORCH_DMX = {"id": 5, "name": "Porch DMX", "fixtureType": "dmx"}
TREE = {"id": 6, "name": "Tree", "fixtureType": "led", "type": "linear", "childId": PERF}
CAM = {"id": 10, "name": "Cam", "fixtureType": "camera"}
GROUP = {"id": 11, "name": "Roofline", "type": "group", "childIds": [1, 2]}


def tl(name, *tracks):
    return {"id": 99, "name": name, "tracks": list(tracks)}


def track(fid):
    return {"fixtureId": fid, "clips": [{"actionId": 1, "startS": 0, "durationS": 5}]}


STAGE = {"allPerformers": True, "clips": [{"actionId": 1, "startS": 0, "durationS": 5}]}


def part1():
    print("The rule")
    hp_only = [EAVES, ARCH, PLACEHOLDER, CAM, GROUP]
    mixed = hp_only + [GARAGE, PORCH_DMX, TREE]

    r = sc.offline_check(tl("Roof", track(1), track(2)), HP, mixed, CHILDREN)
    ok("pure HinksPix show → eligible", r == {"eligible": True, "uses": True, "reason": None}, r)
    r = sc.offline_check(tl("Roof", track(11)), HP, mixed, CHILDREN)
    ok("a group of this controller's fixtures → eligible", r["eligible"], r)

    r = sc.offline_check(tl("Christmas show", track(1), track(5)), HP, mixed, CHILDREN)
    ok("mixed with a DMX fixture → refused", not r["eligible"] and r["uses"], r)
    ok("…the reason names the DMX fixture and says SlyLED must run",
       "DMX fixture 'Porch DMX'" in r["reason"] and "needs SlyLED running" in r["reason"]
       and "Kazoo" in r["reason"] and "'Christmas show'" in r["reason"], r["reason"])

    r = sc.offline_check(tl("Christmas show", track(1), track(5), track(6)), HP, mixed, CHILDREN)
    ok("DMX + a performer → one reason naming both",
       "DMX fixture 'Porch DMX' and 1 performer" in (r["reason"] or ""), r["reason"])
    r = sc.offline_check(tl("Both", track(1), track(4)), HP, mixed, CHILDREN)
    ok("pixels on another controller → refused, naming it",
       not r["eligible"] and "pixels on 'Garage'" in r["reason"], r["reason"])

    r = sc.offline_check(tl("Garage only", track(4)), HP, mixed, CHILDREN)
    ok("a show that doesn't touch this controller → not used, not eligible",
       r["uses"] is False and r["eligible"] is False, r)

    r = sc.offline_check(tl("Everything", STAGE), HP, hp_only, CHILDREN)
    ok("All Fixtures (Stage) in a HinksPix-only rig → eligible "
       "(camera, group and empty placeholder don't count)", r["eligible"], r)
    r = sc.offline_check(tl("Everything", STAGE), HP, mixed, CHILDREN)
    ok("All Fixtures (Stage) in a mixed rig → refused", not r["eligible"] and r["uses"], r)

    r = sc.offline_check(tl("Empty", {"fixtureId": 1, "clips": []}), HP, mixed, CHILDREN)
    ok("a track with no clips drives nothing", r["uses"] is False, r)

    s = sc.offline_summary(tl("Roof", track(1)), mixed, CHILDREN)
    ok("summary: eligible on Kazoo", s == {"eligible": True, "controllerId": HP, "reason": None}, s)
    s = sc.offline_summary(tl("Christmas show", track(1), track(5)), mixed, CHILDREN)
    ok("summary: mixed → the reason", not s["eligible"] and "needs SlyLED running" in s["reason"], s)
    s = sc.offline_summary(tl("DMX only", track(5)), mixed, CHILDREN)
    ok("summary: no HinksPix pixels at all → says so",
       not s["eligible"] and s["controllerId"] is None and "any HinksPix pixels" in s["reason"], s)

    print("The compile refuses whole entries")
    doc = {"enabled": True,
           "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
           "idle": {"kind": "off"},
           "hinkspix": {"handoff": "manual", "compileIdle": True},
           "schedules": [{"id": 1, "name": "Xmas", "priority": 10, "entries": [
               {"id": 1, "name": "Evening", "days": list(se.DAYS),
                "start": {"ref": "clock", "time": "18:00"}, "end": {"ref": "clock", "time": "22:00"},
                "play": {"kind": "playlist", "order": [3, 5], "loop": True},
                "hinkspix": {"compile": True}}]}]}
    pure = {"eligible": True, "uses": True, "reason": None}
    mix = {"eligible": False, "uses": True, "reason": "'Finale' also drives DMX fixture 'Porch DMX' — "
                                                      "it needs SlyLED running, so it can't be scheduled offline on Kazoo"}
    res = sc.compile_week(doc, date(2026, 12, 7), lambda t: pure if t == 3 else mix)
    ok("a playlist with one mixed show is not compiled at all (no partial playlist)",
       res["playlists"] == {} and not any(res["days"].values()), res["playlists"])
    ok("…refused with the entry and the reason",
       res["refused"] == [{"entry": "Evening", "reason": mix["reason"]}], res["refused"])
    ok("…and a warning an operator can read",
       any(w.startswith("Evening: not scheduled offline") and "Porch DMX" in w for w in res["warnings"]),
       res["warnings"])
    res = sc.compile_week(doc, date(2026, 12, 7), lambda t: pure)
    ok("the same entry, all eligible → compiled", res["playlists"] == {"EVENING": [3, 5]}
       and res["refused"] == [], res["playlists"])
    res = sc.compile_week(doc, date(2026, 12, 7), lambda t: True)
    ok("a plain-bool timeline_ok still works", res["playlists"] == {"EVENING": [3, 5]}, res["playlists"])


# ── Part 2: the routes ──────────────────────────────────────────────────────
def part2():
    import parent_server as ps
    import orch_hinkspix

    c = ps.app.test_client()
    CID, PID = 4401, 4402
    ps._children[:] = [x for x in ps._children if x["id"] not in (CID, PID)] + [
        {"id": CID, "type": "hinkspix", "ip": "192.0.2.61", "name": "Kazoo", "status": 1,
         "strings": [], "hinks": {"model": "HinksPix PRO", "mcpu": 160, "uploadSupported": True,
                                  "ports": [{"port": 1, "leds": 50, "enabled": True}]}},
        {"id": PID, "type": "slyled", "ip": "192.0.2.62", "name": "Porch tree", "status": 1,
         "strings": [{"leds": 30}]}]
    child = next(x for x in ps._children if x["id"] == CID)
    child["hinks"]["configHash"] = orch_hinkspix._config_hash(child["hinks"])
    eaves = c.post("/api/fixtures", json={"name": "Eaves", "fixtureType": "led", "type": "linear",
                                          "childId": CID, "strings": [{"port": 1, "leds": 50, "mm": 200}]}
                   ).get_json()["id"]
    porch = c.post("/api/fixtures", json={"name": "Porch DMX", "fixtureType": "dmx", "type": "point",
                                          "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 3,
                                          "dmxProfileId": "generic-rgb"}).get_json()["id"]
    aid = c.post("/api/actions", json={"name": "Red", "type": 1, "r": 255}).get_json()["id"]

    def timeline(name, fids):
        tid = c.post("/api/timelines", json={"name": name, "durationS": 10}).get_json()["id"]
        c.put(f"/api/timelines/{tid}", json={"name": name, "durationS": 10, "tracks": [
            {"fixtureId": f, "clips": [{"actionId": aid, "startS": 0, "durationS": 10}]} for f in fids]})
        return tid

    roof = timeline("Roof only", [eaves])
    xmas = timeline("Christmas show", [eaves, porch])

    print("Schedule state tells the SPA")
    st = c.get("/api/schedule/state").get_json()
    el = st.get("offlineEligibility") or {}
    e_roof, e_xmas = el.get(str(roof)) or el.get(roof), el.get(str(xmas)) or el.get(xmas)
    ok("state: the pure show is eligible on the controller",
       e_roof and e_roof["eligible"] and e_roof["controllerId"] == CID, e_roof)
    ok("state: the mixed show is not, with the reason",
       e_xmas and not e_xmas["eligible"] and "Porch DMX" in e_xmas["reason"], e_xmas)

    print("Compile route refuses the mixed entry")
    doc = {"enabled": True, "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
           "idle": {"kind": "off"},
           "hinkspix": {"handoff": "manual", "compileIdle": True, "controllers": [CID]},
           "schedules": [{"id": 1, "name": "Xmas", "priority": 10, "entries": [
               {"id": 1, "name": "Roof", "days": list(se.DAYS),
                "start": {"ref": "clock", "time": "17:00"}, "end": {"ref": "clock", "time": "18:00"},
                "play": {"kind": "timeline", "timelineId": roof, "loop": True},
                "hinkspix": {"compile": True}},
               {"id": 2, "name": "Big show", "days": list(se.DAYS),
                "start": {"ref": "clock", "time": "19:00"}, "end": {"ref": "clock", "time": "21:00"},
                "play": {"kind": "timeline", "timelineId": xmas, "loop": True},
                "hinkspix": {"compile": True}}]}]}
    r = c.put("/api/schedule", json=doc)
    ok("schedule saved", r.status_code == 200, r.get_json())
    r = c.post(f"/api/schedule/compile/hinkspix/{CID}", json={"deploy": False})
    d = (r.get_json() or {}).get("compile") or {}
    ok("compile: the pure entry compiles", d.get("playlists") == {"ROOF": [roof]}, d.get("playlists"))
    ok("compile: the mixed entry is refused with the reason",
       [x["entry"] for x in d.get("refused") or []] == ["Big show"]
       and "needs SlyLED running" in d["refused"][0]["reason"], d.get("refused"))

    print("Standalone deploy refuses the mixed sequence")
    r = c.put(f"/api/hinkspix/{CID}/deploy", json={"items": [{"timelineId": xmas}]})
    ok("PUT a mixed sequence → 400 with the reason",
       r.status_code == 400 and "Porch DMX" in r.get_json()["err"], r.get_json())
    ok("…and it is not stored",
       not any(i["timelineId"] == xmas
               for i in (orch_hinkspix._deploy_cfg().get(str(CID)) or {}).get("items") or []))
    r = c.put(f"/api/hinkspix/{CID}/deploy", json={"items": [{"timelineId": roof}]})
    ok("PUT the pure sequence → 200", r.status_code == 200, r.get_json())
    g = c.get(f"/api/hinkspix/{CID}/deploy").get_json()
    by = {e["timelineId"]: e for e in g.get("eligibility") or []}
    ok("GET deploy lists eligibility for the screen",
       by.get(roof, {}).get("eligible") is True and by.get(xmas, {}).get("eligible") is False, g.get("eligibility"))

    # The stored show is edited to also drive the DMX fixture: deploy refuses.
    c.put(f"/api/timelines/{roof}", json={"name": "Roof only", "durationS": 10, "tracks": [
        {"fixtureId": f, "clips": [{"actionId": aid, "startS": 0, "durationS": 10}]} for f in (eaves, porch)]})
    ps._bake_result[roof] = {"fixtures": {}}
    r = c.post(f"/api/hinkspix/{CID}/deploy")
    ok("POST deploy after the show gained DMX → 409 with the reason",
       r.status_code == 409 and any("needs SlyLED running" in x for x in r.get_json().get("reasons") or []),
       r.get_json())
    ok("…nothing started", not orch_hinkspix._deploy_state.get(CID, {}).get("running"))


def main():
    part1()
    part2()
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
