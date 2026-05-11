#!/usr/bin/env python3
"""
test_help_ui_granularity.py — Playwright integration test for the
granular help-key wiring shipped in #881.

For each navigation flow (tab → sub-section → optional modal → optional
mode), drives the SPA in a headless browser and asserts that clicking
the `?` button issues a GET to ``/api/help/<key>`` matching the
expected granular helpkey, and that the rendered body contains an
authored marker substring.

Spins up an isolated SlyLED server on port 18087.
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

PROJ = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(PROJ, 'desktop', 'shared'))

PORT = 18087
BASE = f"http://127.0.0.1:{PORT}"


# ── Start isolated server ──────────────────────────────────────────────
def _start_server():
    import tempfile
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="slyled-help-test-")
    import parent_server
    parent_server.app.run(host="127.0.0.1", port=PORT,
                          threaded=True, use_reloader=False)


t = threading.Thread(target=_start_server, daemon=True)
t.start()

deadline = time.time() + 20
up = False
while time.time() < deadline:
    try:
        urllib.request.urlopen(f"{BASE}/status", timeout=1).read()
        up = True
        break
    except Exception:
        time.sleep(0.3)
if not up:
    print("FAIL: server did not come up")
    sys.exit(1)


def _post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


dmx = _post("/api/fixtures", {
    "name": "TestMover", "type": "point", "fixtureType": "dmx",
    "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 4,
})
print(f"Seeded fixtures: DMX id={dmx.get('id')}", flush=True)


from playwright.sync_api import sync_playwright


passed = 0
failed = 0


def _record(ok_, name, detail=""):
    global passed, failed
    if ok_:
        passed += 1
        print(f"  [PASS] {name}", flush=True)
    else:
        failed += 1
        line = f"  [FAIL] {name}"
        if detail:
            line += f"  ({detail})"
        print(line, flush=True)


# ── Help-panel helpers ─────────────────────────────────────────────────
def _help_close(page):
    """Idempotently close the help panel and clear its body so the
    next open's wait isn't fooled by stale content."""
    page.evaluate("""
        var p=document.getElementById('help-panel');
        if(p&&p.style.display==='block'){toggleHelp();}
        var b=document.getElementById('help-body');
        if(b){b.innerHTML='';}
    """)


def _help_open_and_capture(page):
    """Click the `?` button (toggleHelp()) and return (key, body_text).

    Captures the GET URL via expect_response so we sync correctly with
    the network round-trip rather than waiting on a DOM mutation that
    might be a stale render.
    """
    with page.expect_response(lambda r: "/api/help/" in r.url) as resp_info:
        page.evaluate("toggleHelp()")
    resp = resp_info.value
    tail = resp.url.split("/api/help/", 1)[1]
    key = urllib.parse.unquote(tail.split("?", 1)[0])
    # Body lands once the JSON resolves + the .innerHTML assignment
    # in toggleHelp() runs. Wait until the help-body has > 20 chars
    # of content to be sure the render fired.
    page.wait_for_function(
        "document.getElementById('help-body') && "
        "document.getElementById('help-body').innerHTML.length > 20",
        timeout=4000,
    )
    body = page.text_content("#help-body") or ""
    return key, body


def _assert_help(page, label, expected_key, marker):
    _help_close(page)
    key, body = _help_open_and_capture(page)
    _record(key == expected_key,
            f"{label}: helpkey == {expected_key}",
            f"got={key!r}")
    _record(marker.lower() in body.lower(),
            f"{label}: body contains '{marker}'",
            f"body[:80]={body[:80]!r}")


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)

    # #880 workaround — settings.js:_setSection('profiles') calls a
    # loadDmxProfiles() that no module defines as a free function.
    # Install a no-op stub so the navigation flow doesn't throw and
    # the help-panel test still exercises the real _setSection path.
    page.evaluate("if(typeof loadDmxProfiles!=='function')window.loadDmxProfiles=function(){};")

    # ── Tab-level baselines ────────────────────────────────────────
    page.evaluate("showTab('dash')")
    page.wait_for_timeout(500)
    _assert_help(page, "Dashboard tab", "dash", "Getting Started")

    page.evaluate("showTab('setup')")
    page.wait_for_timeout(500)
    _assert_help(page, "Setup tab", "setup", "Fixture Setup")

    page.evaluate("showTab('firmware')")
    page.wait_for_timeout(500)
    _assert_help(page, "Firmware tab", "firmware", "Firmware")

    # ── Settings sub-sections ─────────────────────────────────────
    page.evaluate("showTab('settings')")
    page.wait_for_timeout(500)
    page.evaluate("_setSection('profiles')")
    page.wait_for_timeout(300)
    _assert_help(page, "Settings → Profiles",
                 "settings.profiles", "Profiles")

    page.evaluate("_setSection('dmx')")
    page.wait_for_timeout(300)
    _assert_help(page, "Settings → DMX",
                 "settings.dmx", "DMX")

    # ── Modals on top of settings → DMX ───────────────────────────
    page.evaluate("showDmxMonitor()")
    page.wait_for_timeout(400)
    _assert_help(page, "DMX Monitor modal",
                 "settings.dmx-monitor", "512-channel grid")
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)

    page.evaluate("showGroupControl()")
    page.wait_for_timeout(400)
    _assert_help(page, "Group Control modal",
                 "settings.group-control", "fixture group")
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)

    # ── Layout mode toggle ────────────────────────────────────────
    page.evaluate("showTab('layout')")
    page.wait_for_timeout(500)
    page.evaluate("_layView='front'; _layTool='rotate'")
    _assert_help(page, "Layout 2D rotate",
                 "layout.2d.rotate", "compass ring")

    page.evaluate("_layTool='move'")
    _help_close(page)
    key, _ = _help_open_and_capture(page)
    _record(key == "layout.2d.move",
            "Layout 2D move: helpkey == layout.2d.move",
            f"got={key!r}")

    # ── Add Fixture wizard ────────────────────────────────────────
    page.evaluate("showTab('setup')")
    page.wait_for_timeout(400)
    page.evaluate("showFixtureWizard()")
    page.wait_for_timeout(300)
    _assert_help(page, "Add Fixture wizard step 1",
                 "setup.add-fixture.step-1-choose", "library")

    page.evaluate("_wizStep2()")
    page.wait_for_timeout(300)
    _assert_help(page, "Add Fixture wizard step 2",
                 "setup.add-fixture.step-2-address",
                 "conflict detection")

    page.evaluate("_wizStep3()")
    page.wait_for_timeout(200)
    _assert_help(page, "Add Fixture wizard step 3",
                 "setup.add-fixture.step-3-confirm",
                 "Confirm")
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)

    # ── Edit DMX fixture ──────────────────────────────────────────
    page.evaluate(
        f"loadFixtures(function(){{setTimeout(function(){{editFixture({dmx['id']})}},50)}});"
    )
    page.wait_for_timeout(800)
    _assert_help(page, "Edit DMX fixture",
                 "setup.edit-fixture.dmx", "Fixture Setup")
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)

    # ── Track-editor advanced expander ────────────────────────────
    page.evaluate("showTab('actions')")
    page.wait_for_timeout(400)
    # `trackCycleMs` triggers the advOpen heuristic so the details
    # element renders open and our toggle handler pins the advanced key.
    page.evaluate(
        "_showActModal({id:0,name:'t',type:18,scope:'performer',"
        "targetIds:[],trackCycleMs:2500})"
    )
    page.wait_for_timeout(500)
    _assert_help(page, "Track editor (advanced auto-open)",
                 "actions.track-editor.advanced", "Cycle Time")
    page.evaluate("closeModal()")
    page.wait_for_timeout(200)

    # ── Stub fallback when no fragment exists ────────────────────
    page.evaluate("""
        document.getElementById('modal').style.display='block';
        window._helpModalKey='made-up.thing';
    """)
    _help_close(page)
    key, body = _help_open_and_capture(page)
    _record(key == "made-up.thing",
            "Stub key reaches backend",
            f"got={key!r}")
    _record("full user manual" in body.lower() or "no targeted help" in body.lower(),
            "Stub body offers full-manual link",
            f"body[:100]={body[:100]!r}")
    page.evaluate("document.getElementById('modal').style.display='none'")
    page.evaluate("window._helpModalKey=null")

    b.close()

print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
sys.exit(0 if failed == 0 else 1)
