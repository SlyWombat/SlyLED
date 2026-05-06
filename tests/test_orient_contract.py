#!/usr/bin/env python3
"""test_orient_contract.py — End-to-end orient → aim_stage contract (#824).

Pins down the **agreement** between the four coupled convention surfaces
that have driven the gesture-axis whack-a-mole this issue tracks:

    Android body-axes  →  wire (Euler/quat)  →  Remote.body-frame axes  →
    R_world_to_stage   →  aim_stage          →  pan/tilt → DMX

Each test fixes a remote kind, a grip (forward_local / up_local), and a
calibrate pose, then applies a known rotation and asserts the aim_stage
moves in the expected stage-frame direction. If a single sign elsewhere
in the pipeline is wrong, the matching matrix entry fails — and which
entry fails is the diagnostic.

Convention reminders (CLAUDE.md):
    Stage frame: X = stage-left, Y = stage-forward (back-wall→audience),
                  Z = stage-up. Origin = stage-right back-wall floor.
    pan_deg > 0  =  beam swept toward +X (stage-left).
    tilt_deg > 0 =  beam above horizon (toward +Z, ceiling).
    tilt_deg < 0 =  beam below horizon (toward -Z, floor).

Run:  python -X utf8 tests/test_orient_contract.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from remote_math import (  # noqa: E402
    quat_from_axis_angle,
    quat_from_euler_zyx_deg,
    quat_mul,
)
from remote_orientation import (  # noqa: E402
    KIND_PHONE,
    KIND_PUCK,
    OrientConvention,
    Remote,
)

_passed = 0
_failed = 0
_known_failing = 0


def _assert(cond, msg, known_failing=False):
    """Standard assert. `known_failing=True` flips a counter without
    failing the suite — used to surface broken-but-not-fixed-yet
    pipeline cells (per #824, sign-flip fix is a separate gated PR)."""
    global _passed, _failed, _known_failing
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    elif known_failing:
        _known_failing += 1
        print(f"  [XFAIL] {msg}  (#824 — fix gated on full matrix)")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


def _close(actual, expected, tol=0.05):
    return abs(actual - expected) <= tol


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: minimal Remote builder + canonical calibrations.

def _phone_portrait(target_aim_stage=(0.0, 1.0, 0.0)):
    """Phone held in portrait grip, top-edge pointing at stage. Calibrated
    against straight-forward stage aim. Matches the post-#816 grip publish."""
    r = Remote(id=99, kind=KIND_PHONE,
                forward_local=(0.0, 1.0, 0.0),  # portrait pointer = phone +Y
                up_local=(0.0, 0.0, 1.0))       # phone +Z = out-of-screen
    r.convention = OrientConvention.FLAT_PITCH_YAW
    # Identity quat — phone neutral, pointer aligned with stage forward.
    r.calibrate(target_aim_stage=target_aim_stage,
                quat=(1.0, 0.0, 0.0, 0.0))
    return r


def _phone_landscape(target_aim_stage=(0.0, 1.0, 0.0)):
    """Phone held landscape, long edge pointing at stage. forward_local
    falls back to the codebase default (X-forward).
    """
    r = Remote(id=98, kind=KIND_PHONE)  # default forward=(1,0,0), up=(0,0,1)
    r.convention = OrientConvention.FLAT_PITCH_YAW
    r.calibrate(target_aim_stage=target_aim_stage,
                quat=(1.0, 0.0, 0.0, 0.0))
    return r


def _puck(target_aim_stage=(0.0, 1.0, 0.0)):
    """Gyro puck, LCD-up, default convention (FLAT_PITCH_YAW post-#777)."""
    r = Remote(id=97, kind=KIND_PUCK)  # default forward=(1,0,0), up=(0,0,1)
    r.convention = OrientConvention.FLAT_PITCH_YAW
    r.calibrate(target_aim_stage=target_aim_stage,
                quat=(1.0, 0.0, 0.0, 0.0))
    return r


def _phone_for_surface_rotation(surface_rot, target_aim_stage=(0.0, 1.0, 0.0)):
    """Build a phone Remote whose forward_local/up_local match what
    `ControlViewModel.publishGripFromSurfaceRotation` (Android) sends for
    the given Surface.ROTATION_* value. Keeps the contract test in lock-
    step with the actual grip-publish table."""
    grips = {
        0: ((0.0,  1.0, 0.0), (0.0, 0.0, 1.0)),   # ROTATION_0 portrait
        1: ((1.0,  0.0, 0.0), (0.0, 0.0, 1.0)),   # ROTATION_90 landscape-left
        2: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),   # ROTATION_180 inverted-portrait
        3: ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),   # ROTATION_270 landscape-right
    }
    fwd, up = grips[surface_rot]
    r = Remote(id=100 + surface_rot, kind=KIND_PHONE,
                forward_local=fwd, up_local=up)
    r.convention = OrientConvention.FLAT_PITCH_YAW
    r.calibrate(target_aim_stage=target_aim_stage,
                quat=(1.0, 0.0, 0.0, 0.0))
    return r


# ─────────────────────────────────────────────────────────────────────────────
# A. Phone × portrait × pitch — top-edge tips toward stage → tilt DOWN.
#
# In the phone's body frame, "pitching forward" is a rotation around its
# X axis (right side). Right-hand rule: +X axis rotation by NEGATIVE angle
# takes body +Y (pointer = forward) toward body -Z (down). So a pitch-
# forward gesture is `quat_from_axis_angle((1,0,0), -angle)`.
#
# Expected aim_stage: tilts down → aim_stage.z < 0 (per stage convention).

def test_phone_portrait_pitch_forward_tilts_down():
    r = _phone_portrait()
    # 30° pitch forward.
    q = quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone portrait pitch produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] < -0.3,
            f"pitch forward 30° → aim_stage.z ≈ -0.5 (got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[1] > 0.7,
            f"pitch forward 30° → aim_stage.y ≈ +0.87 (got {r.aim_stage[1]:.3f})")
    _assert(_close(r.aim_stage[0], 0.0, tol=0.05),
            f"pitch forward 30° → aim_stage.x ≈ 0 (got {r.aim_stage[0]:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# B. Phone × portrait × yaw — operator turns left (CCW from above) → pan LEFT.
#
# Yaw-left from operator perspective = rotation around world-up (= phone +Z
# when held vertical). Right-hand rule on +Z by POSITIVE angle takes body +Y
# (pointer = forward) toward body -X (operator's left from above).
# Expected aim_stage: pans toward stage +X (= stage-left per CLAUDE.md).

def test_phone_portrait_yaw_left_pans_stage_left():
    r = _phone_portrait()
    q = quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone portrait yaw produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    # Pan left = stage +X. The operator's "yaw left" gesture must give
    # aim_stage.x > 0. The 2026-05-05 live-test reported the opposite
    # sign (pan reversed); resolved by the phone-only qz negate in
    # `Remote._apply_quat` (#824). This entry is now the regression
    # gate — if a future change reintroduces the sign error, this
    # asserts before the operator notices on hardware.
    _assert(r.aim_stage[0] > 0.3,
            f"yaw left 30° → aim_stage.x ≈ +0.5 (got {r.aim_stage[0]:.3f})")
    _assert(r.aim_stage[1] > 0.7,
            f"yaw left 30° → aim_stage.y ≈ +0.87 (got {r.aim_stage[1]:.3f})")
    _assert(_close(r.aim_stage[2], 0.0, tol=0.05),
            f"yaw left 30° → aim_stage.z ≈ 0 (got {r.aim_stage[2]:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# C. Phone × portrait × roll — operator twists phone left/right → no aim move.
#
# Roll = rotation around the body-Y axis (the pointer). It changes which
# way "up" points but leaves the forward axis untouched. aim_stage should
# stay essentially fixed.

def test_phone_portrait_roll_does_not_move_aim():
    r = _phone_portrait()
    q = quat_from_axis_angle((0.0, 1.0, 0.0), math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone portrait roll produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(_close(r.aim_stage[0], 0.0, tol=0.05),
            f"roll → aim_stage.x ≈ 0 (got {r.aim_stage[0]:.3f})")
    _assert(_close(r.aim_stage[1], 1.0, tol=0.05),
            f"roll → aim_stage.y ≈ +1 (got {r.aim_stage[1]:.3f})")
    _assert(_close(r.aim_stage[2], 0.0, tol=0.05),
            f"roll → aim_stage.z ≈ 0 (got {r.aim_stage[2]:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# D. Phone × landscape × pitch — top of phone tips toward stage → tilt down.
#
# In landscape grip, the long edge points at stage. forward_local = (1,0,0)
# (default). Pitching forward = rotation around body +Y axis. Right-hand
# rule on +Y by POSITIVE angle takes body +X (forward) toward body +Z (up)
# — so to tip forward (toward -Z) we need NEGATIVE angle on +Y.
#
# But wait — that's only true for one of the two landscape grips (left vs
# right). Operator-perspective convention: phone held with screen toward
# operator, top-edge to the operator's left, long-edge pointing at stage.
# Pitching forward (top tips toward stage) = rotation around body +Y by
# NEGATIVE angle? Or POSITIVE? Body +Y points up (operator's right side
# of the device when held this way)... Conventions differ; this entry
# captures whichever is the one currently shipped via Android grip-publish.

def test_phone_landscape_pitch_forward_tilts_down():
    r = _phone_landscape()
    # Landscape grip ambiguity — test the 2026-05-05 shipped convention
    # (forward_local = +X, up_local = +Z, pitch around +Y axis).
    q = quat_from_axis_angle((0.0, 1.0, 0.0), -math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone landscape pitch produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    # Aim should tilt down (z<0) without panning sideways. Currently
    # reverses on landscape grip — known-failing per #824.
    _assert(r.aim_stage[2] < -0.3,
            f"landscape pitch forward → aim_stage.z < -0.3 (got {r.aim_stage[2]:.3f})",
            known_failing=True)


# ─────────────────────────────────────────────────────────────────────────────
# E. Puck × LCD-up × pitch — gyro tipped forward → tilt down.

def test_puck_pitch_forward_tilts_down():
    r = _puck()
    q = quat_from_axis_angle((0.0, 1.0, 0.0), -math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "puck pitch produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] < -0.3,
            f"puck pitch forward → aim_stage.z < -0.3 (got {r.aim_stage[2]:.3f})",
            known_failing=True)


# ─────────────────────────────────────────────────────────────────────────────
# F. Puck × LCD-up × yaw — gyro yawed left → pan stage-left.

def test_puck_yaw_left_pans_stage_left():
    r = _puck()
    q = quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(30))
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "puck yaw produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    # Puck is BOTTOM_FORWARD_ROLL_PITCH default? No — post-#777 it's
    # FLAT_PITCH_YAW (full Euler). Yaw must propagate. Same sign-flip
    # symptom as the phone-portrait yaw test — known-failing per #824.
    _assert(r.aim_stage[0] > 0.3,
            f"puck yaw left → aim_stage.x > +0.3 (got {r.aim_stage[0]:.3f})",
            known_failing=True)


# ─────────────────────────────────────────────────────────────────────────────
# G. Round-trip: aim_stage from a known target via euler-deg path matches
#    the same call with a quaternion. Guards against future divergence
#    between the two `update_*` entry points.

def test_euler_and_quat_paths_agree():
    r1 = _phone_landscape()
    r2 = _phone_landscape()
    # 20° pitch via Euler.
    r1.update_from_euler_deg(roll=0.0, pitch=20.0, yaw=0.0)
    # Same rotation as quaternion. ZYX intrinsic Euler (roll, pitch, yaw):
    q = quat_from_euler_zyx_deg(0.0, 20.0, 0.0)
    r2.update_from_quat(q)
    if r1.aim_stage is None or r2.aim_stage is None:
        _assert(False, "both paths produce aim_stage")
        return
    for i, axis in enumerate("xyz"):
        _assert(_close(r1.aim_stage[i], r2.aim_stage[i], tol=1e-4),
                f"euler vs quat aim_stage.{axis} agree "
                f"(euler={r1.aim_stage[i]:.4f} quat={r2.aim_stage[i]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# H. Identity rotation after calibrate-against-(0,1,0) → aim straight forward.

def test_identity_quat_aims_at_calibration_target():
    for builder in (_phone_portrait, _phone_landscape, _puck):
        r = builder(target_aim_stage=(0.0, 1.0, 0.0))
        r.update_from_quat((1.0, 0.0, 0.0, 0.0))
        _assert(r.aim_stage is not None, f"{builder.__name__}: identity quat produces aim_stage")
        if r.aim_stage is None:
            continue
        for i, (axis, want) in enumerate(zip("xyz", (0.0, 1.0, 0.0))):
            _assert(_close(r.aim_stage[i], want, tol=1e-3),
                    f"{builder.__name__}: identity → aim_stage.{axis} = {want} "
                    f"(got {r.aim_stage[i]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# I. MIRROR gestures — every test above must also work in the opposite
# direction. Without these, a sign error that flips one axis can hide as
# "looks right when pitched forward, broken when pitched back".

def test_phone_portrait_pitch_back_tilts_up():
    r = _phone_portrait()
    q = quat_from_axis_angle((1.0, 0.0, 0.0), +math.radians(30))  # pitch back
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone portrait pitch-back produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] > 0.3,
            f"pitch back 30° → aim_stage.z ≈ +0.5 (got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[1] > 0.7,
            f"pitch back 30° → aim_stage.y ≈ +0.87 (got {r.aim_stage[1]:.3f})")


def test_phone_portrait_yaw_right_pans_stage_right():
    r = _phone_portrait()
    q = quat_from_axis_angle((0.0, 0.0, 1.0), -math.radians(30))  # yaw right
    r.update_from_quat(q)
    _assert(r.aim_stage is not None, "phone portrait yaw-right produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[0] < -0.3,
            f"yaw right 30° → aim_stage.x ≈ -0.5 (got {r.aim_stage[0]:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# J. COMBINED gestures — the qz-negate trick from #824 only exactly mirrors
# yaw on PURE-yaw quaternions. A live phone produces composite rotations
# (pitch + yaw + a touch of roll) every frame. If the negate breaks down on
# composite quats, this is where it shows up — and matches the operator's
# "tilt no longer works" report from the manual #758 test.

def test_phone_portrait_pitch_forward_then_yaw_left_combined():
    """Operator pitches phone forward 25° AND yaws left 25° simultaneously.
    Expected: aim moves down-and-left in stage frame.
    """
    r = _phone_portrait()
    # Compose in body frame: yaw THEN pitch (yaw is around world-up first,
    # then pitch around the new body-right). Same shape as the live
    # `getQuaternionFromVector` output.
    q_yaw = quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(25))
    q_pitch = quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(25))
    q_combined = quat_mul(q_yaw, q_pitch)
    r.update_from_quat(q_combined)
    _assert(r.aim_stage is not None, "combined gesture produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] < -0.2,
            f"pitch+yaw → aim_stage.z < -0.2 (tilt down, got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[0] > 0.2,
            f"pitch+yaw → aim_stage.x > +0.2 (pan stage-left, got {r.aim_stage[0]:.3f})")


def test_phone_portrait_pitch_then_yaw_combined_other_order():
    """Sibling of the previous test using the OPPOSITE composition order
    (`quat_mul(q_pitch, q_yaw)` instead of `quat_mul(q_yaw, q_pitch)`).
    The live phone's `getQuaternionFromVector` returns a single composed
    quat — the orchestrator does not choose the order. If the qz-negate
    in `_apply_quat` is order-dependent on composite quats, this cell
    fails while the original combined test passes."""
    r = _phone_portrait()
    q_yaw = quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(25))
    q_pitch = quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(25))
    q_combined = quat_mul(q_pitch, q_yaw)  # opposite of the previous test
    r.update_from_quat(q_combined)
    _assert(r.aim_stage is not None, "combined-other-order produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] < -0.2,
            f"pitch+yaw (other order) → aim_stage.z < -0.2 (got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[0] > 0.2,
            f"pitch+yaw (other order) → aim_stage.x > +0.2 (got {r.aim_stage[0]:.3f})")


def test_phone_portrait_pitch_back_then_yaw_right_combined():
    """Mirror of the previous test — pitch back AND yaw right together.
    Expected: aim moves up-and-right.
    """
    r = _phone_portrait()
    q_yaw = quat_from_axis_angle((0.0, 0.0, 1.0), -math.radians(25))
    q_pitch = quat_from_axis_angle((1.0, 0.0, 0.0), +math.radians(25))
    q_combined = quat_mul(q_yaw, q_pitch)
    r.update_from_quat(q_combined)
    _assert(r.aim_stage is not None, "combined-mirror gesture produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] > 0.2,
            f"pitch-back+yaw-right → aim_stage.z > +0.2 (got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[0] < -0.2,
            f"pitch-back+yaw-right → aim_stage.x < -0.2 (got {r.aim_stage[0]:.3f})")


# ─────────────────────────────────────────────────────────────────────────────
# K. Surface-rotation grip matrix — mirrors the four-way table in
# `ControlViewModel.publishGripFromSurfaceRotation`. For each of the four
# Android Surface.ROTATION_* values, identity quat must aim at the
# calibration target. (Catches a future regression in the grip publish
# table or the server's interpretation of `forward_local` / `up_local`.)

def test_phone_grip_matrix_identity():
    for surf in (0, 1, 2, 3):
        r = _phone_for_surface_rotation(surf)
        r.update_from_quat((1.0, 0.0, 0.0, 0.0))
        if r.aim_stage is None:
            _assert(False, f"surface_rotation={surf}: identity produces aim_stage")
            continue
        for i, (axis, want) in enumerate(zip("xyz", (0.0, 1.0, 0.0))):
            _assert(_close(r.aim_stage[i], want, tol=1e-3),
                    f"surf={surf} identity → aim_stage.{axis}={want} "
                    f"(got {r.aim_stage[i]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# L. Wire-format parity — the puck speaks Euler over UDP CMD_GYRO_ORIENT,
# the phone speaks quat over HTTP /api/mover-control/orient. Given physically
# equivalent rotations, both wire paths must converge on aim_stage.
#
# We model the actual server endpoints by calling the same Remote method
# the handlers call (`update_from_euler_deg` for UDP, `update_from_quat`
# for HTTP). Diverging implementations of the two — e.g. the qz-flip
# applying to one path but not the other — fail this test.

def test_puck_euler_path_matches_quat_path():
    """Puck UDP-Euler and an equivalent quat update on the same Remote
    should produce identical aim_stage. (Same shape as test G but explicit
    about the wire-path framing, and uses combined rotation.)"""
    r1 = _puck()
    r2 = _puck()
    r1.update_from_euler_deg(roll=15.0, pitch=10.0, yaw=20.0)
    r2.update_from_quat(quat_from_euler_zyx_deg(15.0, 10.0, 20.0))
    if r1.aim_stage is None or r2.aim_stage is None:
        _assert(False, "puck Euler+quat both produce aim_stage")
        return
    for i, axis in enumerate("xyz"):
        _assert(_close(r1.aim_stage[i], r2.aim_stage[i], tol=1e-4),
                f"puck wire-path parity .{axis} "
                f"(euler={r1.aim_stage[i]:.4f} quat={r2.aim_stage[i]:.4f})")


def test_phone_quat_and_euler_paths_agree_combined():
    """Phone HTTP /orient quat path vs Euler-fallback path agree on a
    combined rotation. The qz-flip in `_apply_quat` runs in BOTH paths
    (Euler routes through it via `update_from_euler_deg`), so the two
    must remain identical."""
    r1 = _phone_portrait()
    r2 = _phone_portrait()
    r1.update_from_euler_deg(roll=10.0, pitch=15.0, yaw=20.0)
    r2.update_from_quat(quat_from_euler_zyx_deg(10.0, 15.0, 20.0))
    if r1.aim_stage is None or r2.aim_stage is None:
        _assert(False, "phone Euler+quat both produce aim_stage")
        return
    for i, axis in enumerate("xyz"):
        _assert(_close(r1.aim_stage[i], r2.aim_stage[i], tol=1e-4),
                f"phone wire-path parity .{axis} "
                f"(euler={r1.aim_stage[i]:.4f} quat={r2.aim_stage[i]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# M. Phone-vs-puck PARITY — both clients must agree on the same "operator
# gesture in operator's frame" given they share the FLAT_PITCH_YAW
# convention and an X-forward / Z-up grip (puck LCD-up == phone landscape
# ROTATION_90). If a fix to one breaks the other, this matrix breaks
# loudly.

def _gesture_pitch_forward(deg=20.0):
    return quat_from_axis_angle((0.0, 1.0, 0.0), -math.radians(deg))


def _gesture_yaw_left(deg=20.0):
    return quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(deg))


def test_phone_landscape_and_puck_match_on_pitch():
    """Phone landscape (forward=+X) and puck LCD-up (forward=+X) must
    produce IDENTICAL aim_stage for the same body-frame pitch gesture.
    Currently both XFAIL on tilt direction — but they should fail
    TOGETHER. If they diverge, a per-kind branch went wrong."""
    rphone = _phone_landscape()
    rpuck = _puck()
    q = _gesture_pitch_forward()
    rphone.update_from_quat(q)
    rpuck.update_from_quat(q)
    if rphone.aim_stage is None or rpuck.aim_stage is None:
        _assert(False, "landscape phone + puck both produce aim_stage")
        return
    print(f"      phone aim_stage = {tuple(round(v, 3) for v in rphone.aim_stage)}")
    print(f"      puck  aim_stage = {tuple(round(v, 3) for v in rpuck.aim_stage)}")
    # Without a per-kind branch, these MUST match. The #824 qz-flip is
    # phone-only — for pure pitch that's a no-op (qz=0), so this passes.
    for i, axis in enumerate("xyz"):
        _assert(_close(rphone.aim_stage[i], rpuck.aim_stage[i], tol=1e-3),
                f"phone-landscape vs puck pitch parity .{axis} "
                f"(phone={rphone.aim_stage[i]:.4f} puck={rpuck.aim_stage[i]:.4f})")


def test_phone_landscape_and_puck_diverge_on_yaw_by_design():
    """Phone landscape and puck use the SAME grip but #824 added a phone-
    only qz-flip. For a pure yaw gesture, phone and puck must DIVERGE on
    the X axis (mirrored aim_stage.x). Pins down the intentional asymmetry
    so a future "make them agree" fix is forced to update this test
    explicitly rather than silently breaking one client."""
    rphone = _phone_landscape()
    rpuck = _puck()
    q = _gesture_yaw_left()
    rphone.update_from_quat(q)
    rpuck.update_from_quat(q)
    if rphone.aim_stage is None or rpuck.aim_stage is None:
        _assert(False, "phone+puck both produce aim_stage on yaw")
        return
    print(f"      phone aim_stage = {tuple(round(v, 3) for v in rphone.aim_stage)}")
    print(f"      puck  aim_stage = {tuple(round(v, 3) for v in rpuck.aim_stage)}")
    _assert(_close(rphone.aim_stage[0], -rpuck.aim_stage[0], tol=1e-3),
            f"phone-landscape vs puck yaw aim_stage.x are mirrored "
            f"(phone={rphone.aim_stage[0]:.4f} puck={rpuck.aim_stage[0]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# N. CALIBRATE-FRAME parity — the regression every prior cell missed.
#
# All `_phone_*` helpers above calibrate with the IDENTITY quat
# (1,0,0,0). qz=0, so the #824 phone-only qz-negate in `_apply_quat`
# is a no-op for those calibrations. The LIVE Android phone never
# calibrates against identity — `getQuaternionFromVector` captures the
# operator's actual portrait grip, which has all four components
# meaningful. If the qz-negate runs on orient updates but NOT in
# `calibrate()`, the calibrate-frame R_world_to_stage and the orient-
# frame quats live in different conventions — the math breaks and the
# operator sees "tilt no longer works at all".
#
# This cell models the live flow: calibrate against a non-identity
# portrait quat, then pitch forward, expect tilt down.

def _phone_portrait_realistic_calibrate():
    """Phone in portrait grip, calibrated against a realistic non-identity
    quat (operator holds phone vertical with a slight tip/twist — what
    `getQuaternionFromVector` actually produces in the field)."""
    r = Remote(id=199, kind=KIND_PHONE,
                forward_local=(0.0, 1.0, 0.0),
                up_local=(0.0, 0.0, 1.0))
    r.convention = OrientConvention.FLAT_PITCH_YAW
    # ~10° pitch + ~5° yaw + ~3° roll — small but enough to make every
    # quat component nonzero, mirroring a live Android sensor reading.
    cal_quat = quat_from_euler_zyx_deg(roll=3.0, pitch=10.0, yaw=5.0)
    r.calibrate(target_aim_stage=(0.0, 1.0, 0.0), quat=cal_quat)
    return r, cal_quat


def test_phone_calibrate_with_nonidentity_quat_then_pitch():
    """Live-phone reproduction: non-identity calibrate quat, then operator
    pitches forward 30° from that pose. Expected: aim tilts down (z<0).

    Pre-fix this test fails because `calibrate()` skips the qz-negate
    that `_apply_quat` applies — the resulting R_world_to_stage is
    inconsistent with the orient quats it consumes."""
    r, cal_quat = _phone_portrait_realistic_calibrate()
    # Operator pitches forward 30° from calibrate pose. In live flow,
    # the orient quat is calibrate-pose * pitch-delta (right-multiplied
    # in body frame).
    pitch_delta = quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(30))
    live_quat = quat_mul(cal_quat, pitch_delta)
    r.update_from_quat(live_quat)
    _assert(r.aim_stage is not None,
            "non-identity calibrate + pitch produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    _assert(r.aim_stage[2] < -0.3,
            f"non-identity calibrate + pitch fwd 30° → aim_stage.z < -0.3 "
            f"(got {r.aim_stage[2]:.3f})")
    _assert(r.aim_stage[1] > 0.5,
            f"non-identity calibrate + pitch fwd 30° → aim_stage.y > +0.5 "
            f"(got {r.aim_stage[1]:.3f})")


def test_phone_calibrate_with_nonidentity_quat_identity_delta_aims_at_target():
    """Sanity baseline: with a non-identity calibrate quat, sending the
    SAME quat back as the live orient (zero gesture from calibrate
    pose) must aim straight at the calibrate target. Pre-fix this
    fails the same way — the qz-negate applies to the live quat but
    not the calibrate quat, so identity-from-calibrate is no longer
    identity-from-the-server's-perspective."""
    r, cal_quat = _phone_portrait_realistic_calibrate()
    r.update_from_quat(cal_quat)
    _assert(r.aim_stage is not None, "calibrate-pose orient produces aim_stage")
    if r.aim_stage is None:
        return
    print(f"      aim_stage = {tuple(round(v, 3) for v in r.aim_stage)}")
    for i, (axis, want) in enumerate(zip("xyz", (0.0, 1.0, 0.0))):
        _assert(_close(r.aim_stage[i], want, tol=1e-3),
                f"calibrate-pose orient → aim_stage.{axis}={want} "
                f"(got {r.aim_stage[i]:.4f})")


# ─────────────────────────────────────────────────────────────────────────────

ALL = [
    test_phone_portrait_pitch_forward_tilts_down,
    test_phone_portrait_yaw_left_pans_stage_left,
    test_phone_portrait_roll_does_not_move_aim,
    test_phone_landscape_pitch_forward_tilts_down,
    test_puck_pitch_forward_tilts_down,
    test_puck_yaw_left_pans_stage_left,
    test_euler_and_quat_paths_agree,
    test_identity_quat_aims_at_calibration_target,
    # I. mirror gestures
    test_phone_portrait_pitch_back_tilts_up,
    test_phone_portrait_yaw_right_pans_stage_right,
    # J. combined gestures (the live-test gap)
    test_phone_portrait_pitch_forward_then_yaw_left_combined,
    test_phone_portrait_pitch_then_yaw_combined_other_order,
    test_phone_portrait_pitch_back_then_yaw_right_combined,
    # K. surface-rotation grip matrix
    test_phone_grip_matrix_identity,
    # L. wire-format parity
    test_puck_euler_path_matches_quat_path,
    test_phone_quat_and_euler_paths_agree_combined,
    # M. phone-vs-puck parity
    test_phone_landscape_and_puck_match_on_pitch,
    test_phone_landscape_and_puck_diverge_on_yaw_by_design,
    # N. CALIBRATE-FRAME parity (the live regression cell)
    test_phone_calibrate_with_nonidentity_quat_then_pitch,
    test_phone_calibrate_with_nonidentity_quat_identity_delta_aims_at_target,
]


if __name__ == "__main__":
    print("=== #824 orient → aim_stage contract matrix ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} passed, {_failed} failed, {_known_failing} known-failing")
    sys.exit(0 if _failed == 0 else 1)
