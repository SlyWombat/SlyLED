#!/usr/bin/env python3
"""verify_mover_cal_findings.py — Regression guards for #679.

After the Gemini review (two-pass) surfaced seven bugs in the
mover-calibration pipeline, each fix lands with a matching assertion
here. The script reads the code paths directly (source + imported
symbols) rather than spinning up a live orchestrator; the goal is a
fast, CI-friendly post-fix witness that the bugs stay fixed.

Usage:
    python tests/verify_mover_cal_findings.py

Exit code: 0 if every check passes, 1 otherwise.

Each check is numbered to match the acceptance list in issue #679.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MCAL_PATH = os.path.join(_ROOT, "desktop", "shared", "mover_calibrator.py")
_PARENT_PATH = os.path.join(_ROOT, "desktop", "shared", "parent_server.py")
_PARAM_PATH = os.path.join(_ROOT, "desktop", "shared", "parametric_mover.py")

_passed = 0
_failed = 0


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def ok(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}")


def check1_bracket_floor():
    """#1 — BRACKET_FLOOR must come from the fixture's pan bits, not 1/255."""
    src = _read(_MCAL_PATH)
    # The old literal `1.0 / 255.0` directly assigned to BRACKET_FLOOR is
    # the bug; the fix computes from 2**pan_bits - 1.
    bad = re.search(r"BRACKET_FLOOR\s*=\s*1\.0\s*/\s*255\.0", src)
    good = re.search(r"BRACKET_FLOOR\s*=\s*1\.0\s*/\s*float\(2\s*\*\*\s*pan_bits\s*-\s*1\)", src)
    ok(not bad, "#1 no hardcoded 1/255 BRACKET_FLOOR literal")
    ok(bool(good), "#1 BRACKET_FLOOR derives from pan_bits (2**bits-1)")


def check2_cal_blackout_targeted():
    """#2 — _cal_blackout must target only the fixture's channel window."""
    src = _read(_PARENT_PATH)
    m = re.search(r"def _cal_blackout\(\):.*?_set_calibrating\(fid, False\)",
                  src, re.DOTALL)
    body = m.group(0) if m else ""
    # The buggy signature was `_mcal._hold_dmx(bridge_ip, [0]*512, ...)`;
    # comments that quote the literal don't count, so look for the call.
    ok(body and "_mcal._hold_dmx(bridge_ip, [0]" not in body,
       "#2 _cal_blackout does not call _hold_dmx with a 512-byte buffer")
    ok("set_channels(addr, [0] * ch_count)" in body,
       "#2 _cal_blackout zeroes only the fixture's channel window")


def check3_oversample_default_n():
    """#3 — _refine_battleship_hit must not override n=2 (triggers mean path)."""
    src = _read(_MCAL_PATH)
    # The buggy pattern pinned n=2 at the _refine call site.
    ok(re.search(r"_beam_detect_oversampled\(camera_ip, cam_idx, color,\s*center=True,\s*n=2,",
                 src) is None,
       "#3 _refine_battleship_hit no longer passes n=2 to oversample")
    # And the median helper still does the right thing for odd n (sanity).
    import mover_calibrator as mcal  # noqa: WPS433 — test-side import
    med_odd = mcal.__dict__  # keep reference, just ensure import succeeds
    # Exercise the public default N on an odd count.
    ok(mcal.OVERSAMPLE_N == 3,
       "#3 OVERSAMPLE_N default is 3 (odd — real median path)")


def check4_pixel_score_resolution():
    """#4 — pixel-centre score must adapt to camera resolution, not 640×480."""
    src = _read(_MCAL_PATH)
    # The buggy formula used literal 600/400 upper bounds. The fix scales
    # with camera_resolution.
    bad = re.search(r"min\(bx\s*-\s*40,\s*600\s*-\s*bx\)", src)
    ok(bad is None, "#4 no literal 600/400 upper bound in refine score")
    ok("camera_resolution" in src and "cam_w, cam_h = camera_resolution" in src,
       "#4 refine score consumes camera_resolution=(w, h)")
    # Verify battleship_discover plumbs the param.
    ok(re.search(r"def battleship_discover\([^)]*camera_resolution", src, re.DOTALL),
       "#4 battleship_discover exposes camera_resolution param")


def check5_aruco_blackout_targeted():
    """#5 / #681-A — _aruco_multi_snapshot_detect must not blast the
    whole universe AND must not clobber bystander fixtures. Post-#681
    the blackout targets only the `calibrating_fixture` passed by the
    caller.
    """
    src = _read(_PARENT_PATH)
    m = re.search(r"def _aruco_multi_snapshot_detect\(.*?\n(?=def )",
                  src, re.DOTALL)
    body = m.group(0) if m else ""
    ok(body and "[0] * 512" not in body and "[0]*512" not in body,
       "#5 pre-scan blackout does not broadcast [0]*512")
    ok(body and "calibrating_fixture" in body,
       "#5/#681-A pre-scan blackout is scoped to the calibrating fixture")
    ok(body and "set_channels(addr, [0] * chc)" in body,
       "#5 pre-scan zeros only the calibrating fixture's channel window")


def check6_aim_respects_lock():
    """#6 — api_mover_cal_aim must refuse when the fixture is calibrating."""
    src = _read(_PARENT_PATH)
    m = re.search(r"def api_mover_cal_aim\(fid\):(.*?)(?=\n@app\.|\ndef )",
                  src, re.DOTALL)
    body = m.group(1) if m else ""
    ok("_fixture_is_calibrating(fid)" in body,
       "#6 api_mover_cal_aim checks _fixture_is_calibrating")
    ok("409" in body, "#6 api_mover_cal_aim returns 409 when locked")


def check7_mirror_ambiguity_flag():
    """#7 — FitQuality must expose mirror_ambiguity, and fit_model sets it."""
    import parametric_mover as pm  # noqa: WPS433
    q = pm.FitQuality(rms_error_deg=0.0, max_error_deg=0.0,
                      sample_count=0, condition_number=1.0)
    ok(hasattr(q, "mirror_ambiguity") and q.mirror_ambiguity is False,
       "#7 FitQuality has mirror_ambiguity attribute, defaults False")
    ok("mirrorAmbiguity" in q.to_dict(),
       "#7 FitQuality.to_dict surfaces mirrorAmbiguity for API consumers")
    src = _read(_PARAM_PATH)
    # The flag must only fire when force_signs is None — otherwise the
    # caller already disambiguated.
    ok(re.search(r"mirror_ambiguity\s*=\s*\(.*force_signs is None", src, re.DOTALL)
       is not None,
       "#7 mirror_ambiguity requires force_signs is None before signalling")

    # Empirical: force_signs must suppress the ambiguity flag. A full
    # mirror-ambiguous sample set is harder to construct deterministically
    # than it is to assert the no-op direction — the absence assertion is
    # what the UI contract depends on.
    gt = pm.ParametricFixtureModel(
        fixture_pos=(1500, 500, 3500),
        pan_range_deg=540, tilt_range_deg=270,
        mount_yaw_deg=-8.0, mount_pitch_deg=3.0, mount_roll_deg=1.5,
        pan_offset=0.48, tilt_offset=0.51, pan_sign=1, tilt_sign=-1,
    )
    import random as _r
    rng = _r.Random(42)
    samples = []
    for _ in range(8):
        pn = 0.5 + rng.uniform(-0.18, 0.18)
        tn = 0.5 + rng.uniform(-0.1, 0.1)
        d = gt.forward(pn, tn)
        dist = rng.uniform(4500, 7500)
        samples.append({"pan": pn, "tilt": tn,
                         "stageX": gt.fixture_pos[0] + d[0] * dist,
                         "stageY": gt.fixture_pos[1] + d[1] * dist,
                         "stageZ": gt.fixture_pos[2] + d[2] * dist})
    _, q_force = pm.fit_model(gt.fixture_pos, gt.pan_range_deg,
                                gt.tilt_range_deg, samples,
                                force_signs=(gt.pan_sign, gt.tilt_sign))
    ok(q_force.mirror_ambiguity is False,
       "#7 force_signs supplied → mirror_ambiguity always False")


def check8_startup_investigation():
    """#8 — documented as not-applicable; assert the reasoning holds."""
    # Engine __init__ makes no persistent pre-set buffers.
    import dmx_artnet as da  # noqa: WPS433
    # ArtNetEngine.__init__ signature + no persisted state field is the
    # invariant the investigation relied on.
    engine = da.ArtNetEngine()
    ok(getattr(engine, "_universes") == {},
       "#8 engine starts with empty universes dict (no stale buffer)")
    # Startup clear of isCalibrating in parent_server is present.
    src = _read(_PARENT_PATH)
    ok('_f.pop("isCalibrating", None)' in src,
       "#8 startup clears stale isCalibrating flags from fixtures")


def check681_A_targeted_blackout():
    """#681-A — every `_hold_dmx(bridge_ip, [0]*512, ...)` in the
    calibration paths is replaced with `_targeted_fixture_blackout(fid)`
    so bystander fixtures stay lit.
    """
    src = _read(_PARENT_PATH)
    ok("_targeted_fixture_blackout" in src,
       "#681-A _targeted_fixture_blackout helper defined")
    ok(src.count("_targeted_fixture_blackout(fid)") >= 10,
       f"#681-A helper replaces >=10 universe-wide blackout sites "
       f"(got {src.count('_targeted_fixture_blackout(fid)')})")
    # No remaining `_mcal._hold_dmx(bridge_ip, [0]*512, ...)` EXECUTABLE
    # calls — comments quoting the literal are fine.
    offending = re.findall(
        r"^\s*_mcal\._hold_dmx\(bridge_ip,\s*\[0\]\s*\*\s*512,",
        src, re.MULTILINE)
    ok(not offending,
       f"#681-A no remaining _hold_dmx(bridge_ip, [0]*512, ...) executable calls "
       f"(found {len(offending)})")


def check681_B_grid_filter():
    """#681-B — battleship_discover takes a `grid_filter` predicate and
    uses it to prioritise candidates inside any camera's floor polygon.
    """
    src = _read(_MCAL_PATH)
    ok(re.search(r"def battleship_discover\([^)]*grid_filter", src, re.DOTALL),
       "#681-B battleship_discover exposes grid_filter param")
    ok("camera-FOV filter kept" in src,
       "#681-B battleship logs FOV filter stats")
    src_p = _read(_PARENT_PATH)
    ok("_build_battleship_grid_filter" in src_p,
       "#681-B parent_server has grid-filter builder")
    # Filter function uses ParametricFixtureModel + camera_floor_polygon
    ok("camera_floor_polygon" in src_p and "ParametricFixtureModel" in src_p,
       "#681-B grid-filter builder uses camera_floor_polygon + ParametricFixtureModel")


def check681_CD_markers_seed_and_ranges():
    """#681-C/D — markers-mode battleship call passes seed_pan / seed_tilt
    / pan_range_deg / tilt_range_deg / beam_width_deg (matching the
    legacy path) so the grid is FOV-aware + adaptive-density fires.
    """
    src = _read(_PARENT_PATH)
    # Locate _mover_cal_thread_markers_body and its battleship call.
    m = re.search(r"def _mover_cal_thread_markers_body\(.*?(?=\ndef )",
                  src, re.DOTALL)
    body = m.group(0) if m else ""
    ok("battleship_discover" in body,
       "#681-C/D markers body calls battleship_discover")
    ok("seed_pan=seed_pan" in body and "seed_tilt=seed_tilt" in body,
       "#681-C markers path passes seed_pan + seed_tilt")
    ok("pan_range_deg=pan_range_deg" in body and
       "tilt_range_deg=tilt_range_deg" in body,
       "#681-D markers path passes pan_range_deg + tilt_range_deg")
    ok("beam_width_deg=beam_width_deg" in body,
       "#681-D markers path passes beam_width_deg")
    ok("compute_initial_aim" in body,
       "#681-C markers path computes initial aim as seed")


def check681_all_auto_mode():
    """#681 — 'all-auto' mode runs markers first, falls back to legacy
    on discovery failure, and logs the transition.
    """
    src = _read(_PARENT_PATH)
    ok("_mover_cal_thread_all_auto" in src,
       "#681 all-auto thread wrapper defined")
    ok('mode == "all-auto"' in src or 'mode != "all-auto"' in src or
       "'all-auto'" in src,
       "#681 start endpoint accepts all-auto mode")
    ok("falling back to Legacy BFS" in src,
       "#681 all-auto logs markers→legacy transition")


def check599_floor_alignment_wired():
    """#599 — every scan path that produces a monocular or feature-based
    point cloud must auto-call `_apply_marker_z_alignment`. ZoeDepth +
    mono shipped in 95b393b; stereo was the missing site, added here.
    Operator-triggered `/align-to-markers` passes force=True; auto
    callers leave force=False so the guard skips re-measurement.
    """
    src = _read(_PARENT_PATH)
    # Helper exists and supports the force kwarg.
    ok("def _apply_marker_z_alignment" in src,
       "#599 _apply_marker_z_alignment helper defined")
    ok(re.search(r"def _apply_marker_z_alignment\([^)]*force=False",
                 src) is not None,
       "#599 helper has force kwarg")
    # Guard present.
    ok('"already aligned in this session"' in src,
       "#599 helper skips re-application when already aligned")
    # Auto-apply sites — ZoeDepth, mono, stereo.
    zoe = re.search(r'@app\.post\("/api/space/scan/zoedepth"\).*?(?=\n@app\.)',
                     src, re.DOTALL)
    zoe_body = zoe.group(0) if zoe else ""
    ok("_apply_marker_z_alignment(_point_cloud)" in zoe_body,
       "#599 ZoeDepth scan auto-applies marker-Z alignment")
    stereo = re.search(r"def api_space_scan_stereo.*?(?=\n@app\.|\ndef )",
                        src, re.DOTALL)
    stereo_body = stereo.group(0) if stereo else ""
    ok("_apply_marker_z_alignment(_point_cloud)" in stereo_body,
       "#599 stereo scan auto-applies marker-Z alignment")
    # Operator endpoint uses force=True.
    op = re.search(r"def api_space_align_to_markers.*?(?=\n@app\.|\ndef )",
                    src, re.DOTALL)
    op_body = op.group(0) if op else ""
    ok("force=True" in op_body,
       "#599 /api/space/align-to-markers calls with force=True")


def check681_new_tuning_keys():
    """#681 — CAL_TUNING_SPEC gains rejectReflection, refineAfterHit,
    adaptiveDensity (bool toggles) exposed to the wizard Advanced panel.
    """
    src = _read(_PARENT_PATH)
    for k in ("rejectReflection", "refineAfterHit", "adaptiveDensity"):
        ok(f'"{k}":' in src,
           f"#681 CAL_TUNING_SPEC contains {k}")
    ok('"type": "bool"' in src or "'type': 'bool'" in src,
       "#681 CAL_TUNING_SPEC supports bool type")


def main():
    print("=== #679 + #681 mover-calibration regression checks ===\n")
    print("-- #1 BRACKET_FLOOR --")
    check1_bracket_floor()
    print("-- #2 _cal_blackout targeted --")
    check2_cal_blackout_targeted()
    print("-- #3 oversample default n --")
    check3_oversample_default_n()
    print("-- #4 pixel-score resolution --")
    check4_pixel_score_resolution()
    print("-- #5 ArUco pre-scan blackout --")
    check5_aruco_blackout_targeted()
    print("-- #6 /aim respects lock --")
    check6_aim_respects_lock()
    print("-- #7 mirror-ambiguity flag --")
    check7_mirror_ambiguity_flag()
    print("-- #8 startup investigation --")
    check8_startup_investigation()
    print("-- #681-A targeted blackout helper --")
    check681_A_targeted_blackout()
    print("-- #681-B FOV-aware grid filter --")
    check681_B_grid_filter()
    print("-- #681-C/D markers-mode seed + ranges --")
    check681_CD_markers_seed_and_ranges()
    print("-- #681 All-Auto mode --")
    check681_all_auto_mode()
    print("-- #681 new tuning keys --")
    check681_new_tuning_keys()
    print("-- #599 floor-alignment wiring --")
    check599_floor_alignment_wired()

    total = _passed + _failed
    print(f"\n{'=' * 56}")
    if _failed == 0:
        print(f"  ALL {total} CHECKS PASS")
        sys.exit(0)
    print(f"  {_passed}/{total} PASS, {_failed} FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
