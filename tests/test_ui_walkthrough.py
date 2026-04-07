#!/usr/bin/env python3
"""
test_ui_walkthrough.py — Comprehensive UI walkthrough with screenshots.

Clicks every button, opens every modal, fills every form in the SPA.
Captures screenshots at each step. Reports issues found.

Output: docs/screenshots/walkthrough/ (one PNG per step)

Usage:
    python tests/test_ui_walkthrough.py
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18093
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots', 'walkthrough')
os.makedirs(OUTDIR, exist_ok=True)

_step = 0
_issues = []
_screenshots = []


def snap(page, name, delay=0.5):
    global _step
    time.sleep(delay)
    _step += 1
    fname = f'{_step:03d}-{name}.png'
    path = os.path.join(OUTDIR, fname)
    page.screenshot(path=path, full_page=False)
    _screenshots.append(fname)
    print(f'  [{_step:3d}] {fname}')


def issue(desc):
    _issues.append(desc)
    print(f'  \033[31m[ISSUE]\033[0m {desc}')


def seed():
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'UI Walkthrough', 'canvasW': 10000, 'canvasH': 5000, 'darkMode': 1})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # LED performers
        r = c.post('/api/children', json={'ip': '192.168.10.50'})
        cid1 = r.get_json()['id']
        c.post('/api/fixtures', json={
            'name': 'FOH Truss Left', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}, {'leds': 30, 'mm': 1500, 'sdir': 1}]
        })
        r = c.post('/api/children', json={'ip': '192.168.10.51'})
        cid2 = r.get_json()['id']
        c.post('/api/fixtures', json={
            'name': 'FOH Truss Right', 'type': 'linear', 'fixtureType': 'led', 'childId': cid2,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 2}]
        })

        # DMX fixtures
        c.post('/api/fixtures', json={
            'name': 'Beam 200 SL', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit', 'aimPoint': [5000, 2000, 5000]
        })
        c.post('/api/fixtures', json={
            'name': 'SlimPAR Center', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb', 'aimPoint': [5000, 0, 5000]
        })

        # Layout
        c.post('/api/layout', json={'children': [
            {'id': 0, 'x': 1000, 'y': 4500, 'z': 0},
            {'id': 1, 'x': 9000, 'y': 4500, 'z': 0},
            {'id': 2, 'x': 2000, 'y': 5000, 'z': 2000},
            {'id': 3, 'x': 5000, 'y': 2500, 'z': 5000},
        ]})

        # Objects
        c.post('/api/objects', json={
            'name': 'Back Wall', 'objectType': 'wall', 'color': '#1e293b', 'opacity': 30,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
        })

        # Actions
        for a in [
            {'name': 'Warm Solid', 'type': 1, 'r': 255, 'g': 160, 'b': 40},
            {'name': 'Cool Fade', 'type': 2, 'r': 0, 'g': 100, 'b': 255, 'r2': 100, 'g2': 0, 'b2': 200, 'speedMs': 3000},
            {'name': 'Rainbow Chase', 'type': 4, 'r': 255, 'g': 0, 'b': 0, 'speedMs': 40, 'spacing': 5},
        ]:
            c.post('/api/actions', json=a)

        # Spatial effect
        c.post('/api/spatial-effects', json={
            'name': 'Blue Sweep', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 0, 'g': 80, 'b': 220, 'size': {'radius': 2000},
            'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                       'durationS': 10, 'easing': 'linear'},
            'blend': 'add'
        })

        # Timeline
        r = c.post('/api/timelines', json={'name': 'Demo Show', 'durationS': 30})
        tl_id = r.get_json()['id']

        c.post('/api/wifi', json={'ssid': 'TestNet', 'password': 'test123'})

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)


def walkthrough():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, color_scheme='dark')
        page = ctx.new_page()

        # Collect JS errors
        js_errors = []
        page.on('console', lambda m: js_errors.append(m.text) if m.type == 'error' else None)

        page.goto(BASE, wait_until='networkidle', timeout=15000)
        time.sleep(1)

        # -- DASHBOARD --------------------------------------------
        print('\n-- Dashboard --')
        snap(page, 'dashboard')
        # Check performer table
        rows = page.query_selector_all('#t-dash table tr')
        if len(rows) < 3:
            issue('Dashboard table has < 3 rows (expected header + 2 performers)')
        # Refresh button
        btn = page.query_selector('#dash-refresh')
        if btn:
            btn.click()
            snap(page, 'dashboard-refreshing', 2)

        # -- SETUP ------------------------------------------------
        print('\n-- Setup --')
        page.evaluate("showTab('setup')")
        snap(page, 'setup-overview', 1)

        # Check Hardware section
        text = page.inner_text('#t-setup')
        if 'Hardware' not in text and 'Fixtures' not in text:
            issue('Setup missing Hardware/Fixtures sections')

        # Add Fixture modal — DMX (should be default)
        page.evaluate("showAddFixtureModal()")
        snap(page, 'setup-add-fixture-dmx-default', 0.5)
        # Verify DMX is default
        default_val = page.evaluate("document.getElementById('aft').value")
        if default_val != 'dmx':
            issue(f'Add Fixture default is "{default_val}" not "dmx"')
        else:
            print('  OK: DMX is default fixture type')

        # OFL search in add fixture
        ofl_input = page.query_selector('#af-ofl-q')
        if ofl_input:
            ofl_input.fill('par')
            page.click('button[onclick*="_afOflSearch"]')
            snap(page, 'setup-add-ofl-search', 3)
            results = page.query_selector('#af-ofl-results')
            if results:
                rtext = results.inner_text()
                if 'Select' not in rtext and len(rtext) < 20:
                    issue('OFL search in Add Fixture returned no results for "par"')
        else:
            issue('OFL search input not found in Add Fixture modal')

        # Switch to LED type
        page.evaluate("document.getElementById('aft').value='led';_toggleAddFixFields()")
        snap(page, 'setup-add-fixture-led', 0.5)

        # Switch to Group type
        page.evaluate("document.getElementById('aft').value='group';_toggleAddFixFields()")
        snap(page, 'setup-add-fixture-group', 1)

        page.evaluate("closeModal()")

        # DMX fixture Details button
        details_btn = page.query_selector('button[onclick*="showDmxDetails"]')
        if details_btn:
            details_btn.click()
            snap(page, 'setup-dmx-details', 1)
            # Check slider controls
            sliders = page.query_selector_all('.dmx-detail-slider')
            if len(sliders) == 0:
                issue('DMX Details modal has no channel sliders')
            else:
                print(f'  OK: DMX Details has {len(sliders)} channel sliders')
            # Quick color buttons
            for color in ['White', 'Red', 'Green', 'Blue']:
                btn = page.query_selector(f'button:has-text("{color}")')
                if not btn:
                    issue(f'DMX Details missing {color} quick button')
            snap(page, 'setup-dmx-details-sliders', 0.3)
            page.evaluate("closeModal()")
        else:
            issue('No DMX Details button on setup page')

        # Edit fixture
        edit_btn = page.query_selector('button[onclick*="editFixture"]')
        if edit_btn:
            edit_btn.click()
            snap(page, 'setup-edit-fixture', 0.5)
            page.evaluate("closeModal()")

        # Discover button
        disc_btn = page.query_selector('#disc-btn')
        if disc_btn:
            print('  OK: Discover button present')

        # -- LAYOUT ----------------------------------------------─
        print('\n-- Layout --')
        page.evaluate("showTab('layout')")
        snap(page, 'layout-2d', 1.5)

        # Check canvas
        canvas = page.query_selector('#t-layout canvas, #lcv')
        if not canvas:
            issue('Layout has no canvas element')

        # 3D mode
        try:
            page.evaluate("setLayoutMode('3d')")
            snap(page, 'layout-3d', 2.5)
            page.evaluate("setLayoutMode('2d')")
        except Exception as e:
            issue(f'3D mode switch failed: {e}')

        # Double-click fixture to edit (simulate)
        # Node edit modal
        try:
            page.evaluate("var placed=_fixtures.filter(_isFixturePlaced);if(placed.length)showNodeEdit(placed[0]);")
            snap(page, 'layout-node-edit', 0.5)
            # Check for Click to Aim button (DMX only)
            aim_btn = page.query_selector('button[onclick*="startAimMode"]')
            text = page.inner_text('#modal-body')
            if 'Set Position' not in text:
                issue('Node edit missing Set Position button')
            page.evaluate("closeModal()")
        except Exception:
            pass

        # -- ACTIONS ----------------------------------------------
        print('\n-- Actions --')
        page.evaluate("showTab('actions')")
        snap(page, 'actions-list', 1)

        # New action modal
        try:
            page.evaluate("newAction()")
            snap(page, 'actions-new-modal', 0.5)
            # Check action type dropdown
            type_sel = page.query_selector('#modal-body select')
            if not type_sel:
                issue('New Action modal missing type selector')
            # Fill fields
            name_input = page.query_selector('#modal-body input[type="text"]')
            if name_input:
                name_input.fill('Test Action')
            snap(page, 'actions-new-filled', 0.3)
            page.evaluate("closeModal()")
        except Exception as e:
            issue(f'New Action modal failed: {e}')

        # Edit existing action
        try:
            page.evaluate("if(_acts&&_acts.length)editAction(_acts[0].id);")
            snap(page, 'actions-edit', 0.5)
            page.evaluate("closeModal()")
        except Exception:
            pass

        # -- RUNTIME ----------------------------------------------
        print('\n-- Runtime --')
        page.evaluate("showTab('runtime')")
        snap(page, 'runtime-overview', 1.5)

        # Emulator canvas
        emu = page.query_selector('#emu-cv')
        if not emu:
            issue('Runtime emulator canvas not found')

        # Load show modal
        try:
            page.evaluate("openLoadShowModal()")
            snap(page, 'runtime-load-show', 1)
            # Check preset buttons
            presets = page.query_selector_all('#preset-list button')
            if len(presets) < 5:
                issue(f'Load Show has only {len(presets)} preset buttons (expected 14)')
            else:
                print(f'  OK: {len(presets)} preset show buttons')
            page.evaluate("closeModal()")
        except Exception as e:
            issue(f'Load Show modal failed: {e}')

        # Timeline selector
        tl_items = page.query_selector_all('#t-runtime select option, .tl-item, #tl-select option')
        if len(tl_items) < 1:
            print('  WARN: No timeline items visible')

        # -- SETTINGS --------------------------------------------─
        print('\n-- Settings --')
        page.evaluate("showTab('settings')")
        snap(page, 'settings-overview', 1)

        # Settings fields
        nm = page.query_selector('#s-nm')
        if nm:
            val = nm.evaluate('el => el.value')
            if not val:
                issue('Settings name field empty')

        # Logging section
        log_start = page.query_selector('#btn-log-start')
        log_stop = page.query_selector('#btn-log-stop')
        if not log_start or not log_stop:
            issue('Logging Start/Stop buttons missing')
        else:
            print('  OK: Logging Start/Stop buttons present')

        # Save/Load Config buttons
        export_cfg = page.query_selector('button[onclick*="exportConfig"]')
        export_show = page.query_selector('button[onclick*="exportShow"]')
        if not export_cfg:
            issue('Save Config button missing')
        if not export_show:
            issue('Save Show button missing')

        # OFL Browse button
        ofl_btn = page.query_selector('button[onclick*="showOflBrowse"]')
        if ofl_btn:
            ofl_btn.click()
            snap(page, 'settings-ofl-browse', 0.5)
            # Search
            q = page.query_selector('#ofl-q')
            if q:
                q.fill('chauvet')
                page.click('button[onclick*="_oflSearch"]')
                snap(page, 'settings-ofl-search-chauvet', 5)
                results_el = page.query_selector('#ofl-results')
                if results_el:
                    rtext = results_el.inner_text()
                    if 'Import' not in rtext:
                        issue('OFL search for "chauvet" found no importable results')
                    else:
                        print('  OK: OFL search found results for "chauvet"')
            # Manufacturers button
            mfr_btn = page.query_selector('button[onclick*="_oflShowMfrs"]')
            if mfr_btn:
                mfr_btn.click()
                snap(page, 'settings-ofl-manufacturers', 3)
            # Browse All
            browse_btn = page.query_selector('button[onclick*="_oflBrowseAll"]')
            if browse_btn:
                browse_btn.click()
                snap(page, 'settings-ofl-browse-all', 10)
            page.evaluate("closeModal()")
        else:
            issue('Search OFL button missing')

        # Profile browser
        try:
            page.evaluate("showProfileBrowser()")
            snap(page, 'settings-profiles', 1)
            # View a profile
            page.evaluate("viewProfile('generic-moving-head-16bit')")
            snap(page, 'settings-profile-detail', 0.5)
            page.evaluate("closeModal()")
            # New profile editor
            page.evaluate("setTimeout(showProfileEditor,200)")
            snap(page, 'settings-profile-editor', 0.8)
            page.evaluate("closeModal()")
        except Exception as e:
            issue(f'Profile browser failed: {e}')

        # QR code
        qr_btn = page.query_selector('button[onclick*="Qr"], button[onclick*="qr"]')
        if qr_btn:
            qr_btn.click()
            snap(page, 'settings-qr', 1)
            page.evaluate("closeModal()")

        # DMX Settings section
        snap(page, 'settings-dmx-section', 0.5)

        # -- FIRMWARE --------------------------------------------─
        print('\n-- Firmware --')
        page.evaluate("showTab('firmware')")
        snap(page, 'firmware-overview', 1.5)

        # WiFi section
        wifi_pw = page.query_selector('input[type="password"]')
        if not wifi_pw:
            issue('Firmware tab missing WiFi password input')

        # OTA section
        text = page.inner_text('#t-firmware')
        if 'OTA' not in text and 'Update' not in text and 'Flash' not in text:
            issue('Firmware tab missing OTA/Flash section')

        # -- HELP ------------------------------------------------─
        print('\n-- Help --')
        help_btn = page.query_selector('button[onclick*="Help"], button[onclick*="help"]')
        if help_btn:
            help_btn.click()
            snap(page, 'help-panel', 0.5)

        # -- JS ERRORS --------------------------------------------
        print('\n-- JS Errors --')
        real_errors = [e for e in js_errors
                       if 'github' not in e.lower() and 'firmware' not in e.lower()
                       and 'Failed to load resource' not in e and 'favicon' not in e]
        if real_errors:
            for e in real_errors[:10]:
                issue(f'JS console error: {e}')
        else:
            print('  OK: No unexpected JS errors')

        browser.close()


def main():
    print('=== SlyLED Comprehensive UI Walkthrough ===')
    print(f'Output: {OUTDIR}\n')

    print('Seeding test data...')
    seed()

    print('Running walkthrough...')
    walkthrough()

    # Summary
    print(f'\n{"="*60}')
    print(f'Screenshots: {len(_screenshots)} saved to {OUTDIR}')
    if _issues:
        print(f'\n\033[31mISSUES FOUND ({len(_issues)}):\033[0m')
        for i, iss in enumerate(_issues):
            print(f'  {i+1}. {iss}')
    else:
        print('\033[32mNo issues found!\033[0m')

    # Write issue summary
    summary_path = os.path.join(OUTDIR, 'ISSUES.md')
    with open(summary_path, 'w') as f:
        f.write('# UI Walkthrough Issues\n\n')
        if _issues:
            for iss in _issues:
                f.write(f'- {iss}\n')
        else:
            f.write('No issues found.\n')
        f.write(f'\n## Screenshots ({len(_screenshots)})\n\n')
        for s in _screenshots:
            f.write(f'- `{s}`\n')

    return 1 if _issues else 0


if __name__ == '__main__':
    sys.exit(main())
