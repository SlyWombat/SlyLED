"""tools/docs/build.py — single entry point for every documentation build.

Driven by per-issue scope: #665 (consolidate), #666 (HTML + Kinetic-Prism),
#667 (mermaid), #668 (source split — landed), #670 (FR translation), etc.

Formats
-------
- assembled-md : `docs/USER_MANUAL.md` (+ _fr) reassembled from `docs/src/`
- html         : Kinetic-Prism themed single-file + per-chapter HTML
- help         : per-section HTML fragments for `/api/help/<section>`
- pdf          : print-ready via pandoc + playwright
- docx         : python-docx (styled) — preserves screenshots + inline images
- website      : Astro Starlight output (shell into `website/`)

Prerequisites
-------------
- python-docx, playwright, numpy, pyyaml (already in the repo)
- pandoc >= 2.17  (the pipeline falls back to the python-docx renderer
  when pandoc is absent, but HTML output is pandoc-only)
- mermaid-cli (optional) OR kroki.io reachable for diagram rendering

Usage
-----
    python tools/docs/build.py --lang all --format all
    python tools/docs/build.py --lang en  --format html
    python tools/docs/build.py --lang fr  --format pdf --skip-screenshots
    python tools/docs/build.py --lang en  --format website --deploy
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / 'docs'
SRC = DOCS / 'src'
BUILD = DOCS / 'build'          # gitignored output root
TOOLS = ROOT / 'tools' / 'docs'
THEME = TOOLS / 'theme'

LANGS = ('en', 'fr')
FORMATS = ('assembled-md', 'html', 'help', 'pdf', 'docx', 'website')

logging.basicConfig(level=logging.INFO,
                    format='%(levelname)s %(name)s: %(message)s')
log = logging.getLogger('slyled.docs')


# ── Source assembly ──────────────────────────────────────────────────────

def section_files(lang: str) -> list[Path]:
    """All split sources for a language, in the order they'll assemble."""
    src_dir = SRC / lang
    if not src_dir.is_dir():
        raise SystemExit(f'Missing source directory: {src_dir}')
    return sorted(src_dir.glob('*.md'))


def assemble_markdown(lang: str) -> Path:
    """Concatenate split sources into a single monolithic file.

    Writes `docs/USER_MANUAL.md` (EN) or `docs/USER_MANUAL_fr.md` (FR) as a
    byte-exact concatenation so existing consumers (GitHub link previews,
    legacy tooling) keep working.
    """
    files = section_files(lang)
    out = DOCS / ('USER_MANUAL.md' if lang == 'en' else 'USER_MANUAL_fr.md')
    with open(out, 'wb') as fh:
        for f in files:
            fh.write(f.read_bytes())
    log.info('assembled-md [%s]: %d sections → %s (%d bytes)',
             lang, len(files), out.relative_to(ROOT), out.stat().st_size)
    return out


# ── Format backends ──────────────────────────────────────────────────────

def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def build_html(lang: str) -> Path:
    """Render the assembled markdown to themed HTML.

    Pandoc-based when available. Falls back to the existing
    ``build_manual_from_md.py`` HTML step when pandoc is missing.
    """
    out_dir = BUILD / lang / 'html'
    out_dir.mkdir(parents=True, exist_ok=True)
    assembled = assemble_markdown(lang)
    out = out_dir / 'index.html'

    if _have('pandoc'):
        css = THEME / 'kinetic-prism.css'
        cmd = ['pandoc', str(assembled),
               '-o', str(out),
               '--standalone', '--toc', '--toc-depth=3',
               '--metadata', f'lang={lang}',
               '--metadata', 'title=SlyLED User Manual']
        if css.exists():
            cmd += ['--css', str(css)]
        log.info('html [%s]: pandoc → %s', lang, out.relative_to(ROOT))
        subprocess.run(cmd, check=True)
    else:
        # Legacy fallback — use tests/build_manual_from_md.py which has its
        # own python-markdown → HTML path. The code moves under tools/docs/
        # in #665; for now import by relative path.
        legacy = ROOT / 'tests' / 'build_manual_from_md.py'
        if not legacy.exists():
            raise SystemExit('pandoc not found and legacy renderer missing')
        log.warning('html [%s]: pandoc unavailable — using legacy fallback', lang)
        subprocess.run([sys.executable, str(legacy), '--lang', lang,
                         '--no-pdf', '--no-docx'], check=True)
    return out


def build_help_fragments(lang: str) -> Path:
    """Split the assembled markdown into per-section HTML fragments.

    Output: ``docs/build/{lang}/help/<section-slug>.html``. These feed the
    SPA's ``/api/help/<section>`` endpoint (#670 wires the runtime).
    """
    out_dir = BUILD / lang / 'help'
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in section_files(lang):
        slug = src.stem
        out = out_dir / f'{slug}.html'
        if _have('pandoc'):
            subprocess.run(['pandoc', str(src), '-o', str(out),
                             '--from', 'gfm', '--to', 'html5',
                             '--metadata', f'lang={lang}'],
                            check=True)
        else:
            # Minimal fallback — wrap markdown in <pre> so the fragment is
            # at least navigable. Real conversion requires pandoc.
            out.write_text('<pre>' + src.read_text(encoding='utf-8',
                                                    errors='replace') + '</pre>',
                            encoding='utf-8')
    log.info('help [%s]: %d fragments → %s',
             lang, len(section_files(lang)), out_dir.relative_to(ROOT))
    return out_dir


def build_pdf(lang: str) -> Path:
    """Print-ready PDF via pandoc → HTML → playwright chromium print.

    The two-step route gives us Kinetic-Prism theming (pandoc → themed HTML)
    while using playwright's Chromium for the actual PDF rasterisation —
    same engine the screenshot pipeline already depends on.
    """
    out_dir = BUILD / lang / 'pdf'
    out_dir.mkdir(parents=True, exist_ok=True)
    html = build_html(lang)
    out = out_dir / ('SlyLED-Manual.pdf' if lang == 'en' else
                     'SlyLED-Manuel.pdf')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('playwright not installed; cannot produce PDF')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(html.as_uri())
        page.pdf(path=str(out), format='Letter',
                 margin={'top': '0.75in', 'bottom': '0.75in',
                         'left': '0.75in', 'right': '0.75in'})
        browser.close()
    log.info('pdf [%s]: %s (%.1f MB)', lang, out.relative_to(ROOT),
             out.stat().st_size / 1e6)
    return out


def build_docx(lang: str) -> Path:
    """Delegate to the existing markdown→docx renderer (tests/
    build_manual_from_md.py) until #665 folds it into tools/docs/."""
    legacy = ROOT / 'tests' / 'build_manual_from_md.py'
    if not legacy.exists():
        raise SystemExit('Legacy docx renderer not found')
    log.info('docx [%s]: delegating to %s', lang, legacy.relative_to(ROOT))
    subprocess.run([sys.executable, str(legacy), '--lang', lang,
                     '--no-pdf'], check=True)
    out = DOCS / ('USER_MANUAL.docx' if lang == 'en' else
                  'USER_MANUAL_fr.docx')
    return out


def build_website(lang: str) -> Path:
    """Astro Starlight build — placeholder until #669 scaffolds the site."""
    site_dir = ROOT / 'website'
    if not site_dir.is_dir():
        log.warning('website [%s]: skipped — `%s` not yet scaffolded (#669)',
                    lang, site_dir.relative_to(ROOT))
        return site_dir
    log.info('website [%s]: npm run build in %s', lang, site_dir)
    subprocess.run(['npm', 'run', 'build'], cwd=site_dir, check=True)
    return site_dir / 'dist'


# ── Orchestration ────────────────────────────────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='SlyLED documentation builder')
    p.add_argument('--lang', choices=('en', 'fr', 'all'), default='all')
    p.add_argument('--format', choices=FORMATS + ('all',), default='all')
    p.add_argument('--skip-screenshots', action='store_true',
                   help='Use existing docs/src/screenshots/ without recapture')
    p.add_argument('--skip-diagrams', action='store_true',
                   help='Skip mermaid .mmd → .svg render (#667)')
    p.add_argument('--deploy', action='store_true',
                   help='Deploy build/website/ to electricrv.ca (#668 deploy script)')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    langs = LANGS if args.lang == 'all' else (args.lang,)
    formats = FORMATS if args.format == 'all' else (args.format,)

    # extractor is cheap; always run so glossary / sections.yml stay fresh.
    try:
        from extractor import build_schema
        build_schema()
    except ImportError:
        log.info('extractor: skipped (schema YAMLs already exist)')

    if not args.skip_diagrams:
        try:
            from render_diagrams import render_all  # noqa: F401 — #667
            log.info('diagrams: rendering all .mmd → build/diagrams/')
            render_all(ROOT)
        except ImportError:
            log.info('diagrams: skipped (render_diagrams.py not yet — #667)')

    for lang in langs:
        for fmt in formats:
            if fmt == 'assembled-md':
                assemble_markdown(lang)
            elif fmt == 'html':
                build_html(lang)
            elif fmt == 'help':
                build_help_fragments(lang)
            elif fmt == 'pdf':
                build_pdf(lang)
            elif fmt == 'docx':
                build_docx(lang)
            elif fmt == 'website':
                build_website(lang)

    if args.deploy:
        deploy = TOOLS / 'deploy_website.py'
        if deploy.exists():
            log.info('deploy: %s', deploy.relative_to(ROOT))
            subprocess.run([sys.executable, str(deploy)], check=True)
        else:
            log.warning('deploy skipped — deploy_website.py not yet (#669)')

    log.info('docs build complete.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
