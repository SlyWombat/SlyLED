#!/usr/bin/env python3
"""SPA <-> Python pixel renderer parity gate (#938).

Runs desktop/shared/spa/js/pixel_renderer.js under Node over every case in
tests/fixtures/pixel_corpus/corpus.json and asserts the output is BYTE-IDENTICAL
to desktop/shared/pixel_renderer.py.

Why this matters: the SPA renders the 3D preview client-side while the server
renders the same effects for live streaming (#940) and offline .hseq generation
(#941). If the two drift, what the operator previews is not what the lights do.
This repo has been bitten by twin-implementation drift before (fixture_shortcuts
.js / FixtureShortcuts.kt), which is why that pattern is copied here.

The JS is the SPEC. If this test fails, the Python is wrong unless the JS was
deliberately changed — in which case regenerate the goldens
(`python3 tests/test_pixel_renderer.py --regen`) and bump EFFECT_SPEC_VERSION in
BOTH renderers.

Run: `python3 tests/test_pixel_renderer_parity.py`
Requires: node (any LTS) on PATH — skips cleanly without it.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "desktop" / "shared"))

import pixel_renderer as pr  # noqa: E402

CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "pixel_corpus" / "corpus.json"
JS_RENDERER = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "pixel_renderer.js"

# Node harness: load the renderer, walk the corpus, emit every pixel.
NODE_HARNESS = r"""
const m = require(process.argv[2]);
const cases = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const out = [];
for (const c of cases) {
  const row = [];
  for (let i = 0; i < c.n; i++) row.push(m._emuPixel({t: c.t, p: c.p}, i, c.n, c.e));
  out.push(row);
}
process.stdout.write(JSON.stringify({specVersion: m.EFFECT_SPEC_VERSION, rows: out}));
"""


def require_node():
    if shutil.which("node") is None:
        print("[SKIP] node not on PATH — pixel renderer parity gate skipped.")
        sys.exit(0)


def run_js(cases):
    with tempfile.TemporaryDirectory() as td:
        harness = Path(td) / "run_parity.js"
        harness.write_text(NODE_HARNESS, encoding="utf-8")
        cases_file = Path(td) / "cases.json"
        cases_file.write_text(json.dumps(cases), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(harness), str(JS_RENDERER), str(cases_file)],
            capture_output=True, text=True, timeout=300,
        )
    if proc.returncode != 0:
        print("[FAIL] node harness failed:")
        print(proc.stderr[:2000])
        sys.exit(1)
    return json.loads(proc.stdout)


def main():
    require_node()
    cases = json.loads(CORPUS_PATH.read_text())
    print(f"Running {len(cases)} corpus cases through Node and Python...")
    js = run_js(cases)

    failures = 0

    if js["specVersion"] != pr.EFFECT_SPEC_VERSION:
        print(f"  [FAIL] spec version: JS v{js['specVersion']} != Python v{pr.EFFECT_SPEC_VERSION}")
        failures += 1
    else:
        print(f"  [PASS] spec version agrees (v{pr.EFFECT_SPEC_VERSION})")

    total_px = 0
    mismatches = []
    for ci, c in enumerate(cases):
        js_rows = js["rows"][ci]
        for i in range(c["n"]):
            total_px += 1
            got = pr.pixel(c["t"], c["p"], i, c["n"], c["e"])
            want = js_rows[i]
            if got != want:
                mismatches.append((c, i, got, want))

    if mismatches:
        failures += 1
        by_type = {}
        for c, i, got, want in mismatches:
            by_type.setdefault(c["t"], []).append((c, i, got, want))
        print(f"  [FAIL] {len(mismatches)} of {total_px} pixels differ from the JS spec")
        for t in sorted(by_type):
            c, i, got, want = by_type[t][0]
            print(f"         type {t}: {len(by_type[t])} mismatches — "
                  f"n={c['n']} e={c['e']} i={i} python={got} js={want}")
    else:
        print(f"  [PASS] all {total_px} pixels byte-identical to the JS spec")

    # The fast path must also agree with the JS, not just with the scalar oracle.
    fast_bad = 0
    for ci, c in enumerate(cases):
        rendered = pr.render_string(c["t"], c["p"], c["n"], c["e"])
        if rendered is None:
            continue
        want = bytearray()
        for px in js["rows"][ci]:
            want.extend(px)
        if bytes(want) != rendered:
            fast_bad += 1
    if fast_bad:
        failures += 1
        print(f"  [FAIL] render_string differs from JS in {fast_bad} cases")
    else:
        print(f"  [PASS] render_string (numpy fast path) byte-identical to the JS spec")

    print(f"\n{'FAILED' if failures else 'OK'} — {len(cases)} cases, {total_px} pixels")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
