#!/usr/bin/env python3
"""test_hinkspix_discover.py — #949: Discover finds HinksPix controllers.

A HinksPix answers neither SlyLED's UDP PING nor ArtPoll; its one fingerprint
is ``GET /XLights_BoardInfo.cgi`` -> ``{"CMD":"BD_INFO","Controller":"H",...}``.
Hermetic: fake controllers + decoys on 127.0.0.1, and a synthetic interface
table shaped like the kdocker3 bench host (bond0 + nine docker bridges).

Covers:
  * hinkspix_bridge.discover — only real BD_INFO replies (plain and gzipped,
    PRO and EasyLights) survive; non-JSON 200, JSON without BD_INFO, a foreign
    Controller code, a closed port and a slow host are all rejected, and the
    sweep finishes inside its time budget.
  * net_ifaces.sweep_hosts — real LAN subnet only (no docker bridges, own IP
    skipped), /16 narrowed to the /24 with a note, the host cap.
  * POST/GET /api/hinkspix/discover — background sweep, known IPs skipped,
    typed results, idempotent while running, one summary log line.

Run:
    python3 tests/test_hinkspix_discover.py
"""

import gzip
import http.server
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from collections import namedtuple

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-949-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_bridge as hb  # noqa: E402
import net_ifaces  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def eq(name, got, want):
    ok(name, got == want, f"got {got!r}, want {want!r}")


# The MS_160 unit's real reply (192.168.10.6, 2026-09-24), trimmed.
BD_INFO_PRO = {"CMD": "BD_INFO", "Controller": "H", "Type": "P", "MCPU": "MS_160",
               "PCPU": "PS_107", "ECPU": "ES_100", "WEB": "WB_110",
               "BD1": "L", "BD2": "S", "BD3": "N", "MaxU": "402", "NumU": "12"}
BD_INFO_EASY = dict(BD_INFO_PRO, Controller="E", MCPU="MS_155", MaxU="32")


def _server(body_fn, delay=0.0):
    """Threaded HTTP server on 127.0.0.1:<free>; returns 'host:port'."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if delay:
                time.sleep(delay)
            if self.path != hb.CGI_BOARD_INFO:
                self.send_response(404)
                self.end_headers()
                return
            body = body_fn()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"127.0.0.1:{srv.server_address[1]}"


def _closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


REAL_PRO = _server(lambda: json.dumps(BD_INFO_PRO).encode())
REAL_EASY_GZ = _server(lambda: gzip.compress(json.dumps(BD_INFO_EASY).encode()))
NOT_JSON = _server(lambda: b"<html><body>router login</body></html>")
NO_BD_INFO = _server(lambda: json.dumps({"CMD": "STATUS", "Controller": "H"}).encode())
FOREIGN = _server(lambda: json.dumps(dict(BD_INFO_PRO, Controller="X")).encode())
CLOSED = _closed_port()
SLOW = _server(lambda: json.dumps(BD_INFO_PRO).encode(), delay=5.0)


def test_sweep():
    hosts = [NOT_JSON, REAL_PRO, NO_BD_INFO, CLOSED, SLOW, FOREIGN, REAL_EASY_GZ]
    calls = []
    t0 = time.time()
    found = hb.discover(hosts, timeout=0.8, progress=lambda d, t: calls.append((d, t)))
    elapsed = time.time() - t0
    eq("only the real controllers are returned, in host order",
       [f["ip"] for f in found], [REAL_PRO, REAL_EASY_GZ])
    ok("finished inside the budget (slow host abandoned)", elapsed < 1 * 2 * 0.8 + 1.0 + 0.5, elapsed)
    by_ip = {f["ip"]: f for f in found}
    pro, easy = by_ip.get(REAL_PRO, {}), by_ip.get(REAL_EASY_GZ, {})
    eq("PRO identified", pro.get("model"), "HinksPix PRO")
    eq("PRO boards decoded", pro.get("boards"),
       {"BD1": "Long_Range", "BD2": "Local_SPI", "BD3": "Not_Present"})
    eq("PRO MCPU", pro.get("mcpuRaw"), "MS_160")
    eq("PRO uploadSupported (MS_160 >= 151)", pro.get("uploadSupported"), True)
    eq("gzipped EasyLights reply decoded", easy.get("model"), "EasyLights")
    ok("progress reported for every host that finished",
       calls and calls[-1][1] == len(hosts) and len(calls) >= len(hosts) - 1, calls[-3:])

    eq("no hosts -> no work", hb.discover([]), [])
    ok("is_board_info rejects a dict without CMD", not hb.is_board_info({"Controller": "H"}))

    # A /24's worth of dead hosts finishes in rounds, not serially.
    dead = [_closed_port() for _ in range(128)]
    t0 = time.time()
    eq("128 closed ports -> nothing", hb.discover(dead, timeout=0.8, workers=64), [])
    ok("128 dead hosts in < 5 s", time.time() - t0 < 5.0, time.time() - t0)


Addr = namedtuple("Addr", "family address netmask broadcast ptp")
Stat = namedtuple("Stat", "isup flags")


class _FakePsutil:
    def __init__(self, table):
        self.table = table

    def net_if_addrs(self):
        return self.table

    def net_if_stats(self):
        return {k: Stat(True, "up,broadcast,running") for k in self.table}


def test_sweep_hosts():
    table = {"bond0": [Addr(socket.AF_INET, "192.168.10.38", "255.255.255.0", None, None)],
             "docker0": [Addr(socket.AF_INET, "172.17.0.1", "255.255.0.0", None, None)]}
    for n in range(18, 26):
        table[f"br-{n}abc"] = [Addr(socket.AF_INET, f"172.{n}.0.1", "255.255.0.0", None, None)]
    saved = net_ifaces.psutil
    try:
        net_ifaces.psutil = _FakePsutil(table)
        hosts, notes = net_ifaces.sweep_hosts()
    finally:
        net_ifaces.psutil = saved
    eq("kdocker3: the /24 minus our own address", len(hosts), 253)
    ok("kdocker3: no docker bridge host swept", not [h for h in hosts if h.startswith("172.")])
    ok("own address skipped", "192.168.10.38" not in hosts)
    ok("HinksPix .6 in range", "192.168.10.6" in hosts)
    eq("no notes for a plain /24", notes, [])

    wide = [net_ifaces._entry("en0", "10.20.3.4", 16, "t")]
    hosts, notes = net_ifaces.sweep_hosts(wide)
    eq("/16 narrowed to the /24 around the address", (hosts[0], hosts[-1], len(hosts)),
       ("10.20.3.1", "10.20.3.254", 253))
    ok("/16 narrowing is noted", notes and "wider than /22" in notes[0], notes)

    h22, _ = net_ifaces.sweep_hosts([net_ifaces._entry("en0", "192.168.8.1", 22, "t")])
    eq("/22 swept whole (1022 hosts minus own)", len(h22), 1021)
    three = [net_ifaces._entry(f"en{i}", f"10.{i}.0.1", 22, "t") for i in range(3)]
    capped, notes = net_ifaces.sweep_hosts(three, max_hosts=1024)
    eq("host cap", len(capped), 1024)
    ok("cap is noted", any("cap" in n for n in notes), notes)


def test_routes():
    import parent_server as ps
    import orch_hinkspix as oh

    lines = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = ListHandler()
    ps.log.addHandler(handler)
    saved_hosts = net_ifaces.sweep_hosts
    known_child = {"id": 9949, "ip": REAL_EASY_GZ, "type": "hinkspix", "name": "known"}
    try:
        net_ifaces.sweep_hosts = lambda: ([REAL_PRO, REAL_EASY_GZ, NOT_JSON, CLOSED],
                                          ["test note"])
        ps._children.append(known_child)
        with ps.app.test_client() as c:
            r = c.post("/api/hinkspix/discover")
            st = r.get_json()
            ok("start returns 200 + pending", r.status_code == 200 and st.get("pending"), st)
            eq("known IP not swept (total excludes it)", st.get("total"), 3)
            again = c.post("/api/hinkspix/discover").get_json()
            ok("second start while running is idempotent", again.get("pending") and again.get("total") == 3, again)
            for _ in range(100):
                st = c.get("/api/hinkspix/discover").get_json()
                if not st.get("pending"):
                    break
                time.sleep(0.1)
            ok("sweep completes", not st.get("pending"), st)
            found = st.get("found") or []
            eq("found only the new controller", [f["ip"] for f in found], [REAL_PRO])
            if found:
                f = found[0]
                eq("typed hinkspix", f.get("type"), "hinkspix")
                eq("boardType is the model", f.get("boardType"), "HinksPix PRO")
                eq("firmware carried", f.get("mcpuRaw"), "MS_160")
            eq("notes surfaced", st.get("notes"), ["test note"])
            eq("progress done == total", (st.get("done"), st.get("total")), (3, 3))
            ok("elapsedMs recorded", isinstance(st.get("elapsedMs"), int), st.get("elapsedMs"))

            # Added after the sweep -> drops out of the results.
            ps._children.append({"id": 9950, "ip": REAL_PRO, "type": "hinkspix"})
            st = c.get("/api/hinkspix/discover").get_json()
            eq("controller added meanwhile is no longer offered", st.get("found"), [])
        summary = [ln for ln in lines if ln.startswith("HinksPix sweep:")]
        eq("one summary log line per sweep", len(summary), 1)
        ok("summary names hosts and the find",
           summary and "hosts=3" in summary[0] and REAL_PRO in summary[0], summary)
    finally:
        net_ifaces.sweep_hosts = saved_hosts
        ps._children[:] = [c for c in ps._children if c.get("id") not in (9949, 9950)]
        ps.log.removeHandler(handler)
        oh._sweep["pending"] = False


def main():
    test_sweep()
    test_sweep_hosts()
    test_routes()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({str(detail)[:300]})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
