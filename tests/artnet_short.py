#!/usr/bin/env python3
"""Send a SHORT ArtDMX packet (24 bytes) to test if buffer size is the issue."""
import socket, struct, time, urllib.request, json, sys

target = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.219"
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# SHORT ArtDMX: 18 header + 6 data = 24 bytes total (well under 508)
pkt = bytearray(b"Art-Net\x00")
pkt += struct.pack("<H", 0x5000)
pkt += struct.pack(">H", 14)
pkt += bytes([1, 0])
pkt += struct.pack("<H", 0)
pkt += struct.pack(">H", 6)       # only 6 channels
pkt += bytes([255, 0, 0, 0, 0, 0])

print(f"Short ArtDMX packet: {len(pkt)} bytes")
print(f"Sending 80 frames to {target}:6454...")
for i in range(80):
    pkt[12] = (i + 1) & 0xFF
    sock.sendto(bytes(pkt), (target, 6454))
    time.sleep(0.025)

time.sleep(1)

try:
    r = urllib.request.urlopen(f"http://{target}/dmx/channels", timeout=3)
    d = json.loads(r.read())
    rx = d.get("artnetRx", "N/A")
    sender = d.get("artnetSender", "")
    ch = d.get("ch", [])
    print(f"artnetRx={rx}, sender={sender}")
    print(f"ch1-6={ch[0:6]}")
except Exception as e:
    print(f"HTTP error: {e}")

sock.close()
