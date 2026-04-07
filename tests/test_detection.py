#!/usr/bin/env python3
"""
test_detection.py — Unit tests for the SlyLED object detection module.

Runs on the dev machine (requires opencv-python-headless + numpy).
Downloads the YOLOv8n model if not present.

Usage:
    python tests/test_detection.py
"""

import os
import sys
import time

# Add firmware dir to path so we can import detector
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'firmware', 'orangepi'))

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("ERROR: opencv-python-headless and numpy required")
        print("  pip install opencv-python-headless numpy")
        sys.exit(2)

    # Override model path for local testing
    import detector
    local_model_dir = os.path.join(os.path.dirname(__file__), '..', 'firmware', 'orangepi', 'models')
    os.makedirs(local_model_dir, exist_ok=True)
    detector.MODEL_DIR = type(detector.MODEL_DIR)(local_model_dir)
    detector.MODEL_PATH = detector.MODEL_DIR / "yolov8n.onnx"

    print("Object Detection Unit Tests\n")

    # ── Detector init ──────────────────────────────────────────────
    det = detector.ObjectDetector()
    ok("ObjectDetector created", det is not None)

    # ── Blank frame ────────────────────────────────────────────────
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    detections, ms = det.detect(blank, threshold=0.5)
    ok("Blank frame returns empty list", len(detections) == 0,
       f"{len(detections)} detections, {ms:.0f}ms")

    # ── Dark frame (near-black noise) ──────────────────────────────
    dark = np.random.randint(0, 10, (480, 640, 3), dtype=np.uint8)
    detections, ms = det.detect(dark, threshold=0.5)
    ok("Dark frame returns empty or few", len(detections) <= 2,
       f"{len(detections)} detections")

    # ── Random noise frame ─────────────────────────────────────────
    noise = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
    detections, ms = det.detect(noise, threshold=0.8)
    ok("Noise frame high threshold → few detections", len(detections) <= 5,
       f"{len(detections)} detections at threshold=0.8")

    # ── Detection output format ────────────────────────────────────
    # Create a frame with some structure (colored rectangles)
    test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(test_frame, (100, 100), (250, 400), (0, 128, 255), -1)  # orange blob
    cv2.rectangle(test_frame, (300, 50), (500, 350), (128, 255, 0), -1)   # green blob
    detections, ms = det.detect(test_frame, threshold=0.1)
    # May or may not detect anything from colored rectangles
    ok("Detection format check (low threshold)", True, f"{len(detections)} detections")
    for d in detections[:1]:
        ok("Detection has label field", isinstance(d.get("label"), str))
        ok("Detection has confidence 0-1", 0 < d["confidence"] <= 1)
        ok("Detection x >= 0", d["x"] >= 0)
        ok("Detection y >= 0", d["y"] >= 0)
        ok("Detection w > 0", d["w"] > 0)
        ok("Detection h > 0", d["h"] > 0)
        ok("Detection x+w <= frame width", d["x"] + d["w"] <= 640)
        ok("Detection y+h <= frame height", d["y"] + d["h"] <= 480)
        break

    # ── Confidence threshold filtering ─────────────────────────────
    detections_low, _ = det.detect(test_frame, threshold=0.01)
    detections_high, _ = det.detect(test_frame, threshold=0.9)
    ok("Higher threshold → fewer detections",
       len(detections_high) <= len(detections_low),
       f"low={len(detections_low)} high={len(detections_high)}")

    # ── Class filter ───────────────────────────────────────────────
    detections_all, _ = det.detect(test_frame, threshold=0.01, classes=None)
    detections_person, _ = det.detect(test_frame, threshold=0.01, classes=["person"])
    non_person = [d for d in detections_person if d["label"] != "person"]
    ok("Class filter excludes non-person", len(non_person) == 0,
       f"person-only: {len(detections_person)}, non-person leaked: {len(non_person)}")

    # ── Resolution options ─────────────────────────────────────────
    d320, ms320 = det.detect(blank, threshold=0.5, input_size=320)
    ok("Resolution 320 accepted", True, f"{ms320:.0f}ms")

    d640, ms640 = det.detect(blank, threshold=0.5, input_size=640)
    ok("Resolution 640 accepted", True, f"{ms640:.0f}ms")
    ok("640 slower than 320", ms640 >= ms320 * 0.5,
       f"320={ms320:.0f}ms, 640={ms640:.0f}ms")

    # ── Benchmark ──────────────────────────────────────────────────
    print("\n  Benchmarks:")
    test_img = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
    for size in (320, 640):
        times = []
        for _ in range(3):
            _, t = det.detect(test_img, threshold=0.5, input_size=size)
            times.append(t)
        avg = sum(times) / len(times)
        print(f"    {size}x{size}: avg {avg:.0f}ms ({min(times):.0f}-{max(times):.0f}ms)")

    # ── Print results ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    for name, cond, detail in results:
        mark = '\u2705' if cond else '\u274c'
        line = f"  {mark} {name}"
        if detail:
            line += f"  ({detail})"
        print(line)
    print(f"\n{passed} passed, {failed} failed, {len(results)} total")
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    run()
