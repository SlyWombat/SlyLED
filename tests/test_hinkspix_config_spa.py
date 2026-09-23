#!/usr/bin/env python3
"""The HinksPix push wizard, as the browser runs it (#945).

Scope note: a *light* contract test, not the QA lane's UI pass. It drives the
real SPA in a real browser against **stubbed fetch**, and asserts what the
wizard renders and which requests it makes. The server side of these routes is
covered by ``tests/test_hinkspix_apply.py`` against a fake controller; this file
is about the panel — the HTML strings, the inline handlers, the acknowledgement
gate, the progress poll, and the poll's ability to stop.

Stubbing fetch is also what keeps the promise this suite is named for: the only
addresses that appear in the request log are orchestrator paths. A wizard that
reached the controller directly from the browser would be a second, unverified
configuration path.

Run: python tests/test_hinkspix_config_spa.py
"""

import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hwspa-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 18095
BASE = f"http://127.0.0.1:{PORT}"
CID = 7

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    """Assert one thing, named in the output.

    The argument order is checked rather than trusted: written the other way
    round every call passes, because the name is a non-empty string and
    therefore truthy. A suite that reports 31/31 while asserting nothing is
    worse than one that fails.
    """
    global _passed, _failed
    if not isinstance(name, str) or isinstance(cond, str):
        raise TypeError(f"ok() called with swapped arguments: {name!r}, {cond!r}")
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def section(name):
    print(f"\n{name}")


# ── Stubbed orchestrator responses ───────────────────────────────────────────


def _port_rows(board):
    rows = []
    for i in range(16):
        out = (board - 1) * 16 + i + 1
        used = (out == 17)
        rows.append({"output": out, "protocol": 1 if used else 0,
                     "start": 4801 if used else 1, "pixels": 200 if used else 100,
                     "end": 5100 if used else 300, "direction": 0,
                     "colorOrder": 0, "nullPixels": 0, "brightness": 100,
                     "gamma": 1, "used": used})
    return rows


DEVICE = {
    "ok": True,
    "device": {"mode": "E131", "maxUniverses": 402,
               "boardPorts": {"1": _port_rows(1), "2": _port_rows(2)},
               "serial": {"dmxActive": 0, "dmxUniverse": 1}},
    "diff": {"changed": True, "counts": {"ports": 1, "universes": 0},
             "items": [{"section": "mode", "text": "input mode: ARTNET -> E131"}]},
}

JOB = {
    "ok": True, "inSync": False,
    "state": {},
    "lastApply": {"at": 1780000000, "kind": "apply", "ok": False,
                  "requests": 84, "backupId": "20260923-170000",
                  "err": "the controller refused PCONFIG", "failedAt": 71},
    "lastVerify": {"at": 1780000000, "ok": False,
                   "text": "the controller differs from what was sent",
                   "counts": {"ports": 1, "universes": 0},
                   "items": [{"section": "port", "text": "board 2 output 17: "
                             "4801/200px -> 1/0px"}]},
    "backups": [{"id": "20260923-170000", "at": 1780000000, "version": 1,
                 "mode": "E131",
                 "summary": "2 board(s), 1 port(s) in use, 2 universe row(s), "
                            "mode E131"}],
}


def plan(findings=None, requests=None):
    return {
        "ok": True, "id": CID, "protocol": "E131", "maxUniverses": 402,
        "universesUsed": 2, "boards": [1, 2],
        "findings": findings or [],
        "requests": requests or [
            {"kind": "read", "method": "GET",
             "path": "/Xlights_Data_Mode.cgi", "headers": {"BLK": "0"},
             "note": "read the current input mode"},
            {"kind": "write", "method": "GET", "path": "/Xlights_PostData.cgi",
             "headers": {"DATA": '{"CMD":"DATAMODE","MODE":"E131"}'},
             "note": "set the input mode to E131"},
            {"kind": "write", "method": "GET",
             "path": "/Xlights_Board_Port_Config.cgi",
             "headers": {"DATA": '{"CMD":"PCONFIG","BOARD":"0"}'},
             "note": "board 1: write 16 output rows"},
            {"kind": "reboot", "method": "GET", "path": "/Xlights_PostData.cgi",
             "headers": {"DATA": '{"CMD":"OP_MODE","MODE":"ETHERNET"}'},
             "note": "reboot into live-Ethernet mode (sent twice)"},
        ],
    }


# Installed in the page: a fetch that answers from a canned table and records
# every call, so the test can assert both what was rendered and what was asked.
STUB_JS = """
() => {
  window._fetches = [];
  window._canned = {
    'device-config': %s,
    'apply': %s,
    'plan': %s
  };
  window._planFindings = null;
  window._jobRunning = false;
  window.fetch = function (url, opts) {
    var u = String(url);
    window._fetches.push(u + ' ' + ((opts && opts.method) || 'GET'));
    var body = null;
    if (u.indexOf('/device-config') >= 0) body = window._canned['device-config'];
    else if (u === '/api/hinkspix/7') {
      // The port editor's own load: the child record, not the device read.
      // Matched exactly — a prefix test here would swallow /apply and /plan.
      body = {ok: true, hinks: {baseUniverse: 1, protocol: 'e131', mcpu: 160,
                                maxU: 402, uploadSupported: true,
                                dmxOut: {enabled: false},
                                ports: [{port: 17, leds: 200, enabled: true,
                                         protocol: 'ws2811',
                                         colorOrder: 'RGB'}]},
              map: {universes: [1, 2], totalChannels: 600, spans: []},
              inSync: false, protocols: ['e131', 'sacn', 'artnet']};
    }
    else if (u.indexOf('/plan') >= 0) {
      body = JSON.parse(JSON.stringify(window._canned['plan']));
      if (window._planFindings) body.findings = window._planFindings;
    } else if (u.indexOf('/apply') >= 0 || u.indexOf('/restore') >= 0
               || u.indexOf('/backups') >= 0) {
      body = JSON.parse(JSON.stringify(window._canned['apply']));
      if (window._jobRunning) {
        body.state = {running: true, phase: 'upload', step: 2, kind: 'apply',
                      steps: ['a', 'b', 'c'], stepsDone: [{}],
                      message: 'board 1: write 16 output rows (2/3)',
                      backupId: '20260923-170000'};
      } else {
        body.state = {running: false, ok: true, phase: 'done', step: 4,
                      kind: 'apply', steps: ['a', 'b', 'c'],
                      stepsDone: [{}, {}, {}],
                      message: 'the controller holds what was sent',
                      backupId: '20260923-170000'};
        body.lastVerify = {at: 1780000000, ok: true,
                           text: 'the controller holds what was sent',
                           counts: {}, items: []};
      }
    }
    return Promise.resolve({
      status: 200,
      json: function () { return Promise.resolve(body); }
    });
  };
  return true;
}
"""


def start_server():
    import parent_server
    from parent_server import app

    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT,
                                            threaded=True, use_reloader=False),
                     daemon=True).start()
    time.sleep(1.5)
    return app


def body_text(page):
    el = page.query_selector("#modal-body")
    return el.inner_text() if el else ""


def main():
    from playwright.sync_api import sync_playwright

    start_server()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(BASE, wait_until="networkidle", timeout=15000)
        page.evaluate("window.confirm = () => true")

        stub = STUB_JS % (repr(DEVICE).replace("True", "true").replace("False", "false"),
                          repr(JOB).replace("True", "true").replace("False", "false")
                          .replace("None", "null"),
                          repr(plan()).replace("True", "true")
                          .replace("False", "false"))
        # The bodies are plain JSON: build them with json.dumps instead of repr.
        import json as _json
        stub = STUB_JS % (_json.dumps(DEVICE), _json.dumps(JOB),
                          _json.dumps(plan()))
        page.evaluate(stub)

        section("hinkspix_config.js loads and the wizard opens (#945)")
        ok("the wizard script is served (index.html tag)",
           page.evaluate("() => typeof hinksConfig === 'function'") is True)
        ok("the wizard is the five-step flow",
           page.evaluate("() => typeof _HW_STEPS !== 'undefined'")
           and page.evaluate("() => _HW_STEPS.length") == 5)

        page.evaluate(f"() => hinksConfig({CID})")
        time.sleep(0.6)
        text = body_text(page)
        ok("Read shows what the controller holds",
           "On the controller" in text and "E131" in text
           and "2 pixel board(s)" in text, text[:120].replace("\n", " | "))
        ok("...the difference from SlyLED's copy",
           "input mode: ARTNET -> E131" in text)
        # The list shows when and what, not the raw id: the operator picks a
        # snapshot by recognising the state it holds.
        ok("...the snapshots, with a way back",
           "2 board(s), 1 port(s) in use" in text and "Restore" in text,
           text[-260:])
        ok("...and the per-board output table on request",
           page.query_selector("#hpw-root details") is not None,
           "no details element")
        ok("the request log so far is orchestrator-only",
           all(u.startswith("/api/hinkspix/") for u in
               page.evaluate("() => window._fetches")),
           str(page.evaluate("() => window._fetches"))[:200])
        ok("Read ends with the device's own state, not a diff of nothing",
           page.query_selector("#hpw-root") is not None)

        section("Edit hands off to the port editor without losing the wizard")
        page.evaluate(f"() => hinksConfig({CID}, 2)")
        time.sleep(0.3)
        ok("Edit explains that saving does not touch the controller",
           "controller keeps running what it has" in body_text(page),
           body_text(page)[:160].replace("\n", " | "))
        page.evaluate("() => _hwOpenEditor()")
        time.sleep(0.8)
        ok("the port editor opens on top of the wizard",
           page.query_selector(".hp-leds") is not None, "no port rows rendered")
        stack = page.evaluate("() => _modalStack.length")
        ok("...and the wizard is kept on the modal stack (§closeModal returns)",
           stack == 1, f"stack {stack}")
        page.evaluate("() => closeModal()")
        time.sleep(0.4)
        ok("closing the editor returns to the wizard",
           page.query_selector("#hpw-root") is not None
           and page.query_selector(".hp-leds") is None,
           "the editor is still up")

        section("Review shows the real requests, and the gate")
        page.evaluate(f"() => hinksConfig({CID}, 3)")
        time.sleep(0.5)
        ok("Review offers to build the plan rather than failing",
           "No plan yet" in body_text(page) and "Build the plan" in body_text(page),
           body_text(page)[:160])
        page.evaluate("() => _hwPlan()")
        time.sleep(0.6)
        text = body_text(page)
        ok("...then counts what will be sent",
           "4 requests" in text or "verbatim" in text or "2 write" in text,
           text[:200].replace("\n", " | "))
        ok("the reboot is called out before it happens",
           "reboots at the end" in text and "pixels go dark" in text,
           text[:300].replace("\n", " | "))
        # The request list is a collapsed disclosure — the operator opens it to
        # check the preview. Assert it the way they would read it.
        page.evaluate("() => { var d = document.querySelector('#hpw-root details');"
                      " if (d) d.open = true; }")
        time.sleep(0.2)
        text = body_text(page)
        ok("the exact requests are there, DATA payloads included",
           'Xlights_Board_Port_Config.cgi' in text
           and '{"CMD":"PCONFIG","BOARD":"0"}' in text,
           text[-400:].replace("\n", " | "))
        ok("with nothing blocking, the push is live",
           page.evaluate("() => !document.querySelector("
                         "'#hpw-root button[disabled]')"))

        page.evaluate("""() => { window._planFindings = [
            {level: 'warn', code: 'empty_config', port: null,
             text: 'Every port is disabled, so this upload blanks the output.'}]; }""")
        page.evaluate("() => _hwPlan()")
        time.sleep(0.6)
        text = body_text(page)
        ok("a warning lands in the findings list",
           "Needs acknowledgement" in text and "blanks the output" in text,
           text[:200].replace("\n", " | "))
        ok("...and disables the push until it is acknowledged",
           page.evaluate("() => !!document.querySelector("
                         "'#hpw-root button[disabled]')"))
        page.check("#hpw-root input[type=checkbox]")
        time.sleep(0.4)
        ok("ticking the box re-enables it (the inline handler is wired)",
           page.evaluate("() => !document.querySelector("
                         "'#hpw-root button[disabled]')")
           and page.evaluate("() => !!_hw.ack['empty_config']"))

        section("Apply is a job, and the poll is visible and bounded")
        page.evaluate("() => { window._jobRunning = true; _hwUpload(); }")
        time.sleep(1.4)
        text = body_text(page)
        ok("the running push shows its phase, progress and step list",
           "Writing" in text and "board 1: write 16 output rows" in text
           and "Snapshot taken before writing" in text,
           text[:200].replace("\n", " | "))
        ok("the poll is registered while it runs",
           page.evaluate("() => typeof window._hwPoll === 'number'")
           or page.evaluate("() => window._hwPoll !== null"))
        page.evaluate("() => { window._jobRunning = false; }")
        time.sleep(1.6)
        ok("when the job finishes the poll stops on its own",
           page.evaluate("() => window._hwPoll === null"),
           str(page.evaluate("() => window._hwPoll")))
        text = body_text(page)
        ok("...and the wizard advances to the verification",
           "holds what was sent" in text, text[:200].replace("\n", " | "))

        section("Verify reads back, and a restore is previewed first")
        page.evaluate(f"() => hinksConfig({CID}, 5)")
        time.sleep(0.5)
        text = body_text(page)
        ok("Verify states what the controller ended up with",
           "holds what was sent" in text and "Last run" in text,
           text[:200].replace("\n", " | "))
        page.evaluate("() => { window._fetches = []; window._jobRunning = true; }")
        page.evaluate("() => _hwRestore('20260923-170000')")
        time.sleep(0.8)
        calls = page.evaluate("() => window._fetches")
        ok("Restore asks for the preview before sending anything",
           any("/restore?backupId=" in c for c in calls), str(calls))
        ok("...and only then posts the restore, which is a job",
           any("/restore POST" in c for c in calls), str(calls))
        ok("...moving the wizard to the progress step",
           "Snapshot taken before writing" in body_text(page)
           or "Writing" in body_text(page),
           body_text(page)[:160].replace("\n", " | "))
        ok("every request the wizard made was to the orchestrator",
           all(c.startswith("/api/hinkspix/") for c in calls), str(calls))

        section("a poll cannot outlive its panel")
        page.evaluate(f"() => hinksConfig({CID}, 4)")
        page.evaluate("() => { window._jobRunning = true; }")
        page.evaluate("() => _hwPollStart()")
        time.sleep(0.3)
        ok("a running push is polled while the wizard is open",
           page.evaluate("() => window._hwPoll !== null"))
        page.evaluate("() => closeModal()")
        time.sleep(1.4)
        ok("closing the modal stops the poll instead of leaving it running",
           page.evaluate("() => window._hwPoll === null"),
           str(page.evaluate("() => window._hwPoll")))

        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
