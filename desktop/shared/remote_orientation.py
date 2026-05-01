"""Remote orientation primitive — the stage-space rotation of each remote.

This module is the **primitive** layer of the gyro/phone architecture
(#484). It owns `Remote` objects that track:

- Static placement in the stage (`pos`, `kind`, `name`).
- Calibration result `R_world_to_stage` — a quaternion rotating the
  remote's own world frame into stage coordinates.
- Live orientation samples (`last_quat_world`, `aim_stage`, `up_stage`).
- Staleness reasons (`age`, `connection-lost`, `session-ended`).

It knows nothing about movers, DMX, or any consumer feature. Feature
modules (e.g. `mover_control.py` in phase 4) read `Remote.aim_stage`
and act on it.

See docs/gyro-stage-space.md §4, §7 for the architecture.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import threading
import time

from remote_math import (
    dot3,
    frame_align,
    normalize3,
    quat_from_euler_zyx_deg,
    quat_rotate_vec,
)

log = logging.getLogger("slyled.remote_orientation")


# ── #762 Orient axis conventions ──────────────────────────────────────────
#
# Selects which Euler axes from the remote's IMU drive the fixture aim. The
# gyro puck (Waveshare ESP32-S3 LCD 1.28" — QMI8658, 6-axis, NO magnetometer)
# has *integrating* yaw that drifts indefinitely; only roll and pitch are
# gravity-anchored and absolute. The Android phone has Sensor.TYPE_ROTATION_
# VECTOR which is magnetometer-fused, so it does keep usable yaw.
#
# The convention is a per-remote attribute. Per-fixture / per-claim overrides
# are scaffolded (MoverClaim.convention, /api/mover-control/claim body field)
# but the engine default per-kind table below is the only path with UI today.

class OrientConvention(str, enum.Enum):
    # Bottom-forward grip (puck/phone held vertical, charging port toward
    # stage). Roll → pan, pitch → tilt. Yaw is dropped before the orientation
    # quaternion is built — drift becomes a no-op because the world frame
    # stays anchored to the calibrate-time pose. Default for the gyro puck.
    BOTTOM_FORWARD_ROLL_PITCH = "bottom_forward"
    # Legacy convention — full (roll, pitch, yaw) → quaternion → R_world_to
    # _stage. Yaw is consumed; works for any controller that has a usable
    # absolute yaw source (compass-fused rotation vector, magnetometer).
    # Default for the Android phone.
    FLAT_PITCH_YAW = "flat_pitch_yaw"


def _coerce_convention(value, default=None):
    """Map a raw value (Enum, string, None) onto an OrientConvention.
    Unknown strings fall back to ``default`` and emit a debug log."""
    if value is None:
        return default
    if isinstance(value, OrientConvention):
        return value
    try:
        return OrientConvention(str(value))
    except ValueError:
        log.debug("unknown orient convention %r; using default %s", value, default)
        return default


# Per-kind default. Future-flag: an engine-wide setting (settings.json
# ``moverControl.orientConvention``) overrides this; per-fixture or
# per-claim overrides win over the engine default in turn.
#
# #777 — gyro-puck switched from BOTTOM_FORWARD_ROLL_PITCH (yaw-dropped)
# to FLAT_PITCH_YAW (full Euler). The yaw-drop was a workaround for the
# old "stick-mounted, forward through the bottom" pose that put the puck
# in continuous gimbal lock. Live test on 2026-05-01 (see
# docs/imu-axis-test-2026-05-01.md) confirmed yaw is the cleanest signal
# for pan when the puck is held LCD-up / +X-forward, so the workaround
# is no longer the right default.
_DEFAULT_CONVENTION_BY_KIND = {
    "gyro-puck": OrientConvention.FLAT_PITCH_YAW,
    "phone":     OrientConvention.FLAT_PITCH_YAW,
}


def default_convention_for_kind(kind):
    """Engine default convention for a remote of the given ``kind``."""
    return _DEFAULT_CONVENTION_BY_KIND.get(
        kind, OrientConvention.FLAT_PITCH_YAW)

# ── Constants ─────────────────────────────────────────────────────────────

# Body-frame axes of a remote: forward = +X, right = +Y, up = +Z (right-
# handed). Confirmed against the QMI8658 chip's native frame on the
# Waveshare ESP32-S3 puck — when the puck is held LCD-up with its +X
# axis along the wand's pointing direction, pan = yaw (rotation around
# +Z, drifts) and tilt = pitch (rotation around +Y, accel-anchored).
# Live test 2026-05-01: docs/imu-axis-test-2026-05-01.md (#777).
#
# Earlier convention was forward = +Y; flipped to +X on 2026-05-01
# alongside switching the gyro-puck default OrientConvention from
# BOTTOM_FORWARD_ROLL_PITCH to FLAT_PITCH_YAW. Android phones in
# controller mode also produce X-forward Euler now (landscape grip with
# the device's top edge — the +X side — pointing toward the stage).
REMOTE_FORWARD_LOCAL = (1.0, 0.0, 0.0)
REMOTE_UP_LOCAL      = (0.0, 0.0, 1.0)

# Staleness thresholds. Decision #7 says "N days" — N=7 initially.
#
# Two-tier comms staleness (#476):
#   soft (5-60s):   puck fell off the air briefly — keep the claim, stop
#                   writing pan/tilt (dmx frozen), UI pulses amber
#                   "Reconnecting..."
#   hard (>60s):    gone for long enough we should drop the claim and
#                   blackout — operator will need to re-Send-Lock.
STALE_AGE_SECS   = 7 * 24 * 3600
STALE_HARD_SECS  = 60      # comms silence beyond this → hard-stale (auto-release)
STALE_SOFT_SECS  = 5       # comms silence beyond this → soft-stale (freeze dmx)
# #690 — orphan-pruning grace periods for remotes that registered (e.g. via
# UDP) but never sent orientation data. Picked to err generous: an operator
# might register a puck before turning it on. Hard prune at 1 h.
STALE_NEVER_SOFT_SECS = 5 * 60     # 5 minutes
STALE_NEVER_HARD_SECS = 60 * 60    # 1 hour

# Backwards-compatibility alias — some call sites referenced the original
# single threshold. Kept as the hard value.
STALE_COMMS_SECS = STALE_HARD_SECS

CONNECTION_STATES = ("idle", "armed", "streaming", "stale")

# Remote kinds (extend as needed).
KIND_PUCK  = "gyro-puck"
KIND_PHONE = "phone"
VALID_KINDS = {KIND_PUCK, KIND_PHONE}


# ── Remote ────────────────────────────────────────────────────────────────

class Remote:
    """A single remote controller as a stage-space object."""

    __slots__ = (
        "id", "name", "kind", "device_id", "pos", "rot",
        "R_world_to_stage", "calibrated", "calibrated_at",
        "calibrated_against", "stale_reason", "soft_stale",
        "last_quat_world", "aim_stage", "up_stage", "last_data",
        "connection_state", "registered_at",
        # #762 — orient axis convention (puck defaults to roll+pitch only,
        # yaw dropped to immunise against compassless drift; phone defaults
        # to legacy roll+pitch+yaw quaternion).
        "convention",
    )

    def __init__(self, id, name="", kind=KIND_PUCK, device_id=None,
                 pos=None, rot=None, convention=None):
        self.id = int(id)
        self.name = name or f"Remote {id}"
        self.kind = kind if kind in VALID_KINDS else KIND_PUCK
        self.device_id = device_id
        # #762 — pick per-kind default if no explicit convention was given.
        self.convention = (_coerce_convention(convention)
                           or default_convention_for_kind(self.kind))
        # Default position: stage centre at head height (decision #4).
        # The registry/API layer may override this with a layout-driven value.
        self.pos = list(pos) if pos is not None else [0.0, 0.0, 1600.0]
        self.rot = list(rot) if rot is not None else [0.0, 0.0, 0.0]

        # Runtime (not user-editable)
        self.R_world_to_stage = None  # unit quaternion or None
        self.calibrated = False
        self.calibrated_at = 0.0
        self.calibrated_against = None  # {"objectId": int, "kind": str}
        self.stale_reason = None        # None | "age" | "connection-lost"
                                        # | "session-ended" | "never-active"
        self.soft_stale = False         # transient: comms silent 5-60s
        self.last_quat_world = None     # last sensor orientation in remote world frame
        self.aim_stage = None           # unit vector in stage coords
        self.up_stage = None            # unit "up" in stage coords
        self.last_data = 0.0            # epoch seconds
        self.connection_state = "idle"
        # #690 — first time this remote appeared in the registry. Used by
        # the never-active stale path so an orphan that was registered via
        # UDP once but never re-pinged eventually expires.
        self.registered_at = time.time()

    # ── Persistence ──────────────────────────────────────────────────────

    def to_persisted_dict(self):
        """Fields saved to remotes.json. Transient runtime state is not persisted."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "deviceId": self.device_id,
            "pos": list(self.pos),
            "rot": list(self.rot),
            "R_world_to_stage": list(self.R_world_to_stage) if self.R_world_to_stage else None,
            "calibrated": self.calibrated,
            "calibratedAt": self.calibrated_at,
            "calibratedAgainst": self.calibrated_against,
            "registeredAt": self.registered_at,
            # #762 — only persist if the operator has overridden the per-
            # kind default; otherwise rely on the kind→default table so a
            # future flip of the global default propagates to old records.
            "orientConvention": (self.convention.value
                                 if self.convention != default_convention_for_kind(self.kind)
                                 else None),
        }

    @classmethod
    def from_persisted_dict(cls, d):
        r = cls(
            id=d["id"],
            name=d.get("name", ""),
            kind=d.get("kind", KIND_PUCK),
            device_id=d.get("deviceId"),
            pos=d.get("pos"),
            rot=d.get("rot"),
            convention=d.get("orientConvention"),
        )
        q = d.get("R_world_to_stage")
        if q and len(q) == 4:
            r.R_world_to_stage = tuple(float(x) for x in q)
        r.calibrated = bool(d.get("calibrated", False))
        r.calibrated_at = float(d.get("calibratedAt", 0.0))
        r.calibrated_against = d.get("calibratedAgainst")
        # #690 — fall back to "now" so old persisted records (no field)
        # still get the never-active grace period before pruning.
        r.registered_at = float(d.get("registeredAt") or time.time())
        # Persisted calibration is treated as stale on restart — the
        # operator must explicitly re-confirm alignment this session.
        # Until then: aim_stage is suppressed, viz shows no ray, and
        # consumer features refuse to act (see §7.4 in the design doc).
        if r.calibrated:
            r.stale_reason = "session-ended"
        return r

    # ── Calibration ──────────────────────────────────────────────────────

    def calibrate(self, target_aim_stage, target_info=None,
                  roll=None, pitch=None, yaw=None, quat=None):
        """Compute `R_world_to_stage` from the remote's current orientation
        and the target's stage-space aim vector.

        `target_aim_stage` — unit vector in stage coordinates.
        `target_info` — optional `{"objectId": int, "kind": str}` for UX.
        `roll/pitch/yaw` in degrees, ZYX intrinsic order — use if the
            remote's current orientation is fresh. Mutually exclusive with
            `quat`.
        `quat` — unit `(w, x, y, z)` body-to-world quaternion.

        If none of `roll/pitch/yaw/quat` is supplied, uses `last_quat_world`
        (must have been populated by an `update_*` call).
        """
        if quat is None and (roll is not None or pitch is not None or yaw is not None):
            # #762 — bottom-forward grip ignores yaw entirely (no compass on
            # the puck IMU; yaw integrates and drifts). Calibrate-time pose
            # is built from roll+pitch only, so the resulting R_world_to
            # _stage maps a yaw=0 world frame onto stage. All subsequent
            # orient updates use yaw=0 too — see update_from_euler_deg.
            yaw_for_calib = 0.0 if self.convention == OrientConvention.BOTTOM_FORWARD_ROLL_PITCH else (yaw or 0.0)
            quat = quat_from_euler_zyx_deg(
                roll or 0.0, pitch or 0.0, yaw_for_calib,
            )
        if quat is None:
            if self.last_quat_world is None:
                raise ValueError(
                    "calibrate() needs orientation: pass roll/pitch/yaw or "
                    "quat, or call an update method first."
                )
            quat = self.last_quat_world

        self.last_quat_world = tuple(quat)

        # Remote forward and up, in the remote's own world frame.
        f_remote = quat_rotate_vec(quat, REMOTE_FORWARD_LOCAL)
        u_remote = quat_rotate_vec(quat, REMOTE_UP_LOCAL)

        # Stage "up" reference: stage +Z projected onto the plane
        # perpendicular to the target aim. See §4.1 step 2.
        a_stage = normalize3(target_aim_stage)
        z_stage = (0.0, 0.0, 1.0)
        d = dot3(z_stage, a_stage)
        u_stage = normalize3((
            z_stage[0] - d * a_stage[0],
            z_stage[1] - d * a_stage[1],
            z_stage[2] - d * a_stage[2],
        ))
        # If target aims straight up or down, pick a fallback stage "up"
        # in the XY plane so frame_align has a well-defined secondary axis.
        if u_stage == (0.0, 0.0, 0.0):
            u_stage = (0.0, 1.0, 0.0)  # stage +Y

        self.R_world_to_stage = frame_align(f_remote, u_remote, a_stage, u_stage)
        self.calibrated = True
        self.calibrated_at = time.time()
        self.calibrated_against = target_info
        self.stale_reason = None
        self.connection_state = "streaming" if self.last_data else "armed"

        # Refresh derived state so aim_stage reflects the new calibration
        # immediately (not only on the next update).
        self._recompute_derived()

    # ── Orientation updates ─────────────────────────────────────────────

    def update_from_euler_deg(self, roll, pitch, yaw):
        """Accept ZYX intrinsic Euler (aerospace convention) in degrees.

        The ESP32 puck's roll/pitch/yaw and the Android phone's Euler
        fallback both use this convention.

        #762 — under ``BOTTOM_FORWARD_ROLL_PITCH`` (default for the gyro
        puck) yaw is dropped before the quaternion is built. The puck's
        QMI8658 IMU has no magnetometer, so its yaw integrates indefinitely
        and drifts; both the calibrate-time R_world_to_stage and the live
        orient stream use yaw=0, which leaves the world frame anchored to
        gravity-only roll+pitch. Operator gestures around the gravity axis
        (pure yaw motion) are deliberately ignored — there is no compass to
        anchor them, so they would only show up as drift.
        """
        if self.convention == OrientConvention.BOTTOM_FORWARD_ROLL_PITCH:
            yaw = 0.0
        self._apply_quat(quat_from_euler_zyx_deg(roll, pitch, yaw))

    def update_from_quat(self, quat):
        """Accept a unit (w, x, y, z) quaternion directly.

        Reserved for the future Android native-quat wire format (tracked
        as a follow-up issue). Current v1 wire format is Euler only.
        """
        self._apply_quat(tuple(float(x) for x in quat))

    def _apply_quat(self, q):
        self.last_quat_world = q
        self.last_data = time.time()
        self._recompute_derived()

    def _recompute_derived(self):
        """Recompute aim_stage/up_stage/connection_state from current state."""
        if self.stale_reason is not None:
            self.connection_state = "stale"
            return
        if not self.calibrated or self.R_world_to_stage is None:
            self.connection_state = "idle"
            self.aim_stage = None
            self.up_stage = None
            return
        if self.last_quat_world is None:
            self.connection_state = "armed"
            return
        f_world = quat_rotate_vec(self.last_quat_world, REMOTE_FORWARD_LOCAL)
        u_world = quat_rotate_vec(self.last_quat_world, REMOTE_UP_LOCAL)
        self.aim_stage = quat_rotate_vec(self.R_world_to_stage, f_world)
        self.up_stage = quat_rotate_vec(self.R_world_to_stage, u_world)
        self.connection_state = "streaming"

    # ── Staleness ────────────────────────────────────────────────────────

    def check_staleness(self, now=None):
        """Update `stale_reason` + `soft_stale` based on age + comms.

        - soft_stale:  transient — comms silence 5-60s; auto-clears when
                       the next orient packet arrives. UI shows
                       "Reconnecting..." and dmx writes freeze.
        - stale_reason (hard): latched — age > N days, session-ended, or
                       comms > 60s. Requires re-calibrate/clear-stale.
        """
        if now is None:
            now = time.time()

        # Hard latch already set — skip transient updates.
        if self.stale_reason:
            self.soft_stale = False
            return

        # #690 — never-active orphan: registered (often via auto-register
        # on a single UDP packet) but no orientation data has ever arrived.
        # Without this branch, an orphan with `calibrated=False` and
        # `last_data=0` sits in the registry forever — the original
        # check_staleness early-returned at the !calibrated guard.
        if not self.calibrated and self.last_data <= 0.0:
            silence = now - self.registered_at
            if silence > STALE_NEVER_HARD_SECS:
                self.stale_reason = "never-active"
                self.connection_state = "stale"
                self.soft_stale = False
            elif silence > STALE_NEVER_SOFT_SECS:
                self.soft_stale = True
                self.connection_state = "idle"
            else:
                self.soft_stale = False
                self.connection_state = "idle"
            return

        if not self.calibrated or self.R_world_to_stage is None:
            return

        # Age-out (hard).
        if now - self.calibrated_at > STALE_AGE_SECS:
            self.stale_reason = "age"
            self.connection_state = "stale"
            self.soft_stale = False
            return

        # Comms silence → soft / hard.
        if self.last_data > 0:
            silence = now - self.last_data
            if silence > STALE_HARD_SECS:
                self.stale_reason = "connection-lost"
                self.connection_state = "stale"
                self.soft_stale = False
            elif silence > STALE_SOFT_SECS:
                self.soft_stale = True
            else:
                self.soft_stale = False

    def end_session(self):
        """Explicit user signal — remote is no longer in active use."""
        self.stale_reason = "session-ended"
        self.connection_state = "stale"

    def clear_stale(self):
        self.stale_reason = None
        self._recompute_derived()

    # ── Serialisation for the live API ──────────────────────────────────

    def live_dict(self, now=None):
        if now is None:
            now = time.time()
        self.check_staleness(now)
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "deviceId": self.device_id,
            "pos": list(self.pos),
            "rot": list(self.rot),
            "calibrated": self.calibrated,
            "calibratedAt": self.calibrated_at,
            "calibratedAgainst": self.calibrated_against,
            "staleReason": self.stale_reason,
            "softStale": self.soft_stale,
            "hardStale": self.stale_reason is not None,
            "aim": list(self.aim_stage) if self.aim_stage else None,
            "up": list(self.up_stage) if self.up_stage else None,
            "connectionState": self.connection_state,
            "lastDataAge": (now - self.last_data) if self.last_data else None,
            # #762 — surface so the dashboard can show "puck (roll+pitch)" vs
            # "phone (roll+pitch+yaw)" without inferring it from kind.
            "orientConvention": self.convention.value,
        }

    def set_convention(self, value):
        """#762 — change convention at runtime (per-claim override path).
        Recomputes derived state so aim_stage reflects the new mapping
        immediately without waiting for the next orient packet."""
        new_conv = _coerce_convention(value, default=self.convention)
        if new_conv == self.convention:
            return
        self.convention = new_conv
        # If we're already calibrated, the existing R_world_to_stage was
        # built under the previous convention. Don't silently keep using
        # it — force the operator to recalibrate by clearing it. (A more
        # invasive option would be to project last_quat_world to the new
        # convention's yaw-zero plane, but the safer call is "reset and
        # ask for a fresh anchor".)
        if self.calibrated:
            self.calibrated = False
            self.R_world_to_stage = None
            self.connection_state = "armed" if self.last_data else "idle"
            self.aim_stage = None
            self.up_stage = None


# ── RemoteRegistry ────────────────────────────────────────────────────────

class RemoteRegistry:
    """Thread-safe registry of remotes with JSON persistence.

    Not coupled to Flask — the parent server imports and owns an instance.
    """

    def __init__(self, data_path=None):
        self._lock = threading.RLock()
        self._remotes: dict[int, Remote] = {}
        self._next_id = 1
        self._data_path = data_path

    # Persistence ──────────────────────────────────────────────────────

    def load(self):
        if not self._data_path or not os.path.exists(self._data_path):
            return
        try:
            with open(self._data_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            log.error("Failed to read %s: %s", self._data_path, e)
            return
        with self._lock:
            self._remotes.clear()
            self._next_id = 1
            for d in data.get("remotes", []):
                try:
                    r = Remote.from_persisted_dict(d)
                except Exception as e:
                    log.error("Skipping bad remote %r: %s", d, e)
                    continue
                self._remotes[r.id] = r
                if r.id >= self._next_id:
                    self._next_id = r.id + 1
            log.info("Loaded %d remotes from %s", len(self._remotes), self._data_path)

    def save(self):
        if not self._data_path:
            return
        with self._lock:
            data = {
                "schemaVersion": 1,
                "remotes": [r.to_persisted_dict() for r in self._remotes.values()],
            }
        tmp = self._data_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._data_path) or ".", exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._data_path)
        except Exception as e:
            log.error("Failed to write %s: %s", self._data_path, e)

    # CRUD ─────────────────────────────────────────────────────────────

    def add(self, name="", kind=KIND_PUCK, device_id=None, pos=None, rot=None):
        with self._lock:
            r = Remote(
                id=self._next_id,
                name=name,
                kind=kind,
                device_id=device_id,
                pos=pos,
                rot=rot,
            )
            self._remotes[r.id] = r
            self._next_id += 1
        self.save()
        return r

    def get(self, remote_id):
        with self._lock:
            return self._remotes.get(int(remote_id))

    def remove(self, remote_id):
        with self._lock:
            r = self._remotes.pop(int(remote_id), None)
        if r:
            self.save()
        return r

    def by_device(self, device_id):
        if device_id is None:
            return None
        with self._lock:
            for r in self._remotes.values():
                if r.device_id == device_id:
                    return r
            return None

    def list(self):
        with self._lock:
            return list(self._remotes.values())

    def update_fields(self, remote_id, **fields):
        """Update static fields on a remote (name, pos, rot, deviceId).

        Calibration state and runtime fields are not writable this way —
        use `calibrate()` on the Remote or `remove()` + re-add.
        """
        with self._lock:
            r = self._remotes.get(int(remote_id))
            if r is None:
                return None
            if "name" in fields and fields["name"] is not None:
                r.name = str(fields["name"])
            if "pos" in fields and fields["pos"] is not None:
                pos = fields["pos"]
                if len(pos) == 3:
                    r.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
            if "rot" in fields and fields["rot"] is not None:
                rot = fields["rot"]
                if len(rot) == 3:
                    r.rot = [float(rot[0]), float(rot[1]), float(rot[2])]
            if "deviceId" in fields:
                r.device_id = fields["deviceId"]
            if "kind" in fields and fields["kind"] in VALID_KINDS:
                r.kind = fields["kind"]
        self.save()
        return r

    # Aggregate views ──────────────────────────────────────────────────

    def live_list(self, prune_hard_stale=True):
        """Return live snapshots of every remote.

        When ``prune_hard_stale`` is True (default), any remote whose
        staleness check has just promoted to hard-stale (``stale_reason``
        set) is removed from the registry first. Operators see the orphan
        disappear once the pruning grace period elapses without manual
        intervention. #690.
        """
        now = time.time()
        pruned = []
        with self._lock:
            # First pass: tick staleness so any orphan crossings flip now.
            for r in self._remotes.values():
                r.check_staleness(now)
            if prune_hard_stale:
                for rid, r in list(self._remotes.items()):
                    # "never-active" is the orphan path; the others
                    # (age / connection-lost / session-ended) historically
                    # leave the entry in place so the operator can see
                    # what failed and recalibrate. Auto-prune only the
                    # never-active orphan to match the issue scope.
                    if r.stale_reason == "never-active":
                        del self._remotes[rid]
                        pruned.append(rid)
            snap = [r.live_dict(now) for r in self._remotes.values()]
        if pruned:
            log.info("RemoteRegistry: pruned %d never-active orphan(s): %s",
                     len(pruned), pruned)
            self.save()
        return snap

    def tick_staleness(self):
        now = time.time()
        with self._lock:
            for r in self._remotes.values():
                r.check_staleness(now)
