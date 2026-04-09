"""Capture Settings > Cameras screenshot."""
import sys, os, json, time, threading, shutil
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18098
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots')

CONFIG = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config.json')
with open(CONFIG) as f:
    cfg = json.load(f)

import parent_server
TD = Path(os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared', 'data_test_cam'))
TD.mkdir(exist_ok=True)
parent_server.DATA = TD
app = parent_server.app
with app.test_client() as c:
    c.post('/api/config/import', json=cfg)
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
    page.goto(BASE)
    time.sleep(2)

    # Settings tab
    page.click('text=Settings')
    time.sleep(1)

    # Click Cameras sub-tab (within Settings)
    cam_btn = page.query_selector('#sn-cameras')
    if cam_btn:
        cam_btn.click()
        time.sleep(2)
    else:
        print('WARNING: #sn-cameras button not found')
    page.screenshot(path=os.path.join(OUTDIR, 'settings-cameras.png'))
    print('Captured: settings-cameras.png')

    # Layout tab — check for warning
    page.click('text=Layout')
    time.sleep(3)
    page.screenshot(path=os.path.join(OUTDIR, 'layout-cal-warning.png'))
    print('Captured: layout-cal-warning.png')

    browser.close()

shutil.rmtree(str(TD), ignore_errors=True)
