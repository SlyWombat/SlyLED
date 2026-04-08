#!/usr/bin/env python3
"""
test_calibrate_flash.py — Full calibration pipeline using flash detection.

1. Discovery: geometric estimate → flash detect to find beam
2. BFS mapping: sweep visible region using flash at each position
3. Build grid → aim at detected objects → verify
"""

import json
import os
import socket
import struct
import sys
import time
import urllib.request
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from mover_calibrator import (
    compute_initial_aim, build_grid, grid_inverse, grid_lookup,
    _set_mover_dmx, _hold_dmx, _send_artnet,
)

BRIDGE_IP = "192.168.10.219"
CAMERA_IP = "192.168.10.235"
CAM_IDX = 1  # EMEET 90°

MOVERS = [
    {"name": "Mover1", "addr": 14, "color": [0, 0, 255], "pos": (516, 1670, 26)},
    {"name": "Mover2", "addr": 1,  "color": [0, 0, 255], "pos": (2001, 1670, 45)},
]
CAMERA_AIM = (1500, 0, 1500)  # where camera looks (stage floor center)

OUT_DIR = "/mnt/d/temp/calibration_flash"
os.makedirs(OUT_DIR, exist_ok=True)
_dmx = [0] * 512
_cap_idx = 0


def send():
    _send_artnet(BRIDGE_IP, 0, _dmx)

def hold(dur=1.0):
    _hold_dmx(BRIDGE_IP, _dmx, dur)

def blackout():
    global _dmx
    _dmx = [0] * 512
    hold(0.5)

def capture(label):
    global _cap_idx
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={CAM_IDX}", timeout=10)
        fname = f"{OUT_DIR}/{_cap_idx:04d}_{label}.jpg"
        with open(fname, "wb") as f:
            f.write(resp.read())
        _cap_idx += 1
        return fname
    except:
        return None


def flash_detect(cam_idx, color, mover_addr, pan, tilt, threshold=25):
    """Synchronous flash detection:
    1. Capture ON frame (camera snapshot — light is already on)
    2. Turn light OFF via DMX
    3. Wait for head to go dark
    4. Capture OFF frame
    5. Send both to camera for diff comparison
    """
    # Capture ON frame
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={cam_idx}", timeout=10)
        on_data = resp.read()
    except:
        return None

    # Turn light OFF
    global _dmx
    _set_mover_dmx(_dmx, mover_addr, pan, tilt, 0, 0, 0, 0)
    hold(0.5)

    # Capture OFF frame
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={cam_idx}", timeout=10)
        off_data = resp.read()
    except:
        return None

    # Analyze locally using OpenCV
    try:
        import cv2
        import numpy as np
        on_arr = np.frombuffer(on_data, dtype=np.uint8)
        off_arr = np.frombuffer(off_data, dtype=np.uint8)
        frame_on = cv2.imdecode(on_arr, cv2.IMREAD_COLOR)
        frame_off = cv2.imdecode(off_arr, cv2.IMREAD_COLOR)
        if frame_on is None or frame_off is None:
            return None

        # Color filter both frames
        if color and color != [255, 255, 255]:
            hsv_on = cv2.cvtColor(frame_on, cv2.COLOR_BGR2HSV)
            hsv_off = cv2.cvtColor(frame_off, cv2.COLOR_BGR2HSV)
            # Blue filter
            if color[2] > 200:
                lo, hi = np.array([100, 60, 80]), np.array([130, 255, 255])
            elif color[1] > 200:
                lo, hi = np.array([35, 60, 80]), np.array([85, 255, 255])
            else:
                lo, hi = np.array([0, 60, 80]), np.array([12, 255, 255])
            on_mask = cv2.inRange(hsv_on, lo, hi)
            off_mask = cv2.inRange(hsv_off, lo, hi)
        else:
            on_mask = cv2.cvtColor(frame_on, cv2.COLOR_BGR2GRAY)
            off_mask = cv2.cvtColor(frame_off, cv2.COLOR_BGR2GRAY)

        diff = cv2.subtract(on_mask, off_mask)
        diff = cv2.GaussianBlur(diff, (15, 15), 0)
        _, peak, _, _ = cv2.minMaxLoc(diff)
        if peak < threshold:
            return None

        _, binary = cv2.threshold(diff, max(threshold, int(peak * 0.4)), 255, cv2.THRESH_BINARY)
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
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        return (cx, cy, int(area))
    except Exception as e:
        print(f"    Flash analyze error: {e}")
        return None


def discover(mover, others):
    """Find beam using geometric estimate + flash detection."""
    addr = mover["addr"]
    color = mover["color"]
    pos = mover["pos"]

    est_pan, est_tilt = compute_initial_aim(pos, CAMERA_AIM)
    print(f"  Geometric estimate: pan={est_pan:.3f} tilt={est_tilt:.3f}")

    # Need to sweep toward the floor — the camera sees the floor, not the ceiling
    # The geometric estimate aims at the camera, but we need to aim at where
    # the camera is LOOKING (the floor/stage area)
    # Tilt needs to be higher to point down toward the floor
    for tilt_offset in [0, 0.05, 0.10, 0.15, -0.05, 0.20, -0.10, 0.25]:
        pan = est_pan
        tilt = min(1, max(0, est_tilt + tilt_offset))

        # Light ON
        global _dmx
        _dmx = [0] * 512
        for a in others:
            _set_mover_dmx(_dmx, a, 0.5, 0.5, 0, 0, 0, 0)
        _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
        hold(1.5)

        capture(f"{mover['name']}_discover_t{tilt:.2f}")

        # Flash detect (captures ON, turns OFF, captures OFF, diffs)
        result = flash_detect(CAM_IDX, color, addr, pan, tilt)

        # Restore light for next iteration
        _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
        hold(0.5)

        if result:
            px, py, area = result
            print(f"  FOUND at pan={pan:.3f} tilt={tilt:.3f} → pixel ({px},{py}) area={area}")
            return (pan, tilt, px, py)
        else:
            print(f"  tilt={tilt:.3f} → not visible")

    return None


def map_region(mover, others, start_pan, start_tilt, step=0.03, max_samples=30):
    """BFS map the visible region using flash detection at each position."""
    addr = mover["addr"]
    color = mover["color"]
    samples = []
    visited = set()
    queue = [(start_pan, start_tilt)]

    while queue and len(samples) < max_samples:
        pan, tilt = queue.pop(0)
        key = (round(pan, 3), round(tilt, 3))
        if key in visited or pan < 0 or pan > 1 or tilt < 0 or tilt > 1:
            continue
        visited.add(key)

        # Light ON at this position
        global _dmx
        _dmx = [0] * 512
        for a in others:
            _set_mover_dmx(_dmx, a, 0.5, 0.5, 0, 0, 0, 0)
        _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
        hold(1.5)  # settle time

        # Flash detect (captures ON, turns OFF, captures OFF, diffs)
        result = flash_detect(CAM_IDX, color, addr, pan, tilt)
        if not result:
            print(f"    pan={pan:.3f} tilt={tilt:.3f} → no beam")

        # Restore light ON for next position
        _set_mover_dmx(_dmx, addr, pan, tilt, *color, 255)
        hold(0.5)

        if result:
            px, py, area = result
            # Accept all flash detections — flash is reliable, no stale rejection needed
            if True:
                samples.append((pan, tilt, px, py))
                print(f"    [{len(samples)}] pan={pan:.3f} tilt={tilt:.3f} → ({px},{py}) area={area}")
                for dp, dt in [(step, 0), (-step, 0), (0, step), (0, -step)]:
                    nb = (round(pan + dp, 3), round(tilt + dt, 3))
                    if nb not in visited:
                        queue.append(nb)
        # If not found, don't explore from here

    return samples


def run():
    print(f"\n=== Flash Calibration Pipeline ===")
    print(f"Output: {OUT_DIR}\n")

    other_addrs = [m["addr"] for m in MOVERS]
    calibrations = {}

    for mover in MOVERS:
        addr = mover["addr"]
        others = [a for a in other_addrs if a != addr]

        print(f"\n{'='*50}")
        print(f"Calibrating {mover['name']} (addr {addr})")
        print(f"{'='*50}")

        # Discovery
        print("  Phase 1: Discovery...")
        found = discover(mover, others)
        if not found:
            print("  FAILED — beam never visible")
            continue
        start_pan, start_tilt, px, py = found

        # BFS mapping
        print(f"  Phase 2: Mapping from ({start_pan:.3f}, {start_tilt:.3f})...")
        samples = map_region(mover, others, start_pan, start_tilt)
        print(f"  Collected {len(samples)} samples")

        if len(samples) < 4:
            print("  Insufficient samples")
            continue

        grid = build_grid(samples)
        if not grid:
            print("  Grid build failed")
            continue

        pans = grid["panSteps"]
        tilts = grid["tiltSteps"]
        print(f"  Grid: pan=[{pans[0]:.2f},{pans[-1]:.2f}] tilt=[{tilts[0]:.2f},{tilts[-1]:.2f}]")

        calibrations[addr] = {"grid": grid, "samples": samples}

        # Save
        with open(f"{OUT_DIR}/{mover['name']}_calibration.json", "w") as f:
            json.dump({"samples": samples, "grid": grid}, f, indent=2)

    # Scan for targets
    print(f"\n{'='*50}")
    print("Scanning for objects...")
    print(f"{'='*50}")
    blackout()
    time.sleep(1)

    req = urllib.request.Request(
        f"http://{CAMERA_IP}:5000/scan",
        data=json.dumps({"cam": CAM_IDX, "threshold": 0.2, "resolution": 640}).encode(),
        headers={"Content-Type": "application/json"})
    scan = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    dets = scan.get("detections", [])
    for d in dets:
        print(f"  {d['label']} {d['confidence']:.0%} at ({d['x']},{d['y']}) {d['w']}x{d['h']}")

    if not dets:
        print("  No objects found")
        blackout()
        return 1

    # Aim at best target
    target = max(dets, key=lambda d: d["confidence"])
    tx = target["x"] + target["w"] / 2
    ty = target["y"] + target["h"] / 2
    print(f"\n  Target: {target['label']} at pixel ({tx:.0f}, {ty:.0f})")

    print(f"\n{'='*50}")
    print(f"Aiming at {target['label']}")
    print(f"{'='*50}")

    global _dmx
    _dmx = [0] * 512
    colors = [[255, 0, 0], [0, 255, 0]]  # Red and Green for visibility

    for i, mover in enumerate(MOVERS):
        addr = mover["addr"]
        cal = calibrations.get(addr)
        if not cal:
            print(f"  {mover['name']}: no calibration")
            continue

        grid = cal["grid"]
        pan, tilt = grid_inverse(grid, tx, ty)
        pans = grid["panSteps"]
        tilts = grid["tiltSteps"]
        pan = max(pans[0], min(pans[-1], pan))
        tilt = max(tilts[0], min(tilts[-1], tilt))

        c = colors[i % 2]
        _set_mover_dmx(_dmx, addr, pan, tilt, *c, 255)
        print(f"  {mover['name']}: pan={pan:.3f} tilt={tilt:.3f} ({'RED' if c[0] else 'GREEN'})")

    print(f"\n  Lights on {target['label']} for 15s...")
    capture("final_aimed")
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
