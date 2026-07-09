#!/usr/bin/env python3
"""
test_persistence_atomic.py — #889 persistence safety.

Proves:
  1. _load() quarantines a corrupt JSON file to <name>.json.corrupt
     (numbered when one already exists) instead of silently returning
     the default and leaving the wreckage in place to be re-saved over.
  2. _save() is atomic: it writes a temp file then os.replace()s it, so
     a failure mid-save leaves the previous content intact — never a
     truncated file.

Usage:
    python tests/test_persistence_atomic.py
"""

import json
import os
import sys
import tempfile

# Must set SLYLED_DATA before importing parent_server so the module's
# DATA dir (and its import-time _load calls) never touch a live project.
_DATA_DIR = tempfile.mkdtemp(prefix="slyled-test-889-")
os.environ["SLYLED_DATA"] = _DATA_DIR

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import _load, _save, DATA

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))

def run():
    # ── Corrupt file → quarantined, default returned ────────────────
    (DATA / "t889.json").write_text('{"truncated": [1, 2,')   # invalid JSON
    got = _load("t889", {"fallback": True})
    ok("corrupt _load returns default", got == {"fallback": True}, repr(got))
    ok("corrupt file removed from live path", not (DATA / "t889.json").exists())
    q = DATA / "t889.json.corrupt"
    ok("corrupt file quarantined", q.exists())
    ok("quarantine preserves original bytes",
       q.exists() and q.read_text() == '{"truncated": [1, 2,')

    # ── Second corruption → numbered quarantine, first untouched ────
    (DATA / "t889.json").write_text('not json either')
    got = _load("t889", [])
    ok("second corrupt _load returns default", got == [])
    q1 = DATA / "t889.json.corrupt.1"
    ok("second quarantine gets numeric suffix", q1.exists())
    ok("first quarantine untouched",
       q.exists() and q.read_text() == '{"truncated": [1, 2,')
    ok("second quarantine preserves bytes",
       q1.exists() and q1.read_text() == 'not json either')

    # ── Missing file still returns default (no quarantine noise) ────
    got = _load("t889-missing", {"d": 1})
    ok("missing file returns default", got == {"d": 1})
    ok("missing file leaves no quarantine",
       not (DATA / "t889-missing.json.corrupt").exists())

    # ── _save round-trips through _load ─────────────────────────────
    _save("t889rt", {"a": [1, 2, 3], "b": "x"})
    ok("_save/_load round-trip", _load("t889rt", None) == {"a": [1, 2, 3], "b": "x"})
    ok("_save leaves no .tmp behind", not (DATA / "t889rt.json.tmp").exists())

    # ── _save atomicity: failure at replace-time keeps OLD content ──
    _save("t889atomic", {"generation": 1})
    real_replace = os.replace
    def _boom(src, dst):
        raise OSError("simulated crash between write and replace")
    parent_server.os.replace = _boom
    try:
        raised = False
        try:
            _save("t889atomic", {"generation": 2})
        except OSError:
            raised = True
        ok("_save surfaces replace failure", raised)
        on_disk = json.loads((DATA / "t889atomic.json").read_text())
        ok("old content intact after failed save (never truncated)",
           on_disk == {"generation": 1}, repr(on_disk))
    finally:
        parent_server.os.replace = real_replace

    # New content lands only via the (now restored) atomic replace.
    _save("t889atomic", {"generation": 2})
    ok("new content lands after successful save",
       json.loads((DATA / "t889atomic.json").read_text()) == {"generation": 2})

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
