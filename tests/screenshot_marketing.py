#!/usr/bin/env python3
"""screenshot_marketing.py — capture the 5 marketing-page screenshots for
issues #572 (dmx-artnet), #573 (dmx-monitor), #574 (groups), #575
(community + profile editor).

Outputs land directly in ``server/slyled/`` so the existing ``<img>`` tags
in ``server/slyled/{dmx-artnet,dmx-monitor,groups,community}/index.html``
resolve without further changes.

Usage:
    python tests/screenshot_marketing.py        # capture all 5
    python tests/screenshot_marketing.py -v     # verbose

Requires: pip install flask playwright (and ``playwright install chromium``).
"""

import os, sys, time, threading, signal

PROJ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(PROJ)
sys.path.insert(0, os.path.join('desktop', 'shared'))

OUTDIR = os.path.join(PROJ, 'server', 'slyled')
PORT = 18097
BASE = f'http://127.0.0.1:{PORT}'

# ── Stub community_client BEFORE parent_server imports it ───────────────────
# The Community Browser hits electricrv.ca; offline we just want the modal
# populated with realistic-looking rows so the screenshot reads correctly.

import community_client as _cc

_CANNED = [
    {'slug': 'chauvet-intimidator-spot-475z', 'name': 'Intimidator Spot 475Z',
     'manufacturer': 'Chauvet DJ', 'channel_count': 16, 'downloads': 142},
    {'slug': 'martin-mac-aura-xb', 'name': 'MAC Aura XB',
     'manufacturer': 'Martin', 'channel_count': 22, 'downloads': 98},
    {'slug': 'adj-vizi-beam-rxone', 'name': 'Vizi Beam RXONE',
     'manufacturer': 'ADJ', 'channel_count': 14, 'downloads': 67},
    {'slug': 'eurolite-led-tmh-x25', 'name': 'LED TMH-X25',
     'manufacturer': 'Eurolite', 'channel_count': 18, 'downloads': 41},
    {'slug': 'shehds-mini-moving-head', 'name': 'Mini Moving Head 60W',
     'manufacturer': 'Shehds', 'channel_count': 11, 'downloads': 215},
]
_cc.popular = lambda limit=20: {'ok': True,
    'data': {'profiles': _CANNED[:limit]}}
_cc.recent = lambda limit=20: {'ok': True,
    'data': {'profiles': list(reversed(_CANNED))[:limit]}}
_cc.stats = lambda: {'ok': True,
    'data': {'total': 1284, 'downloads_30d': 8420}}
_cc.search = lambda query='', category=None, limit=50, offset=0: {'ok': True,
    'data': {'profiles': [p for p in _CANNED if query.lower() in p['name'].lower()]}}

# ── Server ──────────────────────────────────────────────────────────────────

import parent_server
from parent_server import app

def populate():
    """Seed fixtures, groups, DMX settings, and a custom profile."""
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        c.post('/api/settings', json={'name': 'Marketing Demo', 'darkMode': 1})
        c.post('/api/stage', json={'w': 10.0, 'h': 5.0, 'd': 8.0})

        # Two moving heads + two pars across two universes
        movers = []
        for i, (name, uni, addr) in enumerate([
            ('Beam SL', 1, 1),  ('Beam SR', 1, 17),
            ('Wash SL', 1, 33), ('Wash SR', 1, 41),
            ('Spot Center', 2, 1), ('Spot Audience', 2, 17),
        ]):
            r = c.post('/api/fixtures', json={
                'name': name, 'type': 'point', 'fixtureType': 'dmx',
                'dmxUniverse': uni, 'dmxStartAddr': addr,
                'dmxChannelCount': 16 if 'Beam' in name or 'Spot' in name else 8,
                'dmxProfileId': ('generic-moving-head-16bit'
                                 if 'Beam' in name or 'Spot' in name
                                 else 'generic-rgb'),
            })
            movers.append(r.get_json().get('id'))

        # Two named groups
        c.post('/api/fixtures', json={
            'name': 'Front Beams', 'fixtureType': 'led', 'type': 'group',
            'childIds': movers[:2],
        })
        c.post('/api/fixtures', json={
            'name': 'Wash Pair', 'fixtureType': 'led', 'type': 'group',
            'childIds': movers[2:4],
        })

        # DMX settings — Art-Net with two universe routes
        c.post('/api/dmx/settings', json={
            'protocol': 'artnet',
            'frameRate': 40,
            'bindIp': '127.0.0.1',
            'autoStartEngine': True,
            'bootBlinkFixtures': False,
            'universeRoutes': [
                {'universe': 1, 'destination': '192.168.10.219',
                 'net': 0, 'subnet': 0, 'universeIdx': 0,
                 'label': 'Stage Bridge'},
                {'universe': 2, 'destination': '192.168.10.220',
                 'net': 0, 'subnet': 0, 'universeIdx': 1,
                 'label': 'FOH Bridge'},
            ],
        })

        # Start the engine on loopback so /api/dmx/monitor/<u>/set lands
        c.post('/api/dmx/start')

        # Light some channels so the monitor isn't all zero
        chans1 = [
            {'addr': 1, 'value': 200},  {'addr': 2, 'value': 128},  # pan
            {'addr': 3, 'value': 90},   {'addr': 4, 'value': 64},   # tilt
            {'addr': 5, 'value': 255},                              # dimmer
            {'addr': 7, 'value': 220},  {'addr': 8, 'value': 60},
            {'addr': 17, 'value': 80},  {'addr': 18, 'value': 200}, # SR pan
            {'addr': 19, 'value': 140}, {'addr': 21, 'value': 200},
            {'addr': 33, 'value': 255}, {'addr': 34, 'value': 180},
            {'addr': 35, 'value': 40},                              # wash RGB
            {'addr': 41, 'value': 60},  {'addr': 42, 'value': 220}, {'addr': 43, 'value': 200},
        ]
        c.post('/api/dmx/monitor/1/set', json={'channels': chans1})

        # Add a custom profile so the library shows it (for #575 second img)
        c.post('/api/dmx-profiles', json={
            'id': 'demo-beam-200-16ch',
            'name': 'Beam 200 16ch',
            'manufacturer': 'Demo Co.',
            'category': 'moving-head',
            'colorMode': 'rgb',
            'beamWidth': 8, 'panRange': 540, 'tiltRange': 270,
            'channels': [
                {'offset': 0, 'name': 'Pan', 'type': 'pan',
                 'capabilities': [{'range': [0, 255], 'type': 'Pan',
                                   'label': 'Pan 0-540°'}]},
                {'offset': 1, 'name': 'Pan Fine', 'type': 'panFine',
                 'capabilities': [{'range': [0, 255], 'type': 'PanFine',
                                   'label': '16-bit pan LSB'}]},
                {'offset': 2, 'name': 'Tilt', 'type': 'tilt',
                 'capabilities': [{'range': [0, 255], 'type': 'Tilt',
                                   'label': 'Tilt 0-270°'}]},
                {'offset': 3, 'name': 'Tilt Fine', 'type': 'tiltFine',
                 'capabilities': [{'range': [0, 255], 'type': 'TiltFine',
                                   'label': '16-bit tilt LSB'}]},
                {'offset': 4, 'name': 'Speed', 'type': 'speed',
                 'capabilities': [{'range': [0, 255], 'type': 'PanTiltSpeed',
                                   'label': 'Pan/Tilt speed'}]},
                {'offset': 5, 'name': 'Dimmer', 'type': 'dimmer',
                 'capabilities': [{'range': [0, 255], 'type': 'Intensity',
                                   'label': 'Master dimmer 0-100%'}]},
                {'offset': 6, 'name': 'Strobe', 'type': 'strobe',
                 'capabilities': [
                     {'range': [0, 7], 'type': 'ShutterStrobe', 'label': 'Closed'},
                     {'range': [8, 247], 'type': 'ShutterStrobe',
                      'label': 'Strobe slow→fast'},
                     {'range': [248, 255], 'type': 'ShutterStrobe',
                      'label': 'Open'}]},
                {'offset': 7, 'name': 'Red',   'type': 'red',
                 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity',
                                   'color': 'Red',   'label': 'Red 0-100%'}]},
                {'offset': 8, 'name': 'Green', 'type': 'green',
                 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity',
                                   'color': 'Green', 'label': 'Green 0-100%'}]},
                {'offset': 9, 'name': 'Blue',  'type': 'blue',
                 'capabilities': [{'range': [0, 255], 'type': 'ColorIntensity',
                                   'color': 'Blue',  'label': 'Blue 0-100%'}]},
                {'offset': 10, 'name': 'Colour Wheel', 'type': 'color',
                 'capabilities': [
                     {'range': [0, 9],   'type': 'ColorPreset', 'label': 'White'},
                     {'range': [10, 30], 'type': 'ColorPreset', 'label': 'Red'},
                     {'range': [31, 50], 'type': 'ColorPreset', 'label': 'Green'},
                     {'range': [51, 70], 'type': 'ColorPreset', 'label': 'Blue'},
                     {'range': [71, 90], 'type': 'ColorPreset', 'label': 'Yellow'}]},
                {'offset': 11, 'name': 'Gobo Wheel', 'type': 'gobo',
                 'capabilities': [
                     {'range': [0, 7],   'type': 'WheelSlot', 'label': 'Open'},
                     {'range': [8, 23],  'type': 'WheelSlot', 'label': 'Dots'},
                     {'range': [24, 39], 'type': 'WheelSlot', 'label': 'Triangle'},
                     {'range': [40, 55], 'type': 'WheelSlot', 'label': 'Stars'}]},
                {'offset': 12, 'name': 'Gobo Rotation', 'type': 'goboRotation',
                 'capabilities': [{'range': [0, 255], 'type': 'WheelRotation',
                                   'label': 'CW → CCW'}]},
                {'offset': 13, 'name': 'Prism', 'type': 'prism',
                 'capabilities': [{'range': [0, 255], 'type': 'Prism',
                                   'label': '3-facet prism'}]},
                {'offset': 14, 'name': 'Focus', 'type': 'focus',
                 'capabilities': [{'range': [0, 255], 'type': 'Focus',
                                   'label': 'Near → Far'}]},
                {'offset': 15, 'name': 'Reset', 'type': 'reset',
                 'capabilities': [{'range': [0, 199], 'type': 'NoFunction'},
                                  {'range': [200, 255], 'type': 'Maintenance',
                                   'label': 'Reset all motors'}]},
            ],
        })


def start_server():
    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)
    return t


def capture():
    from playwright.sync_api import sync_playwright

    out = lambda name: os.path.join(OUTDIR, name)
    saved = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900},
                                   color_scheme='dark')
        page = ctx.new_page()
        # SPA polls live endpoints at 1 Hz so 'networkidle' never fires.
        # Use 'load' (DOM + assets) and a short settle for the SPA bootstrap.
        page.goto(BASE, wait_until='load', timeout=15000)
        page.wait_for_timeout(2500)

        # 1. Settings → DMX Engine (#572 — Art-Net + universe routing)
        page.evaluate("showTab('settings'); _setSection('dmx');")
        page.wait_for_timeout(1500)
        page.screenshot(path=out('dmx-artnet-settings.png'), full_page=False)
        saved.append('dmx-artnet-settings.png')

        # 2. DMX Monitor modal (#573 — 512-channel grid with lit channels)
        page.evaluate("showDmxMonitor();")
        page.wait_for_timeout(1500)
        page.screenshot(path=out('dmx-monitor-screenshot.png'), full_page=False)
        saved.append('dmx-monitor-screenshot.png')
        page.evaluate("closeModal();")
        page.wait_for_timeout(300)

        # 3. Group Control modal (#574 — named groups with sliders)
        page.evaluate("showGroupControl();")
        page.wait_for_timeout(1200)
        page.screenshot(path=out('groups-control.png'), full_page=False)
        saved.append('groups-control.png')
        page.evaluate("closeModal();")
        page.wait_for_timeout(300)

        # 4. Community Browser modal (#575 first image)
        # _setSection('profiles') unconditionally calls loadDmxProfiles()
        # which is a stale reference (no longer defined) — stub it first so
        # the section switch doesn't throw, then open the modal directly.
        page.evaluate("window.loadDmxProfiles = window.loadDmxProfiles || function(){};")
        page.evaluate("showTab('settings'); _setSection('profiles');")
        page.wait_for_timeout(500)
        page.evaluate("showCommunityBrowser();")
        page.wait_for_timeout(800)
        # Click Popular so the canned list lands in the table
        page.evaluate("_commPopular();")
        page.wait_for_timeout(1500)
        page.screenshot(path=out('community-browser.png'), full_page=False)
        saved.append('community-browser.png')
        page.evaluate("closeModal();")
        page.wait_for_timeout(300)

        # 5. Profile Library modal (#575 second image — list view of all
        # profiles with channel counts, manufacturer, category. Issue #575
        # accepts either the library list or the editor; the library is the
        # better visual because it actually contains data the user recognises
        # as "fixture profile data".
        page.evaluate("showProfileBrowser();")
        # showProfileBrowser sets display='block' (not 'flex') and renders
        # the table after an async GET /api/dmx-profiles. Wait for the table.
        page.wait_for_function(
            "document.getElementById('prof-tbl') && "
            "document.getElementById('prof-tbl').rows.length > 1",
            timeout=8000,
        )
        page.wait_for_timeout(600)
        page.screenshot(path=out('profile-library.png'), full_page=False)
        saved.append('profile-library.png')

        browser.close()

    return saved


def main():
    populate()
    start_server()
    try:
        saved = capture()
    finally:
        # Stop the DMX engine cleanly so no stray Art-Net packets continue
        try:
            with app.test_client() as c:
                c.post('/api/dmx/stop')
        except Exception:
            pass

    print('\nCaptured:')
    for s in saved:
        full = os.path.join(OUTDIR, s)
        sz = os.path.getsize(full) if os.path.exists(full) else 0
        print(f'  {s:36s}  {sz:>7d} bytes')

    if len(saved) != 5:
        print(f'\nERROR: expected 5 captures, got {len(saved)}')
        sys.exit(1)


if __name__ == '__main__':
    # Trap SIGTERM/SIGINT so any rogue parent_server thread dies with us
    def _bye(*_a):
        try:
            with app.test_client() as c:
                c.post('/api/dmx/stop')
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    main()
