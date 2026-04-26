#!/usr/bin/env python3
"""test_camera_sensor_refresh.py — camera-fixture live-sync.

When a USB camera on a node gets swapped (different model, different
FOV, different resolution), the operator clicks Refresh All on the
Setup tab. The SPA then calls GET /api/cameras, which probes each
registered camera node and reconciles per-sensor descriptors back onto
the fixture record.

The original sync only copied `customName` to `fixture.name`, leaving
fovDeg / resolutionW / resolutionH stale — a hardware swap would not
change anything visible to the operator.

After the fix, the live `cameras[idx]` object is reconciled into the
fixture for: name (customName → name → unchanged), fovDeg, resolutionW
(resW), resolutionH (resH), device, flip, plus a new `hwDescriptor`
field carrying the device-string regardless of customName.
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


import parent_server
from parent_server import app

# Stub _probe_camera to avoid hitting the live network. Test injects the
# sensor list via this stub and verifies the fixture record updates.
_probe_payload = {}

def _stub_probe(ip, timeout=2):
    return _probe_payload.get(ip)

parent_server._probe_camera = _stub_probe

# Reset before each scenario.
def _reset():
    with app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

# ── Scenario 1: register a camera, then swap hardware on the node ───────

section('Hardware swap reflects on next /api/cameras call')

_reset()
_probe_payload['10.0.0.50'] = {
    'hostname': 'CamNode',
    'fwVersion': '1.6.1',
    'cameras': [{
        'customName': 'Stage Right',
        'name': 'Logitech C920',
        'device': '/dev/video0',
        'fovDeg': 78,
        'resW': 1920, 'resH': 1080,
        'flip': 'none',
    }],
}

with app.test_client() as c:
    rv = c.post('/api/cameras', json={'ip': '10.0.0.50'})
    ok(rv.status_code == 201, f'register → 201 (got {rv.status_code})')
    fid = rv.get_json()['id']

    # First fetch — descriptor should land on the fixture.
    rv = c.get('/api/cameras')
    cam = next((x for x in rv.get_json() if x['id'] == fid), None)
    ok(cam is not None, 'fixture present')
    ok(cam['fovDeg'] == 78, f'fovDeg=78 from probe (got {cam.get("fovDeg")})')
    ok(cam['resolutionW'] == 1920, f'resolutionW=1920 (got {cam.get("resolutionW")})')
    ok(cam['resolutionH'] == 1080, f'resolutionH=1080 (got {cam.get("resolutionH")})')
    ok(cam['name'] == 'Stage Right', f'name=customName (got {cam.get("name")!r})')
    ok(cam.get('hwDescriptor') == 'Logitech C920',
       f'hwDescriptor=Logitech C920 (got {cam.get("hwDescriptor")!r})')

    # Operator swaps the camera — same node, same /dev/video0 slot,
    # different hardware. customName preserved from previous sensor.
    _probe_payload['10.0.0.50']['cameras'][0] = {
        'customName': 'Stage Right',
        'name': 'EMEET SmartCam Nova 4K',
        'device': '/dev/video0',
        'fovDeg': 90,
        'resW': 3840, 'resH': 2160,
        'flip': 'none',
    }

    rv = c.get('/api/cameras')
    cam = next((x for x in rv.get_json() if x['id'] == fid), None)
    ok(cam['fovDeg'] == 90, f'fovDeg picked up new 90 (got {cam.get("fovDeg")})')
    ok(cam['resolutionW'] == 3840, f'resolutionW picked up 3840 (got {cam.get("resolutionW")})')
    ok(cam['resolutionH'] == 2160, f'resolutionH picked up 2160 (got {cam.get("resolutionH")})')
    ok(cam.get('hwDescriptor') == 'EMEET SmartCam Nova 4K',
       f'hwDescriptor flipped to EMEET (got {cam.get("hwDescriptor")!r})')
    # Name kept (customName unchanged).
    ok(cam['name'] == 'Stage Right', f'name persists when customName unchanged')

# ── Scenario 2: customName edited on the node — propagates ─────────────

section('customName edit on node propagates')

_reset()
_probe_payload['10.0.0.51'] = {
    'hostname': 'CamNode2',
    'fwVersion': '1.6.1',
    'cameras': [{
        'customName': 'Front Right',
        'name': 'EMEET',
        'device': '/dev/video0',
        'fovDeg': 90,
        'resW': 1920, 'resH': 1080,
    }],
}
with app.test_client() as c:
    rv = c.post('/api/cameras', json={'ip': '10.0.0.51'})
    fid = rv.get_json()['id']
    # Operator renames on node /config page.
    _probe_payload['10.0.0.51']['cameras'][0]['customName'] = 'Audience'
    rv = c.get('/api/cameras')
    cam = next((x for x in rv.get_json() if x['id'] == fid), None)
    ok(cam['name'] == 'Audience', f'name picked up customName edit (got {cam.get("name")!r})')

# ── Scenario 3: descriptor falls back to name when no customName ───────

section('Falls back to device name when customName empty')

_reset()
_probe_payload['10.0.0.52'] = {
    'cameras': [{
        'customName': '',
        'name': 'Razer Kiyo',
        'device': '/dev/video0',
        'fovDeg': 81, 'resW': 1280, 'resH': 720,
    }],
}
with app.test_client() as c:
    rv = c.post('/api/cameras', json={'ip': '10.0.0.52'})
    fid = rv.get_json()['id']
    rv = c.get('/api/cameras')
    cam = next((x for x in rv.get_json() if x['id'] == fid), None)
    ok(cam['name'] == 'Razer Kiyo',
       f'name falls back to device descriptor (got {cam.get("name")!r})')

# ── Scenario 4: changes persist on disk (round-trip via _save) ─────────

section('Sync persists fixture record')

_reset()
_probe_payload['10.0.0.53'] = {
    'cameras': [{'customName': 'A', 'name': 'CamA',
                 'device': '/dev/video0', 'fovDeg': 60,
                 'resW': 640, 'resH': 480}],
}
with app.test_client() as c:
    rv = c.post('/api/cameras', json={'ip': '10.0.0.53'})
    fid = rv.get_json()['id']
    # Swap.
    _probe_payload['10.0.0.53']['cameras'][0] = {
        'customName': 'A', 'name': 'CamB',
        'device': '/dev/video0', 'fovDeg': 100,
        'resW': 1920, 'resH': 1080,
    }
    c.get('/api/cameras')
    # Verify the in-memory fixture record was updated, not just the
    # response payload.
    fix = next(f for f in parent_server._fixtures if f['id'] == fid)
    ok(fix['fovDeg'] == 100, f'in-memory fovDeg=100 (got {fix["fovDeg"]})')
    ok(fix['resolutionW'] == 1920, f'in-memory resW=1920')
    ok(fix.get('hwDescriptor') == 'CamB', f'in-memory hwDescriptor=CamB')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
