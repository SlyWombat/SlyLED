#!/usr/bin/env python3
"""Check Giga DMX bridge Art-Net stats."""
import urllib.request, json, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.219"
try:
    r = urllib.request.urlopen(f"http://{ip}/dmx/channels", timeout=5)
    d = json.loads(r.read())
    print(f"  Art-Net RX:  {d.get('artnetRx', 'N/A')}")
    print(f"  Art-Net PPS: {d.get('artnetPps', 'N/A')}")
    print(f"  Sender:      {d.get('artnetSender', 'N/A')}")
    print(f"  DMX frames:  {d.get('frames', 'N/A')}")
    print(f"  Active:      {d.get('active', 'N/A')}")
    print(f"  Subnet:      {d.get('subnet', 'N/A')}")
    print(f"  Universe:    {d.get('universe', 'N/A')}")
    ch_vals = d.get("ch", [])
    print(f"  Ch1-6:       {ch_vals[:6]}")
except Exception as e:
    print(f"  ERROR: {e}")
