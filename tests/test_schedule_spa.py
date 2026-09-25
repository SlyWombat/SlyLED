"""Runtime → Schedule panel and Settings → Location, in the browser (#954).

Real SPA, real server (nothing on the network: the scheduler engine is not
started — start_background_tasks() isn't called — so the panel only edits
and reads). Asserts the panel renders the off state, adds a schedule +
entry through the UI and saves it via PUT only, shows the entry in the week
grid, simulates a date, turns the schedule on, shows Resume under a manual
override, and that Settings → Location saves.

Run: python tests/test_schedule_spa.py   (needs playwright + chromium)
"""

import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-954spa-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

PORT = 18101
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


def flat(t):
    return " ".join((t or "").split())


def main():
    from playwright.sync_api import sync_playwright

    import parent_server as ps

    ps._timelines[:] = [{"id": 31, "name": "Eaves show", "durationS": 60, "tracks": []},
                        {"id": 32, "name": "Wash", "durationS": 60, "tracks": []}]
    threading.Thread(target=lambda: ps.app.run(host="127.0.0.1", port=PORT, threaded=True,
                                               use_reloader=False), daemon=True).start()
    time.sleep(1.5)
    puts = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())
        page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)

        print("Settings → Location")
        page.goto(BASE + "/?tab=settings", wait_until="networkidle", timeout=15000)
        page.evaluate("() => { showTab('settings'); if (typeof loadSettings==='function') loadSettings(); }")
        time.sleep(0.8)
        ok("location card rendered", page.query_selector("#sl-lat") is not None)
        page.fill("#sl-lat", "43.6532")
        page.fill("#sl-lon", "-79.3832")
        page.evaluate("() => schedLocSave()")
        time.sleep(0.5)
        ok("location saved", "Saved" in flat(page.inner_text("#sl-msg")), flat(page.inner_text("#sl-msg")))
        ok("server holds it", ps._schedule_doc["location"]["lat"] == 43.6532, ps._schedule_doc["location"])

        print("Runtime → Schedule panel")
        page.evaluate("() => { showTab('runtime'); }")
        time.sleep(1.5)
        panel = page.query_selector("#sched-panel")
        ok("panel present in the Runtime tab", panel is not None)
        t = flat(page.inner_text("#sched-panel"))
        ok("off state explained", "Schedule is off" in t, t[:200])
        page.evaluate("() => _scAddSchedule()")
        time.sleep(0.3)
        ok("a schedule with one entry appears",
           page.query_selector(".sc-sched") is not None and page.query_selector(".sc-entry") is not None)
        page.fill(".sc-s-name", "Christmas")
        page.select_option(".sc-play-tl", "31")
        page.select_option("#sc-idle-tl", "32")
        n_before = len(puts)
        page.evaluate("() => _scSave()")
        time.sleep(1.2)
        ok("saved via PUT /api/schedule", any(u.endswith("/api/schedule") for u in puts[n_before:]), puts)
        sch = ps._schedule_doc.get("schedules") or []
        ok("document saved with the entry",
           sch and sch[0]["name"] == "Christmas" and sch[0]["entries"][0]["play"]["timelineId"] == 31
           and sch[0]["entries"][0]["start"] == {"ref": "sunset", "offsetMin": -15},
           sch)
        ok("idle wash saved", ps._schedule_doc["idle"] == {"kind": "timeline", "timelineId": 32},
           ps._schedule_doc["idle"])
        time.sleep(0.8)
        ok("week grid rendered 7 days", len(page.query_selector_all(".sc-day")) == 7)
        ok("the entry is drawn in the grid (legend)",
           "Christmas › Evening" in flat(page.inner_text("#sc-week")), flat(page.inner_text("#sc-week"))[:200])
        page.evaluate("() => _scSimulate('2026-12-01')")
        time.sleep(0.8)
        t = flat(page.inner_text("#sc-sim"))
        ok("simulate a date lists the day with reasons",
           "2026-12-01" in t and "Christmas › Evening" in t and "sunset" in t.lower(), t[:300])

        print("On, and manual override → Resume")
        page.evaluate("() => _scSetEnabled(true)")
        time.sleep(0.8)
        ok("enabled on the server", ps._schedule_doc["enabled"] is True)
        ps._scheduler.set_override("manual", by="192.0.2.44")
        page.evaluate("() => _scFetchAll(true)")
        time.sleep(0.8)
        t = flat(page.inner_text("#sc-head"))
        ok("header says Manual — schedule paused, with who", "Manual — schedule paused" in t and "192.0.2.44" in t, t)
        ok("Resume button offered", "Resume" in t)
        page.evaluate("() => _scResume()")
        time.sleep(1.2)
        ok("resume cleared the override", not (ps._schedule_state.get("override") or {}).get("active"))
        page.evaluate("() => _scSetEnabled(false)")
        time.sleep(0.5)

        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
