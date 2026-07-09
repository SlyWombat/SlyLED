"""
tracker.py — Continuous person tracking with proximity-based re-ID.

Runs a detection loop on the camera, matches detections to existing tracked
objects by proximity, and pushes temporal stage objects to the orchestrator.
Thread-safe: one tracker per camera node.
"""

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("slyled-cam")

# #896 — re-ID proximity gate. Track coordinates are PIXEL centres (the
# old `_px_to_stage` hook was never wired), so despite the historical
# "mm" naming the gate has always been pixels. Be honest about it: the
# configured value (API field `reidMm`, kept for wire compat) is treated
# as pixels at a 640-wide reference frame and scaled by the actual frame
# width, so higher-resolution captures don't tighten the gate relative
# to the image. Default 500 reproduces the old behaviour on a 640-wide
# frame. True stage-space (mm) gating is future work (#896).
REID_GATE_PX_AT_REF = 500
REID_REF_FRAME_W = 640.0

# #896 — orchestrator-push queue bound. Small on purpose: entries are
# superseded every tick, so when the orchestrator lags we drop the
# oldest rather than serve it stale positions.
PUSH_QUEUE_MAX = 64


class Tracker:
    """Continuous tracking loop with proximity-based re-ID."""

    def __init__(self, detector, capture_fn):
        """
        Args:
            detector: ObjectDetector instance
            capture_fn: callable(device) → BGR numpy frame or None
        """
        self._detector = detector
        self._capture = capture_fn
        self._orch_url = ""
        self._cam_id = 0
        self._fps = 2
        self._threshold = 0.4
        self._ttl = 5
        self._classes = ["person"]
        self._class_thresholds = None  # #423 — per-class threshold override
        self._reid_gate_ref_px = REID_GATE_PX_AT_REF  # #896 — px at 640-wide ref frame
        self._input_size = 320
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._tracks = {}
        self._next_track_id = 0
        self._tick_count = 0
        self._capture_fail_count = 0
        self._detect_count = 0
        self._last_error = None
        # #896 — orchestrator pushes run on a single sender daemon so
        # the detection tick rate is independent of orchestrator
        # latency. Bounded queue; drop-oldest on overflow.
        self._push_q = queue.Queue(maxsize=PUSH_QUEUE_MAX)
        self._sender_thread = None

    @property
    def running(self):
        return self._running

    @property
    def track_count(self):
        return len(self._tracks)

    @property
    def debug_info(self):
        return {
            "running": self._running,
            "trackCount": len(self._tracks),
            "ticks": self._tick_count,
            "captureFails": self._capture_fail_count,
            "detections": self._detect_count,
            "lastError": self._last_error,
            "orchestratorUrl": self._orch_url,
            "classes": self._classes,
            # #896 — key kept as "reidMm" for API compat; value is the
            # pixel gate at the 640-wide reference frame (see header).
            "reidMm": self._reid_gate_ref_px,
            "pushQueueDepth": self._push_q.qsize(),
        }

    def start(self, device, orch_url="", camera_id=0,
              fps=2, threshold=0.4, ttl=5,
              classes=None, reid_mm=None, input_size=None,
              class_thresholds=None):
        """Start tracking loop on the given camera device."""
        if self._running:
            return
        self._orch_url = orch_url.rstrip("/") if orch_url else ""
        self._cam_id = camera_id
        self._fps = fps
        self._threshold = threshold
        self._ttl = ttl
        self._classes = classes if classes else ["person"]
        self._class_thresholds = dict(class_thresholds) if class_thresholds else None
        # #896 — kwarg kept as `reid_mm` for API compat but it has always
        # been a pixel gate (at a 640-wide reference frame); see header.
        self._reid_gate_ref_px = reid_mm if reid_mm is not None else REID_GATE_PX_AT_REF
        self._input_size = input_size if input_size else 320
        self._running = True
        self._push_q = queue.Queue(maxsize=PUSH_QUEUE_MAX)  # fresh queue per session
        self._thread = threading.Thread(target=self._loop, args=(device,), daemon=True)
        self._thread.start()
        # #896 — single sender daemon decouples HTTP pushes from the tick
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        log.info("Tracking started on %s (fps=%d, thr=%.2f, ttl=%ds)",
                 device, self._fps, self._threshold, self._ttl)

    def stop(self):
        """Stop tracking loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._sender_thread:
            self._sender_thread.join(timeout=5)
            self._sender_thread = None
        self._tracks.clear()
        log.info("Tracking stopped")

    def _loop(self, device):
        interval = 1.0 / max(self._fps, 0.1)
        tick_count = 0
        fail_count = 0
        # Keep camera open for the entire tracking session (fast capture)
        cap = None
        try:
            import cv2
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                # Capture at native resolution for full FOV — YOLO pre-downscales internally
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                log.info("Tracking: opened persistent capture %s at %dx%d (native)", device, actual_w, actual_h)
            else:
                log.warning("Tracking: failed to open persistent capture %s", device)
                cap = None
        except Exception as e:
            log.warning("Tracking: persistent capture init failed: %s", e)
            cap = None
        while self._running:
            t0 = time.monotonic()
            try:
                self._tick(device, cap)
                tick_count += 1
                if tick_count == 1:
                    log.info("Tracking: first tick OK on %s", device)
                # Reopen camera after 10 consecutive capture failures
                if self._capture_fail_count == 10:
                    log.info("Tracking: 10 capture failures — reopening %s", device)
                    try:
                        import cv2
                        if cap:
                            cap.release()
                        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
                        if cap.isOpened():
                            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                            log.info("Tracking: reopened %s", device)
                        self._capture_fail_count = 11  # prevent re-trigger next tick
                    except Exception as e:
                        log.warning("Tracking: reopen failed: %s", e)
            except Exception as e:
                fail_count += 1
                self._last_error = str(e)
                log.warning("Tracking tick error (#%d): %s", fail_count, e)
                if fail_count >= 10 and tick_count == 0:
                    log.error("Tracking: 10 consecutive failures with no success — stopping")
                    self._running = False
                    break
            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        if cap:
            cap.release()
            log.info("Tracking: released persistent capture")
        log.info("Tracking loop exited: %d ticks, %d errors", tick_count, fail_count)

    def _tick(self, device, cap=None):
        self._tick_count += 1
        # Fast capture from persistent VideoCapture, or fallback to _cv_capture
        frame = None
        if cap and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                frame = None
        if frame is None:
            frame = self._capture(device)
        if frame is None:
            self._capture_fail_count += 1
            if self._capture_fail_count <= 3 or self._capture_fail_count % 20 == 0:
                log.warning("Tracking: capture returned None for %s (fail #%d)",
                            device, self._capture_fail_count)
            return
        self._capture_fail_count = 0

        # Run detection
        detections, _ = self._detector.detect(
            frame, threshold=self._threshold,
            classes=self._classes,
            class_thresholds=self._class_thresholds,
            input_size=self._input_size)
        if not detections:
            return
        self._detect_count += len(detections)

        # Keep raw pixel data for orchestrator-side conversion.
        # Pixel centres are the internal tracking coords (re-ID proximity);
        # real stage conversion happens on the orchestrator via
        # cameraId + pixelBox. Z=0 floor (#387).
        frame_h, frame_w = frame.shape[:2]
        stage_dets = [{"label": d["label"], "confidence": d["confidence"],
                       "x": d["x"] + d["w"] // 2,
                       "y": d["y"] + d["h"] // 2,
                       "z": 0,
                       "w": d["w"], "h": d["h"],
                       "_raw": d, "_frameSize": [frame_w, frame_h],
                       } for d in detections]

        now = time.monotonic()
        matched_track_ids = set()
        # #896 — pixel gate scaled from the 640-wide reference (header note)
        gate_px = self._reid_gate_ref_px * (frame_w / REID_REF_FRAME_W)

        # Match detections to existing tracks by pixel-centre proximity
        for det in stage_dets:
            best_id = None
            best_dist = gate_px + 1
            for tid, trk in self._tracks.items():
                dx = det["x"] - trk["x"]
                dy = det["y"] - trk["y"]
                dist = (dx*dx + dy*dy) ** 0.5
                if dist < best_dist and tid not in matched_track_ids:
                    best_dist = dist
                    best_id = tid

            if best_id is not None and best_dist <= gate_px:
                # Update existing track
                trk = self._tracks[best_id]
                trk["x"] = det["x"]
                trk["y"] = det["y"]
                trk["label"] = det.get("label", "person")
                trk["last_seen"] = now
                matched_track_ids.add(best_id)
                self._enqueue_push(best_id, det)
            else:
                # New track. The orchestrator object is created by the
                # sender thread (#896) — orch_obj_id stays None until it
                # succeeds, and the track re-IDs locally in the meantime.
                with self._lock:
                    tid = self._next_track_id
                    self._next_track_id += 1
                self._tracks[tid] = {
                    "x": det["x"], "y": det["y"],
                    "label": det.get("label", "person"),
                    "last_seen": now,
                    "orch_obj_id": None,
                }
                matched_track_ids.add(tid)
                self._enqueue_push(tid, det)

        # Remove stale tracks (not seen for > TTL)
        stale = [tid for tid, trk in self._tracks.items()
                 if now - trk["last_seen"] > self._ttl]
        for tid in stale:
            del self._tracks[tid]

    def _enqueue_push(self, tid, det):
        """#896 — queue an orchestrator push for a track. Non-blocking:
        the detection tick never waits on HTTP. Drop-oldest on overflow
        (a newer position for the same scene supersedes it anyway)."""
        if not self._orch_url:
            return
        try:
            self._push_q.put_nowait((tid, det))
        except queue.Full:
            try:
                self._push_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._push_q.put_nowait((tid, det))
            except queue.Full:
                pass

    def _sender_loop(self):
        """#896 — single sender daemon: drains the push queue so tick
        rate is independent of orchestrator latency. Capped backoff on
        consecutive failures."""
        fails = 0
        while self._running:
            try:
                tid, det = self._push_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._process_push(tid, det):
                fails = 0
            else:
                fails += 1
                time.sleep(min(0.25 * (2 ** min(fails, 5)), 5.0))

    def _process_push(self, tid, det):
        """Send one queued push. Returns True on success (or a skippable
        stale entry), False on a transient failure worth backing off."""
        trk = self._tracks.get(tid)
        if trk is None:
            return True  # track reaped while queued — nothing to do
        obj_id = trk.get("orch_obj_id")
        if obj_id is None:
            new_id = self._orch_create_temporal(det)
            if new_id is None:
                return False
            trk["orch_obj_id"] = new_id
            return True
        result = self._orch_update_pos(obj_id, det)
        if result is None:
            return False
        if result == -1:
            # 404 — orchestrator no longer knows this id (expired /
            # deleted / fusion map pruned). Drop it so the next push
            # re-creates instead of flogging the stale id (#896).
            trk["orch_obj_id"] = None
            return True
        if result != obj_id:
            # #896 — id was fused away server-side; rebind to survivor.
            log.info("Track %d rebound: orchestrator object %s → %s (fused)",
                     tid, obj_id, result)
            trk["orch_obj_id"] = result
        return True

    def _orch_create_temporal(self, det):
        """Create a temporal object on the orchestrator. Returns object ID or None."""
        try:
            body = {
                "name": det.get("label", "person"),
                "objectType": det.get("label", "person"),
                "ttl": self._ttl,
                "color": "#f472b6",
                "opacity": 40,
                "pos": [det["x"], det["y"], 0],   # stage: X=width, Y=depth, Z=0 (floor)
                # [width, height(Z), depth(Y)] — matches renderer convention
                "scale": [det.get("w", 400), 1700, 400],
            }
            # Send raw pixel box + camera ID for orchestrator-side pixel→stage conversion
            raw = det.get("_raw")
            fs = det.get("_frameSize")
            if raw and fs and self._cam_id:
                body["cameraId"] = self._cam_id
                body["pixelBox"] = {"x": raw["x"], "y": raw["y"],
                                    "w": raw.get("w", 100), "h": raw.get("h", 200)}
                body["frameSize"] = fs
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{self._orch_url}/api/objects/temporal",
                data=data, headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=2)
            r = json.loads(resp.read().decode())
            return r.get("id")
        except Exception as e:
            self._last_error = f"create_temporal: {e}"
            log.warning("Failed to create temporal object at %s: %s",
                        self._orch_url, e)
            return None

    def _orch_update_pos(self, obj_id, det):
        """Update position of an existing temporal object.
        Sends pixel data for orchestrator-side conversion.

        Returns the orchestrator's current id for the object (#896 —
        differs from obj_id when it was fused into a survivor), -1 when
        the id is gone (404, caller should re-create), or None on a
        transient failure."""
        try:
            body = {"pos": [det["x"], det["y"], 0]}
            raw = det.get("_raw")
            fs = det.get("_frameSize")
            if raw and fs and self._cam_id:
                body["cameraId"] = self._cam_id
                body["pixelBox"] = {"x": raw["x"], "y": raw["y"],
                                    "w": raw.get("w", 100), "h": raw.get("h", 200)}
                body["frameSize"] = fs
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{self._orch_url}/api/objects/{obj_id}/pos",
                data=data, headers={"Content-Type": "application/json"},
                method="PUT")
            resp = urllib.request.urlopen(req, timeout=2)
            try:
                r = json.loads(resp.read().decode())
            except ValueError:
                return obj_id  # non-JSON 2xx — treat as plain success
            new_id = r.get("objectId")
            return new_id if isinstance(new_id, int) else obj_id
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return -1
            self._last_error = f"update_pos({obj_id}): {e}"
            if self._tick_count <= 3 or self._tick_count % 50 == 0:
                log.warning("Failed to update temporal object %s: %s", obj_id, e)
            return None
        except Exception as e:
            self._last_error = f"update_pos({obj_id}): {e}"
            if self._tick_count <= 3 or self._tick_count % 50 == 0:
                log.warning("Failed to update temporal object %s: %s", obj_id, e)
            return None
