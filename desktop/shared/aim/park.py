"""aim/park.py — `go_home(fid)` helper (#781, #782 PR-β).

Drives a moving-head fixture to its Home anchor via the canonical aim
path. Home is the angular zero by definition (per #784 c3), so
`AimSphere.aim_direction(0, 0)` returns ≈ `(homePanDmx16,
homeTiltDmx16)` — the operator-saved "rotation-forward, level-horizon"
pose. The helper is the single park entry point — every "abort /
cancel / error / shutdown / claim-release" path that wants to park the
head goes through here.

Lamps stay where they are. Caller composes `go_home()` with `lamp_off()`
for a "park and dark" operation. Per the #782 operator decision on
`/api/dmx/blackout`: blackout = lamps off, pan/tilt UNTOUCHED, head
stays where it was. A future "park heads home AND lights off"
operation builds from `go_home()` + `lamp_off()` together.

The helper depends on the same dependency-injected `get_fixtures` /
`profile_lib` / `write_pose` / `get_engine` callables that
`aim/routes.register(...)` consumes, so it stays Flask-free at the
leaf and reuses the per-fixture sphere cache from `aim/routes`.
"""


def go_home(fid, *, get_fixtures, profile_lib, write_pose, get_engine):
    """Drive fixture <fid> to its Home anchor via `AimSphere.aim_direction(0, 0)`.

    Returns `(pan_dmx16, tilt_dmx16)` of the written pose, or `None`
    when the fixture is missing / not a mover / has no Home anchor /
    has no profile / no engine running. Lamps untouched.
    """
    f = next((x for x in get_fixtures() if x.get("id") == fid), None)
    if f is None or f.get("fixtureType") != "dmx":
        return None
    pid = f.get("dmxProfileId")
    prof_info = profile_lib.channel_info(pid) if pid else None
    if not prof_info:
        return None
    if f.get("homePanDmx16") is None or f.get("homeTiltDmx16") is None:
        return None
    if get_engine() is None:
        return None
    # Reuse the per-fixture sphere cache from aim/routes — same key
    # (fid, home, rotation, profileId) so we don't double-build.
    from .routes import _get_or_build_sphere
    try:
        sphere = _get_or_build_sphere(f, prof_info)
    except Exception:
        return None
    # Home IS the angular zero — `aim_direction(0, 0)` lands at the
    # home DMX values (within bilinear-blend cell tolerance). Returns
    # None only when the cell index is empty, which can't happen for a
    # fixture that constructed cleanly.
    pose = sphere.aim_direction(0.0, 0.0)
    if pose is None:
        # Belt-and-suspenders: fall back to the recorded home anchor
        # so a degenerate sphere never strands the head off-axis.
        pose = (sphere.home_pan_dmx16, sphere.home_tilt_dmx16)
    pan_dmx16, tilt_dmx16 = pose
    try:
        write_pose(int(f.get("dmxUniverse", 1) or 1),
                    int(f.get("dmxStartAddr", 1) or 1),
                    pan_dmx16, tilt_dmx16, prof_info)
    except Exception:
        return None
    return (pan_dmx16, tilt_dmx16)
