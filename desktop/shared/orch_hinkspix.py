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
from pixel_output import PixelOutputMap, UniverseCollision

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


def _config_hash(hinks):
    """Stable hash of everything that must be pushed to the controller, so the
    UI can say "config differs from device" without a readback round-trip."""
    payload = {
        "baseUniverse": hinks.get("baseUniverse"),
        "protocol": hinks.get("protocol"),
        "dmxOut": hinks.get("dmxOut"),
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
        if proto not in ("e131", "sacn", "artnet", "ddp"):
            return jsonify(err="protocol must be e131/sacn/artnet/ddp"), 400
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


@bp.post("/api/hinkspix/<int:cid>/push-config")
def api_hinkspix_push(cid):
    """Push the stored config to the controller.

    Explicit and operator-triggered — never implicit on save. A push reboots
    the controller when `opMode` is requested, and silently reconfiguring live
    hardware from a UI edit would be the wrong default.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    hinks = child.get("hinks") or {}
    output_map, err = _build_map_or_error(child)
    if err:
        return err

    max_u = int(hinks.get("maxU") or 0)
    if max_u and len(output_map.universes) > max_u:
        return jsonify(err=f"layout needs {len(output_map.universes)} universes "
                           f"but the controller supports {max_u}"), 400

    steps = []
    try:
        hb.push_data_mode(child["ip"], hinks.get("protocol", "e131"),
                          hinks.get("dmxOut"))
        steps.append("data_mode")
        hb.push_input_universes(child["ip"], output_map.spans,
                                max_u or len(output_map.spans))
        steps.append("universes")
        ports = hinks.get("ports") or []
        for board in range(0, (MAX_PORTS + 15) // 16):
            chunk = [p for p in ports
                     if board * 16 < int(p["port"]) <= (board + 1) * 16]
            if chunk:
                hb.push_port_config(child["ip"], board, chunk)
                steps.append(f"pconfig{board}")
        if body.get("opMode") == "ethernet":
            hb.op_mode_ethernet(child["ip"])
            steps.append("op_mode_ethernet")
    except hb.HinksPixError as exc:
        return jsonify(ok=False, err=str(exc), completed=steps), 502

    with ps._lock:
        hinks["configPushedAt"] = int(time.time())
        hinks["configHash"] = _config_hash(hinks)
        ps._save("children", ps._children)
    return jsonify(ok=True, steps=steps, configHash=hinks["configHash"])


@bp.get("/api/hinkspix/<int:cid>/readback")
def api_hinkspix_readback(cid):
    """Read the controller's own view of its config for a side-by-side verify.

    Returned raw: these CGIs emit comma-separated text whose key set is not
    documented, so the SPA renders it rather than parsing speculatively.
    """
    child, err = _child(cid)
    if err:
        return err
    return jsonify(ok=True, readback=hb.readback(child["ip"]))


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
