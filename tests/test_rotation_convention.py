"""#600 — rotation convention smoke test.

After the swap, the layout array is ``[rx pitch, ry roll, rz yaw]``
(axis-letter-matched). The test asserts:

1. `rotation_from_layout([30, 0, 0])` → tilt=30°, pan=0°, roll=0°
2. `rotation_from_layout([0, 45, 0])` → roll=45° (formerly yaw in v1 convention)
3. `rotation_from_layout([0, 0, 90])` → pan=90° (formerly roll in v1 convention)
4. Round-trip via `rotation_to_layout` returns the same array.
5. `_migrate_rotation_schema()` on a fixtures list with old-convention
   rotations swaps them and marks the layout as schema v2.
6. The migration is idempotent (second call is a no-op).

Run:
    python -X utf8 tests/test_rotation_convention.py
"""

import os
import sys

_SHARED = os.path.join(os.path.dirname(__file__), "..", "desktop", "shared")
sys.path.insert(0, os.path.abspath(_SHARED))


def test_rotation_from_layout_index_convention():
    from camera_math import rotation_from_layout, rotation_to_layout
    fails = 0; total = 0
    print("=== rotation_from_layout index convention ===")
    t, p, r = rotation_from_layout([30, 0, 0])
    total += 1
    if abs(t - 30.0) > 1e-6 or p or r:
        print(f"  FAIL: [30,0,0] → ({t},{p},{r}); expected (30,0,0)"); fails += 1
    else:
        print("  PASS: [30,0,0] → tilt=30, pan=0, roll=0")
    t, p, r = rotation_from_layout([0, 45, 0])
    total += 1
    if r != 45.0 or p or t:
        print(f"  FAIL: [0,45,0] → ({t},{p},{r}); expected roll=45"); fails += 1
    else:
        print("  PASS: [0,45,0] → roll=45 (ry index)")
    t, p, r = rotation_from_layout([0, 0, 90])
    total += 1
    if p != 90.0 or r or t:
        print(f"  FAIL: [0,0,90] → ({t},{p},{r}); expected pan=90"); fails += 1
    else:
        print("  PASS: [0,0,90] → pan=90 (rz index)")
    roundtrip = rotation_to_layout(30, 90, 45)
    total += 1
    if roundtrip != [30.0, 45.0, 90.0]:
        print(f"  FAIL: rotation_to_layout(30, 90, 45) → {roundtrip}; expected [30, 45, 90]"); fails += 1
    else:
        print("  PASS: rotation_to_layout round-trip")
    return total, fails


def test_migration_swap():
    import parent_server as ps
    fails = 0; total = 0
    print("\n=== _migrate_rotation_schema swap ===")
    # Stash + seed state. Note the migration writes via _save so we need
    # to avoid clobbering the on-disk data — use a fresh temporary state
    # and swap layout.rotationSchemaVersion manually to force migration.
    saved_fixtures = list(ps._fixtures)
    saved_layout_children = list(ps._layout.get("children") or [])
    saved_schema = ps._layout.pop("rotationSchemaVersion", None)
    saved_save = ps._save
    ps._save = lambda *a, **k: None  # silence disk writes during test
    try:
        ps._fixtures = [
            {"id": 101, "fixtureType": "camera", "rotation": [30, 45, 90]},
            {"id": 102, "fixtureType": "dmx", "rotation": [10, 20, 30]},
            {"id": 103, "fixtureType": "camera"},  # no rotation — ignore
        ]
        ps._layout["children"] = [
            {"id": 101, "rotation": [30, 45, 90]},
        ]
        swapped = ps._migrate_rotation_schema()
        total += 1
        if swapped != 3:
            print(f"  FAIL: expected 3 swaps, got {swapped}"); fails += 1
        else:
            print("  PASS: 3 rotations swapped")
        total += 1
        if ps._fixtures[0]["rotation"] != [30, 90, 45]:
            print(f"  FAIL: fid 101 rotation {ps._fixtures[0]['rotation']}; expected [30, 90, 45]"); fails += 1
        else:
            print("  PASS: fid 101 swapped [30,45,90] → [30,90,45]")
        total += 1
        if ps._layout.get("rotationSchemaVersion") != ps._ROTATION_SCHEMA_VERSION:
            print(f"  FAIL: schema not marked ({ps._layout.get('rotationSchemaVersion')})"); fails += 1
        else:
            print("  PASS: layout marked rotationSchemaVersion=2")
        # Idempotent second call
        total += 1
        if ps._migrate_rotation_schema() != 0:
            print("  FAIL: second call swapped again (not idempotent)"); fails += 1
        else:
            print("  PASS: migration idempotent on second call")
    finally:
        ps._save = saved_save
        ps._fixtures = saved_fixtures
        ps._layout["children"] = saved_layout_children
        if saved_schema is not None:
            ps._layout["rotationSchemaVersion"] = saved_schema
        elif "rotationSchemaVersion" in ps._layout:
            ps._layout.pop("rotationSchemaVersion")
    return total, fails


if __name__ == "__main__":
    grand_total = 0
    grand_fail = 0
    for fn in (test_rotation_from_layout_index_convention, test_migration_swap):
        try:
            t, f = fn()
        except Exception as e:
            print(f"  FAIL: {fn.__name__} raised {e}")
            t, f = 1, 1
        grand_total += t
        grand_fail += f
    print(f"\n=== #600 rotation convention: {grand_total - grand_fail}/{grand_total} pass ===")
    sys.exit(0 if grand_fail == 0 else 1)
