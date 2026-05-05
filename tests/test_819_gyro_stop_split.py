#!/usr/bin/env python3
"""test_819_gyro_stop_split.py — Regression for #819.

Asserts the orchestrator no longer treats GyroOrientPayload.flags bit 3
as a STOP signal. The discrete `CMD_GYRO_STOP = 0x69` is the only path
that releases a gyro claim. Bit 3 of orient flags is reserved-for-future
and the handler must process the orient (update Remote pose) regardless
of whether bit 3 is set.

Run:  python -X utf8 tests/test_819_gyro_stop_split.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


def test_proto_bumped_to_v5():
    _assert(parent_server.UDP_VERSION == 5,
            f"UDP_VERSION == 5 (got {parent_server.UDP_VERSION})")


def test_cmd_gyro_stop_constant_defined():
    _assert(hasattr(parent_server, "CMD_GYRO_STOP"),
            "CMD_GYRO_STOP defined on module")
    _assert(getattr(parent_server, "CMD_GYRO_STOP", None) == 0x69,
            f"CMD_GYRO_STOP == 0x69 (got {getattr(parent_server, 'CMD_GYRO_STOP', None)})")


def test_orient_handler_no_longer_releases_on_bit3():
    """The CMD_GYRO_ORIENT branch must NOT call _mover_engine.release on
    bit 3. Source-level check: the orient handler body must not contain
    `_mover_engine.release(` inside an `if flags & 0x08:` block.
    """
    import inspect
    src = inspect.getsource(parent_server)
    # Find the orient handler block
    marker = "elif cmd == CMD_GYRO_ORIENT and len(data) >= 16:"
    idx = src.find(marker)
    _assert(idx >= 0, "CMD_GYRO_ORIENT handler still exists")
    if idx < 0:
        return
    # Body extends until the next `elif cmd == ` at the same indent
    rest = src[idx + len(marker):]
    next_elif = rest.find("\n        elif cmd ==")
    body = rest if next_elif < 0 else rest[:next_elif]
    _assert("_mover_engine.release(" not in body,
            "no _mover_engine.release(...) in CMD_GYRO_ORIENT handler body")
    _assert("end_session()" not in body,
            "no remote.end_session() in CMD_GYRO_ORIENT handler body")


def test_cmd_gyro_stop_handler_present_and_releases():
    """The CMD_GYRO_STOP branch must call _mover_engine.release with
    blackout=True and end_session() — the same teardown the bit-3 branch
    used to do.
    """
    import inspect
    src = inspect.getsource(parent_server)
    marker = "elif cmd == CMD_GYRO_STOP:"
    idx = src.find(marker)
    _assert(idx >= 0, "CMD_GYRO_STOP handler exists in dispatcher")
    if idx < 0:
        return
    rest = src[idx:]
    next_elif = rest[len(marker):].find("\n        elif cmd ==")
    body = rest if next_elif < 0 else rest[:len(marker) + next_elif]
    _assert("_mover_engine.release(" in body,
            "CMD_GYRO_STOP handler calls _mover_engine.release(...)")
    _assert("blackout=True" in body,
            "CMD_GYRO_STOP handler passes blackout=True")
    _assert("end_session()" in body,
            "CMD_GYRO_STOP handler calls remote.end_session()")


def test_legacy_v4_packets_still_accepted():
    """An old field-deployed child (gyro v1.2.5, LED 7.5.x, etc.) sends
    UDP_VERSION = 4. The orchestrator's accept-list must still admit v4
    so a flag-day reflash isn't required to keep the system online.
    """
    import inspect
    src = inspect.getsource(parent_server)
    _assert("ver not in (3, 4, UDP_VERSION)" in src
            or "ver in (3, 4, UDP_VERSION)" in src
            or "ver not in (3, 4," in src,
            "UDP-version accept-list still includes v3 and v4")


ALL = [
    test_proto_bumped_to_v5,
    test_cmd_gyro_stop_constant_defined,
    test_orient_handler_no_longer_releases_on_bit3,
    test_cmd_gyro_stop_handler_present_and_releases,
    test_legacy_v4_packets_still_accepted,
]


if __name__ == "__main__":
    print("=== #819 CMD_GYRO_STOP split from orient bit-3 ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
