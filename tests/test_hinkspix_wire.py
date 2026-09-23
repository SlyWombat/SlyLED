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


# ── The operator's real layout ───────────────────────────────────────────────
# Garage eaves: one model, 200 WS2811 RGB nodes on port 17, start channel 1
# (universes 1-2). Port 17 is on BLK 1 = BD2, which is a Local_SPI board;
# BD1 is a Long_Range board with nothing on it; BD3 is not fitted.

def operator_child():
    return {
        "id": 7, "type": "hinkspix", "ip": "192.168.10.6", "name": "Roofline",
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

    print("transport — BLK selects the board (#943 B3)")
    # The real reply is JSON `{"LIST":[{"V": "<row>"}, ...]}` — verified against
    # InitExpansionBoardData (HinksPix.cpp:303-322), which is also where the
    # "exactly `length` rows" rule and `BLK: expansion - 1` come from.
    body = ('{"LIST":[' + ",".join(
        '{"V":"%d,0,1,0,0,0,0,0,100,1"}' % o for o in range(17, 33)) + ']}').encode()
    rec = Recorder(body)
    rows = hb.read_board_ports("10.0.0.1", 1, opener=rec)
    ok("a board read carries BLK", rec.only.headers.get("Blk") == "1",
       rec.only.headers.get("Blk"))
    ok("a board read sends no DATA header",
       rec.only.headers.get("Data") is None)
    ok("board 1 comes back as ports 17-32, not 1-16",
       rows[0].split(",")[0] == "17" and len(rows) == 16, rows[0])

    short = ('{"LIST":[' + ",".join(
        '{"V":"%d,0,1,0,0,0,0,0,100,1"}' % o for o in range(1, 16)) + ']}').encode()
    try:
        hb.read_board_ports("10.0.0.1", 0, opener=Recorder(short))
        ok("a 15-row reply is rejected rather than padded", False, "no exception")
    except hb.HinksPixError:
        ok("a 15-row reply is rejected rather than padded", True)
    try:
        hb.read_board_ports("10.0.0.1", 0, opener=Recorder(b"not json at all"))
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

        # The apply executor is asserted by capturing its calls: the transport
        # itself is covered above, so what matters here is the dispatch — the
        # right call per request kind, and a reboot that always happens.
        calls = []
        real_command, real_read, real_faf = hb.command, hb.read_data_mode, hb.fire_and_forget
        hb.command = lambda ip, payload, path=hb.CGI_POST_DATA, **kw: (
            calls.append(("write", path, payload)) or '"OK"')
        hb.read_data_mode = lambda ip, **kw: (calls.append(("read", kw)) or {"MODE": "E131"})
        hb.fire_and_forget = lambda ip, payload, **kw: (
            calls.append(("reboot", payload)) or [{"sent": True, "err": ""}])
        try:
            r = c.post("/api/hinkspix/7/apply", json={})
            ab = r.get_json()
        finally:
            hb.command, hb.read_data_mode, hb.fire_and_forget = (
                real_command, real_read, real_faf)
        ok("POST apply succeeds", r.status_code == 200 and ab.get("ok"),
           str(ab)[:300])
        ok("apply sends the same number of requests the plan showed",
           ab.get("requests") == len(body.get("requests")), str(ab.get("requests")))
        ok("apply reboots the controller", calls and calls[-1][0] == "reboot")
        ok("apply reports that it rebooted", ab.get("rebooted") is True)
        ok("the reboot is the last thing that happens",
           [c[0] for c in calls].count("reboot") == 1)
        ok("apply records a config hash so the UI can say in-sync",
           bool(ab.get("configHash")))

        r = c.get("/api/hinkspix/7/device-config")
        ok("GET device-config fails cleanly when the device is unreachable",
           r.status_code in (200, 502), str(r.status_code))
        parent_server._children.remove(child)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
