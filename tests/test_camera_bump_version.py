#!/usr/bin/env python3
"""firmware/orangepi/bump_version.py bumps BOTH files (#926).

It used to look up registry id "camera-orangepi" (the entry is "camera-node"),
so camera_server.py was bumped and registry.json silently wasn't — the OTA
path kept advertising the old version. Run against temp copies, never the
real tree.

Run: python3 tests/test_camera_bump_version.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def stage(tmp, registry_text=None):
    os.makedirs(os.path.join(tmp, "orangepi"))
    for f in ("bump_version.py", "camera_server.py"):
        shutil.copy(os.path.join(ROOT, "firmware", "orangepi", f), os.path.join(tmp, "orangepi", f))
    reg = registry_text if registry_text is not None else \
        open(os.path.join(ROOT, "firmware", "registry.json"), encoding="utf-8-sig").read()
    with open(os.path.join(tmp, "registry.json"), "w", encoding="utf-8", newline="\n") as fp:
        fp.write(reg)


def ver(path):
    return re.search(r'VERSION\s*=\s*"([\d.]+)"', open(path, encoding="utf-8").read()).group(1)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        stage(tmp)
        before_srv = ver(os.path.join(tmp, "orangepi", "camera_server.py"))
        reg_before = open(os.path.join(tmp, "registry.json"), encoding="utf-8").read()
        r = subprocess.run([sys.executable, os.path.join(tmp, "orangepi", "bump_version.py")],
                           capture_output=True, text=True)
        ok("script exits 0", r.returncode == 0, r.stderr)
        after_srv = ver(os.path.join(tmp, "orangepi", "camera_server.py"))
        a, b, c = before_srv.split(".")
        want = f"{a}.{b}.{int(c) + 1}"
        ok("camera_server.py bumped", after_srv == want, (before_srv, after_srv))
        reg_after_text = open(os.path.join(tmp, "registry.json"), encoding="utf-8").read()
        reg_after = json.loads(reg_after_text)
        cam = next(f for f in reg_after["firmware"] if f["id"] == "camera-node")
        ok("registry 'camera-node' bumped to match", cam["version"] == want, cam["version"])
        others_before = [f for f in json.loads(reg_before)["firmware"] if f["id"] != "camera-node"]
        others_after = [f for f in reg_after["firmware"] if f["id"] != "camera-node"]
        ok("no other registry entry touched", others_before == others_after)
        changed = [l for l in reg_after_text.splitlines() if l not in reg_before.splitlines()]
        ok("registry layout preserved (only the version line changes)",
           len(changed) == 1 and '"version"' in changed[0], changed[:3])
        ok("registry keeps its final newline", reg_after_text.endswith("}\n"))

    with tempfile.TemporaryDirectory() as tmp:
        reg = json.loads(open(os.path.join(ROOT, "firmware", "registry.json"), encoding="utf-8-sig").read())
        reg["firmware"] = [f for f in reg["firmware"] if f["id"] != "camera-node"]
        stage(tmp, json.dumps(reg, indent=4) + "\n")
        before = open(os.path.join(tmp, "orangepi", "camera_server.py"), encoding="utf-8").read()
        r = subprocess.run([sys.executable, os.path.join(tmp, "orangepi", "bump_version.py")],
                           capture_output=True, text=True)
        ok("missing registry entry → non-zero exit", r.returncode != 0, r.returncode)
        ok("…saying which entry", "camera-node" in r.stderr, r.stderr)
        ok("…and camera_server.py is NOT bumped",
           open(os.path.join(tmp, "orangepi", "camera_server.py"), encoding="utf-8").read() == before)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
