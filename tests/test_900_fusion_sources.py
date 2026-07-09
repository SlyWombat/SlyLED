#!/usr/bin/env python3
"""test_900_fusion_sources.py — #900 source-agnostic temporal-object fusion.

`_fuse_temporal_objects` no longer assumes camera provenance: each
temporal object may carry `source: {"type": ..., ...}` and the fusion
weight is routed through a per-source-type registry
(`register_fusion_source_weight`) so a future radar_fusion.py (#912)
plugs in without touching the fusion core.

Covered here:
  1. Regression — two camera-source objects fuse exactly as pre-#900
     (lowest-id survivor, weighted mean, _fusionSources, forwarding).
  2. Legacy objects (no source stamp) default to camera weighting.
  3. A synthetic non-camera source participates via its registered hook
     and its hook-supplied weight shows up in _fusionSources.
  4. Fused-ID forwarding (#896) still works across a mixed-source merge.
  5. All-weights-zero cluster keep edge case preserved.
  6. Camera ingest (POST /api/objects/temporal with cameraId) stamps
     source metadata; a body-declared non-camera source is honoured.

Usage:
    SLYLED_DATA=$(mktemp -d) python3 tests/test_900_fusion_sources.py
"""

import os
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-900-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402
from parent_server import app  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _temporal(oid, x, y, ttl=5.0, source=None, method="homography",
              camera_id=None, confidence=0.8):
    obj = {
        "id": oid, "name": f"person-{oid}", "objectType": "person",
        "_temporal": True, "ttl": ttl, "_expiresAt": time.time() + ttl,
        "transform": {"pos": [x, y, 850.0], "rot": [0, 0, 0],
                      "scale": [500, 1700, 500]},
    }
    if method is not None:
        obj["_method"] = method
    if camera_id is not None:
        obj["_cameraId"] = camera_id
    if confidence is not None:
        obj["confidence"] = confidence
    if source is not None:
        obj["source"] = source
    return obj


def _reset():
    ps._temporal_objects.clear()
    ps._fused_id_map.clear()


# ── 1. Regression: camera+camera fusion unchanged ───────────────────────────

def run_camera_regression():
    _reset()
    a = _temporal(10001, 1000.0, 2000.0, camera_id=101,
                  source={"type": "camera", "cameraId": 101})
    b = _temporal(10002, 1200.0, 2000.0, camera_id=102,
                  source={"type": "camera", "cameraId": 102})
    ps._temporal_objects.extend([a, b])
    ps._fuse_temporal_objects()
    fused = ps._temporal_objects
    ok("two same-type camera sources fuse to one object", len(fused) == 1,
       repr([o.get("id") for o in fused]))
    m = fused[0]
    ok("survivor keeps the lowest id (sticky #629)", m.get("id") == 10001)
    px = m["transform"]["pos"][0]
    ok("weighted-mean X between the two placements (equal weights → 1100)",
       abs(px - 1100.0) < 1.0, repr(px))
    ok("_fusionCams counts both cameras", m.get("_fusionCams") == 2, repr(m))
    srcs = m.get("_fusionSources") or []
    ok("_fusionSources keeps cameraId per contributor",
       sorted(s.get("cameraId") for s in srcs) == [101, 102], repr(srcs))
    ok("_fusionSources stamps sourceType=camera",
       all(s.get("sourceType") == "camera" for s in srcs), repr(srcs))
    ok("fused-away id forwarded in _fused_id_map",
       ps._fused_id_map.get(10002, {}).get("to") == 10001,
       repr(ps._fused_id_map))


# ── 2. Legacy objects (no source stamp) default to camera weighting ─────────

def run_legacy_default():
    _reset()
    legacy = _temporal(10011, 500.0, 500.0)  # no source key at all
    ok("legacy object types as camera",
       ps._fusion_source_type(legacy) == "camera")
    w_legacy = ps._fusion_weight_for(legacy, 0.0)
    w_camera = ps._fusion_weight(legacy, 0.0)
    ok("legacy weight identical to pre-#900 camera weighting",
       abs(w_legacy - w_camera) < 1e-12, f"{w_legacy} vs {w_camera}")
    # And legacy+stamped-camera objects still fuse together.
    stamped = _temporal(10012, 600.0, 500.0, camera_id=101,
                        source={"type": "camera", "cameraId": 101})
    ps._temporal_objects.extend([legacy, stamped])
    ps._fuse_temporal_objects()
    ok("legacy + stamped camera objects fuse", len(ps._temporal_objects) == 1)


# ── 3. Non-camera source participates via its registered hook ───────────────

def run_radar_hook():
    _reset()
    calls = []

    def radar_weight(obj, age_s):
        calls.append((obj.get("id"), age_s))
        # e.g. covariance-derived confidence from a Kalman track (#912)
        return float(obj.get("_trackConfidence", 0.0))

    ps.register_fusion_source_weight("test-radar", radar_weight)
    try:
        cam = _temporal(10021, 3000.0, 3000.0, camera_id=101,
                        source={"type": "camera", "cameraId": 101})
        rad = _temporal(10022, 3400.0, 3000.0, method=None, camera_id=None,
                        confidence=None,
                        source={"type": "test-radar", "nodeId": "mmw-1"})
        rad["_trackConfidence"] = 0.9
        ps._temporal_objects.extend([cam, rad])
        ps._fuse_temporal_objects()
        fused = ps._temporal_objects
        ok("camera + radar sources fuse into one object", len(fused) == 1,
           repr([o.get("id") for o in fused]))
        ok("radar weight hook was consulted",
           any(oid == 10022 for oid, _ in calls), repr(calls))
        m = fused[0]
        srcs = m.get("_fusionSources") or []
        rs = next((s for s in srcs if s.get("sourceType") == "test-radar"), None)
        ok("_fusionSources carries the radar contributor", rs is not None,
           repr(srcs))
        ok("radar contributor weight comes from the hook (0.9 fresh)",
           rs is not None and abs(rs.get("weight", 0) - 0.9) < 0.01, repr(rs))
        # Camera weight fresh homography×0.8 = 0.8; radar 0.9 →
        # mean X = (0.8*3000 + 0.9*3400) / 1.7 ≈ 3211.8
        px = m["transform"]["pos"][0]
        ok("mixed-source weighted mean honours hook weight",
           abs(px - (0.8 * 3000.0 + 0.9 * 3400.0) / 1.7) < 2.0, repr(px))
    finally:
        ps._FUSION_SOURCE_WEIGHTS.pop("test-radar", None)


# ── 4. Fused-ID forwarding still works (mixed-source merge, #896) ───────────

def run_fused_id_forwarding():
    _reset()

    ps.register_fusion_source_weight("test-radar", lambda o, a: 0.5)
    try:
        cam = _temporal(10031, 4000.0, 1000.0, camera_id=101,
                        source={"type": "camera", "cameraId": 101})
        rad = _temporal(10032, 4100.0, 1000.0, method=None, confidence=None,
                        source={"type": "test-radar", "nodeId": "mmw-2"})
        ps._temporal_objects.extend([cam, rad])
        ps._fuse_temporal_objects()
        ok("radar id forwarded to camera survivor",
           ps._fused_id_map.get(10032, {}).get("to") == 10031,
           repr(ps._fused_id_map))
        c = app.test_client()
        r = c.put("/api/objects/10032/pos", json={"pos": [4200.0, 1000.0, 850.0]})
        ok("PUT /pos on fused-away id succeeds", r.status_code == 200,
           r.data[:120])
        body = r.get_json() or {}
        ok("response objectId reports the survivor for rebind",
           body.get("objectId") == 10031, repr(body))
        surv = next((o for o in ps._temporal_objects if o["id"] == 10031), None)
        ok("survivor received the forwarded position",
           surv is not None and surv["transform"]["pos"][0] == 4200.0,
           repr(surv and surv["transform"]["pos"]))
    finally:
        ps._FUSION_SOURCE_WEIGHTS.pop("test-radar", None)
        _reset()


# ── 5. All-weights-zero cluster keep edge case ──────────────────────────────

def run_zero_weight_cluster_keep():
    _reset()
    now = time.time()
    # Both members aged past _FUSION_MAX_AGE_S → freshness 0 → weight 0.
    # age = now - (_expiresAt - ttl); _expiresAt stays in the future so
    # the members are stale for fusion but not yet reaped.
    a = _temporal(10041, 5000.0, 5000.0)
    b = _temporal(10042, 5100.0, 5000.0)
    for o in (a, b):
        o["_expiresAt"] = now + 2.0
        o["ttl"] = ps._FUSION_MAX_AGE_S + 4.0
    ps._temporal_objects.extend([a, b])
    ps._fuse_temporal_objects()
    ok("all-weights-zero cluster keeps every member (no silent drop)",
       sorted(o["id"] for o in ps._temporal_objects) == [10041, 10042],
       repr([o.get("id") for o in ps._temporal_objects]))
    ok("no forwarding recorded for a kept (unmerged) cluster",
       not ps._fused_id_map, repr(ps._fused_id_map))
    _reset()


# ── 6. Ingest stamping via POST /api/objects/temporal ───────────────────────

def run_ingest_stamping():
    _reset()
    c = app.test_client()
    # Non-camera ingest with a declared source.
    r = c.post("/api/objects/temporal", json={
        "ttl": 5, "objectType": "person", "pos": [1, 2, 3],
        "source": {"type": "test-radar", "nodeId": "mmw-9"},
    })
    ok("temporal create with declared source succeeds", r.status_code == 200,
       r.data[:120])
    oid = (r.get_json() or {}).get("id")
    obj = next((o for o in ps._temporal_objects if o["id"] == oid), None)
    ok("declared non-camera source stored",
       obj is not None and obj.get("source", {}).get("type") == "test-radar",
       repr(obj and obj.get("source")))
    # Plain ingest without cameraId/source → no stamp (legacy shape).
    r2 = c.post("/api/objects/temporal", json={"ttl": 5, "pos": [4, 5, 6]})
    oid2 = (r2.get_json() or {}).get("id")
    obj2 = next((o for o in ps._temporal_objects if o["id"] == oid2), None)
    ok("plain ingest carries no source stamp (legacy default path)",
       obj2 is not None and "source" not in obj2, repr(obj2))
    ok("plain ingest still types as camera for weighting",
       obj2 is not None and ps._fusion_source_type(obj2) == "camera")
    _reset()


def main():
    run_camera_regression()
    run_legacy_default()
    run_radar_hook()
    run_fused_id_forwarding()
    run_zero_weight_cluster_keep()
    run_ingest_stamping()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
