#!/usr/bin/env python3
"""test_911_radar_fixture.py — #911 (server half) `radar` fixture type.

The MMwave radar node (ESP32-C61 + Rd-03D) is a placeable fixture:
standard pose (layout position + rotation, stage Z-up mm) plus the
radar-specific fields `radarNode` (reporting node's PONG hostname),
`rangeMm`, `fovDeg`, `radarEnabled`. Registered through the #899
fixture-type registry — the POST/PUT routes were not edited.

Covered here (all through the live Flask routes):
  1. Create with defaults (rangeMm 8000, fovDeg 120, radarEnabled True,
     radarNode None) and create with explicit fields.
  2. Per-field validation on POST and PUT (radarNode/rangeMm/fovDeg/
     radarEnabled), and that radar's wider fovDeg band (1-360) applies
     to radar — not camera — fixtures.
  3. Generic-PUT whitelist accepts the radar fields.
  4. Registry surface: capabilities (tracks_people=True, has_dmx=False,
     placeable=True) and the unknown-type error naming 'radar'.
  5. Pose fields ride the standard layout store (radar is placeable).

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_911_radar_fixture.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-911-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import fixture_types  # noqa: E402
import parent_server as ps  # noqa: E402
from parent_server import app  # noqa: E402

results = []
_created = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _post(c, payload):
    r = c.post("/api/fixtures", json=payload)
    if r.status_code == 200:
        _created.append((r.get_json() or {}).get("id"))
    return r


def run_create_defaults(c):
    r = _post(c, {"name": "R1", "type": "point", "fixtureType": "radar"})
    ok("radar create (no radar fields) → 200", r.status_code == 200,
       r.data[:120])
    fid = (r.get_json() or {}).get("id")
    f = next((x for x in ps._fixtures if x["id"] == fid), {})
    ok("radarNode defaults to None (optional at create)",
       "radarNode" in f and f["radarNode"] is None, repr(f))
    ok("rangeMm defaults to 8000 (Rd-03D ~8 m)", f.get("rangeMm") == 8000)
    ok("fovDeg defaults to 120 (±60° wedge)", f.get("fovDeg") == 120)
    ok("radarEnabled defaults to True", f.get("radarEnabled") is True)
    ok("standard placeable fields present (rotation)",
       f.get("rotation") == [0, 0, 0], repr(f.get("rotation")))
    return fid


def run_create_explicit(c):
    r = _post(c, {"name": "R2", "type": "point", "fixtureType": "radar",
                  "radarNode": "MMW-A1B2", "rangeMm": 6000, "fovDeg": 90,
                  "radarEnabled": False, "rotation": [10, 0, -45]})
    ok("radar create with explicit fields → 200", r.status_code == 200,
       r.data[:120])
    fid = (r.get_json() or {}).get("id")
    f = next((x for x in ps._fixtures if x["id"] == fid), {})
    ok("explicit fields stamped",
       f.get("radarNode") == "MMW-A1B2" and f.get("rangeMm") == 6000
       and f.get("fovDeg") == 90 and f.get("radarEnabled") is False,
       repr(f))
    ok("rotation stored per stage conventions", f.get("rotation") == [10, 0, -45])
    return fid


def run_validation(c, fid):
    # radarNode
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "radarNode": 42})
    ok("radarNode non-string → 400", r.status_code == 400, r.data[:120])
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "radarNode": "   "})
    ok("radarNode blank string → 400", r.status_code == 400)
    # rangeMm
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "rangeMm": "8m"})
    ok("rangeMm non-int → 400", r.status_code == 400)
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "rangeMm": 50})
    ok("rangeMm below band → 400", r.status_code == 400)
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "rangeMm": True})
    ok("rangeMm bool rejected (not an int)", r.status_code == 400)
    # fovDeg
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "fovDeg": 0})
    ok("fovDeg 0 → 400", r.status_code == 400)
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "fovDeg": 361})
    ok("fovDeg 361 → 400", r.status_code == 400)
    r = _post(c, {"name": "Rok360", "type": "point", "fixtureType": "radar",
                  "fovDeg": 360})
    ok("fovDeg 360 valid for radar (wider than camera's 1-180)",
       r.status_code == 200, r.data[:120])
    # camera keeps its own tighter band — the radar validator must not
    # have leaked onto camera creates.
    r = _post(c, {"name": "CamStill180", "type": "point",
                  "fixtureType": "camera", "fovDeg": 360})
    ok("camera fovDeg 360 still rejected (per-type validators)",
       r.status_code == 400)
    # radarEnabled
    r = _post(c, {"name": "Rbad", "type": "point", "fixtureType": "radar",
                  "radarEnabled": "yes"})
    ok("radarEnabled non-bool → 400", r.status_code == 400)

    # PUT-side validation on an existing radar fixture.
    r = c.put(f"/api/fixtures/{fid}", json={"rangeMm": 999999})
    ok("PUT rangeMm out of band → 400", r.status_code == 400)
    r = c.put(f"/api/fixtures/{fid}", json={"fovDeg": -5})
    ok("PUT fovDeg out of band → 400", r.status_code == 400)
    r = c.put(f"/api/fixtures/{fid}", json={"radarNode": ""})
    ok("PUT blank radarNode → 400", r.status_code == 400)
    r = c.put(f"/api/fixtures/{fid}", json={"radarEnabled": 1})
    ok("PUT non-bool radarEnabled → 400", r.status_code == 400)


def run_update(c, fid):
    r = c.put(f"/api/fixtures/{fid}",
              json={"radarNode": "MMW-C3D4", "rangeMm": 7000,
                    "radarEnabled": False})
    ok("PUT valid radar fields → 200", r.status_code == 200, r.data[:120])
    f = next((x for x in ps._fixtures if x["id"] == fid), {})
    ok("PUT persisted radar fields",
       f.get("radarNode") == "MMW-C3D4" and f.get("rangeMm") == 7000
       and f.get("radarEnabled") is False, repr(f))
    r = c.put(f"/api/fixtures/{fid}", json={"radarNode": None})
    ok("PUT radarNode=None (unbind) → 200", r.status_code == 200)
    f = next((x for x in ps._fixtures if x["id"] == fid), {})
    ok("radarNode unbound", f.get("radarNode") is None, repr(f))


def run_registry_surface():
    desc = fixture_types.FIXTURE_TYPES.get("radar")
    ok("radar registered in FIXTURE_TYPES", desc is not None)
    ok("radar capabilities: tracks_people=True, has_dmx=False, placeable=True",
       desc is not None
       and desc.capabilities.get("tracks_people") is True
       and desc.capabilities.get("has_dmx") is False
       and desc.capabilities.get("placeable") is True,
       repr(getattr(desc, "capabilities", None)))
    wl = fixture_types.update_field_whitelist()
    ok("generic-PUT whitelist gained the radar fields",
       all(k in wl for k in ("radarNode", "rangeMm", "radarEnabled")),
       repr(wl))
    ok("unknown-type 400 message names radar",
       "'radar'" in fixture_types.invalid_type_error(),
       fixture_types.invalid_type_error())


def run_layout_pose(c, fid):
    # Placeable: position rides the standard layout store, like cameras.
    r = c.post("/api/layout", json={"children": [
        {"id": fid, "x": 1200, "y": 300, "z": 1800}], "force": True})
    ok("layout save with radar position → 200", r.status_code == 200)
    r = c.get("/api/layout")
    lay = r.get_json() or {}
    rf = next((x for x in lay.get("fixtures", []) if x.get("id") == fid), {})
    ok("radar fixture positioned in layout",
       rf.get("positioned") and rf.get("x") == 1200 and rf.get("y") == 300
       and rf.get("z") == 1800, repr({k: rf.get(k) for k in ("x", "y", "z", "positioned")}))


def _cleanup():
    if _created:
        with ps._lock:
            ps._fixtures[:] = [f for f in ps._fixtures
                               if f.get("id") not in set(_created)]
            ps._save("fixtures", ps._fixtures)


def main():
    c = app.test_client()
    try:
        fid1 = run_create_defaults(c)
        fid2 = run_create_explicit(c)
        run_validation(c, fid2)
        run_update(c, fid2)
        run_registry_surface()
        run_layout_pose(c, fid1)
    finally:
        _cleanup()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
