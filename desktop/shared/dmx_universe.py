"""
DMXUniverse — Thread-safe 512-byte buffer for a single DMX-512 universe.

Used by both ArtNetEngine and sACN_Engine for channel data management.
"""

import threading


class DMXUniverse:
    """Thread-safe 512-channel DMX universe buffer."""

    __slots__ = ("_data", "_lock", "universe", "dirty")

    def __init__(self, universe=1):
        self.universe = universe
        self._data = bytearray(512)
        self._lock = threading.Lock()
        self.dirty = False  # set True on write, cleared by engine after send

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
        """Set RGB channels for a fixture at *start_addr* (1-based).
        If *profile* is given, uses its channel mapping; otherwise assumes
        consecutive R, G, B at the start address."""
        if profile:
            ch_map = profile.get("channel_map", {})
            r_off = ch_map.get("red")
            g_off = ch_map.get("green")
            b_off = ch_map.get("blue")
            if r_off is not None:
                self.set_channel(start_addr + r_off, r)
            if g_off is not None:
                self.set_channel(start_addr + g_off, g)
            if b_off is not None:
                self.set_channel(start_addr + b_off, b)
        else:
            self.set_channels(start_addr, [r, g, b])

    def set_fixture_dimmer(self, start_addr, value, profile=None):
        """Set dimmer channel for a fixture."""
        if profile:
            off = profile.get("channel_map", {}).get("dimmer")
            if off is not None:
                self.set_channel(start_addr + off, value)

    def __len__(self):
        return 512

    def __repr__(self):
        return f"DMXUniverse(universe={self.universe})"
