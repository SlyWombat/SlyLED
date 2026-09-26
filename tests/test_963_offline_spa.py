"""HinksPix offline only for shows on that controller — in the browser (#963).

Real SPA, real server, nothing on the network. The Schedule panel offers the
Offline box only for a show that lights nothing but one HinksPix's pixels
(disabled, with the reason, for a show that also drives a DMX fixture) and
re-gates when the entry's Play changes; the section no longer promises
"keep playing when SlyLED is off" and says it isn't verified on hardware.
The Standalone screen offers only eligible shows and drops "SlyLED does not
need to be running".

Run: python tests/test_963_offline_spa.py   (needs playwright + chromium)
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import sys
import threading
import time

PORT = 18104
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

    ps._children[:] = [{"id": 5, "type": "hinkspix", "ip": "192.0.2.6", "name": "Kazoo",
                        "sc": 0, "strings": [], "status": 1,
                        "hinks": {"ports": [], "uploadSupported": True, "mcpu": 160}}]
    ps._fixtures[:] = [
        {"id": 50, "name": "Eaves", "fixtureType": "led", "type": "linear", "childId": 5,
         "strings": [{"port": 1, "leds": 50}]},
        {"id": 51, "name": "Porch DMX", "fixtureType": "dmx", "type": "point",
         "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 3}]
    clip = [{"actionId": 1, "startS": 0, "durationS": 30}]
    ps._timelines[:] = [
        {"id": 41, "name": "Roof only", "durationS": 30, "tracks": [{"fixtureId": 50, "clips": clip}]},
        {"id": 42, "name": "Christmas show", "durationS": 30,
         "tracks": [{"fixtureId": 50, "clips": clip}, {"fixtureId": 51, "clips": clip}]}]
    threading.Thread(target=lambda: ps.app.run(host="127.0.0.1", port=PORT, threaded=True,
                                               use_reloader=False), daemon=True).start()
    time.sleep(1.5)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(BASE + "/?tab=runtime", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function("typeof showTab === 'function' && typeof _scAddSchedule === 'function'",
                               timeout=15000)

        print("Schedule panel")
        page.evaluate("() => showTab('runtime')")
        page.wait_for_selector("#sched-panel", timeout=10000)
        time.sleep(1.0)
        page.evaluate("() => _scAddSchedule()")
        page.wait_for_selector(".sc-entry", timeout=5000)
        page.select_option(".sc-play-tl", "42")
        time.sleep(0.2)
        box = page.query_selector(".sc-e-offline")
        ok("mixed show: the Offline box is disabled", box.is_disabled())
        why = box.get_attribute("title") or ""
        ok("…with the reason as its tooltip",
           "Porch DMX" in why and "needs SlyLED running" in why and "Kazoo" in why, why)
        ok("…and an ⓘ that carries the same reason",
           page.query_selector(".sc-e-offline-why") is not None
           and "Porch DMX" in (page.get_attribute(".sc-e-offline-why", "title") or ""))
        page.select_option(".sc-play-tl", "41")
        time.sleep(0.2)
        ok("switching Play to the controller-only show enables it",
           not page.query_selector(".sc-e-offline").is_disabled())
        page.check(".sc-e-offline")
        page.select_option(".sc-play-tl", "42")
        time.sleep(0.2)
        box = page.query_selector(".sc-e-offline")
        ok("a ticked entry whose show becomes mixed stays untickable with a ⚠",
           box.is_checked() and not box.is_disabled()
           and "⚠" in (page.inner_text(".sc-e-offline-why") or ""))
        box.click()          # unticking re-gates the cell, replacing the element
        time.sleep(0.2)
        ok("…and once unticked it can't be ticked again",
           page.query_selector(".sc-e-offline").is_disabled())

        summary = flat(page.inner_text("#sched-panel details:has(#sc-hp-policy) summary"))
        ok("section retitled", "HinksPix standalone" in summary and "only use this controller" in summary, summary)
        ok("labelled not yet verified on hardware", "not yet verified on hardware" in summary, summary)
        # textContent: the section is a collapsed <details>, which inner_text skips
        panel = flat(page.evaluate("() => document.getElementById('sched-panel').textContent"))
        ok("no 'keep playing when SlyLED is off' promise", "keep playing when SlyLED is off" not in panel)
        ok("says mixed shows need SlyLED running", "need SlyLED running" in panel, panel[:300])

        print("Standalone screen")
        page.evaluate("() => hinksStandalone(5)")
        page.wait_for_selector("#hp-addtid", timeout=8000)
        body = flat(page.inner_text("#modal-body"))
        title = flat(page.inner_text("#modal-title"))
        ok("no 'SlyLED does not need to be running'", "does not need to be running" not in body, body[:300])
        ok("states the rule", "nothing but this controller" in body and "needs SlyLED running" in body, body[:400])
        ok("labelled not yet verified on hardware",
           "not yet verified on hardware" in title.lower() and "Not yet verified on hardware" in body, title)
        opt41 = page.query_selector("#hp-addtid option[value='41']")
        opt42 = page.query_selector("#hp-addtid option[value='42']")
        ok("the controller-only show is offered", opt41 is not None and not opt41.is_disabled())
        ok("the mixed show is listed but disabled, with the reason",
           opt42 is not None and opt42.is_disabled()
           and "Porch DMX" in (opt42.get_attribute("title") or ""), opt42 and opt42.get_attribute("title"))
        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
