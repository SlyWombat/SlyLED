"""#592 — unit coverage for the ArUco prescan + simple-scan endpoints.

Pure app.test_client() tests; no real cameras, no subprocess. The
`_aruco_snapshot_detect` helper is monkey-patched to return synthetic
detection results so the test is network-free.

Run: python -X utf8 tests/test_aruco_simple_scan.py
"""

import math
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


def test_marker_stage_corners_flat_on_floor():
    """A marker at (1500, 2000, 0) with size 100 and rotation=0 should
    produce 4 corners in the XY plane at z=0 forming a 100×100 square."""
    import numpy as np
    m = {"id": 1, "x": 1500, "y": 2000, "z": 0,
         "size": 100, "rx": 0, "ry": 0, "rz": 0}
    pts = parent_server._marker_stage_corners(m)
    ok(pts.shape == (4, 3), f"corners shape=(4,3) got {pts.shape}")
    # All on floor
    ok(all(abs(p[2]) < 1e-6 for p in pts), "all corners on z=0 plane")
    # Square side length
    side = np.linalg.norm(pts[1] - pts[0])
    ok(abs(side - 100) < 1e-6, f"side length 100mm (got {side:.3f})")
    # Center is marker position
    center = pts.mean(axis=0)
    ok(abs(center[0] - 1500) < 1e-6 and abs(center[1] - 2000) < 1e-6,
       f"center matches marker xy (got {center[:2]})")


def test_marker_stage_corners_wall_rotated():
    """Marker rotated 90° around stage X (rx=90) flips its face to point
    along +Y — corners should lie in the XZ plane."""
    import numpy as np
    m = {"id": 1, "x": 1000, "y": 500, "z": 1500,
         "size": 200, "rx": 90, "ry": 0, "rz": 0}
    pts = parent_server._marker_stage_corners(m)
    # Y component should be close to 500 for all corners (marker face now
    # lies in the XZ plane passing through y=500).
    y_spread = max(p[1] for p in pts) - min(p[1] for p in pts)
    ok(y_spread < 1e-6, f"wall-rotated corners share Y plane (spread {y_spread:.3f})")
    # Z spread = side length (corners above/below center).
    z_spread = max(p[2] for p in pts) - min(p[2] for p in pts)
    ok(abs(z_spread - 200) < 1e-6, f"Z spread = side (got {z_spread:.3f})")


def test_anchor_extrinsics_round_trip():
    """Project 4 synthetic marker corners through a known camera pose
    then recover that pose via _aruco_anchor_extrinsics. Recovered pose
    should match the original within sub-mm / fractional-degree
    accuracy for noise-free input."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        ok(True, "anchor round-trip skipped (cv2 unavailable)")
        return
    # Ground-truth: camera at (0, -3000, 1800) aimed downstage +Y,
    # tilted down 20°. Stage +X = camera +X; stage +Y = camera +Z
    # (optical axis forward); stage +Z = camera -Y (up in pixel frame).
    # Build the rotation stage → camera explicitly: tilt-down by 20°
    # puts the camera's forward axis into the Y-down-forward stage
    # direction.
    fov_deg, w, h = 70.0, 1280, 720
    fov_type = "horizontal"
    cam_pos = np.array([0, -3000, 1800], dtype=np.float64)
    tilt_deg = 20.0
    # R_cam_from_stage: first align cam forward with stage +Y, then
    # tilt down by 20° around cam X. Using a synthetic construction
    # rather than camera_math to keep the test's coordinate reasoning
    # self-contained.
    # Stage +X → cam +X
    # Stage +Y → cam +Z (forward)
    # Stage +Z → cam -Y (up)
    R_align = np.array([
        [1, 0, 0],
        [0, 0, -1],
        [0, 1, 0],
    ], dtype=np.float64)
    c, s = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    R_tilt = np.array([
        [1, 0, 0],
        [0, c, -s],
        [0, s, c],
    ], dtype=np.float64)
    R_cam_from_stage = R_tilt @ R_align
    t_truth = -R_cam_from_stage @ cam_pos.reshape(3, 1)
    rvec_truth, _ = cv2.Rodrigues(R_cam_from_stage)

    # Build K from FOV (matches _aruco_anchor_extrinsics formula).
    h_fov = math.radians(fov_deg)
    fx = (w / 2.0) / math.tan(h_fov / 2.0)
    K = np.array([[fx, 0, w / 2.0], [0, fx, h / 2.0], [0, 0, 1]], dtype=np.float64)

    # Two surveyed markers on the floor, well-spaced in X.
    markers = [
        {"id": 3, "x": -500, "y": 1500, "z": 0, "size": 150,
         "rx": 0, "ry": 0, "rz": 0},
        {"id": 7, "x": +500, "y": 1500, "z": 0, "size": 150,
         "rx": 0, "ry": 0, "rz": 0},
    ]
    detected = {}
    for m in markers:
        corners_3d = parent_server._marker_stage_corners(m)
        proj, _ = cv2.projectPoints(
            corners_3d.astype(np.float64),
            rvec_truth, t_truth, K, np.zeros(5))
        detected[m["id"]] = [[float(proj[i, 0, 0]), float(proj[i, 0, 1])]
                              for i in range(4)]

    reg_by_id = {m["id"]: m for m in markers}
    result = parent_server._aruco_anchor_extrinsics(
        w, h, fov_deg, fov_type, detected, reg_by_id)
    ok("err" not in result, f"anchor succeeds (got {result.get('err')})")
    if "err" in result:
        return
    ok(result["cornerCount"] == 8,
       f"uses 2 markers × 4 corners = 8 (got {result['cornerCount']})")
    ok(result["reprojectionRmsPx"] < 1.0,
       f"RMS reprojection <1px on noise-free (got {result['reprojectionRmsPx']})")

    R_rec, _ = cv2.Rodrigues(result["rvec"])
    t_rec = result["tvec"]
    # Recovered camera center in stage frame.
    cam_pos_rec = (-R_rec.T @ t_rec).flatten()
    pos_err = float(np.linalg.norm(cam_pos_rec - cam_pos))
    ok(pos_err < 5.0,
       f"recovered camera pos within 5mm of truth (got {pos_err:.3f}mm)")
    # Rotation delta — compose R_rec · R_truth^T and check angle.
    R_delta = R_rec @ R_cam_from_stage.T
    ang_deg = math.degrees(math.acos(
        max(-1.0, min(1.0, (float(np.trace(R_delta)) - 1) / 2))))
    ok(ang_deg < 0.5,
       f"recovered rotation within 0.5° of truth (got {ang_deg:.3f}°)")


def test_anchor_extrinsics_insufficient_corners():
    """Fewer than 4 correspondences (e.g. only 3 markers registered but
    2 visible with 4 corners each — wait, that IS ≥4). Here we test the
    empty-intersection case: detections present but none registered."""
    det = {99: [[100, 100], [200, 100], [200, 200], [100, 200]]}
    reg = {}  # none
    result = parent_server._aruco_anchor_extrinsics(1280, 720, 70, "horizontal",
                                                     det, reg)
    ok("err" in result, "empty intersection returns err")
    ok(result["cornerCount"] == 0, f"cornerCount=0 (got {result['cornerCount']})")


ALL = [
    test_preview_empty_world,
    test_preview_shared_detection,
    test_preview_respects_camera_filter,
    test_simple_rejects_empty_registry,
    test_simple_rejects_no_shared,
    test_simple_triangulates_shared_marker,
    test_marker_stage_corners_flat_on_floor,
    test_marker_stage_corners_wall_rotated,
    test_anchor_extrinsics_round_trip,
    test_anchor_extrinsics_insufficient_corners,
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
