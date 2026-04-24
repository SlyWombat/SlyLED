#!/usr/bin/env python3
"""Live-test screenshot helper — Playwright snapshot of the SPA.

Usage:
    /usr/bin/python3 tools/live_test_screenshot.py \
        --url http://192.168.10.5:8080 \
        --out docs/live-test-sessions/2026-04-24/snapshots/t1-phase-mapping.png \
        [--tab layout|calibration|dashboard] \
        [--wait-selector '.calibration-panel'] \
        [--full-page]

Idempotent — overwrites the target PNG. Uses /usr/bin/python3's Playwright.
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True, help='SPA base URL')
    ap.add_argument('--out', required=True, help='output PNG path')
    ap.add_argument('--tab', default=None, help='optional tab to activate before shot')
    ap.add_argument('--wait-selector', default=None, help='wait for this CSS selector')
    ap.add_argument('--full-page', action='store_true', help='full-page screenshot')
    ap.add_argument('--viewport-w', type=int, default=1920)
    ap.add_argument('--viewport-h', type=int, default=1080)
    ap.add_argument('--timeout-ms', type=int, default=15000)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeoutError
    except ImportError:
        print("ERROR: Playwright not installed; run with /usr/bin/python3", file=sys.stderr)
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width': args.viewport_w,
                                                 'height': args.viewport_h})
        page = context.new_page()
        try:
            page.goto(args.url, timeout=args.timeout_ms)
            try:
                page.wait_for_load_state('domcontentloaded', timeout=args.timeout_ms)
            except PwTimeoutError:
                pass
            page.wait_for_timeout(1500)  # settle — SPA has long-poll heartbeat, networkidle won't fire
            if args.tab:
                sel = f'#n-{args.tab}'
                try:
                    page.click(sel, timeout=3000)
                    page.wait_for_timeout(500)  # let tab content animate in
                except PwTimeoutError:
                    print(f"(tab selector {sel} not found — continuing on current tab)",
                          file=sys.stderr)
            if args.wait_selector:
                page.wait_for_selector(args.wait_selector, timeout=args.timeout_ms)
            page.screenshot(path=str(out), full_page=args.full_page)
        finally:
            browser.close()

    print(f"wrote {out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
