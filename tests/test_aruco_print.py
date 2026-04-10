"""Test ArUco marker print generates SVG popups."""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5558'],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    requests.get('http://localhost:5558/api/settings', timeout=5)
    print('Server up')
except:
    print('FAIL'); proc.kill(); sys.exit(1)

from playwright.sync_api import sync_playwright

passed = 0
failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print('  [PASS]', name)
    else: failed += 1; print('  [FAIL]', name)

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
    page = ctx.new_page()

    popups = []
    ctx.on('page', lambda pg: popups.append(pg))

    page.goto('http://localhost:5558')
    page.wait_for_timeout(2000)

    # Go to Settings > Cameras
    page.click('#n-settings')
    page.wait_for_timeout(1000)
    page.click('#sn-cameras')
    page.wait_for_timeout(1000)

    # Call _printAruco
    result = page.evaluate('() => { try { _printAruco(); return "ok"; } catch(e) { return "error: " + e.message; } }')
    check('_printAruco called without error', result == 'ok')
    page.wait_for_timeout(3000)

    check('Popups opened (6 markers)', len(popups) >= 6)

    # Check status bar
    status = page.evaluate('() => document.getElementById("hs") ? document.getElementById("hs").textContent : ""')
    check('Status shows marker count', 'marker' in status.lower() or 'aruco' in status.lower())

    # Check first popup has SVG content
    if popups:
        first = popups[0]
        first.wait_for_load_state()
        content = first.content()
        check('First popup is SVG', '<svg' in content.lower() or 'svg' in first.url)
        check('SVG has ArUco ID text', 'ArUco' in content or 'aruco' in content.lower())

    # Test Layout default view is 3D
    page.click('#n-layout')
    page.wait_for_timeout(3000)
    view = page.evaluate('() => window._layView')
    check('Layout default view is 3D', view == '3d')

    # Check stage dimension labels exist
    has_dim_labels = page.evaluate('''() => {
        var count = 0;
        if(window._s3d && window._s3d.scene) {
            window._s3d.scene.children.forEach(function(c) {
                if(c.userData && c.userData.stageDimLabel) count++;
            });
        }
        return count;
    }''')
    check('Stage dimension labels: ' + str(has_dim_labels), has_dim_labels >= 3)

    page.screenshot(path='tests/user/layout_3d_default.png')

    print('\n%d passed, %d failed out of %d tests' % (passed, failed, passed + failed))
    browser.close()

proc.kill()
