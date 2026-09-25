#!/usr/bin/env python3
"""No UTF-8 BOM at the start of tracked source / JSON (#933).

build_release.ps1 used Windows PowerShell 5.1's `Set-Content -Encoding UTF8`,
which writes a BOM: parent_server.py's shebang stopped working and strict JSON
readers rejected the build caches. The script now writes BOM-free; this keeps
it that way. (Firmware version.h files are rewritten BOM-free on their next
deliberate bump — they're outside this check so a firmware track isn't bumped
just to strip three bytes.)

Run: python3 tests/test_no_bom.py
"""

import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT = (".py", ".json", ".kts", ".yml", ".yaml", ".sh", ".ps1", ".js", ".html", ".md", ".iss")


def main():
    files = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True,
                           check=True).stdout.decode().split("\0")
    bad = []
    for f in files:
        if not f.endswith(EXT):
            continue
        p = os.path.join(ROOT, f)
        try:
            with open(p, "rb") as fp:
                if fp.read(3) == b"\xef\xbb\xbf":
                    bad.append(f)
        except OSError:
            continue
    shebang_ok = open(os.path.join(ROOT, "desktop", "shared", "parent_server.py"), "rb").read(2) == b"#!"
    print(f"  [{'PASS' if not bad else 'FAIL'}] no tracked source/JSON file starts with a UTF-8 BOM"
          + (f"  ({bad[:10]})" if bad else ""))
    print(f"  [{'PASS' if shebang_ok else 'FAIL'}] parent_server.py shebang is on byte 0")
    fails = (1 if bad else 0) + (0 if shebang_ok else 1)
    print(f"\n{2 - fails} passed, {fails} failed out of 2 tests")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
