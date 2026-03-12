#!/usr/bin/env python3
"""
SlyLED Web Interface Test Suite
Tests the HTTP/JSON API running on the Arduino Giga R1 WiFi.

Usage:
    python3 tests/test_web.py [host]
    python3 tests/test_web.py 192.168.10.219
"""

import sys
import time
import json
import urllib.request
import urllib.error

HOST    = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.219"
BASE    = f"http://{HOST}"
TIMEOUT = 6

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = 0
failed = 0


def get(path):
    """GET a path, return (status_code, body). Returns (0, error_str) on failure."""
    try:
        with urllib.request.urlopen(BASE + path, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def post(path):
    """POST a path (empty body), return (status_code, body)."""
    try:
        req = urllib.request.Request(BASE + path, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def get_json(path):
    """GET a path and parse JSON body. Returns (status_code, dict or None)."""
    code, body = get(path)
    try:
        return code, json.loads(body)
    except Exception:
        return code, None


def post_json(path):
    """POST a path and parse JSON body. Returns (status_code, dict or None)."""
    code, body = post(path)
    try:
        return code, json.loads(body)
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
    print(f"\n{BOLD}{'-' * 58}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'-' * 58}{RESET}")


def log_row_count(body):
    return body.count("<tr><td>")


# ── Connectivity ──────────────────────────────────────────────────────────────

section(f"Connectivity  ({HOST})")
code, body = get("/")
if not check("Board reachable", code == 200, f"HTTP {code}: {body[:80]}"):
    print(f"\n  {RED}Cannot reach board — aborting.{RESET}\n")
    sys.exit(1)

# ── SPA main page ─────────────────────────────────────────────────────────────

section("SPA main page  GET /")
check("HTTP 200",                 code == 200,              f"got {code}")
check("Title contains SlyLED",    "SlyLED"      in body)
check("Has header element",       "id='hdr'"    in body or 'id="hdr"' in body)
check("Has status element",       "hdr-status"  in body)
check("Has badge element",        "id='badge'"  in body or 'id="badge"' in body)
check("Has Enable button",        "Enable"      in body)
check("Has Disable button",       "Disable"     in body)
check("Has /led/on in JS",        "/led/on"     in body)
check("Has /led/off in JS",       "/led/off"    in body)
check("Has /status poll in JS",   "/status"     in body)
check("Has View Log link",        "href='/log'" in body or 'href="/log"' in body)
check("Has version string",       "v1." in body or "v2." in body)
check("No old form /on action",   "action='/on'"  not in body and 'action="/on"'  not in body)
check("No old form /off action",  "action='/off'" not in body and 'action="/off"' not in body)

# ── Cache-Control headers ─────────────────────────────────────────────────────

section("Cache-Control headers")
try:
    with urllib.request.urlopen(BASE + "/", timeout=TIMEOUT) as r:
        cc = r.headers.get("Cache-Control", "")
        check("/ has no-cache",  "no-cache" in cc.lower(), f"Cache-Control: '{cc}'")
        check("/ has no-store",  "no-store" in cc.lower(), f"Cache-Control: '{cc}'")
    with urllib.request.urlopen(BASE + "/log", timeout=TIMEOUT) as r2:
        cc2 = r2.headers.get("Cache-Control", "")
        check("/log has no-cache", "no-cache" in cc2.lower(), f"Cache-Control: '{cc2}'")
        check("/log has no-store", "no-store" in cc2.lower(), f"Cache-Control: '{cc2}'")
except Exception as e:
    check("Cache-Control header fetch", False, str(e))

# ── GET /status ───────────────────────────────────────────────────────────────

section("Status endpoint  GET /status")
code, data = get_json("/status")
check("HTTP 200",                  code == 200,                    f"got {code}")
check("Valid JSON",                data is not None,               "failed to parse JSON")
check("Has onboard_led key",       data is not None and "onboard_led" in data)
check("onboard_led.active is bool",
      data is not None and isinstance(data.get("onboard_led", {}).get("active"), bool))

# ── Enable rainbow  POST /led/on ──────────────────────────────────────────────

section("Enable rainbow  POST /led/on")
code, data = post_json("/led/on")
check("HTTP 200",         code == 200,                               f"got {code}")
check("Returns ok:true",  data is not None and data.get("ok") is True)
time.sleep(0.4)
_, st = get_json("/status")
check("/status shows active:true",
      st is not None and st.get("onboard_led", {}).get("active") is True,
      f"status: {st}")

# ── Disable rainbow  POST /led/off ────────────────────────────────────────────

section("Disable rainbow  POST /led/off")
code, data = post_json("/led/off")
check("HTTP 200",          code == 200,                               f"got {code}")
check("Returns ok:true",   data is not None and data.get("ok") is True)
time.sleep(0.4)
_, st = get_json("/status")
check("/status shows active:false",
      st is not None and st.get("onboard_led", {}).get("active") is False,
      f"status: {st}")

# ── State toggle sequence ─────────────────────────────────────────────────────

section("State toggle sequence")
post_json("/led/off"); time.sleep(0.3)
_, st = get_json("/status")
check("After /led/off: active=false", st is not None and st.get("onboard_led", {}).get("active") is False)

post_json("/led/on");  time.sleep(0.3)
_, st = get_json("/status")
check("After /led/on:  active=true",  st is not None and st.get("onboard_led", {}).get("active") is True)

post_json("/led/off"); time.sleep(0.3)
_, st = get_json("/status")
check("After /led/off again: active=false", st is not None and st.get("onboard_led", {}).get("active") is False)

# Re-enable for subsequent tests
post_json("/led/on"); time.sleep(0.3)

# ── Rapid-fire AJAX commands ──────────────────────────────────────────────────

section("Rapid-fire AJAX commands (no sleep)")
results = []
for _ in range(5):
    c2, d2 = post_json("/led/on")
    results.append(c2 == 200 and d2 is not None and d2.get("ok") is True)
check("5x POST /led/on all return ok:true", all(results), f"results: {results}")
time.sleep(0.3)
_, st = get_json("/status")
check("Status active after rapid-fire", st is not None and st.get("onboard_led", {}).get("active") is True)

# ── Log page — structure ──────────────────────────────────────────────────────

section("Log page  GET /log")
code, body = get("/log")
check("HTTP 200",               code == 200,   f"got {code}")
check("Contains 'Event Log'",   "Event Log" in body)
check("Has <table>",            "<table>"   in body)
check("Has Source column",      ">Source<"  in body)
check("Has IP column",          ">IP<"      in body)
check("Back is anchor href=/",  "href='/'"  in body or 'href="/"' in body)
check("No POST form on log",    "action='/'" not in body and 'action="/"' not in body)

# ── Log page — entries ────────────────────────────────────────────────────────

section("Log page — entries")
post("/led/off"); time.sleep(0.3)
post("/led/on");  time.sleep(0.3)
post("/led/off"); time.sleep(0.3)
post("/led/on");  time.sleep(0.3)

code, body = get("/log")
check("HTTP 200",             code == 200, f"got {code}")
check("Has ON  entry",        ">ON<"  in body)
check("Has OFF entry",        ">OFF<" in body)
row_count = log_row_count(body)
check("Has 4+ entries",       row_count >= 4, f"found {row_count} data rows")
check("Has source labels",    ">Web<" in body or ">Boot<" in body)
check("Has IP address",       any(f"{i}." in body for i in range(1, 255)))

first_on  = body.find(">ON<")
first_off = body.find(">OFF<")
check("Newest entry is ON (last action was /led/on)",
      first_on < first_off if first_on >= 0 and first_off >= 0 else False)

# ── Log entry count — multiple presses ───────────────────────────────────────

section("Log entry counting — multiple presses")
_, before = get("/log")
count_before = log_row_count(before)

post("/led/off"); time.sleep(0.3)
post("/led/off"); time.sleep(0.3)
post("/led/off"); time.sleep(0.3)
post("/led/on");  time.sleep(0.3)
post("/led/on");  time.sleep(0.3)

_, after = get("/log")
count_after = log_row_count(after)
delta = count_after - count_before
check("3x off + 2x on = 5 new log entries", delta == 5,
      f"before={count_before}, after={count_after}, delta={delta}")

# ── Log consistency — repeated fetches ───────────────────────────────────────

section("Log consistency — repeated fetches")
all_logs = True
for i in range(5):
    _, lb = get("/log")
    if "Event Log" not in lb or "<table>" not in lb:
        all_logs = False
        print(f"         {YELLOW}fetch #{i+1}: got wrong page{RESET}")
    time.sleep(0.1)
check("/log returns log page on 5 consecutive fetches", all_logs)

all_rapid = True
for i in range(3):
    _, lb = get("/log")
    if "Event Log" not in lb:
        all_rapid = False
        print(f"         {YELLOW}rapid fetch #{i+1}: got wrong page{RESET}")
check("/log returns log on 3 rapid fetches (no delay)", all_rapid)

# ── Navigation ────────────────────────────────────────────────────────────────

section("Navigation")
code_log, body_log = get("/log")
check("/log loads",               code_log == 200)
check("Back anchor on /log",      "href='/'" in body_log or 'href="/"' in body_log)

code_main, body_main = get("/")
check("/ loads",                  code_main == 200)
check("View Log link on /",       "href='/log'" in body_main or 'href="/log"' in body_main)

# ── Summary ───────────────────────────────────────────────────────────────────

total = passed + failed
print(f"\n{BOLD}{'=' * 58}{RESET}")
if failed == 0:
    print(f"{BOLD}{GREEN}  ALL {total} TESTS PASSED{RESET}")
else:
    print(f"{BOLD}  {passed}/{total} passed   {RED}{failed} FAILED{RESET}")
print(f"{BOLD}{'=' * 58}{RESET}\n")

sys.exit(0 if failed == 0 else 1)
