"""aim/_rotmat.py — rotation primitives for the aim package (#784, #785).

Self-contained copies of the mount-frame ↔ stage-frame transforms so
the aim package has no imports from `coverage_math` or any other legacy
module. Convention matches CLAUDE.md `## Rotation convention (#586, #600)`:
Z-up stage frame, `R = Rz_lh(rz) · Rx(rx) · Ry(ry)`.

Once `coverage_math.py` is renamed to `camera_coverage.py` in #784 PR-7
these primitives stay here — they're the aim package's own.
"""

import math


def mount_rotation(rotation):
    """Build the 3×3 rotation matrix that maps mount-frame vectors →
    stage-frame vectors.

    Conventions:
      - `rx > 0` → fixture pitched DOWN (forward axis tips toward stage -Z).
      - `ry > 0` → roll about forward axis (clockwise from behind).
      - `rz > 0` → yaw about stage-up; aims toward stage +X (stage-left).
    """
    rx = math.radians(float(rotation[0]) if len(rotation) > 0 else 0.0)
    ry = math.radians(float(rotation[1]) if len(rotation) > 1 else 0.0)
    rz = math.radians(float(rotation[2]) if len(rotation) > 2 else 0.0)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = [[1.0, 0.0, 0.0],
          [0.0,  cx,  sx],
          [0.0, -sx,  cx]]
    Ry = [[ cy, 0.0,  sy],
          [0.0, 1.0, 0.0],
          [-sy, 0.0,  cy]]
    Rz = [[ cz,  sz, 0.0],
          [-sz,  cz, 0.0],
          [0.0, 0.0, 1.0]]
    return _mm(Rz, _mm(Rx, Ry))


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def matvec(M, v):
    return (M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2])


def transpose(M):
    return [[M[0][0], M[1][0], M[2][0]],
            [M[0][1], M[1][1], M[2][1]],
            [M[0][2], M[1][2], M[2][2]]]
