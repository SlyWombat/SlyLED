#!/usr/bin/env python3
"""diag_orient_transitions.py — print intermediate values for the two
sign-error patterns the contract matrix exposed (#824 redesign).

Pattern 1: yaw gesture → aim_stage.x reversed (phone AND puck).
Pattern 2: pitch gesture → aim_stage.z reversed when forward_local has
           +X component (landscape phone + puck), but NOT for portrait
           phone (forward_local = +Y).

Prints raw numbers at every transition (input quat → f_remote in world
→ R_world_to_stage matrix → aim_stage). No assertions — operator reads
the numbers and identifies which transition is physically wrong relative
to their intent.

Run:  python -X utf8 tests/diag_orient_transitions.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from remote_math import (
    quat_from_axis_angle,
    quat_rotate_vec,
    matrix_vec_mul,
    cross3,
)
from remote_orientation import (
    KIND_PHONE,
    KIND_PUCK,
    OrientConvention,
    Remote,
)


def _fmt_v(v, n=4):
    return tuple(round(x, n) for x in v)


def _fmt_m(M, n=4):
    return [[round(M[i][j], n) for j in range(3)] for i in range(3)]


def _fmt_q(q, n=4):
    return tuple(round(x, n) for x in q)


def _trace(label, kind, forward_local, up_local, calib_quat, live_quat,
            convention=OrientConvention.FLAT_PITCH_YAW):
    print(f"\n=== {label} ===")
    print(f"  kind          = {kind}")
    print(f"  forward_local = {forward_local}")
    print(f"  up_local      = {up_local}")
    print(f"  calib_quat    = {_fmt_q(calib_quat)}")
    print(f"  live_quat     = {_fmt_q(live_quat)}")

    r = Remote(id=1, kind=kind,
                forward_local=forward_local,
                up_local=up_local)
    r.convention = convention

    # 1. f_world / u_world at calibrate time (raw, pre-#824 negate).
    f_world_calib = quat_rotate_vec(calib_quat, forward_local)
    u_world_calib = quat_rotate_vec(calib_quat, up_local)
    print(f"  → f_world(calib) = {_fmt_v(f_world_calib)}")
    print(f"  → u_world(calib) = {_fmt_v(u_world_calib)}")

    # 2. Calibrate: builds R_world_to_stage so that f_world(calib) → (0,1,0).
    r.calibrate(target_aim_stage=(0.0, 1.0, 0.0), quat=calib_quat)
    R = r.R_world_to_stage  # quaternion (w,x,y,z)
    print(f"  → R_world_to_stage (quat) = {_fmt_q(R)}")
    # As a sanity check, show what R does to world basis vectors:
    print(f"     R·(+X) = {_fmt_v(quat_rotate_vec(R, (1.0, 0.0, 0.0)))}")
    print(f"     R·(+Y) = {_fmt_v(quat_rotate_vec(R, (0.0, 1.0, 0.0)))}")
    print(f"     R·(+Z) = {_fmt_v(quat_rotate_vec(R, (0.0, 0.0, 1.0)))}")

    # 3. Live update — note: _apply_quat applies kind-specific qz-negate
    #    for KIND_PHONE before storing in last_quat_world. We capture the
    #    EFFECTIVE quat the math sees by reading last_quat_world.
    r.update_from_quat(live_quat)
    effective_q = r.last_quat_world
    print(f"  → effective_quat = {_fmt_q(effective_q)}  "
          f"({'qz-negated' if kind == KIND_PHONE else 'raw'})")

    # 4. f_world / u_world at live time.
    f_world_live = quat_rotate_vec(effective_q, forward_local)
    u_world_live = quat_rotate_vec(effective_q, up_local)
    print(f"  → f_world(live)  = {_fmt_v(f_world_live)}")
    print(f"  → u_world(live)  = {_fmt_v(u_world_live)}")

    # 5. aim_stage = R_world_to_stage * f_world(live).
    print(f"  → aim_stage      = {_fmt_v(r.aim_stage)}")
    print(f"  → up_stage       = {_fmt_v(r.up_stage)}")


# ── Pattern 1: yaw gesture → aim_stage.x reversed.

# 1A. Phone portrait, calibrated against identity, yaw +30° around world +Z.
#     Operator's intent: pointer turns to operator's left.
#     Test B's expectation: aim_stage.x ≈ +0.5 (stage-LEFT per CLAUDE.md).
print("\n────── PATTERN 1: yaw → aim_stage.x ──────")
_trace(
    "Pattern1A: phone portrait, +30° world-Z yaw",
    kind=KIND_PHONE,
    forward_local=(0.0, 1.0, 0.0),
    up_local=(0.0, 0.0, 1.0),
    calib_quat=(1.0, 0.0, 0.0, 0.0),
    live_quat=quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(30)),
)

# 1B. Puck (LCD-up, forward=+X), same +30° world-Z yaw.
_trace(
    "Pattern1B: puck LCD-up, +30° world-Z yaw",
    kind=KIND_PUCK,
    forward_local=(1.0, 0.0, 0.0),
    up_local=(0.0, 0.0, 1.0),
    calib_quat=(1.0, 0.0, 0.0, 0.0),
    live_quat=quat_from_axis_angle((0.0, 0.0, 1.0), +math.radians(30)),
)

# ── Pattern 2: pitch → aim_stage.z reversed (only forward=(1,0,0) cases).

# 2A. Phone portrait, pitch -30° around world +X. forward=(0,1,0), no flip.
print("\n────── PATTERN 2: pitch → aim_stage.z ──────")
_trace(
    "Pattern2A: phone portrait, -30° world-X pitch (forward=+Y)",
    kind=KIND_PHONE,
    forward_local=(0.0, 1.0, 0.0),
    up_local=(0.0, 0.0, 1.0),
    calib_quat=(1.0, 0.0, 0.0, 0.0),
    live_quat=quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(30)),
)

# 2B. Phone landscape, pitch -30° around world +Y. forward=(1,0,0), FLIPS.
_trace(
    "Pattern2B: phone landscape, -30° world-Y pitch (forward=+X)",
    kind=KIND_PHONE,
    forward_local=(1.0, 0.0, 0.0),
    up_local=(0.0, 0.0, 1.0),
    calib_quat=(1.0, 0.0, 0.0, 0.0),
    live_quat=quat_from_axis_angle((0.0, 1.0, 0.0), -math.radians(30)),
)

# 2C. Puck LCD-up, pitch -30° around world +Y. forward=(1,0,0), FLIPS.
_trace(
    "Pattern2C: puck LCD-up, -30° world-Y pitch (forward=+X)",
    kind=KIND_PUCK,
    forward_local=(1.0, 0.0, 0.0),
    up_local=(0.0, 0.0, 1.0),
    calib_quat=(1.0, 0.0, 0.0, 0.0),
    live_quat=quat_from_axis_angle((0.0, 1.0, 0.0), -math.radians(30)),
)
