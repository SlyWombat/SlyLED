#!/usr/bin/env python3
"""
test_dmx_fixtures.py — Integration tests for DMX fixture type selector (#91).

Tests fixture CRUD with fixtureType 'led' and 'dmx', backwards compatibility
migration, DMX validation, discover filtering, and mixed fixture lists.

Usage:
    python tests/test_dmx_fixtures.py [host:port]

Docker:
    bash tests/docker/run_dmx_tests.sh
"""
import json, os, sys, urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "localhost:8080"
BASE = f"http://{HOST}"

passed = 0
failed = 0
assertions = 0


def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"err": str(e)}
    except Exception as e:
        return 0, {"err": str(e)}


def ok(name, result, detail=""):
    global passed, failed, assertions
    assertions += 1
    if result:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def run():
    print(f"\n=== DMX Fixture Tests against {BASE} ===\n")

    # Clean slate
    api("POST", "/api/reset")

    # ── 1. LED fixture creation (no child) ────────────────────────────
    print("── LED fixture basics ──")
    code, d = api("POST", "/api/fixtures", {
        "name": "Test LED Strip", "type": "linear", "fixtureType": "led"
    })
    ok("POST LED fixture", code == 200 and d.get("ok"))
    led_id = d.get("id")

    code, d = api("GET", f"/api/fixtures/{led_id}")
    ok("LED fixture fixtureType=led", d.get("fixtureType") == "led")
    ok("LED fixture has no DMX fields", "dmxUniverse" not in d)

    # ── 2. DMX fixture creation ───────────────────────────────────────
    print("\n── DMX fixture CRUD ──")
    code, d = api("POST", "/api/fixtures", {
        "name": "Moving Head 1", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": 1, "dmxChannelCount": 13,
        "dmxProfileId": "generic-moving-head"
    })
    ok("POST DMX fixture", code == 200 and d.get("ok"))
    dmx_id = d.get("id")

    code, d = api("GET", f"/api/fixtures/{dmx_id}")
    ok("DMX fixtureType", d.get("fixtureType") == "dmx")
    ok("DMX universe", d.get("dmxUniverse") == 1)
    ok("DMX startAddr", d.get("dmxStartAddr") == 1)
    ok("DMX channelCount", d.get("dmxChannelCount") == 13)
    ok("DMX profileId", d.get("dmxProfileId") == "generic-moving-head")

    # PUT — update DMX address
    code, d = api("PUT", f"/api/fixtures/{dmx_id}", {"dmxStartAddr": 100, "dmxUniverse": 2})
    ok("PUT DMX fixture", code == 200 and d.get("ok"))
    code, d = api("GET", f"/api/fixtures/{dmx_id}")
    ok("DMX addr updated to 100", d.get("dmxStartAddr") == 100)
    ok("DMX universe updated to 2", d.get("dmxUniverse") == 2)

    # Create RGB Par
    code, d = api("POST", "/api/fixtures", {
        "name": "RGB Par", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": 50, "dmxChannelCount": 3
    })
    ok("POST DMX RGB Par", code == 200)
    dmx_id2 = d.get("id")

    # ── 3. DMX validation ─────────────────────────────────────────────
    print("\n── DMX validation ──")
    code, _ = api("POST", "/api/fixtures", {
        "name": "Bad", "type": "point", "fixtureType": "dmx",
        "dmxStartAddr": 1, "dmxChannelCount": 3
    })
    ok("Missing universe → 400", code == 400)

    code, _ = api("POST", "/api/fixtures", {
        "name": "Bad", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": 0, "dmxChannelCount": 3
    })
    ok("startAddr 0 → 400", code == 400)

    code, _ = api("POST", "/api/fixtures", {
        "name": "Bad", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": 513, "dmxChannelCount": 3
    })
    ok("startAddr 513 → 400", code == 400)

    code, _ = api("POST", "/api/fixtures", {
        "name": "Bad", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": 1
    })
    ok("Missing channelCount → 400", code == 400)

    code, _ = api("POST", "/api/fixtures", {
        "name": "Bad", "type": "point", "fixtureType": "invalid"
    })
    ok("Bad fixtureType → 400", code == 400)

    # ── 4. Mixed fixture list ─────────────────────────────────────────
    print("\n── Mixed fixture list ──")
    code, flist = api("GET", "/api/fixtures")
    ok("GET fixtures list", code == 200 and isinstance(flist, list))
    led_count = sum(1 for f in flist if f.get("fixtureType") == "led")
    dmx_count = sum(1 for f in flist if f.get("fixtureType") == "dmx")
    ok("Has LED fixtures", led_count >= 1, f"led={led_count}")
    ok("Has DMX fixtures", dmx_count >= 2, f"dmx={dmx_count}")

    # ── 5. Backwards compat (default fixtureType) ────────────────────
    print("\n── Backwards compatibility ──")
    code, d = api("POST", "/api/fixtures", {"name": "Old-style", "type": "linear"})
    ok("POST without fixtureType", code == 200)
    old_id = d.get("id")
    code, d = api("GET", f"/api/fixtures/{old_id}")
    ok("Old fixture defaults to fixtureType=led", d.get("fixtureType") == "led")

    # ── 6. Auto-create sets fixtureType ───────────────────────────────
    print("\n── Auto-create fixtures ──")
    # Register a fake child first
    code, d = api("POST", "/api/children", {"ip": "10.0.0.99"})
    ok("Register fake child", code == 200 or (d and d.get("duplicate")))

    code, d = api("POST", "/api/migrate/layout")
    ok("Migrate layout", code == 200 and d.get("ok"))

    code, flist = api("GET", "/api/fixtures")
    auto_created = [f for f in flist if f.get("childId") is not None and f.get("fixtureType") == "led"]
    ok("Auto-created fixtures have fixtureType=led", len(auto_created) >= 1,
       f"count={len(auto_created)}")

    # ── 7. Delete DMX fixture ─────────────────────────────────────────
    print("\n── Delete ──")
    code, d = api("DELETE", f"/api/fixtures/{dmx_id}")
    ok("DELETE DMX fixture", code == 200 and d.get("ok"))
    code, _ = api("GET", f"/api/fixtures/{dmx_id}")
    ok("Deleted fixture gone", code == 404)

    # Cleanup
    api("DELETE", f"/api/fixtures/{dmx_id2}")
    api("DELETE", f"/api/fixtures/{led_id}")
    api("DELETE", f"/api/fixtures/{old_id}")

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"{passed} passed, {failed} failed out of {assertions} assertions")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
