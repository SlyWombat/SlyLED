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

# ── Constants ─────────────────────────────────────────────────────────────

# Body-frame axes of a remote (decision #1): forward = +Y, up = +Z. Both
# puck and phone use the same convention. Android controller mode locks
# landscape so the phone's top edge is forward.
REMOTE_FORWARD_LOCAL = (0.0, 1.0, 0.0)
REMOTE_UP_LOCAL      = (0.0, 0.0, 1.0)

# Staleness thresholds. Decision #7 says "N days" — N=7 initially.
STALE_AGE_SECS   = 7 * 24 * 3600
STALE_COMMS_SECS = 60

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
        "calibrated_against", "stale_reason",
        "last_quat_world", "aim_stage", "up_stage", "last_data",
        "connection_state",
    )

    def __init__(self, id, name="", kind=KIND_PUCK, device_id=None,
                 pos=None, rot=None):
        self.id = int(id)
        self.name = name or f"Remote {id}"
        self.kind = kind if kind in VALID_KINDS else KIND_PUCK
        self.device_id = device_id
        # Default position: stage centre at head height (decision #4).
        # The registry/API layer may override this with a layout-driven value.
        self.pos = list(pos) if pos is not None else [0.0, 0.0, 1600.0]
        self.rot = list(rot) if rot is not None else [0.0, 0.0, 0.0]

        # Runtime (not user-editable)
        self.R_world_to_stage = None  # unit quaternion or None
        self.calibrated = False
        self.calibrated_at = 0.0
        self.calibrated_against = None  # {"objectId": int, "kind": str}
        self.stale_reason = None        # None | "age" | "connection-lost" | "session-ended"
        self.last_quat_world = None     # last sensor orientation in remote world frame
        self.aim_stage = None           # unit vector in stage coords
        self.up_stage = None            # unit "up" in stage coords
        self.last_data = 0.0            # epoch seconds
        self.connection_state = "idle"

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
        )
        q = d.get("R_world_to_stage")
        if q and len(q) == 4:
            r.R_world_to_stage = tuple(float(x) for x in q)
        r.calibrated = bool(d.get("calibrated", False))
        r.calibrated_at = float(d.get("calibratedAt", 0.0))
        r.calibrated_against = d.get("calibratedAgainst")
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
            quat = quat_from_euler_zyx_deg(
                roll or 0.0, pitch or 0.0, yaw or 0.0,
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
        """
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
        """Update `stale_reason` based on age + comms. Call periodically."""
        if not self.calibrated or self.R_world_to_stage is None:
            return
        if self.stale_reason:
            return  # already flagged — requires re-calibrate or clear
        if now is None:
            now = time.time()
        if now - self.calibrated_at > STALE_AGE_SECS:
            self.stale_reason = "age"
            self.connection_state = "stale"
            return
        if self.last_data > 0 and now - self.last_data > STALE_COMMS_SECS:
            self.stale_reason = "connection-lost"
            self.connection_state = "stale"

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
            "aim": list(self.aim_stage) if self.aim_stage else None,
            "up": list(self.up_stage) if self.up_stage else None,
            "connectionState": self.connection_state,
            "lastDataAge": (now - self.last_data) if self.last_data else None,
        }


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

    def live_list(self):
        now = time.time()
        with self._lock:
            return [r.live_dict(now) for r in self._remotes.values()]

    def tick_staleness(self):
        now = time.time()
        with self._lock:
            for r in self._remotes.values():
                r.check_staleness(now)
