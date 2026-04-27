#!/usr/bin/env python3
"""Offline emulation of `mover_calibrator.battleship_discover` against the
live orchestrator state. Predicts the first N probes the cal will visit
WITHOUT driving the rig, and (optionally) renders a 3D HTML viewer of the
probe rays / floor hits / stage bounds / camera FOV polygons by reusing
`tools/probe_coverage_3d.py`.

Three IK comparisons per probe:
  - cal-as-shipped:    `_ray_floor_hit(... home_pan_norm=seed_pan)`
                       (the post-#710 path the cal uses internally)
  - production filter: `parametric_mover.ParametricFixtureModel.forward()`
                       then ray-floor intersect (mirror of
                       parent_server._build_battleship_grid_filter)
  - operator-validated reference: `tools/probe_coverage_3d.py:floor_hit`

Use this BEFORE live-firing a cal on the next dist build. If the three IKs
diverge, the cal is regressing somewhere new.

Usage:
    python3 -X utf8 tools/emulate_cal_first_probes.py [--fid=17] [--probes=10] [--render]

`--render` runs `tools/probe_coverage_3d.py` against a synthetic cal-status
NDJSON to produce a Three.js HTML viewer. Output path:
docs/live-test-sessions/<today>/emulated-probes-<HHMMSS>.html
"""
import argparse, json, math, os, subprocess, sys, urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/sly/slyled2/desktop/shared")
sys.path.insert(0, "/home/sly/slyled2/tools")
from camera_math import camera_floor_polygon, point_in_polygon
from parametric_mover import ParametricFixtureModel
from mover_calibrator import (_adaptive_coarse_steps, _ray_floor_hit,
                                _camera_visible_tilt_band, _point_in_polygon)
from probe_coverage_3d import floor_hit as good_floor_hit


def http_get(orch, path):
    return json.loads(urllib.request.urlopen(f"{orch}{path}", timeout=5).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, default=17)
    ap.add_argument("--probes", type=int, default=10,
                    help="how many probes to print + render (default 10)")
    ap.add_argument("--render", action="store_true",
                    help="also produce a 3D HTML viewer")
    ap.add_argument("--out", default=None,
                    help="HTML output path (implies --render)")
    args = ap.parse_args()
    if args.out:
        args.render = True

    layout = http_get(args.orch, "/api/layout")
    fixtures = layout.get("fixtures") or []
    cams_api = http_get(args.orch, "/api/cameras") or []
    with open("/home/sly/slyled2/desktop/shared/data/stage.json") as fh:
        stage_m = json.load(fh)
    sb = {"w": int((stage_m.get("w") or 3.0) * 1000),
          "d": int((stage_m.get("d") or 4.0) * 1000),
          "h": int((stage_m.get("h") or 2.0) * 1000)}

    f = next((f for f in fixtures if f["id"] == args.fid), None)
    if not f:
        print(f"error: fixture #{args.fid} not in /api/layout", file=sys.stderr)
        return 2
    fx_pos = (f["x"], f["y"], f["z"])
    fx_rot = f.get("rotation") or [0, 0, 0]
    home_pan = f["homePanDmx16"] / 65535.0
    home_tilt = f["homeTiltDmx16"] / 65535.0
    inv = bool(f.get("mountedInverted"))
    PR, TR = 540.0, 180.0  # adjust if non-150W profile

    # Production grid_filter mirror (parent_server._build_battleship_grid_filter)
    rx = float((fx_rot or [0, 0, 0])[0] or 0)
    ry = float((fx_rot or [0, 0, 0])[1] or 0) if len(fx_rot or []) > 1 else 0.0
    rz = float((fx_rot or [0, 0, 0])[2] or 0) if len(fx_rot or []) > 2 else 0.0
    if rx == 0 and ry == 0 and rz == 0 and inv:
        rx = 180.0
    model = ParametricFixtureModel(
        fixture_pos=fx_pos, pan_range_deg=PR, tilt_range_deg=TR,
        mount_yaw_deg=rz, mount_pitch_deg=rx, mount_roll_deg=ry,
    )

    # camera polygons
    cams, polys = [], []
    for c in cams_api:
        if c.get("fixtureType") != "camera":
            continue
        le = next((x for x in fixtures if x["id"] == c["id"]), None)
        if not le:
            continue
        pos = [le["x"], le["y"], le["z"]]
        rot = le.get("rotation")
        poly = camera_floor_polygon(pos, rot, c.get("fovDeg", 90),
                                     stage_bounds=sb, floor_z=0.0)
        if poly:
            cams.append({"id": c["id"], "pos": pos, "rotation": rot,
                          "fov": c.get("fovDeg", 90)})
            polys.append(poly)

    print(f"=== fixture #{args.fid}: pos={fx_pos} rot={fx_rot} inverted={inv}  "
          f"home_pan_norm={home_pan:.4f} home_tilt_norm={home_tilt:.4f} ===")
    print(f"=== stage bounds: w={sb['w']} d={sb['d']} h={sb['h']} mm ===")
    print(f"=== cameras with floor polygons: {[c['id'] for c in cams]} ===\n")

    # Build the same grid the cal builds
    ps, ts = _adaptive_coarse_steps(PR, TR, 12.0)
    band = _camera_visible_tilt_band(fx_pos, fx_rot, home_pan, PR, TR, inv, polys)
    tlo, thi = band
    tspan = (thi - tlo) / max(1, ts)
    pan_frac = min(360.0, PR) / PR
    half_pan = pan_frac / 2
    pan_lo = max(0.0, min(1.0 - pan_frac, home_pan - half_pan))
    pspan = pan_frac / max(1, ps)

    print(f"Grid {ps}x{ts} (={ps*ts} probes), pan in [{pan_lo:.3f}, "
          f"{pan_lo+pan_frac:.3f}], camera-visible tilt band [{tlo:.3f}, {thi:.3f}]\n")

    grid = [(pan_lo + (i + 0.5) * pspan, tlo + (j + 0.5) * tspan)
            for i in range(ps) for j in range(ts)]

    # Production grid_filter — uses parametric_mover, NOT _ray_floor_hit
    def grid_filter_prod(p, t):
        try:
            dx, dy, dz = model.forward(p, t)
        except Exception:
            return True
        if dz >= -1e-6:
            return False
        scale = -fx_pos[2] / dz
        if scale <= 0:
            return False
        hx = fx_pos[0] + dx * scale
        hy = fx_pos[1] + dy * scale
        return any(point_in_polygon((hx, hy), poly) for poly in polys)

    # Operator-validated reference filter
    def grid_filter_ref(p, t):
        h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv,
                            home_pan_norm=home_pan)
        if not h:
            return False
        return any(_point_in_polygon((h[0], h[1]), poly) for poly in polys)

    key = lambda xy: (abs(xy[0] - home_pan), abs(xy[1] - home_tilt))
    inside, outside = [], []
    for pt in grid:
        (inside if grid_filter_prod(*pt) else outside).append(pt)
    inside.sort(key=key)
    outside.sort(key=key)
    sorted_grid = inside + outside

    inside_r, outside_r = [], []
    for pt in grid:
        (inside_r if grid_filter_ref(*pt) else outside_r).append(pt)
    inside_r.sort(key=key)
    outside_r.sort(key=key)

    print(f"=== partition counts ===")
    print(f"  production (parametric_mover.forward): "
          f"inside={len(inside)}/{len(grid)} outside={len(outside)}")
    print(f"  reference  (probe_coverage_3d):        "
          f"inside={len(inside_r)}/{len(grid)} outside={len(outside_r)}")
    print()

    print(f"=== first {args.probes} probes the cal will visit (production order) ===")
    print(f"{'#':>2} {'pan':>7} {'tilt':>7}  {'cal-IK floor':>22}  "
          f"{'reference IK':>22}  {'parametric':>22}  on-stage  cams")
    print("-" * 130)
    probe_records = []
    for n, (p, t) in enumerate(sorted_grid[:args.probes], 1):
        cal_h = _ray_floor_hit(fx_pos, fx_rot, p, t, PR, TR,
                                mounted_inverted=inv, home_pan_norm=home_pan)
        ref_h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv,
                                home_pan_norm=home_pan)
        try:
            dx, dy, dz = model.forward(p, t)
            par_h = ((fx_pos[0] + dx * (-fx_pos[2] / dz),
                       fx_pos[1] + dy * (-fx_pos[2] / dz))
                     if dz < -1e-6 else None)
        except Exception:
            par_h = None
        fmt = lambda h: f"({h[0]:7.0f}, {h[1]:7.0f})" if h else "no-hit             "
        on_stage = bool(ref_h and 0 <= ref_h[0] <= sb['w']
                          and 0 <= ref_h[1] <= sb['d'])
        in_cam = []
        if ref_h:
            for c, pp in zip(cams, polys):
                if _point_in_polygon((ref_h[0], ref_h[1]), pp):
                    in_cam.append(c["id"])
        print(f"{n:>2} {p:>7.4f} {t:>7.4f}  {fmt(cal_h):>22}  {fmt(ref_h):>22}  "
              f"{fmt(par_h):>22}  {'YES' if on_stage else 'NO ':>8}  "
              f"{in_cam or '-'}")
        probe_records.append({
            "n": n, "pan": p, "tilt": t, "ref_h": ref_h, "cal_h": cal_h,
            "par_h": par_h, "on_stage": on_stage, "in_cam": in_cam,
        })

    # IK divergence sanity-check across all probes (not just first N)
    print(f"\n=== IK divergence check across all {len(grid)} probes ===")
    max_cal_ref_d = 0.0
    max_par_ref_d = 0.0
    for p, t in grid:
        cal_h = _ray_floor_hit(fx_pos, fx_rot, p, t, PR, TR,
                                mounted_inverted=inv, home_pan_norm=home_pan)
        ref_h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv,
                                home_pan_norm=home_pan)
        try:
            dx, dy, dz = model.forward(p, t)
            par_h = ((fx_pos[0] + dx * (-fx_pos[2] / dz),
                       fx_pos[1] + dy * (-fx_pos[2] / dz))
                     if dz < -1e-6 else None)
        except Exception:
            par_h = None
        if cal_h and ref_h:
            d = math.hypot(cal_h[0] - ref_h[0], cal_h[1] - ref_h[1])
            max_cal_ref_d = max(max_cal_ref_d, d)
        if par_h and ref_h:
            d = math.hypot(par_h[0] - ref_h[0], par_h[1] - ref_h[1])
            max_par_ref_d = max(max_par_ref_d, d)
    print(f"  max divergence cal_IK vs reference_IK:        {max_cal_ref_d:7.0f} mm")
    print(f"  max divergence parametric_IK vs reference_IK: {max_par_ref_d:7.0f} mm")
    print(f"  (>10 mm: investigate; >100 mm: cal will lie about probe locations)")

    # ── Optional 3D render ──────────────────────────────────────────────
    if args.render:
        today = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%H%M%S")
        sess_dir = Path(f"/home/sly/slyled2/docs/live-test-sessions/{today}")
        sess_dir.mkdir(parents=True, exist_ok=True)
        ndjson_path = sess_dir / f"emulated-probes-{ts}.ndjson"
        out_path = Path(args.out) if args.out else (sess_dir / f"emulated-probes-{ts}.html")

        # Synthesize a cal-status NDJSON with the first N probes, marking
        # on-stage AND in-cam-FOV ones as "Beam found" candidates so the
        # viewer colour-codes them.
        with open(ndjson_path, "w", encoding="utf-8") as fh:
            now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000000Z")
            fh.write(json.dumps({
                "ts": now_iso, "fid": args.fid,
                "status": "running", "phase": "starting",
                "progress": 0,
            }) + "\n")
            for r in probe_records:
                # Plain probe event
                fh.write(json.dumps({
                    "ts": now_iso, "fid": args.fid,
                    "status": "running", "phase": "battleship",
                    "progress": int(10 + 70 * r["n"] / max(1, len(probe_records))),
                    "message": f"Grid probe {r['n']}/{len(grid)} "
                               f"pan={r['pan']:.2f} tilt={r['tilt']:.2f}",
                    "currentProbe": {
                        "attempt": r["n"], "dimmer": 255,
                        "pan": r["pan"], "tilt": r["tilt"],
                        "rgb": [0, 255, 0],
                        "predictedFloor": list(r["ref_h"]) if r["ref_h"] else None,
                        "onStage": r["on_stage"],
                    },
                    "probeAttempt": r["n"], "probePan": r["pan"],
                    "probeTilt": r["tilt"], "probeDimmer": 255,
                }) + "\n")
                # If in-FOV, also emit a "Beam found" line so the viewer
                # marks it as a candidate (green dot in the 3D viewer).
                if r["on_stage"] and r["in_cam"]:
                    fh.write(json.dumps({
                        "ts": now_iso, "fid": args.fid,
                        "status": "running", "phase": "confirming",
                        "progress": int(10 + 70 * r["n"] / max(1, len(probe_records))),
                        "message": f"Beam found at probe {r['n']}/{len(grid)} "
                                   f"— predicted in-FOV (cams {r['in_cam']})",
                        "currentProbe": {
                            "attempt": r["n"], "dimmer": 255,
                            "pan": r["pan"], "tilt": r["tilt"],
                            "rgb": [0, 255, 0],
                        },
                        "probeAttempt": r["n"], "probePan": r["pan"],
                        "probeTilt": r["tilt"], "probeDimmer": 255,
                    }) + "\n")

        # Invoke the existing 3D viewer renderer
        cmd = [
            sys.executable, "-X", "utf8",
            "/home/sly/slyled2/tools/probe_coverage_3d.py",
            "--orch", args.orch,
            "--fid", str(args.fid),
            "--status", str(ndjson_path),
            "--out", str(out_path),
        ]
        print(f"\n=== rendering 3D viewer ===")
        print(f"  $ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  render failed (exit {result.returncode}):", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return 1
        print(f"  {result.stdout.strip()}")
        print(f"\nOpen in browser: {out_path}")
        print(f"  (synthetic cal-status NDJSON saved at {ndjson_path})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
