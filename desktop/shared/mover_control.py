"""mover_control.py — DMX consumer of the remote-orientation primitive.

Per #484 phase 4 this file is **feature #1 — mover-follow**. It reads
`Remote.aim_stage` from `remote_orientation.py`, runs pan/tilt IK against
the target mover's pose, and writes DMX. No Euler math, no delta
references, no per-axis scale multipliers — the primitive owns orientation.

The engine retains:
- Claim lifecycle (`claim`, `release`, `start_stream`) — so only one device
  controls a mover at a time.
- Colour / dimmer / strobe state (`set_color`, `flash`).
- The 40 Hz DMX write tick.

See docs/gyro-stage-space.md §5 for the full architecture.
"""

import logging
import threading
import time

from mover_calibrator import aim_to_pan_tilt, affine_pan_tilt

log = logging.getLogger("slyled.mover_control")


class MoverClaim:
    """Per-mover claim — ties a device id to a mover id for DMX writes."""

    __slots__ = (
        "mover_id", "device_id", "device_name", "device_type",
        "claimed_at", "last_write_ts", "ttl_s", "state",
        "color_r", "color_g", "color_b", "dimmer", "strobe_active",
        "pan_smooth", "tilt_smooth", "smoothing",
    )

    def __init__(self, mover_id, device_id, device_name, device_type="gyro",
                 smoothing=0.15):
        self.mover_id = mover_id
        self.device_id = device_id
        self.device_name = device_name
        self.device_type = device_type
        self.claimed_at = time.time()
        self.last_write_ts = time.time()
        self.ttl_s = 15.0
        self.state = "claimed"  # claimed | streaming | calibrating

        # Colour / dimmer / strobe — defaults match "white, full, no strobe"
        self.color_r = 255
        self.color_g = 255
        self.color_b = 255
        self.dimmer = 255
        self.strobe_active = False

        # Smoothed pan/tilt in normalised [0,1]. Initialised to centre.
        self.pan_smooth = 0.5
        self.tilt_smooth = 0.5
        self.smoothing = smoothing

    def to_dict(self):
        return {
            "moverId":      self.mover_id,
            "deviceId":     self.device_id,
            "deviceName":   self.device_name,
            "deviceType":   self.device_type,
            "state":        self.state,
            "lastWriteAge": round(time.time() - self.last_write_ts, 1),
            "panNorm":      round(self.pan_smooth, 4),
            "tiltNorm":     round(self.tilt_smooth, 4),
            "color":        {"r": self.color_r, "g": self.color_g, "b": self.color_b},
            "dimmer":       self.dimmer,
        }


class MoverControlEngine:
    """Mover-follow consumer of the remote-orientation primitive."""

    def __init__(self, get_fixtures, get_layout, get_profile_info,
                 get_engine, set_fixture_color_fn, get_remote_by_device_id,
                 get_mover_cal=None):
        """
        Args:
            get_fixtures:             list of fixtures
            get_layout:               layout dict (unused here; kept for symmetry)
            get_profile_info(pid):    DMX profile info dict
            get_engine():             running ArtNet/sACN engine or None
            set_fixture_color_fn:     (engine, uni, addr, r, g, b, prof_info) writer
            get_remote_by_device_id:  callable(device_id) → Remote | None
            get_mover_cal:            callable(mover_id) → calibration dict or None.
                                      When provided, the tick prefers `affine_pan_tilt`
                                      against the calibrated samples over pure IK.
        """
        self._get_fixtures = get_fixtures
        self._get_layout = get_layout
        self._get_profile_info = get_profile_info
        self._get_engine = get_engine
        self._set_fixture_color = set_fixture_color_fn
        self._get_remote = get_remote_by_device_id
        self._get_mover_cal = get_mover_cal or (lambda _mid: None)

        self._claims = {}  # mover_id → MoverClaim
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    # ── Lifecycle ────────────────────────────────────────────────────

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
              smoothing=0.15):
        with self._lock:
            existing = self._claims.get(mover_id)
            if existing and existing.device_id != device_id:
                age = time.time() - existing.last_write_ts
                if age < existing.ttl_s:
                    return False, f"Claimed by {existing.device_name} ({existing.device_type})"
                log.info("Mover %d: TTL expired for %s, releasing",
                         mover_id, existing.device_id)

            claim = MoverClaim(mover_id, device_id, device_name, device_type,
                               smoothing=smoothing)
            self._claims[mover_id] = claim
            log.info("Mover %d claimed by %s (%s)",
                     mover_id, device_name, device_type)
            return True, "ok"

    def release(self, mover_id, device_id=None, blackout=True):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim:
                return True
            if device_id and claim.device_id != device_id:
                return False
            del self._claims[mover_id]
        if blackout:
            self._blackout_mover(mover_id)
        log.info("Mover %d released", mover_id)
        return True

    def start_stream(self, mover_id, device_id):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.state = "streaming"
            claim.last_write_ts = time.time()
        log.info("Mover %d: streaming started by %s", mover_id, device_id)
        return True

    # ── Calibrate (lifecycle only — the primitive does the math) ─────

    def calibrate_start(self, mover_id, device_id):
        """Mark state=calibrating so the tick holds DMX steady during the
        operator's alignment window. Actual `R_world_to_stage` computation
        happens on the `Remote` object (see remote_orientation.py)."""
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.state = "calibrating"
            claim.last_write_ts = time.time()
        log.info("Mover %d: calibrate-start", mover_id)
        return True

    def calibrate_end(self, mover_id, device_id):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.state = "streaming"
            claim.last_write_ts = time.time()
        log.info("Mover %d: calibrate-end — consuming primitive", mover_id)
        return True

    # ── Colour / strobe ──────────────────────────────────────────────

    def set_color(self, mover_id, device_id, r, g, b, dimmer=None):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.color_r = r
            claim.color_g = g
            claim.color_b = b
            claim.strobe_active = False
            if dimmer is not None:
                claim.dimmer = dimmer
        return True

    def flash(self, mover_id, device_id):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.strobe_active = True
            claim.dimmer = 255
        return True

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self):
        with self._lock:
            return [c.to_dict() for c in self._claims.values()]

    def get_claim(self, mover_id):
        with self._lock:
            c = self._claims.get(mover_id)
            return c.to_dict() if c else None

    # ── 40 Hz tick ───────────────────────────────────────────────────

    def _tick_loop(self):
        interval = 0.025
        while self._running:
            start = time.monotonic()
            self._tick()
            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _tick(self):
        with self._lock:
            claims = list(self._claims.items())

        for mover_id, claim in claims:
            mover = self._get_mover(mover_id)
            if mover is None:
                continue
            prof_info = self._get_profile_info(mover.get("dmxProfileId")) \
                if mover.get("dmxProfileId") else None

            if claim.state == "claimed":
                # Locked but user hasn't started streaming — don't write DMX.
                continue

            if claim.state == "streaming":
                remote = self._get_remote(claim.device_id)
                if remote is not None and remote.aim_stage is not None \
                        and remote.stale_reason is None:
                    pan_norm, tilt_norm = self._aim_to_pan_tilt(
                        mover_id, mover, remote.aim_stage,
                    )
                    alpha = max(0.0, min(1.0, 1.0 - claim.smoothing))
                    claim.pan_smooth  += alpha * (pan_norm  - claim.pan_smooth)
                    claim.tilt_smooth += alpha * (tilt_norm - claim.tilt_smooth)

            # Write DMX for streaming + calibrating (holds position while
            # operator aligns the puck during calibration).
            self._write_dmx(mover, prof_info, claim)
            claim.last_write_ts = time.time()

    # ── DMX writers ─────────────────────────────────────────────────

    def _get_mover(self, mover_id):
        for f in self._get_fixtures():
            if f["id"] == mover_id and f.get("fixtureType") == "dmx":
                return f
        return None

    def _aim_to_pan_tilt(self, mover_id, mover, aim_stage):
        """Pan/tilt from a stage-space aim vector, preferring the fixture's
        calibration grid when present. Projects `aim_stage` 3 m from the
        fixture to get a target point, then uses `affine_pan_tilt` against
        the saved samples. Falls back to the pure `aim_to_pan_tilt` IK
        when no usable calibration exists."""
        cal = self._get_mover_cal(mover_id)
        if cal and cal.get("samples") and len(cal["samples"]) >= 2:
            fx = mover.get("x", 0)
            fy = mover.get("y", 0)
            fz = mover.get("z", 0)
            tx = fx + aim_stage[0] * 3000.0
            ty = fy + aim_stage[1] * 3000.0
            tz = fz + aim_stage[2] * 3000.0
            pt = affine_pan_tilt(cal["samples"], tx, ty, tz)
            if pt is not None:
                return pt
        return aim_to_pan_tilt(
            aim_stage,
            mount_rotation_deg=mover.get("rotation") or [0, 0, 0],
            pan_range=mover.get("panRange") or 540,
            tilt_range=mover.get("tiltRange") or 270,
        )

    def _write_dmx(self, mover, prof_info, claim):
        engine = self._get_engine()
        if not engine or not engine.running:
            return
        if not prof_info:
            return
        uni = mover.get("dmxUniverse", 1)
        addr = mover.get("dmxStartAddr", 1)
        profile = {"channel_map": prof_info.get("channel_map", {}),
                   "channels": prof_info.get("channels", [])}
        uni_buf = engine.get_universe(uni)

        uni_buf.set_fixture_pan_tilt(addr, claim.pan_smooth, claim.tilt_smooth, profile)
        uni_buf.set_fixture_dimmer(addr, claim.dimmer, profile)
        self._set_fixture_color(engine, uni, addr,
                                claim.color_r, claim.color_g, claim.color_b,
                                prof_info)

        # Channel defaults + strobe override.
        for ch in prof_info.get("channels", []):
            ch_type = ch.get("type", "")
            if ch_type in ("pan", "tilt", "dimmer", "red", "green", "blue",
                           "color-wheel"):
                continue
            default = ch.get("default")
            # Strobe: always honour the strobe_active flag, even when the
            # profile's default is None (which many fixtures leave blank).
            if ch_type == "strobe":
                if claim.strobe_active:
                    uni_buf.set_channel(addr + ch.get("offset", 0),
                                        self._find_strobe_value(ch))
                elif default is not None and default > 0:
                    uni_buf.set_channel(addr + ch.get("offset", 0), int(default))
                continue
            # Other channels: write the profile default when present.
            if default is None or default <= 0:
                continue
            uni_buf.set_channel(addr + ch.get("offset", 0), int(default))

    @staticmethod
    def _find_strobe_value(ch):
        """DMX value to send when strobe_active. Prefers a ShutterStrobe
        capability with 'strobe' in its label, falls back to channel
        midpoint 128 otherwise (works for most generic intensity-mapped
        strobe channels)."""
        for cap in ch.get("capabilities", []) or []:
            label = (cap.get("label") or "").lower()
            rng = cap.get("range", [0, 255])
            if cap.get("type") == "ShutterStrobe" and "strobe" in label:
                return (rng[0] + rng[1]) // 2
        return 128

    def _blackout_mover(self, mover_id):
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
