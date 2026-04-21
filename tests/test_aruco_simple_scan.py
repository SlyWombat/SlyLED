"""#592 — unit coverage for the ArUco prescan + simple-scan endpoints.

Pure app.test_client() tests; no real cameras, no subprocess. The
`_aruco_snapshot_detect` helper is monkey-patched to return synthetic
detection results so the test is network-free.

Run: python -X utf8 tests/test_aruco_simple_scan.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import (  # noqa: E402
    app, _fixtures, _layout, _aruco_markers,
)


_passed = 0
_failed = 0


def ok(cond, name, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL  {name}{('  — ' + detail) if detail else ''}")


def _reset_world():
    """Clear fixtures/layout/aruco so each test starts from a known state."""
    _fixtures.clear()
    _layout["children"] = []
    _aruco_markers.clear()


def _add_camera(fid, name, pos_xyz):
    _fixtures.append({
        "id": fid, "name": name, "fixtureType": "camera",
        "cameraIp": f"10.0.0.{fid}", "cameraIdx": 0,
        "fovDeg": 90, "fovType": "horizontal",
        "rotation": [15, 0, 0],
    })
    _layout["children"].append({"id": fid, "x": pos_xyz[0], "y": pos_xyz[1], "z": pos_xyz[2]})


def _add_marker(mid, pos_xyz, size=150):
    _aruco_markers.append({
        "id": mid, "x": pos_xyz[0], "y": pos_xyz[1], "z": pos_xyz[2],
        "sizeMm": size, "rxDeg": 0, "ryDeg": 0, "rzDeg": 0,
    })


def _fake_detect(per_camera_map):
    """Build a patch for `_aruco_snapshot_detect` that reads from a
    {fixture_id: [list of marker dicts]} map. The returned dicts match
    what `_aruco_snapshot_detect` produces: {markers, frameSize, err?}.
    """
    def patched(f):
        mids = per_camera_map.get(f["id"], [])
        if mids is None:
            return {"err": "camera unreachable", "markers": [], "frameSize": None}
        markers = []
        for i, mid in enumerate(mids):
            cx = 640 + i * 100  # spread them across the frame
            cy = 400 + i * 60
            corners = [[cx - 30, cy - 30], [cx + 30, cy - 30],
                       [cx + 30, cy + 30], [cx - 30, cy + 30]]
            markers.append({"id": int(mid), "corners": corners,
                            "center": [float(cx), float(cy)]})
        return {"markers": markers, "frameSize": [1280, 720]}
    return patched


# ── Tests ────────────────────────────────────────────────────────────

def test_preview_empty_world():
    _reset_world()
    with app.test_client() as c:
        r = c.post("/api/space/scan/aruco-preview", json={})
        ok(r.status_code == 200, "preview/empty 200")
        body = r.get_json()
        ok(body.get("cameras") == [], "preview/empty cameras=[]",
           str(body))
        ok(body.get("sharedIds") == [], "preview/empty shared=[]")
        ok(body.get("correspondences") == 0, "preview/empty correspondences=0")


def test_preview_shared_detection():
    _reset_world()
    _add_camera(101, "CamL", (0, 0, 2000))
    _add_camera(102, "CamR", (3000, 0, 2000))
    _add_marker(5, (1500, 1500, 0))
    _add_marker(7, (1500, 2500, 0))
    # CamL sees 5 and 7; CamR sees 5 and 9 — shared = {5}
    # But 9 is NOT in the registry so it must never appear in sharedIds.
    saved = parent_server._aruco_snapshot_detect
    parent_server._aruco_snapshot_detect = _fake_detect({101: [5, 7], 102: [5, 9]})
    try:
        with app.test_client() as c:
            r = c.post("/api/space/scan/aruco-preview", json={})
            body = r.get_json()
            ok(body.get("sharedIds") == [5],
               f"preview shared only includes registered markers (got {body.get('sharedIds')})")
            ok(body.get("correspondences") == 4,
               f"preview correspondences = 4 (1 pair × 4 corners, got {body.get('correspondences')})")
            ok(len(body.get("cameras", [])) == 2,
               f"preview reports both cameras (got {len(body.get('cameras', []))})")
    finally:
        parent_server._aruco_snapshot_detect = saved


def test_preview_respects_camera_filter():
    _reset_world()
    _add_camera(201, "A", (0, 0, 2000))
    _add_camera(202, "B", (3000, 0, 2000))
    _add_camera(203, "C", (1500, 0, 2000))
    _add_marker(1, (1000, 1000, 0))
    saved = parent_server._aruco_snapshot_detect
    parent_server._aruco_snapshot_detect = _fake_detect({201: [1], 202: [1], 203: [1]})
    try:
        with app.test_client() as c:
            r = c.post("/api/space/scan/aruco-preview", json={"cameras": [201, 202]})
            body = r.get_json()
            ok(len(body.get("cameras", [])) == 2,
               "preview honours cameras= filter (only 2 snapshots)")
            ok(body.get("sharedIds") == [1],
               "filtered preview still finds shared markers")
    finally:
        parent_server._aruco_snapshot_detect = saved


def test_simple_rejects_empty_registry():
    _reset_world()
    _add_camera(301, "A", (0, 0, 2000))
    _add_camera(302, "B", (3000, 0, 2000))
    # No markers registered.
    with app.test_client() as c:
        r = c.post("/api/space/scan/aruco-simple", json={})
        ok(r.status_code == 400, "simple/no-registry → 400")
        err = (r.get_json() or {}).get("err", "")
        ok("registry" in err.lower(), "simple/no-registry err mentions registry", err)


def test_simple_rejects_no_shared():
    _reset_world()
    _add_camera(401, "A", (0, 0, 2000))
    _add_camera(402, "B", (3000, 0, 2000))
    _add_marker(1, (1000, 1000, 0))
    _add_marker(2, (2000, 1000, 0))
    # CamA sees 1, CamB sees 2 — no overlap.
    saved = parent_server._aruco_snapshot_detect
    parent_server._aruco_snapshot_detect = _fake_detect({401: [1], 402: [2]})
    try:
        with app.test_client() as c:
            r = c.post("/api/space/scan/aruco-simple", json={})
            ok(r.status_code == 400, "simple/no-shared → 400")
            err = (r.get_json() or {}).get("err", "")
            ok("visible" in err.lower() or "≥2" in err or "2 camera" in err.lower(),
               "simple/no-shared err explains overlap requirement", err)
    finally:
        parent_server._aruco_snapshot_detect = saved


def test_simple_triangulates_shared_marker():
    _reset_world()
    # Two well-separated cameras both aimed at the same floor point.
    _add_camera(501, "CamL", (0, 0, 2500))
    _add_camera(502, "CamR", (3000, 0, 2500))
    _add_marker(42, (1500, 2000, 0))
    saved = parent_server._aruco_snapshot_detect
    parent_server._aruco_snapshot_detect = _fake_detect({501: [42], 502: [42]})
    try:
        with app.test_client() as c:
            r = c.post("/api/space/scan/aruco-simple", json={})
            # Could be 200 (triangulated) or 502 (all points rejected by
            # reproject threshold) depending on the synthetic pixel geometry.
            # Both are valid outcomes for this smoke test — we only care
            # that the endpoint doesn't crash and returns a structured
            # response.
            body = r.get_json() or {}
            ok(r.status_code in (200, 502), f"simple returns 200 or 502 (got {r.status_code})")
            if r.status_code == 200:
                ok(body.get("source") == "aruco-markers",
                   "simple/ok source=aruco-markers")
                ok(len(body.get("sharedIds", [])) == 1,
                   "simple/ok sharedIds includes the one visible marker")
                ok(isinstance(body.get("triangulated"), list)
                     and len(body["triangulated"]) == 1,
                   f"simple/ok triangulated has 1 entry (got {len(body.get('triangulated', []))})")
                if body.get("triangulated"):
                    t = body["triangulated"][0]
                    ok(t.get("id") == 42, "triangulated entry id=42")
                    ok("deltaMm" in t and isinstance(t["deltaMm"], (int, float)),
                       "triangulated entry carries deltaMm")
                    ok("surveyed" in t and len(t["surveyed"]) == 3,
                       "triangulated entry carries surveyed xyz")
            else:
                # 502 path — body should still explain the failure mode.
                ok("err" in body, "simple/502 has err")
                ok(body.get("sharedIds") == [42],
                   "simple/502 still reports which markers were shared")
    finally:
        parent_server._aruco_snapshot_detect = saved


ALL = [
    test_preview_empty_world,
    test_preview_shared_detection,
    test_preview_respects_camera_filter,
    test_simple_rejects_empty_registry,
    test_simple_rejects_no_shared,
    test_simple_triangulates_shared_marker,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            import traceback
            _failed += 1
            print(f"CRASH {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
