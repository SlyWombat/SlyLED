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
import re
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
from urllib.parse import urlsplit

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
# #899 — fixture-type registry: POST/PUT fixture validation, per-type
# create defaults, and the generic-PUT whitelist are registry-driven.
# New sensor types (radar, #911) register in fixture_types.py — the
# routes here need no edits.
import fixture_types
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

VERSION = "2.1.1"

#  "  "  UDP protocol  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

UDP_MAGIC   = 0x534C
UDP_VERSION = 5   # #819 — added CMD_GYRO_STOP (0x69); orient-flags bit 3 retired
UDP_PORT    = 4210
# #862 — Android Auto Brightness has its own UDP port. Pre-fix #861 dispatched
# AUTOBRI_PUSH on the shared 4210 listener, but Windows hosts intermittently
# refuse to bind 4210 (kernel-level reservation by HNS / Hyper-V port pool;
# the bind fails with WinError 10013/10048 even with no visible holder). All
# the firmware-side wire (PING/PONG, ACTION_EVENT, GYRO_*) is locked to 4210
# because the firmware hardcodes it; AUTOBRI_PUSH is Android-only, so we can
# pick a less-contended port. 4211 is adjacent for memorability.
UDP_AUTOBRI_PORT = 4211

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
CMD_OTA_STATUS  = 0x51   # child→parent: OtaStatusPayload — status(u8, OTA_STATUS_* per main/OtaUpdate.h) + progress(u8, 0-100). Fire-and-forget from the updating board on each OTA phase change + every ≥10% of download (#922). 0x50 CMD_OTA_UPDATE is parent→child and triggered over HTTP here — nothing to dispatch.

CMD_GYRO_ORIENT = 0x60   # gyro→parent: GyroOrientPayload (8 bytes)
CMD_GYRO_CTRL   = 0x61   # parent→gyro: enabled(1) + targetFps(1)
CMD_GYRO_RECAL  = 0x62   # parent→gyro: zero IMU reference (no payload)
CMD_GYRO_COLOR  = 0x63   # gyro→parent: GyroColorPayload (r, g, b, flags)
CMD_GYRO_CALIBRATE = 0x64  # gyro→parent: calibrate start/end + orientation
CMD_GYRO_HEARTBEAT = 0x65  # parent→gyro: 2s cadence while claim active (#476)
CMD_GYRO_START         = 0x66  # gyro→parent: explicit press-START — claim + start_stream (#772)
CMD_GYRO_CLAIM_DENIED  = 0x67  # parent→gyro: claim refused, gyro reverts to IDLE (#772)
CMD_GYRO_BATT          = 0x68  # gyro→parent: battery telemetry (vbat100 + pct + flags), 10 s cadence (#813 follow-up)
CMD_GYRO_STOP          = 0x69  # gyro→parent: discrete press-STOP — release claim + park + end_session (#819). Nonce(2) since #825 (legacy header-only still accepted).
CMD_GYRO_CLAIM_ACK     = 0x6A  # parent→gyro: claim established — {nonce(2), moverId(2)} (#825).
CMD_GYRO_STOP_ACK      = 0x6B  # parent→gyro: stop confirmed — {nonce(2)} (#825).
CMD_GYRO_HEARTBEAT_REP = 0x6C  # gyro→parent: heartbeat reply — {uiState(1), claimNonce(2), seq(2)} (#825).
CMD_AUTOBRI_PUSH       = 0x6D  # phone→parent: Android Auto Brightness master push (#861) — {master(1), flags(1), seq(1)}. 20 Hz fire-and-forget UDP; orchestrator coalesces by overwriting `_settings["globalBrightness"]` per packet. Replaces the prior HTTP /api/brightness fast path.
CMD_GYRO_OFF           = 0x6E  # gyro→parent: explicit press-OFF (#867) — same nonce shape as CMD_GYRO_STOP but server releases the claim with blackout=True so the head goes dark. STOP leaves head at last frame; OFF blackouts the head AND releases. ACK reuses CMD_GYRO_STOP_ACK.
CMD_GYRO_AIM_WIZARD    = 0x6F  # gyro→parent: empirical aim-axis wizard (#869) — 36-byte payload: 3 Euler triples in degrees (roll, pitch, yaw) for {neutral, pitch_forward, yaw_left}, each 3×float32 LE. Server converts to quats via quat_from_euler_zyx_deg, then runs the same _aim_wizard_compute (#826) the Android wizard uses; persists derived forward_local/up_local on the gyro's `gyro-<ip>` Remote.

# 0x7x — MMwave radar node range (#910). Wire source of truth is
# mmwave/MmwProtocol.h (the node's isolated sketch tree); parity with
# main/Protocol.h is enforced by tests/test_mmwave_wire_parity.py.
CMD_MMW_TARGETS = 0x70  # radar node→parent: MmwTargetsPayload — seq(u16) count(u8) flags(u8, bit0 = radar parse healthy) + 3 × {xMm i16, yMm i16, speedCms i16, resMm u16} = 28 bytes; unused slots zeroed. Sent on fresh frames with targets (≤25 Hz) + 1 Hz empty keepalive.
CMD_MMW_CONFIG  = 0x71  # parent→node: reserved (mode switch / report-rate cap) — deliberately NOT implemented in v1 (design doc §4.3).

# #825 — uiState codes carried in CMD_GYRO_HEARTBEAT_REP.
GYRO_UI_IDLE        = 0
GYRO_UI_WAITING_ACK = 1
GYRO_UI_ACTIVE      = 2
GYRO_UI_STOPPING    = 3

# #872 — CMD_GYRO_CLAIM_DENIED reason codes (1-byte payload).
# See `docs/gyro-claim-lifecycle.md` §3.6 for the operator-facing
# string table the gyro firmware renders. Older firmware (<1.2.11)
# ignores the byte and falls back to the legacy "BUSY / Mover held
# by other" string.
GYRO_DENIED_IDLE                = 0  # unspecified / replayed-from-cache
GYRO_DENIED_CONTROLLER_INACTIVE = 1  # gyroEnabled=False on the gyro fixture
GYRO_DENIED_ALREADY_CLAIMED     = 2  # _mover_engine.claim() returned False
GYRO_DENIED_NO_MOVER_ASSIGNED   = 3  # target_mover_id is None
GYRO_DENIED_ENGINE_UNAVAILABLE  = 4  # _mover_engine is None / not running

#  "  "  Paths  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

BASE = Path(__file__).parent

# When packaged with PyInstaller --onefile, files land in sys._MEIPASS
if getattr(sys, "frozen", False):
    SPA = Path(sys._MEIPASS) / "spa"
    DOCS_ROOT = Path(sys._MEIPASS) / "docs"
    DOCS_HELP = DOCS_ROOT / "help"
else:
    SPA = BASE / "spa"
    DOCS_ROOT = BASE.parent.parent / "docs"
    DOCS_HELP = DOCS_ROOT / "help"

# Persist data under %APPDATA%\SlyLED on Windows; fall back to BASE/data
# elsewhere. SLYLED_DATA overrides both — tests and the screenshot tools
# set it to a throwaway directory so importing this module (and its
# `app.test_client()` writes) can never clobber a live operator project.
if os.environ.get("SLYLED_DATA"):
    DATA = Path(os.environ["SLYLED_DATA"])
elif os.name == "nt" and os.environ.get("APPDATA"):
    DATA = Path(os.environ["APPDATA"]) / "SlyLED" / "data"
else:
    DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)

#  "  "  Persistence  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _load(name, default):
    p = DATA / f"{name}.json"
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # #889 — NEVER silently default over a corrupt/unreadable file:
        # the next _save would overwrite the operator's project with the
        # empty default. Quarantine it as <name>.json.corrupt (numbered
        # if one already exists) so the data is recoverable, log loudly,
        # then start from the default.
        q = DATA / f"{name}.json.corrupt"
        n = 1
        while q.exists():
            q = DATA / f"{name}.json.corrupt.{n}"
            n += 1
        try:
            os.replace(p, q)
            where = str(q)
        except OSError as move_err:
            where = f"COULD NOT QUARANTINE ({move_err}) — left in place"
        log.error("!!! CORRUPT PERSISTENCE FILE %s: %s — quarantined to %s; "
                  "starting '%s' from defaults (#889)", p, e, where, name)
        print(f"!!! CORRUPT PERSISTENCE FILE {p}: {e} — quarantined to {where}; "
              f"starting '{name}' from defaults (#889)", file=sys.stderr)
        return default

def _save(name, obj):
    # #889 — atomic write: dump to a temp file in the same directory,
    # then os.replace() over the target (atomic on the same filesystem,
    # including Windows). A crash mid-write leaves the previous file
    # intact instead of a truncated one. Pattern copied from
    # remote_orientation.py::RemoteRegistry.save.
    p = DATA / f"{name}.json"
    tmp = DATA / f"{name}.json.tmp"
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)
    # #853 — fixture changes invalidate the per-universe intensity-
    # channel cache used by the master grand-master send-time gate.
    # Any path that adds / removes / re-addresses / re-profiles a
    # fixture goes through `_save("fixtures", _fixtures)`, so this
    # is the single chokepoint for the invalidation. Forward
    # reference is fine — `_invalidate_intensity_offsets_cache` is
    # defined later in the file but `_save` is only ever called at
    # runtime when both definitions are loaded.
    if name == "fixtures":
        try:
            _invalidate_intensity_offsets_cache()
        except NameError:
            # Module-load ordering: fixtures may be saved during
            # bootstrap before the cache helpers are defined. Safe
            # to ignore — the lazy build on first read picks up
            # everything correctly.
            pass

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
    # Route through rotation_from_layout so the array index→semantic
    # mapping is single-source. #600 swap lands cleanly this way.
    try:
        from camera_math import rotation_from_layout
        rx, ry, _roll = rotation_from_layout(rotation)
    except Exception:
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

# #841 — one-shot migration: strip stale `colorWheel` from any non-type-17
# action. Pre-fix, the DMX Scene / PT-Move / Gobo Select editors all wrote
# `colorWheel: 0` into the action body even when the user never touched
# the field, which then defeated the rgb_to_wheel_slot fallback at render
# time on hybrid RGB+wheel fixtures (#842). Wheel slots belong to the
# Colour Wheel action (type 17) only.
def _migrate_strip_stale_color_wheel():
    changed = 0
    for a in _actions:
        if a.get("type") != 17 and "colorWheel" in a:
            a.pop("colorWheel", None)
            changed += 1
    if changed:
        _save("actions", _actions)
        print(f"[migration #841] stripped colorWheel from {changed} non-type-17 action(s)")
_migrate_strip_stale_color_wheel()

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


# #600 — rotation-convention schema version. v1 used [rx pitch, ry=pan, rz=roll];
# v2 swaps to axis-letter-matched [rx pitch, ry=roll, rz=yaw]. Loader migrates
# persisted data once on startup. Stored under the top-level `_layout` dict
# because every layout-positioned fixture carries a rotation and the layout
# write path is where migration naturally fires.
_ROTATION_SCHEMA_VERSION = 2


def _migrate_rotation_schema():
    """One-shot migration: swap rotation[1] ↔ rotation[2] on every fixture
    that still stores the pre-#600 convention. Safe to call multiple times
    — no-op once _layout.rotationSchemaVersion is already 2.

    Also handles the same swap for fixture records that carry rotation at
    the top level (cameras / DMX movers loaded from _fixtures).
    """
    if (_layout or {}).get("rotationSchemaVersion") == _ROTATION_SCHEMA_VERSION:
        return 0
    swapped = 0
    # Fixtures table (cameras, DMX) carries rotation on the fixture record.
    for f in (_fixtures or []):
        rot = f.get("rotation")
        if isinstance(rot, list) and len(rot) >= 3:
            f["rotation"] = [rot[0], rot[2], rot[1]]
            swapped += 1
    # Layout children may also carry a rotation when the operator set one
    # via /api/fixtures/<fid>/aim.
    for c in (_layout.get("children") or []):
        rot = c.get("rotation")
        if isinstance(rot, list) and len(rot) >= 3:
            c["rotation"] = [rot[0], rot[2], rot[1]]
            swapped += 1
    _layout["rotationSchemaVersion"] = _ROTATION_SCHEMA_VERSION
    if swapped:
        try:
            _save("fixtures", _fixtures)
            _save("layout", _layout)
            log.info("#600 rotation migration: swapped ry↔rz on %d records", swapped)
        except Exception as e:
            log.warning("#600 rotation migration persist failed: %s", e)
    else:
        # No swaps needed (fresh install or already migrated), but still
        # persist the schema marker so we don't try again.
        try:
            _save("layout", _layout)
        except Exception:
            pass
    return swapped


# #780 Principle 1 — `mountedInverted` becomes save-time only.
#
# The flag previously survived to runtime, where every world-XYZ → DMX
# call site re-applied an "inverted ⇒ +180° roll" augmentation. With
# SMART (Home + Home-Secondary) deriving sign conventions from the
# operator's direction calls, the runtime augmentation double-counted
# the inversion — visible as fid 17 (inverted) and fid 19 (upright)
# refusing to mirror under the same phone-yaw input (#779).
#
# The fix: at fixture save / project import / startup migration, fold
# `mountedInverted=True` into `rotation[1] += 180°` (a roll about the
# fixture's forward axis — the stage-Y axis under the #600 convention)
# and clear the flag. After that, every IK path consumes a single
# rotation source of truth.
#
# Cosmetic 3D-viewport code may still read `mountedInverted`; once
# migrated it is uniformly False, and the same upside-down yoke render
# is reachable from `rotation[1] ≈ 180°` directly.

_MOUNTED_INVERTED_SCHEMA_VERSION = 1


def _normalise_mounted_inverted(fixture):
    """If `fixture["mountedInverted"]` is truthy, fold the inversion
    into `fixture["rotation"][1] += 180°` and set the flag to False.
    Returns True when the record was changed.

    Idempotent — safe to call on already-migrated records (the flag is
    False so nothing happens). Preserves any rx/rz the operator set
    manually.
    """
    if not isinstance(fixture, dict):
        return False
    if not bool(fixture.get("mountedInverted")):
        return False
    rot = fixture.get("rotation") or [0.0, 0.0, 0.0]
    if not isinstance(rot, list) or len(rot) < 3:
        rot = [0.0, 0.0, 0.0]
    try:
        rx = float(rot[0])
        ry = float(rot[1])
        rz = float(rot[2])
    except (TypeError, ValueError):
        rx = ry = rz = 0.0
    ry_new = (ry + 180.0) % 360.0
    if ry_new > 180.0:
        ry_new -= 360.0
    fixture["rotation"] = [rx, ry_new, rz]
    fixture["mountedInverted"] = False
    return True


def _migrate_mounted_inverted_schema():
    """One-shot migration: bake `mountedInverted=True` into
    `rotation[1] += 180°` for every fixture record. No-op once
    `_layout.mountedInvertedSchemaVersion` is already current.
    """
    if (_layout or {}).get("mountedInvertedSchemaVersion") == _MOUNTED_INVERTED_SCHEMA_VERSION:
        return 0
    baked = 0
    for f in (_fixtures or []):
        if _normalise_mounted_inverted(f):
            baked += 1
    _layout["mountedInvertedSchemaVersion"] = _MOUNTED_INVERTED_SCHEMA_VERSION
    if baked:
        try:
            _save("fixtures", _fixtures)
            _save("layout", _layout)
            log.info("#780 P1 mountedInverted migration: baked %d fixture(s) "
                     "into rotation[1]", baked)
        except Exception as e:
            log.warning("#780 P1 mountedInverted migration persist failed: %s", e)
    else:
        try:
            _save("layout", _layout)
        except Exception:
            pass
    return baked


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
        if "x" not in c and "y" not in c and "z" not in c:
            continue  # entry without a position at all — layout registry row
        x = float(c.get("x") or 0)
        y = float(c.get("y") or 0)
        z = float(c.get("z") or 0)
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

# #922 — live per-child OTA progress from CMD_OTA_STATUS (0x51), keyed by
# sender IP → {status, statusName, progress, updatedAt}. Surfaced per child
# as the `ota` field on /api/firmware/check rows (orch_firmware) so the
# Firmware tab can show download/verify/apply progress. Status codes are
# the firmware's OTA_STATUS_* constants (main/OtaUpdate.h).
_OTA_STATUS_NAMES = {0: "idle", 1: "downloading", 2: "verifying",
                     3: "applying", 4: "success", 5: "failed", 6: "rejected"}
_ota_status_live = {}

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


# ── #813 — gyro Active/Inactive lifecycle (operator-driven claim) ─────────
#
# Press-Start on the gyro firmware sends `CMD_GYRO_START` (#772). The
# orchestrator looks up the matching gyro fixture, validates Active
# (`gyroEnabled`), and runs `MoverControlEngine.claim` + `start_stream`.
# On refusal the orchestrator answers `CMD_GYRO_CLAIM_DENIED` so the
# gyro can revert its UI to IDLE.
#
# Inactive state (#801): refuse incoming `CMD_GYRO_START`, release any
# live claim, and send `CMD_GYRO_CTRL(enabled=0)` on the Active→Inactive
# transition so the gyro firmware reverts its UI immediately.
#
# Operator-facing terminology is "Active" / "Inactive"; the on-disk
# field name remains `gyroEnabled` (bool) for backward compat.
#
# Pre-#813 the orchestrator also ran a 5 s auto-lock loop that fired
# `CMD_GYRO_CTRL(enabled=1)` to every Active+disconnected gyro. That
# loop has been removed entirely: it raced with the press-Start flow,
# caused stale-state cascades (#812), and produced constant idle UDP
# traffic with no real benefit now that the gyro self-initiates via
# `CMD_GYRO_START`.


def _gyro_child_ip_for_fixture(gf):
    """Resolve a gyro fixture's child → IP. Returns None when the child
    is missing or has no IP yet (gyro never PONG'd in)."""
    cid = gf.get("gyroChildId")
    if cid is None:
        return None
    c = next((ch for ch in _children if ch["id"] == cid), None)
    return c.get("ip") if c else None


def _gyro_send_release_packet(ip):
    """Send `CMD_GYRO_CTRL(enabled=0)` — tells the gyro to stop its
    orient stream. Used on Active → Inactive transitions."""
    pkt = _hdr(CMD_GYRO_CTRL) + struct.pack("<BB", 0, 0)
    _send(ip, pkt)


def _gyro_inactive_transition(gf):
    """#801 — Active → Inactive: release claim + send CMD_GYRO_CTRL(0)."""
    ip = _gyro_child_ip_for_fixture(gf)
    if ip:
        try:
            _gyro_send_release_packet(ip)
        except Exception:
            log.debug("gyro Inactive: release packet to %s failed", ip,
                      exc_info=True)
    mid = gf.get("assignedMoverId")
    if mid is not None and _mover_engine:
        try:
            # #813 §1.2 / §6.2 — Inactive transition follows the same
            # "release to whatever was driving it before" semantics as
            # press-Stop. Forced blackout would create a 1-frame
            # flicker if a timeline / Track-action is about to write
            # the same channel on the next engine tick.
            _mover_engine.release(mid, f"gyro-{ip}" if ip else None,
                                   blackout=False)
        except Exception:
            log.debug("gyro Inactive: claim release for mover %s failed",
                      mid, exc_info=True)
    log.info("Gyro fid=%s set Inactive — claim released, gyro CTRL(0) sent",
             gf.get("id"))


# #813 — `_gyro_active_lock_loop` / `_tick` deleted. Press-Start on
# the gyro (`CMD_GYRO_START`) is the sole claim trigger; orchestrator
# never spontaneously reaches out to a gyro during idle.


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
# #896 — fused-away temporal id → {"to": surviving id, "at": timestamp}.
# _fuse_temporal_objects collapses a cluster into the lowest id; the
# other ids vanish from _temporal_objects, but the cameras that created
# them keep PUTting /api/objects/<old id>/pos. This map forwards those
# updates to the survivor (and the response's "objectId" lets the
# camera rebind). Chains are collapsed at insert; pruned on reap.
_fused_id_map = {}
_FUSED_ID_TTL_S = 60.0
_nxt_tmp = 10000       # temporal IDs start at 10000 to avoid collision
_nxt_sfx = max((f["id"] for f in _spatial_fx),  default=-1) + 1
_nxt_tl  = max((t["id"] for t in _timelines),  default=-1) + 1
_lock  = threading.Lock()  # #894 — rule: any write to a persisted collection (fixtures/children/objects/…) holds _lock

#  "  "  DMX subsystems  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 
_profile_lib = ProfileLibrary(data_dir=str(DATA))
# #853 — master grand-master callbacks. Lazy lambdas so `_settings`,
# `_fixtures`, `_GAMMA_LUT`, and `_get_intensity_offsets` (all defined
# later in this file) resolve at the engine's call time, not at engine
# construction. The engines's send loops snapshot the master once per
# frame and apply the gamma-corrected scaling at get-time, eliminating
# the per-render-path scaling boilerplate that v1.7.82 introduced.
_artnet = ArtNetEngine(
    get_global_brightness=lambda: _settings.get("globalBrightness", 255),
    get_intensity_offsets=lambda uni: _get_intensity_offsets(uni),
)
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
    # #687 follow-up — stop the `ollama serve` daemon if we started
    # it. ollama_runtime.stop_serve() is a no-op when the daemon was
    # already running before we booted (system service / menu-bar app /
    # operator-launched), so a shared dev box's existing Ollama isn't
    # torn down by an orchestrator restart.
    try:
        import ollama_runtime as _or
        _or.stop_serve()
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

# #859 — shared UDP sender socket. Pre-fix `_send` opened a fresh
# `socket.SOCK_DGRAM` (3 kernel transitions: socket / sendto / close)
# per call. With Auto Brightness streaming `/api/brightness` at 20 Hz,
# `_broadcast_brightness` would fire ~20 N socket-create / close pairs
# per second to N LED children. On Windows / WSL the per-create cost
# is non-trivial and contends the orchestrator's GIL with the playback
# loop and Flask handlers. Single shared sender drops `_send` to one
# `sendto` syscall per packet.
_SEND_SOCK = None
_SEND_SOCK_LOCK = threading.Lock()


def _get_send_sock():
    """Lazy-init the shared sender socket. Caller must NOT close it."""
    global _SEND_SOCK
    if _SEND_SOCK is None:
        with _SEND_SOCK_LOCK:
            if _SEND_SOCK is None:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Allow broadcast — `_broadcast_brightness` and similar
                # callers may target subnet-wide addresses.
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except OSError:
                    pass
                _SEND_SOCK = s
    return _SEND_SOCK


def _send(ip, pkt):
    """#859 — single-syscall UDP send via the shared sender socket.
    Pre-fix this opened+closed a fresh socket per call (~3 kernel
    transitions). Multi-thread safe: `sendto` on a UDP socket is
    re-entrant; no userspace lock needed."""
    try:
        _get_send_sock().sendto(pkt, (ip, UDP_PORT))
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
            old_name = child.get("name") or ""
            child.update({k: v for k, v in info.items() if k != "id"})
            if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                child["fwVersion"] = saved_fw
            # Always probe for full telemetry (version, board type, RSSI, etc.)
            _probe_board_type(child)
            # #618 — node-wins-for-identity: when the operator changed
            # altName on the child's /config page, propagate it to any
            # fixtures still showing the old auto-generated name (the
            # hostname or IP at registration time). Fixtures the operator
            # explicitly renamed are left untouched.
            new_name = child.get("name") or ""
            if new_name and new_name != old_name:
                _sync_fixture_names_from_child(child, old_name)
            return True
    child["status"] = 0
    return False


def _sync_fixture_names_from_child(child, old_name):
    """#618 — propagate child rename to its fixtures when the fixture is
    still using the original auto-generated identity (hostname / IP /
    previous child name). Avoids clobbering operator-customised names."""
    cid = child.get("id")
    if cid is None:
        return
    new_name = child.get("name") or ""
    host = child.get("hostname") or ""
    ip = child.get("ip") or ""
    auto_names = {old_name, host, ip}
    auto_names.discard("")
    if not auto_names:
        return
    changed = False
    for f in _fixtures:
        if f.get("childId") != cid:
            continue
        cur = f.get("name") or ""
        if cur in auto_names:
            f["name"] = new_name
            changed = True
            log.info("FIXTURE-NAME-SYNC: fid %s '%s' → '%s' (child %s)",
                     f.get("id"), cur, new_name, cid)
    if changed:
        try:
            _save("fixtures", _fixtures)
        except Exception:
            pass

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

# #871 — per-action-type mapping from the wire's `p8a/b/c/d` slots
# back to the action body's named keys. Mirrors the render-function
# signatures in `main/ChildLED.cpp:516-528` exactly:
#
#   case ACT_FADE:    renderFade(r, g, b, p8a, p8b, p8c, p16a, ...)
#   case ACT_BREATHE: renderBreathe(r, g, b, p16a, p8a, ...)
#   case ACT_CHASE:   renderChase(r, g, b, p16a, p8a, p8c, ...)
#   case ACT_FIRE:    renderFire(r, g, b, p16a, p8a, p8b, ...)
#   case ACT_COMET:   renderComet(r, g, b, p16a, p8a, p8c, p8d, ...)
#   case ACT_TWINKLE: renderTwinkle(r, g, b, p16a, p8a, p8d, ...)
#   ... (full list in ChildLED.cpp:516)
#
# Pre-#871 a `dict.get` chain (p8a → r2 → minBri → spacing → ...
# → cooling → tailLen → density) walked all named aliases until one
# was present. Bake materialises a fully-zero-defaulted params dict
# (every alias key present, irrelevant ones at 0) so the chain
# always short-circuited at the FIRST present alias, which for a
# Fire effect was `r2 = 0` — `cooling` and `sparking` were never
# read. Wire packet went out as `(t=6, r,g,b, p16a, 0, 0, 0, 0)`
# and the firmware's fire algorithm produced no sparks.
#
# A `None` slot here means "this action type doesn't use that p8
# slot" — the wire still gets a 0 for it.
_ACT_P8_FIELDS = {
    0:  (None,        None,         None,         None),       # ACT_BLACKOUT
    1:  (None,        None,         None,         None),       # ACT_SOLID
    2:  ("r2",        "g2",         "b2",         None),       # ACT_FADE
    3:  ("minBri",    None,         None,         None),       # ACT_BREATHE
    4:  ("spacing",   None,         "direction",  None),       # ACT_CHASE
    5:  ("paletteId", None,         "direction",  None),       # ACT_RAINBOW
    6:  ("cooling",   "sparking",   None,         None),       # ACT_FIRE
    7:  ("tailLen",   None,         "direction",  "decay"),    # ACT_COMET
    8:  ("density",   None,         None,         "fadeSpeed"),# ACT_TWINKLE
    9:  (None,        None,         None,         None),       # ACT_STROBE — p8a=duty% per Protocol.h, but action body has no `duty` field today; bake doesn't compute one. Leave 0 until the action schema is extended.
    10: (None,        None,         "direction",  None),       # ACT_WIPE_SEQ
    11: (None,        None,         None,         None),       # ACT_SCANNER — p8a=barWidth per Protocol.h, but action body has no `barWidth` today.
    12: ("density",   None,         None,         None),       # ACT_SPARKLE
    13: ("r2",        "g2",         "b2",         None),       # ACT_GRADIENT
}

# Per-action-type mapping for the `p16a` 16-bit slot. Most effects
# use `speedMs`; breathe/strobe use `periodMs`; twinkle/sparkle use
# `spawnMs` (the slower spawn cadence reads better as period-style).
_ACT_P16A_FIELD = {
    2:  "speedMs",   3:  "periodMs", 4:  "speedMs",  5:  "speedMs",
    6:  "speedMs",   7:  "speedMs",  8:  "spawnMs",  9:  "periodMs",
    10: "speedMs",   11: "speedMs",  12: "spawnMs",
}


def _act_params(act):
    """Extract generic param fields from an action dict, all coerced to int.

    #871 — type-dispatched lookup. Caller's `act` may have every
    field zero-defaulted (the bake materialises a fully-keyed
    params dict regardless of action type), so we MUST NOT rely on
    a `dict.get` fallback chain — it would short-circuit at the
    first present-but-zero alias and silently zero-out the
    type-relevant field. Use the per-type map instead.
    """
    t = int(act.get("type", 0) or 0)
    r = int(act.get("r", 0) or 0)
    g = int(act.get("g", 0) or 0)
    b = int(act.get("b", 0) or 0)
    p16_field = _ACT_P16A_FIELD.get(t)
    p16a = int(act.get(p16_field, 0) or 0) if p16_field else 0
    fields = _ACT_P8_FIELDS.get(t, (None, None, None, None))
    # Explicit caller-supplied `p8a` / `p8b` / `p8c` / `p8d` keys
    # win over the type-dispatched lookup so a bake-time override
    # path that already computed the slot value (e.g. a future
    # auto-promote pipeline) can still drive the wire directly.
    def _slot(slot_key, type_field):
        if slot_key in act:
            return int(act.get(slot_key, 0) or 0)
        if type_field is None:
            return 0
        return int(act.get(type_field, 0) or 0)
    p8a = _slot("p8a", fields[0])
    p8b = _slot("p8b", fields[1])
    p8c = _slot("p8c", fields[2])
    p8d = _slot("p8d", fields[3])
    return (t, r, g, b, p16a, p8a, p8b, p8c, p8d)

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

# B1 blueprint split — hand this module object to the shared-state bridge so
# extracted Blueprint modules (imported further down, at the positions their
# sections used to occupy) can reach the state that stays defined here. Must
# run before any `import orch_<section>` below. See orch_state.py.
import orch_state
orch_state.bind(sys.modules[__name__])

#  "  "  Status  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@app.get("/favicon.ico")
def favicon():
    return send_from_directory(str(SPA), "favicon.ico", mimetype="image/x-icon")

@app.get("/favicon.png")
def favicon_png():
    return send_from_directory(str(SPA), "favicon.png", mimetype="image/png")

@app.get("/status")
def status():
    # #771 — surface the UDP listener's bind state so the SPA can render a
    # Setup-tab banner when the orchestrator looks alive over HTTP but its
    # UDP listener silently bailed at startup (HNS / Hyper-V / WSL2 phantom
    # port reservation on Windows is the known trigger).
    udp = get_udp_listener_status()
    return jsonify(role="parent", hostname=socket.gethostname(),
                   version=VERSION, udpListener=udp)


@app.get("/api/status")
def api_status():
    """Same payload as /status — the SPA's Setup tab polls this to render
    the listener-health banner (#771)."""
    udp = get_udp_listener_status()
    return jsonify(role="parent", hostname=socket.gethostname(),
                   version=VERSION, udpListener=udp)


@app.post("/api/diagnostics/restart-udp-listener")
def api_diagnostics_restart_udp_listener():
    """#771 — operator-visible recovery for the silent-UDP-bind case.
    The SPA's Setup-tab banner exposes this as a one-click 'Retry' button
    after the operator has freed the port (e.g. Stop-Service winnat).
    Returns the listener status after one rebind attempt."""
    ok = restart_udp_listener()
    return jsonify(ok=ok, udpListener=get_udp_listener_status())

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
                # Blocking HTTP probe stays outside _lock; only the
                # status/seen mutation is locked (#894 — keep this
                # consistent with the locked non-WLED sweep below).
                info = wled_probe(c["ip"], timeout=2.0)
                with _lock:
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

# #771 — UDP listener health, exposed on /api/status so the SPA can render
# a Setup-tab banner when the listener thread can't own UDP_PORT. The
# Windows HNS / Hyper-V / WSL2 phantom-reservation case was silently
# turning every discover into a permanent no-op.
_udp_status = {
    "ok": False,
    "port": None,
    "lastError": None,
    "attemptedAt": None,
    "boundAt": None,
    "attempts": 0,
    # #901 — per-listener receive-loop failure counters (the
    # `except Exception: continue` paths in each recv loop). Exposed via
    # get_udp_listener_status() → /api/status so a wedged/erroring loop
    # is observable; the exception-swallowing behaviour is unchanged.
    "recvErrors": 0,          # main 4210 listener (_udp_listener)
    "autobriRecvErrors": 0,   # dedicated 4211 listener (#862)
    # #910 — MMW_TARGETS ingest health, surfaced beside the listener
    # health so a mis-bound or chattering radar node is operator-visible
    # on /api/status instead of silently dropped:
    "mmwUnbound": 0,     # datagrams whose sender resolved to no radar fixture
    "mmwMalformed": 0,   # structurally invalid MMW_TARGETS payloads
}
_udp_status_lock = threading.Lock()
_udp_listener_thread = None

def _udp_count_recv_error(key):
    """#901 — bump a per-listener receive-loop failure counter under the
    status lock. Called only from the recv loops' pre-existing
    `except Exception: continue` paths."""
    with _udp_status_lock:
        _udp_status[key] = _udp_status.get(key, 0) + 1

def get_udp_listener_status():
    """Snapshot of the UDP listener's bind state. JSON-safe; consumed by
    /api/status and the Setup-tab banner."""
    with _udp_status_lock:
        return dict(_udp_status)

def _try_bind_udp(port, max_attempts=5):
    """Attempt to bind UDP `port` with bounded backoff. Returns the bound
    socket on success, or None on terminal failure. Updates `_udp_status`
    on every attempt so the SPA can show the operator what's going on
    instead of staring at a silently-empty discover list (#771)."""
    backoff = [0.5, 1, 2, 4, 8]  # one entry per max_attempts
    for attempt in range(1, max_attempts + 1):
        with _udp_status_lock:
            _udp_status["port"] = port
            _udp_status["attemptedAt"] = time.time()
            _udp_status["attempts"] = attempt
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.settimeout(1.0)
            with _udp_status_lock:
                _udp_status["ok"] = True
                _udp_status["lastError"] = None
                _udp_status["boundAt"] = time.time()
            log.info("UDP listener bound to port %d on attempt %d", port, attempt)
            return s
        except OSError as e:
            with _udp_status_lock:
                _udp_status["ok"] = False
                _udp_status["lastError"] = str(e)
            if attempt < max_attempts:
                wait = backoff[min(attempt - 1, len(backoff) - 1)]
                log.warning("UDP listener bind attempt %d/%d on port %d "
                            "failed (%s) — retrying in %.1fs",
                            attempt, max_attempts, port, e, wait)
                time.sleep(wait)
            else:
                log.error("UDP listener bind to port %d FAILED after %d "
                          "attempts (last error: %s). Discover and PONG "
                          "flows will not work until the port is free. "
                          "On Windows: Stop-Service winnat -Force, then "
                          "POST /api/diagnostics/restart-udp-listener.",
                          port, max_attempts, e)
    return None

def restart_udp_listener():
    """Operator-triggered rebind. Spawns a fresh listener thread if the
    previous one bailed. Returns True iff the new bind succeeded.
    Used by the SPA's /api/diagnostics/restart-udp-listener route."""
    global _udp_listener_thread
    if _udp_status.get("ok"):
        # Already bound — nothing to do, but report the state so the SPA
        # can clear any stale "listener offline" banner.
        return True
    log.info("Operator requested UDP listener restart")
    t = threading.Thread(target=_udp_listener, daemon=True)
    _udp_listener_thread = t
    t.start()
    # Give the bind retry loop one slow attempt before we report back so
    # the API response can carry the new state instead of always returning
    # "still down" for the first call.
    time.sleep(0.6)
    return _udp_status.get("ok", False)

def _udp_autobri_listener():
    """#862 — dedicated UDP listener for `CMD_AUTOBRI_PUSH` on
    `UDP_AUTOBRI_PORT`. Separate from the firmware-shared 4210
    listener so that Android Auto Brightness still works even when
    Windows kernel reservations refuse to free 4210 (rare but real;
    the gyro controller handshake will be down in that case but Auto
    Brightness still arrives). Same packet format as the 4210 path
    — 8-byte header + 3-byte payload (master/flags/seq) — re-using
    `_handle_autobri_push` so the dispatch is single-source."""
    s = _try_bind_udp(UDP_AUTOBRI_PORT, max_attempts=3)
    if s is None:
        return
    while True:
        try:
            data, addr = s.recvfrom(64)
        except socket.timeout:
            continue
        except Exception:
            _udp_count_recv_error("autobriRecvErrors")
            continue
        if len(data) < 8:
            continue
        try:
            magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        except Exception:
            _udp_count_recv_error("autobriRecvErrors")
            continue
        if magic != UDP_MAGIC or ver not in (3, 4, UDP_VERSION):
            continue
        if cmd == CMD_AUTOBRI_PUSH and len(data) >= 11:
            _handle_autobri_push(addr[0], data)


def _udp_listener():
    """Background daemon: persistent bind on UDP_PORT. Receives every
    child→parent packet — ACTION_EVENT, PONG, the gyro 0x6x family, and
    CMD_AUTOBRI_PUSH on the legacy 4210 path — and routes it through the
    `_UDP_DISPATCH` table (#901). Per-command handling lives in the
    module-level `_handle_*` functions below; PONG matching and the #843
    globalBrightness top-up via _brightness_packet for a matched
    reconnecting child live in `_handle_pong`. Receive-loop failures
    bump `_udp_status["recvErrors"]` (#901 runtime-health note) without
    changing the swallow-and-continue behaviour."""
    s = _try_bind_udp(UDP_PORT)
    if s is None:
        return
    while True:
        try:
            data, addr = s.recvfrom(256)
        except socket.timeout:
            continue
        except Exception:
            _udp_count_recv_error("recvErrors")
            continue
        if len(data) < 8:
            continue
        try:
            magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
        except Exception:
            _udp_count_recv_error("recvErrors")
            continue
        # #819 — proto bumped 4→5 (CMD_GYRO_STOP added). Accept legacy v3/v4
        # frames so field-deployed children that haven't been re-flashed yet
        # still talk to the orchestrator.
        if magic != UDP_MAGIC or ver not in (3, 4, UDP_VERSION):
            continue
        ip = addr[0]
        entry = _UDP_DISPATCH.get(cmd)
        if entry is None or len(data) < entry[0]:
            # Unknown cmd — or a known cmd shorter than its pre-#901
            # `len(data) >= N` elif gate — takes exactly the old chain's
            # trailing-`else` silent-ignore debug log.
            log.debug("UDP cmd=0x%02X from %s (%d bytes)", cmd, ip, len(data))
            continue
        entry[1](ip, addr[1], (magic, ver, cmd), data)


# ── #901 UDP dispatch handlers ────────────────────────────────────────────────
# One module-level `_handle_<name>(ip, port, hdr, data)` per wire command,
# extracted verbatim from the pre-#901 `elif cmd ==` chain in _udp_listener
# (precedent: _handle_autobri_push / _handle_gyro_start_packet). `hdr` is
# the parsed (magic, ver, cmd) header triple; `data` is the FULL datagram,
# payload at offset 8 — struct offsets are unchanged from the wire.
# Each docstring carries its legacy dispatch line verbatim as documentation
# of the pre-#901 wire condition (the `and len(data) >= N` text documents
# the _UDP_DISPATCH minimum-length gate). Historical note: these lines were
# briefly load-bearing anchors for the source-inspection contract suites
# (test_819/825/867/869/872), which #920 migrated to
# inspect.getsource(_handle_*) — the docstrings are documentation only now.


def _handle_action_event(ip, port, hdr, data):
    """CMD_ACTION_EVENT — child action start/end event → _live_events.
    Pre-#901 dispatch line (load-bearing, see block note above):

        if cmd == CMD_ACTION_EVENT and len(data) >= 12:
    """
    at, si, tot, ev = struct.unpack_from("<BBBB", data, 8)
    _live_events[ip] = {
        "actionType": at, "stepIndex": si,
        "totalSteps": tot, "event": ev,
        "ts": time.time(),
    }
    log.debug("ACTION_EVENT from %s: type=%d step=%d/%d event=%s",
               ip, at, si, tot, "started" if ev == 0 else "ended")


def _handle_gyro_orient(ip, port, hdr, data):
    """CMD_GYRO_ORIENT — high-rate gyro pose stream → _gyro_state + Remote.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_ORIENT and len(data) >= 16:
    """
    # GyroOrientPayload: roll100(2) pitch100(2) yaw100(2) fps(1) flags(1)
    roll100, pitch100, yaw100, fps, flags = struct.unpack_from("<hhhBB", data, 8)

    # #819 — bit 3 of orient flags used to mean STOP in proto-v4
    # (gyro firmware ≤v1.2.5). That overload mis-fired on every
    # orient packet a v1.2.5 gyro sent, auto-releasing the live
    # claim ~25 ms after CMD_GYRO_START. Bit 3 is now reserved;
    # STOP moved to a discrete CMD_GYRO_STOP (0x69). We only warn
    # if the bit is observed and continue processing the orient —
    # never tear down the claim from this code path. (Acceptance
    # criterion: grepping desktop/shared/*.py for the old bitmask
    # literal must return nothing.)
    if ((flags >> 3) & 1):
        log.warning(
            "GYRO_ORIENT from %s with bit 3 set (flags=0x%02x) — "
            "protocol mismatch; expected CMD_GYRO_STOP. Ignoring "
            "the bit and processing orient. Reflash gyro firmware "
            "to v1.2.6+ to clear this warning.", ip, flags)
    with _gyro_lock:
        # #813 follow-up — merge instead of overwrite so the
        # battery-telemetry fields populated by CMD_GYRO_BATT
        # survive a subsequent orient packet.
        st = _gyro_state.setdefault(ip, {})
        st["roll"]  = roll100  / 100.0
        st["pitch"] = pitch100 / 100.0
        st["yaw"]   = yaw100   / 100.0
        st["fps"]   = fps
        st["flags"] = flags
        st["ts"]    = time.time()
    log.debug("GYRO_ORIENT from %s: R=%.1f P=%.1f Y=%.1f fps=%d",
              ip, roll100/100.0, pitch100/100.0, yaw100/100.0, fps)
    # Primitive owns orientation (#484 phase 4). Mover-follow
    # reads Remote.aim_stage via its tick loop — no legacy call
    # here any more.
    device_id = f"gyro-{ip}"
    # #822 — bump child.seen so Firmware Updates doesn't flag a
    # streaming gyro as offline. The gyro rarely sends CMD_PONG
    # in steady state; orient is the high-frequency liveness
    # signal.
    _touch_child_seen(ip)
    remote = _auto_register_remote(device_id, kind=KIND_GYRO)
    remote.update_from_euler_deg(
        roll100/100.0, pitch100/100.0, yaw100/100.0,
    )
    # #813 — orient handler is no longer a claim source.
    # Press-Start (`CMD_GYRO_START`) is the only path that
    # establishes a claim. An orient packet without a prior
    # claim updates the Remote's last_quat_world (which is
    # cheap + stale-clearing per #812) but never spontaneously
    # claims. The back-compat auto-claim-on-first-orient path
    # (#772 era) was deleted — green-field reflash assumed.


def _handle_gyro_stop(ip, port, hdr, data):
    """CMD_GYRO_STOP — press-STOP: release claim (blackout=False) + STOP_ACK.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_STOP:
    """
    # #819 — discrete press-STOP. #825 — payload now carries a
    # nonce(2). Header-only legacy variants (gyro firmware ≤
    # v1.2.6) are still accepted with nonce=0 and no STOP_ACK.
    #
    # #813 §1.2 / §6.1 — release(blackout=False). Press-Stop hands
    # the fixture back to whatever was driving it before the gyro
    # session (timeline / Track action / #800 park-at-home idle).
    # Pre-fix this forced dimmer-zero, fighting the next writer's
    # first frame; the operator's mental model is "release to what
    # it was doing before" (Android-controller-mode semantics).
    stop_nonce = None
    if len(data) >= 10:
        try:
            (stop_nonce,) = struct.unpack_from("<H", data, 8)
        except Exception:
            stop_nonce = None
    did_stop = f"gyro-{ip}"
    _gyro_touch_remote(did_stop)  # §6.3 silence-clock
    try:
        # Idempotent dedupe: a retransmitted STOP with the same
        # nonce just replays the STOP_ACK; we don't double-release.
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(did_stop, {})
            prev_nonce = st_hs.get("stop_nonce")
            prev_ts = st_hs.get("stop_ack_ts") or 0
            is_replay = (
                stop_nonce is not None
                and prev_nonce == stop_nonce
                and (time.time() - prev_ts) < GYRO_HANDSHAKE_DEDUPE_S
            )
        if is_replay:
            log.debug("GYRO_STOP replay from %s nonce=%d — re-sending ACK",
                      ip, stop_nonce)
            if stop_nonce is not None:
                _send_gyro_stop_ack(ip, stop_nonce)
            return
        log.info("GYRO_STOP from %s nonce=%s — releasing claim (blackout=False)",
                  ip, stop_nonce)
        if _mover_engine:
            gf_stop = _gyro_fixture_for_ip(ip)
            if gf_stop and gf_stop.get("assignedMoverId") is not None:
                _mover_engine.release(gf_stop["assignedMoverId"],
                                      did_stop, blackout=False)
        remote_stop = _remotes.by_device(did_stop)
        if remote_stop is not None:
            remote_stop.end_session()
            try:
                _remotes.save()
            except Exception as e:
                log.error("remotes.save() during stop failed: %s", e)
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(did_stop, {})
            # Drop nonce tracking for the gesture we just stopped.
            st_hs["start_nonce"] = None
            st_hs["mover_id"] = None
            if stop_nonce is not None:
                st_hs["stop_nonce"] = stop_nonce
                st_hs["stop_ack_ts"] = time.time()
        if stop_nonce is not None:
            _send_gyro_stop_ack(ip, stop_nonce)
    except Exception as e:
        log.error("GYRO_STOP handler failed: %s", e, exc_info=True)


def _handle_gyro_off(ip, port, hdr, data):
    """CMD_GYRO_OFF — press-OFF (#867).
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_OFF:
    """
    # #867 — discrete press-OFF: same payload shape and ACK as
    # CMD_GYRO_STOP but the server calls release(blackout=True)
    # so the claimed mover goes dark before the claim returns
    # to whatever was driving it before the gyro session. STOP
    # is "I'm done driving, hand control back at current
    # frame"; OFF is "I'm done driving AND turn the head off
    # right now." The gyro advances UI on matching STOP_ACK.
    off_nonce = None
    if len(data) >= 10:
        try:
            (off_nonce,) = struct.unpack_from("<H", data, 8)
        except Exception:
            off_nonce = None
    did_off = f"gyro-{ip}"
    _gyro_touch_remote(did_off)
    try:
        # Idempotent dedupe under the same handshake state as
        # STOP — nonce-collision across STOP/OFF is harmless
        # because both paths converge on STOP_ACK and the
        # claim is gone after either. Re-emit on ACK loss
        # just replays the cached ACK.
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(did_off, {})
            prev_nonce = st_hs.get("stop_nonce")
            prev_ts = st_hs.get("stop_ack_ts") or 0
            is_replay = (
                off_nonce is not None
                and prev_nonce == off_nonce
                and (time.time() - prev_ts) < GYRO_HANDSHAKE_DEDUPE_S
            )
        if is_replay:
            log.debug("GYRO_OFF replay from %s nonce=%d — re-sending ACK",
                      ip, off_nonce)
            if off_nonce is not None:
                _send_gyro_stop_ack(ip, off_nonce)
            return
        log.info("GYRO_OFF from %s nonce=%s — releasing claim (blackout=True)",
                  ip, off_nonce)
        if _mover_engine:
            gf_off = _gyro_fixture_for_ip(ip)
            if gf_off and gf_off.get("assignedMoverId") is not None:
                _mover_engine.release(gf_off["assignedMoverId"],
                                      did_off, blackout=True)
        remote_off = _remotes.by_device(did_off)
        if remote_off is not None:
            remote_off.end_session()
            try:
                _remotes.save()
            except Exception as e:
                log.error("remotes.save() during off failed: %s", e)
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(did_off, {})
            st_hs["start_nonce"] = None
            st_hs["mover_id"] = None
            if off_nonce is not None:
                st_hs["stop_nonce"] = off_nonce
                st_hs["stop_ack_ts"] = time.time()
        if off_nonce is not None:
            _send_gyro_stop_ack(ip, off_nonce)
    except Exception as e:
        log.error("GYRO_OFF handler failed: %s", e, exc_info=True)


def _handle_gyro_aim_wizard(ip, port, hdr, data):
    """CMD_GYRO_AIM_WIZARD — gyro-side empirical aim-axis wizard (#869).
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_AIM_WIZARD:
    """
    # #869 — gyro-side empirical aim-axis wizard. Same math
    # as the Android wizard (#826): three captured poses →
    # forward_local / up_local. Wire path differs from the
    # phone (which POSTs to /api/remotes/aim-wizard) because
    # the gyro has no HTTPS stack — captures ride one UDP
    # packet. Payload is 3 Euler triples in degrees:
    #   bytes 8..20  = neutral       (roll, pitch, yaw)
    #   bytes 20..32 = pitch_forward (roll, pitch, yaw)
    #   bytes 32..44 = yaw_left      (roll, pitch, yaw)
    # Server converts each triple to a body-to-world unit
    # quat via quat_from_euler_zyx_deg (the same convention
    # the gyro's orient stream uses) before dispatching to
    # _apply_aim_wizard_to_remote.
    if len(data) < 44:
        log.warning("GYRO_AIM_WIZARD from %s: payload too short "
                    "(%d bytes, expected 44)", ip, len(data))
        return
    try:
        from remote_math import quat_from_euler_zyx_deg as _qfe
        eu = struct.unpack_from("<9f", data, 8)
        poses = {
            "neutral":       _qfe(eu[0], eu[1], eu[2]),
            "pitch_forward": _qfe(eu[3], eu[4], eu[5]),
            "yaw_left":      _qfe(eu[6], eu[7], eu[8]),
        }
        did_wiz = f"gyro-{ip}"
        _gyro_touch_remote(did_wiz)
        r = _remotes.by_device(did_wiz)
        if r is None:
            r = _auto_register_remote(did_wiz, kind=KIND_GYRO)
        ok_, resp, status = _apply_aim_wizard_to_remote(r, poses)
        if ok_:
            _remotes.save()
            log.info("GYRO_AIM_WIZARD from %s — derived forward=%s up=%s",
                     ip, resp.get("forwardLocal"), resp.get("upLocal"))
        else:
            log.warning("GYRO_AIM_WIZARD from %s rejected: %s",
                         ip, resp)
    except Exception as e:
        log.error("GYRO_AIM_WIZARD handler failed: %s", e, exc_info=True)


def _handle_gyro_start(ip, port, hdr, data):
    """CMD_GYRO_START — press-START. Thin adapter: the #874 extraction
    `_handle_gyro_start_packet(ip, data)` predates the #901 uniform
    handler signature and is source-inspected directly by test_825,
    so it keeps its (ip, data) shape.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_START:
    """
    # #874 — extracted to a top-level callable for direct
    # contract testing. Behavior unchanged.
    _handle_gyro_start_packet(ip, data)


def _handle_gyro_batt(ip, port, hdr, data):
    """CMD_GYRO_BATT — battery telemetry → _gyro_state.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_BATT and len(data) >= 12:
    """
    # #813 follow-up — GyroBattPayload: vbat100(2) pct(1) flags(1).
    # Stamp into _gyro_state so /api/gyros and the SPA can surface
    # battery without operator intervention.
    _gyro_touch_remote(f"gyro-{ip}")  # §6.3 silence-clock
    vbat100, pct, bflags = struct.unpack_from("<HBB", data, 8)
    charging = bool(bflags & 0x01)
    with _gyro_lock:
        st = _gyro_state.setdefault(ip, {})
        st["vbat"] = vbat100 / 100.0
        st["batPct"] = (None if pct == 0xFF else int(pct))
        st["batCharging"] = charging
        st["batTs"] = time.time()
    log.debug("GYRO_BATT from %s: %.2fV pct=%s charging=%s",
              ip, vbat100/100.0, pct, charging)


def _handle_gyro_color(ip, port, hdr, data):
    """CMD_GYRO_COLOR — gyro colour-wheel pick → claimed mover.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_COLOR and len(data) >= 12:
    """
    # GyroColorPayload: r(1) g(1) b(1) flags(1)
    _gyro_touch_remote(f"gyro-{ip}")  # §6.3 silence-clock
    r, g, b, flags = struct.unpack_from("<BBBB", data, 8)
    flash = bool(flags & 0x01)
    log.info("GYRO_COLOR from %s: r=%d g=%d b=%d flash=%s", ip, r, g, b, flash)
    _apply_gyro_color(ip, r, g, b, flash)


def _handle_gyro_calibrate(ip, port, hdr, data):
    """CMD_GYRO_CALIBRATE — calibrate start/end + reference orientation.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_CALIBRATE and len(data) >= 15:
    """
    # GyroCalibratePayload: calibrating(1) roll100(2) pitch100(2) yaw100(2)
    calibrating, roll100, pitch100, yaw100 = struct.unpack_from("<Bhhh", data, 8)
    roll = roll100 / 100.0
    pitch = pitch100 / 100.0
    yaw = yaw100 / 100.0
    # #813 §6.3 — every gyro packet refreshes the all-comms-silence
    # clock. Calibrate is optional (#813 §5.0) so we never gate any
    # downstream behaviour on having seen one — but receiving one
    # is proof the gyro is alive.
    _gyro_touch_remote(f"gyro-{ip}")
    log.info("GYRO_CALIBRATE from %s: cal=%d R=%.1f P=%.1f Y=%.1f",
             ip, calibrating, roll, pitch, yaw)
    # Resolve the gyro fixture + target mover for this gyro.
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
            remote = _remotes.by_device(did) or _auto_register_remote(did, kind=KIND_GYRO)
            if mover is not None:
                aim_stage = _mover_current_aim_stage(mover)
                if aim_stage is None:
                    # #806 — UDP calibrate-end with no canonical aim;
                    # surface in the log so live-test can spot it. Skip
                    # the calibrate call instead of locking against a
                    # wrong vector (the #805 silent-fallback bug).
                    log.warning(
                        "Remote %d UDP calibrate skipped for mover %d: "
                        "aim_unresolvable (no canonical aim, sphere "
                        "read failed). Confirm Home/Secondary saved.",
                        remote.id, mover["id"])
                else:
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


def _handle_gyro_hb_rep(ip, port, hdr, data):
    """CMD_GYRO_HEARTBEAT_REP — diagnostics-only heartbeat reply (#872).
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_GYRO_HEARTBEAT_REP and len(data) >= 13:
    """
    # #872 — HB_REP is diagnostics-only.
    #
    # Pre-#872 this handler reconstructed the claim from the
    # gyro's heartbeat ("orchestrator-restart bootstrap path",
    # #813 §5.3) when the gyro reported ACTIVE while the
    # orchestrator had no claim. That path enabled the
    # operator-visible bug class in #872: SPA Release / press-
    # Stop was undone by the next 2 s heartbeat because the
    # gyro's UI was still ACTIVE and the reconstruct branch
    # could not distinguish "operator just released" from
    # "orchestrator just restarted".
    #
    # Operator's contract (2026-05-09): "Once Start is pressed,
    # when we lock and hold." Press-Start is the SOLE
    # orchestrator-side claim entry trigger. No auto-reclaim,
    # no bootstrap. After an orchestrator restart, the
    # operator presses Start again. See `docs/gyro-claim-
    # lifecycle.md` §5.3 + §7.2.
    #
    # This handler may: parse the packet, log it, update
    # `Remote.last_data`. It MUST NOT call `_mover_engine.claim`,
    # `_mover_engine.release`, `start_stream`, `_send_gyro_*` —
    # the spec invariant is enforced here, not at the call
    # site of every future patch.
    ui_state, claim_nonce, hb_seq = struct.unpack_from("<BHH", data, 8)
    device_id_hb = f"gyro-{ip}"
    _gyro_touch_remote(device_id_hb)
    try:
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(device_id_hb, {})
            last_seq = st_hs.get("last_seen_seq")
            st_hs["last_seen_seq"] = hb_seq
        if last_seq != hb_seq:
            # Diagnostics-only log. `server_has_claim` divergence
            # used to drive the reconstruct branch; now it just
            # informs the log so cross-check between the gyro's
            # view and the orchestrator's view is observable.
            server_has_claim = (
                _mover_engine is not None
                and any(cl.get("deviceId") == device_id_hb
                        for cl in (_mover_engine.get_status() or [])))
            log.debug(
                "GYRO_HB_REP from %s ui=%d claimNonce=%d seq=%d "
                "(server_has_claim=%s, diagnostics-only)",
                ip, ui_state, claim_nonce, hb_seq, server_has_claim)
    except Exception as e:
        log.error("GYRO_HB_REP handler failed: %s", e, exc_info=True)


def _handle_autobri_push_udp(ip, port, hdr, data):
    """CMD_AUTOBRI_PUSH — legacy 4210 path. Thin adapter: the #861
    extraction `_handle_autobri_push(ip, data)` predates the #901
    uniform signature and is shared with the dedicated 4211 listener
    (#862) and the local-audio bridge, so it keeps its (ip, data)
    shape.
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_AUTOBRI_PUSH and len(data) >= 11:
    """
    _handle_autobri_push(ip, data)


def _handle_pong(ip, port, hdr, data):
    """CMD_PONG — broadcast/direct ping replies: discovery + child
    liveness. A reconnecting LED child below the current master gets
    a globalBrightness top-up via _brightness_packet once matched
    (#843).
    Pre-#901 dispatch line (load-bearing, see block note above):

        elif cmd == CMD_PONG:
    """
    # Handle PONGs from broadcast/direct pings
    info = _parse_pong(data, ip)
    if info:
        log.debug("PONG from %s (%s) fw=%s", ip, info.get("hostname"), info.get("fwVersion"))
        # Store for discover to find
        _recent_pongs[ip] = info
        # Update known children
        matched = None
        for c in _children:
            if c.get("ip") == ip or c.get("hostname") == info.get("hostname"):
                saved_fw = c.get("fwVersion", "")
                c.update({k: v for k, v in info.items() if k != "id"})
                # Preserve 3-digit version over PONG's 2-digit
                if saved_fw and saved_fw.count(".") >= 2 and info.get("fwVersion", "").count(".") < 2:
                    c["fwVersion"] = saved_fw
                # #822 — bump seen so Firmware Updates tab
                # doesn't show this child as offline. The
                # parsed PongPayload doesn't include `seen`,
                # so the c.update(info) above doesn't refresh
                # it; do it explicitly here.
                c["seen"] = int(time.time())
                c["status"] = 1
                _probe_board_type(c)
                matched = c
                break
        # #843 — top up brightness on a freshly-online child.
        # `childBrightness` boots to 255 in firmware (Child.cpp).
        # If our master is currently below that, push the value
        # so the child doesn't display its first show frame at
        # full intensity. LED children only.
        if matched is not None and matched.get("type") not in ("dmx", "gyro"):
            g_bri = _settings.get("globalBrightness", 255)
            if g_bri < 255:
                _send(ip, _brightness_packet(g_bri))


# ── #910 MMW_TARGETS ingest ──────────────────────────────────────────────────
# Log-once guards so an unbound/malformed radar node chattering at 25 Hz
# can't flood the log; the running counters live in _udp_status
# ("mmwUnbound"/"mmwMalformed") → get_udp_listener_status() → /api/status,
# beside the #901 listener health.
_mmw_logged = set()   # (kind, ip) pairs already logged
_MMW_TARGET_SLOTS = 3  # Rd-03D hard limit (mmwave/MmwProtocol.h MMW_MAX_TARGETS)


def _mmw_log_once(kind, ip, msg, *args):
    if (kind, ip) not in _mmw_logged:
        _mmw_logged.add((kind, ip))
        log.warning(msg, *args)


def _mmw_sender_hostname(ip):
    """Hostname the node at `ip` announced — the PONG discovery record
    (boot-time broadcast self-announce, design doc §4.3) or the persisted
    child record. None if this sender never announced."""
    info = _recent_pongs.get(ip)
    if info and info.get("hostname"):
        return info["hostname"]
    child = next((c for c in _children if c.get("ip") == ip), None)
    return child.get("hostname") if child else None


def _mmw_fixture_for_sender(ip):
    """Resolve a 0x70 sender to its radar fixture (#910 source identity).

    A radar fixture matches when its `radarNode` equals the hostname the
    node announced (a match on a DISABLED fixture means the operator
    turned that radar off — its packets are dropped, never re-bound
    elsewhere). Fallback: iff exactly ONE enabled radar fixture exists
    and the hostname matched nothing, bind to it (logged once) — a fresh
    single-radar setup works before the operator fills in radarNode.
    Multiple candidates → None: never guess which wedge a packet
    belongs to."""
    radars = [f for f in _fixtures if f.get("fixtureType") == "radar"]
    enabled = [f for f in radars if f.get("radarEnabled", True)]
    if not enabled:
        return None
    host = _mmw_sender_hostname(ip)
    if host:
        h = host.strip().lower()
        for f in radars:
            node = f.get("radarNode")
            if isinstance(node, str) and node.strip().lower() == h:
                return f if f.get("radarEnabled", True) else None
    if len(enabled) == 1:
        _mmw_log_once("bind", ip,
                      "MMW_TARGETS from %s (hostname=%s) matched no "
                      "radarNode — binding to the only enabled radar "
                      "fixture %s (#910 single-radar heuristic)",
                      ip, host, enabled[0].get("id"))
        return enabled[0]
    return None


def _handle_mmw_targets(ip, port, hdr, data):
    """CMD_MMW_TARGETS — MMwave radar tracked-target frame → radar_fusion
    (#910/#912). Landed as exactly the one-line registration the #901
    dispatch-table note promised:

        CMD_MMW_TARGETS: (36, _handle_mmw_targets),

    Payload (MmwTargetsPayload, mmwave/MmwProtocol.h is the wire source
    of truth; 8-byte header + 28 = 36-byte gate): seq(u16 LE) count(u8)
    flags(u8, bit0 = radar parse healthy) + 3 × {xMm i16, yMm i16,
    speedCms i16, resMm u16}, fixed slots, unused zeroed. Coordinates
    are sensor-frame mm; ALL projection to stage space happens server-
    side in radar_fusion (design doc §4.4 — node dumb, orchestrator
    smart). count=0 keepalives (1 Hz) still feed the tracker: they
    advance track coasting and expiry. Runs on the UDP listener thread —
    radar_fusion locks its own state; no global _lock on this hot path
    (the temporal-object sinks lock internally, #912).
    """
    seq, count, flags = struct.unpack_from("<HBB", data, 8)
    if count > _MMW_TARGET_SLOTS:
        _udp_count_recv_error("mmwMalformed")
        _mmw_log_once("malformed", ip,
                      "MMW_TARGETS from %s: count=%d exceeds the Rd-03D "
                      "%d-target limit — dropping (logged once)",
                      ip, count, _MMW_TARGET_SLOTS)
        return
    fixture = _mmw_fixture_for_sender(ip)
    if fixture is None:
        _udp_count_recv_error("mmwUnbound")
        _mmw_log_once("unbound", ip,
                      "MMW_TARGETS from %s (hostname=%s) matches no enabled "
                      "radar fixture — counting in mmwUnbound (logged once). "
                      "Set the fixture's radarNode to bind it.",
                      ip, _mmw_sender_hostname(ip))
        return
    targets = [struct.unpack_from("<hhhH", data, 12 + 8 * i)
               for i in range(count)]
    # Pose snapshot: fixture record + layout position — the same stores
    # the camera projection reads (#586/#600 conventions; radar_fusion
    # decodes rotation via rotation_from_layout only).
    pos = next((p for p in _layout.get("children", [])
                if p.get("id") == fixture.get("id")), {})
    pose = {
        "id": fixture.get("id"),
        "x": pos.get("x", 0) or 0,
        "y": pos.get("y", 0) or 0,
        "z": pos.get("z", 0) or 0,
        "rotation": fixture.get("rotation") or [0, 0, 0],
    }
    node = _mmw_sender_hostname(ip) or fixture.get("radarNode")
    try:
        _radar_fusion.ingest(pose, node, seq, flags, targets, time.monotonic())
    except Exception as e:
        # Never let a bad frame take down the shared 4210 listener thread.
        log.error("MMW_TARGETS ingest failed for %s: %s", ip, e, exc_info=True)
        return
    log.debug("MMW_TARGETS from %s: seq=%d count=%d flags=0x%02x → fixture %s",
              ip, seq, count, flags, fixture.get("id"))


def _handle_ota_status(ip, port, hdr, data):
    """CMD_OTA_STATUS — child OTA progress/result report → _ota_status_live
    (#922). Landed as exactly the one-line registration the #901
    dispatch-table note promised:

        CMD_OTA_STATUS: (10, _handle_ota_status),

    Payload (OtaStatusPayload, main/Protocol.h; 8-byte header + 2 =
    10-byte gate): status(u8, OTA_STATUS_* codes per main/OtaUpdate.h) +
    progress(u8, 0-100%). Fire-and-forget from the updating board (ESP32 /
    D1 Mini / gyro / DMX bridge / mmwave node) on every phase change plus
    every ≥10% of download. Keyed by sender IP; /api/firmware/check
    (orch_firmware) joins it onto its per-child rows by `ip`. Also bumps
    child.seen (#822) — a mid-OTA board stops answering PING while it
    downloads, and flagging a child offline while it is actively
    reporting OTA progress would be wrong.
    """
    status, progress = struct.unpack_from("<BB", data, 8)
    _ota_status_live[ip] = {
        "status": status,
        "statusName": _OTA_STATUS_NAMES.get(status, f"unknown({status})"),
        "progress": progress,
        "updatedAt": time.time(),
    }
    _touch_child_seen(ip)
    log.debug("OTA_STATUS from %s: %s (%d%%)", ip,
              _OTA_STATUS_NAMES.get(status, status), progress)


# #901 — UDP 4210 dispatch: {cmd: (min_total_datagram_len, handler)}.
# The minimum length mirrors the pre-#901 `elif cmd == X and len(data) >= N`
# gates exactly; a known cmd arriving shorter than its gate falls through to
# the same debug-log-and-ignore path as an unknown cmd (the old trailing
# `else`). All commands share the single header version gate in
# _udp_listener (v3/v4/v5 accepted) — no per-command version windows existed
# pre-#901 and none are added here.
# Adding a new command is a one-line registration — #910's 0x70
# MMW_TARGETS below landed as exactly that. (0x71 MMW_CONFIG is
# parent→node and reserved — nothing to dispatch.)
_UDP_DISPATCH = {
    CMD_ACTION_EVENT: (12, _handle_action_event),
    CMD_GYRO_ORIENT: (16, _handle_gyro_orient),
    CMD_GYRO_STOP: (8, _handle_gyro_stop),
    CMD_GYRO_OFF: (8, _handle_gyro_off),
    CMD_GYRO_AIM_WIZARD: (8, _handle_gyro_aim_wizard),
    CMD_GYRO_START: (8, _handle_gyro_start),
    CMD_GYRO_BATT: (12, _handle_gyro_batt),
    CMD_GYRO_COLOR: (12, _handle_gyro_color),
    CMD_GYRO_CALIBRATE: (15, _handle_gyro_calibrate),
    CMD_GYRO_HEARTBEAT_REP: (13, _handle_gyro_hb_rep),
    CMD_AUTOBRI_PUSH: (11, _handle_autobri_push_udp),
    CMD_PONG: (8, _handle_pong),
    CMD_OTA_STATUS: (10, _handle_ota_status),
    CMD_MMW_TARGETS: (36, _handle_mmw_targets),
}


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

def _handle_gyro_start_packet(ip, data):
    """Top-level CMD_GYRO_START dispatch.

    Extracted from `_udp_listener`'s in-line `elif cmd ==
    CMD_GYRO_START:` branch (#874) so the contract is directly
    testable — drive a synthetic packet at this function and assert
    the wire output (`_send_gyro_claim_ack` / `_send_gyro_claim_denied`)
    + claim-store side effects.

    Behavior unchanged from the pre-#874 in-line dispatch. See
    `docs/gyro-claim-lifecycle.md` §3.3 / §3.6 / §4.2 for the full
    handshake spec the body implements."""
    # #813 §4 — explicit press-START. Server-side handshake:
    # resolve fixture+mover, claim, start stream, lights on
    # (§4.2 step 6), send CLAIM_ACK (idempotent retransmission
    # protected by the nonce dedupe window — §3.2). No arm-check
    # timer is scheduled; the claim lives until press-Stop /
    # Inactive / 600 s all-comms-silence (§6.1 / §6.2 / §6.3).
    gf = _gyro_fixture_for_ip(ip)
    target_mover_id = gf.get("assignedMoverId") if gf else None
    device_id = f"gyro-{ip}"
    _gyro_touch_remote(device_id)  # §6.3 silence-clock
    start_nonce = None
    if len(data) >= 10:
        try:
            (start_nonce,) = struct.unpack_from("<H", data, 8)
        except Exception:
            start_nonce = None

    # Idempotent retransmission: same nonce within the dedupe
    # window just replays the cached response (ACK or DENIED) —
    # no second claim attempt. #872: replays carry the original
    # reason so the gyro renders the same message it would have
    # rendered for the first DENIED.
    with _gyro_handshake_lock:
        st_hs = _gyro_handshake.setdefault(device_id, {})
        prev_nonce = st_hs.get("start_nonce")
        prev_resp = st_hs.get("start_response")
        prev_reason = st_hs.get("start_response_reason",
                                GYRO_DENIED_IDLE)
        prev_ts = st_hs.get("ack_sent_ts") or 0
        is_replay = (
            start_nonce is not None
            and prev_nonce == start_nonce
            and prev_resp is not None
            and (time.time() - prev_ts) < GYRO_HANDSHAKE_DEDUPE_S
        )
        cached_mover = st_hs.get("mover_id")
    if is_replay:
        log.debug("GYRO_START replay from %s nonce=%d resp=%s reason=%s",
                  ip, start_nonce, prev_resp, prev_reason)
        if prev_resp == "ack" and cached_mover is not None:
            _send_gyro_claim_ack(ip, start_nonce, cached_mover)
        else:
            _send_gyro_claim_denied(ip, prev_reason)
        return

    def _record_denied(reason):
        """Send DENIED with `reason` and cache it so a same-nonce
        retransmission replays the same reason (#872 §3.6)."""
        _send_gyro_claim_denied(ip, reason)
        with _gyro_handshake_lock:
            st_hs2 = _gyro_handshake.setdefault(device_id, {})
            st_hs2["start_nonce"] = start_nonce
            st_hs2["start_response"] = "denied"
            st_hs2["start_response_reason"] = reason
            st_hs2["ack_sent_ts"] = time.time()

    # #801 / #872 — refuse GYRO_START when the controller is
    # Inactive at the orchestrator level. Reason 1 lets the gyro
    # render "Gyro is disabled — enable in Setup" instead of the
    # legacy ambiguous "Mover held by other".
    if gf is not None and not gf.get("gyroEnabled"):
        log.info("GYRO_START from %s nonce=%s — controller Inactive, refusing",
                  ip, start_nonce)
        _record_denied(GYRO_DENIED_CONTROLLER_INACTIVE)
        return
    if target_mover_id is None:
        # #872 — was silent pre-fix; gyro timed out without an
        # actionable error. Now sends DENIED with reason 3 so the
        # gyro renders "No moving head assigned".
        log.info("GYRO_START from %s — no assigned mover, refusing", ip)
        _record_denied(GYRO_DENIED_NO_MOVER_ASSIGNED)
        return
    if _mover_engine is None:
        # #872 — was silent pre-fix; now reason 4.
        log.warning("GYRO_START from %s — mover engine not available", ip)
        _record_denied(GYRO_DENIED_ENGINE_UNAVAILABLE)
        return

    # #823 — press-Start IS the operator's "I'm using this remote
    # now" gesture; clear any hard-stale flag on the matching Remote
    # BEFORE the claim lands. Without this, the engine tick auto-
    # releases the brand-new claim on its very next iteration
    # (~25 ms later) when the Remote still carries
    # `stale_reason="session-ended"` from the prior press-Stop. Same
    # architectural argument as #812 cleared `connection-lost` on
    # resume — except press-Start is more explicit and shouldn't
    # wait for an orient packet to do the clearing.
    remote_pre = _remotes.by_device(device_id)
    if remote_pre is not None and remote_pre.stale_reason is not None:
        log.info("GYRO_START from %s — clearing stale_reason=%s",
                 ip, remote_pre.stale_reason)
        remote_pre.clear_stale()
    dname = _gyro_device_name(ip, gf)
    ok, reason = _mover_engine.claim(target_mover_id, device_id,
                                     dname, "gyro")
    if not ok:
        log.info("GYRO_START from %s nonce=%s — claim DENIED (%s)",
                  ip, start_nonce, reason)
        _record_denied(GYRO_DENIED_ALREADY_CLAIMED)
        return
    _mover_engine.start_stream(target_mover_id, device_id)
    # #813 §1.1 / §4.2 step 6 — turn the lights on. Press-Start is
    # the moment the head should visibly come alive; pre-fix the
    # operator had to hold-to-calibrate before any output was produced.
    _gyro_lights_on(target_mover_id)
    if start_nonce is not None:
        _send_gyro_claim_ack(ip, start_nonce, target_mover_id)
        # Fire an immediate heartbeat so the gyro has something
        # to reply to (HB_REP) right away.
        _send_gyro_heartbeat(ip)
        # Cache the nonce + response for idempotent START
        # retransmissions (#813 §3.2). No arm-check timer is
        # scheduled — the claim lives until §6.1 / §6.2 / §6.3
        # fires, never on a speculative deadline.
        with _gyro_handshake_lock:
            st_hs = _gyro_handshake.setdefault(device_id, {})
            st_hs["start_nonce"] = start_nonce
            st_hs["start_response"] = "ack"
            st_hs["ack_sent_ts"] = time.time()
            st_hs["mover_id"] = target_mover_id
        log.info("GYRO_START from %s nonce=%d — claim+start_stream ok "
                  "mover=%d (lights on, ACK + initial HB sent)",
                  ip, start_nonce, target_mover_id)
    else:
        # Legacy gyro (firmware ≤ v1.2.6, no nonce). Pre-#825
        # behaviour: silent success, no ACK. The gyro advances UI
        # on absence-of-DENIED; the orphan-claim risk is exactly
        # what #825 fixes for new firmware, but back-compat keeps
        # the old gyro working.
        log.info("GYRO_START from %s (legacy, no nonce) — "
                  "claim+start_stream ok mover=%d",
                  ip, target_mover_id)


def _send_gyro_claim_denied(ip, reason=GYRO_DENIED_IDLE):
    """#772 / #872 — fire-and-forget CMD_GYRO_CLAIM_DENIED to a gyro
    whose START we just refused. Payload is 1 byte: a reason code from
    `GYRO_DENIED_*`. Firmware ≥ v1.2.11 reads it to render an
    actionable message; older firmware ignores the byte and falls back
    to the legacy "BUSY / Mover held by other" string. Reason 0
    (`GYRO_DENIED_IDLE`) is the safe default — it preserves legacy
    rendering when the call site hasn't been updated."""
    try:
        pkt = _hdr(CMD_GYRO_CLAIM_DENIED) + struct.pack("<B", int(reason) & 0xFF)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(pkt, (ip, UDP_PORT))
        sock.close()
    except Exception as e:
        log.debug("claim-denied to %s failed: %s", ip, e)


# ── #825 — rock-solid press-Start/Stop handshake ─────────────────────────────
#
# `_gyro_handshake` tracks per-device handshake state so:
#
#   1. duplicate START packets carrying the same nonce are idempotent —
#      we replay the cached response (ACK or DENIED) without re-running
#      claim/start_stream;
#   2. an arm-check timer fires 1.5 s after CLAIM_ACK is sent — if no
#      orient packet has arrived from the device by then, we assume the
#      ACK was lost and release the claim so it doesn't orphan.
#
# Per-device entry shape (see docs/gyro-claim-lifecycle.md §3.2):
#   {
#     "start_nonce":     uint16 | None,   # nonce of last START seen
#     "start_response":  "ack" | "denied" | None,
#     "ack_sent_ts":     float | None,    # ts of last ACK / DENIED send
#     "mover_id":        int | None,      # mover bound to the active claim
#     "stop_nonce":      uint16 | None,
#     "stop_ack_ts":     float | None,
#     "last_seen_seq":   uint16 | None,   # last HB_REP seq we've processed
#   }
#
# No arm-check timer is tracked — the spec (docs/gyro-claim-lifecycle.md
# §7.1) explicitly forbids speculative release timers. Claim survives
# until press-Stop (§6.1), Inactive toggle (§6.2), or 600 s of all-comms
# silence (§6.3); nothing else.
_gyro_handshake = {}
_gyro_handshake_lock = threading.Lock()

# Window (seconds) during which a duplicate START/STOP nonce replays the
# cached response instead of running claim/release. Tuned to comfortably
# cover the gyro's retry budget (5 retries × 150 ms = 750 ms).
GYRO_HANDSHAKE_DEDUPE_S = 5.0


def _send_gyro_claim_ack(ip, nonce, mover_id):
    """#825 — confirm a successful press-Start to the gyro.

    Payload: nonce(2) + moverId(2). The gyro matches `nonce` against the
    START it sent and advances WAITING_ACK → ACTIVE. `moverId` lets it
    sanity-check it's been bound to the mover it's expecting.
    """
    try:
        pkt = _hdr(CMD_GYRO_CLAIM_ACK) + struct.pack("<HH",
                                                      int(nonce) & 0xFFFF,
                                                      int(mover_id) & 0xFFFF)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(pkt, (ip, UDP_PORT))
    except Exception as e:
        log.debug("claim-ack to %s failed: %s", ip, e)


def _send_gyro_stop_ack(ip, nonce):
    """#825 — confirm a successful press-Stop to the gyro."""
    try:
        pkt = _hdr(CMD_GYRO_STOP_ACK) + struct.pack("<H", int(nonce) & 0xFFFF)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(pkt, (ip, UDP_PORT))
    except Exception as e:
        log.debug("stop-ack to %s failed: %s", ip, e)


def _touch_child_seen(ip):
    """#822 — refresh `_children[*].seen` for a child matching `ip`.

    The Firmware Updates tab uses `seen` (last-heard timestamp) to flag
    children as offline / stale (`CHILD_STALE_S`). Pre-fix:
      - The CMD_PONG handler called `c.update(info)` with the parsed
        PongPayload, which doesn't include a `seen` key — so `seen` was
        never bumped on the discovery / ping cycle.
      - The gyro controller rarely sends CMD_PONG (only on boot broadcast); its
        steady-state traffic is CMD_GYRO_ORIENT / BATT / HEARTBEAT_REP,
        none of which touched `_children`. A perfectly online gyro
        therefore showed as offline in the Firmware tab.
      - The Giga DMX bridge sends ArtPollReply via the dmx_artnet engine,
        which doesn't share state with `_children`. Same outcome.

    This helper is the single point that updates `seen` whenever any
    incoming packet establishes liveness. Called from the CMD_PONG
    branch (fixes DMX bridge) and from CMD_GYRO_ORIENT (fixes gyro).
    """
    if not ip:
        return
    now = int(time.time())
    for c in _children:
        if c.get("ip") == ip:
            c["seen"] = now
            if c.get("status") == 0:
                c["status"] = 1
            break


def _gyro_touch_remote(device_id):
    """#813 §6.3 — refresh the all-comms-silence clock for a gyro device.
    Called from every gyro CMD branch in the UDP listener (orient, color,
    calibrate, heartbeat-rep, batt, start, stop). The 600 s stale-comms
    threshold (`STALE_HARD_SECS`) measures silence on ANY incoming packet
    from this device, not just orient — see docs/gyro-claim-lifecycle.md
    §6.3. Without this, a gyro legitimately holding a still pose (no
    orient updates) but otherwise alive would still trip the silence
    fallback, contradicting #813's rock-solid principle.

    Auto-registers the Remote on first call so packets that arrive
    before any orient (BATT / HEARTBEAT_REP from an IDLE gyro) still
    establish a tracked entity.
    """
    try:
        remote = _remotes.by_device(device_id) or _auto_register_remote(
            device_id, kind=KIND_GYRO)
        if remote is not None:
            remote.last_data = time.time()
            if remote.stale_reason == "connection-lost":
                # #812 — auto-clear hard-stale when the gyro comes back.
                remote.stale_reason = None
                remote.soft_stale = False
    except Exception as e:
        log.debug("touch_remote(%s) failed: %s", device_id, e)


def _gyro_lights_on(mover_id):
    """#813 §1.1 / §4.2 step 6 — turn the moving head on at claim time.
    Press-Start should produce visible output immediately; the operator
    must NOT have to perform a calibrate gesture to see the lamp light.

    Per #814, colour is INHERITED from whatever was driving the wire
    before the claim arrived (Track action / scene / SET_BRIGHTNESS) —
    we never slam to white. This function only nudges the dimmer to
    `lampOnDimmer` IF the wire was dark (dimmer=0) and applies non-
    R/G/B/dimmer profile defaults so beam-shaping channels (strobe
    open, gobo open, prism off, …) produce a clean visible beam when
    the fixture is coming up from a cold/idle state.

    For fixtures already lit by an upstream writer (Track action
    coloured red, mid-show), this is effectively a no-op on colour:
    the existing red survives the claim transition.
    """
    try:
        mover = _mover_fixture(mover_id)
        if mover is None:
            return
        engine = _artnet if _dmx_settings.get("protocol", "artnet") == "artnet" else _sacn
        if engine is None or not engine.running:
            return
        pid = mover.get("dmxProfileId")
        prof_info = _profile_lib.channel_info(pid) if pid else None
        if prof_info is None:
            return
        addr = mover.get("dmxStartAddr", 1)
        uni = engine.get_universe(mover.get("dmxUniverse", 1))
        profile = {"channel_map": prof_info.get("channel_map"),
                   "channels": prof_info.get("channels", [])}
        ch_map = prof_info.get("channel_map") or {}
        # Read current dimmer from the universe buffer; only force
        # `lampOnDimmer` when the fixture is currently dark. If a
        # show is already driving the head with dimmer up, leave it.
        dim_off = ch_map.get("dimmer")
        cur_dim = uni.get_channel(addr + dim_off) if dim_off is not None else 0
        if cur_dim == 0:
            dim = int(mover.get("lampOnDimmer", 255) or 255)
            uni.set_fixture_dimmer(addr, max(0, min(255, dim)), profile)
        # #848 invariant 1 — default RGB / wheel-slot when the wire is
        # currently dark so the operator sees the head light up
        # immediately on press-Start. Pre-fix only the dimmer was
        # written; if the wire was at RGB=(0,0,0) and the profile had
        # no master dimmer (or the master was already up), the head
        # stayed black-commanded and the operator's "no lights came
        # on" report was the result. #814's "inherit existing colour"
        # rule is preserved by gating on a darkness check — a show
        # already driving non-zero RGB is not clobbered.
        # `set_fixture_rgb` handles RGB / hybrid / wheel-only dispatch
        # per the #842 centralization, so a 12-ch wheel-only mover
        # like movinghead-150w-12ch picks the closest white slot.
        r_off = ch_map.get("red")
        g_off = ch_map.get("green")
        b_off = ch_map.get("blue")
        cw_off = ch_map.get("color-wheel")
        cur_r = uni.get_channel(addr + r_off) if r_off is not None else 0
        cur_g = uni.get_channel(addr + g_off) if g_off is not None else 0
        cur_b = uni.get_channel(addr + b_off) if b_off is not None else 0
        cur_wheel = uni.get_channel(addr + cw_off) if cw_off is not None else 0
        # Dark = RGB sum < 30 AND no explicit wheel-slot pick. The
        # threshold lets a faint Track-action base wash count as "lit"
        # so we don't clobber it; cur_wheel != 0 means an upstream
        # writer already picked a slot, also leave alone.
        is_dark = (cur_r + cur_g + cur_b) < 30 and cur_wheel == 0
        if is_dark:
            # Operator-tunable per fixture (`lampOnR/G/B` on the
            # fixture record); default white.
            default_r = int(mover.get("lampOnR", 255) or 255)
            default_g = int(mover.get("lampOnG", 255) or 255)
            default_b = int(mover.get("lampOnB", 255) or 255)
            uni.set_fixture_rgb(
                addr,
                max(0, min(255, default_r)),
                max(0, min(255, default_g)),
                max(0, min(255, default_b)),
                profile,
            )
        # Apply non-pan/tilt/dimmer/RGB/colour-wheel defaults so beam-
        # shaping channels (strobe open, gobo open, prism off, focus
        # mid…) produce a clean visible beam. Pan/tilt are driven live
        # by the engine tick from Remote.aim_stage; RGB / colour-wheel
        # are handled by the #848 default-RGB block above.
        skip = {"pan", "tilt", "dimmer", "red", "green", "blue", "color-wheel"}
        for ch in prof_info.get("channels", []):
            ch_type = ch.get("type", "")
            default = ch.get("default")
            if default is not None and ch_type not in skip:
                uni.set_channel(addr + ch.get("offset", 0), int(default))
    except Exception as e:
        log.debug("lights_on(mover=%s) failed: %s", mover_id, e)


def _send_gyro_heartbeat(ip, state="streaming"):
    """#825 — single CMD_GYRO_HEARTBEAT to one gyro. Extracted from
    `_heartbeat_loop` so the START success path can fire an immediate
    heartbeat right after CLAIM_ACK (#813 §3.3 packet diagram). Cheap
    (~10 byte UDP); idempotent."""
    try:
        state_byte = 1 if state == "streaming" else 0
        pkt = _hdr(CMD_GYRO_HEARTBEAT) + bytes([state_byte, 1])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(pkt, (ip, UDP_PORT))
        sock.close()
    except Exception as e:
        log.debug("heartbeat to %s failed: %s", ip, e)


def _heartbeat_loop():
    """#476 — Emit CMD_GYRO_HEARTBEAT to every gyro with an active claim.

    Runs every 2 s. The gyro treats the heartbeat as "parent is alive and
    still holds your claim"; if the gyro doesn't hear one for >5 s it
    shows "RECON", and >20 s it drops back to IDLE. Silence is symmetric
    with the consumer-side auto-release: server times out at 60 s, gyro
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
            _send_gyro_heartbeat(ip, claim.get("state") or "streaming")
        time.sleep(2.0)


def start_background_tasks():
    """Call once after import to kick off periodic ping and UDP listener threads."""
    global _startup_check_done, _udp_listener_thread
    _bootstrap_ssh_defaults()
    _udp_listener_thread = threading.Thread(target=_udp_listener, daemon=True)
    _udp_listener_thread.start()
    # #862 — second UDP listener for AUTOBRI_PUSH on its own port.
    threading.Thread(target=_udp_autobri_listener, daemon=True).start()
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    if _children:
        threading.Thread(target=_periodic_ping, daemon=True).start()
    else:
        _startup_check_done = True
    _check_depth_install_marker()
    _check_ollama_install_marker()
    # Boot-time warm-up of any AI helper that's already installed.
    # Runs in a background thread so HTTP comes up immediately even when
    # ZoeDepth takes 10–30 s to load weights into RAM. Helpers that are
    # mid-download show installing=true via /api/ai/status; warmup just
    # skips them and the operator can press Test once the install finishes.
    threading.Thread(target=_ai_helpers_warmup, daemon=True).start()
    # #879 — initialise local-audio-brightness so a persisted-enabled
    # config auto-resumes capture after orchestrator restart. Lazy: the
    # init only does work if `localAudioBrightness.enabled` is True on
    # disk; otherwise the instance is created but quiescent.
    try:
        _init_local_audio_bri()
    except Exception as e:
        log.info("LocalAudioBrightness startup init failed: %s", e)
    # No auto-claim on boot. The UDP CMD_GYRO_ORIENT handler auto-claims
    # on the first orient packet from an enabled gyro fixture, which is
    # the operator pressing Start on the gyro. That's what turns the
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
        return jsonify(ok=True, id=existing["id"], duplicate=True,
                       type=existing.get("type", "slyled"),
                       boardType=existing.get("boardType", ""),
                       name=existing.get("name", ""),
                       hostname=existing.get("hostname", ""),
                       ip=ip)
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

@app.post("/api/children/<int:cid>/find")
def api_children_find(cid):
    """#291 — broadcast-search for a single performer by its (stable, MAC-
    derived) hostname. Used when DHCP rotated the IP and the device shows
    Offline. Returns ``found=true`` + the new IP when a PONG arrives whose
    hostname matches; updates the stored child record in place."""
    child = next((c for c in _children if c["id"] == cid), None)
    if not child:
        abort(404)
    target_host = child.get("hostname") or ""
    if not target_host:
        return jsonify(ok=False, err="child has no hostname to match"), 400
    # Direct ping last-known IP first (cheap; succeeds when device is up
    # but the listener hasn't seen it lately).
    if _ping(child):
        with _lock:
            _save("children", _children)
        return jsonify(ok=True, found=True, ip=child["ip"], reason="direct")
    # Broadcast search — collect any PONGs for ~3 s and look for hostname.
    _recent_pongs.clear()
    _broadcast_ping_all()
    time.sleep(3.0)
    matched = None
    for ip, info in list(_recent_pongs.items()):
        if (info.get("hostname") or "").lower() == target_host.lower():
            matched = (ip, info)
            break
    if not matched:
        return jsonify(ok=False, found=False,
                       err="device not found on network",
                       hostname=target_host), 200
    new_ip, info = matched
    old_ip = child.get("ip")
    with _lock:
        child["ip"] = new_ip
        child.update({k: v for k, v in info.items() if k != "id"})
        child["status"] = 1
        child["seen"] = int(time.time())
        _save("children", _children)
    log.info("FIND: %s relocated %s → %s", target_host, old_ip, new_ip)
    return jsonify(ok=True, found=True, ip=new_ip, oldIp=old_ip,
                   hostname=target_host)


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

def _action_led_ranges_for(child, string_indices):
    """ledStart[8] / ledEnd[8] uint16 arrays targeting only the strings in
    `string_indices`. Absolute LED offsets are kept identical to
    `_child_led_ranges` (the child's leds[] array is one concatenated
    span) — non-selected slots stay at the 0xFFFF sentinel so the
    firmware skips them."""
    ls = [0xFFFF] * 8
    le = [0xFFFF] * 8
    sc = child.get("sc", 0)
    strings = child.get("strings", [])
    sel = set(string_indices)
    offset = 0
    for j in range(min(sc, len(strings), 8)):
        leds = strings[j].get("leds", 0)
        if leds > 0:
            if j in sel:
                ls[j] = offset
                le[j] = offset + leds - 1
            offset += leds
    return struct.pack("<8H", *ls), struct.pack("<8H", *le)


@app.post("/api/children/<int:cid>/action")
def api_child_action(cid):
    """Fire an ad-hoc action at an LED child, targeting selected strings.

    Body — either a saved action by id, or inline params:
      {"actionId": <id>}                  fire a saved /api/actions entry
      {"type": <0-13>, "r":, "g":, "b":,  inline action (type 0 = blackout)
       "speedMs":, "cooling":, ...}
    plus a string selector:
      {"strings": [0, 2]}                 target string indices 0 and 2
      {"allStrings": true}  / omitted     target every configured string

    Sends one CMD_ACTION (0x10) packet; the child applies it immediately
    without a runner. No bake, no sync — this is the operator override
    path the Android Control→Fixtures LED section uses."""
    child = next((c for c in _children if c.get("id") == cid), None)
    if child is None:
        return jsonify(err="no such child"), 404
    ip = child.get("ip")
    if not ip:
        return jsonify(err="child has no IP"), 409
    body = request.get_json(silent=True) or {}
    if "actionId" in body:
        action = next((a for a in _actions if a.get("id") == body["actionId"]), None)
        if action is None:
            return jsonify(err="no such action"), 404
    else:
        action = body
    sc = int(child.get("sc", 0) or 0)
    if sc <= 0:
        return jsonify(err="child has no configured strings"), 409
    if body.get("allStrings") or "strings" not in body:
        sel = list(range(sc))
    else:
        sel = sorted({int(s) for s in body.get("strings", []) if 0 <= int(s) < sc})
    if not sel:
        return jsonify(err="no strings selected"), 400
    t, r, g, b, p16a, p8a, p8b, p8c, p8d = _act_params(action)
    ls, le = _action_led_ranges_for(child, sel)
    pl = struct.pack("<BBBBHBBBB", t, r, g, b, p16a, p8a, p8b, p8c, p8d)
    _send(ip, _hdr(CMD_ACTION) + pl + ls + le)
    log.info("Child %d ad-hoc action: type=%d strings=%s", cid, t, sel)
    return jsonify(ok=True, type=t, strings=sel)


@app.post("/api/children/<int:cid>/action/stop")
def api_child_action_stop(cid):
    """Stop any ad-hoc action on an LED child (CMD_ACTION_STOP). Leaves
    the LEDs at their last frame — pair with a type-0 blackout action to
    also clear them."""
    child = next((c for c in _children if c.get("id") == cid), None)
    if child is None:
        return jsonify(err="no such child"), 404
    ip = child.get("ip")
    if not ip:
        return jsonify(err="child has no IP"), 409
    _send(ip, _hdr(CMD_ACTION_STOP))
    log.info("Child %d ad-hoc action stop", cid)
    return jsonify(ok=True)


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
        # #712 — surface lens-effective FOV on camera fixtures so the 3D
        # viewport draws the polygon the cal pipeline actually uses.
        if f.get("fixtureType") == "camera":
            fixture_data["effectiveFovDeg"] = _effective_fov_for_camera(f)
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
    force = bool(body.get("force"))
    # #739 — two defenses against stale-cache layout writes wiping
    # imported positions:
    #
    # 1. Acquire _lock so a layout-save fired during /api/project/import
    #    can't race the import's persist block.
    # 2. Refuse-to-wipe: if the body sends (0,0,0) for a fid that
    #    currently has a non-zero position, keep the existing position
    #    instead of zeroing it (and log a warning). Pass {force: true}
    #    in the body to override — required for tests / operators that
    #    legitimately want a fixture at the origin.
    #
    # Fixtures absent from the body still drop out of _layout.children
    # (preserves the remove-from-canvas SPA flow). Non-zero coords
    # always replace whatever was there (preserves drag-to-reposition).
    wipes_blocked = []
    with _lock:
        existing = {c["id"]: c for c in (_layout.get("children") or [])}
        new_children = []
        for f in fixtures:
            fid = f["id"]
            x = f.get("x", 0)
            y = f.get("y", 0)
            z = f.get("z", 0)
            if (not force
                    and x == 0 and y == 0 and z == 0
                    and fid in existing):
                cur = existing[fid]
                if cur.get("x") or cur.get("y") or cur.get("z"):
                    new_children.append(cur)
                    wipes_blocked.append(fid)
                    continue
            new_children.append({"id": fid, "x": x, "y": y, "z": z})
        _layout["children"] = new_children
        _save("layout", _layout)
    if wipes_blocked:
        log.warning("/api/layout suppressed (0,0,0) wipe of non-zero "
                    "positions for fid(s) %s; pass force=true to override",
                    wipes_blocked)
    _apply_auto_stage_bounds()  # #628
    return jsonify(ok=True, wipesBlocked=wipes_blocked or None)

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

def _validate_fixture_strings(strings):
    """#864 / #866 — validate optional per-string position (x/y/z) and
    rotation ([rx, ry, rz] degrees) fields.

    Strings without x/y/z continue to inherit the fixture's layout
    position (legacy behaviour). When any of x/y/z is provided, all three
    must be numeric — partial overrides are rejected so a half-set string
    can't silently inherit one axis from the fixture and override the
    other two.

    Strings without `rotation` fall through to the legacy `sdir` token
    (E/N/W/S in the stage X-Y plane). When `rotation` is provided it
    must be a 3-element numeric array using the project's stage-frame
    rotation convention (#586/#600 — same shape as camera/DMX
    `rotation`, parsed by `camera_math.rotation_from_layout`). The
    string's default-forward is stage +Y; rotation re-aims that. This
    is what lets a strip be vertical (rotation [-90, 0, 0] = pitch up
    so the strip runs along stage +Z), which the legacy `sdir` token
    cannot express.
    """
    if not isinstance(strings, list):
        return "strings must be a list"
    for i, s in enumerate(strings):
        if not isinstance(s, dict):
            return f"strings[{i}] must be an object"
        present = [k for k in ("x", "y", "z") if k in s]
        if present and len(present) != 3:
            return (f"strings[{i}] partial position override — provide all "
                    f"of x/y/z together or none (got {present})")
        for k in present:
            v = s[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return f"strings[{i}].{k} must be numeric (mm)"
        if "rotation" in s:
            rot = s["rotation"]
            if (not isinstance(rot, (list, tuple)) or len(rot) != 3
                    or not all(isinstance(v, (int, float))
                               and not isinstance(v, bool) for v in rot)):
                return (f"strings[{i}].rotation must be a 3-element numeric "
                        f"array [rx, ry, rz] in degrees")
    return None


@app.post("/api/fixtures")
def api_fixtures_create():
    global _nxt_fix
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    ftype = body.get("type", "linear")
    if ftype not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    fixture_type = body.get("fixtureType", "led")
    # #899 — type whitelist + per-type validation from the registry (the
    # dmx/camera blocks moved verbatim into fixture_types.py descriptors).
    if not fixture_types.is_valid_type(fixture_type):
        return jsonify(err=fixture_types.invalid_type_error()), 400
    if "strings" in body:
        err = _validate_fixture_strings(body["strings"])
        if err:
            return jsonify(err=err), 400
    err = fixture_types.validate_create(fixture_type, body)
    if err:
        return jsonify(err=err), 400
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
        # #899 — per-type field defaults from the registry (the dmx/
        # camera/gyro stamping blocks moved verbatim into the
        # descriptors' apply_create hooks).
        fixture_types.apply_create(fixture_type, f, body)
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
    # Validate fixtureType if changing — #899 registry-driven.
    if "fixtureType" in body and not fixture_types.is_valid_type(body["fixtureType"]):
        return jsonify(err=fixture_types.invalid_type_error()), 400
    # Validate geometry type if changing
    if "type" in body and body["type"] not in ("linear", "point", "surface", "group"):
        return jsonify(err="Invalid fixture type"), 400
    # #864 — validate per-string position fields if strings is being written
    if "strings" in body:
        err = _validate_fixture_strings(body["strings"])
        if err:
            return jsonify(err=err), 400
    # #899 — per-type update validation from the registry (the dmx and
    # camera blocks, incl. #Q12 fovType and the #423 per-class threshold
    # checks, moved verbatim into fixture_types.py descriptors).
    ft = body.get("fixtureType", f.get("fixtureType", "led"))
    err = fixture_types.validate_update(ft, body, f)
    if err:
        return jsonify(err=err), 400
    # #742 — `homePanDmx16` / `homeTiltDmx16` / `homeSetAt` / `homeSecondary`
    # are deliberately **NOT** in the generic-PUT writable list. They have
    # dedicated endpoints with validation:
    #   POST /api/fixtures/<fid>/home
    #   POST /api/fixtures/<fid>/home/secondary
    #   POST /api/fixtures/<fid>/home/secondary/retry
    #   DELETE /api/fixtures/<fid>/home
    # Routing those writes through the generic PUT bypassed
    # _validate_home_secondary() and silently corrupted operator-captured
    # home anchors when a SPA edit-modal save round-tripped a stale
    # fixture object. The dedicated endpoints stay the single source of
    # truth for all home-anchor mutations.
    # #801 — capture prior Active state so we can fire the
    # transition hook AFTER the write. Active→Inactive must release
    # the claim and send a CMD_GYRO_CTRL(0) packet so the gyro stops
    # streaming.
    prior_gyro_active = bool(f.get("gyroEnabled")) if f.get("fixtureType") == "gyro" else None
    # #894 — build the full update first, then commit it under _lock in
    # one shot. The old field-by-field mutation of the live dict could
    # be observed torn (half old, half new — e.g. new dmxStartAddr with
    # old dmxChannelCount) by the DMX/show playback loops.
    updates = {}
    # #899 — writable keys are COMMON_UPDATE_FIELDS + the union of every
    # registered type's fields, reproducing the pre-#899 flat literal
    # (any type's fields are accepted on any fixture, exactly as before;
    # a newly registered type's fields join automatically).
    for k in fixture_types.update_field_whitelist():
        if k in body:
            # #Q12 — normalise fovType on write so stored value is always in
            # the whitelist (inputs go through _normalise_fov_type).
            if k == "fovType":
                updates[k] = _normalise_fov_type(body[k])
            else:
                updates[k] = body[k]
    with _lock:
        f.update(updates)
        # #780 P1 — fold any newly-set `mountedInverted=True` into
        # `rotation[1] += 180°` so runtime IK never sees the flag.
        # Idempotent for records the startup migration already processed.
        _normalise_mounted_inverted(f)
        _save("fixtures", _fixtures)
    # #801 — gyro Active state transitions:
    #   True  → False (Active → Inactive): release claim, send
    #          CMD_GYRO_CTRL(disabled) so the gyro stops streaming.
    #   False → True  (Inactive → Active): nothing to do here — the
    #          5s auto-lock loop picks it up on its next tick. (No
    #          one-shot lock fired here on purpose; the loop handles
    #          immediate + retry uniformly.)
    if prior_gyro_active is not None and "gyroEnabled" in body:
        new_active = bool(f.get("gyroEnabled"))
        if prior_gyro_active and not new_active:
            try:
                _gyro_inactive_transition(f)
            except Exception:
                log.debug("gyro Inactive transition failed for fid %s",
                          fid, exc_info=True)
    # #742 — log when a request tries to mutate a home anchor through
    # the generic PUT so post-hoc forensics on "who nuked my home" has
    # an audit trail. We do not honour the write — caller must use the
    # dedicated home endpoints.
    rejected_home_keys = [k for k in
                          ("homePanDmx16", "homeTiltDmx16", "homeSetAt", "homeSecondary")
                          if k in body]
    if rejected_home_keys:
        log.warning("PUT /api/fixtures/%d ignored home-anchor field(s) %s — "
                    "use /api/fixtures/<fid>/home or /home/secondary instead",
                    fid, rejected_home_keys)
    # #785 — rotation / profile / orientation may have changed; drop
    # the cached aim sphere so the next /api/mover/<fid>/aim rebuilds.
    _aim_invalidate_sphere(fid)
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
        _aim_invalidate_sphere(fid)  # #785 — rotation changed.
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
    _aim_invalidate_sphere(fid)  # #785 — rotation derived from aimPoint changed.
    return jsonify(ok=True, rotation=f.get("rotation", [0, 0, 0]))

def _validate_home_secondary(sec):
    """#720 PR-1 + #730 — validate a Home Secondary block.

    Post-#730 shape is direction-only::

        {panOffsetDmx16: int, tiltOffsetDmx16: int,
         panMovedDirection: "left"|"right",
         tiltMovedDirection: "down"|"up"}

    Magnitudes come from the profile envelope (panRange / tiltRange);
    only the sign comes from the operator's binary direction calls.
    The legacy PR-1 shape (panDmx16 / tiltDmx16 / operatorTiltDeg) is
    rejected with ``home_secondary_stale_format`` so the SPA can prompt
    the operator to re-run the wizard once.
    """
    if not isinstance(sec, dict):
        raise ValueError("secondary must be an object")
    # Reject legacy shape so /smart/preview can pinpoint the issue.
    if "operatorTiltDeg" in sec and "panMovedDirection" not in sec:
        raise ValueError("home_secondary_stale_format")
    try:
        pan_off = int(sec["panOffsetDmx16"])
        tilt_off = int(sec["tiltOffsetDmx16"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(
            f"secondary requires panOffsetDmx16/tiltOffsetDmx16: {e}")
    pan_dir = sec.get("panMovedDirection")
    tilt_dir = sec.get("tiltMovedDirection")
    if pan_dir not in ("left", "right"):
        raise ValueError("panMovedDirection must be 'left' or 'right'")
    if tilt_dir not in ("down", "up"):
        raise ValueError("tiltMovedDirection must be 'down' or 'up'")
    if not (-65535 <= pan_off <= 65535) or not (-65535 <= tilt_off <= 65535):
        raise ValueError("offset values must be in [-65535, +65535]")
    return {
        "panOffsetDmx16": pan_off,
        "tiltOffsetDmx16": tilt_off,
        "panMovedDirection": pan_dir,
        "tiltMovedDirection": tilt_dir,
        "capturedAt": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/api/fixtures/<int:fid>/home")
def api_fixture_get_home(fid):
    """#720 PR-1 — return the saved Home anchor and the optional Home
    Secondary block. Either field is ``null`` when unset."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    primary = None
    if f.get("homePanDmx16") is not None and f.get("homeTiltDmx16") is not None:
        primary = {
            "panDmx16": int(f["homePanDmx16"]),
            "tiltDmx16": int(f["homeTiltDmx16"]),
            "setAt": f.get("homeSetAt"),
        }
    return jsonify(ok=True,
                   primary=primary,
                   secondary=f.get("homeSecondary"))


@app.post("/api/fixtures/<int:fid>/home")
def api_fixture_set_home(fid):
    """#687 — capture the operator-confirmed Home anchor for a DMX mover.

    Body: ``{"panDmx16": 0..65535, "tiltDmx16": 0..65535}``. These are the
    16-bit pan/tilt values (coarse << 8 | fine) that the operator drove
    the fixture to during the Set Home modal — at this DMX, the beam aims
    along the fixture's saved ``rotation`` vector.

    Replaces the geometric kickoff chain (#682-LL / #682-C-v2) with a
    single trusted observation.  Calibration kickoff downstream uses
    this anchor instead of ``compute_initial_aim``.

    #720 PR-1 extension: body may also include an optional ``secondary``
    block ``{panDmx16, tiltDmx16, operatorTiltDeg}`` captured by the
    home wizard's secondary slew step. Used downstream (PR-1.5) by
    ``solve_dmx_per_degree`` to bootstrap the SMART 2-pair affine
    estimate before any probes have been collected.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    if f.get("fixtureType") != "dmx":
        return jsonify(err="Set Home applies to DMX mover fixtures only"), 400
    body = request.get_json(silent=True) or {}
    try:
        pan = int(body["panDmx16"])
        tilt = int(body["tiltDmx16"])
    except (KeyError, TypeError, ValueError):
        return jsonify(err="panDmx16 and tiltDmx16 are required ints"), 400
    if not (0 <= pan <= 65535) or not (0 <= tilt <= 65535):
        return jsonify(err="panDmx16/tiltDmx16 must be in [0, 65535]"), 400
    # #743 — re-saving the primary anchor at a meaningfully different DMX
    # invalidates the previously-captured homeSecondary: the L/R + D/U
    # direction calls were anchored to the OLD primary, so the affine
    # estimate would be geometrically wrong. Auto-clear secondary here
    # and surface a flag so the SPA prompts the operator to redo the
    # secondary step. Tolerance is ±5 LSB-16 (≈0.03° at 540°/65535)
    # which is well below operator hand-jog noise but catches genuine
    # re-anchors after a mount adjustment.
    SECONDARY_INVALIDATE_LSB = 5
    secondary_invalidated = False
    prior_pan = f.get("homePanDmx16")
    prior_tilt = f.get("homeTiltDmx16")
    if (f.get("homeSecondary") is not None
            and prior_pan is not None and prior_tilt is not None
            and (abs(int(prior_pan) - pan) > SECONDARY_INVALIDATE_LSB
                 or abs(int(prior_tilt) - tilt) > SECONDARY_INVALIDATE_LSB)
            and body.get("secondary") is None):
        f["homeSecondary"] = None
        secondary_invalidated = True
        log.info("Set Home: fid=%d primary moved (%d,%d→%d,%d) — "
                 "auto-cleared stale homeSecondary",
                 fid, prior_pan, prior_tilt, pan, tilt)
    f["homePanDmx16"] = pan
    f["homeTiltDmx16"] = tilt
    f["homeSetAt"] = datetime.utcnow().isoformat() + "Z"
    sec_in = body.get("secondary")
    if sec_in is not None:
        try:
            f["homeSecondary"] = _validate_home_secondary(sec_in)
        except ValueError as e:
            return jsonify(err=str(e)), 400
    _save("fixtures", _fixtures)
    # #785 — Home anchor changed; drop cached aim sphere.
    _aim_invalidate_sphere(fid)
    log.info("Set Home: fid=%d pan=%d tilt=%d rotation=%s secondary=%s",
             fid, pan, tilt, f.get("rotation"),
             "yes" if f.get("homeSecondary") else "no")
    return jsonify(ok=True,
                   homePanDmx16=pan, homeTiltDmx16=tilt,
                   homeSetAt=f["homeSetAt"],
                   homeSecondary=f.get("homeSecondary"),
                   secondaryInvalidated=secondary_invalidated)


@app.post("/api/fixtures/<int:fid>/home/secondary")
def api_fixture_set_home_secondary(fid):
    """#720 PR-1 — set just the Home Secondary block. Requires Home
    primary to already be set (otherwise the secondary is meaningless)."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    if f.get("fixtureType") != "dmx":
        return jsonify(err="Home applies to DMX mover fixtures only"), 400
    if f.get("homePanDmx16") is None or f.get("homeTiltDmx16") is None:
        return jsonify(err="Home primary must be set first"), 400
    body = request.get_json(silent=True) or {}
    try:
        f["homeSecondary"] = _validate_home_secondary(body)
    except ValueError as e:
        return jsonify(err=str(e)), 400
    _save("fixtures", _fixtures)
    return jsonify(ok=True, homeSecondary=f["homeSecondary"])


SECONDARY_SLEW_DEG = 90.0          # axis slew amount, degrees (#732)
SECONDARY_HOME_PAUSE_MS = 2000     # operator-visible pause at home pre-slew (#732)


def _secondary_axis_offset(home_pan_dmx16, home_tilt_dmx16, axis,
                            pan_range_deg, tilt_range_deg,
                            slew_deg=SECONDARY_SLEW_DEG):
    """#730 + #732 — pick a signed DMX offset corresponding to
    ``slew_deg`` degrees on the requested axis.

    Magnitude is profile-aware: 90 ° on a 540 ° panRange ≈ 10 923 DMX
    ticks (~16.7 % of the 16-bit range), versus 90 ° on a 180 °
    tiltRange = 32 768 (50 %). Sign chosen to keep the slewed pose
    inside ``[0, 65535]``: prefer +, fall back to -, clamp as a last
    resort. Returns ``(slew_pan_dmx16, slew_tilt_dmx16,
    signed_offset)`` with the non-active axis held at home.
    """
    if axis == "pan":
        range_deg = float(pan_range_deg or 540)
    else:
        range_deg = float(tilt_range_deg or 270)
    if range_deg <= 0:
        range_deg = 540.0 if axis == "pan" else 270.0
    base_off = int(round(float(slew_deg) / range_deg * 65535))
    if base_off < 1:
        base_off = 1
    home_dmx = int(home_pan_dmx16 if axis == "pan" else home_tilt_dmx16)
    pos = home_dmx + base_off
    neg = home_dmx - base_off
    if 0 <= pos <= 65535:
        signed = +base_off
        new_dmx = pos
    elif 0 <= neg <= 65535:
        signed = -base_off
        new_dmx = neg
    else:
        signed = +base_off
        new_dmx = max(0, min(65535, pos))
    if axis == "pan":
        return new_dmx, int(home_tilt_dmx16), signed
    return int(home_pan_dmx16), new_dmx, signed


def _write_dmx_pose(fixture, prof_info, pan_dmx16, tilt_dmx16):
    """Helper: write pan/tilt + dimmer + RGB-green to the live engine.

    Centralised so the home-pause and target-slew phases share the
    same write path. Returns ``None`` on success or an error string.
    """
    if not _artnet.running and not _sacn.running:
        return ("Art-Net engine not running — start it from "
                "Settings → DMX Engine before slewing")
    engine = _artnet if _artnet.running else _sacn
    profile = {"channel_map": prof_info.get("channel_map", {}),
               "channels": prof_info.get("channels", [])}
    try:
        uni = fixture.get("dmxUniverse", 1)
        addr = fixture.get("dmxStartAddr", 1)
        uni_buf = engine.get_universe(uni)
        uni_buf.set_fixture_pan_tilt(addr,
                                     pan_dmx16 / 65535.0,
                                     tilt_dmx16 / 65535.0,
                                     profile)
        uni_buf.set_fixture_dimmer(addr, 255, profile)
        uni_buf.set_fixture_rgb(addr, 0, 255, 0, profile)
        return None
    except Exception as e:
        return f"dmx_write_failed: {e}"


def _do_secondary_slew(fid, axis, settle_ms,
                        home_pause_ms=SECONDARY_HOME_PAUSE_MS):
    """Shared helper for /home/secondary/prepare + /home/secondary/retry.

    #732 — every slew now executes a clean three-phase sequence so the
    operator sees the head return to a known reference before any
    motion they have to call:

      1. Drive the head to ``(homePanDmx16, homeTiltDmx16)``.
      2. Hold for ``home_pause_ms`` (default 2 s) so the operator's
         eyes adjust and the previous slew's afterimage clears.
      3. Drive the head to the target axis offset (90 ° per #732)
         and settle for ``settle_ms``.

    Combined-axis (legacy ``axis is None``) calls follow the same
    pattern. Returns a Flask response tuple.
    """
    f = next((x for x in _fixtures if x["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    if f.get("fixtureType") != "dmx":
        return jsonify(err="Home applies to DMX mover fixtures only"), 400
    home_pan = f.get("homePanDmx16")
    home_tilt = f.get("homeTiltDmx16")
    if home_pan is None or home_tilt is None:
        return jsonify(err="Home primary must be set first"), 400
    if axis not in (None, "pan", "tilt"):
        return jsonify(err="axis must be 'pan' or 'tilt' (or omitted)"), 400

    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    if not prof_info:
        return jsonify(err="Fixture has no DMX profile"), 400

    pan_range = prof_info.get("panRange", 540) or 540
    tilt_range = prof_info.get("tiltRange", 270) or 270

    if axis == "pan":
        slew_pan, slew_tilt, pan_off = _secondary_axis_offset(
            home_pan, home_tilt, "pan", pan_range, tilt_range)
        tilt_off = 0
    elif axis == "tilt":
        slew_pan, slew_tilt, tilt_off = _secondary_axis_offset(
            home_pan, home_tilt, "tilt", pan_range, tilt_range)
        pan_off = 0
    else:
        # Backwards-compat: no axis arg → slew both at once.
        slew_pan, _stilt, pan_off = _secondary_axis_offset(
            home_pan, home_tilt, "pan", pan_range, tilt_range)
        _span, slew_tilt, tilt_off = _secondary_axis_offset(
            home_pan, home_tilt, "tilt", pan_range, tilt_range)

    # Phase 1 — drive to home (no-op when head is already there;
    # resets the previous axis's offset on subsequent calls).
    err = _write_dmx_pose(f, prof_info, int(home_pan), int(home_tilt))
    if err:
        log.warning("home/secondary home-write failed: %s", err)
        return jsonify(err=err), (503 if "engine" in err else 500)
    # Phase 2 — operator-visible pause at home (#732).
    if home_pause_ms > 0:
        time.sleep(min(5000, max(0, int(home_pause_ms))) / 1000.0)
    # Phase 3 — drive to target offset and settle.
    err = _write_dmx_pose(f, prof_info, slew_pan, slew_tilt)
    if err:
        log.warning("home/secondary target-write failed: %s", err)
        return jsonify(err=err), 500
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
    return jsonify(ok=True,
                   axis=axis,
                   panDmx16=slew_pan,
                   tiltDmx16=slew_tilt,
                   panOffsetDmx16=pan_off,
                   tiltOffsetDmx16=tilt_off,
                   slewDeg=SECONDARY_SLEW_DEG,
                   homePauseMs=home_pause_ms,
                   panRange=pan_range,
                   tiltRange=tilt_range,
                   tiltOffsetDmx16Profile=prof_info.get("tiltOffsetDmx16", 32768),
                   tiltUp=prof_info.get("tiltUp", False))


@app.post("/api/fixtures/<int:fid>/home/secondary/prepare")
def api_fixture_prepare_home_secondary(fid):
    """#720 PR-1 + #730 — slew a fixture along one axis at a time so the
    operator can call which way the beam moved.

    Body: ``{axis: "pan"|"tilt", settleMs?: int}``. With ``axis``
    omitted the legacy combined slew (#721 PR-1 behaviour) runs — kept
    for backwards-compat with old SPA builds; the post-#730 wizard
    always passes ``axis``. Returns the DMX values written + the
    signed offset applied + profile envelope info.
    """
    body = request.get_json(silent=True) or {}
    settle_ms = max(0, min(5000, int(body.get("settleMs", 1200))))
    return _do_secondary_slew(fid, body.get("axis"), settle_ms)


@app.post("/api/fixtures/<int:fid>/home/secondary/retry")
def api_fixture_retry_home_secondary(fid):
    """#730 — re-slew the requested axis without committing.

    Body: ``{axis: "pan"|"tilt", settleMs?: int}``. Operators get
    distracted; this endpoint exists so the wizard's "Show me again"
    button doesn't have to abort the whole flow."""
    body = request.get_json(silent=True) or {}
    axis = body.get("axis")
    if axis not in ("pan", "tilt"):
        return jsonify(err="axis must be 'pan' or 'tilt'"), 400
    settle_ms = max(0, min(5000, int(body.get("settleMs", 1200))))
    return _do_secondary_slew(fid, axis, settle_ms)


# #784 PR-7 — `GET /api/fixtures/<fid>/coverage` deleted with the
# `coverage_math` module. Fed the legacy SMART preview UI only.


@app.delete("/api/fixtures/<int:fid>/home")
def api_fixture_clear_home(fid):
    """#687 — clear a previously-set Home anchor (forces re-prompt).

    #720 PR-1: also clears any Home Secondary block atomically — the
    SMART 2-pair estimate becomes meaningless without primary."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f:
        return jsonify(err="Fixture not found"), 404
    for k in ("homePanDmx16", "homeTiltDmx16", "homeSetAt", "homeSecondary"):
        f.pop(k, None)
    _save("fixtures", _fixtures)
    # #785 — Home cleared; drop cached sphere (next aim returns 400 no_home).
    _aim_invalidate_sphere(fid)
    return jsonify(ok=True)


@app.delete("/api/fixtures/<int:fid>")
def api_fixture_delete(fid):
    global _fixtures
    # #688 — idempotent. The SPA's bulk-delete and undo flows can
    # legitimately call DELETE for a fid that's already gone (race
    # between the SPA's optimistic local-state update and the
    # subsequent server round-trip). Return 200 with `removed` to
    # signal whether the call actually changed state, instead of
    # erroring on the second call.
    existed = any(f["id"] == fid for f in _fixtures)
    if existed:
        _fixtures = [f for f in _fixtures if f["id"] != fid]
        _save("fixtures", _fixtures)
    return jsonify(ok=True, removed=existed)

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
            ts = g.get("ts") or g.get("batTs") or 0
            stale = (now - ts) > GYRO_STALE_S if ts else True
            flags = g.get("flags", 0)
            entry = {
                "ip":        ip,
                "roll":      round(g.get("roll", 0.0), 2),
                "pitch":     round(g.get("pitch", 0.0), 2),
                "yaw":       round(g.get("yaw", 0.0), 2),
                "fps":       g.get("fps", 0),
                "streaming": bool(flags & 0x01),
                "imuOk":     bool(flags & 0x02),
                "mode":      (flags >> 4) & 0x03,
                "stale":     stale,
                "ts":        ts,
            }
            # #813 follow-up — surface battery telemetry when CMD_GYRO_BATT
            # has been seen for this IP. None on the orient-only path so
            # SPA / consumers can distinguish "no telemetry yet" from
            # "telemetry says 0%". batAge lets the SPA grey-out a stale
            # battery readout (e.g. gyro rebooted, battery channel quiet).
            if "vbat" in g:
                entry["vbat"] = round(g["vbat"], 2)
                entry["batPct"] = g.get("batPct")
                entry["batCharging"] = bool(g.get("batCharging", False))
                entry["batTs"] = g.get("batTs", 0)
                entry["batAge"] = max(0.0, now - g.get("batTs", 0))
            result.append(entry)
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
        _mover_engine.claim(gf["assignedMoverId"], device_id, dname, "gyro")
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

    Also syncs the live sensor descriptor from the node onto the fixture
    record so a hardware swap on a node (camera replaced, FOV/resolution
    changed, customName edited) propagates to the Setup tab without
    requiring re-registration. Node is source of truth when online;
    stored values survive offline.
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
                sensors = info.get("cameras", [])
                idx = c.get("cameraIdx", 0)
                if idx < len(sensors):
                    s = sensors[idx]
                    # Update operator-visible name. Prefer customName (set
                    # via the node's /config page), fall back to the device
                    # descriptor (e.g. "EMEET SmartCam Nova 4K"). Either
                    # changing means the operator should see the new value.
                    live_name = (s.get("customName") or s.get("name") or "").strip()
                    if live_name and live_name != c.get("name"):
                        c["name"] = live_name
                        cam["name"] = live_name
                        dirty = True
                    # Hardware-descriptor sync — the actual reason a swap
                    # would change something the operator cares about.
                    # Camera replaced? FOV / resolution / device-string
                    # all change. Push live values onto the fixture record
                    # so the Setup card reflects current hardware.
                    for field, sensor_key in (
                            ("fovDeg",      "fovDeg"),
                            ("resolutionW", "resW"),
                            ("resolutionH", "resH"),
                            ("device",      "device"),
                            ("flip",        "flip"),
                    ):
                        live = s.get(sensor_key)
                        if live is not None and c.get(field) != live:
                            c[field] = live
                            cam[field] = live
                            dirty = True
                    # Surface the device descriptor separately too so the
                    # Setup card can show "now: EMEET 4K (was: Logitech C920)"
                    # — useful when customName masks the actual hardware.
                    desc = s.get("name")
                    if desc and cam.get("hwDescriptor") != desc:
                        cam["hwDescriptor"] = desc
                        if c.get("hwDescriptor") != desc:
                            c["hwDescriptor"] = desc
                            dirty = True
                # Note: camera node trackingRunning is node-level, not per-sensor.
                # Trust _tracking_state (per-fixture) instead of overriding from
                # the node capability, which would mark all sensors on the same
                # IP as tracking when only one was started.
        # #712 — surface the lens-effective FOV the cal pipeline actually
        # uses, so the SPA dashboard 3D viewport draws the real visible
        # polygon instead of the over-claiming manufacturer spec.
        cam["effectiveFovDeg"] = _effective_fov_for_camera(c)
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
        return jsonify(err="Camera not found", errType="not-found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP", errType="not-configured"), 400
    cam_idx = request.args.get("cam", f.get("cameraIdx", 0), type=int)
    # #685 — per-camera device lock with a 2 s try-acquire. The live
    # preview poll runs at 1 Hz; if auto-tune is mid-capture the preview
    # frame can wait briefly. Anything past 2 s indicates a stuck capture
    # and we'd rather return a typed busy-error than block the request
    # thread (Flask's dev server has a small thread pool).
    lock = _get_camera_device_lock(ip)
    acquired = False
    if lock is not None:
        acquired = lock.acquire(timeout=2.0)
        if not acquired:
            return jsonify(err="Camera capture busy (device locked)",
                            errType="capture-busy"), 503
    try:
        import urllib.request as _ur
        resp = _ur.urlopen(f"http://{ip}:5000/snapshot?cam={cam_idx}", timeout=15)
        data = resp.read()
        from flask import Response
        return Response(data, mimetype="image/jpeg")
    except Exception as e:
        err_type, msg = _classify_camera_fetch_error(e)
        return jsonify(err=msg, errType=err_type), 503
    finally:
        if acquired and lock is not None:
            lock.release()

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
    # #712 Track 1c — surface FOV-drift recommendation so the SPA can
    # banner "Recalibrate camera lens" when ≥2 cal runs reported beams
    # outside this camera's assumed FOV polygon.
    drift = (_calibrations.get(str(fid)) or {}).get("fovDrift")
    if isinstance(drift, dict) and drift.get("recommendRecalibrate"):
        info = dict(info)
        info["fovDrift"] = {
            "recommendRecalibrate": True,
            "events": len(drift.get("events") or []),
            "lastDriftAt": drift.get("lastDriftAt"),
        }
    return jsonify(info)

# ── Q12: FOV type whitelist + helper ──────────────────────────────────
# Cameras store their FOV as a single number (fovDeg) with a type flag
# (fovType) saying whether that number is horizontal, vertical, or
# diagonal. Manufacturers spec different axes; diagonal is the most
# commonly published for USB webcams so we default there. Every caller
# that needs a horizontal FOV for ray math should go through
# _camera_h_fov_rad() so the conversion stays consistent.
# #899 — definitions moved to fixture_types.py (the camera descriptor's
# validators need them); aliased here for the remaining call sites.
_FOV_TYPE_WHITELIST = fixture_types.FOV_TYPE_WHITELIST
_FOV_TYPE_DEFAULT = fixture_types.FOV_TYPE_DEFAULT
_normalise_fov_type = fixture_types.normalise_fov_type


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

    # Tier 2: FOV projection ray-plane intersect. Uses the canonical
    # camera_math.build_camera_to_stage(tilt, pan, roll) helper per
    # CLAUDE.md — review flagged that the previous inline basis derivation
    # silently dropped rotation[2] (roll), so roll-mounted cameras placed
    # feet in the wrong stage location with tier="fov-projection".
    if cam_fixture and frame_w and frame_h:
        pos_map = {p["id"]: p for p in _layout.get("children", [])}
        cam_pos = pos_map.get(cam_fixture.get("id"), {})
        cx0 = float(cam_pos.get("x", 0) or 0)
        cy0 = float(cam_pos.get("y", 0) or 0)
        cz0 = float(cam_pos.get("z", 0) or 0)
        if cz0 > 1:  # camera must be off the floor for a ray-plane intersect
            try:
                from camera_math import build_camera_to_stage, rotation_from_layout
            except Exception:
                build_camera_to_stage = None
                rotation_from_layout = None
            rot = cam_fixture.get("rotation", [0, 0, 0]) or [0, 0, 0]
            if rotation_from_layout:
                tilt_deg, pan_deg, roll_deg = rotation_from_layout(rot)
            else:
                tilt_deg = float(rot[0] or 0)
                pan_deg = float(rot[1] or 0)
                roll_deg = float(rot[2] or 0)
            R = build_camera_to_stage(tilt_deg, pan_deg, roll_deg) if build_camera_to_stage else None
            if R is not None:
                fov_rad = _camera_h_fov_rad(cam_fixture, frame_w, frame_h)
                half_fov = fov_rad / 2.0
                aspect = frame_w / frame_h if frame_h > 0 else 1.0
                # Pinhole cam-local ray: +Z forward, +X right, +Y down.
                ndc_x = (px / frame_w - 0.5) * 2.0
                ndc_y = (py / frame_h - 0.5) * 2.0
                local_x = math.tan(half_fov) * ndc_x
                local_y = math.tan(half_fov) * ndc_y / max(aspect, 1e-6)
                local_z = 1.0
                # R @ local  — numpy path or list-of-lists fallback
                if hasattr(R, "shape"):  # ndarray
                    v = R @ [local_x, local_y, local_z]
                    ray_x, ray_y, ray_z = float(v[0]), float(v[1]), float(v[2])
                else:
                    ray_x = R[0][0]*local_x + R[0][1]*local_y + R[0][2]*local_z
                    ray_y = R[1][0]*local_x + R[1][1]*local_y + R[1][2]*local_z
                    ray_z = R[2][0]*local_x + R[2][1]*local_y + R[2][2]*local_z
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
    """Run object detection on a camera and return detections with stage coords.

    #620 — this used to proxy POST /scan to the camera node's local
    detector. Pi 3 + fw 1.3.0 had an OpenCV VideoCapture regression
    that blocked /scan while /snapshot kept working. Now the
    orchestrator-side CVEngine does detection on a snapshot it pulls
    via GET /snapshot (the capture path that already works on every
    camera node), matching the design the /detect + /beam-detect +
    /depth routes already use (#333 — move CV processing to orchestrator).

    Any camera node that serves /snapshot works here regardless of
    firmware — no more camera-node detector dependency for scans.
    """
    f = next((f for f in _fixtures if f["id"] == fid and f.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    if _cv is None:
        return jsonify(err="CVEngine not available on orchestrator"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    body = request.get_json(silent=True) or {}
    threshold = body.get("threshold", 0.5)
    cam_idx = body.get("cam", f.get("cameraIdx", 0))
    # Treat body resolution as the YOLO input size; snapshot always comes
    # at native camera resolution. YOLO internally letterboxes.
    input_size = int(body.get("resolution", 640))
    classes = body.get("classes")
    # #621 — tile mode for high-res detection of small/distant targets.
    tile = bool(body.get("tile"))
    tile_size = int(body.get("tileSize", 640))
    tile_overlap = float(body.get("tileOverlap", 0.2))

    try:
        t0 = time.monotonic()
        frame = _cv.fetch_snapshot(ip, cam_idx)
        capture_ms = round((time.monotonic() - t0) * 1000)
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
        if tile:
            detections, inference_ms = _cv.detect_objects_tiled(
                frame, threshold=threshold, classes=classes,
                tile_size=tile_size, overlap=tile_overlap)
        else:
            detections, inference_ms = _cv.detect_objects(
                frame, threshold=threshold, classes=classes,
                input_size=input_size)
    except Exception as e:
        return jsonify(err=f"Scan failed: {e}"), 503

    stage_dets = _pixel_to_stage(detections, f, frame_w, frame_h)
    return jsonify(
        ok=True,
        detections=stage_dets,
        cameraId=fid,
        captureMs=capture_ms,
        inferenceMs=round(inference_ms) if inference_ms else None,
        tile=tile,
        frameSize=[frame_w, frame_h],
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


@app.delete("/api/cameras/<int:fid>/intrinsic")
def api_camera_intrinsic_delete(fid):
    """#597 — discard a camera node's saved intrinsic calibration so the
    Advanced Scan wizard can re-run from scratch. Proxies the camera-
    side DELETE; leaves the orchestrator's separate stage-map
    homography in _calibrations untouched (use DELETE /api/cameras/
    <fid>/calibration for that one, #619).
    """
    f = next((fx for fx in _fixtures
              if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"http://{ip}:5000/calibrate/intrinsic?cam={cam_idx}",
            method="DELETE")
        resp = _ur.urlopen(req, timeout=10)
        try:
            body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            body = {"ok": True}
        return jsonify(body)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 503


@app.post("/api/cameras/<int:fid>/intrinsic/reset")
def api_camera_intrinsic_reset(fid):
    """#597 — reset the intrinsic-capture buffer on a camera node
    (drops accumulated ArUco / checkerboard frames without discarding
    any saved calibration). Use before restarting a capture sequence.
    """
    f = next((fx for fx in _fixtures
              if fx["id"] == fid and fx.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    import urllib.request as _ur
    try:
        req = _ur.Request(
            f"http://{ip}:5000/calibrate/intrinsic/reset?cam={cam_idx}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST")
        resp = _ur.urlopen(req, timeout=10)
        try:
            body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            body = {"ok": True}
        return jsonify(body)
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
    # frame-to-frame noisy; on the sample rig each camera reliably
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
    #   solution with the camera under the floor. On the sample rig
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


@app.delete("/api/cameras/<int:fid>/calibration")
def api_camera_calibration_delete(fid):
    """#619 — discard a camera's stage-map calibration. The rig moves, the
    markers move, the operator needs a way to say 'this calibration is
    stale, throw it out' without falling back to a whole-project factory
    reset. Complements the existing DELETE /api/calibration/mover/<fid>
    route for mover calibrations.

    Q7 single-source-homography made this clean to add: there's only one
    place the matrix lives now (``_calibrations[str(fid)]``), so clearing
    that one key removes every downstream consumer's access.
    """
    f = next((x for x in _fixtures
              if x["id"] == fid and x.get("fixtureType") == "camera"), None)
    if not f:
        return jsonify(err="Camera not found"), 404
    existed = _calibrations.pop(str(fid), None) is not None
    if existed:
        try:
            _save("calibrations", _calibrations)
        except Exception as e:
            log.warning("calibration delete: persist failed for fid=%d: %s", fid, e)
    return jsonify(ok=True, removed=existed)


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


# ── #913 Radar calibration walk — track-correlation pose solver ───────
# mmwave_tracking.md §6 layer 2: one person walks a loop through the
# radar overlap zones; radar_calibration.py records every bound
# sensor-frame observation (via the RadarFusion.record_observation hook,
# attached only while recording so the tracking hot path pays nothing
# otherwise) and solves each non-reference radar's 2-D rigid transform
# against the reference radar's trajectory projected through its
# current layer-1 pose. Apply is explicit and per-fixture — solve never
# touches the stores.
import radar_calibration

_radar_cal = radar_calibration.RadarCalibration()


def _radar_pose_snapshots():
    """Current pose snapshot per radar fixture — same stores and
    conventions as _handle_mmw_targets (#910): position from
    _layout.children, rotation from the fixture record (decoded only
    via rotation_from_layout downstream)."""
    pos_map = {p.get("id"): p for p in _layout.get("children", [])}
    poses = {}
    for f in _fixtures:
        if f.get("fixtureType") != "radar":
            continue
        p = pos_map.get(f.get("id"), {})
        poses[f["id"]] = {
            "id": f["id"],
            "name": f.get("name"),
            "x": p.get("x", 0) or 0,
            "y": p.get("y", 0) or 0,
            "z": p.get("z", 0) or 0,
            "rotation": f.get("rotation") or [0, 0, 0],
        }
    return poses


@app.post("/api/radar/calibration/start")
def api_radar_cal_start():
    """Start recording a calibration walk. 409 if one is already running."""
    if not _radar_cal.start():
        return jsonify(err="A radar calibration walk is already recording — "
                           "stop it before starting another"), 409
    # Attach the hook AFTER start so record() never sees a stale session;
    # record() also gates on its own recording flag (belt-and-braces).
    _radar_fusion.record_observation = _radar_cal.record
    log.info("radar calibration: walk recording started")
    return jsonify(ok=True, startedAt=_radar_cal.started_at)


@app.post("/api/radar/calibration/stop")
def api_radar_cal_stop():
    """Stop the walk; returns per-fixture sample counts."""
    _radar_fusion.record_observation = None   # detach first — hot path idle
    counts = _radar_cal.stop()
    if counts is None:
        return jsonify(err="No radar calibration walk is recording"), 400
    log.info("radar calibration: walk stopped — samples per fixture: %s", counts)
    return jsonify(ok=True, samples={str(k): v for k, v in counts.items()})


@app.post("/api/radar/calibration/solve")
def api_radar_cal_solve():
    """Solve pose proposals from the recorded walk. Body: optional
    referenceFixtureId (default: the radar with the most samples).
    Returns proposals with residuals; does NOT apply anything."""
    if _radar_cal.recording:
        return jsonify(err="Stop the calibration walk before solving"), 400
    body = request.get_json(silent=True) or {}
    ref = body.get("referenceFixtureId")
    try:
        result = _radar_cal.solve(_radar_pose_snapshots(),
                                  reference_fixture_id=ref)
    except radar_calibration.SolveError as e:
        return jsonify(err=str(e)), 400
    return jsonify(ok=True, **result)


@app.post("/api/radar/calibration/apply")
def api_radar_cal_apply():
    """Apply accepted proposals from the last solve. Body:
    {fixtureIds: [id, ...]}. Updates layout position (x/y — z kept) and
    fixture rotation (pan/yaw only, via rotation_to_layout inside the
    solver) under _lock, persists, and records a `radarCalibration`
    entry in the calibrations store."""
    body = request.get_json(silent=True) or {}
    ids = body.get("fixtureIds")
    if not isinstance(ids, list) or not ids:
        return jsonify(err="Body must carry fixtureIds: a non-empty list of "
                           "fixture ids to accept"), 400
    solve = _radar_cal.last_solve
    if not solve:
        return jsonify(err="No solve results to apply — run "
                           "/api/radar/calibration/solve first"), 400
    by_id = {p["fixtureId"]: p for p in solve.get("proposals", [])
             if "proposed" in p}
    missing = [fid for fid in ids if fid not in by_id]
    if missing:
        return jsonify(err=f"No applicable proposal for fixture id(s) "
                           f"{missing} in the last solve"), 400
    applied = []
    with _lock:
        children = _layout.setdefault("children", [])
        child_map = {c.get("id"): c for c in children}
        for fid in ids:
            prop = by_id[fid]
            f = next((x for x in _fixtures if x.get("id") == fid), None)
            if f is None:
                return jsonify(err=f"Fixture {fid} no longer exists"), 400
            p = prop["proposed"]
            child = child_map.get(fid)
            if child is None:
                child = {"id": fid, "x": 0, "y": 0, "z": p["z"]}
                children.append(child)
                child_map[fid] = child
            child["x"] = round(float(p["x"]), 1)
            child["y"] = round(float(p["y"]), 1)
            # z untouched (planar solve — design pin)
            f["rotation"] = list(p["rotation"])
            _calibrations.setdefault(str(fid), {})["radarCalibration"] = {
                "timestamp": time.time(),
                "referenceFixtureId": solve.get("referenceFixtureId"),
                "rmsResidualMm": prop.get("rmsResidualMm"),
                "samples": prop.get("samples"),
                "deltaPosMm": prop.get("deltaPosMm"),
                "deltaYawDeg": prop.get("deltaYawDeg"),
                "applied": {"x": child["x"], "y": child["y"],
                            "rotation": list(p["rotation"])},
            }
            applied.append(fid)
        _save("fixtures", _fixtures)
        _save("layout", _layout)
        _save("calibrations", _calibrations)
    _apply_auto_stage_bounds()  # layout moved — same hook as /api/layout (#628)
    log.info("radar calibration: applied proposals for fixture(s) %s", applied)
    return jsonify(ok=True, applied=applied)


@app.get("/api/radar/calibration/status")
def api_radar_cal_status():
    """Recording state + per-fixture sample counts + last solve (kept so
    the SPA card survives a reload without re-solving)."""
    st = _radar_cal.status()
    st["samples"] = {str(k): v for k, v in st["samples"].items()}
    st["ok"] = True
    return jsonify(st)


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


@app.post("/api/fixtures/kill-strobes")
def api_fixtures_kill_strobes():
    """Force every strobe channel to its Open value across all DMX
    fixtures. #888 — backs the "Stop all effects" safety button on the
    Fixtures page (called in parallel with kill-effects).

    Skips fixtures held by a claim writer (mover-control, calibration,
    gyro press-Start) — they're already authored.

    Returns: {ok, killed: int, skipped: list[fid]}.
    """
    if not (_artnet.running or _sacn.running):
        return jsonify(ok=False, err="DMX engine not running"), 503
    from dmx_profiles import strobe_open_value
    snap = _claim_arbiter.snapshot()
    killed = 0
    skipped = []
    engine = _artnet if _artnet.running else _sacn
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f.get("id")
        if _claim_arbiter.is_muted(fid, snap):
            skipped.append(fid)
            continue
        pid = f.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        if not info:
            continue
        channels = info.get("channels", []) or []
        strobe_chs = [c for c in channels if c.get("type") == "strobe"]
        if not strobe_chs:
            continue
        try:
            uni = int(f.get("dmxUniverse", 1) or 1)
            addr = int(f.get("dmxStartAddr", 1) or 1)
            buf = engine.get_universe(uni)
            profile = {"channel_map": info.get("channel_map", {}), "channels": channels}
            open_val = strobe_open_value(profile)
            for ch in strobe_chs:
                off = ch.get("offset", 0)
                if 0 <= addr - 1 + off < 512:
                    buf.set_channel(addr + off, int(open_val))
            killed += 1
        except Exception:
            log.warning("kill-strobes: fixture %s failed", fid, exc_info=True)
            skipped.append(fid)
    return jsonify(ok=True, killed=killed, skipped=skipped)


@app.post("/api/fixtures/kill-effects")
def api_fixtures_kill_effects():
    """Zero every channel tagged with the `bubble-toggle` or
    `haze-segmented` shortcut across all DMX fixtures. #888 — backs the
    "Stop all effects" safety button (with kill-strobes).

    Falls back to name-match (channel name contains "bubble"/"haze"/"fog")
    for profiles without explicit shortcut annotations.

    Returns: {ok, killed: int, channelsWritten: int, skipped: list[fid]}.
    """
    if not (_artnet.running or _sacn.running):
        return jsonify(ok=False, err="DMX engine not running"), 503
    snap = _claim_arbiter.snapshot()
    EFFECT_SHORTCUTS = {"bubble-toggle", "haze-segmented"}
    EFFECT_NAME_HINTS = ("bubble", "haze", "fog")
    killed = 0
    written = 0
    skipped = []
    engine = _artnet if _artnet.running else _sacn
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f.get("id")
        if _claim_arbiter.is_muted(fid, snap):
            skipped.append(fid)
            continue
        pid = f.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        if not info:
            continue
        channels = info.get("channels", []) or []
        targets = []
        # Mirror the JS/Kotlin shortcut renderer's ELIGIBLE_NAME_TYPES gate:
        # only dimmer-class / speed / reset channels are eligible for the
        # name-match fallback, so a strobe-typed "Fog Strobe Macro" channel
        # doesn't get zeroed when the operator hits Stop all effects.
        # Explicit-shortcut hits remain unrestricted (profile author opt-in).
        ELIGIBLE_NAME_TYPES = {"dimmer", "intensity", "speed", "reset"}
        for ch in channels:
            sc = ch.get("shortcut")
            name = (ch.get("name") or "").lower()
            t = ch.get("type")
            if sc in EFFECT_SHORTCUTS:
                targets.append(ch.get("offset", 0))
            elif sc is None and t in ELIGIBLE_NAME_TYPES and any(
                    h in name for h in EFFECT_NAME_HINTS):
                targets.append(ch.get("offset", 0))
        if not targets:
            continue
        try:
            uni = int(f.get("dmxUniverse", 1) or 1)
            addr = int(f.get("dmxStartAddr", 1) or 1)
            buf = engine.get_universe(uni)
            for off in targets:
                if 0 <= addr - 1 + off < 512:
                    buf.set_channel(addr + off, 0)
                    written += 1
            killed += 1
        except Exception:
            log.warning("kill-effects: fixture %s failed", fid, exc_info=True)
            skipped.append(fid)
    return jsonify(ok=True, killed=killed, channelsWritten=written, skipped=skipped)


@app.post("/api/fixtures/<int:fid>/channel-write")
def api_fixture_channel_write(fid):
    """Write raw bytes to specific channel offsets on a DMX fixture.

    Body: {writes: {<offset>: <byte 0-255>, ...}}
    Offset is 0-based from the fixture's `dmxStartAddr`. Used by the
    mobile Fixtures-page shortcut renderer (#888) which already knows
    the right channel offset from the profile, so the server doesn't
    re-walk the channel_map.

    Returns: {ok, written: int}. Fixtures held by a claim writer are
    rejected with 423 to avoid clobbering an in-flight mover-control
    session.
    """
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    if f.get("isCalibrating"):
        return jsonify(err="Fixture is being calibrated"), 423
    snap = _claim_arbiter.snapshot()
    if _claim_arbiter.is_muted(fid, snap):
        return jsonify(err="Fixture is held by a claim writer"), 423
    if not (_artnet.running or _sacn.running):
        return jsonify(err="DMX engine not running"), 503
    body = request.get_json(silent=True) or {}
    writes = body.get("writes") or {}
    if not isinstance(writes, dict) or not writes:
        return jsonify(err="writes must be a non-empty dict"), 400
    uni = int(f.get("dmxUniverse", 1) or 1)
    addr = int(f.get("dmxStartAddr", 1) or 1)
    engine = _artnet if _artnet.running else _sacn
    try:
        buf = engine.get_universe(uni)
    except Exception:
        return jsonify(err="DMX engine not running"), 503
    written = 0
    for k, v in writes.items():
        try:
            off = int(k)
            val = max(0, min(255, int(v)))
        except (TypeError, ValueError):
            continue
        if 0 <= addr - 1 + off < 512:
            buf.set_channel(addr + off, val)
            written += 1
    return jsonify(ok=True, written=written)


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
    # #622 — refuse to write unless the engine is running. Previously
    # this lazy-created a universe buffer and silently returned OK; the
    # fixture wouldn't move but keep-alive frames would start once the
    # engine came up later.
    if not _artnet.running:
        return jsonify(err="Art-Net engine not running — start it from "
                            "Settings → DMX Engine before testing a fixture"), 503
    try:
        uni_buf = _artnet.get_universe(uni)
    except Exception:
        return jsonify(err="Art-Net engine not running"), 503
    profile = {"channel_map": prof_info.get("channel_map"),
               "channels": prof_info.get("channels", [])}
    pan = body.get("pan")
    tilt = body.get("tilt")
    dimmer = body.get("dimmer")
    pan_tilt_written = False
    # Only update pan/tilt if provided and non-negative (skip when -1)
    if pan is not None and pan >= 0 and tilt is not None and tilt >= 0:
        uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
        pan_tilt_written = True
        # #806 — raw-DMX override: we don't have a clean stage-frame
        # aim vector for this slider write. Mark the canonical slot
        # null so the next read goes back through the sphere once and
        # caches a fresh result. Calibrate-end on a fixture that just
        # got DMX-test'd will surface "aim_unresolvable" if the sphere
        # also can't resolve, which is the correct behaviour (the
        # operator overrode the head with raw DMX; canonical aim is
        # legitimately unknown).
        _set_canonical_aim_stage(fid, None)
    ch_map = prof_info.get("channel_map", {})
    # Set dimmer if provided
    if dimmer is not None and "dimmer" in ch_map:
        uni_buf.set_channel(addr + ch_map["dimmer"], int(dimmer * 255))
    # #842 — centralized RGB write. Pre-fix this site duplicated the
    # `if RGB then write components else if wheel-only then
    # rgb_to_wheel_slot` ladder that #842 moved into
    # `set_fixture_rgb`. Now the dmx-test endpoint feeds the same
    # helper as the playback / track / show paths so wheel-only
    # fixtures also pick the right slot when the operator drives
    # them via the test endpoint.
    rgb_provided = any(body.get(c) is not None
                       for c in ("red", "green", "blue"))
    color_wheel_written = False
    if rgb_provided:
        r_in = int((body.get("red") or 0.0) * 255)
        g_in = int((body.get("green") or 0.0) * 255)
        b_in = int((body.get("blue") or 0.0) * 255)
        uni_buf.set_fixture_rgb(addr, r_in, g_in, b_in,
                                 {"channel_map": ch_map,
                                  "channels": prof_info.get("channels", [])})
        # #842 — set_fixture_rgb writes the wheel slot for wheel-only
        # profiles. Mark it as explicitly set so the channel-defaults
        # loop below doesn't clobber it back to white.
        if not any(c in ch_map for c in ("red", "green", "blue")) \
                and "color-wheel" in ch_map:
            color_wheel_written = True
    # White + strobe pass-throughs (set_fixture_rgb only handles RGB
    # + wheel; white and strobe are separate channels with no
    # cross-profile dispatch needed).
    for ch_name in ("white", "strobe"):
        if ch_name in ch_map:
            val = body.get(ch_name)
            if val is not None:
                uni_buf.set_channel(addr + ch_map[ch_name], int(val * 255))
    # Apply profile channel defaults for any channel not explicitly set above
    # (strobe open, color wheel white, etc.) so the beam is visible.
    # #702 Bug D — when set_fixture_pan_tilt has written 16-bit pan/tilt,
    # the LSBs live in pan-fine / tilt-fine channels. The defaults loop
    # below MUST exclude them, otherwise the profile defaults (typically
    # 128 = mid) clobber the LSB and the operator-driven aim drops to
    # 8-bit precision (≈2° per coarse step on a 540° pan, vs ≈0.008°
    # at full 16-bit).
    explicitly_set = {"pan", "tilt", "dimmer"}
    if pan_tilt_written:
        explicitly_set.update({"pan-fine", "tilt-fine"})
    for ch_name in ("red", "green", "blue", "white", "strobe"):
        if body.get(ch_name) is not None:
            explicitly_set.add(ch_name)
    if color_wheel_written:
        explicitly_set.add("color-wheel")
    for ch in prof_info.get("channels", []):
        ch_type = ch.get("type", "")
        default = ch.get("default")
        if default is not None and default > 0 and ch_type not in explicitly_set:
            uni_buf.set_channel(addr + ch.get("offset", 0), int(default))
    return jsonify(ok=True)


def _resolve_dmx_fixture_engine(fid):
    """Shared helper for the lamp/beam/blackout endpoints. Returns
    ``(fixture, engine, prof_info, uni, addr)`` or a Flask response
    tuple to short-circuit out of the caller."""
    f = next((f for f in _fixtures if f["id"] == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return None, jsonify(err="DMX fixture not found"), 404
    if f.get("isCalibrating"):
        return None, jsonify(err="Fixture is being calibrated"), 423
    pid = f.get("dmxProfileId")
    prof_info = _profile_lib.channel_info(pid) if pid else None
    if not prof_info:
        return None, jsonify(err="Fixture has no profile"), 400
    if not _artnet.running and not _sacn.running:
        return None, jsonify(err="Art-Net engine not running — start it from "
                                  "Settings → DMX Engine"), 503
    engine = _artnet if _artnet.running else _sacn
    uni = int(f.get("dmxUniverse", 1))
    addr = int(f.get("dmxStartAddr", 1))
    return (f, engine, prof_info, uni, addr), None, None


@app.post("/api/fixtures/<int:fid>/lamp")
def api_fixture_lamp(fid):
    """#737 — turn a fixture's lamp on or off in a profile-aware way.

    Body: ``{on: bool}``. The helper deals with every per-profile
    quirk (RGB-only without a master dimmer, colour-wheel-only with
    closed-shutter default, hybrid RGB+wheel filters) so call sites
    don't have to.
    """
    state, err, code = _resolve_dmx_fixture_engine(fid)
    if err is not None:
        return err, code
    f, engine, prof_info, uni, addr = state
    body = request.get_json(silent=True) or {}
    on = bool(body.get("on", True))
    try:
        _set_fixture_lamp(engine, uni, addr, on, prof_info)
    except Exception as e:
        log.warning("/api/fixtures/%d/lamp failed: %s", fid, e)
        return jsonify(err="dmx_write_failed", detail=str(e)), 500
    return jsonify(ok=True, on=on)


@app.post("/api/fixtures/<int:fid>/beam")
def api_fixture_beam(fid):
    """#737 — set a fixture's beam intensity 0..1 in a profile-aware way.

    Body: ``{dim: 0..1}``. Routes through dimmer when present, scales
    RGB on RGB-only fixtures, falls back to lamp on/off on wheel-only
    fixtures with no dimmer."""
    state, err, code = _resolve_dmx_fixture_engine(fid)
    if err is not None:
        return err, code
    f, engine, prof_info, uni, addr = state
    body = request.get_json(silent=True) or {}
    try:
        dim = float(body.get("dim", 1.0))
    except (TypeError, ValueError):
        return jsonify(err="dim must be a number 0..1"), 400
    try:
        _set_fixture_beam(engine, uni, addr, dim, prof_info)
    except Exception as e:
        log.warning("/api/fixtures/%d/beam failed: %s", fid, e)
        return jsonify(err="dmx_write_failed", detail=str(e)), 500
    return jsonify(ok=True, dim=max(0.0, min(1.0, dim)))


@app.post("/api/fixtures/<int:fid>/blackout")
def api_fixture_blackout(fid):
    """#737 — drive a fixture into its safe state (dimmer 0, shutter
    closed if present, strobe off, RGB 0). Used by Stop-All and the
    SMART error / cancel parking path. Idempotent."""
    state, err, code = _resolve_dmx_fixture_engine(fid)
    if err is not None:
        return err, code
    f, engine, prof_info, uni, addr = state
    try:
        _set_fixture_blackout(engine, uni, addr, prof_info)
    except Exception as e:
        log.warning("/api/fixtures/%d/blackout failed: %s", fid, e)
        return jsonify(err="dmx_write_failed", detail=str(e)), 500
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


# ── Camera settings + auto-tune (#623) ────────────────────────────────

import camera_settings as _cam_settings

# In-memory + on-disk slot registry. Structure:
#   { "<fixture_id>": { "<slot_name>": { "controls": {...}, "intent": "..." } } }
_camera_settings_slots = _load("camera_settings_slots", default={})

_auto_tune_jobs = {}  # fixture_id (str) → job dict (result + status)

# #685 follow-up — per-fixture cancel hint. Set by the cancel route; the
# iteration loop checks it between iterations and bails out cleanly so
# the device lock is released and the camera returns to a usable state.
_auto_tune_cancel: dict = {}

# #685 — per-camera device lock. Live-preview poller (1 Hz) and the
# auto-tune iteration loop both pull JPEGs from the same V4L2 device on
# the camera node. Without serialisation they race: the loop applies a
# control write between captures, the preview tries to read mid-write,
# the camera-node /snapshot returns 503 ("capture failed"), and the SPA
# surfaces it as the misleading "camera offline?" toast.  The lock is
# acquire-with-timeout so the live preview shows a stale frame instead
# of blocking the SPA when auto-tune is running.
_camera_device_locks: dict = {}
_camera_device_locks_meta_lock = threading.Lock()


def _get_camera_device_lock(camera_ip):
    """Return a process-wide threading.Lock keyed to ``camera_ip``.

    Lazy-initialised; multiple tabs / fixtures pointing at the same
    physical camera node share one lock so the V4L2 device only sees one
    concurrent capture, matching the camera node's actual single-stream
    capability. Use ``acquire(timeout=...)`` rather than blocking forever
    so a stuck capture doesn't hang every other request indefinitely.
    """
    if not camera_ip:
        return None
    with _camera_device_locks_meta_lock:
        lk = _camera_device_locks.get(camera_ip)
        if lk is None:
            lk = threading.Lock()
            _camera_device_locks[camera_ip] = lk
    return lk


def _classify_camera_fetch_error(exc):
    """#685 — bucket a snapshot-fetch exception into one of the typed
    failure modes the SPA renders into operator-facing remedy hints.

    Returns ``(errType, message)``. The bucket names match the issue's
    acceptance-criteria taxonomy: ``camera-unreachable``,
    ``capture-timeout``, ``capture-busy``, ``capture-failed``. Anything
    we can't classify lands in ``capture-failed`` so the SPA at least
    shows the underlying exception.
    """
    import socket
    import urllib.error
    msg = str(exc) or exc.__class__.__name__
    if isinstance(exc, urllib.error.HTTPError):
        # 503 from the camera node maps to capture-busy — its capture
        # endpoints return 503 specifically when V4L2 read fails after
        # retries (firmware/orangepi/camera_server.py).
        if exc.code == 503:
            return ("capture-busy", "Camera capture device busy — retrying")
        if exc.code == 404:
            return ("capture-failed", f"Camera endpoint missing: {msg}")
        return ("capture-failed", f"Camera returned HTTP {exc.code}: {msg}")
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.timeout):
            return ("capture-timeout", "Camera capture timed out")
        return ("camera-unreachable",
                f"Camera unreachable ({reason or msg})")
    if isinstance(exc, socket.timeout):
        return ("capture-timeout", "Camera capture timed out")
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, OSError)):
        return ("camera-unreachable", f"Camera unreachable ({msg})")
    return ("capture-failed", msg)


def _camera_fixture(fid):
    """Return the camera-type fixture record for `fid`, or None."""
    return next((f for f in _fixtures
                 if f.get("id") == int(fid) and f.get("fixtureType") == "camera"),
                None)


@app.get("/api/cameras/<int:fid>/settings")
def api_camera_settings_get(fid):
    """Proxy V4L2 controls from the camera node. Returns the raw
    ``{controls, saved}`` plus stored slots for this fixture."""
    f = _camera_fixture(fid)
    if not f:
        return jsonify(err="Camera fixture not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    try:
        raw = _cam_settings.camera_controls_get(ip, cam_idx)
    except Exception as e:
        return jsonify(err=f"Camera controls query failed: {e}"), 503
    slots = _camera_settings_slots.get(str(fid), {})
    return jsonify(ok=True, cameraId=fid, cameraIp=ip, cameraIdx=cam_idx,
                   controls=raw.get("controls", []),
                   saved=raw.get("saved", {}),
                   slots=slots)


@app.post("/api/cameras/<int:fid>/settings")
def api_camera_settings_set(fid):
    """Apply V4L2 controls. Body: ``{controls: {name: value, ...},
    slot?: "name"}``. When ``slot`` is supplied, the applied set is also
    persisted in the fixture's slot registry so callers can recall it."""
    f = _camera_fixture(fid)
    if not f:
        return jsonify(err="Camera fixture not found"), 404
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    controls = body.get("controls") or {}
    if not isinstance(controls, dict) or not controls:
        return jsonify(err="controls must be a non-empty object"), 400
    try:
        r = _cam_settings.camera_controls_set(ip, cam_idx, controls)
    except Exception as e:
        return jsonify(err=f"Camera control set failed: {e}"), 503
    slot_name = body.get("slot")
    if slot_name:
        slots = _camera_settings_slots.setdefault(str(fid), {})
        slot_entry = {"controls": dict(r.get("applied") or controls),
                      "intent": body.get("intent", "general")}
        # #683 — capture a thumbnail of the frame AT the moment the slot
        # was saved so the SPA's before/after compare works without
        # a live camera. Best-effort: skip silently on any failure so
        # slot save never blocks on thumbnail capture.
        thumb = _capture_slot_thumbnail(ip, cam_idx)
        if thumb:
            slot_entry["thumbnail"] = thumb
        slots[slot_name] = slot_entry
        _save("camera_settings_slots", _camera_settings_slots)
    return jsonify(ok=True, applied=r.get("applied", {}))


def _capture_slot_thumbnail(ip, cam_idx, max_bytes=80_000):
    """#683 — fetch the camera node's current snapshot, base64 it, and
    return a `data:image/jpeg;base64,…` string when the payload fits in
    ``max_bytes``. Returns None on any failure or when the snapshot is
    too large to store inline in settings.json.
    """
    try:
        import base64
        url = f"http://{ip}:5000/snapshot?cam={cam_idx}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
    except Exception:
        return None
    if not data or len(data) > max_bytes:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


@app.get("/api/cameras/<int:fid>/settings/slots")
def api_camera_settings_slots_list(fid):
    """List the stored slots for one camera fixture."""
    if not _camera_fixture(fid):
        return jsonify(err="Camera fixture not found"), 404
    return jsonify(ok=True, slots=_camera_settings_slots.get(str(fid), {}))


@app.post("/api/cameras/<int:fid>/settings/slots/<name>/activate")
def api_camera_settings_slot_activate(fid, name):
    """Apply a stored slot's controls to the camera."""
    f = _camera_fixture(fid)
    if not f:
        return jsonify(err="Camera fixture not found"), 404
    slots = _camera_settings_slots.get(str(fid), {})
    slot = slots.get(name)
    if not slot:
        return jsonify(err=f"Slot '{name}' not found"), 404
    ip = f.get("cameraIp")
    cam_idx = f.get("cameraIdx", 0)
    try:
        r = _cam_settings.camera_controls_set(ip, cam_idx,
                                               slot["controls"])
    except Exception as e:
        return jsonify(err=f"Slot activation failed: {e}"), 503
    return jsonify(ok=True, applied=r.get("applied", {}), slot=name)


@app.delete("/api/cameras/<int:fid>/settings/slots/<name>")
def api_camera_settings_slot_delete(fid, name):
    """Forget a slot."""
    slots = _camera_settings_slots.get(str(fid), {})
    if name not in slots:
        return jsonify(err=f"Slot '{name}' not found"), 404
    slots.pop(name, None)
    if not slots:
        _camera_settings_slots.pop(str(fid), None)
    _save("camera_settings_slots", _camera_settings_slots)
    return jsonify(ok=True)


@app.post("/api/cameras/<int:fid>/settings/auto-tune")
def api_camera_settings_auto_tune(fid):
    """Run the auto-tune loop. Synchronous; iterations run in this
    request's thread. Returns the full before/after/history trace.

    Body fields:
      * ``intent``         "general" | "beam" | "aruco" | "yolo"
      * ``maxIterations``  default 6
      * ``saveSlot``       optional slot name to persist the tuned set
      * ``evaluator``      "heuristic" (default, always works) |
                            "ai" (local VLM via Ollama — no cloud) |
                            "auto" (prefer AI, fall back to heuristic)
    """
    f = _camera_fixture(fid)
    if not f:
        return jsonify(err="Camera fixture not found"), 404
    if _cv is None:
        return jsonify(err="CVEngine not available (needed for snapshots)"), 503
    ip = f.get("cameraIp")
    if not ip:
        return jsonify(err="Camera has no IP"), 400
    cam_idx = f.get("cameraIdx", 0)
    body = request.get_json(silent=True) or {}
    intent = body.get("intent", "general")
    max_it = int(body.get("maxIterations", 6))
    evaluator_mode = body.get("evaluator", "analyzer")
    # #685 follow-up — operator-selectable VLM input resolution. Tune
    # modal exposes 3 presets (Tiny 320 / Standard 640 / Detailed 960)
    # so AI mode can trade inference time for image detail. None falls
    # back to the module default (640).
    try:
        resize_long_side = (int(body["resizeLongSide"])
                              if "resizeLongSide" in body else None)
    except (TypeError, ValueError):
        resize_long_side = None
    if resize_long_side is not None:
        # Clamp to the supported preset range so a malformed request
        # can't ask for a 4 K send to the VLM.
        resize_long_side = max(160, min(1280, resize_long_side))
    # #685 follow-up — operator-selected model for AI mode. Per-run
    # `model` override in the body wins over the persisted setting,
    # which wins over the env default. Heuristic mode ignores it.
    chosen_model = (body.get("model")
                     or _settings.get("aiAutoTuneModel")
                     or _cam_settings._OLLAMA_MODEL)

    def _snap(ip_, idx_):
        # 30 s gives the Pi headroom when it's warming up a YOLO model or
        # servicing a parallel depth scan. Auto-tune is not latency-
        # sensitive on the orchestrator side — client XHR timeout is 5 min.
        # One-shot retry covers transient V4L2 device hangs that the Pi's
        # driver recovers from after a short release pause (empirically
        # 1-5 s is enough on the sample rig).
        # #685 — hold the per-camera device lock across the snapshot so
        # the 1 Hz live-preview poller can't race the iteration. Lock
        # release sits in `finally` so a snapshot exception still hands
        # the lock back. Acquire timeout 30 s — auto-tune is the primary
        # user during its run; the preview can wait or show stale.
        lock_ = _get_camera_device_lock(ip_)
        acquired_ = False
        if lock_ is not None:
            acquired_ = lock_.acquire(timeout=30.0)
        try:
            try:
                return _cv.fetch_snapshot(ip_, idx_, timeout=30)
            except Exception as e:
                err_type, _ = _classify_camera_fetch_error(e)
                # capture-busy (503 from camera node) typically clears in
                # 200-500 ms once the V4L2 driver re-syncs. Bigger backoff
                # for camera-unreachable / capture-timeout — those tend
                # to need the device a few seconds to recover.
                back_off = 0.2 if err_type == "capture-busy" else 3.0
                log.warning("auto-tune: snapshot failed (%s; type=%s) — "
                            "pausing %.1f s and retrying once",
                            e, err_type, back_off)
                time.sleep(back_off)
                return _cv.fetch_snapshot(ip_, idx_, timeout=30)
        finally:
            if acquired_ and lock_ is not None:
                lock_.release()

    # #685 follow-up — cancel hook.  The cancel route flips the flag;
    # the iteration loop checks it between iterations.
    _auto_tune_cancel[str(fid)] = False
    def _is_cancelled():
        return bool(_auto_tune_cancel.get(str(fid)))
    # #685 follow-up — initialise the live job + log buffer BEFORE the
    # loop runs so the SPA's status poller sees state from the first
    # iteration. The auto-tune route is synchronous in its request
    # thread, but Flask serves each request in its own thread, so a
    # parallel GET /auto-tune/status can read this dict mid-run.
    job_state = {
        "status": "running",
        "fid": fid,
        "intent": intent,
        "evaluator": evaluator_mode,
        "maxIterations": max_it,
        "startedAt": time.time(),
        "log": [],
    }
    _auto_tune_jobs[str(fid)] = job_state

    def _emit(level, msg):
        from datetime import datetime
        job_state["log"].append({
            "ts": datetime.utcnow().strftime("%H:%M:%S"),
            "level": level,
            "msg": msg,
        })
        # Cap log length so a long-running tune doesn't unbounded-grow.
        if len(job_state["log"]) > 500:
            del job_state["log"][:100]

    def _progress_cb(info):
        # Translate the auto_tune_loop progress events into operator-
        # friendly log lines. Falls through unknown stages so future
        # additions still surface, just generically.
        try:
            stage = info.get("stage", "")
            it = info.get("iteration")
            if stage == "baseline":
                _emit("info",
                      f"Baseline score {info.get('score'):.1f}/100 "
                      f"applied: {info.get('applied') or {}}")
            elif stage == "iterating":
                applied = info.get("applied") or {}
                changed = ", ".join(f"{k}={v}" for k, v in applied.items())
                _emit("info",
                      f"Iter {it}/{max_it}: score {info.get('score'):.1f}"
                      + (f" - {changed}" if changed else ""))
                notes = info.get("notes") or []
                for n in notes[:3]:
                    _emit("info", f"    · {n}")
            elif stage == "converged":
                _emit("info", f"Iter {it}/{max_it}: converged "
                                f"(score {info.get('score'):.1f})")
            else:
                _emit("info", f"{stage} {info}")
        except Exception:
            pass

    resize_label = (str(resize_long_side) + " px"
                     if resize_long_side else "default")
    _emit("info",
          f"Auto-tune started: intent={intent} evaluator={evaluator_mode} "
          f"maxIter={max_it} model={chosen_model} "
          f"vlmResize={resize_label}")
    try:
        result = _cam_settings.auto_tune_loop(
            ip, cam_idx, intent,
            fetch_snapshot_fn=_snap,
            max_iterations=max_it,
            evaluator_mode=evaluator_mode,
            cancel_check=_is_cancelled,
            progress_cb=_progress_cb,
            resize_long_side=resize_long_side,
            model=chosen_model,
        )
    except _cam_settings.AutoTuneCancelled:
        _emit("warn", "Cancelled by operator")
        job_state["status"] = "cancelled"
        _auto_tune_cancel.pop(str(fid), None)
        return jsonify(ok=False, err="Auto-tune cancelled by operator",
                        errType="cancelled"), 499
    except Exception as e:
        _emit("err", f"Auto-tune failed: {e}")
        job_state["status"] = "error"
        log.exception("auto-tune fid=%d failed", fid)
        _auto_tune_cancel.pop(str(fid), None)
        return jsonify(err=f"Auto-tune failed: {e}"), 500
    _auto_tune_cancel.pop(str(fid), None)
    iters_run = max(0, len(result.get("history") or []) - 1)
    _emit("info", f"Auto-tune completed in "
                    f"{(time.time() - job_state['startedAt']):.1f} s "
                    f"after {iters_run}/{max_it} iterations")
    _emit("info", f"Final score "
                    f"{(result.get('after') or {}).get('score', '?')}"
                    f" / 100  applied: {result.get('applied') or {}}")

    slot_name = body.get("saveSlot")
    if slot_name:
        slots = _camera_settings_slots.setdefault(str(fid), {})
        slot_entry = {"controls": dict(result.get("applied") or {}),
                      "intent": intent,
                      "score": result.get("after", {}).get("score")}
        # #683 — attach a thumbnail captured at the end of the tune run
        # (camera state already matches the slot's saved controls).
        thumb = _capture_slot_thumbnail(ip, cam_idx)
        if thumb:
            slot_entry["thumbnail"] = thumb
        slots[slot_name] = slot_entry
        _save("camera_settings_slots", _camera_settings_slots)

    job_state.update(result)
    job_state["status"] = "done"
    job_state["timestamp"] = time.time()
    job_state["slot"] = slot_name
    return jsonify(ok=True, **result, intent=intent, slot=slot_name)


@app.get("/api/calibration/traces")
def api_cal_traces():
    """#686 — list recent cal-trace NDJSON files.

    Returns a list of trace metadata sorted newest-first. Optional
    ``?fid=<id>`` filter restricts to a single fixture. Useful for the
    SPA "open last cal trace" UI; ``?limit=<n>`` defaults to 50.
    """
    fid = request.args.get("fid", type=int)
    limit = request.args.get("limit", default=50, type=int)
    if not CAL_TRACES_DIR.exists():
        return jsonify(traces=[])
    pattern = f"fid{fid}-*.ndjson" if fid is not None else "*.ndjson"
    files = sorted(CAL_TRACES_DIR.glob(pattern),
                   key=lambda p: p.stat().st_mtime,
                   reverse=True)[:max(1, int(limit))]
    out = []
    for p in files:
        try:
            stat = p.stat()
        except OSError:
            continue
        # Cheap header peek — first line only.
        header = {}
        try:
            with p.open("r", encoding="utf-8") as f:
                first = f.readline()
            if first:
                header = json.loads(first)
        except Exception:
            pass
        out.append({
            "path": str(p),
            "name": p.name,
            "sizeBytes": stat.st_size,
            "modifiedAt": stat.st_mtime,
            "fid": header.get("fid"),
            "mode": header.get("mode"),
            "schema": header.get("schema"),
        })
    return jsonify(traces=out)


@app.get("/api/calibration/traces/<path:name>")
def api_cal_trace_file(name):
    """Stream a cal-trace NDJSON by filename (no path traversal)."""
    safe = Path(name).name  # strip directory components
    candidate = CAL_TRACES_DIR / safe
    if not candidate.is_file():
        return jsonify(err="trace not found"), 404
    return send_from_directory(str(CAL_TRACES_DIR), safe,
                                mimetype="application/x-ndjson")


@app.get("/api/cameras/<int:fid>/settings/auto-tune/status")
def api_camera_settings_auto_tune_status(fid):
    """#685 follow-up — live status for the in-flight auto-tune.

    The auto-tune POST is synchronous in its request thread; this
    endpoint runs in a separate Flask thread and reads the shared
    ``_auto_tune_jobs[fid]`` dict the worker mutates. Returns the
    current ``status`` (running / done / cancelled / error), the log
    tail (default last 50 entries; ``?since=<idx>`` returns entries
    appended after that index), and iteration count so the SPA's Tune
    modal can render a scrollable log pane mirroring the cal wizard.
    """
    job = _auto_tune_jobs.get(str(fid))
    if not job:
        return jsonify(ok=True, status="idle", log=[], total=0)
    since = request.args.get("since", default=0, type=int)
    full_log = job.get("log") or []
    tail = full_log[since:] if since >= 0 else full_log[-50:]
    history = job.get("history") or []
    return jsonify(ok=True,
                   status=job.get("status", "running"),
                   intent=job.get("intent"),
                   evaluator=job.get("evaluator"),
                   maxIterations=job.get("maxIterations"),
                   iterations=max(0, len(history) - 1),
                   startedAt=job.get("startedAt"),
                   log=tail,
                   total=len(full_log))


@app.post("/api/cameras/<int:fid>/settings/auto-tune/cancel")
def api_camera_settings_auto_tune_cancel(fid):
    """#685 follow-up — set the cancel flag for an in-flight auto-tune.

    The auto-tune route is synchronous in its request thread, so cancel
    here just flips the per-fixture flag the iteration loop checks
    between iterations. Returns 200 even when there's no active run so
    the SPA's best-effort cancel never reports a misleading error.
    """
    if not _camera_fixture(fid):
        return jsonify(err="Camera fixture not found"), 404
    _auto_tune_cancel[str(fid)] = True
    log.info("auto-tune cancel requested for fid=%d", fid)
    return jsonify(ok=True)


@app.get("/api/cameras/settings/evaluator-status")
def api_camera_settings_evaluator_status():
    """Report which evaluator modes are available on this orchestrator.
    The SPA uses this to grey out the AI option when Ollama isn't running
    so the operator doesn't hit a runtime error from an invisible dep.
    """
    ok, err = _cam_settings._ollama_available()
    return jsonify(ok=True,
                   modes={
                       "heuristic": {"available": True},
                       "ai": {"available": ok, "err": err,
                              "model": _cam_settings._OLLAMA_MODEL,
                              "url": _cam_settings._OLLAMA_URL},
                   })


# ── Ollama runtime (#623) — mirrors depth_runtime pattern (#598) ──────

try:
    import ollama_runtime as _ollama_rt
except Exception as _e:  # pragma: no cover
    _ollama_rt = None
    log.warning("ollama_runtime not importable: %s", _e)


@app.get("/api/ollama-runtime/status")
def api_ollama_runtime_status():
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    st = _ollama_rt.status()
    # #685 follow-up — surface the operator-selected active model so the
    # Settings card can show "tune-active=qwen2.5vl:3b" even when the
    # env override is set to something else.
    st["activeModel"] = (_settings.get("aiAutoTuneModel")
                          or st.get("model"))
    return jsonify(ok=True, **st)


@app.get("/api/ollama-runtime/models")
def api_ollama_runtime_models():
    """#685 follow-up — list every model Ollama has pulled locally so
    the Settings AI-Runtime card can render a dropdown.

    Returns ``{ok, models: [{name, sizeMb, vision, modifiedAt}], active}``
    where ``active`` is whichever model auto-tune will use right now
    (operator override from settings, falling back to env default)."""
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    models = _ollama_rt.list_models()
    active = _settings.get("aiAutoTuneModel") or _ollama_rt.OLLAMA_MODEL
    return jsonify(ok=True, models=models, active=active)


@app.post("/api/ollama-runtime/install")
def api_ollama_runtime_install():
    """Kick off Ollama install + model pull in the background.
    Body: ``{force?: bool}`` — force re-pulls the model even if present.
    Poll /install-status for progress."""
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    body = request.get_json(silent=True) or {}
    res = _ollama_rt.start_install(force=bool(body.get("force", False)))
    code = 200 if res.get("ok") else 409
    return jsonify(**res), code


@app.get("/api/ollama-runtime/install-status")
def api_ollama_runtime_install_status():
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    return jsonify(ok=True, **_ollama_rt.progress())


@app.post("/api/ollama-runtime/warmup")
def api_ollama_runtime_warmup():
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    ok = _ollama_rt.warmup()
    return jsonify(ok=ok, **_ollama_rt.status())


@app.post("/api/ollama-runtime/test")
def api_ollama_runtime_test():
    """Settings → Test button. Sends a fixed prompt and reports response
    + latency. Used as the canonical proof the runtime works end-to-end.

    Uses the operator-selected model (settings.aiAutoTuneModel) when
    set, otherwise falls back to the env default. Body can override
    per call via {model: "..."}."""
    if _ollama_rt is None:
        return jsonify(ok=False, err="ollama_runtime module not bundled"), 500
    body = request.get_json(silent=True) or {}
    model = (body.get("model")
             or _settings.get("aiAutoTuneModel")
             or _ollama_rt.OLLAMA_MODEL)
    return jsonify(_ollama_rt.run_test(model=model))


# ── AI helpers — aggregate status + boot warm-up (settings page) ──────

def _ai_engine_descriptors():
    """One descriptor per AI helper the orchestrator can host. Each entry
    reports: installed (bool), installing (bool — install job in flight),
    running (bool — process up), warm (bool — ready for low-latency call),
    plus engine-specific extras. Engines that aren't bundled into the
    PyInstaller build (depth_runtime / ollama_runtime missing) report
    installed=False with a reason, so the SPA can still render a row."""
    out = []

    # ZoeDepth host runtime
    if _depth_runtime is None:
        out.append({"id": "zoedepth", "name": "ZoeDepth (host)",
                    "installed": False, "installing": False,
                    "running": False, "warm": False,
                    "err": "module not bundled"})
    else:
        st = _depth_runtime.status()
        prog = _depth_runtime.install_progress() or {}
        installing = bool(prog.get("running"))
        out.append({
            "id": "zoedepth", "name": "ZoeDepth (host)",
            "installed": bool(st.get("installed")),
            "installing": installing,
            "running":   bool(st.get("runnerRunning")),
            "warm":      bool(st.get("warm")),
            "warmedAt":  st.get("warmedAt"),
            "err":       st.get("lastError"),
            "model":     st.get("model"),
            "sizeMb":    st.get("sizeMb"),
            "progress":  prog,
        })

    # Ollama LLM
    if _ollama_rt is None:
        out.append({"id": "ollama", "name": "Ollama LLM",
                    "installed": False, "installing": False,
                    "running": False, "warm": False,
                    "err": "module not bundled"})
    else:
        st = _ollama_rt.status()
        prog = st.get("progress") or {}
        installing = prog.get("phase") in ("install-ollama", "pull-model")
        out.append({
            "id": "ollama", "name": "Ollama LLM",
            "installed": bool(st.get("installed")),
            "installing": installing,
            "running":   bool(st.get("running")),
            "warm":      bool(st.get("warm")),
            "warmedAt":  st.get("warmedAt"),
            "err":       st.get("lastError"),
            "model":     st.get("model"),
            "progress":  prog,
        })

    return out


def _ai_helpers_warmup():
    """Runs once at boot. For each installed AI helper, kick a warm-up in
    its own thread so a slow ZoeDepth load doesn't block Ollama (or vice
    versa). Helpers mid-install are skipped — they'll be warmed when the
    operator next opens Settings or hits Test."""
    if _depth_runtime is not None and _depth_runtime.is_installed():
        threading.Thread(
            target=lambda: _ai_warmup_safe("zoedepth", _depth_runtime.warmup),
            daemon=True).start()
    # #687 follow-up — start `ollama serve` if the binary is installed
    # but the daemon isn't currently running. Ownership is tracked
    # inside ollama_runtime so _graceful_dmx_shutdown only kills the
    # daemon when WE spawned it (system-service / menu-bar instances
    # are left alone). Warmup follows once /api/tags answers.
    if _ollama_rt is not None:
        def _ollama_boot():
            try:
                started = _ollama_rt.start_serve(wait_seconds=10.0)
                if started:
                    log.info("AI: started ollama serve (will be stopped at shutdown)")
            except Exception as e:
                log.warning("AI: ollama auto-start failed (%s)", e)
            # #685 follow-up — auto-pull the configured vision model when
            # Ollama is up but the model isn't yet (e.g. operator just
            # upgraded to the new qwen2.5vl:3b default). Same code path
            # the Settings → Install Ollama button uses; progress is on
            # /api/ollama-runtime/install-status. _install_worker now
            # also runs warmup() at the end so a fresh boot lands on
            # "Ready · warm" by the time the operator opens Settings.
            try:
                # #685 architecture decision — boot path no longer
                # auto-pulls any vision model. The deterministic CV
                # `analyzer` evaluator handles auto-tune by default;
                # AI is opt-in. Only fire the legacy auto-pull when
                # SLYLED_INSTALLER_MODEL was explicitly set (env
                # override) AND the model isn't already pulled.
                installer_model = getattr(_ollama_rt, "INSTALLER_MODEL", "")
                if (installer_model
                        and _ollama_rt.is_ollama_running()
                        and not _ollama_rt.has_model(installer_model)):
                    log.info("AI: bootstrap model %s not pulled — kicking "
                              "off background pull", installer_model)
                    _ollama_rt.start_install()
                    return  # _install_worker handles warmup at the end
            except Exception as e:
                log.warning("AI: model auto-pull check failed (%s)", e)
            if _ollama_rt.is_installed():
                _ai_warmup_safe("ollama", _ollama_rt.warmup)
        threading.Thread(target=_ollama_boot, daemon=True).start()


def _ai_warmup_safe(name, fn):
    try:
        ok = fn()
        log.info("AI warmup %s: %s", name, "ok" if ok else "skipped/failed")
    except Exception as e:
        log.warning("AI warmup %s raised: %s", name, e)


@app.get("/api/ai/status")
def api_ai_status():
    """Aggregate status for the Settings → AI Engines card. Always 200,
    so the UI can render even when individual helpers are missing."""
    return jsonify(ok=True, engines=_ai_engine_descriptors())


@app.post("/api/ai/warmup")
def api_ai_warmup():
    """Trigger a fresh warm-up sweep on demand (idempotent)."""
    threading.Thread(target=_ai_helpers_warmup, daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/ai/<engine>/test")
def api_ai_test(engine):
    """Per-engine test harness. Routes to the engine's run_test()."""
    if engine == "zoedepth":
        if _depth_runtime is None:
            return jsonify(ok=False, err="depth_runtime not bundled"), 500
        return jsonify(_depth_runtime.run_test())
    if engine == "ollama":
        if _ollama_rt is None:
            return jsonify(ok=False, err="ollama_runtime not bundled"), 500
        return jsonify(_ollama_rt.run_test())
    return jsonify(ok=False, err=f"unknown engine '{engine}'"), 404


@app.get("/api/cameras/<int:fid>/settings/auto-tune")
def api_camera_settings_auto_tune_last(fid):
    """Return the last auto-tune result for this fixture (if any)."""
    job = _auto_tune_jobs.get(str(fid))
    if not job:
        return jsonify(ok=False, err="No auto-tune run recorded"), 404
    return jsonify(ok=True, **job)


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


# ── Mover park / blackout helpers (kept after #784 PR-7 cal removal) ──
#
# `_park_fixture_at_home` is consumed by the `MoverControlEngine`
# (`park_fn=`) and remote-orient flows. `_targeted_fixture_blackout` is
# its degraded fallback. The rest of the legacy mover-calibration
# pipeline (SMART, battleship, parametric, manual) was deleted under
# #784 PR-7 — the only IK now is the slope-from-home `aim/` package.


_CAMERA_MODELS_CACHE = None


def _load_camera_models():
    """Return the camera-models dict (`{hwDescriptor: {effectiveFovDeg, ...}}`).
    Empty dict on missing/unparseable file. Cached after first read."""
    global _CAMERA_MODELS_CACHE
    if _CAMERA_MODELS_CACHE is not None:
        return _CAMERA_MODELS_CACHE
    path = (Path(__file__).resolve().parent.parent.parent
             / "firmware" / "orangepi" / "camera_models.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _CAMERA_MODELS_CACHE = data.get("models") or {}
    except Exception as e:
        log.debug("camera_models.json unavailable (%s) - falling back "
                  "to manufacturer FOV", e)
        _CAMERA_MODELS_CACHE = {}
    return _CAMERA_MODELS_CACHE


def _effective_fov_for_camera(fixture):
    """#712 Track 1b — look up lens-effective FOV for a camera fixture.

    Manufacturer-spec ``fovDeg`` overstates the real visible region for
    most USB webcams (V4L2 crop + USB pipeline downsampling). The
    registry at ``firmware/orangepi/camera_models.json`` maps the
    camera's ``hwDescriptor`` to a measured-effective FOV. Falls back to
    the fixture's manufacturer ``fovDeg`` when no entry matches.
    """
    nominal = float(fixture.get("fovDeg") or 90)
    hw = fixture.get("hwDescriptor")
    if not hw:
        return nominal
    models = _load_camera_models()
    entry = models.get(hw)
    if not entry:
        return nominal
    eff = entry.get("effectiveFovDeg")
    if eff is None:
        return nominal
    try:
        return float(eff)
    except (TypeError, ValueError):
        return nominal


def _targeted_fixture_blackout(fid):
    """#681-A — zero one fixture's channel window via the engine. Safe
    no-op if the fixture is missing or no engine is running. Preserves
    every other fixture on the universe — replaces the old
    `_hold_dmx(bridge_ip, [0] * 512, ...)` pattern that persistently
    darkened bystander movers for the duration of a cal run.
    """
    try:
        fx = next((f for f in _fixtures if f["id"] == fid), None)
        if not fx:
            return
        engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
        if not engine:
            return
        uni = fx.get("dmxUniverse", 1)
        addr = fx.get("dmxStartAddr", 1)
        pid = fx.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        ch_count = int((info or {}).get("channelCount") or
                       fx.get("dmxChannelCount") or 13)
        engine.get_universe(uni).set_channels(addr, [0] * ch_count)
    except Exception:
        pass


def _park_fixture_pan_tilt_only(fid):
    """#800 (operator clarification 2026-05-03) — pan/tilt-only park.

    Drives the head to its Home anchor without touching the lamp. Used
    by:
      - cold-start parking (existing cold-start rainbow blink owns
        the lamp; we must NOT lamp_off here or we wipe the blink's
        terminal state).
      - claim transition to streaming (the engine pump's `_write_dmx`
        will write `claim.dimmer` on the next tick anyway; lamp_off
        here would cause a one-tick flicker).
      - timeline-end + track-end snap-to-home.

    `_park_fixture_at_home` (full park + `lamp_off`) remains the right
    helper for explicit `/release` paths where idle = home + dark.
    """
    try:
        fx = next((f for f in _fixtures if f["id"] == fid), None)
        if not fx:
            return
        if _aim_get_engine() is None:
            return
        if fx.get("homePanDmx16") is None or fx.get("homeTiltDmx16") is None:
            return
        from aim.park import go_home as _aim_go_home
        _aim_go_home(fid,
                      get_fixtures=lambda: _fixtures,
                      profile_lib=_profile_lib,
                      write_pose=_aim_write_pose,
                      get_engine=_aim_get_engine)
        # #806 — seed the canonical aim store with the home aim direction
        # in stage frame (R · (0, 1, 0)). Without this, the next
        # calibrate-end on a parked head would fall through to the
        # sphere `dmx_to_aim` path and any glitch there reproduces #805.
        pid = fx.get("dmxProfileId")
        prof = _profile_lib.channel_info(pid) if pid else None
        home_aim = _home_aim_stage_vector(fx, prof)
        if home_aim is not None:
            _set_canonical_aim_stage(fid, home_aim)
    except Exception:
        log.debug("park (pan/tilt only) %s failed", fid, exc_info=True)


def _park_fixture_at_home(fid):
    """#691 — park a moving-head fixture at its Set Home (#687) anchor.

    #781 / #782 PR-β rewrite (2026-05-03): the slew goes through the
    canonical angular path (`aim.park.go_home`), not a direct pan/tilt
    DMX write. Home is the angular zero per #784 c3, so the helper
    aims at `(azDeg=0, elDeg=0)` and the AimSphere cell index lands at
    the recorded `(homePanDmx16, homeTiltDmx16)`. Lamps are turned off
    via `lamp_off()` per the #782 operator decision (intensity-class
    only; pan/tilt and non-intensity defaults preserved). Falls back to
    `_targeted_fixture_blackout` only when the fixture lacks a Home
    anchor (which means the angular path can't run).

    For the cold-start / claim-transition cases that should NOT
    lamp_off, see `_park_fixture_pan_tilt_only` (#800 operator
    clarification).
    """
    try:
        fx = next((f for f in _fixtures if f["id"] == fid), None)
        if not fx:
            return
        engine = _aim_get_engine()
        if not engine:
            return
        home_pan = fx.get("homePanDmx16")
        home_tilt = fx.get("homeTiltDmx16")
        if home_pan is None or home_tilt is None:
            _targeted_fixture_blackout(fid)
            return
        # Canonical angular slew to home.
        from aim.park import go_home as _aim_go_home
        _aim_go_home(fid,
                      get_fixtures=lambda: _fixtures,
                      profile_lib=_profile_lib,
                      write_pose=_aim_write_pose,
                      get_engine=_aim_get_engine)
        # #806 — seed the canonical aim store. See note in
        # `_park_fixture_pan_tilt_only`. Without this, calibrate-end
        # post-park reads through the sphere fallback and #805 reopens.
        pid_full = fx.get("dmxProfileId")
        prof_full = _profile_lib.channel_info(pid_full) if pid_full else None
        home_aim = _home_aim_stage_vector(fx, prof_full)
        if home_aim is not None:
            _set_canonical_aim_stage(fid, home_aim)
        # Lamp off via the profile-aware helper (per #780 P3 + #782
        # blackout decision). Reads the same universe buffer the
        # angular path just wrote into.
        try:
            from dmx_profiles import lamp_off
            uni = int(fx.get("dmxUniverse", 1) or 1)
            addr = int(fx.get("dmxStartAddr", 1) or 1)
            pid = fx.get("dmxProfileId")
            info = _profile_lib.channel_info(pid) if pid else None
            if info:
                profile = {"channel_map": info.get("channel_map", {}),
                            "channels":    info.get("channels", [])}
                # Pull the live universe buffer view as a list, mutate
                # the intensity-class channels, then write back.
                buf = engine.get_universe(uni)
                # set_channel is the supported per-byte write — call it
                # for each lamp_off-affected offset. Build a small
                # bytearray to feed lamp_off, then push the changed
                # bytes through set_channel.
                tmp = bytearray(512)
                for ch in (profile.get("channels") or []):
                    off = ch.get("offset", 0)
                    if 0 <= addr - 1 + off < 512:
                        try:
                            tmp[addr - 1 + off] = int(buf.get_channel(addr + off))
                        except Exception:
                            tmp[addr - 1 + off] = 0
                # #857 — pass color=(0,0,0) so lamp_off zeroes RGB
                # bytes in the scratch buffer (with `color=None` it
                # leaves them alone), and extend the write-back
                # allowlist to include the colour channel types.
                # Pre-fix release left R/G/B latched at the operator's
                # last commanded values: next time anything raised the
                # dimmer (show wash, new claim) the ghost colour bled
                # through. Now release of a Home-set fixture matches
                # the no-Home path's clean blackout via
                # `_targeted_fixture_blackout`.
                lamp_off(profile, tmp, addr, color=(0, 0, 0))
                _RELEASE_BLACKOUT_TYPES = (
                    "dimmer", "intensity", "strobe",
                    "red", "green", "blue", "color-wheel",
                )
                for ch in (profile.get("channels") or []):
                    ch_type = ch.get("type", "")
                    if ch_type in _RELEASE_BLACKOUT_TYPES:
                        off = ch.get("offset", 0)
                        if 0 <= addr - 1 + off < 512:
                            buf.set_channel(addr + off, int(tmp[addr - 1 + off]))
        except Exception:
            log.debug("park %d: lamp_off step skipped", fid, exc_info=True)
    except Exception:
        # Fall back to the safe behaviour: black the fixture out.
        _targeted_fixture_blackout(fid)


# #784 PR-7 — cal threads + SMART pipeline + cal/SMART routes deleted (legacy IK pipeline removed).


# #784 PR-7 — legacy aim-angles + sphere_model fallback deleted (legacy IK pipeline removed).

# ── #784 PR-4 — canonical aim endpoint via aim/ package ─────────
#
# `POST /api/mover/<fid>/aim {x,y,z} | {azDeg,elDeg}` routes through
# `desktop/shared/aim/sphere.AimSphere`, which reads ONLY
# `homePanDmx16`/`homeTiltDmx16`, `rotation`, and the profile's
# `dmxToMechanical` block. No legacy IK fallbacks.


# #806 — Canonical aim_stage store. Populated by every writer that has a
# stage-frame aim vector in hand (claim/orient, /api/mover/<fid>/aim,
# park-at-home). Read by calibrate-end / /api/fixtures/live / 3D viz via
# `_mover_current_aim_stage` (defined further down). Eliminates the
# round-trip risk of `dmx_to_aim` / `aim_direction` disagreement that
# surfaced as #805 / #757-B / #748 — when the canonical vector exists
# the read path is a direct dictionary lookup instead of an IK inversion
# that may silently fall through a fallback ladder.
#
# In-memory only; not persisted. On process restart, the engine pump's
# park-at-home (or the first claim/aim/track write) repopulates it before
# calibrate-end can be called. Callers that find a None entry get the
# legacy sphere read path as a migration safety net (track / bake writers
# don't update the canonical store yet — phase 2 of #806).
#
# Defined here (instead of next to `_mover_current_aim_stage`) so the
# `_register_aim_routes(...)` call below can pass `_set_canonical_aim_stage`
# as the route's writer hook.
_canonical_aim_stage = {}   # int(fid) → (vx, vy, vz) | None ("raw-driven")


def _set_canonical_aim_stage(fid, aim_stage):
    """Record the canonical aim direction for moving-head <fid>.

    Pass `None` to mark the slot as 'raw-DMX-driven' (operator overrode
    the head from the DMX-test page; we don't have a clean vector to
    cache and the read path falls back to the sphere). Pass a 3-tuple
    for normal aim writes."""
    if fid is None:
        return
    if aim_stage is None:
        _canonical_aim_stage[int(fid)] = None
        return
    try:
        _canonical_aim_stage[int(fid)] = (
            float(aim_stage[0]), float(aim_stage[1]), float(aim_stage[2]))
    except (TypeError, ValueError, IndexError):
        return


def _get_canonical_aim_stage(fid):
    """Return the canonical aim vector for <fid>, or `None` when no
    canonical write has happened yet (caller falls back to sphere)."""
    return _canonical_aim_stage.get(int(fid))


def _clear_canonical_aim_stage(fid):
    """Drop the canonical aim entry — used on fixture delete / factory
    reset / project import."""
    _canonical_aim_stage.pop(int(fid), None)


def _canonical_aim_from_pan_tilt(fixture, prof_info, pan_norm, tilt_norm):
    """#806 phase 2 — forward IK from a JUST-WRITTEN normalized pan/tilt to
    a stage-frame unit aim vector, used by timeline-bake playback writers
    to populate the canonical store.

    Forward computation (no fallback ladder, no silent swallow). Prefers
    the AimSphere `dmx_to_aim` path when the fixture has Home + Secondary,
    else mount-relative spherical math via the fixture's rotation. Either
    way the result is the direction the head physically points after the
    given (pan_norm, tilt_norm) DMX write — no inverse-IK round-trip.

    Returns `None` only on explicit math failure (caller skips canonical
    update; existing canonical stays in place).
    """
    try:
        if (prof_info
                and fixture.get("homePanDmx16") is not None
                and fixture.get("homeTiltDmx16") is not None
                and fixture.get("homeSecondary")):
            mover_xyz = dict(fixture)
            for c in (_layout.get("children") or []):
                if c.get("id") == fixture.get("id"):
                    mover_xyz["x"] = c.get("x", 0) or 0
                    mover_xyz["y"] = c.get("y", 0) or 0
                    mover_xyz["z"] = c.get("z", 0) or 0
                    break
            from aim.routes import _get_or_build_sphere
            sphere = _get_or_build_sphere(mover_xyz, prof_info)
            pan_dmx16 = int(round(pan_norm * 65535))
            tilt_dmx16 = int(round(tilt_norm * 65535))
            az_deg, el_deg = sphere.dmx_to_aim(pan_dmx16, tilt_dmx16)
            ar = math.radians(az_deg)
            er = math.radians(el_deg)
            cer = math.cos(er)
            return (math.sin(ar) * cer,
                    math.cos(ar) * cer,
                    math.sin(er))
        # Mount-relative forward calc — same math /api/fixtures/live uses
        # for un-canonicalised heads. Forward only; not a fallback for an
        # inverse-IK call that just failed.
        pr_deg = (pan_norm - 0.5) * (fixture.get("panRange") or 540)
        tr_deg = (tilt_norm - 0.5) * (fixture.get("tiltRange") or 270)
        pr = math.radians(pr_deg)
        tr = math.radians(tr_deg)
        cos_t = math.cos(tr)
        dx = math.sin(pr) * cos_t
        dy = math.cos(pr) * cos_t
        dz = -math.sin(tr)
        rot = fixture.get("rotation") or [0, 0, 0]
        if rot[0] == 0 and rot[1] == 0 and rot[2] == 0:
            return (dx, dy, dz)
        from remote_math import euler_xyz_deg_to_matrix, matrix_vec_mul
        R = euler_xyz_deg_to_matrix(rot)
        return matrix_vec_mul(R, (dx, dy, dz))
    except Exception:
        return None


def _home_aim_stage_vector(mover, prof_info):
    """Compute the stage-frame aim direction the head points in when its
    DMX equals (homePanDmx16, homeTiltDmx16). #806 — used by park-at-home
    paths to seed the canonical store *without* going through
    `sphere.dmx_to_aim` (the IK we're eliminating from the read path).

    The math is just the rotated mount-forward direction: home aim in
    stage frame = R_fixture · (0, 1, 0). Mirrors `AimSphere.__init__`.
    Returns `None` when fixture rotation is missing/invalid; caller then
    skips the canonical store seed and falls through to the sphere."""
    try:
        rot = mover.get("rotation") or [0, 0, 0]
        if (not isinstance(rot, (list, tuple))) or len(rot) < 3:
            return None
        if rot[0] == 0 and rot[1] == 0 and rot[2] == 0:
            return (0.0, 1.0, 0.0)
        from remote_math import euler_xyz_deg_to_matrix, matrix_vec_mul
        R = euler_xyz_deg_to_matrix(rot)
        return matrix_vec_mul(R, (0.0, 1.0, 0.0))
    except Exception:
        return None


def _aim_write_pose(uni, addr, pan_dmx16, tilt_dmx16, prof_info):
    """Engine-side DMX writer plugged into aim.routes.register. Mirrors
    the body of `/api/mover/<fid>/aim-angles` (uni_buf.set_fixture_pan_tilt
    with normalised pan/tilt). Kept identical so the new endpoint
    produces identical wire output for the same target."""
    engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
    if engine is None:
        raise RuntimeError("engine_not_running")
    profile = {"channel_map": prof_info.get("channel_map", {}),
               "channels": prof_info.get("channels", [])}
    uni_buf = engine.get_universe(uni)
    uni_buf.set_fixture_pan_tilt(addr,
                                  pan_dmx16 / 65535.0,
                                  tilt_dmx16 / 65535.0,
                                  profile)


def _aim_get_engine():
    if _artnet.running:
        return _artnet
    if _sacn.running:
        return _sacn
    return None


from aim.routes import register as _register_aim_routes  # noqa: E402
from aim.routes import invalidate_sphere as _aim_invalidate_sphere  # noqa: E402
from aim.routes import invalidate_all_spheres as _aim_invalidate_all_spheres  # noqa: E402

_register_aim_routes(
    app,
    get_fixtures=lambda: _fixtures,
    profile_lib=_profile_lib,
    write_pose=_aim_write_pose,
    get_engine=_aim_get_engine,
    # Lambda defers the lookup — `_fixture_is_calibrating` and
    # `_fixture_position` are defined later in this module.
    check_calibrating=lambda fid: _fixture_is_calibrating(fid),
    get_fixture_xyz=lambda fid: _fixture_position(fid),
    # #806 — canonical-aim store hook for /api/mover/<fid>/aim writes.
    set_canonical_aim_fn=_set_canonical_aim_stage,
)


# ── #699 — Verify Fixture Pose wizard ───────────────────────────────────
#
# Operator-driven X/Y/Z calibration before mover-cal. Layout-data drift
# (configured fixture pose differs from physical reality) caps cal
# accuracy regardless of grid algorithm quality. Wizard flow:
#
#   1. SPA opens wizard for fixture <fid>; reads current layout pose +
#      surveyed ArUco marker registry.
#   2. For each marker the operator picks: orchestrator computes
#      (pan_norm, tilt_norm) from current pose's IK, drives beam.
#   3. Operator confirms beam landed ON marker (or nudges in the SPA;
#      observed pan/tilt at convergence are recorded).
#   4. Wizard runs least-squares fit on all observations; returns
#      suggested pose (X, Y, Z) + per-marker residual.
#   5. Operator reviews + applies → layout updates.
#
# State machine kept in-process per fid; no DB. Survives orchestrator
# restart only via the operator clicking Apply.
_fixture_pose_sessions = {}   # str(fid) → {observations: [], created: ts}


# ── Surviving cal-tuning settings shim (#784 PR-7) ─────────────────────
#
# The full SMART pipeline (and its tuning surface) was deleted, but two
# settings are still consumed by code that survived: `maxScanAgeMinutes`
# (`_surface_model_for_cal` warning gate) and `moverClaimTtlS`
# (`_mover_engine` claim-arbiter TTL). We keep a minimal `_cal_tuning`
# read-helper + a tiny SPEC so the existing `/api/settings` endpoint
# still surfaces the values the SPA already renders.

CAL_TUNING_SPEC = {
    "maxScanAgeMinutes": {"type": "number", "default": 10.0,
                           "min": 1.0, "max": 1440.0,
                           "label": "Max scan age (min)"},
    "moverClaimTtlS":   {"type": "number", "default": 12.0,
                           "min": 1.0, "max": 300.0,
                           "label": "Mover claim TTL (s)"},
}


def _cal_tuning(key, default=None):
    """Return the current value for a cal-tuning key, falling back to the
    SPEC default (or the caller's `default`) when no override is set."""
    overrides = _settings.get("calibrationTuning") or {}
    if key in overrides:
        return overrides[key]
    spec = CAL_TUNING_SPEC.get(key)
    if spec is not None and "default" in spec:
        return spec["default"]
    return default


def _validate_cal_tuning(overrides):
    """Validate `overrides` against `CAL_TUNING_SPEC`. Returns
    `(cleaned, errors)`. Unknown keys are dropped silently."""
    cleaned = {}
    errors = []
    if not isinstance(overrides, dict):
        return ({}, ["calibrationTuning must be an object"])
    for k, v in overrides.items():
        spec = CAL_TUNING_SPEC.get(k)
        if spec is None:
            continue  # ignore unknown keys
        try:
            v = float(v) if spec["type"] == "number" else v
        except (TypeError, ValueError):
            errors.append(f"{k}: not a number")
            continue
        lo = spec.get("min")
        hi = spec.get("max")
        if lo is not None and v < lo:
            errors.append(f"{k}: below minimum {lo}")
            continue
        if hi is not None and v > hi:
            errors.append(f"{k}: above maximum {hi}")
            continue
        cleaned[k] = v
    return (cleaned, errors)


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/start")
def api_verify_pose_start(fid):
    """Open a verify-pose session for fixture <fid>. Returns the
    current layout pose + the list of usable floor markers from the
    ArUco registry. Resets any stale observations from a prior run."""
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f or f.get("fixtureType") != "dmx":
        return jsonify(err="DMX fixture not found"), 404
    pos = _fixture_position(fid)
    rotation = f.get("rotation") or [0.0, 0.0, 0.0]
    floor_markers = [
        {"id": int(m["id"]),
         "name": m.get("name") or f"Marker {m['id']}",
         "x": float(m["x"]), "y": float(m["y"]), "z": float(m.get("z", 0.0))}
        for m in _aruco_markers
        if abs(float(m.get("z", 0) or 0)) < 50
        and abs(float(m.get("rx", 0) or 0)) < 1
        and abs(float(m.get("ry", 0) or 0)) < 1
        and abs(float(m.get("rz", 0) or 0)) < 1
    ]
    _fixture_pose_sessions[str(fid)] = {
        "observations": [],
        "createdAt": time.time(),
        "fixtureRotation": list(rotation),
    }
    return jsonify(ok=True,
                   currentPose={"x": pos[0], "y": pos[1], "z": pos[2],
                                 "rotation": list(rotation)},
                   floorMarkers=floor_markers)


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/aim")
def api_verify_pose_aim(fid):
    """Drive beam at a marker using the current layout pose's IK.
    Body: {markerId}. Computes (pan_norm, tilt_norm) from
    aim_to_pan_tilt(marker_xyz - fixture_xyz), writes DMX.
    Returns the (pan_norm, tilt_norm) actually written so the SPA can
    show them; operator nudges from there."""
    body = request.get_json(silent=True) or {}
    try:
        marker_id = int(body["markerId"])
    except (KeyError, ValueError):
        return jsonify(err="markerId required"), 400
    marker = next((m for m in _aruco_markers if int(m.get("id", -1)) == marker_id),
                   None)
    if marker is None:
        return jsonify(err=f"marker {marker_id} not in registry"), 404
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f:
        return jsonify(err="fixture not found"), 404
    pos = _fixture_position(fid)
    rotation = f.get("rotation") or [0.0, 0.0, 0.0]
    mx, my, mz = float(marker["x"]), float(marker["y"]), float(marker.get("z", 0.0))
    # Aim vector from fixture toward marker.
    dx, dy, dz = (mx - pos[0]), (my - pos[1]), (mz - pos[2])
    norm = (dx * dx + dy * dy + dz * dz) ** 0.5
    if norm < 1e-3:
        return jsonify(err="marker is at fixture position"), 400
    aim_unit = (dx / norm, dy / norm, dz / norm)
    pan_range = (f.get("panRange")
                 or (_profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("panRange")
                 or 540)
    tilt_range = (f.get("tiltRange")
                  or (_profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("tiltRange")
                  or 270)
    # #784 PR-7 — `_mcal.aim_to_pan_tilt` deleted. Inline the same math
    # the verify-pose feature has always used: stage→mount frame via
    # transposed rotation, then atan2 to (pan_deg, tilt_deg), then
    # normalised to [0, 1] using the profile's mechanical range.
    if (rotation[0] == 0 and rotation[1] == 0 and rotation[2] == 0):
        aim_mount = aim_unit
    else:
        from remote_math import (
            euler_xyz_deg_to_matrix, matrix_vec_mul, matrix_transpose,
        )
        R = euler_xyz_deg_to_matrix(rotation)
        aim_mount = matrix_vec_mul(matrix_transpose(R), aim_unit)
    aim_dx, aim_dy, aim_dz = aim_mount
    pan_deg = math.degrees(math.atan2(aim_dx, aim_dy))
    horiz = math.hypot(aim_dx, aim_dy)
    tilt_deg = math.degrees(math.atan2(-aim_dz, horiz))
    pan_n = max(0.0, min(1.0, 0.5 + pan_deg / pan_range))
    tilt_n = max(0.0, min(1.0, 0.5 + tilt_deg / tilt_range))
    # Drive the beam if the engine is running.
    if _artnet.running or _sacn.running:
        engine = _artnet if _artnet.running else _sacn
        prof_info = _profile_lib.channel_info(f.get("dmxProfileId", "")) or {}
        profile = {"channel_map": prof_info.get("channel_map", {}),
                   "channels": prof_info.get("channels", [])}
        try:
            uni = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            uni_buf = engine.get_universe(uni)
            uni_buf.set_fixture_pan_tilt(addr, pan_n, tilt_n, profile)
            uni_buf.set_fixture_dimmer(addr, 255, profile)
            uni_buf.set_fixture_rgb(addr, 0, 255, 0, profile)
        except Exception as e:
            log.warning("verify-pose aim DMX write failed: %s", e)
    return jsonify(ok=True,
                   panNorm=round(pan_n, 4), tiltNorm=round(tilt_n, 4),
                   markerId=marker_id, markerXYZ=[mx, my, mz])


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/observe")
def api_verify_pose_observe(fid):
    """Record one operator-confirmed observation: marker id + the final
    (pan_norm, tilt_norm) that landed beam on it. Body:
    ``{markerId, panNorm, tiltNorm}``. Returns the running observation
    count + per-marker history."""
    sess = _fixture_pose_sessions.get(str(fid))
    if not sess:
        return jsonify(err="no active verify-pose session — call /start"), 400
    body = request.get_json(silent=True) or {}
    try:
        marker_id = int(body["markerId"])
        pan_n = float(body["panNorm"])
        tilt_n = float(body["tiltNorm"])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(err=f"required fields: markerId, panNorm, tiltNorm ({e})"), 400
    marker = next((m for m in _aruco_markers if int(m.get("id", -1)) == marker_id),
                   None)
    if marker is None:
        return jsonify(err=f"marker {marker_id} not in registry"), 404
    obs = {
        "markerId": marker_id,
        "panNorm": pan_n,
        "tiltNorm": tilt_n,
        "markerXYZ": [float(marker["x"]), float(marker["y"]),
                       float(marker.get("z", 0.0))],
        "ts": time.time(),
    }
    # Replace any prior observation for the same marker (operator
    # iterating); never accumulate duplicates.
    sess["observations"] = [
        o for o in sess["observations"] if o["markerId"] != marker_id
    ]
    sess["observations"].append(obs)
    return jsonify(ok=True,
                   observationCount=len(sess["observations"]),
                   observations=sess["observations"])


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/solve")
def api_verify_pose_solve(fid):
    """Run the least-squares solver against the current observation
    set. Returns the suggested (X, Y, Z) + per-marker residual + RMS.
    Operator reviews then POSTs /apply to commit."""
    sess = _fixture_pose_sessions.get(str(fid))
    if not sess:
        return jsonify(err="no active session"), 400
    f = next((x for x in _fixtures if x.get("id") == fid), None)
    if not f:
        return jsonify(err="fixture not found"), 404
    pan_range = (f.get("panRange")
                 or (_profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("panRange")
                 or 540)
    tilt_range = (f.get("tiltRange")
                  or (_profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("tiltRange")
                  or 270)
    from fixture_pose_solver import solve_fixture_pose
    result = solve_fixture_pose(
        sess["observations"],
        fixture_rotation_deg=sess.get("fixtureRotation", [0, 0, 0]),
        pan_range_deg=pan_range, tilt_range_deg=tilt_range,
    )
    if "error" in result:
        return jsonify(ok=False, **result), 400
    return jsonify(ok=True, **result)


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/apply")
def api_verify_pose_apply(fid):
    """Save the solver's suggested pose to the layout. Body:
    ``{x, y, z}``. Updates ``layout.children[fid]`` in place + persists.
    The wizard session is closed."""
    sess = _fixture_pose_sessions.pop(str(fid), None)
    body = request.get_json(silent=True) or {}
    try:
        x = float(body["x"]); y = float(body["y"]); z = float(body["z"])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(err=f"required: x, y, z ({e})"), 400
    f = next((x_ for x_ in _fixtures if x_.get("id") == fid), None)
    if not f:
        return jsonify(err="fixture not found"), 404
    with _lock:
        children = _layout.setdefault("children", [])
        entry = next((c for c in children if c.get("id") == fid), None)
        if entry is None:
            entry = {"id": fid}
            children.append(entry)
        entry["x"] = x
        entry["y"] = y
        entry["z"] = z
        _save("layout", _layout)
    log.info("verify-pose: fid=%d new pose (%.1f, %.1f, %.1f) "
             "from %d observations", fid, x, y, z,
             len(sess["observations"]) if sess else 0)
    return jsonify(ok=True, pose={"x": x, "y": y, "z": z})


@app.post("/api/calibration/fixture/<int:fid>/verify-pose/cancel")
def api_verify_pose_cancel(fid):
    """Discard observations + close the session. Layout pose untouched."""
    _fixture_pose_sessions.pop(str(fid), None)
    return jsonify(ok=True)


# #784 PR-7 — api_mover_cal_manual / manual-cal grid deleted (legacy IK pipeline removed).

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


def _surface_model_for_cal():
    """#684 — return ``(surfaces, age_minutes_or_None, warning_or_None)``
    for the mover-cal threads.

    Cal pipelines use this to decide whether to consume the surface
    model (sample annotation + grid-filter + DEPTH_DISCONTINUITY gate)
    or fall back to legacy floor-plane behaviour. Reasoning, in priority:

      1. No analysed surfaces at all → return ``(None, None, "missing")``.
         Caller logs a clear warning and uses the legacy code path so
         existing rigs don't regress.
      2. Surfaces from ``layout-box`` fallback (no point cloud) → return
         the synthetic surfaces with ``age=None`` and warning="synthetic".
         Better than floor-only, but the operator should run a real scan.
      3. Surfaces from ``pointcloud`` source — compute scan age in
         minutes. If it exceeds ``calibrationTuning.maxScanAgeMinutes``
         (default 10), surface a "stale" warning so the cal status pill
         can flag the issue without aborting.

    The warning value is one of: ``None`` / ``"synthetic"`` / ``"stale"`` /
    ``"missing"``.
    """
    try:
        surfaces = _get_stage_geometry()
    except Exception as e:
        log.warning("Cal surface lookup failed: %s", e)
        return (None, None, "missing")
    if not surfaces:
        return (None, None, "missing")
    if surfaces.get("source") != "pointcloud":
        return (surfaces, None, "synthetic")
    captured = (_point_cloud or {}).get("capturedAt")
    if not captured:
        return (surfaces, None, None)
    age_min = max(0.0, (time.time() - float(captured)) / 60.0)
    max_age = float(_cal_tuning("maxScanAgeMinutes", 10.0))
    warn = "stale" if age_min > max_age else None
    return (surfaces, age_min, warn)


# ── Cal-trace recorder (#686) ─────────────────────────────────────────

CAL_TRACES_DIR = DATA / "cal_traces"
CAL_TRACE_RETENTION_PER_FIXTURE = 20
CAL_TRACE_SCHEMA_VERSION = 1


class CalTraceRecorder:
    """Per-cal-run NDJSON probe-level trace writer (#686).

    One record per probe across the markers / v2 / legacy paths; consumed
    by ``tools/cal_trace_replay.py`` to render top-down debug PNGs that
    name a cal failure mode at a glance. The recorder owns its own file
    handle for the duration of a cal run — caller invokes ``close()``
    once via try/finally so the trace lands on disk even on the error
    path.

    Records share a small set of fields (ts, phase, decision, ...) that
    the replay tool understands; phase- or decision-specific extras live
    under their own keys (predictedFloorPoint, detectedPixel, ...). The
    schema is documented in the issue (#686).

    Skip-by-filter records are intentionally written too — the operator
    needs to see WHY a cell was deferred, not just which cells were
    visited.
    """

    def __init__(self, fid, mode, fixture_pos, mover_rotation,
                  pan_range_deg, tilt_range_deg, mounted_inverted,
                  cameras, surfaces, scene_meta=None):
        from datetime import datetime, timezone
        self._fid = int(fid)
        self._mode = str(mode)
        self._fx_pos = (float(fixture_pos[0]), float(fixture_pos[1]),
                         float(fixture_pos[2]))
        self._fx_rot = list(mover_rotation or [0, 0, 0])
        self._pan_range = float(pan_range_deg or 540.0)
        self._tilt_range = float(tilt_range_deg or 270.0)
        self._inverted = bool(mounted_inverted)
        # cameras: list of {id, polygon: [(x,y),...]} for the predicted-
        # in-fov-of computation. Keep just the metadata the replay tool
        # needs; full fixture records are heavy.
        self._cameras = [
            {"id": int(c.get("id")), "polygon": [
                [float(p[0]), float(p[1])] for p in (c.get("polygon") or [])]}
            for c in (cameras or [])
        ]
        self._surfaces = surfaces  # may be None
        self._scene_meta = dict(scene_meta or {})

        CAL_TRACES_DIR.mkdir(parents=True, exist_ok=True)
        # Millisecond-precision timestamp so back-to-back cal runs don't
        # collide on the same filename — operators retrying within a
        # second would otherwise overwrite the previous trace.
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond // 1000:03d}Z"
        self._path = CAL_TRACES_DIR / f"fid{self._fid}-{ts}-{mode}.ndjson"
        self._fh = self._path.open("w", encoding="utf-8")
        self._closed = False
        self._counts = {"probed": 0, "skipped": 0, "confirmed": 0,
                         "rejected": 0, "refined": 0}
        # Header record names the schema + the static cal-context.
        self._write({
            "kind": "header",
            "schema": CAL_TRACE_SCHEMA_VERSION,
            "fid": self._fid,
            "mode": self._mode,
            "fxPos": list(self._fx_pos),
            "fxRotation": list(self._fx_rot),
            "panRangeDeg": self._pan_range,
            "tiltRangeDeg": self._tilt_range,
            "mountedInverted": self._inverted,
            "cameras": self._cameras,
            "surfacesSource": (surfaces or {}).get("source") if surfaces else None,
            "scene": self._scene_meta,
        })

    # ── Internal write helpers ────────────────────────────────────────
    def _write(self, rec):
        if self._closed or self._fh is None:
            return
        from datetime import datetime, timezone
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
        try:
            self._fh.write(json.dumps(rec, default=str) + "\n")
            self._fh.flush()
        except Exception as e:
            log.debug("cal-trace write failed: %s", e)

    def _predict_floor_point(self, pan_norm, tilt_norm):
        """Stage-mm point where the beam intersects the surface model
        (or the floor plane when no surfaces) for a normalised pan/tilt
        cell. Used for both predictedFloorPoint and predictedInFovOf."""
        try:
            from camera_math import pan_tilt_to_ray, point_in_polygon
        except Exception:
            return None, None, None
        pan_deg = (pan_norm - 0.5) * self._pan_range
        tilt_deg = (tilt_norm - 0.5) * self._tilt_range
        if self._inverted:
            tilt_deg = -tilt_deg
        try:
            o, d = pan_tilt_to_ray(self._fx_pos, self._fx_rot,
                                     pan_deg, tilt_deg)
        except Exception:
            return None, None, None
        # Try surface intersect first; fall back to z=0 floor plane.
        if self._surfaces:
            try:
                from surface_analyzer import beam_surface_check
                hit = beam_surface_check(self._surfaces, o, d)
                if hit is not None:
                    pt = hit.get("point") or (None, None, None)
                    surface = hit.get("surface")
                    in_fov = self._cameras_seeing_floor_point(
                        float(pt[0]), float(pt[1]))
                    return ([float(pt[0]), float(pt[1]), float(pt[2])],
                            surface, in_fov)
            except Exception:
                pass
        # Floor-plane fallback.
        if abs(d[2]) < 1e-6 or d[2] > 0:
            return (None, "ray-escapes", [])
        t = (0.0 - o[2]) / d[2]
        if t <= 0:
            return (None, "ray-behind", [])
        hx = o[0] + t * d[0]
        hy = o[1] + t * d[1]
        return ([float(hx), float(hy), 0.0], "floor",
                self._cameras_seeing_floor_point(hx, hy))

    def _cameras_seeing_floor_point(self, x, y):
        try:
            from camera_math import point_in_polygon
        except Exception:
            return []
        hits = []
        for cam in self._cameras:
            poly = [(float(p[0]), float(p[1])) for p in cam["polygon"]]
            if poly and point_in_polygon((x, y), poly):
                hits.append(cam["id"])
        return hits

    # ── Public recording API ──────────────────────────────────────────
    def record_seed(self, pan_norm, tilt_norm, target_xy, source=""):
        floor_point, surface, in_fov = self._predict_floor_point(
            pan_norm, tilt_norm)
        self._write({
            "kind": "seed",
            "panNorm": float(pan_norm),
            "tiltNorm": float(tilt_norm),
            "targetXY": (list(target_xy[:2]) if target_xy is not None else None),
            "source": str(source),
            "predictedFloorPoint": floor_point,
            "predictedSurface": surface,
            "predictedInFovOf": in_fov,
        })

    def record_skip(self, pan_norm, tilt_norm, reason="grid-filter"):
        self._counts["skipped"] += 1
        floor_point, surface, in_fov = self._predict_floor_point(
            pan_norm, tilt_norm)
        self._write({
            "kind": "probe",
            "phase": "filter",
            "panNorm": float(pan_norm),
            "tiltNorm": float(tilt_norm),
            "decision": "skip-by-filter",
            "decisionReason": reason,
            "predictedFloorPoint": floor_point,
            "predictedSurface": surface,
            "predictedInFovOf": in_fov,
        })

    def record_event(self, info):
        """Map a battleship_discover progress_cb event into a probe
        record. The events come in the existing vocabulary: grid-probe,
        beam-found, confirm-rejected, confirmed."""
        try:
            stage = info.get("stage") if isinstance(info, dict) else None
        except Exception:
            return
        if not stage:
            return
        rec = {"kind": "probe", "phase": stage}
        # Common fields.
        for src, dst in [("probe", "probeIdx"), ("total", "probeTotal"),
                          ("pan", "panNorm"), ("tilt", "tiltNorm"),
                          ("pixelX", "detectedPixelX"),
                          ("pixelY", "detectedPixelY")]:
            v = info.get(src)
            if v is not None:
                rec[dst] = v
        if rec.get("panNorm") is not None and rec.get("tiltNorm") is not None:
            fp, surface, in_fov = self._predict_floor_point(
                rec["panNorm"], rec["tiltNorm"])
            rec["predictedFloorPoint"] = fp
            rec["predictedSurface"] = surface
            rec["predictedInFovOf"] = in_fov
        if stage == "beam-found":
            rec["decision"] = "detected"
            self._counts["probed"] += 1
        elif stage == "confirmed":
            rec["decision"] = "confirmed"
            rec["refined"] = bool(info.get("refined"))
            for src, dst in [("panShiftPx", "panShiftPx"),
                              ("tiltShiftPx", "tiltShiftPx")]:
                v = info.get(src)
                if v is not None:
                    rec[dst] = v
            self._counts["confirmed"] += 1
            if rec["refined"]:
                self._counts["refined"] += 1
        elif stage == "confirm-rejected":
            rec["decision"] = "nudge-rejected"
            rec["decisionReason"] = (info.get("reason") or
                                       info.get("verdict") or "")
            for src, dst in [("verdict", "verdict"),
                              ("panShiftPx", "panShiftPx"),
                              ("tiltShiftPx", "tiltShiftPx"),
                              ("info", "confirmInfo")]:
                v = info.get(src)
                if v is not None:
                    rec[dst] = v
            self._counts["rejected"] += 1
        elif stage == "grid-probe":
            rec["decision"] = "probed"
            self._counts["probed"] += 1
        else:
            rec["decision"] = stage
        self._write(rec)

    def record_decision(self, pan_norm, tilt_norm, decision,
                          reason="", **extras):
        """Free-form recorder for cal-thread-side decisions that don't
        flow through battleship_discover (e.g. markers-mode marker
        convergence outcomes)."""
        rec = {
            "kind": "probe",
            "phase": "thread",
            "panNorm": float(pan_norm) if pan_norm is not None else None,
            "tiltNorm": float(tilt_norm) if tilt_norm is not None else None,
            "decision": str(decision),
            "decisionReason": str(reason),
        }
        rec.update(extras)
        if pan_norm is not None and tilt_norm is not None:
            fp, surface, in_fov = self._predict_floor_point(pan_norm, tilt_norm)
            rec["predictedFloorPoint"] = fp
            rec["predictedSurface"] = surface
            rec["predictedInFovOf"] = in_fov
        self._write(rec)

    def close(self, status="completed", error=None, extra=None):
        if self._closed:
            return
        try:
            self._write({
                "kind": "footer",
                "status": str(status),
                "error": (str(error) if error else None),
                "counts": dict(self._counts),
                "extra": dict(extra or {}),
            })
            try:
                self._fh.close()
            except Exception:
                pass
        finally:
            self._closed = True
            self._fh = None
        self._prune_old_traces()

    def _prune_old_traces(self):
        """Keep only the latest CAL_TRACE_RETENTION_PER_FIXTURE traces
        per fixture so the data dir doesn't grow without bound."""
        try:
            prefix = f"fid{self._fid}-"
            existing = sorted(
                CAL_TRACES_DIR.glob(f"{prefix}*.ndjson"),
                key=lambda p: p.stat().st_mtime,
                reverse=True)
            for stale in existing[CAL_TRACE_RETENTION_PER_FIXTURE:]:
                try:
                    stale.unlink()
                except OSError:
                    pass
        except Exception as e:
            log.debug("cal-trace prune failed: %s", e)

    @property
    def path(self):
        return str(self._path)

    @property
    def counts(self):
        return dict(self._counts)


def _wrap_grid_filter_for_trace(grid_filter, recorder):
    """Wrap a grid-filter predicate so every False result emits a
    record_skip. Returns the original callable when no recorder is
    attached so the wrapping is zero-cost in production."""
    if recorder is None:
        return grid_filter

    def _wrapped(pan_n, tilt_n):
        try:
            keep = bool(grid_filter(pan_n, tilt_n)) if grid_filter else True
        except Exception:
            keep = True
        if not keep:
            try:
                recorder.record_skip(pan_n, tilt_n)
            except Exception:
                pass
        return keep

    return _wrapped


def _wrap_progress_for_trace(progress_cb, recorder):
    """Wrap a progress callback so every event also lands in the trace."""
    if recorder is None:
        return progress_cb

    def _wrapped(info):
        try:
            recorder.record_event(info)
        except Exception:
            pass
        if progress_cb:
            try:
                progress_cb(info)
            except Exception:
                pass

    return _wrapped


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


def _aruco_multi_snapshot_detect(f, max_snapshots=3, blackout_bridge_ip=None,
                                   calibrating_fixture=None):
    """#626 — multi-snapshot ArUco aggregation. Takes up to N snapshots and
    keeps the best per-id by corner perimeter (largest = closest to camera =
    best sub-pixel corners). Matches the same aggregation pattern that
    stage-map has used since #stage-map-flaky.

    If `blackout_bridge_ip` is provided AND `calibrating_fixture` is the
    dict of the mover currently under calibration, its channel window is
    zeroed between snapshots so its beam can't wash out the markers. The
    engine's regular 40 Hz tick keeps the beam off until the thread
    explicitly writes a new DMX frame.

    #681-A — previously (and the intermediate #679 fix) the blackout
    targeted the whole universe or every other mover on the universe;
    bystander fixtures stayed dark for the entire run because nothing
    ever restored their state. Zero only what we need to zero — the
    calibrating mover itself.
    """
    best_per_id = {}
    frame_size = None
    last_err = None
    detected_total = 0
    blackout_target = None
    if blackout_bridge_ip and calibrating_fixture:
        cf = calibrating_fixture
        pid = cf.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        ch_count = int((info or {}).get("channelCount") or
                       cf.get("dmxChannelCount") or 13)
        blackout_target = (cf.get("dmxUniverse", 1),
                           cf.get("dmxStartAddr", 1),
                           ch_count)
    for attempt in range(max(1, int(max_snapshots))):
        if blackout_target:
            try:
                engine = _artnet if _artnet.running else (_sacn if _sacn.running else None)
                if engine:
                    uni, addr, chc = blackout_target
                    engine.get_universe(uni).set_channels(addr, [0] * chc)
                # Brief settle so the fixture has actually darkened before the snapshot.
                time.sleep(0.15)
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


# #692 — when per-marker spot-checks split into opposite-sign clusters
# (a tilted depth cloud will do this), the median of the offsets ≈ 0
# even though the cloud is uniformly hundreds of mm off. If the marker
# offsets disagree by more than this many millimetres, fall back to a
# RANSAC floor-plane solve via surface_analyzer.
_MARKER_DISAGREEMENT_MM = 200.0
# Plane-aware neighbour band — restrict per-marker sampling to points
# whose Z is within ±this many mm of the local floor estimate. Stops
# obstacles, walls, and ceiling reflections from polluting the median.
_MARKER_PLANE_BAND_MM = 100.0


def _apply_marker_z_alignment(cloud, radius_mm=400, min_pts=3, force=False):
    """#599 + #692 — shift a point cloud's Z so surveyed floor markers
    sit at z=0.

    Monocular depth models (ZoeDepth, MiDaS, mono-fallback) place the
    floor wherever their training set's prior puts it — on the sample
    rig that's a consistent ~250 mm above reality. The surveyed ArUco
    registry gives us the ground truth: every floor-level marker is by
    construction at z=0. For each such marker, gather the cloud points
    within `radius_mm` of its XY position AND within ±100 mm of the
    local floor band (#692, plane-aware), take their median Z, then
    take the median across markers and subtract from every point's Z.

    Robustness (#599):
    - Only marker records where `|z| < 50mm` AND `rx == ry == rz == 0`
      count as "floor" (wall-mounted markers skipped).
    - Median (not mean) per marker and across markers — one noisy
      marker can't drag the whole correction.
    - If no marker has ≥ `min_pts` nearby cloud points, returns without
      modifying the cloud and flags `used=False`.
    - Diagnostic payload returned so the SPA / tests can show which
      markers contributed.

    Robustness (#692):
    - Plane-aware neighbour filter: cull points outside ±100 mm of a
      local floor estimate before taking the median. Smaller `radiusMm`
      no longer flips the sign by grabbing obstacle points.
    - Marker-disagreement gate: when `max(offsets) − min(offsets) >
      200 mm`, the per-marker spot-checks are unreliable (cloud has
      per-camera tilt). Fall back to a RANSAC floor-plane solve via
      :func:`surface_analyzer.analyze_surfaces` and use its `floor.z`
      as the offset. Also surfaces a `warnings` list so the operator
      sees the upstream tilt diagnosis instead of silent zOffsetMm=0.

    #599 double-use guard: auto-apply sites (the scan endpoints) call
    this once per scan. If the same cloud dict has already been aligned
    in this process (markerAlignment.applied = True), a second auto call
    would re-measure ~0 residual and record `zOffsetMm=0.x` alongside
    the real offset — clutter, not corruption. Auto callers leave
    `force=False` to skip the redundant work; the operator-triggered
    endpoint passes `force=True` to explicitly re-align against a
    possibly-updated marker registry.

    Returns a diagnostics dict; mutates `cloud["points"]` in place.
    """
    if not cloud or not cloud.get("points"):
        return {"applied": False, "reason": "no points"}
    prior = cloud.get("markerAlignment")
    if not force and isinstance(prior, dict) and prior.get("applied"):
        return {"applied": False, "reason": "already aligned in this session",
                "priorZOffsetMm": prior.get("zOffsetMm")}
    floor = [m for m in _aruco_markers
             if abs(float(m.get("z", 0) or 0)) < 50
             and abs(float(m.get("rx", 0) or 0)) < 1
             and abs(float(m.get("ry", 0) or 0)) < 1
             and abs(float(m.get("rz", 0) or 0)) < 1]
    if not floor:
        return {"applied": False, "reason": "no floor-level markers in registry"}
    import statistics
    pts = cloud["points"]
    warnings = []
    per_marker = []
    offsets = []
    for m in floor:
        mx, my = float(m["x"]), float(m["y"])
        # First pass: any cloud point in the XY radius — used to compute
        # a robust local floor estimate before we filter to the plane band.
        zs_xy = [p[2] for p in pts
                  if abs(p[0] - mx) < radius_mm and abs(p[1] - my) < radius_mm]
        entry = {"id": int(m["id"]), "xy": [mx, my], "nearbyPoints": len(zs_xy)}
        if len(zs_xy) < min_pts:
            entry["used"] = False
            per_marker.append(entry)
            continue
        # Plane-aware second pass: restrict to a ±band around the local
        # FLOOR estimate. Median of all XY-radius points fails when an
        # obstacle (chair, person, lighting truss) sits over the marker
        # and contributes more points than the floor — the median picks
        # the obstacle. We anchor on the bottom-decile of z values (the
        # lowest 10 % of points in the XY radius) and median that. By
        # construction the floor is the lowest stratum, so even when
        # obstacle points outnumber floor points 10-to-1 the bottom
        # decile is still all-floor and gives a clean reference.
        zs_sorted = sorted(zs_xy)
        bottom_n = max(1, len(zs_sorted) // 10)
        bottom = zs_sorted[:bottom_n]
        local_floor = statistics.median(bottom)
        zs_planar = [z for z in zs_xy
                     if abs(z - local_floor) <= _MARKER_PLANE_BAND_MM]
        entry["planarPoints"] = len(zs_planar)
        if len(zs_planar) < min_pts:
            # Plane filter starved this marker — fall back to the looser
            # XY-only median rather than skip entirely.
            entry["planarFallback"] = "xy-only"
            entry["medianZ"] = round(local_floor, 1)
            entry["used"] = True
            offsets.append(local_floor)
        else:
            mz = statistics.median(zs_planar)
            entry["medianZ"] = round(mz, 1)
            entry["used"] = True
            offsets.append(mz)
        per_marker.append(entry)
    if not offsets:
        return {"applied": False, "reason": f"no marker had ≥{min_pts} nearby points",
                "markers": per_marker}
    spread = max(offsets) - min(offsets)
    # #692 — disagreement gate. If markers disagree wildly the cloud has
    # per-camera tilt and the median of opposite-sign clusters ≈ 0.
    # Don't apply the cancelling offset; consult the RANSAC floor plane.
    if spread > _MARKER_DISAGREEMENT_MM and len(offsets) >= 2:
        try:
            from surface_analyzer import analyze_surfaces
            surf = analyze_surfaces(pts) or {}
            floor_plane = surf.get("floor") or {}
            ransac_z = floor_plane.get("z")
        except Exception as e:
            log.warning("marker-Z fallback: analyze_surfaces failed (%s)", e)
            ransac_z = None
        warnings.append(
            f"floor markers span {spread:.0f} mm in z (>{_MARKER_DISAGREEMENT_MM:.0f} mm) — "
            "cloud likely has per-camera tilt. Consider re-scanning with "
            "improved camera-pose calibration; a z-shift alone won't "
            "level the floor."
        )
        if ransac_z is not None and isinstance(ransac_z, (int, float)):
            offset_z = float(ransac_z)
            for p in pts:
                p[2] -= offset_z
            cloud["zOffsetAppliedMm"] = round(
                (cloud.get("zOffsetAppliedMm") or 0.0) + offset_z, 2)
            log.info("marker-Z alignment: marker disagreement %.1f mm > "
                     "%.0f mm gate; applied RANSAC floor.z=%.1f mm instead "
                     "(per-marker offsets=%s)",
                     spread, _MARKER_DISAGREEMENT_MM, offset_z,
                     [round(o, 1) for o in offsets])
            return {"applied": True, "zOffsetMm": round(offset_z, 1),
                    "method": "ransac-floor-fallback",
                    "markerSpreadMm": round(spread, 1),
                    "markers": per_marker, "markersUsed": len(offsets),
                    "warnings": warnings}
        # No RANSAC plane available — refuse rather than apply a
        # cancelling-median zero. Operator can use POST /api/space/shift
        # to apply a manual offset.
        return {"applied": False,
                "reason": ("marker disagreement too large and RANSAC "
                            "floor unavailable — manual shift required"),
                "markerSpreadMm": round(spread, 1),
                "markers": per_marker, "markersUsed": len(offsets),
                "warnings": warnings}
    offset_z = statistics.median(offsets)
    for p in pts:
        p[2] -= offset_z
    cloud["zOffsetAppliedMm"] = round(
        (cloud.get("zOffsetAppliedMm") or 0.0) + offset_z, 2)
    log.info("marker-Z alignment: offset=%.1f mm across %d markers "
             "(offsets=%s, spread=%.1f)",
             offset_z, len(offsets), [round(o, 1) for o in offsets], spread)
    result = {"applied": True, "zOffsetMm": round(offset_z, 1),
              "method": "marker-median",
              "markerSpreadMm": round(spread, 1),
              "markers": per_marker, "markersUsed": len(offsets)}
    if warnings:
        result["warnings"] = warnings
    return result


@app.post("/api/space/shift")
def api_space_shift():
    """#692 — manual escape hatch when marker alignment refuses or
    gives the wrong answer. Apply a Z-offset of `dz` mm directly to
    every point in the current cloud. Positive `dz` raises the cloud;
    operator typically computes `dz = -floor.z` from
    /api/space/analyze when marker alignment would cancel itself out.

    Body: ``{"dz": <mm>}``. Returns the applied delta and the new
    cumulative offset.
    """
    global _point_cloud, _stage_surfaces_cache
    body = request.get_json(silent=True) or {}
    try:
        dz = float(body.get("dz"))
    except (TypeError, ValueError):
        return jsonify(ok=False, err="dz (mm) required, must be a number"), 400
    if not _point_cloud or not _point_cloud.get("points"):
        return jsonify(ok=False, err="no point cloud loaded"), 400
    if not (-10000 < dz < 10000):
        return jsonify(ok=False,
                        err="dz out of range — expected −10000 .. 10000 mm"), 400
    pts = _point_cloud["points"]
    for p in pts:
        p[2] += dz
    _point_cloud["zOffsetAppliedMm"] = round(
        (_point_cloud.get("zOffsetAppliedMm") or 0.0) + dz, 2)
    # Stamp the manual override so the SPA / scan auto-aligners can tell
    # this offset came from operator intent, not a marker solve.
    _point_cloud["markerAlignment"] = {
        "applied": True,
        "method": "manual-shift",
        "zOffsetMm": round(dz, 1),
    }
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("manual cloud shift: dz=%.1f mm applied (cumulative=%s)",
             dz, _point_cloud["zOffsetAppliedMm"])
    return jsonify(ok=True, dz=dz,
                   cumulativeOffsetMm=_point_cloud["zOffsetAppliedMm"],
                   totalPoints=len(pts))


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
    # Operator-triggered — force re-measure even if a prior auto-align
    # already ran in this session. Registry may have gained / lost a
    # marker, or the operator surveyed a new floor reference.
    result = _apply_marker_z_alignment(_point_cloud, radius_mm=radius,
                                         min_pts=min_pts, force=True)
    if result.get("applied"):
        _point_cloud["markerAlignment"] = result
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


def _check_ollama_install_marker():
    """#623 — if the Windows installer was run with the 'ai' component
    ticked, a marker file ``ollama.install-requested`` is dropped next to
    SlyLED.exe. Kick off the install in the background so the user sees
    progress through the Settings → AI Runtime UI instead of the
    installer console."""
    if _ollama_rt is None:
        return
    try:
        if getattr(sys, "frozen", False):
            install_dir = os.path.dirname(sys.executable)
        else:
            return  # dev mode
        _ollama_rt.check_install_marker(install_dir)
    except Exception as e:
        log.warning("ollama install-marker check failed: %s", e)


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


# #696 — ZoeDepth scan state + log. The synchronous endpoint blocked
# longer than the 30 s XHR timeout in app.js's `ra()` helper, so the
# SPA reported "Failed: unknown" while the orchestrator finished the
# scan and saved the cloud. Async + per-stage log fixes both the
# misleading error AND gives the operator visibility into progress.
_zoe_scan_state = {
    "running": False,
    "progress": 0,             # 0..100
    "message": "",             # current human-readable stage
    "log": [],                 # list of {ts, level, message} stage events
    "result": None,            # final scan summary (camerasMeta, totals)
    "error": None,
    "startedAt": 0.0,
}


def _zoe_log(level, message):
    """Append a stage event to the live log buffer + mirror to log.info."""
    import datetime as _dt
    _zoe_scan_state["log"].append({
        "ts": _dt.datetime.utcnow().isoformat() + "Z",
        "level": level,
        "message": message,
    })
    _zoe_scan_state["message"] = message
    if level == "error":
        log.warning("ZoeDepth: %s", message)
    else:
        log.info("ZoeDepth: %s", message)


def _zoe_scan_thread(positioned, pos_map, lighting_mode, max_pts):
    """Background worker for /api/space/scan/zoedepth. Drives the same
    pipeline the synchronous version did, but updates _zoe_scan_state
    so the SPA can poll for progress + render a stage log."""
    global _point_cloud, _stage_surfaces_cache
    import urllib.request
    import math as _math
    import io
    try:
        import numpy as _np
        from PIL import Image
    except Exception as e:
        _zoe_scan_state["error"] = f"numpy / Pillow missing: {e}"
        _zoe_scan_state["running"] = False
        return
    from camera_math import build_camera_to_stage
    from stereo_consistency import cross_camera_filter

    per_cam_clouds = []
    cam_info_list = []
    t_scan = time.time()
    n_cams = len(positioned)

    try:
        with _ScanLightingWindow(lighting_mode):
            _zoe_log("info", f"Lighting window opened (mode: {lighting_mode})")
            for idx, cam in enumerate(positioned):
                cam_label = cam.get("name") or cam.get("cameraIp")
                # Per-camera progress: 5..85 % across all cameras.
                base_pct = 5 + int(80 * idx / max(1, n_cams))
                _zoe_scan_state["progress"] = base_pct
                _zoe_log("info", f"[{idx + 1}/{n_cams}] {cam_label}: capturing snapshot")

                pos = pos_map[cam["id"]]
                cam_pos = (pos.get("x", 0), pos.get("y", 0), pos.get("z", 0))
                rot = cam.get("rotation", [0, 0, 0])
                fov = cam.get("fovDeg", 90)

                try:
                    url = f"http://{cam['cameraIp']}:5000/snapshot?cam={cam.get('cameraIdx', 0)}"
                    jpg_bytes = urllib.request.urlopen(url, timeout=15).read()
                except Exception as e:
                    _zoe_log("error",
                             f"[{idx + 1}/{n_cams}] {cam_label}: snapshot "
                             f"failed ({e}) — skipping")
                    continue

                img = Image.open(io.BytesIO(jpg_bytes)).convert("RGB")
                _zoe_scan_state["progress"] = base_pct + 3
                _zoe_log("info",
                         f"[{idx + 1}/{n_cams}] {cam_label}: snapshot "
                         f"{img.size[0]}×{img.size[1]} px — running "
                         f"ZoeDepth inference (CPU ~15 s)")

                t0 = time.time()
                try:
                    depth_mm, inf_ms = _depth_runtime.infer_jpeg(jpg_bytes)
                except Exception as e:
                    _zoe_log("error",
                             f"[{idx + 1}/{n_cams}] {cam_label}: ZoeDepth "
                             f"runtime error: {e}")
                    _zoe_scan_state["error"] = f"ZoeDepth runtime error: {e}"
                    _zoe_scan_state["running"] = False
                    return
                t1 = time.time()

                if depth_mm.shape[::-1] != img.size:
                    from PIL import Image as _I
                    depth_mm = _np.array(
                        _I.fromarray(depth_mm).resize(img.size, _I.BICUBIC),
                        dtype=_np.float32,
                    )
                _zoe_log("info",
                         f"[{idx + 1}/{n_cams}] {cam_label}: inference "
                         f"{t1 - t0:.1f} s, depth range "
                         f"{depth_mm.min():.0f}..{depth_mm.max():.0f} mm")

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
                    "anchorQuality": "ok",
                })
                cam_info_list.append({
                    "fixtureId": cam["id"],
                    "cameraIdx": cam.get("cameraIdx", 0),
                    "name": cam.get("name"),
                    "pointCount": len(stage_pts),
                    "inferenceS": round(t1 - t0, 2),
                    "anchorQuality": "ok",
                })
                _zoe_log("info",
                         f"[{idx + 1}/{n_cams}] {cam_label}: "
                         f"{len(stage_pts)} stage-frame points")

        if not per_cam_clouds:
            _zoe_scan_state["error"] = "No cameras returned usable frames"
            _zoe_log("error", _zoe_scan_state["error"])
            _zoe_scan_state["running"] = False
            return

        _zoe_scan_state["progress"] = 88
        _zoe_log("info", f"Merging {len(per_cam_clouds)} per-camera clouds "
                          f"with cross-camera filter")
        if len(per_cam_clouds) >= 2:
            merged, filter_stats = cross_camera_filter(per_cam_clouds)
        else:
            merged = per_cam_clouds[0]["points"]
            filter_stats = None

        total_t = time.time() - t_scan
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
        _zoe_scan_state["progress"] = 95
        _zoe_log("info",
                 f"Aligning cloud Z to surveyed ArUco floor markers")
        align = _apply_marker_z_alignment(_point_cloud)
        if align.get("applied"):
            _point_cloud["markerAlignment"] = align
            _zoe_log("info", f"Marker-Z alignment applied: "
                              f"{align.get('zOffsetMm')} mm "
                              f"(method {align.get('method', 'marker-median')})")
        elif align.get("warnings"):
            for w in align["warnings"]:
                _zoe_log("warn", f"Alignment warning: {w}")
        else:
            _zoe_log("info",
                     f"Marker-Z alignment skipped: {align.get('reason', '?')}")

        _save("pointcloud", _point_cloud)
        _stage_surfaces_cache = {"key": None, "value": None}

        _zoe_scan_state["progress"] = 100
        _zoe_scan_state["result"] = {
            "source": "zoedepth",
            "totalPoints": len(merged),
            "cameras": cam_info_list,
            "elapsedS": round(total_t, 2),
            "markerAlignment": align,
        }
        _zoe_log("info",
                 f"Scan complete: {len(merged)} points from "
                 f"{len(per_cam_clouds)} camera(s) in {total_t:.1f} s")
    except Exception as e:
        log.exception("ZoeDepth scan thread crashed")
        _zoe_scan_state["error"] = f"Scan thread crashed: {e}"
        _zoe_log("error", str(e))
    finally:
        _zoe_scan_state["running"] = False


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

    #696 — runs in a background thread and returns immediately. The SPA
    polls /api/space/scan/zoedepth/status to render a per-stage log
    and progress bar. Pre-#696 this was synchronous and any rig with
    more than two cameras tripped the SPA's 30 s XHR timeout, surfacing
    as the misleading "Failed: unknown" while the orchestrator silently
    completed the scan in the background.
    """
    if _zoe_scan_state["running"]:
        return jsonify(err="ZoeDepth scan already in progress",
                       progress=_zoe_scan_state["progress"]), 409

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

    # Reset state for a fresh run.
    _zoe_scan_state["running"] = True
    _zoe_scan_state["progress"] = 1
    _zoe_scan_state["message"] = "Starting ZoeDepth scan…"
    _zoe_scan_state["log"] = []
    _zoe_scan_state["result"] = None
    _zoe_scan_state["error"] = None
    _zoe_scan_state["startedAt"] = time.time()
    _zoe_log("info", f"ZoeDepth scan starting on {len(positioned)} camera(s)")

    threading.Thread(
        target=_zoe_scan_thread,
        args=(positioned, pos_map, lighting_mode, max_pts),
        daemon=True,
    ).start()
    return jsonify(ok=True, started=True, cameras=len(positioned))


@app.get("/api/space/scan/zoedepth/status")
def api_space_scan_zoedepth_status():
    """Poll the live ZoeDepth scan state. Returns running flag,
    progress 0..100, current message, full per-stage log buffer, and
    (when complete) the result summary or error string. #696."""
    return jsonify(
        running=_zoe_scan_state["running"],
        progress=_zoe_scan_state["progress"],
        message=_zoe_scan_state["message"],
        log=list(_zoe_scan_state["log"]),
        result=_zoe_scan_state["result"],
        error=_zoe_scan_state["error"],
        startedAt=_zoe_scan_state["startedAt"],
    )


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
    # routinely drift 5-10°), which on the sample rig is the
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
    # #599 — auto-align Z to surveyed floor markers. The ZoeDepth and
    # mono paths do this; stereo was the missing site. ORB feature
    # matching finds few points on textureless floors, so in practice
    # this often no-ops (fewer than min_pts nearby any floor marker)
    # and flags `applied: false` with a usable reason. When the floor
    # DOES have ArUco-bearing detail to match against, the correction
    # works the same as the mono path.
    _align = _apply_marker_z_alignment(_point_cloud)
    if _align.get("applied"):
        _point_cloud["markerAlignment"] = _align
    _save("pointcloud", _point_cloud)
    _stage_surfaces_cache = {"key": None, "value": None}
    log.info("Stereo scan: %d matches → %d triangulated points (delta=%.1fms, "
             "thr=%.0fmm, anchored=%s)%s",
             len(matches), len(points), data.get("captureDeltaMs", 0),
             thr_mm, anchored,
             f" (Z-aligned {_align['zOffsetMm']}mm)" if _align.get("applied") else "")
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
    # hotspots on walls (see Cam13 r=0.127 in the sample rig test).
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
        # #684 — stamp scan completion time so the cal-thread surface
        # availability check (`_surface_model_for_cal`) can warn / fall
        # back when the cloud is stale relative to calibrationTuning.
        _point_cloud["capturedAt"] = time.time()
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
    # #423 — per-class threshold override forwarded from the fixture
    # config (trackClassThresholds). Missing classes fall back to the
    # global trackThreshold on the camera node side.
    class_thresholds = body.get("classThresholds") or f.get("trackClassThresholds")
    try:
        import urllib.request as _ur
        payload = {
            # #830 — fall back to the fixture's `cameraIdx` so two camera
            # fixtures sharing one node (Stage Right cameraIdx=0, Stage
            # Left cameraIdx=1) actually reach the right /dev/video*. An
            # explicit `cam` in the request body still wins for manual
            # API use; UI-driven starts now route correctly.
            "cam": body.get("cam", f.get("cameraIdx", 0)),
            "orchestratorUrl": f"http://{local_ip}:{port}",
            "cameraId": fid,
            "fps": body.get("fps", f.get("trackFps", 2)),
            "threshold": body.get("threshold", f.get("trackThreshold", 0.4)),
            "ttl": body.get("ttl", f.get("trackTtl", 5)),
            "classes": classes,
            "reidMm": body.get("reidMm", f.get("trackReidMm", 500)),
            "inputSize": body.get("inputSize", f.get("trackInputSize", 320)),
        }
        if class_thresholds:
            payload["classThresholds"] = class_thresholds
        req_data = json.dumps(payload).encode()
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

# B1 phase 1 — camera network scan, SSH+SCP deploy, and camera SSH settings
# routes/helpers extracted to orch_camera_deploy.py (blueprint
# "camera_deploy"). State they touch (_ssh, _camera_ssh, _children,
# _fixtures, _layout, DATA, _FW_DIR, _FW_CACHE_DIR) stays defined in this
# module and is reached via the orch_state bridge.
import orch_camera_deploy
app.register_blueprint(orch_camera_deploy.bp)


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
    # #896 — prune fused-id forwardings whose survivor has itself
    # expired, or that are simply old (cameras rebind within a tick or
    # two; anything older is a leak).
    live_ids = {o.get("id") for o in _temporal_objects}
    for old_id in [k for k, ent in _fused_id_map.items()
                   if ent["to"] not in live_ids or now - ent["at"] > _FUSED_ID_TTL_S]:
        del _fused_id_map[old_id]


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
    """Camera-source weight for a temporal-object placement: tier × YOLO
    confidence × hull-falloff × freshness. Used as the mean-fusion weight
    and as the #630 confidence signal. Registered as the "camera" entry
    (and the legacy default) in the #900 per-source-type weight registry
    below — non-camera sources supply their own weight/covariance hook."""
    tier = _FUSION_TIER_WEIGHT.get(obj.get("_method"), 0.05)
    yolo_conf = obj.get("confidence")
    if yolo_conf is None:
        yolo_conf = obj.get("_yoloConfidence", 0.5)
    # Freshness: 1.0 at t=0s, linearly decays to 0 at MAX_AGE_S.
    freshness = max(0.0, 1.0 - (obj_age_s / _FUSION_MAX_AGE_S))
    return max(0.0, tier * float(yolo_conf) * freshness)


# #900 — source-agnostic fusion. A temporal object may carry provenance
# metadata under "source": {"type": "<source type>", ...} — e.g.
# {"type": "camera", "cameraId": 3}. The camera ingest path stamps it;
# objects created before #900 (or by external POSTs that omit it) default
# to "camera", so legacy objects fuse exactly as they always did.
# The fusion core consults this registry for the per-source weight (a
# covariance-derived confidence for filtered sources like radar), so a
# new sensor type participates in fusion without touching
# _fuse_temporal_objects itself.
#
# radar_fusion.py (#912) registers its weight hook here:
#   register_fusion_source_weight("radar", <fn(obj, obj_age_s) -> float>)
_FUSION_SOURCE_WEIGHTS = {}


def register_fusion_source_weight(source_type, weight_fn):
    """Register `weight_fn(obj, obj_age_s) -> float` as the fusion weight
    for temporal objects whose source["type"] == source_type (#900)."""
    _FUSION_SOURCE_WEIGHTS[str(source_type)] = weight_fn


def _fusion_source_type(obj):
    """Source type of a temporal object. Defaults to "camera" — every
    pre-#900 temporal object was camera-pushed, so the default preserves
    legacy behaviour for objects with no source stamp."""
    src = obj.get("source")
    if isinstance(src, dict):
        st = src.get("type")
        if isinstance(st, str) and st:
            return st
    return "camera"


def _fusion_weight_for(obj, obj_age_s):
    """Per-source fusion weight (#900): route through the registered
    source-type hook; unknown/unregistered types fall back to the camera
    weighting (matching pre-#900 behaviour for legacy objects)."""
    fn = _FUSION_SOURCE_WEIGHTS.get(_fusion_source_type(obj))
    if fn is None:
        fn = _fusion_weight
    try:
        return max(0.0, float(fn(obj, obj_age_s)))
    except Exception:
        log.debug("fusion weight hook failed for source %r",
                  _fusion_source_type(obj), exc_info=True)
        return 0.0


register_fusion_source_weight("camera", _fusion_weight)


# ── #912 — radar fusion → temporal person objects ────────────────────────────
# radar_fusion.py owns projection + per-radar tracking (gated NN
# association, per-track CV Kalman, M-of-N confirmation, coasting).
# Confirmed tracks flow through the two sinks below, which create/update
# temporal person objects through the SAME internal path the camera
# tracker's HTTP pushes take (same dict shape, same _lock discipline,
# same TTL/reap semantics, #896 fused-id forwarding honoured) — no HTTP
# loopback, per design doc §5.1. The #900 per-source weight registered
# here then fuses radar↔radar and radar↔camera clusters for free.
import radar_fusion


def _radar_person_create(pos_xy, source, ttl_s):
    """Sink: confirmed radar track → temporal person object. Mirrors
    api_objects_temporal_create's person shape (objectType "person",
    pink, TTL, mobility moving) with radar provenance stamped."""
    global _nxt_tmp
    cx, cy = float(pos_xy[0]), float(pos_xy[1])
    with _lock:
        obj = {
            "id": _nxt_tmp, "name": f"Person (radar) {_nxt_tmp}",
            "objectType": "person",
            "mobility": "moving",
            "_temporal": True,
            "ttl": float(ttl_s),
            "_expiresAt": time.time() + float(ttl_s),
            "color": radar_fusion.PERSON_COLOR,
            "opacity": 40,
            # pos is the object CENTER (renderer convention, #Q1): the
            # radar tracks the floor point, height is the default person
            # estimate — same as the camera path without a full-frame bbox.
            "transform": {
                "pos": [cx, cy, radar_fusion.PERSON_HEIGHT_MM / 2.0],
                "rot": [0, 0, 0],
                "scale": [500.0, radar_fusion.PERSON_HEIGHT_MM, 500.0],
            },
            "source": dict(source),   # {"type": "radar", "fixtureId", "node"}
        }
        _temporal_objects.append(obj)
        _nxt_tmp += 1
    return obj["id"]


def _radar_person_update(oid, pos_xy):
    """Sink: move a radar person object + refresh its TTL. Follows the
    #896 fused-id forwarding exactly as PUT /api/objects/<id>/pos does,
    returning the surviving id so the track rebinds after a cross-sensor
    merge; None once the object is gone (expired) so the tracker
    re-creates it."""
    with _lock:
        target_id = oid
        obj = next((o for o in _temporal_objects if o["id"] == oid), None)
        if obj is None:
            fwd = _fused_id_map.get(oid)
            if fwd:
                target_id = fwd["to"]
                obj = next((o for o in _temporal_objects
                            if o["id"] == target_id), None)
        if obj is None:
            return None
        tf = obj.setdefault("transform",
                            {"pos": [0, 0, 0], "rot": [0, 0, 0],
                             "scale": [500.0, radar_fusion.PERSON_HEIGHT_MM, 500.0]})
        tf["pos"] = [float(pos_xy[0]), float(pos_xy[1]),
                     radar_fusion.PERSON_HEIGHT_MM / 2.0]
        if obj.get("ttl"):
            obj["_expiresAt"] = time.time() + obj["ttl"]
        return target_id


# The module-level engine _handle_mmw_targets feeds (#910). Tuning knobs
# (gate/confirm/coast/TTL/noise) default from radar_fusion's constants —
# the #908 bench pass revises those, not this call site.
_radar_fusion = radar_fusion.RadarFusion(
    create_person=_radar_person_create,
    update_person=_radar_person_update,
)
register_fusion_source_weight("radar", radar_fusion.fusion_weight)


def _fuse_temporal_objects():
    """Cluster near-duplicate temporal objects across sources (cameras
    today; radar via the #900 per-source weight registry) and replace
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
            # #900 — weight comes from the per-source-type hook (camera
            # weighting for legacy/camera objects, unchanged).
            w = _fusion_weight_for(obj, age)
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
                # #900 — provenance for mixed-source clusters (additive;
                # cameraId stays for the SPA's per-camera breakdown).
                "sourceType": _fusion_source_type(obj),
                "method": src_tier,
                "weight": round(w, 3),
            })
        if total_w <= 0:
            # Review-fix: every cluster member rolled past _FUSION_MAX_AGE_S
            # so each contributed weight 0. Don't collapse to cluster[0]
            # only — that silently drops members [1..N]. Keep every member
            # as-is (they'll reap on the next _expiresAt tick).
            for _i, obj in cluster:
                fused.append(obj)
            continue
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
        # #896 — the non-surviving cluster members' ids vanish from
        # _temporal_objects here, but their cameras keep addressing
        # them via PUT /api/objects/<old id>/pos. Record a forwarding
        # so those updates land on the survivor instead of 404ing
        # until TTL. Collapse chains at insert so lookups are one hop.
        merged_id = merged.get("id")
        fused_away = {obj.get("id") for _i, obj in cluster
                      if obj.get("id") is not None and obj.get("id") != merged_id}
        for oid in fused_away:
            _fused_id_map[oid] = {"to": merged_id, "at": now}
        for ent in _fused_id_map.values():
            if ent["to"] in fused_away:
                ent["to"] = merged_id
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
    # #894 — the read-modify-write rebind + save must hold _lock or a
    # concurrent create/pos-update can be lost between the rebinds.
    with _lock:
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
    # Review-fix — pixel→stage conversion now goes through the same Q1
    # pipeline the temporal ingest uses (homography → FOV → raw fallback),
    # stamping the result with _method. The old inline proportional hack
    # here was the last place the broken Q1 math hid in the codebase.
    cam_id = body.get("cameraId")
    pixel_box = body.get("pixelBox")
    frame_size = body.get("frameSize")
    method_tier = None
    anchors = None
    if cam_id is not None and pixel_box and frame_size:
        cam_fixture = next((f for f in _fixtures
                            if f.get("id") == cam_id
                            and f.get("fixtureType") == "camera"), None)
        anchors = _pixel_box_to_stage_anchors(cam_fixture, pixel_box, frame_size)
        if anchors:
            method_tier = anchors["method"]
            # Treat body.pos as the anchor mode hint: z>100 → center,
            # z==0 → feet, else honour as-provided. Default to center
            # since that's the renderer convention (#Q1).
            hint = float(pos[2] or 0)
            if hint <= 1.0:
                pos = list(anchors["feet"])
            else:
                pos = list(anchors["center"])
    with _lock:
        target_id = oid
        obj = next((o for o in _objects if o["id"] == oid), None)
        if not obj:
            obj = next((o for o in _temporal_objects if o["id"] == oid), None)
        if not obj:
            # #896 — the id may have been fused away by
            # _fuse_temporal_objects. Forward the update to the
            # surviving object and report its id back so the camera
            # can rebind its track instead of 404-flogging until TTL.
            fwd = _fused_id_map.get(oid)
            if fwd:
                target_id = fwd["to"]
                obj = next((o for o in _temporal_objects if o["id"] == target_id), None)
        if not obj:
            return jsonify(err="not found"), 404
        obj.setdefault("transform", {"pos": [0,0,0], "rot": [0,0,0], "scale": [2000,1500,1]})["pos"] = [float(p) for p in pos]
        if method_tier:
            obj["_method"] = method_tier
        if anchors:
            obj["_anchors"] = {
                "feet": [float(v) for v in anchors["feet"]],
                "center": [float(v) for v in anchors["center"]],
                "head": [float(v) for v in anchors["head"]],
            }
        if obj.get("_temporal") and obj.get("ttl"):
            obj["_expiresAt"] = time.time() + obj["ttl"]
    # #896 — "objectId" is included on every success (not just fused
    # forwards) so callers can uniformly rebind to whatever id the
    # orchestrator now tracks this object under.
    return jsonify(ok=True, method=method_tier, objectId=target_id)

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
            # #900 — source provenance for the per-source fusion hooks.
            obj["source"] = {"type": "camera", "cameraId": cam_id}
        elif isinstance(body.get("source"), dict) \
                and isinstance(body["source"].get("type"), str) \
                and body["source"]["type"]:
            # #900 — non-camera ingest (e.g. radar_fusion.py #912, or a
            # test harness) declares its own source type. Absent both,
            # the object carries no source stamp and fuses with the
            # legacy camera weighting (see _fusion_source_type).
            obj["source"] = body["source"]
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


@app.get("/api/dmx-profiles/<profile_id>/issues")
def api_dmx_profile_issues(profile_id):
    """#887 — return soft-warning diagnostics for a profile's shape.

    Surfaces problems like duplicate offsets and the bits=16-without-
    matching-fine-channel pattern that caused the 350W BeamLight motor
    lag (orchestrator wrote pan/tilt LSBs into the tilt and
    pan-tilt-speed slots because the profile declared phantom
    pan-fine / tilt-fine entries at those offsets). Returns a list of
    operator-readable strings; empty list = profile shape is clean."""
    p = _profile_lib.get_profile(profile_id)
    if not p:
        return jsonify(err="Not found"), 404
    return jsonify(id=profile_id, issues=_profile_lib.find_profile_issues(p))

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
    """Set colour on a fixture — RGB or colour-wheel depending on profile.

    Thin compat shim over :meth:`DMXUniverse.set_fixture_rgb`, which now
    centrally handles the RGB / hybrid / wheel-only dispatch (#842).
    Two call shapes survive for legacy callers: ``(engine, uni, addr,
    r, g, b, prof_info)`` and ``(uni_buf, addr, None, r, g, b,
    prof_info)``.
    """
    profile = None
    if prof_info:
        profile = {"channel_map": prof_info.get("channel_map") or {},
                   "channels":    prof_info.get("channels") or []}
    if addr_or_none is not None:
        engine, uni, addr = engine_or_buf, uni_or_addr, addr_or_none
        engine.set_fixture_rgb(uni, addr, r, g, b, profile)
    else:
        uni_buf, addr = engine_or_buf, uni_or_addr
        uni_buf.set_fixture_rgb(addr, r, g, b, profile)


# ── #737 — Lamp / beam / blackout helpers ──────────────────────────────
#
# Per #737, every "turn this fixture on" / "turn this fixture off" code
# path goes through these helpers — NOT direct uni_buf.set_fixture_*
# writes — so the per-profile quirks (RGB-only with no master dimmer,
# colour-wheel-only with closed-shutter default, hybrid RGB+wheel
# fixtures whose wheel filters out the RGB mix) live in one place.
# The helpers branch on `channel_map` and the profile's `channels`
# list to figure out exactly which channels need touching.

def _set_fixture_lamp(engine, uni, addr, on, prof_info):
    """Turn a fixture's lamp on or off across every profile variant.

    On:
      - Dimmer to 255 (if profile has one).
      - Shutter / strobe to its "open" value (honours ShutterStrobe
        capabilities when present, else channel default, else 255).
      - RGB to white (255, 255, 255) when the profile has RGB.
      - Colour-wheel to slot 0 (open / white) when wheel-only.
      - Channel defaults applied for non-touched channels with default>0
        (matches the dmx-test endpoint's existing behaviour).

    Off:
      - Dimmer to 0 (if profile has one).
      - Shutter / strobe to "closed" (range with shutterEffect=Closed)
        when the profile distinguishes; else 0.
      - RGB to (0, 0, 0) so RGB-only fixtures (no master dimmer) go
        dark too.

    Idempotent — calling repeatedly with the same `on` value writes the
    same channels.
    """
    if not prof_info or not engine:
        return
    cm = prof_info.get("channel_map", {}) or {}
    channels = prof_info.get("channels", []) or []
    uni_buf = engine.get_universe(uni)
    # Operator clarification 2026-05-03: write the MASTER dimmer only,
    # not every INTENSITY_TYPES channel. Profiles like slymovehead
    # carry a second channel mistyped as `dimmer` (ch10) that is
    # actually the LASER (default=0); iterating wrote 255 to the laser
    # on every lamp-on. `channel_map["dimmer"]` already disambiguates
    # by picking the highest-default channel as the master (ch4 on
    # slymovehead).
    from dmx_profiles import strobe_open_value
    if on:
        master_off = cm.get("dimmer")
        if master_off is not None:
            uni_buf.set_channel(addr + master_off, 255)
        # Shutter open: honour the ShutterStrobe Open capability when
        # the profile spells it out, else default, else 255.
        if "strobe" in cm:
            try:
                uni_buf.set_channel(addr + cm["strobe"],
                                     strobe_open_value(prof_info))
            except Exception:
                # Fallback: channel default if >0 else 255.
                strobe_ch = next((c for c in channels
                                   if c.get("type") == "strobe"), None)
                default = (strobe_ch or {}).get("default")
                val = int(default) if isinstance(default, (int, float)) and default > 0 else 255
                uni_buf.set_channel(addr + cm["strobe"], val)
        # RGB to white if present, else colour-wheel to open slot.
        if "red" in cm:
            _set_fixture_color(engine, uni, addr, 255, 255, 255, prof_info)
        elif "color-wheel" in cm:
            uni_buf.set_channel(addr + cm["color-wheel"], 0)
        # Apply channel defaults > 0 for any other channels we haven't
        # explicitly written. Skip channel types we own here.
        owned = {"dimmer", "intensity", "strobe", "red", "green", "blue",
                 "white", "color-wheel", "pan", "tilt", "pan-fine", "tilt-fine"}
        for ch in channels:
            ch_type = ch.get("type", "")
            if ch_type in owned:
                continue
            default = ch.get("default")
            if isinstance(default, (int, float)) and default > 0:
                uni_buf.set_channel(addr + ch.get("offset", 0), int(default))
    else:
        # Lamp off — master dimmer to 0, RGB to 0, shutter closed if
        # profile knows. Aux/effect channels mistyped as `dimmer` are
        # left untouched (matches the lamp-on path).
        master_off = cm.get("dimmer")
        if master_off is not None:
            uni_buf.set_channel(addr + master_off, 0)
        if "red" in cm:
            for ch_name in ("red", "green", "blue", "white"):
                if ch_name in cm:
                    uni_buf.set_channel(addr + cm[ch_name], 0)
        if "strobe" in cm:
            # Try to find a "Closed" range; fall back to 0.
            try:
                strobe_ch = next((c for c in channels
                                   if c.get("type") == "strobe"), {})
                closed_val = 0
                for cap in (strobe_ch.get("capabilities") or []):
                    if cap.get("shutterEffect") == "Closed":
                        rng = cap.get("range", [0, 0])
                        closed_val = (rng[0] + rng[1]) // 2
                        break
                uni_buf.set_channel(addr + cm["strobe"], int(closed_val))
            except Exception:
                uni_buf.set_channel(addr + cm["strobe"], 0)


def _set_fixture_beam(engine, uni, addr, dim_norm, prof_info):
    """Set beam intensity 0..1 across every profile variant.

    Profile has a dedicated dimmer channel → write that.
    No dimmer channel (RGB-only) → scale RGB by dim_norm; current
    colour preserved if the engine already has it, else default to
    white.
    Wheel-only with no dimmer → can't dim continuously; treat
    ``dim_norm < 0.05`` as off (lamp-off path) and anything else as on.
    """
    if not prof_info or not engine:
        return
    cm = prof_info.get("channel_map", {}) or {}
    uni_buf = engine.get_universe(uni)
    dim_norm = max(0.0, min(1.0, float(dim_norm)))
    if "dimmer" in cm:
        uni_buf.set_channel(addr + cm["dimmer"], int(round(dim_norm * 255)))
        return
    if "red" in cm:
        # Scale current colour by dim. Read what's already on the wire
        # so we don't clobber the operator's chosen hue.
        try:
            cur_r = uni_buf.get_channel(addr + cm["red"])
            cur_g = uni_buf.get_channel(addr + cm["green"])
            cur_b = uni_buf.get_channel(addr + cm["blue"])
        except Exception:
            cur_r, cur_g, cur_b = 255, 255, 255
        if cur_r == 0 and cur_g == 0 and cur_b == 0:
            cur_r = cur_g = cur_b = 255
        scale = dim_norm
        _set_fixture_color(engine, uni, addr,
                            int(cur_r * scale),
                            int(cur_g * scale),
                            int(cur_b * scale), prof_info)
        return
    # Wheel-only with no dimmer — binary on/off only.
    _set_fixture_lamp(engine, uni, addr, dim_norm > 0.05, prof_info)


def _set_fixture_blackout(engine, uni, addr, prof_info):
    """Atomic safe state — dimmer 0, shutter closed, strobe off, RGB 0,
    pan/tilt left where they are. Used by Stop-All and SMART
    error/cancel parking. Equivalent to ``_set_fixture_lamp(on=False)``
    today; kept as a separate name so future safe-state additions
    (e.g. lamp-off command on profiles that have one) land here without
    revisiting every call site."""
    _set_fixture_lamp(engine, uni, addr, False, prof_info)

# ── Remote-orientation primitive (#484) — initialised first so the
#    mover-follow engine below can read it. ────────────────────────────────

from remote_orientation import RemoteRegistry, KIND_GYRO, KIND_PHONE

_remotes = RemoteRegistry(data_path=str(DATA / "remotes.json"))
_remotes.load()


# #784 PR-7 — `parametric_mover.ParametricFixtureModel` /
# `mover_calibrator.pan_tilt_to_ray` deleted along with the SMART
# pipeline. The mover-control engine no longer reads a parametric
# model; runtime IK lives entirely in `aim/sphere.AimSphere`.


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


# #784 PR-7 — `_get_mover_model` / `_invalidate_mover_model` /
# `_mover_models` cache deleted with the parametric model. The new aim
# sphere is built per-call in `aim/routes._get_or_build_sphere` (cached
# by fixture id + home + rotation + xyz + profile).


# ── Mover-follow engine (#468) — consumer of the primitive (#484 phase 4) ──
from mover_control import MoverControlEngine
from claim_arbiter import ClaimArbiter

_mover_engine = MoverControlEngine(
    get_fixtures=lambda: _fixtures,
    get_layout=lambda: _layout,
    get_profile_info=lambda pid: _profile_lib.channel_info(pid) if pid else None,
    get_engine=lambda: _artnet if _artnet.running else (_sacn if _sacn.running else None),
    set_fixture_color_fn=_set_fixture_color,
    get_remote_by_device_id=lambda did: _remotes.by_device(did),
    get_mover_cal=lambda mid: _mover_cal.get(str(mid)),
    is_calibrating=_fixture_is_calibrating,
    get_claim_ttl_s=lambda: float(_cal_tuning("moverClaimTtlS")),
    # #762 — engine-wide default OrientConvention (settings.json
    # ``moverControl.orientConvention``). None = no engine-level pin, fall
    # through to per-fixture / per-remote defaults. Read live so a settings
    # toggle doesn't require a server restart.
    get_default_convention=lambda: (
        _settings.get("moverControl", {}).get("orientConvention")
        if isinstance(_settings.get("moverControl"), dict) else None),
    # #800 — split park primitives per operator clarification:
    # `park_pan_tilt_fn` (pan/tilt only, no lamp) for `start_stream`
    # so the engine pump's claim.dimmer write isn't immediately
    # contradicted; `park_fn` (full park + lamp_off) for explicit
    # `release` where idle = home + dark.
    park_pan_tilt_fn=lambda mid: _park_fixture_pan_tilt_only(mid),
    park_fn=lambda mid: _park_fixture_at_home(mid),
    # #843 — claim-writer brightness scaling. Live globalBrightness
    # read snapshotted once per `_write_dmx` so a mid-write value
    # change can't tear the dimmer / RGB outputs. Lambda-wrapped so
    # `_scale_for_brightness` (defined further down the file) is
    # resolved lazily.
    get_global_brightness=lambda: _settings.get("globalBrightness", 255),
    scale_for_brightness=lambda v, g: _scale_for_brightness(v, g),
    # #806 — every claim/orient tick that produces a stage-frame aim
    # vector pushes it through here, so calibrate-end / live-render
    # observe the same vector the engine pump used (root-cause fix
    # for the #805 head-jump on calibrate-end).
    set_canonical_aim_fn=_set_canonical_aim_stage,
)
_mover_engine.start()


def _cold_start_park_calibrated_movers():
    """#800 — at orchestrator boot, walk every calibrated DMX mover and
    drive it to its Home anchor. Without this the universe buffer holds
    whatever was last on the wire (or zero / saved snapshot), and
    fixtures sit at stale poses until an operator claims them.

    Operator clarification 2026-05-03: pan/tilt-only — the existing
    cold-start rainbow-blink routine owns lamp behaviour. Calling
    `_park_fixture_at_home` (which also runs `lamp_off`) would clobber
    the blink's terminal state. The home-write fires here BEFORE the
    blink runs; the blink keeps its existing lamp choreography.

    Non-fatal per-fixture so one broken fixture can't block the rest.
    """
    parked = 0
    for f in _fixtures:
        try:
            if f.get("fixtureType") != "dmx":
                continue
            if f.get("homePanDmx16") is None or f.get("homeTiltDmx16") is None:
                continue
            if not f.get("homeSecondary"):
                continue
            _park_fixture_pan_tilt_only(int(f["id"]))
            parked += 1
        except Exception as e:
            log.debug("cold-start park: fid=%s failed: %s", f.get("id"), e)
    if parked:
        log.info("Cold-start: parked %d calibrated mover(s) at Home", parked)


# Defer the cold-start park until the engine has settled — it lives at
# the bottom of orchestrator init alongside the other deferred startup
# tasks. The DMX engine has to be running before the writes hit the
# wire; `_aim_get_engine()` returns None until then.
def _cold_start_park_when_engine_ready():
    """Run `_cold_start_park_calibrated_movers` once the DMX engine is
    actually pumping. Polls every 250 ms for up to 10 s — typical
    ArtNet bind is sub-second; the long ceiling tolerates slow boots
    on the embedded targets."""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _aim_get_engine() is not None:
            _cold_start_park_calibrated_movers()
            return
        time.sleep(0.25)
    log.info("Cold-start park skipped: DMX engine never came up "
             "within 10 s of orchestrator boot")


threading.Thread(target=_cold_start_park_when_engine_ready,
                 daemon=True, name="cold-start-park").start()

# #813 — gyro auto-lock loop deleted. Press-Start on the gyro firmware
# (`CMD_GYRO_START`) is the sole claim trigger; the orchestrator never
# spontaneously reaches out to a gyro during idle. `_gyro_inactive_transition`
# is still wired into the PUT /api/fixtures gyroEnabled=False handler so
# operator-initiated Active→Inactive transitions still release the claim
# and notify the gyro via `CMD_GYRO_CTRL(0)`.

# #763 — arbiter facade between the show writer and the universe buffer.
# Reads claim state from the engine, exposes is_muted() so the playback +
# track loops can skip writes for claimed fixtures, and tracks a 750 ms
# post-release slew window for smooth handover via pan-tilt-speed.
_claim_arbiter = ClaimArbiter(_mover_engine.get_status)


@app.post("/api/mover-control/claim")
def api_mover_claim():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId", "")
    dname = body.get("deviceName", "Unknown")
    dtype = body.get("deviceType", "android")
    # `smoothing` request-body field is ignored as of #877 — the
    # orchestrator no longer transforms the aim vector. Field stays
    # accepted by `request.get_json` so old SPA / Android builds don't
    # 400 on the POST.
    # #762 — optional per-claim OrientConvention override. Accepts the enum
    # string ("bottom_forward" / "flat_pitch_yaw") so an operator/UI can
    # request a non-default grip for this session without changing the
    # fixture or settings. Unknown values fall back to the resolved default.
    conv = body.get("orientConvention")
    if mid is None:
        return jsonify(ok=False, err="moverId required"), 400
    # #823 — explicit claim API call IS the operator's "I'm using this
    # remote now" gesture; clear any hard-stale flag on the matching
    # Remote BEFORE the claim lands so the engine tick won't auto-
    # release the new claim on its next iteration. Mirrors the
    # CMD_GYRO_START handler.
    if did:
        remote_pre = _remotes.by_device(did)
        if remote_pre is not None and remote_pre.stale_reason is not None:
            log.info("MOVER_CLAIM from %s — clearing stale_reason=%s",
                     did, remote_pre.stale_reason)
            remote_pre.clear_stale()
    # #888 §6.2 — force=true releases any prior holder before claiming.
    # Used by the Android TakeoverSheet flow when the operator confirms
    # they want to grab a mover that's currently held by someone else.
    if bool(body.get("force")):
        try: _mover_engine.release(mid, blackout=False)
        except Exception: log.warning("force claim: prior release failed", exc_info=True)
    ok, reason = _mover_engine.claim(mid, did, dname, dtype,
                                     convention=conv)
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
            kind = KIND_PHONE if dtype == "android" else KIND_GYRO
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
    # #763 — start the smooth-handover slew window. Show writer will cap
    # pan-tilt-speed for the next 750 ms so the fixture eases from the
    # operator's last pose to the show's current pose instead of snapping.
    if ok and mid is not None:
        _claim_arbiter.on_release(mid)
    # #647 / #650 — surface engine state so the client can tell
    # "release + blackout wrote zeros" from "engine stopped so the
    # blackout silently dropped". Same signal shape as /start.
    health = _mover_engine.get_engine_health()
    return jsonify(ok=ok, engineRunning=health["running"])

@app.post("/api/mover-control/start")
def api_mover_start():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.start_stream(mid, did)
    # #647 — flag engine-stopped condition so the client knows writes
    # won't hit the wire even though the claim is "streaming".
    health = _mover_engine.get_engine_health()
    return jsonify(ok=ok, engineRunning=health["running"])

@app.post("/api/mover-control/calibrate-start")
def api_mover_cal_start_ctrl():
    """Mark the mover as calibrating so the engine holds DMX steady.

    The orientation math runs on the Remote object — if body includes
    `targetObjectId` or none is given, we also drive the primitive's
    calibrate-start through the device's Remote (via _remotes.by_device).

    #688 — also surfaces the captured reference pan/tilt so the SPA
    can display "Reference: pan=N tilt=M" — the orientation deltas
    streamed in subsequent /orient calls are relative to this anchor.
    """
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.calibrate_start(mid, did)
    if not ok:
        return jsonify(ok=False, err="Not claimed or wrong device"), 403
    # Read back the claim to surface the reference pan/tilt that the
    # subsequent /orient calls are deltas from.
    ref_pan = ref_tilt = None
    try:
        for c in _mover_engine.status().get("claims", []):
            if c.get("moverId") == mid:
                ref_pan = c.get("panNorm")
                ref_tilt = c.get("tiltNorm")
                break
    except Exception:
        pass
    return jsonify(ok=True, refPan=ref_pan, refTilt=ref_tilt)

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
    if aim_stage is None:
        # #806 — the canonical store had nothing for this fixture and
        # the sphere read path also failed. Surface a clear error so
        # the operator can fix the underlying data (Home/Secondary not
        # set) instead of locking the remote against a wrong vector
        # (the #805 silent-fallback bug).
        return jsonify(
            ok=False,
            err="aim_unresolvable",
            detail=("Could not determine the head's current aim direction. "
                    "Confirm Home + Secondary are saved and the fixture "
                    "has been parked or aimed at least once this session."),
        ), 400
    try:
        # #805 — prefer the native (w, x, y, z) quaternion when the
        # client supplies one. Android's roll/pitch/yaw is extracted
        # via `getOrientation` (Android-specific axis + composition
        # convention) but its orient stream sends a raw quaternion.
        # Routing the calibrate snapshot through `quat_from_euler_zyx_deg`
        # (aerospace ZYX intrinsic) describes a different physical
        # orientation than the next /orient packet — the phone's
        # post-calibrate aim flips by ~117° as a result. Quaternions
        # carry no axis-convention ambiguity. Falls back to roll/pitch
        # /yaw for the gyro controller (IMU-native ZYX) and older clients.
        quat = body.get("quat")
        if isinstance(quat, list) and len(quat) == 4:
            remote.calibrate(
                target_aim_stage=aim_stage,
                target_info={"objectId": mover["id"], "kind": "mover"},
                quat=quat,
            )
        else:
            remote.calibrate(
                target_aim_stage=aim_stage,
                target_info={"objectId": mover["id"], "kind": "mover"},
                roll=body.get("roll"),
                pitch=body.get("pitch"),
                yaw=body.get("yaw"),
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
    # #688 — when a moverId is supplied (the Android app aiming a
    # specific mover, not just a free-form orientation update), reject
    # devices that don't own the claim. Pre-fix this endpoint always
    # returned ok=True; the wrong-device guard only fired downstream
    # in the tick loop, so the API surfaced a misleading "success" to
    # tests + clients. The auto-register path below stays for the no-
    # moverId case (free-form Android phone updating its own remote).
    mid_check = body.get("moverId")
    if mid_check is not None:
        # get_claim returns a dict (via MoverClaim.to_dict()), not the
        # MoverClaim instance — read the wire-format key.
        claim = _mover_engine.get_claim(mid_check)
        if claim is not None and claim.get("deviceId") != did:
            return jsonify(ok=False, err="Wrong device — claim is held "
                            "by another device"), 403
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
    # #647 — same-request engine-stopped signal for the orient path.
    health = _mover_engine.get_engine_health()
    return jsonify(ok=True, engineRunning=health["running"])


@app.post("/api/mover-control/color")
def api_mover_color():
    body = request.get_json(silent=True) or {}
    mid = body.get("moverId")
    did = body.get("deviceId")
    ok = _mover_engine.set_color(mid, did, body.get("r", 255), body.get("g", 255), body.get("b", 255),
                                  dimmer=body.get("dimmer"))
    # #647 — flag engine-stopped; the set_color write path sits on the tick
    # loop which silently drops frames when the engine is down.
    health = _mover_engine.get_engine_health()
    return jsonify(ok=ok, engineRunning=health["running"])


@app.post("/api/mover-control/smoothing")
def api_mover_set_smoothing():
    """#877 — endpoint kept for back-compat with older SPA / Android
    builds but now a no-op. The orchestrator no longer smooths or
    speed-caps the aim vector; the fixture is responsible for its
    own motor speed."""
    return jsonify(ok=True, deprecated=True)


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
    # #647 — expose engine-running + dropped-write counters so operators can
    # diagnose the "orient streams but nothing moves" case. Android Status
    # tab polls this endpoint.
    return jsonify(claims=_mover_engine.get_status(),
                    engine=_mover_engine.get_engine_health())


@app.post("/api/mover-control/all-home")
def api_mover_all_home():
    """Send every moving-head fixture to its configured home pose. #888.

    Used by the Grab page "Send all home" safety button. Walks every
    DMX fixture with a non-zero panRange (i.e. mover), reads its
    persisted home anchor, and writes the home pan/tilt + blackout
    dimmer to the universe buffer. Fixtures currently held by a claim
    are skipped — operators shouldn't yank a fixture out from under
    their own controller-mode session.

    Returns: {ok, moved: int, skipped: list[fid], engineRunning: bool}.
    """
    if not (_artnet.running or _sacn.running):
        return jsonify(ok=False, err="DMX engine not running",
                       engineRunning=False), 503
    snap = _claim_arbiter.snapshot()
    moved = 0
    skipped = []  # list of {id, reason} so the operator UI can render hints
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        fid = f.get("id")
        pid = f.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        if not info or int(info.get("panRange", 0) or 0) <= 0:
            continue  # not a mover
        if _claim_arbiter.is_muted(fid, snap):
            skipped.append({"id": fid, "reason": "claimed"})
            continue
        home_pan = f.get("homePanDmx16")
        home_tilt = f.get("homeTiltDmx16")
        if home_pan is None or home_tilt is None:
            skipped.append({"id": fid, "reason": "no_home"})
            continue
        try:
            uni = int(f.get("dmxUniverse", 1) or 1)
            addr = int(f.get("dmxStartAddr", 1) or 1)
            engine = _artnet if _artnet.running else _sacn
            buf = engine.get_universe(uni)
            profile = {"channel_map": info.get("channel_map", {}),
                       "channels":    info.get("channels", [])}
            buf.set_fixture_pan_tilt(addr, int(home_pan), int(home_tilt), profile)
            # Blackout the lamp so a homed fixture doesn't blast the floor.
            if "dimmer" in info.get("channel_map", {}):
                buf.set_fixture_dimmer(addr, 0, profile)
            moved += 1
        except Exception:
            log.warning("all-home: fixture %s failed", fid, exc_info=True)
            skipped.append({"id": fid, "reason": "error"})
    return jsonify(ok=True, moved=moved, skipped=skipped, engineRunning=True)

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
    """Read the mover's current aim direction in stage coordinates.

    #806 phase 2 (final): this is now a pure canonical-store lookup.
    Every writer that drives a moving head's pan/tilt populates the
    canonical store as part of the same DMX commit (claim/orient,
    `/api/mover/<fid>/aim`, park-at-home, Track action, timeline-bake
    playback). The reader has zero IK responsibility — there's no
    `dmx_to_aim` round-trip and no mount-relative fallback ladder, so
    the silent-wrong-vector failure mode behind #805 / #757-B / #748
    is structurally impossible.

    Three return states:
      - 3-tuple `(vx, vy, vz)`: canonical aim is set; head is
        committed to this stage-frame direction.
      - `None` because the slot was explicitly nulled by a raw
        DMX-test write: the operator overrode the head outside the
        canonical pipeline; calibrate-end correctly returns
        `aim_unresolvable` so the operator re-aims first.
      - `None` because no writer has run yet for this fixture this
        session (cold-start before park, fresh-imported fixture
        before its first claim/aim/track tick): same `aim_unresolvable`
        path. Operator parks-or-aims and retries.
    """
    fid = mover.get("id")
    if fid is None:
        return None
    cached = _get_canonical_aim_stage(int(fid))
    if cached is not None:
        return cached
    # Slot might be explicitly None (raw-DMX-driven) or absent
    # entirely (no writer has fired yet). Both surface as None — the
    # caller's `aim_unresolvable` path handles them identically.
    if int(fid) not in _canonical_aim_stage:
        log.warning(
            "_mover_current_aim_stage(fid=%s): no canonical aim recorded "
            "for this fixture this session. Park / claim / aim it once "
            "before calibrate-end.", fid)
    else:
        log.warning(
            "_mover_current_aim_stage(fid=%s): canonical slot is null "
            "(raw DMX-test override active). Re-aim via claim/track/park "
            "before calibrate-end.", fid)
    return None


def _auto_register_remote(device_id, kind=KIND_GYRO):
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
    name = f"Gyro {device_id.split('-', 1)[-1]}" if kind == KIND_GYRO else f"Phone {device_id.split('-', 1)[-1]}"
    return _remotes.add(name=name, kind=kind, device_id=device_id, pos=pos)


# CRUD routes ──────────────────────────────────────────────────────────────

@app.get("/api/remotes")
def api_remotes_list():
    return jsonify(remotes=[r.to_persisted_dict() for r in _remotes.list()])


@app.post("/api/remotes")
def api_remotes_create():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", KIND_GYRO)
    if kind not in (KIND_GYRO, KIND_PHONE):
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


@app.post("/api/remotes/<int:remote_id>/grip")
def api_remote_grip(remote_id):
    """#757 Issue A — set per-remote body-frame `forward_local` /
    `up_local`. Call from the Android app at session start (or when
    the operator changes grip), or from the gyro controller's mount-config
    flow. Both vectors are 3-element lists of floats. Either may be
    omitted to leave the corresponding axis unchanged.

    Body: ``{forwardLocal: [x,y,z], upLocal: [x,y,z]}``.

    Calibration is invalidated if either vector changes — the prior
    R_world_to_stage was computed with the old axes baked in, so the
    operator must re-run calibrate against a known target.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    fwd = body.get("forwardLocal")
    up = body.get("upLocal")
    if fwd is None and up is None:
        return jsonify(ok=False, err="forwardLocal and/or upLocal required"), 400
    try:
        if fwd is not None and (not isinstance(fwd, (list, tuple)) or len(fwd) != 3):
            return jsonify(ok=False, err="forwardLocal must be [x,y,z]"), 400
        if up is not None and (not isinstance(up, (list, tuple)) or len(up) != 3):
            return jsonify(ok=False, err="upLocal must be [x,y,z]"), 400
        r.set_grip(forward_local=fwd, up_local=up)
    except (TypeError, ValueError) as e:
        return jsonify(ok=False, err=str(e)), 400
    _remotes.save()
    return jsonify(ok=True, remote=r.to_persisted_dict())


@app.post("/api/remotes/grip")
def api_remote_grip_by_device():
    """#816 — deviceId-keyed sibling of `/api/remotes/<remote_id>/grip`.
    Phone clients only know their own ``deviceId`` (not the integer
    remote-row id), so they POST here with ``{deviceId, forwardLocal,
    upLocal}`` when entering Controller mode. Auto-registers a phone
    Remote if one doesn't exist yet — same shape as the orient handler's
    auto-register so the first claim from a fresh device works without
    a separate /api/remotes POST.
    """
    body = request.get_json(silent=True) or {}
    did = (body.get("deviceId") or "").strip()
    if not did:
        return jsonify(ok=False, err="deviceId required"), 400
    fwd = body.get("forwardLocal")
    up = body.get("upLocal")
    if fwd is None and up is None:
        return jsonify(ok=False, err="forwardLocal and/or upLocal required"), 400
    if fwd is not None and (not isinstance(fwd, (list, tuple)) or len(fwd) != 3):
        return jsonify(ok=False, err="forwardLocal must be [x,y,z]"), 400
    if up is not None and (not isinstance(up, (list, tuple)) or len(up) != 3):
        return jsonify(ok=False, err="upLocal must be [x,y,z]"), 400
    r = _remotes.by_device(did) or _auto_register_remote(did, kind=KIND_PHONE)
    try:
        r.set_grip(forward_local=fwd, up_local=up)
    except (TypeError, ValueError) as e:
        return jsonify(ok=False, err=str(e)), 400
    _remotes.save()
    return jsonify(ok=True, remote=r.to_persisted_dict())


@app.delete("/api/remotes/<int:remote_id>")
def api_remotes_delete(remote_id):
    # #690 — idempotent: 200 either way, with a `removed` flag the SPA
    # can use to distinguish "deleted just now" from "already gone".
    r = _remotes.remove(remote_id)
    return jsonify(ok=True, removed=r is not None)


# #826 — empirical aim-axis calibration wizard. Operator captures three
# pose quaternions (neutral, pitch-forward-down, yaw-left); the math
# below derives the body-frame `forward_local` / `up_local` directly
# from the measured rotations instead of guessing them from a
# Surface.ROTATION_* table that varied by phone model / OS / sensor
# fusion algorithm. The qz-negate hack from #824 becomes obsolete once
# every device runs the wizard; the kind-specific branches in
# `_apply_quat` collapse to one shared math path.
def _aim_wizard_compute(poses):
    """Derive (forward_local, up_local) from three operator gestures.

    `poses` is a dict mapping role → quat tuple ``(w, x, y, z)``. Required
    roles: ``neutral``, ``pitch_forward``, ``yaw_left``. Optional:
    ``roll_cw`` (sanity check only).

    Returns ``(forward_local, up_local, err_dict, diag)``. On success
    ``err_dict`` is ``None`` and ``diag`` carries every math
    intermediate the operator (or developer) might want to inspect.
    On failure ``forward_local`` / ``up_local`` are ``None`` and
    ``err_dict`` carries ``{err, detail}``; ``diag`` still carries
    whatever was computed up to the rejection point so the SPA can
    show the operator EXACTLY what their gestures delivered to the
    server. This is what was missing from the pre-#885-followup
    error renderer — operators saw "cross magnitude 0.37" but no way
    to know which capture went wrong.

    Math (issue spec):
    1. ΔQ_pitch_body = conj(Q_neutral) · Q_pitch_fwd  (rotation expressed in body frame)
    2. ΔQ_yaw_body   = conj(Q_neutral) · Q_yaw_left
    3. axis-angle of each Δ → unit pitch/yaw axes (body-frame)
    4. forward_local = cross(yaw_axis_body, pitch_axis_body)  (right-hand rule)
    5. up_local      = cross(forward_local, pitch_axis_body)   (chirality-locked)

    Sign is locked by the gesture instructions ("tilt DOWN", "yaw
    LEFT") — no hand-waving about which way is positive.
    """
    diag = {
        "inputQuats":       {},
        "normalizedQuats":  {},
        "deltaQuats":       {},
        "pitchAngleDeg":    None,
        "yawAngleDeg":      None,
        "pitchAxis":        None,
        "yawAxis":          None,
        "crossMagnitude":   None,
        "forwardLocal":     None,
        "upLocal":          None,
    }
    # Snapshot the raw caller-supplied quats so the SPA can show what
    # actually arrived at the server.
    for role, q in poses.items():
        try:
            diag["inputQuats"][role] = [float(c) for c in q]
        except (TypeError, ValueError):
            diag["inputQuats"][role] = list(q) if q else None

    needed = ("neutral", "pitch_forward", "yaw_left")
    for role in needed:
        if role not in poses:
            return None, None, {"err": "missing_pose",
                                "detail": f"Pose '{role}' is required."}, diag

    def _normq(q):
        w, x, y, z = q
        m = math.sqrt(w * w + x * x + y * y + z * z)
        if m < 0.95 or m > 1.05:
            return None, m
        return (w / m, x / m, y / m, z / m), m

    def _conj(q):
        w, x, y, z = q
        return (w, -x, -y, -z)

    def _qmul(a, b):
        aw, ax, ay, az = a
        bw, bx, by, bz = b
        return (
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        )

    def _axis_angle(q):
        """Unit-axis + angle (radians) of a unit quaternion."""
        w, x, y, z = q
        # Clamp w to [-1, 1] to avoid acos domain errors on near-unity quats.
        w_clamped = max(-1.0, min(1.0, w))
        angle = 2.0 * math.acos(w_clamped)
        s = math.sqrt(max(0.0, 1.0 - w_clamped * w_clamped))
        if s < 1e-6:
            return (0.0, 0.0, 1.0), 0.0
        return (x / s, y / s, z / s), angle

    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def _norm(v):
        return math.sqrt(sum(c * c for c in v))

    # Validate sensor stability across captures.
    quats = {}
    for role in ("neutral", "pitch_forward", "yaw_left"):
        nq, m = _normq(poses[role])
        if nq is None:
            return None, None, {
                "err": "bad_quaternion",
                "detail": f"Pose '{role}' quaternion magnitude "
                          f"{m:.3f} outside [0.95, 1.05] — "
                          "the sensor wasn't stable. Hold "
                          "still and retry."}, diag
        quats[role] = nq
        diag["normalizedQuats"][role] = list(nq)

    # Body-frame delta rotations from neutral.
    dq_pitch = _qmul(_conj(quats["neutral"]), quats["pitch_forward"])
    dq_yaw   = _qmul(_conj(quats["neutral"]), quats["yaw_left"])
    diag["deltaQuats"]["pitch"] = list(dq_pitch)
    diag["deltaQuats"]["yaw"]   = list(dq_yaw)

    pitch_axis, pitch_angle = _axis_angle(dq_pitch)
    yaw_axis,   yaw_angle   = _axis_angle(dq_yaw)
    diag["pitchAxis"]     = list(pitch_axis)
    diag["yawAxis"]       = list(yaw_axis)
    diag["pitchAngleDeg"] = round(math.degrees(pitch_angle), 3)
    diag["yawAngleDeg"]   = round(math.degrees(yaw_angle), 3)

    # Reject if either gesture rotated < 10° — operator didn't move enough.
    MIN_ANGLE_RAD = math.radians(10)
    if pitch_angle < MIN_ANGLE_RAD:
        return None, None, {
            "err": "insufficient_pitch",
            "detail": f"Pitch gesture rotated only "
                      f"{math.degrees(pitch_angle):.1f}° from "
                      "neutral. Tilt the phone further "
                      "forward and retry."}, diag
    if yaw_angle < MIN_ANGLE_RAD:
        return None, None, {
            "err": "insufficient_yaw",
            "detail": f"Yaw gesture rotated only "
                      f"{math.degrees(yaw_angle):.1f}° from "
                      "neutral. Yaw the phone further left "
                      "and retry."}, diag

    # Reject near-parallel pitch/yaw axes (cross magnitude < 0.7
    # implies the operator did similar gestures or sensor noise
    # dominated). Both axes are unit vectors so |cross| ≤ 1.
    fwd = _cross(yaw_axis, pitch_axis)
    fwd_mag = _norm(fwd)
    diag["crossMagnitude"] = round(fwd_mag, 4)
    if fwd_mag < 0.7:
        return None, None, {
            "err": "degenerate_axes",
            "detail": (f"Pitch and yaw gestures rotated around nearly-"
                       f"parallel axes (cross magnitude {fwd_mag:.2f}). "
                       "Please retry yaw — turn further from neutral, "
                       "and make sure pitch and yaw are perpendicular.")}, diag

    forward_local = (fwd[0] / fwd_mag, fwd[1] / fwd_mag, fwd[2] / fwd_mag)
    diag["forwardLocal"] = list(forward_local)
    # #826 — `up_local` was previously `yaw_axis`, but yaw_axis has
    # ambiguous sign: when the operator yaws CW vs CCW for "stage-LEFT"
    # the extracted positive-sense axis flips, and a `up_local` anti-
    # parallel to the operator's true body-up makes calibrate insert a
    # 180° flip in `R_world_to_stage` that cancels the yaw correction.
    # Empirical: aim_stage.x came out -0.5 (stage-RIGHT) for both
    # gesture directions even though the wizard claimed to derive
    # correct axes (operator comment 2026-05-09 + roundtrip test). The
    # fix is to derive up by enforcing right-handed chirality against
    # the unambiguous pitch_axis: `cross(forward, pitch_axis)` always
    # picks the body-up direction that makes (forward, up, -pitch_axis)
    # a right-handed triple, independent of yaw direction.
    up_raw = _cross(forward_local, pitch_axis)
    up_mag = _norm(up_raw)
    if up_mag < 1e-6:
        # Pitch and forward are parallel — shouldn't happen because
        # forward = cross(yaw, pitch) is by construction perpendicular
        # to pitch. Surface as degenerate axes if it does.
        return None, None, {
            "err": "degenerate_axes",
            "detail": ("Wizard math produced parallel forward and "
                       "pitch axes — please retry the wizard.")}, diag
    up_local = (up_raw[0] / up_mag, up_raw[1] / up_mag, up_raw[2] / up_mag)
    diag["upLocal"] = list(up_local)

    # Optional roll sanity check: roll axis should be ~co-linear with
    # forward_local. |dot| > 0.85 → frame is orthogonal as expected.
    if "roll_cw" in poses:
        nq_roll, _ = _normq(poses["roll_cw"])
        if nq_roll is not None:
            dq_roll = _qmul(_conj(quats["neutral"]), nq_roll)
            roll_axis, _ra = _axis_angle(dq_roll)
            dot = sum(roll_axis[i] * forward_local[i] for i in range(3))
            diag["rollAxis"] = list(roll_axis)
            diag["rollDotForward"] = round(dot, 4)
            if abs(dot) < 0.85:
                return None, None, {
                    "err": "non_orthogonal_frame",
                    "detail": (f"Roll-axis dot product with derived forward "
                               f"is {dot:+.2f}; expected close to ±1. The "
                               "captured frame isn't orthogonal — please "
                               "retry the wizard.")}, diag

    return forward_local, up_local, None, diag


def _apply_aim_wizard_to_remote(remote, poses):
    """Run the wizard math against `poses` and apply the result to
    `remote`. Returns ``(ok, response_dict, http_status)``.

    The response dict always carries a ``diagnostics`` block (#885
    follow-up) so a failure renderer can show the operator the exact
    quats the server saw and the math intermediates that drove the
    rejection. Pre-fix the SPA rendered only the human-readable
    `detail` string; operators hit ``degenerate_axes`` ("cross
    magnitude 0.37") with no way to know which capture went wrong.
    """
    fwd, up, err, diag = _aim_wizard_compute(poses)
    if err is not None:
        return False, {"ok": False, "diagnostics": diag, **err}, 400
    try:
        remote.set_grip(forward_local=fwd, up_local=up)
    except (TypeError, ValueError) as e:
        return False, {"ok": False, "err": "set_grip_failed",
                       "detail": str(e),
                       "diagnostics": diag}, 400
    # #826 — invalidating the existing R_world_to_stage matches the
    # `set_grip` semantics (axis change → cal stale). Operator must
    # re-anchor against a known target after wizard runs.
    return True, {"ok": True,
                  "forwardLocal": list(fwd),
                  "upLocal": list(up),
                  "diagnostics": diag}, 200


def _parse_wizard_payload(body):
    """Extract `{role: (w,x,y,z)}` from a wizard request body.
    Body shape per #826:
        {deviceId?, poses: [{role, quat: [w,x,y,z]}, ...]}
    """
    out = {}
    for entry in (body.get("poses") or []):
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        quat = entry.get("quat")
        if not role or not isinstance(quat, (list, tuple)) or len(quat) != 4:
            continue
        try:
            out[role] = tuple(float(c) for c in quat)
        except (TypeError, ValueError):
            continue
    return out


# #885 — stale-remote guard for the aim-wizard routes. When a Remote
# is stale (the orient session ended, the puck/phone left the network,
# etc.), `last_quat_world` is the *cached* last reading, not a live
# value. Pre-#885 the SPA's Capture flow polled the diagnostic
# endpoint, got the same cached quat three times in a row, and the
# server math correctly rejected with `insufficient_pitch` because
# Q_neutral == Q_pitch_forward == Q_yaw_left. The operator's actual
# wrist motion couldn't be honoured because no orient packets were
# flowing to refresh the cache.
#
# Reject server-side with a specific `gyro_not_streaming` code so
# both routes (and any external caller, not just the SPA) get the
# truthful diagnosis. The SPA's #878 error renderer already surfaces
# `detail` strings via the existing `r.err`/`r.detail` plumbing.
_STALE_WIZARD_MSG = ("Gyro is idle — press Start first, then re-run "
                     "Calibrate.")


def _reject_if_stale(r):
    """Return a (resp, status) tuple to send back when the Remote can't
    drive a fresh wizard run, or ``None`` if the Remote is live."""
    if r.stale_reason is not None:
        return ({"ok": False, "err": "gyro_not_streaming",
                 "detail": _STALE_WIZARD_MSG,
                 "staleReason": r.stale_reason}, 400)
    return None


@app.post("/api/remotes/<int:remote_id>/aim-wizard")
def api_remote_aim_wizard(remote_id):
    """#826 — empirical aim-axis wizard, by remote-row id."""
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not_found"), 404
    stale = _reject_if_stale(r)
    if stale is not None:
        return jsonify(stale[0]), stale[1]
    body = request.get_json(silent=True) or {}
    poses = _parse_wizard_payload(body)
    ok_, resp, status = _apply_aim_wizard_to_remote(r, poses)
    if ok_:
        _remotes.save()
        log.info("Aim wizard: remote=%d device=%s forward=%s up=%s",
                 remote_id, r.device_id, resp["forwardLocal"], resp["upLocal"])
    return jsonify(resp), status


@app.post("/api/remotes/aim-wizard")
def api_remote_aim_wizard_by_device():
    """#826 — deviceId-keyed sibling. Mirrors `/api/remotes/grip`'s
    auto-register-on-first-use behaviour for phone clients that only
    know their own deviceId, not the integer remote-row id."""
    body = request.get_json(silent=True) or {}
    did = (body.get("deviceId") or "").strip()
    if not did:
        return jsonify(ok=False, err="deviceId required"), 400
    poses = _parse_wizard_payload(body)
    # #878 — auto-register kind defaults to PHONE for back-compat, but
    # a `gyro-<ip>` deviceId is unambiguously a gyro, and the
    # SPA-driven wizard path (gyro controller config page) always uses
    # that shape. Pick KIND_GYRO when the prefix matches so a fresh
    # wizard run lands the right kind on the new Remote.
    auto_kind = KIND_GYRO if did.startswith("gyro-") else KIND_PHONE
    existing = _remotes.by_device(did)
    # #885 — only enforce the stale-guard against an existing Remote.
    # A first-time auto-register has no orient history yet and the
    # wizard math itself will reject with `bad_quaternion` if the
    # caller didn't supply real quats; we don't want to refuse the
    # very first call before the Remote even exists.
    if existing is not None:
        stale = _reject_if_stale(existing)
        if stale is not None:
            return jsonify(stale[0]), stale[1]
    r = existing or _auto_register_remote(did, kind=auto_kind)
    ok_, resp, status = _apply_aim_wizard_to_remote(r, poses)
    if ok_:
        _remotes.save()
        log.info("Aim wizard: device=%s remote=%d forward=%s up=%s",
                 did, r.id, resp["forwardLocal"], resp["upLocal"])
    return jsonify(resp), status


@app.get("/api/remotes/live")
def api_remotes_live():
    """List live remotes (gyro gyros, phone-claim) plus #849 virtual
    Auto Brightness entries derived from `_brightness_obs`. The dashboard
    Remote Controllers card consumes this single endpoint and renders all
    sources uniformly so the operator can see, at a glance, every
    device that's currently driving the rig."""
    snap = list(_remotes.live_list())
    # Stamp the operator-assigned gyro fixture name onto each
    # gyro-* remote so the Dashboard Remote Controllers card can
    # render it directly without having to walk children + fixtures
    # in the browser (the SPA's `window._children` and `window._fixtures`
    # globals aren't populated; doing the resolution server-side is the
    # single source of truth). Resolution chain: deviceId `gyro-<ip>`
    # → child by IP → gyro fixture with matching `gyroChildId` → name.
    for r in snap:
        did = r.get("deviceId") or ""
        if not did.startswith("gyro-"):
            continue
        ip = did[5:]
        child = next((c for c in _children if c.get("ip") == ip), None)
        if child is None:
            continue
        gf = next((f for f in _fixtures
                   if f.get("fixtureType") == "gyro"
                   and f.get("gyroChildId") == child.get("id")
                   and f.get("name")), None)
        if gf is not None:
            r["fixtureName"] = gf["name"]
    # #849 Part 2 — surface Android Auto Brightness as a virtual remote
    # so the operator can see whether `/api/brightness` traffic is
    # actually arriving and which IP is driving. Pre-fix the Android
    # UI showed envelope animation locally even when no POST reached
    # the orchestrator; the dashboard had no way to surface that.
    now = time.time()
    with _brightness_obs_lock:
        # Prune entries dormant > 5 min so the registry doesn't
        # accumulate every IP that ever POSTed once.
        stale_keys = [ip for ip, st in _brightness_obs.items()
                      if (now - st.get("last_log_ts", 0)) > 300]
        for ip in stale_keys:
            _brightness_obs.pop(ip, None)
        for remote_ip, st in _brightness_obs.items():
            last_age = now - st.get("last_log_ts", 0)
            # #862 — read `current_value` (updated every hop) rather than
            # `last_log_value` (only on rate-limited log emissions) so
            # the dashboard reflects the live audio envelope.
            cur_value = st.get("current_value", st.get("last_log_value", -1))
            ab_min = st.get("min_v", 0)
            ab_max = st.get("max_v", 0)
            # LOST if no POST seen in the last 3 s (issue thresholds:
            # LIVE < 1 s, STALE 1-3 s, LOST > 3 s). The dashboard's
            # `_remoteDashColor` consumes hardStale to render the grey
            # LOST chip, mirroring the gyro card vocabulary.
            hard_stale = last_age > 3.0
            # #879 — disambiguate the local-audio producer from Android
            # push remotes. Both flow through `_brightness_obs`; the
            # `remote_ip` key is the differentiator.
            is_local_audio = (remote_ip == _LOCAL_AUDIO_BRI_SOURCE)
            display_name = ("Local Audio Brightness" if is_local_audio
                            else f"Android Auto Brightness ({remote_ip})")
            snap.append({
                "id": -1000 - hash(remote_ip) % 10000,
                "kind": "auto-brightness",
                "name": display_name,
                "deviceId": remote_ip,
                "pos": [0, 0, 0],
                "rot": [0, 0, 0],
                "calibrated": True,
                "calibratedAt": None,
                "calibratedAgainst": None,
                "staleReason": "connection-lost" if hard_stale else None,
                "softStale": False,
                "hardStale": hard_stale,
                "aim": None,
                "up": None,
                "connectionState": "streaming",
                "lastDataAge": last_age if last_age < 1e6 else None,
                "orientConvention": "n/a",
                "autoBrightness": {
                    "currentValue": cur_value if cur_value >= 0 else None,
                    "min": ab_min, "max": ab_max,
                    "globalBrightness": _settings.get("globalBrightness", 255),
                },
            })
    return jsonify(remotes=snap)


@app.post("/api/remotes/disconnect")
def api_remotes_disconnect():
    """#754 BUG-C — explicit "device-going-offline" signal.

    Body: {"deviceId": "<guid>"}. Phones (auto-registered, ephemeral) are
    *removed* from the registry so a subsequent claim re-registers cleanly
    without the latched `stale_reason="session-ended"` causing
    `mover_control._tick` to immediately auto-release the new claim. Gyro
    gyros (persistent hardware in the operator's setup) just get
    `end_session()` — same SPA "gone within 1s" effect, but without losing
    their saved position/rot/calibration record.
    """
    body = request.get_json(silent=True) or {}
    did = (body.get("deviceId") or "").strip()
    if not did:
        return jsonify(ok=False, err="deviceId required"), 400
    r = _remotes.by_device(did)
    if r is None:
        # Idempotent: nothing to disconnect — return ok so the client's
        # cleanup path doesn't have to special-case "already gone".
        return jsonify(ok=True, found=False)
    if r.kind == KIND_PHONE:
        rid = r.id
        _remotes.remove(rid)
        return jsonify(ok=True, found=True, remoteId=rid, removed=True)
    r.end_session()
    _remotes.save()
    return jsonify(ok=True, found=True, remoteId=r.id, removed=False)


@app.get("/api/remotes/<int:remote_id>/diagnostic")
def api_remote_diagnostic(remote_id):
    """Raw + transformed orientation for axis-convention verification (#477).

    Useful when the physical gyro motion doesn't match the 3D ray —
    operator / developer can see every step of the sensor → stage pipeline.
    """
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    # #757 Issue A — diagnostic reads per-remote axes, not the legacy
    # module constants, so a portrait-grip phone shows its own grip in
    # the trace.
    from remote_math import quat_rotate_vec
    q = r.last_quat_world
    # #885 — when the Remote is stale (session ended, lost, etc.) the
    # cached last_quat_world is no longer a live reading; surfacing it
    # as `rawQuat` lets the SPA aim-wizard capture the same poisoned
    # value three times and reject with `insufficient_pitch` even
    # though the operator visibly moved the device. Hide the cache
    # when stale so the SPA's existing null-check fires and the
    # operator sees "No quaternion received from the gyro yet"
    # instead. Server-side wizard guard (Option 3 below) still
    # rejects with `gyro_not_streaming` so non-SPA callers get the
    # same protection.
    is_stale = r.stale_reason is not None
    live_q = None if is_stale else q
    body_fwd_world = (list(quat_rotate_vec(live_q, r.forward_local))
                      if live_q else None)
    body_up_world  = (list(quat_rotate_vec(live_q, r.up_local))
                      if live_q else None)
    return jsonify({
        "id":                 r.id,
        "deviceId":           r.device_id,
        "kind":               r.kind,
        "rawQuat":            list(live_q) if live_q else None,
        "bodyForwardLocal":   list(r.forward_local),
        "bodyUpLocal":        list(r.up_local),
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
    if aim_stage is None:
        # #806 — see /api/mover-control/calibrate-end for rationale. We
        # never lock the remote against a silently-derived wrong vector.
        return jsonify(
            ok=False,
            err="aim_unresolvable",
            detail=("Could not determine the head's current aim direction. "
                    "Confirm Home + Secondary are saved and the fixture "
                    "has been parked or aimed at least once this session."),
        ), 400

    try:
        # #805 — prefer native quaternion when supplied (see the
        # /api/mover-control/calibrate-end handler for the full rationale).
        quat = body.get("quat")
        if isinstance(quat, list) and len(quat) == 4:
            r.calibrate(
                target_aim_stage=aim_stage,
                target_info={"objectId": mover["id"], "kind": "mover"},
                quat=quat,
            )
        else:
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


@app.post("/api/remotes/<int:remote_id>/clear-calibration")
def api_remote_clear_calibration(remote_id):
    """#872 Bug E — explicit operator gesture to clear a remote's
    persisted calibration frame. Drops `R_world_to_stage`,
    `calibrated`, and `calibrated_against` so the next orient stream
    is treated as uncalibrated. Used when an operator suspects the
    calibrate-from-here transform is wrong (e.g. #869 calibrate-frame
    drift) and wants to restart with a fresh capture without rotating
    against another mover or restarting the orchestrator."""
    r = _remotes.get(remote_id)
    if r is None:
        return jsonify(ok=False, err="not found"), 404
    r.R_world_to_stage = None
    r.calibrated = False
    r.calibrated_at = 0.0
    r.calibrated_against = None
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
            # #689 — pan / tilt (and their fine pair channels) are written
            # at fixture-native resolution by set_fixture_pan_tilt below,
            # not as 8-bit defaults here. Skipping prevents the legacy
            # `off + 1` LSB assumption from corrupting non-contiguous
            # OFL pan-fine / tilt-fine offsets.
            if ch.get("type") in ("pan", "pan-fine", "tilt", "tilt-fine"):
                continue
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
        # Seed pan/tilt to the fixture's saved Home anchor when present
        # (#784 PR-7 — was previously a parametric/affine inverse against
        # `rotation` aim target; legacy IK is gone). Falls back to
        # mount-local forward (0.5, 0.5) for fixtures without Home.
        home_pan = f.get("homePanDmx16")
        home_tilt = f.get("homeTiltDmx16")
        if home_pan is not None and home_tilt is not None:
            pan_seed = max(0.0, min(1.0, float(home_pan) / 65535.0))
            tilt_seed = max(0.0, min(1.0, float(home_tilt) / 65535.0))
        else:
            pan_seed, tilt_seed = 0.5, 0.5
        uni_buf.set_fixture_pan_tilt(addr, pan_seed, tilt_seed, profile)

@app.post("/api/dmx/start")
def api_dmx_start():
    body = request.get_json(silent=True) or {}
    protocol = body.get("protocol", "artnet")
    if protocol == "artnet":
        _artnet.start()
        _apply_profile_defaults(_artnet)
        engine = _artnet
    elif protocol == "sacn":
        _sacn.start()
        _apply_profile_defaults(_sacn)
        engine = _sacn
    else:
        return jsonify(err=f"Unknown protocol: {protocol}"), 400
    # #687 — drive movers to their saved Home anchor before any blink so
    # the operator sees the boot animation on-axis. Skipped on engines
    # that didn't actually come up. Done in a thread so the request
    # returns promptly; the settle delay can run in the background.
    if engine.running:
        def _home_then_blink():
            try:
                _drive_movers_to_home(engine)
            except Exception:
                log.exception("drive-to-home crashed")
            if (_dmx_settings.get("bootBlinkFixtures", True)
                    and not _boot_blink_done):
                _run_boot_blink(engine)
        import threading as _thr
        _thr.Thread(target=_home_then_blink, daemon=True).start()
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
    """Lamps off across every fixture.

    #781 / #782 operator-decision rewrite (2026-05-03): blackout means
    "no lights." It must NOT zero pan/tilt and MUST NOT zero non-
    intensity channels (a wheel sitting at default=128 must not be
    clobbered to 0 by blackout). Implementation: iterate every DMX
    fixture, call `lamp_off(profile, dmx, addr, color=None)` to drive
    intensity-class channels (dimmer, intensity, strobe-Open) to off,
    leave everything else alone. Heads stay where they are; the lamps
    go off.

    A future "park heads home AND lights off" command builds from
    `aim.park.go_home(fid)` + `lamp_off()` together — separate helper.
    """
    from dmx_profiles import lamp_off
    engines_to_use = []
    if _artnet.running:
        engines_to_use.append(_artnet)
    if _sacn.running:
        engines_to_use.append(_sacn)
    flushed = False
    # If nothing is running, briefly spin up Art-Net so the lamp-off
    # frames reach the wire — same flush-on-stop pattern (#601).
    if not engines_to_use:
        try:
            _apply_dmx_settings()
            _artnet._bind_ip = "0.0.0.0"  # stale saved IP can block bind (#345)
            _artnet.start()
            if _artnet.running:
                for route in _dmx_settings.get("universeRoutes", []) or []:
                    u = int(route.get("universe") or 1)
                    _artnet.get_universe(u)
                for f in _fixtures:
                    if f.get("fixtureType") == "dmx":
                        _artnet.get_universe(int(f.get("dmxUniverse", 1)))
                engines_to_use.append(_artnet)
                flushed = True
        except Exception:
            pass

    # Apply lamp_off per fixture on every running engine. The 40 Hz
    # loop picks up the dirty buffers and transmits the next frame.
    for engine in engines_to_use:
        for f in _fixtures:
            if f.get("fixtureType") != "dmx":
                continue
            try:
                uni = int(f.get("dmxUniverse", 1) or 1)
                addr = int(f.get("dmxStartAddr", 1) or 1)
                pid = f.get("dmxProfileId")
                info = _profile_lib.channel_info(pid) if pid else None
                if not info:
                    continue
                profile = {"channel_map": info.get("channel_map", {}),
                            "channels":    info.get("channels", [])}
                buf = engine.get_universe(uni)
                # Stage the current intensity-channel values into a
                # scratch bytearray, run lamp_off, then push the
                # changed bytes back through set_channel. set_channel
                # is the supported per-byte write that flags the buffer
                # dirty for the engine's transmit loop.
                tmp = bytearray(512)
                for ch in (profile.get("channels") or []):
                    off = ch.get("offset", 0)
                    if 0 <= addr - 1 + off < 512:
                        try:
                            tmp[addr - 1 + off] = int(buf.get_channel(addr + off))
                        except Exception:
                            tmp[addr - 1 + off] = 0
                lamp_off(profile, tmp, addr, color=None)
                for ch in (profile.get("channels") or []):
                    if ch.get("type") in ("dimmer", "intensity", "strobe"):
                        off = ch.get("offset", 0)
                        if 0 <= addr - 1 + off < 512:
                            buf.set_channel(addr + off, int(tmp[addr - 1 + off]))
            except Exception as e:
                log.warning("blackout: lamp_off skipped for fid %s: %s",
                            f.get("id"), e)

    # If we spun up an engine just for the flush, stop it now — stop()
    # sends 3 forced frames (#601) and tears down. Heads' pan/tilt
    # bytes stay at whatever was last written; lamps are off.
    if flushed:
        try:
            _artnet.stop()
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
    # #687 — re-seed Home pose before the manual blink so the rainbow is
    # visibly on-axis (matches auto-start behaviour).
    def _home_then_blink():
        try:
            _drive_movers_to_home(engine)
        except Exception:
            log.exception("drive-to-home crashed")
        _run_boot_blink(engine, True)
    import threading as _thr_blink
    _thr_blink.Thread(target=_home_then_blink, daemon=True).start()
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
    """Return all 512 channel values for a universe as a flat array.

    #853 — applies the master grand-master scaling so the monitor
    reflects what ArtNet would actually send. Pre-fix the monitor
    showed the raw buffer (writers' values) which diverged from the
    wire whenever globalBrightness < 255 — the operator couldn't
    sanity-check 'is the master actually scaling?' from the SPA.
    Now the monitor reads the same scaled view the send loop uses.
    """
    for engine in (_artnet, _sacn):
        if engine.running and uni in engine._universes:
            g_bri = _settings.get("globalBrightness", 255)
            if g_bri < 255:
                data = engine._universes[uni].get_data_scaled(
                    master_brightness=int(g_bri),
                    intensity_offsets=_get_intensity_offsets(uni),
                    gamma_lut=_GAMMA_LUT,
                )
            else:
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

def _drive_movers_to_home(engine, settle_ms=400):
    """#687 follow-up — at engine start, send every DMX mover that has a
    saved Home anchor to its home pan/tilt before the boot blink runs.

    The operator picked these (pan, tilt) DMX values during Set Home as
    the orientation that aims along the fixture's saved rotation vector.
    Driving there before the rainbow blink means the boot animation is
    visibly on-axis instead of wherever the fixture last sat — and any
    show that starts immediately after has a known initial pose.

    Fixtures without homePanDmx16 / homeTiltDmx16 set (cal-not-yet path)
    are left alone so a fresh rig doesn't get random pan/tilt writes.

    settle_ms: pause after the writes so DMX bridges actually transmit
    the frame to the fixtures before the blink starts overwriting other
    channels — avoids a race where the blink's strobe/dimmer writes
    arrive with stale pan/tilt and the fixture lurches mid-blink.
    """
    moved = 0
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pan16 = f.get("homePanDmx16")
        tilt16 = f.get("homeTiltDmx16")
        if pan16 is None or tilt16 is None:
            continue
        pid = f.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        if not info:
            continue
        try:
            uni = f.get("dmxUniverse", 1)
            addr = f.get("dmxStartAddr", 1)
            profile = {"channel_map": info.get("channel_map", {}),
                        "channels": info.get("channels", [])}
            engine.get_universe(uni).set_fixture_pan_tilt(
                addr, float(pan16) / 65535.0, float(tilt16) / 65535.0,
                profile)
            moved += 1
        except Exception as e:
            log.warning("drive-to-home: fixture %s failed (%s)",
                         f.get("id"), e)
    if moved > 0:
        log.info("drive-to-home: sent %d mover(s) to saved Home anchors", moved)
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)


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
            # #687 — send every mover with a saved Home anchor to its
            # home pan/tilt BEFORE the boot blink runs, so the rainbow
            # animation is visibly on-axis. Movers without Home are
            # left untouched. Done in a background thread so a slow
            # bridge doesn't delay engine start logging.
            def _home_then_blink():
                try:
                    _drive_movers_to_home(_engine)
                except Exception:
                    log.exception("drive-to-home crashed")
                if (_dmx_settings.get("bootBlinkFixtures", True)
                        and not _boot_blink_done):
                    _run_boot_blink(_engine)
            import threading as _thr
            _thr.Thread(target=_home_then_blink, daemon=True).start()

# ── Auto-start show on boot (#390) ────────────────────────────────────────
def _auto_start_show():
    """Resume the last active timeline if autoStartShow is enabled.

    `_bake_result` is an in-memory runtime dict — it does NOT survive an
    orchestrator restart. Pre-fix this function bailed with "not baked —
    staying idle" on every restart, so the auto-show never actually
    resumed: LED children kept looping autonomously from their own
    preloaded steps (appearing to work), but the DMX playback loop had
    no bake to stream and the moving heads stayed dark. So on resume we
    re-bake + re-sync before starting, and bring the DMX engine up so the
    movers actually receive Art-Net/sACN output."""
    time.sleep(5)  # wait for children to reconnect
    # Resume the persisted auto-show pointer. `activeTimeline` is
    # transient (cleared to -1 by sync/stop and by the boot reset), so
    # it cannot be relied on across a restart — `autoShowTimelineId` is
    # the durable pointer. Fall back to `activeTimeline` for configs
    # saved before that field existed.
    tid = _settings.get("autoShowTimelineId")
    if tid is None or tid < 0:
        tid = _settings.get("activeTimeline", -1)
    if tid < 0:
        log.info("Auto-start show: no auto-show timeline saved — staying idle")
        return
    tl = next((t for t in _timelines if t["id"] == tid), None)
    if not tl:
        log.warning("Auto-start show: timeline %d not found — staying idle", tid)
        return
    has_track = any(a.get("type") == 18 for a in _actions)

    # Bring the DMX engine up — moving heads receive no output without it.
    try:
        proto = _dmx_settings.get("protocol", "artnet")
        engine = _artnet if proto == "artnet" else _sacn
        if not engine.running:
            engine.start()
            _apply_profile_defaults(engine)
            log.info("Auto-start show: started %s DMX engine", proto)
    except Exception as e:
        log.warning("Auto-start show: could not start DMX engine: %s", e)

    # Re-bake — `_bake_result` is empty after a restart, and the DMX
    # playback loop streams nothing without it.
    if tid not in _bake_result:
        log.info("Auto-start show: baking timeline %d before resume", tid)
        with app.test_request_context():
            api_timeline_bake(tid)
        for _ in range(240):
            if _bake_progress and _bake_progress.done:
                break
            time.sleep(1)
        if tid not in _bake_result and not has_track:
            log.warning("Auto-start show: bake did not complete — staying idle")
            return

    # Re-sync baked steps to LED children (best-effort — a sync failure
    # must not block the DMX side from starting).
    with app.test_request_context():
        try:
            api_bake_sync(tid)
        except Exception as e:
            log.warning("Auto-start show: sync call failed (%s) — continuing", e)
    time.sleep(2)
    for _ in range(120):
        if not _sync_progress or _sync_progress.get("done"):
            break
        time.sleep(1)

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
    # ONLY when no engine is driving the universe.
    #
    # #863 — pre-fix this substituted the profile default for any channel
    # whose buffer value was 0, even with the engine running. That made
    # every legitimate "wire is currently 0" report a lie (e.g. dimmer-out
    # claim ticks reading back as the profile's default 128). QA pollers
    # built on this endpoint silently masked freezes / wire-stuck-at-0
    # bugs (this is the read-side artifact behind one of the symptoms in
    # #862). Now: real engine, real value — including 0.
    for ch in channels:
        dmx_addr = addr + ch["offset"]
        val = 0
        engine_running = False
        if _artnet.running:
            val = _artnet.get_universe(uni).get_channel(dmx_addr)
            engine_running = True
        elif _sacn.running:
            val = _sacn.get_universe(uni).get_channel(dmx_addr)
            engine_running = True
        if not engine_running and ch.get("default", 0) > 0:
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
    # #763 — snapshot claim state once per request so every entry sees a
    # consistent view, and so we can populate `claimedFixtures` in the
    # response without re-reading the engine.
    claim_snap = _claim_arbiter.snapshot()
    result = []
    for f in _fixtures:
        fid = f["id"]
        ft = f.get("fixtureType", "led")
        # #834 — Fixture Monitor renders light-producing fixtures only.
        # Gyro and camera fixtures are controllers / sensors with their
        # own dashboard cards (Remote Controllers, Cameras); they have
        # no DMX/LED output to surface here. Pre-fix gyros fell through
        # to the LED branch and emitted always-Idle tiles with no useful
        # info.
        if ft in ("gyro", "camera"):
            continue
        entry = {
            "id": fid,
            "name": f.get("name") or f"Fixture {fid}",
            "fixtureType": ft,
            "r": 0, "g": 0, "b": 0,
            "dimmer": 0,
            "active": False,
            "effect": None,
            # #763 — output origin: "claim" while held by mover-control,
            # "show" while the timeline is running, "idle" otherwise.
            "source": "idle",
            "claimedBy": None,
        }
        if _claim_arbiter.is_muted(fid, claim_snap):
            entry["source"] = "claim"
            entry["claimedBy"] = _claim_arbiter.claim_info(fid, claim_snap)
        elif running:
            entry["source"] = "show"
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
            # #622 — peek_universe avoids conjuring a keep-alive-active
            # universe buffer just because the dashboard polls this
            # endpoint. If the engine hasn't been asked to write to this
            # universe yet, uni is None and the live values stay at zero.
            uni = engine.peek_universe(uni_num) if engine else None
            if engine and uni is not None:
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
            if ch_map and "color-wheel" in ch_map and engine and uni is not None:
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
            # #806 — prefer the canonical aim_stage store so the viz
            # cone matches whatever the engine pump / aim API / park
            # last committed (and therefore matches calibrate-end's
            # captured anchor — acceptance #3 of #806). Fall back to
            # legacy mount-relative math from the live DMX buffer for
            # fixtures that don't yet have a canonical entry, so the
            # viz never goes blank on a fresh fixture.
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
                    canonical = _get_canonical_aim_stage(f.get("id"))
                    if canonical is not None:
                        aim = canonical
                    else:
                        # Best-guess for un-canonicalised fixtures (no
                        # park / aim / claim has fired since startup).
                        # Mount-relative math from raw DMX — same as the
                        # pre-#806 default.
                        _pr_deg = (pan_norm - 0.5) * (f.get("panRange") or 540)
                        _tr_deg = (tilt_norm - 0.5) * (f.get("tiltRange") or 270)
                        _pr = math.radians(_pr_deg)
                        _tr = math.radians(_tr_deg)
                        _cos_t = math.cos(_tr)
                        _dx = math.sin(_pr) * _cos_t
                        _dy = math.cos(_pr) * _cos_t
                        _dz = -math.sin(_tr)
                        _rot = f.get("rotation") or [0, 0, 0]
                        if _rot[0] == 0 and _rot[1] == 0 and _rot[2] == 0:
                            aim = (_dx, _dy, _dz)
                        else:
                            from remote_math import (
                                euler_xyz_deg_to_matrix, matrix_vec_mul,
                            )
                            _R = euler_xyz_deg_to_matrix(_rot)
                            aim = matrix_vec_mul(_R, (_dx, _dy, _dz))
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
    return jsonify({"running": running, "fixtures": result,
                    "claimedFixtures": _claim_arbiter.claimed_fids(claim_snap)})


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

        # #843 — packet construction extracted to _brightness_packet().
        bri = _settings.get("globalBrightness", 255)
        bri_pkt = _brightness_packet(bri)

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

        elif pattern == "ribbon":
            # #839 — ribbon: travelling stage-coord anchor along a named
            # axis. Movers + spatial sweeps both lock onto this point so
            # the whole rig moves as one unit. The two anchor points are
            # derived from `axis` + stage bounds; `elevation` (0..1)
            # picks the Z slice; `loopMode` controls wrap behaviour.
            axis_name = pat.get("ribbonAxis") or pat.get("axis", "left-right")
            elevation = float(pat.get("elevation", 0.5))
            loop_mode = pat.get("loopMode", "ping-pong")
            z_anchor = sh * max(0.0, min(1.0, elevation))
            mid_x = sw / 2.0
            mid_y = sd / 2.0
            if axis_name in ("left-right", "right-left"):
                p_a = [0, mid_y, z_anchor]
                p_b = [sw, mid_y, z_anchor]
                if axis_name == "right-left":
                    p_a, p_b = p_b, p_a
            elif axis_name in ("front-back", "back-front"):
                p_a = [mid_x, 0, z_anchor]
                p_b = [mid_x, sd, z_anchor]
                if axis_name == "back-front":
                    p_a, p_b = p_b, p_a
            elif axis_name in ("up-down", "down-up"):
                # Vertical sweep — Z is the up axis (#837). Pin X,Y at
                # stage centre.
                p_a = [mid_x, mid_y, 0]
                p_b = [mid_x, mid_y, sh]
                if axis_name == "down-up":
                    p_a, p_b = p_b, p_a
            elif axis_name == "cross":
                # Diagonal — left-front low to right-back high.
                p_a = [0, 0, sh * 0.2]
                p_b = [sw, sd, sh * 0.8]
            elif axis_name == "figure8":
                # Use the existing figure-8 pattern but at the elevation.
                r = min(sw, sd) * 0.35
                angle = phase * 2.0 * math.pi
                new_pos[0] = mid_x + r * math.sin(angle)
                new_pos[1] = mid_y + r * math.sin(2.0 * angle)
                new_pos[2] = z_anchor
                obj.setdefault("transform", {})["pos"] = new_pos
                continue
            else:
                p_a = [0, mid_y, z_anchor]
                p_b = [sw, mid_y, z_anchor]
            # Compute the parametric `t` along (p_a → p_b) per loop mode.
            if loop_mode == "ping-pong":
                t = 1.0 - abs(2.0 * phase - 1.0)
            elif loop_mode == "once":
                t = min(1.0, phase)  # park at p_b after one cycle
            else:  # "wrap"
                t = phase
            # `figure8` loop mode would ignore `t` — handled above with
            # an explicit `continue`. Easing applies to ping-pong / wrap.
            if easing == "sine":
                t = 0.5 - 0.5 * math.cos(t * math.pi)
            for i in range(3):
                new_pos[i] = p_a[i] + t * (p_b[i] - p_a[i])

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

def _apply_handover_slew(fid, uni, addr, ch_map, engine):
    """#877 — no-op stub. The pre-fix body capped pan-tilt-speed during
    a post-release slew window (#763) so the fixture eased between the
    operator's last pose and the show's commanded pose. Since #877 the
    orchestrator never writes the pan-tilt-speed channel; the fixture
    handles its own motor speed. Drain the just-ended flag so it doesn't
    leak between releases (cheap, idempotent) and return.
    """
    _claim_arbiter.pop_handover_just_ended(fid)


def _evaluate_track_actions(elapsed, engine, dmx_fixtures,
                              timeline_track_fids=None,
                              tl_action_ids=None):
    """Evaluate active Track actions -- compute real-time pan/tilt for moving heads.

    `timeline_track_fids` (#829): when provided, an iterable of fixture
    ids assigned to the running timeline's tracks. Track actions whose
    own `trackFixtureIds` is empty fall back to this scope rather than
    "every mover on the rig". `None` preserves the legacy "all movers"
    behaviour for callers that don't have a timeline scope to pass.

    `tl_action_ids` (#835): when provided, an iterable of action ids
    referenced by clips in the running timeline's tracks. Only Track
    actions whose `id` is in this set are evaluated — orphan Track
    actions in the global library no longer drive the unrelated-
    timeline blackout sweep that zeroes every mover's dimmer. Pre-fix
    a generative show with no Track-action clips would still see every
    type-18 entry in `_actions` evaluate every frame, blackout-stomp
    its bake's dimmer writes via the unassigned-heads sweep, and leave
    the rig dark while pan/tilt animated. `None` preserves the legacy
    "evaluate all type-18 actions" behaviour for non-playback callers.
    """
    track_actions = [a for a in _actions if a.get("type") == 18]
    if tl_action_ids is not None:
        active_ids = set(int(x) for x in tl_action_ids)
        track_actions = [a for a in track_actions
                          if int(a.get("id", -1)) in active_ids]
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
    # #843 — snapshot global brightness once per frame so all writes in
    # this Track-action pass see the same value, immune to a /api/brightness
    # write landing mid-iteration.
    with _lock:
        g_bri = _settings.get("globalBrightness", 255)
    # #829 — timeline-track-scoped default. When the running timeline
    # only assigned the Track action to a subset of fixtures, that
    # subset is the default scope; the action's own `trackFixtureIds`
    # narrows further when set.
    timeline_scope = (set(int(x) for x in timeline_track_fids)
                      if timeline_track_fids is not None else None)
    for ta in track_actions:
        # #827 / #828 — Resolve target objects:
        #   trackObjectType set → filter moving objects by that type
        #   trackObjectIds set → search ALL moving objects (#828: was
        #       silently filtering to _temporal which excluded patrol
        #       props, contradicting the inline doc-comment)
        #   trackMode == "all-moving" → patrol props + camera detections
        #   default (or trackMode == "camera-moving") → camera detections only
        obj_type = ta.get("trackObjectType")
        target_ids = ta.get("trackObjectIds", [])
        track_mode = ta.get("trackMode")
        if obj_type:
            candidates = [o for o in moving_objects if o.get("objectType") == obj_type]
        elif target_ids:
            # #828 — explicit IDs: search the whole moving-object set so
            # patrol-prop ids resolve. Pre-fix the _temporal pre-filter
            # made `targets` empty for any patrol-only id list and the
            # action silently skipped.
            candidates = list(moving_objects)
        elif track_mode == "all-moving":
            # #827 — operator opted in to patrol props + camera dets.
            candidates = list(moving_objects)
        else:
            # Default ("camera-moving" or unset): camera detections only.
            candidates = [o for o in moving_objects if o.get("_temporal")]
        targets = [o for o in candidates if o["id"] in target_ids] if target_ids else candidates
        # If this action has explicit trackObjectIds but none exist, skip entirely —
        # don't blackout heads just because a deleted patrol object is missing.
        # Only auto-discover actions (no trackObjectIds) blackout when no targets found.
        if target_ids and not targets:
            continue
        # #829 — Resolve fixtures with timeline-track scoping:
        #   action.trackFixtureIds set     → those specific fixtures (most explicit)
        #   timeline_track_fids supplied   → fall back to the running
        #                                    timeline's track assignments
        #                                    (matches every other action type's behaviour)
        #   neither                        → every eligible mover (legacy)
        fix_ids = ta.get("trackFixtureIds", [])
        if fix_ids:
            scope = [int(x) for x in fix_ids]
        elif timeline_scope is not None:
            scope = [fid for fid in timeline_scope if fid in fx_lookup]
        else:
            scope = list(fx_lookup.keys())
        heads = [fx_lookup[fid] for fid in scope if fid in fx_lookup]
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
        # #763 — claim-arbiter snapshot for this evaluation pass. Tracker
        # action keeps computing (Q2-(a)) but skips the DMX write for
        # claimed fixtures so re-acquisition is instant on release.
        track_claim_snap = _claim_arbiter.snapshot()
        for hi, head_info in enumerate(heads):
            if not targets:
                break  # skip aim loop, go to blackout
            f = head_info["fixture"]
            fid = f["id"]
            # #511 — skip show output for fixtures mid-calibration.
            if f.get("isCalibrating"):
                continue
            # #763 — keep computing target pose (so re-acquisition is
            # instant on release) but skip the universe write while a
            # mover-control claim holds the fixture.
            if _claim_arbiter.is_muted(fid, track_claim_snap):
                assigned_heads.add(hi)  # don't blackout below — claim owns it
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
            # Compute pan/tilt. #809 fix — for fixtures with the full
            # canonical data (Home + Secondary + sized profile) we now
            # route through `AimSphere.aim_xyz`, the same IK used by
            # the claim writer (`mover_control._aim_to_pan_tilt`) and
            # `/api/mover/<fid>/aim`. Pre-#809 Track action used legacy
            # geometric IK only, which produced DMX that disagreed with
            # the rest of the system for a configured mover — physical
            # head aimed off-target while 3D viz showed correct aim.
            #
            # Order: AimSphere (canonical) → range-calibration override
            # → geometric fallback. Geometric stays for movers without
            # Home + Secondary; deleting it would break unconfigured
            # fixtures that worked before.
            pan = tilt = None
            inverted = head_info.get("mounted_inverted", False)
            # 1. AimSphere — the same IK every other writer uses.
            if (f.get("homePanDmx16") is not None
                    and f.get("homeTiltDmx16") is not None
                    and f.get("homeSecondary")
                    and (head_info.get("pan_range") or 0) > 0
                    and (head_info.get("tilt_range") or 0) > 0
                    and head_info.get("prof_info")):
                try:
                    from aim.routes import _get_or_build_sphere
                    # Patch xyz from layout (same pattern as
                    # `_resolve_sphere` in aim/routes.py).
                    _sf = dict(f)
                    _sf["x"] = fx_pos[0]
                    _sf["y"] = fx_pos[1]
                    _sf["z"] = fx_pos[2]
                    _sphere = _get_or_build_sphere(
                        _sf, head_info["prof_info"])
                    # current_pose for branch picking — read live DMX
                    # from the engine buffer (matches the claim writer's
                    # use of claim.pan_smooth/tilt_smooth as anchor).
                    _cur = None
                    try:
                        _cm = head_info["prof_info"].get("channel_map", {})
                        _channels = head_info["prof_info"].get("channels", [])
                        _addr = f.get("dmxStartAddr", 1)
                        _uni_buf = engine.get_universe(
                            f.get("dmxUniverse", 1))

                        def _read16(axis):
                            offset = _cm.get(axis)
                            if offset is None:
                                return None
                            ch_def = next(
                                (c for c in _channels
                                 if c.get("type") == axis), None)
                            bits = (ch_def or {}).get("bits", 8)
                            if bits >= 16:
                                hi = _uni_buf.get_channel(_addr + offset)
                                fine_off = _cm.get(f"{axis}-fine",
                                                   offset + 1)
                                lo = _uni_buf.get_channel(_addr + fine_off)
                                return ((hi << 8) | (lo & 0xFF))
                            return _uni_buf.get_channel(_addr + offset) << 8
                        _p16 = _read16("pan")
                        _t16 = _read16("tilt")
                        if _p16 is not None and _t16 is not None:
                            _cur = (_p16, _t16)
                    except Exception:
                        _cur = None
                    _pose = _sphere.aim_xyz(
                        tuple(aim), current_pose=_cur, prefer="closest")
                    if _pose is not None:
                        pan = _pose[0] / 65535.0
                        tilt = _pose[1] / 65535.0
                except Exception as e:
                    log.debug("Track AimSphere failed for fid %s: %s — "
                               "falling back to range-cal / geometric",
                               fid, e)
            # 2. Range calibration (automated axis mapping)
            if pan is None:
                pt_cal = compute_pan_tilt_calibrated(fid, aim)
                if pt_cal:
                    pan, tilt = pt_cal
            # 3. Geometric fallback (no calibration data at all)
            if pan is None:
                pt = compute_pan_tilt(fx_pos, aim, head_info["pan_range"],
                                       head_info["tilt_range"],
                                       mounted_inverted=inverted)
                if pt is None:
                    continue
                pan, tilt = pt
            # Write to DMX universe
            prof_info = head_info["prof_info"]
            if prof_info:
                profile = {"channel_map": prof_info.get("channel_map"), "channels": prof_info.get("channels", [])}
                uni = f.get("dmxUniverse", 1)
                uni_buf = engine.get_universe(uni)
                addr = f.get("dmxStartAddr", 1)
                # #763 — apply post-release slew cap before show writes
                # so a tracker-driven head also eases back smoothly.
                _apply_handover_slew(fid, uni, addr,
                                     prof_info.get("channel_map"), engine)
                uni_buf.set_fixture_pan_tilt(addr, pan, tilt, profile)
                # #806 phase 2 — store the canonical aim direction this
                # Track-action commit is driving toward. Source of truth
                # is the operator's `aim` (target stage-mm), not the
                # post-IK pan/tilt — no inverse-IK round-trip risk.
                try:
                    fp = head_info["pos"]
                    _dx = aim[0] - fp[0]
                    _dy = aim[1] - fp[1]
                    _dz = aim[2] - fp[2]
                    _n = math.sqrt(_dx * _dx + _dy * _dy + _dz * _dz)
                    if _n > 1e-6:
                        _set_canonical_aim_stage(
                            fid, (_dx / _n, _dy / _n, _dz / _n))
                except Exception:
                    pass
                # Track action also sets dimmer + color so the beam is visible
                tr = ta.get("trackDimmer", 255)
                cm = prof_info.get("channel_map", {})
                # #842 — set_fixture_rgb dispatches RGB / hybrid / wheel-only.
                # The action body's manual `colorWheel` override (only ever
                # written by type-17 Colour Wheel actions, #841) still wins
                # for wheel-only profiles where the operator explicitly
                # named a slot.
                ta_r = ta.get("r", 255)
                ta_g = ta.get("g", 255)
                ta_b = ta.get("b", 255)
                # #853 — write Track action's raw values; the master
                # grand-master is applied at universe-buffer-send time
                # by the ArtNet engine's `get_data_scaled` so this
                # path automatically respects the operator's brightness
                # without per-writer boilerplate.
                uni_buf.set_fixture_dimmer(addr, tr, profile)
                cw_override = ta.get("colorWheel")
                if (cw_override is not None and "color-wheel" in cm
                        and not any(c in cm for c in ("red", "green", "blue"))):
                    uni_buf.set_channel(addr + cm["color-wheel"], cw_override)
                else:
                    uni_buf.set_fixture_rgb(addr, ta_r, ta_g, ta_b, profile)
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
                # #763 — never blackout a fixture held by mover-control;
                # the operator is driving its dimmer.
                if _claim_arbiter.is_muted(f["id"], track_claim_snap):
                    continue
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
    # #829 — collect the set of fixtures this timeline's tracks are
    # assigned to. Track actions with no explicit `trackFixtureIds`
    # default to this scope rather than "every mover on the rig".
    # #835 — also collect the action ids referenced by clips in this
    # timeline. Track actions NOT in this set are orphans (live in
    # `_actions` but no clip plays them) and must not evaluate — pre-
    # fix the unassigned-heads-blackout swept the timeline's full
    # mover scope every frame, zeroing the bake's dimmer writes.
    _tl_obj = next((t for t in _timelines if t.get("id") == tid), None)
    timeline_track_fids = set()
    tl_action_ids = set()
    for _tr in (_tl_obj.get("tracks", []) if _tl_obj else []):
        _fid = _tr.get("fixtureId")
        if _fid is not None:
            try: timeline_track_fids.add(int(_fid))
            except (TypeError, ValueError): pass
        for _cl in _tr.get("clips", []):
            _aid = _cl.get("actionId")
            if _aid is not None:
                try: tl_action_ids.add(int(_aid))
                except (TypeError, ValueError): pass
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
        # LED-only show: the LED children run autonomously from
        # preloaded LOAD_STEPs, so the 40Hz playback loop has no DMX
        # work to do. But the loop is ALSO the timekeeper for
        # `runnerRunning` — exit here and `_show_playback_loop` clears
        # it back to False within milliseconds of Start, making the SPA
        # report "no show running" even while the bars are actually
        # playing. Idle for the timeline's duration (or until stop) so
        # the runner-state stays True for the show's nominal length.
        log.info("DMX playback: LED-only show, idling for duration=%ss loop=%s",
                 duration, loop)
        if loop:
            _dmx_playback_stop.wait()    # wait until Stop / generation change
        elif duration > 0:
            _dmx_playback_stop.wait(timeout=duration)
        return
    if not dmx_fixtures:
        log.info("DMX playback: no baked segments but Track actions present — loop will run for tracking")

    # #807 — track-driven movers don't contribute baked segments (they're
    # computed live by `_evaluate_track_actions`), so they were absent
    # from `dmx_fixtures` and the post-loop park step at natural-end
    # never reached them. Build the union of every mover this timeline
    # could drive — baked-segment fixtures + every fixture targeted by
    # an active Track action — so the natural-end park snaps every
    # involved head to home.
    # #835 — only consider Track actions that this timeline's clips
    # reference. Pre-fix every type-18 entry in the global action
    # library expanded the natural-end park scope, even when the
    # running timeline didn't use any of them.
    track_driven_fids = set()
    for ta in (a for a in _actions
                if a.get("type") == 18 and int(a.get("id", -1)) in tl_action_ids):
        listed = ta.get("trackFixtureIds") or []
        if listed:
            for tfid in listed:
                track_driven_fids.add(int(tfid))
        else:
            # Auto-discover Track action — fall back to the timeline's
            # assigned mover set (matches `_evaluate_track_actions`'s
            # candidate scope when `trackFixtureIds` is empty + #829
            # timeline-track-fid scoping). If the timeline has no
            # assigned movers and no explicit list, no fixtures are
            # added — the action has no scope.
            for fid in timeline_track_fids:
                f = next((x for x in _fixtures if x.get("id") == fid), None)
                if (f is not None
                        and f.get("fixtureType") == "dmx"
                        and f.get("homePanDmx16") is not None
                        and f.get("homeTiltDmx16") is not None):
                    track_driven_fids.add(int(f["id"]))

    log.info("DMX playback: %d fixture(s), duration=%ds, loop=%s", len(dmx_fixtures), duration, loop)
    # #622 — do NOT auto-start the DMX engine. Previously a timeline
    # targeting only LED children would still bring Art-Net up; now we
    # run the playback loop regardless (LED output is unaffected) and
    # only engage DMX writes if the operator already started the engine.
    proto = _dmx_settings.get("protocol", "artnet")
    engine = _artnet if proto == "artnet" else _sacn
    if not engine.running:
        log.info("DMX playback: engine is stopped — LED children will play, "
                 "DMX fixtures will not receive output this cycle")
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
        # #845 — silent-death guard around the per-frame body. Pre-fix
        # any exception inside this block (e.g. wrong-arity call to
        # `_apply_handover_slew`, missing channel-map key, etc.) killed
        # the daemon thread before a single frame reached the universe,
        # producing the symptom the issue describes: show starts cleanly,
        # initialization is logged, but no per-frame writes occur. The
        # log call below is rate-limited so a persistent failure surfaces
        # in the log without producing 40 tracebacks per second.
        try:
            # #622 — skip DMX writes entirely when the engine is stopped.
            # Previously we still iterated and called engine.get_universe(),
            # which lazy-created keep-alive-active buffers that would emit
            # ArtDMX as soon as the engine started later.
            if not engine.running:
                frame_count += 1
                continue
            # #763 — claim-arbiter snapshot, frozen for the duration of
            # this ~25 ms frame so every per-fixture decision sees a
            # consistent set.
            claim_snap = _claim_arbiter.snapshot()
            # #843 — snapshot the master-brightness setting once per
            # frame so all per-fixture writes in this iteration see the
            # same value (immune to a /api/brightness write landing
            # mid-iteration).
            with _lock:
                g_bri = _settings.get("globalBrightness", 255)
            # Evaluate each DMX fixture — merge ALL matching segments
            # per-channel. Higher-priority segments (_pri) override lower
            # ones per-channel, allowing e.g. a PT sweep to control
            # pan/tilt while a base wash controls color independently.
            for fx in dmx_fixtures:
                # #511 — skip playback for fixtures mid-calibration.
                if _fixture_is_calibrating(fx.get("id")):
                    continue
                # #763 — skip show writes for fixtures held by mover-
                # control. Bake values are still computed (above) but
                # never reach the universe buffer. Mover-control writes
                # through unchanged.
                if _claim_arbiter.is_muted(fx["fid"], claim_snap):
                    continue
                # #763 — smooth-handover slew window: cap pan-tilt-speed
                # for ~750 ms after release so motors ease toward the
                # show's pose instead of snapping. Released once when
                # the window expires.
                # #845 — must use the same 5-arg signature as the call
                # at `_show_playback_loop` (and the function def). The
                # 2-arg form raised TypeError on every first-fixture
                # iteration, killing the daemon thread before any DMX
                # write.
                _apply_handover_slew(fx["fid"], fx["uni"], fx["addr"],
                                     fx["ch_map"], engine)
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
                # #842 — set_fixture_rgb handles RGB, hybrid, and
                # wheel-only. The baked frame's explicit `colorWheel`
                # override (only set by type-17 Colour Wheel actions,
                # #841) still wins for wheel-only profiles where the
                # operator picked a slot.
                cm = fx["ch_map"] or {}
                # #853 — write the bake's raw values; the master grand-
                # master is applied at universe-buffer-send time by the
                # ArtNet engine's `get_data_scaled` (gamma-corrected,
                # intensity-channels only). Pre-fix this path scaled
                # at render time per #843, but the resulting per-
                # writer-must-remember pattern broke for Track action
                # / claim writer / dmx-test / no-show paths. Send-time
                # scaling is uniform across every writer.
                if (color_wheel is not None and "color-wheel" in cm
                        and not any(c in cm for c in ("red", "green", "blue"))):
                    uni_buf.set_channel(fx["addr"] + cm["color-wheel"], color_wheel)
                elif cm and (r or g or b
                             or any(c in cm for c in ("red", "green", "blue"))):
                    uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
                # Dimmer
                if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                    dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                    uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
                # Pan/Tilt
                if pan is not None and tilt is not None and profile:
                    uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
                    # #806 phase 2 — record canonical aim_stage for this
                    # baked-playback write so calibrate-end during a
                    # running show observes the head's true direction
                    # without an inverse-IK round-trip on the read path.
                    try:
                        _f_full = next((_x for _x in _fixtures
                                        if _x.get("id") == fx["fid"]), None)
                        _prof_full = (_profile_lib.channel_info(_f_full.get("dmxProfileId"))
                                      if _f_full and _f_full.get("dmxProfileId") else None)
                        if _f_full is not None:
                            _aim_v = _canonical_aim_from_pan_tilt(
                                _f_full, _prof_full, pan, tilt)
                            if _aim_v is not None:
                                _set_canonical_aim_stage(fx["fid"], _aim_v)
                    except Exception:
                        pass
                # Extra DMX channels via set_fixture_channels.
                # Channel types use hyphenated names (color-wheel, gobo-rotation).
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
            # ── Track action: real-time pan/tilt for moving heads
            #    following objects ──
            if frame_count % 40 == 0:  # reap temporals every 1s
                _reap_temporal_objects()
            _evaluate_track_actions(elapsed, engine, dmx_fixtures,
                                     timeline_track_fids=timeline_track_fids,
                                     tl_action_ids=tl_action_ids)
            frame_count += 1
            if frame_count == 1:
                log.info("DMX playback: first frame sent at elapsed=%.1fs", elapsed)
        except Exception:
            # #845 — surface any per-frame failure in the log instead
            # of letting the daemon thread die silently. Rate-limited
            # to ~once/sec at 40 Hz so a persistent failure (e.g.
            # malformed profile, missing arbiter API) doesn't spam 40
            # tracebacks per second. The first failure always logs
            # because (frame_count % 40 == 0) is true at frame_count=0.
            if frame_count % 40 == 0:
                log.exception("DMX playback frame failed (frame=%d, elapsed=%.1fs)",
                              frame_count, elapsed)
            frame_count += 1
    log.info("DMX playback: stopped after %d frames", frame_count)
    # Timeline end (#800 idle definition): each mover the timeline was
    # driving snaps to home + lamp off, so the head doesn't sit at the
    # last cue's pose indefinitely. Non-mover DMX fixtures still get
    # the legacy zero-channel blackout (LEDs / generic dimmers don't
    # carry a Home anchor).
    # #763 — leave claimed fixtures alone; the operator owns their
    # output.
    final_snap = _claim_arbiter.snapshot()
    parked_fids = set()
    for fx in dmx_fixtures:
        if _claim_arbiter.is_muted(fx["fid"], final_snap):
            continue
        # Mover with Home + Secondary captured → snap to home + lamp off
        # via the canonical helper. Aux/effect channels mistyped as
        # `dimmer` (e.g. slymovehead's laser at ch10) are left
        # untouched — `_park_fixture_at_home` writes only the master.
        rec = next((r for r in _fixtures if r.get("id") == fx["fid"]), None)
        if (rec is not None
                and rec.get("homePanDmx16") is not None
                and rec.get("homeTiltDmx16") is not None
                and rec.get("homeSecondary")):
            try:
                _park_fixture_at_home(fx["fid"])
                parked_fids.add(int(fx["fid"]))
                continue
            except Exception:
                log.debug("timeline-end park fid=%s failed", fx["fid"],
                          exc_info=True)
        # Non-mover DMX fixture (or mover without Home set): legacy
        # zero-everything blackout.
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

    # #807 — park Track-action-driven movers the baked-segment loop
    # above missed. These fixtures never appear in `dmx_fixtures`
    # because `_evaluate_track_actions` produces their pan/tilt at
    # runtime instead of from a baked segment, so without this loop
    # they sat at their last commanded pose forever after natural-end.
    for tfid in track_driven_fids:
        if tfid in parked_fids:
            continue
        if _claim_arbiter.is_muted(tfid, final_snap):
            continue
        rec = next((r for r in _fixtures if r.get("id") == tfid), None)
        if (rec is not None
                and rec.get("homePanDmx16") is not None
                and rec.get("homeTiltDmx16") is not None
                and rec.get("homeSecondary")):
            try:
                _park_fixture_at_home(tfid)
                parked_fids.add(tfid)
            except Exception:
                log.debug("timeline-end park (track) fid=%s failed", tfid,
                          exc_info=True)

    # #807 — clear running flags on natural end. Pre-fix the
    # /api/timelines/<tid>/status endpoint kept reporting `running:
    # true` indefinitely past durationS because nothing flipped
    # `runnerRunning` back to False on the loop's natural exit (manual
    # `/stop` cleared it; natural end did not). With this in place the
    # SPA "Show running" indicator clears within the next status poll.
    with _lock:
        if (_settings.get("runnerRunning")
                and _settings.get("activeTimeline") == tid):
            _settings["runnerRunning"] = False
            _settings["activeTimeline"] = -1
            _settings["runnerStartEpoch"] = 0
            _save("settings", _settings)
            log.info("DMX playback: timeline %d natural-end — runner status cleared", tid)

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
        # Persisted "this is the auto-show" pointer. Unlike
        # `activeTimeline` — which is transient runtime state that
        # `api_bake_sync` and `api_timeline_stop` reset to -1 — this
        # field is only ever overwritten by the next start, so
        # `_auto_start_show` always has a timeline to resume after a
        # restart. Without it the auto-show silently never recovered.
        _settings["autoShowTimelineId"] = tid
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

    # #848 — claim-aware blackout (was `_artnet.blackout()` per #405);
    # operator-claimed fixtures keep their commanded output through stop.
    if _artnet.running or _sacn.running:
        _blackout_unclaimed_fixtures()

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

# #888 — generation counter to make show-start/stop/next thread-safe.
# Every spawn of `_show_playback_loop` captures the current generation;
# state mutations in the loop check that they still match before
# touching `_show_playback` / `_settings`. Prevents two loops from
# racing on universe writes when /api/show/next stop+respawns mid-segment.
_show_playback_generation = 0

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


def _show_playback_loop(playlist_order, loop_all, go_epoch, start_idx=0,
                        my_generation=None):
    """Background thread: play timelines sequentially.

    `my_generation` is captured at spawn time. After every mutation of
    shared state (`_show_playback`, `_settings`), we check that this
    loop is still the active generation; if not, a newer start/next has
    superseded us and we return without clobbering shared state. #888.
    """
    global _show_playback
    if my_generation is None:
        my_generation = _show_playback_generation

    def _is_current():
        return my_generation == _show_playback_generation
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

    # #840 — single-item looping playlist: route through
    # `_dmx_playback_loop(loop=True)`. That function does correct modulo
    # wrap internally and only blackouts on stop, eliminating the one-
    # frame blackout that the per-iteration `_dmx_playback_single` path
    # produces at every loop boundary.
    if loop_all and len(tl_list) == 1 and start_idx == 0:
        tid, tl = tl_list[0]
        duration = tl.get("durationS", 60)
        if not _is_current():
            return
        _show_playback["currentIndex"] = 0
        _show_playback["currentTid"] = tid
        _settings["activeTimeline"] = tid
        _save("settings", _settings)
        log.info("Show playback: single-item loop_all → _dmx_playback_loop")
        _dmx_playback_loop(tid, time.time(), duration, loop=True)
        if not _is_current():
            return  # a newer show start/next has taken over; leave shared state alone
        _show_playback["running"] = False
        _show_playback["currentTid"] = -1
        with _lock:
            _settings["runnerRunning"] = False
            _settings["activeTimeline"] = -1
            _settings["runnerStartEpoch"] = 0
            _save("settings", _settings)
        log.info("Show playback: finished")
        return

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

            # Run the single-timeline DMX loop inline (blocking).
            # #840 — this segment is "final" only when (a) it's the last
            # item of the playlist AND (b) we're not looping. Otherwise
            # skip the blackout sweep so the next iteration's first
            # frame doesn't follow an all-zero frame on the wire.
            is_final = (idx == len(tl_list) - 1) and not loop_all
            _dmx_playback_single(tid, time.time(), duration, is_final=is_final)

            if _dmx_playback_stop.is_set():
                break
            cumulative += duration
            _show_playback["totalElapsed"] = cumulative

        first_pass = False  # subsequent loops start from beginning (#361)
        if not loop_all or _dmx_playback_stop.is_set() or not _is_current():
            break
        # Loop: reset and go again
        cumulative = 0

    if not _is_current():
        return  # newer show start/next has taken over; preserve its shared state
    _show_playback["running"] = False
    _show_playback["currentTid"] = -1
    with _lock:
        _settings["runnerRunning"] = False
        _settings["activeTimeline"] = -1
        _settings["runnerStartEpoch"] = 0
        _save("settings", _settings)
    log.info("Show playback: finished")


def _dmx_playback_single(tid, go_epoch, duration, is_final=True):
    """Play a single timeline's DMX data. Returns when done or stopped.

    `is_final` (#840): when False, skip the post-loop blackout sweep
    so the next playlist iteration's first frame doesn't follow an
    all-zero frame on the wire. Pre-fix the orchestrator's per-iteration
    call always blackout-swept on natural end, producing a one-frame
    blackout at every loop wrap. Caller (`_show_playback_loop`) sets
    True only on the final iteration of a non-looping playlist or on
    the last iteration before stop. For single-item looping playlists
    the orchestrator now bypasses this function entirely and uses
    `_dmx_playback_loop(loop=True)` which has correct modulo-wrap and
    only blackouts on stop.
    """
    result = _bake_result.get(tid)
    if not result:
        return
    # #829 / #835 — same timeline-scope + action-id collection as _dmx_playback_loop.
    _tl_obj = next((t for t in _timelines if t.get("id") == tid), None)
    timeline_track_fids = set()
    tl_action_ids = set()
    for _tr in (_tl_obj.get("tracks", []) if _tl_obj else []):
        _fid = _tr.get("fixtureId")
        if _fid is not None:
            try: timeline_track_fids.add(int(_fid))
            except (TypeError, ValueError): pass
        for _cl in _tr.get("clips", []):
            _aid = _cl.get("actionId")
            if _aid is not None:
                try: tl_action_ids.add(int(_aid))
                except (TypeError, ValueError): pass
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
    # #622 — don't auto-start. If the engine is stopped we still let the
    # timer loop run so timeline duration is respected, but DMX writes
    # are skipped inside the inner loop.
    if not engine.running:
        log.info("DMX playback (single): engine stopped — DMX writes skipped")

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
        # #622 — skip DMX writes when engine is stopped (timer still ticks).
        if not engine.running:
            frame_count += 1
            continue
        # #763 — claim-arbiter snapshot, frozen for the duration of this frame.
        claim_snap = _claim_arbiter.snapshot()
        # #843 — segment-mode also snapshots master brightness per frame.
        with _lock:
            g_bri = _settings.get("globalBrightness", 255)
        for fx in dmx_fixtures:
            # #511 — skip playback for fixtures mid-calibration.
            if _fixture_is_calibrating(fx.get("id")):
                continue
            # #763 — skip show writes for fixtures held by mover-control.
            if _claim_arbiter.is_muted(fx["fid"], claim_snap):
                continue
            # #763 — smooth-handover slew window after release.
            _apply_handover_slew(fx["fid"], fx["uni"], fx["addr"],
                                 fx["ch_map"], engine)
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
            # #842 — see _dmx_playback_loop for the dispatch rationale.
            cm = fx["ch_map"] or {}
            # #853 — render-time scaling removed; ArtNet send-time
            # gate scales every output uniformly via gamma-corrected
            # master grand-master. See _dmx_playback_loop's identical
            # comment.
            if (color_wheel is not None and "color-wheel" in cm
                    and not any(c in cm for c in ("red", "green", "blue"))):
                uni_buf.set_channel(fx["addr"] + cm["color-wheel"], color_wheel)
            elif cm and (r or g or b
                         or any(c in cm for c in ("red", "green", "blue"))):
                uni_buf.set_fixture_rgb(fx["addr"], r, g, b, profile)
            if fx["ch_map"] and "dimmer" in fx["ch_map"]:
                dim = dimmer if dimmer is not None else (255 if (r or g or b) else 0)
                uni_buf.set_fixture_dimmer(fx["addr"], dim, profile)
            if pan is not None and tilt is not None and profile:
                uni_buf.set_fixture_pan_tilt(fx["addr"], pan, tilt, profile)
                # #806 phase 2 — segment-mode playback canonical hook
                # (mirrors the main playback loop above).
                try:
                    _f_full = next((_x for _x in _fixtures
                                    if _x.get("id") == fx["fid"]), None)
                    _prof_full = (_profile_lib.channel_info(_f_full.get("dmxProfileId"))
                                  if _f_full and _f_full.get("dmxProfileId") else None)
                    if _f_full is not None:
                        _aim_v = _canonical_aim_from_pan_tilt(
                            _f_full, _prof_full, pan, tilt)
                        if _aim_v is not None:
                            _set_canonical_aim_stage(fx["fid"], _aim_v)
                except Exception:
                    pass
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
        _evaluate_track_actions(elapsed, engine, dmx_fixtures,
                                 timeline_track_fids=timeline_track_fids,
                                 tl_action_ids=tl_action_ids)
        frame_count += 1
    # Blackout on segment end (#364) — zero RGB, dimmer, pan/tilt, and all extras.
    # #763 — leave claimed fixtures alone; the operator owns their output.
    # #840 — only run the blackout sweep when this is the final segment
    # OR when the operator pressed Stop. Mid-playlist iteration boundaries
    # (loop wrap, next-segment-coming-up) skip it so the universe buffer
    # holds its t=duration values until the next segment writes its t=0
    # values, eliminating the visible one-frame blackout.
    ended_by_stop = _dmx_playback_stop.is_set()
    if is_final or ended_by_stop:
        seg_snap = _claim_arbiter.snapshot()
        for fx in dmx_fixtures:
            if _claim_arbiter.is_muted(fx["fid"], seg_snap):
                continue
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

    # #888 — bump generation so any still-running prior loop sees the
    # mismatch and exits without clobbering this start's shared state.
    global _show_playback_generation
    _show_playback_generation += 1
    my_gen = _show_playback_generation
    threading.Thread(target=_show_playback_loop,
                     args=(order, loop_all, go_epoch, start_idx, my_gen),
                     daemon=True).start()
    return jsonify(ok=True, started=started, goEpoch=go_epoch, timelines=len(order))


def _blackout_unclaimed_fixtures():
    """#848 — lamp-off every UNCLAIMED DMX fixture, leaving operator-
    claimed fixtures (gyro press-Start, mover-control, calibration-in-
    progress) at their claim writer's commanded values.

    Mirrors the per-frame mute check `_dmx_playback_loop` and
    `_evaluate_track_actions` already use (#763). Pre-fix the show-stop
    paths called `_artnet.blackout()` which zeroed every channel of
    every universe — including the claim's pan/tilt/dimmer mid-claim,
    producing the multi-second "head took several seconds to move"
    perception the operator reported.

    `lamp_off` (from `dmx_profiles`) writes only intensity-class
    channels (dimmer, intensity, strobe-Open) to off — pan/tilt and
    other non-intensity channels are preserved per #781/#782.

    Returns the count of fixtures muted (skipped because claimed) so
    callers can log diagnostic context.
    """
    from dmx_profiles import lamp_off
    snap = _claim_arbiter.snapshot()
    engines = []
    if _artnet.running:
        engines.append(_artnet)
    if _sacn.running:
        engines.append(_sacn)
    if not engines:
        return 0
    muted = 0
    for engine in engines:
        for f in _fixtures:
            if f.get("fixtureType") != "dmx":
                continue
            fid = f.get("id")
            if _claim_arbiter.is_muted(fid, snap):
                muted += 1
                continue
            try:
                uni = int(f.get("dmxUniverse", 1) or 1)
                addr = int(f.get("dmxStartAddr", 1) or 1)
                pid = f.get("dmxProfileId")
                info = _profile_lib.channel_info(pid) if pid else None
                if not info:
                    continue
                profile = {"channel_map": info.get("channel_map", {}),
                           "channels":    info.get("channels", [])}
                buf = engine.get_universe(uni)
                # Stage current intensity-channel bytes into a scratch
                # buffer, run lamp_off on that, push changed bytes back
                # via set_channel (the supported per-byte write that
                # marks the buffer dirty for the engine's transmit loop).
                tmp = bytearray(512)
                for ch in (profile.get("channels") or []):
                    off = ch.get("offset", 0)
                    if 0 <= addr - 1 + off < 512:
                        try:
                            tmp[addr - 1 + off] = int(buf.get_channel(addr + off))
                        except Exception:
                            tmp[addr - 1 + off] = 0
                lamp_off(profile, tmp, addr, color=None)
                for ch in (profile.get("channels") or []):
                    if ch.get("type") in ("dimmer", "intensity", "strobe"):
                        off = ch.get("offset", 0)
                        if 0 <= addr - 1 + off < 512:
                            buf.set_channel(addr + off, tmp[addr - 1 + off])
            except Exception:
                continue
    return muted


@app.post("/api/show/next")
def api_show_next():
    """Skip to the next timeline in the running playlist. #888.

    If no show is running, returns 400. If at end-of-playlist with
    loopAll=False, behaves identically to /api/show/stop. Otherwise
    stops the current playback thread and restarts at the next index.

    Implementation note: one frame of dark may appear between segments
    because we stop + restart rather than threading a `skip` flag
    through `_dmx_playback_single`. Acceptable — the operator chose
    Next, the frame gap reads as transition, not glitch.
    """
    if not _show_playback.get("running"):
        return jsonify(err="No show running"), 400
    cur_idx = int(_show_playback.get("currentIndex", 0))
    loop_all = bool(_show_playback.get("loopAll", False))
    order = list(_show_playlist.get("order", []))
    if not order:
        return jsonify(err="Playlist is empty"), 400
    nxt = cur_idx + 1
    if nxt >= len(order):
        if loop_all:
            nxt = 0
        else:
            # Past the end without loop → fall through to stop.
            return api_show_stop()
    # Stop current playback thread, give it ~100ms to settle, then
    # restart via the existing show_start path with the new startIndex.
    _dmx_playback_stop.set()
    time.sleep(0.1)
    _dmx_playback_stop.clear()

    go_epoch = int(time.time()) + 2
    loop_flag = 1 if loop_all else 0
    go_pkt = _hdr(CMD_RUNNER_GO, go_epoch) + struct.pack("<IB", go_epoch, loop_flag)
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], go_pkt)
    _show_playback["running"] = True
    _show_playback["currentIndex"] = nxt
    _show_playback["currentTid"] = order[nxt]
    _show_playback["startEpoch"] = go_epoch
    _show_playback["loopAll"] = loop_all
    _show_playback["totalElapsed"] = 0
    with _lock:
        _settings["runnerRunning"] = True
        _settings["activeTimeline"] = order[nxt]
        _settings["runnerStartEpoch"] = go_epoch
        _save("settings", _settings)
    # #888 — bump generation so the prior loop exits cleanly on its
    # next state-mutation point instead of racing this one's writes.
    global _show_playback_generation
    _show_playback_generation += 1
    my_gen = _show_playback_generation
    threading.Thread(target=_show_playback_loop,
                     args=(order, loop_all, go_epoch, nxt, my_gen),
                     daemon=True).start()
    return jsonify(ok=True, currentIndex=nxt, currentTid=order[nxt])


@app.post("/api/show/stop")
def api_show_stop():
    """Stop sequential show playback + blackout all unclaimed output (#848)."""
    _dmx_playback_stop.set()
    pkt_stop = _hdr(CMD_RUNNER_STOP)
    pkt_off = _hdr(CMD_ACTION_STOP)
    for child in _children:
        if child.get("ip"):
            _send(child["ip"], pkt_stop)
            _send(child["ip"], pkt_off)
    # #848 — claim-aware blackout. Pre-fix this called `_artnet.blackout()`
    # which zeroed every channel of every universe, including the
    # active claim's pan/tilt/dimmer; the claim writer's next frame
    # had to fight back, producing a visible "head dark, then snap"
    # window that operators perceived as multi-second latency.
    if _artnet.running or _sacn.running:
        _blackout_unclaimed_fixtures()
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
        # #763 — fixtures currently held by mover-control. Operator-facing
        # SPA renders a green-ring slow-blink badge on these.
        "claimedFixtures": _claim_arbiter.claimed_fids(),
    })


#  "  "  Android crash intake  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

@app.post("/api/android/crash")
def api_android_crash():
    """Receive a crash / non-fatal report from the Android app and
    persist it under DATA/android-crashes/. The app uploads any pending
    reports on its next successful connection, so a field crash surfaces
    here without the operator hand-fishing files off the phone.

    Body is the raw report text (Content-Type text/plain). Kept
    deliberately dumb — no parsing, no schema — so a malformed report
    still lands on disk for a human to read."""
    body = request.get_data(as_text=True) or ""
    if not body.strip():
        return jsonify(err="empty report"), 400
    if len(body) > 256 * 1024:
        body = body[:256 * 1024] + "\n...[truncated]"
    crash_dir = DATA / "android-crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    # Disambiguate same-second uploads with a short random suffix.
    suffix = f"{int(time.time() * 1000) % 1000:03d}"
    (crash_dir / f"crash-{ts}-{suffix}.txt").write_text(body, encoding="utf-8")
    log.warning("Android crash report received (%d bytes) — saved to %s",
                len(body), crash_dir)
    return jsonify(ok=True)


#  "  "  Settings  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

# B1 phase 1 — /api/settings GET+POST moved to orch_project.py (blueprint
# "project", registered below at the old Actions-library position). The
# brightness / intensity-offsets / gamma / local-audio machinery below
# stays here: it is send-time hot-path code wired into the DMX engines and
# UDP listener, and tests getsource/monkeypatch it on this module.

# #843 — gamma LUT for global-brightness scaling at render time.
#
# LED children apply ``childBrightness`` via FastLED's gamma-corrected
# scaler in firmware. The orchestrator-side DMX render path was
# unscaled, so a mixed rig at globalBrightness=128 saw the LED strips
# look noticeably dimmer than DMX pars at the same setting (LED ~22%
# perceived, DMX ~50% linear). This LUT is the same gamma=2.2 curve
# FastLED uses, so a half-brightness setting reads the same on both
# fixture types.
#
# Build at import time so render frames pay zero cost beyond a list
# index. 256 bytes — trivial.
_GAMMA_LUT = bytes(int((i / 255.0) ** 2.2 * 255 + 0.5) for i in range(256))

# #853 — wire the gamma LUT into the engines now that it exists. The
# engines' send loops snapshot master brightness once per frame and
# apply this LUT to intensity-class channels via
# `DMXUniverse.get_data_scaled`.
_artnet._gamma_lut = _GAMMA_LUT
_sacn._gamma_lut = _GAMMA_LUT


# #853 — intensity-channel offset cache for the master grand-master
# gate. Built lazily, invalidated whenever fixtures change. Per-
# universe set of 0-based buffer indices (i.e. `addr - 1 + offset`
# for each intensity-typed channel on every DMX fixture). Pan / tilt /
# strobe / gobo / wheel-slot / prism / focus / zoom are excluded —
# they're indices / positions, not intensities.
_INTENSITY_TYPES = frozenset({
    "dimmer", "intensity",
    "red", "green", "blue", "white", "amber", "uv", "lime",
})
_intensity_offsets_cache = {}   # int(uni_num) → frozenset[int]
_intensity_offsets_cache_dirty = True


def _invalidate_intensity_offsets_cache():
    """Mark the per-universe intensity-channel cache dirty. Called
    whenever the fixture list / fixture profile assignment / address
    changes."""
    global _intensity_offsets_cache_dirty
    _intensity_offsets_cache_dirty = True


def _build_intensity_offsets_cache():
    """Walk every DMX fixture once and collect the buffer indices
    that should be scaled by the master grand-master."""
    global _intensity_offsets_cache, _intensity_offsets_cache_dirty
    cache = {}
    for f in _fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pid = f.get("dmxProfileId")
        info = _profile_lib.channel_info(pid) if pid else None
        if not info:
            continue
        try:
            uni = int(f.get("dmxUniverse", 1) or 1)
            addr = int(f.get("dmxStartAddr", 1) or 1)
        except (TypeError, ValueError):
            continue
        offsets = cache.setdefault(uni, set())
        for ch in info.get("channels") or []:
            ch_type = ch.get("type", "")
            if ch_type not in _INTENSITY_TYPES:
                continue
            try:
                off = int(ch.get("offset", 0))
            except (TypeError, ValueError):
                continue
            idx = addr - 1 + off
            if 0 <= idx < 512:
                offsets.add(idx)
    # Freeze to prevent the engine's send-thread from observing a
    # half-built set during a rebuild.
    _intensity_offsets_cache = {u: frozenset(s) for u, s in cache.items()}
    _intensity_offsets_cache_dirty = False


def _get_intensity_offsets(universe_num):
    """Return the frozenset of buffer indices on `universe_num` that
    are intensity-class channels (and therefore subject to the master
    grand-master gate). Empty frozenset for universes with no DMX
    fixtures or unknown profile."""
    if _intensity_offsets_cache_dirty:
        _build_intensity_offsets_cache()
    return _intensity_offsets_cache.get(int(universe_num), frozenset())


def _scale_for_brightness(value, g_bri):
    """Apply the global-brightness gamma curve to an 8-bit channel value.

    ``g_bri`` is the master brightness 0..255. ``value`` is the
    pre-scaled output (e.g. the bake's dimmer byte or an RGB
    component). Returns the gamma-corrected scaled byte.

    Fast-path: ``g_bri == 255`` returns the input unchanged. Most
    frames hit this path during normal operation; only Auto Brightness
    or a non-default master slider sees the LUT lookup.
    """
    if g_bri >= 255:
        return value
    if g_bri <= 0:
        return 0
    # Linear scale first, then gamma — matches FastLED's setBrightness
    # ordering: brightness modulates intensity, then the LUT maps the
    # post-scaled value to the perception curve.
    scaled = (int(value) * int(g_bri)) // 255
    return _GAMMA_LUT[scaled & 0xFF]


def _brightness_packet(value):
    """Build a CMD_SET_BRIGHTNESS UDP packet for a single 0..255 value."""
    iv = max(0, min(255, int(value)))
    return _hdr(CMD_SET_BRIGHTNESS) + bytes([iv])


# #859 — throttled dirty-mark for the master grand-master gate.
# Pre-fix every `/api/brightness` POST that changed value flipped
# every active universe's dirty bit, forcing the engine to emit
# ArtDmx at the full 40 Hz cadence (vs the 1 Hz keep-alive when no
# show is running). With Auto Brightness streaming varied envelope
# values at 20 Hz, this 4-5×'d the engine send-thread CPU even when
# the channel data hadn't changed — only the master multiplier had.
#
# Throttle: skip dirty-mark when both
#   (a) under 50 ms since last dirty-mark, AND
#   (b) value within 4 of last dirty-mark value.
# A 4-step delta at gamma 2.2 corresponds to ~0.3 % linear output
# change — invisible to the operator until accumulated. The
# ArtNet keep-alive (1 s) catches sub-threshold drifts so eventual
# consistency is preserved.
_LAST_BRIGHTNESS_DIRTY_TS = 0.0
_LAST_BRIGHTNESS_DIRTY_VALUE = 255


def _maybe_mark_universes_dirty_for_brightness(iv):
    """#859 — throttled wrapper for the #853 mark-all-dirty pattern.
    See module-level comment above for the threshold rationale."""
    global _LAST_BRIGHTNESS_DIRTY_TS, _LAST_BRIGHTNESS_DIRTY_VALUE
    now = time.monotonic()
    if ((now - _LAST_BRIGHTNESS_DIRTY_TS) < 0.05
            and abs(int(iv) - int(_LAST_BRIGHTNESS_DIRTY_VALUE)) < 4):
        return
    _LAST_BRIGHTNESS_DIRTY_TS = now
    _LAST_BRIGHTNESS_DIRTY_VALUE = int(iv)
    for engine in (_artnet, _sacn):
        if engine.running:
            for uni in engine._universes.values():
                uni.dirty = True


def _broadcast_brightness(value):
    """Send CMD_SET_BRIGHTNESS to every online LED child (#843).

    The 0x22 packet carries a single byte 0..255. Gyro / DMX-bridge /
    camera children ignore it; LED children apply it via FastLED. We
    skip ``type=="dmx"`` and ``type=="gyro"`` children so we don't burn
    UDP frames they'll discard.

    Used by:
      * ``/api/brightness`` fast path (Android auto-brightness, ~20 Hz)
      * ``/api/settings`` save when ``globalBrightness`` changes
      * ``_parse_pong`` post-receipt (newly-online child top-up)
    """
    pkt = _brightness_packet(value)
    for c in _children:
        ip = c.get("ip")
        if not ip:
            continue
        ctype = c.get("type")
        if ctype in ("dmx", "gyro"):
            continue  # not LED children — packet has no meaning
        _send(ip, pkt)


# #849 Part 1 — rate-limited observability for /api/brightness traffic.
# Pre-fix the endpoint logged nothing, so when Android Auto Brightness
# silently stopped POSTing (e.g. lastBrightnessJob.isActive in-flight
# guard wedged) there was no orchestrator-side signal at all — operators
# had to inject test values via curl to detect "the feed died." Now:
# first hop in any 5 s window logs at INFO with source IP + value;
# delta ≥10 from previously-logged value also logs (catches floor /
# ceiling transitions); every 30 s a summary entry rolls up
# hops + min/max/mean per source so a steady stream is visible without
# flooding at 20 Hz × N clients.
_brightness_obs_lock = threading.Lock()
_brightness_obs = {}   # remote_ip → {last_log_ts, last_log_value, hops,
                       #               min_v, max_v, sum_v, summary_ts}


def _log_brightness_hop(remote, value, prev):
    """#849 — record a /api/brightness POST and emit log entries when
    one of the rate-limit conditions fires (first-in-window, delta,
    summary)."""
    now = time.time()
    with _brightness_obs_lock:
        st = _brightness_obs.get(remote)
        if st is None:
            st = {"last_log_ts": 0.0, "last_log_value": -1,
                  "hops": 0, "min_v": value, "max_v": value, "sum_v": 0,
                  "summary_ts": now}
            _brightness_obs[remote] = st
        st["hops"] += 1
        st["min_v"] = min(st["min_v"], value)
        st["max_v"] = max(st["max_v"], value)
        st["sum_v"] += value
        # #862 — `current_value` tracks EVERY hop so the dashboard's
        # Auto Brightness card animates with the audio. The pre-fix
        # `last_log_value` only updated when a rate-limited log entry
        # fired (first-in-window or |delta| ≥ 10), so the dashboard
        # showed a frozen-looking `cur` value between log emissions
        # despite packets streaming at 20 Hz.
        st["current_value"] = value
        emit_first = (now - st["last_log_ts"]) >= 5.0
        emit_delta = (st["last_log_value"] >= 0
                      and abs(value - st["last_log_value"]) >= 10)
        emit_summary = (now - st["summary_ts"]) >= 30.0 and st["hops"] > 1
        if emit_first or emit_delta:
            log.info("/api/brightness from %s: value=%d (prev=%d, delta=%+d)",
                     remote, value, prev, value - prev)
            st["last_log_ts"] = now
            st["last_log_value"] = value
        if emit_summary:
            mean = st["sum_v"] // max(1, st["hops"])
            log.info("/api/brightness summary from %s: %d hops in %.1fs, "
                     "range %d-%d, mean ~%d",
                     remote, st["hops"], now - st["summary_ts"],
                     st["min_v"], st["max_v"], mean)
            st["summary_ts"] = now
            st["hops"] = 0
            st["min_v"] = value
            st["max_v"] = value
            st["sum_v"] = 0


def _handle_autobri_push(ip, data):
    """#861 — Android Auto Brightness UDP push handler.

    Replaces the prior HTTP `POST /api/brightness` fast-path which
    suffered TCP retransmit / connection-pool churn at 20 Hz audio
    cadence (live test 2026-05-08: zero POSTs landed in 3.5 min of
    music). UDP fire-and-forget with a 1-byte master value;
    orchestrator coalesces by overwriting `_settings["globalBrightness"]`
    per packet, the next DMX tick reads it. Manual-slider HTTP path
    (#843) stays unchanged — once-per-second human input is fine on TCP.

    Extracted from `_udp_listener` so the regression test can drive the
    dispatch path without spinning up a real socket bind.
    """
    try:
        master, flags, seq = struct.unpack_from("<BBB", data, 8)
        with _lock:
            prev_bri = _settings.get("globalBrightness", 255)
            _settings["globalBrightness"] = int(master)
        if int(master) != prev_bri:
            _broadcast_brightness(int(master))
            _maybe_mark_universes_dirty_for_brightness(int(master))
        # Re-use the HTTP path's rate-limited observability so the
        # dashboard's Auto Brightness card (#849 Part 2) surfaces this
        # stream identically. `flags` and `seq` are diagnostic-only for
        # now; reserved for a future `/api/auto-brightness/status`
        # endpoint.
        _log_brightness_hop(ip, int(master), prev_bri)
    except Exception as e:
        log.warning("AUTOBRI_PUSH handler failed: %s", e)


# #879 — local audio brightness producer. Uses the same coalescing
# dispatch as the Android #861 UDP path so the consumer side is
# identical (DMX gating, /api/remotes/live virtual source). Lazily
# initialised on first settings-update or at startup if the operator
# left it enabled in the persisted settings.
_local_audio_bri = None
_LOCAL_AUDIO_BRI_SOURCE = "local-audio"


def _local_audio_push_callback(master, flags, seq):
    """Bridge `LocalAudioBrightness` → `_handle_autobri_push`. Builds
    the same wire shape Android's UDP packet would carry, so the same
    handler coalesces both producers identically. `ip` is replaced by
    a constant source-id string so the dashboard surfaces it as a
    'Local audio' virtual remote alongside any Android phone push."""
    try:
        # Build the same 11-byte payload the AUTOBRI_PUSH handler
        # expects: header(8) + master(1) + flags(1) + seq(1). The
        # header bytes after the 4-byte magic+ver+cmd prefix are
        # ignored by the handler, so any constant filler works.
        pkt = struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION,
                          CMD_AUTOBRI_PUSH, 0) \
              + struct.pack("<BBB", int(master) & 0xFF,
                            int(flags) & 0xFF, int(seq) & 0xFF)
        _handle_autobri_push(_LOCAL_AUDIO_BRI_SOURCE, pkt)
    except Exception as e:
        log.debug("local-audio push callback failed: %s", e)


def _init_local_audio_bri():
    """Construct the singleton + apply persisted settings. Idempotent:
    returns the existing instance on repeat calls."""
    global _local_audio_bri
    if _local_audio_bri is not None:
        return _local_audio_bri
    try:
        from local_audio_brightness import LocalAudioBrightness
    except Exception as e:
        log.info("LocalAudioBrightness unavailable: %s", e)
        return None
    _local_audio_bri = LocalAudioBrightness(_local_audio_push_callback)
    persisted = _settings.get("localAudioBrightness") or {}
    if persisted:
        try:
            _local_audio_bri.update_config(persisted)
        except Exception as e:
            log.warning("LocalAudioBrightness apply persisted failed: %s", e)
    return _local_audio_bri


@app.get("/api/local-audio-brightness/devices")
def api_local_audio_bri_devices():
    """List input devices the orchestrator can capture from. Empty
    when sounddevice isn't installed."""
    inst = _init_local_audio_bri()
    if inst is None:
        return jsonify(available=False, devices=[])
    return jsonify(available=inst.is_available(),
                   devices=inst.list_devices())


@app.get("/api/local-audio-brightness")
def api_local_audio_bri_status():
    """Current settings + live status. `currentMaster` and `envelope`
    update at the capture-rate so the SPA can show a live VU bar."""
    inst = _init_local_audio_bri()
    if inst is None:
        return jsonify(available=False, enabled=False, config={})
    return jsonify(inst.get_status())


@app.post("/api/local-audio-brightness")
def api_local_audio_bri_update():
    """Merge body into the config + restart capture if needed. Body:
    `{enabled?, device?, gain?, floor?, ceiling?, attackMs?, releaseMs?}`.
    The Settings page wires every input through here."""
    inst = _init_local_audio_bri()
    if inst is None:
        return jsonify(ok=False,
                       err="sounddevice not installed on this server"), 503
    body = request.get_json(silent=True) or {}
    new_status = inst.update_config(body)
    # Persist the operator's choice so the feature comes back enabled
    # after a restart.
    with _lock:
        _settings["localAudioBrightness"] = dict(new_status.get("config", {}))
    _save("settings", _settings)
    return jsonify(ok=True, status=new_status)


# #804 — fast-path master brightness for Android auto-brightness (mic-driven).
# Updates the live in-memory value at high cadence (~20 Hz) without rewriting
# settings.json on every call. Manual slider keeps using POST /api/settings.
#
# #843 — additionally broadcasts CMD_SET_BRIGHTNESS to all LED children on
# every change so the value actually reaches the lights. Pre-fix the value
# stashed in _settings was never read by any output path.
#
# #849 — log rate-limited so operators can diagnose "is Android actually
# sending?" without injecting test values via curl.
@app.post("/api/brightness")
def api_brightness_fast():
    body = request.get_json(silent=True) or {}
    v = body.get("value")
    if not isinstance(v, (int, float)):
        log.warning("/api/brightness invalid body from %s: %r",
                    request.remote_addr, body)
        return jsonify(ok=False, err="value must be 0..255"), 400
    iv = int(max(0, min(255, v)))
    with _lock:
        prev = _settings.get("globalBrightness", 255)
        _settings["globalBrightness"] = iv
    if iv != prev:
        _broadcast_brightness(iv)
        _maybe_mark_universes_dirty_for_brightness(iv)
    _log_brightness_hop(request.remote_addr or "?", iv, prev)
    return jsonify(ok=True, value=iv)

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

# B1 phase 1 — Actions library, Config export-import (incl. show presets /
# export / import), and Project file (complete save/load) sections extracted
# to orch_project.py (blueprint "project"; route paths/behaviour unchanged).
import orch_project
app.register_blueprint(orch_project.bp)

# Re-exports — externally referenced names whose definitions moved:
# tests read parent_server.PROJECT_SCHEMA_VERSION and from-import
# _compress_cloud/_decompress_cloud (tests/test_project_spatial.py).
PROJECT_SCHEMA_VERSION = orch_project.PROJECT_SCHEMA_VERSION
CONFIG_SCHEMA_VERSION = orch_project.CONFIG_SCHEMA_VERSION
CONFIG_MIN_IMPORT_VERSION = orch_project.CONFIG_MIN_IMPORT_VERSION
_compress_cloud = orch_project._compress_cloud
_decompress_cloud = orch_project._decompress_cloud

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

# B1 phase 1 — firmware management + OTA update routes/helpers extracted to
# orch_firmware.py (blueprint "firmware"). _FW_DIR / _FW_CACHE_DIR stay
# defined above: tests monkeypatch them on THIS module
# (parent_server._FW_DIR = tmp, test_870) and orch_firmware reads them via
# the orch_state bridge at call time.
import orch_firmware
app.register_blueprint(orch_firmware.bp)

# Re-exports — externally referenced names whose definitions moved:
#  - _resolve_registry: monkeypatched via parent_server._resolve_registry
#    (test_870); orch_firmware always calls it through ps so the patch wins.
#  - _github_release_cache: from-imported and mutated in place by
#    tests/test_parent.py; the alias preserves object identity.
_resolve_registry = orch_firmware._resolve_registry
_github_release_cache = orch_firmware._github_release_cache

#  "  "  Help (Phase 7)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

# #545 — map SPA tab id → actual `## N. Heading` in docs/USER_MANUAL.md.
# The previous mapping pointed at headings that don't exist ("Dashboard",
# "Setup", "Firmware"), and assigned wrong numeric prefixes from a pre-v1
# manual layout. The api_help() reader does a case-insensitive substring
# match against each line starting with "## ", so we match on the number
# + title pair — stable across future heading-text wording tweaks.
_HELP_SECTIONS = {
    "dash":            "1. Getting Started",
    "setup":           "4. Fixture Setup",
    "layout":          "5. Stage Layout",
    "objects":         "6. Stage Objects",
    "actions":         "7. Creating Spatial Effects",  # SPA "Actions" tab
    "spatial-effects": "7. Creating Spatial Effects",
    "track":           "8. Track Action",
    "timeline":        "9. Building a Timeline",
    "shows":           "11. Show Preview Emulator",
    "runtime":         "9. Building a Timeline",
    "settings":        "12. DMX Fixture Profiles",
    "cameras":         "14. Camera Nodes",
    "firmware":        "15. Firmware & OTA Updates",
    "examples":        "18. Examples",
    "api":             "19. API Quick Reference",
}

# #670 — SPA tab id → split-source slug (the file under docs/src/{lang}/).
# Pre-built HTML fragments at docs/build/{lang}/help/{slug}.html win when
# they exist; otherwise the api_help reader falls back to scanning
# USER_MANUAL.md by the _HELP_SECTIONS heading anchor above.
_HELP_SLUGS = {
    "dash":            "01-getting-started",
    "setup":           "04-fixture-setup",
    "layout":          "05-stage-layout",
    "objects":         "06-stage-objects",
    "actions":         "07-spatial-effects",
    "spatial-effects": "07-spatial-effects",
    "track":           "08-track-actions",
    "timeline":        "09-building-timeline",
    "runtime":         "09-building-timeline",
    "baking":          "10-baking-playback",
    "shows":           "11-show-preview",
    "settings":        "12-dmx-profiles",
    "presets":         "13-preset-shows",
    "cameras":         "14-camera-nodes",
    "firmware":        "15-firmware-ota",
    "limits":          "16-system-limits",
    "troubleshooting": "17-troubleshooting",
    "examples":        "18-examples",
    "api":             "19-api-reference",
    "glossary":        "20-glossary",
    "appendix-a":      "appendix-a-camera-calibration",
    "appendix-b":      "appendix-b-mover-calibration",
    "appendix-c":      "appendix-c-maintenance",
}


def _resolve_lang():
    """Always returns ``"en"`` for now.

    The orchestrator has bilingual content infrastructure
    (``USER_MANUAL_fr.md``, ``docs/build/fr/help/*.html``, ``index_fr.html``,
    a ``slyled_lang`` cookie path, and Accept-Language autodetection) but
    no operator-facing language switcher in the SPA, so the
    autodetection path was silently serving French to any browser
    advertising ``Accept-Language: fr-…``. That surprised operators on
    Canadian / bilingual systems.

    Locked to English until install-time language selection + a
    general settings option land. The infrastructure stays in place
    so re-enabling is a one-line change here.
    """
    return "en"

@app.get("/help")
@app.get("/help/")
def serve_help_index():
    """#546 — serve the full user manual HTML at /help. Allows the '?'
    button in the SPA nav to open the complete manual in a new tab
    (rather than only the side-panel section extract). Works offline
    because the manual ships inside the project tree.

    Locked to English (``index.html``) for now — see ``_resolve_lang``
    for the rationale. The ``index_fr.html`` infrastructure stays in
    place for the planned install-time language selector + settings
    option; once that lands, change ``_resolve_lang`` and this route
    will pick it up.
    """
    help_path = DOCS_HELP / "index.html"
    if not help_path.exists():
        return ("<h1>User manual not found</h1>"
                "<p>Expected at <code>docs/help/index.html</code>.</p>",
                404, {"Content-Type": "text/html; charset=utf-8"})
    try:
        return (help_path.read_text(encoding="utf-8"),
                200, {"Content-Type": "text/html; charset=utf-8"})
    except Exception as e:
        return (f"<h1>Failed to read manual</h1><pre>{e}</pre>", 500,
                {"Content-Type": "text/html; charset=utf-8"})


@app.get("/help/images/<path:filename>")
def serve_help_image(filename):
    """#546 — serve images referenced by the manual. Path-safe via
    Flask's send_from_directory; falls back to 404 when the file is
    missing (some markdown references may not have a matching PNG
    yet)."""
    from flask import send_from_directory
    images_dir = DOCS_HELP / "images"
    if not images_dir.exists():
        return "", 404
    try:
        return send_from_directory(str(images_dir), filename)
    except Exception:
        return "", 404


# #881 — granular help keys. The SPA passes a dotted hierarchical key
# like ``setup.add-fixture.step-2-address`` or ``settings.dmx-monitor``;
# the resolver walks up the hierarchy until it finds a fragment that
# exists on disk, so an authored ``setup.add-fixture.html`` covers all
# wizard steps until per-step fragments ship.
_HELP_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


def _resolve_help_fragment(key, lang):
    """Walk a dotted help key up its hierarchy until a fragment exists.

    Given ``setup.add-fixture.step-2-address`` and only
    ``setup.add-fixture.html`` on disk, returns the path to the
    add-fixture fragment. Falls through to the legacy ``_HELP_SLUGS``
    map (which translates ``settings`` → ``12-dmx-profiles`` etc.)
    when no dotted variant matches, then to ``None`` so the caller
    can serve a stub.

    Refuses path traversal: any key with ``..`` / ``/`` / ``\\`` is
    treated as if the fragment doesn't exist. The HTML output of the
    pandoc build is plain dotted slugs so the constraint is invisible
    to the legitimate caller.
    """
    help_dir = DOCS_ROOT / "build" / lang / "help"
    if not key or not _HELP_KEY_RE.match(key):
        return None
    parts = key.split(".")
    while parts:
        slug = ".".join(parts)
        path = help_dir / f"{slug}.html"
        # Path traversal guard — resolve relative to help_dir and verify
        # we stayed inside it. Cheap belt-and-braces over the regex.
        try:
            resolved = path.resolve()
            help_resolved = help_dir.resolve()
            if help_resolved in resolved.parents and resolved.is_file():
                return resolved
        except (OSError, ValueError):
            pass
        parts.pop()
    # Legacy fallback: the top-level segment may match an entry in the
    # tab → chapter slug map. ``settings.dmx-monitor`` with no fragment
    # falls to ``settings`` → ``12-dmx-profiles.html``.
    top = key.split(".", 1)[0]
    legacy_slug = _HELP_SLUGS.get(top)
    if legacy_slug:
        path = help_dir / f"{legacy_slug}.html"
        if path.is_file():
            return path
    return None


@app.get("/api/help/<section>")
def api_help(section):
    """Return help content for a given SPA tab or granular helpkey.

    Resolution order (#670, #881):
    1. Exact match: ``docs/build/{lang}/help/{key}.html``.
    2. Walk up the dotted hierarchy: drop the trailing segment and retry
       until a fragment is found, so authored sub-keys override
       coarser chapter fragments and missing keys fall back naturally.
    3. Legacy ``_HELP_SLUGS`` chapter-level map (``settings`` →
       ``12-dmx-profiles``).
    4. Legacy USER_MANUAL.md heading scanner for installs whose docs
       build hasn't run yet.
    5. A 200-with-stub fragment so the SPA never has to handle a 404 —
       the side panel always has something to render.
    """
    lang = _resolve_lang()
    slug = _HELP_SLUGS.get(section, section)
    fragment = _resolve_help_fragment(section, lang)
    if fragment is not None:
        try:
            return jsonify(html=fragment.read_text(encoding="utf-8"),
                           lang=lang, slug=fragment.stem, source="fragment")
        except Exception as e:
            log.warning("help fragment read failed %s: %s", fragment, e)

    # ── Legacy fallback: scan USER_MANUAL.md for the anchor heading ──
    # #881 — when the key is dotted (``settings.dmx-monitor``), the
    # heading map only has the top-level segment, so scan against that.
    top_segment = section.split(".", 1)[0]
    manual_path = DOCS_ROOT / ("USER_MANUAL_fr.md" if lang == "fr"
                                else "USER_MANUAL.md")
    if not manual_path.exists():
        manual_path = DOCS_ROOT / "USER_MANUAL.md"
    if not manual_path.exists():
        return jsonify(html=_help_stub_html(section), lang=lang,
                       slug=slug, source="stub")
    try:
        text = manual_path.read_text(encoding="utf-8")
        anchor = _HELP_SECTIONS.get(top_segment) or _HELP_SECTIONS.get(section)
        if not anchor:
            return jsonify(html=_help_stub_html(section), lang=lang,
                           slug=slug, source="stub")
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
            return jsonify(html=_help_stub_html(section), lang=lang,
                           slug=slug, source="stub")
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
        return jsonify(html=html, lang=lang, slug=slug, source="legacy-scan")
    except Exception as e:
        return jsonify(html=f"<p>Error loading help: {e}</p>", lang=lang)


def _help_stub_html(key):
    """#881 — fallback body when no fragment / chapter matches.

    The UI expects every ``/api/help/*`` response to have renderable
    HTML; a 404 would leave the side panel empty and confuse the
    operator. The stub points back to the full manual so the operator
    has a recovery path.
    """
    safe = (key or "").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"<h3 style='color:#22d3ee;margin:0 0 .6em'>Help</h3>"
        f"<p style='color:#cbd5e1;margin:.3em 0'>No targeted help fragment "
        f"is available for <code>{safe}</code> yet.</p>"
        f"<p style='margin:.5em 0'>Try the "
        f"<a href='/help' target='_blank' style='color:#22d3ee'>full user "
        f"manual</a> for complete documentation.</p>"
    )


@app.get("/api/glossary")
def api_glossary():
    """Return the structured glossary (#670) for SPA hover cards.

    Sourced from ``docs/schema/glossary.yml`` (generated by
    ``tools/docs/extractor.py``). Each entry has both EN + FR short /
    long definitions, an ``acronym`` flag, and cross-references.
    """
    lang = _resolve_lang()
    schema = DOCS_ROOT / "schema" / "glossary.yml"
    if not schema.exists():
        return jsonify(ok=False, err="glossary.yml not built yet — "
                                      "run tools/docs/build.py",
                       entries=[], lang=lang)
    try:
        import yaml  # PyYAML — installed alongside python-docx
        entries = yaml.safe_load(schema.read_text(encoding="utf-8")) or []
        return jsonify(ok=True, lang=lang, entries=entries)
    except ImportError:
        return jsonify(ok=False, err="PyYAML not available on this host"), 500
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 500

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
    _ota_status_live.clear()
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
        # #785 — drop every cached aim sphere; fixtures table just got
        # wiped, any residual cache entries point at non-existent fids.
        _aim_invalidate_all_spheres()
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
        _calib_state.clear()
        # #693-followup — clear the remotes registry too. Persisted
        # remotes from earlier sessions/tests carry old `registeredAt`
        # timestamps; with #690's never-active staleness path, an
        # auto-registered remote that re-uses an old deviceId would
        # immediately be flagged hard-stale and the engine would
        # auto-release the operator's claim before any DMX writes
        # land. Reset must wipe this state to keep tests + boot-fresh
        # operator sessions consistent.
        _remotes._remotes.clear()
        _remotes._next_id = 1
        _remotes.save()
        _tracking_state.clear()
        # Delete custom profiles (keep built-ins)
        for p in list(_profile_lib._profiles.values()):
            if not p.get("builtin"):
                _profile_lib.delete_profile(p["id"])
    return jsonify(ok=True)

# B1 phase 1 — the "OTA firmware update" section (child version check,
# /api/firmware/latest, /api/firmware/check, /api/firmware/ota) moved to
# orch_firmware.py, registered as a Blueprint where the "Firmware
# management" section used to live (see above).
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
    # #893 — do NOT reflect arbitrary Origins. Reflecting the request's
    # Origin (plus allowing X-SlyLED-Confirm) handed every web page a
    # cross-origin grant that defeated the CSRF-confirm header on
    # /api/shutdown and every other destructive endpoint. Nothing
    # legitimate needs a cross-origin grant here: the SPA is served
    # same-origin by this very server, and the Android/iOS apps use
    # native HTTP stacks that don't enforce CORS. We therefore only
    # emit CORS headers when the Origin's *host* matches the host this
    # request was addressed to (any port — dev SPA on another port is
    # fine); otherwise no CORS headers at all and the browser blocks.
    origin = request.headers.get("Origin", "")
    if not origin:
        return response
    try:
        origin_host = (urlsplit(origin).hostname or "").lower()
    except ValueError:
        origin_host = ""
    server_host = (urlsplit("//" + request.host).hostname or "").lower()
    if origin_host and origin_host == server_host:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-SlyLED-Confirm, X-SlyLED-Token"
        response.headers.add("Vary", "Origin")
    return response

#  ”  ”  Destructive-endpoint token gate (B2)  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”  ”

# Optional shared-token gate on destructive endpoints. Default OFF: with no
# token configured, behaviour is exactly as before. A token is configured by
# either the SLYLED_API_TOKEN environment variable (takes precedence) or the
# operator-editable settings key "apiToken" (POST /api/settings).
#
# The exact rule, when a token IS configured, per request to a destructive
# path (any method except OPTIONS — CORS preflights carry no side effects):
#   1. header X-SlyLED-Token matches the configured token
#      (hmac.compare_digest)                                → allowed
#   2. else, the request carries an Origin header whose HOST matches the
#      host this request was addressed to (any port — the same rule the
#      #893 CORS grant uses; browsers attach Origin to every POST, so the
#      same-origin SPA keeps working with zero SPA changes, and a cross-
#      site attacker page cannot forge its Origin)           → allowed
#   3. otherwise                                             → 401
#
# Consequence: the token gates only cross-origin browser callers and
# scripted/native callers (curl, python-requests, the mobile apps — they
# send no Origin). Operators who set a token must add the X-SlyLED-Token
# header to any script or mobile client that hits a destructive endpoint.
# The pre-existing X-SlyLED-Confirm checks on /api/shutdown and /api/reset
# are unchanged and evaluated after this gate.

import hmac as _hmac

_DESTRUCTIVE_EXACT = {
    "/api/shutdown",         # kill the orchestrator process
    "/api/reset",            # factory reset (wipe all project data)
    "/api/firmware/flash",   # serial-flash a board
    "/api/cameras/deploy",   # SSH+SCP deploy onto a camera node
}

def _is_destructive_path(path):
    if path in _DESTRUCTIVE_EXACT:
        return True
    if path.startswith("/api/firmware/ota/"):                       # child OTA
        return True
    if path.startswith("/api/children/") and path.endswith("/reboot"):
        return True
    return False

def _configured_api_token():
    return os.environ.get("SLYLED_API_TOKEN") or _settings.get("apiToken") or ""

@app.before_request
def _destructive_token_gate():
    if request.method == "OPTIONS" or not _is_destructive_path(request.path):
        return None
    token = _configured_api_token()
    if not token:
        return None                       # gate disabled — today's behaviour
    supplied = request.headers.get("X-SlyLED-Token", "")
    if supplied and _hmac.compare_digest(supplied, token):
        return None
    origin = request.headers.get("Origin", "")
    if origin:
        try:
            origin_host = (urlsplit(origin).hostname or "").lower()
        except ValueError:
            origin_host = ""
        server_host = (urlsplit("//" + request.host).hostname or "").lower()
        if origin_host and origin_host == server_host:
            return None                   # same-origin browser (the SPA)
    return jsonify(ok=False,
                   err="API token required — send header X-SlyLED-Token "
                       "(configured via SLYLED_API_TOKEN or settings.apiToken)"), 401

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
    if path.startswith("api/") or path in ("status", "favicon.ico", "favicon.png"):
        abort(404)
    resp = send_from_directory(str(SPA), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

#  "  "  Entry point  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

def _resolve_server():
    """B2 — pick the HTTP server. Returns ("waitress", serve_callable) when
    waitress is importable, else ("flask", None). Split out from _serve so
    tests can exercise the fallback decision without binding a port."""
    try:
        from waitress import serve
        return "waitress", serve
    except ImportError:
        return "flask", None

def _serve(host, port):
    """B2 — serve `app` via waitress when available (production-grade WSGI:
    bounded thread pool, no dev-server warning), falling back to the Flask
    dev server so a source checkout without waitress behaves exactly as
    before. Used by both launch paths (parent_server.py __main__ and
    main.py's tray launcher)."""
    kind, serve = _resolve_server()
    if kind == "waitress":
        print(f"  Serving via waitress on {host}:{port}")
        serve(app, host=host, port=port, threads=16)
    else:
        print(f"  waitress not installed - using Flask dev server on {host}:{port}")
        app.run(host=host, port=port, threaded=True, use_reloader=False)

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

# #784 PR-7 — `_migrate_smart_legacy_flag` and `_migrate_v1_mover_cals`
# deleted along with the SMART pipeline. The new aim sphere reads
# `homePanDmx16` / `homeTiltDmx16` / `homeSecondary` / `rotation`
# directly off the fixture record; nothing in `_mover_cal` is consumed
# at runtime any more.


if __name__ == "__main__":
    # #628 — re-derive stage bounds once at startup so rigs with stale
    # manually-edited stage.json self-heal without operator intervention.
    # No-op if the operator has stageBoundsManual=true.
    try:
        _apply_auto_stage_bounds()
    except Exception as _e:
        log.warning("stage auto-derive on startup failed: %s", _e)
    # #784 PR-7 — `_migrate_v1_mover_cals` and `_migrate_smart_legacy_flag`
    # deleted along with the SMART pipeline.
    # #600 — swap rotation array convention once on startup. No-op if the
    # layout already records rotationSchemaVersion == 2.
    try:
        _migrate_rotation_schema()
    except Exception as _e:
        log.warning("#600 rotation migration on startup failed: %s", _e)
    # #780 P1 — bake mountedInverted=True into rotation[1] += 180°. No-op
    # once mountedInvertedSchemaVersion == 1.
    try:
        _migrate_mounted_inverted_schema()
    except Exception as _e:
        log.warning("#780 P1 mountedInverted migration on startup failed: %s", _e)
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
    _serve(args.host, args.port)








































































































































































































