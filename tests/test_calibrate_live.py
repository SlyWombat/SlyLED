#!/usr/bin/env python3
"""
test_calibrate_live.py — Live calibration using the new mover_calibrator engine.

Uses beam_detector on camera node for fast detection (<100ms per probe).
Calibrates both movers, scans for target, aims with convergence test.

Usage:
    python tests/test_calibrate_live.py
"""

import json
import os
import socket
import struct
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import logging
logging.basicConfig(level=logging.INFO, format='  %(message)s')

from mover_calibrator import (
    discover, map_visible, build_grid, grid_inverse, converge,
    _dark_reference, _beam_detect, _set_mover_dmx, _hold_dmx, _send_artnet,
)

BRIDGE_IP = "192.168.10.219"
CAMERA_IP = "192.168.10.235"
CAM_IDX = 1  # EMEET 90° wide FOV

MOVERS = [
    {"name": "Mover1", "addr": 14, "color": [255, 0, 0]},   # RED
    {"name": "Mover2", "addr": 1,  "color": [0, 0, 255]},   # BLUE
]


def blackout():
    dmx = [0] * 512
    _hold_dmx(BRIDGE_IP, dmx, 0.5)


def scan_for_targets():
    """Scan camera for all detected objects, sorted by priority."""
    blackout()
    time.sleep(0.5)
    priority = {"bottle": 5, "sports ball": 4, "cup": 3, "chair": 2, "person": 1}
    targets = []
    for cam in [CAM_IDX]:
        try:
            req = urllib.request.Request(
                f"http://{CAMERA_IP}:5000/scan",
                data=json.dumps({"cam": cam, "threshold": 0.2, "resolution": 640}).encode(),
                headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            if not r.get("ok"):
                continue
            for d in r.get("detections", []):
                d["_cam"] = cam
                d["_score"] = priority.get(d["label"], 1) + d["confidence"]
                targets.append(d)
        except Exception as e:
            print(f"  Scan cam {cam} failed: {e}")
    targets.sort(key=lambda d: d["_score"], reverse=True)
    return targets


def run():
    print("\n=== Live Calibration Test (mover_calibrator engine) ===\n")

    # Step 1: Dark reference
    print("Step 1: Dark reference...")
    blackout()
    time.sleep(2.0)  # let camera auto-exposure settle
    ok = _dark_reference(CAMERA_IP, cam_idx=-1)
    print(f"  Dark reference: {'OK' if ok else 'FAILED'}")
    if not ok:
        return 1

    # Step 2: Calibrate each mover
    calibrations = {}
    other_addrs = [m["addr"] for m in MOVERS]

    for mover in MOVERS:
        addr = mover["addr"]
        color = mover["color"]
        others = [a for a in other_addrs if a != addr]

        print(f"\n{'='*50}")
        print(f"Calibrating {mover['name']} (addr {addr}, color {'G' if color[1]==255 else 'M'})")
        print(f"{'='*50}")

        # Discovery
        print("  Phase 1: Discovery...")
        result = discover(BRIDGE_IP, CAMERA_IP, addr, CAM_IDX, color,
                          other_mover_addrs=others)
        if not result:
            print("  FAILED: beam never visible")
            calibrations[addr] = None
            continue
        start_pan, start_tilt, px, py = result
        print(f"  Found at pan={start_pan:.2f} tilt={start_tilt:.2f} → pixel ({px}, {py})")

        # Mapping
        print("  Phase 2: BFS mapping...")
        def progress(n, p, t):
            print(f"    {n} samples, current ({p:.2f}, {t:.2f})")

        samples = map_visible(BRIDGE_IP, CAMERA_IP, addr, CAM_IDX, color,
                              start_pan, start_tilt,
                              other_mover_addrs=others,
                              progress_cb=progress)
        print(f"  Mapped {len(samples)} positions")

        if len(samples) < 4:
            print("  Insufficient samples for grid")
            calibrations[addr] = None
            continue

        # Build grid
        grid = build_grid(samples)
        if not grid:
            print("  Grid build failed")
            calibrations[addr] = None
            continue

        pans = grid["panSteps"]
        tilts = grid["tiltSteps"]
        print(f"  Grid: pan=[{pans[0]:.2f},{pans[-1]:.2f}] ({len(pans)} steps) "
              f"tilt=[{tilts[0]:.2f},{tilts[-1]:.2f}] ({len(tilts)} steps)")

        calibrations[addr] = {"grid": grid, "samples": samples, "color": color}

    # Step 3: Blackout and scan for target
    print(f"\n{'='*50}")
    print("Step 3: Scanning for targets...")
    print(f"{'='*50}")
    targets = scan_for_targets()
    if not targets:
        print("  No targets found!")
        return 1
    for t in targets[:5]:
        print(f"  {t['label']} {t['confidence']:.0%} at ({t['x']},{t['y']}) {t['w']}x{t['h']}")

    # Step 4: Aim at each target in turn
    for target_idx, target in enumerate(targets[:3]):  # up to 3 objects
        target_px = target["x"] + target["w"] / 2
        target_py = target["y"] + target["h"] / 2
        print(f"\n{'='*50}")
        print(f"Step 4.{target_idx+1}: Aim at {target['label']} ({target_px:.0f}, {target_py:.0f})")
        print(f"{'='*50}")

        final_positions = {}
        for mover in MOVERS:
            addr = mover["addr"]
            cal = calibrations.get(addr)
            if not cal:
                print(f"  {mover['name']}: no calibration, skipping")
                continue

            grid = cal["grid"]
            color = cal["color"]
            others = [a for a in other_addrs if a != addr]

            print(f"\n  {mover['name']}: converging on {target['label']}...")
            result = converge(BRIDGE_IP, CAMERA_IP, CAM_IDX,
                              addr, grid, color,
                              target_px, target_py,
                              other_mover_addrs=others)
            if result:
                pan, tilt, dist = result
                final_positions[addr] = (pan, tilt)
                print(f"  {mover['name']}: pan={pan:.3f} ({int(pan*255)}) "
                      f"tilt={tilt:.3f} ({int(tilt*255)}) dist={dist:.0f}px")
            else:
                print(f"  {mover['name']}: convergence failed")

        # Show both on this target for 5s
        if final_positions:
            dmx = [0] * 512
            colors = [[255,0,0], [0,255,0], [0,0,255]]  # R G B
            for i, mover in enumerate(MOVERS):
                addr = mover["addr"]
                if addr in final_positions:
                    pan, tilt = final_positions[addr]
                    c = colors[i % 3]
                    _set_mover_dmx(dmx, addr, pan, tilt, *c, dimmer=255)
            print(f"\n  Both movers on {target['label']} for 5s...")
            _hold_dmx(BRIDGE_IP, dmx, 5.0)
            blackout()

    # Step 5: Multi-head convergence test on last target
    if len(final_positions) >= 2:
        print(f"\n{'='*50}")
        print("Step 5: Multi-head convergence verify")
        print(f"{'='*50}")
        # Turn on each mover one at a time in its own color, check pixel position
        blackout()
        time.sleep(0.5)
        beam_positions = {}
        for mover in MOVERS:
            addr = mover["addr"]
            if addr not in final_positions:
                continue
            pan, tilt = final_positions[addr]
            dmx = [0] * 512
            _set_mover_dmx(dmx, addr, pan, tilt, *mover["color"], dimmer=255)
            _hold_dmx(BRIDGE_IP, dmx, 1.5)
            beam = _beam_detect(CAMERA_IP, CAM_IDX, mover["color"], center=True)
            blackout()
            time.sleep(0.3)
            if beam:
                beam_positions[addr] = beam
                print(f"  {mover['name']}: beam at ({beam[0]}, {beam[1]})")
            else:
                print(f"  {mover['name']}: beam not visible")

        if len(beam_positions) >= 2:
            addrs = list(beam_positions.keys())
            b1, b2 = beam_positions[addrs[0]], beam_positions[addrs[1]]
            sep = ((b1[0]-b2[0])**2 + (b1[1]-b2[1])**2) ** 0.5
            print(f"  Beam separation: {sep:.0f}px {'OK' if sep < 50 else 'NEEDS CORRECTION'}")

    # Step 6: Final — both movers in distinct colors at target
    print(f"\n{'='*50}")
    print(f"Both movers aimed at {target['label']} — distinct colors 30s")
    print(f"{'='*50}")
    dmx = [0] * 512
    for mover in MOVERS:
        addr = mover["addr"]
        if addr in final_positions:
            pan, tilt = final_positions[addr]
            # Mover1 = RED, Mover2 = BLUE (so you can see which is which)
            if addr == MOVERS[0]["addr"]:
                _set_mover_dmx(dmx, addr, pan, tilt, 255, 0, 0, dimmer=255)
                print(f"  {mover['name']}: RED at pan={pan:.3f} tilt={tilt:.3f}")
            else:
                _set_mover_dmx(dmx, addr, pan, tilt, 0, 0, 255, dimmer=255)
                print(f"  {mover['name']}: BLUE at pan={pan:.3f} tilt={tilt:.3f}")

    print("\n  Holding 30s (Ctrl+C to stop)...")
    try:
        for _ in range(600):
            _send_artnet(BRIDGE_IP, 0, dmx)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    print("  Blackout.")
    blackout()
    return 0


if __name__ == "__main__":
    sys.exit(run())
