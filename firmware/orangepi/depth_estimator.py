"""
depth_estimator.py — Monocular depth estimation via Depth-Anything-V2 ONNX.

Produces a relative depth map from a single camera frame.
Thread-safe, lazy-loaded. Deployed via SCP alongside yolov8n.onnx.
"""

import logging
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("slyled-cam")

MODEL_DIR = Path("/opt/slyled/models")
MODEL_PATH = MODEL_DIR / "depth_anything_v2_small.onnx"
INPUT_SIZE = 518  # Depth-Anything-V2 default input size


class DepthEstimator:
    """Monocular depth estimation using Depth-Anything-V2 small."""

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()

    def _load(self):
        if self._session is not None:
            return
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Depth model not found at {MODEL_PATH} — deploy from the Firmware tab"
            )
        log.info("Loading depth model...")
        t0 = time.monotonic()
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 4
            self._session = ort.InferenceSession(str(MODEL_PATH), opts,
                                                  providers=["CPUExecutionProvider"])
        except ImportError:
            self._session = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
        elapsed = (time.monotonic() - t0) * 1000
        log.info("Depth model loaded in %.0f ms", elapsed)

    def estimate(self, frame):
        """Estimate relative depth from a BGR frame.

        Args:
            frame: numpy array (H, W, 3) BGR

        Returns:
            (depth_map, inference_ms)
            depth_map: numpy float32 array (H, W) in [0, 1]
                0 = closest observed pixel, 1 = farthest observed pixel.
                Matches pinhole `z` convention so `z = d * max_depth_mm`
                in pixel_to_3d / generate_point_cloud is correctly oriented.
        """
        with self._lock:
            self._load()

            orig_h, orig_w = frame.shape[:2]

            # Preprocess: resize to model input, normalize
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = cv2.resize(img, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
            img = img.astype(np.float32) / 255.0
            # Normalize with ImageNet mean/std
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            img = (img - mean) / std
            # NCHW
            blob = np.transpose(img, (2, 0, 1))[np.newaxis, ...]

            # Inference
            t0 = time.monotonic()
            if hasattr(self._session, 'run'):
                # onnxruntime
                input_name = self._session.get_inputs()[0].name
                output = self._session.run(None, {input_name: blob})[0]
            else:
                # OpenCV DNN fallback
                self._session.setInput(blob)
                output = self._session.forward()
            inference_ms = (time.monotonic() - t0) * 1000

            # Output: (1, 1, H, W) or (1, H, W) — squeeze to 2D
            depth = output.squeeze()
            if depth.ndim != 2:
                depth = depth[0] if depth.ndim == 3 else depth

            # Resize back to original frame size
            depth = cv2.resize(depth, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)

            # Normalize to [0, 1] with pinhole depth convention: 0 = closest,
            # 1 = farthest. Depth-Anything-V2 outputs disparity (higher value
            # = closer object), so we min-max normalise AND invert.
            #
            # Previously the code skipped the invert, which made `z = d *
            # max_depth_mm` in generate_point_cloud / pixel_to_3d assign
            # large z to close objects. That folded the reconstructed floor
            # through the camera's pitch axis and produced a plane tilted by
            # ~2× the camera's physical pitch (#589). The disparity-indexed
            # comments above were inconsistent with the downstream code;
            # this is the correction.
            d_min, d_max = depth.min(), depth.max()
            if d_max - d_min > 1e-6:
                depth = 1.0 - (depth - d_min) / (d_max - d_min)
            else:
                depth = np.zeros_like(depth)

            return depth.astype(np.float32), inference_ms

    def depth_at_pixel(self, depth_map, px, py):
        """Get relative depth at a pixel position. Returns float 0-1."""
        h, w = depth_map.shape[:2]
        px = max(0, min(w - 1, int(px)))
        py = max(0, min(h - 1, int(py)))
        return float(depth_map[py, px])

    def pixel_to_3d(self, depth_map, px, py, fov_deg, frame_w, frame_h, max_depth_mm=5000):
        """Project a pixel + relative depth into approximate 3D coordinates.

        Args:
            depth_map: relative depth map (0=near, 1=far) — pinhole z direction
            px, py: pixel position
            fov_deg: horizontal FOV in degrees
            frame_w, frame_h: frame dimensions
            max_depth_mm: maximum depth in mm (for scaling relative depth)

        Returns:
            (x_mm, y_mm, z_mm) in camera-local coordinates
        """
        rel_depth = self.depth_at_pixel(depth_map, px, py)
        z = rel_depth * max_depth_mm  # depth in mm

        # Camera intrinsics from FOV
        fx = (frame_w / 2) / math.tan(math.radians(fov_deg / 2))
        fy = fx  # square pixels
        cx, cy = (frame_w - 1) / 2.0, (frame_h - 1) / 2.0

        # Back-project
        x = (px - cx) * z / fx
        y = (py - cy) * z / fy

        return (round(x), round(y), round(z))

    def generate_point_cloud(self, frame, fov_deg, max_points=10000, max_depth_mm=5000,
                             intrinsics=None):
        """Generate a downsampled point cloud from a BGR frame.

        Args:
            frame: BGR numpy array
            fov_deg: horizontal FOV in degrees
            max_points: maximum number of points to return
            max_depth_mm: maximum depth for scaling
            intrinsics: optional dict with fx, fy, cx, cy from checkerboard calibration (#244)

        Returns:
            (points, inference_ms) where points is a list of [x, y, z, r, g, b]
            Coordinates in camera-local mm. Colors are 0-255.
        """
        depth, ms = self.estimate(frame)
        h, w = depth.shape[:2]

        # Camera intrinsics — prefer calibrated if available
        if intrinsics and intrinsics.get("fx"):
            fx = intrinsics["fx"]
            fy = intrinsics.get("fy", fx)
            cx_cam = intrinsics.get("cx", (w - 1) / 2.0)
            cy_cam = intrinsics.get("cy", (h - 1) / 2.0)
        else:
            fx = (w / 2) / math.tan(math.radians(fov_deg / 2))
            fy = fx
            cx_cam, cy_cam = (w - 1) / 2.0, (h - 1) / 2.0

        # Downsample: pick every Nth pixel to stay under max_points
        total_pixels = h * w
        step = max(1, int(math.sqrt(total_pixels / max_points)))

        # Convert frame to RGB for color
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if rgb.shape[:2] != depth.shape[:2]:
            rgb = cv2.resize(rgb, (w, h))

        points = []
        for py in range(0, h, step):
            for px in range(0, w, step):
                d = float(depth[py, px])
                if d < 0.05 or d > 0.98:
                    continue  # skip unreliable extremes (noise floor + saturated)
                z = d * max_depth_mm
                x = (px - cx_cam) * z / fx
                y = (py - cy_cam) * z / fy
                r, g, b = int(rgb[py, px, 0]), int(rgb[py, px, 1]), int(rgb[py, px, 2])
                points.append([round(x), round(y), round(z), r, g, b])

        return points, ms
