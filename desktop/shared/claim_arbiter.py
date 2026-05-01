"""claim_arbiter.py — per-fixture mute/handover gate for show output (#763).

A live show writes timeline DMX into the universe buffer at ~40 Hz. When an
operator claims a moving head via mover-control, the show output and the
operator's stream both want the same channels. This arbiter sits between the
show writer and the universe buffer:

  • While a fixture is claimed, the show writer skips its `uni_buf.set_*`
    calls. The bake / track action keeps computing target poses (Q3-(a) in
    issue #763), it just doesn't emit. Mover-control writes through unchanged.
  • On release, the arbiter records a 750 ms "slew window" during which the
    show writer also issues a slow `pan-tilt-speed` DMX value. The fixture's
    motors then ease from the operator's last pose toward the show's current
    pose, instead of snapping. After the window, the show writer behaves
    normally.

The arbiter does NOT own claim state — `MoverControlEngine._claims` remains
the source of truth. The arbiter is a read-only facade over
`MoverControlEngine.get_status()` plus a tiny per-fixture release-timer dict.

Public surface used by `parent_server.py`:

    arb = ClaimArbiter(_mover_engine.get_status)
    snap = arb.snapshot()                      # once per playback frame
    if arb.is_muted(fid, snap): continue       # skip show writes for this fid
    h = arb.handover_state(fid)                # returns slow-pt-speed dict or None
    arb.on_release(fid)                        # called from MoverControlEngine.release

Status reporting:

    arb.claimed_fids()                         # for /api/show/status
    arb.claim_info(fid, snap)                  # for /api/fixtures/live
"""

import threading
import time


class _ClaimSnapshot:
    """Frozen-at-frame-start view of which fixtures are currently claimed.

    Held by the playback loop for the duration of one ~25 ms frame so every
    per-fixture write decision in that frame sees a consistent set.
    """
    __slots__ = ("fids", "by_fid")

    def __init__(self, fids, by_fid):
        self.fids = fids
        self.by_fid = by_fid


class ClaimArbiter:
    def __init__(self, get_claim_status, slew_window_ms=750, slow_dmx=200):
        """
        Args:
            get_claim_status: callable returning a list of claim dicts as
                produced by MoverControlEngine.get_status(). Each dict must
                carry "moverId" and may carry "deviceId", "deviceName",
                "deviceType". Read once per playback frame.
            slew_window_ms: post-release window during which the show writer
                caps pan-tilt-speed for a smooth handover. 750 ms is the #763
                operator-confirmed default.
            slow_dmx: the DMX value to write to a profile's "pan-tilt-speed"
                channel during the slew window. Many profiles encode 0=fast,
                255=slowest; 200 sits firmly in the slow half without
                stalling the motors.
        """
        self._get_claim_status = get_claim_status
        self._slew_window_ms = int(slew_window_ms)
        self._slow_dmx = int(slow_dmx)
        self._releases = {}             # fid -> monotonic timestamp of release
        self._just_ended = set()        # fids whose slew window has just expired
        self._lock = threading.Lock()

    def snapshot(self):
        claims = self._get_claim_status() or []
        by_fid = {}
        for c in claims:
            mid = c.get("moverId")
            if mid is None:
                continue
            try:
                by_fid[int(mid)] = c
            except (TypeError, ValueError):
                continue
        return _ClaimSnapshot(frozenset(by_fid.keys()), by_fid)

    def is_muted(self, fid, snap):
        try:
            return int(fid) in snap.fids
        except (TypeError, ValueError):
            return False

    def on_release(self, fid):
        try:
            f = int(fid)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._releases[f] = time.monotonic()

    def handover_state(self, fid):
        try:
            f = int(fid)
        except (TypeError, ValueError):
            return None
        with self._lock:
            t = self._releases.get(f)
            if t is None:
                return None
            age_ms = (time.monotonic() - t) * 1000.0
            if age_ms >= self._slew_window_ms:
                self._releases.pop(f, None)
                self._just_ended.add(f)
                return None
            return {
                "slowDmx": self._slow_dmx,
                "ageMs": age_ms,
                "windowMs": float(self._slew_window_ms),
            }

    def pop_handover_just_ended(self, fid):
        """One-shot: returns True the first time it's called after a fid's
        slew window expires. Lets the show writer issue a single 'fast'
        pan-tilt-speed write to release the cap before resuming normal
        output. Returns False on subsequent calls until the next release.
        """
        try:
            f = int(fid)
        except (TypeError, ValueError):
            return False
        with self._lock:
            if f in self._just_ended:
                self._just_ended.discard(f)
                return True
            return False

    def claimed_fids(self, snap=None):
        s = snap if snap is not None else self.snapshot()
        return sorted(s.fids)

    def claim_info(self, fid, snap=None):
        s = snap if snap is not None else self.snapshot()
        try:
            c = s.by_fid.get(int(fid))
        except (TypeError, ValueError):
            return None
        if not c:
            return None
        return {
            "deviceName": c.get("deviceName"),
            "deviceId": c.get("deviceId"),
            "deviceType": c.get("deviceType"),
        }
