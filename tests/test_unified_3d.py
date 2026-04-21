"""Test unified 3D viewport across Dashboard, Runtime, and Layout tabs."""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5559'],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Always tear down the server subprocess on exit — exception, assertion
# failure, Ctrl+C, anything. Abandoned parent_server processes keep
# driving the DMX bridge at the 1 Hz Art-Net keep-alive and leave
# venue lights glowing for hours afterwards (see memory
# feedback_test_scripts_must_teardown).
import atexit, signal
def _teardown_server():
    try: proc.kill()
    except Exception: pass
    try: proc.wait(timeout=3)
    except Exception: pass
atexit.register(_teardown_server)
for _sig in (signal.SIGINT, signal.SIGTERM):
    try: signal.signal(_sig, lambda *_: (_teardown_server(), sys.exit(1)))
    except Exception: pass

time.sleep(5)
try:
    requests.get('http://localhost:5559/api/settings', timeout=5)
    print('Server up')
except:
    print('FAIL'); _teardown_server(); sys.exit(1)

BASE = 'http://localhost:5559'
requests.post(BASE + '/api/settings', json={'stageW': 600, 'stageH': 300, 'stageD': 400})
# Create a DMX fixture
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'MH1', 'fixtureType': 'dmx', 'rotation': [-30, 10, 0],
    'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16})
fid = r.json()['id']
lay = requests.get(BASE + '/api/layout').json()
lay['children'] = [{'id': fid, 'x': 3000, 'y': 2000, 'z': 2800}]
requests.post(BASE + '/api/layout', json=lay)

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
    errs = []
    page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
    page.goto(BASE)
    page.wait_for_timeout(2000)

    # --- Layout tab (init Three.js) ---
    print('\n--- Layout ---')
    page.click('#n-layout')
    page.wait_for_timeout(3000)
    check('Layout: Three.js inited', page.evaluate('() => !!(window._s3d && window._s3d.inited)'))
    check('Layout: canvas in #stage3d', page.evaluate('() => !!document.querySelector("#stage3d canvas")'))
    check('Layout: fixture nodes', page.evaluate('() => (window._s3d.nodes||[]).length') >= 1)

    # #603 — rest-direction arrow must honour pitch (rx). Fixture above
    # is rotation=[-30, 10, 0] (pitch up 30°, yaw 10°). Pre-fix, the arrow
    # was computed from yaw only with Y hardcoded to 0 — any pitched
    # fixture drew a flat arrow. Now homeDir = (sin(yaw)*cos(pitch),
    # -sin(pitch), cos(yaw)*cos(pitch)), so Y ≈ 0.5 * vecLen(0.4) = 0.2
    # for this fixture. Assert the absolute Y >= 0.05 (well above the
    # <0.001 noise the old flat computation would produce).
    rest_y = page.evaluate('''() => {
        var maxY = 0;
        (window._s3d && window._s3d.nodes || []).forEach(function(g) {
            g.traverse(function(obj) {
                if (obj.userData && obj.userData.restArrow && obj.position) {
                    var y = Math.abs(obj.position.y);
                    if (y > maxY) maxY = y;
                }
            });
        });
        return maxY;
    }''')
    check('#603 Layout: rest arrow honours pitch (|Y| = {:.3f})'.format(rest_y),
          rest_y >= 0.05)

    # --- Dashboard tab ---
    print('\n--- Dashboard ---')
    page.click('#n-dash')
    page.wait_for_timeout(4000)
    has_dash3d = page.evaluate('() => !!document.getElementById("dash-3d")')
    check('Dashboard: dash-3d div exists', has_dash3d)
    dash_canvas = page.evaluate('() => !!document.querySelector("#dash-3d canvas")')
    check('Dashboard: canvas in #dash-3d', dash_canvas)
    dash_active = page.evaluate('() => !!(window._emu3d && window._emu3d.activeTab)')
    check('Dashboard: 3D viewport active', dash_active)
    dash_nodes = page.evaluate('() => (window._emu3d && window._emu3d.nodes) ? window._emu3d.nodes.length : 0')
    check('Dashboard: fixture nodes: ' + str(dash_nodes), dash_nodes >= 1)
    page.screenshot(path='tests/user/dash_3d.png')

    # --- Runtime tab ---
    print('\n--- Runtime ---')
    page.click('#n-runtime')
    page.wait_for_timeout(4000)
    rt_canvas = page.evaluate('() => !!document.querySelector("#emu-3d canvas")')
    check('Runtime: canvas in #emu-3d', rt_canvas)
    rt_active = page.evaluate('() => !!(window._emu3d && window._emu3d.activeTab)')
    check('Runtime: 3D viewport active', rt_active)
    rt_nodes = page.evaluate('() => (window._emu3d && window._emu3d.nodes) ? window._emu3d.nodes.length : 0')
    check('Runtime: fixture nodes: ' + str(rt_nodes), rt_nodes >= 1)

    # --- Tab round-trip ---
    print('\n--- Tab switching ---')
    page.click('#n-dash')
    page.wait_for_timeout(5000)
    dr = page.evaluate('() => { var d=document.getElementById("dash-3d"); var c=window._s3d&&window._s3d.renderer?window._s3d.renderer.domElement.parentElement:null; return {dashExists:!!d, canvasParent:c?c.id:"none", active:window._emu3d?window._emu3d.activeContainer:"?"}; }')
    print('    Debug:', dr)
    check('Dash return: canvas in #dash-3d', page.evaluate('() => !!document.querySelector("#dash-3d canvas")'))

    page.click('#n-layout')
    page.wait_for_timeout(2000)
    check('Layout return: canvas in #stage3d', page.evaluate('() => !!document.querySelector("#stage3d canvas")'))
    check('Layout return: render loop running', page.evaluate('() => !!window._s3d.animId'))

    page.click('#n-runtime')
    page.wait_for_timeout(3000)
    check('Runtime return: canvas in #emu-3d', page.evaluate('() => !!document.querySelector("#emu-3d canvas")'))

    page.click('#n-dash')
    page.wait_for_timeout(5000)
    check('Dash 2nd return: canvas present', page.evaluate('() => !!document.querySelector("#dash-3d canvas")'))

    # --- Beam cone check ---
    print('\n--- Beam cones ---')
    page.wait_for_timeout(3000)
    # Check in both emu3d nodes and layout nodes
    cones = page.evaluate('''() => {
        var c = 0;
        // Check runtime/dashboard nodes
        if(window._emu3d && window._emu3d.nodes) {
            window._emu3d.nodes.forEach(function(g) {
                g.traverse(function(obj) { if(obj.userData && obj.userData.beamCone) c++; });
            });
        }
        // Also check layout scene nodes (cones may be on layout fixture groups)
        if(window._s3d && window._s3d.nodes) {
            window._s3d.nodes.forEach(function(g) {
                g.traverse(function(obj) { if(obj.userData && obj.userData.beamCone) c++; });
            });
        }
        return c;
    }''')
    check('Beam cones present: ' + str(cones), cones >= 1)

    # Verify no JS errors
    real_errs = [e for e in errs if '400' not in e and '404' not in e]
    check('No JS errors', len(real_errs) == 0)
    if real_errs:
        for e in real_errs[:3]: print('    ERR:', e)

    print('\n%d passed, %d failed out of %d tests' % (passed, failed, passed + failed))
    browser.close()

proc.kill()
