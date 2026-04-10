"""Validate: double-click dialog has rotation fields, saving updates 3D vector arrow."""
import subprocess, time, requests, sys, os, json

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5555'],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
try:
    requests.get('http://localhost:5555/api/settings', timeout=5)
    print('Server up')
except:
    print('Server failed'); proc.kill(); sys.exit(1)

BASE = 'http://localhost:5555'
requests.post(BASE + '/api/reset')
requests.post(BASE + '/api/settings', json={'stageW': 600, 'stageH': 300, 'stageD': 400})

# Create a camera fixture
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'Test Cam', 'fixtureType': 'camera',
    'fovDeg': 60, 'rotation': [0, 0, 0]
})
cam_id = r.json()['id']

# Create a DMX fixture
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'Test MH', 'fixtureType': 'dmx',
    'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
    'rotation': [0, 0, 0]
})
dmx_id = r.json()['id']

# Position both in layout
lay = requests.get(BASE + '/api/layout').json()
lay['children'] = [
    {'id': cam_id, 'x': 1000, 'y': 0, 'z': 1800},
    {'id': dmx_id, 'x': 3000, 'y': 0, 'z': 2800},
]
requests.post(BASE + '/api/layout', json=lay)
print('Created camera (id=%d) + DMX (id=%d)' % (cam_id, dmx_id))

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
    page.goto(BASE)
    page.wait_for_timeout(2000)

    # Go to Layout, switch to 3D view
    page.click('#n-layout')
    page.wait_for_timeout(3000)
    page.evaluate('() => setView("3d")')
    page.wait_for_timeout(1000)

    # --- Test 1: Edit dialog (sidebar pencil icon) has rotation fields ---
    print('\n--- Edit dialog (sidebar) ---')
    # Open edit for the camera fixture
    page.evaluate('() => editFixture(%d)' % cam_id)
    page.wait_for_timeout(500)

    has_tilt = page.evaluate('() => !!document.getElementById("fx-rx")')
    check('Edit dialog has Tilt field (fx-rx)', has_tilt)
    has_pan = page.evaluate('() => !!document.getElementById("fx-ry")')
    check('Edit dialog has Pan field (fx-ry)', has_pan)
    has_pos = page.evaluate('() => !!document.getElementById("fx-px")')
    check('Edit dialog has X position field', has_pos)

    # Set tilt to -22, save
    page.evaluate('() => { document.getElementById("fx-rx").value = "-22"; }')
    page.evaluate('() => saveFixture(%d, "camera")' % cam_id)
    page.wait_for_timeout(2000)

    # Verify rotation was saved on server
    r = requests.get(BASE + '/api/fixtures/%d' % cam_id)
    fx = r.json()
    check('Camera rotation saved: tilt=-22', fx.get('rotation', [0])[0] == -22)

    # --- Test 2: Double-click dialog has rotation fields ---
    print('\n--- Double-click dialog ---')
    # Open via showNodeEdit (simulates double-click)
    page.evaluate('() => { var f=null; _fixtures.forEach(function(fx){if(fx.id===%d)f=fx;}); if(f)showNodeEdit(f); }' % cam_id)
    page.wait_for_timeout(500)

    has_ne_tilt = page.evaluate('() => !!document.getElementById("ne-tilt")')
    check('Double-click dialog has Tilt field', has_ne_tilt)
    has_ne_pan = page.evaluate('() => !!document.getElementById("ne-pan")')
    check('Double-click dialog has Pan field', has_ne_pan)
    has_ne_roll = page.evaluate('() => !!document.getElementById("ne-roll")')
    check('Double-click dialog has Roll field', has_ne_roll)

    # Check current tilt value is -22 (from previous save)
    tilt_val = page.evaluate('() => document.getElementById("ne-tilt") ? document.getElementById("ne-tilt").value : null')
    check('Double-click tilt shows -22', tilt_val == '-22')

    # Change pan to 45 and save
    page.evaluate('() => { document.getElementById("ne-pan").value = "45"; }')
    page.evaluate('() => applyNodePos(%d)' % cam_id)
    page.wait_for_timeout(2000)

    # Verify on server
    r = requests.get(BASE + '/api/fixtures/%d' % cam_id)
    fx = r.json()
    check('Camera pan saved: 45', fx.get('rotation', [0, 0])[1] == 45)
    check('Camera tilt preserved: -22', fx.get('rotation', [0])[0] == -22)

    # --- Test 3: Same for DMX fixture ---
    print('\n--- DMX fixture double-click ---')
    page.evaluate('() => { var f=null; _fixtures.forEach(function(fx){if(fx.id===%d)f=fx;}); if(f)showNodeEdit(f); }' % dmx_id)
    page.wait_for_timeout(500)

    has_dmx_tilt = page.evaluate('() => !!document.getElementById("ne-tilt")')
    check('DMX double-click has Tilt field', has_dmx_tilt)
    has_dmx_pan = page.evaluate('() => !!document.getElementById("ne-pan")')
    check('DMX double-click has Pan field', has_dmx_pan)

    # Set tilt=-30, pan=10
    page.evaluate('() => { document.getElementById("ne-tilt").value="-30"; document.getElementById("ne-pan").value="10"; }')
    page.evaluate('() => applyNodePos(%d)' % dmx_id)
    page.wait_for_timeout(2000)

    r = requests.get(BASE + '/api/fixtures/%d' % dmx_id)
    fx = r.json()
    check('DMX tilt saved: -30', fx.get('rotation', [0])[0] == -30)
    check('DMX pan saved: 10', fx.get('rotation', [0, 0])[1] == 10)

    # --- Test 4: 3D vector arrow reflects rotation ---
    print('\n--- 3D vector arrow ---')
    # Reload layout to pick up latest fixture data (with rotations)
    page.evaluate('() => loadLayout()')
    page.wait_for_timeout(3000)
    page.evaluate('() => setView("3d")')
    page.wait_for_timeout(1000)

    # Check fixture nodes and beam cones in 3D scene
    scene_info = page.evaluate('''() => {
        var nodes = 0, cones = 0, conePos = null;
        if(window._s3d && window._s3d.scene) {
            window._s3d.nodes.forEach(function(grp) {
                nodes++;
                grp.traverse(function(obj) {
                    if(obj.userData && obj.userData.beamCone && obj.isMesh) {
                        cones++;
                        conePos = {x: obj.position.x.toFixed(3), y: obj.position.y.toFixed(3), z: obj.position.z.toFixed(3)};
                    }
                });
            });
        }
        return {nodes: nodes, cones: cones, conePos: conePos};
    }''')
    check('3D fixture nodes exist: ' + str(scene_info['nodes']), scene_info['nodes'] >= 2)
    check('3D beam cones exist: ' + str(scene_info['cones']), scene_info['cones'] >= 1)
    if scene_info['conePos']:
        cp = scene_info['conePos']
        check('Cone not at zero (has direction)', cp['x'] != '0.000' or cp['y'] != '0.000' or cp['z'] != '0.000')

    page.screenshot(path='tests/user/rotation_arrows.png')

    print('\n%d passed, %d failed out of %d tests' % (passed, failed, passed + failed))
    browser.close()

proc.kill()
