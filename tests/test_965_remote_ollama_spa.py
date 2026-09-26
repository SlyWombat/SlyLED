"""Settings → AI Runtime, Local / Remote, in the browser (#965).

Real SPA, real server, a fake Ollama on 127.0.0.2 (127.0.0.1 fallback).
Asserts: choosing Remote shows the URL + security note, Test connection
reports version / vision models / RTT, Save persists settings.aiRuntime,
the status line says "Remote ready" and Install is hidden, a remote pull
asks for confirmation naming the host, and an environment override disables
the controls with "set by environment".

Run: python tests/test_965_remote_ollama_spa.py   (needs playwright + chromium)
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 18105
BASE = f"http://127.0.0.1:{PORT}"
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


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = {"/api/tags": {"models": [{"name": "qwen2.5vl:3b", "size": 3_200_000_000},
                                         {"name": "llama3.1:8b", "size": 4_900_000_000}]},
                "/api/version": {"version": "0.12.3"}}.get(self.path)
        data = json.dumps(body or {}).encode()
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def flat(t):
    return " ".join((t or "").split())


def main():
    from playwright.sync_api import sync_playwright
    import parent_server as ps
    import ollama_runtime as orr

    try:
        s = socket.socket()
        s.bind(("127.0.0.2", 0))
        s.close()
        host = "127.0.0.2"
    except OSError:
        host = "127.0.0.1"
    srv = ThreadingHTTPServer((host, 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    remote = f"http://{host}:{srv.server_address[1]}"
    orr._registry_size_bytes = lambda name, timeout=5.0: 3_200_000_000

    threading.Thread(target=lambda: ps.app.run(host="127.0.0.1", port=PORT, threaded=True,
                                               use_reloader=False), daemon=True).start()
    time.sleep(1.5)
    dialogs = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        def on_dialog(d):
            dialogs.append(d.message)
            d.accept()
        page.on("dialog", on_dialog)
        page.goto(BASE + "/?tab=settings", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function("typeof _setSection === 'function' && typeof _aiRtSave === 'function'",
                               timeout=15000)
        page.evaluate("() => { showTab('settings'); _setSection('advanced'); }")
        page.wait_for_selector("#ai-rt-mode-local", timeout=8000)
        time.sleep(0.8)

        print("Local by default")
        ok("'this machine' is selected", page.is_checked("#ai-rt-mode-local"))
        ok("the remote URL row is hidden", not page.is_visible("#ai-rt-remote-row"))

        print("Remote")
        page.check("#ai-rt-mode-remote")
        ok("choosing Remote shows the URL row", page.is_visible("#ai-rt-remote-row"))
        ok("…with the security note",
           "OLLAMA_HOST=0.0.0.0" in flat(page.inner_text("#ai-rt-security"))
           and "over the network" in flat(page.inner_text("#ai-rt-security")))
        page.fill("#ai-rt-url", remote)
        page.click("#ai-rt-test-btn")
        page.wait_for_function("document.getElementById('ai-rt-test').textContent.indexOf('Connected') >= 0",
                               timeout=8000)
        t = flat(page.inner_text("#ai-rt-test"))
        ok("Test connection shows version, vision models and RTT",
           "0.12.3" in t and "1 vision" in t and "qwen2.5vl:3b" in t and " ms" in t, t)
        page.check("#ai-rt-allow-pull")
        page.click("#ai-rt-save")
        page.wait_for_function("document.getElementById('ai-rt-test').textContent.indexOf('Saved') >= 0",
                               timeout=8000)
        ok("Save persists settings.aiRuntime",
           ps._settings.get("aiRuntime") == {"mode": "remote", "url": remote, "allowRemotePull": True},
           ps._settings.get("aiRuntime"))
        page.wait_for_function("document.getElementById('ollama-rt-status').textContent.indexOf('Remote ready') >= 0",
                               timeout=8000)
        ok("status says Remote ready at the URL", remote in flat(page.inner_text("#ollama-rt-status")))
        ok("Install is hidden for a remote", not page.is_visible("#ollama-rt-install"))
        ok("Re-pull is hidden for a remote", not page.is_visible("#ollama-rt-reinstall"))

        print("Pull onto the remote asks first")
        page.fill("#ollama-rt-pull-name", "qwen2.5vl:3b")
        page.click("#ollama-rt-pull")
        page.wait_for_timeout(1500)   # sync Playwright delivers dialogs only while waiting on it
        ok("a confirmation names the host and the size",
           dialogs and host in dialogs[0] and "~3.2 GB" in dialogs[0], dialogs)

        print("Environment override")
        orr._ENV_URL = "http://10.9.9.9:11434"
        page.evaluate("() => _aiRtConfigLoad()")
        time.sleep(0.6)
        env = flat(page.inner_text("#ai-rt-env"))
        ok("'set by environment' shown", page.is_visible("#ai-rt-env") and "set by environment" in env, env)
        ok("the controls are locked", page.is_disabled("#ai-rt-url") and page.is_disabled("#ai-rt-save"))
        ok("the URL shown is the environment's", page.input_value("#ai-rt-url") == "http://10.9.9.9:11434")
        orr._ENV_URL = None
        browser.close()
    srv.shutdown()

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
