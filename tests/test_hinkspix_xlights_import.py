#!/usr/bin/env python3
"""Reading a HinksPix layout out of an xLights show folder (#947).

Two halves, and the order matters. The first asserts against the operator's
**real** show folder — the two files that are the acceptance fixture, copied
verbatim into ``tests/fixtures/xlights_home_eves/``:
``xlights_networks.xml`` (886 B) and ``xlights_rgbeffects.xml`` (6952 B) from
``SlyMega Art Inc/Projects/Xlights - Home Eves``. Those assertions are what
"the importer understands this operator's layout" means, and they are pinned to
exact values (universe 1/channel 1, port 17, 200 nodes) so a change in the
reading cannot pass by drifting into a different plausible answer.

The second half is synthetic: one XML string per construct, for the shapes the
operator's folder does not contain — a chain, a cycle, a multi-output model, a
model type with no node rule, a Custom model, a second controller. Those are the
paths where the importer has to be *right about not knowing*, and they are tested
by their exact warning or error rather than by "something happened".

Nothing here opens a socket, reads a path, or touches a controller: the module
is pure, and this suite passes the XML text in directly.

Run: python tests/test_hinkspix_xlights_import.py
"""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hinkspix_xlights_import as xi  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "xlights_home_eves")

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    """Assert one thing, named in the output.

    The argument order is checked rather than trusted: written the other way
    round every call passes, because the name is a non-empty string and
    therefore truthy. A suite that reports green while asserting nothing is
    worse than one that fails.
    """
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


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


# ── Synthetic XML builders ───────────────────────────────────────────────────
# Written as XML text rather than as dicts on purpose: the module's job is to
# read the file format, and a test that hands it a dict has stopped testing the
# half of it where the bugs live (attribute spelling, tag case, nesting).

NETWORKS = """<?xml version="1.0" encoding="UTF-8"?>
<Networks computer="TEST">
  <Controller Id="1" Name="{name}" Description="{desc}" Type="Ethernet"
              Vendor="{vendor}" Model="{model}" IP="{ip}" Protocol="{protocol}"
              FullxLightsControl="{full}" DefaultBrightnessUnderFullControl="{bright}"
              DefaultGammaUnderFullControl="{gamma}">
{networks}
  </Controller>
</Networks>
"""


def network_rows(universes, channels=510, ip="192.168.2.10", kind="E131"):
    return "\n".join(
        f'    <network ComPort="{ip}" BaudRate="{u}" NetworkType="{kind}"'
        f' MaxChannels="{channels}"/>' for u in universes)


def networks(name="Ethernet_", ip="192.168.2.10", protocol="E131",
             universes=(1, 2), channels=510, vendor="HinksPix",
             model="PRO V1/V2", full="TRUE", bright=100, gamma=1, desc="Garage"):
    return NETWORKS.format(
        name=name, desc=desc, vendor=vendor, model=model, ip=ip,
        protocol=protocol, full=full, bright=bright, gamma=gamma,
        networks=network_rows(universes, channels=channels, ip=ip))


MODEL = """    <model name="{name}" DisplayAs="{display}" StringType="{stringType}"
           parm1="{p1}" parm2="{p2}" parm3="{p3}" Controller="{controller}"
           {custom}StartChannel="{start}" WorldPosX="0.0" WorldPosY="0.0"
           WorldPosZ="0.0" X2="1000.0" Y2="0.0" Z2="0.0">
      <ControllerConnection Protocol="{proto}" Port="{port}"{extra}/>
    </model>
"""


def model(name="Single Line", port=17, display="Single Line",
          string_type="RGB Nodes", p1=1, p2=200, p3=1, start="!Ethernet_:1",
          controller="Ethernet_", proto="ws2811", custom=None, extra=""):
    return MODEL.format(
        name=name, display=display, stringType=string_type, p1=p1, p2=p2, p3=p3,
        controller=controller, start=start, proto=proto, port=port,
        custom=f'CustomModel="{custom}" ' if custom is not None else "",
        extra=extra)


def rgbeffects(*models):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<xrgb>\n  <models>\n'
            + "\n".join(models) + "\n  </models>\n</xrgb>\n")


def show(controller=None, *models):
    nets = controller if controller is not None else networks()
    return xi.parse_show(nets, rgbeffects(*models))


def messages(entries):
    """The ``text`` of a list of problems, for a compact assertion."""
    return [e.get("text") for e in entries]


# ── 1. The operator's real show folder ───────────────────────────────────────


def test_real_networks():
    section("the operator's xlights_networks.xml")
    ctrls = xi.parse_networks(fixture("xlights_networks.xml"))
    ok("one controller is read", len(ctrls) == 1, str(len(ctrls)))
    c = ctrls[0]
    ok("it is Ethernet_ / Garage on a HinksPix PRO V1/V2",
       (c["name"], c["description"], c["vendor"], c["model"])
       == ("Ethernet_", "Garage", "HinksPix", "PRO V1/V2"),
       f"{c['name']}|{c['description']}|{c['vendor']}|{c['model']}")
    ok("its xLights IP is the stale one in the file (the unit is 192.168.10.6)",
       c["ip"] == "192.168.2.10", c["ip"])
    ok("xLights protocol E131 is SlyLED's e131",
       (c["protocol"], c["protocolRaw"]) == ("e131", "E131"),
       f"{c['protocol']}|{c['protocolRaw']}")
    ok("it is under xLights full control", c["fullControl"] is True)
    ok("with xLights' brightness and gamma for full control",
       (c["defaultBrightness"], c["defaultGamma"]) == (100, 1),
       f"{c['defaultBrightness']}|{c['defaultGamma']}")
    ok("its universes are 1 and 2, 510 channels each",
       [(u["universe"], u["channels"]) for u in c["universes"]]
       == [(1, 510), (2, 510)],
       str([(u["universe"], u["channels"]) for u in c["universes"]]))
    # The universe number lives in the `BaudRate` attribute on a network row.
    # This is the single most likely thing to be read wrong, so it is named.
    ok("the universe comes from the row's BaudRate, not a Universe attribute",
       all("Universe" not in u for u in c["universes"])
       and [u["universe"] for u in c["universes"]] == [1, 2])
    ok("no row is mistaken for serial", c["serial"] == [], str(c["serial"]))
    ok("the span is 1..2 with 510-channel pages",
       xi.universe_span(c) == (1, 2, 510), str(xi.universe_span(c)))


def test_real_models():
    section("the operator's xlights_rgbeffects.xml")
    models = xi.parse_models(fixture("xlights_rgbeffects.xml"))
    ok("one model is read", len(models) == 1, str(len(models)))
    m = models[0]
    ok("it is the garage eaves, as a Single Line",
       (m["name"], m["displayAs"]) == ("Single Line", "Single Line"),
       f"{m['name']}|{m['displayAs']}")
    ok("RGB Nodes, 1 string of 200 nodes",
       (m["stringType"], m["parm1"], m["parm2"]) == ("RGB Nodes", 1, 200),
       f"{m['stringType']}|{m['parm1']}|{m['parm2']}")
    ok("it is bound to controller Ethernet_", m["controller"] == "Ethernet_")
    ok("its start channel is !Ethernet_:1", m["startChannel"] == "!Ethernet_:1")
    conn = m["conn"]
    ok("its ControllerConnection is port 17, ws2811",
       (conn["port"], conn["protocol"]) == (17, "ws2811"),
       f"{conn['port']}|{conn['protocol']}")
    ok("...with no per-model brightness, gamma or colour order override",
       (conn["brightness"], conn["gamma"], conn["colorOrder"])
       == (None, None, None),
       f"{conn['brightness']}|{conn['gamma']}|{conn['colorOrder']}")
    ok("...no null nodes and no reverse",
       (conn["nullNodes"], conn["reverse"]) == (0, False),
       f"{conn['nullNodes']}|{conn['reverse']}")
    ok("...and no smart receiver", conn["smartRemote"] == 0)
    ok("the raw attributes are kept, so nothing read is thrown away",
       conn["raw"] == {"Protocol": "ws2811", "Port": "17"}, str(conn["raw"]))
    ok("the preview geometry is reported but not used as a length",
       m["world"]["x2"] == 1006.385132, str(m["world"]))
    ok("200 nodes of RGB are 600 channels",
       xi.node_count(m) * xi.channels_per_node(m["stringType"]) == 600)
    pos = xi.resolve_start_channel(m["startChannel"],
                                   xi.parse_show(fixture("xlights_networks.xml"),
                                                 fixture("xlights_rgbeffects.xml")))
    ok("!Ethernet_:1 is universe 1 channel 1",
       (pos["universe"], pos["channel"], pos["index"]) == (1, 1, 0), str(pos))


def test_real_proposal():
    section("the proposal from the operator's folder")
    layout = xi.parse_show(fixture("xlights_networks.xml"),
                           fixture("xlights_rgbeffects.xml"))
    prop = xi.propose(layout)
    ok("it proposes one port, 17, with 200 pixels",
       [(p["port"], p["leds"]) for p in prop["hinks"]["ports"]] == [(17, 200)],
       str([(p["port"], p["leds"]) for p in prop["hinks"]["ports"]]))
    ok("the port is ws2811 in RGB (the colour order a ws2811 latches)",
       (prop["hinks"]["ports"][0]["protocol"],
        prop["hinks"]["ports"][0]["colorOrder"]) == ("ws2811", "RGB"),
       f"{prop['hinks']['ports'][0]['protocol']}|"
       f"{prop['hinks']['ports'][0]['colorOrder']}")
    ok("forward, no null nodes, enabled",
       (prop["hinks"]["ports"][0]["direction"],
        prop["hinks"]["ports"][0]["startNulls"],
        prop["hinks"]["ports"][0]["enabled"]) == (0, 0, True))
    ok("the port inherits the controller's brightness and gamma",
       (prop["hinks"]["ports"][0]["brightness"],
        prop["hinks"]["ports"][0]["gamma"]) == (100, 1))
    ok("the block starts at universe 1, the controller's first",
       prop["hinks"]["baseUniverse"] == 1, str(prop["hinks"]["baseUniverse"]))
    ok("the protocol is e131", prop["hinks"]["protocol"] == "e131")
    ok("xLights' full-control defaults come across as the port defaults",
       prop["hinks"].get("defaults") == {"brightness": 100, "gamma": 1},
       str(prop["hinks"].get("defaults")))
    ok("length is the 60 px/m convention, in stage mm",
       prop["hinks"]["ports"][0]["mm"] == round(200 * xi.MM_PER_PIXEL_DEFAULT),
       str(prop["hinks"]["ports"][0]["mm"]))
    ok("one fixture, 'Single Line', one string on port 17",
       len(prop["fixtures"]) == 1
       and prop["fixtures"][0]["name"] == "Single Line"
       and [(s["port"], s["leds"]) for s in prop["fixtures"][0]["strings"]]
       == [(17, 200)],
       str(prop["fixtures"]))
    ok("the model row carries what the diff needs",
       prop["models"][0] | {"model": None} == {
           "name": "Single Line", "displayAs": "Single Line",
           "stringType": "RGB Nodes", "port": 17, "ports": [17], "nodes": 200,
           "nodesPerString": 200, "channels": 600, "strings": 1,
           "startChannel": "!Ethernet_:1", "startKind": "controller",
           "universe": 1, "channel": 1, "accepted": True, "problems": [],
           "model": None},
       str(prop["models"][0]))
    # The whole point of the acceptance: the operator's folder imports cleanly.
    ok("nothing to warn about, and every row is accepted",
       prop["warnings"] == [] and prop["models"][0]["accepted"] is True,
       str(prop["warnings"]))
    # And the arithmetic agrees with the file it came from: one 200-pixel port
    # needs two 510-channel universes at 170 px each, which is exactly the
    # universe block the xLights controller declares.
    ok("the proposal's universe block is the one the file declares",
       prop["universes"] == {"base": 1, "count": 2, "channels": 510},
       str(prop["universes"]))


def test_real_folder_end_to_end():
    section("both real files, and the export-dialog file ignored")
    layout = xi.parse_show(fixture("xlights_networks.xml"),
                           fixture("xlights_rgbeffects.xml"))
    ok("the layout has one controller and one model",
       (len(layout["controllers"]), len(layout["models"])) == (1, 1))
    ok("models are indexed by name for chain resolution",
       set(layout["byName"]) == {"Single Line"}, str(list(layout["byName"])))
    ok("a controller may be chosen by name",
       xi.propose(layout, "Ethernet_")["controller"]["name"] == "Ethernet_")
    ok("or by index", xi.propose(layout, 0)["controller"]["name"] == "Ethernet_")
    ok("or found without being named (the only HinksPix)",
       xi.propose(layout)["controller"]["name"] == "Ethernet_")
    # `hinks_export.json` is the export dialog's state — controllers, a schedule
    # day list, a folder. Reading it would put a bogus controller in the picker.
    ok("hinks_export.json is not part of the API at all",
       not any("export" in n.lower() for n in dir(xi)))
    ok("no fixture copy of hinks_export.json exists",
       not os.path.exists(os.path.join(FIXTURES, "hinks_export.json")))


# ── 2. Parsing, on synthetic files ───────────────────────────────────────────


def test_parsing_details():
    section("what the parser tolerates, and what it refuses")
    # Attribute capitalisation drifts between xLights versions; a strict `get`
    # would read a real file as an empty one.
    odd = ('<Networks><controller name="ctrl" ip="10.0.0.2" protocol="artnet"'
           ' fullxlightscontrol="FALSE" defaultbrightnessunderfullcontrol="70"'
           ' defaultgammaunderfullcontrol="2">'
           '<network comport="10.0.0.2" baudrate="12" networktype="artnet"'
           ' maxchannels="510"/></controller></Networks>')
    c = xi.parse_networks(odd)[0]
    ok("a lowercase tag and lowercase attributes still read",
       (c["name"], c["ip"], c["protocol"]) == ("ctrl", "10.0.0.2", "artnet"),
       f"{c['name']}|{c['ip']}|{c['protocol']}")
    ok("...including the full-control flags",
       (c["fullControl"], c["defaultBrightness"], c["defaultGamma"])
       == (False, 70, 2))
    ok("...and its single universe row", xi.universe_span(c) == (12, 12, 510),
       str(xi.universe_span(c)))
    # A serial row's BaudRate really is a baud rate and must not become a universe.
    serial = ('<Networks><Controller Name="dmx" IP="10.0.0.3" Protocol="DMX">'
              '<network ComPort="COM4" BaudRate="115200" NetworkType="DMX"'
              ' MaxChannels="512"/></Controller></Networks>')
    c = xi.parse_networks(serial)[0]
    ok("a serial row is not a universe row",
       c["universes"] == [] and len(c["serial"]) == 1, str(c))
    ok("...and keeps its COM port and baud rate",
       (c["serial"][0]["port"], c["serial"][0]["baud"]) == ("COM4", 115200),
       str(c["serial"][0]))

    for name, text, match in [
            ("empty text", "", "not XML"),
            ("a folder path instead of XML", "/mnt/c/Show", "not XML"),
            ("XML with a trailing tag unclosed", "<Networks><Controller>",
             "not well-formed"),
            ("XML but not a networks file", "<foo><bar/></foo>",
             "no <Controller> found")]:
        try:
            xi.parse_networks(text)
            ok(f"{name} is refused", False, "no error raised")
        except xi.XlightsImportError as exc:
            ok(f"{name} is refused", match in str(exc), str(exc))
    ok("an Element is accepted as well as text",
       xi.parse_networks(ET.fromstring(networks()))[0]["name"] == "Ethernet_")
    ok("bytes are accepted",
       xi.parse_models(ET.tostring(ET.fromstring(
           rgbeffects(model())))) != [])


def test_protocols():
    section("xLights protocol spellings")
    ok("E131 -> e131", xi.protocol_name("E131") == "e131")
    ok("ARTNET -> artnet", xi.protocol_name("artnet") == "artnet")
    ok("DDP -> ddp", xi.protocol_name("DDP") == "ddp")
    ok("an unknown spelling is empty, not a guess",
       xi.protocol_name("ZCPP") == "zcpp" and xi.protocol_name("Blah") == "")
    ok("sACN is not an xLights spelling — xLights calls that mode E131",
       "sacn" not in xi.PROTOCOL_ALIASES.values())


# ── 3. Node and channel maths ────────────────────────────────────────────────


def test_node_count():
    section("node counts")
    for display in ("Single Line", "Tree", "Matrix", "Arches", "Circle"):
        w = []
        m = {"name": "M", "displayAs": display, "parm1": 3, "parm2": 50}
        ok(f"{display} is parm1 x parm2", xi.node_count(m, w) == 150)
        ok(f"...with nothing to warn about", w == [], str(w))
    w = []
    ok("an unlisted type takes the same arithmetic but says so",
       xi.node_count({"name": "Icy", "displayAs": "Icicles", "parm1": 2,
                      "parm2": 25}, w) == 50)
    ok("...naming the model and the type",
       len(w) == 1 and "'Icy'" in w[0] and "Icicles" in w[0]
       and "parm1 x parm2" in w[0], str(w))
    w = []
    ok("a Custom model is read from its text",
       xi.node_count({"name": "C", "displayAs": "Custom", "parm1": 9,
                      "parm2": 9,
                      "customModel": "1,1,10;1,2,3,4,5,6,7,8,9,10"}, w) == 10
       and w == [], str(w))
    w = []
    ok("a Custom model's header sizes it, not the cells that hold a node",
       xi.node_count({"name": "C", "displayAs": "Custom", "parm1": 9,
                      "parm2": 9,
                      "customModel": "2,1,4;1,0,0,0;0,0,0,4"}, w) == 8
       and w == [], str(w))
    w = []
    ok("rows indexing past the header are reported, and the header still wins",
       xi.node_count({"name": "C", "displayAs": "Custom", "parm1": 9,
                      "parm2": 9, "customModel": "1,1,2;1,2,3,4"}, w) == 2
       and len(w) == 1 and "index up to node 4" in w[0], f"{w}")
    for bad, why in [("", "empty"), ("1,1", "header"),
                     ("1,1,0;0", "no nodes per string"),
                     ("0,1,3;1,2,3", "no nodes per string"),
                     ("1,1,3;x,y,z", "not a number"),
                     ("1,1,3;0,0,0", "no node coordinates")]:
        w = []
        m = {"name": "C", "displayAs": "Custom", "parm1": 2, "parm2": 5,
             "customModel": bad}
        ok(f"unreadable CustomModel ({why}) falls back with the reason",
           xi.node_count(m, w) == 10 and len(w) == 1 and why in w[0],
           f"{xi.node_count(m, w)} {w}")
    w = []
    ok("a custom model with no CustomModel text at all also falls back",
       xi.node_count({"name": "C", "displayAs": "Custom", "parm1": 2,
                      "parm2": 5, "customModel": None}, w) == 10
       and "empty" in w[0], str(w))


def test_channels_per_node():
    section("channels per node")
    for string_type, want in [("RGB Nodes", 3), ("GRB Nodes", 3),
                              ("RGB Dumb Nodes", 3), ("RGBW Nodes", 4),
                              ("WRGB Nodes", 4), ("Single Color", 1),
                              ("single colour", 1)]:
        w = []
        ok(f"{string_type} is {want} channel(s) per node",
           xi.channels_per_node(string_type, w) == want and w == [],
           f"{xi.channels_per_node(string_type, w)} {w}")
    # A permutation of RGB is as wide as RGB: warning here would fire on most
    # real shows and teach the operator to ignore warnings.
    for perm in ("RBG", "GRB", "GBR", "BRG", "BGR"):
        ok(f"{perm} Nodes is not treated as unknown",
           xi.channels_per_node(f"{perm} Nodes") == 3)
    w = []
    ok("an unknown type assumes RGB and says so",
       xi.channels_per_node("Some Future Thing", w) == 3
       and "3 channels per node" in w[0], str(w))
    w = []
    ok("a missing type assumes RGB and says so",
       xi.channels_per_node("", w) == 3 and "(none)" in w[0], str(w))
    ok("repeats are not read as a permutation (`rrr` is not RGB)",
       xi.channels_per_node("rrr Nodes") == 3)


# ── 4. StartChannel ──────────────────────────────────────────────────────────


def test_start_channels():
    section("StartChannel forms")
    L = show(None, model("A", start="!Ethernet_:1"))
    ctrl = L["controllers"][0]
    r = xi.resolve_start_channel("!Ethernet_:1", L, controller=ctrl)
    ok("!Controller:1 is the controller's first channel",
       (r["kind"], r["universe"], r["channel"], r["index"])
       == ("controller", 1, 1, 0), str(r))
    r = xi.resolve_start_channel("!Ethernet_:511", L, controller=ctrl)
    ok("!Controller:511 rolls into the second universe",
       (r["universe"], r["channel"]) == (2, 1), str(r))
    r = xi.resolve_start_channel("!Ethernet_:1020", L, controller=ctrl)
    ok("...and the last channel of the block is still in it",
       (r["universe"], r["channel"]) == (2, 510), str(r))
    r = xi.resolve_start_channel("#5:100", L)
    ok("#U:C is taken as written",
       (r["kind"], r["universe"], r["channel"]) == ("universe", 5, 100), str(r))
    r = xi.resolve_start_channel("#192.168.2.10:2:1", L)
    ok("#ip:U:C names the controller by address",
       (r["kind"], r["controller"], r["universe"], r["channel"])
       == ("ip-universe", "Ethernet_", 2, 1), str(r))
    r = xi.resolve_start_channel("#10.9.9.9:3:7", L)
    ok("an address that matches nothing still gives a position",
       (r["kind"], r["controller"], r["universe"], r["channel"])
       == ("ip-universe", None, 3, 7), str(r))
    r = xi.resolve_start_channel("511", L, controller=ctrl)
    ok("a bare integer is an absolute channel in the controller",
       (r["kind"], r["universe"], r["channel"]) == ("absolute", 2, 1), str(r))
    r = xi.resolve_start_channel("1", L)
    ok("...and without a controller it walks the show's channel space",
       (r["universe"], r["channel"]) == (1, 1), str(r))
    two = show(None, model("A", start="1"))
    two["controllers"].append(dict(two["controllers"][0], name="Second",
                                   ip="192.168.2.11"))
    r = xi.resolve_start_channel("1030", two)
    ok("...across every controller, in file order",
       (r["universe"], r["channel"]) == (1, 10), str(r))
    for expr, match in [("", "no start channel"),
                        ("nonsense", "not a channel"),
                        ("!Nope:1", "no controller named 'Nope'"),
                        ("!Ethernet_:0", "below 1"),
                        ("#1", "not #universe:channel"),
                        ("#0:1", "not a valid position"),
                        ("!Ethernet_:2000", "past the last universe")]:
        try:
            xi.resolve_start_channel(expr, L, controller=ctrl)
            ok(f"{expr!r} is refused", False, "no error raised")
        except xi.XlightsImportError as exc:
            ok(f"{expr!r} is refused", match in str(exc), str(exc))


def test_chains():
    section(">Model:N / @Model:N chains")
    # A is universe 1 channel 1 for 600 channels, so its first channel after the
    # end is universe 2 channel 91 (510 + 90).
    L = show(None, model("A"), model("B", port=18, start=">A:1"),
             model("C", port=19, start="@A:1"),
             model("D", port=20, start=">A:11"))
    ctrl = L["controllers"][0]
    r = xi.resolve_start_channel(">A:1", L, controller=ctrl)
    ok(">M:1 is the channel after M's last",
       (r["kind"], r["universe"], r["channel"], r["chainTo"])
       == ("chain-after", 2, 91, "A"), str(r))
    r = xi.resolve_start_channel(">A:11", L, controller=ctrl)
    ok("...and the offset is 1-based, so :11 is ten channels further",
       (r["universe"], r["channel"]) == (2, 101), str(r))
    r = xi.resolve_start_channel("@A:1", L, controller=ctrl)
    ok("@M:1 is M's first channel",
       (r["kind"], r["universe"], r["channel"]) == ("chain-at", 1, 1), str(r))
    r = xi.resolve_start_channel("@A:600", L, controller=ctrl)
    ok("@M:600 is M's last channel",
       (r["universe"], r["channel"]) == (2, 90), str(r))
    # A chain may point at a model that is itself chained. A and B are 600
    # channels each, so on a three-universe controller B's first channel past
    # its end is universe 3 channel 181 (1020 + 180).
    nested = show(networks(universes=(1, 2, 3)), model("A"),
                  model("B", port=18, start=">A:1"),
                  model("E", port=21, start=">B:1"))
    r = xi.resolve_start_channel(">B:1", nested)
    ok("a chain off a chained model resolves through it",
       (r["universe"], r["channel"], r["index"]) == (3, 181, 1200), str(r))
    # ...and a chain that runs off the end of the controller is refused rather
    # than wrapped around to a universe that is not there.
    tight = show(None, model("A"), model("B", port=18, start=">A:1"),
                 model("E", port=21, start=">B:1"))
    try:
        xi.resolve_start_channel(">B:1", tight)
        ok("a chain past the last universe is refused", False, "no error raised")
    except xi.XlightsImportError as exc:
        ok("a chain past the last universe is refused",
           "past the last universe" in str(exc), str(exc))
    prop = xi.propose(tight)
    ok("...and the row that could not resolve is refused, not silently placed",
       {r2["name"]: r2["accepted"] for r2 in prop["models"]}["E"] is False,
       str({r2["name"]: r2["accepted"] for r2 in prop["models"]}))
    for name, text, match in [
            ("a cycle", show(None, model("P", start=">Q:1"),
                             model("Q", start=">P:1")), "chains back to itself"),
            ("a self-reference", show(None, model("P", start=">P:1")),
             "chains back to itself"),
            ("an unknown model", show(None, model("P", start=">Nope:1")),
             "no model named 'Nope'"),
            ("a missing offset", show(None, model("P", start=">A:0")), "1 or more"),
            ("offset with no number", show(None, model("A"),
                                           model("P", start=">A:x")),
             "1 or more")]:
        try:
            xi.resolve_start_channel(text["models"][-1]["startChannel"], text)
            ok(f"{name} is refused", False, "no error raised")
        except xi.XlightsImportError as exc:
            ok(f"{name} is refused", match in str(exc), str(exc))


# ── 5. The proposal's shape and its refusals ─────────────────────────────────


def test_proposal_rows():
    section("multi-output models")
    L = show(None, model("Multi", port=16, p1=3, p2=100))
    prop = xi.propose(L)
    ok("three strings take outputs 16, 17 and 18",
       [p["port"] for p in prop["hinks"]["ports"]] == [16, 17, 18],
       str([p["port"] for p in prop["hinks"]["ports"]]))
    # One string per output, so each port drives parm2 — *not* the model's total
    # of parm1 x parm2. A port set to the total would send three times the
    # channels the strip has.
    ok("...each with parm2 pixels, not the model's total",
       {p["leds"] for p in prop["hinks"]["ports"]} == {100},
       str({p["leds"] for p in prop["hinks"]["ports"]}))
    ok("...and one fixture with three strings, in port order",
       [(s["port"], s["leds"]) for s in prop["fixtures"][0]["strings"]]
       == [(16, 100), (17, 100), (18, 100)], str(prop["fixtures"][0]["strings"]))
    ok("the row records the outputs it spans", prop["models"][0]["ports"]
       == [16, 17, 18])
    ok("the row reports both the per-string and the total node count",
       (prop["models"][0]["nodesPerString"], prop["models"][0]["nodes"])
       == (100, 300), str(prop["models"][0]))
    ok("the model row counts channels over the whole model (300 nodes x 3)",
       prop["models"][0]["channels"] == 900, str(prop["models"][0]["channels"]))
    ok("nothing to warn about for a plain multi-string model",
       prop["warnings"] == [], str(prop["warnings"]))
    # parm3 is not interpreted; a value other than 1 is reported, because for
    # some model types it groups strands onto outputs and this importer does not
    # know which.
    prop = xi.propose(show(None, model("Stranded", port=16, p1=3, p2=100, p3=2)))
    ok("parm3 != 1 is reported rather than guessed at",
       any("parm3=2" in w for w in prop["warnings"]), str(prop["warnings"]))
    ok("...and the reading it applies instead is stated",
       any("one output per string" in w for w in prop["warnings"]))
    ok("...but the row is still accepted (a warning, not an error)",
       prop["models"][0]["accepted"] is True)


def test_string_layout():
    section("how a model's nodes split over outputs")
    L = xi.string_layout({"name": "M", "displayAs": "Matrix", "parm1": 6,
                          "parm2": 40, "parm3": 1})
    ok("parm1 strings of parm2 nodes",
       (L["strings"], L["nodesPerString"], L["nodes"], L["outputs"])
       == (6, 40, 240, 6), str(L))
    ok("...read from the parms", L["source"] == "parms")
    C = xi.string_layout({"name": "C", "displayAs": "Custom", "parm1": 9,
                          "parm2": 9, "parm3": 1,
                          "customModel": "2,1,4;1,2,0,0;0,0,3,4"})
    ok("a Custom model is read from its own header, not its parms",
       (C["strings"], C["nodesPerString"], C["nodes"], C["source"])
       == (2, 4, 8, "custom"), str(C))
    w = []
    F = xi.string_layout({"name": "F", "displayAs": "Icicles", "parm1": 2,
                          "parm2": 30, "parm3": 1}, w)
    ok("an unlisted type falls back to the parms, with its warning",
       (F["nodes"], len(w)) == (60, 1), f"{F} {w}")


def test_proposal_refusals():
    section("what the proposal refuses, and how visibly")
    caps = type("Caps", (), {"name": "PRO V1/V2", "max_pixel_port": 48,
                             "input_protocols": ("e131", "artnet")})()

    prop = xi.propose(show(None, model("Over", port=47, p1=3, p2=100)),
                      caps=caps)
    row = prop["models"][0]
    ok("a model reaching past the last output is an error", row["accepted"] is False)
    ok("...naming the output and the model's limit",
       "output 49" in row["problems"][0]["text"]
       and "PRO V1/V2" in row["problems"][0]["text"],
       str(row["problems"]))
    ok("...and its ports are withdrawn, so the table drives nothing it cannot",
       prop["hinks"]["ports"] == [] and prop["fixtures"] == [],
       str(prop["hinks"]["ports"]))

    prop = xi.propose(show(None, model("A", port=17), model("B", port=17)))
    rows = {r["name"]: r for r in prop["models"]}
    ok("two models on one output: the first keeps it, the second is refused",
       rows["A"]["accepted"] is True and rows["B"]["accepted"] is False)
    ok("...naming the model that has it",
       "already taken by 'A'" in rows["B"]["problems"][0]["text"],
       str(rows["B"]["problems"]))
    ok("...and only the accepted model's port is in the table",
       [p["port"] for p in prop["hinks"]["ports"]] == [17])
    ok("...and only it has a fixture",
       [f["name"] for f in prop["fixtures"]] == ["A"])

    prop = xi.propose(show(None, model("NoPort", port=0)))
    ok("a model with no port is refused",
       prop["models"][0]["accepted"] is False
       and "no ControllerConnection Port" in prop["models"][0]["problems"][0]["text"],
       str(prop["models"][0]["problems"]))

    prop = xi.propose(show(None, model("Zero", p2=0)))
    ok("a model with no pixels is refused",
       prop["models"][0]["accepted"] is False
       and "node(s)" in prop["models"][0]["problems"][0]["text"],
       str(prop["models"][0]["problems"]))

    prop = xi.propose(show(None, model("Bad", start="!Nope:1")))
    ok("an unresolvable start channel is refused, not guessed",
       prop["models"][0]["accepted"] is False
       and "its start channel" in prop["models"][0]["problems"][0]["text"],
       str(prop["models"][0]["problems"]))
    ok("...and the row keeps the text it could not resolve",
       prop["models"][0]["startChannel"] == "!Nope:1")

    prop = xi.propose(show(None, model("Off", controller="")))
    ok("a model bound to no controller is not this controller's",
       prop["models"] == []
       and any("other controllers" in w for w in prop["warnings"]),
       str(prop["warnings"]))

    # The one that matters most: a refused row must not silently look fine.
    prop = xi.propose(show(None, model("Over", port=81)))
    ok("an error never hides behind a warning",
       prop["models"][0]["problems"][0]["level"] == "error")
    ok("and no accepted row is ever missing a fixture",
       all(len(prop["fixtures"]) == sum(1 for r in prop["models"]
                                        if r["accepted"]) for _ in [0]))


def test_proposal_warnings():
    section("the warnings the proposal raises")
    prop = xi.propose(show(networks(protocol="DDPMODE"), model("A")),
                      caps=type("Caps", (), {"name": "PRO V1/V2",
                                             "max_pixel_port": 48,
                                             "input_protocols": ("e131",
                                                                 "artnet")})())
    ok("an unknown protocol falls back to E131 and says so",
       prop["hinks"]["protocol"] == "e131"
       and any("does not know" in w for w in prop["warnings"]),
       str(prop["warnings"]))

    prop = xi.propose(show(networks(protocol="DDP"), model("A")),
                      caps=type("Caps", (), {"name": "PRO V1/V2",
                                             "max_pixel_port": 48,
                                             "input_protocols": ("e131",
                                                                 "artnet")})())
    ok("a protocol the target cannot run is a warning here (an error at accept)",
       prop["hinks"]["protocol"] == "ddp"
       and any("does not offer ddp" in w for w in prop["warnings"]),
       str(prop["warnings"]))

    prop = xi.propose(show(networks(full="FALSE"), model("A")))
    ok("a controller not under full control is called out",
       any("full control" in w for w in prop["warnings"]), str(prop["warnings"]))
    ok("...and its brightness is not pushed as a default",
       "defaults" not in prop["hinks"], str(prop["hinks"].keys()))

    prop = xi.propose(show(networks(universes=(1, 5)), model("A")))
    ok("a gap in the universe list is called out",
       any("1..5" in w or "not reproduced" in w for w in prop["warnings"]),
       str(prop["warnings"]))
    ok("...and the block still starts at the controller's first universe",
       prop["hinks"]["baseUniverse"] == 1)

    prop = xi.propose(show(networks(universes=()), model("A")))
    ok("a controller with no universes proposes from universe 1, with a warning",
       prop["hinks"]["baseUniverse"] == 1
       and any("no network universes" in w for w in prop["warnings"]),
       str(prop["warnings"]))

    prop = xi.propose(show(None, model("A", extra=' SmartRemote="3"')))
    ok("a smart receiver is flagged as needing the type from the port editor",
       any("smart receiver" in w for w in prop["warnings"]), str(prop["warnings"]))

    prop = xi.propose(show(None, model("A", proto="ws2812x")))
    ok("an unknown pixel protocol warns and proposes ws2811",
       prop["hinks"]["ports"][0]["protocol"] == "ws2811"
       and any("does not have a code for" in w for w in prop["warnings"]),
       str(prop["warnings"]) + str(prop["hinks"]["ports"][0]["protocol"]))

    prop = xi.propose(show(None, model("A", proto="ws2812b")))
    ok("...while a protocol SlyLED does have is left alone",
       prop["hinks"]["ports"][0]["protocol"] == "ws2812b"
       and prop["warnings"] == [], str(prop["warnings"]))
    ok("...and its default colour order is the one that strip latches",
       prop["hinks"]["ports"][0]["colorOrder"] == "GRB",
       str(prop["hinks"]["ports"][0]["colorOrder"]))

    prop = xi.propose(show(None, model("A")))
    ok("nothing else warns about a plain model", prop["warnings"] == [],
       str(prop["warnings"]))


def test_overrides():
    section("per-model overrides win over the controller's defaults")
    L = show(None, model("A", port=17, extra=' Brightness="50" Gamma="2"'
                                          ' ColorOrder="GRB" NullNodes="4"'
                                          ' Reverse="1"'))
    prop = xi.propose(L)
    p = prop["hinks"]["ports"][0]
    ok("brightness and gamma come from the model",
       (p["brightness"], p["gamma"]) == (50, 2), f"{p['brightness']}|{p['gamma']}")
    ok("colour order comes from the model", p["colorOrder"] == "GRB")
    ok("null nodes become the port's start nulls", p["startNulls"] == 4)
    ok("reverse becomes direction 1", p["direction"] == 1)
    L = show(networks(full="FALSE", bright=70, gamma=3), model("A"))
    p = xi.propose(L)["hinks"]["ports"][0]
    ok("with no override the controller's own values still fill the row",
       (p["brightness"], p["gamma"]) == (70, 3), f"{p['brightness']}|{p['gamma']}")


def test_controller_choice():
    section("choosing the controller")
    two = show(networks(name="One"), model("A"))
    two["controllers"].append(dict(two["controllers"][0], name="Two",
                                   ip="192.168.2.11"))
    try:
        xi.propose(two)
        ok("two HinksPix controllers are not a coin flip", False, "no error")
    except xi.XlightsImportError as exc:
        ok("two HinksPix controllers are not a coin flip",
           "2 controllers" in str(exc) and "One" in str(exc), str(exc))
    ok("naming one resolves it", xi.propose(two, "Two")["controller"]["name"]
       == "Two")
    ok("indexing one resolves it", xi.propose(two, 1)["controller"]["name"]
       == "Two")
    try:
        xi.propose(two, "Three")
        ok("an unknown name is refused", False, "no error")
    except xi.XlightsImportError as exc:
        ok("an unknown name is refused", "no controller named 'Three'" in str(exc),
           str(exc))
    # A single non-HinksPix controller is still importable: the file is the
    # operator's, and an importer that only understands one vendor's folder
    # would be a second way to lose a layout.
    other = show(networks(vendor="Falcon", model="F16V3"), model("A"))
    ok("a lone controller of another vendor is still the obvious choice",
       xi.propose(other)["controller"]["vendor"] == "Falcon")


def test_purity():
    section("the module stays pure")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "desktop", "shared",
                            "hinkspix_xlights_import.py"), encoding="utf-8").read()
    # The claim is that this module performs no I/O and opens no transport — the
    # words may appear in prose (they do, in the docstring) but no such thing may
    # be imported or called.
    for banned in ("import socket", "import urllib", "import requests",
                   "import http", "import subprocess", "import os",
                   "urlopen(", "open(", "os.path"):
        ok(f"no {banned!r} anywhere in the module", banned not in src)
    ok("it does not import the orchestrator or call the bridge's transport",
       "import orch" not in src and "hb.command" not in src
       and "hb.probe" not in src)
    ok("it is imported only for tables from the bridge",
       "import hinkspix_bridge as hb" in src
       and "hb.PIXEL_PROTOCOLS" in src and "hb.MAX_PORTS" in src)


def main():
    test_real_networks()
    test_real_models()
    test_real_proposal()
    test_real_folder_end_to_end()
    test_parsing_details()
    test_protocols()
    test_node_count()
    test_channels_per_node()
    test_start_channels()
    test_chains()
    test_string_layout()
    test_proposal_rows()
    test_proposal_refusals()
    test_proposal_warnings()
    test_overrides()
    test_controller_choice()
    test_purity()
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
