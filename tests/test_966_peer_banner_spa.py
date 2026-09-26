"""The "another orchestrator on this network" banner, in the browser (#966).

Real SPA served by an in-process Flask thread on 127.0.0.1. The UDP listener
and announce loop are NOT started (no start_background_tasks), so nothing
touches the LAN; peers are fed straight into the registry. Asserts: no peer
→ no banner; a peer → a red banner on every tab naming it with a link; the
"Acknowledge for this session" button collapses it to a strip but never
hides it; a new peer re-expands it; peers gone → banner gone.

Run: python tests/test_966_peer_banner_spa.py   (needs playwright + chromium)
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import sys
import threading
import time

PORT = 18106
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


def flat(t):
    return " ".join((t or "").split())


def main():
    from playwright.sync_api import sync_playwright
    import parent_server as ps

    reg = ps._peer_registry
    reg._peers.clear()
    threading.Thread(target=lambda: ps.app.run(host="127.0.0.1", port=PORT, threaded=True,
                                               use_reloader=False), daemon=True).start()
    time.sleep(1.5)
    ok("no UDP listener / announce loop in this test (nothing on the LAN)",
       ps._udp_listener_thread is None)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(BASE + "/?tab=setup", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function("typeof _peerPoll === 'function'", timeout=15000)
        page.wait_for_timeout(800)
        ok("no peer → no banner", not page.is_visible("#peer-banner"))

        reg.note_announce("192.168.10.38", {"instanceId": 0xABCD0001, "port": 8080,
                                            "version": "2.2.0", "hostname": "kdocker3"})
        page.evaluate("() => _peerPoll()")
        page.wait_for_function("document.getElementById('peer-banner').style.display === 'block'",
                               timeout=5000)
        t = flat(page.inner_text("#peer-banner"))
        ok("banner names the peer, its IP and version",
           "Another SlyLED orchestrator is running on this network" in t
           and "kdocker3" in t and "192.168.10.38" in t and "v2.2.0" in t, t)
        ok("…links to the other instance",
           page.get_attribute("#peer-banner a[target=_blank]", "href") == "http://192.168.10.38:8080")
        ok("…and says to stop one", "Stop one of them" in t)
        for tab in ("dash", "layout", "settings"):
            page.evaluate(f"() => showTab('{tab}')")
            page.wait_for_timeout(200)
            ok(f"visible on the {tab} tab", page.is_visible("#peer-banner"))

        page.click("#peer-banner button")
        page.wait_for_timeout(200)
        t = flat(page.inner_text("#peer-banner"))
        ok("acknowledged → collapsed strip, still visible",
           page.is_visible("#peer-banner") and "1 other SlyLED orchestrator" in t
           and "Stop one of them" not in t, t)
        page.evaluate("() => _peerPoll()")
        page.wait_for_timeout(400)
        ok("the acknowledgement holds across polls",
           "Stop one of them" not in flat(page.inner_text("#peer-banner")))

        reg.note_ping("192.0.2.44")
        page.evaluate("() => _peerPoll()")
        page.wait_for_timeout(400)
        t = flat(page.inner_text("#peer-banner"))
        ok("a new peer expands it again, listing both",
           "Stop one of them" in t and "192.0.2.44" in t and "kdocker3" in t, t)

        reg._peers.clear()
        page.evaluate("() => _peerPoll()")
        page.wait_for_timeout(400)
        ok("peers gone → banner gone", not page.is_visible("#peer-banner"))
        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
