#!/usr/bin/env python3
"""Review tests/build_manual_from_md.py with Gemini.

Hand-rolled markdown → docx / html / pdf converter added in 4b776d9 as part
of the #662/#663/#664 documentation work. ~400 lines, no test coverage,
consumed by every future manual build. High-leverage to catch parser bugs
before they silently corrupt downstream outputs.

Feeds Gemini:
  - The full converter source
  - The English + French manuals (real input it must handle correctly)
  - System context + structured review questions

Output lands at tools/build_manual_from_md_review.md.
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent

# ── .env loader ─────────────────────────────────────────────────────────
env_path = ROOT / '.env'
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
if not api_key:
    if len(sys.argv) >= 2:
        api_key = sys.argv[1]
    else:
        print("No API key found. Set GOOGLE_API_KEY in .env or pass as argument.")
        sys.exit(1)


def _read(path):
    return Path(ROOT / path).read_text(encoding='utf-8')


converter = _read('tests/build_manual_from_md.py')
manual_en = _read('docs/USER_MANUAL.md')
manual_fr = _read('docs/USER_MANUAL_fr.md')

prompt = f'''You are a senior Python engineer reviewing a hand-rolled markdown → docx / html / pdf
converter for a stage-lighting product called SlyLED. The file under review is
`tests/build_manual_from_md.py`; it is called from a CI-free doc-build workflow to
regenerate `docs/USER_MANUAL.{{docx,pdf}}` and `docs/USER_MANUAL_fr.{{docx,pdf}}` whenever
the canonical markdown changes. There is **zero test coverage**. Bugs in this parser silently
corrupt every manual output going forward — this is the high-leverage surface in the repo right
now. Review it like you'd review a linter or a codegen tool: edge cases win.

## System context

- Companion to `tests/build_manual.py`, which hand-crafts a styled docx from scratch (not shown).
  The new converter deliberately outputs to different filenames (`USER_MANUAL.docx/.pdf`) so it
  does not clobber the styled artefact (`SlyLED_User_Manual.docx/.pdf`).
- Consumers today:
  - docx: `python-docx` (Word + Google Docs open it)
  - pdf: Playwright Chromium via intermediate HTML (file:// URL, `page.pdf()`)
- The Linux side has `python-docx` + Playwright in `/usr/bin/python3`. No `pandoc`, no
  `pypandoc`, no `markdown` library, no `mmdc`, no Kroki. That is why this is hand-rolled.
- Future callers (issues #665, #666, #667) will replace this with a Pandoc-driven pipeline plus
  server-side Mermaid rendering, but that hasn't shipped. Until then, this file is authoritative.
- The manual mixes: GFM pipe tables (including pipes inside link text and inline code),
  fenced code blocks (including `lang=mermaid`), blockquotes, ordered / unordered / nested lists,
  images, `<a id="...">` explicit anchors, em-dashes in headings, `[term](#glossary)` links
  pointing at the same-file Glossary section, emoji (⚠), and — in the French file — accented
  characters throughout.
- The Mermaid diagrams currently render as source-code blocks with a caption because there is
  no local Mermaid renderer. That is intentional for now (tracked by #667).

## File under review: tests/build_manual_from_md.py

```python
{converter}
```

## Input file 1: docs/USER_MANUAL.md (English, ~2200 lines)

This is the real input the converter must handle without corrupting. Every construct in here
must round-trip: tables with links, code blocks containing pipes, em-dashes in headings,
nested lists inside blockquotes, explicit anchor tags preceding headings. Look for any markdown
construct in this file that the parser will mis-handle.

```markdown
{manual_en}
```

## Input file 2: docs/USER_MANUAL_fr.md (French stub, ~680 lines)

Shorter mirror, still has tables, headings, blockquotes, accented characters. The parser must
handle UTF-8 correctly through the docx + html + pdf paths.

```markdown
{manual_fr}
```

---

## Your review tasks

Be critical and specific. Cite file:line references in the converter and actual input
snippets from the manual files when you identify a bug. Format your response as Markdown
with one H2 per review task.

### 1. Parser correctness — inline markdown

`INLINE_RE` at line ~175 and `add_inline_runs` / `inline_to_html` / `linkify` etc. handle
bold / italic / code / links. Walk through every quirk you can find:

- Bold inside a link text (`[**x**](...)`) — does it work?
- Italic with underscore-style (`_x_`) — the regex only handles asterisk italic.
- Italic containing an asterisk escape — does the greedy/non-greedy matching break?
- Backticks inside a link: `[ \`x\` ](...)` — does the link regex or the code regex win?
- Reference-style links (`[x][1]`)?
- Auto-links (`<https://foo>`)? Bare URLs?
- HTML entities already in the markdown (`&amp;`) — does `html.escape` double-escape?
- Escape sequences (`\\*`, `\\_`, `\\|`) — handled?
- Inline HTML tags other than `<a id=...>` (`<br>`, `<sub>`, `<kbd>`) — what happens?
- In `inline_to_html`: the italic regex `(?<!\\*)\\*(?!\\*)` — does it misfire on code like
  `x**2 * y` or on a single asterisk inside a table cell?

### 2. Parser correctness — block constructs

- `is_table_sep` — does it accept tables that the GFM spec allows (colon alignment markers
  `| :--- | :---: | ---: |`)? Does it reject near-misses that are actually paragraphs with
  three dashes? How does it interact with `---` horizontal-rule detection?
- Tables with pipes inside inline code or link text — the row split is `line.strip('|').split('|')`.
  Does `| a \\| b | c |` survive? Do pipes inside `\`code\`` survive?
- Fenced code blocks with info-strings longer than one word (` ```python title="foo.py" `) —
  does the `re.match(r"^```(\\w*)\\s*$", ...)` regex reject valid code blocks?
- Setext headings (`\\nHeading\\n=======`)? Not used in the manual, but worth noting if missing.
- Nested lists — the `indent = len(m2.group(1)) // 2` assumes 2-space indentation. What if
  the input uses 4 spaces or tabs? The manual may be inconsistent.
- Blockquotes containing other block elements (lists, code, nested `>`) — quote handling
  is a single-pass that just strips leading `>` and rejoins. Does that break a `>` `> foo`
  nested quote? What about a quote containing a table?
- The `html` block detection at line ~118 only catches self-closing-like tags with no closing
  pair in the same token (`stripped.startswith("<") and stripped.endswith(">") and "</" not in stripped`).
  Does that miss multi-line anchor blocks or inline-HTML like `<span id="x">content</span>`?
- Image detection — `^!\\[([^\\]]*)\\]\\(([^)]+)\\)\\s*$` rejects images inline in a paragraph
  and images with title attributes like `![alt](src "title")`. Known limitation?

### 3. Docx rendering bugs

- Style lookup: `doc.add_paragraph(style=f"Heading {{lvl}}")` — what if `lvl > 9`? python-docx
  caps at Heading 9.
- The list-style lookup tries `List Number` / `List Bullet`, with a `KeyError` fallback.
  Does python-docx raise `KeyError` or `ValueError` for missing styles? Which is right?
- Table header bold (`base_bold=(ri == 0)`) — when a cell contains an explicit `**foo**`
  the bold gets applied twice, is that a problem?
- `cell.paragraphs[0].text = ""` clears the paragraph but may leave an empty run; do the
  subsequent `add_inline_runs` calls attach to the right paragraph?
- Images: the path resolution tries `DOCS / src` then `ROOT / src`. What if the markdown
  uses an absolute path, a `file://` URL, or an HTTP URL?
- Anchor tags rendered as greyed italic text — that's ugly in Word. Should they be hidden
  entirely? Bookmarks are the python-docx-correct way to express anchors.

### 4. HTML rendering bugs (consumed by Playwright for PDF)

- `render_html` escapes via `html.escape` then re-applies markup. A link with special chars in
  the URL (`[x](https://a.com/?q=1&b=2)`) — the ampersand gets `&amp;`-escaped, then the
  regex replaces `\\[([^\\]]+)\\]\\(([^)]+)\\)`, which captures the escaped URL. Result?
- Link text containing a literal `](` — the regex is non-greedy-ish, but does `[foo](bar)baz]` handle?
- Heading slug generation uses `re.sub(r"[^\\w\\s-]", "", ...)` — drops em-dashes AND parentheses
  AND accents. French accented headings get slugs that are missing chars. The manual has
  explicit `<a id="...">` for the appendix headings, so they work — does anything else depend
  on auto-slug?
- `inline_to_html`'s italic regex `(?<!\\*)\\*(?!\\*)([^*\\n]+)\\*(?!\\*)` can't span newlines,
  but a multi-word italic that wraps across a paragraph reflow wouldn't anyway — correct?
- The CSS uses `font-family: Calibri, 'Segoe UI', Arial, sans-serif`. On Chromium Linux, none
  of these are present — what actually renders?
- `@page {{ size: Letter; margin: 0.75in; }}` then `page.pdf(margin=...)` — which wins?
- `pre.mermaid-src::before` adds "[Mermaid diagram source — rendered version in docs/diagrams/]"
  via CSS content. Is that guaranteed to render in Chromium's print CSS? Some CSS
  pseudo-elements do not print consistently.

### 5. Playwright PDF gotchas

- `page.goto(f"file://{{html_path}}")` — the `html_path` is a Path; on Windows this would need
  `file:///C:/...`. The script runs on Linux so this is probably fine, but calling it out.
- `wait_for_load_state("networkidle")` for a file:// URL — what's "network idle" when there's
  no network? Does this hang or return immediately?
- The HTML uses `file://` for embedded images — does Chromium allow loading them with default
  sandboxing? Does it emit mixed-content / file-access warnings?
- The chromium-install step is missing — does `p.chromium.launch()` fail silently if
  `playwright install chromium` was never run?

### 6. Robustness / security

- Path-traversal: image srcs like `![x](../../../etc/passwd)` — harmless for a local dev
  manual, but worth noting.
- Unbounded regex on user input — any catastrophic-backtracking risk (`INLINE_RE`, the link
  regex, the list-item regex)? Feed a pathological input and see.
- Unicode handling — zero-width joiners in emoji, combining characters in accents, BOM at
  file start. The French manual uses combining accents — do slug + escape routines preserve them?
- Large-table pathology — the manual's API Quick Reference has a big table; any O(n²) risk?

### 7. Simplifications

- Would a standard library (`markdown-it-py`, `mistune`, `markdown`, `commonmark`) eliminate
  most of this file? What is the tradeoff vs. the current hand-roll? Note: the env has none
  of these installed, so a recommendation requires a pip-install step.
- If the recommendation is "keep hand-roll but fix these specific bugs," prioritise the
  fixes: which are most likely to bite on the actual manual files above?

### 8. Specific suspicions I want you to check

- In `parse_markdown`, the paragraph accumulator break-condition list is: blank, heading,
  fence, blockquote, rule, list-item, table, image. It does **not** break on a line that is
  a standalone HTML tag. Does the manual contain any such case, and if so does it lead to
  a mangled paragraph?
- The explicit `<a id="..."></a>` lines that precede Appendix headings — these appear before
  the `## Heading` line in the source. The heading line comes immediately after (one blank
  line between). Does the parser correctly attach the anchor to the heading?
- The Glossary table in `USER_MANUAL.md §20` has entries like `**PnP / solvePnP**` in the
  Term column. Does the table cell parse preserve the bold? The glossary-drift check in
  `docs/DOCS_MAINTENANCE.md` depends on being able to extract these terms.
- The French manual contains `(EBAUCHE)` in headings and `(anglais)` in body — accented
  chars throughout. Any round-trip corruption?

### 9. Output quality vs. brand goals

The manual is a **selling feature**, not just a reference. Issues #662/#663/#664 all tag
Kinetic-Prism styling as the next milestone (dark theme, Space Grotesk headings, accent
colours, glow effects). The current converter is intentionally plain.

Given the plan to replace this with a Pandoc-driven Kinetic-Prism build (issues #665–#669
filed + closed 2026-04-24, status ambiguous), should we:
(a) keep this file minimal and stable, add the theming later in the Pandoc pipeline;
(b) invest in theming this file now because the Pandoc pipeline may not land;
(c) ditch this file entirely the moment #665 ships?

Give a concrete recommendation with reasoning.

---

Organise your output as:
- H2 per review task number
- Under each: specific findings (bug / risk / simplification) with file:line and
  input-snippet citations
- End with a prioritised fix list: what would you fix first, second, third?
'''

client = genai.Client(api_key=api_key)
print("Sending tests/build_manual_from_md.py + real manuals to Gemini...")
print(f"Prompt size: {len(prompt):,} chars")
response = client.models.generate_content(
    model='gemini-2.5-pro',
    contents=prompt,
    config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=16384),
)
review = response.text
out_path = ROOT / 'tools' / 'build_manual_from_md_review.md'
out_path.write_text(
    f"# Gemini Review: tests/build_manual_from_md.py\n\n"
    f"_Generated {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()} via {Path(__file__).name}._\n\n"
    + review,
    encoding='utf-8',
)
print(f"Review saved to {out_path}")
print("---")
print(review)
