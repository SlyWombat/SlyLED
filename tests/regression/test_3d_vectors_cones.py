"""test_3d_vectors_cones.py — weekly regression: Playwright verifies
that the View dropdown's Light Cones / Camera Cones / Orientation
Vectors toggles reach BOTH the Layout (`_s3d.scene`) viewport AND
the Dashboard/Runtime emu3d nodes (`window._emu3d.nodes`).

**Gating:** weekly-only. Skipped by `tests/regression/run_all.py`
unless `--weekly` is passed or `SLYLED_WEEKLY=1` is in the env.
End-to-end Playwright spin-up takes ~30 s; running it every CI cycle
is overkill since the underlying toggle wiring rarely changes.

Operator-flagged 2026-05-10: camera cones weren't showing up because
`_layCamConesToggle()` only walked the Layout scene. Fix in commit
bd01d90 (`_applyAllVisibility` walks both scenes; all eight toggle
handlers defer to it). This script pins the fix end-to-end with a
synthetic stage that contains both a DMX mover AND a camera so all
three cone types are exercised.

Run directly: python -X utf8 tests/regression/test_3d_vectors_cones.py
Via runner:   python tests/regression/run_all.py --weekly
"""

import atexit
import os
import signal
import subprocess
import sys
import time

import requests

# This script lives in tests/regression/; chdir to repo root.
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5577
proc = subprocess.Popen(
    [sys.executable, 'desktop/shared/parent_server.py',
     '--no-browser', '--port', str(PORT)],
    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _teardown():
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        pass


atexit.register(_teardown)
for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, lambda *_: (_teardown(), sys.exit(1)))
    except Exception:
        pass

time.sleep(5)
try:
    requests.get(f'http://localhost:{PORT}/api/settings', timeout=5)
    print('Server up on', PORT)
except Exception as e:
    print('FAIL — server not up:', e)
    _teardown()
    sys.exit(1)

BASE = f'http://localhost:{PORT}'

# Seed: 800×400×500 stage, one mover, one camera.
requests.post(BASE + '/api/settings',
              json={'stageW': 800, 'stageH': 500, 'stageD': 400})

r = requests.post(BASE + '/api/fixtures', json={
    'name': 'TestMover', 'fixtureType': 'dmx', 'rotation': [-30, 0, 0],
    'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16})
mover_id = r.json()['id']

r = requests.post(BASE + '/api/fixtures', json={
    'name': 'TestCam', 'fixtureType': 'camera', 'rotation': [-20, 0, 0],
    'fovDeg': 70, 'cameraIp': '127.0.0.1', 'cameraIdx': 0})
cam_id = r.json()['id']

lay = requests.get(BASE + '/api/layout').json()
lay['children'] = [
    {'id': mover_id, 'x': 4000, 'y': 2000, 'z': 4000},
    {'id': cam_id,   'x': 2000, 'y': 1000, 'z': 3500},
]
requests.post(BASE + '/api/layout', json=lay)
print(f'Stage seeded: mover fid={mover_id}, camera fid={cam_id}')

from playwright.sync_api import sync_playwright

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print('  [PASS]', name)
    else:
        failed += 1
        print('  [FAIL]', name)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errs = []
    page.on('console',
            lambda m: errs.append(m.text) if m.type == 'error' else None)
    page.goto(BASE)
    page.wait_for_timeout(3000)

    def count_tag(tag):
        return page.evaluate('''(t) => {
            let c = 0;
            if (window._emu3d && window._emu3d.nodes) {
                window._emu3d.nodes.forEach(g => g.traverse(o => {
                    if (o.userData && o.userData[t]) c++;
                }));
            }
            if (window._s3d && window._s3d.scene) {
                window._s3d.scene.traverse(o => {
                    if (o.userData && o.userData[t]) c++;
                });
            }
            return c;
        }''', tag)

    def count_visible(tag):
        return page.evaluate('''(t) => {
            let c = 0;
            if (window._emu3d && window._emu3d.nodes) {
                window._emu3d.nodes.forEach(g => g.traverse(o => {
                    if (o.userData && o.userData[t] && o.visible) c++;
                }));
            }
            if (window._s3d && window._s3d.scene) {
                window._s3d.scene.traverse(o => {
                    if (o.userData && o.userData[t] && o.visible) c++;
                });
            }
            return c;
        }''', tag)

    def force_toggle(name, target_on):
        """Set the named View checkbox to `target_on` and fire its
        handler. Returns True if the handler was called."""
        return page.evaluate('''({name, target}) => {
            const id_map = {
                lightcones: 'vw-lightcones',
                camcones:   'vw-camcones',
                orient:     'vw-orient',
            };
            const fn_map = {
                lightcones: '_layLightConesToggle',
                camcones:   '_layCamConesToggle',
                orient:     '_layOrientToggle',
            };
            const cb = document.getElementById(id_map[name]);
            if (!cb) return false;
            cb.checked = target;
            if (typeof window[fn_map[name]] === 'function') {
                window[fn_map[name]]();
            }
            return true;
        }''', {'name': name, 'target': target_on})

    def visit_tab(tab_id, label):
        """Click a tab nav link and wait for the scene to redraw."""
        page.click('#n-' + tab_id)
        page.wait_for_timeout(2500)
        print(f'\n── Tab: {label} (#n-{tab_id}) ──')

    # ── Layout tab ──────────────────────────────────────────────
    visit_tab('layout', 'Layout')
    check('Layout: canvas mounted',
          page.evaluate('!!document.querySelector("#stage3d canvas")'))
    check('Layout: beamCone nodes exist',
          count_tag('beamCone') >= 1)
    check('Layout: cameraCone nodes exist',
          count_tag('cameraCone') >= 1)
    check('Layout: restArrow / orientArrow nodes exist',
          count_tag('restArrow') + count_tag('orientArrow') >= 1)

    # Toggle Camera Cones on Layout. With operator's flag set true
    # via the View dropdown, every cameraCone in the Layout scene
    # should become visible.
    force_toggle('camcones', True)
    page.wait_for_timeout(500)
    total_cc = count_tag('cameraCone')
    vis_cc = count_visible('cameraCone')
    check(f'Layout: camCones toggle ON → all {total_cc} cameraCone '
          f'visible (got {vis_cc})',
          vis_cc == total_cc and total_cc >= 1)

    force_toggle('camcones', False)
    page.wait_for_timeout(500)
    vis_cc_off = count_visible('cameraCone')
    check('Layout: camCones toggle OFF → 0 cameraCone visible '
          f'(got {vis_cc_off})',
          vis_cc_off == 0)

    # Force Orientation Vectors on so we can test arrow visibility.
    force_toggle('orient', True)
    page.wait_for_timeout(500)
    total_or = count_tag('restArrow') + count_tag('orientArrow')
    vis_or = count_visible('restArrow') + count_visible('orientArrow')
    check(f'Layout: orient toggle ON → all {total_or} arrows visible '
          f'(got {vis_or})',
          vis_or == total_or and total_or >= 1)

    # ── Dashboard tab — the regression operator hit ─────────────
    visit_tab('dash', 'Dashboard')
    check('Dashboard: canvas mounted',
          page.evaluate('!!document.querySelector("#dash-3d canvas")'))
    check('Dashboard: beamCone nodes exist',
          count_tag('beamCone') >= 1)
    check('Dashboard: cameraCone nodes exist',
          count_tag('cameraCone') >= 1)

    force_toggle('camcones', True)
    page.wait_for_timeout(500)
    total_cc_d = count_tag('cameraCone')
    vis_cc_d = count_visible('cameraCone')
    check(f'Dashboard: camCones toggle reaches emu3d nodes '
          f'(visible {vis_cc_d}/{total_cc_d})',
          vis_cc_d == total_cc_d and total_cc_d >= 1)

    force_toggle('camcones', False)
    page.wait_for_timeout(500)
    vis_cc_d_off = count_visible('cameraCone')
    check('Dashboard: camCones toggle OFF hides emu3d cameraCones '
          f'(got {vis_cc_d_off})',
          vis_cc_d_off == 0)

    # Light cones on the Dashboard should also respond.
    force_toggle('lightcones', True)
    page.wait_for_timeout(500)
    total_bc = count_tag('beamCone')
    vis_bc = count_visible('beamCone')
    check(f'Dashboard: lightcones toggle reaches emu3d beam cones '
          f'(visible {vis_bc}/{total_bc})',
          vis_bc == total_bc and total_bc >= 1)

    # ── Runtime tab ─────────────────────────────────────────────
    visit_tab('runtime', 'Runtime')
    check('Runtime: canvas mounted',
          page.evaluate('!!document.querySelector("#emu-3d canvas")'))
    check('Runtime: cameraCone nodes exist',
          count_tag('cameraCone') >= 1)

    force_toggle('camcones', True)
    page.wait_for_timeout(500)
    total_cc_r = count_tag('cameraCone')
    vis_cc_r = count_visible('cameraCone')
    check(f'Runtime: camCones toggle reaches Runtime emu3d nodes '
          f'(visible {vis_cc_r}/{total_cc_r})',
          vis_cc_r == total_cc_r and total_cc_r >= 1)

    # No JS errors during the whole flow.
    real_errs = [e for e in errs if '400' not in e and '404' not in e]
    check('No JS errors during 3D tab traversal', len(real_errs) == 0)
    if real_errs:
        for e in real_errs[:5]:
            print('    ERR:', e)

    print(f'\n{passed} passed, {failed} failed out of {passed + failed}')
    browser.close()

_teardown()
sys.exit(0 if failed == 0 else 1)
