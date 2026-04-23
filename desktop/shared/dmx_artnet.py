"""
Art-Net 4 Engine — ArtPoll discovery + ArtDMX output at 40Hz.

Wire format reference (Art-Net 4 protocol):
  - All packets start with 'Art-Net\\0' (8 bytes)
  - Little-endian for port/universe, big-endian for protocol version
  - UDP port 6454

Packet types implemented:
  - ArtPoll      (opcode 0x2000) — node discovery request
  - ArtPollReply (opcode 0x2100) — node discovery response
  - ArtDMX       (opcode 0x5000) — DMX data output
"""

import socket
import struct
import threading
import time
import uuid

from dmx_universe import DMXUniverse


def _all_local_broadcast_addrs():
    """Return subnet broadcast addresses for every non-loopback IPv4 interface.

    On Linux (incl. WSL2 mirrored mode), sending to 255.255.255.255 on a
    0.0.0.0-bound socket only reaches the default-route interface. Enumerating
    every adapter's subnet broadcast ensures Art-Net ArtPoll discovery and
    ArtDMX frames reach nodes on all physical NICs.
    """
    import re, subprocess, ipaddress
    broadcasts = []
    seen = set()
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"],
                                      text=True, timeout=2)
        for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)\s", out):
            ip_str, prefix_len = m.group(1), int(m.group(2))
            if ip_str.startswith("127.") or ip_str.startswith("169.254."):
                continue
            iface = ipaddress.IPv4Interface(f"{ip_str}/{prefix_len}")
            bc = str(iface.network.broadcast_address)
            if bc not in seen:
                broadcasts.append(bc)
                seen.add(bc)
    except Exception:
        pass
    # Always keep the limited broadcast as a final fallback — on Windows/macOS
    # (where `ip` is missing) this is the only address we know about.
    if "255.255.255.255" not in seen:
        broadcasts.append("255.255.255.255")
    return broadcasts

# ── Art-Net constants ────────────────────────────────────────────────────────

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_VERSION = 14  # Art-Net 4

OP_POLL       = 0x2000
OP_POLL_REPLY = 0x2100
OP_DMX        = 0x5000
OP_TODREQUEST = 0x8000
OP_TODCONTROL = 0x8200

FRAME_RATE_HZ = 40
FRAME_INTERVAL = 1.0 / FRAME_RATE_HZ  # 25ms


# ── Art-Net packet builders ──────────────────────────────────────────────────

def build_artpoll():
    """Build an ArtPoll packet for node discovery."""
    pkt = bytearray(ARTNET_HEADER)
    pkt += struct.pack("<H", OP_POLL)           # opcode (LE)
    pkt += struct.pack(">H", ARTNET_VERSION)    # protocol version (BE)
    pkt += bytes([0x06, 0x00])                  # TalkToMe: send reply on change + diag
    return bytes(pkt)


def build_artpoll_reply(ip, port, short_name="SlyLED", long_name="SlyLED Parent",
                        universes=None):
    """Build an ArtPollReply identifying this node."""
    pkt = bytearray(ARTNET_HEADER)
    pkt += struct.pack("<H", OP_POLL_REPLY)     # opcode (LE)

    # IP address (4 bytes)
    parts = ip.split(".")
    for p in parts:
        pkt += bytes([int(p)])

    pkt += struct.pack("<H", port)              # port (LE)

    # Version info (firmware)
    pkt += struct.pack(">H", 0x0001)            # version high (BE)

    # NetSwitch, SubSwitch
    pkt += bytes([0x00, 0x00])

    # OEM code
    pkt += struct.pack(">H", 0x00FF)

    # UBEA version
    pkt += bytes([0x00])

    # Status1
    pkt += bytes([0xD0])  # sACN capable, LED indicators normal

    # ESTA manufacturer
    pkt += struct.pack("<H", 0x7FF0)

    # Short name (18 bytes, null-padded)
    sn = short_name[:17].encode("ascii", errors="replace")
    pkt += sn + b"\x00" * (18 - len(sn))

    # Long name (64 bytes, null-padded)
    ln = long_name[:63].encode("ascii", errors="replace")
    pkt += ln + b"\x00" * (64 - len(ln))

    # Node report (64 bytes)
    report = b"#0001 [0000] SlyLED ready"
    pkt += report + b"\x00" * (64 - len(report))

    # NumPorts
    pkt += struct.pack(">H", len(universes or [0]))

    # PortTypes (4 bytes) — output, Art-Net protocol
    port_types = [0x80] * min(len(universes or [0]), 4)  # 0x80 = output, Art-Net
    port_types += [0x00] * (4 - len(port_types))
    pkt += bytes(port_types)

    # GoodInput (4 bytes)
    pkt += bytes([0x00] * 4)

    # GoodOutput (4 bytes)
    good_out = [0x80] * min(len(universes or [0]), 4)  # 0x80 = data is being transmitted
    good_out += [0x00] * (4 - len(good_out))
    pkt += bytes(good_out)

    # SwIn (4 bytes) — input universe subscriptions (not used)
    pkt += bytes([0x00] * 4)

    # SwOut (4 bytes) — output universe addresses
    sw_out = [(u - 1) & 0x0F for u in (universes or [1])[:4]]
    sw_out += [0x00] * (4 - len(sw_out))
    pkt += bytes(sw_out)

    # Padding to 239 bytes minimum
    while len(pkt) < 239:
        pkt += b"\x00"

    return bytes(pkt)


def build_artdmx(universe, sequence, data):
    """Build an ArtDMX packet.
    *universe*: 0-based (Art-Net uses 15-bit port-address)
    *sequence*: 1-255 rolling counter (0 = disable sequencing)
    *data*: 512 bytes of DMX channel data
    """
    pkt = bytearray(ARTNET_HEADER)
    pkt += struct.pack("<H", OP_DMX)             # opcode (LE)
    pkt += struct.pack(">H", ARTNET_VERSION)     # protocol version (BE)
    pkt += bytes([sequence & 0xFF])              # sequence
    pkt += bytes([0x00])                         # physical port
    pkt += struct.pack("<H", universe & 0x7FFF)  # universe / port-address (LE)
    length = min(len(data), 512)
    # Pad to even number
    if length % 2 != 0:
        length += 1
    pkt += struct.pack(">H", length)             # length (BE, must be even)
    pkt += data[:length]
    if len(data) < length:
        pkt += b"\x00" * (length - len(data))
    return bytes(pkt)


def parse_artnet_header(data):
    """Parse Art-Net header, return (opcode, version) or None."""
    if len(data) < 12 or data[:8] != ARTNET_HEADER:
        return None
    opcode = struct.unpack_from("<H", data, 8)[0]
    version = struct.unpack_from(">H", data, 10)[0]
    return opcode, version


def parse_artpoll_reply(data):
    """Parse an ArtPollReply packet into a dict."""
    if len(data) < 100:
        return None
    hdr = parse_artnet_header(data)
    if not hdr or hdr[0] != OP_POLL_REPLY:
        return None
    ip = f"{data[10]}.{data[11]}.{data[12]}.{data[13]}"
    port = struct.unpack_from("<H", data, 14)[0]
    short_name = data[26:44].split(b"\x00")[0].decode("ascii", errors="replace")
    long_name = data[44:108].split(b"\x00")[0].decode("ascii", errors="replace")
    return {
        "ip": ip,
        "port": port,
        "shortName": short_name,
        "longName": long_name,
    }


# ── Art-Net Engine ───────────────────────────────────────────────────────────

class ArtNetEngine:
    """Art-Net 4 output engine with ArtPoll discovery and 40Hz ArtDMX output."""

    def __init__(self, bind_ip="0.0.0.0", unicast_targets=None, frame_rate=40):
        """
        Args:
            bind_ip: IP to bind the UDP socket to (default: all interfaces)
            unicast_targets: dict of universe→ip for unicast mode.
                             If None, broadcasts to 255.255.255.255.
            frame_rate: output frame rate in Hz (default 40)
        """
        self._bind_ip = bind_ip
        self._unicast = unicast_targets or {}
        self._frame_rate = max(1, min(44, frame_rate))
        self._frame_interval = 1.0 / self._frame_rate
        self._universes = {}  # universe_num → DMXUniverse
        self._sequences = {}  # universe_num → rolling 1-255
        self._running = False
        self._thread = None
        self._sock = None
        self._lock = threading.Lock()
        self._discovered = {}  # ip → ArtPollReply info
        self._local_ip = "0.0.0.0"

    def configure(self, bind_ip=None, unicast_targets=None, frame_rate=None):
        """Update configuration. Takes effect on next start()."""
        if bind_ip is not None:
            self._bind_ip = bind_ip
        if unicast_targets is not None:
            self._unicast = unicast_targets
        if frame_rate is not None:
            self._frame_rate = max(1, min(44, frame_rate))
            self._frame_interval = 1.0 / self._frame_rate

    # ── Universe management ──────────────────────────────────────

    def get_universe(self, universe):
        """Get or create a DMXUniverse buffer.

        #622 WARNING — calling this from a read-only path (e.g.
        /api/fixtures/live, or any status endpoint the SPA polls on an
        interval) lazily creates a universe buffer that subsequently
        emits ArtDMX keep-alive frames at 1 Hz. Use ``peek_universe``
        for read-only access so polling never conjures a new universe.
        """
        if universe not in self._universes:
            self._universes[universe] = DMXUniverse(universe)
        return self._universes[universe]

    def peek_universe(self, universe):
        """#622 — read-only universe lookup. Returns the buffer if it
        already exists (i.e. the engine has been asked to write to it)
        or None otherwise. Never creates a new universe, so read-only
        status polling cannot cause keep-alive broadcasts."""
        return self._universes.get(universe)

    def set_channel(self, universe, channel, value):
        """Set a channel in a universe."""
        self.get_universe(universe).set_channel(channel, value)

    def set_fixture_rgb(self, universe, start_addr, r, g, b, profile=None):
        """Set RGB values for a fixture."""
        self.get_universe(universe).set_fixture_rgb(start_addr, r, g, b, profile)

    def blackout(self, universe=None):
        """Blackout one or all universes."""
        if universe is not None:
            self.get_universe(universe).blackout()
        else:
            for u in self._universes.values():
                u.blackout()

    # ── Engine lifecycle ─────────────────────────────────────────

    def start(self):
        """Start the 40Hz output thread."""
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self._bind_ip, ARTNET_PORT))
        except OSError:
            # Port in use — bind to ephemeral port (output-only mode)
            self._sock.bind((self._bind_ip, 0))
        self._sock.settimeout(0.01)
        # Detect local IP
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            self._local_ip = probe.getsockname()[0]
            probe.close()
        except Exception:
            self._local_ip = "127.0.0.1"
        # Cache every interface's broadcast address so poll() and DMX output
        # reach nodes on every NIC, not just the default-route one (#541).
        self._broadcast_addrs = _all_local_broadcast_addrs()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the output thread and close socket.

        Before tearing down, zero every universe and send 3 forced ArtDMX
        frames so downstream bridges latch on a blackout rather than the
        last cue. The Giga DMX bridge re-transmits its last received frame
        at 40 Hz forever once Art-Net stops arriving (by design — network
        blips shouldn't black out a show), so without this, killing the
        engine leaves the wire lit. Mirrors sACNEngine.stop() behaviour
        per E1.31. (#601)
        """
        if self._running and self._sock is not None:
            try:
                for u in self._universes.values():
                    u.blackout()
                for _ in range(3):
                    self._transmit_all_universes_forced()
                    time.sleep(self._frame_interval)
            except Exception:
                pass
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock:
            self._sock.close()
            self._sock = None

    def _transmit_all_universes_forced(self):
        """Send ArtDMX for every universe regardless of dirty/keep-alive state.

        Used by stop() to flush final blackout frames. Routing matches
        _send_all_universes(): unicast if a target is registered, otherwise
        broadcast on every interface's subnet.
        """
        if not self._sock:
            return
        for uni_num, uni in list(self._universes.items()):
            data = uni.get_data()
            seq = self._sequences.get(uni_num, 0) + 1
            if seq > 255:
                seq = 1
            self._sequences[uni_num] = seq
            pkt = build_artdmx(uni_num - 1, seq, data)
            target = self._unicast.get(uni_num)
            if target:
                try:
                    self._sock.sendto(pkt, (target, ARTNET_PORT))
                except Exception:
                    pass
            else:
                for dest in getattr(self, "_broadcast_addrs", ["255.255.255.255"]):
                    try:
                        self._sock.sendto(pkt, (dest, ARTNET_PORT))
                    except Exception:
                        pass
            uni.dirty = False

    @property
    def running(self):
        return self._running

    # ── Discovery ────────────────────────────────────────────────

    def poll(self):
        """Send ArtPoll to discover Art-Net nodes on the network."""
        if not self._sock:
            return
        pkt = build_artpoll()
        # Broadcast on every interface's subnet — default-route-only broadcast
        # leaves non-primary NICs dark (see #541).
        for dest in getattr(self, "_broadcast_addrs", ["255.255.255.255"]):
            try:
                self._sock.sendto(pkt, (dest, ARTNET_PORT))
            except Exception:
                pass
        # Also unicast to known targets (some bridges only respond to unicast)
        for ip in set(self._unicast.values()):
            try:
                self._sock.sendto(pkt, (ip, ARTNET_PORT))
            except Exception:
                pass

    @property
    def discovered_nodes(self):
        """Return dict of discovered Art-Net nodes."""
        return dict(self._discovered)

    # ── Main loop ────────────────────────────────────────────────

    def _run_loop(self):
        """40Hz output loop + poll reply listener."""
        next_frame = time.monotonic()
        poll_interval = 10.0  # ArtPoll every 10s
        next_poll = time.monotonic() + 1.0  # first poll after 1s

        while self._running:
            now = time.monotonic()

            # Receive incoming (ArtPollReply, etc.)
            self._recv()

            # Periodic ArtPoll
            if now >= next_poll:
                self.poll()
                next_poll = now + poll_interval

            # Send DMX frames
            if now >= next_frame:
                self._send_all_universes()
                next_frame += self._frame_interval
                if next_frame < now:
                    next_frame = now + self._frame_interval

            # Sleep until next event
            sleep_time = min(next_frame - time.monotonic(), 0.005)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _recv(self):
        """Non-blocking receive — drain all pending packets."""
        if not self._sock:
            return
        for _ in range(50):  # drain up to 50 packets per call
            try:
                data, addr = self._sock.recvfrom(2048)
            except (socket.timeout, BlockingIOError, OSError):
                return
            # Skip our own packets
            if addr[0] == self._local_ip:
                continue
            hdr = parse_artnet_header(data)
            if not hdr:
                continue
            opcode = hdr[0]
            if opcode == OP_POLL_REPLY:
                info = parse_artpoll_reply(data)
                if info:
                    self._discovered[info["ip"]] = info
            elif opcode == OP_POLL:
                # Respond to ArtPoll from other controllers
                universes = list(self._universes.keys()) or [1]
                reply = build_artpoll_reply(
                    self._local_ip, ARTNET_PORT,
                    short_name="SlyLED", long_name="SlyLED Parent Server",
                    universes=universes,
                )
                try:
                    self._sock.sendto(reply, addr)
                except Exception:
                    pass

    def _send_all_universes(self):
        """Send ArtDMX for dirty universes + keep-alive retransmit every ~1s."""
        if not self._sock:
            return
        now = time.monotonic()
        for uni_num, uni in list(self._universes.items()):
            # Send if dirty OR keep-alive interval elapsed (Art-Net standard)
            last = getattr(uni, '_last_send', 0)
            if not uni.dirty and (now - last) < 1.0:
                continue
            data = uni.get_data()
            seq = self._sequences.get(uni_num, 0) + 1
            if seq > 255:
                seq = 1
            self._sequences[uni_num] = seq
            pkt = build_artdmx(uni_num - 1, seq, data)  # Art-Net uses 0-based universe
            target = self._unicast.get(uni_num)
            if target:
                try:
                    self._sock.sendto(pkt, (target, ARTNET_PORT))
                except Exception:
                    pass
            else:
                # No route — broadcast on every interface's subnet so nodes on
                # non-default NICs still receive frames (#541).
                for dest in getattr(self, "_broadcast_addrs", ["255.255.255.255"]):
                    try:
                        self._sock.sendto(pkt, (dest, ARTNET_PORT))
                    except Exception:
                        pass
            uni.dirty = False
            uni._last_send = now

    # ── Status ───────────────────────────────────────────────────

    def status(self):
        """Return engine status dict."""
        return {
            "running": self._running,
            "protocol": "artnet",
            "localIp": self._local_ip,
            "bindIp": self._bind_ip,
            "unicastTargets": self._unicast,
            "universes": list(self._universes.keys()),
            "discoveredNodes": len(self._discovered),
            "frameRate": self._frame_rate,
        }
