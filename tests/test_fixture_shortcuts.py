#!/usr/bin/env python3
"""Shared shortcut-renderer corpus test.

Runs the JS resolver (desktop/shared/spa/js/fixture_shortcuts.js) via
Node against every profile in tests/fixtures/shortcut_corpus/ and
verifies the output matches tests/fixtures/shortcut_corpus/expected.json.

Both the JS resolver and its Kotlin twin
(android/.../FixtureShortcuts.kt) must produce identical output for
the same profile. This test is the JS half of that gate; the Android
side adds a JUnit test against the same corpus once Compose unit
testing infra lands.

Run: `python3 tests/test_fixture_shortcuts.py`
Requires: node (any LTS) on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "shortcut_corpus"
EXPECTED_PATH = CORPUS_DIR / "expected.json"
JS_RESOLVER = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "fixture_shortcuts.js"


def require_node():
    if shutil.which("node") is None:
        print("[SKIP] node not on PATH — shortcut renderer corpus test skipped.")
        sys.exit(0)


def resolve_via_node(profile: dict) -> list:
    """Run the JS resolver on a single profile JSON, return shortcut list."""
    # Rebuild channel_map (server omits it from /api/dmx-profiles/<id>).
    cm = {}
    for c in profile.get("channels", []):
        t = c.get("type")
        if t and t not in cm:
            cm[t] = c.get("offset", 0)
    profile["channel_map"] = cm

    script = f"""
const {{ resolveShortcutsForProfile }} = require({json.dumps(str(JS_RESOLVER))});
const p = {json.dumps(profile)};
console.log(JSON.stringify(resolveShortcutsForProfile(p)));
"""
    out = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        raise RuntimeError(f"node failed: {out.stderr}")
    return json.loads(out.stdout)


def main():
    require_node()
    expected = json.loads(EXPECTED_PATH.read_text())
    expected.pop("_doc", None)

    failures = []
    checked = 0

    for slug, want in expected.items():
        profile_path = CORPUS_DIR / f"{slug}.json"
        if not profile_path.exists():
            failures.append(f"{slug}: profile JSON not found at {profile_path}")
            continue
        profile = json.loads(profile_path.read_text())
        got = resolve_via_node(profile)
        checked += 1

        # Check id order matches.
        want_ids = want.get("shortcutIds", [])
        got_ids = [s["id"] for s in got]
        if got_ids != want_ids:
            failures.append(
                f"{slug}: shortcut id order mismatch.\n"
                f"  want: {want_ids}\n"
                f"  got:  {got_ids}"
            )
            continue

        # Check per-shortcut offset / anchor.
        for ws, gs in zip(want.get("shortcuts", []), got):
            if ws["id"] != gs["id"]:
                failures.append(f"{slug}: id at index {got.index(gs)} differs: want={ws['id']} got={gs['id']}")
                continue
            if ws.get("ui") and ws["ui"] != gs.get("ui"):
                failures.append(f"{slug}/{ws['id']}: ui mismatch want={ws['ui']} got={gs.get('ui')}")
            if "channelOffset" in ws and ws["channelOffset"] != gs.get("channelOffset"):
                failures.append(
                    f"{slug}/{ws['id']}: channelOffset mismatch "
                    f"want={ws['channelOffset']} got={gs.get('channelOffset')}"
                )
            if "anchorOffset" in ws and ws["anchorOffset"] != gs.get("anchorOffset"):
                failures.append(
                    f"{slug}/{ws['id']}: anchorOffset mismatch "
                    f"want={ws['anchorOffset']} got={gs.get('anchorOffset')}"
                )

    print(f"Shortcut corpus: {checked} profile(s) checked.")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
