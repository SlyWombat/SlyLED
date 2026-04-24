# SlyLED website (Astro Starlight)

Public documentation + marketing site for SlyLED, deployed to
`https://electricrv.ca/slyled`.

## Install

```bash
cd website
npm install            # one-time
```

## Local preview

```bash
npm run dev            # http://localhost:4321/slyled
```

## Build + deploy

```bash
# From repo root — syncs docs/src/ → website/src/content/docs/ then runs
# `npm run build` and pushes website/dist/ to cPanel.
python tools/docs/build.py --format website --deploy
```

## Content flow

```
docs/src/en/*.md       ─┐
docs/src/fr/*.md       ─┼── synced at build time ──► website/src/content/docs/{en,fr}/
docs/src/marketing/   ──┘                            website/src/content/marketing/
docs/src/screenshots/ ──► website/public/screenshots/
docs/build/diagrams/  ──► website/public/diagrams/
```

Never hand-edit `website/src/content/docs/` — it's overwritten on every
build. Edit `docs/src/` instead.

## Customisation

- `astro.config.mjs` — navigation, i18n, Starlight options.
- `src/styles/kinetic-prism.css` — theme (matches the in-app docs theme
  at `tools/docs/theme/kinetic-prism.css` via sync-at-build).

## Issue tracker

Label: `docs-overhaul-2026-04-24`. Scaffold is #669; marketing
content population lives in #672.
