#!/usr/bin/env python3
"""HinksPix PRO wire protocol — self-check for #943.

Scope note: this is a *light* self-check that the #943 rewrite does what the
xLights reference client does, not the full QA contract suite. It asserts the
request shape and the command sequence; it does not stand up a gated
``http.server`` fake, replay golden captures from the MS_160 unit, or wire
anything into CI. That is the QA lane's job.

What it covers, all of which was wrong before #943:

  * every CGI call is an HTTP **GET** with the command in request headers
    (never a POST, never a body);
  * success needs the **quoted** `"OK"`, not a loose substring;
  * a board read sends `BLK:` — without it the controller only ever answers
    for board 0, which is how board 2's ports went invisible;
  * the universe table is **1-based** and always `MaxU` rows;
  * each port gets its own controller-absolute start channel, not 1;
  * PCONFIG covers all 16 rows of every fitted pixel board;
  * the sequence reboots at the end, and the UnPack reset is gated on MCPU;
  * DDP sends a channel range instead of a universe table.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_wire.py
"""

import gzip
import json
import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hxwire-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_bridge as hb  # noqa: E402
import hinkspix_config as hc  # noqa: E402
import parent_server  # noqa: E402   (imports orch_hinkspix after orch_state.bind)
from parent_server import app  # noqa: E402
from pixel_output import PixelOutputMap  # noqa: E402

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


class FakeReply:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    """A urlopen stand-in that records every request and replays a scripted
    body, so the transport can be asserted rather than assumed."""

    def __init__(self, body=b'"OK"', gzipped=False):
        self.body = gzip.compress(body) if gzipped else body
        self.seen = []

    def __call__(self, req, timeout=None):
        self.seen.append(req)
        return FakeReply(self.body)

    @property
    def only(self):
        assert len(self.seen) == 1, f"expected one request, got {len(self.seen)}"
        return self.seen[0]


def row_strings(start):
    """16 PCONFIG row strings for the block starting at output ``start``."""
    return [f"{o},0,1,0,0,0,0,0,100,1" for o in range(start, start + 16)]


def board_info_reply():
    """``XLights_BoardInfo.cgi`` for the operator's unit, verbatim shape.

    BD1 is a Long_Range board with nothing on it, BD2 a Local_SPI board
    carrying the eaves port, BD3 not fitted (the 2026-09-23 MS_160 capture).
    """
    return {"CMD": "BD_INFO", "Controller": "H", "Type": "P", "MCPU": "MS_160",
            "PCPU": "PS_39", "ECPU": "EZ_40", "WEB": "WF_113", "Smart4": "SR_0",
            "SmartAC": "SA_0", "BD1": "L", "BD2": "S", "BD3": "N",
            "MaxU": "402", "NumU": "32"}


def e131_reply(row, per=hb.UNIVERSES_PER_BLOCK):
    """One ``GetE131Data.cgi`` reply: the six rows the ``ROW:`` asks for."""
    rows = []
    for i in range(row * per + 1, row * per + per + 1):
        start = (i - 1) * 510 + 1
        rows.append(f"{i},{i},510,1,{start},{start + 509}")
    return ",".join(rows)


def rows_body(start):
    """The controller's reply for the block starting at output ``start``."""
    return json.dumps({"LIST": [{"V": v} for v in row_strings(start)]}).encode()


class BoardRecorder:
    """A urlopen stand-in that answers **per BLK**, the way the controller does.

    The previous fake replayed one scripted body no matter which block was
    asked for, so a read that was one block late still looked correct — which
    is exactly how the #943 QA blocker (a read sending the raw 0-based board
    instead of ``board - 1``) survived a green run. Keying the reply on the
    ``BLK:`` header is the entire point of this fake.
    """

    def __init__(self, blocks):
        self.blocks = blocks          # {blk: body}
        self.seen = []

    def __call__(self, req, timeout=None):
        self.seen.append(req)
        blk = int(req.headers.get("Blk"))
        if blk not in self.blocks:
            return FakeReply(b'{"LIST":[]}')
        return FakeReply(self.blocks[blk])

    @property
    def blks(self):
        return [int(r.headers.get("Blk")) for r in self.seen]


# ── The operator's real layout ───────────────────────────────────────────────
# Garage eaves: one model, 200 WS2811 RGB nodes on port 17, start channel 1
# (universes 1-2). Port 17 is on BLK 1 = BD2, which is a Local_SPI board;
# BD1 is a Long_Range board with nothing on it; BD3 is not fitted.

def operator_child():
    # The ip is a discard-port placeholder, not the unit's real address. Every
    # transport call in this suite is stubbed, so nothing is sent either way —
    # but a future route that reaches for a real reader should fail to connect
    # in a millisecond rather than configure the operator's controller.
    return {
        "id": 7, "type": "hinkspix", "ip": "127.0.0.1:9", "name": "Roofline",
        "status": 1,
        "hinks": {
            "model": "HinksPix PRO", "mcpu": 160, "hardwareV3": False,
            "maxU": 402, "uploadSupported": True,
            "boards": {"BD1": "Long_Range", "BD2": "Local_SPI",
                       "BD3": "Not_Present", "BD4": "Not_Present",
                       "BD5": "Not_Present"},
            "protocol": "e131", "baseUniverse": 1,
            "dmxOut": {"enabled": False, "universe": None},
            "ports": [{"port": 17, "leds": 200, "enabled": True,
                       "protocol": "ws2811", "colorOrder": "RGB"}],
        },
    }


def plan_for(child, mcpu=None, max_u=None):
    m = PixelOutputMap.build(child)
    intended = hc.intended_config(
        child, m, max_universes=child["hinks"]["maxU"] if max_u is None else max_u)
    return intended, hc.build_commands(
        intended, mcpu=child["hinks"]["mcpu"] if mcpu is None else mcpu)


def main():
    print("transport — GET, headers, quoted OK (#943 B1/B2)")
    rec = Recorder()
    hb.command("10.0.0.1", {"CMD": "OP_MODE", "MODE": "ETHERNET"}, opener=rec)
    req = rec.only
    ok("the request method is GET", req.get_method() == "GET", req.get_method())
    ok("no body is sent", req.data is None)
    ok("the command rides in the DATA header",
       req.headers.get("Data") == '{"CMD":"OP_MODE","MODE":"ETHERNET"}',
       req.headers.get("Data"))
    ok("Content-type is text/plain", req.headers.get("Content-type") == "text/plain")
    ok("unquoted OK is not an acknowledgement",
       hb.matches_ok("OK") is False and hb.matches_ok('"OK"') is True)

    print("transport — BLK selects the board (#943 B3, QA blocker)")
    # The real reply is JSON `{"LIST":[{"V": "<row>"}, ...]}` — verified against
    # InitExpansionBoardData (HinksPix.cpp:300-321), which is also where the
    # "exactly `length` rows" rule and `BLK: expansion - 1` come from. The fake
    # answers per BLK, so an off-by-one shows up as one instead of passing.
    boards = BoardRecorder({0: rows_body(1), 1: rows_body(17)})
    rows1 = hb.read_board_ports("10.0.0.1", 1, opener=boards)
    ok("board 1 asks for BLK 0, not BLK 1", boards.blks == [0], str(boards.blks))
    ok("board 1 comes back as ports 1-16, not 17-32",
       len(rows1) == 16 and rows1[0].split(",")[0] == "1"
       and rows1[-1].split(",")[0] == "16", f"{rows1[0]} .. {rows1[-1]}")
    ok("a board read sends no DATA header",
       boards.seen[0].headers.get("Data") is None)

    boards = BoardRecorder({0: rows_body(1), 1: rows_body(17)})
    rows2 = hb.read_board_ports("10.0.0.1", 2, opener=boards)
    ok("board 2 asks for BLK 1", boards.blks == [1], str(boards.blks))
    ok("board 2 comes back as ports 17-32 (the eaves port lives here)",
       len(rows2) == 16 and rows2[0].split(",")[0] == "17", rows2[0])

    # The controller-level read is the other half of B3: DATA_MODE is a register,
    # not a board, and xLights sends a fixed `BLK: 0` for it (HinksPix.cpp:333).
    # Pinned because it is the same board/BLK confusion in a neighbouring call.
    rec = Recorder(b'{"CMD":"DATA_MODE","MODE":"E131"}')
    hb.read_data_mode("10.0.0.1", opener=rec)
    ok("a DATA_MODE read sends BLK 0, not BLK 1",
       rec.only.headers.get("Blk") == "0", rec.only.headers.get("Blk"))

    # The QA symptom, pinned: rows for the *next* block filed under board 1.
    # decode_device_config looks for outputs 1-16, finds none, drops all 32 rows
    # and reports "board 1 not read" forever — which is what made verify
    # impossible on the real unit. Board 1 must therefore mean BLK 0.
    good = hc.decode_device_config(
        board_info={"MaxU": 402},
        board_ports={1: row_strings(1), 2: row_strings(17)})
    ok("both fitted boards' ports decode, 1-32",
       sorted(good.ports) == list(range(1, 33)), str(sorted(good.ports))[:60])
    ok("...keyed by the 1-based board number",
       sorted(good.board_ports) == [1, 2], str(sorted(good.board_ports)))
    wrong = hc.decode_device_config(
        board_info={"MaxU": 402}, board_ports={1: row_strings(17)})
    ok("rows for the next block under board 1 decode to nothing (the pre-fix "
       "symptom)", wrong.ports == {} and wrong.board_ports == {},
       f"ports={sorted(wrong.ports)}")

    short = json.dumps({"LIST": [{"V": v} for v in row_strings(1)[:15]]}).encode()
    try:
        hb.read_board_ports("10.0.0.1", 1, opener=Recorder(short))
        ok("a 15-row reply is rejected rather than padded", False, "no exception")
    except hb.HinksPixError:
        ok("a 15-row reply is rejected rather than padded", True)
    try:
        hb.read_board_ports("10.0.0.1", 1, opener=Recorder(b"not json at all"))
        ok("a non-JSON reply is reported, not guessed at", False, "no exception")
    except hb.HinksPixError:
        ok("a non-JSON reply is reported, not guessed at", True)

    print("transport — gzip replies (#943 B19)")
    rec = Recorder(b'"OK"', gzipped=True)
    ok("a gzipped reply is decompressed",
       hb.matches_ok(hb.command("10.0.0.1", {"CMD": "X"}, opener=rec)))
    # The MS_160 gzips replies it has to assemble; a stream that claims gzip and
    # isn't must surface the real bytes rather than vanish behind a decode error.
    rec = Recorder(b"\x1f\x8bnotreallygzip")
    try:
        hb.read_board_info("10.0.0.1", opener=rec)
        ok("a bogus gzip stream surfaces the raw bytes", False, "no exception")
    except hb.HinksPixError as exc:
        ok("a bogus gzip stream surfaces the raw bytes",
           "not JSON" in str(exc) and "notreallygzip" in str(exc), str(exc)[:120])

    print("plan — the operator's garage-eaves layout")
    child = operator_child()
    intended, cmds = plan_for(child)
    ok("the plan is a sequence, not one call", len(cmds) > 5, str(len(cmds)))

    first_read = cmds[0]
    ok("it reads the current mode first, with BLK 0",
       first_read.kind == "read" and first_read.path == hb.CGI_DATA_MODE
       and first_read.blk == 0)
    mode = cmds[1]
    ok("then sets the input mode",
       mode.data == '{"CMD":"DATA_MODE","MODE":"E131"}', mode.data)

    e131 = [c for c in cmds if '"CMD":"E131"' in (c.data or "")]
    ok("the universe table is sent in blocks of 6",
       len(e131) == 402 // hb.UNIVERSES_PER_BLOCK, str(len(e131)))
    ok("each block names its own BLK index, 0-based",
       [json.loads(c.data)["BLK"] for c in e131[:3]] == ["0", "1", "2"])
    first_rows = [x["V"] for x in json.loads(e131[0].data)["LIST"]]
    ok("rows are 1-based, with the absolute channel range",
       first_rows[0] == "1,1,510,1,1,510", first_rows[0])
    ok("the second row continues, not restarts",
       first_rows[1] == "2,2,90,1,511,600", first_rows[1])
    ok("rows past the layout are zero-length fillers",
       first_rows[2] == "3,3,0,1,0,0", first_rows[2])

    bd_info = [c for c in cmds if '"BD_INFO"' in (c.data or "")]
    ok("exactly one BD_INFO, carrying the used count not MaxU",
       len(bd_info) == 1 and '"NumU":"2"' in bd_info[0].data,
       bd_info[0].data if bd_info else "")

    print("plan — port rows (#943 B6/B7/B11)")
    pconfig = [c for c in cmds if '"PCONFIG"' in (c.data or "")]
    ok("PCONFIG covers the fitted pixel boards only (BD1, BD2)",
       len(pconfig) == 2 and '"BOARD":"0"' in pconfig[0].data
       and '"BOARD":"1"' in pconfig[1].data)
    board0 = [_row["V"] for _row in json.loads(pconfig[0].data)["LIST"]]
    board1 = [_row["V"] for _row in json.loads(pconfig[1].data)["LIST"]]
    ok("every board block is a full 16 rows",
       len(board0) == 16 and len(board1) == 16)
    ok("board 1's ports are all written as unused (nothing is plugged in)",
       all(r.split(",")[1] == "0" for r in board0))
    ok("port 17's row carries its own start and end channel",
       board1[0] == "17,1,1,200,600,0,0,0,100,1", board1[0])
    ok("...and it is the board's first row, not board 1's",
       board1[0].split(",")[0] == "17")

    print("plan — multi-port start channels are the headline fix (#943 B6)")
    multi = operator_child()
    multi["hinks"]["ports"] = [
        {"port": 1, "leds": 100, "enabled": True, "protocol": "ws2811",
         "colorOrder": "RGB"},
        {"port": 2, "leds": 100, "enabled": True, "protocol": "ws2811",
         "colorOrder": "RGB"},
    ]
    _i, cmds2 = plan_for(multi)
    pc = [c for c in cmds2 if '"PCONFIG"' in (c.data or "")]
    rows = [_row["V"] for _row in json.loads(pc[0].data)["LIST"]]
    starts = [int(r.split(",")[2]) for r in rows if int(r.split(",")[3]) > 0]
    ok("two ports get two different start channels, not 1 and 1",
       starts == [1, 301], str(starts))

    print("plan — RGBW reserves 4 channels per pixel (#943 B11)")
    rgbw = operator_child()
    rgbw["hinks"]["ports"] = [
        {"port": 1, "leds": 100, "enabled": True, "protocol": "ws2811",
         "colorOrder": "RGBW"},
        {"port": 2, "leds": 100, "enabled": True, "protocol": "ws2811",
         "colorOrder": "RGB"},
    ]
    intended_w, cmds_w = plan_for(rgbw)
    pc = [c for c in cmds_w if '"PCONFIG"' in (c.data or "")]
    rows = [_row["V"] for _row in json.loads(pc[0].data)["LIST"]]
    ok("an RGBW port's end channel is start + 4*pixels - 1",
       rows[0] == "1,1,1,100,400,0,6,0,100,1", rows[0])
    ok("and the next port starts after it, not after 3*pixels",
       rows[1] == "2,1,401,100,700,0,0,0,100,1", rows[1])

    print("plan — UnPack reset and the reboot (#943 B8/B13)")
    unpack = [c for c in cmds if c.path == hb.CGI_UNPACK]
    ok("the UnPack reset is sent for MCPU 160, double-braced",
       len(unpack) == 1
       and unpack[0].data == '{{"BLK":"0","NUM":"0","LEFT":"0","LIST":[]}}',
       unpack[0].data if unpack else "absent")
    _i, cmds_old = plan_for(child, mcpu=149)
    ok("and is skipped below the 151 gate",
       not [c for c in cmds_old if c.path == hb.CGI_UNPACK])
    ok("reboot comes last and only once in the plan",
       cmds[-1].kind == "reboot" and cmds[-1].data == '{"CMD":"OP_MODE","MODE":"ETHERNET"}'
       and len([c for c in cmds if c.kind == "reboot"]) == 1)
    ok("nothing else is sent after the reboot",
       not [c for c in cmds if c.kind == "reboot"][:-1])

    print("plan — DDP replaces the universe table (#943 B14)")
    ddp = operator_child()
    ddp["hinks"]["protocol"] = "ddp"
    ddp["hinks"]["hardwareV3"] = True
    _i, cmds_d = plan_for(ddp)
    ok("no E131 blocks and no BD_INFO for DDP",
       not [c for c in cmds_d if '"E131"' in (c.data or "")
            or '"BD_INFO"' in (c.data or "")])
    mode_d = cmds_d[1].data
    ok("the mode command carries the DDP channel range",
       '"MODE":"DDP"' in mode_d and '"DDP_CHAN_COUNT":600' in mode_d, mode_d)
    ok("PCONFIG is still sent for DDP", [c for c in cmds_d
                                         if '"PCONFIG"' in (c.data or "")])

    print("plan — guard rails")
    try:
        hc.build_commands(hc.intended_config(child, PixelOutputMap.build(child),
                                             max_universes=0))
        ok("an unprobed controller cannot be planned", False, "no exception")
    except hc.ConfigError:
        ok("an unprobed controller cannot be planned", True)
    no_boards = operator_child()
    no_boards["hinks"]["boards"] = {}
    try:
        _i, _c = plan_for(no_boards)
        ok("a controller with no known boards cannot be planned", False,
           "no exception")
    except hc.ConfigError:
        ok("a controller with no known boards cannot be planned", True)

    print("diff — an unread table is unread, not 402 differences")
    current = hc.decode_device_config(board_info={"MaxU": 402},
                                      data_mode={"MODE": "E131"})
    d = hc.diff(current, intended)
    uni_lines = [i for i in d["items"] if i["section"] == "universe"]
    ok("the universe section is one 'not read' line",
       len(uni_lines) == 1 and uni_lines[0].get("unread"), str(uni_lines))
    ok("identical mode is not reported as a difference",
       not [i for i in d["items"] if i["section"] == "mode"])

    print("routes — plan is a dry run, apply is the same sequence")
    with app.test_client() as c:
        parent_server._children.append(child)
        r = c.get("/api/hinkspix/7/plan")
        body = r.get_json()
        ok("GET plan succeeds", r.status_code == 200 and body.get("ok"),
           str(body)[:200])
        ok("plan reports the protocol and board count",
           body.get("protocol") == "E131" and body.get("boards") == [1, 2])
        ok("plan carries every request as method/path/headers",
           all(set(["method", "path", "headers"]).issubset(req)
               for req in body.get("requests", [])))
        ok("plan touches nothing on the device",
           not [req for req in body.get("requests", [])
                if req["kind"] == "reload"])
        # Every warning the plan raised is acknowledged on the way in, exactly
        # as the wizard does: an unacknowledged warn is a 409 by design (#945),
        # so a test that skipped them would be asserting a blocked push.
        acks = [f["code"] for f in body.get("findings", [])
                if f.get("level") == "warn"]

        # The apply job is asserted by capturing its calls: the transport itself
        # is covered above, so what matters here is the dispatch — the right
        # call per request kind, the reboot that always happens, and the reads
        # that bracket the run (the pre-write snapshot and the verification
        # readback, #945). Every function the job touches is stubbed, so nothing
        # here can reach the operator's real unit.
        #
        # `wait: true` runs the job to completion in the request, which is what
        # lets a single POST assert the whole thing; the SPA polls `GET` instead.
        calls = []
        real_command, real_read, real_faf = hb.command, hb.read_data_mode, hb.fire_and_forget
        real_info, real_ports, real_e131 = (hb.read_board_info, hb.read_board_ports,
                                            hb.read_e131_text)
        # The fake controller's universe table is taken from the plan itself, so
        # the verification readback has a true answer to compare against.
        planned = {}
        for req in body.get("requests", []):
            data = (req.get("headers") or {}).get("DATA") or ""
            if '"CMD":"E131"' in data:
                # BLK rides in the payload for the universe table, unlike the
                # port read where it is a request header (HinksPix.cpp:422).
                table = json.loads(data)
                planned[int(table["BLK"])] = [e["V"] for e in table["LIST"]]
        hb.command = lambda ip, payload, path=hb.CGI_POST_DATA, **kw: (
            calls.append(("write", path, payload)) or '"OK"')
        hb.read_data_mode = lambda ip, **kw: (calls.append(("read", kw)) or {"MODE": "E131"})
        hb.fire_and_forget = lambda ip, payload, **kw: (
            calls.append(("reboot", payload)) or [{"sent": True, "err": ""}])
        hb.read_board_info = lambda ip, **kw: board_info_reply()
        hb.read_board_ports = lambda ip, board, **kw: row_strings((board - 1) * 16 + 1)
        hb.read_e131_text = lambda ip, row, **kw: ",".join(planned.get(row, []))
        try:
            r = c.post("/api/hinkspix/7/apply",
                       json={"wait": True, "ack": acks})
            ab = r.get_json()
        finally:
            (hb.command, hb.read_data_mode, hb.fire_and_forget, hb.read_board_info,
             hb.read_board_ports, hb.read_e131_text) = (
                real_command, real_read, real_faf, real_info, real_ports, real_e131)
        state = ab.get("state") or {}
        verify = state.get("verify") or {}
        ok("POST apply runs the job and reports its final state",
           r.status_code == 200 and state.get("phase") == "done", str(ab)[:300])
        ok("apply sends the same number of requests the plan showed",
           len(state.get("stepsDone") or []) == len(body.get("requests")),
           str(len(state.get("stepsDone") or [])))
        ok("apply reboots the controller",
           [c for c in calls if c[0] in ("write", "reboot")][-1][0] == "reboot",
           str([c[0] for c in calls][-4:]))
        ok("the reboot is the last thing that happens",
           [c[0] for c in calls].count("reboot") == 1)
        ok("apply records a config hash so the UI can say in-sync",
           bool(state.get("lastApply", {}).get("backupId")))
        snap = os.path.join(os.environ["SLYLED_DATA"], "hinkspix", "7", "backups")
        ok("the controller was snapshotted before anything was written",
           os.path.isdir(snap) and bool(os.listdir(snap)), snap)
        ok("the run verified against a readback, not against its own send count",
           verify.get("counts", {}).get("ports") == 1, str(verify)[:200])
        ok("the universe table reads back as written — no universe differences",
           not [i for i in (verify.get("items") or [])
                if i.get("section") == "universe"], str(verify.get("items"))[:200])
        ok("a port the stubbed write never applied is reported, not hidden",
           verify.get("ok") is False
           and any(i.get("output") == 17 for i in (verify.get("items") or [])),
           str([i.get("output") for i in (verify.get("items") or [])]))

        # The read path, with a fake that honours the documented contract:
        # 1-based board in, that block's rows out. QA's blocker was this route
        # handing read_board_ports the wrong convention, so what catches a
        # regression is asserting what the route *asked for* — and the fake is
        # stubbed rather than letting the suite reach the operator's real unit.
        asked = []
        real_info, real_ports, real_mode = (hb.read_board_info, hb.read_board_ports,
                                            hb.read_data_mode)
        hb.read_board_info = lambda ip, **kw: board_info_reply()
        hb.read_board_ports = lambda ip, board, **kw: (
            asked.append(board) or row_strings((board - 1) * 16 + 1))
        hb.read_data_mode = lambda ip, **kw: {"MODE": "E131"}
        try:
            r = c.get("/api/hinkspix/7/device-config")
            dc = r.get_json()
        finally:
            hb.read_board_info, hb.read_board_ports, hb.read_data_mode = (
                real_info, real_ports, real_mode)
        dev = dc.get("device") or {}
        ok("device-config asks for the fitted boards 1-based, not 0/1",
           asked == [1, 2], str(asked))
        ok("device-config returns the controller's ports, not an empty set",
           r.status_code == 200
           and sorted(int(p) for p in (dev.get("ports") or {})) == list(range(1, 33)),
           f"status={r.status_code} ports={sorted(dev.get('ports') or [])[:12]}")
        ok("...and files them under both fitted boards",
           sorted(dev.get("boardPorts") or {}) == ["1", "2"],
           str(sorted(dev.get("boardPorts") or {})))
        unread = [i for i in ((dc.get("diff") or {}).get("items") or [])
                  if i.get("unread") and i.get("section") == "port"]
        ok("the diff reports no board as unread (the QA symptom)",
           not unread, str(unread[:2]))

        # A transport failure must surface as 502. Stubbed, so this asserts the
        # error path without depending on whether the real controller is up.
        def _refused(ip, board, **kw):
            raise hb.HinksPixError("connection refused")
        hb.read_board_ports = _refused
        try:
            r = c.get("/api/hinkspix/7/device-config")
        finally:
            hb.read_board_ports = real_ports
        ok("GET device-config reports a transport failure as 502",
           r.status_code == 502 and not r.get_json().get("ok"),
           f"status={r.status_code}")
        parent_server._children.remove(child)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
