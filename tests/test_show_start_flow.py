"""test_show_start_flow.py — end-to-end Playwright coverage for the Runtime
tab's Start Show button.

Closes the regression-test gap surfaced 2026-05-14: v2.0.5/v2.0.6 shipped
JS fixes for the Bake→Sync→Start chain, but `tests/test_walkthrough_533.py`
only verifies the Start button is *present* (line 815-819), not that
clicking it actually starts a show. That gap let two consecutive regressions
ship to a live operator at-show.

This test drives the real Runtime-tab Start Show button against a running
orchestrator, waiting for `settings.runnerRunning == True` after the click.
Fails fast (≤ 60s) so a recurrence shows up in CI before another release.

Run against the orchestrator on http://localhost:9000 by default; override
with SLYLED_BASE=http://host:port for other ports / hosts.

  python3 tests/test_show_start_flow.py
"""

import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright


BASE = os.environ.get("SLYLED_BASE", "http://localhost:9000")
PASSED = 0
FAILED = 0


def _ok(cond, msg, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {msg}")
    else:
        FAILED += 1
        print(f"  [FAIL] {msg}  ({detail})")


def _api(method, path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        try:
            return r.status, json.loads(r.read().decode())
        except json.JSONDecodeError:
            return r.status, None


def _wait_runner_running(timeout_s=30):
    """Poll settings until runnerRunning==True or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            _, s = _api("GET", "/api/settings")
            if s and s.get("runnerRunning"):
                return True, s
        except Exception:
            pass
        time.sleep(0.5)
    try:
        _, s = _api("GET", "/api/settings")
    except Exception:
        s = None
    return False, s


def test_start_show_button_actually_starts():
    """Driving the user-visible Start Show button must end with
    runnerRunning=True on the server. v2.0.4 / v2.0.5 both broke this in
    different ways (no sync, then sync-halt-on-error)."""
    print("\n-- test_start_show_button_actually_starts --")
    # Make sure something is stoppable in case a previous run left state.
    try:
        _api("POST", "/api/show/stop", body={})
        time.sleep(0.4)
    except Exception:
        pass

    # Sanity: playlist is non-empty (this test expects the operator to
    # have a show; we don't materialise one because the SPA needs real
    # fixtures+children that the test harness can't easily fabricate).
    _, pl = _api("GET", "/api/show/playlist")
    if not pl or not pl.get("order"):
        print(f"  [SKIP] playlist is empty on {BASE} — no show to start")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        # Capture console errors so a JS exception in our flow surfaces here
        # instead of silently making the start chain a no-op.
        console_errors = []
        page.on("console", lambda msg: (
            console_errors.append(msg.text) if msg.type == "error" else None
        ))
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

        # SPA polls /api/fixtures/live + /api/settings continuously, so
        # `networkidle` never resolves. domcontentloaded is enough — the
        # tab DOM is ready once that fires; the showTab() call below makes
        # the runtime panel visible.
        page.goto(BASE)
        page.wait_for_load_state("domcontentloaded")

        # Make sure the Runtime tab is loaded — its DOM is hidden until the
        # tab is selected so the button locator won't see it on cold load.
        page.evaluate("showTab('runtime')")
        page.wait_for_timeout(500)

        btn = page.locator("#rt-start-btn")
        _ok(btn.count() == 1, "Start Show button (#rt-start-btn) present")
        if btn.count() == 0:
            browser.close()
            return

        # If a previous run/test left runnerRunning=True, restart from a
        # clean state — easier than reasoning about the SPA's Start-while-
        # running edge case.
        try:
            _api("POST", "/api/show/stop", body={})
            time.sleep(0.6)
        except Exception:
            pass

        btn.click()
        # Bake+sync+start can take ~5–15s depending on bake size and child
        # responsiveness; 45s wall-clock is generous without making CI slow.
        ok, settings = False, None
        deadline = time.time() + 45
        last_hs = ""
        while time.time() < deadline:
            try:
                hs = page.evaluate("document.getElementById('hs')?.textContent || ''")
                if hs and hs != last_hs:
                    print(f"    hs={hs!r}")
                    last_hs = hs
                _, s = _api("GET", "/api/settings")
                if s and s.get("runnerRunning"):
                    ok, settings = True, s
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ok:
            try:
                _, settings = _api("GET", "/api/settings")
            except Exception:
                pass
        _ok(ok,
            "settings.runnerRunning becomes True within 45s of Start click",
            f"runnerRunning={settings.get('runnerRunning') if settings else '?'}; "
            f"last hs={last_hs!r}; console errors={console_errors[:3]}")

        # Status bar should reflect the started state (regression sentinel:
        # v2.0.4 happily said "Show started" even with no LOAD_STEPs synced).
        try:
            hs = page.evaluate("document.getElementById('hs')?.textContent || ''")
        except Exception:
            hs = ""
        _ok("Show started" in hs or "Sync" in hs or "Bak" in hs,
            "status bar carries Bake/Sync/Started message after click",
            f"hs textContent={hs!r}")

        _ok(not console_errors,
            "no console errors during Start flow",
            f"errors={console_errors[:5]}")

        # Cleanup — leave the runner stopped for the next test/run.
        try:
            _api("POST", "/api/show/stop", body={})
        except Exception:
            pass
        browser.close()


def main():
    print(f"=== Show-Start flow regression  (BASE={BASE}) ===")
    test_start_show_button_actually_starts()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
