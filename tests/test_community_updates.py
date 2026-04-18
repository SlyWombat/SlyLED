"""Community update-tracking tests (#534).

Covers the pieces that run without hitting the live electricrv.ca
server: _stamp_community_provenance and the check-updates parent
route with a mocked community_client.

Run:
    python -X utf8 tests/test_community_updates.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
from parent_server import _stamp_community_provenance, app  # noqa: E402


_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {msg}")


# ── _stamp_community_provenance ──────────────────────────────────────────

def test_stamp_moves_response_fields_into_community_block():
    p = {
        "id": "acme-mover-8ch",
        "name": "Acme Mover",
        "channels": [],
        "communityUploadTs": "2026-04-17 21:14:23",
        "communityChannelHash": "abcdef1234",
        "communityDownloads": 5,
    }
    _stamp_community_provenance(p, "acme-mover-8ch")
    _assert("_community" in p, "_community block created")
    cm = p["_community"]
    _assert(cm.get("slug") == "acme-mover-8ch", "slug stamped")
    _assert(cm.get("uploadTs") == "2026-04-17 21:14:23", "uploadTs stamped")
    _assert(cm.get("channelHash") == "abcdef1234", "channelHash stamped")
    _assert(isinstance(cm.get("syncedAt"), int), "syncedAt is epoch int")
    _assert(abs(cm["syncedAt"] - int(time.time())) < 5, "syncedAt is ~now")
    # Response-only fields should be stripped from the top level.
    _assert("communityUploadTs" not in p, "top-level upload_ts removed")
    _assert("communityChannelHash" not in p, "top-level channel_hash removed")
    # communityDownloads is a read-only counter — left alone.
    _assert(p.get("communityDownloads") == 5, "communityDownloads preserved")


def test_stamp_no_op_when_no_provenance_fields():
    p = {"id": "hand-rolled", "name": "Hand rolled", "channels": []}
    _stamp_community_provenance(p, "hand-rolled")
    _assert("_community" not in p,
            "no stamp when no upload_ts / channel_hash available")


# ── /api/dmx-profiles/community/check-updates ────────────────────────────

class _FakeCC:
    """Drop-in replacement for `community_client` when we don't want
    to (or can't) reach the live electricrv.ca server from a test
    harness."""

    last_call = None

    @staticmethod
    def check_updates(pairs):
        _FakeCC.last_call = list(pairs)
        # Say the first slug has an update, the rest don't.
        if not pairs:
            return {"ok": True, "data": {"updates": []}}
        first = pairs[0]
        return {
            "ok": True,
            "data": {
                "updates": [{
                    "slug": first.get("slug"),
                    "name": "Updated: " + first.get("slug"),
                    "uploadTs": "2999-01-01 00:00:00",
                    "channelHash": "newhash",
                }],
            },
        }


def test_check_updates_route_passes_through_community_client():
    # Save + restore any mutations to the profile lib.
    lib = parent_server._profile_lib
    snapshot = dict(lib._profiles)
    try:
        # Seed two community-stamped profiles; the route should pass both to CC.
        lib._profiles["_testcu_a"] = {
            "id": "_testcu_a", "name": "A", "channels": [],
            "_community": {"slug": "_testcu_a", "uploadTs": "2026-01-01",
                            "channelHash": "old", "syncedAt": 1},
        }
        lib._profiles["_testcu_b"] = {
            "id": "_testcu_b", "name": "B", "channels": [],
            "_community": {"slug": "_testcu_b", "uploadTs": "2026-01-02",
                            "channelHash": "old2", "syncedAt": 1},
        }
        # Monkey-patch community_client for this test only.
        import community_client as _cc
        real_check = _cc.check_updates
        _cc.check_updates = _FakeCC.check_updates
        try:
            with app.test_client() as c:
                r = c.post("/api/dmx-profiles/community/check-updates", json={})
                _assert(r.status_code == 200, f"route returns 200, got {r.status_code}")
                data = r.get_json() or {}
                _assert(data.get("ok") is True, "ok flag true")
                _assert(data.get("tracked") == 2, f"2 tracked, got {data.get('tracked')}")
                # _FakeCC marks the first slug (alphabetical or insertion
                # order — we only care that exactly one lands).
                updates = data.get("updates") or []
                _assert(len(updates) == 1, f"exactly 1 update, got {len(updates)}")
                u = updates[0]
                _assert(u.get("slug") in ("_testcu_a", "_testcu_b"),
                        f"update slug is one of ours, got {u.get('slug')}")
                _assert(u.get("profileId") == u.get("slug"),
                        "profileId mirrors slug when id==slug")
            # Verify we actually sent the pairs through.
            called = _FakeCC.last_call or []
            slugs = set(p["slug"] for p in called)
            _assert(slugs == {"_testcu_a", "_testcu_b"},
                    f"both slugs passed, got {slugs}")
            known = {p["slug"]: p.get("knownTs") for p in called}
            _assert(known.get("_testcu_a") == "2026-01-01",
                    "knownTs threaded through from _community.uploadTs")
        finally:
            _cc.check_updates = real_check
    finally:
        lib._profiles.clear()
        lib._profiles.update(snapshot)


def test_check_updates_empty_when_no_tracked_profiles():
    lib = parent_server._profile_lib
    snapshot = dict(lib._profiles)
    try:
        # Strip any community tags so the batch starts empty.
        lib._profiles = {pid: {k: v for k, v in p.items() if k != "_community"}
                          for pid, p in snapshot.items()}
        with app.test_client() as c:
            r = c.post("/api/dmx-profiles/community/check-updates", json={})
            d = r.get_json() or {}
            _assert(d.get("ok") is True, "ok")
            _assert(d.get("tracked") == 0, f"0 tracked, got {d.get('tracked')}")
            _assert(d.get("updates") == [], "empty updates array")
    finally:
        lib._profiles.clear()
        lib._profiles.update(snapshot)


ALL = [
    test_stamp_moves_response_fields_into_community_block,
    test_stamp_no_op_when_no_provenance_fields,
    test_check_updates_route_passes_through_community_client,
    test_check_updates_empty_when_no_tracked_profiles,
]


if __name__ == "__main__":
    for t in ALL:
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
