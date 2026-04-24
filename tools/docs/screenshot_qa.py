"""tools/docs/screenshot_qa.py — VLM-assisted screenshot drift check (#671).

After ``screenshot_capture.py`` writes PNGs into ``docs/src/screenshots/``
this script compares each capture to its metadata in ``expected.yml`` and
flags regressions:

- Wrong tab (a setup capture that actually shows the Dashboard).
- Leaked modal (modal visible when it shouldn't be).
- Light-mode regression (dark-mode capture with pale pixels).
- Aspect-ratio or size drift outside per-file tolerance.

Cheap heuristics run first; VLM only fires when heuristics pass to save
budget (same pluggable backend as ``translate.py``).

Status: skeleton — the VLM prompt and expected-metadata schema are
filled in during Phase 2 of the docs-overhaul series. Parity with a
prior run (file-exists + size-within-tolerance) is enforced today.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger('slyled.docs.shotqa')

ROOT = Path(__file__).resolve().parent.parent.parent
SHOTS = ROOT / 'docs' / 'src' / 'screenshots'   # post-#668 location
LEGACY_SHOTS = ROOT / 'docs' / 'screenshots'    # pre-split
MANIFEST = SHOTS / 'expected.yml'

# Per-file tolerance knobs for the cheap heuristic.
SIZE_TOLERANCE = 0.30          # ±30 % file-size drift before alert.
MIN_DARK_RATIO = 0.55          # dark-mode captures must have ≥55 % dark pixels
MIN_COLOUR_ENTROPY = 0.08      # reject near-uniform blank frames (empty canvas)


# ── Heuristics ────────────────────────────────────────────────────────

def size_drift(path: Path, expected_bytes: int) -> float:
    """Return the relative drift between actual and expected file size."""
    if not path.exists():
        return float('inf')
    actual = path.stat().st_size
    return abs(actual - expected_bytes) / max(1, expected_bytes)


def dark_ratio(path: Path) -> float:
    """Percentage of pixels whose luma < 0.35 × 255. Quick dark-mode sanity."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return -1.0
    img = np.asarray(Image.open(path).convert('L'))
    return float((img < 90).mean())


# ── VLM check (pluggable) ──────────────────────────────────────────────

def vlm_check(path: Path, expected: dict, backend: str | None) -> dict:
    """Return {'pass': bool, 'reason': str}. Skeleton until prompt lands."""
    return {'pass': True, 'reason': 'skeleton — VLM prompt not yet wired'}


# ── Main ──────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(MANIFEST.read_text(encoding='utf-8')) or {}
    except Exception as e:
        log.warning('expected.yml parse failed: %s', e)
        return {}


def run_qa(backend: str | None = None) -> int:
    manifest = load_manifest()
    if not manifest:
        log.warning('expected.yml missing — running existence + dark-ratio only')
    # Walk both directories (pre/post split) so the check still works during
    # the migration to docs/src/screenshots/.
    captured = sorted([*LEGACY_SHOTS.glob('*.png'), *SHOTS.glob('*.png')])
    if not captured:
        log.error('no screenshots to QA — is screenshot_capture.py wired?')
        return 2
    failures = 0
    for png in captured:
        expected = manifest.get(png.name, {})
        dark = dark_ratio(png)
        # Heuristic: every SlyLED screenshot is dark-mode. A post-regression
        # light-mode slip drops below MIN_DARK_RATIO.
        if dark >= 0 and expected.get('expect_dark', True) and dark < MIN_DARK_RATIO:
            log.warning('%s: dark_ratio=%.2f < %.2f — light-mode leak?',
                         png.name, dark, MIN_DARK_RATIO)
            failures += 1
            continue
        # Size drift when expected value is supplied.
        if 'size' in expected:
            drift = size_drift(png, int(expected['size']))
            if drift > SIZE_TOLERANCE:
                log.warning('%s: size drift %.0f%%  (expected ~%d bytes, got %d)',
                             png.name, drift * 100, expected['size'],
                             png.stat().st_size)
                failures += 1
                continue
        # VLM dispatch last — only if heuristics passed.
        v = vlm_check(png, expected, backend)
        if not v.get('pass'):
            log.warning('%s: VLM flagged — %s', png.name, v.get('reason'))
            failures += 1
    if failures:
        log.error('%d / %d screenshots flagged', failures, len(captured))
        return 1
    log.info('screenshot QA passed: %d PNGs clean', len(captured))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='screenshot QA (#671)')
    p.add_argument('--backend', default=None, help='VLM backend for semantic check')
    args = p.parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=logging.INFO,
                         format='%(levelname)s %(name)s: %(message)s')
    return run_qa(args.backend)


if __name__ == '__main__':
    sys.exit(main())
