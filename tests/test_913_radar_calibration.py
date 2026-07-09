#!/usr/bin/env python3
"""test_913_radar_calibration.py — #913 radar calibration walk:
recorder + Kabsch/Procrustes pose solver + API + apply persistence.

All synthetic, no hardware. A ground-truth L-shaped walk (with a pause
at the corner) is inverse-projected through two radars' TRUE poses into
sensor-frame observations (+ gaussian noise σ=50 mm). Radar A's stored
pose is correct (the layer-1 reference); radar B's stored pose is WRONG
by (Δx=+400 mm, Δy=−250 mm, Δyaw=+12°). The solver must recover B's
true pose from track correlation alone (mmwave_tracking.md §6 layer 2).

Covered:
  1. solve_rigid_2d: exact rigid recovery (no noise), no-scale contract.
  2. End-to-end API: create radar fixtures A/B at stored poses via the
     real routes → /start (hook attached) → feed frames through the real
     RadarFusion.ingest (hook records sensor-frame samples) → /stop
     (per-fixture counts) → /solve with A as reference → B's proposal
     recovers the TRUE pose within 100 mm / 2°, z untouched, tilt/roll
     untouched (pan-only fold via rotation_to_layout).
  3. /apply for B: layout child x/y updated + fixture rotation updated +
     persisted to fixtures.json/layout.json + `radarCalibration` entry in
     the calibrations store (memory + calibrations.json on disk).
  4. Guards: start while recording → 409; stop while idle → 400; solve
     while recording → 400; solve with <2 recorded fixtures → 400 with a
     human message; solve with a sample-less explicit reference → 400;
     apply without a solve / with unknown ids → 400.
  5. Idle-overhead contract: RadarFusion.record_observation is None
     before start and again after stop (the hot path pays nothing).

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_913_radar_calibration.py
"""

import json
import math
import os
import random
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-913-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import radar_calibration as rcal  # noqa: E402
import parent_server as ps  # noqa: E402
from parent_server import app  # noqa: E402
from camera_math import rotation_from_layout  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


# ── Synthetic geometry ───────────────────────────────────────────────────────
# True poses. A faces stage +Y from the back wall; B faces stage -X from
# stage-right (pan -90; crossing coverage per design doc §3.3).
A_TRUE = {"x": 2500.0, "y": 0.0, "z": 1500.0, "rotation": [0, 0, 0]}
B_TRUE = {"x": 5000.0, "y": 2500.0, "z": 1500.0, "rotation": [0, 0, -90]}
# B's stored (layer-1 manual survey) pose is wrong by the pinned deltas.
B_STORED = {"x": B_TRUE["x"] + 400.0, "y": B_TRUE["y"] - 250.0, "z": 1500.0,
            "rotation": [0, 0, -90 + 12]}


def walk_points():
    """L-shaped ground-truth walk @ 10 Hz, 1 m/s, 1 s pause at the
    corner: (1000,1000) → (1000,4000) → pause → (4000,4000). ~7 s."""
    pts, t = [], 0.0
    for i in range(31):                       # leg 1 (3 m, +Y)
        pts.append((t, 1000.0, 1000.0 + i * 100.0)); t += 0.1
    for _ in range(10):                       # pause at the corner
        pts.append((t, 1000.0, 4000.0)); t += 0.1
    for i in range(31):                       # leg 2 (3 m, +X)
        pts.append((t, 1000.0 + i * 100.0, 4000.0)); t += 0.1
    return pts


def inverse_project(pose, wx, wy):
    """Stage point → sensor frame through a pose: exact inverse of
    radar_fusion.project_to_stage's plan view (pan-only poses here).
    M(pan) = [[cos, sin], [-sin, cos]]; sensor = M(pan)ᵀ · (w − p)."""
    _tilt, pan, _roll = rotation_from_layout(pose["rotation"])
    a = math.radians(pan)
    dx, dy = wx - pose["x"], wy - pose["y"]
    return (math.cos(a) * dx - math.sin(a) * dy,
            math.sin(a) * dx + math.cos(a) * dy)


# ── 1. solve_rigid_2d unit contract ─────────────────────────────────────────

def run_rigid_solver_unit():
    rng = random.Random(7)
    theta = math.radians(23.0)
    tx, ty = 700.0, -400.0
    c, s = math.cos(theta), math.sin(theta)
    src = [(rng.uniform(-3000, 3000), rng.uniform(-3000, 3000))
           for _ in range(40)]
    dst = [(c * x - s * y + tx, s * x + c * y + ty) for x, y in src]
    th, ox, oy, rms = rcal.solve_rigid_2d(src, dst)
    ok("rigid solve recovers rotation exactly (no noise)",
       abs(math.degrees(th) - 23.0) < 1e-6, f"{math.degrees(th):.8f}")
    ok("rigid solve recovers translation exactly",
       abs(ox - tx) < 1e-6 and abs(oy - ty) < 1e-6, f"({ox}, {oy})")
    ok("zero residual on exact data", rms < 1e-6, repr(rms))
    # No-scale contract: a uniformly scaled target must NOT come back
    # residual-free (Procrustes here is rigid, not similarity).
    dst2 = [(2.0 * x, 2.0 * y) for x, y in dst]
    _th2, _ox2, _oy2, rms2 = rcal.solve_rigid_2d(src, dst2)
    ok("no scaling solved-for (scaled data leaves residual)", rms2 > 100.0,
       repr(rms2))


# ── 2-5. End-to-end API narrative ────────────────────────────────────────────

def _post(c, path, body=None):
    return c.post(path, data=json.dumps(body or {}),
                  content_type="application/json")


def run_api():
    c = app.test_client()

    # Create the two radar fixtures at their STORED poses via real routes.
    fids = {}
    for name, stored in (("Radar A", A_TRUE), ("Radar B", B_STORED)):
        r = _post(c, "/api/fixtures",
                  {"name": name, "type": "point", "fixtureType": "radar",
                   "radarNode": name.replace(" ", "-").upper(),
                   "rotation": list(stored["rotation"])})
        ok(f"create {name} → 200", r.status_code == 200, r.data[:120])
        fids[name] = r.get_json()["id"]
    fa, fb = fids["Radar A"], fids["Radar B"]
    r = _post(c, "/api/layout", {"force": True, "fixtures": [
        {"id": fa, "x": A_TRUE["x"], "y": A_TRUE["y"], "z": A_TRUE["z"]},
        {"id": fb, "x": B_STORED["x"], "y": B_STORED["y"], "z": B_STORED["z"]},
    ]})
    ok("place both radars via /api/layout", r.status_code == 200, r.data[:120])

    # Idle contract: no hook attached before any walk.
    ok("recording hook is None while idle (zero hot-path overhead)",
       ps._radar_fusion.record_observation is None)

    # Guards on a virgin session.
    r = _post(c, "/api/radar/calibration/stop")
    ok("stop while idle → 400", r.status_code == 400, r.data[:120])
    r = _post(c, "/api/radar/calibration/solve")
    ok("solve with no recording → 400 with human message",
       r.status_code == 400 and b"at least 2 radar fixtures"
       in r.data, r.data[:160])
    r = _post(c, "/api/radar/calibration/apply", {"fixtureIds": [fb]})
    ok("apply without a solve → 400", r.status_code == 400, r.data[:120])

    # Start the walk.
    r = _post(c, "/api/radar/calibration/start")
    ok("start → 200", r.status_code == 200, r.data[:120])
    ok("start attaches the recording hook",
       ps._radar_fusion.record_observation is not None)
    r = _post(c, "/api/radar/calibration/start")
    ok("start while recording → 409", r.status_code == 409, r.data[:120])
    r = _post(c, "/api/radar/calibration/solve")
    ok("solve while recording → 400", r.status_code == 400, r.data[:120])
    r = c.get("/api/radar/calibration/status")
    ok("status reports recording", r.get_json().get("recording") is True)

    # Feed the walk through the REAL fusion ingest (the same call
    # _handle_mmw_targets makes), one frame per radar per tick, sensor
    # frame + σ=50 mm noise, sequenced on a synthetic monotonic base.
    rng = random.Random(913)
    pose_a = {"id": fa, **A_TRUE}
    pose_b = {"id": fb, **B_STORED}       # handler passes the STORED pose
    t0 = 1000.0
    for seq, (t, wx, wy) in enumerate(walk_points()):
        for pose, true_pose, node in ((pose_a, A_TRUE, "MMW-A"),
                                      (pose_b, B_TRUE, "MMW-B")):
            sx, sy = inverse_project(true_pose, wx, wy)
            tgt = (sx + rng.gauss(0, 50), sy + rng.gauss(0, 50), 100, 100)
            ps._radar_fusion.ingest(pose, node, seq, 0x01, [tgt], t0 + t)

    # Stop → per-fixture sample counts.
    r = _post(c, "/api/radar/calibration/stop")
    ok("stop → 200", r.status_code == 200, r.data[:120])
    counts = r.get_json().get("samples") or {}
    n = len(walk_points())
    ok("stop returns full per-fixture sample counts",
       counts.get(str(fa)) == n and counts.get(str(fb)) == n, repr(counts))
    ok("stop detaches the recording hook",
       ps._radar_fusion.record_observation is None)

    # Solve with an explicit bogus reference first (guard), then with A.
    r = _post(c, "/api/radar/calibration/solve", {"referenceFixtureId": 99999})
    ok("solve with unrecorded reference → 400", r.status_code == 400,
       r.data[:160])
    r = _post(c, "/api/radar/calibration/solve", {"referenceFixtureId": fa})
    ok("solve → 200", r.status_code == 200, r.data[:200])
    body = r.get_json()
    ok("solve echoes the reference", body.get("referenceFixtureId") == fa)
    props = [p for p in body.get("proposals", []) if p.get("fixtureId") == fb]
    ok("exactly one proposal, for B (reference excluded)",
       len(props) == 1 and len(body.get("proposals", [])) == 1,
       repr(body.get("proposals")))
    p = props[0]
    prop = p.get("proposed") or {}
    pos_err = math.hypot(prop.get("x", 1e9) - B_TRUE["x"],
                         prop.get("y", 1e9) - B_TRUE["y"])
    yaw_err = abs(prop.get("yawDeg", 1e9) - (-90.0))
    ok("B proposal recovers TRUE position within 100 mm",
       pos_err < 100.0, f"pos err {pos_err:.1f} mm ({prop})")
    ok("B proposal recovers TRUE yaw within 2°",
       yaw_err < 2.0, f"yaw err {yaw_err:.2f}°")
    ok("z is kept, not solved", prop.get("z") == B_STORED["z"], repr(prop))
    tilt, pan, roll = rotation_from_layout(prop.get("rotation"))
    ok("rotation folds yaw only — tilt/roll untouched, pan == yawDeg",
       tilt == 0.0 and roll == 0.0
       and abs(pan - prop.get("yawDeg")) < 1e-9, repr(prop.get("rotation")))
    ok("solve reports the pinned deltas (≈400/−250 → ~471 mm, ~−12°)",
       420.0 < p["deltaPosMm"] < 560.0 and -14.0 < p["deltaYawDeg"] < -10.0,
       f"Δpos {p['deltaPosMm']:.0f} Δyaw {p['deltaYawDeg']:.1f}")
    ok("RMS residual is noise-scale (< 200 mm) with sample count",
       0.0 < p["rmsResidualMm"] < 200.0 and p["samples"] >= 50,
       f"rms {p['rmsResidualMm']:.0f} n {p['samples']}")

    # Apply guards, then apply B.
    r = _post(c, "/api/radar/calibration/apply", {"fixtureIds": []})
    ok("apply with empty id list → 400", r.status_code == 400, r.data[:120])
    r = _post(c, "/api/radar/calibration/apply", {"fixtureIds": [fa]})
    ok("apply for the reference (no proposal) → 400",
       r.status_code == 400, r.data[:160])
    r = _post(c, "/api/radar/calibration/apply", {"fixtureIds": [fb]})
    ok("apply B → 200", r.status_code == 200, r.data[:160])
    ok("apply echoes applied ids", r.get_json().get("applied") == [fb])

    # In-memory + persisted state.
    child = next((x for x in ps._layout["children"] if x["id"] == fb), {})
    ok("layout child moved to the proposed position",
       math.hypot(child.get("x", 1e9) - prop["x"],
                  child.get("y", 1e9) - prop["y"]) < 1.0, repr(child))
    ok("layout child z untouched", child.get("z") == B_STORED["z"], repr(child))
    fix = next((x for x in ps._fixtures if x["id"] == fb), {})
    ok("fixture rotation updated to the proposal",
       fix.get("rotation") == prop["rotation"], repr(fix.get("rotation")))
    cal = (ps._calibrations.get(str(fb)) or {}).get("radarCalibration")
    ok("radarCalibration entry recorded in the calibrations store",
       bool(cal) and cal.get("referenceFixtureId") == fa
       and cal.get("rmsResidualMm") == p["rmsResidualMm"]
       and cal.get("samples") == p["samples"]
       and cal.get("timestamp"), repr(cal))
    data_dir = os.environ["SLYLED_DATA"]
    with open(os.path.join(data_dir, "calibrations.json")) as fh:
        disk_cal = json.load(fh)
    ok("calibrations.json persisted with the radarCalibration entry",
       "radarCalibration" in (disk_cal.get(str(fb)) or {}), repr(disk_cal.get(str(fb))))
    with open(os.path.join(data_dir, "layout.json")) as fh:
        disk_layout = json.load(fh)
    dchild = next((x for x in disk_layout["children"] if x["id"] == fb), {})
    ok("layout.json persisted with the new position",
       abs(dchild.get("x", 1e9) - prop["x"]) < 1.0
       and abs(dchild.get("y", 1e9) - prop["y"]) < 1.0, repr(dchild))
    with open(os.path.join(data_dir, "fixtures.json")) as fh:
        disk_fix = json.load(fh)
    dfix = next((x for x in disk_fix if x["id"] == fb), {})
    ok("fixtures.json persisted with the new rotation",
       dfix.get("rotation") == prop["rotation"], repr(dfix.get("rotation")))

    # Post-apply: re-projecting B's stored pose now matches truth — the
    # walk seen through the applied pose lands on the reference line.
    import radar_fusion
    new_pose = {"id": fb, "x": child["x"], "y": child["y"],
                "z": child.get("z"), "rotation": fix["rotation"]}
    max_err = 0.0
    for _t, wx, wy in walk_points()[::10]:
        sx, sy = inverse_project(B_TRUE, wx, wy)
        px, py = radar_fusion.project_to_stage(new_pose, sx, sy)
        max_err = max(max_err, math.hypot(px - wx, py - wy))
    ok("applied pose reprojects the true walk within 100 mm",
       max_err < 100.0, f"max reprojection err {max_err:.1f} mm")

    # Status keeps the last solve for the SPA card.
    st = c.get("/api/radar/calibration/status").get_json()
    ok("status carries lastSolve after solving",
       (st.get("lastSolve") or {}).get("referenceFixtureId") == fa,
       repr(st.get("lastSolve", {}).get("referenceFixtureId")))
    ok("status not recording after stop", st.get("recording") is False)

    # A fresh start clears the previous session's proposals (stale
    # proposals must not be appliable against a new walk).
    r = _post(c, "/api/radar/calibration/start")
    ok("restart for a new walk → 200", r.status_code == 200)
    r = _post(c, "/api/radar/calibration/apply", {"fixtureIds": [fb]})
    ok("new walk invalidates old proposals (apply → 400)",
       r.status_code == 400, r.data[:120])
    _post(c, "/api/radar/calibration/stop")
    ok("hook detached again after final stop",
       ps._radar_fusion.record_observation is None)


def main():
    run_rigid_solver_unit()
    run_api()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
