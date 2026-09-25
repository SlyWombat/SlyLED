#!/usr/bin/env python3
"""test_platform_smoke.py — #948 Phase 0 acceptance: the orchestrator boots
and serves its OS-touching routes on this host (run on ubuntu + macOS CI).

Spawns `parent_server.py --no-browser --port <free>` against a throwaway
SLYLED_DATA and asserts 200 on /status, /api/settings, /api/dmx/interfaces
(with >= 1 non-loopback NIC), /api/firmware/ports and /help.

Run:
    python3 tests/test_platform_smoke.py
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SERVER = os.path.join(ROOT, "desktop", "shared", "parent_server.py")

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port, path, timeout=10):
    """(status, body bytes) — status 0 on connection failure."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def run():
    port = _free_port()
    data = tempfile.mkdtemp(prefix="slyled-smoke-")
    # Unbuffered + faulthandler: if startup hangs, SIGABRT dumps every
    # thread's stack into the log printed below.
    env = dict(os.environ, SLYLED_DATA=data, PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1")
    log_path = os.path.join(data, "server.out")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", SERVER, "--no-browser",
                             "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        status, body = 0, b""
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            status, body = _get(port, "/status", timeout=2)
            if status == 200:
                break
            time.sleep(0.5)
        ok("server answers /status within 60 s", status == 200,
           f"status={status} exit={proc.poll()}")
        if status != 200:
            if proc.poll() is None and hasattr(signal, "SIGABRT") and os.name != "nt":
                proc.send_signal(signal.SIGABRT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            log.flush()
            print(open(log_path, encoding="utf-8", errors="replace").read()[-8000:])
            return
        st = json.loads(body)
        want = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
        ok("/status reports host platform", st.get("platform") == want, st.get("platform"))
        ok("/status carries udpListener", isinstance(st.get("udpListener"), dict))

        status, _ = _get(port, "/api/settings")
        ok("/api/settings 200", status == 200, status)

        status, body = _get(port, "/api/dmx/interfaces")
        ok("/api/dmx/interfaces 200", status == 200, status)
        ifaces = json.loads(body) if status == 200 else []
        real = [i for i in ifaces if i.get("ip") not in ("0.0.0.0",)
                and not str(i.get("ip", "")).startswith("127.")]
        ok("/api/dmx/interfaces lists >= 1 non-loopback NIC", len(real) >= 1, ifaces)

        status, body = _get(port, "/api/firmware/ports")
        ok("/api/firmware/ports 200", status == 200, body[:200])
        ok("/api/firmware/ports returns a list",
           status == 200 and isinstance(json.loads(body), list), body[:200])

        status, body = _get(port, "/help")
        ok("/help 200", status == 200, status)
        ok("/help serves the manual", b"<html" in body[:2000].lower(), body[:120])

        # #962 — a second headless instance on the same port must FAIL
        # (exit 1, clear message), not exit 0 after trying to open a
        # browser: exit 0 made a container's `restart: unless-stopped` loop.
        data2 = tempfile.mkdtemp(prefix="slyled-smoke2-")
        second = subprocess.run([sys.executable, "-X", "utf8", SERVER, "--no-browser",
                                 "--port", str(port)], cwd=ROOT,
                                env=dict(env, SLYLED_DATA=data2),
                                capture_output=True, text=True, timeout=120)
        ok("second --no-browser instance on the same port exits 1",
           second.returncode == 1, f"rc={second.returncode}")
        ok("…and says another instance is answering",
           "already answering on port" in second.stderr, second.stderr[-300:])

        ok("data dir honoured (SLYLED_DATA)",
           os.path.isdir(os.path.join(data, "logs")) or any(
               f.endswith(".json") for f in os.listdir(data)),
           os.listdir(data))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def main():
    run()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({detail})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
