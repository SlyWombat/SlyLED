#!/usr/bin/env python3
"""Verify each Gemini-review finding against mover_calibrator.py + parametric_mover.py.

Reports PASS / FAIL per finding with concrete evidence.
Run: /usr/bin/python3 tests/verify_mover_cal_findings.py
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOVER_CAL = ROOT / 'desktop' / 'shared' / 'mover_calibrator.py'
PARAMETRIC = ROOT / 'desktop' / 'shared' / 'parametric_mover.py'

results = []

def check(name, failed, detail):
    status = "FAIL" if failed else "PASS"
    results.append((name, status, detail))
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")

# Import mover_calibrator's helpers directly — dependencies make a full import risky,
# so we load just the helper functions via exec on relevant snippets.
cal_src = MOVER_CAL.read_text(encoding='utf-8')
param_src = PARAMETRIC.read_text(encoding='utf-8')

# ── Bug 1 (P1): BRACKET_FLOOR = 1/255 — only one 8-bit DMX step, wrong for 16-bit
m = re.search(r'BRACKET_FLOOR\s*=\s*([^\s#]+)\s*(#.*)?', cal_src)
bracket_floor_expr = m.group(1) if m else None
check(
    "1. BRACKET_FLOOR hardcodes 1.0/255.0 (wrong for 16-bit fixtures)",
    bracket_floor_expr != '1.0' and '1.0 / 255.0' not in (m.group(0) if m else ''),
    f"literal: {m.group(0).strip() if m else '(not found)'}"
)
# Show the effective value
floor_val = eval(bracket_floor_expr) if bracket_floor_expr else None
check(
    "1b. BRACKET_FLOOR numeric value matches Appendix B claim of 0.002",
    floor_val is None or abs(floor_val - 0.002) > 1e-6,
    f"actual: {floor_val!r}; Appendix B says 0.002; delta = {abs(floor_val - 0.002) if floor_val else 'n/a'}"
)

# ── Bug 2 (P2): _median averages for even-length lists → n=2 is mean, not median
# Extract the _median function and exercise it
exec_ns = {}
# Pull just the _median definition
m_med = re.search(r'def _median\(values\):.*?(?=\n\S|\n    \w+ =|\n    return \()', cal_src, re.DOTALL)
if m_med:
    # Trim to a minimal function block
    med_code = re.search(r'def _median\(values\):[\s\S]*?\n        return [^\n]+', cal_src).group(0)
    # Re-indent to top-level
    lines = med_code.splitlines()
    dedent = "\n".join(ln[4:] if ln.startswith('    ') else ln for ln in lines)
    try:
        exec(dedent, exec_ns)
        med = exec_ns['_median']
        # n=2 behaviour
        result_n2 = med([10.0, 100.0])
        result_n3 = med([10.0, 50.0, 100.0])
        check(
            "2. _median([10, 100]) averages (n=2 acts as mean)",
            result_n2 != 55.0,
            f"_median([10, 100]) = {result_n2}; expected mean 55.0; true median is undefined for n=2"
        )
        check(
            "2b. _median([10, 50, 100]) gives middle value (n=3 works)",
            result_n3 != 50.0,
            f"_median([10, 50, 100]) = {result_n3}"
        )
    except Exception as e:
        check("2. _median behaviour", True, f"exec failed: {e}")
else:
    check("2. _median behaviour", True, "_median function not found in source")

# ── Bug 3 (P2): _refine_battleship_hit hardcodes n=2, ignoring OVERSAMPLE_N = 3
# Search for the call pattern
m_call = re.search(r'_beam_detect_oversampled\([^)]*n=(\d+)[^)]*\)', cal_src)
check(
    "3. _refine_battleship_hit passes n=2 to _beam_detect_oversampled",
    not (m_call and m_call.group(1) == '2'),
    f"matched: {m_call.group(0) if m_call else '(no match)'}; module default OVERSAMPLE_N = 3"
)

# ── Bug 4 (P2): hardcoded 600/400 for pixel scoring assumes 640×480
m_score = re.search(r'min\(\w+ - 40, 600 - \w+\).*min\(\w+ - 40, 400 - \w+\)', cal_src)
check(
    "4. Pixel-centre scoring hardcodes (600, 400) — assumes 640x480 camera",
    m_score is None,
    f"matched: {m_score.group(0) if m_score else '(not found)'}"
)

# Simulate score at 1080p centre vs 640x480 centre
def score(bx, by):
    return max(0.0, min(bx - 40, 600 - bx)) + max(0.0, min(by - 40, 400 - by))
score_1080p_centre = score(960, 540)  # 1920x1080 centre
score_640_centre = score(320, 240)     # 640x480 centre
score_assumed_centre = score(320, 220) # where the formula actually peaks
check(
    "4b. Scoring peaks outside the real image centre on 1080p",
    score_1080p_centre >= score_assumed_centre,
    f"score(1920x1080 centre=960,540) = {score_1080p_centre}; "
    f"score(640x480 centre=320,240) = {score_640_centre}; "
    f"score(formula peak ~320,220) = {score_assumed_centre}"
)

# ── Bug 5 (P3): Mirror ambiguity only logs — not surfaced via FitQuality
has_field = re.search(r'mirror_ambiguity|mirrorAmbiguity|ambiguous', param_src)
has_log = re.search(r'fit_model.*mirror ambiguity', param_src, re.IGNORECASE | re.DOTALL)
check(
    "5. FitQuality has a mirror_ambiguity flag",
    has_field is None,
    f"field in parametric_mover.py: {has_field.group(0) if has_field else '(none)'}; "
    f"logger warning present: {bool(has_log)}"
)

# ── Doc drift: Appendix B claims vs code
manual = (ROOT / 'docs' / 'USER_MANUAL.md').read_text(encoding='utf-8')
b_section = manual[manual.find('## Appendix B'):manual.find('## Appendix C')]

claims_vs_code = [
    ('BRACKET_FLOOR = 0.002', 'BRACKET_FLOOR = 1.0 / 255.0', '0.002' in b_section),
    ('After fit, nudge pan', 'verify_signs landed pre-fit', 'After fit' in b_section and 'nudge pan by +0.02' in b_section),
    ('legacy discovery doesn\'t use adaptive settle',
     'code calls _wait_settled in legacy path',
     'adaptive-settle machinery documented in the mapping phase does not apply here' in b_section),
]
for claim, reality, found in claims_vs_code:
    check(
        f"DOC. Appendix B claim '{claim[:40]}...' present",
        not found,  # FAIL if claim is still in the doc (indicating drift)
        f"reality: {reality}"
    )

# ── Summary
print()
fails = [r for r in results if r[1] == "FAIL"]
print(f"=== {len(fails)} FAIL / {len(results) - len(fails)} PASS ===")
for name, status, _ in results:
    if status == "FAIL":
        print(f"  FAIL  {name}")

sys.exit(len(fails))
