#!/usr/bin/env python3
"""
test_calibrate_flash.py — Full calibration in REAL SPACE coordinates.

All data stored in stage mm. Camera is just the observation tool.
Pipeline: point cloud → floor target → flash detect → depth → 3D sample → grid → aim.
"""

import json
import os
import socket
import struct
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import cv2
import numpy as np

from mover_calibrator import (
    compute_initial_aim, compute_floor_target,
    build_grid, grid_inverse, grid_lookup,
    build_grid_3d, grid_3d_inverse,
    pan_tilt_to_ray, ray_surface_intersect,
    _set_mover_dmx, _hold_dmx, _send_artnet,
)

BRIDGE_IP = "192.168.10.219"
CAMERA_IP = "192.168.10.235"
CAM_IDX = 1  # EMEET 90°

MOVERS = [
    {"name": "Mover1", "addr": 14, "color": [0, 0, 255], "pos": (516, 1670, 26)},
    {"name": "Mover2", "addr": 1,  "color": [0, 0, 255], "pos": (2001, 1670, 45)},
]

OUT_DIR = "/mnt/d/temp/calibration_realspace"
os.makedirs(OUT_DIR, exist_ok=True)
_dmx = [0] * 512


def send():
    _send_artnet(BRIDGE_IP, 0, _dmx)

def hold(dur=1.0):
    _hold_dmx(BRIDGE_IP, _dmx, dur)

def blackout():
    global _dmx
    _dmx = [0] * 512
    hold(0.5)


def flash_detect_pixel(cam_idx, color, mover_addr, pan, tilt):
    """Flash detection → confirmed pixel position where beam lands.
    Returns (pixel_x, pixel_y, area) or None."""
    global _dmx

    # Capture ON frame
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={cam_idx}", timeout=10)
        on_data = resp.read()
    except:
        return None

    # Turn light OFF
    _set_mover_dmx(_dmx, mover_addr, pan, tilt, 0, 0, 0, 0)
    hold(0.5)

    # Capture OFF frame
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={cam_idx}", timeout=10)
        off_data = resp.read()
    except:
        return None

    frame_on = cv2.imdecode(np.frombuffer(on_data, np.uint8), cv2.IMREAD_COLOR)
    frame_off = cv2.imdecode(np.frombuffer(off_data, np.uint8), cv2.IMREAD_COLOR)
    if frame_on is None or frame_off is None:
        return None

    # Blue color filter
    hsv_on = cv2.cvtColor(frame_on, cv2.COLOR_BGR2HSV)
    hsv_off = cv2.cvtColor(frame_off, cv2.COLOR_BGR2HSV)
    lo, hi = np.array([100, 60, 80]), np.array([130, 255, 255])
    diff = cv2.subtract(cv2.inRange(hsv_on, lo, hi), cv2.inRange(hsv_off, lo, hi))
    diff = cv2.GaussianBlur(diff, (15, 15), 0)
    _, peak, _, _ = cv2.minMaxLoc(diff)
    if peak < 25:
        return None

    _, binary = cv2.threshold(diff, max(25, int(peak * 0.4)), 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(best)
    if area < 50:
        return None
    M = cv2.moments(best)
    if M["m00"] == 0:
        return None
    return (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]), int(area))


def sample_to_3d(pan, tilt, mover_pos, surfaces, pan_range=540, tilt_range=270):
    """Convert a (pan, tilt) sample to a 3D world position using ray-surface intersection.
    The ray is an arc from the fixture through the pan/tilt angle hitting the nearest surface."""
    ray_dir = pan_tilt_to_ray(pan, tilt, pan_range, tilt_range)
    hit = ray_surface_intersect(mover_pos, ray_dir, surfaces)
    return hit


def get_floor_target():
    """Get the floor center from stored point cloud analysis."""
    try:
        import sys as _s
        _s.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))
        from parent_server import app
        with app.test_client() as c:
            r = c.get('/api/space/surfaces')
            if r.status_code == 200:
                surfaces = r.get_json()
                floor = surfaces.get("floor")
                if floor:
                    return compute_floor_target(floor, (1187, 1275, 0), (1500, 0, 1500))
    except:
        pass
    # Fallback: approximate floor center
    return (1500, 2280, 2000)


def run():
    print(f"\n=== Real-Space Calibration Pipeline ===")
    print(f"Output: {OUT_DIR}\n")

    # Load surface model from previous point cloud analysis
    surfaces = None
    try:
        from parent_server import app
        with app.test_client() as c:
            r = c.get('/api/space/surfaces')
            if r.status_code == 200:
                surfaces = r.get_json()
                floor = surfaces.get("floor")
                if floor:
                    print(f"Floor detected at y={floor['y']}mm")
    except:
        pass
    if not surfaces:
        print("WARNING: No surface data — using approximate floor at y=0")
        surfaces = {"floor": {"y": 0, "normal": [0, 1, 0],
                    "extent": {"xMin": 0, "xMax": 3000, "zMin": 0, "zMax": 3000}},
                    "walls": [], "obstacles": []}

    # Floor target: center of detected floor within camera view
    floor_target = compute_floor_target(surfaces.get("floor"), (1187, 1275, 0), (1500, 0, 1500))
    print(f"Floor target: {floor_target} mm")

    other_addrs = [m["addr"] for m in MOVERS]
    calibrations = {}

    for mover in MOVERS:
        addr = mover["addr"]
        color = mover["color"]
        pos = mover["pos"]
        others = [a for a in other_addrs if a != addr]

        print(f"\n{'='*50}")
        print(f"Calibrating {mover['name']} (addr {addr})")
        print(f"  Fixture at: {pos}")
        print(f"  Aiming at floor target: {floor_target}")
        print(f"{'='*50}")

        # Compute starting pan/tilt aimed at the floor (not the camera!)
        est_pan, est_tilt = compute_initial_aim(pos, floor_target)
        print(f"  Geometric start: pan={est_pan:.3f} tilt={est_tilt:.3f}")

        # Discovery: sweep from estimate to find beam
        print("  Phase 1: Discovery...")
        found = None
        for tilt_offset in [0, 0.03, -0.03, 0.06, -0.06, 0.10, -0.10, 0.15]:
            pan = est_pan
            tilt = min(1, max(0, est_tilt + tilt_offset))

            global _dmx
            _dmx = [0] * 512
            for a in others:
                _set_mover_dmx(_dmx, a, 0.5, 0.5, 0, 0, 0, 0)
            _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
            hold(1.5)

            result = flash_detect_pixel(CAM_IDX, color, addr, pan, tilt)

            # Restore light
            _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
            hold(0.3)

            if result:
                px, py, area = result
                # Compute 3D landing point via ray-surface intersection
                hit = sample_to_3d(pan, tilt, pos, surfaces)
                if hit:
                    print(f"  FOUND at pan={pan:.3f} tilt={tilt:.3f}")
                    print(f"    pixel ({px},{py}) → world ({hit[0]},{hit[1]},{hit[2]}) mm")
                    found = (pan, tilt, px, py, hit[0], hit[1], hit[2])
                else:
                    print(f"  FOUND at pan={pan:.3f} tilt={tilt:.3f} pixel ({px},{py}) but no surface hit")
                    found = (pan, tilt, px, py, 0, 0, 0)
                break
            else:
                print(f"    tilt={tilt:.3f} → not visible")

        if not found:
            print("  FAILED — beam never visible")
            continue

        start_pan, start_tilt = found[0], found[1]

        # Phase 2: BFS mapping — flash detect → pixel → ray-surface → 3D
        print(f"  Phase 2: Mapping from ({start_pan:.3f}, {start_tilt:.3f})...")
        samples = []  # (pan, tilt, px, py, wx, wy, wz)
        visited = set()
        queue = [(start_pan, start_tilt)]
        step = 0.03
        max_samples = 25

        while queue and len(samples) < max_samples:
            pan, tilt = queue.pop(0)
            key = (round(pan, 3), round(tilt, 3))
            if key in visited or pan < 0 or pan > 1 or tilt < 0 or tilt > 1:
                continue
            visited.add(key)

            _dmx = [0] * 512
            for a in others:
                _set_mover_dmx(_dmx, a, 0.5, 0.5, 0, 0, 0, 0)
            _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
            hold(1.5)

            result = flash_detect_pixel(CAM_IDX, color, addr, pan, tilt)

            # Restore
            _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
            hold(0.3)

            if result:
                px, py, area = result
                # 3D from ray-surface (geometric, not depth)
                hit = sample_to_3d(pan, tilt, pos, surfaces)
                wx, wy, wz = hit if hit else (0, 0, 0)
                samples.append((pan, tilt, px, py, wx, wy, wz))
                print(f"    [{len(samples)}] pan={pan:.3f} tilt={tilt:.3f} → px({px},{py}) world({wx},{wy},{wz})")
                for dp, dt in [(step, 0), (-step, 0), (0, step), (0, -step)]:
                    nb = (round(pan + dp, 3), round(tilt + dt, 3))
                    if nb not in visited:
                        queue.append(nb)

        print(f"  Collected {len(samples)} real-space samples")

        if len(samples) < 4:
            print("  Insufficient samples")
            continue

        # Build 3D grid
        grid3d = build_grid_3d(samples)
        if not grid3d:
            print("  3D grid build failed")
            continue

        print(f"  Grid: pan=[{grid3d['panSteps'][0]:.2f},{grid3d['panSteps'][-1]:.2f}]"
              f" tilt=[{grid3d['tiltSteps'][0]:.2f},{grid3d['tiltSteps'][-1]:.2f}]")

        # World coordinate ranges
        wxs = [s[4] for s in samples]
        wys = [s[5] for s in samples]
        wzs = [s[6] for s in samples]
        print(f"  World range: X=[{min(wxs)},{max(wxs)}] Y=[{min(wys)},{max(wys)}] Z=[{min(wzs)},{max(wzs)}] mm")

        calibrations[addr] = {"grid3d": grid3d, "samples": samples}

        with open(f"{OUT_DIR}/{mover['name']}_cal.json", "w") as f:
            json.dump({"samples": [list(s) for s in samples]}, f, indent=2)

    # Scan for objects with 3D positions
    print(f"\n{'='*50}")
    print("Scanning for objects in 3D...")
    print(f"{'='*50}")
    blackout()
    time.sleep(1)

    # YOLO scan
    req = urllib.request.Request(
        f"http://{CAMERA_IP}:5000/scan",
        data=json.dumps({"cam": CAM_IDX, "threshold": 0.2, "resolution": 640}).encode(),
        headers={"Content-Type": "application/json"})
    scan = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    dets = scan.get("detections", [])

    # Get 3D positions via depth
    depth_points = []
    for d in dets:
        cx = int(d["x"] + d["w"] / 2)
        cy = int(d["y"] + d["h"] / 2)
        depth_points.append({"px": cx, "py": cy})

    req = urllib.request.Request(
        f"http://{CAMERA_IP}:5000/depth-map",
        data=json.dumps({"cam": CAM_IDX, "points": depth_points[:10]}).encode(),
        headers={"Content-Type": "application/json"})
    depth_r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

    for i, d in enumerate(dets[:10]):
        pt = depth_r.get("points3d", [{}])[i] if i < len(depth_r.get("points3d", [])) else {}
        d["world"] = (pt.get("x", 0), pt.get("y", 0), pt.get("z", 0))
        print(f"  {d['label']} {d['confidence']:.0%} → 3D ({d['world'][0]},{d['world'][1]},{d['world'][2]}) mm")

    if not dets:
        print("  No objects")
        blackout()
        return 1

    target = max(dets[:10], key=lambda d: d["confidence"])
    tw = target["world"]
    print(f"\n  Target: {target['label']} at world ({tw[0]},{tw[1]},{tw[2]}) mm")

    # Aim using real-space inverse
    print(f"\n{'='*50}")
    print(f"Aiming at {target['label']} in real space")
    print(f"{'='*50}")

    _dmx = [0] * 512
    colors = [[255, 0, 0], [0, 255, 0]]

    for i, mover in enumerate(MOVERS):
        addr = mover["addr"]
        cal = calibrations.get(addr)
        if not cal:
            print(f"  {mover['name']}: no calibration")
            continue

        grid3d = cal["grid3d"]
        pan, tilt = grid_3d_inverse(grid3d, tw[0], tw[1], tw[2])
        pans = grid3d["panSteps"]
        tilts = grid3d["tiltSteps"]

        # Check if target is within calibrated range
        in_range = (pans[0] <= pan <= pans[-1] and tilts[0] <= tilt <= tilts[-1])

        c = colors[i % 2]
        _set_mover_dmx(_dmx, addr, pan, tilt, *c, 255)
        status = "IN RANGE" if in_range else "CLAMPED (outside calibrated area)"
        print(f"  {mover['name']}: pan={pan:.3f} tilt={tilt:.3f} {'RED' if c[0] else 'GREEN'} [{status}]")

    print(f"\n  Lights on {target['label']} for 15s...")
    try:
        for _ in range(300):
            send()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    blackout()
    print("  Done.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
