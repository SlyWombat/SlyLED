#!/usr/bin/env python3
"""QA bench acceptance for #948 Phase 0 + Phase 2 — the orchestrator as a
headless Linux service, on a real host (default: kdocker3).

Runs from the QA machine. Remote steps go over SSH; HTTP checks hit the
service across the LAN, the way a phone or browser would.

  python tests/qa/qa_948_linux_bench.py --commit <sha>            # read-only preflight
  python tests/qa/qa_948_linux_bench.py --commit <sha> --install  # ship + install + verify
  python tests/qa/qa_948_linux_bench.py --uninstall               # install.sh --uninstall --purge + leftover check

--install always ends with the purge unless --keep is given: the bench host is
left exactly as it was found (operator rule).

kdocker3 is the production Ollama host for SlyTab: this script never reboots
the host and never touches docker. "Survives reboot" is checked as
`systemctl is-enabled` + a service restart; a real reboot needs the operator.

Paths assumed from the #948 spec (desktop/linux/install.sh, slyled.service,
/opt/slyled, port 8080). If the implementation differs, adjust the constants.
"""
import argparse
import ipaddress
import json
import subprocess
import sys
import time
import urllib.request

HOST = "claude@192.168.10.38"
HOST_IP = "192.168.10.38"
PORT = 8080
SERVICE = "slyled"
PREFIX = "/opt/slyled"
STAGE = "~/slyled-qa-src"
HINKSPIX = "192.168.10.6"
REPO = "/mnt/c/Projects/Lighting Arduino"

_p = _f = 0


def ok(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f"  ({str(detail)[:300]})" if detail != "" else ""))
    return cond


def ssh(cmd, timeout=600, check=False):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, cmd],
                       capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit(f"remote failed ({r.returncode}): {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def http(path, method="GET", body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{HOST_IP}:{PORT}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def preflight():
    print(f"\n== preflight {HOST}")
    r = ssh(". /etc/os-release; echo $PRETTY_NAME; uname -m; python3 --version; "
            f"systemctl is-active {SERVICE} 2>&1; ss -lntu | grep -E ':(8080|4210|4211|5568|6454) ' || true")
    print("  " + r.stdout.strip().replace("\n", "\n  "))
    ok("ssh reachable", r.returncode == 0, r.stderr)


def ship(commit):
    print(f"\n== ship {commit} -> {HOST}:{STAGE}")
    sha = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", commit],
                         capture_output=True, text=True, check=True).stdout.strip()
    # The host fetches the exact commit from GitHub itself: streaming a ~200 MB
    # git archive over the WSL->LAN ssh link is far too slow (<1 MB/min seen).
    full = subprocess.run(["git", "-C", REPO, "rev-parse", commit],
                          capture_output=True, text=True, check=True).stdout.strip()
    r = ssh(f"rm -rf {STAGE} && mkdir -p {STAGE} && cd {STAGE} && git init -q && "
            f"git fetch -q --depth 1 https://github.com/SlyWombat/SlyLED.git {full} && "
            f"git checkout -q FETCH_HEAD && git rev-parse --short HEAD", timeout=900)
    ok(f"host fetched {sha} from GitHub", r.returncode == 0 and r.stdout.strip() == sha,
       (r.stdout + r.stderr)[-300:])
    r = ssh(f"ls {STAGE}/desktop/linux/")
    print(f"  desktop/linux: {r.stdout.split()}")
    ok("desktop/linux/install.sh present", "install.sh" in r.stdout, r.stdout)
    return sha


def install():
    print("\n== install (sudo bash desktop/linux/install.sh)")
    t = time.time()
    r = ssh(f"cd {STAGE} && sudo bash desktop/linux/install.sh 2>&1 | tail -40", timeout=1800)
    print("  " + r.stdout.strip().replace("\n", "\n  ")[-2500:])
    ok(f"install.sh exit 0 ({time.time() - t:.0f}s)", r.returncode == 0, r.stderr[-400:])


def verify_service():
    print("\n== systemd")
    r = ssh(f"systemctl is-enabled {SERVICE}; systemctl is-active {SERVICE}; "
            f"systemctl show {SERVICE} -p User -p SupplementaryGroups -p ExecStart -p Restart --no-pager")
    out = r.stdout
    print("  " + out.strip().replace("\n", "\n  "))
    ok("service enabled (starts at boot)", out.splitlines()[:1] == ["enabled"], out)
    ok("service active", "active" in out.splitlines()[1:2], out)
    ok("runs as a non-root user", "User=root" not in out and "User=\n" not in out + "\n", out)
    ok("SupplementaryGroups includes dialout", "dialout" in out, out)
    ok("Restart=on-failure (or always)", "Restart=on-failure" in out or "Restart=always" in out, out)
    for _ in range(30):
        s, _b = http("/status")
        if s == 200:
            break
        time.sleep(2)
    j = ssh(f"journalctl -u {SERVICE} -n 40 --no-pager 2>&1").stdout
    ok("journal shows startup, no Traceback", "Traceback" not in j and len(j) > 0, j[-600:])


def verify_http():
    print(f"\n== HTTP from the LAN (http://{HOST_IP}:{PORT})")
    s, st = http("/status")
    ok("/status 200 from another machine", s == 200, st)
    if isinstance(st, dict):
        print(f"  status: {json.dumps(st)[:300]}")
        ok("/status reports platform linux", str(st.get("platform", "")).lower().startswith("linux"),
           st.get("platform"))
    for path in ("/api/settings", "/api/firmware/ports", "/help"):
        s, b = http(path)
        ok(f"{path} 200", s == 200, b)
    s, ifs = http("/api/dmx/interfaces")
    ok("/api/dmx/interfaces 200", s == 200, ifs)
    txt = json.dumps(ifs)
    print(f"  interfaces: {txt[:400]}")
    ok("interfaces include the LAN address 192.168.10.38", "192.168.10.38" in txt, txt[:300])
    docker = [a for a in ("172.17.", "172.18.", "172.19.", "172.2") if a in txt]
    ok("interfaces exclude docker bridge subnets (172.17-25.x)", not docker, docker)

    print("\n== data location")
    r = ssh(f"sudo -n find {PREFIX} -path '*/desktop/shared/data' -maxdepth 6 2>/dev/null; "
            f"sudo -n systemctl show {SERVICE} -p Environment --no-pager; "
            f"sudo -n ls -d /var/lib/slyled ~slyled/.local/share/SlyLED /home/*/.local/share/SlyLED 2>/dev/null")
    print("  " + r.stdout.strip().replace("\n", "\n  "))
    ok("data NOT written inside the install tree (BASE/data)",
       "desktop/shared/data" not in r.stdout, r.stdout)


SNIFF_SYN = r"""
import socket, struct, time, collections, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(3)); s.settimeout(0.5)
end = time.time() + float(sys.argv[1]); c = collections.Counter()
while time.time() < end:
    try: pkt, addr = s.recvfrom(65535)
    except socket.timeout: continue
    if addr[2] != socket.PACKET_OUTGOING or pkt[12:14] != b"\x08\x00": continue
    ip = pkt[14:]; ihl = (ip[0] & 15) * 4
    if ip[9] != 6: continue
    dport = struct.unpack("!H", ip[ihl + 2:ihl + 4])[0]; flags = ip[ihl + 13]
    if dport == 80 and flags & 0x02 and not flags & 0x10:
        c[(addr[0], socket.inet_ntoa(ip[16:20]).rsplit(".", 1)[0])] += 1
for (ifc, net), n in sorted(c.items()): print(f"{n} {ifc} {net}.x")
"""


def verify_hinkspix_discovery():
    """#949: the HTTP BoardInfo sweep finds the HinksPix on the LAN, fast,
    and never probes docker bridge subnets."""
    print("\n== #949 HinksPix HTTP sweep (before .6 is registered)")
    ssh("cat > /tmp/qa_syn.py <<'PYEOF'\n" + SNIFF_SYN + "\nPYEOF")
    ssh("(sudo -n python3 /tmp/qa_syn.py 12 > /tmp/qa_syn.out 2>&1 &) ; sleep 1.5")
    t = time.time()
    s, _ = http("/api/hinkspix/discover", "POST", {})
    r = {}
    while time.time() - t < 20:
        s, r = http("/api/hinkspix/discover")
        if isinstance(r, dict) and not r.get("pending"):
            break
        time.sleep(0.3)
    wall = time.time() - t
    found = [f.get("ip") for f in (r.get("found") or [])] if isinstance(r, dict) else []
    print(f"  sweep: total={r.get('total')} done={r.get('done')} elapsedMs={r.get('elapsedMs')} "
          f"wall={wall:.1f}s found={found} notes={r.get('notes')}")
    ok("sweep finds 192.168.10.6", HINKSPIX in found, r)
    ok("sweep of the /24 completes in < 5 s", (r.get("elapsedMs") or 99999) < 5000, r.get("elapsedMs"))
    hit = next((f for f in (r.get("found") or []) if f.get("ip") == HINKSPIX), {})
    ok("found entry typed hinkspix with MCPU and boards",
       hit.get("type") == "hinkspix" and hit.get("mcpuRaw") and hit.get("boards"), hit)
    time.sleep(11)
    syn = ssh("cat /tmp/qa_syn.out; rm -f /tmp/qa_syn.py /tmp/qa_syn.out").stdout
    print("  outgoing TCP :80 SYNs by interface/subnet:\n    " + (syn.strip() or "(none)").replace("\n", "\n    "))
    ok("sweep SYNs only on the LAN (192.168.10.x)", "192.168.10.x" in syn, syn)
    ok("zero sweep SYNs to docker bridge subnets (172.x)", "172." not in syn, syn)
    j = ssh(f"journalctl -u {SERVICE} --since '-1min' --no-pager 2>&1 | grep -i 'sweep' | tail -2").stdout
    print("  journal: " + (j.strip() or "(no sweep line)"))
    ok("journal has the one-line sweep summary", "sweep" in j.lower(), j)


def verify_network():
    print("\n== UDP discovery (4210 broadcast)")
    s, _ = http("/api/children/discover")
    time.sleep(6)
    s, res = http("/api/children/discover/results")
    print(f"  discover results: {s} {json.dumps(res)[:400]}")
    ok("discover completes (results 200)", s == 200, res)
    r = ssh(f"journalctl -u {SERVICE} --since '-2min' --no-pager 2>&1 | grep -iE 'broadcast|ping|subnet|172\\.' | tail -8")
    print("  " + (r.stdout.strip() or "(no discovery log lines)").replace("\n", "\n  "))
    ok("no PINGs addressed to docker bridge subnets", "172.1" not in r.stdout and "172.2" not in r.stdout,
       r.stdout[-300:])

    verify_hinkspix_discovery()

    print(f"\n== HinksPix {HINKSPIX} through the Linux service")
    s, add = http("/api/children", "POST", {"ip": HINKSPIX})
    print(f"  add child: {s} {json.dumps(add)[:300] if not isinstance(add, bytes) else add}")
    s, kids = http("/api/children")
    kid = next((k for k in (kids if isinstance(kids, list) else kids.get("children", []) if isinstance(kids, dict) else [])
                if k.get("ip") == HINKSPIX), None)
    ok("HinksPix present as a child", kid is not None, str(kids)[:200])
    s2, _ = http("/api/hinkspix/discover", "POST", {})
    for _ in range(60):
        s2, r2 = http("/api/hinkspix/discover")
        if isinstance(r2, dict) and not r2.get("pending"):
            break
        time.sleep(0.3)
    ok("#949: a registered .6 is not offered again", HINKSPIX not in json.dumps((r2 or {}).get("found", [])), r2)
    if kid:
        cid = kid["id"]
        s, pr = http(f"/api/hinkspix/{cid}/probe", "POST", {})
        ok("HinksPix probe from Linux (TCP to .6)", s == 200, pr)
        s, dc = http(f"/api/hinkspix/{cid}/device-config")
        ports = ((dc or {}).get("device") or {}).get("ports") if isinstance(dc, dict) else None
        ok("device-config reads 32 ports from Linux", isinstance(ports, dict) and len(ports) == 32,
           (s, str(dc)[:200]))
        if isinstance(ports, dict):
            print(f"  port 17: {ports.get('17')}")

    print("\n== sACN from the Linux host (independent tool, 3 s solid dim white)")
    r = ssh(f"python3 {STAGE}/tests/qa/qa_sacn_solid.py {HINKSPIX} --pixels 200 --rgb 20,20,20 --seconds 3 "
            f"&& python3 {STAGE}/tests/qa/qa_sacn_solid.py {HINKSPIX} --pixels 200 --rgb 0,0,0 --seconds 1")
    ok("UDP egress from the Linux host works (sACN sent, no ENETUNREACH)", r.returncode == 0,
       (r.stdout + r.stderr)[-300:])


def verify_restart():
    print("\n== restart survival (no host reboot on kdocker3)")
    ssh(f"sudo -n systemctl restart {SERVICE}")
    up = False
    for _ in range(30):
        time.sleep(2)
        if http("/status")[0] == 200:
            up = True
            break
    ok("service back after systemctl restart", up)
    s, kids = http("/api/children")
    ok("children persisted across restart", HINKSPIX in json.dumps(kids), str(kids)[:200])
    ssh(f"sudo -n systemctl kill -s KILL {SERVICE}")
    up = False
    for _ in range(30):
        time.sleep(2)
        if http("/status")[0] == 200:
            up = True
            break
    ok("systemd restarts it after SIGKILL (Restart=)", up)


def uninstall(commit="origin/main"):
    """Remove everything with the product's own `install.sh --uninstall --purge`
    (so the uninstall path is tested too), then prove nothing is left behind.
    Operator rule (2026-09-24): the bench host is always left clean."""
    print(f"\n== uninstall --purge from {HOST}")
    r = ssh(f"test -f {STAGE}/desktop/linux/install.sh && echo staged")
    if "staged" not in r.stdout:
        ship(commit)
    r = ssh(f"cd {STAGE} && sudo -n bash desktop/linux/install.sh --uninstall --purge 2>&1 | tail -15", timeout=300)
    print("  " + r.stdout.strip().replace("\n", "\n  "))
    ok("install.sh --uninstall --purge exit 0", r.returncode == 0, r.stderr[-300:])
    ssh(f"rm -rf {STAGE}")
    r = ssh(f"systemctl list-unit-files {SERVICE}.service --no-legend | wc -l; "
            f"ls -d {PREFIX} /var/lib/slyled /var/cache/slyled /etc/udev/rules.d/99-slyled-usb.rules "
            f"{STAGE} 2>/dev/null | wc -l; id slyled >/dev/null 2>&1 && echo user-present || echo user-gone; "
            f"ss -lntu | grep -cE ':(8080|4210|4211) ' || true")
    lines = r.stdout.split()
    print(f"  leftovers: unit-files={lines[0:1]} paths={lines[1:2]} {lines[2:3]} ports={lines[3:4]}")
    ok("no slyled unit file left", lines[:1] == ["0"], r.stdout)
    ok("no slyled paths left (/opt, /var/lib, /var/cache, udev rule, stage)", lines[1:2] == ["0"], r.stdout)
    ok("slyled user removed", "user-gone" in r.stdout, r.stdout)
    ok("ports 8080/4210/4211 released", lines[-1:] == ["0"], r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", default="origin/main")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--keep", action="store_true",
                    help="leave the service installed after --install (default: purge it)")
    a = ap.parse_args()
    if a.uninstall:
        uninstall(a.commit)
        print(f"\n{_p} passed, {_f} failed out of {_p + _f} tests")
        sys.exit(1 if _f else 0)
    preflight()
    if a.install:
        ship(a.commit)
        install()
        verify_service()
        verify_http()
        verify_network()
        verify_restart()
        if not a.keep:
            uninstall(a.commit)
    print(f"\n{_p} passed, {_f} failed out of {_p + _f} tests")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
