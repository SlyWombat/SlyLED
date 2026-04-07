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
SETTLE = 0.6      # seconds between moves
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

def discover(bridge_ip, camera_ip, mover_addr, cam_idx, color,
             other_mover_addrs=None, initial_pan=0.0, initial_tilt=0.2):
    """Find the first (pan, tilt) where the beam is visible to the camera.

    Starts from initial guess and spirals outward. Light stays on,
    moves incrementally.

    Returns: (pan, tilt, px, py) or None
    """
    dmx = [0] * 512
    # Black out other movers
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)

    # Turn on our mover at initial position
    pan, tilt = initial_pan, initial_tilt
    _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 1.5)

    # Check initial position
    beam = _beam_detect(camera_ip, cam_idx, color)
    if beam:
        return (pan, tilt, beam[0], beam[1])

    # Spiral outward in 0.05 steps
    for radius in range(1, 12):  # up to 0.55 from start
        r = radius * STEP
        # Generate positions on this ring (clockwise)
        positions = []
        for dp in range(-radius, radius + 1):
            for dt in range(-radius, radius + 1):
                if max(abs(dp), abs(dt)) == radius:  # only the ring edge
                    positions.append((initial_pan + dp * STEP, initial_tilt + dt * STEP))
        for p, t in positions:
            if p < 0 or p > 1 or t < 0 or t > 1:
                continue
            _set_mover_dmx(dmx, mover_addr, p, t, *color, dimmer=255)
            _hold_dmx(bridge_ip, dmx, SETTLE)
            beam = _beam_detect(camera_ip, cam_idx, color)
            if beam:
                return (p, t, beam[0], beam[1])

    return None


# ── BFS Mapping ──────────────────────────────────────────────────────

def map_visible(bridge_ip, camera_ip, mover_addr, cam_idx, color,
                start_pan, start_tilt, other_mover_addrs=None,
                step=STEP, max_samples=MAX_SAMPLES, use_center=True,
                progress_cb=None):
    """BFS explore the visible region from a known visible position.

    Light stays on, moves incrementally. Only explores from positions
    where the beam IS visible. If beam lost, skips that direction (no backtrack).

    Args:
        progress_cb: optional callable(sample_count, current_pan, current_tilt)

    Returns: list of (pan, tilt, pixel_x, pixel_y)
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

        beam = _beam_detect(camera_ip, cam_idx, color, center=use_center)
        if beam:
            px, py = beam
            # Reject stale: if pixel barely moved from a different pan/tilt, it's noise
            is_stale = False
            if samples:
                for sp, st, spx, spy in samples[-5:]:
                    if (abs(px - spx) < 15 and abs(py - spy) < 15 and
                        (abs(pan - sp) > step * 0.5 or abs(tilt - st) > step * 0.5)):
                        is_stale = True
                        break
            if not is_stale:
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
