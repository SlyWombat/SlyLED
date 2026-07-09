"""radar_fusion.py — MMwave radar ingestion → stage-frame person tracks (#912).

Design doc: docs/design/mmwave_tracking.md §4.4 (coordinate handling)
and §5.2 (fusion pipeline). The node is dumb: it reports raw Rd-03D
sensor-frame millimetres over UDP 0x70 (see mmwave/MmwProtocol.h and
parent_server._handle_mmw_targets, #910). Everything tunable lives
here, server-side:

  sensor (x, y) ──project_to_stage()──▶ stage-floor (x, y) [z = 0]
        │  per radar fixture: gated nearest-neighbour association
        ▼  (gate 500 mm, matching the camera tracker's REID threshold)
  per-track constant-velocity Kalman (per-axis; dt from arrival times)
        │  M-of-N confirmation: 3 consecutive frames before a track
        ▼  becomes a person object; coast ≤3000 ms through misses
  temporal person objects (objectType "person", TTL, pink) via the
  injected sinks — the SAME internal path the camera tracker's objects
  take, stamped source={"type": "radar", "fixtureId", "node"} so the
  #900 per-source-weight fusion merges radar↔radar and radar↔camera
  observations of the same person for free.

Sensor frame (MmwProtocol.h MmwTarget): +y = boresight forward,
+x = lateral right from the radar's point of view. The fixture pose
(layout position mm + `rotation`, read ONLY via
camera_math.rotation_from_layout — CLAUDE.md #586/#600) carries the
mount; the Rd-03D is planar, so the transformed point is dropped to
the stage floor plane (z = 0) — a down-tilted mount simply foreshortens
range by cos(tilt), no special-case math (§4.4).

Thread-safety: ingest() runs on parent_server's UDP listener thread and
guards all tracker state with this module's own lock; the injected
sinks take parent_server's temporal-object lock internally. Lock order
is always radar_fusion lock → parent lock (nothing calls back into this
module while holding the parent lock), so no inversion is possible.

No third-party dependencies; plain-float math (server-side, so the
firmware integer-only rule doesn't apply).
"""

import math
import threading

from camera_math import build_camera_to_stage, rotation_from_layout

# ── Tuning defaults ──────────────────────────────────────────────────────────
# All of these are bench-tunable: #908 (C61 + Rd-03D bench validation,
# design doc §10 items 3-5) characterises angle jitter vs range and the
# static-person fade time; revise from measured numbers, not by feel.
GATE_MM = 500.0            # association gate — matches camera REID_THRESHOLD_MM
CONFIRM_FRAMES = 3         # M-of-N ghost rejection (§5.2): consecutive frames
COAST_MS = 3000.0          # ride through the Rd-03D static-person fade (§2.2)
PERSON_TTL_S = 2.0         # temporal-object TTL; refreshed on every update
MEAS_SIGMA_MM = 200.0      # Rd-03D position noise (angle-dominated; #908 tunes)
ACCEL_SIGMA_MM_S2 = 1500.0 # CV-model process noise (people manoeuvre; #908 tunes)
MAX_KF_DT_S = 5.0          # clamp pathological arrival gaps

PERSON_COLOR = "#f472b6"   # pink — same as the camera tracker's person objects
PERSON_HEIGHT_MM = 1700.0  # default person height; object pos is the center

# ── #900 fusion weight ───────────────────────────────────────────────────────
# Registered by parent_server as register_fusion_source_weight("radar", ...).
# A confirmed, Kalman-filtered radar placement is comparable to a good
# camera observation (homography tier × ~0.8 YOLO confidence) — radar
# positions are metric but angle-coarse. Bench-tunable (#908): in overlap
# zones the covariance-weighted mean realises the §3.3 triangulation gain.
RADAR_SOURCE_WEIGHT = 0.8
RADAR_WEIGHT_MAX_AGE_S = 2.0   # freshness horizon, mirrors _FUSION_MAX_AGE_S


def fusion_weight(obj, obj_age_s):
    """Fusion weight for a temporal object with source.type == "radar"."""
    freshness = max(0.0, 1.0 - (obj_age_s / RADAR_WEIGHT_MAX_AGE_S))
    return RADAR_SOURCE_WEIGHT * freshness


# ── Sensor → stage projection (§4.4) ─────────────────────────────────────────

def project_to_stage(fixture, x_mm, y_mm):
    """Project one Rd-03D sensor-frame observation to the stage floor.

    `fixture` is a pose snapshot: {"x", "y", "z"} position in stage mm
    (from the layout store) and "rotation" ([rx, ry, rz], decoded ONLY
    via rotation_from_layout). Sensor axes: +y boresight forward, +x
    lateral right (radar's PoV). Returns (stage_x_mm, stage_y_mm) on the
    z = 0 floor plane.

    The mapping to camera_math's pinhole frame (+X right, +Y down,
    +Z forward) is: radar x → pinhole X, radar y → pinhole Z, pinhole
    Y = 0 (the radar senses in its own horizontal plane). That makes an
    identity-rotation radar face stage +Y exactly like a camera, so one
    rotation convention serves both sensor types.
    """
    tilt, pan, roll = rotation_from_layout(fixture.get("rotation") or [0, 0, 0])
    R = build_camera_to_stage(tilt, pan, roll)
    lx, ly, lz = float(x_mm), 0.0, float(y_mm)
    if hasattr(R, "shape"):  # numpy path
        v = R @ [lx, ly, lz]
        vx, vy = float(v[0]), float(v[1])
    else:                    # nested-list fallback
        vx = R[0][0] * lx + R[0][1] * ly + R[0][2] * lz
        vy = R[1][0] * lx + R[1][1] * ly + R[1][2] * lz
    # Drop to the stage floor plane (z = 0): the Rd-03D is planar (§2.2),
    # so vz carries no target information — only the mount geometry.
    sx = float(fixture.get("x", 0) or 0) + vx
    sy = float(fixture.get("y", 0) or 0) + vy
    return (sx, sy)


# ── Per-axis constant-velocity Kalman filter ─────────────────────────────────

class _AxisKF:
    """1-D constant-velocity Kalman filter (state [pos, vel], 2×2 P).

    Two independent axis filters make a diagonal 2-D CV tracker — the
    Rd-03D gives no cross-axis covariance to exploit, and this keeps the
    math dependency-free. Noise defaults above; #908 bench-tunes them.
    """

    __slots__ = ("p", "v", "p00", "p01", "p11", "_r", "_q")

    def __init__(self, z, meas_sigma_mm=MEAS_SIGMA_MM,
                 accel_sigma=ACCEL_SIGMA_MM_S2):
        self.p = float(z)
        self.v = 0.0
        self._r = meas_sigma_mm ** 2
        self._q = accel_sigma ** 2
        self.p00 = self._r          # position var seeded at measurement var
        self.p01 = 0.0
        self.p11 = 1000.0 ** 2      # generous initial velocity var (mm/s)²

    def extrapolate(self, dt):
        """Predicted position dt seconds ahead — no state mutation."""
        return self.p + self.v * dt

    def step(self, dt, z):
        """Predict by dt then update with measurement z (mm)."""
        if dt > 0:
            # P' = F P Fᵀ + Q,  F = [[1, dt], [0, 1]],
            # Q = q·[[dt⁴/4, dt³/2], [dt³/2, dt²]] (white-accel CV model)
            p = self.p + self.v * dt
            p00 = (self.p00 + 2 * dt * self.p01 + dt * dt * self.p11
                   + self._q * dt ** 4 / 4.0)
            p01 = self.p01 + dt * self.p11 + self._q * dt ** 3 / 2.0
            p11 = self.p11 + self._q * dt * dt
        else:
            p, p00, p01, p11 = self.p, self.p00, self.p01, self.p11
        s = p00 + self._r
        kp = p00 / s
        kv = p01 / s
        innov = float(z) - p
        self.p = p + kp * innov
        self.v = self.v + kv * innov
        self.p00 = (1.0 - kp) * p00
        self.p01 = (1.0 - kp) * p01
        self.p11 = p11 - kv * p01


class _Track:
    """One per-radar person hypothesis."""

    __slots__ = ("kx", "ky", "hits", "confirmed", "object_id",
                 "last_seen", "speed_cms", "flags")

    def __init__(self, sx, sy, t, meas_sigma_mm, accel_sigma):
        self.kx = _AxisKF(sx, meas_sigma_mm, accel_sigma)
        self.ky = _AxisKF(sy, meas_sigma_mm, accel_sigma)
        self.hits = 1              # consecutive frames seen
        self.confirmed = False
        self.object_id = None      # temporal person object id once confirmed
        self.last_seen = t         # monotonic time of last real observation
        self.speed_cms = 0
        self.flags = 0

    def pos(self):
        return (self.kx.p, self.ky.p)


class RadarFusion:
    """Per-radar-fixture tracker bank feeding temporal person objects.

    Sinks (injected by parent_server so this module stays import-clean):
      create_person((x_mm, y_mm), source_dict, ttl_s) -> object id
      update_person(object_id, (x_mm, y_mm)) -> surviving object id
          (follows the #896 fused-id forwarding) or None if the object
          is gone — the tracker then re-creates it.
    """

    def __init__(self, create_person, update_person, *,
                 gate_mm=GATE_MM, confirm_frames=CONFIRM_FRAMES,
                 coast_ms=COAST_MS, person_ttl_s=PERSON_TTL_S,
                 meas_sigma_mm=MEAS_SIGMA_MM,
                 accel_sigma=ACCEL_SIGMA_MM_S2):
        self._create_person = create_person
        self._update_person = update_person
        self.gate_mm = float(gate_mm)
        self.confirm_frames = int(confirm_frames)
        self.coast_ms = float(coast_ms)
        self.person_ttl_s = float(person_ttl_s)
        self.meas_sigma_mm = float(meas_sigma_mm)
        self.accel_sigma = float(accel_sigma)
        self._lock = threading.Lock()
        self._per_fixture = {}   # fixture id -> {"tracks": [...], "last_seq": int}
        # #913 — calibration-walk recording hook. None (zero overhead)
        # outside a walk; parent_server points it at
        # RadarCalibration.record(fixture_id, x_mm, y_mm, t_mono) while
        # recording. Fed every healthy bound observation in SENSOR frame
        # under this module's lock (lock order fusion → recorder).
        self.record_observation = None

    # -- public API ----------------------------------------------------------

    def clear(self):
        """Drop all tracker state (tests / project reset)."""
        with self._lock:
            self._per_fixture.clear()

    def status(self):
        """JSON-safe per-fixture summary (diagnostics)."""
        with self._lock:
            return {
                fid: {
                    "tracks": len(st["tracks"]),
                    "confirmed": sum(1 for tr in st["tracks"] if tr.confirmed),
                    "lastSeq": st.get("last_seq"),
                }
                for fid, st in self._per_fixture.items()
            }

    def ingest(self, fixture, node, seq, flags, targets, t_mono):
        """One 0x70 MMW_TARGETS frame from one radar.

        fixture: pose snapshot {"id", "x", "y", "z", "rotation"} (#910
        builds it from the fixture record + layout store). node: the
        sender's announced hostname (provenance). targets: iterable of
        (x_mm, y_mm, speed_cms, res_mm) sensor-frame tuples — empty for
        the 1 Hz keepalive, which still advances coasting/expiry below.
        t_mono: arrival time from time.monotonic().
        """
        fid = fixture.get("id")
        healthy = bool(flags & 0x01)
        # flags bit0 = radar-frame parse healthy (MmwProtocol.h). An
        # unhealthy frame's target slots are not trustworthy — treat it
        # as a keepalive (coast established tracks, never seed new ones).
        # #908 bench data may soften this.
        observations = []
        if healthy:
            for tgt in targets:
                x_mm, y_mm = tgt[0], tgt[1]
                sx, sy = project_to_stage(fixture, x_mm, y_mm)
                observations.append((sx, sy, tgt))

        with self._lock:
            # #913 — calibration walk recording: raw sensor-frame coords
            # (projection through a possibly-wrong pose is the solver's
            # business, not the recorder's). No-op when the hook is None.
            hook = self.record_observation
            if hook is not None:
                for _sx, _sy, tgt in observations:
                    hook(fid, tgt[0], tgt[1], t_mono)
            st = self._per_fixture.setdefault(fid, {"tracks": [], "last_seq": None})
            st["last_seq"] = seq
            tracks = st["tracks"]

            # 1. Gated greedy nearest-neighbour association against the
            #    tracks' CV-predicted positions (prediction is read-only
            #    here; the KF only steps on a real match, so coasting
            #    tracks hold their last filtered state — the static-
            #    person fade means "stopped", not "kept moving").
            preds = []
            for tr in tracks:
                dt = min(max(t_mono - tr.last_seen, 0.0), MAX_KF_DT_S)
                preds.append((tr.kx.extrapolate(dt), tr.ky.extrapolate(dt)))
            pairs = []
            for ti in range(len(tracks)):
                for oi in range(len(observations)):
                    d = math.hypot(observations[oi][0] - preds[ti][0],
                                   observations[oi][1] - preds[ti][1])
                    if d <= self.gate_mm:
                        pairs.append((d, ti, oi))
            pairs.sort()
            track_obs = {}
            claimed_obs = set()
            for d, ti, oi in pairs:
                if ti in track_obs or oi in claimed_obs:
                    continue
                track_obs[ti] = oi
                claimed_obs.add(oi)

            # 2. Matched tracks: Kalman step + confirmation / object update.
            survivors = []
            for ti, tr in enumerate(tracks):
                if ti in track_obs:
                    sx, sy, tgt = observations[track_obs[ti]]
                    dt = min(max(t_mono - tr.last_seen, 1e-3), MAX_KF_DT_S)
                    tr.kx.step(dt, sx)
                    tr.ky.step(dt, sy)
                    tr.last_seen = t_mono
                    tr.hits += 1
                    tr.speed_cms = tgt[2] if len(tgt) > 2 else 0
                    tr.flags = flags
                    if not tr.confirmed and tr.hits >= self.confirm_frames:
                        tr.confirmed = True
                    if tr.confirmed:
                        self._emit(tr, fid, node)
                    survivors.append(tr)
                else:
                    # Miss. Tentative tracks demand *consecutive* frames
                    # (M-of-N ghost rejection §5.2/§3.1) — one miss kills.
                    if not tr.confirmed:
                        continue
                    if (t_mono - tr.last_seen) * 1000.0 <= self.coast_ms:
                        # Coast: hold position, keep the person object
                        # alive (TTL refresh) through the Rd-03D static-
                        # person fade. Past the window the track drops
                        # and the object expires via its own TTL.
                        self._emit(tr, fid, node)
                        survivors.append(tr)
                # else: dropped — object reaps at _expiresAt.

            # 3. Unclaimed observations seed tentative tracks (Rd-03D caps
            #    at 3 targets, so the bank stays tiny).
            for oi, (sx, sy, tgt) in enumerate(observations):
                if oi in claimed_obs:
                    continue
                tr = _Track(sx, sy, t_mono, self.meas_sigma_mm, self.accel_sigma)
                tr.speed_cms = tgt[2] if len(tgt) > 2 else 0
                tr.flags = flags
                if tr.hits >= self.confirm_frames:   # confirm_frames <= 1
                    tr.confirmed = True
                    self._emit(tr, fid, node)
                survivors.append(tr)

            st["tracks"] = survivors

    # -- internals -----------------------------------------------------------

    def _emit(self, tr, fid, node):
        """Create/update the confirmed track's temporal person object."""
        pos = tr.pos()
        if tr.object_id is not None:
            surviving = self._update_person(tr.object_id, pos)
            if surviving is not None:
                # May differ from object_id when #900 fusion merged this
                # object into a cross-sensor survivor (#896 forwarding) —
                # rebind so later updates go straight to the survivor.
                tr.object_id = surviving
                return
            tr.object_id = None  # object expired underneath us — recreate
        source = {"type": "radar", "fixtureId": fid, "node": node}
        tr.object_id = self._create_person(pos, source, self.person_ttl_s)
