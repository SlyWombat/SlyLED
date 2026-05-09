"""test_871_act_params.py — `_act_params` per-action-type packing contract.

Pre-#871 the dict.get chain in `_act_params` short-circuited at the
first present alias (typically a zero-defaulted r2/g2/b2 from the
bake's fully-keyed params dict), so Fire / Comet / Twinkle / etc.
all sent (p8a=0, p8b=0, p8c=0, p8d=0) regardless of the action
body's `cooling`, `sparking`, `tailLen`, etc. The fire algorithm
on the firmware then produced no sparks → LEDs dark during show
playback while the firmware's local test function still worked.

These tests cover every ACT_* type listed in main/Protocol.h and
verify the wire tuple matches the render-function signatures in
main/ChildLED.cpp:516-528. Each test uses a "fully-keyed" params
dict that mirrors what the bake actually produces, so the chain-
short bug pattern is reproduced if it regresses.

Run: python -X utf8 tests/test_871_act_params.py
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


def _bake_keyed_action(type_, **overrides):
    """Build a params dict shaped like what the bake emits — every
    alias key present, defaulted to 0 — so the chain-short bug
    surfaces if `_act_params` regresses to dict.get-chain logic.
    Caller overrides specific keys to non-zero to verify they're
    read (or NOT read, for fields the action type doesn't use)."""
    base = {
        "type": type_,
        "r": 0, "g": 0, "b": 0,
        "r2": 0, "g2": 0, "b2": 0,
        "speedMs": 0, "periodMs": 0, "spawnMs": 0,
        "minBri": 0, "spacing": 0, "paletteId": 0,
        "cooling": 0, "sparking": 0, "direction": 0,
        "tailLen": 0, "density": 0, "decay": 0,
        "fadeSpeed": 0,
    }
    base.update(overrides)
    return base


def test_solid():
    # ACT_SOLID (1): r, g, b only — no p8 / p16a slots used.
    out = parent_server._act_params(_bake_keyed_action(1, r=255, g=0, b=128))
    _assert(out == (1, 255, 0, 128, 0, 0, 0, 0, 0),
            f"SOLID → (1, 255, 0, 128, 0, 0, 0, 0, 0)  got {out}")


def test_fade():
    # ACT_FADE (2): p8a=r2, p8b=g2, p8c=b2, p16a=speedMs.
    out = parent_server._act_params(_bake_keyed_action(
        2, r=255, g=0, b=0, r2=0, g2=255, b2=0, speedMs=1000))
    _assert(out == (2, 255, 0, 0, 1000, 0, 255, 0, 0),
            f"FADE → (2, 255, 0, 0, 1000, 0, 255, 0, 0)  got {out}")


def test_breathe():
    # ACT_BREATHE (3): p8a=minBri, p16a=periodMs.
    out = parent_server._act_params(_bake_keyed_action(
        3, r=128, g=64, b=32, minBri=20, periodMs=2000))
    _assert(out == (3, 128, 64, 32, 2000, 20, 0, 0, 0),
            f"BREATHE → (3, 128, 64, 32, 2000, 20, 0, 0, 0)  got {out}")


def test_chase():
    # ACT_CHASE (4): p8a=spacing, p8c=direction, p16a=speedMs.
    out = parent_server._act_params(_bake_keyed_action(
        4, r=255, g=255, b=255, spacing=3, direction=1, speedMs=80))
    _assert(out == (4, 255, 255, 255, 80, 3, 0, 1, 0),
            f"CHASE → (4, 255, 255, 255, 80, 3, 0, 1, 0)  got {out}")


def test_rainbow():
    # ACT_RAINBOW (5): p8a=paletteId, p8c=direction, p16a=speedMs.
    out = parent_server._act_params(_bake_keyed_action(
        5, paletteId=4, direction=1, speedMs=200))
    _assert(out == (5, 0, 0, 0, 200, 4, 0, 1, 0),
            f"RAINBOW → (5, 0, 0, 0, 200, 4, 0, 1, 0)  got {out}")


def test_fire_the_one_from_the_issue():
    """The exact reproduction from #871. Pre-fix this returned
    (6, 255, 0, 0, 15, 0, 0, 0, 0) — cooling and sparking lost
    because the chain short-circuited on r2=0 / g2=0."""
    out = parent_server._act_params(_bake_keyed_action(
        6, r=255, g=0, b=0, cooling=55, sparking=120, speedMs=15))
    _assert(out == (6, 255, 0, 0, 15, 55, 120, 0, 0),
            f"FIRE → (6, 255, 0, 0, 15, 55, 120, 0, 0)  got {out}")


def test_comet():
    # ACT_COMET (7): p8a=tailLen, p8c=direction, p8d=decay, p16a=speedMs.
    out = parent_server._act_params(_bake_keyed_action(
        7, r=255, g=128, b=0, tailLen=10, direction=1, decay=240, speedMs=50))
    _assert(out == (7, 255, 128, 0, 50, 10, 0, 1, 240),
            f"COMET → (7, 255, 128, 0, 50, 10, 0, 1, 240)  got {out}")


def test_twinkle():
    # ACT_TWINKLE (8): p8a=density, p8d=fadeSpeed, p16a=spawnMs.
    out = parent_server._act_params(_bake_keyed_action(
        8, r=255, g=255, b=255, density=20, fadeSpeed=8, spawnMs=200))
    _assert(out == (8, 255, 255, 255, 200, 20, 0, 0, 8),
            f"TWINKLE → (8, 255, 255, 255, 200, 20, 0, 0, 8)  got {out}")


def test_strobe():
    # ACT_STROBE (9): firmware reads p8a as duty% but action body
    # has no `duty` field today. Verify it sends 0 (graceful, not
    # garbage) and p16a=periodMs lands.
    out = parent_server._act_params(_bake_keyed_action(
        9, r=255, g=255, b=255, periodMs=200))
    _assert(out == (9, 255, 255, 255, 200, 0, 0, 0, 0),
            f"STROBE → (9, 255, 255, 255, 200, 0, 0, 0, 0)  got {out}")


def test_wipe_seq():
    # ACT_WIPE_SEQ (10): p8c=direction, p16a=speedMs.
    out = parent_server._act_params(_bake_keyed_action(
        10, r=0, g=128, b=255, direction=1, speedMs=30))
    _assert(out == (10, 0, 128, 255, 30, 0, 0, 1, 0),
            f"WIPE_SEQ → (10, 0, 128, 255, 30, 0, 0, 1, 0)  got {out}")


def test_scanner():
    # ACT_SCANNER (11): p8a=barWidth per Protocol.h but no field in
    # action body today. Verify safe default + speedMs lands.
    out = parent_server._act_params(_bake_keyed_action(
        11, r=255, g=0, b=0, speedMs=100))
    _assert(out == (11, 255, 0, 0, 100, 0, 0, 0, 0),
            f"SCANNER → (11, 255, 0, 0, 100, 0, 0, 0, 0)  got {out}")


def test_sparkle():
    # ACT_SPARKLE (12): p8a=density, p16a=spawnMs.
    out = parent_server._act_params(_bake_keyed_action(
        12, r=64, g=64, b=64, density=15, spawnMs=80))
    _assert(out == (12, 64, 64, 64, 80, 15, 0, 0, 0),
            f"SPARKLE → (12, 64, 64, 64, 80, 15, 0, 0, 0)  got {out}")


def test_gradient():
    # ACT_GRADIENT (13): p8a=r2, p8b=g2, p8c=b2 — same as FADE
    # excluding p16a/p8d.
    out = parent_server._act_params(_bake_keyed_action(
        13, r=255, g=0, b=0, r2=0, g2=0, b2=255))
    _assert(out == (13, 255, 0, 0, 0, 0, 0, 255, 0),
            f"GRADIENT → (13, 255, 0, 0, 0, 0, 0, 255, 0)  got {out}")


def test_blackout():
    out = parent_server._act_params(_bake_keyed_action(0))
    _assert(out == (0, 0, 0, 0, 0, 0, 0, 0, 0),
            f"BLACKOUT → all zeros  got {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Regression — explicit p8a/b/c/d in the action body must override the
# type-dispatched lookup (back-compat with paths that pre-compute
# wire slots directly).

def test_explicit_p8_overrides_type_dispatch():
    out = parent_server._act_params({
        "type": 6, "r": 255, "g": 0, "b": 0,
        "cooling": 55, "sparking": 120, "speedMs": 15,
        "p8a": 99,  # explicit override of the type-dispatched cooling
    })
    _assert(out == (6, 255, 0, 0, 15, 99, 120, 0, 0),
            f"explicit p8a=99 wins over cooling=55  got {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Regression — None values in the action body must coerce to 0
# (not raise on `int(None)`).

def test_none_values_coerce_safely():
    out = parent_server._act_params({
        "type": 6, "r": None, "g": 0, "b": 0,
        "cooling": None, "sparking": 120, "speedMs": None,
    })
    _assert(out == (6, 0, 0, 0, 0, 0, 120, 0, 0),
            f"None coerces to 0  got {out}")


# ─────────────────────────────────────────────────────────────────────────────
# The exact `_P8_FIELDS` map shape — guards against future careless
# edits that drop a row or rename an action body key.

def test_p8_fields_map_has_all_action_types():
    expected_types = set(range(14))  # 0..13
    actual = set(parent_server._ACT_P8_FIELDS.keys())
    missing = expected_types - actual
    _assert(not missing,
            f"_ACT_P8_FIELDS covers all 14 action types (missing {missing})")


ALL = [
    test_solid,
    test_fade,
    test_breathe,
    test_chase,
    test_rainbow,
    test_fire_the_one_from_the_issue,
    test_comet,
    test_twinkle,
    test_strobe,
    test_wipe_seq,
    test_scanner,
    test_sparkle,
    test_gradient,
    test_blackout,
    test_explicit_p8_overrides_type_dispatch,
    test_none_values_coerce_safely,
    test_p8_fields_map_has_all_action_types,
]


if __name__ == "__main__":
    print("=== #871 _act_params per-type packing contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
