#!/usr/bin/env python3
"""
test_save_restore_spa.py — Playwright SPA coverage for #741 Group 3.

Locks down two SPA-side behaviours that #739's `file-manager.js` fix
introduced. If either regresses, a future operator gets the
import-wipes-positions bug back.

Group 3.1 — `_fmImportJson()` clears `_fixtures` (and `ld`,
`_layoutDirty`) BEFORE firing the `/api/project/import` POST. We patch
the SPA's `ra` helper to record `_fixtures` at call-time and assert
it's null at the moment the import POST is dispatched.

Group 3.2 — While `_fixtures === null`, calling `saveLayout()` early-
outs without dispatching a `/api/layout` POST. We hook `XMLHttpRequest`
to record any POSTs and call `saveLayout()` from `page.evaluate`.

Usage: python tests/test_save_restore_spa.py [-v]

Requires: pip install playwright && python -m playwright install chromium
"""
import sys
import os
import time
import json
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                 'desktop', 'shared'))

PORT = 18091
BASE = f'http://127.0.0.1:{PORT}'

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose:
            print(f'  [PASS] {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(name):
    print(f'\n── {name} ──')


def start_server():
    import parent_server
    from parent_server import app

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True,
                use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)


def seed():
    """Seed two LED fixtures at known positions through Flask test client."""
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        r1 = c.post('/api/fixtures', json={
            'name': 'h741-spa-1', 'fixtureType': 'led',
            'strings': [{'leds': 30, 'mm': 1000, 'sdir': 0}],
        })
        f1 = r1.get_json()['id']
        r2 = c.post('/api/fixtures', json={
            'name': 'h741-spa-2', 'fixtureType': 'led',
            'strings': [{'leds': 30, 'mm': 1000, 'sdir': 0}],
        })
        f2 = r2.get_json()['id']
        c.post('/api/layout', json={
            'children': [
                {'id': f1, 'x': 1000, 'y': 500, 'z': 0},
                {'id': f2, 'x': 2000, 'y': 500, 'z': 0},
            ],
        })
        # Build a project export the SPA will import. We also need
        # one different position so the import is meaningful.
        proj = c.get('/api/project/export').get_json()
        # Mutate one position so import-vs-current differ.
        for ch in (proj.get('layout') or {}).get('children', []):
            ch['x'] = (ch.get('x') or 0) + 7
        return f1, f2, proj


def main():
    print('=== #741 Group 3 SPA save/restore tests ===')
    print('Seeding...')
    f1, f2, proj = seed()
    print(f'  fixtures: f1={f1} f2={f2}')

    print('Starting Flask server...')
    start_server()
    print(f'  Server: {BASE}\n')

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
        page = ctx.new_page()
        # Auto-accept the "Load project?" confirm() dialog the SPA
        # raises before firing the import.
        page.on('dialog', lambda d: d.accept())
        page.goto(BASE, wait_until='domcontentloaded', timeout=15000)
        time.sleep(1.2)

        # ── Group 3.1 — cache cleared before import POST ────────────
        section('Group 3.1: cache cleared before import POST')

        # Install an XHR-level recorder. We overwrite send() to capture
        # the URL + the value of `window._fixtures` at the moment a
        # POST to /api/project/import is dispatched.
        page.evaluate("""
            (() => {
                window.__h741spa = {
                    importPostFiresFixturesValue: 'NEVER_FIRED',
                    layoutPosts: [],
                    importPosts: 0,
                };
                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(m, u, ...rest){
                    this.__url = u; this.__method = m;
                    return origOpen.call(this, m, u, ...rest);
                };
                XMLHttpRequest.prototype.send = function(body){
                    try {
                        if (this.__method && this.__method.toUpperCase() === 'POST') {
                            if (this.__url && this.__url.indexOf('/api/project/import') >= 0) {
                                window.__h741spa.importPosts += 1;
                                window.__h741spa.importPostFiresFixturesValue =
                                    (typeof _fixtures === 'undefined') ? 'UNDEFINED'
                                    : (_fixtures === null) ? 'NULL'
                                    : 'NON_NULL_LEN_' + ((_fixtures && _fixtures.length) || 0);
                            } else if (this.__url && this.__url.indexOf('/api/layout') >= 0) {
                                window.__h741spa.layoutPosts.push({
                                    url: this.__url,
                                    body: body || null,
                                });
                            }
                        }
                    } catch(e) { /* swallow */ }
                    return origSend.call(this, body);
                };
            })()
        """)

        # Pre-condition: navigate to layout tab so SPA has loaded
        # _fixtures (otherwise the cache-null assertion is trivially
        # true because nothing was cached in the first place).
        page.click('#n-layout')
        page.wait_for_selector('#t-layout', state='visible', timeout=5000)
        time.sleep(0.8)
        pre_fix = page.evaluate(
            "() => (typeof _fixtures !== 'undefined' && _fixtures) "
            "? _fixtures.length : -1")
        ok(pre_fix >= 2,
           f'SPA has _fixtures cached pre-import (got {pre_fix})')

        # Fire the import flow programmatically. _fmImportJson is the
        # post-confirm hook used by the file-input onload handler. It
        # carries the cache-clear logic.
        proj_json = json.dumps(proj)
        # Pass the JSON as a regular argument (NOT via string-formatted
        # JS body) to avoid quote-escaping headaches. page.evaluate
        # serialises arg → JSON-safe string and reparses inside the
        # browser, exactly like our real import flow does.
        page.evaluate(
            "(txt) => _fmImportJson(txt, 'h741-spa-test.slyshow')",
            proj_json,
        )
        # Give the synchronous cache-clear + ra() POST a tick to fire.
        # The XHR send is queued by ra(); we just need the open+send
        # to complete (which is microtask-scoped).
        page.wait_for_function(
            "() => window.__h741spa.importPosts >= 1",
            timeout=5000,
        )

        post_state = page.evaluate("() => window.__h741spa")
        ok(post_state.get('importPosts') == 1,
           f'project/import POST fired exactly once '
           f'(got {post_state.get("importPosts")})')
        ok(post_state.get('importPostFiresFixturesValue') == 'NULL',
           f'_fixtures was null at moment of import POST '
           f'(got {post_state.get("importPostFiresFixturesValue")})')

        # Wait for import to complete + loadAll() to repopulate.
        # loadAll() refetches /api/layout which sets _fixtures back to
        # an array. We poll until that happens (or timeout).
        def _fixtures_repopulated():
            v = page.evaluate(
                "() => (typeof _fixtures !== 'undefined' && _fixtures && "
                "_fixtures.length) || 0")
            return (v or 0) >= 2

        deadline = time.time() + 8
        while time.time() < deadline:
            if _fixtures_repopulated():
                break
            time.sleep(0.2)
        ok(_fixtures_repopulated(),
           'loadAll() repopulated _fixtures after import')

        # ── Group 3.2 — saveLayout() while _fixtures===null is no-op ─
        section('Group 3.2: saveLayout() early-outs while _fixtures null')

        # Reset the recorder.
        page.evaluate("() => { window.__h741spa.layoutPosts = []; }")
        # Force-null the cache (mimic the in-flight import window).
        page.evaluate("() => { _fixtures = null; }")
        # Call saveLayout() — must early-out on `if(!_fixtures)return;`
        # at app.js:1021. No /api/layout POST should be dispatched.
        page.evaluate("() => { if (typeof saveLayout === 'function') "
                      "saveLayout(); }")
        # Wait a beat to let any erroneous XHR fire.
        time.sleep(0.5)
        layout_posts = page.evaluate(
            "() => window.__h741spa.layoutPosts.length")
        ok(layout_posts == 0,
           f'saveLayout() with _fixtures=null fires 0 POSTs '
           f'(got {layout_posts})')

        # Sanity: with _fixtures repopulated, saveLayout() DOES post
        # (otherwise a user toggle silently fails). This proves the
        # guard is the only thing blocking the prior call, not a
        # wiring bug in the test.
        page.evaluate(
            "() => { window.__h741spa.layoutPosts = []; }")
        # Force-load _fixtures back from /api/layout.
        page.evaluate("""
            () => fetch('/api/layout').then(r => r.json()).then(j => {
                _fixtures = (j && j.fixtures) || [];
                window.__h741spa.refixturesLen = _fixtures.length;
            })
        """)
        # Wait for fetch to complete.
        page.wait_for_function(
            "() => typeof _fixtures !== 'undefined' && _fixtures "
            "&& _fixtures.length >= 2",
            timeout=5000,
        )
        page.evaluate("() => { if (typeof saveLayout === 'function') "
                      "saveLayout(); }")
        time.sleep(0.5)
        layout_posts2 = page.evaluate(
            "() => window.__h741spa.layoutPosts.length")
        ok(layout_posts2 >= 1,
           f'saveLayout() with _fixtures populated fires >= 1 POST '
           f'(got {layout_posts2}) — confirms the guard, not a wiring '
           f'bug, blocked the previous call')

        browser.close()

    total = _pass + _fail
    print(f'\n{"=" * 60}')
    if _fail == 0:
        print(f'  ALL {total} TESTS PASSED')
    else:
        print(f'  {_pass} passed, {_fail} failed out of {total} tests')
        for e in _errors:
            print(f'    - {e}')
    print(f'{"=" * 60}')
    sys.exit(0 if _fail == 0 else 1)


if __name__ == '__main__':
    main()
