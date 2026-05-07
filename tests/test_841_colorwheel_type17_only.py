#!/usr/bin/env python3
"""test_841_colorwheel_type17_only.py - Regression for #841.

Asserts the Colour Wheel field is exposed and persisted for the Colour
Wheel action (type 17) only. Hybrid RGB+wheel fixtures rely on
rgb_to_wheel_slot (#842) deriving the wheel slot from RGB at render
time; a stale `colorWheel: 0` baked into a DMX Scene / PT-Move / Gobo
Select action defeats that fallback and forces the wheel home,
filtering the beam.

Covers:

  1. POST /api/actions strips colorWheel from non-type-17 bodies.
  2. PUT /api/actions/<id> strips colorWheel from non-type-17 bodies.
  3. Type-17 actions retain their colorWheel value end-to-end.
  4. The migration helper drops colorWheel from any persisted
     non-type-17 action on import.
  5. SPA editor (actions.js) shows the colorWheel input only when
     the type select is 17, and the save handler writes it only for
     type 17.

Run: python -X utf8 tests/test_841_colorwheel_type17_only.py
"""

import json
import os
import sys
import tempfile

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


def _client():
    parent_server.app.config["TESTING"] = True
    return parent_server.app.test_client()


# ---------------------------------------------------------------------------

def test_post_strips_colorwheel_from_dmx_scene():
    c = _client()
    r = c.post("/api/actions", json={
        "name": "test841-scene", "type": 14,
        "dimmer": 200, "pan": 0.5, "tilt": 0.5,
        "strobe": 0, "gobo": 0, "colorWheel": 7, "prism": 0,
    })
    _assert(r.status_code == 200, "POST returns 200")
    aid = r.get_json()["id"]
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert(a is not None, "action persisted")
    _assert("colorWheel" not in a,
            f"DMX-Scene (type 14) action stripped colorWheel "
            f"(got {a.get('colorWheel', '<absent>')})")
    # Cleanup
    c.delete(f"/api/actions/{aid}")


def test_post_strips_colorwheel_from_pt_move():
    c = _client()
    r = c.post("/api/actions", json={
        "name": "test841-pt", "type": 15,
        "dimmer": 200, "colorWheel": 5,
        "ptStartPos": [0, 0, 0], "ptEndPos": [1000, 0, 0],
    })
    aid = r.get_json()["id"]
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert("colorWheel" not in a,
            "PT-Move (type 15) action stripped colorWheel")
    c.delete(f"/api/actions/{aid}")


def test_post_strips_colorwheel_from_gobo_select():
    c = _client()
    r = c.post("/api/actions", json={
        "name": "test841-gobo", "type": 16,
        "gobo": 3, "colorWheel": 4,
    })
    aid = r.get_json()["id"]
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert("colorWheel" not in a,
            "Gobo Select (type 16) action stripped colorWheel")
    c.delete(f"/api/actions/{aid}")


def test_post_keeps_colorwheel_on_type17():
    c = _client()
    r = c.post("/api/actions", json={
        "name": "test841-cw", "type": 17, "colorWheel": 6,
    })
    aid = r.get_json()["id"]
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert(a.get("colorWheel") == 6,
            f"Colour Wheel (type 17) action retains colorWheel "
            f"(got {a.get('colorWheel')})")
    c.delete(f"/api/actions/{aid}")


def test_put_strips_colorwheel_on_non17():
    c = _client()
    # Create a type-14 action without colorWheel.
    r = c.post("/api/actions", json={
        "name": "test841-put", "type": 14, "dimmer": 100,
    })
    aid = r.get_json()["id"]
    # PUT with a stale colorWheel value — server must reject it.
    c.put(f"/api/actions/{aid}", json={"colorWheel": 9})
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert("colorWheel" not in a,
            "PUT on type-14 strips colorWheel from request body")
    c.delete(f"/api/actions/{aid}")


def test_put_keeps_colorwheel_on_type17():
    c = _client()
    r = c.post("/api/actions", json={
        "name": "test841-put17", "type": 17, "colorWheel": 1,
    })
    aid = r.get_json()["id"]
    c.put(f"/api/actions/{aid}", json={"colorWheel": 12})
    a = next((x for x in parent_server._actions if x["id"] == aid), None)
    _assert(a.get("colorWheel") == 12,
            f"PUT on type-17 keeps colorWheel (got {a.get('colorWheel')})")
    c.delete(f"/api/actions/{aid}")


def test_migration_strips_persisted_stale_colorwheel():
    """Directly invoke the migration helper on a synthetic _actions list
    with stale colorWheel fields and verify they're dropped."""
    saved = list(parent_server._actions)
    try:
        parent_server._actions[:] = [
            {"id": 1001, "name": "scene", "type": 14, "colorWheel": 0, "dimmer": 200},
            {"id": 1002, "name": "pt",    "type": 15, "colorWheel": 3},
            {"id": 1003, "name": "gobo",  "type": 16, "colorWheel": 7, "gobo": 4},
            {"id": 1004, "name": "cw",    "type": 17, "colorWheel": 5},
            {"id": 1005, "name": "solid", "type": 1},
        ]
        parent_server._migrate_strip_stale_color_wheel()
        for a in parent_server._actions:
            if a["type"] != 17:
                _assert("colorWheel" not in a,
                        f"action id={a['id']} type={a['type']} stripped of colorWheel")
            else:
                _assert(a.get("colorWheel") == 5,
                        f"action id={a['id']} type=17 retained colorWheel")
    finally:
        parent_server._actions[:] = saved


# ---------------------------------------------------------------------------
# SPA static-source assertions.

def test_spa_editor_dom_structure():
    actions_js = os.path.join(os.path.dirname(__file__), "..",
                                "desktop", "shared", "spa", "js", "actions.js")
    with open(actions_js, "r", encoding="utf-8") as f:
        src = f.read()
    # Colour Wheel input must NOT be in the shared ae-dmx block.
    # That block is bounded by `id="ae-dmx"` ... `</div>`.
    dmx_open = src.find('id="ae-dmx"')
    dmx_close = src.find("'</div>'", dmx_open)
    dmx_block = src[dmx_open:dmx_close]
    _assert("ae-colorwheel" not in dmx_block,
            "shared ae-dmx block does NOT contain ae-colorwheel input")
    # A dedicated type-17 block must exist.
    _assert('id="ae-colorwheel-block"' in src,
            "dedicated ae-colorwheel-block exists for type 17")
    _assert("(t===17?'':' style=\"display:none\"')" in src,
            "ae-colorwheel-block visibility gated on t===17")
    # Save handler: the shared `if(t>=14&&t<=17)` branch must not write colorWheel.
    save_branch_start = src.find("if(t>=14&&t<=17){")
    save_branch_end = src.find("}", save_branch_start)
    save_block = src[save_branch_start:save_branch_end]
    _assert("body.colorWheel" not in save_block,
            "shared DMX save branch does NOT write body.colorWheel")
    # Only the `if(t===17)` branch writes colorWheel.
    _assert("if(t===17){body.colorWheel=" in src,
            "type-17 save branch writes body.colorWheel")


ALL = [
    test_post_strips_colorwheel_from_dmx_scene,
    test_post_strips_colorwheel_from_pt_move,
    test_post_strips_colorwheel_from_gobo_select,
    test_post_keeps_colorwheel_on_type17,
    test_put_strips_colorwheel_on_non17,
    test_put_keeps_colorwheel_on_type17,
    test_migration_strips_persisted_stale_colorwheel,
    test_spa_editor_dom_structure,
]


if __name__ == "__main__":
    print("=== #841 Colour Wheel field is type-17-only ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
