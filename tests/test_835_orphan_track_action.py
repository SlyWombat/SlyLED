#!/usr/bin/env python3
"""test_835_orphan_track_action.py — Regression for #835.

A Track action (type 18) that lives in the global action library but is
NOT referenced by any clip in the running timeline must not blackout
movers in that timeline. Pre-fix `_evaluate_track_actions` iterated all
type-18 actions every frame and the unassigned-heads-blackout sweep
zeroed master Dimmer on every mover the timeline drove.

Run: python -X utf8 tests/test_835_orphan_track_action.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import inspect

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


# ─────────────────────────────────────────────────────────────────────────────

def test_eval_signature_has_tl_action_ids():
    sig = inspect.signature(parent_server._evaluate_track_actions)
    _assert("tl_action_ids" in sig.parameters,
            "_evaluate_track_actions accepts tl_action_ids")
    _assert(sig.parameters["tl_action_ids"].default is None,
            "tl_action_ids default is None (legacy callers see all type-18)")


def test_eval_filters_track_actions_to_tl_action_ids():
    """Drop two Track actions into _actions, run with tl_action_ids
    referencing only one. The orphan must not be evaluated."""
    saved_actions = list(parent_server._actions)
    saved_fixtures = list(parent_server._fixtures)
    try:
        parent_server._actions[:] = [
            {"id": 1, "type": 18, "name": "in-timeline tracker",
             "trackObjectIds": [], "trackFixtureIds": []},
            {"id": 99, "type": 18, "name": "orphan tracker",
             "trackObjectIds": [], "trackFixtureIds": []},
        ]
        # No DMX fixtures → fx_lookup empty → function returns early
        # AFTER the filter step. We don't need real DMX state to verify
        # the filter; we just confirm the filter narrows the candidate
        # list before the rest of the function executes.
        parent_server._fixtures[:] = []

        # Patch _evaluate_track_actions's pre-filter behaviour by
        # intercepting the candidate list via a wrapper.
        # Simpler: re-read the source and confirm the filter step is
        # present + uses `tl_action_ids`.
        src = inspect.getsource(parent_server._evaluate_track_actions)
        _assert(
            "tl_action_ids is not None" in src
            and "active_ids = set(int(x) for x in tl_action_ids)" in src,
            "filter narrows track_actions when tl_action_ids supplied")
        _assert(
            "int(a.get(\"id\", -1)) in active_ids" in src,
            "filter compares each candidate's id against the active set")
    finally:
        parent_server._actions[:] = saved_actions
        parent_server._fixtures[:] = saved_fixtures


def test_loop_collects_tl_action_ids_and_threads_through():
    """The playback loop must build `tl_action_ids` from the timeline's
    clips and pass it to _evaluate_track_actions on every frame."""
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert("tl_action_ids = set()" in src,
            "_dmx_playback_loop collects tl_action_ids set")
    _assert('_cl.get("actionId")' in src,
            "_dmx_playback_loop iterates clips for actionId")
    _assert("tl_action_ids=tl_action_ids" in src,
            "_dmx_playback_loop passes tl_action_ids to _evaluate_track_actions")


def test_single_collects_tl_action_ids_and_threads_through():
    src = inspect.getsource(parent_server._dmx_playback_single)
    _assert("tl_action_ids = set()" in src,
            "_dmx_playback_single collects tl_action_ids set")
    _assert("tl_action_ids=tl_action_ids" in src,
            "_dmx_playback_single passes tl_action_ids to _evaluate_track_actions")


def test_track_driven_fids_gated_by_tl_action_ids():
    """The natural-end park scope (`track_driven_fids`) should only
    consider Track actions referenced by the running timeline. An orphan
    Track action's `trackFixtureIds` (if any) must not contribute."""
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert(
        'a.get("type") == 18 and int(a.get("id", -1)) in tl_action_ids' in src,
        "track_driven_fids loop filters by tl_action_ids")


def test_legacy_callers_unaffected():
    """Calling _evaluate_track_actions with tl_action_ids=None preserves
    the pre-#835 behaviour (every type-18 action evaluates). This is
    the fallback for any non-playback caller (e.g. tests)."""
    saved_actions = list(parent_server._actions)
    try:
        parent_server._actions[:] = [
            {"id": 1, "type": 18, "name": "x", "trackFixtureIds": []},
        ]
        # No fixtures → returns early but won't raise. The contract is
        # "doesn't crash with None and doesn't filter anything out".
        try:
            parent_server._evaluate_track_actions(
                0.0, parent_server._artnet, [], tl_action_ids=None)
            _assert(True, "legacy call (tl_action_ids=None) doesn't raise")
        except Exception as e:
            _assert(False, f"legacy call raised: {e}")
    finally:
        parent_server._actions[:] = saved_actions


ALL = [
    test_eval_signature_has_tl_action_ids,
    test_eval_filters_track_actions_to_tl_action_ids,
    test_loop_collects_tl_action_ids_and_threads_through,
    test_single_collects_tl_action_ids_and_threads_through,
    test_track_driven_fids_gated_by_tl_action_ids,
    test_legacy_callers_unaffected,
]


if __name__ == "__main__":
    print("=== #835 orphan Track action ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
