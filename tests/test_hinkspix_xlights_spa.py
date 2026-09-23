#!/usr/bin/env python3
"""The xLights import dialog, as the browser runs it (#947).

A *light* contract test, not the QA lane's UI pass: it drives the real SPA in a
real browser against **stubbed fetch** and asserts what the dialog renders and
what it posts. The server side of the two routes is covered by
``tests/test_hinkspix_xlights_routes.py``; this file is about the panel — the
folder box and its upload fallback, the proposal rendered as a diff, the tick
state that travels back as `accepted`, the result screen, and the ambiguity a
two-controller show produces.

The canned bodies are not hand-written: the preview and the accept are produced
by calling the real routes in this process (against the operator's real show
folder, in a temp SLYLED_DATA), so the dialog is asserted against the payload it
will actually receive rather than against a shape invented here.

Stubbing fetch is also what keeps the promise this suite is named for: any
address the panel might reach appears in the request log, and the only ones that
do are orchestrator paths. A dialog that talked to a controller from the browser
would be a second, unverified configuration path.

Run: python tests/test_hinkspix_xlights_spa.py
"""

import json
import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-hx947spa-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 18096
BASE = f"http://127.0.0.1:{PORT}"
CID = 7

SHOW = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fixtures", "xlights_home_eves")

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    """Assert one thing, named in the output.

    The argument order is checked rather than trusted: written the other way
    round every call passes, because the name is a non-empty string and
    therefore truthy.
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


# ── The payloads, from the real routes ───────────────────────────────────────


def make_child(cid=CID, base=100, ports=None):
    return {
        "id": cid, "type": "hinkspix", "boardType": "HinksPix PRO",
        "ip": "192.168.10.6", "name": "Roofline", "status": 1, "seen": 0,
        "sc": 0, "strings": [],
        "hinks": {
            "model": "HinksPix PRO", "hardwareV3": False, "mcpu": 160,
            "maxU": 402, "uploadSupported": True,
            "boards": {"BD1": "Long_Range", "BD2": "Local_SPI",
                       "BD3": "Not_Present"},
            "protocol": "e131", "baseUniverse": base,
            "dmxOut": {"enabled": False, "universe": None},
            "ports": ports if ports is not None else [
                {"port": 1, "leds": 100, "enabled": True},
                {"port": 2, "leds": 300, "enabled": True},
            ],
            "configPushedAt": 0, "configHash": "",
        },
    }


def payloads():
    """What the two routes answer, computed by calling them."""
    import parent_server
    from parent_server import app

    parent_server._children[:] = [make_child()]
    parent_server._fixtures[:] = []
    parent_server._nxt_fix = 100
    c = app.test_client()

    preview = c.post("/api/hinkspix/import/xlights",
                     json={"showFolder": SHOW, "cid": CID}).get_json()
    # Twice: the first accept creates the fixture, the second one reports it as
    # skipped — the two branches the dialog's result screen has to draw, both
    # produced by the route itself rather than written out by hand.
    first = c.post(f"/api/hinkspix/{CID}/import/xlights/accept",
                   json={"proposal": preview, "createFixtures": True}).get_json()
    second = c.post(f"/api/hinkspix/{CID}/import/xlights/accept",
                    json={"proposal": preview, "createFixtures": True}).get_json()
    return preview, first, second


# Installed in the page: a fetch that answers from a canned table and records
# every call *and body*, so the test can assert what was rendered and what was
# asked for.
STUB_JS = """
(payloads) => {
  window._fetches = [];
  window._bodies = [];
  window._x = {mode: 'ok', preview: payloads.preview, applied: payloads.first,
               again: payloads.second, empty: payloads.empty,
               controller: ''};
  window.fetch = function (url, opts) {
    var u = String(url);
    var method = (opts && opts.method) || 'GET';
    window._fetches.push(u + ' ' + method);
    window._bodies.push({url: u, method: method,
                         body: (opts && opts.body) ? String(opts.body) : null});
    if (window._bodies.length && opts && opts.body) {
      try { var b = JSON.parse(String(opts.body));
            if (b && b.controller) window._x.controller = b.controller; } catch (e) {}
    }
    var body = null;
    if (u.indexOf('/import/xlights/accept') >= 0) {
      // The route answers differently the second time — the fixture it made the
      // first time is now in the way — so the stub counts the applies.
      var nth = window._x.accepts || 0;
      window._x.accepts = nth + 1;
      body = JSON.parse(JSON.stringify(nth ? window._x.again : window._x.applied));
    } else if (u.indexOf('/import/xlights') >= 0) {
      if (window._x.mode === 'ambiguous') {
        return Promise.resolve({status: 400, json: function () {
          return Promise.resolve({err: '2 controllers to choose from '
            + '(Garage, Eaves) - name the one to import',
            controllers: ['Garage', 'Eaves']}); }});
      }
      if (window._x.mode === 'empty') {
        body = JSON.parse(JSON.stringify(window._x.preview));
        body.models = []; body.fixtures = [];
      } else if (window._x.mode === 'error') {
        return Promise.resolve({status: 400, json: function () {
          return Promise.resolve({err: 'no such folder: /nope'}); }});
      } else {
        body = window._x.preview;
      }
    } else if (u === '/api/layout') {
      body = {fixtures: []};                 // loadFixtures() after an accept
    } else if (u === '/api/hinkspix/%d') {
      body = {ok: true, hinks: {baseUniverse: 1, protocol: 'e131', mcpu: 160,
                                maxU: 402, uploadSupported: true,
                                dmxOut: {enabled: false},
                                boards: {BD1: 'Long_Range', BD2: 'Local_SPI',
                                         BD3: 'Not_Present'},
                                ports: [{port: 17, leds: 200, enabled: true,
                                         protocol: 'ws2811',
                                         colorOrder: 'RGB'}]},
              map: {universes: [1, 2], totalChannels: 600, spans: []},
              inSync: false, protocols: ['e131', 'sacn', 'artnet'],
              caps: {key: 'pro_v12', name: 'PRO V1/V2', boards: 3,
                     maxPixelPort: 48, maxPixelPortChannels: 2040,
                     pixelProtocols: ['ws2811', 'ws2812b'],
                     smartRemoteTypes: ['hinkspix_4']}};
    }
    return Promise.resolve({
      status: 200,
      json: function () { return Promise.resolve(body); }
    });
  };
  return true;
}
""" % CID


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
    return " ".join(text.split())


def click_button(page, needle):
    """Press the button whose label starts with `needle`."""
    return page.evaluate(
        """(needle) => {
             var bs = Array.prototype.slice.call(document.querySelectorAll('button'));
             var b = bs.filter(function (x) {
               return (x.textContent || '').indexOf(needle) === 0; })[0];
             if (!b) return false;
             b.click();
             return true;
           }""", needle)


def main():
    preview, first, second = payloads()
    assert preview and preview.get("ok"), preview
    assert first and first.get("ok") and first.get("created"), first
    assert second and second.get("ok") and second.get("skipped"), second

    ambiguous = {"err": "2 controllers", "controllers": ["Garage", "Eaves"]}
    start_server()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(BASE, wait_until="networkidle", timeout=15000)
        page.evaluate(STUB_JS, {"preview": preview, "first": first,
                                "second": second, "ambiguous": ambiguous,
                                "empty": preview})

        section("The dialog opens from the port table (#947)")
        ok("hinkspix.js carries the import entry point",
           page.evaluate("() => typeof hinksXlightsImport === 'function'") is True)
        page.evaluate(f"() => hinksConfigure({CID})")
        time.sleep(0.6)
        ok("the port table renders", page.query_selector(".hp-leds") is not None)
        ok("with a way into the import",
           click_button(page, "Import from xLights"), "no import button")
        time.sleep(0.3)
        text = flat(body_text(page))
        ok("the dialog asks for the show folder",
           page.query_selector("#hxi-folder") is not None, text[:160])
        ok("...explains that the orchestrator does the reading",
           "xlights_networks.xml" in text and "orchestrator reads them" in text,
           text[:200])
        ok("...and offers the two files for an orchestrator that cannot see "
           "the folder", page.query_selector("#hxi-files") is not None)
        ok("the port table is kept behind it, so Cancel gives it back",
           page.evaluate("() => _modalStack.length") == 1,
           str(page.evaluate("() => _modalStack.length")))

        section("Reading a folder proposes, and renders the diff")
        page.fill("#hxi-folder", SHOW)
        click_button(page, "Read folder")
        time.sleep(0.5)
        text = flat(body_text(page))
        ok("the read carries the folder and the controller it is for",
           any(b["body"] and SHOW in b["body"] and f'"cid":{CID}' in b["body"]
               for b in page.evaluate("() => window._bodies")),
           str(page.evaluate("() => window._bodies"))[:300])
        ok("the controller from the file is named, with its address",
           "Ethernet_" in text and "192.168.2.10" in text, text[:200])
        ok("the model is listed with the output it drives",
           "Single Line" in text and "17" in text, text[:400])
        ok("the file's start channel is shown as a universe and channel",
           "u1 ch1" in text, text[:400])
        ok("the address xLights holds is called out against the unit's",
           "192.168.10.6" in text and "must answer on" in text, text[:600])
        ok("the base-universe move is called out",
           "base universe would move" in text or "base universe 100" in text,
           text[:600])
        ok("the diff says what is added and what is kept",
           "added: 17" in text and "kept as they are" in text, text[:600])
        ok("the model is ticked by default",
           page.evaluate("() => document.querySelectorAll('#modal-body input[type=checkbox]').length") >= 2
           and page.evaluate("""() => {
                 var bs = document.querySelectorAll('#modal-body input[type=checkbox]');
                 /* the first is the model row, the second the fixtures toggle */
                 return bs[0].checked; }""") is True)
        ok("creating fixtures is offered and on by default",
           page.query_selector("#hxi-mkfix") is not None
           and page.query_selector("#hxi-mkfix").is_checked())

        section("Unticking a model travels back as `accepted: false`")
        page.evaluate("""() => {
            var bs = document.querySelectorAll('#modal-body input[type=checkbox]');
            bs[0].click(); }""")
        time.sleep(0.3)
        ok("the row is now unticked",
           page.evaluate("""() => document.querySelectorAll(
                 '#modal-body input[type=checkbox]')[0].checked""") is False)
        page.query_selector("#hxi-mkfix").uncheck()
        time.sleep(0.2)
        click_button(page, "Apply to SlyLED")
        time.sleep(0.5)
        sent = [b for b in page.evaluate("() => window._bodies")
                if b["url"].endswith("/import/xlights/accept")]
        ok("the accept posts the proposal with the tick state on the row",
           len(sent) == 1 and '"accepted":false' in sent[0]["body"],
           str(sent)[:300])
        ok("...and the fixture toggle the operator left off",
           len(sent) == 1 and '"createFixtures":false' in sent[0]["body"],
           str(sent)[:300])
        text = flat(body_text(page))
        ok("the result screen reports what was written",
           "Written to SlyLED" in text, text[:200])
        ok("...names the fixture the model became",
           "Created 1 fixture(s)" in text and "Single Line (port 17)" in text,
           text[:300])

        section("Running the import again reports the fixture it already made")
        # The result screen is an end state, not a form: the operator's second
        # pass is to read the folder again and apply what comes back. Reading
        # again must therefore clear the previous result — otherwise the summary
        # stays up over the new proposal, with no Apply button on it.
        click_button(page, "Read folder")
        time.sleep(0.5)
        text = flat(body_text(page))
        ok("reading again puts the proposal back, not the old result",
           page.query_selector("#hxi-mkfix") is not None
           and "No fixture was created" not in text, text[:300])
        ok("...so there is an Apply button to press again",
           click_button(page, "Apply to SlyLED"))
        time.sleep(0.5)
        text = flat(body_text(page))
        ok("a second apply says nothing was created",
           "No fixture was created" in text, text[:300])
        ok("...and reports the skip with the reason the route gave",
           "Skipped" in text and "already on this controller" in text,
           text[:400])

        section("Back to the port table, with what was written in it")
        ok("there is a way back", click_button(page, "Back to the port table"))
        time.sleep(0.6)
        ok("the port table is up again, not the import dialog",
           page.query_selector(".hp-leds") is not None
           and page.query_selector("#hxi-folder") is None)
        ok("...and it was re-read from the server rather than patched",
           page.evaluate("() => window._fetches")
           .count(f"/api/hinkspix/{CID} GET") >= 2,
           str(page.evaluate("() => window._fetches"))[-200:])

        section("A show with two controllers offers the choice")
        page.evaluate(f"() => hinksXlightsImport({CID})")
        time.sleep(0.2)
        page.evaluate("() => { window._x.mode = 'ambiguous'; }")
        page.fill("#hxi-folder", SHOW)
        click_button(page, "Read folder")
        time.sleep(0.4)
        ok("the names come back as a chooser, not a retype",
           page.query_selector("#hxi-ctrl") is not None
           and page.evaluate("""() => Array.prototype.map.call(
                 document.querySelectorAll('#hxi-ctrl option'),
                 function (o) { return o.value; })""") == ["Garage", "Eaves"],
           body_text(page)[:200])
        ok("...with the server's own explanation",
           "2 controllers to choose" in flat(body_text(page)),
           flat(body_text(page))[:200])
        page.evaluate("() => { window._x.mode = 'ok'; }")
        page.evaluate("() => { document.getElementById('hxi-ctrl').value = 'Eaves'; }")
        click_button(page, "Read folder")
        time.sleep(0.4)
        ok("the next read names the controller the operator picked",
           page.evaluate("() => window._x.controller") == "Eaves",
           str(page.evaluate("() => window._x.controller")))

        section("A folder with nothing for this controller says so")
        page.evaluate(f"() => hinksXlightsImport({CID})")
        time.sleep(0.2)
        page.evaluate("() => { window._x.mode = 'empty'; }")
        page.fill("#hxi-folder", SHOW)
        click_button(page, "Read folder")
        time.sleep(0.4)
        ok("the empty state is a sentence, not an empty table",
           "No model in the show folder belongs to this controller"
           in flat(body_text(page)), flat(body_text(page))[:200])

        section("A refused read is reported where the operator is looking")
        page.evaluate(f"() => hinksXlightsImport({CID})")
        time.sleep(0.2)
        page.evaluate("() => { window._x.mode = 'error'; }")
        page.fill("#hxi-folder", "/nope")
        click_button(page, "Read folder")
        time.sleep(0.4)
        ok("the error is shown in the dialog, not thrown at the console",
           "no such folder" in flat(body_text(page)),
           flat(body_text(page))[:200])
        ok("...and no proposal is rendered from a failure",
           page.query_selector("#hxi-mkfix") is None
           and "Apply to SlyLED" not in flat(body_text(page)))

        section("The dialog only ever talks to the orchestrator")
        calls = page.evaluate("() => window._fetches")
        ok("every request went to an /api/hinkspix/ path",
           all(c.startswith("/api/hinkspix/") for c in calls), str(calls))
        ok("...and none of them to the controller's address",
           not any("192.168.10.6" in c or "192.168.2.10" in c for c in calls),
           str(calls))

        browser.close()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
