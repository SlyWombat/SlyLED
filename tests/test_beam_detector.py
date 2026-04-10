#!/usr/bin/env python3
"""
test_beam_detector.py — Unit tests for beam detection using synthetic OpenCV frames.

All tests use synthetic frames (no cameras, no network).

Usage:
    python -X utf8 tests/test_beam_detector.py
"""

import os
import sys

# Add firmware dir to path so we can import beam_detector
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'firmware', 'orangepi'))

passed = 0
failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


def make_frame(w=640, h=480, color=(0, 0, 0)):
    """Create a solid BGR frame."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def draw_spot(frame, cx, cy, radius=30, color=(255, 255, 255)):
    """Draw a bright filled circle on a frame (returns a copy)."""
    out = frame.copy()
    cv2.circle(out, (cx, cy), radius, color, -1)
    return out


def draw_ellipse(frame, cx, cy, ax_major, ax_minor, angle=0, color=(255, 255, 255)):
    """Draw a filled ellipse on a frame (returns a copy)."""
    out = frame.copy()
    cv2.ellipse(out, (cx, cy), (ax_major, ax_minor), angle, 0, 360, color, -1)
    return out


def run():
    global passed, failed

    try:
        global cv2, np
        import cv2
        import numpy as np
    except ImportError:
        print("SKIP: opencv-python-headless and numpy required")
        print("  pip install opencv-python-headless numpy")
        sys.exit(0)

    from beam_detector import BeamDetector

    print("Beam Detector Unit Tests\n")

    # ── Constructor ────────────────────────────────────────────────
    print("--- BeamDetector init ---")
    det = BeamDetector()
    check("BeamDetector created", det is not None)

    # ── Dark frame management ──────────────────────────────────────
    print("\n--- Dark frame management ---")
    check("has_dark_frame(0) initially False", not det.has_dark_frame(0))
    check("has_dark_frame(1) initially False", not det.has_dark_frame(1))

    dark = make_frame(640, 480, (10, 10, 10))
    det.set_dark_frame(0, dark)
    check("has_dark_frame(0) after set", det.has_dark_frame(0))
    check("has_dark_frame(1) still False", not det.has_dark_frame(1))

    det.set_dark_frame(1, dark)
    check("has_dark_frame(1) after set", det.has_dark_frame(1))

    # set_dark_frame with None should not crash or add
    det.set_dark_frame(2, None)
    check("set_dark_frame(None) does not add entry", not det.has_dark_frame(2))

    # ── detect: bright spot with dark frame ────────────────────────
    print("\n--- detect: bright spot with dark frame ---")
    det2 = BeamDetector()
    dark_frame = make_frame(640, 480, (5, 5, 5))
    det2.set_dark_frame(0, dark_frame)

    bright = draw_spot(dark_frame, 320, 240, radius=35, color=(255, 255, 255))
    result = det2.detect(bright, cam_idx=0, threshold=30)
    check("Bright spot found=True", result.get("found") is True)
    check("pixelX near 320", abs(result.get("pixelX", -1) - 320) < 20)
    check("pixelY near 240", abs(result.get("pixelY", -1) - 240) < 20)
    check("area > 0", result.get("area", 0) > 0)
    check("peakIntensity > 0", result.get("peakIntensity", 0) > 0)
    check("brightness field present", "brightness" in result)

    # ── detect: bright spot WITHOUT dark frame ─────────────────────
    print("\n--- detect: bright spot without dark frame ---")
    det3 = BeamDetector()
    # Black background, very bright spot — should be detectable on its own
    black_bg = make_frame(640, 480, (0, 0, 0))
    spot_frame = draw_spot(black_bg, 400, 200, radius=35, color=(255, 255, 255))
    result = det3.detect(spot_frame, cam_idx=0, threshold=30)
    check("Bright spot on black found without dark frame", result.get("found") is True)
    check("pixelX near 400", abs(result.get("pixelX", -1) - 400) < 20)

    # ── detect: no spot present ────────────────────────────────────
    print("\n--- detect: no spot (dark frame) ---")
    det4 = BeamDetector()
    dark_only = make_frame(640, 480, (15, 15, 15))
    det4.set_dark_frame(0, dark_only)
    result = det4.detect(dark_only, cam_idx=0, threshold=30)
    check("No spot: found=False", result.get("found") is False)

    # ── detect: very small spot (below 200px area) ─────────────────
    print("\n--- detect: small spot below minimum area ---")
    det5 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det5.set_dark_frame(0, dark_ref)
    # radius=5 → area ~78px, well below 200px minimum
    tiny_spot = draw_spot(dark_ref, 320, 240, radius=5, color=(255, 255, 255))
    result = det5.detect(tiny_spot, cam_idx=0, threshold=20)
    check("Tiny spot (r=5): found=False", result.get("found") is False)

    # ── detect: elongated shape (aspect ratio > 5) ─────────────────
    print("\n--- detect: elongated shape rejected ---")
    det6 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det6.set_dark_frame(0, dark_ref)
    # Very elongated: 150px wide, 10px tall → aspect ~15
    elongated = draw_ellipse(dark_ref, 320, 240, 150, 10, angle=0,
                             color=(255, 255, 255))
    result = det6.detect(elongated, cam_idx=0, threshold=20)
    check("Elongated shape: found=False", result.get("found") is False)

    # ── detect: None frame ─────────────────────────────────────────
    print("\n--- detect: None frame ---")
    result = det6.detect(None, cam_idx=0)
    check("None frame: found=False", result.get("found") is False)

    # ── detect: completely white frame ─────────────────────────────
    print("\n--- detect: completely white frame ---")
    det7 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det7.set_dark_frame(0, dark_ref)
    white_frame = make_frame(640, 480, (255, 255, 255))
    result = det7.detect(white_frame, cam_idx=0, threshold=30)
    # Uniform white differs from dark but has no compact spot — contour may
    # fill the entire frame and fail compactness, or it may pass because the
    # whole frame is one big region. Either way the result dict must have "found".
    check("White frame returns valid dict", "found" in result)

    # ── detect: multiple spots picks brightest/largest ─────────────
    print("\n--- detect: multiple spots ---")
    det8 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det8.set_dark_frame(0, dark_ref)
    multi = dark_ref.copy()
    # Large bright spot
    cv2.circle(multi, (400, 240), 40, (255, 255, 255), -1)
    # Smaller dimmer spot
    cv2.circle(multi, (100, 100), 15, (180, 180, 180), -1)
    result = det8.detect(multi, cam_idx=0, threshold=20)
    check("Multiple spots: found=True", result.get("found") is True)
    check("Picks larger spot (near 400)", abs(result.get("pixelX", -1) - 400) < 30)

    # ── detect_center: 3 beams in a row ────────────────────────────
    print("\n--- detect_center: 3 beams ---")
    det9 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det9.set_dark_frame(0, dark_ref)
    three_beams = dark_ref.copy()
    # Left beam at x=150, center at x=320, right at x=490
    cv2.circle(three_beams, (150, 240), 25, (255, 255, 255), -1)
    cv2.circle(three_beams, (320, 240), 25, (255, 255, 255), -1)
    cv2.circle(three_beams, (490, 240), 25, (255, 255, 255), -1)
    result = det9.detect_center(three_beams, cam_idx=0, threshold=20, beam_count=3)
    check("3 beams: found=True", result.get("found") is True)
    check("Center beam pixelX near 320", abs(result.get("pixelX", -1) - 320) < 30)
    check("beamCount >= 3", result.get("beamCount", 0) >= 3)

    # ── detect_center: single beam ─────────────────────────────────
    print("\n--- detect_center: single beam ---")
    det10 = BeamDetector()
    dark_ref = make_frame(640, 480, (0, 0, 0))
    det10.set_dark_frame(0, dark_ref)
    single_beam = draw_spot(dark_ref, 250, 300, radius=30, color=(255, 255, 255))
    result = det10.detect_center(single_beam, cam_idx=0, threshold=20, beam_count=3)
    check("Single beam: found=True", result.get("found") is True)
    check("Single beam pixelX near 250", abs(result.get("pixelX", -1) - 250) < 30)

    # ── detect_center: None frame ──────────────────────────────────
    print("\n--- detect_center: None frame ---")
    result = det10.detect_center(None, cam_idx=0)
    check("detect_center(None): found=False", result.get("found") is False)

    # ── detect_flash: ON vs OFF ────────────────────────────────────
    print("\n--- detect_flash: ON vs OFF ---")
    det11 = BeamDetector()
    off_frame = make_frame(640, 480, (20, 20, 20))
    on_frame = draw_spot(off_frame, 300, 350, radius=35, color=(255, 255, 255))
    result = det11.detect_flash(on_frame, off_frame, threshold=30)
    check("Flash detect: found=True", result.get("found") is True)
    check("Flash pixelX near 300", abs(result.get("pixelX", -1) - 300) < 25)
    check("Flash pixelY near 350", abs(result.get("pixelY", -1) - 350) < 25)
    check("Flash brightness > 0", result.get("brightness", 0) > 0)

    # ── detect_flash: identical frames ─────────────────────────────
    print("\n--- detect_flash: identical frames ---")
    same_frame = make_frame(640, 480, (80, 80, 80))
    result = det11.detect_flash(same_frame, same_frame, threshold=30)
    check("Identical frames: found=False", result.get("found") is False)

    # ── detect_flash: None frames ──────────────────────────────────
    print("\n--- detect_flash: None frames ---")
    result = det11.detect_flash(None, off_frame)
    check("detect_flash(None, off): found=False", result.get("found") is False)
    result = det11.detect_flash(on_frame, None)
    check("detect_flash(on, None): found=False", result.get("found") is False)

    # ── Summary ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"{passed} passed, {failed} failed, {passed + failed} total assertions")
    if failed:
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    run()
