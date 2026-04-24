#!/usr/bin/env python3
"""Live-test monitor — polls calibration status, writes NDJSON timeline.

Usage:
    /usr/bin/python3 tools/live_test_monitor.py --orch http://192.168.10.5:8080 \
                                                 --fid 3 \
                                                 --out docs/live-test-sessions/2026-04-24/t1-status-timeline.ndjson \
                                                 [--screenshot-url http://192.168.10.5:8080]

Runs until `status == "done"` / `"error"` / `"cancelled"`, or Ctrl-C.

Optionally triggers tools/live_test_screenshot.py at every phase transition,
so SPA screenshots land in docs/live-test-sessions/.../snapshots/ with
filenames keyed on phase name.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def iso_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def fetch(url, timeout=2.0):
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return {'_error': str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--orch', required=True, help='orchestrator base URL')
    ap.add_argument('--fid', required=True, type=int, help='fixture ID')
    ap.add_argument('--out', required=True, help='NDJSON output path')
    ap.add_argument('--interval', type=float, default=0.5, help='poll interval seconds')
    ap.add_argument('--max-wall', type=float, default=600.0, help='hard stop seconds')
    ap.add_argument('--screenshot-url', help='SPA base URL for Playwright snapshots')
    ap.add_argument('--shot-dir', help='screenshot output directory')
    ap.add_argument('--shot-script', default='tools/live_test_screenshot.py',
                    help='path to screenshot helper')
    args = ap.parse_args()

    status_url = f'{args.orch}/api/calibration/mover/{args.fid}/status'
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Pre-flight: can we reach it?
    head = fetch(f'{args.orch}/api/settings')
    if '_error' in head:
        print(f"ERROR: orchestrator unreachable at {args.orch}: {head['_error']}")
        return 2

    print(f"→ polling {status_url} every {args.interval}s, writing {out}")
    t0 = time.monotonic()
    last_phase = None
    last_status = None

    with out.open('a', encoding='utf-8', buffering=1) as fh:
        try:
            while True:
                elapsed = time.monotonic() - t0
                if elapsed > args.max_wall:
                    print(f"⚠ hit --max-wall {args.max_wall}s, stopping")
                    break
                s = fetch(status_url)
                record = {
                    'ts': iso_now(),
                    'elapsed_s': round(elapsed, 2),
                    'status': s,
                }
                fh.write(json.dumps(record) + '\n')

                phase = s.get('phase')
                status = s.get('status')
                if phase != last_phase or status != last_status:
                    print(f"[{elapsed:6.1f}s] status={status!r} phase={phase!r} "
                          f"progress={s.get('progress')}")
                    if args.screenshot_url and args.shot_dir and phase:
                        try:
                            subprocess.Popen(
                                ['/usr/bin/python3', args.shot_script,
                                 '--url', args.screenshot_url,
                                 '--out', f'{args.shot_dir}/t-phase-{phase}.png'],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        except Exception as e:
                            print(f"  (screenshot kick failed: {e})")
                    last_phase, last_status = phase, status
                if status in ('done', 'error', 'cancelled'):
                    print(f"→ terminal status {status!r}, stopping")
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n→ Ctrl-C, stopping")

    print(f"wrote {out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
