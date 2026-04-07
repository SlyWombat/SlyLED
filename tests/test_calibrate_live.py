#!/usr/bin/env python3
"""
test_calibrate_live.py — Live calibration with detailed logging + screen captures.

1. Use layout positions to estimate initial aim toward camera
2. Find beam, then probe small steps to learn axis orientation
3. Save screenshots + detailed log for post-analysis
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("cal")

from mover_calibrator import (
    compute_initial_aim, _dark_reference, _beam_detect,
    _set_mover_dmx, _hold_dmx, _send_artnet,
)

BRIDGE_IP = "192.168.10.219"
CAMERA_IP = "192.168.10.235"
CAM_IDX = 1  # EMEET 90° wide FOV

# From slyled-config (2).json — positions in mm
# Light center of axis is 1670mm from floor
# Blue is best detected color (101K px vs 25K for red)
# Use blue for calibration of both movers (one at a time, other off)
MOVERS = [
    {"name": "Mover1", "addr": 14, "color": [0, 0, 255],
     "pos": (516, 1670, 26)},
    {"name": "Mover2", "addr": 1,  "color": [0, 0, 255],
     "pos": (2001, 1670, 45)},
]
CAMERA_POS = (1187, 1275, 0)

# Output directory for captures and logs
OUT_DIR = "/mnt/d/temp/calibration_debug"
os.makedirs(OUT_DIR, exist_ok=True)

_dmx = [0] * 512
_capture_idx = 0


def send():
    _send_artnet(BRIDGE_IP, 0, _dmx)

def hold(dur=0.6):
    _hold_dmx(BRIDGE_IP, _dmx, dur)

def blackout():
    global _dmx
    _dmx = [0] * 512
    hold(0.5)

def set_mover(addr, pan, tilt, r, g, b, dim=255):
    _set_mover_dmx(_dmx, addr, pan, tilt, r, g, b, dim)

def capture_frame(label):
    """Save a JPEG screenshot from the camera with a label."""
    global _capture_idx
    try:
        resp = urllib.request.urlopen(f"http://{CAMERA_IP}:5000/snapshot?cam={CAM_IDX}", timeout=10)
        data = resp.read()
        fname = f"{OUT_DIR}/{_capture_idx:04d}_{label}.jpg"
        with open(fname, "wb") as f:
            f.write(data)
        _capture_idx += 1
        return fname
    except Exception as e:
        log.warning("Capture failed: %s", e)
        return None

def beam_here(color, label=""):
    """Detect beam and save a screenshot. Returns (px, py) or None."""
    result = _beam_detect(CAMERA_IP, CAM_IDX, color, threshold=50, center=True)
    fname = capture_frame(f"beam_{label}" if label else "beam")
    if result:
        log.info("  BEAM at pixel (%d, %d) [%s]", result[0], result[1], fname or "no capture")
    else:
        log.info("  no beam [%s]", fname or "no capture")
    return result


def probe_axis(addr, color, others, base_pan, base_tilt, axis, delta=0.02):
    """Move one axis by +delta and -delta to learn which direction it moves the beam.
    Returns (direction_sign, pixel_delta) or (0, 0) if can't determine."""
    # Measure at base
    for a in others:
        set_mover(a, 0.5, 0.5, 0, 0, 0, 0)
    set_mover(addr, base_pan, base_tilt, *color, 255)
    hold(1.0)
    b0 = beam_here(color, f"probe_{axis}_base")
    if not b0:
        return (0, 0)

    # Move positive
    if axis == "pan":
        set_mover(addr, min(1, base_pan + delta), base_tilt, *color, 255)
    else:
        set_mover(addr, base_pan, min(1, base_tilt + delta), *color, 255)
    hold(0.8)
    bp = beam_here(color, f"probe_{axis}_plus")

    # Move negative
    if axis == "pan":
        set_mover(addr, max(0, base_pan - delta), base_tilt, *color, 255)
    else:
        set_mover(addr, base_pan, max(0, base_tilt - delta), *color, 255)
    hold(0.8)
    bn = beam_here(color, f"probe_{axis}_minus")

    # Restore base
    set_mover(addr, base_pan, base_tilt, *color, 255)
    hold(0.5)

    # Analyze movement
    if bp and bn:
        if axis == "pan":
            dx_plus = bp[0] - b0[0]
            dx_minus = bn[0] - b0[0]
            log.info("  %s probe: +%.2f → pixel dx=%+d, -%.2f → pixel dx=%+d",
                     axis, delta, dx_plus, delta, dx_minus)
            return (1 if dx_plus > 5 else (-1 if dx_plus < -5 else 0), dx_plus)
        else:
            dy_plus = bp[1] - b0[1]
            dy_minus = bn[1] - b0[1]
            log.info("  %s probe: +%.2f → pixel dy=%+d, -%.2f → pixel dy=%+d",
                     axis, delta, dy_plus, delta, dy_minus)
            return (1 if dy_plus > 5 else (-1 if dy_plus < -5 else 0), dy_plus)
    elif bp:
        d = (bp[0] - b0[0]) if axis == "pan" else (bp[1] - b0[1])
        log.info("  %s probe: +%.2f → delta=%+d (minus not visible)", axis, delta, d)
        return (1 if d > 5 else (-1 if d < -5 else 0), d)
    elif bn:
        d = (bn[0] - b0[0]) if axis == "pan" else (bn[1] - b0[1])
        log.info("  %s probe: -%.2f → delta=%+d (plus not visible)", axis, delta, d)
        return (-1 if d > 5 else (1 if d < -5 else 0), -d)
    else:
        log.info("  %s probe: both directions lost beam", axis)
        return (0, 0)


def run():
    log.info("=== Live Calibration with Captures ===")
    log.info("Output: %s", OUT_DIR)

    # Dark reference
    log.info("Blackout for dark reference...")
    blackout()
    time.sleep(2.0)
    _dark_reference(CAMERA_IP, cam_idx=-1)
    capture_frame("dark_reference")

    other_addrs = [m["addr"] for m in MOVERS]

    for mover in MOVERS:
        addr = mover["addr"]
        color = mover["color"]
        pos = mover["pos"]
        others = [a for a in other_addrs if a != addr]

        log.info("")
        log.info("=" * 60)
        log.info("Calibrating %s (addr %d)", mover["name"], addr)
        log.info("  Fixture position: %s", pos)
        log.info("  Camera position: %s", CAMERA_POS)

        # Geometric estimate to aim at camera
        est_pan, est_tilt = compute_initial_aim(pos, CAMERA_POS)
        log.info("  Geometric aim toward camera: pan=%.3f tilt=%.3f", est_pan, est_tilt)

        # Black out other movers
        for a in others:
            set_mover(a, 0.5, 0.5, 0, 0, 0, 0)

        # Phase 1: Discovery — start from geometric estimate
        log.info("  Phase 1: Discovery from geometric estimate...")
        set_mover(addr, est_pan, est_tilt, *color, 255)
        hold(1.5)
        beam = beam_here(color, f"{mover['name']}_initial")

        if not beam:
            # Spiral outward from estimate
            log.info("  Not visible at estimate, spiraling outward...")
            found = False
            for radius in range(1, 15):
                step = 0.03
                for dp in range(-radius, radius + 1):
                    for dt in range(-radius, radius + 1):
                        if max(abs(dp), abs(dt)) != radius:
                            continue
                        p = est_pan + dp * step
                        t = est_tilt + dt * step
                        if p < 0 or p > 1 or t < 0 or t > 1:
                            continue
                        set_mover(addr, p, t, *color, 255)
                        hold(0.6)
                        beam = beam_here(color, f"{mover['name']}_spiral_r{radius}")
                        if beam:
                            est_pan, est_tilt = p, t
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                log.info("  FAILED: beam never visible")
                continue

        log.info("  FOUND at pan=%.3f tilt=%.3f → pixel (%d, %d)",
                 est_pan, est_tilt, beam[0], beam[1])

        # Phase 2: Axis probing — learn which direction each axis moves the beam
        log.info("  Phase 2: Probing axis orientations...")
        pan_dir, pan_delta = probe_axis(addr, color, others, est_pan, est_tilt, "pan")
        tilt_dir, tilt_delta = probe_axis(addr, color, others, est_pan, est_tilt, "tilt")

        orientation = {
            "pan_increases_pixel_x": pan_dir,  # +1 = right, -1 = left
            "tilt_increases_pixel_y": tilt_dir,  # +1 = down, -1 = up
            "pan_px_per_unit": pan_delta / 0.02 if pan_delta else 0,
            "tilt_py_per_unit": tilt_delta / 0.02 if tilt_delta else 0,
        }
        log.info("  Axis orientation: %s", json.dumps(orientation, indent=4))

        # Save orientation
        orient_path = f"{OUT_DIR}/{mover['name']}_orientation.json"
        with open(orient_path, "w") as f:
            json.dump({
                "fixture": mover["name"],
                "addr": addr,
                "pos": pos,
                "found_at": {"pan": est_pan, "tilt": est_tilt},
                "beam_pixel": beam,
                "orientation": orientation,
            }, f, indent=2)
        log.info("  Saved orientation to %s", orient_path)

        # Phase 3: Controlled sweep using known axis directions
        log.info("  Phase 3: Controlled sweep (staying in camera view)...")
        samples = []
        # Start from found position, sweep in the direction that keeps beam visible
        current_pan, current_tilt = est_pan, est_tilt

        # Sweep pan in both directions from found position
        for direction in [+1, -1]:
            p = current_pan
            for i in range(20):
                p += direction * 0.03
                if p < 0 or p > 1:
                    break
                set_mover(addr, p, current_tilt, *color, 255)
                hold(0.6)
                b = beam_here(color, f"{mover['name']}_pan_{direction:+d}_{i}")
                if b:
                    samples.append({"pan": round(p, 3), "tilt": round(current_tilt, 3),
                                    "px": b[0], "py": b[1]})
                else:
                    log.info("  Pan sweep %+d lost beam at pan=%.3f", direction, p)
                    break

        # Sweep tilt in both directions
        for direction in [+1, -1]:
            t = current_tilt
            for i in range(20):
                t += direction * 0.03
                if t < 0 or t > 1:
                    break
                set_mover(addr, current_pan, t, *color, 255)
                hold(0.6)
                b = beam_here(color, f"{mover['name']}_tilt_{direction:+d}_{i}")
                if b:
                    samples.append({"pan": round(current_pan, 3), "tilt": round(t, 3),
                                    "px": b[0], "py": b[1]})
                else:
                    log.info("  Tilt sweep %+d lost beam at tilt=%.3f", direction, t)
                    break

        # Add the center sample
        samples.append({"pan": round(est_pan, 3), "tilt": round(est_tilt, 3),
                        "px": beam[0], "py": beam[1]})

        log.info("  Collected %d samples", len(samples))

        # Save all samples
        samples_path = f"{OUT_DIR}/{mover['name']}_samples.json"
        with open(samples_path, "w") as f:
            json.dump({"fixture": mover["name"], "samples": samples,
                       "orientation": orientation}, f, indent=2)
        log.info("  Saved to %s", samples_path)

    # Final: scan and report
    log.info("")
    log.info("=" * 60)
    log.info("Scanning for objects...")
    blackout()
    time.sleep(1)
    capture_frame("final_scan_dark")

    try:
        req = urllib.request.Request(
            f"http://{CAMERA_IP}:5000/scan",
            data=json.dumps({"cam": CAM_IDX, "threshold": 0.2, "resolution": 640}).encode(),
            headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        for d in r.get("detections", []):
            log.info("  %s %.0f%% at pixel (%d,%d) %dx%d",
                     d["label"], d["confidence"]*100, d["x"], d["y"], d["w"], d["h"])
    except Exception as e:
        log.error("Scan failed: %s", e)

    capture_frame("final_scan_result")

    log.info("")
    log.info("Calibration debug data saved to: %s", OUT_DIR)
    log.info("Review screenshots to verify beam positions")
    return 0


if __name__ == "__main__":
    sys.exit(run())
