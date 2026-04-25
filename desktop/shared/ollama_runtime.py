"""ollama_runtime.py — manage the local Ollama service + vision model
for #623 AI camera auto-tune.

Mirrors the shape of `depth_runtime.py` (#598) so the Windows installer
can drop an ``ollama.install-requested`` marker and the orchestrator
picks it up on first launch. Runs in the background so the operator
sees progress via the SPA, not the Inno Setup console.

What this module handles:

1. **Probe** — is Ollama running? Is the configured VLM pulled?
2. **Install Ollama** — download + run the official installer on
   Windows / macOS / Linux. On Windows the installer requires UAC; we
   ``ShellExecute`` with ``runas`` so the user sees the prompt once.
3. **Pull model** — call Ollama's ``/api/pull`` streaming endpoint and
   surface byte-progress through ``status()``.

Install is a single background thread with a status dict the API routes
poll. Re-entry is safe — concurrent ``start_install()`` calls no-op.

Environment overrides (shared with camera_settings.py):
    SLYLED_OLLAMA_URL     default ``http://localhost:11434``
    SLYLED_OLLAMA_MODEL   default ``moondream``
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("slyled.ollama_runtime")

OLLAMA_URL = os.environ.get("SLYLED_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("SLYLED_OLLAMA_MODEL", "qwen2.5vl:3b")

# Windows installer URL — Ollama ships a single .exe that sets up the
# service + Start Menu entry. macOS / Linux use the shell installer.
OLLAMA_WINDOWS_INSTALLER = (
    "https://ollama.com/download/OllamaSetup.exe"
)
OLLAMA_MACOS_INSTALLER = (
    "https://ollama.com/download/Ollama-darwin.zip"
)
OLLAMA_LINUX_INSTALL_SH = "https://ollama.com/install.sh"


# ── Module-level progress state ─────────────────────────────────────────

_progress = {
    "phase": None,      # None | "install-ollama" | "pull-model" | "done" | "error"
    "percent": 0,
    "message": None,
    "error": None,
    "startedAt": None,
    "finishedAt": None,
}
_progress_lock = threading.Lock()
_install_thread = None


def _set_progress(**fields):
    with _progress_lock:
        _progress.update(fields)


def progress():
    """Snapshot of the current install state for the SPA status endpoint."""
    with _progress_lock:
        return dict(_progress)


# ── Probes ──────────────────────────────────────────────────────────────

def is_ollama_running(timeout=1.5):
    """Quick TCP/HTTP probe. Returns True when Ollama is reachable."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def has_model(name=None, timeout=2.0):
    """True when the requested model (or OLLAMA_MODEL by default) is pulled."""
    name = name or OLLAMA_MODEL
    base = name.split(":")[0]
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except Exception:
        return False
    for m in body.get("models") or []:
        if m.get("name", "").split(":")[0] == base:
            return True
    return False


def is_installed():
    """True when both the service is running AND the model is pulled.
    Matches the ``_depth_runtime.is_installed()`` contract."""
    return is_ollama_running() and has_model()


def status():
    """Aggregated status for /api/ollama-runtime/status."""
    running = is_ollama_running()
    model = has_model() if running else False
    prog = progress()
    return {
        "running": running,
        "hasModel": model,
        "model": OLLAMA_MODEL,
        "url": OLLAMA_URL,
        "platform": platform.system().lower(),
        "progress": prog,
        "installed": running and model,
        "warm": _warm_state.get("warm", False),
        "warmedAt": _warm_state.get("warmedAt"),
        "lastError": _warm_state.get("lastError"),
        "startedByUs": started_by_us(),
    }


# ── Service lifecycle (start / stop on orchestrator boot + shutdown) ───

# Tracks whether THIS orchestrator process spawned `ollama serve` itself,
# vs. it was already running (system service / menu-bar app / operator
# manually started it). We only stop the daemon on shutdown when we own
# it — never tear down something that was running before us.
_our_serve_proc = None  # subprocess.Popen | None


def _resolve_ollama_binary():
    """Find the ``ollama`` executable that's on PATH or in a known
    install location. Returns the absolute path or None."""
    import shutil as _shutil
    found = _shutil.which("ollama")
    if found:
        return found
    if platform.system() == "Windows":
        for cand in (r"C:\Program Files\Ollama\ollama.exe",
                      r"C:\Program Files (x86)\Ollama\ollama.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")):
            if cand and os.path.isfile(cand):
                return cand
    elif platform.system() == "Darwin":
        for cand in ("/Applications/Ollama.app/Contents/Resources/ollama",
                      "/usr/local/bin/ollama"):
            if os.path.isfile(cand):
                return cand
    else:
        for cand in ("/usr/local/bin/ollama", "/usr/bin/ollama"):
            if os.path.isfile(cand):
                return cand
    return None


def start_serve(wait_seconds: float = 8.0):
    """Start ``ollama serve`` in the background if Ollama is installed
    but not currently running. Idempotent — returns False with no side
    effect when the daemon is already up (someone else owns it) or when
    we already started it on a previous call.

    Returns ``True`` when this call spawned a new ``ollama serve`` and
    the daemon answered ``/api/tags`` within ``wait_seconds``. Records
    the Popen handle on ``_our_serve_proc`` so ``stop_serve()`` knows
    whether to terminate at orchestrator shutdown.
    """
    global _our_serve_proc
    if _our_serve_proc is not None and _our_serve_proc.poll() is None:
        return True  # we already own a running serve
    if is_ollama_running():
        # Already running but we didn't spawn it — leave it alone.
        return False
    binary = _resolve_ollama_binary()
    if binary is None:
        return False
    try:
        # `ollama serve` runs in foreground by design; redirect stdio so
        # it doesn't spam stdout and detach from any terminal so closing
        # the launching shell doesn't kill it (until WE do at shutdown).
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if platform.system() == "Windows":
            # CREATE_NEW_PROCESS_GROUP so we can send CTRL_BREAK on stop.
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            kwargs["start_new_session"] = True  # POSIX setsid()
        _our_serve_proc = subprocess.Popen([binary, "serve"], **kwargs)
    except Exception as e:
        _warm_state["lastError"] = f"start_serve failed: {e}"
        _our_serve_proc = None
        return False
    # Wait for the daemon to answer /api/tags.
    deadline = time.time() + float(wait_seconds)
    while time.time() < deadline:
        if is_ollama_running():
            return True
        if _our_serve_proc.poll() is not None:
            # Process died before responding — clean up.
            _our_serve_proc = None
            return False
        time.sleep(0.5)
    return is_ollama_running()


def stop_serve(timeout_s: float = 5.0):
    """Terminate the ``ollama serve`` we started, if any. Idempotent
    no-op when we never owned the daemon (system service / menu-bar
    app / operator-launched). Sends SIGTERM (CTRL_BREAK on Windows)
    then escalates to SIGKILL on timeout.
    """
    global _our_serve_proc
    proc = _our_serve_proc
    _our_serve_proc = None
    if proc is None:
        return False
    if proc.poll() is not None:
        return False  # already exited
    try:
        if platform.system() == "Windows":
            sig = getattr(signal, "CTRL_BREAK_EVENT", None)
            if sig is not None:
                proc.send_signal(sig)
            else:
                proc.terminate()
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout_s)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass
    return True


def started_by_us():
    """True when ``start_serve()`` succeeded and the daemon we spawned
    is still alive. Used by ``status()`` so the SPA can show "ollama
    started by orchestrator" vs. "ollama running independently"."""
    proc = _our_serve_proc
    return bool(proc is not None and proc.poll() is None)


# ── Warm-up + test harness (#NEW — boot-time prefetch) ─────────────────

_warm_state = {"warm": False, "warmedAt": None, "lastError": None}


def warmup(timeout_s: float = 60.0):
    """Send a single tiny generate request so the model is loaded into RAM
    and subsequent calls are sub-second. No-op when the service isn't
    running or the model isn't pulled — start_install() takes precedence
    over warmup."""
    if not (is_ollama_running() and has_model()):
        _warm_state["lastError"] = "service or model not ready"
        return False
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps({
                "model": OLLAMA_MODEL,
                "prompt": "ping",
                "stream": False,
                "keep_alive": "10m",
                "options": {"num_predict": 1},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
        _warm_state["warm"] = True
        _warm_state["warmedAt"] = time.time()
        _warm_state["lastError"] = None
        return True
    except Exception as e:
        _warm_state["lastError"] = str(e)
        _warm_state["warm"] = False
        return False


def run_test(prompt: str = "Reply with the single word: pong",
             timeout_s: float = 60.0):
    """Fixed-prompt test harness. Returns {ok, response, ms, err}."""
    if not (is_ollama_running() and has_model()):
        return {"ok": False, "err": "Ollama service or model not ready"}
    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=json.dumps({
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "10m",
                "options": {"num_predict": 16},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
        ms = int((time.time() - t0) * 1000)
        _warm_state["warm"] = True
        _warm_state["warmedAt"] = time.time()
        return {"ok": True, "response": (data.get("response") or "").strip(),
                "ms": ms, "model": OLLAMA_MODEL}
    except Exception as e:
        return {"ok": False, "err": str(e),
                "ms": int((time.time() - t0) * 1000)}


# ── Install steps ───────────────────────────────────────────────────────

def _download_to(url, dest_path, chunk=1 << 16):
    """Stream a URL to disk, updating progress bytes."""
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        written = 0
        with open(dest_path, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                written += len(buf)
                if total:
                    pct = int(10 + (written / total) * 60)  # 10..70 band
                    _set_progress(percent=pct,
                                    message=f"Downloading Ollama ({written/1e6:.0f}/{total/1e6:.0f} MB)")
    return dest_path


def _install_ollama_windows():
    """Download OllamaSetup.exe and run it. Returns once the installer
    launches; Ollama's installer runs its own progress UI thereafter."""
    _set_progress(phase="install-ollama", percent=5,
                  message="Downloading Ollama installer")
    tmp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP")
                    or "C:\\Windows\\Temp") / "slyled-ollama"
    installer = _download_to(OLLAMA_WINDOWS_INSTALLER,
                              tmp_dir / "OllamaSetup.exe")
    _set_progress(percent=75, message="Running Ollama installer (approve UAC prompt)")
    # /S = silent mode per the NSIS-based Ollama installer.
    # Launch and wait — UAC may prompt.
    try:
        subprocess.run([str(installer), "/SILENT"], check=False, timeout=600)
    except Exception as e:
        raise RuntimeError(f"Ollama installer exited: {e}")


def _install_ollama_macos():
    """macOS path: download the ZIP, extract the .app into /Applications."""
    _set_progress(phase="install-ollama", percent=5,
                  message="Downloading Ollama.app")
    tmp_dir = Path("/tmp/slyled-ollama")
    zipped = _download_to(OLLAMA_MACOS_INSTALLER, tmp_dir / "Ollama.zip")
    _set_progress(percent=75, message="Installing Ollama.app")
    # Use ditto or unzip; /Applications needs admin on some setups.
    import zipfile
    with zipfile.ZipFile(zipped) as z:
        z.extractall("/Applications")
    # Start the service — Ollama.app doubles as a menu-bar daemon.
    subprocess.run(["open", "/Applications/Ollama.app"], check=False)


def _install_ollama_linux():
    """Linux path: fetch and exec the official install script."""
    _set_progress(phase="install-ollama", percent=5,
                  message="Downloading install.sh")
    tmp_dir = Path("/tmp/slyled-ollama")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_dir / "install.sh"
    _download_to(OLLAMA_LINUX_INSTALL_SH, script)
    script.chmod(0o755)
    _set_progress(percent=50, message="Running install.sh")
    r = subprocess.run(["bash", str(script)], capture_output=True,
                       text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"Ollama install.sh failed: {r.stderr[:400]}")


def _wait_for_service(timeout_s=60):
    """After the installer runs, poll until the daemon answers."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if is_ollama_running():
            return True
        time.sleep(2)
    return False


def _pull_model(name=None):
    """Call Ollama's /api/pull streaming endpoint; update progress from
    each chunk's completed/total fields. Matches the pattern Ollama's
    own CLI uses so the percentage is accurate."""
    name = name or OLLAMA_MODEL
    _set_progress(phase="pull-model", percent=0,
                  message=f"Pulling {name}")
    payload = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/pull", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    # No outer timeout — model pulls can legitimately take > 10 min on
    # slow links. The overall install thread has its own watchdog above.
    resp = urllib.request.urlopen(req, timeout=None)
    layer_total = 0
    layer_done = 0
    while True:
        line = resp.readline()
        if not line:
            break
        try:
            d = json.loads(line.decode())
        except json.JSONDecodeError:
            continue
        if "total" in d and "completed" in d and d["total"]:
            layer_total = int(d["total"])
            layer_done = int(d["completed"])
            pct = int(min(99, 100 * layer_done / max(1, layer_total)))
            _set_progress(percent=pct,
                           message=f"Pulling {name}: "
                                   f"{layer_done/1e6:.0f}/{layer_total/1e6:.0f} MB")
        elif "status" in d:
            # Non-byte phases: "pulling manifest", "verifying digest", …
            _set_progress(message=f"Pulling {name}: {d['status']}")
        if d.get("status") == "success":
            break


def _install_worker(force):
    _set_progress(phase="install-ollama", percent=0,
                  message="Starting install",
                  startedAt=time.time(), finishedAt=None, error=None)
    try:
        if not is_ollama_running():
            sysname = platform.system().lower()
            if sysname == "windows":
                _install_ollama_windows()
            elif sysname == "darwin":
                _install_ollama_macos()
            elif sysname == "linux":
                _install_ollama_linux()
            else:
                raise RuntimeError(f"Unsupported platform: {sysname}")
            if not _wait_for_service(timeout_s=120):
                raise RuntimeError("Ollama installed but service didn't come up")

        if force or not has_model():
            _pull_model()

        _set_progress(phase="done", percent=100,
                       message="Ollama + vision model ready",
                       finishedAt=time.time(), error=None)
    except Exception as e:
        log.exception("ollama install worker failed")
        _set_progress(phase="error", message=str(e), error=str(e),
                       finishedAt=time.time())


def start_install(force=False):
    """Kick off the background install thread. Matches the
    ``_depth_runtime.start_install`` contract used by the depth install
    route — returns a dict with ``ok`` + a ``message`` string."""
    global _install_thread
    if _install_thread is not None and _install_thread.is_alive():
        return {"ok": False, "message": "Install already running",
                 "phase": progress().get("phase")}
    if not force and is_installed():
        return {"ok": False, "message": "Already installed"}
    _install_thread = threading.Thread(target=_install_worker,
                                       args=(force,), daemon=True)
    _install_thread.start()
    return {"ok": True, "message": "Install started"}


# ── Startup marker check (#623 — mirrors #598) ─────────────────────────

def check_install_marker(install_dir):
    """Poll for an ``ollama.install-requested`` marker dropped by the
    Windows installer when the 'ai' component was ticked. Auto-start
    the background install; remove the marker so a repeat launch is
    idempotent."""
    try:
        marker = Path(install_dir) / "ollama.install-requested"
        if not marker.exists():
            return
        try:
            marker.unlink()
        except OSError:
            pass
        if is_installed():
            return
        log.info("ollama.install-requested marker present — kicking off background install")
        start_install()
    except Exception as e:
        log.warning("ollama install-marker check failed: %s", e)
