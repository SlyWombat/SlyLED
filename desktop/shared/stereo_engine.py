"""
stereo_engine.py — Stereo 3D triangulation from multiple calibrated cameras (#230).

Uses camera intrinsics (from ArUco calibration or FOV estimate) and
extrinsics (from stage-map solvePnP) to triangulate 3D points from
2D pixel correspondences across cameras.

Coordinate system: stage mm — X=width, Y=depth, Z=height (floor=0).
"""

import logging
import math

import numpy as np

log = logging.getLogger("slyled")


class StereoEngine:
    """Multi-camera stereo triangulation."""

    def __init__(self):
        self._cameras = {}  # cam_id -> {K, R, t, pos_stage, K_inv}

    @property
    def camera_count(self):
        return len(self._cameras)

    def camera_ids(self):
        return list(self._cameras.keys())

    def add_camera(self, cam_id, intrinsics, extrinsics):
        """Register a calibrated camera.

        Args:
            cam_id: unique camera identifier (str or int)
            intrinsics: dict with fx, fy, cx, cy
            extrinsics: dict with rvec (3-element), tvec (3-element)
        """
        import cv2
        fx = intrinsics["fx"]
        fy = intrinsics["fy"]
        cx = intrinsics["cx"]
        cy = intrinsics["cy"]
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
        K_inv = np.linalg.inv(K)

        rvec = np.array(extrinsics["rvec"], dtype=np.float64).reshape(3, 1)
        tvec = np.array(extrinsics["tvec"], dtype=np.float64).reshape(3, 1)
        R, _ = cv2.Rodrigues(rvec)
        pos_stage = (-R.T @ tvec).flatten()

        self._cameras[cam_id] = {
            "K": K, "K_inv": K_inv, "R": R, "t": tvec,
            "rvec": rvec, "pos_stage": pos_stage,
        }
        log.info("Stereo: registered camera %s at stage pos %s",
                 cam_id, [round(float(v), 1) for v in pos_stage])

    def add_camera_from_fov(self, cam_id, fov_deg, frame_w, frame_h,
                            stage_pos, stage_rotation=None):
        """Register camera using FOV estimate (no ArUco calibration).

        Computes approximate intrinsics from FOV; the cam-to-stage
        rotation comes from the shared `camera_math.build_camera_to_stage`
        helper so the sign convention matches space_mapper and the
        fixture editor (#586). stage_rotation is
        `[tilt_deg, pan_deg, roll_deg]` where positive tilt = aim down
        and positive pan = aim toward +X.
        """
        from camera_math import build_camera_to_stage, rotation_from_layout

        fov_rad = math.radians(fov_deg)
        fx = (frame_w / 2.0) / math.tan(fov_rad / 2.0)
        fy = fx
        cx, cy = frame_w / 2.0, frame_h / 2.0

        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
        K_inv = np.linalg.inv(K)

        tilt, pan, roll = rotation_from_layout(stage_rotation)
        R = np.asarray(build_camera_to_stage(tilt, pan, roll), dtype=np.float64)

        pos = np.array(stage_pos, dtype=np.float64)
        tvec = -R @ pos.reshape(3, 1)

        self._cameras[cam_id] = {
            "K": K, "K_inv": K_inv, "R": R, "t": tvec,
            "rvec": None, "pos_stage": pos,
        }

    def pixel_to_ray(self, cam_id, px, py):
        """Convert pixel to world ray: (origin, direction) in stage mm.

        origin: camera center in stage coordinates
        direction: unit vector from camera center through the pixel
        """
        cam = self._cameras.get(cam_id)
        if cam is None:
            raise ValueError(f"Camera {cam_id} not registered")

        # Ray in camera frame
        ray_cam = cam["K_inv"] @ np.array([px, py, 1.0])
        ray_cam = ray_cam / np.linalg.norm(ray_cam)

        # Transform to world/stage frame
        ray_world = cam["R"].T @ ray_cam
        ray_world = ray_world / np.linalg.norm(ray_world)

        origin = cam["pos_stage"].copy()
        return (origin.tolist(), ray_world.tolist())

    def triangulate_ray_ray(self, cam_id_1, px1, py1, cam_id_2, px2, py2):
        """Two-camera ray-ray intersection (midpoint of closest approach).

        Returns: dict {x, y, z, error} in stage mm, or None if cameras
        are not registered or rays are parallel.
        """
        o1, d1 = self.pixel_to_ray(cam_id_1, px1, py1)
        o2, d2 = self.pixel_to_ray(cam_id_2, px2, py2)
        return _closest_approach(o1, d1, o2, d2)

    def triangulate(self, observations):
        """Triangulate a 3D point from 2+ pixel observations.

        Args:
            observations: list of (cam_id, pixel_x, pixel_y)

        Returns:
            dict {x, y, z, error} in stage mm, or None
        """
        if len(observations) < 2:
            return None

        if len(observations) == 2:
            cid1, px1, py1 = observations[0]
            cid2, px2, py2 = observations[1]
            return self.triangulate_ray_ray(cid1, px1, py1, cid2, px2, py2)

        # N>2 cameras: linear least squares (DLT-style)
        rays = []
        for cid, px, py in observations:
            o, d = self.pixel_to_ray(cid, px, py)
            rays.append((np.array(o), np.array(d)))

        # Build normal equation: minimize sum of squared distances to all rays
        # For each ray: point P, distance to ray = ||(P - O) - ((P - O)·D)D||
        # This is a linear system: (I - D*D^T)(P - O) should be minimized
        A = np.zeros((3, 3))
        b = np.zeros(3)
        for o, d in rays:
            d = d / np.linalg.norm(d)
            I_ddT = np.eye(3) - np.outer(d, d)
            A += I_ddT
            b += I_ddT @ o

        try:
            P = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            return None

        # Compute RMS error
        err_sq = 0
        for o, d in rays:
            d = d / np.linalg.norm(d)
            v = P - o
            proj = np.dot(v, d) * d
            perp = v - proj
            err_sq += np.dot(perp, perp)
        rms = math.sqrt(err_sq / len(rays))

        return {"x": float(P[0]), "y": float(P[1]), "z": float(P[2]),
                "error": round(rms, 1)}


def _closest_approach(o1, d1, o2, d2):
    """Midpoint of closest approach between two 3D rays.

    Returns: dict {x, y, z, error} where error is the distance between
    the closest points on the two rays, or None if parallel.
    """
    o1 = np.array(o1, dtype=np.float64)
    d1 = np.array(d1, dtype=np.float64)
    o2 = np.array(o2, dtype=np.float64)
    d2 = np.array(d2, dtype=np.float64)
    d1 = d1 / np.linalg.norm(d1)
    d2 = d2 / np.linalg.norm(d2)

    w0 = o1 - o2
    a = np.dot(d1, d1)
    b = np.dot(d1, d2)
    c = np.dot(d2, d2)
    d = np.dot(d1, w0)
    e = np.dot(d2, w0)

    denom = a * c - b * b
    if abs(denom) < 1e-10:
        return None  # parallel rays

    t1 = (b * e - c * d) / denom
    t2 = (a * e - b * d) / denom

    p1 = o1 + t1 * d1
    p2 = o2 + t2 * d2
    midpoint = (p1 + p2) / 2.0
    error = float(np.linalg.norm(p1 - p2))

    return {"x": float(midpoint[0]), "y": float(midpoint[1]),
            "z": float(midpoint[2]), "error": round(error, 1)}
