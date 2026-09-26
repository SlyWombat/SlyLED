#!/usr/bin/env python3
"""Standalone fake HinksPix PRO (MS_160) for the isolated QA network.

Speaks the same CGI surface as the operator's real controller (GET with
DATA:/BLK:/ROW: headers, gzipped replies, OP_MODE drops the connection),
seeded from the replies captured from the real unit in
hinkspix_ms160_capture_2026_09_23/. Extracted from qa_943_hinkspix.py so it
can run in its own container without importing the orchestrator.

Adds GetInfo.cgi ROW 906/907 and an E1.31 receiver on UDP 5568 that counts
packets per universe, so the '907' receive counters behave like the real
controller's and tests can prove sACN arrived.

  python fake_hinkspix.py [--port 80]
"""
import argparse
import collections
import gzip
import http.server
import json
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path

CAPTURE = Path(__file__).resolve().parent / "hinkspix_ms160_capture_2026_09_23"

class FakeHinks:
    def __init__(self):
        self.board_info = json.loads((CAPTURE / "boardinfo.json").read_text())
        self.data_mode = json.loads((CAPTURE / "data_mode_blk0.json").read_text())
        self.ports = {b: json.loads((CAPTURE / f"port_config_blk{b}.json").read_text())["LIST"]
                      for b in range(3)}
        # Factory table as captured: 32 universes x 300 ch, then filler rows.
        rows = [f"{i},{i},300,1,{300 * (i - 1) + 1},{300 * i}" if i <= 32
                else f"{i},{i},0,1,0,0" for i in range(1, 403)]
        self.e131 = {b: [{"V": r} for r in rows[b * 6:b * 6 + 6]] for b in range(67)}
        self.log = []            # (method, path, headers-dict, body-bytes)
        self.reject_cmd = None   # CMD name to answer ERROR for
        self.reject_once = False
        self.lock = threading.Lock()

    def writes(self):
        return [e for e in self.log if e[1] != "/XLights_BoardInfo.cgi"
                and e[2].get("DATA") is not None]


def make_handler(fake):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _reply(self, obj):
            raw = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, separators=(",", ":"))
            raw = raw.encode() if isinstance(raw, str) else raw
            body = gzip.compress(raw)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self):
            self._reply({"CMD": "POST", "ERROR": "ERROR"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            with fake.lock:
                fake.log.append(("POST", self.path, {k.upper(): v for k, v in self.headers.items()}, body))
            self._error()

        def do_GET(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            hdr = {k.upper(): v for k, v in self.headers.items()}
            with fake.lock:
                fake.log.append(("GET", self.path, hdr, body))
            path = self.path.split("?")[0]
            data = self.headers.get("DATA")
            blk = self.headers.get("BLK")
            if body:
                return self._error()
            if path == "/XLights_BoardInfo.cgi":
                return self._reply(fake.board_info)
            if path == "/Xlights_Data_Mode.cgi":
                return self._reply(fake.data_mode if blk == "0" else {"CMD": "POST", "ERROR": "ERROR"})
            if path == "/Xlights_Board_Port_Config.cgi":
                b = int(blk) if blk and blk.isdigit() else 0
                return self._reply({"CMD": "PCONFIG", "BOARD": str(b), "LIST": fake.ports.get(b, [])})
            if path == "/GetE131Data.cgi":
                row = self.headers.get("ROW")
                b = int(row) if row and row.isdigit() else 0
                return self._reply(",".join(x["V"] for x in fake.e131.get(b, [])))
            if path == "/Xlights_UnPack_Config.cgi":
                return self._reply({"CMD": "POST", "OK": "OK"} if data else {"CMD": "POST", "ERROR": "ERROR"})
            if path == "/Xlights_PostData.cgi":
                if not data:
                    return self._error()
                try:
                    cmd = json.loads(data)
                except ValueError:
                    return self._error()
                name = cmd.get("CMD")
                if name == fake.reject_cmd:
                    if fake.reject_once:
                        fake.reject_cmd = None
                    return self._error()
                if name == "OP_MODE":
                    self.close_connection = True
                    self.connection.shutdown(2)   # reboots — never replies
                    return
                if name == "PCONFIG":
                    fake.ports[int(cmd["BOARD"])] = cmd["LIST"]
                elif name == "E131":
                    fake.e131[int(cmd["BLK"])] = cmd["LIST"]
                elif name == "BD_INFO":
                    fake.board_info["NumU"] = cmd["NumU"]
                elif name == "DATA_MODE":
                    for k, v in cmd.items():
                        if k != "CMD":
                            fake.data_mode[k] = v
                else:
                    return self._error()
                return self._reply({"CMD": "POST", "OK": "OK"})
            return self._error()
    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True



E131_RX = collections.Counter()


def e131_receiver():
    """Count E1.31 data packets per universe (what ROW 907 reports)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", 5568))
    while True:
        pkt, _ = s.recvfrom(1500)
        if len(pkt) >= 126 and pkt[4:16] == b"ASC-E1.17\x00\x00\x00":
            E131_RX[struct.unpack("!H", pkt[113:115])[0]] += 1


def patch_getinfo(handler_cls):
    orig = handler_cls.do_GET

    def do_GET(self):
        if self.path.split("?")[0] == "/GetInfo.cgi":
            row = self.headers.get("ROW")
            if row == "907":
                return self._reply(",".join(f"{u},{E131_RX.get(u, 0)},0" for u in (1, 2)))
            if row == "906":
                return self._reply("A,0,B,1,C,0,D,0,E,2,F,FAKE HinksPix (QA isolated net) MS_160")
        return orig(self)
    handler_cls.do_GET = do_GET
    return handler_cls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=80)
    a = ap.parse_args()
    threading.Thread(target=e131_receiver, daemon=True).start()
    fake = FakeHinks()
    srv = Server(("0.0.0.0", a.port), patch_getinfo(make_handler(fake)))
    print(f"fake HinksPix MS_160 on :{a.port} (E1.31 rx on 5568)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
