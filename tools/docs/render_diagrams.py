"""tools/docs/render_diagrams.py — render .mmd → .svg  (issue #667).

Backends (selected via ``SLYLED_DIAGRAM_BACKEND`` or auto-probe):

- ``mmdc``    — local mermaid-cli (npm install -g @mermaid-js/mermaid-cli).
- ``kroki``   — kroki.io HTTP render; needs outbound network but no install.
- ``auto``    — prefer mmdc, fall back to kroki.

Default theme is "neutral" to match the Kinetic-Prism dark site; override per-
file by adding a ``%%{init}%% ... %%`` block at the top of the .mmd source.

Used by ``tools/docs/build.py`` before the HTML/PDF/DOCX steps so every
downstream render has the SVGs ready. Failures are hard errors — we don't
ship documentation with missing diagrams.
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

log = logging.getLogger('slyled.docs.diagrams')

KROKI_URL = os.environ.get('SLYLED_KROKI_URL', 'https://kroki.io')
KROKI_TIMEOUT_S = int(os.environ.get('SLYLED_KROKI_TIMEOUT_S', '30'))


def _resolve_backend() -> str:
    """Pick mmdc / kroki based on env override + tool availability."""
    override = (os.environ.get('SLYLED_DIAGRAM_BACKEND') or 'auto').lower()
    if override == 'mmdc':
        if not shutil.which('mmdc'):
            raise RuntimeError('SLYLED_DIAGRAM_BACKEND=mmdc but `mmdc` not on PATH')
        return 'mmdc'
    if override == 'kroki':
        return 'kroki'
    # auto: mmdc wins when available.
    return 'mmdc' if shutil.which('mmdc') else 'kroki'


def _render_mmdc(mmd: Path, out: Path, theme: str = 'neutral') -> None:
    cmd = ['mmdc', '-i', str(mmd), '-o', str(out),
           '-t', theme, '-b', 'transparent']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f'mmdc failed on {mmd.name}: '
                           f'stdout={r.stdout[-200:]!r} stderr={r.stderr[-200:]!r}')


def _render_kroki(mmd: Path, out: Path) -> None:
    """kroki.io HTTP render — POST the mermaid source, get SVG back.

    We use the GET /mermaid/svg/<base64> form so the request is one hop and
    has no payload-size gotchas for small mmd files. Larger diagrams switch
    to POST automatically in future if needed.
    """
    import zlib
    src = mmd.read_bytes()
    # kroki accepts base64(deflate(source)) as the path segment.
    encoded = base64.urlsafe_b64encode(zlib.compress(src, 9)).decode('ascii')
    url = f'{KROKI_URL}/mermaid/svg/{encoded}'
    try:
        with urllib.request.urlopen(url, timeout=KROKI_TIMEOUT_S) as resp:
            svg = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace') if e.fp else ''
        raise RuntimeError(f'kroki HTTP {e.code} on {mmd.name}: {body[:200]}')
    out.write_bytes(svg)


def render_diagram(mmd: Path, out_dir: Path, backend: str | None = None) -> Path:
    """Render one .mmd to SVG. Returns the output path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (mmd.stem + '.svg')
    chosen = backend or _resolve_backend()
    log.info('diagram [%s] %s → %s', chosen, mmd.name, out.name)
    if chosen == 'mmdc':
        _render_mmdc(mmd, out)
    elif chosen == 'kroki':
        _render_kroki(mmd, out)
    else:
        raise ValueError(f'unknown backend {chosen!r}')
    return out


def render_all(repo_root: Path, backend: str | None = None) -> list[Path]:
    """Render every .mmd under ``docs/diagrams/`` and
    ``docs/src/diagrams/``. Returns the list of generated SVG paths."""
    srcs = []
    for d in (repo_root / 'docs' / 'diagrams',
               repo_root / 'docs' / 'src' / 'diagrams'):
        if d.is_dir():
            srcs.extend(sorted(d.glob('*.mmd')))
    if not srcs:
        log.warning('no .mmd sources found under docs/diagrams/ or docs/src/diagrams/')
        return []
    out_dir = repo_root / 'docs' / 'build' / 'diagrams'
    out = []
    chosen = backend or _resolve_backend()
    for mmd in srcs:
        out.append(render_diagram(mmd, out_dir, backend=chosen))
    log.info('rendered %d diagram(s) via %s → %s',
             len(out), chosen, out_dir.relative_to(repo_root))
    return out


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')
    import sys
    ROOT = Path(__file__).resolve().parent.parent.parent
    render_all(ROOT)
