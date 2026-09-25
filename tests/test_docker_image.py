#!/usr/bin/env python3
"""test_docker_image.py — the SlyLED container image (#962).

Runs the image the way docker-compose.yml does (host network, named volume,
TZ) and checks it from the host:

  * /status 200, platform linux, version == the image's version label,
    udpListener ok; /help and /api/settings 200
  * runs as a non-root user
  * data lives on the volume: a child added, then `docker restart`, is still
    listed
  * the HEALTHCHECK reaches "healthy"
  * `docker stop` finishes inside the 20 s grace period with exit code 0 and
    logs the clean-shutdown line (SIGTERM → engines stopped → exit 0)
  * a second container on the same port exits 1 with a clear message (never
    0 — that made `restart: unless-stopped` loop silently)
  * the volume can be removed afterwards (nothing left behind)

Run (SKIPs with exit 0 without docker):
    python3 tests/test_docker_image.py --image ghcr.io/slywombat/slyled:2.1.7
"""

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    return cond


def sh(*argv, timeout=120):
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(port, path, method="GET", body=None, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return 0, b""


def wait_status(port, secs=90):
    end = time.time() + secs
    while time.time() < end:
        s, b = get(port, "/status", timeout=2)
        if s == 200:
            return json.loads(b)
        time.sleep(1)
    return None


def fmt(container, template):
    return sh("docker", "inspect", "--format", template, container).stdout.strip()


def run(image):
    tag = uuid.uuid4().hex[:8]
    name, vol, name2 = f"slyled-img-{tag}", f"slyled-img-vol-{tag}", f"slyled-img2-{tag}"
    port = free_port()
    # Host networking shares the host's UDP 4210: only assert the listener
    # bound when nothing on this host held it before we started.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("", 4210))
        udp_was_free = True
    except OSError:
        udp_was_free = False
    finally:
        probe.close()
    label = sh("docker", "image", "inspect", "--format",
               '{{index .Config.Labels "org.opencontainers.image.version"}}', image).stdout.strip()
    ok("image carries a version label", bool(label), label)
    base = ["docker", "run", "-d", "--network", "host", "-e", f"SLYLED_PORT={port}",
            "-e", "TZ=America/Toronto"]
    try:
        r = sh(*base, "--name", name, "-v", f"{vol}:/var/lib/slyled", image)
        if not ok("container started", r.returncode == 0, r.stderr):
            return
        st = wait_status(port)
        if not ok("/status answers from the host (host network)", st is not None,
                  sh("docker", "logs", "--tail", "40", name).stdout):
            return
        ok("/status platform = linux", st.get("platform") == "linux", st.get("platform"))
        ok("/status version == image label", st.get("version") == label,
           (st.get("version"), label))
        if udp_was_free:
            ok("UDP listener bound", (st.get("udpListener") or {}).get("ok") is True,
               st.get("udpListener"))
        else:
            print("  [SKIP] UDP 4210 was already taken on this host before the test — "
                  "listener bind not asserted")
        for path in ("/help", "/api/settings"):
            s, _ = get(port, path)
            ok(f"{path} 200", s == 200, s)
        uid = sh("docker", "exec", name, "id", "-u").stdout.strip()
        ok("runs as a non-root user", uid not in ("", "0"), uid)
        tz = sh("docker", "exec", name, "date", "+%Z").stdout.strip()
        ok("TZ honoured (America/Toronto → EST/EDT)", tz in ("EST", "EDT"), tz)

        s, add = get(port, "/api/children", "POST", {"ip": "127.0.0.2"}, timeout=60)
        ok("add a child", s == 200, add[:200])
        r = sh("docker", "exec", name, "ls", "/var/lib/slyled/SlyLED/data/children.json")
        ok("data written to the volume (/var/lib/slyled/SlyLED/data)", r.returncode == 0, r.stderr)

        sh("docker", "restart", "-t", "20", name, timeout=90)
        st = wait_status(port)
        s, kids = get(port, "/api/children")
        ok("child persisted across docker restart", st and b"127.0.0.2" in kids, kids[:200])

        health = ""
        for _ in range(90):
            health = fmt(name, "{{.State.Health.Status}}")
            if health == "healthy":
                break
            time.sleep(1)
        ok("HEALTHCHECK reaches healthy", health == "healthy", health)

        print("  (second container on the same port…)")
        r = sh(*base, "--name", name2, image)
        code = ""
        for _ in range(60):
            if fmt(name2, "{{.State.Status}}") == "exited":
                code = fmt(name2, "{{.State.ExitCode}}")
                break
            time.sleep(1)
        ok("second instance on the same port exits 1 (not 0)", code == "1", code)
        ok("…with a clear message",
           "already answering on port" in sh("docker", "logs", name2).stderr,
           sh("docker", "logs", name2).stderr[-300:])

        t = time.time()
        sh("docker", "stop", "-t", "20", name, timeout=60)
        took = time.time() - t
        ok(f"docker stop within the 20 s grace period ({took:.1f} s)", took < 20)
        ok("exit code 0 after SIGTERM", fmt(name, "{{.State.ExitCode}}") == "0",
           fmt(name, "{{.State.ExitCode}}"))
        logs = sh("docker", "logs", name)
        ok("clean-shutdown line logged", "shutdown complete" in logs.stdout + logs.stderr,
           (logs.stdout + logs.stderr)[-400:])
    finally:
        sh("docker", "rm", "-f", name, name2)
        r = sh("docker", "volume", "rm", vol)
        ok("volume removed afterwards", r.returncode == 0, r.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="slyled:dev")
    a = ap.parse_args()
    if not shutil.which("docker") or sh("docker", "info").returncode:
        print("  [SKIP] docker not available")
        sys.exit(0)
    if sh("docker", "image", "inspect", a.image).returncode:
        print(f"  [SKIP] image {a.image} not present (build or pull it first)")
        sys.exit(0)
    run(a.image)
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({str(detail)[:400]})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
