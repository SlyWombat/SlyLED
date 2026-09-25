"""HinksPix controller as hardware + the first-time guide, in the browser
(#961, #953).

Real SPA, real server, nothing on the network: the controller is a child
record injected into the server (never probed), and the guide is driven only
through its save path (PUT port table, fixtures-from-ports, rename) — Identify
and the colour test are covered server-side by tests/test_hinkspix_guide.py.

Asserts:
  * Setup → Hardware lists the controller with its summary and Set up /
    Configure; no fixture row carries the controller's name (#961)
  * the timeline Add Track picker and the layout lists never offer a
    controller placeholder, even if one exists (#961 predicate)
  * the guide describes the controller in plain words, saves pixel counts and
    names, creates the port fixture named as typed, and summarises it on Send
    (#953)

Run: python tests/test_hinkspix_guide_spa.py   (needs playwright + chromium)
"""

import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-953spa-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

PORT = 18099
BASE = f"http://127.0.0.1:{PORT}"
CID = 4

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


def flat(t):
    return " ".join((t or "").split())


def main():
    from playwright.sync_api import sync_playwright

    import parent_server as ps

    ps._children[:] = [{
        "id": CID, "type": "hinkspix", "ip": "192.0.2.6", "name": "Kazoo",
        "sc": 0, "strings": [], "status": 1, "fwVersion": "MS_160",
        "hinks": {"model": "HinksPix PRO", "baseUniverse": 1, "maxU": 402,
                  "mcpu": 160, "uploadSupported": True, "protocol": "e131",
                  "boards": {"BD1": "Long_Range", "BD2": "Local_SPI",
                             "BD3": "Not_Present"},
                  "dmxOut": {"enabled": False, "universe": None}, "ports": []}}]
    ps._fixtures[:] = []
    ps._timelines[:] = [{"id": 1, "name": "T", "durationS": 10, "tracks": []}]
    threading.Thread(target=lambda: ps.app.run(
        host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
        daemon=True).start()
    time.sleep(1.5)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(BASE + "/?tab=setup", wait_until="networkidle", timeout=15000)
        time.sleep(1.0)

        print("#961 — the controller is a Hardware row, not a fixture")
        row = page.query_selector(f'tr[data-hinks-hw="{CID}"]')
        ok("Hardware row for the controller", row is not None)
        rt = flat(row.inner_text()) if row else ""
        ok("row names it 'Kazoo'", "Kazoo" in rt, rt)
        ok("row says no ports yet", "no ports configured yet" in rt, rt)
        ok("row has Set up and Configure", "Set up" in rt and "Configure" in rt, rt)
        body = flat(page.inner_text("#t-setup") if page.query_selector("#t-setup")
                    else page.inner_text("body"))
        ok("no LED fixture row carries the controller",
           page.evaluate("() => (_fixtures||[]).length") == 0,
           page.evaluate("() => (_fixtures||[]).map(f=>f.name)"))

        print("#953 — the guide in plain words")
        page.evaluate(f"() => hinksGuide({CID})")
        time.sleep(0.8)
        t = flat(page.inner_text("#modal-body"))
        ok("step 1 names the model", "HinksPix PRO" in t, t[:160])
        ok("board 1 is Long-Range, ports 1–16, needs receivers",
           "Board 1 = Long-Range (ports 1–16)" in t and "receiver" in t, t[:300])
        ok("board 2 is Local SPI, pixels plug straight in",
           "Board 2 = Local SPI (ports 17–32)" in t and "plug straight in" in t, t[:300])
        ok("board 3 is empty", "Board 3 = empty" in t, t[:400])
        ok("the per-port cap is stated", "680 RGB pixels" in t, t)
        page.evaluate("() => _hgGo(2)")
        time.sleep(0.2)
        t = flat(page.inner_text("#modal-body"))
        ok("step 2 offers xLights import and by hand",
           "Import from my xLights show folder" in t and "Set up by hand" in t, t[:200])
        page.evaluate("() => _hgGo(3)")
        time.sleep(0.3)
        ok("step 3 lists the pixel boards' ports (1–32), not the empty board",
           page.query_selector('.hg-leds[data-port="17"]') is not None
           and page.query_selector('.hg-leds[data-port="33"]') is None)
        page.fill('.hg-len[data-port="17"]', "3.3")
        page.select_option('.hg-dens[data-port="17"]', "60")
        page.evaluate("() => _hgFromLength(17)")
        ok("length × density fills the count (3.3 m × 60/m = 198)",
           page.input_value('.hg-leds[data-port="17"]') == "198")
        page.fill('.hg-leds[data-port="17"]', "200")
        page.fill('.hg-name[data-port="17"]', "Garage eaves")
        page.fill('.hg-leds[data-port="18"]', "900")
        page.evaluate("() => _hgSaveStrings()")
        time.sleep(0.5)
        ok("over-cap count is refused with the reason",
           "more than the 680" in flat(page.inner_text("#modal-body")),
           flat(page.inner_text("#modal-body"))[:200])
        page.fill('.hg-leds[data-port="18"]', "")
        page.evaluate("() => _hgSaveStrings()")
        time.sleep(1.5)
        hinks = next(c for c in ps._children if c["id"] == CID)["hinks"]
        p17 = [p for p in hinks.get("ports") or [] if p["port"] == 17]
        ok("port 17 saved with 200 pixels", p17 and p17[0]["leds"] == 200, hinks.get("ports"))
        fx = [f for f in ps._fixtures if f.get("childId") == CID]
        ok("one port fixture created", len(fx) == 1, [f.get("name") for f in fx])
        ok("…named as typed", fx and fx[0]["name"] == "Garage eaves",
           [f.get("name") for f in fx])
        t = flat(page.inner_text("#modal-body"))
        ok("moves on to Send with a plain summary",
           "Port 17 → Garage eaves, 200 pixels, WS2811, RGB, universes 1–2" in t, t[:300])
        ok("…and says nothing was sent yet", "Nothing has been sent" in t, t[:200])

        print("#961 — pickers never offer a controller placeholder")
        # A placeholder that slipped in (pre-migration data) must still be
        # filtered by the shared predicate, not only removed on load.
        ps._fixtures.append({"id": 77, "name": "Kazoo placeholder", "fixtureType": "led",
                             "type": "linear", "childId": CID, "strings": []})
        page.evaluate("() => closeModal()")
        page.evaluate("() => new Promise(r => ra('GET','/api/layout',null,function(l){"
                      "_fixtures=l.fixtures; r();}))")
        page.evaluate("() => { _curTl = {id: 1, tracks: []}; tlAddTrack(); }")
        time.sleep(0.3)
        opts = page.evaluate("() => Array.from(document.querySelectorAll('#trk-fix option'))"
                             ".map(o => o.textContent)")
        ok("Add Track offers the port fixture", "Garage eaves" in opts, opts)
        ok("Add Track never offers the placeholder", "Kazoo placeholder" not in opts, opts)
        page.evaluate("() => closeModal()")
        page.evaluate("() => { if (typeof renderLayoutSidebar==='function') renderLayoutSidebar(); }")
        lay = page.evaluate("() => (_fixtures||[]).filter(isPickableFixture).map(f => f.name)")
        ok("layout lists skip the placeholder (isPickableFixture)",
           "Kazoo placeholder" not in lay and "Garage eaves" in lay, lay)

        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
