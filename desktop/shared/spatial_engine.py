"""
SlyLED Spatial Engine — geometry resolver and effect field evaluation.

Phase 2: SpatialResolver — convert fixture geometry to per-pixel 3D coordinates.
Phase 3: Intersector — evaluate spatial effect fields against pixel positions.
Phase 4: Capability layer — fixture-type-agnostic response function
    (docs/mover-alignment-review.md §0, §8.1b). A fixture at a stage-mm
    pose evaluates any spatial effect to a bundle of primitive outputs
    (color, intensity, aim, beam_width). The bundle is fixture-agnostic;
    each consumer reads only the primitives it declares in `caps[]`.
"""

from dataclasses import dataclass
import math

# ── Phase 2: SpatialResolver ────────────────────────────────────────────────

def catmull_rom_sample(points, n):
    """Sample n evenly-spaced points along a Catmull-Rom spline through control points.

    Args:
        points: list of [x, y, z] control points (minimum 2)
        n: number of output samples

    Returns:
        list of [x, y, z] sampled positions
    """
    if not points or n <= 0:
        return []
    if len(points) == 1:
        return [list(points[0])] * n
    if len(points) == 2:
        # Linear interpolation
        p0, p1 = points[0], points[1]
        return [[p0[j] + (p1[j] - p0[j]) * i / max(n - 1, 1) for j in range(3)] for i in range(n)]

    # Pad endpoints for Catmull-Rom (duplicate first and last)
    pts = [points[0]] + list(points) + [points[-1]]

    # Compute cumulative arc length for uniform sampling
    seg_lengths = []
    for i in range(1, len(points)):
        dx = points[i][0] - points[i-1][0]
        dy = points[i][1] - points[i-1][1]
        dz = points[i][2] - points[i-1][2]
        seg_lengths.append(math.sqrt(dx*dx + dy*dy + dz*dz))
    total_len = sum(seg_lengths) or 1.0

    result = []
    for i in range(n):
        # Target distance along the spline
        target = (i / max(n - 1, 1)) * total_len
        # Find which segment this falls in
        accum = 0.0
        seg = 0
        for s in range(len(seg_lengths)):
            if accum + seg_lengths[s] >= target - 1e-9:
                seg = s
                break
            accum += seg_lengths[s]
            seg = s

        # Local t within segment [0, 1]
        t = (target - accum) / seg_lengths[seg] if seg_lengths[seg] > 0 else 0.0
        t = max(0.0, min(1.0, t))

        # Catmull-Rom interpolation using pts[seg..seg+3] (padded array)
        p0 = pts[seg]
        p1 = pts[seg + 1]
        p2 = pts[seg + 2]
        p3 = pts[seg + 3]

        t2 = t * t
        t3 = t2 * t

        point = [0.0, 0.0, 0.0]
        for j in range(3):
            point[j] = 0.5 * (
                (2 * p1[j]) +
                (-p0[j] + p2[j]) * t +
                (2*p0[j] - 5*p1[j] + 4*p2[j] - p3[j]) * t2 +
                (-p0[j] + 3*p1[j] - 3*p2[j] + p3[j]) * t3
            )
        result.append(point)

    return result


def _rotate_vec(v, origin, rot_deg):
    """Rotate vector v around origin by rot_deg [rx, ry, rz] in degrees."""
    if not rot_deg or all(r == 0 for r in rot_deg):
        return v
    # Translate to origin
    dx, dy, dz = v[0] - origin[0], v[1] - origin[1], v[2] - origin[2]
    rx, ry, rz = [math.radians(r) for r in rot_deg]
    # Rotate around X
    if rx:
        cy, cz = dy, dz
        dy = cy * math.cos(rx) - cz * math.sin(rx)
        dz = cy * math.sin(rx) + cz * math.cos(rx)
    # Rotate around Y
    if ry:
        cx, cz = dx, dz
        dx = cx * math.cos(ry) + cz * math.sin(ry)
        dz = -cx * math.sin(ry) + cz * math.cos(ry)
    # Rotate around Z
    if rz:
        cx, cy = dx, dy
        dx = cx * math.cos(rz) - cy * math.sin(rz)
        dy = cx * math.sin(rz) + cy * math.cos(rz)
    return [origin[0] + dx, origin[1] + dy, origin[2] + dz]


def resolve_linear_fixture(child_pos, string_cfg, fixture_points=None, rotation=None):
    """Resolve per-pixel positions for a linear fixture (LED string).

    Args:
        child_pos: [x, y, z] of the child in mm
        string_cfg: dict with keys: leds, mm, sdir (0=E,1=N,2=W,3=S), plus optional points
        fixture_points: list of [x,y,z] control points in mm (overrides auto-compute)
        rotation: [rx, ry, rz] degrees — fixture rotation override (applied after direction)

    Returns:
        list of [x, y, z] pixel positions in mm
    """
    leds = string_cfg.get("leds", 0)
    if leds <= 0:
        return []

    if fixture_points and len(fixture_points) >= 2:
        pixels = catmull_rom_sample(fixture_points, leds)
        if rotation and any(r != 0 for r in rotation):
            pixels = [_rotate_vec(p, child_pos, rotation) for p in pixels]
        return pixels

    # Auto-compute: straight line from child_pos in strip direction
    length = string_cfg.get("mm", 1000)
    sdir = string_cfg.get("sdir", 0)
    # Direction vectors: E=+X, N=+Y, W=-X, S=-Y (in layout mm space)
    dirs = [[1,0,0], [0,1,0], [-1,0,0], [0,-1,0]]
    d = dirs[sdir] if sdir < 4 else dirs[0]

    start = list(child_pos)
    end = [child_pos[0] + d[0] * length,
           child_pos[1] + d[1] * length,
           child_pos[2] + d[2] * length]

    pixels = catmull_rom_sample([start, end], leds)

    # Apply fixture rotation override (e.g., child says "east" but fixture is vertical)
    if rotation and any(r != 0 for r in rotation):
        pixels = [_rotate_vec(p, child_pos, rotation) for p in pixels]

    return pixels


def resolve_fixture(fixture):
    """Resolve pixel positions for any fixture type.

    Args:
        fixture: dict with type, childPos, strings (for linear), aoeRadius (for point)

    Returns:
        dict with pixelPositions: list of [x,y,z] in mm
    """
    ftype = fixture.get("type", "linear")
    child_pos = fixture.get("childPos", [0, 0, 0])

    if ftype == "point":
        # Point fixture: single position
        return {"pixelPositions": [list(child_pos)]}

    if ftype == "surface":
        # Surface: no pixels to resolve (future: UV sampling)
        return {"pixelPositions": []}

    if ftype == "group":
        # Group: no direct pixels (resolved via member fixtures)
        return {"pixelPositions": []}

    # Linear: resolve each string, applying fixture rotation override
    rotation = fixture.get("rotation", [0, 0, 0])
    all_pixels = []
    for s in fixture.get("strings", []):
        points = s.get("points")  # custom control points
        pixels = resolve_linear_fixture(child_pos, s, points, rotation)
        all_pixels.extend(pixels)

    return {"pixelPositions": all_pixels}


# ── Phase 3: Intersector (Spatial Effect Fields) ────────────────────────────

def _dist3(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx*dx + dy*dy + dz*dz)

def _lerp3(a, b, t):
    return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t]

def _ease(t, easing):
    """Apply easing function to t in [0,1]."""
    t = max(0.0, min(1.0, t))
    if easing == "ease-in":
        return t * t
    if easing == "ease-out":
        return t * (2 - t)
    if easing == "ease-in-out":
        return t * t * (3 - 2 * t)  # smoothstep
    return t  # linear

def _blend_color(base, effect, mode):
    """Blend effect color onto base color using blend mode. All values 0-255."""
    if mode == "replace":
        return list(effect)
    if mode == "add":
        return [min(255, base[i] + effect[i]) for i in range(3)]
    if mode == "multiply":
        return [base[i] * effect[i] // 255 for i in range(3)]
    if mode == "screen":
        return [255 - (255 - base[i]) * (255 - effect[i]) // 255 for i in range(3)]
    return list(effect)


def sphere_field_evaluate(center, radius, pixel_positions, color, blend="replace", falloff=True):
    """Evaluate a sphere field: pixels inside the sphere get colored.

    Args:
        center: [x, y, z] center of sphere (mm)
        radius: sphere radius (mm)
        pixel_positions: list of [x, y, z]
        color: [r, g, b] 0-255
        blend: "replace", "add", "multiply", "screen"
        falloff: if True, intensity decreases toward edge

    Returns:
        list of [r, g, b] per pixel (0 if outside)
    """
    result = []
    radius_sq = radius * radius
    for px in pixel_positions:
        dist_sq = sum((px[i] - center[i])**2 for i in range(3))
        if dist_sq <= radius_sq:
            dist = math.sqrt(dist_sq)  # only sqrt when inside
            intensity = 1.0 - (dist / radius) if falloff and radius > 0 else 1.0
            c = [int(color[i] * intensity) for i in range(3)]
            result.append(c)
        else:
            result.append([0, 0, 0])
    return result


def plane_field_evaluate(normal, offset, thickness, pixel_positions, color, blend="replace"):
    """Evaluate a plane field: pixels within thickness of the plane get colored.

    Args:
        normal: [nx, ny, nz] unit normal vector
        offset: signed distance from origin along normal (mm)
        thickness: half-thickness of the slab (mm)
        pixel_positions: list of [x, y, z]
        color: [r, g, b] 0-255

    Returns:
        list of [r, g, b] per pixel
    """
    # Normalize normal vector
    mag = math.sqrt(sum(n*n for n in normal)) or 1.0
    n = [normal[i] / mag for i in range(3)]

    result = []
    for px in pixel_positions:
        # Signed distance from plane
        d = sum(px[i] * n[i] for i in range(3)) - offset
        if abs(d) <= thickness:
            intensity = 1.0 - abs(d) / thickness if thickness > 0 else 1.0
            c = [int(color[i] * intensity) for i in range(3)]
            result.append(c)
        else:
            result.append([0, 0, 0])
    return result


def box_field_evaluate(min_corner, max_corner, pixel_positions, color, blend="replace"):
    """Evaluate a box field (AABB): pixels inside the box get colored.

    Args:
        min_corner: [x, y, z] minimum corner (mm)
        max_corner: [x, y, z] maximum corner (mm)
        pixel_positions: list of [x, y, z]
        color: [r, g, b] 0-255

    Returns:
        list of [r, g, b] per pixel
    """
    result = []
    for px in pixel_positions:
        inside = all(min_corner[i] <= px[i] <= max_corner[i] for i in range(3))
        result.append(list(color) if inside else [0, 0, 0])
    return result


def evaluate_spatial_effect(effect, pixel_positions, t):
    """Evaluate a spatial effect at time t.

    Args:
        effect: dict with shape, color, size, motion, blend, etc.
        pixel_positions: list of [x,y,z] in mm
        t: time in seconds since effect start

    Returns:
        list of [r, g, b] per pixel
    """
    if not pixel_positions:
        return []

    shape = effect.get("shape", "sphere")
    color = [effect.get("r", 255), effect.get("g", 255), effect.get("b", 255)]
    blend = effect.get("blend", "replace")
    motion = effect.get("motion", {})
    duration = motion.get("durationS", 1) or 1
    easing = motion.get("easing", "linear")

    # Compute field position from motion path
    start_pos = motion.get("startPos", [0, 0, 0])
    end_pos = motion.get("endPos", [0, 0, 0])
    progress = _ease(min(t / duration, 1.0), easing)
    pos = _lerp3(start_pos, end_pos, progress)

    size = effect.get("size", {})

    if shape == "sphere":
        radius = size.get("radius", 1000)  # mm
        return sphere_field_evaluate(pos, radius, pixel_positions, color, blend)

    elif shape == "plane":
        normal = size.get("normal", [0, 1, 0])
        thickness = size.get("thickness", 200)  # mm
        # Offset = dot(pos, normal) — plane moves with motion
        mag = math.sqrt(sum(n*n for n in normal)) or 1.0
        n = [normal[i] / mag for i in range(3)]
        offset = sum(pos[i] * n[i] for i in range(3))
        return plane_field_evaluate(n, offset, thickness, pixel_positions, color, blend)

    elif shape == "box":
        w = size.get("width", 1000)
        h = size.get("height", 1000)
        d = size.get("depth", 1000)
        # Stage: X=width, Y=depth, Z=height (#385)
        min_c = [pos[0] - w/2, pos[1] - d/2, pos[2] - h/2]
        max_c = [pos[0] + w/2, pos[1] + d/2, pos[2] + h/2]
        return box_field_evaluate(min_c, max_c, pixel_positions, color, blend)

    return [[0, 0, 0]] * len(pixel_positions)


def compute_pan_tilt(fixture_pos, aim_point, pan_range_deg, tilt_range_deg,
                     mounted_inverted=False, pan_offset=None):
    """Compute normalized pan/tilt (0.0-1.0) from fixture position to aim point.

    Stage coordinates: X=width, Y=depth (forward toward audience), Z=height.
    Convention: pan=0.5 = forward (+Y), tilt=0.5 = horizontal.

    Args:
        pan_offset: normalized offset for pan home direction (#365).
            0.5 = 180° flip (inverted ceiling mounts face backward at pan=0.5).
            If None, defaults to 0.5 when mounted_inverted, else 0.0.

    Returns:
        (pan_normalized, tilt_normalized) both 0.0-1.0, or None if ranges are 0
    """
    if pan_range_deg <= 0 or tilt_range_deg <= 0:
        return None

    dx = aim_point[0] - fixture_pos[0]
    dy = aim_point[1] - fixture_pos[1]
    dz = aim_point[2] - fixture_pos[2]

    dist_xy = math.sqrt(dx * dx + dy * dy)

    pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(abs(dz), dist_xy)) if (dist_xy > 0.001 or abs(dz) > 0.001) else 0.0
    if dz > 0:
        tilt_deg = -tilt_deg

    if pan_offset is None:
        # Inverted ceiling mounts face backward — 180° pan flip
        pan_offset = 180.0 if mounted_inverted else 0.0
    pan_norm = 0.5 + (pan_deg + pan_offset) / pan_range_deg
    tilt_norm = 0.5 + tilt_deg / tilt_range_deg

    return (max(0.0, min(1.0, pan_norm)), max(0.0, min(1.0, tilt_norm)))


def effect_aim_point(effect, t):
    """Return the center position of a spatial effect field at time t.
    This is where a moving head should aim."""
    motion = effect.get("motion", {})
    start_pos = motion.get("startPos", [0, 0, 0])
    end_pos = motion.get("endPos", [0, 0, 0])
    duration = motion.get("durationS", 1) or 1
    easing = motion.get("easing", "linear")
    progress = _ease(min(t / duration, 1.0), easing)
    return _lerp3(start_pos, end_pos, progress)


def blend_pixel_layers(layers, modes=None):
    """Blend multiple pixel color layers together.

    Args:
        layers: list of pixel arrays, each is list of [r,g,b]
        modes: list of blend modes per layer (default "replace")

    Returns:
        blended pixel array
    """
    if not layers:
        return []
    if len(layers) == 1:
        return layers[0]

    n_pixels = len(layers[0])
    result = [[0, 0, 0]] * n_pixels

    for li, layer in enumerate(layers):
        mode = (modes[li] if modes and li < len(modes) else "add")
        for pi in range(min(n_pixels, len(layer))):
            if any(layer[pi][c] > 0 for c in range(3)):
                result[pi] = _blend_color(result[pi], layer[pi], mode)

    return result


# ── Phase 4: Capability-layer primitive ──────────────────────────────────────
#
# The evaluator below is the single entry point for every spatial-effect
# consumer: LED strings (bake path), DMX movers (bake path), and future
# runtime consumers (Track actions, MoverControlEngine). It replaces the
# per-shape duplication that existed in bake_engine (_compile_sphere_sweep /
# _compile_plane_sweep / _compile_box / _compile_dmx_fixture).
#
# See docs/mover-alignment-review.md §8.1b Q8–Q11 for the architectural
# decisions.

# Capability tags — fixed vocabulary consumed by evaluate_primitive's caller
# to decide which primitives to read out of the bundle. A fixture's caps[]
# list is auto-derived from its profile by derive_caps(); no schema migration.
CAP_COLOR_RGB     = "color.rgb"
CAP_COLOR_WHITE   = "color.white"
CAP_COLOR_WHEEL   = "color.wheel"
CAP_INTENSITY     = "intensity.dimmer"
CAP_DIRECTION     = "direction.pan-tilt"
CAP_STROBE        = "strobe"
CAP_BEAM_ZOOM     = "beam.zoom"
CAP_BEAM_FOCUS    = "beam.focus"


@dataclass
class PrimitiveOutputs:
    """Bundle of primitive outputs returned by evaluate_primitive.

    A consumer reads only the fields its fixture's caps[] declares.
    Unread fields are ignored — zero cost.

    Fields:
        color: (r, g, b) each in [0, 255]. Emissive primitive.
        intensity: 0.0–1.0 dimmer value. 1.0 when the effect field covers
            the fixture, 0.0 when out of range.
        aim: (x, y, z) stage-mm target point, or None if the effect has no
            directional component (e.g. a box field that isn't moving).
            Consumed by fixtures with CAP_DIRECTION.
        beam_width: degrees of beam cone, or None. Consumed by CAP_BEAM_ZOOM.
    """
    color: tuple       # (r, g, b), ints 0-255
    intensity: float   # 0.0–1.0
    aim: tuple = None  # (x, y, z) stage mm, or None
    beam_width: float = None  # degrees, or None


def evaluate_primitive(fixture_pose, effect, t):
    """Evaluate a spatial effect at a fixture's stage-mm pose and time t.

    Returns a PrimitiveOutputs bundle. Every consumer — LED strings, DMX
    movers, floods — calls the same evaluator; the caller reads only the
    primitives its caps[] declares.

    Args:
        fixture_pose: [x, y, z] in stage mm. For LED strings this is per-LED;
            for DMX fixtures it's typically the fixture position (or a beam-
            cone sample point — caller's choice).
        effect: effect dict (shape, color, size, motion, ...).
        t: time in seconds since effect start.

    Returns:
        PrimitiveOutputs bundle. `color` is always populated; `aim` is the
        effect's current centre (where a mover participating in the effect
        should point); `intensity` is 1.0 when the field covers the pose,
        0.0 otherwise.
    """
    if effect is None:
        return PrimitiveOutputs(color=(0, 0, 0), intensity=0.0, aim=None)

    # Evaluate colour at this single pose by reusing the existing field
    # evaluators — keeps one source of truth for the field geometry.
    colors = evaluate_spatial_effect(effect, [list(fixture_pose)], t)
    c = colors[0] if colors else [0, 0, 0]
    color_tuple = (int(c[0]), int(c[1]), int(c[2]))

    # Intensity: 1.0 when any channel is lit, 0.0 otherwise. Shape fields
    # already do the distance-falloff; we surface the binary covered/not
    # state here so dimmer-only fixtures (pars, washes without RGB) can
    # participate.
    intensity = 1.0 if any(v > 0 for v in color_tuple) else 0.0

    # Aim: where a direction-capable fixture (mover) should point to
    # participate in this effect. Re-uses effect_aim_point which returns
    # the interpolated motion-path centre at time t.
    aim_point = effect_aim_point(effect, t)
    # Tuple for hashability / immutability
    aim = (aim_point[0], aim_point[1], aim_point[2]) if aim_point else None

    return PrimitiveOutputs(
        color=color_tuple,
        intensity=intensity,
        aim=aim,
        beam_width=None,
    )


def shape_coverage_time(effect, pixel_pose, samples=101):
    """Effect-local time window during which the field covers a pixel.

    Generic over shape — works for sphere, plane, box, and any future
    field by sampling the intensity channel of the evaluator. Replaces
    the per-shape intersect functions (_sphere_intersection_time and
    the inline plane/box versions) in bake_engine.py.

    Args:
        effect: effect dict (shape, color, size, motion).
        pixel_pose: [x, y, z] stage mm — the point to test.
        samples: number of time samples across the effect duration.
            101 gives ~10 ms resolution for a 1 s effect; scale up for
            longer effects if tighter edge accuracy is needed.

    Returns:
        (t_enter, t_peak, t_exit) in effect-local seconds, or None if
        the field never covers the pixel. t_peak is the midpoint of
        the coverage window — good enough for sweep-direction detection
        and DMX-bake hint timing.
    """
    if effect is None:
        return None

    fx_dur = (effect.get("motion") or {}).get("durationS", 1) or 1
    t_enter = None
    t_exit = None

    for i in range(samples):
        t = (i / max(samples - 1, 1)) * fx_dur
        out = evaluate_primitive(pixel_pose, effect, t)
        if out.intensity > 0:
            if t_enter is None:
                t_enter = t
            t_exit = t

    if t_enter is None:
        return None

    t_peak = (t_enter + t_exit) / 2.0
    return (t_enter, t_peak, t_exit)


def derive_caps(profile):
    """Auto-derive the capability set of a DMX profile.

    Scans the profile's channel list and returns the set of capability
    tags the fixture can consume. A consumer iterates `caps` to decide
    which primitives of a PrimitiveOutputs bundle to read.

    Args:
        profile: DMX profile dict (from dmx_profiles.py). Expects
            `channels[]` with per-channel `type` and `capabilities[]`
            plus optional `panRange` / `tiltRange` / `beamWidth`.

    Returns:
        list of capability tag strings (CAP_* constants). Sorted,
        deduplicated. Empty list for a non-DMX fixture (caller passes
        None).
    """
    if not profile:
        return []

    caps = set()
    channels = profile.get("channels") or []

    # Channel-type heuristics
    has_r = any(ch.get("type") == "red" for ch in channels)
    has_g = any(ch.get("type") == "green" for ch in channels)
    has_b = any(ch.get("type") == "blue" for ch in channels)
    if has_r and has_g and has_b:
        caps.add(CAP_COLOR_RGB)
    if any(ch.get("type") == "white" for ch in channels):
        caps.add(CAP_COLOR_WHITE)
    if any(ch.get("type") == "dimmer" for ch in channels):
        caps.add(CAP_INTENSITY)
    if any(ch.get("type") == "strobe" for ch in channels):
        caps.add(CAP_STROBE)
    if any(ch.get("type") in ("zoom",) for ch in channels):
        caps.add(CAP_BEAM_ZOOM)
    if any(ch.get("type") in ("focus",) for ch in channels):
        caps.add(CAP_BEAM_FOCUS)

    # Pan/tilt: either named channels or profile-level ranges
    has_pan = any(ch.get("type") == "pan" for ch in channels)
    has_tilt = any(ch.get("type") == "tilt" for ch in channels)
    if has_pan and has_tilt:
        caps.add(CAP_DIRECTION)
    elif (profile.get("panRange") or 0) > 0 and (profile.get("tiltRange") or 0) > 0:
        caps.add(CAP_DIRECTION)

    # Colour wheel: any channel whose capabilities[] contains a WheelSlot
    # with colour annotation. Re-checks the GDTF-style nested capabilities.
    for ch in channels:
        for cap in (ch.get("capabilities") or []):
            if cap.get("type") == "WheelSlot" and (cap.get("color") or cap.get("colorHex")):
                caps.add(CAP_COLOR_WHEEL)
                break

    return sorted(caps)
