#!/usr/bin/env python3
"""
test_structured_light.py — Unit tests for #236: Beam-as-structured-light.

Tests accumulate_beam_hits() and refine_surface_model() with synthetic
sweep data and surface geometry.

Usage:
    python tests/test_structured_light.py        # run all
    python tests/test_structured_light.py -v     # verbose
"""

import sys, os, math

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join('desktop', 'shared'))

from structured_light import accumulate_beam_hits, refine_surface_model

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


# ── refine_surface_model ─────────────────────────────────────────────────

section('refine_surface_model (#236)')

# Floor at Z=0, beam contacts say floor is actually at Z=25
surfaces = {
    "floor": {"z": 0.0, "normal": [0, 0, 1], "d": 0,
              "inliers": 100, "extent": {"xMin": 0, "xMax": 6000, "yMin": 0, "yMax": 4000}},
    "walls": [
        {"normal": [0, 1, 0], "d": 0, "extent": {"xMin": 0, "xMax": 6000}},
    ],
    "obstacles": [],
}

contacts = []
for i in range(10):
    contacts.append({
        "point": [1000 + i * 200, 1000 + i * 100, 25.0],
        "predicted": [1000 + i * 200, 1000 + i * 100, 0.0],
        "surface": "floor",
        "distance": 2000,
        "confidence": 0.8,
        "pan": 0.3 + i * 0.02,
        "tilt": 0.5,
    })

result = refine_surface_model(surfaces, contacts, min_contacts=5)
ok(result["applied"] >= 1, f'At least 1 correction applied: {result["applied"]}')
ok(len(result["corrections"]) >= 1, 'Corrections list non-empty')
if result["corrections"]:
    corr = result["corrections"][0]
    ok(corr["type"] == "floor_height", f'Correction type: {corr["type"]}')
    ok(abs(corr["new_z"] - 25.0) < 5, f'New floor Z ≈ 25: {corr["new_z"]}')

# Too few contacts — no corrections
result2 = refine_surface_model(surfaces, contacts[:2], min_contacts=5)
ok(result2["applied"] == 0, 'Too few contacts → no corrections')

# No contacts
result3 = refine_surface_model(surfaces, [], min_contacts=5)
ok(result3["applied"] == 0, 'Empty contacts → no corrections')

# Wall boundary extension
wall_contacts = []
for i in range(8):
    wall_contacts.append({
        "point": [500 + i * 500, 100, 0],
        "predicted": [500 + i * 500, 0, 0],
        "surface": "wall",
        "distance": 1000,
        "confidence": 0.6,
        "pan": 0.2 + i * 0.05,
        "tilt": 0.5,
    })

surfaces2 = {
    "floor": {"z": 0.0, "normal": [0, 0, 1], "d": 0, "inliers": 50,
              "extent": {"xMin": 1000, "xMax": 3000}},
    "walls": [{"normal": [0, 1, 0], "d": 0, "extent": {"xMin": 1000, "xMax": 3000}}],
    "obstacles": [],
}
result4 = refine_surface_model(surfaces2, wall_contacts, min_contacts=3)
ok(result4 is not None, 'Wall boundary refinement returns result')

# ── Import checks ────────────────────────────────────────────────────────

section('Import Checks (#236)')

ok(callable(accumulate_beam_hits), 'accumulate_beam_hits is callable')
ok(callable(refine_surface_model), 'refine_surface_model is callable')

# ── Summary ──────────────────────────────────────────────────────────────

print(f'\n{"=" * 60}')
print(f'  {_pass} passed, {_fail} failed out of {_pass + _fail} tests')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"=" * 60}')

sys.exit(1 if _fail else 0)
