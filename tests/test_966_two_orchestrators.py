#!/usr/bin/env python3
"""Two real orchestrators notice each other — on both sides (#966).

**Spawns two orchestrator processes → CI or the isolated QA network only**
(`python tests/qa/qa_isolated_env.py suites`), never on a LAN machine
(CLAUDE.md, Tests: operator rule 2026-09-26). Each instance gets its own
HTTP port, its own UDP port (SLYLED_UDP_PORT, testing knob) and the other
one as a unicast announce target (SLYLED_PEER_TARGETS), with a 1 s announce
interval — so detection doesn't depend on the runner's broadcast domain.

Asserts: each /status lists the other (hostname, port → link, version)
within a few seconds; after one stops, the survivor drops it within the
3-interval window; both log the conflict.

Run: python3 tests/test_966_two_orchestrators.py
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation (no parent_server import here)
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SERVER = os.path.join(ROOT, "desktop", "shared", "parent_server.py")
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


def _free(kind):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _status(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _spawn(http, udp, peer_udp, logs):
    data = tempfile.mkdtemp(prefix="slyled-966-")
    env = dict(os.environ, SLYLED_DATA=data, SLYLED_UDP_PORT=str(udp),
               SLYLED_PEER_TARGETS=f"127.0.0.1:{peer_udp}", SLYLED_ORCH_ANNOUNCE_S="1",
               PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    log = open(os.path.join(data, "server.out"), "w", encoding="utf-8")
    logs.append(log.name)
    return subprocess.Popen([sys.executable, "-X", "utf8", SERVER, "--no-browser",
                             "--port", str(http)], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def _wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.5)
    return None


def main():
    ha, hb = _free(socket.SOCK_STREAM), _free(socket.SOCK_STREAM)
    ua, ub = _free(socket.SOCK_DGRAM), _free(socket.SOCK_DGRAM)
    logs = []
    a = _spawn(ha, ua, ub, logs)
    b = _spawn(hb, ub, ua, logs)
    try:
        sa = _wait(lambda: _status(ha), 60)
        sb = _wait(lambda: _status(hb), 60)
        ok("both servers answer /status", sa and sb, (sa, sb))
        if not (sa and sb):
            return 1
        ok("distinct instance ids", sa["instanceId"] != sb["instanceId"])

        def peers_of(port):
            s = _status(port) or {}
            return s.get("peerOrchestrators") or []
        pa = _wait(lambda: [p for p in peers_of(ha) if p.get("instanceId") == sb["instanceId"]], 20)
        pb = _wait(lambda: [p for p in peers_of(hb) if p.get("instanceId") == sa["instanceId"]], 20)
        ok("A lists B", bool(pa), peers_of(ha))
        ok("B lists A", bool(pb), peers_of(hb))
        if pa:
            # The announce caps the hostname at 64 bytes (CI runner names run long).
            ok("A's entry for B links to B's HTTP port and names it",
               pa[0]["port"] == hb and pa[0]["url"].endswith(f":{hb}")
               and pa[0]["hostname"] and sb["hostname"].startswith(pa[0]["hostname"])
               and pa[0]["version"] == sb["version"], (pa, sb["hostname"]))
        ok("neither lists itself",
           all(p.get("instanceId") != sa["instanceId"] for p in peers_of(ha))
           and all(p.get("instanceId") != sb["instanceId"] for p in peers_of(hb)))

        b.terminate()
        b.wait(timeout=30)
        gone = _wait(lambda: not [p for p in peers_of(ha) if p.get("instanceId") == sb["instanceId"]] or None, 15)
        ok("after B stops, A drops it within the expiry window (3 × 1 s)", bool(gone), peers_of(ha))
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=20)
                except Exception:
                    p.kill()
    texts = [open(f, encoding="utf-8", errors="replace").read() for f in logs]
    ok("both logged the conflict", all("CONFLICT: another SlyLED orchestrator" in t for t in texts),
       [t[-400:] for t in texts])
    ok("A logged B going away", "is gone" in texts[0], texts[0][-400:])

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
