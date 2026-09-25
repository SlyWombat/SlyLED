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
                     "colorOrder": 0, "startNulls": 0, "brightness": 100,
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
  // A job state the test writes out whole, for the end states the two canned
  // ones above cannot reach (a push that stopped part-way, #945 F1).
  window._jobState = null;
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
      if (window._jobState) {
        body.state = JSON.parse(JSON.stringify(window._jobState));
      } else if (window._jobRunning) {
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
    var resp = {
      status: 200,
      json: function () { return Promise.resolve(body); }
    };
    // #955 — a slow controller read (the real one takes 2-6 s).
    if (u.indexOf('/device-config') >= 0 && window._devDelayMs) {
      var ms = window._devDelayMs;
      return new Promise(function (res) { setTimeout(function () { res(resp); }, ms); });
    }
    return Promise.resolve(resp);
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


def flat(text):
    """The panel's words as one line.

    The wizard breaks its state across lines, and an assertion is about what it
    says rather than where it wraps — matching raw inner text makes every
    sentence a whitespace-dependent assertion, which is how a suite starts
    passing for the wrong reason.
    """
    return " ".join(text.split())


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

        section("#955 — a late device read never wipes the port editor")
        page.evaluate("() => { window._devDelayMs = 1500; window._fetches = []; }")
        page.evaluate(f"() => hinksConfig({CID})")          # starts the slow read
        time.sleep(0.2)
        page.evaluate("() => _hwGo(2)")                     # step click: no re-read
        page.evaluate("() => _hwOpenEditor()")
        time.sleep(0.5)
        ok("the port editor is up while the read is still in flight",
           page.query_selector(".hp-leds") is not None, "no port rows")
        time.sleep(1.6)                                     # the read lands now
        ok("…and is still up after the read lands (not re-rendered over)",
           page.query_selector(".hp-leds") is not None
           and page.query_selector("#hpw-root") is None,
           body_text(page)[:120].replace("\n", " | "))
        reads = [u for u in page.evaluate("() => window._fetches")
                 if "/device-config" in u]
        ok("one controller read for open + step click (no read per step)",
           len(reads) == 1, reads)
        page.evaluate("() => closeModal()")
        time.sleep(0.3)
        ok("closing the editor returns to the wizard's Edit step",
           page.query_selector("#hpw-root") is not None
           and "controller keeps running what it has" in body_text(page),
           body_text(page)[:120].replace("\n", " | "))
        page.evaluate("() => _hwGo(1)")
        time.sleep(0.2)
        ok("the read that landed meanwhile is shown on Read",
           "On the controller" in body_text(page), body_text(page)[:120])
        page.evaluate("() => { window._devDelayMs = 0; }")

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

        # The two levels either side of a warning (#945 F2, F3): an error is
        # refused outright, a note is not a gate at all. The gate is a mirror of
        # the server's, so the panel has to draw all three the same way round.
        page.evaluate("""() => { window._planFindings = [
            {level: 'info', code: 'reboot_required', port: null,
             text: 'Applying reboots the controller.'}]; }""")
        page.evaluate("() => _hwPlan()")
        time.sleep(0.6)
        text = body_text(page)
        ok("an informational finding is drawn as a Note",
           "Note" in text and "Applying reboots the controller" in text,
           text[:200].replace("\n", " | "))
        ok("...with no box to tick and the push left live",
           not page.evaluate("() => !!document.querySelector("
                             "'#hpw-root input[type=checkbox]')")
           and page.evaluate("() => !document.querySelector("
                             "'#hpw-root button[disabled]')"),
           text[:200].replace("\n", " | "))

        page.evaluate("""() => { window._planFindings = [
            {level: 'error', code: 'empty_config', port: null,
             text: 'Every port is disabled, and the controller refuses to be '
                   + 'told about no universes.'}]; }""")
        page.evaluate("() => _hwPlan()")
        time.sleep(0.6)
        text = body_text(page)
        ok("an error-level finding is drawn as Blocked",
           "Blocked" in text, text[:200].replace("\n", " | "))
        ok("...and cannot be ticked past, however many boxes are ticked",
           not page.evaluate("() => !!document.querySelector("
                             "'#hpw-root input[type=checkbox]')")
           and page.evaluate("() => !!document.querySelector("
                             "'#hpw-root button[disabled]')"),
           text[:200].replace("\n", " | "))

        page.evaluate("() => { window._planFindings = null; _hw.ack = {}; }")
        page.evaluate("() => _hwPlan()")
        time.sleep(0.6)

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

        section("A push that stopped part-way says what was done about it (#945 F1)")
        # From here the job state is written out whole: the interesting end
        # states are the ones a *failed* push leaves, and neither canned body
        # above reaches them.
        page.evaluate(f"() => hinksConfig({CID}, 4)")
        time.sleep(0.4)
        # The record and the job state are written together because the server
        # writes them together: the same `lastApply` record goes on the child
        # (which is what the badge reads) and into the job state.
        page.evaluate("""() => {
            var rec = {at: 1780000000, kind: 'apply', ok: false, requests: 75,
                       backupId: '20260923-170000', failedAt: 71,
                       err: 'PCONFIG rejected: FAILED', partial: true,
                       accepted: 70,
                       restore: {ok: true, backupId: '20260923-170000',
                                 text: 'snapshot 20260923-170000 is back on '
                                       + 'the controller'}};
            window._rec = rec;
            window._canned['apply'].lastApply = rec;
            window._jobState = {
              running: false, ok: false, phase: 'failed', step: 71,
              kind: 'apply',
              steps: ['read the current input mode',
                      'set the input mode to E131',
                      'board 1: write 16 output rows'],
              stepsDone: ['read the current input mode',
                          'set the input mode to E131'],
              message: 'the controller refused PCONFIG',
              err: rec.err, backupId: rec.backupId, partial: rec.partial,
              accepted: rec.accepted, requests: rec.requests,
              restoreBackupId: rec.backupId, restore: rec.restore};
        }""")
        page.evaluate("() => _hwPollStart()")
        time.sleep(1.3)
        text = flat(body_text(page))
        ok("how much had landed is stated in numbers, not implied",
           "70 of 75 request(s) had been accepted, so the controller was left "
           "part-way" in text, text[:400])
        ok("...the failure is still reported where the push stopped",
           "PCONFIG rejected: FAILED" in text and "(step 71)" in text, text[:300])
        ok("...and what was done about it, in the server's own words",
           "is back on the controller" in text, text[:400])
        ok("with the snapshot named, so the operator can see which one",
           "20260923-170000" in text, text[:400])
        ok("nothing left to repair, so no Restore button is put up",
           not page.evaluate("""() => Array.prototype.some.call(
                 document.querySelectorAll('#hpw-root button'),
                 function (b) { return (b.textContent || '').indexOf('Restore') === 0; })"""),
           text[:200])
        ok("the badge says part-way, and that it was put back",
           "last push failed part-way (snapshot restored)" in text, text[:200])

        page.evaluate("""() => {
            var rec = window._rec;
            rec.restore = {ok: false, backupId: '20260923-170000',
                           text: 'putting snapshot 20260923-170000 back '
                                 + 'failed: PCONFIG rejected: FAILED'};
            window._jobState.restore = rec.restore;
        }""")
        page.evaluate("() => _hwPollStart()")
        time.sleep(1.3)
        text = flat(body_text(page))
        ok("a recovery that could not be done says so, and where to go",
           "putting snapshot 20260923-170000 back failed" in text
           and "the first step lists the snapshots to go back to" in text,
           text[:400])
        ok("...and the one-click way back is offered",
           page.evaluate("""() => Array.prototype.some.call(
                 document.querySelectorAll('#hpw-root button'),
                 function (b) { return (b.textContent || '').indexOf('Restore 2026') === 0; })"""),
           text[:200])
        ok("the badge does not claim a restore that did not happen",
           "last push failed part-way" in text
           and "snapshot restored" not in text, text[:200])

        page.evaluate("""() => {
            window._rec.restore = undefined;
            window._rec.partial = false;
            window._rec.accepted = 1;
            window._jobState.restore = undefined;
            window._jobState.partial = false;
            window._jobState.accepted = 1;
        }""")
        page.evaluate("() => _hwPollStart()")
        time.sleep(1.3)
        text = flat(body_text(page))
        ok("a failure before any write is not called part-way",
           "Nothing had been written yet" in text
           and "left part-way" not in text, text[:400])
        ok("...and no repair is offered for damage that never happened",
           not page.evaluate("""() => Array.prototype.some.call(
                 document.querySelectorAll('#hpw-root button'),
                 function (b) { return (b.textContent || '').indexOf('Restore') === 0; })"""),
           text[:200])
        ok("every request that section made was still to the orchestrator",
           all(c.startswith("/api/hinkspix/") for c in
               page.evaluate("() => window._fetches")),
           str(page.evaluate("() => window._fetches"))[:200])
        page.evaluate("() => { window._jobState = null; }")

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
