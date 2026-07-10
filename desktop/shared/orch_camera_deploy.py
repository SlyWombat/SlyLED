"""orch_camera_deploy — camera-node network scan, SSH deploy, SSH settings (B1).

Mechanically extracted from parent_server.py ("Camera network scan",
"Camera deploy via SSH+SCP", "Camera SSH settings" sub-sections of the
Fixtures section). Route paths, names, and behaviour are byte-identical;
only the decorator target changed (@app → @bp) and parent_server-owned
state (_children, _fixtures, _layout, _lock, _save, _ssh, _camera_ssh,
_FW_DIR, _FW_CACHE_DIR, DATA, …) is reached through the orch_state
bridge (ps.*) so test monkeypatching on parent_server keeps working.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

import orch_state

ps = orch_state.ps  # the live parent_server module (bound before this import)
assert ps is not None, "orch_state.bind() must run before importing orch_camera_deploy"

bp = Blueprint("camera_deploy", __name__)

# ── Camera network scan (SSH port scan for fresh SBCs) ──────────────────

_ssh_scan_state = {"pending": False, "data": []}

def _scan_ssh_devices():
    """TCP connect scan for port 22 on all local subnets. Returns SSH-accessible hosts."""
    import concurrent.futures
    try:
        local_ip = ps._get_local_ip()
    except Exception:
        local_ip = "192.168.1.1"
    skip_ips = {local_ip}
    for c in ps._children:
        skip_ips.add(c.get("ip", ""))

    def _check(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            if s.connect_ex((ip, 22)) == 0:
                s.close()
                cam_info = ps._probe_camera(ip, timeout=0.5)
                return {"ip": ip, "hasCamera": cam_info is not None,
                        "hostname": (cam_info or {}).get("hostname", ""),
                        "fwVersion": (cam_info or {}).get("fwVersion", "")}
            s.close()
        except Exception:
            pass
        return None

    ips = []
    for prefix in ps._local_subnet_prefixes():
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

@bp.get("/api/cameras/scan-network")
def api_cameras_scan_network():
    if _ssh_scan_state["pending"]:
        return jsonify(pending=True)
    _ssh_scan_state["pending"] = True
    _ssh_scan_state["data"] = []
    threading.Thread(target=_ssh_scan_bg, daemon=True).start()
    return jsonify(pending=True)

@bp.get("/api/cameras/scan-network/results")
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
    """Extract the VERSION literal from camera_server.py source text."""
    import re
    m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None

def _camera_local_version():
    """Read VERSION from the local (bundled) camera_server.py source."""
    for base in [Path(getattr(sys, '_MEIPASS', '')) / "firmware" / "orangepi",
                 ps._FW_DIR / "orangepi"]:
        p = base / "camera_server.py"
        if p.exists():
            try:
                v = _parse_version_from_text(p.read_text(encoding="utf-8"))
                if v:
                    return v
            except Exception:
                pass
    return None

def _camera_download_candidate_dirs():
    """Every directory the camera deploy might pick up a downloaded
    camera_server.py from, in no particular order. Both the dedicated
    /api/firmware/camera/download path (ps.DATA/firmware/camera/) and the
    Firmware-Library /api/firmware/fetch path (ps._FW_CACHE_DIR/orangepi/)
    extract here, so a Library download must feed the camera deploy too."""
    return [
        ps.DATA / "firmware" / "camera",
        ps._FW_CACHE_DIR / "orangepi",
    ]

def _camera_downloaded_version():
    """Highest VERSION found in any download candidate directory, or None."""
    best = None
    best_t = None
    for d in _camera_download_candidate_dirs():
        p = d / "camera_server.py"
        if not p.exists():
            continue
        try:
            v = _parse_version_from_text(p.read_text(encoding="utf-8"))
        except Exception:
            v = None
        if not v:
            continue
        try:
            vt = tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            vt = (0,)
        if best_t is None or vt > best_t:
            best, best_t = v, vt
    return best

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
    Prefers whichever download candidate has the newest camera_server.py;
    falls back to the bundled tree when no download is newer."""
    loc_ver = _camera_local_version()
    best_dir = None
    best_t = None
    for d in _camera_download_candidate_dirs():
        p = d / "camera_server.py"
        if not p.exists():
            continue
        try:
            v = _parse_version_from_text(p.read_text(encoding="utf-8"))
        except Exception:
            v = None
        if not v:
            continue
        try:
            vt = tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            vt = (0,)
        if best_t is None or vt > best_t:
            best_dir, best_t = d, vt
    if best_dir is None:
        return ps._FW_DIR / "orangepi"
    if loc_ver:
        try:
            loc_t = tuple(int(x) for x in loc_ver.split("."))
            if loc_t > best_t:
                return ps._FW_DIR / "orangepi"
        except (ValueError, AttributeError):
            pass
    return best_dir

@bp.get("/api/firmware/camera/check")
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
                ps.log.info("GitHub camera firmware: v%s", latest)
        except Exception as e:
            ps.log.debug("GitHub camera check failed: %s", e)
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

@bp.post("/api/firmware/camera/download")
def api_firmware_camera_download():
    """Download all camera firmware files from GitHub main branch."""
    import urllib.request as _ur
    dest = ps.DATA / "firmware" / "camera"
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
            ps.log.warning("Failed to download %s: %s", fname, e)
            errors.append(f"{fname}: {e}")
    # Parse version from downloaded camera_server.py
    ver = _camera_downloaded_version()
    if ver:
        _github_camera_cache["version"] = ver
        _github_camera_cache["ts"] = time.time()
    ps.log.info("Downloaded %d camera firmware files (v%s)", len(downloaded), ver)
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
        remote_info = ps._probe_camera(ip, timeout=3)
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
        ps.log.info("Deploy: uid=%s, sudo=%s", uid, "no" if not sudo else "yes")

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
        ps.log.info("Deploy: using firmware from %s", src_dir)
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
                m_src = ps._FW_DIR / "orangepi" / "models" / model_name
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
            ps.log.warning("apt-get partial failure (continuing): %s", err)

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
            ps.log.warning("yolov8n.onnx not on device (not bundled locally?) — scan will be unavailable")

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
            info = ps._probe_camera(ip, timeout=5)
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

@bp.post("/api/cameras/deploy")
def api_cameras_deploy():
    with _deploy_lock:
        if _deploy_status["running"]:
            return jsonify(err="Deploy already in progress"), 409
    body = request.get_json(silent=True) or {}
    ip = body.get("ip", "").strip()
    force = body.get("force", False)
    if not ip:
        return jsonify(err="ip required"), 400
    if not ps._ssh.get("sshPassword") and not ps._ssh.get("sshKeyPath"):
        return jsonify(err="SSH credentials not configured"), 400
    with _deploy_lock:
        _deploy_status.update(running=True, progress=0, message="Starting...",
                              error=None, ip=ip, remoteVersion=None,
                              localVersion=None)
    threading.Thread(target=_deploy_camera_bg, args=(ip, force), daemon=True).start()
    return jsonify(ok=True, pending=True)

@bp.get("/api/cameras/deploy/status")
def api_cameras_deploy_status():
    with _deploy_lock:
        return jsonify(dict(_deploy_status))

# ── Camera SSH settings ─────────────────────────────────────────────────

@bp.get("/api/cameras/ssh")
def api_cameras_ssh_get():
    key_path = ps._ssh.get("sshKeyPath", "")
    key_exists = bool(key_path and Path(os.path.expanduser(key_path)).exists())
    return jsonify({
        "sshUser": ps._ssh.get("sshUser", "root"),
        "hasPassword": bool(ps._ssh.get("sshPassword")),
        "sshKeyPath": key_path,
        "hasKey": key_exists,
    })

@bp.post("/api/cameras/ssh/generate-key")
def api_cameras_ssh_generate_key():
    """Generate an Ed25519 SSH key pair for camera deployments."""
    try:
        import paramiko
    except ImportError:
        return jsonify(err="paramiko not installed"), 500
    key_dir = ps.DATA / "ssh"
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

    with ps._lock:
        ps._ssh["sshKeyPath"] = str(key_file)
        ps._save("ssh", ps._ssh)

    return jsonify(ok=True, publicKey=pub_str, keyPath=str(key_file))

@bp.post("/api/cameras/ssh")
def api_cameras_ssh_save():
    body = request.get_json(silent=True) or {}
    with ps._lock:
        if "sshUser" in body:
            ps._ssh["sshUser"] = body["sshUser"]
        if "sshPassword" in body:
            ps._ssh["sshPassword"] = ps._encrypt_pw(body["sshPassword"])
        if "sshKeyPath" in body:
            ps._ssh["sshKeyPath"] = body["sshKeyPath"]
        if "sshKeyContent" in body:
            # Save pasted key content to a managed file
            key_dir = ps.DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            key_file = key_dir / "camera_key"
            key_file.write_text(body["sshKeyContent"])
            key_file.chmod(0o600)
            ps._ssh["sshKeyPath"] = str(key_file)
        ps._save("ssh", ps._ssh)
    return jsonify(ok=True)

# -- Per-camera-node SSH config (#311) -------------------------------------------
# SSH credentials keyed by camera node IP (not per sensor/fixture).
# Multiple sensors on the same Orange Pi share one SSH config.

@bp.get("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_get(ip):
    """Get SSH config for a camera hardware node by IP (password masked)."""
    ssh = ps._camera_ssh.get(ip, {})
    return jsonify({
        "ip": ip,
        "authType": ssh.get("authType", "password"),
        "user": ssh.get("user", ps._ssh.get("sshUser", "root")),
        "hasPassword": bool(ssh.get("password")),
        "keyPath": ssh.get("keyPath", ""),
        "keyStored": ssh.get("keyStored", False),
        "configured": bool(ssh),
    })


@bp.post("/api/cameras/node/<path:ip>/ssh")
def api_camera_node_ssh_save(ip):
    """Save SSH config for a camera hardware node."""
    body = request.get_json(silent=True) or {}
    with ps._lock:
        ssh = ps._camera_ssh.get(ip, {})
        if "authType" in body:
            ssh["authType"] = body["authType"]
        if "user" in body:
            ssh["user"] = body["user"]
        if "password" in body:
            ssh["password"] = ps._encrypt_pw(body["password"]) if body["password"] else ""
        if "keyPath" in body:
            ssh["keyPath"] = body["keyPath"]
            ssh["keyStored"] = False
        if "keyContent" in body and body["keyContent"]:
            key_dir = ps.DATA / "ssh"
            key_dir.mkdir(parents=True, exist_ok=True)
            safe_ip = ip.replace(".", "_")
            key_file = key_dir / f"cam_node_{safe_ip}_key"
            key_file.write_text(body["keyContent"])
            key_file.chmod(0o600)
            ssh["keyPath"] = str(key_file)
            ssh["keyStored"] = True
        ps._camera_ssh[ip] = ssh
        ps._save("camera_ssh", ps._camera_ssh)
    return jsonify(ok=True)


@bp.post("/api/cameras/node/<path:ip>/ssh/test")
def api_camera_node_ssh_test(ip):
    """Test SSH connection to a camera node.

    Accepts optional ``user`` / ``password`` / ``keyPath`` in the body —
    when present, those override the saved per-node config so the SPA
    can test unsaved form values without committing them. Falls through
    to the saved config when fields are omitted. #690-followup.
    """
    body = request.get_json(silent=True) or {}
    saved = _get_node_ssh(ip)
    user = body.get("user") if body.get("user") else saved["user"]
    password = body["password"] if "password" in body else saved.get("password", "")
    key_path = body.get("keyPath") if body.get("keyPath") else saved.get("keyPath", "")
    try:
        import paramiko
    except ImportError:
        return jsonify(ok=False, err="paramiko not installed — run: pip install paramiko")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {"hostname": ip, "port": 22, "username": user, "timeout": 8,
                  "look_for_keys": False, "allow_agent": False}
        if key_path and Path(os.path.expanduser(key_path)).exists():
            kwargs["key_filename"] = os.path.expanduser(key_path)
        elif password:
            kwargs["password"] = password
        else:
            return jsonify(ok=False, err="No password or key configured.",
                           guidance="Enter a password or provide an SSH key path, then retry.")
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
    """Get SSH credentials for a camera node by IP, falling back to global ps._ssh."""
    ssh = ps._camera_ssh.get(ip, {})
    if ssh.get("password") or ssh.get("keyPath"):
        pw = ""
        if ssh.get("password"):
            try:
                pw = ps._decrypt_pw(ssh["password"])
            except Exception:
                pw = ""
        return {
            "user": ssh.get("user", "root"),
            "password": pw,
            "keyPath": ssh.get("keyPath", ""),
        }
    # Fall back to global SSH config
    pw = ""
    if ps._ssh.get("sshPassword"):
        try:
            pw = ps._decrypt_pw(ps._ssh["sshPassword"])
        except Exception:
            pw = ""
    return {
        "user": ps._ssh.get("sshUser", "root"),
        "password": pw,
        "keyPath": ps._ssh.get("sshKeyPath", ""),
    }
