"""
Local IPv4 interface enumeration for discovery broadcasts, the camera
subnet scan and the DMX bind-IP picker (#948 Phase 0.2).

One chain, first non-empty answer wins:

  1. psutil.net_if_addrs()      — every OS; real netmask, interface names,
                                  skips interfaces psutil reports as down.
  2. `ip -4 addr show`          — Linux without psutil (incl. WSL2 mirrored
                                  mode, where the hostname resolves to just
                                  one of several mirrored NICs).
  3. getaddrinfo(gethostname()) — hosts with neither; netmask unknown, /24
                                  assumed.
  4. default-route probe        — UDP connect() to a public address (no
                                  traffic sent); /24 assumed.

Loopback interfaces/addresses and link-local (169.254/16) addresses are
never returned, nor are container/VM bridges (docker*, br-*, veth*,
virbr*). Private 172.16/12 addresses are dropped when any other address exists — that is the WSL2 NAT
bridge / Docker default range, and it has no path to the lighting LAN.
"""

import ipaddress
import re
import socket
import subprocess
import sys

try:
    import psutil
except ImportError:  # optional — the chain degrades to `ip` / getaddrinfo
    psutil = None

_VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr")
_NET_172 = ipaddress.ip_network("172.16.0.0/12")


def _entry(name, ip, prefixlen, source):
    iface = ipaddress.IPv4Interface(f"{ip}/{prefixlen}")
    return {
        "name": name,
        "ip": ip,
        "prefixlen": prefixlen,
        "netmask": str(iface.netmask),
        # /31 and /32 have no broadcast address worth sending to.
        "broadcast": (str(iface.network.broadcast_address)
                      if prefixlen < 31 else None),
        "source": source,
    }


def _is_loopback_name(name):
    # WSL2 parks its DNS-tunnel address 10.255.255.254/32 on `lo`, so the
    # address alone doesn't identify loopback.
    n = (name or "").lower()
    return n in ("lo", "lo0") or n.startswith("loopback")


def _usable(name, ip):
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return False
    if _is_loopback_name(name):
        return False
    return not (name or "").startswith(_VIRTUAL_PREFIXES)


def _from_psutil():
    if psutil is None:
        return []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return []
    out = []
    for name, entries in addrs.items():
        st = stats.get(name)
        if st is not None and (not st.isup
                               or "loopback" in (getattr(st, "flags", "") or "")):
            continue
        for a in entries:
            if a.family != socket.AF_INET or not _usable(name, a.address):
                continue
            try:
                prefixlen = ipaddress.IPv4Network(
                    f"0.0.0.0/{a.netmask or '255.255.255.0'}").prefixlen
            except ValueError:
                prefixlen = 24
            out.append(_entry(name, a.address, prefixlen, "psutil"))
    return out


def _from_ip_cmd():
    try:
        text = subprocess.check_output(["ip", "-4", "-o", "addr", "show"],
                                       text=True, timeout=3,
                                       stderr=subprocess.DEVNULL)
    except Exception:
        return []
    return parse_ip_addr(text)


def parse_ip_addr(text):
    """Parse `ip -4 -o addr show` (one line per address) output."""
    out = []
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)",
                         text, re.M):
        name, ip, plen = m.group(1), m.group(2), int(m.group(3))
        if _usable(name, ip):
            out.append(_entry(name, ip, plen, "ip"))
    return out


def _from_getaddrinfo():
    out = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if _usable(host, ip):
                out.append(_entry(host, ip, 24, "getaddrinfo"))
    except Exception:
        pass
    return out


def default_route_ip():
    """Source address the OS would use to reach the internet, or None.
    A UDP connect() sends nothing; it only resolves the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return None


def _from_default_route():
    ip = default_route_ip()
    return [_entry("default", ip, 24, "route")] if _usable("default", ip) else []


def _filter(entries):
    seen, out = set(), []
    for e in entries:
        if e["ip"] in seen:
            continue
        seen.add(e["ip"])
        out.append(e)
    if any(ipaddress.ip_address(e["ip"]) not in _NET_172 for e in out):
        out = [e for e in out if ipaddress.ip_address(e["ip"]) not in _NET_172]
    return out


def ipv4_interfaces():
    """Non-loopback IPv4 interfaces as dicts: name, ip, prefixlen, netmask,
    broadcast (None for /31, /32), source (which chain step answered)."""
    for step in (_from_psutil, _from_ip_cmd, _from_getaddrinfo, _from_default_route):
        found = _filter(step())
        if found:
            return found
    return []


def subnet_broadcasts(interfaces=None):
    """Directed broadcast address of every interface's real subnet."""
    out = []
    for e in (ipv4_interfaces() if interfaces is None else interfaces):
        bc = e.get("broadcast")
        if bc and bc not in out:
            out.append(bc)
    return out


def scan_prefixes(interfaces=None):
    """'a.b.c' /24 prefixes containing each interface address — the unit the
    camera-node HTTP sweep probes (254 hosts each), regardless of the real
    netmask, so a /16 LAN never turns into a 65k-host scan."""
    out = []
    for e in (ipv4_interfaces() if interfaces is None else interfaces):
        p = e["ip"].rsplit(".", 1)[0]
        if p not in out:
            out.append(p)
    return out


# BSD/macOS only lets a second socket bind the same unicast UDP port when
# every socket on it set SO_REUSEPORT; with SO_REUSEADDR alone the second
# bind (_send_recv on 4210, the one-shot ArtPoll on 6454) hits EADDRINUSE.
# Linux is deliberately excluded: there SO_REUSEPORT load-balances unicast
# datagrams across the sockets by 4-tuple hash, which would hand a child's
# reply to the listener instead of the waiting _send_recv. Plain
# SO_REUSEADDR on Linux gives unicast to the newest bind — the behaviour
# _send_recv relies on. Windows has no SO_REUSEPORT.
REUSEPORT = hasattr(socket, "SO_REUSEPORT") and not sys.platform.startswith("linux")


def allow_port_sharing(sock):
    """SO_REUSEADDR everywhere, plus SO_REUSEPORT on BSD/macOS. Returns True
    iff SO_REUSEPORT was set."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if REUSEPORT:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            return True
        except OSError:
            pass
    return False
