"""
stereo_consistency.py — Cross-camera consistency filter for multi-cam
point clouds (#582).

After each camera's monocular cloud is transformed into stage
coordinates, this module merges them with a per-point confidence:

  - Points confirmed by ≥2 cameras (their reprojections agree within
    `tolerance_mm`) get confidence 0.8–0.95.
  - Points seen by a single camera (outside any other camera's FOV)
    pass through tagged with confidence 0.4 — "singleCam".
  - Points seen by ≥2 cameras but disagreeing on where in 3D space
    they are get dropped as monocular hallucinations.

This depends on:
  - Phase 1 (#581) — depths are in true metric, otherwise the
    cross-check threshold is meaningless.
  - Issue #586 — shared rotation convention via `camera_math.build_camera_to_stage`.
"""

from __future__ import annotations

import math

import numpy as np

from camera_math import build_camera_to_stage, rotation_from_layout


def _build_cam_context(cam_fixture, stage_pos, fov_deg):
    """Pre-compute rotation, position and FOV bounds for a camera.

    Returns a dict used by the filter loop.
    """
    tilt, pan, roll = rotation_from_layout(cam_fixture.get("rotation", [0, 0, 0]))
    R = np.array(build_camera_to_stage(tilt, pan, roll), dtype=np.float64)
    # We need R⁻¹ to bring stage points back into cam-local frame.
    R_inv = R.T
    half_fov = math.radians(fov_deg) / 2.0
    tan_half = math.tan(half_fov)
    return {
        "id": cam_fixture.get("id"),
        "name": cam_fixture.get("name", ""),
        "pos": np.array(stage_pos, dtype=np.float64),
        "R": R,
        "R_inv": R_inv,
        "tan_half": tan_half,
    }


def _in_fov(stage_point, cam_ctx):
    """True if `stage_point` falls within `cam_ctx`'s FOV cone.

    A point is in the cone when its projection onto the camera's local
    frame has positive forward (z > 0) AND |x|/z, |y|/z < tan(FOV/2).
    """
    delta = np.asarray(stage_point, dtype=np.float64) - cam_ctx["pos"]
    local = cam_ctx["R_inv"] @ delta
    if local[2] <= 0:
        return False
    if abs(local[0]) > local[2] * cam_ctx["tan_half"]:
        return False
    if abs(local[1]) > local[2] * cam_ctx["tan_half"]:
        return False
    return True


def _nearest_distance(stage_point, cloud_xyz):
    """Minimum Euclidean distance from `stage_point` to any point in `cloud_xyz`.

    `cloud_xyz` is an (N, 3) numpy array. Returns `math.inf` when empty.
    """
    if cloud_xyz.size == 0:
        return math.inf
    diff = cloud_xyz - np.asarray(stage_point, dtype=np.float64)
    return float(np.sqrt((diff * diff).sum(axis=1).min()))


def cross_camera_filter(per_cam, tolerance_mm=150.0,
                        confidence_confirmed=0.85, confidence_single=0.4):
    """Merge per-camera clouds with cross-camera consistency weighting.

    Args:
        per_cam: list of dicts, each:
            {
              "fixture": {...camera fixture record},
              "stage_pos": (x, y, z) in mm,
              "fov_deg": float,
              "points": [[x,y,z,r,g,b, (confidence)], ...] in stage mm,
            }
        tolerance_mm: maximum distance between cross-cam corresponding
            points for them to be considered the same observation.
        confidence_confirmed: confidence value stored on cross-confirmed points.
        confidence_single: confidence value stored on singleCam points.

    Returns:
        merged_points: single list of 7-slot points sorted by source order.
        stats: dict with per-camera counts of confirmed / single / dropped.
    """
    # Build camera contexts
    ctxs = []
    clouds = []
    for entry in per_cam:
        ctx = _build_cam_context(entry["fixture"], entry["stage_pos"], entry["fov_deg"])
        ctxs.append(ctx)
        pts = entry.get("points", [])
        clouds.append(pts)

    # Precompute (N, 3) arrays for fast nearest-neighbour checks
    cloud_arrays = []
    for pts in clouds:
        if pts:
            arr = np.array([[p[0], p[1], p[2]] for p in pts], dtype=np.float64)
        else:
            arr = np.empty((0, 3), dtype=np.float64)
        cloud_arrays.append(arr)

    merged = []
    stats = []

    for i, pts in enumerate(clouds):
        n_confirmed = 0
        n_single = 0
        n_dropped = 0
        for p in pts:
            xyz = (p[0], p[1], p[2])
            confirmed_by = False
            disagreed_by = False
            any_other_sees = False
            for j, ctx_j in enumerate(ctxs):
                if i == j:
                    continue
                if not _in_fov(xyz, ctx_j):
                    continue
                any_other_sees = True
                d = _nearest_distance(xyz, cloud_arrays[j])
                if d < tolerance_mm:
                    confirmed_by = True
                    break
                else:
                    disagreed_by = True
            if confirmed_by:
                conf = confidence_confirmed
                n_confirmed += 1
            elif any_other_sees and disagreed_by and not confirmed_by:
                # Another camera should have seen this but its nearest
                # observation is too far away — likely a monocular
                # hallucination. Drop.
                n_dropped += 1
                continue
            else:
                # No other camera has this point in its FOV cone — keep
                # with reduced confidence.
                conf = confidence_single
                n_single += 1
            out = [p[0], p[1], p[2],
                   p[3] if len(p) > 3 else 0,
                   p[4] if len(p) > 4 else 0,
                   p[5] if len(p) > 5 else 0,
                   conf]
            merged.append(out)
        stats.append({
            "fixtureId": ctxs[i]["id"],
            "name": ctxs[i]["name"],
            "confirmed": n_confirmed,
            "single": n_single,
            "dropped": n_dropped,
        })

    return merged, stats
