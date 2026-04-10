"""Test 3D runtime viewport — verify Layout works, then Runtime with simple fixture."""
import subprocess, time, requests, sys, os, json

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5555'],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    r = requests.get('http://localhost:5555/api/settings', timeout=5)
    print('Server up:', r.status_code)
except Exception as e:
    print('Server failed:', e); proc.kill(); sys.exit(1)

BASE = 'http://localhost:5555'

# Clean slate
requests.post(BASE + '/api/reset')

# Set stage dimensions (6m x 3m x 4m)
requests.post(BASE + '/api/settings', json={'stageW': 600, 'stageH': 300, 'stageD': 400})

# Create one simple DMX fixture and position it in layout
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'Test MH', 'fixtureType': 'dmx',
    'rotation': [-30, 0, 0],
    'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16
})
fid = r.json().get('id', 0)
# Save position in layout children array (this is how the SPA positions fixtures)
layout = requests.get(BASE + '/api/layout').json()
layout['children'] = layout.get('children', [])
layout['children'].append({'id': fid, 'x': 3000, 'y': 2000, 'z': 2800})
r2 = requests.post(BASE + '/api/layout', json=layout)
print('Created+positioned fixture %d: %d %d' % (fid, r.status_code, r2.status_code))

from playwright.sync_api import sync_playwright

passed = 0
failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print('  [PASS]', name)
    else:
        failed += 1; print('  [FAIL]', name)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    console_errors = []
    page.on('console', lambda msg: console_errors.append(msg.text) if msg.type == 'error' else None)

    page.goto(BASE)
    page.wait_for_timeout(2000)

    # -- Phase 1: Layout tab --
    print('\n--- Phase 1: Layout tab ---')
    page.click('#n-layout')
    page.wait_for_timeout(3000)

    s3d_ok = page.evaluate('() => !!(window._s3d && window._s3d.inited)')
    check('Three.js scene initialized', s3d_ok)

    has_canvas = page.evaluate('() => !!document.querySelector("#stage3d canvas")')
    check('WebGL canvas in #stage3d', has_canvas)

    node_count = page.evaluate('() => (window._s3d && window._s3d.nodes) ? window._s3d.nodes.length : 0')
    check('Fixture nodes in layout: ' + str(node_count), node_count >= 1)

    has_box = page.evaluate('() => { var f=false; if(window._s3d&&window._s3d.scene) window._s3d.scene.children.forEach(function(c){if(c.userData&&c.userData.stageBox)f=true;}); return f; }')
    check('Stage wireframe box visible', has_box)

    page.screenshot(path='tests/user/layout_3d.png')

    # -- Phase 2: Runtime tab --
    print('\n--- Phase 2: Runtime tab ---')
    page.click('#n-runtime')
    page.wait_for_timeout(4000)

    has_emu_canvas = page.evaluate('() => !!document.querySelector("#emu-3d canvas")')
    check('WebGL canvas in #emu-3d', has_emu_canvas)

    emu_active = page.evaluate('() => !!(window._emu3d && window._emu3d.activeTab)')
    check('Runtime 3D active', emu_active)

    emu_nodes = page.evaluate('() => (window._emu3d && window._emu3d.nodes) ? window._emu3d.nodes.length : 0')
    check('Runtime fixture nodes: ' + str(emu_nodes), emu_nodes >= 1)

    cam_target = page.evaluate('() => { var c=window._emu3d&&window._emu3d.controls; return c ? [c.target.x.toFixed(1), c.target.y.toFixed(1), c.target.z.toFixed(1)] : null; }')
    check('Camera target set (auto-zoom)', cam_target is not None and cam_target[0] != '0.0')

    emu_box = page.evaluate('() => { var el=document.getElementById("emu-3d"); return el ? {w:el.clientWidth, h:el.clientHeight} : null; }')
    check('emu-3d container sized', emu_box is not None and emu_box['w'] > 400 and emu_box['h'] > 200)

    page.screenshot(path='tests/user/runtime_3d.png')

    # -- Phase 3: Tab switching --
    print('\n--- Phase 3: Tab switching ---')
    page.click('#n-layout')
    page.wait_for_timeout(2000)

    layout_canvas = page.evaluate('() => !!document.querySelector("#stage3d canvas")')
    check('Canvas returned to #stage3d', layout_canvas)

    layout_anim = page.evaluate('() => !!(window._s3d && window._s3d.animId)')
    check('Layout render loop restarted', layout_anim)

    page.click('#n-runtime')
    page.wait_for_timeout(3000)

    reattach = page.evaluate('() => !!document.querySelector("#emu-3d canvas")')
    check('Canvas re-attached to #emu-3d', reattach)

    page.screenshot(path='tests/user/runtime_reattach.png')

    # -- Summary --
    print('\n%d passed, %d failed out of %d tests' % (passed, failed, passed + failed))
    if console_errors:
        print('\nConsole errors:')
        for e in console_errors[:5]:
            print(' ', e)

    browser.close()

proc.kill()
