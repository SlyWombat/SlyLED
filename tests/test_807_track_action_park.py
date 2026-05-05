#!/usr/bin/env python3
"""test_807_track_action_park.py — Regression for #807.

Two failure modes locked in:

1. A no-loop timeline driven by a Track action only (no baked segments
   for the targeted mover) used to leave the head frozen at its last
   commanded pose forever past `durationS` because the post-loop park
   iterated `dmx_fixtures` (baked-only). v1.7.55+ unions baked-segment
   movers with Track-action-targeted movers so every involved head is
   parked.

2. `runnerRunning` / `activeTimeline` weren't cleared on natural end —
   only on manual `/stop`. SPA "Show running" stayed misleading.

Run:  python -X utf8 tests/test_807_track_action_park.py
"""

import os
import sys
import time

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


def test_807_park_loop_targets_track_driven_movers():
    """Regression: the natural-end park loop in `_dmx_playback_loop`
    must include Track-action-targeted fixtures, not just baked-segment
    fixtures. We verify the union-set construction by inspecting the
    function's source — the underlying playback runs on a thread with
    real DMX engines so a hermetic end-to-end run is heavy; the static
    check locks in the fix and is cheap."""
    import inspect
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert("track_driven_fids" in src,
            "_dmx_playback_loop builds a track_driven_fids set")
    _assert("for ta in (a for a in _actions if a.get(\"type\") == 18)" in src,
            "_dmx_playback_loop iterates type=18 (Track) actions")
    _assert("for tfid in track_driven_fids" in src,
            "_dmx_playback_loop park step iterates track_driven_fids")
    _assert("_park_fixture_at_home(tfid)" in src,
            "park step calls _park_fixture_at_home for each track-driven fid")


def test_807_park_loop_respects_claim_arbiter_for_track_movers():
    """A track-driven mover currently held by mover-control must NOT
    be parked at natural-end. The operator owns its output until
    release."""
    import inspect
    src = inspect.getsource(parent_server._dmx_playback_loop)
    # Track-driven park branch wraps the same _claim_arbiter.is_muted
    # check the baked-segment branch uses.
    _assert("if _claim_arbiter.is_muted(tfid, final_snap):" in src,
            "track-driven park branch checks claim-arbiter mute")
    _assert("if _claim_arbiter.is_muted(fx[\"fid\"], final_snap):" in src,
            "baked-segment park branch (kept) checks claim-arbiter mute")


def test_807_park_loop_requires_full_canonical_data():
    """A track-driven mover without Home + Secondary stays put — no
    park, no error log noise. Same guard as the baked-segment branch."""
    import inspect
    src = inspect.getsource(parent_server._dmx_playback_loop)
    # Both branches wrap the park call in the Home + Secondary guard.
    _assert(src.count('rec.get("homeSecondary")') >= 2,
            "both park branches gate on homeSecondary being present")
    _assert(src.count('rec.get("homePanDmx16") is not None') >= 2,
            "both park branches gate on homePanDmx16 being present")


def test_807_natural_end_clears_runner_flags():
    """Pre-#807 the playback thread's loop-exit didn't clear
    `runnerRunning`/`activeTimeline`/`runnerStartEpoch`. Status endpoint
    kept reporting `running: true` past `durationS`. Verify the loop's
    exit branch now resets the settings dict."""
    import inspect
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert('_settings["runnerRunning"] = False' in src,
            "natural-end clears runnerRunning")
    _assert('_settings["activeTimeline"] = -1' in src,
            "natural-end clears activeTimeline")
    _assert('_settings["runnerStartEpoch"] = 0' in src,
            "natural-end clears runnerStartEpoch")
    _assert('_settings.get("activeTimeline") == tid' in src,
            "natural-end only clears when this timeline is still active "
            "(racey overlap with /api/timelines/<tid>/start of a different "
            "timeline must not stomp the new run's flags)")


def test_807_track_driven_includes_auto_discover_fallback():
    """A Track action with no `trackFixtureIds` set is the
    auto-discover variant — `_evaluate_track_actions` aims every Home +
    Secondary mover at the patrol target. The natural-end park union
    must mirror that candidate set, otherwise auto-discover timelines
    would still leave heads pointed at the last patrol position
    forever post-natural-end."""
    import inspect
    src = inspect.getsource(parent_server._dmx_playback_loop)
    _assert("Auto-discover Track action" in src,
            "auto-discover path documented + handled in track_driven_fids build")
    _assert("if listed:" in src,
            "explicit trackFixtureIds branch present")


ALL = [
    test_807_park_loop_targets_track_driven_movers,
    test_807_park_loop_respects_claim_arbiter_for_track_movers,
    test_807_park_loop_requires_full_canonical_data,
    test_807_natural_end_clears_runner_flags,
    test_807_track_driven_includes_auto_discover_fallback,
]


if __name__ == "__main__":
    print("=== #807 Track-action timeline natural-end park ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
