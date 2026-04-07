"""
beam_detector.py — Fast beam detection for moving head calibration.

Color-filtered bright spot detection with 3-beam center identification.
Designed for <100ms per detection (no YOLO, just OpenCV threshold + centroid).
"""

import logging
import threading

import cv2
import numpy as np

log = logging.getLogger("slyled-cam")

# Color hue ranges in HSV (OpenCV uses H=0-180)
COLOR_RANGES = {
    "red":     [(0, 60, 80, 12, 255, 255), (168, 60, 80, 180, 255, 255)],  # wraps
    "green":   [(35, 60, 80, 85, 255, 255)],
    "blue":    [(100, 60, 80, 130, 255, 255)],
    "magenta": [(140, 40, 80, 170, 255, 255)],
    "white":   None,  # use brightness only
}


class BeamDetector:
    """Fast beam detection with dark-frame differencing and color filtering."""

    def __init__(self):
        self._dark_frames = {}  # {cam_idx: grayscale numpy array}
        self._lock = threading.Lock()

    def set_dark_frame(self, cam_idx, frame):
        """Store a dark reference frame for a camera."""
        with self._lock:
            if frame is not None:
                self._dark_frames[cam_idx] = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) \
                    if len(frame.shape) == 3 else frame.copy()

    def has_dark_frame(self, cam_idx):
        return cam_idx in self._dark_frames

    def detect(self, frame, cam_idx=0, color=None, threshold=30):
        """Detect a bright beam spot in the frame.

        Args:
            frame: BGR numpy array
            cam_idx: camera index (for dark frame lookup)
            color: [r, g, b] beam color to filter for, or None for brightness-only
            threshold: minimum difference from dark frame

        Returns:
            dict with {found, pixelX, pixelY, peakIntensity, area} or {found: False}
        """
        if frame is None:
            return {"found": False}

        # Build detection mask
        if color and color != [255, 255, 255]:
            mask = self._color_mask(frame, color)
        else:
            mask = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Apply dark-frame differencing if available
        dark = self._dark_frames.get(cam_idx)
        if dark is not None:
            if len(mask.shape) != len(dark.shape):
                dark_resized = dark
            else:
                dark_resized = dark
            if mask.shape != dark_resized.shape:
                dark_resized = cv2.resize(dark_resized, (mask.shape[1], mask.shape[0]))
            mask = cv2.absdiff(mask, dark_resized)

        # Blur and threshold
        mask = cv2.GaussianBlur(mask, (15, 15), 0)
        _, peak_val, _, _ = cv2.minMaxLoc(mask)
        if peak_val < threshold:
            return {"found": False}

        thresh_val = max(threshold, int(peak_val * 0.35))
        _, binary = cv2.threshold(mask, thresh_val, 255, cv2.THRESH_BINARY)

        # Find centroid
        ys, xs = np.where(binary > 0)
        if len(xs) < 5:
            return {"found": False}

        return {
            "found": True,
            "pixelX": int(np.mean(xs)),
            "pixelY": int(np.mean(ys)),
            "peakIntensity": int(peak_val),
            "area": int(len(xs)),
        }

    def detect_center(self, frame, cam_idx=0, color=None, threshold=30, beam_count=3):
        """Detect the center beam of a multi-beam fixture.

        Uses connectedComponents to identify individual beam spots,
        then returns the one with the median X position (center beam).
        """
        if frame is None:
            return {"found": False}

        if color and color != [255, 255, 255]:
            mask = self._color_mask(frame, color)
        else:
            mask = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        dark = self._dark_frames.get(cam_idx)
        if dark is not None:
            if mask.shape != dark.shape:
                dark_r = cv2.resize(dark, (mask.shape[1], mask.shape[0]))
            else:
                dark_r = dark
            mask = cv2.absdiff(mask, dark_r)

        mask = cv2.GaussianBlur(mask, (11, 11), 0)
        _, peak_val, _, _ = cv2.minMaxLoc(mask)
        if peak_val < threshold:
            return {"found": False}

        thresh_val = max(threshold, int(peak_val * 0.35))
        _, binary = cv2.threshold(mask, thresh_val, 255, cv2.THRESH_BINARY)

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8)

        # Filter out background (label 0) and tiny components
        components = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < 5:
                continue
            cx, cy = centroids[i]
            components.append({"idx": i, "cx": cx, "cy": cy, "area": area})

        if not components:
            return {"found": False}

        # If we found the expected beam count, pick the center one (median X)
        if len(components) >= beam_count:
            # Sort by X, pick middle
            components.sort(key=lambda c: c["cx"])
            center = components[len(components) // 2]
        elif len(components) >= 2:
            # Fewer than expected — pick the one closest to the geometric center
            avg_cx = np.mean([c["cx"] for c in components])
            avg_cy = np.mean([c["cy"] for c in components])
            center = min(components,
                         key=lambda c: (c["cx"] - avg_cx)**2 + (c["cy"] - avg_cy)**2)
        else:
            center = components[0]

        return {
            "found": True,
            "pixelX": int(center["cx"]),
            "pixelY": int(center["cy"]),
            "peakIntensity": int(peak_val),
            "area": int(center["area"]),
            "beamCount": len(components),
        }

    def _color_mask(self, frame, color):
        """Create a grayscale mask emphasizing the target color."""
        r, g, b = color
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Determine which color range to use
        hue_name = self._classify_color(r, g, b)
        ranges = COLOR_RANGES.get(hue_name)

        if ranges is None:
            # White or unknown — use brightness
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for rng in ranges:
            lo = np.array([rng[0], rng[1], rng[2]])
            hi = np.array([rng[3], rng[4], rng[5]])
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))

        return mask

    @staticmethod
    def _classify_color(r, g, b):
        """Classify an RGB color to the nearest detection range."""
        if r > 200 and g < 100 and b < 100:
            return "red"
        if g > 200 and r < 100 and b < 100:
            return "green"
        if b > 200 and r < 100 and g < 100:
            return "blue"
        if r > 150 and b > 150 and g < 100:
            return "magenta"
        return "white"
