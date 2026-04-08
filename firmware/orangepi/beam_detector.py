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
        """Store a dark reference frame (full BGR) for a camera."""
        with self._lock:
            if frame is not None:
                self._dark_frames[cam_idx] = frame.copy()

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

        dark_bgr = self._dark_frames.get(cam_idx)

        # Step 1: Color diff between light and dark frames
        if color and color != [255, 255, 255]:
            light_mask = self._color_mask(frame, color)
            if dark_bgr is not None:
                dark_resized = dark_bgr if dark_bgr.shape[:2] == frame.shape[:2] else \
                    cv2.resize(dark_bgr, (frame.shape[1], frame.shape[0]))
                dark_mask = self._color_mask(dark_resized, color)
                mask = cv2.absdiff(light_mask, dark_mask)
            else:
                mask = light_mask
        else:
            light_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if dark_bgr is not None:
                dark_resized = dark_bgr if dark_bgr.shape[:2] == frame.shape[:2] else \
                    cv2.resize(dark_bgr, (frame.shape[1], frame.shape[0]))
                dark_gray = cv2.cvtColor(dark_resized, cv2.COLOR_BGR2GRAY)
                mask = cv2.absdiff(light_gray, dark_gray)
            else:
                mask = light_gray

        mask = cv2.GaussianBlur(mask, (15, 15), 0)
        _, peak_val, _, _ = cv2.minMaxLoc(mask)
        if peak_val < threshold:
            return {"found": False}

        thresh_val = max(threshold, int(peak_val * 0.4))
        _, binary = cv2.threshold(mask, thresh_val, 255, cv2.THRESH_BINARY)

        # Step 2: Find contour candidates
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"found": False}

        # Step 3: Validate each candidate against the ORIGINAL frame
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = cv2.contourArea(contour)
            if area < 200:
                break  # sorted descending, rest are smaller

            M = cv2.moments(contour)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            # Check 3a: Brightness — beam spot must be BRIGHT in original frame
            # Sample a small region around the centroid
            y1 = max(0, cy - 15)
            y2 = min(frame.shape[0], cy + 15)
            x1 = max(0, cx - 15)
            x2 = min(frame.shape[1], cx + 15)
            roi_v = hsv[y1:y2, x1:x2, 2]  # Value channel
            mean_brightness = float(np.mean(roi_v)) if roi_v.size > 0 else 0

            if mean_brightness < 160:
                continue  # too dim — ambient shift, not a beam

            # Check 3b: Saturation — beam is vivid colored, not grey/white
            # A colored beam (blue, red, green) is both bright AND saturated
            # A white wall is bright but NOT saturated
            if color and color != [255, 255, 255]:
                roi_s = hsv[y1:y2, x1:x2, 1]  # Saturation channel
                mean_sat = float(np.mean(roi_s)) if roi_s.size > 0 else 0
                if mean_sat < 80:
                    continue  # not saturated enough — bright white surface, not colored beam

            # Check 3c: Compactness — beam spot is roughly round, not a thin edge
            rect = cv2.minAreaRect(contour)
            w_r, h_r = rect[1]
            if w_r > 0 and h_r > 0:
                aspect = max(w_r, h_r) / min(w_r, h_r)
                if aspect > 5:
                    continue  # too elongated

            # All checks passed — this is a real beam
            return {
                "found": True,
                "pixelX": cx,
                "pixelY": cy,
                "peakIntensity": int(peak_val),
                "area": int(area),
                "brightness": int(mean_brightness),
            }

        return {"found": False}

    def detect_center(self, frame, cam_idx=0, color=None, threshold=30, beam_count=3):
        """Detect the center beam of a multi-beam fixture.

        Uses connectedComponents to identify individual beam spots,
        then returns the one with the median X position (center beam).
        """
        if frame is None:
            return {"found": False}

        dark_bgr = self._dark_frames.get(cam_idx)
        if color and color != [255, 255, 255]:
            light_mask = self._color_mask(frame, color)
            if dark_bgr is not None:
                dark_resized = dark_bgr if dark_bgr.shape[:2] == frame.shape[:2] else \
                    cv2.resize(dark_bgr, (frame.shape[1], frame.shape[0]))
                dark_mask = self._color_mask(dark_resized, color)
                mask = cv2.absdiff(light_mask, dark_mask)
            else:
                mask = light_mask
        else:
            light_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if dark_bgr is not None:
                dark_resized = dark_bgr if dark_bgr.shape[:2] == frame.shape[:2] else \
                    cv2.resize(dark_bgr, (frame.shape[1], frame.shape[0]))
                mask = cv2.absdiff(light_gray, cv2.cvtColor(dark_resized, cv2.COLOR_BGR2GRAY))
            else:
                mask = light_gray

        mask = cv2.GaussianBlur(mask, (11, 11), 0)
        _, peak_val, _, _ = cv2.minMaxLoc(mask)
        if peak_val < threshold or peak_val < 60:
            return {"found": False}

        thresh_val = max(threshold, int(peak_val * 0.4))
        _, binary = cv2.threshold(mask, thresh_val, 255, cv2.THRESH_BINARY)

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8)

        # Validate components: must be bright + saturated in original frame
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        components = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < 100:
                continue
            cx, cy = int(centroids[i][0]), int(centroids[i][1])
            # Check brightness at component center
            y1 = max(0, cy - 10)
            y2 = min(frame.shape[0], cy + 10)
            x1 = max(0, cx - 10)
            x2 = min(frame.shape[1], cx + 10)
            roi_v = hsv[y1:y2, x1:x2, 2]
            if roi_v.size == 0 or float(np.mean(roi_v)) < 150:
                continue  # too dim — not a real beam spot
            components.append({"idx": i, "cx": float(centroids[i][0]),
                               "cy": float(centroids[i][1]), "area": area})

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
