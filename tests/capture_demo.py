#!/usr/bin/env python3
"""
capture_demo.py — Capture step-by-step demo screenshots for the website.

Populates realistic data and captures each step of the user journey.
Output: server/slyled/demo/*.png

Usage:
    python tests/capture_demo.py
"""

import sys, os, json, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18085
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'server', 'slyled', 'demo')
os.makedirs(OUTDIR, exist_ok=True)

captured = []

def snap(page, name, delay=0.5):
    time.sleep(delay)
    path = os.path.join(OUTDIR, name)
    page.screenshot(path=path, full_page=False)
    captured.append(name)
    print(f'  [{len(captured):2d}] {name}')


def populate():
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'My Stage', 'darkMode': 1, 'canvasW': 10000, 'canvasH': 5000})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)
    return app


def main():
    print('SlyLED Demo Capture')
    print('=' * 40)

    print('\nPopulating...')
    app = populate()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='networkidle', timeout=15000)
        time.sleep(1)

        # Step 1: First launch — empty dashboard
        print('\nStep 1: First Launch')
        snap(page, '01-first-launch.png', 1)

        # Step 2: Go to Firmware tab
        print('\nStep 2: Firmware Tab')
        page.evaluate("showTab('firmware')")
        snap(page, '02-firmware-tab.png', 1.5)

        # Step 3: Go to Setup, discover performers
        print('\nStep 3: Setup — Add Performers')
        with app.test_client() as c:
            r = c.post('/api/children', json={'ip': '192.168.10.50'})
            cid1 = r.get_json()['id']
            c.post('/api/fixtures', json={
                'name': 'Stage Left LED', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
                'strings': [{'leds': 150, 'mm': 5000, 'sdir': 0}, {'leds': 150, 'mm': 5000, 'sdir': 2}]
            })
        page.evaluate("showTab('setup')")
        snap(page, '03-setup-performer.png', 1.5)

        # Step 4: Add DMX bridge
        print('\nStep 4: Add DMX Bridge')
        with app.test_client() as c:
            r = c.post('/api/children', json={'ip': '192.168.10.219'})
            # Simulate DMX bridge type
            for ch in c.get('/api/children').get_json():
                if ch['ip'] == '192.168.10.219':
                    ch['type'] = 'dmx'
                    ch['boardType'] = 'giga-dmx'
                    ch['name'] = 'Giga DMX Bridge'
        page.evaluate("showTab('setup')")
        snap(page, '04-setup-bridge.png', 1)

        # Step 5: Add DMX fixture via wizard
        print('\nStep 5: Add DMX Fixture')
        with app.test_client() as c:
            c.post('/api/fixtures', json={
                'name': 'Chauvet SlimPAR 56', 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 7,
                'dmxProfileId': 'generic-wash-7ch', 'aimPoint': [5000, 2000, 5000]
            })
            c.post('/api/fixtures', json={
                'name': 'ADJ Vizi Beam', 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 8, 'dmxChannelCount': 16,
                'dmxProfileId': 'generic-moving-head-16bit', 'aimPoint': [5000, 0, 5000]
            })
        page.evaluate("showTab('setup')")
        snap(page, '05-setup-fixtures.png', 1)

        # Step 6: Layout — place fixtures
        print('\nStep 6: Layout')
        with app.test_client() as c:
            c.post('/api/layout', json={'children': [
                {'id': 0, 'x': 2000, 'y': 4000, 'z': 0},
                {'id': 2, 'x': 3000, 'y': 5000, 'z': 2000},
                {'id': 3, 'x': 7000, 'y': 5000, 'z': 2000},
            ]})
            c.post('/api/surfaces', json={
                'name': 'Back Wall', 'surfaceType': 'wall', 'color': '#1e293b', 'opacity': 25,
                'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
            })
        page.evaluate("showTab('layout')")
        snap(page, '06-layout-2d.png', 1.5)

        # Step 7: 3D Layout
        print('\nStep 7: 3D Layout')
        try:
            page.evaluate("setLayoutMode('3d')")
            snap(page, '07-layout-3d.png', 2.5)
            page.evaluate("setLayoutMode('2d')")
        except Exception:
            pass

        # Step 8: Actions
        print('\nStep 8: Actions')
        with app.test_client() as c:
            c.post('/api/actions', json={'name': 'Warm Wash', 'type': 1, 'r': 255, 'g': 180, 'b': 60})
            c.post('/api/actions', json={'name': 'Ocean Wave', 'type': 5, 'speedMs': 40})
            c.post('/api/actions', json={'name': 'Fire Flicker', 'type': 6, 'r': 255, 'g': 80, 'b': 0})
        page.evaluate("showTab('actions')")
        snap(page, '08-actions.png', 1)

        # Step 9: Load preset show
        print('\nStep 9: Runtime — Load Show')
        with app.test_client() as c:
            c.post('/api/show/preset', json={'id': 'spotlight-sweep'})
        page.evaluate("showTab('runtime')")
        snap(page, '09-runtime.png', 1.5)

        # Step 10: Settings with DMX
        print('\nStep 10: Settings + DMX')
        page.evaluate("showTab('settings')")
        snap(page, '10-settings.png', 1)

        browser.close()

    print(f'\nCaptured {len(captured)} demo screenshots to {OUTDIR}')


if __name__ == '__main__':
    main()
