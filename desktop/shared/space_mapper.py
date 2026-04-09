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


def transform_points(points, cam_pos, cam_rotation, cam_aim=None):
    """Transform camera-local points to stage coordinates.

    Camera-local frame (pinhole convention): X-right, Y-down, Z-forward.
    Stage frame: X-right, Y-up, Z-forward.
    The Y axis is flipped before rotation to convert from camera to stage convention.

    Rotation composition: RY(yaw) * RX(pitch) * RZ(roll) — YXZ intrinsic Euler angles.
    If cam_aim is provided, computes yaw/pitch from position→aim direction.

    Args:
        points: list of [x, y, z, r, g, b] in camera-local mm (Y-down)
        cam_pos: (x, y, z) camera position in stage mm
        cam_rotation: (rx, ry, rz) rotation in degrees
        cam_aim: optional (x, y, z) aim point — overrides rotation if provided

    Returns: list of [x, y, z, r, g, b] in stage mm (Y-up)
    """
    cx, cy, cz = cam_pos

    # Compute yaw AND pitch from aim direction
    # The depth model gives camera-local 3D coords that need full rotation to stage space
    if cam_aim:
        dx = cam_aim[0] - cx
        dy = cam_aim[1] - cy
        dz = cam_aim[2] - cz
        dist_xz = math.sqrt(dx * dx + dz * dz)
        ry_rad = math.atan2(dx, dz)  # yaw
        rx_rad = math.atan2(-dy, dist_xz)  # pitch (negative because looking down = positive pitch)
        rz_rad = 0
    else:
        rx_rad = math.radians(cam_rotation[0]) if len(cam_rotation) > 0 else 0
        ry_rad = math.radians(cam_rotation[1]) if len(cam_rotation) > 1 else 0
        rz_rad = math.radians(cam_rotation[2]) if len(cam_rotation) > 2 else 0

    # Build rotation matrix: RY * RX * RZ (yaw, pitch, roll order)
    cos_rx, sin_rx = math.cos(rx_rad), math.sin(rx_rad)
    cos_ry, sin_ry = math.cos(ry_rad), math.sin(ry_rad)
    cos_rz, sin_rz = math.cos(rz_rad), math.sin(rz_rad)

    # Combined rotation matrix elements (RY * RX * RZ)
    r00 = cos_ry * cos_rz + sin_ry * sin_rx * sin_rz
    r01 = -cos_ry * sin_rz + sin_ry * sin_rx * cos_rz
    r02 = sin_ry * cos_rx
    r10 = cos_rx * sin_rz
    r11 = cos_rx * cos_rz
    r12 = -sin_rx
    r20 = -sin_ry * cos_rz + cos_ry * sin_rx * sin_rz
    r21 = sin_ry * sin_rz + cos_ry * sin_rx * cos_rz
    r22 = cos_ry * cos_rx

    result = []
    for pt in points:
        lx, lz = pt[0], pt[2]
        ly = -pt[1]  # Flip Y: camera Y-down → stage Y-up (#257)
        # Skip invalid points
        if not (math.isfinite(lx) and math.isfinite(ly) and math.isfinite(lz)):
            continue
        # Apply rotation then translation
        wx = r00 * lx + r01 * ly + r02 * lz + cx
        wy = r10 * lx + r11 * ly + r12 * lz + cy
        wz = r20 * lx + r21 * ly + r22 * lz + cz
        result.append([wx, wy, wz, pt[3], pt[4], pt[5]])
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
            # Use aim point for yaw direction if available
            cam_aim = cam.get("aimPoint")
            stage_points = transform_points(points, cam_pos, cam_rot, cam_aim=cam_aim)

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
