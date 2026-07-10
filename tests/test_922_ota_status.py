#!/usr/bin/env python3
"""test_922_ota_status.py — #922 0x51 CMD_OTA_STATUS handler + Firmware-tab
surfacing.

Drives crafted 0x51 datagrams through the real `_UDP_DISPATCH` handler
(no socket — the #901/#910 pattern) and asserts on the surfaced state:

  1. Registration surface: 0x51 landed as exactly the one-line
     registration `CMD_OTA_STATUS: (10, _handle_ota_status)`.
  2. A crafted OtaStatusPayload {status u8, progress u8} records
     {status, statusName, progress, updatedAt} in `_ota_status_live`
     keyed by sender IP, and bumps child.seen (#822 — a mid-OTA board
     stops answering PING while it reports progress).
  3. A full phase walk (downloading 0→90 / verifying / applying /
     success) overwrites in place — last report wins.
  4. Unknown status codes surface as "unknown(N)" instead of crashing.
  5. /api/firmware/check rows carry the per-child `ota` field for a
     reporting child and None for a silent one (GitHub fetches are
     monkeypatched — no network).
  6. /api/reset clears the store.

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_922_ota_status.py
"""

import os
import struct
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-922-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402
from parent_server import app  # noqa: E402
import orch_firmware  # noqa: E402

results = []

IP_REPORTER = "10.88.0.20"   # child that sends 0x51
IP_SILENT   = "10.88.0.21"   # child that never reports


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _hdr(cmd):
    return struct.pack("<HBBI", ps.UDP_MAGIC, ps.UDP_VERSION, cmd, 0)


def _ota_packet(status, progress):
    """Craft a full 10-byte 0x51 datagram per main/Protocol.h
    OtaStatusPayload: status(u8) + progress(u8)."""
    return _hdr(ps.CMD_OTA_STATUS) + struct.pack("<BB", status, progress)


def _dispatch(ip, pkt):
    entry = ps._UDP_DISPATCH[ps.CMD_OTA_STATUS]
    assert len(pkt) >= entry[0], "crafted packet under the dispatch gate"
    entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_OTA_STATUS), pkt)


# ── 1. registration surface ──────────────────────────────────────────────────

def run_registration_checks():
    ok("0x51 registered as (10, _handle_ota_status) (#901 one-liner)",
       ps._UDP_DISPATCH.get(ps.CMD_OTA_STATUS) is not None
       and ps._UDP_DISPATCH[ps.CMD_OTA_STATUS][0] == 10
       and ps._UDP_DISPATCH[ps.CMD_OTA_STATUS][1] is ps._handle_ota_status)
    ok("gate matches the wire size (8-byte header + 2-byte payload)",
       ps._UDP_DISPATCH[ps.CMD_OTA_STATUS][0]
       == len(_ota_packet(0, 0)))


# ── 2/3/4. crafted datagrams → _ota_status_live ─────────────────────────────

def run_record_checks(c):
    ok("store starts empty for the reporter IP",
       IP_REPORTER not in ps._ota_status_live)

    t0 = time.time()
    _dispatch(IP_REPORTER, _ota_packet(1, 30))
    st = ps._ota_status_live.get(IP_REPORTER)
    ok("first report recorded keyed by sender IP", st is not None, repr(st))
    if st:
        ok("record carries {status, statusName, progress, updatedAt}",
           st.get("status") == 1 and st.get("statusName") == "downloading"
           and st.get("progress") == 30
           and t0 <= st.get("updatedAt", 0) <= time.time() + 1, repr(st))

    child = next((ch for ch in ps._children if ch.get("ip") == IP_REPORTER),
                 None)
    ok("child.seen bumped by the report (#822 mid-OTA liveness)",
       child is not None and child.get("seen", 0) >= int(t0), repr(child))

    # Phase walk: last report wins, in place.
    for status, prog, name in [(1, 90, "downloading"), (2, 90, "verifying"),
                               (3, 90, "applying"), (4, 100, "success")]:
        _dispatch(IP_REPORTER, _ota_packet(status, prog))
        st = ps._ota_status_live.get(IP_REPORTER) or {}
        if st.get("status") != status or st.get("statusName") != name \
                or st.get("progress") != prog:
            ok(f"phase walk: {name} ({prog}%) overwrites in place", False,
               repr(st))
            break
    else:
        ok("phase walk downloading→verifying→applying→success overwrites "
           "in place", True)
    ok("one IP → one record (no history growth)",
       list(ps._ota_status_live) == [IP_REPORTER],
       repr(sorted(ps._ota_status_live)))

    _dispatch(IP_REPORTER, _ota_packet(42, 7))
    st = ps._ota_status_live.get(IP_REPORTER) or {}
    ok("unknown status code surfaces as unknown(42)",
       st.get("status") == 42 and st.get("statusName") == "unknown(42)"
       and st.get("progress") == 7, repr(st))


# ── 5. /api/firmware/check surfacing ─────────────────────────────────────────

def run_check_surface(c):
    # No network: pin the GitHub release + registry fetches.
    orig_rel = orch_firmware._fetch_github_release
    orig_reg = orch_firmware._load_registry_for_ota
    orig_wifi = dict(ps._wifi)
    orch_firmware._fetch_github_release = lambda: {
        "version": "9.9.9", "tag": "v9.9.9", "assets": [], "url": ""}
    orch_firmware._load_registry_for_ota = lambda: [
        {"id": "child-led-esp32", "board": "esp32", "version": "9.9.9"}]
    ps._wifi["ssid"] = "test-ssid"
    ps._wifi["password"] = "test-pw"
    try:
        # A terminal state to make the assertion unambiguous.
        _dispatch(IP_REPORTER, _ota_packet(5, 40))
        r = c.get("/api/firmware/check")
        ok("firmware/check responds 200 with pinned fetches",
           r.status_code == 200, r.data[:200])
        rows = (r.get_json() or {}).get("children", [])
        rep = next((x for x in rows if x.get("ip") == IP_REPORTER), None)
        sil = next((x for x in rows if x.get("ip") == IP_SILENT), None)
        ok("reporting child row carries the live ota record",
           rep is not None and isinstance(rep.get("ota"), dict)
           and rep["ota"].get("status") == 5
           and rep["ota"].get("statusName") == "failed"
           and rep["ota"].get("progress") == 40
           and "updatedAt" in rep["ota"], repr(rep))
        ok("silent child row surfaces ota=None",
           sil is not None and "ota" in sil and sil["ota"] is None,
           repr(sil))
    finally:
        orch_firmware._fetch_github_release = orig_rel
        orch_firmware._load_registry_for_ota = orig_reg
        ps._wifi.clear()
        ps._wifi.update(orig_wifi)


# ── 6. reset clears the store ────────────────────────────────────────────────

def run_reset_clears(c):
    assert ps._ota_status_live, "precondition: store populated"
    r = c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
    ok("POST /api/reset accepted", r.status_code == 200, r.data[:200])
    ok("reset clears _ota_status_live", ps._ota_status_live == {},
       repr(ps._ota_status_live))


def main():
    c = app.test_client()
    run_registration_checks()
    for ip in (IP_REPORTER, IP_SILENT):
        r = c.post("/api/children", json={"ip": ip})
        assert r.status_code == 200, r.data
    try:
        run_record_checks(c)
        run_check_surface(c)
        run_reset_clears(c)
    finally:
        ps._ota_status_live.clear()
        with ps._lock:
            ps._children[:] = [ch for ch in ps._children
                               if ch.get("ip") not in (IP_REPORTER, IP_SILENT)]
            ps._save("children", ps._children)
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
