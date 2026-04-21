"""depth_runtime.py — orchestrator-side manager for the optional
ZoeDepth runtime (#598).

Keeps torch / transformers OUT of the main SlyLED.exe bundle. Installs
a standalone Python venv under %LOCALAPPDATA%\\SlyLED\\runtimes\\depth\\
that the orchestrator spawns as a subprocess when a calibration scan
needs metric depth. The main process only ever talks to it over
127.0.0.1 HTTP.

Public API (what parent_server.py consumes):

    paths()                         — dict of runtime_dir, venv_dir, python_exe, runner_py, manifest
    is_installed()                  — bool
    status()                        — full status dict for the UI
    start_install(force=False)      — kicks off a background install job
    install_progress()              — poll the background job
    uninstall()                     — rmtree the runtime dir
    ensure_running()                — spawn + health-check the runner subprocess
    infer_jpeg(jpg_bytes)           — proxy inference; returns (depth_mm ndarray, inference_ms)
    stop_runner()                   — /shutdown the subprocess

All file IO is resilient to the runtime dir not existing yet — the
status() call alone won't create it.
"""

import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

# On Windows the orchestrator exe is built with PyInstaller --windowed
# (no parent console). When we Popen a console-subsystem python.exe
# from a windowless parent Windows creates a fresh cmd window for each
# child — tempting to close, which would abort the install mid-download.
# CREATE_NO_WINDOW (0x08000000) suppresses that. On non-Windows platforms
# the flag is an empty dict, so the call becomes a no-op kwarg.
if sys.platform == "win32":
    _NO_WINDOW = {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
else:
    _NO_WINDOW = {}

# ── Layout ──────────────────────────────────────────────────────────────

def _runtime_root() -> str:
    """Return %LOCALAPPDATA%\\SlyLED\\runtimes on Windows, ~/.local/share/SlyLED/runtimes elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "SlyLED", "runtimes")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "SlyLED", "runtimes")


def paths() -> dict:
    root = _runtime_root()
    runtime_dir = os.path.join(root, "depth")
    venv_dir = os.path.join(runtime_dir, "venv")
    if sys.platform == "win32":
        py_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        py_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")
    return {
        "runtime_dir": runtime_dir,
        "venv_dir": venv_dir,
        "python_exe": py_exe,
        "pip_exe": pip_exe,
        "runner_py": os.path.join(runtime_dir, "depth_runner.py"),
        "manifest": os.path.join(runtime_dir, "depth_runtime.json"),
        "hf_home": os.path.join(runtime_dir, "hf_cache"),
    }


# ── Pinned dependency set ────────────────────────────────────────────────
# CPU-only wheels keep the download small and portable; GPU support is a
# follow-up (#598 defers CUDA/MPS detection).
#
# transformers ≥4.45 is required: ZoeDepthForDepthEstimation was added
# in 4.39 but 4.45 is the first line with a settled Windows-CPU wheel
# set. We leave torch unpinned on purpose so pip's resolver picks the
# version that transformers + accelerate actually want (accelerate
# ≥1.x pulls torch ≥2.4, so pinning torch==2.2 forced a wasteful
# uninstall-reinstall round trip on the first install attempt).
# Single combined pip install below lets the resolver see all
# constraints at once.
_PIP_INDEX = "https://download.pytorch.org/whl/cpu"
_PIP_PINS = [
    "numpy<2",
    "Pillow>=9",
    "torch>=2.4,<3",
    "transformers>=4.45,<5",
    "tokenizers>=0.19",
    "safetensors>=0.4",
    "huggingface-hub>=0.23",
    "timm>=0.9",
    "accelerate>=0.30",
    "flask>=3.0",
]


# ── Install job state ───────────────────────────────────────────────────

_install_lock = threading.Lock()
_install_state = {
    "running": False,
    "phase": "idle",
    "message": "",
    "progress": 0.0,   # 0..1
    "ok": None,        # True / False / None while running
    "error": None,
    "startedAt": None,
    "endedAt": None,
    "log": [],         # ring buffer
}
_LOG_RING = 80


def _log(msg: str):
    log.info("[depth-install] %s", msg)
    _install_state["log"].append({"t": time.time(), "m": msg})
    if len(_install_state["log"]) > _LOG_RING:
        _install_state["log"] = _install_state["log"][-_LOG_RING:]


def _phase(phase: str, message: str, progress: float):
    _install_state["phase"] = phase
    _install_state["message"] = message
    _install_state["progress"] = max(0.0, min(1.0, progress))
    _log(f"{phase}: {message}")


# ── Status / manifest ───────────────────────────────────────────────────

def _read_manifest() -> dict:
    p = paths()["manifest"]
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _dir_size_bytes(path: str) -> int:
    total = 0
    for dp, _, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def is_installed() -> bool:
    p = paths()
    return (os.path.isfile(p["python_exe"])
            and os.path.isfile(p["runner_py"])
            and os.path.isfile(p["manifest"]))


def status() -> dict:
    p = paths()
    installed = is_installed()
    manifest = _read_manifest() if installed else {}
    size_mb = None
    if installed:
        try:
            size_mb = round(_dir_size_bytes(p["runtime_dir"]) / (1024 * 1024), 1)
        except Exception:
            size_mb = None
    return {
        "installed": installed,
        "runtimeDir": p["runtime_dir"],
        "sizeMb": size_mb,
        "model": manifest.get("model"),
        "installedAt": manifest.get("installedAt"),
        "pythonVersion": manifest.get("pythonVersion"),
        "runnerPort": _runner_port(),
        "runnerRunning": _runner_is_healthy(),
    }


# ── Install / uninstall ─────────────────────────────────────────────────

def _find_host_python() -> str:
    """Find a Python ≥3.9 to bootstrap the venv. Used only at install time.

    In a PyInstaller-frozen SlyLED.exe, `sys.executable` is the .exe
    itself (not usable for venv). We prefer `py -3` (Windows launcher),
    then fall back to scanning PATH.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable

    candidates = []
    if sys.platform == "win32":
        candidates.extend(["py", "python", "python3"])
    else:
        candidates.extend(["python3", "python"])

    for c in candidates:
        try:
            out = subprocess.check_output(
                [c, "-c", "import sys; print(sys.version_info[:2])"],
                stderr=subprocess.DEVNULL, timeout=5,
                **_NO_WINDOW,
            ).decode().strip()
            if "(3," in out:
                minor = int(out.split(",")[1].strip().rstrip(")"))
                if minor >= 9:
                    return c
        except Exception:
            continue
    raise RuntimeError(
        "A Python 3.9+ interpreter is required to install the depth runtime. "
        "Install Python from https://www.python.org/ then click Install again."
    )


def _source_runner_py() -> str:
    """Locate depth_runner.py at install time — from the PyInstaller
    bundle's _MEIPASS if frozen, else alongside this module."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "depth_runner.py")
    if os.path.isfile(candidate):
        return candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = os.path.join(meipass, "depth_runner.py")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("depth_runner.py not found in bundle or alongside depth_runtime.py")


def uninstall() -> dict:
    stop_runner()
    p = paths()
    if not os.path.isdir(p["runtime_dir"]):
        return {"ok": True, "removed": False}
    try:
        shutil.rmtree(p["runtime_dir"], ignore_errors=False)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "removed": True}


def start_install(force: bool = False) -> dict:
    with _install_lock:
        if _install_state["running"]:
            return {"ok": False, "error": "install already running"}
        if is_installed() and not force:
            return {"ok": False, "error": "already installed (pass force=true to reinstall)"}
        _install_state.update({
            "running": True,
            "phase": "starting",
            "message": "",
            "progress": 0.0,
            "ok": None,
            "error": None,
            "startedAt": time.time(),
            "endedAt": None,
            "log": [],
        })
    t = threading.Thread(target=_install_worker, args=(force,), daemon=True)
    t.start()
    return {"ok": True}


def install_progress() -> dict:
    return dict(_install_state)


def _install_worker(force: bool):
    try:
        if force:
            _phase("cleanup", "Removing existing runtime...", 0.01)
            uninstall()

        p = paths()
        os.makedirs(p["runtime_dir"], exist_ok=True)

        _phase("venv", "Locating host Python interpreter...", 0.03)
        host_py = _find_host_python()
        _log(f"host python: {host_py}")

        _phase("venv", "Creating virtual environment...", 0.05)
        _run([host_py, "-m", "venv", p["venv_dir"]], cwd=p["runtime_dir"])

        _phase("venv", "Upgrading pip in venv...", 0.10)
        _run([p["python_exe"], "-m", "pip", "install", "--upgrade", "pip"], cwd=p["runtime_dir"])

        # Single pip install so the resolver sees all constraints at
        # once — previously we installed torch separately and then the
        # second step upgraded it, wasting a 200 MB download. Using the
        # download.pytorch.org CPU channel as the primary index (for
        # CPU wheels on Linux) with PyPI as fallback for everything
        # else. On Windows the default PyPI torch is already CPU-only
        # without CUDA, so either index works.
        _phase("deps", "Installing torch + transformers + dependencies — ~500 MB download...", 0.15)
        _run([p["python_exe"], "-m", "pip", "install",
              "--index-url", _PIP_INDEX,
              "--extra-index-url", "https://pypi.org/simple",
              *_PIP_PINS],
             cwd=p["runtime_dir"], heavy=True, progress_base=0.15, progress_span=0.60)

        _phase("weights", "Downloading ZoeDepth model weights — ~1.3 GB, one-time...", 0.78)
        os.makedirs(p["hf_home"], exist_ok=True)
        # Pull weights via huggingface_hub inside the venv so the main
        # process never needs transformers. Any HF_HOME override points
        # into the runtime dir for clean uninstall.
        hf_script = (
            "import os, sys\n"
            f"os.environ['HF_HOME']=r'{p['hf_home']}'\n"
            "from huggingface_hub import snapshot_download\n"
            "path = snapshot_download('Intel/zoedepth-nyu-kitti')\n"
            "print(path)\n"
        )
        _run([p["python_exe"], "-c", hf_script], cwd=p["runtime_dir"],
             heavy=True, progress_base=0.78, progress_span=0.18,
             env_extra={"HF_HOME": p["hf_home"]})

        _phase("runner", "Installing runner script...", 0.95)
        shutil.copy2(_source_runner_py(), p["runner_py"])

        _phase("verify", "Verifying transformers has ZoeDepth...", 0.97)
        # Fail loudly if the pinned version resolved to something that
        # doesn't expose ZoeDepthForDepthEstimation. Previously: silent
        # success → user hit "cannot import name" at inference time.
        verify_script = (
            "import sys, json\n"
            "try:\n"
            "    import torch, transformers\n"
            "    from transformers import ZoeDepthForDepthEstimation, AutoImageProcessor\n"
            "    out = {'torch': torch.__version__, 'transformers': transformers.__version__}\n"
            "    sys.stdout.write('VERIFY_OK ' + json.dumps(out))\n"
            "except Exception as e:\n"
            "    sys.stderr.write(str(e) + '\\n')\n"
            "    sys.exit(2)\n"
        )
        try:
            vout = subprocess.check_output(
                [p["python_exe"], "-c", verify_script],
                stderr=subprocess.PIPE, timeout=60, **_NO_WINDOW,
            ).decode().strip()
            _log(vout)
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"post-install verification failed: {err or 'see log above'}. "
                "The installed transformers package does not expose "
                "ZoeDepthForDepthEstimation — pin conflict likely."
            )

        _phase("manifest", "Writing manifest...", 0.99)
        py_ver = subprocess.check_output(
            [p["python_exe"], "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            stderr=subprocess.DEVNULL, **_NO_WINDOW,
        ).decode().strip()
        # pip freeze into the manifest so the user can see exactly what
        # resolved when something breaks later.
        try:
            freeze = subprocess.check_output(
                [p["python_exe"], "-m", "pip", "freeze"],
                stderr=subprocess.DEVNULL, timeout=30, **_NO_WINDOW,
            ).decode().strip().splitlines()
        except Exception:
            freeze = []
        manifest = {
            "schemaVersion": 1,
            "model": "Intel/zoedepth-nyu-kitti",
            "installedAt": time.time(),
            "pythonVersion": py_ver,
            "pins": _PIP_PINS,
            "resolved": freeze,
        }
        with open(p["manifest"], "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        _install_state["ok"] = True
        _phase("done", f"Installed — {status().get('sizeMb')} MB", 1.0)
    except Exception as e:
        log.exception("depth-runtime install failed")
        _install_state["ok"] = False
        _install_state["error"] = str(e)
        _phase("error", str(e), _install_state["progress"])
    finally:
        _install_state["running"] = False
        _install_state["endedAt"] = time.time()


def _run(cmd, cwd=None, heavy=False, progress_base=0.0, progress_span=0.0, env_extra=None):
    """Run a subprocess, streaming stderr line-by-line into the install
    log. For pip / huggingface_hub commands we give the UI a slowly
    increasing spinner within [base, base+span] so it doesn't look
    frozen during the multi-minute downloads — we don't parse pip's
    output for accurate progress, that's too brittle across versions."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    _log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            **_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"command not found: {cmd[0]}: {e}")

    ticks = 0
    start = time.time()
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            _log(line[:200])
        if heavy:
            ticks += 1
            # asymptotic approach to progress_base + progress_span*0.95
            elapsed = time.time() - start
            frac = 1.0 - 1.0 / (1.0 + elapsed / 20.0)   # reaches 0.5 at 20s, 0.8 at 80s, 0.95 at 380s
            _install_state["progress"] = progress_base + progress_span * frac * 0.95

    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"subprocess failed (rc={rc}): {' '.join(cmd)}")


# ── Runner subprocess supervision ───────────────────────────────────────

_runner_lock = threading.Lock()
_runner_proc = None
_runner_port_val = None
_runner_last_use = 0.0
_RUNNER_IDLE_KILL_S = 600  # runner has its own 300s idle; we keep ours looser


def _runner_port():
    return _runner_port_val


def _runner_is_healthy() -> bool:
    port = _runner_port_val
    if port is None:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_running(timeout_s: float = 30.0) -> int:
    """Spawn the runner if needed. Returns the port it's listening on.

    Raises RuntimeError with a human-readable message if the runtime
    isn't installed or the subprocess fails to come up.
    """
    global _runner_proc, _runner_port_val, _runner_last_use

    with _runner_lock:
        if _runner_is_healthy():
            _runner_last_use = time.time()
            return _runner_port_val

        if not is_installed():
            raise RuntimeError(
                "Depth runtime is not installed. Install it from "
                "Settings → Depth runtime, or from the Advanced Scan card."
            )

        # Clean up any dead child from a previous launch
        if _runner_proc is not None:
            try:
                _runner_proc.terminate()
            except Exception:
                pass
            _runner_proc = None
            _runner_port_val = None

        p = paths()
        env = os.environ.copy()
        env["HF_HOME"] = p["hf_home"]
        env.setdefault("TRANSFORMERS_OFFLINE", "0")
        log.info("spawning depth runner: %s %s", p["python_exe"], p["runner_py"])
        proc = subprocess.Popen(
            [p["python_exe"], p["runner_py"]],
            cwd=p["runtime_dir"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            **_NO_WINDOW,
        )

        # Read the first line from stdout — must be "PORT=<n>"
        port = None
        start = time.time()
        while time.time() - start < timeout_s:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    err = proc.stderr.read()
                    raise RuntimeError(f"runner exited immediately: {err[:500]}")
                continue
            line = line.strip()
            if line.startswith("PORT="):
                try:
                    port = int(line.split("=", 1)[1])
                    break
                except ValueError:
                    pass
        if port is None:
            try:
                proc.terminate()
            except Exception:
                pass
            raise RuntimeError("runner did not announce port within timeout")

        # Drain remaining stdout in a background thread so the pipe buffer
        # doesn't fill and block the child. Stderr is surfaced at INFO
        # because that's where tracebacks land when /infer blows up —
        # seeing them in the orchestrator log is usually how the user
        # finds out *why* inference failed.
        def _drain_out(stream):
            for ln in stream:
                log.debug("[runner-out] %s", ln.rstrip())
        def _drain_err(stream):
            for ln in stream:
                s = ln.rstrip()
                if s:
                    log.info("[runner-err] %s", s)
        threading.Thread(target=_drain_out, args=(proc.stdout,), daemon=True).start()
        threading.Thread(target=_drain_err, args=(proc.stderr,), daemon=True).start()

        _runner_proc = proc
        _runner_port_val = port
        _runner_last_use = time.time()

        # Wait for /health to come up (Flask takes a moment)
        h_start = time.time()
        while time.time() - h_start < 10:
            if _runner_is_healthy():
                return port
            time.sleep(0.2)
        raise RuntimeError("runner announced port but /health never responded")


def infer_jpeg(jpg_bytes: bytes, timeout_s: float = 300.0):
    """Proxy a JPEG to the runner. Returns (depth_mm ndarray, inference_ms).

    Raises RuntimeError with a specific message (including the runner's
    Python exception class + text, not just "HTTP 500") when inference
    fails on the subprocess side. The runner emits structured JSON for
    its 500 responses; we unpack that here so the user sees the real
    problem in the SPA.
    """
    global _runner_last_use
    port = ensure_running()
    _runner_last_use = time.time()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/infer",
        data=jpg_bytes,
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
    except urllib.error.HTTPError as he:
        body = b""
        try:
            body = he.read()
        except Exception:
            pass
        msg = None
        if body:
            try:
                obj = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(obj, dict):
                    msg = obj.get("err") or obj.get("error")
                    tb = obj.get("traceback")
                    if tb:
                        log.warning("depth runner traceback:\n%s", tb)
            except Exception:
                msg = body.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"runner HTTP {he.code}: {msg or he.reason or 'unknown error'}"
        )
    except urllib.error.URLError as ue:
        raise RuntimeError(f"runner unreachable: {ue.reason}")

    with resp as r:
        raw = r.read()
        shape = r.headers.get("X-Depth-Shape")
        dtype = r.headers.get("X-Depth-Dtype", "float32")
        inf_ms = int(r.headers.get("X-Inference-Ms", "0"))
    if not shape:
        raise RuntimeError("runner response missing X-Depth-Shape")
    import numpy as np
    h, w = (int(x) for x in shape.split(","))
    arr = np.frombuffer(raw, dtype=np.float32 if dtype == "float32" else dtype)
    arr = arr.reshape(h, w)
    return arr, inf_ms


def stop_runner() -> bool:
    global _runner_proc, _runner_port_val
    with _runner_lock:
        if _runner_proc is None:
            return False
        port = _runner_port_val
        try:
            if port is not None:
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(f"http://127.0.0.1:{port}/shutdown", method="POST"),
                        timeout=3,
                    )
                except Exception:
                    pass
            _runner_proc.terminate()
            try:
                _runner_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _runner_proc.kill()
        finally:
            _runner_proc = None
            _runner_port_val = None
        return True
