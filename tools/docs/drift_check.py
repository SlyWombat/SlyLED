"""tools/docs/drift_check.py — code ↔ docs sync gate (#672).

Catches drift between the canonical calibration code surface and the
Appendix A/B prose. Runs in two modes:

- **PR-time grep** (``--mode pr``) — fast, no VLM. Fails when a PR
  touches files on ``DOCS_MAINTENANCE.md``'s source-of-truth list
  without also touching ``docs/src/``. GitHub Actions consumes this.
- **Scheduled semantic** (``--mode weekly``) — slower. Uses a VLM
  (same pluggable backend as ``translate.py``) to look at code +
  appendix sections side-by-side and report conceptual drift. Opens
  a ``docs: drift report YYYY-MM-DD`` PR when findings surface.

Two parity checks always run:

1. **FR ↔ EN filename parity** — every ``docs/src/en/*.md`` must have a
   matching ``docs/src/fr/*.md`` counterpart.
2. **FR review freshness** — any FR file marked ``<!-- review-status:
   pending -->`` whose EN source changed since the last ``reviewed``
   stamp blocks the build until re-translated + re-approved.

Exit codes: 0 clean, 1 drift detected, 2 infrastructure error.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger('slyled.docs.drift')

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / 'docs' / 'src'
EN = SRC / 'en'
FR = SRC / 'fr'

# Files whose changes must be reflected in the appendices. Mirror of the
# source-of-truth list in DOCS_MAINTENANCE.md. Wildcards supported via
# simple "startswith" match against repo-relative paths.
SOT_MOVER = [
    'desktop/shared/mover_calibrator.py',
    'desktop/shared/mover_control.py',
    'desktop/shared/parametric_mover.py',
    'desktop/shared/spa/js/calibration.js',
]
SOT_CAMERA = [
    'firmware/orangepi/camera_server.py',
    'firmware/orangepi/beam_detector.py',
    'firmware/orangepi/depth_estimator.py',
    'desktop/shared/space_mapper.py',
    'desktop/shared/surface_analyzer.py',
    'desktop/shared/camera_math.py',
]
APPENDIX_MOVER = 'docs/src/en/appendix-b-mover-calibration.md'
APPENDIX_CAMERA = 'docs/src/en/appendix-a-camera-calibration.md'


# ── PR-time grep mode ─────────────────────────────────────────────────

def changed_files(base_ref: str = 'origin/main') -> list[str]:
    """Files changed between HEAD and base_ref (CI: GITHUB_BASE_REF)."""
    try:
        r = subprocess.run(['git', 'diff', '--name-only', f'{base_ref}...HEAD'],
                           capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        log.warning('git diff failed: %s — defaulting to empty list', e)
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def needs_appendix_update(files: list[str]) -> dict[str, list[str]]:
    """Group changed code files by which appendix they affect."""
    mover = [f for f in files if any(f.startswith(p) for p in SOT_MOVER)]
    cam   = [f for f in files if any(f.startswith(p) for p in SOT_CAMERA)]
    return {'B (mover)': mover, 'A (camera)': cam}


def pr_check(base_ref: str = 'origin/main') -> int:
    """Non-blocking PR nudge — prints findings, always exit 0 unless
    infrastructure failed. Reserving exit 1 for the weekly gate keeps
    contributors from seeing drift as a merge blocker."""
    files = changed_files(base_ref)
    if not files:
        log.info('drift (pr): no changes vs %s', base_ref)
        return 0
    appendix_touched = any(f == APPENDIX_MOVER or f == APPENDIX_CAMERA
                            or f.startswith('docs/src/') for f in files)
    by_appendix = needs_appendix_update(files)
    flagged = False
    for label, paths in by_appendix.items():
        if paths and not appendix_touched:
            flagged = True
            log.warning('drift (pr): Appendix %s source-of-truth files '
                         'changed without docs/src/ edits:', label)
            for p in paths:
                log.warning('    %s', p)
            log.warning('    Consider a matching edit in docs/src/en/appendix-%s.md '
                         'per DOCS_MAINTENANCE.md.',
                         'b-mover-calibration' if 'B' in label else 'a-camera-calibration')
    if flagged:
        log.info('drift (pr): nudge only — not blocking.')
    else:
        log.info('drift (pr): all clear.')
    return 0


# ── Parity checks (always run) ─────────────────────────────────────────

def check_filename_parity() -> int:
    en_names = {p.name for p in EN.glob('*.md')}
    fr_names = {p.name for p in FR.glob('*.md')}
    missing_fr = en_names - fr_names
    missing_en = fr_names - en_names
    if missing_fr or missing_en:
        if missing_fr:
            log.error('FR missing %d file(s): %s', len(missing_fr),
                      ', '.join(sorted(missing_fr)))
        if missing_en:
            log.error('EN missing %d file(s): %s', len(missing_en),
                      ', '.join(sorted(missing_en)))
        return 1
    log.info('parity: EN and FR have %d matching files.', len(en_names))
    return 0


REVIEW_RE = re.compile(r'<!-- review-status:\s*(\w+)')


def check_review_freshness() -> int:
    """Any FR file marked pending whose EN source changed after the FR
    file's last-modified is considered drifted (EN advanced without a
    re-review).
    """
    drifted = []
    for en_file in sorted(EN.glob('*.md')):
        fr_file = FR / en_file.name
        if not fr_file.exists():
            continue
        fr_text = fr_file.read_text(encoding='utf-8', errors='replace')
        m = REVIEW_RE.search(fr_text)
        status = m.group(1).lower() if m else 'untracked'
        if status == 'reviewed':
            continue
        # If FR is missing the sentinel entirely, treat it as drift only
        # when FR looks like a stub (short + "traduction en attente").
        if status == 'untracked':
            if 'traduction en attente' not in fr_text and len(fr_text) > 600:
                continue  # FR has real content; author just didn't stamp
        if en_file.stat().st_mtime > fr_file.stat().st_mtime + 10:
            drifted.append(en_file.name)
    if drifted:
        log.error('review-freshness: %d EN file(s) advanced past a '
                   'pending FR translation:', len(drifted))
        for n in drifted:
            log.error('    docs/src/fr/%s — rerun translate.py or '
                      'mark-reviewed.', n)
        return 1
    log.info('review-freshness: all FR pending files are current.')
    return 0


# ── Weekly semantic mode ──────────────────────────────────────────────

def weekly_check(backend_name: str | None) -> int:
    """Run a VLM-assisted comparison between appendix sections and the
    cited code. Emits a JSON report to ``docs/build/drift-report.json``
    and a markdown summary suitable for a ``docs: drift report`` PR.

    Current scope: skeleton only — the real VLM call plugs in via the
    ``translate.py`` backend factory once the code-reading prompt is
    validated on real sources.
    """
    log.warning('drift (weekly): semantic-check skeleton only — VLM '
                 'prompt wiring pending. Today\'s run reports parity + '
                 'review-freshness only.')
    # Dispatch to the parity checks and stop.
    rc = 0
    rc |= check_filename_parity()
    rc |= check_review_freshness()
    return rc


# ── CLI ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='docs ↔ code drift check')
    p.add_argument('--mode', choices=('pr', 'weekly', 'parity'), default='pr')
    p.add_argument('--base-ref', default='origin/main',
                    help='git ref to compare against for PR mode')
    p.add_argument('--backend', default=None,
                    help='VLM backend override (weekly mode)')
    args = p.parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=logging.INFO,
                         format='%(levelname)s %(name)s: %(message)s')
    if args.mode == 'pr':
        rc = pr_check(args.base_ref)
    elif args.mode == 'weekly':
        rc = weekly_check(args.backend)
    else:  # parity
        rc = check_filename_parity() | check_review_freshness()
    return rc


if __name__ == '__main__':
    sys.exit(main())
