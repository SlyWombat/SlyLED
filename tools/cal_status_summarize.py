#!/usr/bin/env python3
"""#706 — cal-status NDJSON summarizer.

Reads the per-poll snapshots written by `cal_status_poller.py` and prints
a one-screen health check for the cal run. The fields we summarise come
from the #706 telemetry shipped in c898008:

  - `battleship-init` event: cameraPolygons, fovFilter, firstProbe
  - `beam-found` events: predictedFloor, onStage flag
  - `confirm-rejected` events: verdict, info, ratio bounds
  - DMX-trace cross-reference is left to operator scripts; this tool
    only reads cal-status NDJSON and reports what's in it.

Designed for QA box use: hand it the NDJSON the poller wrote and get
"are the gates firing? are probes on-stage? is the band sane?" without
re-reading the live-rig logs.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_records(path):
    """Yield each NDJSON record (one per line). Skip blanks / parse errors."""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarize(path):
    records = list(load_records(path))
    if not records:
        print(f"[empty] {path} — no NDJSON records")
        return 1

    # Tally records.
    phases = Counter()
    statuses = Counter()
    probes_total = 0
    probes_with_floor = 0
    probes_on_stage = 0
    probes_off_stage = 0
    confirm_verdicts = Counter()
    confirm_ratios = []
    init_event = None

    for rec in records:
        if rec.get("phase"):
            phases[rec["phase"]] += 1
        if rec.get("status"):
            statuses[rec["status"]] += 1
        # battleship-init can arrive as a top-level field, or embedded in
        # a progress message.
        if rec.get("stage") == "battleship-init" or rec.get("battleshipInit"):
            init_event = rec.get("battleshipInit") or rec
        # per-probe events
        if rec.get("currentProbe") is not None:
            probes_total += 1
            cp = rec.get("currentProbe") or {}
            pf = cp.get("predictedFloor") if isinstance(cp, dict) else None
            on = cp.get("onStage") if isinstance(cp, dict) else None
            if pf:
                probes_with_floor += 1
            if on is True:
                probes_on_stage += 1
            elif on is False:
                probes_off_stage += 1
        # confirm events embedded in messages
        msg = rec.get("message") or ""
        if "REJECTED_" in msg or "CONFIRMED" in msg:
            for verdict in ("CONFIRMED", "REJECTED_OUT_OF_FRAME",
                             "REJECTED_DISCONTINUOUS",
                             "REJECTED_DISPROPORTIONATE",
                             "REJECTED_DEPTH_DISCONTINUITY",
                             "PARTIAL"):
                if verdict in msg:
                    confirm_verdicts[verdict] += 1
                    break
        info = rec.get("info") or {}
        if isinstance(info, dict) and "observedOverExpected" in info:
            confirm_ratios.append(info["observedOverExpected"])

    # Pretty print.
    print(f"=== cal-status summary: {path} ===")
    print(f"Records:         {len(records)}")
    print(f"Statuses:        {dict(statuses)}")
    print(f"Phases visited:  {sorted(phases)}")
    print()
    if init_event:
        cp = init_event.get("cameraPolygons") or {}
        ff = init_event.get("fovFilter")
        first = init_event.get("firstProbe") or {}
        print("--- battleship-init ---")
        print(f"  camera polygons:   count={cp.get('count')!r:>5}  "
              f"totalVerts={cp.get('totalVerts')!r}")
        if ff:
            kept = ff.get("kept", 0)
            total = ff.get("total", 0)
            pct = (100.0 * kept / total) if total else 0.0
            print(f"  fovFilter:         kept={kept}/{total} ({pct:.1f}%)  "
                  f"deferred={ff.get('deferred')}")
        else:
            print(f"  fovFilter:         (not applied — grid_filter was None)")
        if first:
            pf = first.get("predictedFloor")
            print(f"  first probe:       pan={first.get('pan')}  "
                  f"tilt={first.get('tilt')}  "
                  f"mech_tilt={first.get('mechTiltDeg')}deg")
            print(f"  first floor hit:   {pf}")
        print()
    else:
        print("--- battleship-init: NOT EMITTED ---")
        print("  (likely cause: build pre-c898008, OR cal aborted before init)")
        print()

    print("--- per-probe ---")
    print(f"  total probes:      {probes_total}")
    print(f"  with floor proj:   {probes_with_floor}")
    if probes_with_floor:
        on_pct = 100.0 * probes_on_stage / probes_with_floor
        off_pct = 100.0 * probes_off_stage / probes_with_floor
        print(f"  on-stage:          {probes_on_stage} ({on_pct:.1f}%)")
        print(f"  off-stage:         {probes_off_stage} ({off_pct:.1f}%)")
    else:
        print("  on/off-stage:      (no per-probe predictedFloor in NDJSON)")
    print()

    print("--- confirm-nudge verdicts ---")
    if confirm_verdicts:
        total_conf = sum(confirm_verdicts.values())
        for v, n in sorted(confirm_verdicts.items(),
                             key=lambda kv: -kv[1]):
            pct = 100.0 * n / total_conf
            print(f"  {v:<35} {n:3d} ({pct:.1f}%)")
    else:
        print("  (no confirm verdicts in NDJSON)")
    if confirm_ratios:
        lo = min(confirm_ratios)
        hi = max(confirm_ratios)
        avg = sum(confirm_ratios) / len(confirm_ratios)
        print(f"  observedOverExpected: lo={lo:.2f} hi={hi:.2f} avg={avg:.2f}")
    print()

    # Headline diagnostic: did ANY probe land on-stage AND get confirmed?
    healthy = (probes_on_stage > 0
                and confirm_verdicts.get("CONFIRMED", 0) > 0)
    if healthy:
        print("VERDICT:  cal looks healthy (on-stage probes + at least one "
              "CONFIRMED).")
    elif probes_off_stage > probes_on_stage > 0:
        print("VERDICT:  cal is mostly off-stage. Check the camera-FOV "
              "filter / fixture rotation / Set Home anchor.")
    elif (probes_on_stage > 0
            and confirm_verdicts.get("CONFIRMED", 0) == 0):
        print("VERDICT:  on-stage probes found but ALL rejected by DD gate "
              "(#697). Try loosening confirmContinuityCapMult / "
              "confirmRatioMax in CAL_TUNING_SPEC.")
    elif probes_total == 0:
        print("VERDICT:  no probe events in NDJSON — cal aborted before the "
              "battleship sweep started.")
    else:
        print("VERDICT:  inconclusive — check raw NDJSON.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ndjson", type=Path, help="cal-status NDJSON from cal_status_poller.py")
    args = ap.parse_args()
    if not args.ndjson.exists():
        print(f"file not found: {args.ndjson}", file=sys.stderr)
        sys.exit(2)
    sys.exit(summarize(args.ndjson))


if __name__ == "__main__":
    main()
