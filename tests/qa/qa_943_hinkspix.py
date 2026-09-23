#!/usr/bin/env python3
"""QA acceptance harness for #943 — HinksPix wire protocol.

Independent of the implementation's own tests (tests/test_hinkspix_wire.py
mocks urlopen). This harness stands up a **real HTTP socket** fake controller
whose behaviour is taken from two primary sources only:

  * the xLights driver (src-core/controllers/HinksPix.cpp) — the reference
    client, and
  * replies captured from the operator's unit (MS_160) on 2026-09-23, in
    tests/qa/hinkspix_ms160_capture_2026_09_23/.

The fake is strict the way the real unit is: anything that is not a GET with
the command in a ``DATA:`` header gets ``{"CMD":"POST","ERROR":"ERROR"}``;
port-config reads without ``BLK:`` answer for board 0; replies are gzipped
(MS_160 gzips); OP_MODE drops the connection (the unit reboots).

Everything is driven through the Flask routes (probe → PUT → device-config →
plan → apply → device-config), so it tests what the SPA will exercise.

Modes:
  python tests/qa/qa_943_hinkspix.py              offline (fake controller)
  python tests/qa/qa_943_hinkspix.py --hw-read    + read-only checks on the unit
  python tests/qa/qa_943_hinkspix.py --hw-apply   + APPLY garage eaves to the unit
                                                    (rewrites all ports, reboots)
"""

import gzip
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPTURE = HERE / "hinkspix_ms160_capture_2026_09_23"
UNIT_IP = "192.168.10.6"

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-qa943-")
sys.path.insert(0, str(HERE.parent.parent / "desktop" / "shared"))

import parent_server  # noqa: E402
from parent_server import app  # noqa: E402

_passed = _failed = 0
_findings = []


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))
    return cond


def finding(text):
    """Divergence from xLights that is not an acceptance failure."""
    _findings.append(text)
    print(f"  [NOTE] {text}")


# ── Strict fake controller ───────────────────────────────────────────────────

class FakeHinks:
    def __init__(self):
        self.board_info = json.loads((CAPTURE / "boardinfo.json").read_text())
        self.data_mode = json.loads((CAPTURE / "data_mode_blk0.json").read_text())
        self.ports = {b: json.loads((CAPTURE / f"port_config_blk{b}.json").read_text())["LIST"]
                      for b in range(3)}
        self.e131 = {}
        self.log = []            # (method, path, headers-dict, body-bytes)
        self.reject_cmd = None   # CMD name to answer ERROR for
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


# ── Helpers ──────────────────────────────────────────────────────────────────

EAVES_PORT = {"port": 17, "leds": 200, "enabled": True, "protocol": "ws2811",
              "colorOrder": "RGB"}
XL_EAVES_ROW = "17,1,1,200,600,0,0,0,100,1"       # HinksPixOutput::BuildCommand
XL_UNUSED = "{n},0,1,0,0,0,0,0,100,1"            # HinksPix.h defaults, brightness 100
SERIAL_KEYS = ["CMD", "DMX_ACTIVE", "DMX_UNIV", "DMX_START", "DMX_CHAN_CNT",
               "DDP_DMX_ACTIVE", "DDP_DMX_START", "DDP_DMX_CHAN_CNT"]


def add_child(ip, cid):
    child = {"id": cid, "type": "hinkspix", "boardType": "HinksPix PRO",
             "ip": ip, "name": "Garage", "status": 0, "seen": 0, "sc": 0,
             "strings": [], "hinks": {"protocol": "e131", "baseUniverse": 1,
                                      "dmxOut": {"enabled": False, "universe": None},
                                      "ports": [], "configPushedAt": 0, "configHash": ""}}
    parent_server._children[:] = [c for c in parent_server._children if c.get("id") != cid]
    parent_server._children.append(child)
    return child


ACKED = []


def do_apply(c, cid, path="apply", extra=None):
    """POST apply/restore as a blocking job (#945: wait:true). Warnings block
    until acknowledged by code; acknowledge only non-error blockers, and record
    them so the report says what was waved through."""
    body = dict(extra or {}, wait=True)
    r = c.post(f"/api/hinkspix/{cid}/{path}", json=body)
    if r.status_code == 409:
        j = r.get_json() or {}
        blk = j.get("blocking") or []
        # Only ever wave through warnings known to be benign for these tests.
        # NEVER empty_config: acknowledging it pushes a blank layout (QA burned
        # the real unit's universe table this way on 2026-09-23).
        safe = {"reboot_required", "engine_protocol_mismatch", "port_unbound"}
        if blk and all(b.get("level") != "error" and b.get("code") in safe for b in blk):
            codes = [b.get("code") for b in blk]
            ACKED.append((cid, path, [(b.get("level"), b.get("code")) for b in blk]))
            print(f"  (acknowledging {[(b.get('level'), b.get('code')) for b in blk]})")
            body["ack"] = codes
            r = c.post(f"/api/hinkspix/{cid}/{path}", json=body)
    return r


def port_rows(dev_json, port):
    """Find a port in the device-config JSON regardless of nesting."""
    hits = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("output") == port or o.get("port") == port:
                hits.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(dev_json)
    return hits


# ── Offline acceptance ───────────────────────────────────────────────────────

def offline(c):
    fake = FakeHinks()
    srv = Server(("127.0.0.1", 0), make_handler(fake))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ip = f"127.0.0.1:{srv.server_address[1]}"
    cid = 9431
    add_child(ip, cid)

    print("\n== probe (BoardInfo from the MS_160 capture)")
    r = c.post(f"/api/hinkspix/{cid}/probe")
    j = r.get_json() or {}
    ok("probe 200", r.status_code == 200, r.data[:200])
    h = parent_server._children[-1]["hinks"]
    ok("probe records maxU 402", h.get("maxU") == 402, h.get("maxU"))
    ok("probe records mcpu 160", h.get("mcpu") == 160, h.get("mcpu"))
    ok("probe records BD1 Long_Range / BD2 Local_SPI / BD3 Not_Present",
       h.get("boards", {}).get("BD1") == "Long_Range"
       and h.get("boards", {}).get("BD2") == "Local_SPI"
       and h.get("boards", {}).get("BD3") == "Not_Present", h.get("boards"))

    print("\n== PUT garage eaves (port 17, 200 x WS2811 RGB, base universe 1)")
    r = c.put(f"/api/hinkspix/{cid}", json={"baseUniverse": 1, "protocol": "e131",
                                             "ports": [EAVES_PORT]})
    ok("PUT 200", r.status_code == 200, r.data[:300])

    print("\n== device-config before apply (factory rows)")
    r = c.get(f"/api/hinkspix/{cid}/device-config")
    j = r.get_json() or {}
    ok("device-config 200", r.status_code == 200, r.data[:300])
    blks = sorted({e[2].get("BLK") for e in fake.log if e[1] == "/Xlights_Board_Port_Config.cgi"})
    ok("port config read for BD1 and BD2 via BLK 0 / BLK 1 (not BD3)", blks == ["0", "1"], blks)
    ok("data mode read with BLK: 0",
       any(e[1] == "/Xlights_Data_Mode.cgi" and e[2].get("BLK") == "0" for e in fake.log))
    dev = (j.get("device") or {}).get("ports") or {}
    ok("device-config returns all 32 fitted-board ports (BD1+BD2)", len(dev) == 32, f"{len(dev)} ports")
    p17 = dev.get("17") or {}
    ok("device-config port 17 == factory row (100 px @ ch 4801-5100)",
       p17.get("pixels") == 100 and p17.get("start") == 4801 and p17.get("end") == 5100, p17)
    p1 = dev.get("1") or {}
    ok("device-config port 1 == factory row (100 px @ ch 1-300)",
       p1.get("pixels") == 100 and p1.get("start") == 1, p1)
    ok("diff reports port 17 as different", j.get("diff") and "17" in json.dumps(j.get("diff")),
       str(j.get("diff"))[:200])

    print("\n== plan (dry run — must not touch the device)")
    before = len(fake.log)
    r = c.get(f"/api/hinkspix/{cid}/plan")
    plan = r.get_json() or {}
    ok("plan 200", r.status_code == 200, r.data[:300])
    ok("plan sends no requests to the controller", len(fake.log) == before, len(fake.log) - before)
    reqs = plan.get("requests") or []
    ok("plan lists requests", len(reqs) > 0)
    print(f"  plan: {len(reqs)} requests; first={json.dumps(reqs[0])[:160] if reqs else None}")

    print("\n== apply")
    fake.log.clear()
    t0 = time.time()
    r = do_apply(c, cid)
    res = r.get_json() or {}
    print(f"  apply response keys: {sorted(res)[:12]}  state={json.dumps(res.get('state') or {})[:300]}")
    ok("apply (wait) 200 ok=true despite the reboot dropping the connection",
       r.status_code == 200 and res.get("ok") is True, r.data[:400])
    print(f"  apply took {time.time() - t0:.1f}s, {len(fake.log)} requests on the wire")

    full = list(fake.log)
    first_write = next((i for i, e in enumerate(full) if e[2].get("DATA")), len(full))
    ok("snapshot taken before the first write (BLK port reads precede writes)",
       any(e[1] == "/Xlights_Board_Port_Config.cgi" for e in full[:first_write]),
       [e[1] for e in full[:first_write]])
    ok("readback after the reboot (port reads follow the last OP_MODE)",
       any(e[1] == "/Xlights_Board_Port_Config.cgi" for e in full[max(i for i, e in enumerate(full) if "OP_MODE" in (e[2].get("DATA") or "")):]))
    log = [e for e in full if e[2].get("DATA")]
    ok("no POSTs on the wire", all(m == "GET" for m, *_ in log),
       [(m, p) for m, p, *_ in log if m != "GET"][:3])
    ok("no request bodies", all(not b for *_, b in log))
    ok("every request carries Content-type: text/plain",
       all((hd.get("CONTENT-TYPE") or "").startswith("text/plain")
           for _, _, hd, _ in log))
    plan_writes = [q for q in reqs if (q.get("headers") or {}).get("DATA")]
    ok("plan writes == wire writes (+1 for the doubled reboot)",
       len(log) == len(plan_writes) + 1, f"plan {len(plan_writes)} vs wire {len(log)}")

    cmds = []
    for m, p, hd, _ in log:
        d = hd.get("DATA")
        name = None
        if d:
            try:
                name = json.loads(d).get("CMD")
            except ValueError:
                name = "UNPACK" if p == "/Xlights_UnPack_Config.cgi" else "?"
        cmds.append((p, name, hd.get("BLK"), d))

    # Sequence: read mode, MODE, E131*, BD_INFO, PCONFIG*, serial, UnPack, OP_MODE x2
    seq = []
    for p, name, blk, d in cmds:
        tag = ("READ_MODE" if p == "/Xlights_Data_Mode.cgi" else
               "UNPACK" if p == "/Xlights_UnPack_Config.cgi" else name)
        if not seq or seq[-1] != tag or tag not in ("E131", "PCONFIG"):
            seq.append(tag)
    ok("write sequence matches HinksPix::SetOutputs",
       seq == ["DATA_MODE", "E131", "BD_INFO", "PCONFIG", "DATA_MODE",
               "UNPACK", "OP_MODE", "OP_MODE"], seq)

    mode_cmd = json.loads(cmds[0][3]) if cmds and cmds[0][3] else {}
    ok("first DATA_MODE is {CMD, MODE: E131} only", mode_cmd == {"CMD": "DATA_MODE", "MODE": "E131"}, mode_cmd)

    rows = []
    for b in sorted(fake.e131):
        rows += [x["V"] for x in fake.e131[b]]
    ok("E131 table has exactly MaxU (402) rows in 67 blocks",
       len(rows) == 402 and len(fake.e131) == 67, f"{len(rows)} rows / {len(fake.e131)} blocks")
    ok("E131 row 1 == 1,1,510,1,1,510 (1-based, controller ch 1-510)",
       rows[:1] == ["1,1,510,1,1,510"], rows[:2])
    if len(rows) > 1:
        r2 = rows[1].split(",")
        ok("E131 row 2 is index 2, universe 2, starts at controller ch 511",
           r2[:2] == ["2", "2"] and r2[4] == "511", rows[1])
        if rows[1] != "2,2,510,1,511,1020":
            finding(f"E131 row 2 is {rows[1]!r}; xLights sizes every universe at its "
                    f"configured 510 ch ('2,2,510,1,511,1020'). Pixels still map (port 17 "
                    f"ends at 600) — bench-confirm the controller accepts a short universe.")
    ok("E131 unused rows are 'i,i,0,1,0,0' (row 3 and row 402)",
       len(rows) >= 402 and rows[2] == "3,3,0,1,0,0" and rows[401] == "402,402,0,1,0,0",
       (rows[2:3], rows[401:402]))
    ok("BD_INFO NumU == 2", fake.board_info.get("NumU") == "2", fake.board_info.get("NumU"))

    pc = [(json.loads(d)) for p, n, _, d in cmds if n == "PCONFIG"]
    ok("PCONFIG sent for BOARD 0 and BOARD 1 only (BD3 Not_Present skipped)",
       [x["BOARD"] for x in pc] == ["0", "1"], [x.get("BOARD") for x in pc])
    ok("each PCONFIG carries exactly 16 rows", all(len(x["LIST"]) == 16 for x in pc),
       [len(x["LIST"]) for x in pc])
    b1 = {int(x["V"].split(",")[0]): x["V"] for x in fake.ports[1]}
    b0 = {int(x["V"].split(",")[0]): x["V"] for x in fake.ports[0]}
    ok("board 1 covers outputs 17-32", sorted(b1) == list(range(17, 33)), sorted(b1)[:3])
    ok(f"port 17 row == {XL_EAVES_ROW} (xLights BuildCommand)", b1.get(17) == XL_EAVES_ROW, b1.get(17))
    unused = [b0.get(1), b1.get(18)]
    ok("unused ports are written with 0 pixels", all(u and u.split(",")[3] == "0" for u in unused), unused)
    if unused[0] != XL_UNUSED.format(n=1):
        finding(f"unused row is {unused[0]!r}; xLights full-control default is "
                f"{XL_UNUSED.format(n=1)!r}")

    ser = [json.loads(d) for p, n, _, d in cmds if n == "DATA_MODE"][1:]
    ok("serial DATA_MODE has exactly xLights' 8 keys in order",
       ser and list(ser[0].keys()) == SERIAL_KEYS, ser[:1])
    unp = [d for p, n, _, d in cmds if p == "/Xlights_UnPack_Config.cgi"]
    ok("UnPack reset is the literal double-brace form",
       unp == ['{{"BLK":"0","NUM":"0","LEFT":"0","LIST":[]}}'], unp)
    ops = [json.loads(d) for p, n, _, d in cmds if n == "OP_MODE"]
    ok("OP_MODE ETHERNET sent twice", ops == [{"CMD": "OP_MODE", "MODE": "ETHERNET"}] * 2, ops)

    print("\n== device-config after apply (verify)")
    r = c.get(f"/api/hinkspix/{cid}/device-config")
    j = r.get_json() or {}
    ok("device-config 200 after apply", r.status_code == 200, r.data[:200])
    dev = (j.get("device") or {}).get("ports") or {}
    p17 = dev.get("17") or {}
    ok("readback port 17 == intended (200 px, ws2811, ch 1-600)",
       p17.get("pixels") == 200 and p17.get("protocol") == 1 and p17.get("start") == 1
       and p17.get("end") == 600, p17)
    ok("readback covers all 32 fitted-board ports", len(dev) == 32, f"{len(dev)} ports")
    print(f"  diff after apply: {json.dumps(j.get('diff'))[:400]}")

    print("\n== failure handling: controller rejects PCONFIG")
    fake.reject_cmd = "PCONFIG"
    fake.log.clear()
    r = do_apply(c, cid)
    res = r.get_json() or {}
    print(f"  rejected-apply response: {r.status_code} {json.dumps(res)[:400]}")
    ok("apply reports failure on a rejected command (ok=false)", res.get("ok") is False, r.status_code)
    ok("no reboot sent after a failure",
       not any("OP_MODE" in (e[2].get("DATA") or "") for e in fake.log))
    fake.reject_cmd = None

    print("\n== failure handling: unreachable controller")
    add_child("127.0.0.1:9", 9432)
    parent_server._children[-1]["hinks"].update(h)
    r = do_apply(c, 9432)
    ok("apply to an unreachable controller fails cleanly (not 500, ok=false)",
       r.status_code != 500 and (r.get_json() or {}).get("ok") is False, (r.status_code, r.data[:200]))
    srv.shutdown()


# ── Hardware ─────────────────────────────────────────────────────────────────

def hw_read(c):
    print(f"\n== HARDWARE read-only against {UNIT_IP}")
    cid = 9433
    # Drop the offline fake children: they claim universes 1-2 and would make
    # the PUT below collide (-> empty plan that blanks every port).
    parent_server._children[:] = [x for x in parent_server._children
                                  if x.get("id") not in (9431, 9432)]
    add_child(UNIT_IP, cid)
    r = c.post(f"/api/hinkspix/{cid}/probe")
    ok("probe real unit", r.status_code == 200, r.data[:200])
    h = parent_server._children[-1]["hinks"]
    print(f"  mcpu={h.get('mcpu')} maxU={h.get('maxU')} boards={h.get('boards')}")
    r = c.get(f"/api/hinkspix/{cid}/device-config")
    j = r.get_json() or {}
    ok("device-config against the real unit", r.status_code == 200, r.data[:300])
    dev = (j.get("device") or {}).get("ports") or {}
    ok("real unit: device-config returns 32 ports", len(dev) == 32, f"{len(dev)} ports: {sorted(dev, key=int)[:5]}")
    # Compare against an independent direct read (curl-equivalent, BLK header)
    # rather than a hard-coded state: the unit may hold factory or applied rows.
    import urllib.request
    req = urllib.request.Request(f"http://{UNIT_IP}/Xlights_Board_Port_Config.cgi",
                                 headers={"Content-type": "text/plain", "BLK": "1"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    direct = json.loads(raw)["LIST"][0]["V"].split(",")
    p17 = dev.get("17") or {}
    ok(f"real unit: device-config port 17 == direct BLK 1 read ({','.join(direct)})",
       [p17.get(k) for k in ("output", "protocol", "start", "pixels", "end")] == [int(x) for x in direct[:5]], p17)
    r = c.put(f"/api/hinkspix/{cid}", json={"baseUniverse": 1, "protocol": "e131", "ports": [EAVES_PORT]})
    ok("real unit: PUT garage eaves 200", r.status_code == 200, r.data[:300])
    r = c.get(f"/api/hinkspix/{cid}/plan")
    plan = r.get_json() or {}
    ok("plan for garage eaves against the real unit's probe", r.status_code == 200, r.data[:300])
    pc1 = [q for q in plan.get("requests", []) if '"BOARD":"1"' in (q.get("headers", {}).get("DATA") or "")]
    ok("real-unit plan writes port 17 = 17,1,1,200,600,... (guard before any apply)",
       plan.get("universesUsed") == 2 and pc1 and XL_EAVES_ROW in pc1[0]["headers"]["DATA"],
       f"universesUsed={plan.get('universesUsed')}")
    Path(os.environ["SLYLED_DATA"], "plan_real_unit.json").write_text(json.dumps(r.get_json(), indent=1))
    print(f"  plan saved to {os.environ['SLYLED_DATA']}/plan_real_unit.json")
    return cid


def hw_apply(c, cid):
    if _failed:
        print("\n!! refusing hardware apply: earlier checks failed")
        return
    print(f"\n== HARDWARE APPLY garage eaves to {UNIT_IP} (controller will reboot)")
    r = c.post(f"/api/hinkspix/{cid}/apply", json={})
    res = r.get_json() or {}
    ok("apply against the real unit", r.status_code == 200 and res.get("ok"), json.dumps(res)[:400])
    deadline = time.time() + 90
    back = False
    while time.time() < deadline:
        time.sleep(3)
        if c.post(f"/api/hinkspix/{cid}/probe").status_code == 200:
            back = True
            break
    ok("controller back after reboot (<90 s)", back)
    r = c.get(f"/api/hinkspix/{cid}/device-config")
    j = r.get_json() or {}
    ok("device-config after apply", r.status_code == 200)
    print(f"  port 17: {json.dumps(port_rows(j.get('device'), 17)[:1])}")
    print(f"  diff: {json.dumps(j.get('diff'))[:600]}")


def main():
    c = app.test_client()
    offline(c)
    if "--hw-read" in sys.argv or "--hw-apply" in sys.argv:
        cid = hw_read(c)
        if "--hw-apply" in sys.argv:
            hw_apply(c, cid)
    if ACKED:
        print(f"acknowledged warnings: {ACKED}")
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    if _findings:
        print(f"{len(_findings)} note(s) — divergences from xLights to review")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
