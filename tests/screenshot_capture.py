#!/usr/bin/env python3
"""
screenshot_capture.py — Automated screenshot capture for SlyLED user manual.

Populates test data via Flask test client, starts the server, uses Playwright
to capture all SPA tabs, modals, and key workflows. Optionally captures
Android screens via adb and child config pages from live devices.

Usage:
    python tests/screenshot_capture.py                       # SPA only
    python tests/screenshot_capture.py --android             # + Android via adb
    python tests/screenshot_capture.py --child 192.168.10.x  # + child device

Output: docs/screenshots/*.png
"""

import sys, os, json, time, threading, argparse, subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PROJ = os.path.join(os.path.dirname(__file__), '..')
OUTDIR = os.path.join(PROJ, 'docs', 'screenshots')
os.makedirs(OUTDIR, exist_ok=True)

PORT = 18080  # avoid conflicts with running servers
BASE = f'http://127.0.0.1:{PORT}'

captured = []
skipped = []


def out(name):
    return os.path.join(OUTDIR, name)


# ── 1. Populate test data via Flask test client ─────────────────────────────

def populate_data():
    """Create realistic test data for screenshots."""
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Clean up custom profiles from previous test runs
        r = c.get('/api/dmx-profiles')
        for p in (r.get_json() or []):
            if not p.get('builtin'):
                c.delete(f'/api/dmx-profiles/{p["id"]}')

        # Settings
        c.post('/api/settings', json={'name': 'Main Stage', 'darkMode': 1,
                                       'canvasW': 10000, 'canvasH': 5000})

        # Stage
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 10.0})

        # LED children + fixtures — realistic venue names
        r = c.post('/api/children', json={'ip': '192.168.10.50'})
        cid1 = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'FOH Truss Left', 'type': 'linear', 'fixtureType': 'led', 'childId': cid1,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 0}]
        })
        fix1 = r.get_json().get('id')

        r = c.post('/api/children', json={'ip': '192.168.10.51'})
        cid2 = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': 'FOH Truss Right', 'type': 'linear', 'fixtureType': 'led', 'childId': cid2,
            'strings': [{'leds': 60, 'mm': 3000, 'sdir': 2}]
        })
        fix2 = r.get_json().get('id')

        # DMX moving heads — realistic product names
        r = c.post('/api/fixtures', json={
            'name': 'Beam 200 Stage Left', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'rotation': [-20, 0, 0]
        })
        dmx1 = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'Beam 200 Stage Right', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 17, 'dmxChannelCount': 16,
            'dmxProfileId': 'generic-moving-head-16bit',
            'rotation': [-20, 0, 0]
        })
        dmx2 = r.get_json().get('id')

        # RGB Par — realistic name
        r = c.post('/api/fixtures', json={
            'name': 'SlimPAR Center Wash', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 33, 'dmxChannelCount': 3,
            'dmxProfileId': 'generic-rgb',
            'rotation': [0, 0, 0]
        })
        dmx3 = r.get_json().get('id')

        # Layout positions (cameras added below after camera fixture creation)

        # Actions library
        actions = [
            {'name': 'Warm Solid', 'type': 1, 'r': 255, 'g': 160, 'b': 40},
            {'name': 'Cool Fade', 'type': 2, 'r': 0, 'g': 100, 'b': 255, 'r2': 100, 'g2': 0, 'b2': 200, 'speedMs': 3000},
            {'name': 'Rainbow Chase', 'type': 4, 'r': 255, 'g': 0, 'b': 0, 'speedMs': 40, 'spacing': 5, 'tailLen': 3},
            {'name': 'Ocean Rainbow', 'type': 5, 'r': 0, 'g': 100, 'b': 200, 'speedMs': 60, 'paletteId': 1},
            {'name': 'Campfire', 'type': 6, 'r': 255, 'g': 80, 'b': 0, 'cooling': 50, 'sparking': 100},
            {'name': 'Blue Comet', 'type': 7, 'r': 0, 'g': 80, 'b': 255, 'speedMs': 30, 'tailLen': 8},
            {'name': 'Starlight', 'type': 8, 'r': 200, 'g': 200, 'b': 255, 'spawnMs': 80, 'density': 4},
            {'name': 'Flash Strobe', 'type': 9, 'r': 255, 'g': 255, 'b': 255, 'periodMs': 100},
        ]
        for a in actions:
            c.post('/api/actions', json=a)

        # Spatial effects
        c.post('/api/spatial-effects', json={
            'name': 'Blue Sweep', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 0, 'g': 80, 'b': 220, 'size': {'radius': 2000},
            'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                       'durationS': 8, 'easing': 'ease-in-out'},
            'blend': 'add'
        })
        c.post('/api/spatial-effects', json={
            'name': 'Golden Wash', 'category': 'spatial-field', 'shape': 'plane',
            'r': 255, 'g': 180, 'b': 40, 'size': {'normal': [0, 1, 0], 'thickness': 1500},
            'motion': {'startPos': [5000, 5000, 5000], 'endPos': [5000, 0, 5000],
                       'durationS': 12, 'easing': 'ease-out'},
            'blend': 'screen'
        })

        # Install a moving-head preset show
        r = c.post('/api/show/preset', json={'id': 'spotlight-sweep'})
        tl_id = r.get_json().get('timelineId')

        # Camera fixtures — for calibration screenshots (#329, #330)
        r = c.post('/api/fixtures', json={
            'name': 'Stage Left Cam', 'type': 'point', 'fixtureType': 'camera',
            'fovDeg': 90, 'resolutionW': 1920, 'resolutionH': 1080,
            'trackClasses': ['person', 'chair', 'backpack'],
            'trackFps': 3, 'trackThreshold': 0.35, 'trackTtl': 8, 'trackReidMm': 600
        })
        cam1 = r.get_json().get('id')
        c.put(f'/api/fixtures/{cam1}', json={
            'cameraIp': '192.168.10.235', 'cameraIdx': 0
        })

        r = c.post('/api/fixtures', json={
            'name': 'Stage Right Cam', 'type': 'point', 'fixtureType': 'camera',
            'fovDeg': 60, 'resolutionW': 1920, 'resolutionH': 1080
        })
        cam2 = r.get_json().get('id')
        c.put(f'/api/fixtures/{cam2}', json={
            'cameraIp': '192.168.10.109', 'cameraIdx': 0
        })

        # Place cameras in layout — mounted high on side walls
        c.post('/api/layout', json={'children': [
            {'id': fix1, 'x': 1000, 'y': 4500, 'z': 0},
            {'id': fix2, 'x': 9000, 'y': 4500, 'z': 0},
            {'id': dmx1, 'x': 2000, 'y': 5000, 'z': 2000},
            {'id': dmx2, 'x': 8000, 'y': 5000, 'z': 2000},
            {'id': dmx3, 'x': 5000, 'y': 4800, 'z': 5000},
            {'id': cam1, 'x': 500, 'y': 0, 'z': 2500},
            {'id': cam2, 'x': 9500, 'y': 0, 'z': 2500},
        ]})

        # Synthetic point cloud data — simulates a scanned venue
        import parent_server as _ps
        _ps._point_cloud = {
            'points': [[x * 100, y * 100, 0, 128, 128, 128]
                       for x in range(60) for y in range(40)],
            'totalPoints': 2400,
            'floorNormalized': True,
            'floorOffset': 0,
            'surfaces': {
                'floor': {'z': 0, 'normal': [0, 0, 1], 'inliers': 1800},
                'walls': [{'normal': [0, 1, 0], 'd': 0}],
                'obstacles': [],
            },
        }

        # Objects — proper transform format
        c.post('/api/objects', json={
            'name': 'Back Wall', 'objectType': 'wall', 'color': '#1e293b', 'opacity': 30,
            'transform': {'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [10000, 5000, 100]}
        })

    print(f'  Data populated: 7 fixtures (5+2 cameras), 8 actions, 2 effects, 1 preset show')
    return tl_id


# ── 2. Start Flask server in background ──────────────────────────────────────

def start_server():
    """Start the Flask server in a background thread."""
    import parent_server
    from parent_server import app

    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)  # wait for server to start
    print(f'  Server running at {BASE}')
    return t


# ── 3. Playwright SPA capture ────────────────────────────────────────────────

def capture_spa():
    """Capture all SPA tabs and modals using Playwright."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800},
                                   color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='networkidle', timeout=15000)
        time.sleep(1)  # let SPA initialize

        def snap(name, delay=0.5):
            time.sleep(delay)
            path = out(name)
            page.screenshot(path=path, full_page=False)
            captured.append(name)
            print(f'    [{len(captured):2d}] {name}')

        print('  Capturing SPA tabs...')

        # Dashboard
        page.evaluate("showTab('dash')")
        snap('spa-dashboard.png', 1.5)

        # Setup
        page.evaluate("showTab('setup')")
        snap('spa-setup.png', 1.5)

        # Setup modals
        page.evaluate("showAddFixtureModal()")
        snap('spa-setup-add-led.png', 0.5)

        page.evaluate("document.getElementById('aft').value='dmx';_toggleAddFixFields()")
        snap('spa-setup-add-dmx.png', 1.0)

        page.evaluate("closeModal()")

        # Edit DMX fixture (find first DMX fixture)
        try:
            page.evaluate("""
                var dmxFix = _fixtures.find(f => f.fixtureType === 'dmx');
                if (dmxFix) editFixture(dmxFix.id);
            """)
            snap('spa-setup-edit-dmx.png', 0.5)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('spa-setup-edit-dmx.png')

        # Edit camera fixture — shows tracking configuration (#391, #392)
        try:
            page.evaluate("""
                var camFix = _fixtures.find(f => f.fixtureType === 'camera');
                if (camFix) editFixture(camFix.id);
            """)
            snap('spa-setup-edit-camera.png', 0.5)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('spa-setup-edit-camera.png')

        # Layout — Front view (orthographic, closest to old 2D canvas)
        page.evaluate("showTab('layout')")
        time.sleep(1)
        try:
            page.evaluate("setView('front')")
            snap('spa-layout-2d.png', 2.0)
        except Exception as e:
            print(f'    Front view capture failed: {e}')
            skipped.append('spa-layout-2d.png')

        # Layout — 3D perspective view
        try:
            page.evaluate("setView('3d')")
            snap('spa-layout-3d.png', 2.5)
        except Exception as e:
            print(f'    3D capture failed: {e}')
            skipped.append('spa-layout-3d.png')

        # Actions
        page.evaluate("showTab('actions')")
        snap('spa-actions.png', 1.0)

        # Runtime — show emulator canvas with fixtures
        page.evaluate("showTab('runtime')")
        snap('spa-runtime.png', 1.5)

        # Runtime — load show modal
        try:
            page.evaluate("openLoadShowModal()")
            snap('spa-runtime-load-show.png', 1.0)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('spa-runtime-load-show.png')

        # Settings
        page.evaluate("showTab('settings')")
        snap('spa-settings.png', 1.0)

        # Profile browser
        try:
            page.evaluate("showProfileBrowser()")
            snap('spa-settings-profiles.png', 1.0)

            page.evaluate("viewProfile('generic-moving-head-16bit')")
            snap('spa-settings-profile-view.png', 0.5)

            page.evaluate("closeModal(); setTimeout(showProfileEditor, 300)")
            snap('spa-settings-profile-editor.png', 1.0)

            page.evaluate("closeModal(); setTimeout(showOflImport, 300)")
            snap('spa-settings-ofl-import.png', 1.0)

            page.evaluate("closeModal()")
        except Exception as e:
            skipped.append(f'profile modals: {e}')

        # Firmware
        page.evaluate("showTab('firmware')")
        snap('spa-firmware.png', 1.0)

        # Settings > Cameras sub-tab — calibration overview
        try:
            page.evaluate("showTab('settings')")
            time.sleep(0.5)
            page.evaluate("document.querySelector('#sn-cameras')?.click()")
            snap('spa-settings-cameras.png', 1.5)
        except Exception:
            skipped.append('spa-settings-cameras.png')

        # CV Engine status
        try:
            page.evaluate("showTab('settings')")
            time.sleep(0.5)
            snap('spa-cv-status.png', 0.5)
        except Exception:
            skipped.append('spa-cv-status.png')

        # Workflow: test channels
        try:
            page.evaluate("showTab('setup')")
            time.sleep(1)
            page.evaluate("""
                var dmxFix = _fixtures.find(f => f.fixtureType === 'dmx');
                if (dmxFix) editFixture(dmxFix.id);
            """)
            snap('workflow-test-channels.png', 0.5)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('workflow-test-channels.png')

        # Workflow: profile capabilities
        try:
            page.evaluate("showTab('settings')")
            time.sleep(0.5)
            page.evaluate("""
                showProfileEditor();
                setTimeout(function(){ _peEditCaps(0); }, 500);
            """)
            snap('workflow-profile-caps.png', 1.5)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('workflow-profile-caps.png')

        browser.close()

    print(f'  SPA: {len(captured)} captured')


# ── 4. Child device capture ──────────────────────────────────────────────────

def capture_child(child_ip):
    """Capture child config pages from a live device."""
    from playwright.sync_api import sync_playwright

    print(f'  Capturing child at {child_ip}...')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 800, 'height': 600},
                                   color_scheme='dark')
        page = ctx.new_page()

        try:
            page.goto(f'http://{child_ip}/config', wait_until='networkidle', timeout=10000)
            time.sleep(1)

            # Detect if LED or DMX based on page content
            content = page.content()
            is_dmx = 'DMX' in content or 'dmx' in content

            prefix = 'child-dmx' if is_dmx else 'child-led'

            # Dashboard tab (first tab, default)
            page.screenshot(path=out(f'{prefix}-dashboard.png'))
            captured.append(f'{prefix}-dashboard.png')

            # Settings tab
            tabs = page.query_selector_all('button, .tab-btn, [onclick*="tab"]')
            for tab in tabs:
                txt = (tab.inner_text() or '').lower()
                if 'settings' in txt or 'setting' in txt:
                    tab.click()
                    time.sleep(0.5)
                    page.screenshot(path=out(f'{prefix}-settings.png'))
                    captured.append(f'{prefix}-settings.png')
                    break

            # Config tab
            for tab in tabs:
                txt = (tab.inner_text() or '').lower()
                if 'config' in txt and 'settings' not in txt:
                    tab.click()
                    time.sleep(0.5)
                    page.screenshot(path=out(f'{prefix}-config.png'))
                    captured.append(f'{prefix}-config.png')
                    break

        except Exception as e:
            print(f'  Child capture failed: {e}')
            skipped.append(f'child-{child_ip}')
        finally:
            browser.close()


# ── 5. Android capture via adb ───────────────────────────────────────────────

def capture_android():
    """Capture Android app screens via adb screencap."""
    print('  Capturing Android via adb...')

    def adb(cmd):
        return subprocess.run(['adb'] + cmd.split(), capture_output=True, text=True, timeout=15)

    # Check device
    r = adb('devices')
    lines = [l for l in r.stdout.strip().split('\n')[1:] if l.strip() and 'device' in l]
    if not lines:
        print('  No Android device/emulator found — skipping')
        skipped.append('android (no device)')
        return

    pkg = 'com.slywombat.slyled'

    # Launch app
    adb(f'shell am start -n {pkg}/.MainActivity')
    time.sleep(3)

    screens = [
        ('android-connection.png', None),  # capture whatever is showing first
    ]

    # Connection screen (app starts here if not connected)
    r = adb('shell screencap -p /sdcard/slyled_ss.png')
    adb('pull /sdcard/slyled_ss.png ' + out('android-connection.png'))
    if os.path.exists(out('android-connection.png')):
        captured.append('android-connection.png')
        print(f'    [{len(captured):2d}] android-connection.png')

    # Try to connect to our test server (input the IP)
    # This requires the device to reach our host — works on emulator with 10.0.2.2
    host_ip = '10.0.2.2'  # Android emulator host loopback
    adb(f'shell input text {host_ip}:{PORT}')
    time.sleep(0.5)
    adb('shell input keyevent 66')  # ENTER
    time.sleep(3)

    # Capture each tab by tapping bottom nav (approximate positions)
    tab_names = ['dashboard', 'setup', 'layout', 'actions', 'runtime', 'settings']
    # Bottom nav positions (6 tabs evenly spaced, assumes 1080px wide screen)
    tab_x_positions = [90, 270, 450, 630, 810, 990]

    for i, name in enumerate(tab_names):
        adb(f'shell input tap {tab_x_positions[i]} 2280')  # bottom nav ~y=2280
        time.sleep(1.5)
        fname = f'android-{name}.png'
        adb('shell screencap -p /sdcard/slyled_ss.png')
        adb('pull /sdcard/slyled_ss.png ' + out(fname))
        if os.path.exists(out(fname)):
            captured.append(fname)
            print(f'    [{len(captured):2d}] {fname}')

    adb('shell rm /sdcard/slyled_ss.png')
    print(f'  Android: done')


# ── 6. Example-specific screenshot capture ──────────────────────────────────

def capture_examples():
    """Capture screenshots for the Examples section of the user manual.

    Sets up a mover-tracking scenario (Example B) and captures:
    - Profile editor, layout 3D, actions, timeline, runtime tracking views
    Also captures calibration-related panels (Examples C & D).
    """
    from playwright.sync_api import sync_playwright
    import shutil

    print('  Setting up example data...')

    # ── Example B data: mover tracking with spatial effects ──
    import parent_server
    from parent_server import app

    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

        # Stage: 6m x 3m x 4m
        c.post('/api/stage', json={'w': 6.0, 'h': 3.0, 'd': 4.0})
        c.post('/api/settings', json={'name': 'Tracking Demo', 'darkMode': 1})

        # Create narrow-beam mover profile
        c.post('/api/dmx-profiles', json={
            'id': 'narrow-mover', 'name': 'Narrow Spot 8deg',
            'beamWidth': 8, 'panRange': 540, 'tiltRange': 270,
            'channels': [
                {'offset': 0, 'name': 'Pan', 'type': 'pan', 'bits': 16},
                {'offset': 2, 'name': 'Tilt', 'type': 'tilt', 'bits': 16},
                {'offset': 4, 'name': 'Dimmer', 'type': 'dimmer'},
                {'offset': 5, 'name': 'Red', 'type': 'red'},
                {'offset': 6, 'name': 'Green', 'type': 'green'},
                {'offset': 7, 'name': 'Blue', 'type': 'blue'},
            ]
        })

        # Two movers
        r = c.post('/api/fixtures', json={
            'name': 'Mover SL', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 8,
            'dmxProfileId': 'narrow-mover', 'rotation': [-30, -15, 0]
        })
        msl = r.get_json().get('id')

        r = c.post('/api/fixtures', json={
            'name': 'Mover SR', 'type': 'point', 'fixtureType': 'dmx',
            'dmxUniverse': 1, 'dmxStartAddr': 14, 'dmxChannelCount': 8,
            'dmxProfileId': 'narrow-mover', 'rotation': [-30, 15, 0]
        })
        msr = r.get_json().get('id')

        # Camera fixtures (for Examples C & D)
        r = c.post('/api/fixtures', json={
            'name': 'Stage Cam', 'type': 'point', 'fixtureType': 'camera',
            'fovDeg': 90, 'resolutionW': 1920, 'resolutionH': 1080
        })
        cam = r.get_json().get('id')
        c.put(f'/api/fixtures/{cam}', json={
            'cameraIp': '192.168.10.235', 'cameraIdx': 0
        })

        # Position in layout
        c.post('/api/layout', json={'children': [
            {'id': msl, 'x': 1500, 'y': 0, 'z': 2800},
            {'id': msr, 'x': 4500, 'y': 0, 'z': 2800},
            {'id': cam, 'x': 3000, 'y': 0, 'z': 2000},
        ]})

        # Spatial effect: green sphere sweep
        r = c.post('/api/spatial-effects', json={
            'name': 'Sweep Green', 'category': 'spatial-field', 'shape': 'sphere',
            'r': 0, 'g': 255, 'b': 0, 'size': {'radius': 800},
            'motion': {'startPos': [1000, 2000, 0], 'endPos': [5000, 2000, 0],
                       'durationS': 8, 'easing': 'linear'},
            'blend': 'replace'
        })
        eff_id = r.get_json().get('id')

        # Timeline with effect
        r = c.post('/api/timelines', json={
            'name': 'Mover Tracking Demo', 'durationS': 20, 'loop': True
        })
        tl_id = r.get_json().get('id')

        # Add track + clip
        c.post(f'/api/timelines/{tl_id}/tracks', json={'target': 'all'})
        tl_data = c.get(f'/api/timelines/{tl_id}').get_json()
        tracks = tl_data.get('tracks', [])
        if tracks:
            track_id = tracks[0].get('id')
            c.post(f'/api/timelines/{tl_id}/tracks/{track_id}/clips', json={
                'effectId': eff_id, 'startS': 0, 'durationS': 8
            })

        # Bake
        c.post(f'/api/timelines/{tl_id}/bake')

        # Start playback
        c.post(f'/api/timelines/{tl_id}/start')

    print('  Example data ready. Capturing with Playwright...')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 800},
                                   color_scheme='dark')
        page = ctx.new_page()
        page.goto(BASE, wait_until='networkidle', timeout=15000)
        time.sleep(1.5)

        def snap(name, delay=0.5):
            time.sleep(delay)
            path = out(name)
            page.screenshot(path=path, full_page=False)
            captured.append(name)
            print(f'    [{len(captured):2d}] {name}')

        # ── Example B screenshots ──

        # Profile editor (Settings → Profiles → view narrow-mover)
        try:
            page.evaluate("showTab('settings')")
            time.sleep(0.5)
            page.evaluate("showProfileBrowser()")
            time.sleep(0.5)
            page.evaluate("viewProfile('narrow-mover')")
            snap('example-b-profile.png', 1.0)
            page.evaluate("closeModal()")
        except Exception as e:
            print(f'    Profile capture failed: {e}')
            skipped.append('example-b-profile.png')

        # Layout 3D with movers on truss
        try:
            page.evaluate("showTab('layout')")
            time.sleep(1)
            page.evaluate("setView('3d')")
            snap('example-b-layout-3d.png', 2.5)
        except Exception as e:
            print(f'    Layout 3D capture failed: {e}')
            skipped.append('example-b-layout-3d.png')

        # Actions tab with spatial effect
        page.evaluate("showTab('actions')")
        snap('example-b-action.png', 1.5)

        # Shows tab with timeline
        page.evaluate("showTab('shows')")
        snap('example-b-timeline.png', 1.5)

        # Runtime tab — tracking T=0
        page.evaluate("showTab('runtime')")
        time.sleep(2)
        snap('example-b-tracking-t0.png', 1.0)

        # Wait and capture T=5
        time.sleep(5)
        snap('example-b-tracking-t5.png', 0.5)

        # Wait and capture T=10
        time.sleep(5)
        snap('example-b-tracking-t10.png', 0.5)

        # ── Example D screenshots — camera calibration panels ──

        # Camera settings panel
        try:
            page.evaluate("showTab('settings')")
            time.sleep(0.5)
            page.evaluate("document.querySelector('#sn-cameras')?.click()")
            snap('example-d-camera-config.png', 1.5)
        except Exception:
            skipped.append('example-d-camera-config.png')

        # ArUco print dialog
        try:
            page.evaluate("_printAruco()")
            snap('example-d-print-markers.png', 1.0)
            page.evaluate("closeModal()")
        except Exception:
            skipped.append('example-d-print-markers.png')

        # ── Example C screenshots — mover calibration panel ──

        # Open mover edit then calibrate panel
        try:
            page.evaluate("showTab('layout')")
            time.sleep(1)
            # Double-click first mover to open edit dialog
            page.evaluate("""
                var mover = _fixtures.find(f => f.dmxProfileId === 'narrow-mover');
                if (mover) editFixture(mover.id);
            """)
            snap('example-c-calibrate-panel.png', 1.0)
            page.evaluate("closeModal()")
        except Exception as e:
            print(f'    Calibrate panel capture failed: {e}')
            skipped.append('example-c-calibrate-panel.png')

        # Stop playback
        try:
            with app.test_client() as c:
                tls = c.get('/api/timelines').get_json() or []
                for tl in tls:
                    c.post(f'/api/timelines/{tl["id"]}/stop')
        except Exception:
            pass

        browser.close()

    # ── Copy regression test tracking screenshots as higher-quality fallbacks ──
    reg_dir = os.path.join(PROJ, 'tests', 'regression')
    for src_name, dst_name in [
        ('tracking_t0.png', 'example-b-tracking-t0.png'),
        ('tracking_t5.png', 'example-b-tracking-t5.png'),
        ('tracking_t10.png', 'example-b-tracking-t10.png'),
    ]:
        src = os.path.join(reg_dir, src_name)
        dst = out(dst_name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f'    Copied regression screenshot: {src_name} → {dst_name}')

    # ── Create placeholder images for calibration screenshots ──
    # These need real hardware; provide labeled placeholders for the manual build
    _create_placeholder('example-c-discovery.png',
                        'Discovery in progress\nCoarse grid scan with camera detecting beam')
    _create_placeholder('example-c-grid-result.png',
                        'Grid calibration complete\nSample count, pan/tilt range, grid density')
    _create_placeholder('example-c-light-map.png',
                        'Light map build in progress\nSystematic sweep with stage coordinate mapping')
    _create_placeholder('example-c-aim-verify.png',
                        'Aim verification\nBeam aimed at target using calibrated light map')
    _create_placeholder('example-d-detection.png',
                        'Camera snapshot with ArUco markers detected\nGreen overlays showing marker IDs')
    _create_placeholder('example-d-result.png',
                        'Calibration complete\nReprojection error and reference point summary')


def _create_placeholder(filename, text):
    """Create a labeled placeholder PNG for screenshots that require hardware."""
    path = out(filename)
    if os.path.exists(path):
        return  # don't overwrite existing real screenshots
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (1280, 800), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype('arial.ttf', 24)
            font_sm = ImageFont.truetype('arial.ttf', 16)
        except OSError:
            font = ImageFont.load_default()
            font_sm = font
        # Title
        draw.text((40, 340), text, fill=(56, 189, 248), font=font)
        # Footer
        draw.text((40, 740), '[Placeholder — replace with live capture]',
                  fill=(100, 116, 139), font=font_sm)
        img.save(path)
        captured.append(filename)
        print(f'    [{len(captured):2d}] {filename} (placeholder)')
    except ImportError:
        # PIL not available — write a minimal 1x1 PNG as fallback
        print(f'    Skipped placeholder {filename} (Pillow not installed)')
        skipped.append(filename)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SlyLED screenshot capture')
    parser.add_argument('--android', action='store_true', help='Capture Android screens via adb')
    parser.add_argument('--child', type=str, help='Capture child config from IP address')
    parser.add_argument('--examples', action='store_true',
                        help='Capture example-specific screenshots (Examples B, C, D)')
    args = parser.parse_args()

    print('SlyLED Screenshot Capture')
    print('=' * 50)

    print('\n1. Populating test data...')
    tl_id = populate_data()

    print('\n2. Starting server...')
    start_server()

    print('\n3. Capturing SPA screenshots...')
    try:
        capture_spa()
    except Exception as e:
        print(f'  SPA capture error: {e}')

    if args.child:
        print(f'\n4. Capturing child device ({args.child})...')
        capture_child(args.child)

    if args.android:
        print(f'\n5. Capturing Android screens...')
        try:
            capture_android()
        except Exception as e:
            print(f'  Android capture error: {e}')

    if args.examples:
        print(f'\n6. Capturing example screenshots...')
        try:
            capture_examples()
        except Exception as e:
            print(f'  Example capture error: {e}')
            import traceback
            traceback.print_exc()

    # Summary
    print(f'\n{"=" * 50}')
    print(f'Captured: {len(captured)} screenshots')
    if skipped:
        print(f'Skipped:  {len(skipped)}')
        for s in skipped:
            print(f'  - {s}')
    print(f'Output:   {OUTDIR}')

    # List files
    for f in sorted(captured):
        size = os.path.getsize(out(f)) if os.path.exists(out(f)) else 0
        print(f'  {f:40s} {size:>8,d} bytes')

    return 0 if captured else 1


if __name__ == '__main__':
    sys.exit(main())
