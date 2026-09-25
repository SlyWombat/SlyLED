#!/usr/bin/env python3
"""Show scheduler Phase 2 — HinksPix offline hand-off and fades (#954).

  * POST /api/schedule/compile/hinkspix/<cid> previews the week (no device)
  * with deploy: bakes, renders one .hseq per timeline, uploads a .ply per
    playlist (show and wash separate), seven .sched files whose rows name
    their playlist, sets the clock — and switches to standalone ONLY under the
    "always" hand-off policy
  * refused (not sent) when the controller's port config isn't pushed
  * hand-off policy "shutdown": clean exit → standalone, start → live;
    other policies never touch the mode
  * fades: the engine fades in from dark and fades out ahead of an 'off'
    edge; the send-time master is scaled while the operator's value is
    untouched; a manual verb cancels a fade

The controller is a recording fake patched over hinkspix_tcp.HinksPixTcp;
nothing leaves the machine.

Run: python3 tests/test_schedule_offline.py
"""

import os
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-sched2-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402  (binds orch_state first)
import hinkspix_bridge  # noqa: E402
import hinkspix_tcp  # noqa: E402
import orch_hinkspix  # noqa: E402
import schedule_eval as se  # noqa: E402
import show_scheduler as ss  # noqa: E402
from datetime import datetime  # noqa: E402
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


CALLS = []


class FakeTcp:
    def __init__(self, ip, *a, **k):
        self.ip = ip

    def upload(self, name, data, progress_cb=None):
        CALLS.append(("upload", self.ip, name, bytes(data)))
        if progress_cb:
            progress_cb(len(data), len(data), "")

    def set_time(self, *a, **k):
        CALLS.append(("set_time", self.ip))

    def set_mode(self, mode):
        CALLS.append(("set_mode", self.ip, mode))


CID = 9544


def main():
    real_tcp, real_eth = hinkspix_tcp.HinksPixTcp, hinkspix_bridge.op_mode_ethernet
    hinkspix_tcp.HinksPixTcp = FakeTcp
    hinkspix_bridge.op_mode_ethernet = lambda ip, *a, **k: CALLS.append(("live", ip))
    c = ps.app.test_client()
    try:
        ps._children[:] = [x for x in ps._children if x["id"] != CID] + [{
            "id": CID, "type": "hinkspix", "ip": "127.0.0.9", "name": "HP", "sc": 0,
            "strings": [], "status": 1,
            "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402, "mcpu": 160,
                      "uploadSupported": True, "protocol": "e131",
                      "dmxOut": {"enabled": False, "universe": None},
                      "ports": [{"port": 17, "leds": 4, "enabled": True}]}}]
        child = ps._children[-1]
        fid = c.post("/api/fixtures", json={"name": "Eaves", "fixtureType": "led", "type": "linear",
                                            "childId": CID, "strings": [{"port": 17, "leds": 4, "mm": 100}]}
                     ).get_json()["id"]

        def timeline(name, rgb, secs=2):
            aid = c.post("/api/actions", json={"name": name, "type": 1, "r": rgb[0], "g": rgb[1],
                                               "b": rgb[2]}).get_json()["id"]
            tid = c.post("/api/timelines", json={"name": name, "durationS": secs, "loop": True}).get_json()["id"]
            c.put(f"/api/timelines/{tid}", json={"name": name, "durationS": secs, "loop": True, "tracks": [
                {"fixtureId": fid, "clips": [{"actionId": aid, "startS": 0, "durationS": secs}]}]})
            return tid

        show, wash = timeline("Evening", (255, 0, 0)), timeline("Wash", (0, 0, 255))
        doc = {"enabled": True, "location": {"lat": 43.6532, "lon": -79.3832, "tz": "America/Toronto"},
               "idle": {"kind": "timeline", "timelineId": wash},
               "hinkspix": {"handoff": "manual", "compileIdle": True, "controllers": [CID]},
               "schedules": [{"id": 1, "name": "Xmas", "priority": 10, "entries": [
                   {"id": 1, "name": "Evening", "days": list(se.DAYS),
                    "start": {"ref": "clock", "time": "18:00"}, "end": {"ref": "clock", "time": "23:00"},
                    "play": {"kind": "timeline", "timelineId": show, "loop": True},
                    "hinkspix": {"compile": True}}]}]}
        r = c.put("/api/schedule", json=doc)
        ok("schedule saved", r.status_code == 200, r.get_json())

        print("Compile preview (no device)")
        r = c.post(f"/api/schedule/compile/hinkspix/{CID}", json={"deploy": False})
        d = r.get_json() or {}
        ok("200 with rows", r.status_code == 200 and d["compile"]["days"]["MONDAY"], d)
        ok("playlists: EVENING + WASH", d["compile"]["playlists"] == {"EVENING": [show], "WASH": [wash]},
           d["compile"]["playlists"])
        ok("no device traffic", not CALLS, CALLS)
        ok("state records the compile",
           (ps._schedule_state.get("hinkspix") or {}).get(str(CID), {}).get("to") == d["compile"]["to"])

        print("Refused while the port config isn't pushed")
        child["hinks"].pop("configHash", None)
        r = c.post(f"/api/schedule/compile/hinkspix/{CID}", json={"deploy": True})
        ok("deploy says not sent, with the reason",
           "not sent" in (r.get_json().get("deploy") or "") and "pushed" in r.get_json()["deploy"], r.get_json())
        child["hinks"]["configHash"] = orch_hinkspix._config_hash(child["hinks"])

        print("Compile & send — policy 'manual': files + clock, no mode switch")
        ps._bake_result.pop(show, None)
        ps._bake_result.pop(wash, None)
        CALLS.clear()
        r = c.post(f"/api/schedule/compile/hinkspix/{CID}", json={"deploy": True})
        ok("sending", (r.get_json().get("deploy") or "").startswith("sending"), r.get_json())
        for _ in range(200):
            if not orch_hinkspix._deploy_state.get(CID, {}).get("running") and any(c_[0] == "set_time" for c_ in CALLS):
                break
            time.sleep(0.1)
        st = orch_hinkspix._deploy_state.get(CID, {})
        ok("deploy finished ok", st.get("ok") is True, st)
        ok("both timelines were baked on demand", show in ps._bake_result and wash in ps._bake_result)
        names = [x[2] for x in CALLS if x[0] == "upload"]
        ok("one .hseq per timeline", sorted(n for n in names if n.endswith(".hseq")) == ["EVENING.hseq", "WASH.hseq"], names)
        ok("a .ply per playlist", sorted(n for n in names if n.endswith(".ply")) == ["EVENING.ply", "WASH.ply"], names)
        ok("seven .sched files", len([n for n in names if n.endswith(".sched")]) == 7, names)
        mon = next(x[3] for x in CALLS if x[0] == "upload" and x[2] == "MONDAY.sched").decode()
        ok("MONDAY.sched rows name their own playlist",
           '"P":"WASH.ply"' in mon and '"S":"1800","E":"2300","P":"EVENING.ply"' in mon, mon)
        ok("clock set", any(x[0] == "set_time" for x in CALLS))
        ok("NO mode switch under 'manual'", not any(x[0] == "set_mode" for x in CALLS), CALLS)
        ok("lastDeploy records live mode",
           orch_hinkspix._deploy_cfg()[str(CID)]["lastDeploy"]["mode"] == "live")

        print("Policy 'always' switches to standalone after sending")
        doc["hinkspix"]["handoff"] = "always"
        c.put("/api/schedule", json=doc)
        CALLS.clear()
        c.post(f"/api/schedule/compile/hinkspix/{CID}", json={"deploy": True})
        for _ in range(200):
            if not orch_hinkspix._deploy_state.get(CID, {}).get("running") and any(x[0] == "set_mode" for x in CALLS):
                break
            time.sleep(0.1)
        ok("set_mode(standalone) sent", any(x[0] == "set_mode" and x[2] == hinkspix_tcp.MODE_MASTER for x in CALLS), CALLS)

        print("Policy 'shutdown': exit → standalone, start → live")
        doc["hinkspix"]["handoff"] = "shutdown"
        c.put("/api/schedule", json=doc)
        CALLS.clear()
        ps._schedule_handoff("standalone", "test exit")
        ps._schedule_handoff("live", "test start")
        ok("exit sends standalone", ("set_mode", "127.0.0.9", hinkspix_tcp.MODE_MASTER) in CALLS, CALLS)
        ok("start sends live", ("live", "127.0.0.9") in CALLS, CALLS)
        doc["hinkspix"]["handoff"] = "manual"
        c.put("/api/schedule", json=doc)
        CALLS.clear()
        ps._schedule_handoff("standalone", "test exit")
        ok("'manual' never touches the mode", not CALLS, CALLS)

        print("Fades")
        ps._settings["globalBrightness"] = 200
        ps._fade_start(0.0, 1.0, 1.0)
        m0 = ps._master_with_fade()
        time.sleep(0.5)
        m1 = ps._master_with_fade()
        time.sleep(0.6)
        m2 = ps._master_with_fade()
        ok("fade-in ramps the send-time master 0 → operator value",
           m0 <= 20 and 60 < m1 < 160 and m2 == 200, (m0, m1, m2))
        ok("the operator's master value is untouched", ps._settings["globalBrightness"] == 200)
        ps._fade_start(1.0, 0.0, 5.0)
        with ps.app.test_request_context("/api/show/stop", method="POST", json={}):
            ps._schedule_manual()
        ok("a manual verb cancels a fade", ps._master_with_fade() == 200)
        ps._settings["globalBrightness"] = 255

        class Act:
            def __init__(self):
                self.calls = []

            def play(self, p, pos, d):
                self.calls.append(("play", p.get("timelineId")))

            def idle(self, p, d):
                self.calls.append(("idle", p.get("kind")))

            def off(self, d):
                self.calls.append(("off",))

            def fade(self, a, b, s):
                self.calls.append(("fade", a, b, round(s)))

        fd = {"enabled": True, "location": {"lat": 43.65, "lon": -79.38, "tz": "America/Toronto"},
              "idle": {"kind": "off"},
              "schedules": [{"id": 1, "name": "S", "priority": 1, "entries": [
                  {"id": 1, "name": "E", "days": list(se.DAYS),
                   "start": {"ref": "clock", "time": "18:00"}, "end": {"ref": "clock", "time": "23:00"},
                   "play": {"kind": "timeline", "timelineId": 3, "loop": True},
                   "transition": {"fadeInS": 4, "fadeOutS": 10}}]}]}

        def ts(s):
            return datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp()

        clk = {"t": ts("2026-10-07 17:59")}
        act = Act()
        eng = ss.ShowScheduler(lambda: fd, act, clock=lambda: clk["t"])
        eng.tick()
        clk["t"] = ts("2026-10-07 18:00") + 1
        eng.tick()
        ok("coming up from dark: play then fade 0→1 over 4 s",
           act.calls[-2:] == [("play", 3), ("fade", 0.0, 1.0, 4)], act.calls)
        clk["t"] = ts("2026-10-07 22:59") + 52
        eng.tick()
        ok("10 s before the 'off' edge: fade 1→0", act.calls[-1][:3] == ("fade", 1.0, 0.0), act.calls)
        clk["t"] = ts("2026-10-07 23:00") + 1
        eng.tick()
        ok("then off", act.calls[-1] == ("off",), act.calls)
        fd2 = dict(fd, idle={"kind": "timeline", "timelineId": 12})
        act2 = Act()
        clk2 = {"t": ts("2026-10-07 17:00")}
        e2 = ss.ShowScheduler(lambda: fd2, act2, clock=lambda: clk2["t"])
        e2.tick()
        clk2["t"] = ts("2026-10-07 18:00") + 1
        e2.tick()
        clk2["t"] = ts("2026-10-07 22:59") + 55
        e2.tick()
        ok("wash → show → wash: no fades (never a dark dip in a hand-off)",
           not any(x[0] == "fade" for x in act2.calls), act2.calls)
    finally:
        hinkspix_tcp.HinksPixTcp, hinkspix_bridge.op_mode_ethernet = real_tcp, real_eth
        ps._fade_reset()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
