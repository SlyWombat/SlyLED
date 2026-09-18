#!/usr/bin/env python3
"""HinksPix PRO first-class device tests (#939).

Covers the pure mapping/encoding layer and the Flask device routes:
  * PixelOutputMap universe layout, multi-universe ports, DMX-out span
  * universe collision detection (other controllers + DMX fixtures)
  * hinkspix_bridge encoder tables and the MCPU>=151 upload gate
  * device config PUT validation and the universeRoutes upsert
  * port-bound fixture validation (range, duplicates, cross-fixture conflict)
  * fixtures-from-ports
  * _is_performer guards — a HinksPix must never receive a performer UDP packet

The last one is the load-bearing safety property: performer wire structs are
hard-sized for 8 strings (MAX_STR_PER_CHILD) and a 48-port controller entering
that path would be a protocol violation, so the guards are asserted by
capturing every _send() rather than by inspection.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_device.py
(SLYLED_DATA matters — see #942; without it this writes the live data dir.)
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hinkspix-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import app  # noqa: E402
import hinkspix_bridge as hb  # noqa: E402
from pixel_output import PixelOutputMap, UniverseCollision  # noqa: E402

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


def make_child(cid=7, base=100, ports=None, dmx_out=None, ip="192.168.10.6"):
    return {
        "id": cid, "type": "hinkspix", "boardType": "HinksPix PRO",
        "ip": ip, "name": "Roofline", "status": 1, "seen": 0,
        "sc": 0, "strings": [],
        "hinks": {
            "model": "HinksPix PRO", "hardwareV3": False, "mcpu": 160,
            "maxU": 402, "uploadSupported": True, "boards": {},
            "protocol": "e131", "baseUniverse": base,
            "dmxOut": dmx_out or {"enabled": False, "universe": None},
            "ports": ports if ports is not None else [
                {"port": 1, "leds": 100, "enabled": True},
                {"port": 2, "leds": 300, "enabled": True},
            ],
            "configPushedAt": 0, "configHash": "",
        },
    }


def main():
    print("PixelOutputMap — universe layout")
    m = PixelOutputMap.build(make_child())
    ok("port 1 (100px) takes one universe at baseUniverse",
       m.spans_for_port(1)[0]["universe"] == 100 and len(m.spans_for_port(1)) == 1)
    ok("port 2 (300px) spans two universes", len(m.spans_for_port(2)) == 2)
    ok("each port starts on a fresh universe",
       m.spans_for_port(2)[0]["universe"] == 101)
    ok("170px fills one universe exactly",
       m.spans_for_port(2)[0]["pixels"] == 170)
    ok("absStart is controller-absolute and contiguous",
       [s["absStart"] for s in m.spans] == [1, 301, 811])
    ok("total channels = pixels x 3", m.total_channels == (100 + 300) * 3)
    ok("pixel universes are reported for all-intensity scaling",
       m.pixel_universes == [100, 101, 102])

    disabled = make_child(ports=[{"port": 1, "leds": 100, "enabled": False},
                                 {"port": 2, "leds": 50, "enabled": True}])
    m2 = PixelOutputMap.build(disabled)
    ok("disabled ports are skipped entirely", m2.spans_for_port(1) == [])
    ok("enabled port still starts at baseUniverse",
       m2.spans_for_port(2)[0]["universe"] == 100)

    zero = PixelOutputMap.build(make_child(ports=[{"port": 1, "leds": 0, "enabled": True}]))
    ok("zero-pixel port consumes no universe", zero.universes == [])

    print("PixelOutputMap — DMX-out trailing span")
    md = PixelOutputMap.build(make_child(dmx_out={"enabled": True, "universe": 200}))
    ok("DMX-out appends a 512-channel span", md.dmx_span["channels"] == 512)
    ok("DMX-out lands after all pixel data",
       md.dmx_span["absStart"] == (100 + 300) * 3 + 1)
    ok("DMX-out universe is not an all-intensity pixel universe",
       200 not in md.pixel_universes)
    try:
        PixelOutputMap.build(make_child(dmx_out={"enabled": True, "universe": None}))
        ok("DMX-out without a universe is rejected", False)
    except ValueError:
        ok("DMX-out without a universe is rejected", True)

    print("PixelOutputMap — collisions")
    a = PixelOutputMap.build(make_child(cid=7, base=100))
    b = PixelOutputMap.build(make_child(cid=8, base=102))   # overlaps a's 102
    try:
        a.check_collisions([b], [])
        ok("overlapping controllers collide", False)
    except UniverseCollision:
        ok("overlapping controllers collide", True)
    far = PixelOutputMap.build(make_child(cid=8, base=500))
    try:
        a.check_collisions([far], [])
        ok("non-overlapping controllers are fine", True)
    except UniverseCollision as exc:
        ok("non-overlapping controllers are fine", False, str(exc))
    try:
        a.check_collisions([], [101])
        ok("collision with a DMX fixture universe is caught", False)
    except UniverseCollision:
        ok("collision with a DMX fixture universe is caught", True)
    try:
        a.check_collisions([a], [])
        ok("a map does not collide with itself", True)
    except UniverseCollision:
        ok("a map does not collide with itself", False)

    print("PixelOutputMap — Art-Net route rows")
    rows = a.route_rows("192.168.10.6")
    ok("one route row per universe", len(rows) == len(a.universes))
    ok("route rows carry the child label",
       all(r["label"] == "hinkspix:7" and r["destination"] == "192.168.10.6"
           for r in rows))

    print("hinkspix_bridge — encoder tables (from HinksPix.cpp)")
    ok("ws2811 -> 1", hb.encode_protocol("ws2811") == 1)
    ok("apa102 -> 7", hb.encode_protocol("APA102") == 7)
    ok("unknown protocol falls back to ws2811", hb.encode_protocol("nope") == 1)
    ok("rgb -> 0 / grb -> 2 / wrgb -> 7",
       (hb.encode_color_order("RGB"), hb.encode_color_order("grb"),
        hb.encode_color_order("WRGB")) == (0, 2, 7))
    ok("brightness truncates to the 10s", hb.encode_brightness(77) == 70)
    ok("brightness under 20 clamps to 15 (not 0)", hb.encode_brightness(12) == 15)
    ok("brightness 100 stays 100", hb.encode_brightness(100) == 100)
    ok("gamma clamps to 1..4",
       (hb.encode_gamma(0), hb.encode_gamma(3), hb.encode_gamma(9)) == (1, 3, 4))
    ok("direction Reverse -> 1", hb.encode_direction("Reverse") == 1)

    print("hinkspix_bridge — firmware upload gate")
    ok("MS_149 parses to 149", hb.parse_version("MS_149") == 149)
    ok("MCPU 149 cannot upload (the operator's own unit)",
       hb.supports_upload(149) is False)
    ok("MCPU 151 can upload", hb.supports_upload(151) is True)
    ok("PRO 80 / hardware V3 gate is 129",
       hb.supports_upload(129, hardware_v3=True) is True)
    ok("unparseable version cannot upload", hb.supports_upload(None) is False)

    print("hinkspix_bridge — universe table rows")
    spans = PixelOutputMap.build(make_child()).spans
    rows = hb.build_universe_rows(spans, 10)
    ok("first row maps universe + absolute channel range",
       rows[0] == "0,100,300,1,1,300")
    ok("unused rows up to maxU are zero-length", rows[5] == "5,5,0,1,0,0")
    ok("row count reaches maxU", len(rows) == 10)

    with app.test_client() as c:
        print("Device routes — config + validation")
        child = make_child()
        parent_server._children.append(child)

        r = c.get("/api/hinkspix/7")
        ok("GET device config", r.status_code == 200 and r.get_json().get("ok"))
        ok("GET includes the computed map",
           (r.get_json().get("map") or {}).get("universes") == [100, 101, 102])

        r = c.put("/api/hinkspix/7", json={"baseUniverse": 0})
        ok("baseUniverse 0 rejected", r.status_code == 400)
        r = c.put("/api/hinkspix/7", json={"protocol": "telepathy"})
        ok("unknown protocol rejected", r.status_code == 400)
        r = c.put("/api/hinkspix/7", json={"ports": [{"port": 99, "leds": 10}]})
        ok("port 99 rejected (>48)", r.status_code == 400)
        r = c.put("/api/hinkspix/7", json={"ports": [{"port": 1, "leds": 10},
                                                     {"port": 1, "leds": 20}]})
        ok("duplicate port rejected", r.status_code == 400)
        r = c.put("/api/hinkspix/7", json={"dmxOut": {"enabled": True}})
        ok("dmxOut enabled without universe rejected", r.status_code == 400)

        r = c.put("/api/hinkspix/7", json={"baseUniverse": 100, "ports": [
            {"port": 1, "leds": 100, "enabled": True},
            {"port": 2, "leds": 300, "enabled": True}]})
        ok("valid config accepted", r.status_code == 200)
        ok("mm defaults to 60px/m stage-mm, not a DMX fraction",
           (r.get_json()["hinks"]["ports"][0]["mm"]) == int(round(100 * 16.67)))

        routes = parent_server._dmx_settings.get("universeRoutes") or []
        mine = [x for x in routes if x.get("label") == "hinkspix:7"]
        ok("universeRoutes upserted for Art-Net unicast", len(mine) == 3)
        r = c.put("/api/hinkspix/7", json={"ports": [{"port": 1, "leds": 100,
                                                      "enabled": True}]})
        routes = parent_server._dmx_settings.get("universeRoutes") or []
        mine = [x for x in routes if x.get("label") == "hinkspix:7"]
        ok("universeRoutes are replaced, not duplicated, on re-save", len(mine) == 1)

        print("Device routes — collision rejection through the API")
        other = make_child(cid=8, base=100, ip="192.168.10.7")
        parent_server._children.append(other)
        r = c.put("/api/hinkspix/7", json={"baseUniverse": 100, "ports": [
            {"port": 1, "leds": 100, "enabled": True}]})
        ok("config colliding with another controller is rejected",
           r.status_code == 400 and "already used" in (r.get_json().get("err") or ""))
        parent_server._children.remove(other)

        r = c.put("/api/hinkspix/7", json={"baseUniverse": 100, "ports": [
            {"port": 1, "leds": 100, "enabled": True},
            {"port": 2, "leds": 300, "enabled": True}]})
        ok("config restored after collision test", r.status_code == 200)

        print("Fixtures — port binding validation")
        r = c.post("/api/fixtures", json={"name": "Bad port", "fixtureType": "led",
                                          "type": "linear", "childId": 7,
                                          "strings": [{"port": 99, "leds": 100}]})
        ok("port out of range rejected on create", r.status_code == 400)
        r = c.post("/api/fixtures", json={"name": "Dup", "fixtureType": "led",
                                          "type": "linear", "childId": 7,
                                          "strings": [{"port": 1, "leds": 100},
                                                      {"port": 1, "leds": 100}]})
        ok("duplicate port within one fixture rejected", r.status_code == 400)
        r = c.post("/api/fixtures", json={"name": "Wrong count", "fixtureType": "led",
                                          "type": "linear", "childId": 7,
                                          "strings": [{"port": 1, "leds": 999}]})
        ok("leds disagreeing with the device rejected", r.status_code == 400)
        r = c.post("/api/fixtures", json={"name": "Roof L", "fixtureType": "led",
                                          "type": "linear", "childId": 7,
                                          "strings": [{"port": 1, "leds": 100}]})
        ok("valid port-bound fixture accepted", r.status_code == 200)
        first_fid = (r.get_json() or {}).get("id")
        r = c.post("/api/fixtures", json={"name": "Roof L dup", "fixtureType": "led",
                                          "type": "linear", "childId": 7,
                                          "strings": [{"port": 1, "leds": 100}]})
        ok("port already bound to another fixture rejected", r.status_code == 400)

        print("Fixtures — create from ports")
        r = c.post("/api/hinkspix/7/fixtures-from-ports")
        d = r.get_json() or {}
        ok("fixtures-from-ports succeeds", r.status_code == 200 and d.get("ok"))
        ok("already-bound port 1 is skipped, not duplicated", d.get("skipped") == [1])
        ok("port 2 gets a fixture",
           [x["port"] for x in d.get("created", [])] == [2])
        made = next((f for f in parent_server._fixtures
                     if f["id"] == d["created"][0]["id"]), None)
        ok("created fixture is an ordinary LED fixture (no new type)",
           made and made["fixtureType"] == "led" and made["type"] == "linear")
        ok("created fixture carries stage-mm geometry",
           made and made["strings"][0]["mm"] == int(round(300 * 16.67)))

        print("Safety — a HinksPix must never get a performer UDP packet")
        ok("_is_performer(hinkspix) is False", parent_server._is_performer(child) is False)
        ok("_is_performer(slyled) is True",
           parent_server._is_performer({"type": "slyled"}) is True)
        ok("_is_performer(legacy child with no type) is True",
           parent_server._is_performer({"ip": "10.0.0.5"}) is True)
        ok("_is_performer(wled) is False",
           parent_server._is_performer({"type": "wled"}) is False)

        sent = []
        orig_send = parent_server._send
        parent_server._send = lambda ip, pkt, *a, **k: sent.append((ip, pkt))
        try:
            c.post("/api/show/start", json={})
            c.post("/api/timelines/1/baked/sync")
        finally:
            parent_server._send = orig_send
        ok("no UDP packet was addressed to the HinksPix",
           all(ip != "192.168.10.6" for ip, _ in sent),
           f"sent={[ip for ip, _ in sent]}")

        # cleanup
        parent_server._children[:] = [x for x in parent_server._children if x["id"] != 7]
        parent_server._fixtures[:] = [f for f in parent_server._fixtures
                                      if f.get("childId") != 7]

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
