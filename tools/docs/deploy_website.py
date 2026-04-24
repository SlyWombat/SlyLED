"""tools/docs/deploy_website.py — push the Astro Starlight build to cPanel (#669).

Flow:

1. Verify ``website/dist/`` exists (Astro site was built).
2. Walk the tree and upload every file to ``/public_html/slyled/`` via
   cPanel UAPI — same client the project already uses in
   ``server/deploy.py``.
3. Also push fresh PDF / DOCX downloads to ``/public_html/slyled/downloads/``
   so the site's "Download manual" buttons resolve against pre-cached
   files instead of proxying the repo.

Credentials come from ``.env`` (CPANEL_HOST, CPANEL_USER, CPANEL_TOKEN,
WEB_ROOT). Dry-run with ``--preview`` logs every operation without
calling the network.

Status: skeleton — the `server/deploy.py` UAPI client already knows how
to talk to the host; this script is the thin wrapper that hands it the
website directory and the downloads list.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger('slyled.docs.deploy')

ROOT = Path(__file__).resolve().parent.parent.parent
WEBSITE_DIST = ROOT / 'website' / 'dist'
DOCS_BUILD = ROOT / 'docs' / 'build'

DOWNLOAD_FILES = [
    ('en', 'pdf',  'docs/USER_MANUAL.pdf'),
    ('en', 'docx', 'docs/USER_MANUAL.docx'),
    ('fr', 'pdf',  'docs/USER_MANUAL_fr.pdf'),
    ('fr', 'docx', 'docs/USER_MANUAL_fr.docx'),
]


def _cpanel_client():
    """Import the shared server/deploy.py client. Kept isolated so this
    file stays importable in environments that don't have credentials."""
    sys.path.insert(0, str(ROOT / 'server'))
    try:
        from deploy import CPanelClient  # type: ignore
    except ImportError:
        raise SystemExit('server/deploy.py missing — nothing to wrap')
    return CPanelClient()


def walk_dist(dist: Path) -> list[Path]:
    return [p for p in dist.rglob('*') if p.is_file()]


def preview(dist: Path, download_root: str) -> None:
    log.info('PREVIEW: would deploy %d file(s) from %s',
              len(walk_dist(dist)), dist.relative_to(ROOT))
    for _, _, src in DOWNLOAD_FILES:
        p = ROOT / src
        if p.exists():
            log.info('PREVIEW: would push %s → %s/%s',
                      src, download_root, p.name)
        else:
            log.info('PREVIEW: skip %s (missing)', src)


def deploy(target_root: str = '/public_html/slyled', dry_run: bool = False) -> int:
    if not WEBSITE_DIST.is_dir():
        log.error('website/dist/ not found — run `npm run build` in website/')
        return 2
    download_root = f'{target_root}/downloads'
    if dry_run:
        preview(WEBSITE_DIST, download_root)
        return 0
    client = _cpanel_client()
    # Site tree.
    for p in walk_dist(WEBSITE_DIST):
        rel = p.relative_to(WEBSITE_DIST).as_posix()
        client.upload_file(p, f'{target_root}/{rel}')
    log.info('deployed %d site files to %s', len(walk_dist(WEBSITE_DIST)),
              target_root)
    # Manual downloads.
    for _lang, _fmt, src in DOWNLOAD_FILES:
        p = ROOT / src
        if not p.exists():
            log.warning('skip missing download %s', src)
            continue
        client.upload_file(p, f'{download_root}/{p.name}')
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--preview', action='store_true')
    p.add_argument('--target', default='/public_html/slyled')
    args = p.parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=logging.INFO,
                         format='%(levelname)s %(name)s: %(message)s')
    return deploy(args.target, dry_run=args.preview)


if __name__ == '__main__':
    sys.exit(main())
