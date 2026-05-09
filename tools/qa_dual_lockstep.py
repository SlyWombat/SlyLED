#!/usr/bin/env python3
"""qa_dual_lockstep.py — capture (puck rpy, aim_stage, claim pan/tilt,
WIRE pan/tilt) at 250 ms cadence in one compact CSV-ish line per sample.

Designed to confirm whether claim → wire propagation is working during a
live Start/Calibrate/move cycle. The earlier qa_poll_gyro session showed
claim pan/tilt frozen across 35 s of varying puck input; this poller adds
the wire so we can tell whether the wire matches the claim (freeze
upstream) or moves independently (freeze downstream).

Usage:
    python tools/qa_dual_lockstep.py --fid 19 --out tools/qa_lockstep.log
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def now_hms() -> str:
    n = datetime.now()
    return f"{n.strftime('%H:%M:%S')}.{n.microsecond // 1000:03d}"


def fetch(url: str, timeout: float = 1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, OSError, ValueError):
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--orch', default='http://localhost:8080')
    p.add_argument('--fid', type=int, default=19)
    p.add_argument('--out', default='tools/qa_lockstep.log')
    p.add_argument('--interval', type=float, default=0.25)
    args = p.parse_args()

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(out_path, 'a', encoding='utf-8', buffering=1)

    def emit(line: str):
        msg = f"{now_hms()}  {line}"
        print(msg, flush=True)
        out.write(msg + "\n")

    emit(f"=== qa_dual_lockstep: fid={args.fid} interval={args.interval}s ===")
    emit("=== columns: rpy=(r,p,y) aim=(x,y,z) claim=(pan,tilt) wire=(pan8,tilt8) ===")

    prev_key = None
    try:
        while True:
            gy = fetch(f"{args.orch}/api/gyro/state")
            rm = fetch(f"{args.orch}/api/remotes/live")
            st = fetch(f"{args.orch}/api/mover-control/status")
            ch = fetch(f"{args.orch}/api/dmx/fixture/{args.fid}/channels")

            # Puck rpy
            rpy = (None, None, None)
            imu = streaming = None
            if isinstance(gy, list):
                for g in gy:
                    if g.get('ip') == '192.168.10.211':
                        rpy = (g.get('roll'), g.get('pitch'), g.get('yaw'))
                        imu = g.get('imuOk')
                        streaming = g.get('streaming')
                        break

            # Remote aim_stage — pick the remote currently driving the
            # active claim (any kind). Falls back to first non-idle remote.
            aim = (None, None, None)
            stale = None
            conn = None
            kind = None
            claim_did = None
            if isinstance(st, dict):
                for c in st.get('claims') or []:
                    if c.get('moverId') == args.fid:
                        claim_did = c.get('deviceId')
                        break
            if isinstance(rm, dict):
                remotes = rm.get('remotes') or []
                target = None
                if claim_did:
                    for r in remotes:
                        if r.get('deviceId') == claim_did:
                            target = r
                            break
                if target is None:
                    for r in remotes:
                        if r.get('connectionState') == 'streaming':
                            target = r
                            break
                if target is None and remotes:
                    target = remotes[0]
                if target is not None:
                    a = target.get('aim') or [None] * 3
                    aim = tuple(a)
                    stale = target.get('staleReason')
                    conn = target.get('connectionState')
                    kind = target.get('kind')

            # Claim pan/tilt
            pan_n = tilt_n = None
            cstate = None
            cal = None
            if isinstance(st, dict):
                for c in st.get('claims') or []:
                    if c.get('moverId') == args.fid:
                        pan_n = c.get('panNorm')
                        tilt_n = c.get('tiltNorm')
                        cstate = c.get('state')
                        cal = c.get('calibrated')
                        break

            # Wire pan/tilt (8-bit fixture)
            wpan = wtilt = None
            if isinstance(ch, dict):
                for c in ch.get('channels') or []:
                    if c.get('type') == 'pan' and wpan is None:
                        wpan = c.get('value')
                    elif c.get('type') == 'tilt' and wtilt is None:
                        wtilt = c.get('value')

            # Build a compact line; only emit when something *meaningful* changes
            def fmt_f(v, n=2):
                return f"{v:.{n}f}" if isinstance(v, (int, float)) else str(v)

            key = (
                # puck rpy bucketed at 1° to suppress sensor noise
                None if rpy[0] is None else round(rpy[0]),
                None if rpy[1] is None else round(rpy[1]),
                None if rpy[2] is None else round(rpy[2]),
                # aim bucketed at 0.02
                None if aim[0] is None else round(aim[0] * 50) / 50.0,
                None if aim[1] is None else round(aim[1] * 50) / 50.0,
                None if aim[2] is None else round(aim[2] * 50) / 50.0,
                # claim
                None if pan_n is None else round(pan_n, 3),
                None if tilt_n is None else round(tilt_n, 3),
                cstate, cal, stale, conn, kind, imu, streaming,
                # wire
                wpan, wtilt,
            )
            if key != prev_key:
                line = (
                    f"rpy=({fmt_f(rpy[0],1)},{fmt_f(rpy[1],1)},{fmt_f(rpy[2],1)}) "
                    f"aim=({fmt_f(aim[0],3)},{fmt_f(aim[1],3)},{fmt_f(aim[2],3)}) "
                    f"claim=(p={fmt_f(pan_n,4)},t={fmt_f(tilt_n,4)},s={cstate},cal={cal}) "
                    f"wire=(p={wpan},t={wtilt}) "
                    f"kind={kind} stale={stale} conn={conn} puck_imu={imu} puck_stream={streaming}"
                )
                emit(line)
                prev_key = key

            time.sleep(args.interval)
    except KeyboardInterrupt:
        emit("=== stopped ===")
    finally:
        out.close()


if __name__ == '__main__':
    main()
