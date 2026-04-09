#!/usr/bin/env python3
"""
SlyLED Camera Node Server

Runs on OrangePi / Raspberry Pi hardware. Exposes HTTP API for the
SlyLED orchestrator to discover, configure, and control the camera.

Usage:  python3 camera_server.py
        Listens on port 5000, advertises via mDNS as _slyled-cam._tcp.
"""

import atexit
import json
import os
import socket
import struct
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

VERSION = "1.2.3"
PORT = 5000
UDP_PORT = 4210
CONFIG_DIR = Path("/opt/slyled")
CONFIG_PATH = CONFIG_DIR / "camera.json"

# UDP protocol constants (must match parent_server.py)
UDP_MAGIC   = 0x534C
UDP_VERSION = 4
CMD_PING       = 0x01
CMD_PONG       = 0x02
CMD_STATUS_REQ = 0x40
CMD_STATUS_RESP = 0x41

app = Flask(__name__)

_config = {}
_hw_info = {}

# ── Config persistence ──────────────────────────────────────────────────

def _load_config():
    global _config
    defaults = {
        "hostname": "",
        "fovDeg": 60,
        "cameraFov": {},
        "cameraCfg": {},  # per-camera: {idx: {name, fovDeg, enabled, flip, preferred}}
        "cameraUrl": "",
        "resolutionW": 1920,
        "resolutionH": 1080,
    }
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
            defaults.update(saved)
    except Exception:
        pass
    _config = defaults

def _camera_fov(idx):
    """Get FOV: user config > detected from USB VID:PID > node default."""
    cfg = _config.get("cameraCfg", {}).get(str(idx), {})
    if cfg.get("fovDeg"):
        return cfg["fovDeg"]
    fov_map = _config.get("cameraFov", {})
    if str(idx) in fov_map:
        return fov_map[str(idx)]
    # Try auto-detected from hardware
    cameras = _hw_info.get("cameras", [])
    if idx < len(cameras) and cameras[idx].get("detectedFov"):
        return cameras[idx]["detectedFov"]
    return _config.get("fovDeg", 60)

def _camera_cfg(idx):
    """Get full config for a camera index with defaults."""
    cfg = _config.get("cameraCfg", {}).get(str(idx), {})
    return {
        "name": cfg.get("name", ""),
        "fovDeg": cfg.get("fovDeg", _camera_fov(idx)),
        "enabled": cfg.get("enabled", True),
        "flip": cfg.get("flip", "none"),  # none, h, v, 180
        "preferred": cfg.get("preferred", False),
    }

def _save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to save config: {e}")

# ── Known camera FOV database (USB VID:PID → degrees) ─────────────────
# Add entries as cameras are identified
KNOWN_FOV = {
    "3443:60bb": 60,    # NexiGo N60 FHD Webcam
    "038f:0541": 78,    # lihappe8 USB 2.0 Camera
    "0c45:636b": 100,   # Spedal MF920P (Microdia chipset)
    "2341:025e": 90,    # EMEET SmartCam Nova 4K (variant)
    "328f:00eb": 90,    # EMEET SmartCam Nova 4K
    "1bcf:2284": 90,    # EMEET SmartCam
    "046d:0825": 78,    # Logitech C270
    "046d:082d": 78,    # Logitech C920
    "046d:0893": 90,    # Logitech C930e
    "046d:085c": 82,    # Logitech C922
    "046d:08e5": 90,    # Logitech BRIO
}

# ── Hardware detection ──────────────────────────────────────────────────

def _detect_usb_fov():
    """Build a map of /dev/videoN → FOV from USB VID:PID lookup via udevadm."""
    import subprocess, glob
    fov_map = {}
    for dev_path in sorted(glob.glob("/dev/video*")):
        try:
            # Use udevadm to get the USB VID:PID for this video device
            r = subprocess.run(["udevadm", "info", "-q", "property", "-n", dev_path],
                               capture_output=True, text=True, timeout=2)
            vid = pid = ""
            for line in r.stdout.splitlines():
                if line.startswith("ID_VENDOR_ID="):
                    vid = line.split("=", 1)[1].strip()
                elif line.startswith("ID_MODEL_ID="):
                    pid = line.split("=", 1)[1].strip()
            if vid and pid:
                key = f"{vid}:{pid}"
                if key in KNOWN_FOV:
                    fov_map[dev_path] = KNOWN_FOV[key]
        except Exception:
            pass
    return fov_map

def _detect_cameras():
    """List real video capture devices, filtering out SoC nodes (sunxi-vin etc.)
    that crash when probed. Uses v4l2-ctl --info (safe) to check the driver name."""
    import glob, subprocess
    cameras = []
    has_v4l2 = os.path.exists("/usr/bin/v4l2-ctl")
    usb_fov = _detect_usb_fov()
    for dev in sorted(glob.glob("/dev/video*")):
        if has_v4l2:
            try:
                r = subprocess.run(
                    ["/usr/bin/v4l2-ctl", "-d", dev, "--info"],
                    capture_output=True, text=True, timeout=3)
                driver = ""
                card = dev
                caps = ""
                for line in r.stdout.splitlines():
                    if "Driver name" in line:
                        driver = line.split(":", 1)[1].strip()
                    elif "Card type" in line:
                        card = line.split(":", 1)[1].strip()
                    elif "Capabilities" in line:
                        caps = line
                # Skip SoC/ISP media nodes (not real cameras)
                if any(d in driver for d in ("sunxi", "sun6i", "cedrus", "bcm2835", "bcm2835-isp")) or \
                   any(d in card for d in ("bcm2835-isp", "sunxi-vin")):
                    continue
                # Check Device Caps section (after "Device Caps" line)
                # Real capture has "Video Capture", metadata-only has "Metadata Capture"
                in_dev_caps = False
                is_capture = False
                for line in r.stdout.splitlines():
                    if "Device Caps" in line and ":" in line:
                        in_dev_caps = True
                    elif in_dev_caps:
                        stripped = line.strip()
                        if stripped == "Video Capture":
                            is_capture = True
                            break
                        elif not stripped.startswith(("\t", " ", "0x")):
                            break  # next section
                if not is_capture:
                    continue
                detected_fov = usb_fov.get(dev, 0)
                cameras.append({"device": dev, "resW": 0, "resH": 0,
                                "name": card, "probed": False,
                                "detectedFov": detected_fov})
            except Exception:
                cameras.append({"device": dev, "resW": 0, "resH": 0,
                                "name": dev, "probed": False})
        else:
            cameras.append({"device": dev, "resW": 0, "resH": 0,
                            "name": dev, "probed": False})
    # NOTE: Pi CSI ribbon cameras (libcamera/rpicam) not supported in v1.x.
    # Only USB cameras via V4L2 are supported. See docs/camera.md for details.

    return cameras

def _probe_camera_details(cam):
    """Lazy-probe a single camera device for name and resolution via v4l2-ctl.
    Called on first snapshot or status request, not at startup."""
    if cam.get("probed"):
        return
    cam["probed"] = True
    import subprocess
    if not os.path.exists("/usr/bin/v4l2-ctl"):
        return
    dev = cam["device"]
    try:
        r = subprocess.run(
            ["/usr/bin/v4l2-ctl", "-d", dev, "--info"],
            capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if "Card type" in line:
                cam["name"] = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["/usr/bin/v4l2-ctl", "--list-formats-ext", "-d", dev],
            capture_output=True, text=True, timeout=3)
        max_w, max_h = 0, 0
        for line in r.stdout.splitlines():
            if "Size:" in line:
                for p in line.split():
                    if "x" in p and p[0].isdigit():
                        try:
                            w, h = p.split("x")
                            w, h = int(w), int(h)
                            if w * h > max_w * max_h:
                                max_w, max_h = w, h
                        except ValueError:
                            pass
        if max_w > 0:
            cam["resW"] = max_w
            cam["resH"] = max_h
    except Exception:
        pass

def _detect_hardware():
    global _hw_info
    info = {"board": "unknown", "hasCamera": False, "tracking": False, "cameras": []}

    # Board identification
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text().strip().rstrip("\x00")
            info["board"] = model
    except Exception:
        pass

    # Camera device detection (multiple cameras supported)
    info["cameras"] = _detect_cameras()
    info["hasCamera"] = len(info["cameras"]) > 0

    # Use highest resolution from first camera as defaults
    if info["cameras"]:
        first = info["cameras"][0]
        if first["resW"] > 0:
            _config.setdefault("resolutionW", first["resW"])
            _config.setdefault("resolutionH", first["resH"])

    # Hostname
    hostname = socket.gethostname()
    if not _config.get("hostname"):
        _config["hostname"] = hostname
    info["hostname"] = _config["hostname"] or hostname

    _hw_info = info

    # Apply saved V4L2 settings on startup
    _apply_saved_v4l2()


# ── Per-camera V4L2 controls (#271) ───────────────────────────────────

V4L2_SETTINGS_DIR = CONFIG_DIR / "v4l2"
V4L2_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def _apply_saved_v4l2():
    """Apply saved V4L2 settings to all cameras on startup."""
    import subprocess
    for i, cam in enumerate(_hw_info.get("cameras", [])):
        path = V4L2_SETTINGS_DIR / f"cam{i}.json"
        if not path.exists():
            continue
        try:
            settings = json.loads(path.read_text())
            dev = cam["device"]
            for k, v in settings.items():
                subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"{k}={v}"],
                               capture_output=True, timeout=3)
            log.info("Applied V4L2 settings to cam%d (%s): %s", i, dev, settings)
        except Exception as e:
            log.warning("Failed to apply V4L2 settings cam%d: %s", i, e)


@app.get("/camera/controls")
def camera_controls():
    """List V4L2 controls for a camera with current values.
    Query: ?cam=0"""
    import subprocess
    cam_idx = int(request.args.get("cam", 0))
    cameras = _hw_info.get("cameras", [])
    if cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    dev = cameras[cam_idx]["device"]
    try:
        r = subprocess.run(["v4l2-ctl", "-d", dev, "--list-ctrls"],
                           capture_output=True, text=True, timeout=5)
        controls = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            # Parse: "brightness 0x00980900 (int) : min=-64 max=64 step=1 default=0 value=0"
            parts = line.split(":")
            if len(parts) < 2:
                continue
            name_part = parts[0].strip()
            name = name_part.split()[0] if name_part else ""
            vals = parts[1].strip()
            ctrl = {"name": name}
            for token in vals.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    try:
                        ctrl[k] = int(v)
                    except ValueError:
                        ctrl[k] = v
            if "(" in name_part:
                ctrl["type"] = name_part.split("(")[1].split(")")[0]
            controls.append(ctrl)
        # Load saved settings
        saved_path = V4L2_SETTINGS_DIR / f"cam{cam_idx}.json"
        saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
        return jsonify(ok=True, cam=cam_idx, controls=controls, saved=saved)
    except Exception as e:
        return jsonify(ok=False, err=str(e)), 500


@app.post("/camera/controls")
def camera_controls_set():
    """Set V4L2 controls and save. Body: {cam: 0, controls: {brightness: -10, ...}}"""
    import subprocess
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    ctrls = body.get("controls", {})
    cameras = _hw_info.get("cameras", [])
    if cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    dev = cameras[cam_idx]["device"]
    applied = {}
    for k, v in ctrls.items():
        try:
            subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"{k}={v}"],
                           capture_output=True, timeout=3)
            applied[k] = v
        except Exception as e:
            log.warning("v4l2 set %s=%s failed: %s", k, v, e)
    # Save persistently
    saved_path = V4L2_SETTINGS_DIR / f"cam{cam_idx}.json"
    existing = json.loads(saved_path.read_text()) if saved_path.exists() else {}
    existing.update(applied)
    saved_path.write_text(json.dumps(existing, indent=2))
    log.info("V4L2 cam%d: set %s, saved to %s", cam_idx, applied, saved_path)
    return jsonify(ok=True, applied=applied)


# ── mDNS advertisement ──────────────────────────────────────────────────

_zc = None
_zc_info = None

def _register_mdns():
    global _zc, _zc_info
    try:
        from zeroconf import Zeroconf, ServiceInfo
        hostname = _config.get("hostname") or socket.gethostname()
        ip = _get_local_ip()
        _zc_info = ServiceInfo(
            "_slyled-cam._tcp.local.",
            f"{hostname}._slyled-cam._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=PORT,
            properties={
                "role": "camera",
                "version": VERSION,
                "board": _hw_info.get("board", "unknown"),
            },
        )
        _zc = Zeroconf()
        _zc.register_service(_zc_info)
        print(f"[mDNS] Registered _slyled-cam._tcp as {hostname} at {ip}:{PORT}")
    except Exception as e:
        print(f"[mDNS] Registration failed: {e}")

def _unregister_mdns():
    global _zc, _zc_info
    try:
        if _zc and _zc_info:
            _zc.unregister_service(_zc_info)
            _zc.close()
    except Exception:
        pass

def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ── HTTP routes ─────────────────────────────────────────────────────────

@app.get("/status")
def status():
    # Lazy-probe cameras on first status request
    cameras = _hw_info.get("cameras", [])
    for cam in cameras:
        _probe_camera_details(cam)
    # Build cameras list with per-camera FOV
    cam_list = []
    for i, cam in enumerate(cameras):
        c = dict(cam)
        cfg = _camera_cfg(i)
        c["fovDeg"] = cfg["fovDeg"]
        c["customName"] = cfg["name"]
        c["enabled"] = cfg["enabled"]
        c["flip"] = cfg["flip"]
        c["preferred"] = cfg["preferred"]
        cam_list.append(c)
    return jsonify({
        "role": "camera",
        "hostname": _config.get("hostname") or socket.gethostname(),
        "fwVersion": VERSION,
        "fovDeg": _config.get("fovDeg", 60),
        "resolutionW": _config.get("resolutionW", 1920),
        "resolutionH": _config.get("resolutionH", 1080),
        "cameraCount": len(cameras),
        "cameras": cam_list,
        "capabilities": {
            "tracking": (_tracker.running if _tracker else False),
            "hasCamera": _hw_info.get("hasCamera", False),
            "scan": _get_detector() is not None,
        },
        "board": _hw_info.get("board", "unknown"),
    })

@app.get("/config")
def config_page():
    """HTML config SPA — consistent with performer /config pages."""
    hostname = _config.get("hostname") or socket.gethostname()
    has_scan = _get_detector() is not None
    det_btn_style = "background:#7c3aed" if has_scan else "background:#7c3aed;display:none"
    board = _hw_info.get("board", "unknown")
    res = f"{_config.get('resolutionW', '?')}x{_config.get('resolutionH', '?')}"
    ip = _get_local_ip()
    uptime = int(time.monotonic())
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{hostname} — SlyLED Camera</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:1em;max-width:480px;margin:0 auto}}
h1{{font-size:1.1em;color:#22d3ee;margin-bottom:.2em}}
.sub{{color:#64748b;font-size:.78em;margin-bottom:1em}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.8em;margin-bottom:.8em}}
.card h2{{font-size:.85em;color:#94a3b8;margin-bottom:.5em}}
label{{display:block;font-size:.82em;color:#94a3b8;margin:.4em 0 .15em}}
input,select{{width:100%;padding:.35em .5em;background:#0f172a;border:1px solid #475569;border-radius:4px;color:#e2e8f0;font-size:.9em}}
.row{{display:flex;gap:.5em}}
.row>div{{flex:1}}
.btn{{display:inline-block;padding:.4em 1.2em;border:none;border-radius:4px;font-size:.85em;cursor:pointer;margin-top:.5em}}
.btn-save{{background:#0e7490;color:#fff}}
.btn-save:hover{{background:#0891b2}}
.btn-reboot{{background:#dc2626;color:#fff;font-size:.75em;padding:.3em .8em}}
.btn-reset{{background:#475569;color:#e2e8f0;font-size:.75em;padding:.3em .8em}}
.info-row{{display:flex;justify-content:space-between;padding:.2em 0;font-size:.82em}}
.info-row .lbl{{color:#64748b}}
.badge{{display:inline-block;padding:.1em .4em;border-radius:3px;font-size:.75em}}
.badge-on{{background:#065f46;color:#34d399}}
.badge-off{{background:#7f1d1d;color:#fca5a5}}
#msg{{color:#22d3ee;font-size:.82em;min-height:1.2em;margin-top:.3em}}
.tabs{{display:flex;gap:2px;margin-bottom:.8em}}
.tab{{padding:.35em .8em;background:#1e293b;border:1px solid #334155;border-radius:4px 4px 0 0;cursor:pointer;font-size:.82em;color:#94a3b8}}
.tab.active{{background:#334155;color:#e2e8f0;border-bottom-color:#334155}}
.tab-content{{display:none}}
.tab-content.active{{display:block}}
.cam-wrap{{position:relative;margin-top:.5em}}
.cam-wrap canvas{{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none}}
.cam-wrap img{{width:100%;border-radius:4px;border:1px solid #334155;display:block}}
.det-controls{{display:flex;gap:.4em;align-items:center;flex-wrap:wrap;margin-top:.4em}}
.det-controls label{{display:inline;margin:0;font-size:.78em}}
.det-controls input[type=range]{{width:80px;padding:0}}
.det-controls select{{width:auto;padding:.15em .3em;font-size:.78em}}
.timing{{font-size:.75em;color:#64748b;margin-top:.2em}}
</style></head><body>
<h1>{hostname}</h1>
<div class="sub">SlyLED Camera Node v{VERSION} &middot; {ip}</div>
<div class="tabs">
<div class="tab active" onclick="_tab(0)">Dashboard</div>
<div class="tab" onclick="_tab(1)">Settings</div>
</div>
<div id="t0" class="tab-content active">
<div class="card">
<h2>Status</h2>
<div class="info-row"><span class="lbl">Board</span><span>{board}</span></div>
<div class="info-row"><span class="lbl">Cameras</span><span>{len(_hw_info.get("cameras", []))}</span></div>
<div class="info-row"><span class="lbl">Firmware</span><span>v{VERSION}</span></div>
<div class="info-row"><span class="lbl">Uptime</span><span>{uptime}s</span></div>
</div>
{''.join(f"""<div class="card">
<h2>Camera {i} &mdash; {c["name"]}</h2>
<div class="info-row"><span class="lbl">Device</span><span>{c["device"]}</span></div>
<div class="info-row"><span class="lbl">Resolution</span><span>{c["resW"]}x{c["resH"]}</span></div>
<div class="info-row"><span class="lbl">FOV</span><span>{_camera_fov(i)}&deg;</span></div>
<button class="btn btn-save" onclick="_snap({i})" id="snap-btn-{i}">Capture Frame</button>
<button class="btn btn-save" onclick="_detect({i})" id="det-btn-{i}" style="{det_btn_style}">Detect Objects</button>
<div class="det-controls" id="det-ctl-{i}" style="display:none">
<label>Threshold</label><input type="range" min="10" max="90" value="50" id="det-thr-{i}" oninput="document.getElementById('det-thr-v-{i}').textContent=this.value/100"><span id="det-thr-v-{i}" style="font-size:.78em;min-width:2em">0.5</span>
<label>Size</label><select id="det-res-{i}"><option value="320">320</option><option value="640">640</option></select>
<label><input type="checkbox" id="det-auto-{i}" onchange="_autoToggle({i})"> Auto</label>
</div>
<div id="cam-msg-{i}" style="color:#94a3b8;font-size:.82em;margin-top:.3em"></div>
<div class="cam-wrap" id="cam-wrap-{i}" style="display:none">
<img id="cam-img-{i}">
<canvas id="cam-cvs-{i}"></canvas>
</div>
<div class="timing" id="cam-time-{i}"></div>
</div>""" for i, c in enumerate(_hw_info.get("cameras", [])))
if _hw_info.get("cameras") else '<div class="card"><h2>Camera</h2><p style="color:#fca5a5;font-size:.82em">No cameras detected on this device.</p></div>'}
</div>
<div id="t1" class="tab-content">
<div class="card">
<h2>Device Settings</h2>
<label>Node Name</label>
<input id="cfg-name" value="{hostname}" maxlength="32">
<div id="msg"></div>
<button class="btn btn-save" onclick="_save()">Save</button>
</div>
{''.join(f"""<div class="card" style="margin-top:.5em">
<h2>Camera {i} &mdash; {c["name"]}</h2>
<div class="info-row"><span class="lbl">Device</span><span>{c["device"]}</span></div>
<div class="info-row"><span class="lbl">Resolution</span><span>{c["resW"]}x{c["resH"]}</span></div>
<label>Custom Name</label>
<input id="cam-name-{i}" value="{_camera_cfg(i)['name']}" maxlength="32" placeholder="{c['name'][:20]}">
<div style="display:flex;gap:.5em;align-items:center;margin:.3em 0">
<div><label style="font-size:.82em">FOV (&deg;)</label><br><input type="number" id="cam-fov-{i}" value="{_camera_cfg(i)['fovDeg']}" min="1" max="180" style="width:55px"></div>
<div style="padding-top:1em"><button class="btn btn-reset" onclick="document.getElementById('cam-fov-{i}').value={c.get('detectedFov',0) or 60}" style="font-size:.65em;padding:.15em .4em">Reset ({c.get('detectedFov',0) or 60}&deg;)</button></div>
</div>
<label>Orientation</label>
<select id="cam-flip-{i}" style="margin-bottom:.3em">
<option value="none" {'selected' if _camera_cfg(i)['flip']=='none' else ''}>Normal</option>
<option value="h" {'selected' if _camera_cfg(i)['flip']=='h' else ''}>Flip Horizontal</option>
<option value="v" {'selected' if _camera_cfg(i)['flip']=='v' else ''}>Flip Vertical</option>
<option value="180" {'selected' if _camera_cfg(i)['flip']=='180' else ''}>Rotate 180&deg;</option>
</select>
<div style="display:flex;gap:1.5em;align-items:center;margin:.4em 0">
<label style="display:flex;align-items:center;gap:.3em"><input type="checkbox" id="cam-en-{i}" {'checked' if _camera_cfg(i)['enabled'] else ''}> Enabled</label>
<label style="display:flex;align-items:center;gap:.3em"><input type="radio" name="cam-pref" id="cam-pref-{i}" {'checked' if _camera_cfg(i)['preferred'] else ''}> Preferred</label>
</div>
<button class="btn btn-save" onclick="_saveCam({i})" style="margin-top:.3em">Save Camera {i}</button>
</div>""" for i, c in enumerate(_hw_info.get("cameras", [])))
if _hw_info.get("cameras") else '<div class="card" style="margin-top:.5em"><h2>Cameras</h2><p style="color:#fca5a5;font-size:.82em">No cameras detected.</p></div>'}
<div class="card" style="margin-top:.5em">
<h2>Device</h2>
<button class="btn btn-reboot" onclick="_reboot()">Reboot</button>
<button class="btn btn-reset" onclick="_reset()">Factory Reset</button>
</div>
</div>
<script>
var _autoTimers={{}};
function _tab(i){{
  document.querySelectorAll('.tab').forEach(function(t,j){{t.classList.toggle('active',j===i)}});
  document.querySelectorAll('.tab-content').forEach(function(t,j){{t.classList.toggle('active',j===i)}});
}}
function _save(){{
  var name=document.getElementById('cfg-name').value.trim();
  var x=new XMLHttpRequest();
  x.open('POST','/config');
  x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){{var r=JSON.parse(x.responseText);document.getElementById('msg').textContent=r.ok?'Saved':'Error: '+(r.err||'unknown');}};
  x.send(JSON.stringify({{hostname:name}}));
}}
function _showImg(idx,blob,dets){{
  var wrap=document.getElementById('cam-wrap-'+idx);
  var img=document.getElementById('cam-img-'+idx);
  var cvs=document.getElementById('cam-cvs-'+idx);
  wrap.style.display='block';
  var url=URL.createObjectURL(blob);
  img.onload=function(){{
    cvs.width=img.naturalWidth;cvs.height=img.naturalHeight;
    var ctx=cvs.getContext('2d');ctx.clearRect(0,0,cvs.width,cvs.height);
    if(!dets||!dets.length)return;
    ctx.lineWidth=2;ctx.font='bold 14px sans-serif';ctx.textBaseline='bottom';
    var colors=['#22d3ee','#a78bfa','#f472b6','#34d399','#fbbf24','#fb923c','#f87171'];
    var labels={{}};var ci=0;
    for(var d=0;d<dets.length;d++){{
      var det=dets[d];
      if(!(det.label in labels))labels[det.label]=colors[ci++%colors.length];
      var c=labels[det.label];
      ctx.strokeStyle=c;ctx.strokeRect(det.x,det.y,det.w,det.h);
      var txt=det.label+' '+Math.round(det.confidence*100)+'%';
      var tw=ctx.measureText(txt).width;
      ctx.fillStyle=c;ctx.globalAlpha=0.7;
      ctx.fillRect(det.x,det.y-18,tw+6,18);
      ctx.globalAlpha=1;ctx.fillStyle='#000';
      ctx.fillText(txt,det.x+3,det.y-3);
    }}
  }};
  img.src=url;
}}
function _snap(idx){{
  var btn=document.getElementById('snap-btn-'+idx);
  var msg=document.getElementById('cam-msg-'+idx);
  var time=document.getElementById('cam-time-'+idx);
  btn.disabled=true;btn.textContent='Capturing...';msg.textContent='';time.textContent='';
  var t0=performance.now();
  var x=new XMLHttpRequest();
  x.open('GET','/snapshot?cam='+idx);x.responseType='blob';
  x.onload=function(){{
    btn.disabled=false;btn.textContent='Capture Frame';
    if(x.status===200&&x.response&&x.response.size>0){{
      _showImg(idx,x.response,null);
      msg.textContent='Captured at '+new Date().toLocaleTimeString();msg.style.color='#94a3b8';
      time.textContent='Round-trip: '+Math.round(performance.now()-t0)+'ms';
    }}else{{
      msg.style.color='#fca5a5';
      if(x.response&&x.response.size>0){{
        var reader=new FileReader();reader.onload=function(){{
          try{{msg.textContent='Error: '+JSON.parse(reader.result).err;}}
          catch(e){{msg.textContent='Capture failed ('+x.status+')';}}
        }};reader.readAsText(x.response);
      }}else msg.textContent='Capture failed (empty response)';
    }}
  }};
  x.onerror=function(){{btn.disabled=false;btn.textContent='Capture Frame';msg.textContent='Connection failed';msg.style.color='#fca5a5';}};
  x.send();
}}
function _detect(idx){{
  var btn=document.getElementById('det-btn-'+idx);
  var msg=document.getElementById('cam-msg-'+idx);
  var time=document.getElementById('cam-time-'+idx);
  var ctl=document.getElementById('det-ctl-'+idx);
  ctl.style.display='flex';
  btn.disabled=true;btn.textContent='Detecting...';msg.textContent='';time.textContent='';
  var thr=document.getElementById('det-thr-'+idx).value/100;
  var res=document.getElementById('det-res-'+idx).value;
  var x=new XMLHttpRequest();
  x.open('POST','/scan');x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){{
    btn.disabled=false;btn.textContent='Detect Objects';
    try{{
      var r=JSON.parse(x.responseText);
      if(!r.ok){{msg.textContent='Error: '+(r.err||'unknown');msg.style.color='#fca5a5';return;}}
      time.textContent='Capture: '+r.captureMs+'ms | Inference: '+r.inferenceMs+'ms | '+r.detections.length+' object'+(r.detections.length!==1?'s':'');
      msg.textContent=r.detections.length?r.detections.map(function(d){{return d.label+' '+Math.round(d.confidence*100)+'%';}}).join(', '):'No objects detected';
      msg.style.color=r.detections.length?'#22d3ee':'#94a3b8';
      // Fetch snapshot to overlay boxes on
      var sx=new XMLHttpRequest();sx.open('GET','/snapshot?cam='+idx);sx.responseType='blob';
      sx.onload=function(){{if(sx.status===200)_showImg(idx,sx.response,r.detections);}};
      sx.send();
    }}catch(e){{msg.textContent='Parse error';msg.style.color='#fca5a5';}}
  }};
  x.onerror=function(){{btn.disabled=false;btn.textContent='Detect Objects';msg.textContent='Connection failed';msg.style.color='#fca5a5';}};
  x.send(JSON.stringify({{cam:idx,threshold:thr,resolution:parseInt(res)}}));
}}
function _autoToggle(idx){{
  var cb=document.getElementById('det-auto-'+idx);
  if(cb.checked){{
    _detect(idx);
    _autoTimers[idx]=setInterval(function(){{_detect(idx);}},3000);
  }}else{{
    if(_autoTimers[idx])clearInterval(_autoTimers[idx]);
    delete _autoTimers[idx];
  }}
}}
function _saveFov(idx){{
  var v=parseInt(document.getElementById('fov-'+idx).value)||60;
  if(v<1)v=1;if(v>180)v=180;
  document.getElementById('fov-'+idx).value=v;
  var x=new XMLHttpRequest();
  x.open('POST','/config');x.setRequestHeader('Content-Type','application/json');
  x.send(JSON.stringify({{cameraFov:{{[idx]:v}}}}));
}}
function _saveCam(idx){{
  var cfg={{}};
  cfg.name=(document.getElementById('cam-name-'+idx)||{{}}).value||'';
  cfg.fovDeg=parseInt((document.getElementById('cam-fov-'+idx)||{{}}).value)||60;
  cfg.flip=(document.getElementById('cam-flip-'+idx)||{{}}).value||'none';
  cfg.enabled=!!(document.getElementById('cam-en-'+idx)||{{}}).checked;
  cfg.preferred=!!(document.getElementById('cam-pref-'+idx)||{{}}).checked;
  var body={{cameraCfg:{{}}}};body.cameraCfg[idx]=cfg;
  var x=new XMLHttpRequest();
  x.open('POST','/config');x.setRequestHeader('Content-Type','application/json');
  x.onload=function(){{document.getElementById('msg').textContent='Camera '+idx+' saved';}};
  x.send(JSON.stringify(body));
}}
function _reboot(){{
  if(!confirm('Reboot camera node?'))return;
  var x=new XMLHttpRequest();x.open('POST','/reboot');x.send();
  document.getElementById('msg').textContent='Rebooting...';
}}
function _reset(){{
  if(!confirm('Factory reset? This will clear all settings.'))return;
  var x=new XMLHttpRequest();x.open('POST','/config/reset');x.send();
  document.getElementById('msg').textContent='Reset to factory defaults. Rebooting...';
}}
</script></body></html>'''

@app.get("/config/json")
def config_json():
    return jsonify(_config)

@app.post("/config")
def config_post():
    body = request.get_json(silent=True) or {}
    for k in ("hostname", "fovDeg", "cameraUrl", "resolutionW", "resolutionH"):
        if k in body:
            _config[k] = body[k]
    # Merge per-camera FOV (legacy)
    if "cameraFov" in body and isinstance(body["cameraFov"], dict):
        fov_map = _config.get("cameraFov", {})
        for idx, val in body["cameraFov"].items():
            v = int(val) if isinstance(val, (int, float, str)) else 60
            if 1 <= v <= 180:
                fov_map[str(idx)] = v
        _config["cameraFov"] = fov_map
    # Merge per-camera config (name, fov, enabled, flip, preferred)
    if "cameraCfg" in body and isinstance(body["cameraCfg"], dict):
        cfg_map = _config.get("cameraCfg", {})
        for idx, cam_cfg in body["cameraCfg"].items():
            if not isinstance(cam_cfg, dict):
                continue
            existing = cfg_map.get(str(idx), {})
            for k in ("name", "fovDeg", "enabled", "flip", "preferred"):
                if k in cam_cfg:
                    existing[k] = cam_cfg[k]
            cfg_map[str(idx)] = existing
        # If setting preferred, clear preferred on others
        for idx, cam_cfg in body["cameraCfg"].items():
            if cam_cfg.get("preferred"):
                for other_idx in cfg_map:
                    if other_idx != str(idx):
                        cfg_map[other_idx]["preferred"] = False
        _config["cameraCfg"] = cfg_map
    _save_config()
    return jsonify(ok=True, config=_config)

@app.post("/config/reset")
def config_reset():
    """Factory reset — clear config, reboot."""
    global _config
    _config = {"hostname": "", "fovDeg": 60, "cameraFov": {}, "cameraCfg": {},
               "cameraUrl": "", "resolutionW": 1920, "resolutionH": 1080}
    _save_config()
    threading.Timer(1, lambda: os.system("reboot")).start()
    return jsonify(ok=True, message="Factory reset. Rebooting...")

@app.post("/reboot")
def reboot():
    def _do_reboot():
        time.sleep(1)
        os.system("reboot")
    threading.Timer(1, _do_reboot).start()
    return jsonify(ok=True, message="Rebooting in 1 second...")

@app.get("/health")
def health():
    return "", 200

@app.get("/snapshot")
def snapshot():
    """Capture a single JPEG frame. ?cam=0 selects camera index (default 0)."""
    cameras = _hw_info.get("cameras", [])
    if not cameras:
        return jsonify(ok=False, err="No camera detected"), 404
    idx = request.args.get("cam", 0, type=int)
    if idx < 0 or idx >= len(cameras):
        return jsonify(ok=False, err=f"Camera index {idx} out of range (0-{len(cameras)-1})"), 400
    dev = cameras[idx]["device"]
    res = f"{cameras[idx].get('resW') or _config.get('resolutionW', 1920)}x{cameras[idx].get('resH') or _config.get('resolutionH', 1080)}"

    from flask import Response
    import subprocess

    # Try OpenCV first (fastest — direct numpy, no subprocess)
    try:
        import cv2
        frame = _cv_capture(dev)
        if frame is not None:
            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return Response(jpeg.tobytes(), mimetype="image/jpeg")
    except ImportError:
        pass

    # Fall back to CLI capture tools by absolute path (systemd PATH may be minimal)
    tools = [
        (["/usr/bin/fswebcam", "-d", dev, "--no-banner", "-r", res, "--jpeg", "85", "-"], "fswebcam"),
        (["/usr/bin/ffmpeg", "-y", "-f", "v4l2", "-i", dev,
          "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "pipe:1"], "ffmpeg"),
        (["/usr/bin/v4l2-ctl", "-d", dev, "--set-fmt-video=pixelformat=MJPG",
          "--stream-mmap", "--stream-count=1", "--stream-to=-"], "v4l2-ctl"),
    ]
    for cmd, name in tools:
        if not os.path.exists(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=10)
            if proc.returncode == 0 and proc.stdout:
                return Response(proc.stdout, mimetype="image/jpeg")
            log.warning("Capture with %s failed: exit %d, stderr: %s",
                        name, proc.returncode, proc.stderr[:200])
        except Exception as e:
            log.warning("Capture with %s error: %s", name, e)

    return jsonify(ok=False, err="No capture tool available (install fswebcam or ffmpeg)"), 500

# ── OpenCV capture helper ─────────────────────────────────────────────

def _cv_capture(device, timeout=5):
    """Capture a single BGR frame from a V4L2 USB camera."""

    # Standard V4L2 via OpenCV
    try:
        import cv2
        # Use V4L2 backend explicitly — GStreamer backend ignores resolution on many cameras
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not cap.isOpened():
            # Fallback to default backend
            cap = cv2.VideoCapture(device)
            if not cap.isOpened():
                return None
        # Set MJPEG format + 1080p — required for correct aspect ratio on USB cameras
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.read()
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            # Apply per-camera flip if configured
            cam_idx = None
            for i, c in enumerate(_hw_info.get("cameras", [])):
                if c.get("device") == device:
                    cam_idx = i; break
            if cam_idx is not None:
                flip = _camera_cfg(cam_idx).get("flip", "none")
                if flip == "h":
                    frame = cv2.flip(frame, 1)
                elif flip == "v":
                    frame = cv2.flip(frame, 0)
                elif flip == "180":
                    frame = cv2.flip(frame, -1)
            return frame
    except Exception as e:
        log.warning("OpenCV capture %s failed: %s", device, e)
    return None

# ── Object detection ──────────────────────────────────────────────────

_detector = None

def _get_detector():
    """Lazy-init the object detector."""
    global _detector
    if _detector is None:
        try:
            from detector import ObjectDetector
            _detector = ObjectDetector()
        except ImportError:
            log.warning("detector module not available (missing opencv/numpy?)")
            return None
    return _detector

@app.post("/scan")
def scan():
    """Single-frame object detection. Returns detections with bounding boxes."""
    cameras = _hw_info.get("cameras", [])
    if not cameras:
        return jsonify(ok=False, err="No camera detected"), 404

    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    threshold = body.get("threshold", 0.5)
    classes = body.get("classes", None)  # None = all classes
    resolution = body.get("resolution", 320)

    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err=f"Camera index {cam_idx} out of range (0-{len(cameras)-1})"), 400
    if resolution not in (320, 640):
        return jsonify(ok=False, err="resolution must be 320 or 640"), 400

    det = _get_detector()
    if det is None:
        return jsonify(ok=False, err="Object detection not available (install python3-opencv python3-numpy)"), 503

    dev = cameras[cam_idx]["device"]

    # Capture frame via OpenCV
    t0 = time.monotonic()
    frame = _cv_capture(dev)
    capture_ms = (time.monotonic() - t0) * 1000

    if frame is None:
        return jsonify(ok=False, err=f"Failed to capture from {dev}"), 503

    # Run detection
    try:
        detections, inference_ms = det.detect(frame, threshold=threshold,
                                               classes=classes, input_size=resolution)
    except Exception as e:
        log.error("Detection failed: %s", e)
        return jsonify(ok=False, err=str(e)), 500

    return jsonify(
        ok=True,
        detections=detections,
        captureMs=round(capture_ms),
        inferenceMs=round(inference_ms),
        resolution=resolution,
        camera=cam_idx,
        frameSize=[int(frame.shape[1]), int(frame.shape[0])],
    )

# ── Depth estimation ───────────────────────────────────────────────────

_depth_estimator = None

def _get_depth_estimator():
    global _depth_estimator
    if _depth_estimator is None:
        try:
            from depth_estimator import DepthEstimator
            _depth_estimator = DepthEstimator()
        except ImportError:
            log.warning("depth_estimator module not available")
            return None
    return _depth_estimator

@app.post("/depth-map")
def depth_map():
    """Estimate depth map from a camera. Returns depth stats + optional 3D points."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    est = _get_depth_estimator()
    if est is None:
        return jsonify(ok=False, err="Depth estimator not available"), 503
    dev = cameras[cam_idx]["device"]
    frame = _cv_capture(dev)
    if frame is None:
        return jsonify(ok=False, err="Capture failed"), 503
    depth, ms = est.estimate(frame)
    h, w = depth.shape[:2]
    # Return depth stats + optional sample points
    points = body.get("points", [])  # [{px, py}] to project to 3D
    fov = _camera_fov(cam_idx)
    results_3d = []
    for pt in points[:20]:  # max 20 points
        x, y, z = est.pixel_to_3d(depth, pt["px"], pt["py"], fov, w, h)
        results_3d.append({"px": pt["px"], "py": pt["py"], "x": x, "y": y, "z": z})
    return jsonify(
        ok=True,
        inferenceMs=round(ms),
        width=w, height=h,
        depthMin=round(float(depth.min()), 3),
        depthMax=round(float(depth.max()), 3),
        depthMean=round(float(depth.mean()), 3),
        points3d=results_3d,
        camera=cam_idx,
    )

@app.post("/point-cloud")
def point_cloud():
    """Generate a 3D point cloud from depth estimation. Returns downsampled [x,y,z,r,g,b] array."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    max_points = body.get("maxPoints", 10000)
    max_depth = body.get("maxDepthMm", 5000)
    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    est = _get_depth_estimator()
    if est is None:
        return jsonify(ok=False, err="Depth estimator not available"), 503
    dev = cameras[cam_idx]["device"]
    frame = _cv_capture(dev)
    if frame is None:
        return jsonify(ok=False, err="Capture failed"), 503
    fov = _camera_fov(cam_idx)
    # Load intrinsic calibration if available (#244)
    intrinsics = None
    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    if cal_path.exists():
        try:
            intrinsics = json.loads(cal_path.read_text())
        except Exception:
            pass
    points, ms = est.generate_point_cloud(frame, fov, max_points=max_points,
                                           max_depth_mm=max_depth, intrinsics=intrinsics)
    return jsonify(ok=True, points=points, pointCount=len(points),
                   inferenceMs=round(ms), camera=cam_idx, fovDeg=fov,
                   calibrated=intrinsics is not None)

# ── Beam detection (for calibration) ───────────────────────────────────

_beam_detector = None

def _get_beam_detector():
    global _beam_detector
    if _beam_detector is None:
        try:
            from beam_detector import BeamDetector
            _beam_detector = BeamDetector()
        except ImportError:
            log.warning("beam_detector module not available")
            return None
    return _beam_detector

@app.post("/dark-reference")
def dark_reference():
    """Capture dark reference frame for beam detection."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", -1)  # -1 = all cameras
    det = _get_beam_detector()
    if det is None:
        return jsonify(ok=False, err="Beam detector not available"), 503
    cameras = _hw_info.get("cameras", [])
    captured = []
    cams_to_capture = list(range(len(cameras))) if cam_idx == -1 else [cam_idx]
    for ci in cams_to_capture:
        if ci < 0 or ci >= len(cameras):
            continue
        frame = _cv_capture(cameras[ci]["device"])
        if frame is not None:
            import cv2
            det.set_dark_frame(ci, frame)
            captured.append(ci)
    return jsonify(ok=True, cameras=captured)

@app.post("/beam-detect")
def beam_detect():
    """Detect a bright beam spot. Fast (<100ms), color-filtered."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    color = body.get("color", None)
    threshold = body.get("threshold", 30)
    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    det = _get_beam_detector()
    if det is None:
        return jsonify(ok=False, err="Beam detector not available"), 503
    frame = _cv_capture(cameras[cam_idx]["device"])
    if frame is None:
        return jsonify(ok=False, err="Capture failed"), 503
    result = det.detect(frame, cam_idx=cam_idx, color=color, threshold=threshold)
    return jsonify(ok=True, **result)

@app.post("/beam-detect/flash")
def beam_detect_flash():
    """Flash detection: caller must turn light ON before calling, then this endpoint
    captures the ON frame, waits for the caller to turn light OFF (via 'offDelay' ms),
    captures the OFF frame, and diffs them. Immune to ambient shifts.

    Body: {cam, color, threshold, offDelayMs (default 500)}
    The caller should turn the light OFF offDelayMs after making this request.
    OR: pass frameOff as base64 JPEG if already captured."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    color = body.get("color", None)
    threshold = body.get("threshold", 30)
    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    det = _get_beam_detector()
    if det is None:
        return jsonify(ok=False, err="Beam detector not available"), 503
    dev = cameras[cam_idx]["device"]
    # Capture ON frame (beam should be on now)
    frame_on = _cv_capture(dev)
    if frame_on is None:
        return jsonify(ok=False, err="ON frame capture failed"), 503
    # Wait for caller to turn light off, then capture OFF frame
    off_delay = body.get("offDelayMs", 500) / 1000.0
    import time as _time
    _time.sleep(off_delay)
    frame_off = _cv_capture(dev)
    if frame_off is None:
        return jsonify(ok=False, err="OFF frame capture failed"), 503
    result = det.detect_flash(frame_on, frame_off, color=color, threshold=threshold)
    return jsonify(ok=True, **result)

@app.post("/beam-detect/center")
def beam_detect_center():
    """Detect the center beam of a multi-beam fixture."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    color = body.get("color", None)
    threshold = body.get("threshold", 30)
    beam_count = body.get("beamCount", 3)
    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400
    det = _get_beam_detector()
    if det is None:
        return jsonify(ok=False, err="Beam detector not available"), 503
    frame = _cv_capture(cameras[cam_idx]["device"])
    if frame is None:
        return jsonify(ok=False, err="Capture failed"), 503
    result = det.detect_center(frame, cam_idx=cam_idx, color=color,
                                threshold=threshold, beam_count=beam_count)
    return jsonify(ok=True, **result)

# ── Intrinsic calibration (checkerboard) ──────────────────────────────

CALIB_DIR = Path("/opt/slyled/calibration")
CALIB_DIR.mkdir(parents=True, exist_ok=True)
_calib_frames = {}  # cam_idx → list of (corners, img_shape)

CHECKER_ROWS = 6  # inner corners (7x10 squares → 6x9 inner corners)
CHECKER_COLS = 9
CHECKER_SIZE = 25.0  # mm per square (when printed at 100%)

# ArUco markers for stage-distance calibration
ARUCO_DICT_ID = 0  # cv2.aruco.DICT_4X4_50
ARUCO_MARKER_SIZE = 150.0  # mm — size of printed marker (letter paper ~180mm usable)


@app.post("/calibrate/intrinsic/capture")
def intrinsic_capture():
    """Capture checkerboard from ALL cameras simultaneously.
    Body: {cam: N} for single camera, or omit for all cameras at once.
    Returns: {ok, cameras: [{cam, found, corners, frameCount}]}"""
    body = request.get_json(silent=True) or {}
    single_cam = body.get("cam", None)
    cameras = _hw_info.get("cameras", [])

    import cv2
    cam_indices = [single_cam] if single_cam is not None else list(range(len(cameras)))
    results = []

    for cam_idx in cam_indices:
        if cam_idx >= len(cameras):
            results.append({"cam": cam_idx, "found": False, "err": "invalid index"})
            continue
        frame = _cv_capture(cameras[cam_idx]["device"])
        if frame is None:
            results.append({"cam": cam_idx, "found": False, "err": "capture failed"})
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        import numpy as np
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
        search_img = gray.copy()

        # Find ALL checkerboards in the frame (mask each found one, search again)
        boards_found = 0
        if cam_idx not in _calib_frames:
            _calib_frames[cam_idx] = []

        for attempt in range(10):  # max 10 boards per frame
            ret, corners = cv2.findChessboardCorners(
                search_img, (CHECKER_COLS, CHECKER_ROWS), flags)
            if not ret:
                break
            # Refine to sub-pixel
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            _calib_frames[cam_idx].append((corners, gray.shape[::-1]))
            boards_found += 1
            # Mask out found board region so next search finds a different one
            pts = corners.reshape(-1, 2).astype(np.int32)
            margin = 15
            x_min = max(0, pts[:, 0].min() - margin)
            x_max = min(search_img.shape[1], pts[:, 0].max() + margin)
            y_min = max(0, pts[:, 1].min() - margin)
            y_max = min(search_img.shape[0], pts[:, 1].max() + margin)
            search_img[y_min:y_max, x_min:x_max] = 128  # neutral gray mask

        count = len(_calib_frames[cam_idx])
        if boards_found > 0:
            log.info("Intrinsic calibration cam%d: found %d boards (%d total frames)",
                     cam_idx, boards_found, count)
            results.append({"cam": cam_idx, "found": True, "boardsInFrame": boards_found,
                            "corners": CHECKER_ROWS * CHECKER_COLS, "frameCount": count})
        else:
            results.append({"cam": cam_idx, "found": False,
                            "frameCount": count})

    return jsonify(ok=True, cameras=results)


@app.post("/calibrate/intrinsic/compute")
def intrinsic_compute():
    """Compute intrinsic calibration from captured checkerboard frames.
    Body: {cam: 0, squareSize: 25}
    Returns: {ok, rmsError, fx, fy, cx, cy, distCoeffs, frameCount}"""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    sq_size = body.get("squareSize", CHECKER_SIZE)

    frames = _calib_frames.get(cam_idx, [])
    if len(frames) < 3:
        return jsonify(ok=False, err=f"Need at least 3 frames, have {len(frames)}")

    import cv2
    import numpy as np

    # 3D object points (same for all frames)
    objp = np.zeros((CHECKER_ROWS * CHECKER_COLS, 3), np.float32)
    objp[:, :2] = np.mgrid[0:CHECKER_COLS, 0:CHECKER_ROWS].T.reshape(-1, 2) * sq_size

    obj_points = [objp] * len(frames)
    img_points = [f[0] for f in frames]
    img_size = frames[0][1]  # (w, h)

    ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, img_size, None, None)

    if not ret:
        return jsonify(ok=False, err="Calibration failed")

    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    dist_list = dist.flatten().tolist()

    # Save to disk
    cal_data = {
        "cam": cam_idx,
        "rmsError": round(float(ret), 4),
        "fx": round(fx, 2), "fy": round(fy, 2),
        "cx": round(cx, 2), "cy": round(cy, 2),
        "distCoeffs": [round(d, 6) for d in dist_list],
        "imageSize": list(img_size),
        "frameCount": len(frames),
        "squareSize": sq_size,
        "timestamp": time.time(),
    }
    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    cal_path.write_text(json.dumps(cal_data, indent=2))
    log.info("Intrinsic calibration cam%d: RMS=%.4f fx=%.1f fy=%.1f cx=%.1f cy=%.1f (%d frames)",
             cam_idx, ret, fx, fy, cx, cy, len(frames))

    # Clear captured frames
    _calib_frames.pop(cam_idx, None)

    return jsonify(ok=True, **cal_data)


@app.get("/calibrate/intrinsic")
def intrinsic_get():
    """Get intrinsic calibration for a camera.
    Query: ?cam=0
    Returns calibration data or {calibrated: false}"""
    cam_idx = int(request.args.get("cam", 0))
    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    if not cal_path.exists():
        return jsonify(calibrated=False)
    try:
        cal = json.loads(cal_path.read_text())
        return jsonify(calibrated=True, **cal)
    except Exception:
        return jsonify(calibrated=False)


@app.delete("/calibrate/intrinsic")
def intrinsic_delete():
    """Delete intrinsic calibration for a camera. Body: {cam: 0}"""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    if cal_path.exists():
        cal_path.unlink()
    _calib_frames.pop(cam_idx, None)
    return jsonify(ok=True)


@app.post("/calibrate/intrinsic/reset")
def intrinsic_reset():
    """Reset captured frames without deleting saved calibration. Body: {cam: 0}"""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    _calib_frames.pop(cam_idx, None)
    return jsonify(ok=True, frameCount=0)


# ── ArUco marker calibration (stage-distance friendly) ────────────────

_aruco_frames = {}  # cam_idx → list of (corners, ids, img_shape)


@app.get("/calibrate/aruco/generate")
def aruco_generate():
    """Generate printable ArUco markers as SVG. Returns 6 markers for letter paper.
    Query: ?count=6&size=150"""
    import cv2
    count = int(request.args.get("count", 6))
    size_mm = int(request.args.get("size", ARUCO_MARKER_SIZE))
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    markers = []
    for i in range(count):
        img = cv2.aruco.drawMarker(aruco_dict, i, 200)  # 200px image
        # Convert to SVG path (black squares)
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{size_mm}mm" height="{size_mm}mm" viewBox="0 0 200 200">'
        svg += '<rect width="200" height="200" fill="white"/>'
        for y in range(img.shape[0]):
            for x in range(img.shape[1]):
                if img[y, x] == 0:
                    svg += f'<rect x="{x}" y="{y}" width="1" height="1" fill="black"/>'
        svg += f'<text x="100" y="215" text-anchor="middle" font-size="12" fill="#666">ID {i} — {size_mm}mm — SlyLED</text>'
        svg += '</svg>'
        markers.append({"id": i, "svg": svg})
    return jsonify(ok=True, markers=markers, count=count, sizeMm=size_mm)


@app.post("/calibrate/aruco/capture")
def aruco_capture():
    """Capture ArUco markers from all cameras. Each marker gives 4 corners.
    Body: {cam: N} for single, omit for all. {markerSize: 150} optional.
    Returns: {ok, cameras: [{cam, markersFound, ids, frameCount}]}"""
    body = request.get_json(silent=True) or {}
    single_cam = body.get("cam", None)
    marker_size = body.get("markerSize", ARUCO_MARKER_SIZE)
    cameras = _hw_info.get("cameras", [])

    import cv2
    import numpy as np
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    params = cv2.aruco.DetectorParameters_create()
    cam_indices = [single_cam] if single_cam is not None else list(range(len(cameras)))
    results = []

    for cam_idx in cam_indices:
        if cam_idx >= len(cameras):
            results.append({"cam": cam_idx, "markersFound": 0, "err": "invalid"})
            continue
        frame = _cv_capture(cameras[cam_idx]["device"])
        if frame is None:
            results.append({"cam": cam_idx, "markersFound": 0, "err": "capture failed"})
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, rejected = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=params)

        if ids is None or len(ids) == 0:
            results.append({"cam": cam_idx, "markersFound": 0,
                            "frameCount": len(_aruco_frames.get(cam_idx, []))})
            continue

        if cam_idx not in _aruco_frames:
            _aruco_frames[cam_idx] = []
        _aruco_frames[cam_idx].append((corners, ids, gray.shape[::-1]))

        count = len(_aruco_frames[cam_idx])
        found_ids = ids.flatten().tolist()
        log.info("ArUco calibration cam%d: found %d markers (ids=%s, total=%d frames)",
                 cam_idx, len(ids), found_ids, count)
        results.append({"cam": cam_idx, "markersFound": len(ids), "ids": found_ids,
                        "frameCount": count})

    return jsonify(ok=True, cameras=results)


@app.post("/calibrate/aruco/compute")
def aruco_compute():
    """Compute intrinsic calibration from ArUco marker detections.
    Body: {cam: 0, markerSize: 150}"""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    marker_size = body.get("markerSize", ARUCO_MARKER_SIZE)

    frames = _aruco_frames.get(cam_idx, [])
    if len(frames) < 3:
        return jsonify(ok=False, err=f"Need at least 3 frames, have {len(frames)}")

    import cv2
    import numpy as np
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)

    # Collect all corners and IDs across frames
    all_corners = []
    all_ids = []
    img_size = frames[0][2]
    for corners, ids, _ in frames:
        all_corners.extend(corners)
        all_ids.extend(ids)

    if len(all_corners) < 4:
        return jsonify(ok=False, err=f"Need at least 4 marker detections, have {len(all_corners)}")

    # Use ArUco calibration (CharucoBoard not needed — direct marker calibration)
    # Each marker gives 4 object points at known size
    half = marker_size / 2.0
    obj_points_per_marker = np.array([
        [-half, half, 0], [half, half, 0],
        [half, -half, 0], [-half, -half, 0]
    ], dtype=np.float32)

    obj_pts = []
    img_pts = []
    for corners, ids, _ in frames:
        for i in range(len(ids)):
            obj_pts.append(obj_points_per_marker)
            img_pts.append(corners[i].reshape(4, 2))

    obj_pts = np.array(obj_pts, dtype=np.float32)
    img_pts = np.array(img_pts, dtype=np.float32)

    # calibrateCamera wants list-of-arrays
    obj_list = [obj_pts[i] for i in range(len(obj_pts))]
    img_list = [img_pts[i] for i in range(len(img_pts))]

    ret, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj_list, img_list, img_size, None, None)

    if not ret:
        return jsonify(ok=False, err="Calibration failed")

    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    dist_list = dist.flatten().tolist()

    cal_data = {
        "cam": cam_idx, "method": "aruco",
        "rmsError": round(float(ret), 4),
        "fx": round(fx, 2), "fy": round(fy, 2),
        "cx": round(cx, 2), "cy": round(cy, 2),
        "distCoeffs": [round(d, 6) for d in dist_list],
        "imageSize": list(img_size),
        "frameCount": len(frames),
        "markerSize": marker_size,
        "totalDetections": len(obj_list),
        "timestamp": time.time(),
    }
    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    cal_path.write_text(json.dumps(cal_data, indent=2))
    log.info("ArUco calibration cam%d: RMS=%.4f fx=%.1f fy=%.1f (%d markers, %d frames)",
             cam_idx, ret, fx, fy, len(obj_list), len(frames))

    _aruco_frames.pop(cam_idx, None)
    return jsonify(ok=True, **cal_data)


@app.post("/calibrate/aruco/reset")
def aruco_reset():
    """Reset ArUco captured frames. Body: {cam: 0}"""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    _aruco_frames.pop(cam_idx, None)
    return jsonify(ok=True, frameCount=0)


# ── ArUco Stage Mapping — pixel ↔ real stage coordinates ─────────────
#
# ArUco markers placed at KNOWN stage positions (measured by user) allow
# building a homography that maps camera pixels to real stage mm.
#
# This is the foundation for single-coordinate-system calibration:
#   - Fixtures positioned in stage mm (from layout)
#   - Camera sees beam at pixel (u,v) → homography → stage (X, Y=0, Z)
#   - Compare expected vs actual → compute orientation correction
#
# The homography maps the FLOOR PLANE (Y=0) only. For 3D points above
# the floor, additional depth info is needed (from ArUco marker distance
# or monocular depth scaled by the known marker sizes).
#
# Coordinate system (matches layout 3D view):
#   X = stage width  (stage right=0 → stage left)
#   Y = height       (floor=0 → ceiling) — always 0 for floor mapping
#   Z = depth        (back wall=0 → audience)

@app.post("/calibrate/stage-map")
def stage_map():
    """Build pixel→stage floor mapping from ArUco markers at known positions.

    The user places ArUco markers on the stage floor and provides their
    real-world stage coordinates. The camera detects the markers and
    computes a homography matrix that maps any pixel on the floor plane
    to real stage (X, Z) coordinates in mm.

    Body: {
        cam: 0,
        markers: {
            "0": {"x": 0, "z": 0},         // marker ID → stage position (floor, Y=0)
            "1": {"x": 3000, "z": 0},
            "2": {"x": 1500, "z": 1500},
            ...
        },
        markerSize: 150   // physical marker size in mm (for distance estimation)
    }

    Requires at least 4 markers with known positions for a reliable homography.

    Returns: {
        ok: true,
        markersDetected: N,
        markersMatched: N,       // how many detected markers have known positions
        homography: [...],       // 3x3 matrix (flattened) for pixel→stage
        rmsError: float,         // reprojection error in mm
        markerDistances: {...},  // real distance to each marker (from marker size + fx)
        depthScale: float,       // multiplier to convert relative depth → real mm
    }
    """
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    known_markers = body.get("markers", {})
    marker_size_mm = body.get("markerSize", ARUCO_MARKER_SIZE)

    if len(known_markers) < 4:
        return jsonify(ok=False, err=f"Need at least 4 markers with known positions, got {len(known_markers)}")

    cameras = _hw_info.get("cameras", [])
    if cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400

    import cv2
    import numpy as np

    # ── Step 1: Capture frame and detect all ArUco markers ──────────
    frame = _cv_capture(cameras[cam_idx]["device"])
    if frame is None:
        return jsonify(ok=False, err="Capture failed"), 503

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    params = cv2.aruco.DetectorParameters_create()
    corners, ids, rejected = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=params)

    if ids is None or len(ids) == 0:
        return jsonify(ok=False, err="No ArUco markers detected in frame")

    detected_ids = ids.flatten().tolist()
    log.info("Stage map cam%d: detected markers %s", cam_idx, detected_ids)

    # ── Step 2: Match detected markers with known stage positions ───
    # For each detected marker that has a known stage position,
    # collect the pixel center and the stage (X, Z) coordinate.
    pixel_points = []   # pixel coordinates of marker centers
    stage_points = []   # corresponding stage (X, Z) coordinates in mm

    for i, marker_id in enumerate(detected_ids):
        str_id = str(marker_id)
        if str_id not in known_markers:
            continue
        # Marker center pixel = average of 4 corners
        cx = float(np.mean(corners[i][0][:, 0]))
        cy = float(np.mean(corners[i][0][:, 1]))
        # Stage position from user input
        stage_x = known_markers[str_id]["x"]
        stage_z = known_markers[str_id]["z"]
        pixel_points.append([cx, cy])
        stage_points.append([stage_x, stage_z])

    matched = len(pixel_points)
    log.info("Stage map cam%d: %d/%d markers matched with known positions",
             cam_idx, matched, len(detected_ids))

    if matched < 4:
        return jsonify(ok=False,
                       err=f"Only {matched} markers matched (need 4+). Detected IDs: {detected_ids}",
                       markersDetected=len(detected_ids), markersMatched=matched)

    # ── Step 3: Compute homography — pixel (u,v) → stage (X, Z) ────
    # This is a 2D-to-2D mapping on the floor plane.
    # We use findHomography which finds a 3x3 matrix H such that:
    #   [X, Z, 1]^T ~ H * [u, v, 1]^T
    # After applying H, divide by the third component (homogeneous divide).

    src = np.array(pixel_points, dtype=np.float32)    # pixel coords
    dst = np.array(stage_points, dtype=np.float32)    # stage (X, Z) coords
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)

    if H is None:
        return jsonify(ok=False, err="Homography computation failed")

    # ── Step 4: Compute reprojection error ──────────────────────────
    # Transform pixel points through H and compare with known stage positions.
    errors = []
    for i in range(matched):
        px = np.array([pixel_points[i][0], pixel_points[i][1], 1.0])
        mapped = H @ px
        mapped = mapped / mapped[2]  # homogeneous divide
        ex = mapped[0] - stage_points[i][0]
        ez = mapped[1] - stage_points[i][1]
        errors.append(math.sqrt(ex * ex + ez * ez))
    rms_error = (sum(e * e for e in errors) / len(errors)) ** 0.5

    # ── Step 5: Compute real distance to each marker ────────────────
    # Using calibrated focal length + known marker size:
    #   real_distance = (marker_size_mm * fx) / apparent_pixel_size
    # This gives us a depth scale factor for monocular depth.

    cal_path = CALIB_DIR / f"intrinsic_cam{cam_idx}.json"
    fx = None
    if cal_path.exists():
        try:
            cal = json.loads(cal_path.read_text())
            fx = cal.get("fx")
        except Exception:
            pass
    if not fx:
        # Estimate from FOV
        fov = _camera_fov(cam_idx)
        w = frame.shape[1]
        fx = (w / 2) / math.tan(math.radians(fov / 2))

    marker_distances = {}
    for i, marker_id in enumerate(detected_ids):
        # Apparent size = distance between first two corners in pixels
        c = corners[i][0]
        side_px = float(np.linalg.norm(c[0] - c[1]))
        if side_px > 1:
            real_dist = (marker_size_mm * fx) / side_px
            marker_distances[str(marker_id)] = round(real_dist, 1)

    # Compute depth scale: ratio of real distances to monocular depth values
    # (This will be used later to scale the point cloud to real mm)
    depth_scale = None
    if marker_distances:
        avg_real = sum(marker_distances.values()) / len(marker_distances)
        # For now just store the average distance — full scaling needs
        # matching with monocular depth values at the same pixels
        depth_scale = round(avg_real, 1)

    # ── Step 6: Save the stage map ──────────────────────────────────
    stage_map_data = {
        "cam": cam_idx,
        "homography": H.flatten().tolist(),
        "rmsError": round(rms_error, 2),
        "markersDetected": len(detected_ids),
        "markersMatched": matched,
        "markerPositions": known_markers,
        "markerDistances": marker_distances,
        "depthScale": depth_scale,
        "imageSize": [frame.shape[1], frame.shape[0]],
        "timestamp": time.time(),
    }
    map_path = CALIB_DIR / f"stage_map_cam{cam_idx}.json"
    map_path.write_text(json.dumps(stage_map_data, indent=2))

    log.info("Stage map cam%d: H computed from %d markers, RMS=%.1fmm, distances=%s",
             cam_idx, matched, rms_error, marker_distances)

    return jsonify(ok=True, **stage_map_data)


@app.post("/calibrate/pixel-to-stage")
def pixel_to_stage():
    """Convert a camera pixel coordinate to real stage position on the floor.

    Uses the saved stage map homography. Only valid for points on the
    floor plane (Y=0 in stage coordinates).

    Body: {cam: 0, pixelX: 960, pixelY: 700}
    Returns: {ok, stageX, stageZ, stageY: 0}
    """
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    px = body.get("pixelX", 0)
    py = body.get("pixelY", 0)

    map_path = CALIB_DIR / f"stage_map_cam{cam_idx}.json"
    if not map_path.exists():
        return jsonify(ok=False, err="No stage map — run /calibrate/stage-map first")

    import numpy as np
    stage_map = json.loads(map_path.read_text())
    H = np.array(stage_map["homography"]).reshape(3, 3)

    # Apply homography: pixel → stage floor
    p = np.array([px, py, 1.0])
    mapped = H @ p
    if abs(mapped[2]) < 1e-9:
        return jsonify(ok=False, err="Degenerate mapping at this pixel")
    stage_x = float(mapped[0] / mapped[2])
    stage_z = float(mapped[1] / mapped[2])

    return jsonify(ok=True, stageX=round(stage_x, 1), stageY=0, stageZ=round(stage_z, 1))


@app.get("/calibrate/stage-map")
def stage_map_get():
    """Get saved stage map for a camera. Query: ?cam=0"""
    cam_idx = int(request.args.get("cam", 0))
    map_path = CALIB_DIR / f"stage_map_cam{cam_idx}.json"
    if not map_path.exists():
        return jsonify(calibrated=False)
    try:
        data = json.loads(map_path.read_text())
        return jsonify(calibrated=True, **data)
    except Exception:
        return jsonify(calibrated=False)


# ── Tracking mode ──────────────────────────────────────────────────────

_tracker = None

def _get_tracker():
    """Get or create the tracker instance."""
    global _tracker
    if _tracker is not None:
        return _tracker
    det = _get_detector()
    if det is None:
        return None
    try:
        from tracker import Tracker
        _tracker = Tracker(detector=det, capture_fn=_cv_capture)
        return _tracker
    except ImportError:
        log.warning("tracker module not available")
        return None

@app.post("/track/start")
def track_start():
    """Start continuous tracking on a camera."""
    body = request.get_json(silent=True) or {}
    cam_idx = body.get("cam", 0)
    orch_url = body.get("orchestratorUrl", "")
    camera_id = body.get("cameraId", 0)
    fps = body.get("fps", 2)
    threshold = body.get("threshold", 0.4)
    ttl = body.get("ttl", 5)

    cameras = _hw_info.get("cameras", [])
    if cam_idx < 0 or cam_idx >= len(cameras):
        return jsonify(ok=False, err="Invalid camera index"), 400

    tracker = _get_tracker()
    if tracker is None:
        return jsonify(ok=False, err="Tracker not available"), 503

    if tracker.running:
        return jsonify(ok=True, message="Already tracking")

    tracker.start(cameras[cam_idx]["device"], orch_url=orch_url,
                  camera_id=camera_id, fps=fps, threshold=threshold, ttl=ttl)
    return jsonify(ok=True, message="Tracking started")

@app.post("/track/stop")
def track_stop():
    """Stop continuous tracking."""
    tracker = _get_tracker()
    if tracker and tracker.running:
        tracker.stop()
    return jsonify(ok=True, message="Tracking stopped")

@app.get("/track/status")
def track_status():
    """Get current tracking state."""
    tracker = _get_tracker()
    return jsonify(
        running=tracker.running if tracker else False,
        trackCount=tracker.track_count if tracker else 0,
    )

# ── UDP protocol (PING/PONG + STATUS) ─────────────────────────────────

def _udp_header(cmd, epoch=0):
    return struct.pack("<HBBI", UDP_MAGIC, UDP_VERSION, cmd, epoch)

def _build_pong():
    """Build 134-byte PongPayload matching Protocol.h struct."""
    full_name = _config.get("hostname") or socket.gethostname()
    hostname = full_name[:10]   # hostname[10] — protocol limit
    alt_name = full_name[:16]   # altName[16] — display name
    desc = "Camera node"[:32]
    # Pack hostname[10] + altName[16] + description[32] + stringCount(1)
    payload = hostname.encode("ascii", "replace").ljust(10, b"\x00")
    payload += alt_name.encode("ascii", "replace").ljust(16, b"\x00")
    payload += desc.encode("ascii", "replace").ljust(32, b"\x00")
    payload += struct.pack("B", len(_hw_info.get("cameras", [])))  # stringCount = camera count
    # 8 x PongString (9 bytes each) = 72 bytes, all zeros
    payload += b"\x00" * 72
    # fwMajor, fwMinor, fwPatch
    parts = VERSION.split(".")
    payload += struct.pack("BBB", int(parts[0]), int(parts[1]),
                           int(parts[2]) if len(parts) > 2 else 0)
    return _udp_header(CMD_PONG) + payload

def _build_status_resp():
    """Build STATUS_RESP: 8 bytes (activeAction, runnerActive, currentStep, rssi, uptime)."""
    rssi = 0
    try:
        # Read WiFi RSSI from /proc on Linux
        with open("/proc/net/wireless", "r") as f:
            for line in f:
                if "wlan" in line:
                    parts = line.split()
                    rssi = abs(int(float(parts[3])))
                    break
    except Exception:
        pass
    uptime = int(time.monotonic())
    return _udp_header(CMD_STATUS_RESP) + struct.pack("<BBBBI",
        0, 0, 0, rssi, uptime)

def _udp_listener():
    """Background thread: listen for PING and STATUS_REQ on UDP_PORT."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", UDP_PORT))
        print(f"[UDP] Listening on port {UDP_PORT}")
    except Exception as e:
        print(f"[UDP] Could not bind port {UDP_PORT}: {e}")
        return
    while True:
        try:
            data, addr = s.recvfrom(512)
            if len(data) < 8:
                continue
            magic, ver, cmd = struct.unpack_from("<HBB", data, 0)
            if magic != UDP_MAGIC:
                continue
            if cmd == CMD_PING:
                s.sendto(_build_pong(), (addr[0], UDP_PORT))
            elif cmd == CMD_STATUS_REQ:
                s.sendto(_build_status_resp(), (addr[0], UDP_PORT))
        except Exception:
            pass

# ── Main ────────────────────────────────────────────────────────────────

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("slyled-cam")

if __name__ == "__main__":
    import sys
    log.info("SlyLED Camera Node v%s starting (Python %s)", VERSION, sys.version.split()[0])
    _load_config()
    log.info("Config loaded")
    try:
        _detect_hardware()
        cams = _hw_info.get("cameras", [])
        log.info("Board: %s | Cameras: %d", _hw_info.get("board", "unknown"), len(cams))
        for c in cams:
            log.info("  %s: %s (%dx%d)", c["device"], c["name"], c["resW"], c["resH"])
    except Exception as e:
        log.error("Hardware detection failed: %s", e)
    _save_config()
    try:
        _register_mdns()
    except Exception as e:
        log.warning("mDNS failed: %s", e)
    atexit.register(_unregister_mdns)
    threading.Thread(target=_udp_listener, daemon=True).start()
    log.info("Listening on HTTP :%d, UDP :%d", PORT, UDP_PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
