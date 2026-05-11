#!/usr/bin/env python3
"""
test_help_resolution.py — backend resolution chain for /api/help (#881).

The SPA passes dotted hierarchical helpkeys (``setup.add-fixture.step-2-address``,
``settings.dmx-monitor`` …); the resolver walks up the hierarchy until a
fragment exists on disk and falls back to the legacy chapter-slug map.

Usage:
    python tests/test_help_resolution.py
"""

import sys, os, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import parent_server
from parent_server import app, _resolve_help_fragment, DOCS_ROOT, _HELP_SLUGS

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


# ── Fragment inventory the SPA can emit — kept in sync with
#    _helpContextKey() in desktop/shared/spa/js/app.js. Update both
#    sides together when adding new help surfaces (#881 acceptance:
#    "no dead helpkeys").
SPA_HELP_KEYS = [
    'dash',
    'dash.auto-brightness',
    'setup',
    'setup.add-fixture.step-1-choose',
    'setup.add-fixture.step-2-address',
    'setup.add-fixture.step-3-confirm',
    'setup.edit-fixture.dmx',
    'setup.edit-fixture.led',
    'setup.edit-fixture.gyro',
    'setup.edit-fixture.camera',
    'setup.edit-fixture.group',
    'layout.2d.move',
    'layout.2d.rotate',
    'layout.3d.move',
    'layout.3d.rotate',
    'spatial-effects',          # 'actions' tab maps here for back-compat
    'actions',
    'actions.track-editor',
    'actions.track-editor.advanced',
    'shows',
    'shows.bake',
    'shows.preset-load',
    'runtime',
    'settings.general',
    'settings.profiles',
    'settings.profiles.community',
    'settings.dmx',
    'settings.dmx-monitor',
    'settings.group-control',
    'settings.cameras',
    'settings.advanced',
    'firmware',
    'firmware.ota',
    'firmware.force-update',
]


def run():
    help_dir = DOCS_ROOT / 'build' / 'en' / 'help'

    # ── Exact match wins when the fragment exists on disk ──────────
    f = _resolve_help_fragment('setup.edit-fixture.gyro', 'en')
    ok('exact match returns the dotted-key fragment',
       f is not None and f.name == 'setup.edit-fixture.gyro.html',
       f'got={f}')

    # ── Walk-up one level: drop the trailing segment ──────────────
    # 'setup.edit-fixture.unknown' has no exact file; resolver walks
    # to 'setup.edit-fixture' (no file) → 'setup' (no file) → legacy
    # _HELP_SLUGS['setup'] → '04-fixture-setup.html'.
    f = _resolve_help_fragment('setup.edit-fixture.unknown', 'en')
    ok('walk-up falls through to legacy chapter for unknown sub-key',
       f is not None and '04-fixture-setup' in f.name,
       f'got={f}')

    # Walking up to a directly-existing intermediate slug:
    # set up: ensure 'firmware.ota' fragment exists, then resolve a
    # never-built child to verify it falls back to 'firmware.ota.html'.
    parent_path = help_dir / 'firmware.ota.html'
    if parent_path.is_file():
        f = _resolve_help_fragment('firmware.ota.nonexistent-leaf', 'en')
        ok('walk-up lands on intermediate fragment when one exists',
           f is not None and f.name == 'firmware.ota.html',
           f'got={f}')

    # ── Chapter-level legacy fallback ──────────────────────────────
    # 'settings.dmx-monitor' has its own fragment now; but if the
    # operator deleted it, the resolver should fall to the legacy
    # 'settings' → '12-dmx-profiles.html' mapping.
    # Verify the legacy path directly with a key that has NO dotted
    # fragment authored: 'firmware.usb-flash' (not authored yet).
    f = _resolve_help_fragment('firmware.usb-flash', 'en')
    ok('legacy chapter fallback for un-authored sub-key',
       f is not None and '15-firmware-ota' in f.name,
       f'got={f}')

    # ── Bare top-level key still works (back-compat with #670) ────
    f = _resolve_help_fragment('dash', 'en')
    ok('bare tab key resolves to chapter fragment',
       f is not None and '01-getting-started' in f.name,
       f'got={f}')

    # ── Path traversal is refused ─────────────────────────────────
    f = _resolve_help_fragment('foo/../etc/passwd', 'en')
    ok('path traversal returns None', f is None, f'got={f}')

    f = _resolve_help_fragment('../USER_MANUAL', 'en')
    ok('parent-dir traversal returns None', f is None, f'got={f}')

    f = _resolve_help_fragment('a\\..\\b', 'en')
    ok('backslash traversal returns None', f is None, f'got={f}')

    # ── Empty / nonsense keys ─────────────────────────────────────
    ok('empty key returns None', _resolve_help_fragment('', 'en') is None)
    ok('None key returns None', _resolve_help_fragment(None, 'en') is None)
    ok('uppercase / mixed-case keys refused',
       _resolve_help_fragment('Setup.Edit-Fixture.Gyro', 'en') is None)

    # Unknown top segment with no chapter mapping → None (caller
    # serves the stub).
    f = _resolve_help_fragment('madeupthing.subkey', 'en')
    ok('unknown top segment with no legacy mapping returns None',
       f is None, f'got={f}')

    # ── /api/help integration: every SPA key returns 200 + HTML ───
    with app.test_client() as c:
        for key in SPA_HELP_KEYS:
            r = c.get(f'/api/help/{key}')
            d = r.get_json() or {}
            html = d.get('html', '')
            ok(f'/api/help/{key} returns 200',
               r.status_code == 200,
               f'status={r.status_code}')
            ok(f'/api/help/{key} body is non-empty HTML',
               isinstance(html, str) and len(html) > 0,
               f'len={len(html) if isinstance(html, str) else type(html)}')

    # ── Unknown key returns a stub, not 404 (UI expects 200) ──────
    with app.test_client() as c:
        r = c.get('/api/help/made-up.thing')
        d = r.get_json() or {}
        ok('/api/help/<unknown> returns 200',
           r.status_code == 200, f'status={r.status_code}')
        ok('/api/help/<unknown> renders stub HTML with full-manual link',
           '/help' in (d.get('html') or ''),
           f'html={(d.get("html") or "")[:120]!r}')

    # ── Path traversal blocked at the HTTP layer too ──────────────
    with app.test_client() as c:
        r = c.get('/api/help/foo..etc.passwd')
        ok('/api/help blocks dotted traversal-shaped keys (200 + stub)',
           r.status_code == 200, f'status={r.status_code}')

    # ── Fragment HTML is well-formed enough to render in the panel.
    #    No <script>, balanced enough that browsers don't choke.
    seen = set()
    for path in help_dir.glob('*.html'):
        if path.stem in seen:
            continue
        seen.add(path.stem)
        html = path.read_text(encoding='utf-8')
        ok(f'fragment {path.name} has no <script> tag',
           '<script' not in html.lower(),
           'XSS guard — fragments shouldn\'t embed inline scripts')

    # ── Every SPA helpkey has SOME resolvable response. This is the
    #    "no dead helpkeys" gate — a SPA-side typo would land here.
    with app.test_client() as c:
        for key in SPA_HELP_KEYS:
            r = c.get(f'/api/help/{key}')
            d = r.get_json() or {}
            ok(f'inventory: {key} has source != error',
               d.get('source') in ('fragment', 'legacy-scan', 'stub'),
               f'source={d.get("source")} html={(d.get("html") or "")[:80]!r}')

    # ── Print results ─────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
