#!/usr/bin/env python3
"""
SlyLED Parent Server   " Windows / Mac desktop parent application.

Replaces the Arduino Giga R1 as the full-featured parent.  Manages layout,
timelines, spatial effects, and DMX output.

Usage (from project root):
    pip install -r desktop/windows/requirements.txt
    python desktop/shared/parent_server.py [--port 8080] [--no-browser]
"""

import argparse
import atexit
import json
import math
import os
try:
    import numpy as np
except ImportError:
    np = None
import signal
import socket
import struct
import sys
import threading
import time
import webbrowser
from pathlib import Path

import io
from flask import Flask, abort, jsonify, request, send_file, send_from_directory
import flask.cli
flask.cli.show_server_banner = lambda *a, **kw: None   # suppress dev-server warning (#289)
import logging
from datetime import datetime

from wled_bridge import (wled_probe, wled_stop,
                         wled_get_effects, wled_get_palettes, wled_get_segments)
from spatial_engine import (catmull_rom_sample, resolve_fixture,
                            evaluate_spatial_effect, blend_pixel_layers,
                            compute_pan_tilt)
from bake_engine import (bake_timeline, pack_lsq_zip, segments_to_load_steps,
                         BakeProgress)
from dmx_profiles import ProfileLibrary
import dmx_profiles
from dmx_artnet import ArtNetEngine
from dmx_sacn import sACNEngine

log = logging.getLogger("slyled")
log.setLevel(logging.DEBUG)
_log_handler = None   # file handler, created/removed by _apply_logging()

def _apply_logging(enabled, log_path=None):
    """Enable/disable file logging.  Optionally set custom log file path."""
    global _log_handler
    # Remove existing file handler
    if _log_handler:
        log.removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None
    if enabled:
        if log_path:
            log_file = Path(log_path)
            # If path is a directory (or has no extension), treat as directory and add filename
            if log_file.is_dir() or (log_file.suffix == '' and not log_file.name.endswith('.log')):
                log_file.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = log_file / f"slyled_{ts}.log"
            else:
                log_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            log_dir = DATA / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"slyled_{ts}.log"
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(fh)
        _log_handler = fh
        log.info("Logging started -> %s", fh.baseFilename)

#  "  "  Version  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

VERSION = "1.5.66"

#  "  "  UDP protocol  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

UDP_MAGIC   = 0x534C
UDP_VERSION = 4
UDP_PORT    = 4210

CMD_PING        = 0x01
CMD_PONG        = 0x02
CMD_ACTION      = 0x10
CMD_ACTION_STOP = 0x11
CMD_LOAD_STEP       = 0x20
CMD_LOAD_ACK        = 0x21
CMD_SET_BRIGHTNESS  = 0x22
CMD_RUNNER_GO       = 0x30
CMD_RUNNER_STOP = 0x31
CMD_ACTION_EVENT = 0x12
CMD_STATUS_REQ  = 0x40
CMD_STATUS_RESP = 0x41

CMD_GYRO_ORIENT = 0x60   # gyro→parent: GyroOrientPayload (8 bytes)
CMD_GYRO_CTRL   = 0x61   # parent→gyro: enabled(1) + targetFps(1)
CMD_GYRO_RECAL  = 0x62   # parent→gyro: zero IMU reference (no payload)
CMD_GYRO_COLOR  = 0x63   # gyro→parent: GyroColorPayload (r, g, b, flags)
CMD_GYRO_CALIBRATE = 0x64  # gyro→parent: calibrate start/end + orientation
CMD_GYRO_HEARTBEAT = 0x65  # parent→gyro: 2s cadence while claim active (#476)

#  "  "  Paths  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

BASE = Path(__file__).parent

# When packaged with PyInstaller --onefile, files land in sys._MEIPASS
if getattr(sys, "frozen", False):
    SPA = Path(sys._MEIPASS) / "spa"
else:
    SPA = BASE / "spa"

# Persist data under %APPDATA%\SlyLED on Windows; fall back to BASE/data elsewhere
if os.name == "nt" and os.environ.get("APPDATA"):
    DATA = Path(os.environ["APPDATA"]) / "SlyLED" / "data"
else:
    DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)

#  "  "  Persistence  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _load(name, default):
    p = DATA / f"{name}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except Exception:
        return default

def _save(name, obj):
    (DATA / f"{name}.json").write_text(json.dumps(obj, indent=2))

#  "  "  In-memory state  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_children = _load("children", [])
# Reset all children to offline on startup   " ping sweep will restore responsive ones
for _c in _children:
    _c["status"] = 0
_settings = _load("settings", {
    "name": "SlyLED", "units": 0, "canvasW": 3000, "canvasH": 2000,
    "darkMode": 1, "runnerRunning": False, "runnerElapsed": 0,
    "runnerLoop": True, "autoStartShow": False,
})
# Backfill autoStartShow for existing configs (#390)
if "autoStartShow" not in _settings:
    _settings["autoStartShow"] = False
# Boot runner state: reset unless auto-start is enabled (#390)
if not _settings.get("autoStartShow"):
    _settings["runnerRunning"] = False
    _settings["activeTimeline"] = -1
    _settings["runnerStartEpoch"] = 0
_layout  = _load("layout",  {"canvasW": 3000, "canvasH": 2000, "children": []})
_stage   = _load("stage",   {"w": 3.0, "h": 2.0, "d": 1.5})
# #628 — `stageBoundsManual` defaults False. Auto-derive runs on startup
# (after fixtures/layout/markers all load, see call below) and on each
# layout/marker write unless the operator has explicitly opted out.
if "stageBoundsManual" not in _stage:
    _stage["stageBoundsManual"] = False
_fixtures   = _load("fixtures",   [])

#  "  "  Fixture migration: backfill fixtureType on old data  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_fix_patched = False
for _f in _fixtures:
    if "fixtureType" not in _f:
        _f["fixtureType"] = "led"
        _fix_patched = True
    # Migrate aimPoint → rotation (one-time conversion)
    if _f.get("aimPoint") and (not _f.get("rotation") or _f["rotation"] == [0, 0, 0]):
        _ap = _f["aimPoint"]
        _fx = _f.get("x", 0) or 0
        _fy = _f.get("y", 0) or 0
        _fz = _f.get("z", 0) or 0
        _dx, _dy, _dz = _ap[0] - _fx, _ap[1] - _fy, _ap[2] - _fz
        _hdist = math.sqrt(_dx * _dx + _dy * _dy)  # floor plane = XY (Z=height)
        if _hdist > 0.001 or abs(_dz) > 0.001:
            _f["rotation"] = [
                round(-math.atan2(_dz, _hdist) * 180 / math.pi, 2),  # tilt (pitch)
                round(math.atan2(_dx, _dy) * 180 / math.pi, 2),       # pan (yaw)
                0
            ]
        del _f["aimPoint"]
        _fix_patched = True
    if _f.get("fixtureType") == "dmx" and "rotation" not in _f:
        _f["rotation"] = [0, 0, 0]
        _fix_patched = True
    if _f.get("fixtureType") == "camera":
        if "rotation" not in _f:
            _f["rotation"] = [0, 0, 0]
            _fix_patched = True
        if "fovDeg" not in _f:
            _f["fovDeg"] = 60
            _fix_patched = True
        # #Q12 — default existing cameras to "diagonal" (matches how most
        # webcam manufacturers publish the spec). Whitelist enforced on
        # write via _normalise_fov_type; this migration just backfills.
        _valid_ft = ("horizontal", "vertical", "diagonal")
        _stored_ft = _f.get("fovType")
        if _stored_ft is None or (isinstance(_stored_ft, str)
                                   and _stored_ft.strip().lower() not in _valid_ft):
            _f["fovType"] = "diagonal"
            _fix_patched = True
    # #484 phase 5 — strip legacy gyro-tuning fields from persisted data.
    # These were consumer-owned tunables in the delta-path era; the
    # stage-space primitive doesn't use them and the SPA no longer
    # surfaces them. Remove silently so old fixtures.json files stop
    # carrying dead weight forward.
    for _legacy in ("panScale", "tiltScale", "panCenter", "tiltCenter",
                    "panOffsetDeg", "tiltOffsetDeg"):
        if _legacy in _f:
            _f.pop(_legacy, None)
            _fix_patched = True
    # #Q7 — single-source homography. Previous versions mirrored the
    # calibration matrix onto fixture.homography so the v2 mover-cal
    # pre-check could find it without loading _calibrations. That store
    # is now authoritative on its own; strip the stale fixture-side copy
    # (which would otherwise silently lie about recalibration state).
    for _legacy_cal in ("homography", "calibrationMatrix"):
        if _legacy_cal in _f:
            _f.pop(_legacy_cal, None)
            _fix_patched = True
if _fix_patched:
    _save("fixtures", _fixtures)
del _fix_patched

def _rotation_to_aim(rotation, pos, dist=3000):
    """Convert rotation [rx, ry, rz] (degrees) + position to an aim point [x,y,z].

    rx = tilt/pitch, ry = pan/yaw.  Default distance is 3000mm (3m).
    Stage coordinates: X=width, Y=depth (forward), Z=height (up).
    """
    rx = rotation[0] if rotation else 0
    ry = rotation[1] if rotation and len(rotation) > 1 else 0
    pan_rad = math.radians(ry)
    tilt_rad = math.radians(rx)
    dx = math.sin(pan_rad) * math.cos(tilt_rad) * dist
    dy = math.cos(pan_rad) * math.cos(tilt_rad) * dist   # Y = depth (forward)
    dz = -math.sin(tilt_rad) * dist                       # Z = height (up)
    return [pos[0] + dx, pos[1] + dy, pos[2] + dz]

_objects    = _load("objects",     [])
_spatial_fx = _load("spatial_fx", [])
_timelines  = _load("timelines",  [])
_show_playlist = _load("show_playlist", {"order": [], "loopAll": False})  # {order: [tid,...], loopAll: bool}
_actions = _load("actions", [])
_wifi    = _load("wifi",    {"ssid": "", "password": ""})
_ssh     = _load("ssh",    {"sshUser": "root", "sshPassword": "", "sshKeyPath": ""})
_camera_ssh = _load("camera_ssh", {})  # {ip: {authType, user, password(encrypted), keyPath, keyStored}}
_calibrations = _load("calibrations", {})  # {fixtureId_str: {matrix, error, points, timestamp}}
_range_cal    = _load("range_calibrations", {})  # {fixtureId_str: {pan, tilt, timestamp}}
_mover_cal    = _load("mover_calibrations", {})  # {fixtureId_str: {grid, samples, ...}}
# #596 — ArUco marker registry: surveyed markers in stage space. Shared by
# the Setup tab editor and the Advanced Scan card panel; also used as
# ground-truth anchors by stereo scans once #592 lands.
# Each record: {id:int, size:float(mm), x:float, y:float, z:float,
#                rx:float(deg), ry:float(deg), rz:float(deg), label?:str}
_aruco_markers = _load("aruco_markers", [])
_ssh_bootstrapped = False  # deferred pre-population (needs _encrypt_pw defined later)


# #628 — Auto-derive stage bounds from placed fixtures + surveyed markers.
# The operator-editable free-form w/h/d values in stage.json drifted
# (live-test #628 found w=10m, d=8m against an actual 2×3.5m rig, a 5× error
# amplifier on the tracking ingest). Auto-derive replaces that guess with
# something grounded in actual placed geometry. Operator can opt back into
# manual bounds with stageBoundsManual=true on /api/stage POST.
_STAGE_PAD_MM = 500.0
_STAGE_MIN_W_M = 1.0  # keep a sane floor if fixtures/markers are missing
_STAGE_MIN_D_M = 1.0
_STAGE_MIN_H_M = 1.5


def _derive_stage_bounds():
    """Return (w_m, h_m, d_m) derived from placed fixtures + surveyed markers
    + 500 mm padding on each side. Values are stage X (width), Z (height),
    Y (depth) in metres. Missing dimensions fall back to the stored value
    then a sane minimum."""
    max_x = 0.0
    max_y = 0.0
    max_z = 0.0
    seen = False
    for c in (_layout.get("children") or []):
        x = float(c.get("x") or 0)
        y = float(c.get("y") or 0)
        z = float(c.get("z") or 0)
        if x == 0 and y == 0 and z == 0:
            continue
        max_x = max(max_x, x)
        max_y = max(max_y, y)
        max_z = max(max_z, z)
        seen = True
    for m in (_aruco_markers or []):
        x = float(m.get("x") or 0)
        y = float(m.get("y") or 0)
        z = float(m.get("z") or 0)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
        max_z = max(max_z, z)
        seen = True
    if not seen:
        return (max(_stage.get("w", _STAGE_MIN_W_M), _STAGE_MIN_W_M),
                max(_stage.get("h", _STAGE_MIN_H_M), _STAGE_MIN_H_M),
                max(_stage.get("d", _STAGE_MIN_D_M), _STAGE_MIN_D_M))
    w_m = max((max_x + _STAGE_PAD_MM) / 1000.0, _STAGE_MIN_W_M)
    d_m = max((max_y + _STAGE_PAD_MM) / 1000.0, _STAGE_MIN_D_M)
    h_m = max((max_z + _STAGE_PAD_MM) / 1000.0, _STAGE_MIN_H_M)
    return (w_m, h_m, d_m)


def _apply_auto_stage_bounds(*, save=True):
    """Recompute auto bounds and write to _stage unless manual override is on.
    Call this on startup, on /api/layout POST, on /api/aruco/markers POST,
    and on fixture create/delete/reposition."""
    if _stage.get("stageBoundsManual"):
        return False
    w_m, h_m, d_m = _derive_stage_bounds()
    changed = (abs(_stage.get("w", 0) - w_m) > 1e-3
               or abs(_stage.get("h", 0) - h_m) > 1e-3
               or abs(_stage.get("d", 0) - d_m) > 1e-3)
    if not changed:
        return False
    _stage["w"] = w_m
    _stage["h"] = h_m
    _stage["d"] = d_m
    # Keep canvas dims (mm) in sync with stage (m) — matches /api/stage POST.
    try:
        _settings["canvasW"] = int(w_m * 1000)
        _settings["canvasH"] = int(h_m * 1000)
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
    except Exception:
        pass
    if save:
        _save("stage", _stage)
    return True


# Live action events pushed by children (ip  -' {actionType, stepIndex, totalSteps, event, ts})
_live_events = {}

# Live gyro orientation data keyed by child IP
# {ip: {roll, pitch, yaw, fps, flags, ts}}
_gyro_state = {}
_gyro_lock  = threading.Lock()

def _gyro_fixture_for_ip(ip: str):
    """Return the gyro fixture whose gyroChildId points at a child with this IP."""
    return next((f for f in _fixtures if f.get("fixtureType") == "gyro"
                 and f.get("gyroChildId") is not None
                 and next((c for c in _children if c["id"] == f["gyroChildId"]
                           and c.get("ip") == ip), None)), None)

def _gyro_assigned_mover_id(ip: str):
    gf = _gyro_fixture_for_ip(ip)
    return gf.get("assignedMoverId") if gf else None

def _gyro_device_name(ip: str, gf=None):
    if gf is None:
        gf = _gyro_fixture_for_ip(ip)
    if gf and gf.get("gyroChildId") is not None:
        c = next((ch for ch in _children if ch["id"] == gf["gyroChildId"]), None)
        if c:
            return c.get("altName") or c.get("name") or c.get("hostname") or ip
    return ip

def _apply_gyro_color(gyro_ip: str, r: int, g: int, b: int, flash: bool):
    """Route gyro colour through unified MoverControlEngine. Legacy direct-write removed."""
    if not _mover_engine:
        return
    gf = next((f for f in _fixtures if f.get("fixtureType") == "gyro"
               and f.get("gyroChildId") is not None
               and next((c for c in _children if c["id"] == f["gyroChildId"]
                         and c.get("ip") == gyro_ip), None)), None)
    if not gf or not gf.get("assignedMoverId"):
        return
    mid = gf["assignedMoverId"]
    did = f"gyro-{gyro_ip}"
    if flash:
        _mover_engine.flash(mid, did)
    else:
        _mover_engine.set_color(mid, did, r, g, b)

# Recent PONGs seen by UDP listener (ip  -' parsed pong info)   " used by discover
_recent_pongs = {}

# Bake state (Phase 5)
_bake_progress = None   # BakeProgress instance while baking
_bake_result = {}       # timeline_id  -' bake result dict

# Apply logging from saved settings on startup
_apply_logging(_settings.get("logging", False))

_nxt_c = max((c["id"] for c in _children), default=-1) + 1
_nxt_a = max((a["id"] for a in _actions),  default=-1) + 1
_nxt_fix = max((f["id"] for f in _fixtures),   default=-1) + 1
_nxt_obj = max((f["id"] for f in _objects),    default=-1) + 1
_temporal_objects = []  # in-memory only, never saved
_nxt_tmp = 10000       # temporal IDs start at 10000 to avoid collision
_nxt_sfx = max((f["id"] for f in _spatial_fx),  default=-1) + 1
_nxt_tl  = max((t["id"] for t in _timelines),  default=-1) + 1
_lock  = threading.Lock()

#  "  "  DMX subsystems  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_profile_lib = ProfileLibrary(data_dir=str(DATA))
_artnet = ArtNetEngine()
_sacn = sACNEngine()

_shutdown_blackout_done = False

def _graceful_dmx_shutdown():
    """Blackout and stop every running DMX engine so downstream bridges don't
    latch on the last cue when the orchestrator exits. Idempotent — safe to
    call from atexit, signal handlers, and /api/shutdown. (#601)
    """
    global _shutdown_blackout_done
    if _shutdown_blackout_done:
        return
    _shutdown_blackout_done = True
    for eng in (_artnet, _sacn):
        try:
            if eng.running:
                eng.stop()
        except Exception:
            pass
    # #598 — stop the depth-runtime subprocess too so it doesn't
    # outlive us (the runner has its own idle timer but prompt exit
    # is cleaner and frees localhost ports immediately).
    try:
        import depth_runtime as _dr
        _dr.stop_runner()
    except Exception:
        pass

atexit.register(_graceful_dmx_shutdown)

def _signal_shutdown_handler(signum, frame):
    _graceful_dmx_shutdown()
    os._exit(0)

for _sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
    _sig = getattr(signal, _sig_name, None)
    if _sig is not None:
        try:
            signal.signal(_sig, _signal_shutdown_handler)
        except (ValueError, OSError):
            pass  # e.g. not on main thread, or unsupported on platform

#  "  "  UDP helpers  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _hdr(cmd, epoch=0):
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd,
                       epoch or (int(time.time()) & 0xFFFFFFFF))

def _send(ip, pkt):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(pkt, (ip, UDP_PORT))
    except Exception:
        pass

def _local_broadcasts():
    """Return subnet-directed broadcast addresses for all non-loopback interfaces."""
    bcs = []
    for prefix in _local_subnet_prefixes():
        bc = prefix + ".255"
        if bc not in bcs:
            bcs.append(bc)
    return bcs

def _local_subnet_prefixes():
    """Return /24 subnet prefixes (e.g. '192.168.10') for all non-loopback interfaces.

    Primary method parses `ip -4 addr show` so WSL2 mirrored-mode hosts (where
    the Linux hostname only resolves to one of several mirrored NICs) still
    see every physical subnet. Falls back to getaddrinfo then _get_local_ip()
    on platforms without the `ip` command.
    """
    prefixes = []
    seen = set()

    # Method 1: parse `ip -4 addr show` — enumerates every attached interface,
    # which is the only reliable way to catch all mirrored NICs under WSL2.
    try:
        import subprocess, re
        out = subprocess.check_output(["ip", "-4", "addr", "show"],
                                      text=True, timeout=3)
        for m in re.finditer(r"inet (\d+\.\d+\.\d+)\.\d+/\d+", out):
            prefix = m.group(1)
            if prefix in seen:
                continue
            if prefix.startswith("127.") or prefix.startswith("169.254."):
                continue
            # Skip the WSL2 NAT bridge (172.x) when real mirrored adapters are
            # also present — the NAT bridge has no path to external LAN devices.
            if prefix.startswith("172.") and prefixes:
                continue
            prefixes.append(prefix)
            seen.add(prefix)
    except Exception:
        pass

    # Method 2: socket.getaddrinfo — works on Windows/macOS hosts without `ip`.
    if not prefixes:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                prefix = ip.rsplit(".", 1)[0]
                if prefix not in seen:
                    prefixes.append(prefix)
                    seen.add(prefix)
        except Exception:
            pass

    # Method 3: _get_local_ip() last resort — single primary-interface prefix.
    if not prefixes:
        try:
            prefix = _get_local_ip().rsplit(".", 1)[0]
            if prefix:
                prefixes.append(prefix)
        except Exception:
            pass
    return prefixes

def _send_recv(ip, pkt, timeout=1.5, maxb=256):
    """Send UDP packet and wait for reply from the specified IP only.
    Binds to UDP_PORT (with SO_REUSEADDR) so the child replies to the
    firewall-allowed port 4210.  Falls back to an ephemeral port if 4210
    is momentarily busy.  Discards packets from other sources.
    """
    for bind_port in (UDP_PORT, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(timeout)
                s.bind(("", bind_port))
                s.sendto(pkt, (ip, UDP_PORT))
                deadline = time.time() + timeout
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    s.settimeout(remaining)
                    data, addr = s.recvfrom(maxb)
                    if addr[0] == ip:
                        return data
                    # else: discard stale packet from different source
        except OSError:
            if bind_port == 0:
                return None   # ephemeral port also failed
            continue          # port 4210 busy   " retry with ephemeral
        except Exception:
            return None
    return None

def _parse_pong(data, src_ip):
    # PONG v4: 8-byte header + 133-byte PongPayload = 141 bytes (v3: 139 bytes)
    # PongPayload: hostname[10]+altName[16]+desc[32]+stringCount(1)+PongString[8] --9+fwMajor(1)+fwMinor(1)
    if not data or len(data) < 139:  # backward compat: accept v3 (139) and v4 (141)
        return None
    if data[3] != CMD_PONG:
        return None
    p  = data[8:]
    hn = p[0:10].rstrip(b"\x00").decode("ascii", "replace")
    nm = p[10:26].rstrip(b"\x00").decode("ascii", "replace")
    ds = p[26:58].rstrip(b"\x00").decode("ascii", "replace")
    sc = p[58]
    strings = []
    off = 59
    for _ in range(8):
        leds, mm, tp, cd, cm, sd = struct.unpack_from("<HHBBHB", p, off)
        strings.append({"leds": leds, "mm": mm, "type": tp,
                         "cdir": cd, "cmm": cm, "sdir": sd,
                         "folded": bool(cd & 0x01)})
        off += 9
    # Firmware version: v4.0 added fwMajor+fwMinor (141 bytes), v5.3.6+ adds fwPatch (142 bytes)
    fw_ver = None
    if len(data) >= 142:
        fw_ver = f"{p[131]}.{p[132]}.{p[133]}"
    elif len(data) >= 141:
        fw_ver = f"{p[131]}.{p[132]}"
    # Detect gyro boards: stringCount=0 + hostname starts with SLYG
    board_type = None
    if sc == 0 and hn.upper().startswith("SLYG"):
        board_type = "gyro"
    result = {
        "hostname": hn, "name": nm or hn, "desc": ds, "sc": sc,
        "strings": strings, "ip": src_ip,
        "status": 1, "seen": int(time.time()),
        "fwVersion": fw_ver,
    }
    if board_type:
        result["type"] = board_type
        result["boardType"] = "Gyro Controller"
    return result

def _probe_board_type(child):
    """Fetch board type, version, and telemetry from child's HTTP /status endpoint."""
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://{child['ip']}/status", method="GET")
        resp = _ur.urlopen(req, timeout=2)
        data = json.loads(resp.read().decode("utf-8"))
        board = data.get("board")
        if board:
            board_map = {"esp32": "ESP32", "d1mini": "D1 Mini", "giga-child": "Giga",
                         "dmx-bridge": "DMX Bridge", "gyro": "Gyro Controller"}
            child["boardType"] = board_map.get(board, board)
        # Detect DMX bridge from boardType field in /status
        bt = data.get("boardType")
        if bt == "dmx":
            child["type"] = "dmx"
        # Detect gyro board from role or board field in /status
        role = data.get("role")
        if role == "gyro" or board == "gyro":
            child["type"] = "gyro"
        # Full version from /status (3-part: 5.3.2) overrides PONG's 2-part version
        version = data.get("version")
        if version:
            child["fwVersion"] = version
        # Extended telemetry
        for key in ("rssi", "chipModel", "chipTemp", "flashSize", "freeHeap",
                     "sdkVersion", "uptime"):
            if key in data:
                child[key] = data[key]
    except Exception:
        pass

def _ping(child, retries=2):
    """Send CMD_PING and update child from PONG response.
    Retries up to `retries` times on timeout before marking offline.
    """
    pkt = _hdr(CMD_PING)
    for _ in range(retries + 1):
        resp = _send_recv(child["ip"], pkt)
        info = _parse_pong(resp, child["ip"])
        if info:
            # Don't let PONG's 2-digit fwVersion overwrite a more detailed 3-digit version
            saved_fw = child.get("fwVersion", "")
            child.update({k: v for k, v in info.items() if k != "id"})
            if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                child["fwVersion"] = saved_fw
            # Always probe for full telemetry (version, board type, RSSI, etc.)
            _probe_board_type(child)
            return True
    child["status"] = 0
    return False

def _broadcast_ping_all():
    """Send broadcast PINGs + direct pings to all known children.
    The UDP listener daemon handles incoming PONGs  -' _recent_pongs."""
    pkt = _hdr(CMD_PING)
    for c in list(_children):
        _send(c["ip"], pkt)
    for bc in ["255.255.255.255"] + _local_broadcasts():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(pkt, (bc, UDP_PORT))
        except Exception:
            pass

def _discover_all():
    """Broadcast PING, wait for listener to collect PONGs, return all by hostname."""
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(2.0)
    return {info.get("hostname"): info for ip, info in _recent_pongs.items()
            if info.get("hostname")}

def _discover():
    """Broadcast PING, wait for listener to collect PONGs, return unknown devices.
    Includes LED performers, DMX bridges, camera nodes, and Art-Net bridges
    that speak ArtPoll (even ones that don't respond to SlyLED's own UDP
    PING). The single Setup → Discover button covers every hardware type
    — there's no dedicated per-type discovery anywhere else (#564).
    """
    known_ips = {c["ip"] for c in _children}
    known_hosts = {c.get("hostname") for c in _children}
    known_cam_ips = {f.get("cameraIp") for f in _fixtures
                     if f.get("fixtureType") == "camera" and f.get("cameraIp")}
    # Fire ArtPoll in parallel with the SlyLED PING broadcast so a
    # single 2 s wait catches both kinds of responders.
    _recent_pongs.clear()
    _broadcast_ping_all()
    try:
        _artnet_oneshot_poll()  # broadcasts + listens ~2 s for ArtPollReply
    except Exception as e:
        log.debug("_discover ArtPoll leg failed: %s", e)
    time.sleep(2.0)
    results = []
    pong_ips = set()
    for ip, info in _recent_pongs.items():
        pong_ips.add(ip)
        if ip in known_cam_ips:
            continue
        if ip in known_ips or info.get("hostname") in known_hosts:
            continue
        # Probe /status to detect board type — try port 80 (performers), then 5000 (cameras)
        import urllib.request as _ur
        board_type = info.get("boardType", "slyled")  # preserve PONG-detected type
        for probe_port in (80, 5000):
            try:
                resp = _ur.urlopen(f"http://{ip}:{probe_port}/status", timeout=2)
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("role") == "camera":
                    board_type = "camera"
                    info.update({
                        "fovDeg": data.get("fovDeg"),
                        "resolutionW": data.get("resolutionW"),
                        "resolutionH": data.get("resolutionH"),
                        "cameraUrl": data.get("cameraUrl", ""),
                    })
                    break
                if data.get("role") == "gyro" or data.get("board") == "gyro":
                    board_type = "Gyro Controller"
                    info["type"] = "gyro"
                    break
                board_type = data.get("boardType", board_type)
                break
            except Exception:
                continue
        info["boardType"] = board_type
        results.append(info)
    # Merge in any Art-Net bridges that replied to ArtPoll but not to
    # SlyLED's UDP PING (third-party Enttec, old Giga bridges without
    # the PONG extension). Skip our own server address so we don't
    # "discover" ourselves.
    own_ip = _get_local_ip()
    for ip, node in (getattr(_artnet, "_discovered", None) or {}).items():
        if ip == own_ip:
            continue
        if ip in known_ips or ip in pong_ips:
            continue
        results.append({
            "ip": ip,
            "hostname": node.get("shortName") or ip,
            "name": node.get("longName") or node.get("shortName") or ip,
            "type": "dmx",
            "boardType": "DMX Bridge",
            "sc": 0,
            "strings": [],
        })
    return results

#  "  "  Async discover / refresh state  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_discover_state = {"pending": False, "data": []}
_refresh_state  = {"pending": False, "data": {}}

def _discover_bg():
    """Run _discover() in background, store results."""
    try:
        _discover_state["data"] = _discover()
    finally:
        _discover_state["pending"] = False

def _refresh_bg():
    """Run refresh-all logic in background, store results."""
    try:
        _recent_pongs.clear()
        _broadcast_ping_all()
        time.sleep(2.5)
        responded_ips = set(_recent_pongs.keys())
        responded_hostnames = {info.get("hostname") for info in _recent_pongs.values()}
        for c in _children:
            if c.get("type") == "wled":
                wled_info = wled_probe(c["ip"], timeout=2.0)
                if wled_info:
                    c["status"] = 1
                    c["seen"] = int(time.time())
                else:
                    c["status"] = 0
            elif c["ip"] in responded_ips or c.get("hostname") in responded_hostnames:
                for ip, info in _recent_pongs.items():
                    if info.get("hostname") == c.get("hostname"):
                        if ip != c["ip"]:
                            c["ip"] = ip
                        c.update({k: v for k, v in info.items() if k != "id"})
                        break
            else:
                c["status"] = 0
        with _lock:
            _save("children", _children)
        online = sum(1 for c in _children if c.get("status") == 1)
        _refresh_state["data"] = {"ok": True, "total": len(_children), "online": online}
    finally:
        _refresh_state["pending"] = False

def _child_led_ranges(child):
    """Build ledStart[8] / ledEnd[8] as uint16 arrays from child's string config.
    ESP32 multi-string: strings are concatenated in one leds[] array,
    so string N starts at the sum of all previous string lengths.
    For unconfigured strings: 0xFFFF (sentinel)."""
    ls = [0xFFFF] * 8
    le = [0xFFFF] * 8
    sc = child.get("sc", 0)
    strings = child.get("strings", [])
    offset = 0
    for j in range(min(sc, len(strings), 8)):
        leds = strings[j].get("leds", 0)
        if leds > 0:
            ls[j] = offset
            le[j] = offset + leds - 1
            offset += leds
    return struct.pack("<8H", *ls), struct.pack("<8H", *le)

def _act_params(act):
    """Extract generic param fields from an action dict, all coerced to int."""
    t = act.get("type", 0)
    r, g, b = act.get("r", 0), act.get("g", 0), act.get("b", 0)
    p16a = act.get("speedMs", act.get("periodMs", act.get("spawnMs", 500)))
    p8a = act.get("p8a", act.get("r2", act.get("minBri", act.get("spacing",
           act.get("paletteId", act.get("cooling", act.get("tailLen",
           act.get("density", 0))))))))
    p8b = act.get("p8b", act.get("g2", act.get("sparking", 0)))
    p8c = act.get("p8c", act.get("b2", act.get("direction", 0)))
    p8d = act.get("p8d", act.get("decay", act.get("fadeSpeed", 0)))
    return tuple(int(v or 0) for v in (t, r, g, b, p16a, p8a, p8b, p8c, p8d))

def _load_step_pkt(idx, total, step, child, delay_ms=0):
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(step)
    dur = int(step.get("durationS", 5) or 5)
    # Check for per-string LED range override from bake
    if "_ledOffset" in step:
        # Target specific string's LED range only
        ls = [0xFFFF] * 8
        le = [0xFFFF] * 8
        si = step.get("_stringIndex", 0)
        ls[si] = step["_ledOffset"]
        le[si] = step["_ledOffset"] + step["_ledCount"] - 1
        ls = struct.pack("<8H", *ls)
        le = struct.pack("<8H", *le)
    else:
        ls, le = _child_led_ranges(child)
    pl = struct.pack("<BBBBBBHBBBBHH", idx, total, t, r, g, b, p16a, p8a, p8b, p8c, p8d, dur, int(delay_ms))
    return _hdr(CMD_LOAD_STEP) + pl + ls + le

#  "  "  Flask application  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

app = Flask(__name__, static_folder=None)

#  "  "  Status  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/favicon.ico")
def favicon():
    abort(404)

@app.get("/status")
def status():
    return jsonify(role="parent", hostname=socket.gethostname(), version=VERSION)

#  "  "  Children  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

CHILD_STALE_S = 120   # mark offline if not seen for 2 minutes
_startup_check_done = False

def _periodic_ping():
    """Background thread: broadcast PING periodically.  The UDP listener
    daemon picks up PONGs and updates child records   " no per-child
    send_recv needed, so there are no port conflicts."""
    global _startup_check_done
    # Startup sweep: ping twice with a gap for slow booters
    _broadcast_ping_all()
    _startup_check_done = True
    time.sleep(5)
    _broadcast_ping_all()
    with _lock:
        # Mark children not seen recently as offline
        now = int(time.time())
        for c in _children:
            if c.get("seen", 0) > 0 and now - c["seen"] > CHILD_STALE_S:
                c["status"] = 0
        _save("children", _children)
    # Periodic sweep every 30 seconds
    while True:
        time.sleep(30)
        _broadcast_ping_all()
        # Also probe WLED devices via HTTP
        for c in list(_children):
            if c.get("type") == "wled":
                info = wled_probe(c["ip"], timeout=2.0)
                if info:
                    c["status"] = 1
                    c["seen"] = int(time.time())
                    c["fwVersion"] = info.get("ver")
                else:
                    c["status"] = 0
        time.sleep(2)   # allow PONGs to arrive
        with _lock:
            now = int(time.time())
            for c in _children:
                if c.get("type") != "wled" and c.get("seen", 0) > 0 and now - c["seen"] > CHILD_STALE_S:
                    c["status"] = 0
            _save("children", _children)

def _udp_listener():
    """Background daemon: persistent bind on UDP_PORT, receives ACTION_EVENT packets from children."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", UDP_PORT))
        s.settimeout(1.0)
    except OSError as e:
        print(f"[udp-listener] Could not bind port {UDP_PORT}: {e}")
        return
    while True:
        try:
            data, addr = s.recvfrom(256)
        except socket.timeout:
            continue
        except Exception:
            continue
        if len(data) < 8:
            continue
        try:
            magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        except Exception:
            continue
        if magic != UDP_MAGIC or ver not in (3, UDP_VERSION):
            continue
        ip = addr[0]
        if cmd == CMD_ACTION_EVENT and len(data) >= 12:
            at, si, tot, ev = struct.unpack_from("<BBBB", data, 8)
            _live_events[ip] = {
                "actionType": at, "stepIndex": si,
                "totalSteps": tot, "event": ev,
                "ts": time.time(),
            }
            log.debug("ACTION_EVENT from %s: type=%d step=%d/%d event=%s",
                       ip, at, si, tot, "started" if ev == 0 else "ended")
        elif cmd == CMD_GYRO_ORIENT and len(data) >= 16:
            # GyroOrientPayload: roll100(2) pitch100(2) yaw100(2) fps(1) flags(1)
            roll100, pitch100, yaw100, fps, flags = struct.unpack_from("<hhhBB", data, 8)

            # flags bit 3 = stop signal → release claim + blackout + stale cal
            if flags & 0x08:
                try:
                    log.info("GYRO_STOP from %s — releasing claim, clearing cal", ip)
                    did_stop = f"gyro-{ip}"
                    if _mover_engine:
                        gf_stop = _gyro_fixture_for_ip(ip)
                        if gf_stop and gf_stop.get("assignedMoverId") is not None:
                            _mover_engine.release(gf_stop["assignedMoverId"],
                                                  did_stop, blackout=True)
                    # Invalidate the primitive's calibration too — next Start
                    # must re-align before the fixture can follow again.
                    remote_stop = _remotes.by_device(did_stop)
                    if remote_stop is not None:
                        remote_stop.end_session()
                        try:
                            _remotes.save()
                        except Exception as e:
                            log.error("remotes.save() during stop failed: %s", e)
                except Exception as e:
                    log.error("GYRO_STOP handler failed: %s", e, exc_info=True)
            else:
                with _gyro_lock:
                    _gyro_state[ip] = {
                        "roll":  roll100  / 100.0,
                        "pitch": pitch100 / 100.0,
                        "yaw":   yaw100   / 100.0,
                        "fps":   fps,
                        "flags": flags,
                        "ts":    time.time(),
                    }
                log.debug("GYRO_ORIENT from %s: R=%.1f P=%.1f Y=%.1f fps=%d",
                          ip, roll100/100.0, pitch100/100.0, yaw100/100.0, fps)
                # Primitive owns orientation (#484 phase 4). Mover-follow
                # reads Remote.aim_stage via its tick loop — no legacy call
                # here any more.
                device_id = f"gyro-{ip}"
                remote = _auto_register_remote(device_id, kind=KIND_PUCK)
                remote.update_from_euler_deg(
                    roll100/100.0, pitch100/100.0, yaw100/100.0,
                )
                # Auto-claim on first orient if the fixture is enabled but
                # the claim was lost (e.g. after Stop). Avoids forcing the
                # user back to the SPA to click Send Lock again — pressing
                # START on the puck re-engages DMX output.
                if _mover_engine and not _mover_engine.get_claim(
                        _gyro_assigned_mover_id(ip) or -1):
                    gf = _gyro_fixture_for_ip(ip)
                    if gf and gf.get("gyroEnabled") and gf.get("assignedMoverId") is not None:
                        dname = _gyro_device_name(ip, gf)
                        _mover_engine.claim(gf["assignedMoverId"], device_id,
                                              dname, "gyro",
                                              smoothing=gf.get("smoothing", 0.15))
                        _mover_engine.start_stream(gf["assignedMoverId"], device_id)
        elif cmd == CMD_GYRO_COLOR and len(data) >= 12:
            # GyroColorPayload: r(1) g(1) b(1) flags(1)
            r, g, b, flags = struct.unpack_from("<BBBB", data, 8)
            flash = bool(flags & 0x01)
            log.info("GYRO_COLOR from %s: r=%d g=%d b=%d flash=%s", ip, r, g, b, flash)
            _apply_gyro_color(ip, r, g, b, flash)
        elif cmd == CMD_GYRO_CALIBRATE and len(data) >= 15:
            # GyroCalibratePayload: calibrating(1) roll100(2) pitch100(2) yaw100(2)
            calibrating, roll100, pitch100, yaw100 = struct.unpack_from("<Bhhh", data, 8)
            roll = roll100 / 100.0
            pitch = pitch100 / 100.0
            yaw = yaw100 / 100.0
            log.info("GYRO_CALIBRATE from %s: cal=%d R=%.1f P=%.1f Y=%.1f",
                     ip, calibrating, roll, pitch, yaw)
            # Resolve the gyro fixture + target mover for this puck.
            _gf3 = next((f for f in _fixtures if f.get("fixtureType") == "gyro"
                         and f.get("gyroChildId") is not None
                         and next((c for c in _children if c["id"] == f["gyroChildId"]
                                   and c.get("ip") == ip), None)), None)
            target_mover_id = _gf3.get("assignedMoverId") if _gf3 else None
            did = f"gyro-{ip}"
            if target_mover_id is not None:
                # State transition on the claim (hold DMX during align).
                if calibrating:
                    _mover_engine.calibrate_start(target_mover_id, did)
                else:
                    # Primitive computes R_world_to_stage against the mover's
                    # current stage aim; engine resumes streaming.
                    mover = _mover_fixture(target_mover_id)
                    remote = _remotes.by_device(did) or _auto_register_remote(did, kind=KIND_PUCK)
                    if mover is not None:
                        aim_stage = _mover_current_aim_stage(mover)
                        try:
                            remote.calibrate(
                                target_aim_stage=aim_stage,
                                target_info={"objectId": mover["id"], "kind": "mover"},
                                roll=roll, pitch=pitch, yaw=yaw,
                            )
                            _remotes.save()
                            log.info("Remote %d calibrated via UDP against mover %d aim=%s",
                                     remote.id, mover["id"], aim_stage)
                        except Exception as e:
                            log.error("Remote %d calibrate failed: %s", remote.id, e)
                    _mover_engine.calibrate_end(target_mover_id, did)
        elif cmd == CMD_PONG:
            # Handle PONGs from broadcast/direct pings
            info = _parse_pong(data, ip)
            if info:
                log.debug("PONG from %s (%s) fw=%s", ip, info.get("hostname"), info.get("fwVersion"))
                # Store for discover to find
                _recent_pongs[ip] = info
                # Update known children
                for c in _children:
                    if c.get("ip") == ip or c.get("hostname") == info.get("hostname"):
                        saved_fw = c.get("fwVersion", "")
                        c.update({k: v for k, v in info.items() if k != "id"})
                        # Preserve 3-digit version over PONG's 2-digit
                        if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                            c["fwVersion"] = saved_fw
                        _probe_board_type(c)
                        break
        else:
            log.debug("UDP cmd=0x%02X from %s (%d bytes)", cmd, ip, len(data))

def _bootstrap_ssh_defaults():
    """Pre-populate SSH credentials on first run (default OrangePi/RPi creds)."""
    global _ssh, _ssh_bootstrapped
    if _ssh_bootstrapped:
        return
    _ssh_bootstrapped = True
    if not _ssh.get("sshPassword") and not _ssh.get("sshKeyPath"):
        import pathlib
        key_path = str(pathlib.Path.home() / ".ssh" / "id_ed25519")
        _ssh["sshUser"] = "root"
        _ssh["sshPassword"] = _encrypt_pw("orangepi")
        if pathlib.Path(key_path).exists():
            _ssh["sshKeyPath"] = key_path
        _save("ssh", _ssh)
        log.info("SSH defaults set: root/orangepi, key=%s", _ssh.get("sshKeyPath") or "(none)")

def _heartbeat_loop():
    """#476 — Emit CMD_GYRO_HEARTBEAT to every puck with an active claim.

    Runs every 2 s. The puck treats the heartbeat as "parent is alive and
    still holds your claim"; if the puck doesn't hear one for >5 s it
    shows "RECON", and >20 s it drops back to IDLE. Silence is symmetric
    with the consumer-side auto-release: server times out at 60 s, puck
    times out at 20 s — both resolve to "operator must Send-Lock again".
    """
    while True:
        try:
            claims = _mover_engine.get_status() if _mover_engine else []
        except Exception:
            claims = []
        for claim in claims:
            did = claim.get("deviceId") or ""
            if not did.startswith("gyro-"):
                continue
            ip = did[len("gyro-"):]
            if not ip:
                continue
            try:
                state_byte = 1 if claim.get("state") == "streaming" else 0
                active_byte = 1
                pkt = _hdr(CMD_GYRO_HEARTBEAT) + bytes([state_byte, active_byte])
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(pkt, (ip, UDP_PORT))
                sock.close()
            except Exception as e:
                log.debug("heartbeat to %s failed: %s", ip, e)
        time.sleep(2.0)


def start_background_tasks():
    """Call once after import to kick off periodic ping and UDP listener threads."""
    global _startup_check_done
    _bootstrap_ssh_defaults()
    threading.Thread(target=_udp_listener, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    if _children:
        threading.Thread(target=_periodic_ping, daemon=True).start()
    else:
        _startup_check_done = True
    _check_depth_install_marker()
    # No auto-claim on boot. The UDP CMD_GYRO_ORIENT handler auto-claims
    # on the first orient packet from an enabled gyro fixture, which is
    # the operator pressing Start on the puck. That's what turns the
    # fixture on — the server staying silent on boot lets the fixture
    # hold its blackout until the operator actively starts.

@app.get("/api/children")
def api_children():
    now = int(time.time())
    for c in _children:
        if c.get("status") == 1 and c.get("seen", 0) > 0:
            if now - c["seen"] > CHILD_STALE_S:
                c["status"] = 0
    return jsonify([dict(c, startupDone=_startup_check_done) for c in _children])

@app.get("/api/children/discover")
def api_children_discover():
    if _discover_state["pending"]:
        return jsonify(pending=True)
    # Start background discovery
    _discover_state["pending"] = True
    _discover_state["data"] = []
    threading.Thread(target=_discover_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/children/discover/results")
def api_children_discover_results():
    if _discover_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_discover_state["data"])

@app.get("/api/children/export")
def api_children_export():
    return jsonify(_children)

@app.post("/api/children")
def api_children_add():
    global _nxt_c
    ip = (request.get_json(silent=True) or {}).get("ip", "").strip()
    # Sanitize: strip protocol prefix and any path/port suffix
    ip = ip.replace("https://", "").replace("http://", "").split("/")[0].strip()
    if not ip:
        return jsonify(ok=False, err="ip required"), 400
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        if not addr.is_private:
            return jsonify(ok=False, err="Only private/LAN IP addresses allowed"), 400
    except ValueError:
        return jsonify(ok=False, err="Invalid IP address"), 400
    # Prevent duplicate IP entries
    existing = next((c for c in _children if c.get("ip") == ip), None)
    if existing:
        return jsonify(ok=True, id=existing["id"], duplicate=True)
    child = {"ip": ip, "hostname": ip, "name": ip,
             "desc": "", "sc": 0, "strings": [], "status": 0, "seen": 0,
             "type": "slyled"}
    with _lock:
        child["id"] = _nxt_c
        _nxt_c += 1
        _children.append(child)
        _save("children", _children)
    # Try SlyLED PING first
    _ping(child)
    # Capability-probe for camera nodes on port 5000 — don't rely on the
    # PONG description string. /status on a camera node returns role=="camera".
    # Routing detected cameras to type="camera" lets the SPA register them
    # via /api/cameras (creates fixtureType="camera" per sensor) rather than
    # auto-spawning an LED fixture.
    is_camera = False
    try:
        import urllib.request as _ur
        resp = _ur.urlopen(f"http://{ip}:5000/status", timeout=2)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("role") == "camera":
            is_camera = True
    except Exception:
        pass
    if is_camera:
        # Match the Discover Cameras flow: camera nodes are represented only
        # as camera fixtures (addressed by cameraIp), not as children. Drop
        # the speculative child record we just wrote so Setup → Children
        # doesn't show a dead "slyled" row next to the real camera fixture.
        with _lock:
            _children[:] = [c for c in _children if c["id"] != child["id"]]
            _save("children", _children)
        return jsonify(ok=True, id=None, type="camera", name=child.get("name", ip),
                       hostname=child.get("hostname", ip), ip=ip)
    # If SlyLED ping failed, try WLED probe
    if child.get("status") != 1:
        wled_info = wled_probe(ip)
        if wled_info:
            child["type"] = "wled"
            child["hostname"] = wled_info["name"]
            child["name"] = wled_info["name"]
            child["sc"] = 1
            child["strings"] = [{"leds": wled_info["ledCount"], "mm": 0,
                                  "type": 0, "cdir": 0, "cmm": 0, "sdir": 0, "folded": False}]
            child["status"] = 1
            child["seen"] = int(time.time())
            child["fwVersion"] = wled_info["ver"]
            child["wled"] = wled_info
            log.info("WLED device found at %s: %s (%d LEDs, v%s)",
                     ip, wled_info["name"], wled_info["ledCount"], wled_info["ver"])
    with _lock:
        _save("children", _children)
    ct = child.get("type", "slyled")
    return jsonify(ok=True, id=child["id"], type=ct, boardType=child.get("boardType", ""),
                   name=child.get("name", ""), hostname=child.get("hostname", ""),
                   ip=ip)

@app.delete("/api/children/<int:cid>")
def api_children_delete(cid):
    global _children
    with _lock:
        n = len(_children)
        _children = [c for c in _children if c["id"] != cid]
        if len(_children) == n:
            abort(404)
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/<int:cid>/refresh")
def api_children_refresh(cid):
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        abort(404)
    _ping(child)          # ping outside lock so DELETE/other requests aren't blocked
    with _lock:
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/<int:cid>/reboot")
def api_children_reboot(cid):
    """Send HTTP POST /reboot to a child, causing it to restart."""
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        abort(404)
    ip = child["ip"]
    log.info("REBOOT: sending to %s (%s)", ip, child.get("hostname"))
    try:
        import urllib.request
        req = urllib.request.Request(f"http://{ip}/reboot", method="POST", data=b"")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass  # child reboots immediately, response may not arrive
    child["status"] = 0
    with _lock:
        _save("children", _children)
    return jsonify(ok=True)

@app.post("/api/children/refresh-all")
def api_children_refresh_all():
    """Broadcast ping all children. Non-blocking - starts background thread."""
    if _refresh_state["pending"]:
        return jsonify(pending=True)
    _refresh_state["pending"] = True
    _refresh_state["data"] = {}
    threading.Thread(target=_refresh_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/children/refresh-all/results")
def api_children_refresh_all_results():
    if _refresh_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_refresh_state["data"])

@app.get("/api/children/<int:cid>/status")
def api_child_status(cid):
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        return jsonify(ok=False, err="not found")
    resp = _send_recv(child["ip"], _hdr(CMD_STATUS_REQ))
    if not resp or len(resp) < 16:
        return jsonify(ok=False, err="timeout")
    aa, ra, cs, rssi, up = struct.unpack_from("<BBBbI", resp, 8)
    return jsonify(ok=True, activeAction=aa, runnerActive=bool(ra),
                   currentStep=cs, wifiRssi=rssi, uptimeS=up)

@app.post("/api/children/import")
def api_children_import():
    global _nxt_c
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        abort(400)
    added = updated = skipped = 0
    with _lock:
        for c in data:
            ex = next((x for x in _children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = _nxt_c
                _nxt_c += 1
                _children.append(c)
                added += 1
        _save("children", _children)
    return jsonify(ok=True, added=added, updated=updated, skipped=skipped)

#  "  "  WLED device API  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_wled_cache = {}   # child_id  -' {"effects": [...], "palettes": [...], "ts": epoch}
_WLED_CACHE_TTL = 300  # 5 minutes

@app.get("/api/wled/effects/<int:cid>")
def api_wled_effects(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    now = time.time()
    cached = _wled_cache.get(cid)
    if cached and cached.get("effects") and now - cached.get("ts", 0) < _WLED_CACHE_TTL:
        return jsonify(cached["effects"])
    effects = wled_get_effects(child["ip"])
    if effects is None:
        return jsonify(ok=False, err="device unreachable"), 502
    _wled_cache.setdefault(cid, {})["effects"] = effects
    _wled_cache[cid]["ts"] = now
    return jsonify(effects)

@app.get("/api/wled/palettes/<int:cid>")
def api_wled_palettes(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    now = time.time()
    cached = _wled_cache.get(cid)
    if cached and cached.get("palettes") and now - cached.get("ts", 0) < _WLED_CACHE_TTL:
        return jsonify(cached["palettes"])
    palettes = wled_get_palettes(child["ip"])
    if palettes is None:
        return jsonify(ok=False, err="device unreachable"), 502
    _wled_cache.setdefault(cid, {})["palettes"] = palettes
    _wled_cache[cid]["ts"] = now
    return jsonify(palettes)

@app.get("/api/wled/segments/<int:cid>")
def api_wled_segments(cid):
    child = next((c for c in _children if c["id"] == cid and c.get("type") == "wled"), None)
    if not child:
        return jsonify(ok=False, err="WLED device not found"), 404
    # Try cached segments from probe first
    segs = child.get("wled", {}).get("segments")
    if segs:
        return jsonify(segs)
    segs = wled_get_segments(child["ip"])
    if segs is None:
        return jsonify(ok=False, err="device unreachable"), 502
    return jsonify(segs)

#  "  "  Layout  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/layout")
def api_layout_get():
    layout = dict(_layout)
    # Merge fixture positions into fixture objects for the SPA
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    child_map = {c["id"]: c for c in _children}
    layout["fixtures"] = []
    for f in _fixtures:
        fid = f["id"]
        pos = pos_map.get(fid, pos_map.get(f.get("childId"), {}))
        fixture_data = {**f}
        # Merge string data from linked child if fixture doesn't have its own
        if f.get("childId") is not None and not fixture_data.get("strings"):
            child = child_map.get(f["childId"])
            if child:
                fixture_data["strings"] = child.get("strings", [])
                fixture_data["sc"] = child.get("sc", 0)
        layout["fixtures"].append({
            **fixture_data,
            "x": pos.get("x", 0),
            "y": pos.get("y", 0),
            "z": pos.get("z", 0),
            "positioned": fid in pos_map or f.get("childId") in pos_map,
        })
    # Legacy: keep children for backward compat with bake/resolve
    layout["children"] = _layout.get("children", [])
    return jsonify(layout)

@app.post("/api/layout")
def api_layout_save():
    body = request.get_json(silent=True) or {}
    # #543 — prefer `children` (the positioned-item list). The SPA sometimes
    # posts the full cached layout object which carries both arrays; the
    # canonical position data lives in `children`, while `fixtures` is the
    # fixture registry and has x/y/z pinned at 0 from the server side.
    # Reading `fixtures` first silently discarded every position edit.
    fixtures = body.get("children") or body.get("fixtures") or []
    _layout["children"] = [{"id": f["id"], "x": f.get("x", 0), "y": f.get("y", 0), "z": f.get("z", 0)} for f in fixtures]
    _save("layout", _layout)
    _apply_auto_stage_bounds()  # #628
    return jsonify(ok=True)

#  "  "  Stage  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/stage")
def api_stage_get():
    # #628 — also report what auto-derive would produce right now so the UI
    # can show a "Auto: X.XX × Y.YY m" hint alongside the active value.
    auto_w, auto_h, auto_d = _derive_stage_bounds()
    out = dict(_stage)
    out["auto"] = {"w": auto_w, "h": auto_h, "d": auto_d}
    return jsonify(out)

@app.post("/api/stage")
def api_stage_save():
    body = request.get_json(silent=True) or {}
    # #628 — operator can toggle between auto-derived and manual bounds via
    # stageBoundsManual. When the flag is *explicitly* in the body, honour
    # it as-given (so the SPA can turn auto-derive back on even while the
    # form is simultaneously sending the currently-displayed w/h/d). When
    # it's absent, fall back to the stored value.
    manual_flag_sent = "stageBoundsManual" in body
    if manual_flag_sent:
        _stage["stageBoundsManual"] = bool(body["stageBoundsManual"])
    for k in ("w", "h", "d"):
        if k in body:
            v = body[k]
            if not isinstance(v, (int, float)) or v <= 0:
                return jsonify(err=f"Stage dimension '{k}' must be a positive number"), 400
            _stage[k] = float(v)
            # Writing explicit dimensions without ever mentioning the flag
            # is the legacy code path; treat that as manual intent so older
            # callers don't get their values auto-clobbered. Newer callers
            # set stageBoundsManual alongside and win either way.
            if not manual_flag_sent:
                _stage["stageBoundsManual"] = True
    # If the operator flipped manual off, recompute from geometry.
    if not _stage.get("stageBoundsManual"):
        _apply_auto_stage_bounds(save=False)
    _save("stage", _stage)
    # Sync canvas dimensions (mm) from stage (meters)
    with _lock:
        _settings["canvasW"] = int(_stage["w"] * 1000)
        _settings["canvasH"] = int(_stage["h"] * 1000)
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
        _save("settings", _settings)
        _save("layout", _layout)
        _sync_locked_objects()
    return jsonify(ok=True)


#  "  "  Fixtures (Phase 2)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/fixtures")
def api_fixtures_get():
    return jsonify(_fixtures)

@app.post("/api/fixtures")
def api_fixtures_create():
    global _nxt_fix
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    ftype = body.get("type", "linear")
    if ftype not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    fixture_type = body.get("fixtureType", "led")
    if fixture_type not in ("led", "dmx", "camera", "gyro"):
        return jsonify(err="Invalid fixtureType - must be 'led', 'dmx', 'camera', or 'gyro'"), 400
    # DMX-specific validation
    if fixture_type == "dmx":
        dmx_uni = body.get("dmxUniverse")
        dmx_addr = body.get("dmxStartAddr")
        dmx_ch = body.get("dmxChannelCount")
        if not isinstance(dmx_uni, int) or dmx_uni < 1:
            return jsonify(err="dmxUniverse must be an integer >= 1"), 400
        if not isinstance(dmx_addr, int) or dmx_addr < 1 or dmx_addr > 512:
            return jsonify(err="dmxStartAddr must be 1-512"), 400
        if not isinstance(dmx_ch, int) or dmx_ch < 1:
            return jsonify(err="dmxChannelCount must be an integer >= 1"), 400
    # Camera-specific validation
    if fixture_type == "camera":
        fov = body.get("fovDeg")
        if fov is not None and (not isinstance(fov, (int, float)) or fov < 1 or fov > 180):
            return jsonify(err="fovDeg must be 1-180"), 400
        # #Q12 — fovType whitelist
        if "fovType" in body and body["fovType"] is not None:
            ft_raw = body["fovType"]
            if not isinstance(ft_raw, str) or ft_raw.strip().lower() not in _FOV_TYPE_WHITELIST:
                return jsonify(err=f"fovType must be one of {list(_FOV_TYPE_WHITELIST)}"), 400
    with _lock:
        f = {
            "id": _nxt_fix, "name": name or f"Fixture {_nxt_fix}",
            "fixtureType": fixture_type,
            "childId": body.get("childId"), "type": ftype,
            "childIds": body.get("childIds", []),  # for group fixtures
            "strings": body.get("strings", []),
            "rotation": body.get("rotation", [0, 0, 0]),  # [rx, ry, rz] degrees   " overrides child stripDir
            "aoeRadius": body.get("aoeRadius", 1000),
            "meshFile": body.get("meshFile"),
        }
        if fixture_type == "dmx":
            f["dmxUniverse"] = body["dmxUniverse"]
            f["dmxStartAddr"] = body["dmxStartAddr"]
            f["dmxChannelCount"] = body["dmxChannelCount"]
            f["dmxProfileId"] = body.get("dmxProfileId")
        if fixture_type == "camera":
            f["fovDeg"] = body.get("fovDeg", 60)
            f["fovType"] = _normalise_fov_type(body.get("fovType"))
            f["cameraUrl"] = body.get("cameraUrl", "")
            f["resolutionW"] = body.get("resolutionW", 1920)
            f["resolutionH"] = body.get("resolutionH", 1080)
            f["trackClasses"] = body.get("trackClasses", ["person"])
            f["trackFps"] = body.get("trackFps", 2)
            f["trackThreshold"] = body.get("trackThreshold", 0.4)
            f["trackTtl"] = body.get("trackTtl", 5)
            f["trackReidMm"] = body.get("trackReidMm", 500)
            f["trackInputSize"] = body.get("trackInputSize", 320)
        if fixture_type == "gyro":
            f["gyroChildId"]       = body.get("gyroChildId")       # child record ID of the gyro board
            f["assignedMoverId"]   = body.get("assignedMoverId")   # fixture ID of the DMX mover to control
            f["gyroEnabled"]       = body.get("gyroEnabled", False)
            f["smoothing"]         = body.get("smoothing", 0.15)   # EMA factor 0-1 (only operator tunable)
        _fixtures.append(f)
        _nxt_fix += 1
        _save("fixtures", _fixtures)
    return jsonify(ok=True, id=f["id"])

@app.get("/api/fixtures/<int:fid>")
def api_fixture_get(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    return jsonify(f)

@app.put("/api/fixtures/<int:fid>")
def api_fixture_update(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    # Validate fixtureType if changing
    if "fixtureType" in body and body["fixtureType"] not in ("led", "dmx", "camera", "gyro"):
        return jsonify(err="Invalid fixtureType - must be 'led', 'dmx', 'camera', or 'gyro'"), 400
    # Validate geometry type if changing
    if "type" in body and body["type"] not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    # Validate DMX fields
    ft = body.get("fixtureType", f.get("fixtureType", "led"))
    if ft == "dmx":
        addr = body.get("dmxStartAddr", f.get("dmxStartAddr"))
        if "dmxStartAddr" in body:
            if not isinstance(addr, int) or addr < 1 or addr > 512:
                return jsonify(err="dmxStartAddr must be 1-512"), 400
        uni = body.get("dmxUniverse", f.get("dmxUniverse"))
        if "dmxUniverse" in body:
            if not isinstance(uni, int) or uni < 1:
                return jsonify(err="dmxUniverse must be an integer >= 1"), 400
        ch = body.get("dmxChannelCount", f.get("dmxChannelCount"))
        if "dmxChannelCount" in body:
            if not isinstance(ch, int) or ch < 1:
                return jsonify(err="dmxChannelCount must be an integer >= 1"), 400
    # Validate camera fields
    if ft == "camera" and "fovDeg" in body:
        fov = body["fovDeg"]
        if not isinstance(fov, (int, float)) or fov < 1 or fov > 180:
            return jsonify(err="fovDeg must be 1-180"), 400
    # #Q12 — fovType whitelist
    if ft == "camera" and "fovType" in body and body["fovType"] is not None:
        ft_raw = body["fovType"]
        if not isinstance(ft_raw, str) or ft_raw.strip().lower() not in _FOV_TYPE_WHITELIST:
            return jsonify(err=f"fovType must be one of {list(_FOV_TYPE_WHITELIST)}"), 400
    if ft == "camera":
        if "trackClasses" in body:
            tc = body["trackClasses"]
            if not isinstance(tc, list) or not tc or not all(isinstance(c, str) for c in tc):
                return jsonify(err="trackClasses must be a non-empty list of strings"), 400
        if "trackFps" in body:
            v = body["trackFps"]
            if not isinstance(v, (int, float)) or v < 0.5 or v > 10:
                return jsonify(err="trackFps must be 0.5-10"), 400
        if "trackThreshold" in body:
            v = body["trackThreshold"]
            if not isinstance(v, (int, float)) or v < 0.1 or v > 0.95:
                return jsonify(err="trackThreshold must be 0.1-0.95"), 400
        if "trackTtl" in body:
            v = body["trackTtl"]
            if not isinstance(v, (int, float)) or v < 1 or v > 60:
                return jsonify(err="trackTtl must be 1-60"), 400
        if "trackReidMm" in body:
            v = body["trackReidMm"]
            if not isinstance(v, (int, float)) or v < 50 or v > 5000:
                return jsonify(err="trackReidMm must be 50-5000"), 400
    for k in ("name", "type", "fixtureType", "childId", "childIds", "strings",
              "rotation", "orientation", "mountedInverted", "aoeRadius", "meshFile",
              "dmxUniverse", "dmxStartAddr", "dmxChannelCount", "dmxProfileId",
              "fovDeg", "fovType", "cameraUrl", "cameraIp", "cameraIdx", "resolutionW", "resolutionH",
              "trackClasses", "trackFps", "trackThreshold", "trackTtl", "trackReidMm",
              "gyroChildId", "assignedMoverId", "gyroEnabled", "smoothing"):
        if k in body:
            # #Q12 — normalise fovType on write so stored value is always in
            # the whitelist (inputs go through _normalise_fov_type).
            if k == "fovType":
                f[k] = _normalise_fov_type(body[k])
            else:
                f[k] = body[k]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

@app.put("/api/fixtures/<int:fid>/aim")
def api_fixture_set_aim(fid):
    """Set rotation for a DMX or camera fixture.

    Accepts either {rotation: [rx, ry, rz]} or legacy {aimPoint: [x,y,z]}
    (converted to rotation on import).
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") not in ("dmx", "camera"):
        return jsonify(err="DMX or camera fixture not found"), 404
    body = request.get_json(silent=True) or {}
    # Accept rotation directly
    rot = body.get("rotation")
    if isinstance(rot, list) and len(rot) == 3:
        try:
            f["rotation"] = [float(v) for v in rot]
        except (TypeError, ValueError):
            return jsonify(err="rotation values must be numbers"), 400
        _save("fixtures", _fixtures)
        return jsonify(ok=True)
    # Legacy aimPoint → convert to rotation
    ap = body.get("aimPoint")
    if not isinstance(ap, list) or len(ap) != 3:
        return jsonify(err="rotation must be [rx,ry,rz]"), 400
    try:
        ap = [float(v) for v in ap]
    except (TypeError, ValueError):
        return jsonify(err="aimPoint values must be numbers"), 400
    fx = f.get("x", 0) or 0
    fy = f.get("y", 0) or 0
    fz = f.get("z", 0) or 0
    dx, dy, dz = ap[0] - fx, ap[1] - fy, ap[2] - fz
    hdist = math.sqrt(dx * dx + dy * dy)  # floor plane = XY (Z=height)
    if hdist > 0.001 or abs(dz) > 0.001:
        f["rotation"] = [
            round(-math.atan2(dz, hdist) * 180 / math.pi, 2),
            round(math.atan2(dx, dy) * 180 / math.pi, 2),
            f.get("rotation", [0, 0, 0])[2] if f.get("rotation") else 0
        ]
    _save("fixtures", _fixtures)
    return jsonify(ok=True, rotation=f.get("rotation", [0, 0, 0]))

@app.delete("/api/fixtures/<int:fid>")
def api_fixture_delete(fid):
    global _fixtures
    if not any(f["id"] == fid for f in _fixtures):
        return jsonify(ok=False, err="fixture not found"), 404
    _fixtures = [f for f in _fixtures if f["id"] != fid]
    _save("fixtures", _fixtures)
    return jsonify(ok=True)

# ── Gyro API ─────────────────────────────────────────────────────────────

GYRO_STALE_S = 2.0  # seconds before orientation data is considered stale

@app.get("/api/gyro/state")
def api_gyro_state():
    """Return live orientation for all known gyro boards.
    Each entry: {ip, roll, pitch, yaw, fps, streaming, imuOk, stale}
    """
    now = time.time()
    with _gyro_lock:
        result = []
        for ip, g in _gyro_state.items():
            stale = (now - g["ts"]) > GYRO_STALE_S
            flags = g.get("flags", 0)
            result.append({
                "ip":        ip,
                "roll":      round(g["roll"], 2),
                "pitch":     round(g["pitch"], 2),
                "yaw":       round(g["yaw"], 2),
                "fps":       g["fps"],
                "streaming": bool(flags & 0x01),
                "imuOk":     bool(flags & 0x02),
                "mode":      (flags >> 4) & 0x03,
                "stale":     stale,
                "ts":        g["ts"],
            })
    return jsonify(result)

def _gyro_child_ip(child_id):
    """Return IP for a child by ID, or None if not found / offline."""
    c = next((c for c in _children if c["id"] == child_id), None)
    if not c:
        return None, jsonify(err="gyro child not found"), 404
    if c.get("status") != 1:
        return None, jsonify(err="gyro child offline"), 503
    return c["ip"], None, None

@app.post("/api/gyro/<int:child_id>/enable")
def api_gyro_enable(child_id):
    """Send CMD_GYRO_CTRL(enabled=1) to the gyro board at child_id."""
    ip, err, code = _gyro_child_ip(child_id)
    if err:
        return err, code
    fps = request.get_json(silent=True, force=True) or {}
    target_fps = int(fps.get("fps", 20)) if isinstance(fps, dict) else 20
    target_fps = max(1, min(50, target_fps))
    pkt = _hdr(CMD_GYRO_CTRL) + struct.pack("<BB", 1, target_fps)
    _send(ip, pkt)
    # Auto-claim the assigned mover via unified engine (#468)
    gf = next((f for f in _fixtures if f.get("fixtureType") == "gyro"
               and f.get("gyroChildId") == child_id), None)
    if gf and gf.get("assignedMoverId") and _mover_engine:
        device_id = f"gyro-{ip}"
        c = next((ch for ch in _children if ch["id"] == child_id), None)
        dname = c.get("altName") or c.get("name") or c.get("hostname") or ip if c else ip
        _mover_engine.claim(gf["assignedMoverId"], device_id, dname, "gyro",
                            smoothing=gf.get("smoothing", 0.15))
        # Don't start_stream here — light stays off until user presses
        # START on gyro and first CMD_GYRO_ORIENT arrives
    return jsonify(ok=True)

@app.post("/api/gyro/<int:child_id>/disable")
def api_gyro_disable(child_id):
    """Send CMD_GYRO_CTRL(enabled=0) to the gyro board at child_id."""
    ip, err, code = _gyro_child_ip(child_id)
    if err:
        return err, code
    pkt = _hdr(CMD_GYRO_CTRL) + struct.pack("<BB", 0, 0)
    _send(ip, pkt)
    # Auto-release the assigned mover (#468)
    gf = next((f for f in _fixtures if f.get("fixtureType") == "gyro"
               and f.get("gyroChildId") == child_id), None)
    if gf and gf.get("assignedMoverId") and _mover_engine:
        _mover_engine.release(gf["assignedMoverId"], f"gyro-{ip}")
    return jsonify(ok=True)

# ── Camera discovery & CRUD ─────────────────────────────────────────────

_cam_discover_state = {"pending": False, "data": []}

def _probe_camera(ip, timeout=2):
    """Probe a camera node via HTTP GET /status. Returns info dict or None.

    #561 — the `role: \"camera\"` check was rejecting every real camera
    because the Orange Pi firmware never emitted that field. Current
    firmware responses look like:
        {\"board\": \"sun55iw3\", \"cameraCount\": 2, \"cameras\": [...]}
    Recognise the response via any of these signals:
        - explicit `role == \"camera\"` (future firmware)
        - `cameras` is a non-empty list (current firmware)
        - `cameraCount` > 0 (current firmware)
    """
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/status", timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        looks_like_camera = (
            data.get("role") == "camera"
            or (isinstance(data.get("cameras"), list) and len(data["cameras"]) > 0)
            or data.get("cameraCount", 0) > 0
        )
        if not looks_like_camera:
            return None
        return {
            "ip": ip,
            "hostname": data.get("hostname", ip),
            "name": data.get("hostname", ip),
            "fwVersion": data.get("fwVersion", data.get("version", "")),
            "fovDeg": data.get("fovDeg"),
            "resolutionW": data.get("resolutionW"),
            "resolutionH": data.get("resolutionH"),
            "capabilities": data.get("capabilities", {}),
            "cameraUrl": data.get("cameraUrl", ""),
            "cameras": data.get("cameras", []),
            "cameraCount": data.get("cameraCount", 0),
            "rssi": data.get("rssi", 0),
        }
    except Exception:
        return None

def _discover_cameras():
    """Scan all local subnets for camera nodes in parallel, return unregistered ones.

    Sequential probing was ~76s per /24 subnet (254 × 0.3s) and linear in the
    number of subnets, which blew past the browser poll timeout on multi-NIC
    hosts. The ThreadPoolExecutor mirrors the pattern used by _scan_ssh_devices.

    #542 — the first scan after the SPA opens sometimes misses a camera that
    a second scan finds. Root cause is a lost first probe (ARP cache miss,
    cold HTTP accept queue, WiFi scanner stealing the radio briefly). Two
    passes with a short back-off between them catches the slow responders
    without doubling the happy-path time since pass 2 only retries the IPs
    that returned nothing the first time.
    """
    import concurrent.futures, time as _time
    known_ips = set()
    for f in _fixtures:
        if f.get("fixtureType") == "camera" and f.get("cameraIp"):
            known_ips.add(f["cameraIp"])

    ips_to_probe = []
    for prefix in _local_subnet_prefixes():
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            if ip not in known_ips:
                ips_to_probe.append(ip)

    # Pass 1 — 64 workers + 1.2s timeout. WSL2-measured full HTTP round-trip
    # to an Orange Pi on /24 is ~330 ms and can spike to 700 ms under a
    # loaded WiFi radio. 0.8 s was cutting it too close (#562) — bumped to
    # 1.2 s so the common case reliably catches the camera on pass 1.
    found = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        for ip, info in zip(ips_to_probe,
                            pool.map(lambda ip: _probe_camera(ip, timeout=1.2), ips_to_probe)):
            if info:
                found[ip] = info
    # Pass 2 — retry anything that didn't answer with a longer 2.0 s
    # timeout. Skip the retry entirely when pass 1 already got a full
    # complement (no known cold-start cases ever go past two responders).
    missing = [ip for ip in ips_to_probe if ip not in found]
    if missing:
        _time.sleep(0.15)  # let the radio settle / ARP cache warm up
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
            for ip, info in zip(missing,
                                pool.map(lambda ip: _probe_camera(ip, timeout=2.0), missing)):
                if info:
                    found[ip] = info
    return list(found.values())

def _cam_discover_bg():
    try:
        _cam_discover_state["data"] = _discover_cameras()
    finally:
        _cam_discover_state["pending"] = False

@app.get("/api/cameras")
def api_cameras():
    """List registered camera fixtures with live status.

    Also syncs `fixture.name` from the node's per-sensor `customName` so
    operator renames done on the RPi's `/config` page propagate to the
    Setup tab without requiring re-registration. Node is source of truth
    when online; stored name survives offline.
    """
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    result = []
    dirty = False
    for c in cams:
        cam = dict(c)
        ip = c.get("cameraIp")
        cam["online"] = False
        cam["tracking"] = _tracking_state.get(c["id"], False)
        if ip:
            info = _probe_camera(ip, timeout=1)
            if info:
                cam["online"] = True
                cam["fwVersion"] = info.get("fwVersion", "")
                cam["hostname"] = info.get("hostname", "")
                cam["capabilities"] = info.get("capabilities", {})
                cam["rssi"] = info.get("rssi", 0)
                # Sync fixture name from node customName when present.
                sensors = info.get("cameras", [])
                idx = c.get("cameraIdx", 0)
                if idx < len(sensors):
                    live_name = sensors[idx].get("customName") or ""
                    if live_name and live_name != c.get("name"):
                        c["name"] = live_name
                        cam["name"] = live_name
                        dirty = True
                # Note: camera node trackingRunning is node-level, not per-sensor.
                # Trust _tracking_state (per-fixture) instead of overriding from
                # the node capability, which would mark all sensors on the same
                # IP as tracking when only one was started.
        result.append(cam)
    if dirty:
        with _lock:
            _save("fixtures", _fixtures)
    return jsonify(result)

@app.get("/api/cameras/discover")
def api_cameras_discover():
    if _cam_discover_state["pending"]:
        return jsonify(pending=True)
    _cam_discover_state["pending"] = True
    _cam_discover_state["data"] = []
    threading.Thread(target=_cam_discover_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/cameras/discover/results")
def api_cameras_discover_results():
    if _cam_discover_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_cam_discover_state["data"])

@app.post("/api/cameras/probe")
def api_cameras_probe():
    """Probe a single IP for a camera node."""
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    if not ip:
        return jsonify(ok=False, err="ip required"), 400
    info = _probe_camera(ip, timeout=3)
    if info:
        return jsonify(ok=True, info=info)
    return jsonify(ok=False, err="No camera found"), 404

def _camera_fov_from_info(info, cam_idx=0):
    """Extract per-camera FOV from probe info, falling back to node-level."""
    if not info:
        return None
    cameras = info.get("cameras", [])
    if cam_idx < len(cameras) and "fovDeg" in cameras[cam_idx]:
        return cameras[cam_idx]["fovDeg"]
    return info.get("fovDeg")

@app.post("/api/cameras")
def api_cameras_register():
    """Register a camera node — creates a camera fixture."""
    global _nxt_fix
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    if not ip:
        return jsonify(err="ip required"), 400
    import ipaddress as _ipa
    try:
        addr = _ipa.ip_address(ip)
        if not addr.is_private:
            return jsonify(err="Only private/LAN IP addresses allowed"), 400
    except ValueError:
        return jsonify(err="Invalid IP address"), 400
    # Probe camera for info
    info = _probe_camera(ip, timeout=3)
    cameras = (info or {}).get("cameras", [])
    base_name = body.get("name") or (info.get("hostname") if info else None) or f"Camera {ip}"

    # Create one fixture per camera sensor (not one per node)
    created_ids = []
    with _lock:
        for cam_idx in range(max(1, len(cameras))):
            # Check for duplicate (same IP + same camera index)
            dup = next((f for f in _fixtures if f.get("fixtureType") == "camera"
                        and f.get("cameraIp") == ip and f.get("cameraIdx", 0) == cam_idx), None)
            if dup:
                continue
            cam_info = cameras[cam_idx] if cam_idx < len(cameras) else {}
            # Prefer the operator-set customName from the node's /config page.
            # Single-sensor node → use customName directly; multi-sensor →
            # prefix with the node name so both sensors remain distinguishable.
            cam_name = cam_info.get("customName") or cam_info.get("name", "")
            if cam_name:
                fixture_name = f"{base_name} — {cam_name}" if len(cameras) > 1 else cam_name
            else:
                fixture_name = base_name
            f = {
                "id": _nxt_fix, "name": fixture_name,
                "fixtureType": "camera", "type": "point",
                "childId": None, "childIds": [], "strings": [],
                "rotation": [0, 0, 0], "aoeRadius": 1000, "meshFile": None,
                "cameraIp": ip,
                "cameraIdx": cam_idx,
                "fovDeg": _camera_fov_from_info(info, cam_idx) or body.get("fovDeg") or 60,
                "cameraUrl": (info or {}).get("cameraUrl") or body.get("cameraUrl", ""),
                "resolutionW": cam_info.get("resW") or body.get("resolutionW") or 1920,
                "resolutionH": cam_info.get("resH") or body.get("resolutionH") or 1080,
            }
            _fixtures.append(f)
            created_ids.append(_nxt_fix)
            _nxt_fix += 1
        _save("fixtures", _fixtures)
    if not created_ids:
        return jsonify(err="Camera already registered at this IP"), 409
    return jsonify(ok=True, id=created_ids[0], ids=created_ids, count=len(created_ids)), 201

@app.delete("/api/cameras/<int:fid>")
def api_cameras_delete(fid):
    """Unregister a camera — removes the fixture."""
    global _fixtures
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    with _lock:
        _fixtures = [x for x in _fixtures if x["id"] != fid]
        _save("fixtures", _fixtures)
    return jsonify(ok=True), 200

@app.get("/api/cameras/<int:fid>/snapshot")
def api_camera_snapshot(fid):
    """Proxy a snapshot from a camera node.

    The sensor index defaults to the fixture's saved `cameraIdx` — e.g.
    fid=13 mapped to `cameraIdx=1` on a multi-sensor Orange Pi grabs
    /dev/video2, not /dev/video0. An explicit `?cam=N` query param still
    overrides (used by diagnostics that want to probe a specific index
    regardless of the fixture's saved mapping). Pre-fix this always sent
    `cam=0` regardless of fixture, so every multi-sensor node served
    cam-0's feed for every fixture and any SPA thumbnail / ArUco overlay
    painted detections onto the wrong frame.
    """
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = request.args.get("cam", f.get("cameraIdx", 0), type=int)
    try:
        import urllib.request as _ur
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        data = resp.read()
        from flask import Response
        return Response(data, mimetype="image/jpeg")
    except Exception as e:
        return jsonify(err=str(e)), 503

@app.get("/api/cameras/<int:fid>/status")
def api_camera_status(fid):
    """Fetch live status from a camera node."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    info = _probe_camera(ip, timeout=3)
    if not info:
        return jsonify(err="Camera offline"), 503
    return jsonify(info)

# ── Q12: FOV type whitelist + helper ──────────────────────────────────
# Cameras store their FOV as a single number (fovDeg) with a type flag
# (fovType) saying whether that number is horizontal, vertical, or
# diagonal. Manufacturers spec different axes; diagonal is the most
# commonly published for USB webcams so we default there. Every caller
# that needs a horizontal FOV for ray math should go through
# _camera_h_fov_rad() so the conversion stays consistent.
_FOV_TYPE_WHITELIST = ("horizontal", "vertical", "diagonal")
_FOV_TYPE_DEFAULT = "diagonal"


def _normalise_fov_type(value, *, default=_FOV_TYPE_DEFAULT):
    """Return a whitelist-validated fovType string. Unknown inputs map to
    the default so a malformed fixture record never crashes a ray calc."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FOV_TYPE_WHITELIST:
            return v
    return default


def _camera_h_fov_rad(cam_fixture, frame_w, frame_h):
    """Return the camera's **horizontal** FOV in radians, derived from the
    stored fovDeg + fovType. Falls back to a 60° horizontal FOV when the
    fixture is missing data."""
    fov_deg = cam_fixture.get("fovDeg", 60) or 60
    fov_type = _normalise_fov_type(cam_fixture.get("fovType"))
    fov_rad = math.radians(fov_deg)
    if fov_type == "horizontal":
        return fov_rad
    if not frame_w or not frame_h or frame_w <= 0 or frame_h <= 0:
        return fov_rad
    if fov_type == "diagonal":
        diag = math.sqrt(frame_w * frame_w + frame_h * frame_h)
        return 2.0 * math.atan(math.tan(fov_rad / 2.0) * (frame_w / diag))
    # vertical
    return 2.0 * math.atan(math.tan(fov_rad / 2.0) * (frame_w / frame_h))


def _pixel_point_to_stage_floor(cam_fixture, px, py, frame_w, frame_h):
    """Project a single pixel (px, py) onto the Z=0 stage-floor plane.

    Returns (stage_x_mm, stage_y_mm, tier) where tier is one of:
      - "homography"    — surveyed-marker cal matrix applied (best).
      - "fov-projection" — camera pose + FOV ray-plane intersect (ok).
      - "raw"           — camera position/FOV unavailable; uses stage-bounds
                          proportional fallback (same broken path as pre-fix
                          tracking; callers should treat as low-confidence).

    This is the Q1/Q5 replacement for the broken proportional ingest that
    used to live inline in api_objects_temporal_create. Callers pass the
    bbox bottom-center for feet, bbox center for center, etc.
    """
    # Tier 1: calibrated homography.
    cal = _calibrations.get(str(cam_fixture.get("id"))) if cam_fixture else None
    if cal and cal.get("matrix"):
        try:
            sx, sy = _apply_homography(cal["matrix"], px, py)
            return (float(sx), float(sy), "homography")
        except Exception:
            pass

    # Tier 2: FOV projection ray-plane intersect (needs camera pose).
    if cam_fixture and frame_w and frame_h:
        pos_map = {p["id"]: p for p in _layout.get("children", [])}
        cam_pos = pos_map.get(cam_fixture.get("id"), {})
        cx0 = float(cam_pos.get("x", 0) or 0)
        cy0 = float(cam_pos.get("y", 0) or 0)
        cz0 = float(cam_pos.get("z", 0) or 0)
        if cz0 > 1:  # camera must be off the floor for a ray-plane intersect
            aim = _rotation_to_aim(cam_fixture.get("rotation", [0, 0, 0]),
                                     [cx0, cy0, cz0])
            dx0 = aim[0] - cx0
            dy0 = aim[1] - cy0
            dz0 = aim[2] - cz0
            dist = math.sqrt(dx0 * dx0 + dy0 * dy0 + dz0 * dz0)
            if dist >= 1:
                dx0, dy0, dz0 = dx0 / dist, dy0 / dist, dz0 / dist
                rx = dy0
                ry = -dx0
                r_len = math.sqrt(rx * rx + ry * ry)
                if r_len >= 0.001:
                    rx, ry = rx / r_len, ry / r_len
                else:
                    rx, ry = 1.0, 0.0
                rz = 0.0
                ux = ry * dz0 - rz * dy0
                uy = rz * dx0 - rx * dz0
                uz = rx * dy0 - ry * dx0
                fov_rad = _camera_h_fov_rad(cam_fixture, frame_w, frame_h)
                half_fov = fov_rad / 2.0
                aspect = frame_w / frame_h if frame_h > 0 else 1.0
                ndc_x = (px / frame_w - 0.5) * 2.0
                ndc_y = -(py / frame_h - 0.5) * 2.0
                ray_x = dx0 + math.tan(half_fov) * (ndc_x * rx + ndc_y / aspect * ux)
                ray_y = dy0 + math.tan(half_fov) * (ndc_x * ry + ndc_y / aspect * uy)
                ray_z = dz0 + math.tan(half_fov) * (ndc_x * rz + ndc_y / aspect * uz)
                if abs(ray_z) > 1e-4:
                    t = -cz0 / ray_z
                    if t > 0:
                        sx = cx0 + t * ray_x
                        sy = cy0 + t * ray_y
                        return (float(sx), float(sy), "fov-projection")

    # Tier 3: raw proportional fallback. Signals the caller that this
    # placement isn't trustworthy; consumers (tracking, track-actions)
    # should prefer "hold last good" over acting on tier=raw data.
    if frame_w and frame_h:
        sw = _stage.get("w", 3.0) * 1000.0
        sd = _stage.get("d", 1.5) * 1000.0
        cx_f = (px / frame_w)
        cy_f = (py / frame_h)
        return (sw * (1.0 - cx_f), sd * (1.0 - cy_f), "raw")
    return (0.0, 0.0, "raw")


def _pixel_box_to_stage_anchors(cam_fixture, pixel_box, frame_size,
                                  default_height_mm=1700.0):
    """Project a pixel bbox to stage-space anchors {feet, center, head, method}.

    Used by the Q1 tracking ingest path to replace the broken proportional
    math. Q4's aimTarget enum reads from the returned anchors.

    * `feet`  = bbox bottom-center projected to Z=0 (where the person stands).
    * `head`  = feet + (0, 0, default_height_mm) (unless the bbox fills the
                entire frame height, in which case we trust YOLO's height and
                derive it via vertical-FOV + distance — not implemented here;
                fixed estimate is fine for #488's baseline).
    * `center` = midpoint between feet and head.
    * `method` = the tier stamp from _pixel_point_to_stage_floor (feeds the
                Q5 `_method` field on the temporal object record).
    """
    if not pixel_box or not frame_size:
        return None
    fw, fh = frame_size[0], frame_size[1]
    bx = float(pixel_box.get("x", 0))
    by = float(pixel_box.get("y", 0))
    bw = float(pixel_box.get("w", 0))
    bh = float(pixel_box.get("h", 0))
    feet_px_x = bx + bw / 2.0
    feet_px_y = by + bh  # bottom of bbox
    sx, sy, tier = _pixel_point_to_stage_floor(
        cam_fixture, feet_px_x, feet_px_y, fw, fh)
    feet = [sx, sy, 0.0]
    head = [sx, sy, float(default_height_mm)]
    center = [sx, sy, float(default_height_mm) / 2.0]
    return {"feet": feet, "center": center, "head": head,
            "method": tier, "heightMm": float(default_height_mm)}


def _pixel_to_stage_homography(detections, H_flat, frame_w, frame_h):
    """Transform detections using a calibrated homography matrix."""
    stage_w = _stage.get("w", 3.0) * 1000
    stage_d = _stage.get("d", 1.5) * 1000
    result = []
    for det in detections:
        # Bounding box center
        px = det["x"] + det["w"] / 2
        py = det["y"] + det["h"] / 2
        sx, sz = _apply_homography(H_flat, px, py)
        # Estimate size using corner-to-corner transform
        px1, py1 = det["x"], det["y"]
        px2, py2 = det["x"] + det["w"], det["y"] + det["h"]
        sx1, sz1 = _apply_homography(H_flat, px1, py1)
        sx2, sz2 = _apply_homography(H_flat, px2, py2)
        obj_w = abs(sx2 - sx1)
        obj_h = abs(sz2 - sz1)
        # Clamp to stage
        sx = max(0, min(sx, stage_w))
        sz = max(0, min(sz, stage_d))
        result.append({
            "label": det["label"],
            "confidence": det["confidence"],
            "x": round(sx), "y": 0, "z": round(sz),
            "w": round(max(obj_w, 100)), "h": round(max(obj_h, 100)),
            "pixelBox": {"x": det["x"], "y": det["y"], "w": det["w"], "h": det["h"]},
        })
    return result

def _pixel_to_stage(detections, cam_fixture, frame_w, frame_h):
    """Transform pixel-space detections to stage-space (mm).

    Uses calibrated homography if available, otherwise falls back to
    ground-plane projection using camera position, rotation, and FOV.
    """
    # Try calibrated homography first
    cal = _calibrations.get(str(cam_fixture.get("id")))
    if cal and cal.get("matrix"):
        return _pixel_to_stage_homography(detections, cal["matrix"], frame_w, frame_h)

    # Camera position from layout
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    cam_pos = pos_map.get(cam_fixture["id"], {})
    cx = cam_pos.get("x", 0)  # mm (width)
    cy = cam_pos.get("y", 0)  # mm (depth)
    cz = cam_pos.get("z", 0)  # mm (height)

    # Compute aim from rotation
    aim = _rotation_to_aim(cam_fixture.get("rotation", [0, 0, 0]), [cx, cy, cz])
    ax, ay, az = aim[0], aim[1], aim[2]

    # #Q12 — honour fovType so the ray math matches the manufacturer spec.
    # Without this, a diagonal-spec 90° webcam was treated as horizontal-90°
    # and every pixel projected ~20% too far off-axis.
    fov_rad = _camera_h_fov_rad(cam_fixture, frame_w, frame_h)

    # Camera look direction (normalized)
    dx, dy, dz = ax - cx, ay - cy, az - cz
    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1:
        return detections  # Camera not positioned, return raw

    dx, dy, dz = dx/dist, dy/dist, dz/dist

    # Camera right vector (cross of look × world_up)
    # World up = (0, 0, 1) — Z is height
    # cross(look, up) = (dy*1 - dz*0, dz*0 - dx*1, dx*0 - dy*0) = (dy, -dx, 0)
    rx = dy
    ry = -dx
    rz = 0
    r_len = math.sqrt(rx*rx + ry*ry)
    if r_len < 0.001:
        rx, ry, rz = 1, 0, 0  # Looking straight up/down, pick arbitrary right
    else:
        rx, ry = rx/r_len, ry/r_len

    # Camera up vector (cross of right × look)
    ux = ry*dz - rz*dy
    uy = rz*dx - rx*dz
    uz = rx*dy - ry*dx

    # Half-FOV determines the image plane extent
    half_fov = fov_rad / 2
    aspect = frame_w / frame_h if frame_h > 0 else 1.0

    stage_w = _stage.get("w", 3.0) * 1000  # mm
    stage_h = _stage.get("h", 2.0) * 1000
    stage_d = _stage.get("d", 1.5) * 1000

    result = []
    for det in detections:
        # Bounding box center in pixel coords
        px = det["x"] + det["w"] / 2
        py = det["y"] + det["h"] / 2

        # Normalize pixel coords to [-1, 1] (NDC)
        ndc_x = (px / frame_w - 0.5) * 2   # -1 (left) to 1 (right)
        ndc_y = -(py / frame_h - 0.5) * 2  # -1 (bottom) to 1 (top), flip Y

        # Ray direction through pixel on image plane
        ray_x = dx + math.tan(half_fov) * (ndc_x * rx + ndc_y / aspect * ux)
        ray_y = dy + math.tan(half_fov) * (ndc_x * ry + ndc_y / aspect * uy)
        ray_z = dz + math.tan(half_fov) * (ndc_x * rz + ndc_y / aspect * uz)

        # Intersect ray with ground plane (z=0)
        # Point = camera_pos + t * ray, solve for z=0: cz + t * ray_z = 0
        if abs(ray_z) < 0.0001:
            # Ray parallel to ground — place at aim point distance
            t = dist
        else:
            t = -cz / ray_z
            if t < 0:
                t = dist  # Ray points away from ground, use aim distance

        # Stage intersection point
        sx = cx + t * ray_x
        sy = cy + t * ray_y

        # Estimate object size on ground plane from bounding box
        # Use proportion of FOV covered by the box
        ground_span = 2 * t * math.tan(half_fov)  # total width visible at distance t
        obj_w = (det["w"] / frame_w) * ground_span
        obj_h = (det["h"] / frame_h) * ground_span / aspect

        # Clamp to stage bounds
        sx = max(0, min(sx, stage_w))
        sy = max(0, min(sy, stage_d))

        result.append({
            "label": det["label"],
            "confidence": det["confidence"],
            "x": round(sx),
            "y": round(sy),
            "z": 0,
            "w": round(max(obj_w, 100)),   # minimum 100mm
            "h": round(max(obj_h, 100)),
            "pixelBox": {"x": det["x"], "y": det["y"], "w": det["w"], "h": det["h"]},
        })
    return result

@app.post("/api/cameras/<int:fid>/scan")
def api_camera_scan(fid):
    """Proxy scan request to camera node. Returns detections with stage coordinates."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    body = request.get_json(silent=True) or {}
    threshold = body.get("threshold", 0.5)
    cam_idx = body.get("cam", 0)
    resolution = body.get("resolution", 320)
    # Forward to camera node /scan endpoint
    try:
        import urllib.request as _ur
        req_data = json.dumps({"threshold": threshold, "cam": cam_idx,
                                "resolution": resolution}).encode()
        req = _ur.Request(f"http://{ip}:5000/scan",
                          data=req_data,
                          headers={"Content-Type": "application/json"})
        resp = _ur.urlopen(req, timeout=30)
        raw = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify(err=f"Camera scan failed: {e}"), 503
    if not raw.get("ok"):
        return jsonify(err=raw.get("err", "Scan failed")), 503
    raw_dets = raw.get("detections", [])
    frame_size = raw.get("frameSize", [640, 480])
    # Transform pixel coords to stage coords
    stage_dets = _pixel_to_stage(raw_dets, f, frame_size[0], frame_size[1])
    return jsonify(
        ok=True,
        detections=stage_dets,
        cameraId=fid,
        captureMs=raw.get("captureMs"),
        inferenceMs=raw.get("inferenceMs"),
    )

# ── Camera calibration — homography math ──────────────────────────────

def _compute_homography(stage_pts, pixel_pts):
    """Compute 3×3 homography mapping pixel coords → stage coords (mm) using DLT.

    Args:
        stage_pts: list of [x, z] in stage mm (ground plane, y=0)
        pixel_pts: list of [px, py] in camera pixels

    Returns:
        (matrix_3x3_flat, avg_reproj_error_px) or raises ValueError
    """
    n = len(stage_pts)
    if n < 2:
        raise ValueError(f"Need at least 2 reference points, got {n}")
    if n != len(pixel_pts):
        raise ValueError("stage_pts and pixel_pts must have same length")

    # Check for collinearity (all points on a line) — only relevant for 3+ points
    if n >= 3:
        pts = np.array(pixel_pts, dtype=float)
        v1 = pts[1] - pts[0]
        v2 = pts[2] - pts[0]
        cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
        if cross < 1.0:
            raise ValueError("Reference points are collinear — need non-collinear points")

    sp = np.array(stage_pts, dtype=float)
    pp = np.array(pixel_pts, dtype=float)

    # 2-point case: compute similarity transform (scale + translate)
    if n == 2:
        # Simple affine: stage = scale * pixel + offset
        dp = pp[1] - pp[0]
        ds = sp[1] - sp[0]
        px_dist = np.linalg.norm(dp)
        if px_dist < 0.001:
            raise ValueError("Reference pixel points are identical")
        st_dist = np.linalg.norm(ds)
        scale = st_dist / px_dist
        # Rotation angle
        angle_p = np.arctan2(dp[1], dp[0])
        angle_s = np.arctan2(ds[1], ds[0])
        theta = angle_s - angle_p
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        # Build 3x3 matrix: rotate + scale + translate
        R = scale * np.array([[cos_t, -sin_t], [sin_t, cos_t]])
        t = sp[0] - R @ pp[0]
        H = np.array([
            [R[0, 0], R[0, 1], t[0]],
            [R[1, 0], R[1, 1], t[1]],
            [0, 0, 1],
        ])
        # Compute error
        errors = []
        for i in range(n):
            v = H @ np.array([pp[i][0], pp[i][1], 1.0])
            errors.append(np.sqrt((v[0] - sp[i][0])**2 + (v[1] - sp[i][1])**2))
        return H.flatten().tolist(), float(np.mean(errors))

    # Build DLT matrix A (2n × 9) for 3+ points
    A = []
    for i in range(n):
        px, py = pp[i]
        sx, sz = sp[i]
        A.append([-px, -py, -1, 0, 0, 0, sx*px, sx*py, sx])
        A.append([0, 0, 0, -px, -py, -1, sz*px, sz*py, sz])
    A = np.array(A)

    # SVD solve for h (last column of V)
    _, _, Vt = np.linalg.svd(A)
    h = Vt[-1]
    H = h.reshape(3, 3)

    # Normalize so H[2,2] = 1
    if abs(H[2, 2]) > 1e-10:
        H = H / H[2, 2]

    # Compute reprojection error
    errors = []
    for i in range(n):
        px, py = pp[i]
        v = H @ np.array([px, py, 1.0])
        if abs(v[2]) > 1e-10:
            proj_sx, proj_sz = v[0]/v[2], v[1]/v[2]
        else:
            proj_sx, proj_sz = v[0], v[1]
        err = np.sqrt((proj_sx - sp[i][0])**2 + (proj_sz - sp[i][1])**2)
        errors.append(err)
    avg_error = float(np.mean(errors))

    return H.flatten().tolist(), avg_error

def _apply_homography(H_in, px, py):
    """Apply 3×3 homography to a pixel point → stage coords [x, z] in mm.

    Accepts either a flat 9-element list or a nested 3×3 list. Stage-map
    persists nested (H_floor.tolist()) while older ArUco flows produced
    flat; the helper now tolerates both so downstream consumers (#Q7
    single-source homography) don't have to care which format landed."""
    if (len(H_in) == 3 and isinstance(H_in[0], (list, tuple))
            and len(H_in[0]) == 3):
        H = H_in
    else:
        H = [H_in[0:3], H_in[3:6], H_in[6:9]]
    w = H[2][0]*px + H[2][1]*py + H[2][2]
    if abs(w) < 1e-10:
        w = 1e-10
    sx = (H[0][0]*px + H[0][1]*py + H[0][2]) / w
    sz = (H[1][0]*px + H[1][1]*py + H[1][2]) / w
    return sx, sz


_calib_state = {}  # {cam_fid: {step, fixtures, flashing, detected}}

@app.post("/api/cameras/<int:fid>/calibrate/start")
def api_camera_calibrate_start(fid):
    """Start calibration sequence — identifies reference fixtures to flash."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404

    # Find positioned LED/DMX fixtures as reference points
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    refs = []
    for fx in _fixtures:
        if fx["id"] == fid:
            continue
        if fx["id"] not in pos_map:
            continue
        if fx.get("fixtureType") not in ("led", "dmx"):
            continue
        p = pos_map[fx["id"]]
        refs.append({"id": fx["id"], "name": fx.get("name", ""),
                      "x": p.get("x", 0), "z": p.get("z", 0),
                      "fixtureType": fx.get("fixtureType")})

    if len(refs) < 2:
        return jsonify(err=f"Need at least 2 positioned fixtures as reference points, found {len(refs)}"), 400

    _calib_state[fid] = {"step": 0, "fixtures": refs, "detected": []}
    return jsonify(ok=True, steps=len(refs), fixtures=refs)


@app.post("/api/cameras/<int:fid>/calibrate/detect")
def api_camera_calibrate_detect(fid):
    """Capture a detection for a specific reference fixture during calibration.
    Body: {fixtureId, pixelX, pixelY} — the pixel position where the fixture was detected."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    state = _calib_state.get(fid)
    if not state:
        return jsonify(err="No calibration in progress — call /calibrate/start first"), 400
    body = request.get_json(silent=True) or {}
    fix_id = body.get("fixtureId")
    px = body.get("pixelX")
    py = body.get("pixelY")
    if fix_id is None or px is None or py is None:
        return jsonify(err="fixtureId, pixelX, pixelY required"), 400
    # Verify fixture is in the reference list
    ref = next((r for r in state["fixtures"] if r["id"] == fix_id), None)
    if not ref:
        return jsonify(err=f"Fixture {fix_id} is not a calibration reference"), 400
    state["detected"].append({
        "fixtureId": fix_id, "stageX": ref["x"], "stageZ": ref["z"],
        "pixelX": float(px), "pixelY": float(py),
    })
    state["step"] = len(state["detected"])
    return jsonify(ok=True, step=state["step"], total=len(state["fixtures"]))


@app.post("/api/cameras/<int:fid>/calibrate/compute")
def api_camera_calibrate_compute(fid):
    """Compute homography from collected reference points."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    state = _calib_state.get(fid)
    if not state or len(state.get("detected", [])) < 2:
        return jsonify(err="Need at least 2 detected reference points"), 400
    detected = state["detected"]
    stage_pts = [[d["stageX"], d["stageZ"]] for d in detected]
    pixel_pts = [[d["pixelX"], d["pixelY"]] for d in detected]
    try:
        matrix, error = _compute_homography(stage_pts, pixel_pts)
    except ValueError as e:
        return jsonify(err=str(e)), 400
    # Store calibration
    cal = {
        "matrix": matrix,
        "error": round(error, 2),
        "points": detected,
        "timestamp": time.time(),
    }
    _calibrations[str(fid)] = cal
    _save("calibrations", _calibrations)
    f["calibrated"] = True
    _save("fixtures", _fixtures)
    # Clean up state
    _calib_state.pop(fid, None)
    return jsonify(ok=True, error=round(error, 2), calibrated=True)


@app.get("/api/cameras/<int:fid>/intrinsic")
def api_camera_intrinsic_get(fid):
    """Proxy intrinsic calibration data from a camera node."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/calibrate/intrinsic?cam={cam_idx}", timeout=10)
        return jsonify(json.loads(resp.read().decode("utf-8")))
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


# -- ArUco calibration — detection runs on orchestrator, cameras only provide snapshots (#329)

_aruco_frames = {}  # {fid: [(corners, ids, frame_size), ...]}

def _aruco_detect(frame):
    """Run ArUco detection on a frame. Returns (corners, ids, rejected, frame_size).
    Tries default params first (fast), falls back to relaxed for high-res.
    Compatible with OpenCV 4.7 (detectMarkers) and 4.8+ (ArucoDetector)."""
    import cv2
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    params = cv2.aruco.DetectorParameters()
    def _detect(g, d, p):
        if hasattr(cv2.aruco, 'ArucoDetector'):
            return cv2.aruco.ArucoDetector(d, p).detectMarkers(g)
        return cv2.aruco.detectMarkers(g, d, parameters=p)
    corners, ids, rejected = _detect(gray, aruco_dict, params)
    if (ids is None or len(ids) == 0) and frame.shape[1] >= 1920:
        params.adaptiveThreshWinSizeMin = 3
        params.adaptiveThreshWinSizeMax = 53
        params.adaptiveThreshWinSizeStep = 4
        params.minMarkerPerimeterRate = 0.01
        params.maxMarkerPerimeterRate = 4.0
        params.polygonalApproxAccuracyRate = 0.05
        params.minCornerDistanceRate = 0.01
        params.minDistanceToBorder = 1
        params.errorCorrectionRate = 0.8
        corners, ids, rejected = _detect(gray, aruco_dict, params)
    return corners, ids, rejected, gray.shape[::-1]


@app.post("/api/cameras/<int:fid>/aruco/capture")
def api_camera_aruco_capture(fid):
    """Fetch snapshot from camera, run ArUco detection on orchestrator."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed on orchestrator"), 500
    # Fetch JPEG snapshot from camera
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        jpeg_data = resp.read()
    except Exception as e:
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                       "err": f"Snapshot failed: {e}",
                       "frameCount": len(_aruco_frames.get(fid, []))}])
    # Decode and detect
    frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                       "err": "Decode failed",
                       "frameCount": len(_aruco_frames.get(fid, []))}])
    corners, ids, rejected, frame_size = _aruco_detect(frame)
    if ids is not None and len(ids) > 0:
        if fid not in _aruco_frames:
            _aruco_frames[fid] = []
        _aruco_frames[fid].append((corners, ids, frame_size))
        found_ids = ids.flatten().tolist()
        log.info("ArUco capture fid=%d: %d markers (ids=%s), total=%d frames",
                 fid, len(ids), found_ids, len(_aruco_frames[fid]))
        return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": len(ids),
                       "ids": found_ids, "frameCount": len(_aruco_frames[fid])}])
    return jsonify(ok=True, cameras=[{"cam": cam_idx, "markersFound": 0,
                   "frameCount": len(_aruco_frames.get(fid, []))}])


@app.post("/api/cameras/<int:fid>/aruco/compute")
def api_camera_aruco_compute(fid):
    """Compute intrinsic calibration from accumulated frames — all on orchestrator."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    frames = _aruco_frames.get(fid, [])
    if len(frames) < 3:
        return jsonify(ok=False, err=f"Need at least 3 frames, have {len(frames)}")
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed"), 500
    body = request.get_json(silent=True) or {}
    marker_size = body.get("markerSize", 150)
    half = marker_size / 2.0
    # Build calibration arrays: each marker = 4 object + 4 image points
    obj_points = []
    img_points = []
    frame_size = frames[0][2]
    for corners, ids, sz in frames:
        for i in range(len(ids)):
            obj = np.array([[-half, half, 0], [half, half, 0],
                            [half, -half, 0], [-half, -half, 0]], dtype=np.float32)
            obj_points.append(obj)
            img_points.append(corners[i].reshape(4, 2).astype(np.float32))
    try:
        ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
            obj_points, img_points, frame_size, None, None)
    except Exception as e:
        return jsonify(ok=False, err=f"calibrateCamera failed: {e}")
    if not ret or K is None:
        return jsonify(ok=False, err="Calibration failed — try more frames")
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    rms = float(ret)
    # Save to camera node if possible
    ip = f.get("cameraIp")
    cam_idx = f.get("cameraIdx", 0)
    if ip:
        cal_data = {"cam": cam_idx, "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                    "distCoeffs": dist.flatten().tolist() if dist is not None else [],
                    "rmsError": rms, "frameCount": len(frames)}
        try:
            import urllib.request as _ur
            req = _ur.Request(f"http://{ip}:5000/calibrate/intrinsic/save",
                              data=json.dumps(cal_data).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            _ur.urlopen(req, timeout=5)
        except Exception:
            pass  # Save failed — calibration still valid locally
    return jsonify(ok=True, frameCount=len(frames), rmsError=round(rms, 4),
                   fx=round(fx, 1), fy=round(fy, 1), cx=round(cx, 1), cy=round(cy, 1),
                   distCoeffs=dist.flatten().tolist() if dist is not None else [])


@app.post("/api/cameras/<int:fid>/aruco/reset")
def api_camera_aruco_reset(fid):
    """Reset accumulated ArUco frames for a camera."""
    _aruco_frames.pop(fid, None)
    return jsonify(ok=True, frameCount=0)


# ── ArUco marker registry (#596) ──────────────────────────────────────
# CRUD for surveyed markers (id → stage-space pose + physical size).
# Consumed by the Setup tab editor and the Advanced Scan card panel; used
# as ground-truth correspondences by stereo scans once #592 lands.

_ARUCO_DICT_ID = 50  # DICT_4X4_50 — matches _aruco_detect above

def _aruco_marker_normalise(rec):
    """Coerce / clamp a marker record to the canonical schema. Raises
    ValueError on invalid input."""
    if rec is None or "id" not in rec:
        raise ValueError("marker record must include 'id'")
    mid = int(rec["id"])
    if mid < 0 or mid >= _ARUCO_DICT_ID:
        raise ValueError(f"marker id {mid} is outside dictionary range 0..{_ARUCO_DICT_ID - 1}")
    def _f(key, default=0.0):
        v = rec.get(key, default)
        try:
            return float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            return float(default)
    out = {
        "id": mid,
        "size": max(1.0, _f("size", 100.0)),
        "x": _f("x"), "y": _f("y"), "z": _f("z"),
        "rx": _f("rx"), "ry": _f("ry"), "rz": _f("rz"),
    }
    label = rec.get("label")
    if isinstance(label, str) and label.strip():
        out["label"] = label.strip()[:60]
    return out


@app.get("/api/aruco/markers")
def api_aruco_markers_list():
    """Return the marker registry (all surveyed ArUco tags)."""
    return jsonify(ok=True,
                   dictId=_ARUCO_DICT_ID,
                   markers=list(_aruco_markers))


@app.get("/api/aruco/markers/coverage")
def api_aruco_markers_coverage():
    """Q11/#612 — pre-cal marker coverage summary.

    Returns per-camera visibility (which markers each camera detects right
    now), the marker hull's stage XY bounds, the count of markers visible
    to ≥2 cameras (fusion-ready), and a recommendation pin for where an
    additional marker would help most. Drives the SPA marker-coverage
    overlay so the operator can see "Cam 16 only sees 2 markers — drop one
    near (3000, 2500) to get coverage for stage-right" without dispatching
    a stage-map run.
    """
    cam_fixtures = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    cams = []
    for f in cam_fixtures:
        if not f.get("cameraIp"):
            continue
        try:
            r = _aruco_snapshot_detect(f)
        except Exception as e:
            cams.append({"id": f["id"], "name": f.get("name"),
                         "err": str(e), "seenIds": [], "frameSize": None})
            continue
        seen = sorted({int(m.get("id")) for m in r.get("markers", [])
                        if m.get("id") is not None})
        cams.append({"id": f["id"], "name": f.get("name"),
                      "seenIds": seen,
                      "frameSize": r.get("frameSize"),
                      "err": r.get("err")})
    # Coverage stats
    counts = {}
    for c in cams:
        for mid in c.get("seenIds") or []:
            counts[mid] = counts.get(mid, 0) + 1
    shared_ids = sorted(mid for mid, n in counts.items() if n >= 2)
    registered_ids = sorted(int(m.get("id")) for m in _aruco_markers
                              if m.get("id") is not None)
    visible_ids = sorted(counts.keys())
    # Hull stats over registered markers (XY bounds, simple).
    if _aruco_markers:
        xs = [float(m.get("x", 0)) for m in _aruco_markers]
        ys = [float(m.get("y", 0)) for m in _aruco_markers]
        hull = {
            "xMin": min(xs), "xMax": max(xs),
            "yMin": min(ys), "yMax": max(ys),
            "centerXy": [(min(xs) + max(xs)) / 2.0,
                          (min(ys) + max(ys)) / 2.0],
            "spanX": max(xs) - min(xs),
            "spanY": max(ys) - min(ys),
        }
    else:
        hull = None
    # Recommendation pin — where would an additional marker most help?
    # Heuristic: pick the camera with the fewest visible-but-registered
    # markers and recommend a position roughly at the centre of its
    # un-covered FOV (approximated by the stage centre offset away from
    # whichever markers it already sees).
    recommendation = None
    if cams and registered_ids:
        worst = min(cams, key=lambda c: len(c.get("seenIds") or []))
        worst_seen = set(worst.get("seenIds") or [])
        worst_unseen = [m for m in _aruco_markers
                          if int(m.get("id")) in (set(registered_ids) - worst_seen)]
        if worst_unseen and hull:
            # Average position of markers worst camera doesn't see — that
            # area is where coverage is most likely missing.
            ax = sum(float(m.get("x", 0)) for m in worst_unseen) / len(worst_unseen)
            ay = sum(float(m.get("y", 0)) for m in worst_unseen) / len(worst_unseen)
            # Pull recommendation slightly inside hull to keep it placeable.
            recommendation = {
                "cameraId": worst["id"],
                "cameraName": worst.get("name"),
                "missingCount": len(worst_unseen),
                "suggestedPlacement": {
                    "x": round(min(max(ax, hull["xMin"]), hull["xMax"]), 1),
                    "y": round(min(max(ay, hull["yMin"]), hull["yMax"]), 1),
                    "z": 0.0,
                },
                "rationale": (f"Cam '{worst.get('name')}' currently sees "
                                f"{len(worst_seen)}/{len(registered_ids)} surveyed "
                                f"markers. Adding one near the indicated XY would "
                                f"give it a third anchor for stable findHomography."),
            }
    return jsonify(ok=True,
                   cameras=cams,
                   registeredCount=len(registered_ids),
                   visibleCount=len(visible_ids),
                   sharedCount=len(shared_ids),
                   sharedIds=shared_ids,
                   hull=hull,
                   recommendation=recommendation)


@app.post("/api/aruco/markers")
def api_aruco_markers_upsert():
    """Create or update a marker by id. Body = single record, or list of
    records. Replaces by id (no dup ids). Returns the full normalized
    registry so the caller can refresh without a second GET."""
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(err="JSON body required"), 400
    records = body if isinstance(body, list) else [body]
    try:
        updates = [_aruco_marker_normalise(r) for r in records]
    except ValueError as e:
        return jsonify(err=str(e)), 400
    # Replace-by-id
    by_id = {m["id"]: m for m in _aruco_markers}
    for u in updates:
        by_id[u["id"]] = u
    _aruco_markers.clear()
    _aruco_markers.extend(sorted(by_id.values(), key=lambda m: m["id"]))
    _save("aruco_markers", _aruco_markers)
    _apply_auto_stage_bounds()  # #628
    return jsonify(ok=True, markers=list(_aruco_markers),
                   updated=[u["id"] for u in updates])


@app.delete("/api/aruco/markers/<int:mid>")
def api_aruco_markers_delete(mid):
    """Remove a marker by id. Returns {removed: bool}."""
    before = len(_aruco_markers)
    _aruco_markers[:] = [m for m in _aruco_markers if m.get("id") != mid]
    removed = len(_aruco_markers) < before
    if removed:
        _save("aruco_markers", _aruco_markers)
        _apply_auto_stage_bounds()  # #628
    return jsonify(ok=True, removed=removed,
                   markers=list(_aruco_markers))


@app.post("/api/cameras/<int:fid>/stage-map")
def api_camera_stage_map(fid):
    """Compute stage-map calibration on orchestrator using solvePnP (#330).

    Fetches a snapshot from the camera, runs ArUco detection locally,
    matches detected markers against provided marker positions, and
    computes camera pose via cv2.solvePnP.
    """
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    try:
        import cv2
    except ImportError:
        return jsonify(ok=False, err="OpenCV not installed on orchestrator"), 500
    if np is None:
        return jsonify(ok=False, err="NumPy not installed on orchestrator"), 500
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", f.get("cameraIdx", 0))
    markers = body.get("markers", [])
    if not markers or len(markers) < 3:
        return jsonify(ok=False, err="Need at least 3 marker positions"), 400
    marker_size = body.get("markerSize", 150)  # mm
    half = marker_size / 2.0
    # Build lookup: marker_id → {x, y, z}
    marker_map = {}
    for m in markers:
        mid = m.get("id")
        if mid is not None:
            marker_map[int(mid)] = m
    # Multi-snapshot aggregation (#stage-map-flaky). ArUco detection is
    # frame-to-frame noisy; on the basement rig each camera reliably
    # misses one of the three surveyed markers per frame, but across
    # ~5 snapshots every marker gets seen at least once. Accumulate
    # by marker-id, keeping the single cleanest detection per id
    # (largest-perimeter = closest-to-camera = best sub-pixel corners).
    # When the operator registers N surveyed markers, `max_snapshots`
    # is bounded so we don't spin forever if one marker is physically
    # out of every camera's FOV.
    import urllib.request as _ur
    max_snapshots = int(body.get("maxSnapshots", 6))
    best_per_id = {}  # mid → (perimeter, corners, frame_size)
    detected_count = 0
    frame_size = None
    for attempt in range(max_snapshots):
        try:
            resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}",
                               timeout=15)
            jpeg_data = resp.read()
        except Exception as e:
            if attempt == 0:
                return jsonify(ok=False, err=f"Snapshot failed: {e}"), 503
            continue
        frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8),
                              cv2.IMREAD_COLOR)
        if frame is None:
            continue
        corners_snap, ids_snap, _rej, fsz = _aruco_detect(frame)
        if fsz is not None:
            frame_size = fsz
        if ids_snap is None or len(ids_snap) == 0:
            continue
        detected_count += len(ids_snap)
        for i, mid in enumerate(ids_snap.flatten()):
            mid_int = int(mid)
            if mid_int not in marker_map:
                continue
            pts = corners_snap[i].reshape(4, 2)
            # Perimeter as a quality proxy — bigger = better sub-pixel.
            perim = float(sum(
                np.linalg.norm(pts[(j + 1) % 4] - pts[j]) for j in range(4)))
            prior = best_per_id.get(mid_int)
            if prior is None or perim > prior[0]:
                best_per_id[mid_int] = (perim, pts.astype(np.float64))
        if len(best_per_id) >= len(marker_map):
            break  # all surveyed markers seen — no need to keep snapping

    if not best_per_id:
        return jsonify(ok=True, markersDetected=0, markersMatched=0,
                       err="No ArUco markers detected across "
                           f"{max_snapshots} snapshots")
    if frame_size is None:
        return jsonify(ok=False, err="could not determine frame size"), 500
    # Build the correspondence arrays in deterministic id order.
    obj_points = []
    img_points = []
    matched_ids = []
    for mid_int, (_, pts) in sorted(best_per_id.items()):
        m = marker_map[mid_int]
        mx = float(m.get("x", 0))
        my = float(m.get("y", 0))
        mz = float(m.get("z", 0))
        # 3D corners: spread in X and Y, constant Z (floor-plane).
        obj_pts = np.array([
            [mx - half, my + half, mz],   # top-left
            [mx + half, my + half, mz],   # top-right
            [mx + half, my - half, mz],   # bottom-right
            [mx - half, my - half, mz],   # bottom-left
        ], dtype=np.float64)
        obj_points.append(obj_pts)
        img_points.append(pts)
        matched_ids.append(mid_int)
    w, h = int(frame_size[0]), int(frame_size[1])
    # solvePnP needs ≥4 coplanar points or ≥3 non-coplanar. With floor
    # markers we always have coplanar (all at z=0), so 2 × 4 = 8 corner
    # points is sufficient provided the two marker centres aren't
    # colinear (trivially true for any realistic stage layout). On a rig
    # where no single camera FOV covers 3+ surveyed markers (cam 12 sees
    # AR1+AR2, cam 13 sees AR0+AR2 — no camera sees all 3), the 2-marker
    # path is the only one that works without a multi-frame aggregation
    # pass. Error below 5 px is still routine with 8 corners.
    if len(matched_ids) < 2:
        return jsonify(ok=True, markersDetected=detected_count,
                       markersMatched=len(matched_ids),
                       err=f"Only {len(matched_ids)} marker matched (need 2+)")
    # Stack all points
    obj_all = np.vstack(obj_points)  # (N*4, 3)
    img_all = np.vstack(img_points)  # (N*4, 2)
    # Prefer calibrated intrinsics from the camera node (saved by
    # /api/cameras/<fid>/aruco/compute) over an FOV-derived estimate —
    # the FOV value is nameplate-accurate at best and drives solvePnP
    # towards implausible Z values when the fixture's real lens deviates
    # (#331).
    intrinsic_source = "fov-estimate"
    dist_coeffs = np.zeros(4, dtype=np.float64)
    K = None
    try:
        import urllib.request as _ur_calib
        _resp = _ur_calib.urlopen(
            f"http://{ip}:5000/calibrate/intrinsic?cam={cam_idx}", timeout=3)
        _cal = json.loads(_resp.read().decode("utf-8"))
        if _cal.get("calibrated") and all(k in _cal for k in ("fx","fy","cx","cy")):
            K = np.array([
                [float(_cal["fx"]), 0, float(_cal["cx"])],
                [0, float(_cal["fy"]), float(_cal["cy"])],
                [0, 0, 1],
            ], dtype=np.float64)
            dc = _cal.get("distCoeffs") or []
            if dc:
                dist_coeffs = np.array(dc, dtype=np.float64).flatten()
            intrinsic_source = "calibrated"
    except Exception:
        pass
    if K is None:
        fov_deg = f.get("fovDeg", 60)
        fov_rad = math.radians(fov_deg)
        fx_est = (w / 2.0) / math.tan(fov_rad / 2.0)
        fy_est = fx_est  # square pixels
        cx_est = w / 2.0
        cy_est = h / 2.0
        K = np.array([
            [fx_est, 0,      cx_est],
            [0,      fy_est, cy_est],
            [0,      0,      1     ],
        ], dtype=np.float64)
    # solvePnP strategy:
    # - Floor markers (all z=0) are coplanar, which creates a pose
    #   ambiguity — SQPNP and ITERATIVE can both converge to a mirror
    #   solution with the camera under the floor. On the basement rig
    #   this produced cam z=-58mm from a camera layout-recorded at
    #   z=1920mm. The ITERATIVE solver with a good initial guess avoids
    #   the mirror branch.
    # - The layout already has the camera's rough stage-frame position
    #   (fid in `_layout.children`) plus its rotation (from `fixture.
    #   rotation` = [tilt, pan, roll]). Use `camera_math.build_camera_
    #   to_stage` + the layout position to seed (rvec, tvec) so
    #   ITERATIVE refines around the physically plausible pose rather
    #   than jumping branches.
    # - If no layout pose is available, fall back to SQPNP → ITERATIVE
    #   without a guess (the legacy path).
    success = False
    rvec_out = tvec_out = None
    pos_map_ = {p["id"]: p for p in _layout.get("children", [])}
    lp = pos_map_.get(fid)
    fixture_rot = f.get("rotation") or [0, 0, 0]
    rvec_init = tvec_init = None
    if lp and any(lp.get(k) is not None for k in ("x", "y", "z")):
        try:
            from camera_math import build_camera_to_stage, rotation_from_layout
            tilt, pan, roll = rotation_from_layout(fixture_rot)
            R_cam_to_stage = np.asarray(
                build_camera_to_stage(tilt, pan, roll), dtype=np.float64)
            # build_camera_to_stage returns cam-local → stage. solvePnP
            # wants stage → cam (world → cam). Invert by transposing.
            R_stage_to_cam = R_cam_to_stage.T
            cam_pos = np.array([float(lp.get("x", 0)),
                                 float(lp.get("y", 0)),
                                 float(lp.get("z", 0))], dtype=np.float64)
            t_init = (-R_stage_to_cam @ cam_pos).reshape(3, 1)
            rvec_init, _ = cv2.Rodrigues(R_stage_to_cam)
            tvec_init = t_init
        except Exception as e:
            log.debug("stage-map: initial pose derivation failed: %s", e)
            rvec_init = tvec_init = None

    if rvec_init is not None:
        try:
            success, rvec_out, tvec_out = cv2.solvePnP(
                obj_all, img_all, K, dist_coeffs,
                rvec=rvec_init.copy(), tvec=tvec_init.copy(),
                useExtrinsicGuess=True,
                flags=cv2.SOLVEPNP_ITERATIVE)
        except Exception:
            success = False
    if not success or rvec_out is None:
        try:
            success, rvec_out, tvec_out = cv2.solvePnP(
                obj_all, img_all, K, dist_coeffs,
                flags=getattr(cv2, "SOLVEPNP_SQPNP", cv2.SOLVEPNP_ITERATIVE))
        except Exception:
            success = False
    if not success or rvec_out is None:
        try:
            success, rvec_out, tvec_out = cv2.solvePnP(
                obj_all, img_all, K, dist_coeffs,
                flags=cv2.SOLVEPNP_ITERATIVE)
        except Exception as e:
            return jsonify(ok=False, markersDetected=detected_count,
                           markersMatched=len(matched_ids),
                           err=f"solvePnP raised: {e}")
    if not success:
        return jsonify(ok=False, markersDetected=detected_count,
                       markersMatched=len(matched_ids),
                       err="solvePnP failed")
    rvec, tvec = rvec_out, tvec_out
    # Compute camera position in stage coords: cam_pos = -R^T @ tvec
    R, _ = cv2.Rodrigues(rvec)
    cam_pos = (-R.T @ tvec).flatten()
    # Compute reprojection error (RMS)
    proj, _ = cv2.projectPoints(obj_all, rvec, tvec, K, dist_coeffs)
    proj = proj.reshape(-1, 2)
    err = np.sqrt(np.mean(np.sum((img_all - proj) ** 2, axis=1)))
    # Build floor-plane homography. Two paths:
    # 1. Derive from solvePnP pose (R + t + K) — requires non-coplanar
    #    correspondences OR an unambiguous pose. Fails on 2 coplanar
    #    floor markers (solvePnP mirror-pose ambiguity).
    # 2. Compute DIRECTLY via cv2.findHomography(img_pts, stage_pts_xy).
    #    Unambiguous for coplanar points by construction — homography
    #    is the unique plane-to-plane map. Works with as few as 4
    #    corner pairs (1 marker).
    #
    # For mover calibration we only need pixel ↔ floor (target_stage is
    # always on the floor plane by convention, Z=0), so the direct
    # path is strictly better. Prefer it and cross-check against the
    # pose-derived version; if they disagree, use the direct one.
    try:
        # stage_pts_xy: Nx2 floor-plane coordinates (drop Z because Z=0).
        stage_pts_xy = obj_all[:, :2].astype(np.float32)
        img_pts_xy = img_all.astype(np.float32)
        H_pixel_to_stage, _mask = cv2.findHomography(
            img_pts_xy, stage_pts_xy, method=0)  # no RANSAC; clean corners
        H_floor = H_pixel_to_stage
    except Exception as e:
        log.warning("findHomography direct path failed: %s — using pose-derived", e)
        H_cam_to_floor = K @ np.column_stack([R[:, 0], R[:, 1], tvec.flatten()])
        H_floor = np.linalg.inv(H_cam_to_floor)
    # Get camera layout position for cross-validation
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    lp = pos_map.get(fid)
    camera_pos_layout = None
    if lp:
        camera_pos_layout = {"x": lp.get("x", 0), "y": lp.get("y", 0), "z": lp.get("z", 0)}
    cam_pos_rounded = [round(float(cam_pos[0]), 1),
                       round(float(cam_pos[1]), 1),
                       round(float(cam_pos[2]), 1)]
    # Q8 — solvePnP pose is diagnostic-only. On coplanar floor markers it
    # produces physically impossible positions (negative Z, X outside the
    # stage). The direct findHomography above is the authoritative output;
    # cameraPositionDiagnostic is kept for operator-visible disagreement
    # reporting only.
    pnp_layout_disagreement_mm = None
    if camera_pos_layout:
        pnp_layout_disagreement_mm = round(float(math.sqrt(
            (cam_pos_rounded[0] - camera_pos_layout.get("x", 0)) ** 2 +
            (cam_pos_rounded[1] - camera_pos_layout.get("y", 0)) ** 2 +
            (cam_pos_rounded[2] - camera_pos_layout.get("z", 0)) ** 2
        )), 1)
    result = {
        "ok": True,
        "markersDetected": detected_count,
        "markersMatched": len(matched_ids),
        "matchedIds": matched_ids,
        # Q8 — cameraPositionDiagnostic replaces the previous cameraPosStage /
        # cameraPosition keys. Kept as diagnostic fields only — operators
        # should read cameraPos (layout) for the authoritative camera pose.
        "cameraPositionDiagnostic": {"x": cam_pos_rounded[0],
                                       "y": cam_pos_rounded[1],
                                       "z": cam_pos_rounded[2]},
        "pnpLayoutDisagreementMm": pnp_layout_disagreement_mm,
        "rmsError": round(float(err), 2),
        "method": "findHomography+solvePnPDiagnostic",
        "intrinsicSource": intrinsic_source,
        "homography": H_floor.tolist(),
        "intrinsics": {"fx": round(float(K[0, 0]), 1),
                       "fy": round(float(K[1, 1]), 1),
                       "cx": round(float(K[0, 2]), 1),
                       "cy": round(float(K[1, 2]), 1)},
    }
    if camera_pos_layout:
        result["cameraPos"] = camera_pos_layout

    # #Q7 — single-source homography. Persist only to _calibrations; the
    # legacy mirror onto fixture.homography (and the dead _calibrated_cameras
    # store) is gone. Every downstream consumer reads from
    # _calibrations[str(fid)]["matrix"].
    global _calibrations
    _calibrations[str(fid)] = {
        "matrix": H_floor.tolist(),
        "method": "stage-map-surveyed-markers",
        "markersMatched": len(matched_ids),
        "matchedIds": matched_ids,
        "rmsError": round(float(err), 2),
        "intrinsicSource": intrinsic_source,
        "frameSize": [w, h],
        "timestamp": time.time(),
    }
    try:
        _save("calibrations", _calibrations)
    except Exception as e:
        log.warning("stage-map: persist failed: %s", e)
    return jsonify(result)


@app.get("/api/cameras/<int:fid>/calibration")
def api_camera_calibration_get(fid):
    """Get calibration data for a camera."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    cal = _calibrations.get(str(fid))
    if not cal:
        return jsonify(calibrated=False)
    return jsonify(calibrated=True, error=cal.get("error"),
                   points=len(cal.get("points", [])),
                   timestamp=cal.get("timestamp"))


@app.get("/api/cameras/<int:fid>/calibration-status")
def api_camera_calibration_status(fid):
    """Q5 — return the placement-tier health for a camera so the SPA can
    show a badge (homography / fov / raw) and downstream consumers can
    gate behaviour on tier. Never 404s for a registered camera — the
    "no cal" case is still a valid status with tier='raw'.
    """
    f = next((x for x in _fixtures
              if x["id"] == fid and x.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    cal = _calibrations.get(str(fid))
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    cam_pos = pos_map.get(fid, {})
    has_pos = any(abs(float(cam_pos.get(k, 0) or 0)) > 1 for k in ("x", "y", "z"))
    has_fov = bool(f.get("fovDeg"))
    if cal and cal.get("matrix"):
        tier = "homography"
        quality_hint = "best"
    elif has_pos and has_fov:
        tier = "fov-projection"
        quality_hint = "ok"
    else:
        tier = "raw"
        quality_hint = "poor"
    return jsonify(
        ok=True,
        fid=fid,
        tier=tier,
        qualityHint=quality_hint,
        calibrated=bool(cal and cal.get("matrix")),
        timestamp=(cal or {}).get("timestamp"),
        markersMatched=(cal or {}).get("markersMatched"),
        rmsError=(cal or {}).get("rmsError"),
        intrinsicSource=(cal or {}).get("intrinsicSource"),
        hasPosition=has_pos,
        hasFov=has_fov,
        fovType=_normalise_fov_type(f.get("fovType")),
    )


# ── Moving head range calibration ─────────────────────────────────────

def _compute_axis_mapping(samples):
    """Fit a linear mapping from normalized DMX value (0-1) → stage position.

    Args:
        samples: list of (dmx_norm, stage_x, stage_z) tuples

    Returns:
        (offset, scale_x, scale_z) where stage_pos ≈ offset + dmx_norm * scale
    """
    if len(samples) < 2:
        return None
    norms = np.array([s[0] for s in samples])
    xs = np.array([s[1] for s in samples])
    zs = np.array([s[2] for s in samples])
    # Linear fit: stage_coord = a + b * dmx_norm
    A = np.vstack([np.ones_like(norms), norms]).T
    sol_x = np.linalg.lstsq(A, xs, rcond=None)[0]  # [intercept, slope]
    sol_z = np.linalg.lstsq(A, zs, rcond=None)[0]
    return {
        "intercept_x": float(sol_x[0]), "slope_x": float(sol_x[1]),
        "intercept_z": float(sol_z[0]), "slope_z": float(sol_z[1]),
    }


def _inverse_axis_lookup(mapping, target_x, target_z):
    """Given a linear mapping and target stage position, compute the DMX normalized value.
    Weight by abs(slope) so the axis with more signal dominates. (#259)"""
    sx, bx = mapping["intercept_x"], mapping["slope_x"]
    sz, bz = mapping["intercept_z"], mapping["slope_z"]
    vals, weights = [], []
    if abs(bx) > 0.001:
        vals.append((target_x - sx) / bx)
        weights.append(abs(bx))
    if abs(bz) > 0.001:
        vals.append((target_z - sz) / bz)
        weights.append(abs(bz))
    if not vals:
        return 0.5
    wsum = sum(v * w for v, w in zip(vals, weights))
    return max(0.0, min(1.0, wsum / sum(weights)))


@app.post("/api/fixtures/<int:fid>/calibrate-range")
def api_fixture_calibrate_range(fid):
    """Calibrate a moving head's pan/tilt range using camera observation.

    Body: {cameraId, panSamples: [{dmxNorm, pixelX, pixelY}], tiltSamples: [...]}
    The SPA wizard sweeps the head through its range, captures beam positions via
    the camera, and submits the collected samples here for processing.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    if f.get("fixtureType") != "dmx":
        return jsonify(err="Only DMX fixtures support range calibration"), 400

    body = request.get_json(silent=True) or {}
    cam_id = body.get("cameraId")
    pan_samples = body.get("panSamples", [])
    tilt_samples = body.get("tiltSamples", [])

    if not cam_id:
        return jsonify(err="cameraId required"), 400

    # Need camera calibration for pixel→stage transform
    cal = _calibrations.get(str(cam_id))
    if not cal or not cal.get("matrix"):
        return jsonify(err="Camera must be calibrated first"), 400

    H = cal["matrix"]

    # Transform pixel samples to stage coordinates
    pan_stage = []
    for s in pan_samples:
        sx, sz = _apply_homography(H, s["pixelX"], s["pixelY"])
        pan_stage.append((s["dmxNorm"], sx, sz))

    tilt_stage = []
    for s in tilt_samples:
        sx, sz = _apply_homography(H, s["pixelX"], s["pixelY"])
        tilt_stage.append((s["dmxNorm"], sx, sz))

    result = {}

    if len(pan_stage) >= 2:
        pan_map = _compute_axis_mapping(pan_stage)
        if pan_map:
            result["pan"] = pan_map
            result["panSampleCount"] = len(pan_stage)

    if len(tilt_stage) >= 2:
        tilt_map = _compute_axis_mapping(tilt_stage)
        if tilt_map:
            result["tilt"] = tilt_map
            result["tiltSampleCount"] = len(tilt_stage)

    if not result:
        return jsonify(err="Need at least 2 samples per axis"), 400

    result["timestamp"] = time.time()
    result["cameraId"] = cam_id
    _range_cal[str(fid)] = result
    _save("range_calibrations", _range_cal)

    f["rangeCalibrated"] = True
    _save("fixtures", _fixtures)

    return jsonify(ok=True, rangeCalibrated=True, result=result)


@app.post("/api/fixtures/<int:fid>/dmx-test")
def api_fixture_dmx_test(fid):
    """Send test DMX values to a fixture. Used by range calibration wizard.
    Body: {pan: 0-1, tilt: 0-1, dimmer: 0-1}"""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    # #511 — fixture is locked while its calibration run is active.
    if f.get("isCalibrating"):
        return jsonify(err="Fixture is being calibrated"), 423
    body = request.get_json(silent=True) or {}
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    if not prof_info:
        return jsonify(err="Fixture has no profile"), 400
    uni = f.get("dmxUniverse", 1)
    addr = f.get("dmxStartAddr", 1)
    try:
        uni_buf = _artnet.get_universe(uni)
    except Exception:
        return jsonify(err="Art-Net engine not running"), 503
    profile = {"channel_map": prof_info.get("channel_map"),
               "channels": prof_info.get("channels", [])}
    pan = body.get("pan")
    tilt = body.get("tilt")
    dimmer = body.get("dimmer")
    # Only update pan/tilt if provided and non-negative (skip when -1)
    if pan is not None and pan >= 0 and tilt is not None and tilt >= 0:
        uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
    ch_map = prof_info.get("channel_map", {})
    # Set dimmer if provided
    if dimmer is not None and "dimmer" in ch_map:
        uni_buf.set_channel(addr + ch_map["dimmer"], int(dimmer * 255))
    # Set color + strobe channels if provided
    for ch_name in ("red", "green", "blue", "white", "strobe"):
        if ch_name in ch_map:
            val = body.get(ch_name)
            if val is not None:
                uni_buf.set_channel(addr + ch_map[ch_name], int(val * 255))
    # Apply profile channel defaults for any channel not explicitly set above
    # (strobe open, color wheel white, etc.) so the beam is visible
    explicitly_set = {"pan", "tilt", "dimmer"}
    for ch_name in ("red", "green", "blue", "white", "strobe"):
        if body.get(ch_name) is not None:
            explicitly_set.add(ch_name)
    for ch in prof_info.get("channels", []):
        ch_type = ch.get("type", "")
        default = ch.get("default")
        if default is not None and default > 0 and ch_type not in explicitly_set:
            uni_buf.set_channel(addr + ch.get("offset", 0), int(default))
    return jsonify(ok=True)


@app.get("/api/fixtures/<int:fid>/calibrate-range")
def api_fixture_range_cal_get(fid):
    """Get range calibration data for a fixture."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    cal = _range_cal.get(str(fid))
    if not cal:
        return jsonify(rangeCalibrated=False)
    return jsonify(rangeCalibrated=True, **cal)


def compute_pan_tilt_calibrated(fixture_id, target_pos):
    """Compute calibrated pan/tilt for a fixture aiming at target_pos.

    Returns (pan_norm, tilt_norm) 0.0-1.0 using calibration data,
    or None if fixture has no range calibration.
    """
    cal = _range_cal.get(str(fixture_id))
    if not cal:
        return None
    pan_norm = 0.5
    tilt_norm = 0.5
    if "pan" in cal:
        pan_norm = _inverse_axis_lookup(cal["pan"], target_pos[0], target_pos[2])
    if "tilt" in cal:
        tilt_norm = _inverse_axis_lookup(cal["tilt"], target_pos[0], target_pos[2])
    return (pan_norm, tilt_norm)


# ── CV Engine — orchestrator-side computer vision (#333) ──────────────

try:
    from cv_engine import CVEngine
    _cv = CVEngine()
    log.info("CVEngine loaded — beam=%s depth=%s detection=%s",
             _cv.status()["beam"], _cv.status()["depth"], _cv.status()["detection"])
except Exception as _cv_err:
    _cv = None
    log.warning("CVEngine not available: %s", _cv_err)


@app.get("/api/cv/status")
def api_cv_status():
    """Return CV engine model loading status."""
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not initialized")
    return jsonify(ok=True, **_cv.status())


@app.post("/api/cameras/<int:fid>/detect")
def api_camera_detect_local(fid):
    """Run object detection locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        detections, ms = _cv.detect_objects(
            frame, threshold=body.get("threshold", 0.5),
            classes=body.get("classes"), input_size=body.get("inputSize", 640))
        return jsonify(ok=True, detections=detections, inferenceMs=ms)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


@app.post("/api/cameras/<int:fid>/depth")
def api_camera_depth_local(fid):
    """Run depth estimation locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        fov = f.get("fovDeg", 60)
        points, ms = _cv.generate_point_cloud(
            frame, fov, max_points=body.get("maxPoints", 5000),
            max_depth_mm=body.get("maxDepthMm", 5000))
        return jsonify(ok=True, pointCount=len(points), inferenceMs=ms)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


@app.post("/api/cameras/<int:fid>/beam-detect")
def api_camera_beam_detect_local(fid):
    """Run beam detection locally on orchestrator (#333)."""
    f = next((fx for fx in _fixtures if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(ok=False, err="CVEngine not available"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(ok=False, err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    try:
        frame = _cv.fetch_snapshot(ip, cam_idx)
        result = _cv.detect_beam(frame, cam_idx,
                                  color=body.get("color"),
                                  threshold=body.get("threshold", 30))
        return jsonify(ok=True, **result)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


# ── Stereo 3D reconstruction (#230) ──────────────────────────────────

try:
    from stereo_engine import StereoEngine
    _stereo = StereoEngine()
except ImportError:
    _stereo = None


@app.post("/api/calibration/stereo/calibrate")
def api_stereo_calibrate():
    """Build stereo engine from calibrated cameras. Requires stage-map data."""
    if _stereo is None:
        return jsonify(ok=False, err="StereoEngine not available"), 503
    body = request.get_json(silent=True) or {}
    camera_ids = body.get("cameraIds")
    # Auto-select all cameras with stage-map data if no IDs given
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"
            and f.get("cameraIp")]
    added = 0
    for cam in cams:
        fid = cam["id"]
        if camera_ids and fid not in camera_ids:
            continue
        fov = cam.get("fovDeg", 60)
        pos = None
        for p in _layout.get("children", []):
            if p.get("id") == fid:
                pos = [p.get("x", 0), p.get("y", 0), p.get("z", 0)]
                break
        if pos:
            rot = cam.get("rotation", [0, 0, 0])
            _stereo.add_camera_from_fov(str(fid), fov, 640, 480, pos, rot)
            added += 1
    return jsonify(ok=True, camerasAdded=added,
                   totalCameras=_stereo.camera_count)


@app.post("/api/calibration/stereo/triangulate")
def api_stereo_triangulate():
    """Triangulate 3D point from pixel observations across cameras."""
    if _stereo is None:
        return jsonify(ok=False, err="StereoEngine not available"), 503
    if _stereo.camera_count < 2:
        return jsonify(ok=False, err="Need at least 2 calibrated cameras"), 400
    body = request.get_json(silent=True) or {}
    observations = body.get("observations", [])
    if len(observations) < 2:
        return jsonify(ok=False, err="Need at least 2 observations"), 400
    obs_tuples = [(str(o["camId"]), o["px"], o["py"]) for o in observations]
    result = _stereo.triangulate(obs_tuples)
    if result is None:
        return jsonify(ok=False, err="Triangulation failed (parallel rays?)")
    return jsonify(ok=True, **result)


# ── Unified mover calibration (grid-based) ────────────────────────────

import mover_calibrator as _mcal

# Wire CVEngine into the calibrator for local processing (#333)
if _cv is not None:
    _mcal.set_cv_engine(_cv)

# Wire DMX engine into calibrator so it uses the engine buffer, not raw UDP (#344)
def _mcal_dmx_sender(universe_1based, start_addr, values):
    """Write DMX channels through the Art-Net/sACN engine."""
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    if not engine:
        log.warning("DMX sender: no engine running — DMX write discarded")  # #346
        return
    uni = engine.get_universe(universe_1based)
    uni.set_channels(start_addr, values)
_mcal.set_dmx_sender(_mcal_dmx_sender)


def _mcal_engine_snapshot(universe_1based):
    """#594 — return the engine's current 512-byte buffer for *universe_1based*
    so calibration can seed its local DMX buffer with the live state
    (lamp-on, mode, shutter-open, other fixtures) rather than zeros.
    Returns None when no engine is running — the calibrator falls back to
    zero-seeded writes in that case."""
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    if not engine:
        return None
    try:
        uni = engine.get_universe(universe_1based)
        return uni.get_data()
    except Exception as e:
        log.warning("engine snapshot for uni=%d failed: %s", universe_1based, e)
        return None
_mcal.set_engine_snapshot_getter(_mcal_engine_snapshot)

_mover_cal_jobs = {}  # fid_str → {thread, status, phase, progress, error, result}

# #602 — per-job log ring buffer for the richer progress panel. Capped at
# ~32 entries so a long-running calibration doesn't balloon the job dict.
_MCAL_LOG_MAX = 32

def _mcal_log(job, msg, level="info"):
    """Append a status line to the job's ring-buffered log AND the main
    log stream. Called from the calibration threads alongside existing
    log.info/log.warning to populate the SPA's live log tail.
    """
    if not isinstance(job, dict):
        return
    buf = job.get("log")
    if buf is None:
        buf = []
        job["log"] = buf
    buf.append({"t": time.time(), "level": level, "msg": str(msg)})
    if len(buf) > _MCAL_LOG_MAX:
        del buf[:len(buf) - _MCAL_LOG_MAX]
    # Mirror to the main log so legacy consumers (file log, console) still
    # see every event.
    if level == "warning":
        log.warning(msg)
    elif level == "error":
        log.error(msg)
    else:
        log.info(msg)


def _best_camera_for(fixture):
    """Pick the widest-FOV positioned camera for calibration."""
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"
            and f.get("cameraIp")]  # #342 — was cameraUrl
    if not cams:
        return None
    # Prefer widest FOV
    cams.sort(key=lambda c: c.get("fovDeg") or 60, reverse=True)
    return cams[0]


def _get_bridge_ip():
    """Find the DMX Art-Net bridge IP from discovered nodes or children."""
    nodes = _artnet.discovered_nodes if hasattr(_artnet, 'discovered_nodes') and isinstance(_artnet.discovered_nodes, dict) else {}
    for ip, info in nodes.items():
        if info.get("style") == "bridge" or "giga" in info.get("longName", "").lower():
            return ip
    if nodes:
        return next(iter(nodes))
    # Fallback: DMX children
    for c in _children:
        if c.get("type") == "dmx":
            return c.get("ip")
    # Fallback: universe routes destination
    for r in _dmx_settings.get("universeRoutes", []):
        if r.get("destination"):
            return r["destination"]
    return None


def _mover_cal_thread_markers(fid, cam, bridge_ip, mover_color,
                                warmup=False, warmup_seconds=30.0):
    """#610 marker-direct calibration. Wrapper that catches
    CalibrationAborted cleanly (like the v2 wrapper)."""
    job = _mover_cal_jobs[str(fid)]
    try:
        _mover_cal_thread_markers_body(fid, cam, bridge_ip, mover_color,
                                        warmup, warmup_seconds)
    except _mcal.CalibrationAborted:
        log.info("MOVER-CAL markers %d: cancelled by operator", fid)
        job["error"] = "Cancelled by operator"; job["status"] = "cancelled"
        job["phase"] = "cancelled"
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0] * 512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)
    except Exception as e:
        log.exception("MOVER-CAL markers %d: unhandled", fid)
        job["error"] = f"Unhandled: {e}"; job["status"] = "error"
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0] * 512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)


def _mover_cal_thread_markers_body(fid, cam, bridge_ip, mover_color,
                                     warmup=False, warmup_seconds=30.0):
    """#610 marker-direct mover calibration.

    The three-step algorithm the operator described:

      1. **Battleship discovery.** Sparse coarse grid across the full
         pan/tilt plane to find where the beam lands. Fixture-position-
         agnostic: works equally for ceiling, floor, and side mounts
         without needing an "initial aim" hint that assumes the fixture
         is pointing at visible floor.
      2. **Blink-confirm.** A small pan/tilt nudge verifies the
         detected pixel is actually the beam (it moves when we move)
         and not a reflection or ambient-light blob.
      3. **Per-marker convergence.** For each surveyed ArUco marker
         visible in the current camera frame, drive the beam to that
         marker's DETECTED pixel (no homography — the pixel IS the
         target). Record (pan, tilt, marker.stageXYZ) at convergence.
      4. **Fit.** With ≥3 samples, fit a `ParametricFixtureModel` so
         show runtime can ask "what pan/tilt lands the beam at stage
         (x, y, z)?" by inverting the model.

    The whole flow is "no operator pre-knowledge" — no pre-aiming, no
    multi-frame chessboard, no hand-specified targets. Surveyed ArUco
    markers are the only external input.
    """
    import numpy as _np
    job = _mover_cal_jobs[str(fid)]
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f:
        job["error"] = "Fixture not found"; job["status"] = "error"; return

    addr = f.get("dmxStartAddr", 1)
    uni = f.get("dmxUniverse", 1) - 1
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    _mcal._active_profile = prof_info
    _mcal._active_universe = uni + 1
    cam_ip = cam.get("cameraIp", "")
    cam_idx = cam.get("cameraIdx", 0)

    _set_calibrating(fid, True)

    # Pre-flight — surveyed markers in registry + camera can see at
    # least one of them (prescan).
    _mcal_log(job, "Markers-mode: checking registry + camera view")
    reg = [m for m in _aruco_markers
           if abs(float(m.get("z", 0) or 0)) < 50
           and abs(float(m.get("rx", 0) or 0)) < 1
           and abs(float(m.get("ry", 0) or 0)) < 1
           and abs(float(m.get("rz", 0) or 0)) < 1]
    if len(reg) < 3:
        job["error"] = (f"Need ≥3 floor-level surveyed ArUco markers in "
                        f"the registry; have {len(reg)}. Add via Setup → ArUco.")
        job["status"] = "error"; _set_calibrating(fid, False); return

    # #626 — multi-snapshot aggregation + forced blackout. Single-frame
    # detect was a coin-flip on edge markers and failed outright if a
    # previously-lit fixture was still washing the frame. Three snapshots
    # with blackout-between gives every surveyed marker a fair chance to
    # land in at least one of them.
    detect = _aruco_multi_snapshot_detect(cam, max_snapshots=3,
                                           blackout_bridge_ip=bridge_ip)
    if detect.get("err") and not detect.get("markers"):
        job["error"] = f"Snapshot/detect failed: {detect['err']}"
        job["status"] = "error"; _set_calibrating(fid, False); return
    seen_by_id = {int(m["id"]): m for m in detect.get("markers", [])}
    reg_by_id = {int(m["id"]): m for m in reg}
    usable = sorted(reg_by_id.keys() & seen_by_id.keys())
    # #626 — flag unregistered detections so the operator can decide
    # whether to add them to the registry (prevents using random ArUco
    # tags that happen to be in the scene — a decoy-marker concern from
    # review §12.8).
    unregistered = sorted(set(seen_by_id.keys()) - set(reg_by_id.keys()))
    if unregistered:
        _mcal_log(job, f"Detected unregistered ArUco markers {unregistered} — "
                       "ignored for calibration; add via Setup → ArUco if intended")
    if len(usable) < 3:
        job["error"] = (f"Camera {cam.get('name','?')} only sees "
                        f"{len(usable)} registered marker(s) ({usable}); "
                        f"need ≥3. Reposition markers or camera.")
        job["status"] = "error"; _set_calibrating(fid, False); return
    _mcal_log(job, f"Camera sees markers {usable} of {sorted(reg_by_id.keys())} "
                   f"(aggregated over {detect.get('snapshotsTaken', 1)} snapshots)")

    # Phase 1 — battleship discovery
    job["phase"] = "battleship"; job["progress"] = 10
    job["message"] = "Searching for beam (4×4 coarse grid)"
    _mcal_log(job, "Battleship discovery (4×4 coarse grid + confirm nudge)")

    def _discovery_progress(ev):
        stage = ev.get("stage")
        if stage == "grid-probe":
            probe = ev.get("probe"); total = ev.get("total", 16)
            # 10% → 25% progress band for discovery
            job["progress"] = int(10 + 15 * (probe / max(total, 1)))
            job["message"] = (f"Grid probe {probe}/{total} "
                              f"pan={ev.get('pan',0):.2f} "
                              f"tilt={ev.get('tilt',0):.2f}")
        elif stage == "beam-found":
            job["phase"] = "confirming"; job["progress"] = 25
            job["message"] = (f"Beam found at probe {ev.get('probe')}/"
                              f"{ev.get('total')} — confirming with nudge")
            _mcal_log(job, f"Beam candidate at probe {ev.get('probe')}, "
                           f"pixel ({ev.get('pixelX')},{ev.get('pixelY')}) "
                           f"— verifying with pan/tilt nudge")

    discovered = _mcal.battleship_discover(
        bridge_ip, cam_ip, addr, cam_idx, mover_color,
        profile=prof_info,
        progress_cb=_discovery_progress,
    )
    if discovered is None:
        job["error"] = ("Battleship discovery found no beam. Check "
                        "lamp, shutter, DMX wiring, and camera view "
                        "of the fixture's reachable floor area.")
        job["status"] = "error"; _set_calibrating(fid, False)
        try: _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception: pass
        return
    disc_pan, disc_tilt, disc_px, disc_py = discovered
    job["foundAt"] = {"pan": disc_pan, "tilt": disc_tilt,
                       "pixelX": disc_px, "pixelY": disc_py}
    _mcal_log(job, f"Beam confirmed at pan={disc_pan:.3f} tilt={disc_tilt:.3f} "
                   f"pixel=({disc_px},{disc_py})")

    # Phase 2 — per-marker convergence. Use discovered pan/tilt as the
    # warm-start for the FIRST marker; subsequent markers warm-start
    # from the previous converged pan/tilt (closest trajectory).
    job["phase"] = "sampling"; job["progress"] = 30
    job["totalTargets"] = len(usable)
    samples = []
    per_marker = []
    warm_pan, warm_tilt = disc_pan, disc_tilt
    for i, mid in enumerate(usable):
        job["currentTarget"] = i
        job["progress"] = int(30 + 60 * (i / len(usable)))
        marker = reg_by_id[mid]
        detected = seen_by_id[mid]
        target_px = detected["center"]
        stage_xyz = (float(marker["x"]), float(marker["y"]),
                      float(marker["z"]))
        job["message"] = (f"Converging on marker {mid} @ stage "
                          f"({stage_xyz[0]:.0f},{stage_xyz[1]:.0f}) "
                          f"pixel ({target_px[0]:.0f},{target_px[1]:.0f})")
        _mcal_log(job, job["message"])
        result = _mcal.converge_on_target_pixel(
            bridge_ip, cam_ip, addr, cam_idx, mover_color,
            target_px=target_px,
            start_pan=warm_pan, start_tilt=warm_tilt,
            profile=prof_info,
        )
        entry = {"id": mid, "stage": list(stage_xyz),
                 "targetPixel": target_px,
                 "converged": result["converged"],
                 "iterations": result["iterations"],
                 "errorPx": result.get("errorPx"),
                 "pan": result["pan"], "tilt": result["tilt"]}
        per_marker.append(entry)
        if result["converged"]:
            samples.append({
                "pan": result["pan"], "tilt": result["tilt"],
                "stageX": stage_xyz[0], "stageY": stage_xyz[1],
                "stageZ": stage_xyz[2],
                "markerId": mid, "errorPx": result["errorPx"],
            })
            warm_pan, warm_tilt = result["pan"], result["tilt"]
            _mcal_log(job, f"Marker {mid}: CONVERGED "
                           f"pan={result['pan']:.3f} tilt={result['tilt']:.3f} "
                           f"err={result['errorPx']:.1f}px "
                           f"({result['iterations']} iters)")
        else:
            # #625 — on failure, warm-start the next marker from the
            # closest pose this one reached (result["pan"]/["tilt"] is
            # best_pan/best_tilt from the bracket-and-retry loop, NOT
            # the final bouncing-on-limit pose). Only fall back to the
            # discovery pose if the loop truly never saw the beam, in
            # which case result["errorPx"] is None.
            if result.get("errorPx") is not None:
                warm_pan, warm_tilt = result["pan"], result["tilt"]
                _mcal_log(job, f"Marker {mid}: did NOT converge "
                               f"({result['errorPx']:.1f}px after "
                               f"{result['iterations']} iters) — "
                               f"next marker warm-starts from best pose "
                               f"(pan={warm_pan:.3f}, tilt={warm_tilt:.3f})",
                          level="warning")
            else:
                warm_pan, warm_tilt = disc_pan, disc_tilt
                _mcal_log(job, f"Marker {mid}: beam never re-acquired during "
                               f"convergence ({result['iterations']} iters) — "
                               f"resetting warm-start to discovery pose",
                          level="warning")

    if len(samples) < 3:
        job["error"] = (f"Only {len(samples)}/{len(usable)} markers "
                        "converged — cannot fit a stable model.")
        job["status"] = "error"
        job["targets"] = per_marker
        _set_calibrating(fid, False)
        try: _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception: pass
        return

    # Phase 3 — fit ParametricFixtureModel from the (pan, tilt, stage) samples.
    job["phase"] = "fitting"; job["progress"] = 92
    _mcal_log(job, f"Fitting ParametricFixtureModel from {len(samples)} samples")
    try:
        from parametric_mover import ParametricFixtureModel
        model = ParametricFixtureModel()
        fit_rms = model.fit(samples)
    except Exception as e:
        job["error"] = f"Model fit failed: {e}"
        job["status"] = "error"
        job["targets"] = per_marker
        _set_calibrating(fid, False)
        return

    # Save to _mover_cal in the same shape v2 uses.
    _mover_cal[str(fid)] = {
        "version": 2,
        "method": "markers",
        "cameraId": cam["id"],
        "samples": samples,
        "sampleCount": len(samples),
        "model": model.to_dict(),
        "fit": {"rmsErrorDeg": fit_rms, "sampleCount": len(samples),
                 "perMarker": per_marker},
        "timestamp": time.time(),
    }
    _save("mover_calibrations", _mover_cal)
    _invalidate_mover_model(fid)

    job["phase"] = "complete"; job["progress"] = 100
    job["status"] = "done"
    job["fit"] = _mover_cal[str(fid)]["fit"]
    job["sampleCount"] = len(samples)
    job["targets"] = per_marker
    _mcal_log(job, f"Complete. {len(samples)}/{len(usable)} markers converged, "
                   f"RMS {fit_rms:.2f}°")
    _set_calibrating(fid, False)
    try: _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
    except Exception: pass


def _mover_cal_thread_v2(fid, cam, bridge_ip, mover_color,
                          warmup=False, warmup_seconds=30.0,
                          target_overrides=None):
    """#594 — wrapper around the v2 body that catches CalibrationAborted
    so Cancel unwinds cleanly on the target-driven path too."""
    job = _mover_cal_jobs[str(fid)]
    try:
        _mover_cal_thread_v2_body(fid, cam, bridge_ip, mover_color,
                                   warmup, warmup_seconds, target_overrides)
    except _mcal.CalibrationAborted:
        log.info("MOVER-CAL v2 %d: cancelled by operator", fid)
        job["error"] = "Cancelled by operator"
        job["status"] = "cancelled"
        job["phase"] = "cancelled"
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)
    except Exception as e:
        log.exception("MOVER-CAL v2 %d: unhandled exception", fid)
        job["error"] = f"Unhandled error: {e}"
        job["status"] = "error"
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)


def _mover_cal_thread_v2_body(fid, cam, bridge_ip, mover_color,
                               warmup=False, warmup_seconds=30.0,
                               target_overrides=None):
    """#499 — per-target convergence calibration.

    Picks N floor targets (auto or operator-supplied), drives the beam
    to each via `converge_on_stage_target`, fits the parametric model
    from the collected samples. No BFS mapping phase.

    Requires a camera calibration homography on the chosen camera —
    without it we can't project stage→pixel. Falls back to the legacy
    thread with a warning if absent.
    """
    job = _mover_cal_jobs[str(fid)]

    def _blackout():
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)

    f = next((x for x in _fixtures if x["id"] == fid), None)
    if not f:
        job["error"] = "Fixture not found"; job["status"] = "error"
        _blackout(); return

    # #Q7 — single-source homography. Read only from _calibrations[str(fid)];
    # the legacy _calibrated_cameras and fixture.homography stores are gone.
    cam_cal = _calibrations.get(str(cam["id"]))
    H_flat = (cam_cal or {}).get("matrix") if cam_cal else None
    if H_flat is None:
        job["error"] = ("Camera must be calibrated (ArUco homography) before "
                         "running v2 target-driven calibration")
        job["status"] = "error"
        log.warning("MOVER-CAL v2 %d: no camera homography on cam=%d", fid, cam["id"])
        return  # don't blackout / no lock engaged yet

    _set_calibrating(fid, True)
    addr = f.get("dmxStartAddr", 1)
    uni = f.get("dmxUniverse", 1) - 1
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    _mcal._active_profile = prof_info
    _mcal._active_universe = uni + 1  # #594 — for engine snapshot seeding
    cam_ip = cam.get("cameraIp", "")
    cam_idx = cam.get("cameraIdx", 0)

    # Warmup (shared with legacy path).
    if warmup:
        job["phase"] = "warmup"; job["progress"] = 2
        job["message"] = "Warming up fixture"
        try:
            def _wp(frac):
                job["progress"] = int(2 + frac * 6)
                job["message"] = f"Warming up fixture ({int(frac*100)}%)"
            _mcal.warmup_sweep(bridge_ip, addr, color=(0, 0, 0),
                               duration_s=warmup_seconds, progress_cb=_wp)
            job["message"] = None
        except Exception as e:
            log.warning("MOVER-CAL v2 %d: warmup failed (%s)", fid, e)

    # Target selection: operator override wins, else auto-pick.
    geometry = _get_stage_geometry()
    job["geometrySource"] = geometry.get("source")
    fx_pos = _fixture_position(fid)
    cam_pos = _fixture_position(cam["id"])
    if target_overrides:
        targets = [(t[0], t[1], t[2] if len(t) > 2 else 0) for t in target_overrides]
    else:
        try:
            targets = _mcal.pick_calibration_targets(
                fx_pos, geometry, n=6,
                camera_pos=cam_pos, camera_fov_deg=cam.get("fovDeg", 90),
            )
        except Exception as e:
            job["error"] = f"Target selection failed: {e}"; job["status"] = "error"
            _blackout(); return
    if len(targets) < 4:
        job["error"] = f"Only {len(targets)} targets — need at least 4 for a stable fit"
        job["status"] = "error"; _blackout(); return

    # Status payload — per-target progress table.
    job["totalTargets"] = len(targets)
    job["targets"] = [{"idx": i, "stagePos": list(t),
                        "status": "pending", "iterations": 0,
                        "errorPx": None}
                       for i, t in enumerate(targets)]

    # Warm-start for the first aim — use v2 model when present.
    model = _get_mover_model(fid, f)

    samples = []
    for i, target in enumerate(targets):
        job["currentTarget"] = i
        job["phase"] = "sampling"
        job["progress"] = int(10 + 70 * (i / len(targets)))
        tstate = job["targets"][i]
        tstate["status"] = "converging"
        job["message"] = f"Converging on target {i+1}/{len(targets)}"
        _mcal_log(job, f"Target {i+1}/{len(targets)}: "
                       f"stage=({target[0]:.0f},{target[1]:.0f},{target[2]:.0f}) — converging")
        try:
            result = _mcal.converge_on_stage_target(
                bridge_ip, cam_ip, addr, cam_idx, mover_color,
                H_flat, target, model=model,
                start_pan=0.5, start_tilt=0.5,
            )
        except Exception as e:
            log.warning("MOVER-CAL v2 %d: converge failed on target %d: %s", fid, i, e)
            tstate["status"] = "failed"
            tstate["error"] = str(e)
            continue

        tstate["iterations"] = result.get("iterations", 0)
        tstate["errorPx"] = result.get("errorPx")
        if result.get("converged"):
            tstate["status"] = "converged"
            samples.append({
                "pan": result["pan"], "tilt": result["tilt"],
                "stageX": float(target[0]),
                "stageY": float(target[1]),
                "stageZ": float(target[2]),
                "pixelX": (result.get("beamPixel") or [0, 0])[0],
                "pixelY": (result.get("beamPixel") or [0, 0])[1],
            })
        else:
            tstate["status"] = "skipped"

    if len(samples) < 4:
        job["error"] = (f"Only {len(samples)} of {len(targets)} targets converged "
                         "— cannot fit a stable model")
        job["status"] = "error"; _blackout(); return

    # Fit + persist.
    job["phase"] = "fitting"; job["progress"] = 85
    job["message"] = "Running Levenberg-Marquardt fit"
    pan_range = f.get("panRange") or (prof_info.get("panRange") if prof_info else None) or 540
    tilt_range = f.get("tiltRange") or (prof_info.get("tiltRange") if prof_info else None) or 270
    try:
        fit_model_obj, quality = _fit_model(
            fx_pos, pan_range, tilt_range, samples,
            mounted_inverted=bool(f.get("mountedInverted")),
        )
    except Exception as e:
        job["error"] = f"LM fit failed: {e}"; job["status"] = "error"
        _blackout(); return

    cal_data = {
        "version": 2,
        "method": "v2-convergence",
        "cameraId": cam["id"],
        "color": mover_color,
        "samples": samples,
        "sampleCount": len(samples),
        "timestamp": time.time(),
        "model": fit_model_obj.to_dict(),
        "fit": quality.to_dict(),
        "targets": [{"idx": i, "stagePos": list(t),
                      "status": job["targets"][i]["status"],
                      "iterations": job["targets"][i]["iterations"],
                      "errorPx": job["targets"][i].get("errorPx")}
                     for i, t in enumerate(targets)],
        "centerPan": round(sum(s["pan"] for s in samples) / len(samples), 4),
        "centerTilt": round(sum(s["tilt"] for s in samples) / len(samples), 4),
    }

    _mover_cal[str(fid)] = cal_data
    _save("mover_calibrations", _mover_cal)
    _invalidate_mover_model(fid)
    f["moverCalibrated"] = True
    _set_calibrating(fid, False)
    _save("fixtures", _fixtures)

    job["fit"] = quality.to_dict()
    job["model"] = fit_model_obj.to_dict()
    job["result"] = {"sampleCount": len(samples),
                     "converged": sum(1 for s in job["targets"] if s["status"] == "converged")}
    job["phase"] = "complete"
    job["progress"] = 100
    job["status"] = "done"
    job["message"] = None
    log.info("MOVER-CAL v2 fid=%d: %d/%d converged, rms=%.2f°",
             fid, len(samples), len(targets), quality.rms_error_deg)
    _blackout()


def _mover_cal_thread(fid, cam, bridge_ip, mover_color,
                      warmup=False, warmup_seconds=30.0):
    """Background thread wrapper — catches any unhandled exception so the
    SPA polling loop sees `status=\"error\"` instead of a job frozen at
    `status=\"running\"` forever. The inner `_mover_cal_thread_body` does
    the real work. (#576)

    #594 — also catches CalibrationAborted so Cancel unwinds cleanly (fixture
    is blacked out, lock released, status reported to the wizard)."""
    job = _mover_cal_jobs[str(fid)]
    try:
        _mover_cal_thread_body(fid, cam, bridge_ip, mover_color, warmup, warmup_seconds)
    except _mcal.CalibrationAborted:
        log.info("MOVER-CAL %d: cancelled by operator", fid)
        job["error"] = "Cancelled by operator"
        job["status"] = "cancelled"
        job["phase"] = "cancelled"
        # Clear the cancel flag before the blackout write so _hold_dmx
        # actually runs (rather than re-raising CalibrationAborted).
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)
    except Exception as e:
        log.exception("MOVER-CAL %d: unhandled exception in cal thread", fid)
        job["error"] = f"Unhandled error: {e}"
        job["status"] = "error"
        _mcal.arm_cancel()
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        _set_calibrating(fid, False)


def _mover_cal_thread_body(fid, cam, bridge_ip, mover_color,
                           warmup=False, warmup_seconds=30.0):
    """Background thread: optional warmup → discovery → mapping → save grid."""
    job = _mover_cal_jobs[str(fid)]

    def _cal_blackout():
        """Blackout fixture and release the calibration lock when cal ends."""
        try:
            _mcal._hold_dmx(bridge_ip, [0]*512, 0.3)
        except Exception:
            pass
        # #511 — always release the lock on exit.
        _set_calibrating(fid, False)

    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        job["error"] = "Fixture not found"
        job["status"] = "error"
        _cal_blackout()
        return

    # Pre-flight sanity log — surfaces the common "silent failure" causes
    # (no profile, beam channels can't be resolved, fixture not positioned)
    # before we sink time into a warmup or discovery sweep.
    addr_pre = f.get("dmxStartAddr", 1)
    uni_pre = f.get("dmxUniverse", 1)
    pid_pre = f.get("dmxProfileId")
    prof_pre = _profile_lib.channel_info(pid_pre) if pid_pre else None
    cm_pre = (prof_pre or {}).get("channel_map", {})
    log.info("MOVER-CAL %d: start — uni=%d addr=%d profile=%s "
             "dimmer=%s strobe=%s color=%s",
             fid, uni_pre, addr_pre, pid_pre,
             cm_pre.get("dimmer"), cm_pre.get("strobe"),
             "rgb" if "red" in cm_pre else ("wheel" if "color-wheel" in cm_pre else "none"))
    if not prof_pre:
        job["error"] = ("Fixture has no DMX profile — open the fixture editor "
                        "and pick one before calibrating")
        job["status"] = "error"
        _cal_blackout()
        return

    # Kick the beam on right now so the operator can see DMX is flowing
    # before the slow phases start. If any later step fails, at least the
    # beam turning on (or not) tells them where the break is.
    # #594 — set active universe before the pre-warm write so the snapshot
    # helper seeds from the live engine buffer (preserving lamp-on / mode
    # defaults instead of zeroing the universe).
    _mcal._active_universe = uni_pre
    try:
        _dmx_pre = _mcal._fresh_buffer()
        _mcal._set_mover_dmx(_dmx_pre, addr_pre, 0.5, 0.5,
                             *mover_color, dimmer=255, profile=prof_pre)
        _mcal._send_artnet(bridge_ip, uni_pre - 1, _dmx_pre)
        log.info("MOVER-CAL %d: pre-warm beam-on sent (addr=%d uni=%d)",
                 fid, addr_pre, uni_pre)
    except Exception as e:
        log.warning("MOVER-CAL %d: pre-warm beam-on failed: %s", fid, e)

    # #511 — engage the calibration lock. Any external pan/tilt writer
    # (show bake, mover-follow, dmx-test, profile defaults) will skip this
    # fixture until we clear the flag in the finally-style cleanup below.
    _set_calibrating(fid, True)

    # #513 — optional warmup sweep before any measurement. Motors, belts,
    # and LED modules drift thermally; running the fixture through its
    # range for ~30 s stabilises pan/tilt position before samples are
    # captured.
    if warmup:
        job["phase"] = "warmup"
        job["progress"] = 2
        job["message"] = "Warming up fixture (thermal + mechanical settle)"
        try:
            def _warmup_progress(frac):
                # Warmup occupies the 2-8% progress band so the
                # downstream phases still map to their legacy ranges.
                job["progress"] = int(2 + frac * 6)
                job["message"] = f"Warming up fixture ({int(frac * 100)}%)"
            _mcal.warmup_sweep(
                bridge_ip, f.get("dmxStartAddr", 1), color=(0, 0, 0),
                duration_s=warmup_seconds, progress_cb=_warmup_progress,
            )
            job["message"] = None
            log.info("MOVER-CAL %d: warmup complete (%.0fs)", fid, warmup_seconds)
        except Exception as e:
            log.warning("MOVER-CAL %d: warmup failed (%s) — continuing without", fid, e)
    addr = f.get("dmxStartAddr", 1)
    uni = f.get("dmxUniverse", 1) - 1  # Art-Net is 0-based
    # Set profile for profile-aware DMX writes (#467)
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    _mcal._active_profile = prof_info
    _mcal._active_universe = uni + 1  # #594 — for engine snapshot seeding
    cam_ip = cam.get("cameraIp", "")  # #342
    cam_idx = cam.get("cameraIdx", 0)  # #342

    # #496 — log which stage geometry source we're calibrating against
    # so the operator (and later the wizard UI) can tell "scanned
    # floor at z=38mm" from "assumed flat floor at z=0".
    geometry = _get_stage_geometry()
    job["geometrySource"] = geometry.get("source")
    floor = geometry.get("floor") or {}
    if "z" in floor:
        job["floorZ"] = floor["z"]
    log.info("MOVER-CAL %d: geometry source=%s floor_z=%s walls=%d",
             fid, geometry.get("source"), floor.get("z"),
             len(geometry.get("walls") or []))

    # Phase 1: Discovery
    job["phase"] = "discovery"
    job["status"] = "running"
    job["progress"] = 10
    _mcal_log(job, f"Discovery phase: addr={addr_pre} uni={uni_pre} "
                   f"warmStart={job.get('warmStart','geometric')}")
    try:
        inverted = f.get("mountedInverted", False)  # #349
        # Positions live in _layout["children"], not in _fixtures
        pos_map = {p["id"]: p for p in _layout.get("children", [])}
        fp = pos_map.get(f["id"], {})
        cp = pos_map.get(cam["id"], {})
        fx_pos = [fp.get("x", 0), fp.get("y", 0), fp.get("z", 0)]
        cam_pos = [cp.get("x", 0), cp.get("y", 0), cp.get("z", 0)]

        # Compute initial aim from camera geometry in the thread (#347)
        cam_rot = cam.get("rotation", [15, 0, 0])
        cam_fov = cam.get("fovDeg", 90)
        stage_d = int(_stage.get("d", 4.0) * 1000)
        import math as _m
        _ct = cam_rot[0] if cam_rot else 15
        _fh = cam_fov / 2
        _ba = min(_ct + _fh, 89)
        _near = cam_pos[1] + cam_pos[2] / _m.tan(_m.radians(_ba)) if cam_pos[2] > 0 else 0
        _ceny = cam_pos[1] + cam_pos[2] / _m.tan(_m.radians(_ct)) if _ct > 0.1 else stage_d
        _ceny = min(_ceny, stage_d)
        _ty = _near + (_ceny - _near) * 0.67
        floor_target = [(fx_pos[0] + cam_pos[0]) / 2, _ty, 0]
        start_pan, start_tilt = _mcal.compute_initial_aim(
            fx_pos, floor_target, mounted_inverted=inverted)

        # #498 — model-predicted discovery. If a v2 parametric model
        # already exists for this fixture (re-calibration flow), use
        # model.inverse(floor_target) directly — it captures mount
        # rotation, signs, and offsets, so the beam lands on the
        # predicted pixel without the blind spiral. Falls back to the
        # legacy geometric estimate when no model is present.
        model = _get_mover_model(fid, f)
        used_model_warmstart = False
        if model is not None:
            try:
                mp, mt = model.inverse(floor_target[0], floor_target[1], floor_target[2])
                start_pan, start_tilt = mp, mt
                used_model_warmstart = True
                log.info("MOVER-CAL %d: warm-start from v2 model → pan=%.3f tilt=%.3f",
                         fid, start_pan, start_tilt)
            except Exception as e:
                log.warning("MOVER-CAL %d: model inverse failed (%s); falling back", fid, e)

        # If we didn't warm-start from a v2 model, try the legacy priority:
        # existing manual sample → orientation override → rotation-derived aim.
        if not used_model_warmstart:
            existing_cal = _mover_cal.get(str(fid), {})
            existing_samples = existing_cal.get("samples", [])
            if existing_samples:
                s = existing_samples[0]
                # Samples can be dicts {pan, tilt, ...} or lists [pan, tilt, x, y, ...]
                if isinstance(s, dict):
                    start_pan = s["pan"]
                    start_tilt = s["tilt"]
                elif isinstance(s, (list, tuple)) and len(s) >= 2:
                    start_pan = s[0]
                    start_tilt = s[1]
                log.info("MOVER-CAL %d: starting from manual sample pan=%.3f tilt=%.3f",
                         fid, start_pan, start_tilt)
            else:
                # Override with orientation/rotation if available
                orient = f.get("orientation", {})
                if orient.get("homePan") is not None:
                    start_pan = orient["homePan"]
                    start_tilt = orient.get("homeTilt", 0.5)
                rot = f.get("rotation", [0, 0, 0])
                if any(v != 0 for v in rot):
                    aim_pt = _rotation_to_aim(rot, fx_pos)
                    pt = _mcal.compute_initial_aim(fx_pos, aim_pt, mounted_inverted=inverted)
                    if pt:
                        start_pan, start_tilt = pt
        job["warmStart"] = "model" if used_model_warmstart else "geometric"

        job["debug"] = {"fx_pos": fx_pos, "cam_pos": cam_pos, "cam_rot": cam_rot,
                        "cam_fov": cam_fov, "stage_d": stage_d, "inverted": inverted,
                        "start_pan": start_pan, "start_tilt": start_tilt,
                        "floor_target": floor_target}
        found = _mcal.discover(
            bridge_ip, cam_ip, addr, cam_idx, mover_color,
            universe=uni, mover_pos=fx_pos, camera_pos=cam_pos,
            start_pan=start_pan, start_tilt=start_tilt,
            mounted_inverted=inverted, max_probes=80,
            camera_rotation=cam_rot, camera_fov=cam_fov,
            stage_depth=stage_d)
        if not found:
            job["error"] = "Beam not found — check fixture and camera positions"
            job["status"] = "error"
            _cal_blackout()
            return
        job["progress"] = 30
        # discover() returns (pan, tilt, pixelX, pixelY) tuple
        found_pan, found_tilt = found[0], found[1]
        found_px, found_py = found[2], found[3]
        job["foundAt"] = {"pan": found_pan, "tilt": found_tilt,
                          "pixelX": found_px, "pixelY": found_py}
        log.info("MOVER-CAL fixture %d: beam discovered at pan=%.2f tilt=%.2f pixel=(%d,%d)",
                 fid, found_pan, found_tilt, found_px, found_py)
    except Exception as e:
        job["error"] = f"Discovery failed: {e}"
        job["status"] = "error"
        log.exception("Mover cal discovery error fid=%d", fid)
        _cal_blackout()
        return

    # Phase 2: BFS mapping
    job["phase"] = "mapping"
    job["progress"] = 35
    job["message"] = "Mapping visible region (BFS from discovered beam)"
    _mcal_log(job, f"Beam found at pan={found_pan:.3f} tilt={found_tilt:.3f} "
                   f"px=({found_px},{found_py}) — starting BFS mapping")
    # #576 — stream per-sample progress back to the SPA so the modal
    # shows which pan/tilt position is being probed and how many samples
    # have been collected. Without this the UI sat at "Mapping..." for
    # 30-60s with no indication the thread was alive.
    _map_target = 50
    def _mapping_progress(sample_count, cur_pan, cur_tilt):
        # 35-70% progress band for the mapping phase.
        frac = min(sample_count / _map_target, 1.0)
        job["progress"] = int(35 + frac * 35)
        job["sampleCount"] = sample_count
        job["message"] = (f"Mapping: {sample_count}/{_map_target} samples · "
                          f"current pan={cur_pan:.2f} tilt={cur_tilt:.2f}")
    try:
        samples, boundaries = _mcal.map_visible(
            bridge_ip, cam_ip, addr, cam_idx, mover_color,
            start_pan=found_pan, start_tilt=found_tilt,
            collect_3d=False, max_samples=_map_target,
            progress_cb=_mapping_progress)
        if len(samples) < 6:
            job["error"] = f"Only {len(samples)} samples collected — need at least 6"
            job["status"] = "error"
            _cal_blackout()
            return
        job["progress"] = 70
        job["sampleCount"] = len(samples)
        log.info("MOVER-CAL fixture %d: %d BFS samples collected", fid, len(samples))
    except Exception as e:
        job["error"] = f"Mapping failed: {e}"
        job["status"] = "error"
        log.exception("Mover cal mapping error fid=%d", fid)
        _cal_blackout()
        return

    # Phase 3: Build grid
    job["phase"] = "grid"
    job["progress"] = 80
    _mcal_log(job, f"Mapping complete: {len(samples)} samples — building grid")
    try:
        grid = _mcal.build_grid(samples)
        if not grid:
            job["error"] = "Grid build failed — insufficient sample spread"
            job["status"] = "error"
            _cal_blackout()
            return
    except Exception as e:
        job["error"] = f"Grid build failed: {e}"
        job["status"] = "error"
        _cal_blackout()
        return

    # Phase 3.5 — verification sweep (#501). Aim at 3 held-out pan/tilt
    # points and measure pixel-space error vs grid prediction. Failures
    # here flag overfitting / sample-coverage issues without blocking
    # the save — we always persist the cal; verification is advisory.
    job["phase"] = "verification"
    job["progress"] = 90
    verification = None
    try:
        fit_keys = [(s[0], s[1]) if isinstance(s, (list, tuple)) else (s.get("pan"), s.get("tilt"))
                    for s in samples]
        verification = _mcal.verification_sweep(
            bridge_ip, cam_ip, addr, cam_idx, mover_color, grid,
            n_points=3, avoid_samples=fit_keys,
        )
        # Summary: worst pixel error across the sweep.
        errs = [v["errorPx"] for v in (verification or []) if v.get("errorPx") is not None]
        if errs:
            worst = max(errs)
            rms = math.sqrt(sum(e * e for e in errs) / len(errs))
            job["verification"] = {
                "points": verification, "rmsErrorPx": rms, "maxErrorPx": worst,
                "skipped": False,
            }
            log.info("MOVER-CAL %d: verification sweep rms=%.1fpx max=%.1fpx",
                     fid, rms, worst)
        else:
            job["verification"] = {"points": verification or [], "skipped": True,
                                     "reason": "no beam detected on any verification point"}
            log.warning("MOVER-CAL %d: verification sweep detected no beam", fid)
    except Exception as e:
        log.warning("MOVER-CAL %d: verification failed (%s) — continuing", fid, e)
        job["verification"] = {"skipped": True, "reason": f"exception: {e}"}

    # Save calibration data. Q9-P3 phase 4 — only the samples list is a
    # schema-v2 input; `grid` / `boundaries` / `foundAt` / `centerPan` /
    # `centerTilt` were v1-only structures whose values no longer have any
    # read path. Dropped from the persisted cal to keep mover_calibrations.json
    # clean. The job result still exposes sampleCount / gridSize so the SPA
    # progress card works unchanged.
    cal_data = {
        "cameraId": cam["id"],
        "color": mover_color,
        "samples": samples,
        "sampleCount": len(samples),
        "timestamp": time.time(),
    }
    if job.get("verification"):
        cal_data["verification"] = job["verification"]
    _mover_cal[str(fid)] = cal_data
    _save("mover_calibrations", _mover_cal)
    _invalidate_mover_model(fid)
    # Q9-P3 phase 3 — fit v2 inline immediately. Previously the save left a
    # v1 cal on disk and lazy-migrated on first _get_mover_model call.
    # Eagerly fitting here means a successful legacy run writes v2 directly,
    # so there's no migration state to carry across restarts.
    try:
        _get_mover_model(fid)
    except Exception as _e:
        log.warning("Q9-P3 legacy cal: inline v2 fit failed for fid=%d: %s "
                    "(cal stays v1 until next use)", fid, _e)
    f["moverCalibrated"] = True
    # #511 — release the lock before persisting so isCalibrating doesn't
    # leak into fixtures.json.
    _set_calibrating(fid, False)
    _save("fixtures", _fixtures)

    # #500 — populate the job with v2 fit/model so SPA polling picks up
    # the quality metrics before the job expires from _mover_cal_jobs.
    v2_cal = _mover_cal.get(str(fid)) or {}
    if v2_cal.get("version") == 2:
        if "fit" in v2_cal:
            job["fit"] = v2_cal["fit"]
        if "model" in v2_cal:
            job["model"] = v2_cal["model"]
    else:
        # Q9-P3 phase 2 — deprecation breadcrumb when a legacy cal stays v1
        # on disk after save. A v2 fit normally happens inline above; only
        # truly under-sampled or degenerate rigs land here. Operators see
        # this in logs and can decide whether the cal is usable.
        log.warning("Q9-P3: fixture %d cal saved as v1 (%d samples) — v2 fit "
                    "deferred; SPA will surface the v1 grid until fit succeeds",
                    fid, len(samples))

    job["result"] = {"sampleCount": len(samples), "gridSize": len(grid.get("panSteps", []))}
    job["progress"] = 100
    job["status"] = "done"
    job["phase"] = "complete"
    log.info("MOVER-CAL fixture %d: calibration complete, %d samples, grid %s",
             fid, len(samples), job["result"]["gridSize"])
    _cal_blackout()


@app.post("/api/calibration/mover/<int:fid>/start")
def api_mover_cal_start(fid):
    """Start unified mover calibration (discovery + BFS + grid) in background."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    # Check if already running
    existing = _mover_cal_jobs.get(str(fid))
    if existing and existing.get("status") == "running":
        return jsonify(err="Calibration already running"), 409
    cam = _best_camera_for(f)
    if not cam:
        return jsonify(err="No camera available — register and position a camera first"), 400
    bridge_ip = _get_bridge_ip()
    if not bridge_ip:
        return jsonify(err="No Art-Net bridge found — start the Art-Net engine"), 400
    if not _artnet.running and not _sacn.running:  # #346
        return jsonify(err="DMX engine is not running — start it from Settings \u2192 DMX Engine"), 400
    body = request.get_json(silent=True) or {}
    color = body.get("color", [0, 255, 0])  # default green
    warmup = bool(body.get("warmup", False))
    warmup_seconds = float(body.get("warmupSeconds", 30.0))
    # #499 — opt-in per-target convergence loop. Default stays on the
    # legacy BFS path until we've hardware-validated the new loop.
    mode = body.get("mode", "legacy")
    if mode not in ("legacy", "v2", "markers"):
        mode = "legacy"
    target_overrides = body.get("targets")  # optional list of [x, y, z]
    job = {"status": "running", "phase": "starting", "progress": 0,
           "error": None, "result": None, "cameraId": cam["id"],
           "cameraName": cam.get("name", "Camera"), "bridgeIp": bridge_ip,
           "warmup": warmup, "mode": mode}
    _mover_cal_jobs[str(fid)] = job
    # #594 — clear any stale cancel flag from a previous aborted run before
    # the new thread starts checking it.
    _mcal.arm_cancel()
    # #602 — reset probe counter + clear last-probe so the UI starts
    # fresh with attempt=1 on the next _send_artnet.
    _mcal.reset_probe_counter()
    _mcal_log(job, f"Calibration started (mode={mode}, camera={cam.get('name','?')}, "
                   f"bridge={bridge_ip})")
    if mode == "v2":
        t = threading.Thread(
            target=_mover_cal_thread_v2,
            args=(fid, cam, bridge_ip, color, warmup, warmup_seconds,
                   target_overrides),
            daemon=True)
    elif mode == "markers":
        # #610 — marker-direct cal. Discover beam, then drive it to
        # each visible+surveyed marker's detected pixel, record
        # (pan, tilt, marker.stageXYZ) per sample.
        t = threading.Thread(
            target=_mover_cal_thread_markers,
            args=(fid, cam, bridge_ip, color, warmup, warmup_seconds),
            daemon=True)
    else:
        t = threading.Thread(target=_mover_cal_thread,
                             args=(fid, cam, bridge_ip, color, warmup, warmup_seconds),
                             daemon=True)
    job["thread"] = t
    t.start()
    return jsonify(ok=True, started=True, cameraId=cam["id"],
                   cameraName=cam.get("name"), warmup=warmup)


@app.get("/api/calibration/mover/<int:fid>/targets")
def api_mover_cal_targets(fid):
    """Preview auto-selected calibration targets for a fixture (#497).

    Returns the target list the cal thread *would* pick if calibration
    started now, computed from the current stage geometry + fixture /
    camera positions. Accepts optional ?n=6 query for target count.
    """
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    try:
        n = int(request.args.get("n", 6))
    except ValueError:
        n = 6
    cam = _best_camera_for(f)
    geometry = _get_stage_geometry()
    fx_pos = _fixture_position(fid)
    cam_pos = _fixture_position(cam["id"]) if cam else None
    cam_fov = cam.get("fovDeg", 90) if cam else 90
    try:
        targets = _mcal.pick_calibration_targets(
            fx_pos, geometry, n=n,
            camera_pos=cam_pos, camera_fov_deg=cam_fov,
        )
    except Exception as e:
        log.warning("pick_calibration_targets failed for fid=%d: %s", fid, e)
        targets = []
    return jsonify(
        ok=True,
        targets=[{"x": float(t[0]), "y": float(t[1]), "z": float(t[2])}
                  for t in targets],
        geometrySource=geometry.get("source"),
        fixturePos=list(fx_pos),
        cameraId=cam["id"] if cam else None,
    )


@app.get("/api/calibration/mover/<int:fid>/status")
def api_mover_cal_status(fid):
    """Poll calibration progress.

    #500 — enhanced schema. Running jobs now surface `targets` (per-target
    progress table, populated by the v2 convergence loop when it lands
    in #499) and `currentTarget`/`totalTargets` counters. Done jobs
    return the v2 fit quality metrics and model parameters so the wizard
    can show residuals without a second round-trip.
    """
    job = _mover_cal_jobs.get(str(fid))
    if not job:
        cal = _mover_cal.get(str(fid))
        if cal:
            resp = {
                "status": "done",
                "calibrated": True,
                "sampleCount": cal.get("sampleCount"),
                "timestamp": cal.get("timestamp"),
                "calibrationLocked": bool(_fixture_is_calibrating(fid)),
            }
            if cal.get("version") == 2:
                resp["version"] = 2
                if "fit" in cal:
                    resp["fit"] = cal["fit"]
                if "model" in cal:
                    resp["model"] = cal["model"]
            if "verification" in cal:
                resp["verification"] = cal["verification"]
            return jsonify(**resp)
        return jsonify(status="none", calibrated=False,
                       calibrationLocked=bool(_fixture_is_calibrating(fid)))
    # #602 — build currentProbe + dmxFrame from the mover_calibrator
    # live state so the SPA can show what the fixture is being told to do
    # right now, independent of the phase-name string.
    probe = _mcal.get_last_probe() or {}
    current_probe = None
    dmx_frame = None
    if probe:
        current_probe = {
            "pan": probe.get("pan"),
            "tilt": probe.get("tilt"),
            "dmxPan": probe.get("dmxPan"),
            "dmxTilt": probe.get("dmxTilt"),
            "rgb": probe.get("rgb"),
            "dimmer": probe.get("dimmer"),
            "attempt": probe.get("attempt"),
            "sentAt": probe.get("sentAt"),
        }
        if probe.get("channels") is not None:
            dmx_frame = {
                "universe": probe.get("universe"),
                "addr": probe.get("addr"),
                "channels": probe.get("channels"),
            }
    return jsonify(
        status=job["status"],
        phase=job.get("phase"),
        progress=job.get("progress", 0),
        error=job.get("error"),
        result=job.get("result"),
        cameraId=job.get("cameraId"),
        foundAt=job.get("foundAt"),
        sampleCount=job.get("sampleCount"),
        debug=job.get("debug"),
        targets=job.get("targets") or [],
        currentTarget=job.get("currentTarget"),
        totalTargets=job.get("totalTargets"),
        message=job.get("message"),
        fit=job.get("fit"),
        model=job.get("model"),
        verification=job.get("verification"),
        warmStart=job.get("warmStart"),
        geometrySource=job.get("geometrySource"),
        floorZ=job.get("floorZ"),
        calibrationLocked=bool(_fixture_is_calibrating(fid)),
        currentProbe=current_probe,
        dmxFrame=dmx_frame,
        log=job.get("log") or [],
    )


@app.get("/api/calibration/mover/<int:fid>")
def api_mover_cal_get(fid):
    """Get saved mover calibration data."""
    cal = _mover_cal.get(str(fid))
    if not cal:
        return jsonify(calibrated=False)
    return jsonify(calibrated=True, sampleCount=cal.get("sampleCount"),
                   timestamp=cal.get("timestamp"),
                   grid=cal.get("grid") is not None,
                   cameraId=cal.get("cameraId"),
                   method=cal.get("method"),
                   centerPan=cal.get("centerPan"),
                   centerTilt=cal.get("centerTilt"),
                   samples=cal.get("samples"))


@app.post("/api/calibration/mover/<int:fid>/cancel")
def api_mover_cal_cancel(fid):
    """#594/#604 — signal the running calibration thread to abort AND
    immediately zero the fixture's DMX channel range so the beam goes
    dark right now, independent of when the background thread actually
    unwinds.

    The thread-level cancel (`_check_cancel()` inside `_hold_dmx` /
    `_wait_settled`) is best-effort — if the thread is blocked inside a
    camera `urlopen()` call (up to 30 s for `/depth-map`, 5 s for
    `/beam-detect`), the flag isn't checked until the HTTP request
    returns. Meanwhile the 40 Hz Art-Net engine keeps re-transmitting
    whatever non-blackout frame the thread last wrote, so the moving
    head keeps pointing/lit. Operators report "I pressed Cancel and
    the light stayed on."

    Fix: overlay a zero-seeded window on the running engine's universe
    buffer for just this fixture's channel range. The engine's next
    frame (within 25 ms) carries the zeros to the bridge. Other
    fixtures sharing the universe are untouched. The thread still
    unwinds via its own CalibrationAborted path and fires its own
    `_cal_blackout` — redundant but harmless.
    """
    job = _mover_cal_jobs.get(str(fid))
    if not job or job.get("status") != "running":
        return jsonify(ok=True, cancelled=False, reason="no running calibration")
    job["cancelRequested"] = True
    _mcal.request_cancel()
    log.info("MOVER-CAL %d: cancel requested by operator", fid)

    # Immediate foreground blackout — don't wait for the thread.
    try:
        f = next((x for x in _fixtures if x.get("id") == fid), None)
        if f and f.get("fixtureType") == "dmx":
            uni = int(f.get("dmxUniverse", 1))
            addr = int(f.get("dmxStartAddr", 1))
            pid = f.get("dmxProfileId")
            info = _profile_lib.channel_info(pid) if pid else None
            ch_count = int((info or {}).get("channelCount") or
                           f.get("dmxChannelCount") or 13)
            engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
            if engine:
                uni_buf = engine.get_universe(uni)
                # Zero exactly this fixture's channel window — leave
                # everything else (other fixtures on this universe,
                # profile defaults not yet seeded) alone.
                uni_buf.set_channels(addr, [0] * ch_count)
                log.info("MOVER-CAL %d: immediate blackout applied to "
                         "uni=%d addr=%d..%d",
                         fid, uni, addr, addr + ch_count - 1)
    except Exception as e:
        log.warning("MOVER-CAL %d: immediate blackout failed: %s", fid, e)

    return jsonify(ok=True, cancelled=True)


@app.delete("/api/calibration/mover/<int:fid>")
def api_mover_cal_delete(fid):
    """Delete mover calibration data."""
    if str(fid) in _mover_cal:
        del _mover_cal[str(fid)]
        _save("mover_calibrations", _mover_cal)
        _invalidate_mover_model(fid)
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if f:
        f.pop("moverCalibrated", None)
        _save("fixtures", _fixtures)
    return jsonify(ok=True)


@app.get("/api/calibration/mover/<int:fid>/residuals")
def api_mover_cal_residuals(fid):
    """Per-sample residual data for the 3D viewport (#512).

    Returns one entry per calibration sample with:
      - ``actual``: the stage target the sample was recorded at.
      - ``predicted``: where the fitted model thinks that pan/tilt pair
        lands on the floor plane (Z = `geometry.floor.z` when available,
        otherwise 0).
      - ``errorMm``: 3D distance between actual and predicted.

    Scene-3d renders a short line per entry so operators can spot bad
    samples at a glance without reading the residual table.
    """
    cal = _mover_cal.get(str(fid))
    if not cal:
        return jsonify(err="Fixture not calibrated"), 404
    model = _get_mover_model(fid)
    if model is None:
        return jsonify(err="No parametric model — re-calibrate to generate"), 400
    geometry = _get_stage_geometry()
    floor_z = (geometry.get("floor") or {}).get("z", 0)
    fx_pos = _fixture_position(fid)
    entries = []
    for s in (cal.get("samples") or []):
        if not isinstance(s, dict):
            continue
        pan = s.get("pan")
        tilt = s.get("tilt")
        actual = (s.get("stageX"), s.get("stageY"), s.get("stageZ"))
        if None in (pan, tilt) or None in actual:
            continue
        try:
            dx, dy, dz = model.forward(pan, tilt)
        except Exception:
            continue
        # Intersect the beam with the floor plane at Z = floor_z.
        if abs(dz) < 1e-6:
            continue
        t = (floor_z - fx_pos[2]) / dz
        if t <= 0:
            continue
        px = fx_pos[0] + dx * t
        py = fx_pos[1] + dy * t
        pz = floor_z
        err = math.sqrt((px - actual[0]) ** 2 + (py - actual[1]) ** 2
                         + (pz - actual[2]) ** 2)
        entries.append({
            "pan": pan, "tilt": tilt,
            "actual": [float(actual[0]), float(actual[1]), float(actual[2])],
            "predicted": [float(px), float(py), float(pz)],
            "errorMm": float(err),
        })
    return jsonify(ok=True, samples=entries,
                   fixturePos=list(fx_pos),
                   floorZ=float(floor_z))


@app.post("/api/calibration/mover/<int:fid>/exclude-sample")
def api_mover_cal_exclude_sample(fid):
    """Remove a calibration sample and re-fit the v2 parametric model (#504).

    Body: ``{index: int}`` — zero-based index into the current samples
    list. The sample is popped, the remaining samples are re-fit via
    fit_model, and the fresh model + fit quality are persisted. Returns
    the new fit quality so the wizard's residual table can refresh.
    """
    cal = _mover_cal.get(str(fid))
    if not cal or not cal.get("samples"):
        return jsonify(err="Fixture not calibrated"), 404
    body = request.get_json(silent=True) or {}
    idx = body.get("index")
    if not isinstance(idx, int):
        return jsonify(err="index required"), 400
    samples = list(cal["samples"])
    if idx < 0 or idx >= len(samples):
        return jsonify(err=f"index {idx} out of range (0-{len(samples)-1})"), 400
    if len(samples) <= 2:
        return jsonify(err="At least 2 samples required — re-calibrate instead"), 400

    removed = samples.pop(idx)
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    pos = _fixture_position(fid)
    prof = _profile_lib.channel_info(f.get("dmxProfileId")) \
        if f.get("dmxProfileId") else None
    pan_range = f.get("panRange") \
        or (prof.get("panRange") if prof else None) or 540
    tilt_range = f.get("tiltRange") \
        or (prof.get("tiltRange") if prof else None) or 270
    try:
        model, quality = _fit_model(
            pos, pan_range, tilt_range, samples,
            mounted_inverted=bool(f.get("mountedInverted")),
        )
    except Exception as e:
        # Put the sample back — we can't re-fit without it.
        samples.insert(idx, removed)
        return jsonify(err=f"Re-fit failed: {e}"), 400

    cal["samples"] = samples
    cal["sampleCount"] = len(samples)
    cal["version"] = 2
    cal["model"] = model.to_dict()
    cal["fit"] = quality.to_dict()
    _mover_cal[str(fid)] = cal
    _save("mover_calibrations", _mover_cal)
    _invalidate_mover_model(fid)
    log.info("Mover %d: excluded sample %d → re-fit rms=%.2f° max=%.2f° (N=%d)",
             fid, idx, quality.rms_error_deg, quality.max_error_deg, len(samples))
    return jsonify(ok=True, fit=quality.to_dict(), model=model.to_dict(),
                   sampleCount=len(samples))


@app.post("/api/calibration/mover/<int:fid>/aim")
def api_mover_cal_aim(fid):
    """Use calibration grid to aim a mover at a target pixel or stage position.
    Body: {targetX, targetY} (stage mm) or {pixelX, pixelY}"""
    cal = _mover_cal.get(str(fid))
    if not cal or (not cal.get("grid") and not cal.get("samples")):
        return jsonify(err="Fixture not calibrated"), 400
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    body = request.get_json(silent=True) or {}
    grid = cal["grid"]
    pan = tilt = None

    # Stage coordinate target — use affine transform for extrapolation (#371)
    tx = body.get("targetX")
    ty = body.get("targetY")
    tz = body.get("targetZ", 0)
    if tx is not None and ty is not None:
        samples = cal.get("samples", [])
        if samples and len(samples) >= 2:
            pt = _mcal.affine_pan_tilt(samples, tx, ty, tz)
            if pt:
                pan, tilt = pt
        # Fallback: grid_inverse treats stage coords as "pixel" coords for manual grids
        if pan is None and grid:
            pan, tilt = _mcal.grid_inverse(grid, tx, ty)

    # Direct pixel target
    if pan is None:
        px = body.get("pixelX")
        py = body.get("pixelY")
        if px is not None and py is not None and grid:
            pan, tilt = _mcal.grid_inverse(grid, px, py)

    if pan is not None:
        # Send DMX
        pid = f.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        if prof_info:
            uni = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            try:
                uni_buf = _artnet.get_universe(uni)
                profile = {"channel_map": prof_info.get("channel_map"),
                           "channels": prof_info.get("channels", [])}
                uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
            except Exception:
                pass
        return jsonify(ok=True, pan=round(pan, 4), tilt=round(tilt, 4))
    return jsonify(err="Provide targetX/targetY (stage mm) or pixelX/pixelY"), 400


@app.post("/api/calibration/mover/<int:fid>/manual")
def api_mover_cal_manual(fid):
    """Save manual calibration from jog marker samples (#368).

    Body: {samples: [{pan, tilt, stageX, stageY, stageZ}, ...]}
    The grid maps pan/tilt → stage coords (not pixels), so grid_inverse
    returns pan/tilt from a stage target directly.

    Manual calibration is atomic — flag is set only for the duration of
    the save so external writers (show bake / gyro) don't clobber
    pan/tilt while samples are being serialized.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    _set_calibrating(fid, True)
    body = request.get_json(silent=True) or {}
    samples = body.get("samples", [])
    if len(samples) < 2:
        _set_calibrating(fid, False)
        return jsonify(err="Need at least 2 calibration samples"), 400

    # Build grid using stage coords as the "pixel" dimension
    grid_samples = [(s["pan"], s["tilt"], s["stageX"], s["stageY"]) for s in samples]
    grid = None
    if len(grid_samples) >= 2:
        try:
            grid = _mcal.build_grid(grid_samples)
        except Exception as e:
            log.warning("Manual calibration grid build failed: %s", e)

    avg_pan = sum(s["pan"] for s in samples) / len(samples)
    avg_tilt = sum(s["tilt"] for s in samples) / len(samples)
    avg_x = sum(s["stageX"] for s in samples) / len(samples)
    avg_y = sum(s["stageY"] for s in samples) / len(samples)
    avg_z = sum(s.get("stageZ", 0) for s in samples) / len(samples)

    # Compute boundaries from samples
    pans = [s["pan"] for s in samples]
    tilts = [s["tilt"] for s in samples]

    cal_data = {
        "method": "manual",
        "samples": samples,
        "grid": grid,
        "boundaries": {
            "panMin": round(min(pans), 3), "panMax": round(max(pans), 3),
            "tiltMin": round(min(tilts), 3), "tiltMax": round(max(tilts), 3),
            "verified": False,
        },
        "centerPan": round(avg_pan, 4),
        "centerTilt": round(avg_tilt, 4),
        "centerTarget": [round(avg_x), round(avg_y), round(avg_z)],
        "sampleCount": len(samples),
        "timestamp": time.time(),
    }

    # #490 — fit the parametric v2 model inline. The lazy migration path
    # would eventually do this on first read, but doing it here surfaces
    # the fit quality in the POST response so the calibration wizard can
    # show residuals to the operator immediately.
    pos = _fixture_position(fid)
    prof = _profile_lib.channel_info(f.get("dmxProfileId")) \
        if f.get("dmxProfileId") else None
    pan_range = f.get("panRange") \
        or (prof.get("panRange") if prof else None) or 540
    tilt_range = f.get("tiltRange") \
        or (prof.get("tiltRange") if prof else None) or 270
    fit_quality = None
    try:
        model, quality = _fit_model(
            pos, pan_range, tilt_range, samples,
            mounted_inverted=bool(f.get("mountedInverted")),
        )
        cal_data["version"] = 2
        cal_data["model"] = model.to_dict()
        cal_data["fit"] = quality.to_dict()
        fit_quality = quality
    except Exception as e:
        log.warning("Mover %d LM fit failed on manual save: %s", fid, e)

    _mover_cal[str(fid)] = cal_data
    _save("mover_calibrations", _mover_cal)
    _invalidate_mover_model(fid)
    f["moverCalibrated"] = True
    _save("fixtures", _fixtures)
    log.info("Manual calibration saved for fixture %d: %d samples, grid=%s, rms=%s",
             fid, len(samples), "yes" if grid else "no",
             f"{fit_quality.rms_error_deg:.2f}°" if fit_quality else "n/a")
    resp = {"ok": True, "sampleCount": len(samples), "hasGrid": grid is not None}
    if fit_quality is not None:
        resp["fit"] = fit_quality.to_dict()
    _set_calibrating(fid, False)
    return jsonify(**resp)


def pixel_to_pan_tilt(fixture_id, px, py):
    """Direct pixel→pan/tilt lookup using mover calibration grid.
    Returns (pan, tilt) or None if not calibrated."""
    cal = _mover_cal.get(str(fixture_id))
    if not cal or not cal.get("grid"):
        return None
    return _mcal.grid_inverse(cal["grid"], px, py)


# ── Environment point cloud ───────────────────────────────────────────

from space_mapper import SpaceScan

_space_scan = SpaceScan()
_point_cloud = _load("pointcloud", None)

# Analyzed surfaces cache (#496) — computed lazily from _point_cloud.
_stage_surfaces_cache = {"key": None, "value": None}


def _objects_as_obstacles():
    """Convert user-placed Objects to obstacle dicts (#605 pillar gap).

    Monocular depth models miss textureless structures (white pillars,
    blank walls, glossy glass). The surveyed markers + auto-Z-alignment
    (#599) fix the scale prior but not the coverage gap — a pillar
    ZoeDepth can't see won't appear in `surface_analyzer.obstacles` no
    matter how tightly we align the cloud. The user's escape hatch is
    to place the obstacle manually as an Object in the Layout tab.

    This helper maps such Objects into the same obstacle-dict shape
    `surface_analyzer._cluster_obstacles` produces, so `ray_surface_
    intersect` + `pick_calibration_targets` can consume them uniformly.

    Inclusion rule — must be a structural object:
      - `objectType in {prop, floor, wall, pillar, obstacle}`
      - `transform.scale` has non-zero X, Y, Z (a point object with
        zero extents has no surface to intersect).

    Size convention matches `_cluster_obstacles`: `[w (X), h (Z), d (Y)]`.
    """
    out = []
    structural = {"prop", "pillar", "obstacle", "wall", "floor"}
    for o in _objects:
        otype = o.get("objectType") or "custom"
        if otype not in structural:
            continue
        tr = o.get("transform") or {}
        pos = tr.get("pos")
        scale = tr.get("scale")
        if not pos or not scale or len(pos) < 3 or len(scale) < 3:
            continue
        w = float(scale[0] or 0)
        h = float(scale[1] or 0)  # Z-extent (height)
        d = float(scale[2] or 0)  # Y-extent (depth)
        if w <= 0 or h <= 0 or d <= 0:
            continue
        out.append({
            "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
            "size": [w, h, d],
            "label": otype,
            "source": f"object:{o.get('id')}",
            "objectName": o.get("name"),
        })
    return out


def _get_stage_geometry():
    """Return a dict of structural surfaces for calibration (#496).

    Priority chain:
      1. Point cloud — run `surface_analyzer.analyze_surfaces` on the
         latest scan. Produces floor Z (not assumed 0), wall normals,
         obstacle clusters. Cached until the point cloud changes.
      2. Layout box — synthetic floor at Z=0 + 4 walls from stage w/d/h.

    Either path has user-placed structural Objects (pillars, props,
    walls) appended to its `obstacles` list — the cloud misses
    textureless columns, so the operator's manual box is the only way
    to tell the beam-solver "there's a thing here." See
    `_objects_as_obstacles` for the schema translation.

    Consumers (ray_surface_intersect, target selection) accept either
    form so the fallback is safe.
    """
    global _stage_surfaces_cache
    pc = _point_cloud
    if pc and pc.get("points"):
        # Cache key covers point-count plus the last-known bbox so re-scans
        # invalidate naturally. Use a stable tuple of sizes, not the full
        # point list (cheap + catches common edits).
        key = (
            len(pc.get("points") or []),
            pc.get("stageW"), pc.get("stageH"), pc.get("stageD"),
        )
        if _stage_surfaces_cache["key"] == key and _stage_surfaces_cache["value"] is not None:
            return _stage_surfaces_cache["value"]
        try:
            from surface_analyzer import analyze_surfaces
            surfaces = analyze_surfaces(pc["points"]) or {}
            surfaces["source"] = "pointcloud"
            # Append user-placed structural objects (#605 pillar gap).
            extras = _objects_as_obstacles()
            if extras:
                surfaces.setdefault("obstacles", []).extend(extras)
            _stage_surfaces_cache = {"key": key, "value": surfaces}
            return surfaces
        except Exception as e:
            log.warning("surface_analyzer.analyze_surfaces failed: %s", e)

    # Fallback — synthesize a rectangular stage from the configured box.
    sw = int(_stage.get("w", 10) * 1000)
    sd = int(_stage.get("d", 10) * 1000)
    sh = int(_stage.get("h", 5) * 1000)
    synthetic = {
        "floor": {"z": 0, "extent": {"xMin": 0, "xMax": sw,
                                       "yMin": 0, "yMax": sd}},
        "walls": [
            {"normal": [0, 1, 0], "d": 0,       "label": "back"},
            {"normal": [0, -1, 0], "d": sd,     "label": "front"},
            {"normal": [1, 0, 0], "d": 0,       "label": "stage-left"},
            {"normal": [-1, 0, 0], "d": sw,     "label": "stage-right"},
        ],
        "obstacles": [],
        "stage": {"w": sw, "d": sd, "h": sh},
        "source": "layout-box",
    }
    # Still honour user-placed structural objects even when there is no
    # point cloud — the layout-box fallback is just the room shell.
    extras = _objects_as_obstacles()
    if extras:
        synthetic["obstacles"] = extras
    return synthetic


def _build_lite_point_cloud():
    """Synthesize a point cloud from layout dimensions + positioned
    fixtures/cameras — no depth scan, no camera pull (#577).

    Produces a grid of synthetic points on the floor plane (Z=0) and the
    back wall (Y=stage.d). Output shape matches `_space_scan._result`
    so downstream consumers (surface_analyzer, calibration target
    picker, IK ray-intersect) treat it identically to a real scan.

    The cloud is marked with source=\"lite\" so callers that care
    (the Setup tab status pill, the calibration wizard) can distinguish
    \"I have real geometry\" from \"I'm using surveyed layout dimensions\".
    """
    sw_m = float(_stage.get("w", 6))
    sd_m = float(_stage.get("d", 4))
    sh_m = float(_stage.get("h", 3))
    sw = int(sw_m * 1000)
    sd = int(sd_m * 1000)
    sh = int(sh_m * 1000)
    # ~250 mm grid spacing — dense enough for RANSAC to detect planes,
    # sparse enough that even a 20×20 m stage stays under 10k points.
    # Shape [x, y, z, r, g, b] in stage millimetres (same convention as
    # a real space scan) — the SPA renderer reads all six slots.
    step = 250
    points = []
    # Floor plane at Z=0 — cyan tint so the lite cloud is visually
    # distinct from a real colour-mapped scan.
    x = 0
    while x <= sw:
        y = 0
        while y <= sd:
            points.append([float(x), float(y), 0.0, 34, 211, 238])
            y += step
        x += step
    # Back wall at Y=sd — darker cyan.
    x = 0
    while x <= sw:
        z = 0
        while z <= sh:
            points.append([float(x), float(sd), float(z), 14, 116, 144])
            z += step
        x += step
    # Tag each positioned camera as a contributing camera so the Setup
    # pill (#578) can mark them "in cloud" even though no depth was
    # collected — the operator explicitly chose the lite path and the
    # camera's layout position is what's backing the cloud's walls.
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    positioned_cam_ids = [c["id"] for c in cams if c["id"] in pos_map]
    cam_info = [{"fixtureId": cid, "cameraIdx": 0,
                 "name": next((c.get("name", "") for c in cams if c["id"] == cid), ""),
                 "pointCount": 0, "lite": True}
                for cid in positioned_cam_ids]
    return {
        "schemaVersion": 1,
        "timestamp": time.time(),
        "cameras": cam_info,
        "points": points,
        "totalPoints": len(points),
        "floorNormalized": True,
        "floorOffset": 0,
        "source": "lite",
        "stageW": sw,
        "stageH": sh,
        "stageD": sd,
    }


# ── #592 ArUco-anchored scan helpers ──────────────────────────────────

def _aruco_snapshot_detect(f):
    """Fetch a snapshot from a camera fixture and run ArUco detection.

    Returns a dict `{frameSize, markers: [{id, corners[4][2], center[2]}], err?}`.
    Never raises — errors are returned in the dict so the caller can
    report per-camera failures without aborting the whole preview.
    Pure function over a fixture dict — no persistence, no frame buffer.
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        return {"err": "OpenCV not installed on orchestrator",
                "markers": [], "frameSize": None}
    if np is None:
        return {"err": "NumPy not installed on orchestrator",
                "markers": [], "frameSize": None}
    ip = f.get("cameraIp")
    if not ip:
        return {"err": "Camera has no IP", "markers": [], "frameSize": None}
    cam_idx = f.get("cameraIdx", 0)
    import urllib.request as _ur
    try:
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        jpeg = resp.read()
    except Exception as e:
        return {"err": f"Snapshot failed: {e}", "markers": [], "frameSize": None}
    import cv2
    frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return {"err": "JPEG decode failed", "markers": [], "frameSize": None}
    corners, ids, _rej, frame_size = _aruco_detect(frame)
    out = []
    if ids is not None and len(ids) > 0:
        for i, mid in enumerate(ids.flatten().tolist()):
            # corners[i] is shape (1, 4, 2) float32 — flatten to list of [x, y]
            pts = corners[i].reshape(4, 2).tolist()
            cx = sum(p[0] for p in pts) / 4.0
            cy = sum(p[1] for p in pts) / 4.0
            out.append({"id": int(mid),
                         "corners": [[float(p[0]), float(p[1])] for p in pts],
                         "center": [float(cx), float(cy)]})
    return {"markers": out, "frameSize": list(frame_size)}


def _aruco_multi_snapshot_detect(f, max_snapshots=3, blackout_bridge_ip=None):
    """#626 — multi-snapshot ArUco aggregation. Takes up to N snapshots and
    keeps the best per-id by corner perimeter (largest = closest to camera =
    best sub-pixel corners). Matches the same aggregation pattern that
    stage-map has used since #stage-map-flaky.

    If `blackout_bridge_ip` is provided, a 512-channel all-zero frame is
    pushed to that bridge before each snapshot so whatever moving head
    was lit from the previous step can't wash out the marker detection.
    The engine's regular 40 Hz tick may re-light the fixture within a few
    frames, but between those ticks there's a reliably dark window for
    the snapshot to land in.
    """
    best_per_id = {}
    frame_size = None
    last_err = None
    detected_total = 0
    for attempt in range(max(1, int(max_snapshots))):
        if blackout_bridge_ip:
            try:
                _mcal._hold_dmx(blackout_bridge_ip, [0] * 512, 0.15)
            except Exception:
                pass
        r = _aruco_snapshot_detect(f)
        if r.get("err"):
            last_err = r["err"]
            continue
        if frame_size is None and r.get("frameSize"):
            frame_size = r["frameSize"]
        detected_total += len(r.get("markers", []))
        for m in r.get("markers", []):
            mid = int(m.get("id"))
            corners = m.get("corners") or []
            if len(corners) != 4:
                continue
            perim = 0.0
            for i in range(4):
                x1, y1 = corners[i]
                x2, y2 = corners[(i + 1) % 4]
                perim += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            prior = best_per_id.get(mid)
            if prior is None or perim > prior.get("_perim", 0.0):
                m2 = dict(m)
                m2["_perim"] = float(perim)
                best_per_id[mid] = m2
    if not best_per_id and last_err:
        return {"err": last_err, "markers": [], "frameSize": frame_size}
    return {"frameSize": frame_size,
            "markers": list(best_per_id.values()),
            "detectedTotal": detected_total,
            "snapshotsTaken": max(1, int(max_snapshots))}


def _aruco_visibility_report(camera_ids=None):
    """Run `_aruco_snapshot_detect` across a set of camera fixtures and
    build a cross-camera visibility report.

    Returns `{cameras, shared, sharedIds, correspondences, registry}`
    where `shared` is the set of marker IDs seen by >=2 cameras AND
    registered in `_aruco_markers`, and `correspondences` is the number
    of (camera-a, camera-b, marker, corner) quadruples available for
    triangulation.
    """
    if camera_ids is None:
        cams = [f for f in _fixtures
                if f.get("fixtureType") == "camera" and f.get("cameraIp")]
    else:
        cams = [next((f for f in _fixtures
                      if f.get("id") == cid and f.get("fixtureType") == "camera"),
                     None)
                for cid in camera_ids]
        cams = [c for c in cams if c]
    per_cam = []
    all_seen = {}  # id → [cam_idx_in_per_cam]
    for f in cams:
        d = _aruco_snapshot_detect(f)
        per_cam.append({
            "id": f.get("id"),
            "name": f.get("name"),
            "cameraIp": f.get("cameraIp"),
            "cameraIdx": f.get("cameraIdx", 0),
            "frameSize": d.get("frameSize"),
            "markers": d.get("markers", []),
            "err": d.get("err"),
        })
        for m in d.get("markers", []):
            all_seen.setdefault(m["id"], []).append(len(per_cam) - 1)
    registered_ids = {int(m.get("id")) for m in _aruco_markers}
    # A marker is "shared-anchored" only if it's visible to >=2 cameras
    # AND present in the surveyed registry — unregistered visible markers
    # can't be used for anchoring because we don't know their stage pos.
    shared_ids = sorted(mid for mid, cams in all_seen.items()
                         if len(cams) >= 2 and mid in registered_ids)
    # Correspondences = 4 corners per shared marker per distinct camera
    # pair that both see it. For N cameras seeing a marker, that's
    # C(N, 2) * 4 pairs.
    correspondences = 0
    for mid in shared_ids:
        n = len(all_seen[mid])
        correspondences += (n * (n - 1) // 2) * 4
    return {
        "cameras": per_cam,
        "shared": shared_ids,
        "sharedIds": shared_ids,
        "correspondences": correspondences,
        "registry": list(_aruco_markers),
    }


def _marker_stage_corners(marker):
    """Return the 4 stage-frame 3D corners for a surveyed ArUco marker
    in the order OpenCV's detector outputs them (TL, TR, BR, BL viewed
    from in front of the marker face).

    Marker-local frame: +X right, +Y down, +Z face normal out.
      TL = (-s/2, -s/2, 0)
      TR = (+s/2, -s/2, 0)
      BR = (+s/2, +s/2, 0)
      BL = (-s/2, +s/2, 0)

    Surveyed rotation is the XYZ-intrinsic Euler triple (rx, ry, rz) in
    degrees; applied as R = Rz · Ry · Rx so the standard "marker lying
    flat on the floor face-up" case uses rx=ry=rz=0. Translated by the
    marker center (x, y, z) in stage mm.
    """
    if np is None:
        raise RuntimeError("NumPy unavailable")
    s = float(marker.get("size", 100)) / 2.0
    local = np.array([
        [-s, -s, 0.0],
        [+s, -s, 0.0],
        [+s, +s, 0.0],
        [-s, +s, 0.0],
    ], dtype=np.float64)
    rx = math.radians(float(marker.get("rx", 0) or 0))
    ry = math.radians(float(marker.get("ry", 0) or 0))
    rz = math.radians(float(marker.get("rz", 0) or 0))
    cxa, sxa = math.cos(rx), math.sin(rx)
    cya, sya = math.cos(ry), math.sin(ry)
    cza, sza = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cxa, -sxa], [0, sxa, cxa]], dtype=np.float64)
    Ry = np.array([[cya, 0, sya], [0, 1, 0], [-sya, 0, cya]], dtype=np.float64)
    Rz = np.array([[cza, -sza, 0], [sza, cza, 0], [0, 0, 1]], dtype=np.float64)
    R = Rz @ Ry @ Rx
    center = np.array([marker.get("x", 0), marker.get("y", 0), marker.get("z", 0)],
                       dtype=np.float64)
    corners = (R @ local.T).T + center
    return corners  # shape (4, 3)


def _aruco_anchor_extrinsics(frame_w, frame_h, fov_deg, fov_type,
                              detected_by_id, registered_by_id):
    """Run cv2.solvePnP on detected 2D corners vs surveyed 3D corners to
    compute the camera's stage-frame extrinsics (#592 Phase 2).

    Args:
        frame_w, frame_h: actual captured resolution (V4L2 may downscale
            silently — always trust the decoded dims, not the request).
        fov_deg, fov_type: FOV for the intrinsic K — same convention as
            StereoEngine.add_camera_from_fov (`horizontal`, `diagonal`,
            `vertical`).
        detected_by_id: dict {marker_id: [[x, y], x4]} of pixel corners
            returned by `_aruco_detect` on this camera's frame.
        registered_by_id: dict {marker_id: registry_record} of surveyed
            markers the orchestrator knows the stage position of.

    Returns:
        dict with {K, rvec, tvec, reprojectionRmsPx, cornerCount} on
        success; {err, cornerCount} on failure. `cornerCount` is the
        number of (marker, corner) pairs used in the solve — need ≥4
        distinct-plane correspondences for a unique solution.
    """
    if np is None:
        return {"err": "NumPy unavailable", "cornerCount": 0}
    try:
        import cv2  # noqa: F401
    except ImportError:
        return {"err": "OpenCV unavailable", "cornerCount": 0}
    import cv2

    if fov_type == "diagonal":
        diag = math.sqrt(frame_w * frame_w + frame_h * frame_h)
        h_fov = 2.0 * math.atan(math.tan(math.radians(fov_deg) / 2.0) * (frame_w / diag))
    elif fov_type == "vertical":
        h_fov = 2.0 * math.atan(math.tan(math.radians(fov_deg) / 2.0) * (frame_w / frame_h))
    else:
        h_fov = math.radians(fov_deg)
    fx = (frame_w / 2.0) / math.tan(h_fov / 2.0)
    fy = fx
    cx, cy = frame_w / 2.0, frame_h / 2.0
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)

    obj_pts = []
    img_pts = []
    used_marker_ids = []
    for mid, pix_corners in detected_by_id.items():
        reg = registered_by_id.get(int(mid))
        if not reg or not pix_corners or len(pix_corners) < 4:
            continue
        stage_corners = _marker_stage_corners(reg)
        for i in range(4):
            obj_pts.append(stage_corners[i])
            img_pts.append([float(pix_corners[i][0]), float(pix_corners[i][1])])
        used_marker_ids.append(int(mid))
    if len(obj_pts) < 4:
        return {"err": f"need ≥4 surveyed corners, got {len(obj_pts)}",
                "cornerCount": len(obj_pts)}

    obj = np.array(obj_pts, dtype=np.float64)
    img = np.array(img_pts, dtype=np.float64)
    dist = np.zeros(5, dtype=np.float64)
    # SOLVEPNP_SQPNP is the default robust planar/non-planar solver in
    # modern OpenCV — but it requires ≥4 points and can be brittle on
    # exactly 4 coplanar corners from a single marker. Fall through to
    # the iterative solver when SQPNP rejects or is unavailable.
    ok = False
    rvec = tvec = None
    try:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist,
                                       flags=getattr(cv2, "SOLVEPNP_SQPNP",
                                                      cv2.SOLVEPNP_ITERATIVE))
    except Exception:
        ok = False
    if not ok or rvec is None:
        try:
            ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist,
                                           flags=cv2.SOLVEPNP_ITERATIVE)
        except Exception as e:
            return {"err": f"solvePnP raised: {e}", "cornerCount": len(obj_pts)}
        if not ok:
            return {"err": "solvePnP failed to converge",
                    "cornerCount": len(obj_pts)}

    # Reprojection RMS for operator feedback. Lower = tighter anchor.
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    proj = proj.reshape(-1, 2)
    diff = proj - img
    rms_px = float(math.sqrt(float(np.mean(np.sum(diff * diff, axis=1)))))
    return {
        "K": K, "rvec": rvec, "tvec": tvec,
        "reprojectionRmsPx": round(rms_px, 2),
        "cornerCount": len(obj_pts),
        "markerIds": used_marker_ids,
    }


def _apply_marker_z_alignment(cloud, radius_mm=400, min_pts=3):
    """#599 — shift a point cloud's Z so surveyed floor markers sit at z=0.

    Monocular depth models (ZoeDepth, MiDaS, mono-fallback) place the
    floor wherever their training set's prior puts it — on the basement
    rig that's a consistent ~250 mm above reality. The surveyed ArUco
    registry gives us the ground truth: every floor-level marker is by
    construction at z=0. For each such marker, gather the cloud points
    within `radius_mm` of its XY position, take their median Z, average
    across markers, and subtract the result from every point's Z.

    Robustness:
    - Only marker records where `|z| < 50mm` AND `rx == ry == rz == 0`
      count as "floor" (wall-mounted markers skipped).
    - Median (not mean) per marker and across markers — one noisy
      marker can't drag the whole correction.
    - If no marker has ≥ `min_pts` nearby cloud points, returns without
      modifying the cloud and flags `used=False`.
    - Diagnostic payload returned so the SPA / tests can show which
      markers contributed.

    Returns a diagnostics dict; mutates `cloud["points"]` in place.
    """
    if not cloud or not cloud.get("points"):
        return {"applied": False, "reason": "no points"}
    floor = [m for m in _aruco_markers
             if abs(float(m.get("z", 0) or 0)) < 50
             and abs(float(m.get("rx", 0) or 0)) < 1
             and abs(float(m.get("ry", 0) or 0)) < 1
             and abs(float(m.get("rz", 0) or 0)) < 1]
    if not floor:
        return {"applied": False, "reason": "no floor-level markers in registry"}
    import statistics
    pts = cloud["points"]
    per_marker = []
    offsets = []
    for m in floor:
        mx, my = float(m["x"]), float(m["y"])
        zs = [p[2] for p in pts
              if abs(p[0] - mx) < radius_mm and abs(p[1] - my) < radius_mm]
        entry = {"id": int(m["id"]), "xy": [mx, my], "nearbyPoints": len(zs)}
        if len(zs) >= min_pts:
            mz = statistics.median(zs)
            entry["medianZ"] = round(mz, 1)
            entry["used"] = True
            offsets.append(mz)
        else:
            entry["used"] = False
        per_marker.append(entry)
    if not offsets:
        return {"applied": False, "reason": f"no marker had ≥{min_pts} nearby points",
                "markers": per_marker}
    offset_z = statistics.median(offsets)
    for p in pts:
        p[2] -= offset_z
    cloud["zOffsetAppliedMm"] = round(
        (cloud.get("zOffsetAppliedMm") or 0.0) + offset_z, 2)
    log.info("marker-Z alignment: offset=%.1f mm across %d markers "
             "(offsets=%s)",
             offset_z, len(offsets), [round(o, 1) for o in offsets])
    return {"applied": True, "zOffsetMm": round(offset_z, 1),
            "markers": per_marker, "markersUsed": len(offsets)}


@app.post("/api/space/align-to-markers")
def api_space_align_to_markers():
    """Apply a Z-offset correction to the current point cloud using
    surveyed floor-level ArUco markers. Operator-triggered version of
    the auto-alignment that runs at the end of mono/ZoeDepth scans
    (#599). Idempotent-ish: each call re-measures the current cloud
    against the registry and shifts it toward z=0 again, so repeated
    calls converge to zero offset.
    """
    global _point_cloud, _stage_surfaces_cache
    body = request.get_json(silent=True) or {}
    radius = int(body.get("radiusMm", 400))
    min_pts = int(body.get("minPts", 3))
    if not _point_cloud or not _point_cloud.get("points"):
        return jsonify(ok=False, err="no point cloud loaded"), 400
    result = _apply_marker_z_alignment(_point_cloud, radius_mm=radius,
                                         min_pts=min_pts)
    if result.get("applied"):
        _save("pointcloud", _point_cloud)
        _stage_surfaces_cache = {"key": None, "value": None}
    return jsonify(ok=True, **result, totalPoints=len(_point_cloud["points"]))


@app.post("/api/space/scan/aruco-simple")
def api_space_scan_aruco_simple():
    """#592 — Build a minimal marker-anchored point cloud using only the
    ArUco markers currently visible to >=2 cameras AND registered in the
    surveyed registry.

    For each shared marker, every camera pair that both see it
    triangulates the four corners via `StereoEngine.triangulate_pair`
    with cameras registered via `add_camera_from_fov` (works without a
    full intrinsic/extrinsic calibration — relies on the fixture's
    fovDeg / stage position / rotation). Multiple pairs for the same
    marker are averaged per corner; results are tagged with the marker
    ID so the SPA can show per-marker residuals.

    This endpoint does NOT run ORB matching or consume textureless
    regions. It produces a tiny cloud (4 × len(sharedIds) points when
    every pair converges) but the points are ground-truth-anchored, so
    the delta vs surveyed position gives an immediate calibration-
    quality number without the full stereo wizard. Subsequent work
    (#592 Phase 2) will feed these into a pose/scale correction before
    the main stereo path runs.

    Body: `{cameras: [fid, ...]}` (optional subset).

    Response:
        {
          ok: true,
          source: "aruco-markers",
          sharedIds: [...],
          triangulated: [
            {id, surveyed: [x,y,z], triangulatedCenter: [x,y,z],
             deltaMm: float, cornerPoints: [[x,y,z,r,g,b,conf], ...4]}
          ],
          totalPoints: int,
          cameras: [...],
          elapsedS: float
        }

    Persists as the active point cloud with source="aruco-markers".
    """
    try:
        from stereo_engine import StereoEngine
    except ImportError:
        return jsonify(ok=False, err="stereo_engine module missing"), 500
    t0 = time.time()
    body = request.get_json(silent=True) or {}
    cam_ids = body.get("cameras")
    report = _aruco_visibility_report(cam_ids)
    shared = report["sharedIds"]
    if not _aruco_markers:
        return jsonify(ok=False, err="No surveyed ArUco markers in the registry — "
                                      "add at least one in Setup → ArUco before scanning"), 400
    if not shared:
        return jsonify(ok=False,
                       err="No surveyed markers are visible to ≥2 cameras — "
                           "move the cameras or re-seat the markers so they overlap",
                       cameras=report["cameras"]), 400

    # Build a StereoEngine with every participating camera using the
    # FOV fallback. Cameras already have stage position + rotation from
    # layout, and the prescan reported frameSize — enough to stand up a
    # reasonable intrinsic/extrinsic without a full ArUco wizard run.
    engine = StereoEngine()
    registered = {}  # fid → per_cam entry with markers detected
    for c in report["cameras"]:
        if c.get("err") or not c.get("markers") or not c.get("frameSize"):
            continue
        fid = c["id"]
        f = next((x for x in _fixtures if x.get("id") == fid), None)
        if not f:
            continue
        pos = _fixture_position(fid)
        if all(abs(v) < 1e-6 for v in pos):
            log.warning("aruco-simple: camera fid=%d has no stage position — skipping", fid)
            continue
        fov = f.get("fovDeg", 90)
        fov_type = _normalise_fov_type(f.get("fovType"))
        frame_w, frame_h = c["frameSize"][0], c["frameSize"][1]
        rotation = f.get("rotation", [0, 0, 0])
        try:
            engine.add_camera_from_fov(
                fid, fov, int(frame_w), int(frame_h),
                list(pos), stage_rotation=rotation, fov_type=fov_type,
            )
            registered[fid] = c
        except Exception as e:
            log.warning("aruco-simple: add_camera_from_fov failed for fid=%d: %s", fid, e)

    if len(registered) < 2:
        return jsonify(ok=False,
                       err=f"Need ≥2 calibratable cameras; got {len(registered)}",
                       cameras=report["cameras"]), 400

    # For each shared marker, collect (fid → corners) from prescan, then
    # triangulate every pair of cameras that sees it. Corner ordering
    # matters — ArUco gives us the same 4-corner order across cameras,
    # so corner[i] in cam-A pairs with corner[i] in cam-B.
    marker_to_corners = {}  # mid → {fid: [(x,y), x4]}
    for c in registered.values():
        for m in c.get("markers", []):
            if m["id"] in shared:
                marker_to_corners.setdefault(m["id"], {})[c["id"]] = m["corners"]

    reg_by_id = {int(m.get("id")): m for m in _aruco_markers}
    triangulated_out = []
    all_points = []
    for mid in shared:
        cam_corners = marker_to_corners.get(mid, {})
        if len(cam_corners) < 2:
            continue
        cam_ids_for_marker = list(cam_corners.keys())
        # Average-per-corner across all pairs that converge.
        corner_accums = [[] for _ in range(4)]
        for i in range(len(cam_ids_for_marker)):
            for j in range(i + 1, len(cam_ids_for_marker)):
                cid_a, cid_b = cam_ids_for_marker[i], cam_ids_for_marker[j]
                pts_a = cam_corners[cid_a]
                pts_b = cam_corners[cid_b]
                matches = []
                for k in range(4):
                    matches.append((pts_a[k][0], pts_a[k][1],
                                     pts_b[k][0], pts_b[k][1],
                                     180, 255, 180))  # green-ish for ArUco
                pts = engine.triangulate_pair(cid_a, cid_b, matches,
                                                max_reproject_err_mm=500.0)
                for k, p in enumerate(pts[:4]):
                    corner_accums[k].append(p)
        # Reduce per-corner accums to a single 7-tuple.
        corner_points = []
        for acc in corner_accums:
            if not acc:
                continue
            xs = sum(p[0] for p in acc) / len(acc)
            ys = sum(p[1] for p in acc) / len(acc)
            zs = sum(p[2] for p in acc) / len(acc)
            conf = sum(p[6] for p in acc) / len(acc)
            corner_points.append([xs, ys, zs, 180, 255, 180, conf])
        if not corner_points:
            continue
        cx = sum(p[0] for p in corner_points) / len(corner_points)
        cy = sum(p[1] for p in corner_points) / len(corner_points)
        cz = sum(p[2] for p in corner_points) / len(corner_points)
        surveyed = reg_by_id.get(int(mid), {})
        sx, sy, sz = surveyed.get("x", 0), surveyed.get("y", 0), surveyed.get("z", 0)
        delta = math.sqrt((cx - sx) ** 2 + (cy - sy) ** 2 + (cz - sz) ** 2)
        triangulated_out.append({
            "id": int(mid),
            "surveyed": [sx, sy, sz],
            "triangulatedCenter": [cx, cy, cz],
            "deltaMm": round(delta, 1),
            "cornerPoints": corner_points,
        })
        all_points.extend(corner_points)

    if not all_points:
        return jsonify(ok=False,
                       err="All shared markers failed triangulation (reprojection err > 500 mm). "
                           "Check camera position / FOV / rotation in the layout.",
                       cameras=report["cameras"],
                       sharedIds=shared), 502

    elapsed = time.time() - t0
    global _point_cloud, _stage_surfaces_cache
    _point_cloud = {
        "schemaVersion": 2,
        "timestamp": time.time(),
        "source": "aruco-markers",
        "cameras": [{"id": c["id"], "name": c["name"],
                     "pointCount": sum(1 for t in triangulated_out
                                        if c["id"] in marker_to_corners.get(t["id"], {}))
                     * 4}
                    for c in registered.values()],
        "points": all_points,
        "totalPoints": len(all_points),
        "stageW": int(_stage.get("w", 3) * 1000),
        "stageH": int(_stage.get("h", 2) * 1000),
        "stageD": int(_stage.get("d", 4) * 1000),
        "elapsedS": round(elapsed, 2),
        "arucoTriangulated": triangulated_out,
    }
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("ArUco-simple scan: %d shared markers → %d points in %.2fs",
             len(triangulated_out), len(all_points), elapsed)
    return jsonify(ok=True, source="aruco-markers",
                   sharedIds=shared,
                   triangulated=triangulated_out,
                   totalPoints=len(all_points),
                   cameras=report["cameras"],
                   elapsedS=round(elapsed, 2))


@app.post("/api/space/scan/aruco-preview")
def api_space_scan_aruco_preview():
    """#592 Pre-scan ArUco visibility report. Snapshots every registered
    camera (or a supplied subset), runs ArUco detection, and returns a
    per-camera marker list plus the set of marker IDs visible to >=2
    cameras AND surveyed in the registry.

    Body: `{cameras: [fid, ...]}` (optional — defaults to every camera
    fixture with a cameraIp).

    Response:
        {
          ok: true,
          cameras: [
            {id, name, cameraIp, cameraIdx, frameSize, markers: [...], err?}
          ],
          shared: [markerId, ...],       // visible-to-2+ AND registered
          correspondences: int,           // pair-corner count
          registry: [...]                 // surveyed markers snapshot
        }

    Never persists. Safe to poll. Typical latency is
    `len(cameras) * (snapshot_rtt + aruco_detect_ms)`.
    """
    body = request.get_json(silent=True) or {}
    cam_ids = body.get("cameras")
    report = _aruco_visibility_report(cam_ids)
    report["ok"] = True
    return jsonify(report)


@app.post("/api/space/scan/lite")
def api_space_scan_lite():
    """Synthesize a point cloud from layout dimensions (#577).

    Zero-scan first-pass geometry for the calibration wizard — lets new
    users calibrate on day one before any camera scan has succeeded. A
    subsequent real scan (`/api/space/scan`) overwrites this with
    actual depth data.
    """
    global _point_cloud, _stage_surfaces_cache
    _point_cloud = _build_lite_point_cloud()
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("Lite point cloud synthesized: %d points, %d cameras tagged",
             _point_cloud["totalPoints"], len(_point_cloud["cameras"]))
    return jsonify(ok=True, source="lite",
                   totalPoints=_point_cloud["totalPoints"],
                   cameras=len(_point_cloud["cameras"]))


# #598 — ZoeDepth runs in a separate venv/subprocess now. See
# desktop/shared/depth_runtime.py. Nothing in this file imports torch
# or transformers; the main PyInstaller bundle stays small.

try:
    import depth_runtime as _depth_runtime
except Exception as _e_dr:  # pragma: no cover — only fails in broken bundles
    _depth_runtime = None
    log.warning("depth_runtime unavailable: %s", _e_dr)


@app.get("/api/space/scan/zoedepth")
def api_space_scan_zoedepth_info():
    """#594/#598 UI — report whether the out-of-process ZoeDepth
    runtime is installed so the Advanced Scan card can offer the
    option and, when missing, show an 'Install now' button instead
    of the old 'run orchestrator from source' message."""
    if _depth_runtime is None:
        return jsonify(ok=True, available=False, installable=False,
                       reason="depth_runtime module not bundled")
    installed = _depth_runtime.is_installed()
    return jsonify(
        ok=True,
        available=installed,
        installable=not installed,
        loaded=_depth_runtime._runner_is_healthy(),
        status=_depth_runtime.status(),
    )


def _check_depth_install_marker():
    """#598 — if the Windows installer was run with the depth component
    ticked, a marker file `depth.install-requested` is dropped next to
    SlyLED.exe. Kick off the install in the background so the user sees
    the progress bar through the normal Settings → Depth Runtime UI
    instead of a blocking installer console."""
    if _depth_runtime is None:
        return
    try:
        if getattr(sys, "frozen", False):
            install_dir = os.path.dirname(sys.executable)
        else:
            return   # dev mode — no Windows installer in play
        marker = os.path.join(install_dir, "depth.install-requested")
        if not os.path.exists(marker):
            return
        try:
            os.remove(marker)
        except OSError:
            pass
        if _depth_runtime.is_installed():
            return
        log.info("depth.install-requested marker present — kicking off background install")
        _depth_runtime.start_install()
    except Exception as e:
        log.warning("depth install-marker check failed: %s", e)


@app.get("/api/depth-runtime/status")
def api_depth_runtime_status():
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    return jsonify(ok=True, **_depth_runtime.status())


@app.post("/api/depth-runtime/install")
def api_depth_runtime_install():
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))
    res = _depth_runtime.start_install(force=force)
    code = 200 if res.get("ok") else 409
    return jsonify(**res), code


@app.get("/api/depth-runtime/install-status")
def api_depth_runtime_install_status():
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    # Return the progress dict directly — it has its own `ok` field
    # (None while running, True/False when finished) that the SPA
    # polling loop reads, and merging with an outer ok=True would
    # collide.
    return jsonify(_depth_runtime.install_progress())


@app.delete("/api/depth-runtime")
def api_depth_runtime_uninstall():
    """Remove the runtime. Pass ?includeWeights=1 to also wipe the
    1.3 GB model cache (default: preserve weights so a subsequent
    Reinstall is fast)."""
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    inc = request.args.get("includeWeights", "0") in ("1", "true", "yes")
    return jsonify(**_depth_runtime.uninstall(include_weights=inc))


@app.post("/api/depth-runtime/install/cancel")
def api_depth_runtime_install_cancel():
    """Abort an in-progress install. The next Reinstall wipes any
    partial venv and starts fresh."""
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    return jsonify(**_depth_runtime.cancel_install())


@app.post("/api/depth-runtime/verify")
def api_depth_runtime_verify():
    """Lightweight check of the currently-installed runtime. Runs
    pip check + the ZoeDepth import probe without reinstalling or
    spawning the runner. Used by the Check Install button — fast
    (a couple seconds) and doesn't touch weights."""
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    return jsonify(**_depth_runtime.verify())


@app.post("/api/depth-runtime/test")
def api_depth_runtime_test():
    """Validate + warm-up probe for the depth runtime. Spawns the
    runner subprocess if it's not already live, pushes a tiny
    synthetic JPEG through /infer, returns timing + depth stats so
    the Settings card can report "working · warm" vs the actual
    error. Follow-up calibration scans skip the cold-start penalty
    because the runner stays resident for 5 min after this probe.
    """
    if _depth_runtime is None:
        return jsonify(ok=False, err="depth_runtime module not bundled"), 500
    if not _depth_runtime.is_installed():
        return jsonify(ok=False, err="runtime not installed"), 409

    import io
    import time as _t
    try:
        import numpy as _np
        from PIL import Image as _I
    except Exception as e:
        return jsonify(ok=False, err=f"numpy/Pillow missing in orchestrator: {e}"), 500

    # 256x256 synthetic gradient — gives the model something non-trivial
    # without the overhead of pulling a real camera snapshot.
    h, w = 256, 256
    grid_y = _np.linspace(0, 255, h, dtype=_np.uint8)[:, None]
    grid_x = _np.linspace(0, 255, w, dtype=_np.uint8)[None, :]
    rgb = _np.stack([
        _np.broadcast_to(grid_y, (h, w)),
        _np.broadcast_to(grid_x, (h, w)),
        _np.full((h, w), 128, dtype=_np.uint8),
    ], axis=-1)
    buf = io.BytesIO()
    _I.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=80)
    jpg = buf.getvalue()

    t0 = _t.time()
    try:
        depth_mm, inf_ms = _depth_runtime.infer_jpeg(jpg, timeout_s=120.0)
    except Exception as e:
        return jsonify(ok=False, err=str(e),
                       runnerPort=_depth_runtime._runner_port()), 502

    total_ms = int((_t.time() - t0) * 1000)
    d_min = float(depth_mm.min())
    d_max = float(depth_mm.max())
    d_mean = float(depth_mm.mean())
    sane = (depth_mm.shape == (h, w)
            and not _np.isnan(depth_mm).any()
            and d_min >= 0 and d_max > d_min)
    return jsonify(
        ok=bool(sane),
        shape=list(depth_mm.shape),
        inferenceMs=inf_ms,
        totalMs=total_ms,
        depthMinMm=round(d_min, 1),
        depthMaxMm=round(d_max, 1),
        depthMeanMm=round(d_mean, 1),
        runnerPort=_depth_runtime._runner_port(),
    )


@app.post("/api/space/scan/zoedepth")
def api_space_scan_zoedepth():
    """Host-side high-quality monocular depth scan via ZoeDepth (#593).

    Pulls a raw snapshot from each selected camera, runs ZoeDepth on
    the orchestrator host (CPU or GPU), back-projects to cam-local 3D
    via the pinhole model, transforms through known camera poses to
    stage coords, merges with cross-cam filter.

    Body: {
      cameras: [fid1, fid2, ...]  — optional; defaults to all positioned
      lighting: \"blackout\" (default) | \"keep\" | \"fill\"
      maxPoints: int per camera, default 5000
    }
    """
    import urllib.request
    global _point_cloud, _stage_surfaces_cache

    body = request.get_json(silent=True) or {}
    sel = body.get("cameras")
    lighting_mode = body.get("lighting", "blackout")
    max_pts = int(body.get("maxPoints", 5000))

    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    positioned = [c for c in cams if c["id"] in pos_map and c.get("cameraIp")]
    if sel:
        ids = set(int(x) for x in sel)
        positioned = [c for c in positioned if c["id"] in ids]
    if not positioned:
        return jsonify(err="No positioned cameras selected"), 400

    if _depth_runtime is None or not _depth_runtime.is_installed():
        return jsonify(
            err="ZoeDepth runtime is not installed",
            detail="Install it from Settings → Depth runtime or from "
                   "the 'Install now' button in the Advanced Scan card."
        ), 501

    import math as _math
    import io
    try:
        import numpy as _np
        from PIL import Image
    except Exception as e:
        return jsonify(err=f"numpy/Pillow missing in orchestrator: {e}"), 500

    from camera_math import build_camera_to_stage
    from stereo_consistency import cross_camera_filter

    # Snapshot each camera (inside blackout window)
    per_cam_clouds = []
    cam_info_list = []
    t_scan = time.time()
    with _ScanLightingWindow(lighting_mode):
        for cam in positioned:
            pos = pos_map[cam["id"]]
            cam_pos = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
            rot = cam.get("rotation", [0, 0, 0])
            fov = cam.get("fovDeg", 90)
            # Pull snapshot
            try:
                url = f"http://{cam['cameraIp']}:5000/snapshot?cam={cam.get('cameraIdx', 0)}"
                jpg_bytes = urllib.request.urlopen(url, timeout=15).read()
            except Exception as e:
                log.warning("snapshot failed for %s: %s", cam.get("name"), e)
                continue
            img = Image.open(io.BytesIO(jpg_bytes)).convert("RGB")
            # ZoeDepth inference — out-of-process (#598). Returns a
            # float32 depth map already in millimetres.
            t0 = time.time()
            try:
                depth_mm, inf_ms = _depth_runtime.infer_jpeg(jpg_bytes)
            except Exception as e:
                log.warning("ZoeDepth subprocess failed for %s: %s", cam.get("name"), e)
                return jsonify(err=f"ZoeDepth runtime error: {e}"), 502
            t1 = time.time()
            # Resize depth to image dims if the runner used a different
            # input size (defensive — current runner returns full-res)
            if depth_mm.shape[::-1] != img.size:
                from PIL import Image as _I
                depth_mm = _np.array(
                    _I.fromarray(depth_mm).resize(img.size, _I.BICUBIC),
                    dtype=_np.float32,
                )
            log.info("ZoeDepth %s: inference %.1fs (runner %dms), depth %.0f..%.0f mm",
                     cam.get("name"), t1 - t0, inf_ms, depth_mm.min(), depth_mm.max())
            # Back-project
            h, w = depth_mm.shape
            fx = (w / 2.0) / _math.tan(_math.radians(fov / 2))
            fy = fx
            cx, cy = w / 2.0, h / 2.0
            step = max(1, int(_math.sqrt(h * w / max_pts)))
            cam_local = []
            rgb = _np.array(img)
            for py in range(0, h, step):
                for px in range(0, w, step):
                    z = float(depth_mm[py, px])
                    if z < 50 or z > 10000:
                        continue
                    x = (px - cx) * z / fx
                    y = (py - cy) * z / fy
                    r, g, b = int(rgb[py, px, 0]), int(rgb[py, px, 1]), int(rgb[py, px, 2])
                    cam_local.append([x, y, z, r, g, b])
            # Transform to stage coords via the canonical helper
            R = _np.array(build_camera_to_stage(rot[0], rot[1], rot[2]))
            stage_pts = []
            for p in cam_local:
                local = _np.array([p[0], p[1], p[2]])
                stage = R @ local + _np.array(cam_pos)
                stage_pts.append([float(stage[0]), float(stage[1]), float(stage[2]),
                                  p[3], p[4], p[5]])
            per_cam_clouds.append({
                "fixture": cam,
                "stage_pos": cam_pos,
                "fov_deg": fov,
                "points": stage_pts,
                "anchorQuality": "ok",  # ZoeDepth is already metric
            })
            cam_info_list.append({
                "fixtureId": cam["id"],
                "cameraIdx": cam.get("cameraIdx", 0),
                "name": cam.get("name"),
                "pointCount": len(stage_pts),
                "inferenceS": round(t1 - t0, 2),
                "anchorQuality": "ok",
            })

    total_t = time.time() - t_scan
    if not per_cam_clouds:
        return jsonify(err="No cameras returned usable frames"), 502

    # Cross-camera filter (same as the monocular scan)
    if len(per_cam_clouds) >= 2:
        merged, filter_stats = cross_camera_filter(per_cam_clouds)
    else:
        merged = per_cam_clouds[0]["points"]
        filter_stats = None

    _point_cloud = {
        "schemaVersion": 2,
        "timestamp": time.time(),
        "source": "zoedepth",
        "cameras": cam_info_list,
        "filterStats": filter_stats,
        "points": merged,
        "totalPoints": len(merged),
        "stageW": int(_stage.get("w", 3) * 1000),
        "stageH": int(_stage.get("h", 2) * 1000),
        "stageD": int(_stage.get("d", 4) * 1000),
        "elapsedS": round(total_t, 2),
    }
    # #599 — auto-align Z to the surveyed floor markers when any are
    # registered. ZoeDepth's monocular scale-prior routinely plants the
    # floor at 200-400 mm above truth on the basement rig; the surveyed
    # ArUco registry is the ground-truth anchor, and this step shifts
    # the cloud so the floor sits at z=0 per the markers.
    align = _apply_marker_z_alignment(_point_cloud)
    if align.get("applied"):
        _point_cloud["markerAlignment"] = align
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("ZoeDepth scan complete: %d points from %d cameras in %.1fs%s",
             len(merged), len(per_cam_clouds), total_t,
             f" (Z-aligned {align['zOffsetMm']}mm)" if align.get("applied") else "")
    return jsonify(ok=True, source="zoedepth",
                   totalPoints=len(merged),
                   cameras=cam_info_list,
                   elapsedS=round(total_t, 2),
                   markerAlignment=align)


@app.post("/api/space/scan/stereo")
def api_space_scan_stereo():
    """Run a stereo-triangulation scan on a pair of cameras that share
    an Orange Pi (#583). Unlike the monocular `/api/space/scan`, this
    pulls two synchronised frames via `/stereo-capture`, runs ORB
    feature matching, triangulates via the shared StereoEngine, and
    returns a stage-frame point cloud with per-point confidence.

    Body: { "cameras": [fixture_id_a, fixture_id_b] } — both cameras
    must be registered and positioned. They should share the same
    cameraIp for the synchronised capture to work.
    """
    body = request.get_json(silent=True) or {}
    ids = body.get("cameras", [])
    if len(ids) != 2:
        return jsonify(err="body must include cameras=[fid_a, fid_b]"), 400
    cams = [next((f for f in _fixtures if f.get("id") == cid
                   and f.get("fixtureType") == "camera"), None) for cid in ids]
    if any(c is None for c in cams):
        return jsonify(err="one or both camera fixtures not found"), 404
    cam_a, cam_b = cams

    # Same-hardware guard — stereo only runs when both camera sensors
    # are on the same Orange Pi (i.e. share cameraIp), because only
    # then can firmware grab both frames in one V4L2 round-trip with
    # sub-10 ms sync. Cross-Pi stereo over the network drifts 30-100 ms
    # which makes triangulation wrong for anything moving.
    ip_a = cam_a.get("cameraIp")
    ip_b = cam_b.get("cameraIp")
    if not ip_a or not ip_b or ip_a != ip_b:
        return jsonify(
            err="stereo requires both cameras on the same node (same cameraIp)",
            detail=f"cam_a={ip_a}  cam_b={ip_b}"), 400
    # Must be two DIFFERENT sensor indices on that node.
    if cam_a.get("cameraIdx") == cam_b.get("cameraIdx"):
        return jsonify(
            err="stereo requires two different sensor indices on the node",
            detail=f"both cameras map to cameraIdx={cam_a.get('cameraIdx')}"), 400

    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    for c in (cam_a, cam_b):
        if c.get("id") not in pos_map:
            return jsonify(err=f"camera {c.get('name')} not positioned on layout"), 400

    # Tilt-alignment advisory (not a blocker). Classical stereo assumes
    # the two image planes are close to parallel — large tilt deltas
    # make rectification warp severely and ORB feature-descriptor
    # matching falls off a cliff beyond ~10° difference.
    rot_a = cam_a.get("rotation") or [0, 0, 0]
    rot_b = cam_b.get("rotation") or [0, 0, 0]
    tilt_delta = abs((rot_a[0] if len(rot_a) > 0 else 0) -
                     (rot_b[0] if len(rot_b) > 0 else 0))
    pan_delta = abs((rot_a[1] if len(rot_a) > 1 else 0) -
                     (rot_b[1] if len(rot_b) > 1 else 0))
    tilt_warning = None
    if tilt_delta > 10:
        tilt_warning = (f"Large tilt delta ({tilt_delta:.0f}°) between the two cameras — "
                        "classical stereo works best when tilts are within ~5°. "
                        "Expect a low feature-match yield.")
    elif tilt_delta > 5:
        tilt_warning = (f"Moderate tilt delta ({tilt_delta:.0f}°) — triangulation will "
                        "work but match counts will be reduced.")
    if pan_delta > 15:
        tilt_warning = ((tilt_warning or "") +
                        f" Pan delta ({pan_delta:.0f}°) is also large; cameras may "
                        "cover different stage regions with little overlap.")
    if tilt_warning:
        log.warning("Stereo scan: %s", tilt_warning)

    import base64, io
    import urllib.request  # not imported at module scope; local import keeps handler self-contained
    try:
        import cv2
        import numpy as _np
    except ImportError:
        return jsonify(err="cv2 / numpy not available on host"), 500

    # Pull paired frames. Request the highest resolution both cameras
    # can reasonably deliver — 1920×1080 is the firmware's per-cam cap
    # so the HTTP round-trip stays under ~1 MB per frame. Callers can
    # override via the request body.
    req_res = body.get("resolution", [1920, 1080])
    body_payload = {
        "pair": [cam_a.get("cameraIdx", 0), cam_b.get("cameraIdx", 1)],
        "resolution": req_res,
        "quality": body.get("quality", 85),
    }
    # #591 — blackout DMX for the capture window. Synchronous context
    # manager so state restores even if the HTTP call or ORB step
    # raises. Default "blackout"; callers can pass "keep" to preserve
    # show playback, or "fill" for a scan-friendly dim preset.
    lighting_mode = body.get("lighting", "blackout")
    with _ScanLightingWindow(lighting_mode):
        try:
            req = urllib.request.Request(
                f"http://{cam_a['cameraIp']}:5000/stereo-capture",
                data=json.dumps(body_payload).encode(),
                headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=45)
            data = json.loads(resp.read().decode())
        except Exception as e:
            return jsonify(err=f"stereo-capture request failed: {e}"), 502
    if not data.get("ok"):
        return jsonify(err=f"camera rejected request: {data.get('err')}"), 502

    frames = data.get("frames", {})
    key_a = str(cam_a.get("cameraIdx", 0))
    key_b = str(cam_b.get("cameraIdx", 1))

    def _decode(b64):
        buf = _np.frombuffer(base64.b64decode(b64), dtype=_np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)

    frame_a = _decode(frames[key_a])
    frame_b = _decode(frames[key_b])
    h_a, w_a = frame_a.shape[:2]
    h_b, w_b = frame_b.shape[:2]

    # Register both cameras with the stereo engine using the ACTUAL
    # captured resolution (sensor may ignore the request; trust the
    # firmware's reported `sizes`). FOV type defaults to horizontal but
    # each camera fixture can override via `fovType` — useful when a
    # camera's spec sheet quotes diagonal FOV, which is typical for
    # consumer USB cams.
    from stereo_engine import StereoEngine, feature_match_points
    engine = StereoEngine()
    pos_a = pos_map[cam_a["id"]]
    pos_b = pos_map[cam_b["id"]]

    # #592 Phase 2 — ArUco-anchored extrinsics. When `arucoMarkers=true`
    # in the body, run ArUco detection on both frames and solvePnP
    # against the surveyed corners so the cameras register with a pose
    # correction instead of the raw FOV fallback. Surveyed-marker
    # anchoring corrects mount-angle miscalibration that the layout
    # alone can't capture (consumer USB cams + hand-placed tripods
    # routinely drift 5-10°), which on the basement rig is the
    # difference between 350mm median reprojection error (FOV-only,
    # 500mm threshold needed to get any points) and <50mm.
    want_aruco = bool(body.get("arucoMarkers", False))
    anchor_info = {"requested": want_aruco, "a": None, "b": None, "fallback": None}
    def _detected_map(frame):
        corners, ids, _r, _sz = _aruco_detect(frame)
        out = {}
        if ids is not None and len(ids) > 0:
            for i, mid in enumerate(ids.flatten().tolist()):
                pts = corners[i].reshape(4, 2).tolist()
                out[int(mid)] = [[float(p[0]), float(p[1])] for p in pts]
        return out

    anchored = False
    if want_aruco:
        reg_by_id = {int(m.get("id")): m for m in _aruco_markers}
        if not reg_by_id:
            anchor_info["fallback"] = "no surveyed markers in registry"
        else:
            det_a = _detected_map(frame_a)
            det_b = _detected_map(frame_b)
            r_a = _aruco_anchor_extrinsics(
                w_a, h_a, cam_a.get("fovDeg", 90),
                _normalise_fov_type(cam_a.get("fovType")), det_a, reg_by_id)
            r_b = _aruco_anchor_extrinsics(
                w_b, h_b, cam_b.get("fovDeg", 90),
                _normalise_fov_type(cam_b.get("fovType")), det_b, reg_by_id)
            anchor_info["a"] = {k: v for k, v in r_a.items()
                                  if k not in ("K", "rvec", "tvec")}
            anchor_info["b"] = {k: v for k, v in r_b.items()
                                  if k not in ("K", "rvec", "tvec")}
            if "err" in r_a or "err" in r_b:
                anchor_info["fallback"] = (
                    f"solvePnP failed: a={r_a.get('err', 'ok')} b={r_b.get('err', 'ok')}")
            else:
                engine.add_camera(
                    "a",
                    {"fx": r_a["K"][0, 0], "fy": r_a["K"][1, 1],
                     "cx": r_a["K"][0, 2], "cy": r_a["K"][1, 2]},
                    {"rvec": r_a["rvec"].flatten().tolist(),
                     "tvec": r_a["tvec"].flatten().tolist()})
                engine.add_camera(
                    "b",
                    {"fx": r_b["K"][0, 0], "fy": r_b["K"][1, 1],
                     "cx": r_b["K"][0, 2], "cy": r_b["K"][1, 2]},
                    {"rvec": r_b["rvec"].flatten().tolist(),
                     "tvec": r_b["tvec"].flatten().tolist()})
                anchored = True
                log.info("Stereo anchored: cam_a %d corners RMS=%.2fpx, "
                         "cam_b %d corners RMS=%.2fpx",
                         r_a.get("cornerCount", 0), r_a.get("reprojectionRmsPx", 0),
                         r_b.get("cornerCount", 0), r_b.get("reprojectionRmsPx", 0))

    if not anchored:
        # Legacy FOV-only path — no surveyed anchor, 500 mm threshold
        # needed to get anything out of uncalibrated consumer webcams.
        engine.add_camera_from_fov(
            "a", cam_a.get("fovDeg", 90), w_a, h_a,
            (pos_a.get("x", 0), pos_a.get("y", 0), pos_a.get("z", 0)),
            cam_a.get("rotation", [0, 0, 0]),
            fov_type=_normalise_fov_type(cam_a.get("fovType")))
        engine.add_camera_from_fov(
            "b", cam_b.get("fovDeg", 90), w_b, h_b,
            (pos_b.get("x", 0), pos_b.get("y", 0), pos_b.get("z", 0)),
            cam_b.get("rotation", [0, 0, 0]),
            fov_type=_normalise_fov_type(cam_b.get("fovType")))

    matches = feature_match_points(frame_a, frame_b)
    # Threshold: tight (50 mm) when anchored, lenient (500 mm) otherwise.
    # Anchored poses correct the 5-15% consumer-lens mount-angle error
    # that FOV-only intrinsics can't model, so ORB matches survive a
    # tight reprojection filter that would drop 100% of them pre-anchor.
    default_thr = 50.0 if anchored else 500.0
    thr_mm = float(body.get("maxReprojErrMm", default_thr))
    points = engine.triangulate_pair("a", "b", matches,
                                     max_reproject_err_mm=thr_mm)

    global _point_cloud, _stage_surfaces_cache
    _point_cloud = {
        "schemaVersion": 2,
        "timestamp": time.time(),
        "source": "stereo",
        "cameras": [
            {"fixtureId": cam_a["id"], "cameraIdx": cam_a.get("cameraIdx", 0),
             "name": cam_a.get("name"), "pointCount": len(points)},
            {"fixtureId": cam_b["id"], "cameraIdx": cam_b.get("cameraIdx", 1),
             "name": cam_b.get("name"), "pointCount": len(points)},
        ],
        "points": points,
        "totalPoints": len(points),
        "captureDeltaMs": data.get("captureDeltaMs"),
        "featureMatches": len(matches),
        "stageW": int(_stage.get("w", 3) * 1000),
        "stageH": int(_stage.get("h", 2) * 1000),
        "stageD": int(_stage.get("d", 4) * 1000),
    }
    # Attach anchor provenance into the saved cloud so the Layout tab
    # can show a badge ("stereo · ArUco-anchored · 6 corners · RMS 2.4 px")
    # without a second round-trip.
    if anchored:
        _point_cloud["arucoAnchored"] = True
        _point_cloud["arucoAnchor"] = anchor_info
        _point_cloud["reprojThresholdMm"] = thr_mm
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("Stereo scan: %d matches → %d triangulated points (delta=%.1fms, "
             "thr=%.0fmm, anchored=%s)",
             len(matches), len(points), data.get("captureDeltaMs", 0),
             thr_mm, anchored)
    return jsonify(ok=True, source="stereo",
                   totalPoints=len(points),
                   featureMatches=len(matches),
                   captureDeltaMs=data.get("captureDeltaMs"),
                   tiltDelta=round(tilt_delta, 1),
                   panDelta=round(pan_delta, 1),
                   warning=tilt_warning,
                   arucoAnchored=anchored,
                   arucoAnchor=anchor_info,
                   reprojThresholdMm=thr_mm)


def _dmx_snapshot_state():
    """Capture the current ArtNet + sACN universe buffers so we can restore
    them after a blackout window. Returns a dict of engine → {uni → bytes}."""
    snap = {"artnet": {}, "sacn": {}}
    for name, eng in (("artnet", _artnet), ("sacn", _sacn)):
        if not getattr(eng, "running", False):
            continue
        for uni_num, uni in getattr(eng, "_universes", {}).items():
            try:
                snap[name][uni_num] = bytes(uni.get_data())
            except Exception:
                pass
    return snap


def _dmx_restore_state(snap):
    """Restore universe buffers from a _dmx_snapshot_state() result."""
    for name, eng in (("artnet", _artnet), ("sacn", _sacn)):
        if not getattr(eng, "running", False):
            continue
        for uni_num, data in snap.get(name, {}).items():
            try:
                eng.get_universe(uni_num).set_data(data)
            except Exception:
                pass


class _ScanLightingWindow:
    """Context manager that blacks out (or applies a fill preset to) all
    DMX universes for the duration of a scan and restores the prior
    state on exit. #591."""

    def __init__(self, mode="blackout"):
        self.mode = mode if mode in ("blackout", "keep", "fill") else "blackout"
        self._snap = None

    def __enter__(self):
        if self.mode == "keep":
            return self
        self._snap = _dmx_snapshot_state()
        if self.mode == "blackout":
            try:
                _artnet.blackout()
                _sacn.blackout()
                log.info("Scan: DMX blacked out for capture")
            except Exception as e:
                log.warning("Scan: blackout failed: %s", e)
        elif self.mode == "fill":
            # Scan-friendly fill: write a low neutral dimmer to each DMX
            # fixture that has a dimmer channel. No pan/tilt changes.
            try:
                for f in _fixtures:
                    if f.get("fixtureType") != "dmx":
                        continue
                    pid = f.get("dmxProfileId")
                    info = _profile_lib.channel_info(pid) if pid else None
                    if not info:
                        continue
                    ch_map = info.get("channel_map", {})
                    if "dimmer" not in ch_map:
                        continue
                    uni = f.get("dmxUniverse", 1)
                    addr = f.get("dmxStartAddr", 1)
                    for eng in (_artnet, _sacn):
                        if eng.running:
                            eng.get_universe(uni).set_channel(addr + ch_map["dimmer"], 60)
                log.info("Scan: fill-light preset applied (dimmer=60 on DMX fixtures)")
            except Exception as e:
                log.warning("Scan: fill preset failed: %s", e)
        # Give the bridge a short moment to transmit
        time.sleep(0.2)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._snap is not None:
            try:
                _dmx_restore_state(self._snap)
                log.info("Scan: DMX state restored")
            except Exception as e:
                log.warning("Scan: DMX restore failed: %s", e)
        return False


_scan_lighting_window = None  # tracks an open _ScanLightingWindow for async scans


@app.post("/api/space/scan")
def api_space_scan():
    """Start an async environment scan using all positioned camera sensors.

    Body:
        maxPointsPerCamera: int — monocular points per camera.
        lighting: "blackout" (default) | "keep" | "fill" — #591.
        cameras: optional list of fixture IDs to restrict the scan
                 (#588; otherwise every positioned camera is used).
    """
    global _scan_lighting_window
    if _space_scan.running:
        return jsonify(err="Scan already in progress"), 409
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera"]
    if not cams:
        return jsonify(err="No camera fixtures registered"), 400
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    positioned_cams = [c for c in cams if c["id"] in pos_map]
    if not positioned_cams:
        return jsonify(err="No camera fixtures positioned on layout"), 400
    body = request.get_json(silent=True) or {}
    max_pts = body.get("maxPointsPerCamera", 10000)
    # #588 — optional per-camera selection. When body.cameras is set,
    # only run the scan on those fixture IDs (still must be positioned).
    sel = body.get("cameras")
    if sel:
        ids = set(int(x) for x in sel)
        positioned_cams = [c for c in positioned_cams if c["id"] in ids]
        if not positioned_cams:
            return jsonify(err="None of the selected cameras are positioned"), 400
    # #581 — pass stage dimensions so depth anchoring can bound each
    # camera's rays against the surveyed box. Dimensions come from the
    # stage.json data file; values may be stored in either metres
    # (float < 100) or millimetres (int ≥ 100) historically — the
    # anchor_depth_scale helper normalises.
    stage_dims = dict(_stage) if _stage else None
    # #591 — open a lighting window before starting the scan. The
    # status endpoint closes it when the scan completes. Without this
    # the monocular depth model's output was being corrupted by DMX
    # hotspots on walls (see Cam13 r=0.127 in the basement rig test).
    lighting_mode = body.get("lighting", "blackout")
    _scan_lighting_window = _ScanLightingWindow(lighting_mode)
    _scan_lighting_window.__enter__()
    _space_scan.start(positioned_cams, pos_map,
                      max_points_per_cam=max_pts,
                      stage_dims=stage_dims)
    return jsonify(ok=True, pending=True,
                   cameras=len(positioned_cams),
                   lighting=lighting_mode)

@app.get("/api/space/scan/status")
def api_space_scan_status():
    """Poll environment scan progress."""
    global _scan_lighting_window
    st = _space_scan.status
    # #591 — once the scan finishes, close the lighting window so the
    # operator's previous DMX state is restored.
    if not st["running"] and _scan_lighting_window is not None:
        try:
            _scan_lighting_window.__exit__(None, None, None)
        except Exception as e:
            log.warning("Scan: lighting restore failed: %s", e)
        _scan_lighting_window = None
    if not st["running"] and st.get("result"):
        global _point_cloud, _stage_surfaces_cache
        _point_cloud = st["result"]
        _point_cloud["stageW"] = int(_stage.get("w", 3) * 1000)
        _point_cloud["stageH"] = int(_stage.get("h", 2) * 1000)
        _point_cloud["stageD"] = int(_stage.get("d", 1.5) * 1000)
        # #599 — auto-align Z to surveyed floor markers. Same treatment
        # ZoeDepth gets — monocular depth's scale-prior-derived floor
        # position is pretty but arbitrary; the ArUco registry is the
        # authoritative anchor.
        align = _apply_marker_z_alignment(_point_cloud)
        if align.get("applied"):
            _point_cloud["markerAlignment"] = align
        _save("pointcloud", _point_cloud)
        # #496 — new cloud invalidates analyzed surfaces cache.
        _stage_surfaces_cache = {"key": None, "value": None}
    # #588 — return per-camera summary (name, pointCount, anchorQuality)
    # so the Advanced Scan card can show a quality breakdown when the
    # scan completes. Keep `result` slim (no points, just metadata) to
    # avoid ballooning the JSON on every poll.
    result_meta = None
    if st.get("result"):
        r = st["result"]
        result_meta = {
            "totalPoints": r.get("totalPoints", 0),
            "cameras": r.get("cameras", []),
            "filterStats": r.get("filterStats"),
            "source": r.get("source"),
            "floorOffset": r.get("floorOffset"),
        }
    return jsonify(running=st["running"], progress=st["progress"],
                   message=st["message"],
                   totalPoints=st["result"]["totalPoints"] if st.get("result") else 0,
                   result=result_meta)

@app.get("/api/space")
def api_space_get():
    """Get the stored point cloud.

    Query `?meta=1` returns only the metadata (timestamp, source,
    contributing cameras, counts) — used by the Setup tab (#578) so
    the status pill doesn't have to pull 10k points on every render.
    """
    if not _point_cloud:
        return jsonify(ok=False, err="No environment scan available"), 404
    if request.args.get("meta"):
        return jsonify(ok=True,
                       timestamp=_point_cloud.get("timestamp"),
                       source=_point_cloud.get("source", "scan"),
                       totalPoints=_point_cloud.get("totalPoints", 0),
                       cameras=_point_cloud.get("cameras", []),
                       floorNormalized=_point_cloud.get("floorNormalized"),
                       stageW=_point_cloud.get("stageW"),
                       stageH=_point_cloud.get("stageH"),
                       stageD=_point_cloud.get("stageD"))
    return jsonify(ok=True, **_point_cloud)

@app.post("/api/space/analyze")
def api_space_analyze():
    """Analyze the point cloud to detect surfaces (floor, walls, obstacles)."""
    if not _point_cloud or not _point_cloud.get("points"):
        return jsonify(err="No point cloud — run environment scan first"), 404
    from surface_analyzer import analyze_surfaces
    result = analyze_surfaces(_point_cloud["points"])
    _point_cloud["surfaces"] = result
    _save("pointcloud", _point_cloud)
    return jsonify(ok=True, **result)

@app.post("/api/space/create-objects")
def api_space_create_objects():
    """Create stage objects from detected surfaces (floor, walls, obstacles)."""
    global _nxt_obj
    if not _point_cloud or not _point_cloud.get("surfaces"):
        return jsonify(err="No surface analysis — run /api/space/analyze first"), 404
    surfaces = _point_cloud["surfaces"]
    created = []
    with _lock:
        # Floor
        floor = surfaces.get("floor")
        if floor:
            ext = floor.get("extent", {})
            w = ext.get("xMax", 0) - ext.get("xMin", 0)
            d = ext.get("zMax", 0) - ext.get("zMin", 0)
            obj = {
                "id": _nxt_obj, "name": "Floor",
                "objectType": "floor", "mobility": "static",
                "color": "#475569", "opacity": 15,
                "transform": {
                    "pos": [ext.get("xMin", 0), floor["y"], ext.get("zMin", 0)],
                    "rot": [0, 0, 0],
                    "scale": [max(w, 100), 10, max(d, 100)],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": "Floor"})
            _nxt_obj += 1

        # Walls
        for i, wall in enumerate(surfaces.get("walls", [])):
            ext = wall.get("extent", {})
            w = ext.get("xMax", 0) - ext.get("xMin", 0)
            h = ext.get("yMax", 0) - ext.get("yMin", 0)
            n = wall.get("normal", [0, 0, 1])
            # Name based on direction
            if abs(n[2]) > 0.7:
                wname = "Back Wall" if n[2] > 0 else "Front Wall"
            elif abs(n[0]) > 0.7:
                wname = "Right Wall" if n[0] > 0 else "Left Wall"
            else:
                wname = f"Wall {i+1}"
            obj = {
                "id": _nxt_obj, "name": wname,
                "objectType": "wall", "mobility": "static",
                "color": "#334155", "opacity": 10,
                "transform": {
                    "pos": [ext.get("xMin", 0), ext.get("yMin", 0), ext.get("zMin", 0)],
                    "rot": [0, 0, 0],
                    "scale": [max(w, 100), max(h, 100), 50],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": wname})
            _nxt_obj += 1

        # Obstacles
        for obs in surfaces.get("obstacles", []):
            obj = {
                "id": _nxt_obj, "name": obs.get("label", "Obstacle").title(),
                "objectType": "prop", "mobility": "static",
                "color": "#7c3aed", "opacity": 20,
                "transform": {
                    "pos": [obs["pos"][0] - obs["size"][0]//2,
                            obs["pos"][1] - obs["size"][1]//2,
                            obs["pos"][2] - obs["size"][2]//2],
                    "rot": [0, 0, 0],
                    "scale": [max(obs["size"][0], 100), max(obs["size"][1], 100),
                              max(obs["size"][2], 100)],
                },
            }
            _objects.append(obj)
            created.append({"id": _nxt_obj, "name": obs.get("label", "Obstacle").title()})
            _nxt_obj += 1

        _save("objects", _objects)
    return jsonify(ok=True, created=created, count=len(created))

@app.get("/api/space/surfaces")
def api_space_surfaces():
    """Get detected surfaces from the last analysis."""
    if not _point_cloud or not _point_cloud.get("surfaces"):
        return jsonify(err="No surface analysis — run /api/space/analyze first"), 404
    return jsonify(ok=True, **_point_cloud["surfaces"])

@app.delete("/api/space")
def api_space_clear():
    """Clear the stored point cloud."""
    global _point_cloud
    _point_cloud = None
    _save("pointcloud", None)
    return jsonify(ok=True)


# ── Camera tracking — orchestrator proxy ──────────────────────────────

_tracking_state = {}  # {cam_fid: True/False}

@app.post("/api/cameras/<int:fid>/track/start")
def api_camera_track_start(fid):
    """Start tracking on a camera node with pre-flight checks."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400

    # Pre-flight: probe camera node for readiness
    info = _probe_camera(ip, timeout=3)
    if not info:
        return jsonify(err=f"Camera node {ip} is offline or unreachable"), 503
    caps = info.get("capabilities", {})
    if not caps.get("hasCamera"):
        return jsonify(err=f"Camera node {ip} has no working camera connected"), 503
    if not caps.get("scan") and not caps.get("tracking"):
        return jsonify(err=f"Camera node {ip} has no detection model loaded — deploy firmware with model first"), 503

    # If already tracking on this camera, stop first so settings refresh cleanly
    if _tracking_state.get(fid):
        try:
            import urllib.request as _ur_stop
            _ur_stop.urlopen(
                _ur_stop.Request(f"http://{ip}:5000/track/stop", data=b"{}",
                                 headers={"Content-Type": "application/json"}),
                timeout=5)
        except Exception:
            pass
        _tracking_state.pop(fid, None)

    body = request.get_json(silent=True) or {}
    local_ip = _get_local_ip()
    port = request.host.split(":")[-1] if ":" in request.host else "8080"
    classes = body.get("classes", f.get("trackClasses", ["person"]))
    try:
        import urllib.request as _ur
        req_data = json.dumps({
            "cam": body.get("cam", 0),
            "orchestratorUrl": f"http://{local_ip}:{port}",
            "cameraId": fid,
            "fps": body.get("fps", f.get("trackFps", 2)),
            "threshold": body.get("threshold", f.get("trackThreshold", 0.4)),
            "ttl": body.get("ttl", f.get("trackTtl", 5)),
            "classes": classes,
            "reidMm": body.get("reidMm", f.get("trackReidMm", 500)),
            "inputSize": body.get("inputSize", f.get("trackInputSize", 320)),
        }).encode()
        req = _ur.Request(f"http://{ip}:5000/track/start",
                          data=req_data,
                          headers={"Content-Type": "application/json"})
        resp = _ur.urlopen(req, timeout=10)
        r = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify(err=f"Failed to start tracking: {e}"), 503
    if not r.get("ok", True):
        return jsonify(err=r.get("err", "Camera node rejected track start")), 503
    _tracking_state[fid] = True
    lbl = classes[0] if len(classes) == 1 else f"{len(classes)} classes"
    log.info("Tracking started on camera %d (%s) — watching for %s", fid, ip, lbl)
    return jsonify(ok=True, tracking=True)


@app.post("/api/cameras/<int:fid>/track/stop")
def api_camera_track_stop(fid):
    """Stop tracking on a camera node."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://{ip}:5000/track/stop",
                          data=b"{}",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception:
        pass  # Camera may be offline — still mark as stopped
    _tracking_state.pop(fid, None)
    return jsonify(ok=True, tracking=False)


@app.get("/api/cameras/<int:fid>/track/status")
def api_camera_track_status(fid):
    """Get tracking state for a camera."""
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    return jsonify(tracking=_tracking_state.get(fid, False))


def _get_local_ip():
    """Get local IP by connecting a UDP socket (no traffic sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())

# ── Camera network scan (SSH port scan for fresh SBCs) ──────────────────

_ssh_scan_state = {"pending": False, "data": []}

def _scan_ssh_devices():
    """TCP connect scan for port 22 on all local subnets. Returns SSH-accessible hosts."""
    import concurrent.futures
    try:
        local_ip = _get_local_ip()
    except Exception:
        local_ip = "192.168.1.1"
    skip_ips = {local_ip}
    for c in _children:
        skip_ips.add(c.get("ip", ""))

    def _check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            if s.connect_ex((ip, 22)) == 0:
                s.close()
                cam_info = _probe_camera(ip, timeout=0.5)
                return {"ip": ip, "hasCamera": cam_info is not None,
                        "hostname": (cam_info or {}).get("hostname", ""),
                        "fwVersion": (cam_info or {}).get("fwVersion", "")}
            s.close()
        except Exception:
            pass
        return None

    ips = []
    for prefix in _local_subnet_prefixes():
        for i in range(1, 255):
            ip = f"{prefix}.{i}"
            if ip not in skip_ips:
                ips.append(ip)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        for r in pool.map(_check, ips):
            if r:
                results.append(r)
    return results

def _ssh_scan_bg():
    try:
        _ssh_scan_state["data"] = _scan_ssh_devices()
    finally:
        _ssh_scan_state["pending"] = False

@app.get("/api/cameras/scan-network")
def api_cameras_scan_network():
    if _ssh_scan_state["pending"]:
        return jsonify(pending=True)
    _ssh_scan_state["pending"] = True
    _ssh_scan_state["data"] = []
    threading.Thread(target=_ssh_scan_bg, daemon=True).start()
    return jsonify(pending=True)

@app.get("/api/cameras/scan-network/results")
def api_cameras_scan_network_results():
    if _ssh_scan_state["pending"]:
        return jsonify(pending=True)
    return jsonify(_ssh_scan_state["data"])

# ── Camera deploy via SSH+SCP ───────────────────────────────────────────

_deploy_status = {"running": False, "progress": 0, "message": "", "error": None,
                  "ip": "", "remoteVersion": None, "localVersion": None}
_deploy_lock = threading.Lock()

_CAMERA_FW_FILES = ("camera_server.py", "detector.py", "depth_estimator.py",
                    "beam_detector.py", "tracker.py", "requirements.txt", "slyled-cam.service")
_github_camera_cache = {"version": None, "ts": 0}
_GITHUB_CAMERA_TTL = 3600  # 1 hour cache

def _parse_version_from_text(text):
    """Extract VERSION = "1.5.66" from camera_server.py source text."""
    import re
    m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None

def _camera_local_version():
    """Read VERSION from the local (bundled) camera_server.py source."""
    for base in [Path(getattr(sys, '_MEIPASS', '')) / "firmware" / "orangepi",
                 _FW_DIR / "orangepi"]:
        p = base / "camera_server.py"
        if p.exists():
            try:
                v = _parse_version_from_text(p.read_text(encoding="utf-8"))
                if v:
                    return v
            except Exception:
                pass
    return None

def _camera_downloaded_version():
    """Read VERSION from the downloaded (cached) camera_server.py if present."""
    p = DATA / "firmware" / "camera" / "camera_server.py"
    if p.exists():
        try:
            return _parse_version_from_text(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None

def _camera_deploy_version():
    """Return the version that would actually be deployed (downloaded > local)."""
    dl = _camera_downloaded_version()
    loc = _camera_local_version()
    if dl and loc:
        # Compare semver-style: prefer whichever is newer
        try:
            dl_t = tuple(int(x) for x in dl.split("."))
            loc_t = tuple(int(x) for x in loc.split("."))
            return dl if dl_t >= loc_t else loc
        except (ValueError, AttributeError):
            return dl
    return dl or loc

def _camera_deploy_dir():
    """Return the directory to use for camera firmware deployment.
    Prefers downloaded cache if it has a newer version than bundled."""
    dl_dir = DATA / "firmware" / "camera"
    dl_ver = _camera_downloaded_version()
    loc_ver = _camera_local_version()
    if dl_ver and dl_dir.exists() and (dl_dir / "camera_server.py").exists():
        if not loc_ver:
            return dl_dir
        try:
            dl_t = tuple(int(x) for x in dl_ver.split("."))
            loc_t = tuple(int(x) for x in loc_ver.split("."))
            if dl_t >= loc_t:
                return dl_dir
        except (ValueError, AttributeError):
            return dl_dir
    return _FW_DIR / "orangepi"

@app.get("/api/firmware/camera/check")
def api_firmware_camera_check():
    """Compare bundled vs downloaded vs GitHub latest camera firmware versions."""
    import urllib.request as _ur
    local_ver = _camera_local_version() or "0.0.0"
    dl_ver = _camera_downloaded_version()
    now = time.time()
    # Check cache first
    if _github_camera_cache["version"] and now - _github_camera_cache["ts"] < _GITHUB_CAMERA_TTL:
        latest = _github_camera_cache["version"]
    else:
        latest = None
        try:
            req = _ur.Request(
                "https://api.github.com/repos/SlyWombat/SlyLED/contents/firmware/orangepi/camera_server.py?ref=main",
                headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
            import base64
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            latest = _parse_version_from_text(content)
            if latest:
                _github_camera_cache["version"] = latest
                _github_camera_cache["ts"] = now
                log.info("GitHub camera firmware: v%s", latest)
        except Exception as e:
            log.debug("GitHub camera check failed: %s", e)
            latest = _github_camera_cache.get("version")  # stale cache
    # Determine if update is available
    update = False
    effective = dl_ver or local_ver
    if latest and effective:
        try:
            latest_t = tuple(int(x) for x in latest.split("."))
            eff_t = tuple(int(x) for x in effective.split("."))
            update = latest_t > eff_t
        except (ValueError, AttributeError):
            pass
    return jsonify(localVersion=local_ver, downloadedVersion=dl_ver,
                   latestVersion=latest, updateAvailable=update)

@app.post("/api/firmware/camera/download")
def api_firmware_camera_download():
    """Download all camera firmware files from GitHub main branch."""
    import urllib.request as _ur
    dest = DATA / "firmware" / "camera"
    dest.mkdir(parents=True, exist_ok=True)
    downloaded = []
    errors = []
    for fname in _CAMERA_FW_FILES:
        url = f"https://raw.githubusercontent.com/SlyWombat/SlyLED/main/firmware/orangepi/{fname}"
        try:
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=15)
            content = resp.read()
            (dest / fname).write_bytes(content)
            downloaded.append(fname)
        except Exception as e:
            log.warning("Failed to download %s: %s", fname, e)
            errors.append(f"{fname}: {e}")
    # Parse version from downloaded camera_server.py
    ver = _camera_downloaded_version()
    if ver:
        _github_camera_cache["version"] = ver
        _github_camera_cache["ts"] = time.time()
    log.info("Downloaded %d camera firmware files (v%s)", len(downloaded), ver)
    if errors:
        return jsonify(ok=True, version=ver, files=downloaded,
                       warnings=errors)
    return jsonify(ok=True, version=ver, files=downloaded)

def _deploy_camera_bg(ip, force=False):
    """Deploy camera_server.py to a remote SBC via SSH+SCP."""
    def _update(progress, message, error=None):
        with _deploy_lock:
            _deploy_status.update(progress=progress, message=message, error=error)
    try:
        import paramiko
    except ImportError:
        _update(0, "paramiko not installed", error="pip install paramiko")
        with _deploy_lock:
            _deploy_status["running"] = False
        return

    deploy_ver = _camera_deploy_version()
    with _deploy_lock:
        _deploy_status["localVersion"] = deploy_ver

    try:
        # ── Version check ──────────────────────────────────────────
        _update(2, "Checking remote version...")
        remote_info = _probe_camera(ip, timeout=3)
        remote_ver = remote_info.get("fwVersion") if remote_info else None
        with _deploy_lock:
            _deploy_status["remoteVersion"] = remote_ver

        if remote_ver and deploy_ver and remote_ver == deploy_ver and not force:
            _update(100, f"Already up-to-date \u2014 v{remote_ver}")
            return

        if remote_ver:
            _update(3, f"Upgrading {remote_ver} \u2192 {deploy_ver}...")
        else:
            _update(3, f"Fresh install \u2014 v{deploy_ver}...")

        # ── SSH connect ────────────────────────────────────────────
        _update(5, f"Connecting to {ip} via SSH...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Use per-node SSH credentials if available, else fall back to global
        _cam_ssh = _get_node_ssh(ip)
        user = _cam_ssh["user"]
        key_path = os.path.expanduser(_cam_ssh.get("keyPath", "")) if _cam_ssh.get("keyPath") else ""
        pw = _cam_ssh.get("password", "")

        connected = False
        # Try key auth first
        if key_path and os.path.isfile(key_path):
            try:
                ssh.connect(hostname=ip, port=22, username=user,
                            key_filename=key_path, timeout=10,
                            look_for_keys=False, allow_agent=False)
                connected = True
            except paramiko.AuthenticationException:
                pass
        # Try password auth
        if not connected and pw:
            try:
                ssh.connect(hostname=ip, port=22, username=user,
                            password=pw, timeout=10,
                            look_for_keys=False, allow_agent=False)
                connected = True
            except paramiko.AuthenticationException as e:
                if "publickey" in str(e):
                    _update(0, "Key auth required",
                            error="This device only accepts SSH key authentication. "
                                  "Generate a key pair in Camera Setup, then add the "
                                  "public key to the device's ~/.ssh/authorized_keys")
                    with _deploy_lock:
                        _deploy_status["running"] = False
                    return
                raise
        # Try default keys from agent/system
        if not connected:
            try:
                ssh.connect(hostname=ip, port=22, username=user, timeout=10)
                connected = True
            except paramiko.AuthenticationException as e:
                auth_types = str(e)
                if "publickey" in auth_types and not key_path:
                    _update(0, "Key auth required",
                            error="This device only accepts SSH key authentication. "
                                  "Generate a key pair in Camera Setup, then add the "
                                  "public key to the device's ~/.ssh/authorized_keys")
                else:
                    _update(0, "Authentication failed",
                            error=f"Could not authenticate to {ip}. "
                                  f"Check SSH credentials in Camera Setup. ({auth_types})")
                with _deploy_lock:
                    _deploy_status["running"] = False
                return

        # ── Detect if we need sudo ─────────────────────────────────
        _, stdout, _ = ssh.exec_command("id -u")
        uid = stdout.read().decode().strip()
        sudo = "" if uid == "0" else "sudo "
        log.info("Deploy: uid=%s, sudo=%s", uid, "no" if not sudo else "yes")

        # ── Pre-flight checks ──────────────────────────────────────
        _update(10, "Pre-flight checks...")
        _, stdout, _ = ssh.exec_command("python3 --version")
        py_out = stdout.read().decode().strip()
        if not py_out:
            _update(0, "Python3 not found on device", error="python3 is required")
            ssh.close()
            return

        _, stdout, _ = ssh.exec_command("which pip3")
        if not stdout.read().decode().strip():
            _update(15, "Installing pip3...")
            _, stdout, stderr = ssh.exec_command(
                f"{sudo}apt-get update -qq && {sudo}apt-get install -y -qq python3-pip", timeout=120)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode("utf-8", errors="replace")[:300]
                _update(0, "Failed to install pip3", error=err)
                ssh.close()
                return

        # ── Create target directory ────────────────────────────────
        _update(25, "Creating /opt/slyled...")
        _, stdout, _ = ssh.exec_command(f"{sudo}mkdir -p /opt/slyled/models && {sudo}chmod 777 /opt/slyled /opt/slyled/models")
        stdout.channel.recv_exit_status()

        # ── Upload firmware files ──────────────────────────────────
        _update(30, "Uploading firmware files...")
        sftp = ssh.open_sftp()
        src_dir = _camera_deploy_dir()
        log.info("Deploy: using firmware from %s", src_dir)
        for fname in _CAMERA_FW_FILES:
            src = src_dir / fname
            if src.exists():
                sftp.put(str(src), f"/opt/slyled/{fname}")
        # Upload ML models if present locally (check both downloaded cache and bundled)
        try:
            sftp.stat("/opt/slyled/models")
        except FileNotFoundError:
            sftp.mkdir("/opt/slyled/models")
        for model_name, desc, size_hint in [
            ("yolov8n.onnx",                    "detection model", "~12 MB"),
            ("depth_anything_v2_small.onnx",    "depth model (disparity)", "~95 MB"),
            ("dav2_metric_indoor_small.onnx",   "depth model (metric, #593)", "~95 MB"),
        ]:
            m_src = src_dir / "models" / model_name
            if not m_src.exists():
                m_src = _FW_DIR / "orangepi" / "models" / model_name
            if m_src.exists():
                _update(35, f"Uploading {desc} ({size_hint})...")
                sftp.put(str(m_src), f"/opt/slyled/models/{model_name}")
        sftp.close()

        # ── Install system packages ────────────────────────────────
        _update(40, "Installing system packages...")
        _, stdout, stderr = ssh.exec_command(
            f"{sudo}apt-get install -y -qq fswebcam python3-opencv python3-numpy v4l-utils",
            timeout=120)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err = stderr.read().decode("utf-8", errors="replace")[:300]
            log.warning("apt-get partial failure (continuing): %s", err)

        # ── Install Python dependencies ────────────────────────────
        _update(50, "Installing Python dependencies...")
        _, stdout, stderr = ssh.exec_command(
            f"cd /opt/slyled && {sudo}pip3 install --break-system-packages -r requirements.txt 2>&1",
            timeout=180)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            # Try without --break-system-packages (older pip)
            _, stdout, stderr = ssh.exec_command(
                f"cd /opt/slyled && {sudo}pip3 install -r requirements.txt 2>&1", timeout=180)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode("utf-8", errors="replace")[:500]
                _update(50, "pip install failed", error=err)
                ssh.close()
                return

        # ── Verify detection model ─────────────────────────────────
        _update(60, "Checking detection model...")
        _, stdout, _ = ssh.exec_command("test -f /opt/slyled/models/yolov8n.onnx && echo EXISTS")
        if "EXISTS" in stdout.read().decode():
            _update(65, "Detection model present")
        else:
            log.warning("yolov8n.onnx not on device (not bundled locally?) — scan will be unavailable")

        # ── Install systemd service ────────────────────────────────
        _update(70, "Setting up systemd service...")
        ssh.exec_command(f"{sudo}systemctl stop slyled-cam 2>/dev/null || true")
        time.sleep(1)
        # Copy tracked service file from upload to systemd
        _, stdout, _ = ssh.exec_command(
            f"{sudo}cp /opt/slyled/slyled-cam.service /etc/systemd/system/slyled-cam.service "
            f"&& {sudo}systemctl daemon-reload && {sudo}systemctl enable slyled-cam")
        stdout.channel.recv_exit_status()

        # ── Start and verify ───────────────────────────────────────
        _update(80, "Starting camera server...")
        _, stdout, _ = ssh.exec_command(f"{sudo}systemctl start slyled-cam")
        stdout.channel.recv_exit_status()
        ssh.close()

        _update(90, "Verifying camera server...")
        # Retry probe with increasing delays — slow devices (RPi) can take 60s+
        info = None
        for attempt in range(12):
            time.sleep(5 if attempt < 3 else 10)
            _update(90 + min(attempt, 9), f"Verifying... ({(attempt+1)*5}s)")
            info = _probe_camera(ip, timeout=5)
            if info:
                break
        if info:
            new_ver = info.get("fwVersion", "?")
            if remote_ver:
                _update(100, f"Upgrade complete \u2014 v{remote_ver} \u2192 v{new_ver}")
            else:
                _update(100, f"Deploy complete \u2014 {info.get('hostname', ip)} v{new_ver} online")
        else:
            _update(100, f"\u2713 Deploy uploaded successfully. Server may still be starting on {ip}.")
    except Exception as e:
        _update(_deploy_status.get("progress", 0), "Deploy failed", error=str(e))
    finally:
        with _deploy_lock:
            _deploy_status["running"] = False

@app.post("/api/cameras/deploy")
def api_cameras_deploy():
    with _deploy_lock:
        if _deploy_status["running"]:
            return jsonify(err="Deploy already in progress"), 409
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    force = body.get("force", False)
    if not ip:
        return jsonify(err="ip required"), 400
    if not _ssh.get("sshPassword") and not _ssh.get("sshKeyPath"):
        return jsonify(err="SSH credentials not configured"), 400
    with _deploy_lock:
        _deploy_status.update(running=True, progress=0, message="Starting...",
                              error=None, ip=ip, remoteVersion=None,
                              localVersion=None)
    threading.Thread(target=_deploy_camera_bg, args=(ip, force), daemon=True).start()
    return jsonify(ok=True, pending=True)

@app.get("/api/cameras/deploy/status")
def api_cameras_deploy_status():
    with _deploy_lock:
        return jsonify(dict(_deploy_status))

# ── Camera SSH settings ─────────────────────────────────────────────────

@app.get("/api/cameras/ssh")
def api_cameras_ssh_get():
    key_path = _ssh.get("sshKeyPath", "")
    key_exists = bool(key_path and Path(os.path.expanduser(key_path)).exists())
    return jsonify({
        "sshUser": _ssh.get("sshUser", "root"),
        "hasPassword": bool(_ssh.get("sshPassword")),
        "sshKeyPath": key_path,
        "hasKey": key_exists,
    })

@app.post("/api/cameras/ssh/generate-key")
def api_cameras_ssh_generate_key():
    """Generate an Ed25519 SSH key pair for camera deployments."""
    try:
        import paramiko
    except ImportError:
        return jsonify(err="paramiko not installed"), 500
    key_dir = DATA / "ssh"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / "camera_key"
    pub_file = key_dir / "camera_key.pub"

    # Generate Ed25519 key using cryptography library (paramiko wraps it)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()
    )
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH
    )
    key_file.write_bytes(priv_pem)
    key_file.chmod(0o600)

    pub_str = pub_bytes.decode("utf-8") + " slyled-camera"
    pub_file.write_text(pub_str + "\n")

    with _lock:
        _ssh["sshKeyPath"] = str(key_file)
        _save("ssh", _ssh)

    return jsonify(ok=True, publicKey=pub_str, keyPath=str(key_file))

@app.post("/api/cameras/ssh")
def api_cameras_ssh_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        if "sshUser" in body:
            _ssh["sshUser"] = body["sshUser"]
        if "sshPassword" in body:
            _ssh["sshPassword"] = _encrypt_pw(body["sshPassword"])
        if "sshKeyPath" in body:
            _ssh["sshKeyPath"] = body["sshKeyPath"]
        if "sshKeyContent" in body:
            # Save pasted key content to a managed file
            key_dir = DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            key_file = key_dir / "camera_key"
            key_file.write_text(body["sshKeyContent"])
            key_file.chmod(0o600)
            _ssh["sshKeyPath"] = str(key_file)
        _save("ssh", _ssh)
    return jsonify(ok=True)

# -- Per-camera-node SSH config (#311) -------------------------------------------
# SSH credentials keyed by camera node IP (not per sensor/fixture).
# Multiple sensors on the same Orange Pi share one SSH config.

@app.get("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_get(ip):
    """Get SSH config for a camera hardware node by IP (password masked)."""
    ssh = _camera_ssh.get(ip, {})
    return jsonify({
        "ip": ip,
        "authType": ssh.get("authType", "password"),
        "user": ssh.get("user", _ssh.get("sshUser", "root")),
        "hasPassword": bool(ssh.get("password")),
        "keyPath": ssh.get("keyPath", ""),
        "keyStored": ssh.get("keyStored", False),
        "configured": bool(ssh),
    })


@app.post("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_save(ip):
    """Save SSH config for a camera hardware node."""
    body = request.get_json(silent=True) or {}
    with _lock:
        ssh = _camera_ssh.get(ip, {})
        if "authType" in body:
            ssh["authType"] = body["authType"]
        if "user" in body:
            ssh["user"] = body["user"]
        if "password" in body:
            ssh["password"] = _encrypt_pw(body["password"]) if body["password"] else ""
        if "keyPath" in body:
            ssh["keyPath"] = body["keyPath"]
            ssh["keyStored"] = False
        if "keyContent" in body and body["keyContent"]:
            key_dir = DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            safe_ip = ip.replace(".", "_")
            key_file = key_dir / f"cam_node_{safe_ip}_key"
            key_file.write_text(body["keyContent"])
            key_file.chmod(0o600)
            ssh["keyPath"] = str(key_file)
            ssh["keyStored"] = True
        _camera_ssh[ip] = ssh
        _save("camera_ssh", _camera_ssh)
    return jsonify(ok=True)


@app.post("/api/cameras/node/<path:ip>/ssh/test")
def api_camera_node_ssh_test(ip):
    """Test SSH connection to a camera node."""
    ssh_cfg = _get_node_ssh(ip)
    try:
        import paramiko
    except ImportError:
        return jsonify(ok=False, err="paramiko not installed — run: pip install paramiko")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {"hostname": ip, "port": 22, "username": ssh_cfg["user"], "timeout": 8}
        if ssh_cfg.get("keyPath") and Path(os.path.expanduser(ssh_cfg["keyPath"])).exists():
            kwargs["key_filename"] = os.path.expanduser(ssh_cfg["keyPath"])
        elif ssh_cfg.get("password"):
            kwargs["password"] = ssh_cfg["password"]
        else:
            return jsonify(ok=False, err="No password or key configured. Enter credentials and save first.",
                           guidance="Set a password or provide an SSH key, then click Save.")
        client.connect(**kwargs)
        stdin, stdout, stderr = client.exec_command("whoami")
        user = stdout.read().decode().strip()
        client.close()
        return jsonify(ok=True, user=user, msg=f"Connected as {user}")
    except paramiko.AuthenticationException:
        return jsonify(ok=False, err="Authentication failed",
                       guidance="Check username and password, or ensure your SSH key is in the device's authorized_keys file.")
    except paramiko.SSHException as e:
        return jsonify(ok=False, err=f"SSH error: {e}",
                       guidance="Check that SSH is enabled on the device and the IP is correct.")
    except OSError as e:
        if "timed out" in str(e).lower():
            return jsonify(ok=False, err="Connection timed out",
                           guidance="Camera not responding. Check the IP address and network connectivity.")
        return jsonify(ok=False, err=f"Connection refused: {e}",
                       guidance="Check that the camera is powered on and SSH port 22 is accessible.")
    except Exception as e:
        return jsonify(ok=False, err=str(e))
    finally:
        try:
            client.close()
        except Exception:
            pass


def _get_node_ssh(ip):
    """Get SSH credentials for a camera node by IP, falling back to global _ssh."""
    ssh = _camera_ssh.get(ip, {})
    if ssh.get("password") or ssh.get("keyPath"):
        pw = ""
        if ssh.get("password"):
            try:
                pw = _decrypt_pw(ssh["password"])
            except Exception:
                pw = ""
        return {
            "user": ssh.get("user", "root"),
            "password": pw,
            "keyPath": ssh.get("keyPath", ""),
        }
    # Fall back to global SSH config
    pw = ""
    if _ssh.get("sshPassword"):
        try:
            pw = _decrypt_pw(_ssh["sshPassword"])
        except Exception:
            pw = ""
    return {
        "user": _ssh.get("sshUser", "root"),
        "password": pw,
        "keyPath": _ssh.get("sshKeyPath", ""),
    }


@app.post("/api/fixtures/<int:fid>/resolve")
def api_fixture_resolve(fid):
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Not found"), 404
    # Build resolve input from fixture + child position
    child = next((c for c in _children if c["id"] == f.get("childId")), None)
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    lp = pos_map.get(f.get("childId"), {})
    child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
    resolve_input = {
        "type": f.get("type", "linear"),
        "childPos": child_pos,
        "strings": f.get("strings", []),
        "aoeRadius": f.get("aoeRadius", 1000),
    }
    # If child has string info, merge it
    if child and not f.get("strings"):
        resolve_input["strings"] = [
            {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
            for s in child.get("strings", [])[:child.get("sc", 0)]
        ]
    result = resolve_fixture(resolve_input)
    return jsonify(result)

#  "  "  Objects (Phase 2)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

def _apply_stage_lock(s):
    """Resize a stage-locked object to match current stage dimensions (mm)."""
    sw = int(_stage["w"] * 1000)
    sh = int(_stage["h"] * 1000)
    sd = int(_stage["d"] * 1000)
    t = s.setdefault("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]})
    st = s.get("objectType", "custom")
    if st == "wall":
        t["scale"] = [sw, sh, 100]
        t["pos"] = [0, 0, 0]
    elif st == "floor":
        t["scale"] = [sw, sd + 1000, 100]
        t["pos"] = [0, 0, 0]

def _sync_locked_objects():
    """Re-apply stage dimensions to all stage-locked objects."""
    changed = False
    for s in _objects:
        if s.get("stageLocked"):
            _apply_stage_lock(s)
            changed = True
    if changed:
        _save("objects", _objects)

def _reap_temporal_objects():
    """Remove expired temporal objects and fuse near-duplicates across
    cameras. Q3/#629/#630 — a person seen by two cameras should become
    one tracked object with higher confidence, not two drifting ones."""
    now = time.time()
    global _temporal_objects
    _temporal_objects = [o for o in _temporal_objects if o.get("_expiresAt", 0) > now]
    _fuse_temporal_objects()


# Q3/#629/#630 — multi-camera fusion.
# Tuning defaults. Cluster radius = "how close in XY do two placements
# need to be before we call them the same person". 500 mm is a human
# shoulder-width ballpark and matches the existing per-camera re-ID
# threshold (feedback_layout_positions.md). Age gate = within the same
# tracker-push cycle (~2 s); older clusters aren't overwritten.
_FUSION_CLUSTER_MM = 500.0
_FUSION_MAX_AGE_S = 2.0
_FUSION_TIER_WEIGHT = {"homography": 1.0, "fov-projection": 0.4, "raw": 0.05}


def _fusion_weight(obj, obj_age_s):
    """Weight for a temporal-object placement: tier × YOLO confidence ×
    hull-falloff × freshness. Used as the mean-fusion weight and as the
    #630 confidence signal."""
    tier = _FUSION_TIER_WEIGHT.get(obj.get("_method"), 0.05)
    yolo_conf = obj.get("confidence")
    if yolo_conf is None:
        yolo_conf = obj.get("_yoloConfidence", 0.5)
    # Freshness: 1.0 at t=0s, linearly decays to 0 at MAX_AGE_S.
    freshness = max(0.0, 1.0 - (obj_age_s / _FUSION_MAX_AGE_S))
    return max(0.0, tier * float(yolo_conf) * freshness)


def _fuse_temporal_objects():
    """Cluster near-duplicate temporal objects across cameras and replace
    each cluster with a single weighted-mean object. Runs on every reap
    (piggybacks on the existing /api/objects + bake-tick cadence).

    - Clusters grouped by (objectType, XY distance <= _FUSION_CLUSTER_MM).
    - Merge preserves the lowest id (sticky — #629 cross-camera handoff).
    - Output object stamps the fused sources' method tiers in _fusionTier
      (best of the cluster), total contributing cameras in _fusionCams,
      and the overall #630 confidence signal in _fusionConfidence.
    """
    global _temporal_objects
    items = list(_temporal_objects)
    now = time.time()
    fused = []
    used = [False] * len(items)
    for i, a in enumerate(items):
        if used[i]:
            continue
        if not a.get("_temporal"):
            fused.append(a); used[i] = True; continue
        cluster = [(i, a)]
        used[i] = True
        ap = a.get("transform", {}).get("pos", [0, 0, 0])
        ax, ay = float(ap[0] or 0), float(ap[1] or 0)
        atype = a.get("objectType")
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            b = items[j]
            if not b.get("_temporal") or b.get("objectType") != atype:
                continue
            bp = b.get("transform", {}).get("pos", [0, 0, 0])
            bx, by = float(bp[0] or 0), float(bp[1] or 0)
            d = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
            if d <= _FUSION_CLUSTER_MM:
                cluster.append((j, b))
                used[j] = True
        if len(cluster) == 1:
            fused.append(a)
            continue
        # Weighted mean over the cluster.
        total_w = 0.0
        px = py = pz = 0.0
        sw_w = sh_w = sd_w = 0.0
        sources = []
        best_tier = "raw"
        tier_order = {"homography": 2, "fov-projection": 1, "raw": 0}
        for _idx, obj in cluster:
            ex = obj.get("_expiresAt", now)
            age = max(0.0, now - (ex - (obj.get("ttl") or 0)))
            w = _fusion_weight(obj, age)
            if w <= 0:
                continue
            pos = obj.get("transform", {}).get("pos", [0, 0, 0])
            scl = obj.get("transform", {}).get("scale", [500, 1700, 500])
            total_w += w
            px += w * float(pos[0] or 0)
            py += w * float(pos[1] or 0)
            pz += w * float(pos[2] or 0)
            sw_w += w * float(scl[0] or 0)
            sh_w += w * float(scl[1] or 0)
            sd_w += w * float(scl[2] or 0)
            src_tier = obj.get("_method", "raw")
            if tier_order.get(src_tier, -1) > tier_order.get(best_tier, -1):
                best_tier = src_tier
            sources.append({
                "id": obj.get("id"),
                "cameraId": obj.get("_cameraId"),
                "method": src_tier,
                "weight": round(w, 3),
            })
        if total_w <= 0:
            fused.append(a); continue
        merged = dict(cluster[0][1])  # keep sticky id from lowest-id member
        merged["transform"] = {
            "pos": [px / total_w, py / total_w, pz / total_w],
            "rot": [0, 0, 0],
            "scale": [sw_w / total_w, sh_w / total_w, sd_w / total_w],
        }
        merged["_method"] = best_tier
        merged["_fusionCams"] = len(sources)
        merged["_fusionSources"] = sources
        # #630 confidence: mean contributing weight × breadth bonus for
        # multi-camera agreement. Single-camera observations cap at the
        # tier × YOLO product; multi-camera converges toward 1.0.
        base = total_w / len(sources)
        breadth_bonus = 1.0 - (0.5 ** max(0, len(sources) - 1))  # 0, 0.5, 0.75, 0.875, ...
        merged["_fusionConfidence"] = round(min(1.0, base * (1.0 + breadth_bonus)), 3)
        # #629 cross-camera handoff: persist the id across cluster merges
        # (already sticky via merged = dict(first)), but also remember
        # the object's last-seen absolute position for the next reap so
        # a brief blind-zone between cameras doesn't break the identity.
        merged["_lastXyMm"] = [px / total_w, py / total_w]
        merged["_lastSeenAt"] = now
        fused.append(merged)
    _temporal_objects = fused

@app.get("/api/objects")
def api_objects_get():
    _reap_temporal_objects()
    return jsonify(_objects + _temporal_objects)

@app.post("/api/objects")
def api_objects_create():
    global _nxt_obj
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    with _lock:
        s = {
            "id": _nxt_obj, "name": name or f"Object {_nxt_obj}",
            "objectType": body.get("objectType", "custom"),
            "mobility": body.get("mobility", "static"),
            "filename": body.get("filename", ""),
            "color": body.get("color", "#334155"),
            "opacity": body.get("opacity", 30),
            "transform": body.get("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]}),
            "stageLocked": body.get("stageLocked", False),
        }
        if "patrol" in body and isinstance(body["patrol"], dict):
            s["patrol"] = body["patrol"]
        if s["stageLocked"]:
            _apply_stage_lock(s)
        _objects.append(s)
        _nxt_obj += 1
        _save("objects", _objects)
    return jsonify(ok=True, id=s["id"])

@app.delete("/api/objects/<int:sid>")
def api_object_delete(sid):
    global _objects, _temporal_objects
    before = len(_objects) + len(_temporal_objects)
    _objects = [s for s in _objects if s["id"] != sid]
    _temporal_objects = [s for s in _temporal_objects if s["id"] != sid]
    if len(_objects) + len(_temporal_objects) < before:
        _save("objects", _objects)
    return jsonify(ok=True)

@app.put("/api/objects/<int:oid>/pos")
def api_object_pos(oid):
    body = request.get_json(silent=True) or {}
    pos = body.get("pos")
    if not pos or not isinstance(pos, list) or len(pos) != 3:
        return jsonify(err="pos must be [x, y, z]"), 400
    # Pixel→stage conversion if camera data provided
    cam_id = body.get("cameraId")
    pixel_box = body.get("pixelBox")
    frame_size = body.get("frameSize")
    if cam_id is not None and pixel_box and frame_size:
        fw, fh = frame_size
        cx = (pixel_box["x"] + pixel_box.get("w", 0) / 2) / fw
        cy = (pixel_box["y"] + pixel_box.get("h", 0) / 2) / fh
        sw = _stage.get("w", 3.0) * 1000
        sd = _stage.get("d", 4.0) * 1000
        pos = [sw * (1.0 - cx), sd * (1.0 - cy), 0]
    with _lock:
        obj = next((o for o in _objects if o["id"] == oid), None)
        if not obj:
            obj = next((o for o in _temporal_objects if o["id"] == oid), None)
        if not obj:
            return jsonify(err="not found"), 404
        obj.setdefault("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]})["pos"] = [float(p) for p in pos]
        if obj.get("_temporal") and obj.get("ttl"):
            obj["_expiresAt"] = time.time() + obj["ttl"]
    return jsonify(ok=True)

@app.post("/api/objects/temporal")
def api_objects_temporal_create():
    global _nxt_tmp
    body = request.get_json(silent=True) or {}
    ttl = body.get("ttl")
    if not isinstance(ttl, (int, float)) or ttl <= 0:
        return jsonify(err="ttl must be > 0"), 400
    pos = body.get("pos", [0, 0, 0])
    scale = body.get("scale", [500, 1800, 500])
    # Q1/Q5 — pixel ingest path. Project the bbox through the camera's
    # calibrated homography (fallback: FOV projection, then raw). Tier
    # stamped on the object so track-actions can hold last-good when
    # cal is missing and so the SPA can surface accuracy.
    cam_id = body.get("cameraId")
    pixel_box = body.get("pixelBox")  # {x, y, w, h}
    frame_size = body.get("frameSize")  # [w, h]
    method_tier = None
    anchors = None
    if cam_id is not None and pixel_box and frame_size:
        cam_fixture = next((f for f in _fixtures
                            if f.get("id") == cam_id
                            and f.get("fixtureType") == "camera"), None)
        anchors = _pixel_box_to_stage_anchors(cam_fixture, pixel_box, frame_size)
        if anchors:
            method_tier = anchors["method"]
            # scale: bbox-derived width & depth — best available without
            # stereo reconstruction. Height from default 1700 mm
            # (#Q1 sizing refinement is follow-up work, not in scope).
            fw = frame_size[0] or 1
            sw = _stage.get("w", 3.0) * 1000
            obj_w_mm = max(100.0, float(pixel_box.get("w", 100)) * sw / fw)
            scale = [obj_w_mm, anchors["heightMm"], 400.0]
            # pos is the object CENTER. Feet-at-Z=0, head-at-height → center
            # lives on the vertical axis through feet at height/2.
            pos = list(anchors["center"])
    with _lock:
        obj = {
            "id": _nxt_tmp, "name": body.get("name", f"Temporal {_nxt_tmp}"),
            "objectType": body.get("objectType", "prop"),
            "mobility": "moving",
            "_temporal": True,
            "ttl": ttl,
            "_expiresAt": time.time() + ttl,
            "color": body.get("color", "#FF6B35"),
            "opacity": body.get("opacity", 40),
            "transform": {"pos": [float(p) for p in pos], "rot": [0,0,0], "scale": [float(s) for s in scale]},
        }
        # #Q5 — record the placement method tier so downstream consumers
        # (Track actions, SPA badges) can treat low-confidence placements
        # conservatively.
        if method_tier:
            obj["_method"] = method_tier
        # Q3/#629 — track which camera pushed this placement, so the
        # fusion pass can surface per-camera contributions in
        # _fusionSources.
        if cam_id is not None:
            obj["_cameraId"] = cam_id
        # Q3/#630 — forward the YOLO confidence if the tracker provided
        # one. Feeds _fusion_weight alongside the method tier.
        if "confidence" in body:
            obj["_yoloConfidence"] = float(body["confidence"])
        # #Q4 — stash feet/head anchors so track-actions with aimTarget
        # can pick the right stage-point without recomputing.
        if anchors:
            obj["_anchors"] = {
                "feet": [float(v) for v in anchors["feet"]],
                "center": [float(v) for v in anchors["center"]],
                "head": [float(v) for v in anchors["head"]],
            }
        _temporal_objects.append(obj)
        _nxt_tmp += 1
    return jsonify(ok=True, id=obj["id"], method=method_tier)

#  "  "  DMX Profiles  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/dmx-profiles")
def api_dmx_profiles():
    cat = request.args.get("category")
    return jsonify(_profile_lib.list_profiles(category=cat))

@app.post("/api/dmx-profiles")
def api_dmx_profile_create():
    body = request.get_json(silent=True) or {}
    ok_valid, err = _profile_lib.validate_profile(body)
    if not ok_valid:
        return jsonify(err=err), 400
    if _profile_lib.save_profile(body):
        return jsonify(ok=True, id=body["id"])
    return jsonify(err="Failed to save"), 500

# Static sub-paths BEFORE parameterized <profile_id>
@app.get("/api/dmx-profiles/export")
def api_dmx_profiles_export():
    ids = request.args.get("ids")
    category = request.args.get("category")
    id_list = [s.strip() for s in ids.split(",") if s.strip()] if ids else None
    profiles = _profile_lib.export_profiles(ids=id_list, category=category)
    return jsonify(profiles)

@app.post("/api/dmx-profiles/import")
def api_dmx_profiles_import():
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify(err="Body must be a JSON array of profiles"), 400
    result = _profile_lib.import_profiles(data)
    return jsonify(ok=True, **result)

# OFL data cache
_ofl_mfr_cache = {"data": None, "ts": 0}   # manufacturer index (name + fixtureCount)
_ofl_fix_cache = {}                          # mfr_key → [fixture dicts]
_OFL_CACHE_TTL = 3600

def _ofl_fetch_manufacturer_index():
    """Fetch manufacturer index (name + fixtureCount only, no fixture lists)."""
    import urllib.request as _ur
    now = time.time()
    if _ofl_mfr_cache["data"] and now - _ofl_mfr_cache["ts"] < _OFL_CACHE_TTL:
        return _ofl_mfr_cache["data"]
    url = "https://open-fixture-library.org/api/v1/manufacturers"
    req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
    resp = _ur.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    _ofl_mfr_cache["data"] = data
    _ofl_mfr_cache["ts"] = now
    log.info("OFL: cached %d manufacturers", len(data))
    return data

def _ofl_fetch_manufacturer_fixtures(mfr_key):
    """Fetch fixtures for a specific manufacturer (cached)."""
    import urllib.request as _ur
    if mfr_key in _ofl_fix_cache:
        return _ofl_fix_cache[mfr_key]
    url = f"https://open-fixture-library.org/api/v1/manufacturers/{mfr_key}"
    req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
    resp = _ur.urlopen(req, timeout=15)
    data = json.loads(resp.read().decode("utf-8"))
    fixtures = data.get("fixtures", [])
    _ofl_fix_cache[mfr_key] = {"name": data.get("name", mfr_key), "fixtures": fixtures}
    return _ofl_fix_cache[mfr_key]

@app.get("/api/dmx-profiles/ofl/manufacturers")
def api_ofl_manufacturers():
    """List all OFL manufacturers with fixture counts."""
    try:
        data = _ofl_fetch_manufacturer_index()
        result = []
        for mfr_key, mfr in sorted(data.items()):
            if not isinstance(mfr, dict):
                continue
            count = mfr.get("fixtureCount", 0)
            if count <= 0:
                continue
            result.append({
                "key": mfr_key,
                "name": mfr.get("name", mfr_key),
                "fixtureCount": count,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify(err=f"OFL fetch failed: {e}"), 502

@app.get("/api/dmx-profiles/ofl/manufacturer/<mfr_key>")
def api_ofl_manufacturer_fixtures(mfr_key):
    """List all fixtures for a specific manufacturer."""
    try:
        mfr_data = _ofl_fetch_manufacturer_fixtures(mfr_key)
        fixtures = mfr_data.get("fixtures", [])
        return jsonify({
            "key": mfr_key,
            "name": mfr_data.get("name", mfr_key),
            "fixtures": [{"key": f.get("key", f) if isinstance(f, dict) else f,
                          "name": f.get("name", f.get("key","?")) if isinstance(f, dict) else f.replace("-"," ").title(),
                          "categories": f.get("categories", []) if isinstance(f, dict) else []}
                         for f in fixtures],
        })
    except Exception as e:
        return jsonify(err=f"OFL fetch failed: {e}"), 502

# Full fixture index: flat list of all fixtures across all manufacturers
_ofl_full_index = {"data": None, "ts": 0}

def _ofl_build_full_index():
    """Build a flat searchable index of ALL OFL fixtures. Fetches all manufacturers."""
    import urllib.request as _ur
    from concurrent.futures import ThreadPoolExecutor
    now = time.time()
    if _ofl_full_index["data"] and now - _ofl_full_index["ts"] < _OFL_CACHE_TTL:
        return _ofl_full_index["data"]
    mfr_index = _ofl_fetch_manufacturer_index()
    mfr_keys = [k for k, m in mfr_index.items()
                if isinstance(m, dict) and m.get("fixtureCount", 0) > 0]
    log.info("OFL: building full index from %d manufacturers...", len(mfr_keys))
    all_fixtures = []
    def fetch_one(mfr_key):
        try:
            data = _ofl_fetch_manufacturer_fixtures(mfr_key)
            mfr_name = data.get("name", mfr_key)
            results = []
            for f in data.get("fixtures", []):
                fkey = f.get("key", f) if isinstance(f, dict) else f
                fname = f.get("name", fkey) if isinstance(f, dict) else fkey.replace("-", " ").title()
                cats = f.get("categories", []) if isinstance(f, dict) else []
                results.append({"manufacturer": mfr_key, "manufacturerName": mfr_name,
                                "fixture": fkey, "name": fname, "categories": cats})
            return results
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in pool.map(fetch_one, mfr_keys):
            all_fixtures.extend(batch)
    _ofl_full_index["data"] = all_fixtures
    _ofl_full_index["ts"] = now
    log.info("OFL: full index built — %d fixtures from %d manufacturers", len(all_fixtures), len(mfr_keys))
    return all_fixtures

@app.get("/api/dmx-profiles/ofl/search")
def api_dmx_profiles_ofl_search():
    """Search ALL OFL fixtures by name, manufacturer, or category."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify(err="Query must be at least 2 characters"), 400
    limit = min(int(request.args.get("limit", 100)), 500)
    try:
        all_fixtures = _ofl_build_full_index()
        ql = q.lower()
        results = []
        for f in all_fixtures:
            if (ql in f["fixture"].lower() or ql in f["name"].lower()
                    or ql in f["manufacturerName"].lower() or ql in f["manufacturer"]
                    or any(ql in cat.lower() for cat in f.get("categories", []))):
                results.append(f)
                if len(results) >= limit:
                    break
        return jsonify(results)
    except Exception as e:
        return jsonify(err=f"OFL search failed: {e}"), 502

@app.get("/api/dmx-profiles/ofl/browse")
def api_dmx_profiles_ofl_browse():
    """Browse ALL OFL fixtures. Returns full index (cached). ?offset=0&limit=100."""
    offset = int(request.args.get("offset", 0))
    limit = min(int(request.args.get("limit", 100)), 500)
    try:
        all_fixtures = _ofl_build_full_index()
        page = all_fixtures[offset:offset + limit]
        return jsonify({"total": len(all_fixtures), "offset": offset, "fixtures": page})
    except Exception as e:
        return jsonify(err=f"OFL browse failed: {e}"), 502

@app.post("/api/dmx-profiles/ofl/import-by-id")
def api_dmx_profiles_ofl_import_by_id():
    """Fetch fixture(s) from OFL and import. Body: {manufacturer, fixture} or {manufacturer} for all."""
    import urllib.request as _ur
    body = request.get_json(silent=True) or {}
    manufacturer = body.get("manufacturer", "").strip()
    fixture = body.get("fixture", "").strip()
    mode_idx = body.get("mode")
    if not manufacturer:
        return jsonify(err="manufacturer required"), 400

    from ofl_importer import ofl_to_slyled
    all_profiles = []
    errors = []

    # Single fixture or all from manufacturer
    if fixture:
        fixture_keys = [fixture]
    else:
        try:
            mfr_data = _ofl_fetch_manufacturer_fixtures(manufacturer)
            raw_fixtures = mfr_data.get("fixtures", [])
            fixture_keys = [f.get("key", f) if isinstance(f, dict) else f for f in raw_fixtures]
        except Exception as e:
            return jsonify(err=f"Could not fetch manufacturer: {e}"), 502

    for fix_key in fixture_keys:
        try:
            url = f"https://open-fixture-library.org/{manufacturer}/{fix_key}.json"
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent", "Accept": "application/json"})
            resp = _ur.urlopen(req, timeout=15)
            ofl_json = json.loads(resp.read().decode("utf-8"))
            profiles = ofl_to_slyled(ofl_json, mode=mode_idx)
            all_profiles.extend(profiles)
        except Exception as e:
            errors.append(f"{fix_key}: {e}")
            log.debug("OFL import %s/%s failed: %s", manufacturer, fix_key, e)

    if not all_profiles:
        return jsonify(err=f"No profiles converted. Errors: {'; '.join(errors[:5])}"), 400

    result = _profile_lib.import_profiles(all_profiles)
    resp = {"ok": True, **result,
            "profiles": [{"id": p["id"], "name": p["name"], "channels": p["channelCount"]} for p in all_profiles]}
    if errors:
        resp["warnings"] = errors[:10]
    return jsonify(resp)

@app.post("/api/dmx-profiles/ofl/import-json")
def api_dmx_profiles_ofl_import():
    """Import OFL fixture JSON directly (paste or upload)."""
    body = request.get_json(silent=True) or {}
    ofl_json = body.get("ofl") or body
    mode_idx = body.get("mode")
    if "ofl" in body:
        ofl_json = body["ofl"]
    from ofl_importer import ofl_to_slyled
    profiles = ofl_to_slyled(ofl_json, mode=mode_idx)
    if not profiles:
        return jsonify(err="Could not convert OFL fixture (no valid modes/channels)"), 400
    result = _profile_lib.import_profiles(profiles)
    return jsonify(ok=True, profiles=[p["id"] for p in profiles], **result)

# ── Community Profile Server ─────────────────────────────────────────────

@app.get("/api/dmx-profiles/community/search")
def api_community_search():
    import community_client as cc
    q = request.args.get("q", "")
    cat = request.args.get("category")
    limit = int(request.args.get("limit", 50))
    return jsonify(cc.search(q, cat, limit))

@app.get("/api/dmx-profiles/community/recent")
def api_community_recent():
    import community_client as cc
    return jsonify(cc.recent(int(request.args.get("limit", 20))))

@app.get("/api/dmx-profiles/community/popular")
def api_community_popular():
    import community_client as cc
    return jsonify(cc.popular(int(request.args.get("limit", 20))))

@app.get("/api/dmx-profiles/community/stats")
def api_community_stats():
    import community_client as cc
    return jsonify(cc.stats())

#: Fields the community API either does not understand or regenerates
# server-side. They only exist on the local record for bookkeeping and
# shipping them on upload wastes bytes against the size ceiling. #605.
_COMMUNITY_UPLOAD_STRIP = frozenset({
    "builtin",              # local library's built-in marker
    "_community",           # local sync state (channelHash/slug/syncedAt/uploadTs)
    "communityDownloads",   # server-maintained counter
    "communityUploadTs",    # server-authoritative; stamped into _community on download
    "communityChannelHash", # server-computed hash; stamped into _community on download
})

#: Byte headroom below which we warn the operator. At 5% of ceiling a
# small future edit is likely to bounce — worth flagging before the
# round-trip fails opaquely. Kept as a module-level constant so #606
# (server-side limit raise) can bump the ceiling and this stays in sync.
_COMMUNITY_UPLOAD_CEILING = 32768  # #606 raised from 8192
_COMMUNITY_UPLOAD_WARN_FRACTION = 0.95  # warn when ≥95% of ceiling


def _prepare_community_payload(profile_id):
    """Shared payload builder for community upload + update routes.

    Strips local bookkeeping fields (#605) so the outbound JSON carries
    only what the server actually persists. Returns `(payload, None)`
    on success or `(None, (msg, status))` on error.
    """
    profile = _profile_lib.get_profile(profile_id)
    if not profile:
        return None, ("Profile not found locally", 404)
    import re
    p = {k: v for k, v in profile.items() if k not in _COMMUNITY_UPLOAD_STRIP}
    slug = re.sub(r'[^a-z0-9\-]', '-', p.get("id", "").lower())
    slug = re.sub(r'-+', '-', slug).strip('-')[:128]
    if not slug:
        return None, ("Profile ID cannot be converted to a valid slug", 400)
    p["id"] = slug
    return p, None


def _community_payload_size_info(p):
    """Return byte-size telemetry for an outbound payload (#605).

    Mirrors how `community_client.upload/update` frames the request:
    `{"profile": p}` serialized with the same separators the Python
    stdlib JSON defaults to. Lets the Flask route report "your profile
    is N bytes, ceiling is M" before the HTTP round-trip, so rejections
    don't look mysterious.

    Returned keys: `bytes`, `ceiling`, `headroom`, `nearLimit` (bool).
    """
    wire = json.dumps({"profile": p}, separators=(",", ":"))
    size = len(wire.encode("utf-8"))
    headroom = _COMMUNITY_UPLOAD_CEILING - size
    near = size >= int(_COMMUNITY_UPLOAD_CEILING * _COMMUNITY_UPLOAD_WARN_FRACTION)
    return {
        "bytes": size,
        "ceiling": _COMMUNITY_UPLOAD_CEILING,
        "headroom": headroom,
        "nearLimit": near,
    }


@app.post("/api/dmx-profiles/community/upload")
def api_community_upload():
    """Upload a local profile to the community server. If ``overwrite:
    true`` is in the body and the slug already exists, the call falls
    back to the `update` action so operators can re-publish a revised
    version of their own profile in one request."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profileId")
    overwrite = bool(body.get("overwrite"))
    if not profile_id:
        return jsonify(ok=False, err="profileId required"), 400
    p, err = _prepare_community_payload(profile_id)
    if err:
        msg, code = err
        return jsonify(ok=False, err=msg), code
    # #605 — surface payload size so operators see "7994 / 32768 bytes"
    # instead of a generic "upload failed" when the server rejects.
    size = _community_payload_size_info(p)
    if size["headroom"] < 0:
        return jsonify(ok=False,
                        err=f"Profile too large ({size['bytes']} bytes, "
                            f"ceiling {size['ceiling']}). Trim capability "
                            f"annotations or open an issue to raise the limit.",
                        payloadBytes=size["bytes"],
                        ceilingBytes=size["ceiling"]), 413
    result = cc.upload(p)
    # Fall through to `update` when the server rejected the insert
    # because the slug already exists and the caller asked for overwrite.
    if overwrite and isinstance(result, dict) and not result.get("ok"):
        err_msg = (result.get("error") or "").lower()
        if "already exists" in err_msg:
            log.info("Community upload '%s': slug exists → retrying as update", p["id"])
            result = cc.update(p)
    log.info("Community upload '%s' (slug '%s'): %d bytes → %s",
             profile_id, p["id"], size["bytes"], result)
    if isinstance(result, dict):
        result.setdefault("payloadBytes", size["bytes"])
        result.setdefault("ceilingBytes", size["ceiling"])
        result.setdefault("nearLimit", size["nearLimit"])
    return jsonify(result)


@app.get("/api/dmx-profiles/community/peek")
def api_community_peek():
    """Fetch a community profile WITHOUT importing it locally.

    The Share/Update wizard calls this to build the diff view: we need
    the remote profile in-memory for comparison, but we don't want to
    stomp the operator's local copy until they've confirmed the update.
    """
    import community_client as cc
    slug = (request.args.get("slug") or "").strip()
    if not slug:
        return jsonify(ok=False, err="slug required"), 400
    result = cc.get_profile(slug)
    if not isinstance(result, dict) or not result.get("ok"):
        # Community returns 404 for missing — surface as ok:false with the
        # flag the SPA needs to pick the "new upload" path.
        err = result.get("error") if isinstance(result, dict) else "Fetch failed"
        return jsonify(ok=False, err=err, notFound="not found" in (err or "").lower())
    return jsonify(ok=True, profile=result.get("data") or result)


@app.post("/api/dmx-profiles/community/update")
def api_community_update():
    """Overwrite an existing community profile (same slug). Requires
    the caller's IP to match the original uploader server-side."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profileId")
    if not profile_id:
        return jsonify(ok=False, err="profileId required"), 400
    p, err = _prepare_community_payload(profile_id)
    if err:
        msg, code = err
        return jsonify(ok=False, err=msg), code
    size = _community_payload_size_info(p)
    if size["headroom"] < 0:
        return jsonify(ok=False,
                        err=f"Profile too large ({size['bytes']} bytes, "
                            f"ceiling {size['ceiling']}). Trim capability "
                            f"annotations or open an issue to raise the limit.",
                        payloadBytes=size["bytes"],
                        ceilingBytes=size["ceiling"]), 413
    result = cc.update(p)
    log.info("Community update '%s' (slug '%s'): %d bytes → %s",
             profile_id, p["id"], size["bytes"], result)
    if isinstance(result, dict):
        result.setdefault("payloadBytes", size["bytes"])
        result.setdefault("ceilingBytes", size["ceiling"])
        result.setdefault("nearLimit", size["nearLimit"])
    return jsonify(result)

def _stamp_community_provenance(profile, slug):
    """#534 — tag a freshly-downloaded community profile with the
    `_community` block so later check_updates calls can detect drift.

    Reads the server's response-only fields (communityUploadTs +
    communityChannelHash), moves them into the private `_community`
    sub-dict, and drops the top-level duplicates so the profile that
    ends up in the editor isn't polluted with transient fields.
    """
    import time as _time
    upload_ts = profile.pop("communityUploadTs", None)
    channel_hash = profile.pop("communityChannelHash", None)
    if not (upload_ts or channel_hash):
        return
    profile["_community"] = {
        "slug": slug,
        "uploadTs": upload_ts or "",
        "channelHash": channel_hash or "",
        "syncedAt": int(_time.time()),
    }


@app.post("/api/dmx-profiles/community/download")
def api_community_download():
    """Download a community profile and import it locally. Stamps the
    `_community` provenance block so the Profile Library can later
    detect when the remote has been updated (#534)."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    slug = body.get("slug", "").strip()
    if not slug:
        return jsonify(ok=False, err="slug required"), 400
    result = cc.get_profile(slug)
    if not result or not result.get("ok"):
        return jsonify(ok=False, err=result.get("error", "Fetch failed")), 502
    profile = result.get("data", result)
    if isinstance(profile, dict) and "id" in profile:
        _stamp_community_provenance(profile, slug)
        imported = _profile_lib.import_profiles([profile])
        log.info("Community download '%s': %s", slug, imported)
        if imported.get("errors"):
            log.warning("Community download errors: %s", imported["errors"])
        return jsonify(ok=True, **imported)
    log.warning("Community download '%s': invalid data — keys=%s", slug,
                list(profile.keys()) if isinstance(profile, dict) else type(profile).__name__)
    return jsonify(ok=False, err="Invalid profile data"), 400


@app.post("/api/dmx-profiles/community/check-updates")
def api_community_check_updates():
    """Batch-check every locally-tracked community profile for newer
    versions on the server. Builds the slug/knownTs pairs from the
    profiles that carry a `_community` provenance block and proxies to
    `community_client.check_updates`.
    """
    import community_client as cc
    pairs = []
    tracked_profiles = {}
    for pid in list(_profile_lib._profiles.keys()):
        p = _profile_lib._profiles.get(pid) or {}
        cm = p.get("_community") or {}
        slug = cm.get("slug")
        if not slug:
            continue
        tracked_profiles[slug] = pid
        pairs.append({"slug": slug, "knownTs": cm.get("uploadTs", "")})
    if not pairs:
        return jsonify(ok=True, tracked=0, updates=[])
    result = cc.check_updates(pairs) or {}
    if not result.get("ok"):
        return jsonify(ok=False, err=result.get("error", "Check failed")), 502
    data = result.get("data") or {}
    updates = []
    for u in (data.get("updates") or []):
        slug = u.get("slug")
        if not slug:
            continue
        updates.append({
            "slug": slug,
            "profileId": tracked_profiles.get(slug, slug),
            "name": u.get("name"),
            "uploadTs": u.get("uploadTs"),
            "channelHash": u.get("channelHash"),
        })
    return jsonify(ok=True, tracked=len(pairs), updates=updates)

@app.post("/api/dmx-profiles/community/check")
def api_community_check():
    """Check if a profile would be a duplicate on the community server."""
    import community_client as cc
    body = request.get_json(silent=True) or {}
    profile_id = body.get("profileId")
    if not profile_id:
        return jsonify(ok=False, err="profileId required"), 400
    profile = _profile_lib.get_profile(profile_id)
    if not profile:
        return jsonify(ok=False, err="Profile not found"), 404
    import re as _re
    p = {k: v for k, v in profile.items() if k != "builtin"}
    slug = _re.sub(r'[^a-z0-9\-]', '-', p.get("id", "").lower())
    slug = _re.sub(r'-+', '-', slug).strip('-')[:128]
    if slug:
        p["id"] = slug
    return jsonify(cc.check_duplicate(p))

@app.get("/api/dmx-profiles/unified-search")
def api_unified_search():
    """Search local + community + OFL in one call."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify(err="Query must be at least 2 characters"), 400
    ql = q.lower()
    results = []
    seen = set()
    # 1. Local profiles (instant)
    for p in _profile_lib.list_profiles():
        if ql in p.get("name", "").lower() or ql in p.get("manufacturer", "").lower() or ql in p.get("id", "").lower():
            results.append({"id": p["id"], "name": p["name"], "manufacturer": p.get("manufacturer", ""),
                            "category": p.get("category", ""), "channelCount": p.get("channelCount", 0),
                            "source": "local", "builtin": p.get("builtin", False)})
            seen.add(p["id"])
    # 2. Community (fast)
    try:
        import community_client as cc
        cr = cc.search(q, limit=20)
        data = cr.get("data", cr)
        profiles = data.get("profiles", data) if isinstance(data, dict) else data
        for p in (profiles if isinstance(profiles, list) else []):
            slug = p.get("slug", "")
            if slug and slug not in seen:
                results.append({"id": slug, "name": p.get("name", slug), "manufacturer": p.get("manufacturer", ""),
                                "channelCount": int(p.get("channel_count", 0)), "source": "community"})
                seen.add(slug)
    except Exception:
        pass
    # 3. OFL (if still need more)
    if len(results) < 30:
        try:
            for f in _ofl_build_full_index():
                fk = f.get("fixture", "")
                if fk in seen: continue
                if ql in fk.lower() or ql in f.get("name", "").lower() or ql in f.get("manufacturerName", "").lower():
                    results.append({"id": fk, "name": f.get("name", fk), "manufacturer": f.get("manufacturerName", ""),
                                    "source": "ofl", "oflMfr": f.get("manufacturer", "")})
                    seen.add(fk)
                    if len(results) >= 50: break
        except Exception:
            pass
    return jsonify(results[:50])

# Parameterized routes AFTER static paths
@app.get("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_get(profile_id):
    p = _profile_lib.get_profile(profile_id)
    if not p:
        return jsonify(err="Not found"), 404
    return jsonify(p)

@app.put("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_update(profile_id):
    body = request.get_json(silent=True) or {}
    ok_upd, err = _profile_lib.update_profile(profile_id, body)
    if not ok_upd:
        p = _profile_lib.get_profile(profile_id)
        code = 400 if p else 404
        return jsonify(err=err), code
    return jsonify(ok=True)

@app.delete("/api/dmx-profiles/<profile_id>")
def api_dmx_profile_delete(profile_id):
    if _profile_lib.delete_profile(profile_id):
        return jsonify(ok=True)
    return jsonify(err="Cannot delete (built-in or not found)"), 400

#  "  "  DMX Patch / Conflicts  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/dmx/patch")
def api_dmx_patch():
    """Return DMX address map per universe with conflict detection."""
    dmx_fixtures = [f for f in _fixtures if f.get("fixtureType") == "dmx"]
    universes = {}
    conflicts = []
    for f in dmx_fixtures:
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        count = f.get("dmxChannelCount", 1)
        if uni not in universes:
            universes[uni] = []
        entry = {"id": f["id"], "name": f.get("name", "?"), "startAddr": addr,
                 "channelCount": count, "endAddr": addr + count - 1,
                 "profileId": f.get("dmxProfileId")}
        # Check for overlaps within this universe
        for existing in universes[uni]:
            if addr <= existing["endAddr"] and existing["startAddr"] <= addr + count - 1:
                conflicts.append({
                    "universe": uni,
                    "fixtures": [existing["name"], entry["name"]],
                    "overlapStart": max(addr, existing["startAddr"]),
                    "overlapEnd": min(addr + count - 1, existing["endAddr"]),
                })
        universes[uni].append(entry)
    return jsonify(universes=universes, conflicts=conflicts,
                   totalFixtures=len(dmx_fixtures), totalConflicts=len(conflicts))

#  "  "  DMX Output Engines  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/dmx/status")
def api_dmx_status():
    return jsonify(
        artnet=_artnet.status(),
        sacn=_sacn.status(),
    )

def _set_fixture_color(engine_or_buf, uni_or_addr, addr_or_none, r, g, b, prof_info):
    """Set color on a fixture — RGB or color-wheel depending on profile.
    Accepts (engine, uni, addr, ...) or (uni_buf, addr, None, ...)."""
    from dmx_profiles import rgb_to_wheel_slot
    if addr_or_none is not None:
        # Engine mode: engine.set_fixture_rgb(uni, addr, ...)
        engine, uni, addr = engine_or_buf, uni_or_addr, addr_or_none
        cm = prof_info.get("channel_map", {}) if prof_info else {}
        if "red" in cm or not cm:
            profile = {"channel_map": cm} if cm else None
            engine.set_fixture_rgb(uni, addr, r, g, b, profile)
        elif "color-wheel" in cm:
            cw = rgb_to_wheel_slot(prof_info, r, g, b) if (r or g or b) else 0
            engine.get_universe(uni).set_channel(addr + cm["color-wheel"], cw)
    else:
        # Buffer mode: uni_buf.set_fixture_rgb(addr, ...)
        uni_buf, addr = engine_or_buf, uni_or_addr
        cm = prof_info.get("channel_map", {}) if prof_info else {}
        if "red" in cm or not cm:
            profile = {"channel_map": cm, "channels": prof_info.get("channels", [])} if cm else None
            uni_buf.set_fixture_rgb(addr, r, g, b, profile)
        elif "color-wheel" in cm:
            cw = rgb_to_wheel_slot(prof_info, r, g, b) if (r or g or b) else 0
            uni_buf.set_channel(addr + cm["color-wheel"], cw)

# ── Remote-orientation primitive (#484) — initialised first so the
#    mover-follow engine below can read it. ────────────────────────────────

from remote_orientation import RemoteRegistry, KIND_PUCK, KIND_PHONE
from mover_calibrator import pan_tilt_to_ray as _pan_tilt_to_ray

_remotes = RemoteRegistry(data_path=str(DATA / "remotes.json"))
_remotes.load()

# ── Parametric calibration model (#489-#494) ───────────────────────────────
#
# Lazy per-fixture cache: first access fits the v2 model from samples (or
# loads pre-fitted params from `cal["model"]`) and stores the result here.
# Invalidated on calibration save/delete so re-calibration is a fresh fit.
from parametric_mover import ParametricFixtureModel, fit_model as _fit_model

_mover_models = {}  # fixture_id (int) → ParametricFixtureModel


def _fixture_position(fid):
    """Stage-space position for a fixture. Layout holds x/y/z keyed by id;
    the fixture record itself only has the metadata fields."""
    for c in (_layout.get("children") or []):
        if c.get("id") == fid:
            return (c.get("x", 0) or 0, c.get("y", 0) or 0, c.get("z", 0) or 0)
    return (0.0, 0.0, 0.0)


# ── Calibration lock (#511) ────────────────────────────────────────────────
#
# Runtime-only flag on the fixture record. When a calibration run is active
# the lock blocks every other DMX writer (mover-control, show/bake playback,
# test panel, profile-defaults re-seed) so the cal thread's beam samples
# aren't corrupted by a concurrent pan/tilt write. Not persisted — cleared
# on server start so a crash mid-calibration doesn't orphan the flag.

def _fixture_is_calibrating(fid):
    if fid is None:
        return False
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    return bool(f and f.get("isCalibrating"))


def _set_calibrating(fid, val):
    """Toggle the fixture-level calibration lock. Idempotent."""
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f:
        return
    if val:
        f["isCalibrating"] = True
        log.info("Mover %d: calibration lock engaged — external DMX writes blocked", fid)
    else:
        if f.pop("isCalibrating", None):
            log.info("Mover %d: calibration lock released", fid)


# Clear stale locks from any crash-induced persistence leak.
for _f in _fixtures:
    _f.pop("isCalibrating", None)


def _get_mover_model(fid, mover=None):
    """Return the ParametricFixtureModel for a fixture, fitting lazily.

    Migration path:
      - v2 (`cal["version"] == 2` and `cal["model"]` present) → load directly.
      - v1 (`cal["samples"]` with ≥ 2 entries, no "model") → LM-fit, persist
        as v2 inline, save, return.
      - No calibration → None.

    Result is cached in `_mover_models`. Call ``_invalidate_mover_model(fid)``
    whenever samples change.
    """
    fid = int(fid)
    cached = _mover_models.get(fid)
    if cached is not None:
        return cached

    cal = _mover_cal.get(str(fid))
    if not cal:
        return None

    pos = _fixture_position(fid)
    if mover is None:
        mover = next((f for f in _fixtures if f.get("id") == fid), {}) or {}
    prof = _profile_lib.channel_info(mover.get("dmxProfileId")) \
        if mover.get("dmxProfileId") else None
    pan_range = mover.get("panRange") \
        or (prof.get("panRange") if prof else None) or 540
    tilt_range = mover.get("tiltRange") \
        or (prof.get("tiltRange") if prof else None) or 270

    # v2 fast path.
    if cal.get("version") == 2 and cal.get("model"):
        try:
            model = ParametricFixtureModel.from_dict(pos, cal["model"])
        except Exception as e:
            log.warning("Mover %d v2 model load failed: %s — re-fitting", fid, e)
        else:
            _mover_models[fid] = model
            return model

    # v1 migration: fit from samples, persist inline as v2 additive.
    samples = cal.get("samples") or []
    if len(samples) < 2:
        return None

    try:
        model, quality = _fit_model(
            pos, pan_range, tilt_range, samples,
            mounted_inverted=bool(mover.get("mountedInverted")),
        )
    except Exception as e:
        log.warning("Mover %d v2 fit failed: %s — keeping v1 affine", fid, e)
        return None

    cal["version"] = 2
    cal["model"] = model.to_dict()
    cal["fit"] = quality.to_dict()
    _save("mover_calibrations", _mover_cal)
    _mover_models[fid] = model
    log.info("Mover %d: migrated v1→v2 calibration, rms=%.2f° max=%.2f° samples=%d",
             fid, quality.rms_error_deg, quality.max_error_deg, quality.sample_count)
    return model


def _invalidate_mover_model(fid):
    """Drop the cached model so the next ``_get_mover_model`` call refits."""
    _mover_models.pop(int(fid), None)


# ── Mover-follow engine (#468) — consumer of the primitive (#484 phase 4) ──
from mover_control import MoverControlEngine

_mover_engine = MoverControlEngine(
    get_fixtures=lambda: _fixtures,
    get_layout=lambda: _layout,
    get_profile_info=lambda pid: _profile_lib.channel_info(pid) if pid else None,
    get_engine=lambda: _artnet if _artnet.running else (_sacn if _sacn.running else None),
    set_fixture_color_fn=_set_fixture_color,
    get_remote_by_device_id=lambda did: _remotes.by_device(did),
    get_mover_cal=lambda mid: _mover_cal.get(str(mid)),
    get_mover_model=_get_mover_model,
    is_calibrating=_fixture_is_calibrating,
)
_mover_engine.start()

@app.post("/api/mover-control/claim")
def api_mover_claim():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId", "")
    dname = body.get("deviceName", "Unknown")
    dtype = body.get("deviceType", "android")
    sm = body.get("smoothing", 0.15)
    if mid is None:
        return jsonify(ok=False, err="moverId required"), 400
    ok, reason = _mover_engine.claim(mid, did, dname, dtype, smoothing=sm)
    if not ok:
        return jsonify(ok=False, err=reason), 409
    # #492 — when an Android phone claims a mover it supplies its own
    # hostname via deviceName ("Pixel 9 Pro XL" etc.). Stamp that onto
    # the Remote record so the dashboard can render a human name
    # instead of the raw GUID we auto-registered during the first
    # orient packet.
    if did:
        remote = _remotes.by_device(did)
        if remote is None:
            kind = KIND_PHONE if dtype == "android" else KIND_PUCK
            remote = _remotes.add(device_id=did, kind=kind, name=dname or did)
        else:
            if dname and dname != "Unknown" and remote.name != dname:
                remote.name = dname
            if dtype == "android" and remote.kind != KIND_PHONE:
                remote.kind = KIND_PHONE
        _remotes.save()
    return jsonify(ok=True)

@app.post("/api/mover-control/release")
def api_mover_release():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.release(mid, did)
    return jsonify(ok=ok)

@app.post("/api/mover-control/start")
def api_mover_start():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.start_stream(mid, did)
    return jsonify(ok=ok)

@app.post("/api/mover-control/calibrate-start")
def api_mover_cal_start_ctrl():
    """Mark the mover as calibrating so the engine holds DMX steady.

    The orientation math runs on the Remote object — if body includes
    `targetObjectId` or none is given, we also drive the primitive's
    calibrate-start through the device's Remote (via _remotes.by_device).
    """
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.calibrate_start(mid, did)
    if not ok:
        return jsonify(ok=False, err="Not claimed or wrong device"), 403
    return jsonify(ok=True)

@app.post("/api/mover-control/calibrate-end")
def api_mover_cal_end_ctrl():
    """Run calibration: compute R_world_to_stage on the remote against
    the claimed mover's current stage aim, then resume streaming."""
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    if mid is None or did is None:
        return jsonify(ok=False, err="moverId/deviceId required"), 400
    mover = _mover_fixture(mid)
    if mover is None:
        return jsonify(ok=False, err="mover not found"), 404
    remote = _remotes.by_device(did)
    if remote is None:
        return jsonify(ok=False, err="no remote for this device"), 404
    aim_stage = _mover_current_aim_stage(mover)
    try:
        remote.calibrate(
            target_aim_stage=aim_stage,
            target_info={"objectId": mover["id"], "kind": "mover"},
            roll=body.get("roll"), pitch=body.get("pitch"), yaw=body.get("yaw"),
        )
        _remotes.save()
    except ValueError as e:
        return jsonify(ok=False, err=str(e)), 400
    _mover_engine.calibrate_end(mid, did)
    return jsonify(ok=True, aim=list(aim_stage))

@app.post("/api/mover-control/orient")
def api_mover_orient_compat():
    """Legacy compat — route orient to the remote primitive (#484 phase 4
    removed the direct path). Android APKs < this commit hit this
    endpoint; this thin wrapper keeps them working without an APK update.
    """
    body = request.get_json(silent=True) or {}
    did = body.get("deviceId")
    if not did:
        return jsonify(ok=False, err="deviceId required"), 400
    dname = body.get("deviceName") or ""
    remote = _remotes.by_device(did)
    if remote is None:
        # Auto-register — matches the UDP path's behaviour. Prefer the
        # deviceName the Android app supplies (phone hostname / model)
        # over the GUID so the dashboard shows "Pixel 9 Pro XL", not
        # the raw UUID (#492).
        remote = _remotes.add(device_id=did, kind=KIND_PHONE, name=dname or did)
    elif dname and remote.name != dname and remote.name == did:
        # Upgrade the placeholder name once the app starts sending one.
        remote.name = dname
        _remotes.save()
    quat = body.get("quat")
    try:
        if quat and len(quat) == 4:
            remote.update_from_quat(quat)
        else:
            remote.update_from_euler_deg(
                float(body.get("roll", 0.0)),
                float(body.get("pitch", 0.0)),
                float(body.get("yaw", 0.0)),
            )
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 400
    return jsonify(ok=True)


@app.post("/api/mover-control/color")
def api_mover_color():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.set_color(mid, did, body.get("r", 255), body.get("g", 255), body.get("b", 255),
                                  dimmer=body.get("dimmer"))
    return jsonify(ok=ok)


@app.post("/api/mover-control/smoothing")
def api_mover_set_smoothing():
    """Update EMA smoothing without re-claiming (#481 — Android parity)."""
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    sm = body.get("smoothing")
    if mid is None or not did or sm is None:
        return jsonify(ok=False, err="moverId + deviceId + smoothing required"), 400
    ok = _mover_engine.set_smoothing(mid, did, sm)
    return jsonify(ok=ok)


@app.post("/api/mover-control/flash")
def api_mover_flash():
    """Trigger strobe on a claimed mover (#482 — Android parity).

    Server-side MoverControlEngine.flash() already toggles claim.strobe_active
    which the tick maps to the fixture's strobe channel. No HTTP endpoint
    existed before — this exposes it.
    """
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    on = body.get("on", True)
    if mid is None or not did:
        return jsonify(ok=False, err="moverId + deviceId required"), 400
    ok = _mover_engine.flash(mid, did, on=bool(on))
    return jsonify(ok=ok)

@app.get("/api/mover-control/status")
def api_mover_status():
    return jsonify(claims=_mover_engine.get_status())

# ── End Mover Control ───────────────────────────────────────────────────────


# ── Remote Orientation Primitive (#484) ─────────────────────────────────────
#
# Primitive layer: each remote is a stage-space object with a calibrated
# orientation (R_world_to_stage). Consumer features (mover-follow above)
# read `remote.aim_stage`. The registry + _mover_current_aim_stage helper
# are defined above; the API routes follow.


def _mover_fixture(object_id):
    for f in _fixtures:
        if f.get("id") == int(object_id) and f.get("fixtureType") == "dmx":
            return f
    return None


def _mover_current_aim_stage(mover):
    """Read the mover's current pan/tilt from the universe buffer and
    convert it to a unit aim vector in stage coordinates.

    Preference order (#491):
      1. **Parametric v2 model** — closed-form forward kinematics against
         the fitted mount + offsets. Round-trips with ``model.inverse``,
         so calibrate-end locks against the fixture's *actual*
         DMX-commanded aim instead of a guessed layout-forward (#510).
      2. **Calibration grid** (v1 affine) — legacy fallback if the fit
         failed or no v2 data yet.
      3. **Pure ``pan_tilt_to_ray``** — assumes DMX centre = mount-local
         forward. Used when no calibration exists at all.

    Falls back to ``(0.5, 0.5)`` centre when the DMX buffer has no data or
    the profile lookup fails. Decision #6 scopes v1 to movers only.
    """
    pan_norm = 0.5
    tilt_norm = 0.5
    pid = mover.get("dmxProfileId")
    prof = _profile_lib.channel_info(pid) if pid else None
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    # Fixture instance may have panRange/tiltRange as None — prefer the
    # profile's declared ranges (Slymovehead = 540° pan / 180° tilt) and
    # fall back to generic moving-head defaults only as a last resort.
    pan_range = mover.get("panRange") \
        or (prof.get("panRange") if prof else None) or 540
    tilt_range = mover.get("tiltRange") \
        or (prof.get("tiltRange") if prof else None) or 270
    if prof and engine:
        ch_map = prof.get("channel_map", {})
        channels = prof.get("channels", [])
        addr = mover.get("dmxStartAddr", 1)
        uni = mover.get("dmxUniverse", 1)
        uni_buf = engine.get_universe(uni)

        def _read(axis):
            offset = ch_map.get(axis)
            if offset is None:
                return 0.5
            ch_def = next((c for c in channels if c.get("type") == axis), None)
            bits = ch_def.get("bits", 8) if ch_def else 8
            if bits == 16:
                hi = uni_buf.get_channel(addr + offset)
                lo = uni_buf.get_channel(addr + offset + 1)
                return ((hi << 8) | lo) / 65535.0
            return uni_buf.get_channel(addr + offset) / 255.0

        pan_norm = _read("pan")
        tilt_norm = _read("tilt")

    # 1 — parametric model (preferred when calibration exists).
    model = _get_mover_model(mover.get("id"), mover)
    if model is not None:
        return model.forward(pan_norm, tilt_norm)

    # 2 — legacy affine grid (pre-migration fallback).
    cal = _mover_cal.get(str(mover.get("id")))
    if cal and cal.get("samples") and len(cal["samples"]) >= 2:
        try:
            from mover_calibrator import affine_stage_point
            pt = affine_stage_point(cal["samples"], pan_norm, tilt_norm)
            if pt is not None:
                layout_pos = next((c for c in (_layout.get("children") or [])
                                   if c.get("id") == mover.get("id")), None) or {}
                fx = layout_pos.get("x", mover.get("x", 0))
                fy = layout_pos.get("y", mover.get("y", 0))
                fz = layout_pos.get("z", mover.get("z", 0))
                vx, vy, vz = pt[0] - fx, pt[1] - fy, pt[2] - fz
                mag = math.sqrt(vx*vx + vy*vy + vz*vz)
                if mag > 1e-6:
                    return (vx/mag, vy/mag, vz/mag)
        except Exception:
            pass

    # 3 — no calibration; generic mount-relative IK.
    return _pan_tilt_to_ray(
        pan_norm, tilt_norm,
        pan_range=pan_range,
        tilt_range=tilt_range,
        mount_rotation_deg=mover.get("rotation") or [0, 0, 0],
    )


def _auto_register_remote(device_id, kind=KIND_PUCK):
    """Return an existing remote for this device or create a fresh one.

    The first time we see a sensor stream from a device we haven't stored
    yet, stand up a remote at the default position (stage centre at head
    height — decision #4). The operator can rename or relocate via the
    layout UI later.
    """
    r = _remotes.by_device(device_id)
    if r is not None:
        return r
    # Default position: stage centre at head height
    stage_w_mm = float(_stage.get("w", 3.0)) * 1000.0
    stage_d_mm = float(_stage.get("d", 1.5)) * 1000.0
    pos = [stage_w_mm / 2.0, stage_d_mm * 0.7, 1600.0]
    name = f"Puck {device_id.split('-', 1)[-1]}" if kind == KIND_PUCK else f"Phone {device_id.split('-', 1)[-1]}"
    return _remotes.add(name=name, kind=kind, device_id=device_id, pos=pos)


# CRUD routes ──────────────────────────────────────────────────────────────

@app.get("/api/remotes")
def api_remotes_list():
    return jsonify(remotes=[r.to_persisted_dict() for r in _remotes.list()])


@app.post("/api/remotes")
def api_remotes_create():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", KIND_PUCK)
    if kind not in (KIND_PUCK, KIND_PHONE):
        return jsonify(ok=False, err="invalid kind"), 400
    r = _remotes.add(
        name=body.get("name", ""),
        kind=kind,
        device_id=body.get("deviceId"),
        pos=body.get("pos"),
        rot=body.get("rot"),
    )
    return jsonify(ok=True, remote=r.to_persisted_dict())


@app.post("/api/remotes/<int:remote_id>")
def api_remotes_update(remote_id):
    body = request.get_json(silent=True) or {}
    r = _remotes.update_fields(
        remote_id,
        name=body.get("name"),
        pos=body.get("pos"),
        rot=body.get("rot"),
        kind=body.get("kind"),
        deviceId=body.get("deviceId"),
    )
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(ok=True, remote=r.to_persisted_dict())


@app.delete("/api/remotes/<int:remote_id>")
def api_remotes_delete(remote_id):
    r = _remotes.remove(remote_id)
    return jsonify(ok=r is not None)


@app.get("/api/remotes/live")
def api_remotes_live():
    return jsonify(remotes=_remotes.live_list())


@app.get("/api/remotes/<int:remote_id>/diagnostic")
def api_remote_diagnostic(remote_id):
    """Raw + transformed orientation for axis-convention verification (#477).

    Useful when the physical puck motion doesn't match the 3D ray —
    operator / developer can see every step of the sensor → stage pipeline.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    from remote_math import quat_rotate_vec
    from remote_orientation import REMOTE_FORWARD_LOCAL, REMOTE_UP_LOCAL
    q = r.last_quat_world
    body_fwd_world = list(quat_rotate_vec(q, REMOTE_FORWARD_LOCAL)) if q else None
    body_up_world  = list(quat_rotate_vec(q, REMOTE_UP_LOCAL))      if q else None
    return jsonify({
        "id":                 r.id,
        "deviceId":           r.device_id,
        "kind":               r.kind,
        "rawQuat":            list(q) if q else None,
        "bodyForwardLocal":   list(REMOTE_FORWARD_LOCAL),
        "bodyUpLocal":        list(REMOTE_UP_LOCAL),
        "bodyForwardInWorld": body_fwd_world,
        "bodyUpInWorld":      body_up_world,
        "rWorldToStage":      list(r.R_world_to_stage) if r.R_world_to_stage else None,
        "aimStage":           list(r.aim_stage) if r.aim_stage else None,
        "upStage":            list(r.up_stage) if r.up_stage else None,
        "calibrated":         r.calibrated,
        "calibratedAt":       r.calibrated_at,
        "calibratedAgainst":  r.calibrated_against,
        "staleReason":        r.stale_reason,
        "connectionState":    r.connection_state,
        "lastDataAge":        (time.time() - r.last_data) if r.last_data else None,
    })


# Calibration ──────────────────────────────────────────────────────────────

@app.post("/api/remotes/<int:remote_id>/calibrate-start")
def api_remote_calibrate_start(remote_id):
    """Mark that calibration is in progress.

    v1 does not suppress timeline writes to the target — the design doc's
    "target held still" precondition is the operator's responsibility for
    now. Phase 4 (mover-follow rewrite) adds the hold automatically.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    r.connection_state = "armed"
    return jsonify(ok=True)


@app.post("/api/remotes/<int:remote_id>/calibrate-end")
def api_remote_calibrate_end(remote_id):
    """Compute R_world_to_stage against a target stage object.

    Body:
      { "targetObjectId": <fixture id>, "targetKind": "mover",
        "roll": deg, "pitch": deg, "yaw": deg }
    If roll/pitch/yaw are omitted, uses `remote.last_quat_world` from the
    most recent orient sample.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    target_id = body.get("targetObjectId")
    target_kind = body.get("targetKind", "mover")
    if target_kind != "mover":
        return jsonify(ok=False, err="only mover targets in v1 (decision #6)"), 400
    mover = _mover_fixture(target_id) if target_id is not None else None
    if mover is None:
        return jsonify(ok=False, err="target mover not found"), 404

    aim_stage = _mover_current_aim_stage(mover)

    try:
        r.calibrate(
            target_aim_stage=aim_stage,
            target_info={"objectId": mover["id"], "kind": "mover"},
            roll=body.get("roll"),
            pitch=body.get("pitch"),
            yaw=body.get("yaw"),
        )
    except ValueError as e:
        return jsonify(ok=False, err=str(e)), 400
    _remotes.save()
    return jsonify(ok=True, remote=r.live_dict())


@app.post("/api/remotes/<int:remote_id>/clear-stale")
def api_remote_clear_stale(remote_id):
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    r.clear_stale()
    return jsonify(ok=True, remote=r.live_dict())


@app.post("/api/remotes/<int:remote_id>/end-session")
def api_remote_end_session(remote_id):
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    r.end_session()
    _remotes.save()
    return jsonify(ok=True, remote=r.live_dict())


@app.post("/api/remotes/<int:remote_id>/orient")
def api_remote_orient(remote_id):
    """Push an orientation sample from Android (HTTP) or tests.

    v1 accepts Euler roll/pitch/yaw (degrees, ZYX intrinsic). A follow-up
    issue adds native quaternion support.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    quat = body.get("quat")
    if quat and len(quat) == 4:
        r.update_from_quat(quat)
    else:
        r.update_from_euler_deg(
            float(body.get("roll", 0.0)),
            float(body.get("pitch", 0.0)),
            float(body.get("yaw", 0.0)),
        )
    return jsonify(ok=True, aim=list(r.aim_stage) if r.aim_stage else None,
                    connectionState=r.connection_state)


# ── End Remote Orientation Primitive ────────────────────────────────────────

def _apply_profile_defaults(engine):
    """Apply profile channel default values to all DMX fixtures.

    For moving heads, also centres pan/tilt (0.5, 0.5) so the fixture
    powers up aimed at the layout-forward direction (stage +Y in mount
    frame, transformed by `fixture.rotation`) rather than drooping to
    the mechanical minimum.
    """
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        # #511 — a fixture mid-calibration owns its pan/tilt channels.
        if f.get("isCalibrating"):
            continue
        pid = f.get("dmxProfileId")
        if not pid:
            continue
        info = _profile_lib.channel_info(pid)
        if not info:
            continue
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        uni_buf = engine.get_universe(uni)
        profile = {"channel_map": info.get("channel_map", {}),
                   "channels": info.get("channels", [])}
        for ch in info.get("channels", []):
            default = ch.get("default")
            if default is not None and default > 0:
                offset = ch.get("offset", 0)
                bits = ch.get("bits", 8)
                if bits == 16:
                    val16 = max(0, min(65535, int(default)))
                    uni_buf.set_channel(addr + offset, val16 >> 8)
                    uni_buf.set_channel(addr + offset + 1, val16 & 0xFF)
                else:
                    uni_buf.set_channel(addr + offset, max(0, min(255, int(default))))
        # #516 — for the strobe channel, always write the "Open" DMX
        # value derived from ShutterStrobe capability ranges. The profile
        # default may be 0, which on "Closed at 0" wirings would leave
        # the fixture blacked out; strobe_open_value honours both
        # conventions via the shutterEffect annotation.
        strobe_open = dmx_profiles.strobe_open_value(info)
        ch_map = info.get("channel_map", {})
        if "strobe" in ch_map:
            uni_buf.set_channel(addr + ch_map["strobe"], strobe_open)
        # Seed pan/tilt to the fixture's home position — the layout-stored
        # `rotation` aim vector (#493). Preference order:
        #   1. Parametric v2 model inverse() of the home target — closed form,
        #      always within mechanical range (clamped).
        #   2. Calibration's explicit `centerPan`/`centerTilt` — legacy.
        #   3. v1 affine fit against the rotation-derived target.
        #   4. Mount-local forward 0.5/0.5.
        pan_seed, tilt_seed = 0.5, 0.5
        cal = _mover_cal.get(str(f["id"]))
        model = _get_mover_model(f["id"], f)
        if model is not None:
            try:
                from bake_engine import _rotation_to_aim
                pos = _fixture_position(f["id"])
                rot = f.get("rotation") or [0, 0, 0]
                # _rotation_to_aim returns a stage-space target 3 m ahead
                # along the home direction.
                aim_target = _rotation_to_aim(rot, list(pos), 3000)
                pan_seed, tilt_seed = model.inverse(aim_target[0], aim_target[1], aim_target[2])
            except Exception as e:
                log.debug("Mover %d home-seed via parametric failed: %s", f["id"], e)
        elif cal:
            cp, ct = cal.get("centerPan"), cal.get("centerTilt")
            if cp is not None and ct is not None:
                pan_seed, tilt_seed = cp, ct
            elif cal.get("samples") and len(cal["samples"]) >= 2:
                try:
                    from bake_engine import _rotation_to_aim
                    from mover_calibrator import affine_pan_tilt
                    pos = _fixture_position(f["id"])
                    rot = f.get("rotation") or [0, 0, 0]
                    aim = _rotation_to_aim(rot, list(pos), 3000)
                    pt = affine_pan_tilt(cal["samples"], aim[0], aim[1], aim[2])
                    if pt is not None:
                        pan_seed, tilt_seed = pt
                except Exception:
                    pass
        uni_buf.set_fixture_pan_tilt(addr, pan_seed, tilt_seed, profile)

@app.post("/api/dmx/start")
def api_dmx_start():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol", "artnet")
    if protocol == "artnet":
        _artnet.start()
        _apply_profile_defaults(_artnet)
    elif protocol == "sacn":
        _sacn.start()
        _apply_profile_defaults(_sacn)
    else:
        return jsonify(err=f"Unknown protocol: {protocol}"), 400
    return jsonify(ok=True, protocol=protocol)

@app.post("/api/dmx/stop")
def api_dmx_stop():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol")
    if protocol == "artnet" or protocol is None:
        _artnet.stop()
    if protocol == "sacn" or protocol is None:
        _sacn.stop()
    return jsonify(ok=True)

@app.post("/api/dmx/blackout")
def api_dmx_blackout():
    """Zero every universe buffer.

    If a running engine exists, its 40 Hz loop picks up the dirty buffers
    and transmits zeros on the next frame. If BOTH engines are stopped,
    zeroing the buffer alone doesn't reach the wire — so we briefly spin
    up Art-Net, seed it with the registered universeRoutes and any fixture
    universes, blackout, and stop. Stop() then flushes 3 forced blackout
    frames (#601), unsticking bridges that latched on a stale cue.
    """
    _artnet.blackout()
    _sacn.blackout()
    flushed = False
    if not _artnet.running and not _sacn.running:
        try:
            _apply_dmx_settings()
            _artnet._bind_ip = "0.0.0.0"  # stale saved IP can block bind (#345)
            _artnet.start()
            if _artnet.running:
                # Register every universe we know about so stop() has
                # something to transmit zeros on. Fixture-derived universes
                # come from _apply_profile_defaults; route-only universes
                # (configured but no fixtures yet) get created here.
                for route in _dmx_settings.get("universeRoutes", []) or []:
                    u = int(route.get("universe") or 1)
                    _artnet.get_universe(u)
                for f in _fixtures:
                    if f.get("fixtureType") == "dmx":
                        _artnet.get_universe(int(f.get("dmxUniverse", 1)))
                _artnet.blackout()
                # stop() sends 3 forced blackout frames (#601) and tears
                # the socket down, leaving the bridge latched on zeros.
                _artnet.stop()
                flushed = True
        except Exception:
            pass
    return jsonify(ok=True, flushed=flushed)

@app.post("/api/dmx/blink")
def api_dmx_blink():
    """Rainbow-cycle all DMX fixtures (same as boot blink). Engine must be running."""
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    if not engine:
        return jsonify(ok=False, err="DMX engine is not running"), 400
    dmx_count = sum(1 for f in _fixtures if f.get("fixtureType") == "dmx")
    if dmx_count == 0:
        return jsonify(ok=False, err="No DMX fixtures defined — add one via Add Fixture"), 400
    import threading as _thr_blink
    _thr_blink.Thread(target=_run_boot_blink, args=(engine, True), daemon=True).start()
    return jsonify(ok=True, fixtures=dmx_count)

@app.post("/api/dmx/channel")
def api_dmx_set_channel():
    """Set a single DMX channel. Body: {universe, channel, value}."""
    body = request.get_json(silent=True) or {}
    uni = body.get("universe", 1)
    ch = body.get("channel")
    val = body.get("value", 0)
    if not ch or ch < 1 or ch > 512:
        return jsonify(err="channel must be 1-512"), 400
    if _artnet.running:
        _artnet.set_channel(uni, ch, val)
    if _sacn.running:
        _sacn.set_channel(uni, ch, val)
    return jsonify(ok=True)

@app.post("/api/dmx/fixture")
def api_dmx_set_fixture():
    """Set DMX channels for a fixture by ID. Body: {fixtureId, r, g, b, dimmer}."""
    body = request.get_json(silent=True) or {}
    fid = body.get("fixtureId")
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)
    pid = fixture.get("dmxProfileId")
    profile_map = _profile_lib.channel_map(pid) if pid else None

    r = body.get("r", 0)
    g = body.get("g", 0)
    b = body.get("b", 0)
    dimmer = body.get("dimmer")

    for engine in (_artnet, _sacn):
        if engine.running:
            _set_fixture_color(engine, uni, addr, r, g, b, prof_info)
            if dimmer is not None and profile_map and "dimmer" in profile_map:
                engine.get_universe(uni).set_fixture_dimmer(
                    addr, dimmer, {"channel_map": profile_map})
    return jsonify(ok=True)

@app.get("/api/dmx/discovered")
def api_dmx_discovered():
    """Return Art-Net nodes discovered via ArtPoll.

    Both code paths now wait ~1 s for replies to trickle in before
    returning — the engine-running path used to return `discovered_nodes`
    synchronously right after issuing the poll, which guaranteed an empty
    list on the first click because replies take 50-500 ms to arrive.
    The one-shot path had the same bug plus a `break` on its first recv
    timeout that exited the listen loop ~500 ms early. Both fixed (#564).
    """
    if _artnet.running:
        _artnet.poll()
        # Also poll any known DMX bridge IP directly — subnet broadcast
        # can get dropped by switches that disable IGMP on a guest VLAN,
        # and unicast to a known bridge is always reliable.
        _artnet_unicast_known_bridges()
        # Give the engine's _recv loop time to stamp late replies.
        _time_mod = time
        _deadline = _time_mod.time() + 1.0
        _seen_at_start = set(_artnet.discovered_nodes.keys())
        while _time_mod.time() < _deadline:
            if set(_artnet.discovered_nodes.keys()) - _seen_at_start:
                break  # at least one new node — short-circuit
            _time_mod.sleep(0.05)
    else:
        _artnet_oneshot_poll()
    return jsonify(_artnet.discovered_nodes)

def _artnet_unicast_known_bridges():
    """Unicast an ArtPoll to every known DMX bridge IP. Subnet broadcast
    can be silently dropped by managed switches / guest VLANs; unicast to
    a known-good IP is always reachable when the bridge is online."""
    try:
        from dmx_artnet import build_artpoll, ARTNET_PORT
    except Exception:
        return
    if not _artnet._sock:
        return
    pkt = build_artpoll()
    for c in _children:
        if c.get("type") == "dmx" and c.get("ip"):
            try:
                _artnet._sock.sendto(pkt, (c["ip"], ARTNET_PORT))
            except Exception:
                pass

def _artnet_oneshot_poll():
    """Send ArtPoll + listen for replies without starting the full engine.

    Fixed in #564:
    - broadcast list now comes from `_all_local_broadcast_addrs()` so
      every interface's subnet is covered (matches the engine's path);
    - `recvfrom` timeout is a tight 100 ms and the loop no longer breaks
      on timeout — it continues polling until the 2 s deadline expires,
      which means we actually catch replies that arrive 300+ ms after
      the first second of silence.

    Fixed in #570:
    - **binds to port 6454** (with SO_REUSEADDR). Art-Net 4 spec mandates
      the node reply goes to UDP port 6454 regardless of the source port
      of the ArtPoll; binding to an ephemeral port meant every reply
      landed somewhere we weren't listening. Cold-start discover now
      actually receives ArtPollReply packets.
    - If the engine's already bound exclusively to 6454 we fall back to
      issuing the poll through the engine's own socket, which lets its
      `_recv()` loop stamp the replies into `_artnet._discovered`.
    """
    try:
        from dmx_artnet import (build_artpoll, parse_artnet_header,
                                parse_artpoll_reply, ARTNET_PORT,
                                OP_POLL_REPLY, _all_local_broadcast_addrs)
        # If the engine is already running it owns port 6454 and its
        # _recv loop will catch replies — just trigger the broadcast.
        if _artnet.running and _artnet._sock is not None:
            _artnet.poll()
            _artnet_unicast_known_bridges()
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind to ARTNET_PORT (6454) so ArtPollReply packets land here.
        # Bridges always target 6454 per spec, not the sender's ephemeral
        # port. Fall back to ephemeral if 6454 is held by some other app
        # (e.g. an external console running on the same host) — replies
        # will be lost in that edge case but at least the poll goes out.
        sock.settimeout(0.1)  # short per-recv so late replies still land
        try:
            sock.bind(("", ARTNET_PORT))
        except OSError:
            log.warning("ArtPoll one-shot: port %d in use — binding ephemeral; "
                        "replies may be missed", ARTNET_PORT)
            sock.bind(("", 0))
        pkt = build_artpoll()
        for dest in _all_local_broadcast_addrs():
            try:
                sock.sendto(pkt, (dest, ARTNET_PORT))
            except Exception:
                pass
        # Also unicast to known children with type=dmx — reliable path
        # when the switch drops broadcasts.
        for c in _children:
            if c.get("type") == "dmx" and c.get("ip"):
                try:
                    sock.sendto(pkt, (c["ip"], ARTNET_PORT))
                except Exception:
                    pass
        # Listen for the full 2 s — do NOT break on the first timeout.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break
            hdr = parse_artnet_header(data)
            if hdr and hdr[0] == OP_POLL_REPLY:
                info = parse_artpoll_reply(data)
                if info:
                    _artnet._discovered[info["ip"]] = info
                    log.info("ArtPoll reply from %s: %s", info["ip"],
                             info.get("shortName"))
        sock.close()
    except Exception as e:
        log.debug("One-shot ArtPoll failed: %s", e)

# -- DMX Monitor (live 512-channel view) --------------------------------------

@app.get("/api/dmx/monitor/<int:uni>")
def api_dmx_monitor(uni):
    """Return all 512 channel values for a universe as a flat array."""
    for engine in (_artnet, _sacn):
        if engine.running and uni in engine._universes:
            data = engine._universes[uni].get_data()
            return jsonify({"universe": uni, "channels": list(data)})
    # No engine running or universe not created — return zeros
    return jsonify({"universe": uni, "channels": [0] * 512})

@app.post("/api/dmx/monitor/<int:uni>/set")
def api_dmx_monitor_set(uni):
    """Set individual channels. Body: {channels: [{addr: 1-512, value: 0-255}]}."""
    body = request.get_json(silent=True) or {}
    for ch in body.get("channels", []):
        addr = ch.get("addr", 0)
        val = max(0, min(255, int(ch.get("value", 0))))
        for engine in (_artnet, _sacn):
            if engine.running:
                engine.set_channel(uni, addr, val)
    return jsonify(ok=True)

# -- Fixture Group Control ----------------------------------------------------

@app.post("/api/fixtures/group/<int:gid>/control")
def api_group_control(gid):
    """Apply dimmer/color to all members of a fixture group."""
    group = next((f for f in _fixtures if f["id"] == gid and f.get("type") == "group"), None)
    if not group:
        return jsonify(err="Group not found"), 404
    body = request.get_json(silent=True) or {}
    r = body.get("r")
    g = body.get("g")
    b = body.get("b")
    dimmer = body.get("dimmer")
    member_ids = group.get("childIds", [])
    applied = 0
    for mid in member_ids:
        member = next((f for f in _fixtures if f["id"] == mid), None)
        if not member or member.get("fixtureType") != "dmx":
            continue
        uni = member.get("dmxUniverse", 1)
        addr = member.get("dmxStartAddr", 1)
        pid = member.get("dmxProfileId")
        profile_map = None
        prof_info_full = _profile_lib.channel_info(pid) if pid else None
        if pid:
            prof = _profile_lib.get_profile(pid)
            if prof:
                profile_map = {}
                for ch in prof.get("channels", []):
                    profile_map[ch["type"]] = ch["offset"]
        for engine in (_artnet, _sacn):
            if engine.running:
                if r is not None and g is not None and b is not None:
                    _set_fixture_color(engine, uni, addr, r, g, b, prof_info_full)
                if dimmer is not None and profile_map and "dimmer" in profile_map:
                    engine.get_universe(uni).set_channel(addr + profile_map["dimmer"], dimmer)
        applied += 1
    return jsonify(ok=True, applied=applied)

# -- DMX Settings (persistent) ------------------------------------------------

_DMX_SETTINGS_DEFAULTS = {
    "protocol": "artnet",
    "frameRate": 40,
    "bindIp": "0.0.0.0",
    "universeRoutes": [],     # [{universe: int, destination: ip, label: str}]
    "sacnPriority": 100,
    "sacnSourceName": "SlyLED",
    "autoStartEngine": True,   # auto-start DMX engine on boot (#389)
    "bootBlinkFixtures": True,  # rainbow blink on first boot (#389)
}
_dmx_settings = _load("dmx_settings", dict(_DMX_SETTINGS_DEFAULTS))
# Backfill new keys from defaults (#389)
for _dk, _dv in _DMX_SETTINGS_DEFAULTS.items():
    if _dk not in _dmx_settings:
        _dmx_settings[_dk] = _dv
# Migrate old unicastTargets to universeRoutes
if "unicastTargets" in _dmx_settings and not _dmx_settings.get("universeRoutes"):
    _old = _dmx_settings.pop("unicastTargets", {})
    _dmx_settings["universeRoutes"] = [
        {"universe": int(k), "destination": v, "label": ""}
        for k, v in _old.items() if v
    ]

def _routes_to_unicast(routes):
    """Convert universeRoutes list to {universe_int: ip} dict for engine."""
    result = {}
    for r in (routes or []):
        uni = r.get("universe")
        dest = r.get("destination", "").strip()
        if uni is not None and dest:
            result[int(uni)] = dest
    return result

def _apply_dmx_settings():
    """Apply persisted DMX settings to engines."""
    s = _dmx_settings
    _artnet.configure(
        bind_ip=s.get("bindIp", "0.0.0.0"),
        unicast_targets=_routes_to_unicast(s.get("universeRoutes", [])),
        frame_rate=s.get("frameRate", 40),
    )
    _sacn.configure(
        source_name=s.get("sacnSourceName", "SlyLED"),
        priority=s.get("sacnPriority", 100),
        bind_ip=s.get("bindIp", "0.0.0.0"),
        frame_rate=s.get("frameRate", 40),
    )

_apply_dmx_settings()

# ── Boot blink function (#389) ────────────────────────────────────────────
_boot_blink_done = False

def _run_boot_blink(engine, force=False):
    """Boot sequence for DMX fixtures (#487): hold at layout-forward
    position (already seeded by _apply_profile_defaults) → brief blackout
    hold → rainbow cycle → final blackout. The mover never slews —
    pan/tilt are untouched throughout so the fixture visibly stays on
    its layout direction while colour and dimmer confirm the pipeline
    is alive.

    Runs once on boot unless force=True (manual blink from Settings).
    """
    global _boot_blink_done
    if _boot_blink_done and not force:
        return
    _boot_blink_done = True
    try:
        _run_boot_blink_body(engine, force)
    except Exception:
        log.exception("Boot blink crashed")


def _run_boot_blink_body(engine, force):
    import colorsys
    # Collect DMX fixtures once before the animation
    dmx_fx = [(f, f.get("dmxProfileId")) for f in _fixtures if f.get("fixtureType") == "dmx"]
    if not dmx_fx:
        log.info("Boot blink skipped: no DMX fixtures defined")
        return
    profiles = {}
    for f, pid in dmx_fx:
        if pid and pid not in profiles:
            info = _profile_lib.channel_info(pid)
            if info:
                profiles[pid] = {"channel_map": info.get("channel_map", {}),
                                 "channels": info.get("channels", [])}
    log.info("Boot blink: %d DMX fixtures, %d profiles", len(dmx_fx), len(profiles))

    # Seed shutter/strobe to "open" on every fixture — the profile defaults
    # pass that runs on engine start-up sets this, but a manual Blink from
    # Settings may fire before or after other channel writers; writing the
    # open value here guarantees the beam is unshuttered throughout the
    # rainbow regardless of prior state.
    for f, pid in dmx_fx:
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        prof = profiles.get(pid)
        if not prof:
            continue
        info = _profile_lib.channel_info(pid)
        if info:
            strobe_open = dmx_profiles.strobe_open_value(info)
            cm = prof.get("channel_map", {})
            if "strobe" in cm:
                engine.get_universe(uni).set_channel(addr + cm["strobe"], strobe_open)
            if "shutter" in cm:
                engine.get_universe(uni).set_channel(addr + cm["shutter"], 255)

    # Step 1: explicit blackout hold (500 ms) so the blink starts
    # against darkness — the fixture is aimed at layout-forward but
    # dark. Makes the "DMX is alive" flash unambiguous.
    for f, pid in dmx_fx:
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        prof = profiles.get(pid)
        if prof:
            cm = prof.get("channel_map", {})
            if "dimmer" in cm:
                engine.get_universe(uni).set_channel(addr + cm["dimmer"], 0)
    time.sleep(0.5)

    # Step 2: rainbow colour cycle (3 s, no pan/tilt motion).
    steps = 30
    step_ms = 100  # 30 × 100ms = 3s
    for i in range(steps):
        hue = i / steps
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0)]
        for f, pid in dmx_fx:
            uni = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            prof = profiles.get(pid)
            if prof:
                _set_fixture_color(engine, uni, addr, r, g, b, prof)
                cm = prof.get("channel_map", {})
                if "dimmer" in cm:
                    engine.get_universe(uni).set_channel(addr + cm["dimmer"], 255)
                else:
                    # RGB-only fixture with no dimmer channel — the RGB
                    # writes in _set_fixture_color already carry brightness.
                    pass
            else:
                # No profile — write dimmer-only pulse to first channel
                engine.get_universe(uni).set_channel(addr, 255)
        time.sleep(step_ms / 1000)
    # Blackout all fixtures
    for f, pid in dmx_fx:
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        prof = profiles.get(pid)
        if prof:
            engine.set_fixture_rgb(uni, addr, 0, 0, 0, prof)
            cm = prof.get("channel_map", {})
            if "dimmer" in cm:
                engine.get_universe(uni).set_channel(addr + cm["dimmer"], 0)
        else:
            engine.get_universe(uni).set_channel(addr, 0)
    log.info("Boot blink complete: %d fixtures cycled rainbow → blackout", len(dmx_fx))

# Auto-start DMX engine if universe routes are configured (#389: gated by setting)
if _dmx_settings.get("autoStartEngine", True) and _dmx_settings.get("universeRoutes"):
    _proto = _dmx_settings.get("protocol", "artnet")
    _engine = _artnet if _proto == "artnet" else _sacn if _proto == "sacn" else None
    if _engine:
        try:
            _engine.start()
        except Exception as e:
            # Bind IP may be stale (DHCP changed) — retry with 0.0.0.0 (#345)
            log.warning("DMX auto-start failed on %s: %s — retrying with 0.0.0.0",
                        _dmx_settings.get("bindIp", "?"), e)
            try:
                _engine._bind_ip = "0.0.0.0"
                _engine.start()
            except Exception as e2:
                log.warning("DMX auto-start fallback also failed: %s", e2)
        if _engine.running:
            _apply_profile_defaults(_engine)
            log.info("%s auto-started (%d routes), profile defaults applied",
                     _proto.upper(), len(_dmx_settings["universeRoutes"]))
            # Boot blink: rainbow cycle then blackout (#389)
            if _dmx_settings.get("bootBlinkFixtures", True) and not _boot_blink_done:
                import threading as _thr
                _thr.Thread(target=_run_boot_blink, args=(_engine,), daemon=True).start()

# ── Auto-start show on boot (#390) ────────────────────────────────────────
def _auto_start_show():
    """Resume the last active timeline if autoStartShow is enabled."""
    time.sleep(5)  # wait for children to reconnect
    tid = _settings.get("activeTimeline", -1)
    if tid < 0:
        log.info("Auto-start show: no active timeline saved — staying idle")
        return
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        log.warning("Auto-start show: timeline %d not found — staying idle", tid)
        return
    has_track = any(a.get("type") == 18 for a in _actions)
    if tid not in _bake_result and not has_track:
        log.warning("Auto-start show: timeline %d not baked — staying idle", tid)
        return
    # Start playback
    log.info("Auto-start show: resuming timeline %d (%s)", tid, tl.get("name", "?"))
    with app.test_request_context():
        api_timeline_start(tid)

# ── Boot cleanup: stop any camera trackers left running from previous session ──
def _boot_stop_trackers():
    """Send track/stop to all camera nodes so stale trackers don't keep pushing data."""
    import urllib.request as _ur_boot
    time.sleep(3)  # wait for network
    cams = [f for f in _fixtures if f.get("fixtureType") == "camera" and f.get("cameraIp")]
    seen_ips = set()
    for c in cams:
        ip = c["cameraIp"]
        if ip in seen_ips:
            continue
        seen_ips.add(ip)
        try:
            req = _ur_boot.Request(f"http://{ip}:5000/track/stop",
                                   data=b"{}",
                                   headers={"Content-Type": "application/json"})
            _ur_boot.urlopen(req, timeout=3)
            log.info("Boot cleanup: stopped tracker on %s", ip)
        except Exception:
            pass  # camera offline — nothing to stop

import threading as _thr_boot
_thr_boot.Thread(target=_boot_stop_trackers, daemon=True).start()

if _settings.get("autoStartShow"):
    import threading as _thr2
    _thr2.Thread(target=_auto_start_show, daemon=True).start()

@app.get("/api/dmx/interfaces")
def api_dmx_interfaces():
    """List local network interfaces with their IPv4 addresses."""
    result = [{"name": "All Interfaces", "ip": "0.0.0.0"}]
    try:
        # Cross-platform: use socket.getaddrinfo on the hostname
        import socket as _sock
        hostname = _sock.gethostname()
        for info in _sock.getaddrinfo(hostname, None, _sock.AF_INET):
            ip = info[4][0]
            if ip and ip != "127.0.0.1" and not any(r["ip"] == ip for r in result):
                result.append({"name": hostname, "ip": ip})
        # Also try netifaces if available (gives interface names)
        try:
            import netifaces
            for iface in netifaces.interfaces():
                addrs = netifaces.ifaddresses(iface)
                for addr_info in addrs.get(netifaces.AF_INET, []):
                    ip = addr_info.get("addr", "")
                    if ip and ip != "127.0.0.1" and not any(r["ip"] == ip for r in result):
                        result.append({"name": iface, "ip": ip})
        except ImportError:
            pass
    except Exception:
        pass
    # Fallback: probe default route
    if len(result) == 1:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            result.append({"name": "default", "ip": s.getsockname()[0]})
            s.close()
        except Exception:
            pass
    return jsonify(result)

@app.get("/api/dmx/settings")
def api_dmx_settings_get():
    return jsonify(_dmx_settings)

@app.post("/api/dmx/settings")
def api_dmx_settings_save():
    body = request.get_json(silent=True) or {}
    for k in ("protocol", "frameRate", "bindIp", "universeRoutes",
              "sacnPriority", "sacnSourceName",
              "autoStartEngine", "bootBlinkFixtures"):
        if k in body:
            _dmx_settings[k] = body[k]
    # Remove legacy field
    _dmx_settings.pop("unicastTargets", None)
    fr = _dmx_settings.get("frameRate", 40)
    if not isinstance(fr, int) or fr < 1 or fr > 44:
        _dmx_settings["frameRate"] = 40
    pri = _dmx_settings.get("sacnPriority", 100)
    if not isinstance(pri, int) or pri < 0 or pri > 200:
        _dmx_settings["sacnPriority"] = 100
    # Validate routes
    routes = _dmx_settings.get("universeRoutes", [])
    _dmx_settings["universeRoutes"] = [
        r for r in routes
        if isinstance(r, dict) and r.get("destination")
    ]
    _save("dmx_settings", _dmx_settings)
    _apply_dmx_settings()
    return jsonify(ok=True)

# -- DMX Fixture Test ---------------------------------------------------------

@app.get("/api/dmx/fixture/<int:fid>/channels")
def api_dmx_fixture_channels(fid):
    """Return channel list for a DMX fixture (from its profile or generic)."""
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    pid = fixture.get("dmxProfileId")
    profile = _profile_lib.get_profile(pid) if pid else None
    count = fixture.get("dmxChannelCount", 3)
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)
    if profile:
        channels = [{"offset": ch["offset"], "name": ch["name"], "type": ch["type"],
                      "default": ch.get("default", 0),
                      "capabilities": ch.get("capabilities", [])}
                    for ch in profile.get("channels", [])]
    else:
        channels = [{"offset": i, "name": f"Ch {i+1}", "type": "dimmer",
                      "default": 0,
                      "capabilities": [{"range": [0, 255], "type": "Intensity", "label": f"Ch {i+1} 0-100%"}]}
                    for i in range(count)]
    # Read current values from universe buffer; fall back to profile default
    for ch in channels:
        dmx_addr = addr + ch["offset"]
        val = 0
        if _artnet.running:
            val = _artnet.get_universe(uni).get_channel(dmx_addr)
        elif _sacn.running:
            val = _sacn.get_universe(uni).get_channel(dmx_addr)
        # If engine isn't running or channel is 0, use profile default
        if val == 0 and ch.get("default", 0) > 0:
            val = ch["default"]
        ch["value"] = val
    pan_range = profile.get("panRange", 0) if profile else 0
    tilt_range = profile.get("tiltRange", 0) if profile else 0
    orient = fixture.get("orientation", {})
    inverted = fixture.get("mountedInverted", False)
    # Compute home position: aim at audience center at floor level
    home_pan = 0.5
    home_tilt = 0.5
    if pan_range > 0 and tilt_range > 0:
        # Fixture position lives in _layout["children"], not on fixture object
        pos = next((c for c in _layout.get("children", []) if c.get("id") == fid), None)
        fx = pos.get("x", 0) if pos else 0
        fy = pos.get("y", 0) if pos else 0
        fz = pos.get("z", 0) if pos else 0
        # Target: same X as fixture, mid-stage depth, floor
        stage_d = (_stage.get("d", 4) * 1000) if _stage else 4000
        target = (fx, stage_d / 2, 0)
        pt = compute_pan_tilt((fx, fy, fz), target, pan_range, tilt_range,
                              mounted_inverted=inverted)
        if pt:
            home_pan, home_tilt = pt
    return jsonify(universe=uni, startAddr=addr, channels=channels,
                   panRange=pan_range, tiltRange=tilt_range,
                   panSign=orient.get("panSign", 1),
                   tiltSign=orient.get("tiltSign", -1),
                   mountedInverted=inverted,
                   homePan=round(home_pan, 4),
                   homeTilt=round(home_tilt, 4))

@app.post("/api/dmx/fixture/<int:fid>/test")
def api_dmx_fixture_test(fid):
    """Set channel values for testing a DMX fixture.

    Two payload shapes (may be combined):
      {channels: [{offset, value}]}   — raw channel writes (slider path)
      {color: {r, g, b, dimmer?}}     — profile-aware semantic color (#609).
                                        Routes through _set_fixture_color so
                                        color-wheel fixtures pick the right
                                        wheel slot instead of writing RGB
                                        channels that don't exist.
    """
    fixture = next((f for f in _fixtures if f["id"] == fid), None)
    if not fixture or fixture.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    body = request.get_json(silent=True) or {}
    uni = fixture.get("dmxUniverse", 1)
    addr = fixture.get("dmxStartAddr", 1)

    # Profile-aware semantic color (#609).
    color = body.get("color")
    if color is not None:
        pid = fixture.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        r = max(0, min(255, int(color.get("r", 0))))
        g = max(0, min(255, int(color.get("g", 0))))
        b = max(0, min(255, int(color.get("b", 0))))
        dimmer = color.get("dimmer")
        ch_map = (prof_info or {}).get("channel_map", {}) if prof_info else {}
        for engine in (_artnet, _sacn):
            if engine.running:
                _set_fixture_color(engine, uni, addr, r, g, b, prof_info)
                if dimmer is not None and "dimmer" in ch_map:
                    dval = max(0, min(255, int(dimmer)))
                    engine.get_universe(uni).set_channel(addr + ch_map["dimmer"], dval)

    # Raw channel writes — used by the slider UI.
    for ch in body.get("channels", []):
        dmx_addr = addr + ch.get("offset", 0)
        val = max(0, min(255, int(ch.get("value", 0))))
        if _artnet.running:
            _artnet.set_channel(uni, dmx_addr, val)
        if _sacn.running:
            _sacn.set_channel(uni, dmx_addr, val)
    return jsonify(ok=True)

# -- Live fixture status (#303) -----------------------------------------------

# Action type names — must match SPA _typeNames array
_ACTION_NAMES = [
    "Blackout", "Solid", "Fade", "Breathe", "Chase", "Rainbow", "Fire",
    "Comet", "Twinkle", "Strobe", "Color Wipe", "Scanner", "Sparkle",
    "Gradient", "DMX Scene", "Pan/Tilt Move", "Gobo Select", "Color Wheel",
    "Track",
]

@app.get("/api/fixtures/live")
def api_fixtures_live():
    """Return per-fixture live output state for the dashboard status grid.

    For DMX fixtures: reads current channel values from Art-Net/sACN universe
    buffers and maps them to named parameters (r, g, b, dimmer, pan, tilt, …).

    For LED children: uses ACTION_EVENT data pushed by child nodes to report
    the current action type and step.

    Returns a list of fixture status objects, one per fixture.
    """
    running = bool(_settings.get("runnerRunning"))
    result = []
    for f in _fixtures:
        fid = f["id"]
        ft = f.get("fixtureType", "led")
        entry = {
            "id": fid,
            "name": f.get("name") or f"Fixture {fid}",
            "fixtureType": ft,
            "r": 0, "g": 0, "b": 0,
            "dimmer": 0,
            "active": False,
            "effect": None,
        }
        if ft == "dmx":
            uni_num = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            pid = f.get("dmxProfileId")
            prof_info = _profile_lib.channel_info(pid) if pid else None
            ch_map = prof_info.get("channel_map") if prof_info else None
            # Read channels from running engine
            engine = None
            if _artnet.running:
                engine = _artnet
            elif _sacn.running:
                engine = _sacn
            if engine:
                uni = engine.get_universe(uni_num)
                if ch_map:
                    if "red" in ch_map:
                        entry["r"] = uni.get_channel(addr + ch_map["red"])
                    if "green" in ch_map:
                        entry["g"] = uni.get_channel(addr + ch_map["green"])
                    if "blue" in ch_map:
                        entry["b"] = uni.get_channel(addr + ch_map["blue"])
                    if "dimmer" in ch_map:
                        entry["dimmer"] = uni.get_channel(addr + ch_map["dimmer"])
                    if "pan" in ch_map:
                        entry["pan"] = uni.get_channel(addr + ch_map["pan"])
                    if "tilt" in ch_map:
                        entry["tilt"] = uni.get_channel(addr + ch_map["tilt"])
                    if "pan-fine" in ch_map:
                        entry["panFine"] = uni.get_channel(addr + ch_map["pan-fine"])
                    if "tilt-fine" in ch_map:
                        entry["tiltFine"] = uni.get_channel(addr + ch_map["tilt-fine"])
                else:
                    # Generic RGB fixture — assume channels at start
                    count = f.get("dmxChannelCount", 3)
                    if count >= 3:
                        entry["r"] = uni.get_channel(addr)
                        entry["g"] = uni.get_channel(addr + 1)
                        entry["b"] = uni.get_channel(addr + 2)
                    if count >= 4:
                        entry["dimmer"] = uni.get_channel(addr + 3)
            # Color wheel slot lookup — populate swatch color from wheel slot
            if ch_map and "color-wheel" in ch_map and engine:
                cw_val = uni.get_channel(addr + ch_map["color-wheel"])
                entry["colorWheelDmx"] = cw_val
                for ch_def in (prof_info.get("channels") or []):
                    if ch_def.get("type") == "color-wheel":
                        for cap in (ch_def.get("capabilities") or []):
                            rng = cap.get("range", [0, 0])
                            if cap.get("type") == "WheelSlot" and rng[0] <= cw_val <= rng[1]:
                                entry["colorWheelSlot"] = cap.get("label", "")
                                hex_col = cap.get("color", "")
                                entry["colorWheelColor"] = hex_col
                                # Use wheel color for swatch if no RGB channels
                                if hex_col and "red" not in ch_map:
                                    try:
                                        entry["r"] = int(hex_col[1:3], 16)
                                        entry["g"] = int(hex_col[3:5], 16)
                                        entry["b"] = int(hex_col[5:7], 16)
                                    except (ValueError, IndexError):
                                        pass
                                break
                        break
            # Active = producing visible light.  For color-wheel-only fixtures the
            # r/g/b are inferred from the wheel slot and don't mean the beam is on —
            # only dimmer > 0 matters.  For RGB fixtures check actual channel values.
            # Generic (profile-less) DMX fixtures also populate r/g/b from raw
            # channels above, so treat them as RGB for the active check.
            has_rgb_ch = (not ch_map) or ("red" in ch_map)
            if has_rgb_ch:
                entry["active"] = (entry["r"] > 0 or entry["g"] > 0
                                   or entry["b"] > 0 or entry["dimmer"] > 0)
            else:
                entry["active"] = entry["dimmer"] > 0
            # DMX address info for display
            entry["dmxAddr"] = f"U{uni_num}.{addr}"
            # Live aim vector in stage coords for the 3D viewport cone.
            # Reads current pan/tilt from the universe buffer (including 16-bit
            # pairs) and runs pan_tilt_to_ray with the fixture's mount rotation.
            if ch_map and "pan" in ch_map and "tilt" in ch_map and engine:
                try:
                    def _read_norm(axis):
                        offset = ch_map.get(axis)
                        if offset is None:
                            return 0.5
                        ch_def = next((c for c in prof_info.get("channels", [])
                                       if c.get("type") == axis), None)
                        bits = ch_def.get("bits", 8) if ch_def else 8
                        if bits == 16:
                            hi = uni.get_channel(addr + offset)
                            lo = uni.get_channel(addr + offset + 1)
                            return ((hi << 8) | lo) / 65535.0
                        return uni.get_channel(addr + offset) / 255.0
                    pan_norm = _read_norm("pan")
                    tilt_norm = _read_norm("tilt")
                    aim = _pan_tilt_to_ray(
                        pan_norm, tilt_norm,
                        pan_range=f.get("panRange") or 540,
                        tilt_range=f.get("tiltRange") or 270,
                        mount_rotation_deg=f.get("rotation") or [0, 0, 0],
                    )
                    entry["aim"] = [round(aim[0], 4),
                                    round(aim[1], 4),
                                    round(aim[2], 4)]
                    entry["panNorm"] = round(pan_norm, 4)
                    entry["tiltNorm"] = round(tilt_norm, 4)
                except Exception:
                    pass
        elif ft == "led":
            # LED fixtures — check live_events from child node
            cid = f.get("childId")
            child = next((c for c in _children if c["id"] == cid), None) if cid is not None else None
            entry["online"] = bool(child and child.get("status") == 1) if child else False
            if child:
                ip = child.get("ip", "")
                ev = _live_events.get(ip)
                if ev and time.time() - ev.get("ts", 0) < 30:
                    at = ev.get("actionType", 0)
                    entry["active"] = ev.get("event", 1) == 0  # 0=started
                    if at < len(_ACTION_NAMES):
                        entry["effect"] = _ACTION_NAMES[at]
                    entry["step"] = ev.get("stepIndex", 0)
                    entry["totalSteps"] = ev.get("totalSteps", 0)
        elif ft == "camera":
            entry["online"] = bool(f.get("ip"))
            continue  # cameras aren't light-emitting fixtures
        result.append(entry)
    return jsonify({"running": running, "fixtures": result})


# -- Spatial Effects (Phase 3) ------------------------------------------------

@app.get("/api/spatial-effects")
def api_sfx_get():
    return jsonify(_spatial_fx)

@app.post("/api/spatial-effects")
def api_sfx_create():
    global _nxt_sfx
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(err="Name required"), 400
    cat = body.get("category", "spatial-field")
    if cat not in ("fixture-local", "spatial-field"):
        return jsonify(err="Invalid category"), 400
    with _lock:
        fx = {"id": _nxt_sfx, "name": name, "category": cat}
        for k in ("shape", "r", "g", "b", "r2", "g2", "b2",
                  "size", "motion", "blend", "fixtureIds", "params",
                  "actionType"):
            if k in body:
                fx[k] = body[k]
        # Defaults
        fx.setdefault("shape", "sphere")
        fx.setdefault("r", 255)
        fx.setdefault("g", 255)
        fx.setdefault("b", 255)
        fx.setdefault("blend", "replace")
        fx.setdefault("size", {"radius": 1000})
        fx.setdefault("motion", {"startPos": [0,0,0], "endPos": [5000,0,0], "easing": "linear", "durationS": 5})
        fx.setdefault("fixtureIds", [])
        _spatial_fx.append(fx)
        _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True, id=fx["id"])

@app.get("/api/spatial-effects/<int:fxid>")
def api_sfx_detail(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    return jsonify(fx)

@app.put("/api/spatial-effects/<int:fxid>")
def api_sfx_update(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    for k in ("name", "category", "shape", "r", "g", "b", "r2", "g2", "b2",
              "size", "motion", "blend", "fixtureIds", "params", "actionType"):
        if k in body:
            fx[k] = body[k]
    _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True)

@app.delete("/api/spatial-effects/<int:fxid>")
def api_sfx_delete(fxid):
    global _spatial_fx
    _spatial_fx = [f for f in _spatial_fx if f["id"] != fxid]
    _save("spatial_fx", _spatial_fx)
    return jsonify(ok=True)

@app.post("/api/spatial-effects/<int:fxid>/evaluate")
def api_sfx_evaluate(fxid):
    fx = next((f for f in _spatial_fx if f["id"] == fxid), None)
    if not fx:
        return jsonify(err="Not found"), 404
    t = float(request.args.get("t", 0))
    # Gather pixel positions from targeted fixtures
    fix_ids = fx.get("fixtureIds", [])
    all_pixels = []
    for fid in fix_ids:
        fixture = next((f for f in _fixtures if f["id"] == fid), None)
        if fixture:
            resolved = resolve_fixture(_build_resolve_input(fixture))
            all_pixels.extend(resolved.get("pixelPositions", []))
    if not all_pixels:
        # Fall back: all fixtures
        for fixture in _fixtures:
            resolved = resolve_fixture(_build_resolve_input(fixture))
            all_pixels.extend(resolved.get("pixelPositions", []))
    colors = evaluate_spatial_effect(fx, all_pixels, t)
    return jsonify(pixels=colors)

def _build_resolve_input(fixture):
    """Build resolve input dict from a fixture record."""
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    # Look up position by fixture ID first, then fall back to childId
    lp = pos_map.get(fixture["id"], pos_map.get(fixture.get("childId"), {}))
    child_pos = [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)]
    child = next((c for c in _children if c["id"] == fixture.get("childId")), None)
    strings = fixture.get("strings", [])
    has_leds = strings and any(s.get("leds", 0) > 0 for s in strings)
    if not has_leds and child:
        strings = [
            {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
            for s in child.get("strings", [])[:child.get("sc", 0)]
        ]
    return {
        "type": fixture.get("type", "linear"),
        "childPos": child_pos,
        "strings": strings,
        "rotation": fixture.get("rotation", [0, 0, 0]),
        "aoeRadius": fixture.get("aoeRadius", 1000),
    }

#  "  "  Timelines (Phase 4)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/timelines")
def api_timelines_get():
    return jsonify(_timelines)

@app.post("/api/timelines")
def api_timelines_create():
    global _nxt_tl
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(err="Name required"), 400
    with _lock:
        tl = {
            "id": _nxt_tl, "name": name,
            "durationS": body.get("durationS", 60),
            "tracks": body.get("tracks", []),
            "loop": body.get("loop", False),
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)
    return jsonify(ok=True, id=tl["id"])

@app.get("/api/timelines/<int:tid>")
def api_timeline_detail(tid):
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    return jsonify(tl)

@app.put("/api/timelines/<int:tid>")
def api_timeline_update(tid):
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    body = request.get_json(silent=True) or {}
    for k in ("name", "durationS", "tracks", "loop"):
        if k in body:
            tl[k] = body[k]
    _save("timelines", _timelines)
    return jsonify(ok=True)

@app.delete("/api/timelines/<int:tid>")
def api_timeline_delete(tid):
    global _timelines
    if not any(t["id"] == tid for t in _timelines):
        return jsonify(ok=False, err="timeline not found"), 404
    _timelines = [t for t in _timelines if t["id"] != tid]
    _save("timelines", _timelines)
    # Prune deleted timeline from playlist
    pl_order = _show_playlist.get("order", [])
    if tid in pl_order:
        _show_playlist["order"] = [t for t in pl_order if t != tid]
        _save("show_playlist", _show_playlist)
    return jsonify(ok=True)

@app.post("/api/timelines/<int:tid>/frame")
def api_timeline_frame(tid):
    """Evaluate all active clips at time t, return per-fixture pixel colors."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    t = float(request.args.get("t", 0))

    # Expand allPerformers and group fixtures into per-fixture tracks
    fix_map_local = {f["id"]: f for f in _fixtures}
    raw_tracks = tl.get("tracks", [])
    tracks = []
    for track in raw_tracks:
        if track.get("allPerformers"):
            for f in _fixtures:
                if f.get("type") != "group":
                    tracks.append({"fixtureId": f["id"], "clips": list(track.get("clips", []))})
        else:
            # Expand group fixtures to their members
            fid = track.get("fixtureId")
            grp = fix_map_local.get(fid)
            if grp and grp.get("type") == "group" and grp.get("childIds"):
                for mid in grp["childIds"]:
                    if mid in fix_map_local:
                        tracks.append({"fixtureId": mid, "clips": list(track.get("clips", []))})
                continue
            tracks.append(track)

    result = {}  # fixture_id  -' [r,g,b] array
    for track in tracks:
        fix_id = track.get("fixtureId")
        fixture = next((f for f in _fixtures if f["id"] == fix_id), None)
        if not fixture:
            continue

        # Resolve pixel positions for this fixture
        resolved = resolve_fixture(_build_resolve_input(fixture))
        pixels = resolved.get("pixelPositions", [])
        if not pixels:
            continue

        # Find active clips at time t
        layers = []
        modes = []
        for clip in track.get("clips", []):
            cs = clip.get("startS", 0)
            cd = clip.get("durationS", 1)
            if cs <= t < cs + cd:
                # Handle classic action clips   " fill all pixels with action color
                aid = clip.get("actionId")
                if aid is not None:
                    act = next((a for a in _actions if a["id"] == aid), None)
                    if act:
                        col = [act.get("r", 0), act.get("g", 0), act.get("b", 0)]
                        layers.append([col] * len(pixels))
                        modes.append("replace")
                    continue
                # Get the spatial effect
                eid = clip.get("effectId")
                fx = next((f for f in _spatial_fx if f["id"] == eid), None)
                if not fx:
                    continue
                local_t = t - cs
                # Scale local_t to effect's motion duration
                motion = fx.get("motion", {})
                fx_dur = motion.get("durationS", cd) or cd
                scaled_t = local_t * (fx_dur / cd) if cd > 0 else 0
                colors = evaluate_spatial_effect(fx, pixels, scaled_t)
                layers.append(colors)
                modes.append(fx.get("blend", "replace"))

        if layers:
            blended = blend_pixel_layers(layers, modes)
            result[str(fix_id)] = blended
        else:
            result[str(fix_id)] = [[0,0,0]] * len(pixels)

    return jsonify(result)

#  "  "  Baking (Phase 5)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/timelines/<int:tid>/bake")
def api_timeline_bake(tid):
    """Start baking a timeline (background thread)."""
    global _bake_progress
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    if _bake_progress and not _bake_progress.done:
        return jsonify(err="Bake already in progress"), 409

    n_frames = int(math.ceil(tl.get("durationS", 60) * 40))
    _bake_progress = BakeProgress(n_frames)

    # Pre-enrich fixtures with child string data so the bake engine can resolve pixels
    enriched_fixtures = []
    for f in _fixtures:
        ef = dict(f)
        fix_strings = ef.get("strings", [])
        has_leds = fix_strings and any(s.get("leds", 0) > 0 for s in fix_strings)
        if not has_leds:
            child = next((c for c in _children if c["id"] == ef.get("childId")), None)
            if child:
                ef["strings"] = [
                    {"leds": s.get("leds", 0), "mm": s.get("mm", 1000), "sdir": s.get("sdir", 0)}
                    for s in child.get("strings", [])[:child.get("sc", 0)]
                ]
        enriched_fixtures.append(ef)

    log.info("BAKE: timeline %d '%s' dur=%ds frames=%d fixtures=%d clips=%d effects=%d",
             tid, tl.get("name"), tl.get("durationS", 0), n_frames, len(enriched_fixtures),
             sum(len(t.get("clips", [])) for t in tl.get("tracks", [])),
             len(_spatial_fx))
    for ef in enriched_fixtures:
        ft = ef.get("fixtureType", "led")
        strings = ef.get("strings", [])
        leds = sum(s.get("leds", 0) for s in strings)
        log.info("  fixture %d '%s' type=%s strings=%d leds=%d rot=%s pos=(%s,%s)",
                 ef.get("id"), ef.get("name"), ft, len(strings), leds,
                 ef.get("rotation"), ef.get("x", "?"), ef.get("y", "?"))
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    placed = [f for f in enriched_fixtures if f["id"] in pos_map]
    log.info("BAKE: %d/%d fixtures have layout positions", len(placed), len(enriched_fixtures))

    def _bake_thread():
        global _bake_result
        try:
            result = bake_timeline(
                tl, enriched_fixtures, _spatial_fx, _layout,
                resolve_fn=resolve_fixture,
                evaluate_fn=evaluate_spatial_effect,
                blend_fn=blend_pixel_layers,
                progress=_bake_progress,
                actions=_actions,
                profile_lib=_profile_lib,
                mover_calibrations=_mover_cal,
            )
            n_fix = len(result.get("fixtures", {}))
            n_frames_out = result.get("totalFrames", 0)
            lsq_size = sum(len(v) for v in result.get("lsq_files", {}).values())
            preview_keys = list(result.get("preview", {}).keys())
            log.info("BAKE DONE: %d fixtures, %d frames, %d LSQ bytes, preview keys=%s",
                     n_fix, n_frames_out, lsq_size, preview_keys[:5])
            # Store result
            _bake_result[tid] = {
                "timelineId": tid,
                "bakedAt": int(time.time()),
                "fixtures": result["fixtures"],
                "totalFrames": result["totalFrames"],
                "fps": result["fps"],
                "lsqSize": lsq_size,
                "preview": result.get("preview", {}),
            }
            # Save LSQ files to data/baked/
            baked_dir = DATA / "baked"
            baked_dir.mkdir(parents=True, exist_ok=True)
            for fix_id, lsq_data in result.get("lsq_files", {}).items():
                (baked_dir / f"fixture_{fix_id}.lsq").write_bytes(lsq_data)
            zip_data = pack_lsq_zip(result.get("lsq_files", {}))
            (baked_dir / f"timeline_{tid}.zip").write_bytes(zip_data)
        except Exception as e:
            import traceback
            log.error("BAKE FAILED: %s\n%s", e, traceback.format_exc())
            _bake_progress.error = str(e)
            _bake_progress.done = True

    threading.Thread(target=_bake_thread, daemon=True).start()
    return jsonify(ok=True, message="Bake started")

@app.get("/api/timelines/<int:tid>/baked/status")
def api_bake_status(tid):
    if not _bake_progress:
        return jsonify(running=False, done=False, progress=0)
    return jsonify(_bake_progress.to_dict())

@app.get("/api/timelines/<int:tid>/baked")
def api_bake_result(tid):
    result = _bake_result.get(tid)
    if not result:
        return jsonify(err="No baked data for this timeline"), 404
    return jsonify(result)

@app.get("/api/timelines/<int:tid>/baked/download")
def api_bake_download(tid):
    zip_path = DATA / "baked" / f"timeline_{tid}.zip"
    if not zip_path.exists():
        return jsonify(err="No baked data"), 404
    return send_file(str(zip_path), mimetype="application/zip",
                     as_attachment=True, download_name=f"timeline_{tid}_lsq.zip")

@app.get("/api/timelines/<int:tid>/baked/preview")
def api_bake_preview(tid):
    result = _bake_result.get(tid)
    if not result:
        log.debug("PREVIEW: no bake result for timeline %d (available: %s)", tid, list(_bake_result.keys()))
        return jsonify(err="No baked data"), 404
    preview = result.get("preview", {})
    log.debug("PREVIEW: timeline %d -> %d fixture keys, sample: %s",
              tid, len(preview), list(preview.keys())[:3])
    return jsonify(preview)

# Sync progress   " tracks per-child sync state for UI polling
_sync_progress = None  # dict when active

@app.post("/api/timelines/<int:tid>/baked/sync")
def api_bake_sync(tid):
    """Sync baked segments to all children. Runs in background with progress tracking."""
    global _sync_progress
    result = _bake_result.get(tid)
    if not result:
        return jsonify(err="No baked data - bake first"), 404

    targets = [c for c in _children if c.get("ip")]
    if not targets:
        return jsonify(ok=True, synced=0, warn="no performers registered")

    # Build per-child sync plan
    plan = []  # [{child, steps, fixture_name}]
    for fix_id_str, fix_data in result.get("fixtures", {}).items():
        fix_id = int(fix_id_str) if isinstance(fix_id_str, str) else fix_id_str
        fixture = next((f for f in _fixtures if f["id"] == fix_id), None)
        if not fixture:
            continue
        child = next((c for c in targets if c["id"] == fixture.get("childId")), None)
        if not child:
            continue
        segments = fix_data.get("segments", [])
        fix_strings = fixture.get("strings", [])
        steps = []
        # Per-pixel effect types where speedMs = time per pixel step
        PER_PIXEL_TYPES = {4, 7, 10, 11}  # CHASE, COMET, WIPE, SCANNER
        # Directional effect types (use direction param)
        DIR_TYPES = {4, 5, 7, 10, 11}  # CHASE, RAINBOW, COMET, WIPE, SCANNER
        # Direction flip map: E -"W, N -"S
        DIR_FLIP = {0: 2, 1: 3, 2: 0, 3: 1}
        REF_PITCH_MM = 16.67  # 60 LEDs/m reference density
        for seg in segments[:16]:
            step = dict(seg.get("params", {}))
            step["type"] = seg.get("type", 0)
            step["durationS"] = max(1, int(math.ceil(seg.get("durationS", 1))))
            # Per-string LED range override from bake
            if "ledOffset" in seg:
                step["_ledOffset"] = seg["ledOffset"]
                step["_ledCount"] = seg["ledCount"]
                step["_stringIndex"] = seg.get("stringIndex", 0)
            si = seg.get("stringIndex", 0)
            sinfo = fix_strings[si] if si < len(fix_strings) else {}
            # Map action direction to string physical direction:
            # if string faces W or S, flip the effect direction so the
            # visual sweep matches physical orientation
            if step["type"] in DIR_TYPES:
                sdir = sinfo.get("sdir", 0)
                if sdir in (2, 3):  # West or South   " flip direction
                    step["direction"] = DIR_FLIP.get(step.get("direction", 0), 0)
            # Normalize speedMs for per-pixel effects so physical speed is
            # consistent regardless of LED density (50 LEDs/1m = 150 LEDs/1m)
            if step["type"] in PER_PIXEL_TYPES and step.get("speedMs", 0) > 0:
                leds = sinfo.get("leds", 0)
                mm = sinfo.get("mm", 0)
                if leds > 0 and mm > 0:
                    pitch = mm / leds
                    step["speedMs"] = max(1, round(step["speedMs"] * pitch / REF_PITCH_MM))
            steps.append(step)
        # Append final blackout so LEDs turn off when the show ends
        if steps and steps[-1].get("type", 0) != 0 and len(steps) < 16:
            steps.append({"type": 0, "durationS": 1, "r": 0, "g": 0, "b": 0})
        if steps:
            plan.append({"child": child, "steps": steps, "name": fixture.get("name", "?")})

    # Initialize progress
    _sync_progress = {
        "done": False, "allReady": False,
        "performers": {p["child"]["id"]: {
            "name": p.get("name") or p["child"].get("name") or p["child"].get("hostname"),
            "ip": p["child"]["ip"],
            "status": "pending", "stepsLoaded": 0, "totalSteps": len(p["steps"]),
            "retries": 0, "verified": False, "error": None
        } for p in plan},
        "totalPerformers": len(plan), "readyCount": 0,
    }

    def _sync_thread():
        MAX_RETRIES = 3
        # Stop any running show first   " both on children and server state
        pkt_stop = _hdr(CMD_RUNNER_STOP)
        pkt_off = _hdr(CMD_ACTION_STOP)
        for c in _children:
            if c.get("ip"):
                _send(c["ip"], pkt_stop)
                _send(c["ip"], pkt_off)
        with _lock:
            _settings["runnerRunning"] = False
            _settings["activeTimeline"] = -1
            _save("settings", _settings)
        time.sleep(0.15)

        bri = _settings.get("globalBrightness", 255)
        bri_pkt = _hdr(CMD_SET_BRIGHTNESS) + bytes([bri & 0xFF])

        for p in plan:
            child = p["child"]
            cid = child["id"]
            steps = p["steps"]
            ip = child["ip"]
            prog = _sync_progress["performers"][cid]
            prog["status"] = "syncing"

            _send(ip, bri_pkt)
            time.sleep(0.02)

            # Send each step with retry
            all_ok = True
            for idx, step in enumerate(steps):
                pkt = _load_step_pkt(idx, len(steps), step, child, 0)
                sent = False
                for attempt in range(MAX_RETRIES):
                    _send(ip, pkt)
                    time.sleep(0.04)
                    # Simple verification: send and trust (LOAD_ACK comes async via UDP listener)
                    sent = True
                    break
                if sent:
                    prog["stepsLoaded"] = idx + 1
                else:
                    prog["error"] = f"Step {idx} failed after {MAX_RETRIES} retries"
                    all_ok = False
                    break

            if all_ok:
                prog["status"] = "verifying"
                # Verify child is alive via HTTP /status (more reliable than UDP)
                verified = False
                for attempt in range(MAX_RETRIES):
                    try:
                        import urllib.request
                        resp = urllib.request.urlopen(f"http://{ip}/status", timeout=3)
                        if resp.status == 200:
                            verified = True
                            break
                    except Exception:
                        pass
                    prog["retries"] = attempt + 1
                    time.sleep(0.2)
                # If HTTP failed, still consider it loaded (steps were sent successfully)
                if not verified and prog["stepsLoaded"] == prog["totalSteps"]:
                    verified = True
                    prog["status"] = "ready"
                    log.info("SYNC: %s HTTP verify failed but all steps loaded - accepting", ip)
                prog["verified"] = verified
                prog["status"] = "ready" if verified else "unverified"
                if verified:
                    _sync_progress["readyCount"] = _sync_progress.get("readyCount", 0) + 1
            else:
                prog["status"] = "failed"

        _sync_progress["done"] = True
        _sync_progress["allReady"] = _sync_progress["readyCount"] == len(plan)

    threading.Thread(target=_sync_thread, daemon=True).start()
    return jsonify(ok=True, performers=len(plan))

@app.get("/api/timelines/<int:tid>/sync/status")
def api_sync_status(tid):
    if not _sync_progress:
        return jsonify(done=False, performers={})
    return jsonify(_sync_progress)

#  "  "  Show Execution (Phase 6)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_dmx_playback_stop = threading.Event()

_PATROL_SPEED_PRESETS = {"slow": 20.0, "medium": 10.0, "fast": 5.0}

def _evaluate_object_patrols(elapsed):
    """Update positions of patrolling objects based on elapsed playback time.

    Motion patterns:
      pingpong — oscillate back and forth along axis (default)
      circle   — circular motion in the horizontal plane (XY)
      figure8  — figure-8 pattern in the horizontal plane (XY)
      square   — rectangular path along the perimeter of the range

    Bounding box: if patrol.boundingObject is set to another object's name,
    the patrol range is derived from that object's transform (pos + scale)
    instead of using startPct/endPct of the stage dimensions.
    """
    sw = _stage.get("w", 10) * 1000  # stage width in mm (X)
    sd = _stage.get("d", 10) * 1000  # stage depth in mm (Y)
    sh = _stage.get("h", 5) * 1000   # stage height in mm (Z)
    dims = {"x": sw, "y": sd, "z": sh}
    all_objs = _objects + _temporal_objects
    # Build name→object lookup for bounding box references
    obj_by_name = {o.get("name", ""): o for o in all_objs if o.get("name")}

    # Build set of active Track action target object IDs (for on-demand patrol check)
    active_track_obj_ids = set()
    if _settings.get("runnerRunning"):
        active_tid = _settings.get("activeTimeline", -1)
        active_tl = next((t for t in _timelines if t["id"] == active_tid), None) if active_tid >= 0 else None
        if active_tl:
            for tr in active_tl.get("tracks", []):
                for cl in tr.get("clips", []):
                    aid = cl.get("actionId")
                    act = next((a for a in _actions if a.get("id") == aid), None) if aid is not None else None
                    if act and act.get("type") == 18:
                        for oid in (act.get("trackObjectIds") or []):
                            active_track_obj_ids.add(oid)

    for obj in all_objs:
        if obj.get("mobility") != "moving":
            continue
        pat = obj.get("patrol")
        if not pat or not pat.get("enabled"):
            continue
        # On-demand patrols only animate when linked Track action is in active timeline
        if pat.get("patrolMode") == "on-demand" and obj["id"] not in active_track_obj_ids:
            continue
        preset = pat.get("speedPreset", "medium")
        cycle_s = _PATROL_SPEED_PRESETS.get(preset, pat.get("cycleS", 10.0))
        if cycle_s <= 0:
            continue
        easing = pat.get("easing", "sine")
        pattern = pat.get("pattern", "pingpong")

        # Phase: 0→1 over one full cycle
        phase = (elapsed % cycle_s) / cycle_s

        # Determine bounding range — either from a named bounding object or stage %
        bound_obj_name = pat.get("boundingObject", "")
        if bound_obj_name and bound_obj_name in obj_by_name:
            # Use the bounding object's transform as the motion range
            bo = obj_by_name[bound_obj_name]
            bt = bo.get("transform", {})
            bp = bt.get("pos", [0, 0, 0])
            bs = bt.get("scale", [1000, 1000, 1000])
            x_lo, x_hi = bp[0], bp[0] + bs[0]
            y_lo, y_hi = bp[1], bp[1] + bs[1]
            z_lo, z_hi = bp[2], bp[2] + bs[2]
        else:
            start_pct = pat.get("startPct", 10) / 100.0
            end_pct = pat.get("endPct", 90) / 100.0
            x_lo, x_hi = sw * start_pct, sw * end_pct
            y_lo, y_hi = sd * start_pct, sd * end_pct
            z_lo, z_hi = 0, 0  # floor level for horizontal patterns

        # Center and half-size for circular/figure-8 patterns
        cx = (x_lo + x_hi) / 2.0
        cy = (y_lo + y_hi) / 2.0
        rx = (x_hi - x_lo) / 2.0
        ry = (y_hi - y_lo) / 2.0

        pos = obj.get("transform", {}).get("pos", [0, 0, 0])
        new_pos = list(pos)

        if pattern == "circle":
            # Circular motion in XY plane
            angle = phase * 2.0 * math.pi
            if easing == "sine":
                angle = phase * 2.0 * math.pi  # already smooth for circle
            new_pos[0] = cx + rx * math.cos(angle)
            new_pos[1] = cy + ry * math.sin(angle)

        elif pattern == "figure8":
            # Figure-8 (lissajous): use uniform radius so loops are round
            r = min(rx, ry)
            angle = phase * 2.0 * math.pi
            new_pos[0] = cx + r * math.sin(angle)
            new_pos[1] = cy + r * math.sin(2.0 * angle)

        elif pattern == "square":
            # Rectangular perimeter path: 4 equal segments
            # Segment 0: left→right (bottom), 1: bottom→top (right),
            # 2: right→left (top), 3: top→bottom (left)
            seg = int(phase * 4) % 4
            seg_t = (phase * 4) % 1.0
            if easing == "sine":
                seg_t = 0.5 - 0.5 * math.cos(seg_t * math.pi)
            if seg == 0:
                new_pos[0] = x_lo + seg_t * (x_hi - x_lo)
                new_pos[1] = y_lo
            elif seg == 1:
                new_pos[0] = x_hi
                new_pos[1] = y_lo + seg_t * (y_hi - y_lo)
            elif seg == 2:
                new_pos[0] = x_hi - seg_t * (x_hi - x_lo)
                new_pos[1] = y_hi
            else:
                new_pos[0] = x_lo
                new_pos[1] = y_hi - seg_t * (y_hi - y_lo)

        else:
            # Default: pingpong — back-and-forth along axis
            t = 1.0 - abs(2.0 * phase - 1.0)  # triangle wave 0→1→0
            if easing == "sine":
                t = 0.5 - 0.5 * math.cos(t * math.pi)
            axis = pat.get("axis", "x")
            for ax in (list(axis) if len(axis) > 1 else [axis]):
                dim = dims.get(ax, sw)
                start_pct = pat.get("startPct", 10) / 100.0
                end_pct = pat.get("endPct", 90) / 100.0
                lo = dim * start_pct
                hi = dim * end_pct
                if bound_obj_name and bound_obj_name in obj_by_name:
                    lo = {"x": x_lo, "y": y_lo, "z": z_lo}.get(ax, lo)
                    hi = {"x": x_hi, "y": y_hi, "z": z_hi}.get(ax, hi)
                idx = {"x": 0, "y": 1, "z": 2}.get(ax, 0)
                new_pos[idx] = lo + t * (hi - lo)

        obj.setdefault("transform", {})["pos"] = new_pos

def _evaluate_track_actions(elapsed, engine, dmx_fixtures):
    """Evaluate active Track actions -- compute real-time pan/tilt for moving heads."""
    track_actions = [a for a in _actions if a.get("type") == 18]
    if not track_actions:
        return
    all_objects = _objects + _temporal_objects
    moving_objects = [o for o in all_objects if o.get("mobility") == "moving"]
    # Pre-filter by objectType per Track action below
    # Build fixture lookup: id -> fixture info (with profile pan/tilt range)
    # Positions live in _layout["children"], not in _fixtures
    pos_map = {p["id"]: p for p in _layout.get("children", [])}
    fx_lookup = {}
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pid = f.get("dmxProfileId")
        prof = _profile_lib.get_profile(pid) if pid else None
        pan_range = prof.get("panRange", 0) if prof else 0
        tilt_range = prof.get("tiltRange", 0) if prof else 0
        if pan_range > 0 and tilt_range > 0:
            lp = pos_map.get(f["id"], {})
            fx_lookup[f["id"]] = {
                "fixture": f, "pan_range": pan_range, "tilt_range": tilt_range,
                "prof_info": _profile_lib.channel_info(pid) if pid else None,
                "pos": [lp.get("x", 0), lp.get("y", 0), lp.get("z", 0)],
                "mounted_inverted": bool(f.get("mountedInverted", False)),
            }
    if not fx_lookup:
        return
    for ta in track_actions:
        # Resolve target objects:
        #   trackObjectIds set → use those specific objects
        #   trackObjectType set → filter moving objects by type (e.g. "figure8-target")
        #   neither → all temporal moving objects (camera detections, not patrol objects)
        obj_type = ta.get("trackObjectType")
        target_ids = ta.get("trackObjectIds", [])
        if obj_type:
            candidates = [o for o in moving_objects if o.get("objectType") == obj_type]
        else:
            candidates = [o for o in moving_objects if o.get("_temporal")]
        targets = [o for o in candidates if o["id"] in target_ids] if target_ids else candidates
        # If this action has explicit trackObjectIds but none exist, skip entirely —
        # don't blackout heads just because a deleted patrol object is missing.
        # Only auto-discover actions (no trackObjectIds) blackout when no targets found.
        if target_ids and not targets:
            continue
        # Resolve fixtures
        fix_ids = ta.get("trackFixtureIds", [])
        heads = [fx_lookup[fid] for fid in (fix_ids or fx_lookup.keys()) if fid in fx_lookup]
        if not heads:
            continue
        # Global offset
        g_off = ta.get("trackOffset", [0, 0, 0])
        per_fx_off = ta.get("trackFixtureOffsets", {})
        auto_spread = ta.get("trackAutoSpread", False)
        fixed_assign = ta.get("trackFixedAssignment", False)
        cycle_ms = ta.get("trackCycleMs", 2000)
        cycle_s = max(cycle_ms / 1000.0, 0.1)
        n_heads = len(heads)
        n_targets = len(targets)
        # Track which heads get assigned — unassigned heads get blackout
        assigned_heads = set()
        if not targets:
            n_targets = 0  # will blackout all heads below
        for hi, head_info in enumerate(heads):
            if not targets:
                break  # skip aim loop, go to blackout
            f = head_info["fixture"]
            fid = f["id"]
            # #511 — skip show output for fixtures mid-calibration.
            if f.get("isCalibrating"):
                continue
            fx_pos = head_info["pos"]
            # Assignment: 1 person = all heads aim at them,
            # 2 people = 1:1, 3+ people (fixed) = first N only
            if n_heads > n_targets:
                # More heads than targets: all heads aim at available targets (spread)
                obj = targets[hi % n_targets]
            elif fixed_assign and n_targets > n_heads:
                # Fixed 1:1 — each head gets one target, excess people ignored
                obj = targets[hi]
            elif n_heads <= n_targets:
                # Cycling: this head covers a chunk of targets
                chunk_size = max(1, n_targets // n_heads)
                chunk_start = hi * chunk_size
                chunk = targets[chunk_start:chunk_start + chunk_size]
                if hi == n_heads - 1:
                    chunk = targets[chunk_start:]  # last head gets remainder
                if len(chunk) > 1:
                    idx = int(elapsed / cycle_s) % len(chunk)
                    obj = chunk[idx]
                else:
                    obj = chunk[0]
            else:
                # More heads than targets: spread heads across targets
                obj = targets[hi % n_targets]
            # Q5 — hold last good when the target placement is raw-tier
            # (camera not calibrated + no position). Acting on tier='raw'
            # means swinging the head to a random spot derived from a
            # proportional pixel mapping; better to freeze the head at its
            # current aim until the camera is calibrated or re-positioned.
            if obj.get("_method") == "raw":
                continue
            # Q4 — aimTarget picks feet / center / head point on the target.
            # Default "feet" matches the operator-preferred intuition ("aim
            # the spot at where the person stands"). Falls back to transform.pos
            # when _anchors isn't present (older objects or non-temporal props).
            aim_target_mode = (ta.get("aimTarget") or "feet").lower()
            if aim_target_mode not in ("feet", "center", "head"):
                aim_target_mode = "feet"
            _anchors = obj.get("_anchors") or {}
            obj_pos = _anchors.get(aim_target_mode) or obj.get("transform", {}).get("pos", [0, 0, 0])
            # Apply offsets
            p_off = per_fx_off.get(str(fid), [0, 0, 0])
            # Auto-spread when multiple heads on same object
            spread_off = [0, 0, 0]
            if auto_spread and n_heads > n_targets:
                heads_on_this = n_heads // n_targets + (1 if hi % n_targets < n_heads % n_targets else 0)
                if heads_on_this > 1:
                    obj_w = obj.get("transform", {}).get("scale", [500, 1800, 500])[0]
                    local_idx = (hi // n_targets)
                    spread_off[0] = (local_idx - (heads_on_this - 1) / 2.0) * obj_w / max(heads_on_this, 1)
            aim = [obj_pos[i] + g_off[i] + p_off[i] + spread_off[i] for i in range(3)]
            # Clamp to stage bounds (X=width, Y=depth, Z=height)
            sw = _stage.get("w", 10) * 1000
            sd = _stage.get("d", 10) * 1000
            sh = _stage.get("h", 5) * 1000
            aim[0] = max(0, min(sw, aim[0]))
            aim[1] = max(0, min(sd, aim[1]))
            aim[2] = max(0, min(sh, aim[2]))
            # Compute pan/tilt — hybrid affine + geometric blend (#437)
            pan = tilt = None
            inverted = head_info.get("mounted_inverted", False)
            orient = f.get("orientation", {})
            # 1. Mover calibration affine (manual calibration with stage samples)
            mcal_data = _mover_cal.get(str(fid))
            if mcal_data and mcal_data.get("samples") and len(mcal_data["samples"]) >= 2:
                pt_affine = _mcal.affine_pan_tilt(mcal_data["samples"], aim[0], aim[1], aim[2])
                if pt_affine:
                    samps = mcal_data["samples"]
                    # Compute distance from aim to nearest calibration sample
                    min_dist = min(
                        math.sqrt((aim[0] - s.get("stageX", 0))**2 +
                                  (aim[1] - s.get("stageY", 0))**2 +
                                  (aim[2] - s.get("stageZ", 0))**2)
                        for s in samps
                    )
                    # Bounding box of calibration samples
                    sx = [s.get("stageX", 0) for s in samps]
                    sy = [s.get("stageY", 0) for s in samps]
                    bbox_diag = math.sqrt((max(sx) - min(sx))**2 + (max(sy) - min(sy))**2) or 1000
                    # Blend: within bbox → pure affine; beyond bbox → blend to geometric
                    # fade_dist = distance beyond bbox at which geometric fully takes over
                    fade_dist = bbox_diag * 0.5
                    # How far outside the bbox is the aim point?
                    outside_x = max(0, min(sx) - aim[0], aim[0] - max(sx))
                    outside_y = max(0, min(sy) - aim[1], aim[1] - max(sy))
                    outside_dist = math.sqrt(outside_x**2 + outside_y**2)
                    if outside_dist <= 0:
                        # Inside calibrated region — pure affine
                        pan, tilt = max(0.0, min(1.0, pt_affine[0])), max(0.0, min(1.0, pt_affine[1]))
                    else:
                        # Outside — blend affine → geometric
                        blend = min(1.0, outside_dist / fade_dist)  # 0=affine, 1=geometric
                        if orient.get("verified"):
                            pt_geo = _mcal.compute_aim_with_orientation(
                                fx_pos, aim, orient, head_info["pan_range"], head_info["tilt_range"])
                        else:
                            pt_geo = compute_pan_tilt(fx_pos, aim, head_info["pan_range"],
                                                      head_info["tilt_range"], mounted_inverted=inverted)
                        if pt_geo:
                            aff_p = max(0.0, min(1.0, pt_affine[0]))
                            aff_t = max(0.0, min(1.0, pt_affine[1]))
                            pan = aff_p + blend * (pt_geo[0] - aff_p)
                            tilt = aff_t + blend * (pt_geo[1] - aff_t)
                        else:
                            # Geometric failed — use clamped affine as last resort
                            pan = max(0.0, min(1.0, pt_affine[0]))
                            tilt = max(0.0, min(1.0, pt_affine[1]))
            # 2. Range calibration (automated axis mapping)
            if pan is None:
                pt_cal = compute_pan_tilt_calibrated(fid, aim)
                if pt_cal:
                    pan, tilt = pt_cal
            # 3. Geometric fallback (no calibration data at all)
            if pan is None:
                if orient.get("verified"):
                    pt = _mcal.compute_aim_with_orientation(
                        fx_pos, aim, orient, head_info["pan_range"], head_info["tilt_range"])
                else:
                    pt = compute_pan_tilt(fx_pos, aim, head_info["pan_range"], head_info["tilt_range"],
                                          mounted_inverted=inverted)
                if pt is None:
                    continue
                pan, tilt = pt
            # Write to DMX universe
            prof_info = head_info["prof_info"]
            if prof_info:
                profile = {"channel_map": prof_info.get("channel_map"), "channels": prof_info.get("channels", [])}
                uni_buf = engine.get_universe(f.get("dmxUniverse", 1))
                addr = f.get("dmxStartAddr", 1)
                uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
                # Track action also sets dimmer + color so the beam is visible
                tr = ta.get("trackDimmer", 255)
                uni_buf.set_fixture_dimmer(addr, tr, profile)
                cm = prof_info.get("channel_map", {})
                if "red" in cm:
                    # RGB fixture — set color from action
                    uni_buf.set_fixture_rgb(addr, ta.get("r", 255), ta.get("g", 255), ta.get("b", 255), profile)
                elif "color-wheel" in cm:
                    # Color wheel fixture — resolve action RGB to closest wheel slot
                    from dmx_profiles import rgb_to_wheel_slot
                    cw_val = ta.get("colorWheel")
                    if cw_val is None:
                        cw_val = rgb_to_wheel_slot(prof_info, ta.get("r", 255), ta.get("g", 255), ta.get("b", 255))
                    uni_buf.set_channel(addr + cm["color-wheel"], cw_val)
                # Apply channel defaults (strobe open, etc.) so beam is visible
                for ch in prof_info.get("channels", []):
                    ch_type = ch.get("type", "")
                    default = ch.get("default")
                    if default is not None and ch_type not in ("pan", "tilt", "dimmer", "red", "green", "blue", "color-wheel"):
                        uni_buf.set_channel(addr + ch.get("offset", 0), int(default))
                assigned_heads.add(hi)
        # Blackout unassigned heads (no target = beam off)
        for hi, head_info in enumerate(heads):
            if hi not in assigned_heads:
                f = head_info["fixture"]
                prof_info = head_info["prof_info"]
                if prof_info:
                    profile = {"channel_map": prof_info.get("channel_map"), "channels": prof_info.get("channels", [])}
                    uni_buf = engine.get_universe(f.get("dmxUniverse", 1))
                    uni_buf.set_fixture_dimmer(f.get("dmxStartAddr", 1), 0, profile)

def _dmx_playback_loop(tid, go_epoch, duration, loop):
    """Background thread: stream DMX channel data during show playback."""
    result = _bake_result.get(tid)
    has_track_actions = any(a.get("type") == 18 for a in _actions)
    if not result and not has_track_actions:
        log.warning("DMX playback: no bake result for timeline %d and no Track actions", tid)
        return
    if not result:
        log.info("DMX playback: no bake result but Track actions present — running for tracking")
        result = {"fixtures": {}}
    baked_fixtures = result.get("fixtures", {})
    # Collect DMX fixtures with their baked segments
    dmx_fixtures = []
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f["id"]
        # Bake result keys can be int or str depending on JSON round-trip
        fix_data = baked_fixtures.get(fid) or baked_fixtures.get(str(fid), {})
        segs = fix_data.get("segments", [])
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        pid = f.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        ch_map = prof_info.get("channel_map") if prof_info else None
        channels = prof_info.get("channels", []) if prof_info else []
        log.info("DMX playback: fixture %d '%s' uni=%d addr=%d segs=%d profile=%s",
                 fid, f.get("name", "?"), uni, addr, len(segs), pid or "none")
        if not segs:
            log.warning("DMX playback: fixture %d has 0 segments - skipping", fid)
            continue
        dmx_fixtures.append({"fid": fid, "name": f.get("name", "?"),
                             "uni": uni, "addr": addr, "ch_map": ch_map,
                             "channels": channels, "segs": segs})
    has_track_actions = any(a.get("type") == 18 for a in _actions)
    if not dmx_fixtures and not has_track_actions:
        log.warning("DMX playback: no DMX fixtures with segments and no Track actions")
        return
    if not dmx_fixtures:
        log.info("DMX playback: no baked segments but Track actions present — loop will run for tracking")
    log.info("DMX playback: %d fixture(s), duration=%ds, loop=%s", len(dmx_fixtures), duration, loop)
    # Auto-start Art-Net engine if not running
    proto = _dmx_settings.get("protocol", "artnet")
    engine = _artnet if proto == "artnet" else _sacn
    if not engine.running:
        engine.start()
        log.info("DMX playback: auto-started %s engine", proto)
    # Wait until go_epoch
    wait = go_epoch - time.time()
    if wait > 0:
        _dmx_playback_stop.wait(timeout=wait)
    if _dmx_playback_stop.is_set():
        return
    # 40Hz playback loop
    interval = 0.025
    next_frame = time.monotonic()
    frame_count = 0
    while not _dmx_playback_stop.is_set():
        now_mono = time.monotonic()
        if now_mono < next_frame:
            _dmx_playback_stop.wait(timeout=next_frame - now_mono)
            if _dmx_playback_stop.is_set():
                break
            continue
        next_frame += interval
        if next_frame < now_mono:
            next_frame = now_mono + interval
        elapsed = time.time() - go_epoch
        if elapsed < 0:
            continue
        if loop and duration > 0:
            elapsed = elapsed % duration
        elif elapsed > duration:
            break  # show ended
        # Evaluate each DMX fixture — merge ALL matching segments per-channel.
        # Higher-priority segments (_pri) override lower ones per-channel,
        # allowing e.g. a PT sweep to control pan/tilt while a base wash
        # controls color independently.
        for fx in dmx_fixtures:
            # #511 — skip playback for fixtures mid-calibration.
            if _fixture_is_calibrating(fx.get("id")):
                continue
            # Collect per-channel values: {channel_name: (value, priority)}
            ch_vals = {}
            for seg in fx["segs"]:
                ss = seg.get("startS", 0)
                sd = seg.get("durationS", 1)
                if ss <= elapsed < ss + sd:
                    p = seg.get("params", {})
                    pri = seg.get("_pri", 0)
                    for k, v in p.items():
                        if v is not None and (k not in ch_vals or pri >= ch_vals[k][1]):
                            ch_vals[k] = (v, pri)
            r = ch_vals.get("r", (0, 0))[0]
            g = ch_vals.get("g", (0, 0))[0]
            b = ch_vals.get("b", (0, 0))[0]
            pan = ch_vals.get("pan", (None, 0))[0]
            tilt = ch_vals.get("tilt", (None, 0))[0]
            dimmer = ch_vals.get("dimmer", (None, 0))[0]
            strobe = ch_vals.get("strobe", (None, 0))[0]
            gobo = ch_vals.get("gobo", (None, 0))[0]
            color_wheel = ch_vals.get("colorWheel", (None, 0))[0]
            prism = ch_vals.get("prism", (None, 0))[0]
            focus = ch_vals.get("focus", (None, 0))[0]
            zoom = ch_vals.get("zoom", (None, 0))[0]
            profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
            uni_buf = engine.get_universe(fx["uni"])
            # RGB or color-wheel resolution
            if fx["ch_map"] and "red" in fx["ch_map"]:
                uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
            elif fx["ch_map"] and "color-wheel" in fx["ch_map"] and (r or g or b):
                from dmx_profiles import rgb_to_wheel_slot
                cw = color_wheel if color_wheel is not None else rgb_to_wheel_slot(fx, r, g, b)
                uni_buf.set_channel(fx["addr"] + fx["ch_map"]["color-wheel"], cw)
            # Dimmer
            if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
            # Pan/Tilt
            if pan is not None and tilt is not None and profile:
                uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
            # Extra DMX channels via set_fixture_channels
            # Channel types use hyphenated names (color-wheel, gobo-rotation)
            extra_ch = {}
            if strobe is not None:
                extra_ch["strobe"] = strobe
            if gobo is not None:
                extra_ch["gobo"] = gobo
            if color_wheel is not None:
                extra_ch["color-wheel"] = color_wheel
            if prism is not None:
                extra_ch["prism"] = prism
            if focus is not None:
                extra_ch["focus"] = focus
            if zoom is not None:
                extra_ch["zoom"] = zoom
            if extra_ch and profile:
                uni_buf.set_fixture_channels(fx["addr"], extra_ch, profile)
        # ── Object patrols: update moving object positions ──
        _evaluate_object_patrols(elapsed)
        # ── Track action: real-time pan/tilt for moving heads following objects ──
        if frame_count % 40 == 0:  # reap temporals every 1s
            _reap_temporal_objects()
        _evaluate_track_actions(elapsed, engine, dmx_fixtures)
        frame_count += 1
        if frame_count == 1:
            log.info("DMX playback: first frame sent at elapsed=%.1fs", elapsed)
    log.info("DMX playback: stopped after %d frames", frame_count)
    # Blackout DMX fixtures on stop (#364) — zero all channels
    for fx in dmx_fixtures:
        profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
        uni_buf = engine.get_universe(fx["uni"])
        uni_buf.set_fixture_rgb(fx["addr"], 0, 0, 0, profile)
        if fx["ch_map"] and "dimmer" in fx["ch_map"]:
            uni_buf.set_fixture_dimmer(fx["addr"], 0, profile)
        if profile and fx["ch_map"]:
            zero_ch = {}
            for ch_type in ("pan", "tilt", "strobe", "gobo", "color-wheel", "prism", "focus", "zoom", "speed"):
                if ch_type in fx["ch_map"]:
                    zero_ch[ch_type] = 0
            if zero_ch:
                uni_buf.set_fixture_channels(fx["addr"], zero_ch, profile)

@app.post("/api/timelines/<int:tid>/start")
def api_timeline_start(tid):
    """Send RUNNER_GO to all children + start DMX playback thread."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404
    has_track_actions = any(a.get("type") == 18 for a in _actions)
    if tid not in _bake_result and not has_track_actions:
        return jsonify(err="Timeline not baked yet - bake first"), 400

    # Check sync is done
    if _sync_progress and not _sync_progress.get("done"):
        return jsonify(err="Sync still in progress - wait for it to finish"), 409

    # Send RUNNER_GO with 5s offset for NTP alignment
    go_epoch = int(time.time()) + 5
    loop_flag = 1 if tl.get("loop") else 0
    go_pkt = _hdr(CMD_RUNNER_GO, go_epoch) + struct.pack("<IB", go_epoch, loop_flag)

    started = 0
    for child in _children:
        if not child.get("ip"):
            continue
        _send(child["ip"], go_pkt)
        started += 1

    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeTimeline"] = tid
        _settings["runnerStartEpoch"] = go_epoch
        _save("settings", _settings)

    # Start DMX playback thread for DMX fixtures
    _dmx_playback_stop.clear()
    duration = tl.get("durationS", 60)
    loop = tl.get("loop", False)
    threading.Thread(target=_dmx_playback_loop, args=(tid, go_epoch, duration, loop),
                     daemon=True).start()

    return jsonify(ok=True, started=started, goEpoch=go_epoch)

@app.post("/api/timelines/<int:tid>/stop")
def api_timeline_stop(tid):
    """Stop timeline playback on all children + DMX playback thread + blackout."""
    # Stop DMX playback thread
    _dmx_playback_stop.set()

    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    stopped = 0
    for _attempt in range(3):
        for child in _children:
            if not child.get("ip"):
                continue
            _send(child["ip"], pkt_stop)
            _send(child["ip"], pkt_off)
            if _attempt == 0:
                stopped += 1

    # Blackout all DMX universes (#405)
    if _artnet.running:
        _artnet.blackout()

    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)

    return jsonify(ok=True, stopped=stopped)

@app.get("/api/timelines/<int:tid>/status")
def api_timeline_playback_status(tid):
    """Get playback status for a timeline."""
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        return jsonify(err="Not found"), 404

    running = _settings.get("runnerRunning") and _settings.get("activeTimeline") == tid
    elapsed = 0
    if running and _settings.get("runnerStartEpoch"):
        elapsed = max(0, int(time.time()) - _settings["runnerStartEpoch"])

    return jsonify(
        id=tid,
        name=tl.get("name", "Timeline"),
        running=running,
        elapsed=elapsed,
        durationS=tl.get("durationS", 0),
        loop=tl.get("loop", False),
        activeTimeline=_settings.get("activeTimeline", -1),
    )

#  "  "  Show playlist (sequential playback)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

# Show-level playback state (for sequential multi-timeline playback)
_show_playback = {
    "running": False,
    "currentIndex": 0,     # index into playlist order
    "currentTid": -1,
    "startEpoch": 0,
    "loopAll": False,
    "totalElapsed": 0,
}

@app.get("/api/show/playlist")
def api_show_playlist_get():
    """Return ordered timeline playlist + loop setting."""
    order = _show_playlist.get("order", [])
    # Build enriched list with timeline metadata
    items = []
    for tid in order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if tl:
            items.append({
                "id": tid,
                "name": tl.get("name", f"Timeline {tid}"),
                "durationS": tl.get("durationS", 0),
                "baked": tid in _bake_result,
            })
    total_duration = sum(it["durationS"] for it in items)
    return jsonify({
        "order": order,
        "loopAll": _show_playlist.get("loopAll", False),
        "items": items,
        "totalDurationS": total_duration,
    })


@app.post("/api/show/playlist")
def api_show_playlist_set():
    """Set ordered timeline playlist + loop setting."""
    data = request.get_json(silent=True) or {}
    if "order" in data:
        # Validate all IDs exist
        valid_ids = {t["id"] for t in _timelines}
        _show_playlist["order"] = [tid for tid in data["order"] if tid in valid_ids]
    if "loopAll" in data:
        _show_playlist["loopAll"] = bool(data["loopAll"])
    _save("show_playlist", _show_playlist)
    return jsonify(ok=True)


def _show_playback_loop(playlist_order, loop_all, go_epoch, start_idx=0):
    """Background thread: play timelines sequentially."""
    global _show_playback
    tl_list = []
    for tid in playlist_order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if not tl:
            continue
        # Include timeline if baked OR if Track actions exist (live tracking)
        has_track = any(a.get("type") == 18 for a in _actions)
        if tid not in _bake_result and not has_track:
            continue
        tl_list.append((tid, tl))
    if not tl_list:
        log.warning("Show playback: no baked timelines in playlist")
        return

    log.info("Show playback: %d timelines, loop=%s, startIdx=%d", len(tl_list), loop_all, start_idx)
    cumulative = 0
    first_pass = True

    while not _dmx_playback_stop.is_set():
        for idx, (tid, tl) in enumerate(tl_list):
            # Skip items before startIndex on first pass (#361)
            if first_pass and idx < start_idx:
                continue
            if _dmx_playback_stop.is_set():
                break
            duration = tl.get("durationS", 60)
            _show_playback["currentIndex"] = idx
            _show_playback["currentTid"] = tid
            log.info("Show playback: starting timeline %d '%s' (%ds)",
                     tid, tl.get("name", "?"), duration)

            # Reuse single-timeline playback for this segment
            _settings["activeTimeline"] = tid
            _save("settings", _settings)

            # Run the single-timeline DMX loop inline (blocking)
            _dmx_playback_single(tid, time.time(), duration)

            if _dmx_playback_stop.is_set():
                break
            cumulative += duration
            _show_playback["totalElapsed"] = cumulative

        first_pass = False  # subsequent loops start from beginning (#361)
        if not loop_all or _dmx_playback_stop.is_set():
            break
        # Loop: reset and go again
        cumulative = 0

    _show_playback["running"] = False
    _show_playback["currentTid"] = -1
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)
    log.info("Show playback: finished")


def _dmx_playback_single(tid, go_epoch, duration):
    """Play a single timeline's DMX data. Returns when done or stopped."""
    result = _bake_result.get(tid)
    if not result:
        return
    baked_fixtures = result.get("fixtures", {})
    dmx_fixtures = []
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f["id"]
        fix_data = baked_fixtures.get(fid) or baked_fixtures.get(str(fid), {})
        segs = fix_data.get("segments", [])
        uni = f.get("dmxUniverse", 1)
        addr = f.get("dmxStartAddr", 1)
        pid = f.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        ch_map = prof_info.get("channel_map") if prof_info else None
        channels = prof_info.get("channels", []) if prof_info else []
        if not segs:
            continue
        dmx_fixtures.append({"fid": fid, "name": f.get("name", "?"),
                             "uni": uni, "addr": addr, "ch_map": ch_map,
                             "channels": channels, "segs": segs})
    if not dmx_fixtures:
        # No DMX fixtures — just wait for duration to pass
        _dmx_playback_stop.wait(timeout=duration)
        return

    proto = _dmx_settings.get("protocol", "artnet")
    engine = _artnet if proto == "artnet" else _sacn
    if not engine.running:
        engine.start()

    interval = 0.025
    next_frame = time.monotonic()
    frame_count = 0
    while not _dmx_playback_stop.is_set():
        now_mono = time.monotonic()
        if now_mono < next_frame:
            _dmx_playback_stop.wait(timeout=next_frame - now_mono)
            if _dmx_playback_stop.is_set():
                break
            continue
        next_frame += interval
        if next_frame < now_mono:
            next_frame = now_mono + interval
        elapsed = time.time() - go_epoch
        if elapsed < 0:
            continue
        if elapsed > duration:
            break
        for fx in dmx_fixtures:
            # #511 — skip playback for fixtures mid-calibration.
            if _fixture_is_calibrating(fx.get("id")):
                continue
            ch_vals = {}
            for seg in fx["segs"]:
                ss = seg.get("startS", 0)
                sd = seg.get("durationS", 1)
                if ss <= elapsed < ss + sd:
                    p = seg.get("params", {})
                    pri = seg.get("_pri", 0)
                    for k, v in p.items():
                        if v is not None and (k not in ch_vals or pri >= ch_vals[k][1]):
                            ch_vals[k] = (v, pri)
            r = ch_vals.get("r", (0, 0))[0]
            g = ch_vals.get("g", (0, 0))[0]
            b = ch_vals.get("b", (0, 0))[0]
            pan = ch_vals.get("pan", (None, 0))[0]
            tilt = ch_vals.get("tilt", (None, 0))[0]
            dimmer = ch_vals.get("dimmer", (None, 0))[0]
            strobe = ch_vals.get("strobe", (None, 0))[0]
            gobo = ch_vals.get("gobo", (None, 0))[0]
            color_wheel = ch_vals.get("colorWheel", (None, 0))[0]
            prism = ch_vals.get("prism", (None, 0))[0]
            focus = ch_vals.get("focus", (None, 0))[0]
            zoom = ch_vals.get("zoom", (None, 0))[0]
            profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
            uni_buf = engine.get_universe(fx["uni"])
            if fx["ch_map"] and "red" in fx["ch_map"]:
                uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
            elif fx["ch_map"] and "color-wheel" in fx["ch_map"] and (r or g or b):
                from dmx_profiles import rgb_to_wheel_slot
                cw = color_wheel if color_wheel is not None else rgb_to_wheel_slot(fx, r, g, b)
                uni_buf.set_channel(fx["addr"] + fx["ch_map"]["color-wheel"], cw)
            if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
            if pan is not None and tilt is not None and profile:
                uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
            extra_ch = {}
            if strobe is not None: extra_ch["strobe"] = strobe
            if gobo is not None: extra_ch["gobo"] = gobo
            if color_wheel is not None: extra_ch["color-wheel"] = color_wheel
            if prism is not None: extra_ch["prism"] = prism
            if focus is not None: extra_ch["focus"] = focus
            if zoom is not None: extra_ch["zoom"] = zoom
            if extra_ch and profile:
                uni_buf.set_fixture_channels(fx["addr"], extra_ch, profile)
        _evaluate_object_patrols(elapsed)
        if frame_count % 40 == 0:
            _reap_temporal_objects()
        _evaluate_track_actions(elapsed, engine, dmx_fixtures)
        frame_count += 1
    # Blackout on segment end (#364) — zero RGB, dimmer, pan/tilt, and all extras
    for fx in dmx_fixtures:
        profile = {"channel_map": fx["ch_map"], "channels": fx.get("channels", [])} if fx["ch_map"] else None
        uni_buf = engine.get_universe(fx["uni"])
        uni_buf.set_fixture_rgb(fx["addr"], 0, 0, 0, profile)
        if fx["ch_map"] and "dimmer" in fx["ch_map"]:
            uni_buf.set_fixture_dimmer(fx["addr"], 0, profile)
        if profile and fx["ch_map"]:
            # Zero all mapped channels (pan, tilt, strobe, gobo, etc.)
            zero_ch = {}
            for ch_type in ("pan", "tilt", "strobe", "gobo", "color-wheel", "prism", "focus", "zoom", "speed"):
                if ch_type in fx["ch_map"]:
                    zero_ch[ch_type] = 0
            if zero_ch:
                uni_buf.set_fixture_channels(fx["addr"], zero_ch, profile)


@app.post("/api/show/start")
def api_show_start():
    """Start sequential playback of the show playlist."""
    global _show_playback
    data = request.get_json(silent=True) or {}
    order = data.get("order") or _show_playlist.get("order", [])
    loop_all = data.get("loopAll", _show_playlist.get("loopAll", False))
    if not order:
        return jsonify(err="Playlist is empty"), 400
    # Auto-enable loop for track-only playlists (#410)
    if not loop_all:
        track_action_ids = {a["id"] for a in _actions if a.get("type") == 18}
        all_track = True
        for tid in order:
            tl = next((t for t in _timelines if t["id"] == tid), None)
            if not tl:
                continue
            for tr in tl.get("tracks", []):
                for cl in tr.get("clips", []):
                    if cl.get("actionId") not in track_action_ids:
                        all_track = False
                        break
        if all_track and track_action_ids:
            loop_all = True
    # Verify all timelines are baked (Track actions bypass bake requirement)
    has_track_actions = any(a.get("type") == 18 for a in _actions)
    unbaked = [tid for tid in order if tid not in _bake_result]
    if unbaked and not has_track_actions:
        return jsonify(err="Unbaked timelines in playlist", unbaked=unbaked), 400
    # Stop any existing playback
    _dmx_playback_stop.set()
    time.sleep(0.1)
    _dmx_playback_stop.clear()

    go_epoch = int(time.time()) + 2
    # Send RUNNER_GO to all children
    loop_flag = 1 if loop_all else 0
    go_pkt = _hdr(CMD_RUNNER_GO, go_epoch) + struct.pack("<IB", go_epoch, loop_flag)
    started = 0
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], go_pkt)
            started += 1

    start_idx = max(0, min(len(order) - 1, data.get("startIndex", 0)))
    _show_playback = {
        "running": True, "currentIndex": start_idx, "currentTid": order[start_idx],
        "startEpoch": go_epoch, "loopAll": loop_all, "totalElapsed": 0,
    }
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeTimeline"] = order[start_idx]
        _settings["runnerStartEpoch"] = go_epoch
        _save("settings", _settings)

    threading.Thread(target=_show_playback_loop, args=(order, loop_all, go_epoch, start_idx),
                     daemon=True).start()
    return jsonify(ok=True, started=started, goEpoch=go_epoch, timelines=len(order))


@app.post("/api/show/stop")
def api_show_stop():
    """Stop sequential show playback + blackout all output."""
    _dmx_playback_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], pkt_stop)
            _send(child["ip"], pkt_off)
    # Blackout all DMX universes (#405)
    if _artnet.running:
        _artnet.blackout()
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)
    _show_playback["running"] = False
    _show_playback["currentTid"] = -1
    return jsonify(ok=True)


@app.get("/api/show/status")
def api_show_status():
    """Get sequential show playback status."""
    running = _show_playback.get("running", False)
    current_tid = _show_playback.get("currentTid", -1)
    current_tl = next((t for t in _timelines if t["id"] == current_tid), None)
    # Compute elapsed for current timeline
    current_elapsed = 0
    if running and _settings.get("runnerStartEpoch"):
        current_elapsed = max(0, int(time.time()) - _settings["runnerStartEpoch"])
    # Build enriched playlist
    order = _show_playlist.get("order", [])
    items = []
    cumulative_before = 0
    for tid in order:
        tl = next((t for t in _timelines if t["id"] == tid), None)
        if tl:
            d = tl.get("durationS", 0)
            items.append({
                "id": tid, "name": tl.get("name", "?"),
                "durationS": d, "baked": tid in _bake_result,
                "playing": tid == current_tid,
            })
            if tid == current_tid:
                break
            cumulative_before += d
    total_elapsed = cumulative_before + current_elapsed if running else 0
    total_duration = sum(
        t.get("durationS", 0) for t in _timelines
        if t["id"] in order
    )
    return jsonify({
        "running": running,
        "loopAll": _show_playback.get("loopAll", False),
        "currentTimeline": current_tid,
        "currentName": current_tl.get("name", "?") if current_tl else None,
        "currentIndex": _show_playback.get("currentIndex", 0),
        "currentElapsed": current_elapsed,
        "currentDurationS": current_tl.get("durationS", 0) if current_tl else 0,
        "totalElapsed": total_elapsed,
        "totalDurationS": total_duration,
        "items": items,
    })


#  "  "  Settings  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.get("/api/settings")
def api_settings_get():
    s = dict(_settings)
    # Compute elapsed dynamically from start epoch
    if s.get("runnerRunning") and s.get("runnerStartEpoch"):
        s["runnerElapsed"] = max(0, int(time.time()) - s["runnerStartEpoch"])
    return jsonify(s)

@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in ("name", "units", "canvasW", "canvasH", "darkMode", "runnerLoop",
                  "globalBrightness", "logging", "logPath", "autoStartShow"):
            if k in body:
                _settings[k] = body[k]
        _layout["canvasW"] = _settings["canvasW"]
        _layout["canvasH"] = _settings["canvasH"]
        _save("settings", _settings)
        # Sync stage dimensions (meters) from canvas (mm)
        _stage["w"] = _settings["canvasW"] / 1000.0
        _stage["h"] = _settings["canvasH"] / 1000.0
        _save("stage", _stage)
    # Toggle file logging if changed
    if "logging" in body:
        _apply_logging(body["logging"], body.get("logPath"))
    return jsonify(ok=True)

@app.post("/api/logging/start")
def api_logging_start():
    """Start file logging. Optional body: {path: '/path/to/file.log'}."""
    try:
        body = request.get_json(silent=True) or {}
        log_path = body.get("path") if isinstance(body, dict) else None
        _settings["logging"] = True
        _save("settings", _settings)
        _apply_logging(True, log_path)
        return jsonify(ok=True, path=_log_handler.baseFilename if _log_handler else None)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 500

@app.post("/api/logging/stop")
def api_logging_stop():
    """Stop file logging."""
    _settings["logging"] = False
    _save("settings", _settings)
    _apply_logging(False)
    return jsonify(ok=True)

@app.get("/api/logging/status")
def api_logging_status():
    """Return current logging state and file path."""
    return jsonify(
        enabled=bool(_log_handler),
        path=_log_handler.baseFilename if _log_handler else None
    )

#  "  "  Actions library  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/actions")
def api_actions():
    return jsonify(_actions)

_ACTION_FIELDS = ("name", "type", "scope", "canvasEffect", "targetIds", "r", "g", "b",
                  "r2", "g2", "b2",           # Fade second colour
                  "speedMs", "periodMs", "spawnMs",  # timing
                  "minBri", "spacing", "paletteId",  # Breathe/Chase/Rainbow
                  "cooling", "sparking",              # Fire
                  "direction", "tailLen", "density",  # Chase/Comet/Twinkle
                  "decay", "fadeSpeed",               # Comet/Twinkle
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct",  # legacy compat
                  "wledFxOverride", "wledPalOverride", "wledSegId",  # WLED overrides
                  "trackObjectIds", "trackCycleMs", "trackOffset",  # Track action
                  "trackFixtureIds", "trackFixtureOffsets", "trackAutoSpread", "trackFixedAssignment", "trackDimmer",
                  "dimmer", "pan", "tilt", "strobe", "gobo", "colorWheel", "prism",  # DMX channels
                  "ptStartPos", "ptEndPos")  # Pan/Tilt Move: stage coordinate positions [x,y,z] mm

@app.post("/api/actions")
def api_actions_create():
    global _nxt_a
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    with _lock:
        a = {"id": _nxt_a}
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        a.setdefault("name", name)
        a.setdefault("type", 1)
        _actions.append(a)
        _nxt_a += 1
        _save("actions", _actions)
    return jsonify(ok=True, id=a["id"])

@app.get("/api/actions/<int:aid>")
def api_action_get(aid):
    a = next((x for x in _actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(a)

@app.put("/api/actions/<int:aid>")
def api_action_put(aid):
    a = next((x for x in _actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with _lock:
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        _save("actions", _actions)
    return jsonify(ok=True)

@app.delete("/api/actions/<int:aid>")
def api_action_delete(aid):
    global _actions
    with _lock:
        n = len(_actions)
        _actions = [x for x in _actions if x["id"] != aid]
        if len(_actions) == n:
            return jsonify(ok=False, err="not found"), 404
        _save("actions", _actions)
    return jsonify(ok=True)

#  "  "  Config export-import  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

CONFIG_SCHEMA_VERSION = 3  # bump when export format changes incompatibly
CONFIG_MIN_IMPORT_VERSION = 1  # oldest version we can still import

@app.get("/api/config/export")
def api_config_export():
    """Bundle children + fixtures + layout as a portable config file.

    Schema v3: strip internal-only fields (aimPoint, orientation, _placed,
    _beamWidth, status, moverCalibrated, calibrated, _temporal, _ttl).
    """
    # Strip internal/transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = []
    for c in _children:
        cc = {k: v for k, v in c.items() if k not in _CHILD_STRIP}
        clean_children.append(cc)

    # Strip internal/transient fields from fixtures
    _FIX_STRIP = {"aimPoint", "orientation", "_placed", "_beamWidth",
                  "moverCalibrated", "calibrated", "rangeCalibrated",
                  "_temporal", "_ttl", "positioned"}
    clean_fixtures = []
    for f in _fixtures:
        cf = {k: v for k, v in f.items() if k not in _FIX_STRIP}
        clean_fixtures.append(cf)

    return jsonify({
        "type": "slyled-config",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "version": CONFIG_SCHEMA_VERSION,  # backward compat
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": _layout,
    })

@app.post("/api/config/import")
def api_config_import():
    """Merge children by hostname, auto-create fixtures, remap layout IDs."""
    global _nxt_c, _nxt_fix, _layout
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-config":
        return jsonify(ok=False, err="Not a SlyLED config file (missing type field)"), 400
    # Schema version check — accept v1-v3, reject future incompatible versions
    sv = data.get("schemaVersion") or data.get("version") or 1
    if sv > CONFIG_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, but this app only supports up to version {CONFIG_SCHEMA_VERSION}. Please update SlyLED."), 400
    if sv < CONFIG_MIN_IMPORT_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, which is too old. Minimum supported version is {CONFIG_MIN_IMPORT_VERSION}."), 400
    imported_children = data.get("children", [])
    imported_layout = data.get("layout")
    added = updated = fixtures_created = 0
    child_id_map = {}  # old_child_id -> new_child_id
    fixture_id_map = {}  # old_layout_id -> new_fixture_id
    with _lock:
        # Import children
        for c in imported_children:
            old_id = c.get("id", -1)
            ex = next((x for x in _children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                child_id_map[old_id] = ex["id"]
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = _nxt_c
                child_id_map[old_id] = _nxt_c
                _nxt_c += 1
                _children.append(c)
                added += 1
        _save("children", _children)

        # Auto-create fixtures for children that don't already have one
        for c in _children:
            cid = c["id"]
            # Skip if fixture already exists for this child
            if any(f.get("childId") == cid for f in _fixtures):
                continue
            # DMX bridges never get auto-fixtures (they ARE the transport, not a light)
            if c.get("type") == "dmx" or c.get("boardType") in ("giga-dmx", "DMX Bridge"):
                continue
            # Create LED fixture if child has strings with LEDs
            sc = c.get("sc", 0)
            strings = c.get("strings", [])[:sc]
            if not strings or not any(s.get("leds", 0) > 0 for s in strings):
                continue
            f = {
                "id": _nxt_fix,
                "name": c.get("name") or c.get("hostname") or f"Fixture {_nxt_fix}",
                "fixtureType": "led", "type": "linear", "childId": cid,
                "strings": [{"leds": s.get("leds", 0), "mm": s.get("mm", 1000),
                              "sdir": s.get("sdir", 0)} for s in strings if s.get("leds", 0) > 0],
                "rotation": [0, 0, 0], "aoeRadius": 1000,
            }
            _fixtures.append(f)
            # Map: if layout had an entry for this child's old ID, remap to new fixture ID
            for old_cid, new_cid in child_id_map.items():
                if new_cid == cid:
                    fixture_id_map[old_cid] = _nxt_fix
            fixture_id_map[cid] = _nxt_fix
            _nxt_fix += 1
            fixtures_created += 1
        _save("fixtures", _fixtures)

        # Remap layout position IDs
        if imported_layout:
            _layout = imported_layout
            for lc in _layout.get("children", []):
                old_id = lc.get("id")
                # Try fixture map first (old fixture/child ID → new fixture ID)
                new_id = fixture_id_map.get(old_id)
                if new_id is None:
                    # Try child map
                    new_cid = child_id_map.get(old_id)
                    if new_cid is not None:
                        new_id = fixture_id_map.get(new_cid, new_cid)
                if new_id is not None:
                    lc["id"] = new_id
            _save("layout", _layout)

        # Import explicit fixtures from config (v2+ includes fixtures array)
        imported_fixtures = data.get("fixtures", [])
        for f in imported_fixtures:
            old_fid = f.get("id", -1)
            # Skip if we already auto-created a fixture for this child
            cid = f.get("childId")
            if cid is not None:
                new_cid = child_id_map.get(cid, cid)
                if any(ef.get("childId") == new_cid for ef in _fixtures):
                    # Already exists — update fixture_id_map for layout remapping
                    existing = next(ef for ef in _fixtures if ef.get("childId") == new_cid)
                    fixture_id_map[old_fid] = existing["id"]
                    continue
            # Create the fixture with a new ID
            f = dict(f)
            new_fid = _nxt_fix
            fixture_id_map[old_fid] = new_fid
            f["id"] = new_fid
            if cid is not None:
                f["childId"] = child_id_map.get(cid, cid)
            _fixtures.append(f)
            _nxt_fix += 1
            fixtures_created += 1
        _save("fixtures", _fixtures)

        # Re-remap layout IDs with the complete fixture_id_map
        if imported_layout:
            for lc in _layout.get("children", []):
                old_id = lc.get("id")
                new_id = fixture_id_map.get(old_id)
                if new_id is not None:
                    lc["id"] = new_id
            _save("layout", _layout)

    log.info("CONFIG IMPORT: %d children added, %d updated, %d fixtures created, child_map=%s, fix_map=%s",
             added, updated, fixtures_created, child_id_map, fixture_id_map)
    return jsonify(ok=True, added=added, updated=updated, fixturesCreated=fixtures_created)

@app.post("/api/show/preset")
def api_show_preset():
    """Install a preset show by theme ID from request body."""
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")
    return _install_preset_show(preset_id)

def _install_preset_show(preset_id):
    """Install a preset show as a timeline with spatial effects and actions.

    Dynamically generates a show based on the selected theme and the user's
    actual fixtures, positions, and capabilities. Every fixture gets coverage
    so there are no dark periods.
    """
    global _nxt_a, _nxt_sfx, _nxt_tl, _nxt_obj

    from show_generator import generate_show, THEMES
    if preset_id not in THEMES:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    # Prerequisite check for live-tracking presets (#382)
    warnings = []
    theme = THEMES.get(preset_id, {})
    if theme.get("live_track"):
        needs_camera = not theme.get("patrol_objects")  # patrol shows don't need cameras
        has_camera = any(f.get("fixtureType") == "camera" for f in _fixtures)
        has_mover = any(
            f.get("fixtureType") == "dmx" and _profile_lib and
            (_profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("panRange", 0) > 0
            for f in _fixtures
        )
        warnings = []
        if needs_camera and not has_camera:
            warnings.append("No camera node registered — person detection will not work")
        if not has_mover:
            warnings.append("No moving head fixtures found — tracking requires DMX movers with pan/tilt")
        # Allow loading but include warnings in response
        if warnings:
            log.warning("Preset %s prerequisites: %s", preset_id, "; ".join(warnings))

    show = generate_show(preset_id, _fixtures, _layout, _stage, _profile_lib)
    if not show:
        return jsonify(ok=False, err="Failed to generate show"), 500

    with _lock:
        dur = show["durationS"]

        # Create patrol objects first so their IDs can be linked to Track actions
        patrol_obj_ids = []
        obj_count = 0
        for po in show.get("patrol_objects", []):
            obj = {
                "id": _nxt_obj, "name": po.get("name", f"Patrol {_nxt_obj}"),
                "objectType": po.get("objectType", "custom"),
                "mobility": "moving",
                "color": po.get("color", "#00DCFF"),
                "opacity": po.get("opacity", 40),
                "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0],
                               "scale": po.get("scale", [500, 500, 500])},
                "patrol": po.get("patrol", {}),
            }
            _objects.append(obj)
            patrol_obj_ids.append(_nxt_obj)
            _nxt_obj += 1
            obj_count += 1
        if obj_count:
            _save("objects", _objects)

        # Create action records and build id lookup (#531 — dedupe by
        # (presetId, name, type) so re-loading a preset doesn't clone the
        # same action into the library. Preset-generated actions are
        # tagged with ``presetSource`` so they're distinguishable from
        # user-created entries and only previously-generated presets are
        # considered match candidates — an operator's manually-created
        # action with the same name is never overwritten).
        action_ref_map = {}
        action_count = 0
        existing_preset_by_key = {}
        for a in _actions:
            src = a.get("presetSource")
            if not src:
                continue
            key = (src, a.get("name"), a.get("type"))
            existing_preset_by_key[key] = a

        for act_info in show.get("base_actions", []) + show.get("mover_actions", []):
            act_data = act_info.get("action", act_info) if isinstance(act_info, dict) and "action" in act_info else act_info
            key = (preset_id, act_data.get("name"), act_data.get("type"))
            existing = existing_preset_by_key.get(key)
            if existing is not None:
                # Update in place — a preset redefinition is allowed to
                # bump parameters without duplicating the record.
                existing.update(act_data)
                existing["presetSource"] = preset_id
                if existing.get("type") == 18 and patrol_obj_ids:
                    existing["trackObjectIds"] = patrol_obj_ids
                action_ref_map[id(act_info)] = existing["id"]
                continue
            act = {"id": _nxt_a, **act_data, "presetSource": preset_id}
            if act.get("type") == 18 and patrol_obj_ids:
                act["trackObjectIds"] = patrol_obj_ids
            _actions.append(act)
            existing_preset_by_key[key] = act
            action_ref_map[id(act_info)] = _nxt_a
            action_count += 1
            _nxt_a += 1
        _save("actions", _actions)

        # Create spatial effect records
        effect_ref_map = {}  # maps python id() of effect dict -> assigned effect id
        for fx in show.get("effects", []):
            fx_rec = {"id": _nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            _spatial_fx.append(fx_rec)
            effect_ref_map[id(fx)] = _nxt_sfx
            _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)

        # Build timeline tracks from generator's track structure
        # Tracks are ordered: lower index = lower priority (background)
        tracks = []
        for gen_track in show.get("tracks", []):
            track = {}
            if gen_track.get("allPerformers"):
                track["allPerformers"] = True
            elif gen_track.get("fixtureId"):
                track["fixtureId"] = gen_track["fixtureId"]
            else:
                continue

            clips = []
            for gen_clip in gen_track.get("clips", []):
                clip = {
                    "startS": gen_clip.get("startS", 0),
                    "durationS": gen_clip.get("durationS", dur),
                }
                # Resolve action or effect reference
                aref = gen_clip.get("_action_ref")
                eref = gen_clip.get("_effect_ref")
                if aref and id(aref) in action_ref_map:
                    clip["actionId"] = action_ref_map[id(aref)]
                    act_data = aref.get("action", aref) if isinstance(aref, dict) and "action" in aref else aref
                    clip["name"] = act_data.get("name", "Action")
                elif eref and id(eref) in effect_ref_map:
                    clip["effectId"] = effect_ref_map[id(eref)]
                    clip["name"] = eref.get("name", "Effect")
                else:
                    continue  # skip clips with no resolved reference
                clips.append(clip)

            if clips:
                track["clips"] = clips
                tracks.append(track)

        tl = {
            "id": _nxt_tl, "name": show["name"],
            "durationS": dur,
            "tracks": tracks,
            "loop": True,
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)

    resp = {"ok": True, "name": show["name"], "timelineId": tl["id"],
            "actions": action_count, "effects": len(effect_ref_map),
            "objects": obj_count}
    if theme.get("live_track") and warnings:
        resp["warnings"] = warnings
    return jsonify(resp)


def _api_show_preset_old():
    """LEGACY: hardcoded preset shows — kept as fallback reference."""
    global _nxt_a, _nxt_sfx, _nxt_tl
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")

    PRESETS = {
        "rainbow-up": {
            "name": "Rainbow Up",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 60,
                         "paletteId": 0, "direction": 1}],
        },
        "rainbow-across": {
            "name": "Rainbow Across",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 50,
                         "paletteId": 0, "direction": 0}],
        },
        "slow-fire": {
            "name": "Slow Fire",
            "durationS": 60,
            "actions": [{"name": "Fire Effect", "type": 6, "r": 255, "g": 80, "b": 0,
                         "speedMs": 40, "cooling": 45, "sparking": 100}],
        },
        "disco": {
            "name": "Disco",
            "durationS": 60,
            "actions": [{"name": "Disco Twinkle", "type": 8, "r": 200, "g": 100, "b": 255,
                         "spawnMs": 80, "density": 5, "fadeSpeed": 15}],
        },
        "ocean-wave": {
            "name": "Ocean Wave",
            "durationS": 40,
            "effects": [{"name": "Blue Wave", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 80, "b": 220, "size": {"normal": [1,0,0], "thickness": 800},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 10, "easing": "ease-in-out"},
                         "blend": "add"},
                        {"name": "Teal Wash", "category": "spatial-field", "shape": "sphere",
                         "r": 0, "g": 180, "b": 160, "size": {"radius": 2500},
                         "motion": {"startPos": [8000,1000,0], "endPos": [0,3000,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "screen"}],
        },
        "sunset": {
            "name": "Sunset Glow",
            "durationS": 45,
            "actions": [{"name": "Warm Breathe", "type": 3, "r": 255, "g": 100, "b": 20,
                         "periodMs": 4000, "minBri": 30}],
            "effects": [{"name": "Golden Sweep", "category": "spatial-field", "shape": "plane",
                         "r": 255, "g": 160, "b": 30, "size": {"normal": [0,1,0], "thickness": 1000},
                         "motion": {"startPos": [5000,5000,0], "endPos": [5000,0,0], "durationS": 20, "easing": "ease-out"},
                         "blend": "screen"}],
        },
        "police": {
            "name": "Police Lights",
            "durationS": 30,
            "actions": [{"name": "Red Strobe", "type": 9, "r": 255, "g": 0, "b": 0,
                         "periodMs": 200, "p8a": 50}],
            "effects": [{"name": "Blue Flash Sweep", "category": "spatial-field", "shape": "box",
                         "r": 0, "g": 0, "b": 255, "size": {"width": 2000, "height": 5000, "depth": 3000},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 2, "easing": "linear"},
                         "blend": "add"}],
        },
        "starfield": {
            "name": "Starfield",
            "durationS": 60,
            "actions": [{"name": "Star Sparkle", "type": 12, "r": 5, "g": 5, "b": 20,
                         "spawnMs": 60, "density": 4}],
        },
        "aurora": {
            "name": "Aurora Borealis",
            "durationS": 40,
            "effects": [{"name": "Green Curtain", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 255, "b": 80, "size": {"normal": [1,0.3,0], "thickness": 1500},
                         "motion": {"startPos": [0,2000,0], "endPos": [10000,3000,0], "durationS": 15, "easing": "ease-in-out"},
                         "blend": "screen"},
                        {"name": "Purple Shimmer", "category": "spatial-field", "shape": "sphere",
                         "r": 120, "g": 0, "b": 200, "size": {"radius": 2000},
                         "motion": {"startPos": [8000,3000,0], "endPos": [1000,1500,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "add"}],
        },
        # ── Moving-head-aware presets ──────────────────────────────────
        # These use spatial effects with motion paths. LED fixtures get
        # color washes; DMX moving heads also track the effect center
        # with pan/tilt, creating beam sweeps across the stage.
        "spotlight-sweep": {
            "name": "Spotlight Sweep",
            "durationS": 20,
            "effects": [
                {"name": "Sweep Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 240, "b": 200, "size": {"radius": 3000},
                 "motion": {"startPos": [0, 2500, 2500], "endPos": [10000, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Return Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 180, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [10000, 2500, 2500], "endPos": [0, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "concert-wash": {
            "name": "Concert Wash",
            "durationS": 30,
            "actions": [{"name": "Slow Breathe Blue", "type": 3, "r": 0, "g": 40, "b": 200,
                         "periodMs": 5000, "minBri": 20}],
            "effects": [
                {"name": "Magenta Flood", "category": "spatial-field", "shape": "plane",
                 "r": 220, "g": 0, "b": 180, "size": {"normal": [1, 0, 0], "thickness": 2000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 12, "easing": "ease-in-out"},
                 "blend": "screen"},
                {"name": "Amber Spot", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 160, "b": 40, "size": {"radius": 3000},
                 "motion": {"startPos": [8000, 2500, 3000], "endPos": [2000, 2500, 7000],
                            "durationS": 15, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "figure-eight": {
            "name": "Figure Eight",
            "durationS": 24,
            "effects": [
                # Two spheres crossing at center stage — moving heads track each
                {"name": "Cyan Path A", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Cyan Path B", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return A", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 8000], "endPos": [1000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return B", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 8000], "endPos": [9000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "thunderstorm": {
            "name": "Thunderstorm",
            "durationS": 30,
            "actions": [{"name": "Deep Blue Base", "type": 1, "r": 5, "g": 5, "b": 30}],
            "effects": [
                # Lightning bolts — fast-moving spheres that moving heads chase
                {"name": "Lightning Strike 1", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 255, "b": 240, "size": {"radius": 3000},
                 "motion": {"startPos": [3000, 5000, 5000], "endPos": [3000, 0, 5000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Lightning Strike 2", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 200, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [7000, 5000, 3000], "endPos": [7000, 0, 3000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Rolling Thunder", "category": "spatial-field", "shape": "plane",
                 "r": 30, "g": 20, "b": 80, "size": {"normal": [1, 0, 0], "thickness": 3000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 8, "easing": "linear"},
                 "blend": "screen"},
            ],
        },
        "dance-floor": {
            "name": "Dance Floor",
            "durationS": 20,
            "actions": [{"name": "Chase Pulse", "type": 4, "r": 255, "g": 0, "b": 128,
                         "speedMs": 30, "spacing": 6, "tailLen": 3, "direction": 0}],
            "effects": [
                # Fast orbiting spots — moving heads rapidly track
                {"name": "Red Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 0, "b": 50, "size": {"radius": 2500},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Blue Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 50, "g": 0, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Green Flash", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 255, "b": 80, "size": {"radius": 3000},
                 "motion": {"startPos": [5000, 5000, 5000], "endPos": [5000, 1000, 5000],
                            "durationS": 2, "easing": "ease-in"},
                 "blend": "add"},
            ],
        },
    }

    preset = PRESETS.get(preset_id)
    if not preset:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    with _lock:
        # Create actions from preset
        action_ids = []
        for a in preset.get("actions", []):
            act = {"id": _nxt_a, **a}
            _actions.append(act)
            action_ids.append(_nxt_a)
            _nxt_a += 1
        _save("actions", _actions)

        # Create spatial effects from preset
        effect_ids = []
        for fx in preset.get("effects", []):
            fx_rec = {"id": _nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            _spatial_fx.append(fx_rec)
            effect_ids.append(_nxt_sfx)
            _nxt_sfx += 1
        _save("spatial_fx", _spatial_fx)

        # Build timeline with one "all performers" track
        clips = []
        t = 0
        for aid in action_ids:
            dur = preset.get("durationS", 30)
            clips.append({"actionId": aid, "startS": 0, "durationS": dur})
        for eid in effect_ids:
            dur = preset.get("durationS", 30)
            clips.append({"effectId": eid, "startS": 0, "durationS": dur})

        tl = {
            "id": _nxt_tl, "name": preset["name"],
            "durationS": preset.get("durationS", 30),
            "tracks": [{"allPerformers": True, "clips": clips}],
            "loop": True,
        }
        _timelines.append(tl)
        _nxt_tl += 1
        _save("timelines", _timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in _show_playlist.get("order", []):
            _show_playlist.setdefault("order", []).append(tl["id"])
            _save("show_playlist", _show_playlist)

    return jsonify(ok=True, name=preset["name"], timelineId=tl["id"],
                   actions=len(action_ids), effects=len(effect_ids))

@app.get("/api/show/presets")
def api_show_presets():
    """List available preset shows."""
    from show_generator import list_themes
    presets = list_themes()
    return jsonify(presets)

@app.post("/api/show/demo")
def api_show_demo():
    """Generate a demo show using a random preset theme and existing fixtures."""
    from show_generator import THEMES
    import random
    theme_id = random.choice(list(THEMES.keys()))
    return _install_preset_show(theme_id)

@app.get("/api/show/export")
def api_show_export():
    """Bundle actions + spatial effects + timelines as a portable show file."""
    return jsonify({"type": "slyled-show", "version": 1,
                    "actions": _actions, "spatialEffects": _spatial_fx,
                    "timelines": _timelines})

@app.post("/api/show/import")
def api_show_import():
    """Replace all actions, spatial effects, and timelines from a show file."""
    global _actions, _spatial_fx, _timelines, _nxt_a, _nxt_sfx, _nxt_tl
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-show":
        return jsonify(ok=False, err="not a slyled-show file"), 400
    with _lock:
        _actions = data.get("actions", [])
        _spatial_fx = data.get("spatialEffects", [])
        _timelines = data.get("timelines", [])
        _nxt_a = max((a["id"] for a in _actions), default=-1) + 1
        _nxt_sfx = max((f["id"] for f in _spatial_fx), default=-1) + 1
        _nxt_tl = max((t["id"] for t in _timelines), default=-1) + 1
        _save("actions", _actions)
        _save("spatial_fx", _spatial_fx)
        _save("timelines", _timelines)
    return jsonify(ok=True, actions=len(_actions), spatialEffects=len(_spatial_fx),
                   timelines=len(_timelines),
                   runners=0, flights=0, shows=0)

#  "  "  Project file (complete save/load)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

PROJECT_SCHEMA_VERSION = 2   # bumped from 1 → 2 for spatial data (#336)


def _compress_cloud(cloud):
    """Gzip-compress point cloud data for .slyshow portability (#336)."""
    import gzip, base64, io
    raw = json.dumps(cloud.get("points", [])).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as f:
        f.write(raw)
    result = {k: v for k, v in cloud.items() if k != "points"}
    result["points"] = {"compressed": True,
                        "data": base64.b64encode(buf.getvalue()).decode("ascii")}
    return result


def _decompress_cloud(cloud):
    """Decompress gzip-compressed point cloud from import (#336)."""
    import gzip, base64, io
    pts = cloud.get("points")
    if isinstance(pts, dict) and pts.get("compressed"):
        raw = gzip.decompress(base64.b64decode(pts["data"]))
        cloud["points"] = json.loads(raw)
    return cloud


@app.get("/api/project/export")
def api_project_export():
    """Bundle ALL state into a complete project file (.slyshow)."""
    # Strip transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = [{k: v for k, v in c.items() if k not in _CHILD_STRIP}
                      for c in _children]
    # Strip transient fields from fixtures
    _FIX_STRIP = {"_placed", "_beamWidth", "_temporal", "_ttl", "positioned"}
    clean_fixtures = [{k: v for k, v in f.items() if k not in _FIX_STRIP}
                      for f in _fixtures]
    # Camera SSH: export per-node config with passwords stripped (not portable)
    clean_camera_ssh = {}
    for ip, ssh in _camera_ssh.items():
        clean = dict(ssh)
        clean.pop("password", None)  # encrypted passwords are machine-specific
        clean_camera_ssh[ip] = clean
    # Settings minus transient runtime state
    clean_settings = {k: v for k, v in _settings.items()
                      if k not in ("runnerRunning", "runnerElapsed")}
    # Point cloud: compress if large (#336)
    cloud_export = None
    if _point_cloud and _point_cloud.get("points"):
        cloud_export = _compress_cloud(_point_cloud)
    # Light maps from mover calibrations (#336)
    light_maps = {}
    for fid, cal in _mover_cal.items():
        lm = cal.get("lightMap")
        if lm:
            light_maps[fid] = lm
    # Collect custom DMX profiles referenced by fixtures (#337)
    profile_ids = set()
    for f in _fixtures:
        pid = f.get("dmxProfileId")
        if pid:
            profile_ids.add(pid)
    export_profiles = []
    for pid in profile_ids:
        p = _profile_lib.get_profile(pid)
        if p and not p.get("builtin"):
            export_profiles.append(p)
    return jsonify({
        "type": "slyled-project",
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "appVersion": VERSION,
        "savedAt": datetime.utcnow().isoformat() + "Z",
        "name": _settings.get("name", "SlyLED"),
        "stage": _stage,
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": _layout,
        "actions": _actions,
        "spatialEffects": _spatial_fx,
        "timelines": _timelines,
        "objects": _objects,
        "dmxSettings": {k: v for k, v in _dmx_settings.items()},
        "calibrations": _calibrations,
        "rangeCalibrations": _range_cal,
        "moverCalibrations": _mover_cal,
        "cameraSsh": clean_camera_ssh,
        "showPlaylist": _show_playlist,
        "profiles": export_profiles,
        "settings": clean_settings,
        # Spatial data (#336)
        "pointCloud": cloud_export,
        "lightMaps": light_maps if light_maps else None,
        # ArUco marker registry (#596) — surveyed ground-truth tags
        "arucoMarkers": list(_aruco_markers),
    })


@app.post("/api/project/import")
def api_project_import():
    """Load a complete project file, replacing ALL state."""
    global _children, _fixtures, _layout, _stage, _settings
    global _actions, _spatial_fx, _timelines, _objects
    global _dmx_settings, _calibrations, _range_cal, _mover_cal
    global _nxt_c, _nxt_a, _nxt_fix, _nxt_obj, _nxt_sfx, _nxt_tl
    global _aruco_markers  # #596 — registry round-trips through project files
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-project":
        return jsonify(ok=False, err="Not a SlyLED project file"), 400
    sv = data.get("schemaVersion", 1)
    if sv > PROJECT_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Project file is version {sv}, but this app only supports version {PROJECT_SCHEMA_VERSION}. Please update SlyLED."), 400
    # Stop active playback
    _dmx_playback_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c.get("ip"):
            _send(c["ip"], pkt_stop)
            _send(c["ip"], pkt_off)
    _live_events.clear()
    _bake_result.clear()
    with _lock:
        _children = data.get("children", [])
        for c in _children:
            c["status"] = 0  # all offline until next ping
        _fixtures = data.get("fixtures", [])
        _layout = data.get("layout", {"canvasW": 3000, "canvasH": 2000, "children": []})
        _stage = data.get("stage", {"w": 10.0, "h": 5.0, "d": 10.0})
        _actions = data.get("actions", [])
        _spatial_fx = data.get("spatialEffects", [])
        _timelines = data.get("timelines", [])
        _objects = data.get("objects", [])
        _dmx_settings = data.get("dmxSettings", dict(_DMX_SETTINGS_DEFAULTS))
        # Reconfigure and restart engine with imported settings (#350)
        if _artnet.running:
            _artnet.stop()
        if _sacn.running:
            _sacn.stop()
        _apply_dmx_settings()
        _proto = _dmx_settings.get("protocol", "artnet")
        _eng = _artnet if _proto == "artnet" else _sacn if _proto == "sacn" else None
        if _eng and _dmx_settings.get("universeRoutes"):
            _eng._bind_ip = "0.0.0.0"  # always use wildcard — saved IP may be stale (#345)
            try:
                _eng.start()
            except Exception:
                pass
            if _eng.running:
                _apply_profile_defaults(_eng)
        _calibrations.clear()
        _calibrations.update(data.get("calibrations", {}))
        _range_cal.clear()
        _range_cal.update(data.get("rangeCalibrations", {}))
        _mover_cal.clear()
        _mover_cal.update(data.get("moverCalibrations", {}))
        # Rebuild grids from samples if missing (e.g. saved before grid fix)
        for _fid_str, _cal in _mover_cal.items():
            if _cal.get("method") == "manual" and _cal.get("samples") and not _cal.get("grid"):
                _gs = [(_s["pan"], _s["tilt"], _s["stageX"], _s["stageY"]) for _s in _cal["samples"]]
                if len(_gs) >= 2:
                    try:
                        _cal["grid"] = _mcal.build_grid(_gs)
                    except Exception:
                        pass
        # Restore show playlist — prune any orphan IDs that reference deleted timelines
        _show_playlist.clear()
        _show_playlist.update(data.get("showPlaylist", {"order": [], "loopAll": False}))
        valid_tl_ids = {t["id"] for t in _timelines}
        _show_playlist["order"] = [tid for tid in _show_playlist.get("order", []) if tid in valid_tl_ids]
        # Auto-populate playlist if empty but timelines exist (fixes #312)
        if not _show_playlist.get("order") and _timelines:
            _show_playlist["order"] = [t["id"] for t in _timelines]
        # Restore per-node camera SSH (passwords stripped — user must re-enter)
        imported_cam_ssh = data.get("cameraSsh", {})
        if imported_cam_ssh:
            _camera_ssh.update(imported_cam_ssh)
            _save("camera_ssh", _camera_ssh)
        # Merge imported settings (preserve runtime-only fields)
        imp_settings = data.get("settings", {})
        for k, v in imp_settings.items():
            _settings[k] = v
        _settings["runnerRunning"] = False
        _settings["runnerElapsed"] = 0
        # Recompute sequence counters
        _nxt_c = max((c["id"] for c in _children), default=-1) + 1
        _nxt_fix = max((f["id"] for f in _fixtures), default=-1) + 1
        _nxt_a = max((a["id"] for a in _actions), default=-1) + 1
        _nxt_obj = max((o["id"] for o in _objects), default=-1) + 1
        _nxt_sfx = max((f["id"] for f in _spatial_fx), default=-1) + 1
        _nxt_tl = max((t["id"] for t in _timelines), default=-1) + 1
        # Restore spatial data (#336)
        global _point_cloud
        cloud = data.get("pointCloud")
        if cloud:
            _point_cloud = _decompress_cloud(cloud)
            _save("pointcloud", _point_cloud)
        # Restore light maps into mover calibrations (#336)
        light_maps = data.get("lightMaps")
        if light_maps:
            for fid_str, lm in light_maps.items():
                if fid_str in _mover_cal:
                    _mover_cal[fid_str]["lightMap"] = lm
        # #596 — restore ArUco marker registry from the project file.
        # Silently skip records that fail schema validation rather than
        # aborting the whole import.
        _aruco_markers.clear()
        for rec in data.get("arucoMarkers", []) or []:
            try:
                _aruco_markers.append(_aruco_marker_normalise(rec))
            except (ValueError, TypeError):
                continue
        _aruco_markers.sort(key=lambda m: m["id"])
        _save("aruco_markers", _aruco_markers)
        # Import custom DMX profiles referenced by fixtures (#337).
        # Embedded profiles may or may not exist in the community — we
        # try to stamp `_community` provenance on any that do so the
        # SPA can detect staleness later (#534). Collect the slugs we
        # ended up with so we can batch check_updates after the import.
        _imported_community_slugs = []
        for p in data.get("profiles", []):
            pid = p.get("id")
            if not pid:
                continue
            if not _profile_lib.get_profile(pid):
                _profile_lib.save_profile(p)
            if p.get("_community") and p["_community"].get("slug"):
                _imported_community_slugs.append(p["_community"]["slug"])
        # Fetch missing profiles from community server (#351) — and
        # stamp them with _community provenance while we're at it.
        _missing_pids = set()
        for f in _fixtures:
            pid = f.get("dmxProfileId")
            if pid and not _profile_lib.get_profile(pid):
                _missing_pids.add(pid)
        if _missing_pids:
            try:
                import community_client as cc
                for pid in _missing_pids:
                    result = cc.get_profile(pid)
                    if result and result.get("ok"):
                        prof = result.get("data", result)
                        if isinstance(prof, dict) and "id" in prof:
                            _stamp_community_provenance(prof, pid)
                            _profile_lib.import_profiles([prof])
                            _imported_community_slugs.append(pid)
                            log.info("Project import: fetched missing profile '%s' from community", pid)
                        else:
                            log.warning("Project import: community returned invalid data for '%s'", pid)
                    else:
                        log.warning("Project import: could not fetch profile '%s' from community", pid)
            except Exception as e:
                log.warning("Project import: community profile fetch failed: %s", e)
        # Persist everything
        _save("children", _children)
        _save("fixtures", _fixtures)
        _save("layout", _layout)
        _save("stage", _stage)
        _save("actions", _actions)
        _save("spatial_fx", _spatial_fx)
        _save("timelines", _timelines)
        _save("objects", _objects)
        _save("dmx_settings", _dmx_settings)
        _save("calibrations", _calibrations)
        _save("range_calibrations", _range_cal)
        _save("mover_calibrations", _mover_cal)
        _save("show_playlist", _show_playlist)
        _save("settings", _settings)
    _apply_dmx_settings()
    name = data.get("name", "Untitled")
    # Report camera nodes that need SSH credentials re-entered
    ssh_needed = []
    for ip, ssh in _camera_ssh.items():
        if ssh.get("authType") == "password" and not ssh.get("password"):
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "password"})
        elif ssh.get("authType") == "key" and ssh.get("keyPath") and not Path(os.path.expanduser(ssh["keyPath"])).exists():
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "key", "keyPath": ssh["keyPath"]})
    # #534 — post-import community update check. Batch-check every
    # profile we just stamped with _community provenance; surface the
    # stale count so the SPA can toast "3 embedded profiles have
    # community updates available". Failures are non-fatal — if the
    # community server is unreachable we just report 0.
    stale_profiles = 0
    stale_detail = []
    if _imported_community_slugs:
        try:
            import community_client as cc
            pairs = []
            for slug in set(_imported_community_slugs):
                p = _profile_lib.get_profile(slug) or {}
                ts = (p.get("_community") or {}).get("uploadTs", "")
                pairs.append({"slug": slug, "knownTs": ts})
            result = cc.check_updates(pairs) or {}
            if result.get("ok"):
                stale_detail = (result.get("data") or {}).get("updates") or []
                stale_profiles = len(stale_detail)
        except Exception as e:
            log.warning("Project import: community check_updates failed: %s", e)
    return jsonify(ok=True, name=name,
                   children=len(_children), fixtures=len(_fixtures),
                   actions=len(_actions), timelines=len(_timelines),
                   objects=len(_objects), sshNeeded=ssh_needed,
                   communityStaleProfiles=stale_profiles,
                   communityStaleDetail=stale_detail)


@app.get("/api/project/name")
def api_project_name_get():
    return jsonify(name=_settings.get("name", "SlyLED"))


@app.post("/api/project/name")
def api_project_name_set():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    _settings["name"] = name
    _save("settings", _settings)
    return jsonify(ok=True, name=name)


#  "  "  Factory reset  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

_DEFAULT_SETTINGS = {
    "name": "SlyLED", "units": 0, "canvasW": 3000, "canvasH": 2000,
    "darkMode": 1, "runnerRunning": False,
    "runnerElapsed": 0, "runnerLoop": True, "logging": False,
}
_DEFAULT_LAYOUT = {"canvasW": 3000, "canvasH": 2000, "children": []}
_DEFAULT_STAGE  = {"w": 10.0, "h": 5.0, "d": 10.0}
_DEFAULT_FIXTURES  = []
_DEFAULT_OBJECTS   = []
_DEFAULT_SPATIAL_FX = []
_DEFAULT_TIMELINES = []

#  "  "  WiFi credentials  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

import base64, hashlib
from cryptography.fernet import Fernet, InvalidToken

def _wifi_key():
    """Derive a Fernet key from machine identity using PBKDF2."""
    seed = (socket.gethostname() + "-slyled-wifi").encode()
    dk = hashlib.pbkdf2_hmac("sha256", seed, b"slyled-salt-v2", 100_000, dklen=32)
    return base64.urlsafe_b64encode(dk)

def _encrypt_pw(plain):
    if not plain:
        return ""
    f = Fernet(_wifi_key())
    return f.encrypt(plain.encode("utf-8")).decode("ascii")

def _decrypt_pw(enc):
    if not enc:
        return ""
    try:
        f = Fernet(_wifi_key())
        return f.decrypt(enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        # Fallback: try legacy XOR decryption for migration
        try:
            legacy_seed = (socket.gethostname() + "-slyled-wifi").encode()
            legacy_key = hashlib.sha256(legacy_seed).digest()
            raw = base64.b64decode(enc)
            plain = bytes(b ^ legacy_key[i % len(legacy_key)] for i, b in enumerate(raw)).decode("utf-8")
            # Re-encrypt with Fernet for auto-migration
            return plain
        except Exception:
            return enc   # last resort: return as-is (old unencrypted data)

@app.get("/api/wifi")
def api_wifi_get():
    return jsonify({"ssid": _wifi.get("ssid", ""),
                    "hasPassword": bool(_wifi.get("password"))})

@app.post("/api/wifi")
def api_wifi_save():
    body = request.get_json(silent=True) or {}
    with _lock:
        if "ssid" in body:
            _wifi["ssid"] = body["ssid"]
        if "password" in body:
            _wifi["password"] = _encrypt_pw(body["password"])
        _save("wifi", _wifi)
    return jsonify(ok=True)

def get_wifi_password():
    """Get decrypted WiFi password (for firmware flashing)."""
    return _decrypt_pw(_wifi.get("password", ""))

#  "  "  Firmware management  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

try:
    from firmware_manager import list_ports, load_registry, flash_board, get_flash_status, detect_chip, query_serial
    _fw_available = True
except ImportError:
    _fw_available = False

# Firmware directory: check PyInstaller bundle first, then project root, then alongside exe
if getattr(sys, "frozen", False):
    _FW_DIR = Path(sys._MEIPASS) / "firmware"
    if not _FW_DIR.exists():
        _FW_DIR = Path(sys.executable).parent / "firmware"
else:
    _FW_DIR = BASE.parent.parent / "firmware"   # project root: ../../firmware from desktop/shared/
    if not _FW_DIR.exists():
        _FW_DIR = BASE / "firmware"

# Writable cache for firmware binaries downloaded on demand (#568). The
# installer no longer bundles the .bin files — only registry.json ships —
# so the first flash of a given board will fetch the binary from the
# matching GitHub release asset and park it here for later reuse.
if getattr(sys, "frozen", False) and os.name == "nt" and os.environ.get("APPDATA"):
    _FW_CACHE_DIR = Path(os.environ["APPDATA"]) / "SlyLED" / "firmware"
elif getattr(sys, "frozen", False):
    _FW_CACHE_DIR = Path.home() / ".slyled" / "firmware"
else:
    # Dev / source checkout: re-use the project firmware tree so locally
    # built binaries are picked up without a download round-trip.
    _FW_CACHE_DIR = _FW_DIR
_FW_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _parent_wifi_hash():
    """Compute the same djb2 hash as the firmware for SSID+password comparison."""
    ssid = _wifi.get("ssid", "")
    pw = _decrypt_pw(_wifi.get("password", ""))
    h = 5381
    for c in ssid:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    for c in pw:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    return format(h, 'X')

@app.get("/api/firmware/ports")
def api_fw_ports():
    """Fast port list - no serial queries. Use /api/firmware/query for per-port info."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not installed"), 500
    return jsonify(list_ports())

@app.post("/api/firmware/query")
def api_fw_query_port():
    """Query a single port via serial for version + wifi hash. Slow (~2s)."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    info = query_serial(port, timeout=2.0)
    if not info:
        return jsonify(ok=True, fwVersion=None, fwBoard=None, wifiMatch=None)
    parent_hash = _parent_wifi_hash()
    bmap = {"esp32": "esp32", "d1mini": "d1mini", "giga-child": "giga", "giga-parent": "giga"}
    return jsonify(ok=True,
                   fwVersion=info.get("version"),
                   fwBoard=info.get("board"),
                   board=bmap.get(info.get("board", ""), None),
                   wifiHash=info.get("wifiHash"),
                   wifiMatch=(info.get("wifiHash") == parent_hash) if info.get("wifiHash") else None)

@app.get("/api/firmware/registry")
def api_fw_registry():
    return jsonify(load_registry(_FW_DIR))


@app.get("/api/firmware/library")
def api_fw_library():
    """#567 — return every registry entry annotated with its local
    availability so the Firmware Library section can render Download
    buttons for the missing ones."""
    reg = load_registry(_FW_DIR)
    entries = []
    for e in reg.get("firmware", []):
        fname = e.get("file") or ""
        cache_path = _FW_CACHE_DIR / fname if fname else None
        bundle_path = _FW_DIR / fname if fname else None
        local = False
        local_path = ""
        for p in (cache_path, bundle_path):
            if p and p.is_file():
                local = True
                local_path = str(p)
                break
        entries.append({**e, "local": local, "localPath": local_path,
                        "hasReleaseAsset": bool(e.get("releaseAsset"))})
    return jsonify(firmware=entries)


@app.post("/api/firmware/fetch")
def api_fw_fetch():
    """#567 — download a single registry entry's binary from the matching
    GitHub release asset into the writable cache directory. Idempotent:
    a redownload overwrites the cached copy."""
    from firmware_manager import download_firmware, _registry_fetch_assets
    body = request.get_json(silent=True) or {}
    fid = body.get("id") or ""
    reg = load_registry(_FW_DIR)
    entry = next((e for e in reg.get("firmware", []) if e.get("id") == fid), None)
    if not entry:
        return jsonify(ok=False, err="unknown firmware id"), 404
    if not entry.get("releaseAsset"):
        return jsonify(ok=False, err="registry entry has no releaseAsset"), 400
    path = download_firmware(entry, _FW_CACHE_DIR)
    if not path:
        return jsonify(ok=False, err="download failed — asset missing or network error"), 502
    log.info("Firmware library fetch: %s → %s", fid, path)
    return jsonify(ok=True, id=fid, path=path)


@app.post("/api/firmware/refresh-all")
def api_fw_refresh_all():
    """#567 — bulk-download every registry entry that has a release asset
    but isn't cached locally. Skips entries already on disk so repeat
    clicks are cheap."""
    from firmware_manager import download_firmware, _registry_fetch_assets
    assets = _registry_fetch_assets()
    if assets is None:
        return jsonify(ok=False, err="could not reach GitHub releases"), 502
    reg = load_registry(_FW_DIR)
    results = []
    for e in reg.get("firmware", []):
        fname = e.get("file") or ""
        if not fname:
            continue
        cache_p = _FW_CACHE_DIR / fname
        bundle_p = _FW_DIR / fname
        if cache_p.is_file() or bundle_p.is_file():
            results.append({"id": e.get("id"), "status": "already-local"})
            continue
        if not e.get("releaseAsset"):
            results.append({"id": e.get("id"), "status": "no-release-asset"})
            continue
        path = download_firmware(e, _FW_CACHE_DIR, assets_by_name=assets)
        results.append({"id": e.get("id"),
                        "status": "downloaded" if path else "download-failed"})
    downloaded = sum(1 for r in results if r["status"] == "downloaded")
    return jsonify(ok=True, results=results, downloaded=downloaded)

@app.post("/api/firmware/download")
def api_fw_download():
    """Download latest firmware from GitHub Releases and save locally for USB flashing."""
    body = request.get_json(silent=True) or {}
    board = body.get("board", "")
    if board not in ("esp32", "d1mini"):
        return jsonify(ok=False, err="board must be esp32 or d1mini"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    # USB flash needs merged binary; OTA needs app-only. Download both for ESP32.
    # For D1 Mini there's only one binary that works for both.
    downloads = {
        "esp32": [
            ("esp32-firmware-merged.bin", "esp32/main.ino.merged.bin"),
            ("esp32-firmware-app.bin",    "esp32/main.ino.bin"),
        ],
        "d1mini": [
            ("d1mini-firmware.bin", "d1mini/main.ino.bin"),
        ],
    }
    assets_available = {a["name"]: a["url"] for a in rel.get("assets", [])}
    pairs = downloads.get(board, [])
    downloaded = 0
    import urllib.request as _ur
    try:
        for asset_name, target_path in pairs:
            url = assets_available.get(asset_name)
            if not url:
                log.warning("Asset %s not in release, skipping", asset_name)
                continue
            log.info("Downloading %s from %s", asset_name, url)
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=60)
            data = resp.read()
            dest = _FW_DIR / target_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            log.info("Downloaded %s (%d bytes)  -' %s", asset_name, len(data), dest)
            downloaded += 1
        if downloaded == 0:
            return jsonify(ok=False, err=f"No firmware assets for {board} in release"), 404
        # Update local registry version
        reg_path = _FW_DIR / "registry.json"
        if reg_path.exists():
            reg = json.loads(reg_path.read_text())
            for fw in reg.get("firmware", []):
                if fw.get("board") == board and "child" in fw.get("id", ""):
                    fw["version"] = rel["version"]
            reg_path.write_text(json.dumps(reg, indent=2))
        return jsonify(ok=True, version=rel["version"], downloaded=downloaded)
    except Exception as e:
        log.error("Download failed: %s", e)
        return jsonify(ok=False, err=str(e)), 502

@app.get("/api/firmware/binary/<board>")
def api_fw_binary(board):
    """Serve a firmware binary for OTA   " child downloads from parent over plain HTTP.
    ESP32 OTA needs app-only binary (main.ino.bin), NOT the merged binary."""
    file_map = {"esp32": "esp32/main.ino.bin", "d1mini": "d1mini/main.ino.bin",
                 "esp32s3": "esp32s3/main.ino.bin"}
    rel_path = file_map.get(board)
    if not rel_path:
        return jsonify(ok=False, err=f"unknown board: {board}"), 404
    bin_path = _FW_DIR / rel_path
    if not bin_path.exists():
        # Try downloading from GitHub first
        rel = _fetch_github_release()
        if rel:
            # OTA needs app-only binary; try esp32-firmware-app.bin first, fallback to merged
            asset_names = {"esp32": ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
                           "d1mini": ["d1mini-firmware.bin"],
                           "esp32s3": ["esp32s3-firmware-app.bin", "esp32s3-firmware-merged.bin"]}
            asset_name = None
            for name in asset_names.get(board, []):
                if any(a["name"] == name for a in rel.get("assets", [])):
                    asset_name = name
                    break
            for a in rel.get("assets", []):
                if a["name"] == asset_name:
                    try:
                        import urllib.request as _ur
                        log.info("Downloading %s from GitHub for proxy serve", asset_name)
                        req = _ur.Request(a["url"], headers={"User-Agent": "SlyLED-Parent"})
                        resp = _ur.urlopen(req, timeout=60)
                        data = resp.read()
                        bin_path.parent.mkdir(parents=True, exist_ok=True)
                        bin_path.write_bytes(data)
                    except Exception as e:
                        log.error("Download failed: %s", e)
                        return jsonify(ok=False, err="download from GitHub failed"), 502
                    break
    if not bin_path.exists():
        return jsonify(ok=False, err="firmware binary not available"), 404
    return send_file(str(bin_path), mimetype="application/octet-stream",
                     download_name=f"slyled-{board}.bin")

@app.post("/api/firmware/detect")
def api_fw_detect():
    """Detect chip type on an ambiguous port."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    chip = detect_chip(port)
    return jsonify(ok=True, board=chip)

@app.post("/api/firmware/flash")
def api_fw_flash():
    """Flash firmware to a board in a background thread."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    if not _wifi.get("ssid") or not _wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required before flashing - set them on the Firmware tab first"), 400
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    fw_id = body.get("firmwareId", "")
    board = body.get("board", "")
    if not port or not fw_id:
        return jsonify(ok=False, err="port and firmwareId required"), 400
    reg = load_registry(_FW_DIR)
    fw = next((f for f in reg.get("firmware", []) if f["id"] == fw_id), None)
    if not fw:
        return jsonify(ok=False, err="firmware not found in registry"), 404
    # #568 — if the binary is missing locally, auto-download it from the
    # matching GitHub release asset before kicking off the flash. The
    # installer no longer bundles binaries, so the first flash of any
    # given board will pull from the cloud on demand.
    from firmware_manager import resolve_binary_path
    bin_path_str = resolve_binary_path(fw, _FW_CACHE_DIR, _FW_DIR, auto_download=True)
    if not bin_path_str:
        return jsonify(ok=False,
                       err=f"binary not found locally and release asset "
                           f"'{fw.get('releaseAsset') or fw.get('file')}' "
                           "could not be downloaded"), 502
    # Flash in background thread
    def _do_flash():
        flash_board(port, bin_path_str, board or fw["board"],
                    wifi_ssid=_wifi.get("ssid"), wifi_pass=_decrypt_pw(_wifi.get("password", "")))
    threading.Thread(target=_do_flash, daemon=True).start()
    return jsonify(ok=True, message="Flashing started")

@app.get("/api/firmware/flash/status")
def api_fw_flash_status():
    if not _fw_available:
        return jsonify(running=False, progress=0, message="not available")
    return jsonify(get_flash_status())

#  "  "  Help (Phase 7)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_HELP_SECTIONS = {
    "dash": "Dashboard",
    "setup": "Setup",
    "layout": "1-getting-started",
    "spatial-effects": "3-spatial-effects",
    "timeline": "4-timeline",
    "settings": "Settings",
    "firmware": "Firmware",
}

@app.get("/api/help/<section>")
def api_help(section):
    """Return help content for a given section, extracted from USER_MANUAL.md."""
    manual_path = BASE.parent.parent / "docs" / "USER_MANUAL.md"
    if not manual_path.exists():
        return jsonify(html="<p>User manual not found.</p>")
    try:
        text = manual_path.read_text(encoding="utf-8")
        # Find section by heading
        anchor = _HELP_SECTIONS.get(section, section)
        lines = text.split("\n")
        collecting = False
        result = []
        for line in lines:
            if line.startswith("## ") and anchor.lower() in line.lower():
                collecting = True
                result.append(line)
                continue
            if collecting and line.startswith("## "):
                break
            if collecting:
                result.append(line)
        if not result:
            return jsonify(html=f"<p>No help found for '{section}'.</p>")
        # Simple markdown  -' HTML conversion
        html = ""
        for line in result:
            if line.startswith("### "):
                html += f"<h4 style='color:#e2e8f0;margin:1em 0 .4em'>{line[4:]}</h4>"
            elif line.startswith("## "):
                html += f"<h3 style='color:#22d3ee;margin:0 0 .6em'>{line[3:]}</h3>"
            elif line.startswith("| "):
                html += f"<div style='font-family:monospace;font-size:.85em;color:#64748b'>{line}</div>"
            elif line.startswith("- "):
                html += f"<div style='padding-left:1em'>&#x2022; {line[2:]}</div>"
            elif line.strip():
                html += f"<p style='margin:.3em 0'>{line}</p>"
        return jsonify(html=html)
    except Exception as e:
        return jsonify(html=f"<p>Error loading help: {e}</p>")

@app.post("/api/reset")
def api_reset():
    """Clear all data and restore default settings."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    global _children, _settings, _layout, _stage, _actions
    global _fixtures, _objects, _temporal_objects, _spatial_fx, _timelines
    global _wifi, _nxt_c, _nxt_a, _dmx_settings, _bake_result
    global _nxt_fix, _nxt_obj, _nxt_sfx, _nxt_tl
    # Stop DMX playback + engines
    _dmx_playback_stop.set()
    try:
        _artnet.stop()
    except Exception:
        pass
    try:
        _sacn.stop()
    except Exception:
        pass
    # Stop all children
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for c in _children:
        if c.get("ip"):
            if c.get("type") == "wled":
                wled_stop(c["ip"])
            else:
                _send(c["ip"], pkt_stop)
                _send(c["ip"], pkt_off)
    _live_events.clear()
    _bake_result.clear()
    with _lock:
        _children = []
        _actions  = []
        _wifi     = {"ssid": "", "password": ""}
        _ssh      = {"sshUser": "root", "sshPassword": "", "sshKeyPath": ""}
        _layout   = dict(_DEFAULT_LAYOUT)
        _stage    = dict(_DEFAULT_STAGE)
        _settings = dict(_DEFAULT_SETTINGS)
        _fixtures   = list(_DEFAULT_FIXTURES)
        _objects    = list(_DEFAULT_OBJECTS)
        _temporal_objects.clear()
        _spatial_fx = list(_DEFAULT_SPATIAL_FX)
        _timelines  = list(_DEFAULT_TIMELINES)
        _dmx_settings = {"protocol": "artnet", "frameRate": 40, "bindIp": "0.0.0.0",
                         "universeRoutes": [], "sacnPriority": 100, "sacnSourceName": "SlyLED"}
        _nxt_c = _nxt_a = 0
        _nxt_fix = _nxt_obj = _nxt_sfx = _nxt_tl = 0
        _save("children", _children)
        _save("actions",  _actions)
        _save("wifi",     _wifi)
        _save("layout",   _layout)
        _save("stage",    _stage)
        _save("settings", _settings)
        _save("fixtures",   _fixtures)
        _save("objects",    _objects)
        _save("spatial_fx", _spatial_fx)
        _save("timelines",  _timelines)
        _save("dmx_settings", _dmx_settings)
        _show_playlist.clear()
        _show_playlist.update({"order": [], "loopAll": False})
        _save("show_playlist", _show_playlist)
        _camera_ssh.clear()
        _save("camera_ssh", _camera_ssh)
        _calibrations.clear()
        _save("calibrations", _calibrations)
        _range_cal.clear()
        _save("range_calibrations", _range_cal)
        _mover_cal.clear()
        _save("mover_calibrations", _mover_cal)
        _mover_cal_jobs.clear()
        _calib_state.clear()
        _tracking_state.clear()
        # Delete custom profiles (keep built-ins)
        for p in list(_profile_lib._profiles.values()):
            if not p.get("builtin"):
                _profile_lib.delete_profile(p["id"])
    return jsonify(ok=True)

#  "  "  OTA firmware update  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_github_release_cache = {"data": None, "ts": 0}
_GITHUB_RELEASE_TTL = 3600  # 1 hour cache

def _fetch_github_release():
    """Fetch latest release info from GitHub API. Returns dict or None."""
    import urllib.request as _ur
    now = time.time()
    if _github_release_cache["data"] and now - _github_release_cache["ts"] < _GITHUB_RELEASE_TTL:
        return _github_release_cache["data"]
    try:
        req = _ur.Request(
            "https://api.github.com/repos/SlyWombat/SlyLED/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SlyLED-Parent"})
        resp = _ur.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "").lstrip("v")
        assets = []
        for a in data.get("assets", []):
            assets.append({
                "name": a["name"],
                "size": a.get("size", 0),
                "url": a.get("browser_download_url", ""),
            })
        result = {"version": tag, "tag": data.get("tag_name", ""), "assets": assets,
                  "url": data.get("html_url", "")}
        _github_release_cache["data"] = result
        _github_release_cache["ts"] = now
        log.info("GitHub release: v%s (%d assets)", tag, len(assets))
        return result
    except Exception as e:
        log.debug("GitHub release fetch failed: %s", e)
        return _github_release_cache.get("data")  # return stale cache if available

@app.get("/api/firmware/latest")
def api_firmware_latest():
    """Return latest firmware version from GitHub Releases."""
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info from GitHub"), 502
    # Include registry firmware version + whether release has firmware binaries
    registry = load_registry(_FW_DIR).get("firmware", [])
    reg_versions = {e.get("board"): e.get("version", "0.0") for e in registry}
    has_fw = any(a.get("name", "").endswith(".bin") for a in rel.get("assets", []))
    return jsonify(**rel, registryVersion=max(reg_versions.values(), default="0.0"),
                   hasFirmware=has_fw)

@app.get("/api/firmware/check")
def api_firmware_check():
    """Compare each child's firmware against the registry entry for its
    specific board. Each board track is independent — gyros compare against
    gyro-esp32s3, D1 Mini against child-led-d1mini, etc."""
    if not _wifi.get("ssid") or not _wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required - set them on the Firmware tab before checking for updates"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    registry = load_registry(_FW_DIR).get("firmware", [])
    # Index registry entries by their `id` (e.g. "gyro-esp32s3", "child-led-esp32").
    reg_by_id = {e.get("id"): e for e in registry}

    # Map a child's detected board onto its registry id.
    board_to_regid = {
        "esp32":     "child-led-esp32",
        "d1mini":    "child-led-d1mini",
        "giga":      "child-led-giga",
        "giga-dmx":  "dmx-bridge-esp32",
        "dmx":       "dmx-bridge-esp32",
        "gyro":      "gyro-esp32s3",
    }

    def _detect_board(c):
        """Return a normalised board key for this child."""
        if c.get("type") == "wled":
            return "wled"
        if c.get("type") == "gyro":
            return "gyro"
        if c.get("type") == "dmx":
            # DMX bridge — prefer Giga-DMX when hostname is SLYC-* or boardType says so
            if (c.get("boardType") or "").lower().startswith("giga"):
                return "giga-dmx"
            return "dmx"
        bt = (c.get("boardType") or "").lower()
        if "gyro" in bt:
            return "gyro"
        if bt in ("esp32",):
            return "esp32"
        if "d1" in bt or bt == "d1mini":
            return "d1mini"
        if "giga" in bt:
            return "giga"
        return "esp32"  # last-resort fallback

    def _cmp(cur, latest):
        try:
            cur_parts  = [int(x) for x in (cur or "0.0").split(".")]
            lat_parts  = [int(x) for x in (latest or "0.0").split(".")]
            while len(cur_parts) < 3: cur_parts.append(0)
            while len(lat_parts) < 3: lat_parts.append(0)
            return lat_parts > cur_parts
        except (ValueError, IndexError):
            return (cur or "") != (latest or "")

    results = []
    for c in _children:
        board = _detect_board(c)
        reg_entry = reg_by_id.get(board_to_regid.get(board))
        latest = reg_entry.get("version", "0.0") if reg_entry else "0.0"
        fw = c.get("fwVersion", "0.0") or "0.0"
        needs_update = _cmp(fw, latest) if board != "wled" else False

        # OTA download URL (ESP32/D1 only — Gigas are DFU-only, gyro flashes over USB).
        asset_prefs = {
            "esp32":  ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
            "d1mini": ["d1mini-firmware.bin"],
        }
        download_url = ""
        for name in asset_prefs.get(board, []):
            for a in rel.get("assets", []):
                if a["name"] == name:
                    download_url = a["url"]
                    break
            if download_url:
                break

        results.append({
            "id": c["id"], "hostname": c.get("hostname"), "name": c.get("name", ""),
            "ip": c.get("ip", ""),
            "currentVersion": fw, "latestVersion": latest,
            "needsUpdate": needs_update, "board": board,
            "type": c.get("type", ""),
            "status": c.get("status", 0),
            "downloadUrl": download_url,
        })

    # The top-level "latest" is informational — the latest of any board.
    top_latest = max((r["latestVersion"] for r in results), default="0.0")
    return jsonify({"latest": top_latest, "children": results})

@app.post("/api/firmware/ota/<int:cid>")
def api_firmware_ota(cid):
    """Trigger OTA update on a specific child."""
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        return jsonify(ok=False, err="child not found"), 404
    if child.get("type") == "wled":
        return jsonify(ok=False, err="WLED devices update through their own UI"), 400
    if child.get("status") != 1:
        return jsonify(ok=False, err="child is offline"), 400

    # Require WiFi credentials to be configured before OTA
    if not _wifi.get("ssid"):
        return jsonify(ok=False, err="WiFi credentials not configured - set them on the Firmware tab first"), 400

    # Push WiFi credentials to child before OTA (so new firmware can reconnect)
    ip = child["ip"]
    try:
        import urllib.request as _ur
        wifi_body = json.dumps({"ssid": _wifi["ssid"],
                                "password": _decrypt_pw(_wifi.get("password", ""))}).encode()
        wifi_req = _ur.Request(f"http://{ip}/wifi", data=wifi_body, method="POST",
                               headers={"Content-Type": "application/json"})
        _ur.urlopen(wifi_req, timeout=3)
        log.info("OTA: pushed WiFi credentials to %s", ip)
    except Exception as e:
        log.warning("OTA: failed to push WiFi to %s: %s (continuing anyway)", ip, e)

    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    latest = rel.get("version", "0.0")

    # Determine board type from stored boardType
    bt = child.get("boardType", "")
    board = "esp32" if bt in ("ESP32", "esp32") else "d1mini" if bt in ("D1 Mini", "d1mini") else "esp32"
    # OTA needs app-only binary for ESP32; try app first, fallback to merged
    asset_prefs = {"esp32": ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
                   "d1mini": ["d1mini-firmware.bin"]}
    download_url = ""
    for name in asset_prefs.get(board, []):
        for a in rel.get("assets", []):
            if a["name"] == name:
                download_url = a["url"]
                break
        if download_url:
            break
    if not download_url:
        return jsonify(ok=False, err=f"no firmware binary for {board}"), 404

    # Parse version
    try:
        parts = latest.split(".")
        new_major = int(parts[0])
        new_minor = int(parts[1]) if len(parts) > 1 else 0
        new_patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return jsonify(ok=False, err="invalid version format"), 500

    # Send OTA command   " use parent as proxy (child can't do HTTPS to GitHub)
    ip = child["ip"]
    # Determine parent's LAN IP for the proxy URL
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        parent_ip = s.getsockname()[0]
        s.close()
    except Exception:
        parent_ip = "127.0.0.1"
    # Use the actual Flask port from the incoming request (not hardcoded 8080)
    parent_port = request.host.split(":")[-1] if ":" in request.host else "8080"
    proxy_url = f"http://{parent_ip}:{parent_port}/api/firmware/binary/{board}"
    log.info("OTA: triggering update on %s (%s) to v%s via proxy %s", ip, child.get("hostname"), latest, proxy_url)
    try:
        import urllib.request as _ur
        body = json.dumps({"url": proxy_url, "sha256": "", "major": new_major, "minor": new_minor, "patch": new_patch}).encode()
        req = _ur.Request(f"http://{ip}/ota", data=body, method="POST",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception as e:
        log.warning("OTA trigger to %s failed: %s", ip, e)
        # Child may have already started updating and dropped the connection   " that's OK
        pass

    return jsonify(ok=True, version=latest, board=board)

#  "  "  QR code for mobile app  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/api/qr")
def api_qr():
    """Generate a QR code PNG encoding slyled://{host}:{port} for the mobile app."""
    try:
        import qrcode
    except ImportError:
        return jsonify(ok=False, err="qrcode package not installed"), 500
    # Use the machine's LAN IP, not request.host (which may be localhost)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host = s.getsockname()[0]
        s.close()
    except Exception:
        host = request.host.split(":")[0]
    port = request.host.split(":")[-1] if ":" in request.host else "8080"
    url = f"slyled://{host}:{port}"
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="slyled-qr.png")

#  "  "  CORS  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.after_request
def add_cors(response):
    # Allow same-origin and Android app connections from LAN
    origin = request.headers.get("Origin", "")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        response.headers["Access-Control-Allow-Origin"] = request.host_url.rstrip("/")
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SlyLED-Confirm"
    return response

#  "  "  Shutdown  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.post("/api/shutdown")
def api_shutdown():
    """Terminate the parent process after sending the response."""
    # Require confirmation header to prevent CSRF
    if request.headers.get("X-SlyLED-Confirm") != "true":
        return jsonify(err="Missing confirmation header"), 403
    def _kill():
        time.sleep(0.3)
        _graceful_dmx_shutdown()
        os._exit(0)
    threading.Thread(target=_kill, daemon=True).start()
    return jsonify(ok=True)

#  "  "  SPA fallback - must be last  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.route("/lib/<path:filename>")
def spa_lib(filename):
    """Serve bundled JS libraries (Three.js etc.) — no internet required (#269)."""
    return send_from_directory(str(SPA / "lib"), filename)

@app.route("/js/<path:filename>")
def spa_js(filename):
    """Serve SPA JavaScript modules."""
    resp = send_from_directory(str(SPA / "js"), filename)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/css/<path:filename>")
def spa_css(filename):
    """Serve SPA stylesheets."""
    return send_from_directory(str(SPA / "css"), filename)

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa_fallback(path):
    if path.startswith("api/") or path in ("status", "favicon.ico"):
        abort(404)
    resp = send_from_directory(str(SPA), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

#  "  "  Entry point  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _check_single_instance(port):
    """Check if another instance is already running on this port."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://localhost:{port}/status", timeout=2)
        data = resp.read().decode()
        if "parent" in data or "SlyLED" in data:
            return True   # another instance is running
    except Exception:
        pass
    return False

def _migrate_v1_mover_cals():
    """Q9-P3 Phase 1 — eager v1→v2 mover-cal migration on startup.

    Walks every entry in _mover_cal, attempts to fit a ParametricFixtureModel
    (v2) for any cal still stored as v1 grid samples. Successful fits are
    persisted inline as v2; failures log a warning and leave the v1 cal as-
    is (lazy migration on first use will retry).

    Runs once at startup. Subsequent sessions observe only v2 on disk, which
    is the prerequisite for Q9 phases 2-5 (remove v1 read-paths, delete v1
    grid fitting code, clean up dead dict keys).
    """
    v1_count = 0
    migrated = 0
    failed = 0
    for fid_str, cal in list(_mover_cal.items()):
        if cal.get("version") == 2 and cal.get("model"):
            continue
        if not cal.get("samples"):
            continue
        v1_count += 1
        try:
            fid = int(fid_str)
        except (TypeError, ValueError):
            failed += 1
            continue
        try:
            model = _get_mover_model(fid)
        except Exception as e:
            log.warning("v1 migration failed for fid=%s: %s", fid_str, e)
            failed += 1
            continue
        if model is not None and cal.get("version") == 2:
            migrated += 1
        else:
            failed += 1
    if v1_count:
        log.info("Q9 mover-cal migration: %d v1 cals on disk — %d migrated to v2, "
                 "%d left as v1 (will retry lazily)", v1_count, migrated, failed)


if __name__ == "__main__":
    # #628 — re-derive stage bounds once at startup so rigs with stale
    # manually-edited stage.json self-heal without operator intervention.
    # No-op if the operator has stageBoundsManual=true.
    try:
        _apply_auto_stage_bounds()
    except Exception as _e:
        log.warning("stage auto-derive on startup failed: %s", _e)
    # Q9-P3 Phase 1 — try to migrate any v1 mover cals eagerly so the v2
    # pipeline is the only read path operators encounter after a restart.
    try:
        _migrate_v1_mover_cals()
    except Exception as _e:
        log.warning("v1 mover-cal migration on startup failed: %s", _e)
    ap = argparse.ArgumentParser(description="SlyLED Parent Server")
    ap.add_argument("--port",       type=int, default=8080)
    ap.add_argument("--host",       default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if _check_single_instance(args.port):
        print(f"SlyLED Orchestrator is already running on port {args.port}.")
        print(f"Opening browser to existing instance...")
        webbrowser.open(f"http://localhost:{args.port}")
        sys.exit(0)

    start_background_tasks()

    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{args.port}")
        threading.Thread(target=_open, daemon=True).start()

    print(f"SlyLED Orchestrator  v{VERSION}")
    print(f"  UI   -> http://localhost:{args.port}")
    print(f"  Data -> {DATA}")
    app.run(host=args.host, port=args.port, threaded=True)






















