#!/usr/bin/env python3
"""
discover.py — Broadcast UDP PING and list all SlyLED children that respond.

Usage:
    python tests/discover.py [subnet_broadcast]

Defaults:
    subnet_broadcast = 255.255.255.255

Examples:
    python tests/discover.py
    python tests/discover.py 192.168.10.255

Prints each responding child as:
    <ip>  <hostname>  <altName>  <stringCount> strings
"""

import socket
import struct
import sys
import time

UDP_PORT    = 4210
UDP_MAGIC   = 0x534C
UDP_VERSION = 2
CMD_PING    = 0x01
CMD_PONG    = 0x02
WAIT_S      = 3.0   # seconds to collect responses

broadcast = sys.argv[1] if len(sys.argv) > 1 else "255.255.255.255"


def make_ping():
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, CMD_PING, int(time.time()) & 0xFFFFFFFF)


def parse_pong(data, src_ip):
    if len(data) < 8 + 95:
        return None
    magic, version, cmd, _ = struct.unpack_from("<HBBI", data, 0)
    if magic != UDP_MAGIC or cmd != CMD_PONG:
        return None
    p = data[8:]
    hostname    = p[0:10].rstrip(b'\x00').decode("ascii", errors="replace")
    alt_name    = p[10:26].rstrip(b'\x00').decode("ascii", errors="replace")
    description = p[26:58].rstrip(b'\x00').decode("ascii", errors="replace")
    sc          = p[58]
    return {"ip": src_ip, "hostname": hostname, "altName": alt_name,
            "description": description, "stringCount": sc}


def discover(broadcast_addr=broadcast, wait=WAIT_S):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.2)
    # Children always reply to (sender_ip, UDP_PORT) — must bind to UDP_PORT to receive PONGs.
    sock.bind(("", UDP_PORT))

    pkt = make_ping()
    sock.sendto(pkt, (broadcast_addr, UDP_PORT))

    found = {}
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            data, (src_ip, _) = sock.recvfrom(256)
            info = parse_pong(data, src_ip)
            if info and src_ip not in found:
                found[src_ip] = info
        except socket.timeout:
            pass

    sock.close()
    return list(found.values())


if __name__ == "__main__":
    print(f"Broadcasting PING to {broadcast}:{UDP_PORT} (waiting {WAIT_S:.0f}s)...")
    children = discover(broadcast)

    if not children:
        print("No children found.")
        sys.exit(1)

    print(f"\nFound {len(children)} child(ren):\n")
    print(f"  {'IP':<18}  {'Hostname':<12}  {'AltName':<16}  {'Desc':<24}  Strings")
    print(f"  {'-'*18}  {'-'*12}  {'-'*16}  {'-'*24}  -------")
    for c in children:
        print(f"  {c['ip']:<18}  {c['hostname']:<12}  {c['altName']:<16}  {c['description']:<24}  {c['stringCount']}")

    # Emit bare IP list for scripting
    print()
    for c in children:
        print(c["ip"])
