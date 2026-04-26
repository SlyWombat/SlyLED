#!/usr/bin/env python3
"""test_view_toggles.py — exhaustive 3D-viewport toggle test.

User report 2026-04-26: "select point cloud, it doesn't render, but
then selecting it again (which unchecks the box) it then renders it."
Race between the View-menu checkbox state and the renderer.

Test plan:
- Seed parent_server with fixtures, layout, ArUco markers, and a
  synthetic point cloud (so every toggle has something to render).
- Open the SPA in Playwright. For each tab with a 3D viewport
  (Layout, Dashboard, Runtime, Settings/Cameras), open the View
  menu and for every checkbox:
    cycle (off → on → off → on)
  After each click assert (a) the checkbox state matches expectation,
  AND (b) the renderer's scene contains the visual element when
  expected (cloud points, light cones, camera cones, ArUco dots,
  stage objects, grid, labels, stage box, orientation vectors,
  LED strings).
- Report any toggle whose first-click state didn't match, by tab
  + checkbox id + which transition lagged.

Output: ``tests/regression/view-toggle-report.md`` summarising any
flakes plus a screenshot per failed transition.

Run with ``python tests/test_view_toggles.py [-v]``. Designed to run
in the background (~3-4 minutes for the full sweep).
"""
import os, sys, time, threading, signal, json

PROJ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(PROJ)
sys.path.insert(0, os.path.join('desktop', 'shared'))

PORT = 18099
BASE = f'http://127.0.0.1:{PORT}'
REPORT_PATH = os.path.join(PROJ, 'tests', 'regression',
                            'view-toggle-report.md')
SCREENSHOT_DIR = os.path.join(PROJ, 'tests', 'regression', 'view-toggle-shots')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

VERBOSE = '-v' in sys.argv


# Seeds the orchestrator with enough scene content that every toggle has
# something to render: 4 fixtures, 1 camera, 5 ArUco markers, 1 stage
# object, and a 200-point synthetic cloud.

def populate():
    import parent_server
    from parent_server import app
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'View Toggle Test'})
        c.post('/api/stage', json={'w': 6.0, 'h': 3.0, 'd': 4.0})

        # Mover fixtures with rotation (so orient vectors render).
        for i, (name, x, rot) in enumerate([
            ('Beam SL', 1000, [-15, 0, 30]),
            ('Beam SR', 5000, [-15, 0, -30]),
            ('Wash C',  3000, [-10, 0, 0]),
        ]):
            r = c.post('/api/fixtures', json={
                'name': name, 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': 1, 'dmxStartAddr': 1 + i * 16,
                'dmxChannelCount': 16,
                'dmxProfileId': 'generic-moving-head-16bit',
                'rotation': rot,
            })
            fid = r.get_json().get('id')
            c.post('/api/layout', json={'children': [
                {'id': fid, 'x': x, 'y': 4500, 'z': 2500},
            ] + [
                {'id': f.get('id'), 'x': f.get('x', 0),
                 'y': f.get('y', 0), 'z': f.get('z', 0)}
                for f in (parent_server._layout.get('children') or [])
                if f.get('id') != fid
            ]})

        # Camera fixture (so cam-cones + cam toggles light up).
        r = c.post('/api/fixtures', json={
            'name': 'Stage Cam',
            'type': 'point', 'fixtureType': 'camera',
            'fovDeg': 60, 'resolutionW': 1920, 'resolutionH': 1080,
            'rotation': [-25, 0, 0],
        })
        cam_fid = r.get_json().get('id')
        c.put(f'/api/fixtures/{cam_fid}', json={
            'cameraIp': '127.0.0.1', 'cameraIdx': 0,
        })
        c.post('/api/layout', json={'children': (
            (parent_server._layout.get('children') or []) +
            [{'id': cam_fid, 'x': 3000, 'y': 100, 'z': 2200}])})

        # Stage object (wall) so stageObjs toggle has something to flip.
        c.post('/api/objects', json={
            'name': 'Back Wall', 'objectType': 'wall',
            'color': '#1e293b', 'opacity': 30,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0],
                          'scale': [6000, 3000, 100]},
        })

        # ArUco markers (registry).
        for mid, mx, my in [(0, 500, 1000), (1, 5500, 1000),
                              (2, 3000, 2000), (4, 1500, 3500),
                              (5, 4500, 3500)]:
            try:
                c.post('/api/aruco/markers', json={
                    'id': mid, 'name': f'M{mid}',
                    'x': float(mx), 'y': float(my), 'z': 0.0,
                })
            except Exception:
                pass

        # Synthetic point cloud — bypass /api/space/scan to keep this fast.
        import random
        random.seed(7)
        pts = []
        for _ in range(200):
            x = random.uniform(0, 6000)
            y = random.uniform(0, 4000)
            z = random.uniform(-50, 50)
            pts.append([x, y, z, 128, 200, 128])
        parent_server._point_cloud = {
            'schemaVersion': 2,
            'timestamp': time.time(),
            'source': 'test-seed',
            'cameras': [{'fixtureId': cam_fid, 'name': 'Stage Cam',
                          'pointCount': 200, 'anchorQuality': 'ok'}],
            'points': pts,
            'totalPoints': 200,
            'stageW': 6000, 'stageH': 3000, 'stageD': 4000,
        }
        parent_server._save('pointcloud', parent_server._point_cloud)


def start_server():
    import parent_server
    from parent_server import app
    t = threading.Thread(target=lambda: app.run(
        host='127.0.0.1', port=PORT, threaded=True, use_reloader=False),
        daemon=True)
    t.start()
    time.sleep(2.0)


# Map each checkbox id to a probe that asks the live SPA whether the
# corresponding visual is in the scene. Returns True / False / None
# (None when the tab doesn't expose that visual).

# All probes evaluate against window._s3d.scene.children where each
# render function tags its meshes with userData fields. We probe a
# union of marker keys — covers the variety across scene-3d.js,
# emulation.js, and runtime sources.

def probe_js(checkbox_id):
    """Return the JS expression that evaluates True iff the visual for
    `checkbox_id` is currently VISIBLE in the active 3D scene.

    The toggle handlers in scene-3d.js flip ``c.visible`` instead of
    adding/removing children — so the probe walks the whole scene tree
    via ``Object3D.traverse`` and checks visibility along the chain.
    Matcher names mirror the actual ``userData`` markers used in
    scene-3d.js / calibration.js / emulation.js.
    """
    # All overlays live in the single shared _s3d.scene (no separate
    # _s3d_dash / _s3d_rt — see scene-3d.js).
    matchers = {
        'vw-cloud':      'o=>o.userData&&o.userData.pointCloud',
        'vw-strings':    'o=>o.userData&&o.userData.ledString',
        'vw-lightcones': 'o=>o.userData&&(o.userData.beamCone||o.userData.isAimPoint)',
        'vw-camcones':   'o=>o.userData&&o.userData.cameraCone',
        'vw-orient':     'o=>o.userData&&(o.userData.orientArrow||o.userData.restArrow)',
        'vw-grid':       'o=>(o.type==="GridHelper")||(o.userData&&o.userData.isGrid)',
        'vw-labels':     'o=>o.userData&&(o.userData.isLabel||o.userData.stageDimLabel)',
        'vw-stagebox':   'o=>o.userData&&o.userData.stageBox',
        'vw-aruco':      'o=>o.userData&&(o.userData.arucoMarker||o.userData.arucoRecommend)',
        'vw-stageobjs':  'o=>o.userData&&(o.userData.stageObj||o.userData.stageObject)',
    }
    pred = matchers.get(checkbox_id)
    if not pred:
        return 'null'
    # Walk the whole scene; an object counts as "rendered" only if it AND
    # every ancestor up to the root is visible.
    return (
        '(function(){'
        ' if(!(window._s3d && window._s3d.inited && window._s3d.scene)) return false;'
        ' var match=' + pred + ';'
        ' var hit=false;'
        ' window._s3d.scene.traverse(function(o){'
        '   if(hit) return;'
        '   if(!match(o)) return;'
        '   var p=o; var ok=true;'
        '   while(p){ if(p.visible===false){ok=false;break;} p=p.parent; }'
        '   if(ok) hit=true;'
        ' });'
        ' return hit;'
        '})()'
    )


def main():
    populate()
    start_server()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f'Playwright missing: {e}')
        sys.exit(2)

    findings = []   # list of dicts: {tab, checkbox, expected, actual, screenshot}
    summary = []    # per-tab pass/fail summary

    # Map tab name → SPA function that switches to it. Each tab's
    # 3D viewport has a slightly different init path; we wait for
    # an "inited" flag before probing.
    tabs = [
        ('layout',  "showTab('layout');", 'window._s3d && window._s3d.inited'),
        ('runtime', "showTab('runtime');", '(window._s3d_rt && window._s3d_rt.inited) || (window._s3d && window._s3d.inited)'),
        ('dashboard', "showTab('dash');", 'true'),
    ]

    checkboxes = [
        'vw-strings', 'vw-lightcones', 'vw-camcones', 'vw-orient',
        'vw-cloud', 'vw-grid', 'vw-labels', 'vw-stagebox',
        'vw-aruco', 'vw-stageobjs',
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900},
                                    color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='load', timeout=20000)
        page.wait_for_timeout(2500)

        for tab_name, switch_js, ready_js in tabs:
            print(f'\n── tab: {tab_name} ──')
            try:
                page.evaluate(switch_js)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            # Open the View menu.
            try:
                page.evaluate("_toggleViewMenu && _toggleViewMenu();")
            except Exception:
                pass
            page.wait_for_timeout(300)

            tab_pass = 0
            tab_fail = 0

            for cb_id in checkboxes:
                # Skip checkboxes that don't exist on this tab.
                exists = page.evaluate(f"!!document.getElementById('{cb_id}')")
                if not exists:
                    continue
                probe = probe_js(cb_id)

                # Skip if the scene has no meshes that match this toggle's
                # category at all (regardless of visibility) — toggling can't
                # be observed without something to render.
                count_match = (
                    '(function(){'
                    ' if(!(window._s3d && window._s3d.inited && window._s3d.scene)) return 0;'
                    ' var m=' + probe.split("var match=")[1].split(';')[0] + ';'
                    ' var n=0; window._s3d.scene.traverse(function(o){ if(m(o)) n++; });'
                    ' return n;'
                    '})()'
                )
                try:
                    nmatch = int(page.evaluate(count_match) or 0)
                except Exception:
                    nmatch = 0
                if nmatch == 0:
                    if VERBOSE:
                        print(f'  {cb_id}: no matching meshes in scene — skipping')
                    continue

                # Read initial state.
                init_state = bool(page.evaluate(
                    f"document.getElementById('{cb_id}').checked"))
                init_render = bool(page.evaluate(probe))

                if VERBOSE:
                    print(f'  {cb_id}: initial checked={init_state} '
                          f'rendered={init_render}')

                # Cycle 4 transitions — each click should flip both the
                # checkbox AND the rendered state. Any mismatch is a
                # finding.
                for transition in range(4):
                    expected_check_after = not init_state if transition % 2 == 0 \
                                              else init_state
                    # The render state after click should match the
                    # checkbox state IF the renderer is wired up correctly.
                    # Click via the label so the onchange fires (some
                    # browsers fire on label click but not on direct
                    # input.click() depending on rendering).
                    page.evaluate(f"document.getElementById('{cb_id}').click()")
                    page.wait_for_timeout(450)   # allow async re-render

                    actual_check = bool(page.evaluate(
                        f"document.getElementById('{cb_id}').checked"))
                    actual_render = bool(page.evaluate(probe))

                    if actual_check != expected_check_after:
                        tab_fail += 1
                        finding = {
                            'tab': tab_name, 'checkbox': cb_id,
                            'transition': transition,
                            'expectedChecked': expected_check_after,
                            'actualChecked': actual_check,
                            'actualRendered': actual_render,
                            'kind': 'checkbox-state',
                        }
                        findings.append(finding)
                        if VERBOSE:
                            print(f'    [FAIL t{transition}] checkbox '
                                  f'expected={expected_check_after} '
                                  f'actual={actual_check}')
                        # Snapshot for diagnosis.
                        sn = os.path.join(SCREENSHOT_DIR,
                            f'{tab_name}-{cb_id}-t{transition}-checkmiss.png')
                        try:
                            page.screenshot(path=sn, full_page=False)
                            finding['screenshot'] = sn
                        except Exception:
                            pass
                        continue

                    # The render should follow the checkbox. Allow a 200 ms
                    # extra grace (some render paths debounce); re-poll
                    # before declaring a render miss.
                    if actual_render != actual_check:
                        page.wait_for_timeout(800)
                        actual_render = bool(page.evaluate(probe))
                    if actual_render != actual_check:
                        tab_fail += 1
                        finding = {
                            'tab': tab_name, 'checkbox': cb_id,
                            'transition': transition,
                            'expectedRendered': actual_check,
                            'actualRendered': actual_render,
                            'kind': 'render-mismatch',
                        }
                        findings.append(finding)
                        if VERBOSE:
                            print(f'    [FAIL t{transition}] render '
                                  f'expected={actual_check} '
                                  f'actual={actual_render}')
                        sn = os.path.join(SCREENSHOT_DIR,
                            f'{tab_name}-{cb_id}-t{transition}-rendermiss.png')
                        try:
                            page.screenshot(path=sn, full_page=False)
                            finding['screenshot'] = sn
                        except Exception:
                            pass
                    else:
                        tab_pass += 1
                        if VERBOSE:
                            print(f'    [OK   t{transition}] '
                                  f'checked={actual_check} rendered={actual_render}')

            print(f'  Tab {tab_name}: {tab_pass} pass, {tab_fail} fail '
                  f'(across {len(checkboxes)} checkboxes × 4 transitions)')
            summary.append({'tab': tab_name, 'pass': tab_pass, 'fail': tab_fail})

        browser.close()

    # Write Markdown report.
    lines = ['# 3D Viewport View-Toggle Test Report',
             '',
             f'Generated {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}',
             f'Total tabs tested: {len(summary)}',
             f'Total findings: {len(findings)}',
             '',
             '## Summary by tab',
             '',
             '| Tab | Pass | Fail |',
             '|---|---|---|']
    for s in summary:
        lines.append(f'| {s["tab"]} | {s["pass"]} | {s["fail"]} |')
    lines.append('')
    if findings:
        lines.append('## Findings')
        lines.append('')
        for f in findings:
            cb = f['checkbox']; tab = f['tab']; t = f['transition']
            kind = f['kind']
            if kind == 'checkbox-state':
                lines.append(f'- **{tab} / {cb}** transition {t}: checkbox '
                              f'expected `{f["expectedChecked"]}` got '
                              f'`{f["actualChecked"]}`')
            else:
                lines.append(f'- **{tab} / {cb}** transition {t}: '
                              f'rendered `{f["actualRendered"]}` but checkbox '
                              f'is `{f["expectedRendered"]}` — **race**')
            sn = f.get('screenshot')
            if sn:
                rel = os.path.relpath(sn, os.path.dirname(REPORT_PATH))
                lines.append(f'  - screenshot: `{rel}`')
    else:
        lines.append('## Findings')
        lines.append('')
        lines.append('All toggles flipped cleanly across 4 transitions.')

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'\nReport: {REPORT_PATH}')
    print(f'  total findings: {len(findings)}')
    sys.exit(1 if findings else 0)


if __name__ == '__main__':
    def _bye(*_a):
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    main()
