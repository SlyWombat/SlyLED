#!/usr/bin/env python3
"""autotune_apply_compare.py — apply each candidate `deltaProposal` to a
real camera, snapshot the result, and run BOTH heuristic + (optionally)
moondream's score against each result. Bypasses moondream's broken scoring
by using the resulting IMAGES as the judgement criterion.

Workflow per candidate delta:
  1. Restore baseline V4L2 controls (Sly slot).
  2. Snapshot baseline → save as `baseline.jpg` (one shared baseline).
  3. Apply delta to camera (only the keys the candidate proposed).
  4. Wait for camera to stabilise.
  5. Snapshot post-delta → save as `<candidate>.jpg`.
  6. Score that snapshot via heuristic evaluator (objective, no moondream).
  7. Restore baseline at end of cell.

Output: NDJSON one record per candidate plus a summary table of objective
heuristic scores. Operator visually compares the JPEGs.

Usage:
    /usr/bin/python3 tools/autotune_apply_compare.py \\
        --cam-fid 16 \\
        --intent aruco \\
        --candidates-file <ndjson with deltaProposals> \\
        --out-dir docs/live-test-sessions/2026-04-25/apply-compare
"""
from __future__ import annotations
import argparse, base64, json, sys, time
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

ORCH = "http://localhost:8080"

CAMERA_ROUTES = {
    12: ("http://192.168.10.235:5000", 0),
    13: ("http://192.168.10.235:5000", 1),
    16: ("http://192.168.10.109:5000", 0),
}

# Default candidates to test if no --candidates-file given. These mirror
# the deltaProposals we observed across moondream prompt variants v1-v6
# during the 2026-04-25 prompt-lab session, plus a heuristic-style large
# exposure bump for an underexposed scene.
DEFAULT_CANDIDATES = {
    "baseline-sly": {},   # empty — applied state stays at Sly slot
    "v1-theatrical": {
        "exposure_time_absolute": 200,
        "gain": 5,
        "white_balance_automatic": 0,
        "white_balance_temperature": 5000,
    },
    "v3-engineer": {
        "exposure_time_absolute": 800,
        "gain": 15,
        "white_balance_automatic": 0,
        "white_balance_temperature": 5500,
    },
    "v5-with-example-78": {
        "exposure_time_absolute": 250,
        "gain": 0,
        "white_balance_automatic": 0,
        "white_balance_temperature": 4800,
    },
    "v5-with-example-42": {
        "exposure_time_absolute": 1500,
        "gain": 25,
        "white_balance_automatic": 0,
        "white_balance_temperature": 5050,
    },
    # A "manual operator guess" — large exposure bump for the underexposed
    # scene. Provides a known-direction comparison.
    "manual-bright": {
        "exposure_time_absolute": 2000,
        "gain": 50,
        "white_balance_automatic": 0,
        "white_balance_temperature": 5000,
    },
    # Even more aggressive — what if we go max exposure?
    "manual-max": {
        "exposure_time_absolute": 4000,
        "gain": 80,
        "white_balance_automatic": 0,
        "white_balance_temperature": 5000,
    },
}


def http(method, url, body=None, timeout=20):
    req = urllib.request.Request(url, method=method,
        headers={"Content-Type":"application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        return json.loads(urllib.request.urlopen(req, data=data, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return {"_error": str(e)}


def save_snapshot(cam_fid, out_path):
    base, idx = CAMERA_ROUTES[cam_fid]
    try:
        data = urllib.request.urlopen(f"{base}/snapshot?cam={idx}", timeout=10).read()
        Path(out_path).write_bytes(data)
        return {"ok": True, "bytes": len(data)}
    except Exception as e:
        return {"_error": str(e)}


def set_controls(cam_fid, controls):
    base, idx = CAMERA_ROUTES[cam_fid]
    return http("POST", f"{base}/camera/controls",
                 {"cam": idx, "controls": controls})


def restore_slot(cam_fid, slot_name):
    return http("POST",
                 f"{ORCH}/api/cameras/{cam_fid}/settings/slots/{slot_name}/activate",
                 {})


def heuristic_score_via_orch(cam_fid, intent):
    """Run a single 1-iter auto-tune in heuristic mode just to get the
    'before' score for the current camera state. The orchestrator will not
    actually move the controls if the heuristic finds no improvement.
    Returns (score, full_response).
    """
    return http("POST",
                 f"{ORCH}/api/cameras/{cam_fid}/settings/auto-tune",
                 {"intent": intent, "evaluator": "heuristic",
                  "maxIterations": 1}, timeout=15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam-fid", type=int, default=16,
                    help="camera fixture id to test against")
    ap.add_argument("--intent", default="aruco",
                    choices=["general","beam","aruco","yolo"])
    ap.add_argument("--baseline-slot", default="Sly",
                    help="slot to restore between candidates")
    ap.add_argument("--candidates-file",
                    help="optional JSON file: {name: {control:value}, ...}")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to wait after applying controls before snapping")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = out_dir / "results.ndjson"

    if args.candidates_file:
        candidates = json.loads(Path(args.candidates_file).read_text())
    else:
        candidates = DEFAULT_CANDIDATES

    print(f"== apply-and-compare: cam #{args.cam_fid} intent={args.intent} "
          f"slot={args.baseline_slot}  {len(candidates)} candidate(s)")
    print(f"   out: {out_dir}\n")

    summary = []

    with ndjson_path.open("a", encoding="utf-8") as fh:
        for name, delta in candidates.items():
            print(f"--- {name} ---  delta={delta}")
            rec = {"ts": datetime.now(timezone.utc).isoformat(),
                   "name": name, "delta": delta,
                   "camFid": args.cam_fid, "intent": args.intent}

            # 1. Restore baseline
            r = restore_slot(args.cam_fid, args.baseline_slot)
            rec["baselineRestore"] = r
            time.sleep(args.settle)

            # 2. Apply candidate delta (only the proposed keys; leave others alone)
            if delta:
                non_null = {k: v for k, v in delta.items() if v is not None}
                rec["applyResult"] = set_controls(args.cam_fid, non_null)
                time.sleep(args.settle)
            else:
                rec["applyResult"] = {"_skipped": "baseline-only"}

            # 3. Snapshot the resulting frame
            snap_path = out_dir / f"{name}.jpg"
            rec["snapshot"] = save_snapshot(args.cam_fid, snap_path)

            # 4. Heuristic score the resulting state
            score_resp = heuristic_score_via_orch(args.cam_fid, args.intent)
            rec["heuristicScore"] = score_resp
            score = (score_resp.get("after") or {}).get("score")
            mean = (score_resp.get("after") or {}).get("mean")
            std  = (score_resp.get("after") or {}).get("std")
            highC = (score_resp.get("after") or {}).get("highlightsClipped")
            shdC  = (score_resp.get("after") or {}).get("shadowsClipped")
            rec["heuristicSummary"] = {"score": score, "mean": mean, "std": std,
                                        "highlightsClipped": highC,
                                        "shadowsClipped": shdC}
            print(f"  → score={score}  mean={mean}  std={std}  "
                  f"highC={highC}  shadC={shdC}")

            summary.append({"name": name, "score": score, "mean": mean,
                            "std": std, "highC": highC, "shadC": shdC,
                            "delta": delta, "snap": str(snap_path)})

            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    # Restore baseline one more time so we leave the camera in Sly state.
    restore_slot(args.cam_fid, args.baseline_slot)

    # Final ranked summary by heuristic score (descending) — visual review still needed.
    print(f"\n=== ranked summary ({args.intent} on cam {args.cam_fid}) ===")
    print(f"{'name':25s} {'score':>6s} {'mean':>6s} {'std':>6s} {'highC':>6s} {'shdC':>6s}")
    for r in sorted(summary, key=lambda x: x["score"] or -1, reverse=True):
        print(f"{r['name']:25s} {str(r['score']):>6s} {str(r['mean']):>6s} "
              f"{str(r['std']):>6s} {str(r['highC']):>6s} {str(r['shdC']):>6s}")
    print(f"\nNDJSON: {ndjson_path}")
    print(f"Snapshots: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
