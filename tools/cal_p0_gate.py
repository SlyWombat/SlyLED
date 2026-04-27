#!/usr/bin/env python3
"""#707 P0 verification gate — binary PASS / WARN / FAIL per checklist row.

Companion to `tools/cal_status_summarize.py` (which prints exploratory stats
and a softer VERDICT line). This tool produces a CI-style one-line-per-check
report, intended for the basement-rig live-test sign-off and for pasting
into the #707 verification table.

Reads the NDJSON written by `tools/cal_status_poller.py` plus, optionally,
the DMX trace from `tools/dmx_monitor.py`. The DMX file is required for the
#695 (light-on-during-travel) and #705 (colour-wheel cycle) checks; without
it those rows show WARN.

Checks (each maps to an open or recently-closed mover-cal issue):

    [PASS/FAIL] battleship-init telemetry event present       (#706)
    [PASS/FAIL] fovFilter kept >= 1 probe                     (#702)
    [PASS/FAIL] first probe lands on-stage                    (#702 / #704)
    [PASS/FAIL] on-stage probes exhausted before off-stage    (#702 partition)
    [PASS/FAIL] tilt-first ordering within home pan column    (#696)
    [PASS/FAIL] >= 1 unique probe confirmed                   (#697)
    [PASS/FAIL] zero light-on-during-travel violations        (#695)
    [PASS/FAIL] colour-wheel cycles <= 1 over the full run    (#705)

Usage:
    python3 tools/cal_p0_gate.py \\
        --status docs/live-test-sessions/2026-04-27/cal-status-HHMMSS.ndjson \\
        --dmx    docs/live-test-sessions/2026-04-27/dmx-trace-HHMMSS.ndjson

Exit code is non-zero if any check FAILed (so CI can use it as a gate).
WARN does not fail the gate.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _load_ndjson(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows


def summarize_status(rows):
    facts = {
        "rows": len(rows),
        "init_event": None,
        "first_probe": None,
        "fov_filter": None,
        "probe_order": [],
        "candidates_confirmed_probes": set(),
        "candidates_rejected": Counter(),
        "phases_seen": Counter(),
        "final_status": None,
    }

    for r in rows:
        phase = r.get("phase") or r.get("stage")
        if phase:
            facts["phases_seen"][phase] += 1

        if (phase in ("battleship-init", "battleship_init")
                or r.get("battleshipInit")) and facts["init_event"] is None:
            init = r.get("battleshipInit") or r
            facts["init_event"] = init
            facts["fov_filter"] = init.get("fovFilter")
            fp = init.get("firstProbe") or {}
            facts["first_probe"] = {
                "pan": fp.get("pan"),
                "tilt": fp.get("tilt"),
                "predictedFloor": fp.get("predictedFloor"),
                "mechTiltDeg": fp.get("mechTiltDeg"),
                "onStage": fp.get("onStage"),
            }

        cp = r.get("currentProbe")
        msg = r.get("message") or ""
        if cp and "Grid probe" in msg:
            try:
                probe_n = int(msg.split("Grid probe ")[1].split("/")[0])
            except Exception:
                probe_n = None
            if not any(p.get("n") == probe_n for p in facts["probe_order"]):
                facts["probe_order"].append({
                    "n": probe_n,
                    "pan": cp.get("pan"),
                    "tilt": cp.get("tilt"),
                    "predictedFloor": cp.get("predictedFloor"),
                    "onStage": cp.get("onStage"),
                })

        if "Beam found" in msg:
            try:
                pn = int(msg.split("Beam found at probe ")[1].split("/")[0])
                facts["candidates_confirmed_probes"].add(pn)
            except Exception:
                pass
        for reason in ("REJECTED_DISCONTINUOUS", "REJECTED_OUT_OF_FRAME",
                       "REJECTED_DISPROPORTIONATE", "REJECTED_TOO_FAR",
                       "REJECTED_DEPTH_DISCONTINUITY"):
            if reason in msg:
                facts["candidates_rejected"][reason] += 1

        if r.get("status") in ("done", "completed", "succeeded", "failed",
                               "cancelled", "error"):
            facts["final_status"] = r.get("status")

    return facts


def summarize_dmx(rows):
    out = {
        "events": len(rows),
        "colour_changes": 0,
        "colour_change_pairs": Counter(),
        "light_on_travel_violations": 0,
        "grid_moves_observed": 0,
    }
    prev = None
    prev_colour = None
    for e in rows:
        d = e.get("derived") or {}
        cur_colour = d.get("colour_slot")
        if cur_colour and prev_colour and cur_colour != prev_colour:
            out["colour_changes"] += 1
            out["colour_change_pairs"][f"{prev_colour}->{cur_colour}"] += 1
        if cur_colour:
            prev_colour = cur_colour

        if e.get("kind") != "change":
            prev = e
            continue
        if prev is None:
            prev = e
            continue
        chg = {c.get("name"): (c.get("from"), c.get("to"))
               for c in e.get("changes", [])}
        pd = (prev.get("derived") or {})
        cd = d
        pan_d = abs((cd.get("pan_norm") or 0) - (pd.get("pan_norm") or 0))
        tilt_d = abs((cd.get("tilt_norm") or 0) - (pd.get("tilt_norm") or 0))
        if pan_d > 0.04 or tilt_d > 0.04:
            out["grid_moves_observed"] += 1
            if (pd.get("dimmer", 0) or 0) > 0 and (cd.get("dimmer", 0) or 0) > 0 \
                    and "dimmer" not in chg:
                out["light_on_travel_violations"] += 1
        prev = e
    return out


def fmt_check(label, ok, detail="", fail_count=None):
    """ok: True=PASS, False=FAIL, None=WARN. Returns 1 if FAIL, else 0."""
    tag = "PASS" if ok is True else ("WARN" if ok is None else "FAIL")
    print(f"  [{tag}] {label:<55s} {detail}")
    return 1 if ok is False else 0


def render(facts, dmx):
    print(f"=== cal-status summary ===")
    print(f"  rows: {facts['rows']}    final_status: {facts['final_status']}")
    print(f"  phases: {dict(facts['phases_seen'])}")
    print()
    print(f"=== #707 P0 verification gate ===")

    fails = 0
    init = facts["init_event"]
    fails += fmt_check(
        "battleship-init telemetry present (#706)",
        init is not None,
        "" if init else "  no event in NDJSON -- pre-c898008 build, or cal aborted before init",
    )

    fov = facts["fov_filter"] or {}
    if fov:
        kept = fov.get("kept")
        total = fov.get("total")
        fails += fmt_check(
            "fovFilter kept >= 1 probe (#702)",
            kept is not None and kept >= 1,
            f"kept={kept}/{total} deferred={fov.get('deferred')}",
        )
    else:
        fmt_check("fovFilter kept >= 1 probe (#702)", None,
                  "  no fovFilter in init event")

    fp = facts["first_probe"] or {}
    fp_on = fp.get("onStage")
    fp_floor = fp.get("predictedFloor")
    detail = ""
    if fp_floor:
        x, y = fp_floor[0], fp_floor[1]
        detail = (f"pan={fp.get('pan'):.4f} tilt={fp.get('tilt'):.4f} -> "
                  f"floor=({x:.0f},{y:.0f})")
    if fp_on is None:
        fmt_check("first probe lands on-stage (#702 / #704)", None,
                  "  no firstProbe.onStage in init event")
    else:
        fails += fmt_check("first probe lands on-stage (#702 / #704)",
                           fp_on is True, detail)

    order = facts["probe_order"]
    if order and any(p.get("onStage") is not None for p in order):
        on_stage_count = sum(1 for p in order if p.get("onStage"))
        off_stage_count = sum(1 for p in order if p.get("onStage") is False)
        first_off_idx = next((i for i, p in enumerate(order)
                              if p.get("onStage") is False), len(order))
        last_on_idx = max((i for i, p in enumerate(order)
                           if p.get("onStage") is True), default=-1)
        if on_stage_count == 0:
            fmt_check("on-stage probes exhausted before off-stage (#702)",
                      None, "  no on-stage probes -- pose-fit needed (#699)")
        else:
            ok = first_off_idx > last_on_idx
            fails += fmt_check(
                "on-stage probes exhausted before off-stage (#702)",
                ok,
                f"on={on_stage_count} off={off_stage_count} "
                f"first-off@{first_off_idx} last-on@{last_on_idx}",
            )
    else:
        fmt_check("on-stage probes exhausted before off-stage (#702)", None,
                  "  no per-probe onStage telemetry (pre-#706)")

    if order and len(order) >= 2 and fp:
        seed_pan = fp.get("pan")
        if seed_pan is not None:
            first_col = []
            for p in order:
                if abs((p.get("pan") or 0) - seed_pan) < 1e-6:
                    first_col.append(p)
                else:
                    break
            ok = len(first_col) >= 2
            fails += fmt_check(
                "tilt-first within home pan column (#696)",
                ok,
                f"contiguous home-pan probes at start = {len(first_col)}",
            )
        else:
            fmt_check("tilt-first within home pan column (#696)", None,
                      "  no firstProbe.pan in init event")
    else:
        fmt_check("tilt-first within home pan column (#696)", None,
                  "  insufficient probe events")

    uniq_confirmed = len(facts["candidates_confirmed_probes"])
    fails += fmt_check(
        ">= 1 unique probe confirmed (#697)",
        uniq_confirmed >= 1,
        f"confirmed={uniq_confirmed} "
        f"({sorted(facts['candidates_confirmed_probes']) or '-'}) "
        f"rejected={dict(facts['candidates_rejected']) or 'none'}",
    )

    if dmx is None:
        fmt_check("zero light-on-during-travel violations (#695)", None,
                  "  no DMX trace provided")
        fmt_check("colour-wheel cycles <= 1 over the run (#705)", None,
                  "  no DMX trace provided")
    else:
        fails += fmt_check(
            "zero light-on-during-travel violations (#695)",
            dmx["light_on_travel_violations"] == 0,
            f"{dmx['light_on_travel_violations']} violations across "
            f"{dmx['grid_moves_observed']} grid moves",
        )
        fails += fmt_check(
            "colour-wheel cycles <= 1 over the run (#705)",
            dmx["colour_changes"] <= 1,
            f"{dmx['colour_changes']} transitions: "
            f"{dict(dmx['colour_change_pairs'])}",
        )

    print()
    if fails == 0:
        print("OVERALL: PASS")
    else:
        print(f"OVERALL: FAIL ({fails} check(s) failed)")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True,
                    help="cal-status NDJSON (from tools/cal_status_poller.py)")
    ap.add_argument("--dmx", default=None,
                    help="optional DMX trace NDJSON (from tools/dmx_monitor.py)")
    args = ap.parse_args()

    sp = Path(args.status)
    if not sp.exists():
        print(f"error: cal-status file not found: {sp}", file=sys.stderr)
        return 2
    facts = summarize_status(_load_ndjson(sp))

    dmx = None
    if args.dmx:
        dp = Path(args.dmx)
        if not dp.exists():
            print(f"warning: dmx-trace file not found: {dp} -- skipping DMX checks",
                  file=sys.stderr)
        else:
            dmx = summarize_dmx(_load_ndjson(dp))

    fails = render(facts, dmx)
    return 1 if fails > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
