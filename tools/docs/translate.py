"""tools/docs/translate.py — EN → FR (or any pair) via a pluggable VLM.

The #623 camera-auto-tune stays strictly local (Ollama). Translation runs
at build time on the maintainer's machine — using a cloud model here is
fine and preferred for quality. Backend is still pluggable so power users
who want a fully-offline workflow can switch to Ollama with one env var.

Backends
--------
- ``claude``  (default) — Claude via the Anthropic SDK. Requires
  ``ANTHROPIC_API_KEY`` and ``pip install anthropic``.
- ``ollama``  — local chat model, same daemon the runtime uses (#623).
  ``SLYLED_OLLAMA_MODEL`` picks the backend. Default ``qwen2.5:14b``.
- ``noop``    — passthrough. Writes the EN content into FR unchanged.
  Useful for wiring tests without burning tokens.

Select via ``SLYLED_TRANSLATE_BACKEND=claude|ollama|noop``.

Usage
-----
    # Translate one section, write to docs/src/fr/ and open the diff.
    python tools/docs/translate.py --section 04-fixture-setup

    # Batch-translate every EN file still stubbed in FR.
    python tools/docs/translate.py --all --sync

    # Mark a hand-reviewed FR file as approved (removes DRAFT banner).
    python tools/docs/translate.py --mark-reviewed 04-fixture-setup

Review gate
-----------
Each FR file carries a review sentinel at the top:

    <!-- review-status: pending | reviewed -->

Build-time parity checks in ``drift_check.py`` (#672) fail when a FR
file is ``pending`` and its EN counterpart changed after the last
review.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap
from pathlib import Path

log = logging.getLogger('slyled.docs.translate')

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / 'docs' / 'src'
EN = SRC / 'en'
FR = SRC / 'fr'

SYSTEM_PROMPT = textwrap.dedent("""\
    You translate technical documentation for SlyLED, a stage-lighting
    control system. Source is English markdown. Target is Canadian French
    (Quebec conventions — "courriel" not "mail", "clavardage" not "chat").

    Hard rules:

    1. Preserve markdown structure 1:1. Every ##/### heading, list marker,
       code fence, table pipe and image reference must appear in the
       output in the same place.
    2. Keep technical tokens untranslated: DMX, Art-Net, ArtDMX, ArtPoll,
       sACN, RGB, ESP32, YOLO, ArUco, GDTF, MVR, PID, FOV, ONNX, RANSAC,
       UDP, HTTP, JSON, YAML, API, SPA, UI, CLI, WiFi, USB, GPIO,
       hostname, routing headers.
    3. Preserve inline `code`, ```code blocks```, and URLs verbatim.
    4. When an English term appears in UI labels (e.g. "Dashboard",
       "Calibrate", "Bake"), translate the prose around it but KEEP the
       UI label in a parenthesised English form on first occurrence:
       "Tableau de bord (Dashboard)".
    5. Do not invent content. If the English says 4 iterations, the
       French says 4 iterations.

    Output the translated markdown and nothing else — no commentary, no
    wrapping fences, no preamble.
""").strip()


# ── Backends ───────────────────────────────────────────────────────────

def _backend_claude(text: str) -> str:
    """Claude via Anthropic SDK."""
    try:
        import anthropic  # type: ignore
    except ImportError:
        raise RuntimeError('anthropic SDK not installed — `pip install '
                           'anthropic` to use --backend claude')
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise RuntimeError('ANTHROPIC_API_KEY not set')
    model = os.environ.get('SLYLED_TRANSLATE_MODEL', 'claude-sonnet-4-6')
    client = anthropic.Anthropic(api_key=key)
    # Chunk long sections; Claude handles the whole manual in one call but
    # we cap at ~40 kB to stay below the single-message limit for the
    # smaller Claude tiers.
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': text}],
    )
    return ''.join(
        (b.text if hasattr(b, 'text') else '')
        for b in resp.content if getattr(b, 'type', '') == 'text'
    )


def _backend_ollama(text: str) -> str:
    """Local chat model via the #623 Ollama runtime."""
    import json
    import urllib.request

    url = os.environ.get('SLYLED_OLLAMA_URL', 'http://localhost:11434')
    model = os.environ.get('SLYLED_OLLAMA_MODEL', 'qwen2.5:14b')
    payload = {
        'model': model,
        'prompt': SYSTEM_PROMPT + '\n\n---\n\n' + text,
        'stream': False,
        'options': {'temperature': 0.15, 'num_predict': 8000},
    }
    req = urllib.request.Request(
        f'{url}/api/generate',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return (body.get('response') or '').strip()


def _backend_noop(text: str) -> str:
    """Passthrough — returns the EN source verbatim for test wiring."""
    return text


BACKENDS = {
    'claude': _backend_claude,
    'ollama': _backend_ollama,
    'noop':   _backend_noop,
}


def resolve_backend(name: str | None) -> tuple[str, callable]:
    chosen = (name
              or os.environ.get('SLYLED_TRANSLATE_BACKEND')
              or 'claude').lower()
    if chosen not in BACKENDS:
        raise ValueError(f'unknown translate backend {chosen!r} — one of '
                         f'{sorted(BACKENDS)}')
    return chosen, BACKENDS[chosen]


# ── Review sentinel ────────────────────────────────────────────────────

REVIEW_MARKER = '<!-- review-status:'


def read_review_status(path: Path) -> str:
    if not path.exists():
        return 'missing'
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.strip().startswith(REVIEW_MARKER):
            # e.g. "<!-- review-status: pending -->"
            body = line.split(':', 1)[1].strip().rstrip('-> ').strip()
            return body or 'unknown'
    return 'untracked'


def stamp_review_status(path: Path, status: str) -> None:
    content = path.read_text(encoding='utf-8', errors='replace')
    lines = content.splitlines(keepends=True)
    new_first = f'{REVIEW_MARKER} {status} -->\n'
    if lines and lines[0].strip().startswith(REVIEW_MARKER):
        lines[0] = new_first
    else:
        lines.insert(0, new_first + '\n')
    path.write_text(''.join(lines), encoding='utf-8')


# ── Command surface ────────────────────────────────────────────────────

def translate_one(slug: str, backend_name: str | None = None) -> Path:
    en = EN / f'{slug}.md'
    fr = FR / f'{slug}.md'
    if not en.exists():
        raise SystemExit(f'EN source missing: {en}')
    name, fn = resolve_backend(backend_name)
    log.info('translate [%s]: %s → %s', name, en.name, fr.name)
    src = en.read_text(encoding='utf-8')
    try:
        out = fn(src)
    except RuntimeError as e:
        raise SystemExit(f'translate failed ({name}): {e}')
    # Prepend review sentinel so the parity gate (#672) flags it.
    fr.write_text(f'{REVIEW_MARKER} pending -->\n\n{out.lstrip()}',
                  encoding='utf-8')
    log.info('translate: wrote %d bytes → %s',
             fr.stat().st_size, fr.relative_to(ROOT))
    return fr


def translate_all(backend_name: str | None = None,
                  only_stubbed: bool = True) -> list[Path]:
    out = []
    for en in sorted(EN.glob('*.md')):
        slug = en.stem
        fr = FR / f'{slug}.md'
        if only_stubbed and fr.exists():
            text = fr.read_text(encoding='utf-8', errors='replace')
            # Skip files that aren't just the auto-generated stub.
            if 'traduction en attente' not in text and len(text) > 300:
                continue
        out.append(translate_one(slug, backend_name))
    return out


def mark_reviewed(slug: str) -> Path:
    fr = FR / f'{slug}.md'
    if not fr.exists():
        raise SystemExit(f'FR file missing: {fr}')
    stamp_review_status(fr, 'reviewed')
    log.info('marked reviewed: %s', fr.relative_to(ROOT))
    return fr


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='SlyLED docs translator')
    p.add_argument('--section', help='translate one source file by slug')
    p.add_argument('--all', action='store_true',
                   help='translate every FR stub (ignores already-translated files)')
    p.add_argument('--sync', action='store_true',
                   help='with --all, re-translate even non-stub FR files (regressions)')
    p.add_argument('--backend', choices=sorted(BACKENDS),
                   help='override SLYLED_TRANSLATE_BACKEND')
    p.add_argument('--mark-reviewed', metavar='SLUG',
                   help='stamp a FR file as operator-reviewed')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')
    if args.mark_reviewed:
        mark_reviewed(args.mark_reviewed)
        return 0
    if args.section:
        translate_one(args.section, args.backend)
        return 0
    if args.all:
        paths = translate_all(args.backend, only_stubbed=not args.sync)
        log.info('translated %d files', len(paths))
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
