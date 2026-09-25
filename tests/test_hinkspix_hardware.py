#!/usr/bin/env python3
"""A HinksPix controller is hardware, not a fixture (#961).

Operator decision 2026-09-24: the controller lives in Setup → Hardware only;
its port-bound LED strings are the fixtures. Covered here:

  * fixture_types.is_pixel_target / is_controller_placeholder
  * the one-off migration removes string-less controller placeholders, carries
    the operator's name onto the controller, and drops their layout positions
    and timeline tracks — port fixtures and performer fixtures untouched
  * POST /api/fixtures refuses a new placeholder
  * /api/fixtures and /api/layout stamp `pixelTarget`
  * /api/children carries the controller summary (#953) and PUT renames it
  * generate_show never puts a track on a controller placeholder

Run: python3 tests/test_hinkspix_hardware.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hphw-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import fixture_types  # noqa: E402
import parent_server as ps  # noqa: E402
import show_generator  # noqa: E402

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


HP, PERF = 8610, 8611
KAZOO, EVE, PERF_FIX, DMX_FIX = 8620, 8621, 8622, 8623


def hinks_child(name="192.168.10.6"):
    return {"id": HP, "type": "hinkspix", "ip": "192.168.10.6", "name": name,
            "sc": 0, "strings": [], "status": 1, "fwVersion": "MS_160",
            "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402,
                      "mcpu": 160, "uploadSupported": True, "protocol": "e131",
                      "dmxOut": {"enabled": False, "universe": None},
                      "ports": [{"port": 17, "leds": 200, "enabled": True},
                                {"port": 18, "leds": 0, "enabled": True}]}}


def rig():
    """The operator's rig: controller placeholder "Kazoo", port fixture
    "Eve Lights", plus a performer LED fixture and a DMX fixture."""
    ps._children[:] = [c for c in ps._children if c["id"] not in (HP, PERF)]
    ps._children.append(hinks_child())
    ps._children.append({"id": PERF, "type": "slyled", "ip": "192.168.10.50",
                         "name": "esp", "sc": 1, "strings": [{"leds": 60}]})
    ps._fixtures[:] = [f for f in ps._fixtures
                       if f["id"] not in (KAZOO, EVE, PERF_FIX, DMX_FIX)]
    ps._fixtures.extend([
        {"id": KAZOO, "name": "Kazoo", "fixtureType": "led", "type": "linear",
         "childId": HP, "strings": []},
        {"id": EVE, "name": "Eve Lights", "fixtureType": "led", "type": "linear",
         "childId": HP, "strings": [{"port": 17, "leds": 200, "mm": 3333}]},
        {"id": PERF_FIX, "name": "Porch", "fixtureType": "led", "type": "linear",
         "childId": PERF, "strings": []},
        {"id": DMX_FIX, "name": "Par", "fixtureType": "dmx", "type": "point",
         "dmxUniverse": 5, "dmxStartAddr": 1, "dmxChannelCount": 3},
    ])
    ps._layout.setdefault("children", [])
    ps._layout["children"] = [p for p in ps._layout["children"]
                              if p.get("id") not in (KAZOO, EVE)]
    ps._layout["children"] += [{"id": KAZOO, "x": 100, "y": 0, "z": 0},
                               {"id": EVE, "x": 500, "y": 0, "z": 2500}]
    ps._timelines[:] = [t for t in ps._timelines if t.get("id") != 8630]
    ps._timelines.append({"id": 8630, "name": "t", "durationS": 10, "tracks": [
        {"fixtureId": KAZOO, "clips": []}, {"fixtureId": EVE, "clips": []}]})


def main():
    by_id = {HP: hinks_child(), PERF: {"id": PERF, "type": "slyled"}}

    print("Predicate — is_pixel_target")
    ok("controller placeholder (no strings) is not a target",
       not fixture_types.is_pixel_target(
           {"fixtureType": "led", "childId": HP, "strings": []}, by_id))
    ok("port fixture with pixels is a target",
       fixture_types.is_pixel_target(
           {"fixtureType": "led", "childId": HP, "strings": [{"port": 17, "leds": 200}]}, by_id))
    ok("port fixture whose strings have 0 pixels is not a target",
       not fixture_types.is_pixel_target(
           {"fixtureType": "led", "childId": HP, "strings": [{"port": 18, "leds": 0}]}, by_id))
    ok("performer LED fixture (strings live on the child) is a target",
       fixture_types.is_pixel_target({"fixtureType": "led", "childId": PERF}, by_id))
    ok("LED fixture with no fixtureType field defaults to LED",
       fixture_types.is_pixel_target({"childId": PERF}, by_id))
    ok("DMX fixture is not a pixel target",
       not fixture_types.is_pixel_target({"fixtureType": "dmx"}, by_id))
    ok("LED group is a target", fixture_types.is_pixel_target(
        {"fixtureType": "led", "type": "group", "childIds": [KAZOO]}, by_id))

    print("Migration — placeholders removed, name carried onto the controller")
    rig()
    n = ps._migrate_controller_placeholders()
    ids = {f["id"] for f in ps._fixtures}
    ok("one placeholder removed", n == 1, n)
    ok("'Kazoo' fixture is gone", KAZOO not in ids)
    ok("'Eve Lights' port fixture kept", EVE in ids)
    ok("performer LED fixture kept", PERF_FIX in ids)
    ok("DMX fixture kept", DMX_FIX in ids)
    hp = next(c for c in ps._children if c["id"] == HP)
    ok("controller named 'Kazoo' (was its IP)", hp["name"] == "Kazoo", hp["name"])
    ok("placeholder's layout position dropped",
       all(p.get("id") != KAZOO for p in ps._layout["children"]))
    ok("port fixture's layout position kept",
       any(p.get("id") == EVE for p in ps._layout["children"]))
    tl = next(t for t in ps._timelines if t["id"] == 8630)
    ok("track on the placeholder dropped, port-fixture track kept",
       [t["fixtureId"] for t in tl["tracks"]] == [EVE], tl["tracks"])
    ok("second run is a no-op", ps._migrate_controller_placeholders() == 0)

    print("Migration — an operator-named controller keeps its own name")
    rig()
    next(c for c in ps._children if c["id"] == HP)["name"] = "Garage controller"
    ps._migrate_controller_placeholders()
    ok("controller name not overwritten",
       next(c for c in ps._children if c["id"] == HP)["name"] == "Garage controller")

    c = ps.app.test_client()
    rig()
    print("API — POST /api/fixtures refuses a new placeholder")
    r = c.post("/api/fixtures", json={"name": "HP", "fixtureType": "led",
                                      "type": "linear", "childId": HP})
    ok("400 for a string-less fixture on a HinksPix", r.status_code == 400,
       f"{r.status_code} {r.get_json()}")
    ok("error says it's hardware", "hardware" in (r.get_json() or {}).get("err", ""))
    r = c.post("/api/fixtures", json={"name": "Porch 2", "fixtureType": "led",
                                      "type": "linear", "childId": PERF})
    ok("performer LED fixture still creatable", r.status_code == 200, r.get_json())
    if r.status_code == 200:
        c.delete(f"/api/fixtures/{r.get_json()['id']}")

    print("API — pixelTarget stamped on fixture lists")
    fx = {f["id"]: f for f in c.get("/api/fixtures").get_json()}
    ok("/api/fixtures: placeholder pixelTarget false", fx[KAZOO]["pixelTarget"] is False)
    ok("/api/fixtures: port fixture pixelTarget true", fx[EVE]["pixelTarget"] is True)
    ok("/api/fixtures: performer fixture pixelTarget true", fx[PERF_FIX]["pixelTarget"] is True)
    ok("/api/fixtures: DMX fixture pixelTarget false (not a pixel fixture)",
       fx[DMX_FIX]["pixelTarget"] is False)
    lay = {f["id"]: f for f in c.get("/api/layout").get_json()["fixtures"]}
    ok("/api/layout: placeholder pixelTarget false", lay[KAZOO]["pixelTarget"] is False)
    ok("/api/layout: port fixture pixelTarget true", lay[EVE]["pixelTarget"] is True)

    print("API — controller summary and rename (Setup → Hardware row)")
    kids = {k["id"]: k for k in c.get("/api/children").get_json()}
    summ = kids[HP].get("hinksSummary") or {}
    ok("1 port configured (port 18 has 0 pixels)", summ.get("portsConfigured") == 1, summ)
    ok("2 universes for 200 RGB pixels", summ.get("universes") == 2, summ)
    ok("universes 1-2", (summ.get("firstUniverse"), summ.get("lastUniverse")) == (1, 2), summ)
    ok("200 pixels", summ.get("pixels") == 200, summ)
    ok("performer child has no hinksSummary", "hinksSummary" not in kids[PERF])
    r = c.put(f"/api/children/{HP}", json={"name": "  Kazoo  "})
    ok("rename ok", r.status_code == 200 and r.get_json()["name"] == "Kazoo", r.get_json())
    ok("rename rejects empty name",
       c.put(f"/api/children/{HP}", json={"name": " "}).status_code == 400)
    ok("rename 404 for unknown child",
       c.put("/api/children/999999", json={"name": "x"}).status_code == 404)

    print("show_generator — never a track on a controller placeholder")
    fixtures = [f for f in ps._fixtures if f["id"] in (KAZOO, EVE, PERF_FIX)]
    layout = {"children": [{"id": KAZOO, "x": 100, "y": 0, "z": 0},
                           {"id": EVE, "x": 500, "y": 0, "z": 2500},
                           {"id": PERF_FIX, "x": 900, "y": 0, "z": 0}]}
    stage = {"w": 5.0, "h": 3.0, "d": 4.0}
    bad = []
    for theme in list(show_generator.THEMES):
        show = show_generator.generate_show(theme, fixtures, layout, stage,
                                            children=ps._children)
        if not show or show.get("error"):
            continue
        tracks = (show.get("timeline") or {}).get("tracks") or show.get("tracks") or []
        targets = {t.get("fixtureId") for t in tracks}
        if KAZOO in targets or KAZOO in (show.get("led_fixtures") or []):
            bad.append(theme)
    ok("no theme targets the placeholder", not bad, bad)
    show = show_generator.generate_show("rainbow-across", fixtures, layout, stage,
                                        children=ps._children)
    tracks = (show.get("timeline") or {}).get("tracks") or show.get("tracks") or []
    ok("the port fixture is still targeted",
       EVE in {t.get("fixtureId") for t in tracks} or EVE in (show.get("led_fixtures") or []),
       [t.get("fixtureId") for t in tracks])

    # cleanup
    ps._children[:] = [x for x in ps._children if x["id"] not in (HP, PERF)]
    ps._fixtures[:] = [f for f in ps._fixtures
                       if f["id"] not in (KAZOO, EVE, PERF_FIX, DMX_FIX)]
    ps._timelines[:] = [t for t in ps._timelines if t.get("id") != 8630]

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
