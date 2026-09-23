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

import hinkspix_bridge as hb

log = logging.getLogger("slyled.hinkspix")

# The row past the end of the table. xLights walks a fixed 6 slots per call and
# emits this for any index beyond MaxU, so the final block is always full.
UNIVERSE_ZERO_ROW = "0,0,0,0,0,0"

# Options that exist but are not usable on the hardware xLights ships
# controller definitions for. `hinkspix.xcontroller` lists no DDP output for
# PRO V1/V2, which is every unit whose BoardInfo `Type` is not "8"
# (HinksPix.cpp:378-382 still accepts the mode, the caps never offer it).
# Offering a mode the controller cannot serve produces a config that is
# written, accepted, and inert (#943 B14).
PRO_HARDWARE_MODES = ("e131", "sacn", "artnet")


def input_protocols_supported(hardware_v3=False):
    """The input protocols this hardware can actually be driven with."""
    return ("e131", "sacn", "artnet", "ddp") if hardware_v3 else PRO_HARDWARE_MODES


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
                "nullPixels": self.null_pixels, "brightness": self.brightness,
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
                 "max_universes", "ddp_start", "ddp_channels")

    def __init__(self, mode=None, serial=None, universes=None, ports=None,
                 board_ports=None, max_universes=0, ddp_start=1,
                 ddp_channels=0):
        self.mode = mode
        self.serial = serial
        self.universes = universes if universes is not None else {}
        self.ports = ports if ports is not None else {}
        self.board_ports = board_ports if board_ports is not None else {}
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
            "maxUniverses": self.max_universes,
        }


def present_boards(child):
    """``{board_number: type_name}`` for the expansion boards that are fitted.

    ``probe()`` leaves ``hinks.boards`` as ``{"BD1": "Long_Range", ...}``.
    Accepts a plain ``{1: "Long_Range"}`` too, so a caller that has already
    normalised the keys is not forced back into the BD names.
    """
    raw = ((child or {}).get("hinks") or {}).get("boards") or {}
    out = {}
    for k, v in raw.items():
        if isinstance(k, int):
            num = k
        else:
            name = str(k).upper()
            if not name.startswith("BD") or not name[2:].isdigit():
                continue
            num = int(name[2:])
        if 1 <= num <= hb.MAX_BOARDS:
            out[num] = v
    return out


def pixel_boards(child):
    """Boards that carry pixel ports, in ascending order.

    A board is written only if it is a ``Local_SPI`` or ``Long_Range``
    differential board — ``Not_Present`` and ``Local_AC`` get no PCONFIG at all
    (``HinksPix.cpp:670-675``, #943 B7).
    """
    return sorted(b for b, t in present_boards(child).items()
                  if t in hb.PIXEL_BOARD_TYPES)


# ── Building what we want ────────────────────────────────────────────────────

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
        null_pixels=int(cfg.get("nullPixels") or 0),
        brightness=hb.encode_brightness(cfg.get("brightness", 100)),
        gamma=hb.encode_gamma(cfg.get("gamma", 1)),
    )


def intended_config(child, output_map, max_universes=0):
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
    return DeviceConfig(mode=mode, serial=serial,
                        universes={r.index: r for r in
                                   universe_table(output_map.spans,
                                                  max_universes)},
                        ports=ports, board_ports=board_ports,
                        max_universes=max_universes,
                        ddp_start=1, ddp_channels=output_map.total_channels)


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
    4. ``PCONFIG`` per fitted pixel board, 16 rows each;
    5. the serial ``DATA_MODE``, which is the J3 DMX-out bridge;
    6. the UnPack remap reset, when the firmware understands it;
    7. ``OP_MODE ETHERNET`` — the reboot that makes the controller load any of
       this.

    Step 7 is not optional and not conditional. A configuration written without
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
