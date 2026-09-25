#!/usr/bin/env python3
"""HinksPix show output, end to end on the wire (#957, #958, #959).

The operator's first real show on v2.1.2 (HinksPix PRO at 192.168.10.6,
"Red Chase" on the garage eaves) stayed dark for three stacked reasons.
This suite drives the same path hermetically — a streamed HinksPix fixture,
sACN routed by unicast to 127.0.0.1, the DMX engine STOPPED — and checks
the packets that actually leave the orchestrator:

  * #958  /api/show/start (and /api/timelines/<id>/start) starts the
          configured engine instead of reporting success with nothing sent;
          refuses with 409 when the engine can't start
  * #959  sACN honours universeRoutes: routed universes go unicast to the
          route's destination, not only to the multicast group
  * #957  "Red Chase" arrives as (255, 0, 0), not (255, 200, 255)

Run: python3 tests/test_hinkspix_show_output.py
"""

import os
import socket
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hpshow-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from dmx_sacn import SACN_PORT, parse_sacn_data  # noqa: E402

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


CID, FID, TID = 7950, 7951, 7952
BASE_UNI = 100
LEDS = 30
RED_CHASE = {"r": 255, "g": 0, "b": 0, "speedMs": 100, "spacing": 3, "direction": 0}


def make_rig():
    ps = parent_server
    child = {"id": CID, "type": "hinkspix", "ip": "127.0.0.1", "name": "Eaves ctl",
             "sc": 0, "strings": [], "status": 1,
             "hinks": {"baseUniverse": BASE_UNI, "maxU": 402, "mcpu": 160,
                       "uploadSupported": True, "protocol": "e131",
                       "dmxOut": {"enabled": False, "universe": None},
                       "ports": [{"port": 1, "leds": LEDS, "enabled": True}]}}
    fixture = {"id": FID, "childId": CID, "fixtureType": "led", "name": "Eaves",
               "strings": [{"port": 1, "leds": LEDS}]}
    ps._children.append(child)
    ps._fixtures.append(fixture)
    ps._timelines.append({"id": TID, "name": "Red Chase", "durationS": 30,
                          "loop": False, "tracks": []})
    ps._bake_result[TID] = {"fixtures": {FID: {"segments": [
        {"type": 4, "startS": 0, "durationS": 30, "params": dict(RED_CHASE)}]}}}
    ps._dmx_settings["protocol"] = "sacn"
    ps._dmx_settings["universeRoutes"] = [
        {"universe": BASE_UNI, "destination": "127.0.0.1", "label": "eaves"}]
    ps._apply_dmx_settings()


def clear_rig():
    ps = parent_server
    ps._children[:] = [c for c in ps._children if c.get("id") != CID]
    ps._fixtures[:] = [f for f in ps._fixtures if f.get("id") != FID]
    ps._timelines[:] = [t for t in ps._timelines if t.get("id") != TID]
    ps._bake_result.pop(TID, None)


def stop_everything(c):
    c.post("/api/show/stop")
    parent_server._dmx_playback_stop.set()
    time.sleep(0.2)
    parent_server._sacn.stop()
    parent_server._artnet.stop()


def collect(rx, seconds):
    """Read E1.31 packets from the receiver for up to *seconds*."""
    pkts = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            data, addr = rx.recvfrom(1024)
        except OSError:
            continue
        p = parse_sacn_data(data)
        if p:
            pkts.append(p)
    return pkts


def main():
    ps = parent_server
    ps._artnet.stop()
    ps._sacn.stop()
    make_rig()
    c = ps.app.test_client()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", SACN_PORT))
        rx.settimeout(0.2)
    except OSError as e:
        rx.close()
        rx = None
        print(f"  [SKIP] wire checks — 127.0.0.1:{SACN_PORT} unavailable ({e})")

    try:
        print("#958 — Start show brings a stopped DMX engine up")
        ok("precondition: both engines stopped",
           not ps._sacn.running and not ps._artnet.running)
        r = c.post("/api/show/start", json={"order": [TID]})
        body = r.get_json() or {}
        ok("show start returns 200", r.status_code == 200, f"{r.status_code} {body}")
        ok("the configured sACN engine is now running", ps._sacn.running)
        ok("Art-Net was not started behind the operator's back",
           not ps._artnet.running)
        ok("response reports output auto-started",
           (body.get("output") or {}).get("autoStarted") is True
           and (body.get("output") or {}).get("protocol") == "sacn", body.get("output"))

        if rx is not None:
            print("#959 / #957 — Red Chase reaches the routed controller as red")
            pkts = collect(rx, 4.0)   # go_epoch is now+2 s
            ours = [p for p in pkts if p["universe"] == BASE_UNI]
            ok(f"E1.31 packets for universe {BASE_UNI} arrive by unicast on 127.0.0.1",
               len(ours) > 0, f"{len(pkts)} packets, universes {sorted({p['universe'] for p in pkts})}")
            lit = set()
            for p in ours:
                d = p["dmxData"][:LEDS * 3]
                for i in range(0, len(d), 3):
                    px = tuple(d[i:i + 3])
                    if px != (0, 0, 0):
                        lit.add(px)
            ok("chase pixels are lit", len(lit) > 0, "every frame was black")
            ok("every lit pixel is pure red (255, 0, 0)", lit == {(255, 0, 0)},
               f"lit colours seen: {sorted(lit)[:5]}")

        print("#957 follow-up — /api/dmx/monitor shows what sACN is streaming")
        from dmx_universe import DMXUniverse
        time.sleep(0.3)
        m = c.get(f"/api/dmx/monitor/{BASE_UNI}").get_json() or {}
        ch = m.get("channels") or []
        mlit = {tuple(ch[i:i + 3]) for i in range(0, LEDS * 3, 3)} - {(0, 0, 0)}
        ok("monitor reads the sACN engine", m.get("engine") == "sacn", m.get("engine"))
        ok("monitor shows lit red pixels", mlit == {(255, 0, 0)}, sorted(mlit)[:5])
        # Art-Net also running and holding the same universe number (idle
        # zeros) must not shadow the configured sACN engine. Simulated
        # without a socket so nothing is sent on the LAN.
        art = ps._artnet
        saved = (art._running, dict(art._universes))
        art._running = True
        art._universes[BASE_UNI] = DMXUniverse(BASE_UNI)
        try:
            m2 = c.get(f"/api/dmx/monitor/{BASE_UNI}").get_json() or {}
        finally:
            art._running = saved[0]
            art._universes.clear()
            art._universes.update(saved[1])
        ch2 = m2.get("channels") or []
        ok("an idle running Art-Net doesn't shadow the sACN show in the monitor",
           m2.get("engine") == "sacn" and any(ch2[:LEDS * 3]), m2.get("engine"))

        stop_everything(c)

        print("#958 — timeline start takes the same path")
        ok("precondition: sACN stopped again", not ps._sacn.running)
        r = c.post(f"/api/timelines/{TID}/start")
        body = r.get_json() or {}
        ok("timeline start returns 200", r.status_code == 200, f"{r.status_code} {body}")
        ok("timeline start started the sACN engine", ps._sacn.running)
        c.post(f"/api/timelines/{TID}/stop")
        stop_everything(c)

        print("#958 — an engine that can't start is refused, not silently 'running'")
        real_start = ps._sacn.start

        def _boom():
            raise OSError("bind failed (test)")
        ps._sacn.start = _boom
        try:
            r = c.post("/api/show/start", json={"order": [TID]})
            body = r.get_json() or {}
            ok("show start returns 409", r.status_code == 409, f"{r.status_code} {body}")
            ok("error names the stopped DMX output",
               "DMX output" in (body.get("err") or ""), body.get("err"))
            ok("show is not marked running", not ps._show_playback.get("running"))
        finally:
            ps._sacn.start = real_start

        print("#958 — a rig with nothing to drive over DMX doesn't need an engine")
        clear_rig()
        ps._timelines.append({"id": TID, "name": "empty", "durationS": 5,
                              "loop": False, "tracks": []})
        ps._bake_result[TID] = {"fixtures": {}}
        had_dmx = [f for f in ps._fixtures if f.get("fixtureType") == "dmx"]
        had_hp = [x for x in ps._children if x.get("type") == "hinkspix"]
        if not had_dmx and not had_hp:
            r = c.post("/api/show/start", json={"order": [TID]})
            body = r.get_json() or {}
            ok("show start still succeeds", r.status_code == 200, f"{r.status_code} {body}")
            ok("no engine was started", not ps._sacn.running and not ps._artnet.running)
            ok("output is reported as not applicable", body.get("output") is None,
               body.get("output"))
        stop_everything(c)
    finally:
        stop_everything(c)
        clear_rig()
        if rx is not None:
            rx.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
