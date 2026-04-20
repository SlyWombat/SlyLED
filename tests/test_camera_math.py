"""
test_camera_math.py — Regression tests for the canonical camera rotation
helper (#586) and the space_mapper / stereo_engine callers.
"""

import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import numpy as np
from camera_math import build_camera_to_stage, rotation_from_layout


_PASS = 0
_FAIL = 0


def ok(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {label}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def approx(a, b, tol=1e-6):
    return abs(a - b) < tol


# ═══════════════════════════════════════════════════════════════════════
print("=== camera_math.build_camera_to_stage — sign conventions ===")

# 1. Identity rotation → frame swap only
R = build_camera_to_stage(0, 0, 0)
# pinhole +Z (forward) should map to stage +Y (depth)
v = np.array(R) @ np.array([0, 0, 1.0])
ok("Identity: pinhole +Z → stage +Y", approx(v[0], 0, 1e-9) and approx(v[1], 1, 1e-9) and approx(v[2], 0, 1e-9),
   f"got {v.tolist()}")
# pinhole +X (right) should map to stage +X (stage-left)
v = np.array(R) @ np.array([1.0, 0, 0])
ok("Identity: pinhole +X → stage +X", approx(v[0], 1, 1e-9) and approx(v[1], 0, 1e-9) and approx(v[2], 0, 1e-9),
   f"got {v.tolist()}")
# pinhole +Y (down) should map to stage -Z (below floor)
v = np.array(R) @ np.array([0, 1.0, 0])
ok("Identity: pinhole +Y → stage -Z", approx(v[0], 0, 1e-9) and approx(v[1], 0, 1e-9) and approx(v[2], -1, 1e-9),
   f"got {v.tolist()}")


# 2. Tilt positive → aim down
R = build_camera_to_stage(30, 0, 0)
v = np.array(R) @ np.array([0, 0, 1.0])  # pinhole forward
ok("Tilt +30°: forward Y = cos(30)", approx(v[1], math.cos(math.radians(30)), 1e-6),
   f"got Y={v[1]}")
ok("Tilt +30°: forward Z = -sin(30) (DOWN)", approx(v[2], -math.sin(math.radians(30)), 1e-6),
   f"got Z={v[2]}")


# 3. Pan positive → aim toward +X (stage-left, matches fixture UI)
R = build_camera_to_stage(0, 20, 0)
v = np.array(R) @ np.array([0, 0, 1.0])
ok("Pan +20°: forward X = sin(20) (toward stage-left)",
   approx(v[0], math.sin(math.radians(20)), 1e-6), f"got X={v[0]}")
ok("Pan +20°: forward Y = cos(20)",
   approx(v[1], math.cos(math.radians(20)), 1e-6), f"got Y={v[1]}")


# 4. Match bake_engine._rotation_to_aim for combined tilt+pan
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))
from bake_engine import _rotation_to_aim
rot = [15, 30, 0]
pos = [1500, 500, 2000]
# _rotation_to_aim returns the aim POINT at distance dist from pos
aim_pt = _rotation_to_aim(rot, pos, dist=1000)
dir_from_bake = [aim_pt[i] - pos[i] for i in range(3)]  # direction vector length=1000
# build_camera_to_stage applied to pinhole +Z (forward) scaled by 1000 should match
R = np.array(build_camera_to_stage(rot[0], rot[1], rot[2]))
dir_from_math = (R @ np.array([0, 0, 1000.0])).tolist()
ok("Match bake_engine: dx",
   approx(dir_from_bake[0], dir_from_math[0], 1e-3),
   f"bake={dir_from_bake[0]:.3f} math={dir_from_math[0]:.3f}")
ok("Match bake_engine: dy",
   approx(dir_from_bake[1], dir_from_math[1], 1e-3),
   f"bake={dir_from_bake[1]:.3f} math={dir_from_math[1]:.3f}")
ok("Match bake_engine: dz",
   approx(dir_from_bake[2], dir_from_math[2], 1e-3),
   f"bake={dir_from_bake[2]:.3f} math={dir_from_math[2]:.3f}")


# 5. space_mapper and stereo_engine produce the SAME rotation matrix
#    for the same input (the #586 acceptance criterion).
print("\n=== space_mapper vs stereo_engine rotation consistency ===")

from space_mapper import transform_points
from stereo_engine import StereoEngine

# Use a point at cam-local (0, 0, 1000) — pure forward, so the result
# reveals just the rotation * frame-swap applied to forward.
pts = [[0, 0, 1000, 0, 0, 0]]
cam_pos = (0, 0, 0)

for rot in ([0, 0, 0], [15, 0, 0], [0, 30, 0], [15, 30, 0], [15, 30, 10]):
    # space_mapper path
    sm_result = transform_points(pts, cam_pos, rot)
    sm_pt = sm_result[0]

    # stereo_engine path — register a camera, reconstruct a pinhole ray,
    # multiply by 1000 and add cam_pos to get a comparable stage point
    se = StereoEngine()
    se.add_camera_from_fov("c", 60, 1920, 1080, cam_pos, rot)
    R_se = se._cameras["c"]["R"]
    se_pt = (R_se @ np.array([0, 0, 1000.0])).tolist()

    for axis, label in enumerate(("X", "Y", "Z")):
        ok(f"rot={rot} axis={label}: space_mapper == stereo_engine",
           approx(sm_pt[axis], se_pt[axis], 0.01),
           f"sm={sm_pt[axis]:.4f} se={se_pt[axis]:.4f}")


# ═══════════════════════════════════════════════════════════════════════
print("\n=== #587 7-element point migration ===")
# ═══════════════════════════════════════════════════════════════════════

from camera_math import point_confidence, point_coords, POINT_SCHEMA_VERSION

ok("Schema version = 2", POINT_SCHEMA_VERSION == 2)

# Confidence helper: v1 six-element point returns 1.0 (fully trusted)
ok("v1 6-element point: confidence = 1.0",
   point_confidence([0, 0, 0, 255, 0, 0]) == 1.0)
# v2 seven-element point returns the stored confidence
ok("v2 7-element point: confidence = 0.75",
   abs(point_confidence([0, 0, 0, 255, 0, 0, 0.75]) - 0.75) < 1e-9)
# Clamp above 1 or below 0
ok("Confidence clamped to 1.0",
   point_confidence([0, 0, 0, 0, 0, 0, 5.0]) == 1.0)
ok("Confidence clamped to 0.0",
   point_confidence([0, 0, 0, 0, 0, 0, -1.0]) == 0.0)
# None slot falls back to 1.0
ok("None confidence falls back to 1.0",
   point_confidence([0, 0, 0, 0, 0, 0, None]) == 1.0)

# Coords helper
x, y, z = point_coords([100, 200, 300, 0, 0, 0, 0.5])
ok("point_coords ignores trailing slots",
   x == 100.0 and y == 200.0 and z == 300.0)

# transform_points preserves slot 6 when present
from space_mapper import transform_points
pts_v2 = [[0, 0, 1000, 255, 0, 0, 0.42]]
result = transform_points(pts_v2, (0, 0, 0), [0, 0, 0])
ok("transform_points: v2 input produces v2 output (7 slots)",
   len(result[0]) == 7, f"got {len(result[0])} slots")
ok("transform_points: v2 confidence preserved",
   abs(result[0][6] - 0.42) < 1e-9, f"got {result[0][6]}")

# v1 input produces v1 output (no fabricated slot)
pts_v1 = [[0, 0, 1000, 255, 0, 0]]
result = transform_points(pts_v1, (0, 0, 0), [0, 0, 0])
ok("transform_points: v1 input produces v1 output (6 slots)",
   len(result[0]) == 6, f"got {len(result[0])} slots")


# ═══════════════════════════════════════════════════════════════════════
print("\n=== #581 anchor_depth_scale ===")
# ═══════════════════════════════════════════════════════════════════════

import random
from space_mapper import anchor_depth_scale, apply_depth_correction

# Build a synthetic cloud: camera at ceiling on back wall, pitched down.
# Stage is 3m×4m×2m. Generate cam-local points whose rays terminate at
# known stage surfaces, so `t_true` equals their cam-local Z exactly.
cam_pos = (1500, 0, 2500)
cam_rot = [20, 0, 0]  # tilt down 20°
stage = {"w": 3000, "d": 4000, "h": 2000}

# Cam-local points on a grid: each point's (x, y, z) is a ray direction
# times a random depth; we then snap each to the nearest ray-surface
# intersection so `t_true == z` by construction. Use build_camera_to_stage
# to derive the intersection.
R = np.array(build_camera_to_stage(cam_rot[0], cam_rot[1], cam_rot[2]))
rng = random.Random(42)
synthetic = []
for px in range(-30, 31, 6):
    for py in range(-20, 21, 6):
        # Normalised pinhole ray direction: (px/100, py/100, 1)
        dc = np.array([px / 100.0, py / 100.0, 1.0])
        ds = R @ dc
        # Find nearest stage-bounding surface along this ray
        cands = []
        if ds[2] < -1e-6: cands.append(-cam_pos[2] / ds[2])
        if ds[2] > 1e-6:  cands.append((stage["h"] - cam_pos[2]) / ds[2])
        if ds[1] > 1e-6:  cands.append((stage["d"] - cam_pos[1]) / ds[1])
        if ds[1] < -1e-6: cands.append(-cam_pos[1] / ds[1])
        if ds[0] > 1e-6:  cands.append((stage["w"] - cam_pos[0]) / ds[0])
        if ds[0] < -1e-6: cands.append(-cam_pos[0] / ds[0])
        cands = [t for t in cands if t > 0]
        if not cands:
            continue
        t = min(cands)
        # The cam-local point at ray parameter t is (t*px/100, t*py/100, t)
        # since dir_cam has z=1. So cam-local (x, y, z) = t * dc.
        synthetic.append([t * dc[0], t * dc[1], t * dc[2], 128, 128, 128])

ok(f"Synthetic cloud has enough points ({len(synthetic)} ≥ 50)",
   len(synthetic) >= 50)

# Sanity: running anchor on the correct cloud should give scale≈1, offset≈0.
fit = anchor_depth_scale(synthetic, cam_pos, cam_rot, stage)
ok("Identity cloud: fit recovers scale ≈ 1",
   fit is not None and approx(fit["scale"], 1.0, 1e-3),
   f"got {fit['scale'] if fit else None}")
ok("Identity cloud: fit recovers offset ≈ 0",
   fit is not None and approx(fit["offset"], 0.0, 5.0),
   f"got {fit['offset'] if fit else None} mm")
ok("Identity cloud: RMS error near 0",
   fit is not None and fit["rmsErrorMm"] < 5.0,
   f"rms={fit['rmsErrorMm'] if fit else None}")

# Fuzz with a known scale+offset — verify recovery.
S_true = 0.7    # monocular underestimates depth by 30%
B_true = 300.0  # constant bias of 300 mm
fuzzed = []
for p in synthetic:
    x, y, z, r, g, b = p
    # raw depth = S_true * true_depth + B_true; x, y scale with z
    z_raw = S_true * z + B_true
    k = z_raw / z
    fuzzed.append([x * k, y * k, z_raw, r, g, b])

fit2 = anchor_depth_scale(fuzzed, cam_pos, cam_rot, stage)
# Expected recovery: S_recovered = 1/S_true, B_recovered = -B_true/S_true
expected_S = 1.0 / S_true
expected_B = -B_true / S_true
ok(f"Fuzzed cloud: recovers scale ({fit2['scale']:.4f} vs expected {expected_S:.4f})",
   fit2 is not None and abs(fit2["scale"] / expected_S - 1) < 0.01,
   f"err {(fit2['scale']/expected_S - 1)*100:.2f}%")
ok(f"Fuzzed cloud: recovers offset ({fit2['offset']:.1f} vs expected {expected_B:.1f})",
   fit2 is not None and abs(fit2["offset"] - expected_B) < 20.0,
   f"err {fit2['offset'] - expected_B:.1f} mm")

# apply_depth_correction on fuzzed cloud should undo the fuzz.
corrected = apply_depth_correction(fuzzed, fit2["scale"], fit2["offset"])
# Each corrected point should be within a few mm of the original synthetic
worst = 0.0
for a, b in zip(corrected, synthetic):
    dx = a[0] - b[0]; dy = a[1] - b[1]; dz = a[2] - b[2]
    err = math.sqrt(dx*dx + dy*dy + dz*dz)
    if err > worst: worst = err
ok(f"Correction round-trips to within 10 mm (worst {worst:.1f} mm)",
   worst < 10.0)


# ═══════════════════════════════════════════════════════════════════════
print("\n=== #582 cross-camera consistency filter ===")
# ═══════════════════════════════════════════════════════════════════════

from stereo_consistency import cross_camera_filter

# Two cameras looking at the same stage floor from different angles.
# Synth: cam A at (830, 120, 1930) pitched 22°; cam B at (1275, 120, 1930)
# pitched 15°. Place a real floor "truth" point at stage (1000, 2000, 0).
# Give both cameras a cloud that includes this true point + some noise.
#
# Cam A cloud also contains a HALLUCINATION at stage (0, 3500, 2500)
# that is in B's FOV but B does not have a matching point → should be
# dropped.

truth_point = [1000, 2000, 0, 255, 255, 255]
noise_a = [1100, 2100, 50, 200, 200, 200]
noise_b = [900, 1950, -30, 180, 180, 180]
hallucination = [0, 3500, 2500, 128, 128, 128]

per_cam = [
    {
        "fixture": {"id": 12, "name": "A", "rotation": [22, 0, 0], "fovDeg": 90},
        "stage_pos": (830, 120, 1930),
        "fov_deg": 90,
        "points": [truth_point, noise_a, hallucination],
    },
    {
        "fixture": {"id": 13, "name": "B", "rotation": [15, 0, 0], "fovDeg": 90},
        "stage_pos": (1275, 120, 1930),
        "fov_deg": 90,
        "points": [truth_point, noise_b],
    },
]

merged, stats = cross_camera_filter(per_cam, tolerance_mm=200)

ok("Filter returns stats per camera", len(stats) == 2)
# Camera A: truth_point confirmed, noise_a confirmed (near B's noise_b
# within 200mm), hallucination dropped. 2 confirmed, 0 single, 1 dropped.
a = stats[0]
ok(f"Cam A: 2 confirmed (got {a['confirmed']})", a["confirmed"] == 2)
ok(f"Cam A: 1 dropped hallucination (got {a['dropped']})", a["dropped"] == 1)
# Camera B: both truth + noise_b should be confirmed (cam A has nearby points)
b = stats[1]
ok(f"Cam B: 2 confirmed (got {b['confirmed']})", b["confirmed"] == 2)
ok(f"Cam B: 0 dropped (got {b['dropped']})", b["dropped"] == 0)

# Every kept point should carry a confidence slot (7 elements)
ok("All kept points have 7 slots (v2 schema)",
   all(len(p) == 7 for p in merged))
# Confirmed points should have confidence ≈ 0.85 (default)
confirmed_confs = [p[6] for p in merged if abs(p[6] - 0.85) < 0.01]
ok(f"At least 4 points at confidence 0.85 (cross-confirmed): got {len(confirmed_confs)}",
   len(confirmed_confs) >= 4)

# Point OUTSIDE the other camera's FOV should be kept as singleCam.
# Place a point very far off to the side that only cam A can see.
out_of_fov = [-500, 1000, 0, 255, 0, 0]
per_cam_single = [
    {
        "fixture": {"id": 12, "name": "A", "rotation": [22, 0, 0], "fovDeg": 60},
        "stage_pos": (830, 120, 1930),
        "fov_deg": 60,
        "points": [out_of_fov],
    },
    {
        "fixture": {"id": 13, "name": "B", "rotation": [15, 0, 0], "fovDeg": 60},
        "stage_pos": (1275, 120, 1930),
        "fov_deg": 60,
        "points": [],  # B has no matching points
    },
]
merged2, stats2 = cross_camera_filter(per_cam_single, tolerance_mm=200)
# The point lies outside cam B's FOV cone → should pass as singleCam.
if merged2:
    ok("Single-cam point survives with lower confidence",
       any(abs(p[6] - 0.4) < 0.01 for p in merged2),
       f"confs={[round(p[6],2) for p in merged2]}")


print(f"\n{_PASS} passed, {_FAIL} failed out of {_PASS + _FAIL} tests")
sys.exit(0 if _FAIL == 0 else 1)
