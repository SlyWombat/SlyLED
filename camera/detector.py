"""
detector.py — YOLOv8n object detection via ONNX Runtime.

Lazy-loads the ONNX model on first detect() call.
Thread-safe: single instance reused across Flask requests.
Falls back to OpenCV DNN if onnxruntime is not available.
"""

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("slyled-cam")

MODEL_DIR = Path("/opt/slyled/models")
MODEL_PATH = MODEL_DIR / "yolov8n.onnx"

# COCO 80-class labels (YOLOv8 default)
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


class ObjectDetector:
    """YOLOv8n detector. Prefers onnxruntime, falls back to OpenCV DNN."""

    # Model is exported with fixed 640x640 input
    MODEL_SIZE = 640

    def __init__(self):
        self._session = None  # onnxruntime session or OpenCV net
        self._backend = None  # "ort" or "cv2"
        self._lock = threading.Lock()

    def _ensure_model(self):
        """Check that the ONNX model exists (deployed via SCP)."""
        if MODEL_PATH.exists():
            return
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH} — deploy from the Firmware tab to upload it"
        )

    def _load(self):
        """Load the ONNX model."""
        if self._session is not None:
            return
        self._ensure_model()
        log.info("Loading YOLOv8n model...")
        t0 = time.monotonic()

        # Try onnxruntime first (better ONNX compatibility, especially on ARM)
        try:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 4
            self._session = ort.InferenceSession(str(MODEL_PATH), opts,
                                                  providers=["CPUExecutionProvider"])
            self._backend = "ort"
        except ImportError:
            # Fall back to OpenCV DNN
            self._session = cv2.dnn.readNetFromONNX(str(MODEL_PATH))
            self._session.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._session.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self._backend = "cv2"

        elapsed = (time.monotonic() - t0) * 1000
        log.info("Model loaded in %.0f ms (backend: %s)", elapsed, self._backend)

    def _infer(self, blob):
        """Run forward pass. Returns raw output array."""
        if self._backend == "ort":
            input_name = self._session.get_inputs()[0].name
            return self._session.run(None, {input_name: blob})[0]
        else:
            self._session.setInput(blob)
            return self._session.forward()

    def detect(self, frame, threshold=0.5, classes=None, input_size=640):
        """Run YOLOv8n detection on a BGR frame.

        Args:
            frame: numpy array (H, W, 3) BGR
            threshold: confidence threshold (0.0-1.0)
            classes: list of class names to include (None = all)
            input_size: pre-resize cap (320 = downscale first for speed, 640 = full detail)

        Returns:
            (detections, inference_ms)
            detections: list of dicts [{label, confidence, x, y, w, h}]
            x, y are top-left corner in pixel coords; w, h are box dimensions
        """
        with self._lock:
            self._load()

            orig_h, orig_w = frame.shape[:2]
            img_h, img_w = orig_h, orig_w

            # Optional pre-downscale for speed (input_size < 640)
            pre_scale = 1.0
            if int(input_size) < self.MODEL_SIZE:
                cap = int(input_size)
                pre_scale = min(cap / img_w, cap / img_h)
                if pre_scale < 1.0:
                    frame = cv2.resize(frame, (int(img_w * pre_scale), int(img_h * pre_scale)),
                                       interpolation=cv2.INTER_LINEAR)
                    img_h, img_w = frame.shape[:2]

            # Letterbox resize to model's fixed input size
            size = self.MODEL_SIZE
            scale = min(size / img_w, size / img_h)
            new_w, new_h = int(img_w * scale), int(img_h * scale)
            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

            # Pad to square
            canvas = np.full((size, size, 3), 114, dtype=np.uint8)
            pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
            canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

            # Create blob: NCHW, float32, normalized 0-1, RGB
            blob = cv2.dnn.blobFromImage(canvas, 1.0 / 255.0, (size, size),
                                         swapRB=True, crop=False)

            # YOLOv8 output: (1, 84, N) where 84 = 4 bbox + 80 classes
            t0 = time.monotonic()
            outputs = self._infer(blob)
            inference_ms = (time.monotonic() - t0) * 1000

            # Transpose to (N, 84)
            preds = outputs[0].T  # (N, 84)

            # Filter by confidence
            class_scores = preds[:, 4:]  # (N, 80)
            max_scores = np.max(class_scores, axis=1)
            mask = max_scores >= threshold
            preds = preds[mask]
            max_scores = max_scores[mask]

            if len(preds) == 0:
                return [], inference_ms

            # Get class IDs
            class_ids = np.argmax(preds[:, 4:], axis=1)

            # Convert xywh (center) to xyxy
            cx, cy, bw, bh = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
            x1 = cx - bw / 2
            y1 = cy - bh / 2
            x2 = cx + bw / 2
            y2 = cy + bh / 2

            # NMS
            boxes_for_nms = np.stack([x1, y1, bw, bh], axis=1).tolist()
            indices = cv2.dnn.NMSBoxes(boxes_for_nms, max_scores.tolist(), threshold, 0.45)
            if len(indices) == 0:
                return [], inference_ms

            # Build results, rescale to original image coords
            detections = []
            indices = np.array(indices).flatten()
            for i in indices:
                cid = int(class_ids[i])
                label = COCO_LABELS[cid] if cid < len(COCO_LABELS) else f"class_{cid}"

                # Apply class filter
                if classes and label not in classes:
                    continue

                # Rescale from letterboxed coords to original image
                total_scale = scale * pre_scale
                bx1 = (float(x1[i]) - pad_x) / total_scale
                by1 = (float(y1[i]) - pad_y) / total_scale
                bx2 = (float(x2[i]) - pad_x) / total_scale
                by2 = (float(y2[i]) - pad_y) / total_scale

                # Clamp to original image bounds
                bx1 = max(0, min(bx1, orig_w))
                by1 = max(0, min(by1, orig_h))
                bx2 = max(0, min(bx2, orig_w))
                by2 = max(0, min(by2, orig_h))

                detections.append({
                    "label": label,
                    "confidence": round(float(max_scores[i]), 3),
                    "x": round(bx1),
                    "y": round(by1),
                    "w": round(bx2 - bx1),
                    "h": round(by2 - by1),
                })

            return detections, inference_ms
