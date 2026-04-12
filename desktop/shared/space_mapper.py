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


def fetch_point_cloud(camera_fixture, max_points=10000, max_depth_mm=5000):
    """Fetch a point cloud using two strategies:

    1. Try the camera node's /point-cloud endpoint (works on Orange Pi).
    2. If that fails (503/timeout), fetch a snapshot and run depth estimation
       locally on the orchestrator (requires cv2 + depth model).

    Args:
        camera_fixture: dict with cameraIp, cameraIdx, fovDeg, etc.
        max_points: maximum number of points to return
        max_depth_mm: maximum depth in mm

    Returns:
        list of [x, y, z, r, g, b] in camera-local coords, or None.
    """
    ip = camera_fixture.get("cameraIp")
    cam_idx = camera_fixture.get("cameraIdx", 0)
    if not ip:
        return None

    # Strategy 1: Try camera's /point-cloud endpoint
    try:
        req = urllib.request.Request(
            f"http://{ip}:5000/point-cloud",
            data=json.dumps({"cam": cam_idx, "maxPoints": max_points,
                              "maxDepthMm": max_depth_mm}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        r = json.loads(resp.read().decode())
        if r.get("ok"):
            pts = r.get("points", [])
            if pts:
                return pts
    except Exception as e:
        log.info("Camera /point-cloud failed for %s cam%d: %s — trying orchestrator-side depth",
                 ip, cam_idx, e)

    # Strategy 2: Fetch snapshot + run depth estimation locally on orchestrator
    try:
        snap_resp = urllib.request.urlopen(
            f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        jpeg_data = snap_resp.read()

        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            log.warning("Failed to decode snapshot from %s cam%d", ip, cam_idx)
            return None

        # Try to load depth estimator (may not be available on orchestrator)
        try:
            from depth_estimator import DepthEstimator
        except ImportError:
            log.warning("No depth_estimator module available on orchestrator for %s cam%d", ip, cam_idx)
            return None

        estimator = DepthEstimator()
        depth_map, _ms = estimator.estimate(frame)

        # Generate point cloud from depth map + camera intrinsics
        h, w = depth_map.shape[:2]
        fov = camera_fixture.get("fovDeg", 60)
        fx = (w / 2) / math.tan(math.radians(fov / 2))
        fy = fx
        cx, cy = w / 2.0, h / 2.0

        points = []
        step = max(1, int(math.sqrt(h * w / max_points)))
        for v in range(0, h, step):
            for u in range(0, w, step):
                d = float(depth_map[v, u])
                if d <= 0 or d > max_depth_mm:
                    continue
                x = (u - cx) * d / fx
                y = (v - cy) * d / fy
                z = d
                r_val = int(frame[v, u, 2])
                g_val = int(frame[v, u, 1])
                b_val = int(frame[v, u, 0])
                points.append([x, y, z, r_val, g_val, b_val])
        log.info("Orchestrator-side depth for %s cam%d: %d points", ip, cam_idx, len(points))
        return points if points else None
    except Exception as e:
        log.warning("Orchestrator-side depth failed for %s cam%d: %s", ip, cam_idx, e)

    return None


def transform_points(points, cam_pos, cam_rotation, cam_aim=None):
    """Transform camera-local points to stage coordinates.

    Camera-local frame (pinhole convention): X-right, Y-down, Z-forward (depth).
    Stage frame: X=width (right), Y=depth (forward), Z=height (up).

    Pipeline:
      1. Frame swap: cam(X,Y,Z) → stage-aligned axes
         cam X-right   → stage X (width)
         cam Z-forward → stage Y (depth)
         cam -Y (up)   → stage Z (height)
      2. Rotate by camera pitch (rx) and yaw (ry) in stage frame
      3. Translate by camera position

    Args:
        points: list of [x, y, z, r, g, b] in camera-local coords
        cam_pos: (x, y, z) camera position in stage mm
        cam_rotation: (rx, ry, rz) degrees — rx=pitch (tilt down), ry=yaw (pan)
        cam_aim: optional (x, y, z) aim point — overrides rotation

    Returns: list of [x, y, z, r, g, b] in stage mm (Z-up)
    """
    cx, cy, cz = cam_pos

    # Compute yaw and pitch
    if cam_aim:
        dx = cam_aim[0] - cx
        dy = cam_aim[1] - cy
        dz = cam_aim[2] - cz
        dist_xy = math.sqrt(dx * dx + dy * dy)
        ry_rad = math.atan2(dx, dy)           # yaw
        rx_rad = math.atan2(-dz, dist_xy)     # pitch (down = positive)
    else:
        rx_rad = math.radians(cam_rotation[0]) if len(cam_rotation) > 0 else 0
        ry_rad = math.radians(cam_rotation[1]) if len(cam_rotation) > 1 else 0

    # Rotation matrix: RZ(yaw) * RX(pitch)
    # In stage coords (Z=up), yaw rotates around Z axis, pitch tilts around X axis
    cos_p, sin_p = math.cos(rx_rad), math.sin(rx_rad)
    cos_y, sin_y = math.cos(ry_rad), math.sin(ry_rad)

    # RZ(yaw) * RX(pitch)
    r00 = cos_y;              r01 = -sin_y * cos_p;  r02 = sin_y * sin_p
    r10 = sin_y;              r11 = cos_y * cos_p;   r12 = -cos_y * sin_p
    r20 = 0;                  r21 = sin_p;            r22 = cos_p

    result = []
    for pt in points:
        # Step 1: Frame swap — camera → stage-aligned
        sx = pt[0]        # cam X-right  → stage X
        sy = pt[2]        # cam Z-forward → stage Y (depth)
        sz = -pt[1]       # cam -Y-down  → stage Z (height)

        if not (math.isfinite(sx) and math.isfinite(sy) and math.isfinite(sz)):
            continue

        # Step 2: Rotate + translate to stage position
        wx = r00 * sx + r01 * sy + r02 * sz + cx
        wy = r10 * sx + r11 * sy + r12 * sz + cy
        wz = r20 * sx + r21 * sy + r22 * sz + cz

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

            # Fetch point cloud (tries camera endpoint, then orchestrator-side depth)
            points = fetch_point_cloud(cam, max_points)
            if not points:
                log.warning("No points from %s cam%d", ip, cam_idx)
                continue

            # Transform to stage coordinates using camera position
            pos = positions.get(fid, positions.get(str(fid), {}))
            cam_pos = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
            cam_rot = cam.get("rotation", [0, 0, 0])
            # Use rotation for yaw direction
            stage_points = transform_points(points, cam_pos, cam_rot)

            all_points.extend(stage_points)
            cam_info.append({
                "fixtureId": fid,
                "cameraIdx": cam_idx,
                "name": cam.get("name", ""),
                "pointCount": len(stage_points),
            })

        self._progress = 90
        self._message = f"Normalizing {len(all_points)} points to stage floor..."

        # Floor normalization: detect floor plane, shift Z so floor = Z=0 (#246)
        floor_z = None
        if len(all_points) > 100:
            try:
                from surface_analyzer import _detect_floor
                coords = [(p[0], p[1], p[2]) for p in all_points]
                floor = _detect_floor(coords, tolerance=150)
                if floor:
                    floor_z = floor.get("z", floor.get("y", 0))
                    log.info("Floor normalization: shifting Z by %d mm (floor was at Z=%d)",
                             -floor_z, floor_z)
                    for p in all_points:
                        p[2] -= floor_z  # shift so floor = Z=0
            except Exception as e:
                log.warning("Floor normalization failed: %s", e)

        self._progress = 95
        self._message = f"Merging {len(all_points)} points..."

        self._result = {
            "timestamp": time.time(),
            "cameras": cam_info,
            "points": all_points,
            "totalPoints": len(all_points),
            "floorNormalized": floor_z is not None,
            "floorOffset": floor_z,
        }

        self._progress = 100
        self._message = f"Scan complete — {len(all_points)} points from {len(cam_info)} cameras"
        self._running = False
        log.info("Space scan complete: %d points from %d cameras",
                 len(all_points), len(cam_info))
