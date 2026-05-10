"""
DMXUniverse — Thread-safe 512-byte buffer for a single DMX-512 universe.

Used by both ArtNetEngine and sACN_Engine for channel data management.
"""

import logging
import threading

log = logging.getLogger("slyled.dmx_universe")


class DMXUniverse:
    """Thread-safe 512-channel DMX universe buffer."""

    __slots__ = ("_data", "_lock", "universe", "dirty", "_last_send")

    def __init__(self, universe=1):
        self.universe = universe
        self._data = bytearray(512)
        self._lock = threading.Lock()
        self.dirty = False  # set True on write, cleared by engine after send
        self._last_send = 0  # monotonic timestamp of last ArtDMX send

    # ── Single channel ────────────────────────────────────────────

    def set_channel(self, channel, value):
        """Set a single channel (1-512) to value (0-255)."""
        if channel < 1 or channel > 512:
            return
        with self._lock:
            self._data[channel - 1] = max(0, min(255, int(value)))
            self.dirty = True

    def get_channel(self, channel):
        """Read a single channel (1-512)."""
        if channel < 1 or channel > 512:
            return 0
        with self._lock:
            return self._data[channel - 1]

    # ── Bulk operations ───────────────────────────────────────────

    def set_channels(self, start, values):
        """Set consecutive channels starting at *start* (1-based).
        *values* is an iterable of 0-255 ints."""
        if start < 1:
            return
        with self._lock:
            idx = start - 1
            for v in values:
                if idx >= 512:
                    break
                self._data[idx] = max(0, min(255, int(v)))
                idx += 1
            self.dirty = True

    def get_data(self):
        """Return a snapshot of all 512 bytes (copy)."""
        with self._lock:
            return bytes(self._data)

    def get_data_scaled(self, master_brightness=255,
                        intensity_offsets=None, gamma_lut=None):
        """#853 — return a snapshot with the global master grand-master
        applied to intensity-class channels only.

        ``master_brightness`` 0..255 is the grand-master scalar.
        ``intensity_offsets`` is the iterable of 0-based buffer indices
        (i.e. ``addr - 1 + channel.offset`` for each ``red``/``green``/
        ``blue``/``white``/``amber``/``uv``/``lime``/``dimmer``/
        ``intensity`` channel) on this universe — pan/tilt/strobe/
        gobo/wheel-slot bytes are NOT scaled because they're indices
        / positions, not intensities. ``gamma_lut`` is a 256-byte
        lookup applied after the linear master multiply (matches
        FastLED's gamma curve so DMX and LED outputs perceive
        identically at the same master).

        This is the *single point* where global brightness applies to
        DMX output. Every writer (bake render, Track action, claim
        writer, dmx-test, blackout, calibration sweep) writes raw
        unscaled values into the buffer; the master is applied at
        get-time so the contract is uniform across paths and
        observable from monitor reads + ArtNet sends alike.
        """
        if master_brightness >= 255 or not intensity_offsets:
            with self._lock:
                return bytes(self._data)
        with self._lock:
            scaled = bytearray(self._data)
        if gamma_lut is None:
            for idx in intensity_offsets:
                if 0 <= idx < 512:
                    scaled[idx] = (scaled[idx] * master_brightness) // 255
        else:
            for idx in intensity_offsets:
                if 0 <= idx < 512:
                    linear = (scaled[idx] * master_brightness) // 255
                    scaled[idx] = gamma_lut[linear & 0xFF]
        return bytes(scaled)

    def set_data(self, data):
        """Overwrite the entire universe from a 512-byte buffer."""
        with self._lock:
            n = min(len(data), 512)
            self._data[:n] = data[:n]
            self.dirty = True

    def blackout(self):
        """Zero all 512 channels."""
        with self._lock:
            for i in range(512):
                self._data[i] = 0
            self.dirty = True

    # ── Fixture helpers ───────────────────────────────────────────

    def set_fixture_rgb(self, start_addr, r, g, b, profile=None):
        """Set the colour of a fixture at *start_addr* (1-based) from an
        abstract RGB triple, regardless of how the fixture exposes
        colour to DMX.

        Three profile shapes are handled in one place (#842):

        * **RGB** — the profile maps ``red`` / ``green`` / ``blue``.
          Write each channel from the matching component.
        * **Hybrid RGB + colour wheel** — the profile carries both an
          RGB triple AND a ``color-wheel`` channel. Write RGB, then
          force the wheel to slot 0 (open) so the wheel's default
          coloured slot does not filter the beam (per the long-
          standing project rule in
          ``feedback_hybrid_rgb_wheel_colors.md``).
        * **Colour-wheel-only** — no RGB channels, only a
          ``color-wheel`` channel. Resolve the closest matching wheel
          slot via :func:`dmx_profiles.rgb_to_wheel_slot` and write its
          DMX value directly. This was previously duplicated in three
          render paths (``_evaluate_track_actions``,
          ``_dmx_playback_loop``, ``_show_playback_loop`` segment-mode)
          plus the ``_set_fixture_color`` legacy helper — every caller
          had to remember to do the dance themselves.

        If no ``profile`` is supplied the call falls back to writing
        three consecutive bytes at ``start_addr`` (the legacy "raw RGB
        block" path used by tests).
        """
        if not profile:
            self.set_channels(start_addr, [r, g, b])
            return
        ch_map = profile.get("channel_map", {}) or {}
        r_off = ch_map.get("red")
        g_off = ch_map.get("green")
        b_off = ch_map.get("blue")
        cw_off = ch_map.get("color-wheel")
        has_rgb = any(off is not None for off in (r_off, g_off, b_off))
        if has_rgb:
            if r_off is not None:
                self.set_channel(start_addr + r_off, r)
            if g_off is not None:
                self.set_channel(start_addr + g_off, g)
            if b_off is not None:
                self.set_channel(start_addr + b_off, b)
            # Hybrid RGB + wheel: park the wheel at open so the
            # wheel's default slot doesn't filter the beam.
            if cw_off is not None:
                self.set_channel(start_addr + cw_off, 0)
            return
        if cw_off is not None:
            # Wheel-only fixture — derive the slot from RGB.
            from dmx_profiles import rgb_to_wheel_slot
            slot = rgb_to_wheel_slot(profile, r, g, b) if (r or g or b) else 0
            self.set_channel(start_addr + cw_off, slot)

    def set_fixture_dimmer(self, start_addr, value, profile=None):
        """Set the master dimmer channel for a fixture.

        Operator clarification 2026-05-03: a profile with multiple
        ``dimmer``-typed channels has one MASTER (the lamp gate) and
        one or more aux/effect channels mistyped as dimmer (e.g.
        slymovehead's ch10 is the LASER, not a secondary lamp gate).
        Iterating every ``INTENSITY_TYPES`` channel — the previous
        #749 behaviour — wrote 255 to the laser whenever the lamp
        came on. Master-only is the correct semantic.

        ``channel_map["dimmer"]`` already disambiguates: per
        :class:`dmx_profiles.ProfileLibrary.channel_map`, the channel
        with the highest ``default`` wins, which on slymovehead picks
        ch4 (default=255) over ch10 (default=0).

        Aux/effect channels mistyped as ``dimmer`` are left untouched
        and stay at whatever value the engine pump's per-channel
        default loop wrote (zero on cold start, profile default
        otherwise).
        """
        if not profile:
            return
        v = max(0, min(255, int(value)))
        off = profile.get("channel_map", {}).get("dimmer")
        if off is not None:
            self.set_channel(start_addr + off, v)

    def set_fixture_pan_tilt(self, start_addr, pan, tilt, profile=None):
        """Set pan/tilt for a fixture at the profile's native resolution.

        Thin wrapper over :func:`compute_pan_tilt_writes` so all callers
        share one implementation of the bits + fine-channel routing rules.
        See #689.
        """
        if not profile:
            return
        for offset, value in compute_pan_tilt_writes(pan, tilt, profile):
            self.set_channel(start_addr + offset, value)

    def set_fixture_channels(self, start_addr, channel_values, profile=None):
        """Set arbitrary named channels. channel_values: {type: value}.
        Values are 0-255 for 8-bit, 0-65535 for 16-bit channels."""
        if not profile:
            return
        ch_map = profile.get("channel_map", {})
        channels = profile.get("channels", [])
        for ch_type, value in channel_values.items():
            offset = ch_map.get(ch_type)
            if offset is None:
                continue
            ch_def = next((c for c in channels if c.get("type") == ch_type), None)
            bits = ch_def.get("bits", 8) if ch_def else 8
            if bits == 16:
                val16 = max(0, min(65535, int(value)))
                self.set_channel(start_addr + offset, val16 >> 8)
                self.set_channel(start_addr + offset + 1, val16 & 0xFF)
            else:
                self.set_channel(start_addr + offset, max(0, min(255, int(value))))

    def __len__(self):
        return 512

    def __repr__(self):
        return f"DMXUniverse(universe={self.universe})"


# ── Pan/tilt resolution helpers (#689) ───────────────────────────────────

def compute_pan_tilt_writes(pan, tilt, profile):
    """Compute the (offset, byte) pairs to write pan/tilt at the
    fixture's native resolution.

    ``pan`` / ``tilt`` are normalized 0.0–1.0 (clamped). The profile is
    a dict with ``channel_map`` (type → coarse offset) and ``channels``
    (list of ``{type, offset, bits, …}``).

    A profile is treated as 16-bit when EITHER the coarse channel
    declares ``bits == 16`` OR the profile carries a ``pan-fine`` /
    ``tilt-fine`` sibling channel for the axis. The second case
    matters: the built-in ``movinghead-150w-12ch`` and several
    OFL-imported profiles model pan as two separate channel entries
    (``{"type": "pan"}`` + ``{"type": "pan-fine"}``) without any
    ``bits`` annotation. Before #691 the helper checked only ``bits``
    and silently dropped the LSB — losing 1 part in 256 on every move
    and stranding the operator's Set Home anchor's fine resolution.

    For 16-bit pan/tilt the LSB destination is read from
    ``channel_map["pan-fine"]`` / ``channel_map["tilt-fine"]`` whenever
    the profile provides one — OFL imports can place fine channels at
    arbitrary offsets via ``fineChannelAliases``. Only when no fine
    entry exists do we fall back to ``coarse_offset + 1`` (legacy
    ``bits=16``-without-explicit-fine layouts).

    Returns a list of ``(offset, byte)`` pairs to apply on top of the
    fixture's ``start_addr``. Returns ``[]`` if the profile lacks pan
    and tilt mappings or is missing.
    """
    if not profile:
        return []
    ch_map = profile.get("channel_map") or {}
    channels = profile.get("channels") or []
    writes = []
    for axis, value in (("pan", pan), ("tilt", tilt)):
        coarse_off = ch_map.get(axis)
        if coarse_off is None:
            continue
        ch_def = next((c for c in channels if c.get("type") == axis), None)
        bits = ch_def.get("bits", 8) if ch_def else 8
        fine_off = ch_map.get(f"{axis}-fine")
        # #691 — treat split-channel profiles (e.g. built-in 150w-12ch:
        # separate "pan" and "pan-fine" entries, no bits annotation) as
        # 16-bit. Previously they were misclassified as 8-bit and the
        # fine byte was never written.
        is_16 = (bits == 16) or (fine_off is not None)
        v = 0.0 if value is None else float(value)
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        # int() truncation (not round) matches the project's existing
        # 8-bit conversion convention; tests assert pan=0.5 → 127.
        if is_16:
            v16 = int(v * 65535)
            if v16 < 0:
                v16 = 0
            elif v16 > 65535:
                v16 = 65535
            if fine_off is None:
                # #876 — refuse the contiguous fallback (`coarse_off
                # + 1`) when that slot is already occupied by a typed
                # channel other than the matching `*-fine`. Pre-fix
                # this branch blindly wrote the LSB into the next
                # offset, which on the 350W BeamLight profile (bits=16
                # declared without an explicit pan-fine / tilt-fine
                # sibling) ended up clobbering `tilt` with pan_lo and
                # `pan-tilt-speed` with tilt_lo. The operator's #868
                # smoothing write to pan-tilt-speed never reached the
                # fixture because pan/tilt overwrote it every tick.
                next_ch = next((c for c in channels
                                if c.get("offset") == coarse_off + 1), None)
                next_type = next_ch.get("type") if next_ch else None
                fine_type = f"{axis}-fine"
                if next_ch is not None and next_type not in (None, "", fine_type):
                    log.warning(
                        "compute_pan_tilt_writes: profile declares "
                        "bits=16 on %s but lacks %s; the contiguous "
                        "fallback slot (offset %d) is already typed "
                        "%r — refusing to clobber. Writing coarse "
                        "(8-bit visible resolution) only. Fix the "
                        "profile by either (a) adding an explicit "
                        "%s channel at the correct offset, or (b) "
                        "changing %s bits to 8 if the hardware is "
                        "8-bit-only.",
                        axis, fine_type, coarse_off + 1, next_type,
                        fine_type, axis)
                    writes.append((coarse_off, (v16 >> 8) & 0xFF))
                    continue
                # Slot at coarse_off+1 is empty or is the matching
                # *-fine: legacy contiguous fallback is safe.
                fine_off = coarse_off + 1
            writes.append((coarse_off, (v16 >> 8) & 0xFF))
            writes.append((fine_off,    v16        & 0xFF))
        else:
            v8 = int(v * 255)
            if v8 < 0:
                v8 = 0
            elif v8 > 255:
                v8 = 255
            writes.append((coarse_off, v8))
    return writes


def write_pan_tilt_to_buffer(buf, start_addr, pan, tilt, profile):
    """Apply :func:`compute_pan_tilt_writes` to a 512-byte ``bytearray``.

    Used by the calibration sweep (``mover_calibrator``) which builds a
    fresh universe buffer per probe rather than going through the
    engine's ``DMXUniverse``. ``start_addr`` is 1-based.
    """
    if not profile:
        return
    base = start_addr - 1
    for offset, value in compute_pan_tilt_writes(pan, tilt, profile):
        idx = base + offset
        if 0 <= idx < 512:
            buf[idx] = value
