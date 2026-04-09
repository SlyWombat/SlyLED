"""Capture layout with a fixture selected to verify side panel."""
import sys, os, json, time, threading, shutil
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18097
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots')
os.makedirs(OUTDIR, exist_ok=True)

CONFIG = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config.json')
with open(CONFIG) as f:
    cfg = json.load(f)

import parent_server
TEST_DATA = Path(os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared', 'data_test_sel'))
TEST_DATA.mkdir(exist_ok=True)
parent_server.DATA = TEST_DATA

app = parent_server.app
with app.test_client() as c:
    r = c.post('/api/config/import', json=cfg)
    print(f'Import: {r.get_json()}')
    lay = cfg.get('layout', {})
    c.post('/api/stage', json={'w': lay.get('canvasW', 3000) / 1000, 'h': lay.get('canvasH', 2000) / 1000, 'd': 1.5})

def run():
    app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(2)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1400, 'height': 900})
    # Capture console messages
    console_msgs = []
    page.on('console', lambda msg: console_msgs.append(f'{msg.type}: {msg.text}'))

    page.goto(BASE)
    time.sleep(2)
    page.click('text=Layout')
    time.sleep(3)

    # Screenshot 1: initial front view
    page.screenshot(path=os.path.join(OUTDIR, 'layout-sel-front.png'))
    print('Captured: front view')

    # Click on a fixture in the 3D viewport (click near the top where fixtures are)
    # The fixtures are positioned at y=1800 in a 2000mm stage, so near the top
    viewport = page.query_selector('#stage3d')
    if viewport:
        box = viewport.bounding_box()
        # Click near top-center where DMX fixtures should be
        click_x = box['x'] + box['width'] * 0.25
        click_y = box['y'] + box['height'] * 0.15
        page.mouse.click(click_x, click_y)
        time.sleep(1)
        page.screenshot(path=os.path.join(OUTDIR, 'layout-sel-clicked.png'))
        print(f'Captured: clicked at ({click_x:.0f}, {click_y:.0f})')

    # Try clicking a fixture in the sidebar list
    sly_mh = page.query_selector('text=Sly MH 1')
    if sly_mh:
        sly_mh.click()
        time.sleep(1)
        page.screenshot(path=os.path.join(OUTDIR, 'layout-sel-sidebar.png'))
        print('Captured: sidebar click on Sly MH 1')

    # Double-click to open edit dialog
    if viewport:
        page.mouse.dblclick(click_x, click_y)
        time.sleep(1)
        page.screenshot(path=os.path.join(OUTDIR, 'layout-sel-dblclick.png'))
        print('Captured: double-click edit dialog')

    # Print console warnings
    for msg in console_msgs:
        if 'warn' in msg.lower() or 'error' in msg.lower() or 'panel' in msg.lower():
            print(f'  CONSOLE: {msg}')

    browser.close()

shutil.rmtree(str(TEST_DATA), ignore_errors=True)
print(f'\nAll saved to {OUTDIR}')
