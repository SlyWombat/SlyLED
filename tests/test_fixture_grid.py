"""Test fixture monitor grid on Dashboard — live status display (#303)."""
import subprocess, time, requests, sys, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
PORT = 5561
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py',
                         '--no-browser', '--port', str(PORT)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
BASE = f'http://localhost:{PORT}'
try:
    requests.get(BASE + '/api/settings', timeout=5)
    print('Server up on port', PORT)
except Exception:
    print('FAIL — server did not start'); proc.kill(); sys.exit(1)

# Seed test data: 1 DMX fixture + 1 LED fixture (with child)
r = requests.post(BASE + '/api/children', json={
    'ip': '10.0.0.80', 'hostname': 'GRID-TEST', 'name': 'LED Strip',
    'sc': 1, 'strings': [{'leds': 30, 'mm': 1000}]})
cid = r.json()['id']
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'LED Strip', 'fixtureType': 'led', 'childId': cid,
    'type': 'linear', 'strings': [{'leds': 30, 'mm': 1000, 'sdir': 0}]})
led_fid = r.json()['id']
r = requests.post(BASE + '/api/fixtures', json={
    'name': 'Moving Head 1', 'fixtureType': 'dmx',
    'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 8})
dmx_fid = r.json()['id']
# Place fixtures on layout
lay = requests.get(BASE + '/api/layout').json()
lay['children'] = [
    {'id': led_fid, 'x': 1000, 'y': 1000},
    {'id': dmx_fid, 'x': 2000, 'y': 1000},
]
requests.post(BASE + '/api/layout', json=lay)

# Start Art-Net engine so DMX values read back
requests.post(BASE + '/api/dmx/start', json={'protocol': 'artnet'})

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
    page.wait_for_timeout(3000)

    # --- Dashboard: fixture monitor grid exists ---
    print('\n--- Fixture Monitor Grid ---')
    grid = page.query_selector('#dash-live-grid')
    check('Grid container exists', grid is not None)

    # Wait for the grid to populate (1s refresh timer)
    page.wait_for_timeout(2000)
    cards = page.query_selector_all('.flx-card')
    check('Grid has fixture cards', len(cards) >= 2)
    check('Grid has exactly 2 cards (LED + DMX)', len(cards) == 2)

    # Check card structure
    first_card = cards[0] if cards else None
    if first_card:
        has_name = first_card.query_selector('.flx-name') is not None
        has_swatch = first_card.query_selector('.flx-swatch') is not None
        has_dimbar = first_card.query_selector('.flx-dim-bar') is not None
        has_effect = first_card.query_selector('.flx-effect') is not None
        has_badge = first_card.query_selector('.flx-badge') is not None
        check('Card has name', has_name)
        check('Card has color swatch', has_swatch)
        check('Card has dimmer bar', has_dimbar)
        check('Card has effect label', has_effect)
        check('Card has online badge', has_badge)
    else:
        for name in ['name', 'swatch', 'dimbar', 'effect', 'badge']:
            check(f'Card has {name}', False)

    # Check initial state (idle, black)
    swatch_bg = page.evaluate('''() => {
        var sw = document.querySelector('.flx-swatch');
        return sw ? getComputedStyle(sw).backgroundColor : '';
    }''')
    check('Initial swatch is black/dark', 'rgb(0, 0, 0)' in swatch_bg)

    effect_text = page.evaluate('''() => {
        var eff = document.querySelector('.flx-effect');
        return eff ? eff.textContent : '';
    }''')
    check('Initial effect is Idle', effect_text == 'Idle')

    # --- Set DMX channels and verify grid updates ---
    print('\n--- DMX Channel Update ---')
    requests.post(BASE + '/api/dmx/monitor/1/set', json={'channels': [
        {'addr': 1, 'value': 200},
        {'addr': 2, 'value': 100},
        {'addr': 3, 'value': 50},
    ]})
    # Wait for grid to refresh (1s interval)
    page.wait_for_timeout(2000)

    # Find the DMX fixture card
    dmx_swatch_bg = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        if (!card) return '';
        var sw = card.querySelector('.flx-swatch');
        return sw ? getComputedStyle(sw).backgroundColor : '';
    }''', dmx_fid)
    check('DMX swatch shows color', dmx_swatch_bg != 'rgb(0, 0, 0)' and dmx_swatch_bg != '')
    check('DMX swatch has red component', '200' in dmx_swatch_bg)

    # Check active class
    dmx_active = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        return card ? card.classList.contains('flx-active') : false;
    }''', dmx_fid)
    check('DMX card has active class when lit', dmx_active)

    # Check dimmer bar has width > 0
    dim_width = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        if (!card) return '0%';
        var fill = card.querySelector('.flx-dim-fill');
        return fill ? fill.style.width : '0%';
    }''', dmx_fid)
    check('DMX dimmer bar shows intensity', dim_width != '0%')

    # Check DMX address label
    dmx_addr = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        if (!card) return '';
        var addr = card.querySelector('.flx-dmx-addr');
        return addr ? addr.textContent : '';
    }''', dmx_fid)
    check('DMX address label shows U1.1', 'U1.1' in dmx_addr)

    # --- Clear DMX and verify grid updates back to idle ---
    print('\n--- DMX Clear ---')
    requests.post(BASE + '/api/dmx/monitor/1/set', json={'channels': [
        {'addr': 1, 'value': 0},
        {'addr': 2, 'value': 0},
        {'addr': 3, 'value': 0},
    ]})
    page.wait_for_timeout(2000)
    dmx_swatch_after = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        if (!card) return '';
        var sw = card.querySelector('.flx-swatch');
        return sw ? getComputedStyle(sw).backgroundColor : '';
    }''', dmx_fid)
    check('DMX swatch returns to black', 'rgb(0, 0, 0)' in dmx_swatch_after)

    dmx_active_after = page.evaluate('''(fid) => {
        var card = document.getElementById('flx-' + fid);
        return card ? card.classList.contains('flx-active') : true;
    }''', dmx_fid)
    check('DMX card loses active class', not dmx_active_after)

    # --- Label shows fixture count ---
    print('\n--- Label ---')
    label_text = page.evaluate('() => document.getElementById("dash-live-label").textContent')
    check('Label shows fixture count', '2' in label_text)

    # --- Tab switch preserves grid ---
    print('\n--- Tab Switch ---')
    page.click('#n-setup')
    page.wait_for_timeout(500)
    page.click('#n-dash')
    page.wait_for_timeout(3000)
    cards_after = page.query_selector_all('.flx-card')
    check('Grid survives tab switch', len(cards_after) >= 2)

    # Screenshot
    page.screenshot(path='tests/user/dash_fixture_grid.png')
    print('  Screenshot: tests/user/dash_fixture_grid.png')

    # --- No JS errors ---
    real_errs = [e for e in errs if '400' not in e and '404' not in e
                 and 'favicon' not in e.lower()]
    check('No JS errors', len(real_errs) == 0)
    if real_errs:
        for e in real_errs[:5]: print('    ERR:', e)

    print(f'\n{passed} passed, {failed} failed out of {passed + failed} tests')
    browser.close()

# Cleanup
requests.post(BASE + '/api/reset', headers={'X-SlyLED-Confirm': 'true'})
proc.kill()
sys.exit(1 if failed else 0)
