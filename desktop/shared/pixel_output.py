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

Pure module: no Flask, no device I/O, no global state.
"""

import math

PIXELS_PER_UNIVERSE = 170      # 510 of 512 channels
CHANNELS_PER_UNIVERSE = 512
DMX_OUT_CHANNELS = 512
MAX_PORTS = 48


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
         "kind": "pixel"|"dmx"}
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
            remaining = pixels
            while remaining > 0:
                chunk = min(remaining, PIXELS_PER_UNIVERSE)
                span = {
                    "port": pnum,
                    "universe": universe,
                    "uniChannelStart": 1,
                    "pixels": chunk,
                    "channels": chunk * 3,
                    "absStart": abs_channel,
                    "kind": "pixel",
                }
                spans.append(span)
                this_port.append(span)
                abs_channel += chunk * 3
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


def universes_needed(pixels):
    """Universes a port of ``pixels`` pixels consumes."""
    return math.ceil(max(0, int(pixels)) / PIXELS_PER_UNIVERSE)


def write_fixture_frame(engine, output_map, fixture, rgb):
    """Write one fixture's rendered RGB into the engine's universe buffers.

    ``rgb`` is the concatenated per-string RGB from
    ``pixel_renderer.render_fixture`` — string order must match
    ``fixture["strings"]``, which is what makes the two modules composable.
    Marks universes dirty; the engine thread transmits them.
    """
    offset = 0
    for string, spans in zip(fixture.get("strings") or [], output_map.fixture_spans(fixture)):
        n = int(string.get("leds") or 0)
        if n <= 0:
            continue
        need = n * 3
        chunk = rgb[offset:offset + need]
        offset += need
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
