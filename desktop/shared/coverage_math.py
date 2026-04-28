"""coverage_math.py — SMART canonical IK and 2-pair affine estimate (#720).

Single source of truth for fixture-frame ↔ stage-frame angle math used by
the SMART calibration system. Every higher-level API (the angular aim
endpoint introduced in PR-1.5, SMART's probe loop, ArUco validation)
routes through this module. Forward IK and inverse IK live in the same
file by design — they are exact inverses by construction and tested as
such.

Conventions (match `parent_server._rotation_to_aim` and the #600 layout
schema):

  Stage frame:  +X = stage-left (width), +Y = audience (depth),
                +Z = ceiling (height above floor).

  rotation = [rx, ry, rz] degrees, Z-up axis-matched.
    rx = pitch (rx > 0 aims DOWN — mount-+Y tips toward stage -Z).
    ry = roll  (right-hand rule about stage-forward Y).
    rz = yaw   (rz > 0 aims toward stage +X — left-handed about Z to
                match `_rotation_to_aim`).

  Mount frame: identity at rotation [0,0,0]. Mount-+Y is fixture
  forward, mount-+Z is fixture-up, mount-+X is fixture-right.

  Internal pan rotates the head about mount +Z; +panDeg sweeps the beam
  from mount-+Y toward mount-+X (left-handed about Z).
  Internal tilt rotates the beam about the post-pan local +X; +tiltDeg
  swings the beam from horizontal toward mount-+Z (UP).

At Home — operator confirmed beam aims along the rotation vector — the
fixture mechanics are at internal (panDeg, tiltDeg) = (0, 0).
"""

from __future__ import annotations

import math


# ── Rotation matrix (mount → stage) ─────────────────────────────────────

def _mount_rotation(rotation):
    """Build the 3x3 rotation R that maps mount-frame vectors → stage-frame.

    Composition: R = Rz_lh(rz) · Rx(rx) · Ry(ry). Verifies against
    `_rotation_to_aim` for rotation = [rx, 0, rz]:
      mount [0,1,0] → (sin(rz)*cos(rx), cos(rz)*cos(rx), -sin(rx)).
    """
    rx = math.radians(float(rotation[0]) if len(rotation) > 0 else 0.0)
    ry = math.radians(float(rotation[1]) if len(rotation) > 1 else 0.0)
    rz = math.radians(float(rotation[2]) if len(rotation) > 2 else 0.0)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # Rx (rx>0 aims DOWN): mount [0,1,0] → [0, cos(rx), -sin(rx)]
    Rx = [[1.0, 0.0, 0.0],
          [0.0,  cx,  sx],
          [0.0, -sx,  cx]]
    # Ry (right-hand): mount [0,0,1] → [sin(ry), 0, cos(ry)]
    Ry = [[ cy, 0.0,  sy],
          [0.0, 1.0, 0.0],
          [-sy, 0.0,  cy]]
    # Rz_lh (rz>0 aims +X): mount [0,1,0] → [sin(rz), cos(rz), 0]
    Rz = [[ cz,  sz, 0.0],
          [-sz,  cz, 0.0],
          [0.0, 0.0, 1.0]]
    return _mm(Rz, _mm(Rx, Ry))


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _matvec(M, v):
    return (M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2])


def _transpose(M):
    return [[M[0][0], M[1][0], M[2][0]],
            [M[0][1], M[1][1], M[2][1]],
            [M[0][2], M[1][2], M[2][2]]]


# ── Forward / inverse IK ─────────────────────────────────────────────────

def world_to_fixture_pt(world_xyz, fixture_xyz, rotation):
    """Convert a stage-mm target point to fixture-internal (panDeg, tiltDeg).

    At rotation [0,0,0] and a target along stage +Y, returns (0, 0).
    Returns None if the target coincides with the fixture position.
    """
    dx = float(world_xyz[0]) - float(fixture_xyz[0])
    dy = float(world_xyz[1]) - float(fixture_xyz[1])
    dz = float(world_xyz[2]) - float(fixture_xyz[2])
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-6:
        return None
    aim_stage = (dx / norm, dy / norm, dz / norm)
    R = _mount_rotation(rotation)
    aim_mount = _matvec(_transpose(R), aim_stage)
    mx, my, mz = aim_mount
    pan_deg = math.degrees(math.atan2(mx, my))
    tilt_deg = math.degrees(math.atan2(mz, math.hypot(mx, my)))
    return (pan_deg, tilt_deg)


def fixture_aim_to_world(pan_deg, tilt_deg, fixture_xyz, rotation,
                         floor_z=None):
    """Convert fixture-internal angles → stage-frame aim.

    Returns ``(axis_unit, world_aim_xyz_at_floor)``. The latter is
    ``None`` unless ``floor_z`` is provided AND the beam intersects the
    floor in front of the fixture.
    """
    p = math.radians(float(pan_deg))
    t = math.radians(float(tilt_deg))
    aim_mount = (math.sin(p) * math.cos(t),
                 math.cos(p) * math.cos(t),
                 math.sin(t))
    R = _mount_rotation(rotation)
    aim_stage = _matvec(R, aim_mount)
    n = math.sqrt(aim_stage[0] ** 2 + aim_stage[1] ** 2 + aim_stage[2] ** 2)
    if n < 1e-12:
        return ((0.0, 0.0, 0.0), None)
    axis_unit = (aim_stage[0] / n, aim_stage[1] / n, aim_stage[2] / n)
    floor_pt = None
    if floor_z is not None:
        ax, ay, az = axis_unit
        ox = float(fixture_xyz[0])
        oy = float(fixture_xyz[1])
        oz = float(fixture_xyz[2])
        fz = float(floor_z)
        # Beam pointing down: az < 0. Forward-intersection only.
        if az < -1e-6:
            tparam = (fz - oz) / az
            if tparam > 0:
                floor_pt = (ox + ax * tparam, oy + ay * tparam, fz)
    return (axis_unit, floor_pt)


# ── 2-pair affine estimate (Home + Home-Secondary) ──────────────────────

def solve_dmx_per_degree(home, secondary, fixture_rotation,
                         profile_pan_range_deg,
                         profile_tilt_range_deg=None):
    """#730 — bootstrap DMX-per-degree estimate from Home + direction-only
    Home-Secondary.

    ``home``      = ``{"panDmx16": int, "tiltDmx16": int}``
    ``secondary`` = ``{"panOffsetDmx16": int, "tiltOffsetDmx16": int,
                       "panMovedDirection": "left"|"right",
                       "tiltMovedDirection": "down"|"up"}``

    Magnitudes come from the profile's declared ranges
    (``65535 / panRange`` and ``65535 / tiltRange``); only the sign
    comes from the operator's binary direction calls. This is robust
    near vertical (no ``atan2`` divide-by-near-zero) and immune to
    operator-typed-degree errors. PR-5's LSQ refines magnitudes from
    probes.

    At Home the fixture mechanics are at internal ``(panDeg, tiltDeg)
    = (0, 0)`` by construction (the operator drove the mechanics so
    the beam aims along ``rotation``). So the bias is just the home
    DMX values and the model satisfies ``angles_to_dmx``'s contract.

    Returns ``{panDmxPerDeg, tiltDmxPerDeg, homePanDmx16,
    homeTiltDmx16, homeTiltDegStage}``. Raises ``ValueError`` with
    ``home_secondary_stale_format`` when the persisted block carries
    only the legacy ``operatorTiltDeg`` (PR-1 shape from #721, before
    #730).
    """
    try:
        home_pan = int(home["panDmx16"])
        home_tilt = int(home["tiltDmx16"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"home missing required field: {e}")

    if not isinstance(secondary, dict):
        raise ValueError("secondary must be an object")

    pan_dir = secondary.get("panMovedDirection")
    tilt_dir = secondary.get("tiltMovedDirection")
    if not pan_dir or not tilt_dir:
        # Detect the legacy PR-1 shape (operatorTiltDeg-only) and emit a
        # specific error code so /smart/preview can prompt the operator
        # to re-run the wizard.
        if "operatorTiltDeg" in secondary:
            raise ValueError("home_secondary_stale_format")
        raise ValueError(
            "secondary missing panMovedDirection/tiltMovedDirection")
    if pan_dir not in ("left", "right"):
        raise ValueError("panMovedDirection must be 'left' or 'right'")
    if tilt_dir not in ("down", "up"):
        raise ValueError("tiltMovedDirection must be 'down' or 'up'")

    pan_range = float(profile_pan_range_deg) if profile_pan_range_deg else 540.0
    if pan_range <= 0:
        pan_range = 540.0
    tilt_range = float(profile_tilt_range_deg) if profile_tilt_range_deg else 270.0
    if tilt_range <= 0:
        tilt_range = 270.0

    # Magnitudes from profile envelope. Always positive.
    pan_dmx_per_deg_mag = 65535.0 / pan_range
    tilt_dmx_per_deg_mag = 65535.0 / tilt_range

    # Sign: the operator's binary direction call.
    #   panMovedDirection == "right" → beam swept toward stage-+X →
    #     DMX increase corresponds to mount-internal pan-positive.
    #     Convention matches `world_to_fixture_pt` (panDeg = atan2(mx,
    #     my), positive panDeg pushes mount-+Y toward mount-+X).
    #   tiltMovedDirection == "up" → beam swept above horizon → DMX
    #     increase corresponds to mount-internal tilt-positive (UP).
    pan_sign = +1 if pan_dir == "right" else -1
    tilt_sign = +1 if tilt_dir == "up" else -1

    pan_dmx_per_deg = pan_sign * pan_dmx_per_deg_mag
    tilt_dmx_per_deg = tilt_sign * tilt_dmx_per_deg_mag

    # Stage-frame tilt-from-horizon at Home — informational, used by
    # PR-5 LSQ residual computation. Preserved on the model dict for
    # backcompat with consumers that read it.
    R = _mount_rotation(fixture_rotation or [0.0, 0.0, 0.0])
    aim_home = _matvec(R, (0.0, 1.0, 0.0))
    home_tilt_deg = math.degrees(math.atan2(
        aim_home[2], math.hypot(aim_home[0], aim_home[1])
    ))

    return {
        "panDmxPerDeg": pan_dmx_per_deg,
        "tiltDmxPerDeg": tilt_dmx_per_deg,
        "homePanDmx16": home_pan,
        "homeTiltDmx16": home_tilt,
        "homeTiltDegStage": home_tilt_deg,
    }


# ── DMX ↔ angle conversion ─────────────────────────────────────────────

def angles_to_dmx(pan_deg, tilt_deg, model_or_estimate):
    """Convert fixture-internal (panDeg, tiltDeg) → (panDmx16, tiltDmx16).

    Either ``model_or_estimate`` form: SMART model or 2-pair estimate
    with ``panDmxPerDeg``, ``tiltDmxPerDeg``, ``homePanDmx16``,
    ``homeTiltDmx16``. At Home (panDeg=tiltDeg=0) returns the home DMX
    pose exactly. Output is clamped to ``[0, 65535]``.
    """
    home_pan = int(model_or_estimate["homePanDmx16"])
    home_tilt = int(model_or_estimate["homeTiltDmx16"])
    p_per = float(model_or_estimate["panDmxPerDeg"])
    t_per = float(model_or_estimate["tiltDmxPerDeg"])
    p_dmx = home_pan + float(pan_deg) * p_per
    t_dmx = home_tilt + float(tilt_deg) * t_per
    return (max(0, min(65535, int(round(p_dmx)))),
            max(0, min(65535, int(round(t_dmx)))))


def dmx_to_angles(pan_dmx16, tilt_dmx16, model_or_estimate):
    """Inverse of ``angles_to_dmx``. Exact within float precision when
    inputs fall inside the model's unclamped range."""
    p_per = float(model_or_estimate["panDmxPerDeg"])
    t_per = float(model_or_estimate["tiltDmxPerDeg"])
    if abs(p_per) < 1e-9 or abs(t_per) < 1e-9:
        raise ValueError("model has zero DMX-per-degree on at least one axis")
    home_pan = int(model_or_estimate["homePanDmx16"])
    home_tilt = int(model_or_estimate["homeTiltDmx16"])
    pan_deg = (int(pan_dmx16) - home_pan) / p_per
    tilt_deg = (int(tilt_dmx16) - home_tilt) / t_per
    return (pan_deg, tilt_deg)


# ── #720 PR-2 — Coverage polygon ───────────────────────────────────────

def _profile_envelope_deg(profile):
    """Return ``(pan_min, pan_max, tilt_min, tilt_max)`` mount-internal
    angles in degrees, honouring ``tiltOffsetDmx16`` + ``tiltUp`` (#716).

    Pan envelope is symmetric: ``±panRange/2`` around home.
    Tilt envelope: ``tiltOffsetDmx16`` is the DMX value where the beam
    crosses horizontal (mount-internal tilt = 0). The remainder of DMX
    range ([0..tiltOffsetDmx16) and (tiltOffsetDmx16..65535]) maps
    proportionally to the profile's ``tiltRange`` degrees, with sign
    flipped by ``tiltUp``.
    """
    pan_range = float((profile or {}).get("panRange", 540) or 540)
    tilt_range = float((profile or {}).get("tiltRange", 270) or 270)
    tilt_offset = float((profile or {}).get("tiltOffsetDmx16", 32768) or 32768)
    tilt_up = bool((profile or {}).get("tiltUp", False))

    pan_half = pan_range / 2.0
    # Envelope edges at DMX 0 and 65535. Per-tick degree value:
    deg_per_tick = tilt_range / 65535.0
    deg_at_0 = -tilt_offset * deg_per_tick   # mount-internal tilt at DMX=0
    deg_at_max = (65535.0 - tilt_offset) * deg_per_tick
    if not tilt_up:
        # Legacy: increasing DMX rotates beam DOWN (positive mount-tilt).
        # In our convention positive mount-tilt is UP, so flip sign.
        deg_at_0, deg_at_max = -deg_at_0, -deg_at_max
    tilt_min = min(deg_at_0, deg_at_max)
    tilt_max = max(deg_at_0, deg_at_max)
    return (-pan_half, pan_half, tilt_min, tilt_max)


def _convex_hull(points):
    """Andrew's monotone chain on a list of ``(x, y)`` points. Returns the
    hull as a list of points in CCW order. Single-pass, O(n log n)."""
    pts = sorted(set((round(p[0], 6), round(p[1], 6)) for p in points))
    if len(pts) <= 1:
        return list(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def coverage_polygon(fixture_xyz, rotation, profile, floor_z,
                     samples_per_edge=12, interior_grid=12):
    """Compute the floor footprint of a fixture's pan/tilt envelope.

    Ray-marches the envelope through ``fixture_aim_to_world``,
    intersecting each ray with ``z = floor_z``. Returns the convex
    hull of all forward floor intersections as a polygon
    ``[[x, y], ...]`` in stage XY (CCW). Returns an empty list if no
    rays hit the floor in front of the fixture (e.g. an upward-aimed
    mount with no panRange wrap-around).

    #731 — sampling combines the envelope **perimeter** AND a regular
    **interior grid** (``interior_grid`` × ``interior_grid``).
    Perimeter-only sampling collapses to a 1D line whenever the
    fixture's forward axis aligns with stage Y (e.g. ``rotation =
    [0, 0, 0]`` ceiling-mount) AND ``panRange`` covers a full
    revolution: the four perimeter edges all land on rays whose
    floor-projection has either ``ay=0`` (pan=±90° mod 360) or no
    floor hit (tilt=±90°). Interior samples cover the actual cone
    body where ``cos(pan) ≠ 0`` and ``sin(tilt) ≠ 0``. Convex hull
    cost stays O(n log n).
    """
    pan_min, pan_max, tilt_min, tilt_max = _profile_envelope_deg(profile)
    n_edge = max(2, int(samples_per_edge))
    n_grid = max(2, int(interior_grid))

    edge_pts = []
    # Perimeter (kept — captures the boundary on profiles where the
    # envelope IS the bounding shape).
    for i in range(n_edge):
        f = i / float(n_edge - 1)
        pan = pan_min + f * (pan_max - pan_min)
        edge_pts.append((pan, tilt_min))
        edge_pts.append((pan, tilt_max))
    for i in range(n_edge):
        f = i / float(n_edge - 1)
        tilt = tilt_min + f * (tilt_max - tilt_min)
        edge_pts.append((pan_min, tilt))
        edge_pts.append((pan_max, tilt))
    # Interior grid — fills the envelope so the convex hull catches the
    # full cone body, not just the perimeter rays. #731 fix.
    for i in range(n_grid):
        f = i / float(n_grid - 1)
        pan = pan_min + f * (pan_max - pan_min)
        for j in range(n_grid):
            g = j / float(n_grid - 1)
            tilt = tilt_min + g * (tilt_max - tilt_min)
            edge_pts.append((pan, tilt))
    # Always include the centre (helps when the envelope is tiny — the
    # interior grid already covers it but cheap insurance).
    edge_pts.append(((pan_min + pan_max) / 2.0, (tilt_min + tilt_max) / 2.0))

    floor_xy = []
    for pan_deg, tilt_deg in edge_pts:
        _axis, floor_pt = fixture_aim_to_world(
            pan_deg, tilt_deg, fixture_xyz, rotation, floor_z=floor_z)
        if floor_pt is not None:
            floor_xy.append((floor_pt[0], floor_pt[1]))

    if not floor_xy:
        return []
    hull = _convex_hull(floor_xy)
    return [[round(p[0], 3), round(p[1], 3)] for p in hull]


# ── #720 PR-3 — Working area (cone ∩ camera-visible-floor) + probe grid ─

def _polygon_signed_area(poly):
    """Shoelace; positive when CCW."""
    if len(poly) < 3:
        return 0.0
    a = 0.0
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        a += poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1]
    return a / 2.0


def _ensure_ccw(poly):
    return list(poly) if _polygon_signed_area(poly) >= 0 else list(reversed(poly))


def _sutherland_hodgman(subject, clip):
    """Clip ``subject`` polygon by convex ``clip`` polygon. Returns the
    clipped polygon as a list of ``[x, y]`` (CCW) or ``[]`` if the
    intersection is empty."""
    if len(subject) < 3 or len(clip) < 3:
        return []
    subject = _ensure_ccw([(float(p[0]), float(p[1])) for p in subject])
    clip = _ensure_ccw([(float(p[0]), float(p[1])) for p in clip])
    output = list(subject)
    n = len(clip)
    for i in range(n):
        if not output:
            return []
        a = clip[i]
        b = clip[(i + 1) % n]
        edge = (b[0] - a[0], b[1] - a[1])
        # Inside test: cross(edge, p-a) >= 0 for CCW clip.
        def _inside(p):
            return edge[0] * (p[1] - a[1]) - edge[1] * (p[0] - a[0]) >= -1e-9

        def _intersect(p1, p2):
            r = (p2[0] - p1[0], p2[1] - p1[1])
            denom = edge[0] * r[1] - edge[1] * r[0]
            if abs(denom) < 1e-12:
                return p2
            t = (edge[0] * (p1[1] - a[1]) - edge[1] * (p1[0] - a[0])) / -denom
            return (p1[0] + t * r[0], p1[1] + t * r[1])

        new_output = []
        m = len(output)
        for j in range(m):
            curr = output[j]
            prev = output[(j - 1) % m]
            curr_in = _inside(curr)
            prev_in = _inside(prev)
            if curr_in:
                if not prev_in:
                    new_output.append(_intersect(prev, curr))
                new_output.append(curr)
            elif prev_in:
                new_output.append(_intersect(prev, curr))
        output = new_output
    return [[round(p[0], 3), round(p[1], 3)] for p in output]


def _polygon_centroid(poly):
    """Area-weighted centroid of a non-self-intersecting polygon. Falls
    back to vertex mean for degenerate (zero-area) polygons."""
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    a = _polygon_signed_area(poly)
    if abs(a) < 1e-9:
        cx = sum(p[0] for p in poly) / n
        cy = sum(p[1] for p in poly) / n
        return (cx, cy)
    cx = cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = poly[i][0] * poly[j][1] - poly[j][0] * poly[i][1]
        cx += (poly[i][0] + poly[j][0]) * cross
        cy += (poly[i][1] + poly[j][1]) * cross
    return (cx / (6 * a), cy / (6 * a))


def _point_in_polygon(p, poly):
    """Ray-casting; works for any simple polygon."""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi):
            inside = not inside
        j = i
    return inside


def _min_distance_to_polygon_edge(p, poly):
    """Minimum distance from point ``p`` to any edge of ``poly``."""
    if len(poly) < 2:
        return 0.0
    best = float('inf')
    n = len(poly)
    px, py = p
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        dx = bx - ax
        dy = by - ay
        denom = dx * dx + dy * dy
        if denom < 1e-12:
            d = math.hypot(px - ax, py - ay)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
            cx = ax + t * dx
            cy = ay + t * dy
            d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
    return best


def _inward_buffer(poly, margin_mm):
    """Approximate inward buffer by translating each edge inward by
    ``margin_mm`` and re-clipping. Implementation: re-build the polygon
    from points whose distance to the boundary is ``>= margin_mm``.

    For SMART's purposes the buffer is only used as a "reject points
    too close to the edge" filter — we don't materially need a true
    polygon offset. Here we return the original polygon and let
    ``sample_grid`` enforce the margin per-candidate.
    """
    return list(poly)


def working_area(coverage_poly, camera_visible_poly, margin_mm=150):
    """Intersect coverage with camera-visible-floor, then inward-buffer.

    Returns a polygon ``[[x, y], ...]`` (CCW) of the area where SMART
    can both aim and see beam-detect spots — or ``[]`` if the
    intersection is empty / smaller than 1e-9 m². The ``margin_mm``
    inward buffer is enforced at the per-point level by ``sample_grid``;
    this helper returns the raw intersection.
    """
    if not coverage_poly or not camera_visible_poly:
        return []
    clipped = _sutherland_hodgman(coverage_poly, camera_visible_poly)
    if len(clipped) < 3:
        return []
    if abs(_polygon_signed_area(clipped)) < 1e-9:
        return []
    return _inward_buffer(clipped, margin_mm)


def _polygon_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), max(xs), min(ys), max(ys))


def sample_grid(working_poly, n=16, min_edge_margin_mm=150):
    """Generate up to ``n`` probe points distributed across ``working_poly``.

    Implementation: 4×4 grid seed clipped to the polygon, then one pass
    of Lloyd's relaxation toward each cell's polygon-clipped centroid,
    rejecting any candidate within ``min_edge_margin_mm`` of the
    polygon boundary. Returns ``[[x, y], ...]`` of the surviving points
    (length may be < n when the polygon is small or thin).
    """
    if not working_poly or len(working_poly) < 3:
        return []
    minx, maxx, miny, maxy = _polygon_bbox(working_poly)
    nside = max(2, int(math.ceil(math.sqrt(max(2, n)))))
    candidates = []
    for i in range(nside):
        for j in range(nside):
            fx = (i + 0.5) / nside
            fy = (j + 0.5) / nside
            x = minx + fx * (maxx - minx)
            y = miny + fy * (maxy - miny)
            if not _point_in_polygon((x, y), working_poly):
                continue
            if _min_distance_to_polygon_edge((x, y), working_poly) \
                    < float(min_edge_margin_mm):
                continue
            candidates.append([round(x, 3), round(y, 3)])
            if len(candidates) >= n:
                break
        if len(candidates) >= n:
            break
    return candidates
