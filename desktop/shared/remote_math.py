"""Quaternion + rotation helpers for the remote-orientation primitive.

Pure-Python stdlib (math only). Vectors are 3-tuples; quaternions are
4-tuples in (w, x, y, z) order. See docs/gyro-stage-space.md for the
architecture this supports.

Conventions:
- Stage frame: X = width, Y = depth (forward), Z = height (up).
- Fixture `rotation = [rx, ry, rz]` (degrees) uses intrinsic XYZ order
  (`R = Rx * Ry * Rz`) to match Three.js default Euler and the existing
  baker / scene-3d.js code.
- Sensor Euler (ESP32 roll/pitch/yaw, Android fallback) uses ZYX intrinsic
  aerospace order (`R = Rz(yaw) * Ry(pitch) * Rx(roll)`).
"""

import math

__all__ = [
    "quat_mul", "quat_conj", "quat_normalize", "quat_rotate_vec",
    "quat_from_axis_angle", "quat_from_euler_zyx_deg", "quat_from_to",
    "frame_align",
    "dot3", "cross3", "norm3", "normalize3",
    "euler_xyz_deg_to_matrix", "matrix_vec_mul", "matrix_transpose",
]

_EPS = 1e-12


def dot3(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def cross3(a, b):
    return (a[1]*b[2] - a[2]*b[1],
            a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0])


def norm3(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])


def normalize3(v):
    n = norm3(v)
    if n < _EPS:
        return (0.0, 0.0, 0.0)
    return (v[0]/n, v[1]/n, v[2]/n)


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw*bw - ax*bx - ay*by - az*bz,
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
    )


def quat_conj(q):
    w, x, y, z = q
    return (w, -x, -y, -z)


def quat_normalize(q):
    w, x, y, z = q
    n = math.sqrt(w*w + x*x + y*y + z*z)
    if n < _EPS:
        return (1.0, 0.0, 0.0, 0.0)
    return (w/n, x/n, y/n, z/n)


def quat_rotate_vec(q, v):
    """Rotate a 3-vector by a unit quaternion.

    Uses the efficient form `v' = v + 2 * u x (u x v + w * v)` where
    u = (qx, qy, qz) and w = qw.
    """
    qw, qx, qy, qz = q
    vx, vy, vz = v
    # c1 = u x v
    c1x = qy*vz - qz*vy
    c1y = qz*vx - qx*vz
    c1z = qx*vy - qy*vx
    # t  = u x v + w * v
    tx = c1x + qw*vx
    ty = c1y + qw*vy
    tz = c1z + qw*vz
    # c2 = u x t
    c2x = qy*tz - qz*ty
    c2y = qz*tx - qx*tz
    c2z = qx*ty - qy*tx
    return (vx + 2.0*c2x, vy + 2.0*c2y, vz + 2.0*c2z)


def quat_from_axis_angle(axis, angle_rad):
    ax, ay, az = axis
    n = math.sqrt(ax*ax + ay*ay + az*az)
    if n < _EPS:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle_rad * 0.5) / n
    return (math.cos(angle_rad * 0.5), ax*s, ay*s, az*s)


def quat_from_euler_zyx_deg(roll, pitch, yaw):
    """ZYX intrinsic Euler → quaternion. Aerospace convention.

    Equivalent to `R = Rz(yaw) * Ry(pitch) * Rx(roll)`.
    Inputs in degrees.
    """
    hr = math.radians(roll) * 0.5
    hp = math.radians(pitch) * 0.5
    hy = math.radians(yaw) * 0.5
    cr, sr = math.cos(hr), math.sin(hr)
    cp, sp = math.cos(hp), math.sin(hp)
    cy, sy = math.cos(hy), math.sin(hy)
    return (
        cr*cp*cy + sr*sp*sy,
        sr*cp*cy - cr*sp*sy,
        cr*sp*cy + sr*cp*sy,
        cr*cp*sy - sr*sp*cy,
    )


def quat_from_to(a, b):
    """Minimum-angle quaternion rotating unit vector a onto unit vector b.

    Both inputs are normalised internally. For antiparallel inputs
    (dot = -1) an arbitrary perpendicular axis is chosen for the
    180° rotation.
    """
    a = normalize3(a)
    b = normalize3(b)
    d = dot3(a, b)
    if d >= 1.0 - 1e-8:
        return (1.0, 0.0, 0.0, 0.0)
    if d <= -1.0 + 1e-8:
        # Opposite — pick any perpendicular axis.
        if abs(a[0]) < 0.9:
            perp = normalize3(cross3(a, (1.0, 0.0, 0.0)))
        else:
            perp = normalize3(cross3(a, (0.0, 1.0, 0.0)))
        return (0.0, perp[0], perp[1], perp[2])
    axis = cross3(a, b)
    s = math.sqrt((1.0 + d) * 2.0)
    inv_s = 1.0 / s
    return (s * 0.5, axis[0]*inv_s, axis[1]*inv_s, axis[2]*inv_s)


def frame_align(f_src, u_src, f_dst, u_dst):
    """Rotation mapping source frame (f_src, u_src) onto destination (f_dst, u_dst).

    The forward axes are aligned first (minimum-angle rotation). The "up"
    axes are then aligned within the plane perpendicular to f_dst, which
    leaves the forward alignment untouched. Returns a unit quaternion.

    See §4.1 of docs/gyro-stage-space.md for the role this plays in
    calibration.
    """
    q1 = quat_from_to(f_src, f_dst)
    u_src_rot = quat_rotate_vec(q1, u_src)
    # Project both "up" vectors onto the plane perpendicular to f_dst.
    d_src = dot3(u_src_rot, f_dst)
    d_dst = dot3(u_dst, f_dst)
    u_src_proj = normalize3((u_src_rot[0] - d_src*f_dst[0],
                             u_src_rot[1] - d_src*f_dst[1],
                             u_src_rot[2] - d_src*f_dst[2]))
    u_dst_proj = normalize3((u_dst[0] - d_dst*f_dst[0],
                             u_dst[1] - d_dst*f_dst[1],
                             u_dst[2] - d_dst*f_dst[2]))
    if norm3(u_src_proj) < _EPS or norm3(u_dst_proj) < _EPS:
        # Up vectors are parallel to forward — no secondary alignment
        # possible, return forward-only rotation.
        return q1
    q2 = quat_from_to(u_src_proj, u_dst_proj)
    return quat_mul(q2, q1)


def euler_xyz_deg_to_matrix(rot_deg):
    """Build a 3×3 rotation matrix for a mount rotation.

    `rot_deg = [rx, ry, rz]` in degrees, interpreted as intrinsic XYZ
    per Three.js default Euler order. `R = Rx(rx) * Ry(ry) * Rz(rz)`,
    applied to a mount-local vector as `v_stage = R @ v_mount`.
    """
    rx = math.radians(rot_deg[0])
    ry = math.radians(rot_deg[1])
    rz = math.radians(rot_deg[2])
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # Ry * Rz
    m00 = cy*cz;  m01 = -cy*sz;  m02 = sy
    m10 = sz;     m11 = cz;      m12 = 0.0
    m20 = -sy*cz; m21 = sy*sz;   m22 = cy
    # Rx * (Ry*Rz)
    r00 = m00
    r01 = m01
    r02 = m02
    r10 = cx*m10 - sx*m20
    r11 = cx*m11 - sx*m21
    r12 = cx*m12 - sx*m22
    r20 = sx*m10 + cx*m20
    r21 = sx*m11 + cx*m21
    r22 = sx*m12 + cx*m22
    return [[r00, r01, r02],
            [r10, r11, r12],
            [r20, r21, r22]]


def matrix_vec_mul(m, v):
    return (m[0][0]*v[0] + m[0][1]*v[1] + m[0][2]*v[2],
            m[1][0]*v[0] + m[1][1]*v[1] + m[1][2]*v[2],
            m[2][0]*v[0] + m[2][1]*v[1] + m[2][2]*v[2])


def matrix_transpose(m):
    return [[m[0][0], m[1][0], m[2][0]],
            [m[0][1], m[1][1], m[2][1]],
            [m[0][2], m[1][2], m[2][2]]]
