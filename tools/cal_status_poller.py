#!/usr/bin/env python3
"""Calibration status poller — polls /api/calibration/mover/<fid>/status at
~5 Hz and writes one NDJSON record per phase / probe / message change.
Pairs with tools/dmx_monitor.py so DMX writes can be correlated with which
cal-pipeline stage produced them.
"""
import argparse, json, signal, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fetch(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


KEYS_OF_INTEREST = (
    "status", "phase", "progress", "message", "warmStart",
    "currentTarget", "currentProbe", "candidatesFound",
    "candidatesConfirmed", "candidatesRejectedAsReflection",
    "candidatesRejectedOutOfFrame", "probesRun",
    "calibrationLocked", "geometrySource", "floorZ",
    "error",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, default=17)
    ap.add_argument("--rate-hz", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = out.open("a", encoding="utf-8")

    period = 1.0 / max(0.5, args.rate_hz)
    url = f"{args.orch}/api/calibration/mover/{args.fid}/status"

    print(f"== cal-status poller fid={args.fid} @ {args.rate_hz} Hz -> {out}", flush=True)

    stop = {"flag": False}
    def on_sig(_s, _f): stop["flag"] = True
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    prev_sig = None
    while not stop["flag"]:
        try:
            r = fetch(url)
        except Exception:
            time.sleep(period); continue
        # Build a comparable signature of the keys we care about.
        sig = {k: r.get(k) for k in KEYS_OF_INTEREST if k in r}
        # Probe counter — emit when attempt changes even if other fields equal.
        cp = r.get("currentProbe") or {}
        sig["probeAttempt"] = cp.get("attempt")
        sig["probePan"] = cp.get("pan")
        sig["probeTilt"] = cp.get("tilt")
        sig["probeDimmer"] = cp.get("dimmer")
        if sig != prev_sig:
            ts = iso_now()
            rec = {"ts": ts, "fid": args.fid, **sig}
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            phase = sig.get("phase") or sig.get("status")
            msg = sig.get("message") or ""
            attempt = sig.get("probeAttempt")
            extra = ""
            if attempt is not None:
                extra = f" probe#{attempt} pan={sig.get('probePan'):.4f} tilt={sig.get('probeTilt'):.4f} dim={sig.get('probeDimmer')}" if sig.get('probePan') is not None else f" probe#{attempt}"
            print(f"[{ts}] {phase!s:12} progress={sig.get('progress')}  {msg[:60]}{extra}", flush=True)
            prev_sig = sig
        time.sleep(period)
    fh.close()
    print("== cal-status poller stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
