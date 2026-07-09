#!/usr/bin/env python3
"""#906 — executable parity gate: the action-type name table exists in
four hand-synced copies. This test parses all four sources and asserts
they can never drift again.

Canonical spelling: **"Color ..."** (American) — decided under #906 by
the user manual: docs/USER_MANUAL.md and docs/src/en/ use "Color Wheel"
in every feature description ("Colour" appears only in a troubleshooting
note quoting a pre-#841 UI label).

Sources (index = wire `type` byte):
  1. desktop/shared/parent_server.py   _ACTION_NAMES         (19 entries, canon)
  2. desktop/shared/spa/js/actions.js  _typeNames            (19 entries)
  3. android/.../data/model/Models.kt  ActionTypes.names     (19 entries)
  4. android/.../pages/FixturesPage.kt LED_ACTION_TYPE_NAMES (14 entries —
     deliberately truncated to the LED types 0-13; must be an exact
     prefix of the canon list)

Pure static parse — no node / gradle needed. Run:
    python3 tests/test_parity_action_names.py
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SERVER_PY = REPO_ROOT / "desktop" / "shared" / "parent_server.py"
ACTIONS_JS = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "actions.js"
MODELS_KT = (REPO_ROOT / "android" / "app" / "src" / "main" / "java" / "com"
             / "slywombat" / "slyled" / "data" / "model" / "Models.kt")
FIXTURES_KT = (REPO_ROOT / "android" / "app" / "src" / "main" / "java" / "com"
               / "slywombat" / "slyled" / "ui" / "screens" / "control"
               / "pages" / "FixturesPage.kt")


def parse_server() -> list:
    src = SERVER_PY.read_text(encoding="utf-8")
    m = re.search(r"_ACTION_NAMES\s*=\s*(\[.*?\])", src, re.DOTALL)
    if not m:
        raise AssertionError("_ACTION_NAMES not found in parent_server.py")
    return ast.literal_eval(m.group(1))


def parse_spa() -> list:
    src = ACTIONS_JS.read_text(encoding="utf-8")
    m = re.search(r"var _typeNames\s*=\s*\[(.*?)\];", src, re.DOTALL)
    if not m:
        raise AssertionError("_typeNames not found in actions.js")
    return re.findall(r"'([^']*)'", m.group(1))


def parse_models_kt() -> list:
    src = MODELS_KT.read_text(encoding="utf-8")
    m = re.search(r"val names\s*=\s*listOf\((.*?)\)", src, re.DOTALL)
    if not m:
        raise AssertionError("ActionTypes.names not found in Models.kt")
    return re.findall(r'"([^"]*)"', m.group(1))


def parse_fixtures_kt() -> list:
    src = FIXTURES_KT.read_text(encoding="utf-8")
    m = re.search(r"LED_ACTION_TYPE_NAMES\s*=\s*arrayOf\((.*?)\)", src, re.DOTALL)
    if not m:
        raise AssertionError("LED_ACTION_TYPE_NAMES not found in FixturesPage.kt")
    return re.findall(r'"([^"]*)"', m.group(1))


def main():
    canon = parse_server()
    spa = parse_spa()
    models = parse_models_kt()
    led = parse_fixtures_kt()

    failures = []

    if len(canon) != 19:
        failures.append(f"canon (_ACTION_NAMES) has {len(canon)} entries, expected 19")

    # Canon spelling gate — the manual's spelling, forever.
    for i, name in enumerate(canon):
        if "Colour" in name:
            failures.append(
                f"canon[{i}] uses 'Colour' — manual spelling is 'Color' (#906)")

    def diff(label, got, want):
        if got == want:
            return
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                failures.append(f"{label}[{i}]: {g!r} != canon {w!r}")
        if len(got) != len(want):
            failures.append(f"{label}: length {len(got)} != canon {len(want)}")

    diff("actions.js _typeNames", spa, canon)
    diff("Models.kt ActionTypes.names", models, canon)

    # FixturesPage.kt is documented as LED-only (types 0-13): exact prefix.
    if len(led) != 14:
        failures.append(
            f"FixturesPage.kt LED_ACTION_TYPE_NAMES has {len(led)} entries, "
            "expected 14 (LED types 0-13)")
    diff("FixturesPage.kt LED_ACTION_TYPE_NAMES", led, canon[:len(led)])

    print(f"Action-name parity: canon={len(canon)} spa={len(spa)} "
          f"models.kt={len(models)} fixturespage.kt={len(led)}")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All four copies agree. Canon spelling: 'Color ...' per the manual.")


if __name__ == "__main__":
    main()
