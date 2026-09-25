#!/usr/bin/env python3
"""test_linux_install_docker.py — #948 Phase 2: desktop/linux/install.sh in a
clean Ubuntu container.

What the QA bench on kdocker3 checks under real systemd, minus systemd:
install.sh runs, lays down /opt/slyled + the venv + the unit + the udev rule
and a non-root service user; then this test starts the unit's own ExecStart
with the unit's own User= / Environment= (parsed from slyled.service, as
systemd would) and checks the service over HTTP: platform=linux, data under
/var/lib/slyled and never inside the install tree, children surviving a
SIGKILL restart, and a clean --uninstall / --purge.

The container gets the working tree's tracked + untracked-not-ignored files
(no .git), so uncommitted changes are exercised too.

With --tarball PATH (#962) the container gets ONLY the release tarball — no
source tree — and the run also checks /opt/slyled/VERSION, that no camera
models were swept in, an in-place upgrade through `install.sh --release`
(served from a local HTTP server, sha256-verified, "upgrading X → Y", data
kept) and that a tampered checksum is refused.

Run (needs docker; SKIPs with exit 0 without it):
    python3 tests/test_linux_install_docker.py [--image ubuntu:24.04] [--keep]
    python3 tests/test_linux_install_docker.py --tarball dist/SlyLED-2.1.7-linux.tar.gz
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UNIT = os.path.join(ROOT, "desktop", "linux", "slyled.service")
NAME = "slyled-install-test"
PORT = 8080

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    return cond


def dx(cmd, timeout=900, user=None):
    """docker exec a shell command; returns CompletedProcess."""
    argv = ["docker", "exec", "-i"]
    if user:
        argv += ["-u", user]
    argv += [NAME, "bash", "-c", cmd]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def unit_fields():
    """The [Service] keys this test honours, parsed from the real unit."""
    fields = {"Environment": []}
    for line in open(UNIT, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "Environment":
            fields["Environment"].append(v)
        else:
            fields[k] = v
    return fields


def curl_json(path, method="GET", body=None):
    data = f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(body))}" if body is not None else ""
    r = dx(f"curl -s -m 60 -X {method} {data} -w '\\n%{{http_code}}' http://127.0.0.1:{PORT}{path}")
    out = r.stdout.rstrip("\n").rsplit("\n", 1)
    if len(out) != 2:
        return 0, r.stdout + r.stderr
    try:
        return int(out[1]), json.loads(out[0])
    except ValueError:
        return int(out[1] or 0), out[0][:200]


def start_service(u):
    env = " ".join(shlex.quote(e) for e in u["Environment"])
    # `cd; cmd &` (not `cd && cmd &`) so $! is the service process itself,
    # not a backgrounded root subshell.
    cmd = (f"cd {shlex.quote(u['WorkingDirectory'])}; "
           f"nohup setpriv --reuid={u['User']} --regid={u['Group']} "
           f"--groups={u['SupplementaryGroups']} "
           f"env -i PATH=/usr/bin:/bin HOME=/var/lib/slyled {env} {u['ExecStart']} "
           f">> /tmp/slyled.log 2>&1 & echo $!")
    pid = dx(cmd).stdout.strip()
    for _ in range(90):
        if curl_json("/status")[0] == 200:
            return pid
        time.sleep(1)
    return None


def run(image, keep, tarball=None):
    u = unit_fields()
    ok("unit: non-root User=", u.get("User") not in (None, "", "root"), u.get("User"))
    ok("unit: SupplementaryGroups includes dialout",
       "dialout" in u.get("SupplementaryGroups", "").split(), u.get("SupplementaryGroups"))

    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    r = subprocess.run(["docker", "run", "-d", "--name", NAME, image, "sleep", "7200"],
                       capture_output=True, text=True)
    if not ok(f"container {image} started", r.returncode == 0, r.stderr):
        return
    try:
        # Base images ship no python3; the target hosts do. curl for probing.
        # Retried: distro mirrors briefly 404 a just-superseded .deb.
        r = dx("for i in 1 2 3; do apt-get update -q >/dev/null && DEBIAN_FRONTEND=noninteractive "
               "apt-get install -y -q python3 curl ca-certificates >/dev/null && break; sleep 20; done; "
               "python3 --version")
        ok("base python3 installed", r.returncode == 0, r.stderr[-400:])
        print(f"  container python: {r.stdout.strip()}")

        if tarball:
            ver = os.path.basename(tarball)[len("SlyLED-"):-len("-linux.tar.gz")]
            r = subprocess.run(["docker", "cp", tarball, f"{NAME}:/tmp/{os.path.basename(tarball)}"],
                               capture_output=True, text=True)
            r2 = dx(f"mkdir -p /rel && tar -C /rel -xzf /tmp/{os.path.basename(tarball)} "
                    f"&& ln -s /rel/SlyLED-{ver} /src && test ! -e /src/.git "
                    f"&& test \"$(cat /src/VERSION)\" = {ver}")
            ok("release tarball extracted (no source tree, VERSION file)",
               r.returncode == 0 and r2.returncode == 0, r.stderr + r2.stderr)
        else:
            ver = None
            files = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                                    "--exclude-standard"], capture_output=True, check=True).stdout
            files = b"\0".join(f for f in files.split(b"\0")
                               if f and os.path.exists(os.path.join(ROOT, f.decode())))
            tar = subprocess.Popen(["tar", "-C", ROOT, "--null", "-T", "-", "-cf", "-"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            load = subprocess.Popen(["docker", "exec", "-i", NAME, "bash", "-c",
                                     "mkdir -p /src && tar -C /src -xf -"], stdin=tar.stdout)
            tar.stdin.write(files)
            tar.stdin.close()
            load.wait(timeout=600)
            tar.wait()
            ok("source tree copied (no .git)", load.returncode == 0 and
               dx("test ! -e /src/.git && test -f /src/desktop/linux/install.sh").returncode == 0)

        t = time.time()
        r = dx("bash /src/desktop/linux/install.sh", timeout=1800)
        print("  " + r.stdout.strip().replace("\n", "\n  ")[-1500:])
        ok(f"install.sh exit 0 ({time.time() - t:.0f}s)", r.returncode == 0, r.stderr[-800:])
        ok("install.sh notes systemd absent instead of failing",
           "systemd is not running" in r.stdout, r.stdout[-300:])
        want_ver = ver or dx("sed -n 's/^VERSION *= *\"\\([0-9.]*\\)\".*/\\1/p' "
                             "/src/desktop/shared/parent_server.py").stdout.strip()
        got_ver = dx("cat /opt/slyled/VERSION").stdout.strip()
        ok("/opt/slyled/VERSION written", got_ver == want_ver, (got_ver, want_ver))
        ok("fresh install says 'installing'", f"installing v{want_ver}" in r.stdout, r.stdout[-300:])
        ok("no camera models swept into /opt/slyled",
           dx("test ! -e /opt/slyled/firmware/orangepi/models").returncode == 0)

        r = dx("id slyled; getent passwd slyled | cut -d: -f6,7; "
               "stat -c '%U %a' /opt/slyled /opt/slyled/desktop/shared; "
               "test -f /etc/systemd/system/slyled.service && echo unit; "
               "test -f /etc/udev/rules.d/99-slyled-usb.rules && echo udev; "
               "test -e /opt/slyled/.git || echo nogit; "
               "/opt/slyled/.venv/bin/python -c 'import flask, waitress, esptool, psutil, cv2, numpy, serial, paramiko, qrcode, PIL, yaml; print(\"deps\")'")
        out = r.stdout
        print("  " + out.strip().replace("\n", "\n  "))
        ok("system user slyled exists, uid != 0", "uid=" in out and "uid=0(" not in out, out)
        ok("slyled has no login shell, home /var/lib/slyled", "/var/lib/slyled:" in out and "nologin" in out, out)
        ok("install tree root-owned, not group/world-writable",
           "root 755\nroot 755" in out, out)
        ok("unit + udev rule installed", "unit" in out and "udev" in out, out)
        ok("install tree has no .git (app_dirs treats it as installed)", "nogit" in out, out)
        ok("venv has every runtime dependency", "deps" in out, r.stderr[-400:])

        # systemd would create StateDirectory=/CacheDirectory= owned by User=.
        dx("install -d -o slyled -g slyled /var/lib/slyled /var/cache/slyled")
        pid = start_service(u)
        if not ok("service answers /status as user slyled", pid is not None,
                  dx("tail -40 /tmp/slyled.log").stdout):
            return
        s, st = curl_json("/status")
        ok("/status platform = linux", isinstance(st, dict) and st.get("platform") == "linux", st)
        for path in ("/api/settings", "/api/firmware/ports", "/help"):
            code = dx(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{PORT}{path}").stdout
            ok(f"{path} 200", code == "200", code)
        s, ifs = curl_json("/api/dmx/interfaces")
        ok("/api/dmx/interfaces lists the container NIC",
           s == 200 and any(i.get("ip") not in ("0.0.0.0",) for i in ifs), ifs)
        user = dx(f"stat -c %U /proc/{pid}").stdout.strip()  # no procps on debian images
        ok("process runs as slyled", user == "slyled", user)
        log = dx("cat /tmp/slyled.log").stdout
        ok("startup log has no Traceback", "Traceback" not in log, log[-800:])

        # 127.0.0.2: private (accepted) and every probe is refused at once;
        # an unroutable 10.x address hangs the add past curl's limit on
        # GitHub runners.
        s, add = curl_json("/api/children", "POST", {"ip": "127.0.0.2"})
        ok("add a child", s == 200, add)
        r = dx("ls /var/lib/slyled/SlyLED/data/children.json /var/lib/slyled/SlyLED/firmware "
               "&& test ! -e /opt/slyled/desktop/shared/data && echo clean")
        ok("data in /var/lib/slyled/SlyLED/data, none in the install tree",
           "children.json" in r.stdout and "clean" in r.stdout, r.stdout + r.stderr)

        dx(f"kill -KILL {pid}")
        for _ in range(20):
            if curl_json("/status")[0] != 200:
                break
            time.sleep(0.5)
        ok("SIGKILL actually stopped the service", curl_json("/status")[0] != 200)
        pid = start_service(u)
        ok("service restarts after SIGKILL", pid is not None, dx("tail -20 /tmp/slyled.log").stdout)
        s, kids = curl_json("/api/children")
        ok("children persisted across SIGKILL", "127.0.0.2" in json.dumps(kids), kids)

        r = dx("bash /src/desktop/linux/install.sh", timeout=1800)
        ok("re-install (upgrade) exit 0, venv reused",
           r.returncode == 0 and "reusing /opt/slyled/.venv" in r.stdout, r.stdout[-400:] + r.stderr[-400:])
        ok("same-version re-install says so", "same version" in r.stdout, r.stdout[-300:])
        dx(f"kill {pid}")

        if tarball:
            # In-place upgrade through --release: a re-versioned copy of the
            # tarball served over HTTP, as GitHub would serve the release.
            dx("rm -rf /up && mkdir -p /up/stage && cp -a /src/. /up/stage/SlyLED-9.9.9 "
               "&& echo 9.9.9 > /up/stage/SlyLED-9.9.9/VERSION "
               "&& tar -C /up/stage -czf /up/SlyLED-9.9.9-linux.tar.gz SlyLED-9.9.9 "
               "&& (cd /up && sha256sum SlyLED-9.9.9-linux.tar.gz > SlyLED-9.9.9-linux.tar.gz.sha256) "
               "&& cd /up && nohup python3 -m http.server 8765 >/tmp/http.log 2>&1 &")
            time.sleep(1.5)
            r = dx("SLYLED_RELEASE_BASE=http://127.0.0.1:8765 bash /src/desktop/linux/install.sh "
                   "--release v9.9.9", timeout=1800)
            ok("--release upgrade exit 0", r.returncode == 0, r.stdout[-500:] + r.stderr[-500:])
            ok("--release verified the sha256", "sha256 verified" in r.stdout, r.stdout[-400:])
            ok(f"upgrade says 'upgrading v{want_ver} → v9.9.9'",
               f"upgrading v{want_ver} → v9.9.9" in r.stdout, r.stdout[-400:])
            ok("/opt/slyled/VERSION updated to 9.9.9",
               dx("cat /opt/slyled/VERSION").stdout.strip() == "9.9.9")
            ok("installed --version reports it",
               "installed: 9.9.9" in dx("bash /src/desktop/linux/install.sh --version").stdout)
            pid = start_service(u)
            s, kids = curl_json("/api/children")
            ok("data kept across the upgrade", pid is not None and "127.0.0.2" in json.dumps(kids), kids)
            dx(f"kill {pid}")
            dx("cd /up && echo '0000000000000000000000000000000000000000000000000000000000000000  "
               "SlyLED-9.9.9-linux.tar.gz' > SlyLED-9.9.9-linux.tar.gz.sha256")
            r = dx("SLYLED_RELEASE_BASE=http://127.0.0.1:8765 bash /src/desktop/linux/install.sh "
                   "--release 9.9.9", timeout=300)
            ok("a tampered checksum is refused (nothing installed)",
               r.returncode != 0 and "sha256 mismatch" in r.stderr, r.stdout[-300:] + r.stderr[-300:])

        r = dx("bash /src/desktop/linux/install.sh --uninstall; "
               "test -e /opt/slyled || echo noprefix; test -e /etc/systemd/system/slyled.service || echo nounit; "
               "test -e /etc/udev/rules.d/99-slyled-usb.rules || echo noudev; "
               "test -f /var/lib/slyled/SlyLED/data/children.json && echo datakept")
        for tag in ("noprefix", "nounit", "noudev", "datakept"):
            ok(f"--uninstall: {tag}", tag in r.stdout, r.stdout + r.stderr)
        r = dx("bash /src/desktop/linux/install.sh --uninstall --purge; "
               "test -e /var/lib/slyled || echo nodata; id slyled 2>/dev/null || echo nouser")
        ok("--purge removes data + user", "nodata" in r.stdout and "nouser" in r.stdout, r.stdout)
    finally:
        if keep:
            print(f"  (container {NAME} kept)")
        else:
            subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="ubuntu:24.04")
    ap.add_argument("--keep", action="store_true", help="leave the container running")
    ap.add_argument("--tarball", help="install from this release tarball instead of the source tree")
    a = ap.parse_args()
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode:
        print("  [SKIP] docker not available")
        sys.exit(0)
    run(a.image, a.keep, os.path.abspath(a.tarball) if a.tarball else None)
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({str(detail)[:600]})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
