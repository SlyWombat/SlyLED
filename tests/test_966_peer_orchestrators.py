#!/usr/bin/env python3
"""A second orchestrator on the network is noticed (#966) — in-process.

No sockets are opened toward the LAN: packets are fed to the real UDP
dispatch handlers (`_UDP_DISPATCH`), and the only datagrams sent (the
announce reply to a new peer) go to loopback. Covers:
  * CMD_ORCH_ANNOUNCE (0x72) build/parse round-trip + malformed input
  * a PING from a foreign address → peer; from our own / loopback → ignored
  * an announce names the peer (hostname, port, version, link); our own
    instance id is ignored; the ping-only entry for that IP is merged
  * expiry after 3 silent intervals, with "appeared" / "gone" events
  * /status and /api/status carry instanceId + peerOrchestrators
The two-real-servers test is tests/test_966_two_orchestrators.py (spawns
processes → CI / isolated QA network only).

Run: python3 tests/test_966_peer_orchestrators.py
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import sys

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def main():
    import parent_server as ps
    import peer_orchestrators as po

    print("Wire format")
    hdr = ps._hdr(po.CMD_ORCH_ANNOUNCE)
    pkt = po.build_announce(hdr, 0xDEADBEEF, 8080, "2.2.0", "kdocker3")
    ok("round-trip", po.parse_announce(pkt) == {"instanceId": 0xDEADBEEF, "port": 8080,
                                                "version": "2.2.0", "hostname": "kdocker3"},
       po.parse_announce(pkt))
    ok("header carries cmd 0x72 and the current UDP version",
       pkt[3] == 0x72 and pkt[2] == ps.UDP_VERSION, pkt[:8])
    ok("truncated datagram → None", po.parse_announce(pkt[:12]) is None)
    ok("long names are capped at 32 bytes",
       len(po.parse_announce(po.build_announce(hdr, 1, 1, "v" * 50, "h" * 50))["hostname"]) == 32)
    ok("registered in the dispatch table",
       ps._UDP_DISPATCH[ps.CMD_ORCH_ANNOUNCE][1] is ps._handle_orch_announce
       and ps._UDP_DISPATCH[ps.CMD_PING][1] is ps._handle_ping)

    print("Registry (fake clock)")
    now = [1000.0]
    events = []
    reg = po.PeerRegistry(0x1111, expire_s=90, own_ips=lambda: {"192.168.10.50"},
                          clock=lambda: now[0], on_change=lambda e, p: events.append((e, p["ip"])))
    ok("PING from our own address ignored", reg.note_ping("192.168.10.50") is False and not reg.peers())
    ok("PING from loopback ignored", reg.note_ping("127.0.0.1") is False and not reg.peers())
    ok("PING from a foreign address → peer", reg.note_ping("192.168.10.38") is True
       and [p["ip"] for p in reg.peers()] == ["192.168.10.38"])
    ok("…reported once as 'appeared'", events == [("appeared", "192.168.10.38")], events)
    ok("repeat PINGs don't re-announce", reg.note_ping("192.168.10.38") is False and len(events) == 1)
    ok("our own announce ignored",
       reg.note_announce("192.168.10.38", {"instanceId": 0x1111, "port": 8080}) is False)
    new = reg.note_announce("192.168.10.38", {"instanceId": 0x2222, "port": 8080,
                                              "version": "2.2.0", "hostname": "kdocker3"})
    peers = reg.peers()
    ok("announce from that IP replaces the ping-only entry (one peer, named)",
       len(peers) == 1 and peers[0]["hostname"] == "kdocker3" and peers[0]["instanceId"] == "00002222"
       and peers[0]["url"] == "http://192.168.10.38:8080" and peers[0]["via"] == "announce", peers)
    ok("a same-host peer (loopback, other instance id) is detected by its announce",
       reg.note_announce("127.0.0.1", {"instanceId": 0x3333, "port": 8090}) is True
       and len(reg.peers()) == 2)
    now[0] += 60
    reg.note_ping("192.168.10.38")          # keeps the announced peer alive
    now[0] += 60
    peers = reg.peers()
    ok("a peer that keeps talking stays; a silent one expires after the window",
       [p["ip"] for p in peers] == ["192.168.10.38"], peers)
    ok("…and the expired one is reported 'gone'", ("gone", "127.0.0.1") in events, events)
    now[0] += 200
    ok("everything expires in silence", reg.peers() == [])

    print("Real handlers + /status")
    reg = ps._peer_registry
    ps._handle_ping("192.0.2.77", 4210, None, ps._hdr(ps.CMD_PING))
    ps._handle_orch_announce("127.0.0.9", 4210, None,
                             po.build_announce(hdr, 0xABCDEF01, 8090, "2.2.1", "bench-laptop"))
    ps._handle_orch_announce("127.0.0.9", 4210, None,
                             po.build_announce(hdr, ps._INSTANCE_ID, 8080, "x", "me"))
    c = ps.app.test_client()
    for route in ("/status", "/api/status"):
        d = c.get(route).get_json()
        peers = {p["ip"]: p for p in d.get("peerOrchestrators") or []}
        ok(f"{route}: instanceId + both peers (ping-only and announced), not ourselves",
           d.get("instanceId") == "%08x" % ps._INSTANCE_ID
           and set(peers) == {"192.0.2.77", "127.0.0.9"}
           and peers["127.0.0.9"]["hostname"] == "bench-laptop"
           and peers["127.0.0.9"]["url"] == "http://127.0.0.9:8090"
           and peers["192.0.2.77"]["hostname"] is None, d.get("peerOrchestrators"))
    reg._peers.clear()
    ok("no peers → empty list", c.get("/status").get_json()["peerOrchestrators"] == [])
    ok("expiry window is 3 announce intervals", reg.expire_s == 3 * ps.ORCH_ANNOUNCE_S)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
