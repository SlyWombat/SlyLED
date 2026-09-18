#!/usr/bin/env python3
"""HinksPix offline scheduled playback tests (#941).

Two halves:

  * **Wire/format parity** — struct sizes, the 18-byte header literal,
    TotalSize arithmetic, the FAT date word, and every documented .hseq header
    offset, plus the exact .ply / .sched text. These are transcribed from the
    xLights exporter and are the only spec that exists, so they are pinned
    byte-for-byte (same approach as tests/test_mmwave_wire_parity.py).

  * **Protocol round-trip** — an in-process fake controller that speaks the
    real chunked protocol, reassembles uploaded files and replies `|FOK`. It
    proves what actually lands on the card is byte-identical to what we
    rendered, which no amount of struct checking can.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_offline.py
"""

import datetime
import io
import os
import socket
import struct
import sys
import tempfile
import threading

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hpoff-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_files as hf  # noqa: E402
import hinkspix_tcp as ht  # noqa: E402

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


class FakeHinksPix(threading.Thread):
    """In-process controller: parses chunks, reassembles files, ACKs `|FOK`."""

    def __init__(self, fail_after=None):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.files = {}          # remote name -> bytes
        self.closes = []         # (name, dttm)
        self.times = []          # (hr, min, sec, dow)
        self.modes = []          # b'G' / b'H'
        self.fail_after = fail_after
        self._chunks_seen = 0
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        pending = bytearray()
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
                while len(buf) >= 22:
                    if buf[:18] != ht.HINK_HEADER:
                        return
                    cmd = buf[18]
                    if cmd == ord("F"):
                        if len(buf) < 28:
                            break
                        total, stype, dsize = struct.unpack_from("<HHH", buf, 22)
                        if len(buf) < total:
                            break
                        payload = buf[28:28 + dsize]
                        buf = buf[total:]
                        self._chunks_seen += 1
                        if self.fail_after and self._chunks_seen > self.fail_after:
                            conn.sendall(b"|FERR disk full\n")
                            continue
                        if stype == ht.ST_FIRST:
                            pending = bytearray(payload)
                        elif stype == ht.ST_APPEND:
                            pending += payload
                        elif stype == ht.ST_CLOSE:
                            name = payload[:30].split(b"\x00")[0].decode()
                            dttm = struct.unpack_from("<I", payload, 30)[0]
                            self.files[name] = bytes(pending)
                            self.closes.append((name, dttm))
                            pending = bytearray()
                        conn.sendall(b"|FOK\n")
                    elif cmd == ord("D"):
                        if len(buf) < 26:
                            break
                        self.times.append(tuple(buf[22:26]))
                        buf = buf[26:]
                        conn.sendall(b"|FOK\n")
                    else:
                        self.modes.append(bytes([cmd]))
                        buf = buf[22:]
                        conn.sendall(b"|FOK\n")
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    print("Wire parity — struct sizes (#pragma pack(2) layouts)")
    ok("Tag_Packet is 608 bytes at full payload",
       len(ht.build_chunk(0, b"x" * 580)) == 608)
    ok("HINK header literal is exactly 18 bytes",
       ht.HINK_HEADER == b"HINK TCP_CMD  \r\n\r\n" and len(ht.HINK_HEADER) == 18)
    ok("close packet is 62 bytes (28 + 34)", len(ht.build_close("A.hseq", 0)) == 62)
    ok("Tag_Dow_TimePacket is 26 bytes",
       len(ht.build_time_packet(datetime.datetime(2026, 9, 18, 20, 5, 30))) == 26)
    ok("Tag_CMD_Packet is 22 bytes", len(ht.build_mode_packet(b"G")) == 22)

    print("Wire parity — TotalSize arithmetic")
    small = ht.build_chunk(1, b"abc")
    total, stype, dsize = struct.unpack_from("<HHH", small, 22)
    ok("TotalSize == 28 + DataSize", total == 28 + 3 == len(small))
    ok("StructType round-trips", stype == 1)
    ok("DataSize round-trips", dsize == 3)
    ok("oversized payload is rejected",
       _raises(lambda: ht.build_chunk(0, b"x" * 581), ValueError))

    print("Wire parity — FAT date/time word")
    dt = datetime.datetime(2026, 9, 18, 20, 5, 30)
    w = hf.fat_datetime_word(dt)
    ok("date half encodes y/m/d",
       (w >> 16) == (((2026 - 1980) << 9) | (9 << 5) | 18))
    ok("time half encodes h/m/2s",
       (w & 0xFFFF) == ((20 << 11) | (5 << 5) | 15))

    print("Wire parity — .hseq header offsets")
    h = hf.hseq_header(2400, 1200, 25, "192.168.10.6")
    ok("header is 336 bytes", len(h) == 336)
    ok("[0:4] HSEQ", h[0:4] == b"HSEQ")
    ok("[4] format version 3", h[4] == 3)
    ok("[9] slave count 0", h[9] == 0)
    ok("[16] framerate 25ms -> 1102", struct.unpack_from("<H", h, 16)[0] == 1102)
    ok("[16] framerate 50ms -> 2205",
       struct.unpack_from("<H", hf.hseq_header(10, 3, 50, "1.2.3.4"), 16)[0] == 2205)
    ok("[20] u32 numFrames", struct.unpack_from("<I", h, 20)[0] == 2400)
    ok("[24] u32 channelCount", struct.unpack_from("<I", h, 24)[0] == 1200)
    ok("[28] master IP, NUL-terminated",
       h[28:40].split(b"\x00")[0] == b"192.168.10.6")
    ok("[68] u16 master channels", struct.unpack_from("<H", h, 68)[0] == 1200)
    ok("[320:324] PSEQ", h[320:324] == b"PSEQ")
    ok("[324]=0x40 [327]=1 [328]=28", (h[324], h[327], h[328]) == (0x40, 1, 28))
    ok("[330] u16 original channel count", struct.unpack_from("<H", h, 330)[0] == 1200)
    ok("[334] u16 numFrames", struct.unpack_from("<H", h, 334)[0] == 2400)
    ok("unsupported step time rejected",
       _raises(lambda: hf.hseq_header(10, 3, 33, "1.2.3.4"), hf.HinksPixFileError))
    ok("frame count over the u16 field rejected",
       _raises(lambda: hf.hseq_header(70000, 3, 25, "1.2.3.4"), hf.HinksPixFileError))

    print("Wire parity — .hseq body")
    buf = io.BytesIO()
    frames = [bytes([i] * 6) for i in range(5)]
    n = hf.write_hseq(buf, frames, 6, 25, "1.2.3.4")
    ok("size == 336 + frames*channels", n == 336 + 5 * 6 == len(buf.getvalue()))
    ok("frames are raw and unpadded",
       buf.getvalue()[336:342] == bytes([0] * 6)
       and buf.getvalue()[342:348] == bytes([1] * 6))
    ok("wrong-length frame rejected",
       _raises(lambda: hf.write_hseq(io.BytesIO(), [b"xx"], 6, 25, "1.2.3.4"),
               hf.HinksPixFileError))

    print("Wire parity — playlist and schedule text")
    ok("playlist matches the exact xLights form",
       hf.playlist_text([{"hseq": "SHOW.hseq"}])
       == '[{"H":"SHOW.hseq","A":"NONE","D":2}]')
    ok("audio slot is used when present",
       '"A":"B.au"' in hf.playlist_text([{"hseq": "B.hseq", "au": "B.au"}]))
    ok("8pm-11pm daily serialises correctly",
       hf.schedule_text([{"start": "20:00", "end": "23:00"}], "SHOW")
       == '[{"S":"2000","E":"2300","P":"SHOW.ply","Q":0}]')
    ok("rows are sorted by start time",
       hf.schedule_text([{"start": "22:00", "end": "23:00"},
                         {"start": "18:00", "end": "19:00"}], "S").index('"1800"') <
       hf.schedule_text([{"start": "22:00", "end": "23:00"},
                         {"start": "18:00", "end": "19:00"}], "S").index('"2200"'))
    ok("disabled rows are omitted",
       hf.schedule_text([{"start": "20:00", "end": "23:00", "enabled": False}], "S") == "[]")
    ok("empty day serialises as [] (clears a stale schedule)",
       hf.schedule_text([], "S") == "[]")
    ok("repeat count is carried",
       '"Q":3' in hf.schedule_text([{"start": "1:00", "end": "2:00", "repeat": 3}], "S"))
    ok("schedule filename is DAY.sched", hf.schedule_filename("monday") == "MONDAY.sched")
    ok("non-weekday rejected",
       _raises(lambda: hf.schedule_filename("caturday"), hf.HinksPixFileError))

    print("Schedule validation — the midnight rule")
    ok("overnight window rejected",
       hf.validate_schedule_row({"start": "20:00", "end": "01:00"}) is not None)
    ok("end == start rejected",
       hf.validate_schedule_row({"start": "20:00", "end": "20:00"}) is not None)
    ok("out-of-range hour rejected",
       hf.validate_schedule_row({"start": "25:00", "end": "26:00"}) is not None)
    ok("valid window accepted",
       hf.validate_schedule_row({"start": "20:00", "end": "23:00"}) is None)
    ok("split_overnight produces two same-day windows",
       hf.split_overnight("20:00", "01:00") == [("20:00", "23:59"), ("00:00", "01:00")])
    ok("split_overnight leaves a valid window alone",
       hf.split_overnight("20:00", "23:00") == [("20:00", "23:00")])

    print("Sequence naming")
    ok("uppercase alphanumeric only", hf.short_name("Blue Blue Tree!") == "BLUEBLUETREE")
    ok("truncated to 20 chars", len(hf.short_name("A" * 40)) == 20)
    ok("collisions get a numeric suffix",
       hf.short_name("Show", {"SHOW"}) == "SHOW1")
    ok("suffix keeps the 20-char cap",
       len(hf.short_name("B" * 20, {"B" * 20})) == 20)
    ok("empty name still yields something valid", hf.short_name("!!!") == "SEQ")

    print("Protocol round-trip — against a fake controller")
    srv = FakeHinksPix()
    srv.start()
    try:
        tcp = ht.HinksPixTcp("127.0.0.1", port=srv.port)
        payload = bytes(range(256)) * 9        # 2304 B -> 4 chunks
        seen = []
        tcp.upload("SHOW.hseq", payload,
                   mtime=dt, progress_cb=lambda s, t, m: seen.append(s) or True)
        ok("reassembled file is byte-identical", srv.files.get("SHOW.hseq") == payload)
        ok("chunked at 580-byte payloads", len(seen) == 4, f"chunks={len(seen)}")
        ok("progress reported monotonically to the total",
           seen == sorted(seen) and seen[-1] == len(payload))
        ok("close packet carried the filename", srv.closes[-1][0] == "SHOW.hseq")
        ok("close packet carried the FAT timestamp",
           srv.closes[-1][1] == hf.fat_datetime_word(dt))

        small = b"[]"
        tcp.upload("MONDAY.sched", small)
        ok("a file smaller than one chunk still uploads",
           srv.files.get("MONDAY.sched") == small)

        tcp.set_time(datetime.datetime(2026, 9, 18, 20, 5, 30))   # a Friday
        ok("time packet carries h/m/s", srv.times[-1][:3] == (20, 5, 30))
        ok("weekday maps Sunday=0 (Friday -> 5)", srv.times[-1][3] == 5)

        tcp.set_mode(ht.MODE_MASTER)
        ok("mode packet reaches the controller", srv.modes[-1] == b"G")
    finally:
        srv.stop()

    print("Protocol round-trip — failures surface, never silently succeed")
    srv2 = FakeHinksPix(fail_after=1)
    srv2.start()
    try:
        tcp2 = ht.HinksPixTcp("127.0.0.1", port=srv2.port)
        ok("a rejected chunk raises with the controller's text",
           _raises(lambda: tcp2.upload("BIG.hseq", b"x" * 2000), ht.HinksPixError))
    finally:
        srv2.stop()

    dead = ht.HinksPixTcp("127.0.0.1", port=1)
    ok("unreachable controller raises HinksPixError",
       _raises(lambda: dead.set_time(), ht.HinksPixError))

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


def _raises(fn, exc_type):
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
