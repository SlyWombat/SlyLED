"""Unit tests for remote_math + extended mover_calibrator helpers.

Part of the gyro/phone stage-space architecture (#484, phase 1 — math foundation).
See docs/gyro-stage-space.md.

Run:
    python -X utf8 tests/test_remote_math.py
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from remote_math import (  # noqa: E402
    cross3, dot3, norm3, normalize3,
    quat_mul, quat_conj, quat_normalize, quat_rotate_vec,
    quat_from_axis_angle, quat_from_euler_zyx_deg, quat_from_to,
    frame_align,
    euler_xyz_deg_to_matrix, matrix_vec_mul, matrix_transpose,
)
# #784 PR-7: `mover_calibrator` deleted. The legacy
# `pan_tilt_to_ray` / `aim_to_pan_tilt` round-trip tests below were
# removed when their target module disappeared; the new aim model
# (`desktop/shared/aim/sphere.py`) is covered by `tests/aim/`.

# ── Tiny test harness ─────────────────────────────────────────────────────

_passed = 0
_failed = 0


def _eq(a, b, tol=1e-9, msg=""):
    global _passed, _failed
    if isinstance(a, (tuple, list)):
        ok = len(a) == len(b) and all(abs(x - y) < tol for x, y in zip(a, b))
    else:
        ok = abs(a - b) < tol
    if ok:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}: {a} != {b} (tol={tol})")


def _true(cond, msg=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


# ── Vector primitives ─────────────────────────────────────────────────────

def test_vec_basics():
    _eq(dot3((1, 0, 0), (0, 1, 0)), 0, msg="dot perpendicular")
    _eq(dot3((1, 2, 3), (4, 5, 6)), 32, msg="dot general")
    _eq(cross3((1, 0, 0), (0, 1, 0)), (0, 0, 1), msg="cross x*y = z")
    _eq(cross3((0, 1, 0), (1, 0, 0)), (0, 0, -1), msg="cross y*x = -z")
    _eq(norm3((3, 4, 0)), 5.0, msg="norm 3-4-5")
    _eq(normalize3((0, 2, 0)), (0, 1, 0), msg="normalize")
    _eq(normalize3((0, 0, 0)), (0, 0, 0), msg="normalize zero")


# ── Quaternion primitives ─────────────────────────────────────────────────

def test_quat_mul_identity():
    q = (0.5, 0.5, 0.5, 0.5)
    _eq(quat_mul(q, (1, 0, 0, 0)), q, msg="q * I")
    _eq(quat_mul((1, 0, 0, 0), q), q, msg="I * q")


def test_quat_conj_roundtrip():
    q = quat_normalize((0.1, 0.3, -0.4, 0.8))
    r = quat_mul(q, quat_conj(q))
    _eq(r, (1, 0, 0, 0), tol=1e-9, msg="q * q_conj = I for unit q")


def test_quat_rotate_identity():
    for v in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0.3, -0.4, 0.5)]:
        _eq(quat_rotate_vec((1, 0, 0, 0), v), v, msg=f"I rotates {v}")


def test_quat_rotate_90z():
    # 90° rotation about Z: x→y, y→-x, z→z
    q = quat_from_axis_angle((0, 0, 1), math.radians(90))
    _eq(quat_rotate_vec(q, (1, 0, 0)), (0, 1, 0), tol=1e-9, msg="Rz(90) x → y")
    _eq(quat_rotate_vec(q, (0, 1, 0)), (-1, 0, 0), tol=1e-9, msg="Rz(90) y → -x")
    _eq(quat_rotate_vec(q, (0, 0, 1)), (0, 0, 1), tol=1e-9, msg="Rz(90) z → z")


def test_quat_rotate_neg90x():
    # Right-hand rule about +X: Rx(+90) sends y→z and z→-y; so Rx(-90)
    # sends y→-z and z→y.
    q = quat_from_axis_angle((1, 0, 0), math.radians(-90))
    _eq(quat_rotate_vec(q, (0, 1, 0)), (0, 0, -1), tol=1e-9, msg="Rx(-90) y → -z")
    _eq(quat_rotate_vec(q, (0, 0, 1)), (0, 1, 0), tol=1e-9, msg="Rx(-90) z → y")
    _eq(quat_rotate_vec(q, (1, 0, 0)), (1, 0, 0), tol=1e-9, msg="Rx(-90) x → x")


def test_quat_from_euler_zyx_pure_axes():
    # Pure yaw = rotation about Z only
    q = quat_from_euler_zyx_deg(0, 0, 90)
    _eq(quat_rotate_vec(q, (1, 0, 0)), (0, 1, 0), tol=1e-9,
        msg="ZYX yaw=90: x → y")
    # Pure pitch = rotation about Y only
    q = quat_from_euler_zyx_deg(0, 90, 0)
    _eq(quat_rotate_vec(q, (0, 0, 1)), (1, 0, 0), tol=1e-9,
        msg="ZYX pitch=90: z → x")
    # Pure roll = rotation about X only
    q = quat_from_euler_zyx_deg(90, 0, 0)
    _eq(quat_rotate_vec(q, (0, 1, 0)), (0, 0, 1), tol=1e-9,
        msg="ZYX roll=90: y → z")


def test_quat_from_to_aligned():
    _eq(quat_from_to((1, 0, 0), (1, 0, 0)), (1, 0, 0, 0), msg="aligned → identity")


def test_quat_from_to_general():
    # 90° apart — quaternion rotates a onto b
    a = (1, 0, 0)
    b = (0, 1, 0)
    q = quat_from_to(a, b)
    r = quat_rotate_vec(q, a)
    _eq(r, b, tol=1e-9, msg="from_to x→y rotates correctly")


def test_quat_from_to_opposite():
    # Antiparallel — 180° rotation, any axis perpendicular
    q = quat_from_to((1, 0, 0), (-1, 0, 0))
    r = quat_rotate_vec(q, (1, 0, 0))
    _eq(r, (-1, 0, 0), tol=1e-9, msg="from_to antiparallel")


def test_quat_from_to_random():
    random.seed(42)
    for _ in range(25):
        a = normalize3((random.uniform(-1, 1),
                        random.uniform(-1, 1),
                        random.uniform(-1, 1)))
        b = normalize3((random.uniform(-1, 1),
                        random.uniform(-1, 1),
                        random.uniform(-1, 1)))
        if norm3(a) < 0.1 or norm3(b) < 0.1:
            continue
        q = quat_from_to(a, b)
        r = quat_rotate_vec(q, a)
        _eq(r, b, tol=1e-6, msg=f"from_to random {a}→{b}")


# ── Frame alignment ───────────────────────────────────────────────────────

def test_frame_align_identity():
    f = (0, 1, 0)  # stage +Y
    u = (0, 0, 1)  # stage +Z
    q = frame_align(f, u, f, u)
    _eq(q, (1, 0, 0, 0), tol=1e-9, msg="identical frames → identity")


def test_frame_align_maps_both():
    # Source frame: forward=+X, up=+Z
    # Dest frame: forward=+Y, up=+Z
    f_src, u_src = (1, 0, 0), (0, 0, 1)
    f_dst, u_dst = (0, 1, 0), (0, 0, 1)
    q = frame_align(f_src, u_src, f_dst, u_dst)
    _eq(quat_rotate_vec(q, f_src), f_dst, tol=1e-9, msg="align forward")
    _eq(quat_rotate_vec(q, u_src), u_dst, tol=1e-9, msg="align up")


def test_frame_align_diagonal():
    # Source frame: forward=+Y, up=+Z
    # Dest frame: forward=diagonal, up=stage up
    f_src, u_src = (0, 1, 0), (0, 0, 1)
    f_dst = normalize3((1, 1, -0.5))
    # "Up in stage" projected perpendicular to f_dst
    u_dst_raw = (0, 0, 1)
    d = dot3(u_dst_raw, f_dst)
    u_dst = normalize3((u_dst_raw[0] - d*f_dst[0],
                        u_dst_raw[1] - d*f_dst[1],
                        u_dst_raw[2] - d*f_dst[2]))
    q = frame_align(f_src, u_src, f_dst, u_dst)
    _eq(quat_rotate_vec(q, f_src), f_dst, tol=1e-9, msg="diag align forward")
    u_rot = quat_rotate_vec(q, u_src)
    # Projection of rotated up onto plane ⊥ f_dst should match u_dst
    d2 = dot3(u_rot, f_dst)
    u_rot_proj = normalize3((u_rot[0] - d2*f_dst[0],
                             u_rot[1] - d2*f_dst[1],
                             u_rot[2] - d2*f_dst[2]))
    _eq(u_rot_proj, u_dst, tol=1e-9, msg="diag align up (plane ⊥ forward)")


# ── Mount rotation matrix ─────────────────────────────────────────────────

def test_euler_xyz_identity():
    R = euler_xyz_deg_to_matrix([0, 0, 0])
    _eq(matrix_vec_mul(R, (1, 0, 0)), (1, 0, 0), msg="identity preserves +X")
    _eq(matrix_vec_mul(R, (0, 1, 0)), (0, 1, 0), msg="identity preserves +Y")
    _eq(matrix_vec_mul(R, (0, 0, 1)), (0, 0, 1), msg="identity preserves +Z")


def test_euler_xyz_pure_rx():
    # Pure rx = 90°: y→z, z→-y (right-hand rule, rotation about X)
    R = euler_xyz_deg_to_matrix([90, 0, 0])
    _eq(matrix_vec_mul(R, (0, 1, 0)), (0, 0, 1), tol=1e-9, msg="Rx(90) y → z")
    _eq(matrix_vec_mul(R, (0, 0, 1)), (0, -1, 0), tol=1e-9, msg="Rx(90) z → -y")


def test_euler_xyz_pure_ry():
    # Pure ry = 90°: z→x, x→-z
    R = euler_xyz_deg_to_matrix([0, 90, 0])
    _eq(matrix_vec_mul(R, (0, 0, 1)), (1, 0, 0), tol=1e-9, msg="Ry(90) z → x")
    _eq(matrix_vec_mul(R, (1, 0, 0)), (0, 0, -1), tol=1e-9, msg="Ry(90) x → -z")


def test_euler_xyz_pure_rz():
    # Pure rz = 90°: x→y, y→-x
    R = euler_xyz_deg_to_matrix([0, 0, 90])
    _eq(matrix_vec_mul(R, (1, 0, 0)), (0, 1, 0), tol=1e-9, msg="Rz(90) x → y")
    _eq(matrix_vec_mul(R, (0, 1, 0)), (-1, 0, 0), tol=1e-9, msg="Rz(90) y → -x")


def test_euler_xyz_180x():
    # Ceiling mount: rotate 180° about X. +Y → -Y, +Z → -Z.
    R = euler_xyz_deg_to_matrix([180, 0, 0])
    _eq(matrix_vec_mul(R, (0, 1, 0)), (0, -1, 0), tol=1e-9, msg="180x flips Y")
    _eq(matrix_vec_mul(R, (0, 0, 1)), (0, 0, -1), tol=1e-9, msg="180x flips Z")


def test_matrix_transpose_inverse():
    # For a rotation matrix, transpose == inverse.
    R = euler_xyz_deg_to_matrix([25, -40, 70])
    Rt = matrix_transpose(R)
    v = (0.3, -0.5, 0.8)
    v_roundtrip = matrix_vec_mul(Rt, matrix_vec_mul(R, v))
    _eq(v_roundtrip, v, tol=1e-9, msg="R^T R v = v")


# #784 PR-7: pan_tilt_to_ray / aim_to_pan_tilt tests removed when
# `mover_calibrator` was deleted. The new aim model
# (`desktop/shared/aim/sphere.py`) is covered by `tests/aim/test_sphere.py`.


# ── Run everything ────────────────────────────────────────────────────────

ALL = [
    test_vec_basics,
    test_quat_mul_identity, test_quat_conj_roundtrip,
    test_quat_rotate_identity, test_quat_rotate_90z, test_quat_rotate_neg90x,
    test_quat_from_euler_zyx_pure_axes,
    test_quat_from_to_aligned, test_quat_from_to_general,
    test_quat_from_to_opposite, test_quat_from_to_random,
    test_frame_align_identity, test_frame_align_maps_both,
    test_frame_align_diagonal,
    test_euler_xyz_identity,
    test_euler_xyz_pure_rx, test_euler_xyz_pure_ry, test_euler_xyz_pure_rz,
    test_euler_xyz_180x, test_matrix_transpose_inverse,
]


if __name__ == "__main__":
    for t in ALL:
        t()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
