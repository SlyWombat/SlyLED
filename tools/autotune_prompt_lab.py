#!/usr/bin/env python3
"""autotune_prompt_lab.py — A/B (or A/B/C/D) test prompt variants against
moondream, on a fixed image, for a fixed intent. Prints a comparison table
showing what each variant returns: parse-success, score, deltaProposal
keys, notes, and elapsed time.

Usage:
    /usr/bin/python3 tools/autotune_prompt_lab.py \\
        --image docs/live-test-sessions/2026-04-25/autotune-matrix/cam12-tracking-beam-heuristic/before.jpg \\
        --intent beam \\
        --model moondream \\
        --timeout 90

Pick a frozen frame so every variant sees the SAME pixels and only the
prompt is the independent variable.
"""
from __future__ import annotations
import argparse, base64, json, sys, time
import urllib.request
from pathlib import Path

OLLAMA = "http://localhost:11434"

# Camera control state for the per-call prompt suffix (the orchestrator
# always sends this — we keep it stable across variants).
CONTROLS_BRIEF = [
    {"name":"exposure_time_absolute","value":300,"min":1,"max":5000},
    {"name":"gain","value":0,"min":0,"max":100},
    {"name":"white_balance_automatic","value":1,"min":None,"max":None},
    {"name":"white_balance_temperature","value":5000,"min":2300,"max":6500},
    {"name":"auto_exposure","value":1,"min":0,"max":3},
    {"name":"brightness","value":0,"min":-64,"max":64},
    {"name":"contrast","value":57,"min":0,"max":100},
    {"name":"saturation","value":80,"min":0,"max":128},
]

# JSON schema appended to every variant — keeps the comparison fair.
SCHEMA_BLOCK = (
    'Reply with STRICT JSON only — no prose, no markdown fences. Schema:\n'
    '{"score": <0-100 int>, '
    '"notes": [<short human string>, ...], '
    '"deltaProposal": {"exposure_time_absolute": <int|null>, '
    '"gain": <int|null>, '
    '"white_balance_automatic": <0|1|null>, '
    '"white_balance_temperature": <int|null>}}\n'
    'Set a key to null when you do not want to change that control. '
    'Use the min/max ranges from `Current controls` — values outside them are invalid.'
)

# Variants. Persona / framing varies; schema is the same trailer.
VARIANTS = {
    "v1-theatrical": (
        "You are the evaluator for a theatrical-lighting camera tuning loop. "
        "Assess the attached frame for the stated detection intent. Score "
        "0-100 (higher = better) and propose concrete V4L2 control adjustments "
        "that would improve the score.\n\n"
        + SCHEMA_BLOCK
    ),

    "v2-cv-algorithm": (
        "You are an image-processing algorithm. Analyse the attached frame's "
        "histogram, exposure, contrast, and colour cast. Score 0-100 for the "
        "stated detection intent (higher = better). Propose V4L2 control "
        "deltas that would improve the histogram for that detection task.\n\n"
        + SCHEMA_BLOCK
    ),

    "v3-engineer-explicit": (
        "You are a machine-vision engineer tuning a USB webcam for computer-"
        "vision detection. Inspect the attached frame and rate it 0-100 for "
        "the stated detection intent. Higher = better.\n\n"
        "Scoring guidance per intent:\n"
        "- beam: dark backdrop with a bright concentrated spot is good (60-95). "
        "Blown-out highlights drop the score (0-40).\n"
        "- aruco: balanced mid-grey backdrop with crisp black/white markers is "
        "good (70-95). Underexposed (mean<80) or overexposed (mean>180) drops "
        "to 30-50.\n"
        "- yolo: balanced exposure, accurate colour, minimal motion blur is "
        "good (70-95). Hue shift or low saturation drops to 40-60.\n"
        "- general: well-balanced exposure across the histogram, no clipping, "
        "neutral white-balance is good (75-95).\n\n"
        "Propose V4L2 control deltas that move the histogram toward the ideal "
        "for the requested intent. Stay within the min/max of each control.\n\n"
        + SCHEMA_BLOCK
    ),

    "v4-terse-numeric": (
        "Rate the frame 0-100 for the detection intent (higher=better). "
        "Suggest V4L2 control changes within their min/max to improve the "
        "rating. JSON only.\n\n"
        + SCHEMA_BLOCK
    ),

    # Schema rewrite — replace angle-bracket placeholders with concrete
    # example values that moondream can pattern-match against rather than
    # copying literally.
    "v5-concrete-example": (
        "You are an image-processing algorithm. Analyse the attached frame's "
        "histogram, exposure, contrast, and colour cast. Score 0-100 for the "
        "stated detection intent (higher = better). Propose V4L2 control "
        "deltas that would improve the histogram for that detection task.\n\n"
        "Reply with STRICT JSON only — no prose, no markdown fences. "
        "Example of a valid response:\n"
        '{"score": 78, '
        '"notes": ["histogram is balanced", "minor blue cast"], '
        '"deltaProposal": {"exposure_time_absolute": 250, '
        '"gain": 0, '
        '"white_balance_automatic": 0, '
        '"white_balance_temperature": 4800}}\n'
        "Use the SAME shape as that example. The score must be a whole number "
        "between 0 and 100 (NOT a fraction between 0 and 1). Use integer values "
        "for exposure / gain / temperature within the min/max from "
        "`Current controls`. Set a key to null only when you genuinely don't "
        "want to change it. Notes must be 1-3 short observations about what "
        "you actually see in this image — not placeholder text."
    ),

    # Explicit "multiply by 100" anti-fraction phrasing — moondream's training
    # likely biases toward 0-1 confidence floats; tell it directly.
    "v6-multiply-by-100": (
        "You are a machine-vision engineer tuning a USB webcam for computer-"
        "vision detection. Inspect the attached frame and rate it for the "
        "stated detection intent.\n\n"
        "**The score is a whole number from 0 to 100.** If you naturally "
        "think in 0-to-1 confidence, multiply by 100 first: 0.75 confidence "
        "becomes score 75. Acceptable scores: 0, 12, 50, 78, 91, 100. "
        "UNACCEPTABLE: 0.12, 0.78, 0.91, 1.0.\n\n"
        "Propose V4L2 control deltas that would improve the score. Stay "
        "within the min/max of each control.\n\n"
        + SCHEMA_BLOCK
    ),

    # NO SCORE asked. Only deltaProposal + notes. The orchestrator's
    # heuristic evaluator (post-apply) is the actual judge, so moondream's
    # opinion of 0-100 quality is unused and cannot bias output.
    "v7-no-score": (
        "You are a machine-vision engineer tuning a USB webcam for computer-"
        "vision detection. Inspect the attached frame and propose V4L2 "
        "control deltas that would improve it for the stated detection "
        "intent. Stay within each control's min/max.\n\n"
        "Scoring guidance (use only to decide direction):\n"
        "- beam: dark backdrop with concentrated bright spot is good. "
        "Prefer low exposure + low gain.\n"
        "- aruco: balanced mid-grey backdrop with crisp black/white markers "
        "is good. Prefer mean luminance ~ 120, no clipping.\n"
        "- yolo: balanced exposure, accurate colour, minimal blur. Prefer "
        "mean ~ 120, manual WB at 5000K.\n"
        "- general: balanced histogram, no clipping, neutral WB.\n\n"
        "Reply with STRICT JSON only — no prose, no markdown fences. "
        "Example response:\n"
        '{"notes": ["scene is underexposed", "wall too dark to see markers"], '
        '"deltaProposal": {"exposure_time_absolute": 800, '
        '"gain": 15, '
        '"white_balance_automatic": 0, '
        '"white_balance_temperature": 5000}}\n'
        "Use the SAME shape. Notes must be 1-3 short observations about THIS "
        "image — not placeholder text. Use integer values within min/max. "
        "Set a key to null only when you don't want to change it."
    ),
}


def ask(model, prompt_text, intent, image_b64, timeout):
    full_prompt = (
        prompt_text
        + f"\n\nIntent: {intent}\nCurrent controls:\n"
        + json.dumps(CONTROLS_BRIEF, indent=2)
    )
    body = {
        "model": model,
        "prompt": full_prompt,
        "images": [image_b64],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type":"application/json"},
    )
    t0 = time.monotonic()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        elapsed = time.monotonic() - t0
        return elapsed, resp.get("response","").strip(), None
    except Exception as e:
        return time.monotonic() - t0, None, str(e)


def validate_delta(delta):
    """Check each key against its known control range; return list of issues."""
    if not delta: return []
    valid_ranges = {c["name"]: (c["min"], c["max"]) for c in CONTROLS_BRIEF
                    if c.get("min") is not None}
    issues = []
    for k, v in delta.items():
        if v is None: continue
        if k in valid_ranges:
            lo, hi = valid_ranges[k]
            if not (lo <= v <= hi):
                issues.append(f"{k}={v} ∉ [{lo},{hi}]")
        elif k == "white_balance_automatic":
            if v not in (0, 1):
                issues.append(f"white_balance_automatic={v} ∉ {{0,1}}")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="path to JPEG")
    ap.add_argument("--intent", default="beam",
                    choices=["general","beam","aruco","yolo"])
    ap.add_argument("--model", default="moondream")
    ap.add_argument("--timeout", type=int, default=90,
                    help="per-call timeout seconds")
    ap.add_argument("--variants", default="all",
                    help="comma-separated subset of variant keys, or 'all'")
    args = ap.parse_args()

    img_bytes = Path(args.image).read_bytes()
    image_b64 = base64.b64encode(img_bytes).decode()
    print(f"image: {args.image}  size={len(img_bytes)} bytes  intent={args.intent}  model={args.model}")
    print()

    keys = list(VARIANTS) if args.variants == "all" else args.variants.split(",")
    print(f"{'variant':22s} {'time':>7s}  {'parsed':>6s}  {'score':>5s}  {'delta?':>6s}  {'issues':30s}  notes")
    print("-" * 130)

    results = {}
    for k in keys:
        if k not in VARIANTS:
            print(f"{k:22s} UNKNOWN VARIANT")
            continue
        elapsed, raw, err = ask(args.model, VARIANTS[k], args.intent, image_b64,
                                 timeout=args.timeout)
        if err:
            print(f"{k:22s} {elapsed:>5.1f}s  ERR: {err[:70]}")
            results[k] = {"err": err, "elapsed": elapsed}
            continue
        parsed_ok = True
        try:
            parsed = json.loads(raw)
        except Exception as pe:
            parsed_ok = False
            parsed = {}
            print(f"{k:22s} {elapsed:>5.1f}s  PARSE_FAIL  raw={raw[:80]!r}")
            results[k] = {"raw": raw, "elapsed": elapsed,
                          "parse_error": str(pe)}
            continue
        score = parsed.get("score")
        notes = parsed.get("notes") or []
        delta_raw = parsed.get("deltaProposal")
        # Defensive: moondream sometimes returns delta as list-of-pairs or
        # null. Normalise to dict before continuing.
        if isinstance(delta_raw, dict):
            delta = delta_raw
        elif isinstance(delta_raw, list):
            try:
                delta = {item.get("name") or item.get("key"): item.get("value")
                          for item in delta_raw if isinstance(item, dict)}
            except Exception:
                delta = {}
        else:
            delta = {}
        non_null = {k: v for k, v in delta.items() if v is not None}
        issues = validate_delta(delta)
        delta_marker = ("✓" if non_null else "—") + (f" ({len(non_null)})" if non_null else "")
        issue_str = " | ".join(issues)[:30] if issues else ""
        notes_str = (" | ".join(str(n) for n in notes))[:60] if notes else ""
        print(f"{k:22s} {elapsed:>5.1f}s  {'YES' if parsed_ok else 'NO':>6s}  {score!s:>5s}  {delta_marker:>6s}  {issue_str:30s}  {notes_str}")
        results[k] = {
            "elapsed": elapsed, "score": score, "deltaProposal": delta,
            "issues": issues, "notes": notes,
        }

    return 0


if __name__ == "__main__":
    sys.exit(main())
