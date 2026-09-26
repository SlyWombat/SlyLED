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
  python tests/qa/qa_isolated_env.py suites [--commit SHA] [--only substr]
      server-spawning test suites, run inside the isolated net (operator rule)
  python tests/qa/qa_isolated_env.py peer966 [--commit SHA]
      #966 acceptance: two orchestrators built from source, both must alert
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


TEST_IMG = "slyled-qa-tests:local"
# Suites that spawn a real orchestrator process (parent_server.py / main.py):
# operator rule 2026-09-26 — these run ONLY here, never on a LAN machine.
SPAWNING_SUITES = [
    "tests/test_platform_smoke.py", "tests/test_web_http.py", "tests/test_show_pipeline_regressions.py",
    "tests/test_842_set_fixture_rgb_centralized.py", "tests/test_867_gyro_off.py", "tests/test_gyro_protocol.py",
    "tests/test_capability_bake_e2e.py", "tests/test_parity_action_names.py", "tests/test_parity_aim_vector.py",
    "tests/test_30_combos.py", "tests/test_dash_return.py", "tests/test_fixture_grid.py",
    "tests/test_edit_rotation.py", "tests/test_runtime3d.py", "tests/test_unified_3d.py",
    "tests/test_schedule_spa.py", "tests/test_963_offline_spa.py", "tests/test_880_profiles_spa.py",
    "tests/test_hinkspix_config_spa.py", "tests/test_hinkspix_xlights_spa.py", "tests/test_hinkspix_discover_spa.py",
    "tests/test_hinkspix_guide_spa.py",
    # #965 / #966 — added with the features (#967 retired test_web.py → test_web_http.py).
    "tests/test_965_remote_ollama_spa.py", "tests/test_966_peer_banner_spa.py",
    "tests/test_966_two_orchestrators.py",
    "tests/regression/run_all.py",
]
DOCKERFILE = r"""
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs tzdata libportaudio2 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "flask>=3.0" "qrcode>=7.0" requests numpy opencv-python-headless "cryptography>=41.0" \
    pyyaml "paramiko>=3.0" "pyserial>=3.5" psutil tzdata waitress esptool playwright
RUN playwright install --with-deps chromium
# Harness-only: give every Playwright Chromium software WebGL (no GPU in the
# container) so the SPA suites that open the 3D stage can run. Not product code.
RUN printf '%s\n' \
 'try:' \
 '    from playwright.sync_api._generated import BrowserType as _BT' \
 '    _o = _BT.launch' \
 '    def _l(self, *a, **k):' \
 '        k["args"] = list(k.get("args") or []) + ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]' \
 '        k.setdefault("channel", "chromium")' \
 '        return _o(self, *a, **k)' \
 '    _BT.launch = _l' \
 'except Exception:' \
 '    pass' > /usr/local/lib/python3.11/site-packages/sitecustomize.py
"""


def suites(commit, only):
    import time
    sha = subprocess.run(["git", "-C", str(QA.parent.parent), "rev-parse", commit],
                         capture_output=True, text=True, check=True).stdout.strip()
    print(f"== suites at {sha[:7]} (inside {NET}, no LAN)")
    rc, _, e = ssh(f"mkdir -p {REMOTE} && rm -rf {REMOTE}/src && mkdir -p {REMOTE}/src && cd {REMOTE}/src && "
                   f"git init -q && git fetch -q --depth 1 https://github.com/SlyWombat/SlyLED.git {sha} && "
                   f"git checkout -q FETCH_HEAD", timeout=900)
    ok("source fetched on kdocker3", rc == 0, e[-200:])
    # Record the host's images before we pull/build anything, so `down` only
    # removes what QA added (never a python:*-slim another service already had).
    rc, out, _ = ssh(f"test -f {REMOTE}/.pre_images.json && echo have")
    if "have" not in out:
        _, imgs, _ = ssh("docker images --format '{{.Repository}}:{{.Tag}}'")
        ssh(f"cat > {REMOTE}/.pre_images.json", stdin=json.dumps(sorted(set(imgs.split()))))
    rc, out, _ = ssh(f"docker network ls --format '{{{{.Name}}}}' | grep -c '^{NET}$'")
    if out.strip() != "1":
        ssh(f"docker network create --internal --subnet {SUBNET} {NET}")
    rc, _, e = ssh(f"cd {REMOTE} && cat > Dockerfile.tests && docker build -q -t {TEST_IMG} -f Dockerfile.tests .",
                   timeout=1800, stdin=DOCKERFILE)
    ok("test-runner image built", rc == 0, e[-300:])
    chosen = [s for s in SPAWNING_SUITES if not only or only in s]
    loop = " ".join(chosen)
    script = (f'for t in {loop}; do [ -f "$t" ] || {{ echo "$t :: rc=missing :: MISSING"; continue; }}; '
              f'SLYLED_DATA=$(mktemp -d) timeout 900 python -X utf8 "$t" > /tmp/o.txt 2>&1; rc=$?; '
              f'sum=$(grep -E "passed|failed|agree|All checks|suites passed" /tmp/o.txt | tail -1); '
              f'nf=$(grep -cE "\\[FAIL\\]|FAIL " /tmp/o.txt); '
              f'echo "$t :: rc=$rc fails=$nf :: $sum"; done')
    t0 = time.time()
    rc, out, e = ssh(f"docker run --rm --name slyled-qa-tests --network {NET} "
                     f"-v $(cd {REMOTE}/src && pwd):/src -w /src -e TZ=America/Toronto {TEST_IMG} bash -c '{script}'",
                     timeout=7200)
    print(f"  ({time.time() - t0:.0f}s)")
    for line in out.strip().splitlines():
        parts = line.split(" :: ")
        if len(parts) < 3:
            continue
        name, meta, res = parts[0], parts[1], parts[2]
        # Pass = exit 0 and no [FAIL] lines (the last-line text alone misleads).
        good = meta.startswith("rc=0 ") and meta.endswith("fails=0")
        ok(f"{name}: {meta} {res[:80]}", good)


ORCH_SRC_IMG = "slyled-qa-orch:src"


def _fetch_and_build(commit):
    """Fetch the commit on kdocker3 and build the orchestrator image from the
    repo's own Dockerfile (#962) plus the test-runner image (Playwright)."""
    sha = subprocess.run(["git", "-C", str(QA.parent.parent), "rev-parse", commit],
                         capture_output=True, text=True, check=True).stdout.strip()
    rc, out, _ = ssh(f"test -f {REMOTE}/.pre_images.json && echo have")
    if "have" not in out:
        _, imgs, _ = ssh("docker images --format '{{.Repository}}:{{.Tag}}'")
        ssh(f"mkdir -p {REMOTE} && cat > {REMOTE}/.pre_images.json", stdin=json.dumps(sorted(set(imgs.split()))))
    rc, _, e = ssh(f"cd {REMOTE} && (sudo -n rm -rf src || rm -rf src) && mkdir src && cd src && git init -q && "
                   f"git fetch -q --depth 1 https://github.com/SlyWombat/SlyLED.git {sha} && git checkout -q FETCH_HEAD",
                   timeout=900)
    ok(f"source {sha[:7]} fetched", rc == 0, e[-200:])
    rc, _, e = ssh(f"cd {REMOTE}/src && docker build -q -t {ORCH_SRC_IMG} .", timeout=2400)
    ok("orchestrator image built from the repo Dockerfile", rc == 0, e[-300:])
    rc, _, e = ssh(f"cd {REMOTE} && cat > Dockerfile.tests && docker build -q -t {TEST_IMG} -f Dockerfile.tests .",
                   timeout=1800, stdin=DOCKERFILE)
    ok("test-runner image built", rc == 0, e[-300:])
    return sha


def peer966(commit):
    """#966 acceptance: two orchestrators on one network must each alert."""
    sha = _fetch_and_build(commit)
    print(f"== #966 peer detection at {sha[:7]} (isolated net)")
    rc, out, _ = ssh(f"docker network ls --format '{{{{.Name}}}}' | grep -c '^{NET}$'")
    if out.strip() != "1":
        ssh(f"docker network create --internal --subnet {SUBNET} {NET}")
    for name in ("orch-a", "orch-b"):
        rc, _, e = ssh(f"docker rm -f slyled-qa-{name} >/dev/null 2>&1; docker run -d --name slyled-qa-{name} "
                       f"--hostname qa-{name} --network {NET} --ip {IPS[name]} -e TZ=America/Toronto {ORCH_SRC_IMG}")
        ok(f"{name} started", rc == 0, e[-200:])
    probe = r'''
import json, sys, time, urllib.request
from playwright.sync_api import sync_playwright
A, B = "http://10.250.0.10:8080", "http://10.250.0.11:8080"
phase = sys.argv[1]
def st(u):
    try:
        with urllib.request.urlopen(u + "/status", timeout=5) as r: return json.loads(r.read())
    except Exception as e: return {"_err": str(e)}
def peers(u): return st(u).get("peerOrchestrators") or []
def banner(u):
  try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        # The SPA polls continuously, so it never reaches "networkidle".
        pg.goto(u + "/", wait_until="load", timeout=60000); pg.wait_for_timeout(17000)
        r = pg.evaluate("(() => {const e=document.getElementById('peer-banner'); return e ? {shown: getComputedStyle(e).display!=='none', text: e.innerText} : {missing: true};})()")
        b.close(); return r
  except Exception as e:
    return {"_err": str(e)[:200]}
t0 = time.time(); out = {"phase": phase}
if phase == "both":
    while time.time() - t0 < 90:
        pa, pb = peers(A), peers(B)
        if pa and pb: break
        time.sleep(3)
    out.update(secs=round(time.time() - t0), a_sees=pa, b_sees=pb, a_banner=banner(A), b_banner=banner(B))
elif phase == "b_stopped":
    while time.time() - t0 < 200:
        pa = peers(A)
        if not pa: break
        time.sleep(5)
    out.update(secs=round(time.time() - t0), a_sees=pa, a_banner=banner(A))
elif phase == "b_back":
    while time.time() - t0 < 90:
        pa = peers(A)
        if pa: break
        time.sleep(3)
    out.update(secs=round(time.time() - t0), a_sees=pa)
print(json.dumps(out))
'''
    ssh(f"mkdir -p {REMOTE}/probe && cat > {REMOTE}/probe/p966.py", stdin=probe)

    def run(phase):
        rc, out, e = ssh(f"docker run --rm --network {NET} --ip {IPS['runner']} -v $(cd {REMOTE}/probe && pwd):/p:ro "
                         f"{TEST_IMG} python /p/p966.py {phase}", timeout=600)
        try:
            return json.loads(out.strip().splitlines()[-1])
        except Exception:
            return {"_err": (out + e)[-400:]}

    r = run("both")
    print("  " + json.dumps(r)[:900])
    a, b = r.get("a_sees") or [], r.get("b_sees") or []
    ok("orch-a lists orch-b in /status.peerOrchestrators", any(p.get("ip") == IPS["orch-b"] for p in a), a)
    ok("orch-b lists orch-a in /status.peerOrchestrators", any(p.get("ip") == IPS["orch-a"] for p in b), b)
    ok("detected within one announce interval (<= 45 s)", (r.get("secs") if r.get("secs") is not None else 999) <= 45, r.get("secs"))
    ok("peer entry identifies the other (hostname + version + port)",
       all(p.get("hostname") and p.get("version") and p.get("port") and p.get("instanceId") for p in a + b), a + b)
    for side in ("a", "b"):
        bn = r.get(f"{side}_banner") or {}
        ok(f"orch-{side} SPA shows the conflict banner", bn.get("shown") and "Another SlyLED orchestrator" in (bn.get("text") or ""), bn)
    ok("orch-a banner names orch-b", "qa-orch-b" in ((r.get("a_banner") or {}).get("text") or "") or IPS["orch-b"] in ((r.get("a_banner") or {}).get("text") or ""), r.get("a_banner"))

    ssh("docker stop -t 20 slyled-qa-orch-b")
    r = run("b_stopped")
    print("  " + json.dumps(r)[:500])
    ok("after orch-b stops, orch-a's peer list clears (<= ~120 s)", r.get("a_sees") == [] and (r.get("secs") if r.get("secs") is not None else 999) <= 130, (r.get("secs"), r.get("a_sees")))
    ok("orch-a banner hidden once the peer is gone", (r.get("a_banner") or {}).get("shown") is False, r.get("a_banner"))

    ssh("docker start slyled-qa-orch-b")
    r = run("b_back")
    print("  " + json.dumps(r)[:400])
    ok("orch-b restarted → orch-a sees it again (<= 45 s)", bool(r.get("a_sees")) and (r.get("secs") if r.get("secs") is not None else 999) <= 45, (r.get("secs"), r.get("a_sees")))


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
        if img.startswith(("ghcr.io/slywombat/slyled:", "python:3.12-slim", "python:3.11-slim", "slyled-qa-")):
            ssh(f"docker image rm {img}")
    # Containers run as root and write into the mounted tree, so plain rm fails.
    ssh(f"sudo -n rm -rf {REMOTE} || rm -rf {REMOTE}")
    code, out, _ = ssh(f"docker ps -a --format '{{{{.Names}}}}' | grep -c '^slyled-qa-'; "
                       f"docker network ls --format '{{{{.Name}}}}' | grep -c '^{NET}$'; test -d {REMOTE} && echo dir || echo nodir")
    left = out.split()
    ok("no slyled-qa containers, network or files left", left[:2] == ["0", "0"] and "nodir" in left, left)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["up", "smoke", "suites", "peer966", "down"])
    ap.add_argument("--tag", default="2.2.0")
    ap.add_argument("--commit", default="origin/main")
    ap.add_argument("--only", default="", help="substring filter on the suite list")
    a = ap.parse_args()
    {"up": lambda: up(a.tag), "smoke": smoke, "suites": lambda: suites(a.commit, a.only), "peer966": lambda: peer966(a.commit),
     "down": down}[a.step]()
    print(f"\n{_p} passed, {_f} failed out of {_p + _f} tests")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
