"""hinkspix_xlights_import — read a HinksPix layout out of an xLights show folder (#947).

Pure: XML text in, plain dicts out. No Flask, no sockets, no filesystem — the
caller reads the files and hands over their bytes, so every rule below is
testable against captured XML rather than against a live show, and nothing here
can touch a controller.

**Why import at all.** xLights is where this operator's layout actually lives.
Which output drives which strip, how many pixels are on it and what the model is
called were all typed into xLights years ago; retyping them into SlyLED is both
tedious and a chance to get a port wrong, and a wrong port is a dark line on the
house on the night. So this module reads those two files and *proposes* a
config. Nothing is stored here and nothing is written to the controller: the
operator sees the diff against what SlyLED already has and accepts it, and the
acceptance runs through the same validation as a hand-typed edit (#946).

**The two files**, both from the operator's real show folder (`SlyMega Art Inc /
Projects / Xlights - Home Eves`), which is the acceptance fixture under
`tests/fixtures/xlights_home_eves/`.

``xlights_networks.xml`` — the controllers. One ``<Controller>`` element each,
with one ``<network>`` child per universe it drives::

    <Networks computer="DAVEBOOK-5">
      <Controller Id="1" Name="Ethernet_" Description="Garage" Type="Ethernet"
                  Vendor="HinksPix" Model="PRO V1/V2" IP="192.168.2.10"
                  Protocol="E131" FullxLightsControl="TRUE"
                  DefaultBrightnessUnderFullControl="100"
                  DefaultGammaUnderFullControl="1">
        <network ComPort="192.168.2.10" BaudRate="1" NetworkType="E131" MaxChannels="510"/>
        <network ComPort="192.168.2.10" BaudRate="2" NetworkType="E131" MaxChannels="510"/>
      </Controller>
    </Networks>

``xlights_rgbeffects.xml`` — the models. Geometry lives in the opaque
``parm1/parm2/parm3`` attributes and the physical binding in a
``<ControllerConnection>`` child::

    <xrgb><models>
      <model name="Single Line" DisplayAs="Single Line" StringType="RGB Nodes"
             parm1="1" parm2="200" Controller="Ethernet_"
             StartChannel="!Ethernet_:1">
        <ControllerConnection Protocol="ws2811" Port="17"/>
      </model>
    </models></xrgb>

``hinks_export.json`` sits in the same folder and is the xLights *export
dialog's* own state — a controller list for the upload tab, a schedule day
list, a folder picker. It carries no layout and is deliberately never read.

**Two readings are derived rather than captured**, and say so where they are
implemented: the ``CustomModel`` text format, and the ``>Model:N`` / ``@Model:N``
chain offsets. Neither shape appears in the operator's folder, so neither could
be checked against real bytes. Both fail loudly rather than quietly (see
``node_count`` and ``resolve_start_channel``), so a wrong reading surfaces as a
warning or an error the operator can see, never as a plausible-looking port
table.
"""

import xml.etree.ElementTree as ET

import hinkspix_bridge as hb
import hinkspix_config as hc

__all__ = [
    "XlightsImportError", "protocol_name", "parse_networks", "parse_models",
    "parse_show", "universe_span", "node_count", "string_layout",
    "channels_per_node", "resolve_start_channel", "propose",
    "DISPLAY_AS_PARMS", "STRING_TYPE_CHANNELS", "PROTOCOL_ALIASES",
    "MM_PER_PIXEL_DEFAULT",
]

# The length SlyLED assumes for a pixel when nothing measured is known: 16.67 mm
# (60 px/m). Not an xLights number — xLights' own model dimensions are drawing
# coordinates on the house preview, not strip lengths — so an imported port row
# gets this and the operator corrects it in the fixture editor. Same default the
# port editor and `fixtures-from-ports` use, which is what keeps an imported row
# and a hand-typed one identical.
MM_PER_PIXEL_DEFAULT = 16.67


class XlightsImportError(ValueError):
    """XML that is not an xLights file, or a reference that cannot be resolved.

    Never raised for something merely *odd* in a real file: an unknown model
    type, a model on another controller or a missing universe list are all
    reported as proposal warnings, because a real show folder is allowed to
    contain things this importer does not model, and refusing to import the
    other twenty models over one of them would be worse than saying so.
    """


# ── Attribute access ─────────────────────────────────────────────────────────


def _local(tag):
    """An element's tag without an XML namespace prefix."""
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def _attr(el, *names, default=None):
    """The first attribute present among `names`, case-insensitively.

    xLights' own capitalisation is inconsistent (`FullxLightsControl` has a
    lowercase `x`; `DefaultBrightnessUnderFullControl` capitalises the F) and has
    drifted between versions, so an exact ``el.get`` would read a real file as an
    empty one and report a controller with no brightness rather than the
    brightness it has.
    """
    for name in names:
        value = el.get(name)
        if value is not None:
            return value
    lowered = {k.lower(): v for k, v in el.attrib.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return default


def _int(value, default=0):
    """An attribute as an int. xLights writes empty strings for "unset"."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _truthy(value, default=False):
    """xLights writes TRUE/FALSE in some files and 0/1 in others."""
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes", "y", "on")


def _num(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _elements(root, name):
    """Every descendant whose tag is `name`, case-insensitively.

    Matched on the *whole* tag, never a prefix: ``Controller`` and
    ``ControllerConnection`` both appear in these files, and a ``startswith``
    match would read the second as the first — which is exactly the kind of
    reading that yields a confident, wrong port table.
    """
    want = name.lower()
    return [el for el in root.iter() if _local(el.tag).lower() == want]


def _root(xml):
    """An Element for `xml`, which is XML text, bytes, or an Element.

    A *path* is deliberately not accepted. Reading the file is the caller's job
    and keeping it that way is what makes this module pure, and what makes
    "which folder did you read?" a question the route answers visibly instead of
    this module answering invisibly from a relative path.
    """
    if isinstance(xml, ET.Element):
        return xml
    if isinstance(xml, (bytes, bytearray)):
        xml = bytes(xml).decode("utf-8", "replace")
    if not isinstance(xml, str):
        raise XlightsImportError(
            f"expected XML text or an Element, got {type(xml).__name__}")
    if xml.lstrip()[:1] != "<":
        raise XlightsImportError(
            "not XML — pass the file's contents; reading the file belongs to "
            "the caller")
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise XlightsImportError(f"not well-formed XML: {exc}") from None


def _warn(warnings, text):
    """Append to the caller's collector, when they asked for one."""
    if warnings is not None:
        warnings.append(text)


# ── Protocols ────────────────────────────────────────────────────────────────

# xLights protocol spelling -> SlyLED's. xLights writes `E131` where SlyLED says
# `e131`, and offers ZCPP/FPP which no HinksPix accepts at all. There is no
# `sacn` row: sACN is a SlyLED-side alias for the same wire mode (#946), and
# xLights calls that mode E131.
PROTOCOL_ALIASES = {
    "e131": "e131", "e1.31": "e131", "e131-1": "e131",
    "artnet": "artnet", "art-net": "artnet",
    "ddp": "ddp", "zcpp": "zcpp", "fpp": "fpp", "dmx": "dmx",
}

# A `<network>` row is a universe row only when its type is one of these; a
# serial row's `BaudRate` really is a baud rate.
_NETWORK_TYPES = ("e131", "e1.31", "artnet", "art-net", "ddp", "zcpp", "fpp")


def protocol_name(raw):
    """xLights' protocol spelling as SlyLED's, or ``""`` if unrecognised.

    An unknown spelling comes back empty rather than as a guess: defaulting an
    unknown protocol to E131 would be a silent statement that the controller is
    in a mode it may not be in.
    """
    return PROTOCOL_ALIASES.get(str(raw or "").strip().lower(), "")


# ── xlights_networks.xml ─────────────────────────────────────────────────────


def _network_rows(el):
    """The ``<network>`` children of a controller, as plain dicts.

    An E131 row keeps its **universe number in the `BaudRate` attribute** —
    xLights reuses the serial baud field for it on network rows, which is why the
    operator's controller, whose universes are 1 and 2, reads
    ``BaudRate="1"``/``BaudRate="2"``. ``ComPort`` holds the IP for the same
    reason. Some versions write an explicit ``Universe`` attribute as well, and
    that is preferred when present since it needs no interpretation.
    """
    rows = []
    for net in el:
        if _local(net.tag).lower() != "network":
            continue
        raw_type = _attr(net, "NetworkType", "Type", default="")
        kind = str(raw_type).strip().lower()
        channels = _int(_attr(net, "MaxChannels", "Channels"), 510)
        target = _attr(net, "ComPort", "IP", default="")
        row = {"networkType": raw_type, "channels": channels,
               "protocol": protocol_name(raw_type), "ip": "", "port": "",
               "universe": None, "serial": False}
        if kind in _NETWORK_TYPES:
            # `ComPort` is the address on a network row — xLights has no field
            # named for it. A row whose type is unknown keeps whatever it had
            # there and is reported as serial, which is what an unknown type
            # most likely is.
            row["ip"] = target
            explicit = _attr(net, "Universe")
            universe = (_int(explicit, 0) if explicit is not None
                        else _int(_attr(net, "BaudRate"), 0))
            row["universe"] = universe or 1
        else:
            row["serial"] = True
            row["port"] = target
            row["baud"] = _int(_attr(net, "BaudRate"), 0)
        rows.append(row)
    return rows


def parse_networks(xml):
    """The controllers in an ``xlights_networks.xml``, as plain dicts.

    Each controller::

        {"id": 1, "name": "Ethernet_", "description": "Garage",
         "type": "Ethernet", "vendor": "HinksPix", "model": "PRO V1/V2",
         "variant": "", "ip": "192.168.2.10",
         "protocol": "e131", "protocolRaw": "E131",
         "fullControl": True, "defaultBrightness": 100, "defaultGamma": 1,
         "active": True, "universeCount": 2,
         "universes": [{"universe": 1, "channels": 510, "ip": "192.168.2.10",
                        "protocol": "e131", "networkType": "E131"}, ...],
         "serial": [{"port": "COM3", "baud": 115200, ...}]}

    ``universes`` keeps file order. The importer does not reorder it: xLights
    writes the rows in the order the controller was set up, and that order is
    what the ``!Controller:N`` channel-space arithmetic walks.
    """
    root = _root(xml)
    out = []
    for el in _elements(root, "Controller"):
        rows = _network_rows(el)
        universes = [r for r in rows if not r["serial"]]
        raw_protocol = _attr(el, "Protocol", default="")
        out.append({
            "id": _int(_attr(el, "Id"), 0),
            "name": str(_attr(el, "Name", default="")).strip(),
            "description": str(_attr(el, "Description", default="")).strip(),
            "type": str(_attr(el, "Type", default="")).strip(),
            "vendor": str(_attr(el, "Vendor", default="")).strip(),
            "model": str(_attr(el, "Model", default="")).strip(),
            "variant": str(_attr(el, "Variant", default="")).strip(),
            "ip": str(_attr(el, "IP", "Ip", default="")).strip(),
            "protocol": protocol_name(raw_protocol),
            "protocolRaw": str(raw_protocol).strip(),
            "fullControl": _truthy(_attr(el, "FullxLightsControl",
                                         "FullXlightsControl"), False),
            "defaultBrightness": _int(
                _attr(el, "DefaultBrightnessUnderFullControl"), 100),
            "defaultGamma": _int(_attr(el, "DefaultGammaUnderFullControl"), 1),
            "active": str(_attr(el, "ActiveState", default="Active")).strip()
                      .lower() != "inactive",
            "universeCount": len(universes),
            "universes": universes,
            "serial": [r for r in rows if r["serial"]],
        })
    if not out:
        raise XlightsImportError(
            "no <Controller> found — is this an xlights_networks.xml?")
    return out


def universe_span(controller):
    """``(first, last, channels)`` over a controller's universes, or ``None``.

    ``channels`` is the widest row's channel count, which is what the
    ``!Controller:N`` decomposition walks. Mixed widths within one controller do
    exist in xLights; the widest is used and ``propose`` warns when the rows
    disagree, rather than silently picking one and being wrong about the others.
    """
    rows = (controller or {}).get("universes") or []
    nums = [r["universe"] for r in rows if r.get("universe")]
    if not nums:
        return None
    return min(nums), max(nums), max(r.get("channels") or 510 for r in rows)


# ── xlights_rgbeffects.xml ───────────────────────────────────────────────────

# ControllerConnection attributes, as xLights spells them, and what SlyLED calls
# the same thing. `StartNulls` is accepted alongside `NullNodes` because the two
# names both appear in xLights' own files; the SlyLED port field is `startNulls`
# and the two mean the same thing (blank pixels before the string's first).
_CONN_FIELDS = (
    ("port", ("Port", "Output")),
    ("protocol", ("Protocol",)),
    ("colorOrder", ("ColorOrder", "ColourOrder")),
    ("brightness", ("Brightness",)),
    ("gamma", ("Gamma",)),
    ("nullNodes", ("NullNodes", "StartNulls")),
    ("reverse", ("Reverse", "Reversed")),
    ("smartRemote", ("SmartRemote",)),
)


def parse_models(xml):
    """The models in an ``xlights_rgbeffects.xml``, as plain dicts.

    Each model::

        {"name": "Single Line", "displayAs": "Single Line",
         "stringType": "RGB Nodes", "parm1": 1, "parm2": 200, "parm3": 1,
         "controller": "Ethernet_", "startChannel": "!Ethernet_:1",
         "customModel": None,
         "conn": {"port": 17, "protocol": "ws2811", "colorOrder": None,
                  "brightness": None, "gamma": None, "nullNodes": 0,
                  "reverse": False, "smartRemote": 0, "raw": {...}},
         "world": {"x": -489.2, "y": 547.9, "z": 0.0, "x2": 1006.4, ...}}

    ``raw`` is every attribute the ``<ControllerConnection>`` actually carried.
    The eight named fields are the ones with SlyLED counterparts; keeping the
    rest means a field this importer does not yet use is not *lost* by reading
    the file, and a later change can reach it without re-capturing anything.

    ``world`` is the preview geometry. It is reported, never trusted: xLights'
    ``X2``/``Y2``/``Z2`` are drawing dimensions on the house preview (a fresh
    Single Line gets a default one), not measured strip lengths, so nothing here
    turns into a stage-mm value.
    """
    root = _root(xml)
    out = []
    for el in _elements(root, "model"):
        conn_el = next((c for c in el
                        if _local(c.tag).lower() == "controllerconnection"),
                       None)
        conn = {"port": None, "protocol": "", "colorOrder": None,
                "brightness": None, "gamma": None, "nullNodes": 0,
                "reverse": False, "smartRemote": 0, "raw": {}}
        if conn_el is not None:
            conn["raw"] = dict(conn_el.attrib)
            for key, names in _CONN_FIELDS:
                value = _attr(conn_el, *names)
                if value is None:
                    continue
                if key == "reverse":
                    conn[key] = _truthy(value, False)
                elif key in ("nullNodes", "smartRemote"):
                    conn[key] = _int(value, 0)
                elif key in ("port", "brightness", "gamma"):
                    # xLights writes an empty or zero value for "not set", and
                    # the difference matters: a brightness of 0 is not a
                    # brightness, it is the absence of one.
                    conn[key] = _int(value, 0) or None
                else:
                    conn[key] = str(value).strip()
        out.append({
            "name": str(_attr(el, "name", "Name", default="")).strip(),
            "displayAs": str(_attr(el, "DisplayAs", default="")).strip(),
            "stringType": str(_attr(el, "StringType", default="")).strip(),
            "parm1": _int(_attr(el, "parm1"), 0),
            "parm2": _int(_attr(el, "parm2"), 0),
            "parm3": _int(_attr(el, "parm3"), 0),
            "controller": str(_attr(el, "Controller", default="")).strip(),
            "startChannel": str(_attr(el, "StartChannel", default="")).strip(),
            "customModel": _attr(el, "CustomModel"),
            "conn": conn,
            "world": {
                "x": _num(_attr(el, "WorldPosX")),
                "y": _num(_attr(el, "WorldPosY")),
                "z": _num(_attr(el, "WorldPosZ")),
                "x2": _num(_attr(el, "X2")),
                "y2": _num(_attr(el, "Y2")),
                "z2": _num(_attr(el, "Z2")),
            },
        })
    return out


def parse_show(networks_xml, rgbeffects_xml):
    """The two files as one layout: ``{"controllers": [...], "models": [...]}``.

    Also indexes the models by name, because resolving one model's
    ``StartChannel`` may need another model's position and re-scanning the list
    for every chain would make a cycle slow instead of merely wrong.
    """
    layout = {"controllers": parse_networks(networks_xml),
              "models": parse_models(rgbeffects_xml)}
    layout["byName"] = {m["name"]: m for m in layout["models"] if m["name"]}
    return layout


# ── Node and channel maths ───────────────────────────────────────────────────

# Model types whose node count is `parm1` strings of `parm2` nodes. These are the
# types with captured evidence behind them (the operator's Single Line, and the
# model-dialog labels xLights gives the rest of the loop types). Anything *not*
# in this set takes the same arithmetic but with a warning — see `node_count`.
DISPLAY_AS_PARMS = ("Single Line", "Tree", "Matrix", "Arches", "Circle")

# What each parm means for those types, as xLights labels them: parm1 is the
# number of strings (and therefore the number of physical outputs the model
# spans), parm2 the nodes on each.
_PARMS_DOC = "parm1 strings x parm2 nodes"

STRING_TYPE_CHANNELS = {
    "rgb nodes": 3, "rgb dumb nodes": 3, "rgb": 3,
    "rgbw nodes": 4, "rgbw dumb nodes": 4, "rgbw": 4,
    "single color": 1, "single colour": 1, "single color nodes": 1,
    "white": 1, "single": 1,
}


def _custom_header(text):
    """``((strings, strandsPerString, nodesPerString) | None, note)``.

    The text is a header ``"<strings>,<strandsPerString>,<nodesPerString>"``
    followed by one ``;``-separated row per strand, each a comma-separated list
    of node indices where ``0`` means "no node on this cell". **The header sizes
    the model** — it is what the model dialog shows and what xLights allocates
    channels for — so the header is what comes back, and the row indices are only
    checked against it.

    ``note`` carries the reason the header could not be read (the first element is
    then ``None``), or, on a header that read fine, an advisory: rows that index
    past the header's extent mean the two disagree about how big the model is.
    Both are warnings the caller attaches, never silence.

    *Derived, not captured* — no custom model exists in the operator's folder, so
    this reads the documented format rather than bytes we hold. It therefore
    fails **closed**: anything it cannot make sense of comes back with a reason
    and the caller falls back to ``parm1 x parm2``, so a wrong reading shows up
    as a node count the operator can check in the diff.
    """
    raw = str(text or "").strip()
    if not raw:
        return None, "empty"
    head, _, body = raw.partition(";")
    parts = head.split(",")
    if len(parts) < 3:
        return None, "no strings,strands,nodes header"
    strings, strands = _int(parts[0], 0), _int(parts[1], 0)
    per = _int(parts[2], 0)
    if strings <= 0 or per <= 0:
        return None, "the header has no strings or no nodes per string"
    best = 0
    for row in body.split(";"):
        for token in row.split(","):
            token = token.strip()
            if not token:
                continue
            value = _int(token, -1)
            if value < 0:
                return None, "a coordinate is not a number"
            best = max(best, value)
    if best <= 0:
        return None, "no node coordinates"
    note = ""
    if best > strings * per:
        note = (f"its rows index up to node {best} but its header says "
                f"{strings} x {per} = {strings * per}")
    return (strings, max(1, strands), per), note


def string_layout(model, warnings=None):
    """How a model's nodes are spread over physical outputs.

    ``{"strings", "nodesPerString", "nodes", "outputs", "parm3", "source"}``.

    For the types in ``DISPLAY_AS_PARMS`` this is xLights' parm pair under the
    model dialog's labels: ``parm1`` strings of ``parm2`` nodes each, **one
    string per output**. So a three-string model takes three consecutive outputs
    of ``parm2`` pixels each, and its total is ``parm1 x parm2``. Getting the
    per-output number wrong here is the expensive kind of wrong: a port set to
    the model's *total* would send three times the channels the strip has.

    A Custom model carries the same three numbers in its own text header, which
    is authoritative for it. Anything else — a type with no node rule, or a
    Custom model whose text could not be read — falls back to the parm pair so a
    number is still produced, with the reason in `warnings` (see `node_count`).

    ``parm3`` is **not** interpreted. Its meaning differs between model types
    (for some it groups strands onto one output, for others it does not at all),
    and the operator's file has it at 1 for every model; rather than guess, a
    ``parm3`` outside {0, 1} is reported as a warning that describes how this
    importer read the file, so the operator can check the port list against
    xLights before accepting it.
    """
    name = model.get("name") or "?"
    display = str(model.get("displayAs") or "").strip()
    key = display.lower()
    source = "parms"
    nodes_per_string = _int(model.get("parm2"), 0)
    strings = _int(model.get("parm1"), 0)
    nodes = strings * nodes_per_string
    if key == "custom":
        header, note = _custom_header(model.get("customModel"))
        if header is not None:
            strings, _strands, nodes_per_string = header
            nodes = strings * nodes_per_string
            source = "custom"
            if note:
                _warn(warnings, f"'{name}': {note} — its header was taken as "
                                f"the size of the model")
        else:
            _warn(warnings,
                  f"'{name}' is a Custom model and its CustomModel text could "
                  f"not be read ({note}); its node count is parm1 x parm2")
    elif key not in [d.lower() for d in DISPLAY_AS_PARMS]:
        _warn(warnings,
              f"'{name}' has model type '{display or '(none)'}', which SlyLED "
              f"has no node rule for — read as parm1 x parm2 ({_PARMS_DOC})")
    parm3 = _int(model.get("parm3"), 0)
    if parm3 not in (0, 1):
        _warn(warnings,
              f"'{name}' declares parm3={parm3}, which changes how xLights "
              f"groups a model's strands onto outputs for some model types — "
              f"SlyLED read this file as one output per string "
              f"({strings} of {nodes_per_string} node(s))")
    return {"strings": max(0, strings), "nodesPerString": max(0, nodes_per_string),
            "nodes": max(0, nodes), "outputs": max(0, strings),
            "parm3": parm3, "source": source}


def node_count(model, warnings=None):
    """How many nodes `model` has, warning where the reading is an assumption.

    ``parm1 x parm2`` for the model types in ``DISPLAY_AS_PARMS``; a Custom model
    is read from its ``CustomModel`` text; **any other type still takes
    ``parm1 x parm2`` but says so in `warnings`**. That is the honest shape: the
    arithmetic is right for most of xLights' models and there is no second rule
    to fall back on, but an operator whose Icicles model comes out at the wrong
    node count needs to know which number the importer guessed rather than
    discover it as a dark strip.

    A Custom model whose text cannot be read falls back the same way, with the
    reason. The per-output split is `string_layout`'s job.
    """
    return string_layout(model, warnings)["nodes"]


def channels_per_node(string_type, warnings=None):
    """Channels per node: RGB 3, RGBW 4, single colour 1.

    Colour *order* variants are as wide as the order they permute: ``GRB Nodes``
    is three channels per node in a different sense order, so any permutation of
    RGB is 3 and of RGBW is 4. Treating ``GRB`` as unknown would warn on most
    real shows and teach the operator to ignore the warning.

    AC/dumb-string models are not modelled: they have no nodes, and their
    ``StringType`` (usually empty) falls through to the 3-channel default with a
    warning.
    """
    key = " ".join(str(string_type or "").split()).lower()
    if key in STRING_TYPE_CHANNELS:
        return STRING_TYPE_CHANNELS[key]
    head = key.replace(" nodes", "").replace(" dumb", "").strip()
    if len(head) in (3, 4) and len(set(head)) == len(head) \
            and set(head) == set("rgbw"[:len(head)]):
        return 4 if len(head) == 4 else 3
    _warn(warnings,
          f"string type '{string_type or '(none)'}' is not one SlyLED knows — "
          f"assuming 3 channels per node (RGB)")
    return 3


# ── StartChannel ─────────────────────────────────────────────────────────────


def _index_to_position(index, controller):
    """A 0-based channel index in a controller's space -> (universe, channel).

    ``index`` walks the controller's universes in file order, each contributing
    its own channel count, which is what makes ``!Controller:N`` and a bare
    absolute channel the same arithmetic with a different starting point.
    """
    if controller is not None:
        walk = [(r["universe"], r.get("channels") or 510)
                for r in controller.get("universes") or [] if r.get("universe")]
    else:
        walk = []
    if not walk:
        raise XlightsImportError("no universes to resolve a channel against")
    if index < 0:
        raise XlightsImportError(f"channel index {index} is negative")
    remaining = index
    for universe, channels in walk:
        channels = max(1, channels)
        if remaining < channels:
            return universe, remaining + 1
        remaining -= channels
    raise XlightsImportError(
        f"channel {index + 1} is past the last universe of this controller"
        if controller else f"channel {index + 1} is past the last universe")


def _index_within(universe, channel, controller):
    """The 0-based index of `universe`:`channel` in a controller, or ``None``.

    ``None`` means the universe is not one of that controller's rows — the
    position is still absolute and can be reported, but a chain offset from it
    has no channel space to count within, so the chain resolver refuses rather
    than counting from an arbitrary zero.
    """
    if controller is None:
        return None
    index = 0
    for row in controller.get("universes") or []:
        if row.get("universe") == universe:
            return index + channel - 1
        index += max(1, row.get("channels") or 510)
    return None


def resolve_start_channel(expr, layout, controller=None, _seen=None):
    """An xLights ``StartChannel`` expression as a position on the wire.

    Returns::

        {"kind": "...", "controller": name|None, "universe": 1, "channel": 1,
         "index": 0, "text": "!Ethernet_:1"}

    ``universe``/``channel`` are the absolute position; ``index`` is the 0-based
    channel within the resolved controller's block, which is what a chain offset
    is arithmetic on.

    Forms, exactly as xLights writes them:

    - ``!Controller:N`` — channel N of that controller's block, 1-based, so
      ``!Ethernet_:1`` is the controller's first channel. The operator's model
      uses this form, and its ``:1`` resolves to universe 1 channel 1.
    - ``#Universe:Channel`` — absolute, by universe number.
    - ``#ip:Universe:Channel`` — the same, naming the controller by address. The
      address is looked up for the controller *name*; the position is absolute
      either way, so an address that matches nothing still resolves, with
      ``controller`` left None.
    - a bare integer — an absolute channel in the show's channel space, which
      walks the model's own controller's universes when it has one and every
      controller in file order otherwise.
    - ``>Model:N`` and ``@Model:N`` — relative to another model's *end* (``>``)
      or *start* (``@``), 1-based: ``>M:1`` is the channel after M's last,
      ``@M:1`` is M's first. Chains may nest.

    *Derived, not captured*: the ``>``/``@`` offsets have no captured bytes
    behind them in the operator's folder. They follow xLights' documented
    reading, and a chain that cannot be resolved raises like any other
    unresolvable reference, so a wrong offset is a visible failure.

    Raises `XlightsImportError` for an empty or unparseable expression, an
    unknown controller, an unknown model in a chain, and for a chain that comes
    back to a model it is already resolving.
    """
    text = str(expr or "").strip()
    if not text:
        raise XlightsImportError("no start channel")
    seen = set() if _seen is None else _seen

    def _by_name(name):
        want = name.strip().lower()
        for ctrl in layout.get("controllers") or []:
            if (ctrl.get("name") or "").lower() == want:
                return ctrl
        raise XlightsImportError(f"no controller named '{name.strip()}'")

    def _by_ip(addr):
        want = addr.strip()
        for ctrl in layout.get("controllers") or []:
            if (ctrl.get("ip") or "").strip() == want:
                return ctrl
        return None

    def _resolve_model(name):
        model = (layout.get("byName") or {}).get(name)
        if model is None:
            raise XlightsImportError(f"no model named '{name}'")
        if name in seen:
            raise XlightsImportError(
                f"start channel of '{name}' chains back to itself")
        seen.add(name)
        ctrl = None
        if model.get("controller"):
            try:
                ctrl = _by_name(model["controller"])
            except XlightsImportError:
                ctrl = None
        pos = resolve_start_channel(model.get("startChannel"), layout,
                                    controller=ctrl, _seen=seen)
        nodes = node_count(model)
        channels = nodes * channels_per_node(model.get("stringType"))
        return model, ctrl, pos, channels

    # `!Controller:N`
    if text.startswith("!"):
        name, _, rest = text[1:].partition(":")
        if not rest:
            raise XlightsImportError(f"'{text}' names no channel")
        ctrl = _by_name(name)
        n = _int(rest, 0)
        if n < 1:
            raise XlightsImportError(f"'{text}' names a channel below 1")
        universe, channel = _index_to_position(n - 1, ctrl)
        return {"kind": "controller", "controller": ctrl.get("name"),
                "universe": universe, "channel": channel, "index": n - 1,
                "text": text}

    # `#Universe:Channel` / `#ip:Universe:Channel`
    if text.startswith("#"):
        parts = text[1:].split(":")
        if len(parts) == 2:
            ctrl, kind = None, "universe"
            universe, channel = _int(parts[0], 0), _int(parts[1], 0)
        elif len(parts) == 3:
            ctrl, kind = _by_ip(parts[0]), "ip-universe"
            universe, channel = _int(parts[1], 0), _int(parts[2], 0)
        else:
            raise XlightsImportError(f"'{text}' is not #universe:channel")
        if universe < 1 or channel < 1:
            raise XlightsImportError(f"'{text}' is not a valid position")
        # An address that matches nothing still gives a perfectly good absolute
        # position — the universe and channel are the whole answer — so this
        # resolves with the controller left unnamed rather than refusing a file
        # whose controller list is stale, which is exactly the operator's case.
        return {"kind": kind, "controller": (ctrl or {}).get("name"),
                "universe": universe, "channel": channel,
                "index": _index_within(universe, channel, ctrl or controller),
                "text": text}

    # `>Model:N` / `@Model:N`
    if text[0] in ">@":
        name, _, rest = text[1:].rpartition(":")
        if not name:
            raise XlightsImportError(f"'{text}' names no model")
        offset = _int(rest, 0)
        if offset < 1:
            raise XlightsImportError(f"'{text}' needs an offset of 1 or more")
        _, ctrl, pos, channels = _resolve_model(name)
        if pos.get("index") is None:
            raise XlightsImportError(
                f"cannot chain off '{name}' — its universe {pos['universe']} is "
                f"not one of its controller's universes")
        base = pos["index"] + channels if text[0] == ">" else pos["index"]
        index = base + (offset - 1)
        universe, channel = _index_to_position(index, ctrl)
        return {"kind": "chain-after" if text[0] == ">" else "chain-at",
                "controller": (ctrl or {}).get("name"),
                "universe": universe, "channel": channel, "index": index,
                "text": text, "chainTo": name}

    # A bare absolute channel.
    n = _int(text, 0)
    if n < 1:
        raise XlightsImportError(f"'{text}' is not a channel or a known form")
    walk = controller
    if walk is None:
        # No controller on the model: this is xLights' *show-wide* channel
        # space, which walks every controller's universes in file order.
        walk = {"universes": [row for c in layout.get("controllers") or []
                              for row in c.get("universes") or []]}
    universe, channel = _index_to_position(n - 1, walk)
    return {"kind": "absolute", "controller": (controller or {}).get("name"),
            "universe": universe, "channel": channel, "index": n - 1,
            "text": text}


# ── The proposal ─────────────────────────────────────────────────────────────


def _pick_controller(layout, controller):
    """The controller to import: a dict, a name, an index, or ``None``.

    ``None`` means "the obvious one": a single HinksPix, or a single controller
    of any kind. Two HinksPix controllers are a real possibility on a larger
    show, and picking one of them silently would be a coin flip over which house
    the port table describes, so it raises instead and the caller offers the
    choice.
    """
    controllers = layout.get("controllers") or []
    if isinstance(controller, dict):
        return controller
    if isinstance(controller, int) and not isinstance(controller, bool):
        if not 0 <= controller < len(controllers):
            raise XlightsImportError(f"no controller at index {controller}")
        return controllers[controller]
    if controller:
        want = str(controller).strip().lower()
        for ctrl in controllers:
            if (ctrl.get("name") or "").lower() == want:
                return ctrl
        raise XlightsImportError(f"no controller named '{controller}'")
    hinks = [c for c in controllers
             if (c.get("vendor") or "").lower().startswith("hinks")]
    pool = hinks or controllers
    if len(pool) == 1:
        return pool[0]
    names = ", ".join(c.get("name") or "?" for c in pool)
    raise XlightsImportError(
        f"{len(pool)} controllers to choose from ({names}) — name the one to "
        f"import")


def propose(layout, controller=None, caps=None, warnings=None):
    """A proposed SlyLED config for one xLights controller.

    ``layout`` is what `parse_show` returns; ``controller`` names one (see
    `_pick_controller`); ``caps`` is a `hinkspix_config.Capabilities` when the
    caller has a target unit, so ports beyond the model's output count are
    reported here rather than at the accept.

    Returns::

        {"controller": {...}, "protocol": "e131",
         "hinks": {"baseUniverse": 1, "protocol": "e131",
                   "defaults": {"brightness": 100, "gamma": 1},
                   "ports": [{"port": 17, "leds": 200, "mm": 3334, ...}]},
         "fixtures": [{"name": "Single Line",
                       "strings": [{"port": 17, "leds": 200, "mm": 3334}],
                       "model": {...}}],
         "models": [{"name": ..., "accepted": True, "problems": [], ...}],
         "universes": {"base": 1, "count": 2},
         "warnings": [...]}

    Nothing is stored and no socket is opened: this is a description of a config,
    and the accept route is what persists it.

    ``models[]`` is the per-row view the operator accepts row by row. A row with
    an **error**-level ``problems`` entry comes back ``accepted: False``, because
    a row the port table cannot hold would otherwise be applied by default and
    rejected as a whole — the operator would have to find the one bad row by
    hand. Warnings never un-accept a row.

    Ports beyond the target model's output count, a protocol the target cannot
    run and a controller with no universes are all warnings here and errors at
    the accept, which is the same ordering as a hand-typed edit: the editor lets
    you type anything, and saving is what checks it.
    """
    warnings = [] if warnings is None else warnings
    ctrl = _pick_controller(layout, controller)
    models = layout.get("models") or []
    name = ctrl.get("name") or "?"
    max_port = getattr(caps, "max_pixel_port", None) or hb.MAX_PORTS

    # ── The controller itself ────────────────────────────────────────────────
    proto = ctrl.get("protocol") or ""
    if not proto:
        _warn(warnings,
              f"controller '{name}' declares protocol "
              f"'{ctrl.get('protocolRaw') or '(none)'}', which SlyLED does not "
              f"know — the port table is proposed as E131")
        proto = "e131"
    if caps is not None and proto not in caps.input_protocols:
        _warn(warnings,
              f"a {caps.name} does not offer {proto} — the accept will be "
              f"refused until the controller itself is set to one of "
              f"{'/'.join(caps.input_protocols)}")

    if not ctrl.get("fullControl"):
        _warn(warnings,
              f"xLights is not in full control of '{name}' "
              f"(FullxLightsControl is off), so the universes below are what "
              f"xLights would use rather than what the controller is running")

    span = universe_span(ctrl)
    if span is None:
        base = 1
        _warn(warnings,
              f"controller '{name}' has no network universes in xLights; the "
              f"block is proposed from universe 1")
    else:
        base = span[0]
        if span[1] - span[0] + 1 != ctrl.get("universeCount"):
            _warn(warnings,
                  f"'{name}' uses universes {span[0]}..{span[1]} with "
                  f"{ctrl.get('universeCount')} row(s) — SlyLED lays universes "
                  f"out contiguously from its base, so the pages in between are "
                  f"not reproduced")
        widths = {r.get("channels") for r in ctrl.get("universes") or []}
        if len(widths) > 1:
            _warn(warnings,
                  f"'{name}' mixes universe sizes ({sorted(widths)}) — channel "
                  f"chains are resolved against the widest")

    # ── Which models are this controller's ───────────────────────────────────
    mine = [m for m in models
            if (m.get("controller") or "").lower() == name.lower()]
    others = len(models) - len(mine)
    if others:
        _warn(warnings,
              f"{others} model(s) belong to other controllers and are not part "
              f"of this import")
    if not mine:
        _warn(warnings, f"no models are bound to '{name}' in xLights")

    # ── Rows ─────────────────────────────────────────────────────────────────
    ports, fixtures, rows = [], [], []
    taken = {}

    def _problem(row, level, text):
        row["problems"].append({"level": level, "text": text})
        if level == "error":
            row["accepted"] = False

    for model in mine:
        mname = model.get("name") or "(unnamed)"
        row = {"name": mname, "displayAs": model.get("displayAs"),
               "stringType": model.get("stringType"), "port": None,
               "ports": [], "nodes": None, "nodesPerString": None,
               "channels": None, "strings": 0,
               "startChannel": model.get("startChannel"), "startKind": None,
               "universe": None, "channel": None,
               "accepted": True, "problems": []}
        rows.append(row)

        conn = model.get("conn") or {}
        shape = string_layout(model, warnings)
        nodes = shape["nodes"]
        per_node = channels_per_node(model.get("stringType"), warnings)
        row["nodes"] = nodes
        row["nodesPerString"] = shape["nodesPerString"]
        row["channels"] = nodes * per_node
        strings = shape["outputs"]
        row["strings"] = strings
        if strings <= 0:
            _problem(row, "error",
                     "the model declares no strings in xLights — there is no "
                     "output to bind it to")
        port = conn.get("port")
        if port is None:
            _problem(row, "error",
                     "the model has no ControllerConnection Port in xLights — "
                     "there is no output to bind it to")
        else:
            row["port"] = port
        if nodes <= 0 or shape["nodesPerString"] <= 0:
            _problem(row, "error",
                     f"parm1 x parm2 is {nodes} node(s) — nothing to drive")
        if not model.get("controller"):
            _problem(row, "error", "the model names no controller")

        try:
            pos = resolve_start_channel(model.get("startChannel"), layout,
                                        controller=ctrl)
            row["universe"] = pos["universe"]
            row["channel"] = pos["channel"]
            row["startKind"] = pos["kind"]
        except XlightsImportError as exc:
            row["startKind"] = None
            _problem(row, "error", f"its start channel: {exc}")

        # The port's pixel protocol, as a code the controller has. A model can
        # name a protocol the bridge has no code for (`ws2812x`, or a strip type
        # xLights knows and the controller does not); proposing it verbatim would
        # store a port the upload cannot encode, so it is proposed as ws2811 with
        # a warning naming what was asked for.
        pixel_proto = str(conn.get("protocol") or "").strip()
        if pixel_proto and pixel_proto.lower() not in hb.PIXEL_PROTOCOLS:
            _warn(warnings,
                  f"'{mname}' drives {pixel_proto} output, which SlyLED does not "
                  f"have a code for — the port is proposed as ws2811")
            pixel_proto = "ws2811"
        if conn.get("smartRemote"):
            _warn(warnings,
                  f"'{mname}' declares {conn['smartRemote']} smart receiver(s); "
                  f"SlyLED needs the receiver type (4-port / 16-port / 16AC) "
                  f"set on the port itself, which xLights does not carry here")

        if row["port"] is None or nodes <= 0 or strings <= 0 \
                or shape["nodesPerString"] <= 0:
            continue

        # A model with `parm1` strings takes that many consecutive outputs
        # starting at its Port, one string each — the xLights convention the
        # port table has to reproduce, because the controller addresses pixels
        # per output and each output drives exactly one string.
        colour = conn.get("colorOrder") or hc.color_order_for_type(
            pixel_proto or conn.get("protocol")) or "RGB"
        string_leds = shape["nodesPerString"]
        led_mm = int(round(string_leds * MM_PER_PIXEL_DEFAULT))
        row_ports, strings_out = [], []
        for offset in range(strings):
            num = row["port"] + offset
            row_ports.append(num)
            strings_out.append({"port": num, "leds": string_leds, "mm": led_mm})
            if num > max_port:
                _problem(row, "error",
                         f"output {num} is past {max_port}, the last output on "
                         f"a {getattr(caps, 'name', 'HinksPix')}")
                continue
            if num in taken:
                _problem(row, "error",
                         f"output {num} is already taken by "
                         f"'{taken[num]}' in this import")
                continue
            taken[num] = mname
            # A model's own ControllerConnection value wins where it has one
            # (an override on that model), else the controller's default, which
            # xLights applies to every model under full control. Filling both in
            # means the port row is explicit and the PUT's own 100/1 defaults
            # never quietly overwrite a controller set to something else.
            ports.append({
                "port": num, "leds": string_leds, "mm": led_mm,
                "protocol": pixel_proto or "ws2811",
                "colorOrder": colour,
                "direction": 1 if conn.get("reverse") else 0,
                "startNulls": _int(conn.get("nullNodes"), 0),
                "brightness": conn.get("brightness")
                              or ctrl.get("defaultBrightness") or 100,
                "gamma": conn.get("gamma") or ctrl.get("defaultGamma") or 1,
                "enabled": True,
            })
        row["ports"] = row_ports
        if row["accepted"]:
            fixtures.append({"name": mname, "model": model,
                             "strings": strings_out})
        else:
            # The row's ports are withdrawn with it: a port table that keeps the
            # ports of a model the operator did not accept would upload outputs
            # driving nothing.
            for num in row_ports:
                if taken.get(num) == mname:
                    taken.pop(num)
                    ports = [p for p in ports if p["port"] != num]

    if not ports:
        _warn(warnings, "nothing to import — no accepted model has an output")

    hinks = {"baseUniverse": base, "protocol": proto, "ports": ports}
    if ctrl.get("fullControl"):
        # Only when xLights' values are the values it enforces: with full
        # control off, these are xLights' opinion of what the controller should
        # be, not what it is, and pushing them would overwrite the controller's
        # own setting with a number nobody chose.
        hinks["defaults"] = {"brightness": ctrl.get("defaultBrightness") or 100,
                             "gamma": ctrl.get("defaultGamma") or 1}

    return {
        "controller": {"name": name, "ip": ctrl.get("ip"),
                       "vendor": ctrl.get("vendor"),
                       "model": ctrl.get("model"),
                       "description": ctrl.get("description"),
                       "protocol": proto, "fullControl": ctrl.get("fullControl"),
                       "universes": ctrl.get("universes") or []},
        "protocol": proto,
        "hinks": hinks,
        "fixtures": fixtures,
        "models": rows,
        "universes": {"base": base,
                      "count": ctrl.get("universeCount") or 0,
                      "channels": (span[2] if span else None)},
        "warnings": warnings,
    }
