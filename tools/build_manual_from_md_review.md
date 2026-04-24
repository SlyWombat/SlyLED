# Gemini Review: tests/build_manual_from_md.py

_Generated The system cannot accept the date entered.
Enter the new date: (yy-mm-dd) via gemini_review_build_manual.py._

Excellent. This is a high-impact piece of code, and a thorough review is exactly what's needed. As a senior engineer, my focus will be on correctness, robustness, and providing a clear, actionable path forward.

Here is my review of `tests/build_manual_from_md.py`.

---

## 1. Parser correctness — inline markdown

The inline parsing, based on `INLINE_RE` and `re.split`, is a common pattern for simple cases but fails on any form of nesting or ambiguity. This leads to several silent-corruption bugs.

-   **BUG: Nested formatting is lost.**
    -   **Reference:** `tests/build_manual_from_md.py:175` (`INLINE_RE`), `tests/build_manual_from_md.py:186` (`add_inline_runs`), `tests/build_manual_from_md.py:338` (`inline_to_html`).
    -   **Input:** The manual contains bold text inside links, like in the Glossary: `| **PnP / solvePnP** | ... |`. While this is in a table, the same issue applies to a paragraph link like `[**Important Link**](#foo)`.
    -   **Analysis:** The `INLINE_RE` will match the link part `\[[^\]]+\]\([^)]+\)` first. The `re.split` will treat the entire `[**Important Link**](#foo)` as a single token. Both `add_inline_runs` and `inline_to_html` will then process this as a link, but they will not re-process the link text `**Important Link**` for further markdown. The bold formatting is silently dropped.
    -   **Fix:** A multi-pass or recursive parsing approach is needed. A quick fix is impossible with the current `re.split` architecture.

-   **BUG: Underscore-style emphasis is not supported.**
    -   **Reference:** `tests/build_manual_from_md.py:175` (`INLINE_RE`).
    -   **Input:** While not present in the sample, `_italic_` and `__bold__` are common Markdown. The regex only handles `*italic*` and `**bold**`.
    -   **Analysis:** The `INLINE_RE` has `\*[^*]+\*` and `\*\*[^*]+\*\*` but no corresponding `_[^_]+_` or `__[^_]+__`. The code in `add_inline_runs` *does* handle `__bold__`, but the `INLINE_RE` doesn't split it out as a token, so that code path is unreachable.
    -   **Fix:** Add `_[^_]+_` and `__[^_]+__` to `INLINE_RE`. The `__bold__` case is already handled in `add_inline_runs`, but `inline_to_html` needs it added.

-   **BUG: Backticks inside link text are parsed incorrectly.**
    -   **Reference:** `tests/build_manual_from_md.py:175` (`INLINE_RE`).
    -   **Input:** A link like `[See `foo()` for details](#foo)`.
    -   **Analysis:** Similar to the bold-in-link issue, the link regex `\[[^\]]+\]\([^)]+\)` will consume the entire token. The backticks inside will be treated as literal characters, not code formatting.

-   **BUG: HTML entities are double-escaped in HTML output.**
    -   **Reference:** `tests/build_manual_from_md.py:337` (`inline_to_html`).
    -   **Input:** Any text containing a pre-escaped entity, e.g., `AT&amp;T`.
    -   **Analysis:** `inline_to_html` calls `t = html.escape(text)` on the entire string first. This converts `&amp;` to `&amp;amp;`. The subsequent regex replacements operate on this double-escaped string.
    -   **Fix:** The logic should be inverted: find markdown tokens, convert them to HTML, and *then* escape the remaining plain-text segments.

-   **RISK: Italic regex `(?<!\*)\*(?!\*)` is fragile.**
    -   **Reference:** `tests/build_manual_from_md.py:344`.
    -   **Input:** Code snippets in prose like `a pointer is not the same as a*b`.
    -   **Analysis:** The negative lookarounds prevent it from matching parts of `**bold**`, but it can still misfire. In `a*b`, it would match `*b*` if there was a word boundary after `b`. The `[^*\n]+` helps, but this is a classic case where simple regex is insufficient. It will fail on `_emphasis_ with an asterisk* inside`.

-   **Missing Features:** The parser does not support reference-style links (`[text][id]`), auto-links (`<http://...>` or bare URLs), or escaped characters (`\*`, `\_`, `\|`). These will be rendered literally.

## 2. Parser correctness — block constructs

The block-level parsing is sequential and does not handle nesting correctly, which is a major source of bugs.

-   **BUG: Tables with pipes in code or links are parsed incorrectly.**
    -   **Reference:** `tests/build_manual_from_md.py:82` (`[c.strip() for c in stripped.strip("|").split("|")]`).
    -   **Input:** A hypothetical table row: `| `foo | bar` | Link with [a | pipe](#) |`.
    -   **Analysis:** The `split('|')` call is naive. It does not respect markdown syntax within the cells. The example row would be incorrectly split into multiple columns, corrupting the table structure.
    -   **Fix:** This requires a more sophisticated parsing strategy that tokenizes inline markdown *before* splitting by the pipe delimiter, ignoring pipes found inside code spans or link text.

-   **BUG: Fenced code block info-strings are too restrictive.**
    -   **Reference:** `tests/build_manual_from_md.py:53` (`re.match(r"^```(\w*)\s*$", stripped)`).
    -   **Input:** A common info-string like ` ```mermaid theme="dark" `.
    -   **Analysis:** The regex `(\w*)` only allows word characters (`[a-zA-Z0-9_]`) for the language name. It does not allow for any other attributes. This will cause the parser to miss the start of the code block, treating it as a paragraph.
    -   **Fix:** Loosen the regex to something like `^```(\S*)\s*$` to capture any non-whitespace characters as the info string, then parse the language from that.

-   **BUG: Blockquote parsing flattens all content.**
    -   **Reference:** `tests/build_manual_from_md.py:68` (`quote_lines.append(lines[i].lstrip(">").lstrip())`).
    -   **Input:** `USER_MANUAL.md`, Appendix A, contains a Mermaid diagram inside a blockquote: `> ⚠ **DRAFT** ... \n > \n > ```mermaid ...`.
    -   **Analysis:** The parser will identify the blockquote. It will then loop, stripping all leading `>` characters from each line. A nested quote `>> foo` becomes `foo`, losing a level of indentation. A code fence `> ```mermaid` becomes ` ```mermaid`. The parser then joins all these lines with `\n`. The result is a single `Block("quote", text=...)`. The code fence inside is not parsed as a separate block; it becomes literal text inside the quote block.
    -   **Fix:** A proper fix requires a recursive descent parser that can parse blocks *within* other blocks.

-   **BUG: List indentation is hardcoded to 2 spaces.**
    -   **Reference:** `tests/build_manual_from_md.py:96` (`indent = len(m2.group(1)) // 2`).
    -   **Input:** A nested list using 4-space indentation.
    -   **Analysis:** The code assumes 2-space indents. A 4-space indent will be parsed as `level=2`, while a 3-space indent will be parsed as `level=1`. This is not compliant with GFM, which allows for different indentation styles.

-   **Missing Features:** The parser does not support Setext headings (e.g., `Heading\n=======`). This would be parsed as a paragraph followed by a horizontal rule.

## 3. Docx rendering bugs

The docx renderer has several minor bugs and areas for improvement.

-   **BUG: Image paths are handled incompletely.**
    -   **Reference:** `tests/build_manual_from_md.py:255`.
    -   **Input:** An image with an absolute path (`/foo/bar.png`), a `file://` URL, or an `http://` URL.
    -   **Analysis:** The code checks `is_absolute()` but then proceeds to prepend `DOCS` or `ROOT` to it, which is incorrect. It does not handle `file://` or `http://` URLs at all; `python-docx` cannot embed images from URLs directly, they must be downloaded first.
    -   **Fix:** The logic should be: if absolute, use it directly. If a URL, download it to a temp file. If relative, then try resolving against `DOCS` and `ROOT`.

-   **RISK: Clearing table cells is fragile.**
    -   **Reference:** `tests/build_manual_from_md.py:250` (`cell.paragraphs[0].text = ""`).
    -   **Analysis:** This assumes every cell created by `doc.add_table` has exactly one paragraph. While true for new tables, if this code were ever adapted to modify existing tables, it could fail. The current usage is safe.

-   **Improvement: Use Bookmarks for Anchors.**
    -   **Reference:** `tests/build_manual_from_md.py:270`.
    -   **Analysis:** The current code correctly strips `<a id="...">` tags, making them invisible. However, the correct way to represent an anchor in `.docx` is with a bookmark. This would allow other parts of the document to link to it, which is a feature `python-docx` supports but is not implemented here.

## 4. HTML rendering bugs (consumed by Playwright for PDF)

The HTML renderer has correctness issues related to escaping and slug generation.

-   **BUG: Link parsing can be broken by `](`.**
    -   **Reference:** `tests/build_manual_from_md.py:339` (`re.sub(r"\[([^\]]+)\]\(([^)]+)\)", ...)`).
    -   **Input:** A text fragment like `[a link to a file named foo](bar)baz].md`.
    -   **Analysis:** The `[^\]]+` and `[^)]+` are greedy. The regex will match `[a link to a file named foo](bar)baz]`. The link text will be `a link to a file named foo` and the URL will be `bar)baz`. This is incorrect.
    -   **Fix:** Robust link parsing is notoriously difficult with a single regex. A proper tokenizer is needed.

-   **BUG: Heading slugs do not support Unicode.**
    -   **Reference:** `tests/build_manual_from_md.py:358` (`re.sub(r"[^\w\s-]", "", slug)`).
    -   **Input:** `docs/USER_MANUAL_fr.md`, heading `## 1. Premiers pas`.
    -   **Analysis:** The regex removes any character that is not a word character, whitespace, or hyphen. This strips the accented `è` from `Premiers`, resulting in a slug like `premiers-pas`. This can break same-page links if they expect correct Unicode slugs.
    -   **Fix:** The regex should be modified to preserve letters from all languages, or a dedicated slugify library should be used. A simple fix is to add Unicode character properties if the regex engine supports it, or explicitly add common accented characters to the allowed set.

-   **Redundancy: CSS `@page` margins and Playwright `page.pdf()` margins.**
    -   **Reference:** `tests/build_manual_from_md.py:292` and `tests/build_manual_from_md.py:449`.
    -   **Analysis:** The CSS defines `@page { margin: 0.75in; }` and the `page.pdf()` call also specifies the exact same margins. The `page.pdf()` call will override the CSS. This is harmless but redundant.
    -   **Fix:** Remove the `margin` from the `page.pdf()` call and rely on the CSS `@page` directive, which is the correct place to specify print styling.

## 5. Playwright PDF gotchas

The Playwright integration is simple and has a few platform-specific and robustness risks.

-   **RISK: `file://` URL generation is platform-dependent.**
    -   **Reference:** `tests/build_manual_from_md.py:447` (`page.goto(f"file://{html_path}")`).
    -   **Analysis:** On Linux/macOS, this will produce a valid `file:///path/to/file`. On Windows, a `Path` object stringifies to `C:\path\to\file`, resulting in an invalid URL `file://C:\...`. The correct method is to use `html_path.as_uri()`, which correctly generates `file:///C:/...` on Windows. Since the context is Linux, this is not an active bug, but it's not portable.

-   **RISK: Chromium must be installed manually.**
    -   **Reference:** `tests/build_manual_from_md.py:445` (`browser = p.chromium.launch()`).
    -   **Analysis:** If the Playwright browsers have not been installed via `playwright install chromium`, this call will raise an exception. The script does not handle this or provide instructions.
    -   **Fix:** Add a `try...except` block around the Playwright import or launch call that prints a helpful error message, e.g., "Playwright Chromium not found. Please run 'playwright install chromium'".

-   **RISK: Loading local images might be blocked.**
    -   **Reference:** `tests/build_manual_from_md.py:414` (`<img src="file://{found}" ...>`).
    -   **Analysis:** By default, for security reasons, web pages loaded from `file://` URLs are often blocked from accessing other `file://` resources. Playwright may configure Chromium with `--allow-file-access-from-files`, but if it doesn't, the images will not load.
    -   **Fix:** A more robust solution is to embed images as base64-encoded `data:` URIs in the HTML, which avoids all file access issues.

## 6. Robustness / security

For an internal documentation tool, the security risks are low, but the robustness against unusual inputs could be improved.

-   **Path Traversal:** The image path resolution logic at `tests/build_manual_from_md.py:255` is vulnerable to path traversal (e.g., `../../...`). Given the trusted context (devs running their own code on their own repo), this is a very low risk.
-   **Regex DoS:** The regular expressions used do not appear to be susceptible to catastrophic backtracking, as they avoid nested quantifiers on complex patterns. The risk is low.
-   **Unicode:** As noted in #4, Unicode handling for slugs is broken. The rest of the file I/O correctly uses `encoding="utf-8"`, which should preserve content from the French manual.

## 7. Simplifications

-   **Recommendation: Replace with a standard library.**
    -   The core problem is that this script re-implements a Markdown parser, which is a complex and bug-prone task. The number of correctness bugs in inline and block parsing demonstrates this.
    -   The constraint of "no `pandoc`, no `markdown` library" is the root cause of this complexity and fragility. The most effective simplification is to **challenge this constraint**.
    -   Installing a library like `markdown-it-py` with a few plugins (`mdit-py-plugins.front-matter`, `mdit-py-plugins.deflist`, etc.) would replace `parse_markdown` and `inline_to_html` with a single, well-tested function call. This would eliminate nearly all bugs identified in sections 1 and 2. The engineering cost of adding `pip install markdown-it-py` to the workflow is far lower than the cost of maintaining this hand-rolled parser and fixing its inevitable bugs.

## 8. Specific suspicions I want you to check

-   **Paragraphs and HTML:** The manual's use of `<a id="..."></a>` on a line by itself, separated by blank lines, is correctly handled. The parser identifies it as an `html` block. However, if an HTML tag were part of a paragraph without surrounding blank lines, the paragraph accumulator at `tests/build_manual_from_md.py:124` would incorrectly consume it, as it does not have a break condition for HTML tags. This is a fragility that happens to be avoided by the current manual's style.

-   **Anchor Attachment:** The parser creates separate `html` and `heading` blocks. The renderers process them sequentially. This works correctly for both HTML (where the anchor precedes the heading) and docx (where the anchor is ignored/stripped).

-   **Glossary Table Bold:** Yes, the `**PnP / solvePnP**` text is preserved as a string in the table cell data. Both `add_inline_runs` and `inline_to_html` are called on this cell text and will correctly apply the bold formatting. This works as expected.

-   **French Manual Corruption:** The primary corruption is in the HTML heading slugs, as identified in #4. The content itself should round-trip correctly to both docx and the HTML body due to consistent `utf-8` encoding.

## 9. Output quality vs. brand goals

-   **Recommendation: (a) Keep this file minimal and stable.**
-   **Reasoning:** This script is a stopgap measure and a piece of technical debt. The strategic goal is to replace it with a robust Pandoc-based pipeline that can properly handle the desired "Kinetic-Prism" styling. Investing any effort in adding theming to this fragile, hand-rolled script would be throwaway work. The engineering time is better spent on:
    1.  Fixing the critical correctness bugs identified in this review to ensure the stopgap is reliable.
    2.  Accelerating the development and deployment of the Pandoc pipeline (#665), which is the correct long-term solution.

    This script should be treated as a legacy component in maintenance mode: fix critical bugs, but add no new features.

---

### Prioritised Fix List

If I were to fix this file, I would address the bugs in this order:

1.  **Blockquote Content Flattening (High Priority):** This is the most severe bug actively corrupting the existing `USER_MANUAL.md` file. The Mermaid diagram in Appendix A is currently being rendered as plain text inside a blockquote instead of a proper code block. This requires a significant refactor of the `parse_markdown` function to handle nested blocks.
2.  **Table Pipe Splitting (High Priority):** This is a classic GFM parsing error waiting to happen. Any table cell containing a `|` inside backticks or a link will break the document structure. This is a high risk for future edits.
3.  **Unicode Heading Slugs (Medium Priority):** This breaks same-page navigation for all non-ASCII headings in the French manual's HTML/PDF output. It's a significant correctness bug for internationalization.
4.  **Nested Inline Formatting (Medium Priority):** Losing bold/code formatting inside links degrades the quality and correctness of the output.
5.  **Fenced Code Block Info-String (Low Priority):** While a bug, the current manual only uses simple info-strings like `mermaid` or `powershell`, which the current regex handles. This is a latent bug that will surface if more complex info-strings are used.