#!/usr/bin/env python3
"""test_linux_packaging.py — #948 Phase 2: static contract of desktop/linux/.

Hermetic (no docker, no root): the systemd unit, udev rules, install.sh and
requirements agree with each other and with the code they serve. The
container run that actually executes install.sh is
tests/test_linux_install_docker.py.

Run:
    python3 tests/test_linux_packaging.py
"""

import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIN = os.path.join(ROOT, "desktop", "linux")
sys.path.insert(0, os.path.join(ROOT, "desktop", "shared"))

import firmware_manager  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def read(*p):
    return open(os.path.join(*p), encoding="utf-8").read()


def unit_service_keys(text):
    keys, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Service]" and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            keys.setdefault(k, []).append(v)
    return keys


def reqs(path):
    out = set()
    for line in read(path).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(re.split(r"[<>=!~\[ ]", line, 1)[0].lower())
    return out


def run():
    unit = read(LIN, "slyled.service")
    k = unit_service_keys(unit)
    inst = read(LIN, "install.sh")
    prefix = re.search(r"^PREFIX=(\S+)", inst, re.M).group(1)
    service = re.search(r"^SERVICE=(\S+)", inst, re.M).group(1)
    svc_user = re.search(r"^SVC_USER=(\S+)", inst, re.M).group(1)
    port = re.search(r"^PORT=(\d+)", inst, re.M).group(1)

    # ── systemd unit ─────────────────────────────────────────────────────
    ok("unit runs as a non-root User=", k.get("User", [""])[0] not in ("", "root"), k.get("User"))
    ok("unit User= is install.sh's service user", k.get("User") == [svc_user], (k.get("User"), svc_user))
    ok("SupplementaryGroups includes dialout", "dialout" in " ".join(k.get("SupplementaryGroups", [])).split())
    ok("Restart=on-failure (SIGKILL restarts)", k.get("Restart") == ["on-failure"], k.get("Restart"))
    ok("unit file name matches install.sh SERVICE", os.path.exists(os.path.join(LIN, f"{service}.service")))
    exec_start = k.get("ExecStart", [""])[0].split()
    ok("ExecStart uses the install venv", exec_start[:1] == [f"{prefix}/.venv/bin/python"], exec_start)
    ok("ExecStart runs parent_server.py headless",
       exec_start[1:2] == [f"{prefix}/desktop/shared/parent_server.py"] and "--no-browser" in exec_start,
       exec_start)
    ok("ExecStart port matches install.sh PORT",
       "--port" in exec_start and exec_start[exec_start.index("--port") + 1] == port, exec_start)
    env = dict(e.split("=", 1) for e in k.get("Environment", []))
    ok("XDG_DATA_HOME set outside the install tree",
       env.get("XDG_DATA_HOME", "").startswith("/var/lib/") and not env["XDG_DATA_HOME"].startswith(prefix), env)
    ok("StateDirectory= creates XDG_DATA_HOME owned by User=",
       k.get("StateDirectory") and "/var/lib/" + k["StateDirectory"][0] == env.get("XDG_DATA_HOME"),
       (k.get("StateDirectory"), env))
    ok("SLYLED_DATA not pinned (app_dirs derives data/firmware/runtimes together)",
       "SLYLED_DATA" not in env, env)
    ok("waits for the network", "After=network-online.target" in unit and "Wants=network-online.target" in unit)
    ok("enabled for boot (WantedBy=multi-user.target)", "WantedBy=multi-user.target" in unit)

    # ── udev rules cover every flashable board ───────────────────────────
    rules = read(LIN, "99-slyled-usb.rules").lower()
    missing = []
    for vidpid in firmware_manager.KNOWN_BOARDS:
        vid, pid = vidpid.lower().split(":")
        if not re.search(rf'idvendor\}}=="{vid}", attrs?\{{idproduct\}}=="{pid}"', rules):
            missing.append(vidpid)
    ok("udev rule for every KNOWN_BOARDS VID:PID", not missing, missing)
    ok("Giga DFU (not a tty) matched on SUBSYSTEM==usb",
       re.search(r'subsystem=="usb".*"2341".*"0366"', rules) is not None)
    ok("udev grants group dialout", all('group="dialout"' in l for l in rules.splitlines()
                                        if l.startswith("subsystem")))

    # ── install.sh ───────────────────────────────────────────────────────
    r = subprocess.run(["bash", "-n", os.path.join(LIN, "install.sh")], capture_output=True, text=True)
    ok("install.sh parses (bash -n)", r.returncode == 0, r.stderr)
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", "-S", "warning", os.path.join(LIN, "install.sh")],
                           capture_output=True, text=True)
        ok("shellcheck -S warning clean", r.returncode == 0, r.stdout[-800:])
    ok("never --only-binary (esptool is sdist-only)",
       "--only-binary" not in re.sub(r"#.*", "", inst))
    ok("install.sh installs the unit and udev rule it ships",
       "slyled.service" in inst and "99-slyled-usb.rules" in inst)
    ok("--uninstall removes unit + udev rule", "--uninstall" in inst and 'rm -f "$UNIT_DST" "$UDEV_DST"' in inst)
    ok("desktop/shared/data excluded from the copied tree", "--exclude='desktop/shared/data'" in inst)
    ok("firewall rules cover every UDP port the server binds",
       all(p in inst for p in ("4210/udp", "4211/udp", "5568/udp", "6454/udp")))

    # ── requirements ─────────────────────────────────────────────────────
    lin = reqs(os.path.join(LIN, "requirements.txt"))
    win = reqs(os.path.join(ROOT, "desktop", "windows", "requirements.txt"))
    ok("Linux requirements ⊇ Windows minus the tray", not (win - {"pystray"} - lin), win - {"pystray"} - lin)
    ok("no pystray on a headless controller", "pystray" not in lin)
    ok("psutil present (net_ifaces)", "psutil" in lin)

    # ── release hash gate sees the new files (build_release.ps1) ─────────
    ps1 = read(ROOT, "build_release.ps1")
    fn = ps1[ps1.find("function Get-OrchestratorSourceHash"):]
    m = re.search(r"\$extra = @\((.*?)\)", fn, re.S)
    extra = m.group(1).replace("\\", "/") if m else ""
    for f in sorted(os.listdir(LIN)):
        if f.endswith(".md"):
            continue  # docs don't ship in the install; they must not bump the version
        ok(f"hash gate lists desktop/linux/{f}", f"desktop/linux/{f}" in extra)


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
