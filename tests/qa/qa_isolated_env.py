#!/usr/bin/env python3
"""Isolated QA network on kdocker3 — SlyLED instances that can never reach (or
be seen by) the production lighting LAN.

Operator (2026-09-26): "You can create an isolated network instance for
testing" — so QA stops being a "second orchestrator" next to the production
service (SlyWombat/house-network-ops#278, SlyLED#966).

Topology: a Docker **--internal** bridge `slyled-qa-isolated` (10.250.0.0/24,
no gateway to the LAN or internet; broadcasts stay inside):
  10.250.0.2   slyled-qa-runner    python:3.12-slim, drives tests (stdlib only)
  10.250.0.6   slyled-qa-hinkspix  fake HinksPix MS_160 (tests/qa/fake_hinkspix.py)
  10.250.0.10  slyled-qa-orch-a    ghcr.io/slywombat/slyled:<tag>
  10.250.0.11  slyled-qa-orch-b    second orchestrator (peer-conflict tests, #966)

Everything is named slyled-qa-*; `down` removes only those, plus images this
script pulled that were not on the host before `up`. kdocker3's other
containers and networks (Ollama, Homepage, …) are never touched.

  python tests/qa/qa_isolated_env.py up [--tag 2.2.0]
  python tests/qa/qa_isolated_env.py smoke
  python tests/qa/qa_isolated_env.py down
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HOST = "claude@192.168.10.38"
NET = "slyled-qa-isolated"
SUBNET = "10.250.0.0/24"
REMOTE = "~/slyled-qa-iso"
QA = Path(__file__).resolve().parent
IPS = {"runner": "10.250.0.2", "hinkspix": "10.250.0.6", "orch-a": "10.250.0.10", "orch-b": "10.250.0.11"}
PY_IMG = "python:3.12-slim"

_p = _f = 0


def ok(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
    else:
        _f += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond or detail == "" else f"  ({str(detail)[:250]})"),
          flush=True)
    return cond


def ssh(cmd, timeout=600, stdin=None):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, cmd],
                       capture_output=True, text=True, timeout=timeout, input=stdin)
    return r.returncode, r.stdout, r.stderr


def runner_py(code, timeout=120):
    """Run a stdlib-only Python snippet inside the runner container."""
    return ssh(f"docker exec -i slyled-qa-runner python -", timeout=timeout, stdin=code)


def up(tag):
    print(f"== up (image ghcr.io/slywombat/slyled:{tag})")
    code, out, _ = ssh("docker images --format '{{.Repository}}:{{.Tag}}'")
    pre = set(out.split())
    ssh(f"rm -rf {REMOTE} && mkdir -p {REMOTE}")
    tar = subprocess.run(["tar", "-C", str(QA), "-cf", "-", "fake_hinkspix.py",
                          "hinkspix_ms160_capture_2026_09_23"], capture_output=True).stdout
    subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, f"tar -C {REMOTE} -xf -"], input=tar, check=True)
    img = f"ghcr.io/slywombat/slyled:{tag}"
    cmds = [
        f"docker network create --internal --subnet {SUBNET} {NET}",
        f"docker run -d --name slyled-qa-hinkspix --network {NET} --ip {IPS['hinkspix']} "
        f"-v $(cd {REMOTE} && pwd):/qa:ro {PY_IMG} python -u /qa/fake_hinkspix.py --port 80",
        f"docker run -d --name slyled-qa-runner --network {NET} --ip {IPS['runner']} {PY_IMG} sleep infinity",
    ]
    for name in ("orch-a", "orch-b"):
        cmds.append(f"docker run -d --name slyled-qa-{name} --network {NET} --ip {IPS[name]} "
                    f"-e TZ=America/Toronto {img}")
    for c in cmds:
        rc, o, e = ssh(c, timeout=900)
        ok(c.split(" --")[0][:60] + " …", rc == 0, e[-200:])
    ssh(f"echo '{json.dumps(sorted(pre))}' > {REMOTE}/.pre_images.json")


def smoke():
    print("== smoke")
    code = r'''
import json, socket, time, urllib.request
A, B, H = "http://10.250.0.10:8080", "http://10.250.0.11:8080", "10.250.0.6"
def get(u, t=10):
    with urllib.request.urlopen(u, timeout=t) as r: return json.loads(r.read())
def post(u, body, m="POST", t=30):
    r = urllib.request.Request(u, data=json.dumps(body).encode(), method=m, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as x: return json.loads(x.read())
res = {}
for _ in range(60):
    try: res["a"], res["b"] = get(A + "/status"), get(B + "/status"); break
    except Exception: time.sleep(2)
res["status"] = {k: (v.get("platform"), v.get("version")) for k, v in res.items() if k in "ab"}
# Both orchestrators broadcast CMD_PING on 4210; a third party on the net must hear both.
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 4210)); s.settimeout(1); seen = set(); end = time.time() + 40
while time.time() < end and len(seen) < 2:
    try: d, a = s.recvfrom(1500)
    except socket.timeout: continue
    if len(d) >= 8 and d[:2] == b"LS" and d[3] == 0x01: seen.add(a[0])
res["pingers"] = sorted(seen)
# Fake HinksPix through orch-a: add, probe, device-config.
c = post(A + "/api/children", {"ip": H}); cid = c.get("id"); res["child"] = (c.get("type"), c.get("boardType"))
post(A + f"/api/hinkspix/{cid}/probe", {})
dc = get(A + f"/api/hinkspix/{cid}/device-config", 30)
res["ports"] = len((dc.get("device") or {}).get("ports") or {})
# Streamed output: port 17 layout + fixture + sACN unicast to the fake; solid colour; counters must rise.
post(A + f"/api/hinkspix/{cid}", {"baseUniverse": 1, "protocol": "e131",
     "ports": [{"port": 17, "leds": 200, "enabled": True, "protocol": "ws2811", "colorOrder": "RGB"}]}, "PUT")
post(A + f"/api/hinkspix/{cid}/fixtures-from-ports", {})
post(A + "/api/dmx/settings", {"protocol": "sacn"}); post(A + "/api/dmx/start", {"protocol": "sacn"})
def ctr():
    r = urllib.request.Request(f"http://{H}/GetInfo.cgi", headers={"ROW": "907"})
    raw = urllib.request.urlopen(r, timeout=10).read()
    import gzip; t = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode().split(",")
    return int(t[1]), int(t[4])
c0 = ctr(); post(A + f"/api/children/{cid}/action", {"type": 1, "r": 255, "g": 0, "b": 0, "allStrings": True})
time.sleep(3); c1 = ctr(); post(A + f"/api/children/{cid}/action/stop", {})
res["counters"] = [c0, c1]
print(json.dumps({k: v for k, v in res.items() if k not in "ab"}))
'''
    rc, out, err = runner_py(code, timeout=300)
    try:
        r = json.loads(out.strip().splitlines()[-1])
    except Exception:
        ok("runner script completed", False, (out + err)[-400:])
        return
    print("  " + json.dumps(r))
    st = r.get("status", {})
    ok("both orchestrators up inside the isolated net (linux)", all(v[0] == "linux" for v in st.values()) and len(st) == 2, st)
    ok("both orchestrators' broadcast PINGs are visible on the isolated net (basis for #966)",
       r.get("pingers") == ["10.250.0.10", "10.250.0.11"], r.get("pingers"))
    ok("fake HinksPix added as hinkspix hardware", r.get("child", [None])[0] == "hinkspix", r.get("child"))
    ok("device-config reads 32 ports from the fake", r.get("ports") == 32, r.get("ports"))
    c0, c1 = r.get("counters", [[0, 0], [0, 0]])
    ok("sACN unicast reaches the fake controller (counters rise, both universes)",
       c1[0] - c0[0] > 40 and c1[1] - c0[1] > 40, r.get("counters"))

    print("== isolation")
    rc, out, _ = ssh("docker exec slyled-qa-orch-a python -c \"import socket; s=socket.socket(); s.settimeout(3); "
                     "print(s.connect_ex(('192.168.10.6', 80)))\"")
    ok("orch-a cannot reach the real HinksPix (192.168.10.6)", out.strip() not in ("0",), out.strip())
    rc, out, _ = ssh("docker exec slyled-qa-orch-a python -c \"import socket; s=socket.socket(); s.settimeout(3); "
                     "print(s.connect_ex(('1.1.1.1', 443)))\"")
    ok("orch-a has no internet route", out.strip() not in ("0",), out.strip())
    rc, out, _ = ssh(f"docker network inspect {NET} --format '{{{{.Internal}}}}'")
    ok("network is --internal", out.strip() == "true", out.strip())


def down():
    print("== down")
    code, out, _ = ssh(f"cat {REMOTE}/.pre_images.json 2>/dev/null || echo '[]'")
    try:
        pre = set(json.loads(out.strip() or "[]"))
    except ValueError:
        pre = set()
    ssh("docker ps -a --format '{{.Names}}' | grep '^slyled-qa-' | xargs -r docker rm -f")
    ssh(f"docker network rm {NET} 2>/dev/null")
    code, out, _ = ssh("docker images --format '{{.Repository}}:{{.Tag}}'")
    for img in set(out.split()) - pre:
        if img.startswith(("ghcr.io/slywombat/slyled:", "python:3.12-slim")):
            ssh(f"docker image rm {img}")
    ssh(f"rm -rf {REMOTE}")
    code, out, _ = ssh(f"docker ps -a --format '{{{{.Names}}}}' | grep -c '^slyled-qa-'; "
                       f"docker network ls --format '{{{{.Name}}}}' | grep -c '^{NET}$'; test -d {REMOTE} && echo dir || echo nodir")
    left = out.split()
    ok("no slyled-qa containers, network or files left", left[:2] == ["0", "0"] and "nodir" in left, left)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["up", "smoke", "down"])
    ap.add_argument("--tag", default="2.2.0")
    a = ap.parse_args()
    {"up": lambda: up(a.tag), "smoke": smoke, "down": down}[a.step]()
    print(f"\n{_p} passed, {_f} failed out of {_p + _f} tests")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
