"""Debug: dashboard canvas persistence on tab return."""
import subprocess, time, requests, sys, os
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
proc = subprocess.Popen([sys.executable, 'desktop/shared/parent_server.py', '--no-browser', '--port', '5571'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(5)
requests.post('http://localhost:5571/api/stage', json={'w': 6, 'h': 3, 'd': 4})
r = requests.post('http://localhost:5571/api/fixtures', json={
    'name': 'MH', 'fixtureType': 'dmx', 'dmxUniverse': 1, 'dmxStartAddr': 1, 'dmxChannelCount': 13})
fid = r.json()['id']
lay = requests.get('http://localhost:5571/api/layout').json()
lay['children'] = [{'id': fid, 'x': 3000, 'y': 2000, 'z': 2800}]
requests.post('http://localhost:5571/api/layout', json=lay)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.goto('http://localhost:5571')
    page.wait_for_timeout(2000)

    page.click('#n-layout'); page.wait_for_timeout(3000)
    print('Layout: canvas in stage3d:', page.evaluate('() => !!document.querySelector("#stage3d canvas")'))

    page.click('#n-dash'); page.wait_for_timeout(5000)
    r1 = page.evaluate('() => ({canvas: !!document.querySelector("#dash-3d canvas"), parent: window._s3d&&window._s3d.renderer?window._s3d.renderer.domElement.parentElement.id:"?"})')
    print('Dash 1st:', r1)

    page.click('#n-runtime'); page.wait_for_timeout(3000)
    r2 = page.evaluate('() => ({canvas: !!document.querySelector("#emu-3d canvas"), parent: window._s3d&&window._s3d.renderer?window._s3d.renderer.domElement.parentElement.id:"?"})')
    print('Runtime:', r2)

    page.click('#n-dash'); page.wait_for_timeout(5000)
    r3 = page.evaluate('''() => {
        var e = window._emu3d || {};
        return {
            canvas: !!document.querySelector("#dash-3d canvas"),
            parent: window._s3d&&window._s3d.renderer?window._s3d.renderer.domElement.parentElement.id:"?",
            dashExists: !!document.getElementById("dash-3d"),
            hasCam: !!e.camera, hasCtrl: !!e.controls, inited: e.inited,
            activeTab: e.activeTab, container: e.activeContainer
        };
    }''')
    print('Dash 2nd:', r3)

    browser.close()
proc.kill()
