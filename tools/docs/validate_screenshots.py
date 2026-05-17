#!/usr/bin/env python3
"""Validate that documentation screenshots reflect what the docs describe.

The manual and the in-SPA help embed PNG screenshots. Over time the SPA
changes and the captures drift — a screenshot can quietly stop matching
the UI it is captioned as. This tool is the validation process that
catches that drift. It runs four deterministic checks:

  1. Reference integrity — every `screenshots/X.png` a doc links exists
     on disk. A broken image link fails.
  2. Orphans — every PNG in the screenshot dir is referenced by at least
     one doc. An unreferenced capture is dead weight (or a rename was
     missed) and fails.
  3. Freshness — every referenced screenshot is newer than the SPA
     sources it depicts (index.html + js/ + css/). A screenshot older
     than the UI it shows cannot reflect the current SPA, so it fails
     and must be re-captured (tools: tests/screenshot_capture.py).
  4. Alt-text — every screenshot reference carries descriptive alt text
     (Markdown `![alt](...)`), not an empty string or a bare filename,
     so the caption actually says what the reader should see.

Exit code is non-zero if any check fails — wire it into CI and run it
before any docs release.

Usage:
    python tools/docs/validate_screenshots.py
    python tools/docs/validate_screenshots.py --json
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "docs" / "screenshots"

# Docs that embed screenshots — the user manual (en + fr), the chapter
# sources, and the built in-SPA help.
DOC_GLOBS = [
    "docs/USER_MANUAL.md",
    "docs/USER_MANUAL_fr.md",
    "docs/src/en/*.md",
    "docs/src/fr/*.md",
    "docs/src/en/help-fragments/*.md",
]

# SPA sources whose mtime defines "current UI". A screenshot older than
# the newest of these can't depict the current SPA.
SPA_DIRS = [ROOT / "desktop" / "shared" / "spa"]

# Markdown image: ![alt](path)  — capture alt + path.
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
# Bare screenshots/ path (HTML <img>, or a path mentioned in prose).
PATH_RE = re.compile(r"screenshots/([A-Za-z0-9_./-]+\.png)")


def _docs():
    out = []
    for g in DOC_GLOBS:
        if "*" in g:
            out += sorted(ROOT.glob(g))
        else:
            p = ROOT / g
            if p.exists():
                out.append(p)
    return out


def _spa_mtime():
    """Newest mtime across SPA sources — the 'current UI' timestamp."""
    newest = 0.0
    for d in SPA_DIRS:
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".js", ".css"):
                newest = max(newest, f.stat().st_mtime)
    return newest


def validate():
    fails = []          # (check, detail)
    referenced = {}     # png name -> list of (doc, alt)
    spa_mtime = _spa_mtime()

    for doc in _docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        rel = doc.relative_to(ROOT)
        alts = {}
        for alt, path in IMG_RE.findall(text):
            m = PATH_RE.search(path)
            if m:
                alts[m.group(1)] = alt
        for m in PATH_RE.finditer(text):
            name = m.group(1)
            alt = alts.get(name, None)
            referenced.setdefault(name, []).append((str(rel), alt))

    # 1. Reference integrity
    for name, uses in sorted(referenced.items()):
        if not (SHOT_DIR / name).exists():
            for doc, _ in uses:
                fails.append(("broken-link",
                              f"{doc} references missing screenshots/{name}"))

    # 2. Orphans
    on_disk = {p.name for p in SHOT_DIR.glob("*.png")}
    for name in sorted(on_disk - set(referenced)):
        fails.append(("orphan",
                       f"screenshots/{name} is not referenced by any doc"))

    # 3. Freshness
    for name, uses in sorted(referenced.items()):
        f = SHOT_DIR / name
        if f.exists() and f.stat().st_mtime < spa_mtime:
            fails.append(("stale",
                           f"screenshots/{name} predates the current SPA "
                           f"— re-capture (referenced by "
                           f"{', '.join(sorted({d for d, _ in uses}))})"))

    # 4. Alt-text
    for name, uses in sorted(referenced.items()):
        for doc, alt in uses:
            if alt is not None and (not alt.strip()
                                    or alt.strip().lower() == name.lower()):
                fails.append(("weak-alt",
                              f"{doc}: screenshots/{name} has no descriptive "
                              f"alt text"))

    return {
        "screenshotsOnDisk": len(on_disk),
        "screenshotsReferenced": len(referenced),
        "failures": fails,
        "ok": not fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    r = validate()
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"Screenshots: {r['screenshotsReferenced']} referenced, "
              f"{r['screenshotsOnDisk']} on disk")
        if r["ok"]:
            print("PASS — all documentation screenshots validate.")
        else:
            by = {}
            for check, detail in r["failures"]:
                by.setdefault(check, []).append(detail)
            for check in ("broken-link", "stale", "orphan", "weak-alt"):
                items = by.get(check, [])
                if items:
                    print(f"\n{check.upper()} ({len(items)}):")
                    for d in items:
                        print(f"  - {d}")
            print(f"\nFAIL — {len(r['failures'])} issue(s).")
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
