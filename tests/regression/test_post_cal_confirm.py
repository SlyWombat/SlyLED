#!/usr/bin/env python3
"""#682-FF — post-cal confirmation regression.

Imports ``tools/post_cal_confirm.py`` and exercises the projection +
verdict logic in two passes:

1. **Offline (always runs):** monkey-patches the camera-pose + projection
   helpers so we can prove the verdict mapping is correct without a live
   rig. Confirms CONFIRMED / OFF_TARGET / NO_DETECTION / BEHIND_CAMERA /
   NO_PROJECTION verdicts come out of ``verdict_for_camera`` for the
   right inputs.

2. **Live rig (gated on ``SLYLED_LIVE_RIG=1``):** invokes the tool against
   a calibrated mover + ArUco markers and asserts every camera reports
   either CONFIRMED or BEHIND_CAMERA — i.e. nothing OFF_TARGET.

CI runs only the offline pass.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import post_cal_confirm as pcc  # noqa: E402


# ── offline cases ──────────────────────────────────────────────────────

def offline_cases():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # 1. CONFIRMED — detected within tolerance.
    v = pcc.verdict_for_camera(
        detected={"found": True, "pixelX": 320, "pixelY": 240},
        projected={"px": 322.0, "py": 238.0, "behindCamera": False},
        beam_width_px=10.0,
        fov_tolerance_factor=5.0,
    )
    check("CONFIRMED", v["verdict"], "CONFIRMED")
    if v["distancePx"] is None or v["distancePx"] > 50:
        failures.append(f"CONFIRMED distance unreasonable: {v['distancePx']}")
    if v["tolerancePx"] != 50.0:
        failures.append(f"CONFIRMED tolerance wrong: {v['tolerancePx']}")

    # 2. OFF_TARGET — outside tolerance.
    v = pcc.verdict_for_camera(
        detected={"found": True, "pixelX": 100, "pixelY": 100},
        projected={"px": 400.0, "py": 400.0, "behindCamera": False},
        beam_width_px=10.0,
        fov_tolerance_factor=5.0,
    )
    check("OFF_TARGET", v["verdict"], "OFF_TARGET")
    if v["distancePx"] is None or v["distancePx"] < 50:
        failures.append(f"OFF_TARGET distance not > tol: {v['distancePx']}")

    # 3. NO_DETECTION — detected.found is False.
    v = pcc.verdict_for_camera(
        detected={"found": False},
        projected={"px": 320.0, "py": 240.0, "behindCamera": False},
        beam_width_px=10.0,
    )
    check("NO_DETECTION", v["verdict"], "NO_DETECTION")

    # 4. BEHIND_CAMERA — projection flagged behind.
    v = pcc.verdict_for_camera(
        detected={"found": True, "pixelX": 0, "pixelY": 0},
        projected={"px": None, "py": None, "behindCamera": True},
        beam_width_px=10.0,
    )
    check("BEHIND_CAMERA", v["verdict"], "BEHIND_CAMERA")

    # 5. NO_PROJECTION — couldn't reach orchestrator for camera fixture.
    v = pcc.verdict_for_camera(
        detected={"found": True, "pixelX": 0, "pixelY": 0},
        projected=None,
        beam_width_px=10.0,
    )
    check("NO_PROJECTION", v["verdict"], "NO_PROJECTION")

    # 6. project_to_camera_pixel returns None for unknown camera.
    pcc._CAM_POSE_CACHE.clear()
    real_http = pcc.http
    pcc.http = lambda *a, **kw: {"_error": "stubbed"}
    try:
        proj = pcc.project_to_camera_pixel("http://nope", 999, 0, 0, 0)
        if proj is not None:
            failures.append(f"unknown camera should yield None, got {proj!r}")
    finally:
        pcc.http = real_http
        pcc._CAM_POSE_CACHE.clear()

    return failures


# ── live-rig case ──────────────────────────────────────────────────────

def live_available():
    if not os.environ.get("SLYLED_LIVE_RIG"):
        return False
    orch = os.environ.get("SLYLED_ORCH", "http://localhost:8080")
    try:
        urllib.request.urlopen(f"{orch}/api/dmx/status", timeout=3).read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def live_pass():
    """Run the tool itself — fail on any OFF_TARGET verdict."""
    orch = os.environ.get("SLYLED_ORCH", "http://localhost:8080")
    fid = os.environ.get("SLYLED_CANARY_FID", "17")
    cameras = os.environ.get("SLYLED_CANARY_CAMERAS", "12,13")
    # #684 — include aruco:3 (Pillar Post, z=1368 mm on the basement
    # rig) so the live regression exercises the surface-aware path. A
    # cal that was contaminated by the old z=0 floor-plane assumption
    # will project the pillar marker to a wildly wrong pixel and the
    # tool fails-on-off-target.
    targets = os.environ.get("SLYLED_POSTCAL_TARGETS",
                              "aruco:0,aruco:1,aruco:2,aruco:3,aruco:4,aruco:5")
    cmd = [
        sys.executable, str(ROOT / "tools" / "post_cal_confirm.py"),
        "--orch", orch, "--fid", str(fid),
        "--cameras", cameras, "--targets", targets,
        "--fail-on-off-target",
    ]
    print(f"== live: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def main():
    print("=== post-cal confirm regression ===")
    print("-- offline cases --")
    failures = offline_cases()
    for f in failures:
        print(f"  [FAIL] {f}")
    if failures:
        print(f"  {len(failures)} offline failure(s)")
        return 1
    print("  all offline cases passed")

    if live_available():
        print("-- live-rig pass --")
        rc = live_pass()
        if rc != 0:
            print(f"  live pass failed (rc={rc})")
            return 1
        print("  live pass: OK")
    else:
        print("-- live-rig pass: SKIP (SLYLED_LIVE_RIG not set or orch unreachable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
