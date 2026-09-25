#!/usr/bin/env python3
"""solar.py — sunrise/sunset for the show scheduler (#954).

Pinned against published Toronto sun times (43.6532 N, 79.3832 W; NRC Canada /
timeanddate agree to the minute for these dates), tolerance ±2 min as the
issue specifies. Also: polar day/night returns None, results are UTC-aware.

Run: python3 tests/test_solar.py
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import solar  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Toronto")
except Exception:  # pragma: no cover
    TZ = None

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


LAT, LON = 43.6532, -79.3832

# (date, event, local HH:MM) — published values for Toronto.
PINNED = [
    (date(2026, 12, 21), "sunrise", "07:47"),
    (date(2026, 12, 21), "sunset", "16:44"),
    (date(2026, 6, 21), "sunrise", "05:36"),
    (date(2026, 6, 21), "sunset", "21:03"),
    (date(2026, 9, 22), "sunrise", "07:04"),
    (date(2026, 9, 22), "sunset", "19:14"),
]


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def main():
    print("Toronto sunrise/sunset within ±2 min of published values")
    for d, ev, want in PINNED:
        got = solar.sun_times(d, LAT, LON)[ev]
        ok(f"{d} {ev} is UTC-aware", got is not None and got.utcoffset().total_seconds() == 0)
        loc = got.astimezone(TZ)
        diff = abs(loc.hour * 60 + loc.minute + loc.second / 60 - minutes(want))
        ok(f"{d} {ev} {loc.strftime('%H:%M')} ≈ {want}", diff <= 2, f"off by {diff:.1f} min")

    print("Civil twilight brackets sunrise/sunset")
    s = solar.sun_times(date(2026, 12, 21), LAT, LON)
    ok("civil dawn before sunrise", s["civilDawn"] < s["sunrise"])
    ok("civil dusk after sunset", s["civilDusk"] > s["sunset"])
    ok("civil dusk ~30-40 min after sunset",
       25 <= (s["civilDusk"] - s["sunset"]).total_seconds() / 60 <= 45)

    print("A western sunset later than 00:00 UTC lands on the right instant")
    ss = solar.sun_times(date(2026, 6, 21), LAT, LON)["sunset"]
    ok("June sunset is 01:03 UTC the next day",
       ss.date() == date(2026, 6, 22) and ss.hour == 1, ss)

    print("Polar day / night")
    ok("Svalbard midsummer: no sunset", solar.sun_times(date(2026, 6, 21), 78.2, 15.6)["sunset"] is None)
    ok("Svalbard midwinter: no sunrise", solar.sun_times(date(2026, 12, 21), 78.2, 15.6)["sunrise"] is None)

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
