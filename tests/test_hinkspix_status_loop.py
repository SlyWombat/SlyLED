#!/usr/bin/env python3
"""Device status loop (#956).

A HinksPix added after startup went Offline after CHILD_STALE_S because the
status loop only started when devices already existed at boot. Covered:

  * start_background_tasks() starts the loop even with no devices
  * a device added after boot keeps status 1 while it answers its probe,
    past CHILD_STALE_S
  * a device that stops answering goes Offline
  * an exception in one sweep (or one device's probe) doesn't kill the loop

The loop runs for real in a thread with the intervals shrunk; the HTTP probe
and the UDP broadcast are stubbed, so nothing goes on the network.

Run: python3 tests/test_hinkspix_status_loop.py
"""

import os
import sys
import tempfile
import threading
import time

if not os.environ.get("SLYLED_DATA"):
    os.environ["SLYLED_DATA"] = tempfile.mkdtemp(prefix="slyled-status-test-")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server as ps  # noqa: E402

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
    print("start_background_tasks starts the status loop with no devices")
    ps._children[:] = []
    started = []
    real_thread = ps.threading.Thread

    class _Rec:
        def __init__(self, target=None, *a, **k):
            started.append(getattr(target, "__name__", str(target)))

        def start(self):
            pass

    real_init_audio = ps._init_local_audio_bri
    ps.threading.Thread = _Rec
    ps._init_local_audio_bri = lambda: None
    try:
        ps.start_background_tasks()
    except Exception as e:
        started.append(f"raised {e!r}")
    finally:
        ps.threading.Thread = real_thread
        ps._init_local_audio_bri = real_init_audio
    ok("_periodic_ping is started on an empty rig", "_periodic_ping" in started, started)

    print("A device added after boot stays Online while it answers")
    answering = {"on": True}
    probes = []
    calls = {"broadcast": 0}

    def fake_probe(child, timeout=2.0):
        probes.append(child.get("ip"))
        if child.get("ip") == "10.9.9.9":
            raise RuntimeError("bad record (test)")
        return {"mcpuRaw": "MS_160", "mcpu": 160} if answering["on"] else None

    def flaky_broadcast():
        calls["broadcast"] += 1
        if calls["broadcast"] == 4:          # blow up one sweep
            raise OSError("no interfaces (test)")

    saved = (ps._probe_child_http, ps._broadcast_ping_all, ps.PING_INTERVAL_S,
             ps._STARTUP_REPING_S, ps.CHILD_STALE_S, ps.time.sleep)
    ps._probe_child_http = fake_probe
    ps._broadcast_ping_all = flaky_broadcast
    ps.PING_INTERVAL_S = 0.2
    ps._STARTUP_REPING_S = 0.05
    ps.CHILD_STALE_S = 1
    real_sleep = saved[5]
    # the loop's fixed 2 s PONG wait → shrink too
    ps.time.sleep = lambda s: real_sleep(min(s, 0.05) if s == 2 else s)
    try:
        t = threading.Thread(target=ps._periodic_ping, daemon=True)
        t.start()
        real_sleep(0.3)
        # Added after the loop started — the #956 case.
        child = {"id": 9561, "type": "hinkspix", "ip": "192.168.10.6",
                 "name": "HP", "sc": 0, "strings": [], "status": 1,
                 "seen": int(time.time()), "hinks": {}}
        bad = {"id": 9562, "type": "wled", "ip": "10.9.9.9", "name": "bad",
               "status": 1, "seen": int(time.time())}
        ps._children.extend([bad, child])
        real_sleep(2.6)                      # > CHILD_STALE_S, several sweeps
        ok("loop thread still alive after a sweep raised",
           t.is_alive() and calls["broadcast"] > 4, calls)
        ok("device added after boot was re-probed",
           probes.count("192.168.10.6") >= 3, probes.count("192.168.10.6"))
        ok("…and is still Online past CHILD_STALE_S", child["status"] == 1, child)
        ok("seen keeps refreshing",
           int(time.time()) - child["seen"] <= 1, int(time.time()) - child["seen"])
        ok("a probe that raises marks only that device Offline", bad["status"] == 0)
        ok("firmware refreshed from the probe", child.get("fwVersion") == "MS_160")
        answering["on"] = False
        real_sleep(0.8)
        ok("a device that stops answering goes Offline", child["status"] == 0)
    finally:
        (ps._probe_child_http, ps._broadcast_ping_all, ps.PING_INTERVAL_S,
         ps._STARTUP_REPING_S, ps.CHILD_STALE_S, ps.time.sleep) = saved
        ps._children[:] = []

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
