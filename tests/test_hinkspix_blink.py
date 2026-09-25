#!/usr/bin/env python3
"""Engine-start blink reaches pixel strings, every start (#960).

  * POST /api/dmx/start blinks a HinksPix string R → G → B → dark, and puts
    back what was on the span before
  * a second start (stop → start) blinks again — no once-per-launch gate
  * no blink while a show is running, or with bootBlinkFixtures off
  * the response says what it will blink; /api/dmx/blink counts pixels too

sACN is routed to 127.0.0.1; nothing leaves the machine.

Run: python3 tests/test_hinkspix_blink.py
"""

import os
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-blink-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402

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


CID, FID = 9600, 9601


def watch(seconds=2.2):
    """Sample pixel 0 of universe 1 on the sACN engine; return the colours
    seen, in order, de-duplicated."""
    seen = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        u = ps._sacn.peek_universe(1)
        if u is not None:
            px = tuple(u.get_data()[0:3])
            if not seen or seen[-1] != px:
                seen.append(px)
        time.sleep(0.02)
    return seen


def main():
    ps._children[:] = [c for c in ps._children if c["id"] != CID] + [{
        "id": CID, "type": "hinkspix", "ip": "127.0.0.1", "name": "HP", "sc": 0,
        "strings": [], "status": 1,
        "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402, "mcpu": 160,
                  "uploadSupported": True, "protocol": "e131",
                  "dmxOut": {"enabled": False, "universe": None},
                  "ports": [{"port": 17, "leds": 20, "enabled": True}]}}]
    ps._fixtures[:] = [f for f in ps._fixtures if f["id"] != FID] + [{
        "id": FID, "name": "Eaves", "fixtureType": "led", "type": "linear",
        "childId": CID, "strings": [{"port": 17, "leds": 20}]}]
    ps._dmx_settings.update(protocol="sacn", bootBlinkFixtures=True,
                            universeRoutes=[{"universe": 1, "destination": "127.0.0.1"}])
    ps._apply_dmx_settings()
    ps._artnet.stop()
    ps._sacn.stop()
    c = ps.app.test_client()
    try:
        print("First engine start blinks the pixel string")
        # Something already on the span, to prove it is restored afterwards.
        ps._sacn.get_universe(1).set_channels(1, bytes([9, 8, 7]) * 20)
        r = c.post("/api/dmx/start", json={"protocol": "sacn"})
        d = r.get_json() or {}
        ok("start ok", r.status_code == 200 and d.get("ok"), d)
        ok("response says 1 pixel fixture will blink",
           (d.get("blink") or {}).get("pixel") == 1, d.get("blink"))
        seen = watch()
        ok("red, green, blue seen in that order",
           [p for p in seen if p in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
           == [(255, 0, 0), (0, 255, 0), (0, 0, 255)], seen)
        ok("goes dark between/after", (0, 0, 0) in seen, seen)
        time.sleep(0.3)
        ok("what was on the span before is put back",
           tuple(ps._sacn.get_universe(1).get_data()[0:3]) == (9, 8, 7),
           tuple(ps._sacn.get_universe(1).get_data()[0:3]))

        print("Stop → start blinks again (no once-per-launch gate)")
        ps._sacn.stop()
        c.post("/api/dmx/start", json={"protocol": "sacn"})
        seen = watch()
        ok("second start blinks too", (0, 255, 0) in seen, seen)

        print("Never during a show")
        ps._sacn.stop()
        ps._show_playback["running"] = True
        try:
            r = c.post("/api/dmx/start", json={"protocol": "sacn"})
            ok("response: no blink planned", (r.get_json() or {}).get("blink") is None,
               r.get_json())
            seen = watch(1.2)
            ok("no R/G/B while a show runs",
               not any(p in seen for p in ((255, 0, 0), (0, 255, 0), (0, 0, 255))), seen)
            r = c.post("/api/dmx/blink")
            ok("manual blink refused during a show (409)", r.status_code == 409)
        finally:
            ps._show_playback["running"] = False

        print("bootBlinkFixtures off → no blink")
        ps._sacn.stop()
        ps._dmx_settings["bootBlinkFixtures"] = False
        c.post("/api/dmx/start", json={"protocol": "sacn"})
        seen = watch(1.2)
        ok("no blink with the setting off",
           not any(p in seen for p in ((255, 0, 0), (0, 255, 0), (0, 0, 255))), seen)
        ps._dmx_settings["bootBlinkFixtures"] = True

        print("Manual Blink counts pixel strings")
        r = c.post("/api/dmx/blink")
        d = r.get_json() or {}
        ok("manual blink ok with only a pixel fixture (no DMX)",
           r.status_code == 200 and d.get("pixel") == 1 and d.get("dmx") == 0, d)
        seen = watch()
        ok("…and it blinks", (0, 0, 255) in seen, seen)
    finally:
        ps._sacn.stop()
        ps._artnet.stop()
        ps._children[:] = [x for x in ps._children if x["id"] != CID]
        ps._fixtures[:] = [f for f in ps._fixtures if f["id"] != FID]

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
