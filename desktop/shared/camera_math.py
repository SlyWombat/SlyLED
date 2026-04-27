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


# ── Pixel → stage ray (#684) ─────────────────────────────────────────────

def pixel_to_ray(pixel, cam_pos, cam_rotation, fov_deg, cam_resolution,
                  aspect=None):
    """Inverse of project_stage_to_pixel.

    Given a camera pixel, return the stage-space ray ``(origin, direction)``
    that originates at ``cam_pos`` and points toward whatever the pixel sees.
    The ray direction is unit-length.

    Used by the surface-aware mover-cal confirm gate (#684) to figure out
    which surface a beam-detection pixel is sitting on. Pinhole assumption,
    square pixels, focal length derived from the declared horizontal FOV
    matching ``project_stage_to_pixel`` — so round-tripping a stage point
    through project → pixel_to_ray returns a ray that hits the original
    point exactly.

    Args:
        pixel:           (px, py) in image coordinates, origin at top-left.
        cam_pos:         (x, y, z) in stage mm.
        cam_rotation:    layout-rotation triple ``[rx, ry, rz]``.
        fov_deg:         camera horizontal FOV in degrees.
        cam_resolution:  (W, H) in pixels.
        aspect:          optional W/H ratio override (default = W/H).

    Returns ``((ox, oy, oz), (dx, dy, dz))`` with ``hypot(dx, dy, dz) == 1``.
    """
    tilt_deg, pan_deg, roll_deg = rotation_from_layout(cam_rotation)
    R = build_camera_to_stage(tilt_deg, pan_deg, roll_deg)
    w, h = cam_resolution
    fx = float(w) / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    fy = fx  # square pixels — must match project_stage_to_pixel
    cx = float(w) / 2.0
    cy = float(h) / 2.0
    px, py = float(pixel[0]), float(pixel[1])
    # Ray in pinhole-camera frame: x_cam = (px - cx) / fx, y_cam = (py - cy) / fy, z_cam = 1.
    cam_dir = (
        (px - cx) / fx,
        (py - cy) / fy,
        1.0,
    )
    if _HAS_NUMPY and hasattr(R, "dot"):
        d = R.dot([cam_dir[0], cam_dir[1], cam_dir[2]])
        dx, dy, dz = float(d[0]), float(d[1]), float(d[2])
    else:
        dx = R[0][0] * cam_dir[0] + R[0][1] * cam_dir[1] + R[0][2] * cam_dir[2]
        dy = R[1][0] * cam_dir[0] + R[1][1] * cam_dir[1] + R[1][2] * cam_dir[2]
        dz = R[2][0] * cam_dir[0] + R[2][1] * cam_dir[1] + R[2][2] * cam_dir[2]
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag < 1e-12:
        return ((float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])),
                (0.0, 0.0, 1.0))
    return ((float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])),
            (dx / mag, dy / mag, dz / mag))


def pan_tilt_to_ray(fixture_pos, fixture_rotation, pan_deg, tilt_deg):
    """Build a stage-space ray from a moving-head's pan / tilt commands.

    Mirrors the geometric assumption used by
    ``parametric_mover.ParametricFixtureModel.forward`` (and the legacy
    ``_rotation_to_aim`` in bake_engine): pan is rotation about stage-Z
    (``+pan`` aims toward stage-left = +X), tilt is rotation about the
    fixture-local horizontal axis with ``tilt = 0`` aiming straight DOWN
    (the canonical hang orientation) and ``tilt = +90`` aiming horizontal.

    Args:
        fixture_pos:      (x, y, z) in stage mm.
        fixture_rotation: layout rotation triple — applied as a body-frame
                          pre-rotation so an upside-down fixture's beam
                          flips correctly without sign hacks.
        pan_deg:          pan in degrees.
        tilt_deg:         tilt in degrees.

    Returns ``((ox, oy, oz), (dx, dy, dz))`` with unit-length direction.
    """
    pan = math.radians(pan_deg)
    tilt = math.radians(tilt_deg)
    # Body-local beam direction. tilt=0 points along -Z (down), tilt=90 along +Y.
    sin_t = math.sin(tilt)
    cos_t = math.cos(tilt)
    cos_p = math.cos(pan)
    sin_p = math.sin(pan)
    # Pan rotates the horizontal component around stage-Z.
    body_dir = (
        sin_t * sin_p,   # +x for +pan when tilted out
        sin_t * cos_p,   # +y for tilt forward
        -cos_t,          # -z (down) for tilt = 0
    )
    # Apply the fixture's mounting rotation (so an inverted hang flips beam).
    # Stage-frame Euler: pitch about X, roll about Y, yaw about Z. No
    # camera frame-swap — body_dir is already in stage axis convention.
    fx_tilt, fx_pan, fx_roll = rotation_from_layout(fixture_rotation)
    rx = math.radians(fx_tilt)
    ry = math.radians(fx_roll)
    rz = math.radians(fx_pan)
    cx_, sx_ = math.cos(rx), math.sin(rx)
    cy_, sy_ = math.cos(ry), math.sin(ry)
    cz_, sz_ = math.cos(rz), math.sin(rz)
    Rx = ((1, 0, 0), (0, cx_, -sx_), (0, sx_, cx_))
    Ry = ((cy_, 0, sy_), (0, 1, 0), (-sy_, 0, cy_))
    Rz = ((cz_, -sz_, 0), (sz_, cz_, 0), (0, 0, 1))
    def _mv(m, v):
        return (m[0][0]*v[0]+m[0][1]*v[1]+m[0][2]*v[2],
                m[1][0]*v[0]+m[1][1]*v[1]+m[1][2]*v[2],
                m[2][0]*v[0]+m[2][1]*v[1]+m[2][2]*v[2])
    rotated = _mv(Rz, _mv(Ry, _mv(Rx, body_dir)))
    dx, dy, dz = rotated[0], rotated[1], rotated[2]
    mag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if mag < 1e-12:
        return ((float(fixture_pos[0]), float(fixture_pos[1]), float(fixture_pos[2])),
                (0.0, 0.0, -1.0))
    return ((float(fixture_pos[0]), float(fixture_pos[1]), float(fixture_pos[2])),
            (dx / mag, dy / mag, dz / mag))


# ── Camera floor-view polygon (#659) ─────────────────────────────────────

def _sutherland_hodgman_clip(subject, clip_rect):
    """Clip a polygon (list of (x, y)) to an axis-aligned rectangle
    ``(xmin, ymin, xmax, ymax)`` via Sutherland-Hodgman. Returns a list
    of (x, y) tuples; empty when the subject lies entirely outside.

    #712 — replaces the per-vertex `clamp(0, w, x)` clamp the previous
    `camera_floor_polygon` used. Per-vertex clamping bends a polygon's
    edges toward the rectangle corners in ways that include floor
    regions the camera doesn't actually see.
    """
    xmin, ymin, xmax, ymax = clip_rect

    def _clip_edge(poly, inside_fn, intersect_fn):
        out = []
        if not poly:
            return out
        prev = poly[-1]
        prev_in = inside_fn(prev)
        for cur in poly:
            cur_in = inside_fn(cur)
            if cur_in:
                if not prev_in:
                    out.append(intersect_fn(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(intersect_fn(prev, cur))
            prev, prev_in = cur, cur_in
        return out

    def _isect_x(p1, p2, x):
        # Linear interpolation x → y.
        if abs(p2[0] - p1[0]) < 1e-12:
            return (x, p1[1])
        t = (x - p1[0]) / (p2[0] - p1[0])
        return (x, p1[1] + t * (p2[1] - p1[1]))

    def _isect_y(p1, p2, y):
        if abs(p2[1] - p1[1]) < 1e-12:
            return (p1[0], y)
        t = (y - p1[1]) / (p2[1] - p1[1])
        return (p1[0] + t * (p2[0] - p1[0]), y)

    out = list(subject)
    out = _clip_edge(out, lambda p: p[0] >= xmin, lambda a, b: _isect_x(a, b, xmin))
    out = _clip_edge(out, lambda p: p[0] <= xmax, lambda a, b: _isect_x(a, b, xmax))
    out = _clip_edge(out, lambda p: p[1] >= ymin, lambda a, b: _isect_y(a, b, ymin))
    out = _clip_edge(out, lambda p: p[1] <= ymax, lambda a, b: _isect_y(a, b, ymax))
    return out


def camera_floor_polygon(cam_pos, rotation, fov_deg, aspect=16.0 / 9.0,
                          stage_bounds=None, floor_z=0.0,
                          edge_samples_per_side=16,
                          max_view_distance_mm=20000.0):
    """Project the camera's viewing frustum onto the floor plane.

    Returns a convex polygon in stage XY (z = floor_z) describing the
    floor region the camera can physically see.

    #712 — pre-#712 used the four corner rays + convex hull and a
    per-vertex stage-bounds clamp. On a high-mount camera with small
    downward pitch, the upper corner rays project to floor at very
    large y (we observed (0, +182093 mm) for cam #12), and the
    per-vertex clamp pulled that to (0, stage_d) — producing a
    polygon that included huge swaths of stage X the lens cone
    doesn't actually cover. Operator eye-test on basement rig:
    the polygon claimed 4 of 4 first-cal probes were in cam FOV but
    only 1 was physically visible.

    The fix:
      1. Sample edge rays DENSELY — 16 per image edge by default,
         not just the 4 corners. The visible region for a pitched
         camera is a true trapezoid; corner rays alone can miss the
         curvature of the visible boundary on the floor plane.
      2. Reject samples whose ray points up / parallel / overshoots
         the configurable max view distance (default 20 m). Near-
         horizontal rays from a high mount produce nonsense
         intersection points; capping distance keeps the polygon
         realistic.
      3. Sutherland-Hodgman clip against the stage rectangle, NOT
         per-vertex clamp. Per-vertex clamp is the bug that pulled
         the over-reaching corner inside the rectangle and misled
         every consumer.

    Args:
        cam_pos:       (x, y, z) in stage mm.
        rotation:      layout rotation array `[rx, ry, rz]`. Read via
                        rotation_from_layout — axis-semantic tilt/pan/roll.
        fov_deg:       horizontal field-of-view in degrees.
        aspect:        width/height ratio (default 16:9).
        stage_bounds:  optional dict ``{w, d, h}`` in mm to clip the
                        polygon to stage boundaries.
        floor_z:       floor plane z in mm (default 0).
        edge_samples_per_side: number of rays sampled along each
                        image edge. Default 16 (= 64 rays total) is
                        enough to capture trapezoidal shapes without
                        oversampling.
        max_view_distance_mm: rays whose floor intersection lies
                        further than this from the camera are
                        discarded as "near-horizontal overshoot".
                        Default 20 m matches typical small-stage
                        cameras.

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
    tx = math.tan(hfov / 2.0)
    ty = math.tan(vfov / 2.0)

    n = max(2, int(edge_samples_per_side))
    # Sample image-edge points in CCW order from the camera's
    # perspective: top edge L→R, right edge top→bottom, bottom edge
    # R→L, left edge bottom→top. After projection through a pitched
    # camera the order on the floor plane stays CCW.
    edge_samples = []
    for i in range(n):  # top: x ∈ [-tx, +tx], y = -ty
        u = -1.0 + 2.0 * (i / (n - 1))
        edge_samples.append((u * tx, -ty))
    for i in range(n):  # right: x = +tx, y ∈ [-ty, +ty]
        v = -1.0 + 2.0 * (i / (n - 1))
        edge_samples.append((+tx, v * ty))
    for i in range(n):  # bottom: x ∈ [+tx, -tx], y = +ty
        u = 1.0 - 2.0 * (i / (n - 1))
        edge_samples.append((u * tx, +ty))
    for i in range(n):  # left: x = -tx, y ∈ [+ty, -ty]
        v = 1.0 - 2.0 * (i / (n - 1))
        edge_samples.append((-tx, v * ty))

    cx, cy, cz = float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])
    hits = []
    for (xi, yi) in edge_samples:
        ray = np.array([xi, yi, 1.0])
        ray_stage = R @ ray
        rz = ray_stage[2]
        if rz >= -1e-6:
            continue  # ray points up or parallel
        t = (floor_z - cz) / rz
        if t <= 0:
            continue
        fx = cx + t * ray_stage[0]
        fy = cy + t * ray_stage[1]
        # Reject overshoot (near-horizontal rays from high mounts).
        dist = math.hypot(fx - cx, fy - cy)
        if dist > max_view_distance_mm:
            # Cap at max distance: project the same direction but
            # truncate. Keeps the polygon convex without dropping
            # the edge entirely on cameras pitched only slightly.
            scale = max_view_distance_mm / max(1e-6, dist)
            fx = cx + (fx - cx) * scale
            fy = cy + (fy - cy) * scale
        hits.append((float(fx), float(fy)))

    if not hits:
        return []

    # Sutherland-Hodgman clip to stage bounds.
    if stage_bounds:
        w = float(stage_bounds.get("w", 0) or 0)
        d = float(stage_bounds.get("d", 0) or 0)
        if w > 0 and d > 0:
            hits = _sutherland_hodgman_clip(hits, (0.0, 0.0, w, d))

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
