#!/usr/bin/env python3
"""Gemini review of the current mover-calibration code.

In the two weeks leading up to 2026-04-24 a large batch of calibration-
reliability fixes landed on main (issues #610, #357, #625-627, #647,
#651-655, #658-661). This script pulls each issue's closing comment
(with commit hash + behaviour description), the current state of the
three core source files, and the DRAFT Appendix B content, and asks
Gemini to verify the behaviour actually shipped and flag any bugs or
documentation drift.

Output: tools/mover_cal_review.md
"""
import json
import os
import subprocess
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

env_path = ROOT / '.env'
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

api_key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
if not api_key:
    print("No API key found in .env", file=sys.stderr)
    sys.exit(1)

REPO = 'SlyWombat/SlyLED'
ISSUES = [651, 652, 653, 654, 655, 658, 659, 660, 661, 357, 625, 626, 627, 647]


def _gh(path, jq=None):
    args = ['gh', 'api', f'repos/{REPO}/{path}']
    if jq:
        args += ['--jq', jq]
    out = subprocess.check_output(args, stderr=subprocess.STDOUT)
    return out.decode('utf-8', errors='replace')


def fetch_issue(n):
    head = json.loads(_gh(f'issues/{n}', '{title, body, state, closedAt}'))
    comments_raw = _gh(f'issues/{n}/comments', '[.[] | {body, createdAt}]')
    comments = json.loads(comments_raw)
    # Pick the comment that mentions a commit hash (the closing/verification comment)
    closing = None
    for c in reversed(comments):
        if 'commit' in c['body'].lower() or ' on `main`' in c['body']:
            closing = c['body']
            break
    if not closing and comments:
        closing = comments[-1]['body']
    return {
        'num': n,
        'title': head['title'],
        'body': (head.get('body') or '')[:2500],
        'closing': (closing or '(no closing comment)')[:2500],
    }


def _read(path):
    return (ROOT / path).read_text(encoding='utf-8')


print("Fetching 14 issues…", file=sys.stderr)
issues = [fetch_issue(n) for n in ISSUES]
print("Reading source files…", file=sys.stderr)
mover_cal = _read('desktop/shared/mover_calibrator.py')
mover_ctl = _read('desktop/shared/mover_control.py')
parametric = _read('desktop/shared/parametric_mover.py')

# Appendix B content — pull just that section of the assembled markdown
manual = _read('docs/USER_MANUAL.md')
b_start = manual.find('## Appendix B')
b_end = manual.find('<a id="appendix-c"></a>')
appendix_b = manual[b_start:b_end] if b_start >= 0 and b_end >= 0 else '(Appendix B not found)'

issues_block = "\n\n".join(
    f"### Issue #{i['num']} — {i['title']} (state: closed)\n\n"
    f"**Body (truncated):**\n{i['body']}\n\n"
    f"**Closing comment (truncated):**\n{i['closing']}"
    for i in issues
)

prompt = f'''You are a senior Python / control-systems engineer reviewing a burst of calibration-
reliability changes that landed on `main` between 2026-04-17 and 2026-04-24 for a stage-
lighting product called SlyLED. Fourteen GitHub issues closed during that window; all of
them touch the moving-head calibration pipeline (`desktop/shared/mover_calibrator.py`),
the per-fixture control engine (`desktop/shared/mover_control.py`), or the parametric
kinematic model (`desktop/shared/parametric_mover.py`).

Your job is twofold:

1. **Verify each issue actually shipped.** For each issue below, the closing comment
   names a commit + behaviour. Read the current code (provided below) and confirm the
   described behaviour is present and correct. Flag any issue where the code does not
   match the claim (commit reverted, partial implementation, regression, gap between
   intent and delivery).

2. **Find bugs.** These 14 fixes landed in rapid succession and interact with each
   other (e.g. #651 dark-reference + #655 oversample + #658 blink-confirm + #660 coarse-
   to-fine refine all mutate the discovery phase; #653 phase budgets + #654 held-out
   gate + #627 cal safety all mutate the abort path). Look for:
   - Incorrect control flow between fixes (e.g. #653 timeout firing while #627 blackout
     is pending)
   - Race conditions between the calibration thread and the mover-control claim engine
   - Missed edge cases (e.g. #654 held-out verification on a rig where the held-out
     target lands outside the camera's floor-view polygon after #659 filtering)
   - Numerical issues (e.g. #655 median of a pair vs. median of 5, #660 bracket-floor
     collapse below single DMX step)
   - Any unreachable fallback, TOCTOU, or off-by-one
   - State leaks between calibration runs
   - Anywhere the code promises something the comment doesn't (dead code, debug prints,
     half-applied refactors)

## System context

- Python Flask orchestrator on Windows/Mac; talks UDP to LED performers and Art-Net/sACN
  to DMX fixtures.
- Moving-head calibration pipeline has 8+ phases: claim → warmup → discovery (battleship
  or legacy spiral) → blink-confirm → mapping/convergence → grid → verify sweep → LM fit
  → held-out gate → save. See Appendix B (provided) for the authoritative narrative.
- Calibration runs in a background thread; operator can cancel via
  `POST /api/calibration/mover/<fid>/cancel` which sets `_cancel_event` that inner loops
  check via `_hold_dmx` / `_check_cancel()`.
- `_set_calibrating(fid, True)` blocks the mover-follow engine (`mover_control.py`) from
  writing pan/tilt; lock must always be released via `_set_calibrating(fid, False)` in
  cleanup, regardless of exit path.
- Rotation schema v2 (post-#600): `fixture.rotation = [rx pitch, ry roll, rz yaw/pan]`.
  Always read via `camera_math.rotation_from_layout`. Never index `rotation[1]` or
  `rotation[2]` directly.

## Closed issues — titles, bodies, closing comments

{issues_block}

## Code under review

### desktop/shared/mover_calibrator.py ({len(mover_cal):,} chars)

```python
{mover_cal}
```

### desktop/shared/mover_control.py ({len(mover_ctl):,} chars)

```python
{mover_ctl}
```

### desktop/shared/parametric_mover.py ({len(parametric):,} chars)

```python
{parametric}
```

## Authoritative documentation — Appendix B (DRAFT)

This is what we tell operators happens during calibration. When the code contradicts
this, one of them is wrong and we want to know which.

```markdown
{appendix_b}
```

---

## Output format

Organise your response with one H2 per review task:

### H2 "1. Per-issue verification"

One table row per issue:
`#N | shipped? (yes/no/partial) | file:line evidence | gap or bug found (if any)`

For anything marked `no` or `partial`, expand in a follow-up bullet explaining what's
missing or wrong.

### H2 "2. Cross-issue interactions / bugs"

For each bug or risk you identify:
- **Severity** (P1 / P2 / P3): P1 = active correctness issue, P2 = latent bug triggered
  by plausible input, P3 = cleanup / cosmetic
- **File:line** citation
- **Reproduction:** what input or sequence triggers it
- **Fix suggestion:** concrete code change (1-5 lines if possible)

### H2 "3. Documentation drift"

Line-by-line compare Appendix B claims against current code. List each mismatch as:
`Appendix B says X → code does Y (file:line). Winner: <code | docs>. Fix: <which side>.`

### H2 "4. Prioritised fix list"

Top 5 things you would fix tomorrow, in order. Include the severity, the file, and the
one-sentence fix.

Be specific. Cite file:line for every claim. Do not guess — if a behaviour is ambiguous,
say so and explain what you checked.
'''

print(f"Prompt size: {len(prompt):,} chars", file=sys.stderr)
client = genai.Client(api_key=api_key)
print("Sending to Gemini 2.5 Pro…", file=sys.stderr)
response = client.models.generate_content(
    model='gemini-2.5-pro',
    contents=prompt,
    config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=32768),
)
review = response.text

out_path = ROOT / 'tools' / 'mover_cal_review.md'
header = (
    f"# Gemini Review: mover calibration — 14 closed issues\n\n"
    f"_Generated {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()} via "
    f"`tools/gemini_review_mover_cal.py`._\n\n"
    f"Issues reviewed: {', '.join(f'#{n}' for n in ISSUES)}\n\n"
    f"Source files: `mover_calibrator.py` ({len(mover_cal):,} chars), "
    f"`mover_control.py` ({len(mover_ctl):,} chars), "
    f"`parametric_mover.py` ({len(parametric):,} chars).\n\n---\n\n"
)
out_path.write_text(header + review, encoding='utf-8')
print(f"\nSaved to {out_path.relative_to(ROOT)}")
print("---")
print(review)
