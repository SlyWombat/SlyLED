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

    def detect(self, frame, threshold=0.5, classes=None, input_size=640,
               class_thresholds=None):
        """Run YOLOv8n detection on a BGR frame.

        Args:
            frame: numpy array (H, W, 3) BGR
            threshold: default confidence threshold (0.0-1.0)
            classes: list of class names to include (None = all)
            class_thresholds: optional dict of {class_name: threshold}
                overriding `threshold` per class. #423 — YOLOv8n scores
                furniture classes (chair, couch, dining table) in the
                0.2-0.35 band; a global 0.4 threshold kills them while
                a person-only rig is happy at 0.5. Per-class thresholds
                let the operator lower the floor just for the class
                that needs it.
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

            # Filter by confidence. #423 — if class_thresholds is set
            # we pre-filter at the minimum of any configured threshold
            # so lower-floor classes (e.g. chair at 0.25) have material
            # to survive the class-aware post-filter below.
            class_scores = preds[:, 4:]  # (N, 80)
            max_scores = np.max(class_scores, axis=1)
            min_pre_threshold = float(threshold)
            if class_thresholds:
                try:
                    min_pre_threshold = min(
                        min_pre_threshold,
                        *(float(v) for v in class_thresholds.values()))
                except Exception:
                    pass
            mask = max_scores >= min_pre_threshold
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
                # #423 — per-class threshold enforcement. If the caller
                # supplied a class-specific threshold, enforce it here
                # (the pre-NMS mask ran at min(thresholds) so enough
                # candidates survived for this per-label decision).
                if class_thresholds:
                    cls_thr = float(class_thresholds.get(label, threshold))
                    if float(max_scores[i]) < cls_thr:
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


    def detect_tiled(self, frame, threshold=0.5, classes=None,
                     tile_size=640, overlap=0.2):
        """#621 — SAHI-style tiled detection for high-res frames.

        Slices the frame into overlapping tiles, runs self.detect() on
        each at full model resolution, then merges the per-tile results
        with a global NMS pass. Preserves small-object detail that
        single-frame MODEL_SIZE downscaling washes out (a 1.8 m person
        standing 5 m from a 4K camera occupies ~20 px after downscale
        to 640×640, below YOLOv8n's effective floor).

        Args:
            frame: BGR numpy array.
            threshold: confidence cutoff — same as detect().
            classes: allowlist (same shape).
            tile_size: edge length of each tile in frame pixels. Default
                640 matches the model's input size so each tile runs
                through without further downscale.
            overlap: tile-to-tile overlap fraction (0.0-0.5 typical).
                Catches objects straddling tile boundaries.

        Returns: (detections, inference_ms) with bboxes already in
        whole-frame coordinates.
        """
        import time as _time
        import cv2 as _cv2
        import numpy as _np

        H, W = frame.shape[:2]
        tile = int(tile_size)
        step = max(1, int(tile * (1.0 - max(0.0, min(0.5, overlap)))))
        xs = list(range(0, max(1, W - tile + 1), step))
        ys = list(range(0, max(1, H - tile + 1), step))
        if not xs or xs[-1] + tile < W:
            xs.append(max(0, W - tile))
        if not ys or ys[-1] + tile < H:
            ys.append(max(0, H - tile))

        all_dets = []
        t0 = _time.monotonic()
        for y0 in ys:
            for x0 in xs:
                x1_c = min(W, x0 + tile)
                y1_c = min(H, y0 + tile)
                patch = frame[y0:y1_c, x0:x1_c]
                dets, _ = self.detect(patch, threshold=threshold,
                                        classes=classes,
                                        input_size=tile)
                for d in dets:
                    d2 = dict(d)
                    d2["x"] = d["x"] + x0
                    d2["y"] = d["y"] + y0
                    all_dets.append(d2)
        inference_ms = (_time.monotonic() - t0) * 1000

        if not all_dets:
            return [], inference_ms

        # Global NMS across all tiles — a person seen in two overlapping
        # tiles returns as two detections that NMS collapses to one.
        boxes = [[d["x"], d["y"], d["w"], d["h"]] for d in all_dets]
        scores = [float(d["confidence"]) for d in all_dets]
        try:
            keep = _cv2.dnn.NMSBoxes(boxes, scores, threshold, 0.45)
        except Exception:
            keep = list(range(len(all_dets)))
        if len(keep) == 0:
            return [], inference_ms
        keep_idx = _np.array(keep).flatten()
        merged = [all_dets[int(i)] for i in keep_idx]
        return merged, inference_ms
