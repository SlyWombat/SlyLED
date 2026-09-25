#!/usr/bin/env python3
"""First-time HinksPix setup guide — server side (#953).

  * hinkspix_config.derive_color_order: every strip order, from any current
    port order, is recovered from the two answers
  * POST /api/hinkspix/<cid>/identify lights exactly the port's span (solid
    and chase), times out, stops early, refuses unknown ports and a running
    show, and starts a stopped engine (#958 path)
  * POST /api/hinkspix/<cid>/color-order stores the derived order through the
    validated config path

sACN is routed to 127.0.0.1 so nothing leaves the machine.

Run: python3 tests/test_hinkspix_guide.py
"""

import itertools
import os
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hpguide-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_config as hc  # noqa: E402
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


CID = 9530


def simulate(strip, configured):
    """What the operator sees: the controller puts logical channel
    configured[i] in wire slot i; the strip shows slot i as strip[i]."""
    def seen(logical):
        return strip[configured.index(logical)]
    return seen("R"), seen("G")


def main():
    print("derive_color_order — every strip order from every configured order")
    orders = ["".join(p) for p in itertools.permutations("RGB")]
    bad = []
    for strip in orders:
        for cur in orders:
            r, g = simulate(strip, cur)
            got = hc.derive_color_order(cur, r, g)
            if got != strip:
                bad.append((strip, cur, got))
    ok("all 36 (strip, configured) pairs recover the strip order", not bad, bad[:3])
    for args in (("RGBW", "R", "G"), ("RGB", "R", "R"), ("RGB", "X", "G"), ("RGB", "", "G")):
        try:
            hc.derive_color_order(*args)
            ok(f"rejects {args}", False)
        except ValueError:
            ok(f"rejects {args}", True)

    child = {"id": CID, "type": "hinkspix", "ip": "127.0.0.1", "name": "HP",
             "sc": 0, "strings": [], "status": 1,
             "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402,
                       "mcpu": 160, "uploadSupported": True, "protocol": "e131",
                       "boards": {"BD1": "Long_Range", "BD2": "Local_SPI"},
                       "dmxOut": {"enabled": False, "universe": None},
                       "ports": [{"port": 17, "leds": 10, "enabled": True,
                                  "protocol": "ws2811", "colorOrder": "RGB"},
                                 {"port": 18, "leds": 5, "enabled": True,
                                  "protocol": "ws2811", "colorOrder": "RGB"}]}}
    ps._children[:] = [c for c in ps._children if c["id"] != CID] + [child]
    ps._dmx_settings["protocol"] = "sacn"
    ps._dmx_settings["universeRoutes"] = [{"universe": 1, "destination": "127.0.0.1"}]
    ps._apply_dmx_settings()
    ps._sacn.stop()
    ps._artnet.stop()
    c = ps.app.test_client()
    try:
        print("identify — lights the port's span, starting a stopped engine")
        r = c.post(f"/api/hinkspix/{CID}/identify", json={"port": 17, "color": "red",
                                                          "seconds": 2})
        d = r.get_json() or {}
        ok("200", r.status_code == 200, f"{r.status_code} {d}")
        ok("sACN engine auto-started", ps._sacn.running and (d.get("output") or {}).get("autoStarted"))
        ok("protocol match reported (e131 vs sacn)", d.get("protocolMatch") is True, d)
        time.sleep(0.2)
        buf = ps._sacn.get_universe(1).get_data()
        # port 17 = first configured port → channels 1..30; port 18 follows.
        ok("port 17's 10 pixels are red", list(buf[0:30]) == [255, 0, 0] * 10, list(buf[0:6]))
        ok("port 18's span is untouched", list(buf[30:45]) == [0] * 15, list(buf[30:36]))
        time.sleep(2.3)
        buf = ps._sacn.get_universe(1).get_data()
        ok("times out and blacks the span out", list(buf[0:30]) == [0] * 30, list(buf[0:6]))
        ok("no identify entries left", not any(str(k).startswith("identify:")
                                                for k in ps._live_pixel_actions))

        r = c.post(f"/api/hinkspix/{CID}/identify", json={"all": True, "pattern": "chase",
                                                          "color": "white", "seconds": 5})
        ok("light them all: both ports", (r.get_json() or {}).get("ports") == [17, 18], r.get_json())
        time.sleep(0.2)
        buf = ps._sacn.get_universe(1).get_data()
        lit = [tuple(buf[i:i + 3]) for i in range(0, 45, 3)]
        ok("chase: some pixels lit white, some dark",
           (255, 255, 255) in lit and (0, 0, 0) in lit, lit)
        r = c.post(f"/api/hinkspix/{CID}/identify", json={"stop": True})
        ok("stop clears both", (r.get_json() or {}).get("stopped") == 2, r.get_json())
        buf = ps._sacn.get_universe(1).get_data()
        ok("stop blacks out", not any(buf[0:45]))

        r = c.post(f"/api/hinkspix/{CID}/identify", json={"port": 5})
        ok("port with no pixel count → 400", r.status_code == 400, r.get_json())
        r = c.post(f"/api/hinkspix/{CID}/identify", json={"port": 17, "color": "pink"})
        ok("unknown colour → 400", r.status_code == 400)
        ps._show_playback["running"] = True
        try:
            r = c.post(f"/api/hinkspix/{CID}/identify", json={"port": 17})
            ok("refused while a show runs → 409", r.status_code == 409, r.get_json())
        finally:
            ps._show_playback["running"] = False

        print("color-order — derived and stored in SlyLED's copy")
        r = c.post(f"/api/hinkspix/{CID}/color-order",
                   json={"port": 17, "seenRed": "G", "seenGreen": "R"})
        d = r.get_json() or {}
        ok("strip is GRB", r.status_code == 200 and d.get("colorOrder") == "GRB", d)
        ok("reports a change that needs sending", d.get("changed") and d.get("needsSend"))
        p17 = next(p for p in child["hinks"]["ports"] if p["port"] == 17)
        ok("stored on port 17", p17["colorOrder"] == "GRB", p17)
        ok("port 18 untouched",
           next(p for p in child["hinks"]["ports"] if p["port"] == 18)["colorOrder"] == "RGB")
        r = c.post(f"/api/hinkspix/{CID}/color-order",
                   json={"port": 17, "seenRed": "R", "seenGreen": "G"})
        ok("a correct test reports no change",
           (r.get_json() or {}).get("changed") is False, r.get_json())
        r = c.post(f"/api/hinkspix/{CID}/color-order",
                   json={"port": 17, "seenRed": "R", "seenGreen": "R"})
        ok("contradictory answers → 400", r.status_code == 400)
        r = c.post(f"/api/hinkspix/{CID}/color-order",
                   json={"port": 40, "seenRed": "R", "seenGreen": "G"})
        ok("port not in the table → 400", r.status_code == 400)
    finally:
        c.post(f"/api/hinkspix/{CID}/identify", json={"stop": True})
        ps._sacn.stop()
        ps._artnet.stop()
        ps._children[:] = [x for x in ps._children if x["id"] != CID]

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
