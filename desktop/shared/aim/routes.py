"""aim/routes.py — moving-head aim HTTP endpoint (#784 PR-4, #785).

Single endpoint:

    POST /api/mover/<fid>/aim
        body: {x, y, z}                       — stage-mm world target
              | {azDeg, elDeg}                 — stage-frame direction
        query: ?prefer=closest|A|B            — branch policy (default closest)
               ?currentPanDmx16=&currentTiltDmx16=
                                              — explicit current_pose override
        response: 200 {ok, panDmx16, tiltDmx16}
                  400 {err: "no_home" | "no_profile" | "invalid_body"
                            | "invalid_fixture" | "degenerate_target"}
                  404 {err: "not_found" | "not_a_mover"}
                  409 {err: "calibrating"}
                  503 {err: "engine_not_running"}

Sphere instances are cached per-fixture, keyed on `(fid,
homePanDmx16, homeTiltDmx16, rotation_tuple, profile_id)`. Cache
invalidates automatically when any key element changes (rotation
edits, Home re-saves, profile swap). Eliminates the ~6 ms per-call
sphere rebuild for track-update workloads.

`current_pose` plumbing:
  1. `?currentPanDmx16=&currentTiltDmx16=` query overrides win.
  2. Else: read the fixture's last-written DMX from the engine's
     universe buffer at the channel offsets defined by the profile.
  3. Else (engine has no recorded pose yet): fall back to home anchor.
  The sphere never invents `current_pose`; the route handler is the
  canonical source.
"""

import math

from .sphere import AimSphere


_sphere_cache = {}   # fid → ((cache_key_tuple), AimSphere)


def _sphere_cache_key(f, prof_info):
    """Build the cache key for a fixture + profile pair. Includes the
    layout xyz because fixture position lives in `_layout.children`,
    not on the fixture record itself (#785 QA round-2 finding) — the
    cache must invalidate when the operator drags a fixture in the
    Layout tab."""
    rot = tuple(float(x) for x in (f.get("rotation") or [0, 0, 0])[:3])
    xyz = (float(f.get("x") or 0),
           float(f.get("y") or 0),
           float(f.get("z") or 0))
    return (
        int(f.get("homePanDmx16") or 0),
        int(f.get("homeTiltDmx16") or 0),
        rot,
        xyz,
        prof_info.get("id"),
    )


def _get_or_build_sphere(f, prof_info):
    """Look up the fixture's sphere from the cache, rebuilding when
    `(home, rotation, xyz, profile)` has changed since the last build."""
    fid = int(f.get("id") or 0)
    key = _sphere_cache_key(f, prof_info)
    cached = _sphere_cache.get(fid)
    if cached is not None and cached[0] == key:
        return cached[1]
    sphere = AimSphere(f, prof_info)
    _sphere_cache[fid] = (key, sphere)
    return sphere


def invalidate_sphere(fid):
    """Drop the cached sphere for a fixture. Called by parent_server
    after PUT /api/fixtures/<fid> (rotation/Home edits) and after a
    project import (mass mutations).
    """
    _sphere_cache.pop(int(fid), None)


def invalidate_all_spheres():
    """Drop every cached sphere — used after `/api/project/import`,
    `/api/reset`, or any other operation that may have mutated
    fixture records or the profile library en masse."""
    _sphere_cache.clear()


def _resolve_sphere(fid, fixtures, profile_lib, get_fixture_xyz=None):
    """Return `(sphere, error_dict_or_none, status_code)`.

    `get_fixture_xyz(fid)` is an injected callable that returns the
    fixture's `(x, y, z)` from the layout (`_layout.children` in
    parent_server). Per #785 QA round 2: fixture position is NOT on
    the fixture record — it's keyed by id in the layout. Without this
    lookup, `aim_xyz` computes target direction from origin instead
    of the real fixture position, and tilt collapses to home for
    every below-horizon target.
    """
    f = next((x for x in fixtures if x.get("id") == fid), None)
    if f is None:
        return None, {"err": "not_found"}, 404
    if f.get("fixtureType") != "dmx":
        return None, {"err": "not_a_mover"}, 400
    pid = f.get("dmxProfileId")
    if not pid:
        return None, {"err": "no_profile"}, 400
    prof_info = profile_lib.channel_info(pid)
    if not prof_info:
        return None, {"err": "no_profile"}, 400
    if f.get("homePanDmx16") is None or f.get("homeTiltDmx16") is None:
        return None, {"err": "no_home"}, 400
    # #785 QA r2 — patch the fixture dict's xyz with the live layout
    # position before constructing the sphere. The patched dict is a
    # view (the AimSphere constructor copies fixture_xyz into a tuple
    # at __init__ time, so subsequent mutations don't leak). The cache
    # key includes xyz so a layout edit invalidates the cached sphere.
    if get_fixture_xyz is not None:
        try:
            x, y, z = get_fixture_xyz(fid)
            f = dict(f)
            f["x"] = x
            f["y"] = y
            f["z"] = z
        except Exception:
            pass
    try:
        sphere = _get_or_build_sphere(f, prof_info)
    except ValueError as e:
        # Profile missing pan/tilt range, or Home out of [0, 65535],
        # or other constructor-level data problems — surface the message.
        return None, {"err": "invalid_fixture", "detail": str(e)}, 400
    return sphere, None, 200


def _resolve_current_pose(f, prof_info, query_args, get_engine, sphere):
    """Determine `current_pose` for this aim call. Resolution:
       1. ?currentPanDmx16=&currentTiltDmx16= query overrides.
       2. Home anchor.

    Engine-last-write was tried briefly (#785 QA Bug 3) but produced
    non-deterministic aim — same XYZ target, different DMX based on
    whatever the engine last wrote. One-off operator clicks via
    `POST /api/mover/<fid>/aim` should be deterministic per call.
    Track-update flows that DO want "minimize travel from current
    pose" go through `mover_control._aim_to_pan_tilt` which passes
    `current_pose` directly to `sphere.aim_xyz()` — they don't hit
    this resolver.
    """
    cp_pan = query_args.get("currentPanDmx16")
    cp_tilt = query_args.get("currentTiltDmx16")
    if cp_pan is not None and cp_tilt is not None:
        try:
            return (max(0, min(65535, int(cp_pan))),
                     max(0, min(65535, int(cp_tilt))))
        except (TypeError, ValueError):
            pass
    return (sphere.home_pan_dmx16, sphere.home_tilt_dmx16)


def _read_axis_dmx16(buf, addr, cm, channels, axis_type):
    """Read a 16-bit DMX axis value from the engine's universe buffer
    using the profile's channel definitions. Returns None when the axis
    isn't in the profile or the read fails."""
    if axis_type not in cm:
        return None
    coarse_off = cm[axis_type]
    # Find the fine-channel offset if the axis is 16-bit.
    fine_off = cm.get(f"{axis_type}-fine")
    coarse_ch = next((c for c in channels if c.get("type") == axis_type), None)
    bits = (coarse_ch or {}).get("bits", 8)
    try:
        coarse = int(buf.get_channel(addr + coarse_off))
    except Exception:
        return None
    if bits >= 16:
        if fine_off is None:
            # Profile says 16-bit but no explicit fine channel — assume
            # contiguous coarse + (coarse + 1).
            try:
                fine = int(buf.get_channel(addr + coarse_off + 1))
            except Exception:
                fine = 0
        else:
            try:
                fine = int(buf.get_channel(addr + fine_off))
            except Exception:
                fine = 0
        return (coarse << 8) | (fine & 0xFF)
    # 8-bit axis: scale to 16-bit.
    return coarse << 8


def _aim_from_body(body, query_args, sphere, current_pose):
    """Resolve the request body to a `(panDmx16, tiltDmx16)` pose.
    Returns `(pose_or_none, err_dict_or_none, status_code)`."""
    if not isinstance(body, dict):
        return None, {"err": "invalid_body"}, 400
    prefer = query_args.get("prefer", "closest")
    if prefer not in ("closest", "A", "B"):
        return None, {"err": "invalid_body",
                       "detail": f"prefer must be closest|A|B, got {prefer!r}"}, 400
    if "x" in body and "y" in body and "z" in body:
        try:
            target = (float(body["x"]), float(body["y"]), float(body["z"]))
        except (TypeError, ValueError):
            return None, {"err": "invalid_body",
                           "detail": "x/y/z must be numbers"}, 400
        pose = sphere.aim_xyz(target, current_pose=current_pose, prefer=prefer)
        if pose is None:
            return None, {"err": "degenerate_target"}, 400
        return pose, None, 200
    if "azDeg" in body and "elDeg" in body:
        try:
            az = float(body["azDeg"])
            el = float(body["elDeg"])
        except (TypeError, ValueError):
            return None, {"err": "invalid_body",
                           "detail": "azDeg/elDeg must be numbers"}, 400
        pose = sphere.aim_direction(az, el, current_pose=current_pose,
                                      prefer=prefer)
        if pose is None:
            return None, {"err": "degenerate_target"}, 400
        return pose, None, 200
    return None, {"err": "invalid_body",
                   "detail": "body must contain {x,y,z} or {azDeg,elDeg}"}, 400


def register(app, *, get_fixtures, profile_lib, write_pose, get_engine,
             check_calibrating=lambda fid: False,
             get_fixture_xyz=None):
    """Plug the aim routes into the Flask `app`. `get_fixture_xyz(fid)`
    is the layout-position lookup callable (parent_server's
    `_fixture_position` does the `_layout.children` traversal). Without
    it, AimSphere builds against the fixture record's xyz which is
    typically (0, 0, 0); aim_xyz then computes target direction from
    origin and the result is wrong (#785 QA round 2)."""
    from flask import jsonify, request

    @app.post("/api/mover/<int:fid>/aim")
    def api_mover_aim(fid):
        if check_calibrating(fid):
            return jsonify(err="calibrating"), 409
        if get_engine() is None:
            return jsonify(err="engine_not_running"), 503
        sphere, err, status = _resolve_sphere(
            fid, get_fixtures(), profile_lib, get_fixture_xyz)
        if err is not None:
            return jsonify(**err), status
        body = request.get_json(silent=True) or {}
        f = next((x for x in get_fixtures() if x.get("id") == fid), None)
        prof_info = profile_lib.channel_info(f.get("dmxProfileId"))
        # Resolve current_pose using query overrides → engine read → home.
        current_pose = _resolve_current_pose(
            f, prof_info, request.args, get_engine, sphere)
        pose, err, status = _aim_from_body(body, request.args, sphere, current_pose)
        if err is not None:
            return jsonify(**err), status
        pan_dmx16, tilt_dmx16 = pose
        try:
            write_pose(int(f.get("dmxUniverse", 1) or 1),
                        int(f.get("dmxStartAddr", 1) or 1),
                        pan_dmx16, tilt_dmx16, prof_info)
        except Exception as e:
            return jsonify(err="dmx_write_failed", detail=str(e)), 500
        return jsonify(ok=True,
                        panDmx16=pan_dmx16, tiltDmx16=tilt_dmx16)

    return api_mover_aim
