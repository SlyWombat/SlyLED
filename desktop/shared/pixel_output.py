"""pixel_output.py — pixel-string to DMX-universe mapping (#939).

A HinksPix PRO consumes E1.31/Art-Net/DDP universes and fans them out to up to
48 pixel ports. This module owns the one mapping that says which universe and
channel each port's pixels live at, and it is deliberately the ONLY place that
decision is made — the live streaming path (#940) and the offline ``.hseq``
writer (#941) must agree byte-for-byte about frame layout, so both read this
map rather than computing their own.

Layout rules (matching the xLights convention the controller expects):

* Enabled ports are laid out in port order.
* **Each port starts on a fresh universe**, channel 1. Ports are not packed
  together into a universe, because the controller's own port table addresses
  them that way and partial-universe packing makes the port config unreadable.
* A port of N pixels spans ``ceil(N / 170)`` universes — 170 px = 510 of the
  512 channels.
* ``absStart`` is the controller-absolute channel where the port's data begins,
  counting only the channels actually used. That value is both the
  ``hinksStart`` in the device's input-universe table and the offset into an
  ``.hseq`` frame, which is why it is computed once here.
* When the J3 DMX-512 output is enabled it is appended as a trailing 512-channel
  span *after* all pixel data, matching xLights' "DMX after pixels" assertion.
* A pixel is **not** always three channels. A port whose ``colorOrder`` is
  ``rgbw``/``wrgb`` drives 4-channel nodes, so its end channel, its share of the
  packed channel counter, and the number of pixels a universe holds all change
  (#943 B11). 170 px per universe is the *RGB* figure — it is 510 of 512
  channels; an RGBW universe holds 128.

Pure module: no Flask, no device I/O, no global state. It does import the
protocol's colour-order table from ``hinkspix_bridge`` rather than keeping its
own, because node width is a property of the controller's colour-order code and
a second copy of that table is exactly how the two would drift apart.
"""

import math

import hinkspix_bridge as hb

PIXELS_PER_UNIVERSE = 170      # RGB nodes in one universe: 510 of 512 channels
CHANNELS_PER_UNIVERSE = 512
DMX_OUT_CHANNELS = 512
# The highest output any model addresses, from the protocol module rather than
# from the model this map was first written for: a PRO V3 addresses 80, and a
# table-sized 48 here would have rejected its top two boards (#946).
MAX_PORTS = hb.MAX_PORTS


def channels_per_pixel(color_order):
    """3 for RGB nodes, 4 for RGBW/WRGB — see ``hinkspix_bridge``."""
    return hb.channels_per_pixel(color_order)


def pixels_per_universe(color_order):
    """Pixels one universe holds at this node width: 170 RGB, 128 RGBW."""
    return CHANNELS_PER_UNIVERSE // channels_per_pixel(color_order)


def to_wire_frame(rgb, pixels, chpp):
    """Expand rendered RGB into the byte layout the controller wants.

    ``rgb`` is 3 bytes per pixel (``pixel_renderer`` emits no white channel) and
    ``chpp`` is 3 or 4. For a 3-channel port this is the identity. For a
    4-channel port each pixel becomes ``R,G,B,0``: the controller applies
    ``colorOrder`` itself, exactly as it does for a 3-channel port, so the wire
    order stays canonical RGB and the white slot is zero rather than invented.

    Open bench question: whether a white-channel node on this hardware wants the
    white byte last for ``wrgb`` too (permuted by the controller) or first
    (permuted by us). Sending canonical RGBW and letting the controller permute
    is the reading that matches the 3-channel path, and is what the #943 QA lane
    should confirm against a real strip.
    """
    if chpp == 3:
        return rgb[:pixels * 3]
    out = bytearray(pixels * 4)
    for i in range(pixels):
        out[i * 4:i * 4 + 3] = rgb[i * 3:i * 3 + 3]
    return bytes(out)


class UniverseCollision(ValueError):
    """Raised when a computed universe range overlaps something already in use."""


class PixelOutputMap:
    """The universe/channel layout for one HinksPix child.

    ``spans`` is the flat list this produces, in wire order. Each entry:

        {"port": 1,            # 0 for the trailing DMX-out span
         "universe": 100,
         "uniChannelStart": 1, # 1-based channel within that universe
         "pixels": 170,        # 0 for the DMX-out span
         "channels": 510,
         "absStart": 1,        # 1-based controller-absolute channel
         "channelsPerPixel": 3,  # 4 for RGBW/WRGB ports — see to_wire_frame
         "colorOrder": "RGB",
         "kind": "pixel"|"dmx"}

    ``channelsPerPixel`` is authoritative for the wire layout: a fixture's string
    record has no ``colorOrder`` of its own, so writers read the width off the
    span rather than re-deriving it. 4-channel ports also fit fewer pixels per
    universe (128, not 170).
    """

    __slots__ = ("child_id", "base_universe", "spans", "port_spans", "dmx_span")

    def __init__(self, child_id, base_universe, spans, port_spans, dmx_span):
        self.child_id = child_id
        self.base_universe = base_universe
        self.spans = spans
        self.port_spans = port_spans
        self.dmx_span = dmx_span

    # ── Construction ─────────────────────────────────────────────

    @classmethod
    def build(cls, child):
        """Build the map for a ``type: "hinkspix"`` child record."""
        hinks = (child or {}).get("hinks") or {}
        base = int(hinks.get("baseUniverse") or 1)
        if base < 1:
            raise ValueError("baseUniverse must be >= 1")

        spans = []
        port_spans = {}
        universe = base
        abs_channel = 1

        for port in sorted(hinks.get("ports") or [], key=lambda p: int(p.get("port") or 0)):
            if not port.get("enabled", True):
                continue
            pixels = int(port.get("leds") or 0)
            if pixels <= 0:
                continue
            pnum = int(port.get("port") or 0)
            if not 1 <= pnum <= MAX_PORTS:
                raise ValueError(f"port {pnum} out of range 1..{MAX_PORTS}")

            this_port = []
            chpp = channels_per_pixel(port.get("colorOrder"))
            per_uni = pixels_per_universe(port.get("colorOrder"))
            remaining = pixels
            while remaining > 0:
                chunk = min(remaining, per_uni)
                span = {
                    "port": pnum,
                    "universe": universe,
                    "uniChannelStart": 1,
                    "pixels": chunk,
                    "channels": chunk * chpp,
                    "channelsPerPixel": chpp,
                    "colorOrder": port.get("colorOrder"),
                    "absStart": abs_channel,
                    "kind": "pixel",
                }
                spans.append(span)
                this_port.append(span)
                abs_channel += chunk * chpp
                universe += 1
                remaining -= chunk
            port_spans[pnum] = this_port

        dmx_span = None
        dmx_out = hinks.get("dmxOut") or {}
        if dmx_out.get("enabled"):
            uni = int(dmx_out.get("universe") or 0)
            if uni < 1:
                raise ValueError("dmxOut enabled but universe is unset")
            dmx_span = {
                "port": 0,
                "universe": uni,
                "uniChannelStart": 1,
                "pixels": 0,
                "channels": DMX_OUT_CHANNELS,
                "absStart": abs_channel,
                "kind": "dmx",
            }
            spans.append(dmx_span)

        return cls(int(child.get("id")), base, spans, port_spans, dmx_span)

    # ── Queries ──────────────────────────────────────────────────

    @property
    def universes(self):
        """Every universe this child consumes, in order, de-duplicated."""
        seen, out = set(), []
        for s in self.spans:
            if s["universe"] not in seen:
                seen.add(s["universe"])
                out.append(s["universe"])
        return out

    @property
    def pixel_universes(self):
        """Universes carrying packed RGB only — these are all-intensity, so the
        master grand-master scales every byte (see DMXUniverse.all_intensity)."""
        return [s["universe"] for s in self.spans if s["kind"] == "pixel"]

    @property
    def total_channels(self):
        return sum(s["channels"] for s in self.spans)

    def spans_for_port(self, port):
        return self.port_spans.get(int(port), [])

    def fixture_spans(self, fixture):
        """Spans for each of a fixture's strings, in fixture-string order.

        Returns a list parallel to ``fixture["strings"]``; each element is the
        span list for that string's bound port (empty when unbound).
        """
        return [self.spans_for_port(s.get("port"))
                for s in (fixture.get("strings") or [])]

    def check_collisions(self, other_maps=(), dmx_universes=()):
        """Raise ``UniverseCollision`` if this map overlaps anything else.

        ``other_maps`` are the maps of other HinksPix children; ``dmx_universes``
        are universes already claimed by DMX fixtures. Each port owning whole
        universes makes this a simple set intersection.
        """
        mine = set(self.universes)
        for om in other_maps:
            if om.child_id == self.child_id:
                continue
            clash = mine & set(om.universes)
            if clash:
                raise UniverseCollision(
                    f"universes {sorted(clash)} already used by child {om.child_id}")
        clash = mine & {int(u) for u in dmx_universes}
        if clash:
            raise UniverseCollision(
                f"universes {sorted(clash)} already used by DMX fixtures")

    def route_rows(self, ip):
        """``dmx_settings.universeRoutes`` rows so Art-Net unicast reaches this
        controller without the operator configuring anything. sACN multicast
        needs no route."""
        return [{"universe": u, "destination": ip,
                 "label": f"hinkspix:{self.child_id}"} for u in self.universes]

    def to_json(self):
        return {
            "childId": self.child_id,
            "baseUniverse": self.base_universe,
            "universes": self.universes,
            "totalChannels": self.total_channels,
            "spans": self.spans,
        }


def universes_needed(pixels, chpp=3):
    """Universes a port of ``pixels`` pixels consumes at this node width."""
    return math.ceil(max(0, int(pixels)) / (CHANNELS_PER_UNIVERSE // chpp))


def write_fixture_frame(engine, output_map, fixture, rgb):
    """Write one fixture's rendered RGB into the engine's universe buffers.

    ``rgb`` is the concatenated per-string RGB from
    ``pixel_renderer.render_fixture`` — string order must match
    ``fixture["strings"]``, which is what makes the two modules composable.
    Each string is expanded to its port's node width first, so an RGBW port
    advances 4 bytes per pixel here exactly as ``absStart`` assumed. The width
    comes from the span, not from the fixture record: the span is what the
    universe layout was actually built with.
    Marks universes dirty; the engine thread transmits them.
    """
    offset = 0
    for string, spans in zip(fixture.get("strings") or [], output_map.fixture_spans(fixture)):
        n = int(string.get("leds") or 0)
        if n <= 0:
            continue
        chpp = int(spans[0]["channelsPerPixel"]) if spans else 3
        chunk = to_wire_frame(rgb[offset:offset + n * 3], n, chpp)
        offset += n * 3
        pos = 0
        for span in spans:
            take = span["channels"]
            part = chunk[pos:pos + take]
            if not part:
                break
            buf = engine.get_universe(span["universe"])
            # Pixel universes are pure intensity — flag so the #853 master
            # scales every byte rather than only profile-declared offsets.
            buf.all_intensity = True
            buf.set_channels(span["uniChannelStart"], part)
            pos += take
