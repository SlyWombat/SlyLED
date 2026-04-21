"""
mover_calibrator.py — Moving head calibration engine.

Discovery spiral, BFS visible-region mapping, bilinear grid interpolation,
inverse lookup (pixel → pan/tilt), and convergence correction.
"""

import json
import logging
import math
import random
import socket
import struct
import threading
import time
import urllib.request

log = logging.getLogger("slyled")

# Module-level cv_engine instance — set by parent_server to enable local processing (#333)
_cv_engine = None

def set_cv_engine(engine):
    """Set the shared CVEngine instance for local beam detection."""
    global _cv_engine
    _cv_engine = engine

STEP = 0.05       # pan/tilt step size for BFS
SETTLE = 0.6      # seconds between moves (reduced from 1.2)
MAX_SAMPLES = 80   # stop BFS after this many (increased from 60 for better coverage)

# ── Adaptive settle time (#238) ─────────────────────────────────────────
SETTLE_BASE = 0.4          # base settle time (seconds, reduced from 0.8)
SETTLE_ESCALATE = [0.4, 0.8, 1.5]  # escalation stages (faster)
SETTLE_VERIFY_GAP = 0.2   # gap between double-capture (reduced from 0.3)
SETTLE_PIXEL_THRESH = 30  # max pixel drift to consider settled


# ── Art-Net helpers ───────────────────────────────────────────────────

# ── DMX output ────────────────────────────────────────────────────────
# Uses a dmx_sender callback injected by the orchestrator to write
# through the Art-Net/sACN engine rather than raw UDP. This ensures
# the calibrator works with any transport and doesn't conflict with
# the engine's continuous 40Hz output.

_dmx_sender = None  # set by set_dmx_sender(fn)

def set_dmx_sender(fn):
    """Register a callback: fn(universe_1based, addr_1based, values_list).
    Called by parent_server at startup."""
    global _dmx_sender
    _dmx_sender = fn


# #594 — seed calibration's local DMX buffer from the running engine state
# so `_apply_profile_defaults` (lamp-on, mode, shutter-open, other fixtures)
# is preserved when calibration writes the full universe back. Without this,
# calibration's `dmx = [0] * 512` clobbers every channel except the ones it
# explicitly re-asserts — the fixture responds but its lamp/shutter/mode
# defaults are gone, so the beam stays dark.
_engine_snapshot_getter = None  # fn(universe_1based) -> bytes | list[int] of len 512

def set_engine_snapshot_getter(fn):
    """Register a callback the calibrator uses to seed its local DMX buffer
    from the engine's current universe state. fn(universe_1based) returns
    a 512-byte buffer."""
    global _engine_snapshot_getter
    _engine_snapshot_getter = fn


def _fresh_buffer():
    """Return a 512-entry DMX buffer seeded from the engine's current state
    for `_active_universe` when a snapshot getter is registered. Falls back
    to zeros if either the getter or the active universe isn't set. This
    preserves lamp-on / mode / shutter defaults and other fixtures' channels
    when the calibration path later writes the whole buffer back."""
    try:
        if _engine_snapshot_getter and _active_universe is not None:
            snap = _engine_snapshot_getter(_active_universe)
            if snap is not None:
                buf = list(snap)
                if len(buf) < 512:
                    buf.extend([0] * (512 - len(buf)))
                return buf[:512]
    except Exception as e:
        log.warning("Engine snapshot failed (uni=%s): %s — zero-seeding", _active_universe, e)
    return [0] * 512


# #594 — cooperative cancellation. The Cancel button used to only close the
# modal; the background thread would keep sweeping the fixture. Now the
# orchestrator sets this event, and discovery/BFS/hold_dmx loops raise
# CalibrationAborted, unwinding cleanly to `_cal_blackout`.
_cancel_event = threading.Event()


class CalibrationAborted(Exception):
    """Raised when cancellation is requested while calibration is running."""


def arm_cancel():
    """Clear the cancel flag at the start of a new calibration job."""
    _cancel_event.clear()


def request_cancel():
    """Signal the running calibration thread to abort at its next check."""
    _cancel_event.set()


def is_cancel_requested():
    return _cancel_event.is_set()


def _check_cancel():
    if _cancel_event.is_set():
        raise CalibrationAborted()


def _send_artnet(bridge_ip, universe, channels):
    """Send DMX — uses engine callback if available, falls back to raw UDP."""
    global _probe_counter, _last_probe
    _probe_counter += 1
    if _last_probe is None:
        _last_probe = {}
    _last_probe["attempt"] = _probe_counter
    _last_probe["universe"] = universe + 1  # 1-based for display
    _last_probe["sentAt"] = time.time()
    # Snapshot up to 64 channels; avoids shipping the full 512 on every poll.
    try:
        _last_probe["channels"] = list(channels[:64])
    except Exception:
        _last_probe["channels"] = []
    if _dmx_sender:
        # Write all non-zero channels through the engine
        _dmx_sender(universe + 1, 1, channels)  # universe is 0-based here, engine is 1-based
        return
    # Fallback: raw Art-Net UDP (legacy, may conflict with engine)
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


_active_profile = None  # Set by caller before calibration; used by _set_mover_dmx
_active_universe = None  # 1-based universe of the fixture being calibrated (#594)

# #602 — per-probe telemetry so the SPA status panel can render a live view
# of what the calibration thread is actually doing (pan/tilt normalized,
# DMX byte values currently on the wire, attempt counter).
_last_probe = None  # dict populated by _set_mover_dmx + _send_artnet
_probe_counter = 0  # monotonic counter; bumped on each _send_artnet


def get_last_probe():
    """Return the most recently sent probe record, or None.

    Used by parent_server's /status endpoint to expose currentProbe and
    dmxFrame to the SPA. Safe to call from any thread.
    """
    return dict(_last_probe) if _last_probe else None


def reset_probe_counter():
    """Called at the start of a new calibration job to reset attempt#."""
    global _probe_counter, _last_probe
    _probe_counter = 0
    _last_probe = None

def _set_mover_dmx(dmx, addr, pan, tilt, r, g, b, dimmer=255, profile=None):
    """Set a mover fixture in a DMX buffer using profile channel map.

    If profile is provided (or _active_profile is set), uses channel_map for
    correct offsets and handles 8/16-bit pan/tilt, color-wheel, strobe defaults.
    Falls back to hardcoded 13ch Slymovehead layout if no profile.
    """
    global _last_probe
    # #602 — record the normalized pan/tilt and the output RGB so the
    # /status endpoint can show what the operator just asked the fixture
    # to do, independent of the wire-level DMX bytes.
    if _last_probe is None:
        _last_probe = {}
    _last_probe["pan"] = float(pan)
    _last_probe["tilt"] = float(tilt)
    _last_probe["addr"] = int(addr)
    _last_probe["rgb"] = [int(r), int(g), int(b)]
    _last_probe["dimmer"] = int(dimmer)
    base = addr - 1
    profile = profile or _active_profile
    if profile:
        try:
            cm = profile.get("channel_map", {})
            channels = profile.get("channels", [])
            # Pan/tilt — detect 8 vs 16 bit
            for axis, val in [("pan", pan), ("tilt", tilt)]:
                off = cm.get(axis)
                if off is None:
                    continue
                ch_def = next((c for c in channels if c.get("type") == axis), None)
                bits = ch_def.get("bits", 8) if ch_def else 8
                if bits == 16:
                    v16 = max(0, min(65535, int(val * 65535)))
                    dmx[base + off] = v16 >> 8
                    dmx[base + off + 1] = v16 & 0xFF
                else:
                    dmx[base + off] = max(0, min(255, int(val * 255)))
            # Dimmer
            if "dimmer" in cm:
                dmx[base + cm["dimmer"]] = max(0, min(255, dimmer))
            # Color — RGB or color-wheel
            if "red" in cm:
                dmx[base + cm["red"]] = max(0, min(255, r))
                if "green" in cm: dmx[base + cm["green"]] = max(0, min(255, g))
                if "blue" in cm: dmx[base + cm["blue"]] = max(0, min(255, b))
            elif "color-wheel" in cm:
                from dmx_profiles import rgb_to_wheel_slot
                cw = rgb_to_wheel_slot(profile, r, g, b) if (r or g or b) else 0
                dmx[base + cm["color-wheel"]] = cw
            # Apply channel defaults (strobe open, speed, etc.)
            for ch in channels:
                default = ch.get("default")
                ch_type = ch.get("type", "")
                if default is not None and default > 0 and ch_type not in ("pan", "tilt", "dimmer", "red", "green", "blue", "color-wheel"):
                    dmx[base + ch.get("offset", 0)] = max(0, min(255, int(default)))
            # #516 — ensure the shutter is OPEN during calibration so the
            # beam is always visible to the camera. strobe_open_value
            # honours the shutterEffect annotation on ShutterStrobe ranges
            # (Open vs Closed) and overrides any channel default that
            # would leave the fixture strobing or blacked out.
            if "strobe" in cm:
                try:
                    from dmx_profiles import strobe_open_value
                    dmx[base + cm["strobe"]] = strobe_open_value(profile)
                except Exception:
                    pass
        except Exception as e:
            log.warning("Profile-aware DMX write failed (addr=%d): %s — falling back to legacy", addr, e)
            # Fall through to legacy
            profile = None
    if not profile:
        # Legacy fallback: 13ch Slymovehead layout
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

    # #602 — record the actual DMX bytes that came out of the channel-map
    # resolution so the UI can show the operator the exact numbers that
    # will be on the wire (high-byte on 16-bit pan/tilt).
    try:
        if profile:
            cm = profile.get("channel_map", {})
            if "pan" in cm:
                _last_probe["dmxPan"] = int(dmx[base + cm["pan"]])
            if "tilt" in cm:
                _last_probe["dmxTilt"] = int(dmx[base + cm["tilt"]])
        else:
            _last_probe["dmxPan"] = int(dmx[base + 0])
            _last_probe["dmxTilt"] = int(dmx[base + 1])
    except Exception:
        pass


def _hold_dmx(bridge_ip, dmx, duration=0.5):
    """Set DMX channels and wait for the fixture to settle. Honours the
    cancel flag (#594) so a long settle doesn't delay abort."""
    _check_cancel()
    _send_artnet(bridge_ip, 0, dmx)
    # Break the sleep into short slices so Cancel is responsive mid-settle.
    remaining = max(0.0, duration)
    while remaining > 0:
        slice_s = 0.1 if remaining > 0.1 else remaining
        time.sleep(slice_s)
        remaining -= slice_s
        _check_cancel()


def pick_calibration_targets(fixture_pos, geometry, n=6,
                              camera_pos=None, camera_fov_deg=90,
                              margin_frac=0.15):
    """Pick N stage-space aim targets for calibration (#497).

    Lays out a uniform grid inside the floor extent, clips to the
    camera's rough visibility cone, drops anything inside the AABB of
    a detected obstacle, then trims to N via angular-spread ranking
    from the fixture's position (prefer points that are mutually
    well-separated in pan/tilt angle).

    Args:
        fixture_pos:    (x, y, z) of the mover in stage mm.
        geometry:       dict from _get_stage_geometry (floor + walls +
                        obstacles or the layout-box fallback).
        n:              number of targets to return (minimum 4).
        camera_pos:     optional (x, y, z) — when set, the target grid
                        is clipped to the camera's forward FOV.
        camera_fov_deg: horizontal FOV of the camera, used for the
                        visibility cone.
        margin_frac:    fraction of extent shrunk inward on all sides so
                        targets don't land at the mechanical-range edge.

    Returns a list of (x, y, z) in stage mm, length ≤ n. The caller
    must still verify each is reachable (fixture pan/tilt range) and
    visible to the camera — this function gives a reasonable default.
    """
    n = max(4, int(n))
    floor = geometry.get("floor") if geometry else None
    if not floor:
        return []
    extent = floor.get("extent") or {}
    if not extent:
        return []
    xmin = extent.get("xMin", 0)
    xmax = extent.get("xMax", 0)
    ymin = extent.get("yMin", 0)
    ymax = extent.get("yMax", 0)
    floor_z = floor.get("z", 0)
    if xmax <= xmin or ymax <= ymin:
        return []

    # Shrink inward from the extent boundary.
    sx = (xmax - xmin) * margin_frac
    sy = (ymax - ymin) * margin_frac
    xmin_s, xmax_s = xmin + sx, xmax - sx
    ymin_s, ymax_s = ymin + sy, ymax - sy

    # 3×2 base grid (6 points) — overridden below if n differs.
    cols = 3 if n >= 5 else 2
    rows = max(2, math.ceil(n / cols))
    candidates = []
    for j in range(rows):
        fy = j / max(rows - 1, 1)
        py = ymin_s + (ymax_s - ymin_s) * fy
        for i in range(cols):
            fx = i / max(cols - 1, 1)
            px = xmin_s + (xmax_s - xmin_s) * fx
            candidates.append((px, py, floor_z))

    # Drop any candidate that falls inside an obstacle AABB. We inflate
    # each obstacle bbox by 150 mm so the beam doesn't try to land on
    # the edge of a pillar.
    obstacles = geometry.get("obstacles") or []
    def _blocked(pt):
        px, py, pz = pt
        for ob in obstacles:
            bbox = ob.get("bbox") or ob.get("aabb")
            if not bbox:
                continue
            x0 = bbox.get("xMin", bbox.get("x_min", float("inf")))
            x1 = bbox.get("xMax", bbox.get("x_max", float("-inf")))
            y0 = bbox.get("yMin", bbox.get("y_min", float("inf")))
            y1 = bbox.get("yMax", bbox.get("y_max", float("-inf")))
            if x0 - 150 <= px <= x1 + 150 and y0 - 150 <= py <= y1 + 150:
                return True
        return False
    candidates = [c for c in candidates if not _blocked(c)]

    # Camera visibility: drop points outside the camera's horizontal FOV
    # cone along the stage-forward Y axis. Uses a simple projection onto
    # the camera-forward vector; cheap and good enough as a first filter.
    if camera_pos is not None:
        cx, cy, _ = camera_pos
        half_fov = math.radians(camera_fov_deg) / 2.0
        def _visible(pt):
            dx = pt[0] - cx
            dy = pt[1] - cy
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                return False
            ang = abs(math.atan2(dx, dy))  # 0 = straight forward
            return ang <= half_fov
        candidates = [c for c in candidates if _visible(c)]

    if len(candidates) <= n:
        return candidates

    # Trim to N via greedy angular-spread selection from the fixture.
    fx_x, fx_y, fx_z = fixture_pos

    def _aim_angles(pt):
        dx = pt[0] - fx_x
        dy = pt[1] - fx_y
        dz = pt[2] - fx_z
        pan = math.atan2(dx, dy)  # 0 = forward
        horiz = math.hypot(dx, dy)
        tilt = math.atan2(-dz, horiz)
        return pan, tilt

    # Seed with the point closest to the fixture's forward center.
    seeds = sorted(candidates, key=lambda c: (c[0] - fx_x) ** 2 + (c[1] - fx_y) ** 2)
    picked = [seeds[0]]
    pool = [c for c in candidates if c is not seeds[0]]
    while len(picked) < n and pool:
        # Pick the candidate whose minimum angular distance to any
        # already-picked target is largest (maximises spread).
        best = None
        best_score = -1.0
        for cand in pool:
            cp, ct = _aim_angles(cand)
            min_dist = min(
                math.hypot(cp - pp, ct - pt)
                for pp, pt in (_aim_angles(p) for p in picked)
            )
            if min_dist > best_score:
                best_score = min_dist
                best = cand
        picked.append(best)
        pool.remove(best)
    return picked


def stage_to_pixel(homography_flat, sx, sy):
    """Inverse of `pixel_to_stage` — project a stage-floor point (sx, sy in mm)
    back to camera pixel coordinates (#499). Returns (px, py) or None.

    The homography is pixel→stage, so we invert the 3×3 matrix. Stage Y
    is the depth component used during camera calibration (matches the
    same [x, z] convention as `_apply_homography` in parent_server).
    """
    import numpy as np
    try:
        H = np.array(homography_flat, dtype=float).reshape(3, 3)
        Hi = np.linalg.inv(H)
    except Exception:
        return None
    v = Hi @ np.array([float(sx), float(sy), 1.0])
    if abs(v[2]) < 1e-9:
        return None
    return (float(v[0] / v[2]), float(v[1] / v[2]))


def converge_on_stage_target(bridge_ip, camera_ip, mover_addr, cam_idx, color,
                              homography_flat, target_stage,
                              model=None, start_pan=0.5, start_tilt=0.5,
                              other_mover_addrs=None, max_iterations=25,
                              converge_px=20):
    """Per-target convergence loop (#499).

    Aims the fixture at `target_stage` (x, y, z in mm). Uses:
      - ``model`` (ParametricFixtureModel) for the initial aim. When not
        given, falls back to ``start_pan`` / ``start_tilt``.
      - ``homography_flat`` (pixel→stage 3×3) for the stage→pixel
        projection that tells us "where should the beam land in the
        camera image?".

    Closed-loop: detect → nudge → repeat until pixel error < converge_px
    or max_iterations. Uses a numerical pan/tilt Jacobian from the model
    (tiny ±0.005 probe aims) so no pre-built grid is required.

    Returns dict:
        {
          "converged": bool, "iterations": int,
          "pan": float, "tilt": float,
          "beamPixel": [x, y] | None,
          "targetPixel": [x, y],
          "errorPx": float | None,
        }
    """
    target_px = stage_to_pixel(homography_flat, target_stage[0], target_stage[1])
    if target_px is None:
        return {"converged": False, "iterations": 0, "pan": start_pan,
                "tilt": start_tilt, "beamPixel": None,
                "targetPixel": None, "errorPx": None,
                "reason": "homography inversion failed"}

    dmx = _fresh_buffer()
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)

    if model is not None:
        try:
            pan, tilt = model.inverse(target_stage[0], target_stage[1], target_stage[2])
        except Exception:
            pan, tilt = start_pan, start_tilt
    else:
        pan, tilt = start_pan, start_tilt

    best_pan, best_tilt = pan, tilt
    best_dist = 1e9
    final_beam = None
    worse_streak = 0

    for it in range(max_iterations):
        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.6)
        beam = _beam_detect(camera_ip, cam_idx, color, center=True)
        if beam is None:
            worse_streak += 1
            if worse_streak >= 3:
                pan, tilt = best_pan, best_tilt
                break
            continue

        final_beam = beam
        err_x = target_px[0] - beam[0]
        err_y = target_px[1] - beam[1]
        dist = math.hypot(err_x, err_y)
        if dist < best_dist:
            best_dist = dist
            best_pan, best_tilt = pan, tilt
            worse_streak = 0
        else:
            worse_streak += 1

        if dist < converge_px:
            break
        if worse_streak >= 4:
            pan, tilt = best_pan, best_tilt
            break

        # Pan/tilt → pixel Jacobian via two tiny probe aims.
        dp = 0.005
        probe_pan = min(1.0, pan + dp)
        probe_tilt = min(1.0, tilt + dp)
        _set_mover_dmx(dmx, mover_addr, probe_pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.4)
        b_p = _beam_detect(camera_ip, cam_idx, color, center=True)
        _set_mover_dmx(dmx, mover_addr, pan, probe_tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.4)
        b_t = _beam_detect(camera_ip, cam_idx, color, center=True)
        if b_p is None or b_t is None:
            # Fall back: small blind nudge toward the target direction.
            pan = max(0.0, min(1.0, pan + 0.01 * (1 if err_x > 0 else -1)))
            tilt = max(0.0, min(1.0, tilt + 0.01 * (1 if err_y > 0 else -1)))
            continue
        dpx_dp = (b_p[0] - beam[0]) / dp
        dpy_dp = (b_p[1] - beam[1]) / dp
        dpx_dt = (b_t[0] - beam[0]) / dp
        dpy_dt = (b_t[1] - beam[1]) / dp
        det = dpx_dp * dpy_dt - dpx_dt * dpy_dp
        if abs(det) < 0.001:
            break
        gain = 0.5 if it < 8 else 0.25
        d_pan = (dpy_dt * err_x - dpx_dt * err_y) / det * gain
        d_tilt = (-dpy_dp * err_x + dpx_dp * err_y) / det * gain
        pan = max(0.0, min(1.0, pan + d_pan))
        tilt = max(0.0, min(1.0, tilt + d_tilt))

    return {
        "converged": best_dist < converge_px,
        "iterations": it + 1,
        "pan": best_pan, "tilt": best_tilt,
        "beamPixel": list(final_beam) if final_beam else None,
        "targetPixel": [int(target_px[0]), int(target_px[1])],
        "errorPx": best_dist if best_dist < 1e8 else None,
    }


def verification_sweep(bridge_ip, camera_ip, mover_addr, cam_idx, color,
                        grid, n_points=3, avoid_samples=None):
    """Post-fit verification (#501). Aims the fixture at N pan/tilt points
    drawn from inside the calibrated region but excluded from the fit,
    detects the beam via camera, and reports the residual in pixel
    space against the grid's own prediction.

    This is a cross-check against overfitting — the LM residual on the
    fit set is always low by construction. Verification on held-out
    points is the real test of model generalisation.

    Args:
        grid:           the interpolation grid produced by `build_grid`,
                        used to predict "where should the beam land".
        n_points:       number of verification aims (default 3).
        avoid_samples:  iterable of (pan, tilt) already used for fit —
                        verification points stay at least 0.05 (≈5 %) in
                        pan/tilt space away from any of them.

    Returns a list of dicts:
        [{"pan": ..., "tilt": ...,
          "expectedPixel": [x, y], "detectedPixel": [x, y] | None,
          "errorPx": float | None}, ...]
    """
    pans = grid.get("panSteps") or []
    tilts = grid.get("tiltSteps") or []
    if len(pans) < 2 or len(tilts) < 2:
        return []
    pan_min, pan_max = min(pans), max(pans)
    tilt_min, tilt_max = min(tilts), max(tilts)
    # Shrink inward 10 % so we don't probe the grid edge where
    # interpolation is weakest.
    margin_p = 0.1 * (pan_max - pan_min)
    margin_t = 0.1 * (tilt_max - tilt_min)

    avoid = list(avoid_samples or [])

    rng = random.Random(0x5A1ED)  # deterministic for reproducibility
    candidates = []
    attempts = 0
    while len(candidates) < n_points and attempts < 50:
        attempts += 1
        p = rng.uniform(pan_min + margin_p, pan_max - margin_p)
        t = rng.uniform(tilt_min + margin_t, tilt_max - margin_t)
        # Skip points near any fit sample.
        too_close = any(abs(p - ap) < 0.05 and abs(t - at) < 0.05
                         for ap, at in avoid)
        if too_close:
            continue
        candidates.append((p, t))

    dmx = _fresh_buffer()
    results = []
    for pan, tilt in candidates:
        expected = grid_lookup(grid, pan, tilt)
        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, 0.8)
        beam = _beam_detect(camera_ip, cam_idx, color, center=True)
        if beam is None:
            results.append({
                "pan": pan, "tilt": tilt,
                "expectedPixel": list(expected) if expected else None,
                "detectedPixel": None, "errorPx": None,
            })
            continue
        bx, by = beam
        err_px = None
        if expected is not None:
            err_px = math.hypot(bx - expected[0], by - expected[1])
        results.append({
            "pan": pan, "tilt": tilt,
            "expectedPixel": list(expected) if expected else None,
            "detectedPixel": [int(bx), int(by)],
            "errorPx": err_px,
        })
    return results


def warmup_sweep(bridge_ip, mover_addr, color=(0, 0, 0),
                 duration_s=30.0, progress_cb=None, abort_event=None):
    """Cycle the fixture through its pan/tilt range to warm motors/belts
    before calibration (#513). Pattern: pan 0→1→0, tilt 0→1→0, diagonal.

    - Dimmer stays 0 throughout — the warm-up is mechanical, not visual.
    - ``progress_cb(frac)`` is called once per step with frac ∈ [0, 1].
    - ``abort_event`` is a threading.Event; warmup exits early if set.

    Total wall time targets ``duration_s`` split evenly across the three
    phases with small settle pauses between DMX writes."""
    dmx = _fresh_buffer()
    phases = [
        # (axis, start, end) tuples — tilt locked to 0.5 during pan sweep, etc.
        ("pan",      0.0, 1.0),
        ("pan",      1.0, 0.0),
        ("tilt",     0.0, 1.0),
        ("tilt",     1.0, 0.0),
        ("diagonal", 0.0, 1.0),
        ("diagonal", 1.0, 0.0),
    ]
    steps_per_phase = 20
    step_sleep = max(0.05, duration_s / (len(phases) * steps_per_phase))
    total_steps = len(phases) * steps_per_phase
    step_count = 0

    for axis, a, b in phases:
        for i in range(steps_per_phase):
            if abort_event is not None and abort_event.is_set():
                return
            t = i / (steps_per_phase - 1) if steps_per_phase > 1 else 1.0
            v = a + (b - a) * t
            pan = v if axis in ("pan", "diagonal") else 0.5
            tilt = v if axis in ("tilt", "diagonal") else 0.5
            _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=0)
            _hold_dmx(bridge_ip, dmx, step_sleep)
            step_count += 1
            if progress_cb:
                try:
                    progress_cb(step_count / total_steps)
                except Exception:
                    pass


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


def _wait_settled(camera_ip, cam_idx, color, prev_pan=None, prev_tilt=None,
                  new_pan=None, new_tilt=None, center=False, threshold=50):
    """Wait until beam has stopped moving (#238). Returns (px, py) or None.

    Uses adaptive settle time: scales base wait by movement distance, then
    escalates through SETTLE_ESCALATE stages if double-capture shows drift.
    """
    # Scale base settle by angular movement distance
    if prev_pan is not None and new_pan is not None:
        dist = math.sqrt((new_pan - prev_pan) ** 2 +
                         (new_tilt - prev_tilt) ** 2)
        base = SETTLE_BASE * (1.0 + 2.0 * min(dist, 0.5))
    else:
        base = SETTLE_BASE

    for attempt, escalate in enumerate(SETTLE_ESCALATE):
        _check_cancel()
        wait = max(base, escalate)
        time.sleep(wait)
        _check_cancel()
        beam1 = _beam_detect(camera_ip, cam_idx, color, threshold, center)
        if not beam1:
            return None
        time.sleep(SETTLE_VERIFY_GAP)
        _check_cancel()
        beam2 = _beam_detect(camera_ip, cam_idx, color, threshold, center)
        if not beam2:
            return None
        dx = abs(beam1[0] - beam2[0])
        dy = abs(beam1[1] - beam2[1])
        if dx <= SETTLE_PIXEL_THRESH and dy <= SETTLE_PIXEL_THRESH:
            return ((beam1[0] + beam2[0]) // 2, (beam1[1] + beam2[1]) // 2)
        log.info("Settle attempt %d: dx=%d dy=%d (threshold=%d), escalating",
                 attempt + 1, dx, dy, SETTLE_PIXEL_THRESH)
    log.warning("Beam still moving after %d settle attempts", len(SETTLE_ESCALATE))
    return None


def _beam_detect(camera_ip, cam_idx, color=None, threshold=50, center=False):
    """Detect beam — uses local CVEngine if available, else camera HTTP (#333)."""
    # Strategy 1: Local processing via CVEngine
    if _cv_engine is not None:
        try:
            frame = _cv_engine.fetch_snapshot(camera_ip, cam_idx, timeout=5)
            r = _cv_engine.detect_beam(frame, cam_idx, color, threshold)
            if r.get("found"):
                return (r["pixelX"], r["pixelY"])
            return None
        except Exception as e:
            log.debug("Local beam detect failed, falling back to camera: %s", e)

    # Strategy 2: HTTP to camera node (legacy)
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
    """Get 3D position for a pixel — uses local CVEngine or camera HTTP (#333)."""
    # Strategy 1: Local depth via CVEngine
    if _cv_engine is not None:
        try:
            frame = _cv_engine.fetch_snapshot(camera_ip, cam_idx, timeout=15)
            depth_map, _ms = _cv_engine.estimate_depth(frame)
            h, w = frame.shape[:2]
            pt = _cv_engine.pixel_to_3d(depth_map, int(px), int(py), 60, w, h)
            if pt:
                return pt
        except Exception as e:
            log.debug("Local depth failed, falling back to camera: %s", e)

    # Strategy 2: HTTP to camera node (legacy)
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
    """Capture dark reference — uses local CVEngine or camera HTTP (#333)."""
    # Strategy 1: Local dark reference via CVEngine
    if _cv_engine is not None:
        try:
            frame = _cv_engine.fetch_snapshot(camera_ip, cam_idx, timeout=10)
            _cv_engine.set_dark_frame(cam_idx, frame)
            return True
        except Exception as e:
            log.debug("Local dark reference failed: %s", e)

    # Strategy 2: HTTP to camera node (legacy)
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

def compute_initial_aim(mover_pos, target_pos, pan_range=540, tilt_range=270,
                        mounted_inverted=False, pan_offset=None):
    """Estimate the pan/tilt to aim the mover at a target point in stage mm.

    Convention: pan=0.5 = forward (+Y), tilt=0.5 = horizontal.
    Stage coordinates: X=width, Y=depth (toward audience), Z=height.

    Args:
        mover_pos: (x, y, z) in mm
        target_pos: (x, y, z) in mm
        pan_range, tilt_range: in degrees
        mounted_inverted: True if fixture is mounted upside-down
        pan_offset: degrees to add to the geometric pan angle (#365).
            Inverted ceiling mounts typically need 180° because pan=0.5
            mechanically faces the mounting surface, not into the room.
            If None, defaults to 180 when mounted_inverted, else 0.

    Returns: (pan_norm, tilt_norm) both 0.0-1.0
    """
    dx = target_pos[0] - mover_pos[0]
    dy = target_pos[1] - mover_pos[1]
    dz = target_pos[2] - mover_pos[2]
    dist_xy = (dx*dx + dy*dy) ** 0.5

    pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(abs(dz), dist_xy)) if (dist_xy > 0.001 or abs(dz) > 0.001) else 0.0
    if dz > 0:
        tilt_deg = -tilt_deg

    if pan_offset is None:
        pan_offset = 180.0 if mounted_inverted else 0.0
    pan_norm = max(0, min(1, 0.5 + (pan_deg + pan_offset) / pan_range))
    tilt_norm = max(0, min(1, 0.5 + tilt_deg / tilt_range))
    return (pan_norm, tilt_norm)


def compute_aim_with_orientation(mover_pos, target_pos, orientation,
                                  pan_range=540, tilt_range=180):
    """Compute pan/tilt using saved fixture orientation data.

    Stage coordinate system (matches layout 3D view):
      X = stage width (stage right=0 → stage left)
      Y = depth (back wall=0 → audience)
      Z = height (floor=0 → ceiling)

    Pan rotates in the XY horizontal plane (atan2(dx, dy)).
    Tilt rotates in the vertical plane (atan2(-dz, dist_xy)).

    The orientation dict corrects for physical mounting by applying
    panOffset, tiltOffset, panSign, and tiltSign — calibrated from
    a known anchor point where beam position was verified by camera.

    Args:
        mover_pos: (x, y, z) in stage mm (from layout fixture position)
        target_pos: (x, y, z) in stage mm
        orientation: dict with panSign, tiltSign, panOffset, tiltOffset
        pan_range, tilt_range: degrees (fixture DMX range)

    Returns: (pan_norm, tilt_norm) both 0.0-1.0
    """
    dx = target_pos[0] - mover_pos[0]
    dy = target_pos[1] - mover_pos[1]  # depth toward audience
    dz = target_pos[2] - mover_pos[2]  # height (floor to ceiling)
    dist_xy = (dx * dx + dy * dy) ** 0.5

    # Geometric angles from fixture to target
    pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(-dz, dist_xy)) if (dist_xy > 0.001 or abs(dz) > 0.001) else 0.0

    # Apply orientation corrections
    pan_sign = orientation.get("panSign", 1)
    tilt_sign = orientation.get("tiltSign", -1)
    pan_offset = orientation.get("panOffset", 0.0)  # where pan=0 is in normalized space
    tilt_offset = orientation.get("tiltOffset", 0.0)  # where tilt=0 is in normalized space

    # Convert geometric angle to normalized pan/tilt
    # pan_offset is the normalized value that corresponds to "forward" (+Y)
    # pan_sign determines which direction increasing pan goes
    pan_norm = pan_offset + pan_sign * pan_deg / pan_range
    tilt_norm = tilt_offset + tilt_sign * tilt_deg / tilt_range

    # Clamp to valid range
    pan_norm = max(0.0, min(1.0, pan_norm))
    tilt_norm = max(0.0, min(1.0, tilt_norm))

    return (pan_norm, tilt_norm)


def calibrate_fixture_orientation(bridge_ip, camera_ip, cam_idx, mover_addr,
                                   mover_pos, floor_target, color=(0, 0, 255),
                                   universe=0, pan_range=540, tilt_range=180,
                                   beam_count=1):
    """Automated per-fixture orientation calibration.

    1. Discovery: spiral from geometric estimate to find beam
    2. Axis probe: tiny nudges to learn pan/tilt directions
    3. Aim at floor target: compare expected vs actual pixel
    4. Compute orientation correction offsets

    Args:
        bridge_ip: Art-Net bridge IP
        camera_ip: camera node IP
        cam_idx: camera index on the node
        mover_addr: DMX start address of the fixture
        mover_pos: (x, y, z) fixture position in stage mm
        floor_target: (x, y, z) target point on floor in stage mm
        color: (r, g, b) beam color for detection
        beam_count: 1 for single beam, 3 for 3-beam fixtures

    Returns: orientation dict or None on failure
    """
    import json
    import urllib.request

    detect_endpoint = "/beam-detect/center" if beam_count > 1 else "/beam-detect"
    step = 0.005  # small step for axis probing

    def send_dmx(pan, tilt, on=True):
        """Send DMX to position the mover."""
        dmx = _fresh_buffer()
        if on:
            _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, duration=2.5)

    def detect_beam():
        """Detect beam position using camera."""
        req = urllib.request.Request(
            f"http://{camera_ip}:5000{detect_endpoint}",
            data=json.dumps({"cam": cam_idx, "color": list(color), "threshold": 50}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=10).read())
            if r.get("found"):
                return r["pixelX"], r["pixelY"], r.get("beamCount", 1)
        except Exception as e:
            log.warning("Beam detect failed: %s", e)
        return None, None, 0

    def detect_at(pan, tilt):
        """Move to position, detect beam, return pixel coords."""
        import threading
        dmx = _fresh_buffer()
        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        t = threading.Thread(target=_hold_dmx, args=(bridge_ip, dmx, 4.0), daemon=True)
        t.start()
        import time; time.sleep(2.5)
        px, py, bc = detect_beam()
        t.join()
        return px, py, bc

    log.info("ORIENT-CAL fixture addr=%d: starting from pos=%s target=%s",
             mover_addr, mover_pos, floor_target)

    # ── Step 1: Discovery ──────────────────────────────────────────
    # Geometric estimate: start from center convention (0.5=forward)
    # Then spiral outward to find the beam
    est_pan, est_tilt = compute_initial_aim(mover_pos, floor_target, pan_range, tilt_range)
    # Raw geometric angle (degrees) from fixture toward target
    dx = floor_target[0] - mover_pos[0]
    dy = floor_target[1] - mover_pos[1]  # depth
    dz = floor_target[2] - mover_pos[2]  # height
    dist_xy = (dx * dx + dy * dy) ** 0.5
    geo_pan_deg = math.degrees(math.atan2(dx, dy)) if dist_xy > 0.001 else 0.0
    geo_tilt_deg = math.degrees(math.atan2(-dz, dist_xy))

    log.info("ORIENT-CAL: geometric estimate pan=%.3f tilt=%.3f (angles: pan=%.1f° tilt=%.1f°)",
             est_pan, est_tilt, geo_pan_deg, geo_tilt_deg)

    # Try multiple conventions — we don't know mounting orientation yet
    # Priority: upside-down (pan=0 forward, low tilt = floor) since it's common
    candidates = [
        # Upside-down mounting (most common for ceiling-mount moving heads)
        (geo_pan_deg / pan_range, 0.12),                     # pan=0 forward, tilt empirical floor
        (geo_pan_deg / pan_range, 0.10),                     # slight tilt variation
        (geo_pan_deg / pan_range, 0.15),                     # slight tilt variation
        # Standard convention
        (est_pan, est_tilt),
        (est_pan, 1.0 - est_tilt),                           # tilt inverted
        # Straight down from fixture (target directly below)
        (0.0, 0.12), (0.0, 0.10), (0.0, 0.15),
        (0.05, 0.12), (0.05, 0.10), (0.05, 0.15),
    ]
    # Normalize and deduplicate
    seen = set()
    norm_candidates = []
    for p, t in candidates:
        p = max(0.0, min(1.0, p))
        t = max(0.0, min(1.0, t))
        key = (round(p, 3), round(t, 3))
        if key not in seen:
            seen.add(key)
            norm_candidates.append((p, t))
    candidates = norm_candidates

    found_pan, found_tilt = None, None
    for cp, ct in candidates:
        log.info("ORIENT-CAL: trying pan=%.3f tilt=%.3f", cp, ct)
        px, py, bc = detect_at(cp, ct)
        if px is not None:
            found_pan, found_tilt = cp, ct
            log.info("ORIENT-CAL: FOUND at pan=%.3f tilt=%.3f → pixel (%d, %d) beams=%d",
                     cp, ct, px, py, bc)
            break

    if found_pan is None:
        # Spiral search from each candidate
        for cp, ct in candidates:
            for spiral_step in range(1, 20):
                for dp, dt in [(1,0), (0,1), (-1,0), (0,-1), (1,1), (-1,-1)]:
                    sp = max(0, min(1, cp + dp * spiral_step * 0.02))
                    st = max(0, min(1, ct + dt * spiral_step * 0.02))
                    px, py, bc = detect_at(sp, st)
                    if px is not None:
                        found_pan, found_tilt = sp, st
                        log.info("ORIENT-CAL: FOUND (spiral) at pan=%.3f tilt=%.3f → (%d,%d)",
                                 sp, st, px, py)
                        break
                if found_pan is not None:
                    break
            if found_pan is not None:
                break

    if found_pan is None:
        log.error("ORIENT-CAL: beam not found after search")
        return None

    # ── Step 2: Axis probe ─────────────────────────────────────────
    log.info("ORIENT-CAL: probing axes from pan=%.4f tilt=%.4f", found_pan, found_tilt)

    base_px, base_py, _ = detect_at(found_pan, found_tilt)
    pan_plus_px, pan_plus_py, _ = detect_at(found_pan + step, found_tilt)
    pan_minus_px, pan_minus_py, _ = detect_at(found_pan - step, found_tilt)
    tilt_plus_px, tilt_plus_py, _ = detect_at(found_pan, found_tilt + step)
    tilt_minus_px, tilt_minus_py, _ = detect_at(found_pan, found_tilt - step)

    if any(v is None for v in [base_px, pan_plus_px, pan_minus_px, tilt_plus_px, tilt_minus_px]):
        log.error("ORIENT-CAL: axis probe lost beam")
        return None

    pan_dx = pan_plus_px - pan_minus_px
    pan_dy = pan_plus_py - pan_minus_py
    tilt_dx = tilt_plus_px - tilt_minus_px
    tilt_dy = tilt_plus_py - tilt_minus_py

    pan_sign = 1 if pan_dx > 0 else -1
    tilt_sign = 1 if tilt_dy > 0 else -1

    log.info("ORIENT-CAL: pan axis dx=%+d dy=%+d (sign=%+d), tilt axis dx=%+d dy=%+d (sign=%+d)",
             pan_dx, pan_dy, pan_sign, tilt_dx, tilt_dy, tilt_sign)

    # ── Step 3: Compute orientation offset ─────────────────────────
    # geo_pan_deg and geo_tilt_deg already computed above

    # The beam was found at (found_pan, found_tilt) which corresponds to
    # the fixture physically aiming at the floor target.
    # So: found_pan = panOffset + panSign * geo_pan_deg / pan_range
    # → panOffset = found_pan - panSign * geo_pan_deg / pan_range
    pan_offset = found_pan - pan_sign * geo_pan_deg / pan_range
    tilt_offset = found_tilt - tilt_sign * geo_tilt_deg / tilt_range

    orientation = {
        "panSign": pan_sign,
        "tiltSign": tilt_sign,
        "panOffset": round(pan_offset, 4),
        "tiltOffset": round(tilt_offset, 4),
        "homePan": round(found_pan, 4),
        "homeTilt": round(found_tilt, 4),
        "panSensitivity": round(abs(pan_dx) / (2 * step), 1),
        "tiltSensitivity": round(abs(tilt_dy) / (2 * step), 1),
        "beamCount": beam_count,
        "verified": True,
        "panRange": pan_range,
        "tiltRange": tilt_range,
    }

    log.info("ORIENT-CAL: result: panOffset=%.4f tiltOffset=%.4f panSign=%+d tiltSign=%+d",
             pan_offset, tilt_offset, pan_sign, tilt_sign)

    # ── Step 4: Verify — aim at a second point ─────────────────────
    # Pick a point 500mm to the right of the floor target
    verify_target = (floor_target[0] + 500, floor_target[1], floor_target[2])
    verify_pan, verify_tilt = compute_aim_with_orientation(
        mover_pos, verify_target, orientation, pan_range, tilt_range)

    log.info("ORIENT-CAL: verify aim at %s → pan=%.4f tilt=%.4f", verify_target, verify_pan, verify_tilt)
    verify_px, verify_py, _ = detect_at(verify_pan, verify_tilt)
    if verify_px is not None:
        log.info("ORIENT-CAL: verify beam at (%d, %d) — BEAM VISIBLE", verify_px, verify_py)
        orientation["verifyPixel"] = [verify_px, verify_py]
    else:
        log.warning("ORIENT-CAL: verify beam NOT FOUND — orientation may need refinement")

    # Turn off
    send_dmx(0.5, 0.5, on=False)
    return orientation


def pan_tilt_to_ray(pan_norm, tilt_norm, pan_range=540, tilt_range=270,
                    mount_rotation_deg=None):
    """Convert normalized pan/tilt (0-1) to a unit direction vector.

    Convention (in mount-local frame): pan=0.5 = forward (+Y),
    tilt=0.5 = horizontal. Pan increases clockwise viewed from above.
    Tilt increases downward.

    If `mount_rotation_deg` is given (as `[rx, ry, rz]` in degrees,
    interpreted as intrinsic XYZ per Three.js default Euler order),
    the returned vector is rotated into stage space. Otherwise the
    mount-local vector is returned, preserving the legacy call shape.

    Stage coordinates: X=width, Y=depth (forward), Z=height (up).

    Returns: (dx, dy, dz) normalized direction vector
    """
    pan_deg = (pan_norm - 0.5) * pan_range
    tilt_deg = (tilt_norm - 0.5) * tilt_range
    pan_rad = math.radians(pan_deg)
    tilt_rad = math.radians(tilt_deg)

    # Spherical to cartesian
    # pan rotates in XY plane, tilt rotates down from horizontal
    cos_tilt = math.cos(tilt_rad)
    dx = math.sin(pan_rad) * cos_tilt
    dy = math.cos(pan_rad) * cos_tilt   # Y is forward (depth)
    dz = -math.sin(tilt_rad)            # Z is up (height), positive tilt = downward

    if mount_rotation_deg is None or (
            mount_rotation_deg[0] == 0
            and mount_rotation_deg[1] == 0
            and mount_rotation_deg[2] == 0):
        return (dx, dy, dz)

    from remote_math import euler_xyz_deg_to_matrix, matrix_vec_mul
    R = euler_xyz_deg_to_matrix(mount_rotation_deg)
    return matrix_vec_mul(R, (dx, dy, dz))


def aim_to_pan_tilt(aim_stage, mount_rotation_deg=None,
                    pan_range=540, tilt_range=270):
    """Inverse of `pan_tilt_to_ray` with mount orientation.

    Given a unit aim vector in stage coordinates and the mover's mount
    rotation (intrinsic XYZ Euler, degrees), return `(pan_norm, tilt_norm)`
    that aim the mover along that vector. Values are clipped to `[0, 1]`.

    Round-trips with `pan_tilt_to_ray` inside the mechanical range.
    """
    if mount_rotation_deg is None or (
            mount_rotation_deg[0] == 0
            and mount_rotation_deg[1] == 0
            and mount_rotation_deg[2] == 0):
        aim_mount = aim_stage
    else:
        from remote_math import (
            euler_xyz_deg_to_matrix, matrix_vec_mul, matrix_transpose,
        )
        R = euler_xyz_deg_to_matrix(mount_rotation_deg)
        aim_mount = matrix_vec_mul(matrix_transpose(R), aim_stage)

    dx, dy, dz = aim_mount
    pan_deg = math.degrees(math.atan2(dx, dy))
    horiz = math.hypot(dx, dy)
    tilt_deg = math.degrees(math.atan2(-dz, horiz))
    pan_norm = 0.5 + pan_deg / pan_range
    tilt_norm = 0.5 + tilt_deg / tilt_range
    pan_norm = max(0.0, min(1.0, pan_norm))
    tilt_norm = max(0.0, min(1.0, tilt_norm))
    return (pan_norm, tilt_norm)


def ray_surface_intersect(origin, direction, surfaces):
    """Find where a ray from origin in direction hits the nearest surface.

    Stage coordinates: X=width, Y=depth, Z=height.
    Floor is at Z=floor_z. Wall normals are in the XY plane.

    Args:
        origin: (x, y, z) in mm — fixture position
        direction: (dx, dy, dz) — unit direction vector
        surfaces: dict from surface_analyzer {floor, walls, obstacles}

    Returns: (x, y, z) intersection point in mm, or None
    """
    best_t = 1e9
    best_point = None

    # Floor: horizontal plane at z = floor_z
    floor = surfaces.get("floor")
    floor_z = floor.get("z", floor.get("y", 0)) if floor else None
    if floor_z is not None and abs(direction[2]) > 0.001:
        t = (floor_z - origin[2]) / direction[2]
        if 0 < t < best_t:
            px = origin[0] + t * direction[0]
            py = origin[1] + t * direction[1]
            pz = origin[2] + t * direction[2]
            best_t = t
            best_point = (round(px), round(py), round(pz))

    # Walls: vertical planes — normals in XY plane, use full 3-component dot (#263)
    for wall in surfaces.get("walls", []):
        n = wall["normal"]
        d = wall.get("d", 0)
        denom = n[0] * direction[0] + n[1] * direction[1] + n[2] * direction[2]
        if abs(denom) < 0.001:
            continue
        t = -(n[0] * origin[0] + n[1] * origin[1] + n[2] * origin[2] + d) / denom
        if 0 < t < best_t:
            px = origin[0] + t * direction[0]
            py = origin[1] + t * direction[1]
            pz = origin[2] + t * direction[2]
            best_t = t
            best_point = (round(px), round(py), round(pz))

    return best_point


def compute_floor_target(floor_surface, camera_pos, camera_aim):
    """Compute the center of the floor area visible to the camera.

    Stage coordinates: X=width, Y=depth, Z=height.
    Floor is at Z=floor_z.

    Args:
        floor_surface: dict from surface_analyzer with {z, extent: {xMin,xMax,yMin,yMax}}
        camera_pos: (x, y, z) mm
        camera_aim: (x, y, z) mm — aim point

    Returns: (x, y, z) in stage mm — center of visible floor area
    """
    if not floor_surface or not floor_surface.get("extent"):
        # Fallback: aim at stage center floor
        return (1500, 1500, 0)
    ext = floor_surface["extent"]
    floor_z = floor_surface.get("z", floor_surface.get("y", 0))
    # Center of detected floor
    cx = (ext["xMin"] + ext["xMax"]) / 2
    cy = (ext.get("yMin", ext.get("zMin", 0)) + ext.get("yMax", ext.get("zMax", 0))) / 2
    return (round(cx), round(cy), round(floor_z))


def discover(bridge_ip, camera_ip, mover_addr, cam_idx, color,
             other_mover_addrs=None, initial_pan=None, initial_tilt=None,
             mover_pos=None, camera_pos=None, floor_surface=None,
             universe=0, start_pan=None, start_tilt=None, max_probes=80,
             mounted_inverted=False, camera_rotation=None, camera_fov=90,
             profile=None,
             stage_depth=4000):
    """Find the first (pan, tilt) where the beam is visible to the camera.

    Aims at the floor area visible to the camera (not at the camera body). (#262)
    Falls back to geometric estimate, then to sensible defaults (forward, slightly down).

    Returns: (pan, tilt, pixelX, pixelY) or None
    """
    # Use explicit start values if provided
    if start_pan is not None:
        initial_pan = start_pan
    if start_tilt is not None:
        initial_tilt = start_tilt
    # Compute starting point: prefer floor target, then geometric estimate (#347)
    if initial_pan is None and mover_pos:
        if floor_surface and camera_pos:
            target = compute_floor_target(floor_surface, camera_pos, camera_pos)
            est_pan, est_tilt = compute_initial_aim(mover_pos, target,
                                                     mounted_inverted=mounted_inverted)
        elif camera_pos:
            # Aim at center of floor visible to camera (#347)
            cam_tilt = (camera_rotation or [15, 0, 0])[0]  # degrees below horizontal
            fov_half = camera_fov / 2
            bottom_angle = cam_tilt + fov_half  # steepest view angle
            if bottom_angle > 89:
                bottom_angle = 89
            near_y = camera_pos[1] + camera_pos[2] / math.tan(math.radians(bottom_angle))
            if cam_tilt > 0.1:
                center_y = camera_pos[1] + camera_pos[2] / math.tan(math.radians(cam_tilt))
            else:
                center_y = stage_depth
            center_y = min(center_y, stage_depth)
            target_y = near_y + (center_y - near_y) * 0.67
            floor_target = [
                (mover_pos[0] + camera_pos[0]) / 2,
                target_y,
                0]
            log.info("Floor target from camera geometry: (%.0f, %.0f, 0) cam_tilt=%.1f fov=%.0f",
                     floor_target[0], floor_target[1], cam_tilt, camera_fov)
            est_pan, est_tilt = compute_initial_aim(mover_pos, floor_target,
                                                     mounted_inverted=mounted_inverted)
            log.info("Initial aim: pan=%.3f tilt=%.3f (DMX %d,%d) inverted=%s",
                     est_pan, est_tilt, int(est_pan*255), int(est_tilt*255), mounted_inverted)
        else:
            est_pan, est_tilt = 0.5, 0.6
        initial_pan = initial_pan if initial_pan is not None else est_pan
        initial_tilt = initial_tilt if initial_tilt is not None else est_tilt
        log.info("Discovery start from layout estimate: pan=%.2f tilt=%.2f (inverted=%s)",
                 initial_pan, initial_tilt, mounted_inverted)
    else:
        initial_pan = initial_pan if initial_pan is not None else 0.5   # forward (#266)
        initial_tilt = initial_tilt if initial_tilt is not None else 0.6  # slightly down (#266)
    dmx = _fresh_buffer()
    # Black out other movers
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)

    # Turn on our mover at initial position
    pan, tilt = initial_pan, initial_tilt
    _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 1.0)  # settle for first position (reduced from 2.0)

    # Check initial position — use adaptive settle (#238)
    beam = _wait_settled(camera_ip, cam_idx, color, center=False, threshold=30)
    if beam:
        return (pan, tilt, beam[0], beam[1])

    # Phase 1: Coarse grid scan across full range (#367)
    # Covers the entire pan/tilt space quickly — finds the beam regardless
    # of fixture orientation, mount direction, or motor mapping.
    COARSE_PAN = 10   # 10 pan positions across 0.02–0.98
    COARSE_TILT = 7    # 7 tilt positions across 0.1–0.95
    prev_p, prev_t = pan, tilt
    probes = 0
    log.info("Discovery: coarse grid scan %dx%d", COARSE_PAN, COARSE_TILT)
    for ti in range(COARSE_TILT):
        t = 0.1 + (0.85 * ti / max(COARSE_TILT - 1, 1))
        for pi in range(COARSE_PAN):
            p = 0.02 + (0.96 * pi / max(COARSE_PAN - 1, 1))
            probes += 1
            _set_mover_dmx(dmx, mover_addr, p, t, *color, dimmer=255)
            _hold_dmx(bridge_ip, dmx, SETTLE)
            beam = _wait_settled(camera_ip, cam_idx, color,
                                 prev_pan=prev_p, prev_tilt=prev_t,
                                 new_pan=p, new_tilt=t, threshold=30)
            prev_p, prev_t = p, t
            if beam:
                log.info("Discovery: beam found at coarse probe %d (pan=%.2f tilt=%.2f)", probes, p, t)
                return (p, t, beam[0], beam[1])

    # Phase 2: Fine spiral from initial aim if coarse missed (#348)
    log.info("Discovery: coarse scan missed, fine spiral from (%.2f, %.2f)", initial_pan, initial_tilt)
    prev_p, prev_t = initial_pan, initial_tilt
    for radius in range(1, 12):
        positions = []
        for dp in range(-radius, radius + 1):
            for dt in range(-radius, radius + 1):
                if max(abs(dp), abs(dt)) == radius:
                    positions.append((initial_pan + dp * STEP, initial_tilt + dt * STEP))
        for p, t in positions:
            if p < 0 or p > 1 or t < 0 or t > 1:
                continue
            probes += 1
            if probes > max_probes:
                log.warning("Discovery exhausted %d probes without finding beam", max_probes)
                return None
            _set_mover_dmx(dmx, mover_addr, p, t, *color, dimmer=255)
            _hold_dmx(bridge_ip, dmx, SETTLE)
            beam = _wait_settled(camera_ip, cam_idx, color,
                                 prev_pan=prev_p, prev_tilt=prev_t,
                                 new_pan=p, new_tilt=t, threshold=30)
            prev_p, prev_t = p, t
            if beam:
                return (p, t, beam[0], beam[1])

    return None


# ── BFS Mapping ──────────────────────────────────────────────────────

def _verify_boundary(bridge_ip, camera_ip, cam_idx, mover_addr, pan, tilt,
                     color, dmx, threshold=50):
    """Flash on/off at boundary position to confirm beam truly invisible (#239).
    Returns True if the position is truly a boundary (no beam when light on)."""
    _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, SETTLE_BASE)
    beam_on = _beam_detect(camera_ip, cam_idx, color, threshold)
    return beam_on is None


def map_visible(bridge_ip, camera_ip, mover_addr, cam_idx, color,
                start_pan, start_tilt, other_mover_addrs=None,
                step=STEP, max_samples=MAX_SAMPLES, use_center=True,
                progress_cb=None, collect_3d=False, verify_boundary=False):
    """BFS explore the visible region from a known visible position.

    Light stays on, moves incrementally. Only explores from positions
    where the beam IS visible. Stops at boundaries — when beam is lost,
    that direction is recorded as a boundary and no further cells in that
    direction are explored (#239). Uses adaptive settle time (#238).

    Args:
        progress_cb: optional callable(sample_count, current_pan, current_tilt)
        collect_3d: if True, also query depth to get 3D world coords per sample
        verify_boundary: if True, flash on/off at boundaries to confirm

    Returns: (samples, boundaries) where samples is a list and boundaries is a dict.
        samples: list of (pan, tilt, pixel_x, pixel_y) or with collect_3d:
                 list of (pan, tilt, pixel_x, pixel_y, world_x, world_y, world_z)
        boundaries: {"panMin": float, "panMax": float,
                     "tiltMin": float, "tiltMax": float, "verified": bool}
    """
    dmx = _fresh_buffer()
    for addr in (other_mover_addrs or []):
        _set_mover_dmx(dmx, addr, 0.5, 0.5, 0, 0, 0, dimmer=0)
    _set_mover_dmx(dmx, mover_addr, start_pan, start_tilt, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 1.0)

    samples = []
    visited = set()
    lost = set()       # positions where beam was lost (#239)
    queue = [(start_pan, start_tilt, None, None)]  # (pan, tilt, prev_pan, prev_tilt)
    last_good = (start_pan, start_tilt)

    while queue and len(samples) < max_samples:
        pan, tilt, prev_p, prev_t = queue.pop(0)
        key = (round(pan, 3), round(tilt, 3))
        if key in visited or pan < 0 or pan > 1 or tilt < 0 or tilt > 1:
            continue
        visited.add(key)

        _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
        _hold_dmx(bridge_ip, dmx, SETTLE)  # #352 — must send DMX before detecting
        # Use adaptive settle (#238) — scales by movement distance
        beam = _wait_settled(camera_ip, cam_idx, color,
                             prev_pan=prev_p, prev_tilt=prev_t,
                             new_pan=pan, new_tilt=tilt, center=use_center,
                             threshold=30)
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
                # Explore neighbors — skip if a neighbor in this direction was already lost
                for dp, dt in [(step, 0), (-step, 0), (0, step), (0, -step)]:
                    nb = (round(pan + dp, 3), round(tilt + dt, 3))
                    if nb not in visited and nb not in lost:
                        queue.append((nb[0], nb[1], pan, tilt))
                if progress_cb:
                    progress_cb(len(samples), pan, tilt)
        else:
            # Beam lost at this position — record boundary (#239)
            lost.add(key)
            log.debug("Beam lost at pan=%.3f tilt=%.3f — boundary", pan, tilt)

    # Compute boundary box from successful samples
    if samples:
        pans = [s[0] for s in samples]
        tilts = [s[1] for s in samples]
        boundaries = {
            "panMin": round(min(pans), 3),
            "panMax": round(max(pans), 3),
            "tiltMin": round(min(tilts), 3),
            "tiltMax": round(max(tilts), 3),
            "verified": False,
        }
        # Optional boundary verification (#239)
        if verify_boundary and lost:
            verified_count = 0
            for lp, lt in list(lost)[:8]:  # verify up to 8 boundary positions
                if _verify_boundary(bridge_ip, camera_ip, cam_idx, mover_addr,
                                    lp, lt, color, dmx):
                    verified_count += 1
            boundaries["verified"] = verified_count > 0
            log.info("Boundary verification: %d/%d confirmed",
                     verified_count, min(len(lost), 8))
    else:
        boundaries = {"panMin": 0.0, "panMax": 1.0,
                      "tiltMin": 0.0, "tiltMax": 1.0, "verified": False}

    return samples, boundaries


# ── Real-space coordinate transforms (#246) ──────────────────────────

def pixel_to_stage(px, py, homography):
    """Convert pixel coordinates to stage mm using a floor-plane homography.

    The homography matrix is from solvePnP (3x3, maps pixel to stage XY on Z=0 floor).
    Returns (stage_x_mm, stage_y_mm) or None if the homography is degenerate.
    """
    import numpy as np
    H = np.array(homography).reshape(3, 3)
    pt = H @ np.array([float(px), float(py), 1.0])
    if abs(pt[2]) < 1e-10:
        return None
    pt /= pt[2]
    return (float(pt[0]), float(pt[1]))


def compute_depth_scale(marker_positions_3d, marker_positions_pixel,
                        depth_map, fov_deg, frame_w, frame_h):
    """Compute a scale factor to convert relative depth to absolute mm (#246).

    Uses known 3D distances between ArUco markers on the stage floor and their
    relative depth values from the depth model. Returns mm_per_relative_unit.
    """
    if len(marker_positions_3d) < 2 or len(marker_positions_pixel) < 2:
        return None
    import numpy as np
    # Compute real-world distances between marker pairs
    scales = []
    for i in range(len(marker_positions_3d)):
        for j in range(i + 1, len(marker_positions_3d)):
            m1 = marker_positions_3d[i]
            m2 = marker_positions_3d[j]
            real_dist = math.sqrt(
                (m1["x"] - m2["x"]) ** 2 +
                (m1["y"] - m2["y"]) ** 2 +
                (m1["z"] - m2["z"]) ** 2)
            if real_dist < 100:  # skip markers too close together
                continue
            p1 = marker_positions_pixel[i]
            p2 = marker_positions_pixel[j]
            # Get depth values at marker pixel positions
            h, w = depth_map.shape[:2]
            py1 = max(0, min(h - 1, int(p1[1])))
            px1 = max(0, min(w - 1, int(p1[0])))
            py2 = max(0, min(h - 1, int(p2[1])))
            px2 = max(0, min(w - 1, int(p2[0])))
            d1 = float(depth_map[py1, px1])
            d2 = float(depth_map[py2, px2])
            if d1 <= 0 or d2 <= 0:
                continue
            # Approximate 3D using pinhole model
            fx = (frame_w / 2.0) / math.tan(math.radians(fov_deg / 2.0))
            x1_3d = (p1[0] - frame_w / 2.0) * d1 / fx
            y1_3d = (p1[1] - frame_h / 2.0) * d1 / fx
            x2_3d = (p2[0] - frame_w / 2.0) * d2 / fx
            y2_3d = (p2[1] - frame_h / 2.0) * d2 / fx
            depth_dist = math.sqrt(
                (x1_3d - x2_3d) ** 2 +
                (y1_3d - y2_3d) ** 2 +
                (d1 - d2) ** 2)
            if depth_dist > 0:
                scales.append(real_dist / depth_dist)
    if not scales:
        return None
    return sum(scales) / len(scales)


# ── Per-fixture light mapping (#234) ─────────────────────────────────

def build_light_map(bridge_ip, camera_ip, cam_idx, mover_addr, color,
                    boundaries, stage_map_homography,
                    pan_steps=20, tilt_steps=15, progress_cb=None):
    """Sweep mover across visible area, build (pan,tilt) → (x,y,z) lookup (#234).

    For each grid point within boundaries:
      1. Move to (pan, tilt), wait for settle.
      2. Detect beam pixel position.
      3. Convert pixel to stage coords via homography.
      4. Store: (pan, tilt) → (stage_x, stage_y, stage_z=0).

    Returns:
        dict with panSteps, tiltSteps, samples (list of dicts), panMin/Max, tiltMin/Max
        or None if insufficient samples.
    """
    pmin = boundaries.get("panMin", 0.0)
    pmax = boundaries.get("panMax", 1.0)
    tmin = boundaries.get("tiltMin", 0.0)
    tmax = boundaries.get("tiltMax", 1.0)

    p_step = (pmax - pmin) / max(pan_steps - 1, 1)
    t_step = (tmax - tmin) / max(tilt_steps - 1, 1)

    dmx = _fresh_buffer()
    _set_mover_dmx(dmx, mover_addr, pmin, tmin, *color, dimmer=255)
    _hold_dmx(bridge_ip, dmx, 1.5)

    samples = []
    total = pan_steps * tilt_steps
    prev_pan, prev_tilt = pmin, tmin

    for pi in range(pan_steps):
        pan = pmin + pi * p_step
        for ti in range(tilt_steps):
            tilt = tmin + ti * t_step
            _set_mover_dmx(dmx, mover_addr, pan, tilt, *color, dimmer=255)
            beam = _wait_settled(camera_ip, cam_idx, color,
                                 prev_pan=prev_pan, prev_tilt=prev_tilt,
                                 new_pan=pan, new_tilt=tilt)
            if beam:
                px, py = beam
                stage = pixel_to_stage(px, py, stage_map_homography)
                if stage:
                    samples.append({
                        "pan": round(pan, 4), "tilt": round(tilt, 4),
                        "px": px, "py": py,
                        "stageX": round(stage[0], 1),
                        "stageY": round(stage[1], 1),
                        "stageZ": 0.0,  # floor plane
                    })
            prev_pan, prev_tilt = pan, tilt
            if progress_cb:
                progress_cb(len(samples), pi * tilt_steps + ti + 1, total)

    if len(samples) < 4:
        return None

    return {
        "panSteps": pan_steps, "tiltSteps": tilt_steps,
        "samples": samples,
        "panMin": pmin, "panMax": pmax,
        "tiltMin": tmin, "tiltMax": tmax,
        "sampleCount": len(samples),
    }


def light_map_inverse(light_map, target_x, target_y, target_z=0):
    """Inverse lookup: (x,y,z) stage coords → (pan, tilt) (#234).

    Finds the nearest sample by stage distance, then interpolates
    between nearby samples for smoother results.
    """
    samples = light_map.get("samples", [])
    if not samples:
        return None

    best_dist = float('inf')
    best_pan = None
    best_tilt = None
    # Weighted average of K nearest samples
    K = 4
    nearest = []

    for s in samples:
        dx = s["stageX"] - target_x
        dy = s["stageY"] - target_y
        dz = s["stageZ"] - target_z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        nearest.append((dist, s["pan"], s["tilt"]))

    nearest.sort(key=lambda x: x[0])
    if not nearest:
        return None

    # Inverse-distance weighted average of K nearest
    top = nearest[:K]
    if top[0][0] < 1.0:  # exact match
        return (top[0][1], top[0][2])

    w_pan = 0.0
    w_tilt = 0.0
    w_sum = 0.0
    for dist, pan, tilt in top:
        w = 1.0 / max(dist, 1.0)
        w_pan += w * pan
        w_tilt += w * tilt
        w_sum += w
    return (w_pan / w_sum, w_tilt / w_sum)


# ── Grid interpolation ───────────────────────────────────────────────

def build_grid(samples):
    """Build a regular interpolation grid from scattered samples.

    Returns dict with panSteps, tiltSteps, pixelX (2D), pixelY (2D),
    or None if insufficient samples.
    """
    if len(samples) < 2:
        return None

    import numpy as np
    pans = sorted(set(round(s[0], 3) for s in samples))
    tilts = sorted(set(round(s[1], 3) for s in samples))

    if len(pans) < 2 or len(tilts) < 2:
        return None

    # Build lookup: (pan, tilt) → (px, py)
    lookup = {}
    for s in samples:  # supports 4-tuple or 7-tuple (#264)
        p, t, px, py = s[0], s[1], s[2], s[3]
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
    Allows extrapolation up to 20% beyond grid bounds (#371).
    """
    pans = grid["panSteps"]
    tilts = grid["tiltSteps"]
    # Allow 20% extrapolation beyond grid bounds
    pan_range = pans[-1] - pans[0] if len(pans) > 1 else 0.2
    tilt_range = tilts[-1] - tilts[0] if len(tilts) > 1 else 0.2
    pan_lo = pans[0] - pan_range * 0.2
    pan_hi = pans[-1] + pan_range * 0.2
    tilt_lo = tilts[0] - tilt_range * 0.2
    tilt_hi = tilts[-1] + tilt_range * 0.2
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
        pan = max(pan_lo, min(pan_hi, pan + d_pan))
        tilt = max(tilt_lo, min(tilt_hi, tilt + d_tilt))

    # Final clamp to valid DMX range
    return (max(0.0, min(1.0, pan)), max(0.0, min(1.0, tilt)))


def affine_stage_point(samples, pan, tilt):
    """Forward affine fit: (pan, tilt) → (stageX, stageY, stageZ).

    Inverse of affine_pan_tilt. Uses the same per-fixture manual samples
    to ask "where in stage is the mover aimed at these DMX values?".
    Returns (x, y, z) or None with < 2 samples.
    """
    if len(samples) < 2:
        return None
    pts = []
    for s in samples:
        if isinstance(s, dict):
            pts.append((s["pan"], s["tilt"], s["stageX"], s["stageY"],
                        s.get("stageZ", 0)))
        else:
            pts.append((s[0], s[1], s[2], s[3], s[4] if len(s) >= 5 else 0))

    import numpy as np
    A = np.array([[p[0], p[1], 1.0] for p in pts])
    x_vals = np.array([p[2] for p in pts])
    y_vals = np.array([p[3] for p in pts])
    z_vals = np.array([p[4] for p in pts])
    try:
        x_coeffs, _, _, _ = np.linalg.lstsq(A, x_vals, rcond=None)
        y_coeffs, _, _, _ = np.linalg.lstsq(A, y_vals, rcond=None)
        z_coeffs, _, _, _ = np.linalg.lstsq(A, z_vals, rcond=None)
    except Exception:
        return None
    x = float(x_coeffs[0] * pan + x_coeffs[1] * tilt + x_coeffs[2])
    y = float(y_coeffs[0] * pan + y_coeffs[1] * tilt + y_coeffs[2])
    z = float(z_coeffs[0] * pan + z_coeffs[1] * tilt + z_coeffs[2])
    return (x, y, z)


def affine_pan_tilt(samples, target_x, target_y, target_z=0):
    """Compute pan/tilt for a stage position using affine transform from manual samples.

    Fits 3D: pan = a1*x + b1*y + c1*z + d1, tilt = a2*x + b2*y + c2*z + d2
    Falls back to 2D if samples lack Z or have < 3 samples with distinct Z.
    Works for extrapolation beyond the calibrated range (#371).

    samples: list of {pan, tilt, stageX, stageY, stageZ?} dicts (or tuples)
    Returns: (pan, tilt) or None
    """
    if len(samples) < 2:
        return None
    # Extract data — include Z if available
    pts = []
    for s in samples:
        if isinstance(s, dict):
            pts.append((s["pan"], s["tilt"], s["stageX"], s["stageY"],
                        s.get("stageZ", 0)))
        elif len(s) >= 5:
            pts.append((s[0], s[1], s[2], s[3], s[4]))
        else:
            pts.append((s[0], s[1], s[2], s[3], 0))

    import numpy as np
    # Use 3D if samples have distinct Z values, else 2D
    z_vals = set(p[4] for p in pts)
    use_3d = len(z_vals) > 1 and len(pts) >= 3

    if use_3d:
        A = np.array([[p[2], p[3], p[4], 1.0] for p in pts])
    else:
        A = np.array([[p[2], p[3], 1.0] for p in pts])
    pan_vals = np.array([p[0] for p in pts])
    tilt_vals = np.array([p[1] for p in pts])

    # Least-squares solve
    try:
        pan_coeffs, _, _, _ = np.linalg.lstsq(A, pan_vals, rcond=None)
        tilt_coeffs, _, _, _ = np.linalg.lstsq(A, tilt_vals, rcond=None)
    except Exception:
        return None

    if use_3d:
        pan = float(pan_coeffs[0] * target_x + pan_coeffs[1] * target_y +
                     pan_coeffs[2] * target_z + pan_coeffs[3])
        tilt = float(tilt_coeffs[0] * target_x + tilt_coeffs[1] * target_y +
                      tilt_coeffs[2] * target_z + tilt_coeffs[3])
    else:
        pan = float(pan_coeffs[0] * target_x + pan_coeffs[1] * target_y + pan_coeffs[2])
        tilt = float(tilt_coeffs[0] * target_x + tilt_coeffs[1] * target_y + tilt_coeffs[2])
    return (max(0.0, min(1.0, pan)), max(0.0, min(1.0, tilt)))


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
    Finds the pan/tilt that aims closest to the target 3D point.
    Converges on XY (horizontal plane: width+depth), ignores Z (height)."""
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
        # Convergence on XY only — Newton step only corrects XY (#265)
        err = ((wx - target_x)**2 + (wy - target_y)**2) ** 0.5
        if err < 10:  # within 10mm in XY plane
            break
        dp = 0.001
        wx_dp, wy_dp, wz_dp = grid_3d_lookup(grid3d, pan + dp, tilt)
        wx_dt, wy_dt, wz_dt = grid_3d_lookup(grid3d, pan, tilt + dp)
        # Jacobian: d(world)/d(pan,tilt) — use X and Y (horizontal plane)
        dwx_dp = (wx_dp - wx) / dp; dwy_dp = (wy_dp - wy) / dp
        dwx_dt = (wx_dt - wx) / dp; dwy_dt = (wy_dt - wy) / dp
        det = dwx_dp * dwy_dt - dwx_dt * dwy_dp
        if abs(det) < 0.001:
            break
        ex, ey = target_x - wx, target_y - wy
        gain = 0.4 if it < 15 else 0.15
        d_pan = (dwy_dt * ex - dwx_dt * ey) / det * gain
        d_tilt = (-dwy_dp * ex + dwx_dp * ey) / det * gain
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
    dmx = _fresh_buffer()
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
