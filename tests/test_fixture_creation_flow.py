#!/usr/bin/env python3
"""Test the complete fixture creation flow — every step, every exit condition."""
import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18094
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots', 'walkthrough')
os.makedirs(OUTDIR, exist_ok=True)
_step = 0
_issues = []

def snap(page, name, delay=0.5):
    global _step
    time.sleep(delay)
    _step += 1
    page.screenshot(path=os.path.join(OUTDIR, f'{_step+100:03d}-fixture-{name}.png'))
    print(f'  [{_step:2d}] {name}')

def issue(msg):
    _issues.append(msg)
    print(f'  [ISSUE] {msg}')

def main():
    import parent_server
    from parent_server import app
    # Reset + seed
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Fixture Test', 'canvasW': 10000, 'canvasH': 5000, 'darkMode': 1})

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, color_scheme='dark')
        page = ctx.new_page()
        js_errors = []
        page.on('console', lambda m: js_errors.append(m.text) if m.type == 'error' else None)
        page.goto(f'http://127.0.0.1:{PORT}', wait_until='networkidle', timeout=15000)
        time.sleep(1)

        print('\n== 1. Open Add Fixture Modal ==')
        page.evaluate("showTab('setup')")
        time.sleep(1)
        page.evaluate("showAddFixtureModal()")
        time.sleep(0.5)
        snap(page, '01-modal-open')

        # Check DMX is default
        val = page.evaluate("document.getElementById('aft').value")
        if val != 'dmx':
            issue(f'Default fixture type is "{val}" not "dmx"')
        else:
            print('  OK: DMX is default')

        # Check DMX form visible, LED form hidden
        dmx_vis = page.evaluate("document.getElementById('af-dmx').style.display")
        led_vis = page.evaluate("document.getElementById('af-led').style.display")
        if dmx_vis == 'none':
            issue('DMX form hidden when DMX is default')
        if led_vis != 'none':
            issue('LED form visible when DMX is default')

        print('\n== 2. OFL Search ==')
        ofl = page.query_selector('#af-ofl-q')
        if ofl:
            ofl.fill('chauvet')
            page.click('button[onclick*="_afOflSearch"]')
            snap(page, '02-ofl-searching', 8)  # Wait for full index build
            results = page.query_selector('#af-ofl-results')
            rtext = results.inner_text() if results else ''
            if 'Select' in rtext:
                print(f'  OK: OFL results have Select buttons')
                snap(page, '03-ofl-results')
                # Click first Select
                sel_btn = results.query_selector('button')
                if sel_btn:
                    sel_btn.click()
                    time.sleep(1)
                    snap(page, '04-ofl-selected')
                    name_val = page.evaluate("document.getElementById('af-name').value")
                    ch_val = page.evaluate("document.getElementById('af-ch').value")
                    prof_val = page.evaluate("document.getElementById('af-prof').value")
                    print(f'  Auto-fill: name="{name_val}", channels={ch_val}, profile="{prof_val}"')
                    if not name_val:
                        issue('OFL Select did not auto-fill name')
                    if not prof_val:
                        issue('OFL Select did not auto-fill profile')
            else:
                issue(f'OFL search returned no results: "{rtext[:100]}"')
        else:
            issue('No OFL search input in modal')

        print('\n== 3. Fill DMX Fields ==')
        page.evaluate("document.getElementById('af-uni').value='1'")
        page.evaluate("document.getElementById('af-addr').value='1'")
        snap(page, '05-fields-filled')

        print('\n== 4. Submit ==')
        page.evaluate("_submitAddFixture()")
        time.sleep(1)
        snap(page, '06-after-submit')
        # Verify fixture created
        page.evaluate("showTab('setup')")
        time.sleep(1)
        text = page.inner_text('#t-setup')
        has_dmx = 'DMX' in text and 'U1 @ 1' in text
        if has_dmx:
            print('  OK: DMX fixture visible on Setup page')
        else:
            issue('Created fixture not visible on Setup page')
        snap(page, '07-setup-with-fixture')

        print('\n== 5. DMX Details ==')
        det_btn = page.query_selector('button[onclick*="showDmxDetails"]')
        if det_btn:
            det_btn.click()
            time.sleep(1)
            snap(page, '08-details-modal')
            sliders = page.query_selector_all('.dmx-detail-slider')
            print(f'  Channel sliders: {len(sliders)}')
            if len(sliders) == 0:
                issue('Details modal has 0 channel sliders')
            # Test quick buttons
            for btn_name in ['White', 'Red', 'Green', 'Blue', 'Blackout']:
                b = page.query_selector(f'button:has-text("{btn_name}")')
                if b:
                    b.click()
                    time.sleep(0.2)
                else:
                    issue(f'Missing quick button: {btn_name}')
            snap(page, '09-details-colors-tested')
            page.evaluate("closeModal()")
        else:
            issue('No Details button for DMX fixture')

        print('\n== 6. Edit Fixture ==')
        edit_btn = page.query_selector('button[onclick*="editFixture"]')
        if edit_btn:
            edit_btn.click()
            time.sleep(0.5)
            snap(page, '10-edit-modal')
            body = page.inner_text('#modal-body')
            checks = {
                'Universe': 'Universe' in body or 'universe' in body,
                'Address': 'Address' in body or 'address' in body or 'Start' in body,
                'Channel': 'Channel' in body or 'channel' in body,
                'Profile': 'Profile' in body or 'profile' in body,
                'Aim Point': 'Aim' in body or 'aim' in body,
            }
            for field, found in checks.items():
                if found:
                    print(f'  OK: Edit has {field}')
                else:
                    issue(f'Edit fixture missing {field} field')

            # Test Load channels
            load_btn = page.query_selector('button[onclick*="loadFixtureChannels"]')
            if load_btn:
                load_btn.click()
                time.sleep(1)
                snap(page, '11-edit-channels-loaded')
            # Save without changes
            save_btn = page.query_selector('button[onclick*="saveFixture"]')
            if save_btn:
                save_btn.click()
                time.sleep(0.5)
                snap(page, '12-edit-saved')
            else:
                page.evaluate("closeModal()")
        else:
            issue('No Edit button for fixture')

        print('\n== 7. Exit Conditions ==')
        # Cancel without saving
        page.evaluate("showAddFixtureModal()")
        time.sleep(0.3)
        page.evaluate("closeModal()")
        print('  OK: Cancel without saving')

        # Submit with empty fields
        page.evaluate("showAddFixtureModal()")
        time.sleep(0.3)
        page.evaluate("document.getElementById('af-addr').value=''")
        page.evaluate("_submitAddFixture()")
        time.sleep(0.3)
        snap(page, '13-submit-empty-addr')
        # Check for error message
        status = page.inner_text('#hs')
        print(f'  Empty addr submit result: "{status}"')

        # Submit with invalid address (> 512)
        page.evaluate("showAddFixtureModal()")
        time.sleep(0.3)
        page.evaluate("document.getElementById('af-addr').value='600'")
        page.evaluate("_submitAddFixture()")
        time.sleep(0.3)
        status = page.inner_text('#hs')
        if '512' in status or 'Address' in status:
            print(f'  OK: Invalid address rejected: "{status}"')
        else:
            issue(f'Address 600 not rejected, got: "{status}"')

        # Switch types back and forth
        page.evaluate("showAddFixtureModal()")
        time.sleep(0.3)
        for t in ['led', 'dmx', 'group', 'dmx', 'led']:
            page.evaluate(f"document.getElementById('aft').value='{t}';_toggleAddFixFields()")
            time.sleep(0.1)
        snap(page, '14-type-switching')
        page.evaluate("closeModal()")

        print('\n== 8. JS Errors ==')
        real = [e for e in js_errors if 'github' not in e.lower() and 'Failed to load' not in e and 'firmware' not in e.lower()]
        if real:
            for e in real:
                issue(f'JS error: {e}')
        else:
            print('  OK: No JS errors during entire flow')

        browser.close()

    print(f'\n{"="*60}')
    print(f'Screenshots: {_step}')
    if _issues:
        print(f'\n[ISSUES] ({len(_issues)}):')
        for i in _issues:
            print(f'  - {i}')
    else:
        print('No issues found!')
    return 1 if _issues else 0

if __name__ == '__main__':
    sys.exit(main())
