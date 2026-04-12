"""Mock camera server for testing — serves synthetic ArUco JPEG snapshots.

Generates a 640x480 image with 6 ArUco DICT_4X4_50 markers (IDs 0-5)
at known pixel positions. Used by test_aruco_orchestrator.py and
test_stage_map.py to test orchestrator-side CV processing without
real camera hardware.
"""
import io
import json
import threading
import time

import cv2
import numpy as np


def _detect_markers(gray, aruco_dict, params):
    """Compat wrapper — OpenCV 4.8+ uses ArucoDetector class."""
    if hasattr(cv2.aruco, 'ArucoDetector'):
        detector = cv2.aruco.ArucoDetector(aruco_dict, params)
        return detector.detectMarkers(gray)
    return cv2.aruco.detectMarkers(gray, aruco_dict, parameters=params)

# ── Marker layout (pixel positions of marker centers on 640x480 image) ──
MARKER_POSITIONS = {
    0: (100, 100),   # top-left area
    1: (320, 80),    # top-center
    2: (540, 100),   # top-right area
    3: (100, 380),   # bottom-left area
    4: (320, 400),   # bottom-center
    5: (540, 380),   # bottom-right area
}
MARKER_SIZE_PX = 80   # pixels per marker (drawn size)
IMAGE_W, IMAGE_H = 640, 480


def generate_aruco_image():
    """Create a BGR image with 6 ArUco 4x4_50 markers at known positions."""
    img = np.ones((IMAGE_H, IMAGE_W, 3), dtype=np.uint8) * 200  # light gray bg
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for mid, (cx, cy) in MARKER_POSITIONS.items():
        marker = cv2.aruco.generateImageMarker(aruco_dict, mid, MARKER_SIZE_PX)
        # Convert grayscale marker to BGR
        marker_bgr = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        x0 = cx - MARKER_SIZE_PX // 2
        y0 = cy - MARKER_SIZE_PX // 2
        x1 = x0 + MARKER_SIZE_PX
        y1 = y0 + MARKER_SIZE_PX
        # Clip to image bounds
        sx0 = max(0, x0)
        sy0 = max(0, y0)
        sx1 = min(IMAGE_W, x1)
        sy1 = min(IMAGE_H, y1)
        mx0 = sx0 - x0
        my0 = sy0 - y0
        mx1 = MARKER_SIZE_PX - (x1 - sx1)
        my1 = MARKER_SIZE_PX - (y1 - sy1)
        img[sy0:sy1, sx0:sx1] = marker_bgr[my0:my1, mx0:mx1]
    return img


def generate_aruco_image_perturbed(seed=None):
    """Create a slightly different view of the markers (for calibration diversity).

    Applies a small random perspective warp so each capture gives
    cv2.calibrateCamera() enough variety to solve for intrinsics.
    """
    rng = np.random.RandomState(seed)
    img = generate_aruco_image()
    # Small perspective jitter: shift corners by +-10 pixels
    src = np.float32([[0, 0], [IMAGE_W, 0], [IMAGE_W, IMAGE_H], [0, IMAGE_H]])
    jitter = rng.uniform(-10, 10, (4, 2)).astype(np.float32)
    dst = src + jitter
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (IMAGE_W, IMAGE_H),
                                borderValue=(200, 200, 200))


def generate_aruco_jpeg():
    """Create JPEG bytes of the ArUco marker image."""
    img = generate_aruco_image()
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


class MockCameraServer:
    """Threaded Flask mock camera that serves synthetic ArUco snapshots."""

    def __init__(self, port=5000):
        from flask import Flask, Response, jsonify
        self._app = Flask("mock_camera")
        self._port = port
        self._thread = None
        self._server = None
        self._frame_counter = 0
        self.actual_port = None

        app = self._app

        @app.get("/snapshot")
        def snapshot():
            # Each request gets a slightly different perspective for calibration diversity
            self._frame_counter += 1
            img = generate_aruco_image_perturbed(seed=self._frame_counter)
            ok_, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            return Response(buf.tobytes(), mimetype="image/jpeg")

        @app.get("/status")
        def status():
            return jsonify(ok=True, hostname="MOCK-CAM", version="0.0.0",
                           cameras=[{"idx": 0, "name": "mock", "enabled": True}])

        @app.get("/health")
        def health():
            return jsonify(ok=True)

        @app.post("/dark-reference")
        def dark_ref():
            return jsonify(ok=True)

        @app.post("/calibrate/intrinsic/save")
        def intrinsic_save():
            return jsonify(ok=True)

        @app.post("/beam-detect")
        def beam_detect():
            return jsonify(found=False)

        @app.post("/beam-detect/flash")
        def beam_flash():
            return jsonify(found=False)

        @app.get("/calibrate/intrinsic")
        def intrinsic_get():
            return jsonify(ok=True, calibrated=False)

    def start(self):
        """Start the mock server in a daemon thread. Returns actual port."""
        import socket
        if self._port == 0:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', 0))
            self._port = s.getsockname()[1]
            s.close()
        self.actual_port = self._port

        def _run():
            from werkzeug.serving import make_server
            self._server = make_server('127.0.0.1', self._port, self._app,
                                        threaded=True)
            self._server.serve_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        # Wait for server to be ready
        for _ in range(50):
            try:
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{self._port}/health",
                                       timeout=1)
                return self._port
            except Exception:
                time.sleep(0.1)
        raise RuntimeError(f"Mock camera failed to start on port {self._port}")

    def stop(self):
        """Shut down the mock server."""
        if self._server:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.actual_port}"

    @property
    def ip(self):
        return f"127.0.0.1:{self.actual_port}"


if __name__ == "__main__":
    # Quick self-test: verify markers are detectable
    img = generate_aruco_image()
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    params = cv2.aruco.DetectorParameters()
    corners, ids, rejected = _detect_markers(
        cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), aruco_dict, params)
    if ids is not None:
        print(f"Detected {len(ids)} markers: {ids.flatten().tolist()}")
    else:
        print("No markers detected!")
    cv2.imwrite("/mnt/d/temp/mock_aruco.png", img)
    print("Saved to /mnt/d/temp/mock_aruco.png")
