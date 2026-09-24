#!/usr/bin/env python3
"""HinksPix model capabilities and smart receivers (#946).

Scope note: the *implementation* self-check, not the QA lane's pass. It covers
the two things #946 adds that can be wrong in ways a live push would not reveal
in time:

  * the per-model capability table — how many boards a controller addresses,
    which pixel protocols it takes, how many channels a port carries — because
    a wrong number here rejects a port the hardware has, or accepts one it
    doesn't, and the failure only shows up as a dark strip;
  * the smart-receiver calculation, which is a port of ``CalculateSmartReceivers``
    (``HinksPix.cpp:860-918``). Its output goes on the wire as an ``SCONFIG``
    payload and the controller has **no readback for it**, so nothing after the
    fact can tell a wrong start-pixel from a right one.

The reference client is the spec, so each case below is written to read like the
C++ it mirrors, and the pinned payloads are quoted from it rather than from our
own output.

Nothing here talks to a controller: these routes are pure, and the bridge entry
points are replaced with ones that raise, so a route that grew a network call
would fail loudly instead of reaching the operator's unit.

Run: SLYLED_DATA=$(mktemp -d) python3 tests/test_hinkspix_smart.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hxsmart-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import hinkspix_bridge as hb  # noqa: E402
import hinkspix_config as hc  # noqa: E402
import parent_server  # noqa: E402   (binds state, then imports orch_hinkspix)
from parent_server import app  # noqa: E402
from pixel_output import PixelOutputMap  # noqa: E402

CID = 7
_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if not isinstance(name, str) or isinstance(cond, str):
        raise TypeError(f"ok() called with swapped arguments: {name!r}, {cond!r}")
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def section(name):
    print(f"\n{name}")


# ── Fixtures ─────────────────────────────────────────────────────────────────


def port(n, leds=100, order="RGB", rec=None, rtype=None, **kw):
    """A stored port row. `rec` is the operator-facing letter, `rtype` ours."""
    p = {"port": n, "leds": leds, "enabled": True, "protocol": "ws2811",
         "colorOrder": order, "brightness": 100, "gamma": 1}
    if rec:
        p["smartRemote"] = rec
    if rtype:
        p["smartRemoteType"] = rtype
    p.update(kw)
    return p


# The operator's unit as of 2026-09-23 (MS_160, Type "P"): BD1 is the long-range
# differential board, BD2 a local SPI board, the rest not fitted. BD1 carries a
# receiver by default so the ordinary child is quiet — a Long Range board with
# no receiver raises `board_long_range` by design.
OPERATOR_BOARDS = {"BD1": "Long_Range", "BD2": "Local_SPI", "BD3": "Not_Present",
                   "BD4": "Not_Present", "BD5": "Not_Present"}


def make_child(ports=None, boards=None, cid=CID, **hinks):
    h = {"model": "HinksPix PRO", "controller": "H", "type": "P",
         "hardwareV3": False, "mcpu": 160, "maxU": 402, "uploadSupported": True,
         "boards": dict(boards if boards is not None else OPERATOR_BOARDS),
         "protocol": "e131", "baseUniverse": 1,
         "dmxOut": {"enabled": False, "universe": None},
         "ports": ports if ports is not None
                  else [port(1, leds=100, rec="A", rtype="hinkspix_4")]}
    h.update(hinks)
    return {"id": cid, "type": "hinkspix", "ip": "127.0.0.1", "name": "Roofline",
            "status": 1, "hinks": h}


class _NoDevice:
    """Replaces every bridge entry point, so a network call raises (#946).

    The routes covered here are pure by design — they read and write SlyLED's
    own record and never the controller. This turns that claim into a test
    rather than a comment, and makes it impossible for this suite to reach the
    operator's unit.
    """

    NAMES = ("probe", "command", "fire_and_forget", "op_mode_ethernet",
             "read_board_info", "read_board_ports", "read_data_mode",
             "read_e131_text", "read_info_row")

    def __enter__(self):
        self.saved = {}
        for n in self.NAMES:
            if hasattr(hb, n):
                self.saved[n] = getattr(hb, n)
                setattr(hb, n, self._raise(n))
        return self

    def __exit__(self, *exc):
        for n, fn in self.saved.items():
            setattr(hb, n, fn)
        return False

    @staticmethod
    def _raise(name):
        def _f(*a, **kw):
            raise AssertionError(f"the suite reached the device: hb.{name}()")
        return _f


# ── Capabilities ─────────────────────────────────────────────────────────────


def test_caps_table():
    section("the capability table — one row per model, from the xcontroller")
    keys = sorted(hc.CAPABILITIES)
    ok("three models, and they are the three xLights defines",
       keys == ["easylights", "pro_v12", "pro_v3"], str(keys))

    el = hc.CAPABILITIES["easylights"]
    ok("EasyLights Pix16: 1 board, 16 ports, 2040 ch/port, 65 universes",
       (el.boards, el.max_pixel_port, el.max_pixel_port_channels,
        el.max_input_universes) == (1, 16, 2040, 65),
       f"{el.boards}, {el.max_pixel_port}, {el.max_pixel_port_channels}, "
       f"{el.max_input_universes}")
    ok("...and it is the only model offered the longer pixel-protocol list",
       el.pixel_protocols == ("ws2811", "ws2801", "tls3001", "apa102")
       and all(c.pixel_protocols == ("ws2811",)
               for k, c in hc.CAPABILITIES.items() if k != "easylights"),
       str(el.pixel_protocols))
    ok("...with DDP, which it does serve", "ddp" in el.input_protocols)
    ok("...and no smart receivers, because it has no long-range board",
       el.smart_remote_types == ())

    v12 = hc.CAPABILITIES["pro_v12"]
    ok("PRO V1/V2: 3 boards, 48 ports, 2040 ch/port, 402 universes",
       (v12.boards, v12.max_pixel_port, v12.max_pixel_port_channels,
        v12.max_input_universes) == (3, 48, 2040, 402))
    ok("...and no DDP, which is why the picker must not offer it (#943 B14)",
       "ddp" not in v12.input_protocols, str(v12.input_protocols))
    ok("...and all three smart-receiver types",
       v12.smart_remote_types == (hb.SMART_REMOTE_4, hb.SMART_REMOTE_16,
                                  hb.SMART_REMOTE_16AC))

    v3 = hc.CAPABILITIES["pro_v3"]
    ok("PRO V3: 5 boards, 80 ports, 3072 ch/port, 684 universes",
       (v3.boards, v3.max_pixel_port, v3.max_pixel_port_channels,
        v3.max_input_universes) == (5, 80, 3072, 684))
    ok("...with DDP, which the V3 firmware does serve",
       "ddp" in v3.input_protocols)

    ok("boards is derived from the port count, never stored separately",
       all(c.boards == c.max_pixel_port // hb.PORTS_PER_BOARD
           for c in hc.CAPABILITIES.values()))
    ok("max_pixels is the channel cap at this node width",
       v12.max_pixels("RGB") == 680 and v12.max_pixels("RGBW") == 510,
       f"{v12.max_pixels('RGB')}, {v12.max_pixels('RGBW')}")
    ok("the JSON carries everything the editor needs",
       set(v12.to_json()) >= {"key", "name", "pixelProtocols", "inputProtocols",
                              "maxInputUniverses", "maxPixelPort",
                              "maxPixelPortChannels", "boards",
                              "smartRemoteTypes"})


def test_caps_key():
    section("which model a controller is — three witnesses, best first")
    ok("the operator's unit (Controller H, Type P, MaxU 402) is a PRO V1/V2",
       hc.caps_key(make_child()["hinks"]) == "pro_v12")
    ok("hardwareV3 is a PRO V3", hc.caps_key({"hardwareV3": True}) == "pro_v3")
    ok("Type 8 is a PRO 80 (#946, HinksPix.cpp:378-382)",
       hc.caps_key({"type": "8"}) == "pro_v3")
    ok("Controller E is an EasyLights, whatever the rest says",
       hc.caps_key({"controller": "E", "maxU": 402, "hardwareV3": True})
       == "easylights")
    ok("a model name starting easylights counts too",
       hc.caps_key({"model": "EasyLights Pix16"}) == "easylights")
    ok("with nothing probed beyond MaxU, 684 is a PRO V3",
       hc.caps_key({"maxU": 684}) == "pro_v3")
    ok("...65 is an EasyLights (the only model with a limit that low)",
       hc.caps_key({"maxU": 65}) == "easylights")
    ok("...and 402 is a PRO V1/V2", hc.caps_key({"maxU": 402}) == "pro_v12")
    ok("a child from before #946 is still read correctly, not assumed",
       hc.caps_key({"hardwareV3": False, "maxU": 684}) == "pro_v3")
    ok("and an empty record falls back to the common model rather than a crash",
       hc.caps_key({}) == "pro_v12" and hc.caps_key(None) == "pro_v12")

    section("the protocol list a model offers, with sACN kept beside E131")
    ok("PRO V1/V2: e131, sacn, artnet — and no ddp",
       hc.input_protocols_for({"hardwareV3": False})
       == ("e131", "sacn", "artnet"))
    ok("PRO V3: ddp offered, after the three",
       hc.input_protocols_for({"hardwareV3": True})
       == ("e131", "sacn", "artnet", "ddp"))
    ok("EasyLights: ddp offered too, and sACN still beside E131",
       hc.input_protocols_for({"controller": "E"})
       == ("e131", "sacn", "artnet", "ddp"))
    ok("the generation-only wrapper still answers as it always did",
       hc.input_protocols_supported(True) == ("e131", "sacn", "artnet", "ddp"))


def test_board_decomposition():
    section("port -> board / bank / sub-port (HinksPix.cpp:860-867)")
    ok("port 1 is board 1, bank 0, sub-port 0",
       hc.board_of(1) == 1 and hc.board_bank(1) == (1, 0, 0))
    ok("port 4 is the last of bank 0", hc.board_bank(4) == (1, 0, 3))
    ok("port 5 opens bank 1", hc.board_bank(5) == (1, 1, 0))
    ok("port 16 is the last output on board 1",
       hc.board_bank(16) == (1, 3, 3) and hc.board_of(16) == 1)
    ok("port 17 is the first on board 2", hc.board_bank(17) == (2, 0, 0))
    ok("port 80 is the last output a PRO V3 addresses",
       hc.board_bank(80) == (5, 3, 3))
    ok("the model's own ceiling agrees with the arithmetic",
       hc.CAPABILITIES["pro_v3"].max_pixel_port == 80
       and hc.CAPABILITIES["pro_v12"].max_pixel_port == 48)
    ok("A..P map onto 0..15 both ways",
       hc.smart_id_label(0) == "A" and hc.smart_id_label(15) == "P"
       and hc.smart_id_from_label("A") == 0
       and hc.smart_id_from_label("p") == 15
       and hc.smart_id_from_label("3") == 3
       and hc.smart_id_from_label("Q") == -1)


# ── Smart receivers ──────────────────────────────────────────────────────────


def recs(ports, strings_by_port=None, boards=None):
    """The bank lists as wire strings, the way they go into SCONFIG."""
    out = hc.calculate_smart_receivers(
        ports, boards=OPERATOR_BOARDS if boards is None else boards,
        strings_by_port=strings_by_port)
    return {b: {k: [r.to_v() for r in v] for k, v in sorted(banks.items())}
            for b, banks in sorted(out.items())}


def test_receivers():
    section("smart receivers — one per output on a bank")

    # Four outputs of bank 0, each with its own receiver. Each receiver is told
    # the pixel at which *its* output's run begins, in that output's own slot of
    # the four-value list — the slot is the sub-port (`:791-794`).
    got = recs([port(1, rec="A"), port(2, rec="B"),
                port(3, rec="C"), port(4, rec="D")])
    ok("four outputs, four receivers, each in its own sub-port slot",
       got == {1: {0: ["0,0,1,0,0,0", "1,0,0,1,0,0",
                       "2,0,0,0,1,0", "3,0,0,0,0,1"]}}, str(got))

    # A chain on one output: two fixtures, one receiver each. The second
    # receiver's run starts at pixel 51 — 50 pixels into the same output's
    # stream, on the *same* sub-port (`:816-819`).
    chain = {1: [{"leds": 50, "smartRemote": "A"},
                 {"leds": 50, "smartRemote": "B"}]}
    got = recs([port(1, leds=100, rec="A")], strings_by_port=chain)
    ok("a chain on one output puts the second receiver 50 px in",
       got == {1: {0: ["0,0,1,0,0,0", "1,0,51,0,0,0"]}}, str(got))

    # A 16-port receiver is addressed as a group of four ids on a multiple of
    # four; only the id in use is placed, the other three are announced so the
    # controller knows the group is there (`:885-899`).
    got = recs([port(1, rec="A", rtype="hinkspix_16")])
    ok("a 16-port receiver is announced as its whole group of four",
       got == {1: {0: ["0,1,1,0,0,0", "1,0,0,0,0,0",
                       "2,0,0,0,0,0", "3,0,0,0,0,0"]}}, str(got))
    got = recs([port(5, rec="E", rtype="hinkspix_16")])
    ok("...and the group starts on a multiple of four, not on the id",
       got == {1: {1: ["4,1,1,0,0,0", "5,0,0,0,0,0",
                       "6,0,0,0,0,0", "7,0,0,0,0,0"]}}, str(got))

    # A 16AC counts its start pixel in its own three-channel terms rather than
    # in the port's (`:901-905`): after 40 RGBW nodes the port has seen 160
    # channels, so the AC's run begins at 160/3+1 = 54, not at 41.
    ac = {2: [{"leds": 40, "smartRemote": "A", "smartRemoteType": "hinkspix_4"},
              {"leds": 60, "smartRemote": "B",
               "smartRemoteType": "hinkspix_16ac"}]}
    got = recs([port(2, leds=100, order="RGBW")], strings_by_port=ac)
    ok("a 16AC counts in three-channel terms, not the port's node width",
       got == {1: {0: ["0,0,0,1,0,0", "1,2,0,54,0,0"]}}, str(got))

    got = recs([port(1, rec="A")], boards={"BD1": "Long_Range"})
    ok("a receiver on a long-range board is collected",
       got == {1: {0: ["0,0,1,0,0,0"]}}, str(got))
    got = recs([port(17, rec="A")], boards={"BD1": "Long_Range"})
    ok("...and one on a board the map does not mark long-range is not",
       got == {}, str(got))
    got = recs([port(17, rec="A")])
    ok("...which is how a Local_SPI board is skipped, exactly as xLights does",
       got == {}, str(got))
    got = recs([port(1, rec="A"), port(2, rec="B", enabled=False),
                port(3, rec="C", leds=0)])
    ok("disabled and zero-pixel rows contribute nothing",
       got == {1: {0: ["0,0,1,0,0,0"]}}, str(got))
    ok("a port with no receiver set contributes nothing",
       recs([port(1, leds=100)]) == {})
    ok("nothing in, nothing out", recs([]) == {})

    ok("the receiver's JSON spells its id the way the dial does",
       hc.SmartReceiver(0, 1).to_json()["label"] == "A"
       and hc.SmartReceiver(0, 1).to_json()["typeName"] == "16-port"
       and hc.SmartReceiver(6, 2).to_json()["typeName"] == "16AC"
       and hc.SmartReceiver(0, 0).to_json()["typeName"] == "4-port")


def _data_of(cmd):
    return (cmd.to_json().get("headers") or {}).get("DATA") or ""


def _commands(child):
    hinks = child["hinks"]
    intended = hc.intended_config(child, PixelOutputMap.build(child),
                                  max_universes=hinks.get("maxU"))
    return hc.build_commands(intended, mcpu=hinks.get("mcpu"),
                             hardware_v3=bool(hinks.get("hardwareV3")))


def test_sconfig_commands():
    section("SCONFIG — one request per bank, board-major, before PCONFIG")
    child = make_child(ports=[port(1, rec="A"), port(2, rec="B")])
    cmds = _commands(child)
    payloads = [d for d in (_data_of(c) for c in cmds) if '"SCONFIG"' in d]

    ok("one SCONFIG for the one bank in use", len(payloads) == 1, str(payloads))
    ok("its payload is the reference client's shape, 0-based board and bank",
       payloads and payloads[0] == (
           '{"CMD":"SCONFIG","BOARD":"0","Port4":"0","LIST":'
           '[{"V":"0,0,1,0,0,0"},{"V":"1,0,0,1,0,0"}]}'),
       str(payloads[:1]))
    ok("a bank with no receiver gets no request at all, not an empty LIST",
       len(payloads) == 1 and "LIST\":[]" not in payloads[0])

    data = [_data_of(c) for c in cmds]
    bd = next(i for i, d in enumerate(data) if '"BD_INFO"' in d)
    sc = next(i for i, d in enumerate(data) if '"SCONFIG"' in d)
    pc = next(i for i, d in enumerate(data) if '"PCONFIG"' in d)
    unpack = next(i for i, c in enumerate(cmds) if "remap" in c.note)
    reboot = next(i for i, c in enumerate(cmds) if c.kind == "reboot")
    ok("SCONFIG is sent after the universe table and before the ports",
       bd < sc < pc, f"bd_info {bd}, sconfig {sc}, pconfig {pc}")
    ok("...and the remap reset and the reboot still bracket the end",
       pc < unpack < reboot, f"pconfig {pc}, unpack {unpack}, reboot {reboot}")

    section("...and it is addressed per board, on the model's own board count")
    two = make_child(
        ports=[port(1, rec="A"), port(17, rec="C")],
        boards={"BD1": "Long_Range", "BD2": "Long_Range", "BD3": "Not_Present",
                "BD4": "Not_Present", "BD5": "Not_Present"})
    sc = [d for d in (_data_of(c) for c in _commands(two)) if '"SCONFIG"' in d]
    ok("two boards in use get two SCONFIG requests, board-major",
       len(sc) == 2 and '"BOARD":"0"' in sc[0] and '"BOARD":"1"' in sc[1],
       str(sc))
    ok("...each addressed to bank 0 of its own board",
       all('"Port4":"0"' in d for d in sc), str(sc))

    # The PRO 80 path: five boards, 80 ports. The board count comes from the
    # caps table, so this is also the check that #946 did not stop at 48.
    section("a PRO V3 with all five boards fitted addresses 80 outputs")
    v3 = make_child(hardwareV3=True, maxU=684, mcpu=140, ports=[port(80, rec="P")],
                    boards={"BD1": "Long_Range", "BD2": "Long_Range",
                            "BD3": "Long_Range", "BD4": "Long_Range",
                            "BD5": "Long_Range"})
    cmds = _commands(v3)
    data = [_data_of(c) for c in cmds]
    sc = [d for d in data if '"SCONFIG"' in d]
    ok("the last output of the last board is addressed as board 4, bank 3",
       len(sc) == 1 and '"BOARD":"4"' in sc[0] and '"Port4":"3"' in sc[0],
       str(sc))
    ok("...with its receiver in the fourth sub-port slot",
       '"V":"15,0,0,0,0,1"' in sc[0], str(sc))
    pc_boards = sorted({d.split('"BOARD":"')[1].split('"')[0]
                        for d in data if '"PCONFIG"' in d})
    ok("...and five PCONFIG requests, one per fitted board",
       pc_boards == ["0", "1", "2", "3", "4"], str(pc_boards))


# ── Findings ─────────────────────────────────────────────────────────────────


def codes(child, **kw):
    return [f.code for f in hc.validate(child, **kw)]


def levels(child, **kw):
    return [(f.code, f.level) for f in hc.validate(child, **kw)]


def test_findings():
    section("findings — the ones #946 adds, and what level each is")
    ok("a probed, configured child raises nothing at all",
       levels(make_child()) == [], str(levels(make_child())))
    ok("an unprobed controller is an error, because nothing can be checked",
       "not_probed" in codes(make_child(maxU=0, boards={})),
       str(codes(make_child(maxU=0, boards={}))))

    c = make_child(ports=[port(1)])
    ok("a used port on a Long Range board with no receiver warns once",
       levels(c) == [("board_long_range", "warn")], str(levels(c)))
    ok("...and the warning stops the moment the board has one",
       levels(make_child(ports=[port(1, rec="A")])) == [],
       str(levels(make_child(ports=[port(1, rec="A")]))))

    absent = {"BD1": "Long_Range", "BD2": "Not_Present", "BD3": "Not_Present",
              "BD4": "Not_Present", "BD5": "Not_Present"}
    c = make_child(ports=[port(17, rec="A")], boards=absent)
    ok("a receiver on a board that is not fitted is an error",
       "smart_on_non_long_range" in codes(c), str(codes(c)))
    c = make_child(ports=[port(17, rec="A")],
                   boards={"BD1": "Long_Range", "BD2": "Local_SPI",
                           "BD3": "Not_Present", "BD4": "Not_Present",
                           "BD5": "Not_Present"})
    ok("...and on a Local SPI board too, which is the case xLights refuses",
       "smart_on_non_long_range" in codes(c), str(codes(c)))
    ok("...but a receiver on a Long Range board alone is not an error",
       "smart_on_non_long_range" not in codes(make_child(ports=[port(1, rec="A")])))

    c = make_child(ports=[port(40)])
    ok("a used port on a board that is not fitted is an error",
       "port_on_absent_board" in codes(c), str(codes(c)))
    v3 = make_child(ports=[port(40)], hardwareV3=True, maxU=684, mcpu=140,
                    boards={"BD1": "Long_Range", "BD2": "Long_Range",
                            "BD3": "Not_Present", "BD4": "Not_Present",
                            "BD5": "Not_Present"})
    ok("...as is one on a Not_Present board of a PRO V3",
       "port_on_absent_board" in codes(v3), str(codes(v3)))
    v3_ok = make_child(ports=[port(40)], hardwareV3=True, maxU=684, mcpu=140,
                       boards={"BD1": "Long_Range", "BD2": "Long_Range",
                               "BD3": "Local_SPI", "BD4": "Not_Present",
                               "BD5": "Not_Present"})
    ok("...and not one on a fitted board, which is the point of #946",
       "port_on_absent_board" not in codes(v3_ok), str(codes(v3_ok)))
    c = make_child(ports=[port(60)])
    ok("a port past 48 on a PRO V1/V2 is an error: that board does not exist",
       "port_on_absent_board" in codes(c), str(codes(c)))

    c = make_child(ports=[port(1, leds=700)])      # 700 RGB nodes = 2100 ch
    ok("more channels than a port carries is an error",
       "port_channels_exceed" in codes(c), str(codes(c)))
    v3 = make_child(ports=[port(1, leds=1024)], hardwareV3=True, maxU=684,
                    mcpu=140,
                    boards={"BD1": "Long_Range", "BD2": "Not_Present",
                            "BD3": "Not_Present", "BD4": "Not_Present",
                            "BD5": "Not_Present"})
    ok("...but 1024 nodes fit a PRO V3's 3072-channel port",
       "port_channels_exceed" not in codes(v3), str(codes(v3)))

    c = make_child(ports=[port(1, protocol="apa102")])
    ok("a pixel protocol the model does not offer is an error",
       "protocol_unsupported" in codes(c), str(codes(c)))
    c = make_child(ports=[port(1, protocol="apa102")], controller="E",
                   model="EasyLights Pix16", maxU=65,
                   boards={"BD1": "Local_SPI"}, mcpu=160)
    ok("...and apa102 is fine on the one model that offers it",
       "protocol_unsupported" not in codes(c), str(codes(c)))

    c = make_child(protocol="ddp")
    ok("an input protocol the model cannot serve is an error",
       "input_protocol_unsupported" in codes(c), str(codes(c)))
    ok("...and DDP is accepted on the model that does serve it",
       "input_protocol_unsupported" not in
       codes(make_child(protocol="ddp", hardwareV3=True, maxU=684, mcpu=140,
                        boards={"BD1": "Long_Range", "BD2": "Not_Present",
                                "BD3": "Not_Present", "BD4": "Not_Present",
                                "BD5": "Not_Present"})))

    section("...and firmware, where the only floor is the upload gate")
    c = make_child(ports=[port(1)], mcpu=149)
    ok("firmware below the upload floor is an error, not a warning",
       ("upload_unsupported", "error") in levels(c), str(levels(c)))
    ok("...and there is no separate smart-receiver firmware warning (#946 "
       "listed one; every firmware that could raise it is blocked above)",
       "firmware_below_101" not in codes(c)
       and not hasattr(hb, "MIN_MCPU_SMART"))

    c = make_child(ports=[port(1, enabled=False, leds=0)])
    # #946 called this a warning, so it had to be ticked past on every push.
    # The bench run settled it: with no port in use the sequence ends in
    # {"CMD":"BD_INFO","NumU":"0"}, the controller answers ERROR, and by then it
    # has already had its universe table wiped. There is no configuration to
    # accept here, only one to refuse.
    ok("a config that enables nothing is an error, not an acknowledgement",
       levels(c) == [("empty_config", "error")], str(levels(c)))
    ok("...and the finding says why it is an error rather than a nudge",
       "refuses" in " ".join(f.text for f in hc.validate(c)
                             if f.code == "empty_config"),
       str([f.text for f in hc.validate(c) if f.code == "empty_config"]))
    ok("...and it is raised the moment one port comes back",
       "empty_config" not in codes(make_child(ports=[port(1, enabled=False,
                                                          leds=0),
                                                     port(2)])))

    section("...and the ones that need the fixtures bound to the controller")
    fix = [{"id": 1, "name": "Eaves", "childId": CID,
            "strings": [{"port": 2, "leds": 100}]}]
    c = make_child(ports=[port(1, rec="A"), port(2, leds=100)])
    found = hc.validate(c, fixtures=fix)
    ok("an enabled port with no fixture on it is a warning, not an error",
       ("port_unbound", "warn") in [(f.code, f.level) for f in found]
       and any(f.port == 1 for f in found if f.code == "port_unbound"),
       str([(f.code, f.port, f.level) for f in found]))
    ok("...and it is not raised at all when no fixtures were supplied",
       "port_unbound" not in codes(c), str(codes(c)))
    fix_bad = [{"id": 1, "name": "Eaves", "childId": CID,
                "strings": [{"port": 2, "leds": 300}]}]
    ok("a fixture whose pixel count disagrees with the port is an error",
       any(f.code == "fixture_leds_mismatch"
           for f in hc.validate(c, fixtures=fix_bad)),
       str([(f.code, f.port) for f in hc.validate(c, fixtures=fix_bad)]))
    ok("...and agrees when the numbers match",
       "fixture_leds_mismatch" not in codes(c, fixtures=fix),
       str(codes(c, fixtures=fix)))

    section("...and the engine protocol, which only the orchestrator knows")
    c = make_child(protocol="artnet")
    ok("a controller on ARTNET with the engine on e131 warns, but does not block",
       ("engine_protocol_mismatch", "warn") in levels(c, engine_protocol="e131"),
       str(levels(c, engine_protocol="e131")))
    c = make_child(protocol="sacn")
    ok("...and sacn against an engine on sacn is not a mismatch (it *is* E131)",
       "engine_protocol_mismatch" not in codes(c, engine_protocol="sacn"),
       str(codes(c, engine_protocol="sacn")))

    section("...and what applying costs, said before it happens")
    c = make_child()
    intended = hc.intended_config(c, PixelOutputMap.build(c), max_universes=402)
    # The reboot is a fact about applying, not a decision the operator makes.
    # #946 made it a warning, so every push carried a box to tick — which is
    # how an acknowledgement gate turns into a click-through. It is a note.
    ok("with a plan in hand, the reboot is stated as a note",
       ("reboot_required", "info") in levels(c, intended=intended),
       str(levels(c, intended=intended)))
    ok("...and it is not asked for before there is a plan to apply",
       "reboot_required" not in codes(c), str(codes(c)))
    ok("...and nothing in that list blocks the push",
       not [f for f in hc.validate(c, intended=intended) if f.level == "error"],
       str(levels(c, intended=intended)))


# ── Routes ───────────────────────────────────────────────────────────────────


def test_routes():
    child = make_child()
    parent_server._children.append(child)
    with _NoDevice(), app.test_client() as c:
        section("GET — the model's capabilities, and the routes stay in-process")

        b = c.get(f"/api/hinkspix/{CID}").get_json()
        ok("GET reports the model's capabilities",
           (b.get("caps") or {}).get("key") == "pro_v12"
           and (b.get("caps") or {}).get("maxPixelPort") == 48,
           str(b.get("caps")))
        ok("...and its protocols are the model's own",
           b.get("protocols") == ["e131", "sacn", "artnet"], str(b.get("protocols")))

        section("PUT — the port range is the model's, not the protocol's")
        r = c.put(f"/api/hinkspix/{CID}", json={"ports": [{"port": 60, "leds": 100}]})
        ok("port 60 is refused on a 48-port model",
           r.status_code == 400 and "48" in (r.get_json().get("err") or ""),
           str(r.get_json())[:160])
        r = c.put(f"/api/hinkspix/{CID}", json={"ports": [{"port": 81, "leds": 100}]})
        ok("...and so is 81, past every model the protocol describes",
           r.status_code == 400, str(r.get_json())[:160])
        r = c.put(f"/api/hinkspix/{CID}", json={"protocol": "ddp"})
        ok("DDP is refused on a PRO V1/V2, and the error says why",
           r.status_code == 400 and "hardware V3" in (r.get_json().get("err") or ""),
           str(r.get_json())[:160])

        v3 = make_child(cid=CID + 1, hardwareV3=True, maxU=684, mcpu=140, ports=[],
                        baseUniverse=100,
                        boards={"BD1": "Long_Range", "BD2": "Long_Range",
                                "BD3": "Long_Range", "BD4": "Long_Range",
                                "BD5": "Long_Range"})
        parent_server._children.append(v3)
        r = c.put(f"/api/hinkspix/{CID + 1}",
                  json={"protocol": "ddp",
                        "ports": [{"port": 79, "leds": 100},
                                  {"port": 80, "leds": 100}]})
        ok("a PRO V3 accepts ports 79 and 80 and DDP",
           r.status_code == 200, str(r.get_json())[:200])
        ok("...and its findings say nothing blocks that",
           not [f for f in (r.get_json().get("findings") or [])
                if f["level"] == "error"], str(r.get_json().get("findings")))
        parent_server._children.remove(v3)

        section("PUT — start nulls, both spellings, and the receivers")
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "startNulls": 7}]})
        ok("startNulls is stored under its real name",
           r.get_json()["hinks"]["ports"][0]["startNulls"] == 7,
           str(r.get_json()["hinks"]["ports"][:1]))
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "nullPixels": 5}]})
        ok("the pre-#946 spelling is still accepted, so an old editor keeps working",
           r.get_json()["hinks"]["ports"][0]["startNulls"] == 5,
           str(r.get_json()["hinks"]["ports"][:1]))
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "startNulls": -1}]})
        ok("a negative skip start is refused", r.status_code == 400,
           str(r.get_json())[:120])

        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": "A",
                                   "smartRemoteType": "hinkspix_16"}]})
        p = r.get_json()["hinks"]["ports"][0]
        ok("a receiver is stored as the letter the operator dialled",
           p["smartRemote"] == "A" and p["smartRemoteType"] == "hinkspix_16", str(p))
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": 3,
                                   "smartRemoteType": "hinkspix_4"}]})
        ok("...and a number means the same as its letter",
           r.get_json()["hinks"]["ports"][0]["smartRemote"] == "D",
           str(r.get_json()["hinks"]["ports"][:1]))
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": "Q"}]})
        ok("a receiver id that is not on the dial is refused",
           r.status_code == 400, str(r.get_json())[:120])
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": "A",
                                   "smartRemoteType": "hinkspix_64"}]})
        ok("a receiver type that does not exist is refused",
           r.status_code == 400, str(r.get_json())[:120])
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": "A"}]})
        ok("...and a receiver with no type is refused rather than guessed at",
           r.status_code == 400, str(r.get_json())[:120])

        el = make_child(cid=CID + 2, controller="E", model="EasyLights Pix16",
                        maxU=65, mcpu=160, boards={"BD1": "Local_SPI"}, ports=[])
        parent_server._children.append(el)
        r = c.put(f"/api/hinkspix/{CID + 2}",
                  json={"ports": [{"port": 1, "leds": 100, "smartRemote": "A",
                                   "smartRemoteType": "hinkspix_4"}]})
        ok("a model with no smart receivers refuses one outright",
           r.status_code == 400
           and "no smart receivers" in (r.get_json().get("err") or ""),
           str(r.get_json())[:160])
        r = c.put(f"/api/hinkspix/{CID + 2}",
                  json={"ports": [{"port": 17, "leds": 100}]})
        ok("...and a port past its 16 is refused on that model too",
           r.status_code == 400, str(r.get_json())[:160])
        parent_server._children.remove(el)

        section("PUT — defaults, stored at the steps the controller uses")
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"defaults": {"brightness": 90, "gamma": 3}})
        ok("defaults are stored encoded",
           r.get_json()["hinks"]["defaults"] == {"brightness": 90, "gamma": 3},
           str(r.get_json()["hinks"].get("defaults")))
        r = c.put(f"/api/hinkspix/{CID}", json={"defaults": {"brightness": 45}})
        ok("...and a partial update leaves the other alone, rounded to the step",
           r.get_json()["hinks"]["defaults"] == {"brightness": 40, "gamma": 3},
           str(r.get_json()["hinks"].get("defaults")))

        section("PUT — findings come back with the save, so the editor can say")
        # A controller of its own, so the port rows the tests below reconstruct
        # are not disturbed, and a base universe that collides with nobody.
        c3 = make_child(cid=CID + 3, baseUniverse=500, ports=[])
        parent_server._children.append(c3)
        r = c.put(f"/api/hinkspix/{CID + 3}",
                  json={"ports": [{"port": 40, "leds": 100}]})
        ok("a port on a Not_Present board is saved but reported as an error",
           r.status_code == 200
           and any(f["code"] == "port_on_absent_board" and f["level"] == "error"
                   for f in r.get_json().get("findings", [])),
           str(r.get_json().get("findings")))
        parent_server._children.remove(c3)

        # The port table the defaults test below works from: two ports in use,
        # neither of them bound to a fixture yet.
        r = c.put(f"/api/hinkspix/{CID}",
                  json={"ports": [{"port": 1, "leds": 100},
                                  {"port": 17, "leds": 100}]})
        ok("...and a save that only warns still comes back with its findings",
           r.status_code == 200 and r.get_json().get("findings")
           and not [f for f in r.get_json()["findings"] if f["level"] == "error"],
           str(r.get_json().get("findings"))[:200])

        section("POST defaults-from-fixtures — a proposal, and only a proposal")
        parent_server._fixtures[:] = [
            {"id": 1, "name": "Garage eaves", "childId": CID,
             "strings": [{"port": 1, "leds": 120, "mm": 2000,
                          "ledType": "ws2812b"}]},
            {"id": 2, "name": "Roofline", "childId": CID,
             "strings": [{"port": 2, "leds": 200, "mm": 3300, "ledType": "ws2811"},
                         {"port": 2, "leds": 60, "mm": 1000, "ledType": "ws2811"}]},
            {"id": 3, "name": "Elsewhere", "childId": 99,
             "strings": [{"port": 3, "leds": 999, "mm": 1}]},
        ]
        before = c.get(f"/api/hinkspix/{CID}").get_json()["hinks"]["ports"]
        r = c.post(f"/api/hinkspix/{CID}/defaults-from-fixtures")
        d = r.get_json()
        byp = {p["port"]: p for p in d.get("ports", [])}
        ok("it proposes one row per bound port, and only this child's",
           sorted(byp) == [1, 2], str(sorted(byp)))
        ok("...pixels are the sum of the strings on the port, not the first one",
           byp[2]["leds"] == 260, str(byp.get(2)))
        ok("...and so is the length, in stage mm",
           byp[2]["mm"] == 4300 and byp[1]["mm"] == 2000, str(byp.get(2)))
        ok("the colour order comes from the string type that declares one",
           byp[1]["colorOrder"] == "GRB" and byp[2]["colorOrder"] == "RGB",
           f"{byp[1]['colorOrder']}, {byp[2]['colorOrder']}")
        ok("the receiver is left as the port had it — it is wiring, not layout",
           byp[1]["smartRemote"] is None and byp[2]["smartRemote"] is None)
        ok("it says what would change, in the operator's own units",
           any("port 1" in ch and "120" in ch for ch in d.get("changes", [])),
           str(d.get("changes")))
        ok("...and which enabled ports drive nothing",
           d.get("unbound") == [17], str(d.get("unbound")))
        after = c.get(f"/api/hinkspix/{CID}").get_json()["hinks"]["ports"]
        ok("nothing was stored: the port table is exactly as it was",
           after == before, f"{before} -> {after}")
        ok("...and it carries the capabilities the editor needs to place the rows",
           (d.get("caps") or {}).get("maxPixelPort") == 48, str(d.get("caps")))
        parent_server._fixtures[:] = []

    parent_server._children.remove(child)


def test_fixture_port_validation():
    section("binding a fixture string to a port — board-aware, from #946")
    child = make_child()
    parent_server._children.append(child)
    parent_server._fixtures[:] = []
    with _NoDevice(), app.test_client() as c:

        def create(port_num, leds=100):
            return c.post("/api/fixtures", json={
                "name": "Eaves", "fixtureType": "led", "type": "linear",
                "childId": CID, "strings": [{"port": port_num, "leds": leds}]})

        r = create(81)
        ok("port 81 is refused for shape alone, before any controller is asked",
           r.status_code == 400 and "out of range 1..80" in
           (r.get_json().get("err") or ""), str(r.get_json())[:200])
        r = create(60)
        ok("a port past the model's own 48 is refused",
           r.status_code == 400 and "not an output" in
           (r.get_json().get("err") or ""), str(r.get_json())[:200])
        r = create(33)
        ok("a port on a board that reports Not_Present is refused",
           r.status_code == 400 and "BD3" in (r.get_json().get("err") or ""),
           str(r.get_json())[:200])
        r = create(17)
        ok("a port on a fitted board the port table does not carry is refused",
           r.status_code == 400 and "not configured" in
           (r.get_json().get("err") or ""), str(r.get_json())[:200])
        r = create(1, leds=90)
        ok("...and a pixel count that disagrees with the controller is refused",
           r.status_code == 400 and "disagrees" in
           (r.get_json().get("err") or ""), str(r.get_json())[:200])
        r = create(1, leds=100)
        ok("a configured, fitted port with matching pixels is accepted",
           r.status_code == 200 and r.get_json().get("id") is not None,
           str(r.get_json())[:200])

        parent_server._fixtures[:] = []
    parent_server._children.remove(child)


def fresh_store():
    """Start from an empty orchestrator store, whatever the data dir holds.

    `parent_server` loads `children` and `fixtures` at import, so a second run
    in the same SLYLED_DATA opens on the previous run's controllers and fixture
    bindings — and a port that already carries the fixture this suite is about
    to bind is simply already bound, so the refusal it asserts never happens.
    The documented invocation is `SLYLED_DATA=$(mktemp -d)`; clearing the store
    is what keeps a reused directory from turning that into a red suite.
    """
    parent_server._children[:] = []
    parent_server._fixtures[:] = []


def main():
    fresh_store()
    test_caps_table()
    test_caps_key()
    test_board_decomposition()
    test_receivers()
    test_sconfig_commands()
    test_findings()
    test_routes()
    test_fixture_port_validation()
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
