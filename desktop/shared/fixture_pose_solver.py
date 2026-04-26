"""fixture_pose_solver.py — #699.

Least-squares fit of fixture position (X_fx, Y_fx, Z_fx) from N≥2
observations of the form (pan_norm, tilt_norm, marker_xyz). Produced by
the operator-driven Verify-Pose wizard: drive beam at a known surveyed
ArUco marker, nudge until it lands ON the marker, record the pan/tilt
that aimed it there. Solving for fixture position is now a linear
problem because the floor (z=0) intersection gives:

    hit_x = X_fx + dx * t_i           where t_i = −Z_fx / dz
    hit_y = Y_fx + dy * t_i

Substituting and rearranging (linear in [X_fx, Y_fx, Z_fx]):

    X_fx + 0·Y_fx + (−dx/dz)·Z_fx = marker_x
    0·X_fx + Y_fx + (−dy/dz)·Z_fx = marker_y

With N markers we get 2N rows in 3 unknowns. Standard least-squares
via the normal equations. Pure Python; no numpy dependency.

Use ``solve_fixture_pose(observations, fixture_rotation_deg,
pan_range_deg, tilt_range_deg)`` to fit; returns
``{x, y, z, residualRmsMm, perMarker}``.
"""
from __future__ import annotations

import math


def _pan_tilt_to_ray(pan_norm, tilt_norm, pan_range_deg, tilt_range_deg,
                      rotation_deg):
    """Local copy of mover_calibrator.pan_tilt_to_ray to keep this
    module dependency-free of the cal pipeline. Returns a unit vector
    in stage coordinates."""
    pan_deg = (pan_norm - 0.5) * pan_range_deg
    tilt_deg = (tilt_norm - 0.5) * tilt_range_deg
    pan_rad = math.radians(pan_deg)
    tilt_rad = math.radians(tilt_deg)
    cos_tilt = math.cos(tilt_rad)
    dx = math.sin(pan_rad) * cos_tilt
    dy = math.cos(pan_rad) * cos_tilt
    dz = -math.sin(tilt_rad)
    if rotation_deg is None or all(r == 0 for r in rotation_deg):
        return (dx, dy, dz)
    # XYZ intrinsic Euler — match mover_calibrator.pan_tilt_to_ray.
    rx, ry, rz = (math.radians(float(a)) for a in rotation_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    Ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    Rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))

    def matmul(A, B):
        return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                            for j in range(3))
                      for i in range(3))

    R = matmul(matmul(Rx, Ry), Rz)
    rx_, ry_, rz_ = (
        R[0][0]*dx + R[0][1]*dy + R[0][2]*dz,
        R[1][0]*dx + R[1][1]*dy + R[1][2]*dz,
        R[2][0]*dx + R[2][1]*dy + R[2][2]*dz,
    )
    return (rx_, ry_, rz_)


def _solve_3x3(M, b):
    """Solve M·x = b for a 3×3 system via straight Gaussian elimination
    with partial pivoting. Returns the solution list or None when
    singular."""
    A = [row[:] + [b[i]] for i, row in enumerate(M)]
    n = 3
    for c in range(n):
        # Pivot on largest |A[r][c]| in rows c..n-1.
        pivot = c
        for r in range(c + 1, n):
            if abs(A[r][c]) > abs(A[pivot][c]):
                pivot = r
        if abs(A[pivot][c]) < 1e-12:
            return None
        A[c], A[pivot] = A[pivot], A[c]
        # Eliminate.
        for r in range(c + 1, n):
            f = A[r][c] / A[c][c]
            for k in range(c, n + 1):
                A[r][k] -= f * A[c][k]
    # Back-substitute.
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (A[r][n] - sum(A[r][c] * x[c] for c in range(r + 1, n))) / A[r][r]
    return x


def solve_fixture_pose(observations,
                        fixture_rotation_deg=None,
                        pan_range_deg=540.0, tilt_range_deg=270.0):
    """Solve (X_fx, Y_fx, Z_fx) from a list of operator observations.

    Each observation is a dict with keys ``panNorm``, ``tiltNorm``,
    ``markerXYZ`` (e.g. ``[1150.0, 2100.0, 0.0]``). The marker's
    Z is expected to be 0 (floor markers); the helper computes the
    floor-hit point of the beam ray and fits fixture pose by minimising
    the sum of squared (hit − marker) residuals.

    Returns:
        ``{x, y, z, residualRmsMm, perMarker: [{markerId, predicted,
          observed, errorMm}, ...]}``
        or ``{error: <reason>}`` when the solve fails.
    """
    if not observations or len(observations) < 2:
        return {"error": "need at least 2 marker observations"}

    rotation = list(fixture_rotation_deg or [0.0, 0.0, 0.0])
    # Build 2N rows in 3 unknowns: row x is [1, 0, -dx/dz] = marker_x;
    # row y is [0, 1, -dy/dz] = marker_y.
    rows = []
    rhs = []
    cached_dirs = []
    for obs in observations:
        try:
            pan = float(obs["panNorm"])
            tilt = float(obs["tiltNorm"])
            mx, my, _ = (float(v) for v in obs["markerXYZ"])
        except (KeyError, TypeError, ValueError) as e:
            return {"error": f"invalid observation: {e}"}
        d = _pan_tilt_to_ray(pan, tilt, pan_range_deg, tilt_range_deg, rotation)
        if d[2] >= -1e-6:
            # Beam aims up or horizontal — can't intersect floor; skip.
            continue
        cached_dirs.append((d, (mx, my)))
        rows.append([1.0, 0.0, -d[0] / d[2]])
        rows.append([0.0, 1.0, -d[1] / d[2]])
        rhs.append(mx)
        rhs.append(my)
    if len(rows) < 3:
        return {"error": "no observation produced a valid floor ray"}

    # Normal equations: (Aᵀ·A)·x = Aᵀ·b.
    AtA = [[0.0] * 3 for _ in range(3)]
    Atb = [0.0] * 3
    for r, b_val in zip(rows, rhs):
        for i in range(3):
            Atb[i] += r[i] * b_val
            for j in range(3):
                AtA[i][j] += r[i] * r[j]
    sol = _solve_3x3(AtA, Atb)
    if sol is None:
        return {"error": "linear solve failed (markers may be collinear "
                          "with fixture)"}
    X_fx, Y_fx, Z_fx = sol

    # Compute per-marker residuals + RMS.
    per_marker = []
    sq_sum = 0.0
    n = 0
    for (d, (mx, my)), obs in zip(cached_dirs, observations):
        t = -Z_fx / d[2] if d[2] != 0 else 0.0
        hit_x = X_fx + d[0] * t
        hit_y = Y_fx + d[1] * t
        err = math.hypot(hit_x - mx, hit_y - my)
        sq_sum += err * err
        n += 1
        per_marker.append({
            "markerId": obs.get("markerId"),
            "observed": [mx, my, 0.0],
            "predicted": [round(hit_x, 1), round(hit_y, 1), 0.0],
            "errorMm": round(err, 1),
        })
    rms = math.sqrt(sq_sum / n) if n else 0.0
    return {
        "x": round(X_fx, 1),
        "y": round(Y_fx, 1),
        "z": round(Z_fx, 1),
        "residualRmsMm": round(rms, 1),
        "perMarker": per_marker,
        "observationsUsed": n,
    }
