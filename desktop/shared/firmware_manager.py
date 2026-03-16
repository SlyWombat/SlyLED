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
}

FQBN_MAP = {
    "esp32":  "esp32:esp32:esp32",
    "d1mini": "esp8266:esp8266:d1_mini",
    "giga":   "arduino:mbed_giga:giga",
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
        if "ESP32" in out:
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

# ── Registry ──────────────────────────────────────────────────────────────────

def load_registry(firmware_dir):
    """Load firmware/registry.json."""
    p = Path(firmware_dir) / "registry.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"firmware": []}

# ── Flash ─────────────────────────────────────────────────────────────────────

_flash_status = {"running": False, "progress": 0, "message": "", "error": None}
_flash_lock = threading.Lock()

def get_flash_status():
    with _flash_lock:
        return dict(_flash_status)

def flash_esp(port, bin_path, board="esp32", progress_cb=None):
    """Flash an ESP32 or ESP8266 using esptool."""
    with _flash_lock:
        _flash_status.update(running=True, progress=0, message="Starting...", error=None)

    try:
        baud = "460800" if board == "d1mini" else "921600"
        cmd = [
            sys.executable, "-m", "esptool",
            "--port", port, "--baud", baud,
            "write_flash", "0x0", str(bin_path)
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.strip()
            if "%" in line:
                try:
                    pct = int(line.split("(")[1].split("%")[0].strip())
                    with _flash_lock:
                        _flash_status["progress"] = pct
                        _flash_status["message"] = f"Writing... {pct}%"
                except Exception:
                    pass
            if progress_cb:
                progress_cb(line)
        proc.wait()
        if proc.returncode != 0:
            with _flash_lock:
                _flash_status.update(error="Flash failed", message="Error")
            return False
        with _flash_lock:
            _flash_status.update(progress=100, message="Complete")
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

def flash_board(port, bin_path, board, wifi_ssid=None, wifi_pass=None, progress_cb=None):
    """Flash firmware to a board. Dispatches to the correct method."""
    if board in ("esp32", "d1mini"):
        return flash_esp(port, bin_path, board, progress_cb)
    elif board == "giga":
        return flash_giga(port, bin_path, progress_cb)
    else:
        with _flash_lock:
            _flash_status.update(error=f"Unknown board: {board}", message="Error", running=False)
        return False
