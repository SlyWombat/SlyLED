#!/usr/bin/env python3
"""qa_poll_gyro.py — QA-lead live polling for the remote→mover pipeline.

Polls three endpoints every 250 ms; emits one line ONLY when a relevant
slice of state changes. Designed for "operator drives the rig, Claude
watches the wire" sessions. Output is suitable for tail -F and for after-
the-fact grep.

Watched signals:
  /api/mover-control/status — claims[], engine.{running,droppedWrites}
  /api/remotes/live         — remotes[].{connectionState,staleReason,
                                          lastDataAge,calibrated,id,kind,
                                          deviceId}
  /api/gyro/state           — [].{ip,imuOk,streaming,mode,pitch,roll,yaw,
                                   batPct,fps,stale}

Every line is timestamped (HH:MM:SS.mmm). State-change lines are prefixed
with [D]; warnings (heuristic anomaly detection) with [!].

Heuristics:
  - panNorm/tiltNorm jump > 0.05 between samples while streaming
  - claim acquired without prior puck Start (heartbeat-bootstrap path)
  - claim released by orphan-arm timer (no orient within 1.5 s of Start)
  - puck imuOk flips false→true or true→false
  - staleReason set/cleared
  - lastDataAge > 2 s while claim is held

Usage:
    python tools/qa_poll_gyro.py --out tools/qa_gyro_session.log
    tail -F tools/qa_gyro_session.log
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_hms() -> str:
    n = datetime.now()
    return f"{n.strftime('%H:%M:%S')}.{n.microsecond // 1000:03d}"


def fetch(url: str, timeout: float = 1.5):
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, OSError, ValueError) as e:
        return {'_error': str(e)}


def slim_status(s):
    if isinstance(s, dict) and '_error' in s:
        return {'err': s['_error']}
    eng = (s or {}).get('engine') or {}
    claims = (s or {}).get('claims') or []
    return {
        'engRun': eng.get('running'),
        'engType': eng.get('engineType'),
        'drops': eng.get('droppedWrites'),
        'nClaims': len(claims),
        'claims': [{
            'fid': c.get('moverId'),
            'did': c.get('deviceId'),
            'state': c.get('state'),
            'cal': c.get('calibrated'),
            'pan': round(c.get('panNorm', 0), 4) if c.get('panNorm') is not None else None,
            'tilt': round(c.get('tiltNorm', 0), 4) if c.get('tiltNorm') is not None else None,
            'r': c.get('color_r'), 'g': c.get('color_g'), 'b': c.get('color_b'),
            'dim': c.get('dimmer'),
        } for c in claims],
    }


def slim_remotes(s):
    if isinstance(s, dict) and '_error' in s:
        return {'err': s['_error']}
    rs = (s or {}).get('remotes') or []
    out = []
    for r in rs:
        a = r.get('aim')
        aim = (round(a[0], 3), round(a[1], 3), round(a[2], 3)) if a else None
        out.append({
            'id': r.get('id'),
            'kind': r.get('kind'),
            'did': r.get('deviceId'),
            'name': r.get('name'),
            'cal': r.get('calibrated'),
            'conn': r.get('connectionState'),
            'stale': r.get('staleReason'),
            'soft': r.get('softStale'),
            'age': round(r.get('lastDataAge'), 2)
                   if r.get('lastDataAge') is not None else None,
            'aim': aim,
        })
    return out


def slim_gyro(s):
    if isinstance(s, dict) and '_error' in s:
        return {'err': s['_error']}
    if not isinstance(s, list):
        return s
    return [{
        'ip': g.get('ip'),
        'imu': g.get('imuOk'),
        'str': g.get('streaming'),
        'mode': g.get('mode'),
        'rpy': (round(g.get('roll', 0), 1),
                 round(g.get('pitch', 0), 1),
                 round(g.get('yaw', 0), 1)),
        'fps': g.get('fps'),
        'bat': g.get('batPct'),
        'stale': g.get('stale'),
    } for g in s]


def diff_keys(prev, curr):
    """Return a compact diff string for two dicts/lists."""
    if prev == curr:
        return None
    return f"{prev}  →  {curr}"


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


def detect_anomalies(prev_st, curr_st, prev_rm, curr_rm, prev_gy, curr_gy):
    out = []
    # Guard against transient API errors — slimmer returns a dict on error
    # (e.g. {'err': 'timed out'}); skip anomaly detection until the next
    # valid sample.
    if not isinstance(curr_rm, list) or not isinstance(prev_rm, list):
        return out
    if not isinstance(curr_gy, list) or not isinstance(prev_gy, list):
        return out
    if not isinstance(curr_st, dict) or not isinstance(prev_st, dict):
        return out
    if 'err' in curr_st or 'err' in prev_st:
        return out

    # Pan/tilt jump while streaming
    prev_claims = {c['fid']: c for c in (prev_st or {}).get('claims', [])
                   if c.get('state') == 'streaming'}
    curr_claims = {c['fid']: c for c in (curr_st or {}).get('claims', [])
                   if c.get('state') == 'streaming'}
    for fid, cc in curr_claims.items():
        pc = prev_claims.get(fid)
        if not pc:
            continue
        if cc['pan'] is not None and pc['pan'] is not None:
            if abs(cc['pan'] - pc['pan']) > 0.05:
                out.append(f"pan jump on fid={fid}: {pc['pan']} → {cc['pan']}")
        if cc['tilt'] is not None and pc['tilt'] is not None:
            if abs(cc['tilt'] - pc['tilt']) > 0.05:
                out.append(f"tilt jump on fid={fid}: {pc['tilt']} → {cc['tilt']}")

    # imuOk flip on puck
    prev_gy_map = {g['ip']: g for g in (prev_gy or [])}
    for g in curr_gy or []:
        ip = g.get('ip')
        pg = prev_gy_map.get(ip)
        if pg and pg.get('imu') != g.get('imu'):
            out.append(f"puck {ip} imuOk: {pg.get('imu')} → {g.get('imu')}")
        if pg and pg.get('str') != g.get('str'):
            out.append(f"puck {ip} streaming: {pg.get('str')} → {g.get('str')}")

    # staleReason set/cleared
    prev_rm_map = {r['did']: r for r in (prev_rm or [])}
    for r in curr_rm or []:
        did = r.get('did')
        pr = prev_rm_map.get(did)
        if pr and pr.get('stale') != r.get('stale'):
            out.append(
                f"remote {did} ({r.get('kind')}) staleReason: "
                f"{pr.get('stale')!r} → {r.get('stale')!r}"
            )

    # Claim acquired / released
    prev_fids = {c['fid'] for c in (prev_st or {}).get('claims', [])}
    curr_fids = {c['fid'] for c in (curr_st or {}).get('claims', [])}
    for fid in curr_fids - prev_fids:
        cc = next(c for c in curr_st['claims'] if c['fid'] == fid)
        out.append(f"CLAIM ACQUIRED fid={fid} did={cc['did']} state={cc['state']}")
    for fid in prev_fids - curr_fids:
        out.append(f"CLAIM RELEASED fid={fid}")

    # lastDataAge > 2 s while a claim references this remote
    claim_dids = {c['did'] for c in (curr_st or {}).get('claims', [])}
    for r in curr_rm or []:
        if r.get('did') in claim_dids:
            age = r.get('age') or 0
            if age > 2.0:
                out.append(
                    f"stale remote during claim: did={r['did']} age={age:.1f}s"
                )

    return out


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--orch', default='http://localhost:8080',
                   help='Orchestrator base URL')
    p.add_argument('--out', default='tools/qa_gyro_session.log',
                   help='Append session log here')
    p.add_argument('--interval', type=float, default=0.25,
                   help='Poll interval seconds')
    args = p.parse_args()

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(out_path, 'a', encoding='utf-8', buffering=1)

    def emit(line: str):
        msg = f"{now_hms()}  {line}"
        print(msg, flush=True)
        out.write(msg + "\n")

    emit(f"=== qa_poll_gyro.py started — orch={args.orch} interval={args.interval}s ===")

    prev_st = prev_rm = prev_gy = None
    try:
        while True:
            st = fetch(args.orch + '/api/mover-control/status')
            rm = fetch(args.orch + '/api/remotes/live')
            gy = fetch(args.orch + '/api/gyro/state')

            curr_st = slim_status(st)
            curr_rm = slim_remotes(rm)
            curr_gy = slim_gyro(gy)

            # Initial baseline dump
            if prev_st is None:
                emit(f"[base] status={curr_st}")
                emit(f"[base] remotes={curr_rm}")
                emit(f"[base] gyros={curr_gy}")
            else:
                if prev_st != curr_st:
                    emit(f"[D] status: {diff_keys(prev_st, curr_st)}")
                if prev_rm != curr_rm:
                    emit(f"[D] remotes: {diff_keys(prev_rm, curr_rm)}")
                if prev_gy != curr_gy:
                    emit(f"[D] gyros: {diff_keys(prev_gy, curr_gy)}")
                for warn in detect_anomalies(prev_st, curr_st,
                                              prev_rm, curr_rm,
                                              prev_gy, curr_gy):
                    emit(f"[!] {warn}")

            prev_st, prev_rm, prev_gy = curr_st, curr_rm, curr_gy
            time.sleep(args.interval)
    except KeyboardInterrupt:
        emit("=== qa_poll_gyro.py stopped (keyboard interrupt) ===")
    finally:
        out.close()


if __name__ == '__main__':
    main()
