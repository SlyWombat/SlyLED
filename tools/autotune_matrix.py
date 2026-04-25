#!/usr/bin/env python3
"""autotune_matrix.py — exercise camera auto-tune across a matrix of
(camera, intent, evaluator) cells, capturing before/after snapshots and
the full per-iteration history for each cell.

Usage:
    /usr/bin/python3 tools/autotune_matrix.py \\
        --cameras 12,16 \\
        --intents general,beam,aruco,yolo \\
        --evaluators heuristic,ai \\
        --max-iter 6 \\
        --out-dir docs/live-test-sessions/2026-04-25/autotune-matrix

Per cell:
  - Saves a `<run-tag>-before.jpg` snapshot
  - Calls POST /api/cameras/<fid>/settings/auto-tune
  - Saves a `<run-tag>-after.jpg` snapshot
  - Appends one record to <out-dir>/results.ndjson with the full response

The harness ALWAYS restores the camera's pre-run V4L2 controls at the end of
each cell (via the `Sly` slot if present, else by replaying the captured
controls), so adjacent cells don't pollute each other.

Per-cell timeout defaults to 240 s (heuristic ~30 s, AI mode 90+ s expected
on 4K frames per #685). The AI cells will probably fail until the resize-
before-base64 fix from #685 lands; the harness records the failure cleanly.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ORCH = "http://localhost:8080"

# Camera-fid → (node IP, cam_idx) for direct snapshot pulls when we want
# the underlying-resolution image instead of any orchestrator proxy.
CAMERA_ROUTES = {
    12: ("http://192.168.10.235:5000", 0),   # Tracking host, sensor 0 — Stage Right
    13: ("http://192.168.10.235:5000", 1),   # Tracking host, sensor 1 — Stage Left
    16: ("http://192.168.10.109:5000", 0),   # RPi host — Out Left
}

CAMERA_HOST_LABEL = {
    12: "tracking", 13: "tracking", 16: "rpi",
}


def http(method, url, body=None, timeout=30):
    req = urllib.request.Request(url, method=method,
                                  headers={"Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        resp = urllib.request.urlopen(req, data=data, timeout=timeout)
        raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw[:400].decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}",
                "_body": e.read().decode("utf-8", errors="replace")[:400]}
    except urllib.error.URLError as e:
        return {"_error": f"URL: {e.reason}"}
    except Exception as e:
        return {"_error": str(e)}


def save_snapshot(cam_fid, out_path):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    try:
        data = urllib.request.urlopen(
            f"{base}/snapshot?cam={cam_idx}", timeout=10).read()
        Path(out_path).write_bytes(data)
        return {"ok": True, "path": str(out_path), "bytes": len(data)}
    except Exception as e:
        return {"_error": str(e)}


def get_cam_controls(cam_fid):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    return http("GET", f"{base}/camera/controls?cam={cam_idx}", timeout=6)


def set_cam_controls(cam_fid, controls):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    return http("POST", f"{base}/camera/controls",
                 {"cam": cam_idx, "controls": controls}, timeout=8)


def restore_via_slot(cam_fid, slot_name):
    """Activate a saved slot through the orchestrator. Returns the response."""
    return http("POST",
                 f"{ORCH}/api/cameras/{cam_fid}/settings/slots/{slot_name}/activate",
                 {}, timeout=10)


def run_autotune(cam_fid, intent, evaluator, max_iter, save_slot, timeout):
    body = {"intent": intent, "evaluator": evaluator,
            "maxIterations": max_iter}
    if save_slot:
        body["saveSlot"] = save_slot
    return http("POST",
                 f"{ORCH}/api/cameras/{cam_fid}/settings/auto-tune",
                 body, timeout=timeout)


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def slugify(s):
    return "".join(c if c.isalnum() else "-" for c in s).strip("-")


def run_cell(out_dir, cam_fid, intent, evaluator, max_iter, *,
             baseline_slot="Sly", per_cell_timeout=240,
             ndjson_handle=None):
    host = CAMERA_HOST_LABEL.get(cam_fid, "unknown")
    tag = f"cam{cam_fid}-{host}-{slugify(intent)}-{slugify(evaluator)}"
    cell_dir = out_dir / tag
    cell_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": iso_now(),
        "camFid": cam_fid,
        "host": host,
        "intent": intent,
        "evaluator": evaluator,
        "maxIterations": max_iter,
        "tag": tag,
        "perCellTimeout_s": per_cell_timeout,
    }

    # 0. Restore baseline (Sly) before each cell so they don't pollute each other.
    print(f"\n=== {tag} ===")
    print(f"  baseline restore via slot {baseline_slot!r}…")
    record["baselineRestore"] = restore_via_slot(cam_fid, baseline_slot)

    # 1. Capture before snapshot + record current V4L2 state.
    print("  capturing 'before' snapshot…")
    record["beforeSnapshot"] = save_snapshot(cam_fid, cell_dir / "before.jpg")
    pre_ctrls = get_cam_controls(cam_fid)
    record["preControls"] = {c["name"]: c.get("value")
                              for c in pre_ctrls.get("controls", [])
                              if c.get("value") is not None}

    # 2. Run auto-tune.
    print(f"  running auto-tune (intent={intent} evaluator={evaluator} maxIter={max_iter})…")
    t0 = time.monotonic()
    result = run_autotune(cam_fid, intent, evaluator, max_iter,
                           save_slot=None, timeout=per_cell_timeout)
    elapsed = time.monotonic() - t0
    record["elapsed_s"] = round(elapsed, 2)
    record["autotuneResult"] = result

    if "_error" in result:
        print(f"  → FAILED in {elapsed:.1f}s: {result.get('_error')}")
        if "_body" in result: print(f"    body: {result['_body']!r}")
    else:
        before = result.get("before") or {}
        after = result.get("after") or {}
        bs = before.get("score")
        as_ = after.get("score")
        nIter = len(result.get("history") or [])
        applied = result.get("applied") or {}
        print(f"  → done in {elapsed:.1f}s  ({nIter} iter)  "
              f"score {bs} → {as_}  applied={applied}")

    # 3. Capture after snapshot.
    print("  capturing 'after' snapshot…")
    record["afterSnapshot"] = save_snapshot(cam_fid, cell_dir / "after.jpg")

    # 4. Always restore baseline so the next cell starts clean.
    record["finalRestore"] = restore_via_slot(cam_fid, baseline_slot)

    if ndjson_handle:
        ndjson_handle.write(json.dumps(record) + "\n")
        ndjson_handle.flush()
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cameras", default="12,16",
                    help="comma-separated cam fixture IDs to test")
    ap.add_argument("--intents", default="general,beam,aruco,yolo")
    ap.add_argument("--evaluators", default="heuristic,ai")
    ap.add_argument("--max-iter", type=int, default=6)
    ap.add_argument("--out-dir", required=True,
                    help="directory for snapshots + results.ndjson")
    ap.add_argument("--per-cell-timeout", type=float, default=240.0)
    ap.add_argument("--baseline-slot", default="Sly")
    ap.add_argument("--ai-cell-timeout", type=float, default=300.0,
                    help="larger timeout used specifically for AI cells")
    args = ap.parse_args()

    cams = [int(c) for c in args.cameras.split(",") if c.strip()]
    intents = [i.strip() for i in args.intents.split(",") if i.strip()]
    evaluators = [e.strip() for e in args.evaluators.split(",") if e.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = out_dir / "results.ndjson"

    n_cells = len(cams) * len(intents) * len(evaluators)
    print(f"== autotune matrix: {len(cams)} cam(s) × {len(intents)} intent(s) "
          f"× {len(evaluators)} eval(s) = {n_cells} cells")
    print(f"   out: {out_dir}")

    with ndjson_path.open("a", encoding="utf-8") as fh:
        for cam in cams:
            for intent in intents:
                for evaluator in evaluators:
                    timeout = (args.ai_cell_timeout if evaluator == "ai"
                                else args.per_cell_timeout)
                    try:
                        run_cell(out_dir, cam, intent, evaluator,
                                 args.max_iter,
                                 baseline_slot=args.baseline_slot,
                                 per_cell_timeout=timeout,
                                 ndjson_handle=fh)
                    except KeyboardInterrupt:
                        print("\n→ interrupted, partial results saved")
                        return 1
                    except Exception as e:
                        print(f"  → CELL EXCEPTION: {e}")

    print(f"\n== matrix complete; {ndjson_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
