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

import copy
import hashlib
import json
import os
import re
import threading
import time

from flask import Blueprint, jsonify, request

import orch_state

ps = orch_state.ps
assert ps is not None, "orch_state.bind() must run before importing orch_hinkspix"

import hinkspix_bridge as hb
import hinkspix_config as hc
import net_ifaces
import hinkspix_xlights_import as xi
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


# Everything about a port that PCONFIG carries a byte for, plus the SlyLED
# fields that change what goes on the wire: `mm` (stage geometry), `enabled`
# (whether the row is written at all) and the two smart-receiver fields, which
# decide the SCONFIG lists (#946). A port field *not* listed here would be
# edited in the UI, change what an upload sends, and leave the "device in sync"
# badge lying.
_PORT_HASH_FIELDS = ("port", "leds", "mm", "protocol", "colorOrder", "direction",
                     "startNulls", "brightness", "gamma", "enabled",
                     "smartRemote", "smartRemoteType")


def _config_hash(hinks):
    """Stable hash of everything that must be pushed to the controller, so the
    UI can say "config differs from device" without a readback round-trip.

    The fitted boards are part of it: which boards get a PCONFIG, and therefore
    which ports exist at all, comes from the probe rather than from the port
    table. A re-probe that reports a different board layout changes what an
    upload would write even though no port row was edited (#943 B7).

    The main-CPU version and hardware generation are part of it too, because
    they gate two requests in the sequence itself — the UnPack remap reset and
    the DDP branch (`build_commands`). A firmware update therefore changes what
    an upload sends even with the port table untouched.
    """
    payload = {
        "baseUniverse": hinks.get("baseUniverse"),
        "protocol": hinks.get("protocol"),
        "dmxOut": hinks.get("dmxOut"),
        # The per-port defaults feed the SCONFIG-free fields of every new row
        # (#946), so a change to them changes what an upload sends.
        "defaults": hinks.get("defaults"),
        "mcpu": hinks.get("mcpu"),
        "hardwareV3": bool(hinks.get("hardwareV3")),
        # Which model the caps came out of: the same port table means different
        # things on a PRO V1/V2 and a PRO V3, and a re-probe that changes the
        # answer changes the port count and the channel caps.
        "model": hinks.get("model"),
        "controller": hinks.get("controller"),
        "type": hinks.get("type"),
        "boards": {str(k): v for k, v in sorted(
            (hinks.get("boards") or {}).items())},
        "ports": sorted(
            [{k: p.get(k) for k in _PORT_HASH_FIELDS}
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


# ── Per-device storage ───────────────────────────────────────────────────────

def _device_dir(cid):
    """Orchestrator-side scratch for one controller: sequences, snapshots.

    Shared by the standalone deploy (#941) and configuration backups (#945) —
    neither lives on the controller, because both exist to survive it.
    """
    d = os.path.join(str(ps.DATA), "hinkspix", str(cid))
    os.makedirs(d, exist_ok=True)
    return d



# ── Discovery (#949) ─────────────────────────────────────────────────────────
#
# Setup → Discover starts this alongside the UDP PING/ArtPoll discover; the SPA
# polls it separately, so UDP results never wait on the HTTP sweep. Adding a
# found controller is the ordinary POST /api/children {ip}, which already
# probes and types it "hinkspix".

_sweep_lock = threading.Lock()
_sweep = {"pending": False, "done": 0, "total": 0, "found": [],
          "notes": [], "startedAt": None, "elapsedMs": None}


def _sweep_known_ips():
    known = {c.get("ip") for c in ps._children}
    known |= {f.get("cameraIp") for f in ps._fixtures
              if f.get("fixtureType") == "camera" and f.get("cameraIp")}
    return {ip for ip in known if ip}


def _sweep_bg(hosts, notes):
    t0 = time.time()

    def progress(done, total):
        _sweep["done"] = done

    found = []
    try:
        for info in hb.discover(hosts, progress=progress):
            found.append({
                "ip": info["ip"], "type": "hinkspix", "boardType": info["model"],
                "hostname": info["ip"], "name": info["model"],
                "mcpuRaw": info.get("mcpuRaw"), "maxU": info.get("maxU"),
                "boards": info.get("boards", {}),
                "uploadSupported": info.get("uploadSupported"),
            })
    finally:
        elapsed = time.time() - t0
        with _sweep_lock:
            _sweep.update(pending=False, found=found,
                          elapsedMs=int(elapsed * 1000))
        subnets = sorted({f"{e['ip']}/{e['prefixlen']}"
                          for e in net_ifaces.ipv4_interfaces()})
        ps.log.info("HinksPix sweep: subnets=%s hosts=%d found=%s in %.1fs%s",
                    ",".join(subnets), len(hosts),
                    [f["ip"] for f in found] or "none", elapsed,
                    ("; " + "; ".join(notes)) if notes else "")


@bp.post("/api/hinkspix/discover")
def api_hinkspix_discover_start():
    """Start the BoardInfo sweep of every local subnet (net_ifaces), skipping
    IPs already registered. Idempotent while a sweep is running."""
    with _sweep_lock:
        if _sweep["pending"]:
            return jsonify(dict(_sweep, ok=True))
        hosts, notes = net_ifaces.sweep_hosts()
        known = _sweep_known_ips()
        hosts = [h for h in hosts if h not in known]
        _sweep.update(pending=True, done=0, total=len(hosts), found=[],
                      notes=notes, startedAt=time.time(), elapsedMs=None)
        threading.Thread(target=_sweep_bg, args=(hosts, notes), daemon=True,
                         name="hinks-sweep").start()
        return jsonify(dict(_sweep, ok=True))


@bp.get("/api/hinkspix/discover")
def api_hinkspix_discover_state():
    """Sweep progress and results: pending, done/total, found[], notes[]."""
    with _sweep_lock:
        state = dict(_sweep)
    # A controller added while the sweep ran is no longer "new".
    known = _sweep_known_ips()
    state["found"] = [f for f in state["found"] if f["ip"] not in known]
    return jsonify(state)


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
    caps = hc.caps_for(hinks)
    body = {"ok": True, "id": cid, "ip": child.get("ip"),
            "name": child.get("name"), "status": child.get("status", 0),
            "engineProtocol": engine_proto, "protocolMatch": bool(proto_match),
            "hinks": hinks, "configHash": _config_hash(hinks),
            "pushedHash": hinks.get("configHash", ""),
            # The SPA builds its protocol picker from this rather than a
            # hard-coded list, so an option the controller cannot serve is
            # never offered (#943 B14).
            "protocols": list(hc.input_protocols_for(hinks)),
            # What this model can do at all — how many boards it addresses, its
            # per-port channel cap, which receivers it takes. The editor is
            # built from it so a new controller generation is a table row here
            # rather than a branch in the browser (#946).
            "caps": caps.to_json(),
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
    out = {}
    rejected = _apply_config_body(child, request.get_json(silent=True) or {}, out)
    if rejected is not None:
        return rejected
    hinks = out["hinks"]
    return jsonify(ok=True, hinks=hinks, map=out["map"].to_json(),
                   configHash=_config_hash(hinks),
                   findings=[f.to_json() for f in
                             _config_findings(child, out["map"])])


def _apply_config_body(child, body, out):
    """Validate and store a config body on `child`, or reject it.

    Returns a ``(response, status)`` pair when the body cannot be stored, else
    ``None`` — and on success fills ``out`` with ``{"hinks": ..., "map": ...}``.

    Shared by the PUT route and the xLights import's accept route, so a port
    table that arrived from an xLights show folder is held to exactly the rules a
    hand-typed one is: same ranges, same protocol list, same DMX-out and
    smart-receiver checks. Two implementations of "is this config valid" is how
    an import turns into a second way to write a config the editor would have
    refused (#947).

    Nothing is stored until the whole body validates: the map is built against a
    copy of the child first, so a rejected body leaves the child untouched.
    """
    hinks = dict(child.get("hinks") or {})
    caps = hc.caps_for(hinks)

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
        supported = hc.input_protocols_for(hinks)
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

    if "defaults" in body:
        d = body["defaults"] or {}
        cur = dict(hinks.get("defaults") or {})
        if "brightness" in d:
            cur["brightness"] = hb.encode_brightness(d["brightness"])
        if "gamma" in d:
            cur["gamma"] = hb.encode_gamma(d["gamma"])
        # Stored already encoded, so the editor shows the step the controller
        # will actually use rather than the number that was typed.
        hinks["defaults"] = {k: cur[k] for k in ("brightness", "gamma")
                             if k in cur}

    if "ports" in body:
        ports, seen = [], set()
        for i, p in enumerate(body["ports"] or []):
            try:
                num = int(p.get("port"))
            except (TypeError, ValueError):
                return jsonify(err=f"ports[{i}].port must be an integer"), 400
            # The model's own ceiling, not the protocol's 80: a port the
            # controller has no board for can never be written, so storing it
            # would only produce a config that cannot be uploaded. Whether the
            # board on a port *within* the ceiling is actually fitted is a fact
            # about the device rather than about this model, so it is a finding
            # (`port_on_absent_board`) — the operator can lay out a board they
            # are about to fit (#946).
            if not 1 <= num <= caps.max_pixel_port:
                return jsonify(err=f"ports[{i}].port {num} out of range 1.."
                                   f"{caps.max_pixel_port} for a "
                                   f"{caps.name}"), 400
            if num in seen:
                return jsonify(err=f"duplicate port {num}"), 400
            seen.add(num)
            try:
                leds = int(p.get("leds") or 0)
            except (TypeError, ValueError):
                return jsonify(err=f"ports[{i}].leds must be an integer"), 400
            if leds < 0:
                return jsonify(err=f"ports[{i}].leds must be >= 0"), 400
            try:
                nulls = int(p["startNulls"] if p.get("startNulls") is not None
                            else (p.get("nullPixels") or 0))
            except (TypeError, ValueError):
                return jsonify(err=f"ports[{i}].startNulls must be an integer"), 400
            if nulls < 0:
                return jsonify(err=f"ports[{i}].startNulls must be >= 0"), 400

            # The smart receiver on this output. Absent, empty or false clears
            # it; the letter the operator reads off the dial and the number that
            # goes on the wire are both accepted, and the letter is what is
            # stored so the table reads the way the hardware does (#946).
            raw_id = p.get("smartRemote")
            rec_id = hc.smart_remote_id({"smartRemote": raw_id})
            if raw_id not in (None, "", False) and rec_id < 0:
                return jsonify(err=f"ports[{i}].smartRemote must be A..P or "
                                   f"0..15"), 400
            if rec_id >= 0 and not caps.smart_remote_types:
                return jsonify(err=f"a {caps.name} has no smart receivers"), 400
            rec_type = ""
            if rec_id >= 0:
                rec_type = str(p.get("smartRemoteType") or "").strip().lower()
                if rec_type not in hb.SMART_REMOTE_TYPES:
                    return jsonify(err=f"ports[{i}].smartRemoteType must be one "
                                       f"of {'/'.join(hb.SMART_REMOTE_TYPES)}"), 400

            ports.append({
                "port": num, "leds": leds,
                "mm": p.get("mm") if p.get("mm") is not None
                else int(round(leds * 16.67)),
                "protocol": p.get("protocol", "ws2811"),
                "colorOrder": p.get("colorOrder", "RGB"),
                "direction": p.get("direction", 0),
                "startNulls": nulls,
                "brightness": hb.encode_brightness(p.get("brightness", 100)),
                "gamma": hb.encode_gamma(p.get("gamma", 1)),
                "enabled": bool(p.get("enabled", True)),
                "smartRemote": hc.smart_id_label(rec_id) if rec_id >= 0 else None,
                "smartRemoteType": rec_type or None,
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
    out["hinks"] = hinks
    out["map"] = output_map
    return None


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

def _read_e131_text(ip, row_index):
    """One universe-table read, or ``''`` when the controller refuses.

    Best-effort wherever it is used: ``GetControllerE131Data`` is declared and
    never called by xLights, so its ``ROW:`` semantics are unverified on
    hardware (#943). A read that fails must not fail a snapshot or a
    verification that can still say something useful.
    """
    try:
        return hb.read_e131_text(ip, row_index)
    except hb.HinksPixError as exc:
        ps.log.info("HinksPix %s E131 row %d read failed: %s", ip, row_index, exc)
        return ""


def _e131_indices(rows):
    """The table-row indices a reply covers; unparseable rows are dropped."""
    out = []
    for r in rows:
        row = hb.parse_universe_row(r)
        if row:
            out.append(row["index"])
    return out


def _e131_block_offset(ip):
    """Whether the universe table's ``ROW:`` header counts from 0 or from 1.

    Nothing in the reference says what ``ROW:`` selects, but the controller
    answers, and its first block is unambiguous: whichever index comes back
    carrying table rows 1-6 is the one block 0 is asked for. Returns None when
    neither does, and the caller then skips the table rather than storing rows
    it cannot place (#945).
    """
    want = list(range(1, hb.UNIVERSES_PER_BLOCK + 1))
    for idx in (0, 1):
        if _e131_indices(hb.parse_e131_reply(_read_e131_text(ip, idx))) == want:
            return idx
    return None


def _read_universe_blocks(ip, board_info):
    """The universe table, block by block — ``(blocks, warnings)``.

    Only blocks that came back carrying the rows they must hold are kept. A
    snapshot with a known gap is one a restore can describe ("these rows are
    left as they are"); a block stored against the wrong rows is a restore that
    silently writes them somewhere else, which is worse than not having it.
    """
    max_u = int((board_info or {}).get("MaxU") or 0)
    if max_u <= 0:
        return {}, ["the controller's universe limit is unknown, so its "
                    "universe table was not read"]
    offset = _e131_block_offset(ip)
    if offset is None:
        return {}, ["the controller's universe table could not be read — it did "
                    "not return the rows that were asked for, so this backup "
                    "holds no universe rows"]
    per = hb.UNIVERSES_PER_BLOCK
    blocks, missing = {}, []
    for blk in range((max_u + per - 1) // per):
        rows = hb.parse_e131_reply(_read_e131_text(ip, blk + offset))
        if _e131_indices(rows) == list(range(blk * per + 1, blk * per + per + 1)):
            blocks[blk] = rows
        else:
            missing.append(str(blk))
    warnings = []
    if missing:
        warnings.append("the controller's universe table did not read back for "
                        "block(s) " + ", ".join(missing) + " — those rows are "
                        "not in this backup and a restore leaves them as they are")
    return blocks, warnings


def _read_device(child, with_e131=False):
    """One round of reads off a controller — ``(read, warnings)``.

    BoardInfo, the block-0 input mode, one entry per fitted pixel board, and —
    when asked — the universe table. The boards come from *this* reply rather
    than from the last probe, so a board fitted since then is picked up rather
    than overwritten unbacked-up.
    """
    ip = child["ip"]
    read = {"probe": hb.read_board_info(ip),
            "dataMode": hb.read_data_mode(ip, blk=0),
            "boards": {}, "e131": {}}
    for board in hc.pixel_boards_from_info(read["probe"]):
        read["boards"][board] = hb.read_board_ports(ip, board)
    warnings = []
    if with_e131:
        read["e131"], warnings = _read_universe_blocks(ip, read["probe"])
    return read, warnings


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
        # `controller` and `type` are what the caps row is chosen from (#946);
        # they are recorded here because they are answers of the *device*, and
        # a model read off a probe is worth more than one inferred from a
        # universe limit.
        for k in ("mcpu", "pcpu", "ecpu", "web", "maxU", "hardwareV3",
                  "uploadSupported", "boards", "model", "controller", "type"):
            if k in info:
                hinks[k] = info[k]
        ps._save("children", ps._children)
    return jsonify(ok=True, info=info)


@bp.get("/api/hinkspix/<int:cid>/device-config")
def api_hinkspix_device_config(cid):
    """Read what the controller currently holds, in SlyLED's own shape.

    Three reads: BoardInfo (MaxU and the fitted boards), the input mode, and
    16 PCONFIG rows per fitted pixel board. The universe table is **not** read
    here — it is one request per block, and this route answers on every panel
    open. `diff` says so rather than reporting every row as a difference; the
    snapshot route and the apply job's verification do read it, which is where
    that cost earns its keep (#945).

    Also returns the diff against the stored config, so one call answers "is
    what I have on screen what the device has?".
    """
    child, err = _child(cid)
    if err:
        return err
    hinks = child.get("hinks") or {}
    try:
        read, _ = _read_device(child)
    except hb.HinksPixError as exc:
        return jsonify(ok=False, err=str(exc)), 502

    current = hc.decode_device_config(board_info=read["probe"],
                                      data_mode=read["dataMode"],
                                      board_ports=read["boards"])
    max_u = int(current.max_universes or hinks.get("maxU") or 0)
    body = {"ok": True, "id": cid, "device": current.to_json()}
    output_map, merr = _build_map_or_error(child)
    if merr or not max_u:
        body["diff"] = None
        body["diffError"] = (merr[0].get_json().get("err") if merr
                             else "the controller's universe limit is not known")
    else:
        try:
            intended = _intended_config(child, output_map,
                                        max_universes=max_u)
            body["diff"] = hc.diff(current, intended)
        except hc.ConfigError as exc:
            body["diff"] = None
            body["diffError"] = str(exc)
    return jsonify(body)


def _intended_config(child, output_map, max_universes=0):
    """``hc.intended_config`` with this module's one live input supplied.

    The ordered fixture strings bound to each port are what make a chain of
    smart receivers on one output expressible, and they live in the fixture
    list rather than in ``hinks`` — so every caller here goes through this
    rather than calling the pure builder directly (#946).
    """
    return hc.intended_config(
        child, output_map, max_universes=max_universes,
        port_strings=hc.strings_by_port(child, ps._fixtures))


def _validate(child, intended=None):
    """``hc.validate`` with the two inputs that live outside the stored config.

    The fixtures bound to this controller decide which ports carry a receiver
    chain and which enabled ports are driving nothing; the engine protocol
    decides whether the controller is listening to the wire SlyLED streams.
    Neither is readable from ``hinks`` — they are read here, where both are
    known, rather than passed in from every route (#946).
    """
    return hc.validate(child, intended=intended, fixtures=ps._fixtures,
                       engine_protocol=ps._dmx_settings.get("protocol"))


def _config_findings(child, output_map):
    """Findings for a config that is not necessarily uploadable yet.

    The intended config is built best-effort: a stored state that cannot
    express one at all is itself something ``validate`` reports, and the editor
    should still be told about the rest rather than being handed a bare error.
    """
    try:
        intended = _intended_config(child, output_map,
                                    (child.get("hinks") or {}).get("maxU"))
    except hc.ConfigError:
        intended = None
    return _validate(child, intended=intended)


def _blocking_findings(findings, ack):
    """Findings that stop a push: every error, plus warnings not acknowledged.

    An error is never passable. A warning is a decision the operator is allowed
    to make — but only after being shown it, so it blocks until the request
    names it by code. An *info* finding is neither, and never blocks: it is
    something true of every push rather than a decision, and gating on it would
    make a box to tick on every push (#945 F3). The previous version blocked
    every unacknowledged finding whatever its level, which is a gate that
    trains the operator to tick without reading.
    """
    acked = {str(c) for c in (ack if isinstance(ack, list) else [])}
    return [f for f in findings
            if f.level == "error"
            or (f.level == "warn" and f.code not in acked)]


def _build_plan(child, output_map):
    """``((intended, cmds), findings, None)`` or ``(None, None, response)``.

    The findings are computed even when the plan cannot be built, so one list
    answers both "this cannot be uploaded" and "this can, but read this first".
    A blocked plan returns 409 with the findings as well as the flat ``reasons``
    the preview has always quoted.
    """
    hinks = child.get("hinks") or {}
    try:
        intended = _intended_config(child, output_map,
                                    max_universes=hinks.get("maxU"))
    except hc.ConfigError as exc:
        return None, None, (jsonify(ok=False, err=str(exc)), 400)

    findings = _validate(child, intended=intended)
    blocked = hc.errors(findings)
    if blocked:
        return None, findings, (
            jsonify(ok=False, err="; ".join(f.text for f in blocked),
                    reasons=[f.text for f in blocked],
                    findings=[f.to_json() for f in findings]), 409)

    try:
        cmds = hc.build_commands(intended, mcpu=hinks.get("mcpu"),
                                 hardware_v3=bool(hinks.get("hardwareV3")))
    except hc.ConfigError as exc:
        # A backstop: `validate` above already rejects every state build_commands
        # refuses, so this only fires if a finding is ever removed from it.
        return None, findings, (jsonify(ok=False, err=str(exc),
                                        findings=[f.to_json() for f in findings]), 400)
    return (intended, cmds), findings, None


@bp.get("/api/hinkspix/<int:cid>/plan")
def api_hinkspix_plan(cid):
    """The exact requests an upload would send — dry run, nothing touches the
    device.

    Every entry carries the real method, path and header values, so the
    preview the operator approves is the same sequence `apply` executes rather
    than a description of it. ``findings`` are the reasons to hesitate: an
    ``error`` blocks the upload, a ``warn`` needs acknowledging on the way in.
    """
    child, err = _child(cid)
    if err:
        return err
    output_map, err = _build_map_or_error(child)
    if err:
        return err
    built, findings, err = _build_plan(child, output_map)
    if err:
        return err
    intended, cmds = built
    return jsonify(ok=True, id=cid, protocol=intended.mode,
                   maxUniverses=intended.max_universes,
                   universesUsed=len(intended.used_universes),
                   boards=sorted(intended.board_ports),
                   requests=[c.to_json() for c in cmds],
                   findings=[f.to_json() for f in (findings or [])],
                   intended=intended.to_json())


# ── Applying a configuration: snapshot, push, reboot, verify (#945) ──────────
#
# A configuration push is not a request, it is a sequence that ends in a reboot:
# the controller goes away mid-run, comes back on whatever it managed to store,
# and is only *proven* to hold it by being read back. Run synchronously that
# made the browser sit on a socket for a minute with no progress and no way to
# tell "rebooting" from "hung" (#945).
#
# So an apply is a job: POST starts it, GET reports progress, and the job ends
# by reading the device back and diffing it against what was intended. Every run
# snapshots the controller first, because the one thing an overwrite needs is a
# way back — and if that snapshot cannot be taken, nothing is written at all.
#
# The sequence is never cancelled part-way: a controller left with half a
# configuration is worse than one that finished the wrong thing, because the
# half-written one has no description. The failure path is a deliberate restore
# of the snapshot the run took, which is why its id is in the failure record.

_config_state = {}                 # cid -> progress dict
_config_lock = threading.Lock()

BACKUP_KEEP = 10                   # snapshots kept per device, newest first
BACKUP_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}(-[0-9]+)?\Z")
REBOOT_SETTLE_S = 3                # the controller does not drop instantly
REBOOT_WAIT_S = 90                 # give up well after a normal boot (~20 s)
REBOOT_POLL_S = 3


def _set_config_state(cid, **kw):
    with _config_lock:
        _config_state.setdefault(cid, {}).update(kw)


def _config_running(cid):
    return bool((_config_state.get(cid) or {}).get("running"))


def _backup_dir(cid):
    d = os.path.join(_device_dir(cid), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def _backup_path(cid, backup_id):
    """Where a snapshot lives, or None when the id is not one of ours.

    The id comes back from the client on restore, so it is matched against the
    shape this module generates rather than pasted into a path.
    """
    if not BACKUP_ID_RE.match(str(backup_id or "")):
        return None
    return os.path.join(_backup_dir(cid), f"{backup_id}.json")


def _save_backup(cid, backup):
    """Write a snapshot and prune the oldest, returning ``{id, at}``.

    Written to a temp name and renamed: a half-written snapshot that a later
    restore trusts is worse than no snapshot at all.
    """
    d = _backup_dir(cid)
    stem = time.strftime("%Y%m%d-%H%M%S", time.localtime(backup["at"]))
    name, n = f"{stem}.json", 0
    while os.path.exists(os.path.join(d, name)):
        n += 1
        name = f"{stem}-{n}.json"
    path = os.path.join(d, name)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(backup, fp, separators=(",", ":"))
    os.replace(tmp, path)
    for old in sorted(f for f in os.listdir(d) if f.endswith(".json"))[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(d, old))
        except OSError:
            pass
    return {"id": name[:-5], "at": backup["at"]}


def _load_backup(cid, backup_id):
    path = _backup_path(cid, backup_id)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError) as exc:
        ps.log.warning("HinksPix %s snapshot %s is unreadable: %s",
                       cid, backup_id, exc)
        return None


def _list_backups(cid):
    out = []
    for name in sorted(os.listdir(_backup_dir(cid)), reverse=True):
        if not name.endswith(".json"):
            continue
        b = _load_backup(cid, name[:-5])
        if b is None:
            continue
        out.append({"id": name[:-5], "at": b.get("at"),
                    "version": b.get("version"),
                    "mode": (b.get("decoded") or {}).get("mode"),
                    "summary": hc.backup_summary(b)})
    return out


def _snapshot(child):
    """Read the controller and store a snapshot — ``(saved, backup, warnings)``."""
    read, warnings = _read_device(child, with_e131=True)
    backup = hc.backup_from_read(probe=read["probe"], data_mode=read["dataMode"],
                                 board_ports=read["boards"],
                                 universe_blocks=read["e131"])
    return _save_backup(child["id"], backup), backup, warnings


def _wait_for_device(child, timeout=REBOOT_WAIT_S):
    """Wait for the controller to answer again after a reboot.

    A reboot takes the device off the network for ~20 s and it answers nothing
    until it is back. Polling for it is what turns "the requests were sent" into
    "the controller is up", which is the precondition for reading anything back.
    """
    ip = child["ip"]
    time.sleep(REBOOT_SETTLE_S)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            hb.read_board_info(ip, timeout=REBOOT_POLL_S + 2)
            return True
        except hb.HinksPixError:
            time.sleep(REBOOT_POLL_S)
    return False


def _verify(child, intended):
    """Read the controller back and diff it against what was intended.

    The universe table is read too: a port table that landed and a universe
    table that did not is a controller that looks configured and drives the
    wrong pixels. When the table cannot be read in full it is left out of the
    comparison and said so, rather than reported as a difference nobody can act
    on — an unread section is not a mismatch (#945).
    """
    try:
        read, warnings = _read_device(child, with_e131=True)
    except hb.HinksPixError as exc:
        return {"ok": False, "unknown": True, "items": [], "counts": {},
                "text": f"the controller could not be read back: {exc}",
                "warnings": []}

    rows = []
    if not warnings:
        for blk in sorted(read["e131"], key=lambda k: int(k)):
            rows.extend(read["e131"][blk])
    else:
        warnings = list(warnings) + ["the universe table was not compared"]
    current = hc.decode_device_config(board_info=read["probe"],
                                      data_mode=read["dataMode"],
                                      board_ports=read["boards"],
                                      universe_rows=rows or None)
    d = hc.diff(current, intended)
    items = d.get("items") or []
    compared = [i for i in items if not i.get("unread")]
    counts = d.get("counts") or {}
    if not compared:
        text = "the controller holds what was sent"
    else:
        text = (f"the controller differs from what was sent — "
                f"{counts.get('ports', 0)} port(s), "
                f"{counts.get('universes', 0)} universe row(s)")
    return {"ok": not compared, "changed": bool(compared), "unknown": False,
            "items": items[:40], "counts": counts, "text": text,
            "warnings": warnings}


def _run_commands(cid, ip, cmds, phase="upload", progress=None):
    """Send a command sequence, recording progress; returns the accepted entries.

    Split out of `_config_worker` because the recovery of a failed push replays
    a snapshot through exactly this loop — one implementation of "walk the
    sequence, stop at the first thing that throws, remember how far it got" is
    what makes the two paths comparable.

    The accepted entries are the ones the controller acknowledged: an entry is
    added only after its request returned, so their number is how much of the
    sequence landed. That number is what tells a failure that wrote nothing from
    one that left the device part-way (#945 F1).

    A list the caller passes as ``progress`` is filled in as the run goes, and
    is *the* record of it: the sequence stops by raising, so a caller that has
    to know how far it got cannot read a return value that never comes. That is
    why the worker keeps its own list rather than reading the return — a caller
    that only reads the return reports "nothing was written" however much
    landed, which is precisely the confusion #945 F1 is about.
    """
    done = [] if progress is None else progress
    for i, req in enumerate(cmds):
        _set_config_state(cid, phase=phase, step=i + 1,
                          message=f"{req.note} ({i + 1}/{len(cmds)})")
        entry = {"index": i, "kind": req.kind, "note": req.note}
        if req.kind == "read":
            hb.read_data_mode(ip, blk=req.blk)
        elif req.kind == "reboot":
            # Fire-and-forget by design: the controller drops the connection as
            # it restarts, so waiting on the reply would time out rather than
            # tell us anything.
            entry["sends"] = hb.fire_and_forget(ip, req.data)
        else:
            hb.command(ip, req.data, path=req.path)
        entry["ok"] = True
        done.append(entry)
        _set_config_state(cid, stepsDone=list(done))
    return done


def _recover_partial(cid, child, backup_id):
    """Put back the snapshot a failed push took — ``{ok, text, ...}``.

    Called when a push died after the controller had accepted a *write*: the
    device is holding part of a config and part of the one before it,
    which is worse than either. The snapshot taken moments earlier is the state
    it held before the first write, so replaying it is the way back, and it is
    taken automatically rather than left to a button the operator has to find
    (#945 F1).

    Deliberately **not** attempted after a failed *restore*: that run's snapshot
    is the state the operator was already trying to get away from, so replaying
    it would put them back where they started. A failure there is reported and
    the snapshot list is theirs to choose from.

    Nothing here raises: this runs inside another failure's handler, and a
    recovery that cannot be attempted must still leave a report behind.
    """
    backup = _load_backup(cid, backup_id)
    if backup is None:
        return {"ok": False, "backupId": backup_id,
                "text": f"snapshot {backup_id} could not be read back"}
    hinks = child.get("hinks") or {}
    try:
        cmds, warnings = hc.restore_commands(
            backup, mcpu=hinks.get("mcpu"),
            hardware_v3=bool(hinks.get("hardwareV3")))
    except hc.ConfigError as exc:
        return {"ok": False, "backupId": backup_id,
                "text": f"snapshot {backup_id} cannot be replayed: {exc}"}

    # The sequence on screen becomes the recovery's own, so the step list the
    # wizard draws ticks off against the requests actually being sent rather
    # than against the failed push's.
    _set_config_state(cid, phase="recover", step=0, stepsDone=[],
                      steps=[c.note for c in cmds],
                      message="Putting back the snapshot taken before this push")
    try:
        _run_commands(cid, child["ip"], cmds, phase="recover")
    except (hb.HinksPixError, OSError, ValueError) as exc:
        return {"ok": False, "backupId": backup_id, "warnings": warnings,
                "text": f"putting snapshot {backup_id} back failed: {exc}"}

    # The device now holds the snapshot, which is not the config this
    # orchestrator describes — the same bookkeeping a deliberate restore does.
    with ps._lock:
        h = child.setdefault("hinks", {})
        h["configHash"] = ""
        h["configPushedAt"] = 0
        ps._save("children", ps._children)

    _set_config_state(cid, phase="reboot",
                      message="Waiting for the controller to come back")
    if not _wait_for_device(child):
        return {"ok": False, "backupId": backup_id, "warnings": warnings,
                "text": (f"snapshot {backup_id} was written but the controller "
                         f"did not answer within {REBOOT_WAIT_S} s")}
    verify = _verify(child, hc.decode_backup(backup))
    return {"ok": bool(verify["ok"]), "backupId": backup_id, "warnings": warnings,
            "verify": verify,
            "text": (f"snapshot {backup_id} is back on the controller"
                     if verify["ok"] else
                     f"snapshot {backup_id} was written but the readback "
                     f"differs: {verify['text']}")}


def _config_worker(cid, child, cmds, intended, kind):
    """Snapshot, push, reboot, wait, verify — the whole push as one job.

    Nothing is written until the snapshot has been taken; a controller that
    cannot be read is one that cannot be put back, and overwriting it is the
    situation this exists to prevent. A failed request stops the sequence
    (every later request would be written against a device in an unknown state),
    and if it stopped *after* the controller had accepted writes, the snapshot
    taken before them is put back automatically (`_recover_partial`) — a push
    that half-landed leaves a device holding neither config (#945 F1).
    """
    ip = child["ip"]
    backup_id = None
    # Owned here, not returned: the run stops by raising, and the handler below
    # has to know how much of the sequence landed before it did (#945 F1).
    done = []
    try:
        _set_config_state(cid, phase="backup",
                          message="Snapshotting the controller before writing")
        saved, _backup, backup_warnings = _snapshot(child)
        backup_id = saved["id"]
        _set_config_state(cid, backupId=backup_id, backupWarnings=backup_warnings)

        _run_commands(cid, ip, cmds, progress=done)

        with ps._lock:
            hinks = child.setdefault("hinks", {})
            if kind == "apply":
                # The device now holds what this orchestrator describes, which
                # is what the "in sync" badge means.
                hinks["configHash"] = _config_hash(hinks)
                hinks["configPushedAt"] = int(time.time())
            else:
                # A restore puts back a snapshot that is not this config, so the
                # badge must stop claiming the two agree.
                hinks["configHash"] = ""
                hinks["configPushedAt"] = 0
            ps._save("children", ps._children)

        _set_config_state(cid, phase="reboot",
                          message="Waiting for the controller to come back")
        if _wait_for_device(child):
            with ps._lock:
                child["status"] = 1
                child["seen"] = int(time.time())
                ps._save("children", ps._children)
            _set_config_state(cid, phase="verify",
                              message="Reading the configuration back")
            verify = _verify(child, intended)
        else:
            verify = {"ok": False, "unknown": True, "items": [], "counts": {},
                      "text": (f"the controller did not answer within "
                               f"{REBOOT_WAIT_S} s of the reboot, so the "
                               f"configuration could not be verified"),
                      "warnings": []}

        record = {"at": int(time.time()), "kind": kind, "ok": bool(verify["ok"]),
                  "requests": len(cmds), "backupId": backup_id,
                  "verify": verify["text"]}
        with ps._lock:
            hinks = child.setdefault("hinks", {})
            hinks["lastApply"] = record
            hinks["lastVerify"] = {"at": record["at"], "ok": verify["ok"],
                                   "text": verify["text"],
                                   "counts": verify.get("counts") or {},
                                   "items": verify.get("items") or []}
            ps._save("children", ps._children)

        _set_config_state(cid, running=False, ok=bool(verify["ok"]),
                          phase="done", message=verify["text"], verify=verify,
                          lastApply=record)
        ps.log.info("HinksPix %s %s: %d requests, snapshot %s, verify %s", ip,
                    kind, len(cmds), backup_id,
                    "ok" if verify["ok"] else "reports differences")
    except (hb.HinksPixError, OSError, ValueError) as exc:
        step = (_config_state.get(cid) or {}).get("step")
        # How much of the sequence landed before it stopped. "Failed at the
        # snapshot" is a device that still holds what it held a moment ago;
        # "failed at step 40 of 68" is a device part-way between two configs.
        # Anything reading this job — the wizard, a script, a test — needs to
        # tell those apart without guessing (#945 F1).
        accepted = len(done)
        # A landed *read* changes nothing on the device, so it is not what makes
        # a failure "part-way" — and the recovery reboots the controller, which
        # is 90 seconds of the operator's pixels going dark. Only a write that
        # landed justifies that.
        partial = any(e.get("kind") == "write" for e in done)
        record = {"at": int(time.time()), "kind": kind, "ok": False,
                  "err": str(exc), "failedAt": step, "backupId": backup_id,
                  "partial": partial, "accepted": accepted,
                  "requests": len(cmds)}
        restore = None
        if partial and kind == "apply" and backup_id:
            restore = _recover_partial(cid, child, backup_id)
            record["restore"] = restore
        with ps._lock:
            child.setdefault("hinks", {})["lastApply"] = record
            ps._save("children", ps._children)
        # `step` goes back to where the *push* stopped: the recovery's own
        # sequence is what `steps`/`stepsDone` now describe, and without this
        # the failure would be reported at the recovery's last step instead of
        # the one that actually failed.
        _set_config_state(cid, running=False, ok=False, phase="failed",
                          err=str(exc), step=step, lastApply=record,
                          partial=partial, accepted=accepted,
                          requests=len(cmds), restore=restore,
                          restoreBackupId=backup_id if partial else None)
        ps.log.warning("HinksPix %s %s failed at %s after %d/%d request(s) (%s): %s",
                       ip, kind, f"step {step}" if step else "the snapshot",
                       accepted, len(cmds),
                       "nothing was written" if not partial else
                       ("the snapshot was put back" if (restore or {}).get("ok")
                        else f"snapshot {backup_id} still needs restoring"),
                       exc)


def _start_config_job(cid, child, cmds, intended, kind):
    """Kick the worker off — ``(response, status)``."""
    if _config_running(cid):
        return jsonify(ok=False, err=f"a configuration {kind} is already "
                                     f"running"), 409
    if _deploy_state.get(cid, {}).get("running"):
        # Both write the controller's own tables and both end with it
        # rebooting; interleaving them leaves neither in a known state.
        return jsonify(ok=False, err="a standalone deploy is running — wait for "
                                     "it to finish"), 409
    steps = [c.note for c in cmds]
    with _config_lock:
        _config_state[cid] = {"running": True, "ok": None, "phase": "start",
                              "step": 0, "steps": steps, "stepsDone": [],
                              "err": "", "verify": None, "kind": kind}
    threading.Thread(target=_config_worker, args=(cid, child, cmds, intended, kind),
                     daemon=True, name=f"hinkspix-config-{cid}").start()
    return jsonify(ok=True, started=True, kind=kind, steps=steps), 200


@bp.get("/api/hinkspix/<int:cid>/apply")
def api_hinkspix_apply_status(cid):
    """Progress of the running (or last) configuration push."""
    child, err = _child(cid)
    if err:
        return err
    hinks = child.get("hinks") or {}
    return jsonify(ok=True, state=_config_state.get(cid) or {},
                   lastApply=hinks.get("lastApply"),
                   lastVerify=hinks.get("lastVerify"),
                   inSync=bool(hinks.get("configHash")
                               and hinks["configHash"] == _config_hash(hinks)),
                   backups=_list_backups(cid))


@bp.post("/api/hinkspix/<int:cid>/apply")
def api_hinkspix_apply(cid):
    """Upload the stored config, reboot the controller, and verify it landed.

    Returns as soon as the job is running, because the sequence ends in a
    reboot and the controller is unreachable for part of it — a synchronous
    route can only sit on the socket and then guess (#945). `GET` on this same
    path reports progress, including the snapshot the run took and the diff read
    back afterwards.

    ``findings`` gate the start: an error is never passable, and a warning
    blocks until the request acknowledges it by code. ``wait: true`` runs it to
    completion and returns the final state in one call — for a script or a test
    that has no UI to poll with.
    """
    child, err = _child(cid)
    if err:
        return err
    output_map, err = _build_map_or_error(child)
    if err:
        return err
    built, findings, err = _build_plan(child, output_map)
    if err:
        return err
    intended, cmds = built

    body = request.get_json(silent=True) or {}
    blocking = _blocking_findings(findings or [], body.get("ack"))
    if blocking:
        return jsonify(ok=False, err=blocking[0].text,
                       findings=[f.to_json() for f in (findings or [])],
                       blocking=[f.to_json() for f in blocking]), 409

    started = _start_config_job(cid, child, cmds, intended, "apply")
    if not body.get("wait"):
        return started
    return _await_config_job(cid, started)


@bp.get("/api/hinkspix/<int:cid>/restore")
def api_hinkspix_restore_preview(cid):
    """The requests a restore would send, and what it cannot put back.

    A restore's warnings are not optional reading — UnPack and smart-receiver
    settings have no read CGI, so no snapshot can hold them and a restore resets
    them. Better said before the button than discovered after it.
    """
    child, err = _child(cid)
    if err:
        return err
    raw = str(request.args.get("backupId") or "")
    backup = _load_backup(cid, raw)
    if backup is None:
        return jsonify(err=f"no snapshot {raw}"), 404
    hinks = child.get("hinks") or {}
    try:
        cmds, warnings = hc.restore_commands(
            backup, mcpu=hinks.get("mcpu"),
            hardware_v3=bool(hinks.get("hardwareV3")))
    except hc.ConfigError as exc:
        return jsonify(ok=False, err=str(exc)), 400
    return jsonify(ok=True, id=raw, at=backup.get("at"),
                   summary=hc.backup_summary(backup),
                   requests=[c.to_json() for c in cmds], warnings=warnings)


@bp.post("/api/hinkspix/<int:cid>/restore")
def api_hinkspix_restore(cid):
    """Put a snapshot back on the controller, reboot, and verify.

    The same job as an apply with the requests taken from the snapshot instead
    of from the layout — including the reboot, which a restore needs for exactly
    the same reason a push does. The run snapshots first as well, so a restore
    of the wrong snapshot is itself restorable.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    raw = str(body.get("backupId") or "")
    backup = _load_backup(cid, raw)
    if backup is None:
        return jsonify(err=f"no snapshot {raw}"), 404
    hinks = child.get("hinks") or {}
    try:
        cmds, _warnings = hc.restore_commands(
            backup, mcpu=hinks.get("mcpu"),
            hardware_v3=bool(hinks.get("hardwareV3")))
    except hc.ConfigError as exc:
        return jsonify(ok=False, err=str(exc)), 400
    started = _start_config_job(cid, child, cmds, hc.decode_backup(backup),
                                "restore")
    if not body.get("wait"):
        return started
    return _await_config_job(cid, started)


def _await_config_job(cid, started):
    """Block until the job finishes and return its final state.

    The SPA polls `GET` so it can show progress; this exists for a script or a
    test that wants one call and no loop.
    """
    if started[1] != 200:
        return started
    deadline = time.time() + REBOOT_WAIT_S + 60
    while time.time() < deadline:
        state = dict(_config_state.get(cid) or {})
        if not state.get("running"):
            return jsonify(ok=bool(state.get("ok")), state=state), 200
        time.sleep(1)
    return jsonify(ok=False, err="the configuration job did not finish in time",
                   state=dict(_config_state.get(cid) or {})), 504


# ── Snapshots (#945) ─────────────────────────────────────────────────────────

@bp.post("/api/hinkspix/<int:cid>/backups")
def api_hinkspix_backup_create(cid):
    """Snapshot the controller's configuration into this device's list.

    Worth doing before any change of mind, and done automatically before every
    push. The universe table is read here as well as in the push path because a
    snapshot that cannot restore a universe map is not a restore point.
    """
    child, err = _child(cid)
    if err:
        return err
    try:
        saved, backup, warnings = _snapshot(child)
    except hb.HinksPixError as exc:
        return jsonify(ok=False, err=str(exc)), 502
    ps.log.info("HinksPix %s snapshot %s: %s", child["ip"], saved["id"],
                hc.backup_summary(backup))
    return jsonify(ok=True, id=saved["id"], at=saved["at"],
                   summary=hc.backup_summary(backup),
                   decoded=backup.get("decoded"), warnings=warnings)


@bp.get("/api/hinkspix/<int:cid>/backups")
def api_hinkspix_backup_list(cid):
    """The snapshots held for this device, newest first."""
    child, err = _child(cid)
    if err:
        return err
    return jsonify(ok=True, backups=_list_backups(cid), keep=BACKUP_KEEP)


@bp.get("/api/hinkspix/<int:cid>/backups/<backup_id>")
def api_hinkspix_backup_get(cid, backup_id):
    child, err = _child(cid)
    if err:
        return err
    backup = _load_backup(cid, backup_id)
    if backup is None:
        return jsonify(err=f"no snapshot {backup_id}"), 404
    return jsonify(ok=True, id=backup_id, summary=hc.backup_summary(backup),
                   backup=backup)


@bp.delete("/api/hinkspix/<int:cid>/backups/<backup_id>")
def api_hinkspix_backup_delete(cid, backup_id):
    child, err = _child(cid)
    if err:
        return err
    path = _backup_path(cid, backup_id)
    if not path or not os.path.exists(path):
        return jsonify(err=f"no snapshot {backup_id}"), 404
    os.remove(path)
    ps.log.info("HinksPix %s snapshot %s deleted", child["ip"], backup_id)
    return jsonify(ok=True, deleted=backup_id)



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


# ── First-time setup guide helpers (#953) ────────────────────────────────────

@bp.post("/api/hinkspix/<int:cid>/identify")
def api_hinkspix_identify(cid):
    """Light a port so the operator can see which string it drives.

    Body: ``{"port": 17}`` or ``{"ports": [17, 18]}`` or ``{"all": true}``,
    ``"pattern": "solid"|"chase"`` (chase = the "Light them all" direction
    check), ``"color": "red"|"green"|"blue"|"white"``, ``"seconds": 1..30``.
    ``{"stop": true}`` ends it early. Uses the layout SlyLED holds for the
    controller, so it lights the right string only once that layout is on the
    controller (the guide sends it first). Refused while a show is running —
    the operator's show owns the output.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    if body.get("stop"):
        return jsonify(ok=True, stopped=ps._identify_stop(cid))
    if ps._show_playback.get("running") or ps._settings.get("runnerRunning"):
        return jsonify(err="a show is running — stop it to identify ports"), 409
    ports_cfg = [int(p["port"]) for p in ((child.get("hinks") or {}).get("ports") or [])
                 if p.get("enabled", True) and int(p.get("leds") or 0) > 0]
    if body.get("all"):
        ports = ports_cfg
    elif "ports" in body:
        try:
            ports = sorted({int(x) for x in body.get("ports") or []})
        except (TypeError, ValueError):
            return jsonify(err="ports must be integers"), 400
    else:
        try:
            ports = [int(body.get("port"))]
        except (TypeError, ValueError):
            return jsonify(err="port required"), 400
    if not ports:
        return jsonify(err="no ports with a pixel count yet"), 400
    pattern = body.get("pattern", "solid")
    if pattern not in ("solid", "chase"):
        return jsonify(err="pattern must be solid or chase"), 400
    colour = str(body.get("color", "red")).lower()
    rgb = ps._IDENTIFY_COLOURS.get(colour)
    if rgb is None:
        return jsonify(err="color must be red, green, blue or white"), 400
    try:
        seconds = float(body.get("seconds", 8))
    except (TypeError, ValueError):
        return jsonify(err="seconds must be a number"), 400
    seconds = max(1.0, min(30.0, seconds))
    output, out_err = ps._ensure_output_engine(f"HinksPix {child.get('ip')} identify")
    if out_err:
        return jsonify(err=out_err, output=output), 409
    try:
        lit = ps._identify_ports(child, ports, pattern, rgb, seconds)
    except (ValueError, TypeError) as exc:
        return jsonify(err=str(exc)), 400
    hinks = child.get("hinks") or {}
    engine_proto = (ps._dmx_settings.get("protocol") or "artnet").lower()
    device_proto = (hinks.get("protocol") or "").lower()
    match = ((device_proto in ("e131", "sacn") and engine_proto == "sacn")
             or device_proto == engine_proto)
    return jsonify(ok=True, ports=lit, seconds=seconds, pattern=pattern,
                   color=colour, output=output, protocolMatch=bool(match),
                   inSync=bool(hinks.get("configHash")
                               and hinks["configHash"] == _config_hash(hinks)))


@bp.post("/api/hinkspix/<int:cid>/color-order")
def api_hinkspix_color_order(cid):
    """Work out a port's colour order from the red/green test and store it.

    Body: ``{"port": 17, "seenRed": "G", "seenGreen": "R"}`` — what the
    string showed when the guide lit it red, then green. Stores the order in
    SlyLED's copy (the same validated path as the port editor); it reaches
    the controller on the next send.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        port = int(body.get("port"))
    except (TypeError, ValueError):
        return jsonify(err="port required"), 400
    ports = [dict(p) for p in ((child.get("hinks") or {}).get("ports") or [])]
    row = next((p for p in ports if int(p.get("port") or 0) == port), None)
    if row is None:
        return jsonify(err=f"port {port} is not in the port table"), 400
    try:
        order = hc.derive_color_order(row.get("colorOrder") or "RGB",
                                      body.get("seenRed"), body.get("seenGreen"))
    except ValueError as exc:
        return jsonify(err=str(exc)), 400
    was = row.get("colorOrder") or "RGB"
    row["colorOrder"] = order
    out = {}
    rejected = _apply_config_body(child, {"ports": ports}, out)
    if rejected is not None:
        return rejected
    return jsonify(ok=True, port=port, colorOrder=order, was=was,
                   changed=(order != was), needsSend=(order != was))


@bp.post("/api/hinkspix/<int:cid>/defaults-from-fixtures")
def api_hinkspix_defaults_from_fixtures(cid):
    """Propose a port table from the fixtures already bound to this controller.

    A *proposal*: nothing is stored and nothing is written to the controller.
    The caller puts the rows in the editor for the operator to look at, because
    the one thing a "fill it in for me" button must not do is change a port the
    operator never saw (#946). ``changes`` says what would differ, and
    ``unbound`` lists the enabled ports driving nothing — the two things worth
    a second look before saving.
    """
    child, err = _child(cid)
    if err:
        return err
    return jsonify(ok=True, **hc.defaults_from_fixtures(child, ps._fixtures))


# ── xLights show-folder import (#947) ────────────────────────────────────────
# The operator already owns the show's layout in xLights: which controller, how
# many universes it feeds, which model drives which output. Retyping that into
# the port table is transcription work whose failure mode is a controller
# configured for a show nobody is running, so the layout is *read from the two
# files xLights keeps it in* and offered back as a proposal (#947).
#
# Reading and writing are two routes on purpose. The preview stores nothing; the
# accept writes the config through `_apply_config_body`, which is the same
# function the editor's own save uses — so an import cannot become a second way
# to write a config the editor would have refused.

XLI_NETWORKS_FILE = "xlights_networks.xml"
XLI_RGBEFFECTS_FILE = "xlights_rgbeffects.xml"

# Port fields worth showing in a diff. The rest of a port row (mm defaulting,
# smart-receiver labels) is normalised by the port table itself.
XLI_PORT_DIFF_KEYS = ("leds", "mm", "protocol", "colorOrder", "direction",
                      "startNulls", "brightness", "gamma", "enabled")


def _win_to_wsl(path):
    """``C:\\Users\\x`` -> ``/mnt/c/Users/x``, or None when it is not that shape."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not m:
        return None
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")


def _wsl_to_win(path):
    """``/mnt/c/Users/x`` -> ``C:\\Users\\x``, or None when it is not that shape."""
    m = re.match(r"^/mnt/([A-Za-z])/(.*)$", path)
    if not m:
        return None
    return m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\")


def _show_folder_candidates(raw):
    """The spellings of a show-folder path worth trying, most literal first.

    The orchestrator reads the folder itself, which means it needs the path in
    *its own* platform's spelling — and the operator copied it from wherever
    they happen to be looking. So a Windows path is also tried as its WSL mount
    and vice versa, and only a spelling that exists is ever opened.
    """
    raw = str(raw or "").strip().strip('"').strip("'")
    if not raw:
        return []
    out = []
    for cand in (raw, _win_to_wsl(raw), _wsl_to_win(raw)):
        if not cand:
            continue
        # A path to either file is as good as the folder holding it: the
        # operator copies whatever they had selected in their file manager.
        # Trimmed by hand rather than with `os.path.dirname`, which only knows
        # this platform's separator — on a POSIX orchestrator a Windows path has
        # no separator at all as far as it is concerned, and the folder it would
        # return is ""/"." , i.e. the orchestrator's working directory. A bare
        # filename is left alone so it fails as "no such folder" instead of
        # quietly reading whatever `xlights_networks.xml` happens to be in cwd.
        if re.search(r"\.xml$", cand, re.I):
            cand = re.sub(r"[\\/][^\\/]*$", "", cand)
        if cand not in out:
            out.append(cand)
    return out


def _read_show_folder(raw):
    """Read both xLights files out of a show folder.

    Returns ``(networks_text, rgbeffects_text, folder, err)``. A folder missing
    either file is refused rather than half-imported: without models there is
    nothing to import, and without networks there is no controller whose
    universes the channels resolve against — so in both cases the likelier
    explanation is the wrong folder, and saying which file is missing is how the
    operator finds the right one.

    ``hinks_export.json`` sits in the same folder and is deliberately never read:
    it is export-dialog state (which dialog was last open), not the show.
    """
    tried = _show_folder_candidates(raw)
    if not tried:
        return None, None, None, "showFolder is required"
    for folder in tried:
        if not os.path.isdir(folder):
            continue
        missing = [n for n in (XLI_NETWORKS_FILE, XLI_RGBEFFECTS_FILE)
                   if not os.path.isfile(os.path.join(folder, n))]
        if missing:
            return None, None, None, (f"'{folder}' has no {' or '.join(missing)}"
                                      f" — pick the folder holding xLights' show "
                                      f"files")
        try:
            texts = []
            for name in (XLI_NETWORKS_FILE, XLI_RGBEFFECTS_FILE):
                with open(os.path.join(folder, name), "r", encoding="utf-8",
                          errors="replace") as fh:
                    texts.append(fh.read())
        except OSError as exc:
            return None, None, None, f"could not read '{folder}': {exc}"
        return texts[0], texts[1], folder, None
    return None, None, None, (f"no such folder: {tried[0]}"
                              + (f" (also tried {', '.join(tried[1:])})"
                                 if len(tried) > 1 else ""))


def _show_from_upload(files):
    """Both XML files posted as a multipart form, for a remote orchestrator.

    Matched on the part's field name *and* its filename, because the sender is a
    browser file picker and what it names the field is the sender's business.
    """
    texts = {}
    for part in files.values():
        label = (getattr(part, "filename", "") or part.name or "").lower()
        if "network" in label:
            texts.setdefault("networks", part.read().decode("utf-8", "replace"))
        elif "effect" in label:
            texts.setdefault("rgbeffects", part.read().decode("utf-8", "replace"))
    missing = [n for n in ("networks", "rgbeffects") if n not in texts]
    if missing:
        return None, None, None, ("upload both xLights files (missing "
                                  + " and ".join(missing) + ")")
    return texts["networks"], texts["rgbeffects"], "(uploaded)", None


def _entries_differ(now, new, keys):
    """The subset of `keys` whose value would change, as ``{key: {from, to}}``."""
    return {k: {"from": now.get(k), "to": new.get(k)}
            for k in keys if now.get(k) != new.get(k)}


def _port_rows(hinks):
    """A hinks config's port table keyed by port number."""
    return {int(p["port"]): p for p in (hinks or {}).get("ports") or []
            if p.get("port") is not None}


def _import_diff(child, proposal):
    """The proposal against what the controller holds now.

    Four lists, because they are four different decisions: an output that is new
    (added), one whose settings would change (changed), one the show folder
    never mentions (kept), and one the operator unticked (withdrawn — the
    editor's own view of `models[]`, so it is only reported here).

    `kept` is the one worth reading. An xLights folder describes the models it
    knows about, not every output on the controller, so the ports it is silent
    about are *carried over* rather than deleted — importing a one-model show
    folder into a configured unit must not clear the other 47 outputs.
    """
    now_ports = _port_rows(child.get("hinks") or {})
    new_ports = _port_rows(proposal.get("hinks") or {})
    added = sorted(n for n in new_ports if n not in now_ports)
    changed = [{"port": n, "fields": _entries_differ(now_ports[n], new_ports[n],
                                                     XLI_PORT_DIFF_KEYS)}
               for n in sorted(new_ports)
               if n in now_ports
               and _entries_differ(now_ports[n], new_ports[n],
                                   XLI_PORT_DIFF_KEYS)]
    kept = sorted(n for n in now_ports if n not in new_ports)
    withdrawn = sorted({int(p) for row in proposal.get("models") or []
                        if isinstance(row, dict) and row.get("accepted") is False
                        for p in row.get("ports") or []})
    settings = _entries_differ(child.get("hinks") or {},
                               proposal.get("hinks") or {},
                               ("baseUniverse", "protocol", "defaults"))
    return {"added": added, "changed": changed, "kept": kept,
            "withdrawn": withdrawn, "settings": settings}


@bp.post("/api/hinkspix/import/xlights")
def api_hinkspix_import_xlights():
    """Read an xLights show folder and propose a port table from it.

    Body is ``{showFolder}`` — read by the orchestrator, so a Windows path and
    its WSL mount both work — or a multipart form carrying the two XML files,
    for an orchestrator on another machine. Optional ``cid`` names the target
    controller so the proposal can be checked against *its* boards, and
    ``controller`` names which controller in the file to import when the show
    has more than one.

    Stores nothing and opens no socket to the controller: the proposal goes back
    to the editor, which shows it against the current port table. Accepting it is
    a separate call (#947).
    """
    body = request.get_json(silent=True) or {}
    if request.files:
        networks, rgbeffects, source, err = _show_from_upload(request.files)
    else:
        networks, rgbeffects, source, err = _read_show_folder(
            body.get("showFolder"))
    if err:
        return jsonify(err=err), 400

    try:
        layout = xi.parse_show(networks, rgbeffects)
    except xi.XlightsImportError as exc:
        # `controllers` is always present in the payload, empty or not, so the
        # editor has one shape to read whether the file was unreadable or had
        # more controllers in it than the operator wants to pick from by name.
        return jsonify(err=str(exc), controllers=[]), 400

    child = None
    caps = None
    cid = body.get("cid")
    if cid is not None:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            return jsonify(err="cid must be an integer"), 400
        child, err = _child(cid)
        if err:
            return err
        caps = hc.caps_for(child.get("hinks") or {})

    names = [c.get("name") for c in layout.get("controllers") or []]
    try:
        proposal = xi.propose(layout, controller=body.get("controller"),
                              caps=caps)
    except xi.XlightsImportError as exc:
        # An ambiguous controller comes back with the list attached: the editor
        # offers the choice instead of making the operator retype a name it
        # already knows.
        return jsonify(err=str(exc), controllers=names), 400

    diff = _import_diff(child, proposal) if child is not None else None
    notes = []
    if child is None:
        notes.append("no target controller — the port table is proposed "
                     "against the HinksPix PRO's own limits, not this unit's "
                     "fitted boards")
    else:
        ip = (proposal.get("controller") or {}).get("ip")
        if ip and ip != child.get("ip"):
            notes.append(f"xLights sends to {ip}; this controller is at "
                         f"{child.get('ip')} — the address xLights has is one "
                         f"this unit must answer on")
        # The universe block is the mapping, so moving the base renumbers every
        # page below it *and* rewrites the Art-Net routes derived from it. It is
        # one number in the diff and a wholesale change on the wire, which is
        # why it is called out in words as well.
        base = (diff or {}).get("settings", {}).get("baseUniverse")
        if base and base.get("from") != base.get("to"):
            notes.append(f"the base universe would move from {base.get('from')} "
                         f"to {base.get('to')} — the universes below it and the "
                         f"routes that publish them follow, so an existing "
                         f"config is renumbered")
    proposal.update({"source": source, "controllers": names, "notes": notes,
                     "diff": diff})
    return jsonify(ok=True, **proposal)


def _proposal_body(body):
    """The proposal out of an accept body, in either shape it arrives in.

    ``{proposal: <preview>, createFixtures: bool}`` is the documented form. The
    preview response *itself* is accepted too: it is self-describing (it carries
    `hinks` and `models`), so a caller that posts back what the preview handed
    it verbatim has done nothing wrong, and making it guess which of two nesting
    levels to use is a trap with no purpose (#947 QA). Returns ``None`` when
    neither shape carries a port table, so the route can say so.
    """
    if not isinstance(body, dict):
        return None
    for cand in (body.get("proposal"), body):
        if isinstance(cand, dict) and isinstance(cand.get("hinks"), dict):
            return cand
    return None


@bp.post("/api/hinkspix/<int:cid>/import/xlights/accept")
def api_hinkspix_import_accept(cid):
    """Apply an xLights proposal: the port table, then a fixture per model.

    Body is ``{proposal, createFixtures}`` — or the preview response itself,
    which is the same thing unwrapped — where ``proposal`` is what the
    preview returned — the editor may have unticked rows in it, and an unticked
    model's outputs are dropped. Unticking narrows what is written; it never
    widens it, because a row the preview rejected was rejected for a reason the
    port table cannot hold.

    Two things are carried over deliberately. Ports the proposal never mentions
    keep the config they have, so a folder holding one model does not clear the
    other outputs; and a port the preview could not accept keeps what it has
    too. Everything written goes through `_apply_config_body`, the editor's own
    save path — same ranges, same protocol list, same DMX-out and
    smart-receiver checks.

    The config and the fixtures are one transaction: if a fixture cannot be
    created, the port table is put back rather than left half-imported.
    """
    child, err = _child(cid)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    prop = _proposal_body(body)
    if prop is None:
        return jsonify(err="the body must be the object "
                           "/api/hinkspix/import/xlights returned — either as "
                           "`proposal` or verbatim, since it carries `hinks` "
                           "and `models` itself"), 400

    hinks_before = copy.deepcopy(child.get("hinks") or {})
    fixtures_before = list(ps._fixtures)
    nxt_fix_before = ps._nxt_fix
    notes = []

    # Two rows for one output is refused rather than resolved by keeping the
    # last: the port table addresses each output once, and a body that says
    # otherwise is not a layout with a preference, it is a layout that
    # disagrees with itself. (`_apply_config_body` refuses this too — the check
    # is here because the port set below is keyed by output, so a duplicate
    # would collapse before it ever reached the validator.)
    def _port_number(row):
        try:
            return int(row.get("port"))
        except (AttributeError, TypeError, ValueError):
            return None

    numbers = [n for n in (_port_number(p) for p in prop["hinks"].get("ports") or [])
               if n is not None]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        return jsonify(err=f"the proposal names output(s) {dupes} more than "
                           f"once"), 400

    # The port set to write: the proposal's own accepted rows, minus the ones
    # the operator unticked, laid over the ports this controller already has.
    applied = {n: dict(p) for n, p in _port_rows(prop.get("hinks")).items()}
    withdrawn = []
    for row in prop.get("models") or []:
        if not isinstance(row, dict) or row.get("accepted") is not False:
            continue
        for num in row.get("ports") or []:
            if applied.pop(int(num), None) is not None:
                withdrawn.append(int(num))
    if withdrawn:
        # Unticking a row means "do not write this output" — for an output the
        # controller already has, that is leaving it alone; for a new one, it is
        # not adding it. Either way nothing is deleted, which is why the wording
        # is "not written" rather than "removed".
        notes.append(f"{len(withdrawn)} unticked output(s) not written: "
                     + ", ".join(str(n) for n in sorted(withdrawn)))

    current = _port_rows(hinks_before)
    kept = sorted(n for n in current if n not in applied)
    merged = dict(current)
    merged.update(applied)
    if kept:
        notes.append(f"{len(kept)} output(s) on this controller are not in the "
                     f"show folder and were kept: "
                     + ", ".join(str(n) for n in kept))

    cfg_body = {"ports": [merged[n] for n in sorted(merged)]}
    for key in ("baseUniverse", "protocol"):
        if key in prop["hinks"]:
            cfg_body[key] = prop["hinks"][key]
    if prop["hinks"].get("defaults"):
        cfg_body["defaults"] = prop["hinks"]["defaults"]

    out = {}
    rejected = _apply_config_body(child, cfg_body, out)
    if rejected is not None:
        # Nothing was stored — `_apply_config_body` validates the whole body
        # before it touches the child — so there is nothing to roll back.
        return rejected
    if hinks_before.get("baseUniverse") != out["hinks"].get("baseUniverse"):
        notes.append(f"base universe moved {hinks_before.get('baseUniverse')} to "
                     f"{out['hinks'].get('baseUniverse')} — the universes below "
                     f"it and their routes are renumbered")

    created, skipped = [], []
    if body.get("createFixtures"):
        rejected_names = {row.get("name") for row in prop.get("models") or []
                          if isinstance(row, dict)
                          and row.get("accepted") is False}
        try:
            with ps._lock:
                for f in prop.get("fixtures") or []:
                    if not isinstance(f, dict) or not f.get("name"):
                        continue
                    name = str(f["name"])
                    if name in rejected_names:
                        continue
                    # The pixel count comes from the port row that was just
                    # applied rather than from the proposal's own copy, so a
                    # fixture can never disagree with the output it drives.
                    strings = []
                    for s in f.get("strings") or []:
                        if not isinstance(s, dict) or s.get("port") is None:
                            continue
                        row = applied.get(int(s["port"]))
                        if row is None:
                            continue
                        strings.append({"port": int(s["port"]),
                                        "leds": int(row.get("leds") or 0),
                                        "mm": row.get("mm")})
                    if not strings:
                        skipped.append({"name": name,
                                        "reason": "no output left in the "
                                                  "accepted rows"})
                        continue
                    problem = ps._validate_fixture_strings(strings)
                    if problem is None and any(
                            x.get("childId") == cid
                            and (x.get("name") or "") == name
                            for x in ps._fixtures):
                        problem = (f"a fixture named '{name}' is already on "
                                   f"this controller")
                    if problem is None:
                        # Checked against the config that was just stored, so
                        # "the port exists and the device owns its pixel count"
                        # is a statement about the table, not about the file.
                        problem = ps._validate_fixture_ports(
                            strings, child, ps._fixtures)
                    if problem is not None:
                        skipped.append({"name": name, "reason": problem})
                        continue
                    fix = {"id": ps._nxt_fix, "name": name,
                           "fixtureType": "led", "type": "linear",
                           "childId": cid, "strings": strings}
                    ps._fixtures.append(fix)
                    ps._nxt_fix += 1
                    created.append({"id": fix["id"], "name": name,
                                    "ports": [s["port"] for s in strings]})
                if created:
                    ps._save("fixtures", ps._fixtures)
        except Exception as exc:                      # noqa: BLE001
            with ps._lock:
                child["hinks"] = hinks_before
                ps._fixtures[:] = fixtures_before
                ps._nxt_fix = nxt_fix_before
                ps._save("children", ps._children)
                ps._save("fixtures", ps._fixtures)
            old_map, _ = _build_map_or_error(child)
            if old_map is not None:
                _sync_universe_routes(child, old_map)
            ps.log.error("HinksPix %s xLights import rolled back: %s",
                         child.get("ip"), exc)
            return jsonify(err=f"import failed, nothing was changed: {exc}"), 500

    ps.log.info("HinksPix %s xLights import: %d port(s) written, %d fixture(s), "
                "%d skipped", child.get("ip"), len(applied), len(created),
                len(skipped))
    return jsonify(ok=True, hinks=out["hinks"], map=out["map"].to_json(),
                   configHash=_config_hash(out["hinks"]), created=created,
                   skipped=skipped, notes=notes,
                   findings=[f.to_json() for f in
                             _config_findings(child, out["map"])])


# ── Offline standalone playback (#941) ───────────────────────────────────────
# The controller plays .hseq sequences from its SD card against an on-board
# day/time schedule, with the orchestrator switched off. There is NO "play
# sequence N now" verb — playback is schedule-driven, so the native day/time
# model IS the feature rather than a workaround.

import hashlib as _hashlib

import hinkspix_files as hf
import hinkspix_tcp as htcp
import pixel_renderer
import schedule_compile


def _offline_check(tid, cid):
    """#963 — a sequence plays standalone only when its timeline lights this
    controller's pixels and nothing else (schedule_compile.offline_check)."""
    tl = next((t for t in ps._timelines if t.get("id") == tid), None)
    if tl is None:
        return {"eligible": False, "uses": False, "reason": f"timeline {tid} doesn't exist"}
    return schedule_compile.offline_check(tl, cid, ps._fixtures, ps._children)


def _eligibility(cid):
    return [dict(schedule_compile.offline_check(t, cid, ps._fixtures, ps._children),
                 timelineId=t.get("id"), name=t.get("name") or f"timeline {t.get('id')}")
            for t in ps._timelines]

DEPLOY_STORE = "hinkspix_deploy"
STEP_MS = 25                       # matches the live loop's 40 Hz tick

_deploy_state = {}                 # cid -> progress dict
_deploy_lock = threading.Lock()


def _deploy_cfg():
    return ps._load(DEPLOY_STORE, {}) if hasattr(ps, "_load") else {}


def _save_deploy_cfg(cfg):
    ps._save(DEPLOY_STORE, cfg)


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
    # #954 Phase 2 — a compiled show schedule deploys several playlists
    # (the wash and each offline entry) with per-day rows that name their
    # own playlist. The hand-edited standalone config keeps its one list.
    compiled = entry.get("playlists")
    if compiled:
        playlists = {hf.short_name(k): [int(t) for t in v] for k, v in compiled.items()}
        order = []
        for tids in playlists.values():
            for t in tids:
                if t not in order:
                    order.append(t)
        items = [{"timelineId": t} for t in order]
    else:
        playlists = {playlist_name: [i.get("timelineId") for i in items]}
    manifest = []
    taken = set()
    seq_for = {}

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
            seq_for[tid] = name
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
        for pl_name, tids in playlists.items():
            _set_progress(cid, phase="playlist", message=f"Uploading {pl_name}.ply")
            ply = hf.playlist_text([{"hseq": f"{seq_for[t]}.hseq"} for t in tids
                                    if t in seq_for])
            tcp.upload(f"{pl_name}.ply", ply.encode("ascii"))
            manifest.append({"name": f"{pl_name}.ply", "bytes": len(ply),
                             "ack": True})

        rows_by_day = {d: [] for d in hf.DAYS}
        if entry.get("days"):
            # Compiled (#954): rows already grouped by weekday name, each
            # naming its own playlist.
            for d in hf.DAYS:
                rows_by_day[d] = list((entry.get("days") or {}).get(d) or [])
        for row in ([] if entry.get("days") else (entry.get("schedule") or [])):
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
        # #954 — a compiled schedule under the "manual" / "shutdown" hand-off
        # policy copies the files but leaves the controller in live mode.
        switch = entry.get("switchMode", True)
        if switch:
            _set_progress(cid, phase="mode", message="Switching to standalone")
            tcp.set_mode(htcp.MODE_MASTER)

        with ps._lock:
            cfg.setdefault(str(cid), {})["lastDeploy"] = {
                "at": int(time.time()), "ok": True, "files": manifest,
                "mode": "G" if switch else "live", "clockSetAt": int(time.time()),
                "playlist": ",".join(playlists) if compiled else playlist_name,
            }
            _save_deploy_cfg(cfg)
        _set_progress(cid, running=False, ok=True, phase="done",
                      message=f"Deployed {len(rendered)} sequence(s)")
        ps.log.info("HinksPix %s: deployed %d sequences, %s mode",
                    child["ip"], len(rendered), "standalone" if switch else "live")
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
                   gate=_gate_payload(child), eligibility=_eligibility(cid))


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
        items = [{"timelineId": int(i.get("timelineId"))}
                 for i in (body["items"] or [])
                 if i.get("timelineId") is not None]
        # #963: refuse, don't store, a sequence that also needs SlyLED.
        bad = [r["reason"] for r in (_offline_check(i["timelineId"], cid) for i in items)
               if not r["eligible"]]
        if bad:
            return jsonify(err="; ".join(bad), reasons=bad), 400
        entry["items"] = items
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
    if _config_running(cid):
        return jsonify(err="a configuration push is running — wait for it to "
                           "finish"), 409

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
        chk = _offline_check(tid, cid)
        if not chk["eligible"]:
            reasons.append(chk["reason"])      # #963 — edited since it was added
        elif not ps._bake_result.get(tid):
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
