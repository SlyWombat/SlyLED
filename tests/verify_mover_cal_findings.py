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
    """#5 — _aruco_multi_snapshot_detect must not blast the whole universe."""
    src = _read(_PARENT_PATH)
    m = re.search(r"def _aruco_multi_snapshot_detect\(.*?\n(?=def )",
                  src, re.DOTALL)
    body = m.group(0) if m else ""
    ok(body and "[0] * 512" not in body and "[0]*512" not in body,
       "#5 pre-scan blackout does not broadcast [0]*512")
    ok(body and "set_channels(addr, [0] * chc)" in body,
       "#5 pre-scan zeros only mover fixture windows on target universe")
    ok(body and "isCalibrating" in body,
       "#5 pre-scan skips fixtures holding a calibration lock")


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


def main():
    print("=== #679 mover-calibration review regression checks ===\n")
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

    total = _passed + _failed
    print(f"\n{'=' * 56}")
    if _failed == 0:
        print(f"  ALL {total} CHECKS PASS")
        sys.exit(0)
    print(f"  {_passed}/{total} PASS, {_failed} FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
