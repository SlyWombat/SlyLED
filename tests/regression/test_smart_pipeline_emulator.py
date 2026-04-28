#!/usr/bin/env python3
"""test_smart_pipeline_emulator.py — #733 weekly-regression wrapper.

Thin shell around ``tools/emulate_smart_pipeline.py`` so the
regression runner (``tests/regression/run_all.py``) can include the
SMART pipeline emulator in its always-on suite. Exits with the
emulator's exit code so a corpus failure breaks the regression run.

The emulator runs offline against ``tests/fixtures/cal/corpus.json`` —
no orchestrator, no camera node, no rig required. Cheap to run
weekly; cheaper than catching the same regression on the basement
rig.
"""

import os
import subprocess
import sys


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def main():
    emulator = os.path.join(REPO_ROOT, "tools", "emulate_smart_pipeline.py")
    if not os.path.isfile(emulator):
        print(f"FAIL — emulator missing at {emulator}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", emulator],
        cwd=REPO_ROOT,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
