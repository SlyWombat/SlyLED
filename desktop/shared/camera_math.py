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

    #600 convention (Z-up axis-matched): ``[rx, ry, rz]`` where
    rx = pitch (rotation about X),
    ry = roll  (rotation about Y — the stage-forward axis),
    rz = yaw   (rotation about Z — the stage-up axis, i.e. pan).

    This helper is the single source of truth for the array index →
    axis-semantic mapping. Callers only ever get back axis-semantic
    ``(tilt, pan, roll)``; the index layout is an implementation detail
    that moved during #600 and will stay pinned to this helper going
    forward. Missing entries default to 0.
    """
    if not rotation:
        return 0.0, 0.0, 0.0
    tilt = float(rotation[0]) if len(rotation) > 0 else 0.0
    # #600 — swapped indices. Old: ry=pan, rz=roll. New: ry=roll, rz=yaw.
    roll = float(rotation[1]) if len(rotation) > 1 else 0.0
    pan = float(rotation[2]) if len(rotation) > 2 else 0.0
    return tilt, pan, roll


def rotation_to_layout(tilt, pan, roll=0.0):
    """Inverse of rotation_from_layout. Assemble a layout-array from the
    axis-semantic triple. #600 convention — ``[rx, ry, rz] = [tilt, roll, pan]``.
    """
    return [float(tilt), float(roll), float(pan)]


# ── Stage → pixel projection (#682-DD) ───────────────────────────────────

def project_stage_to_pixel(stage_point, cam_pos, cam_rotation,
                            fov_deg, cam_resolution):
    """Project a stage-mm point to a camera pixel.

    Inverse of the cam→stage transform. Returns ``(px, py)`` in pixel
    coordinates with origin at image top-left, or ``(None, None)`` when
    the point is behind the camera (negative z in camera frame).

    Assumes a centred pinhole with square pixels. Focal length derived
    from the camera's declared horizontal FOV:

        fx = fy = W / (2 × tan(fov/2))

    Used by ``expected_pixel_shift_per_deg`` to build the DD plausibility
    gate, and by ``tools/post_cal_confirm.py`` for the #682-FF
    "projection vs detection" verdict.
    """
    tilt_deg, pan_deg, roll_deg = rotation_from_layout(cam_rotation)
    R_c2s = build_camera_to_stage(tilt_deg, pan_deg, roll_deg)
    # Invert: R_cam_to_stage is orthonormal, so R_stage_to_cam = R.T
    sx, sy, sz = float(stage_point[0]), float(stage_point[1]), float(stage_point[2])
    dx, dy, dz = sx - cam_pos[0], sy - cam_pos[1], sz - cam_pos[2]
    if _HAS_NUMPY and hasattr(R_c2s, "T"):
        R_s2c = R_c2s.T
        cam_x = R_s2c[0, 0] * dx + R_s2c[0, 1] * dy + R_s2c[0, 2] * dz
        cam_y = R_s2c[1, 0] * dx + R_s2c[1, 1] * dy + R_s2c[1, 2] * dz
        cam_z = R_s2c[2, 0] * dx + R_s2c[2, 1] * dy + R_s2c[2, 2] * dz
    else:
        # Transpose a nested-list matrix.
        cam_x = R_c2s[0][0] * dx + R_c2s[1][0] * dy + R_c2s[2][0] * dz
        cam_y = R_c2s[0][1] * dx + R_c2s[1][1] * dy + R_c2s[2][1] * dz
        cam_z = R_c2s[0][2] * dx + R_c2s[1][2] * dy + R_c2s[2][2] * dz
    if cam_z <= 1e-6:
        return (None, None)  # behind camera
    w, h = cam_resolution
    fx = float(w) / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    fy = fx  # square pixels
    cx = float(w) / 2.0
    cy = float(h) / 2.0
    px = fx * cam_x / cam_z + cx
    py = fy * cam_y / cam_z + cy
    return (px, py)


def expected_pixel_shift_per_deg(mover_pos, floor_hit, cam_pos, cam_rotation,
                                   fov_deg, cam_resolution):
    """#682-DD — px/° sensitivity of the camera view to mover pan / tilt
    nudges at a given beam floor-hit point.

    The plausibility gate in ``battleship_discover._confirm`` uses this
    to reject "shift too large (blob identity swap)" and
    "shift disproportionate to expected" false positives.

    Args:
        mover_pos:       (x, y, z) fixture position in stage mm.
        floor_hit:       (x, y, z) stage point where the beam currently
                         lands. Typically on floor (z≈0) but the helper
                         works for any hit plane.
        cam_pos:         (x, y, z) camera fixture position.
        cam_rotation:    layout-rotation triple for the camera.
        fov_deg:         camera horizontal FOV in degrees.
        cam_resolution:  (W, H) in pixels.

    Returns ``(px_per_deg_pan, px_per_deg_tilt)``. Either entry can be
    0.0 when the geometry is degenerate (beam behind camera, floor-hit
    directly below the mover) — caller should treat 0.0 as "can't judge,
    fall back to the legacy ≥ 8 px threshold".
    """
    mx, my, mz = float(mover_pos[0]), float(mover_pos[1]), float(mover_pos[2])
    fx, fy, fz = float(floor_hit[0]), float(floor_hit[1]), float(floor_hit[2])
    v_xy_mag = math.hypot(fx - mx, fy - my)
    # Beam length fixture → floor-hit. When fixture is (nearly) over the
    # hit point the pan arc tangent collapses; skip the gate.
    beam_len = math.sqrt((fx - mx) ** 2 + (fy - my) ** 2 + (fz - mz) ** 2)
    if v_xy_mag < 1e-3 or beam_len < 1e-3:
        return (0.0, 0.0)
    # Arc tangent for a 1° pan rotation around the stage-Z axis:
    #   tangent direction = perp-to-radial in XY plane
    #   arc length = r_xy × (π/180)
    pan_arc_mm = v_xy_mag * math.pi / 180.0
    pan_tx = -(fy - my) / v_xy_mag * pan_arc_mm
    pan_ty = (fx - mx) / v_xy_mag * pan_arc_mm
    pan_hit = (fx + pan_tx, fy + pan_ty, fz)
    # Arc tangent for 1° tilt (rotation around a horizontal axis through
    # the mover): for small angles the floor-hit moves radially along
    # the beam direction projected on the floor by ~beam_len × (π/180).
    tilt_arc_mm = beam_len * math.pi / 180.0
    tilt_tx = (fx - mx) / v_xy_mag * tilt_arc_mm
    tilt_ty = (fy - my) / v_xy_mag * tilt_arc_mm
    tilt_hit = (fx + tilt_tx, fy + tilt_ty, fz)

    p0 = project_stage_to_pixel(floor_hit, cam_pos, cam_rotation,
                                  fov_deg, cam_resolution)
    pp = project_stage_to_pixel(pan_hit, cam_pos, cam_rotation,
                                  fov_deg, cam_resolution)
    pt = project_stage_to_pixel(tilt_hit, cam_pos, cam_rotation,
                                  fov_deg, cam_resolution)
    if any(v is None for v in (p0[0], p0[1], pp[0], pp[1], pt[0], pt[1])):
        return (0.0, 0.0)
    pan_shift = math.hypot(pp[0] - p0[0], pp[1] - p0[1])
    tilt_shift = math.hypot(pt[0] - p0[0], pt[1] - p0[1])
    return (pan_shift, tilt_shift)


# ── Camera floor-view polygon (#659) ─────────────────────────────────────

def camera_floor_polygon(cam_pos, rotation, fov_deg, aspect=16.0 / 9.0,
                          stage_bounds=None, floor_z=0.0):
    """Project the camera's viewing frustum onto the floor plane.

    Returns a convex polygon in stage XY (z = floor_z) describing every
    floor point the camera can see. Uses the four corner rays of the
    image frustum, intersects each with the floor plane, and clips the
    result to the stage bounding box when supplied.

    Skips frustum corners that point AWAY from the floor (no intersection
    on the forward half-space) — a camera aimed straight up produces an
    empty polygon, not a degenerate one.

    Args:
        cam_pos:       (x, y, z) in stage mm.
        rotation:      layout rotation array `[rx, ry, rz]`. Read via
                        rotation_from_layout — axis-semantic tilt/pan/roll.
        fov_deg:       horizontal field-of-view in degrees.
        aspect:        width/height ratio (default 16:9).
        stage_bounds:  optional dict ``{w, d, h}`` in mm to clip the
                        polygon to stage boundaries.
        floor_z:       floor plane z in mm (default 0).

    Returns a list of (x, y) tuples in CCW order, or an empty list when
    the camera sees no floor.
    """
    if not _HAS_NUMPY:
        return []

    tilt, pan, roll = rotation_from_layout(rotation)
    R = build_camera_to_stage(tilt, pan, roll)

    hfov = math.radians(fov_deg)
    # Vertical FOV from horizontal + aspect.
    vfov = 2.0 * math.atan(math.tan(hfov / 2.0) / max(1e-6, aspect))

    # Image-plane corner rays in pinhole frame (+Z forward).
    tx = math.tan(hfov / 2.0)
    ty = math.tan(vfov / 2.0)
    # Order: top-left, top-right, bottom-right, bottom-left (CCW from
    # the floor's perspective after projection — top rays land far, bottom
    # rays land near).
    corners_cam = [
        np.array([-tx, -ty, 1.0]),  # TL
        np.array([+tx, -ty, 1.0]),  # TR
        np.array([+tx, +ty, 1.0]),  # BR
        np.array([-tx, +ty, 1.0]),  # BL
    ]

    cx, cy, cz = float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])
    hits = []
    for c in corners_cam:
        ray_stage = R @ c
        rz = ray_stage[2]
        if rz >= -1e-6:
            # Ray points up or parallel to floor — no forward intersection.
            continue
        # Solve cz + t * rz = floor_z for t > 0.
        t = (floor_z - cz) / rz
        if t <= 0:
            continue
        fx = cx + t * ray_stage[0]
        fy = cy + t * ray_stage[1]
        hits.append((float(fx), float(fy)))

    if not hits:
        return []

    # Clip to stage bounds (rectangle 0..w × 0..d).
    if stage_bounds:
        w = float(stage_bounds.get("w", 0) or 0)
        d = float(stage_bounds.get("d", 0) or 0)
        if w > 0 and d > 0:
            hits = [(max(0.0, min(w, x)), max(0.0, min(d, y)))
                    for x, y in hits]

    return hits


def point_in_polygon(pt, polygon):
    """Ray-cast point-in-polygon test. Polygon is a list of (x, y).
    Returns True when pt lies inside (or on the boundary within 1 mm).
    """
    if len(polygon) < 3:
        return False
    x, y = float(pt[0]), float(pt[1])
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside
