"""Capture layout with point cloud visible."""
import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import sys, os, json, time, threading, shutil
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18099
BASE = f'http://127.0.0.1:{PORT}'
OUTDIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'screenshots')

# The capture needs the dev point cloud. Copy it into the isolated data dir
# (#942) instead of pointing parent_server at desktop/shared/data, which the
# config import below would rewrite.
_PC = os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared', 'data', 'pointcloud.json')
if os.path.exists(_PC) and not os.path.exists(os.path.join(os.environ['SLYLED_DATA'], 'pointcloud.json')):
    shutil.copy(_PC, os.environ['SLYLED_DATA'])
import parent_server

# Load user config
CONFIG = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config.json')
with open(CONFIG) as f:
    cfg = json.load(f)

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
    page.click('text=Layout')
    time.sleep(3)

    # Click the point cloud toggle button
    pc_btn = page.query_selector('#btn-show-cloud')
    if pc_btn:
        pc_btn.click()
        time.sleep(1)

    # Front view with point cloud
    page.screenshot(path=os.path.join(OUTDIR, 'layout-pointcloud-front.png'))
    print('Captured: layout-pointcloud-front.png')

    # 3D view
    td_btn = page.query_selector('#btn-view-3d')
    if td_btn:
        td_btn.click()
        time.sleep(1.5)
    page.screenshot(path=os.path.join(OUTDIR, 'layout-pointcloud-3d.png'))
    print('Captured: layout-pointcloud-3d.png')

    # Top view
    top_btn = page.query_selector('#btn-view-top')
    if top_btn:
        top_btn.click()
        time.sleep(1)
    page.screenshot(path=os.path.join(OUTDIR, 'layout-pointcloud-top.png'))
    print('Captured: layout-pointcloud-top.png')

    browser.close()
