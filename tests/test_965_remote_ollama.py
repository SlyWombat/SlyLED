#!/usr/bin/env python3
"""Remote Ollama as a first-class AI Runtime setting (#965).

A fake Ollama on 127.0.0.2 (127.0.0.1 where the OS has no 127.0.0.2, e.g.
macOS) records every request, so the tests can prove what SlyLED does and —
more importantly — doesn't do to a remote it doesn't own:
  * settings.aiRuntime {mode, url, allowRemotePull} + model via
    /api/ai-runtime/config, validated; env override wins and says so
  * Test connection: reachability, version, vision models, RTT
  * remote mode never installs / serves / warms up at boot
  * a pull onto a remote is refused unless enabled (403), then needs a
    per-pull confirm naming the host + size (409), then runs
  * remote down → "remote unreachable", install still refused, and the AI
    evaluator falls back to the CV analyzer with a note

Run: python3 tests/test_965_remote_ollama.py
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


CALLS = []   # (method, path)
MODELS = [{"name": "qwen2.5vl:3b", "size": 3_200_000_000, "modified_at": "2026-09-01T00:00:00Z"},
          {"name": "llama3.1:8b", "size": 4_900_000_000, "modified_at": "2026-09-01T00:00:00Z"}]


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        CALLS.append(("GET", self.path))
        if self.path == "/api/tags":
            return self._json({"models": MODELS})
        if self.path == "/api/version":
            return self._json({"version": "0.12.3"})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        CALLS.append(("POST", self.path))
        if self.path == "/api/pull":
            body = b'{"status":"pulling manifest"}\n{"status":"success"}\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/generate":
            return self._json({"response": "pong"})
        self._json({"error": "not found"}, 404)


def _bind_host():
    try:
        s = socket.socket()
        s.bind(("127.0.0.2", 0))
        s.close()
        return "127.0.0.2"
    except OSError:
        return "127.0.0.1"


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def main():
    import parent_server as ps
    import ollama_runtime as orr
    import camera_settings as cs

    host = _bind_host()
    srv = ThreadingHTTPServer((host, 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    REMOTE = f"http://{host}:{srv.server_address[1]}"
    c = ps.app.test_client()

    # Guards: nothing may spawn a local serve or run an installer.
    spawned = []
    orr._resolve_ollama_binary = lambda: "/usr/local/bin/ollama"
    orr.subprocess.Popen = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
        AssertionError("Popen called"))
    installers = []
    for fn in ("_install_ollama_windows", "_install_ollama_macos", "_install_ollama_linux"):
        setattr(orr, fn, lambda fn=fn: installers.append(fn))
    orr._registry_size_bytes = lambda name, timeout=5.0: 3_200_000_000

    print("Default + validation")
    d = c.get("/api/ai-runtime/config").get_json()
    ok("default is local on this machine",
       d["mode"] == "local" and d["url"] == "http://localhost:11434" and d["source"] == "settings", d)
    r = c.put("/api/ai-runtime/config", json={"mode": "remote", "url": ""})
    ok("remote without a URL → 400", r.status_code == 400, r.get_json())
    r = c.put("/api/ai-runtime/config", json={"mode": "remote", "url": "192.168.10.67:11434"})
    ok("URL without http:// → 400", r.status_code == 400, r.get_json())
    r = c.put("/api/ai-runtime/config", json={"mode": "cloud", "url": REMOTE})
    ok("unknown mode → 400", r.status_code == 400)
    r = c.put("/api/ai-runtime/config", json={"mode": "remote", "url": REMOTE + "/", "model": "qwen2.5vl:3b"})
    d = r.get_json()
    ok("remote saved (trailing slash trimmed), model stored",
       r.status_code == 200 and d["mode"] == "remote" and d["url"] == REMOTE
       and d["model"] == "qwen2.5vl:3b" and ps._settings.get("aiAutoTuneModel") == "qwen2.5vl:3b", d)
    ok("persisted as settings.aiRuntime",
       ps._settings.get("aiRuntime") == {"mode": "remote", "url": REMOTE, "allowRemotePull": False},
       ps._settings.get("aiRuntime"))

    print("Status + test connection")
    st = c.get("/api/ollama-runtime/status").get_json()
    ok("status: remote, ready, not installable",
       st["remote"] and st["state"] == "ready" and st["canInstall"] is False
       and st["url"] == REMOTE and st["platform"] == "remote", st)
    t = c.post("/api/ai-runtime/test-connection", json={}).get_json()
    ok("test connection: reachable with version + RTT",
       t["ok"] and t["version"] == "0.12.3" and isinstance(t["rttMs"], int), t)
    ok("test connection: lists models and flags vision ones",
       [m["name"] for m in t["models"]] == ["qwen2.5vl:3b", "llama3.1:8b"]
       and t["visionModels"] == ["qwen2.5vl:3b"], t)
    t = c.post("/api/ai-runtime/test-connection", json={"url": _dead_url()}).get_json()
    ok("test connection on a dead URL → not ok, with the reason", not t["ok"] and "unreachable" in t["err"], t)
    m = c.get("/api/ollama-runtime/models").get_json()
    ok("model list comes from the remote", [x["name"] for x in m["models"]][0] == "qwen2.5vl:3b", m)

    print("Hands off the remote")
    CALLS.clear()
    r = c.post("/api/ollama-runtime/install", json={})
    ok("install → 409 naming the remote", r.status_code == 409
       and "never installs" in r.get_json()["message"] and host in r.get_json()["message"], r.get_json())
    ok("start_serve is a no-op", orr.start_serve(wait_seconds=0.1) is False)
    ps._ai_helpers_warmup()
    time.sleep(0.5)
    ok("boot: no serve spawned, no installer run", not spawned and not installers, (spawned, installers))
    ok("boot: no warm-up generate sent to the remote", ("POST", "/api/generate") not in CALLS, CALLS)

    print("Pulls onto a remote")
    CALLS.clear()
    r = c.post("/api/ollama-runtime/pull", json={"model": "qwen2.5vl:3b", "confirm": True})
    ok("remote pulls disabled by default → 403 even with confirm", r.status_code == 403
       and "disabled" in r.get_json()["message"], r.get_json())
    ok("…and nothing reached the remote", ("POST", "/api/pull") not in CALLS, CALLS)
    c.put("/api/ai-runtime/config", json={"mode": "remote", "url": REMOTE, "allowRemotePull": True})
    r = c.post("/api/ollama-runtime/pull", json={"model": "qwen2.5vl:3b"})
    d = r.get_json()
    ok("enabled, no confirm → 409 asking, with host and size",
       r.status_code == 409 and d["needsConfirm"] and d["host"] == host
       and "~3.2 GB" in d["message"] and host in d["message"], d)
    ok("…still nothing pulled", ("POST", "/api/pull") not in CALLS, CALLS)
    r = c.post("/api/ollama-runtime/pull", json={"model": "qwen2.5vl:3b", "confirm": True})
    ok("confirmed → 200", r.status_code == 200 and r.get_json()["ok"], r.get_json())
    for _ in range(50):
        if orr.progress().get("phase") == "done":
            break
        time.sleep(0.05)
    ok("…the pull reached the remote and finished",
       ("POST", "/api/pull") in CALLS and orr.progress().get("phase") == "done", (CALLS, orr.progress()))

    print("AI evaluator against a live remote")
    ev = cs.make_evaluator("ai", model="qwen2.5vl:3b")
    ok("AI mode builds the real AI evaluator while the remote answers", ev.__name__ == "_run_ai", ev)
    es = c.get("/api/cameras/settings/evaluator-status").get_json()["modes"]["ai"]
    ok("evaluator-status points at the remote", es["available"] and es["url"] == REMOTE and es["remote"], es)

    print("Remote down")
    srv.shutdown()
    srv.server_close()
    st = c.get("/api/ollama-runtime/status").get_json()
    ok("status: 'remote unreachable', not 'not installed'",
       st["state"] == "remote unreachable" and not st["installed"], st)
    r = c.post("/api/ollama-runtime/install", json={})
    ok("install still refused (no local fallback)", r.status_code == 409 and not installers)
    es = c.get("/api/cameras/settings/evaluator-status").get_json()["modes"]["ai"]
    ok("evaluator-status says remote unreachable", not es["available"] and "remote unreachable" in es["err"], es)
    ev = cs.make_evaluator("ai", model="qwen2.5vl:3b")
    import numpy as np
    frame = np.full((120, 160, 3), 128, dtype=np.uint8)
    res = ev(frame, controls_meta=[], intent="general")
    ok("AI evaluator falls back to the CV analyzer, with a note",
       res.get("evaluator") == "analyzer" and "remote unreachable" in (res.get("fallback") or ""), res)

    print("Environment override")
    orr._ENV_URL = "http://10.9.9.9:11434"
    d = c.get("/api/ai-runtime/config").get_json()
    ok("env URL wins and says so; non-loopback → remote",
       d["source"] == "environment" and d["url"] == "http://10.9.9.9:11434"
       and d["mode"] == "remote" and d["envOverride"]["url"], d)
    ok("…Settings are kept underneath", d["settingsUrl"] == REMOTE, d)
    orr._ENV_URL = "http://127.0.0.1:11434"
    ok("env URL on loopback → local", orr.config()["mode"] == "local")
    orr._ENV_MODE = "remote"
    ok("SLYLED_OLLAMA_MODE forces the mode", orr.config()["mode"] == "remote")
    orr._ENV_URL, orr._ENV_MODE = None, None

    print("Back to local")
    r = c.put("/api/ai-runtime/config", json={"mode": "local"})
    d = r.get_json()
    ok("local mode uses this machine; the remote URL is remembered",
       d["mode"] == "local" and d["url"] == "http://localhost:11434" and d["settingsUrl"] == REMOTE, d)
    st = c.get("/api/ollama-runtime/status").get_json()
    ok("local status is installable again", st["canInstall"] is True and not st["remote"], st)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
