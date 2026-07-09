#!/usr/bin/env python3
"""
test_893_cors.py — #893 CORS must not reflect arbitrary Origins.

The old after_request hook echoed any Origin header into
Access-Control-Allow-Origin and allowed X-SlyLED-Confirm, which let any
web page defeat the CSRF-confirm header on /api/shutdown and every other
destructive endpoint. CORS headers are now emitted only when the
Origin's host matches the host the request was addressed to (any port).

Usage:
    SLYLED_DATA=$(mktemp -d) python tests/test_893_cors.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-893-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

from parent_server import app

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    with app.test_client() as c:
        # Cross-origin attacker page → no CORS grant at all.
        r = c.get('/status', headers={'Origin': 'http://evil.example'})
        ok('foreign Origin gets no ACAO',
           'Access-Control-Allow-Origin' not in r.headers, dict(r.headers))
        ok('foreign Origin gets no allow-headers grant',
           'Access-Control-Allow-Headers' not in r.headers)
        ok('foreign Origin request itself still served', r.status_code == 200)

        # Same-host Origin (test client host is "localhost") → grant.
        r = c.get('/status', headers={'Origin': 'http://localhost'})
        ok('same-host Origin allowed',
           r.headers.get('Access-Control-Allow-Origin') == 'http://localhost')

        # Same host, different port (dev SPA) → still allowed.
        r = c.get('/status', headers={'Origin': 'http://localhost:3000'})
        ok('same-host other-port Origin allowed',
           r.headers.get('Access-Control-Allow-Origin') == 'http://localhost:3000')
        ok('allowed response varies on Origin',
           'Origin' in (r.headers.get('Vary') or ''))
        ok('confirm header only granted to same host',
           'X-SlyLED-Confirm' in (r.headers.get('Access-Control-Allow-Headers') or ''))

        # Host-suffix trickery must not match.
        r = c.get('/status', headers={'Origin': 'http://localhost.evil.example'})
        ok('host-suffix spoof denied',
           'Access-Control-Allow-Origin' not in r.headers)

        # Garbage Origin → denied, not crashed.
        r = c.get('/status', headers={'Origin': 'not a url'})
        ok('garbage Origin denied without error',
           r.status_code == 200 and 'Access-Control-Allow-Origin' not in r.headers)

        # No Origin header (same-origin fetch, curl, native apps) → no
        # CORS headers needed and none emitted.
        r = c.get('/status')
        ok('no Origin → no CORS headers',
           'Access-Control-Allow-Origin' not in r.headers)

def main():
    run()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
