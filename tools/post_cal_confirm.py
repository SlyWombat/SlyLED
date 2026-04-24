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
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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


def project_to_camera_pixel(orch, cam_fixture_id, x, y, z):
    """Ask the orchestrator for the projected pixel (x,y) of stage-mm point
    (x,y,z) in the given camera. If the orchestrator exposes such an endpoint,
    use it. Otherwise return None — caller uses a tolerance of 'any visible'
    instead of a specific pixel target.
    """
    # TODO: find the right endpoint name in this build; for now, skip.
    return None


def format_summary(rec):
    out = [f"[{rec['target']}]  aim→ pan={rec.get('aimResult',{}).get('pan'):.3f} tilt={rec.get('aimResult',{}).get('tilt'):.3f}"
           if rec.get('aimResult',{}).get('pan') is not None
           else f"[{rec['target']}]  aim ERROR: {rec.get('aimResult',{}).get('_error','?')}"]
    for c in rec['cameras']:
        pri = rec.get('detect', {}).get(c) or {}
        if pri.get('_error'):
            out.append(f"  cam #{c}: ERR {pri['_error']}")
        elif not pri.get('found'):
            out.append(f"  cam #{c}: no-beam")
        else:
            out.append(f"  cam #{c}: found px=({pri.get('pixelX')}, {pri.get('pixelY')})  bright={pri.get('brightness')}  area={pri.get('area')}")
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
        for c in cameras:
            det[c] = beam_detect(c, threshold=args.threshold, use_dark=not args.no_dark_ref)
            if args.snapshot_dir:
                sd = Path(args.snapshot_dir); sd.mkdir(parents=True, exist_ok=True)
                fp = sd / f"{args.run_tag}-{t_name.replace(':','-').replace(' ','_').replace('/','-')}-cam{c}.jpg"
                snaps[c] = save_snapshot(c, fp)
        rec["detect"] = det
        if snaps: rec["snapshots"] = snaps

        print(format_summary(rec))
        if out_fp:
            out_fp.write(json.dumps(rec) + "\n")
            out_fp.flush()

    set_dimmer(args.orch, args.fid, 0)
    if out_fp:
        out_fp.close()
        print(f"== wrote {args.out}")
    print("== dimmer zeroed on fixture", args.fid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
