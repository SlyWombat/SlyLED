#!/usr/bin/env python3
"""test_b2_serving.py — B2: waitress serving fallback + optional
destructive-endpoint token gate.

The gate rule under test (see parent_server "Destructive-endpoint token
gate (B2)" section):

  * No token configured (default)      → behaviour identical to before.
  * Token configured (SLYLED_API_TOKEN env var wins over settings
    "apiToken") and request hits a destructive path:
      - X-SlyLED-Token header matches            → allowed
      - Origin host == request host (same-origin
        browser caller, i.e. the SPA)            → allowed
      - anything else (scripted / cross-origin)  → 401
  * Harmless routes are never gated. OPTIONS is never gated.

Run:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_b2_serving.py
"""

import os
import sys
import tempfile

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-b2-")
os.environ.pop("SLYLED_API_TOKEN", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import app  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def run():
    with app.test_client() as c:

        # ── Gate OFF (default: no token configured) ──────────────────────
        parent_server._settings.pop("apiToken", None)

        r = c.post("/api/shutdown")
        ok("no token: shutdown reaches confirm-header check (403, not 401)",
           r.status_code == 403, f"got {r.status_code}")

        r = c.post("/api/children/999/reboot")
        ok("no token: child reboot not gated (non-401)",
           r.status_code != 401, f"got {r.status_code}")

        r = c.get("/api/status")
        ok("no token: harmless GET 200", r.status_code == 200)

        # ── Gate ON via settings apiToken ────────────────────────────────
        parent_server._settings["apiToken"] = "s3cret-tok"
        try:
            r = c.post("/api/shutdown")
            ok("token set: bare shutdown → 401", r.status_code == 401,
               f"got {r.status_code}")
            ok("401 payload says which header to send",
               "X-SlyLED-Token" in (r.get_json() or {}).get("err", ""))

            r = c.post("/api/shutdown", headers={"X-SlyLED-Token": "s3cret-tok"})
            ok("token set: correct token passes gate (403 confirm, not 401)",
               r.status_code == 403, f"got {r.status_code}")

            r = c.post("/api/shutdown", headers={"X-SlyLED-Token": "wrong"})
            ok("token set: wrong token → 401", r.status_code == 401,
               f"got {r.status_code}")

            # Same-origin browser caller (the SPA): Origin host == request
            # host → allowed without token (post-#893 CORS, CSRF-safe).
            r = c.post("/api/shutdown", headers={"Origin": "http://localhost"})
            ok("token set: same-origin Origin passes gate (403 confirm)",
               r.status_code == 403, f"got {r.status_code}")

            r = c.post("/api/shutdown",
                       headers={"Origin": "http://evil.example"})
            ok("token set: cross-origin Origin → 401", r.status_code == 401,
               f"got {r.status_code}")

            # Destructive path coverage: exact + both prefix forms.
            r = c.post("/api/children/999/reboot")
            ok("token set: child reboot gated → 401", r.status_code == 401,
               f"got {r.status_code}")
            r = c.post("/api/children/999/reboot",
                       headers={"X-SlyLED-Token": "s3cret-tok"})
            ok("token set: child reboot with token passes gate (non-401)",
               r.status_code != 401, f"got {r.status_code}")
            r = c.post("/api/firmware/ota/1")
            ok("token set: firmware OTA gated → 401", r.status_code == 401,
               f"got {r.status_code}")
            r = c.post("/api/reset")
            ok("token set: factory reset gated → 401", r.status_code == 401,
               f"got {r.status_code}")
            r = c.post("/api/cameras/deploy")
            ok("token set: camera SSH deploy gated → 401", r.status_code == 401,
               f"got {r.status_code}")

            # Never gated: harmless routes + OPTIONS preflight.
            r = c.get("/api/status")
            ok("token set: harmless GET /api/status still 200",
               r.status_code == 200)
            r = c.get("/api/settings")
            ok("token set: harmless GET /api/settings still 200",
               r.status_code == 200)
            r = c.open("/api/shutdown", method="OPTIONS")
            ok("token set: OPTIONS preflight never 401", r.status_code != 401,
               f"got {r.status_code}")
        finally:
            parent_server._settings.pop("apiToken", None)

        # ── Gate ON via env var; env wins over settings ──────────────────
        os.environ["SLYLED_API_TOKEN"] = "env-tok"
        parent_server._settings["apiToken"] = "settings-tok"
        try:
            r = c.post("/api/shutdown")
            ok("env token: bare shutdown → 401", r.status_code == 401,
               f"got {r.status_code}")
            r = c.post("/api/shutdown", headers={"X-SlyLED-Token": "env-tok"})
            ok("env token: env value passes (403 confirm)",
               r.status_code == 403, f"got {r.status_code}")
            r = c.post("/api/shutdown",
                       headers={"X-SlyLED-Token": "settings-tok"})
            ok("env token: settings value rejected when env set → 401",
               r.status_code == 401, f"got {r.status_code}")
        finally:
            os.environ.pop("SLYLED_API_TOKEN", None)
            parent_server._settings.pop("apiToken", None)

        # ── Gate OFF again: back to today's behaviour ────────────────────
        r = c.post("/api/shutdown")
        ok("token cleared: shutdown back to 403 confirm check",
           r.status_code == 403, f"got {r.status_code}")

    # ── Waitress serving with Flask dev-server fallback ──────────────────
    import importlib.util
    kind, serve_fn = parent_server._resolve_server()
    if importlib.util.find_spec("waitress"):
        ok("waitress installed: _resolve_server picks waitress",
           kind == "waitress" and callable(serve_fn), f"got {kind}")
    else:
        ok("waitress absent: _resolve_server falls back to flask",
           kind == "flask" and serve_fn is None, f"got {kind}")

    # Simulate waitress being uninstallable regardless of the host env:
    # sys.modules[name] = None makes `from waitress import serve` raise
    # ImportError, which must select the Flask dev-server fallback.
    saved = sys.modules.get("waitress", "<absent>")
    sys.modules["waitress"] = None
    try:
        kind, serve_fn = parent_server._resolve_server()
        ok("waitress import blocked: falls back to flask dev server",
           kind == "flask" and serve_fn is None, f"got {kind}")
    finally:
        if saved == "<absent>":
            del sys.modules["waitress"]
        else:
            sys.modules["waitress"] = saved


def main():
    run()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({detail})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
