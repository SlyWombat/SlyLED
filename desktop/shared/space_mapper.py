"""
space_mapper.py — Environment point cloud from multiple cameras.

Collects per-camera point clouds, transforms to stage coordinates
using camera fixture positions from the layout, merges into a single cloud.
"""

import json
import logging
import math
import threading
import time
import urllib.request

log = logging.getLogger("slyled")


def fetch_point_cloud(camera_fixture, max_points=10000, max_depth_mm=5000):
    """Fetch a point cloud using two strategies:

    1. Try the camera node's /point-cloud endpoint (works on Orange Pi).
    2. If that fails (503/timeout), fetch a snapshot and run depth estimation
       locally on the orchestrator (requires cv2 + depth model).

    Args:
        camera_fixture: dict with cameraIp, cameraIdx, fovDeg, etc.
        max_points: maximum number of points to return
        max_depth_mm: maximum depth in mm

    Returns:
        list of [x, y, z, r, g, b] in camera-local coords, or None.
    """
    ip = camera_fixture.get("cameraIp")
    cam_idx = camera_fixture.get("cameraIdx", 0)
    if not ip:
        return None

    # Strategy 1: Try camera's /point-cloud endpoint
    try:
        req = urllib.request.Request(
            f"http://{ip}:5000/point-cloud",
            data=json.dumps({"cam": cam_idx, "maxPoints": max_points,
                              "maxDepthMm": max_depth_mm}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=60)
        r = json.loads(resp.read().decode())
        if r.get("ok"):
            pts = r.get("points", [])
            if pts:
                return pts
    except Exception as e:
        log.info("Camera /point-cloud failed for %s cam%d: %s — trying orchestrator-side depth",
                 ip, cam_idx, e)

    # Strategy 2: Fetch snapshot + run depth estimation locally via CVEngine (#333)
    try:
        from cv_engine import CVEngine
        cv = CVEngine()
        frame = cv.fetch_snapshot(ip, cam_idx, timeout=15)
        fov = camera_fixture.get("fovDeg", 60)
        intrinsics = None  # Use FOV-based estimate
        points, _ms = cv.generate_point_cloud(
            frame, fov, max_points=max_points,
            max_depth_mm=max_depth_mm, intrinsics=intrinsics)
        log.info("Orchestrator-side depth for %s cam%d: %d points in %dms",
                 ip, cam_idx, len(points), _ms)
        return points if points else None
    except Exception as e:
        log.warning("Orchestrator-side depth failed for %s cam%d: %s", ip, cam_idx, e)

    return None


def anchor_depth_scale(cam_local_points, cam_pos, cam_rotation, stage_dims,
                        min_samples=50, rms_threshold_mm=2000.0):
    """Fit a 2-parameter (scale, offset) correction on a monocular point
    cloud so that its depths agree with the known stage geometry (#581).

    For each cam-local point we build its pinhole ray, project that ray
    into stage space using the camera pose, and compute the parameter
    `t_true` at which it first intersects a stage-bounding surface
    (floor Z=0, walls, ceiling). Then `d_raw = z_cam` is the raw monocular
    depth. Solving `S * d_raw + B ≈ t_true` via least-squares gives the
    scale/offset that aligns the whole cloud to the surveyed box.

    Only points whose rays actually strike a stage surface (positive t
    toward a bounded face) contribute to the fit. Outliers beyond 2σ of
    the initial fit are discarded and the fit is re-run once.

    Args:
        cam_local_points: list of [x, y, z, ...] in pinhole cam-local mm
        cam_pos: (x, y, z) stage mm
        cam_rotation: [tilt, pan, roll] degrees — see camera_math
        stage_dims: dict with 'w', 'd', 'h' in mm (stage width / depth / height)
        min_samples: don't attempt fit with fewer ray-surface hits
        rms_threshold_mm: reject fits with RMS error above this (return
            None → caller keeps the raw cloud untouched)

    Returns:
        dict with keys `scale`, `offset`, `rmsErrorMm`, `samplesUsed`,
        or None if no reliable fit could be produced.
    """
    from camera_math import build_camera_to_stage, rotation_from_layout

    tilt, pan, roll = rotation_from_layout(cam_rotation)
    R = build_camera_to_stage(tilt, pan, roll)
    cx, cy, cz = cam_pos
    sw = float(stage_dims.get("w", 0) * 1000) if stage_dims.get("w", 0) < 100 else float(stage_dims.get("w", 0))
    sd = float(stage_dims.get("d", 0) * 1000) if stage_dims.get("d", 0) < 100 else float(stage_dims.get("d", 0))
    sh = float(stage_dims.get("h", 0) * 1000) if stage_dims.get("h", 0) < 100 else float(stage_dims.get("h", 0))
    if sw <= 0 or sd <= 0 or sh <= 0:
        return None

    samples = []  # list of (d_raw, t_true)
    # Expand stage bounds slightly so rays that clip the edges of the box
    # still find a surface. 10% margin.
    margin = 0.1
    x_lo, x_hi = -sw * margin, sw * (1 + margin)
    y_lo, y_hi = -sd * margin, sd * (1 + margin)
    z_lo, z_hi = -sh * margin, sh * (1 + margin)

    for pt in cam_local_points:
        x, y, z = pt[0], pt[1], pt[2]
        if not math.isfinite(z) or z <= 50:
            continue
        # Reject rays with extreme angular deviation. Monocular depth
        # estimators sometimes emit points whose pinhole ratio |x/z| or
        # |y/z| is absurdly large (image-edge artefacts). Those rays
        # pass through the stage bounding box at grazing angles, making
        # t_true very sensitive to the expanded-margin cutoffs. Keep
        # only rays within a reasonable FOV cone (±60° each axis).
        if abs(x) > z * 1.732 or abs(y) > z * 1.732:
            continue
        dir_cam = [x / z, y / z, 1.0]
        dx = R[0][0] * dir_cam[0] + R[0][1] * dir_cam[1] + R[0][2] * dir_cam[2]
        dy = R[1][0] * dir_cam[0] + R[1][1] * dir_cam[1] + R[1][2] * dir_cam[2]
        dz = R[2][0] * dir_cam[0] + R[2][1] * dir_cam[1] + R[2][2] * dir_cam[2]

        candidates = []
        eps = 1e-6
        # For each face, include the intersection only if the point
        # where the ray hits the infinite plane falls inside the
        # (margin-expanded) bounded face. This prevents e.g. a ray
        # pointed forward-and-up from being scored against "the floor"
        # just because dz is slightly negative.
        if dz < -eps:
            t = -cz / dz
            if t > 0:
                hx = cx + t * dx; hy = cy + t * dy
                if x_lo <= hx <= x_hi and y_lo <= hy <= y_hi:
                    candidates.append(t)
        if dz > eps:
            t = (sh - cz) / dz
            if t > 0:
                hx = cx + t * dx; hy = cy + t * dy
                if x_lo <= hx <= x_hi and y_lo <= hy <= y_hi:
                    candidates.append(t)
        if dy > eps:
            t = (sd - cy) / dy
            if t > 0:
                hx = cx + t * dx; hz = cz + t * dz
                if x_lo <= hx <= x_hi and z_lo <= hz <= z_hi:
                    candidates.append(t)
        if dy < -eps:
            t = -cy / dy
            if t > 0:
                hx = cx + t * dx; hz = cz + t * dz
                if x_lo <= hx <= x_hi and z_lo <= hz <= z_hi:
                    candidates.append(t)
        if dx > eps:
            t = (sw - cx) / dx
            if t > 0:
                hy = cy + t * dy; hz = cz + t * dz
                if y_lo <= hy <= y_hi and z_lo <= hz <= z_hi:
                    candidates.append(t)
        if dx < -eps:
            t = -cx / dx
            if t > 0:
                hy = cy + t * dy; hz = cz + t * dz
                if y_lo <= hy <= y_hi and z_lo <= hz <= z_hi:
                    candidates.append(t)
        if not candidates:
            continue
        t_true = min(candidates)
        samples.append((z, t_true))

    if len(samples) < min_samples:
        return None

    # Closed-form LSQ for d_true = S * d_raw + B
    def _fit(pairs):
        n = len(pairs)
        sx = sum(p[0] for p in pairs)
        sy = sum(p[1] for p in pairs)
        sxx = sum(p[0] * p[0] for p in pairs)
        sxy = sum(p[0] * p[1] for p in pairs)
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-9:
            return None
        S = (n * sxy - sx * sy) / denom
        B = (sy - S * sx) / n
        residuals = [(S * p[0] + B - p[1]) for p in pairs]
        rms = math.sqrt(sum(r * r for r in residuals) / n)
        return S, B, rms, residuals

    fit = _fit(samples)
    if fit is None:
        return None
    S, B, rms, residuals = fit

    # One round of outlier rejection: drop points >2σ from the fit
    if len(samples) >= 2 * min_samples:
        sigma = rms
        clean = [s for s, r in zip(samples, residuals) if abs(r) <= 2 * sigma]
        if len(clean) >= min_samples:
            fit2 = _fit(clean)
            if fit2:
                S, B, rms, _ = fit2
                samples = clean

    if rms > rms_threshold_mm:
        log.warning("Depth anchor fit RMS %.0f mm exceeds threshold %.0f mm — "
                    "using fallback", rms, rms_threshold_mm)
        return _fallback_anchor(cam_local_points, cam_pos, cam_rotation, stage_dims)
    # Sanity: a negative scale would invert the cloud through the camera,
    # which is never physically meaningful. Usually indicates that the
    # monocular depth map and the stage bounding box disagree severely
    # (saturated DMX hotspots, wrong rotation, untextured regions).
    if S <= 0:
        log.warning("Depth anchor fit produced negative scale %.3f — "
                    "using fallback (see #590)", S)
        return _fallback_anchor(cam_local_points, cam_pos, cam_rotation, stage_dims)

    return {"scale": S, "offset": B, "rmsErrorMm": rms,
            "samplesUsed": len(samples), "quality": "ok"}


def _fallback_anchor(cam_local_points, cam_pos, cam_rotation, stage_dims):
    """Produce a coarse anchor fit when the LSQ fit can't converge (#590).

    The median of the raw cam-local depths is mapped to the median of
    the per-ray geometric expected depths. Scale = ratio of those
    medians, offset = 0. Much less precise than the LSQ fit, but keeps
    the camera's cloud at a reasonable order-of-magnitude so the cross-
    camera filter isn't seeing completely unscaled disparity.

    Returns the same shape dict as `anchor_depth_scale` but tagged
    with `quality: "fallback"` so downstream consumers can weight it.
    Returns None if even the median can't be computed (too few points).
    """
    if not cam_local_points:
        return None
    from camera_math import build_camera_to_stage, rotation_from_layout
    tilt, pan, roll = rotation_from_layout(cam_rotation)
    R = build_camera_to_stage(tilt, pan, roll)
    cx, cy, cz = cam_pos
    sw = float(stage_dims.get("w", 0) * 1000) if stage_dims.get("w", 0) < 100 else float(stage_dims.get("w", 0))
    sd = float(stage_dims.get("d", 0) * 1000) if stage_dims.get("d", 0) < 100 else float(stage_dims.get("d", 0))
    sh = float(stage_dims.get("h", 0) * 1000) if stage_dims.get("h", 0) < 100 else float(stage_dims.get("h", 0))
    if sw <= 0 or sd <= 0 or sh <= 0:
        return None
    raws = []
    trues = []
    for pt in cam_local_points:
        x, y, z = pt[0], pt[1], pt[2]
        if not math.isfinite(z) or z <= 50:
            continue
        if abs(x) > z * 1.732 or abs(y) > z * 1.732:
            continue
        dc = [x / z, y / z, 1.0]
        dx = R[0][0] * dc[0] + R[0][1] * dc[1] + R[0][2] * dc[2]
        dy = R[1][0] * dc[0] + R[1][1] * dc[1] + R[1][2] * dc[2]
        dz = R[2][0] * dc[0] + R[2][1] * dc[1] + R[2][2] * dc[2]
        cands = []
        eps = 1e-6
        if dz < -eps and -cz/dz > 0: cands.append(-cz/dz)
        if dz > eps and (sh-cz)/dz > 0: cands.append((sh-cz)/dz)
        if dy > eps and (sd-cy)/dy > 0: cands.append((sd-cy)/dy)
        if dy < -eps and -cy/dy > 0: cands.append(-cy/dy)
        if dx > eps and (sw-cx)/dx > 0: cands.append((sw-cx)/dx)
        if dx < -eps and -cx/dx > 0: cands.append(-cx/dx)
        if not cands:
            continue
        raws.append(z)
        trues.append(min(cands))
    if len(raws) < 10:
        return None
    import statistics
    m_raw = statistics.median(raws)
    m_true = statistics.median(trues)
    if m_raw < 1:
        return None
    S = m_true / m_raw
    B = 0.0
    log.info("Fallback anchor: median raw=%.0f, median true=%.0f → scale=%.3f",
             m_raw, m_true, S)
    return {"scale": S, "offset": B, "rmsErrorMm": None,
            "samplesUsed": len(raws), "quality": "fallback"}


def apply_depth_correction(cam_local_points, scale, offset):
    """Apply a scale+offset correction along each point's ray.

    Since the cam-local (x, y, z) coords come from `x = (px-cx)*z/fx`,
    scaling z by k requires scaling x and y by the same k so the ray
    direction stays constant. With scale S and offset B:
        t_new = S * z + B
        x_new = x * t_new / z
        y_new = y * t_new / z
        z_new = t_new

    Returns a new list; colour and confidence slots are preserved.
    """
    corrected = []
    for pt in cam_local_points:
        x, y, z = pt[0], pt[1], pt[2]
        if z <= 0 or not math.isfinite(z):
            continue
        t_new = scale * z + offset
        if t_new <= 0:
            # Correction would put the point behind the camera — drop it.
            continue
        k = t_new / z
        out = [x * k, y * k, t_new] + list(pt[3:])
        corrected.append(out)
    return corrected


def transform_points(points, cam_pos, cam_rotation, cam_aim=None):
    """Transform camera-local points to stage coordinates.

    Uses the canonical `camera_math.build_camera_to_stage` helper so the
    sign convention matches stereo_engine, bake_engine._rotation_to_aim
    and the fixture editor UI (#586).

    Args:
        points: list of [x, y, z, r, g, b] in camera-local pinhole coords
        cam_pos: (x, y, z) camera position in stage mm
        cam_rotation: (tilt, pan, roll) degrees — see camera_math
        cam_aim: optional (x, y, z) aim point in stage mm — when set,
                 tilt and pan are derived from this vector instead of
                 cam_rotation (roll falls back to rotation[2] or 0)

    Returns: list of [x, y, z, r, g, b] in stage mm (Z-up)
    """
    from camera_math import build_camera_to_stage, rotation_from_layout

    cx, cy, cz = cam_pos

    if cam_aim:
        dx = cam_aim[0] - cx
        dy = cam_aim[1] - cy
        dz = cam_aim[2] - cz
        dist_xy = math.sqrt(dx * dx + dy * dy)
        # Derive tilt and pan from the aim vector using the documented
        # convention: pan positive = aim toward +X, tilt positive = down.
        tilt = math.degrees(math.atan2(-dz, dist_xy))
        pan = math.degrees(math.atan2(dx, dy))
        roll = rotation_from_layout(cam_rotation)[2]
    else:
        tilt, pan, roll = rotation_from_layout(cam_rotation)

    R = build_camera_to_stage(tilt, pan, roll)
    # R is a numpy array when numpy is available (it is here, as a hard
    # dep via cv_engine). Index as 2-D.
    r00, r01, r02 = R[0][0], R[0][1], R[0][2]
    r10, r11, r12 = R[1][0], R[1][1], R[1][2]
    r20, r21, r22 = R[2][0], R[2][1], R[2][2]

    result = []
    for pt in points:
        x, y, z = pt[0], pt[1], pt[2]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        wx = r00 * x + r01 * y + r02 * z + cx
        wy = r10 * x + r11 * y + r12 * z + cy
        wz = r20 * x + r21 * y + r22 * z + cz
        # Preserve the optional 7th slot (confidence, #587). v1 six-element
        # points pass through unchanged.
        out = [wx, wy, wz, pt[3], pt[4], pt[5]]
        if len(pt) > 6:
            out.append(pt[6])
        result.append(out)
    return result


class SpaceScan:
    """Async environment scan — collects point clouds from all cameras."""

    def __init__(self):
        self._running = False
        self._progress = 0
        self._message = ""
        self._result = None
        self._thread = None

    @property
    def running(self):
        return self._running

    @property
    def status(self):
        return {
            "running": self._running,
            "progress": self._progress,
            "message": self._message,
            "result": self._result,
        }

    def start(self, camera_fixtures, layout_positions, max_points_per_cam=10000,
              stage_dims=None):
        """Start an async environment scan.

        Args:
            camera_fixtures: list of camera fixture dicts (with cameraIp, cameraIdx, rotation)
            layout_positions: dict of {fixture_id: {x, y, z}} from layout
            stage_dims: optional dict {w, d, h} in mm for depth anchoring
                (#581). Without it, per-camera depth correction is skipped
                and the caller is expected to supply geometry separately.
        """
        if self._running:
            return
        self._running = True
        self._progress = 0
        self._message = "Starting scan..."
        self._result = None
        self._thread = threading.Thread(
            target=self._scan, daemon=True,
            args=(camera_fixtures, layout_positions, max_points_per_cam, stage_dims))
        self._thread.start()

    def _scan(self, cameras, positions, max_points, stage_dims=None):
        all_points = []
        cam_info = []
        per_cam_clouds = []  # for #582 cross-camera consistency filter
        total = len(cameras)

        for i, cam in enumerate(cameras):
            fid = cam.get("id")
            ip = cam.get("cameraIp")
            cam_idx = cam.get("cameraIdx", 0)
            self._progress = int((i / total) * 90)
            self._message = f"Scanning camera {i+1}/{total}: {cam.get('name', ip)} (cam{cam_idx})"
            log.info("Space scan: %s", self._message)

            if not ip:
                continue

            # Fetch point cloud (tries camera endpoint, then orchestrator-side depth)
            points = fetch_point_cloud(cam, max_points)
            if not points:
                log.warning("No points from %s cam%d", ip, cam_idx)
                continue

            pos = positions.get(fid, positions.get(str(fid), {}))
            cam_pos = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
            cam_rot = cam.get("rotation", [0, 0, 0])

            # #581 Phase 1 — anchor monocular depth to surveyed geometry.
            # The camera endpoint returns relative depth scaled by a FOV
            # guess that is typically off by hundreds of mm. Fitting a
            # per-camera (scale, offset) against the stage bounding box
            # moves the cloud into true metric coords before transform.
            anchor = None
            if stage_dims:
                anchor = anchor_depth_scale(points, cam_pos, cam_rot, stage_dims)
                if anchor:
                    log.info("Depth anchor cam%d: scale=%.3f offset=%.0fmm "
                             "rms=%.0fmm n=%d",
                             cam_idx, anchor["scale"], anchor["offset"],
                             anchor["rmsErrorMm"], anchor["samplesUsed"])
                    points = apply_depth_correction(
                        points, anchor["scale"], anchor["offset"])

            stage_points = transform_points(points, cam_pos, cam_rot)

            # #590 — pass the anchor quality into the cross-cam filter
            # so a failed / fallback camera can't veto a well-anchored
            # camera's observations. `quality` is set by anchor_depth_scale:
            #   "ok"       — LSQ fit succeeded, camera is a full voter
            #   "fallback" — median-based coarse fit, camera votes with
            #                relaxed expectations (still participates)
            #   "failed"   — anchor attempted but produced no usable fit;
            #                cloud is at raw disparity scale; camera does
            #                NOT get to veto, and its points come out as
            #                singleCam confidence
            #   None       — no anchor attempted (e.g. stage_dims absent);
            #                camera participates normally
            if anchor is not None:
                quality = anchor.get("quality", "ok")
            elif stage_dims:
                # stage_dims given but anchor returned None — the fit
                # (including the fallback) couldn't produce anything.
                quality = "failed"
            else:
                quality = None
            per_cam_clouds.append({
                "fixture": cam,
                "stage_pos": cam_pos,
                "fov_deg": cam.get("fovDeg", 60),
                "points": stage_points,
                "anchorQuality": quality,
            })
            cam_info.append({
                "fixtureId": fid,
                "cameraIdx": cam_idx,
                "name": cam.get("name", ""),
                "pointCount": len(stage_points),
                "depthAnchor": anchor,
                "anchorQuality": quality,
            })

        # #582 — cross-camera consistency filter. When ≥2 cameras are
        # present, dropping monocular hallucinations here adds a
        # confidence slot and typically rejects 20-30% of noise.
        filter_stats = None
        if len(per_cam_clouds) >= 2:
            try:
                from stereo_consistency import cross_camera_filter
                all_points, filter_stats = cross_camera_filter(per_cam_clouds)
                log.info("Cross-cam filter: %s", filter_stats)
            except Exception as e:
                log.warning("Cross-cam filter failed (%s) — merging without", e)
                for c in per_cam_clouds:
                    all_points.extend(c["points"])
        else:
            for c in per_cam_clouds:
                all_points.extend(c["points"])

        self._progress = 90
        self._message = f"Normalizing {len(all_points)} points to stage floor..."

        # Floor normalization — camera-anchored. Each camera's layout
        # position has a known Z (height above floor, surveyed by the
        # operator). After transform_points, a point directly below each
        # camera that lies on the floor should land at stage Z=0. In
        # practice depth noise + pitch error pushes the "floor" up or
        # down by tens to hundreds of mm. Shift the entire cloud so the
        # median observed floor (5th-percentile Z per camera, averaged)
        # lands at Z=0. This uses the operator's known geometry as the
        # anchor rather than trusting RANSAC alone.
        floor_z = None
        if len(all_points) > 100:
            try:
                per_cam_floors = []
                for cam in cameras:
                    fid = cam.get("id")
                    pos = positions.get(fid, positions.get(str(fid), {}))
                    cx, cy = pos.get("x", 0), pos.get("y", 0)
                    # Points within ±1 m of this camera's XY footprint
                    below = [p[2] for p in all_points
                             if abs(p[0] - cx) < 1000 and abs(p[1] - cy) < 1000]
                    if len(below) >= 10:
                        below.sort()
                        per_cam_floors.append(below[len(below) // 20])  # 5th %ile
                if per_cam_floors:
                    floor_z = sum(per_cam_floors) / len(per_cam_floors)
                    log.info("Floor (camera-anchored): %d cameras, shift %d mm",
                             len(per_cam_floors), -int(floor_z))
                else:
                    # Fallback: RANSAC on the full cloud (#246 legacy path)
                    from surface_analyzer import _detect_floor
                    coords = [(p[0], p[1], p[2]) for p in all_points]
                    floor = _detect_floor(coords, tolerance=150)
                    if floor:
                        floor_z = floor.get("z", floor.get("y", 0))
                        log.info("Floor (RANSAC fallback): shift %d mm", -int(floor_z))
                if floor_z is not None:
                    for p in all_points:
                        p[2] -= floor_z  # shift so floor = Z=0
            except Exception as e:
                log.warning("Floor normalization failed: %s", e)

        self._progress = 95
        self._message = f"Merging {len(all_points)} points..."

        # #587 — tag payload with the point-cloud schema version. v2
        # includes a per-point confidence slot populated by the cross-
        # camera filter (#582). Single-camera scans fall through as v1.
        self._result = {
            "schemaVersion": 2 if filter_stats else 1,
            "timestamp": time.time(),
            "cameras": cam_info,
            "filterStats": filter_stats,
            "points": all_points,
            "totalPoints": len(all_points),
            "floorNormalized": floor_z is not None,
            "floorOffset": floor_z,
        }

        self._progress = 100
        self._message = f"Scan complete — {len(all_points)} points from {len(cam_info)} cameras"
        self._running = False
        log.info("Space scan complete: %d points from %d cameras",
                 len(all_points), len(cam_info))
