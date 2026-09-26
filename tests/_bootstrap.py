"""Test isolation bootstrap — SLYLED_DATA + import path (#942, #907).

`desktop/shared/parent_server.py` resolves its persistence directory at
IMPORT time: `SLYLED_DATA` if set, else `%APPDATA%\\SlyLED\\data` on
Windows, else the repo-local `desktop/shared/data`. A test that imports
parent_server without SLYLED_DATA set reads AND rewrites that live
project (the operator's show data on a Windows box).

`tests/conftest.py` covers pytest, but every suite here is also run as a
plain script (`python tests/test_foo.py` — CI, devgui, humans). Each
script-style test that imports parent_server therefore does

    import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation (#942)

before anything else, which makes the unsafe invocation impossible
instead of merely documented. tests/test_bootstrap_guard.py fails the
build if a file imports parent_server without it.

A caller that sets SLYLED_DATA itself still wins (deliberate overrides,
the regression runner, CI job env).
"""

import os
import sys
import tempfile

if "parent_server" in sys.modules and not os.environ.get("SLYLED_DATA"):
    # parent_server already bound its DATA dir to the live project — too
    # late to isolate. Fail loudly rather than let the test write there.
    raise RuntimeError("import _bootstrap before parent_server (#942)")

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-")

SHARED = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
