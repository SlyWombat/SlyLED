"""
test_surface_analyzer.py -- Unit tests for RANSAC surface analysis.

Covers: analyze_surfaces integration, _detect_walls, _cluster_obstacles,
beam_surface_check, _ray_plane_intersect, and edge cases.

Does NOT duplicate tests already in test_spatial_math.py (flat floor Z=0,
tilted floor 5-deg, ray-sphere hit/miss).

Run: python -X utf8 tests/test_surface_analyzer.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from surface_analyzer import (
    analyze_surfaces,
    beam_surface_check,
    _detect_walls,
    _cluster_obstacles,
    _ray_plane_intersect,
)

random.seed(42)

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


def approx(a, b, tol=1.0):
    return abs(a - b) <= tol


# ===================================================================
print("\n=== analyze_surfaces -- integration ===")
# ===================================================================

# 1a. Too few points (<50) returns empty result
random.seed(42)
sparse = [[random.uniform(0, 5000), random.uniform(0, 5000), random.uniform(-20, 20), 0, 0, 0]
          for _ in range(30)]
res = analyze_surfaces(sparse)
check("Too few points: floor is None", res["floor"] is None)
check("Too few points: walls empty", res["walls"] == [])
check("Too few points: obstacles empty", res["obstacles"] == [])

# 1b. Floor + one wall detected
random.seed(42)
pts = []
# Floor at Z=0 -- 200 points spread across X 0..5000, Y 0..5000
for _ in range(200):
    pts.append([random.uniform(0, 5000), random.uniform(0, 5000),
                random.gauss(0, 15), 128, 128, 128])
# Vertical wall at X=0 (normal ~[1,0,0]) -- 120 points along Y, height 0..2500
for _ in range(120):
    pts.append([random.gauss(0, 15), random.uniform(0, 5000),
                random.uniform(0, 2500), 128, 128, 128])

res = analyze_surfaces(pts, floor_tolerance=80, wall_tolerance=80)
check("Floor+wall: floor detected", res["floor"] is not None)
check("Floor+wall: floor Z near 0", res["floor"] is not None and approx(res["floor"]["z"], 0, 80))
check("Floor+wall: at least 1 wall", len(res["walls"]) >= 1)

# Check wall normal is roughly horizontal (nz == 0)
if res["walls"]:
    w0 = res["walls"][0]
    check("Floor+wall: wall normal Z == 0", w0["normal"][2] == 0)

# 1c. Floor + obstacle cluster
# Use a wide XY spread (sigma=200) so the cluster is NOT planar and cannot
# be misidentified as a wall by RANSAC.
random.seed(42)
pts2 = []
# Floor
for _ in range(200):
    pts2.append([random.uniform(0, 8000), random.uniform(0, 8000),
                 random.gauss(0, 10), 128, 128, 128])
# Obstacle cluster at (4000, 4000) going up to Z=1500 -- wide XY spread
for _ in range(80):
    pts2.append([random.gauss(4000, 200), random.gauss(4000, 200),
                 random.uniform(200, 1500), 128, 128, 128])

res2 = analyze_surfaces(pts2, floor_tolerance=80, min_cluster=15)
check("Floor+obstacle: floor detected", res2["floor"] is not None)
check("Floor+obstacle: obstacle found", len(res2["obstacles"]) >= 1)

# ===================================================================
print("\n=== _detect_walls -- vertical plane detection ===")
# ===================================================================

# 2a. Vertical wall at X=0 (points clustered near X=0, spread in Y and Z)
random.seed(42)
wall_pts = [(random.gauss(0, 10), random.uniform(0, 5000), random.uniform(0, 3000))
            for _ in range(150)]
walls = _detect_walls(wall_pts, tolerance=80)
check("Single wall at X=0: detected", len(walls) >= 1)
if walls:
    nx = walls[0]["normal"][0]
    ny = walls[0]["normal"][1]
    # Normal should be ~[1,0,0] or ~[-1,0,0]
    check("Single wall at X=0: |nx| near 1", abs(abs(nx) - 1.0) < 0.15)
    check("Single wall at X=0: |ny| near 0", abs(ny) < 0.15)
    check("Single wall at X=0: nz == 0", walls[0]["normal"][2] == 0)

# 2b. Two perpendicular walls: one at X=0, one at Y=0
random.seed(42)
two_wall_pts = []
# Wall at X=0
for _ in range(120):
    two_wall_pts.append((random.gauss(0, 8), random.uniform(500, 5000), random.uniform(0, 2500)))
# Wall at Y=0
for _ in range(120):
    two_wall_pts.append((random.uniform(500, 5000), random.gauss(0, 8), random.uniform(0, 2500)))

walls2 = _detect_walls(two_wall_pts, tolerance=60)
check("Two perp walls: detected 2", len(walls2) == 2)

if len(walls2) == 2:
    # One normal should be ~[1,0,0], the other ~[0,1,0]
    normals = sorted(walls2, key=lambda w: abs(w["normal"][0]), reverse=True)
    check("Two perp walls: first nx dominant", abs(normals[0]["normal"][0]) > 0.8)
    check("Two perp walls: second ny dominant", abs(normals[1]["normal"][1]) > 0.8)

# 2c. Too few points (<30) returns empty
tiny = [(random.gauss(0, 5), random.uniform(0, 100), random.uniform(0, 100)) for _ in range(20)]
walls_tiny = _detect_walls(tiny, tolerance=50)
check("Too few wall points: empty", walls_tiny == [])

# 2d. Wall tolerance: tight tolerance rejects noisy points
random.seed(42)
noisy_wall = [(random.gauss(0, 80), random.uniform(0, 5000), random.uniform(0, 2500))
              for _ in range(200)]
walls_tight = _detect_walls(noisy_wall, tolerance=20)
# With tolerance=20 and noise sigma=80, most points are outliers -- may fail to detect
walls_loose = _detect_walls(noisy_wall, tolerance=200)
# Loose tolerance should find it (or at least find more inliers)
check("Wall tolerance: loose finds >= tight inliers",
      len(walls_loose) >= len(walls_tight))

# ===================================================================
print("\n=== _cluster_obstacles -- grid-based BFS ===")
# ===================================================================

# 3a. Single cluster
random.seed(42)
single = [(random.gauss(2000, 50), random.gauss(2000, 50), random.uniform(0, 1000))
          for _ in range(40)]
obs = _cluster_obstacles(single, min_cluster=10)
check("Single cluster: 1 obstacle", len(obs) == 1)
if obs:
    check("Single cluster: pos near (2000, 2000)", approx(obs[0]["pos"][0], 2000, 150)
          and approx(obs[0]["pos"][1], 2000, 150))

# 3b. Two separate clusters (far apart -- different grid cells)
random.seed(42)
cluster_a = [(random.gauss(1000, 30), random.gauss(1000, 30), random.uniform(0, 800))
             for _ in range(35)]
cluster_b = [(random.gauss(6000, 30), random.gauss(6000, 30), random.uniform(0, 800))
             for _ in range(35)]
obs2 = _cluster_obstacles(cluster_a + cluster_b, min_cluster=10)
check("Two clusters: 2 obstacles", len(obs2) == 2)

# 3c. Tall thin cluster -> "pillar" (h>500, max(w,d)<500)
random.seed(42)
pillar = [(random.gauss(3000, 40), random.gauss(3000, 40), random.uniform(0, 2000))
          for _ in range(50)]
obs_p = _cluster_obstacles(pillar, min_cluster=10)
check("Pillar: detected", len(obs_p) >= 1)
if obs_p:
    check("Pillar: label is 'pillar'", obs_p[0]["label"] == "pillar")
    check("Pillar: height > 500", obs_p[0]["size"][1] > 500)

# 3d. Wide cluster -> "obstacle"
random.seed(42)
wide = [(random.gauss(3000, 400), random.gauss(3000, 400), random.uniform(0, 200))
        for _ in range(80)]
obs_w = _cluster_obstacles(wide, min_cluster=10)
check("Wide cluster: detected", len(obs_w) >= 1)
if obs_w:
    check("Wide cluster: label is 'obstacle'", obs_w[0]["label"] == "obstacle")

# 3e. Too few points (<min_cluster) returns empty
tiny_obs = [(100, 100, 100) for _ in range(5)]
obs_tiny = _cluster_obstacles(tiny_obs, min_cluster=20)
check("Too few obstacle points: empty", obs_tiny == [])

# ===================================================================
print("\n=== beam_surface_check -- ray intersection ===")
# ===================================================================

# 4a. Ray hits floor only
surfaces_floor = {
    "floor": {"z": 0, "normal": [0, 0, 1], "d": 0},
    "walls": [],
    "obstacles": [],
}
# Origin at (1000, 1000, 3000), aiming down
ray_dir = (0, 0, -1)
hit = beam_surface_check(surfaces_floor, (1000, 1000, 3000), ray_dir)
check("Ray hits floor: surface='floor'", hit is not None and hit["surface"] == "floor")
check("Ray hits floor: distance=3000", hit is not None and approx(hit["distance"], 3000, 1))

# 4b. Ray hits wall before floor
surfaces_wf = {
    "floor": {"z": 0, "normal": [0, 0, 1], "d": 0},
    "walls": [{"normal": [0, 1, 0], "d": -2000}],  # wall at Y=2000
    "obstacles": [],
}
# Origin at (500, 0, 1500), aiming forward and slightly down
ray_fwd = (0, 1, -0.1)
rlen = math.sqrt(sum(d * d for d in ray_fwd))
ray_fwd = tuple(d / rlen for d in ray_fwd)
hit2 = beam_surface_check(surfaces_wf, (500, 0, 1500), ray_fwd)
check("Wall before floor: surface='wall_0'", hit2 is not None and hit2["surface"] == "wall_0")

# 4c. Ray toward obstacle returns obstacle label
surfaces_ob = {
    "floor": None,
    "walls": [],
    "obstacles": [{"pos": [3000, 3000, 1000], "size": [600, 600, 600], "label": "obstacle"}],
}
direction = (3000 - 0, 3000 - 0, 1000 - 1000)
dlen = math.sqrt(sum(d * d for d in direction))
direction = tuple(d / dlen for d in direction)
hit3 = beam_surface_check(surfaces_ob, (0, 0, 1000), direction)
check("Ray hits obstacle: label='obstacle'", hit3 is not None and hit3["surface"] == "obstacle")

# 4d. Split detection: two surfaces within 200mm
surfaces_split = {
    "floor": {"z": 0, "normal": [0, 0, 1], "d": 0},
    "walls": [],
    "obstacles": [{"pos": [500, 500, 100], "size": [400, 400, 200], "label": "obstacle"}],
}
# Ray from above aimed at obstacle which is near the floor
ray_down = (0, 0, -1)
hit4 = beam_surface_check(surfaces_split, (500, 500, 400), ray_down)
check("Split detection: split=True", hit4 is not None and hit4.get("split") is True)

# 4e. No surfaces -> None
empty_surf = {"floor": None, "walls": [], "obstacles": []}
hit5 = beam_surface_check(empty_surf, (0, 0, 0), (0, 0, -1))
check("No surfaces: returns None", hit5 is None)

# 4f. Horizontal ray parallel to floor -> no floor hit
surfaces_floor2 = {
    "floor": {"z": 0, "normal": [0, 0, 1], "d": 0},
    "walls": [],
    "obstacles": [],
}
ray_horiz = (1, 0, 0)  # purely horizontal
hit6 = beam_surface_check(surfaces_floor2, (0, 0, 500), ray_horiz)
check("Horizontal ray: no floor hit", hit6 is None)

# ===================================================================
print("\n=== _ray_plane_intersect ===")
# ===================================================================

# 5a. Normal intersection: plane at Z=0, ray from Z=1000 going down
t = _ray_plane_intersect((0, 0, 1000), (0, 0, -1), [0, 0, 1], 0)
check("Normal intersection: t=1000", t is not None and approx(t, 1000, 0.01))

# 5b. Parallel ray -> None (direction perpendicular to normal has zero dot)
t2 = _ray_plane_intersect((0, 0, 500), (1, 0, 0), [0, 0, 1], 0)
check("Parallel ray: returns None", t2 is None)

# 5c. Ray pointing away (t < 0) -> None
t3 = _ray_plane_intersect((0, 0, 1000), (0, 0, 1), [0, 0, 1], 0)
check("Ray away from plane: returns None", t3 is None)

# ===================================================================
print("\n=== Edge cases ===")
# ===================================================================

# 6a. All points at same location -- degenerate for RANSAC (cross product = 0)
random.seed(42)
same = [[1000, 1000, 0, 0, 0, 0] for _ in range(100)]
res_same = analyze_surfaces(same)
# Floor detection uses random samples; collinear points may or may not yield a plane
# but it should not crash
check("All same points: no crash", True)
check("All same points: floor None or valid",
      res_same["floor"] is None or isinstance(res_same["floor"]["z"], (int, float)))

# 6b. Points in a line (degenerate -- all Y and Z identical)
random.seed(42)
line = [[i * 10, 0, 0, 0, 0, 0] for i in range(100)]
res_line = analyze_surfaces(line)
check("Collinear points: no crash", True)

# 6c. Noisy floor with outliers -- RANSAC should still find dominant plane
random.seed(42)
noisy_floor = []
# 250 inliers near Z=500
for _ in range(250):
    noisy_floor.append([random.uniform(0, 6000), random.uniform(0, 6000),
                        random.gauss(500, 20), 0, 0, 0])
# 80 outlier points scattered far from the floor
for _ in range(80):
    noisy_floor.append([random.uniform(0, 6000), random.uniform(0, 6000),
                        random.uniform(1500, 4000), 0, 0, 0])

res_noisy = analyze_surfaces(noisy_floor, floor_tolerance=80)
check("Noisy floor: detected", res_noisy["floor"] is not None)
check("Noisy floor: Z near 500",
      res_noisy["floor"] is not None and approx(res_noisy["floor"]["z"], 500, 80))

# ===================================================================
print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
if failed:
    sys.exit(1)
