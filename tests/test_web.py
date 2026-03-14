#!/usr/bin/env python3
"""
SlyLED Phase 2 Web API Test Suite
Tests the HTTP/JSON API running on the Arduino Giga R1 WiFi (parent node).

Usage:
    python3 tests/test_web.py [host]
    python3 tests/test_web.py 192.168.10.219

All tests are non-destructive or clean up after themselves.
No registered children are required — runner lifecycle tests run fine on an empty board.
"""

import sys
import time
import json
import urllib.request
import urllib.error

HOST    = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.219"
BASE    = f"http://{HOST}"
TIMEOUT = 8

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = 0
failed = 0


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def post_body(path, body_bytes=b"", content_type="application/json"):
    try:
        req = urllib.request.Request(
            BASE + path, data=body_bytes, method="POST",
            headers={"Content-Type": content_type,
                     "Content-Length": str(len(body_bytes))})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def post_json(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    code, text = post_body(path, body)
    try:
        return code, json.loads(text)
    except Exception:
        return code, None


def put_json(path, payload):
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(
            BASE + path, data=body, method="PUT",
            headers={"Content-Type": "application/json",
                     "Content-Length": str(len(body))})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None


def delete(path):
    try:
        req = urllib.request.Request(BASE + path, method="DELETE")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None


def get_json(path):
    code, text = get(path)
    try:
        return code, json.loads(text)
    except Exception:
        return code, None


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  {GREEN}PASS{RESET}  {name}")
    else:
        failed += 1
        detail_str = f"\n         {YELLOW}{detail}{RESET}" if detail else ""
        print(f"  {RED}FAIL{RESET}  {name}{detail_str}")
    return condition


def section(title):
    print(f"\n{BOLD}{'-' * 62}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'-' * 62}{RESET}")


# ── Connectivity ───────────────────────────────────────────────────────────────

section(f"Connectivity  ({HOST})")
code, body = get("/")
if not check("Board reachable", code == 200, f"HTTP {code}: {body[:80]}"):
    print(f"\n  {RED}Cannot reach board — aborting.{RESET}\n")
    sys.exit(1)

# ── SPA main page ──────────────────────────────────────────────────────────────

section("SPA main page  GET /")
check("HTTP 200",                    code == 200,                f"got {code}")
check("Title contains SlyLED",       "SlyLED"    in body)
check("Has header element",          "id='hdr'"  in body or 'id="hdr"' in body)
check("Has nav tabs",                "Dashboard" in body and "Setup" in body
                                     and "Layout" in body and "Runtime" in body)
check("Has version string",          "v2." in body)
check("No old rainbow badge",        "badge-rainbow" not in body)
check("No old siren route",          "/led/siren/on" not in body)
check("Has /api/children in JS",     "/api/children" in body)
check("Has /api/runners in JS",      "/api/runners"  in body)
check("Has /api/action in JS",       "/api/action"   in body)
check("SPA does not expose /log",    "href='/log'" not in body
                                     and 'href="/log"' not in body)

# ── Cache-Control ──────────────────────────────────────────────────────────────

section("Cache-Control headers")
try:
    with urllib.request.urlopen(BASE + "/", timeout=TIMEOUT) as r:
        cc = r.headers.get("Cache-Control", "")
        check("/ has no-cache", "no-cache" in cc.lower(), f"Cache-Control: '{cc}'")
        check("/ has no-store", "no-store" in cc.lower(), f"Cache-Control: '{cc}'")
except Exception as e:
    check("Cache-Control header fetch", False, str(e))

# ── GET /status ────────────────────────────────────────────────────────────────

section("Status endpoint  GET /status")
code, data = get_json("/status")
check("HTTP 200",            code == 200,       f"got {code}")
check("Valid JSON",          data is not None,  "failed to parse JSON")
check("Has role field",      data is not None and "role" in data)
check("role == parent",      data is not None and data.get("role") == "parent",
      f"role={data.get('role') if data else None}")
check("Has hostname field",  data is not None and "hostname" in data)

# ── GET /favicon.ico ──────────────────────────────────────────────────────────

section("Favicon  GET /favicon.ico")
code, _ = get("/favicon.ico")
check("Returns 404", code == 404, f"got {code}")

# ── Unknown route falls through to SPA ────────────────────────────────────────

section("Unknown route fallback")
code, body2 = get("/this-route-does-not-exist")
check("Unknown route returns SPA (200)", code == 200, f"got {code}")
check("Body contains SlyLED",            "SlyLED" in body2)

# ── GET /api/children ─────────────────────────────────────────────────────────

section("Children API  GET /api/children")
code, data = get_json("/api/children")
check("HTTP 200",       code == 200,                  f"got {code}")
check("Valid JSON",     data is not None,             "failed to parse JSON")
check("Returns array",  isinstance(data, list),       f"type={type(data)}")

# ── GET /api/children/export ──────────────────────────────────────────────────

section("Children export  GET /api/children/export")
code, data = get_json("/api/children/export")
check("HTTP 200",       code == 200,             f"got {code}")
check("Valid JSON",     data is not None)
check("Returns array",  isinstance(data, list))

# ── POST /api/children/import ─────────────────────────────────────────────────

section("Children import  POST /api/children/import")
import_data = [{"hostname": "SLYC-TEST", "name": "Test Child",
                "desc": "Import test", "ip": "10.0.0.99", "x": 500, "y": 250}]
code, data = post_json("/api/children/import", import_data)
check("HTTP 200",         code == 200,                              f"got {code}")
check("Returns ok:true",  data is not None and data.get("ok") is True)
check("Reports added>=1", data is not None and data.get("added", 0) >= 1,
      f"data={data}")

# Verify child appeared
code2, kids = get_json("/api/children")
found_id = None
if isinstance(kids, list):
    for k in kids:
        if k.get("hostname") == "SLYC-TEST":
            found_id = k.get("id")
            break
check("Imported child visible in GET /api/children", found_id is not None,
      f"children={kids}")

# Re-import same data → should update, not duplicate
code3, data3 = post_json("/api/children/import", import_data)
check("Re-import returns ok:true", data3 is not None and data3.get("ok") is True)
check("Re-import shows updated=1", data3 is not None and data3.get("updated", 0) >= 1,
      f"data={data3}")
code4, kids4 = get_json("/api/children")
same_hn = sum(1 for k in (kids4 or []) if k.get("hostname") == "SLYC-TEST")
check("No duplicate hostname after re-import", same_hn == 1, f"count={same_hn}")

# Clean up the imported child
if found_id is not None:
    del_code, del_data = delete(f"/api/children/{found_id}")
    check("Imported child removed (cleanup)", del_code == 200
          and del_data is not None and del_data.get("ok") is True,
          f"HTTP {del_code} data={del_data}")

# ── GET /api/layout ───────────────────────────────────────────────────────────

section("Layout API  GET /api/layout")
code, data = get_json("/api/layout")
check("HTTP 200",           code == 200,             f"got {code}")
check("Valid JSON",         data is not None)
check("Has canvasW field",  data is not None and "canvasW" in data,
      f"keys={list(data.keys()) if data else None}")
check("Has canvasH field",  data is not None and "canvasH" in data)
check("Has children array", data is not None and isinstance(data.get("children"), list))

# ── GET /api/settings ─────────────────────────────────────────────────────────

section("Settings API  GET /api/settings")
code, data = get_json("/api/settings")
check("HTTP 200",              code == 200,               f"got {code}")
check("Valid JSON",            data is not None)
check("Has name field",        data is not None and "name"    in data)
check("Has units field",       data is not None and "units"   in data)
check("Has canvasW field",     data is not None and "canvasW" in data)
check("Has canvasH field",     data is not None and "canvasH" in data)
check("Has darkMode field",    data is not None and "darkMode" in data,
      f"keys={list(data.keys()) if data else None}")
check("Has runnerRunning",     data is not None and "runnerRunning" in data)
check("Has activeRunner",      data is not None and "activeRunner" in data)

# ── POST /api/settings ────────────────────────────────────────────────────────

section("Settings API  POST /api/settings")
# Read current settings first
_, orig = get_json("/api/settings")
orig_name = (orig or {}).get("name", "SlyLED Parent")
orig_dm   = (orig or {}).get("darkMode", 1)

new_name = "TestParent"
code, data = post_json("/api/settings", {
    "name": new_name, "units": 0,
    "canvasW": 10000, "canvasH": 5000,
    "darkMode": 1
})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)
_, verify = get_json("/api/settings")
check("Name persisted",  verify is not None and verify.get("name") == new_name,
      f"name={verify.get('name') if verify else None}")

# Restore original name
post_json("/api/settings", {"name": orig_name, "units": 0,
                             "canvasW": 10000, "canvasH": 5000,
                             "darkMode": orig_dm})

# ── GET /api/runners — initial state ──────────────────────────────────────────

section("Runners API — initial state  GET /api/runners")
code, data = get_json("/api/runners")
check("HTTP 200",       code == 200,             f"got {code}")
check("Valid JSON",     data is not None)
check("Returns array",  isinstance(data, list))

# ── Full runner lifecycle ──────────────────────────────────────────────────────

section("Runner lifecycle — create")
code, data = post_json("/api/runners", {"name": "TestRunner"})
check("HTTP 200",          code == 200,                              f"got {code}")
check("Returns ok:true",   data is not None and data.get("ok") is True)
check("Returns id field",  data is not None and "id" in data,        f"data={data}")
runner_id = data.get("id") if data else None

if runner_id is None:
    print(f"  {RED}Cannot continue runner lifecycle — no id returned{RESET}")
else:
    section(f"Runner lifecycle — GET /api/runners/{runner_id}")
    code, data = get_json(f"/api/runners/{runner_id}")
    check("HTTP 200",           code == 200,                           f"got {code}")
    check("Valid JSON",         data is not None)
    check("id matches",         data is not None and data.get("id") == runner_id)
    check("name is TestRunner", data is not None and data.get("name") == "TestRunner",
          f"name={data.get('name') if data else None}")
    check("steps is array",     data is not None and isinstance(data.get("steps"), list))
    check("computed is false",  data is not None and data.get("computed") is False,
          f"computed={data.get('computed') if data else None}")

    section(f"Runner lifecycle — PUT /api/runners/{runner_id} (add steps)")
    steps = [
        {"type": 1, "r": 255, "g": 0, "b": 0,
         "onMs": 500, "offMs": 500, "wdir": 0, "wspd": 50,
         "x0": 0, "y0": 0, "x1": 10000, "y1": 10000, "dur": 5},
        {"type": 2, "r": 0, "g": 0, "b": 255,
         "onMs": 300, "offMs": 200, "wdir": 0, "wspd": 50,
         "x0": 0, "y0": 0, "x1": 10000, "y1": 10000, "dur": 3},
    ]
    code, data = put_json(f"/api/runners/{runner_id}",
                          {"name": "TestRunner", "steps": steps})
    check("HTTP 200",          code == 200,                              f"got {code}")
    check("Returns ok:true",   data is not None and data.get("ok") is True)

    _, data2 = get_json(f"/api/runners/{runner_id}")
    check("Step count == 2",   data2 is not None and len(data2.get("steps", [])) == 2,
          f"steps={data2.get('steps') if data2 else None}")
    check("computed reset to false", data2 is not None and data2.get("computed") is False)

    section(f"Runner lifecycle — POST /api/runners/{runner_id}/compute")
    code, data = post_json(f"/api/runners/{runner_id}/compute")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)

    _, data3 = get_json(f"/api/runners/{runner_id}")
    check("computed == true after compute",
          data3 is not None and data3.get("computed") is True,
          f"computed={data3.get('computed') if data3 else None}")

    section("Runners list shows computed flag")
    _, rlist = get_json("/api/runners")
    found = next((r for r in (rlist or []) if r.get("id") == runner_id), None)
    check("Runner in list",         found is not None)
    check("List shows computed=true", found is not None and found.get("computed") is True,
          f"entry={found}")

    section(f"Runner lifecycle — POST /api/runners/stop")
    code, data = post_json("/api/runners/stop")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)

    _, sett = get_json("/api/settings")
    check("runnerRunning is false after stop",
          sett is not None and sett.get("runnerRunning") is False,
          f"runnerRunning={sett.get('runnerRunning') if sett else None}")

    section(f"Runner lifecycle — DELETE /api/runners/{runner_id}")
    code, data = delete(f"/api/runners/{runner_id}")
    check("HTTP 200",         code == 200,                              f"got {code}")
    check("Returns ok:true",  data is not None and data.get("ok") is True)

    code2, data2 = get_json(f"/api/runners/{runner_id}")
    check("GET after delete returns error",
          data2 is None or data2.get("ok") is False or data2.get("err") is not None,
          f"data={data2}")

# ── Runner bad-id handling ─────────────────────────────────────────────────────

section("Runner error handling")
code, data = get_json("/api/runners/99")
check("GET /api/runners/99 returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None),
      f"data={data}")

code, data = delete("/api/runners/99")
check("DELETE /api/runners/99 returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None),
      f"data={data}")

code, data = post_json("/api/runners/99/compute")
check("POST /api/runners/99/compute returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None),
      f"data={data}")

# ── POST /api/action (target=all, no children → no crash) ─────────────────────

section("Action API  POST /api/action")
code, data = post_json("/api/action",
    {"type": 1, "r": 255, "g": 0, "b": 0,
     "onMs": 500, "offMs": 500, "wipeDir": 0, "wipeSpeedPct": 50,
     "target": "all"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

code, data = post_json("/api/action", {"type": 0, "target": "all"})
check("ACT_OFF returns ok:true", data is not None and data.get("ok") is True)

section("Action stop  POST /api/action/stop")
code, data = post_json("/api/action/stop", {"target": "all"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

# Bad target id
code, data = post_json("/api/action", {"type": 1, "r": 0, "g": 0, "b": 0, "target": "99"})
check("Bad target returns error", data is not None and
      (data.get("ok") is False or data.get("err") is not None),
      f"data={data}")

# ── POST /api/children (add non-existent IP — OK to send, reply is ok:true) ───

section("Add child  POST /api/children (no-response scenario)")
code, data = post_json("/api/children", {"ip": "10.0.0.254"})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)
# Clean up — remove any child slot it added (it may not add if ping never replied)
_, kids = get_json("/api/children")
for k in (kids or []):
    if k.get("ip") == "10.0.0.254":
        delete(f"/api/children/{k['id']}")
        break

# ── POST /api/layout ──────────────────────────────────────────────────────────

section("Layout API  POST /api/layout (empty)")
code, data = post_json("/api/layout", {"children": []})
check("HTTP 200",        code == 200,                              f"got {code}")
check("Returns ok:true", data is not None and data.get("ok") is True)

# ── Runner create / compute — multiple runners ─────────────────────────────────

section("Multiple runners (up to MAX_RUNNERS=4)")
created_ids = []
for i in range(4):
    _, d = post_json("/api/runners", {"name": f"R{i}"})
    if d and d.get("ok"):
        created_ids.append(d["id"])
check(f"Created {len(created_ids)} runners",
      len(created_ids) >= 1, f"ids={created_ids}")

# 5th runner should fail (full)
_, d5 = post_json("/api/runners", {"name": "Overflow"})
check("5th runner returns error (full)",
      d5 is not None and (d5.get("ok") is False or d5.get("err") is not None),
      f"data={d5}")

# Clean up
for rid in created_ids:
    delete(f"/api/runners/{rid}")
_, rlist = get_json("/api/runners")
check("Runners list empty after cleanup",
      isinstance(rlist, list) and len(rlist) == 0,
      f"list={rlist}")

# ── Content-Length headers on JSON responses ──────────────────────────────────

section("Content-Length on JSON responses")
try:
    with urllib.request.urlopen(BASE + "/api/runners", timeout=TIMEOUT) as r:
        cl = r.headers.get("Content-Length", "")
        body_r = r.read()
        check("/api/runners has Content-Length",
              cl != "", f"Content-Length: '{cl}'")
        check("Content-Length matches body",
              cl == "" or int(cl) == len(body_r),
              f"header={cl} body={len(body_r)}")
    with urllib.request.urlopen(BASE + "/api/children", timeout=TIMEOUT) as r:
        cl2 = r.headers.get("Content-Length", "")
        check("/api/children has Content-Length", cl2 != "",
              f"Content-Length: '{cl2}'")
except Exception as e:
    check("Content-Length fetch", False, str(e))

# ── Summary ───────────────────────────────────────────────────────────────────

total = passed + failed
print(f"\n{BOLD}{'=' * 62}{RESET}")
if failed == 0:
    print(f"{BOLD}{GREEN}  ALL {total} TESTS PASSED{RESET}")
else:
    print(f"{BOLD}  {passed}/{total} passed   {RED}{failed} FAILED{RESET}")
print(f"{BOLD}{'=' * 62}{RESET}\n")

sys.exit(0 if failed == 0 else 1)
