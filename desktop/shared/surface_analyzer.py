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

    # Step 1: Floor detection — find dominant horizontal plane (consistent Z)
    floor = _detect_floor(coords, floor_tolerance)
    floor_z = floor["z"] if floor else None

    # Step 2: Separate floor points from remaining
    if floor_z is not None:
        remaining = [(x, y, z) for x, y, z in coords
                     if abs(z - floor_z) > floor_tolerance]
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
             f"z={floor_z}" if floor_z else "none", len(walls), len(obstacles), elapsed)

    return {
        "floor": floor,
        "walls": walls,
        "obstacles": obstacles,
        "analysisMs": round(elapsed),
    }


def _detect_floor(coords, tolerance, ransac_trials=200):
    """RANSAC 3-point plane fit to find the floor. (#261)
    Floor is a horizontal plane at Z=constant.
    Accepts planes whose normal is within ~18 deg of vertical (dot with (0,0,1) > 0.95)."""
    if len(coords) < 10:
        return None

    best_normal = None
    best_d = 0
    best_count = 0
    n = len(coords)

    for _ in range(ransac_trials):
        # Pick 3 random non-collinear points
        i, j, k = random.sample(range(n), 3)
        p1, p2, p3 = coords[i], coords[j], coords[k]
        # Two edge vectors
        e1 = (p2[0]-p1[0], p2[1]-p1[1], p2[2]-p1[2])
        e2 = (p3[0]-p1[0], p3[1]-p1[1], p3[2]-p1[2])
        # Cross product → normal
        nx = e1[1]*e2[2] - e1[2]*e2[1]
        ny = e1[2]*e2[0] - e1[0]*e2[2]
        nz = e1[0]*e2[1] - e1[1]*e2[0]
        length = math.sqrt(nx*nx + ny*ny + nz*nz)
        if length < 1e-6:
            continue
        nx, ny, nz = nx/length, ny/length, nz/length
        # Ensure normal points upward (Z+)
        if nz < 0:
            nx, ny, nz = -nx, -ny, -nz
        # Must be near-vertical: dot with (0,0,1) > 0.95
        if nz < 0.95:
            continue
        d = -(nx*p1[0] + ny*p1[1] + nz*p1[2])
        # Count inliers
        count = sum(1 for x, y, z in coords
                    if abs(nx*x + ny*y + nz*z + d) < tolerance)
        if count > best_count:
            best_count = count
            best_normal = [round(nx, 4), round(ny, 4), round(nz, 4)]
            best_d = d

    # Need at least 5% of points
    if best_count < n * 0.05:
        return None

    # Collect inlier points for extent and average Z (height)
    floor_pts = [(x, y, z) for x, y, z in coords
                 if abs(best_normal[0]*x + best_normal[1]*y + best_normal[2]*z + best_d) < tolerance]
    if not floor_pts:
        return None
    avg_z = sum(p[2] for p in floor_pts) / len(floor_pts)
    xs = [p[0] for p in floor_pts]
    ys = [p[1] for p in floor_pts]

    return {
        "z": round(avg_z),
        "normal": best_normal,
        "d": round(best_d),
        "inliers": len(floor_pts),
        "extent": {
            "xMin": round(min(xs)), "xMax": round(max(xs)),
            "yMin": round(min(ys)), "yMax": round(max(ys)),
        },
    }


def _detect_walls(coords, tolerance, max_walls=4):
    """Find vertical planes in the point cloud using RANSAC.
    Wall normals are in the XY horizontal plane (Z=height is vertical)."""
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

            # Plane normal in XY (vertical plane: normal is horizontal)
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            length = math.sqrt(dx * dx + dy * dy)
            if length < 10:
                continue

            # Normal perpendicular to the line in XY, pointing outward
            nx, ny = -dy / length, dx / length
            nz = 0
            d = -(nx * p1[0] + ny * p1[1])

            # Count inliers
            count = sum(1 for x, y, z in remaining
                        if abs(nx * x + ny * y + d) < tolerance)

            if count > best_count:
                best_count = count
                best_wall = {"normal": [round(nx, 4), round(ny, 4), 0], "d": round(d)}

        # Accept wall if enough inliers (>5% of remaining or 50 absolute) (#266)
        if best_wall and best_count > max(50, len(remaining) * 0.05):
            # Compute wall extent
            n = best_wall["normal"]
            wall_pts = [(x, y, z) for x, y, z in remaining
                        if abs(n[0] * x + n[1] * y + best_wall["d"]) < tolerance]
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
                             if abs(n[0] * x + n[1] * y + best_wall["d"]) >= tolerance]

    return walls


def _point_to_plane_dist(x, y, z, normal, d):
    return normal[0] * x + normal[1] * y + normal[2] * z + d


def _cluster_obstacles(coords, min_cluster, grid_size=300):
    """Simple grid-based clustering of remaining points into obstacles.
    Grid uses (x, y) since X=width and Y=depth are the horizontal axes."""
    if len(coords) < min_cluster:
        return []

    # Grid the points in XY (horizontal plane)
    grid = {}
    for x, y, z in coords:
        key = (round(x / grid_size), round(y / grid_size))
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
        w = max(xs) - min(xs)   # width (X)
        d = max(ys) - min(ys)   # depth (Y)
        h = max(zs) - min(zs)   # height (Z)

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

    # Check floor (horizontal plane at Z=floor_z, normal = (0,0,1))
    floor = surfaces.get("floor")
    if floor:
        floor_z = floor.get("z", floor.get("y", 0))
        t = _ray_plane_intersect(ray_origin, ray_dir, [0, 0, 1], -floor_z)
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

    # Check obstacles — proper ray-sphere intersection (#260)
    for obs in surfaces.get("obstacles", []):
        pos = obs["pos"]
        size = obs["size"]
        radius = max(size) / 2
        # Vector from ray origin to obstacle center
        oc = (pos[0] - ray_origin[0], pos[1] - ray_origin[1], pos[2] - ray_origin[2])
        # Project onto ray direction (t = oc . dir)
        t = oc[0] * ray_dir[0] + oc[1] * ray_dir[1] + oc[2] * ray_dir[2]
        if t < 0:
            continue  # obstacle is behind the ray origin
        # Closest point on ray to obstacle center
        cx = ray_origin[0] + t * ray_dir[0] - pos[0]
        cy = ray_origin[1] + t * ray_dir[1] - pos[1]
        cz = ray_origin[2] + t * ray_dir[2] - pos[2]
        perp_dist = math.sqrt(cx * cx + cy * cy + cz * cz)
        if perp_dist < radius:
            pt = (ray_origin[0] + t * ray_dir[0],
                  ray_origin[1] + t * ray_dir[1],
                  ray_origin[2] + t * ray_dir[2])
            hits.append({"surface": obs["label"], "distance": t, "point": pt})

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
