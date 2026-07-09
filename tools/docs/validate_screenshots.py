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
# sources, the built in-SPA help, the marketing surface, and the README.
DOC_GLOBS = [
    "README.md",
    "docs/USER_MANUAL.md",
    "docs/USER_MANUAL_fr.md",
    "docs/src/en/*.md",
    "docs/src/fr/*.md",
    "docs/src/en/help-fragments/*.md",
    "docs/src/fr/help-fragments/*.md",
    "docs/src/marketing/**/*.md",
]

# SPA sources whose mtime defines "current UI". A screenshot older than
# the newest of these can't depict the current SPA.
SPA_DIRS = [ROOT / "desktop" / "shared" / "spa"]

# Capture-session leftovers that predate the recursive orphan check
# (#898 extended it to subdirectories, 2026-07-09). Nothing references
# these; they are intermediate frames from the #533 walkthrough capture
# runs, kept pending a triage pass (delete or reference — see #898
# follow-up). Do NOT add new entries: a new unreferenced capture is a
# bug this check exists to catch.
GRANDFATHERED_ORPHANS = {
    "walkthrough/02-file-menu.png",
    "walkthrough/03-dmx-section.png",
    "walkthrough/03-settings-tab.png",
    "walkthrough/03a-discover.png",
    "walkthrough/04-add-fixture-dialog.png",
    "walkthrough/04-edit-350w-spot-fixed.png",
    "walkthrough/04-edit-350w-spot.png",
    "walkthrough/04-edit-mh1-sly-fixed.png",
    "walkthrough/04-edit-mh1-sly.png",
    "walkthrough/04-edit-mh2-sly-fixed.png",
    "walkthrough/04-edit-mh2-sly.png",
    "walkthrough/04-fixture-type-select.png",
    "walkthrough/04-setup-before.png",
    "walkthrough/04c-layout-positions.png",
    "walkthrough/06-cameras.png",
    "walkthrough/06b-cam2-added.png",
    "walkthrough/07-calibrate.png",
    "walkthrough/09b-red-set.png",
    "walkthrough/11a-tracking-ui.png",
    "walkthrough/11a-tracking.png",
    "walkthrough/11b-track-action.png",
    "walkthrough/11c-floor-target.png",
    "walkthrough/4-mh1-sly.png",
    "walkthrough/4-mh2-sly.png",
    "walkthrough/5-350w-spot.png",
    "walkthrough/cam-add-camera-type.png",
    "walkthrough/cam-add-modal.png",
    "walkthrough/cam-after-add.png",
    "walkthrough/cam-ip-filled.png",
    "walkthrough/cam2-filled.png",
    "walkthrough/cam3-after-add.png",
    "walkthrough/positions.png",
    "walkthrough/profile-search-mh.png",
    "walkthrough/version-check.png",
    "walkthrough/version-check2.png",
}

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


def validate(skip_freshness=False):
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

    # 2. Orphans — recurses subdirectories (walkthrough/, android/, ...).
    # Names are SHOT_DIR-relative POSIX paths to match how docs reference
    # them (`screenshots/walkthrough/x.png`).
    on_disk = {p.relative_to(SHOT_DIR).as_posix()
               for p in SHOT_DIR.rglob("*.png")}
    for name in sorted(on_disk - set(referenced) - GRANDFATHERED_ORPHANS):
        fails.append(("orphan",
                       f"screenshots/{name} is not referenced by any doc"))

    # 3. Freshness — skippable (#904): any SPA source edit legitimately stales
    # every referenced capture until a live-rig recapture session, so the fast
    # per-PR CI job runs with --skip-freshness while the weekly strict run
    # keeps the recapture reminder red until it actually happens.
    for name, uses in sorted(referenced.items()) if not skip_freshness else []:
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
    ap.add_argument("--skip-freshness", action="store_true",
                    help="skip check 3 (stale-vs-SPA); used by the per-PR CI job")
    args = ap.parse_args()
    r = validate(skip_freshness=args.skip_freshness)
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
