"""
gyro_engine.py — Gyro-to-DMX bridge for SlyLED moving head control.

The GyroEngine runs a background thread that reads live orientation data from
_gyro_state (populated by the UDP listener in parent_server.py) and writes pan/
tilt DMX values to the ArtNet/sACN output engines for the assigned mover fixture.

Mapping model:
  pan_raw  = roll  + panOffsetDeg   (gyro board tilt left/right)
  tilt_raw = pitch + tiltOffsetDeg  (gyro board tilt forward/back)

  pan_dmx  = panCenter  + clip(pan_raw  / panScale,  −127, +127)
  tilt_dmx = tiltCenter + clip(tilt_raw / tiltScale, −127, +127)

  EMA smoothing:
    pan_smooth  += smoothing * (pan_dmx  − pan_smooth)
    tilt_smooth += smoothing * (tilt_dmx − tilt_smooth)

  Final DMX values are clamped to [0, 255].

One GyroEngine instance manages all gyro fixtures; each assignment is a dict:
  {fixture_id, gyro_ip, dmx_fixture_id, params}

Integration: import and call `get_gyro_engine()` from parent_server.py after
_artnet / _sacn are initialized.  The engine starts lazily on first assignment.
"""

import math
import struct
import threading
import time
from typing import Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────

GYRO_STALE_S   = 2.0   # orientation data older than this is ignored
ENGINE_HZ      = 25    # background thread target update rate
ENGINE_PERIOD  = 1.0 / ENGINE_HZ

# ── Engine ────────────────────────────────────────────────────────────────────

class GyroEngine:
    """Background thread: reads gyro state → writes DMX pan/tilt values."""

    def __init__(self, artnet_engine, sacn_engine, gyro_state_ref, gyro_lock_ref,
                 fixtures_ref, children_ref):
        """
        Parameters
        ----------
        artnet_engine   : ArtNetEngine instance from parent_server
        sacn_engine     : sACNEngine instance from parent_server
        gyro_state_ref  : reference to _gyro_state dict (mutated by UDP listener)
        gyro_lock_ref   : threading.Lock protecting _gyro_state
        fixtures_ref    : reference to _fixtures list
        children_ref    : reference to _children list
        """
        self._artnet  = artnet_engine
        self._sacn    = sacn_engine
        self._gstate  = gyro_state_ref
        self._glock   = gyro_lock_ref
        self._fixtures = fixtures_ref
        self._children = children_ref

        self._lock    = threading.Lock()
        self._running = False
        self._thread = None  # type: Optional[threading.Thread]

        # EMA state keyed by gyro fixture_id
        self._pan_smooth:  Dict[int, float] = {}
        self._tilt_smooth: Dict[int, float] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="GyroEngine")
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False

    def is_running(self) -> bool:
        return self._running

    # ── Public helpers ────────────────────────────────────────────────────────

    def get_status(self) -> List[dict]:
        """Return a list of status dicts for all gyro fixtures."""
        now = time.time()
        result = []
        for f in self._fixtures:
            if f.get("fixtureType") != "gyro":
                continue
            gip = self._resolve_gyro_ip(f)
            with self._glock:
                g = self._gstate.get(gip) if gip else None
            stale = (g is None) or ((now - g["ts"]) > GYRO_STALE_S)
            result.append({
                "fixtureId":       f["id"],
                "name":            f.get("name", ""),
                "gyroIp":          gip,
                "assignedMoverId": f.get("assignedMoverId"),
                "enabled":         f.get("gyroEnabled", False),
                "stale":           stale,
                "roll":            round(g["roll"], 2)  if g and not stale else None,
                "pitch":           round(g["pitch"], 2) if g and not stale else None,
                "yaw":             round(g["yaw"], 2)   if g and not stale else None,
                "fps":             g["fps"]             if g and not stale else 0,
            })
        return result

    def update_assignment(self, fixture_id: int):
        """Call when a gyro fixture's config changes (enable/disable, params, etc.)."""
        # Reset EMA state so stale smoothing doesn't leak into new config
        with self._lock:
            self._pan_smooth.pop(fixture_id, None)
            self._tilt_smooth.pop(fixture_id, None)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _resolve_gyro_ip(self, fixture: dict) -> Optional[str]:
        """Return the IP of the gyro child assigned to this fixture."""
        cid = fixture.get("gyroChildId")
        if cid is None:
            return None
        c = next((c for c in self._children if c.get("id") == cid), None)
        return c.get("ip") if c else None

    def _resolve_mover(self, fixture: dict) -> Optional[dict]:
        """Return the DMX fixture assigned as the mover."""
        mid = fixture.get("assignedMoverId")
        if mid is None:
            return None
        return next((f for f in self._fixtures
                     if f.get("id") == mid and f.get("fixtureType") == "dmx"), None)

    @staticmethod
    def gyro_to_pan_tilt(roll,         # type: float
                         pitch,        # type: float
                         pan_center,   # type: float
                         tilt_center,  # type: float
                         pan_scale,    # type: float
                         tilt_scale,   # type: float
                         pan_offset_deg,   # type: float
                         tilt_offset_deg): # type: float
        # type: (...) -> tuple
        """
        Convert gyro roll/pitch angles to raw DMX pan/tilt values.

        Returns (pan_dmx_raw, tilt_dmx_raw) as floats before smoothing/clamping.
        pan_scale and tilt_scale are degrees-per-DMX-step (e.g. 1.4 → 1.4°/step).
        """
        pan_deg  = roll  + pan_offset_deg
        tilt_deg = pitch + tilt_offset_deg

        # Convert angles to DMX steps; clip to ±127 to stay within 0-255 range
        pan_step  = max(-127.0, min(127.0, pan_deg  / pan_scale  if pan_scale  != 0 else 0.0))
        tilt_step = max(-127.0, min(127.0, tilt_deg / tilt_scale if tilt_scale != 0 else 0.0))

        return pan_center + pan_step, tilt_center + tilt_step

    def _loop(self):
        while self._running:
            t0 = time.time()
            try:
                self._tick()
            except Exception:
                pass  # never crash the engine thread
            elapsed = time.time() - t0
            sleep_s = ENGINE_PERIOD - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)

    def _tick(self):
        now = time.time()
        for f in list(self._fixtures):
            if f.get("fixtureType") != "gyro":
                continue
            if not f.get("gyroEnabled", False):
                continue

            gip = self._resolve_gyro_ip(f)
            if not gip:
                continue

            with self._glock:
                g = self._gstate.get(gip)

            if g is None or (now - g["ts"]) > GYRO_STALE_S:
                continue  # stale — hold last DMX value; don't write zeros

            mover = self._resolve_mover(f)
            if not mover:
                continue

            # Compute raw DMX pan/tilt
            pan_raw, tilt_raw = self.gyro_to_pan_tilt(
                g["roll"], g["pitch"],
                float(f.get("panCenter",    128)),
                float(f.get("tiltCenter",   128)),
                float(f.get("panScale",     1.0)),
                float(f.get("tiltScale",    1.0)),
                float(f.get("panOffsetDeg", 0.0)),
                float(f.get("tiltOffsetDeg",0.0)),
            )

            # EMA smoothing
            alpha = float(f.get("smoothing", 0.15))
            fid   = f["id"]
            if fid not in self._pan_smooth:
                self._pan_smooth[fid]  = pan_raw
                self._tilt_smooth[fid] = tilt_raw
            else:
                self._pan_smooth[fid]  += alpha * (pan_raw  - self._pan_smooth[fid])
                self._tilt_smooth[fid] += alpha * (tilt_raw - self._tilt_smooth[fid])

            pan_dmx  = max(0, min(255, int(round(self._pan_smooth[fid]))))
            tilt_dmx = max(0, min(255, int(round(self._tilt_smooth[fid]))))

            # Write to DMX engine
            # Mover's pan channel = dmxStartAddr, tilt channel = dmxStartAddr + 2
            # (standard 16-ch moving-head profile: ch1=pan, ch3=tilt)
            uni   = mover.get("dmxUniverse", 1) - 1  # 0-indexed universe
            start = mover.get("dmxStartAddr", 1)      # 1-indexed DMX start address
            pan_ch  = start
            tilt_ch = start + 2

            try:
                self._artnet.set_channel(uni, pan_ch,  pan_dmx)
                self._artnet.set_channel(uni, tilt_ch, tilt_dmx)
            except Exception:
                pass
            try:
                self._sacn.set_channel(uni, pan_ch,  pan_dmx)
                self._sacn.set_channel(uni, tilt_ch, tilt_dmx)
            except Exception:
                pass


# ── Singleton accessor ────────────────────────────────────────────────────────

_engine_instance: Optional[GyroEngine] = None

def get_gyro_engine() -> Optional[GyroEngine]:
    """Return the module-level GyroEngine singleton, or None if not initialised."""
    return _engine_instance

def init_gyro_engine(artnet_engine, sacn_engine,
                     gyro_state_ref, gyro_lock_ref,
                     fixtures_ref, children_ref) -> GyroEngine:
    """Create and start the singleton GyroEngine.  Call once from parent_server."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = GyroEngine(
            artnet_engine, sacn_engine,
            gyro_state_ref, gyro_lock_ref,
            fixtures_ref, children_ref,
        )
        _engine_instance.start()
    return _engine_instance
