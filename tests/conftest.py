"""Session-wide pytest guard — SLYLED_DATA isolation (#907).

`desktop/shared/parent_server.py` resolves its persistence directory at
IMPORT time (parent_server.py "Paths" block): `SLYLED_DATA` env var if
set, else `%APPDATA%\\SlyLED\\data` on Windows, else the repo-local
`desktop/shared/data`. Almost every test in this tree imports
parent_server at module level, and importing it both creates the DATA
dir and (via `app.test_client()` use) writes project JSON into it — so
a pytest run without SLYLED_DATA set would read AND clobber the live
operator project on Windows (the exact failure commit 4add89f was meant
to prevent).

pytest imports this conftest before it collects/imports any test
module, so setting the env var here (at module level, not in a fixture
— script-style tests import parent_server during collection) guarantees
every pytest entry point gets a throwaway data dir.

Guard: only set when the caller hasn't — a deliberate
`SLYLED_DATA=... pytest tests/...` override still wins.

Non-pytest entry points are covered separately:
  - tests/regression/run_all.py exports it into every child process.
  - tests/docker/run_tests.sh / run_dmx_tests.sh set it in-container.
  - tools/devgui/server.py injects it into spawned test subprocesses.
  - GitHub Actions sets it at the job level in python-tests.yml.
Residual risk (direct `python3 tests/test_foo.py` on Windows) is
documented in tests/README.md.
"""
import os
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-pytest-")
