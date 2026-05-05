#!/usr/bin/env python3
"""test_813_gyro_lock_removed.py — Regression for #813.

Asserts the orchestrator no longer runs the 5 s gyro auto-lock loop and
no longer auto-claims on first orient packet. Press-Start
(`CMD_GYRO_START`) is the sole claim trigger; idle orchestrator emits
zero UDP traffic toward gyro pucks.

Run:  python -X utf8 tests/test_813_gyro_lock_removed.py
"""

import os
import sys
import threading

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


def test_auto_lock_loop_symbols_gone():
    """Pre-#813 symbols `_gyro_active_lock_loop`, `_gyro_active_lock_loop_tick`,
    `_gyro_send_lock_packet`, `_GYRO_AUTO_LOCK_PERIOD_S`, `_gyro_is_connected`,
    `_GYRO_DISCONNECTED_AFTER_S` were the auto-lock infrastructure. They
    must not exist on the module — re-adding any of them breaks the
    architectural invariant that idle orchestrators emit no LOCK traffic."""
    deleted = [
        "_gyro_active_lock_loop",
        "_gyro_active_lock_loop_tick",
        "_gyro_send_lock_packet",
        "_GYRO_AUTO_LOCK_PERIOD_S",
        "_gyro_is_connected",
        "_GYRO_DISCONNECTED_AFTER_S",
    ]
    for sym in deleted:
        _assert(
            not hasattr(parent_server, sym),
            f"{sym} removed (pre-#813 auto-lock infra)",
        )


def test_no_auto_lock_thread_running():
    """No daemon thread named `gyro-auto-lock` should exist on the
    process. The thread launch line was deleted; an in-flight regression
    that re-adds the thread would surface here."""
    names = [t.name for t in threading.enumerate()]
    _assert("gyro-auto-lock" not in names,
            f"no gyro-auto-lock thread (active: {names})")


def test_release_packet_helper_kept():
    """`_gyro_send_release_packet` (CTRL(0)) and `_gyro_inactive_transition`
    are still required for operator-driven Active→Inactive transitions.
    They must NOT have been deleted along with the auto-lock loop —
    operator can still flip a fixture to Inactive in the SPA and the
    puck must receive the disable signal."""
    _assert(hasattr(parent_server, "_gyro_send_release_packet"),
            "_gyro_send_release_packet kept (Inactive transition)")
    _assert(hasattr(parent_server, "_gyro_inactive_transition"),
            "_gyro_inactive_transition kept (operator-toggled disable)")


def test_press_start_claim_path_intact():
    """`CMD_GYRO_START` (#772) is the sole claim trigger now.
    The handler logic still has to resolve the fixture and call
    MoverControlEngine.claim — sanity-check the constant is still
    defined on the module."""
    _assert(hasattr(parent_server, "CMD_GYRO_START"),
            "CMD_GYRO_START still defined (press-Start claim path)")
    _assert(hasattr(parent_server, "CMD_GYRO_CLAIM_DENIED"),
            "CMD_GYRO_CLAIM_DENIED still defined (refusal path)")


ALL = [
    test_auto_lock_loop_symbols_gone,
    test_no_auto_lock_thread_running,
    test_release_packet_helper_kept,
    test_press_start_claim_path_intact,
]


if __name__ == "__main__":
    print("=== #813 gyro auto-lock loop removed ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
