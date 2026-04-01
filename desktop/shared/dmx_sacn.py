"""
sACN (E1.31) Engine — Streaming ACN multicast output at 40Hz.

Wire format reference (ANSI E1.31-2018):
  - Root Layer: ACN packet header with CID
  - Framing Layer: universe, priority, sequence
  - DMP Layer: 512 bytes of DMX property data
  - Multicast: 239.255.<univHi>.<univLo> on port 5568
"""

import socket
import struct
import threading
import time
import uuid

from dmx_universe import DMXUniverse

# ── sACN constants ───────────────────────────────────────────────────────────

SACN_PORT = 5568
SACN_PREAMBLE = 0x0010
SACN_POSTAMBLE = 0x0000
SACN_ACN_ID = b"\x41\x53\x43\x2d\x45\x31\x2e\x31\x37\x00\x00\x00"  # "ASC-E1.17\0\0\0"
SACN_ROOT_VECTOR = 0x00000004       # VECTOR_ROOT_E131_DATA
SACN_FRAME_VECTOR = 0x00000002      # VECTOR_E131_DATA_PACKET
SACN_DMP_VECTOR = 0x02              # VECTOR_DMP_SET_PROPERTY

FRAME_RATE_HZ = 40
FRAME_INTERVAL = 1.0 / FRAME_RATE_HZ  # 25ms

DEFAULT_PRIORITY = 100  # 0-200, higher wins


# ── sACN packet builder ─────────────────────────────────────────────────────

def multicast_addr(universe):
    """Compute the sACN multicast address for a universe (1-63999)."""
    hi = (universe >> 8) & 0xFF
    lo = universe & 0xFF
    return f"239.255.{hi}.{lo}"


def build_sacn_data(cid, source_name, universe, sequence, priority, dmx_data):
    """Build a complete sACN E1.31 Data Packet (Root + Framing + DMP layers).

    Args:
        cid: 16-byte Component Identifier (UUID bytes)
        source_name: source name string (max 63 chars)
        universe: 1-63999
        sequence: 0-255 rolling counter
        priority: 0-200
        dmx_data: 512 bytes of DMX channel data
    """
    # ── DMP Layer (523 bytes) ─────────────────────────────────────
    # Flags + Length (2 bytes): 0x7000 | length
    dmp_data_len = 1 + 512  # start code + 512 channels
    dmp_len = 10 + dmp_data_len  # DMP header (10) + data
    dmp = bytearray()
    dmp += struct.pack(">H", 0x7000 | (dmp_len & 0x0FFF))  # flags + length
    dmp += bytes([SACN_DMP_VECTOR])  # vector
    dmp += bytes([0xA1])             # address type & data type
    dmp += struct.pack(">H", 0x0000) # first property address
    dmp += struct.pack(">H", 0x0001) # address increment
    dmp += struct.pack(">H", dmp_data_len)  # property value count
    dmp += bytes([0x00])             # DMX start code
    dmp += dmx_data[:512]
    if len(dmx_data) < 512:
        dmp += b"\x00" * (512 - len(dmx_data))

    # ── Framing Layer ─────────────────────────────────────────────
    frame_len = 77 + len(dmp)  # framing header (77) + DMP
    frame = bytearray()
    frame += struct.pack(">H", 0x7000 | (frame_len & 0x0FFF))  # flags + length
    frame += struct.pack(">I", SACN_FRAME_VECTOR)  # vector
    # Source name (64 bytes, null-terminated)
    sn = source_name[:63].encode("utf-8", errors="replace")
    frame += sn + b"\x00" * (64 - len(sn))
    frame += bytes([priority & 0xFF])  # priority
    frame += struct.pack(">H", 0x0000)  # sync address (0 = none)
    frame += bytes([sequence & 0xFF])   # sequence number
    frame += bytes([0x00])              # options (0 = normal)
    frame += struct.pack(">H", universe & 0xFFFF)  # universe
    frame += dmp

    # ── Root Layer ────────────────────────────────────────────────
    root_len = 22 + len(frame)  # root header (22) + framing
    root = bytearray()
    root += struct.pack(">H", SACN_PREAMBLE)   # preamble size
    root += struct.pack(">H", SACN_POSTAMBLE)  # post-amble size
    root += SACN_ACN_ID                         # ACN packet identifier (12 bytes)
    root += struct.pack(">H", 0x7000 | (root_len & 0x0FFF))  # flags + length
    root += struct.pack(">I", SACN_ROOT_VECTOR) # vector
    root += cid[:16]                            # CID (16 bytes)
    if len(cid) < 16:
        root += b"\x00" * (16 - len(cid))
    root += frame

    return bytes(root)


def parse_sacn_data(data):
    """Parse a sACN data packet. Returns dict or None."""
    if len(data) < 126:
        return None
    # Check preamble
    preamble = struct.unpack_from(">H", data, 0)[0]
    if preamble != SACN_PREAMBLE:
        return None
    # Check ACN ID
    if data[4:16] != SACN_ACN_ID:
        return None
    # Root vector
    root_vector = struct.unpack_from(">I", data, 18)[0]
    if root_vector != SACN_ROOT_VECTOR:
        return None
    cid = data[22:38]
    # Framing layer starts at offset 38
    frame_vector = struct.unpack_from(">I", data, 40)[0]
    if frame_vector != SACN_FRAME_VECTOR:
        return None
    source_name = data[44:108].split(b"\x00")[0].decode("utf-8", errors="replace")
    priority = data[108]
    sequence = data[111]
    universe = struct.unpack_from(">H", data, 113)[0]
    # DMP layer starts at offset 115
    if len(data) < 126 + 512:
        return None
    start_code = data[125]
    dmx_data = data[126:126 + 512]
    return {
        "cid": cid,
        "sourceName": source_name,
        "priority": priority,
        "sequence": sequence,
        "universe": universe,
        "startCode": start_code,
        "dmxData": dmx_data,
    }


# ── sACN Engine ──────────────────────────────────────────────────────────────

class sACNEngine:
    """sACN E1.31 output engine with 40Hz multicast output."""

    def __init__(self, source_name="SlyLED", priority=DEFAULT_PRIORITY, bind_ip="0.0.0.0",
                 frame_rate=40):
        """
        Args:
            source_name: human-readable source name (max 63 chars)
            priority: sACN priority level (0-200, higher wins)
            bind_ip: IP to bind the send socket to
            frame_rate: output frame rate in Hz (default 40)
        """
        self._source_name = source_name
        self._priority = priority
        self._bind_ip = bind_ip
        self._frame_rate = max(1, min(44, frame_rate))
        self._frame_interval = 1.0 / self._frame_rate
        self._cid = uuid.uuid4().bytes  # 16-byte component identifier
        self._universes = {}  # universe_num → DMXUniverse
        self._sequences = {}  # universe_num → rolling 0-255
        self._running = False
        self._thread = None
        self._sock = None
        self._lock = threading.Lock()

    def configure(self, source_name=None, priority=None, bind_ip=None, frame_rate=None):
        """Update configuration. Takes effect on next start()."""
        if source_name is not None:
            self._source_name = source_name[:63]
        if priority is not None:
            self._priority = max(0, min(200, int(priority)))
        if bind_ip is not None:
            self._bind_ip = bind_ip
        if frame_rate is not None:
            self._frame_rate = max(1, min(44, frame_rate))
            self._frame_interval = 1.0 / self._frame_rate

    # ── Universe management ──────────────────────────────────────

    def get_universe(self, universe):
        """Get or create a DMXUniverse buffer."""
        if universe not in self._universes:
            self._universes[universe] = DMXUniverse(universe)
        return self._universes[universe]

    def set_channel(self, universe, channel, value):
        self.get_universe(universe).set_channel(channel, value)

    def set_fixture_rgb(self, universe, start_addr, r, g, b, profile=None):
        self.get_universe(universe).set_fixture_rgb(start_addr, r, g, b, profile)

    def blackout(self, universe=None):
        if universe is not None:
            self.get_universe(universe).blackout()
        else:
            for u in self._universes.values():
                u.blackout()

    # ── Engine lifecycle ─────────────────────────────────────────

    def start(self):
        """Start the 40Hz multicast output thread."""
        if self._running:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Set multicast TTL
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 20)
        # Bind to specific interface if requested
        if self._bind_ip != "0.0.0.0":
            self._sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                socket.inet_aton(self._bind_ip)
            )
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the output thread. Sends 3 final blackout frames per spec."""
        if not self._running:
            return
        # Send 3 blackout frames (E1.31 recommends this for clean termination)
        for _ in range(3):
            for uni_num in list(self._universes.keys()):
                self._send_universe(uni_num, blackout=True)
            time.sleep(self._frame_interval)
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock:
            self._sock.close()
            self._sock = None

    @property
    def running(self):
        return self._running

    @property
    def priority(self):
        return self._priority

    @priority.setter
    def priority(self, value):
        self._priority = max(0, min(200, int(value)))

    # ── Main loop ────────────────────────────────────────────────

    def _run_loop(self):
        """40Hz output loop."""
        next_frame = time.monotonic()

        while self._running:
            now = time.monotonic()
            if now >= next_frame:
                self._send_all_universes()
                next_frame += self._frame_interval
                if next_frame < now:
                    next_frame = now + self._frame_interval
            sleep_time = min(next_frame - time.monotonic(), 0.005)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _send_all_universes(self):
        """Send sACN data for all active universes."""
        for uni_num in list(self._universes.keys()):
            self._send_universe(uni_num)

    def _send_universe(self, uni_num, blackout=False):
        """Send one sACN data packet for a universe."""
        if not self._sock:
            return
        uni = self._universes.get(uni_num)
        if not uni:
            return
        data = b"\x00" * 512 if blackout else uni.get_data()
        seq = self._sequences.get(uni_num, 0)
        seq = (seq + 1) & 0xFF
        self._sequences[uni_num] = seq

        pkt = build_sacn_data(
            self._cid, self._source_name,
            uni_num, seq, self._priority, data
        )
        dest = (multicast_addr(uni_num), SACN_PORT)
        try:
            self._sock.sendto(pkt, dest)
        except Exception:
            pass
        uni.dirty = False

    # ── Status ───────────────────────────────────────────────────

    def status(self):
        """Return engine status dict."""
        return {
            "running": self._running,
            "protocol": "sacn",
            "priority": self._priority,
            "sourceName": self._source_name,
            "universes": list(self._universes.keys()),
            "multicastAddresses": {
                u: multicast_addr(u) for u in self._universes
            },
            "bindIp": self._bind_ip,
            "frameRate": self._frame_rate,
        }
