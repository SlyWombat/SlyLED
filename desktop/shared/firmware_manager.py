"""
SlyLED Firmware Manager — board detection, version reading, and flashing.

Supports ESP32, ESP8266 (D1 Mini), and Arduino Giga R1 WiFi.
Uses esptool for ESP boards (bundled via PyInstaller).
Extensible via registry.json for new board types and firmware variants.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

# ── Board detection by USB VID:PID ────────────────────────────────────────────

KNOWN_BOARDS = {
    # VID:PID → board candidates (list for ambiguous chips)
    "10C4:EA60": [{"board": "esp32", "chip": "CP2102", "name": "ESP32 (CP2102)"}],
    "1A86:7523": [
        {"board": "d1mini", "chip": "CH340", "name": "D1 Mini (CH340)"},
        {"board": "esp32",  "chip": "CH340", "name": "ESP32 (CH340)"},
    ],
    "1A86:55D4": [{"board": "d1mini", "chip": "CH9102", "name": "D1 Mini (CH9102)"}],
    "0403:6001": [{"board": "esp32", "chip": "FT232", "name": "ESP32 (FTDI)"}],
    "2341:0266": [{"board": "giga", "chip": "native", "name": "Arduino Giga R1 WiFi"}],
    "2341:0366": [{"board": "giga", "chip": "DFU", "name": "Giga R1 (DFU bootloader)"}],
    # Waveshare ESP32-S3 LCD 1.28 — native USB (303A:1001) or USB-UART (303A:0002)
    "303A:1001": [{"board": "esp32s3", "chip": "ESP32-S3", "name": "Waveshare ESP32-S3 LCD 1.28 (native USB)"}],
    "303A:0002": [{"board": "esp32s3", "chip": "ESP32-S3", "name": "Waveshare ESP32-S3 LCD 1.28 (USB-UART)"}],
    # WCH CH343 USB-UART variants seen on Waveshare ESP32-S3 batches.
    # 55D3 confirmed live 2026-04-30 with a SLYG-FC98 gyro (#761).
    "1A86:55D3": [{"board": "esp32s3", "chip": "CH343", "name": "Waveshare ESP32-S3 LCD 1.28 (CH343)"}],
}

FQBN_MAP = {
    "esp32":   "esp32:esp32:esp32",
    "d1mini":  "esp8266:esp8266:d1_mini",
    "giga":    "arduino:mbed_giga:giga",
    "esp32s3": "esp32:esp32:esp32s3",
}

# ── Port listing ──────────────────────────────────────────────────────────────

def list_ports():
    """List COM ports with detected board type."""
    try:
        import serial.tools.list_ports
    except ImportError:
        return []
    result = []
    for p in serial.tools.list_ports.comports():
        vid_pid = f"{p.vid:04X}:{p.pid:04X}" if p.vid and p.pid else None
        candidates = KNOWN_BOARDS.get(vid_pid, []) if vid_pid else []
        result.append({
            "port": p.device,
            "description": p.description or "",
            "vid_pid": vid_pid,
            "candidates": candidates,
            "board": candidates[0]["board"] if len(candidates) == 1 else None,
            "boardName": candidates[0]["name"] if len(candidates) == 1 else (
                "Unknown" if not candidates else "Multiple (" + "/".join(c["name"] for c in candidates) + ")"
            ),
        })
    return result

# ── Chip detection for ambiguous ports ────────────────────────────────────────

def detect_chip(port):
    """Use esptool to identify the chip on a port (ESP32 vs ESP8266)."""
    try:
        import esptool
        # esptool.main() captures output; run as subprocess instead
        r = subprocess.run(
            [sys.executable, "-m", "esptool", "--port", port, "chip_id"],
            capture_output=True, text=True, timeout=10
        )
        out = r.stdout + r.stderr
        if "ESP32-S3" in out:
            return "esp32s3"
        elif "ESP32" in out:
            return "esp32"
        elif "ESP8266" in out:
            return "d1mini"
    except Exception:
        pass
    return None

# ── Serial version + board query ───────────────────────────────────────────────

def query_serial(port, timeout=3.0):
    """Query a board via serial for VERSION, BOARD, and WIFIHASH.
    Returns {"version": "4.9", "board": "d1mini", "wifiHash": "A1B2C3D4"} or None."""
    import time as _time
    try:
        import serial
        with serial.Serial(port, 115200, timeout=1) as s:
            _time.sleep(0.3)
            s.reset_input_buffer()
            # Send all queries
            s.write(b"VERSION\n")
            s.flush()
            _time.sleep(0.1)
            s.write(b"BOARD\n")
            s.flush()
            _time.sleep(0.1)
            s.write(b"WIFIHASH\n")
            s.flush()
            version = None
            board = None
            wifi_hash = None
            deadline = _time.time() + timeout
            while _time.time() < deadline:
                line = s.readline().decode("ascii", "replace").strip()
                if line.startswith("SLYLED:"):
                    version = line[7:]
                elif line.startswith("BOARD:"):
                    board = line[6:]
                elif line.startswith("WIFIHASH:"):
                    wifi_hash = line[9:]
                if version and board and wifi_hash:
                    break
            if version:
                return {"version": version, "board": board, "wifiHash": wifi_hash}
    except Exception:
        pass
    return None

# ── Camera-node version query (#203) ──────────────────────────────────────────

def query_camera_node(ip, timeout=3.0):
    """#203 — query a camera-node SBC over HTTP for its firmware version
    and identity. Replaces the per-board serial path (camera nodes don't
    have a USB serial console). Returns {"version": "1.6.0",
    "hostname": "RPi-Sly1", "board": "camera", "role": "camera"} or None.
    """
    try:
        import urllib.request
        req = urllib.request.Request(f"http://{ip}:5000/status",
                                     method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("role") != "camera":
            return None
        return {
            "version":  data.get("fwVersion") or data.get("version"),
            "hostname": data.get("hostname") or "",
            "board":    "camera",
            "role":     "camera",
        }
    except Exception:
        return None


def push_camera_node(ip, registry_entry, cache_dir, registry_dir,
                      ssh_user="orangepi", ssh_port=22, ssh_key=None,
                      progress_cb=None):
    """#203 — push a camera-node firmware bundle to an SBC over SSH+SCP.
    The bundle is the .zip referenced by `registry_entry['releaseAsset']`;
    we extract locally then SCP each member to /opt/slyled/. Restarts
    the slyled-cam systemd service after upload."""
    import paramiko, zipfile, tempfile
    bundle = resolve_binary_path(registry_entry, cache_dir, registry_dir)
    if not bundle or not Path(bundle).exists():
        raise RuntimeError(f"firmware bundle not found: {bundle}")
    extract_to = Path(tempfile.mkdtemp(prefix="slyled-cam-"))
    try:
        with zipfile.ZipFile(bundle, "r") as zf:
            zf.extractall(extract_to)

        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {"username": ssh_user, "port": ssh_port, "timeout": 10}
        if ssh_key:
            kwargs["key_filename"] = ssh_key
        cli.connect(ip, **kwargs)
        try:
            sftp = cli.open_sftp()
            try:
                files = [p for p in extract_to.rglob("*") if p.is_file()]
                total = max(1, len(files))
                for i, src in enumerate(files):
                    rel = src.relative_to(extract_to).as_posix()
                    dst = f"/opt/slyled/{rel}"
                    # Make sure parent dir exists
                    parts = rel.split("/")
                    cur = "/opt/slyled"
                    for p in parts[:-1]:
                        cur = f"{cur}/{p}"
                        try: sftp.mkdir(cur)
                        except IOError: pass
                    sftp.put(str(src), dst)
                    if progress_cb:
                        progress_cb(int(((i + 1) / total) * 90),
                                    f"Uploaded {rel}")
            finally:
                sftp.close()
            # Restart the service so the new code takes effect.
            if progress_cb:
                progress_cb(95, "Restarting slyled-cam.service")
            cli.exec_command("sudo systemctl restart slyled-cam.service")
        finally:
            cli.close()
        if progress_cb:
            progress_cb(100, f"Camera node v{registry_entry.get('version')} deployed")
        return True
    finally:
        try:
            import shutil
            shutil.rmtree(extract_to, ignore_errors=True)
        except Exception:
            pass


# ── Registry ──────────────────────────────────────────────────────────────────

def load_registry(firmware_dir):
    """Load firmware/registry.json."""
    p = Path(firmware_dir) / "registry.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {"firmware": []}


def _registry_fetch_assets(timeout=10, release_tag=None):
    """Fetch a GitHub release's asset list. Returns ``{name: url}`` or None.

    #768 — when ``release_tag`` is given, fetch ``/releases/tags/<tag>`` so
    each registry entry resolves to the per-firmware release that owns its
    pinned version. Without an explicit tag we fall back to the app's
    ``/releases/latest`` (legacy callers); registry-driven downloads must
    pass ``release_tag`` so they never accidentally read the app release.
    """
    import urllib.request
    if release_tag:
        url = ("https://api.github.com/repos/SlyWombat/SlyLED/releases/tags/"
               + urllib.parse.quote(str(release_tag), safe=""))
    else:
        url = "https://api.github.com/repos/SlyWombat/SlyLED/releases/latest"
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github.v3+json",
                          "User-Agent": "SlyLED-Parent"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return {a["name"]: a.get("browser_download_url", "")
            for a in (data.get("assets") or [])}


def _verify_sha256(path, expected):
    """Return True when SHA-256 of `path` matches `expected` (case-insensitive).
    Streams in 64 KB chunks to keep memory bounded on large binaries."""
    if not expected:
        return True  # no hash pinned — caller decided to skip verification
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest().lower() == expected.strip().lower()


def download_firmware(entry, cache_dir, assets_by_name=None):
    """Download a single registry entry's binary into cache_dir / entry['file'].

    Returns the local path on success, None on failure. Caller is expected
    to check `os.path.isfile(path)` first — a missing or partial local file
    means a re-download is needed. `assets_by_name` can be pre-fetched once
    for a batch download (#567 refresh-all) so we only call GitHub once.

    Security (#568 review):
      - If the registry pins a `sha256`, the downloaded bytes are verified
        and the file is deleted on mismatch — we refuse to leave a
        potentially tampered binary on disk that a subsequent flash would
        push straight to hardware.
      - Entries that omit `sha256` fall back to the pre-verification
        behaviour (log a warning via debug channel — upgrading paths not
        yet pinned is the responsibility of the release workflow).

    #768 — release-tag resolution: each registry entry should pin a
    ``releaseTag`` (e.g. ``"gyro-v1.2.1"``) so we fetch from the right
    per-firmware release, not the app's ``releases/latest``. When ``entry``
    sets ``releaseTag``, we ignore any pre-fetched ``assets_by_name`` (it
    was almost certainly fetched for the wrong release) and re-fetch
    against the entry's tag. Entries without ``releaseTag`` keep the
    legacy app-release fallback for backwards compat — but they should
    be migrated.
    """
    import urllib.request
    fname = entry.get("file")
    asset_name = entry.get("releaseAsset") or os.path.basename(fname or "")
    if not fname or not asset_name:
        return None
    dest = Path(cache_dir) / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    release_tag = entry.get("releaseTag")
    if release_tag:
        # Registry pinned a tag — always resolve against THAT release, never
        # whatever happens to be /releases/latest. Pre-fetched assets from a
        # different (likely app) release are irrelevant here.
        per_release = _registry_fetch_assets(release_tag=release_tag) or {}
        url = per_release.get(asset_name)
    else:
        if assets_by_name is None:
            assets_by_name = _registry_fetch_assets() or {}
        url = assets_by_name.get(asset_name)
    if not url:
        return None
    # Zip-bundle assets (e.g. camera firmware ships every .py + service file
    # in one archive) are downloaded to a sibling file alongside dest, then
    # extracted into dest.parent so each member lands at its expected path.
    is_zip = asset_name.lower().endswith(".zip")
    archive_path = dest.parent / asset_name if is_zip else dest
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SlyLED-Parent"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        archive_path.write_bytes(data)
    except Exception:
        return None
    expected = entry.get("sha256")
    if expected and not _verify_sha256(archive_path, expected):
        # Don't keep an archive that failed integrity check — a subsequent
        # flash/deploy would push this straight to hardware. Delete and bail.
        try:
            archive_path.unlink()
        except OSError:
            pass
        return None
    if is_zip:
        import zipfile
        try:
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(dest.parent)
        except (zipfile.BadZipFile, OSError):
            try:
                archive_path.unlink()
            except OSError:
                pass
            return None
        if not dest.is_file():
            # Archive didn't include the registered entry["file"] member.
            return None
    return str(dest)


def resolve_binary_path(entry, cache_dir, registry_dir, auto_download=True):
    """Resolve a registry entry's binary to a local file path.

    Cache-first (writable), then registry-dir (installer-bundled / dev tree),
    then — if auto_download is True — pull it from GitHub Releases into the
    cache. Returns the absolute path or None.

    When the registry pins a `sha256`, any on-disk candidate is verified
    before being returned. A mismatched cached file triggers a re-download;
    a mismatched bundled file is skipped (we can't delete the installer's
    copy on the user's machine safely).
    """
    fname = entry.get("file")
    if not fname:
        return None
    expected = entry.get("sha256")
    # Cache first — if a cached file exists but fails hash, delete it so
    # the download path below will try again with a fresh fetch.
    if cache_dir:
        cache_p = Path(cache_dir) / fname
        if cache_p.is_file():
            if _verify_sha256(cache_p, expected):
                return str(cache_p)
            try:
                cache_p.unlink()
            except OSError:
                pass
    # Bundled / dev-tree copy — only trust if hash matches (or no hash set).
    if registry_dir:
        bundle_p = Path(registry_dir) / fname
        if bundle_p.is_file() and _verify_sha256(bundle_p, expected):
            return str(bundle_p)
    if not auto_download:
        return None
    return download_firmware(entry, cache_dir)

# ── Flash ─────────────────────────────────────────────────────────────────────

_flash_status = {"running": False, "progress": 0, "message": "", "error": None}
_flash_lock = threading.Lock()

def get_flash_status():
    with _flash_lock:
        return dict(_flash_status)


def _query_version_quick(status_url, timeout=2.0):
    """Return device's reported `version` from a /status JSON endpoint, or
    None if the device is unreachable. Used as the pre-flash baseline for
    #761 §C-4 verification — failure to reach the device pre-flash is fine
    (a board in the bootloader has no HTTP server)."""
    if not status_url:
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(status_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data.get("version") or data.get("fwVersion") or data.get("fw")
    except Exception:
        return None


def _verify_post_flash_version(status_url, pre_version, timeout_s=12):
    """#761 §C-4 — poll the device's /status until it reports a `version`
    different from `pre_version`. Returns None on success, a human-readable
    error string on failure. Polls every 500ms up to `timeout_s`.

    A "different version" check (rather than "matches expected X.Y.Z")
    avoids tight coupling to whatever build metadata the firmware happens
    to expose and still proves a successful boot of new code. If
    `pre_version` is None (device was unreachable before the flash), we
    just confirm the device comes back online with any version."""
    if not status_url:
        return None
    import time as _time
    deadline = _time.time() + max(2.0, float(timeout_s))
    last_seen = None
    while _time.time() < deadline:
        v = _query_version_quick(status_url, timeout=1.5)
        if v is not None:
            last_seen = v
            if pre_version is None or v != pre_version:
                return None
        _time.sleep(0.5)
    if last_seen is None:
        return f"device did not respond at {status_url} within {int(timeout_s)}s of flash"
    return (f"device at {status_url} still reports version {last_seen!r} "
            f"after flash (was {pre_version!r}); board did not reboot with "
            f"new firmware")

_ESPTOOL_CHIP_FOR_BOARD = {
    "esp32":   "esp32",
    "esp32s3": "esp32s3",
    "d1mini":  "esp8266",
}

def _esptool_supports_chip(chip):
    """#761 §C-1 — return True iff the imported esptool can program `chip`.
    Old esptool (3.0) doesn't recognise production ESP32-S3 magic 0x09 and
    the silent-success failure mode in flash_esp was the root cause of the
    'Flash complete' lie. Refuse to start a flash we can't actually do."""
    try:
        import esptool
    except ImportError:
        return False
    ver = getattr(esptool, "__version__", "0.0")
    try:
        major = int(str(ver).split(".")[0])
    except (ValueError, IndexError):
        major = 0
    if chip == "esp32s3" and major < 4:
        return False
    return True


def flash_esp(port, bin_path, board="esp32", progress_cb=None,
              verify_status_url=None):
    """Flash an ESP32 or ESP8266 using esptool in-process (no subprocess needed).

    #761 §C — esp32s3 needs --chip esp32s3 + esptool>=4.0; flash failures must
    surface as `error != None` (esptool argparse can sys.exit() with a string
    or None which the previous mapping treated as success). When
    `verify_status_url` is provided, after a successful flash we poll the URL
    for ~10s and confirm `version` differs from the pre-flash baseline so
    "Flash complete" actually means the device rebooted with new code."""
    with _flash_lock:
        _flash_status.update(running=True, progress=0, message="Preparing esptool...", error=None)

    try:
        import esptool
    except ImportError:
        with _flash_lock:
            _flash_status.update(error="esptool not available in this build", message="Error", running=False)
        return False

    chip = _ESPTOOL_CHIP_FOR_BOARD.get(board)
    if chip and not _esptool_supports_chip(chip):
        ver = getattr(esptool, "__version__", "?")
        with _flash_lock:
            _flash_status.update(
                error=f"Installed esptool {ver} cannot program {chip}. Upgrade to esptool>=4.0.",
                message="Error", running=False)
        return False

    try:
        if not os.path.isfile(bin_path):
            with _flash_lock:
                _flash_status.update(error=f"Binary not found: {bin_path}", message="Error", running=False)
            return False

        with _flash_lock:
            _flash_status.update(progress=5, message=f"Connecting to {port}...")

        # #761 §C-4 — capture pre-flash version so we can prove the device
        # actually accepted the new firmware after the reset.
        pre_version = _query_version_quick(verify_status_url) if verify_status_url else None

        baud = "460800" if board == "d1mini" else "921600"
        # #761 §C-2 — pass --chip explicitly. Old esptool auto-detect fails on
        # production ESP32-S3 (chip magic 0x09); even on new esptool it adds
        # round-trip latency. Always specify the chip when we know it.
        args = []
        if chip:
            args += ["--chip", chip]
        args += ["--port", port, "--baud", baud,
                 "--before", "default_reset", "--after", "hard_reset",
                 "write_flash", "0x0", str(bin_path)]

        # Capture esptool output by redirecting stdout/stderr to a pipe
        import io
        captured = io.StringIO()
        exit_code = 0

        # esptool.main() manipulates sys.argv and calls sys.exit()
        # We override both and capture output via a background reader
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        # Use a tee writer that captures output AND updates progress
        class ProgressWriter:
            def __init__(self, status_lock, status_dict):
                self._lock = status_lock
                self._status = status_dict
                self._buf = []
            def write(self, text):
                captured.write(text)
                for line in text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    self._buf.append(line)
                    if "Connecting" in line:
                        with self._lock:
                            self._status.update(progress=10, message="Connecting to board...")
                    elif "Chip is" in line or "Detecting" in line:
                        with self._lock:
                            self._status.update(progress=15, message=line[:60])
                    elif "Erasing" in line or "Compressed" in line:
                        with self._lock:
                            self._status.update(progress=20, message="Erasing flash...")
                    elif "%" in line and ("Writing" in line or "wrote" in line.lower()):
                        try:
                            pct = int(line.split("(")[1].split("%")[0].strip())
                            scaled = 20 + int(pct * 0.75)
                            with self._lock:
                                self._status["progress"] = scaled
                                self._status["message"] = f"Writing... {pct}%"
                        except Exception:
                            pass
                    elif "Hash of data verified" in line:
                        with self._lock:
                            self._status.update(progress=98, message="Verifying hash...")
                    elif "Hard resetting" in line:
                        with self._lock:
                            self._status.update(progress=100, message="Resetting board...")
            def flush(self):
                pass
            def last_lines(self, n=5):
                return self._buf[-n:] if self._buf else []

        writer = ProgressWriter(_flash_lock, _flash_status)
        sys.argv = ["esptool"] + args
        sys.stdout = writer
        sys.stderr = writer
        flash_exception = None
        try:
            esptool.main()
        except SystemExit as e:
            # #761 §C-3 — esptool's argparse / fatal_error path calls
            # sys.exit() with the error message string, not an int. The old
            # mapping (`1 if e.code else 0`) treated an empty/None code as
            # success even when esptool aborted before touching flash.
            if e.code is None:
                exit_code = 0
            elif isinstance(e.code, int):
                exit_code = e.code
            else:
                # Non-int code = error message; preserve the text.
                exit_code = 1
                flash_exception = str(e.code)
        except Exception as e:
            exit_code = 1
            flash_exception = f"{type(e).__name__}: {e}"
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Even with exit_code == 0, scan captured output for the failure
        # signatures esptool emits before exiting cleanly (a few error paths
        # log "A fatal error occurred" then return). If we never saw a
        # successful "Hard resetting" / "Hash of data verified", treat it as
        # a flash failure regardless of exit code.
        captured_text = captured.getvalue()
        flash_visibly_succeeded = (
            "Hash of data verified" in captured_text
            or "Hard resetting" in captured_text
            or "Leaving..." in captured_text
        )
        flash_visibly_failed = (
            "A fatal error occurred" in captured_text
            or "Failed to autodetect" in captured_text
            or "Unexpected CHIP magic value" in captured_text
        )

        if exit_code != 0 or flash_visibly_failed or not flash_visibly_succeeded:
            detail = flash_exception or "\n".join(writer.last_lines(5)) or "esptool exited without writing flash"
            with _flash_lock:
                _flash_status.update(error=f"Flash failed: {detail}", message="Error")
            return False

        # #761 §C-4 — confirm the device actually accepted the new code by
        # re-querying its HTTP /status. If `version` is unchanged after the
        # reset, esptool's "Hard resetting" was a lie about a stale binary.
        if verify_status_url:
            with _flash_lock:
                _flash_status.update(progress=98, message="Verifying flash...")
            verify_err = _verify_post_flash_version(
                verify_status_url, pre_version, timeout_s=12)
            if verify_err:
                with _flash_lock:
                    _flash_status.update(error=f"Flash verify failed: {verify_err}",
                                          message="Error")
                return False

        with _flash_lock:
            _flash_status.update(progress=100, message="Flash complete — board is rebooting")
        return True

    except Exception as e:
        with _flash_lock:
            _flash_status.update(error=str(e), message="Error")
        return False
    finally:
        with _flash_lock:
            _flash_status["running"] = False

def _find_arduino_cli():
    """Find arduino-cli executable."""
    import os
    # Check %LOCALAPPDATA%\Arduino\arduino-cli.exe (Windows standard)
    local = os.path.expandvars(r"%LOCALAPPDATA%\Arduino\arduino-cli.exe")
    if os.path.isfile(local):
        return local
    # Check PATH
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, "arduino-cli.exe" if os.name == "nt" else "arduino-cli")
        if os.path.isfile(candidate):
            return candidate
    return None

def flash_giga(port, bin_path, progress_cb=None):
    """Flash a Giga R1 WiFi via arduino-cli (DFU mode required)."""
    with _flash_lock:
        _flash_status.update(running=True, progress=0, message="Looking for arduino-cli...", error=None)

    cli = _find_arduino_cli()
    if not cli:
        with _flash_lock:
            _flash_status.update(error="arduino-cli not found. Install from arduino.cc or via winget.", message="Error")
        return False

    try:
        with _flash_lock:
            _flash_status.update(message="Uploading via DFU...")

        # arduino-cli upload requires the sketch dir, but we can use --input-file for precompiled
        cmd = [cli, "upload", "--port", port, "--fqbn", "arduino:mbed_giga:giga",
               "--input-file", str(bin_path)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if "%" in line:
                try:
                    pct = int(line.split("]")[0].split("[")[1].strip().replace("=", "").replace(" ", "").replace("%", ""))
                except Exception:
                    try:
                        # Try "Download [====     ] 40%" format
                        if "%" in line:
                            pct = int(line.split("%")[0].split()[-1])
                            with _flash_lock:
                                _flash_status["progress"] = min(pct, 100)
                                _flash_status["message"] = f"Writing... {pct}%"
                    except Exception:
                        pass
            if "File downloaded successfully" in line:
                with _flash_lock:
                    _flash_status.update(progress=100, message="Upload complete")
            if progress_cb:
                progress_cb(line)
        proc.wait()
        if proc.returncode != 0:
            with _flash_lock:
                _flash_status.update(error="Upload failed (is the board in DFU bootloader mode?)", message="Error")
            return False
        with _flash_lock:
            _flash_status.update(progress=100, message="Complete — press reset to boot")
        return True
    except Exception as e:
        with _flash_lock:
            _flash_status.update(error=str(e), message="Error")
        return False
    finally:
        with _flash_lock:
            _flash_status["running"] = False

def flash_board(port, bin_path, board, wifi_ssid=None, wifi_pass=None,
                progress_cb=None, verify_status_url=None):
    """Flash firmware to a board. Dispatches to the correct method.

    `verify_status_url` (optional) — HTTP /status endpoint of the target
    device. When provided, ESP flashes are verified post-reset (#761 §C-4)
    by confirming `version` differs from the pre-flash baseline."""
    if board in ("esp32", "d1mini", "esp32s3"):
        return flash_esp(port, bin_path, board, progress_cb,
                          verify_status_url=verify_status_url)
    elif board == "giga":
        return flash_giga(port, bin_path, progress_cb)
    else:
        with _flash_lock:
            _flash_status.update(error=f"Unknown board: {board}", message="Error", running=False)
        return False
