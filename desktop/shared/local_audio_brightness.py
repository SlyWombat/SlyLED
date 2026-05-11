"""local_audio_brightness.py — #879 local audio input → master brightness.

Parallel to the Android Auto Brightness path (#849 / #861) but with no
phone in the loop. Captures audio from a local input device (default
system mic, or operator-picked device), runs an envelope follower, and
pushes the resulting master value into the orchestrator's brightness
machinery 20× per second via the same coalescing dispatch
(`_handle_autobri_push`).

The module is **optional**: if `sounddevice` isn't installed the import
fails gracefully and the orchestrator continues without the feature.
The Settings UI surfaces the unavailability so the operator knows what's
missing.

Architecture:

  audio device (OS)
    → sounddevice InputStream callback (paFloat32, mono, ~22 kHz)
    → block-RMS (30 ms window)
    → asymmetric one-pole follower (attack 5 ms / release 200 ms)
    → operator gain / floor / ceiling
    → 1-byte master ∈ [0, 255]
    → pushed at 20 Hz via the push callback into _handle_autobri_push
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("slyled.local_audio_brightness")

try:
    import sounddevice as _sd  # type: ignore
    _SD_OK = True
except Exception as _sd_err:
    _sd = None  # type: ignore
    _SD_OK = False
    log.info("sounddevice not available; local-audio brightness disabled "
             "(%s)", _sd_err)


# Capture config. Mono float32, modest sample rate — envelope-follower
# math is band-limited so 22.05 kHz is plenty and keeps CPU low.
_SAMPLE_RATE = 22050
_CHANNELS    = 1
_BLOCK_SIZE  = 512  # ~23 ms at 22050 Hz — matches the RMS window
_PUSH_HZ     = 20   # match the Android path's cadence


# Default envelope-follower coefficients. Operator can override via
# settings; the defaults match the Android implementation so switching
# between sources doesn't visibly change dynamics.
_DEFAULT_ATTACK_MS  = 5.0
_DEFAULT_RELEASE_MS = 200.0


def _coef(time_ms: float, sample_rate: float, block_size: int) -> float:
    """One-pole IIR coefficient for a given time-constant. Operates per
    block (not per sample) since we update the envelope once per RMS
    block. `time_ms` < 1 → instant tracking (coefficient = 1.0)."""
    if time_ms <= 0:
        return 1.0
    # Block period in seconds.
    block_s = block_size / float(sample_rate)
    return 1.0 - math.exp(-block_s / (time_ms / 1000.0))


class LocalAudioBrightness:
    """Captures audio + pushes master brightness values via the
    supplied callback. Operator-tunable settings can be updated live;
    capture restarts only when the device or sample rate changes."""

    def __init__(self, push_fn: Callable[[int, int, int], None]):
        """`push_fn(master, flags, seq)` — pushed at ~20 Hz with the
        coalesced master value (0–255). Same shape as
        `_handle_autobri_push`'s internal dispatch so plumbing
        identical between the local and Android paths."""
        self._push_fn = push_fn
        self._stream = None
        self._lock = threading.Lock()
        self._env_value = 0.0  # current envelope-follower output (0..1)
        self._attack_coef = 1.0
        self._release_coef = 1.0
        self._cfg = {
            "enabled":     False,
            "device":      None,  # OS device name; None = system default
            "gain":        1.5,   # multiplier on raw RMS
            "floor":       0,     # min master ∈ [0, 255]
            "ceiling":     255,   # max master ∈ [0, 255]
            "attackMs":    _DEFAULT_ATTACK_MS,
            "releaseMs":   _DEFAULT_RELEASE_MS,
        }
        self._last_master = 0
        self._seq = 0
        self._pusher_thread = None
        self._pusher_stop = threading.Event()
        self._last_error: Optional[str] = None
        self._device_in_use: Optional[str] = None
        self._recompute_coefs()

    # ── Public API ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        return _SD_OK

    def get_status(self) -> dict:
        with self._lock:
            return {
                "available":     _SD_OK,
                "enabled":       self._cfg["enabled"],
                "device":        self._cfg["device"],
                "deviceInUse":   self._device_in_use,
                "currentMaster": self._last_master,
                "envelope":      round(self._env_value, 4),
                "config":        dict(self._cfg),
                "lastError":     self._last_error,
            }

    def list_devices(self) -> list:
        """Return [(index, name, isDefault)] for every input device. Empty
        list if sounddevice isn't installed."""
        if not _SD_OK:
            return []
        try:
            devices = _sd.query_devices()
            default_in = _sd.default.device[0]
            out = []
            for i, d in enumerate(devices):
                if d.get("max_input_channels", 0) <= 0:
                    continue
                out.append({
                    "index":      i,
                    "name":       d.get("name", f"device {i}"),
                    "isDefault":  (i == default_in),
                    "channels":   d.get("max_input_channels", 0),
                })
            return out
        except Exception as e:
            log.warning("list_devices failed: %s", e)
            return []

    def update_config(self, cfg: dict) -> dict:
        """Merge `cfg` into the running config and (re)start capture if
        needed. Returns the new effective config."""
        restart = False
        with self._lock:
            for key in ("enabled", "device", "gain", "floor",
                        "ceiling", "attackMs", "releaseMs"):
                if key in cfg:
                    new_val = cfg[key]
                    # Clamp the numeric sliders to sane ranges.
                    if key == "gain":
                        new_val = max(0.0, min(10.0, float(new_val)))
                    elif key in ("floor", "ceiling"):
                        new_val = max(0, min(255, int(new_val)))
                    elif key in ("attackMs", "releaseMs"):
                        new_val = max(0.0, min(2000.0, float(new_val)))
                    if key in ("device",) and new_val != self._cfg[key]:
                        restart = True
                    if key == "enabled" and bool(new_val) != self._cfg[key]:
                        restart = True
                    self._cfg[key] = new_val
            # `floor` must not exceed `ceiling`.
            if self._cfg["floor"] > self._cfg["ceiling"]:
                self._cfg["floor"] = self._cfg["ceiling"]
            self._recompute_coefs()
        if restart:
            self._restart_capture()
        return self.get_status()

    def shutdown(self) -> None:
        """Tear down the stream + push thread on orchestrator exit."""
        self._stop_capture()
        self._pusher_stop.set()
        if self._pusher_thread is not None:
            self._pusher_thread.join(timeout=1.0)

    # ── Internals ───────────────────────────────────────────────────

    def _recompute_coefs(self) -> None:
        self._attack_coef = _coef(self._cfg["attackMs"],
                                   _SAMPLE_RATE, _BLOCK_SIZE)
        self._release_coef = _coef(self._cfg["releaseMs"],
                                    _SAMPLE_RATE, _BLOCK_SIZE)

    def _audio_callback(self, indata, _frames, _time_info, _status):
        """`sounddevice` InputStream callback. Runs in a high-priority
        audio thread — keep it fast (no logging, no allocations beyond
        numpy ops)."""
        try:
            # indata: shape (frames, channels), float32, range ~[-1, 1].
            # Mono RMS over the block.
            block = indata[:, 0] if indata.ndim > 1 else indata
            # `(block ** 2).mean()` returns numpy float; convert at the
            # end so the envelope arithmetic stays Python float.
            mean_sq = float((block * block).mean())
            rms = math.sqrt(mean_sq) if mean_sq > 0 else 0.0
            with self._lock:
                gained = rms * self._cfg["gain"]
                if gained > self._env_value:
                    coef = self._attack_coef
                else:
                    coef = self._release_coef
                self._env_value += coef * (gained - self._env_value)
        except Exception as e:
            # Audio thread MUST never raise back into PortAudio.
            self._last_error = f"audio callback: {e}"

    def _restart_capture(self) -> None:
        self._stop_capture()
        if not self._cfg["enabled"]:
            self._device_in_use = None
            return
        if not _SD_OK:
            self._last_error = "sounddevice not installed"
            return
        try:
            device = self._cfg["device"]  # None = system default
            self._stream = _sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype="float32",
                blocksize=_BLOCK_SIZE,
                device=device,
                callback=self._audio_callback,
            )
            self._stream.start()
            try:
                devices = _sd.query_devices()
                actual = self._stream.device
                idx = actual[0] if isinstance(actual, (tuple, list)) else actual
                self._device_in_use = devices[idx].get("name", str(actual))
            except Exception:
                self._device_in_use = str(self._cfg["device"])
            self._last_error = None
            log.info("LocalAudioBrightness capture started (device=%r)",
                     self._device_in_use)
            # Lazily start the push thread.
            if self._pusher_thread is None or not self._pusher_thread.is_alive():
                self._pusher_stop.clear()
                self._pusher_thread = threading.Thread(
                    target=self._pusher_loop, daemon=True,
                    name="local-audio-bri-push")
                self._pusher_thread.start()
        except Exception as e:
            log.error("LocalAudioBrightness capture failed: %s", e)
            self._last_error = str(e)
            self._stream = None
            self._device_in_use = None

    def _stop_capture(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.debug("stream close failed: %s", e)
            self._stream = None

    def _pusher_loop(self) -> None:
        """20 Hz loop: read envelope, map to master byte, fire push_fn."""
        period = 1.0 / float(_PUSH_HZ)
        next_tick = time.monotonic()
        while not self._pusher_stop.is_set():
            now = time.monotonic()
            sleep_for = next_tick - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            next_tick += period
            with self._lock:
                if not self._cfg["enabled"] or self._stream is None:
                    continue
                env = self._env_value  # 0..1 (in theory; clip below)
                floor = self._cfg["floor"]
                ceiling = self._cfg["ceiling"]
            # Map envelope (0..1) to master byte through the operator's
            # floor/ceiling window. Envelope > 1 (over-driven gain) is
            # clipped at ceiling.
            env_clipped = max(0.0, min(1.0, env))
            master = int(round(floor + env_clipped * (ceiling - floor)))
            master = max(0, min(255, master))
            with self._lock:
                self._last_master = master
                self._seq = (self._seq + 1) & 0xFF
                seq = self._seq
            try:
                self._push_fn(master, 0, seq)
            except Exception as e:
                log.debug("push_fn raised: %s", e)
