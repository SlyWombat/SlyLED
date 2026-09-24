#!/usr/bin/env python3
"""Setup → Discover finds a HinksPix, as the browser runs it (#949).

Real SPA, real server, real sweep: net_ifaces.sweep_hosts is pointed at a fake
controller on 127.0.0.1 that answers BoardInfo only after 1.5 s. The UDP
discover endpoints are routed to canned replies by Playwright (no PINGs onto
the LAN), as are the two Add POSTs (the fake's host:port is not an address
/api/children accepts).

Asserts: the UDP results render while the sweep is still running (the sweep
never blocks them), the sweep then lists the controller with a HinksPix badge,
its firmware and boards, and Add posts that IP and reports a HinksPix.

Run: python tests/test_hinkspix_discover_spa.py   (needs playwright + chromium)
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-949spa-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

PORT = 18097
BASE = f"http://127.0.0.1:{PORT}"

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if not isinstance(name, str) or isinstance(cond, str):
        raise TypeError(f"ok() called with swapped arguments: {name!r}, {cond!r}")
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


BD_INFO = {"CMD": "BD_INFO", "Controller": "H", "Type": "P", "MCPU": "MS_160",
           "BD1": "L", "BD2": "S", "BD3": "N", "MaxU": "402", "NumU": "12"}


def fake_controller(delay):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(delay)
            body = json.dumps(BD_INFO).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"127.0.0.1:{srv.server_address[1]}"


def main():
    from playwright.sync_api import sync_playwright

    import hinkspix_bridge
    import net_ifaces
    import parent_server

    ctrl = fake_controller(delay=1.5)
    net_ifaces.sweep_hosts = lambda: ([ctrl], [])
    # The fake answers after 1.5 s; give the sweep room for that.
    hinkspix_bridge.DISCOVER_TIMEOUT = 3.0
    threading.Thread(target=lambda: parent_server.app.run(
        host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
        daemon=True).start()
    time.sleep(1.5)

    posted = []

    def udp_discover(route):
        body = {"pending": True} if route.request.url.endswith("/discover") else []
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def add_child(route):
        if route.request.method != "POST":
            return route.continue_()
        posted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "id": 77, "type": "hinkspix",
                                       "name": "HinksPix PRO", "ip": "127.0.0.1"}))

    def add_fixture(route):
        if route.request.method != "POST":
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", body='{"ok": true, "id": 5}')

    shot = os.path.join(tempfile.gettempdir(), "slyled-949-discover.png")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/api/children/discover**", udp_discover)
        page.route("**/api/children", add_child)
        page.route("**/api/fixtures", add_fixture)
        page.goto(BASE + "/?tab=setup", wait_until="networkidle", timeout=15000)
        page.wait_for_selector("#disc-btn", timeout=10000)
        ok("Discover button present", page.query_selector("#disc-btn") is not None)
        ok("pixel-controller results block present",
           page.query_selector("#disc-hinks-results") is not None)

        page.click("#disc-btn")
        page.wait_for_function(
            "() => (document.getElementById('disc-results')||{}).innerText"
            " && document.getElementById('disc-results').innerText.indexOf('No new devices') >= 0",
            timeout=10000)
        hinks_text = page.inner_text("#disc-hinks-results")
        ok("UDP results render while the sweep is still running",
           "Scanning for pixel controllers" in hinks_text, hinks_text)

        page.wait_for_selector("#disc-hinks-results button", timeout=15000)
        text = " ".join(page.inner_text("#disc-hinks-results").split())
        ok("controller listed with its IP", ctrl in text, text)
        ok("HinksPix badge", "HinksPix" in text, text)
        ok("model + firmware shown", "HinksPix PRO" in text and "MS_160" in text, text)
        ok("boards shown", "Long_Range" in text and "Local_SPI" in text, text)
        page.screenshot(path=shot, full_page=False)

        page.click("#disc-hinks-results button")
        page.wait_for_function(
            "() => (document.getElementById('hs')||{}).textContent"
            " && document.getElementById('hs').textContent.indexOf('HinksPix') >= 0",
            timeout=10000)
        ok("Add posts the controller's IP", posted and posted[-1].get("ip") == ctrl, posted)
        hs = page.inner_text("#hs")
        ok("status reports a HinksPix, not an LED fixture",
           "Added HinksPix controller" in hs, hs)
        browser.close()

    print(f"\n  screenshot: {shot}")
    print("=" * 60)
    print(f"  {_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
