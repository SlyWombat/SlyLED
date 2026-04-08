"""
mover_calibrator.py — Moving head calibration engine.

Discovery spiral, BFS visible-region mapping, bilinear grid interpolation,
inverse lookup (pixel → pan/tilt), and convergence correction.
"""

import json
import logging
import math
import socket
import struct
import time
import urllib.request

log = logging.getLogger("slyled")

STEP = 0.05       # pan/tilt step size for BFS
SETTLE = 1.2      # seconds between moves (heads need time to reach position)
MAX_SAMPLES = 60   # stop BFS after this many


# ── Art-Net helpers ───────────────────────────────────────────────────

def _send_artnet(bridge_ip, universe, channels):
    """Send an ArtDMX packet (with retry on transient network errors)."""
    header = b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
    header += b"\x00\x00" + struct.pack("<H", universe) + struct.pack(">H", len(channels))
    for attempt in range(3):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(header + bytes(channels), (bridge_ip, 6454))
            s.close()
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.2)
            else:
                raise


def _set_mover_dmx(dmx, addr, pan, tilt, r, g, b, dimmer=255):
    """Set a 13ch Slymovehead-style fixture in a DMX buffer.
    Channel layout: pan tilt speed dimmer strobe R G B W UV goboRot gobo macro"""
    base = addr - 1
    dmx[base + 0] = max(0, min(255, int(pan * 255)))
    dmx[base + 1] = max(0, min(255, int(tilt * 255)))
    dmx[base + 2] = 0       # speed fast
    dmx[base + 3] = dimmer
    dmx[base + 4] = 0       # no strobe
    dmx[base + 5] = r
    dmx[base + 6] = g
    dmx[base + 7] = b
    for i in range(8, 13):
        dmx[base + i] = 0


def _hold_dmx(bridge_ip, dmx, duration=0.5):
    """Send DMX at 20fps for given duration."""
    for _ in range(max(int(duration * 20), 1)):
        _send_artnet(bridge_ip, 0, dmx)
        time.sleep(0.05)


# ── Camera beam detection proxy ──────────────────────────────────────

def _beam_detect_flash(bridge_ip, camera_ip, cam_idx, mover_addr, pan, tilt,
                        color, dmx, threshold=30):
    """Flash detection: turn light ON → capture → turn OFF → capture → diff.
    Returns (px, py) or None."""
    # Light ON (should already be on from the caller, but ensure)
    _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 0.3)  # brief hold to ensure DMX is sent

    # Call flash endpoint — it captures ON frame, waits, captures OFF frame
    # We turn light OFF after a delay on our side too
    try:
        req_data = json.dumps({
            "cam": cam_idx, "color": color, "threshold": threshold,
            "offDelayMs": 400,
        }).encode()

        # Start the flash detect request (camera will capture ON frame immediately)
        req = urllib.request.Request(
            f"http://{camera_ip}:5000/beam-detect/flash",
            data=req_data,
            headers={"Content-Type": "application/json"})

        # Turn light OFF after 200ms (camera captures ON first, then waits 400ms for OFF)
        import threading
        def _off():
            time.sleep(0.2)
            _set_mover_dmx(dmx, mover_addr, pan, tilt, 0, 0, 0, dimmer=0)
            _hold_dmx(bridge_ip, dmx, 0.1)
        threading.Thread(target=_off, daemon=True).start()

        resp = urllib.request.urlopen(req, timeout=10)
        r = json.loads(resp.read().decode())

        # Restore light ON for next step
        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.1)

        if r.get("found"):
            return (r["pixelX"], r["pixelY"])
    except Exception as e:
        log.debug("Flash detect failed: %s", e)
    return None


def _beam_detect_verified(camera_ip, cam_idx, color=None, threshold=50, center=False):
    """Double-capture beam detection — takes 2 captures 300ms apart.
    Returns position only if both agree within 30px (head has settled)."""
    b1 = _beam_detect(camera_ip, cam_idx, color, threshold, center)
    if not b1:
        return None
    time.sleep(0.3)
    b2 = _beam_detect(camera_ip, cam_idx, color, threshold, center)
    if not b2:
        return None
    dx = abs(b1[0] - b2[0])
    dy = abs(b1[1] - b2[1])
    if dx > 30 or dy > 30:
        log.debug("Beam moved between captures: (%d,%d) vs (%d,%d) — head still moving",
                  b1[0], b1[1], b2[0], b2[1])
        return None
    # Return average of both
    return ((b1[0] + b2[0]) // 2, (b1[1] + b2[1]) // 2)


def _beam_detect(camera_ip, cam_idx, color=None, threshold=50, center=False):
    """Call beam detection on camera node. Returns (px, py) or None."""
    endpoint = "/beam-detect/center" if center else "/beam-detect"
    body = {"cam": cam_idx, "threshold": threshold}
    if color:
        body["color"] = color
    if center:
        body["beamCount"] = 3
    try:
        req = urllib.request.Request(
            f"http://{camera_ip}:5000{endpoint}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=5)
        r = json.loads(resp.read().decode())
        if r.get("found"):
            return (r["pixelX"], r["pixelY"])
    except Exception as e:
        log.debug("Beam detect failed: %s", e)
    return None


def _depth_at_pixel(camera_ip, cam_idx, px, py):
    """Get 3D position for a pixel using depth estimation on camera node.
    Returns (x_mm, y_mm, z_mm) or None."""
    try:
        req = urllib.request.Request(
            f"http://{camera_ip}:5000/depth-map",
            data=json.dumps({"cam": cam_idx, "points": [{"px": int(px), "py": int(py)}]}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        r = json.loads(resp.read().decode())
        pts = r.get("points3d", [])
        if pts:
            return (pts[0]["x"], pts[0]["y"], pts[0]["z"])
    except Exception as e:
        log.debug("Depth query failed: %s", e)
    return None


def _dark_reference(camera_ip, cam_idx=-1):
    """Capture dark reference on camera node."""
    try:
        req = urllib.request.Request(
            f"http://{camera_ip}:5000/dark-reference",
            data=json.dumps({"cam": cam_idx}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        r = json.loads(resp.read().decode())
        return r.get("ok", False)
    except Exception:
        return False


# ── Discovery ────────────────────────────────────────────────────────

def compute_initial_aim(mover_pos, camera_pos, pan_range=540, tilt_range=270):
    """Estimate the pan/tilt to aim the mover toward the camera.

    Uses the fixture layout positions to compute a geometric starting point.
    Convention: pan=0.5 = forward (+Z), tilt=0.5 = horizontal.

    Args:
        mover_pos: (x, y, z) in mm — fixture position from layout
        camera_pos: (x, y, z) in mm — camera position from layout
        pan_range, tilt_range: in degrees

    Returns: (pan_norm, tilt_norm) both 0.0-1.0
    """
    dx = camera_pos[0] - mover_pos[0]
    dy = camera_pos[1] - mover_pos[1]
    dz = camera_pos[2] - mover_pos[2]
    dist_xz = (dx*dx + dz*dz) ** 0.5

    import math
    pan_deg = math.degrees(math.atan2(dx, dz)) if dist_xz > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(-dy, dist_xz)) if (dist_xz > 0.001 or abs(dy) > 0.001) else 0.0

    pan_norm = max(0, min(1, 0.5 + pan_deg / pan_range))
    tilt_norm = max(0, min(1, 0.5 + tilt_deg / tilt_range))
    return (pan_norm, tilt_norm)


def discover(bridge_ip, camera_ip, mover_addr, cam_idx, color,
             other_mover_addrs=None, initial_pan=None, initial_tilt=None,
             mover_pos=None, camera_pos=None):
    """Find the first (pan, tilt) where the beam is visible to the camera.

    If mover_pos and camera_pos are provided, computes a geometric starting
    estimate. Otherwise uses initial_pan/initial_tilt defaults.
    Spirals outward from the starting point.

    Returns: (pan, tilt, px, py) or None
    """
    # Compute smart starting point from layout positions
    if mover_pos and camera_pos:
        est_pan, est_tilt = compute_initial_aim(mover_pos, camera_pos)
        initial_pan = initial_pan if initial_pan is not None else est_pan
        initial_tilt = initial_tilt if initial_tilt is not None else est_tilt
        log.info("Discovery start from layout estimate: pan=%.2f tilt=%.2f", initial_pan, initial_tilt)
    else:
        initial_pan = initial_pan if initial_pan is not None else 0.0
        initial_tilt = initial_tilt if initial_tilt is not None else 0.2
    dmx = [0] * 512
    # Black out other movers
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)

    # Turn on our mover at initial position
    pan, tilt = initial_pan, initial_tilt
    _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 2.0)  # extra settle for first position

    # Check initial position
    beam = _beam_detect_verified(camera_ip, cam_idx, color)
    if beam:
        return (pan, tilt, beam[0], beam[1])

    # Spiral outward in 0.05 steps
    for radius in range(1, 12):
        positions = []
        for dp in range(-radius, radius + 1):
            for dt in range(-radius, radius + 1):
                if max(abs(dp), abs(dt)) == radius:
                    positions.append((initial_pan + dp * STEP, initial_tilt + dt * STEP))
        for p, t in positions:
            if p < 0 or p > 1 or t < 0 or t > 1:
                continue
            _set_mover_dmx(dmx, mover_addr, p, t, *color, dimmer=255)
            _hold_dmx(bridge_ip, dmx, SETTLE)
            beam = _beam_detect_verified(camera_ip, cam_idx, color)
            if beam:
                return (p, t, beam[0], beam[1])

    return None


# ── BFS Mapping ──────────────────────────────────────────────────────

def map_visible(bridge_ip, camera_ip, mover_addr, cam_idx, color,
                start_pan, start_tilt, other_mover_addrs=None,
                step=STEP, max_samples=MAX_SAMPLES, use_center=True,
                progress_cb=None, collect_3d=False):
    """BFS explore the visible region from a known visible position.

    Light stays on, moves incrementally. Only explores from positions
    where the beam IS visible. If beam lost, skips that direction (no backtrack).

    Args:
        progress_cb: optional callable(sample_count, current_pan, current_tilt)
        collect_3d: if True, also query depth to get 3D world coords per sample

    Returns: list of (pan, tilt, pixel_x, pixel_y) or with collect_3d:
             list of (pan, tilt, pixel_x, pixel_y, world_x, world_y, world_z)
    """
    dmx = [0] * 512
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)
    _set_mover_dmx(dmx, mover_addr, start_pan, start_tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 1.0)

    samples = []
    visited = set()
    queue = [(start_pan, start_tilt)]
    last_good = (start_pan, start_tilt)

    while queue and len(samples) < max_samples:
        pan, tilt = queue.pop(0)
        key = (round(pan, 3), round(tilt, 3))
        if key in visited or pan < 0 or pan > 1 or tilt < 0 or tilt > 1:
            continue
        visited.add(key)

        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, SETTLE)

        beam = _beam_detect_verified(camera_ip, cam_idx, color, center=use_center)
        if beam:
            px, py = beam
            # Reject stale: if pixel barely moved from a different pan/tilt, it's noise
            is_stale = False
            if samples:
                for s in samples[-5:]:
                    sp, st, spx, spy = s[0], s[1], s[2], s[3]
                    if (abs(px - spx) < 15 and abs(py - spy) < 15 and
                        (abs(pan - sp) > step * 0.5 or abs(tilt - st) > step * 0.5)):
                        is_stale = True
                        break
            if not is_stale:
                if collect_3d:
                    pt3d = _depth_at_pixel(camera_ip, cam_idx, px, py)
                    if pt3d:
                        samples.append((pan, tilt, px, py, pt3d[0], pt3d[1], pt3d[2]))
                    else:
                        samples.append((pan, tilt, px, py, 0, 0, 0))
                else:
                    samples.append((pan, tilt, px, py))
                last_good = (pan, tilt)
                # Explore neighbors
                for dp, dt in [(step, 0), (-step, 0), (0, step), (0, -step)]:
                    nb = (round(pan + dp, 3), round(tilt + dt, 3))
                    if nb not in visited:
                        queue.append(nb)
                if progress_cb:
                    progress_cb(len(samples), pan, tilt)

    return samples


# ── Grid interpolation ───────────────────────────────────────────────

def build_grid(samples):
    """Build a regular interpolation grid from scattered samples.

    Returns dict with panSteps, tiltSteps, pixelX (2D), pixelY (2D),
    or None if insufficient samples.
    """
    if len(samples) < 4:
        return None

    import numpy as np
    pans = sorted(set(round(s[0], 3) for s in samples))
    tilts = sorted(set(round(s[1], 3) for s in samples))

    if len(pans) < 2 or len(tilts) < 2:
        return None

    # Build lookup: (pan, tilt) → (px, py)
    lookup = {}
    for p, t, px, py in samples:
        lookup[(round(p, 3), round(t, 3))] = (px, py)

    # Fill grid — use nearest neighbor for missing cells
    grid_px = []
    grid_py = []
    for pi, p in enumerate(pans):
        row_px = []
        row_py = []
        for ti, t in enumerate(tilts):
            key = (round(p, 3), round(t, 3))
            if key in lookup:
                row_px.append(lookup[key][0])
                row_py.append(lookup[key][1])
            else:
                # Nearest neighbor
                best_d = 9999
                best_v = (0, 0)
                for sp, st, spx, spy in samples:
                    d = (sp - p)**2 + (st - t)**2
                    if d < best_d:
                        best_d = d
                        best_v = (spx, spy)
                row_px.append(best_v[0])
                row_py.append(best_v[1])
        grid_px.append(row_px)
        grid_py.append(row_py)

    return {
        "panSteps": pans,
        "tiltSteps": tilts,
        "pixelX": grid_px,
        "pixelY": grid_py,
    }


def grid_lookup(grid, pan, tilt):
    """Bilinear interpolation: (pan, tilt) → (pixel_x, pixel_y)."""
    pans = grid["panSteps"]
    tilts = grid["tiltSteps"]
    gpx = grid["pixelX"]
    gpy = grid["pixelY"]

    # Clamp to grid range
    pan = max(pans[0], min(pans[-1], pan))
    tilt = max(tilts[0], min(tilts[-1], tilt))

    # Find surrounding indices
    pi = 0
    for i in range(len(pans) - 1):
        if pans[i + 1] >= pan:
            pi = i
            break
    ti = 0
    for i in range(len(tilts) - 1):
        if tilts[i + 1] >= tilt:
            ti = i
            break

    # Bilinear weights
    p_range = pans[pi + 1] - pans[pi] if pi + 1 < len(pans) else 1
    t_range = tilts[ti + 1] - tilts[ti] if ti + 1 < len(tilts) else 1
    wp = (pan - pans[pi]) / p_range if p_range > 0 else 0
    wt = (tilt - tilts[ti]) / t_range if t_range > 0 else 0

    pi2 = min(pi + 1, len(pans) - 1)
    ti2 = min(ti + 1, len(tilts) - 1)

    # Interpolate
    px = (gpx[pi][ti] * (1-wp) * (1-wt) +
          gpx[pi2][ti] * wp * (1-wt) +
          gpx[pi][ti2] * (1-wp) * wt +
          gpx[pi2][ti2] * wp * wt)
    py = (gpy[pi][ti] * (1-wp) * (1-wt) +
          gpy[pi2][ti] * wp * (1-wt) +
          gpy[pi][ti2] * (1-wp) * wt +
          gpy[pi2][ti2] * wp * wt)

    return (px, py)


def grid_inverse(grid, target_px, target_py, iterations=20):
    """Inverse lookup: (pixel_x, pixel_y) → (pan, tilt).

    Uses iterative Newton's method with the grid's local Jacobian.
    """
    pans = grid["panSteps"]
    tilts = grid["tiltSteps"]
    # Start from center of grid
    pan = (pans[0] + pans[-1]) / 2
    tilt = (tilts[0] + tilts[-1]) / 2

    for _ in range(iterations):
        px, py = grid_lookup(grid, pan, tilt)
        err_x = target_px - px
        err_y = target_py - py
        if err_x**2 + err_y**2 < 4:  # within 2px
            break
        # Numerical Jacobian
        dp = 0.001
        px_dp, py_dp = grid_lookup(grid, pan + dp, tilt)
        px_dt, py_dt = grid_lookup(grid, pan, tilt + dp)
        dpx_dp = (px_dp - px) / dp
        dpy_dp = (py_dp - py) / dp
        dpx_dt = (px_dt - px) / dp
        dpy_dt = (py_dt - py) / dp
        det = dpx_dp * dpy_dt - dpx_dt * dpy_dp
        if abs(det) < 0.001:
            break
        # Newton step with damping
        d_pan = (dpy_dt * err_x - dpx_dt * err_y) / det * 0.5
        d_tilt = (-dpy_dp * err_x + dpx_dp * err_y) / det * 0.5
        pan = max(pans[0], min(pans[-1], pan + d_pan))
        tilt = max(tilts[0], min(tilts[-1], tilt + d_tilt))

    return (pan, tilt)


def build_grid_3d(samples):
    """Build a grid mapping (pan, tilt) → (world_x, world_y, world_z).
    Samples must be 7-tuples: (pan, tilt, px, py, wx, wy, wz).
    Returns dict with panSteps, tiltSteps, worldX/Y/Z 2D arrays, or None."""
    if len(samples) < 4:
        return None
    pans = sorted(set(round(s[0], 3) for s in samples))
    tilts = sorted(set(round(s[1], 3) for s in samples))
    if len(pans) < 2 or len(tilts) < 2:
        return None
    lookup = {}
    for s in samples:
        lookup[(round(s[0], 3), round(s[1], 3))] = (s[4], s[5], s[6])
    grid_wx, grid_wy, grid_wz = [], [], []
    for p in pans:
        rx, ry, rz = [], [], []
        for t in tilts:
            key = (round(p, 3), round(t, 3))
            if key in lookup:
                rx.append(lookup[key][0]); ry.append(lookup[key][1]); rz.append(lookup[key][2])
            else:
                best_d, best_v = 9999, (0, 0, 0)
                for s in samples:
                    d = (s[0] - p)**2 + (s[1] - t)**2
                    if d < best_d:
                        best_d = d; best_v = (s[4], s[5], s[6])
                rx.append(best_v[0]); ry.append(best_v[1]); rz.append(best_v[2])
        grid_wx.append(rx); grid_wy.append(ry); grid_wz.append(rz)
    return {"panSteps": pans, "tiltSteps": tilts,
            "worldX": grid_wx, "worldY": grid_wy, "worldZ": grid_wz}


def grid_3d_lookup(grid3d, pan, tilt):
    """Bilinear interpolation: (pan, tilt) → (world_x, world_y, world_z)."""
    pans, tilts = grid3d["panSteps"], grid3d["tiltSteps"]
    pan = max(pans[0], min(pans[-1], pan))
    tilt = max(tilts[0], min(tilts[-1], tilt))
    pi = 0
    for i in range(len(pans) - 1):
        if pans[i + 1] >= pan: pi = i; break
    ti = 0
    for i in range(len(tilts) - 1):
        if tilts[i + 1] >= tilt: ti = i; break
    pr = pans[pi + 1] - pans[pi] if pi + 1 < len(pans) else 1
    tr = tilts[ti + 1] - tilts[ti] if ti + 1 < len(tilts) else 1
    wp = (pan - pans[pi]) / pr if pr > 0 else 0
    wt = (tilt - tilts[ti]) / tr if tr > 0 else 0
    pi2, ti2 = min(pi + 1, len(pans) - 1), min(ti + 1, len(tilts) - 1)
    result = []
    for g in [grid3d["worldX"], grid3d["worldY"], grid3d["worldZ"]]:
        v = (g[pi][ti] * (1-wp) * (1-wt) + g[pi2][ti] * wp * (1-wt) +
             g[pi][ti2] * (1-wp) * wt + g[pi2][ti2] * wp * wt)
        result.append(v)
    return tuple(result)


def grid_3d_inverse(grid3d, target_x, target_y, target_z, iterations=30):
    """Inverse: (world_x, world_y, world_z) → (pan, tilt).
    Finds the pan/tilt that aims closest to the target 3D point."""
    pans, tilts = grid3d["panSteps"], grid3d["tiltSteps"]
    # Brute-force search over grid for best starting point
    best_pan, best_tilt, best_dist = pans[0], tilts[0], 1e9
    for p in pans:
        for t in tilts:
            wx, wy, wz = grid_3d_lookup(grid3d, p, t)
            d = (wx - target_x)**2 + (wy - target_y)**2 + (wz - target_z)**2
            if d < best_dist:
                best_dist = d; best_pan = p; best_tilt = t
    # Refine with Newton iteration
    pan, tilt = best_pan, best_tilt
    for it in range(iterations):
        wx, wy, wz = grid_3d_lookup(grid3d, pan, tilt)
        err = ((wx - target_x)**2 + (wy - target_y)**2 + (wz - target_z)**2) ** 0.5
        if err < 10:  # within 10mm
            break
        dp = 0.001
        wx_dp, wy_dp, wz_dp = grid_3d_lookup(grid3d, pan + dp, tilt)
        wx_dt, wy_dt, wz_dt = grid_3d_lookup(grid3d, pan, tilt + dp)
        # Jacobian: d(world)/d(pan,tilt) — use X and Z (horizontal plane)
        dwx_dp = (wx_dp - wx) / dp; dwz_dp = (wz_dp - wz) / dp
        dwx_dt = (wx_dt - wx) / dp; dwz_dt = (wz_dt - wz) / dp
        det = dwx_dp * dwz_dt - dwx_dt * dwz_dp
        if abs(det) < 0.001:
            break
        ex, ez = target_x - wx, target_z - wz
        gain = 0.4 if it < 15 else 0.15
        d_pan = (dwz_dt * ex - dwx_dt * ez) / det * gain
        d_tilt = (-dwz_dp * ex + dwx_dp * ez) / det * gain
        pan = max(pans[0], min(pans[-1], pan + d_pan))
        tilt = max(tilts[0], min(tilts[-1], tilt + d_tilt))
    return (pan, tilt)


# ── Convergence ──────────────────────────────────────────────────────

def converge(bridge_ip, camera_ip, cam_idx,
             mover_addr, grid, color,
             target_px, target_py,
             other_mover_addrs=None,
             max_iterations=25):
    """Closed-loop convergence: aim mover at target pixel, verify, nudge.

    Returns: (pan, tilt, final_dist_px) or None
    """
    dmx = [0] * 512
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)

    pan, tilt = grid_inverse(grid, target_px, target_py)
    best_pan, best_tilt, best_dist = pan, tilt, 9999
    worse_streak = 0

    for it in range(max_iterations):
        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.8)

        beam = _beam_detect(camera_ip, cam_idx, color, center=True)
        if not beam:
            pan, tilt = best_pan, best_tilt
            _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
            _hold_dmx(bridge_ip, dmx, 0.5)
            worse_streak += 1
            if worse_streak > 3:
                break
            continue

        bx, by = beam
        err_x = target_px - bx
        err_y = target_py - by
        dist = (err_x**2 + err_y**2) ** 0.5

        improved = dist < best_dist
        if improved:
            best_dist = dist
            best_pan, best_tilt = pan, tilt
            worse_streak = 0
        else:
            worse_streak += 1

        log.info("converge[%d] beam=(%d,%d) dist=%.0f pan=%.4f tilt=%.4f%s",
                 it, bx, by, dist, pan, tilt, " *" if improved else "")

        if dist < 20:
            break
        if worse_streak >= 5:
            pan, tilt = best_pan, best_tilt
            break

        # Nudge using grid's local Jacobian
        dp = 0.001
        pans = grid["panSteps"]
        tilts = grid["tiltSteps"]
        px0, py0 = grid_lookup(grid, pan, tilt)
        px_dp, py_dp = grid_lookup(grid, pan + dp, tilt)
        px_dt, py_dt = grid_lookup(grid, pan, tilt + dp)
        dpx_dp = (px_dp - px0) / dp
        dpy_dp = (py_dp - py0) / dp
        dpx_dt = (px_dt - px0) / dp
        dpy_dt = (py_dt - py0) / dp
        det = dpx_dp * dpy_dt - dpx_dt * dpy_dp
        if abs(det) < 0.001:
            break
        gain = 0.5 if it < 10 else 0.2  # aggressive early, cautious late
        d_pan = (dpy_dt * err_x - dpx_dt * err_y) / det * gain
        d_tilt = (-dpy_dp * err_x + dpx_dp * err_y) / det * gain
        pan = max(pans[0], min(pans[-1], pan + d_pan))
        tilt = max(tilts[0], min(tilts[-1], tilt + d_tilt))

    return (best_pan, best_tilt, best_dist)
