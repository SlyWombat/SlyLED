#!/usr/bin/env python3
"""Verify each Gemini-review finding against tests/build_manual_from_md.py.

Reports PASS (no bug or not triggered) / FAIL (bug reproduced) per finding.
Run: /usr/bin/python3 tests/verify_build_manual_findings.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))

import build_manual_from_md as b

RESULTS = []

def check(name, predicate, detail):
    status = "FAIL" if predicate else "PASS"
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")

# ── 1. Blockquote flattening — does a nested code fence inside a blockquote
#     get parsed as a code block, or get swallowed as quote text?
md = """> DRAFT banner
>
> ```mermaid
> flowchart TD
>   A --> B
> ```
>
> end of quote.

Next paragraph."""
blocks = b.parse_markdown(md)
kinds = [b.kind for b in blocks]
code_blocks = [b for b in blocks if b.kind == "code"]
check(
    "1. Blockquote with nested code fence",
    len(code_blocks) == 0,  # FAIL if no code block extracted
    f"blocks={kinds}; mermaid block should be parsed as code, got {len(code_blocks)} code blocks",
)

# Also check: does the actual manual currently have any blockquote containing
# a nested fence? This tells us if finding #1 describes a CURRENT bug or a latent one.
manual = (ROOT / 'docs' / 'USER_MANUAL.md').read_text(encoding='utf-8')
import re
quote_with_fence = re.search(r'(?m)^> .*\n(?:> .*\n)*> ```', manual)
check(
    "1b. Current manual has nested fence in blockquote (active bug)",
    quote_with_fence is not None,
    "no occurrence found — finding #1 is a latent risk only" if not quote_with_fence else f"match: {manual[quote_with_fence.start():quote_with_fence.start()+80]!r}",
)

# ── 2. Table pipe splitting — pipe inside inline code / link text
md = "| col1 | col2 |\n|------|------|\n| `a \\| b` | [x \\| y](#z) |\n"
blocks = b.parse_markdown(md)
tbl = next((x for x in blocks if x.kind == "table"), None)
check(
    "2. Table with pipe inside backtick code",
    tbl is None or len(tbl.rows[1]) != 2,
    f"table rows: {tbl.rows if tbl else None}" if tbl else "no table parsed",
)

# ── 3. HTML entity double-escape in HTML output
t = b.inline_to_html("URL: https://a.com/?x=1&y=2")
check(
    "3. HTML entity double-escape",
    "&amp;amp;" in t or ("&amp;" not in t and "&" in t.replace("&amp;", "")),
    f"output: {t!r}",
)

# 3b. Link with ampersand in URL
t2 = b.inline_to_html("[link](https://a.com/?x=1&y=2)")
check(
    "3b. Link URL with & gets double-escaped",
    "&amp;amp;" in t2,
    f"output: {t2!r}",
)

# ── 4. Unicode heading slugs stripped
# Simulate what render_html does for a heading slug
def slug(text):
    t = text.lower()
    t = re.sub(r'<a id="([^"]+)"></a>', "", t)
    t = re.sub(r"[^\w\s-]", "", t).strip().replace(" ", "-")
    return t

s1 = slug("1. Premiers pas")
s2 = slug("Démarrer avec la configuration")
s3 = slug("Configuración")
check(
    "4. French heading slug preserves accents",
    "é" not in s2 and "ó" not in s3,  # FAIL if accents stripped
    f"'Premiers pas' -> {s1!r}; 'Démarrer' -> {s2!r}; 'Configuración' -> {s3!r}",
)

# ── 5. Nested inline — bold inside link
t = b.inline_to_html("See [**Important**](#foo) here.")
check(
    "5a. Bold inside link text (HTML)",
    "<strong>Important</strong>" not in t,
    f"output: {t!r}",
)

# 5b. Backtick inside link text
t = b.inline_to_html("See [`code`](#foo) here.")
check(
    "5b. Backtick inside link text (HTML)",
    "<code>code</code>" not in t,
    f"output: {t!r}",
)

# ── 6. Underscore emphasis
t_italic = b.inline_to_html("_italic_ text")
t_bold = b.inline_to_html("__bold__ text")
check(
    "6a. Underscore italic _x_ → <em>",
    "<em>italic</em>" not in t_italic,
    f"output: {t_italic!r}",
)
check(
    "6b. Underscore bold __x__ → <strong>",
    "<strong>bold</strong>" not in t_bold,
    f"output: {t_bold!r}",
)

# Also: INLINE_RE claims to handle __bold__ but it's unreachable
inline_re_match = b.INLINE_RE.split("__bold__")
check(
    "6c. INLINE_RE splits __bold__ as a token",
    len(inline_re_match) < 2 or "__bold__" not in inline_re_match,
    f"INLINE_RE.split('__bold__') = {inline_re_match!r}",
)

# ── 7. Fenced code info-string regex
md = "```python title=\"foo.py\"\nprint('hi')\n```\n"
blocks = b.parse_markdown(md)
code = next((x for x in blocks if x.kind == "code"), None)
check(
    "7. Info-string with attributes rejected",
    code is None,  # FAIL if no code block parsed
    f"blocks={[b.kind for b in blocks]}",
)

# ── 8. file:// URL portability — check if as_uri() is used
src = (ROOT / 'tests' / 'build_manual_from_md.py').read_text()
check(
    "8. Uses html_path.as_uri() for file:// (portable)",
    "as_uri()" not in src and 'file://{' in src,  # FAIL if uses f-string instead of as_uri
    "found `f\"file://{html_path}\"` pattern, no `.as_uri()` call",
)

# ── 9. Chromium-install guard
check(
    "9. Has playwright chromium install guard",
    "playwright install" not in src.lower(),  # FAIL if no guidance
    "no 'playwright install chromium' hint in except path",
)

# ── 10. Redundant @page + page.pdf margins
has_page_css = "@page" in src and "margin: 0.75in" in src
has_pdf_margin = 'margin={"top": "0.75in"' in src or "'top': '0.75in'" in src
check(
    "10. Redundant margin declarations",
    has_page_css and has_pdf_margin,
    f"@page CSS margin: {has_page_css}; page.pdf margin: {has_pdf_margin}",
)

# ── 11. Local images use file:// (sandbox risk)
check(
    "11. Local images served as file:// (not data: URI)",
    'file://{found}' in src,  # FAIL (confirms finding)
    "HTML uses `<img src=\"file://{found}\">` — may be blocked by Chromium sandbox",
)

# ── Extra: verify the built docx actually has the Mermaid blocks as code,
# not as plain text swallowed into a blockquote
try:
    from docx import Document
    doc = Document(ROOT / 'docs' / 'USER_MANUAL.docx')
    mermaid_source_in_doc = sum(
        1 for p in doc.paragraphs
        if "flowchart" in p.text.lower() or "stateDiagram" in p.text.lower()
            or "sequenceDiagram" in p.text.lower()
    )
    check(
        "12. Mermaid source present in docx (rendered as code-like)",
        mermaid_source_in_doc == 0,
        f"found {mermaid_source_in_doc} paragraphs containing mermaid keywords",
    )
except Exception as e:
    print(f"[SKIP] 12. Mermaid-in-docx check: {e}")

# ── Summary
print()
fails = [r for r in RESULTS if r[1] == "FAIL"]
print(f"=== {len(fails)} FAIL / {len(RESULTS) - len(fails)} PASS ===")
for name, status, detail in RESULTS:
    if status == "FAIL":
        print(f"  FAIL  {name}")

sys.exit(len(fails))
