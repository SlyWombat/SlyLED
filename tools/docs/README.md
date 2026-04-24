# tools/docs/

Canonical home for every script that turns `docs/src/` into shipped
artefacts. Created for the docs-overhaul-2026-04-24 issue series
(#665–#676).

## Scripts

| Script | Status | Purpose |
|---|---|---|
| `build.py` | scaffolded | Orchestrator — `--lang`, `--format`, `--deploy`. Single entry point. |
| `render_diagrams.py` | pending (#667) | `.mmd` → `.svg` via mermaid-cli, kroki.io fallback. |
| `translate.py` | pending (#670) | EN → FR via pluggable VLM backend (Claude default, Ollama alternative). |
| `screenshot_qa.py` | pending (#671) | VLM-assisted drift check on captured screenshots. |
| `drift_check.py` | pending (#672) | Code ↔ docs sync gate. Weekly CI + PR-time nudge. |
| `deploy_website.py` | pending (#668) | Push `website/dist/` to `electricrv.ca/slyled` via cPanel UAPI. |
| `extractor.py` | pending (#666) | Split `docs/src/{lang}/*.md` into per-section HTML fragments. |

## Prerequisites

Python (already in the orchestrator's venv):

- `python-docx` — DOCX renderer.
- `playwright` — screenshot capture + PDF rasterisation.
- `pyyaml` — glossary / comparison matrix parsing.
- `numpy`, `pillow` — image post-processing for QA.
- `anthropic` (optional) — Claude translation / drift checker (`--backend claude`).

External binaries:

- **Pandoc** ≥ 2.17 — required for HTML + DOCX paths. Install via
  `winget install JohnMacFarlane.Pandoc` on Windows, `brew install pandoc`
  on macOS, `apt install pandoc` on Debian/Ubuntu.
- **Mermaid CLI** (`mmdc`) — optional; `npm install -g @mermaid-js/mermaid-cli`.
  Without it, `render_diagrams.py` falls back to kroki.io (needs outbound
  network).
- **Node.js 18+** — required for the Astro Starlight site build (#669 only;
  not needed for the manual itself).

## Quickstart

```bash
# Regenerate the split-source → assembled-md roundtrip (verifies no source drift)
python tools/docs/build.py --format assembled-md

# Build everything in EN
python tools/docs/build.py --lang en --format all

# Build only PDF in both languages, skip screenshot re-capture
python tools/docs/build.py --format pdf --skip-screenshots

# Build + push to electricrv.ca
python tools/docs/build.py --format website --deploy
```

## Output layout (all gitignored)

```
docs/
  build/
    en/
      html/index.html          Full themed HTML (one file)
      help/<section>.html      Per-section fragments for /api/help
      pdf/SlyLED-Manual.pdf
      docx/SlyLED-Manual.docx
    fr/...
    diagrams/*.svg             Mermaid-rendered SVG
  USER_MANUAL.md               Generated — do NOT edit
  USER_MANUAL_fr.md            Generated — do NOT edit
  USER_MANUAL.{docx,pdf}       Generated — also do NOT edit
  USER_MANUAL_fr.{docx,pdf}    Generated — also do NOT edit
```

The committed `docs/USER_MANUAL.md` + `_fr.md` stay in the repo as
generated artefacts so GitHub previews and existing links keep working.
Edits happen in `docs/src/` and `build.py --format assembled-md`
regenerates.

## Related issues

Tracker label: `docs-overhaul-2026-04-24`. Parent plan in the conversation
history; each script's acceptance criteria live on its own issue.
