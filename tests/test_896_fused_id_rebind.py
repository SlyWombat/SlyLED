#!/usr/bin/env python3
"""
test_896_fused_id_rebind.py — #896 fusion orphaning + tracker rebinding.

Server half: when _fuse_temporal_objects collapses a cluster into the
surviving (lowest) id, the fused-away ids must keep working — a
PUT /api/objects/<old id>/pos is forwarded to the survivor via
_fused_id_map and the response carries {"objectId": <survivor>} so the
camera can rebind. Every successful pos update includes "objectId".

Tracker half: the camera-node Tracker rebinds its track when the pos
response carries a different objectId, drops the stale id on 404 so the
next push re-creates, and its push queue is bounded drop-oldest with the
re-ID gate now honestly in pixels scaled from a 640-wide reference.

Usage:
    SLYLED_DATA=$(mktemp -d) python tests/test_896_fused_id_rebind.py
"""

import os
import sys
import tempfile
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-test-896-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'firmware', 'orangepi'))

import parent_server as ps
from parent_server import app

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def _temporal(oid, x, y, ttl=5.0):
    return {
        "id": oid, "name": f"person-{oid}", "objectType": "person",
        "_temporal": True, "_method": "homography", "_cameraId": 100 + oid,
        "confidence": 0.8, "ttl": ttl, "_expiresAt": time.time() + ttl,
        "transform": {"pos": [x, y, 850.0], "rot": [0, 0, 0],
                      "scale": [500, 1700, 500]},
    }


def run_server_half():
    with app.test_client() as c:
        # Two cameras see the same person → two temporal objects 200 mm
        # apart. Fusion keeps the first id and drops the second.
        ps._temporal_objects[:] = [_temporal(10001, 1000, 1000),
                                   _temporal(10002, 1200, 1000)]
        ps._fused_id_map.clear()
        ps._fuse_temporal_objects()
        ok("fusion collapses cluster to one object",
           len(ps._temporal_objects) == 1, repr(ps._temporal_objects))
        survivor = ps._temporal_objects[0]["id"]
        ok("survivor keeps sticky id", survivor == 10001)
        ok("fused-away id mapped to survivor",
           ps._fused_id_map.get(10002, {}).get("to") == survivor,
           repr(ps._fused_id_map))

        # Camera B still addresses its old id → forwarded to survivor,
        # response tells it the id to rebind to.
        r = c.put('/api/objects/10002/pos', json={"pos": [1500, 1100, 0]})
        d = r.get_json() or {}
        ok("pos update on fused-away id succeeds", r.status_code == 200 and d.get("ok"))
        ok("response carries survivor objectId", d.get("objectId") == survivor, repr(d))
        ok("update applied to survivor",
           ps._temporal_objects[0]["transform"]["pos"] == [1500.0, 1100.0, 0.0],
           repr(ps._temporal_objects[0]["transform"]["pos"]))

        # Normal (non-forwarded) updates also carry objectId for uniformity.
        r = c.put(f'/api/objects/{survivor}/pos', json={"pos": [1501, 1101, 0]})
        d = r.get_json() or {}
        ok("normal pos update carries objectId", d.get("objectId") == survivor, repr(d))

        # Chain collapse at insert: an entry forwarding to an id that is
        # itself fused away gets rewritten to the final survivor.
        ps._temporal_objects[:] = [_temporal(10005, 5000, 5000),
                                   _temporal(10006, 5100, 5000)]
        ps._fused_id_map.clear()
        ps._fused_id_map[10009] = {"to": 10006, "at": time.time()}
        ps._fuse_temporal_objects()
        ok("chained forwarding collapsed to final survivor",
           ps._fused_id_map.get(10009, {}).get("to") == 10005,
           repr(ps._fused_id_map))

        # Reap pruning: once the survivor expires, its forwardings go too
        # and the old id 404s (camera then re-creates).
        ps._temporal_objects[0]["_expiresAt"] = time.time() - 1
        ps._reap_temporal_objects()
        ok("map pruned when survivor expires", 10009 not in ps._fused_id_map,
           repr(ps._fused_id_map))
        r = c.put('/api/objects/10006/pos', json={"pos": [1, 2, 0]})
        ok("pruned id returns 404", r.status_code == 404)

        ps._temporal_objects[:] = []
        ps._fused_id_map.clear()


class _StubFrame:
    def __init__(self, w=640, h=480):
        self.shape = (h, w, 3)


class _StubDetector:
    def __init__(self):
        self.next_dets = []

    def detect(self, frame, threshold=0.4, classes=None,
               class_thresholds=None, input_size=320):
        return list(self.next_dets), None


def run_tracker_half():
    import tracker as trk_mod
    det = _StubDetector()
    frame = _StubFrame(640, 480)
    t = trk_mod.Tracker(detector=det, capture_fn=lambda dev: frame)
    t._orch_url = "http://fake"      # enable enqueueing without a server
    t._reid_gate_ref_px = 500

    # Tick 1: one detection → local track created immediately with
    # orch_obj_id pending (None); push queued, tick never blocks on HTTP.
    det.next_dets = [{"label": "person", "confidence": 0.9,
                      "x": 100, "y": 100, "w": 40, "h": 120}]
    t._tick(None, cap=None)
    ok("track created locally before orchestrator ack",
       t.track_count == 1 and t._tracks[0]["orch_obj_id"] is None,
       repr(t._tracks))
    ok("push queued", t._push_q.qsize() == 1)

    # Sender: create assigns the orchestrator id.
    calls = []
    t._orch_create_temporal = lambda d: calls.append("create") or 42
    tid, d0 = t._push_q.get_nowait()
    ok("create push succeeds", t._process_push(tid, d0) is True)
    ok("create binds orchestrator id", t._tracks[0]["orch_obj_id"] == 42)

    # Update whose response carries a different objectId → rebind (#896).
    t._orch_update_pos = lambda oid, d: 41   # fused into survivor 41
    ok("fused update processed", t._process_push(tid, d0) is True)
    ok("track rebound to survivor id", t._tracks[0]["orch_obj_id"] == 41)

    # 404 → drop the stale id so the next push re-creates.
    t._orch_update_pos = lambda oid, d: -1
    ok("404 push processed without backoff", t._process_push(tid, d0) is True)
    ok("stale id dropped on 404", t._tracks[0]["orch_obj_id"] is None)
    ok("next push re-creates", t._process_push(tid, d0) is True
       and t._tracks[0]["orch_obj_id"] == 42)

    # Transient failure → False (sender backs off), id untouched.
    t._orch_update_pos = lambda oid, d: None
    ok("transient failure reported for backoff", t._process_push(tid, d0) is False)
    ok("id kept on transient failure", t._tracks[0]["orch_obj_id"] == 42)

    # Tick 2: detection 300 px away (< 500 px gate at 640 wide) → same
    # track; 640-wide frame reproduces the legacy gate exactly.
    det.next_dets = [{"label": "person", "confidence": 0.9,
                      "x": 400, "y": 100, "w": 40, "h": 120}]
    t._tick(None, cap=None)
    ok("near detection re-IDs to same track", t.track_count == 1)

    # Tick 3: detection ~600 px away (> gate) → new track.
    det.next_dets = [{"label": "person", "confidence": 0.9,
                      "x": 1000, "y": 500, "w": 40, "h": 120}]
    t._tick(None, cap=None)
    ok("far detection opens a new track", t.track_count == 2, repr(t._tracks))

    # Gate scales with frame width: same 600 px offset on a 1280-wide
    # frame is within the scaled gate (1000 px) → re-ID, no third track.
    wide = _StubFrame(1280, 720)
    t._capture = lambda dev: wide
    det.next_dets = [{"label": "person", "confidence": 0.9,
                      "x": 1600, "y": 500, "w": 40, "h": 120}]
    t._tick(None, cap=None)
    ok("gate scales with frame width", t.track_count == 2, repr(t._tracks))

    # Bounded queue drops oldest on overflow.
    while not t._push_q.empty():
        t._push_q.get_nowait()
    for i in range(trk_mod.PUSH_QUEUE_MAX + 5):
        t._enqueue_push(0, {"seq": i})
    ok("queue bounded", t._push_q.qsize() == trk_mod.PUSH_QUEUE_MAX)
    _tid, first = t._push_q.get_nowait()
    ok("overflow drops oldest", first.get("seq") == 5, repr(first))


def main():
    run_server_half()
    run_tracker_half()
    passed = sum(1 for _, p, _ in results if p)
    for name, p, detail in results:
        mark = "PASS" if p else "FAIL"
        extra = f"  ({detail})" if (detail and not p) else ""
        print(f"[{mark}] {name}{extra}")
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
