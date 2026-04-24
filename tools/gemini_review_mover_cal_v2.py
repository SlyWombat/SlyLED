#!/usr/bin/env python3
"""Second-pass Gemini review — adds parent_server.py calibration sections.

The first pass (gemini_review_mover_cal.py) couldn't verify #653 (per-phase
time budgets) or #626 (multi-snapshot ArUco + forced blackout) because
their implementations live in parent_server.py. This pass extracts just the
calibration-relevant functions from that file (~1400 lines) and re-runs
the review focused on the two unverified issues plus cross-file bugs.

Output: tools/mover_cal_review_v2.md
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
    print("No API key", file=sys.stderr)
    sys.exit(1)


def _read_lines(path, ranges):
    """Read concatenated line-ranges from a file with headers identifying each."""
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    out = []
    for start, end, label in ranges:
        out.append(f"# ─── {label} — lines {start}-{end} ───\n")
        out.extend(lines[start - 1:end])
        out.append("")  # blank separator
    return "\n".join(out)


# Extract just the calibration-related bits of parent_server.py
parent_ranges = [
    (342, 414, "_derive_stage_bounds + _apply_auto_stage_bounds (#628)"),
    (2770, 3150, "ArUco detect + marker routes (#626 adjacent, marker registry)"),
    (4152, 4424, "_mover_cal_thread_markers + body (#625/#626/#627/#610 markers path)"),
    (4425, 4652, "_mover_cal_thread_v2 + body (#654 held-out gate, legacy v2 path)"),
    (4653, 5346, "_mover_cal_thread + body (#653 phase budgets, legacy BFS path)"),
    (5347, 5900, "/api/calibration/mover/* route handlers"),
    (6150, 6400, "_aruco_snapshot_detect, _aruco_multi_snapshot_detect (#626), _aruco_visibility_report, _aruco_anchor_extrinsics"),
    (9525, 9542, "_set_calibrating lock"),
]
parent_subset = _read_lines(ROOT / 'desktop' / 'shared' / 'parent_server.py', parent_ranges)

mover_cal = (ROOT / 'desktop' / 'shared' / 'mover_calibrator.py').read_text(encoding='utf-8')
parametric = (ROOT / 'desktop' / 'shared' / 'parametric_mover.py').read_text(encoding='utf-8')

prompt = f'''Second-pass review. The first pass found 4 bugs in the mover-calibration library
code but could not verify two closed issues because their call sites live in
`parent_server.py`. Empirical re-read of the code has since CONFIRMED all four first-pass
findings:

**P1**: `mover_calibrator.py:979` — `BRACKET_FLOOR = 1.0 / 255.0`. On 16-bit pan/tilt
fixtures this is ~257 DMX units; bracket-and-retry gives up way too early.

**P2**: `mover_calibrator.py:1386-1389` — `_median` averages for even-length lists
(returns `0.5 * (s[m-1] + s[m])`). Call site at `mover_calibrator.py:700` hardcodes
`n=2`, so "median" is actually "mean of two samples" — zero outlier rejection.

**P2**: `mover_calibrator.py:708` — pixel-centre score formula
`min(bx-40, 600-bx) + min(by-40, 400-by)` hardcodes 640x480 assumption. On 1080p the
real centre (960, 540) scores 0; the formula peaks at (320, 220).

**P3**: `parametric_mover.py:400-414` — mirror ambiguity between top-two sign-combo
candidates only logs via `logging.warning`; not attached to `FitQuality`, not surfaced
to the UI.

Your job in this pass:

1. **Verify #653 (per-phase time budgets + blackout-on-timeout + tier-2 handoff flag)**
   and **#626 (markers-mode pre-check — multi-snapshot ArUco aggregation + forced
   blackout)** against the provided `parent_server.py` calibration subset.

2. **Look for NEW bugs** that only become visible when the `parent_server.py` caller
   code is in view — especially:
   - Abort-path race conditions between `/cancel` (foreground blackout) and the
     calibration thread's `CalibrationAborted` unwind
   - `_set_calibrating(fid, True/False)` leak paths on any exit branch
   - Phase-budget timer vs. `_check_cancel()` priority
   - `_aruco_multi_snapshot_detect` blackout interacts with whatever is currently
     holding DMX channels (fixtures being calibrated are the common case)
   - Any route that writes DMX without checking the calibration lock
   - Interaction between #628 auto stage bounds (triggered on marker POST/DELETE) and
     a running calibration that depends on those bounds

3. **Do not re-describe the 4 confirmed bugs above** — those are already queued for an
   issue. Focus on new findings only.

## System context (unchanged from first pass)

- Python Flask orchestrator, UDP to LED children, Art-Net/sACN to DMX fixtures.
- `_set_calibrating(fid, True)` blocks mover-follow engine writes; must release on every
  exit path. `_cal_blackout()` sends 512 zeros + releases lock.
- Three calibration thread entry points: `_mover_cal_thread` (legacy BFS),
  `_mover_cal_thread_v2` (per-target convergence), `_mover_cal_thread_markers`
  (markers + battleship + blink-confirm).
- `/api/calibration/mover/<fid>/cancel` does a FOREGROUND immediate blackout on the
  engine buffer before the background thread catches `_cancel_event`.

## Code under review

### desktop/shared/mover_calibrator.py ({len(mover_cal):,} chars — unchanged from first pass)

```python
{mover_cal}
```

### desktop/shared/parametric_mover.py ({len(parametric):,} chars — unchanged)

```python
{parametric}
```

### desktop/shared/parent_server.py (calibration subset — {len(parent_subset):,} chars, ~1400 lines)

Included ranges:
- 342-414: `_derive_stage_bounds` + `_apply_auto_stage_bounds` (#628)
- 2770-3150: ArUco detect + marker CRUD routes
- 4152-4424: `_mover_cal_thread_markers` + body (#625/#626/#627/#610)
- 4425-4652: `_mover_cal_thread_v2` + body (#654)
- 4653-5346: `_mover_cal_thread` + body (#653)
- 5347-5900: `/api/calibration/mover/*` route handlers
- 6150-6400: `_aruco_snapshot_detect` + `_aruco_multi_snapshot_detect` (#626) + anchor helpers
- 9525-9542: `_set_calibrating` lock

```python
{parent_subset}
```

---

## Output format

### H2 "1. Verification of #626 and #653"

- `#626` — does `_aruco_multi_snapshot_detect` actually aggregate ≥3 snapshots, use
  best-per-ID selection, and push blackout between frames when `blackout_bridge_ip` is
  set? Is it actually *called* from the markers-mode pre-check path? Cite file:line.

- `#653` — does the per-phase time budget mechanism exist and fire? What are the actual
  budget values? Does timeout trigger `_cal_blackout` and set the tier-2 handoff flag?
  Cite file:line for each.

### H2 "2. New bugs (not in the P1-P3 list above)"

Per bug: **Severity** (P1/P2/P3), **file:line**, **reproduction**, **concrete fix**.
Focus on abort paths, lock leaks, route-vs-thread races, and inter-fix interactions.

### H2 "3. Cross-file correctness"

Any place the mover_calibrator.py library code and the parent_server.py orchestration
code disagree about invariants (e.g. library expects a DMX buffer primed a certain way
and the thread builds it differently, or library expects `profile` to have certain keys
that the thread doesn't guarantee).

### H2 "4. Prioritised new-bug fix list"

Top 3 new bugs to fix first (beyond the four already queued). Specific enough that a
reader can open the file and apply the change.

Cite file:line for every claim. Be terse.
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

out_path = ROOT / 'tools' / 'mover_cal_review_v2.md'
header = (
    f"# Gemini Review v2: mover calibration — with parent_server.py subset\n\n"
    f"_Generated {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()} "
    f"via `tools/gemini_review_mover_cal_v2.py`._\n\n"
    f"Adds calibration-relevant sections of `parent_server.py` to the v1 payload so "
    f"the two previously-unverifiable issues (#653 phase budgets, #626 multi-snapshot "
    f"ArUco) can be confirmed.\n\n---\n\n"
)
out_path.write_text(header + review, encoding='utf-8')
print(f"\nSaved to {out_path.relative_to(ROOT)}")
print("---")
print(review)
