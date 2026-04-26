#!/usr/bin/env python3
"""test_marker_z_alignment.py — #692.

Exercises ``_apply_marker_z_alignment`` and ``POST /api/space/shift``
in three regimes:

  1. Healthy markers — small spread, applies the simple median offset.
  2. Disagreeing markers — opposite-sign clusters, median ≈ 0. Helper
     should DETECT the disagreement (#692 sanity gate, 200 mm) and
     either fall back to a RANSAC floor solve or refuse outright.
  3. Plane-aware filter — when an obstacle sits over a marker's XY,
     the previous code grabbed obstacle z-values and flipped the sign
     at small radii. Filter restricts to ±100 mm of local floor.
  4. Manual shift endpoint as the operator-accessible escape hatch.
"""
import os, sys
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
from parent_server import app, _apply_marker_z_alignment


def _seed_floor_markers():
    """Seed the in-memory ArUco registry with five floor-level markers
    matching the basement-rig trace from the issue body."""
    parent_server._aruco_markers[:] = [
        {"id": 0, "x": 500.0,  "y": 2280.0, "z": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.0},
        {"id": 1, "x": 2050.0, "y": 3170.0, "z": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.0},
        {"id": 2, "x": 1150.0, "y": 2100.0, "z": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.0},
        {"id": 4, "x": 500.0,  "y": 3500.0, "z": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.0},
        {"id": 5, "x": 3120.0, "y": 3090.0, "z": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.0},
    ]


def _floor_cloud_at(z, n=200, jitter=15.0, x_range=(0, 4000), y_range=(0, 4000)):
    """Build a synthetic 'floor' point cloud at z mm with small jitter."""
    import random
    random.seed(42)
    pts = []
    for _ in range(n):
        x = random.uniform(*x_range)
        y = random.uniform(*y_range)
        pz = z + random.uniform(-jitter, jitter)
        pts.append([x, y, pz, 128, 128, 128])
    return pts


# ── Scenario 1: healthy markers, modest offset ──────────────────────────

section('Healthy markers — simple median path')

_seed_floor_markers()
# Cloud 250 mm above z=0 (ZoeDepth's typical bias).
cloud = {"points": _floor_cloud_at(z=250.0, n=400)}
result = _apply_marker_z_alignment(cloud, radius_mm=400, min_pts=3)
ok(result["applied"] is True, f'applied (got {result})')
ok(result.get("method") == "marker-median",
   f'method=marker-median (got {result.get("method")})')
ok(abs(result["zOffsetMm"] - 250.0) < 25.0,
   f'zOffsetMm ≈ 250 (got {result["zOffsetMm"]})')
# Cloud was shifted in place.
post_median = sorted(p[2] for p in cloud["points"])[len(cloud["points"]) // 2]
ok(abs(post_median) < 25.0,
   f'cloud now centred near z=0 (median {post_median:.1f})')

# ── Scenario 2: disagreeing markers — falls back to RANSAC floor ───────

section('Marker disagreement — RANSAC fallback (#692)')

_seed_floor_markers()
# Synthesize the basement-rig pattern: half the floor at z=-435 (cameras
# disagree on tilt), half at z=+433. Median of offsets ≈ 0 even though
# the RANSAC plane sits well below z=0.
import random
random.seed(43)
pts = []
# West side: half-cloud at -435
for _ in range(800):
    x = random.uniform(0, 2000)
    y = random.uniform(0, 4000)
    z = -435.0 + random.uniform(-15, 15)
    pts.append([x, y, z, 128, 128, 128])
# East side: half-cloud at +433
for _ in range(800):
    x = random.uniform(2000, 4000)
    y = random.uniform(0, 4000)
    z = 433.0 + random.uniform(-15, 15)
    pts.append([x, y, z, 128, 128, 128])
cloud = {"points": pts}
result = _apply_marker_z_alignment(cloud, radius_mm=400, min_pts=3)

# Two acceptable outcomes:
#   (a) RANSAC fallback fired and applied a non-zero offset, OR
#   (b) RANSAC unavailable / refused — applied=False with the warning.
# In both cases the helper MUST flag the disagreement (no silent zero).
ok(result.get("markerSpreadMm", 0) > 200,
   f'detected marker spread > 200 mm (got {result.get("markerSpreadMm")})')
ok("warnings" in result and any("tilt" in w for w in result["warnings"]),
   f'warning surfaced (got {result.get("warnings")})')
if result.get("applied"):
    ok(result.get("method") == "ransac-floor-fallback",
       f'applied via RANSAC fallback (got method={result.get("method")})')
    ok(abs(result["zOffsetMm"]) > 50,
       f'fallback offset is non-zero (got {result["zOffsetMm"]})')
else:
    ok(result.get("reason") and "manual shift" in result["reason"],
       f'refusal points to manual shift (got {result.get("reason")!r})')

# Critical regression: never silently apply ≈ 0 with the cancelling
# median. Either the helper must apply the RANSAC offset, or it must
# return applied:False with a warning.
applied_zero = (result.get("applied") is True
                and result.get("method") == "marker-median"
                and abs(result.get("zOffsetMm", 1)) < 5)
ok(not applied_zero,
   '#692 regression — must NOT silently apply ~0 cancelling median')

# ── Scenario 3: plane-aware band filter rejects obstacle pollution ─────

section('Plane-aware band filter — obstacle pollution rejected')

_seed_floor_markers()
# Floor cloud at z=0 ± 15. Add an obstacle (chair-height bump) sitting
# directly over marker id=0 at z=+800. Without the band filter, the
# obstacle's z values dominate the per-marker median (small radius).
pts = _floor_cloud_at(z=0.0, n=400)
import random
random.seed(44)
# Obstacle: 50 dense points at (500, 2280, +800) — over marker 0
for _ in range(50):
    x = 500.0 + random.uniform(-50, 50)
    y = 2280.0 + random.uniform(-50, 50)
    z = 800.0 + random.uniform(-25, 25)
    pts.append([x, y, z, 200, 200, 200])
cloud = {"points": pts}
result = _apply_marker_z_alignment(cloud, radius_mm=200, min_pts=3)
m0 = next((m for m in result.get("markers", []) if m["id"] == 0), None)
ok(m0 is not None, 'marker 0 reported')
if m0:
    # The plane-aware filter should have killed the obstacle band.
    # medianZ should be near 0 (floor), not near +800 (obstacle).
    ok(abs(m0.get("medianZ", 999)) < 100,
       f'obstacle pollution filtered (m0.medianZ={m0.get("medianZ")} should ≈ 0)')
    ok(m0.get("planarPoints") is not None,
       f'planarPoints reported in diagnostic (got {m0})')

# ── Scenario 4: POST /api/space/shift manual escape hatch ──────────────

section('POST /api/space/shift — manual escape hatch')

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    parent_server._point_cloud = {
        "points": _floor_cloud_at(z=400.0, n=200),
    }
    rv = c.post('/api/space/shift', json={'dz': -400.0})
    j = rv.get_json()
    ok(rv.status_code == 200 and j.get('ok') is True,
       f'200 ok=True (got {rv.status_code} {j})')
    ok(abs(j['dz'] - (-400.0)) < 0.01, f'dz echoes (got {j.get("dz")})')
    ok(abs(j['cumulativeOffsetMm'] - (-400.0)) < 0.01,
       f'cumulative offset reflects shift (got {j.get("cumulativeOffsetMm")})')
    pts = parent_server._point_cloud["points"]
    post_median = sorted(p[2] for p in pts)[len(pts) // 2]
    ok(abs(post_median) < 25.0,
       f'cloud centred at z=0 after shift (median {post_median:.1f})')
    ok(parent_server._point_cloud.get("markerAlignment", {}).get("method")
       == "manual-shift",
       f'markerAlignment.method tagged manual-shift')

    # Validation: missing dz → 400.
    rv = c.post('/api/space/shift', json={})
    ok(rv.status_code == 400, f'missing dz → 400 (got {rv.status_code})')

    # Validation: out-of-range → 400.
    rv = c.post('/api/space/shift', json={'dz': 99999})
    ok(rv.status_code == 400, f'huge dz → 400 (got {rv.status_code})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
