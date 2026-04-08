"""
surface_analyzer.py — Detect structural surfaces from point cloud.

Identifies floor, walls, and obstacles (pillars, furniture) using
RANSAC plane fitting and point clustering.
"""

import logging
import math
import random
import time

log = logging.getLogger("slyled")


def analyze_surfaces(points, floor_tolerance=100, wall_tolerance=100, min_cluster=20):
    """Analyze a point cloud to find structural surfaces.

    Args:
        points: list of [x, y, z, r, g, b] in stage mm
        floor_tolerance: mm tolerance for floor plane inliers
        wall_tolerance: mm tolerance for wall plane inliers
        min_cluster: minimum points for an obstacle cluster

    Returns:
        dict with floor, walls, obstacles
    """
    if not points or len(points) < 50:
        return {"floor": None, "walls": [], "obstacles": []}

    t0 = time.monotonic()

    # Extract XYZ
    coords = [(p[0], p[1], p[2]) for p in points]

    # Step 1: Floor detection — find dominant horizontal plane (consistent Y)
    floor = _detect_floor(coords, floor_tolerance)
    floor_y = floor["y"] if floor else None

    # Step 2: Separate floor points from remaining
    if floor_y is not None:
        remaining = [(x, y, z) for x, y, z in coords
                     if abs(y - floor_y) > floor_tolerance]
    else:
        remaining = list(coords)

    # Step 3: Wall detection — find vertical planes in remaining points
    walls = _detect_walls(remaining, wall_tolerance)

    # Step 4: Remove wall points, cluster remaining as obstacles
    wall_points = set()
    for wall in walls:
        for i, (x, y, z) in enumerate(remaining):
            dist = _point_to_plane_dist(x, y, z, wall["normal"], wall["d"])
            if abs(dist) < wall_tolerance:
                wall_points.add(i)

    obstacle_pts = [(x, y, z) for i, (x, y, z) in enumerate(remaining)
                    if i not in wall_points]
    obstacles = _cluster_obstacles(obstacle_pts, min_cluster)

    elapsed = (time.monotonic() - t0) * 1000
    log.info("Surface analysis: floor=%s, %d walls, %d obstacles (%.0fms)",
             f"y={floor_y}" if floor_y else "none", len(walls), len(obstacles), elapsed)

    return {
        "floor": floor,
        "walls": walls,
        "obstacles": obstacles,
        "analysisMs": round(elapsed),
    }


def _detect_floor(coords, tolerance):
    """RANSAC to find the dominant horizontal plane (floor).
    Looks for a cluster of points at a consistent Y value."""
    if len(coords) < 10:
        return None

    # Histogram approach: bin Y values, find the densest bin
    y_vals = [c[1] for c in coords]
    bin_size = tolerance
    bins = {}
    for y in y_vals:
        b = round(y / bin_size) * bin_size
        bins[b] = bins.get(b, 0) + 1

    if not bins:
        return None

    # Best Y bin (most points = floor)
    best_y = max(bins, key=bins.get)
    inlier_count = bins[best_y]

    # Need at least 5% of points to be floor
    if inlier_count < len(coords) * 0.05:
        return None

    # Refine: average Y of all points near best_y
    floor_pts = [(x, y, z) for x, y, z in coords if abs(y - best_y) < tolerance]
    avg_y = sum(p[1] for p in floor_pts) / len(floor_pts)

    # Floor extent
    xs = [p[0] for p in floor_pts]
    zs = [p[2] for p in floor_pts]

    return {
        "y": round(avg_y),
        "normal": [0, 1, 0],
        "inliers": len(floor_pts),
        "extent": {
            "xMin": round(min(xs)), "xMax": round(max(xs)),
            "zMin": round(min(zs)), "zMax": round(max(zs)),
        },
    }


def _detect_walls(coords, tolerance, max_walls=4):
    """Find vertical planes in the point cloud using RANSAC."""
    if len(coords) < 30:
        return []

    walls = []
    remaining = list(coords)

    for _ in range(max_walls):
        if len(remaining) < 30:
            break

        best_wall = None
        best_count = 0

        # RANSAC: try random vertical planes
        for trial in range(200):
            # Pick 2 random points, define a vertical plane through them
            i, j = random.sample(range(len(remaining)), 2)
            p1, p2 = remaining[i], remaining[j]

            # Plane normal in XZ (vertical plane: normal is horizontal)
            dx = p2[0] - p1[0]
            dz = p2[2] - p1[2]
            length = math.sqrt(dx * dx + dz * dz)
            if length < 10:
                continue

            # Normal perpendicular to the line in XZ, pointing outward
            nx, nz = -dz / length, dx / length
            ny = 0
            d = -(nx * p1[0] + nz * p1[2])

            # Count inliers
            count = sum(1 for x, y, z in remaining
                        if abs(nx * x + nz * z + d) < tolerance)

            if count > best_count:
                best_count = count
                best_wall = {"normal": [round(nx, 4), 0, round(nz, 4)], "d": round(d)}

        # Accept wall if enough inliers (>3% of remaining)
        if best_wall and best_count > len(remaining) * 0.03:
            # Compute wall extent
            n = best_wall["normal"]
            wall_pts = [(x, y, z) for x, y, z in remaining
                        if abs(n[0] * x + n[2] * z + best_wall["d"]) < tolerance]
            if wall_pts:
                best_wall["inliers"] = len(wall_pts)
                best_wall["extent"] = {
                    "xMin": round(min(p[0] for p in wall_pts)),
                    "xMax": round(max(p[0] for p in wall_pts)),
                    "yMin": round(min(p[1] for p in wall_pts)),
                    "yMax": round(max(p[1] for p in wall_pts)),
                    "zMin": round(min(p[2] for p in wall_pts)),
                    "zMax": round(max(p[2] for p in wall_pts)),
                }
                walls.append(best_wall)

                # Remove wall points from remaining
                remaining = [(x, y, z) for x, y, z in remaining
                             if abs(n[0] * x + n[2] * z + best_wall["d"]) >= tolerance]

    return walls


def _point_to_plane_dist(x, y, z, normal, d):
    return normal[0] * x + normal[1] * y + normal[2] * z + d


def _cluster_obstacles(coords, min_cluster, grid_size=300):
    """Simple grid-based clustering of remaining points into obstacles."""
    if len(coords) < min_cluster:
        return []

    # Grid the points
    grid = {}
    for x, y, z in coords:
        key = (round(x / grid_size), round(z / grid_size))
        if key not in grid:
            grid[key] = []
        grid[key].append((x, y, z))

    # Merge adjacent grid cells into clusters (flood fill)
    visited = set()
    obstacles = []

    for key in grid:
        if key in visited or len(grid[key]) < 3:
            continue

        # BFS flood fill
        cluster = []
        queue = [key]
        while queue:
            k = queue.pop(0)
            if k in visited:
                continue
            visited.add(k)
            if k in grid:
                cluster.extend(grid[k])
                # Check neighbors
                for dk in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nb = (k[0] + dk[0], k[1] + dk[1])
                    if nb in grid and nb not in visited:
                        queue.append(nb)

        if len(cluster) < min_cluster:
            continue

        xs = [p[0] for p in cluster]
        ys = [p[1] for p in cluster]
        zs = [p[2] for p in cluster]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        d = max(zs) - min(zs)

        # Classify: tall+thin = pillar, wide+short = furniture
        label = "pillar" if h > 500 and max(w, d) < 500 else "obstacle"

        obstacles.append({
            "pos": [round(sum(xs) / len(xs)), round(sum(ys) / len(ys)),
                    round(sum(zs) / len(zs))],
            "size": [round(w), round(h), round(d)],
            "points": len(cluster),
            "label": label,
        })

    return obstacles


def beam_surface_check(surfaces, ray_origin, ray_dir):
    """Check which surface a beam ray intersects first.

    Args:
        surfaces: dict from analyze_surfaces()
        ray_origin: (x, y, z) in mm
        ray_dir: (dx, dy, dz) normalized direction

    Returns:
        dict with {surface, distance, point, split} or None
    """
    hits = []

    # Check floor
    floor = surfaces.get("floor")
    if floor:
        t = _ray_plane_intersect(ray_origin, ray_dir, [0, 1, 0], -floor["y"])
        if t and t > 0:
            pt = (ray_origin[0] + t * ray_dir[0],
                  ray_origin[1] + t * ray_dir[1],
                  ray_origin[2] + t * ray_dir[2])
            hits.append({"surface": "floor", "distance": t, "point": pt})

    # Check walls
    for i, wall in enumerate(surfaces.get("walls", [])):
        n = wall["normal"]
        t = _ray_plane_intersect(ray_origin, ray_dir, n, wall["d"])
        if t and t > 0:
            pt = (ray_origin[0] + t * ray_dir[0],
                  ray_origin[1] + t * ray_dir[1],
                  ray_origin[2] + t * ray_dir[2])
            hits.append({"surface": f"wall_{i}", "distance": t, "point": pt})

    # Check obstacles (as bounding boxes)
    for obs in surfaces.get("obstacles", []):
        pos = obs["pos"]
        size = obs["size"]
        # Simple sphere check
        dx = ray_origin[0] - pos[0]
        dy = ray_origin[1] - pos[1]
        dz = ray_origin[2] - pos[2]
        radius = max(size) / 2
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist < radius * 3:  # rough proximity
            hits.append({"surface": obs["label"], "distance": dist, "point": pos})

    if not hits:
        return None

    hits.sort(key=lambda h: h["distance"])
    result = hits[0]

    # Check for split: if two surfaces are within 200mm of each other along the ray
    result["split"] = len(hits) > 1 and (hits[1]["distance"] - hits[0]["distance"]) < 200

    return result


def _ray_plane_intersect(origin, direction, normal, d):
    """Ray-plane intersection. Returns t (distance) or None."""
    denom = (normal[0] * direction[0] + normal[1] * direction[1] +
             normal[2] * direction[2])
    if abs(denom) < 1e-6:
        return None
    t = -(normal[0] * origin[0] + normal[1] * origin[1] +
          normal[2] * origin[2] + d) / denom
    return t if t > 0 else None
