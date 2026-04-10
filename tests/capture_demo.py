#!/usr/bin/env python3
"""
capture_demo.py — Capture step-by-step demo screenshots for the website.

Captures from both the SPA (Playwright) and a live ESP32 device config UI.
Output: server/slyled/demo/*.png

Usage:
    python tests/capture_demo.py
    python tests/capture_demo.py --child 192.168.10.233
"""

import sys, os, json, time, threading, argparse
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


def populate_and_start():
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', type=str, default='192.168.10.233', help='ESP32 IP for config captures')
    args = parser.parse_args()

    print('SlyLED Demo Capture')
    print('=' * 40)

    print('\nStarting server...')
    app = populate_and_start()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='networkidle', timeout=15000)
        time.sleep(1)

        # ── Step 1: First launch ─────────────────────────────────────
        print('\nStep 1: First Launch')
        snap(page, '01-first-launch.png', 1)

        # ── Step 2: Firmware tab ─────────────────────────────────────
        print('\nStep 2: Firmware Tab')
        page.evaluate("showTab('firmware')")
        snap(page, '02-firmware-tab.png', 1.5)

        # ── Step 3: ESP32 config UI — Dashboard ─────────────────────
        print(f'\nStep 3: ESP32 Config Dashboard ({args.child})')
        child_page = ctx.new_page()
        try:
            child_page.goto(f'http://{args.child}/config', wait_until='networkidle', timeout=10000)
            time.sleep(1)
            snap(child_page, '03-esp32-dashboard.png', 0.5)

            # ── Step 4: ESP32 config — Settings tab ──────────────────
            print('\nStep 4: ESP32 Settings')
            tabs = child_page.query_selector_all('button, .tab, [onclick]')
            for tab in tabs:
                txt = (tab.inner_text() or '').lower()
                if 'setting' in txt:
                    tab.click()
                    time.sleep(0.5)
                    break
            snap(child_page, '04-esp32-settings.png', 0.5)

            # ── Step 5: ESP32 config — Config tab (strings) ─────────
            print('\nStep 5: ESP32 String Config')
            for tab in tabs:
                txt = (tab.inner_text() or '').lower()
                if 'config' in txt and 'setting' not in txt:
                    tab.click()
                    time.sleep(0.5)
                    break
            snap(child_page, '05-esp32-strings.png', 0.5)
        except Exception as e:
            print(f'  ESP32 capture failed: {e}')
            # Fallback — still continue with SPA captures
        finally:
            child_page.close()

        # ── Step 6: Setup — Add performer + discover ─────────────────
        print('\nStep 6: Setup — Performers')
        with app.test_client() as c:
            c.post('/api/children', json={'ip': args.child})
            r = c.get('/api/children').get_json()
            cid = r[0]['id'] if r else 0
            c.post('/api/fixtures', json={
                'name': 'ESP Dual String', 'type': 'linear', 'fixtureType': 'led', 'childId': cid,
                'strings': [{'leds': 150, 'mm': 5000, 'sdir': 2}, {'leds': 150, 'mm': 5000, 'sdir': 0}]
            })
        page.evaluate("showTab('setup')")
        snap(page, '06-setup-performer.png', 1.5)

        # ── Step 7: Add DMX fixtures ─────────────────────────────────
        print('\nStep 7: Setup — DMX Fixtures')
        with app.test_client() as c:
            c.post('/api/fixtures', json={
                'name': 'Chauvet SlimPAR 56', 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 7,
                'dmxProfileId': 'generic-wash-7ch', 'rotation': [-20, 0, 0]
            })
            c.post('/api/fixtures', json={
                'name': 'ADJ Vizi Beam 5RX', 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 8, 'dmxChannelCount': 16,
                'dmxProfileId': 'generic-moving-head-16bit', 'rotation': [0, 0, 0]
            })
        page.evaluate("showTab('setup')")
        snap(page, '07-setup-dmx.png', 1)

        # ── Step 8: Layout — 2D ──────────────────────────────────────
        print('\nStep 8: Layout 2D')
        with app.test_client() as c:
            c.post('/api/layout', json={'children': [
                {'id': 0, 'x': 2000, 'y': 4000, 'z': 0},
                {'id': 1, 'x': 4000, 'y': 5000, 'z': 2000},
                {'id': 2, 'x': 7000, 'y': 5000, 'z': 2000},
            ]})
            c.post('/api/objects', json={
                'name': 'Back Wall', 'objectType': 'wall', 'color': '#1e293b', 'opacity': 25,
                'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
            })
        page.evaluate("showTab('layout')")
        snap(page, '08-layout-2d.png', 1.5)

        # ── Step 9: Layout — 3D ──────────────────────────────────────
        print('\nStep 9: Layout 3D')
        try:
            page.evaluate("setLayoutMode('3d')")
            snap(page, '09-layout-3d.png', 2.5)
            page.evaluate("setLayoutMode('2d')")
        except Exception:
            pass

        # ── Step 10: Actions ──────────────────────────────────────────
        print('\nStep 10: Actions')
        with app.test_client() as c:
            c.post('/api/actions', json={'name': 'Warm Wash', 'type': 1, 'r': 255, 'g': 180, 'b': 60})
            c.post('/api/actions', json={'name': 'Ocean Rainbow', 'type': 5, 'speedMs': 40})
            c.post('/api/actions', json={'name': 'Fire Flicker', 'type': 6, 'r': 255, 'g': 80, 'b': 0})
            c.post('/api/spatial-effects', json={
                'name': 'Blue Sweep', 'category': 'spatial-field', 'shape': 'sphere',
                'r': 0, 'g': 100, 'b': 255, 'size': {'radius': 2500},
                'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                           'durationS': 12, 'easing': 'linear'}, 'blend': 'add'
            })
        page.evaluate("showTab('actions')")
        snap(page, '10-actions.png', 1)

        # ── Step 11: Runtime — Load preset show ──────────────────────
        print('\nStep 11: Runtime')
        with app.test_client() as c:
            c.post('/api/show/preset', json={'id': 'spotlight-sweep'})
        page.evaluate("showTab('runtime')")
        snap(page, '11-runtime.png', 1.5)

        # ── Step 12: Settings + DMX ──────────────────────────────────
        print('\nStep 12: Settings')
        page.evaluate("showTab('settings')")
        snap(page, '12-settings.png', 1)

        browser.close()

    print(f'\nCaptured {len(captured)} demo screenshots to {OUTDIR}')


if __name__ == '__main__':
    main()
