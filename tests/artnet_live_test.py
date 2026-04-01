#!/usr/bin/env python3
"""Send ArtDMX to Giga and check stats in real-time."""
import socket, struct, time, urllib.request, json, sys

target = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.219"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_artdmx(uni, seq, data):
    pkt = bytearray(b"Art-Net\x00")
    pkt += struct.pack("<H", 0x5000)
    pkt += struct.pack(">H", 14)
    pkt += bytes([seq & 0xFF, 0x00])
    pkt += struct.pack("<H", uni & 0x7FFF)
    pkt += struct.pack(">H", 512)
    pkt += data[:512]
    if len(data) < 512: pkt += b"\x00" * (512 - len(data))
    sock.sendto(bytes(pkt), (target, 6454))

def check_stats():
    try:
        r = urllib.request.urlopen(f"http://{target}/dmx/channels", timeout=3)
        return json.loads(r.read())
    except:
        return None

# Check before
print(f"Target: {target}")
d = check_stats()
if d:
    print(f"Before: artnetRx={d.get('artnetRx')}, ch1-3={d.get('ch',[])[0:3]}")
else:
    print("Before: could not reach bridge")

# Send 40 frames with ch1=255
print("Sending 40 ArtDMX frames (ch1=255) over 1 second...")
data = bytearray(512)
data[0] = 255
for i in range(40):
    send_artdmx(0, i+1, bytes(data))
    time.sleep(0.025)

time.sleep(0.5)

# Check after
d = check_stats()
if d:
    print(f"After:  artnetRx={d.get('artnetRx')}, pps={d.get('artnetPps')}, sender={d.get('artnetSender')}, ch1-3={d.get('ch',[])[0:3]}")
else:
    print("After: could not reach bridge")

# Also send an ArtPoll to verify the socket works
print("\nSending ArtPoll to verify socket...")
poll = b"Art-Net\x00" + struct.pack("<H", 0x2000) + struct.pack(">H", 14) + b"\x06\x00"
sock.settimeout(2)
sock.sendto(poll, (target, 6454))
try:
    resp, addr = sock.recvfrom(2048)
    if resp[:8] == b"Art-Net\x00":
        op = struct.unpack_from("<H", resp, 8)[0]
        print(f"  Got reply: opcode=0x{op:04x} from {addr[0]} ({len(resp)} bytes)")
    else:
        print(f"  Non-artnet reply from {addr}")
except socket.timeout:
    print("  No ArtPoll reply (timeout)")

sock.close()
