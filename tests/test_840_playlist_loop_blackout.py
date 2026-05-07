#!/usr/bin/env python3
"""test_840_playlist_loop_blackout.py — Regression for #840.

A looping playlist must not blackout the universe between iterations.
Pre-fix `_show_playback_loop` called `_dmx_playback_single` per
iteration; that function unconditionally ran a #364-style zero-sweep
on natural end, producing a one-frame all-zero DMX frame at every
loop wrap.

This test verifies via static source inspection that:

  1. `_dmx_playback_single` accepts `is_final` (default True for legacy
     callers).
  2. The blackout sweep is gated on `is_final or ended_by_stop`.
  3. `_show_playback_loop` routes single-item `loop_all=True` playlists
     directly to `_dmx_playback_loop(loop=True)`, which has the correct
     modulo-wrap semantics.
  4. Multi-item playlists pass `is_final=False` for non-final
     iterations so the blackout doesn't fire mid-playlist.

Run: python -X utf8 tests/test_840_playlist_loop_blackout.py
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

def test_single_signature_has_is_final():
    sig = inspect.signature(parent_server._dmx_playback_single)
    _assert("is_final" in sig.parameters,
            "_dmx_playback_single accepts is_final")
    _assert(sig.parameters["is_final"].default is True,
            "is_final default is True (legacy / non-orchestrator callers)")


def test_blackout_sweep_gated_on_is_final_or_stop():
    src = inspect.getsource(parent_server._dmx_playback_single)
    _assert("if is_final or ended_by_stop:" in src,
            "blackout sweep guarded by is_final OR ended_by_stop")
    _assert("ended_by_stop = _dmx_playback_stop.is_set()" in src,
            "ended_by_stop derives from the stop flag")


def test_single_item_loop_all_routes_to_dmx_playback_loop():
    src = inspect.getsource(parent_server._show_playback_loop)
    _assert("len(tl_list) == 1" in src
            and "loop_all" in src
            and "_dmx_playback_loop(tid, time.time(), duration, loop=True)" in src,
            "single-item loop_all routes through _dmx_playback_loop(loop=True)")


def test_multi_item_iteration_passes_is_final_correctly():
    """Mid-playlist iterations get is_final=False so the orchestrator
    doesn't run the blackout between segments."""
    src = inspect.getsource(parent_server._show_playback_loop)
    _assert(
        "is_final = (idx == len(tl_list) - 1) and not loop_all" in src,
        "is_final is True only on the last item of a non-looping playlist")
    _assert("is_final=is_final" in src,
            "_dmx_playback_single called with computed is_final")


def test_dmx_playback_loop_unchanged_correct_loop_path():
    """The _dmx_playback_loop function was already correct (modulo wrap,
    no per-wrap blackout). Sanity-check it's still the right shape so
    we don't accidentally regress it while fixing the orchestrator."""
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert("loop" in inspect.signature(parent_server._dmx_playback_loop).parameters,
            "_dmx_playback_loop still takes a loop kwarg")
    # The function uses elapsed % duration when loop=True
    _assert("% duration" in src,
            "_dmx_playback_loop still uses modulo-wrap on elapsed")


ALL = [
    test_single_signature_has_is_final,
    test_blackout_sweep_gated_on_is_final_or_stop,
    test_single_item_loop_all_routes_to_dmx_playback_loop,
    test_multi_item_iteration_passes_is_final_correctly,
    test_dmx_playback_loop_unchanged_correct_loop_path,
]


if __name__ == "__main__":
    print("=== #840 playlist loop blackout-between-iterations ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
