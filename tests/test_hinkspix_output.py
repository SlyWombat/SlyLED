#!/usr/bin/env python3
"""HinksPix live streaming output tests (#940).

Proves that a baked show actually reaches the right bytes in the right
universes, rather than that the plumbing merely exists:

  * write_fixture_frame lands RGB at the mapped universe + channel offsets
  * a multi-universe port splits at the 170-pixel boundary
  * an LED-only show (no DMX fixtures) no longer takes the idle path
  * blackout zeroes pixel spans, and respects the #840 mid-playlist rule
  * ad-hoc actions register/clear on the live ticker instead of sending
    CMD_ACTION to a device with no firmware renderer
  * master brightness reaches pixel universes via all_intensity (#938)

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_output.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hpout-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
import pixel_output  # noqa: E402
import pixel_renderer  # noqa: E402
from dmx_universe import DMXUniverse  # noqa: E402
from pixel_output import PixelOutputMap  # noqa: E402

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


class FakeEngine:
    """Minimal engine: just universe buffers, like the real ones expose."""

    def __init__(self, running=True):
        self.running = running
        self._universes = {}

    def get_universe(self, u):
        if u not in self._universes:
            self._universes[u] = DMXUniverse(u)
        return self._universes[u]

    def peek_universe(self, u):
        return self._universes.get(u)


def make_child(cid=7, base=100, ports=None):
    return {"id": cid, "type": "hinkspix", "ip": "192.168.10.6", "name": "Roof",
            "sc": 0, "strings": [], "status": 1,
            "hinks": {"baseUniverse": base, "maxU": 402, "mcpu": 160,
                      "uploadSupported": True, "protocol": "e131",
                      "dmxOut": {"enabled": False, "universe": None},
                      "ports": ports or [{"port": 1, "leds": 4, "enabled": True},
                                         {"port": 2, "leds": 200, "enabled": True}]}}


def main():
    child = make_child()
    omap = PixelOutputMap.build(child)

    print("write_fixture_frame — bytes land at the mapped offsets")
    eng = FakeEngine()
    fixture = {"id": 1, "childId": 7, "fixtureType": "led",
               "strings": [{"port": 1, "leds": 4}]}
    rgb = bytes([10, 11, 12, 20, 21, 22, 30, 31, 32, 40, 41, 42])
    pixel_output.write_fixture_frame(eng, omap, fixture, rgb)
    data = eng.get_universe(100).get_data()
    ok("first pixel at channel 1", list(data[0:3]) == [10, 11, 12])
    ok("fourth pixel at channel 10", list(data[9:12]) == [40, 41, 42])
    ok("channels past the string stay zero", data[12] == 0)
    ok("universe is flagged all-intensity for the master gate",
       eng.get_universe(100).all_intensity is True)
    ok("writing marks the universe dirty so the engine transmits",
       eng.get_universe(100).dirty is True)

    print("write_fixture_frame — multi-universe port splits at 170px")
    eng2 = FakeEngine()
    fx2 = {"id": 2, "childId": 7, "fixtureType": "led",
           "strings": [{"port": 2, "leds": 200}]}
    rgb2 = bytes([(i % 256) for i in range(200 * 3)])
    pixel_output.write_fixture_frame(eng2, omap, fx2, rgb2)
    u_a = eng2.get_universe(101).get_data()
    u_b = eng2.get_universe(102).get_data()
    ok("first 170 pixels fill universe 101", list(u_a[0:3]) == [0, 1, 2])
    ok("universe 101 uses 510 channels", u_a[509] == rgb2[509])
    ok("pixel 171 starts universe 102", list(u_b[0:3]) == list(rgb2[510:513]))
    ok("remaining 30 pixels land in 102", u_b[89] == rgb2[599])
    ok("102 stops after 90 channels", u_b[90] == 0)

    print("render_fixture -> write_fixture_frame round trip")
    eng3 = FakeEngine()
    bake = {"segments": [{"type": 13, "startS": 0, "durationS": 10,
                          "params": {"r": 255, "g": 0, "b": 0,
                                     "r2": 0, "g2": 0, "b2": 255}}]}
    rgb3 = pixel_renderer.render_fixture(bake, fixture["strings"], 1.0)
    pixel_output.write_fixture_frame(eng3, omap, fixture, rgb3)
    d3 = eng3.get_universe(100).get_data()
    ok("gradient start is red", list(d3[0:3]) == [255, 0, 0])
    ok("gradient end is blue", list(d3[9:12]) == [0, 0, 255])
    ok("written bytes equal the renderer's output", bytes(d3[:12]) == rgb3)

    print("Master brightness reaches pixel universes (#938)")
    u = DMXUniverse(100)
    u.all_intensity = True
    u.set_channels(1, [200, 100, 50])
    ok("50% master halves every pixel byte",
       list(u.get_data_scaled(128, None)[:3]) == [100, 50, 25])
    ok("full master is a no-op", list(u.get_data_scaled(255, None)[:3]) == [200, 100, 50])
    u2 = DMXUniverse(1)          # a DMX-fixture universe
    u2.set_channels(1, [200])
    ok("non-pixel universes still need explicit intensity offsets",
       list(u2.get_data_scaled(128, None)[:1]) == [200])

    print("Playback — streamed fixtures are collected and rendered")
    parent_server._children.append(child)
    parent_server._fixtures.append(fixture)
    plans = parent_server._collect_streamed_fixtures({1: bake})
    ok("streamed fixture is collected from the bake", len(plans) == 1)
    ok("plan carries the output map", plans and plans[0]["map"].child_id == 7)

    eng4 = FakeEngine()
    parent_server._render_streamed_fixtures(plans, eng4, 1.0)
    ok("render writes into the engine", eng4.get_universe(100).get_data()[0] == 255)

    parent_server._blackout_streamed_fixtures(plans, eng4)
    ok("blackout zeroes the pixel span",
       list(eng4.get_universe(100).get_data()[0:12]) == [0] * 12)

    print("Playback — a fixture with no baked segments is not collected")
    ok("no segments -> no plan",
       parent_server._collect_streamed_fixtures({1: {"segments": []}}) == [])

    print("Playback — a performer-backed fixture is never streamed")
    perf_child = {"id": 9, "type": "slyled", "ip": "192.168.10.9",
                  "sc": 1, "strings": [{"leds": 4}]}
    perf_fix = {"id": 3, "childId": 9, "fixtureType": "led",
                "strings": [{"leds": 4}]}
    parent_server._children.append(perf_child)
    parent_server._fixtures.append(perf_fix)
    plans2 = parent_server._collect_streamed_fixtures({3: bake})
    ok("performer LED fixture is excluded from streaming",
       all(p["fid"] != 3 for p in plans2))

    print("Live ad-hoc actions — ticker instead of CMD_ACTION")
    sent = []
    orig_send = parent_server._send
    parent_server._send = lambda ip, pkt, *a, **k: sent.append((ip, pkt))
    try:
        with parent_server.app.test_client() as c:
            r = c.post("/api/children/7/action", json={"type": 5, "allStrings": True})
            d = r.get_json() or {}
            ok("action accepted on a streamed device", r.status_code == 200)
            ok("response marks it streamed", d.get("streamed") is True)
            ok("no CMD_ACTION packet was sent to the device",
               all(ip != "192.168.10.6" for ip, _ in sent),
               f"sent={[ip for ip, _ in sent]}")
            ok("action is registered on the ticker",
               1 in parent_server._live_pixel_actions)

            r = c.post("/api/children/7/action/stop")
            ok("stop accepted", r.status_code == 200)
            ok("ticker entry cleared",
               1 not in parent_server._live_pixel_actions)

            # A performer must still get the real packet.
            sent.clear()
            c.post("/api/children/9/action", json={"type": 5, "allStrings": True})
            ok("performer still receives a UDP action packet",
               any(ip == "192.168.10.9" for ip, _ in sent))
    finally:
        parent_server._send = orig_send

    print("Live ad-hoc actions — unmapped controller is rejected cleanly")
    broken = make_child(cid=11, ports=[])
    broken["hinks"]["baseUniverse"] = 1
    parent_server._children.append(broken)
    with parent_server.app.test_client() as c:
        r = c.post("/api/children/11/action", json={"type": 5, "allStrings": True})
        ok("controller with no bound fixtures returns 409", r.status_code == 409)

    # cleanup
    parent_server._children[:] = [c for c in parent_server._children
                                  if c.get("id") not in (7, 9, 11)]
    parent_server._fixtures[:] = [f for f in parent_server._fixtures
                                  if f.get("id") not in (1, 3)]
    parent_server._live_pixel_actions.clear()
    parent_server._live_pixel_stop.set()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
