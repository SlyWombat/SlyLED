#!/usr/bin/env python3
"""
SlyLED Web Interface Test Suite
Tests the HTTP interface running on the Arduino Giga R1 WiFi.

Usage:
    python3 tests/test_web.py [host]
    python3 tests/test_web.py 192.168.10.219
"""

import sys
import time
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


def snippet(body, keyword, width=60):
    i = body.find(keyword)
    if i < 0:
        return f"'{keyword}' not found in response"
    start = max(0, i - 15)
    end   = min(len(body), i + len(keyword) + 15)
    return repr(body[start:end])


def is_on(body):
    return "#4c4'>" in body or "'#4c4'>" in body


def is_off(body):
    return "#c44'>" in body or "'#c44'>" in body


def log_row_count(body):
    """Count data rows in the log table (each starts with <tr><td>)."""
    return body.count("<tr><td>")


# ── Connectivity ──────────────────────────────────────────────────────────────

section(f"Connectivity  ({HOST})")
code, body = get("/")
if not check("Board reachable", code == 200, f"HTTP {code}: {body[:80]}"):
    print(f"\n  {RED}Cannot reach board — aborting.{RESET}\n")
    sys.exit(1)

# ── Main page ─────────────────────────────────────────────────────────────────

section("Main page  GET /")
check("HTTP 200",              code == 200,              f"got {code}")
check("Title contains SlyLED", "SlyLED"      in body)
check("Shows 'Rainbow is'",    "Rainbow is"  in body)
check("Has Turn On button",    "Turn On"     in body)
check("Has Turn Off button",   "Turn Off"    in body)
check("Has /log link",         "href='/log'" in body or 'href="/log"' in body)
check("Has version string",    "APP_VERSION" not in body and ("v1." in body or "v2." in body),
      "Expected version like 'v1.x' in page")
check("Turn On is POST form",  "action='/on'"  in body or 'action="/on"'  in body)
check("Turn Off is POST form", "action='/off'" in body or 'action="/off"' in body)

# ── Cache-Control headers ─────────────────────────────────────────────────────

section("Cache-Control headers")
try:
    req = urllib.request.Request(BASE + "/")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        cc = r.headers.get("Cache-Control", "")
        check("/ has no-cache header",    "no-cache" in cc.lower(), f"Cache-Control: '{cc}'")
        check("/ has no-store directive", "no-store" in cc.lower(), f"Cache-Control: '{cc}'")
    req2 = urllib.request.Request(BASE + "/log")
    with urllib.request.urlopen(req2, timeout=TIMEOUT) as r2:
        cc2 = r2.headers.get("Cache-Control", "")
        check("/log has no-cache header",    "no-cache" in cc2.lower(), f"Cache-Control: '{cc2}'")
        check("/log has no-store directive", "no-store" in cc2.lower(), f"Cache-Control: '{cc2}'")
except Exception as e:
    check("Cache-Control header fetch", False, str(e))

# ── Turn On (GET — backward compat) ──────────────────────────────────────────

section("Turn On  GET /on")
code, body = get("/on")
check("HTTP 200",       code == 200, f"got {code}")
check("Shows ON state", is_on(body),  snippet(body, "Rainbow is"))
check("Has Turn Off button", "Turn Off" in body)
check("Has /log link",  "href='/log'" in body or 'href="/log"' in body)

# ── Turn Off (GET — backward compat) ─────────────────────────────────────────

section("Turn Off  GET /off")
code, body = get("/off")
check("HTTP 200",        code == 200, f"got {code}")
check("Shows OFF state", is_off(body), snippet(body, "Rainbow is"))
check("Has Turn On button", "Turn On" in body)

time.sleep(0.3)
code2, body2 = get("/")
check("Main page still shows OFF", is_off(body2), snippet(body2, "Rainbow is"))

# ── Turn On again (GET) ───────────────────────────────────────────────────────

section("Turn On again  GET /on")
code, body = get("/on")
check("HTTP 200",       code == 200, f"got {code}")
check("Shows ON state", is_on(body),  snippet(body, "Rainbow is"))

time.sleep(0.3)
code2, body2 = get("/")
check("Main page shows ON", is_on(body2), snippet(body2, "Rainbow is"))

# ── Turn Off via POST (browser form submission) ───────────────────────────────

section("Turn Off  POST /off  (form submission)")
code, body = post("/off")
check("HTTP 200",        code == 200, f"got {code}")
check("Shows OFF state", is_off(body), snippet(body, "Rainbow is"))

# ── Turn On via POST ──────────────────────────────────────────────────────────

section("Turn On  POST /on  (form submission)")
code, body = post("/on")
check("HTTP 200",       code == 200, f"got {code}")
check("Shows ON state", is_on(body), snippet(body, "Rainbow is"))

# ── Log page — structure ──────────────────────────────────────────────────────

section("Log page  GET /log")
code, body = get("/log")
check("HTTP 200",              code == 200,    f"got {code}")
check("Contains 'Event Log'",  "Event Log" in body)
check("Has <table>",           "<table>"   in body)
check("Has Source column",     "<th>Source</th>" in body or ">Source<" in body)
check("Has Back link (href='/')", "href='/'" in body or 'href="/"' in body)

# ── Log page — entries ────────────────────────────────────────────────────────

section("Log page — entries")

# Generate a known sequence: off → on → off → on
get("/off"); time.sleep(0.3)
get("/on");  time.sleep(0.3)
get("/off"); time.sleep(0.3)
get("/on");  time.sleep(0.3)

code, body = get("/log")
check("HTTP 200",             code == 200, f"got {code}")
check("Has ON  entry",        ">ON<"  in body)
check("Has OFF entry",        ">OFF<" in body)
row_count = log_row_count(body)
check("Has 4+ entries",       row_count >= 4, f"found {row_count} data rows")
check("Has source labels (Web or Boot)", ">Web<" in body or ">Boot<" in body,
      "Expected source label in log rows")

# Newest entry should be ON (last action was /on)
first_row_on  = body.find(">ON<")
first_row_off = body.find(">OFF<")
check("Newest entry is ON (row 1)",
      first_row_on < first_row_off if first_row_on >= 0 and first_row_off >= 0 else False,
      "Expected ON to appear before OFF in newest-first order")

# ── Log entry counting — multiple presses ─────────────────────────────────────

section("Log entry counting — multiple presses")

# Snapshot current count, then fire 3 OFF presses and 2 ON presses
_, body_before = get("/log")
count_before = log_row_count(body_before)

post("/off"); time.sleep(0.3)
post("/off"); time.sleep(0.3)
post("/off"); time.sleep(0.3)
post("/on");  time.sleep(0.3)
post("/on");  time.sleep(0.3)

_, body_after = get("/log")
count_after = log_row_count(body_after)
new_entries = count_after - count_before

check("3× POST /off + 2× POST /on → 5 new log entries",
      new_entries == 5,
      f"before={count_before}, after={count_after}, delta={new_entries} (expected 5)")

# Verify the newest two entries are ON (last two presses were /on)
# In newest-first order, first >ON< should appear before first >OFF<
on_pos  = body_after.find(">ON<")
off_pos = body_after.find(">OFF<")
check("Last two presses (ON) appear first in log",
      on_pos >= 0 and on_pos < off_pos,
      f"ON at {on_pos}, OFF at {off_pos}")

# ── Log consistency — repeated fetches return log not main page ───────────────

section("Log consistency — repeated fetches")

# Fetch /log 5 times in a row; each must return log page, not main page
all_logs = True
for i in range(5):
    _, lb = get("/log")
    if "Event Log" not in lb or "<table>" not in lb:
        all_logs = False
        print(f"         {YELLOW}fetch #{i+1}: got main page instead of log{RESET}")
    time.sleep(0.1)
check("/log returns log page on 5 consecutive fetches", all_logs)

# Rapid-fire: no delay between fetches
all_rapid = True
for i in range(3):
    _, lb = get("/log")
    if "Event Log" not in lb:
        all_rapid = False
        print(f"         {YELLOW}rapid fetch #{i+1}: got main page{RESET}")
check("/log returns log on 3 rapid consecutive fetches (no delay)", all_rapid)

# ── Navigation ────────────────────────────────────────────────────────────────

section("Navigation")
code_log, body_log = get("/log")
check("/log loads from link",  code_log == 200)
check("Back link on /log page", "href='/'" in body_log or 'href="/"' in body_log)

code_main, body_main = get("/")
check("/ loads from Back",     code_main == 200)
check("View Log link on /",    "href='/log'" in body_main or 'href="/log"' in body_main)

# ── Summary ───────────────────────────────────────────────────────────────────

total = passed + failed
print(f"\n{BOLD}{'=' * 58}{RESET}")
if failed == 0:
    print(f"{BOLD}{GREEN}  ALL {total} TESTS PASSED{RESET}")
else:
    print(f"{BOLD}  {passed}/{total} passed   {RED}{failed} FAILED{RESET}")
print(f"{BOLD}{'=' * 58}{RESET}\n")

sys.exit(0 if failed == 0 else 1)
