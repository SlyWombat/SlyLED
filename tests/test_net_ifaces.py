#!/usr/bin/env python3
"""test_net_ifaces.py — #948 Phase 0.2: local IPv4 interface enumeration.

Covers desktop/shared/net_ifaces.py: real-netmask broadcasts (no /24
assumption), `ip -4 -o addr show` parsing, loopback/link-local/virtual-bridge
filtering, the WSL2/Docker 172.16/12 drop, the fallback chain order, and a
live call on this host.

Run:
    python3 tests/test_net_ifaces.py
"""

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import net_ifaces  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def eq(name, got, want):
    ok(name, got == want, f"got {got!r}, want {want!r}")


IP_OUTPUT = """\
1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
1: lo    inet 10.255.255.254/32 brd 10.255.255.254 scope global lo\\       valid_lft forever
2: eth0    inet 192.168.10.67/24 brd 192.168.10.255 scope global eth0\\       valid_lft forever
3: eth1    inet 10.20.0.5/16 brd 10.20.255.255 scope global eth1\\       valid_lft forever
4: wlan0    inet 169.254.3.4/16 scope link wlan0\\       valid_lft forever
5: docker0    inet 10.99.0.1/24 scope global docker0\\       valid_lft forever
6: eth2    inet 172.22.1.9/20 scope global eth2\\       valid_lft forever
7: tun0    inet 10.8.0.2/32 scope global tun0\\       valid_lft forever
"""


def run():
    # ── _entry: broadcast from the real netmask ──────────────────────────
    e = net_ifaces._entry("en0", "10.20.3.4", 16, "t")
    eq("/16 broadcast is x.y.255.255, not x.y.z.255", e["broadcast"], "10.20.255.255")
    eq("/16 netmask", e["netmask"], "255.255.0.0")
    eq("/24 broadcast", net_ifaces._entry("en0", "192.168.10.67", 24, "t")["broadcast"],
       "192.168.10.255")
    eq("/22 broadcast", net_ifaces._entry("en0", "192.168.9.1", 22, "t")["broadcast"],
       "192.168.11.255")
    eq("/32 has no broadcast", net_ifaces._entry("tun0", "10.8.0.2", 32, "t")["broadcast"], None)

    # ── `ip -4 -o addr show` parsing + filtering ─────────────────────────
    parsed = net_ifaces.parse_ip_addr(IP_OUTPUT)
    ips = [p["ip"] for p in parsed]
    ok("loopback skipped", "127.0.0.1" not in ips, ips)
    ok("non-127 address on lo skipped (WSL2 DNS tunnel)", "10.255.255.254" not in ips, ips)
    ok("link-local skipped", "169.254.3.4" not in ips, ips)
    ok("docker bridge skipped by name", "10.99.0.1" not in ips, ips)
    eq("interface names parsed", [p["name"] for p in parsed], ["eth0", "eth1", "eth2", "tun0"])
    eq("prefix lengths parsed", [p["prefixlen"] for p in parsed], [24, 16, 20, 32])

    filtered = net_ifaces._filter(parsed + parsed)
    fips = [f["ip"] for f in filtered]
    eq("dedupe + 172.16/12 dropped when other addresses exist",
       fips, ["192.168.10.67", "10.20.0.5", "10.8.0.2"])
    only172 = net_ifaces._filter(net_ifaces.parse_ip_addr(
        "6: eth2    inet 172.22.1.9/20 scope global eth2\n"))
    eq("172.16/12 kept when it is the only address", [f["ip"] for f in only172], ["172.22.1.9"])

    eq("subnet_broadcasts uses real masks, skips /32",
       net_ifaces.subnet_broadcasts(filtered), ["192.168.10.255", "10.20.255.255"])
    eq("scan_prefixes stays /24 per address",
       net_ifaces.scan_prefixes(filtered), ["192.168.10", "10.20.0", "10.8.0"])

    # ── fallback chain: first non-empty step wins ────────────────────────
    saved = (net_ifaces._from_psutil, net_ifaces._from_ip_cmd,
             net_ifaces._from_getaddrinfo, net_ifaces._from_default_route)
    try:
        net_ifaces._from_psutil = lambda: []
        net_ifaces._from_ip_cmd = lambda: []
        net_ifaces._from_getaddrinfo = lambda: [net_ifaces._entry("h", "192.168.1.5", 24, "getaddrinfo")]
        net_ifaces._from_default_route = lambda: [net_ifaces._entry("d", "192.168.1.9", 24, "route")]
        eq("no psutil/ip -> getaddrinfo answers",
           [e["source"] for e in net_ifaces.ipv4_interfaces()], ["getaddrinfo"])
        net_ifaces._from_getaddrinfo = lambda: []
        eq("then default route", [e["source"] for e in net_ifaces.ipv4_interfaces()], ["route"])
        net_ifaces._from_default_route = lambda: []
        eq("nothing anywhere -> empty list", net_ifaces.ipv4_interfaces(), [])
        net_ifaces._from_psutil = lambda: [net_ifaces._entry("en0", "10.0.0.2", 24, "psutil")]
        net_ifaces._from_ip_cmd = lambda: [net_ifaces._entry("en0", "10.0.0.3", 24, "ip")]
        eq("psutil preferred over ip", [e["source"] for e in net_ifaces.ipv4_interfaces()], ["psutil"])
    finally:
        (net_ifaces._from_psutil, net_ifaces._from_ip_cmd,
         net_ifaces._from_getaddrinfo, net_ifaces._from_default_route) = saved

    # ── port sharing (4210 listener + _send_recv on the same port) ───────
    eq("SO_REUSEPORT used only off Linux/Windows",
       net_ifaces.REUSEPORT,
       hasattr(socket, "SO_REUSEPORT") and not sys.platform.startswith("linux"))
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        net_ifaces.allow_port_sharing(a)
        a.bind(("", 0))
        port = a.getsockname()[1]
        net_ifaces.allow_port_sharing(b)
        try:
            b.bind(("", port))
            second_ok = True
        except OSError as e:
            second_ok = False
            ok("second bind on a shared port succeeds", False, str(e))
        if second_ok:
            ok("second bind on a shared port succeeds", True)
        # _send_recv binds after the listener and must receive the child's
        # unicast reply; the listener must not. Skipped on Windows, where
        # Microsoft documents SO_REUSEADDR unicast delivery as undefined.
        if second_ok and sys.platform != "win32":
            a.settimeout(0.3)
            b.settimeout(1.0)
            c.sendto(b"reply", ("127.0.0.1", port))
            try:
                got_b = b.recvfrom(64)[0]
            except OSError:
                got_b = None
            try:
                got_a = a.recvfrom(64)[0]
            except OSError:
                got_a = None
            eq("unicast reply lands on the newest bind (_send_recv)", got_b, b"reply")
            eq("older bind (listener) does not also get it", got_a, None)
    finally:
        a.close(); b.close(); c.close()

    # ── live host ────────────────────────────────────────────────────────
    live = net_ifaces.ipv4_interfaces()
    ok("live host: at least one non-loopback IPv4 interface", len(live) >= 1, live)
    ok("live host: no loopback returned", all(not e["ip"].startswith("127.") for e in live), live)
    ok("live host: psutil step answered when psutil is installed",
       net_ifaces.psutil is None or (live and live[0]["source"] == "psutil"), live)


def main():
    run()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({detail})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
