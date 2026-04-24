"""
cv_engine.py — Unified computer vision engine for the orchestrator (#333).

All CV inference runs here. Camera nodes only provide JPEG snapshots.
Models are loaded lazily on first use. Thread-safe.

Wraps the existing firmware modules (beam_detector, depth_estimator, detector)
with orchestrator-appropriate model paths and snapshot fetching.
"""

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("slyled")

# ── Model search paths ──────────────────────────────────────────────────
# 1. desktop/shared/models/ (orchestrator-local, takes priority)
# 2. firmware/orangepi/models/ (shared repo copy)
_SELF_DIR = Path(__file__).resolve().parent
_MODEL_DIRS = [
    _SELF_DIR / "models",
    _SELF_DIR.parent.parent / "firmware" / "orangepi" / "models",
]

# ── Import firmware modules with adjusted model paths ────────────────────
_FW_DIR = str(_SELF_DIR.parent.parent / "firmware" / "orangepi")
if _FW_DIR not in sys.path:
    sys.path.insert(0, _FW_DIR)

_BeamDetector = None
_DepthEstimator = None
_ObjectDetector = None

try:
    from beam_detector import BeamDetector as _BeamDetector
except ImportError:
    log.warning("beam_detector not available — beam detection disabled")

try:
    from depth_estimator import DepthEstimator as _DepthEstimator
except ImportError:
    log.warning("depth_estimator not available — depth estimation disabled")

try:
    from detector import ObjectDetector as _ObjectDetector
except ImportError:
    log.warning("detector not available — object detection disabled")


def _find_model(filename):
    """Search model directories for a specific model file."""
    for d in _MODEL_DIRS:
        p = d / filename
        if p.exists():
            return p
    return None


class CVEngine:
    """Singleton CV engine for the orchestrator. Thread-safe, lazy-loads models."""

    def __init__(self):
        self._beam = None
        self._depth = None
        self._detector = None
        self._lock = threading.Lock()
        self._model_status = {
            "beam": "available" if _BeamDetector else "unavailable",
            "depth": "not_loaded",
            "detection": "not_loaded",
        }

    # ── Status ───────────────────────────────────────────────────────────

    def status(self):
        """Return model loading status dict."""
        return dict(self._model_status)

    # ── Snapshot fetching ────────────────────────────────────────────────

    def fetch_snapshot(self, camera_ip, cam_idx=0, timeout=15):
        """Fetch JPEG snapshot from camera node, decode to BGR numpy array."""
        url = f"http://{camera_ip}:5000/snapshot?cam={cam_idx}"
        resp = urllib.request.urlopen(url, timeout=timeout)
        jpeg_data = resp.read()
        frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8),
                             cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"Failed to decode snapshot from {url}")
        return frame

    # ── Beam detection ───────────────────────────────────────────────────

    def _ensure_beam(self):
        if self._beam is None and _BeamDetector:
            self._beam = _BeamDetector()
        return self._beam is not None

    def detect_beam(self, frame, cam_idx=0, color=None, threshold=30):
        """Detect beam spot in a frame. Returns dict with found, pixelX, pixelY."""
        if not self._ensure_beam():
            return {"found": False, "err": "BeamDetector not available"}
        result = self._beam.detect(frame, cam_idx, color, threshold)
        return result

    def detect_beam_flash(self, frame_on, frame_off, cam_idx=0,
                          color=None, threshold=30):
        """Flash detection from ON/OFF frame pair."""
        if not self._ensure_beam():
            return {"found": False, "err": "BeamDetector not available"}
        # Difference frame: subtract OFF from ON
        diff = cv2.absdiff(frame_on, frame_off)
        return self._beam.detect(diff, cam_idx, color, threshold)

    def set_dark_frame(self, cam_idx, frame):
        """Store dark reference frame for beam detection."""
        if self._ensure_beam():
            self._beam.set_dark_frame(cam_idx, frame)

    # ── Depth estimation ─────────────────────────────────────────────────

    def _ensure_depth(self):
        if self._depth is not None:
            return True
        if not _DepthEstimator:
            self._model_status["depth"] = "unavailable"
            return False
        model_path = _find_model("depth_anything_v2_small.onnx")
        if not model_path:
            self._model_status["depth"] = "model_not_found"
            return False
        with self._lock:
            if self._depth is not None:
                return True
            try:
                # Override the module-level MODEL_PATH before instantiation
                import depth_estimator as _de_mod
                _de_mod.MODEL_PATH = model_path
                self._depth = _DepthEstimator()
                self._model_status["depth"] = "loaded"
                return True
            except Exception as e:
                self._model_status["depth"] = f"error: {e}"
                log.warning("Depth estimator init failed: %s", e)
                return False

    def estimate_depth(self, frame):
        """Returns (depth_map, inference_ms) or raises if unavailable."""
        if not self._ensure_depth():
            raise RuntimeError("Depth estimator not available")
        return self._depth.estimate(frame)

    def pixel_to_3d(self, depth_map, px, py, fov_deg, frame_w, frame_h,
                    max_depth_mm=5000):
        """Project pixel + relative depth to camera-local 3D (mm)."""
        if not self._ensure_depth():
            raise RuntimeError("Depth estimator not available")
        return self._depth.pixel_to_3d(depth_map, px, py, fov_deg,
                                        frame_w, frame_h, max_depth_mm)

    def generate_point_cloud(self, frame, fov_deg, max_points=10000,
                             max_depth_mm=5000, intrinsics=None):
        """Returns (list of [x,y,z,r,g,b], inference_ms)."""
        if not self._ensure_depth():
            raise RuntimeError("Depth estimator not available")
        return self._depth.generate_point_cloud(
            frame, fov_deg, max_points, max_depth_mm, intrinsics)

    # ── Object detection ─────────────────────────────────────────────────

    def _ensure_detector(self):
        if self._detector is not None:
            return True
        if not _ObjectDetector:
            self._model_status["detection"] = "unavailable"
            return False
        model_path = _find_model("yolov8n.onnx")
        if not model_path:
            self._model_status["detection"] = "model_not_found"
            return False
        with self._lock:
            if self._detector is not None:
                return True
            try:
                import detector as _det_mod
                _det_mod.MODEL_PATH = model_path
                self._detector = _ObjectDetector()
                self._model_status["detection"] = "loaded"
                return True
            except Exception as e:
                self._model_status["detection"] = f"error: {e}"
                log.warning("Object detector init failed: %s", e)
                return False

    def detect_objects(self, frame, threshold=0.5, classes=None,
                       input_size=640):
        """YOLOv8n detection. Returns (detections, inference_ms)."""
        if not self._ensure_detector():
            raise RuntimeError("Object detector not available")
        return self._detector.detect(frame, threshold, classes, input_size)

    def detect_objects_tiled(self, frame, threshold=0.5, classes=None,
                              tile_size=640, overlap=0.2):
        """#621 — SAHI-style tiled detection. Slices frame into overlapping
        patches, runs inference per patch, NMS-merges results. Returns
        (detections, inference_ms). Needed for 4K frames where distant
        targets shrink below the single-frame downscale resolution."""
        if not self._ensure_detector():
            raise RuntimeError("Object detector not available")
        return self._detector.detect_tiled(
            frame, threshold=threshold, classes=classes,
            tile_size=tile_size, overlap=overlap)
