#!/usr/bin/env python3
"""
test_calibrate_aim.py — Calibration that stays in the camera's visible area.

1. Dark reference once
2. Light ON, coarse sweep to find first visible position
3. From there, explore outward in small steps — if beam disappears, go back
4. Build 2D grid of (pan, tilt) → (pixel_x, pixel_y) within visible region
5. Fit per-camera mapping
6. Scan for chair, aim, verify with camera
"""

import json
import socket
import struct
import sys
import time
import urllib.request

BRIDGE_IP = "192.168.10.219"
CAMERA_IP = "192.168.10.235"
ARTNET_PORT = 6454
NUM_CAMERAS = 2
STEP = 0.05
MOVE_WAIT = 0.6  # seconds between small incremental moves

MOVERS = [
    {"name": "Mover1", "addr": 14, "color": (0, 255, 0)},
    {"name": "Mover2", "addr": 1,  "color": (255, 0, 255)},
]

_dmx = [0] * 512


def send_artnet(channels):
    header = b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
    header += b"\x00\x00" + struct.pack("<H", 0) + struct.pack(">H", len(channels))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(header + bytes(channels), (BRIDGE_IP, ARTNET_PORT))
    sock.close()


def set_mover(addr, pan, tilt, r, g, b, dimmer=255):
    base = addr - 1
    _dmx[base+0] = max(0, min(255, int(pan * 255)))
    _dmx[base+1] = max(0, min(255, int(tilt * 255)))
    _dmx[base+2] = 0
    _dmx[base+3] = dimmer
    _dmx[base+4] = 0
    _dmx[base+5] = r
    _dmx[base+6] = g
    _dmx[base+7] = b
    for i in range(8, 13):
        _dmx[base+i] = 0


def send_now(duration=None):
    if duration:
        for _ in range(max(int(duration * 20), 1)):
            send_artnet(_dmx)
            time.sleep(0.05)
    else:
        send_artnet(_dmx)


def blackout():
    global _dmx
    _dmx = [0] * 512
    send_now(0.5)


def snapshot_gray(cam=0):
    try:
        import cv2, numpy as np
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={cam}", timeout=10)
        arr = np.frombuffer(resp.read(), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img is not None else None
    except:
        return None


def find_beam(dark, light):
    try:
        import cv2, numpy as np
        if dark is None or light is None:
            return None
        diff = cv2.absdiff(light, dark)
        diff = cv2.GaussianBlur(diff, (15, 15), 0)
        _, max_val, _, _ = cv2.minMaxLoc(diff)
        if max_val < 30:  # higher threshold — must be a real bright beam
            return None
        _, binary = cv2.threshold(diff, max(30, int(max_val * 0.4)), 255, cv2.THRESH_BINARY)
        ys, xs = np.where(binary > 0)
        if len(xs) < 10:  # need a real cluster of bright pixels
            return None
        return (int(np.mean(xs)), int(np.mean(ys)))
    except:
        return None


def move_and_check(addr, pan, tilt, color, darks, prefer_cam=None):
    """Move mover to position, wait, check cameras. Returns (cam, px, py) or None."""
    set_mover(addr, pan, tilt, *color, dimmer=255)
    send_now(MOVE_WAIT)
    cams = list(range(NUM_CAMERAS))
    if prefer_cam is not None:
        cams = [prefer_cam] + [c for c in cams if c != prefer_cam]
    for cam in cams:
        light = snapshot_gray(cam)
        beam = find_beam(darks[cam], light)
        if beam:
            return (cam, beam[0], beam[1])
    return None


def discover(addr, color, darks, prefer_cam=None):
    """Find the beam with coarse steps. Prefers the widest FOV camera."""
    print("    Finding beam (light on, stepping through positions)...")
    for tilt in [0.2, 0.1, 0.3, 0.0, 0.4]:
        for pan in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            set_mover(addr, pan, tilt, *color, dimmer=255)
            send_now(MOVE_WAIT)
            # Check preferred camera first
            cams = list(range(NUM_CAMERAS))
            if prefer_cam is not None:
                cams = [prefer_cam] + [c for c in cams if c != prefer_cam]
            for cam in cams:
                light = snapshot_gray(cam)
                beam = find_beam(darks[cam], light)
                if beam:
                    print(f"    FOUND at pan={pan:.1f} tilt={tilt:.1f} → cam{cam} ({beam[0]},{beam[1]})")
                    return (pan, tilt, cam)
    return None


def explore_visible(addr, color, darks, start_pan, start_tilt, best_cam):
    """From a known visible position, explore the visible region using BFS.
    Only moves to adjacent positions. If beam lost, skips that direction.
    Returns list of (pan, tilt, cam, px, py) samples."""
    print(f"    Mapping visible region from ({start_pan:.2f}, {start_tilt:.2f}) on cam{best_cam}...")
    samples = []
    visited = set()
    queue = [(start_pan, start_tilt)]
    last_pan, last_tilt = start_pan, start_tilt

    while queue:
        pan, tilt = queue.pop(0)
        key = (round(pan, 3), round(tilt, 3))
        if key in visited or pan < 0 or pan > 1 or tilt < 0 or tilt > 1:
            continue
        visited.add(key)

        result = move_and_check(addr, pan, tilt, color, darks, prefer_cam=best_cam)
        if result:
            cam, px, py = result
            # Reject if pixel is nearly identical to a different pan/tilt
            # (means beam isn't actually there — just noise at same spot)
            is_stale = False
            if samples:
                for sp, st, sc, spx, spy in samples[-5:]:
                    if (abs(px - spx) < 15 and abs(py - spy) < 15 and
                        (abs(pan - sp) > STEP * 0.5 or abs(tilt - st) > STEP * 0.5)):
                        is_stale = True
                        break
            if not is_stale:
                samples.append((pan, tilt, cam, px, py))
                last_pan, last_tilt = pan, tilt
                for dp, dt in [(STEP, 0), (-STEP, 0), (0, STEP), (0, -STEP)]:
                    nb = (round(pan + dp, 3), round(tilt + dt, 3))
                    if nb not in visited:
                        queue.append(nb)
                if len(samples) % 5 == 0:
                    print(f"      {len(samples)} positions mapped...")
                if len(samples) >= 30:
                    print(f"      Stopping at {len(samples)} samples")
                    break
            else:
                # Stale — beam not really here, don't explore further
                pass
        else:
            # Beam lost — don't explore further in this direction
            # Move back to last known good position
            set_mover(addr, last_pan, last_tilt, *color, dimmer=255)
            send_now(MOVE_WAIT)

    print(f"    Mapped {len(samples)} visible positions")
    if samples:
        pans = [s[0] for s in samples]
        tilts = [s[1] for s in samples]
        print(f"    Range: pan=[{min(pans):.2f},{max(pans):.2f}] tilt=[{min(tilts):.2f},{max(tilts):.2f}]")
        for s in samples:
            print(f"      ({s[0]:.2f},{s[1]:.2f}) → cam{s[2]} px=({s[3]},{s[4]})")
    return samples


def fit_2d(samples, target_cam):
    """Fit pan/tilt → pixel mapping for a specific camera.
    Returns (pan_map, tilt_map) or (None, None)."""
    # Filter to samples from this camera only
    cam_samples = [(p, t, px, py) for p, t, c, px, py in samples if c == target_cam]
    if len(cam_samples) < 3:
        return None, None
    import numpy as np
    pans = np.array([s[0] for s in cam_samples])
    tilts = np.array([s[1] for s in cam_samples])
    pxs = np.array([s[2] for s in cam_samples])
    pys = np.array([s[3] for s in cam_samples])
    # Fit: pixel_x = a + b*pan + c*tilt (and same for pixel_y)
    A = np.vstack([np.ones_like(pans), pans, tilts]).T
    sol_x = np.linalg.lstsq(A, pxs, rcond=None)[0]
    sol_y = np.linalg.lstsq(A, pys, rcond=None)[0]
    return (
        {"a": sol_x[0], "bp": sol_x[1], "bt": sol_x[2]},  # px = a + bp*pan + bt*tilt
        {"a": sol_y[0], "bp": sol_y[1], "bt": sol_y[2]},  # py = a + bp*pan + bt*tilt
    )


def inverse_2d(px_fit, py_fit, target_px, target_py):
    """Invert the 2D mapping: given target pixel, find (pan, tilt).
    Solves: target_px = a + bp*pan + bt*tilt
            target_py = a + bp*pan + bt*tilt"""
    import numpy as np
    # [bp_x, bt_x] [pan ]   [target_px - a_x]
    # [bp_y, bt_y] [tilt] = [target_py - a_y]
    A = np.array([[px_fit["bp"], px_fit["bt"]],
                   [py_fit["bp"], py_fit["bt"]]])
    b = np.array([target_px - px_fit["a"], target_py - py_fit["a"]])
    try:
        sol = np.linalg.solve(A, b)
        return (max(0, min(1, float(sol[0]))), max(0, min(1, float(sol[1]))))
    except np.linalg.LinAlgError:
        return None


def run():
    print("\n=== Smart Calibration + Red Chair ===\n")

    darks = {}
    print("Capturing dark frames...")
    blackout()
    time.sleep(0.5)
    for cam in range(NUM_CAMERAS):
        darks[cam] = snapshot_gray(cam)

    # Determine widest FOV camera — use the per-camera FOV from the node
    wide_cam = 0
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/status", timeout=5)
        status = json.loads(resp.read().decode())
        cams = status.get("cameras", [])
        if len(cams) >= 2:
            # Pick camera with larger resolution width (proxy for wider FOV)
            # or use fovDeg if available
            fovs = [(i, c.get("fovDeg", 60), c.get("resW", 640)) for i, c in enumerate(cams)]
            wide_cam = max(fovs, key=lambda x: (x[1], x[2]))[0]
        print(f"  Using cam {wide_cam} as primary (widest FOV)")
    except:
        pass

    calibrations = {}

    for mover in MOVERS:
        addr = mover["addr"]
        color = mover["color"]
        print(f"\n{'='*50}")
        print(f"Calibrating {mover['name']} (addr {addr})")
        print(f"{'='*50}")

        # Other movers off
        for m in MOVERS:
            if m["addr"] != addr:
                set_mover(m["addr"], 0.5, 0.5, 0, 0, 0, dimmer=0)

        # Find the beam — prefer widest FOV camera
        found = discover(addr, color, darks, prefer_cam=wide_cam)
        if not found:
            print("  SKIP: beam never visible")
            calibrations[addr] = None
            continue

        start_pan, start_tilt, best_cam = found

        # Explore the visible region
        samples = explore_visible(addr, color, darks, start_pan, start_tilt, best_cam)

        # Fit per-camera
        px_fit, py_fit = fit_2d(samples, best_cam)
        # Also try other cameras
        for cam in range(NUM_CAMERAS):
            if cam == best_cam:
                continue
            px2, py2 = fit_2d(samples, cam)
            if px2 and not px_fit:
                px_fit, py_fit = px2, py2
                best_cam = cam

        # Get bounds
        visible_pans = [s[0] for s in samples]
        visible_tilts = [s[1] for s in samples]
        bounds = (min(visible_pans), max(visible_pans), min(visible_tilts), max(visible_tilts))

        calibrations[addr] = {
            "px_fit": px_fit, "py_fit": py_fit,
            "cam": best_cam, "bounds": bounds, "sample_count": len(samples)
        }
        if px_fit and py_fit:
            print(f"  Fit on cam{best_cam}: px = {px_fit['a']:.0f} + {px_fit['bp']:.0f}*pan + {px_fit['bt']:.0f}*tilt")
            print(f"                        py = {py_fit['a']:.0f} + {py_fit['bp']:.0f}*pan + {py_fit['bt']:.0f}*tilt")
        print(f"  Bounds: pan=[{bounds[0]:.2f},{bounds[1]:.2f}] tilt=[{bounds[2]:.2f},{bounds[3]:.2f}]")

    # Turn off everything
    blackout()
    time.sleep(1)

    # Scan for chair
    print(f"\n{'='*50}")
    print("Scanning for chair...")
    print(f"{'='*50}")
    best_target = None
    # Scan on calibration camera for consistent pixel space
    scan_order = [wide_cam] + [c for c in range(NUM_CAMERAS) if c != wide_cam]
    for cam in scan_order:
        try:
            req = urllib.request.Request(
                f"http://{CAMERA_IP}:5000/scan",
                data=json.dumps({"cam": cam, "threshold": 0.3, "resolution": 320}).encode(),
                headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            if r.get("ok"):
                dets = r.get("detections", [])
                # Prefer ball, then chair, then anything
                for d in dets:
                    d["_cam"] = cam
                balls = [d for d in dets if d.get("label") == "sports ball"]
                chairs = [d for d in dets if d.get("label") == "chair"]
                targets = balls or chairs or dets
                print(f"  Cam {cam}: {len(dets)} detections ({len(balls)} balls, {len(chairs)} chairs)")
                for t in targets:
                    if not best_target or t["confidence"] > best_target["confidence"]:
                        best_target = t
        except Exception as e:
            print(f"  Cam {cam}: failed ({e})")

    if not best_target:
        print("  No detections. Aborting.")
        return 1

    chair_px = best_target["x"] + best_target["w"] / 2
    chair_py = best_target["y"] + best_target["h"] / 2
    chair_cam = best_target["_cam"]
    print(f"\n  Target: {best_target['label']} {best_target['confidence']:.0%} on cam{chair_cam} at pixel ({chair_px:.0f}, {chair_py:.0f})")

    # Aim
    print(f"\n{'='*50}")
    print("Initial aim at chair")
    print(f"{'='*50}")

    aimed_values = {}
    for mover in MOVERS:
        addr = mover["addr"]
        cal = calibrations.get(addr)
        if cal and cal.get("px_fit") and cal.get("py_fit"):
            cal_cam = cal["cam"]
            if cal_cam == chair_cam:
                tpx, tpy = chair_px, chair_py
            else:
                tpx, tpy = chair_px, chair_py
            pt = inverse_2d(cal["px_fit"], cal["py_fit"], tpx, tpy)
            if pt:
                pan, tilt = pt
                bounds = cal["bounds"]
                pan = max(bounds[0], min(bounds[1], pan))
                tilt = max(bounds[2], min(bounds[3], tilt))
                method = f"calibrated (cam{cal_cam}, {cal['sample_count']} pts)"
            else:
                pan, tilt = 0.5, 0.25
                method = "inverse failed"
        else:
            pan, tilt = 0.5, 0.25
            method = "uncalibrated"

        aimed_values[addr] = (pan, tilt)
        print(f"  {mover['name']}: pan={pan:.3f} ({int(pan*255)}) tilt={tilt:.3f} ({int(tilt*255)}) [{method}]")

    # ── Convergence test ─────────────────────────────────────────
    # Check if both beams land at the same spot. If not, adjust.
    if len(MOVERS) >= 2:
        print(f"\n{'='*50}")
        print("Convergence test — verifying both beams aim at same point")
        print(f"{'='*50}")

        # Capture dark reference
        blackout()
        time.sleep(0.5)
        conv_dark = snapshot_gray(wide_cam)

        # Check each mover individually — where does its beam actually land?
        beam_positions = {}
        for mover in MOVERS:
            addr = mover["addr"]
            cal = calibrations.get(addr)
            if not cal or not cal.get("px_fit"):
                continue
            # Get this mover's aimed pan/tilt
            pan_aimed = aimed_values.get(addr, (0.5, 0.25))
            # Turn on just this mover
            global _dmx
            _dmx = [0] * 512
            set_mover(addr, pan_aimed[0], pan_aimed[1], 255, 255, 255, dimmer=255)
            send_now(1.5)
            light = snapshot_gray(wide_cam)
            beam = find_beam(conv_dark, light)
            blackout()
            time.sleep(0.3)
            if beam:
                beam_positions[addr] = beam
                print(f"  {mover['name']}: beam at pixel ({beam[0]}, {beam[1]})")
            else:
                print(f"  {mover['name']}: beam not visible on cam{wide_cam}")

        # Check convergence
        if len(beam_positions) >= 2:
            addrs = list(beam_positions.keys())
            b1 = beam_positions[addrs[0]]
            b2 = beam_positions[addrs[1]]
            dist = ((b1[0]-b2[0])**2 + (b1[1]-b2[1])**2) ** 0.5
            print(f"\n  Beam separation: {dist:.0f} pixels")
            if dist > 30:
                print(f"  Too far apart — nudging {MOVERS[1]['name']} to match {MOVERS[0]['name']}...")
                # Iteratively adjust Mover2 toward Mover1's beam position
                target_px, target_py = b1
                adj_addr = addrs[1]
                adj_cal = calibrations[adj_addr]
                current_pan, current_tilt = aimed_values.get(adj_addr, (0.5, 0.25))
                for attempt in range(8):
                    # Compute correction: which direction to nudge pan/tilt?
                    adj_beam = beam_positions.get(adj_addr)
                    if not adj_beam:
                        break
                    err_x = target_px - adj_beam[0]
                    err_y = target_py - adj_beam[1]
                    # Use the calibration's derivative to estimate pan/tilt adjustment
                    px_fit = adj_cal["px_fit"]
                    py_fit = adj_cal["py_fit"]
                    # dpx/dpan = bp of px_fit, dpx/dtilt = bt of px_fit
                    # We need: [dpan, dtilt] that moves pixel by [err_x, err_y]
                    import numpy as np
                    J = np.array([[px_fit["bp"], px_fit["bt"]],
                                  [py_fit["bp"], py_fit["bt"]]])
                    try:
                        delta = np.linalg.solve(J, np.array([err_x, err_y]))
                        damp = 0.15  # small steps to avoid overshoot
                        nudge_pan = float(delta[0]) * damp
                        nudge_tilt = float(delta[1]) * damp
                    except:
                        break
                    current_pan = max(0, min(1, current_pan + nudge_pan))
                    current_tilt = max(0, min(1, current_tilt + nudge_tilt))
                    # Test the nudged position
                    _dmx = [0] * 512
                    set_mover(adj_addr, current_pan, current_tilt, 255, 255, 255, dimmer=255)
                    send_now(1.0)
                    light = snapshot_gray(wide_cam)
                    new_beam = find_beam(conv_dark, light)
                    blackout()
                    time.sleep(0.3)
                    if new_beam:
                        beam_positions[adj_addr] = new_beam
                        new_dist = ((target_px-new_beam[0])**2 + (target_py-new_beam[1])**2) ** 0.5
                        print(f"    Attempt {attempt+1}: pan={current_pan:.3f} tilt={current_tilt:.3f} → ({new_beam[0]},{new_beam[1]}) dist={new_dist:.0f}px")
                        if new_dist < 20:
                            print(f"  Converged! Beams within {new_dist:.0f}px")
                            aimed_values[adj_addr] = (current_pan, current_tilt)
                            break
                    else:
                        print(f"    Attempt {attempt+1}: beam lost, reverting")
                        current_pan -= nudge_pan
                        current_tilt -= nudge_tilt
            else:
                print(f"  Beams already converged (within {dist:.0f}px)")

    # Final aim with RED
    print(f"\n{'='*50}")
    print("Final aim — RED on chair")
    print(f"{'='*50}")
    _dmx = [0] * 512
    for mover in MOVERS:
        addr = mover["addr"]
        pt = aimed_values.get(addr, (0.5, 0.25))
        print(f"  {mover['name']}: pan={pt[0]:.3f} ({int(pt[0]*255)}) tilt={pt[1]:.3f} ({int(pt[1]*255)})")
        set_mover(addr, pt[0], pt[1], 255, 0, 0, dimmer=255)

    send_now(2.0)

    # Verify
    print("\n  Verifying...")
    for cam in range(NUM_CAMERAS):
        try:
            req = urllib.request.Request(
                f"http://{CAMERA_IP}:5000/scan",
                data=json.dumps({"cam": cam, "threshold": 0.2, "resolution": 320}).encode(),
                headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            if r.get("ok"):
                for d in r.get("detections", []):
                    if d.get("label") == "chair":
                        print(f"    Cam {cam}: chair at ({d['x']},{d['y']}) {d['confidence']:.0%}")
        except:
            pass

    print("\n  RED on chair — 30 seconds (Ctrl+C to stop)...")
    try:
        for _ in range(600):
            send_artnet(_dmx)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    print("  Blackout.")
    blackout()
    return 0


if __name__ == "__main__":
    sys.exit(run())
