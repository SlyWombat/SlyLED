#!/usr/bin/env python3
"""
test_project_spatial.py — Integration tests for #336: Spatial data in project export.

Tests that point cloud, calibration, and light map data survives
export/import round-trip, including gzip compression.

Usage:
    python tests/test_project_spatial.py        # run all
    python tests/test_project_spatial.py -v     # verbose
"""

import sys, os, json, gzip, base64

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))

# ── Test infrastructure ──────────────────────────────────────────────────

_pass = 0
_fail = 0
_errors = []
_verbose = '-v' in sys.argv


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
        if _verbose:
            print(f'  \033[32m[PASS]\033[0m {name}')
    else:
        _fail += 1
        _errors.append(name)
        print(f'  \033[31m[FAIL]\033[0m {name}')


def section(name):
    print(f'\n\033[1m── {name} ──\033[0m')


# ── Setup ────────────────────────────────────────────────────────────────

section('Setup')

import parent_server
from parent_server import app, _compress_cloud, _decompress_cloud

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    ok(True, 'Server reset')

# ── Gzip round-trip ──────────────────────────────────────────────────────

section('Gzip Compression (#336)')

test_cloud = {
    "points": [[100, 200, 0, 255, 0, 0], [300, 400, 0, 0, 255, 0],
               [500, 600, 10, 0, 0, 255]],
    "totalPoints": 3,
    "floorNormalized": True,
    "floorOffset": 5.0,
}

compressed = _compress_cloud(test_cloud)
ok(compressed is not None, 'Compression returns result')
ok(compressed["points"]["compressed"] is True, 'Points marked as compressed')
ok(isinstance(compressed["points"]["data"], str), 'Data is base64 string')
ok(compressed.get("totalPoints") == 3, 'Non-point fields preserved')

decompressed = _decompress_cloud(compressed)
ok(decompressed is not None, 'Decompression returns result')
ok(len(decompressed["points"]) == 3, f'3 points after decompress: {len(decompressed["points"])}')
ok(decompressed["points"][0] == [100, 200, 0, 255, 0, 0], 'First point matches')
ok(decompressed["points"][2] == [500, 600, 10, 0, 0, 255], 'Last point matches')
ok(decompressed.get("floorNormalized") is True, 'Metadata preserved')

# ── Schema version ───────────────────────────────────────────────────────

section('Schema Version (#336)')

ok(parent_server.PROJECT_SCHEMA_VERSION == 2, f'Schema version = 2 (got {parent_server.PROJECT_SCHEMA_VERSION})')

# ── Export with point cloud ──────────────────────────────────────────────

section('Export with Point Cloud (#336)')

# Inject point cloud and mover calibration with light map
parent_server._point_cloud = test_cloud
parent_server._mover_cal["42"] = {
    "samples": [(0.5, 0.5, 320, 240)],
    "grid": None,
    "lightMap": {"samples": [{"pan": 0.5, "tilt": 0.5, "stageX": 3000, "stageY": 2000, "stageZ": 0}],
                 "panSteps": 5, "tiltSteps": 5},
}

with app.test_client() as c:
    r = c.get('/api/project/export')
    ok(r.status_code == 200, 'Export returns 200')
    bundle = r.get_json()
    ok(bundle.get("schemaVersion") == 2, 'Exported schema version = 2')
    ok(bundle.get("pointCloud") is not None, 'Point cloud included in export')
    ok(bundle["pointCloud"]["points"]["compressed"] is True, 'Point cloud is compressed')
    ok(bundle.get("lightMaps") is not None, 'Light maps included in export')
    ok("42" in bundle["lightMaps"], 'Light map for fixture 42 present')
    lm = bundle["lightMaps"]["42"]
    ok(lm.get("panSteps") == 5, 'Light map panSteps preserved')

# ── Import round-trip ────────────────────────────────────────────────────

section('Import Round-Trip (#336)')

with app.test_client() as c:
    # Clear everything (reset clears fixtures/actions/etc; cloud cleared separately via /api/space)
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.delete('/api/space')  # explicitly clear point cloud
    ok(parent_server._point_cloud is None, 'Cloud cleared after reset + space clear')

    # Import the bundle
    r = c.post('/api/project/import', json=bundle,
               content_type='application/json')
    d = r.get_json()
    ok(d.get('ok') is True, f'Import returns ok: {d}')

    # Verify point cloud restored
    ok(parent_server._point_cloud is not None, 'Point cloud restored after import')
    if parent_server._point_cloud:
        pts = parent_server._point_cloud.get("points", [])
        ok(len(pts) == 3, f'3 points restored: {len(pts)}')
        ok(pts[0] == [100, 200, 0, 255, 0, 0], 'First point matches after round-trip')

    # Verify light map restored
    cal = parent_server._mover_cal.get("42", {})
    ok(cal.get("lightMap") is not None, 'Light map restored')
    if cal.get("lightMap"):
        ok(cal["lightMap"].get("panSteps") == 5, 'Light map panSteps survived import')

# ── Backward compatibility (v1 import) ───────────────────────────────────

section('Backward Compatibility (#336)')

v1_project = {
    "type": "slyled-project",
    "schemaVersion": 1,
    "appVersion": "1.0.0",
    "savedAt": "2026-04-01T00:00:00Z",
    "name": "V1 Project",
    "stage": {"w": 6.0, "h": 3.0, "d": 4.0},
    "children": [],
    "fixtures": [],
    "layout": {"canvasW": 3000, "canvasH": 2000, "children": []},
    "actions": [],
    "spatialEffects": [],
    "timelines": [],
    "objects": [],
    "dmxSettings": {},
    "calibrations": {},
    "rangeCalibrations": {},
    "moverCalibrations": {},
    "cameraSsh": {},
    "showPlaylist": {"order": [], "loopAll": False},
    "settings": {"name": "V1 Project"},
}

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    r = c.post('/api/project/import', json=v1_project)
    ok(r.get_json().get('ok') is True, 'V1 project imports successfully')
    ok(r.get_json().get('name') == 'V1 Project', 'V1 project name preserved')

# Future version rejection
v3_project = dict(v1_project)
v3_project["schemaVersion"] = 3

with app.test_client() as c:
    r = c.post('/api/project/import', json=v3_project)
    ok(r.status_code == 400, 'V3 project rejected with 400')
    ok('version' in r.get_json().get('err', '').lower(), 'Error mentions version')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
