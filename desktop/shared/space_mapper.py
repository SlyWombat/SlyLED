"""
space_mapper.py — Environment point cloud from multiple cameras.

Collects per-camera point clouds, transforms to stage coordinates
using camera fixture positions from the layout, merges into a single cloud.
"""

import json
import logging
import math
import threading
import time
import urllib.request

log = logging.getLogger("slyled")


def fetch_point_cloud(camera_ip, cam_idx, max_points=10000, max_depth_mm=5000):
    """Fetch a point cloud from a camera node's /point-cloud endpoint.
    Returns list of [x, y, z, r, g, b] in camera-local coords, or None."""
    try:
        req = urllib.request.Request(
            f"http://{camera_ip}:5000/point-cloud",
            data=json.dumps({"cam": cam_idx, "maxPoints": max_points,
                              "maxDepthMm": max_depth_mm}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        r = json.loads(resp.read().decode())
        if r.get("ok"):
            return r.get("points", [])
    except Exception as e:
        log.warning("Point cloud fetch failed for %s cam%d: %s", camera_ip, cam_idx, e)
    return None


def transform_points(points, cam_pos, cam_rotation):
    """Transform camera-local points to stage coordinates.

    Args:
        points: list of [x, y, z, r, g, b] in camera-local mm
        cam_pos: (x, y, z) camera position in stage mm
        cam_rotation: (rx, ry, rz) rotation in degrees

    Returns: list of [x, y, z, r, g, b] in stage mm
    """
    cx, cy, cz = cam_pos
    ry_rad = math.radians(cam_rotation[1]) if len(cam_rotation) > 1 else 0

    cos_ry = math.cos(ry_rad)
    sin_ry = math.sin(ry_rad)

    result = []
    for pt in points:
        lx, ly, lz = pt[0], pt[1], pt[2]
        # Rotate around Y axis (horizontal turn)
        wx = lx * cos_ry + lz * sin_ry + cx
        wy = ly + cy
        wz = -lx * sin_ry + lz * cos_ry + cz
        result.append([round(wx), round(wy), round(wz), pt[3], pt[4], pt[5]])
    return result


class SpaceScan:
    """Async environment scan — collects point clouds from all cameras."""

    def __init__(self):
        self._running = False
        self._progress = 0
        self._message = ""
        self._result = None
        self._thread = None

    @property
    def running(self):
        return self._running

    @property
    def status(self):
        return {
            "running": self._running,
            "progress": self._progress,
            "message": self._message,
            "result": self._result,
        }

    def start(self, camera_fixtures, layout_positions, max_points_per_cam=10000):
        """Start an async environment scan.

        Args:
            camera_fixtures: list of camera fixture dicts (with cameraIp, cameraIdx, rotation)
            layout_positions: dict of {fixture_id: {x, y, z}} from layout
        """
        if self._running:
            return
        self._running = True
        self._progress = 0
        self._message = "Starting scan..."
        self._result = None
        self._thread = threading.Thread(
            target=self._scan, daemon=True,
            args=(camera_fixtures, layout_positions, max_points_per_cam))
        self._thread.start()

    def _scan(self, cameras, positions, max_points):
        all_points = []
        cam_info = []
        total = len(cameras)

        for i, cam in enumerate(cameras):
            fid = cam.get("id")
            ip = cam.get("cameraIp")
            cam_idx = cam.get("cameraIdx", 0)
            self._progress = int((i / total) * 90)
            self._message = f"Scanning camera {i+1}/{total}: {cam.get('name', ip)} (cam{cam_idx})"
            log.info("Space scan: %s", self._message)

            if not ip:
                continue

            # Fetch point cloud from camera node
            points = fetch_point_cloud(ip, cam_idx, max_points)
            if not points:
                log.warning("No points from %s cam%d", ip, cam_idx)
                continue

            # Transform to stage coordinates using camera position
            pos = positions.get(fid, positions.get(str(fid), {}))
            cam_pos = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
            cam_rot = cam.get("rotation", [0, 0, 0])
            stage_points = transform_points(points, cam_pos, cam_rot)

            all_points.extend(stage_points)
            cam_info.append({
                "fixtureId": fid,
                "cameraIdx": cam_idx,
                "name": cam.get("name", ""),
                "pointCount": len(stage_points),
            })

        self._progress = 95
        self._message = f"Merging {len(all_points)} points..."

        self._result = {
            "timestamp": time.time(),
            "cameras": cam_info,
            "points": all_points,
            "totalPoints": len(all_points),
        }

        self._progress = 100
        self._message = f"Scan complete — {len(all_points)} points from {len(cam_info)} cameras"
        self._running = False
        log.info("Space scan complete: %d points from %d cameras",
                 len(all_points), len(cam_info))
