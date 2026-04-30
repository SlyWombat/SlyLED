#!/usr/bin/env python3
"""Mover-control live trace — polls /api/mover-control/status + /api/fixtures/live, writes NDJSON.

Usage:
    python3 tools/mover_control_poller.py --orch http://localhost:8080 --fid 19 \
        --rate-hz 5 --out docs/live-test-sessions/YYYY-MM-DD/sub/mover-control-TS.ndjson

Emits one record per change in claims OR target fixture's live state.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def fetch(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--orch', default='http://localhost:8080')
    ap.add_argument('--fid', type=int, required=True)
    ap.add_argument('--rate-hz', type=float, default=5.0)
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-wall', type=float, default=3600.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    period = 1.0 / max(args.rate_hz, 0.1)
    fid_key = f'fid{args.fid}_live'
    last_claims = None
    last_live = None
    t0 = time.monotonic()

    with open(out, 'w', encoding='utf-8') as f:
        while True:
            now_mono = time.monotonic() - t0
            if now_mono > args.max_wall:
                break
            wall = datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]
            mc = fetch(f'{args.orch}/api/mover-control/status') or {}
            fl = fetch(f'{args.orch}/api/fixtures/live') or {}
            claims = mc.get('claims', mc) if isinstance(mc, dict) else None
            target = None
            if isinstance(fl, dict):
                # /api/fixtures/live shape: { fixtures: [...] } or { fid: {...} }
                items = fl.get('fixtures') if isinstance(fl.get('fixtures'), list) else None
                if items is None and isinstance(fl, dict):
                    # try direct lookup
                    direct = fl.get(str(args.fid)) or fl.get(args.fid)
                    if isinstance(direct, dict):
                        target = direct
                if items:
                    for it in items:
                        if it.get('id') == args.fid:
                            target = it
                            break
            changed = (claims != last_claims) or (target != last_live)
            if changed or last_claims is None:
                rec = {
                    'wall': wall,
                    't': round(now_mono, 3),
                    'claims': claims,
                    fid_key: target,
                }
                f.write(json.dumps(rec, sort_keys=True) + '\n')
                f.flush()
                last_claims = claims
                last_live = target
            time.sleep(period)


if __name__ == '__main__':
    main()
