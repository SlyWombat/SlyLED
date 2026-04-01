#!/usr/bin/env python3
"""Quick Art-Net scanner — send ArtPoll, print all responses."""
import socket, struct, time

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", 6454))
sock.settimeout(5)

pkt = b"Art-Net\x00" + struct.pack("<H", 0x2000) + struct.pack(">H", 14) + b"\x00\x00"
for dest in ("255.255.255.255", "192.168.10.255", "192.168.10.4"):
    sock.sendto(pkt, (dest, 6454))
    print(f"ArtPoll -> {dest}")

seen = {}
start = time.time()
while time.time() - start < 5:
    try:
        data, addr = sock.recvfrom(1024)
        ip = addr[0]
        if data[:8] != b"Art-Net\x00" or len(data) < 12:
            continue
        op = struct.unpack_from("<H", data, 8)[0]
        key = f"{ip}:0x{op:04x}"
        if key in seen:
            continue
        seen[key] = True
        if op == 0x2100:
            sn = data[26:44].split(b"\x00")[0].decode("ascii", errors="replace")
            ln = data[44:108].split(b"\x00")[0].decode("ascii", errors="replace")
            print(f"  {ip:>16}  ArtPollReply  short={sn}  long={ln}")
        elif op == 0x2000:
            print(f"  {ip:>16}  ArtPoll (echo or other controller)")
        elif op == 0x5000:
            uni = struct.unpack_from("<H", data, 14)[0]
            print(f"  {ip:>16}  ArtDMX  universe={uni}")
        else:
            print(f"  {ip:>16}  opcode=0x{op:04x}  len={len(data)}")
    except socket.timeout:
        break

sock.close()
print(f"\nDone — {len(seen)} unique responses")
