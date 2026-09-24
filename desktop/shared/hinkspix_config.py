"""hinkspix_config.py — what a HinksPix PRO should hold, and how to say it (#943).

``hinkspix_bridge.py`` knows how to put a request on the wire.
``pixel_output.py`` knows where every pixel lives. This module is the bridge
between them: it turns SlyLED's port model into the controller's own tables,
compares that against what the controller currently holds, and emits the exact
ordered requests an upload sends.

It is pure — no Flask, no sockets, no global state — so the whole command
sequence can be asserted byte-for-byte in a test. That matters more here than
usual: the previous implementation POSTed instead of GETting, wrote the
universe table 0-based, sent every port a start channel of 1, and never
rebooted (#943). None of that was visible to any test that used our own
transport as its reference.

The ordering and the field layouts are xLights' ``HinksPix::SetOutputs``
(``HinksPix.cpp:1201-1506``). Where this module departs from xLights it says so
in a comment; there is no other source of truth for this protocol.
"""

import json
import logging
import time

import hinkspix_bridge as hb

log = logging.getLogger("slyled.hinkspix")

# Bumped when the *shape* of a stored backup changes, so a restore can refuse a
# snapshot it does not understand rather than replaying half of one.
BACKUP_VERSION = 1

# The row past the end of the table. xLights walks a fixed 6 slots per call and
# emits this for any index beyond MaxU, so the final block is always full.
UNIVERSE_ZERO_ROW = "0,0,0,0,0,0"

# ── What each model can do (#946) ────────────────────────────────────────────
#
# `hinkspix.xcontroller` (xLights `resources/controllers/hinkspix.xcontroller`)
# is the spec: three variants, and every number below is read off it rather than
# inferred from the protocol. It is the reference client's own statement of what
# it will offer an operator, so a config we allow and xLights refuses is our bug
# (#943 B16), and a port count we assume rather than look up is how the PRO 80
# became unconfigurable (#943 B21).
#
# `PRO_HARDWARE_MODES` existed before this table and said the same thing about
# DDP with a comment instead of a source; it is kept as the name the rest of the
# module already used.

# Four outputs per smart-receiver bank — the `Port4` dimension of SCONFIG, and
# the reason the receiver list is per *bank of four ports* rather than per port.
SMART_BANK_SIZE = 4

# How the receiver id reads to an operator. The controller wants 0..15; xLights
# shows the same values as A..P, and nothing on the wire is a letter.
SMART_ID_LETTERS = "ABCDEFGHIJKLMNOP"


def smart_id_label(idx):
    """``0`` -> ``"A"``. The operator-facing name for a smart-receiver id."""
    idx = int(idx)
    return SMART_ID_LETTERS[idx] if 0 <= idx < len(SMART_ID_LETTERS) else str(idx)


def smart_id_from_label(label):
    """``"A"`` / ``"a"`` / ``"0"`` -> int. Accepts either spelling on input."""
    text = str(label or "").strip().upper()
    if text in SMART_ID_LETTERS:
        return SMART_ID_LETTERS.index(text)
    try:
        return int(text)
    except (TypeError, ValueError):
        return -1


class Capabilities:
    """One controller model's limits and allowed values.

    ``boards`` and ``max_pixels_per_port`` are derived rather than stored: the
    port count and the channel cap are the two numbers in the table, and every
    other reading of them (how many boards are addressable, how many pixels fit
    in the cap) is arithmetic on those.
    """

    __slots__ = ("key", "name", "pixel_protocols", "input_protocols",
                 "max_input_universes", "max_pixel_port",
                 "max_pixel_port_channels", "smart_remote_types")

    def __init__(self, key, name, pixel_protocols, input_protocols,
                 max_input_universes, max_pixel_port, max_pixel_port_channels,
                 smart_remote_types=()):
        self.key = key
        self.name = name
        self.pixel_protocols = tuple(pixel_protocols)
        self.input_protocols = tuple(input_protocols)
        self.max_input_universes = int(max_input_universes)
        self.max_pixel_port = int(max_pixel_port)
        self.max_pixel_port_channels = int(max_pixel_port_channels)
        self.smart_remote_types = tuple(smart_remote_types)

    @property
    def boards(self):
        """How many expansion boards this model addresses, BD1..BDn."""
        return self.max_pixel_port // hb.PORTS_PER_BOARD

    def max_pixels(self, color_order=None):
        """Pixels one port can hold, at this node width."""
        return self.max_pixel_port_channels // hb.channels_per_pixel(color_order)

    def to_json(self):
        return {"key": self.key, "name": self.name,
                "pixelProtocols": list(self.pixel_protocols),
                "inputProtocols": list(self.input_protocols),
                "maxInputUniverses": self.max_input_universes,
                "maxPixelPort": self.max_pixel_port,
                "maxPixelPortChannels": self.max_pixel_port_channels,
                "boards": self.boards,
                "smartRemoteTypes": list(self.smart_remote_types)}


# Verbatim from the four `<Controller>` blocks. DDP is absent from PRO V1/V2
# even though `HinksPix.cpp:378-382` still accepts the mode: offering a mode the
# controller cannot serve produces a config that is written, accepted and inert
# (#943 B14). EasyLights is a *pixel* controller with one board and no smart
# remotes, and it is the only model offered the longer pixel-protocol list.
CAPABILITIES = {
    "easylights": Capabilities(
        "easylights", "EasyLights Pix16",
        pixel_protocols=("ws2811", "ws2801", "tls3001", "apa102"),
        input_protocols=("e131", "artnet", "ddp"),
        max_input_universes=65, max_pixel_port=16, max_pixel_port_channels=2040,
        smart_remote_types=()),
    "pro_v12": Capabilities(
        "pro_v12", "PRO V1/V2",
        pixel_protocols=("ws2811",),
        input_protocols=("e131", "artnet"),
        max_input_universes=402, max_pixel_port=48, max_pixel_port_channels=2040,
        smart_remote_types=(hb.SMART_REMOTE_4, hb.SMART_REMOTE_16,
                            hb.SMART_REMOTE_16AC)),
    "pro_v3": Capabilities(
        "pro_v3", "PRO V3",
        pixel_protocols=("ws2811",),
        input_protocols=("e131", "artnet", "ddp"),
        max_input_universes=684, max_pixel_port=80, max_pixel_port_channels=3072,
        smart_remote_types=(hb.SMART_REMOTE_4, hb.SMART_REMOTE_16,
                            hb.SMART_REMOTE_16AC)),
}

# Kept as the name the module already used for "the modes a PRO V1/V2 serves".
PRO_HARDWARE_MODES = CAPABILITIES["pro_v12"].input_protocols


def caps_key(hinks):
    """Which row of :data:`CAPABILITIES` this controller is.

    ``BoardInfo`` says it two ways — ``Controller`` marks EasyLights, and
    ``Type`` is ``"8"`` on a PRO 80 (= hardware V3, `HinksPix.cpp:378-382`).
    Both are stored by the probe. A child probed before #946 has only
    ``hardwareV3``, and the universe limit is a third witness to fall back on
    (65/402/684 are distinct), so an un-reprobed controller is still read
    correctly rather than assumed to be a PRO V1/V2.
    """
    hinks = hinks or {}
    controller = str(hinks.get("controller") or "").upper()
    if controller == "E" or str(hinks.get("model") or "").lower().startswith("easylights"):
        return "easylights"
    if bool(hinks.get("hardwareV3")) or str(hinks.get("type") or "") == "8":
        return "pro_v3"
    max_u = int(hinks.get("maxU") or 0)
    if max_u >= CAPABILITIES["pro_v3"].max_input_universes:
        return "pro_v3"
    if 0 < max_u <= CAPABILITIES["easylights"].max_input_universes:
        return "easylights"
    return "pro_v12"


def caps_for(hinks):
    """The :class:`Capabilities` for a stored ``hinks`` dict."""
    return CAPABILITIES[caps_key(hinks)]


def input_protocols_for(hinks):
    """The input protocols *this* controller serves, plus SlyLED's ``sacn``.

    sACN is written to the controller as ``E131`` (it has no sACN mode), so it
    is a SlyLED-side name that maps onto a supported one rather than a fourth
    protocol of its own — it is appended here for the picker, not for the caps
    table, which lists what the controller itself offers. DDP is offered only
    where ``input_protocols`` lists it, which is the whole reason this is
    caps-driven rather than generation-driven (#943 B14, #946).
    """
    caps = caps_for(hinks)
    out = list(caps.input_protocols)
    if "e131" in out and "sacn" not in out:
        # Right after E131, because it *is* E131 on the wire — the two are one
        # mode with two names, and the picker reads better with them adjacent.
        out.insert(out.index("e131") + 1, "sacn")
    return tuple(out)


def input_protocols_supported(hardware_v3=False):
    """``input_protocols_for`` for a caller that only knows the generation."""
    return input_protocols_for({"hardwareV3": hardware_v3})


def board_of(port):
    """The expansion board (BD1..) a controller-absolute port number sits on."""
    return (int(port) - 1) // hb.PORTS_PER_BOARD + 1


def board_bank(port):
    """``(board, bank, sub_port)`` for a port — xLights' own decomposition.

    ``HinksPix::CalculateSmartReceivers`` (``HinksPix.cpp:860-867``) splits a
    port three ways: which board, which bank of four outputs on that board, and
    which of those four. SCONFIG is addressed by board and bank, and each
    receiver in it carries one start-pixel value per sub-port.
    """
    port = int(port)
    zero = port - 1
    board = zero // hb.PORTS_PER_BOARD + 1
    on_board = zero % hb.PORTS_PER_BOARD
    return board, on_board // SMART_BANK_SIZE, on_board % SMART_BANK_SIZE


class ConfigError(ValueError):
    """Raised when a child's stored config cannot be expressed as an upload."""


# ── Row types ────────────────────────────────────────────────────────────────

class PortRow:
    """One PCONFIG row: one of a board's 16 pixel outputs.

    Field order and meanings are ``HinksPixOutput::BuildCommand``
    (``HinksPix.cpp:152-158``). ``start``/``end`` are controller-absolute
    channels, not universe-relative ones — ``setControllerChannels``
    (``HinksPix.cpp:166-173``) computes ``end`` as
    ``start + pixels * channels_per_pixel - 1``.
    """

    __slots__ = ("output", "protocol", "start", "pixels", "end", "direction",
                 "color_order", "null_pixels", "brightness", "gamma")

    def __init__(self, output, protocol, start, pixels, end, direction,
                 color_order, null_pixels, brightness, gamma):
        self.output = int(output)
        self.protocol = int(protocol)
        self.start = int(start)
        self.pixels = int(pixels)
        self.end = int(end)
        self.direction = int(direction)
        self.color_order = int(color_order)
        self.null_pixels = int(null_pixels)
        self.brightness = int(brightness)
        self.gamma = int(gamma)

    @property
    def used(self):
        """Whether this output drives pixels.

        Note a *factory* port reads protocol 0 **with** a pixel count, so
        protocol alone is not the test (#943 B10) — pixels is.
        """
        return self.pixels > 0

    def to_v(self):
        return "{},{},{},{},{},{},{},{},{},{}".format(
            self.output, self.protocol, self.start, self.pixels, self.end,
            self.direction, self.color_order, self.null_pixels,
            self.brightness, self.gamma)

    @classmethod
    def unused(cls, output, brightness=100):
        """The row xLights writes for an output that drives nothing.

        ``InitControllerOutputData`` constructs every output as
        ``protocol 0, start 1, pixels 0, end 0`` and only ``UpdatePortData``
        moves a used one (``HinksPix.cpp:282-298``). Writing this for the
        ports we do not drive is what clears stale rows left on the board by an
        earlier configuration (#943 B7).
        """
        return cls(output, 0, 1, 0, 0, 0, 0, 0, brightness, 1)

    def to_json(self):
        return {"output": self.output, "protocol": self.protocol,
                "start": self.start, "pixels": self.pixels, "end": self.end,
                "direction": self.direction, "colorOrder": self.color_order,
                # The wire field is the *start* null-pixel count (#946); the
                # key is named for what it is rather than for the position it
                # happens to occupy in the row.
                "startNulls": self.null_pixels, "brightness": self.brightness,
                "gamma": self.gamma, "used": self.used}


class UniverseRow:
    """One row of the input-universe table.

    ``HinksPixInputUniverse::BuildCommand`` (``HinksPix.cpp:266-272``):
    ``index,universe,numOfChan,1,hinksStart,hinksEnd``. The literal ``1`` is a
    reserved field xLights writes constant. ``index`` is **1-based** — the
    controller's own saved table reads ``1,1,300,1,1,300,2,2,...`` (#943 B5).
    """

    __slots__ = ("index", "universe", "channels", "start", "end")

    def __init__(self, index, universe, channels, start, end):
        self.index = int(index)
        self.universe = int(universe)
        self.channels = int(channels)
        self.start = int(start)
        self.end = int(end)

    def to_v(self):
        return "{},{},{},1,{},{}".format(self.index, self.universe,
                                         self.channels, self.start, self.end)

    @classmethod
    def filler(cls, index):
        """A table slot with no data: ``index,index,0,1,0,0``."""
        return cls(index, index, 0, 0, 0)

    @property
    def empty(self):
        return self.channels == 0

    def to_json(self):
        return {"index": self.index, "universe": self.universe,
                "channels": self.channels, "start": self.start, "end": self.end}


class SerialRow:
    """The J3 DMX-512 output bridge — the seven keys of a serial ``DATA_MODE``.

    ``HinksPixSerial`` (``HinksPix.cpp:189-213``). It is a *separate* command
    from the input-mode one, sent after PCONFIG, and carries no ``MODE`` key:
    merging the two loses the input mode (xLights sets it first, on its own)
    and drops five of the seven fields whenever the bridge is off (#943 B9).
    """

    __slots__ = ("dmx_active", "dmx_universe", "dmx_start", "dmx_channels",
                 "ddp_active", "ddp_start", "ddp_channels")

    def __init__(self, dmx_active, dmx_universe, dmx_start, dmx_channels,
                 ddp_active, ddp_start, ddp_channels):
        self.dmx_active = int(dmx_active)
        self.dmx_universe = int(dmx_universe)
        self.dmx_start = int(dmx_start)
        self.dmx_channels = int(dmx_channels)
        self.ddp_active = int(ddp_active)
        self.ddp_start = int(ddp_start)
        self.ddp_channels = int(ddp_channels)

    def to_payload(self):
        """Ordered exactly as ``HinksPixSerial::BuildCommand`` builds it."""
        return {
            "CMD": "DATA_MODE",
            "DMX_ACTIVE": self.dmx_active,
            "DMX_UNIV": self.dmx_universe,
            "DMX_START": self.dmx_start,
            "DMX_CHAN_CNT": self.dmx_channels,
            "DDP_DMX_ACTIVE": self.ddp_active,
            "DDP_DMX_START": self.ddp_start,
            "DDP_DMX_CHAN_CNT": self.ddp_channels,
        }

    @classmethod
    def off(cls):
        """Disabled bridge — the field values xLights' defaults produce."""
        return cls(0, 1, 1, 512, 0, 1, 512)

    @classmethod
    def from_read(cls, data_mode):
        """Decode a ``DATA_MODE`` read reply. Missing keys become None."""
        got = hb.parse_serial(data_mode)

        def pick(key, default):
            v = got.get(key)
            return default if v is None else v

        return cls(pick("DMX_ACTIVE", 0), pick("DMX_UNIV", 1),
                   pick("DMX_START", 1), pick("DMX_CHAN_CNT", 512),
                   pick("DDP_DMX_ACTIVE", 0), pick("DDP_DMX_START", 1),
                   pick("DDP_DMX_CHAN_CNT", 512))

    def to_json(self):
        return {"dmxActive": self.dmx_active, "dmxUniverse": self.dmx_universe,
                "dmxStart": self.dmx_start, "dmxChannels": self.dmx_channels,
                "ddpActive": self.ddp_active, "ddpStart": self.ddp_start,
                "ddpChannels": self.ddp_channels}


# ── Config ───────────────────────────────────────────────────────────────────

class DeviceConfig:
    """The controller's configuration: input mode, universe table, ports, J3.

    One type for both directions. ``intended_config`` builds the one we want to
    write; ``decode_device_config`` builds the one read off the controller; the
    two are compared field by field. Sharing the type is what keeps the
    comparison honest — there is no second notion of "the same config".
    """

    __slots__ = ("mode", "serial", "universes", "ports", "board_ports",
                 "max_universes", "ddp_start", "ddp_channels",
                 "smart_receivers")

    def __init__(self, mode=None, serial=None, universes=None, ports=None,
                 board_ports=None, max_universes=0, ddp_start=1,
                 ddp_channels=0, smart_receivers=None):
        self.mode = mode
        self.serial = serial
        self.universes = universes if universes is not None else {}
        self.ports = ports if ports is not None else {}
        self.board_ports = board_ports if board_ports is not None else {}
        # {board: {bank: [SmartReceiver]}} — the SCONFIG lists (#946). Empty
        # when no output carries a smart receiver, in which case no SCONFIG is
        # sent at all rather than an empty list, which xLights never sends.
        self.smart_receivers = smart_receivers if smart_receivers is not None else {}
        self.max_universes = int(max_universes or 0)
        # The DDP *input* range — one channel span in place of the whole
        # universe table. Not the J3 bridge's DDP fields (`serial.ddp_*`),
        # which describe a different output.
        self.ddp_start = int(ddp_start or 1)
        self.ddp_channels = int(ddp_channels or 0)

    @property
    def used_universes(self):
        return [u for u in self.universes.values() if not u.empty]

    def to_json(self):
        return {
            "mode": self.mode,
            "serial": self.serial.to_json() if self.serial else None,
            "universes": [self.universes[i].to_json()
                          for i in sorted(self.universes)],
            "boardPorts": {str(b): [r.to_json() for r in rows]
                           for b, rows in sorted(self.board_ports.items())},
            "ports": {str(o): self.ports[o].to_json()
                      for o in sorted(self.ports)},
            "smartReceivers": {
                str(b): {str(bank): [r.to_json() for r in recs]
                         for bank, recs in sorted(banks.items())}
                for b, banks in sorted(self.smart_receivers.items())},
            "maxUniverses": self.max_universes,
        }


def normalise_boards(raw):
    """Board names -> ``{board_number: type_name}``, sorted keys.

    Accepts the ``{"BD1": "Long_Range"}`` shape ``probe()`` stores, a plain
    ``{1: "Long_Range"}``, and a raw ``XLights_BoardInfo.cgi`` reply whose
    values are still the single-letter codes — so a caller holding any of the
    three is not forced to convert first. Anything that is not a board of this
    controller is dropped.
    """
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(k, int):
            num = k
        else:
            name = str(k).upper()
            if not name.startswith("BD") or not name[2:].isdigit():
                continue
            num = int(name[2:])
        if 1 <= num <= hb.MAX_BOARDS:
            out[num] = hb.EXPANSION_TYPES.get(v, v)
    return out


def _pixel_boards(boards):
    """The ``Local_SPI``/``Long_Range`` subset, ascending.

    A board is written only if it is one of those two — ``Not_Present`` and
    ``Local_AC`` get no PCONFIG at all (``HinksPix.cpp:670-675``, #943 B7).
    """
    return sorted(b for b, t in boards.items() if t in hb.PIXEL_BOARD_TYPES)


def present_boards(child):
    """``{board_number: type_name}`` for the expansion boards that are fitted."""
    return normalise_boards(((child or {}).get("hinks") or {}).get("boards"))


def pixel_boards(child):
    """Boards of this child that carry pixel ports, in ascending order."""
    return _pixel_boards(present_boards(child))


def pixel_boards_from_info(board_info):
    """The same, straight off a ``XLights_BoardInfo.cgi`` reply.

    Reading the fitted boards from the reply in hand rather than from the last
    probe is what makes a snapshot safe to take: a board fitted since that
    probe would otherwise be missing from the backup, and the ports on it would
    be the ones an apply overwrote without a way back (#945).
    """
    return _pixel_boards(normalise_boards(board_info))


# ── Building what we want ────────────────────────────────────────────────────

def _start_nulls(cfg):
    """A port's leading unused pixels, off either spelling of the field.

    ``startNulls`` is what the field is — the PCONFIG value comes from
    xLights' ``GetStartNullPixels()``, not from any end-of-string padding
    (#946). ``nullPixels`` is the name it was stored under before that, and a
    config written then must keep working, so it is still read.
    """
    cfg = cfg or {}
    raw = cfg.get("startNulls")
    if raw is None:
        raw = cfg.get("nullPixels")
    return int(raw or 0)


def _port_row(output, cfg, output_map):
    """One port's intended row, or an unused row when we do not drive it."""
    if not cfg or not cfg.get("enabled", True):
        return PortRow.unused(output)
    pixels = int(cfg.get("leds") or 0)
    if pixels <= 0:
        return PortRow.unused(output)
    spans = output_map.spans_for_port(output)
    if not spans:
        # The port is enabled with pixels but the map has no span for it, which
        # means the layout and the port table disagree. Zeroing it silently
        # would leave a port the operator enabled dark with no explanation, so
        # say so and skip it.
        log.warning("HinksPix port %d is enabled with %d px but has no span in "
                    "the universe map — writing it as unused", output, pixels)
        return PortRow.unused(output)

    order = hb.encode_color_order(cfg.get("colorOrder"))
    chpp = hb.channels_per_pixel(order)
    start = int(spans[0]["absStart"])
    return PortRow(
        output=output,
        protocol=hb.encode_protocol(cfg.get("protocol")),
        start=start,
        pixels=pixels,
        end=start + pixels * chpp - 1,
        direction=hb.encode_direction(cfg.get("direction")),
        color_order=order,
        null_pixels=_start_nulls(cfg),
        brightness=hb.encode_brightness(cfg.get("brightness", 100)),
        gamma=hb.encode_gamma(cfg.get("gamma", 1)),
    )


def strings_by_port(child, fixtures):
    """``{port: [string, ...]}`` — the fixture strings driving each output.

    The order is the fixture order, which is the order the pixels arrive in: a
    port's data stream feeds the first fixture on it, then the next, and so on.
    That ordered list is what makes a chain of smart receivers on one output
    expressible (#946); with one fixture per port — the ordinary case — it is a
    single-element list and the receiver is simply the port's.
    """
    cid = (child or {}).get("id")
    out = {}
    for f in fixtures or []:
        if f.get("childId") != cid:
            continue
        for s in f.get("strings") or []:
            port = s.get("port")
            if not isinstance(port, int) or isinstance(port, bool):
                continue
            out.setdefault(port, []).append(s)
    return out


def intended_config(child, output_map, max_universes=0, port_strings=None):
    """The configuration SlyLED wants the controller to hold.

    Port start channels come from the map's ``absStart``, which is the whole
    point: the port table and the universe table both address
    controller-absolute channels, and they must agree with each other and with
    the frame layout ``.hseq``/live streaming write (#943 B6). Previously every
    port was written with start 1, so all 48 ports decoded the same pixels.
    """
    hinks = (child or {}).get("hinks") or {}
    proto = str(hinks.get("protocol") or "e131").lower()
    mode = hb.BOARD_MODES.get(proto)
    if mode is None:
        raise ConfigError(f"unsupported input protocol {proto!r}")

    ports, board_ports = {}, {}
    for board in pixel_boards(child):
        rows = []
        for i in range(hb.PORTS_PER_BOARD):
            output = (board - 1) * hb.PORTS_PER_BOARD + i + 1
            row = _port_row(output, _port_cfg(hinks, output), output_map)
            ports[output] = row
            rows.append(row)
        board_ports[board] = rows

    serial = _serial_row(hinks, output_map)
    # Receivers are collected per Long_Range board only, which is xLights' own
    # gate — the boards come from the probe, so a receiver set on a board that
    # is not fitted produces no SCONFIG and a `smart_on_non_long_range` error
    # rather than a request the controller would refuse (#946).
    receivers = calculate_smart_receivers(
        hinks.get("ports") or [], boards=present_boards(child),
        strings_by_port=port_strings)
    return DeviceConfig(mode=mode, serial=serial,
                        universes={r.index: r for r in
                                   universe_table(output_map.spans,
                                                  max_universes)},
                        ports=ports, board_ports=board_ports,
                        max_universes=max_universes,
                        ddp_start=1, ddp_channels=output_map.total_channels,
                        smart_receivers=receivers)


def _port_cfg(hinks, output):
    for p in hinks.get("ports") or []:
        try:
            if int(p.get("port")) == output:
                return p
        except (TypeError, ValueError):
            continue
    return None


def _serial_row(hinks, output_map):
    """The J3 DMX-out bridge for this child.

    When off, the field values are xLights' constructor defaults rather than
    zeros: the read path parses all seven keys, so a zeroed channel count is a
    different (and invalid) statement from "unused" (#943 B9).
    """
    dmx = hinks.get("dmxOut") or {}
    if not dmx.get("enabled"):
        return SerialRow.off()
    try:
        uni = int(dmx.get("universe"))
    except (TypeError, ValueError):
        raise ConfigError("dmxOut is enabled but no universe is set")
    if uni < 1:
        raise ConfigError("dmxOut is enabled but no universe is set")
    return SerialRow(1, uni, 1, 512, 0, 1, 512)


# ── Smart receivers (#946) ───────────────────────────────────────────────────
#
# A Long Range board does not drive pixels directly. Its 16 outputs are four
# differential cables of four, and what sits at the far end is a *receiver* that
# may itself pass the signal on to more receivers. Each one in that chain has an
# ID (0..15, shown to the operator as A..P) and owns a run of pixels within the
# output's stream, which is what `SCONFIG` tells the controller
# (`HinksPix.cpp:833-858`).
#
# Nothing reads SCONFIG back — xLights has no `GetSmartReceiverData` — so a
# wrong list is only ever found on the hardware. That is why the editor explains
# the rules instead of assuming the operator knows them, and why
# `smart_on_non_long_range` blocks rather than warns: xLights' own
# `CheckSmartReceivers` (`HinksPix.cpp:1948-1963`) refuses the export.

class SmartReceiver:
    """One entry of an SCONFIG list: ``id,type,p1,p2,p3,p4``.

    ``type`` is 0 for a four-port receiver, 1 for the first of a 16-port group
    and 2 for a 16AC — the codes ``CalculateSmartReceivers`` writes
    (``HinksPix.cpp:885-905``). ``start_pixels`` has one entry per sub-port of
    the bank (four), each the pixel within that output's stream at which the
    receiver's run begins. Zero means "this receiver is not on that output".
    """

    __slots__ = ("id", "type", "start_pixels")

    def __init__(self, id, type=0, start_pixels=None):
        self.id = int(id)
        self.type = int(type)
        self.start_pixels = list(start_pixels or [0] * SMART_BANK_SIZE)
        while len(self.start_pixels) < SMART_BANK_SIZE:
            self.start_pixels.append(0)

    def to_v(self):
        return "{},{},{}".format(self.id, self.type,
                                 ",".join(str(p) for p in self.start_pixels))

    def to_json(self):
        return {"id": self.id, "label": smart_id_label(self.id),
                "type": self.type,
                "typeName": SMART_TYPE_NAMES.get(self.type, str(self.type)),
                "startPixels": list(self.start_pixels),
                "onOutputs": [i + 1 for i, p in enumerate(self.start_pixels)
                              if p]}


# The wire codes, by the name the operator sees. `SMART_TYPE_NAMES` is the
# inverse of what `smart_type_code` decides from the type *string*: xLights
# switches on the text (`GetSmartRemoteType()`), not on a code, so the text is
# what is stored and the code is derived.
SMART_TYPE_NAMES = {0: "4-port", 1: "16-port", 2: "16AC"}


def smart_remote_id(cfg):
    """A port/fixture config's smart-receiver id, or ``-1`` when unset.

    Accepts ``"A"``-``"P"`` as well as ``0``-``15``: the letter is what the
    operator reads off the receiver's dial, the number is what goes on the
    wire, and refusing either spelling would be our convenience, not theirs.
    """
    if not cfg:
        return -1
    raw = cfg.get("smartRemote")
    if raw is None or raw == "" or raw is False:
        return -1
    idx = smart_id_from_label(raw)
    if 0 <= idx < hb.SMART_RECEIVERS_PER_BANK:
        return idx
    return -1


def smart_remote_type(cfg):
    """A port/fixture config's smart-receiver type, as the xLights string."""
    name = str((cfg or {}).get("smartRemoteType") or "").strip().lower()
    return name if name in hb.SMART_REMOTE_TYPES else ""


def smart_type_code(cfg):
    """The wire ``type`` code for a config's receiver type.

    ``CalculateSmartReceivers`` tests the type text, not a code: anything
    containing ``16`` that is not the 16AC is a 16-port group (``type 1``), the
    16AC is ``type 2``, and a four-port receiver — the default — is ``type 0``.
    """
    name = smart_remote_type(cfg)
    if "16ac" in name:
        return 2
    if "16" in name:
        return 1
    return 0


def _segments_for_port(port, strings_by_port):
    """The ordered runs of pixels on one output, as receiver configs.

    One segment per fixture string bound to the port, in fixture order — that
    ordered list is exactly the model list ``CalculateSmartReceivers`` walks.
    With no binding to go on, the port is its own single segment, which is the
    ordinary case: one output, one string, one receiver.
    """
    cfg = port or {}
    strings = (strings_by_port or {}).get(int(cfg.get("port") or 0)) or []
    if not strings:
        return [cfg]
    out = []
    for s in strings:
        # A string may carry its own receiver (a chain on one output); without
        # one it belongs to the receiver the port declares.
        merged = dict(cfg)
        for key in ("smartRemote", "smartRemoteType"):
            if s.get(key) is not None:
                merged[key] = s[key]
        merged["leds"] = int(s.get("leds") or 0)
        merged["colorOrder"] = cfg.get("colorOrder")
        out.append(merged)
    return out


def calculate_smart_receivers(ports, boards=None, strings_by_port=None):
    """``{board: {bank: [SmartReceiver]}}`` — a port of `.cpp:860-918`.

    ``boards`` is the fitted-board map. Given it, receivers are collected **only**
    on ``Long_Range`` boards, matching `UploadSmartReceivers`; left unset, every
    board is eligible and it is the caller's job to have rejected the rest
    (`validate` reports `smart_on_non_long_range` for exactly that case).

    ``strings_by_port`` maps a port number to the ordered fixture strings
    driving it. That is what makes a *chain* of receivers on one output
    expressible, which is the shape xLights is written for; without it each port
    contributes its own single run starting at pixel 1.
    """
    # Either spelling of the board map, because both are in circulation: the
    # stored ``{"BD1": "Long_Range"}`` and the number-keyed form the rest of
    # this module works in. A key shape that silently matched no board would
    # collect no receivers at all — the failure mode here is an empty SCONFIG,
    # which the controller accepts and the operator only sees as dark pixels.
    if boards is not None:
        boards = normalise_boards(boards)
    out = {}
    for port in sorted(ports or [], key=lambda p: int(p.get("port") or 0)):
        if not port.get("enabled", True) or int(port.get("leds") or 0) <= 0:
            continue
        pnum = int(port.get("port") or 0)
        if not 1 <= pnum <= hb.MAX_PORTS:
            continue
        board, bank, sub_port = board_bank(pnum)
        if boards is not None and boards.get(board) != "Long_Range":
            # xLights skips a non-Long_Range board outright (`:823-831`) rather
            # than erroring here; the operator-facing complaint is
            # `smart_on_non_long_range`, which `validate` raises.
            continue
        bank_list = out.setdefault(board, {}).setdefault(bank, [])

        prev_id = -1
        start_pixels = 1
        port_channels = 0
        for seg in _segments_for_port(port, strings_by_port):
            rec_id = smart_remote_id(seg)
            if rec_id < 0:
                continue
            code = smart_type_code(seg)
            if prev_id != rec_id:
                found = next((r for r in bank_list if r.id == rec_id), None)
                if found is not None:
                    found.start_pixels[sub_port] = start_pixels
                elif code == 1:
                    # A 16-port receiver is addressed as a group of four ids
                    # starting on a multiple of four; only the id actually in
                    # use is placed, the other three are announced so the
                    # controller knows the group is there (`:885-899`).
                    group = (rec_id // SMART_BANK_SIZE) * SMART_BANK_SIZE
                    lead = SmartReceiver(group, 1)
                    bank_list.extend([lead, SmartReceiver(group + 1),
                                      SmartReceiver(group + 2),
                                      SmartReceiver(group + 3)])
                    lead.start_pixels[sub_port] = start_pixels
                else:
                    rec = SmartReceiver(rec_id, code)
                    if code == 2:
                        # A 16AC counts pixels in its own three-channel terms
                        # rather than in the port's (`:901-905`).
                        rec.start_pixels[sub_port] = (port_channels // 3) + 1
                    else:
                        rec.start_pixels[sub_port] = start_pixels
                    bank_list.append(rec)

            chpp = max(hb.channels_per_pixel(seg.get("colorOrder")), 3)
            chans = int(seg.get("leds") or 0) * chpp
            port_channels += chans
            start_pixels += chans // chpp
            prev_id = rec_id

    return {b: {bnk: recs for bnk, recs in sorted(banks_.items()) if recs}
            for b, banks_ in sorted(out.items()) if any(banks_.values())}


def _sconfig_request(board, bank, receivers):
    """The ``SCONFIG`` request for one bank of one board."""
    lo = (board - 1) * hb.PORTS_PER_BOARD + bank * SMART_BANK_SIZE + 1
    return CgiRequest(
        hb.CGI_POST_DATA,
        data=_dump({"CMD": "SCONFIG", "BOARD": str(board - 1),
                    "Port4": str(bank),
                    "LIST": [{"V": r.to_v()} for r in receivers]}),
        note=f"smart receivers: board {board} (BD{board}) bank {bank} "
             f"(ports {lo}-{lo + SMART_BANK_SIZE - 1}), "
             f"{len(receivers)} receiver(s)")


# ── Tables ───────────────────────────────────────────────────────────────────

def universe_table(spans, max_universes):
    """The full input-universe table as rows: ``MaxU`` rows, 1-based.

    Rows 1..N carry the layout; rows N+1..``max_universes`` are fillers. The
    table is always ``MaxU`` rows even when the layout is smaller, because the
    rows past it are what clears a universe table left behind by an earlier
    configuration — sizing it to the layout leaves the tail intact (#943 B15).

    Returned as a list of :class:`UniverseRow`; ``build_universe_rows`` is the
    same table in wire-string form.
    """
    rows = []
    for i, span in enumerate(spans, start=1):
        rows.append(UniverseRow(i, span["universe"], span["channels"],
                                span["absStart"],
                                span["absStart"] + span["channels"] - 1))
    for i in range(len(rows) + 1, int(max_universes) + 1):
        rows.append(UniverseRow.filler(i))
    return rows


def build_universe_rows(spans, max_universes):
    """The input-universe table as wire strings — see :func:`universe_table`."""
    return [r.to_v() for r in universe_table(spans, max_universes)]


def universe_blocks(rows, per_block=hb.UNIVERSES_PER_BLOCK):
    """Chunk table rows into the blocks the controller accepts.

    The final block is padded to a full ``per_block`` with the all-zero row —
    xLights walks a fixed 6 slots per call and emits ``0,0,0,0,0,0`` for any
    index past ``MaxU`` (``HinksPix.cpp:427-438``).
    """
    blocks = []
    for i in range(0, len(rows), per_block):
        chunk = list(rows[i:i + per_block])
        while len(chunk) < per_block:
            chunk.append(UNIVERSE_ZERO_ROW)
        blocks.append(chunk)
    return blocks


# ── Building the requests ────────────────────────────────────────────────────

class CgiRequest:
    """One HTTP request, exactly as it goes on the wire.

    The preview the operator sees and the request the executor sends are the
    same object, so a dry run cannot drift from the real thing. Nothing here
    is a summary — ``data`` is the literal ``DATA:`` header value, already
    serialised.
    """

    __slots__ = ("kind", "path", "data", "blk", "row", "note")

    def __init__(self, path, data=None, blk=None, row=None, kind="write",
                 note=""):
        self.path = path
        self.data = data
        self.blk = blk
        self.row = row
        self.kind = kind          # "read" | "write" | "reboot"
        self.note = note

    @property
    def method(self):
        # Always GET. There is no POST path to this controller (#943 B1).
        return "GET"

    @property
    def headers(self):
        return hb.cgi_headers(data=self.data, blk=self.blk, row=self.row)

    def to_json(self):
        return {"kind": self.kind, "method": self.method, "path": self.path,
                "headers": self.headers, "note": self.note}


def _dump(obj):
    return json.dumps(obj, separators=(",", ":"))


def build_commands(intended, mcpu=None, hardware_v3=False, read_mode=True):
    """The ordered requests an upload sends — xLights' sequence exactly.

    ``HinksPix::SetOutputs`` (``HinksPix.cpp:1433-1499``):

    1. read the current input mode (the controller is asked, then told anyway —
       xLights comments out the comparison and says "send mode every time");
    2. ``DATA_MODE`` with ``MODE``;
    3. the ``E131`` universe table in blocks of 6, then ``BD_INFO`` with the
       used count — **skipped entirely for DDP**, which carries its range on
       the mode command instead;
    4. ``SCONFIG`` on every Long_Range board, one request per bank of four
       outputs that has a receiver on it (``:1437-1439`` for the ordering —
       after the universes, before the ports);
    5. ``PCONFIG`` per fitted pixel board, 16 rows each;
    6. the serial ``DATA_MODE``, which is the J3 DMX-out bridge;
    7. the UnPack remap reset, when the firmware understands it;
    8. ``OP_MODE ETHERNET`` — the reboot that makes the controller load any of
       this.

    Step 8 is not optional and not conditional. A configuration written without
    it is stored and never loaded, which looks identical to success from the UI
    (#943 B8). It is returned here as a single request of kind ``reboot``; the
    executor sends it twice 100 ms apart, as xLights does.
    """
    max_u = int(intended.max_universes or 0)
    if max_u <= 0:
        raise ConfigError("the controller's universe limit is unknown — probe "
                          "it before uploading a configuration")
    if not intended.board_ports:
        raise ConfigError("no expansion boards are known — probe the "
                          "controller so its fitted boards are recorded")
    if len(intended.universes) > max_u:
        raise ConfigError(
            f"layout needs {len(intended.universes)} universes but the "
            f"controller supports {max_u}")

    out = []
    if read_mode:
        out.append(CgiRequest(
            hb.CGI_DATA_MODE, blk=0, kind="read",
            note="read the current input mode"))

    mode_cmd = {"CMD": "DATA_MODE", "MODE": intended.mode}
    note = f"input mode -> {intended.mode}"
    if intended.mode == "DDP":
        # DDP replaces the whole universe table with one channel range
        # (HinksPix.cpp:378-382, :402-405).
        mode_cmd["DDP_START"] = intended.ddp_start
        mode_cmd["DDP_CHAN_COUNT"] = intended.ddp_channels
        note += (f" (channels {intended.ddp_start}-"
                 f"{intended.ddp_start + intended.ddp_channels - 1}, "
                 f"no universe table)")
    out.append(CgiRequest(hb.CGI_POST_DATA, data=_dump(mode_cmd), note=note))

    if intended.mode != "DDP":
        table = [intended.universes[i].to_v() for i in sorted(intended.universes)]
        for blk, chunk in enumerate(universe_blocks(table)):
            out.append(CgiRequest(
                hb.CGI_POST_DATA,
                data=_dump({"CMD": "E131", "BLK": str(blk),
                            "LIST": [{"V": r} for r in chunk]}),
                note=f"universe table block {blk} "
                     f"({blk * hb.UNIVERSES_PER_BLOCK + 1}-"
                     f"{blk * hb.UNIVERSES_PER_BLOCK + len(chunk)})"))
        num_u = len(intended.used_universes)
        out.append(CgiRequest(
            hb.CGI_POST_DATA,
            data=_dump({"CMD": "BD_INFO", "NumU": str(num_u)}),
            note=f"universes in use -> {num_u} of {max_u}"))

    for board in sorted(intended.smart_receivers):
        for bank in sorted(intended.smart_receivers[board]):
            recs = intended.smart_receivers[board][bank]
            if recs:
                # A bank with no receiver gets *no request at all*, not an
                # empty LIST — `UploadSmartReceiverData` returns before sending
                # when the list is empty (`:835-838`). An empty LIST would be a
                # statement we have no evidence the controller accepts.
                out.append(_sconfig_request(board, bank, recs))

    for board in sorted(intended.board_ports):
        rows = intended.board_ports[board]
        used = sum(1 for r in rows if r.used)
        out.append(CgiRequest(
            hb.CGI_POST_DATA,
            data=_dump({"CMD": "PCONFIG", "BOARD": str(board - 1),
                        "LIST": [{"V": r.to_v()} for r in rows]}),
            note=f"board {board} (BD{board}) ports "
                 f"{(board - 1) * hb.PORTS_PER_BOARD + 1}-"
                 f"{board * hb.PORTS_PER_BOARD}, {used} in use"))

    out.append(CgiRequest(hb.CGI_POST_DATA, data=_dump(intended.serial.to_payload()),
                          note="J3 DMX-512 output bridge"))

    if hb.supports_unpack(mcpu, hardware_v3):
        # The layout this module produces is always contiguous, which is the
        # "we have continuous memory" branch of UploadUnPack: it sends the
        # reset form with **empty** LIST and a doubled outer brace — the header
        # value is `DATA: {{"BLK":"0",...}}`, not valid JSON, and that is
        # deliberate (HinksPix.cpp:479-487). Skipping it leaves a remap table
        # the operator's last xLights session left behind, scrambling our
        # layout (#943 B13).
        out.append(CgiRequest(
            hb.CGI_UNPACK, data="{" + hb.UNPACK_RESET + "}",
            note="reset the UnPack remap table (contiguous layout)"))
    else:
        log.info("UnPack reset not sent: firmware MCPU %s is below the gate",
                 mcpu)

    out.append(CgiRequest(
        hb.CGI_POST_DATA, data=_dump(hb.OP_MODE_ETHERNET), kind="reboot",
        note="reboot into live-Ethernet mode (sent twice, 100 ms apart)"))
    return out


# ── Backups and restore (#945) ───────────────────────────────────────────────
#
# A backup is a *snapshot of the readbacks*, not of our model of them: the raw
# row strings the controller handed over are kept verbatim and replayed
# verbatim, because a restore's only job is to put the device back the way it
# was. Re-deriving the rows from `decoded` would quietly "fix" anything our
# decoder misreads, which is the opposite of what a restore is for.
#
# Two things cannot be captured at all: UnPack and SCONFIG have no read CGI in
# xLights (`HinksPix.cpp` has no `GetSmartReceiverData`, and `UploadUnPack` has
# no counterpart), so a restore resets them rather than recreating them. That is
# a documented limitation of the controller, surfaced in `restore_commands`'
# warnings rather than hidden (design doc §4.6).

def flatten_blocks(blocks):
    """``{block index: [row strings]}`` -> one row list, in wire order.

    The table is read six rows at a time, so it arrives as blocks; anything
    that wants to look at it as the controller's own table — a summary, a
    restore, a post-restore diff — has to put it back in order first.
    """
    rows = []
    for blk in sorted(blocks or {}, key=lambda k: int(k)):
        rows.extend(blocks[blk])
    return rows


def backup_from_read(probe=None, data_mode=None, board_ports=None,
                     universe_blocks=None, at=None):
    """A restorable snapshot built from one round of device reads.

    ``board_ports`` is ``{1-based board: [16 row strings]}`` as returned by
    ``hinkspix_bridge.read_board_ports``; ``universe_blocks`` is
    ``{block index: [6 row strings]}`` as returned by ``read_e131_text``. Both
    are stored as received, and ``decoded`` is kept beside them so the SPA can
    show what was captured without re-parsing — the decoded side includes the
    universe table, because a snapshot whose decode says nothing about the
    universes reads as a snapshot with none.
    """
    decoded = decode_device_config(board_info=probe, data_mode=data_mode,
                                   board_ports=board_ports,
                                   universe_rows=flatten_blocks(universe_blocks)
                                   or None)
    return {
        "version": BACKUP_VERSION,
        "at": int(time.time() if at is None else at),
        "probe": dict(probe or {}),
        "raw": {
            "dataMode": dict(data_mode or {}),
            "boards": {str(int(b)): list(rows)
                       for b, rows in sorted((board_ports or {}).items())},
            "e131": {str(int(k)): list(v)
                     for k, v in sorted((universe_blocks or {}).items())},
        },
        "decoded": decoded.to_json(),
    }


def backup_summary(backup):
    """The one-line description of a snapshot, for a list or a banner."""
    if not backup:
        return ""
    decoded = backup.get("decoded") or {}
    ports = decoded.get("ports") or {}
    used = [p for p in ports.values() if p.get("used")]
    universes = len([u for u in (decoded.get("universes") or [])
                     if u.get("channels")])
    return (f"{len(backup.get('raw', {}).get('boards') or {})} board(s), "
            f"{len(used)} port(s) in use, {universes} universe row(s), mode "
            f"{decoded.get('mode') or '?'}")


def decode_backup(backup):
    """The ``DeviceConfig`` a snapshot describes, universe table included.

    ``decode_device_config`` leaves the universe table out because an ordinary
    readback has none. A backup does, so the snapshot can be the "intended"
    side of the verification diff after a restore — the comparison then asks
    the one question worth asking, which is whether the controller came back
    holding what was put back.
    """
    raw = (backup or {}).get("raw") or {}
    return decode_device_config(board_info=backup.get("probe"),
                                data_mode=raw.get("dataMode"),
                                board_ports=raw.get("boards"),
                                universe_rows=flatten_blocks(raw.get("e131"))
                                or None)


def _e131_gaps(blocks):
    """Block indices missing from the low end of a captured universe table.

    A backup taken over a flaky link can come back with a hole in it. Replaying
    a gapped table leaves the controller's rows for the missing blocks exactly
    as they are — the restore is not wrong, but it is not complete either, and
    the operator needs to know which blocks were not covered.
    """
    indices = sorted(int(k) for k in (blocks or {}))
    return [i for i in range(indices[-1] + 1) if i not in indices] if indices else []


def restore_commands(backup, mcpu=None, hardware_v3=False):
    """The requests that put a snapshot back — ``(cmds, warnings)``.

    The same sequence :func:`build_commands` emits, in the same order and with
    the same reboot at the end, but every payload is taken from the snapshot
    rather than from the layout. The one thing it does *not* replay is the
    UnPack remap, which a backup cannot contain: the reset form is sent when
    the firmware understands it, which is also what an ordinary upload sends.
    """
    warnings = []
    if not backup:
        raise ConfigError("there is no backup to restore")
    version = int(backup.get("version") or 0)
    if version != BACKUP_VERSION:
        raise ConfigError(f"backup version {version} is not one this build "
                          f"understands (expected {BACKUP_VERSION})")

    raw = backup.get("raw") or {}
    decoded = backup.get("decoded") or {}
    mode = decoded.get("mode")
    if not mode:
        raise ConfigError("the backup does not record an input mode, so it "
                          "cannot be put back")

    boards = raw.get("boards") or {}
    if not boards:
        warnings.append("the backup holds no port rows — the controller's port "
                        "table will be left as it is")
    e131 = raw.get("e131") or {}
    if mode != "DDP":
        if not e131:
            warnings.append("the backup holds no universe table — the "
                            "controller's existing table will be left as it is")
        else:
            gaps = _e131_gaps(e131)
            if gaps:
                warnings.append(
                    "the backup is missing universe block(s) "
                    + ", ".join(str(g) for g in gaps)
                    + " — those rows will be left as they are")
    warnings.append("UnPack and smart-receiver settings are not captured by a "
                    "backup, so a restore resets them (design doc §4.6)")

    out = [CgiRequest(hb.CGI_DATA_MODE, blk=0, kind="read",
                      note="read the current input mode")]
    out.append(CgiRequest(hb.CGI_POST_DATA,
                          data=_dump({"CMD": "DATA_MODE", "MODE": mode}),
                          note=f"input mode -> {mode} (from the backup)"))

    if mode != "DDP":
        for blk in sorted(e131, key=lambda k: int(k)):
            rows = list(e131[blk])
            out.append(CgiRequest(
                hb.CGI_POST_DATA,
                data=_dump({"CMD": "E131", "BLK": str(int(blk)),
                            "LIST": [{"V": r} for r in rows]}),
                note=f"universe table block {blk} (from the backup, verbatim)"))
        num_u = (backup.get("probe") or {}).get("NumU")
        if num_u is not None:
            out.append(CgiRequest(
                hb.CGI_POST_DATA, data=_dump({"CMD": "BD_INFO", "NumU": str(num_u)}),
                note=f"universes in use -> {num_u} (from the backup)"))

    for board in sorted(boards, key=lambda b: int(b)):
        rows = list(boards[board])
        used = sum(1 for r in rows
                   if (hb.parse_port_row(r) or {}).get("pixels"))
        out.append(CgiRequest(
            hb.CGI_POST_DATA,
            data=_dump({"CMD": "PCONFIG", "BOARD": str(int(board) - 1),
                        "LIST": [{"V": r} for r in rows]}),
            note=f"board {board} ports {(int(board) - 1) * hb.PORTS_PER_BOARD + 1}-"
                 f"{int(board) * hb.PORTS_PER_BOARD} (from the backup, verbatim), "
                 f"{used} in use"))

    ser = decoded.get("serial")
    if ser:
        out.append(CgiRequest(
            hb.CGI_POST_DATA,
            data=_dump(SerialRow(ser["dmxActive"], ser["dmxUniverse"],
                                 ser["dmxStart"], ser["dmxChannels"],
                                 ser["ddpActive"], ser["ddpStart"],
                                 ser["ddpChannels"]).to_payload()),
            note="J3 DMX-512 output bridge (from the backup)"))
    else:
        warnings.append("the backup does not record the J3 DMX-512 bridge, so "
                        "it will be left as it is")

    if hb.supports_unpack(mcpu, hardware_v3):
        out.append(CgiRequest(hb.CGI_UNPACK, data="{" + hb.UNPACK_RESET + "}",
                              note="reset the UnPack remap table (a backup "
                                   "cannot capture it)"))
    out.append(CgiRequest(
        hb.CGI_POST_DATA, data=_dump(hb.OP_MODE_ETHERNET), kind="reboot",
        note="reboot into live-Ethernet mode (sent twice, 100 ms apart)"))
    return out, warnings


# ── Defaults from the fixtures on the ports (#946) ───────────────────────────
#
# The controller's port table and the SlyLED fixtures bound to those ports
# describe the same pixels from two sides, and the operator should not have to
# type a number twice. Pixels and length come from the fixture, because the
# fixture is what knows how long the strip is; the controller's own fields stay
# as they are unless something declares otherwise.
#
# Deliberately *not* derived: the pixel protocol and the colour order of a
# string that does not declare one. Both are guesses with a visible cost — a
# recoloured strip or a silently changed protocol — and a wrong guess is worse
# than an untouched field, because it looks like the operator's own setting.

# Strip types, as the operator writes them, to the colour order the node
# latches. A WS2812B takes GRB, a WS2811 RGB, an APA102 BGR; the rest of each
# class behaves the same way. Anything not listed keeps its current order.
LED_TYPE_COLOR_ORDER = {
    "ws2811": "RGB", "ws2815": "RGB",
    "ws2812": "GRB", "ws2812b": "GRB", "ws2813": "GRB", "sk6812": "GRB",
    "apa102": "BGR", "apa107": "BGR", "sk9822": "BGR",
}


def color_order_for_type(led_type):
    """The colour order a declared strip type latches, or ``None``."""
    return LED_TYPE_COLOR_ORDER.get(str(led_type or "").strip().lower())


def defaults_from_fixtures(child, fixtures, caps=None):
    """Port rows filled in from the SlyLED fixtures bound to this controller.

    Returns ``{"ports": [...], "changes": [...], "unbound": [...]}`` — a
    proposal, not a write. Nothing here touches the controller, and the caller
    puts the result in the editor for the operator to review, because the one
    thing a "fill it in for me" button must not do is change something the
    operator did not look at.

    Per bound port: pixels and length are the *sum of the fixture strings on
    it*, which is also what makes a multi-fixture output come out right. The
    stored row is the starting point for everything else.
    """
    hinks = (child or {}).get("hinks") or {}
    caps = caps or caps_for(hinks)
    defaults = hinks.get("defaults") or {}
    have = {int(p.get("port")): p for p in (hinks.get("ports") or [])
            if p.get("port") is not None}
    by_port = strings_by_port(child, fixtures)

    ports, changes, unbound = [], [], []
    for pnum in sorted(by_port):
        strings = by_port[pnum]
        cur = have.get(pnum) or {}
        leds = sum(int(s.get("leds") or 0) for s in strings)
        mm = sum(int(s.get("mm") or 0) for s in strings)
        if leds <= 0:
            continue
        order = None
        for s in strings:
            order = color_order_for_type(s.get("ledType")) or order
        row = {
            "port": pnum,
            "leds": leds,
            "mm": mm or None,
            "protocol": cur.get("protocol") or "ws2811",
            "colorOrder": order or cur.get("colorOrder") or "RGB",
            "direction": cur.get("direction", 0),
            # The receiver comes from the port's own settings: which one is on
            # an output is a fact about the wiring, not about the fixture.
            "smartRemote": cur.get("smartRemote"),
            "smartRemoteType": cur.get("smartRemoteType"),
            "startNulls": _start_nulls(cur),
            "brightness": hb.encode_brightness(
                defaults.get("brightness", cur.get("brightness", 100))),
            "gamma": hb.encode_gamma(defaults.get("gamma", cur.get("gamma", 1))),
            "enabled": True,
        }
        ports.append(row)
        if cur.get("leds") is not None and int(cur["leds"]) != leds:
            changes.append(f"port {pnum}: {int(cur['leds'])} -> {leds} pixels")
        if cur.get("mm") is not None and mm and int(cur["mm"]) != mm:
            changes.append(f"port {pnum}: length {int(cur['mm'])} -> {mm} mm")
        if order and cur.get("colorOrder") and cur["colorOrder"] != order:
            changes.append(f"port {pnum}: colour order {cur['colorOrder']} "
                           f"-> {order} (from the string type)")

    bound = set(by_port)
    unbound = [int(p.get("port")) for p in (hinks.get("ports") or [])
               if p.get("port") is not None and p.get("enabled", True)
               and int(p.get("leds") or 0) > 0 and int(p["port"]) not in bound]
    return {"ports": ports, "changes": changes, "unbound": sorted(unbound),
            "caps": caps.to_json()}


# ── Findings (#945, filled out in #946) ──────────────────────────────────────

class Finding:
    """One statement about a configuration, in the operator's own words.

    ``level`` is one of three, and the difference is what Apply does with it:

    - ``"error"`` — Apply is refused, and there is no acknowledgement that gets
      past it. Reserved for configs the controller itself will not take, so the
      gate cannot be talked around.
    - ``"warn"`` — Apply is refused until the request acknowledges this code.
      For a decision that is the operator's to make, once, having read it.
    - ``"info"`` — never blocks. For something that is true of every push (a
      reboot) rather than a decision: as a warning it becomes a box to tick on
      every push, which is how an acknowledgement gate degrades into a
      click-through (#945 F3).

    ``code`` is a stable slug: tests key on it and the SPA groups by it, so the
    wording of ``text`` can change without breaking anything. ``port`` is set
    when the finding is about one port, so the editor can mark that row rather
    than printing a list.
    """

    __slots__ = ("level", "code", "port", "text")

    def __init__(self, level, code, text, port=None):
        self.level = level
        self.code = code
        self.port = port
        self.text = text

    def to_json(self):
        return {"level": self.level, "code": self.code, "port": self.port,
                "text": self.text}


def errors(findings):
    return [f for f in (findings or []) if f.level == "error"]


def validate(child, intended=None, probe=None, fixtures=None,
             engine_protocol=None, caps=None):
    """Everything worth saying about this configuration, in the operator's words.

    Two levels, and the difference matters: an ``error`` is a configuration the
    controller or the reference client would refuse, and it blocks the push; a
    ``warn`` is a decision the operator is entitled to make once they have been
    shown it, so it blocks until the request names its code in ``ack`` (#945).

    ``probe`` is a fresh reading of the fitted boards — either a raw
    ``XLights_BoardInfo.cgi`` reply or a ``hinkspix_bridge.probe()`` info dict —
    and takes precedence over the recorded ones. ``engine_protocol`` is what
    the orchestrator is streaming; it is passed in rather than read, because
    this module stays pure.
    """
    hinks = (child or {}).get("hinks") or {}
    caps = caps or caps_for(hinks)
    boards = normalise_boards(((probe or {}).get("boards") or probe)) \
        if probe else present_boards(child)
    px_boards = _pixel_boards(boards)
    ports = hinks.get("ports") or []
    out = []

    def used(p):
        return p.get("enabled", True) and int(p.get("leds") or 0) > 0

    # ── Can this controller be configured at all? ────────────────────────────
    probed = bool(int(hinks.get("maxU") or 0))
    if not probed:
        out.append(Finding(
            "error", "not_probed",
            "The controller has not been probed, so its universe limit and "
            "fitted boards are unknown. Probe it before uploading a "
            "configuration."))
    elif not px_boards:
        out.append(Finding(
            "error", "no_boards",
            "No expansion boards are known for this controller. Probe it so "
            "the fitted boards are recorded."))

    mcpu = hinks.get("mcpu")
    if mcpu is not None:
        if not hb.supports_upload(mcpu, bool(hinks.get("hardwareV3"))):
            floor = hb.MIN_MCPU_UPLOAD_V3 if hinks.get("hardwareV3") \
                else hb.MIN_MCPU_UPLOAD
            out.append(Finding(
                "error", "upload_unsupported",
                f"Main CPU MS_{mcpu} is too old for a network upload — the "
                f"controller needs MS_{floor} or newer. Update it over the SD "
                f"card first; nothing can be pushed until then."))
        # There is deliberately no separate "this firmware may not understand
        # smart receivers" warning. #946 listed one (`firmware_below_101`), and
        # it cannot be reached: the upload gate above is MS_151 (MS_129 on a
        # PRO V3), so every firmware that could raise a smart-receiver warning
        # is already blocked outright by that error, and any floor above the
        # upload gate would be a number with no source. xLights has none either
        # — `UploadSmartReceivers` sends to whatever answered the probe. If a
        # bench capture ever shows a floor, it belongs beside MIN_MCPU_UPLOAD
        # in the bridge, reached by the same comparison.

    proto = str(hinks.get("protocol") or "").lower()
    offered = input_protocols_for(hinks)
    if proto and proto not in offered:
        out.append(Finding(
            "error", "input_protocol_unsupported",
            f"The input protocol is set to {proto.upper()}. {caps.name} "
            f"accepts {'/'.join(p.upper() for p in offered)}."))

    if not any(used(p) for p in ports):
        # An **error**, not the warning #946 listed. A bench run on the MS_160
        # unit settled it: with no ports in use `build_commands` ends the
        # sequence with `{"CMD":"BD_INFO","NumU":"0"}` and the controller
        # answers ERROR, so this config can never be applied. Acknowledging a
        # warning would only get the operator past the gate and into a push that
        # writes the (empty) universe table and then dies — the half-written
        # device this whole section exists to prevent (#945 F2).
        out.append(Finding(
            "error", "empty_config",
            "Every port is disabled, so this configuration uses no universes "
            "and the controller refuses to be told about none. Enable at least "
            "one port with pixels on it before applying."))

    # ── Per-port ─────────────────────────────────────────────────────────────
    chpp_of = {}
    for p in sorted(ports, key=lambda x: int(x.get("port") or 0)):
        try:
            pnum = int(p.get("port"))
        except (TypeError, ValueError):
            continue
        leds = int(p.get("leds") or 0)
        board = board_of(pnum)
        btype = boards.get(board)
        chpp = hb.channels_per_pixel(p.get("colorOrder"))
        chpp_of[pnum] = chpp

        if board > caps.boards:
            out.append(Finding(
                "error", "port_on_absent_board",
                f"Port {pnum} is on expansion {board}. {caps.name} addresses "
                f"{caps.max_pixel_port} outputs (BD1-BD{caps.boards}), so that "
                f"board does not exist on this model.", port=pnum))
        elif used(p) and btype is None:
            out.append(Finding(
                "error", "port_on_absent_board",
                f"Port {pnum} is on expansion {board} (BD{board}), which is not "
                f"fitted — nothing is plugged into that slot.", port=pnum))
        elif used(p) and btype not in hb.PIXEL_BOARD_TYPES:
            what = ("reports Not_Present" if btype == "Not_Present"
                    else f"is a {btype.replace('_', ' ')} board")
            out.append(Finding(
                "error", "port_on_absent_board",
                f"Port {pnum} is on expansion {board} (BD{board}), which "
                f"{what} — it has no pixel outputs.", port=pnum))

        if used(p) and leds * chpp > caps.max_pixel_port_channels:
            fit = caps.max_pixels()
            out.append(Finding(
                "error", "port_channels_exceed",
                f"Port {pnum} has {leds} pixels = {leds * chpp} channels; this "
                f"model allows {caps.max_pixel_port_channels} "
                f"({fit} RGB pixels).", port=pnum))

        pproto = str(p.get("protocol") or "ws2811").lower()
        if used(p) and pproto not in caps.pixel_protocols:
            out.append(Finding(
                "error", "protocol_unsupported",
                f"Port {pnum} is set to {pproto}; {caps.name} drives "
                f"{'/'.join(caps.pixel_protocols)} only.", port=pnum))

        if smart_remote_id(p) >= 0 and btype is not None \
                and btype != "Long_Range":
            out.append(Finding(
                "error", "smart_on_non_long_range",
                f"Port {pnum} has a smart receiver set, but BD{board} is a "
                f"{btype.replace('_', ' ')} board. Receivers only work on Long "
                f"Range boards.", port=pnum))

    # ── The guidance a board type earns ──────────────────────────────────────
    for board in px_boards:
        if boards.get(board) != "Long_Range":
            continue
        on_board = [p for p in ports
                    if _port_num(p) and board_of(_port_num(p)) == board
                    and used(p)]
        if on_board and not any(smart_remote_id(p) >= 0 for p in on_board):
            # Only while *no* receiver is set on the board: once the operator
            # has configured one they have acted on this, and a warning that
            # reappears on every push is a warning nobody reads (#945).
            lo = (board - 1) * hb.PORTS_PER_BOARD + 1
            out.append(Finding(
                "warn", "board_long_range",
                f"Ports {lo}-{lo + hb.PORTS_PER_BOARD - 1} are on a Long Range "
                f"board: pixels connect through a receiver, not directly. If "
                f"those receivers are addressable, set each one's ID so the "
                f"controller knows where each run of pixels starts."))

    # ── Against the fixtures on those ports ──────────────────────────────────
    if fixtures is not None:
        by_port = strings_by_port(child, fixtures)
        for p in ports:
            pnum = _port_num(p)
            if pnum and used(p) and pnum not in by_port:
                out.append(Finding(
                    "warn", "port_unbound",
                    f"Port {pnum} is enabled with {int(p.get('leds') or 0)} "
                    f"pixels but no SlyLED fixture uses it — it will be written "
                    f"and never receive data.", port=pnum))
        for pnum, strings in sorted(by_port.items()):
            cur = next((p for p in ports if _port_num(p) == pnum), None)
            if cur is None or not used(cur):
                continue
            bound = sum(int(s.get("leds") or 0) for s in strings)
            if bound and bound != int(cur.get("leds") or 0):
                names = ", ".join(sorted({str(f.get("name") or f.get("id"))
                                          for f in fixtures
                                          if any(s in (f.get("strings") or [])
                                                 for s in strings)}))
                out.append(Finding(
                    "error", "fixture_leds_mismatch",
                    f"Port {pnum} is set to {int(cur.get('leds') or 0)} pixels "
                    f"but the fixture on it ({names}) drives {bound}. The "
                    f"controller would drive pixels the fixture does not "
                    f"have.", port=pnum))

    # ── Against the layout ───────────────────────────────────────────────────
    if intended is not None:
        max_u = int(intended.max_universes or 0)
        if max_u and len(intended.universes) > max_u:
            out.append(Finding(
                "error", "universes_exceed_maxu",
                f"The layout needs {len(intended.universes)} universes but this "
                f"controller supports {max_u}."))

        # Two rows claiming one universe, or two rows claiming one channel. The
        # layout this module derives cannot produce either — every port starts
        # where the last one ended — but a restored or imported table can, and
        # an overlap there means two fixtures writing each other's pixels
        # (#946, #947).
        by_uni, prev = {}, None
        for i in sorted(intended.universes):
            row = intended.universes[i]
            if row.empty:
                continue
            first = by_uni.get(row.universe)
            if first is not None:
                out.append(Finding(
                    "error", "universe_overlap",
                    f"Universe {row.universe} is claimed by table rows "
                    f"{first} and {i}. Every universe must appear once."))
            else:
                by_uni[row.universe] = i
        for pnum in sorted(intended.ports):
            row = intended.ports[pnum]
            if not row.used:
                continue
            if prev is not None and row.start <= prev[1]:
                out.append(Finding(
                    "error", "channel_overlap",
                    f"Port {pnum} starts at channel {row.start}, inside port "
                    f"{prev[2]}'s range ({prev[0]}-{prev[1]}). Two ports cannot "
                    f"share channels.", port=pnum))
            prev = (row.start, row.end, pnum)

        dmx = hinks.get("dmxOut") or {}
        if dmx.get("enabled") and int(dmx.get("universe") or 0) in by_uni:
            out.append(Finding(
                "error", "universe_overlap",
                f"Universe {int(dmx['universe'])} carries the J3 DMX output and "
                f"pixels as well. Give the DMX output a universe of its own."))

    # ── Against the engine, and what a push costs ────────────────────────────
    if engine_protocol and proto:
        want = hb.BOARD_MODES.get(proto)
        streaming = hb.BOARD_MODES.get(str(engine_protocol).lower())
        if want and streaming and want != streaming:
            out.append(Finding(
                "warn", "engine_protocol_mismatch",
                f"The controller is configured for {want} but SlyLED is "
                f"streaming {streaming}. Frames will not reach the device "
                f"until these agree."))

    if intended is not None:
        # **info**, not the warning #946 listed: this is a statement about what
        # a push costs, and it is true of every push, so as a warning it was a
        # box the operator ticked on each one — which is how an acknowledgement
        # gate turns into a click-through (#945 F3). It is said before the
        # button in the wizard's confirm step, where it is still read.
        out.append(Finding(
            "info", "reboot_required",
            "Applying reboots the controller. The pixels go dark for up to "
            "90 seconds while it comes back on the new configuration."))
    return out


def _port_num(cfg):
    try:
        return int((cfg or {}).get("port"))
    except (TypeError, ValueError):
        return 0


# ── Comparing what we want against what is there ─────────────────────────────

def decode_device_config(board_info=None, data_mode=None, board_ports=None,
                         universe_rows=None):
    """Build a ``DeviceConfig`` from the controller's own readbacks.

    ``board_ports`` is ``{board_number: [16 row strings]}`` as returned by
    ``hinkspix_bridge.read_board_ports``. Anything absent stays absent rather
    than defaulted, so ``diff`` reports "not read" instead of inventing a
    match.

    ``universe_rows`` is normally left unset. xLights never reads the universe
    table back — ``GetControllerE131Data`` has no callers — and reading it one
    row at a time would be one request per row, so the route does not fetch it
    and ``diff`` says so rather than reporting every row as a difference.
    """
    cfg = DeviceConfig(max_universes=int((board_info or {}).get("MaxU") or 0))
    if data_mode:
        cfg.mode = data_mode.get("MODE")
        cfg.serial = SerialRow.from_read(data_mode)
    if universe_rows:
        cfg.universes = {r["index"]: UniverseRow(
            r["index"], r["universe"], r["channels"], r["start"], r["end"])
            for r in hb.parse_universes(universe_rows).values()}

    for board, rows in (board_ports or {}).items():
        board = int(board)
        parsed = hb.parse_port_rows(rows)
        got = []
        for i in range(hb.PORTS_PER_BOARD):
            output = (board - 1) * hb.PORTS_PER_BOARD + i + 1
            row = parsed.get(output)
            if row is None or any(row[f] is None for f in hb.PORT_FIELDS):
                # A row the controller sent but we could not read as ten
                # integers. Leaving it out means diff says "not read" for that
                # output instead of comparing against a half-decoded row.
                log.warning("HinksPix board %d output %d: unreadable PCONFIG row "
                            "%r", board, output, row and rows)
                continue
            port = PortRow(row["output"], row["protocol"], row["start"],
                           row["pixels"], row["end"], row["direction"],
                           row["color_order"], row["null_pixels"],
                           row["brightness"], row["gamma"])
            cfg.ports[port.output] = port
            got.append(port)
        if got:
            cfg.board_ports[board] = got
    return cfg


def diff(current, intended, limit=40):
    """What an upload would change, as structured lines.

    Only differences are listed, and ports we would not write are not compared
    — a readback covers 16 rows per board whether or not we drive them, and
    reporting the untouched ones as differences would bury the real ones.
    ``limit`` caps the per-section listing so a first-time upload against a
    factory-fresh controller (48 ports and up to 402 universe rows) stays
    readable; the count is always the true one.

    A section the readback did not cover is reported once as unread, not as N
    differences. xLights never reads the universe table back at all
    (``GetControllerE131Data`` has no callers), so "every row differs" would be
    the normal case and would mean nothing.

    Returns ``{"changed": bool, "items": [...], "counts": {...}}``.
    """
    items = []
    if current is None:
        return {"changed": True, "unknown": True, "items": [
            {"section": "device", "text": "the controller has not been read"}],
            "counts": {}}

    if current.mode != intended.mode and intended.mode is not None:
        items.append({"section": "mode", "now": current.mode,
                      "want": intended.mode,
                      "text": f"input mode: {current.mode} -> {intended.mode}"})

    if current.serial and intended.serial:
        for key, now, want in _serial_diffs(current.serial, intended.serial):
            items.append({"section": "serial", "key": key, "now": now,
                          "want": want, "text": f"J3 {key}: {now} -> {want}"})

    uni_count = 0
    if not current.universes:
        if intended.universes:
            items.append({"section": "universe", "unread": True,
                          "text": f"universe table not read — "
                                  f"{len(intended.universes)} rows will be sent"})
    else:
        for i in sorted(set(current.universes) | set(intended.universes)):
            now = current.universes.get(i)
            want = intended.universes.get(i)
            if _same_universe(now, want):
                continue
            uni_count += 1
            if uni_count <= limit:
                items.append({
                    "section": "universe", "index": i,
                    "now": now.to_json() if now else None,
                    "want": want.to_json() if want else None,
                    "text": (f"universe row {i}: "
                             f"{now.to_v() if now else '(absent)'} -> "
                             f"{want.to_v() if want else '(cleared)'}")})
        if uni_count > limit:
            items.append({"section": "universe", "overflow": uni_count - limit,
                          "text": f"...and {uni_count - limit} more universe "
                                  f"rows"})

    port_count = 0
    read_boards = set(current.board_ports)
    for board in sorted(intended.board_ports):
        if board not in read_boards:
            items.append({"section": "port", "board": board, "unread": True,
                          "text": f"board {board} not read — 16 rows will be "
                                  f"sent"})
    for output in sorted(intended.ports):
        now = current.ports.get(output)
        if now is not None and now.to_v() == intended.ports[output].to_v():
            continue
        want = intended.ports[output]
        port_count += 1
        if port_count <= limit:
            items.append({
                "section": "port", "output": output,
                "now": now.to_json() if now else None,
                "want": want.to_json(),
                "text": (f"board {(output - 1) // hb.PORTS_PER_BOARD + 1} "
                         f"port {output}: {now.to_v() if now else '(not read)'}"
                         f" -> {want.to_v()}")})
    if port_count > limit:
        items.append({"section": "port", "overflow": port_count - limit,
                      "text": f"...and {port_count - limit} more ports"})

    return {"changed": bool(items), "unknown": False, "items": items,
            "counts": {"universes": uni_count, "ports": port_count}}


def _same_universe(now, want):
    if now is None or want is None:
        return now is want
    return (now.universe, now.channels, now.start, now.end) == \
           (want.universe, want.channels, want.start, want.end)


def _serial_diffs(now, want):
    pairs = (("DMX_ACTIVE", now.dmx_active, want.dmx_active),
             ("DMX_UNIV", now.dmx_universe, want.dmx_universe),
             ("DMX_START", now.dmx_start, want.dmx_start),
             ("DMX_CHAN_CNT", now.dmx_channels, want.dmx_channels),
             ("DDP_DMX_ACTIVE", now.ddp_active, want.ddp_active),
             ("DDP_DMX_START", now.ddp_start, want.ddp_start),
             ("DDP_DMX_CHAN_CNT", now.ddp_channels, want.ddp_channels))
    return [(k, n, w) for k, n, w in pairs if n != w]
