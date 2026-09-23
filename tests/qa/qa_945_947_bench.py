#!/usr/bin/env python3
"""QA bench acceptance for #945 (config management), #946 (guidance) and #947
(xLights import) against the operator's real HinksPix PRO.

WRITES TO THE REAL CONTROLLER and reboots it three times. End state is the
garage-eaves layout (port 17 = 200 x WS2811, universes 1-2).

Flow:
  1. import the operator's xLights show folder -> proposal (no writes)
  2. accept the proposal into a child pointing at the unit (+ fixtures)
  3. plan -> apply(wait) -> verify: device port 17 == 17,1,1,200,600,...
  4. change port 17 to 150 px -> apply(wait): the job snapshots the 200-px
     state first; device now 150 px
  5. restore that snapshot (wait) -> device back to 200 px, verify
Each device state is also checked with an independent direct BLK read.

  python tests/qa/qa_945_947_bench.py [--ip 192.168.10.6]
"""
import gzip
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-qa945-")
sys.path.insert(0, str(HERE.parent.parent / "desktop" / "shared"))
sys.path.insert(0, str(HERE))

import parent_server  # noqa: E402
from parent_server import app  # noqa: E402
from qa_943_hinkspix import add_child, do_apply, ok, ACKED  # noqa: E402
import qa_943_hinkspix as base  # noqa: E402

IP = sys.argv[sys.argv.index("--ip") + 1] if "--ip" in sys.argv else "192.168.10.6"
SHOW = ("/mnt/c/Users/DavidSeaman/OneDrive/My Documents/SlyMega Art Inc/"
        "Projects/Xlights - Home Eves")


def direct_row(port):
    blk = (port - 1) // 16
    req = urllib.request.Request(f"http://{IP}/Xlights_Board_Port_Config.cgi",
                                 headers={"Content-type": "text/plain", "BLK": str(blk)})
    raw = urllib.request.urlopen(req, timeout=20).read()
    raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    rows = {int(x["V"].split(",")[0]): x["V"] for x in json.loads(raw)["LIST"]}
    return rows[port]


def state_summary(res):
    st = res.get("state") or {}
    keep = {k: st.get(k) for k in ("ok", "kind", "phase", "err", "backupId",
                                   "backupWarnings", "verify", "inSync")
            if k in st}
    return json.dumps(keep)[:700]


def main():
    c = app.test_client()
    cid = 9450
    add_child(IP, cid)

    print("\n== 0. probe")
    r = c.post(f"/api/hinkspix/{cid}/probe")
    ok("probe", r.status_code == 200, r.data[:200])
    before = direct_row(17)
    print(f"  device port 17 before: {before}")

    print("\n== 1. import xLights show folder (Windows path form)")
    win = r"C:\Users\DavidSeaman\OneDrive\My Documents\SlyMega Art Inc\Projects\Xlights - Home Eves"
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": win, "cid": cid})
    jw = r.get_json() or {}
    print(f"  windows path -> {r.status_code} {json.dumps(jw)[:200]}")
    r = c.post("/api/hinkspix/import/xlights", json={"showFolder": SHOW, "cid": cid})
    prop = r.get_json() or {}
    ok("import (WSL path) 200", r.status_code == 200, r.data[:300])
    ok("import accepts a Windows path too (orchestrator may run on Windows)",
       "controller" in jw and "err" not in jw, json.dumps(jw)[:200])
    ptxt = json.dumps(prop)
    print(f"  proposal: {ptxt[:600]}")
    ok("proposal names port 17 with 200 px", '"port": 17' in ptxt and "200" in ptxt)
    ok("import stored nothing (child ports still empty)",
       not parent_server._children[-1]["hinks"].get("ports"))

    print("\n== 2. accept proposal (+ fixtures)")
    r = c.post(f"/api/hinkspix/{cid}/import/xlights/accept",
               json={"proposal": prop, "createFixtures": True})
    acc = r.get_json() or {}
    ok("accept 200", r.status_code == 200, r.data[:400])
    ports = parent_server._children[-1]["hinks"].get("ports") or []
    p17 = next((p for p in ports if int(p.get("port", 0)) == 17), {})
    ok("child port 17 = 200 leds after accept", p17.get("leds") == 200, p17)
    print(f"  accept: {json.dumps(acc)[:400]}")

    print("\n== 3. plan -> apply(wait) -> verify")
    r = c.get(f"/api/hinkspix/{cid}/plan")
    plan = r.get_json() or {}
    ok("plan 200", r.status_code == 200, r.data[:300])
    pc1 = [q for q in plan.get("requests", []) if '"BOARD":"1"' in ((q.get("headers") or {}).get("DATA") or "")]
    if not (pc1 and "17,1,1,200,600,0,0,0,100,1" in pc1[0]["headers"]["DATA"]):
        print("!! ABORT: plan does not write the eaves row — refusing to touch the unit")
        sys.exit(2)
    print("  findings: " + json.dumps([(f.get("level"), f.get("code")) for f in plan.get("findings", [])]))
    t = time.time()
    r = do_apply(c, cid)
    res = r.get_json() or {}
    print(f"  apply {r.status_code} in {time.time() - t:.0f}s: {state_summary(res)}")
    ok("apply job ok", res.get("ok") is True, state_summary(res))
    row = direct_row(17)
    ok("device port 17 == 17,1,1,200,600,0,0,0,100,1 (direct read)",
       row == "17,1,1,200,600,0,0,0,100,1", row)
    r = c.get(f"/api/hinkspix/{cid}/apply")
    st = r.get_json() or {}
    ok("GET apply reports inSync after apply", st.get("inSync") is True, json.dumps(st)[:300])
    lv = json.dumps(st.get("lastVerify"))
    print(f"  lastVerify: {lv[:400]}")

    print("\n== 4. change port 17 brightness to 50 -> apply(wait)")
    new_ports = [dict(p, brightness=50) if int(p.get("port", 0)) == 17 else p for p in ports]
    r = c.put(f"/api/hinkspix/{cid}", json={"ports": new_ports})
    ok("PUT brightness 50 200", r.status_code == 200, r.data[:300])
    p150 = c.get(f"/api/hinkspix/{cid}/plan").get_json() or {}
    pc = [q for q in p150.get("requests", []) if '"BOARD":"1"' in ((q.get("headers") or {}).get("DATA") or "")]
    if r.status_code != 200 or not (pc and "17,1,1,200,600,0,0,0,50,1" in pc[0]["headers"]["DATA"]) \
            or p150.get("universesUsed") != 2:
        print("!! ABORT before brightness apply: plan is not port 17 = 200 px @ brightness 50")
        print(f"   PUT {r.status_code} {r.data[:400]!r}")
        print(f"   plan universesUsed={p150.get('universesUsed')} err={p150.get('err')} "
              f"row={pc[0]['headers']['DATA'][:90] if pc else None}")
        print(f"   findings={[(f.get('level'), f.get('code')) for f in p150.get('findings', [])]}")
        sys.exit(2)
    r = do_apply(c, cid)
    res = r.get_json() or {}
    snap = (res.get("state") or {}).get("backupId")
    print(f"  apply {r.status_code}: {state_summary(res)}")
    ok("apply(brightness 50) job ok", res.get("ok") is True, state_summary(res))
    row150 = direct_row(17)
    ok("device port 17 now brightness 50 (17,1,1,200,600,0,0,0,50,1)", row150 == "17,1,1,200,600,0,0,0,50,1", row150)
    ok("apply recorded the pre-apply snapshot id", bool(snap), snap)

    print("\n== 5. restore the pre-apply snapshot -> 200 px again")
    r = c.get(f"/api/hinkspix/{cid}/restore?backupId={snap}")
    print(f"  restore preview: {r.status_code} {json.dumps(r.get_json())[:400]}")
    r = do_apply(c, cid, path="restore", extra={"backupId": snap})
    res = r.get_json() or {}
    print(f"  restore {r.status_code}: {state_summary(res)}")
    ok("restore job ok", res.get("ok") is True, state_summary(res))
    rowr = direct_row(17)
    ok("device port 17 back to 17,1,1,200,600,0,0,0,100,1 after restore",
       rowr == "17,1,1,200,600,0,0,0,100,1", rowr)
    e131 = urllib.request.urlopen(urllib.request.Request(
        f"http://{IP}/GetE131Data.cgi"), timeout=20).read()
    e131 = gzip.decompress(e131) if e131[:2] == b"\x1f\x8b" else e131
    print(f"  E131 after restore: {e131[:80]!r}")
    ok("universe table intact after restore (row 1 = 1,1,510,1,1,510)",
       e131.decode(errors="replace").startswith("1,1,510,1,1,510"), e131[:40])

    # put the operator's layout back in SlyLED state too (child is 200 px again)
    c.put(f"/api/hinkspix/{cid}", json={"ports": ports})

    if ACKED:
        print(f"acknowledged warnings: {ACKED}")
    print(f"\n{base._passed} passed, {base._failed} failed out of {base._passed + base._failed} tests")
    sys.exit(1 if base._failed else 0)


if __name__ == "__main__":
    main()
