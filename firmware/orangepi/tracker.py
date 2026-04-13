"""
tracker.py — Continuous person tracking with proximity-based re-ID.

Runs a detection loop on the camera, matches detections to existing tracked
objects by proximity, and pushes temporal stage objects to the orchestrator.
Thread-safe: one tracker per camera node.
"""

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("slyled-cam")

# Default re-ID proximity threshold (mm) — detections within this distance
# of an existing tracked object are considered the same person
REID_THRESHOLD_MM = 500


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
        self._px_to_stage = None
        self._fps = 2
        self._threshold = 0.4
        self._ttl = 5
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._tracks = {}
        self._next_track_id = 0
        self._tick_count = 0
        self._capture_fail_count = 0
        self._detect_count = 0
        self._last_error = None

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
        }

    def start(self, device, orch_url="", camera_id=0,
              fps=2, threshold=0.4, ttl=5):
        """Start tracking loop on the given camera device."""
        if self._running:
            return
        self._orch_url = orch_url.rstrip("/") if orch_url else ""
        self._cam_id = camera_id
        self._fps = fps
        self._threshold = threshold
        self._ttl = ttl
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(device,), daemon=True)
        self._thread.start()
        log.info("Tracking started on %s (fps=%d, thr=%.2f, ttl=%ds)",
                 device, self._fps, self._threshold, self._ttl)

    def stop(self):
        """Stop tracking loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._tracks.clear()
        log.info("Tracking stopped")

    def _loop(self, device):
        interval = 1.0 / max(self._fps, 0.1)
        tick_count = 0
        fail_count = 0
        while self._running:
            t0 = time.monotonic()
            try:
                self._tick(device)
                tick_count += 1
                if tick_count == 1:
                    log.info("Tracking: first tick OK on %s", device)
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
        log.info("Tracking loop exited: %d ticks, %d errors", tick_count, fail_count)

    def _tick(self, device):
        self._tick_count += 1
        # Capture frame
        frame = self._capture(device)
        if frame is None:
            self._capture_fail_count += 1
            if self._capture_fail_count <= 3 or self._capture_fail_count % 20 == 0:
                log.warning("Tracking: capture returned None for %s (fail #%d)",
                            device, self._capture_fail_count)
            return

        # Run detection
        detections, _ = self._detector.detect(frame, threshold=self._threshold,
                                                classes=["person"],
                                                input_size=320)
        if not detections:
            return
        self._detect_count += len(detections)

        # Transform to stage coords if available
        frame_h, frame_w = frame.shape[:2]
        if self._px_to_stage:
            stage_dets = self._px_to_stage(detections, frame_w, frame_h)
        else:
            # Fallback: use raw pixel coords as mm (very rough)
            stage_dets = [{"label": d["label"], "confidence": d["confidence"],
                           "x": d["x"], "y": 0, "z": d["y"],
                           "w": d["w"], "h": d["h"]} for d in detections]

        now = time.monotonic()
        matched_track_ids = set()

        # Match detections to existing tracks by proximity
        for det in stage_dets:
            best_id = None
            best_dist = REID_THRESHOLD_MM + 1
            for tid, trk in self._tracks.items():
                dx = det["x"] - trk["x"]
                dz = det["z"] - trk["z"]
                dist = (dx*dx + dz*dz) ** 0.5
                if dist < best_dist and tid not in matched_track_ids:
                    best_dist = dist
                    best_id = tid

            if best_id is not None and best_dist <= REID_THRESHOLD_MM:
                # Update existing track
                trk = self._tracks[best_id]
                trk["x"] = det["x"]
                trk["z"] = det["z"]
                trk["label"] = det.get("label", "person")
                trk["last_seen"] = now
                matched_track_ids.add(best_id)
                # Update position on orchestrator
                self._orch_update_pos(trk["orch_obj_id"], det["x"], det["z"])
            else:
                # New track
                with self._lock:
                    tid = self._next_track_id
                    self._next_track_id += 1
                orch_id = self._orch_create_temporal(det)
                if orch_id is not None:
                    self._tracks[tid] = {
                        "x": det["x"], "z": det["z"],
                        "label": det.get("label", "person"),
                        "last_seen": now,
                        "orch_obj_id": orch_id,
                    }
                    matched_track_ids.add(tid)

        # Remove stale tracks (not seen for > TTL)
        stale = [tid for tid, trk in self._tracks.items()
                 if now - trk["last_seen"] > self._ttl]
        for tid in stale:
            del self._tracks[tid]

    def _orch_create_temporal(self, det):
        """Create a temporal object on the orchestrator. Returns object ID or None."""
        try:
            data = json.dumps({
                "name": det.get("label", "person"),
                "objectType": det.get("label", "person"),
                "ttl": self._ttl,
                "color": "#f472b6",
                "opacity": 40,
                "transform": {
                    "pos": [det["x"], det["z"], 0],
                    "rot": [0, 0, 0],
                    "scale": [det.get("w", 400), 200, det.get("h", 400)],
                },
            }).encode()
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

    def _orch_update_pos(self, obj_id, x, z):
        """Update position of an existing temporal object.
        Internal x=width, z=depth; mapped to stage coords X=width, Y=depth, Z=0."""
        try:
            data = json.dumps({"pos": [x, z, 0]}).encode()
            req = urllib.request.Request(
                f"{self._orch_url}/api/objects/{obj_id}/pos",
                data=data, headers={"Content-Type": "application/json"},
                method="PUT")
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # Object may have expired
