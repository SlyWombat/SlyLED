#!/usr/bin/env python3
"""Post-calibration confirmation harness — aim the mover at each known target
(ArUco markers + named stage objects) using the saved calibration and verify
the beam actually lands where expected, on each camera.

Complements `beam_detect_harness.py`:
- `beam_detect_harness.py` — raw pan/tilt DMX + detector probe
  (tests the detector; doesn't need calibration).
- `post_cal_confirm.py`   — stage-mm target via /api/calibration/mover/<fid>/aim
  (tests the full aim-math round-trip; REQUIRES a calibrated fixture).

For each target:
  1. POST /api/calibration/mover/<fid>/aim with {targetX, targetY, targetZ}.
  2. Orchestrator uses the saved grid / parametric model to compute pan/tilt.
  3. Wait --settle then capture dark-ref on each camera.
  4. Turn beam ON, detect on each camera, save a snapshot per camera.
  5. Verdict: detected pixel should fall within --tolerance-px of where the
     target's stage position projects to in each camera's frame.

Usage:
    /usr/bin/python3 tools/post_cal_confirm.py --fid 17 \\
        --cameras 12,13 \\
        --targets aruco:0,aruco:1,aruco:2,aruco:4,aruco:5 \\
        --out docs/live-test-sessions/.../post-cal-confirm.ndjson

Target syntax:
    aruco:<id>        surveyed ArUco marker from /api/aruco/markers
    object:<name>     named stage object (TODO — uses /api/objects)
    xy:<x>,<y>[,<z>]  raw stage-mm coordinates
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Reach into desktop/shared for camera_math (project_stage_to_pixel) without
# requiring an orchestrator endpoint we don't ship yet.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "desktop" / "shared"))
from camera_math import project_stage_to_pixel as _project  # noqa: E402

CAMERA_ROUTES = {
    12: ("http://192.168.10.235:5000", 0),
    13: ("http://192.168.10.235:5000", 1),
    16: ("http://192.168.10.109:5000", 0),
}


def http(method, url, body=None, timeout=8):
    req = urllib.request.Request(url, method=method,
                                  headers={"Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        resp = urllib.request.urlopen(req, data=data, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read().decode("utf-8", errors="replace")[:400]}
    except Exception as e:
        return {"_error": str(e)}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def resolve_target(orch, target_str):
    """Resolve 'aruco:N', 'object:NAME', or 'xy:x,y[,z]' to (name, x, y, z)."""
    if target_str.startswith("aruco:"):
        mid = int(target_str.split(":", 1)[1])
        r = http("GET", f"{orch}/api/aruco/markers")
        for m in r.get("markers", []):
            if int(m.get("id")) == mid:
                label = m.get("label") or f"ArUco{mid}"
                return (f"aruco:{mid} ({label})", m["x"], m["y"], m.get("z", 0))
        raise ValueError(f"aruco marker id={mid} not in registry")
    if target_str.startswith("object:"):
        name = target_str.split(":", 1)[1]
        r = http("GET", f"{orch}/api/objects")
        for obj in (r or []):
            if obj.get("name") == name:
                pos = obj.get("transform", {}).get("pos") or [0, 0, 0]
                return (f"object:{name}", pos[0], pos[1], pos[2])
        raise ValueError(f"stage object '{name}' not found")
    if target_str.startswith("xy:"):
        parts = target_str.split(":", 1)[1].split(",")
        x, y = float(parts[0]), float(parts[1])
        z = float(parts[2]) if len(parts) > 2 else 0.0
        return (f"xy:({x:.0f},{y:.0f},{z:.0f})", x, y, z)
    raise ValueError(f"unrecognised target syntax: {target_str}")


def capture_dark(cam_fid):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    return http("POST", f"{base}/dark-reference", {"cam": cam_idx}, timeout=5)


def beam_detect(cam_fid, threshold=30, use_dark=True):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    return http("POST", f"{base}/beam-detect",
                 {"cam": cam_idx, "threshold": threshold,
                  "useDarkReference": bool(use_dark)}, timeout=6)


def save_snapshot(cam_fid, path):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    try:
        data = urllib.request.urlopen(f"{base}/snapshot?cam={cam_idx}", timeout=8).read()
        Path(path).write_bytes(data)
        return {"ok": True, "bytes": len(data), "path": str(path)}
    except Exception as e:
        return {"_error": str(e)}


def set_dimmer(orch, fid, value):
    # Raw per-channel write — dimmer only (offset 5 on 150W-12ch).
    return http("POST", f"{orch}/api/dmx/fixture/{fid}/test",
                 {"channels": [{"offset": 5, "value": int(value)}]})


def aim_via_cal(orch, fid, x, y, z):
    """Drive fixture to stage-mm target using saved calibration.
    Returns {pan, tilt, targetX, targetY, targetZ} on success, or {_error, ...}.
    """
    return http("POST", f"{orch}/api/calibration/mover/{fid}/aim",
                 {"targetX": x, "targetY": y, "targetZ": z})


_CAM_POSE_CACHE = {}


def _camera_pose(orch, cam_fid):
    """Fetch + cache the camera fixture record (pos, rotation, fov, res).

    Reads /api/fixtures/<fid>; falls back to the matching entry in
    /api/fixtures if individual GET isn't routed for cameras in this build.
    Returns ``None`` on any error so the caller skips projection rather
    than crashing the run.
    """
    if cam_fid in _CAM_POSE_CACHE:
        return _CAM_POSE_CACHE[cam_fid]
    rec = http("GET", f"{orch}/api/fixtures/{cam_fid}")
    if not isinstance(rec, dict) or rec.get("_error"):
        rec = None
        all_fix = http("GET", f"{orch}/api/fixtures")
        if isinstance(all_fix, list):
            for f in all_fix:
                if isinstance(f, dict) and int(f.get("id", -1)) == int(cam_fid):
                    rec = f
                    break
    if not isinstance(rec, dict):
        _CAM_POSE_CACHE[cam_fid] = None
        return None
    pos = rec.get("position") or rec.get("pos") or [0, 0, 0]
    rotation = rec.get("rotation") or [0, 0, 0]
    fov = float(rec.get("fov") or rec.get("fovDeg") or 60)
    res = rec.get("resolution") or [640, 480]
    pose = {
        "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
        "rotation": rotation,
        "fov": fov,
        "resolution": (int(res[0]), int(res[1])),
        "label": rec.get("name") or rec.get("label") or f"cam{cam_fid}",
    }
    _CAM_POSE_CACHE[cam_fid] = pose
    return pose


def project_to_camera_pixel(orch, cam_fixture_id, x, y, z):
    """Project stage-mm point (x, y, z) to camera ``cam_fixture_id``'s pixel.

    Uses the cached camera fixture pose (position, rotation, FOV, resolution)
    plus :func:`camera_math.project_stage_to_pixel` — the same projection
    used by the #682-DD plausibility gate. Returns a dict with the projected
    (px, py) plus the resolution and FOV, or ``None`` for an unknown fixture
    or a behind-camera target.
    """
    pose = _camera_pose(orch, cam_fixture_id)
    if not pose:
        return None
    px, py = _project((x, y, z), pose["pos"], pose["rotation"],
                       pose["fov"], pose["resolution"])
    if px is None or py is None:
        return {"px": None, "py": None, "behindCamera": True,
                 "resolution": pose["resolution"], "fov": pose["fov"]}
    return {"px": float(px), "py": float(py),
             "behindCamera": False,
             "resolution": pose["resolution"],
             "fov": pose["fov"]}


def verdict_for_camera(detected, projected, beam_width_px, fov_tolerance_factor=5.0):
    """Compare detected pixel vs projected pixel for one camera.

    Verdict rule (per #682-FF):
        |detected_px − projected_px| < ``fov_tolerance_factor`` × beam_width_px

    Args:
        detected: ``{"found": bool, "pixelX": int, "pixelY": int}`` from
                  ``/beam-detect``.
        projected: dict from :func:`project_to_camera_pixel` or ``None``.
        beam_width_px: expected beam radius/width in image pixels at this
                       distance (used as the ruler for tolerance).

    Returns ``{"verdict": str, "distancePx": float|None, "tolerancePx": float}``.
        verdict ∈ {NO_PROJECTION, BEHIND_CAMERA, NO_DETECTION,
                    CONFIRMED, OFF_TARGET}.
    """
    tolerance_px = float(fov_tolerance_factor) * float(beam_width_px or 30.0)
    if projected is None:
        return {"verdict": "NO_PROJECTION", "distancePx": None,
                 "tolerancePx": tolerance_px}
    if projected.get("behindCamera") or projected.get("px") is None:
        return {"verdict": "BEHIND_CAMERA", "distancePx": None,
                 "tolerancePx": tolerance_px}
    if not detected or not detected.get("found"):
        return {"verdict": "NO_DETECTION", "distancePx": None,
                 "tolerancePx": tolerance_px}
    dx = float(detected.get("pixelX", 0)) - projected["px"]
    dy = float(detected.get("pixelY", 0)) - projected["py"]
    dist = (dx * dx + dy * dy) ** 0.5
    return {
        "verdict": "CONFIRMED" if dist < tolerance_px else "OFF_TARGET",
        "distancePx": dist,
        "tolerancePx": tolerance_px,
    }


def estimate_beam_width_px(orch, cam_fid, mover_fid, target_xyz, beam_deg):
    """Estimate beam radius in pixels at the floor-hit, for the verdict
    tolerance. Uses ``expected_pixel_shift_per_deg`` against the same
    geometry the DD gate uses.

    Returns 30.0 px as a safe fallback when geometry can't be resolved.
    """
    try:
        from camera_math import expected_pixel_shift_per_deg
    except Exception:
        return 30.0
    pose = _camera_pose(orch, cam_fid)
    if not pose:
        return 30.0
    mover_rec = http("GET", f"{orch}/api/fixtures/{mover_fid}") or {}
    mover_pos = mover_rec.get("position") or mover_rec.get("pos") or [0, 0, 0]
    try:
        px_pan, px_tilt = expected_pixel_shift_per_deg(
            mover_pos=mover_pos,
            floor_hit=target_xyz,
            cam_pos=pose["pos"],
            cam_rotation=pose["rotation"],
            fov_deg=pose["fov"],
            cam_resolution=pose["resolution"],
        )
    except Exception:
        return 30.0
    px_per_deg = max(px_pan, px_tilt) or 1.0
    return float(beam_deg) * px_per_deg


def format_summary(rec):
    out = [f"[{rec['target']}]  aim→ pan={rec.get('aimResult',{}).get('pan'):.3f} tilt={rec.get('aimResult',{}).get('tilt'):.3f}"
           if rec.get('aimResult',{}).get('pan') is not None
           else f"[{rec['target']}]  aim ERROR: {rec.get('aimResult',{}).get('_error','?')}"]
    for c in rec['cameras']:
        pri = rec.get('detect', {}).get(c) or {}
        verdict = rec.get('verdict', {}).get(c) or {}
        v_tag = verdict.get("verdict")
        proj = rec.get("projected", {}).get(c) or {}
        if pri.get('_error'):
            out.append(f"  cam #{c}: ERR {pri['_error']}")
        elif not pri.get('found'):
            out.append(f"  cam #{c}: no-beam ({v_tag or '?'})")
        else:
            dist = verdict.get("distancePx")
            tol = verdict.get("tolerancePx")
            dist_str = (f"  dist={dist:.0f}px / tol={tol:.0f}px"
                        if dist is not None else "")
            proj_str = (f"  proj=({proj.get('px'):.0f},{proj.get('py'):.0f})"
                        if proj.get("px") is not None else "")
            out.append(f"  cam #{c}: found px=({pri.get('pixelX')}, {pri.get('pixelY')})"
                        f"{proj_str}  {v_tag or '?'}{dist_str}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, required=True, help="calibrated DMX mover fixture id")
    ap.add_argument("--cameras", default="12,13,16",
                    help="comma-separated camera fixture ids")
    ap.add_argument("--targets",
                    default="aruco:0,aruco:1,aruco:2,aruco:4,aruco:5",
                    help="comma-separated target spec: aruco:N | object:NAME | xy:X,Y[,Z]")
    ap.add_argument("--threshold", type=int, default=30)
    ap.add_argument("--settle", type=float, default=1.2,
                    help="seconds after aim command before detect")
    ap.add_argument("--dark-wait", type=float, default=1.0)
    ap.add_argument("--no-dark-ref", action="store_true")
    ap.add_argument("--dimmer-on", type=int, default=255)
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--run-tag", default="postcal")
    ap.add_argument("--out")
    ap.add_argument("--beam-deg", type=float, default=3.0,
                    help="beam half-angle / spot diameter in deg, used as the "
                         "ruler for projection-vs-detection tolerance "
                         "(default 3° = 150W MH spot)")
    ap.add_argument("--tolerance-factor", type=float, default=5.0,
                    help="tolerance_px = factor × beam_width_px "
                         "(default 5×)")
    ap.add_argument("--fail-on-off-target", action="store_true",
                    help="exit 1 if any camera reports OFF_TARGET")
    args = ap.parse_args()

    cameras = [int(c) for c in args.cameras.split(",") if c.strip()]

    # Sanity: fixture must be calibrated.
    cal = http("GET", f"{args.orch}/api/calibration/mover/{args.fid}")
    if not cal or cal.get("_error"):
        print(f"ERROR: cannot fetch calibration for fixture {args.fid}: {cal}", file=sys.stderr)
        return 2
    if not (cal.get("samples") or cal.get("grid")):
        print(f"ERROR: fixture {args.fid} has no saved calibration. Run cal first.", file=sys.stderr)
        return 2

    # Resolve every target up front so we fail fast on typos.
    resolved = []
    for t in args.targets.split(","):
        t = t.strip()
        if not t: continue
        try:
            resolved.append((t, *resolve_target(args.orch, t)))
        except Exception as e:
            print(f"ERROR resolving target '{t}': {e}", file=sys.stderr)
            return 2

    print(f"== post-cal confirm: fid={args.fid}  {len(resolved)} target(s) × {len(cameras)} camera(s)")

    off_target_failures = []

    out_fp = None
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_fp = out_path.open("a", encoding="utf-8")

    for t_spec, t_name, tx, ty, tz in resolved:
        rec = {
            "ts": iso_now(),
            "target": t_name,
            "targetSpec": t_spec,
            "targetStage": {"x": tx, "y": ty, "z": tz},
            "cameras": cameras,
        }

        # Turn beam off, aim, wait, capture dark-ref.
        set_dimmer(args.orch, args.fid, 0)
        aim_result = aim_via_cal(args.orch, args.fid, tx, ty, tz)
        rec["aimResult"] = aim_result
        time.sleep(max(args.dark_wait, args.settle))

        if not args.no_dark_ref:
            rec["darkRef"] = {c: capture_dark(c) for c in cameras}

        # Beam on.
        set_dimmer(args.orch, args.fid, args.dimmer_on)
        time.sleep(args.settle)

        # Detect + snapshot.
        det = {}
        snaps = {}
        proj = {}
        verd = {}
        for c in cameras:
            det[c] = beam_detect(c, threshold=args.threshold, use_dark=not args.no_dark_ref)
            proj[c] = project_to_camera_pixel(args.orch, c, tx, ty, tz)
            beam_width_px = estimate_beam_width_px(args.orch, c, args.fid,
                                                     (tx, ty, tz), args.beam_deg)
            verd[c] = verdict_for_camera(det[c], proj[c], beam_width_px,
                                          fov_tolerance_factor=args.tolerance_factor)
            verd[c]["beamWidthPx"] = beam_width_px
            if args.snapshot_dir:
                sd = Path(args.snapshot_dir); sd.mkdir(parents=True, exist_ok=True)
                fp = sd / f"{args.run_tag}-{t_name.replace(':','-').replace(' ','_').replace('/','-')}-cam{c}.jpg"
                snaps[c] = save_snapshot(c, fp)
        rec["detect"] = det
        rec["projected"] = proj
        rec["verdict"] = verd
        if snaps: rec["snapshots"] = snaps

        print(format_summary(rec))
        if out_fp:
            out_fp.write(json.dumps(rec) + "\n")
            out_fp.flush()

        for c, v in verd.items():
            if v.get("verdict") == "OFF_TARGET":
                off_target_failures.append((t_name, c, v.get("distancePx"),
                                              v.get("tolerancePx")))

    set_dimmer(args.orch, args.fid, 0)
    if out_fp:
        out_fp.close()
        print(f"== wrote {args.out}")
    print("== dimmer zeroed on fixture", args.fid)
    if off_target_failures:
        print(f"\n== {len(off_target_failures)} OFF_TARGET verdict(s):")
        for t, c, d, tol in off_target_failures:
            d_s = f"{d:.0f}px" if isinstance(d, (int, float)) else "?"
            tol_s = f"{tol:.0f}px" if isinstance(tol, (int, float)) else "?"
            print(f"   - {t}  cam #{c}  dist={d_s} (tol={tol_s})")
        if args.fail_on_off_target:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
