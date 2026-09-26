#!/usr/bin/env python3
"""HTTP-surface checks carried over from the retired tests/test_web.py (#967).

test_web.py targeted the pre-v8 API (runners, UDP protocol v2 with a mock
child bound on the LAN) and had drifted: the runner routes, /api/shutdown and
SPA internals it asserted are gone. Everything it covered that test_parent
doesn't already cover, and that is still the contract, lives here — in
process (Flask test_client, isolated SLYLED_DATA, nothing on the network):
  * the SPA shell is served with no-cache headers; unknown routes fall back
    to it; the favicon is served
  * JSON responses carry Content-Length
  * POST /api/children strips a pasted URL down to the bare IP
  * the first action (id 0) survives a timeline round-trip (falsy-id bug)
  * action scope fields persist; /api/firmware/query rejects an empty port
  * factory reset refuses without the X-SlyLED-Confirm header and, with it,
    clears actions and the WiFi credentials

Run: python3 tests/test_web_http.py
"""

import _bootstrap  # noqa: F401,E402  SLYLED_DATA isolation, before parent_server (#942)
import sys

_passed = 0
_failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def main():
    import parent_server as ps
    c = ps.app.test_client()

    print("SPA shell")
    r = c.get("/")
    body = r.get_data(as_text=True)
    ok("GET / → 200 with the SPA", r.status_code == 200 and "SlyLED" in body and "id='hdr'" in body)
    ok("the tabs are there", all(t in body for t in ("n-dash", "n-setup", "n-layout", "n-runtime",
                                                     "n-settings", "n-firmware")))
    cc = (r.headers.get("Cache-Control") or "").lower()
    ok("/ is no-cache, no-store", "no-cache" in cc and "no-store" in cc, cc)
    r = c.get("/this-route-does-not-exist")
    ok("unknown route falls back to the SPA", r.status_code == 200 and b"SlyLED" in r.data)
    ok("favicon is served", c.get("/favicon.ico").status_code == 200)

    print("JSON responses")
    for path in ("/api/children", "/api/settings", "/status"):
        r = c.get(path)
        cl = r.headers.get("Content-Length")
        ok(f"{path} carries a correct Content-Length", cl is not None and int(cl) == len(r.data), cl)

    print("Children")
    r = c.post("/api/children", json={"ip": "http://192.0.2.253/config"})
    d = r.get_json() or {}
    kid = d.get("id")
    kids = c.get("/api/children").get_json() or []
    got = next((k for k in kids if k.get("id") == kid), None)
    ok("a pasted URL is stored as the bare IP", got is not None and got.get("ip") == "192.0.2.253",
       (d, got))
    if kid is not None:
        c.delete(f"/api/children/{kid}")

    print("Action id 0 through a timeline")
    ps._actions.clear()
    a0 = (c.post("/api/actions", json={"name": "ZeroId", "type": 1, "r": 100}).get_json() or {}).get("id")
    ok("first action on a fresh project has id 0", a0 == 0, a0)
    tid = (c.post("/api/timelines", json={"name": "Zero", "durationS": 4}).get_json() or {}).get("id")
    c.put(f"/api/timelines/{tid}", json={"name": "Zero", "durationS": 4, "tracks": [
        {"allPerformers": True, "clips": [{"actionId": a0, "startS": 0, "durationS": 4}]}]})
    tl = c.get(f"/api/timelines/{tid}").get_json() or {}
    clip = ((tl.get("tracks") or [{}])[0].get("clips") or [{}])[0]
    ok("actionId 0 persists in the clip (not dropped as falsy)", clip.get("actionId") == 0, clip)

    print("Action scope")
    a = c.post("/api/actions", json={"name": "CanvasWipe", "type": 3, "scope": "canvas",
                                     "canvasEffect": "wipe", "direction": 2, "r": 100}).get_json()
    g = c.get(f"/api/actions/{a['id']}").get_json()
    ok("canvas scope + effect + direction persist",
       g.get("scope") == "canvas" and g.get("canvasEffect") == "wipe" and g.get("direction") == 2, g)
    a = c.post("/api/actions", json={"name": "Selected", "type": 1, "scope": "performer-selected",
                                     "targetIds": [10, 20, 30], "r": 200}).get_json()
    g = c.get(f"/api/actions/{a['id']}").get_json()
    ok("performer-selected scope + targetIds persist",
       g.get("scope") == "performer-selected" and g.get("targetIds") == [10, 20, 30], g)

    print("Firmware query")
    r = c.post("/api/firmware/query", json={"port": ""})
    ok("/api/firmware/query with no port → 400", r.status_code == 400, r.status_code)

    print("Factory reset")
    c.post("/api/wifi", json={"ssid": "ResetNet", "password": "resetpw"})
    r = c.post("/api/reset")
    ok("reset without X-SlyLED-Confirm → 403 (CSRF guard)", r.status_code == 403, r.status_code)
    ok("…and nothing was cleared", len(c.get("/api/actions").get_json() or []) > 0)
    r = c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
    ok("reset with the header → 200", r.status_code == 200, r.status_code)
    ok("…actions cleared", c.get("/api/actions").get_json() == [])
    w = c.get("/api/wifi").get_json() or {}
    ok("…WiFi credentials cleared", w.get("ssid") == "" and not w.get("hasPassword"), w)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
