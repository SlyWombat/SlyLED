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

    def detect(self, frame, cam_idx=0, color=None, threshold=10,
                use_dark_reference=True):
        """Detect a bright beam spot in the frame.

        Args:
            frame: BGR numpy array
            cam_idx: camera index (for dark frame lookup)
            color: [r, g, b] beam color to filter for, or None for brightness-only
            threshold: minimum difference from dark frame. Either an int
                       (5-255) or the string ``"auto"`` — when ``"auto"``
                       the helper sets ``thresh_val = max(5, peak * 0.5)``
                       so it adapts to per-probe signal strength. (#700)
            use_dark_reference: when True (default; #700), require a
                       captured dark-reference frame for ``cam_idx`` and
                       subtract it from the working mask. When no
                       dark-ref exists the result includes
                       ``darkRefMissing: True`` so the caller can prompt
                       the operator to run /dark-reference. When False,
                       legacy raw-thresholding path runs (centroid
                       dominated by ambient bright pixels — only useful
                       for sanity-checking the dark-ref capture itself).

        Returns:
            dict with {found, pixelX, pixelY, peakIntensity, area,
                       darkRefApplied, darkRefMissing} or {found: False}
        """
        if frame is None:
            return {"found": False}

        dark_bgr = self._dark_frames.get(cam_idx) if use_dark_reference else None
        # Caller asked for dark-ref but none captured — surface the
        # condition explicitly. The detector still runs (legacy fallback)
        # so the caller's harness keeps working, but with a flag in the
        # response that lets the SPA / QA tool prompt.
        dark_ref_missing = bool(use_dark_reference) and dark_bgr is None

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

        # #700 — adaptive threshold: when caller passes threshold="auto",
        # base the rejection floor on this frame's peak intensity. Adapts
        # to per-probe signal strength — a faint far-aim beam (peak 12
        # over dark ref) and a strong close-aim beam (peak 200) both pass.
        if isinstance(threshold, str) and threshold.lower() == "auto":
            threshold_floor = 5
            threshold = max(threshold_floor, int(peak_val * 0.5))
        if peak_val < threshold:
            result = {"found": False, "peakIntensity": float(peak_val),
                      "darkRefApplied": dark_bgr is not None,
                      "darkRefMissing": dark_ref_missing}
            return result

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
                "darkRefApplied": dark_bgr is not None,
                "darkRefMissing": dark_ref_missing,
            }

        return {"found": False,
                "darkRefApplied": dark_bgr is not None,
                "darkRefMissing": dark_ref_missing}

    def detect_center(self, frame, cam_idx=0, color=None, threshold=10,
                       beam_count=3, use_dark_reference=True):
        """Detect the center beam of a multi-beam fixture.

        Uses connectedComponents to identify individual beam spots,
        then returns the one with the median X position (center beam).
        ``threshold`` accepts ``"auto"`` per #700; ``use_dark_reference``
        is True by default.
        """
        if frame is None:
            return {"found": False}

        dark_bgr = self._dark_frames.get(cam_idx) if use_dark_reference else None
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
        # #700 — adaptive threshold support.
        if isinstance(threshold, str) and threshold.lower() == "auto":
            threshold = max(5, int(peak_val * 0.5))
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

    def detect_flash(self, frame_on, frame_off, color=None, threshold=10,
                      cam_idx=None):
        """Flash detection: find beam by comparing ON frame vs OFF frame.

        When a dark reference has been captured for this camera
        (``cam_idx``), it is subtracted from BOTH the ON and OFF masks
        before diffing — this removes static bright scene elements
        (lit storage bins, pilot LEDs, ArUco markers under ambient) that
        would otherwise leak through if the camera's auto-exposure
        drifted between the two captures. #682-M.

        Without a dark reference the function still works (back-compat)
        but is vulnerable to the scene-baseline shift #682-M documented.

        Args:
            frame_on: BGR frame with beam ON
            frame_off: BGR frame with beam OFF (captured immediately after)
            color: [r, g, b] beam color to filter for
            threshold: minimum difference
            cam_idx: int — camera this frame pair came from, used to look
                     up the stored dark reference.

        Returns:
            dict with {found, pixelX, pixelY, peakIntensity, area, brightness,
                       darkRefApplied}
        """
        if frame_on is None or frame_off is None:
            return {"found": False}

        # Apply color filter to both frames
        if color and color != [255, 255, 255]:
            on_mask = self._color_mask(frame_on, color)
            off_mask = self._color_mask(frame_off, color)
        else:
            on_mask = cv2.cvtColor(frame_on, cv2.COLOR_BGR2GRAY)
            off_mask = cv2.cvtColor(frame_off, cv2.COLOR_BGR2GRAY)

        # #682-M — subtract dark reference from BOTH masks before diffing.
        # This removes any static bright feature in the scene so the
        # ON-OFF diff can't latch onto it even if exposure drifted slightly.
        dark_applied = False
        if cam_idx is not None:
            dark_bgr = self._dark_frames.get(cam_idx)
            if dark_bgr is not None:
                try:
                    if dark_bgr.shape[:2] != frame_on.shape[:2]:
                        dark_resized = cv2.resize(dark_bgr,
                                                   (frame_on.shape[1], frame_on.shape[0]))
                    else:
                        dark_resized = dark_bgr
                    if color and color != [255, 255, 255]:
                        dark_mask = self._color_mask(dark_resized, color)
                    else:
                        dark_mask = cv2.cvtColor(dark_resized, cv2.COLOR_BGR2GRAY)
                    on_mask = cv2.subtract(on_mask, dark_mask)
                    off_mask = cv2.subtract(off_mask, dark_mask)
                    dark_applied = True
                except Exception:
                    # Shape mismatch / cv2 fallthrough — detect without dark ref.
                    dark_applied = False

        # Diff: what's bright in ON but not in OFF
        diff = cv2.subtract(on_mask, off_mask)  # clamps to 0, no negative
        diff = cv2.GaussianBlur(diff, (15, 15), 0)
        _, peak_val, _, _ = cv2.minMaxLoc(diff)

        # #700 — adaptive threshold support.
        if isinstance(threshold, str) and threshold.lower() == "auto":
            threshold = max(5, int(peak_val * 0.5))
        if peak_val < threshold:
            return {"found": False}

        thresh_val = max(threshold, int(peak_val * 0.4))
        _, binary = cv2.threshold(diff, thresh_val, 255, cv2.THRESH_BINARY)

        # Find contours
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"found": False}

        # Validate: bright + saturated in the ON frame
        hsv_on = cv2.cvtColor(frame_on, cv2.COLOR_BGR2HSV)

        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = cv2.contourArea(contour)
            if area < 100:
                break

            M = cv2.moments(contour)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            # Brightness in ON frame
            y1, y2 = max(0, cy - 15), min(frame_on.shape[0], cy + 15)
            x1, x2 = max(0, cx - 15), min(frame_on.shape[1], cx + 15)
            roi_v = hsv_on[y1:y2, x1:x2, 2]
            mean_brightness = float(np.mean(roi_v)) if roi_v.size > 0 else 0
            if mean_brightness < 120:
                continue

            # Compactness
            rect = cv2.minAreaRect(contour)
            w_r, h_r = rect[1]
            if w_r > 0 and h_r > 0 and max(w_r, h_r) / min(w_r, h_r) > 5:
                continue

            return {
                "found": True,
                "pixelX": cx,
                "pixelY": cy,
                "peakIntensity": int(peak_val),
                "area": int(area),
                "brightness": int(mean_brightness),
                "darkRefApplied": bool(dark_applied),
            }

        return {"found": False, "darkRefApplied": bool(dark_applied)}

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
