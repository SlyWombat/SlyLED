#!/usr/bin/env python3
"""Show scheduler — routes and playback integration (#954).

Flask test client under SLYLED_DATA, sACN routed to 127.0.0.1 so nothing
leaves the machine. Covers:

  * GET/PUT /api/schedule (validation errors / warnings), enabled, location,
    state shape, preview, log
  * the engine through the real actions: bake-before-start on unbaked
    timelines (the post-restart state), engine auto-start (#958),
    join-in-progress position, and a show → wash hand-off with NO dark frame
    on the wire (sampled every 5 ms across the transition)
  * a manual /api/show/stop sets the override ("Manual — schedule paused"),
    /api/schedule/resume clears it and the schedule plays again
  * /api/show/status carries the schedule summary (Android)
  * project export / import round-trips the schedule

Run: python3 tests/test_schedule_routes.py
"""

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-sched-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402
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


CID = 9540
LEDS = 12


def pixel0():
    u = ps._sacn.peek_universe(1)
    return tuple(u.get_data()[0:3]) if u is not None else None


def window_doc(show_tid, wash_tid, start_local, end_local, days=None):
    return {"enabled": True,
            "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
            "idle": {"kind": "timeline", "timelineId": wash_tid},
            "overridePolicy": {"autoResumeAtBoundary": True},
            "schedules": [{"id": 1, "name": "Eaves", "priority": 10, "entries": [
                {"id": 1, "name": "Evening", "days": days or ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                 "start": {"ref": "clock", "time": start_local},
                 "end": {"ref": "clock", "time": end_local},
                 "play": {"kind": "timeline", "timelineId": show_tid, "loop": True}}]}]}


def main():
    c = ps.app.test_client()
    # ── rig: a HinksPix port fixture, sACN routed to localhost ─────────────
    ps._children[:] = [x for x in ps._children if x["id"] != CID] + [{
        "id": CID, "type": "hinkspix", "ip": "127.0.0.1", "name": "HP", "sc": 0,
        "strings": [], "status": 1,
        "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402, "mcpu": 160,
                  "uploadSupported": True, "protocol": "e131",
                  "dmxOut": {"enabled": False, "universe": None},
                  "ports": [{"port": 17, "leds": LEDS, "enabled": True}]}}]
    ps._dmx_settings.update(protocol="sacn", bootBlinkFixtures=False,
                            universeRoutes=[{"universe": 1, "destination": "127.0.0.1"}])
    ps._apply_dmx_settings()
    ps._sacn.stop()
    ps._artnet.stop()
    fid = c.post("/api/fixtures", json={"name": "Eaves", "fixtureType": "led", "type": "linear",
                                        "childId": CID,
                                        "strings": [{"port": 17, "leds": LEDS, "mm": 200}]}).get_json()["id"]

    def timeline(name, rgb):
        aid = c.post("/api/actions", json={"name": name, "type": 1, "r": rgb[0],
                                           "g": rgb[1], "b": rgb[2]}).get_json()["id"]
        tid = c.post("/api/timelines", json={"name": name, "durationS": 20, "loop": True}).get_json()["id"]
        c.put(f"/api/timelines/{tid}", json={
            "name": name, "durationS": 20, "loop": True,
            "tracks": [{"fixtureId": fid, "clips": [{"actionId": aid, "startS": 0, "durationS": 20}]}]})
        return tid

    show = timeline("Red show", (255, 0, 0))
    wash = timeline("Blue wash", (0, 0, 255))

    try:
        print("Document routes")
        r = c.get("/api/schedule")
        ok("GET default document", r.status_code == 200 and r.get_json()["enabled"] is False
           and r.get_json()["location"]["tz"] == "America/Toronto", r.get_json())
        bad = window_doc(show, wash, "25:00", "23:00")
        r = c.put("/api/schedule", json=bad)
        ok("PUT with a bad time → 400 with errors", r.status_code == 400 and r.get_json()["errors"], r.get_json())
        bad = window_doc(987654, wash, "18:00", "23:00")
        r = c.put("/api/schedule", json=bad)
        ok("PUT with an unknown timeline → 400", r.status_code == 400, r.get_json())
        sun = window_doc(show, wash, "18:00", "23:00")
        sun["location"] = {"lat": None, "lon": None, "tz": "America/Toronto"}
        sun["schedules"][0]["entries"][0]["start"] = {"ref": "sunset", "offsetMin": -15}
        r = c.put("/api/schedule", json=sun)
        ok("sunset entry without a location → 400 naming Settings",
           r.status_code == 400 and any("location" in e for e in r.get_json()["errors"]), r.get_json())
        r = c.post("/api/schedule/location", json={"lat": "43.6532", "lon": "-79.3832", "tz": "America/Toronto"})
        ok("POST location", r.status_code == 200 and r.get_json()["location"]["lat"] == 43.6532, r.get_json())
        r = c.post("/api/schedule/location", json={"lat": "abc"})
        ok("POST location rejects a non-number", r.status_code == 400)
        r = c.post("/api/schedule/location", json={"lat": 1, "lon": 1, "tz": "Mars/Base"})
        ok("POST location rejects an unknown tz", r.status_code == 400, r.get_json())

        # A window open right now (local), so the engine plays the show.
        now = datetime.now(TZ)
        start = (now - timedelta(minutes=30)).strftime("%H:%M")
        end = (now + timedelta(minutes=30)).strftime("%H:%M")
        doc = window_doc(show, wash, start, end)
        doc["enabled"] = False
        r = c.put("/api/schedule", json=doc)
        ok("PUT a valid document", r.status_code == 200 and r.get_json()["ok"], r.get_json())

        print("State and preview")
        st = c.get("/api/schedule/state").get_json()
        ok("state while disabled says so", st["enabled"] is False, st)
        ok("state names the timelines", st["timelines"].get(str(show)) == "Red show"
           or st["timelines"].get(show) == "Red show", st.get("timelines"))
        pv = c.get("/api/schedule/preview?days=1").get_json()
        ok("preview has segments with the entry", any(s["what"] == "Eaves › Evening" for s in pv["segments"]),
           [s["what"] for s in pv["segments"]])
        ok("preview has sun times", pv["sun"] and pv["sun"][0]["sunset"])
        ok("bad preview date → 400", c.get("/api/schedule/preview?date=nope").status_code == 400)

        print("Engine: bake before start, engine auto-start, position")
        ps._bake_result.pop(show, None)
        ps._bake_result.pop(wash, None)
        ok("precondition: both unbaked, engine stopped",
           show not in ps._bake_result and not ps._sacn.running)
        r = c.post("/api/schedule/enabled", json={"enabled": True})
        ok("enable", r.get_json()["enabled"] is True)
        ps._scheduler.tick()                         # synchronous: bakes + starts
        ok("the show was baked on demand", show in ps._bake_result)
        ok("sACN engine auto-started (#958 path)", ps._sacn.running)
        ok("show playback running from the scheduler",
           ps._show_playback.get("running") and ps._show_playback.get("source") == "schedule",
           ps._show_playback)
        ok("joined in position (~30 min into the window → 1800 s mod 20 s)",
           ps._show_playback.get("currentTid") == show)
        time.sleep(2.6)                              # go_epoch = now+2
        ok("red on the wire", pixel0() == (255, 0, 0), pixel0())
        st = c.get("/api/schedule/state").get_json()
        ok("state.now is the entry", (st["now"]["entry"] or {}).get("name") == "Evening", st["now"])
        ok("state.now.positionS ≈ 1800", abs(st["now"]["positionS"] - 1800) < 90, st["now"]["positionS"])
        ok("state.next is the window end → idle",
           st["next"] and st["next"]["what"] == "idle", st["next"])
        s = c.get("/api/show/status").get_json().get("schedule") or {}
        ok("/api/show/status carries the schedule summary",
           s.get("enabled") is True and s["now"]["what"] == "Evening" and s.get("next"), s)

        print("Hand-off: show → wash with no dark frame")
        seen, stop = [], {"v": False}
        import threading

        def sample():
            while not stop["v"]:
                seen.append(pixel0())
                time.sleep(0.005)
        t = threading.Thread(target=sample, daemon=True)
        t.start()
        # Close the window now → idle wash.
        end2 = (datetime.now(TZ) - timedelta(minutes=1)).strftime("%H:%M")
        start2 = (datetime.now(TZ) - timedelta(minutes=40)).strftime("%H:%M")
        c.put("/api/schedule", json=window_doc(show, wash, start2, end2))
        ps._scheduler.tick()
        time.sleep(3.2)
        stop["v"] = True
        t.join(1)
        ok("the wash was baked on demand too", wash in ps._bake_result)
        ok("blue on the wire after the hand-off", pixel0() == (0, 0, 255), pixel0())
        colours = [p for p in seen if p is not None]
        dark = [p for p in colours if p == (0, 0, 0)]
        ok("no dark frame across the transition", not dark and (255, 0, 0) in colours
           and (0, 0, 255) in colours, f"{len(dark)} dark of {len(colours)} samples")
        ok("transition logged with its reason",
           any("because" in e["msg"] for e in ps._scheduler.log_ring), [e["msg"] for e in ps._scheduler.log_ring])

        print("Manual override and resume")
        r = c.post("/api/show/stop")
        ok("manual stop ok", r.status_code == 200)
        st = c.get("/api/schedule/state").get_json()
        ok("state: manual override active", (st.get("override") or {}).get("active") is True, st.get("override"))
        ok("…by the requesting address", (st.get("override") or {}).get("by") == "127.0.0.1", st.get("override"))
        time.sleep(0.3)
        ok("a real stop sweeps to black", pixel0() == (0, 0, 0), pixel0())
        ps._scheduler.tick()
        ok("scheduler stays passive while overridden", not ps._show_playback.get("running"))
        r = c.post("/api/schedule/resume")
        ok("resume ok", r.status_code == 200 and not (ps._schedule_state.get("override") or {}).get("active"))
        ps._scheduler.tick()
        time.sleep(2.6)
        ok("after resume the schedule plays again (wash)", pixel0() == (0, 0, 255), pixel0())
        r = c.post("/api/schedule/override", json={"kind": "hold"})
        ok("explicit hold override", r.get_json()["override"]["kind"] == "hold")
        c.post("/api/schedule/resume")
        ps._scheduler.tick()                         # back on schedule
        ok("log route", isinstance(c.get("/api/schedule/log?limit=5").get_json(), list))

        print("Kill switch")
        c.post("/api/schedule/enabled", json={"enabled": False})
        n = len(ps._scheduler.log_ring)
        ps._scheduler.tick()
        ok("disabled engine logs that it stopped driving output",
           any("disabled" in e["msg"] for e in list(ps._scheduler.log_ring)[n - 1:]))
        ok("/api/show/status schedule summary says disabled",
           c.get("/api/show/status").get_json()["schedule"] == {"enabled": False})

        print("Project export / import carries the schedule")
        exp = c.get("/api/project/export").get_json()
        ok("export includes schedule", exp.get("schedule", {}).get("schedules"), list(exp)[:12])
        exp["schedule"]["schedules"][0]["name"] = "Imported"
        r = c.post("/api/project/import", json=exp)
        ok("import ok", r.status_code == 200, r.get_json())
        ok("imported schedule applied", ps._schedule_doc["schedules"][0]["name"] == "Imported",
           ps._schedule_doc.get("schedules"))
    finally:
        try:
            c.post("/api/schedule/enabled", json={"enabled": False})
            ps._stop_show_internal(sweep=True)
        except Exception:
            pass
        ps._sacn.stop()
        ps._artnet.stop()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
