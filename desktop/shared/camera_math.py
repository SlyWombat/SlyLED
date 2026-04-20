"""
camera_math.py — Canonical camera-to-stage rotation helpers.

All modules that convert between a pinhole camera frame and the stage
frame must use this helper. Historically space_mapper.transform_points
and stereo_engine.add_camera_from_fov each built their own 3×3 matrix
with subtly different conventions (different axis order, different pan
sign, missing frame-swap when rotation ≠ 0). That disagreement made
cross-module workflows — like the Phase-2 cross-camera consistency
check — misplace points by tens of cm silently. This module is the
single source of truth; #586.

## Conventions (match the fixture-editor UI and bake_engine._rotation_to_aim)

Stage frame:
    +X = stage-left (stage width)
    +Y = audience (stage depth from back wall)
    +Z = ceiling (height above floor)

Pinhole camera frame (as returned by the depth estimator):
    +X = camera-right
    +Y = camera-down
    +Z = camera-forward (depth axis)

Rotation input `rotation = [tilt_deg, pan_deg, roll_deg]`:
    tilt > 0  → camera aims DOWN (forward +Y tips toward -Z)
    pan  > 0  → camera aims toward +X (stage-left, same as _rotation_to_aim)
    roll > 0  → clockwise when looking along the optical axis from behind

Apply order: roll (in body frame) → tilt → pan. Equivalent world-order:
    R = Rz(pan) @ Rx(-tilt) @ Ry(roll) @ F
where F is the frame-swap from pinhole to stage-aligned.
"""

from __future__ import annotations

import math

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover — numpy is a hard dep elsewhere
    _HAS_NUMPY = False


# Frame-swap from pinhole (X-right, Y-down, Z-forward) to stage-aligned
# (X-width, Y-depth, Z-height). Applied before any pan/tilt/roll so the
# camera at the identity rotation naturally faces stage +Y.
_FRAME_SWAP = [[1, 0, 0],
               [0, 0, 1],
               [0, -1, 0]]


def build_camera_to_stage(tilt_deg: float, pan_deg: float, roll_deg: float = 0.0):
    """3×3 rotation that takes a pinhole cam-local direction → stage frame.

    Includes the frame swap, so a cam-local +Z (forward) vector returns
    stage +Y for tilt=pan=roll=0. See module docstring for the sign
    conventions on each input.

    Returns a numpy ndarray if numpy is available, else a nested list.
    """
    tilt = math.radians(tilt_deg)
    pan = math.radians(pan_deg)
    roll = math.radians(roll_deg)

    ct, st = math.cos(tilt), math.sin(tilt)
    cp, sp = math.cos(pan), math.sin(pan)
    cr, sr = math.cos(roll), math.sin(roll)

    # RX(-tilt) — positive tilt aims DOWN (forward +Y → -Z)
    Rx = [[1,  0,  0],
          [0, ct, st],
          [0, -st, ct]]
    # RZ(-pan) w.r.t. standard right-hand, written directly so positive
    # pan aims toward stage-left (+X), matching _rotation_to_aim.
    Rz = [[cp, sp, 0],
          [-sp, cp, 0],
          [0,  0,  1]]
    # RY(+roll) — rotation around stage-aligned forward (Y).
    Ry = [[cr, 0, sr],
          [0,  1, 0],
          [-sr, 0, cr]]

    if _HAS_NUMPY:
        Rz_n = np.array(Rz, dtype=np.float64)
        Rx_n = np.array(Rx, dtype=np.float64)
        Ry_n = np.array(Ry, dtype=np.float64)
        F_n = np.array(_FRAME_SWAP, dtype=np.float64)
        return Rz_n @ Rx_n @ Ry_n @ F_n

    # Pure-python fallback (nested lists). Used by tests that can't import numpy.
    def _mm(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return _mm(Rz, _mm(Rx, _mm(Ry, _FRAME_SWAP)))


def transform_cam_to_stage(cam_point, cam_pos, tilt_deg, pan_deg, roll_deg=0.0):
    """Transform a single pinhole cam-local point [x, y, z] to stage mm.

    Equivalent to `build_camera_to_stage(...) @ cam_point + cam_pos` but
    doesn't require numpy on the caller side.
    """
    R = build_camera_to_stage(tilt_deg, pan_deg, roll_deg)
    x, y, z = float(cam_point[0]), float(cam_point[1]), float(cam_point[2])
    if _HAS_NUMPY and hasattr(R, "dot"):
        out = R.dot([x, y, z])
        return (out[0] + cam_pos[0], out[1] + cam_pos[1], out[2] + cam_pos[2])
    # Nested-list fallback
    wx = R[0][0] * x + R[0][1] * y + R[0][2] * z + cam_pos[0]
    wy = R[1][0] * x + R[1][1] * y + R[1][2] * z + cam_pos[1]
    wz = R[2][0] * x + R[2][1] * y + R[2][2] * z + cam_pos[2]
    return (wx, wy, wz)


# ── Point-cloud schema (#587) ────────────────────────────────────────────
#
# Points travel as lists of scalars in the shape:
#   v1 (legacy):  [x, y, z, r, g, b]
#   v2 (current): [x, y, z, r, g, b, confidence]
# Every consumer reads slots 0-5 by index and uses `point_confidence()`
# to get the optional 7th slot. This lets v1 and v2 payloads coexist
# through the fusion migration in #584.

POINT_SCHEMA_VERSION = 2


def point_confidence(p):
    """Return the per-point confidence (0.0-1.0).

    Falls back to 1.0 for v1 six-element points so legacy data is
    treated as fully trusted. Clamped to [0, 1] for sanity.
    """
    if len(p) > 6 and p[6] is not None:
        return max(0.0, min(1.0, float(p[6])))
    return 1.0


def point_coords(p):
    """Return the (x, y, z) tuple of a point regardless of length."""
    return (float(p[0]), float(p[1]), float(p[2]))


def rotation_from_layout(rotation):
    """Normalise a layout-stored `rotation` list to (tilt, pan, roll) degrees.

    Layout format: `[rx, ry, rz]` where rx=tilt, ry=pan, rz=roll. Any
    missing entries default to 0. See CLAUDE.md "Camera nodes" section.
    """
    if not rotation:
        return 0.0, 0.0, 0.0
    tilt = float(rotation[0]) if len(rotation) > 0 else 0.0
    pan = float(rotation[1]) if len(rotation) > 1 else 0.0
    roll = float(rotation[2]) if len(rotation) > 2 else 0.0
    return tilt, pan, roll
