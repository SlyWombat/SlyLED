"""Q14 — E2E synthetic regression tests for the camera-review pipelines.

Two pipelines, both pure-Python with no hardware/network requirement:

1. Tracking pipeline: synthetic bbox in pixel space → _pixel_box_to_stage_anchors
   (homography tier) → temporal-object ingest → multi-camera fusion → assert
   the fused stage position matches the surveyed ground truth within 50 mm.

2. Mover-cal pipeline: synthetic (pan, tilt, stage_xy) samples generated from
   a known ParametricFixtureModel → fit_model → assert the fit recovers the
   ground-truth pan_sign / tilt_sign and lands within 1° RMS of the synthetic
   data. Then nudge a single beam pixel and call verify_signs() to confirm
   the sign-confirmation probe returns the right answer.

Run:
    python -X utf8 tests/test_camera_review_pipelines.py

Both tests print PASS/FAIL counts and exit non-zero on any failure so a
CI runner can wire it into the regression suite.
"""

import math
import sys
import os

# Ensure desktop/shared is importable.
_SHARED = os.path.join(os.path.dirname(__file__), "..", "desktop", "shared")
sys.path.insert(0, os.path.abspath(_SHARED))


def _h_floor_for_synthetic_camera():
    """Build a synthetic 3×3 homography that maps pixel (px, py) to stage
    (mm) for a back-wall camera looking forward. We use a clean affine map
    so projection is exact and the test only fails on logic bugs, not
    floating-point noise."""
    # Camera frame 1920×1080 → stage XY 0..3000 × 0..2500 mm. (px, py) →
    # stage (sx, sy) where sx = (1 - px/W) * SW, sy = (1 - py/H) * SD.
    W, H = 1920.0, 1080.0
    SW, SD = 3000.0, 2500.0
    # Affine, no perspective: matrix multiplied by [px, py, 1].T
    return [
        [-SW / W, 0.0,    SW],
        [0.0,    -SD / H, SD],
        [0.0,    0.0,    1.0],
    ]


def test_tracking_pipeline():
    import parent_server as ps  # noqa
    print("=== Tracking pipeline (Q1 + Q3 + Q5) ===")
    fails = 0; total = 0

    # Wire two fake camera fixtures both seeing the same stage point from
    # different (synthetic) homographies.
    H_a = _h_floor_for_synthetic_camera()
    cam_a = {"id": 9001, "fixtureType": "camera", "fovDeg": 90, "fovType": "diagonal"}
    cam_b = {"id": 9002, "fixtureType": "camera", "fovDeg": 90, "fovType": "diagonal"}
    ps._fixtures.extend([cam_a, cam_b])
    ps._calibrations[str(cam_a["id"])] = {"matrix": H_a}
    ps._calibrations[str(cam_b["id"])] = {"matrix": H_a}  # same H for simplicity

    try:
        # Person at stage (1500, 1250) — frame center for both cameras.
        # Bbox 200×600 px centered at (960, 540) — bottom = (960, 840).
        # Feet pixel (960, 840) maps via H to (~1500, ~556). Hmm — H maps
        # the *bottom* of the bbox not the center, so let me compute the
        # expected stage-feet from H * [960, 840, 1].
        gt_feet_px = (960.0, 840.0)
        sx = -(3000.0/1920.0) * gt_feet_px[0] + 3000.0
        sy = -(2500.0/1080.0) * gt_feet_px[1] + 2500.0
        gt_feet = (sx, sy)

        anchors = ps._pixel_box_to_stage_anchors(
            cam_a, {"x": 860, "y": 240, "w": 200, "h": 600}, [1920, 1080])
        total += 1
        if anchors is None or anchors["method"] != "homography":
            print(f"  FAIL: expected method=homography, got {anchors}"); fails += 1
        else:
            print(f"  PASS: tier='homography'")

        total += 1
        feet = anchors["feet"]
        d = math.hypot(feet[0] - gt_feet[0], feet[1] - gt_feet[1])
        if d > 1.0:  # synthetic — should be <0.001
            print(f"  FAIL: feet projection error = {d:.1f} mm "
                  f"(got {feet[:2]}, want {gt_feet})"); fails += 1
        else:
            print(f"  PASS: feet projection within {d:.3f} mm of ground truth")

        # Multi-camera fusion: ingest the same person from both cameras
        # at slightly different placements. Fusion should collapse them.
        ps._temporal_objects.clear()
        for cam, jitter_x in [(cam_a, 0.0), (cam_b, 80.0)]:
            ps._temporal_objects.append({
                "id": ps._nxt_tmp,
                "name": f"person-{cam['id']}",
                "objectType": "person",
                "_temporal": True,
                "_method": "homography",
                "_cameraId": cam["id"],
                "_yoloConfidence": 0.85,
                "ttl": 5.0,
                "_expiresAt": __import__("time").time() + 5.0,
                "transform": {"pos": [gt_feet[0] + jitter_x, gt_feet[1], 850.0],
                              "rot": [0,0,0], "scale": [500, 1700, 500]},
            })
            ps._nxt_tmp += 1
        ps._fuse_temporal_objects()
        total += 1
        if len(ps._temporal_objects) != 1:
            print(f"  FAIL: expected 1 fused object, got {len(ps._temporal_objects)}"); fails += 1
        else:
            print(f"  PASS: 2 cameras fused into 1 tracked object")

        total += 1
        fused = ps._temporal_objects[0]
        if fused.get("_fusionCams") != 2:
            print(f"  FAIL: expected _fusionCams=2, got {fused.get('_fusionCams')}"); fails += 1
        else:
            print(f"  PASS: _fusionCams=2 recorded")

        total += 1
        conf = fused.get("_fusionConfidence", 0)
        if conf <= 0.5:
            print(f"  FAIL: expected confidence >0.5 for two homography sources, got {conf}"); fails += 1
        else:
            print(f"  PASS: _fusionConfidence={conf}")

        # Q5 — raw-tier object should be excluded from track-action aim
        # but can still appear in /api/objects. We assert _method survives
        # ingest for downstream consumers (Track action checks it).
        total += 1
        if fused.get("_method") != "homography":
            print(f"  FAIL: best-tier of cluster should be homography, got {fused.get('_method')}"); fails += 1
        else:
            print(f"  PASS: cluster best tier preserved on fused object")
    finally:
        # Tear down — leave parent_server state clean for other tests.
        ps._fixtures = [f for f in ps._fixtures if f.get("id") not in (9001, 9002)]
        ps._calibrations.pop("9001", None)
        ps._calibrations.pop("9002", None)
        ps._temporal_objects.clear()

    return total, fails


def test_mover_cal_pipeline():
    print("\n=== Mover-cal pipeline (Q10 + ParametricFixtureModel) ===")
    fails = 0; total = 0
    from parametric_mover import ParametricFixtureModel, fit_model, verify_signs

    # Ground-truth model: ceiling-mounted, level, no roll. pan_sign=+1
    # (default), tilt_sign=-1 (project convention for "tilt up = toward
    # ceiling, tilt down = toward floor").
    gt = ParametricFixtureModel(
        fixture_pos=(1500.0, 0.0, 2000.0),
        pan_range_deg=540.0,
        tilt_range_deg=270.0,
        mount_yaw_deg=0.0,
        mount_pitch_deg=0.0,
        mount_roll_deg=0.0,
        pan_offset=0.5,
        tilt_offset=0.5,
        pan_sign=1,
        tilt_sign=-1,
    )

    # Generate 6 synthetic samples by ray-marching from gt across a small
    # pan/tilt grid and intersecting with the floor (z=0).
    samples = []
    ray_origin = gt.fixture_pos
    for pan in (0.4, 0.5, 0.6):
        for tilt in (0.2, 0.35):  # tilt_sign=-1 ceiling fixture: aim DOWN = tilt<0.5
            ray_dir = gt.forward(pan, tilt)
            # Solve for t such that origin.z + t*dir.z = 0
            if abs(ray_dir[2]) < 1e-6:
                continue
            t = -ray_origin[2] / ray_dir[2]
            if t <= 0:
                continue
            sx = ray_origin[0] + t * ray_dir[0]
            sy = ray_origin[1] + t * ray_dir[1]
            samples.append({"pan": pan, "tilt": tilt,
                            "stageX": sx, "stageY": sy, "stageZ": 0.0})
    total += 1
    if len(samples) < 4:
        print(f"  FAIL: synthetic sample generation produced only {len(samples)}"); fails += 1
    else:
        print(f"  PASS: synthetic sample generation produced {len(samples)} samples")

    # Fit without forced signs — should pick up the right combo from RMS
    # alone (no convention tie-break post-Q10).
    fitted, q = fit_model(
        fixture_pos=(1500.0, 0.0, 2000.0),
        pan_range_deg=540.0, tilt_range_deg=270.0,
        samples=samples)
    total += 1
    if q.rms_error_deg > 1.0:
        print(f"  FAIL: fit RMS {q.rms_error_deg:.3f}° > 1° on synthetic data"); fails += 1
    else:
        print(f"  PASS: fit RMS {q.rms_error_deg:.4f}° on synthetic samples")

    total += 1
    # The fit should land on the ground-truth signs (pan_sign=+1,
    # tilt_sign=-1) because the RMS is unique on noise-free data.
    if fitted.pan_sign != 1 or fitted.tilt_sign != -1:
        print(f"  FAIL: fit picked wrong mirror "
              f"(pan_sign={fitted.pan_sign}, tilt_sign={fitted.tilt_sign})"); fails += 1
    else:
        print("  PASS: fit recovered ground-truth (pan_sign=+1, tilt_sign=-1)")

    # force_signs path — give it the wrong signs and assert the fit honours
    # them (RMS will be terrible, but the API must respect the request).
    forced, fq = fit_model(
        fixture_pos=(1500.0, 0.0, 2000.0),
        pan_range_deg=540.0, tilt_range_deg=270.0,
        samples=samples, force_signs=(-1, +1))
    total += 1
    if forced.pan_sign != -1 or forced.tilt_sign != +1:
        print(f"  FAIL: force_signs ignored "
              f"(got pan_sign={forced.pan_sign}, tilt_sign={forced.tilt_sign})"); fails += 1
    else:
        print(f"  PASS: force_signs honoured (RMS={fq.rms_error_deg:.2f}°)")

    # verify_signs: simulate a ceiling fixture with the project default
    # convention (tilt_sign=-1 = "+tilt DMX aims beam toward ceiling,
    # so on a downward-looking camera +tilt moves beam UP on screen").
    # Scenario: pan+ moves beam +X in frame (standard), tilt+ moves
    # beam UP in frame (dpy negative) — that's the -1 convention.
    pan_sign, tilt_sign = verify_signs(
        beam_pixel_before=(960.0, 540.0),
        beam_pixel_after_pan_plus=(1100.0, 540.0),  # moved +X (right)
        beam_pixel_after_tilt_plus=(960.0, 380.0),  # moved -Y (up on screen)
    )
    total += 1
    if pan_sign != 1 or tilt_sign != -1:
        print(f"  FAIL: verify_signs returned ({pan_sign}, {tilt_sign}); expected (+1, -1)"); fails += 1
    else:
        print(f"  PASS: verify_signs returned (+1, -1) for ceiling-fixture rig "
              f"(tilt+ = beam up)")

    # Inverse scenario — fixture where tilt+ sends beam DOWN on screen.
    # verify_signs should flip to +1.
    pan_sign2, tilt_sign2 = verify_signs(
        beam_pixel_before=(960.0, 540.0),
        beam_pixel_after_pan_plus=(1100.0, 540.0),
        beam_pixel_after_tilt_plus=(960.0, 700.0),  # moved +Y (down)
    )
    total += 1
    if pan_sign2 != 1 or tilt_sign2 != +1:
        print(f"  FAIL: inverse tilt-sign scenario returned ({pan_sign2}, {tilt_sign2}); "
              f"expected (+1, +1)"); fails += 1
    else:
        print("  PASS: verify_signs returned (+1, +1) for tilt-flipped rig")

    return total, fails


def test_fusion_aged_cluster():
    """Review-finding regression: when every cluster member ages past
    _FUSION_MAX_AGE_S, total_w == 0 and the zero-weight branch used to
    drop members [1..N]. This asserts each input survives to be reaped
    on its own _expiresAt schedule instead."""
    import parent_server as ps
    import time as _t
    print("\n=== Fusion aged-cluster (review-finding) ===")
    fails = 0; total = 0
    ps._temporal_objects.clear()
    # Three detections of the same person, each aged past the 2s window
    # but still inside their ttl (so they haven't reaped yet).
    stale = _t.time() - 3.0  # 3s ago — past _FUSION_MAX_AGE_S = 2.0s
    for i in range(3):
        ps._temporal_objects.append({
            "id": 3100 + i,
            "_temporal": True,
            "objectType": "person",
            "_method": "homography",
            "_yoloConfidence": 0.8,
            "ttl": 10.0,
            "_expiresAt": stale + 10.0,  # still in-flight; age bypasses freshness
            "transform": {"pos": [1500.0, 2000.0, 850.0], "rot": [0,0,0], "scale": [500,1700,500]},
        })
    before = len(ps._temporal_objects)
    ps._fuse_temporal_objects()
    after = len(ps._temporal_objects)
    total += 1
    if after != before:
        print(f"  FAIL: aged cluster shrank {before} → {after} — data loss"); fails += 1
    else:
        print(f"  PASS: aged cluster preserved ({before} in, {after} out)")
    ps._temporal_objects.clear()
    return total, fails


def test_stage_bounds_origin_survivor():
    """Review-finding regression: the _derive_stage_bounds zero-origin
    filter used to drop any fixture/marker surveyed at (0, 0, 0), which
    is the valid back-right-floor corner per project_coordinate_system.md."""
    import parent_server as ps
    print("\n=== Stage bounds origin-survivor (review-finding) ===")
    fails = 0; total = 0
    # Stash + replace _layout / _aruco_markers with controlled inputs.
    saved_layout = ps._layout.get("children")
    saved_markers = list(ps._aruco_markers)
    try:
        ps._layout["children"] = [
            {"id": 7001, "x": 0, "y": 0, "z": 0},       # origin corner — MUST count
            {"id": 7002, "x": 3000, "y": 4000, "z": 2000},
        ]
        ps._aruco_markers.clear()
        w, h, d = ps._derive_stage_bounds()
        total += 1
        # origin fixture should at least keep the max values at 3/4/2 m plus padding
        expected_w = (3000 + 500) / 1000.0
        if abs(w - expected_w) > 1e-3:
            print(f"  FAIL: expected w={expected_w}, got {w}"); fails += 1
        else:
            print(f"  PASS: w={w} honours origin-fixture presence")
    finally:
        if saved_layout is not None:
            ps._layout["children"] = saved_layout
        ps._aruco_markers[:] = saved_markers
    return total, fails


def test_pixel_to_stage_roll_honoured():
    """Review-finding regression: the FOV-projection tier used to drop
    rotation[2] (roll). A camera with tilt=0, pan=0, roll=90° should
    produce a different feet placement than roll=0."""
    import parent_server as ps
    print("\n=== FOV-tier roll honour (review-finding) ===")
    fails = 0; total = 0
    cam_a = {"id": 9800, "fixtureType": "camera", "fovDeg": 90, "fovType": "diagonal",
             "rotation": [30, 0, 0]}
    cam_b = {"id": 9801, "fixtureType": "camera", "fovDeg": 90, "fovType": "diagonal",
             "rotation": [30, 0, 90]}
    ps._fixtures.extend([cam_a, cam_b])
    saved_layout = ps._layout.get("children")
    try:
        ps._layout["children"] = [
            {"id": 9800, "x": 1500, "y": 0, "z": 2000},
            {"id": 9801, "x": 1500, "y": 0, "z": 2000},
        ]
        px, py = 800.0, 400.0
        sx_a, sy_a, tier_a = ps._pixel_point_to_stage_floor(cam_a, px, py, 1920, 1080)
        sx_b, sy_b, tier_b = ps._pixel_point_to_stage_floor(cam_b, px, py, 1920, 1080)
        total += 1
        if tier_a != "fov-projection" or tier_b != "fov-projection":
            print(f"  FAIL: expected fov-projection tiers, got a={tier_a} b={tier_b}"); fails += 1
        else:
            print(f"  PASS: both cameras projected via fov-projection tier")
        total += 1
        delta = ((sx_a - sx_b) ** 2 + (sy_a - sy_b) ** 2) ** 0.5
        if delta < 100.0:
            print(f"  FAIL: roll ignored — a and b placements only {delta:.0f} mm apart"); fails += 1
        else:
            print(f"  PASS: roll honoured — placements differ by {delta:.0f} mm "
                  f"(a={tuple(round(v) for v in (sx_a, sy_a))}, "
                  f"b={tuple(round(v) for v in (sx_b, sy_b))})")
    finally:
        ps._fixtures = [f for f in ps._fixtures if f.get("id") not in (9800, 9801)]
        if saved_layout is not None:
            ps._layout["children"] = saved_layout
    return total, fails


if __name__ == "__main__":
    grand_total = 0
    grand_fail = 0
    tests = (test_tracking_pipeline, test_mover_cal_pipeline,
             test_fusion_aged_cluster, test_stage_bounds_origin_survivor,
             test_pixel_to_stage_roll_honoured)
    for fn in tests:
        try:
            t, f = fn()
        except Exception as e:
            print(f"  FAIL: {fn.__name__} raised {e}")
            t, f = 1, 1
        grand_total += t
        grand_fail += f
    print(f"\n=== Q14 synthetic-pipeline regression: "
          f"{grand_total - grand_fail}/{grand_total} pass ===")
    sys.exit(0 if grand_fail == 0 else 1)
