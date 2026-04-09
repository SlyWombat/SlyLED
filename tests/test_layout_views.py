"""Capture layout screenshots using user's real stage config."""
import sys, os, json, time, threading, shutil
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18096
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots')
os.makedirs(OUTDIR, exist_ok=True)

# Load user config
CONFIG = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config.json')
with open(CONFIG) as f:
    cfg = json.load(f)

import parent_server
TEST_DATA = Path(os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared', 'data_test_views'))
TEST_DATA.mkdir(exist_ok=True)
parent_server.DATA = TEST_DATA

app = parent_server.app
with app.test_client() as c:
    # Import config via the config import endpoint
    r = c.post('/api/config/import', json=cfg)
    d = r.get_json()
    print(f'Config import: {d}')
    # Set stage dimensions from config layout
    lay = cfg.get('layout', {})
    cw = lay.get('canvasW', 3000)
    ch = lay.get('canvasH', 2000)
    c.post('/api/stage', json={'w': cw / 1000, 'h': ch / 1000, 'd': 1.5})

# Start server
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
    page.click('text=Layout')
    time.sleep(3)

    views = ['front', 'top', 'side', '3d']
    for v in ['Front', 'Top', 'Side', '3D']:
        btn = page.query_selector(f'button:has-text("{v}")')
        if btn:
            btn.click()
            time.sleep(1.5)
        page.screenshot(path=os.path.join(OUTDIR, f'layout-real-{v.lower()}.png'))
        print(f'Captured: layout-real-{v.lower()}.png')

    browser.close()

shutil.rmtree(str(TEST_DATA), ignore_errors=True)
print(f'\nScreenshots saved to {OUTDIR}')
