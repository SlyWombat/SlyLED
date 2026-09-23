"""hinkspix_tcp.py — raw-TCP file upload / clock / mode for HinksPix PRO (#941).

The HinksPix accepts files, a clock set and an operational-mode switch over a
**proprietary TCP protocol on port 80** that fakes an HTTP header. It is not
HTTP: there is no request line, no status code, and the reply is a bare line
containing ``|FOK``.

Every structure here is transcribed from the xLights driver
(``src-core/controllers/HinksPix.{h,cpp}``, read 2026-09-17). Layouts use
``#pragma pack(2)``, hence the explicit padding in the struct sizes below.

    Tag_Packet (file chunk)   608 bytes  CMD[0] = 'F'
      char  HINK[18]  "HINK TCP_CMD  \\r\\n\\r\\n"
      uint8 CMD[4]    {'F', 0x5a, 0xa5, 0}
      u16   TotalSize     28 + (bytes that follow) — the framing the controller uses
      u16   StructType    0 = open/truncate, 1 = append, 2 = close
      u16   DataSize      bytes that follow, except 0 on a close packet
      uint8 Data[580]

    Tag_File_Data_Close   34 bytes in Data when StructType == 2
      char FN[30]     target filename
      u32  DTTM       FAT date/time word

An upload is: one StructType 0 chunk, then StructType 1 chunks to the end of the
file, then the close. A file whose length is an exact multiple of 580 gets one
extra zero-length StructType 1 first — see ``needs_flush_chunk`` (#944 B17b).

    Tag_Dow_TimePacket    26 bytes  CMD[0] = 'D'   hr, min, sec, dow
    Tag_CMD_Packet        22 bytes  CMD[0] = mode  'G' master/standalone,
                                                   'H' remote/slave

Throughput note: 580-byte payloads with a round-trip ACK each means a 30 MB
sequence is ~52k round trips. Callers should surface progress and an ETA rather
than appearing hung.
"""

import logging
import socket
import struct
import time

log = logging.getLogger("slyled.hinkspix.tcp")

HINK_HEADER = b"HINK TCP_CMD  \r\n\r\n"      # exactly 18 bytes
assert len(HINK_HEADER) == 18

PORT = 80
CHUNK_DATA = 580
PACKET_SIZE = 608
CLOSE_DATA_SIZE = 34
TIME_PACKET_SIZE = 26
MODE_PACKET_SIZE = 22
HEADER_OVERHEAD = 28          # TotalSize = HEADER_OVERHEAD + DataSize

ST_FIRST, ST_APPEND, ST_CLOSE = 0, 1, 2

# The DataSize field the close packet carries. xLights writes 0 there rather
# than the 34 bytes of body it actually sends — see build_chunk (#944 B17).
CLOSE_DATA_SIZE_FIELD = 0

MODE_MASTER = b"G"            # standalone, plays from SD
MODE_REMOTE = b"H"            # remote / slave

ACK_TOKEN = "|FOK"
ACK_TIMEOUT = 5.0

# dow: 0 = Sunday, matching the controller's own numbering.
_DOW_FROM_PY = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}


class HinksPixError(RuntimeError):
    """Upload rejected, timed out, or the controller replied with an error."""


def build_chunk(struct_type, data, data_size=None):
    """Build one Tag_Packet. Returns exactly ``TotalSize`` bytes.

    ``data_size`` overrides the DataSize *field* without changing the bytes on
    the wire or TotalSize. Only the close packet needs it, because xLights sets
    DataSize to 0 there while still writing the 34-byte close body and a
    TotalSize covering it (``HinksPix.cpp:1708-1717``) — so the controller must
    frame that packet on TotalSize. Putting 34 in the field (#944 B17) risks the
    firmware rejecting the close as a malformed type-2 packet.
    """
    if len(data) > CHUNK_DATA:
        raise ValueError(f"chunk payload {len(data)} exceeds {CHUNK_DATA}")
    if data_size is None:
        data_size = len(data)
    total = HEADER_OVERHEAD + len(data)
    pkt = bytearray()
    pkt += HINK_HEADER
    pkt += bytes([ord("F"), 0x5A, 0xA5, 0])
    pkt += struct.pack("<HHH", total, struct_type, data_size)
    pkt += data
    return bytes(pkt)


def build_close(filename, dttm):
    """Build the StructType=2 close packet carrying name + FAT timestamp.

    62 bytes on the wire: a 28-byte header (DataSize field 0, per ``build_chunk``)
    followed by the 34-byte Tag_File_Data_Close.
    """
    name = str(filename).encode("ascii", "ignore")[:29]
    payload = name + b"\x00" * (30 - len(name)) + struct.pack("<I", dttm)
    assert len(payload) == CLOSE_DATA_SIZE
    return build_chunk(ST_CLOSE, payload, data_size=CLOSE_DATA_SIZE_FIELD)


def needs_flush_chunk(total):
    """True when ``total`` is a non-zero exact multiple of the chunk payload.

    xLights tests the *previous* read's length at the top of its upload loop
    (``HinksPix.cpp:1706-1743``), so a file that divides evenly by 580 never
    takes the "we have fully sent the file" branch on the last full chunk — it
    reads once more, gets 0, and sends a zero-length StructType 1. Without that
    packet an exact-multiple file may never be finalised (#944 B17b).
    """
    return bool(total) and total % CHUNK_DATA == 0


def build_time_packet(when):
    """Build Tag_Dow_TimePacket. Carries time-of-day + weekday only — no date,
    so DST changes require a re-sync (design doc §8.8)."""
    pkt = bytearray()
    pkt += HINK_HEADER
    pkt += bytes([ord("D"), 0x5A, 0xA5, 0])
    pkt += bytes([when.hour, when.minute, when.second,
                  _DOW_FROM_PY[when.weekday()]])
    assert len(pkt) == TIME_PACKET_SIZE
    return bytes(pkt)


def build_mode_packet(mode):
    """Build Tag_CMD_Packet for an operational-mode switch."""
    if mode not in (MODE_MASTER, MODE_REMOTE):
        raise ValueError(f"mode must be {MODE_MASTER!r} or {MODE_REMOTE!r}")
    pkt = bytearray()
    pkt += HINK_HEADER
    pkt += bytes([mode[0], 0x5A, 0xA5, 0])
    assert len(pkt) == MODE_PACKET_SIZE
    return bytes(pkt)


def _read_ack(sock, timeout=ACK_TIMEOUT):
    """Read the controller's reply, matching xLights' ReadLineFromSocket.

    Skips until '|', then collects printable characters, stopping at the first
    non-printable byte or the timeout.
    """
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    started = False
    out = []
    while time.monotonic() < deadline:
        try:
            b = sock.recv(1)
        except socket.timeout:
            break
        except OSError as exc:
            raise HinksPixError(f"socket error while reading ACK: {exc}") from exc
        if not b:
            break
        ch = b[0]
        if not started:
            if ch == 0x7C:            # '|'
                started = True
                out.append("|")
            continue
        if 32 <= ch < 127:
            out.append(chr(ch))
        else:
            break
    return "".join(out)


class HinksPixTcp:
    """Raw-TCP client. One connection per operation, as xLights does."""

    def __init__(self, ip, port=PORT, connect_timeout=5.0):
        self.ip = ip
        self.port = port
        self.connect_timeout = connect_timeout

    def _connect(self):
        try:
            s = socket.create_connection((self.ip, self.port),
                                         timeout=self.connect_timeout)
        except OSError as exc:
            raise HinksPixError(f"could not connect to {self.ip}:{self.port}: {exc}") from exc
        s.settimeout(ACK_TIMEOUT)
        return s

    def _send_expect_ack(self, sock, packet, what):
        try:
            sock.sendall(packet)
        except OSError as exc:
            raise HinksPixError(f"{what}: send failed: {exc}") from exc
        ack = _read_ack(sock)
        if ACK_TOKEN not in ack:
            raise HinksPixError(f"{what}: controller replied {ack!r} "
                                f"(expected {ACK_TOKEN})")
        return ack

    def upload(self, remote_name, data, mtime=None, progress_cb=None):
        """Upload ``data`` to ``remote_name`` on the controller's SD card.

        ``progress_cb(sent, total, message)`` is called per chunk; returning
        False from it aborts. Returns the number of bytes uploaded.
        """
        from datetime import datetime
        from hinkspix_files import fat_datetime_word

        data = bytes(data)
        total = len(data)
        when = mtime or datetime.now()
        chunks = max(1, (total + CHUNK_DATA - 1) // CHUNK_DATA)
        flush = needs_flush_chunk(total)
        sock = self._connect()
        try:
            sent = 0
            for i in range(chunks):
                part = data[i * CHUNK_DATA:(i + 1) * CHUNK_DATA]
                st = ST_FIRST if i == 0 else ST_APPEND
                self._send_expect_ack(sock, build_chunk(st, part),
                                      f"{remote_name} chunk {i + 1}/{chunks}")
                sent += len(part)
                if progress_cb and progress_cb(
                        sent, total, f"Uploading {remote_name}") is False:
                    raise HinksPixError(f"{remote_name}: aborted by caller")
            if flush:
                # Exact-multiple file: one zero-length append before the close,
                # so the controller sees the read that returns nothing. See
                # needs_flush_chunk (#944 B17b).
                self._send_expect_ack(sock, build_chunk(ST_APPEND, b""),
                                      f"{remote_name} flush")
            self._send_expect_ack(
                sock, build_close(remote_name, fat_datetime_word(when)),
                f"{remote_name} close")
        finally:
            try:
                sock.close()
            except OSError:
                pass
        log.info("HinksPix %s: uploaded %s (%d bytes in %d chunks)",
                 self.ip, remote_name, total, chunks)
        return total

    def set_time(self, when=None):
        """Set the controller clock. Required for schedule playback to be right."""
        from datetime import datetime
        when = when or datetime.now()
        sock = self._connect()
        try:
            self._send_expect_ack(sock, build_time_packet(when), "set_time")
        finally:
            sock.close()
        log.info("HinksPix %s: clock set to %s (dow=%d)", self.ip,
                 when.strftime("%H:%M:%S"), _DOW_FROM_PY[when.weekday()])
        return True

    def set_mode(self, mode):
        """Switch operational mode: 'G' standalone-from-SD, 'H' remote/slave."""
        if isinstance(mode, str):
            mode = mode.encode("ascii")
        sock = self._connect()
        try:
            self._send_expect_ack(sock, build_mode_packet(mode),
                                  f"set_mode {mode!r}")
        finally:
            sock.close()
        log.info("HinksPix %s: operational mode -> %s", self.ip, mode)
        return True
