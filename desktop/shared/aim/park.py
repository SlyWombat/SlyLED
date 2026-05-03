"""aim/park.py — `go_home(fid)` helper (#781, #782 PR-β).

Drives a moving-head fixture to its Home anchor by writing
`(homePanDmx16, homeTiltDmx16)` directly. Home is the operator-saved
DMX pose (whatever they captured during Save Home — could be at any
elevation). Pre-#798 this helper went through `aim_direction(0, 0)`
which only worked because the old sphere conflated mount-body
orientation and home aim direction; under #798's anchor-based model
home aim is captured per-fixture and need not be at stage `(0, 0)`.

The helper is the single park entry point — every "abort / cancel /
error / shutdown / claim-release" path that wants to park the head
goes through here.

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
    """Drive fixture <fid> to its Home anchor by writing the recorded
    `(homePanDmx16, homeTiltDmx16)` directly.

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
    h_pan = f.get("homePanDmx16")
    h_tilt = f.get("homeTiltDmx16")
    if h_pan is None or h_tilt is None:
        return None
    if get_engine() is None:
        return None
    pan_dmx16 = int(h_pan)
    tilt_dmx16 = int(h_tilt)
    try:
        write_pose(int(f.get("dmxUniverse", 1) or 1),
                    int(f.get("dmxStartAddr", 1) or 1),
                    pan_dmx16, tilt_dmx16, prof_info)
    except Exception:
        return None
    return (pan_dmx16, tilt_dmx16)
