#!/usr/bin/env python3
"""Test sending ArtDMX from port 6454 (same as Art-Net standard)."""
import socket, struct, time, urllib.request, json

target = "192.168.10.219"

# Bind to port 6454 (Art-Net standard — sender should use this)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
try:
    sock.bind(("192.168.10.174", 6454))
    print("Bound to 192.168.10.174:6454")
except OSError as e:
    sock.bind(("192.168.10.174", 0))
    print(f"Could not bind 6454 ({e}), using ephemeral port {sock.getsockname()[1]}")

data = bytearray(512)
data[0] = 255  # ch1

pkt = bytearray(b"Art-Net\x00")
pkt += struct.pack("<H", 0x5000)
pkt += struct.pack(">H", 14)
pkt += bytes([1, 0])  # seq, physical
pkt += struct.pack("<H", 0)  # universe 0
pkt += struct.pack(">H", 512)
pkt += bytes(data)

print(f"Sending 100 ArtDMX frames from :{sock.getsockname()[1]} to {target}:6454...")
for i in range(100):
    pkt[12] = (i + 1) & 0xFF
    sock.sendto(bytes(pkt), (target, 6454))
    time.sleep(0.025)

time.sleep(1)

try:
    r = urllib.request.urlopen(f"http://{target}/dmx/channels", timeout=3)
    d = json.loads(r.read())
    print(f"artnetRx={d.get('artnetRx')}, pps={d.get('artnetPps')}, sender={d.get('artnetSender')}")
    print(f"ch1-6={d.get('ch', [])[0:6]}")
except Exception as e:
    print(f"HTTP error: {e}")

sock.close()
