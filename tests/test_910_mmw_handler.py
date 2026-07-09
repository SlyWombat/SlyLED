#!/usr/bin/env python3
"""test_910_mmw_handler.py — #910 0x70 MMW_TARGETS handler + synthetic
end-to-end radar pipeline (#910 → #912 → #900).

Drives crafted 0x70 datagrams through the real `_UDP_DISPATCH` handler
(no socket — the #901 pattern) against a radar fixture created through
the real API at a known pose, and asserts on GET /api/objects — the
same surface every downstream consumer (3D view, Spotlight Follow,
show generator) reads.

Narrative:
  1. Create a radar fixture (radarNode "MMW-A1B2") at pose
     pos=(1000, 0, 1500) rotation [0,0,0]; announce the node via a real
     PONG so sender-ip → hostname → fixture resolution runs end-to-end.
  2. Walk a synthetic person in SENSOR frame (x=300 mm lateral, y
     2000→2900 mm boresight): no person object before the 3rd frame
     (M-of-N); afterwards exactly one radar-source person whose stage
     position matches the projected line (stage ≈ (1300, y)) within
     tolerance.
  3. Keepalives (count=0) coast the track — object held alive with a
     frozen position — then past coast_ms + TTL the object expires.
  4. A second radar fixture at a crossing pose (design doc §3.3) seeing
     the same physical point yields ONE fused object (#900/#896).
  5. Unbound senders (unknown hostname, ≥2 candidate fixtures) create
     nothing and increment the mmwUnbound counter surfaced on
     /api/status beside the #901 listener health; count>3 increments
     mmwMalformed. Single-radar heuristic binds when exactly one
     enabled radar fixture exists.

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_910_mmw_handler.py
"""

import math
import os
import struct
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-910-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402
from parent_server import app  # noqa: E402

results = []
_created_fixtures = []

IP1, HOST1 = "10.77.0.10", "MMW-A1B2"
IP2, HOST2 = "10.77.0.11", "MMW-C3D4"

# Fast-expiry tuning so the coast/expiry narrative runs in seconds.
# These are the bench-tunable (#908) knobs on the live engine; the
# defaults (3000 ms / 2 s) are asserted separately below.
COAST_MS = 400.0
TTL_S = 0.6


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _hdr(cmd):
    return struct.pack("<HBBI", ps.UDP_MAGIC, ps.UDP_VERSION, cmd, 0)


def _mmw_packet(seq, targets, flags=0x01):
    """Craft a full 36-byte 0x70 datagram per MmwProtocol.h:
    seq(u16) count(u8) flags(u8) + 3 × {xMm i16, yMm i16, speedCms i16,
    resMm u16}, unused slots zeroed."""
    payload = struct.pack("<HBB", seq, len(targets), flags)
    for i in range(3):
        x, y, spd, res = targets[i] if i < len(targets) else (0, 0, 0, 0)
        payload += struct.pack("<hhhH", x, y, spd, res)
    return _hdr(ps.CMD_MMW_TARGETS) + payload


def _dispatch(ip, pkt):
    entry = ps._UDP_DISPATCH[ps.CMD_MMW_TARGETS]
    assert len(pkt) >= entry[0], "crafted packet under the dispatch gate"
    entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_MMW_TARGETS), pkt)


def _pong_packet(hostname):
    """v5 PONG (142 bytes) with stringCount=0 — the radar node's boot
    self-announce shape (MmwProtocol.h PongPayload)."""
    payload = bytearray()
    payload += hostname.encode().ljust(10, b"\x00")
    payload += b"Radar Node".ljust(16, b"\x00")
    payload += b"Rd-03D MMwave people radar".ljust(32, b"\x00")
    payload += bytes([0])                       # stringCount = 0
    for _ in range(8):
        payload += struct.pack("<HHBBHB", 0, 0, 0, 0, 0, 0)
    payload += bytes([0, 1, 0])                 # fw 0.1.0
    return _hdr(ps.CMD_PONG) + bytes(payload)


def _announce(ip, hostname):
    entry = ps._UDP_DISPATCH[ps.CMD_PONG]
    entry[1](ip, 4210, (ps.UDP_MAGIC, ps.UDP_VERSION, ps.CMD_PONG),
             _pong_packet(hostname))


def _radar_objects(c):
    r = c.get("/api/objects")   # triggers reap + #900 fusion
    objs = r.get_json() or []
    return [o for o in objs
            if o.get("objectType") == "person"
            and (o.get("source") or {}).get("type") == "radar"]


def _mk_radar(c, name, node, x, y, z, rotation):
    r = c.post("/api/fixtures", json={
        "name": name, "type": "point", "fixtureType": "radar",
        "radarNode": node, "rotation": rotation})
    fid = (r.get_json() or {}).get("id")
    assert r.status_code == 200 and fid is not None, r.data
    _created_fixtures.append(fid)
    # Positions ride the layout store, exactly like camera fixtures.
    lay = c.get("/api/layout").get_json() or {}
    children = [ch for ch in lay.get("children", []) if ch.get("id") != fid]
    children.append({"id": fid, "x": x, "y": y, "z": z})
    r = c.post("/api/layout", json={"children": children, "force": True})
    assert r.status_code == 200, r.data
    return fid


def _counter(c, key):
    u = (c.get("/api/status").get_json() or {}).get("udpListener") or {}
    return u.get(key)


# ── setup + registration surface ─────────────────────────────────────────────

def run_setup_checks(c):
    ok("0x70 registered as (36, _handle_mmw_targets) (#901 one-liner)",
       ps._UDP_DISPATCH.get(ps.CMD_MMW_TARGETS) is not None
       and ps._UDP_DISPATCH[ps.CMD_MMW_TARGETS][0] == 36
       and ps._UDP_DISPATCH[ps.CMD_MMW_TARGETS][1] is ps._handle_mmw_targets)
    ok("0x71 MMW_CONFIG stays unimplemented",
       ps.CMD_MMW_CONFIG == 0x71 and 0x71 not in ps._UDP_DISPATCH)
    ok("bench-default tuning: coast 3000 ms, M-of-N = 3, gate 500 mm",
       ps._radar_fusion.coast_ms == 3000.0
       and ps._radar_fusion.confirm_frames == 3
       and ps._radar_fusion.gate_mm == 500.0)
    u = (c.get("/api/status").get_json() or {}).get("udpListener") or {}
    ok("mmwUnbound / mmwMalformed surfaced beside #901 listener health",
       "mmwUnbound" in u and "mmwMalformed" in u, repr(sorted(u)))


# ── 2. walk → M-of-N → projected line ───────────────────────────────────────

def run_walk_e2e(c, fid1):
    _announce(IP1, HOST1)
    ok("PONG announce recorded for the radar node",
       (ps._recent_pongs.get(IP1) or {}).get("hostname") == HOST1,
       repr(ps._recent_pongs.get(IP1)))

    ok("no radar person objects before any frame", _radar_objects(c) == [])

    # Sensor-frame walk: x = 300 mm lateral-right, y = 2000 → 2900 mm.
    # Fixture at (1000, 0, 1500) rot [0,0,0] → stage line x=1300, y=yMm.
    seq = 0
    for i in range(2):
        _dispatch(IP1, _mmw_packet(seq, [(300, 2000 + i * 100, 150, 120)]))
        seq += 1
        time.sleep(0.03)
        ok(f"frame {i + 1}: still no object (M-of-N pending)",
           _radar_objects(c) == [], repr(_radar_objects(c)))

    _dispatch(IP1, _mmw_packet(seq, [(300, 2200, 150, 120)]))
    seq += 1
    objs = _radar_objects(c)
    ok("3rd consecutive frame → exactly one person object", len(objs) == 1,
       repr(objs))
    if objs:
        o = objs[0]
        ok("object is a temporal pink person",
           o.get("_temporal") and o.get("color") == "#f472b6"
           and o.get("mobility") == "moving", repr(o))
        ok("source provenance {type, fixtureId, node} stamped",
           o.get("source") == {"type": "radar", "fixtureId": fid1,
                               "node": HOST1}, repr(o.get("source")))
        px, py = o["transform"]["pos"][0], o["transform"]["pos"][1]
        ok("confirmed position on the projected line (stage x≈1300)",
           abs(px - 1300) < 150 and abs(py - 2100) < 250,
           f"pos=({px:.0f}, {py:.0f})")

    # Continue the walk; positions must track the projected line.
    errs = []
    for i in range(3, 10):
        y = 2000 + i * 100
        _dispatch(IP1, _mmw_packet(seq, [(300, y, 150, 120)]))
        seq += 1
        time.sleep(0.03)
        objs = _radar_objects(c)
        if len(objs) == 1:
            px, py = objs[0]["transform"]["pos"][:2]
            errs.append(math.hypot(px - 1300, py - y))
    ok("walk stays a single object end-to-end", len(objs) == 1, repr(objs))
    ok("stage positions match the projected walk within 250 mm",
       errs and max(errs) < 250,
       f"errs={[f'{e:.0f}' for e in errs]}")
    # Steady-state CV-KF lag for a 100 mm/frame walk is ~1.5 frames of
    # motion (~150 mm); the bound is the lag envelope, not a wish.
    ok("final position within the 200 mm KF lag envelope of (1300, 2900)",
       errs and errs[-1] < 200, f"final err {errs[-1]:.0f} mm")
    return seq


def run_coast_expire(c, seq):
    # Shrink the live engine's knobs so coast+TTL expiry runs fast.
    ps._radar_fusion.coast_ms = COAST_MS
    ps._radar_fusion.person_ttl_s = TTL_S
    # Refresh the confirmed track once so the shrunken TTL applies.
    _dispatch(IP1, _mmw_packet(seq, [(300, 2900, 0, 120)]))
    seq += 1
    with ps._lock:
        for o in ps._temporal_objects:
            if (o.get("source") or {}).get("type") == "radar":
                o["ttl"] = TTL_S
                o["_expiresAt"] = time.time() + TTL_S

    # Keepalives (count=0) within the coast window hold the object alive.
    time.sleep(0.2)
    _dispatch(IP1, _mmw_packet(seq, []))
    seq += 1
    objs = _radar_objects(c)
    ok("keepalive within coast window keeps the object alive",
       len(objs) == 1, repr(objs))
    if objs:
        px, py = objs[0]["transform"]["pos"][:2]
        ok("coasting holds the last position (static-person fade)",
           abs(px - 1300) < 150 and abs(py - 2900) < 250,
           f"pos=({px:.0f}, {py:.0f})")

    # Past coast_ms the track drops; keepalives no longer refresh the
    # TTL, so the object expires on the next reap.
    time.sleep(COAST_MS / 1000.0 + 0.1)
    _dispatch(IP1, _mmw_packet(seq, []))
    seq += 1
    time.sleep(TTL_S + 0.1)
    _dispatch(IP1, _mmw_packet(seq, []))
    seq += 1
    ok("past coast + TTL the person object expires",
       _radar_objects(c) == [], repr(_radar_objects(c)))
    return seq


# ── 4. two radars, one person → ONE fused object ────────────────────────────

def run_cross_sensor(c, fid1, fid2):
    _announce(IP2, HOST2)
    # Physical person at stage (1300, 2500):
    #   radar 1 @ (1000, 0, 1500) rot [0,0,0]   → sensor (300, 2500)
    #   radar 2 @ (4300, 2500, 1500) rot [0,0,-90] (boresight → stage -X,
    #   crossing view per §3.3) → sensor (0, 3000)
    for i in range(5):
        _dispatch(IP1, _mmw_packet(100 + i, [(300, 2500, 0, 120)]))
        _dispatch(IP2, _mmw_packet(200 + i, [(0, 3000, 0, 120)]))
        time.sleep(0.02)
    objs = _radar_objects(c)
    ok("two radars seeing the same person fuse to ONE object (#900)",
       len(objs) == 1, repr([(o.get('id'), o.get('transform', {}).get('pos'))
                             for o in objs]))
    if len(objs) == 1:
        o = objs[0]
        px, py = o["transform"]["pos"][:2]
        ok("fused position at the physical person (1300, 2500)",
           abs(px - 1300) < 150 and abs(py - 2500) < 150,
           f"pos=({px:.0f}, {py:.0f})")
        srcs = o.get("_fusionSources") or []
        ok("fused object credits both radar sources",
           o.get("_fusionCams") == 2
           and all(s.get("sourceType") == "radar" for s in srcs),
           repr(srcs))
    # Both trackers keep updating the fused survivor (#896 forwarding).
    for i in range(3):
        _dispatch(IP1, _mmw_packet(110 + i, [(300, 2500, 0, 120)]))
        _dispatch(IP2, _mmw_packet(210 + i, [(0, 3000, 0, 120)]))
        time.sleep(0.02)
    objs = _radar_objects(c)
    ok("post-merge updates land on the survivor, still one object",
       len(objs) == 1, repr(objs))


# ── 5. unbound / malformed / single-radar heuristic ─────────────────────────

def run_unbound_and_malformed(c):
    before_objs = len(_radar_objects(c))
    before = _counter(c, "mmwUnbound")
    # Unknown sender, ≥2 enabled radar fixtures → never guess.
    for i in range(3):
        _dispatch("10.77.0.99", _mmw_packet(i, [(0, 1500, 0, 120)]))
    after = _counter(c, "mmwUnbound")
    ok("unbound sender increments mmwUnbound (never guesses among 2)",
       after == before + 3, f"{before} -> {after}")
    ok("unbound packets create nothing",
       len(_radar_objects(c)) == before_objs)

    before = _counter(c, "mmwMalformed")
    bad = (_hdr(ps.CMD_MMW_TARGETS)
           + struct.pack("<HBB", 0, 7, 0x01)   # count=7 > 3-slot limit
           + b"\x00" * 24)
    _dispatch(IP1, bad)
    after = _counter(c, "mmwMalformed")
    ok("count>3 increments mmwMalformed and is dropped",
       after == before + 1, f"{before} -> {after}")


def run_single_radar_heuristic(c, fid1, fid2):
    # Start from a clean tracker/object slate so the assertion below is
    # about exactly the heuristic-bound frames.
    ps._radar_fusion.clear()
    with ps._lock:
        ps._temporal_objects[:] = [
            o for o in ps._temporal_objects
            if (o.get("source") or {}).get("type") != "radar"]
    # Disable radar 2 → exactly one enabled radar fixture remains; an
    # unknown-hostname sender now binds to it (logged once).
    r = c.put(f"/api/fixtures/{fid2}", json={"radarEnabled": False})
    ok("disable second radar via PUT", r.status_code == 200)
    ip = "10.77.0.55"   # never announced a PONG
    for i in range(3):
        _dispatch(ip, _mmw_packet(i, [(-400, 1800, 0, 120)]))
        time.sleep(0.02)
    objs = _radar_objects(c)
    ok("single-radar heuristic binds an unmatched sender",
       len(objs) == 1
       and (objs[0].get("source") or {}).get("fixtureId") == fid1,
       repr(objs))
    if objs:
        px, py = objs[0]["transform"]["pos"][:2]
        ok("heuristic-bound frames project through fixture 1's pose",
           abs(px - (1000 - 400)) < 150 and abs(py - 1800) < 150,
           f"pos=({px:.0f}, {py:.0f})")
    # Disabled radar's own node is ignored entirely.
    before = _counter(c, "mmwUnbound")
    _dispatch(IP2, _mmw_packet(0, [(0, 2000, 0, 120)]))
    ok("disabled radar's node counts as unbound (radarEnabled gate)",
       _counter(c, "mmwUnbound") == before + 1)


def _cleanup(c):
    ps._radar_fusion.clear()
    ps._radar_fusion.coast_ms = 3000.0
    ps._radar_fusion.person_ttl_s = 2.0
    with ps._lock:
        ps._temporal_objects[:] = [
            o for o in ps._temporal_objects
            if (o.get("source") or {}).get("type") != "radar"]
        ps._fixtures[:] = [f for f in ps._fixtures
                           if f.get("id") not in set(_created_fixtures)]
        ps._save("fixtures", ps._fixtures)
    for ip in (IP1, IP2):
        ps._recent_pongs.pop(ip, None)


def main():
    c = app.test_client()
    try:
        run_setup_checks(c)
        fid1 = _mk_radar(c, "Radar A", HOST1, 1000, 0, 1500, [0, 0, 0])
        fid2 = _mk_radar(c, "Radar B", HOST2, 4300, 2500, 1500, [0, 0, -90])
        seq = run_walk_e2e(c, fid1)
        seq = run_coast_expire(c, seq)
        run_cross_sensor(c, fid1, fid2)
        run_unbound_and_malformed(c)
        run_single_radar_heuristic(c, fid1, fid2)
    finally:
        _cleanup(c)
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
