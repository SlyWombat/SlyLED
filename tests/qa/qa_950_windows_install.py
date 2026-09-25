#!/usr/bin/env python3
"""QA acceptance for #950 — the installed Windows build on davebook-5.

Run with the Windows QA venv (it drives PowerShell and talks to localhost):
  %USERPROFILE%\\.venvs\\slyled-qa-win\\Scripts\\python.exe tests\\qa\\qa_950_windows_install.py <step> [...]

Steps (run in order; each is safe to repeat):
  fetch  --tag <release-tag> [--sha-setup HEX] [--sha-exe HEX]   download + verify SHA-256
  install                     silent Inno install (ONE UAC prompt for the operator)
  check                       installed-build checks + launch + HTTP + HinksPix discovery/probe
  live   [--rgb R,G,B] [--seconds N]   eaves solid colour via SlyLED's own sACN engine
                                       (operator watches the garage), then blackout
  uninstall                   silent uninstall (UAC prompt) + leftover check + remove test data

Nothing is written to the HinksPix config: `live` only streams pixels into the
layout the unit already holds (port 17 = 200 x WS2811, universes 1-2).
Operator rule: the machine is left clean — always finish with `uninstall`.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "SlyWombat/SlyLED"
WORK = Path(os.environ.get("TEMP", ".")) / "slyled-qa-950"
PORT = 8080          # replaced at runtime by the installed port.txt when present
BASE = f"http://127.0.0.1:{PORT}"


def use_installed_port():
    """The installer lets the operator pick the port (and remembers a previous
    one), so read {app}\\port.txt instead of assuming 8080."""
    global PORT, BASE
    e = uninstall_entry() or {}
    loc = (e.get("InstallLocation") or "").rstrip("\\")
    if loc:
        code, out, _ = ps(f"Get-Content '{loc}\\port.txt' -ErrorAction SilentlyContinue")
        if out.strip().isdigit():
            PORT = int(out.strip())
            BASE = f"http://127.0.0.1:{PORT}"
            FW_PORTS["tcp"] = {PORT}
    print(f"  (orchestrator port: {PORT})")
HINKSPIX = "192.168.10.6"
FW_PORTS = {"tcp": {PORT}, "udp": {4210, 4211, 5568, 6454}}

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


def ps(script, timeout=300):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def http(path, method="GET", body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
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


def uninstall_entry():
    code, out, _ = ps(
        "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*, "
        "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*, "
        "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* -ErrorAction SilentlyContinue"
        " | Where-Object DisplayName -like '*SlyLED*' | Select-Object DisplayName,DisplayVersion,InstallLocation,"
        "UninstallString | ConvertTo-Json -Compress")
    try:
        j = json.loads(out) if out else None
    except ValueError:
        return None
    return j[0] if isinstance(j, list) else j


def firewall_rules():
    code, out, _ = ps(
        "Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object DisplayName -like '*SlyLED*' | "
        "ForEach-Object { $pf = $_ | Get-NetFirewallPortFilter; [pscustomobject]@{n=$_.DisplayName;"
        "p=\"$($pf.Protocol)\";l=\"$($pf.LocalPort)\";e=\"$($_.Enabled)\"} } | ConvertTo-Json -Compress")
    try:
        j = json.loads(out) if out else []
    except ValueError:
        return []
    return j if isinstance(j, list) else [j]


# ── steps ────────────────────────────────────────────────────────────────────

def step_fetch(a):
    print(f"\n== fetch release {a.tag}")
    WORK.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["gh", "release", "download", a.tag, "-R", REPO, "--clobber", "-D", str(WORK),
                        "-p", "SlyLED-Setup.exe", "-p", "SlyLED.exe"], capture_output=True, text=True)
    ok("gh release download", r.returncode == 0, r.stderr)
    for name, want in (("SlyLED-Setup.exe", a.sha_setup), ("SlyLED.exe", a.sha_exe)):
        f = WORK / name
        if not f.exists():
            if name == "SlyLED-Setup.exe":
                ok(f"{name} present", False)
            else:
                print(f"  {name}: not a release asset (the installer embeds it) - skipped")
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        print(f"  {name}: {f.stat().st_size} bytes sha256={h}")
        if want:
            ok(f"{name} SHA-256 matches the coder's", h.lower() == want.lower(), h)


def step_install(a):
    print("\n== install (silent; approve the UAC prompt on the desktop)")
    ok("nothing SlyLED installed beforehand", uninstall_entry() is None, uninstall_entry())
    setup = WORK / "SlyLED-Setup.exe"
    log = WORK / "install.log"
    code, out, err = ps(
        f"$p = Start-Process -FilePath '{setup}' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"
        f"'/LOG=\"{log}\"' -Verb RunAs -Wait -PassThru; $p.ExitCode", timeout=900)
    print(f"  installer exit: {out} {err[-200:]}")
    ok("installer exit code 0", out.strip().endswith("0"), (out, err))
    e = uninstall_entry()
    print(f"  uninstall entry: {e}")
    ok("installed (Add/Remove Programs entry)", bool(e), e)


def step_check(a):
    print("\n== installed build")
    e = uninstall_entry() or {}
    loc = (e.get("InstallLocation") or "").rstrip("\\")
    exe = Path(loc) / "SlyLED.exe" if loc else None
    code, out, _ = ps(f"Get-Content '{loc}\\port.txt' -ErrorAction SilentlyContinue")
    ok("silent install uses the default port 8080 (not an inherited one)", out.strip() == "8080", f"port.txt={out.strip()}")
    ok("SlyLED.exe in the install location", bool(exe) and ps(f"Test-Path '{exe}'")[1] == "True", loc)
    code, out, _ = ps("Get-ChildItem \"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\","
                      "\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\" -Recurse -Filter *SlyLED*.lnk "
                      "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName")
    ok("Start menu shortcut", "SlyLED" in out, out)
    rules = firewall_rules()
    print("  firewall: " + "; ".join(f"{r['n']} {r['p']}/{r['l']} {r['e']}" for r in rules))
    for proto, ports in FW_PORTS.items():
        for port in sorted(ports):
            ok(f"firewall rule {proto.upper()} {port}",
               any(str(port) in r.get("l", "") and r.get("p", "").lower() == proto for r in rules), rules)

    print("\n== launch")
    if http("/status")[0] != 200:
        ps(f"Start-Process -FilePath '{exe}'")
    up = False
    for _ in range(60):
        time.sleep(1)
        if http("/status")[0] == 200:
            up = True
            break
    ok("server answers /status after launch", up)
    s, st = http("/status")
    print(f"  status: {json.dumps(st)[:300]}")
    ok("platform windows", isinstance(st, dict) and str(st.get("platform", "")).startswith("windows"), st)
    ok("version matches Add/Remove Programs", isinstance(st, dict) and st.get("version") == e.get("DisplayVersion"),
       (st.get("version") if isinstance(st, dict) else st, e.get("DisplayVersion")))
    ok("UDP listener bound", isinstance(st, dict) and (st.get("udpListener") or {}).get("ok") is True, st)
    code, out, _ = ps("Get-Process SlyLED -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path")
    ok("running from the install location (frozen build)", loc and loc.lower() in out.lower(), out)
    for path in ("/help", "/api/settings", "/api/firmware/ports", "/api/dmx/interfaces"):
        s, b = http(path)
        ok(f"{path} 200", s == 200, b)
    code, out, _ = ps("Test-Path \"$env:APPDATA\\SlyLED\\data\"")
    ok("data in %APPDATA%\\SlyLED\\data", out == "True", out)

    print("\n== HinksPix from the installed build")
    http("/api/hinkspix/discover", "POST", {})
    r = {}
    for _ in range(60):
        s, r = http("/api/hinkspix/discover")
        if isinstance(r, dict) and not r.get("pending"):
            break
        time.sleep(0.3)
    found = [f.get("ip") for f in (r.get("found") or [])] if isinstance(r, dict) else []
    print(f"  sweep: elapsedMs={r.get('elapsedMs') if isinstance(r, dict) else r} found={found}")
    ok("#949 sweep finds .6", HINKSPIX in found, r)
    s, add = http("/api/children", "POST", {"ip": HINKSPIX})
    cid = add.get("id") if isinstance(add, dict) else None
    ok("add .6 as child", s == 200 and cid is not None, add)
    if cid is not None:
        s, _ = http(f"/api/hinkspix/{cid}/probe", "POST", {})
        ok("probe", s == 200)
        s, dc = http(f"/api/hinkspix/{cid}/device-config")
        ports = ((dc or {}).get("device") or {}).get("ports") if isinstance(dc, dict) else None
        ok("device-config reads 32 ports", isinstance(ports, dict) and len(ports) == 32, str(dc)[:200])
        if isinstance(ports, dict):
            print(f"  device port 17: {ports.get('17')}")


def _child_id():
    s, kids = http("/api/children")
    lst = kids if isinstance(kids, list) else (kids.get("children", []) if isinstance(kids, dict) else [])
    return next((k["id"] for k in lst if k.get("ip") == HINKSPIX), None)


def step_live(a):
    print("\n== live action through SlyLED's own sACN engine (no device config write)")
    cid = _child_id()
    if cid is None:
        ok("HinksPix child present (run `check` first)", False)
        return
    s, b = http(f"/api/hinkspix/{cid}", "PUT", {
        "baseUniverse": 1, "protocol": "e131",
        "ports": [{"port": 17, "leds": 200, "enabled": True, "protocol": "ws2811", "colorOrder": "RGB"}]})
    ok("SlyLED layout = device layout (port 17, 200 px, universe 1)", s == 200, b)
    s, b = http(f"/api/hinkspix/{cid}/fixtures-from-ports", "POST", {})
    ok("fixture bound to port 17", s == 200, b)
    s0, st0 = http("/api/dmx/status")
    was_running = isinstance(st0, dict) and any((st0.get(k) or {}).get("running") for k in ("sacn", "artnet"))
    s, b = http("/api/dmx/settings", "POST", {"protocol": "sacn"})
    ok("DMX engine protocol -> sACN", s == 200, b)
    s, b = http("/api/dmx/start", "POST", {"protocol": "sacn"})
    ok("sACN engine started", s == 200, b)
    r, g, bl = (int(x) for x in a.rgb.split(","))
    s, b = http(f"/api/children/{cid}/action", "POST", {"type": 1, "r": r, "g": g, "b": bl, "allStrings": True})
    ok(f"solid ({r},{g},{bl}) streamed to the HinksPix fixture", s == 200 and isinstance(b, dict) and b.get("streamed"), b)
    print(f"  >>> OPERATOR: eaves should be solid ({r},{g},{bl}) for {a.seconds}s — look at the garage now <<<",
          flush=True)
    time.sleep(a.seconds)
    # Clean up for real: STOP the live action (clears it and blacks out its
    # spans). A type-0 "blackout" action is itself a live action that keeps
    # streaming zeros at 40 Hz and fights any show started later (QA left
    # one running on 2026-09-24 and the operator's first show flickered).
    s, b = http(f"/api/children/{cid}/action/stop", "POST", {})
    ok("live action stopped and cleared", s == 200 and isinstance(b, dict) and b.get("ok"), b)
    # Leave the output engine as found. Stopping it made the operator's next
    # show "run" with no output (#958).
    if not was_running:
        http("/api/dmx/stop", "POST", {})
    s, st = http("/api/dmx/status")
    ok("DMX engine left as found", isinstance(st, dict) and
       any((st.get(k) or {}).get("running") for k in ("sacn", "artnet")) == was_running, st)


def step_uninstall(a):
    print("\n== uninstall (silent; approve the UAC prompt)")
    ps("Get-Process SlyLED -ErrorAction SilentlyContinue | Stop-Process -Force")
    e = uninstall_entry()
    if not e:
        ok("was installed", False)
    else:
        unins = e["UninstallString"].strip('"')
        code, out, err = ps(f"$p = Start-Process -FilePath '{unins}' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES',"
                            f"'/NORESTART' -Verb RunAs -Wait -PassThru; $p.ExitCode", timeout=600)
        print(f"  uninstaller exit: {out} {err[-200:]}")
        ok("uninstaller exit code 0", out.strip().endswith("0"), (out, err))
        time.sleep(3)
        loc = (e.get("InstallLocation") or "").rstrip("\\")
        left = ps(f"Get-ChildItem -Force '{loc}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name")[1]
        ok("install folder removed", ps(f"Test-Path '{loc}'")[1] == "False", f"{loc} still holds: {left}")
    ok("Add/Remove Programs entry gone", uninstall_entry() is None, uninstall_entry())
    ok("firewall rules removed", not firewall_rules(), firewall_rules())
    ok("server no longer answering", http("/status", timeout=3)[0] is None)
    # Test data from a fresh QA install (nothing was installed before `install`).
    ps("Remove-Item -Recurse -Force \"$env:APPDATA\\SlyLED\" -ErrorAction SilentlyContinue")
    ok("%APPDATA%\\SlyLED test data removed", ps("Test-Path \"$env:APPDATA\\SlyLED\"")[1] == "False")
    ps(f"Remove-Item -Recurse -Force '{WORK}' -ErrorAction SilentlyContinue")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "install", "check", "live", "uninstall"])
    ap.add_argument("--tag")
    ap.add_argument("--sha-setup")
    ap.add_argument("--sha-exe")
    ap.add_argument("--rgb", default="0,0,255")
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    if a.step in ("check", "live", "uninstall"):
        use_installed_port()
    {"fetch": step_fetch, "install": step_install, "check": step_check,
     "live": step_live, "uninstall": step_uninstall}[a.step](a)
    print(f"\n{_p} passed, {_f} failed out of {_p + _f} tests")
    sys.exit(1 if _f else 0)


if __name__ == "__main__":
    main()
