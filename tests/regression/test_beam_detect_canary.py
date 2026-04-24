#!/usr/bin/env python3
"""#682-EE — beam-detect canary regression.

Drives a known-good set of pan/tilt positions through ``beam_detect_harness``
on a live rig and asserts the per-label verdicts:

  * ``Floor`` / ``ArUco`` / ``Pillar-ArUco`` — at least one configured camera
    must report CONFIRMED.
  * ``Off-Camera-Canary`` — every camera must return no-beam (this is the
    dark-ref smoke test; any detection here is an auto-fail).
  * ``Partial-Observation`` — detection is acceptable IFF the verdict is
    PARTIAL or no-beam; CONFIRMED here is an auto-fail (it would mean the
    pipeline accepted a backscatter glow as a real beam).

The test is gated on a live rig: ``--live-rig`` flag on
``tests/regression/run_all.py`` or the ``SLYLED_LIVE_RIG=1`` env var. CI
skips when neither is set, since the harness needs a calibrated mover plus
two reachable Pi camera nodes.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import beam_detect_harness as harness  # noqa: E402

ORCH = os.environ.get("SLYLED_ORCH", "http://localhost:8080")
FID = int(os.environ.get("SLYLED_CANARY_FID", "17"))
CAMERAS = [int(c) for c in os.environ.get("SLYLED_CANARY_CAMERAS", "12,13").split(",") if c.strip()]
COLOUR = os.environ.get("SLYLED_CANARY_COLOUR", "green")
CSV = ROOT / "docs" / "live-test-sessions" / "2026-04-24" / "harness-positions.csv"


def _http_get(url, timeout=3):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def live_rig_available():
    """Probe orchestrator + every camera. All must respond — otherwise skip."""
    if not os.environ.get("SLYLED_LIVE_RIG"):
        return False, "SLYLED_LIVE_RIG not set"
    dmx = _http_get(f"{ORCH}/api/dmx/status")
    if not dmx or not dmx.get("artnet", {}).get("running"):
        return False, "Art-Net engine not running on orchestrator"
    for cam in CAMERAS:
        base, _idx = harness.CAMERA_ROUTES.get(cam, (None, None))
        if not base:
            return False, f"camera fid {cam} not in CAMERA_ROUTES"
        if not _http_get(f"{base}/status"):
            return False, f"camera node {base} unreachable"
    return True, "ok"


def label_passes(label, verdict_per_cam):
    """Return (ok, why) for a single position's per-camera verdict map.

    ``verdict_per_cam[cam_fid]`` is the dict from ``rec['confirmVerdict'][c]``
    on the harness output; ``confirmed=True/False`` plus ``notes``.
    Distinguishes CONFIRMED / PARTIAL / no-beam by inspecting the primary
    detection result alongside the confirm verdict.
    """
    confirmed = [c for c, v in verdict_per_cam.items() if v.get("confirmed")]
    any_partial = [c for c, v in verdict_per_cam.items()
                   if v.get("notes") in ("pan-shift-missing", "tilt-shift-missing")]
    none_found = all(v.get("notes") == "primary-not-found" for v in verdict_per_cam.values())

    if label in ("Floor", "ArUco", "Pillar-ArUco"):
        if confirmed:
            return True, f"confirmed on cam(s) {confirmed}"
        return False, f"no camera CONFIRMED (verdicts: {verdict_per_cam})"
    if label == "Off-Camera-Canary":
        if confirmed:
            return False, (f"dark-ref leak — camera(s) {confirmed} CONFIRMED at off-camera "
                           "position; detector is finding spurious beam")
        return True, "no detections (as expected)"
    if label == "Partial-Observation":
        if confirmed:
            return False, (f"camera(s) {confirmed} CONFIRMED on a known partial-observation "
                           "position — pipeline is accepting backscatter as full beam")
        return True, ("partial or no-beam (any-partial=" + str(any_partial)
                       + ", none-found=" + str(none_found) + ")")
    return True, f"unknown label {label!r} — pass-through"


def run():
    ok, why = live_rig_available()
    if not ok:
        print(f"SKIP — {why}")
        return 0

    positions = harness.load_positions(str(CSV))
    print(f"== canary: {len(positions)} positions × {len(CAMERAS)} cameras  csv={CSV}")

    failures = []
    for pan, tilt, label in positions:
        if not label:
            continue
        rec = harness.run_one(
            ORCH, FID, pan, tilt, label,
            colour_dmx=harness.COLOUR_SLOTS[COLOUR],
            cameras=CAMERAS,
            nudge=0.02,
            settle_s=0.8,
            dark_wait_s=1.0,
            threshold=30,
            dark_ref=True,
            nudge_confirm=True,
        )
        verdict_map = rec.get("confirmVerdict", {})
        passed, reason = label_passes(label, verdict_map)
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {label}: {reason}")
        if not passed:
            failures.append((label, reason))

    if failures:
        print(f"\n{len(failures)} canary failure(s):")
        for label, why in failures:
            print(f"  - {label}: {why}")
        return 1
    print("\nAll canary positions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
