"""Auto target-selection tests (#497).

Covers `pick_calibration_targets`:
  - Returns N points inside the floor extent.
  - Respects the camera FOV clip.
  - Drops points inside obstacle AABBs.
  - Picks spread-out points when the candidate grid exceeds N.

Run:
    python -X utf8 tests/test_target_selection.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from mover_calibrator import pick_calibration_targets, stage_to_pixel  # noqa: E402
from mover_calibrator import pixel_to_stage  # noqa: E402


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


# ── Helpers ─────────────────────────────────────────────────────────────

def _box_geometry(w_mm=6000, d_mm=8000, floor_z=0):
    return {
        "floor": {"z": floor_z,
                   "extent": {"xMin": 0, "xMax": w_mm,
                               "yMin": 0, "yMax": d_mm}},
        "walls": [], "obstacles": [],
        "source": "layout-box",
    }


# ── Tests ───────────────────────────────────────────────────────────────

def test_returns_requested_count_on_empty_stage():
    geom = _box_geometry()
    pts = pick_calibration_targets((3000, 0, 3000), geom, n=6)
    _assert(len(pts) == 6, f"got {len(pts)} targets")


def test_points_lie_inside_extent():
    geom = _box_geometry(6000, 8000)
    pts = pick_calibration_targets((3000, 0, 3000), geom, n=6)
    for (x, y, z) in pts:
        _assert(0 <= x <= 6000, f"x inside {x}")
        _assert(0 <= y <= 8000, f"y inside {y}")
        _assert(z == 0, f"z on floor {z}")


def test_camera_fov_clips_points():
    """With a very narrow FOV, only points near the camera axis remain."""
    geom = _box_geometry(6000, 8000)
    fixture_pos = (3000, 0, 3000)
    camera_pos = (3000, 0, 2500)
    # 10° FOV — half-angle 5° — almost nothing except the centreline survives.
    pts = pick_calibration_targets(fixture_pos, geom, n=6,
                                    camera_pos=camera_pos, camera_fov_deg=10)
    for (x, y, z) in pts:
        dx = x - camera_pos[0]
        dy = y - camera_pos[1]
        ang = math.degrees(abs(math.atan2(dx, dy)))
        _assert(ang <= 5.01, f"point outside narrow FOV: ang={ang:.2f}")


def test_obstacle_aabb_excludes_points():
    geom = _box_geometry(6000, 8000)
    geom["obstacles"] = [{
        "bbox": {"xMin": 1000, "xMax": 5000, "yMin": 3000, "yMax": 5000},
    }]
    pts = pick_calibration_targets((3000, 0, 3000), geom, n=6)
    for (x, y, z) in pts:
        inside = (1000 - 150 <= x <= 5000 + 150
                   and 3000 - 150 <= y <= 5000 + 150)
        _assert(not inside, f"point inside obstacle bbox: ({x}, {y})")


def test_spread_picked_from_dense_candidates():
    """With n < grid count, picker should spread out in pan/tilt angle."""
    geom = _box_geometry(10000, 10000)
    pts = pick_calibration_targets((5000, 0, 4000), geom, n=4)
    _assert(len(pts) == 4, f"got {len(pts)}")
    # Compute pan angles and check spread > 30°
    fx = 5000
    fy = 0
    pans = [math.degrees(math.atan2(p[0] - fx, p[1] - fy)) for p in pts]
    spread = max(pans) - min(pans)
    _assert(spread > 30, f"pan spread under 30°: {spread:.1f}")


def test_no_floor_extent_returns_empty():
    geom = {"floor": {"z": 0}}  # no extent
    pts = pick_calibration_targets((0, 0, 0), geom, n=6)
    _assert(pts == [], "empty list when no extent")


def test_no_floor_returns_empty():
    geom = {"walls": [], "obstacles": []}
    pts = pick_calibration_targets((0, 0, 0), geom, n=6)
    _assert(pts == [], "empty when no floor")


def test_pointcloud_floor_z_honoured():
    geom = _box_geometry(6000, 8000, floor_z=-50)
    pts = pick_calibration_targets((3000, 0, 3000), geom, n=6)
    for (_, _, z) in pts:
        _assert(z == -50, f"floor_z=-50 honoured, got {z}")


def test_stage_to_pixel_roundtrips():
    """Identity homography maps pixel (x,y) ↔ stage (x,y) 1:1, and the
    inverse is consistent for non-trivial homographies."""
    # A simple scale + translate: stage = 10 * pixel + 100.
    H = [10, 0, 100,
         0, 10, 100,
         0,  0,   1]
    # pixel → stage (forward via existing pixel_to_stage)
    s = pixel_to_stage(50, 40, H)
    _assert(s == (600.0, 500.0), f"pixel→stage got {s}")
    # stage → pixel (inverse via new helper)
    px = stage_to_pixel(H, 600.0, 500.0)
    _assert(px is not None and abs(px[0] - 50) < 1e-6 and abs(px[1] - 40) < 1e-6,
            f"stage→pixel got {px}")


def test_stage_to_pixel_degenerate():
    # A singular matrix returns None instead of raising.
    H = [1, 0, 0, 2, 0, 0, 0, 0, 0]
    _assert(stage_to_pixel(H, 100, 100) is None,
            "singular homography returns None")


ALL = [
    test_returns_requested_count_on_empty_stage,
    test_points_lie_inside_extent,
    test_camera_fov_clips_points,
    test_obstacle_aabb_excludes_points,
    test_spread_picked_from_dense_candidates,
    test_no_floor_extent_returns_empty,
    test_no_floor_returns_empty,
    test_pointcloud_floor_z_honoured,
    test_stage_to_pixel_roundtrips,
    test_stage_to_pixel_degenerate,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
