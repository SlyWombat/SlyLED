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

Future features (#427 gyro-as-pointer, analogous features for any remote):
Gyro and phone both land on the same `orient` path — the legacy
`gyro_engine.py` (delta-based) was removed 2026-04-23 and will not return.
When implementing "remote as laser pointer" (project aim vector onto stage
geometry, drive fixture at the hit point), extend this engine with a
pointer-mode variant of `_aim_to_pan_tilt` that consumes a stage-mm target
point instead of an aim direction. No new engine, no new UDP path.
"""

import logging
import math
import threading
import time

# #784 PR-5 — `aim_to_pan_tilt` and `affine_pan_tilt` from
# mover_calibrator are no longer consulted. AimSphere replaces both.
# Kept only the import-side note here so the next reader knows the
# legacy IK ladder was deliberately removed, not forgotten.
from remote_orientation import (
    OrientConvention,
    _coerce_convention,
    default_convention_for_kind,
)

log = logging.getLogger("slyled.mover_control")


# ── #720 PR-1 — Home Secondary helpers ─────────────────────────────────
#
# Pure functions, no IO. Used by the home wizard's secondary slew step to
# pick a DMX pose that is offset from primary Home by a known fraction of
# the fixture's pan range, with the tilt at a known DMX value. Output is
# fed back into PR-1.5's `solve_dmx_per_degree` to bootstrap the SMART
# 2-pair affine estimate before any probes have been collected.

def secondary_pan_offset_dmx16(home_pan_dmx16, fraction=0.25):
    """Pick a secondary pan DMX16 offset by ``fraction`` of full DMX range.

    The full 16-bit DMX range corresponds to the profile's full pan range
    in degrees, so ``fraction * 65535`` ticks ≡ ``fraction * panRange``
    degrees regardless of profile. Sign is chosen so the secondary pose
    stays inside ``[0, 65535]``: prefer + first, then -, finally clamp.

    Returns the absolute secondary DMX16 (int), already clamped to
    ``[0, 65535]``.
    """
    delta = int(round(float(fraction) * 65535))
    pos = int(home_pan_dmx16) + delta
    neg = int(home_pan_dmx16) - delta
    if 0 <= pos <= 65535:
        return pos
    if 0 <= neg <= 65535:
        return neg
    return max(0, min(65535, pos))


def secondary_tilt_dmx16(profile_tilt_offset_dmx16=32768):
    """Pick a secondary tilt DMX16 — mid-range / half-tick of the profile.

    Defaults to 32768 (DMX center). Profile-aware callers can pass
    ``tiltOffsetDmx16`` (#716) to centre on the fixture's stage-frame
    horizon, but for the wizard's bootstrap any tilt distinct from Home is
    sufficient — the operator measures and reports the resulting stage-
    frame angle.
    """
    return max(0, min(65535, int(profile_tilt_offset_dmx16)))


class MoverClaim:
    """Per-mover claim — ties a device id to a mover id for DMX writes."""

    __slots__ = (
        "mover_id", "device_id", "device_name", "device_type",
        "claimed_at", "last_write_ts", "ttl_s", "state",
        "color_r", "color_g", "color_b", "dimmer", "strobe_active",
        "pan_smooth", "tilt_smooth", "have_pan_tilt",
        "calibrated_here", "smoothing",
        # #762 — convention this claim runs under. Resolved at claim time
        # from (per-claim override > per-fixture orientConvention > engine
        # default > per-kind default). Surfaced via /api/mover-control/
        # status so the SPA can label the active grip.
        "convention",
        # Convention the Remote was using *before* the claim took effect.
        # Restored on release so the Remote falls back to its persisted /
        # per-kind default when no claim is overriding it.
        "_prior_remote_convention",
        # #855 — last-known prerequisite-failure code from
        # `_aim_to_pan_tilt`. None when aim is healthy. One-shot WARNING
        # log fires on every transition (so the log isn't noisy while
        # the condition persists).
        "aim_error",
    )

    def __init__(self, mover_id, device_id, device_name, device_type="gyro",
                 smoothing=0.15, ttl_s=None, convention=None):
        self.mover_id = mover_id
        self.device_id = device_id
        self.device_name = device_name
        self.device_type = device_type
        self.claimed_at = time.time()
        self.last_write_ts = time.time()
        # #680 — ttl_s comes from operator settings via MoverControlEngine.
        # Keep 15 s as a module-level default for any caller that still
        # constructs MoverClaim directly.
        self.ttl_s = float(ttl_s) if ttl_s is not None else 15.0
        self.state = "claimed"  # claimed | streaming | calibrating

        # #814 — Colour / dimmer / strobe inherit from whatever writer
        # held the wire before the claim arrived (Track action, scene,
        # SET_BRIGHTNESS, …). `None` means "claim hasn't decided" and
        # `_write_dmx` skips that channel entirely. As soon as the
        # operator calls `/api/mover-control/color` or `…/flash`, the
        # corresponding fields become real values and the engine pump
        # starts writing them on every tick.
        self.color_r = None
        self.color_g = None
        self.color_b = None
        self.dimmer = None
        self.strobe_active = None

        # Smoothed pan/tilt in normalised [0,1]. Initialised to centre but
        # not considered valid until the first aim sample arrives.
        self.pan_smooth = 0.5
        self.tilt_smooth = 0.5
        self.have_pan_tilt = False
        # Persisted calibration from a previous session is NOT trusted to
        # drive pan/tilt until the operator confirms alignment via
        # calibrate-end in THIS claim. Fixture holds the seeded
        # layout-forward position until then.
        self.calibrated_here = False
        self.smoothing = smoothing
        # #762 — convention is resolved by MoverControlEngine.claim() and
        # passed in here. None = leave the Remote on whatever convention
        # it was already running (per-kind default or persisted override).
        self.convention = _coerce_convention(convention)
        self._prior_remote_convention = None
        self.aim_error = None

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
            # #688 — surface the operator-confirmed-alignment flag so
            # the SPA's status panel can show "Calibrated · streaming"
            # vs "Streaming (uncalibrated)". Pre-fix this lived as
            # `calibrated_here` on the claim but never made it into
            # the wire format.
            "calibrated":   bool(self.calibrated_here),
            "color":        {"r": self.color_r, "g": self.color_g, "b": self.color_b},
            "dimmer":       self.dimmer,
            # #762 — surface the convention so SPA / Android can show the
            # operator which axis-set the active claim is using.
            "orientConvention": (self.convention.value
                                  if self.convention else None),
            # #855 — last-known prerequisite-failure for the aim path.
            # `None` when the orient → pan/tilt translation is healthy.
            # Surface values: "missing_profile", "missing_home",
            # "missing_secondary", "non_mover_profile",
            # "aimsphere_value_error", "aimsphere_exception". The
            # dashboard / Android Status tab can render this as
            # "head not aiming because <reason>" instead of leaving
            # the operator staring at a non-moving head.
            "aimError":     self.aim_error,
        }


class MoverControlEngine:
    """Mover-follow consumer of the remote-orientation primitive."""

    def __init__(self, get_fixtures, get_layout, get_profile_info,
                 get_engine, set_fixture_color_fn, get_remote_by_device_id,
                 get_mover_cal=None, get_mover_model=None,
                 is_calibrating=None, get_claim_ttl_s=None,
                 get_default_convention=None,
                 park_fn=None, park_pan_tilt_fn=None,
                 set_canonical_aim_fn=None,
                 get_global_brightness=None,
                 scale_for_brightness=None):
        """
        Args:
            get_fixtures:             list of fixtures
            get_layout:               layout dict (unused here; kept for symmetry)
            get_profile_info(pid):    DMX profile info dict
            get_engine():             running ArtNet/sACN engine or None
            set_fixture_color_fn:     (engine, uni, addr, r, g, b, prof_info) writer
            get_remote_by_device_id:  callable(device_id) → Remote | None
            get_mover_cal:            callable(mover_id) → calibration dict or None.
                                      Kept for back-compat; the v2 parametric
                                      model is the preferred IK path.
            get_mover_model:          callable(mover_id, mover) → ParametricFixtureModel
                                      or None. When provided, IK uses the
                                      closed-form model.inverse() — no grid,
                                      no round-trip mismatch.
        """
        self._get_fixtures = get_fixtures
        self._get_layout = get_layout
        self._get_profile_info = get_profile_info
        self._get_engine = get_engine
        self._set_fixture_color = set_fixture_color_fn
        self._get_remote = get_remote_by_device_id
        self._get_mover_cal = get_mover_cal or (lambda _mid: None)
        self._get_mover_model = get_mover_model or (lambda _mid, _mv: None)
        self._is_calibrating = is_calibrating or (lambda _mid: False)
        # #680 — operator-tunable claim TTL. Callable (not a captured
        # value) so setting changes take effect on the next claim without
        # engine restart.
        self._get_claim_ttl_s = get_claim_ttl_s or (lambda: 15.0)
        # #762 — engine-wide default OrientConvention from settings.json.
        # Returns None when the operator hasn't pinned one, in which case
        # the per-kind default applies. Callable so setting changes take
        # effect on the next claim without engine restart.
        self._get_default_convention = (
            get_default_convention or (lambda: None))
        # #800 — split park primitives (operator clarification
        # 2026-05-03):
        #   `park_fn(mover_id)` — full park: pan/tilt to home + lamp
        #     off. Used by `release` where idle = home + dark.
        #   `park_pan_tilt_fn(mover_id)` — pan/tilt only, no lamp
        #     touch. Used by `start_stream` so the engine pump's
        #     immediate `claim.dimmer` write isn't contradicted by a
        #     park-time `lamp_off` (was a one-tick flicker).
        # Both are optional; missing fns degrade to RGB+dimmer-only
        # blackout (release) or no-op (start_stream).
        self._park_fn = park_fn
        self._park_pan_tilt_fn = park_pan_tilt_fn
        # #806 — callback to record the canonical aim_stage vector each
        # time the engine commits a pan/tilt write. Lets calibrate-end
        # / live-render observe the same vector the engine pump used,
        # eliminating the dmx_to_aim / aim_direction round-trip risk
        # that produced #805. Optional; missing fn → no canonical write.
        self._set_canonical_aim = set_canonical_aim_fn or (lambda _fid, _v: None)
        # #843 — claim-writer brightness scaling. Mirrors the playback
        # loop's per-frame snapshot of `_settings["globalBrightness"]`
        # so a press-Start-while-an-Auto-Brightness-feed-runs claim
        # also dims with the audio envelope. Without this, gyro / mover-
        # control claims wrote dimmer / RGB at full intensity and the
        # operator saw the claimed mover stay bright while every other
        # fixture dimmed (G3 fail in the issue's contract).
        self._get_global_brightness = (
            get_global_brightness or (lambda: 255))
        self._scale_for_brightness = (
            scale_for_brightness or (lambda v, g: v))

        self._claims = {}  # mover_id → MoverClaim
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        # #647 observability: the 40 Hz tick silently drops writes when the
        # Art-Net/sACN engine is not running, so the operator sees phone UI
        # + orient streaming + state=streaming, yet no frames hit the wire.
        # Track drops so the status endpoint can surface the condition.
        self._dropped_writes = 0
        self._last_drop_ts = None
        self._last_drop_log_ts = 0.0

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
              smoothing=0.15, convention=None):
        # #762 — resolve OrientConvention precedence outside the lock so we
        # can read the fixture / remote without holding it. Order:
        #   per-claim arg > per-fixture > engine default > remote default.
        resolved_conv = self._resolve_convention(
            mover_id, device_id, requested=convention)

        with self._lock:
            existing = self._claims.get(mover_id)
            if existing and existing.device_id != device_id:
                age = time.time() - existing.last_write_ts
                if age < existing.ttl_s:
                    return False, f"Claimed by {existing.device_name} ({existing.device_type})"
                log.info("Mover %d: TTL expired for %s, releasing",
                         mover_id, existing.device_id)

            claim = MoverClaim(mover_id, device_id, device_name, device_type,
                               smoothing=smoothing,
                               ttl_s=self._get_claim_ttl_s(),
                               convention=resolved_conv)
            self._claims[mover_id] = claim
            log.info("Mover %d claimed by %s (%s) — convention=%s",
                     mover_id, device_name, device_type,
                     resolved_conv.value if resolved_conv else "remote-default")

        # Apply the convention to the Remote *outside* the engine lock —
        # set_convention() may discard a stale calibration, which clears
        # aim_stage, and we don't want that to race with the tick loop's
        # read. The Remote's own state is internally synchronised.
        if resolved_conv is not None:
            remote = self._get_remote(device_id)
            if remote is not None and remote.convention != resolved_conv:
                claim._prior_remote_convention = remote.convention
                remote.set_convention(resolved_conv)

        # #847 — trust cross-session puck calibration. Pre-fix every
        # claim started with `calibrated_here = False` and the tick
        # loop's `if … claim.calibrated_here …` gate at the orient →
        # pan/tilt path silently dropped every DMX write until the
        # operator ran calibrate-end this session. Operators with a
        # puck already calibrated against this mover (R_world_to_stage
        # set, calibrated_against.objectId == mover_id) saw a claim
        # that reported `calibrated: false`, panNorm/tiltNorm stuck at
        # 0.5, and `droppedWrites` ticking at 40 Hz — even though
        # AimSphere had everything it needed to resolve.
        # Now: if the puck's persisted cal matches this mover and the
        # mover has Home + Secondary anchors so AimSphere can resolve,
        # accept the cross-session cal as the calibration-of-record.
        # The original "don't trust persisted calibration" rationale
        # was a 2026-01 conservative default; the operator's explicit
        # request (#847) is to trust it. A bad cross-session
        # calibration now manifests as the head pointing at the wrong
        # target — re-calibrate to fix; mid-session calibrate-end
        # still works as before.
        mover = self._get_mover(mover_id)
        remote_post = self._get_remote(device_id)
        cal_against = (remote_post.calibrated_against
                       if remote_post is not None else None)
        if (mover is not None
                and remote_post is not None
                and remote_post.R_world_to_stage is not None
                and remote_post.stale_reason is None
                and isinstance(cal_against, dict)
                and cal_against.get("kind") == "mover"
                and int(cal_against.get("objectId", -1)) == int(mover_id)
                and mover.get("homePanDmx16") is not None
                and mover.get("homeTiltDmx16") is not None
                and mover.get("homeSecondary")):
            claim.calibrated_here = True
            log.info("Mover %d: trusted cross-session puck cal from %s "
                     "(calibrated_against mover, R_world_to_stage set)",
                     mover_id, device_id)
        return True, "ok"

    def _resolve_convention(self, mover_id, device_id, requested=None):
        """#762 — resolve which OrientConvention this claim should run.

        Precedence (most-specific wins):
          1. Per-claim ``requested`` (body field on /api/mover-control/claim).
          2. Per-fixture ``orientConvention`` field.
          3. Engine-wide setting (settings.json moverControl.orientConvention).
          4. None — let the Remote's own per-kind default stand.

        Returns an OrientConvention or None.
        """
        conv = _coerce_convention(requested)
        if conv is not None:
            return conv
        mover = self._get_mover(mover_id)
        if mover is not None:
            conv = _coerce_convention(mover.get("orientConvention"))
            if conv is not None:
                return conv
        return _coerce_convention(self._get_default_convention())

    def release(self, mover_id, device_id=None, blackout=True):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim:
                return True
            if device_id and claim.device_id != device_id:
                return False
            del self._claims[mover_id]
        # #762 — if this claim overrode the Remote's convention, restore
        # the Remote's prior convention so a subsequent claim picks up its
        # per-kind default (or persisted override) rather than the previous
        # operator's per-claim choice. Done outside the engine lock.
        if claim._prior_remote_convention is not None:
            remote = self._get_remote(claim.device_id)
            if remote is not None:
                remote.set_convention(claim._prior_remote_convention)
        if blackout:
            # #800 — claim release parks the head at home AND turns the
            # lamp off (operator expectation: idle = home pose, dark).
            # Falls back to RGB+dimmer-only blackout when no `park_fn`
            # is wired (e.g. unit tests instantiating the engine
            # without parent_server's `_park_fixture_at_home`).
            if self._park_fn is not None:
                try:
                    self._park_fn(mover_id)
                except Exception as e:
                    log.debug("Mover %d release park failed: %s",
                              mover_id, e)
                    self._blackout_mover(mover_id)
            else:
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
        # #800 — park the head at home before any orient packets can
        # land. `calibrate-end` snapshots the head's stage-frame aim
        # as the reference the phone's pose is locked to; if the head
        # was at a stale pose from a previous test, the lock anchors
        # to that direction and the very next orient update appears
        # to "jump" the head. Parking pins the reference to home so
        # the operator's calibrate-with-phone-steady gives a
        # deterministic forward anchor.
        #
        # Operator clarification 2026-05-03: pan/tilt-only here. The
        # engine pump's next `_write_dmx` will write `claim.dimmer`
        # (255 by default) so the operator can see the beam; calling
        # the full `park_fn` (which includes `lamp_off`) would cause
        # a one-tick flicker.
        park = self._park_pan_tilt_fn or self._park_fn
        if park is not None:
            try:
                park(mover_id)
            except Exception as e:
                log.debug("Mover %d start_stream park failed: %s",
                          mover_id, e)
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
            claim.calibrated_here = True
            claim.have_pan_tilt = False  # force jump-to-target on next tick
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

    def set_smoothing(self, mover_id, device_id, smoothing):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.smoothing = max(0.01, min(1.0, float(smoothing)))
        return True

    def flash(self, mover_id, device_id, on=True):
        with self._lock:
            claim = self._claims.get(mover_id)
            if not claim or claim.device_id != device_id:
                return False
            claim.strobe_active = bool(on)
            if on:
                claim.dimmer = 255
        return True

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self):
        with self._lock:
            return [c.to_dict() for c in self._claims.values()]

    def get_engine_health(self):
        """Engine-running signal for observability (#647).

        Returns a dict the caller can attach to /api/mover-control/status so
        operators can tell the difference between "phone is streaming and
        fixture is off because the show isn't lit" and "phone is streaming
        and the DMX engine silently stopped transmitting an hour ago."

        #802 — `engineType` is detected by the engine's class name
        (`ArtNetEngine` / `sACNEngine`), not a non-existent `sender_name`
        attribute. The previous code's `getattr(engine, "sender_name",
        "") == "artnet"` always evaluated False (neither class has that
        attribute), so the type fell through to "sacn" for ANY running
        engine — leading to operator-visible "engineType: sacn" even
        when art-net was the only engine ever started. Mover_engine's
        actual write path uses `self._get_engine()` (a lookup-at-call-
        time lambda from parent_server) so writes hit the right engine
        regardless of this label, but the cosmetic mislabel made
        `/api/mover-control/status` misleading.
        """
        engine = self._get_engine()
        cls = type(engine).__name__ if engine is not None else ""
        if "ArtNet" in cls:
            engine_type = "artnet"
        elif "sACN" in cls or "Sacn" in cls or "SACN" in cls:
            engine_type = "sacn"
        else:
            engine_type = None
        return {
            "running": bool(engine and engine.running),
            "engineType": engine_type,
            "droppedWrites": self._dropped_writes,
            "lastDropTs": self._last_drop_ts,
        }

    def _note_dropped_write(self):
        """Called inside the tick when a DMX write is silently dropped
        because the underlying engine is not running. Rate-limited so we
        don't flood logs at the 40 Hz tick rate."""
        self._dropped_writes += 1
        now = time.monotonic()
        self._last_drop_ts = time.time()
        if now - self._last_drop_log_ts > 5.0:
            log.warning("mover_control: DMX write dropped — engine not running "
                        "(total drops: %d since start)", self._dropped_writes)
            self._last_drop_log_ts = now

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

            # #511 — fixture is mid-calibration; don't fight the cal thread
            # for pan/tilt. The claim remains so the operator keeps control
            # as soon as cal releases.
            if self._is_calibrating(mover_id):
                continue

            # #476 — if the remote has gone hard-stale (comms silence > 60s,
            # session-ended, or age-out), drop the claim and blackout. The
            # operator will need to Send-Lock again.
            remote = self._get_remote(claim.device_id)
            if remote is not None:
                remote.check_staleness()
                if remote.stale_reason is not None and claim.state == "streaming":
                    # #823 — grace period for freshly-created claims.
                    # Press-Start handlers clear stale BEFORE creating
                    # the claim, but if any future code path lands a
                    # streaming claim on a stale remote without clearing,
                    # give it 200 ms to do so before tearing down. Belt-
                    # and-suspenders against future regressions.
                    if time.time() - claim.claimed_at < 0.2:
                        continue
                    log.info("Mover %d auto-released: remote %s %s",
                             mover_id, claim.device_id, remote.stale_reason)
                    self.release(mover_id, claim.device_id, blackout=True)
                    continue

            have_aim = False
            if claim.state == "streaming":
                # Only drive pan/tilt from the puck once the operator has
                # calibrated THIS session. Stale-across-restart calibration
                # would point the fixture at the previous aim direction; we
                # want the layout-forward seed held until re-calibration.
                if remote is not None and remote.aim_stage is not None \
                        and claim.calibrated_here and remote.stale_reason is None:
                    pan_norm, tilt_norm = self._aim_to_pan_tilt(
                        mover_id, mover, remote.aim_stage,
                    )
                    # #784 PR-5 — fixture without Home or signed profile
                    # returns (None, None); skip the pan/tilt claim write
                    # for this tick. Non-pan/tilt state (dimmer, colour)
                    # still flows below.
                    if pan_norm is not None and tilt_norm is not None:
                        if not claim.have_pan_tilt:
                            claim.pan_smooth  = pan_norm
                            claim.tilt_smooth = tilt_norm
                            claim.have_pan_tilt = True
                        else:
                            alpha = max(0.0, min(1.0, 1.0 - claim.smoothing))
                            claim.pan_smooth  += alpha * (pan_norm  - claim.pan_smooth)
                            claim.tilt_smooth += alpha * (tilt_norm - claim.tilt_smooth)
                        have_aim = True
                        # #806 — this orient tick has a clean stage-frame
                        # aim vector in hand. Push it to the canonical
                        # store so calibrate-end / live-render skip the
                        # sphere round-trip (root cause of #805).
                        try:
                            self._set_canonical_aim(
                                mover_id, tuple(remote.aim_stage))
                        except Exception:
                            pass

            # Always write the non-pan/tilt claim state (dimmer, colour,
            # strobe, channel defaults) while streaming or calibrating —
            # that's what turns the light on. Pan/tilt are only written
            # once `claim.have_pan_tilt` is true (i.e. a fresh puck aim
            # has overridden the seeded layout-forward position).
            if claim.state in ("streaming", "calibrating"):
                self._write_dmx(mover, prof_info, claim,
                                include_pan_tilt=claim.have_pan_tilt)
                claim.last_write_ts = time.time()

    # ── DMX writers ─────────────────────────────────────────────────

    def _get_mover(self, mover_id):
        for f in self._get_fixtures():
            if f["id"] == mover_id and f.get("fixtureType") == "dmx":
                return f
        return None

    def _aim_to_pan_tilt(self, mover_id, mover, aim_stage):
        """Pan/tilt-norm `(0..1, 0..1)` from a stage-frame aim direction.

        #784 PR-5 — surgical rewire to `aim.sphere.AimSphere`. No
        SMART / parametric / affine / generic-IK fallback ladder;
        without Home + Secondary + a moving-head profile (`panRange` +
        `tiltRange`) the fixture refuses to aim and this returns
        `(None, None)` so the caller can skip the pan/tilt claim write
        (correct per #738 — angular control needs only Home).

        `aim_stage` is a stage-frame unit aim vector; converting to
        `(az_deg, el_deg)` lets us call `aim_direction` directly,
        sidestepping any need to know fixture_xyz (the synthetic-
        target dance was a relic of the pre-#785 generic-IK path).
        Mount inversion + home-aim direction live in `fixture.rotation`
        (composed inside the sphere's `home_az_stage` /
        `home_el_stage` derivation). Multi-valued azimuth resolves via
        `prefer="closest"` against the claim's current DMX pose so a
        track-update never crosses the pan A/B branch on a fixture
        already settled on one side.
        """
        pid = mover.get("dmxProfileId")
        prof_info = self._get_profile_info(pid) if pid else None
        # #855 — explicit guard for every prerequisite AimSphere
        # actually requires. `homeSecondary` was missing from this
        # ladder pre-fix, so a Home-but-no-Secondary fixture slipped
        # past, hit the `except Exception` below at DEBUG, and the
        # operator saw a head that didn't move with no surfaced error.
        # Now we record an `aim_error` on the claim so the dashboard
        # can show "head not aiming because Secondary not saved" and
        # the WARNING log carries the same diagnostic.
        if prof_info is None:
            self._set_claim_aim_error(mover_id, "missing_profile")
            return (None, None)
        if (mover.get("homePanDmx16") is None
                or mover.get("homeTiltDmx16") is None):
            self._set_claim_aim_error(mover_id, "missing_home")
            return (None, None)
        if not mover.get("homeSecondary"):
            self._set_claim_aim_error(mover_id, "missing_secondary")
            return (None, None)
        if (not ((prof_info.get("panRange", 0) or 0) > 0)
                or not ((prof_info.get("tiltRange", 0) or 0) > 0)):
            self._set_claim_aim_error(mover_id, "non_mover_profile")
            return (None, None)
        # All prerequisites met — clear any prior error so the next
        # reconfiguration that resolves the problem unsticks the
        # claim's status without operator intervention.
        self._set_claim_aim_error(mover_id, None)
        try:
            from aim.routes import _get_or_build_sphere
            sphere = _get_or_build_sphere(mover, prof_info)
            az_deg = math.degrees(math.atan2(aim_stage[0], aim_stage[1]))
            el_deg = math.degrees(math.atan2(
                aim_stage[2], math.hypot(aim_stage[0], aim_stage[1])))
            # #851 — assertions in `sphere.aim_direction` can fire on
            # boundary az/el; clamp into the assertion's exact accepted
            # range so a tiny float overshoot doesn't silently drop the
            # frame. Without this clamp, the AssertionError caught by
            # the wrapper below would silently freeze panNorm at its
            # last good value while orient packets keep streaming —
            # exactly the symptom #851 reports.
            az_deg = max(-180.0, min(180.0, az_deg))
            el_deg = max(-90.0, min(90.0, el_deg))
            claim = self._claims.get(mover_id)
            cur_pose = ((int(claim.pan_smooth * 65535),
                          int(claim.tilt_smooth * 65535))
                         if claim and getattr(claim, "have_pan_tilt", False)
                         else None)
            pose = sphere.aim_direction(az_deg, el_deg, current_pose=cur_pose)
            return ((pose[0] / 65535.0, pose[1] / 65535.0)
                     if pose is not None else (None, None))
        except ValueError as e:
            # #855 — AimSphere raises ValueError for prerequisite
            # failures we can name and surface. The early-return
            # guards above already cover the common cases (no
            # profile, no Home, no Secondary, non-mover profile);
            # a ValueError from inside AimSphere is therefore an
            # unexpected prerequisite failure (e.g. malformed
            # secondary entry, profile shape mismatch). Tag it
            # generically and surface to the claim.
            self._set_claim_aim_error(mover_id, "aimsphere_value_error")
            log.warning("aim_to_pan_tilt: AimSphere ValueError for mover %s "
                         "aim_stage=%s: %s", mover_id, aim_stage, e)
            return (None, None)
        except Exception as e:
            # #851 — was log.debug; promoted to log.warning so a
            # production silent-failure mode is visible without needing
            # to re-flash with DEBUG logging enabled. If this fires
            # repeatedly the orient → panNorm refresh is broken and
            # panNorm freezes at its last good value — the symptom the
            # issue describes.
            self._set_claim_aim_error(mover_id, "aimsphere_exception")
            log.warning("aim_to_pan_tilt: AimSphere failed for mover %s "
                         "aim_stage=%s: %s",
                         mover_id, aim_stage, e)
            return (None, None)

    # #855 — track the last-known prerequisite-failure for each
    # claim so /api/mover-control/status can surface "head not
    # aiming because Secondary not saved" instead of leaving the
    # operator staring at a non-moving fixture. Logged once per
    # (mover_id, error) transition so the log isn't noisy when the
    # condition persists across many ticks.
    def _set_claim_aim_error(self, mover_id, err):
        with self._lock:
            claim = self._claims.get(mover_id)
            if claim is None:
                return
            prev = getattr(claim, "aim_error", None)
            if prev == err:
                return
            claim.aim_error = err
            if err is not None:
                log.warning("Mover %d aim error transition: %s -> %s",
                             mover_id, prev or "ok", err)
            elif prev is not None:
                log.info("Mover %d aim error cleared (was %s)", mover_id, prev)

    # #784 PR-5 (2026-05-03) — `_get_smart_model` removed. The SMART
    # model / 2-pair-affine path is no longer consulted; `AimSphere` is
    # the canonical IK and only needs Home + a moving-head profile.
    # `_get_mover_cal` is still alive for `mover_calibrations` lookups
    # (light-map data lives there); only the angular-IK consultation is
    # gone.

    def _write_dmx(self, mover, prof_info, claim, include_pan_tilt=True):
        engine = self._get_engine()
        if not engine or not engine.running:
            self._note_dropped_write()
            return
        if not prof_info:
            return
        uni = mover.get("dmxUniverse", 1)
        addr = mover.get("dmxStartAddr", 1)
        profile = {"channel_map": prof_info.get("channel_map", {}),
                   "channels": prof_info.get("channels", [])}
        uni_buf = engine.get_universe(uni)

        if include_pan_tilt:
            uni_buf.set_fixture_pan_tilt(addr, claim.pan_smooth,
                                         claim.tilt_smooth, profile)
        # #853 — render-time scaling removed; the ArtNet engine's
        # send-time master grand-master gate (`get_data_scaled` with
        # gamma-corrected scaling on intensity-class channels) handles
        # globalBrightness for every writer uniformly. Pre-fix this
        # path multiplied dimmer + RGB by g_bri locally per #843,
        # which would now double-apply on top of the send gate.
        # #814 — only stamp dimmer / color when the operator has set
        # them explicitly (set_color / flash). Tristate `None` means
        # "inherit whatever's on the wire" so a Track-action show
        # keeps its color while the operator drives pan/tilt.
        if claim.dimmer is not None:
            uni_buf.set_fixture_dimmer(addr, int(claim.dimmer), profile)
        if (claim.color_r is not None and claim.color_g is not None
                and claim.color_b is not None):
            self._set_fixture_color(engine, uni, addr,
                                    claim.color_r, claim.color_g,
                                    claim.color_b,
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
            # When NOT strobing, write the profile's shutterEffect=Open
            # value (#516) so operator-controlled movers power up with a
            # visible beam — the raw channel default may be 0, which on
            # "closed at 0" wirings would blacken the fixture.
            if ch_type == "strobe":
                # #814 — leave the wire alone until the operator has made
                # an explicit strobe decision (set_color / flash). Inherit
                # whatever the previous writer (Track action, scene) left.
                if claim.strobe_active is None:
                    continue
                if claim.strobe_active:
                    uni_buf.set_channel(addr + ch.get("offset", 0),
                                        self._find_strobe_value(ch))
                else:
                    open_val = self._find_strobe_open(prof_info)
                    if open_val is not None:
                        uni_buf.set_channel(addr + ch.get("offset", 0), open_val)
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
        range with ``shutterEffect == "Strobe"`` (#516 annotation), falls
        back to label matching for legacy profiles, then to channel
        midpoint 128."""
        for cap in ch.get("capabilities", []) or []:
            if cap.get("type") != "ShutterStrobe":
                continue
            eff = cap.get("shutterEffect")
            if eff == "Strobe":
                rng = cap.get("range", [0, 255])
                return (rng[0] + rng[1]) // 2
        # Legacy label heuristic
        for cap in ch.get("capabilities", []) or []:
            label = (cap.get("label") or "").lower()
            rng = cap.get("range", [0, 255])
            if cap.get("type") == "ShutterStrobe" and "strobe" in label:
                return (rng[0] + rng[1]) // 2
        return 128

    @staticmethod
    def _find_strobe_open(prof_info):
        """DMX value that means 'shutter open / solid light' for the
        profile. Delegates to dmx_profiles.strobe_open_value which
        handles both annotated and legacy ShutterStrobe layouts.
        Returns None if the profile has no strobe channel at all so the
        caller can decide how to degrade."""
        try:
            from dmx_profiles import strobe_open_value, _strobe_channel
            if _strobe_channel(prof_info) is None:
                return None
            return strobe_open_value(prof_info)
        except Exception:
            return None

    def _blackout_mover(self, mover_id):
        # #650 — zero dimmer AND RGB. Dimmer=0 is enough on fixtures that
        # have one; fixtures without a dimmer channel (some older RGB-only
        # movers) still need RGB=0 to actually go dark. Pan/tilt are
        # intentionally preserved so the fixture holds position.
        mover = self._get_mover(mover_id)
        if not mover:
            return
        engine = self._get_engine()
        if not engine or not engine.running:
            self._note_dropped_write()
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
        uni_buf.set_fixture_rgb(addr, 0, 0, 0, profile)
