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
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

VERSION = "1.0.0"
PORT = 5000
CONFIG_DIR = Path("/opt/slyled")
CONFIG_PATH = CONFIG_DIR / "camera.json"

app = Flask(__name__)

_config = {}
_hw_info = {}

# ── Config persistence ──────────────────────────────────────────────────

def _load_config():
    global _config
    defaults = {
        "hostname": "",
        "fovDeg": 60,
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

def _save_config():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(_config, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to save config: {e}")

# ── Hardware detection ──────────────────────────────────────────────────

def _detect_hardware():
    global _hw_info
    info = {"board": "unknown", "hasCamera": False, "tracking": False}

    # Board identification
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text().strip().rstrip("\x00")
            info["board"] = model
    except Exception:
        pass

    # Camera device detection
    info["hasCamera"] = os.path.exists("/dev/video0")

    # Resolution detection via v4l2
    if info["hasCamera"]:
        try:
            import subprocess
            r = subprocess.run(
                ["v4l2-ctl", "--list-formats-ext", "-d", "/dev/video0"],
                capture_output=True, text=True, timeout=5
            )
            # Parse highest resolution from output
            max_w, max_h = 0, 0
            for line in r.stdout.splitlines():
                line = line.strip()
                if "Size:" in line:
                    # Format: "Size: Discrete 1920x1080"
                    parts = line.split()
                    for p in parts:
                        if "x" in p and p[0].isdigit():
                            try:
                                w, h = p.split("x")
                                w, h = int(w), int(h)
                                if w * h > max_w * max_h:
                                    max_w, max_h = w, h
                            except ValueError:
                                pass
            if max_w > 0:
                _config["resolutionW"] = max_w
                _config["resolutionH"] = max_h
        except Exception:
            pass

    # Hostname
    hostname = socket.gethostname()
    if not _config.get("hostname"):
        _config["hostname"] = hostname
    info["hostname"] = _config["hostname"] or hostname

    _hw_info = info

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
    return jsonify({
        "role": "camera",
        "hostname": _config.get("hostname") or socket.gethostname(),
        "fwVersion": VERSION,
        "fovDeg": _config.get("fovDeg", 60),
        "resolutionW": _config.get("resolutionW", 1920),
        "resolutionH": _config.get("resolutionH", 1080),
        "cameraUrl": _config.get("cameraUrl", ""),
        "capabilities": {
            "tracking": _hw_info.get("tracking", False),
            "hasCamera": _hw_info.get("hasCamera", False),
        },
        "board": _hw_info.get("board", "unknown"),
    })

@app.get("/config")
def config_get():
    return jsonify(_config)

@app.post("/config")
def config_post():
    body = request.get_json(silent=True) or {}
    for k in ("hostname", "fovDeg", "cameraUrl", "resolutionW", "resolutionH"):
        if k in body:
            _config[k] = body[k]
    _save_config()
    return jsonify(ok=True, config=_config)

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

# ── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"SlyLED Camera Node v{VERSION}")
    _load_config()
    _detect_hardware()
    print(f"  Board: {_hw_info.get('board', 'unknown')}")
    print(f"  Camera: {'found' if _hw_info.get('hasCamera') else 'not found'}")
    print(f"  Resolution: {_config.get('resolutionW')}x{_config.get('resolutionH')}")
    _save_config()
    _register_mdns()
    atexit.register(_unregister_mdns)
    app.run(host="0.0.0.0", port=PORT, debug=False)
