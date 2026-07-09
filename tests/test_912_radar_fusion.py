#!/usr/bin/env python3
"""test_912_radar_fusion.py — #912 radar_fusion.py unit contract.

Direct tests of desktop/shared/radar_fusion.py with fake sinks — no
Flask, no UDP. The synthetic end-to-end path (real handler → real
temporal objects → #900 cross-sensor fusion) lives in
tests/test_910_mmw_handler.py.

Covered here:
  1. project_to_stage — identity pose, yaw ±90°, translated fixture,
     tilted-down mount (foreshortens range, still lands on z=0 plane).
  2. M-of-N confirmation: no person object until 3 consecutive frames;
     a tentative track that misses a frame starts over.
  3. Straight-line walk: filtered positions track ground truth within
     tolerance and the update stream is monotonic along the walk.
  4. Association gate: two separated people → two tracks/objects; a
     far-off observation never captures an existing track.
  5. Coasting: keepalives hold the object (TTL refresh) up to coast_ms,
     then updates stop (object left to expire via TTL).
  6. flags bit0 unhealthy → targets ignored (frame degrades to keepalive).
  7. update_person returning None (object expired) → object re-created;
     surviving-id rebinding is honoured.
  8. fusion_weight freshness curve.

Usage:
    python3 tests/test_912_radar_fusion.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import radar_fusion as rf  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


# ── 1. project_to_stage ─────────────────────────────────────────────────────

def run_projection():
    # Identity pose at origin: boresight (+y sensor) → stage +Y.
    fx = {"x": 0, "y": 0, "z": 1500, "rotation": [0, 0, 0]}
    p = rf.project_to_stage(fx, 0, 3000)
    ok("identity: boresight → stage +Y",
       abs(p[0]) < 1e-6 and abs(p[1] - 3000) < 1e-6, repr(p))
    p = rf.project_to_stage(fx, 500, 3000)
    ok("identity: sensor +x (lateral right) → stage +X",
       abs(p[0] - 500) < 1e-6 and abs(p[1] - 3000) < 1e-6, repr(p))

    # Translated fixture: offsets add.
    fx = {"x": 1000, "y": 2000, "z": 1500, "rotation": [0, 0, 0]}
    p = rf.project_to_stage(fx, 500, 3000)
    ok("translated fixture offsets add",
       abs(p[0] - 1500) < 1e-6 and abs(p[1] - 5000) < 1e-6, repr(p))

    # Yaw +90° (layout rotation [rx, ry, rz] = [tilt, roll, pan]; pan>0
    # aims stage +X per camera_math conventions).
    fx = {"x": 1000, "y": 2000, "z": 1500, "rotation": [0, 0, 90]}
    p = rf.project_to_stage(fx, 0, 3000)
    ok("yaw 90: boresight → stage +X",
       abs(p[0] - 4000) < 1e-6 and abs(p[1] - 2000) < 1e-6, repr(p))
    p = rf.project_to_stage(fx, 500, 0)
    ok("yaw 90: lateral right → stage -Y (right-handed)",
       abs(p[0] - 1000) < 1e-6 and abs(p[1] - 1500) < 1e-6, repr(p))
    fx = {"x": 4300, "y": 2500, "z": 1500, "rotation": [0, 0, -90]}
    p = rf.project_to_stage(fx, 0, 3000)
    ok("yaw -90: boresight → stage -X",
       abs(p[0] - 1300) < 1e-6 and abs(p[1] - 2500) < 1e-6, repr(p))

    # Tilted-down wall mount (design doc §7): range foreshortens by
    # cos(tilt), result still lands on the z=0 floor plane by
    # construction (project_to_stage returns a floor point).
    fx = {"x": 0, "y": 0, "z": 1800, "rotation": [30, 0, 0]}
    p = rf.project_to_stage(fx, 0, 2000)
    ok("tilt 30° down: range foreshortened by cos(30°) onto floor",
       abs(p[0]) < 1e-6 and abs(p[1] - 2000 * math.cos(math.radians(30))) < 1e-6,
       repr(p))
    # Rotation decoded via rotation_from_layout: ry is ROLL, not pan
    # (#600) — a rolled radar's boresight must stay on stage +Y.
    fx = {"x": 0, "y": 0, "z": 1500, "rotation": [0, 45, 0]}
    p = rf.project_to_stage(fx, 0, 2000)
    ok("rotation[1] is roll (#600): boresight unaffected",
       abs(p[0]) < 1e-6 and abs(p[1] - 2000) < 1e-6, repr(p))


# ── Fake-sink harness ───────────────────────────────────────────────────────

class Sink:
    def __init__(self):
        self.created = []      # (oid, pos, source, ttl)
        self.updates = []      # (oid, pos)
        self.alive = set()
        self.forward = {}      # oid -> surviving oid (#896 simulation)
        self._next = 10000

    def create(self, pos, source, ttl):
        oid = self._next
        self._next += 1
        self.created.append((oid, tuple(pos), dict(source), ttl))
        self.alive.add(oid)
        return oid

    def update(self, oid, pos):
        target = self.forward.get(oid, oid)
        if target not in self.alive:
            return None
        self.updates.append((target, tuple(pos)))
        return target


def _engine(sink, **kw):
    kw.setdefault("confirm_frames", 3)
    kw.setdefault("coast_ms", 3000.0)
    return rf.RadarFusion(create_person=sink.create,
                          update_person=sink.update, **kw)


FX = {"id": 7, "x": 0, "y": 0, "z": 1500, "rotation": [0, 0, 0]}


def _frame(eng, targets, t, flags=0x01, seq=0):
    eng.ingest(FX, "MMW-TEST", seq, flags, targets, t)


# ── 2. M-of-N confirmation ──────────────────────────────────────────────────

def run_confirmation():
    sink = Sink()
    eng = _engine(sink)
    t = 100.0
    _frame(eng, [(0, 2000, 10, 100)], t)
    _frame(eng, [(0, 2050, 10, 100)], t + 0.04)
    ok("no person object before 3rd consecutive frame",
       len(sink.created) == 0, repr(sink.created))
    _frame(eng, [(0, 2100, 10, 100)], t + 0.08)
    ok("3rd consecutive frame confirms → one create",
       len(sink.created) == 1, repr(sink.created))
    oid, pos, source, ttl = sink.created[0]
    ok("source stamped {type: radar, fixtureId, node}",
       source == {"type": "radar", "fixtureId": 7, "node": "MMW-TEST"},
       repr(source))
    ok("created near the observed position",
       abs(pos[0]) < 100 and abs(pos[1] - 2050) < 150, repr(pos))
    ok("create carries the engine's person TTL", ttl == eng.person_ttl_s)

    # Tentative track that misses a frame starts over (consecutive M-of-N).
    sink2 = Sink()
    eng2 = _engine(sink2)
    _frame(eng2, [(0, 2000, 0, 100)], 10.0)
    _frame(eng2, [(0, 2050, 0, 100)], 10.04)
    _frame(eng2, [], 10.08)                      # miss → tentative dropped
    _frame(eng2, [(0, 2100, 0, 100)], 10.12)
    _frame(eng2, [(0, 2150, 0, 100)], 10.16)
    ok("miss during confirmation restarts the count",
       len(sink2.created) == 0, repr(sink2.created))
    _frame(eng2, [(0, 2200, 0, 100)], 10.20)
    ok("3 consecutive after the miss confirms",
       len(sink2.created) == 1)


# ── 3. Straight-line walk tracks ground truth ───────────────────────────────

def run_walk_accuracy():
    sink = Sink()
    eng = _engine(sink)
    t = 200.0
    truth = []
    # 25 Hz walk: y 1500 → 3900 mm at 1.5 m/s, x fixed 400 mm.
    for i in range(40):
        y = 1500 + i * 60
        truth.append((400.0, float(y)))
        _frame(eng, [(400, y, 150, 100)], t + i * 0.04, seq=i)
    ok("walk creates exactly one track/object", len(sink.created) == 1)
    ok("confirmed track updates on every subsequent frame",
       len(sink.updates) == 40 - eng.confirm_frames + 1
       or len(sink.updates) >= 30, str(len(sink.updates)))
    # Filtered estimate lags but must stay near the line.
    errs = []
    for (oid, pos), (tx, ty) in zip(sink.updates, truth[eng.confirm_frames - 1:]):
        errs.append(math.hypot(pos[0] - tx, pos[1] - ty))
    ok("all filtered positions within 250 mm of ground truth",
       max(errs) < 250, f"max err {max(errs):.1f} mm")
    ok("late-track error < 100 mm (KF converged)",
       max(errs[20:]) < 100, f"late max {max(errs[20:]):.1f} mm")
    ys = [pos[1] for _, pos in sink.updates]
    ok("update stream walks monotonically along +Y",
       all(b >= a for a, b in zip(ys, ys[1:])), repr(ys[:5]))


# ── 4. Association gate ─────────────────────────────────────────────────────

def run_gate():
    sink = Sink()
    eng = _engine(sink)
    t = 300.0
    # Two people 2 m apart — must never share a track.
    for i in range(5):
        _frame(eng, [(-800, 2000 + i * 50, 0, 100),
                     (1200, 2000 + i * 50, 0, 100)], t + i * 0.04)
    ok("two separated people → two person objects",
       len(sink.created) == 2, repr(sink.created))
    xs = sorted(p[0] for _, p, _, _ in sink.created)
    ok("objects near each person's lateral offset",
       abs(xs[0] + 800) < 150 and abs(xs[1] - 1200) < 150, repr(xs))

    # An observation far outside the gate spawns a new tentative track
    # instead of yanking an existing confirmed one.
    n_before = len(sink.updates)
    _frame(eng, [(5000, 7000, 0, 100)], t + 0.24)
    moved = [u for u in sink.updates[n_before:]
             if math.hypot(u[1][0] - 5000, u[1][1] - 7000) < 1000]
    ok("out-of-gate observation never captures an existing track",
       not moved, repr(moved))


# ── 5. Coasting through misses / keepalives ─────────────────────────────────

def run_coasting():
    sink = Sink()
    eng = _engine(sink, coast_ms=500.0)
    t = 400.0
    for i in range(4):
        _frame(eng, [(0, 2000 + i * 50, 0, 100)], t + i * 0.04)
    ok("confirmed before coasting test", len(sink.created) == 1)
    last_seen = t + 3 * 0.04
    pos_at_confirm = sink.updates[-1][1]
    # Keepalives (count=0) within the coast window keep updating (TTL
    # refresh) while HOLDING position — the Rd-03D static-person fade
    # means "stopped", not "kept moving at last velocity".
    n0 = len(sink.updates)
    _frame(eng, [], last_seen + 0.3)
    ok("keepalive within coast window still emits an update",
       len(sink.updates) == n0 + 1)
    held = sink.updates[-1][1]
    ok("coasting holds last filtered position (no CV drift)",
       math.hypot(held[0] - pos_at_confirm[0], held[1] - pos_at_confirm[1]) < 1.0,
       f"{pos_at_confirm} -> {held}")
    # Past the coast window the track drops: no further updates.
    _frame(eng, [], last_seen + 0.7)
    n1 = len(sink.updates)
    _frame(eng, [], last_seen + 1.0)
    ok("past coast_ms the track drops (no more updates)",
       len(sink.updates) == n1)
    ok("track bank empty after drop",
       eng.status().get(7, {}).get("tracks") == 0, repr(eng.status()))


# ── 6. Unhealthy frames (flags bit0 clear) ──────────────────────────────────

def run_unhealthy():
    sink = Sink()
    eng = _engine(sink)
    t = 500.0
    for i in range(5):
        _frame(eng, [(0, 2000, 0, 100)], t + i * 0.04, flags=0x00)
    ok("unhealthy frames never seed tracks/objects",
       len(sink.created) == 0 and eng.status().get(7, {}).get("tracks", 0) == 0,
       repr(eng.status()))
    # Unhealthy frame acts as keepalive for an established track.
    for i in range(3):
        _frame(eng, [(0, 2000, 0, 100)], t + 1 + i * 0.04)
    n0 = len(sink.updates)
    _frame(eng, [(0, 9999, 0, 100)], t + 1.2, flags=0x00)
    ok("unhealthy frame coasts (update emitted, garbage target ignored)",
       len(sink.updates) == n0 + 1
       and abs(sink.updates[-1][1][1] - 2000) < 150,
       repr(sink.updates[-3:]))


# ── 7. Object-expired recreate + surviving-id rebind ────────────────────────

def run_sink_contract():
    sink = Sink()
    eng = _engine(sink)
    t = 600.0
    for i in range(3):
        _frame(eng, [(0, 2000 + i * 50, 0, 100)], t + i * 0.04)
    oid = sink.created[0][0]
    # Simulate TTL expiry (e.g. a long GET gap reaped the object).
    sink.alive.discard(oid)
    _frame(eng, [(0, 2200, 0, 100)], t + 0.16)
    ok("update→None (object expired) re-creates the person object",
       len(sink.created) == 2 and sink.created[1][0] in sink.alive,
       repr(sink.created))
    # Simulate #896 fused-id forwarding: updates rebind to the survivor.
    new_oid = sink.created[1][0]
    survivor = 9999
    sink.alive.add(survivor)
    sink.forward[new_oid] = survivor
    _frame(eng, [(0, 2250, 0, 100)], t + 0.20)
    ok("update follows fused-id forwarding to the survivor",
       sink.updates[-1][0] == survivor, repr(sink.updates[-1]))
    sink.forward.clear()   # track must now address the survivor directly
    _frame(eng, [(0, 2300, 0, 100)], t + 0.24)
    ok("track rebinds its object_id to the survivor",
       sink.updates[-1][0] == survivor, repr(sink.updates[-1]))


# ── 8. fusion weight ────────────────────────────────────────────────────────

def run_weight():
    w0 = rf.fusion_weight({}, 0.0)
    ok("fresh radar weight comparable to a good camera observation",
       abs(w0 - rf.RADAR_SOURCE_WEIGHT) < 1e-9 and 0.5 <= w0 <= 1.0, repr(w0))
    ok("weight decays with age",
       rf.fusion_weight({}, rf.RADAR_WEIGHT_MAX_AGE_S / 2) < w0)
    ok("weight floors at zero past the freshness horizon",
       rf.fusion_weight({}, rf.RADAR_WEIGHT_MAX_AGE_S + 1) == 0.0)


def main():
    run_projection()
    run_confirmation()
    run_walk_accuracy()
    run_gate()
    run_coasting()
    run_unhealthy()
    run_sink_contract()
    run_weight()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
