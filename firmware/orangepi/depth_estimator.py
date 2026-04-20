"""
depth_estimator.py — Monocular depth estimation via ONNX.

Supports two models, selectable at load time:
  - "metric" (default, preferred): Depth-Anything-V2 Metric Indoor Small.
    Outputs depth directly in metres. Same 94 MB size as the disparity
    variant. Eliminates the #589-class disparity-direction + 1/d-vs-linear
    errors that plagued the original DA-V2 pipeline.
  - "disparity" (legacy): Depth-Anything-V2 Small. Outputs normalised
    disparity (higher = closer). Kept for backward compatibility and as
    a fallback when the metric model isn't deployed.

The model choice is persisted in /opt/slyled/models/active_depth_model;
falls back to whichever ONNX is available if the preference file is
missing. Thread-safe, lazy-loaded. Deployed via SCP.
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
MODEL_DISPARITY = MODEL_DIR / "depth_anything_v2_small.onnx"          # legacy
MODEL_METRIC    = MODEL_DIR / "dav2_metric_indoor_small.onnx"          # preferred (#593)
ACTIVE_PREF     = MODEL_DIR / "active_depth_model"
INPUT_SIZE = 518  # multiple of 14 (DINOv2 patch size), standard DA-V2 size


def _select_model():
    """Return (path, kind) for the model to load. Preference order:
    1. value in `active_depth_model` preference file, if the target
       ONNX exists
    2. metric model, if present
    3. disparity model, if present
    Raises FileNotFoundError if neither exists.
    """
    pref = None
    if ACTIVE_PREF.exists():
        try:
            pref = ACTIVE_PREF.read_text().strip().lower()
        except Exception:
            pref = None
    if pref == "disparity" and MODEL_DISPARITY.exists():
        return MODEL_DISPARITY, "disparity"
    if pref == "metric" and MODEL_METRIC.exists():
        return MODEL_METRIC, "metric"
    # no preference / stale preference — prefer metric, fall back to disparity
    if MODEL_METRIC.exists():
        return MODEL_METRIC, "metric"
    if MODEL_DISPARITY.exists():
        return MODEL_DISPARITY, "disparity"
    raise FileNotFoundError(
        f"No depth model found — expected one of {MODEL_METRIC} or "
        f"{MODEL_DISPARITY}. Deploy from the Firmware tab.")


class DepthEstimator:
    """Monocular depth estimation. Defaults to DA-V2 Metric Indoor Small;
    falls back to the disparity variant if the metric ONNX isn't deployed."""

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()
        self._kind = None  # "metric" | "disparity" — set at load time

    @property
    def kind(self):
        """'metric' or 'disparity'. None until the model has loaded."""
        return self._kind

    def _load(self):
        if self._session is not None:
            return
        path, kind = _select_model()
        self._kind = kind
        log.info("Loading depth model (%s) from %s", kind, path.name)
        t0 = time.monotonic()
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 4
            self._session = ort.InferenceSession(str(path), opts,
                                                  providers=["CPUExecutionProvider"])
        except ImportError:
            self._session = cv2.dnn.readNetFromONNX(str(path))
        elapsed = (time.monotonic() - t0) * 1000
        log.info("Depth model loaded in %.0f ms (%s)", elapsed, kind)

    def estimate(self, frame):
        """Estimate depth from a BGR frame.

        Args:
            frame: numpy array (H, W, 3) BGR

        Returns:
            (depth_map, inference_ms)
            depth_map: numpy float32 array (H, W)
              - if self.kind == 'metric': depth in **millimetres** directly
              - if self.kind == 'disparity': normalised [0, 1] with 0=near,
                1=far (matches pinhole z direction). Callers that want
                mm multiply by their own `max_depth_mm` — the scaling is
                a rough approximation of the disparity→depth relationship.
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

            if self._kind == "metric":
                # Model outputs metric depth in METRES; convert to mm.
                # Clamp implausible values (e.g. sky → 0 or negative on
                # indoor-trained models).
                depth = depth * 1000.0
                depth[~np.isfinite(depth)] = 0
                depth[depth < 0] = 0
            else:
                # Disparity model — normalise to [0, 1] with
                # 0 = closest, 1 = farthest (#589 sign fix).
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
        """Project a pixel + depth into approximate 3D camera-local mm.

        Depth semantics depend on self.kind:
          metric    → depth_map[py, px] is already in mm
          disparity → depth_map[py, px] is normalised [0, 1]; scale by max_depth_mm
        """
        sample = float(self.depth_at_pixel(depth_map, px, py))
        if self._kind == "metric":
            z = sample
        else:
            z = sample * max_depth_mm

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
            max_depth_mm: max depth cap. Applied regardless of model kind —
                the metric model occasionally predicts very large depths on
                saturated pixels; capping keeps those out. Also doubles as
                the disparity→mm multiplier for the disparity model.
            intrinsics: optional dict with fx, fy, cx, cy from calibration.

        Returns:
            (points, inference_ms). points is a list of [x, y, z, r, g, b].
            Coordinates are camera-local mm (cam-local Z is forward depth,
            X right, Y down — pinhole convention). Colors 0-255.
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

        is_metric = (self._kind == "metric")
        points = []
        for py in range(0, h, step):
            for px in range(0, w, step):
                d = float(depth[py, px])
                if is_metric:
                    # Metric mm: reject obviously bad samples (0/negative
                    # from the clamp, or beyond max_depth_mm).
                    if d < 50 or d > max_depth_mm:
                        continue
                    z = d
                else:
                    # Normalised disparity path: [0, 1] with 0=near, 1=far.
                    # Skip noise floor + saturation extremes.
                    if d < 0.05 or d > 0.98:
                        continue
                    z = d * max_depth_mm
                x = (px - cx_cam) * z / fx
                y = (py - cy_cam) * z / fy
                r, g, b = int(rgb[py, px, 0]), int(rgb[py, px, 1]), int(rgb[py, px, 2])
                points.append([round(x), round(y), round(z), r, g, b])

        return points, ms
