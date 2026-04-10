"""Test ArUco print renders in modal with iframe — no popups needed."""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5560'],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    requests.get('http://localhost:5560/api/settings', timeout=5)
except:
    proc.kill(); sys.exit(1)

from playwright.sync_api import sync_playwright

passed = 0
failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print('  [PASS]', name)
    else: failed += 1; print('  [FAIL]', name)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.goto('http://localhost:5560')
    page.wait_for_timeout(2000)
    page.click('#n-settings')
    page.wait_for_timeout(1000)
    page.click('#sn-cameras')
    page.wait_for_timeout(1000)

    # Click Print ArUco Markers
    page.click('button:has-text("Print ArUco")')
    page.wait_for_timeout(2000)

    # Check modal opened
    modal = page.evaluate('() => document.getElementById("modal").style.display')
    check('Modal opened', modal == 'block')

    # Check iframe exists with markers
    iframe = page.query_selector('#aruco-frame')
    check('Iframe exists', iframe is not None)

    if iframe:
        srcdoc = page.evaluate('() => document.getElementById("aruco-frame").srcdoc || ""')
        check('Iframe has SVG content', '<svg' in srcdoc)
        check('Iframe has ArUco ID 0', 'ID 0' in srcdoc)
        check('Iframe has ArUco ID 5', 'ID 5' in srcdoc)
        check('Iframe has 150mm size', '150mm' in srcdoc)

    # Check Print button exists
    print_btn = page.query_selector('button:has-text("Print All Markers")')
    check('Print button exists', print_btn is not None)

    # Check Download button exists
    dl_btn = page.query_selector('button:has-text("Download HTML")')
    check('Download button exists', dl_btn is not None)

    # Status bar
    hs = page.evaluate('() => document.getElementById("hs").textContent')
    check('Status bar updated', 'ArUco' in hs or 'marker' in hs.lower())

    page.screenshot(path='tests/user/aruco_modal.png')

    print('\n%d passed, %d failed out of %d tests' % (passed, failed, passed + failed))
    browser.close()

proc.kill()
