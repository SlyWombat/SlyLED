"""Settings → Profiles opens without a ReferenceError and lists profiles (#880).

`loadDmxProfiles` was called by settings.js and seven profiles.js paths but
defined nowhere, so opening Settings → Profiles threw and #profile-lib stayed
empty. Real SPA, real server, nothing on the network.

Run: python tests/test_880_profiles_spa.py   (needs playwright + chromium)
"""

import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-880spa-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

PORT = 18103
BASE = f"http://127.0.0.1:{PORT}"
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


def main():
    from playwright.sync_api import sync_playwright
    import parent_server as ps

    threading.Thread(target=lambda: ps.app.run(host="127.0.0.1", port=PORT, threaded=True,
                                               use_reloader=False), daemon=True).start()
    time.sleep(1.5)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/?tab=settings", wait_until="networkidle", timeout=15000)
        page.evaluate("() => showTab('settings')")
        time.sleep(0.5)
        errors.clear()
        page.evaluate("() => _setSection('profiles')")
        time.sleep(1.5)
        ok("loadDmxProfiles is defined", page.evaluate("() => typeof loadDmxProfiles") == "function")
        ok("opening Settings → Profiles throws nothing",
           not [e for e in errors if "loadDmxProfiles" in e or "ReferenceError" in e], errors)
        lib = page.inner_text("#profile-lib")
        ok("the profile summary renders (built-in count)", "built-in" in lib, lib[:200])
        page.evaluate("() => { window._profileCache = {x: 1}; loadDmxProfiles(); }")
        ok("a reload drops the shared profile cache (layout picks up edits)",
           page.evaluate("() => window._profileCache") is None)
        browser.close()
    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
