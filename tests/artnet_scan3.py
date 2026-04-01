#!/usr/bin/env python3
"""Art-Net scanner v3 — use ephemeral port to avoid firewall blocking 6454."""
import socket, struct, time

TARGET = "192.168.10.4"

# Use ephemeral port — reply comes back to sender port, not 6454
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.bind(("192.168.10.174", 0))  # ephemeral port
local_port = sock.getsockname()[1]
sock.settimeout(3)

pkt = bytearray(b"Art-Net\x00")
pkt += struct.pack("<H", 0x2000)
pkt += struct.pack(">H", 14)
pkt += bytes([0x06, 0x00])

print(f"Bound to 192.168.10.174:{local_port} (ephemeral)")
print(f"Sending ArtPoll unicast to {TARGET}:6454...")
sock.sendto(bytes(pkt), (TARGET, 6454))

# Also try sending TO port 6454 FROM port 6454 (standard expects this)
sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock2.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock2.bind(("192.168.10.174", 6454))
    sock2.settimeout(3)
    sock2.sendto(bytes(pkt), (TARGET, 6454))
    print(f"Also sent from :6454")
    use_sock2 = True
except OSError as e:
    print(f"Could not bind 6454: {e}")
    use_sock2 = False

print(f"Listening 5s...")
start = time.time()
while time.time() - start < 5:
    for s in ([sock, sock2] if use_sock2 else [sock]):
        try:
            data, addr = s.recvfrom(2048)
            ip = addr[0]
            if ip == "192.168.10.174":
                continue
            if data[:8] == b"Art-Net\x00" and len(data) >= 12:
                op = struct.unpack_from("<H", data, 8)[0]
                print(f"  {ip:>16} opcode=0x{op:04x} len={len(data)} from_port={addr[1]}")
                if op == 0x2100:
                    sn = data[26:44].split(b"\x00")[0].decode("ascii", errors="replace")
                    print(f"                   ArtPollReply short='{sn}'")
            else:
                print(f"  {ip:>16} non-artnet len={len(data)}")
        except socket.timeout:
            continue
    time.sleep(0.1)

sock.close()
if use_sock2:
    sock2.close()
print("Done")
