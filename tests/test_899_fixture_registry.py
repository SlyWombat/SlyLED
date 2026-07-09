#!/usr/bin/env python3
"""test_899_fixture_registry.py — #899 (server half) fixture-type registry.

POST/PUT /api/fixtures validation and the create/update whitelists now
route through desktop/shared/fixture_types.py::FIXTURE_TYPES instead of
inline literals, so a new sensor type (radar, #911) is a registration,
not a route edit.

Covered here (all through the live Flask routes):
  1. Each existing type (led/dmx/camera/gyro) creates + updates with the
     same validation outcomes as pre-#899, including the per-type error
     cases and default stamping.
  2. Unknown fixtureType still rejects on POST and PUT (same message).
  3. Geometry type axis (linear/point/surface/group) unchanged — and
     "surface" remains a geometry, not a fixtureType.
  4. A test-registered dummy type becomes creatable/updatable without
     touching the route code; unregistering makes it invalid again.
  5. Registry surface: whitelist union parity + capability flags.

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_899_fixture_registry.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-899-")

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


def run_existing_types(c):
    # led — plain create, no per-type block.
    r = _post(c, {"name": "L1", "type": "linear", "fixtureType": "led"})
    ok("led create 200", r.status_code == 200, r.data[:120])
    led_id = (r.get_json() or {}).get("id")

    # dmx — required-field validation identical to pre-#899.
    r = _post(c, {"name": "D-bad", "type": "point", "fixtureType": "dmx"})
    ok("dmx create without universe → 400", r.status_code == 400)
    ok("dmx error text preserved",
       (r.get_json() or {}).get("err") == "dmxUniverse must be an integer >= 1",
       r.data[:120])
    r = _post(c, {"name": "D-bad2", "type": "point", "fixtureType": "dmx",
                  "dmxUniverse": 1, "dmxStartAddr": 700, "dmxChannelCount": 8})
    ok("dmx create addr>512 → 400", r.status_code == 400)
    r = _post(c, {"name": "D1", "type": "point", "fixtureType": "dmx",
                  "dmxUniverse": 1, "dmxStartAddr": 10, "dmxChannelCount": 16})
    ok("dmx create valid → 200", r.status_code == 200, r.data[:120])
    dmx_id = (r.get_json() or {}).get("id")
    f = next((x for x in ps._fixtures if x["id"] == dmx_id), {})
    ok("dmx per-type fields stamped",
       f.get("dmxUniverse") == 1 and f.get("dmxStartAddr") == 10
       and f.get("dmxChannelCount") == 16 and "dmxProfileId" in f, repr(f))

    # camera — fov validation + default stamping identical to pre-#899.
    r = _post(c, {"name": "C-bad", "type": "point", "fixtureType": "camera",
                  "fovDeg": 500})
    ok("camera create fovDeg>180 → 400", r.status_code == 400)
    r = _post(c, {"name": "C-bad2", "type": "point", "fixtureType": "camera",
                  "fovType": "sideways"})
    ok("camera create bad fovType → 400", r.status_code == 400)
    r = _post(c, {"name": "C1", "type": "point", "fixtureType": "camera"})
    ok("camera create valid → 200", r.status_code == 200, r.data[:120])
    cam_id = (r.get_json() or {}).get("id")
    f = next((x for x in ps._fixtures if x["id"] == cam_id), {})
    ok("camera defaults stamped (fovDeg/fovType/track*)",
       f.get("fovDeg") == 60 and f.get("fovType") == "diagonal"
       and f.get("trackClasses") == ["person"] and f.get("trackFps") == 2
       and f.get("trackInputSize") == 320, repr(f))

    # gyro — defaults stamped.
    r = _post(c, {"name": "G1", "type": "point", "fixtureType": "gyro"})
    ok("gyro create valid → 200", r.status_code == 200, r.data[:120])
    gyro_id = (r.get_json() or {}).get("id")
    f = next((x for x in ps._fixtures if x["id"] == gyro_id), {})
    ok("gyro defaults stamped",
       "gyroChildId" in f and "assignedMoverId" in f
       and f.get("gyroEnabled") is False, repr(f))

    # PUT validation identical.
    r = c.put(f"/api/fixtures/{dmx_id}", json={"dmxStartAddr": 999})
    ok("dmx PUT addr>512 → 400", r.status_code == 400)
    r = c.put(f"/api/fixtures/{dmx_id}", json={"dmxStartAddr": 20})
    ok("dmx PUT valid addr → 200", r.status_code == 200)
    r = c.put(f"/api/fixtures/{cam_id}", json={"trackFps": 99})
    ok("camera PUT trackFps out of band → 400", r.status_code == 400)
    r = c.put(f"/api/fixtures/{cam_id}", json={"trackFps": 4,
                                               "fovType": "HORIZONTAL "})
    ok("camera PUT valid fields → 200", r.status_code == 200)
    f = next((x for x in ps._fixtures if x["id"] == cam_id), {})
    ok("camera PUT normalises fovType on write",
       f.get("fovType") == "horizontal", repr(f.get("fovType")))
    # Pre-#899 flat-whitelist semantics: another type's field is still
    # accepted on any fixture (membership union, not per-type gating).
    r = c.put(f"/api/fixtures/{led_id}", json={"aoeRadius": 1500})
    ok("led PUT common field → 200", r.status_code == 200)


def run_unknown_type(c):
    r = _post(c, {"name": "Bad", "type": "point", "fixtureType": "lidar"})
    ok("unknown type POST → 400", r.status_code == 400)
    # #911 — the radar registration extends the message; wording pattern
    # (registration-order listing, Oxford "or") is what's pinned.
    ok("unknown-type error message preserved",
       (r.get_json() or {}).get("err")
       == "Invalid fixtureType - must be 'led', 'dmx', 'camera', 'gyro', or 'radar'",
       r.data[:160])
    # PUT with unknown type on an existing fixture.
    r = _post(c, {"name": "L2", "type": "linear", "fixtureType": "led"})
    fid = (r.get_json() or {}).get("id")
    r = c.put(f"/api/fixtures/{fid}", json={"fixtureType": "foo"})
    ok("unknown type PUT → 400", r.status_code == 400)


def run_geometry_axis(c):
    # "surface" is a geometry type, not a fixtureType (#899 verification).
    r = _post(c, {"name": "S1", "type": "surface", "fixtureType": "led"})
    ok("geometry type=surface still valid → 200", r.status_code == 200,
       r.data[:120])
    r = _post(c, {"name": "S2", "type": "surface", "fixtureType": "surface"})
    ok("fixtureType=surface rejected (geometry ≠ fixture type)",
       r.status_code == 400)
    r = _post(c, {"name": "S3", "type": "bogus", "fixtureType": "led"})
    ok("bad geometry type still → 400", r.status_code == 400)


def run_dummy_registration(c):
    dummy = fixture_types.FixtureTypeDescriptor(
        "testdummy",
        fields=("dummyKnob",),
        validate_create=lambda body: (
            "dummyKnob must be <= 100"
            if isinstance(body.get("dummyKnob"), (int, float))
            and body["dummyKnob"] > 100 else None),
        apply_create=lambda f, body: f.__setitem__(
            "dummyKnob", body.get("dummyKnob", 42)),
        capabilities={"tracks_people": True},
    )
    fixture_types.register_fixture_type(dummy)
    try:
        r = _post(c, {"name": "DU1", "type": "point",
                      "fixtureType": "testdummy", "dummyKnob": 7})
        ok("registered dummy type creates without route edits",
           r.status_code == 200, r.data[:120])
        fid = (r.get_json() or {}).get("id")
        f = next((x for x in ps._fixtures if x["id"] == fid), {})
        ok("dummy apply_create stamped its field",
           f.get("dummyKnob") == 7, repr(f))
        r = _post(c, {"name": "DU2", "type": "point",
                      "fixtureType": "testdummy", "dummyKnob": 500})
        ok("dummy validate_create enforced", r.status_code == 400,
           r.data[:120])
        r = c.put(f"/api/fixtures/{fid}", json={"dummyKnob": 9})
        ok("dummy field accepted by generic PUT whitelist",
           r.status_code == 200)
        f = next((x for x in ps._fixtures if x["id"] == fid), {})
        ok("dummy field updated", f.get("dummyKnob") == 9, repr(f))
        ok("dummy capabilities defaulted + overridable",
           dummy.capabilities.get("tracks_people") is True
           and dummy.capabilities.get("has_dmx") is False
           and dummy.capabilities.get("placeable") is True,
           repr(dummy.capabilities))
    finally:
        del fixture_types.FIXTURE_TYPES["testdummy"]
    r = _post(c, {"name": "DU3", "type": "point", "fixtureType": "testdummy"})
    ok("unregistered dummy type invalid again", r.status_code == 400)


def run_registry_surface():
    ok("registry holds exactly the built-in types",
       list(fixture_types.FIXTURE_TYPES) == ["led", "dmx", "camera", "gyro",
                                             "radar"],  # radar since #911
       repr(list(fixture_types.FIXTURE_TYPES)))
    wl = fixture_types.update_field_whitelist()
    ok("whitelist starts with the common fields in order",
       wl[:len(fixture_types.COMMON_UPDATE_FIELDS)]
       == fixture_types.COMMON_UPDATE_FIELDS, repr(wl))
    ok("trackInputSize create-only (not in PUT whitelist, pre-#899 parity)",
       "trackInputSize" not in wl)
    ok("smoothing stays out of the PUT whitelist (#877)",
       "smoothing" not in wl)
    caps = {n: d.capabilities for n, d in fixture_types.FIXTURE_TYPES.items()}
    ok("camera and radar (#911) are the tracks_people types",
       [n for n, c in caps.items() if c.get("tracks_people")]
       == ["camera", "radar"],
       repr(caps))
    ok("dmx is the only has_dmx type", caps["dmx"]["has_dmx"] is True
       and not any(c["has_dmx"] for n, c in caps.items() if n != "dmx"))
    # fovType helpers re-exported for parent_server's other call sites.
    ok("parent_server fovType helpers alias fixture_types",
       ps._normalise_fov_type is fixture_types.normalise_fov_type
       and ps._FOV_TYPE_WHITELIST == fixture_types.FOV_TYPE_WHITELIST)


def _cleanup():
    if _created:
        with ps._lock:
            ps._fixtures[:] = [f for f in ps._fixtures
                               if f.get("id") not in set(_created)]
            ps._save("fixtures", ps._fixtures)


def main():
    c = app.test_client()
    try:
        run_existing_types(c)
        run_unknown_type(c)
        run_geometry_axis(c)
        run_dummy_registration(c)
        run_registry_surface()
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
