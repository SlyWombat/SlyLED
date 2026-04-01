#!/usr/bin/env python3
"""Send ArtDMX to a target — test if bridge passes DMX through."""
import socket, struct, time, sys

target = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.4"
universe = int(sys.argv[2]) if len(sys.argv) > 2 else 0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

def build_artdmx(uni, seq, data):
    pkt = bytearray(b"Art-Net\x00")
    pkt += struct.pack("<H", 0x5000)       # opcode
    pkt += struct.pack(">H", 14)           # version
    pkt += bytes([seq & 0xFF])             # sequence
    pkt += bytes([0x00])                   # physical
    pkt += struct.pack("<H", uni & 0x7FFF) # universe
    pkt += struct.pack(">H", 512)          # length
    pkt += data[:512]
    if len(data) < 512:
        pkt += b"\x00" * (512 - len(data))
    return bytes(pkt)

print(f"Sending ArtDMX to {target}:6454 universe {universe}")
print(f"  Ch1=RED, Ch2=0, Ch3=0 for 3s...")

data = bytearray(512)
data[0] = 255  # ch1 = red full
seq = 1
start = time.time()
while time.time() - start < 3:
    pkt = build_artdmx(universe, seq, bytes(data))
    sock.sendto(pkt, (target, 6454))
    seq = (seq % 255) + 1
    time.sleep(0.025)
frames = seq - 1
print(f"  Sent {frames} frames. Light should be RED.")
time.sleep(1)

print(f"  Ch1=0, Ch2=255, Ch3=0 for 3s (GREEN)...")
data[0] = 0; data[1] = 255
start = time.time()
while time.time() - start < 3:
    pkt = build_artdmx(universe, seq, bytes(data))
    sock.sendto(pkt, (target, 6454))
    seq = (seq % 255) + 1
    time.sleep(0.025)
print(f"  Light should be GREEN.")
time.sleep(1)

print(f"  Ch1=0, Ch2=0, Ch3=255 for 3s (BLUE)...")
data[1] = 0; data[2] = 255
start = time.time()
while time.time() - start < 3:
    pkt = build_artdmx(universe, seq, bytes(data))
    sock.sendto(pkt, (target, 6454))
    seq = (seq % 255) + 1
    time.sleep(0.025)
print(f"  Light should be BLUE.")
time.sleep(1)

print(f"  Blackout...")
data = bytearray(512)
for _ in range(40):
    pkt = build_artdmx(universe, seq, bytes(data))
    sock.sendto(pkt, (target, 6454))
    seq = (seq % 255) + 1
    time.sleep(0.025)

sock.close()
print("Done. Did the light change colors?")
