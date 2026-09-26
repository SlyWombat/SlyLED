#!/usr/bin/env python3
"""Every test that imports parent_server isolates its data dir first (#942).

parent_server binds its persistence dir at import time, so a script-style
test run without SLYLED_DATA rewrites the live project. Each file under
tests/ that imports parent_server must, BEFORE that import, either
`import _bootstrap` or set SLYLED_DATA itself. Static check — nothing is
imported or run.

Run: python3 tests/test_bootstrap_guard.py
"""

import ast
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
SKIP = {"_bootstrap.py", "conftest.py", os.path.basename(__file__)}
_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def first_line(tree, pred):
    lines = [n.lineno for n in ast.walk(tree) if pred(n)]
    return min(lines) if lines else None


def is_ps_import(n):
    return ((isinstance(n, ast.Import) and any(a.name == "parent_server" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "parent_server"))


def is_bootstrap_import(n):
    return isinstance(n, ast.Import) and any(a.name == "_bootstrap" for a in n.names)


def main():
    checked = 0
    for root, dirs, files in os.walk(TESTS):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "user", "fixtures")]
        for fn in sorted(files):
            if not fn.endswith(".py") or fn in SKIP:
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, os.path.dirname(TESTS))
            src = open(path, encoding="utf-8-sig").read()
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                ok(f"{rel} parses", False, e)
                continue
            ps_line = first_line(tree, is_ps_import)
            if ps_line is None:
                continue
            checked += 1
            boot = first_line(tree, is_bootstrap_import)
            env = next((i + 1 for i, l in enumerate(src.splitlines())
                        if "SLYLED_DATA" in l and not l.lstrip().startswith("#")), None)
            guarded = (boot is not None and boot < ps_line) or (env is not None and env < ps_line)
            ok(f"{rel} isolates SLYLED_DATA before importing parent_server (line {ps_line})",
               guarded, "add `import _bootstrap` at the top")
    ok("found the parent_server-importing suites", checked >= 100, checked)
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests ({checked} files checked)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
