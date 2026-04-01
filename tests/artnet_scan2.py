#!/usr/bin/env python3
"""Art-Net scanner v2 — try both broadcast and unicast ArtPoll, longer listen."""
import socket, struct, time

ARTNET_PORT = 6454
TARGET = "192.168.10.4"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("192.168.10.174", ARTNET_PORT))
sock.settimeout(2)

# Art-Net 4 ArtPoll — 14 bytes total
# Some devices need TalkToMe flags set to respond
pkt = bytearray(b"Art-Net\x00")  # 8 bytes ID
pkt += struct.pack("<H", 0x2000)  # opcode LE
pkt += struct.pack(">H", 14)     # protocol version BE (Art-Net 4)
pkt += bytes([0x06])              # TalkToMe: send ArtPollReply on change + send diagnostics
pkt += bytes([0x00])              # DiagPriority
pkt = bytes(pkt)

print(f"ArtPoll packet ({len(pkt)} bytes): {pkt.hex()}")
print()

# Try unicast first (some devices only respond to unicast)
print(f"1. Unicast ArtPoll -> {TARGET}:6454")
sock.sendto(pkt, (TARGET, ARTNET_PORT))
time.sleep(0.1)

# Also broadcast
print(f"2. Broadcast ArtPoll -> 192.168.10.255:6454")
sock.sendto(pkt, ("192.168.10.255", ARTNET_PORT))
time.sleep(0.1)

print(f"3. Broadcast ArtPoll -> 255.255.255.255:6454")
sock.sendto(pkt, ("255.255.255.255", ARTNET_PORT))

print(f"\nListening for 5 seconds on {sock.getsockname()}...")
seen = {}
start = time.time()
while time.time() - start < 5:
    try:
        data, addr = sock.recvfrom(2048)
        ip = addr[0]
        if ip == "192.168.10.174":
            continue  # skip self
        if data[:8] != b"Art-Net\x00":
            print(f"  {ip:>16} non-artnet ({len(data)} bytes)")
            continue
        if len(data) < 12:
            continue
        op = struct.unpack_from("<H", data, 8)[0]
        key = f"{ip}:0x{op:04x}"
        if key in seen:
            seen[key] += 1
            continue
        seen[key] = 1

        if op == 0x2100:  # ArtPollReply
            rip = f"{data[10]}.{data[11]}.{data[12]}.{data[13]}"
            port = struct.unpack_from("<H", data, 14)[0]
            sn = data[26:44].split(b"\x00")[0].decode("ascii", errors="replace")
            ln = data[44:108].split(b"\x00")[0].decode("ascii", errors="replace")
            print(f"  {ip:>16} ArtPollReply  reportedIP={rip}  port={port}")
            print(f"                   short='{sn}'  long='{ln}'")
            print(f"                   raw[10:14]={data[10:14].hex()}  raw[0:20]={data[0:20].hex()}")
        elif op == 0x2000:
            print(f"  {ip:>16} ArtPoll")
        elif op == 0x5000:
            uni = struct.unpack_from("<H", data, 14)[0]
            print(f"  {ip:>16} ArtDMX universe={uni}")
        else:
            print(f"  {ip:>16} opcode=0x{op:04x} len={len(data)}")
    except socket.timeout:
        # Send another poll mid-way
        if time.time() - start < 3:
            sock.sendto(pkt, (TARGET, ARTNET_PORT))
            continue
        break

sock.close()
print(f"\nTotal unique: {len(seen)}")
for k, v in seen.items():
    print(f"  {k}: {v} packet(s)")
