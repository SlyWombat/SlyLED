#!/usr/bin/env python3
"""
test_887_profile_issues.py — Soft-warning diagnostics for DMX profile
shape problems that bypass the runtime contiguous-fallback guard.

The motivating bug: a 350W BeamLight profile in the field declared
phantom ``pan-fine`` and ``tilt-fine`` channels at the same offsets
its real ``tilt`` and ``pan-tilt-speed`` channels occupied. The
runtime guard at ``compute_pan_tilt_writes`` only fires when
``channel_map['pan-fine']`` is *missing*; once the profile claimed a
fine channel existed, the guard was bypassed and pan/tilt LSBs were
written into the colliding slots, causing motor lag (the speed
channel read the LSB byte as a slow-fast value).

Usage:
    python tests/test_887_profile_issues.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                'desktop', 'shared'))

from dmx_profiles import ProfileLibrary

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def run():
    lib = ProfileLibrary()

    # ── Case 1: bits=16 declared, no fine sibling, neighbour is a
    #            different typed channel. The runtime guard catches
    #            this at write time; the finder surfaces it at load.
    p1 = {
        "id": "test-887-case-1",
        "name": "Case 1",
        "channels": [
            {"offset": 9,  "type": "pan",            "bits": 16},
            {"offset": 10, "type": "tilt",           "bits": 16},
            {"offset": 11, "type": "pan-tilt-speed"},
            {"offset": 12, "type": "frost"},
        ],
    }
    issues = lib.find_profile_issues(p1)
    ok("case 1: pan reports bits=16-with-no-fine-sibling pattern",
       any("'pan' at offset 9" in s and "bits=16" in s
           and "offset 10" in s for s in issues),
       f"issues={issues}")
    ok("case 1: tilt reports bits=16-with-no-fine-sibling pattern",
       any("'tilt' at offset 10" in s and "bits=16" in s
           and "offset 11" in s for s in issues),
       f"issues={issues}")

    # ── Case 2: phantom pan-fine + tilt-fine entries colliding with
    #            the real channels at those offsets (the #887 root
    #            cause). Duplicate-offset AND collision warnings both
    #            fire.
    p2 = {
        "id": "test-887-case-2",
        "name": "Case 2",
        "channels": [
            {"offset": 9,  "type": "pan",            "bits": 16},
            {"offset": 10, "type": "pan-fine"},
            {"offset": 10, "type": "tilt",           "bits": 16},
            {"offset": 11, "type": "tilt-fine"},
            {"offset": 11, "type": "pan-tilt-speed"},
            {"offset": 12, "type": "frost"},
        ],
    }
    issues2 = lib.find_profile_issues(p2)
    ok("case 2: duplicate offset 10 flagged",
       any("duplicate offset 10" in s for s in issues2),
       f"issues={issues2}")
    ok("case 2: duplicate offset 11 flagged",
       any("duplicate offset 11" in s for s in issues2),
       f"issues={issues2}")
    ok("case 2: pan-fine/tilt collision call-out",
       any("'pan-fine'" in s and "collides" in s and "'tilt'" in s
           for s in issues2),
       f"issues={issues2}")
    ok("case 2: tilt-fine/pan-tilt-speed collision call-out",
       any("'tilt-fine'" in s and "collides" in s
           and "pan-tilt-speed" in s for s in issues2),
       f"issues={issues2}")
    ok("case 2: collision message includes the operator-actionable "
       "fix (remove spurious entry)",
       all("removing the spurious" in s for s in issues2
           if "collides" in s),
       f"issues={issues2}")

    # ── Case 3: orphaned pan-fine (no matching coarse with bits=16).
    p3 = {
        "id": "test-887-case-3",
        "name": "Case 3",
        "channels": [
            {"offset": 0, "type": "pan-fine"},
            {"offset": 1, "type": "tilt"},
        ],
    }
    issues3 = lib.find_profile_issues(p3)
    ok("case 3: orphaned pan-fine flagged",
       any("'pan-fine'" in s and "no matching coarse" in s
           for s in issues3),
       f"issues={issues3}")

    # ── Case 4: clean 150W-style profile produces zero issues.
    p4 = {
        "id": "test-887-case-4",
        "name": "Case 4",
        "channels": [
            {"offset": 0, "type": "pan",            "bits": 16},
            {"offset": 1, "type": "pan-fine"},
            {"offset": 2, "type": "tilt",           "bits": 16},
            {"offset": 3, "type": "tilt-fine"},
            {"offset": 4, "type": "pan-tilt-speed"},
            {"offset": 5, "type": "dimmer"},
        ],
    }
    issues4 = lib.find_profile_issues(p4)
    ok("case 4: clean 150W-style returns no issues",
       len(issues4) == 0, f"issues={issues4}")

    # ── Case 5: empty / malformed channels list never crashes.
    p5 = {"id": "test-887-case-5", "name": "Case 5", "channels": []}
    issues5 = lib.find_profile_issues(p5)
    ok("case 5: empty channels list returns no issues",
       len(issues5) == 0, f"issues={issues5}")

    p6 = {"id": "test-887-case-6", "name": "Case 6", "channels": None}
    issues6 = lib.find_profile_issues(p6)
    ok("case 6: None channels list returns no issues "
       "(handled gracefully)",
       len(issues6) == 0, f"issues={issues6}")

    # ── Case 7: negative offset surfaces clean error.
    p7 = {
        "id": "test-887-case-7",
        "name": "Case 7",
        "channels": [
            {"offset": -1, "type": "pan"},
            {"offset": 0,  "type": "tilt"},
        ],
    }
    issues7 = lib.find_profile_issues(p7)
    ok("case 7: negative offset flagged",
       any("negative offset" in s for s in issues7),
       f"issues={issues7}")

    # ── HTTP route exposes the finder. ────────────────────────────
    import parent_server as _ps
    # Inject case 2 into the live library so the route can read it.
    _ps._profile_lib._profiles["test-887-case-2"] = p2
    try:
        with _ps.app.test_client() as c:
            r = c.get('/api/dmx-profiles/test-887-case-2/issues')
            body = r.get_json() or {}
            ok("HTTP /api/dmx-profiles/<id>/issues returns 200",
               r.status_code == 200,
               f"status={r.status_code} body={body}")
            ok("HTTP response contains the profile id",
               body.get("id") == "test-887-case-2",
               f"id={body.get('id')}")
            ok("HTTP response surfaces the same issues as direct call",
               isinstance(body.get("issues"), list) and
               len(body["issues"]) == len(issues2),
               f"http={body.get('issues')} direct={issues2}")
            r404 = c.get('/api/dmx-profiles/does-not-exist/issues')
            ok("HTTP 404 on unknown profile",
               r404.status_code == 404,
               f"status={r404.status_code}")
    finally:
        _ps._profile_lib._profiles.pop("test-887-case-2", None)

    # ── Print results ─────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
