# Documentation sources

Canonical markdown for the SlyLED user manual, marketing site, inline SPA help,
and printable artefacts. Edit here — everything downstream is derived.

## Layout

```
docs/src/
  en/                               English canonical — one file per top-level
                                    manual section, numbered to preserve order.
  fr/                               French mirror — same filenames. Stubs
                                    marked "[FR — traduction en attente]"
                                    until #670 fills them in.
  marketing/                        Landing-page hero, feature cards, press
                                    kit, case studies, PLASA submission draft.
                                    Populated by #672.
  screenshots/                      Capture output from tools/docs/
                                    screenshot_capture.py. Regenerable.
  diagrams/*.mmd                    Mermaid sources. Rendered by
                                    tools/docs/render_diagrams.py (#667).
```

## Build

One entry point:

```bash
python tools/docs/build.py --lang all --format all
```

See `tools/docs/README.md` (issue #665) for the full flag matrix, pandoc /
mermaid prerequisites, and the deploy path.

## Derived artefacts (do NOT edit)

- `docs/USER_MANUAL.md` — assembled from `docs/src/en/*.md`
- `docs/USER_MANUAL_fr.md` — assembled from `docs/src/fr/*.md`
- `docs/USER_MANUAL.{pdf,docx}`, `docs/USER_MANUAL_fr.{pdf,docx}`
- `docs/help/*.html` — per-section fragments served by `/api/help`
- `website/` — Astro Starlight site (#669) pulling from these sources

## Why split

- Per-section files keep reviews focused (one PR ≠ one 2000-line diff).
- Bilingual parity becomes a filename check (EN/FR filename sets must match).
- Inline-help extraction becomes a file read, not a markdown-heading scan.
- Marketing can pull specific sections into the site without regex-parsing a monolith.

## DRAFT banners

Appendices A and B are marked DRAFT pending the full #610 / #651–#661 / #357
series. Removal criteria live in `docs/DOCS_MAINTENANCE.md`. With the
calibration reliability series merged (2026-04-23), DRAFT removal is a
reviewer action on the next doc pass.

Issue tracking for this reorganisation: #665–#676 under the
`docs-overhaul-2026-04-24` label.
