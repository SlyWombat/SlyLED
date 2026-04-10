#!/usr/bin/env python3
"""
test_red_chair.py — Send REAL Art-Net to aim moving heads at the detected chair in RED.

This sends actual DMX packets to the Giga bridge at 192.168.10.219.
Moving heads:
  - Mover 1: Universe 1, Start addr 14, 13ch
  - Mover 2: Universe 1, Start addr 1, 13ch

Channel layout (generic mini moving head 13ch):
  0: Pan (8-bit or 16-bit coarse)
  1: Pan fine
  2: Tilt (8-bit or 16-bit coarse)
  3: Tilt fine
  4: Speed (0=fast, 255=slow)
  5: Dimmer
  6: Strobe (0=open)
  7: Red
  8: Green
  9: Blue
  10: White
  11: Color wheel (0=open)
  12: Gobo (0=open)

Usage:
    python tests/test_red_chair.py
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

# Moving head positions from config (mm)
MOVER1 = {"addr": 14, "x": 1500, "y": 1350, "z": 800}
MOVER2 = {"addr": 1,  "x": 945,  "y": 1335, "z": 0}

# Stage: 3000 x 1500mm
STAGE_W = 3000
STAGE_H = 1500


def send_artnet(universe, channels):
    """Send an ArtDMX packet."""
    # Art-Net header
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)  # ArtDMX
    proto_ver = struct.pack(">H", 14)
    sequence = b"\x00"
    physical = b"\x00"
    uni = struct.pack("<H", universe)
    length = struct.pack(">H", len(channels))
    packet = header + opcode + proto_ver + sequence + physical + uni + length + bytes(channels)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(packet, (BRIDGE_IP, ARTNET_PORT))
    sock.close()


def compute_pan_tilt(fx_pos, target, pan_range=540, tilt_range=270):
    """Compute normalized pan/tilt (0-1) from fixture position to target."""
    import math
    dx = target[0] - fx_pos[0]
    dy = target[1] - fx_pos[1]
    dz = target[2] - fx_pos[2]
    dist_xz = math.sqrt(dx * dx + dz * dz)
    pan_deg = math.degrees(math.atan2(dx, dz)) if dist_xz > 0.001 else 0.0
    tilt_deg = math.degrees(math.atan2(-dy, dist_xz)) if (dist_xz > 0.001 or abs(dy) > 0.001) else 0.0
    pan_norm = 0.5 + pan_deg / pan_range
    tilt_norm = 0.5 + tilt_deg / tilt_range
    return (max(0.0, min(1.0, pan_norm)), max(0.0, min(1.0, tilt_norm)))


def set_mover(channels, addr, pan, tilt, r, g, b, dimmer=255):
    """Set Slymovehead 13-channel moving head in the DMX buffer.
    Profile loaded from community library / slyled-profiles backup.
    Channel layout: pan(8) tilt(8) speed dimmer strobe R G B W UV goboRot gobo macro
    """
    base = addr - 1  # 0-indexed
    # Pan 8-bit
    channels[base + 0] = max(0, min(255, int(pan * 255)))
    # Tilt 8-bit
    channels[base + 1] = max(0, min(255, int(tilt * 255)))
    # Speed: 0 = fastest
    channels[base + 2] = 0
    # Dimmer
    channels[base + 3] = dimmer
    # Strobe: 0 = open (no strobe)
    channels[base + 4] = 0
    # RGB
    channels[base + 5] = r
    channels[base + 6] = g
    channels[base + 7] = b
    # White: 0
    channels[base + 8] = 0
    # UV: 0
    channels[base + 9] = 0
    # Gobo rotation: 0
    channels[base + 10] = 0
    # Gobo: 0 = open
    channels[base + 11] = 0
    # Macro: 0
    channels[base + 12] = 0


def run():
    print("\n=== Red Chair Test — LIVE DMX ===\n")

    # Step 1: Scan for the chair
    print("Step 1: Scanning camera for chair...")
    try:
        req = urllib.request.Request(
            f"http://{CAMERA_IP}:5000/scan",
            data=json.dumps({"cam": 0, "threshold": 0.3, "resolution": 320}).encode(),
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        scan = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Camera scan failed: {e}")
        return 1

    dets = scan.get("detections", [])
    print(f"  Detections: {len(dets)}")
    for d in dets:
        print(f"    {d['label']} {d['confidence']:.0%} at pixel ({d['x']},{d['y']}) {d['w']}x{d['h']}")

    chairs = [d for d in dets if d.get("label") == "chair"]
    if not chairs:
        print("  No chair detected! Using first detection or center.")
        if dets:
            target_det = dets[0]
        else:
            print("  No detections at all. Aiming at stage center.")
            target_det = {"x": 320, "y": 240, "w": 100, "h": 100, "label": "center"}
    else:
        target_det = chairs[0]
        print(f"  Best chair: {target_det['confidence']:.0%} confidence")

    # Step 2: Convert pixel to stage coords (simple linear mapping)
    frame_w = scan.get("frameSize", [640, 480])[0]
    frame_h = scan.get("frameSize", [640, 480])[1]
    # Center of bounding box
    px = target_det["x"] + target_det["w"] / 2
    py = target_det["y"] + target_det["h"] / 2
    # Simple proportional mapping (rough — camera at back of stage looking forward)
    stage_x = (px / frame_w) * STAGE_W
    stage_z = (1 - py / frame_h) * STAGE_H  # flip Y
    target = [stage_x, 0, stage_z]
    print(f"\n  Target stage position: x={stage_x:.0f}mm z={stage_z:.0f}mm")

    # Step 3: Compute pan/tilt for each mover
    print("\nStep 2: Computing pan/tilt...")
    # Slymovehead: panRange=540, tiltRange=180
    pt1 = compute_pan_tilt([MOVER1["x"], MOVER1["y"], MOVER1["z"]], target, 540, 180)
    pt2 = compute_pan_tilt([MOVER2["x"], MOVER2["y"], MOVER2["z"]], target, 540, 180)
    print(f"  Mover 1 (addr {MOVER1['addr']}): pan={pt1[0]:.3f} tilt={pt1[1]:.3f}")
    print(f"  Mover 2 (addr {MOVER2['addr']}): pan={pt2[0]:.3f} tilt={pt2[1]:.3f}")

    # Step 4: Send Art-Net packets — RED color, full brightness
    print("\nStep 3: Sending Art-Net — RED on chair...")
    dmx = [0] * 512

    set_mover(dmx, MOVER1["addr"], pt1[0], pt1[1], r=255, g=0, b=0, dimmer=255)
    set_mover(dmx, MOVER2["addr"], pt2[0], pt2[1], r=255, g=0, b=0, dimmer=255)

    # Send multiple times to ensure reception
    for i in range(10):
        send_artnet(0, dmx)
        time.sleep(0.05)

    print(f"\n  SENT! Both movers aimed at {target_det.get('label', 'target')} in RED")
    print(f"  Mover 1: pan={int(pt1[0]*255)} tilt={int(pt1[1]*255)} @ addr {MOVER1['addr']}")
    print(f"  Mover 2: pan={int(pt2[0]*255)} tilt={int(pt2[1]*255)} @ addr {MOVER2['addr']}")

    # Keep sending for 30 seconds so the lights stay on
    print("\n  Holding for 30 seconds (Ctrl+C to stop)...")
    try:
        for _ in range(600):  # 30s at 20fps
            send_artnet(0, dmx)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    # Blackout
    print("  Blackout.")
    dmx = [0] * 512
    for _ in range(5):
        send_artnet(0, dmx)
        time.sleep(0.05)

    return 0


if __name__ == "__main__":
    sys.exit(run())
