"""
mover_control.py — Unified moving head control engine.

Handles claim/release, calibrate (hold-to-align), orientation streaming,
and color for both ESP32 gyro boards (via UDP translation) and Android
phones (via HTTP). Replaces gyro_engine.py.

One device controls one mover at a time. The calibrate flow captures
device orientation + current mover pan/tilt as a reference pair; subsequent
orientation deltas are mapped to pan/tilt deltas from that reference.
"""

import logging
import math
import threading
import time

log = logging.getLogger("slyled.mover_control")


class MoverClaim:
    """Per-mover claim state."""
    __slots__ = (
        "mover_id", "device_id", "device_name", "device_type",
        "claimed_at", "last_data_ts", "ttl_s", "state",
        "ref_roll", "ref_pitch", "ref_yaw",
        "ref_pan", "ref_tilt", "calibrated",
        "cur_roll", "cur_pitch", "cur_yaw",
        "color_r", "color_g", "color_b", "dimmer",
        "pan_smooth", "tilt_smooth",
        "pan_scale", "tilt_scale", "smoothing",
    )

    def __init__(self, mover_id, device_id, device_name, device_type="gyro",
                 pan_scale=1.0, tilt_scale=1.0, smoothing=0.15):
        self.mover_id = mover_id
        self.device_id = device_id
        self.device_name = device_name
        self.device_type = device_type
        self.claimed_at = time.time()
        self.last_data_ts = time.time()
        self.ttl_s = 30.0
        self.state = "claimed"  # claimed | streaming | calibrating

        # Reference pair (set during calibrate)
        self.ref_roll = 0.0
        self.ref_pitch = 0.0
        self.ref_yaw = 0.0
        self.ref_pan = 0.5
        self.ref_tilt = 0.5
        self.calibrated = False

        # Latest device orientation
        self.cur_roll = 0.0
        self.cur_pitch = 0.0
        self.cur_yaw = 0.0

        # Color
        self.color_r = 255
        self.color_g = 255
        self.color_b = 255
        self.dimmer = 255

        # Smoothing
        self.pan_smooth = 0.5
        self.tilt_smooth = 0.5
        self.pan_scale = pan_scale
        self.tilt_scale = tilt_scale
        self.smoothing = smoothing

    def to_dict(self):
        return {
            "moverId": self.mover_id,
            "deviceId": self.device_id,
            "deviceName": self.device_name,
            "deviceType": self.device_type,
            "state": self.state,
            "calibrated": self.calibrated,
            "lastDataAge": round(time.time() - self.last_data_ts, 1),
            "panNorm": round(self.pan_smooth, 4),
            "tiltNorm": round(self.tilt_smooth, 4),
            "color": {"r": self.color_r, "g": self.color_g, "b": self.color_b},
            "dimmer": self.dimmer,
        }


class MoverControlEngine:
    """Unified moving head control — claim, calibrate, stream orientation."""

    def __init__(self, get_fixtures, get_layout, get_profile_info,
                 get_engine, set_fixture_color_fn):
        """
        Args:
            get_fixtures: callable → list of fixture dicts
            get_layout: callable → layout dict with children positions
            get_profile_info: callable(profile_id) → profile info dict
            get_engine: callable → running ArtNet/sACN engine (or None)
            set_fixture_color_fn: callable(engine, uni, addr, r, g, b, prof_info)
        """
        self._get_fixtures = get_fixtures
        self._get_layout = get_layout
        self._get_profile_info = get_profile_info
        self._get_engine = get_engine
        self._set_fixture_color = set_fixture_color_fn

        self._claims = {}  # mover_id → MoverClaim
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        log.info("MoverControlEngine started (40Hz)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    # ── Claim / Release ──────────────────────────────────────────────

    def claim(self, mover_id, device_id, device_name, device_type="gyro",
              pan_scale=1.0, tilt_scale=1.0, smoothing=0.15):
        with self._lock:
            existing = self._claims.get(mover_id)
            if existing and existing.device_id != device_id:
                age = time.time() - existing.last_data_ts
                if age < existing.ttl_s:
                    return False, f"Claimed by {existing.device_name} ({existing.device_type})"
                # TTL expired — auto-release
                log.info("Mover %d: TTL expired for %s, releasing", mover_id, existing.device_id)

            claim = MoverClaim(mover_id, device_id, device_name, device_type,
                               pan_scale, tilt_scale, smoothing)
            self._claims[mover_id] = claim
            log.info("Mover %d claimed by %s (%s)", mover_id, device_name, device_type)
            return True, "ok"

    def release(self, mover_id, device_id=None, blackout=True):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim:
                return True
            if device_id and claim.device_id != device_id:
                return False  # not the owner
            del self._claims[mover_id]
        if blackout:
            self._blackout_mover(mover_id)
        log.info("Mover %d released", mover_id)
        return True

    # ── Start streaming ──────────────────────────────────────────────

    def start_stream(self, mover_id, device_id):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.state = "streaming"
            claim.last_data_ts = time.time()
        # Turn on light
        self._set_mover_light(mover_id, claim)
        log.info("Mover %d: streaming started by %s", mover_id, device_id)
        return True

    # ── Calibrate ────────────────────────────────────────────────────

    def calibrate_start(self, mover_id, device_id, roll, pitch, yaw):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return None
            claim.state = "calibrating"
            # Capture current device orientation
            claim.ref_roll = roll
            claim.ref_pitch = pitch
            claim.ref_yaw = yaw
            # Capture current mover pan/tilt
            claim.ref_pan = claim.pan_smooth
            claim.ref_tilt = claim.tilt_smooth
            claim.last_data_ts = time.time()
        log.info("Mover %d: calibrate start — ref orient=(%.1f,%.1f,%.1f) ref pt=(%.3f,%.3f)",
                 mover_id, roll, pitch, yaw, claim.ref_pan, claim.ref_tilt)
        return {"refPan": claim.ref_pan, "refTilt": claim.ref_tilt}

    def calibrate_end(self, mover_id, device_id, roll, pitch, yaw):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            # Update reference to the release orientation
            claim.ref_roll = roll
            claim.ref_pitch = pitch
            claim.ref_yaw = yaw
            claim.calibrated = True
            claim.state = "streaming"
            claim.last_data_ts = time.time()
        log.info("Mover %d: calibrate end — locked ref orient=(%.1f,%.1f,%.1f)",
                 mover_id, roll, pitch, yaw)
        return True

    # ── Orient ───────────────────────────────────────────────────────

    def orient(self, mover_id, device_id, roll, pitch, yaw):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.cur_roll = roll
            claim.cur_pitch = pitch
            claim.cur_yaw = yaw
            claim.last_data_ts = time.time()
        return True

    # ── Color ────────────────────────────────────────────────────────

    def set_color(self, mover_id, device_id, r, g, b, dimmer=None):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.color_r = r
            claim.color_g = g
            claim.color_b = b
            if dimmer is not None:
                claim.dimmer = dimmer
        return True

    # ── Flash ────────────────────────────────────────────────────────

    def flash(self, mover_id, device_id):
        """Brief white flash — sets dimmer to 255 + white, then restores after 150ms."""
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            saved_r, saved_g, saved_b = claim.color_r, claim.color_g, claim.color_b
            claim.color_r = 255
            claim.color_g = 255
            claim.color_b = 255
            claim.dimmer = 255
        # Restore after brief pulse
        def _restore():
            time.sleep(0.15)
            with self._lock:
                c = self._claims.get(mover_id)
                if c and c.device_id == device_id:
                    c.color_r = saved_r
                    c.color_g = saved_g
                    c.color_b = saved_b
        import threading
        threading.Thread(target=_restore, daemon=True).start()
        return True

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self):
        with self._lock:
            return [c.to_dict() for c in self._claims.values()]

    def get_claim(self, mover_id):
        with self._lock:
            c = self._claims.get(mover_id)
            return c.to_dict() if c else None

    # ── Internal: 40Hz tick ──────────────────────────────────────────

    def _tick_loop(self):
        interval = 0.025  # 40Hz
        while self._running:
            start = time.monotonic()
            self._tick()
            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _tick(self):
        now = time.time()
        expired = []

        with self._lock:
            claims = list(self._claims.items())

        for mover_id, claim in claims:
            # TTL check
            if now - claim.last_data_ts > claim.ttl_s:
                expired.append((mover_id, claim.device_id))
                continue

            # Get mover + profile (needed for all states)
            mover = self._get_mover(mover_id)
            if not mover:
                continue
            pid = mover.get("dmxProfileId")
            prof_info = self._get_profile_info(pid) if pid else None

            if claim.state == "calibrating":
                # During calibrate hold, keep light on at current position
                self._write_dmx(mover_id, mover, prof_info, claim)
                continue

            if claim.calibrated and claim.state == "streaming":
                # Compute delta from reference
                d_roll = claim.cur_roll - claim.ref_roll
                d_pitch = claim.cur_pitch - claim.ref_pitch

                pan_range = (prof_info or {}).get("panRange", 540)
                tilt_range = (prof_info or {}).get("tiltRange", 270)

                if pan_range > 0 and tilt_range > 0:
                    d_pan = (d_roll * claim.pan_scale) / pan_range
                    d_tilt = (d_pitch * claim.tilt_scale) / tilt_range
                    pan = max(0.0, min(1.0, claim.ref_pan + d_pan))
                    tilt = max(0.0, min(1.0, claim.ref_tilt + d_tilt))

                    alpha = claim.smoothing
                    claim.pan_smooth += alpha * (pan - claim.pan_smooth)
                    claim.tilt_smooth += alpha * (tilt - claim.tilt_smooth)

            # Always write DMX — holds position + updates color/dimmer even before calibration
            self._write_dmx(mover_id, mover, prof_info, claim)

        # Release expired claims
        for mid, did in expired:
            self.release(mid, did, blackout=True)

    def _get_mover(self, mover_id):
        for f in self._get_fixtures():
            if f["id"] == mover_id and f.get("fixtureType") == "dmx":
                return f
        return None

    def _write_dmx(self, mover_id, mover, prof_info, claim):
        engine = self._get_engine()
        if not engine or not engine.running:
            return
        uni = mover.get("dmxUniverse", 1)
        addr = mover.get("dmxStartAddr", 1)
        if not prof_info:
            return
        profile = {"channel_map": prof_info.get("channel_map", {}),
                   "channels": prof_info.get("channels", [])}
        uni_buf = engine.get_universe(uni)

        # Pan/tilt
        uni_buf.set_fixture_pan_tilt(addr, claim.pan_smooth, claim.tilt_smooth, profile)

        # Dimmer
        uni_buf.set_fixture_dimmer(addr, claim.dimmer, profile)

        # Color (RGB or color-wheel)
        self._set_fixture_color(engine, uni, addr,
                                claim.color_r, claim.color_g, claim.color_b,
                                prof_info)

        # Channel defaults (strobe open etc.)
        cm = prof_info.get("channel_map", {})
        for ch in prof_info.get("channels", []):
            ch_type = ch.get("type", "")
            default = ch.get("default")
            if default is not None and default > 0 and ch_type not in (
                    "pan", "tilt", "dimmer", "red", "green", "blue", "color-wheel"):
                uni_buf.set_channel(addr + ch.get("offset", 0), int(default))

    def _set_mover_light(self, mover_id, claim):
        """Turn on the mover's light when streaming starts."""
        mover = self._get_mover(mover_id)
        if not mover:
            return
        engine = self._get_engine()
        if not engine or not engine.running:
            return
        pid = mover.get("dmxProfileId")
        prof_info = self._get_profile_info(pid) if pid else None
        if prof_info:
            self._write_dmx(mover_id, mover, prof_info, claim)

    def _blackout_mover(self, mover_id):
        """Blackout a mover when released."""
        mover = self._get_mover(mover_id)
        if not mover:
            return
        engine = self._get_engine()
        if not engine or not engine.running:
            return
        uni = mover.get("dmxUniverse", 1)
        addr = mover.get("dmxStartAddr", 1)
        pid = mover.get("dmxProfileId")
        prof_info = self._get_profile_info(pid) if pid else None
        if not prof_info:
            return
        profile = {"channel_map": prof_info.get("channel_map", {}),
                   "channels": prof_info.get("channels", [])}
        uni_buf = engine.get_universe(uni)
        uni_buf.set_fixture_dimmer(addr, 0, profile)
