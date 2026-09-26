"""peer_orchestrators.py — notice a second SlyLED orchestrator on the network (#966).

Two orchestrators on one lighting LAN fight over the same devices (sACN /
Art-Net to the same universes, HinksPix pushes, performer RUNNER_GO, gyro
claims, the scheduler). Both must tell the operator.

Detection is symmetric by construction:

* every orchestrator already broadcasts ``CMD_PING`` on UDP 4210 every 30 s
  and listens on 4210 — a PING from an address that isn't one of ours is a
  peer (older orchestrators are caught this way);
* each orchestrator also broadcasts ``CMD_ORCH_ANNOUNCE`` (0x72) carrying a
  random instance id, its HTTP port, version and hostname. The instance id
  tells two instances on the same host apart, and port + hostname give the
  SPA a link to the other one. Performer / gyro / Giga firmware ignores the
  unknown command (their dispatch is an if/else chain), so UDP_VERSION
  stays 5.

A peer expires after ``expire_s`` of silence (3 announce intervals). This
module is pure (no sockets, no Flask): parent_server feeds it packets.
Detection needs both instances in one broadcast domain; a peer on a routed
VLAN is only seen when listed in ``SLYLED_PEER_TARGETS``.
"""

import ipaddress
import struct
import threading
import time

CMD_ORCH_ANNOUNCE = 0x72
_HDR = 8                      # <HBBI magic, version, cmd, epoch>
MIN_ANNOUNCE_LEN = _HDR + 4 + 2 + 1 + 1


def build_announce(header, instance_id, http_port, version, hostname):
    """Payload after the 8-byte header: instanceId(u32) httpPort(u16)
    verLen(u8) version, hostLen(u8) hostname (each ≤ 32 bytes, UTF-8)."""
    v = (version or "").encode("utf-8")[:32]
    h = (hostname or "").encode("utf-8")[:32]
    return (header + struct.pack("<IH", instance_id & 0xFFFFFFFF, int(http_port) & 0xFFFF)
            + bytes([len(v)]) + v + bytes([len(h)]) + h)


def parse_announce(data):
    """Inverse of ``build_announce``; ``None`` for a malformed datagram."""
    if len(data) < MIN_ANNOUNCE_LEN:
        return None
    try:
        iid, port = struct.unpack_from("<IH", data, _HDR)
        i = _HDR + 6
        vl = data[i]
        version = data[i + 1:i + 1 + vl].decode("utf-8", "replace")
        i += 1 + vl
        hl = data[i]
        hostname = data[i + 1:i + 1 + hl].decode("utf-8", "replace")
        if i + 1 + hl > len(data):
            return None
    except (struct.error, IndexError):
        return None
    return {"instanceId": iid, "port": port, "version": version, "hostname": hostname}


def _is_loopback(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


class PeerRegistry:
    """Peers seen on the wire. ``own_ips`` is a callable returning this
    host's IPv4 addresses (so our own looped-back broadcasts are ignored);
    ``on_change(event, peer)`` is called with "appeared" / "gone"."""

    def __init__(self, instance_id, expire_s=90.0, own_ips=None, clock=time.time,
                 on_change=None):
        self.instance_id = instance_id
        self.expire_s = float(expire_s)
        self._own_ips = own_ips or (lambda: set())
        self._clock = clock
        self._on_change = on_change or (lambda event, peer: None)
        self._peers = {}          # key → peer dict
        self._lock = threading.Lock()

    def _own(self, ip):
        if _is_loopback(ip):
            return True
        try:
            return ip in self._own_ips()
        except Exception:
            return False

    def _upsert(self, key, fields):
        now = self._clock()
        new = False
        with self._lock:
            p = self._peers.get(key)
            if p is None:
                p = {"firstSeen": now}
                self._peers[key] = p
                new = True
            p.update(fields)
            p["lastSeen"] = now
            snap = dict(p)
        if new:
            self._on_change("appeared", snap)
        return new

    def note_ping(self, ip):
        """A CMD_PING from *ip*. Our own (or any loopback) address is
        ignored — a same-host peer is only told apart by its announce."""
        if self._own(ip):
            return False
        with self._lock:
            if any(p.get("ip") == ip and p.get("instanceId") is not None
                   for p in self._peers.values()):
                # Already known by its announce — just keep it alive.
                for p in self._peers.values():
                    if p.get("ip") == ip:
                        p["lastSeen"] = self._clock()
                return False
        return self._upsert("ip:" + ip, {"ip": ip, "instanceId": None, "port": None,
                                         "version": None, "hostname": None, "via": "ping"})

    def note_announce(self, ip, info):
        """A CMD_ORCH_ANNOUNCE. Returns True when this peer is new (the
        caller answers with its own announce so the other side hears us
        without waiting a full interval)."""
        if not info or info.get("instanceId") == self.instance_id:
            return False
        with self._lock:
            # The ping-only entry for this address is the same machine.
            self._peers.pop("ip:" + ip, None)
        return self._upsert("id:%08x" % info["instanceId"], {
            "ip": ip, "instanceId": "%08x" % info["instanceId"], "port": info.get("port"),
            "version": info.get("version") or None, "hostname": info.get("hostname") or None,
            "via": "announce"})

    def peers(self):
        """Live peers, oldest first, with ``url`` when the port is known.
        Expired ones are dropped (and reported "gone")."""
        now = self._clock()
        gone = []
        with self._lock:
            for k in list(self._peers):
                if now - self._peers[k]["lastSeen"] > self.expire_s:
                    gone.append(self._peers.pop(k))
            out = [dict(p) for p in self._peers.values()]
        for p in gone:
            self._on_change("gone", p)
        for p in out:
            p["url"] = f"http://{p['ip']}:{p['port']}" if p.get("port") else None
            p["ageS"] = round(now - p["lastSeen"], 1)
        return sorted(out, key=lambda p: p["firstSeen"])
