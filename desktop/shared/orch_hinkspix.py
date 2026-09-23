"""orch_hinkspix — HinksPix PRO device management blueprint (#939).

Owns the routes for registering and configuring a HolidayCoro HinksPix PRO as a
first-class SlyLED device: the 48-port table, the universe map, pushing that
config to the controller, and creating port-bound fixtures from it.

Follows the B1 split convention (see orch_state.py): parent_server keeps all
module state, reached here through `ps.*` at call time so test monkeypatching of
parent_server attributes keeps working.

Streaming pixels to the device is #940; offline .hseq playback is #941. This
module deliberately stops at "the device is described, configured, and bound to
fixtures".
"""

import hashlib
import json
import time

from flask import Blueprint, jsonify, request

import orch_state

ps = orch_state.ps
assert ps is not None, "orch_state.bind() must run before importing orch_hinkspix"

import hinkspix_bridge as hb
import hinkspix_config as hc
from pixel_output import PixelOutputMap, UniverseCollision, to_wire_frame

bp = Blueprint("hinkspix", __name__)

MAX_PORTS = hb.MAX_PORTS


def _child(cid):
    """Fetch a hinkspix child, or (None, error_response)."""
    c = next((c for c in ps._children if c.get("id") == cid), None)
    if not c:
        return None, (jsonify(err="child not found"), 404)
    if c.get("type") != "hinkspix":
        return None, (jsonify(err=f"child {cid} is not a HinksPix "
                              f"(type={c.get('type')})"), 400)
    return c, None


def _upload_gate(child):
    """Refuse a raw-TCP operation on firmware too old to accept one.

    ``FirmwareSupportsUpload`` (``HinksPix.cpp:1902``) is MCPU >= 151, or >= 129
    on hardware V3, and xLights checks it before *every* raw-TCP operation —
    time, master/remote mode, file upload, schedule upload
    (``HinksPixExportDialog.cpp:471/505/520/546/655/685``). Below the gate the
    controller drops the connection instead of answering, which reaches the
    operator as a bare socket error with no hint of the real cause (#944 B18).

    Returns a ``(response, 409)`` pair when the device cannot be managed over the
    network, else None.
    """
    hinks = child.get("hinks") or {}
    mcpu = hinks.get("mcpu")
    hardware_v3 = bool(hinks.get("hardwareV3"))
    if hb.supports_upload(mcpu, hardware_v3):
        return None
    minimum = hb.MIN_MCPU_UPLOAD_V3 if hardware_v3 else hb.MIN_MCPU_UPLOAD
    shown = hinks.get("mcpuRaw") or (f"MS_{mcpu}" if mcpu is not None else "unknown")
    # xLights' own wording is "'%s' CPU Firmware is too old (v%d) Update to a
    # Newer Version."; naming the threshold saves a support round-trip.
    return jsonify(
        ok=False,
        err=(f"'{child.get('ip')}' CPU firmware is too old ({shown}) — "
             f"MS_{minimum} or newer is required for network upload"),
        mcpu=mcpu, mcpuRaw=shown, minMcpu=minimum, hardwareV3=hardware_v3,
    ), 409


def _gate_payload(child):
    """The same gate as a JSON body for a read-only route (never a response)."""
    gated = _upload_gate(child)
    if gated is None:
        return {"ok": True}
    body = gated[0].get_json()
    return {"ok": False, "err": body.get("err"), "mcpu": body.get("mcpu"),
            "minMcpu": body.get("minMcpu")}


def _config_hash(hinks):
    """Stable hash of everything that must be pushed to the controller, so the
    UI can say "config differs from device" without a readback round-trip.

    The fitted boards are part of it: which boards get a PCONFIG, and therefore
    which ports exist at all, comes from the probe rather than from the port
    table. A re-probe that reports a different board layout changes what an
    upload would write even though no port row was edited (#943 B7).
    """
    payload = {
        "baseUniverse": hinks.get("baseUniverse"),
        "protocol": hinks.get("protocol"),
        "dmxOut": hinks.get("dmxOut"),
        "boards": {str(k): v for k, v in sorted(
            (hinks.get("boards") or {}).items())},
        "ports": sorted(
            [{k: p.get(k) for k in ("port", "leds", "protocol", "colorOrder",
                                    "direction", "nullPixels", "brightness",
                                    "gamma", "enabled")}
             for p in (hinks.get("ports") or [])],
            key=lambda p: p.get("port") or 0),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _other_maps(cid):
    out = []
    for c in ps._children:
        if c.get("type") != "hinkspix" or c.get("id") == cid:
            continue
        try:
            out.append(PixelOutputMap.build(c))
        except (ValueError, TypeError):
            continue
    return out


def _dmx_fixture_universes():
    return {int(f["dmxUniverse"]) for f in ps._fixtures
            if f.get("fixtureType") == "dmx" and f.get("dmxUniverse")}


def _build_map_or_error(child):
    try:
        m = PixelOutputMap.build(child)
    except (ValueError, TypeError) as exc:
        return None, (jsonify(err=f"invalid port layout: {exc}"), 400)
    try:
        m.check_collisions(_other_maps(child["id"]), _dmx_fixture_universes())
    except UniverseCollision as exc:
        return None, (jsonify(err=str(exc)), 400)
    return m, None


def _sync_universe_routes(child, output_map):
    """Upsert Art-Net unicast routes for this child's universes.

    sACN reaches the controller by multicast and needs nothing; Art-Net would
    otherwise broadcast. Doing it here means adding a controller doesn't
    require the operator to hand-edit routes.
    """
    routes = [r for r in (ps._dmx_settings.get("universeRoutes") or [])
              if r.get("label") != f"hinkspix:{child['id']}"]
    routes.extend(output_map.route_rows(child.get("ip") or ""))
    ps._dmx_settings["universeRoutes"] = routes
    ps._save("dmx_settings", ps._dmx_settings)


# ── Device config ────────────────────────────────────────────────────────────

@bp.get("/api/hinkspix/<int:cid>")
def api_hinkspix_get(cid):
    child, err = _child(cid)
    if err:
        return err
    hinks = child.get("hinks") or {}
    # #940 — the engine actually streaming (from dmx_settings) and the input
    # protocol the controller was configured for must agree, or frames go out
    # on a wire the device isn't listening to. Surfaced rather than silently
    # coerced: changing either side is an operator decision.
    engine_proto = (ps._dmx_settings.get("protocol") or "artnet").lower()
    device_proto = (hinks.get("protocol") or "").lower()
    proto_match = (device_proto in ("e131", "sacn") and engine_proto == "sacn") or \
                  (device_proto == engine_proto)
    body = {"ok": True, "id": cid, "ip": child.get("ip"),
            "name": child.get("name"), "status": child.get("status", 0),
            "engineProtocol": engine_proto, "protocolMatch": bool(proto_match),
            "hinks": hinks, "configHash": _config_hash(hinks),
            "pushedHash": hinks.get("configHash", ""),
            # The SPA builds its protocol picker from this rather than a
            # hard-coded list, so an option the controller cannot serve is
            # never offered (#943 B14).
            "protocols": list(hc.input_protocols_supported(
                bool(hinks.get("hardwareV3")))),
            "inSync": bool(hinks.get("configHash")
                           and hinks["configHash"] == _config_hash(hinks))}
    try:
        body["map"] = PixelOutputMap.build(child).to_json()
    except (ValueError, TypeError) as exc:
        body["map"] = None
        body["mapError"] = str(exc)
    return jsonify(body)


@bp.put("/api/hinkspix/<int:cid>")
def api_hinkspix_put(cid):
    """Update the port table / base universe / DMX-out / protocol.

    Rejects a layout that collides with another controller or a DMX fixture
    *before* persisting, so the stored config is always mappable.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    hinks = dict(child.get("hinks") or {})

    if "baseUniverse" in body:
        try:
            base = int(body["baseUniverse"])
        except (TypeError, ValueError):
            return jsonify(err="baseUniverse must be an integer"), 400
        if not 1 <= base <= 63999:
            return jsonify(err="baseUniverse must be 1..63999"), 400
        hinks["baseUniverse"] = base

    if "protocol" in body:
        proto = str(body["protocol"]).lower()
        supported = hc.input_protocols_supported(bool(hinks.get("hardwareV3")))
        if proto not in supported:
            # DDP is a mode the firmware accepts but this controller's
            # definition never offers (#943 B14) — writing it produces a config
            # that is stored, accepted, and inert.
            return jsonify(err=f"protocol must be one of "
                               f"{'/'.join(supported)}"
                               f"{' (DDP needs hardware V3)' if proto == 'ddp' else ''}"), 400
        hinks["protocol"] = proto

    if "dmxOut" in body:
        d = body["dmxOut"] or {}
        enabled = bool(d.get("enabled"))
        uni = d.get("universe")
        if enabled:
            try:
                uni = int(uni)
            except (TypeError, ValueError):
                return jsonify(err="dmxOut.universe required when enabled"), 400
            if not 1 <= uni <= 63999:
                return jsonify(err="dmxOut.universe must be 1..63999"), 400
        hinks["dmxOut"] = {"enabled": enabled,
                           "universe": uni if enabled else None}

    if "ports" in body:
        ports, seen = [], set()
        for i, p in enumerate(body["ports"] or []):
            try:
                num = int(p.get("port"))
            except (TypeError, ValueError):
                return jsonify(err=f"ports[{i}].port must be an integer"), 400
            if not 1 <= num <= MAX_PORTS:
                return jsonify(err=f"ports[{i}].port {num} out of range 1..{MAX_PORTS}"), 400
            if num in seen:
                return jsonify(err=f"duplicate port {num}"), 400
            seen.add(num)
            try:
                leds = int(p.get("leds") or 0)
            except (TypeError, ValueError):
                return jsonify(err=f"ports[{i}].leds must be an integer"), 400
            if leds < 0:
                return jsonify(err=f"ports[{i}].leds must be >= 0"), 400
            ports.append({
                "port": num, "leds": leds,
                "mm": p.get("mm") if p.get("mm") is not None
                else int(round(leds * 16.67)),
                "protocol": p.get("protocol", "ws2811"),
                "colorOrder": p.get("colorOrder", "RGB"),
                "direction": p.get("direction", 0),
                "nullPixels": int(p.get("nullPixels") or 0),
                "brightness": hb.encode_brightness(p.get("brightness", 100)),
                "gamma": hb.encode_gamma(p.get("gamma", 1)),
                "enabled": bool(p.get("enabled", True)),
            })
        hinks["ports"] = sorted(ports, key=lambda p: p["port"])

    probe_child = dict(child)
    probe_child["hinks"] = hinks
    output_map, err = _build_map_or_error(probe_child)
    if err:
        return err

    with ps._lock:
        child["hinks"] = hinks
        ps._save("children", ps._children)
    _sync_universe_routes(child, output_map)
    ps.log.info("HinksPix %s config updated: %d ports, %d universes from %d",
                child.get("ip"), len(hinks.get("ports") or []),
                len(output_map.universes), hinks.get("baseUniverse"))
    return jsonify(ok=True, hinks=hinks, map=output_map.to_json(),
                   configHash=_config_hash(hinks))


@bp.get("/api/hinkspix/<int:cid>/map")
def api_hinkspix_map(cid):
    child, err = _child(cid)
    if err:
        return err
    output_map, err = _build_map_or_error(child)
    if err:
        return err
    return jsonify(ok=True, map=output_map.to_json())


# ── Device I/O ───────────────────────────────────────────────────────────────

@bp.post("/api/hinkspix/<int:cid>/probe")
def api_hinkspix_probe(cid):
    child, err = _child(cid)
    if err:
        return err
    try:
        info = hb.probe(child["ip"])
    except hb.HinksPixError as exc:
        with ps._lock:
            child["status"] = 0
            ps._save("children", ps._children)
        return jsonify(ok=False, err=str(exc)), 502
    with ps._lock:
        child["status"] = 1
        child["seen"] = int(time.time())
        child["fwVersion"] = info.get("mcpuRaw")
        hinks = child.setdefault("hinks", {})
        for k in ("mcpu", "pcpu", "ecpu", "web", "maxU", "hardwareV3",
                  "uploadSupported", "boards", "model"):
            if k in info:
                hinks[k] = info[k]
        ps._save("children", ps._children)
    return jsonify(ok=True, info=info)


@bp.get("/api/hinkspix/<int:cid>/device-config")
def api_hinkspix_device_config(cid):
    """Read what the controller currently holds, in SlyLED's own shape.

    Three reads: BoardInfo (MaxU and the fitted boards), the input mode, and
    16 PCONFIG rows per fitted pixel board. The universe table is **not** read
    — xLights never reads it either (`GetControllerE131Data` has no callers) and
    it would be one request per row; `diff` says so rather than reporting every
    row as a difference.

    Also returns the diff against the stored config, so one call answers "is
    what I have on screen what the device has?".
    """
    child, err = _child(cid)
    if err:
        return err
    hinks = child.get("hinks") or {}
    ip = child["ip"]
    try:
        raw_ports = {}
        for board in hc.pixel_boards(child):
            raw_ports[board] = hb.read_board_ports(ip, board)
        current = hc.decode_device_config(
            board_info={"MaxU": hinks.get("maxU")},
            data_mode=hb.read_data_mode(ip, blk=0),
            board_ports=raw_ports)
    except hb.HinksPixError as exc:
        return jsonify(ok=False, err=str(exc)), 502

    body = {"ok": True, "id": cid, "device": current.to_json()}
    output_map, merr = _build_map_or_error(child)
    if merr or not hinks.get("maxU"):
        body["diff"] = None
        body["diffError"] = (merr[0].get_json().get("err") if merr
                             else "the controller's universe limit is not known")
    else:
        try:
            intended = hc.intended_config(child, output_map,
                                          max_universes=hinks.get("maxU"))
            body["diff"] = hc.diff(current, intended)
        except hc.ConfigError as exc:
            body["diff"] = None
            body["diffError"] = str(exc)
    return jsonify(body)


def _plan_preconditions(child, output_map):
    """Reasons the stored config cannot be uploaded yet, or []."""
    hinks = child.get("hinks") or {}
    reasons = []
    if not hinks.get("maxU"):
        reasons.append("the controller has not been probed — its universe "
                       "limit and fitted boards are unknown")
    if not hc.pixel_boards(child):
        reasons.append("no expansion boards are known — probe the controller")
    max_u = int(hinks.get("maxU") or 0)
    if max_u and len(output_map.universes) > max_u:
        reasons.append(f"layout needs {len(output_map.universes)} universes "
                       f"but the controller supports {max_u}")
    return reasons


def _build_plan(child, output_map):
    """``(plan, None)`` or ``(None, error_response)``."""
    hinks = child.get("hinks") or {}
    reasons = _plan_preconditions(child, output_map)
    if reasons:
        return None, (jsonify(ok=False, err="; ".join(reasons),
                              reasons=reasons), 409)
    try:
        intended = hc.intended_config(child, output_map,
                                      max_universes=hinks.get("maxU"))
        cmds = hc.build_commands(intended, mcpu=hinks.get("mcpu"),
                                 hardware_v3=bool(hinks.get("hardwareV3")))
    except hc.ConfigError as exc:
        return None, (jsonify(ok=False, err=str(exc)), 400)
    return (intended, cmds), None


@bp.get("/api/hinkspix/<int:cid>/plan")
def api_hinkspix_plan(cid):
    """The exact requests an upload would send — dry run, nothing touches the
    device.

    Every entry carries the real method, path and header values, so the
    preview the operator approves is the same sequence `apply` executes rather
    than a description of it. Also returns the diff against the device when it
    has been read recently enough to be worth comparing.
    """
    child, err = _child(cid)
    if err:
        return err
    output_map, err = _build_map_or_error(child)
    if err:
        return err
    built, err = _build_plan(child, output_map)
    if err:
        return err
    intended, cmds = built
    return jsonify(ok=True, id=cid, protocol=intended.mode,
                   maxUniverses=intended.max_universes,
                   universesUsed=len(intended.used_universes),
                   boards=sorted(intended.board_ports),
                   requests=[c.to_json() for c in cmds],
                   intended=intended.to_json())


@bp.post("/api/hinkspix/<int:cid>/apply")
def api_hinkspix_apply(cid):
    """Upload the stored config, then reboot the controller.

    Synchronous and operator-triggered — never implicit on save. The reboot is
    part of the sequence, not an option: a config written without it is stored
    and never loaded, which is indistinguishable from success until the pixels
    don't move (#943 B8).

    Stops at the first failed request and reports what completed, because every
    request after a failure would be written against a device that is in an
    unknown state.
    """
    child, err = _child(cid)
    if err:
        return err
    output_map, err = _build_map_or_error(child)
    if err:
        return err
    built, err = _build_plan(child, output_map)
    if err:
        return err
    intended, cmds = built

    hinks = child.get("hinks") or {}
    ip = child["ip"]
    done = []
    for i, req in enumerate(cmds):
        try:
            if req.kind == "read":
                hb.read_data_mode(ip, blk=req.blk)
                done.append({"index": i, "kind": req.kind, "ok": True,
                             "note": req.note})
                continue
            if req.kind == "reboot":
                # Fire-and-forget by design: the controller drops the
                # connection as it restarts, and waiting on the reply would
                # time out rather than tell us anything.
                results = hb.fire_and_forget(ip, req.data)
                done.append({"index": i, "kind": req.kind, "ok": True,
                             "note": req.note, "sends": results})
                continue
            hb.command(ip, req.data, path=req.path)
            done.append({"index": i, "kind": req.kind, "ok": True,
                         "note": req.note})
        except hb.HinksPixError as exc:
            ps.log.warning("HinksPix %s apply stopped at request %d/%d (%s): %s",
                           ip, i + 1, len(cmds), req.note, exc)
            return jsonify(ok=False, err=str(exc), failedAt=i,
                           failedNote=req.note, completed=done), 502

    with ps._lock:
        hinks["configPushedAt"] = int(time.time())
        hinks["configHash"] = _config_hash(hinks)
        ps._save("children", ps._children)
    ps.log.info("HinksPix %s config applied: %d requests, %d boards, %d universes",
                ip, len(cmds), len(intended.board_ports),
                len(intended.used_universes))
    return jsonify(ok=True, requests=len(cmds), completed=done,
                   configHash=hinks["configHash"],
                   rebooted=True)


# ── Fixtures from ports ──────────────────────────────────────────────────────

@bp.post("/api/hinkspix/<int:cid>/fixtures-from-ports")
def api_hinkspix_fixtures_from_ports(cid):
    """Create one LED fixture per enabled port.

    The default shape: one port, one fixture, one string. Merging ports into a
    multi-string fixture is then an ordinary edit in the fixture editor, which
    already supports per-string position/rotation (#864/#866). Ports already
    bound to a fixture are skipped rather than duplicated.
    """
    child, err = _child(cid)
    if err:
        return err
    hinks = child.get("hinks") or {}
    bound = {s.get("port") for f in ps._fixtures
             if f.get("childId") == cid for s in (f.get("strings") or [])
             if isinstance(s.get("port"), int)}

    created, skipped = [], []
    with ps._lock:
        for p in sorted(hinks.get("ports") or [], key=lambda x: int(x["port"])):
            num = int(p["port"])
            if not p.get("enabled", True) or int(p.get("leds") or 0) <= 0:
                continue
            if num in bound:
                skipped.append(num)
                continue
            leds = int(p["leds"])
            fix = {
                "id": ps._nxt_fix,
                "name": f"{child.get('name') or 'HinksPix'} P{num}",
                "fixtureType": "led", "type": "linear",
                "childId": cid,
                "strings": [{
                    "port": num, "leds": leds,
                    # Stage-mm geometry: default 60 px/m, operator corrects it
                    # in the fixture editor. Never a DMX fraction (#stage-coords).
                    "mm": int(p.get("mm") or round(leds * 16.67)),
                }],
            }
            ps._fixtures.append(fix)
            ps._nxt_fix += 1
            created.append({"id": fix["id"], "name": fix["name"], "port": num,
                            "leds": leds})
        if created:
            ps._save("fixtures", ps._fixtures)
    return jsonify(ok=True, created=created, skipped=skipped)


# ── Offline standalone playback (#941) ───────────────────────────────────────
# The controller plays .hseq sequences from its SD card against an on-board
# day/time schedule, with the orchestrator switched off. There is NO "play
# sequence N now" verb — playback is schedule-driven, so the native day/time
# model IS the feature rather than a workaround.

import hashlib as _hashlib
import os
import threading

import hinkspix_files as hf
import hinkspix_tcp as htcp
import pixel_renderer

DEPLOY_STORE = "hinkspix_deploy"
STEP_MS = 25                       # matches the live loop's 40 Hz tick

_deploy_state = {}                 # cid -> progress dict
_deploy_lock = threading.Lock()


def _deploy_cfg():
    return ps._load(DEPLOY_STORE, {}) if hasattr(ps, "_load") else {}


def _save_deploy_cfg(cfg):
    ps._save(DEPLOY_STORE, cfg)


def _device_dir(cid):
    d = os.path.join(str(ps.DATA), "hinkspix", str(cid))
    os.makedirs(d, exist_ok=True)
    return d


def _set_progress(cid, **kw):
    with _deploy_lock:
        st = _deploy_state.setdefault(cid, {})
        st.update(kw)


def render_hseq_frames(child, output_map, timeline_id, duration_s, step_ms=STEP_MS):
    """Render a whole timeline into controller frames.

    Each frame is `output_map.total_channels` bytes laid out by absStart, which
    is the SAME layout the live path writes into universes — both read the one
    map, so what was previewed is what plays unattended.

    Master brightness IS applied here, unlike the live path: offline there is no
    send-time gate to apply it later, and the operator expects the deployed show
    to look like the one they previewed.
    """
    bake = (ps._bake_result.get(timeline_id) or {}).get("fixtures") or {}
    fixtures = [f for f in ps._fixtures
                if f.get("childId") == child.get("id")
                and f.get("fixtureType") == "led"]
    total = output_map.total_channels
    n_frames = hf.frames_for_duration(duration_s, step_ms)
    g_bri = int(ps._settings.get("globalBrightness", 255) or 255)

    # Precompute each fixture's (span, offset) plan once rather than per frame.
    plans = []
    for f in fixtures:
        entry = bake.get(f["id"]) or bake.get(str(f["id"])) or {}
        if not entry.get("segments"):
            continue
        plans.append((f, entry, output_map.fixture_spans(f)))

    frames = []
    for i in range(n_frames):
        t_s = (i * step_ms) / 1000.0
        buf = bytearray(total)
        for f, entry, spans_per_string in plans:
            rgb = pixel_renderer.render_fixture(entry, f.get("strings") or [], t_s)
            pos = 0
            for string, spans in zip(f.get("strings") or [], spans_per_string):
                n = int(string.get("leds") or 0)
                if n <= 0:
                    continue
                # Same expansion the live path does, from the same span the
                # layout was built with — an RGBW port must advance 4 bytes per
                # pixel in the .hseq exactly as it does in a universe.
                chpp = int(spans[0]["channelsPerPixel"]) if spans else 3
                chunk = to_wire_frame(rgb[pos:pos + n * 3], n, chpp)
                pos += n * 3
                cpos = 0
                for span in spans:
                    take = span["channels"]
                    part = chunk[cpos:cpos + take]
                    if not part:
                        break
                    start = span["absStart"] - 1
                    buf[start:start + len(part)] = part
                    cpos += take
        if g_bri < 255:
            buf = bytearray((b * g_bri) // 255 for b in buf)
        frames.append(bytes(buf))
    return frames, total, n_frames


def _deploy_worker(cid, child, cfg):
    """Render + upload + schedule + switch to standalone."""
    entry = cfg.get(str(cid)) or {}
    try:
        output_map = PixelOutputMap.build(child)
    except (ValueError, TypeError) as exc:
        _set_progress(cid, running=False, ok=False, err=f"invalid port layout: {exc}")
        return

    tcp = htcp.HinksPixTcp(child["ip"])
    playlist_name = hf.short_name(entry.get("playlistName") or "SHOW")
    items = entry.get("items") or []
    manifest = []
    taken = set()

    try:
        # ── render ──────────────────────────────────────────────────
        rendered = []
        for idx, item in enumerate(items):
            tid = item.get("timelineId")
            tl = next((t for t in ps._timelines if t.get("id") == tid), None)
            if tl is None:
                raise htcp.HinksPixError(f"timeline {tid} not found")
            if not ps._bake_result.get(tid):
                raise htcp.HinksPixError(
                    f"timeline '{tl.get('name', tid)}' is not baked — bake it first")
            name = hf.short_name(tl.get("name") or f"SEQ{tid}", taken)
            taken.add(name)
            _set_progress(cid, running=True, phase="render",
                          message=f"Rendering {name} ({idx + 1}/{len(items)})")
            frames, channels, n_frames = render_hseq_frames(
                child, output_map, tid, tl.get("durationS", 60))
            path = os.path.join(_device_dir(cid), f"{name}.hseq")
            with open(path, "wb") as fp:
                written = hf.write_hseq(fp, frames, channels, STEP_MS, child["ip"])
            sha = _hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
            rendered.append({"name": name, "path": path, "bytes": written,
                             "sha256": sha, "frames": n_frames})
            manifest.append({"name": f"{name}.hseq", "bytes": written,
                             "sha256": sha, "ack": False})

        # ── upload sequences ────────────────────────────────────────
        for i, r in enumerate(rendered):
            def prog(sent, total, msg, _i=i, _r=r):
                _set_progress(cid, phase="upload",
                              message=f"{_r['name']}.hseq {sent * 100 // max(total, 1)}%",
                              fileIndex=_i, fileCount=len(rendered))
                return not _deploy_state.get(cid, {}).get("cancel")
            with open(r["path"], "rb") as fp:
                tcp.upload(f"{r['name']}.hseq", fp.read(), progress_cb=prog)
            manifest[i]["ack"] = True

        # ── playlist + seven schedules ──────────────────────────────
        _set_progress(cid, phase="playlist", message="Uploading playlist")
        ply = hf.playlist_text([{"hseq": f"{r['name']}.hseq"} for r in rendered])
        tcp.upload(f"{playlist_name}.ply", ply.encode("ascii"))
        manifest.append({"name": f"{playlist_name}.ply", "bytes": len(ply),
                         "ack": True})

        rows_by_day = {d: [] for d in hf.DAYS}
        for row in entry.get("schedule") or []:
            for day in (row.get("days") or []):
                key = str(day).upper()
                # accept MON/MONDAY
                match = next((d for d in hf.DAYS if d.startswith(key[:3])), None)
                if match:
                    rows_by_day[match].append(row)
        for day in hf.DAYS:
            # Days with no rows get an explicit empty list so a stale schedule
            # left on the card from a previous deploy is cleared.
            text = hf.schedule_text(rows_by_day[day], playlist_name)
            _set_progress(cid, phase="schedule", message=f"Uploading {day}.sched")
            tcp.upload(hf.schedule_filename(day), text.encode("ascii"))
            manifest.append({"name": hf.schedule_filename(day),
                             "bytes": len(text), "ack": True})

        # ── clock, then standalone ──────────────────────────────────
        _set_progress(cid, phase="clock", message="Setting controller clock")
        tcp.set_time()
        _set_progress(cid, phase="mode", message="Switching to standalone")
        tcp.set_mode(htcp.MODE_MASTER)

        with ps._lock:
            cfg.setdefault(str(cid), {})["lastDeploy"] = {
                "at": int(time.time()), "ok": True, "files": manifest,
                "mode": "G", "clockSetAt": int(time.time()),
                "playlist": playlist_name,
            }
            _save_deploy_cfg(cfg)
        _set_progress(cid, running=False, ok=True, phase="done",
                      message=f"Deployed {len(rendered)} sequence(s)")
        ps.log.info("HinksPix %s: deployed %d sequences, standalone mode",
                    child["ip"], len(rendered))
    except (htcp.HinksPixError, hf.HinksPixFileError, OSError) as exc:
        _set_progress(cid, running=False, ok=False, err=str(exc))
        with ps._lock:
            cfg.setdefault(str(cid), {})["lastDeploy"] = {
                "at": int(time.time()), "ok": False, "err": str(exc),
                "files": manifest,
            }
            _save_deploy_cfg(cfg)
        ps.log.warning("HinksPix %s deploy failed: %s", child.get("ip"), exc)


@bp.get("/api/hinkspix/<int:cid>/deploy")
def api_hinkspix_deploy_get(cid):
    """Deploy config + last-deploy manifest + live progress.

    ``gate`` lets the standalone view disable its TCP buttons with the same
    wording the routes return, rather than letting them fail as a socket error.
    """
    child, err = _child(cid)
    if err:
        return err
    cfg = _deploy_cfg().get(str(cid)) or {}
    return jsonify(ok=True, config=cfg, progress=_deploy_state.get(cid, {}),
                   gate=_gate_payload(child))


@bp.put("/api/hinkspix/<int:cid>/deploy")
def api_hinkspix_deploy_put(cid):
    """Set the playlist + schedule for standalone playback."""
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    cfg = _deploy_cfg()
    entry = cfg.setdefault(str(cid), {})

    if "playlistName" in body:
        entry["playlistName"] = hf.short_name(body["playlistName"])
    if "items" in body:
        entry["items"] = [{"timelineId": int(i.get("timelineId"))}
                          for i in (body["items"] or [])
                          if i.get("timelineId") is not None]
    if "schedule" in body:
        rows = []
        for i, row in enumerate(body["schedule"] or []):
            bad = hf.validate_schedule_row(row)
            if bad:
                hint = ""
                if "before start" in (bad or "") or "not after" in (bad or ""):
                    split = hf.split_overnight(row.get("start"), row.get("end"))
                    hint = (f" — the controller cannot span midnight; split into "
                            f"{split[0][0]}-{split[0][1]} and {split[1][0]}-{split[1][1]} "
                            f"on the following day")
                return jsonify(err=f"schedule[{i}]: {bad}{hint}"), 400
            rows.append({"days": [str(d).upper() for d in (row.get("days") or [])],
                         "start": row.get("start"), "end": row.get("end"),
                         "repeat": int(row.get("repeat") or 0),
                         "enabled": bool(row.get("enabled", True))})
        entry["schedule"] = rows
    _save_deploy_cfg(cfg)
    return jsonify(ok=True, config=entry)


@bp.post("/api/hinkspix/<int:cid>/deploy")
def api_hinkspix_deploy(cid):
    """Render, upload and switch the controller to standalone playback."""
    child, err = _child(cid)
    if err:
        return err
    if _deploy_state.get(cid, {}).get("running"):
        return jsonify(err="a deploy is already running"), 409

    hinks = child.get("hinks") or {}
    reasons = []
    if child.get("status") != 1:
        reasons.append("controller is offline")
    if not hinks.get("uploadSupported"):
        minimum = (hb.MIN_MCPU_UPLOAD_V3 if hinks.get("hardwareV3")
                   else hb.MIN_MCPU_UPLOAD)
        reasons.append(f"firmware MCPU {hinks.get('mcpu')} is below the "
                       f"{minimum} required for network upload — "
                       f"update via SD card first")
    if hinks.get("configHash") != _config_hash(hinks):
        reasons.append("port config has not been pushed to the controller")
    cfg = _deploy_cfg()
    entry = cfg.get(str(cid)) or {}
    if not entry.get("items"):
        reasons.append("no sequences selected")
    for item in entry.get("items") or []:
        tid = item.get("timelineId")
        if not ps._bake_result.get(tid):
            reasons.append(f"timeline {tid} is not baked")
    if reasons:
        return jsonify(err="; ".join(reasons), reasons=reasons), 409

    _deploy_state[cid] = {"running": True, "phase": "start", "message": "Starting",
                          "ok": None, "cancel": False}
    threading.Thread(target=_deploy_worker, args=(cid, child, cfg),
                     daemon=True, name=f"hinkspix-deploy-{cid}").start()
    return jsonify(ok=True, started=True)


@bp.post("/api/hinkspix/<int:cid>/mode")
def api_hinkspix_mode(cid):
    """Switch between live (Ethernet) and standalone (SD) playback."""
    child, err = _child(cid)
    if err:
        return err
    mode = str((request.get_json(silent=True) or {}).get("mode", "")).lower()
    if mode not in ("live", "standalone"):
        return jsonify(err="mode must be 'live' or 'standalone'"), 400
    if mode == "standalone":
        # Standalone is a raw-TCP mode packet. Live is the HTTP config path
        # (#943 OP_MODE ETHERNET) and has no firmware floor of its own, so it
        # stays reachable from a controller that cannot be managed over TCP.
        gate = _upload_gate(child)
        if gate:
            return gate
    try:
        if mode == "live":
            hb.op_mode_ethernet(child["ip"])
        elif mode == "standalone":
            htcp.HinksPixTcp(child["ip"]).set_mode(htcp.MODE_MASTER)
    except (hb.HinksPixError, htcp.HinksPixError) as exc:
        return jsonify(ok=False, err=str(exc)), 502
    return jsonify(ok=True, mode=mode)


@bp.post("/api/hinkspix/<int:cid>/set-clock")
def api_hinkspix_set_clock(cid):
    """Set the controller RTC.

    Worth doing periodically: the time packet carries time-of-day and weekday
    only — no date — so an unattended controller drifts by an hour at each DST
    transition until it is re-synced.
    """
    child, err = _child(cid)
    if err:
        return err
    gate = _upload_gate(child)
    if gate:
        return gate
    try:
        htcp.HinksPixTcp(child["ip"]).set_time()
    except htcp.HinksPixError as exc:
        return jsonify(ok=False, err=str(exc)), 502
    return jsonify(ok=True, at=int(time.time()))
